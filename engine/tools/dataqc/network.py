# -*- coding: utf-8 -*-
"""管网「能不能拿去建一维／二维水动力模型」的体检。只读。

判定逐条移植自 m4-data：管网取数（取数）、管网体检（A～F 主流程）、管网补充检查（A13～A18、B9、B10、C13、F1、F2）、
以及在库里算的三项（B7 重复管段、C7 相交未连接、C11 端点落在管段中间）。平台用 PostGIS 做的空间查询，
这里用 shapely 的 STRtree 做，口径相同（ST_Crosses ＝ crosses，ST_DWithin ＝ dwithin）。

每项结果：{"count", "total", "ids"（全量编号）, "layers", "notDone"}。
"""
from __future__ import annotations

import collections
import math
import re

import numpy as np
import pandas as pd
import shapely
from scipy.spatial import cKDTree

from . import layers as L
from . import sources
from .thresholds import ALL_CODES, PLACEHOLDER_MATERIALS, Thresholds

_NUM_RE = re.compile(r"^\s*-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?\s*$")
_PERFORATED = re.compile(r".*(perf|prft|pefrt|prfrt).*")


def _num(s: pd.Series | None, n: int) -> np.ndarray:
    """像数才转：outfall 的管径是 varchar，里面有 N/A。"""
    if s is None:
        return np.full(n, np.nan)
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float).to_numpy()
    t = s.astype("string")
    mask = t.str.match(_NUM_RE).fillna(False).to_numpy(bool)
    out = np.full(n, np.nan)
    out[mask] = pd.to_numeric(t[mask], errors="coerce").to_numpy(float)
    return out


def _text(s: pd.Series | None, n: int) -> list:
    if s is None:
        return [None] * n
    # ⚠️ pandas 的 str／float 列缺失值有 None、NaN、pd.NA 三种写法，一律当空（用 pd.isna 认全）
    a = s.to_numpy(dtype=object)
    miss = pd.isna(s.to_numpy(dtype=object)) if s.dtype == object else pd.isna(a)
    return [None if m else str(v) for v, m in zip(a, miss)]


def missing_elev(v):
    """标高缺失：空、0、−9999 一类占位、或低于 −50 米（阿布扎比没有那么低的地方）。"""
    v = np.asarray(v, dtype=float)
    return np.isnan(v) | (v == 0) | (v <= -50)


def _blank(v):
    """空值判据：None、NaN、pd.NA 都算空。DataFrame 拼接后 None 会变成 NaN，判空一律走这里。"""
    return v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v))


def _not_in_service(s):
    if _blank(s):
        return False
    s = str(s).lower()
    return any(k in s for k in ("abandon", "remov", "expir", "demolish", "废弃", "拆除"))


