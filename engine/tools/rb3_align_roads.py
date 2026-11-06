# -*- coding: utf-8 -*-
"""RB-3 v3：把按截图描的面（v2，外扩 20%）和底图道路对齐（用户 2026-09-29）：
  · 右上（北段长条）：和街道边界对齐——用 makani udm_roadcentreline 把街道围成街区，取与 v2 重叠 ≥ 50% 的街区合并；
  · 左下（E20 / E22 立交那一块 + 伸出去的尾巴）：和高速公路对齐——取 makani udm_roadsurface 里高速 / 立交 / 主干道的路面，
    外扩 25 m（路肩与绿化带）并闭运算 60 m（把匝道围起来的内场填上），与 v2 的这一块（外扩 120 m 的范围内）相交；
  · 连接两块的 E20 走廊：同左下的做法。
  三块合并、去掉细碎，核对 3 个热点仍在面内。
输入：out/交付/汇水区更正_RB-3/RB-3_RAHA_BEACH_OUTFALL-3.shp（v2）、out/catchment_rb3/work/udm_roadcentreline.gpkg、udm_roadsurface.gpkg
输出：覆盖 out/交付/汇水区更正_RB-3/ 下的 RB-3 文件与合并更正版（v2 另存 v2_外扩20%/）、console/data/rb3_catch.geojson
用法：python tools/rb3_align_roads.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
D = ROOT / "out" / "交付" / "汇水区更正_RB-3"
W = ROOT / "out" / "catchment_rb3" / "work"
NAME = "RB-3_RAHA_BEACH_OUTFALL-3"
Y_SPLIT_LOBE = 2700300.0      # 北段长条 / 南边立交的分界（UTM y），长条南端在 E20 北侧
X_SPLIT = 251450.0            # 立交块 / E20 走廊的分界（UTM x）
HW_TYPES = ["highway", "interchange", "majorArterial", "subArterial"]
STREET_TYPES = ["Minor Local", "Minor Collector", "Major Collector Road", "Minor Arterial", "Major Arterial", "Rural Expressway", "Freeways", "Expressways", "internalRoad"]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    from shapely.geometry import box, Polygon, MultiPolygon
    from shapely.ops import polygonize, unary_union
    V2 = gpd.read_file(D / f"{NAME}.shp")
    attrs = V2.drop(columns="geometry").iloc[0].to_dict()
    P = V2.geometry.iloc[0]
    x0, y0, x1, y1 = P.bounds
    CL = gpd.read_file(W / "udm_roadcentreline.gpkg"); RS = gpd.read_file(W / "udm_roadsurface.gpkg")
    # ---- 北段长条：街区
    north = P.intersection(box(x0 - 500, Y_SPLIT_LOBE, x1 + 500, y1 + 500))
    lines = CL[CL.roadtype.isin(STREET_TYPES)].geometry.clip(north.buffer(600).envelope)
    blocks = [b for b in polygonize(unary_union(list(lines))) if b.area > 500]
    pick = [b for b in blocks if b.intersection(north).area >= 0.5 * b.area]
    north_new = unary_union(pick).buffer(1.0).buffer(-1.0)
    print(f"北段：街区 {len(blocks)} 个，取 {len(pick)} 个；面积 {north.area / 1e4:,.1f} → {north_new.area / 1e4:,.1f} ha")
    # ---- 南边（立交 + 尾巴）与 E20 走廊：高速路面
    south = P.intersection(box(x0 - 500, y0 - 500, x1 + 500, Y_SPLIT_LOBE))
    hw = unary_union(list(RS[RS.roadtype.isin(HW_TYPES)].geometry.clip(south.buffer(800).envelope)))
    corridor = hw.buffer(25.0).buffer(60.0).buffer(-60.0)
    south_new = corridor.intersection(south.buffer(120.0))
    # 立交内场：v2 本来就盖住、又被匝道围住的地方保留
    south_new = unary_union([south_new, south.intersection(corridor.convex_hull).intersection(south_new.buffer(80.0).buffer(-80.0) if False else south)]).buffer(1.0).buffer(-1.0)
    south_new = south_new.intersection(unary_union([corridor.buffer(0), south.buffer(-40.0)]))
    print(f"南边：面积 {south.area / 1e4:,.1f} → {south_new.area / 1e4:,.1f} ha")
    new = unary_union([north_new, south_new, north_new.buffer(40).intersection(south_new.buffer(40)).buffer(0)])
    new = new.buffer(5.0).buffer(-5.0)
    if new.geom_type == "MultiPolygon":
        parts = sorted(new.geoms, key=lambda g: -g.area)
        print("  分成", len(parts), "块，面积", [round(g.area / 1e4, 2) for g in parts[:6]])
        new = unary_union([g for g in parts if g.area > 2e4])
    new = Polygon(new.exterior) if new.geom_type == "Polygon" else MultiPolygon([Polygon(g.exterior) for g in new.geoms])
    new = new.simplify(1.0, preserve_topology=True)
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    chk = {h: (bool(new.contains(H.loc[h].geometry)), round(new.boundary.distance(H.loc[h].geometry), 1)) for h in ("ADM-94", "ADM-192", "ADM-487")}
    print(f"v3：面积 {new.area:,.0f} m²（v2 {P.area:,.0f}），周长 {new.length:,.0f} m；", chk)
    return P, new, attrs, chk, north_new, south_new


def finalize():
    """把 v3 写成正式文件（v2 另存）、更新系统图层。"""
    import pickle
    import geopandas as gpd
    from shapely.geometry import Polygon
    P, new, attrs, chk, _n, _s = pickle.load(open(W / "v3_tmp.pkl", "rb"))
    new = new.buffer(-10.0).buffer(10.0, join_style=2)                 # 去掉街区拼接处的细刺
    if new.geom_type == "MultiPolygon":
        new = max(new.geoms, key=lambda g: g.area)
    new = Polygon(new.exterior).simplify(1.0, preserve_topology=True)
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    chk = {h: (bool(new.contains(H.loc[h].geometry)), round(new.boundary.distance(H.loc[h].geometry), 1)) for h in ("ADM-94", "ADM-192", "ADM-487")}
    assert all(v[0] for v in chk.values()), chk
    bk = D / "v2_外扩20%"; bk.mkdir(exist_ok=True)
    for f in D.glob(f"{NAME}.*"):
        shutil.copy2(f, bk / f.name)
    rec = dict(attrs)
    rec.update(version="v3 对齐道路", v2_area=round(P.area), trace_area=round(new.area), trace_leng=round(new.length),
               src=str(rec.get("src", "")).split("；v2")[0] + "；v2 外扩约 20%；v3 北段按 makani 街道中心线围成的街区对齐、南段按高速 / 立交路面对齐（2026-09-29）",
               hs_inside=";".join(h for h, (i, _) in chk.items() if i))
    g = gpd.GeoDataFrame([rec], geometry=[new], crs=32640)
    g.to_crs(4326).to_file(D / f"{NAME}.geojson", driver="GeoJSON")
    g.to_file(D / f"{NAME}.shp", driver="ESRI Shapefile", encoding="utf-8")
    ring = lambda c: " ".join(f"{x:.7f},{y:.7f},0" for x, y, *_ in c)
    w = gpd.GeoSeries([new], crs=32640).to_crs(4326).iloc[0]
    ext = "".join(f"<Data name='{k}'><value>{v}</value></Data>" for k, v in rec.items())
    (D / f"{NAME}.kml").write_text("<?xml version='1.0' encoding='UTF-8'?><kml xmlns='http://www.opengis.net/kml/2.2'><Document><name>RB-3 RAHA BEACH OUTFALL-3</name>"
        "<Style id='s'><LineStyle><color>ff0000ff</color><width>2.5</width></LineStyle><PolyStyle><color>440000ff</color></PolyStyle></Style>"
        f"<Placemark><name>RB-3 RAHA BEACH OUTFALL-3</name><styleUrl>#s</styleUrl><ExtendedData>{ext}</ExtendedData><Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(w.exterior.coords)}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>", encoding="utf-8")
    # 合并更正版
    C = gpd.read_file(D / "catchment_39+RB-3_更正版.shp")
    C = C[C["label"] != "RB-3"]
    import pandas as pd
    C = gpd.GeoDataFrame(pd.concat([C, gpd.GeoDataFrame([{"label": "RB-3", "New_Number": "RB-3", "src": rec["src"], "area_km2": round(new.area / 1e6, 3)}], geometry=[new], crs=32640)], ignore_index=True), crs=32640)
    C.to_crs(4326).to_file(D / "catchment_39+RB-3_更正版.geojson", driver="GeoJSON")
    C.to_file(D / "catchment_39+RB-3_更正版.shp", driver="ESRI Shapefile", encoding="utf-8")
    gj = json.loads((D / f"{NAME}.geojson").read_text(encoding="utf-8")); gj["source"] = rec["src"]
    (ROOT / "console" / "data" / "rb3_catch.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    print(f"v3 定稿：面积 {new.area:,.0f} m²，周长 {new.length:,.0f} m；", chk)
    return new


if __name__ == "__main__":
    import pickle
    P, new, attrs, chk, n, s = main()
    pickle.dump((P, new, attrs, chk, n, s), open(W / "v3_tmp.pkl", "wb"))
    finalize()
