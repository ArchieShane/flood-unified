#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""治理后数据适配器：将 Stormwater_AD_reporting.gdb 映射到 build_city1d.py 期望的格式

核心问题：
  - 原来读 DMT_StormWater_AbuDhabi_Processed.gdb 的 PIPELINE 层
  - 现在读 Stormwater_AD_reporting.gdb 的 StormwaterLine 层
  - 字段名完全不同，需要映射

策略：
  - 读 StormwaterLine → 重命名字段 → 保存为临时 GDB
  - 或者：修改 build_city1d.py 的 read_pipes() 函数直接读新格式

先检查字段映射关系
"""
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GDB_GOV = "D:/swbrain_work/exp_gov/07_export/Stormwater_AD_reporting.gdb"

# 字段映射（老名 → 新名）
FIELD_MAP = {
    # 管线层
    "UNITID": "dmt_unitid",           # 唯一 ID
    "StartCode": "???",                # 起点码（需要从几何计算？）
    "EndCode": "???",                  # 终点码
    "StartX": "dmt_xcoord_sta",        # 起点 X
    "StartY": "dmt_ycoord_sta",        # 起点 Y
    "EndX": "dmt_xcoord_end",          # 终点 X
    "EndY": "dmt_ycoord_end",          # 终点 Y
    "StartElev": "dmt_zcoord_sta",     # 起点地面高程
    "EndElev": "dmt_zcoord_end",       # 终点地面高程
    "PIPE_DIAMETER": "diameter",       # 管径（mm）
    "LineLength": "measuredlength",    # 长度（m）
    "INVERT_LEVEL_UPSTREAM": "upelev", # 上游管内底
    "INVERT_LEVEL_DOWNSTREAM": "downelev", # 下游管内底
    "STATUS": "dmt_status",            # 状态
}

def check_mapping():
    """检查字段映射是否正确"""
    import pyogrio

    print("=== 检查治理后数据字段映射 ===\n")

    # 读一行检查
    df = pyogrio.read_dataframe(GDB_GOV, layer="StormwaterLine", max_features=5)

    print("需要的字段 → 新字段名 → 是否存在:")
    missing = []
    for old, new in FIELD_MAP.items():
        exists = new in df.columns if new != "???" else False
        status = "✓" if exists else "✗"
        print(f"  {old:30} → {new:30} {status}")
        if not exists and new != "???":
            missing.append((old, new))

    if "???" in FIELD_MAP.values():
        print("\n⚠️  StartCode / EndCode 需要特殊处理:")
        print("  - Esri UN 数据没有明确的端点码字段")
        print("  - 可能需要从 Associations 表读取")
        print("  - 或从几何端点坐标生成唯一码")

    if missing:
        print(f"\n❌ 缺少 {len(missing)} 个字段，无法直接映射")
        return False

    print("\n检查样例数据:")
    for old, new in list(FIELD_MAP.items())[:5]:
        if new != "???" and new in df.columns:
            print(f"  {old}: {df[new].iloc[0]}")

    return True

def check_associations():
    """检查 Associations 表（可能包含端点连接信息）"""
    import pyogrio

    print("\n=== 检查 Associations 表 ===")
    try:
        df = pyogrio.read_dataframe(GDB_GOV, layer="Associations", max_features=5)
        print(f"字段: {list(df.columns)}")
        print(f"样例:\n{df.head()}")
    except Exception as e:
        print(f"无法读取: {e}")

if __name__ == "__main__":
    check_mapping()
    check_associations()

    print("\n=== 下一步 ===")
    print("1. 确认 StartCode/EndCode 如何获取")
    print("2. 选择策略:")
    print("   A) 预处理：读 StormwaterLine → 映射字段 → 导出为兼容 GDB")
    print("   B) 修改 build_city1d.py：直接支持新格式")
    print("   C) 从治理前的 prep.gdb 提取端点码映射表")
