"""达标分期、稳健收益、对账指标的单元测试。运行：python tests/test_standard.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from swbrain import whatif, verification, contracts
from swbrain.db import load_config

cfg = load_config()


def test_scenario_weighted_benefit():
    delta = {"surcharge_len_km@常规": -1.0, "surcharge_len_km@百年一遇": -1.0, "tide_exposed_inlets": -10}
    w = {"surcharge_len_km": 1.0, "tide_exposed_inlets": 0.1}
    b = whatif._benefit_score(delta, w, {"常规": 0.5, "百年一遇": 0.05})
    assert abs(b - (0.5 + 0.05 + 1.0)) < 1e-9, b
    b2 = whatif._benefit_score(delta, w, None)   # 没有情景权重时每个情景都按 1 算
    assert abs(b2 - 3.0) < 1e-9, b2


def test_standard_plan_phasing():
    SC = {"常规": 30.0, "百年一遇": 100.0}
    es = pd.DataFrame({
        "gid": [1, 2, 3], "u": [1, 2, 4], "v": [2, 3, 5], "len_m": [1000.0, 1000.0, 1000.0], "pipe_diameter": [300.0, 300.0, 300.0], "diameter_flag": ["ok"] * 3,
        "slope": [0.003] * 3, "n_manning": [0.011] * 3, "q_full": [0.0626] * 3, "n_inlets_up": [60.0, 60.0, 5.0], "terminal": [3, 3, 5],
        "terminal_kind": ["出水口", "出水口", "断头"], "zone_name": ["A", "A", "B"], "is_active": [True] * 3, "area_m2": [36000.0, 36000.0, 3000.0],
    })
    for n, i in SC.items():
        es[f"q_{n}"] = 0.85 * i / 3.6e6 * es["area_m2"]; es[f"ratio_{n}"] = es[f"q_{n}"] / es["q_full"]
    cs = pd.DataFrame({"terminal": [3, 5], "terminal_kind": ["出水口", "断头"], "storage_deficit_m3": [0.0, 0.0], "tide_clearance_m": [2.0, None], "n_pumps": [0, 0],
                       "q_peak_百年一遇": [0.5, 0.01], "suspect_missing_outlet": [False, True]})
    c2 = dict(cfg); c2["standard"] = dict(cfg["standard"]); c2["standard"]["target_scenario"] = "百年一遇"; c2["standard"]["annual_budget_aed"] = 3_000_000
    sp = whatif.standard_plan(es, cs, c2, SC)
    assert sp["target"] == "百年一遇" and sp["n_suspect_first"] == 1
    assert sp["n_projects"] == 1 and sp["years"] >= 1, sp
    assert sp["curve"][-1]["remaining_surcharge_km"] == 0.0 or sp["curve"][-1]["remaining_surcharge_km"] < 1.1  # 断头汇水区不计入年度


def test_compare_metrics():
    obs = {"event_id": "e1", "flood_points": [
        {"vertex_id": 1, "observed_depth_m": 0.3, "duration_min": 60},   # 观测有
        {"vertex_id": 2, "observed_depth_m": 0.0, "duration_min": 0},    # 观测无
        {"vertex_id": 3, "observed_depth_m": 0.4, "duration_min": 40}]}  # 观测有
    pred = {"run_id": "r1", "scenario": {"name": "x"}, "nodes": [
        {"vertex_id": 1, "max_depth_m": 0.25, "flood_duration_min": 50},   # 预测有 → TP
        {"vertex_id": 2, "max_depth_m": 0.20, "flood_duration_min": 10},   # 预测有 → FP
        {"vertex_id": 3, "max_depth_m": 0.05, "flood_duration_min": 5}]}   # 预测无 → FN
    c = verification.compare(obs, pred, cfg)
    assert (c["tp"], c["fp"], c["fn"]) == (1, 1, 1) and c["pod"] == 0.5 and c["far"] == 0.5 and c["passed"] is False, c


def test_observation_example_valid():
    ex = contracts.example_event_observation()
    assert ex["schema"] == contracts.SCHEMA_OBSERVATION and len(ex["flood_points"]) == 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok", name)
