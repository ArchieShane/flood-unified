# -*- coding: utf-8 -*-
"""可挖塘的地：土地利用约束。

用户那句「你现在有的把高速公路都挖了」指的就是这里。守三个真踩过的坑：

1. 建筑曾经只按**外环第一个顶点**打一个 25 m 的圆（注释写的是「质心 + 面积反推半径」，
   代码没做到）。Mussafah 是工业区，一座仓库上百米长，角上那个圆盖不住——
   独立查 bas_building，NP1 压掉 333 m²，占它自己面积的 26%。

2. fit_square 的腐蚀核取了偶数，scipy 的偶数核原点不居中：返回的中心到右/下少半个格，
   塘的方形轮廓探出空地边界（36 m 的塘实测探出 3 m，正好够压到旁边的楼）。

3. 兜底掩膜只看得见建筑和路，看不见权属：塘落在台账标 Commercial 的地块上，
   地上确实空着，地却不是能挖的。

这三条都不碰数据库，几何是构造的。
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from swbrain import landuse as LU

LON0, LAT0 = 54.50, 24.40
M_PER_DEG_LON = 101_300.0          # 24.4°N
M_PER_DEG_LAT = 111_000.0


@pytest.fixture
def site(tmp_path, monkeypatch):
    """一个 400 × 400 m 的空场地，栅格 10 m。路网文件不存在（ROOT 指到临时目录）。"""
    from pyproj import Transformer
    monkeypatch.setattr(LU, "ROOT", tmp_path)          # 免得读到仓库里真的 40 万条路
    d = tmp_path / "lu"
    d.mkdir()
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    cx, cy = tr.transform(LON0, LAT0)
    cfg = {"landuse": {"dir": str(d)}}
    bbox = (cx - 200, cy - 200, cx + 200, cy + 200)
    return cfg, d, bbox, (cx, cy)


def _ring_lonlat(dx_m, dy_m, w_m, h_m):
    """以 (LON0, LAT0) 为原点、偏移 (dx,dy) 米处的一个矩形环（经纬度）。"""
    x0 = LON0 + (dx_m - w_m / 2) / M_PER_DEG_LON
    x1 = LON0 + (dx_m + w_m / 2) / M_PER_DEG_LON
    y0 = LAT0 + (dy_m - h_m / 2) / M_PER_DEG_LAT
    y1 = LAT0 + (dy_m + h_m / 2) / M_PER_DEG_LAT
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def _write(f, feats):
    f.write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                            ensure_ascii=False), encoding="utf-8")


def test_长条建筑整体排除_不是只排一个角(site):
    """160 × 20 m 的仓库：退让 25 m 之后该挡掉 200 格以上，不是一个角上 20 格的圆。"""
    cfg, d, bbox, _ = site
    _write(d / "X.buildings.geojson",
           [{"type": "Feature", "properties": {},
             "geometry": {"type": "Polygon", "coordinates": [_ring_lonlat(0, 0, 160, 20)]}}])
    free, tf = LU.open_land_mask(cfg, "X", bbox, res=10.0, log=lambda *a: None)
    blocked = int((~free).sum())
    # 真轮廓 + 25 m 退让 ≈ (160+50) × (20+50) = 14,700 m² = 147 格，圆角再少些
    assert blocked > 100, f"只挡了 {blocked} 格——像是只按一个顶点打了圆"
    # 仓库正中间必须是不可用的
    x0, y1, res = tf
    cx_utm = (bbox[0] + bbox[2]) / 2
    cy_utm = (bbox[1] + bbox[3]) / 2
    r = int((y1 - cy_utm) / res); c = int((cx_utm - x0) / res)
    assert not free[r, c], "仓库正中间被判成了空地"


def test_台账标了不可挖的地块要排掉(site):
    """兜底掩膜看不见权属：明确标 Commercial 的地块得按地块边界排掉。"""
    cfg, d, bbox, (cx, cy) = site
    ring = [[cx - 50, cy - 50], [cx + 50, cy - 50], [cx + 50, cy + 50],
            [cx - 50, cy + 50], [cx - 50, cy - 50]]
    _write(d / "X.blocked.geojson",                     # 这份导出就是 UTM 32640
           [{"type": "Feature", "properties": {"use": "Commercial"},
             "geometry": {"type": "Polygon", "coordinates": [ring]}}])
    free, tf = LU.open_land_mask(cfg, "X", bbox, res=10.0, log=lambda *a: None)
    x0, y1, res = tf
    assert not free[int((y1 - cy) / res), int((cx - x0) / res)], "禁挖地块中心仍被判成空地"
    assert (~free).sum() >= 90, "只排掉了地块的一部分"        # 100 × 100 m = 100 格


def test_空场地就是全空(site):
    """没有建筑、没有路、没有禁挖地块时，整片都算候选空地——免得判据写反了还看不出来。"""
    cfg, _, bbox, _ = site
    free, _ = LU.open_land_mask(cfg, "X", bbox, res=10.0, log=lambda *a: None)
    assert free.all()


def test_方形不能探出空地边界():
    """腐蚀核必须取奇数：偶数核原点不居中，塘的轮廓会探出空地。

    空地是一条 50 m 宽（5 格）的竖带，要挖一个 40 m 见方的塘。
    查询点故意放在带子右缘外——偶数核会挑到偏右的那一格，塘的右边探出 5 m。
    """
    free = np.zeros((9, 9), bool)
    free[2:7, 2:7] = True                               # x 从 20 到 70 m
    tf = (0.0, 90.0, 10.0)                              # x0=0, y1=90, res=10
    s = LU.fit_square(free, tf, 70.0, 45.0, 1600.0)     # 需要 40 × 40 m
    assert s is not None
    half = s["side_m"] / 2
    assert s["x"] - half >= 20.0 - 1e-6 and s["x"] + half <= 70.0 + 1e-6, \
        f"塘从 x={s['x'] - half:.0f} 到 {s['x'] + half:.0f}，探出了 20–70 m 的空地"
    assert s["y"] - half >= 20.0 - 1e-6 and s["y"] + half <= 70.0 + 1e-6


def test_放不下就如实返回None():
    """装不下就说装不下，不要硬塞一个挖不了的塘进模型。"""
    free = np.zeros((9, 9), bool)
    free[4:6, 4:6] = True                               # 只有 20 × 20 m
    assert LU.fit_square(free, (0.0, 90.0, 10.0), 45.0, 45.0, 10000.0) is None


def test_太远也不给():
    """空地在 max_r_m 之外就不给——塘挪出汇水区就不是原来那个方案了。"""
    free = np.zeros((60, 60), bool)
    free[55:60, 55:60] = True
    assert LU.fit_square(free, (0.0, 600.0, 10.0), 5.0, 595.0, 1600.0, max_r_m=100.0) is None
