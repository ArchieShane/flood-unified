# -*- coding: utf-8 -*-
"""以客户 22 个水力模型为准的汇水区单元（用户 2026-09-30：flood 库与截图合成都是手描的、不准，「以刚才给你的目录里的数据为准」）。

数据：D:/世界模型/OneDrive_1_9-26-2026.zip（客户 9 月 26 日发来的 22 个模型，shapefile）。
  1. 模型汇水区：每个模型的子汇水区（带 A_UNIFIED / SCS_CN 的面层）并集；闭合 CLOSE_M 米补子汇水区之间的细缝，
     补掉小于 HOLE_HA 的空洞（大于的保留——可能是真的不排进这个模型的地块）
  2. 模型之间的重叠：重叠部分归「在重叠区里子汇水区更多（更细）」的那个模型；重叠面积另记
  3. 出水口分区：只在管网能干净分组的模型里做（B_C：O-1 / O-2，见 tools/units_from_models.py）；RB3 的水经塘与泵站、每个子汇水区都追得到几个出水口，不细分
  4. C1 模型版 = RB3 + Musaffah_RD09 + B_C（重叠处理后）并集，闭合、补空洞
产物：out/units_from_models/model_units.geojson（22 个模型汇水区 + B_C 出水口分区）、c1_model_domain.geojson；
      out/交付/C1汇水区_模型版/（GeoJSON / Shapefile / KML + 说明）
用法：python tools/model_units_build.py
"""
from __future__ import annotations

import itertools
import json
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); import datapaths  # noqa: E402  客户数据位置：config.toml [data]
ZIP = datapaths.get("client_models_zip").as_posix()
W = ROOT / "out" / "units_from_models"
D = ROOT / "out" / "交付" / "C1汇水区_模型版"
CLOSE_M = 20.0
HOLE_HA = 2.0
C1_MODELS = ["RB3", "Musaffah_RD09", "B_C"]
HS8 = ["ADM-21", "ADM-90", "ADM-94", "ADM-104", "ADM-192", "ADM-202", "ADM-486", "ADM-487"]
SPLIT = {"B_C": {"O-1": "B_C·O-1", "O-2": "B_C·O-2"}}


