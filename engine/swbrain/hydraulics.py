"""水力筛查公式。曼宁满管流 + 推理公式，够做规划级筛查，不是水动力模型。"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd


def manning_full_flow(d_m, slope, n):
    """圆管满管流量 (m³/s)。d_m 直径(米)，slope 坡度，n 曼宁系数。支持标量或 ndarray。"""
    d = np.asarray(d_m, dtype=float)
    s = np.asarray(slope, dtype=float)
    nn = np.asarray(n, dtype=float)
    area = math.pi * d ** 2 / 4.0
    r = d / 4.0
    return (1.0 / nn) * area * np.power(r, 2.0 / 3.0) * np.sqrt(s)


def rational_q(area_m2, intensity_mmh, c):
    """推理公式 Q = C·i·A (m³/s)。"""
    return np.asarray(c, dtype=float) * (np.asarray(intensity_mmh, dtype=float) / 1000.0 / 3600.0) * np.asarray(area_m2, dtype=float)


def runoff_volume(area_m2, depth_mm, c):
    """单场雨径流量 (m³)。"""
    return np.asarray(c, dtype=float) * (np.asarray(depth_mm, dtype=float) / 1000.0) * np.asarray(area_m2, dtype=float)


def edge_slope(edges: pd.DataFrame, min_slope: float, default_slope: float, ground_slope: pd.Series | None = None) -> tuple[pd.Series, pd.Series]:
    """由上下游管底标高与长度算坡度；无效/逆坡/过平的用兜底值并打标。返回 (slope, slope_flag)。
    ground_slope（可选，来自 5 m DTM）：无标高的管段若地面坡度 ≥ 默认坡度就用地面坡度并打「DTM地面坡度」——管道随地面走，平地仍按默认（设计最小坡）。"""
    raw = (edges["invert_up"] - edges["invert_dn"]) / edges["len_m"].where(edges["len_m"] > 0.5)
    valid = edges["invert_valid"].fillna(False).astype(bool) & raw.notna()
    flag = pd.Series("ok", index=edges.index, dtype=object)
    slope = raw.copy()
    adverse = valid & (raw < 0)
    flat = valid & (raw >= 0) & (raw < min_slope)
    flag[adverse] = "逆坡"
    flag[flat] = "过平"
    flag[~valid] = "无标高"
    slope[adverse | flat] = min_slope
    slope[~valid] = default_slope
    if ground_slope is not None:
        gs = pd.Series(np.asarray(ground_slope, dtype=float), index=edges.index)
        use = (~valid) & gs.notna() & (gs >= default_slope)
        slope[use] = gs[use]; flag[use] = "DTM地面坡度"
    return slope.astype(float), flag


def manning_n_for(materials: pd.Series, table: dict) -> pd.Series:
    return materials.map(lambda m: table.get(m, table["default"]) if isinstance(m, str) else table["default"]).astype(float)


def required_diameter_mm(q_design, slope, n, target_ratio, standards: list[int]) -> float | None:
    """使 q_design/q_full ≤ target_ratio 的最小标准管径。"""
    for d in standards:
        if manning_full_flow(d / 1000.0, slope, n) * target_ratio >= q_design:
            return float(d)
    return None
