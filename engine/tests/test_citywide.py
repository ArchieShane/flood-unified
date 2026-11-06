# -*- coding: utf-8 -*-
"""tools/citywide.py 的判据测试。不连库、不读 GDB。

钉三件事：
  1. SCS 产流深要与手算对得上，且在 Ia 以下必须是 0；
  2. 服务半径要夹住 —— 节点只有两三个时迭代会把半径推到几百米，
     凭空造出十几公顷服务面积（实测出现过 R=297 m）；
  3. 孤立子网要按连通块拆开，不能混成一个。
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

pytest.importorskip("scipy")
import citywide as CW  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402


# ---------------------------------------------------------------- 产流
def test_产流深与手算一致():
    # S = 25400/82 − 254 = 55.76；Ia = 11.15；Q = (61−11.15)²/(61+44.61)
    S = 25400.0 / 82.0 - 254.0
    want = (61.0 - 0.2 * S) ** 2 / (61.0 + 0.8 * S)
    assert CW.scs_excess_mm(61.0, 82.0) == pytest.approx(want)
    assert CW.scs_excess_mm(61.0, 82.0) == pytest.approx(23.53, abs=0.01)


def test_初损以下不产流():
    S = 25400.0 / 82.0 - 254.0
    assert CW.scs_excess_mm(0.2 * S, 82.0) == 0.0
    assert CW.scs_excess_mm(0.2 * S - 1, 82.0) == 0.0
    assert CW.scs_excess_mm(0.0, 82.0) == 0.0


def test_雨越大产流越大_CN越大产流越大():
    v = [CW.scs_excess_mm(p, 82.0) for p in (20, 40, 61, 100)]
    assert v == sorted(v)
    w = [CW.scs_excess_mm(61.0, cn) for cn in (60, 70, 82, 95)]
    assert w == sorted(w)


def test_客户自己的CN是82():
    """CN 是客户模型里的值，不是我们挑的。改这个常量等于改了口径。"""
    assert CW.CN == 82.0


# ---------------------------------------------------------------- 服务面积
def _grid_nodes(nx, ny, step=40.0, x0=0.0, y0=0.0):
    gx = x0 + np.arange(nx) * step
    gy = y0 + np.arange(ny) * step
    GX, GY = np.meshgrid(gx, gy)
    return np.c_[GX.ravel(), GY.ravel()]


def test_服务半径被夹住_节点少时不发散():
    pts = np.array([[0.0, 0.0], [30.0, 0.0]])          # 只有两个点
    tree = cKDTree(pts)
    owner = np.zeros(len(pts), dtype=np.int64)
    A, R = CW.served_area(tree, owner, 0, pts, 10.0)
    assert CW.R_MIN <= R <= CW.R_MAX, R
    assert A <= np.pi * CW.R_MAX ** 2 * len(pts)        # 不会凭空多出面积


def test_服务面积随节点数增长():
    a = _grid_nodes(4, 4); b = _grid_nodes(8, 8)
    for pts in (a, b):
        pass
    tree_a = cKDTree(a); tree_b = cKDTree(b)
    Aa, _ = CW.served_area(tree_a, np.zeros(len(a), dtype=np.int64), 0, a, 10.0)
    Ab, _ = CW.served_area(tree_b, np.zeros(len(b), dtype=np.int64), 0, b, 10.0)
    assert Ab > Aa


def test_相邻汇水域不重复计地():
    """两组节点并排，各自只能认领离自己更近的格子。"""
    a = _grid_nodes(5, 5, x0=0.0)
    b = _grid_nodes(5, 5, x0=1000.0)
    allpts = np.vstack([a, b])
    owner = np.concatenate([np.zeros(len(a), dtype=np.int64),
                            np.ones(len(b), dtype=np.int64)])
    tree = cKDTree(allpts)
    Aa, _ = CW.served_area(tree, owner, 0, a, 10.0)
    Ab, _ = CW.served_area(tree, owner, 1, b, 10.0)
    Aall, _ = CW.served_area(tree, np.zeros(len(allpts), dtype=np.int64), 0, allpts, 10.0)
    assert Aa == pytest.approx(Ab, rel=0.05)
    assert Aa + Ab == pytest.approx(Aall, rel=0.15)


# ---------------------------------------------------------------- 孤立子网
def _net(edges, n, owner):
    a = np.array([e[0] for e in edges], dtype=np.int64)
    b = np.array([e[1] for e in edges], dtype=np.int64)
    return {"owner": np.array(owner, dtype=np.int64), "N": n, "a": a, "b": b}


def test_孤立子网按连通块拆开():
    # 0-1-2 一块，3-4 一块，5 单独；全部没有出水口
    net = _net([(0, 1), (1, 2), (3, 4)], 6, [-1] * 6)
    CW.split_orphans(net, log=lambda *a: None)
    o = net["owner"]
    assert o[0] == o[1] == o[2]
    assert o[3] == o[4]
    assert len({int(o[0]), int(o[3]), int(o[5])}) == 3      # 三块互不相同
    assert (o <= -2).all()                                  # 都拿到孤立编号
    assert net["n_orphan"] == 3


def test_有出水口的节点不被改动():
    net = _net([(0, 1), (2, 3)], 4, [0, 0, -1, -1])
    CW.split_orphans(net, log=lambda *a: None)
    o = net["owner"]
    assert o[0] == 0 and o[1] == 0
    assert o[2] == o[3] <= -2


def test_没有孤立节点时什么都不做():
    net = _net([(0, 1)], 2, [0, 0])
    CW.split_orphans(net, log=lambda *a: None)
    assert (net["owner"] == 0).all()
    assert "n_orphan" not in net


# ---------------------------------------------------------------- 排走比例
def test_排走比例是三个真区的实测值():
    """CONVEY 的上界必须等于 Mussafah 实测（1 − 溢出/入流），改了就是改口径。"""
    a = CW.ANCHOR["Mussafah"]
    for dur in (1, 2, 24):
        want = 1.0 - a[dur] / a["inflow"]
        assert CW.CONVEY[dur][1] == pytest.approx(want, abs=0.002), dur


def test_短历时排走得更少():
    assert CW.CONVEY[1][1] < CW.CONVEY[2][1] < CW.CONVEY[24][1]


def test_Shaliela短历时确实排不走():
    """下界 0 不是占位——Shaliela 1h/2h 溢出量 ≥ 入流量。"""
    a = CW.ANCHOR["Shaliela"]
    assert a[1] >= a["inflow"] and a[2] >= a["inflow"]
    assert CW.CONVEY[1][0] == 0.0 and CW.CONVEY[2][0] == 0.0
