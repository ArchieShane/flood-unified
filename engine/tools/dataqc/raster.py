# -*- coding: utf-8 -*-
"""DTM（地面高程栅格）能不能拿去做二维水动力模型的体检。只读，按原始分辨率分块读。

移植自 m4-data 栅格体检.java（R1～R7，看元数据）与 栅格分块统计.java（R8～R12，逐格），以及当时的 DTM分析.py / DTM低值定位.py。
判定范围三选一（可叠加）：
  - 管网外框：管线范围外扩 100 m（报告原口径）；
  - 计算域：任意面（GeoJSON / GPKG / shp），只统计面内的格；
  - 全图。
管线足迹（R8）＝ 管线烧成栅格后按曼哈顿距离外扩 50 m；海与界外本来就是无数据，按外框算会把海报成洞，所以 R8 只看足迹。
"""
from __future__ import annotations

import math
import os
import re

import numpy as np
import rasterio
from rasterio import features
from rasterio.windows import Window
from scipy import ndimage

from .thresholds import Thresholds

# rasterio 要用自己带的 proj.db：机器上 PostgreSQL / PostGIS 的旧 proj.db 会先被找到，认不出 EPSG 号（与 tools/c1_post.py 同一处理）
_pd = os.path.join(os.path.dirname(rasterio.__file__), "proj_data")
if os.path.isdir(_pd):
    os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = _pd

# 直方图：−100～1000 m，1 cm 一格，用来出分位数；超出的单独计数（最值另记）
_H0, _H1, _HW = -100.0, 1000.0, 0.01
_NB = int(round((_H1 - _H0) / _HW))
# 坡度直方图：0～200 %，0.1 % 一格
_SNB, _SW = 2000, 0.1


def is_nodata(z, nd):
    m = ~np.isfinite(z) | (z < -1e30)
    if nd is not None and not (isinstance(nd, float) and math.isnan(nd)):
        m |= z == np.float32(nd)
    return m


def sample_points(ds, xs, ys):
    """每个点的值（海拔）与「无数据或范围外」标记。"""
    L, B, R, T = ds.bounds
    outside = ~((xs >= L) & (xs <= R) & (ys >= B) & (ys <= T))
    v = np.full(len(xs), np.nan)
    idx = np.nonzero(~outside)[0]
    if len(idx):
        s = np.array([a[0] for a in ds.sample(zip(xs[idx], ys[idx]), indexes=1)], dtype=float)
        s[is_nodata(s.astype(np.float32), ds.nodata)] = np.nan
        v[idx] = s
    return v, np.isnan(v)


def metadata(path: str) -> dict:
    """R1～R7 看的事实。高程基准：CRS 里有没有垂直分量，附带的 .xml／.aux.xml 里有没有写基准。"""
    with rasterio.open(path) as ds:
        wkt = ds.crs.to_wkt() if ds.crs else ""
        m = {"path": path, "name": os.path.basename(path), "bytes": os.path.getsize(path), "width": ds.width, "height": ds.height,
             "res": [abs(ds.transform.a), abs(ds.transform.e)], "rotation": [ds.transform.b, ds.transform.d],
             "crs": ds.crs.to_string() if ds.crs else None,
             "crsName": (re.search(r'^\w+\["([^"]+)"', wkt) or [None, None])[1] if wkt else None,
             "epsg": ds.crs.to_epsg() if ds.crs else None, "dtype": ds.dtypes[0], "nodata": ds.nodata, "bounds": list(ds.bounds),
             "block": list(ds.block_shapes[0]), "overviews": ds.overviews(1), "tags": ds.tags(1),
             "verticalInCrs": ("VERT_CS" in wkt) or ("VERTCRS" in wkt)}
    sidecar_list = []
    for ext in (".xml", ".aux.xml"):
        p = path + ext
        if os.path.exists(p):
            t = open(p, encoding="utf-8", errors="ignore").read()
            # 只认垂直基准的说法。⚠️ 水平坐标系里的 DATUM["D_WGS_1984"]、ELLIPSOID 不算——那是平面基准
            t2 = re.sub(r'(?is)(PROJ|GEOG|BASEGEOG)CRS\[.*', "", t) if "VERTCRS" not in t and "VERT_CS" not in t else t
            kw = sorted(set(k.lower() for k in re.findall(
                r"(?i)VERTCRS|VERT_CS|vertical[ _]?datum|geoid\w*|egm\s?\d+|\bmsl\b|mean sea level|\bNAVD\w*|\bDMVD\w*|"
                r"\bADVD\w*|orthometric|高程基准|垂直基准", t2)))
            sidecar_list.append({"file": os.path.basename(p), "keywords": kw})
    m["sidecars"] = sidecar_list
    return m


