#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全市 L2/L3 总量估算（特急简化方案）

输入：L1 一维结果（溢流总量）
输出：L2/L3 措施总量（不给空间分布）
"""
import json
from pathlib import Path

# 读取 L1 结果
l1_file = Path('out/citywide_gov_61mm1h_L1_summary.json')
l1 = json.loads(l1_file.read_text(encoding='utf-8'))

total_overflow_m3 = l1['summary']['total_flood_m3']
total_inflow_m3 = l1['summary']['total_inflow_m3']

print("=== 全市三级防线总量估算（61mm/1h）===\n")
print(f"L1 现状:")
print(f"  产流总量:     {total_inflow_m3/1e6:>8.1f} 万 m³")
print(f"  管网溢流:     {total_overflow_m3/1e6:>8.1f} 万 m³")
print(f"  排走占比:     {l1['summary']['conveyance_pct']:>8.1f} %")

# L2 估算（基于 RB-01/RD-A 经验）
print(f"\nL2 临时措施（总量估算）:")

# 井口截流泵（假设平均每台处理 400 m³ 溢流）
n_pumps_intercept = int(total_overflow_m3 / 400)
cost_pumps_intercept = n_pumps_intercept * 300000  # USD

# 地面取水泵（假设占截流泵的 20%）
n_pumps_surface = int(n_pumps_intercept * 0.2)
cost_pumps_surface = n_pumps_surface * 300000

n_pumps_total = n_pumps_intercept + n_pumps_surface

# 新塘（需要存 1.5× 溢流量）
pond_volume_m3 = int(total_overflow_m3 * 1.5)
n_ponds_estimate = int(pond_volume_m3 / 30000)  # 假设平均每座塘 3 万 m³
cost_ponds = pond_volume_m3 * 40  # USD/m³

# 重力管 + 压力管（假设平均每台泵配 200m 管）
pipes_km = n_pumps_intercept * 0.2
cost_pipes = pipes_km * 1000 * 800  # USD/m

total_cost = cost_pumps_intercept + cost_pumps_surface + cost_ponds + cost_pipes

print(f"  截流泵（井口）:  {n_pumps_intercept:>8,} 台")
print(f"  地面取水泵:      {n_pumps_surface:>8,} 台")
print(f"  泵总数:          {n_pumps_total:>8,} 台")
print(f"  新塘容积:        {pond_volume_m3/1e6:>8.1f} 万 m³（约 {n_ponds_estimate} 座）")
print(f"  管线:            {pipes_km:>8.1f} km")
print(f"  占位成本:        {total_cost/1e6:>8.1f} 百万 USD")

# L2 效果估算（假设平均效能 70%）
overflow_reduction_pct = 70
residual_overflow_m3 = total_overflow_m3 * (1 - overflow_reduction_pct / 100)

print(f"\nL2 效果（估算）:")
print(f"  溢流减少:        {overflow_reduction_pct:>8.0f} %")
print(f"  残余溢流:        {residual_overflow_m3/1e6:>8.1f} 万 m³")

# L3 资源需求（基于残余溢流）
# 假设：每台泵车 6h 能抽 50 m³，需要在 12h 内退干
pump_trucks_needed = int(residual_overflow_m3 / (50 * 2))
tanker_trips = int(residual_overflow_m3 * 0.3 / 17)  # 30% 用罐车，17 m³/车
crew_persons = pump_trucks_needed * 2

print(f"\nL3 应急调度（资源需求）:")
print(f"  泵车:            {pump_trucks_needed:>8,} 台")
print(f"  罐车车次:        {tanker_trips:>8,} 车次")
print(f"  班组人员:        {crew_persons:>8,} 人")
print(f"  预计时长:        12 小时")

# 保存结果
result = {
    "storm": "61 mm / 1 h",
    "data_source": "Governance Round 14",
    "method": "特急简化方案（总量估算，无空间分布）",
    "L1_baseline": {
        "total_inflow_m3": total_inflow_m3,
        "total_overflow_m3": total_overflow_m3,
        "conveyance_pct": l1['summary']['conveyance_pct']
    },
    "L2_measures_aggregate": {
        "pumps_intercept": n_pumps_intercept,
        "pumps_surface": n_pumps_surface,
        "pumps_total": n_pumps_total,
        "ponds_m3": pond_volume_m3,
        "ponds_count_estimate": n_ponds_estimate,
        "pipes_km": round(pipes_km, 1),
        "cost_usd": int(total_cost),
        "note": "总量估算，基于经验公式，无空间分布"
    },
    "L2_effect_estimate": {
        "overflow_reduction_pct": overflow_reduction_pct,
        "residual_overflow_m3": residual_overflow_m3,
        "note": "基于经验效能估算"
    },
    "L3_resources_estimate": {
        "pump_trucks": pump_trucks_needed,
        "tanker_trips": tanker_trips,
        "crew_persons": crew_persons,
        "duration_h": 12,
        "note": "资源需求估算，无调度方案，无路线"
    }
}

out_file = Path('out/citywide_gov_61mm1h_3lines_summary.json')
out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')

print(f"\n✓ 结果已保存: {out_file}")
print("\n⚠️  重要提醒:")
print("  - 这是特急简化方案，仅供宏观决策")
print("  - L2/L3 只给总量，无空间分布")
print("  - 成本可能偏差 ±30%")
print("  - 正常汇水区工作必须用 30m + 完整优化")
