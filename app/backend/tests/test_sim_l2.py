"""全市任务第二道防线信息（sim_l2.py）：只有 61 mm / 2 h 有报告数，别的雨不外推。"""

import pytest

from stormwater import sim_l2


def test_only_61mm_has_report_numbers():
    assert sim_l2.for_run({"depth_mm": 61, "duration_h": 2})["available"] is True
    for d in (33, 96):
        body = sim_l2.for_run({"depth_mm": d, "duration_h": 2})
        assert body["available"] is False
        assert "compare" not in body


def test_report_numbers_match_source():
    c = {x["key"]: x for x in sim_l2.CITYWIDE[(61.0, 2.0)]["compare"]}
    assert c["flood_volume_m3"]["l1"] - c["flood_volume_m3"]["l2"] == 141204
    assert c["storage_m3"]["l2"] - c["storage_m3"]["l1"] == 2454445


def test_applies_only_to_citywide_line1():
    assert sim_l2.applies({"defence_line_key": "line1"}, "temp/abu-dhabi-citywide-coupled-61mm")
    assert not sim_l2.applies({"defence_line_key": "line1"}, "processed/sim_deliveries/Mussafah_00_61mm2h")
    assert not sim_l2.applies({"defence_line_key": "line1"}, None)


def test_built_ponds_from_ledger():
    ponds = sim_l2.ponds()
    if not ponds:
        pytest.skip("缺 storage_asset_dims.csv")
    assert len(ponds) == 23
    assert all(24 < p["lat"] < 25 and 54 < p["lon"] < 56 for p in ponds)


def test_estimate_ratio_from_flood_volume_borrowed_for_other_storms():
    e61 = sim_l2.estimate(61.0, 2.0)
    assert e61["reduction_ratio"] == 0.026
    assert e61["borrowed"] is False
    e33 = sim_l2.estimate(33.0, 2.0)
    assert e33["reduction_ratio"] == e61["reduction_ratio"] and e33["borrowed"] is True


def test_citywide_l2_state_ponds_from_delivery():
    """#561 的 L2 实算态：22 座真接进模型的塘，坐标取耦合 .inp，库容合计 = 交付塘蓄水表 2,506,742 m³。"""
    d = sim_l2.BACKEND_DIR / "data" / "processed" / "sim_deliveries" / "全市_61mm2h" / "L2"
    if not (d / "pond_storage.csv").is_file():
        pytest.skip("缺全市 L2 交付")
    rows = sim_l2.state_ponds(d)
    assert len(rows) == 22
    assert all(r["district"] for r in rows)          # 全部对上台账
    assert round(sum(r["model_volume_m3"] for r in rows)) == 2_506_742
    assert 20 not in {r["id"] for r in rows}          # 台账 #20 Al Wathbah 没进模型
    assert sim_l2.state_ponds(d.parent / "L1") == []


def test_citywide_l2_pond_inlet_pipes():
    """接入管：32 根接 17 座塘（报告说 5 座没有管道进水），峰值流量取报告 Link Flow Summary。"""
    d = sim_l2.BACKEND_DIR / "data" / "processed" / "sim_deliveries" / "全市_61mm2h" / "L2"
    if not (d / "coupled_scenario.inp").is_file():
        pytest.skip("缺全市 L2 交付")
    r = sim_l2.state_pond_pipes(d)
    assert len(r["inlets"]) == 32
    assert len({x["pond"] for x in r["inlets"]}) == 17
    x = next(x for x in r["inlets"] if x["id"] == "PI_SW_MAR_RD110_PND_37_265314_2689599")
    assert (x["diameter_m"], x["max_flow_m3s"], x["max_flow_min"]) == (1.8, 10.719, 89)
    assert r["context"]


def test_mussafah_planned_storage_units():
    """Mussafah L2+planned：Parsons 新增 STO-PO3 / W-4 / W-6（父态 L2 只有 RD_34 / CH2），W-4 → 泵 PMP5 → 1235 → STO-PO3。"""
    b = sim_l2.BACKEND_DIR / "data" / "processed" / "sim_deliveries" / "Mussafah_00_61mm2h"
    if not (b / "L2_planned" / "coupled_scenario.inp").is_file():
        pytest.skip("缺 Mussafah 交付")
    rows = {r["name"]: r for r in sim_l2.state_storage(b / "L2_planned", b / "L2")}
    assert {k for k, r in rows.items() if r["is_new"]} == {"STO-PO3", "W-4", "W-6"}
    assert {k for k, r in rows.items() if not r["is_new"]} == {"RD_34", "CH2"}
    assert rows["STO-PO3"]["model_volume_m3"] == 1200.0 and rows["STO-PO3"]["max_fill_pct"] == 67.8
    links = {x["id"]: x for x in sim_l2.state_pond_pipes(b / "L2_planned", b / "L2")["inlets"]}
    assert links["PMP5"]["kind"] == "pump" and links["PMP5"]["is_new"]
    assert links["1235"]["pond"] == "STO-PO3" and links["1235"]["max_flow_m3s"] == 0.1114   # LPS → m³/s
