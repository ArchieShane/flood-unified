"""本系统积水点：手册 3.6 超出体积、过程线、DTM 深度换算、契约构建（不依赖数据库与文件）。"""
import numpy as np
import pandas as pd
from swbrain import floodpoints as fpm

CFG = {"idf": {"equation": {"5": {"alpha": 27.0, "beta": 0.62}, "10": {"alpha": 31.0, "beta": 0.62}, "50": {"alpha": 44.0, "beta": 0.62}},
               "return_periods": {"5": {"i15_mmh": 51.0, "p24_mm": 44.9}, "10": {"i15_mmh": 57.7, "p24_mm": 51.1}, "50": {"i15_mmh": 118.2, "p24_mm": 81.8}}},
       "model": {"runoff_c": 0.85, "tide_high_m": 1.3}, "dispatch": {"decision_time_min": 210},
       "gwm": {}, "exchange": {"dir": "exchange"}, "hotspots": {"file": "x"}}


def test_excess_volume_grows_with_return_period_and_zero_when_capacity_enough():
    v5 = fpm.excess_volume(20.0, 0.85, 15.0, 0.30, 5, CFG)
    v50 = fpm.excess_volume(20.0, 0.85, 15.0, 0.30, 50, CFG)
    assert v50["V_m3"] > v5["V_m3"] > 0 and v50["d_star_min"] >= 15.0 and v50["excess_m3s"] > 0
    big = fpm.excess_volume(1.0, 0.85, 15.0, 5.0, 50, CFG)
    assert big["V_m3"] == 0.0 and big["excess_m3s"] == 0.0


def test_hydrograph_network_rises_holds_then_recedes():
    h = fpm.hydrograph_network(15.0, 60.0, 0.5, 0.8, 5000.0, 0.3)
    ts = [p["t_min"] for p in h]; vs = [p["volume_m3"] for p in h]
    assert ts == sorted(ts) and vs[0] == 0 and max(vs) == 5000 and vs[-1] == 0
    assert h[1]["inflow_m3h"] == 1800 and h[3]["inflow_m3h"] == 0
    # 退水时长 = V / Qcap：5000 / (0.3·3600) h ≈ 4.6 h → 约 278 min 加在 d*+tc 之后
    assert abs((h[-1]["t_min"] - 75) - 5000 / (0.3 * 3600) * 60) < 1.0
    h0 = fpm.hydrograph_network(15.0, 60.0, 0.5, 0.8, 5000.0, 0.0)      # 没有管网能力：不退水
    assert h0[-1]["volume_m3"] == 5000


def test_depth_area_scaling_and_spill():
    r = pd.Series({"pond_volume_m3": 4000.0, "pond_area_m2": 10000.0, "depth_max_m": 0.8})
    d, a, s = fpm._depth_area(1000.0, r, 0.1)
    assert abs(d - 0.8 * np.sqrt(0.25)) < 1e-6 and a == 2500.0 and s == 0.0
    d2, a2, s2 = fpm._depth_area(6000.0, r, 0.1)
    assert d2 == 0.8 and a2 == 10000.0 and s2 == 2000.0
    flat = pd.Series({"pond_volume_m3": 0.0, "pond_area_m2": 0.0, "depth_max_m": 0.0})
    assert fpm._depth_area(500.0, flat, 0.1) == (0.1, 5000.0, 0.0)


