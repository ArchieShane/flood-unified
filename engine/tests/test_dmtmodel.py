"""DMT 模型导出解析：雨名归类、累计雨量序列统计（单位 in→mm 的坑）、inp 扫描（合成小文件）。"""
from pathlib import Path

import pytest
from swbrain import dmtmodel as dm


def test_classify_rain_names():
    assert dm.classify_rain_name("5yrand_10_yr-New_AAW-2983-2024_April_critical-2024yr_TS")[0] == "2024-04"
    assert dm.classify_rain_name("5yrand_10_yr-New_AAW-25764-2024_(24Hr)_Updated-75yr_TS")[0] == "2024-04"
    assert dm.classify_rain_name("5yrand_10_yr-New_AAW-25764-2026_(24Hr)_Updated-50yr_TS")[0] == "2026-03"
    assert dm.classify_rain_name("5yrand_10_yr-New_AAW-2983-2026-2026yr_TS")[0] == "2026-03"
    assert dm.classify_rain_name("5yrand_10_yr-New_AAW-38721-10_years_SCS_with_CC-10yr_TS")[0] == "10cc"
    assert dm.classify_rain_name("5yrand_10_yr-New_AAW-25764-10_Year_(24Hr)-10yr_TS")[0] == "10"
    assert dm.classify_rain_name("5yrand_10_yr-New_AAW-2983-10yr-10yr_TS")[0] == "10"
    assert dm.classify_rain_name("5yrand_10_yr-New_AAW-2983-5_Yr-5yr_TS")[0] == "5"
    assert dm.classify_rain_name("5yrand_10_yr-New_AAW-25764-20mm-1yr_TS")[0] == "trial20"
    assert dm.classify_rain_name("5yrand_10_yr-New_AAW-2983-2024_1_hour_-2024yr_TS")[0] is None      # 1 h 短历时不进 24 h 情景
    assert dm.classify_rain_name("5yrand_10_yr-New_AAW-25764-2026_(2Hr)_Updated-50yr_TS")[0] is None
    assert dm.classify_rain_name("whatever-2026-2026yr_TS", duration_h=1.0)[0] is None and dm.classify_rain_name("whatever-2026-2026yr_TS", duration_h=24.0)[0] == "2026-03"


def test_rain_series_stats_units_and_peaks():
    # 累计序列（雨量计标 IN，数值就是 mm）：24 h 51.12 mm，前 15 分钟下 10 mm
    pairs = [("00:00", 0.0), ("00:15", 10.0), ("01:00", 20.0), ("24:00", 51.12)]
    s = dm.rain_series_stats(pairs)
    assert s["depth_mm"] == 51.12 and s["duration_h"] == 24.0 and s["max15_mm"] == 10.0 and s["i15_mmh"] == 40.0 and s["max60_mm"] == 20.0


def test_parse_inp_small(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "OUT", tmp_path)
    inp = tmp_path / "m.inp"
    inp.write_text("""[OPTIONS]
FLOW_UNITS LPS
FLOW_ROUTING DYNWAVE
[JUNCTIONS]
J1 1.0 2.0 0 0 100
[OUTFALLS]
OF1 -0.5 FIXED 0.8 YES
[STORAGE]
PO-1 4 2 0 TABULAR C1
[CURVES]
C1 Storage 0 1000
C1 2 3000
[INFLOWS]
J1 FLOW J1_Inflow_Ts FLOW 1.0 1.0 0
[TIMESERIES]
5yrand-10_Year_(24Hr)-10yr_TS 00:00 0
5yrand-10_Year_(24Hr)-10yr_TS 12:00 20.0
5yrand-10_Year_(24Hr)-10yr_TS 24:00 51.12
J1_Inflow_Ts 0 0
J1_Inflow_Ts 5 1000
J1_Inflow_Ts 10 0
""", encoding="utf-8")
    r = dm.parse_inp(inp)
    assert r["counts"]["JUNCTIONS"] == 1 and r["outfalls"][0][0] == "OF1" and r["storage"][0][0] == "PO-1"
    assert r["storage_volumes"][0]["label"] == "PO-1" and r["storage_volumes"][0]["volume_m3"] == 4000 and r["storage_volumes"][0]["is_pond"]   # 梯形：(1000+3000)/2 × 2
    assert r["rain"][0]["scenario_key"] == "10" and abs(r["rain"][0]["depth_mm"] - 51.12) < 0.01
    # 入流：3 步覆盖 48 h → 步长 24 h；1000 L/s × 86400 s / 1000 = 86,400 m³
    assert r["inflow"]["nodes"] == 1 and r["inflow"]["total_m3"] == 86400 and r["inflow"]["peak_sum_m3s"] == 1.0
    r2 = dm.parse_inp(inp)                                     # 第二次走缓存
    assert r2["parsed_at"] == r["parsed_at"]

