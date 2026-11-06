"""客户五问：盘点 → 世界模型新增 → 最严重 → 一批点怎样最快退水（含成本代价）→ 成本与代价；规划器意图；ui.highlight 与 console_link。"""
import pandas as pd
import pytest

from swbrain.ontology import runtime as R, agent as A
from tests.test_ontology import Fake, rt  # noqa: F401  （复用假后端与 fixture）


class Fake2(Fake):
    """在 Fake 之上补：世界模型 geojson 带模型独有核；recession() 返回退水方案。"""

    def gwm_points(self, key):
        g = super().gwm_points(key)
        g["stats"]["gwm"]["agreement"] = {"both": 1, "hotspot": 0, "system": 0, "model_only": 2}
        g["geojson"]["features"] += [
            {"properties": {"fp_id": "FP-G2", "lon": 54.52, "lat": 24.42, "max_depth_m": 1.9, "volume_m3": 8e5, "area_m2": 5e5, "agreement": "model_only", "hs_ids": [], "sys_ids": [], "first_wet_min": 600, "peak_min": 1500}},
            {"properties": {"fp_id": "FP-G3", "lon": 54.80, "lat": 24.10, "max_depth_m": 0.6, "volume_m3": 2e5, "area_m2": 2e5, "agreement": "model_only", "hs_ids": [], "sys_ids": []}}]
        return g

    def recession(self, fp, key=None, t=None, with_upsize=True):
        if fp != "ADM-1":
            return {"error": f"没有积水点 {fp}"}
        return {"fp_id": fp, "scenario_key": key, "scenario_label": f"{key} 年", "decision_time_min": 210.0, "target_drain_min": 120.0, "lon": 54.69, "lat": 24.54,
                "point": {"location": "Al Shahama Tunnel", "criticality": "Very High", "level": "III", "triage": "可派泵（需接力排放）"},
                "options": [{"key": "natural", "label_zh": "不派", "label_en": "No action", "n_units": 0, "t_empty_min": None, "within_target": False, "cost_aed": 0.0},
                            {"key": "global", "label_zh": "全局", "label_en": "Global", "n_units": 0, "t_empty_min": None, "within_target": False, "cost_aed": 0.0},
                            {"key": "single", "label_zh": "单点最优", "label_en": "Single", "n_units": 3, "t_empty_min": 720.0, "within_target": False, "cost_aed": 3300.0},
                            {"key": "prepo", "label_zh": "预置最优", "label_en": "Prepo", "n_units": 3, "t_empty_min": 300.0, "within_target": True, "cost_aed": 2100.0, "site": {"name": "Staging 1"}},
                            {"key": "upsize_500", "label_zh": "换管 DN500（工程）", "label_en": "Upsize DN500", "n_units": 0, "t_empty_min": 400.0, "within_target": False, "shortfall_m3": 300.0, "cost_aed": 228000.0}],
                "fastest": "prepo", "need_units_for_target": {"n": 4, "done_min": 330.0}, "wait_until_min": None,
                "single_vs_global": {"units_taken_from_global": ["PA-2"], "displaced_points": [{"fp_id": "ADM-9", "saved_min_lost": 90.0}], "displaced_saved_min_total": 90.0},
                "recommendation": {"zh": "最快退水的手段：预置", "en": "Fastest: prepo"}}


@pytest.fixture
def rt2(tmp_path, monkeypatch, rt):
    b = Fake2(); b.calls = []
    return R.Runtime(b, Fake.cfg)


def test_count_points(rt2):
    r = rt2.act("count_points", {"scenario": "10"})
    assert r["ok"]
    res = r["result"]
    assert res["client_total"] == 2 and res["with_state"] == 1 and res["by_level"] == {"III": 1} and res["served"] == 1 and res["world_model_confirmed"] == 1 and res["no_state"] == 1
    assert res["world_model"]["agreement"]["model_only"] == 2
    assert "845" not in r["summary"] and "客户清单共 2 个积水点" in r["summary"] and "2 个是客户清单和本系统都没有的「新增」" in r["summary"]
    assert r["ui"]["layers"] == ["flood", "hs"] and r["ui"]["fit"] == "all" and r["console_link"].startswith("/?tab=line3&rp=10")


def test_world_model_new_points(rt2):
    r = rt2.act("world_model_new_points", {"scenario": "10", "limit": 5})
    assert r["ok"]
    res = r["result"]
    assert res["new_total"] == 2 and [x["fp_id"] for x in res["rows"]] == ["FP-G2", "FP-G3"] and res["new_volume_m3"] == 1_000_000
    assert res["rows"][0]["nearest_hotspot"]["fp_id"] in ("ADM-1", "ADM-2") and res["rows"][0]["nearest_system_point"]["fp_id"] == "ADM-1"
    hl = r["ui"]["highlight"]
    assert [h["id"] for h in hl] == ["FP-G2", "FP-G3"] and hl[0]["kind"] == "gwm" and r["ui"]["layers"] == ["gwm", "hs"]
    assert "hl=FP-G2,FP-G3" in r["console_link"] and "hlk=gwm" in r["console_link"]
    assert "新增点" in r["summary"] and "FP-G2" in r["summary"] and "水深只涨不退" in r["summary"]
    r2 = rt2.act("world_model_new_points", {"scenario": "10", "order_by": "max_depth_m", "limit": 1})
    assert [x["fp_id"] for x in r2["result"]["rows"]] == ["FP-G2"]


