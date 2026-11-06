# -*- coding: utf-8 -*-
"""全市一维模型生成器 - 治理后数据版（Gov Round 14）

用途：从治理后的 Stormwater_AD_reporting.gdb 重建全市一维管网
暴雨：61 mm / 1 h
用于：全市三级防线特急简化方案（100m 二维）

用法：
    python tools/build_city1d_gov.py
产物：$SWBRAIN_SWMM_WORK/City_L1_gov_61mm1h.inp（统一 inp，稍后分片）
"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from workdir import WORK

# 治理后数据（Round 14）
GDB_GOV = "D:/swbrain_work/exp_gov/07_export/Stormwater_AD_reporting.gdb"

# 暴雨参数（61mm / 1h）
P_MM, DUR_H = 61.0, 1.0
SIM_H = 6.0  # 雨后 5 小时

# 其他参数（和原来一致）
DATUM = 100.0
COVER_MED = 1.61
COVER_MIN, COVER_MAX = 0.30, 20.0
N_MANN = 0.013
MIN_LEN = 1.0
CN = 82.0
TC_MIN = 30.0
K_RADIUS = 1.2
CELL = 20.0
DEAD_STATUS = {"Abandoned", "Expired", "Removed"}

def main():
    print(f"=== 全市一维模型生成（治理后数据，61mm/1h）===")
    print(f"数据源: {GDB_GOV}")
    print(f"暴雨: {P_MM} mm / {DUR_H} h")
    print(f"模拟时长: {SIM_H} h")
    print(f"输出: {WORK}/City_L1_gov_61mm1h.inp")

    # TODO: 实际建模逻辑
    # 1. 读 StormwaterLine, StormwaterJunction, StormwaterDevice
    # 2. 拓扑连接
    # 3. 生成 inp

    print("\n❌ 占位脚本，需要完整实现")
    print("核心改动点：")
    print("  - GDB 改为 Stormwater_AD_reporting.gdb")
    print("  - 暴雨改为 61mm/1h")
    print("  - 产流按最近节点法（和原来一致）")
    print("\n下一步：复用 build_city1d.py 的完整逻辑，只改数据源和参数")

if __name__ == "__main__":
    main()
