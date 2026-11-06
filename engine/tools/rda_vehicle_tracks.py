"""RD-A L3 逐台设备调度轨迹：每台泵组 / 罐车什么时候从哪出发、去哪个积水点、走哪条路、每段路耗时、按分钟的坐标（2026-10-28 用户）。

只读、只新增：不改调度器、不改 l3.json / routes.json。
1. 调度：同 tools/rda_threelines.py（同一套积水点、优先级、卡车系数、预案 A 车队、两种资源范围），重跑一遍只为拿到完整派单行
   （出发 / 到达 / 开抽 / 退干 / 抢占释放 / 等车）；跑完与 l3.json 里的到达时刻核对。
2. 路线：每段「出发地 → 积水点」按 config [routing] 取卡车路线，带逐段导航线（directionLines：每段米数、分钟、线形）。
   2026-10-28 起本地 NAServer（Heavy Vehicles，分钟 × local_time_factor）优先，求不出 / 连不上再用 ArcGIS Online（Trucking Time）；每段标来源。
   积水障碍同 rda_routes.py：本态全程最大水深包络，≥ 重型车不可通行水深禁行，其余减速；目的地那片不设障碍；被深水截断时深水只减速，标 blocked。
3. 时间线：出发时刻取调度单；路上的位置按路线逐段分钟插值（段内匀速）；到场后的开抽 / 释放 / 退干时刻取调度单。
   路线卡车时间与调度用的本地路网时间（× 卡车系数）不同，两个到达时刻都列出，路线到达晚于调度开抽的标出来。
4. 罐车：调度模型把「现场装水 → 排放口卸水 → 回来」折算成抽排速率，没有逐趟路线；这里只出驻地 → 现场一段，现场时段标「往返排放（按速率折算）」。
时间 = 自降雨开始的分钟（T+hh:mm）。
输出：out/rda/vehicle_tracks/<雨>/车辆调度轨迹_<雨>.xlsx、vehicle_tracks_<雨>.gpkg；console/data/flood2d_rda_<雨>/tracks.json（页面「三道防线联动」车辆回放）
用法：python -X utf8 tools/rda_vehicle_tracks.py [--storms S61D1,A61D1] [--states ...] [--pools island,adm] [--threads 4]（需 SWBRAIN_SWMM_WORK；在线备选要 ARCGIS_API_KEY）
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import rda_l3 as L3   # noqa: E402
import rda_threelines as TL   # noqa: E402
from shapely.geometry import LineString, Point, shape   # noqa: E402
from swbrain import dispatch2 as D2, local_route as LR, online_route as OR, ponding, routing as RT, travel   # noqa: E402

STORMS = ["S61D1", "A61D1"]
STATES = ["L1", "L2", "L2P", "OPTCS2", "OPTA61PS", "OPTA61PH2"]
CACHE = ROOT / "out" / "routes" / "cache_detail"
OUT = ROOT / "out" / "rda" / "vehicle_tracks"
KX = math.cos(math.radians(24.4)) * 111000
TK = D2.TANKER_MODEL


def clock(m):
    if m is None or (isinstance(m, float) and not math.isfinite(m)):
        return None
    m = int(round(m)); return f"T+{m // 60:02d}:{m % 60:02d}"


def run_dispatch(storm, states, C, road, fleets):
    """一场雨：逐态、逐资源范围走 rda_threelines.dispatch_one（同口径），截下 D2.solve 的完整派单。返回 rain_end, {(态, 范围): {...}}。"""
    rain_end = L3.rain_end_min(storm); out = {}; cap = {}; _solve = D2.solve

    def grab(*a, **k):
        r = _solve(*a, **k); cap["res"] = r; return r
    D2.solve = grab
    try:
        for st in states:
            r = L3.assess(storm, st, C)
            if r is None:
                print(f"  {storm} {st}：没有二维结果，跳过"); continue
            feats, rows, t_end = r
            if not rows:
                continue
            anc = TL.anchors(road, feats, TL.A13_LL)
            for p, fl in fleets.items():
                cap.clear()
                d = TL.dispatch_one(feats, rows, t_end, rain_end, fl, road, C, p, anc)
                res = cap.get("res")
                out[(st, p)] = {"plan": res["plan"] if res else pd.DataFrame(), "summary": d, "rows": {x["id"]: x for x in rows}, "t_end": t_end}
                print(f"  {storm} {st:9s} {p:6s} 派单 {len(out[(st, p)]['plan'])} 行", flush=True)
    finally:
        D2.solve = _solve
    return rain_end, out


def check_l3(storm, runs):
    """重跑的派单与页面 l3.json 的派单线核对（设备、积水点、到达时刻）。"""
    f = ROOT / "console" / "data" / f"flood2d_rda_{storm.lower()}" / "l3.json"
    if not f.exists():
        return {}
    J = json.loads(f.read_text(encoding="utf-8"))["states"]; out = {}
    for (st, p), R in runs.items():
        old = {(x["unit"], x["fp"], x["arrive_h"]) for x in (((J.get(st) or {}).get("dispatch") or {}).get(p) or {}).get("lines", [])}
        pl = R["plan"]
        new = {(x["unit_id"], x["fp_id"], round(float(x["arrive_min"]) / 60, 2)) for _, x in pl.iterrows()} if len(pl) else set()
        out[f"{st}/{p}"] = {"l3_json": len(old), "rerun": len(new), "same": len(old & new)}
    return out


def route_detail(a, b, bars, names):
    """在线卡车路线 + 逐段导航线；2026-10-28 起实现在 swbrain/online_route.detail（缓存不变），这里留个别名给旧调用。"""
    return OR.detail(a, b, bars, names)


def state_barriers(storm, st, cfg):
    imp_m, slow = float(cfg["dispatch"]["impassable_depth_m"]["heavy"]), float(cfg["dispatch"]["slow_factor"])
    r = ponding.compute(f"RDA_{storm}", st, 0.15, pond_scope="state")
    polys = [shape(x["geom"]) for x in r["feats"]]; depths = [x["hmax_m"] for x in r["feats"]]
    return polys, OR.barriers(polys, depths, imp_m, slow), OR.barriers(polys, depths, 99.0, slow)


def routes_for(pairs, polys, hard, soft, threads):
    """pairs: {键: (a, b, 出发地名, 积水点)} → {键: 路线}；目的地那片不设障碍，深水截断时改成只减速并标 blocked。
    来源按 config [routing] providers（2026-10-28 起本地 NAServer 优先、ArcGIS Online 备选，swbrain/routing.py）；路线里 source 标是哪个。"""
    def one(item):
        k, (a, b, dep, fp) = item
        skip = {i for i, g in enumerate(polys) for q in (a, b) if g.contains(Point(*q)) or g.distance(Point(*q)) < 0.0003}   # 目的地那片、转派时出发的那片都不设障碍（车在水边）
        r = RT.solve(a, b, [x for i, x in enumerate(hard) if i not in skip], [x for i, x in enumerate(soft) if i not in skip], (dep, fp), kind="detail")
        return k, r
    with ThreadPoolExecutor(threads) as ex:
        return dict(ex.map(one, pairs.items()))


def trace(segs):
    """逐段导航线 → 沿路线的 (累计分钟, lon, lat, 段号)；段内按长度匀速分配该段分钟。"""
    T, X, Y, S = [0.0], [], [], []
    t0 = 0.0
    for j, s in enumerate(segs):
        P = np.asarray(s["path"], float)
        if len(P) < 2:
            t0 += s["minutes"]; continue
        d = np.r_[0.0, np.cumsum(np.hypot(np.diff(P[:, 0]) * KX, np.diff(P[:, 1]) * 111000))]
        tt = t0 + (d / d[-1] * s["minutes"] if d[-1] > 0 else np.zeros(len(d)))
        if not X:
            X.append(P[0, 0]); Y.append(P[0, 1]); S.append(j)
        T += list(tt[1:]); X += list(P[1:, 0]); Y += list(P[1:, 1]); S += [j] * (len(P) - 1)
        t0 += s["minutes"]
    return np.asarray(T[:len(X)]), np.asarray(X), np.asarray(Y), np.asarray(S)


def legs_of(storm, st, pool, R, routes):
    """派单行 → 每台设备每段行程（出发 / 在线到达 / 调度到达 / 开抽 / 结束）。"""
    pl = R["plan"]; end_h = float(R["t_end"]) + 60.0; out = []
    if not len(pl):
        return out
    pl = pl.sort_values(["unit_id", "depart_min"])
    for uid, g in pl.groupby("unit_id", sort=False):
        for k, (_, x) in enumerate(g.iterrows(), 1):
            a = [round(float(x["from_lon"]), 5), round(float(x["from_lat"]), 5)]; b = [round(float(x["lon"]), 5), round(float(x["lat"]), 5)]
            ro = routes.get(f"{a[0]},{a[1]}|{b[0]},{b[1]}") or {"error": "no route"}
            dep = float(x["depart_min"]); on = ro.get("minutes")
            rel = x.get("released_min"); done = x.get("done_min")
            if rel is not None and pd.notna(rel):
                end, why = float(rel), "入流压住、余量 30 min 内可抽完，本泵释放转派"
            elif done is not None and pd.notna(done):
                end, why = float(done), ("罐车：积水点退干" if x["model"] == TK else "积水点退干")
            else:
                end, why = end_h, "二维视界末仍未退干"
            arr_on = dep + on if on is not None else None
            rw = R["rows"].get(x["fp_id"], {})
            out.append({"storm": storm, "state": st, "pool": pool, "unit_id": uid, "model": x["model"], "leg": k, "window": int(x["window"]),
                        "from": x["from"], "from_lon": a[0], "from_lat": a[1], "fp_id": x["fp_id"], "fp_lon": b[0], "fp_lat": b[1],
                        "fp_level": rw.get("level"), "fp_hotspots": rw.get("hotspots_200m"), "fp_deadline_h": rw.get("deadline_h"),
                        "depart_min": dep, "vehicle_wait_min": float(x.get("vehicle_wait_min") or 0),
                        "online_min": on, "online_km": ro.get("km"), "arrive_online_min": arr_on,
                        "sched_travel_min": float(x["travel_min"]), "arrive_sched_min": float(x["arrive_min"]), "pump_from_min": float(x["pumping_from_min"]),
                        "end_min": end, "end_why": why, "rate_m3h": float(x["rate_m3h"]), "package": x.get("package"),
                        "blocked": bool(ro.get("blocked")), "route_error": ro.get("error"), "route_src": ro.get("source"), "route_fallback": ro.get("fallback_reason"),
                        "online_late": bool(arr_on is not None and arr_on > float(x["pumping_from_min"]) + 0.5), "_route": ro})
    return out


def positions(legs, step, end_cap):
    """一台设备的所有行程 → 每 step 分钟的 (时刻, lon, lat, 状态, 段, 道路)。从第一次出发到最后一段结束。"""
    legs = sorted(legs, key=lambda l: l["depart_min"]); out = []
    t = math.ceil(legs[0]["depart_min"] / step) * step
    t_last = min(max(l["end_min"] for l in legs), end_cap)
    tr = {id(l): (trace(l["_route"]["segs"]) if l["_route"].get("segs") else None) for l in legs}
    while t <= t_last + 1e-9:
        cur = None
        for l in legs:
            if l["depart_min"] <= t:
                cur = l
        l = cur; tt = t - l["depart_min"]; T = tr[id(l)]
        drive = l["online_min"] if l["online_min"] is not None else l["sched_travel_min"]
        if tt < drive and T is not None:
            lo, la = float(np.interp(tt, T[0], T[1])), float(np.interp(tt, T[0], T[2]))
            j = int(T[3][min(np.searchsorted(T[0], tt, side="right") - 1, len(T[3]) - 1)])
            out.append((t, lo, la, "行驶", l["leg"], l["_route"]["segs"][j].get("road"), l["fp_id"]))
        elif tt < drive:                                                # 没有在线路线：起终点直线插值，标出来
            f = tt / drive if drive > 0 else 1.0
            out.append((t, l["from_lon"] + f * (l["fp_lon"] - l["from_lon"]), l["from_lat"] + f * (l["fp_lat"] - l["from_lat"]), "行驶（无在线路线，直线示意）", l["leg"], None, l["fp_id"]))
        else:
            if t < l["pump_from_min"]:
                s = "到场架设 / 等同点设备"
            elif t <= l["end_min"]:
                s = "往返排放（按速率折算）" if l["model"] == TK else "抽排"
            else:
                s = "抽完待命（模型不排回程）"
            out.append((t, l["fp_lon"], l["fp_lat"], s, l["leg"], None, l["fp_id"]))
        t += step
    return out


def page_doc(storm, rain_end, legs, cap, dt=0.25):
    """控制台回放用：每态、每资源范围、每台设备的行程段；路上的位置给 [相对出发分钟, lon, lat]（每 dt 分钟取一点），页面按时刻插值。"""
    out = {"storm": storm, "generated": time.strftime("%Y-%m-%d %H:%M"), "rain_end_min": rain_end, "states": {}}
    for l in legs:
        S = out["states"].setdefault(l["state"], {}).setdefault(l["pool"], {"t_end": cap[(l["state"], l["pool"])], "units": {}})
        U = S["units"].setdefault(l["unit_id"], {"model": l["model"], "legs": []})
        segs = l["_route"].get("segs")
        drive = l["online_min"] if l["online_min"] is not None else l["sched_travel_min"]
        if segs:
            T, X, Y, _ = trace(segs)
            tt = np.r_[np.arange(0.0, T[-1], dt), T[-1]]
            path = [[round(float(a), 2), round(float(np.interp(a, T, X)), 5), round(float(np.interp(a, T, Y)), 5)] for a in tt]
        else:
            path = [[0.0, l["from_lon"], l["from_lat"]], [round(drive, 2), l["fp_lon"], l["fp_lat"]]]
        U["legs"].append({"k": l["leg"], "from": l["from"], "fp": l["fp_id"], "dep": round(l["depart_min"], 1), "drive": round(drive, 2), "km": l["online_km"],
                          "arr_s": round(l["arrive_sched_min"], 1), "pump": round(l["pump_from_min"], 1), "end": round(l["end_min"], 1), "why": l["end_why"],
                          "blocked": l["blocked"], "online": bool(segs), "src": l["route_src"], "path": path})
    for S in out["states"].values():
        for P in S.values():
            for U in P["units"].values():
                U["legs"].sort(key=lambda x: x["dep"])
    return out


LEG_COLS = {"state": "态", "pool": "资源范围", "unit_id": "设备", "model": "型号", "leg": "第几段", "window": "调度窗口", "from": "出发地", "fp_id": "目标积水点",
            "fp_level": "后果等级", "fp_hotspots": "200 m 内客户热点", "fp_deadline_h": "处置期限(h)", "depart": "出发", "vehicle_wait_min": "等车(min)",
            "route_src": "路线来源", "online_km": "路线(km)", "online_min": "路线卡车耗时(min)", "arrive_online": "到达（路线）", "sched_travel_min": "调度行程(min)", "arrive_sched": "到达（调度）",
            "pump_from": "开抽", "end": "离开 / 结束", "end_why": "结束原因", "rate_m3h": "抽排能力(m³/h)", "package": "处置包", "online_late": "路线到达晚于调度开抽",
            "blocked": "深水截断按减速估", "route_error": "路线错误", "route_fallback": "改用备选的原因"}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--storms", default=",".join(STORMS)); ap.add_argument("--states", default=",".join(STATES))
    ap.add_argument("--pools", default="island,adm"); ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args(argv)
    import geopandas as gpd
    C = L3.context(); cfg = C["cfg"]; tf = float(cfg["dispatch"].get("truck_time_factor", 1.0))
    road = travel.RoadNetwork(C["ref"]["edges"], C["ref"]["vertices"])
    if tf != 1.0:                                                       # 同 rda_threelines：本地路网 × 卡车系数
        _tm = road.travel_minutes
        road.travel_minutes = lambda *x, **k: _tm(*x, **k) * tf
    TL.A13_LL = next([d["lon"], d["lat"]] for d in C["fleet"]["depots"] if d["depot_id"] == "A13")
    fleets = {p: TL.sub_fleet(C["fleet"], TL.POOLS[p]) for p in a.pools.split(",")}
    report = {}
    for storm in a.storms.split(","):
        t0 = time.perf_counter()
        rain_end, runs = run_dispatch(storm, a.states.split(","), C, road, fleets)
        chk = check_l3(storm, runs)
        legs = []
        for st in dict.fromkeys(s for s, _ in runs):
            pairs = {}
            for (s_, p), R in runs.items():
                if s_ != st or not len(R["plan"]):
                    continue
                for _, x in R["plan"].iterrows():
                    aa = [round(float(x["from_lon"]), 5), round(float(x["from_lat"]), 5)]; bb = [round(float(x["lon"]), 5), round(float(x["lat"]), 5)]
                    pairs.setdefault(f"{aa[0]},{aa[1]}|{bb[0]},{bb[1]}", (aa, bb, str(x["from"]), str(x["fp_id"])))
            if not pairs:
                continue
            polys, hard, soft = state_barriers(storm, st, cfg)
            routes = routes_for(pairs, polys, hard, soft, a.threads)
            n_err = sum("error" in r for r in routes.values())
            print(f"  {storm} {st:9s} 路线 {len(routes)} 条，出错 {n_err}，深水截断 {sum(bool(r.get('blocked')) for r in routes.values())}", flush=True)
            for (s_, p), R in runs.items():
                if s_ == st:
                    legs += legs_of(storm, st, p, R, routes)
        od = OUT / storm; od.mkdir(parents=True, exist_ok=True)
        cap = {(s_, p): float(R["t_end"]) + 60.0 for (s_, p), R in runs.items()}
        P1, SEG, LN = [], [], []
        by_unit = {}
        for l in legs:
            by_unit.setdefault((l["state"], l["pool"], l["unit_id"]), []).append(l)
            ro = l["_route"]; t = l["depart_min"]
            for j, s in enumerate(ro.get("segs") or [], 1):
                SEG.append({"态": l["state"], "资源范围": l["pool"], "设备": l["unit_id"], "第几段": l["leg"], "导航步": j, "进入时刻": clock(t), "离开时刻": clock(t + s["minutes"]),
                            "进入(min)": round(t, 1), "耗时(min)": round(s["minutes"], 2), "距离(m)": s["meters"], "道路": s["road"], "导航": s["text"]})
                t += s["minutes"]
            line = [tuple(c) for s in (ro.get("segs") or []) for c in s["path"]]
            LN.append({**{k: l[k] for k in ("state", "pool", "unit_id", "model", "leg", "from", "fp_id", "online_km", "online_min", "blocked", "route_src")},
                       "depart": clock(l["depart_min"]), "arrive": clock(l["arrive_online_min"]),
                       "geometry": LineString(line) if len(line) >= 2 else LineString([(l["from_lon"], l["from_lat"]), (l["fp_lon"], l["fp_lat"])])})
        for (st, p, uid), L in by_unit.items():
            for (t, lo, la, s, k, rd, fp) in positions(L, 1.0, cap[(st, p)]):
                P1.append({"态": st, "资源范围": p, "设备": uid, "型号": L[0]["model"], "时刻": clock(t), "分钟": int(round(t)), "经度": round(lo, 6), "纬度": round(la, 6),
                           "状态": s, "第几段": k, "积水点": fp, "道路": rd})
        P1 = pd.DataFrame(P1)
        T = pd.DataFrame([{k: v for k, v in l.items() if k != "_route"} for l in legs])
        if len(T):
            for c in ("depart", "arrive_online", "arrive_sched", "pump_from", "end"):
                T[c] = T[f"{c}_min"].map(clock)
            T = T.sort_values(["state", "pool", "unit_id", "leg"])[list(LEG_COLS)].rename(columns=LEG_COLS)
            for c in ("路线卡车耗时(min)", "调度行程(min)"):
                T[c] = T[c].astype(float).round(1)
        SUM = []
        for (st, p), R in runs.items():
            g = [l for l in legs if l["state"] == st and l["pool"] == p]
            SUM.append({"态": st, "资源范围": p, "设备数": len({l["unit_id"] for l in g}), "行程段": len(g), "转派段": sum(l["leg"] > 1 for l in g),
                        "泵": len({l["unit_id"] for l in g if l["model"] != TK}), "罐车": len({l["unit_id"] for l in g if l["model"] == TK}),
                        "路线来自本地服务": sum(l["route_src"] == "local" for l in g), "路线来自在线（备选）": sum(l["route_src"] == "online" for l in g),
                        "路线到达晚于调度开抽": sum(l["online_late"] for l in g), "深水截断": sum(l["blocked"] for l in g), "路线错误": sum(bool(l["route_error"]) for l in g),
                        "路线/调度行程 中位": round(float(np.median([l["online_min"] / l["sched_travel_min"] for l in g if l["online_min"] and l["sched_travel_min"] > 0])), 2) if g else None,
                        "与页面 l3.json 一致的派单": f"{chk.get(f'{st}/{p}', {}).get('same')}/{chk.get(f'{st}/{p}', {}).get('l3_json')}"})
        note = pd.DataFrame({"说明": [
            f"雨型 {storm}：雨停 T+{clock(rain_end)[2:]}；时刻 = 自降雨开始。",
            "设备 = 预案 A 车队的泵组 / 罐车（逐台）；一台泵抽完一个点可在下一调度窗口转派（第几段 > 1，出发地 = 上一个积水点）。",
            "island = 只用 Island 驻地 A13（现状）；adm = ADM 五个驻地集中调度（上限）。",
            f"调度：swbrain.dispatch2 滚动 MILP（平衡），本地路网 × 卡车系数 {tf:g}，同 tools/rda_threelines.py；出发 / 开抽 / 结束取调度单。",
            f"路线：本地 NAServer（Heavy Vehicles，服务分钟 × {LR.cfg()['local_time_factor']:g} 对齐卡车）优先，求不出时用 ArcGIS Online 卡车路线（Trucking Time），「路线来源」列标明；"
            "本态全程最大水深包络作障碍；路上位置按导航每步的分钟、步内匀速插值。本地服务的已知问题见 docs/本地路径服务问题说明_20261028.md。",
            "到达（路线）与到达（调度）不同：路线卡车耗时与调度用的行程不是一个模型；晚于调度开抽的标出，调度的开抽 / 退干没按路线时间重算。",
            "罐车：只有驻地 → 现场一段路线；现场的装水 → 排放口 → 回来按速率折算，模型没有逐趟路线。抽完后模型不排回程。",
            "逐分钟坐标全程（含到场后的原地点）见 gpkg 图层 positions_1min；本表只列行驶中的逐分钟坐标和全程逐 5 分钟位置。"]})
        xl = od / f"车辆调度轨迹_{storm}.xlsx"
        with pd.ExcelWriter(xl) as w:
            note.to_excel(w, sheet_name="说明", index=False); pd.DataFrame(SUM).to_excel(w, sheet_name="汇总", index=False)
            T.to_excel(w, sheet_name="行程表", index=False); pd.DataFrame(SEG).to_excel(w, sheet_name="逐段导航", index=False)
            if len(P1):
                P1[P1["状态"].str.startswith("行驶")].to_excel(w, sheet_name="逐分钟坐标_行驶中", index=False)
                P1[P1["分钟"] % 5 == 0].to_excel(w, sheet_name="逐5分钟位置_全程", index=False)
        gp = od / f"vehicle_tracks_{storm}.gpkg"
        if gp.exists():
            gp.unlink()
        if LN:
            gpd.GeoDataFrame(LN, crs=4326).to_file(gp, layer="routes", driver="GPKG")
        if len(P1):
            gpd.GeoDataFrame(P1.rename(columns={"态": "state", "资源范围": "pool", "设备": "unit_id", "型号": "model", "时刻": "clock", "分钟": "t_min", "经度": "lon", "纬度": "lat",
                                                "状态": "status", "第几段": "leg", "积水点": "fp_id", "道路": "road"}),
                             geometry=gpd.points_from_xy(P1["经度"], P1["纬度"]), crs=4326).to_file(gp, layer="positions_1min", driver="GPKG")
        cd = ROOT / "console" / "data" / f"flood2d_rda_{storm.lower()}"
        if cd.exists():
            (cd / "tracks.json").write_text(json.dumps(page_doc(storm, rain_end, legs, cap), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            print("→", cd / "tracks.json")
        report[storm] = {"summary": SUM, "check_l3": chk, "s": round(time.perf_counter() - t0, 1)}
        print(f"→ {xl}\n→ {gp}（{report[storm]['s']} s）", flush=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
