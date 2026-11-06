"""v2 调度引擎：资源盘点（模拟）、处置包枚举、单元类聚合 MILP（班组 / 车次 / 软管约束）、滚动释放、费用与帕累托。合成小算例，不读库。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json

import numpy as np
import pandas as pd

from swbrain import dispatch as D1, dispatch2 as D2, resources as RS


def _cfg():
    return {"dispatch": {"decision_time_min": 60, "facility_radius_m": 300, "discharge_radius_m": 1500, "relay_beyond_m": 300, "max_units_per_point": 3, "max_travel_min": 90, "tie_penalty": 0.02, "waves": 3, "slow_factor": 3.0,
                         "relay_hose_m": 100, "relay_rate_factor": 0.85, "relay_min_factor": 0.4, "impassable_depth_m": {"light": 0.3, "heavy": 0.45},
                         "target_drain_min": {"tunnel": 180, "default": 360}, "facility_weight": {"tunnel": 8}, "manual_levels": {"trt_min": 120, "mobilization_min": 30},
                         "cost": {"mobilisation_aed_per_unit": 500, "min_duty_h": 4, "default_duty_aed_h": 60, "horizon_min": 2880},
                         "tankers": {"enabled": True, "capacity_m3": 17, "duty_aed_h": 250, "load_min": 10, "unload_min": 10, "speed_kmh": 30, "default_haul_km": 5, "max_haul_km": 15, "horizon_min": 2880, "max_per_point": 6},
                         "v2": {"window_min": 60, "horizon_min": 600, "max_windows": 4, "package_dt_min": 10, "milp_time_limit_s": 10},
                         "resources": {"simulated": True, "crew_per_unit_if_missing": 2.0, "on_duty_share": 1.0, "hose_m_per_unit": 200, "fuel_l_per_unit": 300}},
            "model": {"scenarios": {"常规": {}}}}


def _hyd(peak_m3, t_peak=120, t_end=900, inflow_peak=600.0):
    rows = []
    for t in range(0, t_end + 1, 30):
        v = peak_m3 * (t / t_peak if t <= t_peak else max(0.0, 1 - (t - t_peak) / (t_end - t_peak)))
        q = inflow_peak * (t / t_peak) if t <= t_peak else max(0.0, inflow_peak * (1 - (t - t_peak) / 120.0))
        rows.append({"t_min": t, "volume_m3": round(v, 1), "inflow_m3h": round(q, 1)})
    return rows


def _ctx(cfg, n_units12=2, n_units8=2, tankers=0, crews=None):
    pts0 = pd.DataFrame([
        {"fp_id": "A", "lon": 54.50, "lat": 24.40, "priority": 300.0, "top_kind": "tunnel", "target_drain_min": 180.0, "volume_m3": 3000.0, "duration_min": 900, "hydrograph": _hyd(3000), "land_use": "tunnel", "note": "", "geom": None, "facilities": [], "max_depth_m": 1.0},
        {"fp_id": "B", "lon": 54.51, "lat": 24.41, "priority": 60.0, "top_kind": "none", "target_drain_min": 360.0, "volume_m3": 800.0, "duration_min": 900, "hydrograph": _hyd(800, inflow_peak=200), "land_use": "", "note": "", "geom": None, "facilities": [], "max_depth_m": 0.4},
        {"fp_id": "C", "lon": 54.60, "lat": 24.50, "priority": 40.0, "top_kind": "none", "target_drain_min": 360.0, "volume_m3": 600.0, "duration_min": 900, "hydrograph": _hyd(600, inflow_peak=150), "land_use": "", "note": "", "geom": None, "facilities": [], "max_depth_m": 0.3},
    ])
    models = [{"model": "diesel-12in", "rate_m3h": 700, "head_m": 15, "hose_m": 100, "setup_min": 25, "crew": 3, "weight_t": 8.0, "vehicle_class": "heavy", "duty_aed_h": 90},
              {"model": "diesel-8in", "rate_m3h": 350, "head_m": 15, "hose_m": 100, "setup_min": 20, "crew": 2, "weight_t": 4.0, "vehicle_class": "light", "duty_aed_h": 60}]
    units = [{"unit_id": f"P12-{i}", "model": "diesel-12in", "depot_id": "D1", "status": "available", "lon": 54.49, "lat": 24.39} for i in range(n_units12)] + \
            [{"unit_id": f"P8-{i}", "model": "diesel-8in", "depot_id": "D1", "status": "available", "lon": 54.49, "lat": 24.39} for i in range(n_units8)]
    depots = [{"depot_id": "D1", "name": "Depot 1", "area": "Zayed", "lon": 54.49, "lat": 24.39, "capacity_units": 10, "plan_a": {"manpower": crews if crews is not None else 0, "tankers": tankers}}]
    fleet = {"models": models, "units": units, "depots": depots}
    mm = {m["model"]: m for m in models}
    udf = pd.DataFrame([{**u, **{k: v for k, v in mm[u["model"]].items() if k != "model"}, "from": u["depot_id"], "avail_min": 0.0} for u in units])
    # 排放去处：A、B 附近有出水口；C 没有（只能罐车）
    cs = pd.DataFrame([{"terminal_unitid": "OF1", "terminal_kind": "出水口", "lon": 54.505, "lat": 24.405, "over_share_常规": 0.1}])
    T_d = np.array([[10.0, 15.0, 40.0]]); T_pp = np.array([[0.0, 8.0, 30.0], [8.0, 0.0, 25.0], [30.0, 25.0, 0.0]])
    return {"fp": {"points": [], "scenario": {}}, "fleet": fleet, "pts0": pts0, "units": udf, "depots": pd.DataFrame(depots), "cs": cs, "hotspots": None,
            "T_heavy_d": T_d, "T_light_d": T_d, "T_pp": T_pp, "T_sites": T_d, "sites": pd.DataFrame(depots).rename(columns={"depot_id": "site_id"}), "travel_method": "test", "travel_ms": 0}


def test_resources_build_simulated_and_override():
    cfg = _cfg(); ctx = _ctx(cfg, crews=0)
    res = RS.build(ctx["fleet"], None, cfg)
    d = res["depots"]["D1"]
    assert res["simulated"] and d["n_units"] == 4 and d["vehicles"] == {"heavy": 1, "light": 1} and d["crews"]["persons"] == 8 and d["crews"]["source"] == "simulated"     # 4 台 × 2 人
    assert d["materials"]["hose_m"] == 800 and d["materials"]["fuel_l"] == 1200 and res["totals"]["units"] == 4
    ctx2 = _ctx(cfg, crews=20); res2 = RS.build(ctx2["fleet"], None, cfg)
    assert res2["depots"]["D1"]["crews"]["persons"] == 20 and res2["depots"]["D1"]["crews"]["source"] == "plan_a"
    ctx2["fleet"]["depots"][0]["resources"] = {"vehicles": {"heavy": 5, "light": 5}, "persons": 40}
    res3 = RS.build(ctx2["fleet"], None, cfg)
    assert res3["depots"]["D1"]["vehicles"]["heavy"] == 5 and res3["depots"]["D1"]["crews"]["persons"] == 40 and not res3["simulated"]


def test_packages_and_window_solve_respect_crews():
    cfg = _cfg(); ctx = _ctx(cfg, n_units12=2, n_units8=2, crews=5)                   # 只有 5 人：12 寸要 3 人、8 寸 2 人 → 最多一台 12 寸 + 一台 8 寸
    res = RS.build(ctx["fleet"], None, cfg); P = D2.params(cfg); t = 60.0
    pts = D1.feasibility(ctx["pts0"], ctx["cs"], ctx["fleet"], cfg, t); pts["level"], pts["level_priority"] = D1.emergency_level(pts)
    units = ctx["units"].copy(); units["at_pt"] = -1; units["busy_until"] = np.nan
    classes = D2.build_classes(units, ctx["depots"], res, {**ctx, "cfg": cfg}, t)
    assert {c["model"] for c in classes} == {"diesel-12in", "diesel-8in"} and all(c["count"] == 2 for c in classes)
    cand = pts[pts["dispatchable"]]
    assert set(cand["fp_id"]) >= {"A", "B"}
    pkgs = {int(p): D2.point_packages(int(p), cand.loc[p], ctx["fleet"]["models"], classes, res, 100.0, cfg, P, t, False) for p in cand.index}
    gA = pkgs[int(cand.index[cand["fp_id"] == "A"][0])]
    assert any(g["need"] == {"diesel-12in": 2} for g in gA) and all(g["dH"] <= 0 for g in gA) and all(g["cost_aed"] > 0 for g in gA)
    depot_state = {"D1": {"crews_avail": 5, "vehicles": {"heavy": 1, "light": 1}, "hose_m": 800.0, "fuel_l": 1200.0}}
    chosen, alloc, meta = D2.solve_window(cand, classes, pkgs, res, 100.0, 1.0, P, depot_state, None, None)
    assert meta["status"] == "optimal", meta
    crew_used = sum(next(c for c in classes if c["cid"] == cid)["crew"] * n for (cid, p), n in alloc.items())
    assert 0 < crew_used <= 5                                                            # 班组人数是硬约束
    # 放开人手：能派更多
    depot_state["crews_avail"] = 100
    chosen2, alloc2, meta2 = D2.solve_window(cand, classes, pkgs, res, 100.0, 1.0, P, {"D1": {"crews_avail": 100, "vehicles": {"heavy": 3, "light": 3}, "hose_m": 8000.0, "fuel_l": 99999.0}}, None, None)
    assert sum(alloc2.values()) >= sum(alloc.values()) and len(chosen2) >= len(chosen)


def test_rolling_and_pareto_order():
    cfg = _cfg(); ctx = _ctx(cfg, n_units12=3, n_units8=3, tankers=4, crews=40)
    out = D2.solve(ctx, cfg, 60.0, "balanced", with_pareto=True)
    plan = out["plan"]
    assert len(plan) and set(plan["fp_id"]) >= {"A"} and "cost_aed" in plan.columns and "package" in plan.columns
    assert out["cost"]["total_aed"] > 0 and out["utilisation"]["D1"]["units_used"] >= 1 and len(out["windows"]) >= 1
    # C 没有排放去处 → 只能罐车（泵没地方排），而且只能是 C；罐车池 4 辆
    tk = plan[plan["model"] == D2.TANKER_MODEL]
    assert len(tk) and set(tk["fp_id"]) == {"C"} and len(tk) <= 4
    assert not len(plan[(plan["fp_id"] == "C") & (plan["model"] != D2.TANKER_MODEL)])      # C 不能被派泵
    pa = {x["objective"]: x for x in out["pareto"]}
    assert set(pa) == {"fastest", "balanced", "cheapest"} and pa["balanced"]["selected"]
    assert pa["cheapest"]["cost_aed"] <= pa["fastest"]["cost_aed"] + 1e-6                 # 最省钱不比最快贵
    assert (pa["fastest"]["weighted_flood_hours"] or 0) <= (pa["cheapest"]["weighted_flood_hours"] or 0) + 1e-6
    # 预算约束：非绑定的预算不得改变结果（2026-09-23：目标函数里的到达惩罚曾被当成钱算进预算行）
    free = D2.solve(ctx, cfg, 60.0, "fastest", with_pareto=False)
    c0 = float(free["cost"]["total_aed"])
    loose = D2.solve(ctx, cfg, 60.0, "fastest", budget_aed=c0 * 1.5, with_pareto=False)
    assert len(loose["plan"]) == len(free["plan"]) and abs(float(loose["cost"]["total_aed"]) - c0) < 1.0
    # 绑定的预算必须真的压住费用
    out_b = D2.solve(ctx, cfg, 60.0, "fastest", budget_aed=c0 * 0.5, with_pareto=False)
    assert float(out_b["cost"]["total_aed"]) <= c0 * 0.5 * 1.6                          # 车公里等在包成本之外，允许小幅超出


def test_schedule_and_vehicle_wait():
    cfg = _cfg(); ctx = _ctx(cfg, n_units12=3, n_units8=0, crews=40)
    out = D2.rolling(ctx, cfg, 60.0, "fastest")
    sch = out["schedule"]; plan = out["plan"]
    d = sch["depots"].get("D1")
    assert d is not None and d["trips"] >= 1 and d["vehicles"]["heavy"] == 2               # 3 台 12 寸 → 2 辆重卡
    assert "vehicle_wait_min" in plan.columns and plan["vehicle_wait_min"].max() >= 0
    assert d["persons_needed"] == 3 * int(plan["unit_id"].nunique())                     # 12 寸泵每台 3 人


def test_contractor_silo_vs_pooled():
    """承包商分区：各管各的（silo）时 A 区驻地不能派到 B 区的点；集中调度（pooled）可以跨区，每台加工单费，并给出多压住几个点。"""
    import pandas as pd
    cfg = _cfg(); cfg["dispatch"]["contractors"] = {"mode": "pooled", "work_order_aed": 2000, "compare_silo": True}
    cfg["dispatch"]["resources"].update({"hose_m_per_unit": 3000, "fuel_l_per_unit": 5000})          # 软管 / 燃油给足，只考承包商分区
    ctx = _ctx(cfg, n_units12=3, n_units8=2, crews=40)
    # 把 B 点划到另一个片区（客户清单 area=Other），驻地 D1 只管 Zayed
    ctx["hotspots"] = pd.DataFrame([{"hotspot_id": "A", "area": "Zayed", "region": "ADM"}, {"hotspot_id": "B", "area": "Other", "region": "ADM"}, {"hotspot_id": "C", "area": "Other", "region": "ADM"}])
    ctx["pts0"]["region"] = "ADM"
    silo = D2.rolling(ctx, cfg, 60.0, "fastest", mode="silo")
    assert "B" not in set(silo["plan"]["fp_id"]) and "A" in set(silo["plan"]["fp_id"])          # silo：B 区没有承包商，派不了
    pooled = D2.solve(ctx, cfg, 60.0, "fastest", with_pareto=True)
    assert "B" in set(pooled["plan"]["fp_id"]) and pooled["cross_zone_units"] >= 1 and pooled["cost"]["work_order_aed"] >= 2000
    sp = pooled["silo_vs_pooled"]; assert sp["delta_points"] >= 1 and sp["cross_zone_units"] >= 1 and sp["silo"]["points_served"] < sp["pooled"]["points_served"]
    assert bool(pooled["plan"].loc[pooled["plan"]["fp_id"] == "B", "cross_zone"].all())


def _coefs(cfg, ctx, fp, t=60.0, V=100.0):
    """某个点每个处置包在「增量延误」与「绝对延误」两种口径下的目标系数。"""
    import numpy as np
    from swbrain import resources as RS
    P = D2.params(cfg); res = RS.build(ctx["fleet"], None, cfg); alpha = float(P["delay_weight"])
    cand = D1.feasibility(ctx["pts0"], ctx["cs"], ctx["fleet"], cfg, t)
    cand["level"], cand["level_priority"] = D1.emergency_level(cand)
    classes = D2.build_classes(ctx["units"].assign(at_pt=-1, busy_until=np.nan), ctx["depots"], res, {**ctx, "cfg": cfg}, t)
    p = cand.index[cand["fp_id"] == fp][0]
    w = float(cand.loc[p, "priority"])
    pk = D2.point_packages(int(p), cand.loc[p], ctx["fleet"]["models"], classes, res, V, cfg, P, t, False, alt=None)
    return [(g, w * g["dH"] + alpha * w * g["delay_delta_min"] / 60.0 + g["cost_aed"] / V,
             w * g["dH"] + alpha * w * g["delay_min"] / 60.0 + g["cost_aed"] / V) for g in pk]


def test_delay_penalty_is_incremental_not_absolute():
    """「派了也追不上目标时限」的点不能被系统性拒派。

    2026-09-24 自查：目标函数里 dH 是增量（派了之后的积水小时 − 不派的），而延误罚用的是
    绝对值「派了之后还迟到多少」——不派这个点根本没有 y 变量、一分不罚。于是对追不上目标的点，
    罚会压过收益，模型宁可不派；偏偏这类点水量大、优先级高。正确写法是两项同基准。
    """
    cfg = _cfg(); ctx = _ctx(cfg, n_units12=3, n_units8=3, crews=40)
    rows = _coefs(cfg, ctx, "A")
    assert rows, "A 点应该枚举得出处置包"
    for g, new, old in rows:
        assert "delay_delta_min" in g and "delay_min" in g
        assert g["delay_delta_min"] <= 1e-6, (g["label_zh"], g["delay_min"], g["delay_delta_min"])
        assert new <= old + 1e-6, "增量口径不该比绝对口径更不愿意派"
    flipped = [(g["label_zh"], old, new) for g, new, old in rows if old > 0 > new]
    assert flipped, "夹具里没有一个包会因为口径不同而翻转符号，这条判据就是恒真的"


def test_point_that_cannot_meet_target_is_still_served():
    """端到端：所有可行包在旧口径下系数都为正的点，旧模型会一台不派，新口径必须派。"""
    cfg = _cfg(); cfg["dispatch"]["v2"]["max_units_per_point"] = 2        # params() 读的是 [dispatch.v2]
    ctx = _ctx(cfg, n_units12=0, n_units8=3, crews=40)          # 只有 8 寸泵：A 点怎么派都追不上 180 min
    rows = _coefs(cfg, ctx, "A")
    assert rows and all(old > 0 for _, _, old in rows), "夹具没造出「旧口径下全正」的点"
    assert any(new < 0 for _, new, _ in rows), "新口径下应当有包值得派"
    out = D2.solve(ctx, cfg, 60.0, "balanced", with_pareto=False)
    assert "A" in set(out["plan"]["fp_id"]), "追不上目标时限的点被整个放弃了"


def test_robust_delay_is_also_incremental():
    """鲁棒目标下 Δ延误同样必须 ≤ 0：每个情景各自减掉自己的基线延误，不能拿绝对值加权。"""
    import numpy as np
    from swbrain import resources as RS
    cfg = _cfg(); ctx = _ctx(cfg, n_units12=3, n_units8=3, tankers=4, crews=40)
    P = D2.params(cfg); res = RS.build(ctx["fleet"], None, cfg)
    cand = D1.feasibility(ctx["pts0"], ctx["cs"], ctx["fleet"], cfg, 60.0)
    cand["level"], cand["level_priority"] = D1.emergency_level(cand)
    classes = D2.build_classes(ctx["units"].assign(at_pt=-1, busy_until=np.nan), ctx["depots"], res, {**ctx, "cfg": cfg}, 60.0)
    p = cand.index[cand["fp_id"] == "A"][0]
    alt = [(_hyd(1500, inflow_peak=300), 0.6), (_hyd(5000, inflow_peak=900), 0.4)]
    pk = D2.point_packages(int(p), cand.loc[p], ctx["fleet"]["models"], classes, res, 100.0, cfg, P, 60.0, False, alt=alt)
    assert pk
    for g in pk:
        assert g["delay_delta_min"] <= 1e-6, (g["label_zh"], g["delay_delta_min"])
        assert g["t_empty_exp_min"] is not None and g["within_target_prob"] is not None
    # 情景里混一个「该情景不积水」的也要走得通
    pk2 = D2.point_packages(int(p), cand.loc[p], ctx["fleet"]["models"], classes, res, 100.0, cfg, P, 60.0, False,
                            alt=[(_hyd(1500, inflow_peak=300), 0.5), (None, 0.5)])
    assert pk2 and all(g["delay_delta_min"] <= 1e-6 for g in pk2)


def test_no_network_data_is_not_the_same_as_no_discharge():
    """「这一片没有管网数据」和「1.5 km 内没有排放去处」必须分开说。

    2026-09-24 实测：460 辆罐车里 384 辆（84%）派给 AAM / DRM——那两个区我们一个管网
    终点都没有（终点经度 54.31–55.08，AAM 在 55.20–55.84、DRM 在 51.77–54.24），
    而客户自己的热点清单标注 AAM 139/184、DRM 120/155 是有管网的。把数据缺口说成
    物理结论，客户一问就穿。行为不变（仍按最保守办给罐车），但文案必须如实。
    """
    import numpy as np
    cfg = _cfg()
    ctx = _ctx(cfg, n_units12=2, n_units8=2, tankers=4, crews=40)
    pts = ctx["pts0"].copy()
    # D 点挪到 60 km 外：超出 discharge_unknown_beyond_m，属于「没有管网数据」
    far = pts.iloc[[2]].copy(); far["fp_id"] = "D"; far["lon"] = 55.20
    pts = pd.concat([pts, far], ignore_index=True)
    out = D1.feasibility(pts, ctx["cs"], ctx["fleet"], cfg, 60.0)
    by = dict(zip(out["fp_id"], out["triage"]))
    dist = dict(zip(out["fp_id"], out["discharge_dist_m"].astype(float)))
    known = dict(zip(out["fp_id"], out["discharge_known"].astype(bool)))

    assert dist["C"] < 20000 and bool(known["C"]), (dist["C"], known["C"])      # C 在 14 km：有数据，但 1.5 km 内没去处
    assert "无可接纳的排放去处" in by["C"] and "没有管网数据" not in by["C"], by["C"]

    assert dist["D"] > 20000 and not bool(known["D"]), (dist["D"], known["D"])
    assert "没有管网数据" in by["D"], by["D"]
    assert "无可接纳的排放去处" not in by["D"], by["D"]

    # 两类点都判不可派泵（行为不变）
    ok = dict(zip(out["fp_id"], out["discharge_ok"].astype(bool)))
    assert not bool(ok["C"]) and not bool(ok["D"])
    assert bool(ok["A"]) and bool(ok["B"])


def test_coverage_threshold_is_configurable():
    cfg = _cfg(); ctx = _ctx(cfg, n_units12=2, n_units8=2, crews=40)
    out1 = D1.feasibility(ctx["pts0"], ctx["cs"], ctx["fleet"], cfg, 60.0)
    assert bool(out1.set_index("fp_id").loc["C", "discharge_known"])          # 14 km < 20 km
    cfg2 = _cfg(); cfg2["dispatch"]["discharge_unknown_beyond_m"] = 10000     # 收到 10 km，C 就成了「没数据」
    ctx2 = _ctx(cfg2, n_units12=2, n_units8=2, crews=40)
    out2 = D1.feasibility(ctx2["pts0"], ctx2["cs"], ctx2["fleet"], cfg2, 60.0)
    assert not bool(out2.set_index("fp_id").loc["C", "discharge_known"])
    assert "没有管网数据" in out2.set_index("fp_id").loc["C", "triage"]


def _plan_fingerprint(out):
    cols = [c for c in ("fp_id", "unit_id", "model", "package", "arrive_min", "done_min", "rate_m3h", "cost_aed", "wave") if c in out["plan"].columns]
    plan = out["plan"][cols].sort_values(cols).round(6).to_dict(orient="records") if len(out["plan"]) else []
    pa = sorted([{k: (round(v, 6) if isinstance(v, float) else v) for k, v in x.items()
                  if k in ("objective", "cost_aed", "points_served", "weighted_flood_hours", "units", "tankers")}
                 for x in (out.get("pareto") or [])], key=lambda x: str(x.get("objective")))
    return json.dumps({"plan": plan, "pareto": pa}, sort_keys=True, default=str)


def test_shared_package_cache_changes_nothing():
    """帕累托三目标 + silo 共用一份处置包缓存，结果必须和不用缓存逐字段一致。

    处置包与目标无关（point_packages 根本不用 V），四遍滚动求解里窗口 1 的包完全一样。
    共用能省一成上下，但缓存键要是漏了 point_packages 真正读到的某个字段，就会拿错包
    算出错的调度单——这条判据就是防它的。
    """
    import swbrain.dispatch2 as mod
    cfg = _cfg(); ctx = _ctx(cfg, n_units12=3, n_units8=3, tankers=4, crews=40)

    real = mod.rolling

    def no_cache(*a, **kw):
        kw["pkg_cache"] = None
        return real(*a, **kw)

    mod.rolling = no_cache
    try:
        a = _plan_fingerprint(D2.solve(ctx, cfg, 60.0, "balanced", with_pareto=True))
    finally:
        mod.rolling = real
    b = _plan_fingerprint(D2.solve(ctx, cfg, 60.0, "balanced", with_pareto=True))
    assert a == b, "共用处置包缓存改变了结果"


def test_package_cache_key_covers_what_point_packages_reads():
    """缓存键必须随「point_packages 真正读到的东西」变化——判据别恒真。"""
    import numpy as np
    from swbrain import resources as RS
    cfg = _cfg(); ctx = _ctx(cfg, n_units12=3, n_units8=3, tankers=4, crews=40); ctx = {**ctx, "cfg": cfg}
    res = RS.build(ctx["fleet"], None, cfg)
    cand = D1.feasibility(ctx["pts0"], ctx["cs"], ctx["fleet"], cfg, 60.0)
    cand["level"], cand["level_priority"] = D1.emergency_level(cand)
    classes = D2.build_classes(ctx["units"].assign(at_pt=-1, busy_until=np.nan), ctx["depots"], res, ctx, 60.0)
    p = int(cand.index[cand["fp_id"] == "A"][0])
    base = D2._pkg_key(p, cand.loc[p], classes, 60.0, False)

    assert D2._pkg_key(p, cand.loc[p], classes, 60.0, False) == base          # 同样的输入 → 同样的键
    assert D2._pkg_key(p, cand.loc[p], classes, 120.0, False) != base         # 时刻变了
    assert D2._pkg_key(p, cand.loc[p], classes, 60.0, True) != base           # 允不允许罐车变了
    row = cand.loc[p].copy(); row["volume_now_m3"] = float(row["volume_now_m3"]) + 100
    assert D2._pkg_key(p, row, classes, 60.0, False) != base                  # 水量变了
    row2 = cand.loc[p].copy(); row2["relay_factor"] = float(row2["relay_factor"]) * 0.9
    assert D2._pkg_key(p, row2, classes, 60.0, False) != base                 # 接力折减变了
    cls2 = [dict(c) for c in classes]
    cls2[0] = {**cls2[0], "travel": np.asarray(cls2[0]["travel"], dtype=float) + 7.0}
    assert D2._pkg_key(p, cand.loc[p], cls2, 60.0, False) != base             # 行程变了


def test_discharge_detour_factor():
    """软管实际要走多远 ÷ 直线距离。缺省 1.0 = 原行为；调大会把一部分点推出 1.5 km。

    2026-09-24 实测（10cc，40 个点抽样）：路网距离是直线的 1.66 倍（四分位 1.32 / 2.52，
    最大 10.25），按路网算每台泵的软管中位从 650 m 涨到 1,300 m、接力从 6 级涨到 12 级，
    40 个里有 20 个按直线判「1.5 km 内有去处」、按路走已经超了。软管在 MILP 里是硬约束。
    """
    cfg = _cfg(); ctx = _ctx(cfg, n_units12=2, n_units8=2, crews=40)
    a = D1.feasibility(ctx["pts0"], ctx["cs"], ctx["fleet"], cfg, 60.0).set_index("fp_id")
    assert "discharge_dist_straight_m" in a.columns
    assert abs(float(a.loc["A", "discharge_dist_m"]) - float(a.loc["A", "discharge_dist_straight_m"])) < 1e-6   # 缺省不放大

    cfg2 = _cfg(); cfg2["dispatch"]["discharge_detour_factor"] = 2.0
    ctx2 = _ctx(cfg2, n_units12=2, n_units8=2, crews=40)
    b = D1.feasibility(ctx2["pts0"], ctx2["cs"], ctx2["fleet"], cfg2, 60.0).set_index("fp_id")
    assert abs(float(b.loc["A", "discharge_dist_m"]) - 2.0 * float(a.loc["A", "discharge_dist_straight_m"])) <= 2.0   # 各自取整，差一两米
    # A 点直线 751 m，×2 = 1502 m 刚过 1.5 km → 从「可派泵」变成「没有排放去处」
    assert bool(a.loc["A", "discharge_ok"]) and not bool(b.loc["A", "discharge_ok"])

    # 还在半径内时，绕行系数要推高接力级数与软管需求（discharge_ok 为假时级数被置 0，所以用 1.5 倍验）
    cfg3 = _cfg(); cfg3["dispatch"]["discharge_detour_factor"] = 1.5
    ctx3 = _ctx(cfg3, n_units12=2, n_units8=2, crews=40)
    c = D1.feasibility(ctx3["pts0"], ctx3["cs"], ctx3["fleet"], cfg3, 60.0).set_index("fp_id")
    assert bool(c.loc["A", "discharge_ok"]), "1.5 倍不该把 A 推出半径"
    assert int(c.loc["A", "relay_stages"]) > int(a.loc["A", "relay_stages"]), "绕行系数没有推高接力级数"
    assert float(c.loc["A", "relay_factor"]) <= float(a.loc["A", "relay_factor"]), "级数多了，排量折减该更狠"
