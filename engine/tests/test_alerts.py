"""预警规则层：预报雨量选情景、三条规则取最高等级、提示单结构（不写文件）。"""
from swbrain import alerts

CFG = {"idf": {"return_periods": {"5": {"i15_mmh": 51.0, "p24_mm": 44.9}, "10": {"i15_mmh": 57.7, "p24_mm": 51.1}, "100": {"i15_mmh": 138.5, "p24_mm": 96.0}}},
       "scenarios": {"cc": {"10cc": {"base_rp": 10, "factor": 1.2, "label": "cc", "label_en": "cc"}},
                     "events": {"2024-04": {"base_rp": 10, "p24_ref_mm": 51.12, "p24_default_mm": 94.0, "label": "e", "label_en": "e"}}},
       "dispatch": {"decision_time_min": 210, "manual_levels": {"trt_min": 120, "mobilization_min": 30,
                    "红": {"level": "III", "priority": "A", "zh": "III 级", "en": "Level III"}, "黄": {"level": "I", "priority": "C", "zh": "I 级", "en": "Level I"}}},
       "alerts": {"rain_mm": {"红": 100.0}}}


def test_pick_scenario_nearest_prefers_return_period():
    assert alerts.pick_scenario(CFG, 44.0)["key"] == "5"
    assert alerts.pick_scenario(CFG, 62.0)["key"] == "10cc"
    assert alerts.pick_scenario(CFG, 95.0)["key"] == "100"          # 96.0 比 94.0 近
    assert alerts.pick_scenario(CFG, 93.0)["key"] == "2024-04"


def test_level_rules_take_max_and_honour_config_override():
    lv, trig = alerts.level_from_rules(CFG, 30.0, 2, 5e5)
    assert lv == "蓝" and len(trig) == 3
    lv, _ = alerts.level_from_rules(CFG, 95.0, 0, 0)                 # 配置把红的雨量阈值改到 100 → 95 只是橙
    assert lv == "橙"
    lv, _ = alerts.level_from_rules(CFG, 30.0, 160, 0)               # III 级点 160 个 → 红
    assert lv == "红"
    lv, _ = alerts.level_from_rules(CFG, 30.0, None, 3.5e6)          # 溢流 350 万 → 橙
    assert lv == "橙"
    lv, _ = alerts.level_from_rules(CFG, 50.0, 66, 2.25e6)           # 10 年一遇的实际值 → 黄
    assert lv == "黄"


def test_advisory_structure():
    bundle = {"key": "10", "label": "10 年", "label_en": "10-yr", "p24_mm": 51.1,
              "scenario": {"network_flood": {"total_m3": 2.2e6, "spilling_len_share": 0.04, "by_zone": [{"zone_name": "Z1", "V_m3": 3e5}]}},
              "dispatch": {"levels": {"points": {"III": 67, "II": 200}}, "decision_time_min": 210, "kpis": {"solver": {"points_total": 300}},
                           "points": {"type": "FeatureCollection", "features": [{"properties": {"fp_id": "ADM-1", "land_use": "tunnel", "level": "III", "priority": 9, "location": "Tunnel A"}},
                                                                                 {"properties": {"fp_id": "ADM-2", "land_use": "road", "level": "III", "priority": 5}}]},
                           "preposition": {"红": {"sites": [{"name": "S1", "units_suggested": 12, "covers": ["ADM-1", "ADM-2"]}], "value": {"saved_vs_after_pct": 54.1}}}}}
    a = alerts.advisory(CFG, bundle, {"p24_mm": 100.0, "lead_h": 24, "issued_at": "2026-09-20 21:00", "source": "manual"}, write=False)
    assert a["level"] == "红" and a["manual"]["level"] == "III" and a["impact"]["tunnels"] == 1 and a["impact"]["preposition"]["pumps"] == 12
    roles = [x["role"] for x in a["actions"]]
    assert roles[0] == "控制室值班" and any("预置" in x["zh"] for x in a["actions"]) and any("下穿" in x["zh"] for x in a["actions"]) and any("巡查" in x["zh"] for x in a["actions"])
    assert a["timeline"][0]["t"] == "T−24h" and "红级预警" in a["headline"] and a["headline_en"].startswith("Red alert")
