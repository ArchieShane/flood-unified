"""第三道防线优化调度 v2：处置包 + 资源（泵 / 车 / 人 / 物资 / 罐车）+ 时变路网 + 滚动时间轴 + 费用进目标。

设计见 docs/第三道防线优化调度方案设计.md。与 v1（dispatch.assign_milp 的三波）相比：
1. 每个积水点先枚举「处置包」（泵型×台数 / 混合 / 罐车×辆），每个包用退水重演算好退干时刻、积水时长、延误、台时、软管、燃油、费用——把非线性预先算掉；
2. 泵按（驻地, 型号）聚成「单元类」用整数变量分派，规模从 533 台降到几十类；班组人数、车次（载量×趟数）、软管、燃油、罐车池、预算都是线性约束；
3. 目标 = Σ 优先级×积水时长 + α×Σ 优先级×超时限延误 + 费用/V；V（每优先级·小时值多少 AED）三档 = 最快 / 平衡 / 最省，画出帕累托；
4. 每 window_min 滚动一次：抽干的泵在原地释放、入流已经压住的点释放多余泵（抢占）、上一窗口「入流压不住」的点在雨峰后重新进候选；
5. 求解后按驻地排车次与班组（载量、往返、当班 / 召回、12 h 换班），得甘特与资源利用；再交给 replay_plan 校核。
求解器仍是 scipy.optimize.milp（HiGHS），每个窗口一个小 MILP。
"""
from __future__ import annotations
import math
import time

import numpy as np
import pandas as pd
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.sparse import coo_matrix

from . import dispatch as D1, replay, resources as RS

OBJECTIVES = ("fastest", "balanced", "cheapest")
OBJ_ZH = {"fastest": "最快退水", "balanced": "平衡", "cheapest": "最省钱"}
OBJ_EN = {"fastest": "Fastest recession", "balanced": "Balanced", "cheapest": "Lowest cost"}
TANKER_MODEL = "tanker-17m3"
CONTRACTOR_DEFAULTS = {"simulated": True, "mode": "pooled", "work_order_aed": 2000.0, "compare_silo": True}


def contractor_params(cfg: dict) -> dict:
    return {**CONTRACTOR_DEFAULTS, **(((cfg.get("dispatch") or {}).get("contractors") or {}))}


def point_zones(pts0: pd.DataFrame, hotspots) -> dict:
    """fp_id → (region, area)：积水点的客户片区（热点清单的 area）；没有清单时只有市政。"""
    z = {}
    area_by = {}
    if hotspots is not None and len(hotspots) and "area" in getattr(hotspots, "columns", []):
        area_by = dict(zip(hotspots["hotspot_id"].astype(str), hotspots["area"].astype(str)))
    for fp, reg in zip(pts0["fp_id"].astype(str), (pts0["region"] if "region" in pts0.columns else [None] * len(pts0))):
        z[fp] = (str(reg) if reg is not None else str(fp).split("-")[0], area_by.get(fp))
    return z


def zone_ok(c: dict, fp: str, zones: dict) -> bool:
    """驻地（承包商服务区）能不能管这个点：同市政，且 ADM 还要同片区。"""
    reg, area = zones.get(str(fp), (str(fp).split("-")[0], None))
    creg = str(c.get("zone_region") or "")
    if not creg:
        return True
    if creg != reg:
        return False
    cars = c.get("zone_areas") or []
    return (not cars) or reg != "ADM" or (area in cars)


DEFAULTS = {"window_min": 60, "horizon_min": 1440, "max_windows": 8, "value_aed_per_priority_hour": {"fastest": 1000.0, "balanced": 100.0, "cheapest": 10.0},
            "delay_weight": 1.0, "max_travel_min_by_level": {"III": 120, "II": 90, "I": 90}, "max_units_per_point": 3, "package_dt_min": 10,
            "preempt_inflow_share": 0.3, "preempt_remaining_min": 30, "tanker_counts": [2, 4, 6], "milp_time_limit_s": 30, "min_useful_rate_share": 0.5, "arrival_hours_weight": 1.0}


def params(cfg: dict) -> dict:
    P = dict(DEFAULTS); user = ((cfg.get("dispatch") or {}).get("v2") or {})
    for k, v in user.items():
        P[k] = ({**DEFAULTS[k], **v} if isinstance(v, dict) and isinstance(DEFAULTS.get(k), dict) else v)
    return P


def _finite(x) -> bool:
    try:
        return x is not None and np.isfinite(float(x))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- 单元类：同驻地同型号（同可用时刻）的泵 / 罐车合成一类