def test_recession_batch_and_cost(rt2):
    r = rt2.act("recession_batch", {"fp_ids": "ADM-1, ADM-2", "scenario": "10"})
    assert r["ok"]
    res = r["result"]; a = res["aggregate"]
    assert res["ids"] == ["ADM-1", "ADM-2"] and a["n"] == 2 and a["n_ok"] == 1 and res["items"][1]["error"].startswith("没有积水点")
    it = res["items"][0]
    assert it["fastest"] == "prepo" and it["fastest_within_target"] and it["fastest_cost_aed"] == 2100.0 and it["need_units"] == 4 and it["upsize"]["dn_mm"] == "500" and it["upsize"]["cost_aed"] == 228000.0
    assert a["meet_target"] == 1 and a["units_total"] == 3 and a["ops_cost_total_aed"] == 2100 and a["single_cost_total_aed"] == 3300 and a["upsize_cost_total_aed"] == 228000
    assert a["units_taken_from_global"] == ["PA-2"] and a["displaced_points"] == ["ADM-9"] and a["staging_sites"] == ["Staging 1"]
    assert [h["id"] for h in r["ui"]["highlight"]] == ["ADM-1"] and r["ui"]["tab"] == "line3" and "hl=ADM-1" in r["console_link"]
    assert "预置" in r["summary"] and "2,100" in r["summary"] and "抢走 1 台泵" in r["summary"] and "228,000" in r["summary"]
    # 不给编号：按 III 级取
    r2 = rt2.act("recession_batch", {"scenario": "10", "limit": 3})
    assert r2["ok"] and r2["result"]["ids"] == ["ADM-1"]
    # 成本与代价：带编号走批量账；不带给情景全局账
    c1 = rt2.act("cost_summary", {"fp_ids": "ADM-1", "scenario": "10"})
    assert c1["ok"] and c1["result"]["mode"] == "points" and c1["result"]["aggregate"]["ops_cost_total_aed"] == 2100
    c2 = rt2.act("cost_summary", {"scenario": "10"})
    assert c2["ok"] and c2["result"]["mode"] == "scenario" and c2["result"]["dispatch_cost"]["total_aed"] == 1200 and c2["result"]["preposition"]["红"]["cost_aed"] == 4000
    assert "1,200" in c2["summary"] and "红级预置" in c2["summary"]


def test_list_points_highlight(rt2):
    r = rt2.act("list_points", {"scenario": "10", "level": "III"})
    assert r["ok"] and [h["id"] for h in r["ui"]["highlight"]] == ["ADM-1"] and r["ui"]["highlight"][0]["kind"] == "points" and "hl=ADM-1&hlk=points" in r["console_link"]


def test_planner_five_questions(rt2):
    pl = A.RulePlanner(rt2)
    cases = [("现在有多少个积水点？", "count_points", {}),
             ("通过世界模型推演后新增了多少积水点？", "world_model_new_points", {}),
             ("其中最严重的积水点有哪些？", "list_points", {"level": "III"}),
             ("世界模型新增的里面最严重的有哪些？", "world_model_new_points", {}),
             ("怎么加快这些验证的积水点的退水速度？", "recession_batch", {}),
             ("怎么加快 ADM-1、ADM-2 的退水速度？", "recession_batch", {"fp_ids": "ADM-1,ADM-2"}),
             ("需要投入的成本和代价是怎么样的？", "cost_summary", {}),
             ("ADM-1 和 ADM-2 的成本和代价", "cost_summary", {"fp_ids": "ADM-1,ADM-2"}),
             ("How many flood points are there now?", "count_points", {}),
             ("How many new flood points does the world model add?", "world_model_new_points", {}),
             ("ADM-1 什么时候能退水，怎样最快", "recession_options", {"fp_id": "ADM-1"})]
    for q, want, params in cases:
        got = pl.plan(q)
        assert got and got[0][0] == want, (q, got)
        for k, v in params.items():
            assert got[0][1].get(k) == v, (q, k, got)
    ag = A.Agent(rt2, {**Fake.cfg, "agent": {**Fake.cfg["agent"], "dsh": {"enabled": False}}})
    r = ag.chat("现在有多少个积水点？", scenario="10")
    assert r["steps"][0]["action"] == "count_points" and r["ui"]["layers"] == ["flood", "hs"] and "客户清单共" in r["reply"]
