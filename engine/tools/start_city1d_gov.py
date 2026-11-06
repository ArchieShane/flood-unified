#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全市一维模型生成 - 治理后数据快速启动脚本

用途：用治理后 Round 14 数据 + 61mm/1h 生成全市一维模型
方法：临时修改 build_city1d.py 的全局变量，然后调用其 main()

用法：
    python tools/start_city1d_gov.py L1 --parts 8 --sim 6
"""
import sys
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

# 导入原脚本
import build_city1d

# 修改全局变量（治理后数据 + 61mm/1h）
build_city1d.GDB = "D:/swbrain_work/exp_gov/07_export/Stormwater_AD_reporting.gdb"
build_city1d.P_MM = 61.0      # 61 mm
build_city1d.DUR_H = 1.0      # 1 小时
build_city1d.SIM_H = 6.0      # 演算 6 小时

print("=== 全市一维（治理后数据，61mm/1h）===")
print(f"数据源: {build_city1d.GDB}")
print(f"暴雨: {build_city1d.P_MM} mm / {build_city1d.DUR_H} h")
print(f"模拟: {build_city1d.SIM_H} h")
print()

# 调用原脚本的 main
if __name__ == "__main__":
    # 参数传递：python start_city1d_gov.py L1 --parts 8 --sim 6
    # → build_city1d.main(["L1", "--parts", "8", "--sim", "6"])
    sys.exit(build_city1d.main())
