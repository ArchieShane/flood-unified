"""只读：RD-A 现状（L1）二维模拟积水点 vs 客户热点（11 个）的空间重合程度（2026-10-26）。
用法：python -X utf8 tools/rda_hotspot_overlap.py [out.json]（需设 SWBRAIN_SWMM_WORK）；结果写进 docs/RD-A_模拟积水点与客户热点重合分析_20261026.md"""
import json, sys, pathlib
sys.path.insert(0, "."); sys.stdout.reconfigure(encoding="utf-8")
from swbrain import ponding
from shapely.geometry import shape, Point
from shapely.ops import transform, unary_union
from pyproj import Transformer
T = Transformer.from_crs(4326, 32640, always_xy=True).transform
IDS = "ADM-75,ADM-76,ADM-80,ADM-161,ADM-376,ADM-377,ADM-378,ADM-379,ADM-380,ADM-382,ADM-383".split(",")
H = {f["properties"]["hotspot_id"]: Point(T(*f["geometry"]["coordinates"][:2])) for f in json.load(open("console/data/hotspots.geojson", encoding="utf-8"))["features"] if f["properties"]["hotspot_id"] in IDS}
EX = unary_union([shape(f["geometry"]) for f in json.load(open("out/l2opt/RDA_96C/exclude_OPTCS.geojson", encoding="utf-8"))["features"]])
STORMS = ["S25D1", "S31D2", "S33D1", "S33D2", "S33D24", "A61D1", "S61D1", "S61D2", "S61D24", "S96D1", "S96D2", "S96D24"]
out = {}
for st in STORMS:
    for dep in (0.15, 0.30):
        r = ponding.compute(f"RDA_{st}", "L1", dep, pond_scope="state")
        P = [(f, transform(T, shape(f["geom"]))) for f in r["feats"]]
        P = [(f, g) for f, g in P if not g.intersection(EX).area > 0.5 * g.area]      # 整片在评估剔除区的不算
        row = {}
        for h, pt in H.items():
            d = [(g.distance(pt), f) for f, g in P]
            dm, f = min(d, key=lambda x: x[0]) if d else (None, None)
            row[h] = {"d": round(dm) if dm is not None else None, "id": f["id"] if f else None, "h": f["hmax_m"] if f else None, "V": f["vol_peak_m3"] if f else None}
        # 反方向：积水点（按量）有多少在某个热点 200 m 内
        near = [f for f, g in P if min(g.distance(pt) for pt in H.values()) <= 200]
        out[(st, dep)] = {"hs": row, "n": len(P), "n_near": len(near), "V": sum(f["vol_peak_m3"] for f, _ in P), "V_near": sum(f["vol_peak_m3"] for f in near)}
        hit = lambda R: sum(1 for v in row.values() if v["d"] is not None and v["d"] <= R)
        print(f"{st:<7}{dep:.2f}  热点命中 ≤0m {hit(0):>2} ≤50m {hit(50):>2} ≤100m {hit(100):>2} ≤200m {hit(200):>2} /11 | 积水点 {len(P):>3}，在热点 200m 内 {len(near):>3}；积水量 {out[(st,dep)]['V']:>8,} m³，其中热点 200m 内 {out[(st,dep)]['V_near']*100/max(out[(st,dep)]['V'],1):.0f}%")
print()
for h in IDS:
    print(h, " ".join(f"{st}:{out[(st,0.15)]['hs'][h]['d']}m/{out[(st,0.15)]['hs'][h]['h']}" for st in ["S25D1", "S31D2", "A61D1", "S61D1", "S61D24", "S96D1"]))
json.dump({f"{k[0]}_{k[1]}": v for k, v in out.items()}, open(sys.argv[1] if len(sys.argv) > 1 else "out/rda_hotspot_overlap.json", "w", encoding="utf-8"), ensure_ascii=False)
