"""热点编号归一：ADM / AAM / DRM 三个台账的编号都认，前缀必须带着（三个台账的数字部分会重复）。"""
import pytest

from stormwater import sim_c1


@pytest.mark.parametrize("raw,key", [
    ("ADM-21", "ADM-21"), ("ADM-021", "ADM-21"), (" AAM-001 ", "AAM-1"), ("DRM-155", "DRM-155"),
])
def test_adm_normalises_all_three_ledgers(raw, key):
    assert sim_c1._adm(raw) == key


@pytest.mark.parametrize("raw", ["Outfalls", "ADM", "XYZ-1", "21", None, "全域"])
def test_adm_rejects_non_hotspot_ids(raw):
    assert sim_c1._adm(raw) is None


def test_same_number_in_different_ledgers_does_not_collide():
    assert sim_c1._adm("ADM-001") != sim_c1._adm("AAM-001")


# ---- 艾因形状：对比_缺口分析/统计值.csv 只有全域行（表头无「区域」列），逐热点的数在 积水点/积水点结果表.csv ----

_HOT_CSV = (
    "积水点,片区,位置,重要性,x (EPSG:32640),y (EPSG:32640),是否在二维计算域内,"
    "L1 200 m 内最大水深 m,L1 积水面积 ≥0.10 m (ha),L1 积水面积 ≥0.30 m (ha),L1 最大积水量 m³,"
    "L2 200 m 内最大水深 m,L2 积水面积 ≥0.10 m (ha),L2 积水面积 ≥0.30 m (ha),L2 最大积水量 m³\n"
    "AAM-001,Al Aamerah,Al Rifaa,Very High,355256.9,2682164.3,是,0.067,0.0,0.0,2817,0.067,0.0,0.0,2817\n"
    "AAM-002,Al Aamerah,34 St,Very High,352983.9,2681375.9,是,0.041,0.0,0.0,1493,0.041,0.0,0.0,1493\n"
)
_ZONE_CSV = "口径,指标,L1,L2\n全域,最大水深 m,1.018,1.018\n全域,积水面积 ≥0.30 m（ha）,191.73,191.73\n"


def test_zone_only_stats_table_is_merged_with_per_hotspot_table(tmp_path):
    if not (sim_c1.DATA_ROOT / "processed" / "hotspots_845.csv").is_file():
        pytest.skip("没有台账 hotspots_845.csv")
    (tmp_path / "对比_缺口分析").mkdir()
    (tmp_path / "对比_缺口分析" / "统计值.csv").write_text(_ZONE_CSV, encoding="utf-8-sig")
    (tmp_path / "积水点").mkdir()
    (tmp_path / "积水点" / "积水点结果表.csv").write_text(_HOT_CSV, encoding="utf-8-sig")
    out = tmp_path / "out"
    out.mkdir()
    assert sim_c1.extract_hotspots(tmp_path, out, ["L1", "L2"]) == out / "hotspots.json"
    import json
    hot = json.loads((out / "hotspots.json").read_text(encoding="utf-8"))
    assert [p["id"] for p in hot["points"]] == ["AAM-1", "AAM-2"]
    assert hot["points"][0]["stats"]["L1"]["near_200m"]["max_depth_m"] == 0.067
    assert hot["zones"]["L1"]["domain"]["max_depth_m"] == 1.018


def test_zone_only_table_without_hotspot_table_gives_no_points(tmp_path):
    (tmp_path / "对比_缺口分析").mkdir()
    (tmp_path / "对比_缺口分析" / "统计值.csv").write_text(_ZONE_CSV, encoding="utf-8-sig")
    out = tmp_path / "out"
    out.mkdir()
    assert sim_c1.extract_hotspots(tmp_path, out, ["L1", "L2"]) is None
