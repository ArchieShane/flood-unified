# -*- coding: utf-8 -*-
"""tools/audit_parsons.py 的判据测试。不读模型文件。

钉两件事：
  1. 边坡要**反算成比值**，不能看面积增幅百分比 —— 同样 3:1，
     小塘增幅 17%、大塘只增 7%，看百分比会把大塘误判成直壁；
  2. 塘与泵站湿井要按**功能**分（泵进水 + 无出水口 = 塘），
     按名字数会得出「Shaliela 36 座塘」这种错数（实为 10 座塘 + 26 座湿井）。
"""
from __future__ import annotations

import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import audit_parsons as AP  # noqa: E402


def slope_of(a0: float, a1: float, depth: float) -> float:
    """工具里用的反算式，独立写一遍做对照。"""
    return (math.sqrt(a1) - math.sqrt(a0)) / (2 * depth)


def prismoid_top_area(a0: float, m: float, depth: float) -> float:
    """底面积 a0 的方塘，按 m:1 边坡放坡 depth 之后的顶面积。"""
    return (math.sqrt(a0) + 2 * m * depth) ** 2


# ---------------------------------------------------------------- 边坡
@pytest.mark.parametrize("a0", [2_500.0, 21_515.0, 78_700.0])
def test_三比一边坡能被反算出来_不论塘大小(a0):
    d, m = 2.0, 3.0
    a1 = prismoid_top_area(a0, m, d)
    assert slope_of(a0, a1, d) == pytest.approx(m, rel=1e-9)


def test_只看面积增幅会把大塘误判成直壁():
    """这正是先前那版的毛病：同样 3:1，增幅差一倍多。"""
    d, m = 2.0, 3.0
    small = prismoid_top_area(2_500.0, m, d) / 2_500.0 - 1
    big = prismoid_top_area(78_700.0, m, d) / 78_700.0 - 1
    assert small > 0.4 and big < 0.2          # 小塘 >40%，大塘 <20%
    assert big < 0.2                            # 用 5% 阈值判「直壁」时，大塘会被误判


def test_直壁的边坡比是零():
    assert slope_of(800.0, 800.0, 1.5) == pytest.approx(0.0)


def test_边坡越陡比值越小():
    d, a0 = 2.0, 10_000.0
    v = [slope_of(a0, prismoid_top_area(a0, m, d), d) for m in (0.5, 1.0, 3.0)]
    assert v == sorted(v)


# ---------------------------------------------------------------- 塘 vs 湿井
def _kind(q_in: float, n_out: int) -> str:
    return "塘" if (q_in > 0 and n_out == 0) else "湿井"


def test_泵进水且无出水口才算塘():
    assert _kind(0.722, 0) == "塘"


def test_有出水口的是湿井_哪怕也叫PO():
    assert _kind(0.722, 1) == "湿井"


def test_重力进水的是湿井():
    assert _kind(0.0, 3) == "湿井"
    assert _kind(0.0, 0) == "湿井"


# ---------------------------------------------------------------- 常量
def test_洪峰取自本系统重算的产流():
    """PEAK_2H 是 61 mm / 2 h 的全网入流洪峰，改它等于改了「泵占洪峰几成」的分母。"""
    assert set(AP.PEAK_2H) == {"Mussafah", "Shaliela", "RB3"}
    assert AP.PEAK_2H["Mussafah"] == pytest.approx(20.71, abs=0.01)
    assert AP.PEAK_2H["Shaliela"] > AP.PEAK_2H["Mussafah"]


def test_判地上地下取周边一圈不取单格():
    """单格会被噪声或一棵树带偏，所以要取一圈。"""
    assert AP.RING_M >= 30.0
