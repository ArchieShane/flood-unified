"""效果分析页（sim_perf）：共同演算窗、逐点分类与调蓄容量。已登记的 Mussafah 五态不在本机时 skip。"""

from pathlib import Path

import pytest

from stormwater import sim_perf

ROOT = Path(__file__).resolve().parents[1] / "data" / "processed" / "sim_deliveries" / "Mussafah_00_61mm2h"
pytestmark = pytest.mark.skipif(not (ROOT / "states.json").is_file(), reason="Mussafah_00 五态成果不在本机")


@pytest.fixture(scope="module")
def l2b():
    return sim_perf.load(ROOT)


def test_default_pair_is_l1_vs_last_state(l2b):
    assert (l2b["base"]["key"], l2b["vs"]["key"], l2b["h"]) == ("L1", "L2+suggested", 2.0)


def test_peaks_only_inside_common_window(l2b):
    # L1 演算 10 h、L2+suggested 5 h：峰值一律截在 5 h 内
    assert l2b["common_end_minutes"] == 300.0
    assert l2b["window"]["base"]["peak_surface_volume_minutes"] <= 300.0
    assert l2b["window"]["vs"]["max_depth_m"] < l2b["window"]["base"]["max_depth_m"]


def test_same_time_numbers_match_state_effect(l2b):
    # 与 sim_effect 的 2 h 切片同一口径（L2+suggested：少 6,283 m³、9 → 4）
    a = l2b["at_h"]
    assert round(a["volume_less_m3"]) == 6283
    assert (a["active_base"], a["active_vs"]) == (9, 4)


def test_categories_partition_base_points(l2b):
    c = l2b["category_counts"]
    assert sum(c.values()) == len(l2b["points"])
    assert c["cleared"] == l2b["at_h"]["cleared"]
    assert c["both"] + c["reduced"] + c["cleared"] == l2b["at_h"]["active_base"]


def test_storage_capacity_from_curves(l2b):
    n = l2b["network"]
    # 成对口径相对第一态：L2+suggested 比 L1 多 Parsons 的塘井 + NP1–NP6
    assert n["new_storage_units"] == ["NP1", "NP2", "NP3", "NP4", "NP5", "NP6", "STO-PO3", "W-4", "W-6"]
    assert n["vs"]["storage_capacity_m3"] - n["base"]["storage_capacity_m3"] > 16271
    assert n["comparable"] is True


def test_unknown_vs_rejected():
    with pytest.raises(sim_perf.PerfError):
        sim_perf.compute(ROOT, "L9", None)


def test_columns_cover_all_states_in_one_window(l2b):
    cols = l2b["columns"]
    assert [c["column"] for c in cols] == ["line1", "line2_current", "line2_planned", "line2_suggested"]
    # L2 ≡ L1：同一窗里数字逐字段相等
    assert cols[0]["window"] == cols[1]["window"]
    # 与成对口径一致
    assert cols[3]["window"] == l2b["window"]["vs"]
    assert cols[3]["at_h"]["active"] == l2b["at_h"]["active_vs"]


def test_window_matches_delivery_five_state_table(l2b):
    """最大积水面积 / 最大水深 / 峰值地表积水量逐位对上交付的「五态统计值.csv」（一二维，截到 5 h）。
    2026-09-28 之前面积取「任一帧 ≥ 0.01 m 的格并集」（L1 33 ha）、水深取 15 m 格平均（L1 0.99 m），都是错的。"""
    want = {  # 同一时刻最大淹没面积 ha、该时刻 h、最大水深 m、地面上同一时刻最多的水 m³
        "L1": (16.026, 2.167, 1.352, 13105.0), "L2": (16.026, 2.167, 1.352, 13105.0),
        "L2+planned": (15.615, 2.183, 1.352, 12467.0), "L2+suggested": (8.494, 1.8, 0.555, 5719.1),
    }
    for c in l2b["columns"]:
        a, t, dep, vol = want[c["key"]]
        w = c["window"]
        assert w["basis"] == "triangle"
        assert w["inundated_area_m2"] / 1e4 == pytest.approx(a, abs=5e-4), c["key"]
        assert w["inundated_area_minutes"] / 60 == pytest.approx(t, abs=1e-3), c["key"]
        assert w["max_depth_m"] == dep, c["key"]
        assert w["peak_surface_volume_m3"] == pytest.approx(vol, abs=0.5), c["key"]


