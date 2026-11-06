"""时变路网行程时间：本地 Dijkstra（scipy 稀疏图），积水区内的路段按深度加罚或断开。

车辆通行判据（可配）：深度 ≥ 不可通行阈值的路段断开；低于阈值但有水的路段代价乘系数。
泵车是重型车，阈值比小车高，但过桥有载重限制（本版未含桥梁限重）。
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from shapely.geometry import shape, Point
from shapely.strtree import STRtree


class RoadNetwork:
    def __init__(self, edges: pd.DataFrame, vertices: pd.DataFrame):
        self.e = edges.reset_index(drop=True)
        self.v = vertices.reset_index(drop=True)
        self.vid_to_ix = pd.Series(np.arange(len(self.v)), index=self.v["vid"].values)
        self.src = self.vid_to_ix.reindex(self.e["source"].values).values
        self.dst = self.vid_to_ix.reindex(self.e["target"].values).values
        ok = ~(np.isnan(self.src) | np.isnan(self.dst))
        self.e = self.e[ok].reset_index(drop=True); self.src = self.src[ok].astype(int); self.dst = self.dst[ok].astype(int)
        self.tree = cKDTree(np.c_[self.v["lon"].values * np.cos(np.radians(24.4)), self.v["lat"].values])  # 经度按纬度缩放，近似等距
        self.mid = np.c_[self.e["mid_lon"].values, self.e["mid_lat"].values]
        self.n = len(self.v)

    def nearest_vertex(self, lon, lat) -> np.ndarray:
        pts = np.c_[np.asarray(lon) * np.cos(np.radians(24.4)), np.asarray(lat)]
        _, ix = self.tree.query(pts)
        return ix

    def flooded_edge_mask(self, flood_polys: list, depths: list[float]) -> tuple[np.ndarray, np.ndarray]:
        """每条边中点落在哪个积水面内 → 该边的积水深度（取最大）。返回 (depth per edge, flooded bool)。"""
        depth = np.zeros(len(self.e))
        if not flood_polys:
            return depth, depth > 0
        tree = STRtree(flood_polys)
        pts = [Point(x, y) for x, y in self.mid]
        res = tree.query(pts, predicate="within")
        for pi, gi in zip(res[0], res[1]):
            depth[pi] = max(depth[pi], depths[gi])
        return depth, depth > 0

    def blocked_edge_mask(self, points, radius_m: float = 40.0) -> np.ndarray:
        """中点落在这些点 radius_m 内的路段（限重桥 / 净高不足的下穿道）→ True。points: [(lon, lat), …]。"""
        mask = np.zeros(len(self.e), dtype=bool)
        if not points:
            return mask
        mx = self.mid[:, 0] * 111000 * np.cos(np.radians(24.4)); my = self.mid[:, 1] * 111000
        for lon, lat in points:
            dx = mx - float(lon) * 111000 * np.cos(np.radians(24.4)); dy = my - float(lat) * 111000
            mask |= (dx * dx + dy * dy) <= float(radius_m) ** 2
        return mask

    def matrix(self, cost_s: np.ndarray, rcost_s: np.ndarray):
        c = np.where(np.isfinite(cost_s) & (cost_s > 0), cost_s, np.inf)
        r = np.where(np.isfinite(rcost_s) & (rcost_s > 0), rcost_s, np.inf)
        rows = np.r_[self.src[np.isfinite(c)], self.dst[np.isfinite(r)]]
        cols = np.r_[self.dst[np.isfinite(c)], self.src[np.isfinite(r)]]
        vals = np.r_[c[np.isfinite(c)], r[np.isfinite(r)]]
        # coo_matrix 对重复的 (row, col) 是求和：同一对路网节点之间有多条平行路段时代价会被累加。
        # 先按 (row, col, val) 排好，每组只留第一条（即最小代价）。实测 41.4 万条边里 7,216 条踩这个（2026-09-23 修）
        if len(rows):
            order = np.lexsort((vals, cols, rows))
            rows, cols, vals = rows[order], cols[order], vals[order]
            keep = np.ones(len(rows), dtype=bool)
            keep[1:] = (rows[1:] != rows[:-1]) | (cols[1:] != cols[:-1])
            rows, cols, vals = rows[keep], cols[keep], vals[keep]
        return coo_matrix((vals, (rows, cols)), shape=(self.n, self.n)).tocsr()

    def travel_minutes(self, from_lonlat: np.ndarray, to_lonlat: np.ndarray, flood_polys=None, depths=None,
                       impassable_depth_m: float = 0.4, slow_factor: float = 3.0, detour_default_min: float | None = None, blocked_points=None, blocked_radius_m: float = 40.0) -> np.ndarray:
        """from × to 的行程分钟矩阵。积水路段：深度 ≥ 阈值断开，否则代价 × slow_factor；blocked_points（限重桥 / 净高不足）附近路段断开。不可达给 inf。"""
        cost = self.e["cost_s"].values.astype(float).copy(); rcost = self.e["rcost_s"].values.astype(float).copy()
        if flood_polys:
            d, fl = self.flooded_edge_mask(flood_polys, depths or [0.3] * len(flood_polys))
            block = d >= impassable_depth_m
            slow = fl & ~block
            cost[slow] *= slow_factor; rcost[slow] *= slow_factor
            cost[block] = np.inf; rcost[block] = np.inf
        if blocked_points:
            bm = self.blocked_edge_mask(blocked_points, blocked_radius_m)
            cost[bm] = np.inf; rcost[bm] = np.inf
        g = self.matrix(cost, rcost)
        s_ix = self.nearest_vertex(from_lonlat[:, 0], from_lonlat[:, 1])
        t_ix = self.nearest_vertex(to_lonlat[:, 0], to_lonlat[:, 1])
        dist = dijkstra(g, directed=True, indices=s_ix)   # (n_from × n_vertices) 秒
        out = dist[:, t_ix] / 60.0
        # 起终点到最近路网点的接驳：按直线距离 30 km/h 补上
        def leg(pts, ix):
            vx = self.v["lon"].values[ix]; vy = self.v["lat"].values[ix]
            dx = (pts[:, 0] - vx) * 111000 * np.cos(np.radians(24.4)); dy = (pts[:, 1] - vy) * 111000
            return np.hypot(dx, dy) / 500.0  # 30 km/h ≈ 500 m/min
        out = out + leg(from_lonlat, s_ix)[:, None] + leg(to_lonlat, t_ix)[None, :]
        return out


    def edge_patch(self, flood_polys: list) -> np.ndarray:
        """每条边中点落在第几片积水里（-1 = 不在积水里）。积水片互不重叠时一次算好，之后按片换水深 / 剔除某片都不用再叠（2026-10-26，在线路线对照用）。"""
        ix = np.full(len(self.e), -1, dtype=np.int64)
        if not flood_polys:
            return ix
        tree = STRtree(flood_polys)
        pts = [Point(x, y) for x, y in self.mid]
        pi, gi = tree.query(pts, predicate="within")
        ix[pi] = gi
        return ix

    def travel_minutes_depth(self, from_lonlat: np.ndarray, to_lonlat: np.ndarray, edge_depth: np.ndarray,
                             impassable_depth_m: float = 0.4, slow_factor: float = 3.0) -> np.ndarray:
        """同 travel_minutes，但每条边的积水深度直接给（edge_patch 配合各片水深得到），省掉每次的空间叠加。"""
        cost = self.e["cost_s"].values.astype(float).copy(); rcost = self.e["rcost_s"].values.astype(float).copy()
        block = edge_depth >= impassable_depth_m; slow = (edge_depth > 0) & ~block
        cost[slow] *= slow_factor; rcost[slow] *= slow_factor
        cost[block] = np.inf; rcost[block] = np.inf
        g = self.matrix(cost, rcost)
        s_ix = self.nearest_vertex(from_lonlat[:, 0], from_lonlat[:, 1]); t_ix = self.nearest_vertex(to_lonlat[:, 0], to_lonlat[:, 1])
        out = dijkstra(g, directed=True, indices=s_ix)[:, t_ix] / 60.0

        def leg(pts, ix):
            vx = self.v["lon"].values[ix]; vy = self.v["lat"].values[ix]
            return np.hypot((pts[:, 0] - vx) * 111000 * np.cos(np.radians(24.4)), (pts[:, 1] - vy) * 111000) / 500.0
        return out + leg(from_lonlat, s_ix)[:, None] + leg(to_lonlat, t_ix)[None, :]


def haversine_minutes(from_lonlat: np.ndarray, to_lonlat: np.ndarray, speed_kmh: float = 35.0, detour: float = 1.3) -> np.ndarray:
    """没有路网时的兜底：直线距离 × 绕行系数 ÷ 速度。"""
    lon1 = np.radians(from_lonlat[:, 0])[:, None]; lat1 = np.radians(from_lonlat[:, 1])[:, None]
    lon2 = np.radians(to_lonlat[:, 0])[None, :]; lat2 = np.radians(to_lonlat[:, 1])[None, :]
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    km = 6371.0 * 2 * np.arcsin(np.sqrt(a))
    return km * detour / speed_kmh * 60.0
