"""用假数据（tests/fake_delivery.py）验证 sim_c1 / sim_city 的流程与格式：分块读、336 帧、5 m、只有两态、设施GIS 位置。
数值没有物理意义，只看形状、帧数、分块与整块一致、不同网格并存。"""

import json

import numpy as np
import pytest
from fake_delivery import make_delivery, make_npz

from stormwater import sim_c1, sim_city


@pytest.fixture(autouse=True)
def _no_rasterio(monkeypatch, tmp_path):
    # 几何转换（rasterio）不是这里要测的；输出落临时目录，不碰真实成果
    monkeypatch.setattr(sim_c1, "_cell_polys", lambda g, ids: [[[54.0, 24.0]] * 5 for _ in ids])
    monkeypatch.setattr(sim_c1, "OUT_ROOT", tmp_path / "out")


def _grid(npz, budget=None, cs=15.0):
    if budget is not None:
        monkey = sim_city._row_blocks.__defaults__
        sim_city._row_blocks.__defaults__ = (budget,)
    try:
        return sim_city._extract_grid(npz, cs)
    finally:
        if budget is not None:
            sim_city._row_blocks.__defaults__ = monkey


@pytest.mark.parametrize("compressed", [True, False])
@pytest.mark.parametrize("speed", [True, False])
def test_chunked_read_equals_single_block(tmp_path, compressed, speed):
    npz = tmp_path / "x.npz"
    make_npz(npz, frames=37, speed=speed, compressed=compressed)     # 37 帧：不是块大小的整数倍
    whole = _grid(npz, budget=10 ** 12)
    for budget in (1, 3 * 3200 * 2, 5 * 3200 * 2 + 1):             # 每块 1 / 3 / 5 帧
        g = _grid(npz, budget=budget)
        assert len(g["frames"]) == 37
        for k in ("maxd", "peak_min", "final", "has", "dem"):
            np.testing.assert_array_equal(g[k], whole[k])
        if speed:
            np.testing.assert_array_equal(g["maxsp"], whole["maxsp"])
        else:
            assert g["maxsp"] is None
        assert g["series"] == whole["series"]
        for (ta, da), (tb, db) in zip(g["frames"], whole["frames"]):
            assert ta == tb
            np.testing.assert_array_equal(da, db)


def test_chunked_matches_direct_numpy(tmp_path):
    npz = tmp_path / "x.npz"
    make_npz(npz, frames=9)
    z = np.load(npz)
    area = z["area"].astype(float)
    g = _grid(npz, budget=2 * 3200 * 2)
    F = z["frames"].astype(np.float32)
    assert g["series"]["max_depth_m"] == [round(float(f.max()), 4) for f in F]
    assert g["series"]["volume_m3"] == [round(float((f * area).sum()), 1) for f in F]
    assert g["tri_hmax"] == pytest.approx(float(z["hmax"].max()))


def test_336_frames_at_5min(tmp_path):
    """24 h 雨模拟 28 h、每 5 分钟一帧 = 336 帧。"""
    root = make_delivery(tmp_path, "FAKE_61mm24h", ("L1",), frames=336, end_h=28.0)
    base = sim_c1.extract(root)
    man = json.loads((base / "L1" / "temporal_snapshots" / "manifest.json").read_text(encoding="utf-8"))["snapshots"]
    assert len(man) == 336 and man[-1]["time_minutes"] == pytest.approx(1680.0)
    s = json.loads((base / "L1" / "delivery_summary.json").read_text(encoding="utf-8"))
    assert s["domain"]["simulation_duration_hours"] == pytest.approx(28.0)
    assert s["model_configuration"]["rainfall_duration_minutes"] == 24 * 60
    assert len(list((base / "L1" / "temporal_snapshots").glob("surface_depth_t*.geojson"))) == 336