class Network:
    """一张管网：管段表 P、节点表 N（pandas），外加几何（P_geom、N_geom）与原始 GeoDataFrame（补充检查要用别的列）。"""

    def __init__(self, path: str, log=print):
        self.path = path
        # ⚠️ 按层名字母序读，与平台从库里取图层的顺序（geometry_columns ORDER BY 1）一致：
        #    节点在表里的先后决定端点吸附时挂到哪个重合节点上（B8 有 9,279 对重合节点），
        #    顺序不同会让 D5～D8 的归属差十几条
        layers = sorted(sources.list_layers(path), key=lambda a: a[0].lower())
        self.raw = {}
        meta = []
        for name, geom in layers:
            gdf = sources.read_layer(path, name)
            self.raw[name] = gdf
            meta.append((name, geom, [counter for counter in gdf.columns if counter != gdf.geometry.name]))
        self.recognize = L.recognize(meta)
        self.crs = next((g.crs for g in self.raw.values() if g.crs is not None), None)
        self._read_links()
        self._read_nodes()
        log("管网：%d 条管段（%s），%d 个节点（%s）；跳过 %s" % (
            len(self.P), "、".join(l.table for l in self.recognize.links), len(self.N), "、".join(l.table for l in self.recognize.nodes),
            "、".join(self.recognize.skipped) or "无"))

    # ── 取数（管网取数.java）──
    def _read_links(self):
        block = []
        geom = []
        for l in self.recognize.links:
            g = self.raw[l.table]
            g = g[g.geometry.notna()].reset_index(drop=True)
            n = len(g)
            counter = lambda k: g[k] if k else None
            geo = np.asarray(g.geometry.values, dtype=object)
            empty = shapely.is_empty(geo)
            nparts = shapely.get_num_geometries(geo)
            first = shapely.get_geometry(geo, 0)
            last = shapely.get_geometry(geo, np.maximum(nparts - 1, 0))
            p0 = shapely.get_point(first, 0)
            p1 = shapely.get_point(last, -1)
            x0, y0 = shapely.get_x(p0), shapely.get_y(p0)
            x1, y1 = shapely.get_x(p1), shapely.get_y(p1)
            empty = empty | np.isnan(x0)
            dia = pd.DataFrame({
                "layer": l.table, "pressure": l.pressure, "id": _text(counter(l.id), n), "us_id": _text(counter(l.us_id), n),
                "ds_id": _text(counter(l.ds_id), n),
                "x0": np.where(empty, 0, x0), "y0": np.where(empty, 0, y0), "x1": np.where(empty, 0, x1), "y1": np.where(empty, 0, y1),
                "length": shapely.length(geo), "attr_len": _num(counter(l.attr_length), n), "diameter": _num(counter(l.diameter), n),
                "us_invert": _num(counter(l.us_invert), n), "ds_invert": _num(counter(l.ds_invert), n),
                "material": _text(counter(l.material), n), "status": _text(counter(l.status), n), "parts": nparts,
                "valid": shapely.is_valid(geo), "empty": empty, "simple": shapely.is_simple(geo), "wall": _text(counter(l.wall), n),
            })
            dia["perforated"] = [bool(w) and bool(_PERFORATED.match(w.lower())) for w in dia["wall"]]
            block.append(dia)
            geom.append(geo)
        self.P = pd.concat(block, ignore_index=True) if block else pd.DataFrame()
        self.P_geom = np.concatenate(geom) if geom else np.array([], dtype=object)

    def _read_nodes(self):
        block = []
        geom = []
        for l in self.recognize.nodes:
            g = self.raw[l.table]
            g = g[g.geometry.notna() & ~g.geometry.is_empty].reset_index(drop=True)
            n = len(g)
            counter = lambda k: g[k] if k else None
            ct = shapely.centroid(np.asarray(g.geometry.values, dtype=object))   # inlet 的几何不一定是 POINT
            block.append(pd.DataFrame({"layer": l.table, "kind": l.kind_of, "id": _text(counter(l.id), n), "x": shapely.get_x(ct),
                                    "y": shapely.get_y(ct), "invert": _num(counter(l.invert), n), "cover": _num(counter(l.cover), n),
                                    "elev_source": _text(counter(l.elev_source), n)}))
            geom.append(ct)
        self.N = pd.concat(block, ignore_index=True) if block else pd.DataFrame()
        self.N["ground_dtm"] = np.nan
        self.N["dtm_missing"] = False
        self.N_geom = np.concatenate(geom) if geom else np.array([], dtype=object)

    def _to_crs(self, geoms, crs):
        """几何换到 crs（DTM 的坐标系）；同一坐标系或任一方未知时原样返回。"""
        if crs is None or self.crs is None or self.crs.equals(crs):
            return geoms
        import geopandas as gpd
        return gpd.GeoSeries(geoms, crs=self.crs).to_crs(crs).values

    def lines(self, crs=None):
        return self._to_crs(self.P_geom[~self.P["empty"].to_numpy()], crs)

    def bounds(self, buffer_cells=0.0, crs=None):
        b = shapely.total_bounds(np.asarray(self.lines(crs), dtype=object))
        return (b[0] - buffer_cells, b[1] - buffer_cells, b[2] + buffer_cells, b[3] + buffer_cells)

    def sample_dtm(self, ds, crs=None):
        """每个节点在 DTM 上的值（海拔，未加偏移）。范围外与无数据都记 DTM无数据。"""
        from .raster import sample_points
        pts = self._to_crs(shapely.points(self.N["x"].to_numpy(), self.N["y"].to_numpy()), crs)
        v, none = sample_points(ds, shapely.get_x(pts), shapely.get_y(pts))
        self.N["ground_dtm"] = v
        self.N["dtm_missing"] = none

    def clip(self, area, log=print):
        """只留与面相交的管段、落在面内的节点（面与管网同坐标系）。"""
        shapely.prepare(area)
        km = shapely.intersects(area, self.P_geom) & (~self.P["empty"].to_numpy(bool) if len(self.P) else True)
        kn = shapely.intersects(area, self.N_geom)
        self.P, self.P_geom = self.P[km].reset_index(drop=True), self.P_geom[km]
        self.N, self.N_geom = self.N[kn].reset_index(drop=True), self.N_geom[kn]        # 补充检查读原始图层：按同样的规则裁
        for name, g in list(self.raw.items()):
            if len(g):
                self.raw[name] = g[shapely.intersects(area, np.asarray(g.geometry.values, dtype=object))].reset_index(drop=True)
        log("  裁到范围内：%d 条管段，%d 个节点" % (len(self.P), len(self.N)))

    def meta(self) -> dict:
        """报告用：各层要素数、节点种类、跳过的层、每项检查里「该层没有这个字段」的层。"""
        per_layer = {}
        for l in self.recognize.links:
            per_layer[l.table] = int((self.P["layer"] == l.table).sum())
        for l in self.recognize.nodes:
            per_layer[l.table] = int((self.N["layer"] == l.table).sum())
        none = {"A4": [l.table for l in self.recognize.links if not (l.us_invert and l.ds_invert)],
             "A9": [l.table for l in self.recognize.nodes if not l.invert], "A10": [l.table for l in self.recognize.nodes if not l.cover],
             "A12": [l.table for l in self.recognize.nodes if not l.cover and not l.invert]}
        return {"path": self.path, "layers": dict(sorted(per_layer.items(), key=lambda a: -a[1])),
                "nodeKinds": {l.table: l.kind_of for l in self.recognize.nodes}, "skipped": dict(self.recognize.skipped), "noField": none,
                "mapping": {"links": [vars(l) for l in self.recognize.links], "nodes": [vars(l) for l in self.recognize.nodes]}}


# ═══════════════════════════════════════════════════════════════ 计数器

class Tally:
    __slots__ = ("code", "count", "total", "ids", "layers", "not_done", "by_layer")

    def __init__(self, code):
        self.code, self.count, self.total, self.ids, self.layers, self.not_done = code, 0, 0, [], [], None
        self.by_layer = collections.Counter()

    def hit(self, samples, layer=None):
        self.count += 1
        if samples is not None:
            self.ids.append(samples)
        if layer is not None:
            self.by_layer[layer] += 1
            if layer not in self.layers:
                self.layers.append(layer)

    def dump(self):
        return {"count": int(self.count), "total": int(self.total), "ids": self.ids, "layers": self.layers,
                "byLayer": dict(self.by_layer.most_common()), "notDone": self.not_done}


class _Tallies(dict):
    def __missing__(self, k):
        v = self[k] = Tally(k)
        return v


def _s(v):
    return "" if v is None else v


# ═══════════════════════════════════════════════════════════════ 主流程（管网体检.java）