def test_new_storage_units_sum_to_column_totals(l2b):
    base, col = l2b["columns"][2], l2b["columns"][3]      # L2+suggested 叠在 L2+planned 上
    assert col["parent"] == "L2+planned"
    units = col["new_storage"]
    assert [u["id"] for u in units] == ["NP1", "NP2", "NP3", "NP4", "NP5", "NP6"]
    assert abs(base["network"]["storage_capacity_m3"] + sum(u["capacity_m3"] for u in units)
               - col["network"]["storage_capacity_m3"]) < 1
    # 峰值蓄量不可加：箱涵先接走上游的水，Parsons 的塘在 L2+suggested 里装得比 L2+planned 少，合计比「parent + 新增」小
    assert col["network"]["storage_peak_m3"] < base["network"]["storage_peak_m3"] + sum(u["peak_m3"] for u in units)
    assert units[0]["area_m2"] == 2654.7


def test_suggested_new_storage_is_relative_to_parent(l2b):
    """L2+suggested 叠在 L2+planned 上：新增调蓄只列本系统 6 座箱涵，Parsons 的塘和井不算它的。"""
    col = next(c for c in l2b["columns"] if c["key"] == "L2+suggested")
    assert col["parent"] == "L2+planned"
    assert [u["id"] for u in col["new_storage"]] == ["NP1", "NP2", "NP3", "NP4", "NP5", "NP6"]


def test_system_storage_from_delivery_table(l2b):
    """系统储水能力取交付「五态统计值」的「蓄存能力（管网 + 蓄水节点）」，逐态对上。"""
    want = {"L1": 11125.8, "L2": 11125.8, "L2+planned": 13533.0, "L2+suggested": 32413.5}
    for c in l2b["columns"]:
        n = c["network"]
        assert n["system_storage_basis"] == "delivery", c["key"]
        assert n["system_storage_m3"] == pytest.approx(want[c["key"]], abs=0.1), c["key"]


def test_pipe_volume_fallback_counts_conduits():
    # 没有交付统计值时按 .inp 自算：L1 满管管容约 9,463 m³（不含检查井，所以比交付的 11,126 小）
    v = sim_perf._pipe_volume(ROOT / "L1" / "coupled_scenario.inp")
    assert 9400 < v < 9500


def test_new_storage_carries_its_devices(l2b):
    """每座箱涵的配套：4 根进水管 + 4 个闸孔 + 1 道溢流堰 + 1 台排空泵；能力取 .inp，表现取耦合报告。"""
    col = next(c for c in l2b["columns"] if c["key"] == "L2+suggested")
    for u in col["new_storage"]:
        kinds = [x["kind"] for x in u["devices"]]
        assert kinds == ["conduit"] * 4 + ["orifice"] * 4 + ["weir", "pump"], u["id"]
        assert {r["name"] for r in u["rules"]} == {f"{u['id']}_{k}" for k in
                                                   ("GATE_SHUT", "GATE_OPEN", "EMPTY_ON", "EMPTY_OFF", "EMPTY_HOLD")}
    np1 = col["new_storage"][0]
    pump = np1["devices"][-1]
    assert (pump["id"], pump["to"], pump["q_max_lps"]) == ("PU_NP1", "CB4346", 122.9)
    assert pump["sim"]["utilized_pct"] == 40.0 and pump["sim"]["volume_m3"] == 885.0
    weir = np1["devices"][-2]
    assert (weir["crest_m"], weir["size2_m"], weir["sim"]["max_flow_lps"]) == (2.0, 12.88, 0.0)
    assert np1["sim_max_inflow_lps"] == 959.66
    # NP5 / NP6 蓄到关闸水位 1.90 m，排空泵 5 h 内没开
    np5 = next(u for u in col["new_storage"] if u["id"] == "NP5")
    assert np5["sim_max_depth_m"] == 1.9 and np5["devices"][-1]["sim"]["startups"] == 0
    # 设备只算相对 parent 新增的：Parsons 的泵不挂到箱涵上
    ids = {x["id"] for u in col["new_storage"] for x in u["devices"]}
    assert not ids & {"PMP5", "PMP-6"}
