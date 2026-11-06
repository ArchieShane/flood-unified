# -*- coding: utf-8 -*-
"""系统盘点 Parsons_Model Data Export（客户转来的 Parsons 模型导出）：每个模型的图层、要素数、字段、坐标系、范围，
与客户 39 个汇水区的对应，以及汇总表 / 说明 / 既有塘 / 关键热点。

用法：python tools/inventory_parsons_export.py ["D:\\阿布扎比\\Parsons_Model Data Export"]
产物：out/parsons_export_inventory.json
"""
from __future__ import annotations

import glob
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = pathlib.Path(r"D:\阿布扎比\Parsons_Model Data Export")
# 图层名（各模型叫法不一）→ 角色
ROLE = [(r"^(catchments?|ca|subcatch.*)$", "子汇水区"), (r"^(conduits?|co|pipes?|gravity.*)$", "管道"), (r"^(manholes?|mh|nodes?|junctions?)$", "检查井"),
        (r"^(outfalls?|of)$", "出水口"), (r"^(ponds?|pond)$", "塘"), (r"^(pumps?|pump)$", "泵"), (r"^(wetwells?|ww|wet.?well)$", "湿井"),
        (r"^(pressure.?pipes?|prpipes?|pressurepipe|forcemain.*)$", "压力管"), (r"^(pressure.?junctions?|prnodes?)$", "压力管节点"),
        (r"^(catch.?basins?|cb|inlets?|gully.*)$", "雨水口"), (r"^(storage.*|tank.*)$", "蓄水")]


def role(name):
    n = name.lower().strip()
    for pat, r in ROLE:
        if re.match(pat, n):
            return r
    return "其他"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    import openpyxl
    from shapely.validation import make_valid
    src = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
    C = gpd.read_file(ROOT / "console" / "data" / "catchments_sw.geojson").to_crs(32640)
    C["geometry"] = [make_valid(g).buffer(0) for g in C.geometry]
    out = {"src": str(src), "models": {}, "files": {}}
    base = src / "26092026"
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        layers = {}
        allg = []
        for shp in sorted(glob.glob(str(d / "**" / "*.shp"), recursive=True)):
            nm = pathlib.Path(shp).stem
            try:
                g = gpd.read_file(shp)
            except Exception as e:  # noqa: BLE001
                layers[nm] = {"error": str(e)[:120]}; continue
            crs = str(g.crs) if g.crs else None
            g2 = g.set_crs(32640, allow_override=True) if g.crs is None else g.to_crs(32640)
            gt = g2.geom_type.value_counts().to_dict() if len(g2) else {}
            cols = [c for c in g.columns if c != "geometry"]
            rec = {"role": role(nm), "n": len(g), "geom": gt, "crs": crs or "（无 .prj，按 UTM 40N）", "fields": cols}
            if rec["role"] == "子汇水区" and len(g2):
                rec["area_ha"] = round(float(g2.area.sum()) / 1e4, 1)
                for f in ("SCS_CN", "TC"):
                    if f in g.columns:
                        v = g[f].astype(float)
                        rec[f] = {"min": round(float(v.min()), 1), "max": round(float(v.max()), 1), "mean": round(float(v.mean()), 1)}
            if rec["role"] == "管道" and len(g2):
                rec["length_km"] = round(float(g2.length.sum()) / 1000, 1)
            layers[nm] = rec
            if len(g2):
                allg.append(g2.geometry)
        ext = None; rel = []
        if allg:
            import pandas as pd
            u = gpd.GeoSeries(pd.concat(allg), crs=32640)
            ca = next((l for l in layers.values() if l.get("role") == "子汇水区"), None)
            env = u.total_bounds.tolist()
            ext = {"bounds_utm": [round(v) for v in env], "w_km": round((env[2] - env[0]) / 1000, 1), "h_km": round((env[3] - env[1]) / 1000, 1)}
            # 与客户汇水区的对应：取子汇水区（没有就用全部要素的凸包）
            shp_ca = [s for s in glob.glob(str(d / "**" / "*.shp"), recursive=True) if role(pathlib.Path(s).stem) == "子汇水区"]
            if shp_ca:
                gc = gpd.read_file(shp_ca[0]); gc = gc.set_crs(32640, allow_override=True) if gc.crs is None else gc.to_crs(32640)
                area = make_valid(gpd.GeoSeries([make_valid(x).buffer(0) for x in gc.geometry], crs=32640).union_all()).buffer(0)
            else:
                from shapely.geometry import GeometryCollection
                area = GeometryCollection([x for x in u if x is not None and not x.is_empty]).convex_hull   # 不做 union：几何可能自相交
            for _, c in C.iterrows():
                if area.intersects(c.geometry):
                    ov = area.intersection(c.geometry).area
                    if ov / max(area.area, 1) >= 0.02 or ov / max(c.geometry.area, 1) >= 0.2:
                        rel.append({"label": c.label, "pct_of_model": round(ov / area.area * 100), "pct_of_catch": round(ov / c.geometry.area * 100)})
            rel = sorted({r["label"]: r for r in rel}.values(), key=lambda r: -r["pct_of_model"])
        roles = {}
        for l in layers.values():
            if "role" in l:
                roles[l["role"]] = roles.get(l["role"], 0) + l["n"]
        out["models"][d.name] = {"layers": layers, "roles": roles, "extent": ext, "client_catch": rel,
                                 "n_files": sum(1 for _ in d.rglob("*") if _.is_file())}
        print(f"{d.name:<24} 角色 {roles}；客户汇水区 {[r['label'] + ' ' + str(r['pct_of_catch']) + '%' for r in rel][:4]}")
    # 汇总表
    for x in glob.glob(str(base / "*.xlsx")):
        wb = openpyxl.load_workbook(x, read_only=True, data_only=True)
        sheets = {}
        for ws in wb.worksheets:
            rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]
            rows = [r for r in rows if any(c is not None for c in r)]
            sheets[ws.title] = {"n_rows": len(rows), "head": [[str(c) if c is not None else "" for c in r[:16]] for r in rows[:25]]}
        out["files"][os.path.basename(x)] = sheets
    nt = base / "Notes.txt"
    out["files"]["Notes.txt"] = nt.read_text(encoding="utf-8", errors="replace").strip() if nt.exists() else None
    # 既有塘
    ex = {}
    for shp in glob.glob(str(src / "Existing Storage" / "**" / "*.shp"), recursive=True):
        g = gpd.read_file(shp); g2 = g.set_crs(32640, allow_override=True) if g.crs is None else g.to_crs(32640)
        ex[str(pathlib.Path(shp).relative_to(src))] = {"n": len(g), "fields": [c for c in g.columns if c != "geometry"], "area_ha": round(float(g2.area.sum()) / 1e4, 1),
                                                      "sample": g.drop(columns="geometry").head(5).astype(str).to_dict("records")}
    out["existing_storage"] = ex
    out["critical_hotspots"] = {"pdf": sorted(os.path.basename(p) for p in glob.glob(str(src / "Critical Hotspots" / "PDF" / "*.pdf"))),
                                "cad": sorted(os.path.basename(p) for p in glob.glob(str(src / "Critical Hotspots" / "CAD" / "*")))}
    (ROOT / "out" / "parsons_export_inventory.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("→ out/parsons_export_inventory.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
