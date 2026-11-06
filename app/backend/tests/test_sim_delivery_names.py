"""交付目录名 / 网格键 / 态目录 / 设施 GIS 位置的解析（不连库、不读真实成果）。"""

import json

import pytest

from stormwater import sim_c1, sim_sww


@pytest.mark.parametrize("name,expect", [
    ("C1_61mm2h", ("C1", 61.0, 2.0, None)),
    ("Mussafah_00_61mm2h", ("Mussafah_00", 61.0, 2.0, None)),
    ("C1_模型版_61mm2h_5m", ("C1_模型版", 61.0, 2.0, 5.0)),
    ("YAS_61mm24h_G5", ("YAS", 61.0, 24.0, 5.0)),
    ("BAT_96.5mm1h_15m", ("BAT", 96.5, 1.0, 15.0)),
])
def test_parse_delivery_name(name, expect):
    assert sim_sww.parse_delivery_name(name) == expect


def test_parse_delivery_name_rejects_garbage():
    with pytest.raises(sim_sww.SwwError):
        sim_sww.parse_delivery_name("C1_final")


def test_output_key_keeps_15m_and_separates_5m(tmp_path):
    a, b = tmp_path / "C1_61mm2h", tmp_path / "C1_模型版_61mm2h_5m"
    assert sim_sww.output_key(a) == "C1_61mm2h"            # 历史目录名不变
    assert sim_sww.output_key(a, 15.0) == "C1_61mm2h"
    assert sim_sww.output_key(b, 5.0) == "C1_模型版_61mm2h_5m"
    assert sim_sww.output_key(b) == "C1_模型版_61mm2h_5m"      # 记录没写 res_m 时取目录名里标的
    assert sim_sww.output_key(tmp_path / "C1_模型版_61mm2h", 5.0) == "C1_模型版_61mm2h_5m"
    assert sim_sww.output_key(a, 5.0) != sim_sww.output_key(a, 15.0)   # 同雨量不同网格不撞键


def test_extent_of():
    from pathlib import Path
    assert sim_c1.extent_of(Path("C1_模型版_61mm2h_5m")) == "C1"
    assert sim_c1.extent_of(Path("YAS_61mm2h")) == "YAS"


def _delivery(root, dirs):
    for d in dirs:
        (root / d).mkdir(parents=True)
    return root


def test_state_dir_matches_v3_suggested_by_prefix(tmp_path):
    root = _delivery(tmp_path / "C1_61mm2h", ["L1_现状", "L2_x", "L2+planned_y", "L2+suggested_v3_按塘余量分配"])
    assert sim_c1._state_dir(root, "L2+suggested").name == "L2+suggested_v3_按塘余量分配"
    assert sim_c1._state_dir(root, "L2").name == "L2_x"        # 前缀 L2_ 不会吃掉 L2+…
    assert sim_c1.present_states(root) == ["L1", "L2", "L2+planned", "L2+suggested"]


def test_present_states_only_what_exists(tmp_path):
    root = _delivery(tmp_path / "YAS_61mm2h", ["L1_现状", "L2_加泵"])
    assert sim_c1.present_states(root) == ["L1", "L2"]


def test_two_dirs_for_one_state_is_an_error(tmp_path):
    root = _delivery(tmp_path / "C1_61mm2h", ["L2+suggested_v2", "L2+suggested_v3"])
    with pytest.raises(sim_sww.SwwError):
        sim_c1._state_dir(root, "L2+suggested")


def _geo(d, stem="蓄水池_规划塘"):
    d.mkdir(parents=True)
    (d / f"{stem}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")


def test_facility_dir_prefers_state_dir_then_sibling(tmp_path):
    root = _delivery(tmp_path / "C1_61mm2h", ["L1_a", "L2+planned_b", "L2+suggested_c"])
    _geo(tmp_path / "planned" / "设施GIS")                       # 旧样例：交付旁边
    assert sim_c1.facility_dir(root, "L2+planned") == tmp_path / "planned" / "设施GIS"
    assert sim_c1.facility_dir(root, "L2+suggested") is None
    _geo(root / "L2+planned_b" / "设施GIS")                       # 服务器交付：态目录里
    assert sim_c1.facility_dir(root, "L2+planned") == root / "L2+planned_b" / "设施GIS"
    assert sim_c1.facility_dir(root, "L1") is None


def test_delivery_res_from_coupling_record(tmp_path):
    root = _delivery(tmp_path / "C1_模型版_61mm2h_5m", ["L1_a/一二维", "L2_b/一二维"])
    for d in ("L1_a", "L2_b"):
        (root / d / "一二维" / "x_耦合运行记录.json").write_text('{"res_m": 5.0}', encoding="utf-8")
    assert sim_c1.delivery_res(root) == 5.0
    (root / "L2_b" / "一二维" / "x_耦合运行记录.json").write_text('{"res_m": 15.0}', encoding="utf-8")
    with pytest.raises(sim_sww.SwwError):
        sim_c1.delivery_res(root)


def test_suggested_note_does_not_reuse_v2_text_for_v3():
    v2 = sim_c1._state_note("L2+suggested", "L2+suggested_转输与地面取水泵")
    v3 = sim_c1._state_note("L2+suggested", "L2+suggested_v3_按塘余量分配")
    assert "转输 / 地面取水泵" in v2 and "挡水土堤" in v2
    assert "v3_按塘余量分配" not in v2 and "按塘余量分配" in v3 and "挡水土堤" not in v3


@pytest.mark.skipif(__import__("os").name == "nt", reason="权限位在 Windows 上没有同样的语义")
def test_link_does_not_inherit_unreadable_mode_from_nas_file(tmp_path, monkeypatch):
    """NAS 文件 660（root:组）：拷贝到抽取目录后要对 API 进程（其他用户）可读，不能把 660 带过来。"""
    import os
    import stat

    from stormwater import sim_city

    src = tmp_path / "x.rpt"
    src.write_text("rpt", encoding="utf-8")
    src.chmod(0o660)
    dst = tmp_path / "out" / "swmm_coupled.rpt"
    dst.parent.mkdir()
    sim_city._link(src, dst)                         # 源文件对其他人不可读：不硬链，改拷贝
    assert dst.read_text(encoding="utf-8") == "rpt"
    assert stat.S_IMODE(dst.stat().st_mode) & 0o004, oct(dst.stat().st_mode)
    assert dst.stat().st_ino != src.stat().st_ino
    src.chmod(0o644)
    dst2 = tmp_path / "out" / "b.rpt"
    sim_city._link(src, dst2)                        # 可读：硬链，零拷贝
    assert dst2.stat().st_ino == src.stat().st_ino
    assert os.path.samefile(src, dst2)
