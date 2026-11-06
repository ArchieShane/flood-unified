"""预置的价值：预置驻地出发的调度单重演，与事后调度比（合成小例子）。"""
import numpy as np
import pandas as pd
from swbrain import dispatch as dp

HYD = [{"t_min": 0, "volume_m3": 0, "inflow_m3h": 0}, {"t_min": 60, "volume_m3": 3000, "inflow_m3h": 3000},
       {"t_min": 75, "volume_m3": 3000, "inflow_m3h": 0}, {"t_min": 675, "volume_m3": 0, "inflow_m3h": 0}]


def _cfg():
    return {"dispatch": {"preposition_depart_min": 30, "max_units_per_point": 3}}


def test_preposition_value_beats_late_dispatch():
    pts = pd.DataFrame([{"fp_id": "A", "volume_m3": 3000.0, "target_drain_min": 240.0, "priority": 5.0, "level": "III", "hydrograph": HYD},
                        {"fp_id": "B", "volume_m3": 3000.0, "target_drain_min": 600.0, "priority": 2.0, "level": "II", "hydrograph": HYD}])
    units = pd.DataFrame([{"unit_id": f"u{i}", "model": "diesel-8in", "rate_m3h": 350.0, "setup_min": 20.0} for i in range(4)])
    sites = pd.DataFrame([{"site_id": "S1", "name": "站1", "lon": 54.4, "lat": 24.4}])
    T = np.array([[10.0, 15.0]])                      # 站1 → A 10 min，→ B 15 min
    pre = {"sites": [{"site_id": "S1", "name": "站1", "covers": ["A", "B"], "units_suggested": 4}]}
    # 事后调度：同样 4 台，但 300 分钟才开始抽
    plan_after = pd.DataFrame([{"fp_id": "A", "unit_id": "u0", "model": "diesel-8in", "rate_m3h": 350.0, "arrive_min": 280, "pumping_from_min": 300},
                               {"fp_id": "A", "unit_id": "u1", "model": "diesel-8in", "rate_m3h": 350.0, "arrive_min": 280, "pumping_from_min": 300},
                               {"fp_id": "B", "unit_id": "u2", "model": "diesel-8in", "rate_m3h": 350.0, "arrive_min": 285, "pumping_from_min": 300},
                               {"fp_id": "B", "unit_id": "u3", "model": "diesel-8in", "rate_m3h": 350.0, "arrive_min": 285, "pumping_from_min": 300}])
    from swbrain import replay
    rows_after, s_after = replay.replay_plan(pts, plan_after)
    plan_after = plan_after.merge(pd.DataFrame(rows_after), on="fp_id", how="left")
    v = dp.preposition_value(pre, pts, units, sites, T, _cfg(), plan_after, s_after)
    assert v is not None and v["units_placed"] == 4 and v["points_with_pumps"] == 2 and v["depart_min"] == 30
    assert v["arrive_median_min"] < v["after_arrive_median_min"]
    assert v["flood_h"]["pre"] < v["flood_h"]["after"] < v["flood_h"]["base"]
    assert v["saved_vs_after_pct"] > 0 and v["within_target"]["pre"] >= v["within_target"]["after"]
    assert all(r["pumping_from_min"] >= 30 + 10 + 20 for r in v["plan"])
    assert dp.preposition_value({"sites": []}, pts, units, sites, T, _cfg(), plan_after, s_after) is None