# ---- 临时塘的库容取哪一个（2026-09-25）----
# 图层属性 max_stor_m3 只有一部分对：Shaliela 10 个塘里 PO-2 / PO-3 与 Parsons 报告表 11
# 分毫不差，其余 8 个偏小 3–20 倍；RB3 七个塘该属性全是 null（图层标签 P1..P7，SWMM 是
# PO-1..PO-7，对不上）。SWMM 蓄水曲线体积与报告表逐个吻合，所以拿它当准。

def test_pond_key_normalises_the_three_label_styles():
    from swbrain.dmtmodel import _pond_key
    assert _pond_key("P1") == _pond_key("PO-1") == _pond_key("PO1") == "PO1"
    assert _pond_key("STO-PO3") == _pond_key("PO-3") == "PO3"
    assert _pond_key("PO-11") == "PO11" and _pond_key("PO-1") != _pond_key("PO-11")


def test_pond_storage_prefers_swmm_curve_over_layer_attribute():
    """图层属性偏小 10 倍时，要取 SWMM 曲线的值，并标明来源。"""
    from swbrain.dmtmodel import ponds_as_sites
    S = {"M": {"area": "测试区", "ponds": {
        "items": [{"label": "PO-4", "max_stor_m3": 3489.5, "area_m2": 17715.0, "lon": 54.78, "lat": 24.67}],
        "swmm_ponds": {"items": [{"label": "PO-4", "depth_m": 2.0, "area_m2": 17715.0,
                                  "volume_m3": 35431.0, "is_pond": True}]}}}}
    r = ponds_as_sites(S)[0]
    assert r["max_stor_m3"] == 35431.0, f"又取了图层属性：{r['max_stor_m3']}"
    assert r["stor_source"] == "swmm_curve"
    assert r["layer_max_stor_m3"] == 3489.5, "原值要留着，方便回头查"


def test_pond_storage_joins_across_differing_labels():
    """图层写 P1、SWMM 写 PO-1，指的是同一个塘——RB3 七个塘就是这么丢掉库容的。"""
    from swbrain.dmtmodel import ponds_as_sites
    S = {"RB3": {"area": "RB3", "ponds": {
        "items": [{"label": "P1", "max_stor_m3": None, "area_m2": 35386.0, "lon": 54.54, "lat": 24.38}],
        "swmm_ponds": {"items": [{"label": "PO-1", "depth_m": 2.0, "area_m2": 35386.0,
                                  "volume_m3": 70771.0, "is_pond": True}]}}}}
    r = ponds_as_sites(S)[0]
    assert r["max_stor_m3"] == 70771.0, f"标签没对上，库容还是 {r['max_stor_m3']}"


def test_pond_storage_falls_back_to_layer_when_no_swmm():
    """没有 SWMM 模型时（如只有图层的汇水区），图层属性照用，但来源要标出来。"""
    from swbrain.dmtmodel import ponds_as_sites
    S = {"X": {"area": "X", "ponds": {
        "items": [{"label": "PO-9", "max_stor_m3": 1234.0, "area_m2": 900.0, "lon": 54.5, "lat": 24.4}]}}}
    r = ponds_as_sites(S)[0]
    assert r["max_stor_m3"] == 1234.0 and r["stor_source"] == "layer_attr"


def test_real_pond_sites_match_the_parsons_report():
    """盘上真实的 ponds_sites.json 要和 Parsons 报告表 11 对得上（Shaliela 十个塘）。"""
    import json
    f = Path(__file__).resolve().parents[1] / "out" / "dmt_model" / "ponds_sites.json"
    if not f.exists():
        pytest.skip("没有 out/dmt_model/ponds_sites.json")
    rep = {"PO-2": 95073.8, "PO-3": 15936.6, "PO-4": 35430.8, "PO-5": 40609.1, "PO-6": 35281.9,
           "PO-7": 34011.1, "PO-8": 44110.4, "PO-9": 44291.3, "PO-10": 43182.3, "PO-11": 16286.7}
    got = {r["site_id"].rsplit("-", 2)[-2] + "-" + r["site_id"].rsplit("-", 1)[-1]: r["max_stor_m3"]
           for r in json.loads(f.read_text(encoding="utf-8")) if "Shaliela" in r["site_id"]}
    assert len(got) == 10, f"Shaliela 应有 10 个塘，实际 {len(got)}"
    off = {k: (got.get(k), v) for k, v in rep.items() if got.get(k) is None or abs(got[k] - v) > 2.0}
    assert not off, f"和报告表 11 对不上：{off}"
