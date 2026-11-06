#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""端到端测试：验证现有候选库的空间约束问题

目标：
1. 加载现有候选库（RD-A，3109项）
2. 为每个候选生成简单的管道几何（直线）
3. 用约束检查器检查
4. 统计问题严重性

输出：
- 有多少候选穿建筑？
- 有多少候选穿海？
- 有多少候选超长？
- 可行率是多少？

这个测试将证明：当前候选库确实存在严重的空间约束问题
"""

import sys
import json
from pathlib import Path
from shapely.geometry import LineString
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from simple_constraint_checker import SimpleConstraintChecker

ROOT = Path(__file__).resolve().parent.parent

def load_existing_candidates(run="RDA_96B"):
    """加载现有候选库"""
    file = ROOT / "out" / "l2opt" / run / "candidates.json"

    print(f"加载候选库: {file}")

    if not file.exists():
        print(f"  ❌ 文件不存在")
        return []

    with open(file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    candidates = data.get('candidates', [])
    print(f"  ✅ 已加载 {len(candidates)} 个候选")

    return candidates

def add_simple_geometry(candidates):
    """为候选添加简单几何（直线管道）"""
    print(f"\n为候选添加几何信息...")

    count_with_pipe = 0

    for cand in candidates:
        kind = cand.get('kind')

        # 只有涉及管道的类型才有管道几何
        if kind in ['pump_outfall', 'pump_newpond', 'pump_pond', 'gravity']:
            # 起点：井口
            well = cand.get('well')
            if well and 'xy' in cand:
                x0, y0 = cand['xy']
            else:
                continue

            # 终点：根据类型不同
            if kind == 'pump_outfall':
                # 出水口
                if 'of_xy' in cand:
                    x1, y1 = cand['of_xy']
                else:
                    continue

            elif kind in ['pump_newpond', 'pump_pond', 'gravity']:
                # 调蓄池
                if 'pond_xy' in cand:
                    x1, y1 = cand['pond_xy']
                else:
                    continue
            else:
                continue

            # 创建直线管道
            pipe_geom = LineString([(x0, y0), (x1, y1)])
            pipe_length = pipe_geom.length

            cand['pipe_geom'] = pipe_geom
            cand['pipe_length'] = pipe_length

            count_with_pipe += 1

    print(f"  ✅ 已为 {count_with_pipe} 个候选添加管道几何")

    return candidates

def test_existing_candidates(run="RDA_96B"):
    """测试现有候选库"""
    print("=" * 80)
    print(f"端到端测试：验证现有候选库（{run}）")
    print("=" * 80)

    # 1. 加载候选库
    candidates = load_existing_candidates(run)

    if len(candidates) == 0:
        print("\n❌ 无候选数据，测试终止")
        return

    # 2. 添加简单几何
    candidates = add_simple_geometry(candidates)

    # 3. 初始化约束检查器
    print("\n初始化约束检查器...")
    gis_dir = ROOT / "data" / "gis"
    checker = SimpleConstraintChecker(gis_dir)

    # 4. 批量检查
    print("\n开始批量检查...")
    results = checker.batch_check(candidates)

    # 5. 详细统计
    print("\n" + "=" * 80)
    print("详细统计")
    print("=" * 80)

    # 按约束类型统计
    violation_stats = {}

    for result in results['results']:
        for violation in result['violations']:
            constraint = violation['constraint']
            if constraint not in violation_stats:
                violation_stats[constraint] = {
                    'count': 0,
                    'severity': violation['severity']
                }
            violation_stats[constraint]['count'] += 1

    print("\n违反约束统计:")
    for constraint, stats in sorted(violation_stats.items()):
        print(f"  {constraint} ({stats['severity']}): {stats['count']} 次")

    # 按设施类型统计
    print("\n按设施类型统计:")
    by_kind = {}

    for i, result in enumerate(results['results']):
        kind = candidates[i].get('kind', 'unknown')

        if kind not in by_kind:
            by_kind[kind] = {'total': 0, 'feasible': 0, 'infeasible': 0}

        by_kind[kind]['total'] += 1
        if result['feasible']:
            by_kind[kind]['feasible'] += 1
        else:
            by_kind[kind]['infeasible'] += 1

    for kind, stats in sorted(by_kind.items()):
        feasible_rate = stats['feasible'] / stats['total'] * 100
        print(f"  {kind}:")
        print(f"    - 总数: {stats['total']}")
        print(f"    - 可行: {stats['feasible']} ({feasible_rate:.1f}%)")
        print(f"    - 不可行: {stats['infeasible']} ({100-feasible_rate:.1f}%)")

    # 6. 保存报告
    report = {
        'run': run,
        'total_candidates': results['total'],
        'feasible': results['feasible'],
        'infeasible': results['infeasible'],
        'feasible_rate': results['feasible_rate'],
        'violation_stats': violation_stats,
        'by_kind': by_kind
    }

    report_file = ROOT / "out" / "l2opt" / run / "constraint_check_report.json"
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n📊 报告已保存: {report_file}")

    # 7. 生成可行候选列表
    feasible_candidates = [
        candidates[i]
        for i, result in enumerate(results['results'])
        if result['feasible']
    ]

    feasible_file = ROOT / "out" / "l2opt" / run / "candidates_feasible.json"
    with open(feasible_file, 'w', encoding='utf-8') as f:
        json.dump({
            'run': run,
            'original_count': len(candidates),
            'feasible_count': len(feasible_candidates),
            'candidates': feasible_candidates
        }, f, indent=2, ensure_ascii=False, default=str)

    print(f"📋 可行候选已保存: {feasible_file}")

    # 8. 关键结论
    print("\n" + "=" * 80)
    print("🎯 关键结论")
    print("=" * 80)

    print(f"\n原候选库: {results['total']} 项")
    print(f"可行候选: {results['feasible']} 项 ({results['feasible_rate']:.1f}%)")
    print(f"不可行: {results['infeasible']} 项 ({100-results['feasible_rate']:.1f}%)")

    if results['feasible_rate'] < 50:
        print("\n⚠️ 警告：超过一半的候选不可行！")
        print("   这证明了空间约束问题的严重性。")

    if 'C001_NO_BUILDING' in violation_stats:
        count = violation_stats['C001_NO_BUILDING']['count']
        print(f"\n🏢 穿建筑问题: {count} 项 ({count/results['total']*100:.1f}%)")

    if 'C003_SEA_MAX_LENGTH' in violation_stats:
        count = violation_stats['C003_SEA_MAX_LENGTH']['count']
        print(f"\n🌊 海底管过长: {count} 项 ({count/results['total']*100:.1f}%)")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    import sys

    run = sys.argv[1] if len(sys.argv) > 1 else "RDA_96B"
    test_existing_candidates(run)
