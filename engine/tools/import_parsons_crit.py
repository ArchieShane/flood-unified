# -*- coding: utf-8 -*-
"""导入 Parsons 的「关键热点」方案（Critical Hotspots C1–C29）：塘、泵 / 进水口、管线，以及每个热点群包含哪些客户热点。

为什么（2026-09-28）：客户问的 C1 不是汇水区，是 Parsons 的关键热点群 C1（Parsons_Model Data Export.rar → Critical Hotspots/）。
用户要在系统上看到 C1 和 ADM-94 / 487 / 192 的空间关系。

来源：
  SHP/AUH-Hotspot_Locations_Combined.zip   AUH_HotSpot_Locations_{Ponds,Pumps,Pipes}（EPSG:32640，CAD 转出）
  PDF/CRITICAL HOT*SPOT*.pdf               每张图纸上的「HS - nn」= 这个热点群包含的客户热点（按 ADM 编号，已与位置核对）
口径：
  · 热点群编号从 CAD 图层名里取（AUH-Hotspot Locations_C1_… → C1）；Ponds 表里另有 Location 字段
  · 塘的 Volume 字段 = 面积 × 1 m（Depth 都是 1.0），是 Parsons 的占位库容，不是设计值
  · 图纸 CRITICAL HOTSPOT SCHEMATIC_1009.pdf（184 MB 总图）没有解析

用法：python tools/import_parsons_crit.py [解压目录]
产物：console/data/parsons_crit.geojson（设施）、console/data/parsons_crit_groups.json（热点群 → 客户热点、范围、注记点）
"""
from __future__ import annotations

import glob
import json
import os
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "console" / "data"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); import datapaths  # noqa: E402  客户数据位置：config.toml [data]
SRC = datapaths.get("parsons_root")     # Parsons_Model Data Export.rar 的解压目录（config.toml [data].parsons_root）


ALIAS = {"C7": "C7&28", "C6&C7": "C6", "Baniyas": "Baniyas Lake", "BaniyasLake": "Baniyas Lake"}      # 同一处的不同写法（塘表 Location 与图层名 / 图纸名不一致）


