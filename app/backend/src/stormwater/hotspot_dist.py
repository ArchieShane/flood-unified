"""客户热点到积水片的距离：按客户文档《RD-A 模拟积水点与客户热点重合分析》的口径，量「热点到最近积水片**边界**的平面距离」
（热点落在片里为 0）。RD-A（15 m 网格）与全市（100 m 网格，`city_ponding.py`）共用这一处，两个页面的覆盖逻辑才一致。

输入是 `.sww` 的三角形顶点（N×3×2，EPSG:32640 绝对坐标）和每片的单元号；候选单元先按形心 `near_m + margin` 预筛
（三角形外接圆半径远小于 margin），再量到三角形的精确距离。
"""

from __future__ import annotations

from typing import Any

import numpy as np


def pt_tri_dist(px: float, py: float, T: np.ndarray) -> np.ndarray:
    """点到一批三角形（m×3×2）的平面距离；点落在三角形里为 0。"""
    d = np.full(len(T), np.inf)
    for i in range(3):
        a, b = T[:, i], T[:, (i + 1) % 3]
        ab = b - a
        t = np.clip(((px - a[:, 0]) * ab[:, 0] + (py - a[:, 1]) * ab[:, 1]) / np.maximum((ab ** 2).sum(1), 1e-12), 0.0, 1.0)
        d = np.minimum(d, np.hypot(px - (a[:, 0] + t * ab[:, 0]), py - (a[:, 1] + t * ab[:, 1])))
    s = []
    for i in range(3):
        a, b = T[:, i], T[:, (i + 1) % 3]
        s.append((b[:, 0] - a[:, 0]) * (py - a[:, 1]) - (b[:, 1] - a[:, 1]) * (px - a[:, 0]))
    inside = ((s[0] >= 0) & (s[1] >= 0) & (s[2] >= 0)) | ((s[0] <= 0) & (s[1] <= 0) & (s[2] <= 0))
    d[inside] = 0.0
    return d


def boundary_near(tri: np.ndarray, xy: np.ndarray, hxy: np.ndarray, hids: list[str], cells: np.ndarray, patch_of: np.ndarray,
                  near_m: float = 200.0, margin: float = 150.0) -> dict[str, Any]:
    """留下的每片里，每个热点到它的边界距离。

    `cells`：留下的片里全部单元的全局下标；`patch_of`：同长，每个单元属于哪一片（片号随意，只要同片同号）；
    `xy`：全部单元的形心（N×2）。返回：
      near      片号 → [(距离, 热点号)]，距离 ≤ near_m，未排序
      pair_min  片号 → (到最近热点的距离, 热点下标)，只含候选范围（≈ near_m + margin）内的热点
      raw       每个热点到最近一片的距离（范围内没有给 None），与 hids 同序
      bins      {"total", "0", "50", "100", "200"}：热点落在积水范围内 / 50 / 100 / 200 m 内有积水的热点个数（按原始距离数）
    """
    from scipy.spatial import cKDTree

    near: dict[int, list[tuple[float, str]]] = {}
    pair_min: dict[int, tuple[float, int]] = {}
    raw: list[float | None] = []
    if len(cells):
        ctree = cKDTree(xy[cells])
        for j, hid in enumerate(hids):
            idx = np.asarray(ctree.query_ball_point(hxy[j], near_m + margin), dtype=np.int64)
            best = None
            if len(idx):
                dd = pt_tri_dist(float(hxy[j, 0]), float(hxy[j, 1]), np.asarray(tri[cells[idx]]))
                per: dict[int, float] = {}
                for p_, d_ in zip(patch_of[idx].tolist(), dd.tolist()):
                    if d_ < per.get(p_, 1e18):
                        per[p_] = d_
                for p_, d_ in per.items():
                    if d_ <= near_m:
                        near.setdefault(p_, []).append((d_, hid))
                    if d_ < pair_min.get(p_, (1e18, 0))[0]:
                        pair_min[p_] = (d_, j)
                best = min(per.values())
            raw.append(best)
    else:
        raw = [None] * len(hids)
    bins = {"total": len(hids), **{str(k): sum(1 for d_ in raw if d_ is not None and d_ <= k + 1e-9) for k in (0, 50, 100, 200)}}
    return {"near": near, "pair_min": pair_min, "raw": raw, "bins": bins}
