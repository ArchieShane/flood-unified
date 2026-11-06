"""第二、三道防线：预报后预置 + 事中应急调度。

流程：积水区（世界模型/模拟）→ 优先级（重点单位×严重度）→ 可行性分诊（入流、排放去处、道路可达）
      → 时变路网行程矩阵 → 分派 MILP（多波次滚动）→ 与贪心基线对照 → 指标 → 调度单
预置：按预警等级选驻地，最大化规定到达时间内的加权覆盖（最大覆盖选址 MILP），再把泵按需求分到驻地。
求解器：scipy.optimize.milp（HiGHS）。
"""
from __future__ import annotations
import json
from pathlib import Path
import time
import numpy as np
import pandas as pd
import shapely.affinity as sh_affine
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.spatial import cKDTree
from shapely.geometry import shape
from . import travel as tr, i18n, replay, hotspots as hsm

ALERTS = ["蓝", "黄", "橙", "红"]
ALERT_EN = {"蓝": "Blue", "黄": "Yellow", "橙": "Orange", "红": "Red"}


# ---------------------------------------------------------------- 输入整理
def points_frame(fp: dict) -> pd.DataFrame:
    rows = []
    for p in fp["points"]:
        geom = shape(p["geometry"]) if p.get("geometry") else None
        lon = p.get("lon", geom.centroid.x if geom is not None else None); lat = p.get("lat", geom.centroid.y if geom is not None else None)
        rows.append({"fp_id": p["fp_id"], "lon": lon, "lat": lat, "area_m2": p.get("area_m2", geom.area * 1.2e10 if geom is not None else 0), "max_depth_m": p.get("max_depth_m", 0),
                     "volume_m3": p.get("volume_m3", 0), "duration_min": p.get("duration_min", 360), "land_use": p.get("land_use", ""), "note": p.get("note", ""),
                     "hydrograph": p.get("hydrograph") or [], "geom": geom, "terminal": p.get("terminal"),
                     "target_drain_min_pt": p.get("target_drain_min"), "source": p.get("source"), "spill_m3": p.get("spill_m3", 0), "note_en": p.get("note_en")})
    return pd.DataFrame(rows)


def state_at(hyd: list, t_min: float, vol_peak: float, dur: float) -> tuple[float, float]:
    """t 时刻的（体积, 入流 m³/h），线性插值；没有过程线时按三角形近似。"""
    if hyd:
        ts = [h["t_min"] for h in hyd]; vs = [h["volume_m3"] for h in hyd]; qs = [h["inflow_m3h"] for h in hyd]
        return float(np.interp(t_min, ts, vs)), float(np.interp(t_min, ts, qs))
    peak = 180.0
    frac = t_min / peak if t_min <= peak else max(0.0, 1 - (t_min - peak) / max(dur - peak, 1))
    return vol_peak * frac, (vol_peak / (peak / 60)) if t_min < peak else 0.0


# ---------------------------------------------------------------- 优先级与可行性
def priority(pts: pd.DataFrame, facilities: pd.DataFrame, cfg: dict, hotspots: pd.DataFrame | None = None) -> pd.DataFrame:
    D = cfg["dispatch"]; W = D["facility_weight"]; T = D["target_drain_min"]
    fac = facilities.dropna(subset=["lon", "lat"]).reset_index(drop=True)
    tree = cKDTree(np.c_[fac["lon"].values * np.cos(np.radians(24.4)) * 111000, fac["lat"].values * 111000])
    q = np.c_[pts["lon"].values * np.cos(np.radians(24.4)) * 111000, pts["lat"].values * 111000]
    near = tree.query_ball_point(q, r=D["facility_radius_m"])
    w_fac, top_kind, fac_list, target = [], [], [], []
    for i, ix in enumerate(near):
        kinds = fac.loc[ix, "kind"].tolist() if len(ix) else []
        if pts.loc[i, "land_use"] == "tunnel":
            kinds.append("tunnel")
        w = 1.0 + sum(float(W.get(k, 1.0)) for k in kinds)
        beds = float(fac.loc[ix, "size"][fac.loc[ix, "kind"] == "hospital"].sum()) if len(ix) else 0.0
        w += beds / 100.0
        best = min(kinds, key=lambda k: T.get(k, T["default"])) if kinds else None
        w_fac.append(w); top_kind.append(best or "none"); fac_list.append(sorted(set(kinds)))
        target.append(float(T.get(best, T["default"])) if best else float(T["default"]))
    sev = (1 + 2 * (pts["max_depth_m"] >= 0.3).astype(float) + np.log1p(pts["volume_m3"] / 1000.0)) * (1 + pts["duration_min"] / 480.0)
    # 客户热点：积水点 300 m 内有客户登记的热点 → 按其重要度加权（客户的现场经验进优先级）
    hw, hid, hcrit = hsm.priority_weights(pts["lon"].values, pts["lat"].values, hotspots, cfg) if (hotspots is not None and "hotspots" in cfg) else (np.zeros(len(pts)), [None] * len(pts), [None] * len(pts))
    out = pts.copy()
    out["w_fac"] = list(np.array(w_fac) + hw); out["top_kind"] = top_kind; out["facilities"] = fac_list
    # 处置时限：积水点自带的（客户预案 A 分级目标）与重点单位目标取小者
    tp = pd.to_numeric(pts["target_drain_min_pt"], errors="coerce") if "target_drain_min_pt" in pts.columns else pd.Series(np.nan, index=pts.index)
    out["target_drain_min"] = np.where(tp.notna(), np.minimum(tp.fillna(np.inf).values, np.array(target)), np.array(target))
    out["client_hotspot_id"] = hid; out["client_criticality"] = hcrit
    out["priority"] = ((np.array(w_fac) + hw) * sev.values).round(2)
    return out


def relay_factor(dist_m, cfg: dict) -> tuple[int, float]:
    """接力排放：排放点超出 relay_beyond_m 的部分每 relay_hose_m 加一级接力，每级泵排量乘 relay_rate_factor（水头损失与串联损耗的规划级近似），不低于 relay_min_factor。返回 (级数, 折减系数)。"""
    D = cfg["dispatch"]
    beyond = float(D.get("relay_beyond_m", 300)); hose = float(D.get("relay_hose_m", 100)); f = float(D.get("relay_rate_factor", 0.85)); fmin = float(D.get("relay_min_factor", 0.4))
    try:
        d = float(dist_m)
    except (TypeError, ValueError):
        return 0, 1.0
    if not np.isfinite(d) or d <= beyond:
        return 0, 1.0
    stages = int(np.ceil((d - beyond) / hose))
    return stages, max(f ** stages, fmin)