def fill_small_holes(g, min_ha):
    from shapely.geometry import MultiPolygon, Polygon
    def one(p):
        return Polygon(p.exterior, [r for r in p.interiors if Polygon(r).area >= min_ha * 1e4])
    if g.geom_type == "Polygon":
        return one(g)
    return MultiPolygon([one(p) for p in g.geoms])


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    import pyogrio
    from shapely.ops import unary_union
    V = "/vsizip/" + ZIP + "/"
    names = zipfile.ZipFile(ZIP).namelist()
    subs = {}
    for s in names:
        if not s.lower().endswith(".shp"):
            continue
        try:
            i = pyogrio.read_info(V + s)
        except Exception:
            continue
        if not i["geometry_type"] or "Polygon" not in i["geometry_type"] or not any(f in i["fields"] for f in ("A_UNIFIED", "SCS_CN")):
            continue
        m = s.split("/")[0]
        g = gpd.read_file(V + s); g = g.set_crs(32640, allow_override=True) if g.crs is None else g.to_crs(32640)
        g["geometry"] = g.geometry.buffer(0)
        subs[m] = gpd.GeoDataFrame(__import__("pandas").concat([subs[m], g[["geometry"]]], ignore_index=True), crs=32640) if m in subs else g[["geometry"]].copy()
    print(f"模型 {len(subs)} 个，子汇水区合计 {sum(len(v) for v in subs.values()):,}")
    raw = {m: fill_small_holes(unary_union(list(g.geometry)).buffer(CLOSE_M).buffer(-CLOSE_M), HOLE_HA) for m, g in subs.items()}
    # 模型之间的重叠：归重叠区里子汇水区（代表点）更多的模型
    units = dict(raw); overlaps = []
    for a, b in itertools.combinations(sorted(raw), 2):
        if not units[a].intersects(units[b]):
            continue
        ov = units[a].intersection(units[b])
        if ov.area < 1e3:
            continue
        na = int(subs[a].geometry.representative_point().within(ov).sum()); nb = int(subs[b].geometry.representative_point().within(ov).sum())
        win, lose = (a, b) if na >= nb else (b, a)
        units[lose] = units[lose].difference(ov)
        overlaps.append({"a": a, "b": b, "overlap_ha": round(ov.area / 1e4, 1), "n_sub_a": na, "n_sub_b": nb, "given_to": win})
    for o in sorted(overlaps, key=lambda x: -x["overlap_ha"]):
        print(f"  重叠 {o['a']} × {o['b']}：{o['overlap_ha']} ha（子汇水区 {o['n_sub_a']} : {o['n_sub_b']}）→ 归 {o['given_to']}")
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    rows = []
    for m, g in sorted(units.items()):
        g = g.buffer(0)
        hs = [h for h in H.index if g.contains(H.loc[h].geometry)]
        rows.append({"unit_id": f"MODEL:{m}", "model": m, "level": "model", "name": m, "n_sub": int(len(subs[m])), "area_ha": round(g.area / 1e4, 1),
                     "n_parts": len(getattr(g, "geoms", [g])), "hotspots": ";".join(hs), "n_hotspots": len(hs), "geometry": g})
    # 出水口分区
    for m, mp in SPLIT.items():
        G = gpd.read_file(W / f"{m}_by_outfall.geojson").to_crs(32640)
        for r in G.itertuples():
            if r.outfall in mp:
                g = fill_small_holes(r.geometry.buffer(CLOSE_M).buffer(-CLOSE_M), HOLE_HA).intersection(units[m]).buffer(0)
                hs = [h for h in H.index if g.contains(H.loc[h].geometry)]
                rows.append({"unit_id": f"MODEL:{m}:{r.outfall}", "model": m, "level": "outfall", "name": mp[r.outfall], "n_sub": int(r.n_sub), "area_ha": round(g.area / 1e4, 1),
                             "n_parts": len(getattr(g, "geoms", [g])), "hotspots": ";".join(hs), "n_hotspots": len(hs), "geometry": g})
    U = gpd.GeoDataFrame(rows, geometry="geometry", crs=32640)
    W.mkdir(parents=True, exist_ok=True)
    U.to_crs(4326).to_file(W / "model_units.geojson", driver="GeoJSON")
    c1 = unary_union([units[m] for m in C1_MODELS]).buffer(CLOSE_M).buffer(-CLOSE_M)
    parts = sorted(getattr(c1, "geoms", [c1]), key=lambda p: -p.area)
    from shapely.geometry import Polygon
    main_part = Polygon(parts[0].exterior)                              # C1 模型版：取最大的一块、外轮廓（无空洞）
    lost = sum(p.area for p in parts[1:])
    hs_in = [h for h in HS8 if main_part.contains(H.loc[h].geometry)]
    C = gpd.GeoDataFrame([{"id": "C1M", "name": "C1 汇水区（模型版：RB3 + Musaffah_RD09 + B_C）", "models": " / ".join(C1_MODELS), "area_km2": round(main_part.area / 1e6, 2),
                           "hs_inside": ";".join(hs_in), "src": "客户 22 个水力模型的子汇水区（OneDrive_1_9-26-2026.zip）并集，闭合 20 m、去空洞；模型之间重叠归子汇水区更多的一方"}],
                         geometry=[main_part], crs=32640)
    C.to_crs(4326).to_file(W / "c1_model_domain.geojson", driver="GeoJSON")
    print(f"C1 模型版：{main_part.area / 1e6:.2f} km²（另有 {len(parts) - 1} 块零碎共 {lost / 1e4:.1f} ha 舍去），8 个热点在内 {len(hs_in)} 个：{hs_in}")
    # 交付
    D.mkdir(parents=True, exist_ok=True)
    for gdf, nm in ((U[U.model.isin(C1_MODELS)], "C1_模型汇水区单元"), (C, "C1_汇水区_模型版"), (U, "全部22个模型汇水区")):
        gdf.to_crs(4326).to_file(D / f"{nm}.geojson", driver="GeoJSON")
        gdf.to_file(D / f"{nm}.shp", driver="ESRI Shapefile", encoding="utf-8")
        (D / f"{nm}.cpg").write_text("UTF-8", encoding="ascii")
    ring = lambda c: " ".join(f"{x:.7f},{y:.7f},0" for x, y, *_ in c)
    Cw = C.to_crs(4326).geometry.iloc[0]; Uw = U[U.model.isin(C1_MODELS)].to_crs(4326)
    def polys(g):
        return "".join("<Polygon><outerBoundaryIs><LinearRing><coordinates>" + ring(p.exterior.coords) + "</coordinates></LinearRing></outerBoundaryIs>"
                       + "".join("<innerBoundaryIs><LinearRing><coordinates>" + ring(h.coords) + "</coordinates></LinearRing></innerBoundaryIs>" for h in p.interiors) + "</Polygon>"
                       for p in getattr(g, "geoms", [g]))
    pm = "".join(f"<Placemark><name>{r.name}（{r.level}）</name><MultiGeometry>{polys(r.geometry)}</MultiGeometry></Placemark>" for r in Uw.itertuples())
    pm += f"<Placemark><name>C1 汇水区（模型版）</name><Style><LineStyle><color>ff000000</color><width>3</width></LineStyle><PolyStyle><fill>0</fill></PolyStyle></Style><MultiGeometry>{polys(Cw)}</MultiGeometry></Placemark>"
    (D / "C1_汇水区_模型版.kml").write_text(f"<?xml version='1.0' encoding='UTF-8'?><kml xmlns='http://www.opengis.net/kml/2.2'><Document><name>C1 汇水区（模型版）</name>{pm}</Document></kml>", encoding="utf-8")
    summ = {"close_m": CLOSE_M, "hole_ha": HOLE_HA, "n_models": len(units), "overlaps": overlaps, "c1_km2": round(main_part.area / 1e6, 2), "c1_hotspots": hs_in,
            "c1_dropped_parts_ha": round(lost / 1e4, 1), "units": [{k: v for k, v in r.items() if k != "geometry"} for r in rows]}
    (W / "model_units_summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in rows:
        if r["model"] in C1_MODELS:
            print(f"  {r['name']:<18} {r['level']:<8} 子汇水区 {r['n_sub']:>5} · {r['area_ha']:>7} ha · {r['n_parts']} 块 · 热点 {r['hotspots'] or '—'}")
    return summ


if __name__ == "__main__":
    main()
