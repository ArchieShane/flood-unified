# -*- coding: utf-8 -*-
"""L2+suggested 二维补点（2026-10-21）：一维贪心选完后，二维里热点附近仍有退不掉的积水（地面低洼，井口比坑底高，管网收不进去），
在这些积水片里加地面取水泵（二维设施，同候选 sump：泵 → 压力管 → 出水口，排出系统）。

取水片 = 方案态（如 OPT20）在定尺寸雨（缺省 S96D1）下的积水点（swbrain.ponding，水深阈值取判据 C 的 0.10 m），
        只取离指定热点 ≤ --radius 的、模拟结束时还有积水（vol_end ≥ --min-vol）的片
泵数   = ceil(max(结束残留, 峰值 / 3) / (0.361 m³/s × 3600 s))：1 h 内抽完残留、3 h 内抽完峰值，取大者（Claude 自定）
压力管 = 到出水口（--outfall-re 匹配的出水口均值位置），直线 × 1.2；管径与造价按 l2opt_hydraulics.size_main（WA-726 7.3.2）
造价   = 泵 30 万 / 台 + 取水井 5 万 + 压力管（占位单价，同候选）
产物：$WORK/<前缀>_<雨>_<新态>.inp（= 方案态的 inp 原样）、c2d/pilot_<前缀>_<雨>_<新态>.json（方案态 + pumps2d）、out/l2opt/<运行>/sumps_<新态>.json
用法：python tools/l2opt_add_sumps.py --prefix RDA --run RDA_96 --state OPT20 --new OPT20S --storms S96D1,S96D24 --hs ADM-75,ADM-76,...
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import shutil
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
import l2opt_hydraulics as HY  # noqa: E402
from l2opt_candidates import COST  # noqa: E402
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
ROUTE_K = 1.2


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="RDA"); ap.add_argument("--run", default="RDA_96")
    ap.add_argument("--state", required=True); ap.add_argument("--new", required=True)
    ap.add_argument("--storms", default="S96D1,S96D24"); ap.add_argument("--size-storm", default="S96D1")
    ap.add_argument("--hs", required=True); ap.add_argument("--radius", type=float, default=200.0)
    ap.add_argument("--depth", type=float, default=0.10); ap.add_argument("--min-vol", type=float, default=30.0)
    ap.add_argument("--outfall-re", default="^31$"); ap.add_argument("--tailwater", type=float, default=1.3)
    ap.add_argument("--min-area", type=float, default=50.0, help="积水片最小面积 m²")
    ap.add_argument("--extra", default="", help="另加取水点：x,y,台数,热点;…（积水片自动识别漏掉的坑，取水范围 = 点外扩 20 m）")
    ap.add_argument("--boost", default="", help="按热点加泵：ADM-76:3,ADM-377:2（该热点附近每处的泵数 × 倍数；判据 A 看峰值水深，峰值时抽得不够快就加）")
    a = ap.parse_args(argv)
    import geopandas as gpd
    import rasterio
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform as stf
    from swbrain import ponding
    T = Transformer.from_crs(4326, 32640, always_xy=True)
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640)
    H = H[H.hotspot_id.isin(a.hs.split(","))]
    hxy = {r.hotspot_id: (r.geometry.x, r.geometry.y) for r in H.itertuples()}
    src = f"{a.prefix}_{a.size_storm}"
    r = ponding.compute(src, a.state, depth=a.depth, min_area=a.min_area, min_dur=0.25, geometry=True)
    boost = {k: float(v) for k, v in (b.split(":") for b in a.boost.split(",") if b)}
    inp = (WORK / f"{src}_{a.state}.inp").read_text(encoding="utf-8")
    co = {q[0]: (float(q[1]), float(q[2])) for q in (ln.split() for ln in re.search(r"\[COORDINATES\]\n(.*?)(?:\n\[|\Z)", inp, re.S).group(1).splitlines()) if len(q) >= 3}
    ofs = [ln.split()[0] for ln in re.search(r"\[OUTFALLS\]\n(.*?)\n\n", inp, re.S).group(1).splitlines() if ln.strip() and re.search(a.outfall_re, ln.split()[0])]
    of_xy = (float(np.mean([co[k][0] for k in ofs])), float(np.mean([co[k][1] for k in ofs])))
    dtm = rasterio.open("D:/阿布扎比/DTM_z40_customer/AUH_DTM_5m_Z40.TIF")
    sumps = []
    for f in r["feats"]:
        x, y = T.transform(f["lon"], f["lat"])
        d = {h: math.hypot(x - hx, y - hy) for h, (hx, hy) in hxy.items()}
        h = min(d, key=d.get)
        if d[h] > a.radius or f["vol_end_m3"] < a.min_vol:
            continue
        n = max(1, math.ceil(max(f["vol_end_m3"], f["vol_peak_m3"] / 3.0) / (HY.Q12 * 3600.0)))
        n = int(math.ceil(n * boost.get(h, 1.0)))
        L = math.hypot(of_xy[0] - x, of_xy[1] - y) * ROUTE_K
        z0 = float(next(dtm.sample([(x, y)]))[0])
        m = HY.size_main(n, max(L, 10.0), z0, a.tailwater)
        g = shape(f["geom"]); g = stf(lambda xx, yy, zz=None: T.transform(xx, yy), g)
        g = max(getattr(g, "geoms", [g]), key=lambda q: q.area)
        sid = f"SS{len(sumps) + 1:02d}"
        sumps.append({"id": sid, "ponding_point": f["id"], "hotspot": h, "hs_dist_m": round(d[h]), "vol_peak_m3": f["vol_peak_m3"],
                      "vol_end_m3": f["vol_end_m3"], "hmax_m": f["hmax_m"], "n_pumps": n, "q_m3s": round(n * HY.Q12, 3), "pipe_m": round(L),
                      "main_d_m": m["main_d_m"], "n_mains": m["n_mains"], "head_m": m["head_m"],
                      "cost_usd": round(n * COST["pump"] + COST["sump"] + m["main_cost_usd"]), "sx": round(x, 1), "sy": round(y, 1),
                      "poly": [list(p) for p in g.exterior.coords]})
    from shapely.geometry import Point
    for e in [q for q in a.extra.split(";") if q]:
        x, y, n, h = e.split(","); x, y, n = float(x), float(y), int(n)
        L = math.hypot(of_xy[0] - x, of_xy[1] - y) * ROUTE_K
        z0 = float(next(dtm.sample([(x, y)]))[0])
        m = HY.size_main(n, max(L, 10.0), z0, a.tailwater)
        sumps.append({"id": f"SS{len(sumps) + 1:02d}", "ponding_point": "手工补点", "hotspot": h, "hs_dist_m": round(math.hypot(x - hxy[h][0], y - hxy[h][1])),
                      "vol_peak_m3": None, "vol_end_m3": None, "hmax_m": None, "n_pumps": n, "q_m3s": round(n * HY.Q12, 3), "pipe_m": round(L),
                      "main_d_m": m["main_d_m"], "n_mains": m["n_mains"], "head_m": m["head_m"],
                      "cost_usd": round(n * COST["pump"] + COST["sump"] + m["main_cost_usd"]), "sx": x, "sy": y,
                      "poly": [list(c) for c in Point(x, y).buffer(20.0).exterior.coords]})
    for s in sumps:
        print(f"  {s['id']} {s['hotspot']}（{s['hs_dist_m']} m）残留 {s['vol_end_m3']} / 峰值 {s['vol_peak_m3']} m³、最深 {s['hmax_m']} m → "
              f"{s['n_pumps']} 台 {s['q_m3s']} m³/s，压力管 {s['pipe_m']} m DN{int(s['main_d_m'] * 1000)}×{s['n_mains']}，{s['cost_usd'] / 1e6:.2f} M USD")
    o = ROOT / "out" / "l2opt" / a.run
    base_rec = json.loads((o / f"variant_{a.size_storm}_{a.state}.json").read_text(encoding="utf-8"))
    for storm in a.storms.split(","):
        name = f"{a.prefix}_{storm}"
        shutil.copyfile(WORK / f"{name}_{a.state}.inp", WORK / f"{name}_{a.new}.inp")
        meta = json.loads((C2D / f"pilot_{name}_{a.state}.json").read_text(encoding="utf-8"))
        meta["state"] = a.new
        p2 = list(meta.get("pumps2d") or [])
        for s in sumps:
            p2.append({"id": s["id"], "q_m3s": s["q_m3s"], "off_m": 0.03, "sump_polygon": s["poly"], "xy": [s["sx"], s["sy"]], "to_xy": None, "pond_cap_frac": 0.9})
        meta["pumps2d"] = p2
        (C2D / f"pilot_{name}_{a.new}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    tot = base_rec["cost_usd"] + sum(s["cost_usd"] for s in sumps)
    (o / f"sumps_{a.new}.json").write_text(json.dumps({"base_state": a.state, "base_cost_usd": base_rec["cost_usd"], "sumps": [{k: v for k, v in s.items() if k != "poly"} for s in sumps],
                                                       "cost_usd": tot, "rule": __doc__.split("产物")[0].strip()}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {a.new}：{a.state} + 地面泵 {len(sumps)} 处 {sum(s['n_pumps'] for s in sumps)} 台，合计占位造价 {tot / 1e6:.2f} M USD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
