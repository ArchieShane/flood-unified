"""RD-A 试点整条链（轻量调度，数据标准 v0.2 第 10 章第一阶段）：按依赖顺序跑，每步都记血缘。

  python -m tools.dgov.run_rda [--from <步骤>] [--no-hash]

步骤：raw → standardize → clean → integrate（含融合后质检）→ resolve → issues → review_map → process → un（Esri Utility Network 校验）→ publish（L1 v1.3-暂定）。
--from 从中间某步开始（前面的产物要已经在）。原始层要算哈希（3 份共约 35 GB，几分钟），--no-hash 只在调试时用。
"""
from __future__ import annotations

import argparse
import sys
import time

STEPS = ["raw", "standardize", "clean", "integrate", "resolve", "issues", "review_map", "process", "un", "publish"]
SOURCES = ["dmt_stormwater", "aecom_rda", "dtm_auh"]
NET_SOURCES = ["dmt_stormwater", "aecom_rda"]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.run_rda")
    ap.add_argument("--from", dest="start", default="raw", choices=STEPS)
    ap.add_argument("--no-hash", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    from . import clean, integrate, issues, process, publish, raw, resolve, review_map, standardize, un
    from .common import load
    t0 = time.time()
    todo = STEPS[STEPS.index(a.start):]
    for step in todo:
        print(f"\n=== {step}（{time.time() - t0:.0f} s）", flush=True)
        if step == "raw":
            for s in SOURCES:
                raw.register(s, do_hash=not a.no_hash)
        elif step == "standardize":
            for s in NET_SOURCES:
                standardize.standardize(s, "rda")
        elif step == "clean":
            for s in NET_SOURCES:
                clean.clean(s, "rda")
        elif step == "integrate":
            integrate.integrate("integrate_rda.yaml")
            integrate.qa(load("integrate_rda.yaml")["extent"])
        elif step == "resolve":
            resolve.resolve("rda")
        elif step == "issues":
            issues.build("rda")
        elif step == "review_map":
            review_map.main(["--extent", "rda"])
        elif step == "process":
            process.process("rda")
        elif step == "un":
            un.build("rda")
        elif step == "publish":
            publish.publish("rda", "L1", "v1.3", provisional=True)
    print(f"\n全部完成（{time.time() - t0:.0f} s）")


if __name__ == "__main__":
    main()