def align_window(ds, bounds):
    """把坐标范围对齐到像元，裁到栅格内。返回 (c0, r0, W, H)。"""
    L, B, R, T = ds.bounds
    xmin, ymin, xmax, ymax = max(bounds[0], L), max(bounds[1], B), min(bounds[2], R), min(bounds[3], T)
    w = rasterio.windows.from_bounds(xmin, ymin, xmax, ymax, transform=ds.transform).round_offsets().round_lengths()
    c0, r0 = max(int(w.col_off), 0), max(int(w.row_off), 0)
    W, H = min(int(w.width), ds.width - c0), min(int(w.height), ds.height - r0)
    return c0, r0, W, H


class _UnionFind:
    """跨块连通：每片记格数、最低点（值, x, y）、外包框。"""

    def __init__(self):
        self.parent, self.cellcount, self.low, self.regionbox = {}, {}, {}, {}

    def add(self, k, n, low, box):
        self.parent[k], self.cellcount[k], self.low[k], self.regionbox[k] = k, n, low, box

    def find(self, a):
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a == b:
            return
        self.parent[b] = a
        self.cellcount[a] += self.cellcount[b]
        if self.low[b][0] < self.low[a][0]:
            self.low[a] = self.low[b]
        x, y = self.regionbox[a], self.regionbox[b]
        self.regionbox[a] = [min(x[0], y[0]), min(x[1], y[1]), max(x[2], y[2]), max(x[3], y[3])]

    def all_regions(self):
        return [(self.cellcount[k], self.low[k], self.regionbox[k]) for k in self.parent if self.parent[k] == k]


def _label_into(mask, z, tf, uf, prev_row, next_id, track_min):
    """给核心区掩码打 4 邻接标号、并入全局并查集；与上一块最后一行逐列相连。返回本块最后一行的全局标号。"""
    W = mask.shape[1]
    lab, n = ndimage.label(mask)
    if n == 0:
        return np.zeros(W, dtype=np.int64)
    idx = np.arange(1, n + 1)
    base = next_id[0]
    next_id[0] += n
    cnt = ndimage.sum_labels(np.ones(lab.shape, dtype=np.int64), lab, idx)
    objs = ndimage.find_objects(lab)
    if track_min:
        zz = np.where(mask, z, np.inf)
        pos = ndimage.minimum_position(zz, lab, idx)
        vals = ndimage.minimum(zz, lab, idx)
    for ref in range(n):
        sl = objs[ref]
        xa, yb = rasterio.transform.xy(tf, sl[0].stop - 1, sl[1].start)
        xb, ya = rasterio.transform.xy(tf, sl[0].start, sl[1].stop - 1)
        low = (math.inf, None, None)
        if track_min:
            x, y = rasterio.transform.xy(tf, *pos[ref])
            low = (float(vals[ref]), x, y)
        uf.add(base + ref, int(cnt[ref]), low, [xa, yb, xb, ya])
    g = np.where(lab > 0, lab + base - 1, 0)
    if prev_row is not None:
        m = (prev_row > 0) & (g[0] > 0)
        for a, b in set(zip(prev_row[m].tolist(), g[0][m].tolist())):
            uf.union(a, b)
    return g[-1]


def _collect(rows, mask, tfc, value, sign, limit=20, pts=None):
    if not mask.any():
        return
    rs, cs = np.nonzero(mask)
    for q in range(min(5, len(rs))):
        if len(rows) >= limit:
            return
        x, y = rasterio.transform.xy(tfc, rs[q], cs[q])
        dia = float(value[rs[q], cs[q]])
        rows.append("%.0f, %.0f（%s%.1f m）" % (x, y, sign, dia))
        if pts is not None:
            pts.append((x, y, dia))


