"""what-if 引擎的单元测试。运行：python tests/test_whatif.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from swbrain import whatif, contracts
from swbrain.db import load_config

cfg = load_config()
SC = {"常规": 30.0, "强降雨": 60.0}


def _toy():
    # 1→2→3(出水口)，两段 DN300，1 挂 60 个进水口
    es = pd.DataFrame({
        "gid": [1, 2], "u": [1, 2], "v": [2, 3], "len_m": [100.0, 100.0], "pipe_diameter": [300.0, 300.0], "slope": [0.003, 0.003], "n_manning": [0.011, 0.011],
        "q_full": [0.0626, 0.0626], "area_m2": [36000.0, 36000.0], "n_inlets_up": [60.0, 60.0], "terminal": [3, 3], "terminal_kind": ["出水口", "出水口"], "is_active": [True, True],
    })
    for n, i in SC.items():
        es[f"q_{n}"] = 0.85 * i / 3.6e6 * es["area_m2"]
        es[f"ratio_{n}"] = es[f"q_{n}"] / es["q_full"]
    cs = pd.DataFrame({"terminal": [3], "terminal_kind": ["出水口"], "terminal_unitid": ["OFL_1"], "n_inlets": [60.0], "tide_clearance_m": [1.2], "n_pumps": [0],
                       "storage_deficit_m3": [0.0], "runoff_m3": [765.0], "storage_m3": [0.0], "needs_storage": [False]})
    return es, cs


def test_surrogate_upsize_reduces_surcharge():
    es, cs = _toy()
    sur = whatif.SurrogateEvaluator(es, cs, cfg, SC)
    assert sur.base["surcharge_len_km@常规"] == 0.2
    s = contracts.InterventionSet("p1", [contracts.Intervention("pipe_upsize", {"gid": 1}, {"diameter_mm": 600})])
    ev = sur.evaluate(s)
    assert ev["delta"]["surcharge_len_km@常规"] == -0.1, ev
    s2 = contracts.InterventionSet("p2", [contracts.Intervention("add_pump", {"vertex_id": 3}, {"capacity_m3s": 0.5})])
    assert whatif.SurrogateEvaluator(es, cs, cfg, SC).evaluate(s2)["delta"]["tide_exposed_inlets"] == -60


def test_storage_propagates_downstream():
    es, cs = _toy()
    sur = whatif.SurrogateEvaluator(es, cs, cfg, SC)
    # 在节点 1 加 300 m³：15 分钟内削峰 0.33 m³/s，两段都降到 0 → 不再满管
    s = contracts.InterventionSet("p3", [contracts.Intervention("add_storage", {"vertex_id": 1}, {"volume_m3": 300})])
    ev = sur.evaluate(s)
    assert ev["delta"]["surcharge_len_km@常规"] == -0.2, ev


def test_cost_and_optimize():
    es, _ = _toy()
    iv = contracts.Intervention("pipe_upsize", {"gid": 1}, {"diameter_mm": 600})
    assert whatif.cost_of(iv, cfg, es) == 100 * 2400
    sets = [contracts.InterventionSet("a", [iv], cost_aed=1e6), contracts.InterventionSet("b", [iv], cost_aed=3e6), contracts.InterventionSet("c", [iv], cost_aed=1e6)]
    evals = {"a": {"delta": {"surcharge_len_km@常规": -1.0}, "kpis": {}, "source": "surrogate"},
             "b": {"delta": {"surcharge_len_km@常规": -6.0}, "kpis": {}, "source": "surrogate"},
             "c": {"delta": {"surcharge_len_km@常规": 0.5}, "kpis": {}, "source": "surrogate"}}
    plan = whatif.optimize(sets, evals, budget_aed=3.5e6, cfg=cfg)
    assert list(plan["set_id"]) == ["b", "a"] and list(plan["selected"]) == [True, False], plan


def test_contract_validation():
    ok = contracts.Intervention("pipe_upsize", {"gid": 5}, {"diameter_mm": 500}).validate()
    assert ok.type == "pipe_upsize"
    try:
        contracts.Intervention("teleport", {"gid": 5}, {}).validate(); raise AssertionError("应报未知类型")
    except ValueError:
        pass
    try:
        contracts.Intervention("add_pump", {"nothing": 1}, {}).validate(); raise AssertionError("应报 target 缺键")
    except ValueError:
        pass
    ex = contracts.example_prediction_state()
    assert ex["schema"] == contracts.SCHEMA_PREDICTION and ex["nodes"][0]["vertex_id"] == 157832


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok", name)
