"""5 资产层前的网络校验：把加工层产物建成 Esri 雨水 Utility Network，用 Esri 官方规则校验、做子网追踪（标准 v0.2 第 11 章、关口 G-08）。

  python -m tools.dgov.un --extent rda

在 ArcGIS Pro 的 Python 里调 tools/dgov/un_build.py（要求 ArcGIS Pro 已登录、Advanced 许可）。
产物：<work_root>/<extent>/<extent>_un/<extent>_un.geodatabase（UN 移动地理数据库）；out/dgov/un/<extent>_un_report.json。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .common import OUT, ROOT, lineage, load, source


def build(ext, log=print, src=None, tag=None):
    """src：要校验的数据包（缺省是加工层产物）；tag：报告和工作目录的后缀（方案包用，如 L1+TIP）。"""
    import geopandas as gpd
    cfg = load("un.yaml")
    name = ext if not tag else f"{ext}_{tag.replace('+', '_')}"
    work = Path(cfg["work_root"]) / name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    src = Path(src) if src else OUT / "proc" / f"{ext}.gpkg"
    gp = work / f"{ext}_proc.gpkg"
    shutil.copy2(src, gp)
    e = load("extents.yaml")["extents"][ext]
    s = source(e["from_source"])
    g = gpd.read_file(Path(s["abspath"]) / e["layer"]).set_crs(e["crs"], allow_override=True)
    terr = work / "territory.shp"
    gpd.GeoDataFrame({"name": [ext]}, geometry=[g.union_all().buffer(e["buffer_m"])], crs=e["crs"]).to_file(terr)
    if cfg.get("method") == "essentials_full":
        cmd = [cfg["arcgis_python"], "-I", str(ROOT / "tools" / "dgov" / "un_build_ess.py"), str(gp), str(work), str(terr),
               cfg["esri_asset_package"], cfg["udms_atbx"], f"{ext}_ess"]
    else:
        cmd = [cfg["arcgis_python"], "-I", str(ROOT / "tools" / "dgov" / "un_build.py"), str(gp), str(work), str(terr), cfg["esri_asset_package"], f"{ext}_un"]
    log("  " + " ".join(cmd[2:]))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    (work / "un_build.log").write_text(r.stdout + "\n" + r.stderr, encoding="utf-8")
    if r.returncode:
        raise SystemExit(f"建 UN 失败，见 {work / 'un_build.log'}：\n{r.stderr[-1500:]}")
    rep = json.loads((work / "un_report.json").read_text(encoding="utf-8"))
    dst = OUT / "un" / (f"{ext}_un_report.json" if not tag else f"{ext}_{tag}_un_report.json")
    dst.parent.mkdir(parents=True, exist_ok=True)
    rep["un_gdb"] = rep.get("un") or str(work / f"{ext}_un" / f"{ext}_un.geodatabase")
    dst.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    lineage("un", [src], [dst], {"extent": ext, "method": cfg.get("method"), "asset_package": cfg["esri_asset_package"], "udms": cfg.get("udms_atbx")},
            notes=f"规则违规 {rep['rule_errors']}；属性规则错误 {sum(rep.get('attribute_rule_errors', {}).values())}；追不到出水口的管 {len(rep['unreached_lines'])}；子网 {rep['subnetworks']}")
    log(f"  UN（{cfg.get('method', 'migrate')}）：规则违规 {rep['rule_errors']}，属性规则错误 {sum(rep.get('attribute_rule_errors', {}).values())}，追不到出水口的管 {len(rep['unreached_lines'])}，用时 {rep['seconds']} s")
    log(f"→ {dst}")
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.un")
    ap.add_argument("--extent", required=True)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    build(a.extent)


if __name__ == "__main__":
    main()
