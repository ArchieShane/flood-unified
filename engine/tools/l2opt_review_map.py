# -*- coding: utf-8 -*-
"""L2+suggested 优化试点 · M1 审阅地图（本地单页 HTML，Leaflet + OSM 底图，数据内嵌）。

图层：土地分级（Makani）、候选井（按溢流量）、重力塘址（每个塘址取 1× 溢流、1.5 m 深那一档的轮廓，按土地级别着色）、
泵送新塘（每口井 1× 那一档）、地面取水点、现有塘余量、v5 新塘（对照）、热点。点井看它的全部候选与占位造价。
用法：python tools/l2opt_review_map.py --run RB01_H61     产物：out/l2opt/<运行>/review_map.html
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))

LAND_RGBA = {0: (120, 120, 120, 90), 1: (26, 152, 80, 150), 2: (254, 224, 139, 150), 3: (253, 174, 97, 150), 4: (158, 154, 200, 140), 9: (220, 220, 220, 60)}


def land_png(run):
    import rasterio
    from PIL import Image
    from pyproj import Transformer
    o = ROOT / "out" / "l2opt" / run
    with rasterio.open(o / "land_cls.tif") as d:
        cls = d.read(1); b = d.bounds
    rgba = np.zeros(cls.shape + (4,), np.uint8)
    for k, c in LAND_RGBA.items():
        rgba[cls == k] = c
    im = Image.fromarray(rgba, "RGBA")
    im = im.resize((im.width // 2, im.height // 2), Image.NEAREST)
    buf = io.BytesIO(); im.save(buf, "PNG", optimize=True)
    T = Transformer.from_crs(32640, 4326, always_xy=True)
    lo0, la0 = T.transform(b.left, b.bottom); lo1, la1 = T.transform(b.right, b.top)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), [[la0, lo0], [la1, lo1]]


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--run", default="RB01_H61"); ap.add_argument("--hs", default="ADM-101,ADM-140,ADM-205,ADM-206,ADM-291,ADM-293")
    a = ap.parse_args(argv)
    import geopandas as gpd
    from pyproj import Transformer
    from shapely.geometry import mapping, shape
    from shapely.ops import transform as stf
    o = ROOT / "out" / "l2opt" / a.run
    D = json.loads((o / "candidates.json").read_text(encoding="utf-8"))
    G = json.loads((o / "candidates.geojson").read_text(encoding="utf-8"))
    geom_of = {f["properties"]["id"]: f["geometry"] for f in G["features"]}
    T = Transformer.from_crs(32640, 4326, always_xy=True)
    W = lambda g: json.loads(json.dumps(mapping(stf(lambda x, y, z=None: T.transform(x, y), g))), parse_float=lambda s: round(float(s), 6))  # noqa: E731
    C = D["candidates"]
    wells = {}
    for c in C:
        if c.get("well"):
            w = wells.setdefault(c["well"], {"well": c["well"], "hotspot": c["hotspot"], "hs_dist_m": c["hs_dist_m"], "overflow_m3": c["overflow_m3"],
                                             "rate": c["overflow_rate_m3s"], "rim": c["rim_m"], "lon": None, "lat": None, "opts": []})
            w["opts"].append({k: c.get(k) for k in ("id", "kind", "n_pumps", "dest", "pond_vol_m3", "pond_depth_m", "pipe_m", "pipe_d_m", "site", "cost_usd", "land")})
    from build_c1_l2 import read_inp
    from workdir import WORK
    S = read_inp(WORK / f"{a.run}_{D['base']}.inp")
    for r in S["[COORDINATES]"]:
        if r[0] in wells:
            wells[r[0]]["lon"], wells[r[0]]["lat"] = [round(v, 6) for v in T.transform(float(r[1]), float(r[2]))]
    rej = D.get("rejected", {})
    for k, w in wells.items():
        w["reject"] = rej.get(k, [])
    sites = [c for c in C if c["kind"] == "gravity" and c["pond_depth_m"] == 1.5 and abs(c["pond_vol_m3"] - max(400, c["overflow_m3"])) / max(400, c["overflow_m3"]) < 0.2]
    gsites = [{"id": c["id"], "well": c["well"], "site": c["site"], "vol": c["pond_vol_m3"], "land": c["land"], "parcels": c.get("parcels"), "cost": c["cost_usd"],
               "pipe": c["pipe_m"], "d": c["pipe_d_m"], "g": geom_of[c["id"]], "link": c.get("link_wgs")} for c in sites]
    pnew = [c for c in C if c["kind"] == "pump_newpond" and c["pond_vol_m3"] <= max(400, c["overflow_m3"]) * 1.2 + 1]
    seen, pn = set(), []
    for c in pnew:
        if c["well"] in seen:
            continue
        seen.add(c["well"]); pn.append({"id": c["id"], "well": c["well"], "vol": c["pond_vol_m3"], "land": c["land"], "parcels": c.get("parcels"), "g": geom_of[c["id"]], "link": c.get("link_wgs")})
    sumps = [{"id": c["id"], "pt": c["ponding_point"], "vol": c["ponding_vol_m3"], "h": c["ponding_hmax_m"], "n": c["n_pumps"], "clear": c["clear_h"], "dest": c["dest"],
              "cost": c["cost_usd"], "lon": c["lon"], "lat": c["lat"]} for c in C if c["kind"] == "sump"]
    deep = [{"id": c["id"], "pond": c["pond"], "add": c["add_depth_m"], "vol": c["add_vol_m3"], "cost": c["cost_usd"], "g": geom_of[c["id"]]} for c in C if c["kind"] == "deepen"]
    exist = [{"node": p["node"], "spare": p["spare_m3"], "pct": p["max_pct"], "lon": round(T.transform(*p["xy"])[0], 6), "lat": round(T.transform(*p["xy"])[1], 6),
              "nosurf": p["no_surface"]} for p in D["ponds_existing"]]
    v5 = json.loads((ROOT / "out" / f"rb01_L2S5_inputs_{a.run.split('_')[1]}.json").read_text(encoding="utf-8"))
    v5p = [{"id": p["id"], "vol": round(p["vol"]), "g": W(shape(p["footprint"]))} for p in v5["ponds"]]
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson")
    H = H[H.hotspot_id.isin(a.hs.split(","))]
    hs = [{"id": r.hotspot_id, "lon": r.geometry.x, "lat": r.geometry.y, "crit": r.criticality} for r in H.itertuples()]
    img, bnd = land_png(a.run)
    summ = json.loads((o / "summary.json").read_text(encoding="utf-8"))
    stats = json.loads((o / "land_stats.json").read_text(encoding="utf-8"))
    data = {"run": a.run, "img": img, "bnd": bnd, "wells": list(wells.values()), "gsites": gsites, "pnew": pn, "sumps": sumps, "deep": deep, "exist": exist,
            "v5": v5p, "hs": hs, "summ": summ, "land": stats["area_ha"], "cost": D["cost_placeholder_usd"]}
    tpl = (HERE / "l2opt_review_template.html").read_text(encoding="utf-8")
    html = tpl.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    (o / "review_map.html").write_text(html, encoding="utf-8")
    print(f"→ {o / 'review_map.html'}  {len(html) / 1e6:.1f} MB  井 {len(wells)}  重力塘址 {len(gsites)}  泵送新塘 {len(pn)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
