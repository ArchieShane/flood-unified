# -*- coding: utf-8 -*-
"""塘的地表汇水区：产流与「来水 / 库容」（tools/pond_surface_catchments.py）。

汇水区本身要读 DTM，这里不测；测的是两件会直接进图、进报告的算术：
  · SCS 净雨要和 tools/citywide.py 的 23.53 mm 一致（同一场雨、同一个 CN）；
  · 台账没写库容（0）的塘不能算出「来水 / 库容 = 无穷大」，只给来水量。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import pond_surface_catchments as PS  # noqa: E402


def test_净雨与全市筛查一致():
    assert PS.scs_runoff_mm(61.0, 82.0) == pytest.approx(23.53, abs=0.01)


def test_雨小于初损不产流():
    assert PS.scs_runoff_mm(5.0, 82.0) == 0.0


def test_装不下时给出溢出量():
    r = PS.pond_stats(area_m2=100_000, vol_m3=1_000, q_mm=23.53)    # 来水 2,353 m³
    assert r["runoff_m3"] == 2353 and r["fill_pct"] == 235 and r["spill_m3"] == 1353 and r["idle_m3"] == 0


def test_大多闲置时给出闲置库容():
    r = PS.pond_stats(area_m2=10_000, vol_m3=10_000, q_mm=23.53)    # 来水 235 m³
    assert r["fill_pct"] == 2 and r["idle_m3"] == 9765 and r["spill_m3"] == 0


def test_没写库容只给来水():
    r = PS.pond_stats(area_m2=10_000, vol_m3=0, q_mm=23.53)
    assert "fill_pct" not in r and r["runoff_m3"] == 235