def feasibility(pts: pd.DataFrame, cs: pd.DataFrame, fleet: dict, cfg: dict, t_min: float) -> pd.DataFrame:
    D = cfg["dispatch"]
    max_rate = max(m["rate_m3h"] for m in fleet["models"]) * D["max_units_per_point"]
    out = pts.copy()
    vol, inflow = zip(*[state_at(h, t_min, v, d) for h, v, d in zip(pts["hydrograph"], pts["volume_m3"], pts["duration_min"])])
    out["volume_now_m3"] = np.round(vol, 0); out["inflow_now_m3h"] = np.round(inflow, 0)
    # 排放去处：附近的出水口/泵站/渗井终点，且其汇水区在目标情景下不是大面积超载
    main = list(cfg["model"]["scenarios"])[0]     # 泵在雨峰后工作，排放去处按常规雨下的管网状态判
    ok = cs[cs["terminal_kind"].isin(["出水口", "泵站", "渗井"]) & cs["lon"].notna()].copy()
    ok = ok[ok[f"over_share_{main}"].fillna(0) < 0.8]
    if len(ok):
        tree = cKDTree(np.c_[ok["lon"].values * np.cos(np.radians(24.4)) * 111000, ok["lat"].values * 111000])
        d, ix = tree.query(np.c_[pts["lon"].values * np.cos(np.radians(24.4)) * 111000, pts["lat"].values * 111000])
        # 直线距离乘一个绕行系数当作「软管实际要走多远」。实测（10cc，40 个点抽样）路网距离是直线的
        # 1.66 倍（四分位 1.32 / 2.52，最大 10.25），按路网算每台泵的软管中位从 650 m 涨到 1,300 m、
        # 接力从 6 级涨到 12 级，且 40 个里有 20 个按直线判「1.5 km 内有去处」、按路走已经超了。
        # 软管在 MILP 里是硬约束（全网 11.6 万米），低估会让模型以为能派的泵比实际多。
        # 缺省 1.0 = 保持原行为；改成 1.66 之前要想清楚：一半的可派泵点会变成只能靠罐车。
        det = float(D.get("discharge_detour_factor", 1.0))
        out["discharge_dist_m"] = np.round(d * det, 0); out["discharge_to"] = ok["terminal_unitid"].values[ix]
        out["discharge_dist_straight_m"] = np.round(d, 0)
    else:
        out["discharge_dist_m"] = np.inf; out["discharge_to"] = None; out["discharge_dist_straight_m"] = np.inf
    out["pump_worthy"] = out["inflow_now_m3h"] < 0.8 * max_rate
    # 「这个点附近没有可接纳的排放去处」与「这一片我们根本没有管网数据」是两回事，不能混成一句话。
    # 实测（10 年 + 20% CC）：ADM 的点离最近终点最远 16.1 km，AAM 最近 27.9 km、DRM 最近 88.8 km——
    # 因为管网台账只覆盖阿布扎比（终点经度 54.31–55.08），艾因与 Dhafra 一个终点都没有。
    # 两组之间没有重叠，20 km 的阈值两边都零误判。2026-09-24 加。
    out["discharge_known"] = out["discharge_dist_m"] <= float(D.get("discharge_unknown_beyond_m", 20000.0))
    out["discharge_ok"] = out["discharge_known"] & (out["discharge_dist_m"] <= D["discharge_radius_m"])
    out["relay_needed"] = out["discharge_ok"] & (out["discharge_dist_m"] > D["relay_beyond_m"])
    rf = [relay_factor(d, cfg) if ok_ else (0, 1.0) for d, ok_ in zip(out["discharge_dist_m"], out["discharge_ok"])]
    out["relay_stages"] = [x[0] for x in rf]; out["relay_factor"] = [x[1] for x in rf]
    # 需排量按折减后的泵能力反算：接力越多，要的泵越多
    out["required_rate_m3h"] = np.round((out["inflow_now_m3h"] + out["volume_now_m3"] / (out["target_drain_min"] / 60.0)) / out["relay_factor"], 0)
    radius_km = float(D["discharge_radius_m"]) / 1000.0

    def reason(r):
        if r["volume_now_m3"] < 50: return "无需处置（已基本退水）"
        if not r["pump_worthy"]: return "入流仍大于可派排量：先封路疏散，雨峰后再派"
        if not r["discharge_known"]: return "本片区没有管网数据，排放去处未知：按最保守办（罐车拉走），拿到管网后重算"
        if not r["discharge_ok"]: return f"{radius_km:g} km 内无可接纳的排放去处：等待管网退水或长距离接力"
        if r["relay_needed"]: return "可派泵（需接力排放）"
        return "可派泵"
    out["triage"] = out.apply(reason, axis=1)
    out["dispatchable"] = out["triage"].str.startswith("可派泵")
    return out


# ---------------------------------------------------------------- 罐车：无排放去处的点靠拉走
def tanker_params(cfg: dict) -> dict:
    T = (cfg.get("dispatch") or {}).get("tankers") or {}; H = cfg.get("hotspots") or {}
    cap = float(T.get("capacity_m3", H.get("tanker_capacity_m3", 17.0)))
    return {"enabled": bool(T.get("enabled", True)), "capacity_m3": cap, "duty_aed_h": float(T.get("duty_aed_h", H.get("tanker_duty_aed_h", 250.0))),
            "load_min": float(T.get("load_min", 10)), "unload_min": float(T.get("unload_min", 10)), "speed_kmh": float(T.get("speed_kmh", 30)),
            "default_haul_km": float(T.get("default_haul_km", 5.0)), "max_haul_km": float(T.get("max_haul_km", 15.0)), "horizon_min": float(T.get("horizon_min", 2880)),
            "max_per_point": int(T.get("max_per_point", 6)), "model": f"tanker-{cap:g}m3"}


def tanker_rate_m3h(cfg: dict, haul_m: float | None) -> tuple[float, float]:
    """一辆罐车的有效排量：容量 / 周转（装 + 卸 + 往返到最近可接纳排放点；没有就按缺省运距）。返回 (m³/h, 周转分钟)。"""
    P = tanker_params(cfg)
    try:
        hk = float(haul_m) / 1000.0
    except (TypeError, ValueError):
        hk = float("nan")
    haul_km = hk if (np.isfinite(hk) and hk <= P["max_haul_km"]) else P["default_haul_km"]      # 最近可接纳排放点太远（艾因/Dhafra 常见）：按缺省运距到就近处置点
    cycle_min = P["load_min"] + P["unload_min"] + 2.0 * haul_km / P["speed_kmh"] * 60.0
    return P["capacity_m3"] / (cycle_min / 60.0), cycle_min