def code_of(layer: str) -> str | None:
    m = re.search(r"Locations_(C\d+(?:\s*&\s*\d+)?|Baniyas[ A-Za-z]*)", str(layer))
    c = m.group(1).strip().replace(" & ", "&").replace(" ", "") if m else None
    return ALIAS.get(c, c) if c else None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    import pymupdf as fitz
    from shapely.geometry import mapping
    from shapely.ops import unary_union
    src = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
    shp = src / "shp" / "AUH-Hotspot_Locations_Combined"
    if not shp.exists():                   # 解压到仓库 out/ 下，不往客户数据目录里写
        shp = ROOT / "out" / "parsons_shp" / "AUH-Hotspot_Locations_Combined"
        if not shp.exists():
            zipfile.ZipFile(src / "Critical Hotspots" / "SHP" / "AUH-Hotspot_Locations_Combined.zip").extractall(shp.parent)
    feats, geoms = [], {}
    for fn, kind in (("Ponds", "pond"), ("Pumps", "point"), ("Pipes", "pipe")):
        g = gpd.read_file(shp / f"AUH_HotSpot_Locations_{fn}.shp").set_crs(32640, allow_override=True)
        for _, r in g.iterrows():
            code = code_of(r["Layer"]) or (str(r.get("Location")).replace(" ", "") if kind == "pond" and r.get("Location") else None)
            code = ALIAS.get(code, code)
            sub = str(r["Layer"]).split("$0$")[-1]
            gg = r.geometry
            if gg is None or gg.is_empty:
                continue
            geoms.setdefault(code, []).append(gg)
            pr = {"code": code or "—", "kind": kind, "sub": sub}
            if kind == "pond":
                pr.update(area_ha=round(float(r["Shape_Area"]) / 1e4, 2), volume_m3=round(float(r["Volume"])), depth_m=float(r["Depth"]))
            if kind == "pipe":
                pr.update(length_m=round(float(gg.length)), pipe=r.get("Pipe"))
            feats.append((gg, pr))
    gdf = gpd.GeoDataFrame([p for _, p in feats], geometry=[g for g, _ in feats], crs=32640).to_crs(4326)
    gj = json.loads(gdf.to_json(drop_id=True))
    gj.update({"layer": "parsons_crit", "source": "Parsons_Model Data Export.rar → Critical Hotspots（SHP + PDF 图纸，2026-09-10 版）",
               "note": "塘的库容是面积 × 1 m 的占位值（Depth 都是 1.0）"})
    (DATA / "parsons_crit.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    # 图纸 → 包含的客户热点
    hs = {}
    for f in sorted(glob.glob(str(src / "Critical Hotspots" / "PDF" / "*.pdf"))):
        if os.path.getsize(f) > 60e6:
            continue
        name = os.path.basename(f)
        m = re.search(r"C\s?(\d+)(?:\s*&\s*(\d+))?", name.replace("HOT SPOT", "").replace("HOTSPOT", ""))
        code = ("C" + m.group(1) + (f"&{m.group(2)}" if m.group(2) else "")) if m and "Baniyas" not in name else ("Baniyas Lake" if "Baniyas" in name else name)
        code = ALIAS.get(code, code)
        t = " ".join(p.get_text() for p in fitz.open(f))
        ids = sorted({int(x) for x in re.findall(r"HS\s*-\s*(\d{1,4})", t)})
        rec = hs.setdefault(code, {"drawings": [], "hotspots": set()})
        rec["drawings"].append(name); rec["hotspots"] |= set(ids)
    H = gpd.read_file(DATA / "hotspots.geojson").set_index("hotspot_id")
    tr = gpd.GeoSeries([], crs=32640)
    groups = {}
    for code in sorted(set(geoms) | set(hs), key=lambda c: (str(c) == "None", str(c))):
        if code is None:
            continue
        gs = geoms.get(code, [])
        u = unary_union(gs) if gs else None
        rp = gpd.GeoSeries([u.representative_point() if u.geom_type in ("Polygon", "MultiPolygon") else u.centroid], crs=32640).to_crs(4326).iloc[0] if u is not None else None
        bb = gpd.GeoSeries([u.envelope], crs=32640).to_crs(4326).total_bounds.tolist() if u is not None else None
        ids = sorted(hs.get(code, {}).get("hotspots", set()))
        members = []
        for i in ids:
            k = f"ADM-{i}"
            if k in H.index:
                p = H.loc[k]
                members.append({"id": k, "location": p.get("location"), "criticality": p.get("criticality"),
                                "lat": round(p.geometry.y, 6), "lon": round(p.geometry.x, 6)})
        n = lambda kind: sum(1 for _, pr in feats if pr["code"] == code and pr["kind"] == kind)
        groups[code] = {"code": code, "drawings": hs.get(code, {}).get("drawings", []), "hotspots": members,
                        "n_ponds": n("pond"), "n_points": n("point"), "n_pipes": n("pipe"),
                        "pond_ha": round(sum(pr.get("area_ha", 0) for _, pr in feats if pr["code"] == code), 1),
                        "label_ll": [round(rp.y, 6), round(rp.x, 6)] if rp is not None else None, "bounds": bb}
    (DATA / "parsons_crit_groups.json").write_text(json.dumps({"source": gj["source"], "note": "hotspots = 图纸上标注的 HS 编号，按 ADM 客户热点对上",
                                                                "groups": groups}, ensure_ascii=False, indent=1), encoding="utf-8")
    for c, g in groups.items():
        print(f"  {c:<14} 塘 {g['n_ponds']:>2}（{g['pond_ha']} ha）点 {g['n_points']:>3} 管 {g['n_pipes']:>3}　热点 {[m['id'] for m in g['hotspots']]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
