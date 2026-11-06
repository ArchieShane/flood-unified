"""0 原始层：登记一份原始数据（只读）。

  python -m tools.dgov.raw register --source dmt_stormwater
  python -m tools.dgov.raw register --source aecom_rda [--no-hash]

产物 out/dgov/raw/<source>/manifest.json：
  - 每个文件：相对路径、字节数、修改时间、sha256；
  - 每个 shapefile 图层：要素数、几何类型、坐标系、字段，以及每个字段的非空率（空值、0、占位文字都算空）；
  - 每个 .shp.xml：Esri 加工历史（lineage 里的 Process：工具、日期、命令），按时间排序；
  - SewerGEMS 工程：情景树（.stsw.sqlite 的 HMIScenario + 标签），每个 SWMM 导出对应哪个情景、哪些已被删掉。
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime

from .common import OUT, lineage, source, sha256, write_json

PLACEHOLDER = {"", "n/a", "na", "undefined", "none", "nan", "nc", "nat"}


def fill_rate(df):
    out = {}
    n = len(df)
    for c in df.columns:
        if c == "geometry":
            continue
        s = df[c]
        txt = s.astype(str).str.strip().str.lower()
        empty = s.isna() | txt.isin(PLACEHOLDER) | txt.isin({"0", "0.0", "1899-12-30 00:00:00", "1600-11-01 00:00:00"})
        out[c] = round(1 - float(empty.sum()) / n, 4) if n else None
    return out


def esri_lineage(xml_path):
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError:
        return []
    steps = []
    for p in root.iter("Process"):
        d, t = p.get("Date", ""), p.get("Time", "")
        tool = os.path.basename((p.get("ToolSource") or "").replace("\\", "/")).split(".tbx")[-1].lstrip("/\\")
        steps.append({"date": d, "time": t, "tool": tool, "cmd": (p.text or "")[:300]})
    return sorted(steps, key=lambda s: (s["date"], s["time"]))


def sewergems_tree(root):
    import glob
    sq = glob.glob(str(root / "**" / "*.stsw.sqlite"), recursive=True)
    if not sq:
        return None
    c = sqlite3.connect(f"file:{sq[0]}?mode=ro", uri=True)
    cols = [r[1] for r in c.execute("pragma table_info(HMIModelingElement)")]
    lab = {a: (b or "").strip() for a, b in c.execute(f'select "{cols[0]}", Label from HMIModelingElement')}
    tree = {str(s): {"name": lab.get(s, ""), "parent": lab.get(p, "") if p else ""}
            for s, p in c.execute("select ScenarioID, ParentID from HMIScenario where IsDeleted = 0")}
    runs = sorted({re.sub(r".*_", "", os.path.splitext(os.path.basename(p))[0]) for p in glob.glob(str(root / "**" / "*.inp"), recursive=True)})
    return {"sqlite": os.path.relpath(sq[0], root), "scenarios": tree,
            "runs": {r: (tree[r] if r in tree else {"name": "（不在情景树里：情景已删）", "parent": ""}) for r in runs}}


def register(sid, do_hash=True, log=print):
    import pyogrio
    s = source(sid)
    root = s["abspath"]
    files, layers, lineage_esri, rasters = [], {}, {}, {}
    t0 = time.time()
    allf = sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith("._"))   # ._* 是 macOS 拷贝留下的元数据文件
    log(f"{sid}：{root}，{len(allf)} 个文件，{sum(p.stat().st_size for p in allf) / 1e9:.2f} GB")
    for i, p in enumerate(allf, 1):
        st = p.stat()
        rec = {"path": p.relative_to(root).as_posix(), "bytes": st.st_size,
               "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}
        if do_hash:
            rec["sha256"] = sha256(p)
        files.append(rec)
        if p.suffix.lower() == ".shp":
            info = pyogrio.read_info(p)
            df = pyogrio.read_dataframe(p, read_geometry=False)
            layers[rec["path"]] = {"features": int(info["features"]), "geometry": info["geometry_type"], "crs": info["crs"],
                                   "fields": list(info["fields"]), "fill_rate": fill_rate(df)}
        if p.name.lower().endswith(".shp.xml"):
            lineage_esri[rec["path"]] = esri_lineage(p)
        if p.suffix.lower() in (".tif", ".tiff") and not p.name.startswith("._"):
            import rasterio
            with rasterio.open(p) as ds:
                rasters[rec["path"]] = {"crs": str(ds.crs), "epsg": ds.crs.to_epsg() if ds.crs else None, "res": list(ds.res), "width": ds.width,
                                        "height": ds.height, "dtype": ds.dtypes[0], "nodata": ds.nodata, "bounds": list(ds.bounds),
                                        "overviews": ds.overviews(1)}
        if i % 50 == 0:
            log(f"  {i}/{len(allf)}（{time.time() - t0:.0f} s）")
    man = {"source": {k: v for k, v in s.items() if k != "abspath"}, "root": str(root), "registered": datetime.now().isoformat(timespec="seconds"),
           "files": files, "layers": layers, "esri_lineage": lineage_esri, "rasters": rasters}
    if s["kind"] == "sewergems_project":
        man["sewergems"] = sewergems_tree(root)
    write_json(OUT / "raw" / sid / "manifest.json", man)
    lineage("raw.register", [root], [OUT / "raw" / sid / "manifest.json"], {"source": sid, "hash": do_hash},
            notes=f"{len(files)} 个文件，{sum(f['bytes'] for f in files) / 1e9:.2f} GB")
    log(f"→ {OUT / 'raw' / sid / 'manifest.json'}（{time.time() - t0:.0f} s）")
    return man


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.raw")
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("register")
    r.add_argument("--source", required=True)
    r.add_argument("--no-hash", action="store_true", help="不算哈希（只看结构时用；正式登记要算）")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    register(a.source, do_hash=not a.no_hash)


if __name__ == "__main__":
    main()