def test_two_states_only_yas_style(tmp_path):
    root = make_delivery(tmp_path, "YAS_61mm2h", ("L1", "L2"), facilities=True)
    base = sim_c1.extract(root)
    st = json.loads((base / "states.json").read_text(encoding="utf-8"))
    assert [s["key"] for s in st["states"]] == ["L1", "L2"]
    assert st["states"][1]["parent"] == "L1" and st["default"] == "L1"
    assert not (base / "L2_planned").exists()
    assert (base / "L2" / "facilities.json").is_file()            # 设施GIS 在态目录里
    assert not (base / "hotspots.json").exists()                   # 没有 C1统计值.xlsx 就不写，不报错


def test_5m_and_15m_coexist_and_use_res_m(tmp_path):
    a = make_delivery(tmp_path / "a", "C1_61mm2h", ("L1",), res_m=15.0)
    b = make_delivery(tmp_path / "b", "C1_模型版_61mm2h_5m", ("L1",), res_m=5.0)
    base_a, base_b = sim_c1.extract(a), sim_c1.extract(b)
    assert base_a != base_b and base_a.name == "C1_61mm2h" and base_b.name == "C1_模型版_61mm2h_5m"
    sa = json.loads((base_a / "L1" / "delivery_summary.json").read_text(encoding="utf-8"))
    sb = json.loads((base_b / "L1" / "delivery_summary.json").read_text(encoding="utf-8"))
    assert sa["model_configuration"]["model_cell_size_m"] == 15.0
    assert sb["model_configuration"]["model_cell_size_m"] == 5.0
    assert sb["domain"]["cell_size_m"] == 15.0                     # 落图格网不细于 15 m
    assert "模型网格 5 m" in sb["claim_boundary"] and "42.8" not in sb["claim_boundary"]
    assert "ADM-90" in sa["claim_boundary"] and "ADM-90" in sb["claim_boundary"]   # 两个都是 C1
    assert (base_a / "L1" / "delivery_summary.json").is_file()      # 5 m 没覆盖 15 m


def test_non_c1_region_has_no_c1_caveats(tmp_path):
    base = sim_c1.extract(make_delivery(tmp_path, "BAT_61mm2h", ("L1",)))
    s = json.loads((base / "L1" / "delivery_summary.json").read_text(encoding="utf-8"))
    assert "ADM-90" not in s["claim_boundary"]


def test_v3_suggested_dir_extracts(tmp_path):
    root = make_delivery(tmp_path, "C1_61mm2h", ("L1", "L2+planned", "L2+suggested"), frames=6)
    base = sim_c1.extract(root)
    s = json.loads((base / "L2_suggested" / "delivery_summary.json").read_text(encoding="utf-8"))
    assert s["source"]["state_dir"] == "L2+suggested_v3_按塘余量分配"
    assert "按塘余量分配" in s["claim_boundary"]


def test_non_c1_labels_come_from_dir_names(tmp_path):
    base = sim_c1.extract(make_delivery(tmp_path, "YAS_61mm2h", ("L1", "L2"), frames=4))
    st = json.loads((base / "states.json").read_text(encoding="utf-8"))["states"]
    assert st[0]["label"] == "L1 Existing network+existing pump stations" and st[1]["label"] == "L2 Plus OP-02 mobile pumps"   # 取自假目录名，不是 STATES 的固定名
    c1 = sim_c1.extract(make_delivery(tmp_path / "c", "C1_61mm2h", ("L1",), frames=4))
    assert json.loads((c1 / "states.json").read_text(encoding="utf-8"))["states"][0]["label"] == "L1 Existing network + existing pump stations"


# ---- 积水点：每个片区交付都有，不只 C1 ----

from fake_delivery import make_region_stats  # noqa: E402

LEDGER = sim_c1.DATA_ROOT / "processed" / "hotspots_845.csv"


@pytest.fixture
def _no_scope(monkeypatch):
    from stormwater import sim_scope
    monkeypatch.setattr(sim_scope, "compute", lambda root, base, hot: {})


