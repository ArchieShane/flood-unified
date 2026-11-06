# -*- coding: utf-8 -*-
"""C1 汇水区的面导出（用户 2026-09-29：C1 原本不是汇水区的定义，现在可以当汇水区用，交付目录里给面的导出文件）。

C1 汇水区 = C1 计算域：8 个热点（C1 的 ADM-21 / 90 / 94 / 104 / 192 / 202 / 486 + ADM-487）的地表（5 m DTM 填洼 D8）+ 管网汇水区并集，外扩 300 m。
三种格式，同一个面：
  C1汇水区.geojson   WGS84（EPSG:4326）
  C1汇水区.shp 等    UTM 40N（EPSG:32640），属性 UTF-8（带 .cpg）
  C1汇水区.kml       Google Earth
属性：id、名称、面积 km² / ha、周长 km、所含热点、定义、来源、日期。
用法：python tools/c1_catchment_export.py <输出目录>（缺省写到 out/交付/C1_*/公共/C1汇水区/）
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HS = ["ADM-21", "ADM-90", "ADM-94", "ADM-104", "ADM-192", "ADM-202", "ADM-486", "ADM-487"]
DEF = "8 个热点（C1 的 ADM-21/90/94/104/192/202/486 + ADM-487）的地表（5 m DTM 填洼 D8）+ 管网汇水区并集外扩 300 m；即 C1 一二维计算域。ADM-90 的地表汇水区伸出窗口、未收敛"


def export(out_dir: pathlib.Path):
    import geopandas as gpd
    out_dir.mkdir(parents=True, exist_ok=True)
    g = gpd.read_file(ROOT / "out" / "c1_domain.geojson")
    g = g.set_crs(4326, allow_override=True) if g.crs is None else g.to_crs(4326)
    u = g.to_crs(32640)
    geom_u = u.geometry.union_all() if hasattr(u.geometry, "union_all") else u.unary_union
    props = {"id": "C1", "name": "C1 热点群汇水区", "area_km2": round(geom_u.area / 1e6, 3), "area_ha": round(geom_u.area / 1e4, 1),
             "perim_km": round(geom_u.length / 1000, 2), "hotspots": " / ".join(HS), "n_hotspot": len(HS),
             "definition": DEF, "source": "排水智能体 C1 一二维模型（tools/c1_existing_pumps.py domain()）", "date": "2026-09-29"}
    gu = gpd.GeoDataFrame([props], geometry=[geom_u], crs=32640)
    gw = gu.to_crs(4326)
    gw.to_file(out_dir / "C1汇水区.geojson", driver="GeoJSON")
    # Shapefile 字段名最长 10 个字符
    gs = gu.rename(columns={"definition": "defin", "hotspots": "hotspots", "n_hotspot": "n_hotspot"})
    gs.to_file(out_dir / "C1汇水区.shp", driver="ESRI Shapefile", encoding="utf-8")
    (out_dir / "C1汇水区.cpg").write_text("UTF-8", encoding="ascii")
    # KML：手写，免得依赖 libkml 驱动
    rings = [geom_u] if geom_u.geom_type == "Polygon" else list(geom_u.geoms)
    rings_w = gpd.GeoSeries(rings, crs=32640).to_crs(4326)
    def ring(c):
        return " ".join(f"{x:.7f},{y:.7f},0" for x, y, *_ in c)
    pm = "".join("<Polygon><outerBoundaryIs><LinearRing><coordinates>" + ring(p.exterior.coords) + "</coordinates></LinearRing></outerBoundaryIs>"
                 + "".join("<innerBoundaryIs><LinearRing><coordinates>" + ring(h.coords) + "</coordinates></LinearRing></innerBoundaryIs>" for h in p.interiors)
                 + "</Polygon>" for p in rings_w)
    desc = "".join(f"<Data name='{k}'><value>{v}</value></Data>" for k, v in props.items())
    kml = ("<?xml version='1.0' encoding='UTF-8'?><kml xmlns='http://www.opengis.net/kml/2.2'><Document><name>C1 热点群汇水区</name>"
           "<Style id='s'><LineStyle><color>ff2f1e0f</color><width>2.5</width></LineStyle><PolyStyle><color>332f6bff</color></PolyStyle></Style>"
           f"<Placemark><name>C1 热点群汇水区</name><styleUrl>#s</styleUrl><ExtendedData>{desc}</ExtendedData>"
           f"<MultiGeometry>{pm}</MultiGeometry></Placemark></Document></kml>")
    (out_dir / "C1汇水区.kml").write_text(kml, encoding="utf-8")
    (out_dir / "说明.md").write_text(
        "# C1 热点群汇水区\n\n"
        f"- **定义**：{DEF}。C1 原本不是汇水区的定义；本次按用户确认，把 C1 一二维计算域当作 C1 的汇水区使用。\n"
        f"- **面积**：{props['area_km2']} km²（{props['area_ha']:,} ha），周长 {props['perim_km']} km，含 {len(HS)} 个热点：{props['hotspots']}。\n"
        "- **文件**：`C1汇水区.geojson`（WGS84，EPSG:4326）；`C1汇水区.shp/.shx/.dbf/.prj/.cpg`（UTM 40N，EPSG:32640，属性 UTF-8；字段 `defin` = 定义）；`C1汇水区.kml`（Google Earth）。三份是同一个面。\n"
        "- **注意**：ADM-90 的地表汇水区伸进内陆、碰到分析窗口边未收敛，窗口外的来水不在这个面里；面里有大片内陆空地。"
        "8 个热点各自的地表 / 管网汇水区见上一级的 `8个热点的地表与管网汇水区.geojson`。\n", encoding="utf-8")
    return props


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    outs = [pathlib.Path(sys.argv[1])] if len(sys.argv) > 1 else [ROOT / "out" / "交付" / d / "公共" / "C1汇水区" for d in ("C1_61mm2h", "C1_96mm2h")]
    for o in outs:
        p = export(o)
        print(f"→ {o}：{p['area_km2']} km²，{sorted(x.name for x in o.iterdir())}")


if __name__ == "__main__":
    main()
