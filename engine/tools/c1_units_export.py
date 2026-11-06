# -*- coding: utf-8 -*-
"""C1 汇水区（客户单元版）：客户 Catchment 图层里的 3 个面拼成（用户 2026-09-29 给了 147 / 31 / 86 三个面的截图和属性）。
  147 RB-3（RAHA BEACH OUTFALL-3）：用 v3（第一张照片描 + 外扩 20 % + 对齐道路，用户看过）；新给的 147 照片重描一遍做校核，没过（见说明）
  31  MBZ（MussafahRD09，Old RD-09）：tools/photo_catch_trace.py 描，面积按 Shape_Area 定
  86  ADGC-10（Outfall-B_C，Old OUTFALL-C）：同上
融合：重叠按 RB-3 > ADGC-10 > MBZ 切；空洞与拼缝分给共用边界最长的单元；合并面无空洞（用户 2026-09-29）。
产物：out/交付/C1汇水区_客户单元/（单元 3 个面 + 合并 1 个面，GeoJSON / Shapefile / KML）、console/data/c1_units.geojson
用法：python tools/c1_units_export.py
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "交付" / "C1汇水区_客户单元"
SRC = "按客户 QGIS 截图描绘（照片配准到 flood 库 catchment.catchment），非客户原始几何；2026-09-29"
UNITS = [
    {"OBJECTID": 147, "Title": "RAHA BEACH OUTFALL-3", "Old_Number": "R.B_OUTFALL-3", "New_Number": "RB-3", "CatchBasin": 210, "Inlet": 612, "Sub_Catchm": 822,
     "Model_Name": "RAHA BEACH OUTFALL-3", "HYDROID": 2911170, "Shape_Leng": 20293.3049066, "Shape_Area": 2800069.15694, "how": "v3：第一张照片描 + 外扩 20 % + 对齐道路（用户确认过）"},
    {"OBJECTID": 31, "Title": "MussafahRD09", "Old_Number": "RD-09", "New_Number": "MBZ", "CatchBasin": 762, "Inlet": 3905, "Sub_Catchm": 4455,
     "Model_Name": "MussafahRD09", "HYDROID": 2911054, "Shape_Leng": 27623.8919354, "Shape_Area": 14631222.0024, "how": "照片描，面积按 Shape_Area 定"},
    {"OBJECTID": 86, "Title": "Outfall-B_C", "Old_Number": "OUTFALL-C", "New_Number": "ADGC-10", "CatchBasin": 179, "Inlet": 415, "Sub_Catchm": 594,
     "Model_Name": "Outfall-B_C", "HYDROID": 2911109, "Shape_Leng": 18414.9927824, "Shape_Area": 2207574.34217, "how": "照片描，面积按 Shape_Area 定"},
]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    from shapely.ops import unary_union
    geoms = [gpd.read_file(ROOT / "out" / "交付" / "汇水区更正_RB-3" / "RB-3_RAHA_BEACH_OUTFALL-3.shp").geometry.iloc[0],
             gpd.read_file(ROOT / "out" / "catchment_trace" / "31" / "trace.geojson").geometry.iloc[0],
             gpd.read_file(ROOT / "out" / "catchment_trace" / "86" / "trace.geojson").geometry.iloc[0]]
    # 融合（用户 2026-09-29「有空洞面在里面，需要做个融合」）：
    #  ① 重叠：按 RB-3（用户确认过）> ADGC-10 > MBZ 的次序切掉，单元之间不重叠
    #  ② 空洞 / 拼缝：三块并集闭运算 60 m 后，外轮廓里所有不属于任何单元的地方，分给和它共用边界最长的单元
    #  ③ 合并面 = 三个单元并集，只取外轮廓（无空洞）
    from shapely.geometry import Polygon, MultiPolygon
    g0, g1, g2 = geoms
    g2 = g2.difference(g0)
    g1 = g1.difference(g0).difference(g2)
    geoms = [g0, g1, g2]
    un = unary_union(geoms).buffer(60.0).buffer(-60.0)
    shell = Polygon(un.exterior) if un.geom_type == "Polygon" else unary_union([Polygon(x.exterior) for x in un.geoms])
    rest = shell.difference(unary_union(geoms))
    pieces = [x for x in (rest.geoms if hasattr(rest, "geoms") else [rest]) if x.area > 1.0]
    add = [[], [], []]
    for pc in pieces:
        k = max(range(3), key=lambda i: pc.boundary.intersection(geoms[i].buffer(1.0)).length)
        add[k].append(pc)
    print("补空洞 / 拼缝：", len(pieces), "块，", round(sum(x.area for x in pieces)), "m²，分给", [round(sum(x.area for x in a_)) for a_ in add])
    def clean(g):
        g = g.buffer(0.5).buffer(-0.5)
        if g.geom_type == "MultiPolygon":
            g = max(g.geoms, key=lambda x: x.area)
        return Polygon(g.exterior)
    geoms = [clean(unary_union([geoms[i], *add[i]])) for i in range(3)]
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    hs = ["ADM-21", "ADM-90", "ADM-94", "ADM-104", "ADM-192", "ADM-202", "ADM-486", "ADM-487"]
    rows = []
    for u, g in zip(UNITS, geoms):
        rows.append({**u, "AssignedTo": "Vendor", "Status": "Sub Catchments Completed", "Model_Stat": "Completed", "src": SRC,
                     "trace_area": round(g.area), "trace_leng": round(g.length), "hs_inside": ";".join(h for h in hs if g.contains(H.loc[h].geometry))})
    U = gpd.GeoDataFrame(rows, geometry=geoms, crs=32640)
    uni = unary_union(geoms).buffer(1.0).buffer(-1.0)
    uni = Polygon(uni.exterior) if uni.geom_type == "Polygon" else unary_union([Polygon(x.exterior) for x in uni.geoms])
    print("合并面：", uni.geom_type, "空洞", len(uni.interiors) if uni.geom_type == "Polygon" else "多块")
    M = gpd.GeoDataFrame([{"id": "C1", "name": "C1 汇水区（客户单元 RB-3 + MBZ + ADGC-10）", "units": "147 RB-3 / 31 MBZ / 86 ADGC-10",
                           "area_km2": round(uni.area / 1e6, 3), "hs_inside": ";".join(h for h in hs if uni.contains(H.loc[h].geometry)), "src": SRC}], geometry=[uni], crs=32640)
    OUT.mkdir(parents=True, exist_ok=True)
    for gdf, nm in ((U, "C1_客户单元_3个面"), (M, "C1_汇水区_合并")):
        gdf.to_crs(4326).to_file(OUT / f"{nm}.geojson", driver="GeoJSON")
        gdf.to_file(OUT / f"{nm}.shp", driver="ESRI Shapefile", encoding="utf-8")
        (OUT / f"{nm}.cpg").write_text("UTF-8", encoding="ascii")
    ring = lambda c: " ".join(f"{x:.7f},{y:.7f},0" for x, y, *_ in c)
    W = U.to_crs(4326); Mw = M.to_crs(4326).geometry.iloc[0]
    col = {"RB-3": "ff2626dc", "MBZ": "ffeb6325", "ADGC-10": "ff3da316"}
    pms = "".join(f"<Placemark><name>{r.New_Number} {r.Title}</name><Style><LineStyle><color>{col[r.New_Number]}</color><width>2.5</width></LineStyle><PolyStyle><color>33{col[r.New_Number][2:]}</color></PolyStyle></Style>"
                  f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(r.geometry.exterior.coords)}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>" for r in W.itertuples())
    pms += (f"<Placemark><name>C1 汇水区（合并）</name><Style><LineStyle><color>ff000000</color><width>3</width></LineStyle><PolyStyle><fill>0</fill></PolyStyle></Style>"
            f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(Mw.exterior.coords)}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>")
    (OUT / "C1_汇水区.kml").write_text(f"<?xml version='1.0' encoding='UTF-8'?><kml xmlns='http://www.opengis.net/kml/2.2'><Document><name>C1 汇水区（客户单元）</name>{pms}</Document></kml>", encoding="utf-8")
    gj = json.loads(W.to_json()); gj["merged"] = json.loads(M.to_crs(4326).to_json()); gj["source"] = SRC
    (ROOT / "console" / "data" / "c1_units.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    for r in rows:
        print(f"{r['New_Number']:<8} {r['trace_area'] / 1e6:6.2f} km²（截图 {r['Shape_Area'] / 1e6:.2f}）· 热点 {r['hs_inside']}")
    print(f"合并 {uni.area / 1e6:.2f} km² · 热点 {M.hs_inside.iloc[0]}")


if __name__ == "__main__":
    main()
