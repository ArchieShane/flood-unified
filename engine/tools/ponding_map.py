# -*- coding: utf-8 -*-
"""积水点清单 → 单页地图（Leaflet，数据内嵌；底图用 OSM 道路线，Artifact 里不能加载瓦片）。

输入：tools/ponding_points.py 的 out/ponding/{name}_d{depth}.geojson / _pts.geojson（可多个阈值）、
      c2d/pilot_{name}_L1.json（计算域）、out/osm_{region}/highway.json、console/data/hotspots.geojson
用法：python tools/ponding_map.py --name RB01_H60 --osm osm_rb01 --depths 0.15,0.3 --hs ADM-101,...
产物：out/ponding/{name}_map.html
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

MAJOR = {"motorway", "trunk", "primary", "secondary", "tertiary", "residential", "unclassified",
         "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link", "living_street"}
RANK = {"motorway": 1, "trunk": 1, "primary": 1, "motorway_link": 2, "trunk_link": 2, "primary_link": 2,
        "secondary": 2, "secondary_link": 2, "tertiary": 3, "tertiary_link": 3}


def rnd(geom, nd=5):
    import shapely
    return shapely.set_precision(geom, 10 ** -nd)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="RB01_H60")
    ap.add_argument("--osm", default="osm_rb01")
    ap.add_argument("--depths", default="0.15,0.3")
    ap.add_argument("--hs", default="ADM-101,ADM-140,ADM-205,ADM-206,ADM-291,ADM-293")
    ap.add_argument("--title", default="RB-01 模拟积水点")
    ap.add_argument("--scen", default="60 mm / 1 h，模拟到雨后 4 h")
    a = ap.parse_args(argv)
    import geopandas as gpd
    from shapely.geometry import LineString, Polygon, mapping

    pilot = json.loads((WORK / "c2d" / f"pilot_{a.name}_L1.json").read_text(encoding="utf-8"))
    dom = gpd.GeoSeries([Polygon(pilot["domain_polygon"])], crs=32640).to_crs(4326)
    dpoly = dom.iloc[0]; bb = dpoly.buffer(0.004)

    roads = []
    osm = json.loads((ROOT / "out" / a.osm / "highway.json").read_text(encoding="utf-8"))
    for e in osm["elements"]:
        hw = e.get("tags", {}).get("highway")
        if e["type"] != "way" or hw not in MAJOR or len(e.get("geometry") or []) < 2:
            continue
        ln = LineString([(p["lon"], p["lat"]) for p in e["geometry"]])
        if not ln.intersects(bb):
            continue
        ln = ln.intersection(bb).simplify(0.00003)
        for part in getattr(ln, "geoms", [ln]):
            if part.length > 0:
                roads.append([RANK.get(hw, 4), [[round(x, 5), round(y, 5)] for x, y in part.coords]])

    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(4326)
    H = H[H.within(bb)]
    group = set(a.hs.split(",")) if a.hs else set()
    hs = [{"id": r.hotspot_id, "g": r.hotspot_id in group, "x": round(r.geometry.x, 6), "y": round(r.geometry.y, 6),
           "crit": r.criticality, "loc": r.location, "cause": r.root_cause_raw} for r in H.itertuples()]

    sys.path.insert(0, str(ROOT))
    from swbrain import ponding
    layers = {}
    for d in a.depths.split(","):
        feats = []
        for s in ponding.available(a.name).get(a.name, []):
            r = ponding.compute(a.name, s, float(d), pond_scope="state")
            par = {"depth_m": r["params"]["depth"], "min_area_m2": r["params"]["min_area"], "min_dur_h": r["params"]["min_dur"]}
            for f in r["feats"]:
                feats.append({"id": f["id"], "s": s, "g": f["geom"], "px": f["lon"], "py": f["lat"], "h": f["hmax_m"], "A": f["area_m2"],
                              "A30": f["area30_m2"], "dur": f["dur_h"], "V": f["vol_peak_m3"], "tV": f["t_peak_h"],
                              "rec": "未退" if f["recede_h"] is None else f["recede_h"], "Vend": f["vol_end_m3"], "z": f["z_m"],
                              "near": ";".join(h + ("*" if h in group else "") for h, _ in f["near"]),
                              "nh": f["nearest"][0] if f["nearest"] else "", "nd": f["nearest"][1] if f["nearest"] else 0})
        layers[f"{float(d):g}"] = {"params": par, "feats": feats}

    data = {"title": a.title, "scen": a.scen, "domain": mapping(rnd(dpoly)), "roads": roads, "hs": hs, "layers": layers,
            "states": ["L1", "L2", "L2P", "L2S4", "L2S5"],
            "stateNames": {"L1": "L1 现状", "L2": "L2 移动泵", "L2P": "L2+planned", "L2S4": "L2+suggested v4", "L2S5": "L2+suggested v5"}}
    tpl = (HERE / "ponding_map_template.html").read_text(encoding="utf-8")
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = tpl.replace("/*__DATA__*/null", js).replace("__TITLE__", a.title)
    out = ROOT / "out" / "ponding" / f"{a.name}_map.html"
    out.write_text(html, encoding="utf-8")
    print(f"→ {out}  {len(html) / 1e6:.2f} MB  道路 {len(roads)}  热点 {len(hs)}（本群 {sum(h['g'] for h in hs)}）  "
          + "  ".join(f"d{k}: {len(v['feats'])}" for k, v in layers.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
