"""客户热点 200 m 圆内的「地表峰值积水量」，以及各热点圆之间的重叠。

积水点清单里的积水量是「积水片」的量；客户热点自己没有积水面（只有一个坐标），所以拿热点为圆心、半径 200 m 的圆当作
它的积水面，圆内按水深阈值取水：

    某一帧的圆内积水量 V(t) = Σ  水深(单元, t) × 单元面积    （只加 水深 ≥ 阈值 的单元；单元形心落在圆内才算）
    热点的地表峰值积水量 = max_t V(t)

这是「圆内」的量，不是积水点的峰值积水量，两者不能混着比。

相邻热点的圆会重叠，同一个单元可能落进几个圆，所以各热点的圆内积水量**不能直接相加**：
    · 并集峰值积水量 = 把所有圆的单元合并（每个单元只算一次）后再按上式算，才是总量；
    · 每对相交的圆给出：圆心距、两圆重叠的几何面积（解析式）、重叠区内的单元数 / 面积 / 峰值积水量；
    · 每个热点给出：和哪几个热点的圆重叠、圆内被别的圆也盖住的单元面积与占比、只属于它自己的那部分峰值积水量。

形心落在圆内取单元：100 m 网格一个圆只有几十个单元，圆边上的取舍误差不小（返回里有 `cell_area_m2` 与圆的理论面积可对照）。
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

RADIUS_M = 200.0
DEPTH_LIMITS = (0.05, 3.0)
MAX_PAIRS = 2000          # 全市热点多，相交的圆对只回面积最大的前这么多对（总数照给）


def check_depth(depth: float) -> None:
    lo, hi = DEPTH_LIMITS
    if not lo <= depth <= hi:
        raise ValueError(f"depth 要在 {lo}–{hi} 之间")


def lens_area(d: float, r: float) -> float:
    """两个半径 r、圆心距 d 的圆的重叠面积（d ≥ 2r 为 0）。"""
    if d >= 2 * r:
        return 0.0
    if d <= 0:
        return math.pi * r * r
    return 2 * r * r * math.acos(d / (2 * r)) - (d / 2) * math.sqrt(4 * r * r - d * d)


def points_in_polygons(x: np.ndarray, y: np.ndarray, polys: list[np.ndarray]) -> np.ndarray:
    """点在多边形内（射线法，向量化；polys 为 UTM 坐标的 (n,2) 环列表，任一个包含即算）。"""
    inside = np.zeros(len(x), bool)
    for ring in polys:
        px, py = ring[:, 0], ring[:, 1]
        x0, x1 = px, np.roll(px, -1)
        y0, y1 = py, np.roll(py, -1)
        m = (x >= px.min()) & (x <= px.max()) & (y >= py.min()) & (y <= py.max())
        idx = np.nonzero(m)[0]
        if not len(idx):
            continue
        xs, ys = x[idx][:, None], y[idx][:, None]
        cross = ((y0 > ys) != (y1 > ys)) & (xs < (x1 - x0) * (ys - y0) / np.where(y1 == y0, 1e-12, y1 - y0) + x0)
        inside[idx] |= (cross.sum(axis=1) % 2) == 1
    return inside


def _series(F: np.ndarray, cells: np.ndarray, area: np.ndarray, depth: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """一组单元的逐帧：圆内积水量（只加 ≥ depth 的）、≥ depth 的面积、最大水深。"""
    if not len(cells):
        z = np.zeros(F.shape[0])
        return z, z, z
    Fc = F[:, cells].astype(np.float32)
    on = Fc >= depth
    a = area[cells].astype(np.float64)
    return (Fc * on * a).sum(axis=1, dtype=np.float64), (on * a).sum(axis=1, dtype=np.float64), Fc.max(axis=1)


def _peak(F, cells, area, t, depth) -> dict[str, Any]:
    v, a, h = _series(F, cells, area, depth)
    ip, ia, ih = int(v.argmax()), int(a.argmax()), int(h.argmax())
    return {
        "vol_peak_m3": int(round(float(v[ip]))), "t_peak_h": round(float(t[ip]), 2),
        "area_peak_m2": int(round(float(a[ia]))), "t_area_peak_h": round(float(t[ia]), 2),
        "hmax_m": round(float(h[ih]), 2), "t_hmax_h": round(float(t[ih]), 2),
    }


def circle_series(xy: np.ndarray, area: np.ndarray, F: np.ndarray, t: np.ndarray, center: tuple[float, float], depth: float,
                  radius: float = RADIUS_M, skip: np.ndarray | None = None) -> dict[str, Any]:
    """单个热点 200 m 圆内逐帧的变化过程：圆内积水量 V(t)（只加 ≥ depth 的单元）、≥ depth 的面积、圆内最大水深。
    选单元与算法同 `buffer_stats`，所以曲线峰值 = 清单里这个热点的「圆内峰值积水量」。"""
    d = np.hypot(xy[:, 0] - center[0], xy[:, 1] - center[1])
    cells = np.nonzero(d <= radius)[0]
    skipped = 0.0
    if skip is not None and len(cells):
        skipped = float(area[cells[skip[cells]]].sum())
        cells = cells[~skip[cells]]
    v, a, h = _series(F, cells, area, depth)
    ip = int(v.argmax())
    return {
        "t_h": [round(float(x), 4) for x in t],
        "vol_m3": [round(float(x), 1) for x in v],
        "area_m2": [round(float(x), 1) for x in a],
        "depth_m": [round(float(x), 3) for x in h],
        "vol_peak_m3": int(round(float(v[ip]))), "t_peak_h": round(float(t[ip]), 2),
        "depth_threshold_m": depth, "radius_m": radius, "n_cells": int(len(cells)),
        "cell_area_m2": int(round(float(area[cells].sum()))) if len(cells) else 0, "excluded_area_m2": int(round(skipped)),
    }


def buffer_stats(xy: np.ndarray, area: np.ndarray, F: np.ndarray, t: np.ndarray, hxy: np.ndarray, hids: list[str],
                 depth: float, radius: float = RADIUS_M, skip: np.ndarray | None = None) -> dict[str, Any]:
    """逐热点的圆内峰值积水量 + 并集 + 圆重叠。`skip` 是不参与的单元（评估剔除区内的，RD-A 才有）。"""
    from scipy.spatial import cKDTree

    n = len(hids)
    tree = cKDTree(xy)
    lists = tree.query_ball_point(hxy, radius)
    cells_of = []
    n_skipped = []
    for lst in lists:
        c = np.asarray(lst, dtype=np.int64)
        if skip is not None and len(c):
            keep = ~skip[c]
            n_skipped.append(float(area[c[~keep]].sum()))
            c = c[keep]
        else:
            n_skipped.append(0.0)
        cells_of.append(np.sort(c))

    cnt = np.zeros(len(xy), np.int32)                      # 每个单元落进几个热点圆
    for c in cells_of:
        cnt[c] += 1

    # 圆对：圆心距 < 2r 就有几何重叠
    ht = cKDTree(hxy)
    pairs = []
    nb: list[list[int]] = [[] for _ in range(n)]
    for i, j in sorted(ht.query_pairs(2 * radius)):
        d = float(np.hypot(*(hxy[i] - hxy[j])))
        la = lens_area(d, radius)
        if la <= 0:
            continue
        nb[i].append(j)
        nb[j].append(i)
        sh = np.intersect1d(cells_of[i], cells_of[j], assume_unique=True)
        pk = _peak(F, sh, area, t, depth) if len(sh) else None
        pairs.append({
            "a": hids[i], "b": hids[j], "dist_m": int(round(d)),
            "lens_area_m2": int(round(la)), "lens_pct": round(la / (math.pi * radius * radius) * 100, 1),
            "shared_cells": int(len(sh)), "shared_area_m2": int(round(float(area[sh].sum()))) if len(sh) else 0,
            "shared_vol_peak_m3": pk["vol_peak_m3"] if pk else 0, "shared_t_peak_h": pk["t_peak_h"] if pk else None,
        })
    pairs.sort(key=lambda p: -p["lens_area_m2"])

    rows = []
    for i, c in enumerate(cells_of):
        pk = _peak(F, c, area, t, depth)
        shared = c[cnt[c] > 1]
        own = c[cnt[c] == 1]
        sh_area = float(area[shared].sum())
        tot_area = float(area[c].sum())
        rows.append({
            "id": hids[i], "n_cells": int(len(c)), "cell_area_m2": int(round(tot_area)),
            "excluded_area_m2": int(round(n_skipped[i])), **pk,
            "overlap_with": [hids[j] for j in sorted(nb[i], key=lambda j: np.hypot(*(hxy[i] - hxy[j])))],
            "shared_area_m2": int(round(sh_area)), "shared_pct": round(sh_area / tot_area * 100, 1) if tot_area else 0.0,
            "vol_exclusive_peak_m3": _peak(F, own, area, t, depth)["vol_peak_m3"] if len(own) else 0,
            "vol_shared_peak_m3": _peak(F, shared, area, t, depth)["vol_peak_m3"] if len(shared) else 0,
        })

    union = np.nonzero(cnt > 0)[0]
    un = _peak(F, union, area, t, depth)
    un.update(n_cells=int(len(union)), cell_area_m2=int(round(float(area[union].sum()))))
    total = sum(r["vol_peak_m3"] for r in rows)

    def domain_peak(d: float, use_skip: bool) -> dict[str, Any]:
        """全域逐帧 Σ 水深×面积（只加 ≥ d 的单元）的最大值；逐帧算，省内存。use_skip 时扣掉 skip 里的单元。"""
        a = np.where(skip, 0.0, area) if (use_skip and skip is not None) else area
        v = np.array([float((a * np.where(F[k] >= d, F[k], 0)).sum(dtype=np.float64)) for k in range(F.shape[0])])
        ip = int(v.argmax())
        n_cells = int((a > 0).sum())
        return {"vol_peak_m3": int(round(float(v[ip]))), "t_peak_h": round(float(t[ip]), 2), "n_cells": n_cells,
                "area_m2": int(round(float(a.sum())))}

    def pct(x: float, y: float) -> float | None:
        return round(x / y * 100, 2) if y else None

    # 不设阈值的一套对齐总览的「总积水量」（逐帧 Σ水深×面积 的峰值，不扣剔除区）；设了阈值的一套才扣剔除区
    dom, dom0 = domain_peak(depth, True), domain_peak(0.0, False)
    union_raw = np.unique(np.concatenate([np.asarray(lst, dtype=np.int64) for lst in lists])) if n else np.zeros(0, np.int64)
    un0 = _peak(F, union_raw, area, t, 0.0)
    return {
        # 热点圆内（并集）占全域的比例：同阈值（分子分母都只加 ≥ depth 的单元），另给不设阈值（全部水深）的一套，与总览的「总积水量」同口径
        "domain": dom, "domain_all": dom0, "union_all": {"vol_peak_m3": un0["vol_peak_m3"], "t_peak_h": un0["t_peak_h"]},
        "union_pct": pct(un["vol_peak_m3"], dom["vol_peak_m3"]), "union_all_pct": pct(un0["vol_peak_m3"], dom0["vol_peak_m3"]),
        "depth_m": depth, "radius_m": radius, "circle_area_m2": int(round(math.pi * radius * radius)),
        "n_hotspots": n, "n_with_water": sum(r["vol_peak_m3"] > 0 for r in rows),
        "hotspots": rows,
        "union": un, "sum_vol_peak_m3": total, "double_count_m3": total - un["vol_peak_m3"],
        "n_overlap_hotspots": sum(bool(x) for x in nb), "n_pairs": len(pairs), "pairs": pairs[:MAX_PAIRS],
    }
