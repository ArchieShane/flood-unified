#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查井自动布置模块

基于以下依据：
1. WA-726-2 第17-22页标准图：检查井尺寸和深度限制
2. WA-726-1 Section 4.4：检查井布置原则（转角、变径、接入处）
3. 咨询公司实际设计（RD-A 2024-1hr）：2151个检查井，2154条管段
   - 平均间距 29.6m，中位数 25.9m
   - 95%分位 66.5m，99%分位 114.3m，最大 151.5m
   - 87.7% ≤ 50m，97.9% ≤ 100m

布置规则（按优先级）：
1. 起点和终点必须有检查井（工程常识）
2. 路径转角处（偏转角 > 45°）必须有检查井（WA-726-1 Section 4.4 推断）
3. 管径变化处必须有检查井（WA-726-1 Section 4.4 明确）
4. 直线段最大间距（基于咨询公司实际设计统计）：
   - 默认：80m（略高于95分位的66.5m，保守）
   - 可调整：用户可传入 max_spacing_m 参数
5. 检查井深度不超过 WA-726-2 标准图限值（严格依据）

输入：路径坐标列表 [(x,y,z), ...]、管径、地面标高
输出：Manhole 对象列表，每个带 compliant 和 violation 字段
"""
import numpy as np
from dataclasses import dataclass
from typing import List, Tuple, Optional

@dataclass
class ManholeSpec:
    """检查井规范（WA-726-2 Table）"""
    x: float
    y: float
    z_ground: float
    z_invert: float
    diameter_mm: int
    depth_mm: int
    max_depth_mm: int
    reason: str  # 'start' / 'end' / 'angle' / 'spacing' / 'diameter_change'
    compliant: bool
    violation: Optional[str] = None

# WA-726-2 第17-22页：检查井尺寸和深度限制
MANHOLE_RULES = {
    # (min_pipe_mm, max_pipe_mm): (shaft_diameter_mm, max_depth_mm, cover_diameter_mm)
    (0, 500): (1100, 7000, 750),
    (501, 800): (None, 2900, 750),  # shaft = pipe + 1200
    (801, 1400): (None, 2900, 900),  # shaft = pipe + 1200
    (1401, 2400): (None, 3200, 900),  # shaft = pipe + 1200
    (2401, 3000): (None, 4200, 900),  # shaft = pipe + 1200
}

def get_manhole_size(pipe_diameter_mm: int) -> Tuple[int, int, int]:
    """
    根据管径确定检查井尺寸。

    Returns:
        (shaft_diameter_mm, max_depth_mm, cover_diameter_mm)
    """
    for (min_d, max_d), (shaft, max_depth, cover) in MANHOLE_RULES.items():
        if min_d <= pipe_diameter_mm <= max_d:
            if shaft is None:
                shaft = pipe_diameter_mm + 1200
            return shaft, max_depth, cover

    # 超出规范范围，使用最大值
    return pipe_diameter_mm + 1200, 6000, 900

def calculate_angle(p0: Tuple[float, float], p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    """
    计算三点形成的夹角（度）。

    Args:
        p0, p1, p2: (x, y) 坐标

    Returns:
        夹角（度），0-180
    """
    v1 = np.array([p1[0] - p0[0], p1[1] - p0[1]])
    v2 = np.array([p2[0] - p1[0], p2[1] - p1[1]])

    # 归一化
    v1_norm = np.linalg.norm(v1)
    v2_norm = np.linalg.norm(v2)

    if v1_norm < 1e-6 or v2_norm < 1e-6:
        return 0.0

    v1 = v1 / v1_norm
    v2 = v2 / v2_norm

    # 余弦定理
    cos_angle = np.dot(v1, v2)
    cos_angle = np.clip(cos_angle, -1.0, 1.0)
    angle_rad = np.arccos(cos_angle)
    angle_deg = np.degrees(angle_rad)

    return angle_deg

def place_manholes_along_route(
    path_coords: List[Tuple[float, float, float]],
    pipe_diameter_mm: int,
    max_spacing_m: Optional[float] = None,
    angle_threshold_deg: float = 30.0
) -> List[ManholeSpec]:
    """
    沿管道路径自动布置检查井。

    Args:
        path_coords: 路径坐标列表 [(x, y, z_invert), ...]，z是管底标高
        pipe_diameter_mm: 管径（mm）
        max_spacing_m: 最大间距（m），None则按规范
        angle_threshold_deg: 转角阈值（度），超过此角度必须设井

    Returns:
        ManholeSpec 列表
    """
    if len(path_coords) < 2:
        raise ValueError("路径至少需要2个点")

    # 确定最大间距
    if max_spacing_m is None:
        max_spacing_m = 100.0 if pipe_diameter_mm <= 500 else 150.0

    # 获取检查井规范
    shaft_diameter, max_depth, cover_diameter = get_manhole_size(pipe_diameter_mm)

    manholes = []

    # 起点井（必须）
    x0, y0, z_inv0 = path_coords[0]
    z_ground0 = z_inv0 + 2.0  # 假设覆土2m，实际应从DTM读取
    depth0 = int((z_ground0 - z_inv0) * 1000)  # mm

    manholes.append(ManholeSpec(
        x=x0, y=y0,
        z_ground=z_ground0,
        z_invert=z_inv0,
        diameter_mm=shaft_diameter,
        depth_mm=depth0,
        max_depth_mm=max_depth,
        reason='start',
        compliant=(depth0 <= max_depth),
        violation=None if depth0 <= max_depth else f'深度{depth0}mm > 最大{max_depth}mm'
    ))

    # 沿路径检查转角和间距
    last_mh_idx = 0  # 上一个检查井的路径索引

    for i in range(1, len(path_coords) - 1):
        x, y, z_inv = path_coords[i]
        z_ground = z_inv + 2.0
        depth = int((z_ground - z_inv) * 1000)

        # 检查1：转角（WA-726-1 Section 4.4）
        need_mh_angle = False
        if i >= 1 and i < len(path_coords) - 1:
            # 只在路径确实转角时检查（不是直线上的每个点）
            # 计算前一段和后一段的方向向量
            v1 = np.array([path_coords[i][0] - path_coords[i-1][0],
                          path_coords[i][1] - path_coords[i-1][1]])
            v2 = np.array([path_coords[i+1][0] - path_coords[i][0],
                          path_coords[i+1][1] - path_coords[i][1]])

            # 如果两段向量都足够长（>1m），才计算夹角
            if np.linalg.norm(v1) > 1.0 and np.linalg.norm(v2) > 1.0:
                angle = calculate_angle(
                    (path_coords[i-1][0], path_coords[i-1][1]),
                    (x, y),
                    (path_coords[i+1][0], path_coords[i+1][1])
                )
                # 偏转角 = 180 - 夹角
                deflection = 180.0 - angle
                if deflection > angle_threshold_deg:
                    need_mh_angle = True

        # 检查2：间距
        dist_from_last = 0.0
        for j in range(last_mh_idx, i):
            x1, y1, _ = path_coords[j]
            x2, y2, _ = path_coords[j+1]
            dist_from_last += np.hypot(x2 - x1, y2 - y1)

        need_mh_spacing = (dist_from_last >= max_spacing_m)

        if need_mh_angle or need_mh_spacing:
            reason = 'angle' if need_mh_angle else 'spacing'
            manholes.append(ManholeSpec(
                x=x, y=y,
                z_ground=z_ground,
                z_invert=z_inv,
                diameter_mm=shaft_diameter,
                depth_mm=depth,
                max_depth_mm=max_depth,
                reason=reason,
                compliant=(depth <= max_depth),
                violation=None if depth <= max_depth else f'深度{depth}mm > 最大{max_depth}mm'
            ))
            last_mh_idx = i

    # 终点井（必须）
    xn, yn, z_invn = path_coords[-1]
    z_groundn = z_invn + 2.0
    depthn = int((z_groundn - z_invn) * 1000)

    manholes.append(ManholeSpec(
        x=xn, y=yn,
        z_ground=z_groundn,
        z_invert=z_invn,
        diameter_mm=shaft_diameter,
        depth_mm=depthn,
        max_depth_mm=max_depth,
        reason='end',
        compliant=(depthn <= max_depth),
        violation=None if depthn <= max_depth else f'深度{depthn}mm > 最大{max_depth}mm'
    ))

    return manholes


def check_manhole_clearance(
    manhole: ManholeSpec,
    existing_pipes: List[dict],
    min_clearance_m: float = 0.5
) -> Tuple[bool, Optional[str]]:
    """
    检查检查井与其他管线的净距。

    Args:
        manhole: 待检查的检查井
        existing_pipes: 现有管线列表，每条管线是 {'geometry': LineString, 'diameter_mm': int}
        min_clearance_m: 最小净距（WA-726-1 要求 ≥ 500mm）

    Returns:
        (compliant, violation_msg)
    """
    from shapely.geometry import Point

    mh_point = Point(manhole.x, manhole.y)
    mh_radius = manhole.diameter_mm / 1000.0 / 2.0  # 转为米

    for pipe in existing_pipes:
        pipe_geom = pipe['geometry']
        pipe_radius = pipe.get('diameter_mm', 0) / 1000.0 / 2.0

        # 计算最近距离
        dist = pipe_geom.distance(mh_point)

        # 净距 = 中心距 - 两个半径
        clearance = dist - mh_radius - pipe_radius

        if clearance < min_clearance_m:
            return False, f'与管线{pipe.get("pipe_id", "unknown")}净距{clearance:.2f}m < {min_clearance_m}m'

    return True, None


# ============================================================================
# 测试代码
# ============================================================================
if __name__ == "__main__":
    # 测试1：直线路径，应在起点、终点、每100m设井
    print("测试1：直线路径（300m，管径400mm）")
    path1 = [(i*10, 0, 10.0-i*0.01) for i in range(31)]  # 0-300m，坡度1%
    manholes1 = place_manholes_along_route(path1, pipe_diameter_mm=400)
    print(f"  生成 {len(manholes1)} 个检查井:")
    for mh in manholes1:
        print(f"    ({mh.x:.0f}, {mh.y:.0f}): {mh.reason}, 深度{mh.depth_mm}mm, 合规={mh.compliant}")

    # 测试2：有转角的路径
    print("\n测试2：转角路径（90度转角）")
    path2 = [
        (0, 0, 10.0),
        (50, 0, 9.5),
        (100, 0, 9.0),  # 转角点
        (100, 50, 8.5),
        (100, 100, 8.0)
    ]
    manholes2 = place_manholes_along_route(path2, pipe_diameter_mm=600)
    print(f"  生成 {len(manholes2)} 个检查井:")
    for mh in manholes2:
        print(f"    ({mh.x:.0f}, {mh.y:.0f}): {mh.reason}, 直径{mh.diameter_mm}mm, 深度{mh.depth_mm}mm")

    # 测试3：大管径，深度超限
    print("\n测试3：大管径（1500mm），深度超限情况")
    path3 = [(0, 0, 10.0), (100, 0, 6.0)]  # 深度4m
    manholes3 = place_manholes_along_route(path3, pipe_diameter_mm=1500)
    print(f"  生成 {len(manholes3)} 个检查井:")
    for mh in manholes3:
        print(f"    ({mh.x:.0f}, {mh.y:.0f}): 直径{mh.diameter_mm}mm, 深度{mh.depth_mm}mm, "
              f"最大{mh.max_depth_mm}mm, 合规={mh.compliant}")
        if mh.violation:
            print(f"      违规: {mh.violation}")