def block_stats(ds, win, thr: Thresholds, domain=None, lines=None, block_rows=2048, log=print) -> dict:
    """窗 = (c0, r0, W, H)。域：面几何列表（与 DTM 同坐标系），None ＝ 整个窗口。线：管线几何列表，None ＝ 不做 R8。"""
    from shapely.geometry import box
    from shapely.strtree import STRtree

    c0, r0, W, H = win
    nd = ds.nodata
    res = abs(ds.transform.a)
    buffer_cells = int(round(thr.footprint_buffer / res))
    halo = max(1, buffer_cells)
    line_tree = STRtree(lines) if lines is not None and len(lines) else None
    st = dict(cells=0, valid=0, nodata=0, below0=0, outOfRange=0, judged=0, spikes=0, pits=0, flat=0, cliff=0, steep=0,
             footprint=0, footprintNoData=0)
    s1 = s2 = 0.0
    hist = np.zeros(_NB, dtype=np.int64)
    below_lo = above_hi = 0
    shist = np.zeros(_SNB + 1, dtype=np.int64)
    lowest, highest = (math.inf, None, None), (-math.inf, None, None)
    samples = {"spikes": [], "pits": [], "cliff": [], "holes": []}
    spike_pts, pit_pts = [], []
    low_uf, hole_uf = _UnionFind(), _UnionFind()
    prev_low = prev_hole = None
    next_id = [1]
    for rr in range(0, H, block_rows):
        a, b = max(rr - halo, 0), min(rr + block_rows + halo, H)
        w = Window(c0, r0 + a, W, b - a)
        z = ds.read(1, window=w).astype(np.float32)
        tf = ds.window_transform(w)
        bad = is_nodata(z, nd)
        core = slice(rr - a, rr - a + min(block_rows, H - rr))
        inside = features.geometry_mask(domain, out_shape=z.shape, transform=tf, invert=True) if domain is not None else np.ones(z.shape, bool)
        zc, bad_c, inside_c = z[core], bad[core], inside[core]
        tfc = ds.window_transform(Window(c0, r0 + rr, W, zc.shape[0]))
        good_c = inside_c & ~bad_c
        st["cells"] += int(inside_c.sum())
        st["valid"] += int(good_c.sum())
        st["nodata"] += int((inside_c & bad_c).sum())
        if good_c.any():
            v = zc[good_c].astype(np.float64)
            s1 += v.sum()
            s2 += (v * v).sum()
            k = np.floor((v - _H0) / _HW).astype(np.int64)
            below_lo += int((k < 0).sum())
            above_hi += int((k >= _NB).sum())
            hist += np.bincount(k[(k >= 0) & (k < _NB)], minlength=_NB)
            st["below0"] += int((v < 0).sum())
            st["outOfRange"] += int(((v < thr.elev_min) | (v > thr.elev_max)).sum())
            for pick, cur, ratio in ((np.argmin, lowest, lambda u, t: u < t), (np.argmax, highest, lambda u, t: u > t)):
                zz = np.where(good_c, zc, np.inf if pick is np.argmin else -np.inf)
                ref = int(pick(zz))
                if ratio(float(zz.flat[ref]), cur[0]):
                    x, y = rasterio.transform.xy(tfc, *np.unravel_index(ref, zz.shape))
                    if pick is np.argmin:
                        lowest = (float(zz.flat[ref]), x, y)
                    else:
                        highest = (float(zz.flat[ref]), x, y)
        prev_low = _label_into(good_c & (zc < thr.low_threshold), zc, tfc, low_uf, prev_low, next_id, True)
        prev_hole = _label_into(inside_c & bad_c, zc, tfc, hole_uf, prev_hole, next_id, False)
        if line_tree is not None:
            ii = line_tree.query(box(*rasterio.windows.bounds(w, ds.transform)).buffer(thr.footprint_buffer + 2 * res))
            if len(ii):
                m = features.rasterize(((lines[ref], 1) for ref in ii), out_shape=z.shape, transform=tf, all_touched=True, dtype="uint8")
                m = ndimage.binary_dilation(m.astype(bool), iterations=buffer_cells)[core] & inside_c   # 十字结构元 ＝ 曼哈顿距离外扩
                st["footprint"] += int(m.sum())
                h = m & bad_c
                st["footprintNoData"] += int(h.sum())
                if h.any() and len(samples["holes"]) < 20:
                    rs, cs = np.nonzero(h)
                    for q in range(0, len(rs), max(1, len(rs) // 3))[:3]:
                        x, y = rasterio.transform.xy(tfc, rs[q], cs[q])
                        samples["holes"].append("%.0f, %.0f" % (x, y))
        # 尖峰／深坑／平地／陡坎／坡度：8 邻格都有效才判；窗口四边不判
        zn = np.where(bad, np.nan, z)
        p = np.pad(zn, 1, constant_values=np.nan)
        nb = np.stack([p[1 + dr:1 + dr + z.shape[0], 1 + dc:1 + dc + z.shape[1]]
                       for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (dr, dc) != (0, 0)])
        full8 = ~np.isnan(nb).any(axis=0) & ~bad
        full8[:, 0] = full8[:, -1] = False
        if a == 0:
            full8[0, :] = False
        if b == H:
            full8[-1, :] = False
        with np.errstate(invalid="ignore"):
            nb_max = np.where(full8, nb.max(axis=0), np.nan)[core]
            nb_min = np.where(full8, nb.min(axis=0), np.nan)[core]
            dzdx = ((p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])) / (8 * res)
            dzdy = ((p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])) / (8 * res)
            slope = (np.hypot(dzdx, dzdy) * 100)[core]          # Horn 法，百分比
        del nb
        g = full8[core] & inside_c
        st["judged"] += int(g.sum())
        up_diff, dn_diff = zc - nb_max, nb_min - zc
        sp = g & (up_diff > thr.spike_diff)
        pt = g & ~sp & (dn_diff > thr.spike_diff)
        relief = np.maximum(nb_max - zc, zc - nb_min)
        fl = g & (relief < thr.flat_diff)
        cl = g & (relief > 2 * thr.spike_diff)          # 与某个邻格差 4 m 以上：挡墙、桥、残留建筑或数据台阶
        st["spikes"] += int(sp.sum())
        st["pits"] += int(pt.sum())
        st["flat"] += int(fl.sum())
        st["cliff"] += int(cl.sum())
        sg = slope[g]
        st["steep"] += int((sg > 30).sum())
        shist += np.bincount(np.minimum((sg / _SW).astype(np.int64), _SNB), minlength=_SNB + 1)
        _collect(samples["spikes"], sp, tfc, up_diff, "+", pts=spike_pts)
        _collect(samples["pits"], pt, tfc, dn_diff, "−", pts=pit_pts)
        _collect(samples["cliff"], cl, tfc, relief, "±")
        log("    DTM %d / %d 行：有效 %s 格，尖峰 %d，深坑 %d" % (min(rr + block_rows, H), H, format(st["valid"], ","), st["spikes"], st["pits"]))
    # 分位数（1 cm 精度）
    n = st["valid"]
    quant = {}
    if n:
        cum = np.cumsum(hist)
        for q in (0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999):
            t = q * n - below_lo
            quant["p%g" % (q * 100)] = None if t <= 0 or t > cum[-1] else round(_H0 + (np.searchsorted(cum, t) + 0.5) * _HW, 2)
    scum = np.cumsum(shist)
    slope_quant = {("p%g" % (q * 100)): round((np.searchsorted(scum, q * scum[-1]) + 0.5) * _SW, 1) for q in (0.5, 0.9, 0.99)} if scum[-1] else {}
    lows = sorted(({"cells": counter, "areaHa": round(counter * res * res / 1e4, 2), "min": round(l[0], 2), "x": round(l[1]), "y": round(l[2]),
                  "bbox": [round(v) for v in bx]} for counter, l, bx in low_uf.all_regions()), key=lambda dia: dia["min"])
    holes = sorted(({"cells": counter, "areaHa": round(counter * res * res / 1e4, 2), "bbox": [round(v) for v in bx]} for counter, _, bx in hole_uf.all_regions()),
                key=lambda dia: -dia["cells"])
    # 高程分布（报告直方图）：按 1 m 分箱，范围取 p0.1～p99.9
    lo = math.floor(quant.get("p0.1") or lowest[0]) if n else 0
    hi = math.ceil(quant.get("p99.9") or highest[0]) if n else 0
    bins = []
    if n and hi > lo:
        step = max(1, math.ceil((hi - lo) / 24))
        for e in range(lo, hi, step):
            i0, i1 = int((e - _H0) / _HW), int((e + step - _H0) / _HW)
            bins.append([e, e + step, int(hist[max(i0, 0):max(min(i1, _NB), 0)].sum())])
    st.update(min=lowest[0] if n else None, minXY=lowest[1:] if n else None, max=highest[0] if n else None, maxXY=highest[1:] if n else None,
             mean=s1 / n if n else None, std=math.sqrt(max(s2 / n - (s1 / n) ** 2, 0)) if n else None, quantiles=quant,
             slopeQuantiles=slope_quant, lows=lows, holes=holes, samples=samples, spikePts=spike_pts, pitPts=pit_pts, elevBins=bins,
             window=[c0, r0, W, H], extent=list(rasterio.windows.bounds(Window(c0, r0, W, H), ds.transform)), res=res,
             buffer=thr.footprint_buffer)
    return st


