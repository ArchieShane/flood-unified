"""单热点详情的纯函数：管径往上取档；换管后能力变大、缺口变小；三条曲线同网格。"""
import pandas as pd
from swbrain import hotspotdetail as hd

CFG = {"idf": {"equation": {"10": {"alpha": 31.0, "beta": 0.62}}, "return_periods": {"10": {"i15_mmh": 57.7, "p24_mm": 51.1}}, "durations_min": [5, 10, 15, 30, 60, 120, 180, 360, 720, 1440]},
       "model": {"runoff_c": 0.85, "default_slope": 0.003}, "cost": {"pipe_aed_per_m_by_dn": {"300": 1, "400": 1, "600": 1, "800": 1, "1000": 1}}, "scenarios": {"hyetographs_file": "nonexistent.json"},
       "dispatch": {"decision_time_min": 210}}


def test_next_dns():
    assert hd.next_dns(300, CFG) == [400, 600] and hd.next_dns(800, CFG, 3) == [1000] and hd.next_dns(None, CFG, 1) == [400]


def test_curves_upsizing_reduces_shortfall():
    from swbrain import scenarios as sc
    S = sc.get(CFG, "10")
    row = {"area_ha": 20.0, "c": 0.8, "tc_min": 15.0, "pond_m3": 0.0, "area": "Zayed"}
    reach = {"pipe_diameter": 300.0, "slope": 0.003, "n_manning": 0.013, "q_full": 0.10}
    base_hyd, v0 = hd.hydrograph_for_capacity(CFG, S, row, 0.10)
    assert v0 > 0 and base_hyd[0]["volume_m3"] == 0 and max(p["volume_m3"] for p in base_hyd) == round(v0)
    plan = pd.DataFrame([{"fp_id": "X", "unit_id": "u1", "model": "diesel-12in", "rate_m3h": 700.0, "arrive_min": 240, "pumping_from_min": 265, "from": "d1"}])
    c = hd.curves(CFG, S, row, reach, base_hyd, plan, dn_steps=2)
    assert len(c["t_min"]) == len(c["base"]) == len(c["pump"]) and c["t_min"][1] - c["t_min"][0] == 15.0
    assert c["pump_kpi"]["flood_h"] <= c["base_kpi"]["flood_h"] and c["pumps"][0]["rate_m3h"] == 700.0
    assert [o["dn_mm"] for o in c["upsized"]] == [400, 600]
    assert c["upsized"][0]["q_full_m3s"] > 0.10 and c["upsized"][1]["q_full_m3s"] > c["upsized"][0]["q_full_m3s"]
    assert c["upsized"][0]["shortfall_m3"] >= c["upsized"][1]["shortfall_m3"] and c["upsized"][1]["shortfall_m3"] < v0
    assert max(c["upsized"][1]["curve"]) <= max(c["base"])
