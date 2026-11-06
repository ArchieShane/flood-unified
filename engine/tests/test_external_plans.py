"""外部预案并排：客户预案 A 就地、Parsons 式按区域从驻地出发（合成小例子）。"""
import numpy as np
import pandas as pd
from swbrain import dispatch as dp, replay

HYD = [{"t_min": 0, "volume_m3": 0, "inflow_m3h": 0}, {"t_min": 60, "volume_m3": 3000, "inflow_m3h": 3000},
       {"t_min": 75, "volume_m3": 3000, "inflow_m3h": 0}, {"t_min": 675, "volume_m3": 0, "inflow_m3h": 0}]


def _ctx():
    fleet = {"models": [{"model": "diesel-12in", "rate_m3h": 700, "setup_min": 25}, {"model": "diesel-8in", "rate_m3h": 350, "setup_min": 20}, {"model": "diesel-6in", "rate_m3h": 180, "setup_min": 15}]}
    hs = pd.DataFrame([{"hotspot_id": "ADM-1", "area": "Zayed", "planA_pump12": 0, "planA_pump8": 1, "planA_pump6": 1, "planA_pumps": 2},
                       {"hotspot_id": "ADM-2", "area": "Mussafah", "planA_pump12": 0, "planA_pump8": 0, "planA_pump6": 0, "planA_pumps": 1}])
    return {"fleet": fleet, "hotspots": hs}


def test_onsite_and_staging_plans():
    ctx = _ctx()
    pts = pd.DataFrame([{"fp_id": "ADM-1", "client_hotspot_id": "ADM-1", "volume_m3": 3000.0, "target_drain_min": 240.0, "priority": 5.0, "level": "III", "hydrograph": HYD},
                        {"fp_id": "ADM-2", "client_hotspot_id": "ADM-2", "volume_m3": 3000.0, "target_drain_min": 600.0, "priority": 2.0, "level": "II", "hydrograph": HYD}])
    sites = pd.DataFrame([{"site_id": "S1", "name": "站1", "lon": 54.4, "lat": 24.4, "area": None}, {"site_id": "S2", "name": "站2", "lon": 54.5, "lat": 24.3, "area": None},
                          {"site_id": "POND-RB3-PO-1", "name": "PO-1 (RB3)", "lon": 54.41, "lat": 24.41, "area": "Zayed"}, {"site_id": "POND-M-PO-3", "name": "PO-3 (M)", "lon": 54.51, "lat": 24.31, "area": "Mussafah"}])
    T = np.array([[10.0, 40.0], [45.0, 12.0], [6.0, 50.0], [50.0, 7.0]])
    cfg = {"dispatch": {"preposition_depart_min": 30, "max_units_per_point": 3,
                        "plans": {"a": {"kind": "onsite", "label": "预案 A", "depart_min": 30},
                                  "p": {"kind": "staging", "label": "Parsons", "model": "diesel-12in", "depart_min": 30,
                                        "catchments": {"Zayed": {"name": "RB3", "pumps": 2}, "Mussafah": {"name": "M", "pumps": 1}, "Nowhere": {"name": "X", "pumps": 5}}}}}}
    plan_after = pd.DataFrame([{"fp_id": "ADM-1", "unit_id": "u0", "model": "diesel-8in", "rate_m3h": 350.0, "arrive_min": 280, "pumping_from_min": 300}])
    rows_after, s_after = replay.replay_plan(pts, plan_after)
    plan_after = plan_after.merge(pd.DataFrame(rows_after), on="fp_id", how="left")
    out = dp.external_plans(ctx, pts, sites, T, cfg, plan_after, s_after)
    assert set(out) == {"a", "p"}
    a, p = out["a"], out["p"]
    assert a["kind"] == "onsite" and a["value"] is not None and a["pumps_total"] == 3            # 1×8 寸 + 1×6 寸 + 1 台未注明口径按 6 寸
    assert a["value"]["units_placed"] == 3 and a["value"]["arrive_median_min"] <= 30 + 20        # 就地：出发即到位 + 架设
    assert p["kind"] == "staging" and p["value"] is not None and p["pumps_total"] == 8 and p["pumps_placed_pool"] == 3   # Nowhere 没有积水点，不算进池
    assert p["value"]["units_placed"] == 3 and p["value"]["points_with_pumps"] == 2
    # 就地预置比从驻地出发更早到位；两者都比事后调度早
    assert a["value"]["arrive_median_min"] < p["value"]["arrive_median_min"] < a["value"]["after_arrive_median_min"]
    assert p["value"]["flood_h"]["pre"] < p["value"]["flood_h"]["base"]
    assert p["sites_prefix"] is None and len(p["staging_sites"]) == 2          # 没指定 sites_prefix：每个区域从最近的候选驻地出发
    # 指定 sites_prefix=POND-：Parsons 的泵只从同区域的塘出发
    cfg["dispatch"]["plans"]["p"]["sites_prefix"] = "POND-"
    out2 = dp.external_plans(ctx, pts, sites, T, cfg, plan_after, s_after)
    p2 = out2["p"]
    assert p2["sites_prefix"] == "POND-" and all(str(x["site_id"]).startswith("POND-") for x in p2["staging_sites"])
    assert {x["area"] for x in p2["staging_sites"]} == {"Zayed", "Mussafah"} and p2["value"]["units_placed"] == 3
    assert p2["value"]["arrive_median_min"] <= p["value"]["arrive_median_min"]     # 塘不比原来远（这里塘本来就是最近的候选）
    # 指示性成本：每张预案都带；就地预案 3 台，每台至少最短计费
    assert a["value"]["cost"]["units"] == 3 and a["value"]["cost"]["total_aed"] > 0 and p["value"]["cost"]["units"] == 3
