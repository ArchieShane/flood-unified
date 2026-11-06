"""数据现状分析（数据探查）：在定标准、做治理之前，先弄清数据实际是什么样。不改任何原始文件。

  python -m tools.dgov.explore inventory        资源盘点：编目里每份数据在不在、多大、里面有什么；另扫出没编目的数据文件
  python -m tools.dgov.explore models           顾问模型盘点：每个模型的范围、规模、设施；不同批次的同一模型认出来（范围重叠）
  python -m tools.dgov.explore versions         同一份数据的不同版本比对（DMT GDB 对 shapefile 导出）
  python -m tools.dgov.explore gaps             建模需求与供给的缺口矩阵（需求清单在 config/requirements.yaml）
  python -m tools.dgov.explore all

产物在 out/dgov/explore/：inventory.json/.md、unlisted.csv、models.gpkg/.md、versions.md、gaps.md；每步记血缘。
口径见 docs/一二维建模数据标准（v0.2 起第 0 章「数据现状分析」）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from .common import CONF, OUT, ROOT, lineage, load, write_json

EXP = OUT / "explore"


def _size(p):
    p = Path(p)
    if p.is_file():
        return p.stat().st_size, 1
    n = s = 0
    for q in p.rglob("*"):
        if q.is_file() and not q.name.startswith("._"):
            n += 1
            s += q.stat().st_size
    return s, n


def _vec_layers(path, gdb=False):
    import pyogrio
    out = []
    if gdb:
        for name, gt in pyogrio.list_layers(path):
            try:
                i = pyogrio.read_info(path, layer=name)
                out.append({"layer": name, "geom": gt, "features": int(i["features"]), "fields": len(i["fields"]), "crs": i["crs"]})
            except Exception as e:
                out.append({"layer": name, "error": str(e)[:100]})
        return out
    for shp in sorted(Path(path).glob("*.shp")):
        try:
            i = pyogrio.read_info(shp)
            out.append({"layer": shp.stem, "geom": i["geometry_type"], "features": int(i["features"]), "fields": len(i["fields"]), "crs": i["crs"],
                        "has_prj": shp.with_suffix(".prj").exists()})
        except Exception as e:
            out.append({"layer": shp.stem, "error": str(e)[:100]})
    return out


def _model_dir(d):
    """一个模型目录：shapefile 图层、SWMM inp、SewerGEMS 工程、表格、PDF。"""
    d = Path(d)
    rec = {"dir": d.name, "shp": _vec_layers(d)}
    for sub in sorted(x for x in d.iterdir() if x.is_dir()):
        sl = _vec_layers(sub)
        if sl:
            rec.setdefault("sub_shp", {})[sub.name] = sl
    rec["inp"] = len(list(d.rglob("*.inp")))
    rec["stsw"] = len([p for p in d.rglob("*.stsw")])
    rec["xlsx"] = [p.name for p in d.rglob("*.xlsx")][:5]
    rec["pdf"] = [p.name for p in d.rglob("*.pdf")][:5]
    rec["dwg"] = len(list(d.rglob("*.dwg")))
    return rec


def inventory(log=print):
    C = load("catalog.yaml")
    out = []
    for ds in C["datasets"]:
        p = Path(ds["path"])
        rec = dict(ds)
        if ds.get("paths"):                                   # 只算列出来的文件（目录里还有别的东西）
            rec["exists"] = all(Path(x).exists() for x in ds["paths"])
            rec["bytes"], rec["files"] = sum(Path(x).stat().st_size for x in ds["paths"] if Path(x).exists()), len(ds["paths"])
            import pypdf
            rec["pdfs"] = [{"file": Path(x).name, "pages": len(pypdf.PdfReader(x).pages), "mb": round(Path(x).stat().st_size / 1e6, 1)} for x in ds["paths"] if x.lower().endswith(".pdf") and Path(x).exists()]
            out.append(rec)
            log(f"  {ds['id']:4} {ds['title'][:40]:42} {'在' if rec['exists'] else '缺'}  {rec['bytes'] / 1e9:6.2f} GB  {rec['files']}")
            continue
        if ds["kind"] == "db":
            rec.update(exists=None, note="数据库：连接见 config.toml；表清单另行导出")
            try:
                sys.path.insert(0, str(ROOT))
                import tomllib
                from swbrain import db
                cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
                with db.connect_source(cfg, "makani", timeout_ms=20000) as cn:          # 只读查询
                    cur = cn.cursor()
                    cur.execute("select count(*) from information_schema.tables where table_schema = 'public'")
                    rec["tables"] = cur.fetchone()[0]
                    rec["exists"] = True
                    rec["counts"] = {}
                    for t in ds.get("tables_of_interest", []):                     # 库里的雨水资产等关键表，逐表计数
                        cur.execute(f'select count(*) from public."{t}"')
                        rec["counts"][t] = cur.fetchone()[0]
            except Exception as e:
                rec["note"] += f"；本次没连上（{str(e)[:80]}）"
            out.append(rec)
            continue
        rec["exists"] = p.exists()
        if not p.exists():
            out.append(rec)
            continue
        rec["bytes"], rec["files"] = _size(p)
        try:
            if ds["kind"] == "gdb":
                rec["layers"] = _vec_layers(str(p), gdb=True)
            elif ds["kind"] == "shp_dir":
                rec["layers"] = _vec_layers(p)
            elif ds["kind"] == "shp_dirs":
                rec["models"] = [_model_dir(x) for x in sorted(p.iterdir()) if x.is_dir()]
                rec["loose"] = [x.name for x in p.iterdir() if x.is_file()][:20]
            elif ds["kind"] == "raster":
                import rasterio
                with rasterio.open(p) as r:
                    rec["raster"] = {"crs": str(r.crs)[:60], "epsg": r.crs.to_epsg() if r.crs else None, "res": list(r.res), "shape": [r.height, r.width],
                                     "dtype": r.dtypes[0], "nodata": r.nodata, "bounds": [round(v) for v in r.bounds], "overviews": r.overviews(1)}
            elif ds["kind"] == "archive":
                if p.suffix.lower() == ".zip":
                    with zipfile.ZipFile(p) as z:
                        names = z.namelist()
                    rec["entries"] = len(names)
                    rec["top"] = sorted({n.split("/")[0] for n in names})[:20]
            elif ds["kind"] == "xlsx":
                import openpyxl
                xs = [p] if p.is_file() else sorted(p.rglob("*.xlsx"))
                rec["workbooks"] = []
                for x in xs:
                    wb = openpyxl.load_workbook(x, read_only=True, data_only=True)
                    rec["workbooks"].append({"file": x.name, "sheets": [(ws.title, ws.max_row, ws.max_column) for ws in wb.worksheets]})
            elif ds["kind"] == "kmz":
                with zipfile.ZipFile(p) as z:
                    kml = z.read(next(n for n in z.namelist() if n.endswith(".kml"))).decode("utf-8", "ignore")
                rec["placemarks"] = kml.count("<Placemark")
            elif ds["kind"] == "pdf":
                import pypdf
                ps = [p] if p.is_file() else sorted(x for x in p.rglob("*.pdf") if re.search(r"WA-726|Catchment Assessment|RD|Parsons|汇水", x.name, re.I))
                rec["pdfs"] = []
                for x in ps[:40]:
                    try:
                        rec["pdfs"].append({"file": x.name, "pages": len(pypdf.PdfReader(str(x)).pages), "mb": round(x.stat().st_size / 1e6, 1)})
                    except Exception as e:
                        rec["pdfs"].append({"file": x.name, "error": str(e)[:60]})
        except Exception as e:
            rec["error"] = str(e)[:200]
        out.append(rec)
        log(f"  {ds['id']:4} {ds['title'][:40]:42} {'在' if rec['exists'] else '缺'}  {rec.get('bytes', 0) / 1e9:6.2f} GB  {rec.get('files', '')}")
    # 没编目的数据文件
    norm = lambda x: os.path.normcase(os.path.normpath(str(x)))
    listed = [norm(d["path"]) for d in C["datasets"] if d["kind"] != "db" and not d.get("paths")]
    listed += [norm(x) for d in C["datasets"] for x in (d.get("paths") or []) + (d.get("originals") or [])]
    un = []
    for root in C["roots"]:
        for dp, dns, fns in os.walk(root):
            rel = os.path.relpath(dp, root).replace("\\", "/")
            dns[:] = [x for x in dns if not any((rel + "/" + x).strip("./").startswith(s) or x == s for s in C["skip_dirs"]) and not x.endswith(".gdb")]
            for fn in fns:
                fp = norm(os.path.join(dp, fn))
                if fn.startswith("._") or Path(fn).suffix.lower() not in C["data_ext"]:
                    continue
                if any(fp == l or fp.startswith(l + os.sep) for l in listed):
                    continue
                un.append({"path": fp, "mb": round(os.path.getsize(fp) / 1e6, 2), "ext": Path(fn).suffix.lower()})
    EXP.mkdir(parents=True, exist_ok=True)
    write_json(EXP / "inventory.json", out)
    U = pd.DataFrame(un)
    U.to_csv(EXP / "unlisted.csv", index=False, encoding="utf-8-sig")
    md = ["# 数据现状分析 · 资源盘点", "", f"编目 {len(out)} 份（`tools/dgov/config/catalog.yaml`）。", "",
          "| 编号 | 资料 | 角色 | 来源方 | 收到 | 在不在 | 大小 | 内容摘要 |", "|---|---|---|---|---|---|---|---|"]
    for r in out:
        if r.get("layers"):
            s = "；".join(f"{x['layer']} {x.get('features', '?'):,}" for x in r["layers"] if "features" in x)[:220]
        elif r.get("models"):
            s = f"{len(r['models'])} 个模型目录：" + "、".join(m["dir"] for m in r["models"])[:200]
        elif r.get("raster"):
            s = f"{r['raster']['res'][0]} m，{r['raster']['shape'][1]}×{r['raster']['shape'][0]}，EPSG {r['raster']['epsg']}，NoData {r['raster']['nodata']}"
        elif r.get("workbooks"):
            s = "；".join(f"{w['file']}（{len(w['sheets'])} 表）" for w in r["workbooks"])[:200]
        elif r.get("pdfs"):
            s = "；".join(f"{x['file']}（{x.get('pages', '?')} 页）" for x in r["pdfs"])[:220]
        elif "placemarks" in r:
            s = f"{r['placemarks']} 个点"
        elif "entries" in r:
            s = f"{r['entries']} 个文件：" + "、".join(r["top"])[:150]
        elif r.get("counts"):
            s = f"{r['tables']} 张表；雨水相关：" + "；".join(f"{k} {v:,}" for k, v in r["counts"].items() if v)[:400]
        else:
            s = r.get("note", r.get("error", ""))
        size = f"{r['bytes'] / 1e9:.2f} GB" if r.get("bytes") else "—"
        md.append(f"| {r['id']} | {r['title']} | {r['role']} | {r['owner']} | {r['received']} | {'在' if r.get('exists') else ('—' if r.get('exists') is None else '**缺**')} | {size} | {s} |")
    if len(U):
        g = U.assign(top=U.path.str.replace("\\", "/").str.extract(r"^(d:/[^/]+/[^/]+(?:/[^/]+)?)", flags=re.I)[0]).groupby("top").agg(n=("path", "size"), mb=("mb", "sum")).sort_values("mb", ascending=False)
        md += ["", f"## 没编目的数据文件：{len(U)} 个（`unlisted.csv`）", "", "| 目录 | 文件数 | MB |", "|---|---|---|"] + [f"| {k} | {v.n} | {v.mb:,.0f} |" for k, v in g.head(30).iterrows()]
    (EXP / "inventory.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    lineage("explore.inventory", [CONF / "catalog.yaml"], [EXP / "inventory.json", EXP / "inventory.md", EXP / "unlisted.csv"], {}, notes=f"{len(out)} 份编目，{len(U)} 个未编目文件")
    log(f"→ {EXP / 'inventory.md'}（未编目 {len(U)} 个）")
    return out


LINK_PAT = re.compile(r"^(conduit|conduits|co|pipe|pipes|gravity.*|.*conduit.*)$", re.I)
NODE_PAT = re.compile(r"(manhole|^mh$|catch.?basin|^cb$|junction|inlet)", re.I)
POND_PAT = re.compile(r"pond", re.I)
PUMP_PAT = re.compile(r"^pump", re.I)
CATCH_PAT = re.compile(r"^(catchment|catchments|ca)$", re.I)


def _model_units(dirpath):
    """一个模型目录（含一层子目录）里各类图层的路径。"""
    found = defaultdict(list)
    for shp in list(Path(dirpath).glob("*.shp")) + list(Path(dirpath).glob("*/*.shp")):
        s = shp.stem.strip()
        for k, pat in (("link", LINK_PAT), ("node", NODE_PAT), ("pond", POND_PAT), ("pump", PUMP_PAT), ("catchment", CATCH_PAT)):
            if pat.search(s) and "pressure" not in s.lower():
                found[k].append(shp)
    return found


def models(log=print):
    """顾问模型盘点：每个模型的范围（管线外扩 30 m 的并集）、规模、设施；跨批次按范围重叠认同一个模型。"""
    import geopandas as gpd
    import pyogrio
    from shapely.ops import unary_union
    C = {d["id"]: d for d in load("catalog.yaml")["datasets"]}
    rows = []
    for sid in ("S10", "S11", "S12", "S13", "S14"):
        root = Path(C[sid]["path"])
        dirs = [x for x in sorted(root.iterdir()) if x.is_dir()]
        if sid == "S13":                                          # test/<汇水区>/<汇水区>/... 或 test/<汇水区>/*.shp
            dirs = [x / x.name if (x / x.name).is_dir() else x for x in dirs]
        if sid == "S14":
            dirs = [root]
        for d in dirs:
            u = _model_units(d)
            if not u.get("link"):
                continue
            lk = u["link"][0]
            if "10+20%-1hr" in [p.parent.name for p in u["link"]]:     # AECOM：取 Base 那组情景
                lk = next(p for p in u["link"] if p.parent.name == "10+20%-1hr")
            g = pyogrio.read_dataframe(lk)
            if not len(g):
                continue
            x0 = g.total_bounds[0]
            unit = "ft?" if x0 > 600000 else "m"
            if g.crs is None:
                g = g.set_crs(32640, allow_override=True)
            if unit == "ft?":
                g = g.set_geometry(g.geometry.scale(0.3048, 0.3048, origin=(0, 0)))
            g = g.to_crs(32640)
            fp = unary_union(g.geometry.buffer(30)).buffer(0)
            cnt = lambda k: sum(int(pyogrio.read_info(p)["features"]) for p in u.get(k, [])[:1])
            fields = set(pyogrio.read_info(lk)["fields"])
            res_cols = sorted(fields & {"CAP_FULL", "V_(MAX)", "MAX_HGL", "D/D", "D_MAX_R", "Q_(MAX)", "FLOW_MAX"})
            rows.append({"batch": sid, "model": d.parent.name if (sid == "S13" and d.parent != root) else d.name, "path": str(d), "links": len(g), "link_layer": lk.name,
                         "nodes": cnt("node"), "ponds": cnt("pond"), "pumps": cnt("pump"), "catchments": cnt("catchment"),
                         "has_prj": lk.with_suffix(".prj").exists(), "coord_unit": unit, "result_columns": ",".join(res_cols),
                         "inp": len(list(d.rglob("*.inp"))), "stsw": len(list(d.rglob("*.stsw"))), "area_km2": round(fp.area / 1e6, 2), "geometry": fp})
            log(f"  {sid} {rows[-1]['model'][:24]:24} 管 {len(g):>6}  塘 {rows[-1]['ponds']:>3}  泵 {rows[-1]['pumps']:>3}  {rows[-1]['area_km2']:>7} km²")
    G = gpd.GeoDataFrame(rows, geometry="geometry", crs=32640)
    # 跨批次认同一个模型：范围交并比 ≥ 0.5
    n = len(G)
    grp = list(range(n))

    def f(i):
        while grp[i] != i:
            grp[i] = grp[grp[i]]
            i = grp[i]
        return i
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = G.geometry.iloc[i], G.geometry.iloc[j]
            if not a.intersects(b):
                continue
            inter = a.intersection(b).area
            iou = inter / a.union(b).area
            cov_i, cov_j = inter / a.area, inter / b.area
            if iou >= 0.5:
                grp[f(i)] = f(j)
            if max(cov_i, cov_j) >= 0.2:
                pairs.append({"a": f"{G.batch.iloc[i]}:{G.model.iloc[i]}", "b": f"{G.batch.iloc[j]}:{G.model.iloc[j]}", "iou": round(iou, 2),
                              "a_covered": round(cov_i, 2), "b_covered": round(cov_j, 2), "same_links": bool(G.links.iloc[i] == G.links.iloc[j])})
    G["group"] = [f(i) for i in range(n)]
    G["group"] = G.group.map({g: k + 1 for k, g in enumerate(sorted(set(G.group)))})
    EXP.mkdir(parents=True, exist_ok=True)
    gp = EXP / "models.gpkg"
    if gp.exists():
        gp.unlink()
    G.to_file(gp, layer="models", driver="GPKG")
    P = pd.DataFrame(pairs)
    P.to_csv(EXP / "model_overlaps.csv", index=False, encoding="utf-8-sig")
    md = ["# 数据现状分析 · 顾问模型盘点", "", f"{len(G)} 个模型目录，来自 {G.batch.nunique()} 批交付；按范围交并比 ≥ 0.5 认作同一个模型，共 {G.group.nunique()} 组。", "",
          "| 组 | 批次:模型 | 管段 | 节点 | 塘 | 泵 | 子汇水区 | 范围 km² | 坐标 | .prj | inp / 工程 | 带运算结果列 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for gid, g in G.sort_values(["group", "batch"]).groupby("group"):
        for r in g.itertuples():
            md.append(f"| {gid} | {r.batch}:{r.model} | {r.links:,} | {r.nodes:,} | {r.ponds} | {r.pumps} | {r.catchments:,} | {r.area_km2} | {r.coord_unit} | {'有' if r.has_prj else '无'} | {r.inp} / {r.stsw} | {r.result_columns or '—'} |")
    if len(P):
        md += ["", "## 范围部分重叠的模型对（一方被覆盖 ≥ 20%）", "", "| A | B | 交并比 | A 被覆盖 | B 被覆盖 | 管段数相同 |", "|---|---|---|---|---|---|"]
        md += [f"| {r.a} | {r.b} | {r.iou} | {r.a_covered} | {r.b_covered} | {'是' if r.same_links else '否'} |" for r in P.sort_values("iou", ascending=False).itertuples()]
    (EXP / "models.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    lineage("explore.models", [Path(C[s]["path"]) for s in ("S10", "S11", "S12", "S13", "S14")], [gp, EXP / "models.md", EXP / "model_overlaps.csv"], {},
            notes=f"{len(G)} 个模型，{G.group.nunique()} 组")
    log(f"→ {EXP / 'models.md'}")
    return G


VERSION_PAIRS = [   # (GDB 图层, shapefile 图层, 比的字段：GDB 名 → shapefile 截断名)
    ("PIPELINE", "PIPELINE", {"PIPE_DIAMETER": "PIPE_DIAME", "INVERT_LEVEL_UPSTREAM": "INVERT_LEV", "INVERT_LEVEL_DOWNSTREAM": "INVERT_L_1", "STATUS": "STATUS"}),
    ("CHAMBER", "MANHOLECHAMBER", {"COVER_LEVEL": "COVER_LEVE", "INVERT_LEVEL": "INVERT_LEV", "STATUS": "STATUS"}),
    ("CATCHBASIN", "CATCHBASIN", {"COVER_LEVEL": "COVER_LEVE", "INVERT_LEVEL": "INVERT_LEV"}),
    ("INLET", "INLET", {"COVER_LEVEL": "COVER_LEVE", "INVERT_LEVEL": "INVERT_LEV"}),
    ("OUTFALL", "OUTFALL", {"INVERT_LEVEL": "INVERT_LEV", "STATUS": "STATUS"}),
    ("SW_CAPPEDEND", "SW_CAPPEDEND", {"INVERT_LEVEL": "INVERT_LEV"}),
    ("PETROLINTERCEPTOR", "PETROLINTERCEPTOR", {}),
    ("SOAKAWAY", "SOAKAWAY", {}),
    ("PS_PUMP", "PUMP", {"FLOW_RATE": "FLOW_RATE", "COMMENTS": "COMMENTS"}),
]


def versions(log=print):
    """DMT 资产库两个版本：S01 GDB（2026-09-25）对 S02 shapefile 导出（2026-10-19）。按 UNITID 对：多了、少了、属性变了。"""
    import pyogrio
    C = {d["id"]: d for d in load("catalog.yaml")["datasets"]}
    gdb, shp = C["S01"]["path"], Path(C["S02"]["path"])
    gl = {n for n, _ in pyogrio.list_layers(gdb)}
    sl = {p.stem for p in shp.glob("*.shp")}
    rows = []
    for lg, ls, fmap in VERSION_PAIRS:
        fa_ = set(pyogrio.read_info(gdb, layer=lg)["fields"])
        fb_ = set(pyogrio.read_info(shp / f"{ls}.shp")["fields"])
        missing = {k: v for k, v in fmap.items() if k not in fa_ or v not in fb_}
        fmap = {k: v for k, v in fmap.items() if k in fa_ and v in fb_}
        a = pyogrio.read_dataframe(gdb, layer=lg, read_geometry=False, columns=["UNITID"] + list(fmap))
        b = pyogrio.read_dataframe(shp / f"{ls}.shp", read_geometry=False, columns=["UNITID"] + list(fmap.values()))
        a["UNITID"] = a.UNITID.astype(object).where(a.UNITID.notna(), "").astype(str).str.strip()
        b["UNITID"] = b.UNITID.astype(object).where(b.UNITID.notna(), "").astype(str).str.strip()
        blank_a, blank_b = int((a.UNITID == "").sum()), int((b.UNITID == "").sum())
        a, b = a[a.UNITID != ""], b[b.UNITID != ""]
        A, B = set(a.UNITID), set(b.UNITID)
        common = A & B
        m = a.drop_duplicates("UNITID").set_index("UNITID").loc[sorted(common)]
        n = b.drop_duplicates("UNITID").set_index("UNITID").loc[sorted(common)]
        changed = {}
        for fa, fb in fmap.items():
            x, y = m[fa], n[fb]
            if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y):
                d = ~np.isclose(x.astype(float).fillna(-9e9), y.astype(float).fillna(-9e9), atol=1e-3)
            else:
                d = x.astype(str).str.strip() != y.astype(str).str.strip()
            changed[fa] = int(d.sum())
        rows.append({"gdb_layer": lg, "shp_layer": ls, "gdb_n": len(a) + blank_a, "shp_n": len(b) + blank_b, "common": len(common),
                     "only_gdb": len(A - B), "only_shp": len(B - A), "dup_ids_gdb": int(a.UNITID.duplicated().sum()), "dup_ids_shp": int(b.UNITID.duplicated().sum()),
                     "changed": changed, "fields_missing": missing, "blank_id_gdb": blank_a, "blank_id_shp": blank_b})
        log(f"  {lg:18} GDB {len(a):>7}  shp {len(b):>7}  共有 {len(common):>7}  只在 GDB {len(A - B):>6}  只在 shp {len(B - A):>6}  改动 {changed}")
    only_g = sorted(gl - {p[0] for p in VERSION_PAIRS} - {"MergeP", "MergeL"})
    only_s = sorted(sl - {p[1] for p in VERSION_PAIRS})
    md = ["# 数据现状分析 · 版本比对：DMT 资产库 GDB（S01，2026-09-25）对 shapefile 导出（S02，2026-10-19）", "",
          f"只在 GDB 里的图层：{', '.join(only_g) or '无'}。只在 shapefile 里的图层：{', '.join(only_s) or '无'}。", "",
          "| 图层（GDB / shp） | GDB | shp | 共有编号 | 只在 GDB | 只在 shp | 编号为空（GDB / shp） | 编号重复（GDB / shp） | 共有编号里属性变了的 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ch = "；".join(f"{k} {v:,}" for k, v in r["changed"].items()) or "—"
        md.append(f"| {r['gdb_layer']} / {r['shp_layer']} | {r['gdb_n']:,} | {r['shp_n']:,} | {r['common']:,} | {r['only_gdb']:,} | {r['only_shp']:,} | {r['blank_id_gdb']} / {r['blank_id_shp']} | {r['dup_ids_gdb']} / {r['dup_ids_shp']} | {ch} |")
    EXP.mkdir(parents=True, exist_ok=True)
    write_json(EXP / "versions.json", {"pairs": rows, "only_gdb_layers": only_g, "only_shp_layers": only_s})
    (EXP / "versions.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    lineage("explore.versions", [gdb, shp], [EXP / "versions.md", EXP / "versions.json"], {})
    log(f"→ {EXP / 'versions.md'}")
    return rows


def gaps(log=print):
    """缺口矩阵（config/requirements.yaml）+ 覆盖度：全市管网、热点有多少落在有顾问模型的范围里。"""
    import geopandas as gpd
    import pyogrio
    import pyproj
    from shapely.geometry import Point
    R = load("requirements.yaml")["requirements"]
    C = {d["id"]: d for d in load("catalog.yaml")["datasets"]}
    M = gpd.read_file(EXP / "models.gpkg")
    cov = M.geometry.union_all()
    aecom = M[M.model.str.startswith("RD-")].geometry.union_all()
    P = pyogrio.read_dataframe(Path(C["S02"]["path"]) / "PIPELINE.shp", columns=["PIPE_SUBTY"])
    P = P[P.PIPE_SUBTY != "Abandoned"]
    L = P.geometry.length
    inside = P.geometry.intersects(cov)
    tr = pyproj.Transformer.from_crs(4326, 32640, always_xy=True)
    H = [h for h in json.loads((ROOT / "exchange" / "hotspots" / "hotspots.json").read_text(encoding="utf-8"))["hotspots"]
         if h.get("lon") and h["hotspot_id"].startswith("ADM")]
    hin = [cov.contains(Point(*tr.transform(h["lon"], h["lat"]))) for h in H]
    stats = {"models_distinct": int(M.group.nunique()), "model_area_km2": round(cov.area / 1e6, 1),
             "pipes_km": round(L.sum() / 1000), "pipes_km_in_models": round(L[inside].sum() / 1000), "pipes_share": round(float(L[inside].sum() / L.sum()), 3),
             "adm_hotspots": len(H), "hotspots_in_models": int(sum(hin)), "aecom_area_km2": round(aecom.area / 1e6, 1)}
    order = {"有": 0, "有·有问题": 1, "部分": 2, "无": 3}
    md = ["# 数据现状分析 · 建模需求与供给缺口", "", "## 覆盖度", "",
          f"- 不同的顾问模型 {stats['models_distinct']} 个，合起来覆盖 {stats['model_area_km2']} km²（AECOM 的 5 个占 {stats['aecom_area_km2']} km²）。",
          f"- 全市在用管道 {stats['pipes_km']:,} km，落在有顾问模型的范围里的 {stats['pipes_km_in_models']:,} km（{100 * stats['pipes_share']:.0f}%）。其余的只有资产库一份数据：没有糙率、子汇水区、泵曲线。",
          f"- ADM 热点 {stats['adm_hotspots']} 个，落在有顾问模型的范围里的 {stats['hotspots_in_models']} 个。", "",
          "## 需求 × 供给", "", "| 类 | 输入 | 用途 | 来源 | 状态 | 证据 | 要向客户要 |", "|---|---|---|---|---|---|---|"]
    for r in sorted(R, key=lambda x: (x["group"], order.get(x["status"], 9))):
        md.append(f"| {r['group']} | {r['item']} | {r['need']} | {', '.join(r['sources']) or '—'} | {r['status']} | {r['evidence']} | {r['ask'] or '—'} |")
    cnt = Counter(r["status"] for r in R)
    md += ["", f"合计 {len(R)} 项：" + "，".join(f"{k} {cnt.get(k, 0)}" for k in ("有", "有·有问题", "部分", "无")) + "。", "",
           "## 要向客户要的（汇总）", ""] + [f"{k}. {r['item']}：{r['ask']}" for k, r in enumerate([x for x in R if x["ask"]], 1)]
    write_json(EXP / "gaps.json", {"stats": stats, "requirements": R})
    (EXP / "gaps.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    lineage("explore.gaps", [CONF / "requirements.yaml", EXP / "models.gpkg", Path(C["S02"]["path"]) / "PIPELINE.shp"], [EXP / "gaps.md", EXP / "gaps.json"], {}, notes=json.dumps(stats, ensure_ascii=False))
    log(json.dumps(stats, ensure_ascii=False))
    log(f"→ {EXP / 'gaps.md'}")
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.explore")
    ap.add_argument("what", choices=["inventory", "models", "versions", "gaps", "all"])
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    steps = ["inventory", "models", "versions", "gaps"] if a.what == "all" else [a.what]
    for s in steps:
        globals()[s]()


if __name__ == "__main__":
    main()
