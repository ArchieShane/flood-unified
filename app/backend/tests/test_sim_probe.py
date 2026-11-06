"""内网自检脚本 sim_probe：在假交付上跑，摘要要说清格式、能识别、只读、不吐数据本体。"""

import os

import numpy as np
from fake_delivery import make_delivery

from stormwater import sim_probe


def _snapshot(root):
    return sorted((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file())


def test_probe_5m_336_frames_two_states(tmp_path):
    root = make_delivery(tmp_path, "C1_模型版_61mm24h_5m", ("L1", "L2"), frames=336, end_h=28.0, res_m=5.0, facilities=True)
    before = _snapshot(tmp_path)
    txt = sim_probe.probe(root, depth=3)
    assert _snapshot(tmp_path) == before                        # 只读
    assert "frames: shape=(336, 3200) dtype=float16" in txt
    assert "帧数 336" in txt and "t 0.0833333 → 28 h，步长 5 min" in txt
    assert "res_m=5.0  end_h=28.0" in txt
    assert "预期输出键 sim_deliveries/C1_模型版_61mm24h_5m/" in txt and "落图格网 15 m" in txt
    assert "设施GIS: 态目录内（3 个 geojson）" in txt
    assert "结论：可抽取（2/2 态满足）" in txt
    assert "抽取代码识别：态 ['L1', 'L2']｜res_m=5｜输出键 C1_模型版_61mm24h_5m" in txt
    assert "系统储水能力将退回按 .inp 自算" in txt and "溢水点" in txt


def test_probe_flags_missing_pieces(tmp_path):
    root = make_delivery(tmp_path, "YAS_61mm2h", ("L1", "L2"), speed=False)
    rec = next((root / "L2_加OP-02移动泵" / "一二维").glob("*耦合运行记录.json"))
    rec.unlink()
    (root / "L1_现状管网+现状泵站" / "一二维" / "耦合的一维" / "x.rpt").write_text("dup", encoding="utf-8")   # 第二个 rpt
    txt = sim_probe.probe(root, depth=3)
    assert "*_耦合运行记录.json 缺" in txt
    assert "耦合的一维/ 下 .rpt 有 2 个，需恰好一个" in txt
    assert "结论：不可抽取" in txt and "0/2 态满足" in txt
    assert "流速 uh/vh 无" in txt


def test_probe_ignores_macos_companion_files(tmp_path):
    root = make_delivery(tmp_path, "YAS_61mm2h", ("L1",))
    d = root / "L1_现状管网+现状泵站" / "一二维" / "耦合的一维"
    (d / "._L1_耦合一维报告.rpt").write_bytes(b"\x00\x05\x16\x07")
    assert "结论：可抽取（1/1 态满足）" in sim_probe.probe(root, depth=3)


def test_probe_recognises_sww_only_layout_as_unsupported(tmp_path):
    """只有「二维/」放 .sww、没有「一维/」也没有 npz 的目录：认不了。"""
    root = tmp_path / "Mussafah_00_模型版_61mm2h"
    d = root / "L1_既有管网" / "二维"
    d.mkdir(parents=True)
    (d / "Mussafah_00_L1.sww").write_bytes(b"CDF\x01")
    (d / "L1_耦合运行记录.json").write_text("{}", encoding="utf-8")
    txt = sim_probe.probe(root, depth=3)
    assert "既没有「一二维/」也没有「一维/ + 二维/」" in txt and "结论：不可抽取" in txt


def test_probe_nas_host_dir_finds_loose_npz_and_deliveries(tmp_path):
    host = tmp_path / "result" / "srv1"
    c2d = host / "work" / "c2d"
    c2d.mkdir(parents=True)
    from fake_delivery import make_npz
    make_npz(c2d / "Mussafah_00_L1_2d.npz", frames=5)
    make_delivery(host / "runtime" / "out" / "交付", "Mussafah_00_模型版_61mm2h", ("L1",))   # 名称带「模型版」也认
    txt = sim_probe.probe(host, depth=6)
    assert "交付目录之外的零散成果" in txt and "Mussafah_00_L1_2d.npz" in txt


def test_probe_never_prints_array_values(tmp_path):
    root = make_delivery(tmp_path, "YAS_61mm2h", ("L1",), frames=6)
    z = np.load(next(root.rglob("*二维水深帧与最大水深.npz")))
    txt = sim_probe.probe(root, depth=3)
    for v in np.unique(z["hmax"])[:200]:
        assert f"{float(v):.5f}" not in txt


def test_probe_missing_path(tmp_path):
    assert "路径不存在" in sim_probe.probe(tmp_path / "nope")
    assert os.path.exists(tmp_path)


def test_probe_model_layout_finds_npz_in_work_c2d(tmp_path):
    """Mussafah 模型版 / 区域交付（ms_post.py / rg_post.py）：一维 + 二维目录，npz 在 <主机>/work/c2d。"""
    from fake_delivery import make_model_delivery
    root = make_model_delivery(tmp_path / "srv1", states=("L1", "L2"), frames=6)
    txt = sim_probe.probe(tmp_path / "srv1", depth=6)
    assert "形态：一维 + 二维（模型版形态，npz 在 work/c2d）" in txt
    assert "（work/c2d（按 运行记录 tag = Mussafah_00_M61H2_L1）" in txt
    assert "地表直接受雨量 acc_m3.rain = 0.0（不直接受雨，来水全是井口溢流）" in txt
    assert "结论：可抽取（2/2 态满足）" in txt
    assert "抽取代码识别：态 ['L1', 'L2']｜res_m=15｜输出键 Mussafah_00_模型版_61mm2h｜范围 Mussafah_00" in txt
    assert "交付目录之外的零散成果" in txt          # work/c2d 里的 npz 本身也各列一遍
    assert root.exists()


def test_probe_model_layout_without_c2d_lists_what_is_there(tmp_path):
    from fake_delivery import make_model_delivery
    root = make_model_delivery(tmp_path / "srv1", states=("L1",), frames=4, with_tag=False)
    c2d = tmp_path / "srv1" / "work" / "c2d"
    (c2d / "Mussafah_00_M61H2_L1_2d.npz").rename(c2d / "something_else_2d.npz")
    (next(root.rglob("*_L1.inp"))).rename(root / "L1_既有管网" / "一维" / "weird.inp")
    txt = sim_probe.probe(tmp_path / "srv1", depth=6)
    assert "共 1 个 *_2d.npz，前 12 个：['something_else_2d.npz']" in txt
    assert "work/c2d 里找不到 L1 的 npz" in txt and "结论：不可抽取" in txt


def test_probe_city_delivery_shows_sim_city_key_not_c1_key(tmp_path):
    from fake_delivery import make_delivery
    root = make_delivery(tmp_path, "全市_61mm2h", ("L1", "L2"), frames=3)
    txt = sim_probe.probe(root, depth=3)
    assert "交付形状：全市一二维（sim_city）" in txt
    assert "登记口径（sim_city）：态 ['L1', 'L2']｜输出键 全市_61mm2h｜范围 City-wide" in txt
    assert "全市_61mm2h_30m" not in txt
