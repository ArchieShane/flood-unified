"""一键报告：合成对象能排出自包含 HTML，中英文都有，缺字段不炸。"""
from swbrain import report


def test_render_html_minimal_and_full():
    S = {"生成时间": "2026-09-20", "管段数": 261547, "在用管长km": 8523.0, "能到出水口或泵站的管长份额": 0.39, "法定标准": {"达标管长份额": 0.876, "不达标km": 1053.3, "依据": "WA-726-1", "按类别": [{"class_zh": "下穿", "class_en": "Underpass", "rp": 50, "km": 47.5, "compliant_share": 0.665, "over_km": 15.9}]},
         "承诺": {"承诺": "12 年内…", "就绪条件": "0/6", "说明": "占位", "commitment_en": "Within 12 years…", "readiness_en": "0/6", "note_en": "placeholder"}, "假设": {"径流系数": {"中位": 0.47}}, "建议数": 274, "法定标准达标的分期": {"n_projects": 1937, "total_cost_aed": 1.51e10, "years": 12}}
    bundle = {"key": "10", "label": "10 年", "label_en": "10-yr", "scenario": {"i_mmh": 57.7, "surcharge_share": 0.29, "surcharge_km": 2471, "n_edges_over": 60000, "network_flood": {"total_m3": 2252352, "edges_spilling": 6163, "spilling_len_share": 0.0417, "backwater": {"edges": 3100}, "by_zone": [{"zone_name": "MBZ", "V_m3": 262694, "n_spill": 700, "n_backwater": 300, "len_km": 900}], "method": "路由", "method_en": "routing"}, "method": "推理公式", "method_en": "rational"},
              "dispatch": {"decision_time_min": 210, "kpis": {"solver": {"points_served": 26, "points_total": 346, "critical_within_target": 1, "critical_total": 35, "units_used": 37, "first_arrival_median_min": 9}},
                           "replay_summary": {"served_saved_pct": 72.7, "served_flood_h_base": 449, "served_flood_h_pump": 123, "all_points": 346, "all_flood_h_base": 8207, "all_flood_h_pump": 7881, "system_empty_base_min": 2226, "system_empty_pump_min": 2226, "never_empties_base": 167, "never_empties_pump": 161},
                           "levels": {"points": {"III": 67, "II": 229, "I": 52}}, "triage": {"可派泵": 6}, "triage_en": {"可派泵": "dispatchable"},
                           "plan": [{"wave": 1, "unit_id": "PA-1", "model": "diesel-12in", "fp_id": "ADM-3", "top_kind": "tunnel", "arrive_min": 213, "done_min": 351, "within_target": True, "priority": 9}],
                           "preposition": {"红": {"sites": [{"name": "S"}] * 12, "value": {"units_placed": 329, "arrive_median_min": 59, "flood_h": {"pre": 3672, "after": 7872, "base": 8197}, "saved_vs_after_pct": 53.4, "within_target": {"pre": 73, "pre_n": 130}, "level_iii_served": {"pre": 44}, "cost": {"total_aed": 436129}, "served_flood_h": {"saved_pct": 83.4, "n": 130}}}},
                           "plans": {"plan_a_onsite": {"label": "客户预案 A", "label_en": "Client Plan A", "pumps_total": 412, "value": {"units_placed": 412, "arrive_median_min": 50, "saved_vs_after_pct": 22.0, "within_target": {"pre": 178, "pre_n": 238}, "served_flood_h": {"saved_pct": 61.9, "n": 238}, "cost": {"total_aed": 374205}}},
                                     "broken": {"label": "坏的", "value": None, "error": "x"}},
                           "cost": {"total_aed": 32044, "pump_hours": 140}}}
    adv = {"level": "红", "level_en": "Red", "manual": {"level": "III"}, "headline": "红级预警", "headline_en": "Red alert", "actions": [{"role": "控制室", "role_en": "Control room", "contact": "x", "zh": "宣布", "en": "Declare"}], "timeline": [{"t": "T−24h", "zh": "发预警", "en": "Issue"}], "note": "演示", "note_en": "demo"}
    cmp_ = {"Musaffah_00": {"hotspots_inside": {"n": 3}, "our_flood_inside_m3": {"10cc": 11357, "2024-04": 23014}, "parsons_flood_m3": {"10cc": 832, "2024-04": 23902}, "our_network_inside": {"length_km": 31.3}, "dmt_conduit_km": 27.3}}
    h = report.render_html({}, S, bundle, adv, cmp_, lang="zh")
    assert h.startswith("<!doctype html>") and "全网溢流量" in h and "225.2 万 m³" in h and "客户预案 A" in h and "Musaffah_00" in h and "红级预警" in h and "<script" not in h
    e = report.render_html({}, S, bundle, adv, cmp_, lang="en")
    assert "Network-wide spill" in e and "2.25 M m³" in e and "Client Plan A" in e and "Red alert" in e
    # 缺字段
    h2 = report.render_html({}, {}, {"key": "x", "label": "x", "label_en": "x"}, None, None, lang="zh")
    assert "<!doctype html>" in h2 and "—" in h2