def scan(path: str, thr: Thresholds, bounds=None, domain=None, lines=None, log=print) -> dict:
    """一张 DTM 的完整检查。

    范围：(xmin, ymin, xmax, ymax)，DTM 坐标系；None 时取域的外包框，再没有就是全图。
    域：面几何列表（DTM 坐标系），只统计面内的格。线：管线几何（R8 足迹用），可为 None。
    """
    meta = metadata(path)
    with rasterio.open(path) as ds:
        if bounds is None and domain is not None:
            import shapely
            bounds = tuple(shapely.total_bounds(np.asarray(domain, dtype=object)))
        if bounds is None:
            bounds = tuple(ds.bounds)
        win = align_window(ds, bounds)
        log("  DTM 窗口 %d × %d = %s 格（%s）" % (win[2], win[3], format(win[2] * win[3], ","), meta["name"]))
        st = block_stats(ds, win, thr, domain=domain, lines=lines, log=log)
    st["meta"] = meta
    st["checks"] = verdict_rows(meta, st, thr, has_lines=lines is not None)
    return st


def verdict_rows(meta, st, thr, has_lines) -> list[dict]:
    """R1～R12 的判定。sev：ok 通过／info 提示／warn 需处理／crit 影响建模。"""
    f = lambda n: format(int(n), ",")
    p = lambda a, b: ("%.1f%%" % (100 * a / b)) if b else "—"
    sq = abs(meta["res"][0] - meta["res"][1]) < 1e-6 and not any(meta["rotation"])
    vert = meta["verticalInCrs"] or any(s["keywords"] for s in meta["sidecars"])
    R = []
    R.append(dict(code="R1", value=("%s（%s）" % (meta["crsName"], meta["crs"])) if meta["crs"] else "未声明",
                  sev="ok" if meta["crs"] else "crit",
                  note="EPSG:%s" % meta["epsg"] if meta["epsg"] else "坐标系没有 EPSG 号，叠加前要确认"))
    R.append(dict(code="R2", value="已声明" if vert else "未声明", sev="ok" if vert else "warn",
                  note=("附带文件里出现：" + "、".join(k for s in meta["sidecars"] for k in s["keywords"])) if vert else
                  "文件与附带的元数据都没有声明高程基准，也没有采集时间与精度说明；与管线台账的基准关系只能由比对得出"))
    R.append(dict(code="R3", value="%g m × %g m%s" % (meta["res"][0], meta["res"][1], "，无旋转" if not any(meta["rotation"]) else "，有旋转"),
                  sev="ok" if sq else "warn", note="全图 %s × %s 格，%s" % (f(meta["width"]), f(meta["height"]), meta["dtype"])))
    R.append(dict(code="R4", value="已声明（%s）" % ("%.4g" % meta["nodata"] if meta["nodata"] is not None else "") if meta["nodata"] is not None else "未声明",
                  sev="ok" if meta["nodata"] is not None else "warn", note="海域与界外应为 NoData"))
    R.append(dict(code="R5", value="%s（%s / %s 格）" % (p(st["valid"], st["cells"]), f(st["valid"]), f(st["cells"])),
                  sev="ok" if st["cells"] and st["valid"] == st["cells"] else "info",
                  note="统计范围内无数据 %s 格" % f(st["nodata"])))
    R.append(dict(code="R6", value=("%.2f ～ %.2f m" % (st["min"], st["max"])) if st["valid"] else "—",
                  sev="warn" if st["outOfRange"] else "info",
                  note="低于 %g m 或高于 %g m 的格 %s 个；低于 0 m 的格 %s 个（约 %.2f km²）" % (
                      thr.elev_min, thr.elev_max, f(st["outOfRange"]), f(st["below0"]), st["below0"] * st["res"] ** 2 / 1e6)))
    R.append(dict(code="R7", value="%d 级" % len(meta["overviews"]), sev="ok" if meta["overviews"] else "info",
                  note="有金字塔才能快速浏览与重采样"))
    if has_lines:
        R.append(dict(code="R8", value="%s / %s" % (f(st["footprintNoData"]), f(st["footprint"])),
                      sev="ok" if st["footprintNoData"] == 0 else "warn",
                      note=("管线两侧 %g m 以内 DTM 完整" % thr.footprint_buffer) if st["footprintNoData"] == 0 else "位置 " + "；".join(st["samples"]["holes"][:5])))
    else:
        hole = st["holes"]
        R.append(dict(code="R8", value="%d 片 / %s 格" % (len(hole), f(st["nodata"])), sev="ok" if not hole else "warn",
                      note="没有管线时按计算域统计：域内的无数据格连成片报出" if not hole else
                      "最大一片 %s 格（%.2f ha），外包框 %s" % (f(hole[0]["cells"]), hole[0]["areaHa"], hole[0]["bbox"])))
    R.append(dict(code="R9", value="%s / %s" % (f(st["spikes"]), f(st["judged"])), sev="ok" if st["spikes"] == 0 else "warn",
                  note="；".join(st["samples"]["spikes"][:5])))
    R.append(dict(code="R10", value="%s / %s" % (f(st["pits"]), f(st["judged"])), sev="ok" if st["pits"] == 0 else "warn",
                  note="；".join(st["samples"]["pits"][:5])))
    R.append(dict(code="R11", value="%s（%s）" % (f(st["flat"]), p(st["flat"], st["judged"])), sev="info",
                  note="二维模型在大片平地上流向对微小高差敏感"))
    R.append(dict(code="R12", value="%d 处" % len(st["lows"]), sev="ok" if not st["lows"] else "warn",
                  note=("最深 %.2f m（%s, %s）" % (st["lows"][0]["min"], st["lows"][0]["x"], st["lows"][0]["y"])) if st["lows"] else
                  "没有低于 %g m 的连片区" % thr.low_threshold))
    R.append(dict(code="R13", value="%s 格（%s）" % (f(st["cliff"]), p(st["cliff"], st["judged"])), sev="ok" if not st["cliff"] else "info",
                  note="与某个邻格高差超过 %g m：挡墙、桥、残留建筑或数据台阶。%s" % (2 * thr.spike_diff, "；".join(st["samples"]["cliff"][:3]))))
    R.append(dict(code="R14", value="中位 %s%%，90%% 分位 %s%%" % (st["slopeQuantiles"].get("p50"), st["slopeQuantiles"].get("p90")),
                  sev="info", note="坡度大于 30%% 的格 %s 个（%s）" % (f(st["steep"]), p(st["steep"], st["judged"]))))
    return R
