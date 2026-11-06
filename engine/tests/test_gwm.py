"""世界模型适配器：用一张小合成格网验证聚合逻辑（不依赖容器）。"""
import numpy as np
from swbrain import gwm


def _cfg():
    return {"gwm": {"depth_min_m": 0.3, "separation_m": 750, "assign_radius_m": 1500, "max_points": 10, "hydrograph_step_min": 15},
            "exchange": {"network_version": "test"}, "model": {"tide_high_m": 1.0}}


def _grid():
    R, C, cell = 12, 16, 250.0
    x = 227000.0 + np.arange(C + 1) * cell
    y = 2723750.0 - np.arange(R + 1) * cell
    land = np.ones((R, C), bool); land[:, :2] = False          # 左两列是海
    T = 25; times = np.arange(T) * 300.0
    depth = np.zeros((T, R * C), np.float32)
    ramp = np.linspace(0, 1, T)
    # 核 A：(3,5) 深 1.0 m，邻格 0.5；核 B：(9,12) 深 0.6，邻格 0.4；相距 > 750 m
    for (r, c, d) in [(3, 5, 1.0), (3, 6, 0.5), (4, 5, 0.5), (9, 12, 0.6), (9, 13, 0.4), (10, 12, 0.4)]:
        depth[:, r * C + c] = d * ramp
    # 一格 0.2 m：低于阈值，不算积水
    depth[:, 6 * C + 8] = 0.2 * ramp
    # 海上一格很深：land_mask 应把它排除
    depth[:, 5 * C + 0] = 3.0 * ramp
    return depth, times, x, y, land


def test_aggregate_cores_and_members():
    depth, times, x, y, land = _grid()
    pts, st = gwm.aggregate(depth, times, x, y, land, "EPSG:32640", _cfg())
    assert st["wet_cells"] == 6 and st["cores"] == 2 and st["points"] == 2 and st["diffuse_cells"] == 0
    a = next(p for p in pts if p["gwm"]["core_cell"] == [3, 5]); b = next(p for p in pts if p["gwm"]["core_cell"] == [9, 12])
    assert a["gwm"]["cells"] == 3 and b["gwm"]["cells"] == 3
    assert abs(a["max_depth_m"] - 1.0) < 1e-6 and abs(a["area_m2"] - 3 * 62500) < 1
    assert abs(a["volume_m3"] - (1.0 + 0.5 + 0.5) * 62500) < 1        # 末时刻体积
    assert pts[0]["fp_id"] == a["fp_id"]                                # 深×体积排序，A 在前
    assert a["geometry"]["type"] in ("Polygon", "MultiPolygon") and 54 < a["lon"] < 55 and 24 < a["lat"] < 25


def test_hydrograph_monotone_and_inflow_nonnegative():
    depth, times, x, y, land = _grid()
    pts, st = gwm.aggregate(depth, times, x, y, land, "EPSG:32640", _cfg())
    h = pts[0]["hydrograph"]
    assert h[0]["t_min"] == 0 and h[-1]["t_min"] == 120
    vols = [q["volume_m3"] for q in h]; assert vols == sorted(vols)
    assert all(q["inflow_m3h"] >= 0 for q in h) and h[1]["inflow_m3h"] > 0
    assert st["suggested_decision_time_min"] % 30 == 0


def test_separation_merges_close_cores():
    depth, times, x, y, land = _grid()
    # 在核 A 旁边 500 m 放一个稍浅的极大值：应被抑制、并进 A
    depth[:, 3 * 16 + 7] = 0.8 * np.linspace(0, 1, depth.shape[0])
    pts, st = gwm.aggregate(depth, times, x, y, land, "EPSG:32640", _cfg())
    assert st["cores"] == 2
    a = next(p for p in pts if p["gwm"]["core_cell"] == [3, 5]); assert a["gwm"]["cells"] == 4


def test_to_flood_points_contract():
    depth, times, x, y, land = _grid()
    pts, st = gwm.aggregate(depth, times, x, y, land, "EPSG:32640", _cfg())
    record = {"run_id": "trained-gwm-20260919T000000Z-deadbeef", "created_at": "2026-09-19T00:00:00+00:00", "metrics": {"maximum_depth_m": 1.0},
              "metadata": {"model": {"release_id": "GWM-R1-test"}, "solver": "test", "grid": {"crs": "EPSG:32640", "cell_size_m": 250.0, "rows": 12, "columns": 16},
                           "claim_boundary": "test", "event": {}, "scenario": {"scenario_total_precipitation_mm": 90.0, "rainfall_duration_hours": 24, "simulation_duration_hours": 32}}}
    fp = gwm.to_flood_points(record, pts, st, _cfg(), return_period_yr=50)
    assert fp["schema"] == "swbrain.flood_points/1" and fp["simulated"] is False and fp["model"]["type"] == "world"
    assert fp["scenario"]["name"].startswith("50年一遇") and fp["scenario"]["name_en"].startswith("50-year") and fp["scenario"]["duration_min"] == 32 * 60
    assert fp["scenario"]["suggested_decision_time_min"] == st["suggested_decision_time_min"]
    assert all({"fp_id", "geometry", "lon", "lat", "max_depth_m", "volume_m3", "hydrograph"} <= set(p) for p in fp["points"])


def test_idf_lookup_and_key():
    import pytest
    cfg = {"idf": {"return_periods": {"50": {"i15_mmh": 75, "p24_mm": 90}}, "duration_hours": 24}}
    assert gwm.idf_for(cfg, 50)["p24_mm"] == 90
    assert gwm.idempotency_key(50, 90.0, 24) == "swbrain-rp50-90mm-24h"
    assert gwm.idempotency_key(None, 100.0, 24) == "swbrain-100mm-24h"
    with pytest.raises(KeyError):
        gwm.idf_for(cfg, 500)
