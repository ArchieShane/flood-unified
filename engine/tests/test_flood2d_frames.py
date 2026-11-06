# -*- coding: utf-8 -*-
"""二维抽帧的口径，以及「图上的数必须等于表里的数」。

这一组存在的理由是一次真实的翻车：第一版抽帧用了**顶点**量和 0.05 m 阈值，
出来的峰值是 5.13 ha / 0.481 m，而四态表里是 8.75 ha / 0.840 m ——
同一个界面里两处数差 1.7 倍，谁也说不清该信哪个。
根因有两条，都不显眼：
  · sww 里同时存顶点量（stage/elevation，129,791 个）和形心量（stage_c/elevation_c，258,332 个）。
    顶点量是限制器重构出来的，深槽处会被压平；耦合脚本用的是形心量。
  · 干湿阈值极其敏感：某一时刻 >0.01 m 有 1,995 个单元、>0.05 m 只剩 633 个，差 3.2 倍。
    耦合脚本用的是 `h > 0.02`。
还有第三条是抽样：均匀抽 40 帧会漏掉真峰（L1 实测 15.38 vs 真值 16.03，差 4%）。

所以下面锁死四件事：形心、0.02、按单元面积加权、峰值全时刻扫描。
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import sww_frames as SF  # noqa: E402

MF = ROOT / "console" / "data" / "flood2d" / "manifest.json"
TB = ROOT / "out" / "state_table_Mussafah_00_R61D2.json"
skip_mf = pytest.mark.skipif(not MF.exists(), reason="还没抽帧")


def test_口径三条和耦合脚本一致():
    assert SF.USE_CENTROID is True, "必须用形心量：顶点量会把峰值水深从 0.840 压到 0.481"
    assert SF.DRY_M == pytest.approx(0.02), (
        f"干湿阈值 {SF.DRY_M} 和 couple2way 的 `h > 0.02` 不一致，淹没面积会对不上")


def test_面积按单元面积加权而不是数格子():
    """15 m 格里平均有 4 个三角单元，只要一个湿就整格算湿，会系统性高估。"""
    src = inspect.getsource(SF.one)
    assert "dot(areas)" in src, "面积没有按三角单元真实面积加权"


def test_峰值是全时刻扫描不是抽样():
    src = inspect.getsource(SF.one)
    assert "for it in range(n_t)" in src, "峰值必须扫全部时刻，抽样会漏掉真峰"
    assert "i_area" in src and "i_dep" in src


@skip_mf
def test_峰值帧在播放序列里():
    """峰值那一帧不在序列里，播到头也看不到最淹的一刻。"""
    m = json.loads(MF.read_text(encoding="utf-8"))
    for st in m["states"]:
        i = st.get("peak_frame")
        assert i is not None and 0 <= i < len(st["frames"]), st["state"]
        fr = st["frames"][i]
        assert fr["wet_ha"] == pytest.approx(st["peak_wet_ha"], abs=0.02), (
            f"{st['state']} 的 peak_frame 指向的不是峰值面积那一帧")


@skip_mf
@pytest.mark.skipif(not TB.exists(), reason="还没出四态表")
def test_图上的峰值等于表里的峰值():
    """**同一个界面里两处数不一致，比数偏低更伤。**"""
    m = json.loads(MF.read_text(encoding="utf-8"))
    t = json.loads(TB.read_text(encoding="utf-8"))
    A = t["table"]["二维·峰值淹没面积"]["values"]
    D = t["table"]["二维·峰值水深"]["values"]
    for st in m["states"]:
        k = st["state"]
        assert A[k] == pytest.approx(st["peak_wet_ha"], abs=0.005), (
            f"{k} 面积：表 {A[k]} vs 图 {st['peak_wet_ha']}")
        assert D[k] == pytest.approx(st["peak_depth_m"], abs=0.0005), (
            f"{k} 水深：表 {D[k]} vs 图 {st['peak_depth_m']}")


@skip_mf
def test_manifest_把口径写给人看():
    m = json.loads(MF.read_text(encoding="utf-8"))
    assert m["dry_m"] == pytest.approx(SF.DRY_M)
    assert m.get("centroid") is True
    for word in ("形心", "0.02", "二维计算域"):
        assert word in m["note"], f"note 里没写清 {word}"
    assert "L2" in m.get("l2_note", ""), "要说明 L2 为什么不单独出帧"


@skip_mf
def test_不给L2单出一套帧():
    """L2 区内 0 座已建成塘，≡ L1。单出一套只会让人以为它是独立一次推演。"""
    m = json.loads(MF.read_text(encoding="utf-8"))
    st = {x["state"] for x in m["states"]}
    assert "L2" not in st
    # 2026-09-27 起加 L2+A+B（Parsons 之上加箱涵，tools/l2ab.py），五态里真正独立推演的是这四套
    assert st == {"L1", "L2+A", "L2+B", "L2+A+B"}
