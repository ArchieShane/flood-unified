"""指示性成本：台时 × 小时单价 + 动员费；退不干按 48 h；最短计费；行里的单价与出发时刻优先。"""
import pandas as pd
from swbrain import dispatch as dp

CFG = {"dispatch": {"cost": {"mobilisation_aed_per_unit": 500, "min_duty_h": 4, "default_duty_aed_h": 60, "horizon_min": 2880}}}
MODELS = {"diesel-12in": {"duty_aed_h": 90}, "diesel-8in": {"duty_aed_h": 60}}


def test_plan_cost_hours_rates_and_horizon():
    plan = pd.DataFrame([{"fp_id": "A", "unit_id": "u1", "model": "diesel-12in", "rate_m3h": 700},
                         {"fp_id": "B", "unit_id": "u2", "model": "diesel-8in", "rate_m3h": 350},
                         {"fp_id": "C", "unit_id": "u3", "model": "diesel-8in", "rate_m3h": 350}])
    rep = [{"fp_id": "A", "replay_pump_end_min": 30 + 600}, {"fp_id": "B", "replay_pump_end_min": 30 + 60}, {"fp_id": "C", "replay_pump_end_min": None}]
    c = dp.plan_cost(plan, rep, 30.0, CFG, MODELS)
    # A：10 h × 90；B：1 h 但最短计费 4 h × 60；C：退不干按 48 h − 0.5 h = 47.5 h × 60；动员 3 × 500
    assert c["units"] == 3 and c["pump_hours"] == 10 + 4 + 47.5
    assert c["duty_aed"] == 10 * 90 + 4 * 60 + 47.5 * 60 and c["mobilisation_aed"] == 1500 and c["total_aed"] == c["duty_aed"] + 1500
    assert c["by_model"]["diesel-8in"]["units"] == 2 and c["min_duty_h"] == 4


def test_plan_cost_prefers_row_rate_and_depart_column():
    plan = pd.DataFrame([{"fp_id": "A", "unit_id": "u1", "model": "x", "duty_aed_h": 100, "depart_min": 60}])
    c = dp.plan_cost(plan, [{"fp_id": "A", "replay_pump_end_min": 360}], 0.0, CFG, {})
    assert c["pump_hours"] == 5 and c["duty_aed"] == 500
    c2 = dp.plan_cost(plan.drop(columns=["duty_aed_h"]), [{"fp_id": "A", "replay_pump_end_min": 360}], 0.0, CFG, {})
    assert c2["duty_aed"] == 5 * 60                         # 没有单价：型号查不到，用 default_duty_aed_h


def test_plan_cost_empty():
    assert dp.plan_cost(pd.DataFrame(), [], 0.0, CFG, {})["total_aed"] == 0
