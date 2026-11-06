#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""在 Docker 容器里并行运行全市一维 SWMM（避免中文路径问题）"""
import sys

inp_base = sys.argv[1] if len(sys.argv) > 1 else "/work/out/l2plus/swmm/City_L1_gov_61mm1h_p"
n_parts = int(sys.argv[2]) if len(sys.argv) > 2 else 8

from swmm.toolkit import solver
import time

for i in range(1, n_parts + 1):
    inp = f"{inp_base}{i}.inp"
    rpt = f"{inp_base}{i}.rpt"
    out = f"{inp_base}{i}.out"

    print(f"\n=== Part {i}/{n_parts} ===", flush=True)
    print(f"inp: {inp}", flush=True)

    t0 = time.time()
    try:
        solver.swmm_run(inp, rpt, out)
        elapsed = (time.time() - t0) / 60
        print(f"✓ Part {i} completed in {elapsed:.1f} min", flush=True)
    except Exception as e:
        elapsed = (time.time() - t0) / 60
        print(f"✗ Part {i} failed after {elapsed:.1f} min: {e}", flush=True)

print("\n=== All parts completed ===", flush=True)
