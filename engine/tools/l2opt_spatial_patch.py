#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L2opt 空间路由补丁：猴子补丁 l2opt_candidates.py，替换所有的 hypot × ROUTE_K

在 l2opt_candidates.py 开头 import 这个模块，它会替换距离计算逻辑。
"""
import math
import os
from pathlib import Path

# 全局路由器实例（延迟初始化）
_router = None
_use_spatial = os.environ.get('L2OPT_USE_SPATIAL_ROUTING') == '1'

def get_router():
    """获取路由器实例（延迟加载）"""
    global _router
    if _router is None and _use_spatial:
        run = os.environ.get('L2OPT_RUN', 'RDA_96B')
        from l2opt_least_cost_path import LeastCostRouter
        root = Path(__file__).resolve().parent.parent
        _router = LeastCostRouter(run, root)
        print(f"  [Spatial routing enabled for {run}]")
    return _router

def spatial_distance(x0, y0, x1, y1):
    """
    替换 hypot(dx,dy) * ROUTE_K 的函数。

    如果环境变量 L2OPT_USE_SPATIAL_ROUTING=1，使用最小成本路径；
    否则回退到直线 × ROUTE_K。

    Returns:
        (length, feasible): 管道长度和可行性
    """
    router = get_router()

    if router is None:
        # 回退：直线 × 1.2
        straight = math.hypot(x1 - x0, y1 - y0)
        return straight * 1.2, True

    # 空间路由
    result = router.route(x0, y0, x1, y1)
    return result['length'], result['feasible']

# 猴子补丁：替换 math.hypot（仅在空间模式下）
if _use_spatial:
    _original_hypot = math.hypot

    # 我们不能直接替换 math.hypot，因为它在其他地方也被使用
    # 改为提供一个包装函数供候选生成调用
    pass

# 导出给 l2opt_candidates.py 使用
__all__ = ['spatial_distance', 'get_router', '_use_spatial']
