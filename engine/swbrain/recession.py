"""退水方案：一个积水点「什么时候退水、用什么手段最快退水」，并把单点最优与全局最优摆在一起。

围绕积水点的全生命周期，客户最终要的两个答案：
1. 什么时候退水——自然退水 / 全局最优调度给它的处置 / 只顾它一个点的最优处置 / 提前预置 / 罐车 / 换管，各自的退干时刻；
2. 用什么手段最快——上面各项里最快的那个，以及要在目标时限内排干需要几台泵；
并且说清「单点最优 vs 全局最优」的差：单点最优会从全局调度单里抢走哪些泵、让哪些点的退干推迟多少。
所有数字都来自同一套过程线与重演器（replay），与第三道页面一致；本模块不重算水力学。
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from . import dispatch as dp, replay


def _finite(x):
    try:
        return x is not None and np.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def point_row(ctx: dict, cfg: dict, fp_id: str, t_decide: float) -> tuple[pd.DataFrame, int]:
    """决策时刻的可行性帧（含等级），以及目标点的行号。"""
    pts = dp.feasibility(ctx["pts0"], ctx["cs"], ctx["fleet"], cfg, float(t_decide))
    pts["level"], pts["level_priority"] = dp.emergency_level(pts)
    hit = pts.index[pts["fp_id"].astype(str) == str(fp_id)]
    if not len(hit):
        raise KeyError(f"没有积水点 {fp_id}")
    return pts, int(hit[0])


def pump_candidates(ctx: dict, pi: int, cfg: dict) -> pd.DataFrame:
    """所有可用移动泵到该点的行程（从各自驻地出发，重车/轻车各自的路网矩阵）；行程 > max_travel_min 或不可达的剔除。"""
    units = ctx["units"].copy()
    units = units[~units["model"].astype(str).str.contains("tanker", case=False)]
    depot_ix = {d: i for i, d in enumerate(ctx["depots"]["depot_id"])}
    Th, Tl = ctx["T_heavy_d"], ctx["T_light_d"]
    trav = []
    for _, u in units.iterrows():
        di = depot_ix.get(u.get("from"))
        if di is None:
            trav.append(np.nan); continue
        T = Th if str(u.get("vehicle_class", "heavy")) == "heavy" else Tl
        trav.append(float(T[di, pi]) if T is not None and T.shape[1] > pi else np.nan)
    units["travel_min"] = trav
    units["avail_min"] = units.get("avail_min", 0.0)
    units["arr"] = units["travel_min"] + units["setup_min"].astype(float) + units["avail_min"].astype(float)
    ok = units["travel_min"].apply(_finite) & (units["travel_min"] <= float(cfg["dispatch"].get("max_travel_min", 90)))
    return units[ok].sort_values("arr")


def best_units(pts: pd.DataFrame, pi: int, cand: pd.DataFrame, n_max: int) -> tuple[list[int], float | None]:
    """只顾这一个点：从候选泵里挑最多 n_max 台，让排干时刻最早。枚举「最晚到位的是第 k 台」，在前 k 台里取排量最大的 n 台。"""
    pt = pts.loc[pi]
    if not len(cand):
        return [], None
    rf = float(pt.get("relay_factor", 1.0) or 1.0); stages = int(pt.get("relay_stages", 0) or 0)
    relay_extra = float(cand["setup_min"].mean()) * max(stages, 1) if bool(pt.get("relay_needed", False)) else 0.0
    V, q = float(pt["volume_now_m3"]), float(pt["inflow_now_m3h"])
    best_ix, best_done = [], None
    arr = cand["arr"].values; rate = cand["rate_m3h"].astype(float).values
    for n in range(1, int(n_max) + 1):
        for k in range(n, len(cand) + 1):
            top = np.argsort(-rate[:k], kind="stable")[:n]
            r = float(rate[top].sum()) * rf; net = r - q
            if net <= 0:
                continue
            done = float(arr[:k].max()) + relay_extra + V / net * 60.0
            if best_done is None or done < best_done - 1e-9:
                best_done, best_ix = done, [int(cand.index[i]) for i in top]
    return best_ix, best_done


def plan_for(pts: pd.DataFrame, pi: int, units: pd.DataFrame, unit_ix: list[int], travel: dict[int, float], t_decide: float) -> pd.DataFrame:
    if not unit_ix:
        return pd.DataFrame()
    assign = pd.DataFrame([{"pt_ix": pi, "unit_ix": ix, "travel_min": float(travel[ix])} for ix in unit_ix])
    return dp.plan_from_assignment(assign, pts, units, float(t_decide), 1)


def replay_rows(pt: pd.Series, rows: pd.DataFrame | None) -> dict:
    pumps = replay.pumps_from_plan(rows) if rows is not None and len(rows) else []
    r = replay.replay(pt.get("hydrograph") or [], pumps, max_depth_m=(float(pt.get("max_depth_m") or 0) or None))
    if r.get("error"):
        return {"t_empty_min": None, "flood_h": None, "peak_m3": None, "never_empties": True, "error": r["error"]}
    k = r["kpi"]["pump"] if pumps else r["kpi"]["base"]
    return {"t_empty_min": k.get("t_empty_min"), "flood_h": k.get("flood_h"), "peak_m3": k.get("peak_m3"), "never_empties": k.get("t_empty_min") is None, "pumped_total_m3": r["kpi"].get("pumped_total_m3") if pumps else 0.0}


def option_cost(rows: pd.DataFrame | None, t_empty_min, cfg: dict, models: dict, depart_min: float, fp_id: str) -> dict:
    """一种处置手段的指示性运维成本：台时 × 客户预案 A 小时单价 + 每台动员费（dispatch.plan_cost 同口径）。"""
    if rows is None or not len(rows):
        return {"total_aed": 0.0, "pump_hours": 0.0, "units": 0, "kind": "none"}
    rep_rows = [{"fp_id": fp_id, "replay_pump_end_min": t_empty_min}]
    c = dp.plan_cost(rows, rep_rows, float(depart_min), cfg, models)
    return {"total_aed": float(c.get("total_aed") or 0.0), "pump_hours": float(c.get("pump_hours") or 0.0), "units": int(c.get("units") or len(rows)), "kind": "运维（台时 + 动员）", "kind_en": "operations (pump-hours + mobilisation)"}


def upsize_cost(dn_mm, reach_len_m, cfg: dict) -> float | None:
    """换管造价：[cost].pipe_aed_per_m_by_dn 往上取档 × 管段长度（占位单价）。"""
    rate = ((cfg.get("cost") or {}).get("pipe_aed_per_m_by_dn") or {})
    if not rate or dn_mm is None or not _finite(reach_len_m):
        return None
    key = min((k for k in rate if float(k) >= float(dn_mm)), key=float, default=max(rate, key=float))
    return round(float(rate[key]) * float(reach_len_m), 0)


def _units_brief(rows: pd.DataFrame | None) -> list[dict]:
    if rows is None or not len(rows):
        return []
    return [{"unit_id": r.get("unit_id"), "model": r.get("model"), "from": r.get("from"), "arrive_min": r.get("arrive_min"), "pumping_from_min": r.get("pumping_from_min"), "rate_m3h": r.get("rate_m3h")} for _, r in rows.iterrows()]


def wait_until(pt: pd.Series, deployable_rate: float, t_from: float) -> float | None:
    """入流大于可派排量时：过程线上入流第一次降到可派排量 80% 以下的时刻（雨峰过后再派）。"""
    hyd = pt.get("hydrograph") or []
    if not hyd:
        return None
    last = max(float(h["t_min"]) for h in hyd)
    for h in hyd:
        if float(h["t_min"]) >= t_from and float(h.get("inflow_m3h", 0)) < 0.8 * deployable_rate:
            return float(h["t_min"]) if float(h["t_min"]) < last else None      # 直到过程线结束都压不住 → 没有可等的时刻
    return None


def options(ctx: dict, cfg: dict, fp_id: str, t_decide: float, plan_global: pd.DataFrame | None, plan_rows_payload: list[dict] | None = None, upsize: dict | None = None, scenario: dict | None = None) -> dict:
    D = cfg["dispatch"]; cap = int(D.get("max_units_per_point", 3)); depart = float(D.get("preposition_depart_min", 30))
    pts, pi = point_row(ctx, cfg, fp_id, t_decide)
    pt = pts.loc[pi]
    units = ctx["units"]
    cand = pump_candidates(ctx, pi, cfg)
    travel = dict(zip(cand.index, cand["travel_min"]))
    target = float(pt.get("target_drain_min") or 360.0)
    deadline = float(t_decide) + target + 60.0
    disp = pts[pts["dispatchable"].astype(bool)]
    rank = int((disp["priority"] > float(pt["priority"])).sum()) + 1 if bool(pt.get("dispatchable")) else None
    opts: list[dict] = []

    models = {m["model"]: m for m in (ctx.get("fleet") or {}).get("models") or []}

    def add(key, zh, en, rows=None, note_zh="", note_en="", depart_at=None, **extra):
        k = replay_rows(pt, rows)
        o = {"key": key, "label_zh": zh, "label_en": en, "units": _units_brief(rows), "n_units": int(len(rows)) if rows is not None else 0, **k,
             "within_target": (bool(k["t_empty_min"] is not None and float(k["t_empty_min"]) <= deadline)), "note_zh": note_zh, "note_en": note_en, **extra}
        c = option_cost(rows, k.get("t_empty_min"), cfg, models, float(t_decide if depart_at is None else depart_at), str(fp_id))
        o["cost_aed"] = c["total_aed"]; o["pump_hours"] = c["pump_hours"]; o["cost_kind"] = c.get("kind"); o["cost_kind_en"] = c.get("kind_en")
        opts.append(o); return o

    # 1. 自然退水
    add("natural", "不派（自然退水）", "No action (natural recession)", None)
    # 2. 全局最优给它的
    rows_g = plan_global[plan_global["fp_id"].astype(str) == str(fp_id)] if plan_global is not None and len(plan_global) else pd.DataFrame()
    if len(rows_g):
        add("global", "全局最优调度给它的", "Global-optimal dispatch", rows_g)
    else:
        add("global", "全局最优调度给它的", "Global-optimal dispatch", None, note_zh=f"本轮没排上：{pt.get('triage')}" + (f"，可派点里优先级第 {rank}/{len(disp)}" if rank else ""), note_en=f"not served this round: {pt.get('triage')}" + (f", priority rank {rank}/{len(disp)} among dispatchable" if rank else ""))
    # 3. 单点最优：现有泵队里挑最多 cap 台只给它
    ix, done_s = best_units(pts, pi, cand, cap) if bool(pt.get("discharge_ok", False)) and float(pt["volume_now_m3"]) >= 50 else ([], None)
    rows_s = plan_for(pts, pi, units, ix, travel, t_decide)
    if len(rows_s):
        add("single", f"单点最优（现有泵队里最多 {cap} 台只给它）", f"Single-point optimum (up to {cap} pumps just for it)", rows_s)
    else:
        why_zh = "已基本退水，不用派" if float(pt["volume_now_m3"]) < 50 else ("1.5 km 内无可接纳的排放去处，泵没地方排" if not bool(pt.get("discharge_ok", False)) else ("没有 90 min 内能到的泵（路网不可达或太远）" if not len(cand) else "入流大于可派排量，泵压不住"))
        why_en = "already receded" if float(pt["volume_now_m3"]) < 50 else ("no acceptable discharge point within 1.5 km" if not bool(pt.get("discharge_ok", False)) else ("no pump can reach it within 90 min (unreachable or too far)" if not len(cand) else "inflow exceeds deployable rate"))
        if not bool(pt.get("discharge_ok", False)) and not len(cand):
            why_zh += "；且没有 90 min 内能到的泵"; why_en += "; and no pump can reach it within 90 min"
        add("single", f"单点最优（现有泵队里最多 {cap} 台只给它）", f"Single-point optimum (up to {cap} pumps just for it)", None, note_zh=why_zh, note_en=why_en)
    # 4. 预置最优：同样的泵提前放在最近的候选驻地，事件开始后 depart 分钟出发
    T_sites, sites = ctx.get("T_sites"), ctx.get("sites")
    if T_sites is not None and sites is not None and len(sites) and T_sites.shape[1] > pi and bool(pt.get("discharge_ok", False)):
        col = np.asarray(T_sites[:, pi], float)
        if np.isfinite(col).any():
            si = int(np.nanargmin(np.where(np.isfinite(col), col, np.inf))); trav_s = float(col[si])
            pool = cand.sort_values("rate_m3h", ascending=False) if len(cand) else units[~units["model"].astype(str).str.contains("tanker", case=False)].sort_values("rate_m3h", ascending=False)
            top = pool.head(cap)
            if len(top):
                rf = float(pt.get("relay_factor", 1.0) or 1.0); stages = int(pt.get("relay_stages", 0) or 0)
                setup = float(top["setup_min"].mean()); relay_extra = setup * max(stages, 1) if bool(pt.get("relay_needed", False)) else 0.0
                rows_p = pd.DataFrame([{"fp_id": str(fp_id), "unit_id": u["unit_id"], "model": u["model"], "rate_m3h": round(float(u["rate_m3h"]) * rf, 1), "from": str(sites.iloc[si]["site_id"]), "arrive_min": round(depart + trav_s, 0),
                                        "pumping_from_min": round(depart + trav_s + setup + relay_extra, 0)} for _, u in top.iterrows()])
                add("prepo", f"预置最优（提前放在最近驻地，{depart:g} min 出发）", f"Pre-positioned optimum (staged at the nearest site, depart at {depart:g} min)", rows_p, depart_at=depart,
                    site={"site_id": str(sites.iloc[si]["site_id"]), "name": str(sites.iloc[si]["name"]), "travel_min": round(trav_s, 1)})
    # 5. 罐车（无排放去处的点）
    if not bool(pt.get("discharge_ok", False)) and float(pt["volume_now_m3"]) >= 50:
        P = dp.tanker_params(cfg)
        if P["enabled"]:
            rate1, cycle = dp.tanker_rate_m3h(cfg, pt.get("discharge_dist_m"))
            n_tk = int(min(P["max_per_point"], max(1, int(np.ceil(float(pt["required_rate_m3h"]) / max(rate1, 1e-6))))))
            Th = ctx["T_heavy_d"]; col = np.asarray(Th[:, pi], float) if Th is not None and Th.shape[1] > pi else np.array([np.nan])
            if np.isfinite(col).any():
                trav_t = float(np.nanmin(np.where(np.isfinite(col), col, np.inf)))
                arrive = float(t_decide) + trav_t + P["load_min"]
                rows_t = pd.DataFrame([{"fp_id": str(fp_id), "unit_id": f"TK-{i+1}", "model": P["model"], "rate_m3h": round(rate1, 1), "from": "nearest depot", "arrive_min": round(arrive, 0), "pumping_from_min": round(arrive, 0), "duty_aed_h": P["duty_aed_h"]} for i in range(n_tk)])
                add("tankers", f"罐车（{n_tk} 辆，周转 {cycle:.0f} min）", f"Tankers ({n_tk}, {cycle:.0f} min cycle)", rows_t, note_zh="无排放去处：只能拉走或等管网退水", note_en="no discharge point: haul away or wait for the network")
    # 6. 换管（长期）
    if upsize and upsize.get("upsized"):
        for o in upsize["upsized"]:
            k = o.get("kpi") or {}
            cap_aed = upsize_cost(o.get("dn_mm"), upsize.get("reach_len_m"), cfg)
            opts.append({"key": f"upsize_{o.get('dn_mm')}", "label_zh": f"换管 DN{o.get('dn_mm')}（工程）", "label_en": f"Upsize to DN{o.get('dn_mm')} (capital)", "units": [], "n_units": 0, "t_empty_min": k.get("t_empty_min"), "flood_h": k.get("flood_h"), "peak_m3": k.get("peak_m3"),
                         "never_empties": k.get("t_empty_min") is None, "within_target": bool(k.get("t_empty_min") is not None and float(k["t_empty_min"]) <= deadline) or float(o.get("shortfall_m3") or 0) <= 0, "shortfall_m3": o.get("shortfall_m3"), "q_full_m3s": o.get("q_full_m3s"),
                         "cost_aed": cap_aed, "pump_hours": 0.0, "cost_kind": "工程（换管造价，占位单价 × 管段长）", "cost_kind_en": "capital (pipe rate × reach length, placeholder rates)",
                         "note_zh": ("换后不再积水" if float(o.get("shortfall_m3") or 0) <= 0 else f"缺口仍有 {o.get('shortfall_m3'):,.0f} m³"), "note_en": ("no flooding after upsizing" if float(o.get("shortfall_m3") or 0) <= 0 else f"shortfall still {o.get('shortfall_m3'):,.0f} m³")})

    # 要几台才能在目标时限内排干（只顾它）
    need = None
    if bool(pt.get("discharge_ok", False)) and float(pt["volume_now_m3"]) >= 50 and len(cand):
        for n in range(1, min(8, len(cand)) + 1):                     # 与方案表同口径：按重演的退干时刻判达标（静态公式忽略入流随时间下降，偏保守）
            ix_n, done_n = best_units(pts, pi, cand, n)
            if done_n is None:
                continue
            rows_n = plan_for(pts, pi, units, ix_n, travel, t_decide); k = replay_rows(pt, rows_n)
            if k["t_empty_min"] is not None and float(k["t_empty_min"]) <= deadline:
                need = {"n": n, "done_min": round(float(k["t_empty_min"]), 0), "t_empty_min": k["t_empty_min"], "units": _units_brief(rows_n)}
                break
        if need is None:
            need = {"n": None, "reason_zh": ("最多 8 台也压不住入流 / 接力折减太大" if not bool(pt.get("pump_worthy", True)) or float(pt.get("relay_factor", 1) or 1) < 0.6 else "最近的泵到得太晚"), "reason_en": ("even 8 pumps cannot beat the inflow / relay derating" if not bool(pt.get("pump_worthy", True)) or float(pt.get("relay_factor", 1) or 1) < 0.6 else "nearest pumps arrive too late")}
    # 入流太大：等到什么时候再派
    wait = None
    if not bool(pt.get("pump_worthy", True)):
        max_rate = max(m["rate_m3h"] for m in ctx["fleet"]["models"]) * cap
        wait = wait_until(pt, max_rate, float(t_decide))

    # 单点最优对全局的代价：抢走的泵原来在全局调度单里服务谁
    single = next((o for o in opts if o["key"] == "single"), None); glob = next((o for o in opts if o["key"] == "global"), None)
    taken = {u["unit_id"] for u in (single or {}).get("units", [])}
    displaced = []
    saved_map = {str(r.get("fp_id")): r.get("replay_saved_min") for r in (plan_rows_payload or []) if r.get("replay_saved_min") is not None}
    if taken and plan_global is not None and len(plan_global):
        g = plan_global[plan_global["unit_id"].astype(str).isin(taken) & (plan_global["fp_id"].astype(str) != str(fp_id))]
        for f in sorted(set(g["fp_id"].astype(str))):
            displaced.append({"fp_id": f, "units": sorted(set(g[g["fp_id"].astype(str) == f]["unit_id"].astype(str))), "saved_min_lost": saved_map.get(f)})
    delta = None
    if single and glob and single.get("t_empty_min") is not None:
        delta = (float(glob["t_empty_min"]) - float(single["t_empty_min"])) if glob.get("t_empty_min") is not None else None
    svg = {"delta_min": (round(delta, 0) if delta is not None else None), "global_never": bool(glob and glob.get("never_empties")), "units_taken_from_global": sorted(taken & set(plan_global["unit_id"].astype(str)) if plan_global is not None and len(plan_global) else set()),
           "displaced_points": displaced, "displaced_saved_min_total": round(float(sum((d["saved_min_lost"] or 0) for d in displaced)), 0)}

    # 结论
    finite = [o for o in opts if o.get("t_empty_min") is not None and not o["key"].startswith("upsize")]
    fastest = min(finite, key=lambda o: float(o["t_empty_min"])) if finite else None
    meets = [o["key"] for o in opts if o.get("within_target")]
    rec = _recommend(pt, opts, fastest, meets, need, wait, svg, target, t_decide)
    sc = scenario or {}
    costs = {o["key"]: o.get("cost_aed") for o in opts}
    return {"fp_id": str(fp_id), "scenario_key": sc.get("key"), "scenario_label": sc.get("label"), "scenario_label_en": sc.get("label_en"), "decision_time_min": float(t_decide), "target_drain_min": target, "deadline_min": deadline, "costs_aed": costs,
            "lon": (float(pt["lon"]) if _finite(pt.get("lon")) else None), "lat": (float(pt["lat"]) if _finite(pt.get("lat")) else None),
            "point": {"location": pt.get("location"), "criticality": pt.get("client_criticality"), "level": pt.get("level"), "top_kind": pt.get("top_kind"), "volume_now_m3": float(pt["volume_now_m3"]), "inflow_now_m3h": float(pt["inflow_now_m3h"]), "volume_peak_m3": float(pt.get("volume_m3") or 0),
                      "triage": pt.get("triage"), "dispatchable": bool(pt.get("dispatchable")), "discharge_to": pt.get("discharge_to"), "discharge_dist_m": (float(pt["discharge_dist_m"]) if _finite(pt.get("discharge_dist_m")) else None), "relay_stages": int(pt.get("relay_stages", 0) or 0), "relay_factor": float(pt.get("relay_factor", 1.0) or 1.0),
                      "required_rate_m3h": float(pt.get("required_rate_m3h") or 0), "priority": float(pt["priority"]), "priority_rank_dispatchable": rank, "n_dispatchable": int(len(disp)), "n_candidate_pumps": int(len(cand))},
            "options": opts, "fastest": (fastest["key"] if fastest else None), "meets_target": meets, "need_units_for_target": need, "wait_until_min": wait, "single_vs_global": svg, "recommendation": rec}


def _h(m):
    return "—" if m is None else f"{float(m) / 60:.1f} h"


def _recommend(pt, opts, fastest, meets, need, wait, svg, target, t_decide) -> dict:
    by = {o["key"]: o for o in opts}
    nat, glob, single, prepo = by.get("natural"), by.get("global"), by.get("single"), by.get("prepo")
    zh, en = [], []
    if nat and nat.get("t_empty_min") is not None:
        zh.append(f"不派也会在事件开始后 {_h(nat['t_empty_min'])} 退干（积水 {nat['flood_h']} h）。"); en.append(f"Left alone it recedes at {_h(nat['t_empty_min'])} after event start ({nat['flood_h']} h flooded).")
    else:
        zh.append("不派的话 48 h 内退不干。"); en.append("Left alone it does not recede within 48 h.")
    if glob:
        if glob["n_units"]:
            zh.append(f"全局最优调度给了 {glob['n_units']} 台，{_h(glob['t_empty_min'])} 退干，{'达标' if glob['within_target'] else '未达标'}。"); en.append(f"The global-optimal dispatch gives it {glob['n_units']} unit(s), drained at {_h(glob['t_empty_min'])}, {'within target' if glob['within_target'] else 'late'}.")
        else:
            zh.append(f"全局最优调度没给它泵（{glob.get('note_zh')}）。"); en.append(f"The global-optimal dispatch gives it nothing ({glob.get('note_en')}).")
    if single and single["n_units"]:
        d = svg.get("delta_min"); n_disp = len(svg.get("displaced_points") or [])
        tot = float(svg.get("displaced_saved_min_total") or 0)
        cost_zh = (f"；代价是从 {n_disp} 个点抢走 {len(svg['units_taken_from_global'])} 台泵（{'、'.join(x['fp_id'] for x in svg['displaced_points'][:3])}），" + (f"那些点退干合计推迟 {_h(tot)}。" if tot > 0 else "重演里那台泵并没有改变那些点的退干时刻，但它们的积水时长会拉长。")) if svg.get("units_taken_from_global") else "；用的泵全局调度里本来空着，不抢别人。"
        cost_en = (f"; the price is taking {len(svg['units_taken_from_global'])} pump(s) from {n_disp} other point(s) ({', '.join(x['fp_id'] for x in svg['displaced_points'][:3])}), " + (f"delaying their drainage by {_h(tot)} in total." if tot > 0 else "which in the replay did not change their drain time but does lengthen their flooded hours.")) if svg.get("units_taken_from_global") else "; those pumps are idle in the global plan, nobody loses."
        zh.append(f"只顾它一个点，现有泵队最多能做到 {_h(single['t_empty_min'])} 退干（{single['n_units']} 台）" + (f"，比全局给的快 {_h(d)}" if d is not None and d > 0 else ("，与全局一样" if d is not None else "")) + cost_zh)
        en.append(f"For this point alone the fleet can drain it by {_h(single['t_empty_min'])} ({single['n_units']} units)" + (f", {_h(d)} sooner than the global plan" if d is not None and d > 0 else (", same as the global plan" if d is not None else "")) + cost_en)
    elif single:
        zh.append(f"事中派泵做不到：{single.get('note_zh')}。"); en.append(f"Dispatch during the event cannot: {single.get('note_en')}.")
    if prepo and prepo.get("t_empty_min") is not None:
        zh.append(f"若提前预置在 {prepo['site']['name']}（{prepo['site']['travel_min']} min 车程），{_h(prepo['t_empty_min'])} 就能退干，{'达标' if prepo['within_target'] else '仍未达标'}。"); en.append(f"Pre-positioned at {prepo['site']['name']} ({prepo['site']['travel_min']} min away) it drains by {_h(prepo['t_empty_min'])}, {'within target' if prepo['within_target'] else 'still late'}.")
    if need and need.get("n"):
        zh.append(f"要在目标 {target:g} min 内排干，只顾它需要 {need['n']} 台（{_h(need['done_min'])} 排干）。"); en.append(f"To meet the {target:g} min target it needs {need['n']} unit(s) on its own (drained at {_h(need['done_min'])}).")
    elif need:
        zh.append(f"目标 {target:g} min 内排干做不到：{need.get('reason_zh')}。"); en.append(f"The {target:g} min target is out of reach: {need.get('reason_en')}.")
    if wait is not None:
        zh.append(f"入流仍大于可派排量，等到事件开始后 {_h(wait)} 雨峰过了再派。"); en.append(f"Inflow still exceeds the deployable rate; dispatch after the peak, around {_h(wait)} after event start.")
    ups = [o for o in opts if o["key"].startswith("upsize")]
    if ups:
        best = min(ups, key=lambda o: (float(o.get("shortfall_m3") or 0), float(o.get("t_empty_min") or 1e9)))
        zh.append(f"长期看，{best['label_zh'].replace('（工程）', '')}后{best['note_zh']}{'' if best['t_empty_min'] is None else f'，{_h(best['t_empty_min'])} 退干'}。"); en.append(f"Long term, {best['label_en'].replace(' (capital)', '')}: {best['note_en']}{'' if best['t_empty_min'] is None else f', drained by {_h(best['t_empty_min'])}'}.")
    if fastest:
        cz = f"，费用约 {fastest['cost_aed']:,.0f} AED（{fastest.get('cost_kind') or ''}）" if fastest.get("cost_aed") else ""
        ce = f", cost about {fastest['cost_aed']:,.0f} AED ({fastest.get('cost_kind_en') or ''})" if fastest.get("cost_aed") else ""
        zh.insert(0, f"最快退水的手段：{fastest['label_zh']}，{_h(fastest['t_empty_min'])} 退干{'，达标' if fastest['within_target'] else '，仍超过目标时限'}{cz}。"); en.insert(0, f"Fastest recession: {fastest['label_en']}, drained by {_h(fastest['t_empty_min'])}{', within target' if fastest['within_target'] else ', still past target'}{ce}.")
    return {"zh": " ".join(zh), "en": " ".join(en)}