@pytest.mark.skipif(not LEDGER.is_file(), reason="没有 hotspots_845.csv")
def test_region_hotspots_from_stats_csv_and_ledger(tmp_path, _no_scope):
    root = make_delivery(tmp_path, "YAS_61mm2h", ("L1", "L2"), frames=4)
    ids = ["ADM-3", "ADM-85", "ADM-490", "ADM-7"]
    make_region_stats(root, ["L1", "L2"], ids)
    base = sim_c1.extract(root)
    hot = json.loads((base / "hotspots.json").read_text(encoding="utf-8"))
    assert [p["id"] for p in hot["points"]] == ["ADM-3", "ADM-7", "ADM-85", "ADM-490"]
    assert all(24 < p["lat"] < 25 and 54 < p["lon"] < 55 for p in hot["points"])      # 取自台账，不是假值
    assert hot["states"] == ["L1", "L2"]
    p3 = hot["points"][0]
    assert set(p3["stats"]) == {"L1", "L2"} and p3["stats"]["L1"]["near_200m"]["max_depth_m"] == 1.0
    assert p3["stats"]["L2"]["near_200m"]["max_depth_m"] == 0.5
    assert "catchment" not in p3["stats"]["L1"]                                    # 区域交付没有汇水区口径
    assert hot["total"]["L1"]["near_200m"]["area_ge_0_30_ha"] == 15.0                 # 「4 个热点」并集行
    assert hot["zones"]["L1"]["domain"]["max_volume_m3"] == 30000.0                    # 全域行
    assert hot["catchments"]["features"] == []
    assert "1D→2D" not in json.dumps(hot, ensure_ascii=False)                          # 水量对账行不混进来


def test_region_hotspots_fall_back_to_delivery_geojson(tmp_path, monkeypatch, _no_scope):
    monkeypatch.setattr(sim_c1, "DATA_ROOT", tmp_path / "nodata")                       # 没有台账
    root = make_delivery(tmp_path, "BNY_61mm2h", ("L1", "L2"), frames=4)
    make_region_stats(root, ["L1", "L2"], ["ADM-49"])
    (root / "公共").mkdir()
    (root / "公共" / "热点.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [54.37, 24.45]},
         "properties": {"hotspot_id": "ADM-049", "area": "ADM", "location": "somewhere"}}]}), encoding="utf-8")
    hot = json.loads((sim_c1.extract(root) / "hotspots.json").read_text(encoding="utf-8"))
    assert [(p["id"], p["lat"], p["lon"], p["location"]) for p in hot["points"]] == [("ADM-49", 24.45, 54.37, "somewhere")]


def test_ad_region_hotspots_from_own_tables(tmp_path, monkeypatch, _no_scope):
    """AD 区域交付（30 m）：积水点结果表 + 汇水区汇总 + 自带点图层；没有台账、没有统计值表、没有汇水区面。"""
    from fake_delivery import make_ad_stats
    monkeypatch.setattr(sim_c1, "DATA_ROOT", tmp_path / "nodata")                       # 没有台账：坐标取自带点图层
    root = make_delivery(tmp_path, "MI-8_61mm1h_30m", ("L1", "L2"), frames=4)
    make_ad_stats(root, ["ADM-23", "ADM-99", "ADM-196"])
    hot = json.loads((sim_c1.extract(root) / "hotspots.json").read_text(encoding="utf-8"))
    assert [p["id"] for p in hot["points"]] == ["ADM-23", "ADM-99", "ADM-196"]
    assert hot["states"] == ["L1", "L2"]
    p99 = hot["points"][1]
    assert p99["lat"] == 24.3 and p99["lon"] == 54.51 and p99["location"] == "loc1"
    assert p99["stats"]["L1"]["near_200m"] == {"max_depth_m": 0.6, "area_ge_0_10_ha": 1.0, "area_ge_0_30_ha": 0.2,
                                              "max_volume_m3": 2000.0}
    assert p99["stats"]["L2"]["near_200m"]["max_depth_m"] == 0.3                       # L2 列取对了（L1 的一半）
    assert hot["zones"]["L1"]["domain"]["max_depth_m"] == 2.851                        # 「全域最大水深 m」归到最大水深
    assert hot["zones"]["L2"]["domain"]["max_volume_m3"] == 5017621.0
    assert hot["zones"]["L1"]["domain"]["max_volume_h"] == 1.5
    assert hot["catchments"]["features"] == []                                         # 没有汇水区面