def check_network(net: Network, thr: Thresholds, tolerance: float = 0.5, has_dtm: bool = False, datum_offset: float | None = None, log=print) -> dict:
    P, N = net.P, net.N
    tally = _Tallies()
    nP, nN = len(P), len(N)
    LINK_LAYER, NODE_LAYER = P["layer"].tolist(), N["layer"].tolist()
    LINK_ID, NODE_ID = P["id"].tolist(), N["id"].tolist()
    NODE_KIND = N["kind"].tolist()
    diam = P["diameter"].to_numpy(float)
    pressure = P["pressure"].to_numpy(bool)
    perforated = P["perforated"].to_numpy(bool)
    empty = P["empty"].to_numpy(bool)
    length = P["length"].to_numpy(float)
    attr_len = P["attr_len"].to_numpy(float)
    us_inv, ds_inv = P["us_invert"].to_numpy(float), P["ds_invert"].to_numpy(float)
    x0, y0, x1, y1 = (P[k].to_numpy(float) for k in ("x0", "y0", "x1", "y1"))
    invert, cover = N["invert"].to_numpy(float), N["cover"].to_numpy(float)
    miss_us, miss_ds = missing_elev(us_inv), missing_elev(ds_inv)
    miss_inv, miss_cov = missing_elev(invert), missing_elev(cover)

    # ── A 属性 ──
    mult_by_layer = {}
    for layer, grp in P.groupby("layer", sort=False):
        dia = grp["diameter"].to_numpy(float)
        dia = dia[(dia > 0) & (dia != -9999) & ~np.isnan(dia)]
        if len(dia) and np.median(dia) < 10:      # 一层管径的中位数小于 10：按米读
            mult_by_layer[layer] = 1000.0
            tally["A3"].count += len(dia)
            tally["A3"].total += len(dia)
            tally["A3"].layers.append(layer)
    mult = np.array([mult_by_layer.get(l, 1.0) for l in LINK_LAYER])
    diam_mm = diam * mult
    for k in range(nP):
        lk_layer, next_id = LINK_LAYER[k], LINK_ID[k]
        tally["A1"].total += 1
        dia = diam_mm[k]
        if np.isnan(dia) or dia <= 0 or diam[k] == -9999:
            tally["A1"].hit(next_id, lk_layer)
        else:
            tally["A2"].total += 1
            if dia < thr.min_diameter or dia > thr.max_diameter:
                tally["A2"].hit(next_id, lk_layer)
            elif not pressure[k] and not perforated[k]:
                # A19：DMT 2022 手册推荐地表水管道最小 300 mm（标准符合性）；穿孔管按第 5 章最小 160 mm，不在此列
                tally["A19"].total += 1
                if dia < thr.manual_min_diameter:
                    tally["A19"].hit(next_id, lk_layer)
        if not pressure[k]:
            tally["A4"].total += 1
            if miss_us[k] or miss_ds[k]:
                tally["A4"].hit(next_id, lk_layer)
        tally["A5"].total += 1
        if np.isnan(attr_len[k]) or attr_len[k] <= 0:
            tally["A5"].hit(next_id, lk_layer)
        elif length[k] > thr.very_short:
            tally["A6"].total += 1
            if abs(attr_len[k] - length[k]) / length[k] > thr.length_deviation:
                tally["A6"].hit(next_id, lk_layer)
        tally["A7"].total += 1
        mat = P["material"].iat[k]
        # ⚠️ 判空要用 _blank：DataFrame 拼接会把 None 变成 NaN，只写 `is None` 会漏掉这些（实测 A7 少 535 条）
        if _blank(mat) or str(mat).strip().lower() in PLACEHOLDER_MATERIALS:
            tally["A7"].hit(next_id, lk_layer)
        tally["A8"].total += 1
        if _not_in_service(P["status"].iat[k]):
            tally["A8"].hit(next_id, lk_layer)
    src = N["elev_source"].tolist()
    for ref in range(nN):
        kind, next_id, nd_layer = NODE_KIND[ref], NODE_ID[ref], NODE_LAYER[ref]
        if kind in (L.CHAMBER, L.INLET, L.OUTFALL, L.SOAKAWAY):
            tally["A9"].total += 1
            if miss_inv[ref]:
                tally["A9"].hit(next_id, nd_layer)
        if kind in (L.CHAMBER, L.INLET):
            tally["A10"].total += 1
            if miss_cov[ref]:
                tally["A10"].hit(next_id, nd_layer)
            if not miss_inv[ref] and not miss_cov[ref]:
                tally["A11"].total += 1
                depth = cover[ref] - invert[ref]
                if depth <= 0 or depth > thr.max_well_depth:
                    tally["A11"].hit(next_id, nd_layer)
        # A12：只有 DEM 推出来的高程、没有原始井盖与井底。原始两项都在的不算（2026-09-27 先前全数计入是错的）
        if src[ref] is not None:
            tally["A12"].total += 1
            s = src[ref].lower()
            if any(k in s for k in ("dem", "dtm", "提取", "calculated")) and miss_cov[ref] and miss_inv[ref]:
                tally["A12"].hit(next_id, nd_layer)

    # ── B 几何 ──
    valid = P["valid"].to_numpy(bool)
    simple = P["simple"].to_numpy(bool)
    parts = P["parts"].to_numpy(int)
    for k in range(nP):
        next_id, lk_layer = LINK_ID[k], LINK_LAYER[k]
        for counter in ("B1", "B2", "B3", "B4", "B5", "B6"):
            tally[counter].total += 1
        if not valid[k]:
            tally["B1"].hit(next_id, lk_layer)
        if empty[k]:
            tally["B2"].hit(next_id, lk_layer)
        if not empty[k] and length[k] < thr.zero_length:
            tally["B3"].hit(next_id, lk_layer)
        elif not empty[k] and length[k] < thr.very_short:
            tally["B4"].hit(next_id, lk_layer)
        if parts[k] > 1:
            tally["B5"].hit(next_id, lk_layer)
        # 首尾重合（闭合成环）也算：JTS 认闭合线是简单的，但起止同一点的管段在模型里没有上下游
        closed = not empty[k] and length[k] >= thr.zero_length and math.hypot(x1[k] - x0[k], y1[k] - y0[k]) < thr.zero_length
        if not simple[k] or closed:
            tally["B6"].hit(next_id, lk_layer)
    node_xy = np.column_stack([N["x"].to_numpy(float), N["y"].to_numpy(float)]) if nN else np.zeros((0, 2))
    node_tree = cKDTree(node_xy) if nN else None
    tally["B8"].total = nN
    if nN:
        pairs = node_tree.query_pairs(thr.dup_node_dist, output_type="ndarray")
        pairs = pairs[np.argsort(pairs[:, 0] * nN + pairs[:, 1], kind="stable")] if len(pairs) else pairs
        for ref, j in pairs:
            if math.hypot(*(node_xy[ref] - node_xy[j])) < thr.dup_node_dist and not (NODE_ID[ref] is not None and NODE_ID[ref] == NODE_ID[j]):
                tally["B8"].hit(_s(NODE_ID[ref]) + "≈" + _s(NODE_ID[j]), NODE_LAYER[ref])
    log("  A、B 完成")

    # ── C 拓扑：端点吸附成顶点 ──
    # 加 1 µm 吃浮点误差：按毫米数字化的数据，差 1 mm 的两个端点算出来是 0.00100000016（2026-09-27 与 ArcGIS 追踪网络对照时发现）
    tol = tolerance + 1e-6
    us = np.full(nP, -1, dtype=np.int64)
    ds = np.full(nP, -1, dtype=np.int64)
    bare = []                     # 裸顶点坐标
    bare_cells = collections.defaultdict(list)
    cell_w = max(tol, 1e-6)
    # ⚠️ 端点吸附要逐字照 Java 的网格实现：按 3×3 格、格内插入顺序找最近的，
    #    距离相等（d <= bd）时后一个候选获胜。用 cKDTree 找最近会与平台差几十到几百条 ——
    #    重合节点（B8 有 9,279 对）上管端挂到哪一个节点，决定 D5 井底、C6 死端这些项的归属
    node_cells = collections.defaultdict(list)
    for v in range(nN):
        node_cells[(math.floor(node_xy[v, 0] / cell_w), math.floor(node_xy[v, 1] / cell_w))].append(v)

    def neighbours(x, y):
        gi, gj = math.floor(x / cell_w), math.floor(y / cell_w)
        for a in (gi - 1, gi, gi + 1):
            for b in (gj - 1, gj, gj + 1):
                yield from node_cells.get((a, b), ())

    def snap(x, y):
        best, bd = -1, tol
        for v in neighbours(x, y):
            d = math.hypot(node_xy[v, 0] - x, node_xy[v, 1] - y)
            if d <= bd:
                bd, best = d, v
        if best >= 0:
            return best
        gi, gj = math.floor(x / cell_w), math.floor(y / cell_w)
        for a in (gi - 1, gi, gi + 1):
            for b in (gj - 1, gj, gj + 1):
                for v in bare_cells.get((a, b), ()):
                    if math.hypot(bare[v][0] - x, bare[v][1] - y) <= tol:
                        return nN + v
        bare.append((x, y))
        bare_cells[(gi, gj)].append(len(bare) - 1)
        return nN + len(bare) - 1

    for k in range(nP):
        us[k] = snap(x0[k], y0[k])
        ds[k] = snap(x1[k], y1[k])
    nV = nN + len(bare)
    degree = np.zeros(nV, dtype=np.int64)
    real = ~empty
    np.add.at(degree, us[real], 1)
    np.add.at(degree, ds[real], 1)
    # 容差内重合的节点是同一个位置：并成一个顶点（2026-09-27：出水口 OFL_70 与两个 SW_NODE 叠在同一点，3,228 条管被误判为 C4）
    coincident = node_tree.query_pairs(tol, output_type="ndarray") if nN else np.zeros((0, 2), dtype=np.int64)
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    def components(edges):
        if len(edges) == 0:
            return np.arange(nV)
        e = np.asarray(edges)
        m = coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(nV, nV))
        return connected_components(m, directed=False)[1]

    find = components(np.vstack([coincident, np.column_stack([us[real], ds[real]])]) if len(coincident) or real.any() else [])
    rep = components(coincident)        # 只并重合节点（C12 按顶点、不按分量）

    # C1 悬挂端点：没有节点、也没接到别的管段（度 1 的裸顶点）
    tally["C1"].total = 2 * nP
    for k in np.nonzero(real)[0]:
        if us[k] >= nN and degree[us[k]] == 1:
            tally["C1"].hit(_s(LINK_ID[k]) + "(起)", LINK_LAYER[k])
        if ds[k] >= nN and degree[ds[k]] == 1:
            tally["C1"].hit(_s(LINK_ID[k]) + "(止)", LINK_LAYER[k])
    # 连通分量：只数有管段的分量
    link_roots = find[us[real]]
    comp_links = collections.Counter(link_roots.tolist())
    comp_sample = {}
    for k in np.nonzero(real)[0]:
        comp_sample.setdefault(int(find[us[k]]), LINK_ID[k])
    outlet_pts = np.array([s in L.OUTLET_KINDS for s in NODE_KIND], dtype=bool)
    has_outlet = {int(r) for r in find[:nN][outlet_pts]} & set(comp_links)
    live = int(real.sum())
    tally["C2"].count = len(comp_links)
    for counter in ("C2", "C3", "C4"):
        tally[counter].total = live
    for r, n in comp_links.items():
        if n == 1:
            tally["C3"].hit(comp_sample[r], None)
        if r not in has_outlet:
            tally["C4"].count += n
    for k in np.nonzero(real)[0]:
        if int(find[us[k]]) not in has_outlet:
            tally["C4"].ids.append(LINK_ID[k])
            if LINK_LAYER[k] not in tally["C4"].layers:
                tally["C4"].layers.append(LINK_LAYER[k])
    largest = max(comp_links.values()) if comp_links else 0
    # C5 孤立节点、C6 检查井死端
    for v in range(nN):
        if NODE_KIND[v] in (L.VALVE, L.PUMP):
            continue
        tally["C5"].total += 1
        if degree[v] == 0 and int(find[v]) not in comp_links:
            tally["C5"].hit(NODE_ID[v], NODE_LAYER[v])
        if NODE_KIND[v] == L.CHAMBER:
            tally["C6"].total += 1
            if degree[v] == 1:
                tally["C6"].hit(NODE_ID[v], NODE_LAYER[v])
    # C8／C9／C10 编号：上下游编号指向的节点在不在、在的话离端点多远
    by_code = {}
    for v in range(nN):
        if NODE_ID[v] is not None:
            by_code.setdefault(NODE_ID[v], v)
    us_ids, ds_ids = P["us_id"].tolist(), P["ds_id"].tolist()
    if any(a is not None or b is not None for a, b in zip(us_ids, ds_ids)):
        for k in np.nonzero(real)[0]:
            for end in (0, 1):
                ref = us_ids[k] if end == 0 else ds_ids[k]
                if ref is None or isinstance(ref, float) or not str(ref).strip():     # GeoPackage 里的空值读出来是 NaN
                    continue
                ref = str(ref)
                tally["C8"].total += 1
                v = by_code.get(ref)
                if v is None:
                    tally["C8"].hit(_s(LINK_ID[k]) + "→" + ref, LINK_LAYER[k])
                    continue
                tally["C9"].total += 1
                ex, ey = (x0[k], y0[k]) if end == 0 else (x1[k], y1[k])
                fx, fy = (x1[k], y1[k]) if end == 0 else (x0[k], y0[k])
                if math.hypot(node_xy[v, 0] - ex, node_xy[v, 1] - ey) > thr.id_offset_tol:
                    rev = math.hypot(node_xy[v, 0] - fx, node_xy[v, 1] - fy)
                    if rev <= thr.id_offset_tol:      # 编号指的节点在另一端 ＝ 画反了；两端都远 ＝ 编号错
                        tally["C10"].hit(LINK_ID[k], LINK_LAYER[k])
                        tally["C10"].total = tally["C9"].total
                    else:
                        tally["C9"].hit(LINK_ID[k], LINK_LAYER[k])
    else:
        tally["C8"].not_done = "管段没有上下游节点编号字段"

    # C12 沿流向到不了出口（只看所在子网有出口的重力管）
    from_v = np.full(nP, -1, dtype=np.int64)
    in_edges = collections.defaultdict(list)
    for k in np.nonzero(real & ~pressure)[0]:
        a, b = int(rep[us[k]]), int(rep[ds[k]])
        us_at_ds = False
        u = by_code.get(us_ids[k]) if us_ids[k] is not None else None
        if u is not None:
            us_at_ds = math.hypot(node_xy[u, 0] - x1[k], node_xy[u, 1] - y1[k]) < math.hypot(node_xy[u, 0] - x0[k], node_xy[u, 1] - y0[k])
        us_v, ds_v = (b, a) if us_at_ds else (a, b)
        if not miss_us[k] and not miss_ds[k] and abs(us_inv[k] - ds_inv[k]) > thr.flat_slope_drop and us_inv[k] < ds_inv[k]:
            us_v, ds_v = ds_v, us_v          # 逆坡：水实际往「上游编号」那一端流
        from_v[k] = us_v
        in_edges[ds_v].append(k)
    seen = set()
    queue = collections.deque()
    for v in np.nonzero(outlet_pts)[0]:
        r = int(rep[v])
        if r not in seen:
            seen.add(r)
            queue.append(r)
    reached = np.zeros(nP, dtype=bool)
    while queue:
        v = queue.popleft()
        for k in in_edges.get(v, ()):
            if reached[k]:
                continue
            reached[k] = True
            if int(from_v[k]) not in seen:
                seen.add(int(from_v[k]))
                queue.append(int(from_v[k]))
    for k in range(nP):
        if from_v[k] < 0 or int(find[us[k]]) not in has_outlet:
            continue
        tally["C12"].total += 1
        if not reached[k]:
            tally["C12"].hit(LINK_ID[k], LINK_LAYER[k])

    # F3 排口不是网络末端：没有任何管接入，或被某条管当成上游
    used_as_us = {u for u in us_ids if u is not None}
    for v in range(nN):
        if NODE_KIND[v] != L.OUTFALL:
            continue
        tally["F3"].total += 1
        if (degree[v] == 0 and int(find[v]) not in comp_links) or (NODE_ID[v] is not None and NODE_ID[v] in used_as_us):
            tally["F3"].hit(NODE_ID[v], NODE_LAYER[v])
    log("  C 完成：%d 个连通分量，最大一块占 %.1f%%" % (len(comp_links), 100 * largest / max(live, 1)))

    # ── D 水力（只对重力管）──
    in_dia = {}
    for k in np.nonzero(real & ~pressure)[0]:
        if not miss_us[k] and not miss_ds[k] and length[k] > thr.zero_length:
            drop = us_inv[k] - ds_inv[k]
            for counter in ("D1", "D2", "D3"):
                tally[counter].total += 1
            if drop < -thr.flat_slope_drop:
                tally["D1"].hit(LINK_ID[k], LINK_LAYER[k])
            elif abs(drop) <= thr.flat_slope_drop:
                tally["D2"].hit(LINK_ID[k], LINK_LAYER[k])
            elif drop / length[k] > thr.steep_slope:
                tally["D3"].hit(LINK_ID[k], LINK_LAYER[k])
        if not np.isnan(diam[k]) and diam[k] > 0:
            v = int(ds[k])
            in_dia[v] = max(in_dia.get(v, -1e300), diam_mm[k])
    for k in np.nonzero(real & ~pressure)[0]:
        if np.isnan(diam[k]) or diam[k] <= 0:
            continue
        up_dia = in_dia.get(int(us[k]))
        if up_dia is None:
            continue
        tally["D4"].total += 1
        if diam_mm[k] + 1e-6 < up_dia:
            tally["D4"].hit(LINK_ID[k], LINK_LAYER[k])
    for k in np.nonzero(real & ~pressure)[0]:
        if miss_us[k] or us[k] >= nN:
            continue
        v = int(us[k])
        if not miss_inv[v]:
            tally["D5"].total += 1
            if us_inv[k] < invert[v] - 0.05:
                tally["D5"].hit(LINK_ID[k], LINK_LAYER[k])
        if not miss_cov[v] and not np.isnan(diam[k]) and diam[k] > 0:
            crown = us_inv[k] + diam_mm[k] / 1000.0
            tally["D6"].total += 1
            tally["D7"].total += 1
            if not perforated[k]:
                tally["D8"].total += 1
            cover_depth = cover[v] - crown
            if cover_depth < 0:
                tally["D6"].hit(LINK_ID[k], LINK_LAYER[k])
            else:
                if cover_depth < thr.min_cover:
                    tally["D7"].hit(LINK_ID[k], LINK_LAYER[k])
                # D8：手册 §4.3.5 最小覆土 1.2 m。D7 问「数据录错没有」，D8 问「合不合设计标准」
                if not perforated[k] and cover_depth < thr.manual_min_cover:
                    tally["D8"].hit(LINK_ID[k], LINK_LAYER[k])

    # ── E 与 DTM ──
    basis = None if datum_offset is None else "给定"
    E = {}
    if not has_dtm:
        for counter in ("E1", "E2", "E3", "E4", "E5"):
            tally[counter].not_done = "没有给 DTM"
    else:
        ground = N["ground_dtm"].to_numpy(float)
        no_dtm = N["dtm_missing"].to_numpy(bool)
        has = ~miss_cov & ~np.isnan(ground)
        deltas = cover[has] - ground[has]
        med = float(np.median(deltas)) if len(deltas) else float("nan")
        if datum_offset is None and len(deltas):
            # 基准差按整米取（DMT 的约定是 +100）；比对 E2–E4 用取整后的值，实测中位数原样写进依据
            datum_offset = float(round(med))
            basis = "井盖 − DTM 的中位数 %.2f m（%d 个节点），比对按整米 %.0f m" % (med, len(deltas), datum_offset)
        tally["E1"].count = 0 if datum_offset is None else round(datum_offset)
        tally["E1"].total = len(deltas)
        if datum_offset is not None:
            e = deltas - datum_offset
            E = {"offset": datum_offset, "median": med, "n": int(len(deltas)), "within05": int((np.abs(e) <= 0.5).sum()),
                 "within1": int((np.abs(e) <= 1.0).sum()),
                 "hist": np.histogram(e, bins=[-1e9, -2, -1, -0.5, -0.2, 0.2, 0.5, 1, 2, 1e9])[0].astype(int).tolist()}
        for ref in range(nN):
            tally["E5"].total += 1
            if no_dtm[ref]:
                tally["E5"].hit(NODE_ID[ref], NODE_LAYER[ref])
                continue
            if datum_offset is None or np.isnan(ground[ref]):
                continue
            g = ground[ref] + datum_offset
            if not miss_cov[ref]:
                tally["E2"].total += 1
                if abs(cover[ref] - g) > thr.cover_dtm_tol:
                    tally["E2"].hit(NODE_ID[ref], NODE_LAYER[ref])
            if not miss_inv[ref]:
                tally["E3"].total += 1
                tally["E4"].total += 1
                buried = g - invert[ref]
                if buried < 0:
                    tally["E3"].hit(NODE_ID[ref], NODE_LAYER[ref])
                elif buried > thr.max_buried:
                    tally["E4"].hit(NODE_ID[ref], NODE_LAYER[ref])
    log("  D、E 完成")

    # ── 外部（平台在库里算的）──
    for k, v in spatial_checks(net, tolerance).items():
        tally[k] = v
    for k, v in extra_checks(net).items():
        tally[k] = v
    log("  空间与补充检查完成")

    scan = {counter: tally[counter].dump() for counter in ALL_CODES if counter in tally}
    summary = {"links": nP, "lengthKm": float(length.sum() / 1000), "nodes": nN, "components": len(comp_links),
            "largestShare": (largest / live) if live else 0.0, "datumOffset": datum_offset, "datumBasis": basis,
            "gravity": int((~pressure).sum()), "pressure": int(pressure.sum()), "tolerance": tolerance}
    return {"summary": summary, "checks": scan, "E": E}


