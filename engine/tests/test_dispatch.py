"""应急调度引擎的单元测试。运行：python tests/test_dispatch.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from swbrain import dispatch
from swbrain.db import load_config

cfg = load_config()


def test_state_at_interpolates():
    hyd = [{"t_min": 0, "volume_m3": 0, "inflow_m3h": 0}, {"t_min": 100, "volume_m3": 1000, "inflow_m3h": 600}, {"t_min": 200, "volume_m3": 0, "inflow_m3h": 0}]
    v, q = dispatch.state_at(hyd, 150, 1000, 200)
    assert abs(v - 500) < 1e-9 and abs(q - 300) < 1e-9


def test_assign_milp_prefers_priority_and_capacity():
    pts = pd.DataFrame({"fp_id": ["A", "B"], "priority": [100.0, 10.0], "required_rate_m3h": [2000.0, 400.0], "volume_now_m3": [2000, 300], "inflow_now_m3h": [0, 0],
                        "target_drain_min": [120, 360], "lon": [0, 0], "lat": [0, 0], "note": ["", ""], "top_kind": ["tunnel", "none"], "relay_needed": [False, False], "discharge_to": [None, None]})
    units = pd.DataFrame({"unit_id": ["u1", "u2", "u3"], "model": ["heavy", "small", "small"], "rate_m3h": [3000.0, 500.0, 500.0], "setup_min": [40, 20, 20], "avail_min": [0.0, 0.0, 0.0],
                          "from": ["D", "D", "D"], "lon": [0, 0, 0], "lat": [0, 0, 0], "vehicle_class": ["heavy", "light", "light"]})
    travel = np.array([[10.0, 10.0], [10.0, 5.0], [10.0, 5.0]])
    assign, meta = dispatch.assign_milp(pts, units, travel, cfg)
    assert meta["status"] == "optimal", meta
    got = assign.groupby("pt_ix")["unit_ix"].apply(set).to_dict()
    assert 0 in got and 0 in got[0], got            # 大泵去高优先级的 A（2000 m³/h 只有它能满足）
    assert 1 in got and got[1] <= {1, 2}, got       # 小泵去 B
    plan = dispatch.plan_from_assignment(assign, pts, units, 0.0, 1)
    a = plan[plan["fp_id"] == "A"].iloc[0]
    assert a["site_rate_m3h"] >= 2000 and a["drain_min"] <= 120, a.to_dict()


def test_assign_respects_max_travel():
    pts = pd.DataFrame({"fp_id": ["A"], "priority": [50.0], "required_rate_m3h": [400.0], "volume_now_m3": [300], "inflow_now_m3h": [0], "target_drain_min": [360],
                        "lon": [0], "lat": [0], "note": [""], "top_kind": ["none"], "relay_needed": [False], "discharge_to": [None]})
    units = pd.DataFrame({"unit_id": ["u1"], "model": ["small"], "rate_m3h": [500.0], "setup_min": [20], "avail_min": [0.0], "from": ["D"], "lon": [0], "lat": [0], "vehicle_class": ["light"]})
    assign, meta = dispatch.assign_milp(pts, units, np.array([[cfg["dispatch"]["max_travel_min"] + 1.0]]), cfg)
    assert len(assign) == 0 and meta["status"] == "no feasible pair"


def test_preposition_covers_within_time():
    pts = pd.DataFrame({"fp_id": ["A", "B", "C"], "priority": [10.0, 20.0, 30.0], "required_rate_m3h": [500.0, 500.0, 500.0]})
    sites = pd.DataFrame({"site_id": ["S1", "S2"], "name": ["s1", "s2"], "lon": [0, 1], "lat": [0, 1]})
    T = np.array([[5.0, 50.0, 50.0], [50.0, 5.0, 5.0]])   # S1 只覆盖 A，S2 覆盖 B、C
    units = pd.DataFrame({"rate_m3h": [500.0] * 4})
    c2 = dict(cfg); c2["dispatch"] = dict(cfg["dispatch"]); c2["dispatch"]["preposition_max_sites"] = {"红": 1}; c2["dispatch"]["preposition_priority_quantile"] = {"红": 0.0}
    r = dispatch.preposition(pts, sites, T, units, "红", c2)
    assert r["status"] == "optimal" and len(r["sites"]) == 1 and r["sites"][0]["site_id"] == "S2", r   # 一个站点时选覆盖优先级更高的 S2
    assert r["covered"] == 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok", name)


def test_relay_factor_and_plan_rate_reduction():
    from swbrain import dispatch as dp
    import numpy as np, pandas as pd
    c = {"dispatch": {"relay_beyond_m": 300, "relay_hose_m": 100, "relay_rate_factor": 0.85, "relay_min_factor": 0.4}}
    assert dp.relay_factor(200, c) == (0, 1.0) and dp.relay_factor(float("nan"), c) == (0, 1.0)
    st, f = dp.relay_factor(550, c)                       # 250 m 超出 → 3 级 → 0.85³
    assert st == 3 and abs(f - 0.85 ** 3) < 1e-9
    st, f = dp.relay_factor(5000, c)
    assert f == 0.4                                      # 不低于下限
    pts = pd.DataFrame([{"fp_id": "A", "note": "", "priority": 5.0, "top_kind": "road", "inflow_now_m3h": 0.0, "volume_now_m3": 1000.0, "target_drain_min": 240.0, "relay_needed": True, "relay_stages": 3, "relay_factor": 0.614, "discharge_to": "X", "lon": 54.5, "lat": 24.4}])
    units = pd.DataFrame([{"unit_id": "u1", "model": "diesel-8in", "rate_m3h": 350.0, "setup_min": 20.0, "avail_min": 0.0, "from": "d", "lon": 54.4, "lat": 24.4}])
    assign = pd.DataFrame([{"pt_ix": 0, "unit_ix": 0, "travel_min": 10.0}])
    plan = dp.plan_from_assignment(assign, pts, units, 210.0, 1)
    r = plan.iloc[0]
    assert abs(r["rate_m3h"] - 350.0 * 0.614) < 0.1 and r["rate_nominal_m3h"] == 350.0 and r["relay_stages"] == 3
    assert r["pumping_from_min"] == 210 + 10 + 20 + 20 * 3     # 三级接力多三次架设
