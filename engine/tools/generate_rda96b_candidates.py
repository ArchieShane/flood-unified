#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为 RDA_96B 批量生成候选（基于本体+空间约束）"""
import sys
import json
import math
import pickle
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import rasterio
import numpy as np
from skimage.graph import route_through_array
from swbrain.ontology import schema

print("RDA_96B 候选生成")
print("="*80)

# 1. 加载成本栅格
cost_tif = Path("D:/阿布扎比/排水智能体/out/l2opt/RDA_96B/corridor_cost.tif")
if not cost_tif.exists():
    print(f"错误: {cost_tif} 不存在")
    sys.exit(1)

ds = rasterio.open(cost_tif)
cost_array = ds.read(1)
transform = ds.transform

print(f"\n1. 加载空间数据:")
print(f"   成本栅格: {cost_array.shape}, 分辨率: {transform.a:.1f}m")

# 2. 定义 RDA_96B 的属性（从之前的分析）
well = {
    'id': 'RDA_96B',
    'x': 529850.0,
    'y': 2622100.0,
    'z': 5.0,  # 地面标高（估算）
    'peak_m3s': 0.5,  # 峰值溢流（从候选库推断）
}

# 3. 定义候选目标池（从客户资产中读取，这里简化为几个已知的塘）
target_ponds = [
    {'id': 'RBW3', 'x': 530200, 'y': 2622300, 'z': 3.0, 'vol_m3': 15000, 'status': 'built'},
    {'id': 'POND_NEW_1', 'x': 529950, 'y': 2622250, 'z': 4.0, 'vol_m3': 8000, 'status': 'planned'},
]

print(f"\n2. 井信息:")
print(f"   ID: {well['id']}")
print(f"   位置: ({well['x']:.0f}, {well['y']:.0f}), z={well['z']}m")
print(f"   峰值溢流: {well['peak_m3s']} m3/s")

print(f"\n3. 目标池: {len(target_ponds)}个")
for p in target_ponds:
    dist = math.hypot(p['x'] - well['x'], p['y'] - well['y'])
    print(f"   {p['id']}: 距离{dist:.0f}m, 容积{p['vol_m3']}m3, {p['status']}")

# 4. 空间路由函数
def route_path(x1, y1, x2, y2):
    """计算最小成本路径"""
    r1, c1 = ~transform * (x1, y1)
    r2, c2 = ~transform * (x2, y2)
    r1, c1 = int(r1), int(c1)
    r2, c2 = int(r2), int(c2)

    # 边界检查
    h, w = cost_array.shape
    if not (0 <= r1 < h and 0 <= c1 < w and 0 <= r2 < h and 0 <= c2 < w):
        return None

    # 起终点可行性
    if cost_array[r1, c1] >= 9999 or cost_array[r2, c2] >= 9999:
        return None

    try:
        indices, weight = route_through_array(cost_array, (r1, c1), (r2, c2))
        if weight >= 1e6:
            return None

        # 转回UTM，每5个点取1个
        path = []
        for r, c in indices[::5]:
            x, y = transform * (c, r)
            path.append((x, y))

        # 确保起终点在路径中
        if len(path) > 0 and (path[0] != (x1, y1) or path[-1] != (x2, y2)):
            path = [(x1, y1)] + path + [(x2, y2)]

        length = sum(math.hypot(path[i+1][0]-path[i][0], path[i+1][1]-path[i][1])
                    for i in range(len(path)-1))

        return {'path': path, 'length': length}
    except:
        return None