def assign_tankers(pts: pd.DataFrame, plan: pd.DataFrame, n_tankers: int, T_dep: np.ndarray, depots: pd.DataFrame, cfg: dict, t_decide: float, wave: int = 1) -> pd.DataFrame:
    """给「1.5 km 内无排放去处」且未被泵处置、仍有水的点派罐车：按优先级，台数 = ceil(需排量 / 单车有效排量)，每点 ≤ max_per_point，池用完为止；从最近驻地出发。"""
    P = tanker_params(cfg)
    if not P["enabled"] or n_tankers <= 0 or not len(pts):
        return pd.DataFrame()
    served = set(plan["fp_id"]) if len(plan) else set()
    cand = pts[(~pts["fp_id"].isin(served)) & (pts["volume_now_m3"] >= 50) & (~pts["discharge_ok"].astype(bool))].sort_values("priority", ascending=False)
    rows = []; left = int(n_tankers); k = 0
    for i, p in cand.iterrows():
        if left <= 0:
            break
        rate, cycle = tanker_rate_m3h(cfg, p.get("discharge_dist_m"))
        need = int(np.ceil(float(p["required_rate_m3h"]) / max(rate, 1e-6))); need = min(max(need, 1), P["max_per_point"], left)
        trav = T_dep[:, i] if T_dep is not None and T_dep.shape[1] > i else np.array([np.nan])
        if not np.isfinite(trav).any():
            continue
        j = int(np.nanargmin(np.where(np.isfinite(trav), trav, np.inf))); travel = float(trav[j])
        arrive = t_decide + travel + P["load_min"]
        total_rate = need * rate; net = total_rate - float(p["inflow_now_m3h"]); drain = float(p["volume_now_m3"]) / net * 60.0 if net > 0 else np.inf
        if not np.isfinite(drain) or arrive + drain > P["horizon_min"]:                 # 派满上限也在 48 h 内拉不完：不把池浪费在这里
            continue
        d_ = p.get("discharge_dist_m")
        haul_km = (float(d_) / 1000.0) if (pd.notna(d_) and np.isfinite(float(d_)) and float(d_) / 1000.0 <= P["max_haul_km"]) else P["default_haul_km"]
        for _ in range(need):
            k += 1
            rows.append({"wave": wave, "unit_id": f"TK-{k:03d}", "model": P["model"], "rate_m3h": round(rate, 1), "from": depots.iloc[j]["depot_id"], "fp_id": p["fp_id"], "note": p.get("note"),
                         "priority": p["priority"], "top_kind": p.get("top_kind"), "depart_min": round(t_decide, 0), "travel_min": round(travel, 1), "arrive_min": round(arrive, 0), "pumping_from_min": round(arrive, 0),
                         "site_rate_m3h": round(total_rate, 1), "inflow_m3h": float(p["inflow_now_m3h"]), "volume_m3": float(p["volume_now_m3"]),
                         "drain_min": None if not np.isfinite(drain) else round(drain, 0), "done_min": None if not np.isfinite(drain) else round(arrive + drain, 0),
                         "target_drain_min": p["target_drain_min"], "within_target": bool(np.isfinite(drain) and (travel + P["load_min"] + drain) <= float(p["target_drain_min"]) + 60),
                         "discharge_to": p.get("discharge_to"), "lon": p["lon"], "lat": p["lat"], "from_lon": depots.iloc[j]["lon"], "from_lat": depots.iloc[j]["lat"],
                         "duty_aed_h": P["duty_aed_h"], "haul_km": round(haul_km, 1), "cycle_min": round(cycle, 0)})
        left -= need
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 分派 MILP
def assign_milp(pts: pd.DataFrame, units: pd.DataFrame, travel_min: np.ndarray, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """pts: 待派积水点（含 required_rate_m3h、priority）；units: 可用泵（rate_m3h、setup_min、avail_min）；travel_min: units × pts。
    变量 x[k,p]∈{0,1}，z[p]∈{0,1}。约束：每泵 ≤1 点；Σ rate·x ≥ R_p·z_p；Σ x ≤ max_units·z_p；旅程 ≤ max_travel。
    目标：max Σ w_p z_p − λ Σ w_p (travel+setup+avail)/60 · x。"""
    D = cfg["dispatch"]
    K, P = len(units), len(pts)
    if K == 0 or P == 0:
        return pd.DataFrame(), {"status": "empty"}
    feas = np.isfinite(travel_min) & (travel_min + units["avail_min"].values[:, None] <= D["max_travel_min"] + units["avail_min"].values[:, None])
    feas &= travel_min <= D["max_travel_min"]
    pairs = [(k, p) for k in range(K) for p in range(P) if feas[k, p]]
    if not pairs:
        return pd.DataFrame(), {"status": "no feasible pair"}
    nx_ = len(pairs); nz = P; n = nx_ + nz
    w = pts["priority"].values.astype(float); rate = units["rate_m3h"].values.astype(float); R = pts["required_rate_m3h"].values.astype(float)
    c = np.zeros(n)
    for j, (k, p) in enumerate(pairs):
        c[j] = D["tie_penalty"] * w[p] * (travel_min[k, p] + units["setup_min"].values[k] + units["avail_min"].values[k]) / 60.0
    c[nx_:] = -w
    rows, cols, vals, lo, hi = [], [], [], [], []
    ri = 0
    for k in range(K):                       # 每泵最多一个点
        js = [j for j, (kk, _) in enumerate(pairs) if kk == k]
        if js:
            rows += [ri] * len(js); cols += js; vals += [1.0] * len(js); lo.append(-np.inf); hi.append(1.0); ri += 1
    for p in range(P):                       # 排量满足需求：Σ rate x − R z ≥ 0
        js = [j for j, (_, pp) in enumerate(pairs) if pp == p]
        if not js:
            rows += [ri]; cols += [nx_ + p]; vals += [1.0]; lo.append(-np.inf); hi.append(0.0); ri += 1; continue
        rows += [ri] * len(js) + [ri]; cols += js + [nx_ + p]; vals += [rate[pairs[j][0]] for j in js] + [-R[p]]; lo.append(0.0); hi.append(np.inf); ri += 1
        rows += [ri] * len(js) + [ri]; cols += js + [nx_ + p]; vals += [1.0] * len(js) + [-float(D["max_units_per_point"])]; lo.append(-np.inf); hi.append(0.0); ri += 1
    from scipy.sparse import coo_matrix
    A = coo_matrix((vals, (rows, cols)), shape=(ri, n)).tocsr()
    t0 = time.perf_counter()
    res = milp(c, constraints=LinearConstraint(A, lo, hi), integrality=np.ones(n), bounds=Bounds(0, 1), options={"time_limit": 20})
    ms = round((time.perf_counter() - t0) * 1000, 1)
    if res.x is None:
        return pd.DataFrame(), {"status": res.message, "ms": ms}
    x = np.round(res.x[:nx_]).astype(int)
    rows_out = []
    for j, (k, p) in enumerate(pairs):
        if x[j]:
            rows_out.append({"unit_ix": k, "pt_ix": p, "travel_min": round(float(travel_min[k, p]), 1)})
    return pd.DataFrame(rows_out), {"status": "optimal" if res.success else res.message, "ms": ms, "n_vars": n, "n_pairs": nx_, "objective": round(float(res.fun), 3)}


def greedy_assign(pts: pd.DataFrame, units: pd.DataFrame, travel_min: np.ndarray, cfg: dict) -> pd.DataFrame:
    """基线：按优先级逐点，取最近的可用泵直到排量满足。"""
    D = cfg["dispatch"]; used = set(); rows = []
    for p in pts.sort_values("priority", ascending=False).index:
        need = pts.loc[p, "required_rate_m3h"]; got = 0.0; n = 0
        order = np.argsort(travel_min[:, p])
        for k in order:
            if k in used or not np.isfinite(travel_min[k, p]) or travel_min[k, p] > D["max_travel_min"]: continue
            used.add(k); rows.append({"unit_ix": int(k), "pt_ix": int(p), "travel_min": round(float(travel_min[k, p]), 1)}); got += units.loc[k, "rate_m3h"]; n += 1
            if got >= need or n >= D["max_units_per_point"]: break
    return pd.DataFrame(rows)


def plan_from_assignment(assign: pd.DataFrame, pts: pd.DataFrame, units: pd.DataFrame, t_decide: float, wave: int) -> pd.DataFrame:
    if not len(assign):
        return pd.DataFrame()
    rows = []
    for p, g in assign.groupby("pt_ix"):
        us = units.loc[g["unit_ix"]]
        pt = pts.loc[p]
        stages = int(pt.get("relay_stages", 0) or 0); rf = float(pt.get("relay_factor", 1.0) or 1.0)
        relay_extra = float(us["setup_min"].mean()) * max(stages, 1) if bool(pt.get("relay_needed", False)) else 0.0     # 每级接力多一次架设
        arrive = g["travel_min"].values + us["setup_min"].values + us["avail_min"].values + relay_extra
        start = float(np.max(arrive))                     # 最后一台到位才算全排量
        rate = float(us["rate_m3h"].sum()) * rf
        net = rate - float(pt["inflow_now_m3h"])
        drain_min = float(pt["volume_now_m3"]) / net * 60.0 if net > 0 else np.inf
        for (_, u), tmin in zip(us.iterrows(), g["travel_min"].values):
            rows.append({"wave": wave, "unit_id": u["unit_id"], "model": u["model"], "rate_m3h": round(float(u["rate_m3h"]) * rf, 1), "rate_nominal_m3h": float(u["rate_m3h"]), "relay_stages": stages, "relay_factor": round(rf, 3), "from": u["from"], "fp_id": pt["fp_id"], "note": pt["note"],
                         "priority": pt["priority"], "top_kind": pt["top_kind"], "depart_min": round(t_decide + float(u["avail_min"]), 0), "travel_min": round(float(tmin), 1),
                         "arrive_min": round(t_decide + float(u["avail_min"]) + float(tmin), 0), "pumping_from_min": round(t_decide + start, 0),
                         "site_rate_m3h": rate, "inflow_m3h": float(pt["inflow_now_m3h"]), "volume_m3": float(pt["volume_now_m3"]),
                         "drain_min": None if not np.isfinite(drain_min) else round(drain_min, 0), "done_min": None if not np.isfinite(drain_min) else round(t_decide + start + drain_min, 0),
                         "target_drain_min": pt["target_drain_min"], "within_target": (np.isfinite(drain_min) and start + drain_min <= pt["target_drain_min"] + 60),
                         "discharge_to": pt.get("discharge_to"), "lon": pt["lon"], "lat": pt["lat"], "from_lon": u["lon"], "from_lat": u["lat"]})
    return pd.DataFrame(rows)


LEVEL_ORDER = {"III": 3, "II": 2, "I": 1}


def emergency_level(pts: pd.DataFrame) -> tuple[list, list]:
    """WA-726-3 表 11-1 / 附录 H-1 的三级应急，按积水点特征近似：
    III（优先级 A）：客户重要度 Very High；或医院/隧道/地铁 300 m 内且深 ≥ 0.3 m；或隧道用地且深 ≥ 0.3 m（VIP/主干路走廊）
    II（优先级 B）：客户重要度 High；或 300 m 内有重点单位；或深 ≥ 0.3 m；或隧道/地块出口用地
    I（优先级 C）：其余路面积水，对公众与财产无风险
    单靠水深最多到 II 级：世界模型的格平均水深普遍偏大，III 级要有「地点重要」这一条。"""
    lv = []
    for _, p in pts.iterrows():
        cc = p.get("client_criticality"); tk = p.get("top_kind"); d = float(p.get("max_depth_m") or 0.0); lu = p.get("land_use")
        if cc == "Very High" or (tk in ("hospital", "tunnel", "metro") and d >= 0.3) or (lu == "tunnel" and d >= 0.3):
            lv.append("III")
        elif cc == "High" or (tk not in (None, "none") and isinstance(tk, str)) or d >= 0.3 or lu in ("tunnel", "access_road"):
            lv.append("II")
        else:
            lv.append("I")
    return lv, [{"III": "A", "II": "B", "I": "C"}[x] for x in lv]


def kpis(plan: pd.DataFrame, pts: pd.DataFrame, t_decide: float, trt_min: float = 120.0) -> dict:
    served = set(plan["fp_id"]) if len(plan) else set()
    wfh = 0.0
    for _, p in pts.iterrows():
        if p["fp_id"] in served:
            done = plan.loc[plan["fp_id"] == p["fp_id"], "done_min"].max()
            hours = ((done if pd.notna(done) else p["duration_min"]) - t_decide) / 60.0
        else:
            hours = max(p["duration_min"] - t_decide, 0) / 60.0
        wfh += float(p["priority"]) * max(hours, 0)
    crit = pts[pts["top_kind"].isin(["hospital", "tunnel", "metro"])]
    crit_ok = int(plan[plan["fp_id"].isin(crit["fp_id"]) & plan["within_target"]]["fp_id"].nunique()) if len(plan) else 0
    first = (plan.groupby("fp_id")["arrive_min"].min() - t_decide) if len(plan) else pd.Series(dtype=float)
    lv = pts["level"] if "level" in pts.columns else pd.Series(["I"] * len(pts), index=pts.index)
    served_lv = pts.loc[pts["fp_id"].isin(served), "level"].value_counts().to_dict() if "level" in pts.columns else {}
    return {"weighted_flood_hours": round(float(wfh), 1), "points_served": int(len(served)), "points_total": int(len(pts)),
            "critical_within_target": crit_ok, "critical_total": int(len(crit)),
            "first_arrival_median_min": None if not len(plan) else round(float(first.median()), 0),
            "within_trt": int((first <= trt_min).sum()) if len(plan) else 0, "trt_min": trt_min,
            "points_by_level": {k: int(v) for k, v in lv.value_counts().items()}, "served_by_level": {k: int(v) for k, v in served_lv.items()},
            "units_used": int(plan.loc[~plan["model"].astype(str).str.startswith("tanker"), "unit_id"].nunique()) if len(plan) else 0,
            "tankers_used": int(plan.loc[plan["model"].astype(str).str.startswith("tanker"), "unit_id"].nunique()) if len(plan) else 0,
            "points_by_tanker": int(plan.loc[plan["model"].astype(str).str.startswith("tanker"), "fp_id"].nunique()) if len(plan) else 0}


# ---------------------------------------------------------------- 预置（最大覆盖选址）
def preposition(pts: pd.DataFrame, sites: pd.DataFrame, travel_site_pt: np.ndarray, units: pd.DataFrame, alert: str, cfg: dict) -> dict:
    D = cfg["dispatch"]
    qthr = float(D["preposition_priority_quantile"][alert]); max_sites = int(D["preposition_max_sites"][alert])
    sel = pts[pts["priority"] >= pts["priority"].quantile(qthr)] if len(pts) else pts
    if not len(sel):
        return {"alert": alert, "sites": [], "covered": 0, "points": 0}
    P = len(sel); S = len(sites)
    cover = travel_site_pt[:, sel.index.values] <= D["preposition_cover_min"]
    w = sel["priority"].values.astype(float)
    # 变量：y_s (S), x_ps (S*P)；max Σ w_p x_ps；x_ps ≤ y_s；Σ_s x_ps ≤ 1；Σ y_s ≤ max_sites；x_ps=0 若不覆盖
    n = S + S * P
    c = np.zeros(n); c[S:] = -np.repeat(w[None, :], S, axis=0).ravel()
    from scipy.sparse import coo_matrix
    rows, cols, vals, lo, hi = [], [], [], [], []
    ri = 0
    for s in range(S):
        for p in range(P):
            j = S + s * P + p
            rows += [ri, ri]; cols += [j, s]; vals += [1.0, -1.0]; lo.append(-np.inf); hi.append(0.0); ri += 1
    for p in range(P):
        js = [S + s * P + p for s in range(S)]
        rows += [ri] * S; cols += js; vals += [1.0] * S; lo.append(-np.inf); hi.append(1.0); ri += 1
    rows += [ri] * S; cols += list(range(S)); vals += [1.0] * S; lo.append(-np.inf); hi.append(float(max_sites)); ri += 1
    A = coo_matrix((vals, (rows, cols)), shape=(ri, n)).tocsr()
    ub = np.ones(n)
    for s in range(S):
        for p in range(P):
            if not cover[s, p]:
                ub[S + s * P + p] = 0.0
    t0 = time.perf_counter()
    res = milp(c, constraints=LinearConstraint(A, lo, hi), integrality=np.ones(n), bounds=Bounds(np.zeros(n), ub), options={"time_limit": 20})
    ms = round((time.perf_counter() - t0) * 1000, 1)
    if res.x is None:
        return {"alert": alert, "sites": [], "covered": 0, "points": int(P), "status": res.message}
    y = np.round(res.x[:S]).astype(int); x = np.round(res.x[S:]).astype(int).reshape(S, P)
    out_sites = []
    total_rate = float(units["rate_m3h"].sum())
    demand = sel["required_rate_m3h"].values.astype(float)
    for s in np.where(y == 1)[0]:
        covered = np.where(x[s] == 1)[0]
        need = float(demand[covered].sum())
        out_sites.append({"site_id": sites.loc[s, "site_id"], "name": sites.loc[s, "name"], "lon": float(sites.loc[s, "lon"]), "lat": float(sites.loc[s, "lat"]),
                          "covers": sel.iloc[covered]["fp_id"].tolist(), "covered_priority": round(float(w[covered].sum()), 1), "required_rate_m3h": round(need, 0)})
    tot_need = sum(s["required_rate_m3h"] for s in out_sites) or 1.0
    for s in out_sites:                       # 泵按需求比例分到驻地，且至少一台
        s["units_suggested"] = max(1, int(round(len(units) * s["required_rate_m3h"] / tot_need)))
    ML = D.get("manual_levels", {}); trt = float(ML.get("trt_min", 120))
    chosen = np.where(y == 1)[0]
    covered_trt = int((travel_site_pt[chosen][:, sel.index.values] <= trt).any(axis=0).sum()) if len(chosen) else 0
    return {"alert": alert, "alert_en": ALERT_EN[alert], "manual": ML.get(alert), "trt_min": trt, "covered_trt": covered_trt,
            "priority_quantile": qthr, "cover_min": D["preposition_cover_min"], "points": int(P),
            "covered": int(x.sum()), "covered_priority_share": round(float((x.sum(axis=0) > 0) @ w / w.sum()), 3) if w.sum() else 0.0,
            "sites": out_sites, "solver_ms": ms, "status": "optimal" if res.success else res.message}


def preposition_value(pre: dict, pts: pd.DataFrame, units: pd.DataFrame, sites: pd.DataFrame, travel_site_pt: np.ndarray, cfg: dict,
                      plan_after: pd.DataFrame, summary_after: dict | None, models: dict | None = None, cost_after: dict | None = None) -> dict | None:
    """把预置方案变成一张「泵从预置驻地出发」的调度单并重演，与无泵、事后调度（从原驻地、决策时刻出发）三者比。
    出发时刻 = 事件开始 + preposition_depart_min（手册动员 30 分钟）；每处驻地按建议台数从泵池取泵，按点优先级分给它覆盖的点，
    每点最多 max_units_per_point 台，先满足「峰值水量 ÷ 客户分级目标时限」的排量需求。"""
    D = cfg["dispatch"]
    if not pre or not pre.get("sites"):
        return None
    depart = float(D.get("preposition_depart_min", 30)); cap = int(D.get("max_units_per_point", 3))
    pool = units.sort_values("rate_m3h", ascending=False).reset_index(drop=True)
    site_ix = {s: i for i, s in enumerate(sites["site_id"])}
    pt_ix = {f: i for i, f in enumerate(pts["fp_id"])}
    rows = []; k = 0
    for s in pre["sites"]:
        n_units = int(s.get("units_suggested", 1)); si = site_ix.get(s["site_id"])
        if si is None:
            continue
        got = pool.iloc[k:k + n_units]; k += n_units
        if not len(got):
            break
        cov = [f for f in s["covers"] if f in pt_ix]
        sub = pts.loc[[pt_ix[f] for f in cov]].copy()
        sub["need_units"] = np.ceil((sub["volume_m3"].astype(float) / np.maximum(sub["target_drain_min"].astype(float) / 60.0, 0.5)) / max(float(got["rate_m3h"].mean()), 1.0)).clip(1, cap).astype(int)
        sub = sub.sort_values("priority", ascending=False)
        alloc = {f: 0 for f in cov}; ui = 0
        for _, p in sub.iterrows():                       # 第一轮：按需求
            while alloc[p["fp_id"]] < int(p["need_units"]) and ui < len(got):
                alloc[p["fp_id"]] += 1; ui += 1
        for _, p in sub.iterrows():                       # 第二轮：多余的泵给高优先级点补到上限
            while ui < len(got) and alloc[p["fp_id"]] < cap:
                alloc[p["fp_id"]] += 1; ui += 1
        ui = 0
        for _, p in sub.iterrows():
            for _ in range(alloc[p["fp_id"]]):
                u = got.iloc[ui]; ui += 1
                trav = float(travel_site_pt[si, pt_ix[p["fp_id"]]])
                if not np.isfinite(trav):
                    continue
                arrive = depart + trav
                rf_ = float(p.get("relay_factor", 1.0) or 1.0)
                rows.append({"fp_id": p["fp_id"], "unit_id": u["unit_id"], "model": u["model"], "rate_m3h": round(float(u["rate_m3h"]) * rf_, 1), "rate_nominal_m3h": float(u["rate_m3h"]), "relay_factor": round(rf_, 3), "from": s["site_id"], "site": s["name"],
                             "travel_min": round(trav, 1), "arrive_min": round(arrive, 0), "pumping_from_min": round(arrive + float(u.get("setup_min", 20)) * max(int(p.get("relay_stages", 0) or 0), 1), 0),
                             "target_drain_min": float(p["target_drain_min"]), "priority": float(p["priority"]), "level": p.get("level"), "duty_aed_h": u.get("duty_aed_h")})
    plan_pre = pd.DataFrame(rows)
    if not len(plan_pre):
        return None
    return value_from_plan(plan_pre, pts, plan_after, summary_after, depart, cfg=cfg, models=models, cost_after=cost_after)


def plan_cost(plan: pd.DataFrame, rep_rows: list[dict], depart_min: float, cfg: dict, models: dict | None = None) -> dict:
    """一张调度单的指示性成本：每台泵 台时 × 小时单价 + 每台动员费。
    小时单价用客户预案 A 表给的 duty_aed_h（12 寸 90 / 8 寸 60 / 6 寸 36 AED/h；行里有就用行里的，否则按型号查，再否则 default_duty_aed_h）；
    台时从出发（调度单有 depart_min 列用它，否则用 depart_min 参数）算到该点重演退干时刻，48 h 内退不干按 horizon_min；
    最短计费 min_duty_h 与每台动员费 mobilisation_aed_per_unit 是假设（[dispatch.cost]）。"""
    C = (cfg.get("dispatch") or {}).get("cost") or {}
    mob = float(C.get("mobilisation_aed_per_unit", 0.0)); min_h = float(C.get("min_duty_h", 0.0)); horizon = float(C.get("horizon_min", replay.T_MAX_MIN)); dflt = float(C.get("default_duty_aed_h", 60.0))
    models = models or {}
    base = {"mobilisation_aed_per_unit": mob, "min_duty_h": min_h, "horizon_min": horizon, "basis": "台时 × 客户预案 A 表小时单价 + 动员费（v1 口径，不含人工 / 燃油 / 车公里 / 跨区工单；求解器优化的是 v2.cost，同一方案要大三成）",
            "basis_en": "pump-hours × client Plan A hourly rates + mobilisation (v1 basis; excludes crew / fuel / vehicle-km / cross-zone work orders — the solver optimises v2.cost, ~30% higher for the same plan)"}
    if plan is None or not len(plan):
        return {"units": 0, "pump_hours": 0.0, "duty_aed": 0.0, "mobilisation_aed": 0.0, "total_aed": 0.0, "by_model": {}, **base}
    end_by = {str(r["fp_id"]): r.get("replay_pump_end_min") for r in (rep_rows or [])}
    hours_total = duty = 0.0; by_model = {}
    for _, u in plan.iterrows():
        rate = u.get("duty_aed_h") if "duty_aed_h" in plan.columns else None
        if rate is None or (isinstance(rate, float) and np.isnan(rate)):
            rate = (models.get(str(u.get("model")), {}) or {}).get("duty_aed_h", dflt)
        t_dep = float(u["depart_min"]) if ("depart_min" in plan.columns and pd.notna(u.get("depart_min"))) else float(depart_min)
        t_end = end_by.get(str(u["fp_id"]))
        t_end = float(t_end) if (t_end is not None and not (isinstance(t_end, float) and np.isnan(t_end))) else horizon
        h = max(min_h, (max(t_end, t_dep) - t_dep) / 60.0)
        hours_total += h; duty += h * float(rate)
        m = str(u.get("model")); bm = by_model.setdefault(m, {"units": 0, "hours": 0.0, "aed": 0.0}); bm["units"] += 1; bm["hours"] += h; bm["aed"] += h * float(rate)
    n = int(len(plan))
    return {"units": n, "pump_hours": round(hours_total, 1), "duty_aed": round(duty, 0), "mobilisation_aed": round(mob * n, 0), "total_aed": round(duty + mob * n, 0),
            "by_model": {k: {"units": v["units"], "hours": round(v["hours"], 1), "aed": round(v["aed"], 0)} for k, v in by_model.items()}, **base}


def value_from_plan(plan_pre: pd.DataFrame, pts: pd.DataFrame, plan_after: pd.DataFrame, summary_after: dict | None, depart: float,
                    cfg: dict | None = None, models: dict | None = None, cost_after: dict | None = None) -> dict:
    """任何一张「预置/预案调度单」→ 重演 → 与无泵、事后调度比的统一指标。"""
    rep_rows, s_pre = replay.replay_plan(pts, plan_pre)
    tgt = dict(zip(pts["fp_id"], pts["target_drain_min"].astype(float)))
    # 达标口径必须和 plan_from_assignment（dispatch.py:263）、dispatch2、recession 一致：
    # 从出动时刻起算 target + 60 分钟。replay_pump_end_min 是事件开始起算的绝对时刻，要先减去出动时刻
    # （2026-09-23 修：原来直接拿绝对时刻和相对时限比，决策时刻 210 min 时两套口径差 270 min）。
    GRACE_MIN = 60.0
    def within(rows_, depart_min: float):
        n = 0
        for r in rows_:
            if r.get("within_target") is not None:                  # 调度单行上已经按正确口径算过，直接用
                n += int(bool(r["within_target"])); continue
            te = r.get("replay_pump_end_min")
            if te is not None and float(te) - float(depart_min) <= tgt.get(r["fp_id"], 1e9) + GRACE_MIN:
                n += 1
        return n
    after_rows = plan_after.drop_duplicates("fp_id").to_dict(orient="records") if len(plan_after) and "replay_pump_end_min" in plan_after.columns else []
    depart_after = float(plan_after["depart_min"].min()) if (len(plan_after) and "depart_min" in plan_after.columns) else float(depart)
    lv = pts.set_index("fp_id")["level"].to_dict() if "level" in pts.columns else {}
    n_iii_pre = int(sum(1 for r in rep_rows if lv.get(r["fp_id"]) == "III")); n_iii_after = int(sum(1 for r in after_rows if lv.get(r["fp_id"]) == "III"))
    is_tk = plan_pre["model"].astype(str).str.startswith("tanker") if "model" in plan_pre.columns else pd.Series(False, index=plan_pre.index)
    return {"depart_min": depart, "units_placed": int(len(plan_pre)), "pumps_placed": int((~is_tk).sum()), "tankers_placed": int(is_tk.sum()), "sites": int(plan_pre["from"].nunique()), "points_with_pumps": int(plan_pre["fp_id"].nunique()),
            "arrive_median_min": round(float(plan_pre.groupby("fp_id")["pumping_from_min"].max().median()), 0),
            "after_arrive_median_min": (round(float(plan_after.groupby("fp_id")["pumping_from_min"].max().median()), 0) if len(plan_after) else None),
            "flood_h": {"base": s_pre["all_flood_h_base"], "after": (summary_after or {}).get("all_flood_h_pump"), "pre": s_pre["all_flood_h_pump"]},
            "vol_hours": {"base": s_pre["all_vol_hours_base"], "after": (summary_after or {}).get("all_vol_hours_pump"), "pre": s_pre["all_vol_hours_pump"]},
            "saved_vs_after_pct": (round((1 - s_pre["all_flood_h_pump"] / (summary_after or {}).get("all_flood_h_pump")) * 100, 1)
                                   if (summary_after or {}).get("all_flood_h_pump") else None),
            "saved_vs_base_pct": s_pre["all_saved_pct"], "after_saved_vs_base_pct": (summary_after or {}).get("all_saved_pct"),
            "within_target": {"pre": within(rep_rows, depart), "pre_n": len(rep_rows), "after": within(after_rows, depart_after), "after_n": len(after_rows)},
            "level_iii_served": {"pre": n_iii_pre, "after": n_iii_after},
            "t_empty_median_min": {"pre": s_pre["served_t_empty_median_pump_min"], "after": (summary_after or {}).get("served_t_empty_median_pump_min")},
            "pumped_total_m3": s_pre["pumped_total_m3"],
            # 只看该预案覆盖到的点（Parsons 这类只管几个汇水区的预案，全网口径会被稀释）
            "served_flood_h": {"base": s_pre["served_flood_h_base"], "pre": s_pre["served_flood_h_pump"], "saved_pct": s_pre["served_saved_pct"], "n": s_pre["served_points"]},
            # 指示性成本：本预案 vs 事后调度（台时 × 客户预案 A 表小时单价 + 动员费）
            "cost": plan_cost(plan_pre, rep_rows, depart, cfg or {}, models), "cost_after": cost_after,
            "plan": plan_pre.to_dict(orient="records")}


def external_plans(ctx: dict, pts: pd.DataFrame, sites: pd.DataFrame, T_sites: np.ndarray, cfg: dict, plan_after: pd.DataFrame, summary_after: dict | None, cost_after: dict | None = None) -> dict:
    """[dispatch.plans] 里的外部预案变成调度单并重演，与本系统预置并排：
    kind=onsite：客户预案 A 的泵就放在各自热点（事件开始 depart_min 后开抽）；
    kind=staging：按热点区域给定台数（如 Parsons 2027 临时方案），泵从离该区域积水点最近的候选驻地出发。"""
    D = cfg["dispatch"]; plans_cfg = D.get("plans") or {}
    if not plans_cfg or not len(pts):
        return {}
    models = {m["model"]: m for m in ctx["fleet"]["models"]}
    hs = ctx.get("hotspots")
    area_of, planA = {}, {}
    if hs is not None and len(hs):
        h = hs.set_index(hs["hotspot_id"].astype(str))
        area_of = h["area"].to_dict()
        for c in ("planA_pump12", "planA_pump8", "planA_pump6", "planA_pumps", "planA_tankers"):
            planA[c] = pd.to_numeric(h[c], errors="coerce").fillna(0).to_dict() if c in h.columns else {}
    pid = pts["fp_id"].astype(str).where(pts["fp_id"].astype(str).isin(area_of), pts.get("client_hotspot_id", pd.Series([None] * len(pts))).astype(str))
    pts = pts.copy(); pts["_hid"] = pid.values; pts["_area"] = pts["_hid"].map(area_of)
    out = {}
    for key, P in plans_cfg.items():
        depart = float(P.get("depart_min", D.get("preposition_depart_min", 30)))
        meta = {"label": P.get("label", key), "label_en": P.get("label_en", key), "source": P.get("source"), "source_en": P.get("source_en"), "kind": P.get("kind"), "depart_min": depart}
        try:
            if P.get("kind") == "onsite":
                rows = []
                sizes = (("planA_pump12", "diesel-12in"), ("planA_pump8", "diesel-8in"), ("planA_pump6", "diesel-6in"))
                for _, p in pts.iterrows():
                    hid = p["_hid"]; n_sized = 0
                    for col, model in sizes:
                        n = int(planA.get(col, {}).get(hid, 0) or 0); n_sized += n
                        for k in range(n):
                            mm = models.get(model) or {}
                            rows.append({"fp_id": p["fp_id"], "unit_id": f"A-{hid}-{model[-4:]}-{k+1}", "model": model, "rate_m3h": float(mm.get("rate_m3h", 180)), "from": str(hid), "site": "on site",
                                         "travel_min": 0.0, "arrive_min": depart, "pumping_from_min": depart + float(mm.get("setup_min", 20)), "target_drain_min": float(p["target_drain_min"]), "priority": float(p["priority"]), "level": p.get("level"), "duty_aed_h": mm.get("duty_aed_h")})
                    ntk = int(planA.get("planA_tankers", {}).get(hid, 0) or 0)                 # 客户预案 A 的罐车：就地，按该点到最近排放点的运距算有效排量
                    if ntk > 0 and tanker_params(cfg)["enabled"]:
                        rate_tk, cyc = tanker_rate_m3h(cfg, p.get("discharge_dist_m")); TP = tanker_params(cfg)
                        for k in range(ntk):
                            rows.append({"fp_id": p["fp_id"], "unit_id": f"A-{hid}-tk-{k+1}", "model": TP["model"], "rate_m3h": round(rate_tk, 1), "from": str(hid), "site": "on site",
                                         "travel_min": 0.0, "arrive_min": depart, "pumping_from_min": depart + TP["load_min"], "target_drain_min": float(p["target_drain_min"]), "priority": float(p["priority"]), "level": p.get("level"), "duty_aed_h": TP["duty_aed_h"]})
                    extra = int(planA.get("planA_pumps", {}).get(hid, 0) or 0) - n_sized     # 只给了总数没给口径的按 6 寸
                    for k in range(max(extra, 0)):
                        mm = models.get("diesel-6in") or {}
                        rows.append({"fp_id": p["fp_id"], "unit_id": f"A-{hid}-6in?-{k+1}", "model": "diesel-6in", "rate_m3h": float(mm.get("rate_m3h", 180)), "from": str(hid), "site": "on site",
                                     "travel_min": 0.0, "arrive_min": depart, "pumping_from_min": depart + float(mm.get("setup_min", 20)), "target_drain_min": float(p["target_drain_min"]), "priority": float(p["priority"]), "level": p.get("level"), "duty_aed_h": mm.get("duty_aed_h")})
                plan_p = pd.DataFrame(rows)
                v = value_from_plan(plan_p, pts, plan_after, summary_after, depart, cfg=cfg, models=models, cost_after=cost_after) if len(plan_p) else None
                meta["pumps_total"] = int((~plan_p["model"].astype(str).str.startswith("tanker")).sum()) if len(plan_p) else 0
                meta["tankers_total"] = int(plan_p["model"].astype(str).str.startswith("tanker").sum()) if len(plan_p) else 0
            else:
                model = P.get("model", "diesel-12in"); mm = models.get(model) or {"rate_m3h": 700, "setup_min": 25}
                cats = P.get("catchments") or {}
                pre_sites = []; pool = []; k0 = 0; ponds = 0; pond_m3 = 0.0
                for area, c in cats.items():
                    sub = pts[pts["_area"] == area]
                    n = int(c.get("pumps", 0)); ponds += int(c.get("ponds", 0)); pond_m3 += float(c.get("pond_m3", 0))
                    if not len(sub) or n <= 0:
                        continue
                    idx = sub.index.values
                    Tsub = np.where(np.isfinite(T_sites[:, idx]), T_sites[:, idx], np.nan)
                    pref = P.get("sites_prefix")
                    cand = np.arange(len(sites))
                    if pref:                                     # 只从指定前缀的驻地（如 POND-）出发；同区域的塘优先
                        isp = sites["site_id"].astype(str).str.startswith(str(pref)).values
                        if "area" in sites.columns:
                            same = isp & (sites["area"].astype(str).values == str(area))
                            isp = same if same.any() else isp
                        if isp.any():
                            cand = np.nonzero(isp)[0]
                    if pref and len(cand) > 1:                    # 每个塘管离它最近的点，台数按点数比例分（每塘至少 1 台）
                        owner = cand[np.nanargmin(np.where(np.isfinite(Tsub[cand]), Tsub[cand], 1e9), axis=0)]
                        groups = {}
                        for f_, o in zip(sub["fp_id"].tolist(), owner):
                            groups.setdefault(int(o), []).append(f_)
                        order = sorted(groups, key=lambda g: -len(groups[g]))
                        alloc = {g: 1 for g in order}; left = n - len(order)
                        if left < 0:                              # 泵比塘少：只给点最多的前 n 个塘
                            order = order[:max(n, 0)]; alloc = {g: 1 for g in order}; left = 0
                        tot_pts = sum(len(groups[g]) for g in order) or 1
                        for g in order:
                            alloc[g] += int(left * len(groups[g]) / tot_pts)
                        for g in order:                           # 余数给点多的
                            if sum(alloc.values()) >= n: break
                            alloc[g] += 1
                        for g in order:
                            pre_sites.append({"site_id": sites.loc[g, "site_id"], "name": f"{c.get('name', area)} ← {sites.loc[g, 'name']}", "covers": groups[g], "units_suggested": int(alloc[g]), "area": area})
                    else:
                        med = np.nanmedian(Tsub[cand], axis=1)
                        si = int(cand[np.nanargmin(med)]) if np.isfinite(med).any() else int(cand[0])
                        pre_sites.append({"site_id": sites.loc[si, "site_id"], "name": f"{c.get('name', area)} ← {sites.loc[si, 'name']}", "covers": sub["fp_id"].tolist(), "units_suggested": n, "area": area})
                    for k in range(n):
                        pool.append({"unit_id": f"{key}-{area[:3]}-{k+1}", "model": model, "rate_m3h": float(mm.get("rate_m3h", 700)), "setup_min": float(mm.get("setup_min", 25)), "duty_aed_h": mm.get("duty_aed_h")}); k0 += 1
                meta.update({"pumps_total": sum(int(c.get("pumps", 0)) for c in cats.values()), "pumps_placed_pool": k0, "ponds": ponds, "pond_m3": pond_m3, "catchments": list(cats.keys()),
                             "staging_sites": [{"area": x["area"], "site": x["name"], "site_id": x["site_id"], "pumps": x["units_suggested"], "points": len(x["covers"])} for x in pre_sites],
                             "sites_prefix": P.get("sites_prefix")})
                v = preposition_value({"sites": pre_sites}, pts, pd.DataFrame(pool), sites, T_sites, {**cfg, "dispatch": {**D, "preposition_depart_min": depart}}, plan_after, summary_after, models=models, cost_after=cost_after) if pre_sites else None
            if v is not None:
                v.pop("plan", None)
            out[key] = {**meta, "value": v}
        except Exception as ex:
            out[key] = {**meta, "value": None, "error": str(ex)[:200]}
    return out


# ---------------------------------------------------------------- 总流程
def flood_extent(pts0: pd.DataFrame, cfg: dict) -> tuple[list, list]:
    """积水点 → 用于挡路判定的积水面 + 水深。

    pts0["geom"] 存的是 Point（零面积），直接拿去做 `within` 判定永远匹配不上路网边的中点——
    2026-09-24 实测：374 个点全有几何体，却断开 0 段、减速 0 段，重型与轻型的行程矩阵一模一样，
    「积水路段加罚/断开」这套从来没生效过。

    这里按每个点的积水面积还原成等效圆（r = √(A/π)，下限 [dispatch].flood_extent_min_r_m）。
    真实洼地多边形要读 25 GB 的 DTM（terrain.depression_polygon 是单点按需用的），374 个点
    批量算不现实。经度按纬度缩放，圆在地面上是正圆而不是椭圆。
    """
    D = cfg.get("dispatch") or {}
    if str(D.get("flood_extent_from", "area")).lower() == "point":
        polys = [g for g in pts0["geom"].tolist() if g is not None]
        return polys, [float(d) for d, g in zip(pts0["max_depth_m"], pts0["geom"]) if g is not None]
    rmin = float(D.get("flood_extent_min_r_m", 15.0))
    kx = 111000.0 * np.cos(np.radians(24.4))                      # 1° 经度 ≈ 101 km
    ky = 111000.0
    polys, depths = [], []
    for g, a, dep in zip(pts0["geom"], pts0.get("area_m2", pd.Series([np.nan] * len(pts0))), pts0["max_depth_m"]):
        if g is None:
            continue
        if g.geom_type != "Point":
            # 已经是面（simulate.py 的模拟积水区、世界模型给的淹没多边形）：它本身就是积水范围，直接用。
            # 2026-10-01 修：原来一律当点取 g.x，换机器没有世界模型输出、走模拟积水区时 run 在这里崩
            polys.append(g); depths.append(float(dep)); continue
        try:
            r = max(rmin, float(np.sqrt(float(a) / np.pi))) if a is not None and np.isfinite(float(a)) else rmin
        except (TypeError, ValueError):
            r = rmin
        # 在「经度按纬度缩放」的平面里做正圆，再变回经纬度：避免直接按度缓冲得到东西向拉长的椭圆
        circ = sh_affine.scale(g.buffer(r / ky), xfact=ky / kx, yfact=1.0, origin=(g.x, g.y))
        polys.append(circ); depths.append(float(dep))
    return polys, depths


def build_context(fp: dict, fleet: dict, facilities: pd.DataFrame, cs: pd.DataFrame, road: tr.RoadNetwork | None, cfg: dict, hotspots: pd.DataFrame | None = None) -> dict:
    """一次性：优先级、行程矩阵（驻地→点、点→点、候选驻地→点）。与决策时刻无关的部分都在这里。"""
    D = cfg["dispatch"]
    pts0 = priority(points_frame(fp), facilities, cfg, hotspots)
    models = {m["model"]: m for m in fleet["models"]}
    units = pd.DataFrame([{**u, **{k: v for k, v in models[u["model"]].items() if k != "model"}, "from": u.get("depot_id"), "avail_min": 0.0} for u in fleet["units"] if u.get("status", "available") == "available"])
    depots = pd.DataFrame(fleet["depots"])
    polys, depths = flood_extent(pts0, cfg)
    src_d = depots[["lon", "lat"]].values.astype(float); dst = pts0[["lon", "lat"]].values.astype(float)
    cd = facilities[facilities["kind"] == "civil_defense"].dropna(subset=["lon", "lat"])
    sites = pd.concat([depots.rename(columns={"depot_id": "site_id"})[["site_id", "name", "lon", "lat"]],
                       pd.DataFrame({"site_id": ["CD%03d" % i for i in range(len(cd))], "name": [n if isinstance(n, str) and n.strip() else f"Civil Defence Centre #{i+1}" for i, n in enumerate(cd["name"])],
                                     "lon": cd["lon"].values, "lat": cd["lat"].values})]).drop_duplicates(subset=["lon", "lat"]).reset_index(drop=True)
    # 额外候选驻地（如 DMT 模型里的临时塘 out/dmt_model/ponds_sites.json：Parsons 方案的泵放在塘边）
    extra = D.get("extra_sites_file")
    if extra:
        ep = Path(extra) if Path(extra).is_absolute() else Path(__file__).resolve().parents[1] / extra
        if ep.exists():
            try:
                ex = pd.DataFrame(json.loads(ep.read_text(encoding="utf-8")))
                if len(ex):
                    keep = ["site_id", "name", "lon", "lat"] + (["area"] if "area" in ex.columns else [])
                    sites = pd.concat([sites, ex[keep]], ignore_index=True).drop_duplicates(subset=["lon", "lat"]).reset_index(drop=True)
            except Exception:
                pass
    t0 = time.perf_counter()
    # 限重桥 / 净高不足：重型车（泵 + 车）过不去的桥附近路段在重型矩阵里断开（[dispatch.bridges]，无台账时为模拟条目）
    B = dict(D.get("bridges") or {})
    heavy_w = float(B.get("heavy_vehicle_t") or (max([float(m.get("weight_t") or 0) for m in fleet["models"] if m.get("vehicle_class") == "heavy"] or [8.0]) + float(B.get("vehicle_tare_t", 4.0))))
    try:                                                                # 真实台账（exchange/bridges.json，契约 swbrain.bridges/1，拉或推）到了就用它、不再标模拟
        from . import feeds as _feeds
        bf = _feeds.load(cfg, "bridges")
        if bf.get("rows"):
            B["items"] = [r for r in bf["rows"] if r.get("lon") is not None and r.get("lat") is not None]; B["simulated"] = False; B["source"] = bf.get("source"); B["as_of"] = bf.get("as_of")
    except Exception:
        pass
    blocked = [(float(b["lon"]), float(b["lat"])) for b in (B.get("items") or []) if float(b.get("limit_t") or 1e9) < heavy_w or float(b.get("clearance_m") or 9.9) < float(B.get("heavy_height_m", 3.8))]
    if road is not None:
        T_heavy_d = road.travel_minutes(src_d, dst, polys, depths, impassable_depth_m=D["impassable_depth_m"]["heavy"], slow_factor=D["slow_factor"], blocked_points=blocked, blocked_radius_m=float(B.get("radius_m", 40)))
        T_light_d = road.travel_minutes(src_d, dst, polys, depths, impassable_depth_m=D["impassable_depth_m"]["light"], slow_factor=D["slow_factor"])
        T_pp = road.travel_minutes(dst, dst, polys, depths, impassable_depth_m=D["impassable_depth_m"]["heavy"], slow_factor=D["slow_factor"], blocked_points=blocked, blocked_radius_m=float(B.get("radius_m", 40)))
        T_sites = road.travel_minutes(sites[["lon", "lat"]].values.astype(float), dst)
        travel_method = "路网 Dijkstra（积水路段加罚/断开）"
    else:
        T_heavy_d = T_light_d = tr.haversine_minutes(src_d, dst); T_pp = tr.haversine_minutes(dst, dst); T_sites = tr.haversine_minutes(sites[["lon", "lat"]].values.astype(float), dst); travel_method = "直线距离兜底"
    return {"fp": fp, "fleet": fleet, "pts0": pts0, "units": units, "depots": depots, "sites": sites, "T_heavy_d": T_heavy_d, "T_light_d": T_light_d, "T_pp": T_pp, "T_sites": T_sites, "hotspots": hotspots,
            "travel_method": travel_method, "travel_ms": round((time.perf_counter() - t0) * 1000), "cs": cs,
            "bridges": {"simulated": bool(B.get("simulated", True)), "heavy_vehicle_t": heavy_w, "blocked": len(blocked), "items": list(B.get("items") or []), "source": B.get("source"), "as_of": B.get("as_of")}}


def run_dispatch(fp: dict, fleet: dict, facilities: pd.DataFrame, cs: pd.DataFrame, road: tr.RoadNetwork | None, cfg: dict, decision_time_min: float | None = None, hotspots: pd.DataFrame | None = None) -> dict:
    ctx = build_context(fp, fleet, facilities, cs, road, cfg, hotspots)
    ctx["hotspot_stats"] = hsm.stats(hotspots, cs, fp, cfg) if hotspots is not None and len(hotspots) else None
    return solve_dispatch(ctx, cfg, decision_time_min)


def default_decision_time(fp: dict, cfg: dict) -> float:
    """决策时刻缺省值：预测侧给了 suggested_decision_time_min 就用它（世界模型的积水在数小时后才成形），否则用配置。"""
    s = (fp.get("scenario") or {}).get("suggested_decision_time_min")
    return float(s) if s else float(cfg["dispatch"]["decision_time_min"])


def solve_dispatch(ctx: dict, cfg: dict, decision_time_min: float | None = None, objective: str | None = None, budget_aed: float | None = None, focus_fp: str | None = None, with_pareto: bool = True, robust: dict | None = None) -> dict:
    D = cfg["dispatch"]
    fp, fleet, cs = ctx["fp"], ctx["fleet"], ctx["cs"]
    t_decide = float(decision_time_min if decision_time_min is not None else default_decision_time(fp, cfg))
    pts = feasibility(ctx["pts0"], cs, fleet, cfg, t_decide)
    pts["level"], pts["level_priority"] = emergency_level(pts)
    ML = D.get("manual_levels", {}); trt = float(ML.get("trt_min", 120))
    units, depots, sites = ctx["units"].copy(), ctx["depots"], ctx["sites"]
    T_heavy_d, T_light_d, T_pp, T_sites = ctx["T_heavy_d"], ctx["T_light_d"], ctx["T_pp"], ctx["T_sites"]
    travel_method, travel_ms = ctx["travel_method"], ctx["travel_ms"]
    models = {m["model"]: m for m in fleet["models"]}
    dst = pts[["lon", "lat"]].values.astype(float)
    units = pd.DataFrame([{**u, **{k: v for k, v in models[u["model"]].items() if k != "model"}, "from": u.get("depot_id"), "avail_min": 0.0} for u in fleet["units"] if u.get("status", "available") == "available"])
    depot_ix = {d: i for i, d in enumerate(depots["depot_id"])}
    v2 = None
    cand = pts[pts["dispatchable"]].copy()                 # 可派点（贪心基线与 v1 / v2 共用）
    if str(D.get("solver", "v1")).lower() == "v2":
        # v2：处置包 + 资源（泵 / 车 / 人 / 物资 / 罐车）+ 滚动时间轴 + 费用进目标（swbrain/dispatch2.py）
        from . import dispatch2
        ob = objective or str((D.get("v2") or {}).get("default_objective", "balanced"))
        eng = dispatch2.solve(ctx, cfg, t_decide, ob, budget_aed, focus_fp, with_pareto=with_pareto, robust=robust)
        plan = eng["plan"]; waves_meta = eng["windows"]; tankers_meta = eng["tankers"]
        v2 = {k: v for k, v in eng.items() if k not in ("plan", "pts")}
    else:
        # ---- 多波次分派 ----
        plan_rows, waves_meta = [], []
        avail = units.copy(); avail["at_pt"] = -1
        remaining = cand.copy()
        for wave in range(1, int(D["waves"]) + 1):
            if not len(remaining) or not len(avail):
                break
            rem = remaining.reset_index(drop=True); av = avail.reset_index(drop=True)
            M = np.zeros((len(av), len(rem)))
            for i, (_, u) in enumerate(av.iterrows()):
                row = (T_heavy_d if u["vehicle_class"] == "heavy" else T_light_d)[depot_ix[u["from"]]] if u["at_pt"] < 0 else T_pp[int(u["at_pt"])]
                M[i] = row[rem.index.map(lambda j: pts.index[pts["fp_id"] == rem.loc[j, "fp_id"]][0])]
            assign, meta = assign_milp(rem, av, M, cfg)
            waves_meta.append({"wave": wave, **meta, "units_available": int(len(av)), "points_remaining": int(len(rem))})
            if not len(assign):
                break
            plan = plan_from_assignment(assign, rem, av, t_decide, wave)
            plan_rows.append(plan)
            # 完成的泵在该点变为可用，可用时刻 = done − t_decide；未完成（排不干）的泵留在原地
            served_ids = set(plan["fp_id"])
            done_map = plan.groupby("unit_id")["done_min"].max()
            new_avail = []
            for _, u in av.iterrows():
                if u["unit_id"] in done_map.index and pd.notna(done_map[u["unit_id"]]):
                    pt_id = plan.loc[plan["unit_id"] == u["unit_id"], "fp_id"].iat[0]
                    pi = int(pts.index[pts["fp_id"] == pt_id][0])
                    uu = u.copy(); uu["avail_min"] = float(done_map[u["unit_id"]] - t_decide); uu["at_pt"] = pi; uu["from"] = pt_id; uu["lon"] = pts.loc[pi, "lon"]; uu["lat"] = pts.loc[pi, "lat"]
                    new_avail.append(uu)
                elif u["unit_id"] not in set(plan["unit_id"]):
                    new_avail.append(u)
            avail = pd.DataFrame(new_avail) if new_avail else pd.DataFrame(columns=av.columns)
            remaining = remaining[~remaining["fp_id"].isin(served_ids)]
        plan = pd.concat(plan_rows, ignore_index=True) if plan_rows else pd.DataFrame()
        # ---- 罐车：无排放去处的点靠拉走；池 = fleet.tankers.n 或客户预案 A 的 ADM 罐车总数 ----
        n_tk = int(((fleet.get("tankers") or {}).get("n")) or 0)
        if not n_tk and ctx.get("hotspots") is not None and "planA_tankers" in ctx["hotspots"].columns:
            hs_ = ctx["hotspots"]; n_tk = int(pd.to_numeric(hs_.loc[hs_["region"] == "ADM", "planA_tankers"], errors="coerce").fillna(0).sum())
        tk_plan = assign_tankers(pts, plan, n_tk, T_heavy_d, depots, cfg, t_decide)
        if len(tk_plan):
            plan = pd.concat([plan, tk_plan], ignore_index=True) if len(plan) else tk_plan
        tankers_meta = {"pool": n_tk, "used": int(len(tk_plan)), "points": (int(tk_plan["fp_id"].nunique()) if len(tk_plan) else 0), **{k: v for k, v in tanker_params(cfg).items() if k != "model"}}
    # ---- 贪心基线（单波次）----
    rem0 = cand.reset_index(drop=True); av0 = units.reset_index(drop=True)
    M0 = np.zeros((len(av0), len(rem0)))
    for i, (_, u) in enumerate(av0.iterrows()):
        M0[i] = (T_heavy_d if u["vehicle_class"] == "heavy" else T_light_d)[depot_ix[u["from"]]][[int(pts.index[pts["fp_id"] == f][0]) for f in rem0["fp_id"]]]
    g = greedy_assign(rem0, av0, M0, cfg)
    plan_g = plan_from_assignment(g, rem0, av0, t_decide, 1)
    k_solver = kpis(plan, pts, t_decide, trt); k_greedy = kpis(plan_g, pts, t_decide, trt)
    # ---- 退水重演：按调度单给每个处置点重算水量平衡，无泵 vs 有泵 ----
    rep_rows, rep_summary = replay.replay_plan(pts, plan, timeline_dt=float(D.get("timeline_dt_min", 15)))
    timeline = rep_summary.pop("timeline", None)                 # 全部积水点在统一时间轴上的无泵/有泵水量（页面地图时间轴）
    cost_after = plan_cost(plan, rep_rows, t_decide, cfg, models)   # 事后调度的指示性成本
    if len(plan) and rep_rows:
        plan = plan.merge(pd.DataFrame(rep_rows), on="fp_id", how="left")
    lvc = pts["level"].value_counts().to_dict()
    levels = {"points": {k: int(lvc.get(k, 0)) for k in ("III", "II", "I")}, "event": max(lvc, key=lambda k: LEVEL_ORDER[k]) if lvc else None,
              "trt_min": trt, "mobilization_min": float(ML.get("mobilization_min", 30)), "manual": {a: ML.get(a) for a in ALERTS}, "basis": "WA-726-3 表 11-1 / 附录 H-1"}
    # ---- 预置：候选驻地 = 泵队驻地 + 全部民防站，按四个预警等级 ----
    pre = {a: preposition(pts.reset_index(drop=True), sites, T_sites, units, a, cfg) for a in ALERTS}
    for a in ALERTS:                                        # 预置的价值：预置驻地出发的调度单重演，与事后调度比
        try:
            pre[a]["value"] = preposition_value(pre[a], pts.reset_index(drop=True), units, sites, T_sites, cfg, plan, rep_summary, models=models, cost_after=cost_after)
        except Exception as ex:
            pre[a]["value"] = {"error": str(ex)[:200]}
    plans = external_plans(ctx, pts.reset_index(drop=True), sites, T_sites, cfg, plan, rep_summary, cost_after=cost_after)   # 客户预案 A 就地 / Parsons 2027 等外部预案并排
    scenario = dict(fp["scenario"])
    scenario.setdefault("name_en", i18n.scenario_name_en(scenario.get("name", "")))
    triage_counts = pts["triage"].value_counts().to_dict()
    fm = fp.get("model") or {}
    flood_source = {"name": fm.get("name", "?"), "name_en": fm.get("name_en"), "type": fm.get("type", "?"), "version": fm.get("version"), "simulated": bool(fp.get("simulated")), "stats": fp.get("stats"),
                    "known_behaviour": fm.get("known_behaviour"), "known_behaviour_en": fm.get("known_behaviour_en"), "claim_boundary": fm.get("claim_boundary"),
                    "aggregation": fp.get("aggregation"), "gwm_metrics": fp.get("gwm_metrics")}
    return {"scenario": scenario, "flood_source": flood_source, "levels": levels, "simulated": bool(fp.get("simulated")), "fleet_simulated": bool(fleet.get("simulated")), "fleet_source": fleet.get("derived_from"),
            "hotspot_stats": ctx.get("hotspot_stats"), "decision_time_min": t_decide,
            "points": pts.drop(columns=["geom"]), "plan": plan, "plan_greedy": plan_g, "kpis": {"solver": k_solver, "greedy": k_greedy}, "replay_summary": rep_summary, "plans": plans, "timeline": timeline, "cost": cost_after,
            "waves": waves_meta, "preposition": pre, "depots": depots, "units": units, "travel_method": travel_method, "travel_ms": travel_ms, "tankers": tankers_meta,
            "travel_method_en": i18n.dispatch_en(travel_method), "triage": triage_counts, "triage_en": {k: i18n.dispatch_en(k) for k in triage_counts}, "v2": v2}
