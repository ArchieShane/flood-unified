# -*- coding: utf-8 -*-
"""塘 × 汇水区的关系分类（tools/pond_catchment.py · relate）。

分四档：in（占地 ≥ 50% 在区内）/ part（部分在区内）/ edge（界外 ≤ 200 m）/ out。
守两条真踩过或容易踩的：
  · 「相交」不等于「在区内」——只擦到边的塘要按占地比例算，不能一相交就判 in；
  · 离得近不等于在区内——贴边的单列 edge，不混进 in/part。
几何是构造的，不读数据。
"""
import sys
from pathlib import Path

from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import pond_catchment as PC  # noqa: E402

CATCH = [("A", box(0, 0, 1000, 1000))]
MODEL = [("M1", box(0, 0, 500, 500))]


def _rel(g):
    return PC.relate([("p", g)], CATCH, MODEL)[0]


def test_整座在区内():
    r = _rel(box(100, 100, 200, 200))
    assert r["catch_rel"] == "in" and r["catch_in"] == [{"label": "A", "pct": 100}]


def test_只擦到边按占地比例算():
    r = _rel(box(900, 100, 1300, 200))          # 100/400 = 25% 在区内
    assert r["catch_rel"] == "part" and r["catch_in"][0]["pct"] == 25


def test_贴边单列不算在区内():
    r = _rel(box(1100, 100, 1200, 200))         # 界外 100 m
    assert r["catch_rel"] == "edge" and r["catch_in"] == [] and r["catch_near"]["dist_m"] == 100


def test_远离就是不在():
    r = _rel(box(3000, 100, 3100, 200))
    assert r["catch_rel"] == "out" and r["catch_near"] == {"label": "A", "dist_m": 2000}


def test_模型区关系一起给():
    assert _rel(box(100, 100, 200, 200))["in_model_area"] is True
    r = _rel(box(700, 100, 800, 200))
    assert r["in_model_area"] is False and r["dist_to_model_m"] == 200.0 and r["nearest_model"] == "M1"
