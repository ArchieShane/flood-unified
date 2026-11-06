"""退水方案：合成上下文里，单点最优比全局给的快、预置比事中快、代价（抢走的泵）算得出、无排放去处的点走罐车、结论文字齐全。"""
import numpy as np
import pandas as pd

from swbrain import recession as rc
from swbrain.db import load_config

cfg = load_config()
MAIN = list(cfg["model"]["scenarios"])[0]


def _hyd(peak_m3, t_peak=180, t_end=1440, q=600.0):
    """三角形过程线：t_peak 前线性涨到 peak，之后按 q m³/h 的入流慢慢退（体积线性降到 0）。"""
    rows = []
    for t in range(0, t_end + 1, 15):
        v = peak_m3 * t / t_peak if t <= t_peak else max(peak_m3 * (1 - (t - t_peak) / (t_end - t_peak)), 0.0)
        rows.append({"t_min": float(t), "volume_m3": round(v, 1), "inflow_m3h": (q if t < t_peak else 120.0), "depth_m": round(0.5 * v / max(peak_m3, 1), 3)})
    return rows


def _ctx():
    pts0 = pd.DataFrame([
        {"fp_id": "P1", "lon": 54.50, "lat": 24.40, "volume_m3": 3000.0, "duration_min": 1440, "max_depth_m": 0.6, "hydrograph": _hyd(3000.0), "priority": 100.0, "target_drain_min": 240.0, "top_kind": "tunnel", "facilities": ["tunnel"], "client_criticality": "Very High", "land_use": "tunnel", "note": "", "geom": None, "target_drain_min_pt": 240.0},
        {"fp_id": "P2", "lon": 54.60, "lat": 24.40, "volume_m3": 1200.0, "duration_min": 1440, "max_depth_m": 0.4, "hydrograph": _hyd(1200.0), "priority": 20.0, "target_drain_min": 360.0, "top_kind": "none", "facilities": [], "client_criticality": "Medium", "land_use": "road", "note": "", "geom": None, "target_drain_min_pt": 360.0},
        {"fp_id": "P3", "lon": 55.30, "lat": 23.90, "volume_m3": 900.0, "duration_min": 1440, "max_depth_m": 0.4, "hydrograph": _hyd(900.0), "priority": 30.0, "target_drain_min": 360.0, "top_kind": "none", "facilities": [], "client_criticality": "Medium", "land_use": "road", "note": "", "geom": None, "target_drain_min_pt": 360.0}])
    models = [{"model": "diesel-12in", "rate_m3h": 700.0, "setup_min": 25.0, "vehicle_class": "heavy", "duty_aed_h": 90}, {"model": "diesel-8in", "rate_m3h": 350.0, "setup_min": 20.0, "vehicle_class": "light", "duty_aed_h": 60}]
    fleet = {"models": models, "depots": [{"depot_id": "D1", "name": "d1", "lon": 54.52, "lat": 24.41}, {"depot_id": "D2", "name": "d2", "lon": 54.62, "lat": 24.41}],
             "units": [{"unit_id": "u1", "model": "diesel-12in", "depot_id": "D1", "lon": 54.52, "lat": 24.41}, {"unit_id": "u2", "model": "diesel-12in", "depot_id": "D2", "lon": 54.62, "lat": 24.41},
                       {"unit_id": "u3", "model": "diesel-8in", "depot_id": "D1", "lon": 54.52, "lat": 24.41}, {"unit_id": "u4", "model": "diesel-8in", "depot_id": "D2", "lon": 54.62, "lat": 24.41}]}
    mm = {m["model"]: m for m in models}
    units = pd.DataFrame([{**u, **{k: v for k, v in mm[u["model"]].items() if k != "model"}, "from": u["depot_id"], "avail_min": 0.0} for u in fleet["units"]])
    depots = pd.DataFrame(fleet["depots"])
    sites = pd.DataFrame([{"site_id": "D1", "name": "d1", "lon": 54.52, "lat": 24.41}, {"site_id": "S9", "name": "staging near P1", "lon": 54.501, "lat": 24.401}])
    T_d = np.array([[8.0, 40.0, 60.0], [40.0, 8.0, 70.0]])              # 驻地→点（分钟）；P3 远但到得了
    T_sites = np.array([[8.0, 40.0, 60.0], [2.0, 45.0, 65.0]])
    cs = pd.DataFrame([{"terminal_kind": "出水口", "terminal_unitid": "OF-1", "lon": 54.503, "lat": 24.40, f"over_share_{MAIN}": 0.1},
                       {"terminal_kind": "出水口", "terminal_unitid": "OF-2", "lon": 54.601, "lat": 24.40, f"over_share_{MAIN}": 0.1}])
    return {"pts0": pts0, "units": units, "depots": depots, "sites": sites, "T_heavy_d": T_d, "T_light_d": T_d, "T_pp": None, "T_sites": T_sites, "cs": cs, "fleet": fleet, "fp": {"scenario": {}}}


