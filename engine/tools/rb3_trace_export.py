# -*- coding: utf-8 -*-
"""RB-3（RAHA BEACH OUTFALL-3）汇水区：按客户 QGIS 截图描绘的面图层（用户 2026-09-29：客户原 catchment 库漏了 ADM-94 / 192 / 487，
红色区域是包含这 3 个点的正确 catchment；原始数据导不出来，按截图参照库里的图层描）。

做法：
  1. tools/rb3_photo_georef.py + tools/rb3_photo_icp.py：照片里的黑色边界线 ↔ flood 库 catchment.catchment 的 39 个面边界，
     倒角匹配 + ICP 求单应变换（照片是对着屏幕拍的，有透视）；产物 out/catchment_rb3/work/Hm_utm2px.npy、redmask.npy
  2. 本脚本：红色区域（描边 + 内部红点，闭运算填满）向内收缩半个描边宽，收缩量按截图属性 Shape_Area = 2,800,069 m² 定；
     轮廓像素 → UTM 40N，化简 2 m；核对 3 个热点在面内
  3. 输出：RB-3 单独一层；以及「库里原 39 个面 + RB-3」合并的更正版一层。GeoJSON（WGS84）/ Shapefile（UTM 40N）/ KML
检验：3 个热点没参与配准，配准后都落在红色区域里（独立验证）。
用法：python tools/rb3_trace_export.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
W = ROOT / "out" / "catchment_rb3" / "work"
OUT = ROOT / "out" / "交付" / "汇水区更正_RB-3"
ATTR = {"OBJECTID": 147, "Title": "RAHA BEACH OUTFALL-3", "Old_Number": "R.B_OUTFALL-3", "New_Number": "RB-3", "AssignedTo": "Vendor",
        "CatchBasin": 210, "Inlet": 612, "Sub_Catchm": 822, "Status": "Sub Catchments Completed", "Model_Name": "RAHA BEACH OUTFALL-3",
        "Model_Tota": 822, "Model_Stat": "Completed", "HYDROID": 2911170, "Shape_Leng": 20293.3049066, "Shape_Area": 2800069.15694}
TARGET = ATTR["Shape_Area"]
SRC = "按客户 QGIS 截图描绘（照片配准到 flood 库 catchment.catchment，非客户原始几何），2026-09-29"
EXPAND = 1.20        # v2：用户要求在描出的面上外扩约 20%（面积）；None = 只按 Shape_Area 描（v1）


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    import rasterio.features
    from affine import Affine
    from scipy import ndimage as ndi
    from shapely.geometry import shape, Point, MultiPolygon, Polygon
    from shapely.ops import transform as stransform, unary_union
    Hm = np.load(W / "Hm_utm2px.npy"); Hi = np.linalg.inv(Hm)
    M = np.load(W / "redmask.npy")
    DTin = ndi.distance_transform_edt(M)

    def px2utm(x, y, z=None):
        q = np.c_[np.asarray(x, float), np.asarray(y, float), np.ones(np.size(x))] @ Hi.T
        return q[:, 0] / q[:, 2], q[:, 1] / q[:, 2]

    def poly_at(t):
        m = (DTin > t).astype(np.uint8)
        # 像素中心坐标：shapes 给的是像素角点坐标（列, 行）
        geoms = [shape(g) for g, v in rasterio.features.shapes(m, mask=m.astype(bool), transform=Affine.identity()) if v == 1]
        if not geoms:
            return None
        g = max(geoms, key=lambda x: x.area) if len(geoms) > 1 else geoms[0]
        gu = stransform(lambda x, y, z=None: px2utm(x, y), g)
        return gu.buffer(0).simplify(2.0, preserve_topology=True)

    lo, hi = 0.0, 20.0
    for _ in range(30):                                  # 二分：面积 = 截图属性的 Shape_Area
        t = (lo + hi) / 2; a = poly_at(t).area
        lo, hi = (t, hi) if a > TARGET else (lo, t)
    t = (lo + hi) / 2; P = poly_at(t)
    P0 = P
    # v2（用户 2026-09-29，看了系统地图后）：在按 Shape_Area 描出的面基础上外扩约 20%（面积 ×1.20，等距外扩、斜接角保形）
    if EXPAND:
        lo2, hi2 = 0.0, 200.0
        for _ in range(40):
            d = (lo2 + hi2) / 2; a = P0.buffer(d, join_style=2, mitre_limit=3.0).area
            lo2, hi2 = (d, hi2) if a < EXPAND * P0.area else (lo2, d)
        dist = (lo2 + hi2) / 2
        P = P0.buffer(dist, join_style=2, mitre_limit=3.0).simplify(2.0, preserve_topology=True)
        print(f"外扩 {dist:.1f} m：面积 {P0.area:,.0f} → {P.area:,.0f} m²（×{P.area / P0.area:.3f}）")
    parts = [P] if P.geom_type == "Polygon" else list(P.geoms)
    print(f"描边内缩 {t:.2f} 像素（约 {t * 3.49:.1f} m）：面积 {P.area:,.0f} m²（目标 {TARGET:,.0f}），周长 {P.length:,.0f} m（截图 {ATTR['Shape_Leng']:,.0f}），{len(parts)} 块")
    Hs = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    chk = {h: (bool(P.contains(Hs.loc[h].geometry)), round(P.exterior.distance(Hs.loc[h].geometry) if P.geom_type == "Polygon" else P.boundary.distance(Hs.loc[h].geometry), 1))
           for h in ("ADM-94", "ADM-192", "ADM-487", "ADM-202", "ADM-90")}
    for h, (inside, d) in chk.items():
        print(f"  {h}：{'在面内' if inside else '不在面内'}，离边界 {d} m")
    # 配准精度（ICP 内点）
    rec = {**ATTR, "src": SRC + ("；v2：按用户要求在描出的面上外扩约 20%（面积 ×1.20）" if EXPAND else ""), "version": "v2 外扩 20%" if EXPAND else "v1 按 Shape_Area",
           "v1_area": round(P0.area), "trace_area": round(P.area), "trace_leng": round(P.length), "erode_px": round(t, 2),
           "hs_inside": ";".join(h for h, (i, _) in chk.items() if i)}
    OUT.mkdir(parents=True, exist_ok=True)
    g1 = gpd.GeoDataFrame([rec], geometry=[P], crs=32640)
    g1.to_crs(4326).to_file(OUT / "RB-3_RAHA_BEACH_OUTFALL-3.geojson", driver="GeoJSON")
    g1.to_file(OUT / "RB-3_RAHA_BEACH_OUTFALL-3.shp", driver="ESRI Shapefile", encoding="utf-8")
    (OUT / "RB-3_RAHA_BEACH_OUTFALL-3.cpg").write_text("UTF-8", encoding="ascii")
    # 合并更正版：库里 39 个面 + RB-3（新增一行，原 39 行不动）
    G = gpd.read_file(W / "catch39.geojson")
    G = G.set_crs(32640, allow_override=True) if G.crs is None else G.to_crs(32640)
    G["src"] = "flood 库 catchment.catchment 原记录"; G["New_Number"] = None
    new = gpd.GeoDataFrame([{"fid": None, "label": "RB-3", "New_Number": "RB-3", "src": SRC}], geometry=[P], crs=32640)
    C = gpd.GeoDataFrame(pd_concat([G, new]), crs=32640)
    C["area_km2"] = (C.geometry.area / 1e6).round(3)
    C.to_crs(4326).to_file(OUT / "catchment_39+RB-3_更正版.geojson", driver="GeoJSON")
    C.to_file(OUT / "catchment_39+RB-3_更正版.shp", driver="ESRI Shapefile", encoding="utf-8")
    (OUT / "catchment_39+RB-3_更正版.cpg").write_text("UTF-8", encoding="ascii")
    ov = [(r.label, round(P.intersection(r.geometry).area)) for r in G.itertuples() if P.intersects(r.geometry) and P.intersection(r.geometry).area > 1]
    print("  和库里原有面重叠：", ov)
    # KML
    W84 = gpd.GeoSeries(parts, crs=32640).to_crs(4326)
    ring = lambda c: " ".join(f"{x:.7f},{y:.7f},0" for x, y, *_ in c)
    pm = "".join("<Polygon><outerBoundaryIs><LinearRing><coordinates>" + ring(p.exterior.coords) + "</coordinates></LinearRing></outerBoundaryIs></Polygon>" for p in W84)
    ext = "".join(f"<Data name='{k}'><value>{v}</value></Data>" for k, v in rec.items())
    (OUT / "RB-3_RAHA_BEACH_OUTFALL-3.kml").write_text(
        "<?xml version='1.0' encoding='UTF-8'?><kml xmlns='http://www.opengis.net/kml/2.2'><Document><name>RB-3 RAHA BEACH OUTFALL-3</name>"
        "<Style id='s'><LineStyle><color>ff0000ff</color><width>2.5</width></LineStyle><PolyStyle><color>440000ff</color></PolyStyle></Style>"
        f"<Placemark><name>RB-3 RAHA BEACH OUTFALL-3</name><styleUrl>#s</styleUrl><ExtendedData>{ext}</ExtendedData><MultiGeometry>{pm}</MultiGeometry></Placemark></Document></kml>",
        encoding="utf-8")
    json.dump({"erode_px": t, "area_m2": P.area, "perim_m": P.length, "hotspots": chk}, open(W / "trace_result.json", "w"), ensure_ascii=False, indent=1)
    print("→", OUT, sorted(x.name for x in OUT.iterdir()))


def pd_concat(frames):
    import pandas as pd
    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    main()