def build_classes(units: pd.DataFrame, depots: pd.DataFrame, res: dict, ctx: dict, t_now: float) -> list[dict]:
    """units: unit_id/model/from/at_pt/busy_until/vehicle_class/rate_m3h/setup_min/crew/duty_aed_h。返回类列表，每类带对所有积水点的行程行。"""
    T_h, T_l, T_pp = ctx["T_heavy_d"], ctx["T_light_d"], ctx["T_pp"]
    depot_ix = {d: i for i, d in enumerate(depots["depot_id"])}
    from . import traffic as _trf
    sc_d, sc_pp = _trf.scale_at(ctx, t_now)                             # 时变路况：这个窗口出发时刻相对基准窗口的系数比（没有序列 = 1）
    out = []
    if len(units):
        u = units.copy(); u["avail_min"] = np.maximum(u["busy_until"].fillna(t_now).astype(float) - t_now, 0.0)
        u["_slot"] = (u["avail_min"] // 15).astype(int)
        if "contractor" not in u.columns:
            u["contractor"] = None
        for (frm, model, at_pt, slot, contractor), g in u.groupby(["from", "model", "at_pt", "_slot", "contractor"], dropna=False):
            at_pt = int(at_pt)
            if at_pt < 0:
                if frm not in depot_ix:
                    continue
                row = (T_h if str(g["vehicle_class"].iloc[0]) == "heavy" else T_l)[depot_ix[frm]]
                if sc_d is not None:
                    row = np.asarray(row, dtype=float) * float(sc_d[depot_ix[frm]])
            else:
                row = T_pp[at_pt]
                if sc_d is not None:
                    row = np.asarray(row, dtype=float) * float(sc_pp)
            r0 = g.iloc[0]
            out.append({"cid": len(out), "kind": "pump", "depot_id": (frm if at_pt < 0 else None), "from": frm, "at_pt": at_pt, "model": str(model), "count": int(len(g)),
                        "unit_ids": g["unit_id"].tolist(), "rate_m3h": float(r0["rate_m3h"]), "setup_min": float(r0["setup_min"]), "crew": int(r0.get("crew") or 0),
                        "vehicle_class": str(r0.get("vehicle_class") or "light"), "duty_aed_h": float(r0.get("duty_aed_h") or 60.0), "avail_min": float(g["avail_min"].max()),
                        "travel": np.asarray(row, dtype=float), "lon": float(r0["lon"]), "lat": float(r0["lat"]),
                        "contractor": (None if contractor is None or (isinstance(contractor, float) and np.isnan(contractor)) else str(contractor)),
                        "zone_region": r0.get("zone_region"), "zone_areas": list(r0.get("zone_areas") or []) if isinstance(r0.get("zone_areas"), (list, tuple)) else []})
    TP = D1.tanker_params(ctx["cfg"]) if "cfg" in ctx else None
    for _, d in depots.iterrows():
        n_tk = int(((res.get("depots") or {}).get(d["depot_id"]) or {}).get("tankers") or 0) - int(res.get("_tankers_used", {}).get(d["depot_id"], 0))
        if n_tk <= 0 or d["depot_id"] not in depot_ix:
            continue
        rd = (res.get("depots") or {}).get(d["depot_id"]) or {}
        out.append({"cid": len(out), "kind": "tanker", "depot_id": d["depot_id"], "from": d["depot_id"], "at_pt": -1, "model": TANKER_MODEL, "count": n_tk, "unit_ids": [],
                    "rate_m3h": 0.0, "setup_min": float((TP or {}).get("load_min", 10)), "crew": 1, "vehicle_class": "heavy", "duty_aed_h": float((TP or {}).get("duty_aed_h", 250)), "avail_min": 0.0,
                    "travel": np.asarray(T_h[depot_ix[d["depot_id"]]], dtype=float) * (float(sc_d[depot_ix[d["depot_id"]]]) if sc_d is not None else 1.0), "lon": float(d["lon"]), "lat": float(d["lat"]),
                    "contractor": rd.get("contractor"), "zone_region": rd.get("region"), "zone_areas": list(rd.get("areas") or [])})
    return out


# ---------------------------------------------------------------- 处置包：每个点枚举「派什么、派几台」，用重演算好结果
def point_packages(p: int, pt: pd.Series, models: list[dict], classes: list[dict], res: dict, V: float, cfg: dict, P: dict, t_now: float, tanker_ok: bool, alt: list | None = None) -> list[dict]:
    """alt: 鲁棒目标用——[(该点在情景 s 的过程线, 权重 p_s), …]（含当前情景本身）；给了就把每个处置包在各情景上重演，dH / 延误 / 退干时刻取期望。"""
    hyd = pt.get("hydrograph") or []
    if not hyd:
        return []
    horizon = float(t_now + P["horizon_min"]); dt = float(P["package_dt_min"])
    base = replay.replay(hyd, [], dt=dt, t_end=horizon, series=False)
    if base.get("error"):
        return []
    H0 = float(base["kpi"]["base"]["flood_h"])
    te0 = base["kpi"]["base"]["t_empty_min"]
    scen = None
    delay0_by_scen: dict[int, float] = {}
    if alt:
        scen = []
        for h_s, w_s in alt:
            if not h_s:
                scen.append((None, 0.0, float(w_s))); continue                        # 该情景下这个点不积水：处置包在那里没有收益
            b_s = replay.replay(h_s, [], dt=dt, t_end=horizon, series=False)
            if b_s.get("error"):
                scen.append((None, 0.0, float(w_s))); continue
            te0_s = b_s["kpi"]["base"]["t_empty_min"]
            delay0_by_scen[id(h_s)] = max(0.0, ((float(te0_s) - t_now) if te0_s is not None else (horizon - t_now)) - float(pt.get("target_drain_min") or 360.0))
            scen.append((h_s, float(b_s["kpi"]["base"]["flood_h"]), float(w_s)))
        wsum = sum(w for _, _, w in scen) or 1.0
        scen = [(h, H0s, w / wsum) for h, H0s, w in scen]
    rf = float(pt.get("relay_factor") or 1.0); stages = int(pt.get("relay_stages") or 0); relay_needed = bool(pt.get("relay_needed", False))
    target = float(pt.get("target_drain_min") or 360.0); inflow = float(pt.get("inflow_now_m3h") or 0.0)
    # 不派泵时这个点本身就要迟到多久。延误罚只能算增量，否则「派了也追不上」的点会被系统性拒派
    delay0 = max(0.0, ((float(te0) - t_now) if te0 is not None else (horizon - t_now)) - target)
    C = (cfg.get("dispatch") or {}).get("cost") or {}
    mob = float(C.get("mobilisation_aed_per_unit", 500)); min_h = float(C.get("min_duty_h", 4)); rates = res["rates"]; cons = res["consumption"]
    # 每型号的行程估计：能到该点的类里取最近（最终分派后再按实际类重算）
    trav = {}
    for c in classes:
        if c["kind"] != "pump" or c["count"] <= 0:
            continue
        v = c["travel"][p]
        if _finite(v) and (c["model"] not in trav or v + c["avail_min"] < trav[c["model"]][0] + trav[c["model"]][1]):
            trav[c["model"]] = (float(v), float(c["avail_min"]), c)
    out = []

    def add(kind, need, pumps, n_units, crew_n, hours_cap, duty_by_unit, hose_m, fuel_l_h_total, label_zh, label_en):
        r = replay.replay(hyd, pumps, dt=dt, t_end=horizon, series=False)
        kp = r["kpi"]["pump"]; te = kp["t_empty_min"]
        H = float(kp["flood_h"]); dH = H - H0
        delay = max(0.0, ((float(te) - t_now) if te is not None else (horizon - t_now)) - target)
        delay_delta = delay - delay0                                    # ≤ 0：派了之后少迟到多少
        te_used = te; p_within = None
        if scen:                                                        # 鲁棒：同一处置包在各情景过程线上重演，按权重取期望
            H_e = dH_e = delay_e = te_e = p_within = 0.0
            for h_s, H0_s, w_s in scen:
                if h_s is None:
                    te_s, H_s, d_s = t_now, 0.0, 0.0
                else:
                    k_s = replay.replay(h_s, pumps, dt=dt, t_end=horizon, series=False)["kpi"]["pump"]; te_s = k_s["t_empty_min"]; H_s = float(k_s["flood_h"]) - H0_s
                    d_s = max(0.0, ((float(te_s) - t_now) if te_s is not None else (horizon - t_now)) - target)
                d0_s = delay0_by_scen.get(id(h_s), delay0) if h_s is not None else 0.0
                H_e += w_s * (H_s + H0_s); dH_e += w_s * H_s; delay_e += w_s * (d_s - d0_s)
                te_e += w_s * (float(te_s) if te_s is not None else horizon); p_within += w_s * (1.0 if te_s is not None and (float(te_s) - t_now) <= target else 0.0)
            H, dH, delay_delta, te_used = H_e, dH_e, delay_e, te_e
            delay = max(0.0, (float(te_used) - t_now) - target)
        hours = ((float(te_used) if te_used is not None else horizon) - t_now) / 60.0
        hours_bill = max(min_h, hours)
        duty = sum(max(min_h, hours) * d for d in duty_by_unit)
        crew_cost = crew_n * hours_bill * rates["crew_aed_h"]; fuel_l = fuel_l_h_total * hours; fuel_cost = fuel_l * rates["fuel_aed_l"]
        cost = duty + mob * n_units + crew_cost + fuel_cost
        out.append({"gid": len(out), "kind": kind, "need": need, "n_units": n_units, "label_zh": label_zh, "label_en": label_en, "rate_site_m3h": round(sum(x["rate_m3h"] for x in pumps), 1),
                    "H": H, "dH": dH, "t_empty_min": te, "delay_min": round(delay, 0), "delay_delta_min": round(delay_delta, 0), "hours": round(hours, 2), "within_target": bool(te is not None and (float(te) - t_now) <= target),
                    "t_empty_exp_min": (round(float(te_used), 0) if scen else None), "within_target_prob": (round(p_within, 3) if scen else None),
                    "cost_aed": round(cost, 0), "cost_parts": {"duty": round(duty, 0), "mobilisation": round(mob * n_units, 0), "crew": round(crew_cost, 0), "fuel": round(fuel_cost, 0)},
                    "hose_m": hose_m, "fuel_l": round(fuel_l, 0), "crew_n": crew_n, "peak_m3": kp["peak_m3"]})

    max_units = int(P["max_units_per_point"]); useful = float(P["min_useful_rate_share"])
    hose_per_unit = float(cons["hose_base_m"]) + stages * float(cons["hose_per_stage_m"])
    # 1.5 km 内没有可接纳的排放去处时不枚举泵包：泵抽上来的水没地方排，只能靠罐车拉走。
    # 与 feasibility 的 triage、本体规则 dispatchable、recession.py:159 三处口径一致（2026-09-23 修：此前照样派泵，
    # 而且 relay_factor 对这些点是 1.0，等于按满额排量算，10cc 下 150 个无排放去处的点里有 90 个被派了泵）。
    mlist = [m for m in models if m["model"] in trav] if bool(pt.get("discharge_ok", True)) else []
    for m in mlist:
        tv, av, c = trav[m["model"]]
        arrive = t_now + av + tv + float(m["setup_min"]) + (float(m["setup_min"]) * max(stages, 1) if relay_needed else 0.0)
        for n in range(1, max_units + 1):
            rate = float(m["rate_m3h"]) * rf
            if n * rate < useful * inflow and n < max_units:
                continue
            add("pump", {m["model"]: n}, [{"arrive_min": arrive, "rate_m3h": rate}] * n, n, int(m.get("crew") or 0) * n, None, [float(m.get("duty_aed_h") or 60.0)] * n,
                n * hose_per_unit, RS.fuel_l_per_h(m["model"], RS.params(cfg)) * n, f"{m['model'].replace('diesel-', '')} × {n}", f"{m['model'].replace('diesel-', '')} × {n}")
    for i in range(len(mlist)):
        for j in range(i + 1, len(mlist)):
            m1, m2 = mlist[i], mlist[j]
            if max_units < 2:
                break
            a1 = t_now + trav[m1["model"]][1] + trav[m1["model"]][0] + float(m1["setup_min"]); a2 = t_now + trav[m2["model"]][1] + trav[m2["model"]][0] + float(m2["setup_min"])
            extra = (max(float(m1["setup_min"]), float(m2["setup_min"])) * max(stages, 1)) if relay_needed else 0.0
            add("pump", {m1["model"]: 1, m2["model"]: 1}, [{"arrive_min": a1 + extra, "rate_m3h": float(m1["rate_m3h"]) * rf}, {"arrive_min": a2 + extra, "rate_m3h": float(m2["rate_m3h"]) * rf}], 2,
                int(m1.get("crew") or 0) + int(m2.get("crew") or 0), None, [float(m1.get("duty_aed_h") or 60.0), float(m2.get("duty_aed_h") or 60.0)], 2 * hose_per_unit,
                RS.fuel_l_per_h(m1["model"], RS.params(cfg)) + RS.fuel_l_per_h(m2["model"], RS.params(cfg)), f"{m1['model'].replace('diesel-', '')} + {m2['model'].replace('diesel-', '')}", f"{m1['model'].replace('diesel-', '')} + {m2['model'].replace('diesel-', '')}")
    if tanker_ok:
        TP = D1.tanker_params(cfg); tks = [c for c in classes if c["kind"] == "tanker" and c["count"] > 0 and _finite(c["travel"][p])]
        if tks:
            c = min(tks, key=lambda x: x["travel"][p]); rate_t, cycle = D1.tanker_rate_m3h(cfg, pt.get("discharge_dist_m"))
            arrive = t_now + float(c["travel"][p]) + float(TP["load_min"])
            for n in [k for k in P["tanker_counts"] if k <= int(TP["max_per_point"])]:
                add("tanker", {TANKER_MODEL: n}, [{"arrive_min": arrive, "rate_m3h": rate_t}] * n, n, n, None, [float(TP["duty_aed_h"])] * n, 0.0, 0.0,
                    f"罐车 × {n}（周转 {cycle:.0f} min）", f"tankers × {n} ({cycle:.0f} min cycle)")
    return out


def _pkg_key(p: int, pt: pd.Series, classes: list[dict], t_now: float, tanker_ok: bool) -> tuple:
    """处置包缓存的键。必须覆盖 point_packages 真正读到的一切——它只用 classes 里
    「每个型号最近的 (行程, 可用时刻)」和罐车类的 (行程, 数量)，不看别的。
    V 不进键：point_packages 不用 V（帕累托三目标枚举的是同一批包）。
    鲁棒目标不走这个缓存——它的键要带各情景的过程线，不值得为它把键弄复杂。"""
    trav = {}
    for c in classes:
        if c["count"] <= 0:
            continue
        v = c["travel"][p]
        if not _finite(v):
            continue
        k = ("tk" if c["kind"] == "tanker" else c["model"])
        cur = (round(float(v), 3), round(float(c["avail_min"]), 3), int(c["count"]))
        if k not in trav or (cur[0] + cur[1]) < (trav[k][0] + trav[k][1]):
            trav[k] = cur
    pt_sig = (round(float(pt.get("volume_now_m3") or 0.0), 3), round(float(pt.get("inflow_now_m3h") or 0.0), 3),
              round(float(pt.get("target_drain_min") or 360.0), 3), round(float(pt.get("relay_factor") or 1.0), 6),
              int(pt.get("relay_stages") or 0), bool(pt.get("relay_needed", False)),
              round(float(pt.get("discharge_dist_m") or 0.0), 1), bool(pt.get("discharge_ok", True)))
    return (int(p), round(float(t_now), 3), bool(tanker_ok), pt_sig, tuple(sorted(trav.items())))


# ---------------------------------------------------------------- 一个滚动窗口的 MILP
def solve_window(cand: pd.DataFrame, classes: list[dict], pkgs: dict, res: dict, V: float, alpha: float, P: dict, depot_state: dict, budget: float | None, focus: str | None,
                 zones: dict | None = None, pooled: bool = True, work_order_aed: float = 0.0) -> tuple[dict, dict, dict]:
    """返回 (chosen {p: pkg}, alloc {(cid, p): n}, meta)。cand 的 index 是 pts0 的位置索引。
    zones / pooled：承包商分区——silo 只能派本区的类；pooled 可跨区，每台加工单费 work_order_aed。"""
    zones = zones or {}
    plist = [int(p) for p in cand.index if pkgs.get(int(p))]
    if not plist or not classes:
        return {}, {}, {"status": "empty", "n_points": len(plist), "n_classes": len(classes)}
    yvars = [(p, g["gid"]) for p in plist for g in pkgs[p]]
    # 逐格 .loc 在「类 × 点」的双重循环里要上万次，占了组装矩阵的绝大部分时间。一次性提成 dict。
    fpid = {int(k): str(v) for k, v in cand["fp_id"].items()}
    lvl = {int(k): str(v) for k, v in cand["level"].items()}
    stg = {int(k): float(v or 0) for k, v in cand["relay_stages"].fillna(0).items()}
    mtl = P["max_travel_min_by_level"]
    nvars = []
    for c in classes:
        for p in plist:
            tv = c["travel"][p]
            lim = float(mtl.get(lvl[p], mtl.get("I", 90)))
            if _finite(tv) and tv + c["avail_min"] <= lim + c["avail_min"] and tv <= lim:
                needs = any(c["model"] in g["need"] for g in pkgs[p])
                if needs and (pooled or zone_ok(c, fpid[p], zones)):
                    nvars.append((c["cid"], p))
    ny, nn = len(yvars), len(nvars); n = ny + nn
    if nn == 0:
        return {}, {}, {"status": "no reachable class", "n_points": len(plist), "n_classes": len(classes)}
    yix = {k: i for i, k in enumerate(yvars)}; nix = {k: ny + i for i, k in enumerate(nvars)}
    cls = {c["cid"]: c for c in classes}
    w = cand["priority"].astype(float).to_dict()
    cvec = np.zeros(n)
    money = np.zeros(n)            # 每个变量真正花的钱（AED）。预算行只能用它，不能拿 cvec 往回减——
                                   # cvec 里除了钱还有 dH、延误、到达惩罚，减不干净就会往预算里塞幻影 AED（2026-09-23 修）
    for (p, gid), i in yix.items():
        g = pkgs[p][gid]; wp = float(w[p]) * (1e4 if focus and fpid[p] == focus else 1.0)
        cvec[i] = wp * g["dH"] + alpha * wp * g.get("delay_delta_min", g["delay_min"]) / 60.0 + g["cost_aed"] / V
        money[i] = float(g["cost_aed"])
    rates = res["rates"]
    # 处置包的重演已经按「该型号最近的那一类」的行程算过到达时刻了，所以到达罚只能罚「比它还远多少」。
    # 2026-09-24 之前对最近的那一类也照罚，而罚额与优先级成正比——结果优先级越高的点被罚得越狠，
    # 夹具里优先级 300、车程 10 min 的点会输给优先级 60、车程 15 min 的点。
    base_tv: dict[tuple, float] = {}
    for (cid, p) in nvars:
        c0 = cls[cid]; k = (p, c0["model"]); v0 = float(c0["travel"][p]) + float(c0["avail_min"])
        if v0 < base_tv.get(k, float("inf")):
            base_tv[k] = v0
    for (cid, p), i in nix.items():
        c = cls[cid]; tv = float(c["travel"][p]); km = tv / 60.0 * rates["vehicle_speed_kmh"]
        tcost = 2 * km * float(rates["vehicle_aed_km"].get(c["vehicle_class"], 3.0)) + tv / 60.0 * max(c["crew"], 1) * rates["crew_aed_h"]
        if not zone_ok(c, fpid[p], zones):
            tcost += float(work_order_aed)                                 # 跨区支援：市政开工单
        wp = float(w[p]) * (1e4 if focus and fpid[p] == focus else 1.0)
        extra = max(0.0, (tv + float(c["avail_min"])) - base_tv.get((p, c["model"]), tv + float(c["avail_min"])))      # 比最近那一类多出来的到达时间
        cvec[i] = tcost / V + 1e-3 * tv + float(P.get("arrival_hours_weight", 1.0)) * wp * extra / 60.0 / max(1, sum(g["n_units"] for g in pkgs[p]) / max(1, len(pkgs[p])))    # 到达越晚积水越久：按包平均台数摊到每台
        money[i] = tcost
    rows, cols, vals, lo, hi = [], [], [], [], []; ri = 0

    def con(idx_vals, l, h):
        nonlocal ri
        for j, v in idx_vals:
            rows.append(ri); cols.append(j); vals.append(float(v))
        lo.append(l); hi.append(h); ri += 1
    for p in plist:                                                     # 每点 ≤ 1 个包
        con([(yix[(p, g["gid"])], 1.0) for g in pkgs[p]], -np.inf, 1.0)
    for p in plist:                                                     # 型号平衡：Σ_c n[c,p] = Σ_g need y
        models_p = sorted({m for g in pkgs[p] for m in g["need"]})
        for m in models_p:
            iv = [(nix[(c["cid"], p)], 1.0) for c in classes if c["model"] == m and (c["cid"], p) in nix]
            iv += [(yix[(p, g["gid"])], -float(g["need"].get(m, 0))) for g in pkgs[p] if g["need"].get(m)]
            con(iv, 0.0, 0.0)
    for c in classes:                                                   # 类容量
        iv = [(nix[(c["cid"], p)], 1.0) for p in plist if (c["cid"], p) in nix]
        if iv:
            con(iv, -np.inf, float(c["count"]))
    hose_base = float(res["consumption"]["hose_base_m"]); hose_stage = float(res["consumption"]["hose_per_stage_m"])
    carry = res["consumption"]["carry"]; trips = float(res["consumption"]["trips_per_window"])
    for did, ds in depot_state.items():                                 # 驻地：班组人数 / 车次 / 软管
        pump_cls = [c for c in classes if c["kind"] == "pump" and c["depot_id"] == did]
        if not pump_cls:
            continue
        iv = [(nix[(c["cid"], p)], float(c["crew"])) for c in pump_cls for p in plist if (c["cid"], p) in nix]
        if iv:
            con(iv, -np.inf, float(ds["crews_avail"]))
        for vc in ("heavy", "light"):
            iv = [(nix[(c["cid"], p)], 1.0 / float(carry.get(c["model"], 1))) for c in pump_cls if c["vehicle_class"] == vc for p in plist if (c["cid"], p) in nix]
            if iv:
                con(iv, -np.inf, float(ds["vehicles"].get(vc, 0)) * trips)
        iv = [(nix[(c["cid"], p)], hose_base + stg[p] * hose_stage) for c in pump_cls for p in plist if (c["cid"], p) in nix]
        if iv:
            con(iv, -np.inf, float(ds["hose_m"]))
    fuel_total = sum(ds["fuel_l"] for ds in depot_state.values())
    iv = [(yix[(p, g["gid"])], float(g["fuel_l"])) for p in plist for g in pkgs[p] if g["fuel_l"]]
    if iv:
        con(iv, -np.inf, float(fuel_total))
    if budget is not None:
        iv = [(i, float(money[i])) for i in range(n) if money[i]]
        con(iv, -np.inf, float(budget))
    A = coo_matrix((vals, (rows, cols)), shape=(ri, n)).tocsr()
    ub = np.ones(n)
    for (cid, p), i in nix.items():
        ub[i] = float(cls[cid]["count"])
    t0 = time.perf_counter()
    r = milp(cvec, constraints=LinearConstraint(A, lo, hi), integrality=np.ones(n), bounds=Bounds(np.zeros(n), ub), options={"time_limit": float(P["milp_time_limit_s"])})
    ms = round((time.perf_counter() - t0) * 1000, 1)
    if r.x is None:
        return {}, {}, {"status": r.message, "ms": ms, "n_vars": n}
    x = np.round(r.x).astype(int)
    chosen = {p: pkgs[p][gid] for (p, gid), i in yix.items() if x[i] == 1}
    alloc = {(cid, p): int(x[i]) for (cid, p), i in nix.items() if x[i] > 0}
    return chosen, alloc, {"status": "optimal" if r.success else str(r.message), "ms": ms, "n_vars": n, "n_y": ny, "n_alloc": nn, "objective": round(float(r.fun), 3), "n_points": len(plist), "n_classes": len(classes)}


# ---------------------------------------------------------------- 解码成调度单行（每台一行）并按实际到达重演
def decode(chosen: dict, alloc: dict, classes: list[dict], cand: pd.DataFrame, res: dict, cfg: dict, t_now: float, window: int, tk_counter: list, zones: dict | None = None) -> tuple[list[dict], dict]:
    cls = {c["cid"]: c for c in classes}; rows = []; used_units = {}; zones = zones or {}
    C = (cfg.get("dispatch") or {}).get("cost") or {}; mob = float(C.get("mobilisation_aed_per_unit", 500)); horizon = t_now + 2880.0
    TP = D1.tanker_params(cfg); Rp = RS.params(cfg)
    for p, g in chosen.items():
        pt = cand.loc[p]; rf = float(pt.get("relay_factor") or 1.0); stages = int(pt.get("relay_stages") or 0); relay_needed = bool(pt.get("relay_needed", False))
        parts = []
        for (cid, pp), nn in alloc.items():
            if pp != p or nn <= 0:
                continue
            c = cls[cid]
            for k in range(nn):
                if c["kind"] == "tanker":
                    tk_counter[0] += 1; uid = f"TK-{tk_counter[0]:03d}"
                    rate_t, cycle = D1.tanker_rate_m3h(cfg, pt.get("discharge_dist_m"))
                    depart = t_now + c["avail_min"]; arrive = depart + float(c["travel"][p]); pf = arrive + float(TP["load_min"])
                    parts.append({"unit_id": uid, "model": TANKER_MODEL, "cid": cid, "rate": rate_t, "rate_nominal": rate_t, "depart": depart, "arrive": arrive, "setup_end": pf, "travel": float(c["travel"][p]),
                                  "from": c["from"], "from_lon": c["lon"], "from_lat": c["lat"], "duty": float(TP["duty_aed_h"]), "crew": 1, "fuel_l_h": 0.0, "haul_km": round(min(float(pt.get("discharge_dist_m") or 0) / 1000.0, float(TP["max_haul_km"])) or float(TP["default_haul_km"]), 1), "cycle_min": round(cycle, 0), "vehicle_class": "heavy",
                                  "contractor": c.get("contractor"), "cross": not zone_ok(c, pt["fp_id"], zones)})
                else:
                    uid = c["unit_ids"][used_units.get(cid, 0)] if used_units.get(cid, 0) < len(c["unit_ids"]) else f"{c['model']}-{cid}-{k}"
                    used_units[cid] = used_units.get(cid, 0) + 1
                    depart = t_now + c["avail_min"]; arrive = depart + float(c["travel"][p])
                    setup = float(c["setup_min"]) + (float(c["setup_min"]) * max(stages, 1) if relay_needed else 0.0)
                    parts.append({"unit_id": uid, "model": c["model"], "cid": cid, "rate": float(c["rate_m3h"]) * rf, "rate_nominal": float(c["rate_m3h"]), "depart": depart, "arrive": arrive, "setup_end": arrive + setup, "travel": float(c["travel"][p]),
                                  "from": c["from"], "from_lon": c["lon"], "from_lat": c["lat"], "duty": c["duty_aed_h"], "crew": c["crew"], "fuel_l_h": RS.fuel_l_per_h(c["model"], Rp), "vehicle_class": c["vehicle_class"],
                                  "contractor": c.get("contractor"), "cross": not zone_ok(c, pt["fp_id"], zones)})
        if not parts:
            continue
        pumps = [{"arrive_min": x["setup_end"], "rate_m3h": x["rate"]} for x in parts]
        r = replay.replay(pt["hydrograph"], pumps, dt=5.0, t_end=horizon)
        te = r["kpi"]["pump"]["t_empty_min"] if not r.get("error") else None
        done = float(te) if te is not None else None
        start = max(x["setup_end"] for x in parts); site_rate = sum(x["rate"] for x in parts)
        for x in parts:
            hours = ((done if done is not None else horizon) - x["depart"]) / 60.0
            rows.append({"wave": window, "window": window, "unit_id": x["unit_id"], "model": x["model"], "rate_m3h": round(x["rate"], 1), "rate_nominal_m3h": x["rate_nominal"], "relay_stages": stages, "relay_factor": round(rf, 3),
                         "from": x["from"], "fp_id": pt["fp_id"], "note": pt.get("note"), "priority": float(pt["priority"]), "top_kind": pt.get("top_kind"), "level": pt.get("level"),
                         "depart_min": round(x["depart"], 0), "travel_min": round(x["travel"], 1), "arrive_min": round(x["arrive"], 0), "pumping_from_min": round(start, 0),
                         "site_rate_m3h": round(site_rate, 1), "inflow_m3h": float(pt["inflow_now_m3h"]), "volume_m3": float(pt["volume_now_m3"]),
                         "drain_min": (None if done is None else round(done - start, 0)), "done_min": (None if done is None else round(done, 0)),
                         "target_drain_min": pt["target_drain_min"], "within_target": bool(done is not None and (done - t_now) <= float(pt["target_drain_min"]) + 60),
                         "discharge_to": pt.get("discharge_to"), "lon": pt["lon"], "lat": pt["lat"], "from_lon": x["from_lon"], "from_lat": x["from_lat"],
                         "package": g["label_zh"], "package_en": g["label_en"], "crew_n": int(x["crew"]), "hose_m": (0.0 if x["model"] == TANKER_MODEL else float(res["consumption"]["hose_base_m"]) + stages * float(res["consumption"]["hose_per_stage_m"])),
                         "fuel_l": round(x["fuel_l_h"] * max(hours, 0), 0), "duty_aed_h": x["duty"], "vehicle_class": x["vehicle_class"], "contractor": x.get("contractor"), "cross_zone": bool(x.get("cross")),
                         "cost_aed": round(max(float(C.get("min_duty_h", 4)), hours) * x["duty"] + mob + x["crew"] * max(hours, 0) * res["rates"]["crew_aed_h"] + x["fuel_l_h"] * max(hours, 0) * res["rates"]["fuel_aed_l"], 0),
                         **({"haul_km": x["haul_km"], "cycle_min": x["cycle_min"]} if x["model"] == TANKER_MODEL else {})})
    return rows, used_units


# ---------------------------------------------------------------- 车次与班组排程（按驻地）
def schedule(plan: pd.DataFrame, res: dict, t_decide: float) -> dict:
    """窗口 1 从驻地出发的泵：按载量拼车次、按车辆轮转排出发；班组：当班先上、召回的晚 mobilization；跨 12 h 需换班。返回甘特与资源利用。"""
    cons = res["consumption"]; out_depots = {}; trips_all = []; unit_shift = {}
    if plan is None or not len(plan):
        return {"depots": {}, "trips": [], "relief_crews_needed": 0}
    pumps = plan[(plan["model"].astype(str) != TANKER_MODEL) & (plan["window"] == 1)] if "window" in plan.columns else plan[plan["model"].astype(str) != TANKER_MODEL]
    for did, g in pumps.groupby("from"):
        ds = (res.get("depots") or {}).get(did)
        if ds is None:
            continue
        veh = {vc: [t_decide] * int(ds["vehicles"].get(vc, 0)) for vc in ("heavy", "light")}     # 每辆车下一次可出发时刻
        rows = g.sort_values(["priority", "travel_min"], ascending=[False, True])
        trips = []
        for vc in ("heavy", "light"):
            gg = rows[rows["vehicle_class"].astype(str) == vc]
            batches = []
            for fp, gp in gg.groupby("fp_id", sort=False):
                units_here = gp.to_dict("records"); i = 0
                while i < len(units_here):
                    cap = int(cons["carry"].get(str(units_here[i]["model"]), 1)); batch = [u for u in units_here[i:i + cap] if str(u["model"]) == str(units_here[i]["model"])]
                    i += len(batch); batches.append((fp, batch))
            if not veh[vc]:
                for fp, batch in batches:
                    for u in batch:
                        unit_shift[u["unit_id"]] = None                     # 没有这类车：标记，不排
                continue
            cp = schedule_cpsat([{"tid": k, "earliest": float(b[0]["depart_min"]), "duration": 2 * float(b[0]["travel_min"]) + 20.0} for k, (fp, b) in enumerate(batches)], len(veh[vc]))
            for k, (fp, batch) in enumerate(batches):
                tv = float(batch[0]["travel_min"])
                if cp is not None and k in cp:
                    j, dep = cp[k]; j = int(j)
                else:
                    j = int(np.argmin(veh[vc])); dep = max(float(veh[vc][j]), float(batch[0]["depart_min"]))
                veh[vc][j] = max(veh[vc][j], dep + 2 * tv + 20.0)               # 送到 + 卸 10 + 返回 + 装 10
                for u in batch:
                    unit_shift[u["unit_id"]] = dep - float(u["depart_min"])
                vid = f"{did}-{vc[0].upper()}{j + 1:02d}"
                trips.append({"depot_id": did, "vehicle_id": vid, "vehicle_class": vc, "trip": len([t for t in trips if t["vehicle_id"] == vid]) + 1, "solver": ("cpsat" if cp is not None else "greedy"),
                              "units": [u["unit_id"] for u in batch], "fp_id": fp, "depart_min": round(dep, 0), "arrive_min": round(dep + tv, 0), "back_min": round(dep + 2 * tv + 20.0, 0)})
        # 班组：当班先上，用完召回（晚 mobilization），泵工作超 12 h 需换班
        persons_needed = int(g["crew_n"].sum()); on = int(ds["crews"]["on_duty"]); callable_ = int(ds["crews"]["callable"])
        from_callable = max(0, persons_needed - on); shortage = max(0, persons_needed - on - callable_)
        long_units = g[(g["done_min"].fillna(t_decide + 2880.0) - g["depart_min"]) > cons["shift_h"] * 60.0]
        relief = int(long_units["crew_n"].sum())
        out_depots[did] = {"depot_id": did, "name": ds.get("name"), "units_dispatched": int(g["unit_id"].nunique()), "units_total": int(ds["n_units"]),
                           "trips": len(trips), "trip_capacity": {vc: int(ds["vehicles"].get(vc, 0)) * int(cons["trips_per_window"]) for vc in ("heavy", "light")}, "vehicles": ds["vehicles"],
                           "persons_needed": persons_needed, "persons_on_duty": on, "persons_callable": callable_, "persons_from_callable": from_callable, "persons_shortage": shortage,
                           "relief_persons_at_shift_change": relief, "hose_used_m": float(g["hose_m"].sum()), "hose_stock_m": float(ds["materials"]["hose_m"]),
                           "fuel_used_l": float(g["fuel_l"].sum()), "fuel_stock_l": float(ds["materials"]["fuel_l"]), "lights_needed": int(math.ceil(g["fp_id"].nunique() * 0.5)), "lights_stock": int(ds["materials"]["lights"])}
        trips_all += trips
    return {"depots": out_depots, "trips": trips_all[:300], "unit_vehicle_wait_min": {k: (round(v, 0) if v is not None else None) for k, v in unit_shift.items()},
            "relief_crews_needed": int(sum(d["relief_persons_at_shift_change"] for d in out_depots.values())), "persons_shortage": int(sum(d["persons_shortage"] for d in out_depots.values()))}


def apply_vehicle_wait(plan: pd.DataFrame, waits: dict) -> pd.DataFrame:
    """车次排队让泵晚出发：把等待时间加到出发 / 到达 / 开抽 / 完成上（同点的开抽时刻取最晚到达 + 原架设）。"""
    if plan is None or not len(plan) or not waits:
        return plan
    p = plan.copy()
    sh = p["unit_id"].map(lambda u: waits.get(u) or 0.0).astype(float)
    for col in ("depart_min", "arrive_min"):
        p[col] = p[col] + sh
    setup = (p["pumping_from_min"] - p.groupby("fp_id")["arrive_min"].transform("max")).clip(lower=0)
    new_start = p.groupby("fp_id")["arrive_min"].transform("max") + setup
    delta = (new_start - p["pumping_from_min"]).clip(lower=0)
    p["pumping_from_min"] = new_start
    p["done_min"] = p["done_min"] + delta
    p["vehicle_wait_min"] = sh.round(0)
    return p


def cost_breakdown(plan: pd.DataFrame, res: dict) -> dict:
    if plan is None or not len(plan):
        return {"total_aed": 0.0, "duty_aed": 0.0, "mobilisation_aed": 0.0, "crew_aed": 0.0, "fuel_aed": 0.0, "vehicle_aed": 0.0, "tanker_aed": 0.0, "units": 0, "tankers": 0, "pump_hours": 0.0}
    tk = plan["model"].astype(str) == TANKER_MODEL
    hours = ((plan["done_min"].fillna(plan["depart_min"] + 2880.0) - plan["depart_min"]) / 60.0).clip(lower=0)
    duty = float((np.maximum(hours, 4.0) * plan["duty_aed_h"].astype(float))[~tk].sum()); tanker = float((np.maximum(hours, 4.0) * plan["duty_aed_h"].astype(float))[tk].sum())
    crew = float((plan["crew_n"].astype(float) * hours * res["rates"]["crew_aed_h"]).sum()); fuel = float(plan["fuel_l"].astype(float).sum() * res["rates"]["fuel_aed_l"])
    km = plan["travel_min"].astype(float) / 60.0 * res["rates"]["vehicle_speed_kmh"] * 2
    veh = float(sum(k * res["rates"]["vehicle_aed_km"].get(str(vc), 3.0) for k, vc in zip(km, plan["vehicle_class"].astype(str))))
    mob = 500.0 * len(plan)
    wo = float(res.get("_work_order_aed", 0.0)) * (int(plan["cross_zone"].astype(bool).sum()) if "cross_zone" in plan.columns else 0)
    return {"total_aed": round(duty + tanker + crew + fuel + veh + mob + wo, 0), "duty_aed": round(duty, 0), "tanker_aed": round(tanker, 0), "mobilisation_aed": round(mob, 0), "crew_aed": round(crew, 0), "fuel_aed": round(fuel, 0), "vehicle_aed": round(veh, 0), "work_order_aed": round(wo, 0),
            "units": int((~tk).sum()), "tankers": int(tk.sum()), "pump_hours": round(float(hours[~tk].sum()), 1), "basis": "台时 + 动员 + 人工 + 燃油 + 车公里 + 罐车台时 + 跨区工单；单价见 [dispatch.cost] / [dispatch.resources]（占位）。这是求解器优化的口径，控制台显示的也是它；顶层 cost 是只算台时 + 动员的 v1 口径，同一方案小三成"}


# ---------------------------------------------------------------- 滚动求解
def rolling(ctx: dict, cfg: dict, t_decide: float, objective: str = "balanced", budget_aed: float | None = None, focus_fp: str | None = None, mode: str | None = None,
            t_start: float | None = None, unit_state: dict | None = None, unit_exclude: set | None = None, served_init: set | None = None, priority_boost: dict | None = None, locked_rows: list | None = None,
            robust: dict | None = None, pkg_cache: dict | None = None) -> dict:
    """robust: {"frame": 当前情景键, "weights": {情景键: 权重}, "hyds": {情景键: {fp_id: 过程线}}}——处置包的收益按各情景期望算（严格版鲁棒目标）。
    t_start / unit_state / unit_exclude / served_init / priority_boost / locked_rows：给 replan 用——从中途时刻起算，已出发的泵按 unit_state 占用，故障泵剔除，已退干的点不再算，报案点加权，已锁定的派单行原样保留。"""
    P = params(cfg); ctx = {**ctx, "cfg": cfg}; CP = contractor_params(cfg)
    mode = (mode or CP.get("mode") or "pooled"); pooled = mode != "silo"; work_order = float(CP.get("work_order_aed", 0.0))
    fleet, cs, pts0, depots = ctx["fleet"], ctx["cs"], ctx["pts0"], ctx["depots"]
    res = RS.build(fleet, ctx.get("hotspots"), cfg)
    zones = point_zones(pts0, ctx.get("hotspots"))
    V = float(P["value_aed_per_priority_hour"].get(objective, 100.0)); alpha = float(P["delay_weight"])
    models = fleet["models"]
    units = ctx["units"].copy()
    units["at_pt"] = -1; units["busy_until"] = np.nan
    dres = res["depots"]
    units["contractor"] = units["from"].map(lambda d: (dres.get(d) or {}).get("contractor"))
    units["zone_region"] = units["from"].map(lambda d: (dres.get(d) or {}).get("region"))
    units["zone_areas"] = units["from"].map(lambda d: (dres.get(d) or {}).get("areas") or [])
    if unit_exclude:
        units = units[~units["unit_id"].isin(set(unit_exclude))].copy()
    for uid, stt in (unit_state or {}).items():                         # replan：已出发 / 在场的泵占用到它的退干时刻，位置在它的点
        m = units["unit_id"] == uid
        if m.any():
            units.loc[m, "busy_until"] = float(stt.get("busy_until") or 0.0)
            if stt.get("at_pt") is not None:
                units.loc[m, "at_pt"] = int(stt["at_pt"]); units.loc[m, "from"] = stt.get("from") or units.loc[m, "from"]
                units.loc[m, "lon"] = float(stt.get("lon") or units.loc[m, "lon"].iloc[0]); units.loc[m, "lat"] = float(stt.get("lat") or units.loc[m, "lat"].iloc[0])
    depot_state = {did: {"crews_avail": ds["crews"]["on_duty"] + ds["crews"]["callable"], "vehicles": dict(ds["vehicles"]), "hose_m": float(ds["materials"]["hose_m"]), "fuel_l": float(ds["materials"]["fuel_l"])} for did, ds in res["depots"].items()}
    res["_tankers_used"] = {}; res["_work_order_aed"] = work_order
    served, rows, windows, tk_counter = set(served_init or set()), [], [], [0]
    for r in (locked_rows or []):                                       # 已出发的派单：原样保留，占用它的驻地班组 / 软管
        if str(r.get("model")) == TANKER_MODEL:
            res["_tankers_used"][r.get("from")] = res["_tankers_used"].get(r.get("from"), 0) + 1
        elif r.get("from") in depot_state:
            depot_state[r["from"]]["crews_avail"] -= int(r.get("crew_n") or 0); depot_state[r["from"]]["hose_m"] -= float(r.get("hose_m") or 0)
    tk_counter[0] = sum(1 for r in (locked_rows or []) if str(r.get("model")) == TANKER_MODEL)
    boost = priority_boost or {}
    t = float(t_start if t_start is not None else t_decide); t_begin = t; budget_left = budget_aed
    for w in range(1, int(P["max_windows"]) + 1):
        pts = D1.feasibility(pts0, cs, fleet, cfg, t)
        pts["level"], pts["level_priority"] = D1.emergency_level(pts)
        if boost:
            pts["priority"] = pts["priority"] * pts["fp_id"].map(lambda f: float(boost.get(str(f), 1.0)))
        tank_ok = any(v > 0 for v in ((d.get("tankers") or 0) - res["_tankers_used"].get(did, 0) for did, d in res["depots"].items()))
        cand = pts[(~pts["fp_id"].isin(served)) & (pts["volume_now_m3"] >= 50) & (pts["dispatchable"] | (tank_ok & (~pts["discharge_ok"].astype(bool)) & pts["pump_worthy"].astype(bool)))]
        avail_units = units[units["busy_until"].isna() | (units["busy_until"] <= t)]
        classes = build_classes(avail_units, depots, res, ctx, t)
        if not classes:
            windows.append({"window": w, "t_min": t, "status": "nothing to do", "n_points": int(len(cand)), "n_classes": len(classes)}); break
        if not len(cand):                                               # 这一窗口还没有够格的点（2026-10-27 修：原来直接结束，积水压得小的方案里晚涨起来的点没人派）
            windows.append({"window": w, "t_min": t, "status": "nothing to do yet", "n_points": 0, "n_classes": len(classes)})
            t += float(P["window_min"])
            if t >= t_decide + float(P["horizon_min"]):
                break
            continue
        pkgs = {}
        for p in cand.index:                                            # 处置包与目标无关，四遍滚动求解公用一份缓存
            tok = tank_ok and not bool(cand.loc[p, "discharge_ok"]); a_ = _alt_for(robust, cand.loc[p])
            if pkg_cache is None or robust:                         # 鲁棒目标的包还取决于各情景过程线，不进共享缓存
                pkgs[int(p)] = point_packages(int(p), cand.loc[p], models, classes, res, V, cfg, P, t, tok, alt=a_)
                continue
            k = _pkg_key(int(p), cand.loc[p], classes, t, tok)
            hit = pkg_cache.get(k)
            if hit is None:
                hit = point_packages(int(p), cand.loc[p], models, classes, res, V, cfg, P, t, tok, alt=a_)
                pkg_cache[k] = hit
            pkgs[int(p)] = hit
        pkgs = {p: g for p, g in pkgs.items() if g}
        chosen, alloc, meta = solve_window(cand, classes, pkgs, res, V, alpha, P, depot_state, budget_left, focus_fp if w == 1 else None, zones=zones, pooled=pooled, work_order_aed=work_order)
        new_rows, _ = decode(chosen, alloc, classes, cand, res, cfg, t, w, tk_counter, zones=zones)
        windows.append({"window": w, "t_min": t, **meta, "n_candidates": int(len(cand)), "n_packages": int(sum(len(g) for g in pkgs.values())), "points_served": len(chosen), "units": len(new_rows)})
        cls = {c["cid"]: c for c in classes}
        for (cid, p), nn in alloc.items():                              # 资源消耗
            c = cls[cid]
            if c["kind"] == "tanker":
                res["_tankers_used"][c["depot_id"]] = res["_tankers_used"].get(c["depot_id"], 0) + nn
            elif c["depot_id"] in depot_state:
                ds = depot_state[c["depot_id"]]; ds["crews_avail"] -= c["crew"] * nn
                ds["hose_m"] -= nn * (float(res["consumption"]["hose_base_m"]) + float(cand["relay_stages"].get(p) or 0) * float(res["consumption"]["hose_per_stage_m"]))
        for p, g in chosen.items():
            fuel_total = sum(depot_state[d]["fuel_l"] for d in depot_state)
            if fuel_total > 0:
                for d in depot_state:
                    depot_state[d]["fuel_l"] -= g["fuel_l"] * depot_state[d]["fuel_l"] / fuel_total
        if budget_left is not None:
            budget_left = max(0.0, budget_left - sum(r["cost_aed"] for r in new_rows))
        for r in new_rows:                                              # 泵状态：占用到退干（48 h 内不退就占到视界）
            if r["model"] == TANKER_MODEL:
                continue
            m = units["unit_id"] == r["unit_id"]
            units.loc[m, "busy_until"] = float(r["done_min"]) if r["done_min"] is not None else t + P["horizon_min"]
            units.loc[m, "at_pt"] = int(pts.index[pts["fp_id"] == r["fp_id"]][0]); units.loc[m, "from"] = r["fp_id"]
            units.loc[m, "lon"] = r["lon"]; units.loc[m, "lat"] = r["lat"]
        served |= set(chosen and {cand["fp_id"].get(p) for p in chosen})
        rows += new_rows
        t += float(P["window_min"])
        if t >= t_decide + float(P["horizon_min"]):
            break
        # 抢占：入流已压住、剩余水量 30 min 内抽完的点，多余的泵在下一窗口释放
        df = pd.DataFrame(rows)
        if len(df):
            for fp, gp in df[(df["model"] != TANKER_MODEL) & (df["done_min"].isna() | (df["done_min"] > t))].groupby("fp_id"):
                pt = pts0[pts0["fp_id"] == fp]
                if not len(pt):
                    continue
                vol, inflow = D1.state_at(pt.iloc[0]["hydrograph"], t, float(pt.iloc[0]["volume_m3"]), float(pt.iloc[0]["duration_min"]))
                site = float(gp["rate_m3h"].sum())
                if len(gp) > 1 and inflow < float(P["preempt_inflow_share"]) * site and vol / max(site - inflow, 1e-6) * 60.0 <= float(P["preempt_remaining_min"]):
                    keep = gp.sort_values("rate_m3h").iloc[0]["unit_id"]
                    for uid in gp["unit_id"]:
                        if uid != keep:
                            units.loc[units["unit_id"] == uid, "busy_until"] = t
                            for r in rows:
                                if r["unit_id"] == uid and r["fp_id"] == fp:
                                    r["released_min"] = round(t, 0)
    plan = pd.DataFrame((locked_rows or []) + rows)
    sched = schedule(plan, res, t_decide)
    plan = apply_vehicle_wait(plan, sched.get("unit_vehicle_wait_min") or {})
    pts_final = D1.feasibility(pts0, cs, fleet, cfg, t_decide); pts_final["level"], pts_final["level_priority"] = D1.emergency_level(pts_final)
    unserved = pts_final[~pts_final["fp_id"].isin(served) & (pts_final["volume_now_m3"] >= 50)]
    reasons = unserved["triage"].value_counts().to_dict()
    n_cand_unserved = int(unserved["dispatchable"].sum())
    if n_cand_unserved:
        reasons["可派泵但资源 / 行程 / 预算不够"] = n_cand_unserved
        reasons = {k: v for k, v in reasons.items() if not str(k).startswith("可派泵") or k == "可派泵但资源 / 行程 / 预算不够"}
    util = {did: {"units_used": int(plan.loc[(plan["from"] == did) & (plan["model"] != TANKER_MODEL), "unit_id"].nunique()) if len(plan) else 0, "units_total": ds["n_units"],
                  "tankers_used": int(res["_tankers_used"].get(did, 0)), "tankers_total": int(ds["tankers"]),
                  "crews_used": int(ds["crews"]["on_duty"] + ds["crews"]["callable"] - depot_state[did]["crews_avail"]), "crews_total": int(ds["crews"]["persons"]),
                  "hose_used_m": round(float(ds["materials"]["hose_m"] - depot_state[did]["hose_m"]), 0), "hose_total_m": ds["materials"]["hose_m"],
                  "fuel_used_l": round(float(ds["materials"]["fuel_l"] - depot_state[did]["fuel_l"]), 0), "fuel_total_l": ds["materials"]["fuel_l"], "vehicles": ds["vehicles"], "name": ds.get("name"), "area": ds.get("area")}
            for did, ds in res["depots"].items()}
    tk_used = int(sum(res["_tankers_used"].values())); tk_pool = int(res["totals"]["tankers"])
    contractors = {}
    if len(plan) and "contractor" in plan.columns:
        for cname, g in plan.groupby(plan["contractor"].fillna("?")):
            contractors[str(cname)] = {"units": int(g.loc[g["model"] != TANKER_MODEL, "unit_id"].nunique()), "tankers": int(g.loc[g["model"] == TANKER_MODEL, "unit_id"].nunique()),
                                       "cross_zone_units": int(g.loc[g["cross_zone"].astype(bool), "unit_id"].nunique()), "points": int(g["fp_id"].nunique()), "cost_aed": round(float(g["cost_aed"].sum()), 0)}
    cross_units = int(plan.loc[plan["cross_zone"].astype(bool), "unit_id"].nunique()) if len(plan) and "cross_zone" in plan.columns else 0
    out = {"plan": plan, "windows": windows, "objective": objective, "mode": mode, "pooled": pooled, "t_start": t_begin, "locked_units": len({r.get("unit_id") for r in (locked_rows or [])}), "work_order_aed": work_order, "cross_zone_units": cross_units, "cross_zone_cost_aed": round(cross_units * work_order, 0), "contractors": contractors, "objective_zh": OBJ_ZH.get(objective, objective), "objective_en": OBJ_EN.get(objective, objective), "value_aed_per_priority_hour": V, "delay_weight": alpha,
           "budget_aed": budget_aed, "budget_left_aed": budget_left, "focus_fp": focus_fp, "served": sorted(served), "unserved_reasons": reasons,
           "resources": {k: v for k, v in res.items() if not k.startswith("_")}, "utilisation": util, "schedule": {k: v for k, v in sched.items() if k != "unit_vehicle_wait_min"},
           "cost": cost_breakdown(plan, res), "tankers": {"pool": tk_pool, "used": tk_used, "points": int(plan.loc[plan["model"] == TANKER_MODEL, "fp_id"].nunique()) if len(plan) else 0, **{k: v for k, v in D1.tanker_params(cfg).items() if k != "model"}},
           "pts": pts_final, "robust": ({"frame": robust.get("frame"), "weights": robust.get("weights")} if robust else None)}
    return out


def _alt_for(robust: dict | None, pt) -> list | None:
    """该点在各情景的过程线与权重；当前情景用它自己的，别的情景按 fp_id 查，查不到 = 那里不积水。"""
    if not robust:
        return None
    W = robust.get("weights") or {}; hy = robust.get("hyds") or {}; frame = robust.get("frame")
    return [((pt.get("hydrograph") if str(k) == str(frame) else (hy.get(str(k)) or {}).get(str(pt["fp_id"]))) or [], float(w)) for k, w in W.items()]


def _compact_eval(st: dict) -> dict:
    return {k: st.get(k) for k in ("points_in_plan", "within_target_worse", "never_empties_worse", "points_with_gap", "extra_12in_equiv_total")} | {"worst": [{kk: w.get(kk) for kk in ("fp_id", "rate_gap_m3h", "extra_12in_equiv", "never_empties")} for w in (st.get("worst") or [])[:5]]}


def robust_spec(frame: str, weights: dict, ctxs: dict, labels: dict | None = None, base_plan=None) -> dict:
    """拼鲁棒目标的输入：权重归一；各情景的过程线按 fp_id 索引；ctxs 留给事后评估。frame 不在权重表里就补进去（取表里最大的权重）。"""
    W = {str(k): float(v) for k, v in (weights or {}).items() if float(v) > 0}
    if str(frame) not in W:
        W[str(frame)] = max(W.values()) if W else 1.0
    tot = sum(W.values()) or 1.0
    W = {k: round(v / tot, 4) for k, v in W.items()}
    hyds = {}
    for k, c in (ctxs or {}).items():
        if c is None or str(k) == str(frame):
            continue
        pts = c["pts0"]
        hyds[str(k)] = dict(zip(pts["fp_id"].astype(str), pts["hydrograph"]))
    return {"frame": str(frame), "weights": W, "hyds": hyds, "ctxs": {str(k): c for k, c in (ctxs or {}).items() if c is not None}, "labels": labels or {}, "base_plan": base_plan}


def solve(ctx: dict, cfg: dict, t_decide: float, objective: str = "balanced", budget_aed: float | None = None, focus_fp: str | None = None, with_pareto: bool = True, mode: str | None = None, robust: dict | None = None) -> dict:
    """主入口：按所选目标滚动求解；with_pareto 时另跑两个目标只取 KPI，给页面画帕累托；compare_silo 时再跑一遍「各管各的」作对照。
    额外的三路（两个目标 + silo）缺省顺序跑；[dispatch.v2].parallel_extras=true 改线程并行，但 HiGHS 与重演都握着 GIL，真机上没有收益还会拖垮服务，只留作开关。
    robust（见 robust_spec）：处置包收益按多情景期望算，额外三路同样按鲁棒算；解出的方案再放到每个情景上重演（与非鲁棒方案对照）。"""
    objective = objective if objective in OBJECTIVES else "balanced"
    P = params(cfg); CP = contractor_params(cfg); mode = mode or CP.get("mode") or "pooled"
    trt = float(((cfg.get("dispatch") or {}).get("manual_levels") or {}).get("trt_min", 120))
    want_silo = bool(with_pareto and CP.get("compare_silo", True) and mode != "silo")
    others = [ob for ob in OBJECTIVES if ob != objective] if with_pareto else []
    jobs = {"main": (objective, budget_aed, focus_fp, mode)}
    if want_silo:
        jobs["silo"] = (objective, budget_aed, None, "silo")
    for ob in others:
        jobs[ob] = (ob, budget_aed, None, mode)

    # 处置包与目标无关（point_packages 不用 V），四遍滚动求解里窗口 1 的包完全一样，
    # 共用一份缓存。并行时不共用——两个线程同时写一个 dict 不值当（2026-09-24）。
    shared_pkgs: dict = {}

    def run(job, cache=None):
        ob, bud, foc, md = job
        return rolling(ctx, cfg, t_decide, ob, bud, foc, mode=md, robust=robust, pkg_cache=cache)
    results = {}; errors = {}
    parallel = bool(len(jobs) > 1 and P.get("parallel_extras", False))          # 缺省关：HiGHS / 重演握着 GIL，并行无收益且会把服务卡住（2026-09-22 真机）
    if parallel:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as ex:
            futs = {k: ex.submit(run, j) for k, j in jobs.items()}
            for k, f in futs.items():
                try:
                    results[k] = f.result()
                except Exception as e:
                    if k == "main":
                        raise
                    errors[k] = str(e)[:200]
    else:
        for k, j in jobs.items():
            try:
                results[k] = run(j, shared_pkgs)
            except Exception as e:
                if k == "main":
                    raise
                errors[k] = str(e)[:200]
    main = results["main"]
    if robust:
        ev = {}
        for k, ctx_s in (robust.get("ctxs") or {}).items():
            try:
                ev[k] = _compact_eval(stress_test(ctx_s, cfg, main["plan"], t_decide, label=str(k)))
                bp = robust.get("base_plan")
                if bp is not None and len(bp):
                    ev[k]["base"] = _compact_eval(stress_test(ctx_s, cfg, bp, t_decide, label=str(k)))
            except Exception as ex:
                ev[k] = {"error": str(ex)[:160]}
        W = robust.get("weights") or {}

        def _exp(field, which=None):
            tot = 0.0
            for k, e in ev.items():
                if e.get("error"):
                    continue
                src = (e.get(which) or {}) if which else e
                tot += float(W.get(k, 0.0)) * float(src.get(field) or 0)
            return round(tot, 1)
        has_base = any(isinstance(e.get("base"), dict) for e in ev.values())
        main["robust"] = {"frame": robust.get("frame"), "weights": W, "labels": robust.get("labels") or {}, "by_scenario": ev,
                          "expected": {"within_target": _exp("within_target_worse"), "never_empties": _exp("never_empties_worse"), "extra_12in_equiv_total": _exp("extra_12in_equiv_total")},
                          "expected_base": ({"within_target": _exp("within_target_worse", "base"), "never_empties": _exp("never_empties_worse", "base"), "extra_12in_equiv_total": _exp("extra_12in_equiv_total", "base")} if has_base else None),
                          "basis": "处置包收益 = Σ 情景权重 × 该情景过程线上重演的（少积水的优先级·小时 + 延误）；一张单同时对三种雨；事后把这张单放到每个情景上重演，与只按当前情景优化的单对照；帕累托三方案与分区对照同样按鲁棒目标算",
                          "basis_en": "Package benefit = Σ scenario weight × (priority-hours saved + delay) replayed on that scenario's hydrograph; one plan for all rains; the plan is then replayed on every scenario and compared with the single-scenario plan; Pareto and silo comparisons use the robust objective too"}
    if want_silo:
        silo = results.get("silo")
        if silo is None:
            main["silo_vs_pooled"] = {"error": errors.get("silo", "silo run failed")}
        else:
            ks = D1.kpis(silo["plan"], silo["pts"], t_decide, trt) if len(silo["plan"]) else {"points_served": 0, "weighted_flood_hours": None, "critical_within_target": 0}
            km = D1.kpis(main["plan"], main["pts"], t_decide, trt) if len(main["plan"]) else {"points_served": 0, "weighted_flood_hours": None, "critical_within_target": 0}
            main["silo_vs_pooled"] = {"silo": {"points_served": ks.get("points_served"), "cost_aed": silo["cost"]["total_aed"], "weighted_flood_hours": ks.get("weighted_flood_hours"), "units": silo["cost"]["units"], "tankers": silo["cost"]["tankers"], "critical_within_target": ks.get("critical_within_target")},
                                     "pooled": {"points_served": km.get("points_served"), "cost_aed": main["cost"]["total_aed"], "weighted_flood_hours": km.get("weighted_flood_hours"), "units": main["cost"]["units"], "tankers": main["cost"]["tankers"], "critical_within_target": km.get("critical_within_target")},
                                     "delta_points": int((km.get("points_served") or 0) - (ks.get("points_served") or 0)), "delta_cost_aed": round(float(main["cost"]["total_aed"] - silo["cost"]["total_aed"]), 0),
                                     "cross_zone_units": main.get("cross_zone_units"), "work_order_aed": main.get("work_order_aed")}
    pareto = []
    if with_pareto:
        for ob in OBJECTIVES:
            r = main if ob == objective else results.get(ob)
            if r is None:
                pareto.append({"objective": ob, "zh": OBJ_ZH[ob], "en": OBJ_EN[ob], "error": errors.get(ob, "failed"), "selected": ob == objective}); continue
            k = D1.kpis(r["plan"], r["pts"], t_decide, trt) if len(r["plan"]) else {"weighted_flood_hours": None, "points_served": 0}
            pareto.append({"objective": ob, "zh": OBJ_ZH[ob], "en": OBJ_EN[ob], "value_aed_per_priority_hour": r["value_aed_per_priority_hour"], "cost_aed": r["cost"]["total_aed"],
                           "weighted_flood_hours": k.get("weighted_flood_hours"), "points_served": k.get("points_served"), "units": r["cost"]["units"], "tankers": r["cost"]["tankers"],
                           "critical_within_target": k.get("critical_within_target"), "selected": ob == objective})
    main["pareto"] = pareto
    main["extras_parallel"] = parallel; main["extras_errors"] = errors or None
    return main


# ---------------------------------------------------------------- 重规划：现场报退干 / 泵故障 / 993 报案 → 锁定已出发、重解未出发
def match_incidents(incidents: list, pts0: pd.DataFrame, radius_m: float = 300.0) -> tuple[dict, list]:
    """993 报案 → 系统积水点。有 fp_id 直接用；只有经纬度就找 radius_m 内最近的点。返回 (fp_id → 报案列表, 未匹配报案)。"""
    from scipy.spatial import cKDTree
    matched: dict = {}; unmatched = []
    ok = pts0.dropna(subset=["lon", "lat"]) if len(pts0) else pts0
    tree = cKDTree(np.c_[ok["lon"].values * np.cos(np.radians(24.4)) * 111000, ok["lat"].values * 111000]) if len(ok) else None
    ids = set(pts0["fp_id"].astype(str)) if len(pts0) else set()
    for inc in incidents or []:
        fp = str(inc.get("fp_id") or "").strip()
        if fp and fp in ids:
            matched.setdefault(fp, []).append(inc); continue
        if tree is not None and inc.get("lon") is not None and inc.get("lat") is not None:
            d, i = tree.query([float(inc["lon"]) * np.cos(np.radians(24.4)) * 111000, float(inc["lat"]) * 111000])
            if d <= radius_m:
                matched.setdefault(str(ok.iloc[int(i)]["fp_id"]), []).append({**inc, "dist_m": round(float(d), 0)}); continue
        unmatched.append(inc)
    return matched, unmatched


def replan(ctx: dict, cfg: dict, base_plan: pd.DataFrame, t_decide: float, t_now: float, dry_points=None, failed_units=None, incidents=None, objective: str = "balanced", mode: str | None = None) -> dict:
    """在 t_now 重规划：depart_min ≤ t_now 的派单锁定（泵占用到退干），之后的作废重解；报退干的点移出；故障泵剔除；993 报案匹配到点后加权（核实 ×1.5，未核实 ×1.2）。"""
    P = params(cfg); dry = {str(x) for x in (dry_points or []) if str(x).strip()}; failed = {str(x) for x in (failed_units or []) if str(x).strip()}
    pts0 = ctx["pts0"]; horizon = t_decide + float(P["horizon_min"])
    base = base_plan if base_plan is not None else pd.DataFrame()
    locked, released, dropped = [], [], []
    unit_state: dict = {}
    if len(base):
        for r in base.to_dict("records"):
            uid = str(r.get("unit_id")); fp = str(r.get("fp_id"))
            if uid in failed:
                dropped.append({"unit_id": uid, "fp_id": fp, "why": "failed"}); continue
            if float(r.get("depart_min") or 0) > t_now:
                dropped.append({"unit_id": uid, "fp_id": fp, "why": "not departed"}); continue
            if fp in dry:                                                # 已退干：泵在原地释放
                released.append({"unit_id": uid, "fp_id": fp}); r = {**r, "released_min": round(t_now, 0)}
                if str(r.get("model")) != TANKER_MODEL:
                    ix = pts0.index[pts0["fp_id"].astype(str) == fp]
                    unit_state[uid] = {"busy_until": t_now, "at_pt": int(ix[0]) if len(ix) else None, "from": fp, "lon": r.get("lon"), "lat": r.get("lat")}
                locked.append(r); continue
            locked.append(r)
            if str(r.get("model")) != TANKER_MODEL:
                ix = pts0.index[pts0["fp_id"].astype(str) == fp]
                unit_state[uid] = {"busy_until": float(r["done_min"]) if r.get("done_min") is not None and not (isinstance(r.get("done_min"), float) and np.isnan(r.get("done_min"))) else horizon,
                                   "at_pt": int(ix[0]) if len(ix) else None, "from": fp, "lon": r.get("lon"), "lat": r.get("lat")}
    served_init = {str(r.get("fp_id")) for r in locked if str(r.get("fp_id")) not in dry} | dry
    matched, unmatched = match_incidents(list(incidents or []), pts0)
    boost = {fp: (1.5 if any(bool(i.get("verified")) for i in lst) else 1.2) for fp, lst in matched.items()}
    out = rolling(ctx, cfg, t_decide, objective, None, None, mode, t_start=t_now, unit_state=unit_state, unit_exclude=failed, served_init=served_init, priority_boost=boost, locked_rows=locked)
    new_pts = sorted({str(r["fp_id"]) for r in out["plan"].to_dict("records")} - {str(r.get("fp_id")) for r in locked}) if len(out["plan"]) else []
    base_pts = set(base["fp_id"].astype(str)) if len(base) else set()
    out["replan"] = {"t_now": t_now, "locked_units": len({r.get("unit_id") for r in locked}), "released_units": released, "dropped": dropped[:50], "n_dropped": len(dropped), "dry_points": sorted(dry), "failed_units": sorted(failed),
                     "incidents_matched": {k: len(v) for k, v in matched.items()}, "incidents_unmatched": len(unmatched), "priority_boost": boost,
                     "points_new": new_pts, "points_no_longer_served": sorted(base_pts - set(out["plan"]["fp_id"].astype(str)) - dry) if len(out["plan"]) else sorted(base_pts - dry)}
    return out


# ---------------------------------------------------------------- 单点最优（严格版）：把某点权重放大 1e4 重解，与全局解比
def focus_compare(ctx: dict, cfg: dict, t_decide: float, fp_id: str, base: dict | None, objective: str = "balanced", mode: str | None = None) -> dict:
    """返回：该点在全局解 / 单点最优解里的退干时刻与台数；为它抢来的泵原来在哪；哪些点因此没排上或推迟；费用差。"""
    g = base if base is not None else rolling(ctx, cfg, t_decide, objective, None, None, mode)
    f = rolling(ctx, cfg, t_decide, objective, None, fp_id, mode)
    gp, fpn = g["plan"], f["plan"]
    def pt_view(plan):
        if not len(plan):
            return {"served": False, "units": 0, "done_min": None}
        rows = plan[plan["fp_id"].astype(str) == str(fp_id)]
        return {"served": bool(len(rows)), "units": int(rows["unit_id"].nunique()), "done_min": (None if not len(rows) or rows["done_min"].isna().all() else float(rows["done_min"].max())),
                "pumping_from_min": (None if not len(rows) else float(rows["pumping_from_min"].min())), "package": (rows["package"].iloc[0] if len(rows) and "package" in rows.columns else None)}
    gv, fv = pt_view(gp), pt_view(fpn)
    f_units = set(fpn.loc[fpn["fp_id"].astype(str) == str(fp_id), "unit_id"]) if len(fpn) else set()
    taken = []
    if len(gp) and f_units:
        for _, r in gp[gp["unit_id"].isin(f_units) & (gp["fp_id"].astype(str) != str(fp_id))].iterrows():
            taken.append({"unit_id": r["unit_id"], "from_point": r["fp_id"], "model": r["model"]})
    g_done = gp.groupby("fp_id")["done_min"].max().to_dict() if len(gp) else {}; f_done = fpn.groupby("fp_id")["done_min"].max().to_dict() if len(fpn) else {}
    displaced = []
    for pfp, gd in g_done.items():
        if str(pfp) == str(fp_id):
            continue
        fd = f_done.get(pfp)
        if pfp not in f_done:
            displaced.append({"fp_id": pfp, "why": "not served", "delay_min": None})
        elif fd is not None and gd is not None and not (isinstance(fd, float) and np.isnan(fd)) and not (isinstance(gd, float) and np.isnan(gd)) and fd > gd + 1:
            displaced.append({"fp_id": pfp, "why": "later", "delay_min": round(float(fd - gd), 0)})
    return {"fp_id": fp_id, "objective": objective, "global": gv, "focus": fv,
            "delta_min": (None if gv["done_min"] is None or fv["done_min"] is None else round(float(gv["done_min"] - fv["done_min"]), 0)),
            "units_taken_from_global": taken, "displaced_points": displaced[:30], "n_displaced": len(displaced),
            "cost_global_aed": g["cost"]["total_aed"], "cost_focus_aed": f["cost"]["total_aed"], "delta_cost_aed": round(float(f["cost"]["total_aed"] - g["cost"]["total_aed"]), 0),
            "points_served_global": int(gp["fp_id"].nunique()) if len(gp) else 0, "points_served_focus": int(fpn["fp_id"].nunique()) if len(fpn) else 0}


# ---------------------------------------------------------------- 压力测试：同一张调度单放到更重的情景下会怎样
def stress_test(ctx_worse: dict, cfg: dict, plan: pd.DataFrame, t_decide: float, label: str = "") -> dict:
    """把当前情景的派单（泵、到达、开抽、排量）原样放到更重情景的过程线上重演：哪些点退不干 / 超时、还差多少排量与台数。"""
    pts_w = D1.feasibility(ctx_worse["pts0"], ctx_worse["cs"], ctx_worse["fleet"], cfg, t_decide)
    pts_w["level"], pts_w["level_priority"] = D1.emergency_level(pts_w)
    rate12 = max([float(m.get("rate_m3h") or 0) for m in ctx_worse["fleet"]["models"]] or [700.0])
    rows = []
    if plan is None or not len(plan):
        return {"error": "没有调度单"}
    for fp, g in plan.groupby("fp_id"):
        pw = pts_w[pts_w["fp_id"].astype(str) == str(fp)]
        if not len(pw):
            continue
        pw = pw.iloc[0]
        pumps = replay.pumps_from_plan(g)
        r = replay.replay(pw["hydrograph"], pumps, dt=5.0, t_end=t_decide + 2880.0)
        if r.get("error"):
            continue
        kp = r["kpi"]["pump"]; te = kp["t_empty_min"]; target = float(pw.get("target_drain_min") or 360.0)
        site_rate = float(g["rate_m3h"].sum()); need = float(pw.get("required_rate_m3h") or 0.0)
        gap = max(0.0, need - site_rate)
        rows.append({"fp_id": str(fp), "level": pw.get("level"), "priority": float(pw.get("priority") or 0), "units": int(g["unit_id"].nunique()), "site_rate_m3h": round(site_rate, 0),
                     "done_min": (None if te is None else float(te)), "within_target": bool(te is not None and (float(te) - t_decide) <= target), "never_empties": te is None,
                     "required_rate_m3h": round(need, 0), "rate_gap_m3h": round(gap, 0), "extra_12in_equiv": int(math.ceil(gap / rate12)) if gap > 0 else 0, "volume_peak_m3": kp["peak_m3"]})
    df = pd.DataFrame(rows)
    base_ok = int(plan.groupby("fp_id")["within_target"].max().sum()) if "within_target" in plan.columns else None
    return {"worse_scenario": label, "points_in_plan": int(len(df)), "within_target_now": base_ok, "within_target_worse": int(df["within_target"].sum()) if len(df) else 0,
            "never_empties_worse": int(df["never_empties"].sum()) if len(df) else 0, "points_with_gap": int((df["rate_gap_m3h"] > 0).sum()) if len(df) else 0,
            "extra_12in_equiv_total": int(df["extra_12in_equiv"].sum()) if len(df) else 0,
            "worst": (df.sort_values(["never_empties", "rate_gap_m3h"], ascending=[False, False]).head(12).to_dict("records") if len(df) else []),
            "basis": "同一张调度单（泵、到达、开抽、排量）放到更重情景的过程线上重演；缺口 = 更重情景需排量 − 在场排量，按最大泵型折算台数"}


# ---------------------------------------------------------------- 车次排程：有 ortools 就用 CP-SAT（并行机排程，最小化泵的等车时间），没有用贪心轮转
def schedule_cpsat(trips: list[dict], n_vehicles: int, time_limit_s: float = 5.0) -> dict | None:
    """trips: [{tid, earliest, duration}]；n_vehicles 辆同类车。返回 {tid: (vehicle_ix, start)} 或 None（无 ortools / 无解）。"""
    try:
        from ortools.sat.python import cp_model
    except Exception:
        return None
    if not trips or n_vehicles <= 0:
        return None
    m = cp_model.CpModel(); H = int(max(t["earliest"] + t["duration"] for t in trips) * 3 + 600)
    starts, ends, assign = {}, {}, {}
    for t in trips:
        tid = t["tid"]; e = int(round(t["earliest"])); d = int(round(max(t["duration"], 1)))
        starts[tid] = m.NewIntVar(e, H, f"s_{tid}"); ends[tid] = m.NewIntVar(e + d, H + d, f"e_{tid}")
        m.Add(ends[tid] == starts[tid] + d)
        for v in range(n_vehicles):
            assign[(tid, v)] = m.NewBoolVar(f"a_{tid}_{v}")
        m.AddExactlyOne(assign[(tid, v)] for v in range(n_vehicles))
    for v in range(n_vehicles):
        ivs = []
        for t in trips:
            tid = t["tid"]; d = int(round(max(t["duration"], 1)))
            ivs.append(m.NewOptionalIntervalVar(starts[tid], d, ends[tid], assign[(tid, v)], f"iv_{tid}_{v}"))
        m.AddNoOverlap(ivs)
    m.Minimize(sum(starts[t["tid"]] - int(round(t["earliest"])) for t in trips))
    solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds = float(time_limit_s); solver.parameters.num_workers = 4
    st = solver.Solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    out = {}
    for t in trips:
        tid = t["tid"]; v = next(v for v in range(n_vehicles) if solver.Value(assign[(tid, v)]))
        out[tid] = (v, float(solver.Value(starts[tid])))
    return out