def test_options_single_vs_global_and_prepo():
    ctx = _ctx(); t = 210.0
    # 全局调度单：只给 P2 一台 8 寸（u3 从 D1 出发 40 min）
    plan_g = pd.DataFrame([{"wave": 1, "unit_id": "u3", "model": "diesel-8in", "rate_m3h": 350.0, "from": "D1", "fp_id": "P2", "arrive_min": 250.0, "pumping_from_min": 270.0, "done_min": 400.0, "within_target": True}])
    payload_rows = [{"fp_id": "P2", "unit_id": "u3", "replay_saved_min": 300.0}]
    r = rc.options(ctx, cfg, "P1", t, plan_g, payload_rows, upsize={"upsized": [{"dn_mm": 600, "q_full_m3s": 0.6, "shortfall_m3": 0.0, "kpi": {"peak_m3": 0, "t_empty_min": 0, "flood_h": 0}}]}, scenario={"key": "10", "label": "10 年", "label_en": "10-yr"})
    keys = [o["key"] for o in r["options"]]
    assert keys[:4] == ["natural", "global", "single", "prepo"] and "upsize_600" in keys
    nat, glob, single, prepo = (next(o for o in r["options"] if o["key"] == k) for k in ("natural", "global", "single", "prepo"))
    assert nat["n_units"] == 0 and nat["t_empty_min"] is not None
    assert glob["n_units"] == 0 and "本轮没排上" in glob["note_zh"] and r["point"]["priority_rank_dispatchable"] == 1
    assert single["n_units"] == 3 and {u["unit_id"] for u in single["units"]} in ({"u1", "u2", "u3"}, {"u1", "u3", "u4"})   # and 比 or 紧：括号不能省，否则机组身份判定失效
    assert single["t_empty_min"] < nat["t_empty_min"]                                       # 单点最优比自然退水快
    assert prepo["site"]["site_id"] == "S9" and prepo["t_empty_min"] <= single["t_empty_min"]    # 预置从 2 min 车程的驻地出发，不慢于事中
    assert r["fastest"] in ("prepo", "single")
    svg = r["single_vs_global"]
    if "u3" in {u["unit_id"] for u in single["units"]}:
        assert svg["units_taken_from_global"] == ["u3"] and svg["displaced_points"][0]["fp_id"] == "P2" and svg["displaced_saved_min_total"] == 300.0
        assert "抢走" in r["recommendation"]["zh"] and "taking" in r["recommendation"]["en"]
    assert r["need_units_for_target"] and r["need_units_for_target"]["n"] is not None and r["need_units_for_target"]["n"] >= 1
    assert "最快退水的手段" in r["recommendation"]["zh"] and "Fastest recession" in r["recommendation"]["en"] and "换管 DN600" in r["recommendation"]["zh"]
    assert r["point"]["n_candidate_pumps"] == 4 and r["target_drain_min"] == 240.0


def test_options_no_discharge_goes_tankers_and_unreachable():
    ctx = _ctx()
    # P3：1.5 km 内没有出水口 → 泵到得了也没处排，单点最优不成立，走罐车
    r = rc.options(ctx, cfg, "P3", 210.0, pd.DataFrame(), None)
    keys = [o["key"] for o in r["options"]]
    single = next(o for o in r["options"] if o["key"] == "single")
    assert single["n_units"] == 0 and "排放去处" in single["note_zh"] and "prepo" not in keys
    tk_on = bool((cfg["dispatch"].get("tankers") or {}).get("enabled", True))
    assert ("tankers" in keys) == tk_on
    if tk_on:
        tk = next(o for o in r["options"] if o["key"] == "tankers")
        assert tk["n_units"] >= 1 and tk["units"][0]["model"].startswith("tanker") and "排放去处" in tk["note_zh"]
    assert r["point"]["n_candidate_pumps"] == 4 and r["single_vs_global"]["units_taken_from_global"] == [] and r["need_units_for_target"] is None
    assert "不派" in r["recommendation"]["zh"]
    # 泵到不了的点：候选为 0
    ctx2 = _ctx(); ctx2["T_heavy_d"] = ctx2["T_light_d"] = np.array([[8.0, 40.0, np.inf], [40.0, 8.0, np.inf]])
    assert rc.options(ctx2, cfg, "P3", 210.0, pd.DataFrame(), None)["point"]["n_candidate_pumps"] == 0


def test_best_units_enumeration():
    pts = pd.DataFrame([{"fp_id": "X", "volume_now_m3": 1000.0, "inflow_now_m3h": 100.0, "relay_needed": False, "relay_stages": 0, "relay_factor": 1.0}])
    cand = pd.DataFrame({"rate_m3h": [700.0, 350.0, 350.0], "setup_min": [25.0, 20.0, 20.0], "arr": [30.0, 100.0, 120.0]}, index=[7, 8, 9])
    ix, done = rc.best_units(pts, 0, cand, 3)
    # 一台大泵 30 min 到：done = 30 + 1000/600*60 = 130；加一台 8 寸要等到 100 min：100 + 1000/950*60 = 163 → 只用大泵
    assert ix == [7] and abs(done - 130.0) < 1e-6
    ix2, done2 = rc.best_units(pts, 0, cand.assign(arr=[30.0, 31.0, 32.0]), 3)
    assert set(ix2) == {7, 8, 9} and done2 < 130.0
    assert rc.best_units(pts, 0, cand.iloc[0:0], 3) == ([], None)