# ═══════════════════════════════════════════════════════════════ 空间检查（管网取数.java 里用 PostGIS 算的三项）

def spatial_checks(net: Network, tolerance: float) -> dict:
    P, G = net.P, net.P_geom
    codes = P["id"].tolist()
    layer = P["layer"].tolist()
    out = {}
    # B7 重复管段：几何完全相同（WKB 相同），每组多出来的条数；跨层也算
    counter = Tally("B7")
    wkb = shapely.to_wkb(G, include_srid=False)
    grp = collections.defaultdict(list)
    for k, w in enumerate(wkb):
        grp[w].append(k)
    counter.total = len(G)
    counter.count = len(G) - len(grp)
    for ks in grp.values():
        if len(ks) > 1:
            counter.ids.extend(_s(codes[k]) or str(k) for k in sorted(ks, key=lambda k: _s(codes[k])))
    out["B7"] = counter
    # C7 相交未连接：两条管段平面交叉（ST_Crosses：内部相交、端点不相交）。跨层的每一对也查
    counter = Tally("C7")
    counter.total = len(G)
    tree = shapely.STRtree(G)
    a, b = tree.query(G, predicate="crosses")
    m = a < b
    for ref, j in zip(a[m], b[m]):
        counter.hit(_s(codes[ref]) + "×" + _s(codes[j]))
    out["C7"] = counter
    # C11 端点落在别的管段中间：端点在容差内碰到另一条管，但碰的不是那条管的端点（T 形接入而主管没打断）
    counter = Tally("C11")
    tol = tolerance + 1e-6
    real = ~P["empty"].to_numpy(bool)
    counter.total = 2 * len(G)
    first = shapely.points(P["x0"].to_numpy(), P["y0"].to_numpy())
    last = shapely.points(P["x1"].to_numpy(), P["y1"].to_numpy())
    hit = set()
    for end_name, pts in (("起", first), ("止", last)):
        idx = np.nonzero(real)[0]
        pi, gj = tree.query(pts[idx], predicate="dwithin", distance=tol)
        pi = idx[pi]
        keep = (pi != gj) & real[gj]
        pi, gj = pi[keep], gj[keep]
        near_first = shapely.dwithin(pts[pi], first[gj], tol)
        near_last = shapely.dwithin(pts[pi], last[gj], tol)
        for k in np.unique(pi[~near_first & ~near_last]):
            hit.add(_s(codes[k]) + "(" + end_name + ")")
    counter.count = len(hit)
    counter.ids = sorted(hit)
    out["C11"] = counter
    return out


