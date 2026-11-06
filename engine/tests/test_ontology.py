"""本体层：声明自检；假后端上的取对象 / 查对象集 / 关系 / 规则；动作参数校验与副作用；规则规划器的意图识别；智能体回答拼装；大模型工具声明。"""
import json

import pandas as pd
import pytest

from swbrain.ontology import schema as S, runtime as R, agent as A


class Fake:
    active_rp = "10"
    cfg = {"model": {"scenarios": {}}, "dispatch": {"target_drain_min": {"tunnel": 180, "default": 360}, "relay_beyond_m": 300}, "alerts": {"rain_mm": {"黄": 45, "橙": 70, "红": 90}},
           "idf": {"return_periods": {"5": {"i15_mmh": 51.0, "p24_mm": 44.9}, "10": {"i15_mmh": 57.7, "p24_mm": 51.1}, "25": {"i15_mmh": 84.1, "p24_mm": 69.6}, "50": {"i15_mmh": 118.2, "p24_mm": 81.8}, "100": {"i15_mmh": 138.5, "p24_mm": 96.0}}},
           "agent": {"observations_file": "out/_test_obs.jsonl", "action_log": "out/_test_actions.jsonl"}, "exchange": {"dir": "exchange"}}
    es = pd.DataFrame([{"gid": 1, "unitid": "E1", "zone_name": "Mussafah", "len_m": 120.0, "pipe_diameter": 600, "pipe_material": "RC", "slope": 0.002, "q_full": 0.46, "terminal_kind": "outfall", "design_class": "local", "design_rp_yr": 10, "ratio_design": 1.3, "n_inlets_up": 12, "area_m2": 36000.0, "nf_spill_m3_10": 500.0, "nf_backwater_10": False}])
    zs = pd.DataFrame([{"zone_name": "Mussafah", "district_name": "Abu Dhabi", "n_edges": 1, "len_km": 0.12, "risk_score": 55.0, "over_share_常规": 0.2, "over_share_极端": 0.5, "storage_deficit_m3": 1000.0, "n_tide_outfalls": 0, "n_suspect_outlets": 1, "blockage_per_100km": 3.0}])
    hotspots = pd.DataFrame([{"hotspot_id": "ADM-1", "region": "ADM", "area": "Al Shahama", "location": "Al Shahama Tunnel", "criticality": "Very High", "root_cause": "capacity", "network_available": True, "lon": 54.69, "lat": 24.54, "lon_xlsx": 54.69, "lat_xlsx": 24.60, "edge_gid": 1, "planA_pumps": 3, "idd_planned_flag": "no", "nf_spill_m3_10": 500.0, "nf_backwater_10": False},
                             {"hotspot_id": "ADM-2", "region": "ADM", "area": "X", "location": "Somewhere", "criticality": "Low", "root_cause": "other", "network_available": False, "lon": 54.5, "lat": 24.4, "lon_xlsx": 54.5, "lat_xlsx": 24.4, "edge_gid": None, "planA_pumps": 0, "idd_planned_flag": "no"}])
    by_pid = {12: {"pid": 12, "kind": "扩管", "title": "扩 E1", "zone_name": "Mussafah", "gid": 1, "priority": 8.0, "confidence": "中", "when_class": "近期", "status": "建议", "client_hotspot_id": "ADM-1"}}
    calls: list = []

    def scenario_bundle(self, key):
        self.calls.append(("bundle", key))
        return {"key": key, "label": f"{key} 年", "label_en": f"{key}-yr", "p24_mm": 51.1, "scenario": {"n_edges_over": 100, "surcharge_share": 0.2, "network_flood": {"total_m3": 2.2e6}},
                "dispatch": {"decision_time_min": 210, "kpis": {"solver": {"points_total": 1, "points_served": 1, "units_used": 2, "critical_within_target": 0, "critical_total": 1, "first_arrival_median_min": 13}}, "levels": {"points": {"III": 1}}, "triage": {"可派泵": 1}, "cost": {"total_aed": 1200}, "tankers": {"used": 0, "pool": 10, "points": []}, "replay_summary": {"served_saved_pct": 70},
                             "points": {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None, "properties": {"fp_id": "ADM-1", "volume_m3": 1444.0, "max_depth_m": 0.64, "duration_min": 140, "level": "III", "level_priority": "A", "top_kind": "tunnel", "facilities": ["tunnel"], "target_drain_min": 120.0, "priority": 155.9, "dispatchable": True, "triage": "可派泵（需接力排放）", "discharge_to": "SOK", "discharge_dist_m": 707.0, "relay_needed": True, "volume_now_m3": 900.0, "served": True, "client_criticality": "Very High", "land_use": "tunnel", "source": "network_gap"}}]},
                             "plan": [{"wave": 1, "unit_id": "PA-1", "model": "diesel-12in", "rate_m3h": 430.0, "rate_nominal_m3h": 700.0, "relay_stages": 3, "from": "A01", "fp_id": "ADM-1", "arrive_min": 213.0, "pumping_from_min": 293.0, "done_min": 399.0, "within_target": False, "target_drain_min": 120.0},
                                      {"wave": 1, "unit_id": "PA-2", "model": "diesel-8in", "rate_m3h": 215.0, "rate_nominal_m3h": 350.0, "relay_stages": 3, "from": "A01", "fp_id": "ADM-1", "arrive_min": 220.0, "pumping_from_min": 300.0, "done_min": 399.0, "within_target": False, "target_drain_min": 120.0}],
                             "depots": [{"depot_id": "A01", "name": "Civil Defence Shahama", "area": "Al Shahama", "lon": 54.68, "lat": 24.54, "capacity_units": 55}], "fleet_models": [{"model": "diesel-12in", "rate_m3h": 700, "setup_min": 25, "duty_aed_h": 90}, {"model": "diesel-8in", "rate_m3h": 350, "setup_min": 20, "duty_aed_h": 60}],
                             "preposition": {"红": {"cover_min": 20, "points": 1, "covered": 1, "sites": [{"site_id": "CD1", "name": "Staging 1", "lon": 54.6, "lat": 24.5, "units_suggested": 4, "covers": ["ADM-1"]}], "value": {"units_placed": 4, "arrive_median_min": 9, "saved_vs_after_pct": 53.4, "within_target": {"pre": 1, "pre_n": 1}, "cost": {"total_aed": 4000}}}}}}

    def gwm_points(self, key):
        return {"run_id": "r1", "matched_by": "return_period", "scenario": {"name": "10年一遇（GWM）", "name_en": "10-yr (GWM)"}, "stats": {"gwm": {"n": 2, "with_hotspot": 1, "agreement": {"both": 1, "hotspot": 0, "system": 0, "model_only": 1}, "model_only_volume_m3": 5e5}, "hotspots": {"n": 2, "covered": 1, "in_grid": 2, "outside_grid": 0, "covered_share": 0.5}, "system": {"n": 1, "covered": 1}},
                "model": {"name": "GWM"}, "hotspot_hits": [{"hotspot_id": "ADM-1", "fp_id": "FP-G1", "dist_m": 0, "depth_m": 1.2, "criticality": "Very High"}],
                "geojson": {"features": [{"properties": {"fp_id": "FP-G1", "lon": 54.69, "lat": 24.54, "max_depth_m": 1.2, "volume_m3": 5e5, "area_m2": 4e5, "agreement": "both", "hs_ids": ["ADM-1"], "sys_ids": ["ADM-1"]}}]}}

    def hotspot_detail(self, hid, dn, key=None):
        self.last_detail_key = key      # 换管 what-if 要把问的情景传下来
        return {"scenario_key": "10", "scenario_label": "10 年", "curves": {"reach": {"dn_mm": 600}, "no_gap": False, "base_kpi": {"peak_m3": 1444, "t_empty_min": 500, "flood_h": 8.0}, "pump_kpi": {"peak_m3": 900, "t_empty_min": 399, "flood_h": 5.0}, "pumps": [{}, {}],
                                                                       "upsized": [{"dn_mm": 750, "q_full_m3s": 0.8, "shortfall_m3": 300, "kpi": {"peak_m3": 300, "t_empty_min": 250, "flood_h": 2}}, {"dn_mm": 900, "q_full_m3s": 1.2, "shortfall_m3": 0, "kpi": {"peak_m3": 0, "t_empty_min": 0, "flood_h": 0}}]}}

    def alert(self, **kw):
        self.calls.append(("alert", kw))
        return {"level": "橙", "level_en": "Orange", "manual": {"level": "II"}, "headline": "橙级预警", "headline_en": "Orange alert", "scenario": {"key": "50", "label": "50 年", "label_en": "50-yr"}, "actions": [{"role": "控制室", "role_en": "Control room", "zh": "宣布", "en": "Declare"}], "timeline": []}

    def whatif_one(self, pid):
        return {"pid": pid, "interventions": [{}], "kpis_before": {"a": 1}, "kpis_after": {"a": 0.8}, "delta": {"a": -0.2}, "cost_aed": 1e6}

    def replay_point(self, fp, pumps=None, t=None):
        self.calls.append(("replay", fp, pumps))
        return {"pumps_source": "manual" if pumps else "plan", "pumps": [{"label": "x", "arrive_min": 240, "rate_m3h": 700}], "kpi": {"base": {"peak_m3": 1444, "t_empty_min": 500, "flood_h": 8.0}, "pump": {"peak_m3": 900, "t_empty_min": 399, "flood_h": 5.0}, "saved_min": 101, "flood_h_saved": 3.0, "pumped_total_m3": 1000}}

    def trace(self, gid):
        return {"gid": gid, "downstream_gids": [2, 3], "downstream_km": 0.5, "upstream_gids_n": 4, "upstream_km": 0.9, "terminal_kind": "outfall"}


