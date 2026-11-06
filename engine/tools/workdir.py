# -*- coding: utf-8 -*-
"""SWMM 工作目录：放 .inp / .rpt / .out。

这些文件很大（RB3 一份 .inp 就 142 MB，一次运行再产出 135 MB 的 .out），
而且是客户模型的派生物，所以放在 out/ 下、不入库。
换机器跑先用 strip_baseline.py 从客户交付的 .inp 重建。
"""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK") or (ROOT / "out" / "l2plus" / "swmm"))
WORK.mkdir(parents=True, exist_ok=True)
OUT = ROOT / "out" / "three_lines"          # 三态页读的产物目录
