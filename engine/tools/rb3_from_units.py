# -*- coding: utf-8 -*-
"""RB-3 v4：单独那份 RB-3 换成 C1 客户单元融合后的 RB-3（用户 2026-09-29「要」）。
v3（对齐道路）另存 v3_对齐道路/；属性沿用 v3，加 version / v3_area；合并更正版与系统图层一起更新。
用法：python tools/rb3_from_units.py（先跑 tools/c1_units_export.py）
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
D = ROOT / "out" / "交付" / "汇水区更正_RB-3"
NAME = "RB-3_RAHA_BEACH_OUTFALL-3"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    import pandas as pd
    U = gpd.read_file(ROOT / "out" / "交付" / "C1汇水区_客户单元" / "C1_客户单元_3个面.shp")
    g = U[U.New_Number == "RB-3"].geometry.iloc[0]
    V3 = gpd.read_file(D / f"{NAME}.shp")
    bk = D / "v3_对齐道路"; bk.mkdir(exist_ok=True)
    for f in D.glob(f"{NAME}.*"):
        shutil.copy2(f, bk / f.name)
    rec = V3.drop(columns="geometry").iloc[0].to_dict()
    v3a = V3.geometry.iloc[0].area
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    rec.update(version="v4 融合（C1 单元间空洞补入）", v3_area=round(v3a), trace_area=round(g.area), trace_leng=round(g.length),
               src=str(rec.get("src", "")) + "；v4 与 MBZ / ADGC-10 融合：切重叠、补单元间空洞与拼缝",
               hs_inside=";".join(h for h in ("ADM-94", "ADM-192", "ADM-487", "ADM-202") if g.contains(H.loc[h].geometry)))
    rec = {k: v for k, v in rec.items() if k in V3.columns or k in ("version", "v3_area")}
    G = gpd.GeoDataFrame([rec], geometry=[g], crs=32640)
    G.to_crs(4326).to_file(D / f"{NAME}.geojson", driver="GeoJSON")
    G.to_file(D / f"{NAME}.shp", driver="ESRI Shapefile", encoding="utf-8")
    ring = lambda c: " ".join(f"{x:.7f},{y:.7f},0" for x, y, *_ in c)
    w = gpd.GeoSeries([g], crs=32640).to_crs(4326).iloc[0]
    ext = "".join(f"<Data name='{k}'><value>{v}</value></Data>" for k, v in rec.items())
    (D / f"{NAME}.kml").write_text("<?xml version='1.0' encoding='UTF-8'?><kml xmlns='http://www.opengis.net/kml/2.2'><Document><name>RB-3 RAHA BEACH OUTFALL-3</name>"
        "<Style id='s'><LineStyle><color>ff0000ff</color><width>2.5</width></LineStyle><PolyStyle><color>440000ff</color></PolyStyle></Style>"
        f"<Placemark><name>RB-3 RAHA BEACH OUTFALL-3</name><styleUrl>#s</styleUrl><ExtendedData>{ext}</ExtendedData><Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(w.exterior.coords)}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>", encoding="utf-8")
    C = gpd.read_file(D / "catchment_39+RB-3_更正版.shp")
    C = C[C["label"] != "RB-3"]
    C = gpd.GeoDataFrame(pd.concat([C, gpd.GeoDataFrame([{"label": "RB-3", "New_Number": "RB-3", "src": rec["src"], "area_km2": round(g.area / 1e6, 3)}], geometry=[g], crs=32640)], ignore_index=True), crs=32640)
    C.to_crs(4326).to_file(D / "catchment_39+RB-3_更正版.geojson", driver="GeoJSON")
    C.to_file(D / "catchment_39+RB-3_更正版.shp", driver="ESRI Shapefile", encoding="utf-8")
    gj = json.loads((D / f"{NAME}.geojson").read_text(encoding="utf-8")); gj["source"] = rec["src"]
    (ROOT / "console" / "data" / "rb3_catch.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    r = D / "说明.md"; t = r.read_text(encoding="utf-8")
    t = t.replace("> **当前版本 v3（2026-09-29）**", f"> **当前版本 v4（2026-09-29）**：和 C1 客户单元（MBZ、ADGC-10）融合，切掉重叠、补上单元之间的空洞与拼缝；"
                  f"面积 {v3a / 1e6:.2f} → **{g.area / 1e6:.2f} km²**，与 `out/交付/C1汇水区_客户单元/` 里的 RB-3 完全一致。v3 留在 `v3_对齐道路/`。\n>\n> v3", 1)
    r.write_text(t, encoding="utf-8")
    print(f"RB-3 v4：{v3a:,.0f} → {g.area:,.0f} m²，周长 {g.length:,.0f} m；面内热点 {rec['hs_inside']}")


if __name__ == "__main__":
    main()