@pytest.fixture
def rt(tmp_path, monkeypatch):
    import swbrain.scenarios as sc
    cat = [{"key": "10", "kind": "rp", "label": "10 年", "label_en": "10-yr", "i15_mmh": 57.7, "p24_mm": 51.1}, {"key": "50", "kind": "rp", "label": "50 年", "label_en": "50-yr", "i15_mmh": 118.2, "p24_mm": 81.8}, {"key": "10cc", "kind": "rp_cc", "label": "10 年 + 20% CC", "label_en": "10-yr + 20% CC", "i15_mmh": 69.2, "p24_mm": 61.3}]
    monkeypatch.setattr(sc, "keys", lambda cfg: [c["key"] for c in cat])
    monkeypatch.setattr(sc, "catalog", lambda cfg: cat)
    monkeypatch.setattr(sc, "get", lambda cfg, k: next(c for c in cat if c["key"] == str(k)))
    monkeypatch.setattr(R, "ROOT", tmp_path)          # 不读真实 out/state
    b = Fake(); b.calls = []
    return R.Runtime(b, Fake.cfg)


def test_schema_check_and_export():
    assert S.check() == []
    d = S.to_dict()
    assert d["core"] == "FloodPoint" and len(d["object_types"]) == len(S.OBJECT_TYPES) and len(d["actions"]) == len(S.ACTIONS) and len(d["rules"]) == len(S.RULES)
    assert all("fn" not in r for r in d["rules"]) and "FloodPoint" in S.mermaid()
    assert S._thr({"黄": 45, "橙": 70, "红": 90}, [1, 2, 3]) == [45.0, 70.0, 90.0] and S._thr([1, 2], [9]) == [1.0, 2.0] and S._thr(None, [9]) == [9]


