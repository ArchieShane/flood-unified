# -*- coding: utf-8 -*-
"""生成耦合运行用的 .inp：把 ALLOW_PONDING 关掉。

耦合运行里地表那一层交给二维算，一维不能再自己在节点上「摊一个 100 m² 的水洼」——
那既是重复计量，也会让 SWMM 的 FLOOD 不等于「真正离开一维的水」，对不上账。
"""
import os, pathlib, re, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from workdir import WORK
from couple_2d import SUFFIX

SCEN = os.environ.get("C2D_SCEN", "")

name = sys.argv[1] if len(sys.argv) > 1 else "Mussafah"
state = sys.argv[2] if len(sys.argv) > 2 else "L1"
src = WORK / f"{name}{SUFFIX[state]}{SCEN}.inp"
d2 = WORK / "c2d"; d2.mkdir(exist_ok=True)
dst = d2 / f"{name}_{state}{SCEN}_2way.inp"
n = 0
out = []
for ln in src.open(encoding="utf-8", errors="replace"):
    if re.match(r"^\s*ALLOW_PONDING\s+", ln, re.I):
        out.append("ALLOW_PONDING        NO\n"); n += 1
    else:
        out.append(ln)
dst.write_text("".join(out), encoding="utf-8")
print(f"{dst.name}：ALLOW_PONDING 改成 NO（{n} 处）")
