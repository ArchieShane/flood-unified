"""v2 的运行时能力：限重桥断开、现场重规划（锁定 / 释放 / 故障 / 993 报案匹配）、单点最优严格版、压力测试、CP-SAT 车次排程。合成算例。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd

from swbrain import dispatch as D1, dispatch2 as D2, resources as RS, travel as tr
from tests.test_dispatch2 import _cfg, _ctx, _hyd


def test_blocked_edge_mask_and_travel_inf():
    # 一条直线路：v0 —e0— v1 —e1— v2；把 e1 中点标成限重桥 → 重型车从 v0 到 v2 不可达，v0 到 v1 仍可达
    v = pd.DataFrame({"vid": [0, 1, 2], "lon": [54.50, 54.51, 54.52], "lat": [24.40, 24.40, 24.40]})
    e = pd.DataFrame({"id": [0, 1], "source": [0, 1], "target": [1, 2], "cost_s": [60.0, 60.0], "rcost_s": [60.0, 60.0], "len_m": [1000.0, 1000.0], "mid_lon": [54.505, 54.515], "mid_lat": [24.40, 24.40]})
    rn = tr.RoadNetwork(e, v)
    m = rn.blocked_edge_mask([(54.515, 24.40)], radius_m=40)
    assert m.tolist() == [False, True]
    T = rn.travel_minutes(np.array([[54.50, 24.40]]), np.array([[54.51, 24.40], [54.52, 24.40]]), blocked_points=[(54.515, 24.40)])
    assert np.isfinite(T[0, 0]) and not np.isfinite(T[0, 1])
    T2 = rn.travel_minutes(np.array([[54.50, 24.40]]), np.array([[54.52, 24.40]]))
    assert np.isfinite(T2[0, 0])


def test_replan_locks_released_failed_and_incidents():
    cfg = _cfg(); cfg["dispatch"]["resources"].update({"hose_m_per_unit": 3000, "fuel_l_per_unit": 5000})
    ctx = _ctx(cfg, n_units12=3, n_units8=2, tankers=4, crews=40)
    base = D2.rolling(ctx, cfg, 60.0, "fastest")
    plan = base["plan"]; assert len(plan)
    served0 = set(plan["fp_id"])
    a_units = list(plan.loc[plan["fp_id"] == "A", "unit_id"])
    # 90 min 时：A 报退干（它的泵释放）、一台泵故障、一条 993 报案落在 B 附近（未核实）
    failed = [u for u in plan["unit_id"] if u not in a_units][:1]
    r = D2.replan(ctx, cfg, plan, 60.0, 90.0, dry_points=["A"], failed_units=failed, incidents=[{"lon": 54.5101, "lat": 24.4101, "verified": False}], objective="fastest")
    rp = r["replan"]
    n_a_not_departed = sum(1 for d in rp["dropped"] if d["fp_id"] == "A" and d["why"] == "not departed")      # 等车还没出发的泵不算「释放」，直接进重解
    assert rp["dry_points"] == ["A"] and len(rp["released_units"]) + n_a_not_departed == len(a_units) and rp["failed_units"] == sorted(failed)
    assert rp["incidents_matched"].get("B") == 1 and rp["incidents_unmatched"] == 0 and abs(rp["priority_boost"]["B"] - 1.2) < 1e-9
    new_plan = r["plan"]
    assert "A" not in set(new_plan.loc[new_plan["depart_min"] >= 90.0, "fp_id"])             # 退干的点不再派
    assert not any(u in set(new_plan.loc[new_plan["depart_min"] >= 90.0, "unit_id"]) for u in failed)   # 故障泵不再出现在新派单
    assert rp["locked_units"] >= 1 and r["t_start"] == 90.0
    # 没匹配到的报案
    r2 = D2.replan(ctx, cfg, plan, 60.0, 90.0, incidents=[{"lon": 55.9, "lat": 25.9}], objective="fastest")
    assert r2["replan"]["incidents_unmatched"] == 1 and r2["replan"]["incidents_matched"] == {}


def test_focus_compare_reports_taken_units():
    cfg = _cfg(); cfg["dispatch"]["resources"].update({"hose_m_per_unit": 3000, "fuel_l_per_unit": 5000})
    ctx = _ctx(cfg, n_units12=1, n_units8=1, crews=40)          # 泵少：全局解与单点最优会抢泵
    base = D2.rolling(ctx, cfg, 60.0, "balanced")
    fc = D2.focus_compare(ctx, cfg, 60.0, "B", base, "balanced")
    assert fc["fp_id"] == "B" and fc["focus"]["served"] and set(fc.keys()) >= {"units_taken_from_global", "displaced_points", "delta_cost_aed", "global", "focus"}
    assert fc["focus"]["units"] >= 1


def test_stress_test_counts_gaps():
    cfg = _cfg(); cfg["dispatch"]["resources"].update({"hose_m_per_unit": 3000, "fuel_l_per_unit": 5000})
    ctx = _ctx(cfg, n_units12=3, n_units8=2, crews=40)
    base = D2.rolling(ctx, cfg, 60.0, "fastest")
    worse = dict(ctx); pts_w = ctx["pts0"].copy()
    pts_w["hydrograph"] = [_hyd(3 * float(v), inflow_peak=1800.0) for v in pts_w["volume_m3"]]; pts_w["volume_m3"] = pts_w["volume_m3"] * 3
    worse["pts0"] = pts_w
    st = D2.stress_test(worse, cfg, base["plan"], 60.0, label="3x")
    assert st["points_in_plan"] >= 1 and st["within_target_worse"] <= st["points_in_plan"] and st["extra_12in_equiv_total"] >= 1 and st["worst"]


def test_cpsat_schedule_available_or_skipped():
    trips = [{"tid": 0, "earliest": 60, "duration": 40}, {"tid": 1, "earliest": 60, "duration": 40}, {"tid": 2, "earliest": 60, "duration": 40}]
    r = D2.schedule_cpsat(trips, 2, time_limit_s=3)
    if r is None:
        return                                                          # 没装 ortools：贪心兜底
    assert set(r) == {0, 1, 2} and all(v in (0, 1) for v, _ in r.values())
    by_v = {}
    for tid, (v, s) in r.items():
        by_v.setdefault(v, []).append(s)
    for v, starts in by_v.items():
        starts = sorted(starts)
        assert all(b - a >= 40 - 1e-6 for a, b in zip(starts, starts[1:]))          # 同一辆车不重叠


def test_robust_expectation_changes_packages_and_reports_per_scenario():
    """鲁棒目标：同一处置包在 3× 水量的情景上重演并按权重取期望；解出的单在每个情景上重演，并与单情景的单对照。"""
    cfg = _cfg(); cfg["dispatch"]["resources"].update({"hose_m_per_unit": 3000, "fuel_l_per_unit": 5000})
    ctx = _ctx(cfg, n_units12=3, n_units8=2, crews=40)
    worse = dict(ctx); pts_w = ctx["pts0"].copy()
    pts_w["hydrograph"] = [_hyd(3 * float(v), inflow_peak=1800.0) for v in pts_w["volume_m3"]]; pts_w["volume_m3"] = pts_w["volume_m3"] * 3
    worse["pts0"] = pts_w
    base = D2.rolling(ctx, cfg, 60.0, "balanced")
    spec = D2.robust_spec("base", {"base": 0.5, "worse": 0.5}, {"base": ctx, "worse": worse}, labels={"base": "常规", "worse": "三倍"}, base_plan=base["plan"])
    assert spec["weights"] == {"base": 0.5, "worse": 0.5} and set(spec["hyds"]) == {"worse"} and "A" in spec["hyds"]["worse"]
    # 处置包层：期望字段存在，且更重情景把「退干」推后 → 期望退干时刻 ≥ 单情景的
    res = RS.build(ctx["fleet"], None, cfg); P = D2.params(cfg); t = 60.0
    pts = D1.feasibility(ctx["pts0"], ctx["cs"], ctx["fleet"], cfg, t); pts["level"], pts["level_priority"] = D1.emergency_level(pts)
    units = ctx["units"].copy(); units["at_pt"] = -1; units["busy_until"] = np.nan
    classes = D2.build_classes(units, ctx["depots"], res, {**ctx, "cfg": cfg}, t)
    pa = pts.index[pts["fp_id"] == "A"][0]
    g1 = D2.point_packages(int(pa), pts.loc[pa], ctx["fleet"]["models"], classes, res, 100.0, cfg, P, t, False)
    g2 = D2.point_packages(int(pa), pts.loc[pa], ctx["fleet"]["models"], classes, res, 100.0, cfg, P, t, False, alt=D2._alt_for(spec, pts.loc[pa]))
    assert len(g1) == len(g2) and all(x["t_empty_exp_min"] is not None and x["within_target_prob"] is not None for x in g2) and all(x["t_empty_exp_min"] is None for x in g1)
    assert any((x2["t_empty_exp_min"] or 0) >= (x1["t_empty_min"] or 0) for x1, x2 in zip(g1, g2) if x1["t_empty_min"] is not None)
    r = D2.solve(ctx, cfg, 60.0, "balanced", robust=spec)
    rb = r["robust"]
    assert rb["frame"] == "base" and set(rb["by_scenario"]) == {"base", "worse"} and rb["expected_base"] is not None and len(r.get("pareto") or []) == 3 and not r.get("extras_errors")
    for k, e in rb["by_scenario"].items():
        assert not e.get("error") and e["points_in_plan"] >= 1 and isinstance(e["base"], dict)
    assert rb["expected"]["within_target"] <= rb["by_scenario"]["base"]["points_in_plan"]