def test_no_stats_no_hotspots_file(tmp_path, _no_scope):
    base = sim_c1.extract(make_delivery(tmp_path, "BAT_61mm2h", ("L1",), frames=4))
    assert not (base / "hotspots.json").exists()


@pytest.mark.skipif(not LEDGER.is_file(), reason="没有 hotspots_845.csv")
def test_scope_without_catchment_polygons(tmp_path, monkeypatch):
    """区域交付没有汇水区面：口径只出 domain / near_200m，不报错。需要 rasterio（本机被策略拦的机器上跳过）。"""
    try:
        from rasterio.warp import transform
    except ImportError:
        pytest.skip("rasterio 不可用")
    from fake_delivery import X0, Y0
    root = make_delivery(tmp_path, "YAS_61mm2h", ("L1", "L2"), frames=4)
    make_region_stats(root, ["L1", "L2"], ["ADM-3"])
    xs, ys = [X0 - 1000, X0 + 2000, X0 + 2000, X0 - 1000, X0 - 1000], [Y0 - 1000, Y0 - 1000, Y0 + 2000, Y0 + 2000, Y0 - 1000]
    lon, lat = transform("EPSG:32640", "EPSG:4326", xs, ys)
    (root / "公共").mkdir()
    (root / "公共" / "计算域.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [list(zip(lon, lat))]}}]}), encoding="utf-8")
    from stormwater import sim_ponding
    monkeypatch.setattr(sim_ponding, "load", lambda d: {"clusters": []})
    hot = json.loads((sim_c1.extract(root) / "hotspots.json").read_text(encoding="utf-8"))
    assert list(hot["scopes"]) == ["domain", "near_200m"]


# ---- 「模型版」交付（Mussafah / Yas / Al Bateen / Bani Yas）：npz 在 <主机>/work/c2d ----

from fake_delivery import make_model_delivery  # noqa: E402
from stormwater.sim_sww import SwwError  # noqa: E402


def _read(base, st, name):
    return json.loads((base / st / name).read_text(encoding="utf-8"))


def test_model_layout_finds_npz_in_host_work_c2d(tmp_path, _no_scope):
    root = make_model_delivery(tmp_path / "srv1", states=("L1", "L2", "L2+planned", "L2+suggested"), frames=6)
    base = sim_c1.extract(root)                                   # 没给 --c2d：从交付往上找 <主机>/work/c2d
    st = json.loads((base / "states.json").read_text(encoding="utf-8"))
    assert [s["key"] for s in st["states"]] == ["L1", "L2", "L2+planned", "L2+suggested"]
    s = _read(base, "L2_suggested", "delivery_summary.json")
    assert s["source"]["native_npz"] == "Mussafah_00_M61H2_L2S3_2d.npz"
    assert sim_c1.extent_of(root) == "Mussafah_00"
    assert base.name == "Mussafah_00_模型版_61mm2h"
    # 不直接受雨：来水全是井口溢流，不套 C1 的「离井 > 46 m 直接受雨」，也不带 C1 的局限
    assert "地表不直接受雨" in s["claim_boundary"] and "直接受雨。" not in s["claim_boundary"]
    assert "ADM-90" not in s["claim_boundary"]
    assert s["model_configuration"]["surface_rainfall_inflow_m3"] == 0
    assert _read(base, "L1", "scenario.json")["spatial_pattern"] == "Per-node inflows (client model)"


def test_model_layout_direct_rain_text(tmp_path):
    root = make_model_delivery(tmp_path / "srv1", "YAS_模型版_61mm2h", "YAS_M61H2", frames=4, direct_rain=True)
    s = _read(sim_c1.extract(root), "L1", "delivery_summary.json")
    assert "离井 > 46 m 的地面直接受雨" in s["claim_boundary"]


def test_model_layout_npz_by_inp_stem_when_record_has_no_tag(tmp_path):
    root = make_model_delivery(tmp_path / "srv1", frames=4, with_tag=False)
    assert sim_c1.state_files(root, "L1").npz.name == "Mussafah_00_M61H2_L1_2d.npz"


def test_model_layout_explicit_c2d_and_res_fallback(tmp_path):
    root = make_model_delivery(tmp_path / "srv1", frames=4, res_m=None)
    elsewhere = tmp_path / "copy_of_c2d"
    (tmp_path / "srv1" / "work" / "c2d").rename(elsewhere)          # 交付往上再也找不到 work/c2d
    with pytest.raises(SwwError, match="work/c2d"):
        sim_c1.state_files(root, "L1")
    assert sim_c1.state_files(root, "L1", elsewhere).npz.parent == elsewhere
    assert sim_c1.delivery_res(root) == 15.0                        # 记录没写 res_m、目录名没标：退回 15 m


def test_model_layout_ambiguous_or_missing_npz(tmp_path):
    root = make_model_delivery(tmp_path / "srv1", frames=4, with_tag=False)
    c2d = tmp_path / "srv1" / "work" / "c2d"
    # 名字对不上、按态记号又有两场：要人指定，不悄悄挑
    inp = next(root.rglob("*_L1.inp")); inp.rename(inp.with_name("other_L1.inp"))
    (c2d / "Mussafah_00_M61H2_L1_2d.npz").rename(c2d / "A_L1_2d.npz")
    (c2d / "A_L1_2d.npz").write_bytes((c2d / "A_L1_2d.npz").read_bytes())
    (c2d / "B_L1_2d.npz").write_bytes((c2d / "A_L1_2d.npz").read_bytes())
    with pytest.raises(SwwError, match="匹配到 2 个"):
        sim_c1.state_files(root, "L1")
    (c2d / "B_L1_2d.npz").unlink(); (c2d / "A_L1_2d.npz").unlink()
    with pytest.raises(SwwError, match="匹配到 0 个"):
        sim_c1.state_files(root, "L1")


def test_model_layout_npz_inside_state_2d_dir(tmp_path):
    """30 m 区域交付：npz 就在 <态>/二维/ 里（AD_<区>_M61H1_L1_2d.npz），没有 work/c2d 也能抽。"""
    root = make_model_delivery(tmp_path / "srv1", "MI-8_61mm1h_30m", "AD_MI8_M61H1", frames=4, npz_in_delivery=True)
    assert not (tmp_path / "srv1" / "work").exists()
    sf = sim_c1.state_files(root, "L2")
    assert sf.npz.name == "AD_MI8_M61H1_L2_2d.npz" and sf.npz.parent.name == "二维"
    base = sim_c1.extract(root)
    assert _read(base, "L1", "delivery_summary.json")["source"]["native_npz"] == "AD_MI8_M61H1_L1_2d.npz"


def test_one_two_dim_layout_without_npz_falls_back_to_c2d(tmp_path):
    """C1 以 --no_big 导出时「一二维/」里没有 npz：退回 work/c2d（按运行记录 tag 找）。"""
    host = tmp_path / "srv1"
    root = make_delivery(host / "runtime" / "out" / "交付", "C1_模型版_61mm2h", ("L1",), frames=4)
    npz = next(root.rglob("*_二维水深帧与最大水深.npz"))
    c2d = host / "work" / "c2d"; c2d.mkdir(parents=True)
    npz.rename(c2d / "FAKE_L1_2d.npz")                              # 假记录里 tag = FAKE_L1
    assert sim_c1.state_files(root, "L1").npz == c2d / "FAKE_L1_2d.npz"


@pytest.mark.skipif(not LEDGER.is_file(), reason="没有 hotspots_845.csv")
def test_model_layout_hotspots_for_mussafah(tmp_path, _no_scope):
    root = make_model_delivery(tmp_path / "srv1", frames=4)
    make_region_stats(root, ["L1", "L2"], ["ADM-86", "ADM-87", "ADM-14", "ADM-189"], union="4 个热点")
    hot = json.loads((sim_c1.extract(root) / "hotspots.json").read_text(encoding="utf-8"))
    assert [p["id"] for p in hot["points"]] == ["ADM-14", "ADM-86", "ADM-87", "ADM-189"]
