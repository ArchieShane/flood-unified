# -*- coding: utf-8 -*-
"""箱涵落位的严格判据（tools/build_l2p.py · box_sites_strict）。

用户问「这些箱涵是不是要挖开高速公路」，查实（2026-09-26）：六座都没压到路面，
但 NP3/NP4 整座落在快速路红线里，NP1/NP2/NP5 压在已分配的工业地块上。
原因是兜底掩膜只认路网中心线 + 30 m，看不见路面、红线和地块。

这里守四条：
  1. 有首选空地就用首选空地，不进红线；
  2. 首选空地没有，才退到红线，并标成 road_reserve；
  3. 两座箱涵不能叠在一起；
  4. 什么地都没有就如实剔掉，不硬塞。

不碰数据库：strict_land_masks 被换成构造的栅格。
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import build_l2p as B  # noqa: E402

X0, Y1, RES = 250_000.0, 2_700_000.0, 5.0
H = W = 200                                   # 1 km × 1 km


def _fake(free1, free2):
    def f(cfg, bbox, log=print):
        return free1.copy(), free2.copy(), (X0, Y1, RES), {}
    return f


def _site(x, y, vol):
    return {"x": x, "y": y, "vol_m3": vol}


def _xy(r, c):
    return X0 + (c + .5) * RES, Y1 - (r + .5) * RES


def test_有首选空地就不进红线(monkeypatch):
    free1 = np.zeros((H, W), bool); free1[20:60, 20:60] = True       # 远一点的空地
    free2 = free1.copy(); free2[95:105, :] = True                     # 贴着站点的一条红线
    monkeypatch.setattr(B.LU, "strict_land_masks", _fake(free1, free2))
    x, y = _xy(100, 100)
    ok, dropped, _ = B.box_sites_strict({}, "T", [_site(x, y, 400.0)], log=lambda *a: None)
    assert not dropped and ok[0]["land"] == "open_land"


def test_没有首选空地才退到红线并标出来(monkeypatch):
    free1 = np.zeros((H, W), bool)
    free2 = np.zeros((H, W), bool); free2[90:110, 90:110] = True
    monkeypatch.setattr(B.LU, "strict_land_masks", _fake(free1, free2))
    x, y = _xy(100, 100)
    ok, _, _ = B.box_sites_strict({}, "T", [_site(x, y, 400.0)], log=lambda *a: None)
    assert ok[0]["land"] == "road_reserve"


def test_两座箱涵不叠在一起(monkeypatch):
    free1 = np.zeros((H, W), bool); free1[80:120, 80:160] = True      # 40 × 80 格，够放两座
    monkeypatch.setattr(B.LU, "strict_land_masks", _fake(free1, free1.copy()))
    x, y = _xy(100, 100)
    ok, dropped, _ = B.box_sites_strict({}, "T", [_site(x, y, 3000.0), _site(x, y, 3000.0)],
                                        log=lambda *a: None)
    assert len(ok) == 2 and not dropped
    side = np.sqrt(3000.0 / 2.0)
    gap = max(abs(ok[0]["x"] - ok[1]["x"]), abs(ok[0]["y"] - ok[1]["y"]))
    assert gap >= side + B.BOX_GAP_M - RES          # 中心距 ≥ 边长 + 净距（容一个格的量化）


def test_没有地就剔掉不硬塞(monkeypatch):
    none = np.zeros((H, W), bool)
    monkeypatch.setattr(B.LU, "strict_land_masks", _fake(none, none))
    x, y = _xy(100, 100)
    ok, dropped, _ = B.box_sites_strict({}, "T", [_site(x, y, 400.0)], log=lambda *a: None)
    assert not ok and dropped and "没有" in dropped[0]["skip"]


def test_放宽口径只在前两档都没地时才借未盖楼地块(monkeypatch):
    """29.RB2 严格口径只放得下 24%，放宽版借用未盖楼的已分配地块——但只作兜底，并标成 vacant_plot。"""
    free1 = np.zeros((H, W), bool); free1[10:40, 10:40] = True        # 首选空地只够一座
    free2 = free1.copy()
    free3 = free2.copy(); free3[90:130, 90:130] = True                # 站点边上一块没盖楼的地块

    def f(cfg, bbox, log=print, vacant=False):
        assert vacant
        return free1.copy(), free2.copy(), free3.copy(), (X0, Y1, RES), {}
    monkeypatch.setattr(B.LU, "strict_land_masks", f)
    monkeypatch.setattr(B, "BOX_VACANT", True)
    a, b = _xy(25, 25), _xy(110, 110)
    ok, dropped, _ = B.box_sites_strict({}, "T", [_site(*a, 400.0), _site(*b, 400.0)],
                                        log=lambda *a: None)
    assert not dropped
    assert [s["land"] for s in ok] == ["open_land", "vacant_plot"]
