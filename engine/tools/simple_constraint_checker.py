#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""空间约束检查引擎 - 核心实现

优先级：🔴 P0（今晚完成）

功能：
1. 检查管道是否穿越建筑物
2. 检查管道是否穿越海域（长度限制）
3. 检查管道长度是否超限
4. 返回约束检查结果 + 成本调整系数

设计原则：
- 先实现核心约束（3-5条），不追求完美
- 快速验证方法可行性
- 后续迭代扩展
"""

import geopandas as gpd
from shapely.geometry import Point, LineString, Polygon
from pathlib import Path
import json

class SimpleConstraintChecker:
    """简化版约束检查器（核心3条约束）"""

    def __init__(self, gis_data_dir):
        """
        Args:
            gis_data_dir: GIS 数据目录（data/gis）
        """
        self.data_dir = Path(gis_data_dir)

        # 延迟加载（用时才加载）
        self._buildings = None
        self._sea = None

    @property
    def buildings(self):
        """延迟加载建筑物"""
        if self._buildings is None:
            file = self.data_dir / "buildings.gpkg"
            if file.exists():
                print(f"  加载建筑物: {file}")
                self._buildings = gpd.read_file(file)
                print(f"    - 共 {len(self._buildings):,} 个建筑")
            else:
                print(f"  ⚠️ 建筑物文件不存在: {file}")
                self._buildings = gpd.GeoDataFrame()
        return self._buildings

    @property
    def sea(self):
        """延迟加载海域"""
        if self._sea is None:
            file = self.data_dir / "sea.gpkg"
            if file.exists():
                print(f"  加载海域: {file}")
                self._sea = gpd.read_file(file, layer='surface')
                print(f"    - 共 {len(self._sea):,} 个海域")
            else:
                print(f"  ⚠️ 海域文件不存在: {file}")
                self._sea = gpd.GeoDataFrame()
        return self._sea

    def check_pipe(self, pipe_geometry, pipe_length=None):
        """
        检查单条管道的空间约束

        Args:
            pipe_geometry: LineString（管道几何）
            pipe_length: float（管道长度，m）- 可选，如不提供则计算

        Returns:
            {
                'feasible': bool,  # 是否可行
                'violations': [    # 违反的约束
                    {
                        'constraint': 'C001_NO_BUILDING',
                        'severity': 'FORBIDDEN',
                        'reason': '管道穿越 3 个建筑物',
                        'count': 3
                    },
                    ...
                ],
                'cost_multiplier': float,  # 成本调整系数
                'warnings': []  # 警告（不影响可行性）
            }
        """
        result = {
            'feasible': True,
            'violations': [],
            'warnings': [],
            'cost_multiplier': 1.0
        }

        if pipe_length is None:
            pipe_length = pipe_geometry.length

        # ========== 约束 1：不得穿越建筑物 ==========
        violation = self._check_building_intersection(pipe_geometry)
        if violation:
            result['feasible'] = False
            result['violations'].append(violation)

        # ========== 约束 2：海底管段长度限制 ==========
        violation = self._check_sea_intersection(pipe_geometry)
        if violation:
            if violation['severity'] == 'FORBIDDEN':
                result['feasible'] = False
            result['violations'].append(violation)
            if 'cost_multiplier' in violation:
                result['cost_multiplier'] *= violation['cost_multiplier']

        # ========== 约束 3：管道总长度限制 ==========
        violation = self._check_pipe_length(pipe_length)
        if violation:
            result['warnings'].append(violation)
            if 'cost_multiplier' in violation:
                result['cost_multiplier'] *= violation['cost_multiplier']

        return result

    def _check_building_intersection(self, pipe_geom):
        """约束 C001：管道不得穿越建筑物"""
        if len(self.buildings) == 0:
            return None  # 无建筑数据，跳过

        # 空间查询：哪些建筑与管道相交
        intersects = self.buildings[self.buildings.intersects(pipe_geom)]

        if len(intersects) > 0:
            return {
                'constraint': 'C001_NO_BUILDING',
                'severity': 'FORBIDDEN',
                'reason': f'管道穿越 {len(intersects)} 个建筑物',
                'count': len(intersects),
                'building_ids': intersects['guid'].tolist()[:5]  # 最多列5个
            }

        return None

    def _check_sea_intersection(self, pipe_geom):
        """约束 C003：海底管段最大长度 500m"""
        if len(self.sea) == 0:
            return None  # 无海域数据，跳过

        # 计算海底段长度
        subsea_length = 0
        for _, sea_poly in self.sea.iterrows():
            intersection = pipe_geom.intersection(sea_poly.geometry)
            if not intersection.is_empty:
                if isinstance(intersection, LineString):
                    subsea_length += intersection.length
                elif hasattr(intersection, 'geoms'):
                    # MultiLineString
                    for geom in intersection.geoms:
                        if isinstance(geom, LineString):
                            subsea_length += geom.length

        if subsea_length > 0:
            if subsea_length > 500:
                # 超过 500m，禁止
                return {
                    'constraint': 'C003_SEA_MAX_LENGTH',
                    'severity': 'FORBIDDEN',
                    'reason': f'海底管段 {subsea_length:.0f}m > 500m',
                    'subsea_length_m': subsea_length
                }
            else:
                # 有海底段但未超限，增加成本
                return {
                    'constraint': 'C003_SEA_COST',
                    'severity': 'COST',
                    'reason': f'海底管段 {subsea_length:.0f}m，成本增加10倍',
                    'subsea_length_m': subsea_length,
                    'cost_multiplier': 1.0 + (subsea_length / pipe_geom.length) * 9.0  # 海底部分10倍
                }

        return None

    def _check_pipe_length(self, pipe_length):
        """约束 C005：压力管建议长度 < 5000m"""
        if pipe_length > 5000:
            # 超长管道，增加成本（扬程损失）
            return {
                'constraint': 'C005_PIPE_LENGTH',
                'severity': 'WARNING',
                'reason': f'管道长度 {pipe_length:.0f}m > 5000m，能耗增加',
                'pipe_length_m': pipe_length,
                'cost_multiplier': 1.0 + (pipe_length - 5000) / 5000 * 0.2  # 超出部分成本增加20%
            }

        return None

    def check_candidate(self, candidate_measure):
        """
        检查候选措施（完整设施）

        Args:
            candidate_measure: dict，候选措施
                {
                    'id': 'PX001_OF1_2',
                    'kind': 'pump_outfall',
                    'well_xy': (x0, y0),
                    'outfall_xy': (x1, y1),
                    'pipe_geom': LineString(...),
                    'pipe_length': 1200,
                    ...
                }

        Returns:
            约束检查结果（同 check_pipe）
        """
        pipe_geom = candidate_measure.get('pipe_geom')
        pipe_length = candidate_measure.get('pipe_length')

        if pipe_geom is None:
            # 没有管道（如地面泵站），跳过管道约束
            return {
                'feasible': True,
                'violations': [],
                'warnings': [],
                'cost_multiplier': 1.0
            }

        return self.check_pipe(pipe_geom, pipe_length)

    def batch_check(self, candidates):
        """
        批量检查候选措施

        Args:
            candidates: list of dict

        Returns:
            {
                'total': int,
                'feasible': int,
                'infeasible': int,
                'results': [
                    {
                        'candidate_id': 'PX001_OF1_2',
                        'feasible': False,
                        'violations': [...],
                        ...
                    },
                    ...
                ]
            }
        """
        print(f"\n批量检查 {len(candidates)} 个候选措施...")

        results = []
        feasible_count = 0

        for i, candidate in enumerate(candidates):
            if (i + 1) % 100 == 0:
                print(f"  进度: {i+1}/{len(candidates)}")

            check_result = self.check_candidate(candidate)
            check_result['candidate_id'] = candidate['id']

            if check_result['feasible']:
                feasible_count += 1

            results.append(check_result)

        summary = {
            'total': len(candidates),
            'feasible': feasible_count,
            'infeasible': len(candidates) - feasible_count,
            'feasible_rate': feasible_count / len(candidates) * 100,
            'results': results
        }

        print(f"\n检查完成:")
        print(f"  总数: {summary['total']}")
        print(f"  可行: {summary['feasible']} ({summary['feasible_rate']:.1f}%)")
        print(f"  不可行: {summary['infeasible']} ({100 - summary['feasible_rate']:.1f}%)")

        return summary


# ========== 测试代码 ==========

def test_simple_checker():
    """测试约束检查器"""
    from pathlib import Path

    print("=" * 80)
    print("测试：简化版约束检查器")
    print("=" * 80)

    # 初始化
    gis_dir = Path(__file__).parent.parent / "data" / "gis"
    checker = SimpleConstraintChecker(gis_dir)

    # 测试用例 1：短直线（可能穿建筑）
    print("\n[测试 1] 短直线管道（可能穿建筑）")
    pipe1 = LineString([
        (259000, 2707000),  # 起点
        (259500, 2707500)   # 终点（500m）
    ])
    result1 = checker.check_pipe(pipe1)
    print(f"  结果: {'✅ 可行' if result1['feasible'] else '❌ 不可行'}")
    if result1['violations']:
        print(f"  违反约束:")
        for v in result1['violations']:
            print(f"    - {v['constraint']}: {v['reason']}")

    # 测试用例 2：穿海管道
    print("\n[测试 2] 穿海管道")
    pipe2 = LineString([
        (258000, 2707000),  # 起点（陆地）
        (256000, 2707000)   # 终点（海中，2km）
    ])
    result2 = checker.check_pipe(pipe2)
    print(f"  结果: {'✅ 可行' if result2['feasible'] else '❌ 不可行'}")
    if result2['violations']:
        print(f"  违反约束:")
        for v in result2['violations']:
            print(f"    - {v['constraint']}: {v['reason']}")
    print(f"  成本系数: {result2['cost_multiplier']:.2f}")

    # 测试用例 3：超长管道
    print("\n[测试 3] 超长管道（6km）")
    pipe3 = LineString([
        (259000, 2707000),
        (265000, 2707000)   # 6km
    ])
    result3 = checker.check_pipe(pipe3)
    print(f"  结果: {'✅ 可行' if result3['feasible'] else '❌ 不可行'}")
    if result3['warnings']:
        print(f"  警告:")
        for w in result3['warnings']:
            print(f"    - {w['constraint']}: {w['reason']}")
    print(f"  成本系数: {result3['cost_multiplier']:.2f}")

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)


if __name__ == "__main__":
    test_simple_checker()
