"""罐车：有效排量 = 容量 / 周转；只派给无排放去处且未被泵处置的点；池用完为止；从最近驻地出发。"""
import numpy as np
import pandas as pd
from swbrain import dispatch as dp

CFG = {"dispatch": {"tankers": {"capacity_m3": 17, "duty_aed_h": 250, "load_min": 10, "unload_min": 10, "speed_kmh": 30, "default_haul_km": 5, "max_per_point": 6}}}


def test_tanker_rate():
    r, cyc = dp.tanker_rate_m3h(CFG, 5000.0)          # 5 km：10+10+2×5/30×60 = 40 min → 17/(40/60) = 25.5 m³/h
    assert abs(cyc - 40.0) < 1e-9 and abs(r - 25.5) < 1e-6
    r2, cyc2 = dp.tanker_rate_m3h(CFG, None)          # 没有排放点：按缺省 5 km
    assert cyc2 == cyc
    r3, _ = dp.tanker_rate_m3h(CFG, 1000.0)
    assert r3 > r


def test_assign_tankers_only_no_discharge_points_and_pool_limit():
    pts = pd.DataFrame([{"fp_id": "A", "priority": 9.0, "volume_now_m3": 600.0, "inflow_now_m3h": 0.0, "required_rate_m3h": 150.0, "target_drain_min": 240.0, "discharge_ok": False, "discharge_dist_m": 4000.0, "discharge_to": "X", "lon": 54.5, "lat": 24.4, "note": "", "top_kind": "road"},
                        {"fp_id": "B", "priority": 5.0, "volume_now_m3": 600.0, "inflow_now_m3h": 0.0, "required_rate_m3h": 150.0, "target_drain_min": 240.0, "discharge_ok": True, "discharge_dist_m": 300.0, "discharge_to": "Y", "lon": 54.6, "lat": 24.4, "note": "", "top_kind": "road"},
                        {"fp_id": "C", "priority": 3.0, "volume_now_m3": 900.0, "inflow_now_m3h": 0.0, "required_rate_m3h": 300.0, "target_drain_min": 240.0, "discharge_ok": False, "discharge_dist_m": np.nan, "discharge_to": None, "lon": 54.7, "lat": 24.4, "note": "", "top_kind": "road"}])
    depots = pd.DataFrame([{"depot_id": "D1", "lon": 54.5, "lat": 24.5}, {"depot_id": "D2", "lon": 54.7, "lat": 24.5}])
    T = np.array([[10.0, 20.0, 40.0], [30.0, 25.0, 12.0]])
    plan = pd.DataFrame(columns=["fp_id"])
    tk = dp.assign_tankers(pts, plan, 8, T, depots, CFG, 210.0)
    assert set(tk["fp_id"]) == {"A", "C"} and "B" not in set(tk["fp_id"])            # B 有排放去处，归泵
    a = tk[tk["fp_id"] == "A"]; c = tk[tk["fp_id"] == "C"]
    assert len(a) == 6 and a["from"].iloc[0] == "D1" and a["arrive_min"].iloc[0] == 210 + 10 + 10       # 4 km：周转 36 min → 28.3 m³/h，150 / 28.3 → 6 辆（也是每点上限）；最近驻地 D1；到位 = 决策 + 行程 + 装车
    assert len(c) == 2 and c["from"].iloc[0] == "D2"                                                    # 池只剩 2
    assert tk["model"].iloc[0] == "tanker-17m3" and tk["duty_aed_h"].iloc[0] == 250 and tk["rate_m3h"].iloc[0] > 25
    # 已被泵处置的点不再派罐车
    tk2 = dp.assign_tankers(pts, pd.DataFrame([{"fp_id": "A"}]), 8, T, depots, CFG, 210.0)
    assert set(tk2["fp_id"]) == {"C"}
    # 关掉
    cfg_off = {"dispatch": {"tankers": {**CFG["dispatch"]["tankers"], "enabled": False}}}
    assert len(dp.assign_tankers(pts, plan, 8, T, depots, cfg_off, 210.0)) == 0


def test_tankers_skip_hopeless_and_cap_haul():
    r_far, cyc_far = dp.tanker_rate_m3h(CFG, 100000.0)      # 100 km 超过 max_haul_km → 按缺省 5 km
    r_def, cyc_def = dp.tanker_rate_m3h(CFG, None)
    assert cyc_far == cyc_def
    pts = pd.DataFrame([{"fp_id": "H", "priority": 9.0, "volume_now_m3": 200000.0, "inflow_now_m3h": 0.0, "required_rate_m3h": 50000.0, "target_drain_min": 240.0, "discharge_ok": False, "discharge_dist_m": np.nan, "discharge_to": None, "lon": 54.5, "lat": 24.4, "note": "", "top_kind": "road"},
                        {"fp_id": "S", "priority": 1.0, "volume_now_m3": 300.0, "inflow_now_m3h": 0.0, "required_rate_m3h": 75.0, "target_drain_min": 240.0, "discharge_ok": False, "discharge_dist_m": np.nan, "discharge_to": None, "lon": 54.6, "lat": 24.4, "note": "", "top_kind": "road"}])
    depots = pd.DataFrame([{"depot_id": "D1", "lon": 54.5, "lat": 24.5}])
    tk = dp.assign_tankers(pts, pd.DataFrame(columns=["fp_id"]), 8, np.array([[10.0, 12.0]]), depots, CFG, 210.0)
    assert set(tk["fp_id"]) == {"S"} and len(tk) == 3                # 20 万 m³ 的点 6 辆拉不完 → 跳过；300 m³ 的点 75/25.5 → 3 辆
