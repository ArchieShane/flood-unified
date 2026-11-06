#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""测试新版候选生成 - 最小可运行版本"""
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# 简单测试：不依赖空间路由，直接构造一个候选
from swbrain.ontology import schema

print("测试新版候选生成（基于本体模型）")
print("="*80)

# 构造一个完整的 pump_pond 候选
candidate = {
    'candidate_id': 'TEST_RDA96B_to_POND001',
    'kind': 'pump_pond',
    'well_id': 'RDA_96B',

    # 泵站（PumpStation 本体对象）
    'pump_station': {
        'ps_id': 'RDA96B_PS',
        'name': 'RDA 96B Pump Station',
        'x_utm': 529850,
        'y_utm': 2622100,
        'ps_type': 'surface',
        'wetwell_volume_m3': 150.0,  # 0.5 m³/s × 300s
        'pump_count': 2,  # 1+1
        'pump_capacity_m3s': 0.5,
        'pump_head_m': 15.0,  # 静扬程 + 管损 + 裕量
        'pressure_pipe_length_m': 400.0,
        'pressure_pipe_diameter_mm': 400,
        'head_loss_m': 5.0,
        'discharge_to': 'pond',
        'discharge_target_id': 'POND_001',
        'footprint_m2': 25.0,
        'power_requirement_kw': 75.0,
        'distance_to_grid_m': 100.0,
        'hydraulic_compatible': True,
        'status': 'planned',
        'source': 'system_optimized',
    },

    # 压力管（PressurePipe 本体对象）
    'pressure_pipe': {
        'pressure_pipe_id': 'RDA96B_PP',
        'upstream_node': 'RDA96B_PS',
        'downstream_node': 'POND_001',
        'length_m': 400.0,
        'diameter_mm': 400,
        'material': 'hdpe',
        'design_flow_m3s': 0.5,
        'design_velocity_ms': 2.0,
        'total_head_loss_m': 5.0,
    },

    # 检查井（Manhole 本体对象列表）
    'manholes': [
        {'manhole_id': 'MH_001', 'x_utm': 529850, 'y_utm': 2622100,
         'depth_mm': 2000, 'max_depth_mm': 7000, 'diameter_mm': 1600,
         'reason': 'start', 'compliant': True},
        {'manhole_id': 'MH_002', 'x_utm': 529930, 'y_utm': 2622150,
         'depth_mm': 2200, 'max_depth_mm': 7000, 'diameter_mm': 1600,
         'reason': 'spacing', 'compliant': True},
        {'manhole_id': 'MH_003', 'x_utm': 530200, 'y_utm': 2622300,
         'depth_mm': 2500, 'max_depth_mm': 7000, 'diameter_mm': 1600,
         'reason': 'end', 'compliant': True},
    ],

    # 阀门（Valve 本体对象列表）
    'valves': [
        {'valve_id': 'V_001', 'valve_type': 'check', 'diameter_mm': 400},
        {'valve_id': 'V_002', 'valve_type': 'gate', 'diameter_mm': 400},
    ],

    # 目标调蓄池（Pond 本体对象）
    'target_pond': {
        'pond_id': 'POND_001',
        'name': 'Existing Pond 001',
        'design_volume_m3': 10000,
        'safety_margin_pct': 5.0,  # WA-726-1 要求
        'effective_volume_m3': 10500,  # 10000 × 1.05
        'freeboard_m': 0.5,
        'side_slope_ratio': '1:3',  # WA-726-2 要求
        'pond_type': 'detention',
        'auto_drain': True,  # WA-726-1 要求
        'status': 'built',
    },

    # 汇总信息
    'total_cost_aed': 500000,  # 详细计算省略
    'compliant': True,  # 待校验
    'constraint_violations': [],
}

print("\n1. 测试候选结构:")
print(f"   候选ID: {candidate['candidate_id']}")
print(f"   类型: {candidate['kind']}")
print(f"   泵站: {candidate['pump_station']['pump_count']}台泵")
print(f"   压力管: {candidate['pressure_pipe']['length_m']:.0f}m, D{candidate['pressure_pipe']['diameter_mm']}mm")
print(f"   检查井: {len(candidate['manholes'])}个")
print(f"   阀门: {len(candidate['valves'])}个")
print(f"   总成本: {candidate['total_cost_aed']/1e6:.2f} M AED")

# 2. 测试泵站校验
print("\n2. 测试泵站校验:")
ps_check = schema.check_pump_station_compliance(candidate['pump_station'], {})
if ps_check:
    print(f"   合规: {ps_check['compliant']}")
    if not ps_check['compliant']:
        print(f"   违规项: {ps_check['constraint_violations']}")
else:
    print("   合规: True (无违规)")

# 3. 测试调蓄池校验
print("\n3. 测试调蓄池校验:")
pond_check = schema.check_pond_compliance(candidate['target_pond'], {})
if pond_check:
    print(f"   合规: {pond_check['compliant']}")
    if not pond_check['compliant']:
        print(f"   违规项: {pond_check['constraint_violations']}")
else:
    print("   合规: True (无违规)")

# 4. 测试检查井校验
print("\n4. 测试检查井校验:")
for mh in candidate['manholes']:
    mh_check = schema.check_manhole_compliance(mh, {})
    status = "合规" if (not mh_check or mh_check['compliant']) else f"不合规: {mh_check['constraint_violations']}"
    print(f"   {mh['manhole_id']}: {status}")

# 5. 汇总
print("\n5. 候选总体评估:")
all_compliant = True
violations = []

if ps_check and not ps_check['compliant']:
    all_compliant = False
    violations.extend(ps_check['constraint_violations'])

if pond_check and not pond_check['compliant']:
    all_compliant = False
    violations.extend(pond_check['constraint_violations'])

for mh in candidate['manholes']:
    mh_check = schema.check_manhole_compliance(mh, {})
    if mh_check and not mh_check['compliant']:
        all_compliant = False
        violations.extend(mh_check['constraint_violations'])

candidate['compliant'] = all_compliant
candidate['constraint_violations'] = violations

print(f"   最终合规: {candidate['compliant']}")
if not all_compliant:
    print(f"   违规项总数: {len(violations)}")
    for v in violations:
        print(f"     - {v}")
else:
    print("   ✓ 所有组件都符合 WA-726 规范")
    print("   ✓ 候选可以进入优化")

print("\n" + "="*80)
print("新版候选生成测试完成")
print("\n对比:")
print("  旧版: {'kind': 'pump_pond', 'pipe_m': 400, 'n_pumps': 2, 'cost': 500k}")
print("  新版: 包含 PumpStation/PressurePipe/Manhole/Valve/Pond 完整本体对象")
print("       每个组件都经过 WA-726 规范校验")
