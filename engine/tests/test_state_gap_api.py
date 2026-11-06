# -*- coding: utf-8 -*-
"""/api/state_gap（一二维耦合的四态缺口）的契约。

这个接口最容易出的错不是「取不到数」，而是**取到另一套数还当成自己的**：
系统里同时存在两条流水线 ——
  · /api/three_lines 读 out/three_lines/，纯一维（R61D1 / R61D24 雨情）
  · /api/state_gap   读 out/state_table_*，一二维耦合（R61D2、72 h、ANUGA 15 m）
雨情、演算窗口、是否耦合都不同，数天然对不上。页面把两块并排显示，
所以**每一块都必须能自证自己是哪一套**，否则看的人只会得出「系统前后矛盾」。

下面锁住四件事：路由在、区域口径带得出来、四个态齐、以及两套数据源不串。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swbrain import routes  # noqa: E402

STATES = ["L1", "L2", "L2+A", "L2+B"]


class _S:
    """只用到 cfg，不连库。"""
    cfg: dict = {}


def _call(region="Mussafah_00"):
    r = routes.resolve("GET", "/api/state_gap")
    assert r is not None, "路由没登记"
    return r.fn(_S(), {"region": [region]}, None, None)


def test_路由已登记():
    assert routes.resolve("GET", "/api/state_gap") is not None


def test_取不到时不假装有数():
    d = _call(region="不存在的区")
    assert d["available"] is False
    assert "note" in d, "要说清为什么没有，不能只给个空壳"


@pytest.mark.skipif(not list((ROOT / "out").glob("state_table_*.json")),
                    reason="还没跑出耦合四态结果")
def test_四个态齐并且区域口径写全():
    d = _call()
    assert d["available"] is True
    assert d["region"] == "Mussafah_00", "区域必须写到分区，不能只写 Mussafah"
    # 台账面积 148.76 与几何 81.98 差 1.81 倍，接口给出去的必须是几何值
    assert 80 <= d["region_ha"] <= 84, (
        f"region_ha={d['region_ha']}，看着像用了客户台账的 148.76，应当用几何 81.98")
    for row, meta in d["table"].items():
        missing = [s for s in STATES if s not in meta["values"]]
        assert not missing, f"{row} 缺态 {missing}"


@pytest.mark.skipif(not list((ROOT / "out").glob("state_table_*.json")),
                    reason="还没跑出耦合四态结果")
def test_二维行带着面积和水深():
    d = _call()
    for k in ("二维·峰值淹没面积", "二维·峰值水深"):
        assert k in d["table"], f"缺 {k} —— 页面上的最大积水面积/深度卡片就是读这两行"


@pytest.mark.skipif(not list((ROOT / "out").glob("state_table_*.json")),
                    reason="还没跑出耦合四态结果")
def test_相邻gap覆盖四段():
    d = _call()
    want = {"L1->L2", "L2->L2+A", "L2+A->L2+B", "L1->L2+B"}
    for row, g in d["gaps"].items():
        assert want <= set(g), f"{row} 的 gap 不全：{sorted(g)}"


def test_两条流水线不共用产物目录():
    """三态页读 out/three_lines/，耦合块读 out/state_table_*。

    这两处一旦被改成同一个来源，页面上的「下面那张表不是同一套数」就成了谎话，
    而且没人会发现 —— 所以在这里锁死。
    """
    src = (ROOT / "swbrain" / "routes.py").read_text(encoding="utf-8")
    i = src.index('@route("/api/state_gap"')
    j = src.index("@route", i + 10)
    body = src[i:j]
    assert "state_table_" in body
    assert "three_lines" not in body.split('"""')[2] if body.count('"""') >= 3 else True
    from swbrain import threelines
    assert threelines.params({})["dir"] == "out/three_lines", "三态页的目录变了，两块的口径说明要一起改"