def test_scenario_key_and_objects(rt):
    assert rt.scenario_key("50 年一遇") == "50" and rt.scenario_key("50-yr") == "50" and rt.scenario_key("10 年 + 20% CC") == "10cc" and rt.scenario_key(None) == "10"
    with pytest.raises(R.ActionError):
        rt.scenario_key("7")
    fp = rt.get("FloodPoint", "adm 1")
    assert fp["fp_id"] == "ADM-1" and fp["zone_name"] == "Mussafah" and fp["matched"] is True
    st = rt.get("FloodState", "ADM-1@50")
    assert st["level"] == "III" and st["units_assigned"] == 2 and st["done_min"] == 399.0 and st["within_target"] is False and st["world_model_confirmed"] is True and st["gwm_core"] == "FP-G1"
    sc = rt.get("Scenario", "50")
    assert sc["alert_level"] == "橙" and sc["n_points"] == 1 and sc["spill_total_m3"] == 2.2e6      # 50 年 p24 81.8 ≥ 70 → 橙
    assert rt.get("Reach", 1)["compliant"] is False and rt.get("Zone", "mussafah")["n_flood_points"] == 1
    assert rt.get("Proposal", 12)["title"] == "扩 E1" and rt.get("PumpUnit", "PA-1")["rate_m3h"] == 700.0 and rt.get("Depot", "CD1")["kind"] == "staging"
    assert rt.get("GwmCore", "r1:FP-G1")["agreement"] == "both"
    # 对象集 / 关系
    rows = rt.search("FloodState", {"level": "III"}, order_by="priority", scenario="10")
    assert [r["fp_id"] for r in rows] == ["ADM-1"] and rows[0]["location"] == "Al Shahama Tunnel"
    assert rt.search("FloodState", {"served": False}, scenario="10") == []
    assert [x["gid"] for x in rt.links("FloodPoint", "ADM-1", "drains_via")] == [1] and rt.links("FloodPoint", "ADM-1", "zone")[0]["zone_name"] == "Mussafah"
    assert len(rt.links("FloodState", "ADM-1@10", "assignments")) == 2 and rt.links("Assignment", "10@PA-1@ADM-1", "depot", "10")[0]["depot_id"] == "A01"
    assert [p["pid"] for p in rt.links("FloodPoint", "ADM-1", "proposals")] == [12] and rt.links("FloodState", "ADM-1@10", "staging_sites")[0]["depot_id"] == "CD1"
    with pytest.raises(R.ActionError):
        rt.links("FloodPoint", "ADM-1", "unit")


