#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为 RDA_96B 重新生成候选（使用空间路由）

直接调用 l2opt 的候选生成逻辑，但替换所有距离计算为基于 corridor_cost 的最小成本路径。
"""
import sys, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

# 设置环境变量启用空间路由
os.environ['L2OPT_USE_SPATIAL_ROUTING'] = '1'
os.environ['L2OPT_RUN'] = 'RDA_96B'

# 导入并执行候选生成
from l2opt_candidates import main

if __name__ == "__main__":
    sys.argv = ['l2opt_candidates.py', '--run', 'RDA_96B']
    raise SystemExit(main())