def test_build_contract_from_matched_frame(monkeypatch):
    m = pd.DataFrame([
        {"hotspot_id": "ADM-1", "criticality": "Very High", "location": "نفق X", "area": "Zayed", "region": "ADM", "network_available": True, "root_cause": "capacity",
         "class": "underpass", "area_ha": 20.0, "c": 0.8, "tc_min": 15.0, "qcap": 0.30, "pond_m3": 0.0, "lon": 54.5, "lat": 24.4,
         "dtm_z": 5.0, "depth_max_m": 1.0, "pond_volume_m3": 3000.0, "pond_area_m2": 8000.0, "spill_z": 6.0, "terrain_note": "", "is_tunnel": True},
        {"hotspot_id": "AAM-1", "criticality": "Medium", "location": "Hili", "area": "Hili", "region": "AAM", "network_available": False, "root_cause": "no_network",
         "class": "general", "area_ha": np.nan, "c": np.nan, "tc_min": np.nan, "qcap": np.nan, "pond_m3": np.nan, "lon": 55.8, "lat": 24.3,
         "dtm_z": 250.0, "depth_max_m": 0.5, "pond_volume_m3": 800.0, "pond_area_m2": 4000.0, "spill_z": 250.5, "terrain_note": "", "is_tunnel": False},
        {"hotspot_id": "DRM-1", "criticality": "Low", "location": "Liwa", "area": "Liwa", "region": "DRM", "network_available": False, "root_cause": "no_network",
         "class": "general", "area_ha": np.nan, "c": np.nan, "tc_min": np.nan, "qcap": np.nan, "pond_m3": np.nan, "lon": 53.7, "lat": 23.1,
         "dtm_z": 100.0, "depth_max_m": 0.02, "pond_volume_m3": 20.0, "pond_area_m2": 200.0, "spill_z": 100.0, "terrain_note": "flat", "is_tunnel": False},
    ])
    fp = fpm.build(CFG, 10, matched=m)
    assert fp["schema"] == "swbrain.flood_points/1" and fp["model"]["type"] == "system"
    ids = {p["fp_id"]: p for p in fp["points"]}
    assert set(ids) == {"ADM-1", "AAM-1"}                       # DRM-1 只有 20 m³，低于下限被滤掉
    a = ids["ADM-1"]; b = ids["AAM-1"]
    assert a["source"] == "network_gap" and a["land_use"] == "tunnel" and a["target_drain_min"] == 120 and a["volume_m3"] > 0
    assert b["source"] == "dtm_proxy" and b["target_drain_min"] == 600 and b["volume_m3"] == 800 and b["max_depth_m"] == 0.5
    assert fp["stats"]["n_network_gap"] == 1 and fp["stats"]["n_dtm_proxy"] == 1 and fp["scenario"]["return_period_yr"] == 10
    assert all(p["depth_m"] is not None for p in a["hydrograph"]) and fp["scenario"]["suggested_decision_time_min"] > 0


def test_hydrograph_from_hyetograph_mass_balance():
    # 1 h 恒定 60 mm/h（5 分钟累计 5 mm），C=1，A=1 ha，tc=5 → Q = K·60 = 0.1662 m³/s；Q_cap = 0.05
    hy = {"dt_min": 5.0, "cum_mm": [5.0 * k for k in range(13)]}
    r = fpm.hydrograph_from_hyetograph(1.0, 1.0, 5.0, 0.05, hy)
    q = fpm.K * 60.0
    assert abs(r["q_peak_m3s"] - q) < 1e-9 and abs(r["excess_peak_m3s"] - (q - 0.05)) < 1e-9
    assert abs(r["v_peak_m3"] - (q - 0.05) * 3600.0) < 1.0 and 55 <= r["t_peak_min"] <= 65          # 一小时蓄到 (Q−Qcap)·3600
    assert r["t_end_min"] is not None and abs(r["t_end_min"] - (60 + r["v_peak_m3"] / (0.05 * 60))) < 6   # 雨停后按 Q_cap 退干
    ts = [p["t_min"] for p in r["hydrograph"]]; vs = [p["volume_m3"] for p in r["hydrograph"]]
    assert ts == sorted(ts) and vs[0] == 0 and max(vs) == round(r["v_peak_m3"]) and vs[-1] == 0
    # 管够大：不积水
    r2 = fpm.hydrograph_from_hyetograph(1.0, 1.0, 5.0, 1.0, hy)
    assert r2["v_peak_m3"] == 0.0 and r2["excess_peak_m3s"] == 0.0


def test_event_hyetograph_scaling(tmp_path, monkeypatch):
    import json
    f = tmp_path / "rain_events.json"
    f.write_text(json.dumps({"2024-04": {"Zayed": {"dt_min": 5.0, "cum_mm": [0, 50, 100], "depth_mm": 100.0}}}), encoding="utf-8")
    cfg = {**CFG, "scenarios": {"hyetographs_file": str(f), "events": {"2024-04": {"base_rp": 10, "p24_ref_mm": 51.12, "p24_default_mm": 94.0, "p24_by_area": {"Al Shahama": 160.0}, "label": "e", "label_en": "e"}}}}
    from swbrain import scenarios as sc
    S = sc.get(cfg, "2024-04")
    a = fpm.event_hyetograph(cfg, S, "Zayed"); assert a["scaled_from"] is None and a["cum_mm"][-1] == 100
    b = fpm.event_hyetograph(cfg, S, "Al Shahama"); assert b["scaled_from"] == "Zayed" and abs(b["cum_mm"][-1] - 160.0) < 1e-6 and b["depth_mm"] == 160.0
    c = fpm.event_hyetograph(cfg, S, "Island"); assert abs(c["cum_mm"][-1] - 94.0) < 1e-6                    # 没数的区按缺省 24 h 雨量
    assert fpm.event_hyetograph(cfg, sc.get(cfg, "10"), "Zayed") is None