def test_rules(rt):
    fp = rt.get("FloodPoint", "ADM-1"); st = rt.get("FloodState", "ADM-1@10")
    rp = {r["rule"]: r for r in rt.rules("FloodPoint", fp, "10")}
    assert rp["coordinate_suspect"]["fired"] and rp["coordinate_suspect"]["value"]["shift_m"] > 6000 and not rp["no_network_but_terminal"]["fired"] and not rp["observation_conflict"]["fired"]
    rs = {r["rule"]: r for r in rt.rules("FloodState", st, "10")}
    assert rs["emergency_level"]["value"] == "III" and rs["emergency_level"]["consistent"] is True
    assert rs["target_drain_time"]["value"] == 120.0 and rs["relay_factor"]["value"]["stages"] == 5 and rs["within_target"]["value"] is False
    assert rs["needs_escalation"]["fired"] and "超过目标时限" in rs["needs_escalation"]["value"]["why"]
    fp2 = rt.get("FloodPoint", "ADM-2")
    assert not any(r["fired"] for r in rt.rules("FloodPoint", fp2, "10") if r["kind"] != "derive")


def test_actions_validation_and_side_effects(rt, tmp_path):
    out = rt.act("inspect_point", {"fp_id": "ADM-99"})
    assert not out["ok"] and "没有积水点" in out["error"]
    assert not rt.act("list_points", {"level": "IV"})["ok"] and not rt.act("issue_alert", {"p24_mm": 900})["ok"] and not rt.act("inspect_point", {"fp_id": "ADM-1", "bogus": 1})["ok"]
    assert not rt.act("record_observation", {"fp_id": "ADM-1"})["ok"]                       # 缺 status
    with pytest.raises(R.ActionError):
        rt.act("no_such_action", {})
    r = rt.act("inspect_point", {"fp_id": "ADM-1", "scenario": "50"})
    assert r["ok"] and r["params"]["scenario"] == "50" and "III 级" in r["summary"] and "level III" in r["summary_en"] and r["ui"] == {"hs": "ADM-1", "rp": "50"}
    r = rt.act("dispatch_plan", {"fp_id": "ADM-1"})
    assert r["ok"] and r["params"]["scenario"] == "10" and "2 台" in r["summary"] and r["result"]["n_assignments"] == 2
    r = rt.act("record_observation", {"fp_id": "ADM-1", "status": "dry"})
    assert r["ok"] and r["result"]["conflict"] and "待核" in r["summary"] and (tmp_path / "out" / "_test_obs.jsonl").exists()
    assert rt.links("FloodPoint", "ADM-1", "observations")[0]["status"] == "dry"
    assert any(x["fired"] for x in rt.rules("FloodPoint", rt.get("FloodPoint", "ADM-1"), "10") if x["rule"] == "observation_conflict")
    log = [json.loads(l) for l in (tmp_path / "out" / "_test_actions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[-1]["action"] == "record_observation" and log[-1]["ok"] and log[-1]["side_effect"].startswith("写")
    r = rt.act("issue_alert", {"p24_mm": 80})
    assert r["ok"] and r["result"]["level"] == "橙" and rt.b.calls[-1][0] == "alert" and rt.b.calls[-1][1]["source"] == "agent"
    assert rt.act("upsize_whatif", {"fp_id": "ADM-1"})["result"]["upsized"][1]["dn_mm"] == 900 and rt.b.last_detail_key in (None, "10")      # 不指定情景 = 激活情景
    assert rt.act("upsize_whatif", {"fp_id": "ADM-1", "scenario": "50 年一遇"})["ok"] and rt.b.last_detail_key == "50"      # 问哪个情景就按哪个情景算（2026-09-22 演练发现一律按激活情景）
    assert "0.50 km" in rt.act("trace_downstream", {"fp_id": "ADM-1"})["summary"] and not rt.act("trace_downstream", {"fp_id": "ADM-2"})["ok"]
    assert rt.act("explain_rule", {"rule": "为什么是三级"})["result"]["rule"]["api"] == "emergency_level"
    assert rt.act("zone_overview", {"zone": "mussafah"})["result"]["n_states"] == 1
    assert rt.act("generate_report", {"scenario": "50", "lang": "en"})["ui"]["url"] == "/api/report?rp=50&lang=en"


def test_rule_planner_intents(rt):
    pl = A.RulePlanner(rt)
    cases = [("ADM-1 在 50 年一遇下是什么情况", [("inspect_point", {"fp_id": "ADM-1", "scenario": "50"})]),
             ("ADM-1 派几台泵、几点能排干", [("dispatch_plan", {"fp_id": "ADM-1"})]),
             ("50 年一遇下最严重的 5 个 III 级点", [("list_points", {"scenario": "50", "level": "III", "limit": 5})]),
             ("Which Very High points are still unserved at 50 years?", [("list_points", {"scenario": "50", "criticality": "Very High", "served": False})]),
             ("为什么 ADM-1 是 III 级", [("inspect_point", {"fp_id": "ADM-1"}), ("explain_rule", {"rule": "emergency_level"})]),
             ("预报明天 80 mm，要发什么级别的预警", [("rain_to_scenario", {"p24_mm": 80.0})]),          # 问级别不发：不带副作用的动作
             ("预报明天 80 mm，发预警", [("issue_alert", {"p24_mm": 80.0})]),
             ("预报三天后 60 mm，相当于几年一遇？该用哪个情景", [("rain_to_scenario", {"p24_mm": 60.0, "lead_days": 3.0})]),
             ("Forecast 75 mm in 24 h in 2 days: which scenario should we prepare for?", [("rain_to_scenario", {"p24_mm": 75.0, "lead_days": 2.0})]),
             ("ADM-1 换大两级管径能不能不积水", [("upsize_whatif", {"fp_id": "ADM-1", "dn_steps": 2})]),
             ("红色预警下预置哪几个驻地", [("preposition_plan", {"alert_level": "红"})]),
             ("世界模型印证了多少客户热点", [("compare_world_model", {})]),
             ("现场看 ADM-1 水深 0.4 m 还在积水，记一下", [("record_observation", {"fp_id": "ADM-1", "status": "flooding", "depth_m": 0.4})]),
             ("ADM-1 现场无积水了，记一下", [("record_observation", {"fp_id": "ADM-1", "status": "dry"})]),
             ("ADM-1 用两台 12 寸泵多久能排干", [("replay_point", {"fp_id": "ADM-1", "pumps": "12in:2@240"})]),
             ("建议 12 做了之后 KPI 变多少", [("proposal_whatif", {"pid": 12})]),
             ("Mussafah 片区怎么样", [("zone_overview", {"zone": "Mussafah"})]),
             ("ADM-1 的水最后排到哪里", [("trace_downstream", {"fp_id": "ADM-1"})]),
             ("出一份 50 年一遇的报告", [("generate_report", {"scenario": "50"})]),
             ("切到 50 年一遇", [("set_scenario", {"scenario": "50"})]),
             ("你好", [])]
    for q, want in cases:
        got = pl.plan(q)
        assert [g[0] for g in got] == [w[0] for w in want], (q, got)
        for (ga, gp), (wa, wp) in zip(got, want):
            for k, v in wp.items():
                assert gp.get(k) == v, (q, k, gp)


def test_agent_chat_rules_and_llm_tools(rt, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = {**Fake.cfg, "agent": {**Fake.cfg["agent"], "dsh": {"enabled": False}}}       # 本测试只看规则规划器；DeepSeek Harness 在 test_dsh_bridge 里单测
    ag = A.Agent(rt, cfg)
    assert ag.planner_name() == "rules" and ag.dsh is None and ag.dsh_info()["enabled"] is False
    r = ag.chat("ADM-1 派几台泵、几点能排干", scenario="50")
    assert r["planner"] == "rules" and r["steps"][0]["action"] == "dispatch_plan" and r["steps"][0]["params"]["scenario"] == "50" and "2 台" in r["reply"] and r["ui"]["tab"] == "line3"
    assert "result" in r["steps"][0] and "assignments" in r["steps"][0]["result"]
    r2 = ag.chat("hello there")
    assert r2["lang"] == "en" and r2["steps"] == [] and "drainage agent" in r2["reply"]
    tools = A.LlmPlanner(Fake.cfg).tools()
    names = [t["name"] for t in tools]
    own = [a.api for a in S.ACTIONS if getattr(a, "owner", "dispatch") == "dispatch"]      # 多智能体：大模型规划器只拿调度研判自己的动作
    assert names[: len(own)] == own and {"get_object", "search_objects", "follow_link"} <= set(names)
    ia = next(t for t in tools if t["name"] == "issue_alert")
    assert ia["input_schema"]["required"] == ["p24_mm"] and ia["input_schema"]["properties"]["p24_mm"]["maximum"] == 400
    sp = A.LlmPlanner(Fake.cfg).system_prompt(rt, "zh", "50")
    assert "FloodPoint" in sp and "10cc" in sp and "只涨不退" in sp
    # 大模型工具执行走同一套 runtime
    out = A.LlmPlanner._exec(rt, "search_objects", {"type": "FloodState", "filters": {"level": "III"}, "scenario": "10"}, "zh")
    assert out["ok"] and out["result"]["rows"][0]["fp_id"] == "ADM-1"
    assert not A.LlmPlanner._exec(rt, "nope", {}, "zh")["ok"]


def test_rain_to_scenario_and_area_zones(rt):
    """预报雨量转情景：年一遇插值、最贴近情景（同样近优先 10 年+CC）、三判据预警、预置窗口、缺省切情景；区域片区：没管网片区的点按客户 area 归并。"""
    r = rt.act("rain_to_scenario", {"p24_mm": 60, "lead_days": 3})
    assert r["ok"], r.get("error"); res = r["result"]
    assert 14 < res["return_period"]["years_est"] < 17 and res["return_period"]["bracket"] == [10, 25]
    assert res["nearest"]["key"] == "10cc" and res["applied"] and r["ui"] == {"rp": "10cc"} and rt.default_scenario == "10cc"
    assert res["alert"]["rain_only"] == "黄" and res["alert"]["with_scenario_rules"] == "黄" and res["preposition_window"] == "open" and res["next_actions"][0]["api"] == "preposition_plan"
    assert "年一遇" in r["summary"] and "10 年 + 20% CC" in r["summary"] and "黄" in r["summary"] and "preposition_plan" in r["summary"] and "return period" not in r["summary"]
    assert "year event" in r["summary_en"] and "Yellow" not in r["summary_en"]
    r2 = rt.act("rain_to_scenario", {"p24_mm": 120, "apply": False})
    assert r2["ok"] and r2["result"]["return_period"]["bracket"] == [">", 100] and not r2["result"]["applied"] and r2["ui"] == {} and "超过 100 年一遇" in r2["summary"]
    assert not rt.act("rain_to_scenario", {"p24_mm": 0.5})["ok"]                                    # 低于下限
    # 区域片区：ADM-2 没匹配到管段，客户清单 area=X → 片区 X（kind=area）；Mussafah 仍是管网片区
    z = rt.get("Zone", "X")
    assert z and z["kind"] == "area" and z["n_flood_points"] == 1 and z["n_with_reach"] == 0 and z["district_name"] == "Abu Dhabi"
    assert rt.get("Zone", "Mussafah")["kind"] == "network" and rt.zone_names() == ["Mussafah", "X"] and len(rt.search("Zone", limit=50)) == 2
    assert rt.get("FloodPoint", "ADM-2")["zone_name"] == "X" and rt.get("FloodPoint", "ADM-1")["zone_name"] == "Mussafah"
    zo = rt.act("zone_overview", {"zone": "X", "scenario": "10"})
    assert zo["ok"] and "区域归并" in zo["summary"] and "area field" in zo["summary_en"] and zo["ui"].get("fit") == "highlight" and "zone" not in zo["ui"] and zo["ui"]["highlight"][0]["id"] == "ADM-2"
    assert "管长" in rt.act("zone_overview", {"zone": "Mussafah", "scenario": "10"})["summary"]
