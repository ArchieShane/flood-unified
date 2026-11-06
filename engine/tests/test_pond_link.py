# -*- coding: utf-8 -*-
"""tools/pond_link.py 的判据测试。

不连外部库、不读 25 GB DTM：用一小块人造地形把三件事钉死——
  1. 基准要逐条判，整表减 100 会把原始基准那几条毁掉；
  2. 开挖反解要真能解到目标体积，且挖不到时如实报缺口而不是硬调深度；
  3. 进水管不许设上限（管内底高过最高水位是跌水进塘，照样进得去）。
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

rasterio = pytest.importorskip("rasterio")
shapely = pytest.importorskip("shapely")

import pond_link as PL  # noqa: E402
from shapely.geometry import box  # noqa: E402


class FakeDS:
    """一块 100 × 100 格、5 m 分辨率的平地，左上角 (0, 500)，高程给定。"""

    def __init__(self, z: np.ndarray):
        from rasterio.transform import from_origin
        self.z = z
        self.transform = from_origin(0.0, 500.0, 5.0, 5.0)
        self.nodata = None
        self.height, self.width = z.shape

    def read(self, band, window=None):
        if window is None:
            return self.z
        r0 = int(round(window.row_off)); c0 = int(round(window.col_off))
        h = int(round(window.height)); w = int(round(window.width))
        r0c, c0c = max(r0, 0), max(c0, 0)
        return self.z[r0c:r0 + h, c0c:c0 + w]

    def window_transform(self, window):
        from rasterio.windows import transform as wt
        return wt(window, self.transform)


def flat(z0=10.0, n=100):
    return FakeDS(np.full((n, n), z0, dtype="float64"))


# ---------------------------------------------------------------- 基准
def test_基准逐条判_加一百那条要减_原始那条不许动():
    # PND_38 的形态：台账 125.30，DTM 实测 25.44 → 应判 +100
    v, how = PL.pick_datum(125.30, 25.44)
    assert how == "+100" and abs(v - 25.30) < 1e-6
    # PND_40 的形态：台账 31.23，DTM 31.60 → 原始基准，减了就成 −68.77
    v, how = PL.pick_datum(31.23, 31.60)
    assert how == "原始" and abs(v - 31.23) < 1e-6


def test_两种基准都对不上就不要_不许硬塞():
    v, how = PL.pick_datum(500.0, 25.0)
    assert v is None and "对不上" in how


def test_空值与零当缺失():
    assert PL.pick_datum(None, 10.0)[0] is None
    assert PL.pick_datum(0.0, 10.0)[0] is None


# ---------------------------------------------------------------- 开挖反解
def test_反解能解到目标体积():
    ds = flat(10.0)
    g = box(100.0, 100.0, 200.0, 200.0)          # 100 × 100 m = 10,000 m²
    z_top = 10.0                                  # 顶水位就在地面
    r = PL.dig_to_volume(ds, g, z_top, 20000.0)   # 要 20,000 m³ → 平地上正好 2 m
    assert r["ok"] and r["enough"]
    assert abs(r["depth_m"] - 2.0) < 0.05
    assert abs(r["volume_m3"] - 20000) / 20000 < 0.02


def test_挖到上限也不够就报缺口_不许硬调深度():
    ds = flat(10.0)
    g = box(100.0, 100.0, 150.0, 150.0)           # 50 × 50 m = 2,500 m²
    r = PL.dig_to_volume(ds, g, 10.0, 999_999.0, max_depth=6.0)
    assert r["ok"] and r["enough"] is False
    assert r["depth_m"] == pytest.approx(6.0)     # 顶到上限就停
    assert r["short_m3"] > 0
    assert r["volume_m3"] <= 2500 * 6.0 * 1.02    # 不会凭空多出库容


def test_现状已经够就不挖():
    z = np.full((100, 100), 10.0)
    z[20:40, 20:40] = 5.0                          # 已有一个 100 × 100 m、5 m 深的坑
    ds = FakeDS(z)
    g = box(100.0, 300.0, 200.0, 400.0)
    r = PL.dig_to_volume(ds, g, 10.0, 1000.0)
    assert r["ok"] and r["depth_m"] == 0.0 and r["dig_m3"] == 0
    assert r["v_now_m3"] > 1000


def test_深度越大库容单调不减():
    ds = flat(10.0)
    g = box(100.0, 100.0, 200.0, 200.0)
    vs = [PL.dig_to_volume(ds, g, 10.0, t)["volume_m3"] for t in (5000, 10000, 20000, 40000)]
    assert vs == sorted(vs)


# ---------------------------------------------------------------- 接管
def _pipes(inv_list, dia_list=None, dist_list=None):
    n = len(inv_list)
    dia_list = dia_list or [300.0] * n
    dist_list = dist_list or [5.0] * n
    return pd.DataFrame({
        "UNITID": [f"P{i}" for i in range(n)],
        "StartCode": ["a"] * n, "EndCode": ["b"] * n,
        "StartElev": [10.0] * n, "EndElev": [10.0] * n,
        "OUTFALLID": [""] * n, "STATUS": ["Existing"] * n,
        "inv_up": list(inv_list), "inv_dn": list(inv_list),
        "dia_mm": dia_list, "dist": dist_list,
    })


def test_进水管不设上限_管内底高过塘顶照样算进水():
    # 塘底 5.0、顶水位 8.0；管内底 12.0 远高于顶水位 —— 跌水进塘，必须算进水
    r = PL.choose_links(_pipes([12.0]), z_bot=5.0, z_top=8.0)
    assert r["inflow"] is not None and r["inflow"]["invert_m"] == 12.0


def test_管内底低于塘底的只能当出水():
    r = PL.choose_links(_pipes([3.0]), z_bot=5.0, z_top=8.0)
    assert r["inflow"] is None
    assert r["outflow"] is not None and r["need_pump"] is False


def test_没有更低的管就必须上泵():
    r = PL.choose_links(_pipes([9.0, 10.0]), z_bot=5.0, z_top=8.0)
    assert r["outflow"] is None and r["need_pump"] is True
    assert "重力排不掉" in r["why"]


def test_净空要留够_贴着塘底的管不算进水():
    # 净空 0.30 m：管内底 5.10 只比塘底高 0.10，算不上进水
    r = PL.choose_links(_pipes([5.10]), z_bot=5.0, z_top=8.0)
    assert r["inflow"] is None


def test_同样合格取管径最大的():
    r = PL.choose_links(_pipes([9.0, 9.0], dia_list=[300.0, 1800.0]), z_bot=5.0, z_top=8.0)
    assert r["inflow"]["dia_mm"] == 1800


def test_周边无管要说清楚():
    r = PL.choose_links(_pipes([]), z_bot=5.0, z_top=8.0)
    assert r["need_pump"] is True and r["why"] == "周边无管"
