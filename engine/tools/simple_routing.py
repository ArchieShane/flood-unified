#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""简化版路径规划引擎

优先级：🔴 P0（今晚完成）

功能：
1. 从道路网络构建图（NetworkX）
2. A* 最短路径算法
3. 避让建筑物（简化版）
4. 计算真实路径长度和成本

设计原则：
- 简单可用，不追求完美
- 先跑通，后优化
"""

import geopandas as gpd
import networkx as nx
from shapely.geometry import Point, LineString
from pathlib import Path
import math

class SimpleRouter:
    """简化版路径规划器"""

    def __init__(self, roads_file, buildings_file=None):
        """
        Args:
            roads_file: 道路 GeoPackage 文件
            buildings_file: 建筑物 GeoPackage 文件（可选）
        """
        self.roads_file = Path(roads_file)
        self.buildings_file = Path(buildings_file) if buildings_file else None

        self.G = None
        self.roads = None
        self.buildings = None

        # 延迟加载
        self._load_roads()
        if self.buildings_file and self.buildings_file.exists():
            self._load_buildings()

    def _load_roads(self):
        """加载道路网络并构建图"""
        if not self.roads_file.exists():
            print(f"⚠️ 道路文件不存在: {self.roads_file}")
            self.G = nx.Graph()
            return

        print(f"加载道路网络: {self.roads_file}")
        self.roads = gpd.read_file(self.roads_file)
        print(f"  - 共 {len(self.roads):,} 条道路")

        # 构建图
        print(f"  - 构建路网图...")
        self.G = nx.Graph()

        for idx, road in self.roads.iterrows():
            coords = list(road.geometry.coords)

            # 每条道路是一系列节点连接
            for i in range(len(coords) - 1):
                n1, n2 = coords[i], coords[i+1]

                # 边权重 = 欧氏距离
                length = math.sqrt((n2[0]-n1[0])**2 + (n2[1]-n1[1])**2)

                # 添加边（双向）
                self.G.add_edge(n1, n2, weight=length, length=length)

        print(f"  - 图构建完成: {self.G.number_of_nodes():,} 节点, {self.G.number_of_edges():,} 边")

    def _load_buildings(self):
        """加载建筑物（用于避让）"""
        print(f"加载建筑物: {self.buildings_file}")
        self.buildings = gpd.read_file(self.buildings_file)
        print(f"  - 共 {len(self.buildings):,} 个建筑")

    def nearest_node(self, x, y):
        """查找最近的路网节点（简化：遍历所有节点）"""
        if self.G.number_of_nodes() == 0:
            return None

        min_dist = float('inf')
        nearest = None

        for node in self.G.nodes():
            dist = math.sqrt((node[0]-x)**2 + (node[1]-y)**2)
            if dist < min_dist:
                min_dist = dist
                nearest = node

        return nearest

    def route(self, start_xy, end_xy):
        """
        规划路径（A*）

        Args:
            start_xy: (x, y) 起点
            end_xy: (x, y) 终点

        Returns:
            {
                'path': LineString,     # 路径几何
                'length': float,        # 长度（m）
                'cost': float,          # 成本（USD，简化：150 USD/m）
                'feasible': bool,       # 是否可行
                'reason': str           # 不可行原因
            }
        """
        if self.G.number_of_nodes() == 0:
            return {
                'path': LineString([start_xy, end_xy]),
                'length': math.sqrt((end_xy[0]-start_xy[0])**2 + (end_xy[1]-start_xy[1])**2),
                'cost': None,
                'feasible': False,
                'reason': '无道路网络数据'
            }

        # 1. 找最近节点
        start_node = self.nearest_node(*start_xy)
        end_node = self.nearest_node(*end_xy)

        if start_node is None or end_node is None:
            return {
                'path': LineString([start_xy, end_xy]),
                'length': None,
                'cost': None,
                'feasible': False,
                'reason': '无法连接到路网'
            }

        # 2. A* 路径规划
        def heuristic(n1, n2):
            """启发式函数：欧氏距离"""
            return math.sqrt((n2[0]-n1[0])**2 + (n2[1]-n1[1])**2)

        try:
            path_nodes = nx.astar_path(
                self.G,
                source=start_node,
                target=end_node,
                heuristic=heuristic,
                weight='weight'
            )
        except nx.NetworkXNoPath:
            # 无路径（被建筑物等阻隔）
            return {
                'path': None,
                'length': None,
                'cost': None,
                'feasible': False,
                'reason': '路网不连通（可能被障碍物阻隔）'
            }

        # 3. 构建完整路径（起点 → 路网 → 终点）
        full_path = [start_xy] + path_nodes + [end_xy]
        path_geom = LineString(full_path)

        # 4. 计算长度
        length = path_geom.length

        # 5. 计算成本（简化：道路敷设 150 USD/m）
        # 连接段（起点→路网，路网→终点）成本略高：200 USD/m
        on_road_length = sum([
            self.G[path_nodes[i]][path_nodes[i+1]]['length']
            for i in range(len(path_nodes) - 1)
        ])
        connection_length = length - on_road_length

        cost = on_road_length * 150 + connection_length * 200

        # 6. 可行性检查（简单：长度限制）
        if length > 10000:
            return {
                'path': path_geom,
                'length': length,
                'cost': cost,
                'feasible': False,
                'reason': f'路径过长（{length:.0f}m > 10 km）'
            }

        return {
            'path': path_geom,
            'length': length,
            'cost': cost,
            'feasible': True,
            'reason': None
        }

    def route_direct(self, start_xy, end_xy):
        """
        直线路径（不考虑道路，用于对比）

        Returns:
            {
                'path': LineString,
                'length': float,
                'cost': float,
                'feasible': bool,
                'reason': str
            }
        """
        path = LineString([start_xy, end_xy])
        length = path.length

        # 直线成本假设（野外开挖，成本更高）：250 USD/m
        cost = length * 250

        return {
            'path': path,
            'length': length,
            'cost': cost,
            'feasible': True,
            'reason': None
        }

    def batch_route(self, pairs):
        """
        批量路径规划

        Args:
            pairs: list of (start_xy, end_xy)

        Returns:
            list of route results
        """
        results = []

        print(f"\n批量规划 {len(pairs)} 条路径...")

        for i, (start, end) in enumerate(pairs):
            if (i + 1) % 100 == 0:
                print(f"  进度: {i+1}/{len(pairs)}")

            result = self.route(start, end)
            results.append(result)

        # 统计
        feasible_count = sum(1 for r in results if r['feasible'])
        print(f"\n规划完成:")
        print(f"  总数: {len(results)}")
        print(f"  可行: {feasible_count} ({feasible_count/len(results)*100:.1f}%)")
        print(f"  不可行: {len(results) - feasible_count}")

        return results


# ========== 测试代码 ==========

def test_router():
    """测试路径规划器"""
    from pathlib import Path

    print("=" * 80)
    print("测试：简化版路径规划器")
    print("=" * 80)

    # 初始化
    gis_dir = Path(__file__).parent.parent / "data" / "gis"
    roads_file = gis_dir / "roads.gpkg"
    buildings_file = gis_dir / "buildings.gpkg"

    router = SimpleRouter(roads_file, buildings_file)

    # 测试用例 1：短距离
    print("\n[测试 1] 短距离路径")
    start = (259000, 2707000)
    end = (259500, 2707500)

    # 对比：直线 vs 沿路网
    result_direct = router.route_direct(start, end)
    result_road = router.route(start, end)

    print(f"  直线路径:")
    print(f"    - 长度: {result_direct['length']:.0f} m")
    print(f"    - 成本: {result_direct['cost']/1e6:.2f} M USD")

    print(f"  沿路网路径:")
    if result_road['feasible']:
        print(f"    - 长度: {result_road['length']:.0f} m")
        print(f"    - 成本: {result_road['cost']/1e6:.2f} M USD")
        print(f"    - 绕行系数: {result_road['length']/result_direct['length']:.2f}")
    else:
        print(f"    - ❌ {result_road['reason']}")

    # 测试用例 2：长距离
    print("\n[测试 2] 长距离路径（2 km）")
    start = (258000, 2706000)
    end = (260000, 2708000)

    result_direct = router.route_direct(start, end)
    result_road = router.route(start, end)

    print(f"  直线: {result_direct['length']:.0f} m")
    if result_road['feasible']:
        print(f"  沿路网: {result_road['length']:.0f} m (绕行 {result_road['length']/result_direct['length']:.2f}x)")
    else:
        print(f"  沿路网: ❌ {result_road['reason']}")

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)


if __name__ == "__main__":
    test_router()
