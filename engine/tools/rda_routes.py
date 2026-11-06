"""RD-A 三道防线调度单 → 卡车路线 → console/data/flood2d_rda_<雨>/routes.json。
来源按 config [routing]（swbrain/routing.py）：2026-10-28 起本地 NAServer 优先，求不出 / 连不上用 ArcGIS Online（swbrain/online_route.py）；每条标 source。

2026-10-26 用户：L3 设施落地（车开哪条路）用在线路径分析服务，直接做进系统。
每个态、每张派单（按「驻地 → 积水点」去重）出一条卡车路线：路线几何、逐段导航（中文）、在线到达时间；同一组积水障碍下再算一遍本地路网时间作对照。
积水障碍：这个态全程最大水深包络里 ≥0.15 m 的二维积水片（swbrain/ponding.py，pond_scope=state）；≥ 重型车不可通行水深（config [dispatch.impassable_depth_m] heavy）禁行，
其余按 [dispatch] slow_factor 放大通行时间；目的地那片不设障碍。最大包络偏保守（车出发时有些片还没形成或已退）：按它进出道路被深水截断时，深水也改成只减速、出参考路线并标 blocked。
不改调度单本身（l3.json 不动）；页面「三道防线联动」地图有 routes.json 时画实际路线，没有时仍画直线。
用法：python -X utf8 tools/rda_routes.py [--storms S96D1,S96D24] [--states L1,L2,L2P,OPTCS2] [--threads 4]（需设 SWBRAIN_SWMM_WORK；在线备选要 ARCGIS_API_KEY）
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from shapely.geometry import Point, shape  # noqa: E402
from swbrain import local_route as LR, online_route as OR, ponding, routing as RT, travel  # noqa: E402

DATA = ROOT / "console" / "data"


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--storms", default=""); ap.add_argument("--states", default=""); ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args(argv)
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))["dispatch"]
    imp_m, slow = float(cfg["impassable_depth_m"]["heavy"]), float(cfg["slow_factor"])
    storms = [s for s in a.storms.split(",") if s] or sorted(p.parent.name.split("_")[-1].upper() for p in DATA.glob("flood2d_rda_*/l3.json"))
    road = travel.RoadNetwork(pd.read_parquet(ROOT / "out/state/road_edges.parquet"), pd.read_parquet(ROOT / "out/state/road_vertices.parquet"))
    for storm in storms:
        f = DATA / f"flood2d_rda_{storm.lower()}" / "l3.json"
        if not f.exists():
            print(f"{storm}：没有 l3.json，跳过"); continue
        L3 = json.loads(f.read_text(encoding="utf-8"))
        out = {"storm": storm, "generated": time.strftime("%Y-%m-%d %H:%M"), "mode": "Trucking Time", "service": "ArcGIS Online Route_World", "providers": LR.cfg()["providers"],
               "local": {"url": LR.cfg()["local_url"].split("/rest/services/")[-1], "mode": LR.cfg()["local_mode"], "time_factor": LR.cfg()["local_time_factor"]},
               "impassable_m": imp_m, "slow_factor": slow, "states": {}}
        for st, S in L3["states"].items():
            if a.states and st not in a.states.split(","):
                continue
            pairs = {}
            for pool, P in (S.get("dispatch") or {}).items():
                for ln in P.get("lines", []):
                    pairs.setdefault(f"{ln['a'][0]},{ln['a'][1]}|{ln['b'][0]},{ln['b'][1]}", (ln["a"], ln["b"], ln["from"], ln["fp"]))
            if not pairs:
                continue
            r = ponding.compute(f"RDA_{storm}", st, 0.15, pond_scope="state")
            polys = [shape(x["geom"]) for x in r["feats"]]; depths = [x["hmax_m"] for x in r["feats"]]
            bars_all = OR.barriers(polys, depths, imp_m, slow)
            bars_soft = OR.barriers(polys, depths, 99.0, slow)

            skip = {k: {i for i, g in enumerate(polys) if g.contains(Point(*v[1])) or g.distance(Point(*v[1])) < 0.0003} for k, v in pairs.items()}   # 目的地那片

            def one(item):                                                     # 在线：并发
                k, (pa, pb, dep, fp) = item
                on = RT.solve(pa, pb, [b for i, b in enumerate(bars_all) if i not in skip[k]], [b for i, b in enumerate(bars_soft) if i not in skip[k]],
                              names=(dep, fp), kind="route")                        # 被深水截断时深水也只减速，出参考路线并标 blocked（routing.solve 里）
                return k, {**(on or {"error": "no result"}), "from": dep, "fp": fp}

            with ThreadPoolExecutor(a.threads) as ex:
                R = dict(ex.map(one, pairs.items()))
            ep = road.edge_patch(polys); dep_e = np.where(ep >= 0, np.asarray(depths, float)[np.clip(ep, 0, None)], 0.0)   # 本地：同一组障碍，每条边的水深一次算好
            for k, (pa, pb, dep, fp) in pairs.items():
                d = dep_e.copy()
                if skip[k]:
                    d[np.isin(ep, list(skip[k]))] = 0.0
                loc = float(road.travel_minutes_depth(np.array([pa]), np.array([pb]), d, impassable_depth_m=imp_m, slow_factor=slow)[0, 0])
                if not np.isfinite(loc) and R[k].get("blocked"):                  # 本地同样口径：深水只减速
                    loc = float(road.travel_minutes_depth(np.array([pa]), np.array([pb]), d, impassable_depth_m=99.0, slow_factor=slow)[0, 0])
                R[k]["local_min"] = round(loc, 2) if np.isfinite(loc) else None
            ok = [v for v in R.values() if "minutes" in v and v.get("local_min")]
            ratio = [v["minutes"] / v["local_min"] for v in ok if v["local_min"] > 0]
            out["states"][st] = {"n": len(R), "n_ok": len(ok), "n_err": sum("error" in v for v in R.values()), "n_blocked": sum(bool(v.get("blocked")) for v in R.values()),
                                 "n_local": sum(v.get("source") == "local" for v in R.values()), "n_online": sum(v.get("source") == "online" for v in R.values()),
                                 "ratio_median": round(float(np.median(ratio)), 2) if ratio else None,
                                 "ratio_p90": round(float(np.percentile(ratio, 90)), 2) if ratio else None, "routes": R}
            print(f"{storm} {st}：{len(R)} 条，成功 {len(ok)}（其中深水截断、按减速估 {out['states'][st]['n_blocked']}），出错 {out['states'][st]['n_err']}；在线 / 本地 中位 {out['states'][st]['ratio_median']}，P90 {out['states'][st]['ratio_p90']}")
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); from console_en import add_en; add_en(out)   # 导航步骤英文 steps_en（2026-10-28）
        (f.parent / "routes.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print("→", f.parent / "routes.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
