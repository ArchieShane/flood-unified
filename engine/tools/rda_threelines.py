"""RD-A 三道防线联动：L1 → L2 → L2+planned → L2+suggested 各态的残余积水点，交给第三道防线的调度求解器（swbrain.dispatch2）派单（2026-10-24）。

只读、只新增：不改 swbrain 的调度代码和 config.toml；所有调整都在本脚本的内存里做：
- 积水点 = tools/rda_l3.assess（二维残余积水片 + 过程线 + 时限 + 能否交给 L3 的单点判定）；
- 调度 = dispatch.build_context（路网 Dijkstra 行程矩阵）+ dispatch2.solve（滚动 MILP，处置包 + 退水重演），目标「平衡」；
- 资源两种（用户 2026-10-24 选「两种都算对比」）：
    island = 只用预案 A Island 片区驻地 A13（现状：各片区各管各的）；
    adm    = ADM 全部驻地 A11–A15，集中调度（跨片区要开工单）——上限，没扣其他片区同时要用的泵；
- 自定（已告知用户）：决策时刻 = 第一个积水点成形（>50 m³）时；滚动窗口数 / 视界放宽到覆盖二维视界（内存里改 [dispatch.v2]，不动文件）；
  积水点归 ADM / Island 片区（只为承包商分区判断）；按时 = 退干时刻 ≤ 雨停 + 处置时限（同 rda_l3）。
输出：console/data/flood2d_rda_<雨>/l3.json（页面「三道防线联动」用）+ out/rda/threelines.json（汇总）。
用法：python tools/rda_threelines.py [--storms S96D1,S96D24] [--states L1,L2,L2P,OPTCS2]"""
from __future__ import annotations
import argparse
import copy
import json
import math
import pathlib
import sys
import time

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import rda_l3 as L3   # noqa: E402
from swbrain import dispatch as D1, dispatch2 as D2, travel   # noqa: E402

AREA, REGION = "Island", "ADM"
POOLS = {"island": ["A13"], "adm": ["A11", "A12", "A13", "A14", "A15"]}
POOL_ZH = {"island": "仅 Island 片区（A13，现状）", "adm": "ADM 全部驻地集中调度（上限）"}
A13_LL = None
HS = ["ADM-75", "ADM-76", "ADM-80", "ADM-161", "ADM-376", "ADM-377", "ADM-378", "ADM-379", "ADM-380", "ADM-382", "ADM-383"]   # RD-A 的 11 个客户历史积水点（同 rda_console.HS）


def sub_fleet(fleet, depots):
    f = copy.deepcopy(fleet)
    f["depots"] = [d for d in f["depots"] if d["depot_id"] in depots]
    f["units"] = [u for u in f["units"] if u.get("depot_id") in depots]
    return f


def anchors(road, feats, depot_ll):
    """路网吸附：积水点最近的路网节点若不连通主路网（干路网上从驻地也到不了，属路网数据断头），改挂最近的连通节点，只用于派车路径；返回 {id: (lon, lat, 平移 m)}。"""
    ll = np.array([[f["lon"], f["lat"]] for f in feats]); src = np.array([depot_ll])
    T = road.travel_minutes(src, ll)[0]
    kx = np.cos(np.radians(24.4))
    out = {}
    for f, (lo, la), tm in zip(feats, ll, T):
        if np.isfinite(tm):
            out[f["id"]] = (lo, la, 0.0); continue
        _, ix = road.tree.query([lo * kx, la], k=300)
        cand = road.v.loc[ix, ["lon", "lat"]].values
        tc = road.travel_minutes(src, cand)[0]
        j = int(np.argmax(np.isfinite(tc))) if np.isfinite(tc).any() else None
        if j is None:
            out[f["id"]] = (lo, la, None); continue
        out[f["id"]] = (float(cand[j, 0]), float(cand[j, 1]), round(float(np.hypot((cand[j, 0] - lo) * kx, cand[j, 1] - la) * 111000)))
    return out


LEVEL_W = {"关键": 100.0, "高": 30.0, "中": 10.0}


def severity(r):
    """严重程度：积水量与水深（自定，已告知用户）；只用来在同一后果等级里排先后。"""
    return (1.0 + math.log1p(r["vol_peak_m3"] / 1000.0)) * (1.0 + min(float(r["max_depth_m"]), 2.0))


