#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L2opt 最小成本路径模块（基于 corridor_cost.tif）

替换 l2opt_candidates.py 里所有的 `hypot(dx,dy) * ROUTE_K`。

用法：
    from l2opt_least_cost_path import LeastCostRouter

    router = LeastCostRouter(run="RDA_96B")
    result = router.route(x0, y0, x1, y1)
    # result['length'] 替换原来的 hypot × ROUTE_K
    # result['feasible'] False → 跳过该候选
"""
from __future__ import annotations
import numpy as np
import rasterio
from pathlib import Path
from skimage.graph import MCP_Geometric

class LeastCostRouter:
    def __init__(self, run: str, root: Path = None):
        """
        Args:
            run: 运行名（如 RDA_96B）
            root: 项目根目录（默认自动检测）
        """
        if root is None:
            root = Path(__file__).resolve().parent.parent

        self.run = run
        self.cost_file = root / "out" / "l2opt" / run / "corridor_cost.tif"

        if not self.cost_file.exists():
            raise FileNotFoundError(f"Corridor cost file not found: {self.cost_file}")

        # 加载成本栅格
        with rasterio.open(self.cost_file) as src:
            self.cost = src.read(1)
            self.transform = src.transform
            self.nodata = src.nodata
            self.res = src.res[0]

        # 不可通行的格子设为极大值（MCP_Geometric 会绕开）
        if self.nodata is not None:
            impassable = (self.cost >= self.nodata * 0.9)  # 接近 nodata 的都视为不可通行
            self.cost[impassable] = 1e9

        # MCP_Geometric 要求 cost > 0
        self.cost = np.maximum(self.cost, 1e-6)

    def _xy_to_rc(self, x, y):
        """世界坐标 → 栅格行列"""
        col = int((x - self.transform.c) / self.transform.a)
        row = int((y - self.transform.f) / self.transform.e)
        return row, col

    def _rc_to_xy(self, r, c):
        """栅格行列 → 世界坐标（格子中心）"""
        x = self.transform.c + (c + 0.5) * self.transform.a
        y = self.transform.f + (r + 0.5) * self.transform.e
        return x, y

    def _in_bounds(self, r, c):
        """检查行列是否在栅格范围内"""
        return 0 <= r < self.cost.shape[0] and 0 <= c < self.cost.shape[1]

    def route(self, x0, y0, x1, y1, return_path=False):
        """
        计算从 (x0, y0) 到 (x1, y1) 的最小成本路径。

        Args:
            x0, y0: 起点（UTM 坐标）
            x1, y1: 终点
            return_path: 是否返回路径坐标（默认 False，只返回长度）

        Returns:
            {
                'length': float,        # 路径长度（m），考虑成本后的等效长度
                'feasible': bool,       # 是否可行
                'reason': str or None,  # 不可行原因
                'cost': float,          # 累计成本
                'path': [(x,y), ...] or None  # 路径坐标（仅 return_path=True）
            }
        """
        # 转换到栅格坐标
        r0, c0 = self._xy_to_rc(x0, y0)
        r1, c1 = self._xy_to_rc(x1, y1)

        # 检查边界
        if not self._in_bounds(r0, c0):
            return {
                'length': np.hypot(x1 - x0, y1 - y0),
                'feasible': False,
                'reason': 'start out of bounds',
                'cost': None,
                'path': None
            }

        if not self._in_bounds(r1, c1):
            return {
                'length': np.hypot(x1 - x0, y1 - y0),
                'feasible': False,
                'reason': 'end out of bounds',
                'cost': None,
                'path': None
            }

        # 检查起点和终点是否可通行
        if self.cost[r0, c0] >= 1e8:
            return {
                'length': np.hypot(x1 - x0, y1 - y0),
                'feasible': False,
                'reason': 'start impassable',
                'cost': None,
                'path': None
            }

        if self.cost[r1, c1] >= 1e8:
            return {
                'length': np.hypot(x1 - x0, y1 - y0),
                'feasible': False,
                'reason': 'end impassable',
                'cost': None,
                'path': None
            }

        # 计算最小成本路径
        try:
            mcp = MCP_Geometric(self.cost, fully_connected=True)
            cum_cost, traceback_arr = mcp.find_costs([(r0, c0)], [(r1, c1)])

            # 检查是否找到路径
            if np.isinf(cum_cost[r1, c1]):
                return {
                    'length': np.hypot(x1 - x0, y1 - y0),
                    'feasible': False,
                    'reason': 'no path found',
                    'cost': None,
                    'path': None
                }

            # 提取路径
            path_rc = mcp.traceback((r1, c1))
            path_rc = list(path_rc)

            if len(path_rc) < 2:
                return {
                    'length': np.hypot(x1 - x0, y1 - y0),
                    'feasible': False,
                    'reason': 'path too short',
                    'cost': None,
                    'path': None
                }

            # 计算路径长度（实际几何长度，不是成本）
            # MCP_Geometric 的 cum_cost 是加权成本，不是几何距离
            # 我们需要重新计算几何长度
            path_xy = [self._rc_to_xy(r, c) for r, c in path_rc]
            length = sum(
                np.hypot(path_xy[i+1][0] - path_xy[i][0], path_xy[i+1][1] - path_xy[i][1])
                for i in range(len(path_xy) - 1)
            )

            result = {
                'length': length,
                'feasible': True,
                'reason': None,
                'cost': float(cum_cost[r1, c1]),
                'path': path_xy if return_path else None
            }

            return result

        except Exception as e:
            return {
                'length': np.hypot(x1 - x0, y1 - y0),
                'feasible': False,
                'reason': f'routing error: {e}',
                'cost': None,
                'path': None
            }


# 测试代码
if __name__ == "__main__":
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parent.parent

    print("Testing LeastCostRouter...")

    try:
        router = LeastCostRouter("RDA_96B", ROOT)
        print(f"  Loaded cost raster: {router.cost.shape}, res={router.res}m")

        # 测试：一条已知穿建筑的直线
        x0, y0 = 229745, 2707006
        x1, y1 = 229000, 2707500

        result = router.route(x0, y0, x1, y1)
        straight = np.hypot(x1 - x0, y1 - y0)

        print(f"\n  Test route: ({x0}, {y0}) → ({x1}, {y1})")
        print(f"    Straight line: {straight:.0f} m")
        print(f"    Routed: {result['length']:.0f} m")
        print(f"    Feasible: {result['feasible']}")
        if not result['feasible']:
            print(f"    Reason: {result['reason']}")
        else:
            print(f"    Detour factor: {result['length'] / straight:.2f}")

        print("\n  Test OK")

    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)
