"""统一登记入口 sim_import：按交付形状选 sim_sww / sim_city / sim_c1，备份目录名不被当成交付。"""

from pathlib import Path

import pytest
from fake_delivery import make_delivery, make_model_delivery

from stormwater import sim_import, sim_probe
from stormwater.sim_sww import SwwError


def _sww_delivery(parent: Path, name="Mussafah_00_61mm2h") -> Path:
    """旧 .sww 交付的骨架：L1 有 二维/*.sww + 耦合运行记录 + 耦合的一维/；L2 只放说明（≡ L1）。"""
    root = parent / name
    d2 = root / "L1_既有管网" / "二维"
    (d2 / "耦合的一维").mkdir(parents=True)
    (d2 / "L1_二维水深时序.sww").write_bytes(b"CDF\x01")
    (d2 / "L1_耦合运行记录.json").write_text('{"res_m": 15.0, "end_h": 6.0}', encoding="utf-8")
    (d2 / "耦合的一维" / "x.rpt").write_text("r", encoding="utf-8")
    (d2 / "耦合的一维" / "x.inp").write_text("i", encoding="utf-8")
    (root / "L2_已建成调蓄").mkdir()
    (root / "L2_已建成调蓄" / "说明.md").write_text("≡ L1", encoding="utf-8")
    return root


def test_detect_three_kinds(tmp_path):
    assert sim_import.detect(_sww_delivery(tmp_path)) == "sww"
    assert sim_import.detect(make_delivery(tmp_path, "全市_61mm2h", ("L1", "L2"), frames=2)) == "city"
    assert sim_import.detect(make_delivery(tmp_path, "C1_模型版_61mm2h_5m", ("L1",), frames=2)) == "c1"
    # 新的「模型版」形状：二维/ 里是 .sww 但文件名不是 *_二维水深时序.sww、也没有 耦合的一维/ → 仍是 c1（npz 在 work/c2d）
    assert sim_import.detect(make_model_delivery(tmp_path / "h", frames=2)) == "c1"


def test_backup_dir_is_not_a_delivery(tmp_path):
    bad = tmp_path / "C1_模型版_61mm2h.bak-20261005"
    bad.mkdir()
    with pytest.raises(SwwError, match="不是「片区_雨深mm历时h"):
        sim_import.detect(bad)


def test_import_any_routes_to_the_right_module(tmp_path, monkeypatch):
    from stormwater import sim_c1, sim_city, sim_sww
    calls = []
    monkeypatch.setattr(sim_sww, "import_all", lambda root, by, only: calls.append(("sww", root.name, by, only)) or 11)
    monkeypatch.setattr(sim_city, "import_all", lambda root, by, only: calls.append(("city", root.name, by, only)) or 22)
    monkeypatch.setattr(sim_c1, "import_all", lambda root, by, only, c2d: calls.append(("c1", root.name, by, only, c2d)) or 33)
    a = _sww_delivery(tmp_path)
    b = make_delivery(tmp_path, "全市_61mm2h", ("L1",), frames=2)
    c = make_delivery(tmp_path, "C1_模型版_61mm2h", ("L1",), frames=2)
    assert sim_import.import_any(a, "AR") == ("sww", 11)
    assert sim_import.import_any(b, "AR", ["L1"]) == ("city", 22)
    assert sim_import.import_any(c, "ZN", None, Path("/x/c2d")) == ("c1", 33)
    assert calls == [("sww", "Mussafah_00_61mm2h", "AR", None), ("city", "全市_61mm2h", "AR", ["L1"]),
                     ("c1", "C1_模型版_61mm2h", "ZN", None, Path("/x/c2d"))]


def test_probe_reports_kind_and_checks_sww_layout(tmp_path):
    root = _sww_delivery(tmp_path)
    txt = sim_probe.probe(root, depth=4)
    assert "交付形状：旧 .sww 交付（sim_sww）" in txt
    assert "L1_既有管网 .sww: L1_二维水深时序.sww" in txt
    assert "L2_已建成调蓄：无「二维/」（≡ L1，只放说明，抽取时硬链 L1）" in txt
    assert "结论：可抽取" in txt and "交付统计值" in txt
    (root / "L1_既有管网" / "二维" / "耦合的一维" / "x.rpt").unlink()
    assert "✗ .rpt 缺" in sim_probe.probe(root, depth=4)


def test_probe_ignores_backup_and_non_delivery_dirs(tmp_path):
    host = tmp_path / "srv" / "runtime" / "out" / "交付"
    (host / "C1_模型版_61mm2h.bak-20261005" / "L1_x").mkdir(parents=True)        # 备份目录：名字不是交付形状
    (host / "C1汇水区_模型版").mkdir()
    txt = sim_probe.probe(tmp_path / "srv", depth=6)
    assert "没找到形如「片区_雨深mm历时h」" in txt