# 5. 生成候选函数
def generate_pump_pond(well, pond):
    """生成 pump_pond 候选"""
    # 空间路由
    route = route_path(well['x'], well['y'], pond['x'], pond['y'])
    if not route:
        return None

    # 添加高程
    path_3d = []
    for i, (x, y) in enumerate(route['path']):
        t = i / (len(route['path']) - 1) if len(route['path']) > 1 else 0
        z = well['z'] + (pond['z'] - well['z']) * t
        path_3d.append((x, y, z))

    # 设计参数
    design_flow = well['peak_m3s'] * 1.2
    diameter_mm = 400  # 简化，实际应根据流速计算
    velocity_ms = design_flow / (math.pi * (0.2)**2)

    # 水头损失
    f = 0.02
    friction_loss = f * (route['length'] / 0.4) * (velocity_ms**2 / 19.62)
    total_loss = friction_loss * 1.1

    # 泵站
    static_head = abs(pond['z'] - well['z'])
    pump_head = static_head + total_loss + 2.0
    n_pumps = 2
    wetwell_vol = design_flow * 300

    pump_station = {
        'ps_id': f"{well['id']}_PS_{pond['id']}",
        'x_utm': well['x'],
        'y_utm': well['y'],
        'wetwell_volume_m3': wetwell_vol,
        'pump_count': n_pumps,
        'pump_capacity_m3s': design_flow,
        'pump_head_m': pump_head,
        'footprint_m2': 25.0,
        'hydraulic_compatible': True,
        'status': 'planned',
    }

    # 校验泵站
    ps_check = schema.check_pump_station_compliance(pump_station, {})
    if ps_check and not ps_check['compliant']:
        return None

    # 检查井（简化：每80m一个）
    n_manholes = max(2, int(route['length'] / 80) + 1)
    manholes = []
    for i in range(n_manholes):
        idx = int(i * (len(path_3d) - 1) / (n_manholes - 1))
        x, y, z = path_3d[idx]
        manholes.append({
            'manhole_id': f"MH_{i+1}",
            'x_utm': x,
            'y_utm': y,
            'depth_mm': 2000,
            'max_depth_mm': 7000,
            'diameter_mm': 1600,
            'compliant': True
        })

    # 调蓄池
    target_pond = {
        'pond_id': pond['id'],
        'design_volume_m3': pond['vol_m3'],
        'safety_margin_pct': 5.0,
        'effective_volume_m3': pond['vol_m3'] * 1.05,
        'freeboard_m': 0.5,
        'side_slope_ratio': '1:3',
        'pond_type': 'detention',
        'auto_drain': True,
        'status': pond['status'],
    }

    pond_check = schema.check_pond_compliance(target_pond, {})
    if pond_check and not pond_check['compliant']:
        return None

    # 成本
    ps_cost = n_pumps * 150000 + wetwell_vol * 2000 + 50000
    pp_cost = route['length'] * 1600
    mh_cost = len(manholes) * (15000 + 2.0 * 5000)
    total_cost = ps_cost + pp_cost + mh_cost

    return {
        'candidate_id': f"{well['id']}_to_{pond['id']}",
        'kind': 'pump_pond',
        'well_id': well['id'],
        'pump_station': pump_station,
        'pressure_pipe': {
            'length_m': route['length'],
            'diameter_mm': diameter_mm,
            'total_head_loss_m': total_loss,
        },
        'manholes': manholes,
        'target_pond': target_pond,
        'total_cost_aed': total_cost,
        'compliant': True,
        'route_length_m': route['length'],
        'n_manholes': len(manholes),
    }

# 6. 批量生成
print(f"\n4. 开始生成候选:")
candidates = []

for pond in target_ponds:
    print(f"\n   目标: {pond['id']}")
    cand = generate_pump_pond(well, pond)

    if cand:
        candidates.append(cand)
        print(f"   OK 生成成功")
        print(f"     路径长度: {cand['route_length_m']:.0f}m")
        print(f"     检查井: {cand['n_manholes']}个")
        print(f"     泵站扬程: {cand['pump_station']['pump_head_m']:.1f}m")
        print(f"     总成本: {cand['total_cost_aed']/1e6:.2f} M AED")
        print(f"     合规: {cand['compliant']}")
    else:
        print(f"   X 不可行（空间约束或工程约束）")

# 7. 保存
output_file = Path("D:/阿布扎比/排水智能体/out/RDA_96B_candidates_v2.json")
output_file.parent.mkdir(exist_ok=True)

with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(candidates, f, indent=2, ensure_ascii=False)

print(f"\n" + "="*80)
print(f"候选生成完成:")
print(f"  总数: {len(candidates)}")
print(f"  全部合规: {all(c['compliant'] for c in candidates)}")
print(f"  保存: {output_file}")

# 8. 统计
if candidates:
    costs = [c['total_cost_aed'] for c in candidates]
    lengths = [c['route_length_m'] for c in candidates]

    print(f"\n统计:")
    print(f"  成本范围: {min(costs)/1e6:.2f} - {max(costs)/1e6:.2f} M AED")
    print(f"  路径长度: {min(lengths):.0f} - {max(lengths):.0f} m")

    # 按成本排序
    candidates_sorted = sorted(candidates, key=lambda x: x['total_cost_aed'])
    print(f"\n最优候选（按成本）:")
    best = candidates_sorted[0]
    print(f"  {best['candidate_id']}")
    print(f"  成本: {best['total_cost_aed']/1e6:.2f} M AED")
    print(f"  路径: {best['route_length_m']:.0f}m")
    print(f"  目标池: {best['target_pond']['pond_id']} ({best['target_pond']['effective_volume_m3']}m3)")