# ═══════════════════════════════════════════════════════════════ 补充检查（管网补充检查.java）

COORD_TOL, FALL_TOL, SLOPE_REL_TOL, INLET_DIST, SIZE_TOL = 1.0, 0.01, 0.2, 30, 1.0
STANDARD_DIAMETERS = np.array([100, 150, 200, 225, 250, 300, 350, 375, 400, 450, 500, 525, 600, 675, 700, 750, 800, 825, 900, 1000, 1050, 1100,
                 1200, 1350, 1400, 1500, 1600, 1650, 1800, 1950, 2000, 2100, 2200, 2400, 2500, 2700, 2800, 3000], dtype=float)


def _first_col(gdf, *candidates):
    for counter in candidates:
        if counter in gdf.columns:
            return counter
    return None


def _elev_ok(v):
    """标高有效：非空、非 0、不低于 −50（SQL 的 NULL 比较为假，这里显式按 NaN 处理）。"""
    return ~np.isnan(v) & (v != 0) & (v > -50)


def _ids(gdf, rows):
    return [("" if v is None else v) for v in _text(gdf[rows], len(gdf))]


def extra_checks(net: Network) -> dict:
    recognize, raw = net.recognize, net.raw
    out = {}

    def not_done(code, reason):
        counter = Tally(code)
        counter.not_done = reason
        out[code] = counter

    # A13 / A14 编号为空或重复
    for code, ls, name in (("A13", recognize.links, "管段"), ("A14", recognize.nodes, "节点")):
        full8 = []
        for l in ls:
            if l.id:
                full8.extend(_ids(raw[l.table], l.id))
        if not full8:
            not_done(code, "%s层没有编号字段" % name)
            continue
        dup_count = collections.Counter(full8)
        counter = Tally(code)
        counter.total = len(full8)
        for ref in full8:
            if not ref.strip() or dup_count[ref] > 1:
                counter.hit(ref)
        out[code] = counter
    # A15 管底高差字段与上下游管底不符：整层先判约定（上游−下游 或 下游−上游）
    counter = Tally("A15")
    done = False
    for l in recognize.links:
        g = raw[l.table]
        fall_col = _first_col(g, "difference_in_invert", "invert_difference", "fall")
        if not (fall_col and l.us_invert and l.ds_invert and l.id):
            continue
        done = True
        n = len(g)
        u, dia, f = _num(g[l.us_invert], n), _num(g[l.ds_invert], n), _num(g[fall_col], n)
        mask = _elev_ok(u) & _elev_ok(dia) & ~np.isnan(f)
        fwd = (np.abs(f - (u - dia)) <= FALL_TOL)[mask].sum()
        rev = (np.abs(f - (dia - u)) <= FALL_TOL)[mask].sum()
        sign = 1 if fwd >= rev else -1
        ids = _ids(g, l.id)
        counter.total += int(mask.sum())
        for k in np.nonzero(mask & (np.abs(f - sign * (u - dia)) > FALL_TOL))[0]:
            counter.hit(ids[k])
    out["A15"] = counter if done else None
    if not done:
        not_done("A15", "管段层没有管底高差字段")
    # A16 坡度字段与计算坡度不符：整层先判单位（小数／百分比／千分比）与正负
    counter = Tally("A16")
    done = False
    for l in recognize.links:
        g = raw[l.table]
        slope_col = _first_col(g, "gradient", "slope", "pipe_slope")
        if not (slope_col and l.us_invert and l.ds_invert and l.id):
            continue
        n = len(g)
        u, dia, s = _num(g[l.us_invert], n), _num(g[l.ds_invert], n), _num(g[slope_col], n)
        length = shapely.length(np.asarray(g.geometry.values, dtype=object))
        with np.errstate(divide="ignore", invalid="ignore"):
            calc_slope = np.where(length > 0, (u - dia) / length, np.nan)
            mask = _elev_ok(u) & _elev_ok(dia) & ~np.isnan(s) & (s != 0) & (np.abs(u - dia) > 0.001)
            ratio = s / np.where(calc_slope == 0, np.nan, calc_slope)
        ratio = ratio[mask & ~np.isnan(ratio)]
        if not len(ratio):
            continue
        done = True
        m = float(np.median(ratio))
        a = abs(m)
        mult = math.copysign(1000 if a > 300 else 100 if a > 30 else 1, m)
        want = mult * calc_slope
        ids = _ids(g, l.id)
        counter.total += int(mask.sum())
        with np.errstate(invalid="ignore"):
            hit = mask & (np.abs(s - want) > SLOPE_REL_TOL * np.abs(want)) & (np.abs(s - want) > 1e-4 * abs(mult))
        for k in np.nonzero(hit)[0]:
            counter.hit(ids[k])
    if done:
        out["A16"] = counter
    else:
        not_done("A16", "管段层没有坡度字段")
    # A17 非标准管径：50～5000 mm 内、与最近标准规格相差超过 1 mm。整层中位数小于 10 的按米读
    counter = Tally("A17")
    done = False
    for l in recognize.links:
        if not (l.diameter and l.id):
            continue
        done = True
        g = raw[l.table]
        dia = _num(g[l.diameter], len(g))
        pos = dia[dia > 0]
        mult = 1000 if len(pos) and np.median(pos) < 10 else 1
        mm = dia * mult
        mask = (mm >= 50) & (mm <= 5000)
        gap = np.abs(mm[:, None] - STANDARD_DIAMETERS[None, :]).min(axis=1)
        ids = _ids(g, l.id)
        counter.total += int(mask.sum())
        for k in np.nonzero(mask & (gap > SIZE_TOL))[0]:
            counter.hit(ids[k])
    out["A17"] = counter if done else out.get("A17")
    if not done:
        not_done("A17", "管段层没有管径字段")
    # A18 压力管或箱涵混在重力管网里
    counter = Tally("A18")
    done = False
    pat = re.compile(r"(pressure|rising|force main|forcemain|culvert|box)")
    for l in recognize.links:
        if l.pressure or not l.id:
            continue
        g = raw[l.table]
        type_cols = [k for k in ("pipe_type", "pipe_subtype", "unittype", "subassetname") if k in g.columns]
        if not type_cols:
            continue
        done = True
        txt = g[type_cols].astype("string").fillna("").agg(" ".join, axis=1).str.lower()
        ids = _ids(g, l.id)
        counter.total += len(g)
        for k in np.nonzero(txt.str.contains(pat).to_numpy(bool))[0]:
            counter.hit(ids[k])
    if done:
        out["A18"] = counter
    else:
        not_done("A18", "管段层没有管型字段")
    # B9 节点属性坐标与点位相距超过 1 m；B10 管段起终点坐标与几何端点相距超过 1 m（对调也算对）
    c9, c10 = Tally("B9"), Tally("B10")
    done_b9 = done_b10 = False
    for l in recognize.nodes:
        g = raw[l.table]
        xc, yc = _first_col(g, "xcoord", "x_coord"), _first_col(g, "ycoord", "y_coord")
        if not (xc and yc and l.id):
            continue
        done_b9 = True
        n = len(g)
        X, Y = _num(g[xc], n), _num(g[yc], n)
        ct = shapely.centroid(np.asarray(g.geometry.values, dtype=object))
        mask = ~np.isnan(X) & ~np.isnan(Y) & (X != 0) & (Y != 0) & ~shapely.is_missing(ct)
        dist = np.hypot(X - shapely.get_x(ct), Y - shapely.get_y(ct))
        ids = _ids(g, l.id)
        c9.total += int(mask.sum())
        for k in np.nonzero(mask & (dist > COORD_TOL))[0]:
            c9.hit(ids[k])
    for l in recognize.links:
        g = raw[l.table]
        a0, b0 = _first_col(g, "xcoord_start", "startx"), _first_col(g, "ycoord_start", "starty")
        a1, b1 = _first_col(g, "xcoord_end", "endx"), _first_col(g, "ycoord_end", "endy")
        if not (a0 and b0 and a1 and b1 and l.id):
            continue
        done_b10 = True
        n = len(g)
        A, B, C_, D = (_num(g[k], n) for k in (a0, b0, a1, b1))
        geo = np.asarray(g.geometry.values, dtype=object)
        nparts = shapely.get_num_geometries(geo)
        s = shapely.get_point(shapely.get_geometry(geo, 0), 0)
        e = shapely.get_point(shapely.get_geometry(geo, np.maximum(nparts - 1, 0)), -1)
        sx, sy, ex, ey = shapely.get_x(s), shapely.get_y(s), shapely.get_x(e), shapely.get_y(e)
        mask = (A != 0) & (B != 0) & (C_ != 0) & (D != 0) & ~np.isnan(A + B + C_ + D) & ~shapely.is_empty(geo) & ~np.isnan(sx)
        fwd = np.maximum(np.hypot(A - sx, B - sy), np.hypot(C_ - ex, D - ey))
        rev = np.maximum(np.hypot(A - ex, B - ey), np.hypot(C_ - sx, D - sy))
        ids = _ids(g, l.id)
        c10.total += int(mask.sum())
        for k in np.nonzero(mask & (np.minimum(fwd, rev) > COORD_TOL))[0]:
            c10.hit(ids[k])
    if done_b9:
        out["B9"] = c9
    else:
        not_done("B9", "节点层没有属性坐标字段")
    if done_b10:
        out["B10"] = c10
    else:
        not_done("B10", "管段层没有起终点坐标字段")
    # C13 雨水口离最近管段超过 30 m
    counter = Tally("C13")
    done = False
    tree = shapely.STRtree(net.P_geom) if len(net.P_geom) else None
    for l in recognize.nodes:
        if l.kind_of != L.INLET or not l.id or tree is None:
            continue
        done = True
        g = raw[l.table]
        ct = shapely.centroid(np.asarray(g.geometry.values, dtype=object))
        has = np.zeros(len(g), dtype=bool)
        ii, _ = tree.query(ct, predicate="dwithin", distance=INLET_DIST)
        has[ii] = True
        ids = _ids(g, l.id)
        counter.total += len(g)
        for k in np.nonzero(~has)[0]:
            counter.hit(ids[k])
    if done:
        out["C13"] = counter
    else:
        not_done("C13", "没有雨水口层或管段层")
    # F1 泵参数缺失或整层同值；F2 泵站台数对不上
    pump_layers = [l for l in recognize.nodes if l.kind_of == L.PUMP]
    if not pump_layers:
        not_done("F1", "没有泵站层")
        not_done("F2", "没有泵站层")
    else:
        f1, f2 = Tally("F1"), Tally("F2")
        done_f1 = done_f2 = False
        for l in pump_layers:
            if not l.id:
                continue
            g = raw[l.table]
            n = len(g)
            ids = _ids(g, l.id)
            f1.total += n
            f2.total += n
            bad = np.zeros(n, dtype=bool)
            has_col = False
            for k in ("flow_rate", "head", "rated_power"):
                if k not in g.columns:
                    continue
                has_col = True
                v = _num(g[k], n)
                # 整层退化：去重后不超过 2 个值、且众数占 95% 以上（空值也算一个值，与 SQL GROUP BY 一致）
                vc = pd.Series(v).value_counts(dropna=False)
                if len(vc) <= 2 and n > 0 and vc.max() >= 0.95 * n:
                    bad[:] = True
                else:
                    bad |= np.isnan(v) | (v == 0)
            if has_col:
                done_f1 = True
                for k in np.nonzero(bad)[0]:
                    f1.hit(ids[k])
            st_col, n_col = _first_col(g, "pump_station_id"), _first_col(g, "no_of_pumps")
            if st_col and n_col:
                done_f2 = True
                s = g[st_col]
                t = pd.Series(_num(g[n_col], n))
                df = pd.DataFrame({"s": s.values, "t": t.values})
                agg = df.dropna(subset=["s"]).groupby("s").agg(n=("t", "size"), m=("t", "max"))
                bad = set(agg.index[agg["n"] != agg["m"]])
                for k in range(n):
                    if s.iat[k] in bad:
                        f2.hit(ids[k])
        out["F1"] = f1 if done_f1 else None
        out["F2"] = f2 if done_f2 else None
        if not done_f1:
            not_done("F1", "泵站层没有流量、扬程、功率字段")
        if not done_f2:
            not_done("F2", "泵站层没有泵站编号或台数字段")
    return {k: v for k, v in out.items() if v is not None}