def dispatch_one(feats_all, rows_all, t_end, rain_end, fleet, road, C, pool, anc):
    """一个态、一种资源 → 派单与逐点结果。
    2026-10-24 用户口径：先后看后果不看出身——无暴露（空地 / 未建地块 / 公园）的点只监测、不进调度；
    调度优先级 = 后果等级权重（关键 100 / 高 30 / 中 10）× 严重程度，覆盖 dispatch.priority 的结果（只在内存里，不改调度器）。"""
    keep = [i for i, r in enumerate(rows_all) if r.get("level") != "无暴露"]
    feats = [feats_all[i] for i in keep]; rows = [rows_all[i] for i in keep]
    if not rows:
        return {"pool": pool, "pool_zh": POOL_ZH[pool], "t_decide_h": None, "n_points": len(rows_all), "n_dispatchable": 0, "counts": {"只监测": len(rows_all)},
                "pumps": 0, "tankers": 0, "cross_zone_units": 0, "cost_aed": 0, "unserved_reasons": {}, "units_available": len(fleet["units"]), "util": {}, "solve_s": 0,
                "points": {r["id"]: {"units": 0, "models": {}, "from": [], "arrive_h": None, "done_h": None, "result": "只监测", "priority": 0.0} for r in rows_all}, "lines": []}
    cfg = copy.deepcopy(C["cfg"])
    forms = [f["t_form_min"] for f in feats if f.get("t_form_min") is not None]
    t_dec = float(min(forms)) if forms else 0.0
    v2 = cfg["dispatch"].setdefault("v2", {})
    v2["max_windows"] = int(min(48, math.ceil((t_end - t_dec) / float(v2.get("window_min", 60))) + 1))
    v2["horizon_min"] = float(t_end - t_dec + 60)
    fp = {"points": [{"fp_id": f["id"], "lon": anc[f["id"]][0], "lat": anc[f["id"]][1], "area_m2": f["area_m2"], "max_depth_m": f["hmax_m"], "volume_m3": f["vol_peak_m3"],
                      "duration_min": f["dur_h"] * 60, "hydrograph": f["hyd"], "target_drain_min": r["target_min"] + (rain_end - t_dec)} for f, r in zip(feats, rows)]}
    # target_drain_min：调度器按「决策后多少分钟内退干」判，换算成同一个绝对期限（雨停 + 处置时限）
    H = C["H"]
    ctx = D1.build_context(fp, fleet, C["ref"]["facilities"], C["cs"], road, cfg, H)
    ctx["pts0"]["region"] = REGION
    pr = {r["id"]: round(LEVEL_W[r["level"]] * severity(r), 2) for r in rows}
    ctx["pts0"]["priority"] = ctx["pts0"]["fp_id"].map(pr).astype(float)
    ctx["hotspots"] = pd.concat([H, pd.DataFrame({"hotspot_id": [f["id"] for f in feats], "area": AREA, "region": REGION})], ignore_index=True)
    ctx["fleet"] = fleet
    t0 = time.perf_counter()
    res = D2.solve(ctx, cfg, t_dec, objective="balanced", with_pareto=False)
    plan = res["plan"]
    dl = {r["id"]: rain_end + r["target_min"] for r in rows}
    out_pts = {}
    for r in rows:
        g = plan[plan["fp_id"] == r["id"]] if len(plan) else plan
        done = g["done_min"].dropna().max() if len(g) else None
        nat_ok = r["l3_means"] == "无需"
        if nat_ok:
            v = "自然退干"
        elif len(g):
            v = "L3 按时退干" if (done is not None and pd.notna(done) and done <= dl[r["id"]]) else "L3 已派、超时"
        else:
            v = "L3 未派"
        out_pts[r["id"]] = {"units": int(len(g)), "models": dict(g["model"].value_counts()) if len(g) else {}, "from": sorted(set(g["from"])) if len(g) else [],
                            "arrive_h": round(float(g["arrive_min"].min()) / 60, 2) if len(g) else None,
                            "done_h": round(float(done) / 60, 2) if done is not None and pd.notna(done) else None, "result": v, "priority": pr[r["id"]]}
    for r in rows_all:
        if r.get("level") == "无暴露":
            out_pts[r["id"]] = {"units": 0, "models": {}, "from": [], "arrive_h": None, "done_h": None, "result": "只监测", "priority": 0.0}
    # 没派的原因：驻地到不了（积水断路，行程矩阵为无穷）/ 1.5 km 内无排放去处且罐车用完 / 其余 = 求解器判不划算或班组占满
    p0 = ctx["pts0"]; reach = np.isfinite(np.minimum(ctx["T_heavy_d"], ctx["T_light_d"])).any(axis=0)
    for k, p in out_pts.items():
        if p["result"] == "L3 未派" and k in set(p0["fp_id"]):
            i = int(p0.index[p0["fp_id"] == k][0])
            r_ = next(x for x in rows if x["id"] == k)
            p["why"] = ("行程不可达（积水断路或路网缺段）" if not reach[i] else ("1.5 km 内无排放去处，罐车不够" if r_["discharge_m"] > float(cfg["dispatch"]["discharge_radius_m"])
                        else "班组 / 资源已占满或派了也赶不上，求解器未选"))
    lines = []
    if len(plan):
        for _, x in plan.iterrows():
            lines.append({"unit": x["unit_id"], "model": x["model"], "from": x["from"], "fp": x["fp_id"], "a": [round(float(x["from_lon"]), 5), round(float(x["from_lat"]), 5)],
                          "b": [round(float(x["lon"]), 5), round(float(x["lat"]), 5)], "arrive_h": round(float(x["arrive_min"]) / 60, 2),
                          "done_h": round(float(x["done_min"]) / 60, 2) if pd.notna(x.get("done_min")) else None, "cross": bool(x.get("cross_zone", False))})
    cnt = pd.Series([p["result"] for p in out_pts.values()]).value_counts().to_dict()
    pumps = int(plan.loc[plan["model"] != D2.TANKER_MODEL, "unit_id"].nunique()) if len(plan) else 0
    tankers = int(plan.loc[plan["model"] == D2.TANKER_MODEL, "unit_id"].nunique()) if len(plan) else 0
    return {"pool": pool, "pool_zh": POOL_ZH[pool], "t_decide_h": round(t_dec / 60, 2), "n_points": len(rows_all), "n_dispatchable": len(rows), "counts": cnt,
            "pumps": pumps, "tankers": tankers, "cross_zone_units": int(res.get("cross_zone_units") or 0),
            "cost_aed": round(float(res["cost"]["total_aed"]), 0), "unserved_reasons": {str(k): int(v) for k, v in (res.get("unserved_reasons") or {}).items()},
            "units_available": len(fleet["units"]),
            "util": {did: {k: u[k] for k in ("units_used", "units_total", "tankers_used", "tankers_total", "crews_used", "crews_total")} for did, u in res["utilisation"].items()},
            "solve_s": round(time.perf_counter() - t0, 1), "travel": ctx.get("travel_method"),
            "points": out_pts, "lines": lines}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--storms", default=",".join(L3.STORMS)); ap.add_argument("--states", default=",".join(L3.STATES))
    ap.add_argument("--pools", default="island,adm")
    ap.add_argument("--truck-factor", type=float, default=None, help="行程时间整体乘这个系数；缺省取 config [dispatch] truck_time_factor（2026-10-27 起 1.70，按 ArcGIS Online 卡车路线实测校正，tools/rda_routes.py）；1 = 原口径")
    ap.add_argument("--tag", default="", help="非空时另存 l3_<tag>.json、out/rda/threelines_<tag>.json，不覆盖原结果")
    a = ap.parse_args(argv)
    C = L3.context()
    if a.truck_factor is None:
        a.truck_factor = float(C["cfg"]["dispatch"].get("truck_time_factor", 1.0))
    from swbrain import refdata
    road = travel.RoadNetwork(C["ref"]["edges"], C["ref"]["vertices"])
    if a.truck_factor != 1.0:                                       # 卡车校正：本地路网是小汽车通行时间、不计转弯，在线卡车路线实测约慢 1.7 倍
        _tm = road.travel_minutes
        road.travel_minutes = lambda *x, **k: _tm(*x, **k) * a.truck_factor
    fleets = {p: sub_fleet(C["fleet"], POOLS[p]) for p in a.pools.split(",")}
    global A13_LL
    A13_LL = next([d["lon"], d["lat"]] for d in C["fleet"]["depots"] if d["depot_id"] == "A13")
    allsum = []
    H = C["H"]; Hs = H[H["hotspot_id"].isin(HS)].set_index("hotspot_id")
    num = lambda v: None if v is None or (isinstance(v, float) and math.isnan(v)) else int(v)
    hs_info = {h: {"criticality": str(Hs.loc[h, "criticality"]), "target_min": L3.CRIT_TARGET.get(str(Hs.loc[h, "criticality"])),
                   "planA": {k: num(Hs.loc[h, c]) for k, c in (("pumps", "planA_pumps"), ("pump12", "planA_pump12"), ("pump8", "planA_pump8"), ("pump6", "planA_pump6"),
                                                               ("tankers", "planA_tankers"), ("manpower", "planA_manpower"))},
                   "root_cause": str(Hs.loc[h, "root_cause"]) if "root_cause" in Hs.columns and pd.notna(Hs.loc[h, "root_cause"]) else None} for h in HS if h in Hs.index}
    for s in a.storms.split(","):
        rain_end = L3.rain_end_min(s)
        doc = {"storm": s, "rain_end_h": round(rain_end / 60, 2), "generated": time.strftime("%Y-%m-%d %H:%M"), "states": {}, "hotspots": hs_info,
               "basis": {"points": "二维残余积水片（≥0.15 m、≥100 m²、≥0.5 h），剔除塘占地和 DTM 填 0 / 私人地块坑", "priority": "后果等级（关键 / 高 / 中 / 无暴露，按碰到的医院急救隧道、输配电变电站、路面、建筑判；客户热点重要度只抬高）× 严重程度（积水量、水深）；无暴露的点只监测、不派 L3",
                         "deadline": "雨停 + 处置时限（300 m 内重点单位与 200 m 内客户热点重要度取最严）",
                         "dispatch": "swbrain.dispatch2 滚动 MILP（平衡目标），路网 Dijkstra 行程，退水重演；预案 A 车队"
                                     + (f"；行程时间 × {a.truck_factor:g}（按 ArcGIS Online 卡车路线实测校正）" if a.truck_factor != 1.0 else ""),
                         "pools": POOL_ZH, "decide": "第一个积水点成形（>50 m³）时开始派单，此后每 60 min 滚动一次",
                         "snap": "积水点最近的路网节点不连通主路网（路网数据断头）时，派车终点改挂最近的连通节点，平移距离记在 road_snap_m"}}
        for st in a.states.split(","):
            r = L3.assess(s, st, C)
            if r is None:
                continue
            feats, rows, t_end = r
            anc = anchors(road, feats, A13_LL) if feats else {}
            S = {"summary": L3.summarize(s, st, rows), "points": [{**{k: x[k] for k in ("id", "lon", "lat", "hotspots_200m", "criticality", "top_kind", "max_depth_m", "vol_peak_m3",
                                                                                         "t_peak_h", "deadline_h", "t_dry_nat_h", "discharge_m", "l3_means", "l3_units", "verdict", "key_point",
                                                                                         "level", "level_why", "road15_m2", "road30_m2", "bldg15", "bldg30", "subst", "sdist30")},
                                                                     "road_snap_m": anc[x["id"]][2]} for x in rows],
                 "hs_links": {h: [x["id"] for x in rows if h in x["hotspots_200m"].split(",")] for h in HS},
                 "dispatch": {}}
            for p, fl in fleets.items():
                if rows:
                    S["dispatch"][p] = dispatch_one(feats, rows, t_end, rain_end, fl, road, C, p, anc)
                    d = S["dispatch"][p]
                    print(f"  {s} {st:7s} {p:6s} 点 {d['n_points']:3d} {d['counts']}  泵 {d['pumps']} 罐车 {d['tankers']}  {d['solve_s']} s", flush=True)
            doc["states"][st] = S
            allsum.append({"storm": s, "state": st, **{k: v for k, v in S["summary"].items() if k not in ("storm", "state")},
                           **{f"{p}_{k}": S["dispatch"][p][k] for p in S["dispatch"] for k in ("counts", "pumps", "tankers", "cost_aed")}})
        od = ROOT / "console" / "data" / f"flood2d_rda_{s.lower()}"
        if od.exists():
            fn = f"l3_{a.tag}.json" if a.tag else "l3.json"
            sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); from console_en import add_en; add_en(doc)   # 英文界面用的 *_en 字段（2026-10-28）
            (od / fn).write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":"), default=float), encoding="utf-8")
            print("→", od / fn)
    (ROOT / "out/rda").mkdir(parents=True, exist_ok=True)
    (ROOT / f"out/rda/threelines{'_' + a.tag if a.tag else ''}.json").write_text(json.dumps(allsum, ensure_ascii=False, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
