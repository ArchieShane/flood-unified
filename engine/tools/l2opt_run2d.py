# -*- coding: utf-8 -*-
"""L2+suggested 优化试点：并行跑几个一二维（docker couple2way_v2），等全部结束。复用 rb01_chain 的容器启动与等待。

用法：python tools/l2opt_run2d.py --runs RB01_S96D1:L2P,RB01_S96D24:L2P [--threads 6]
      每个「运行名:态」要先有 $WORK/<运行>_<态>.inp 与 c2d/pilot_<运行>_<态>.json、c2d/dem_<运行>.tif；
      模拟时长取 out/rb01_inputs_<tag>_L1.json 的 sim_h（tag = 运行名去掉 RB01_）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import rb01_chain as RC  # noqa: E402


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--runs", required=True); ap.add_argument("--threads", type=int, default=6)
    a = ap.parse_args(argv)
    jobs = [r.split(":") for r in a.runs.split(",")]
    cons = []
    for name, st in jobs:
        pfx, tag = name.split("_", 1)
        sim = json.loads((ROOT / "out" / f"{pfx.lower()}_inputs_{tag}_L1.json").read_text(encoding="utf-8"))["sim_h"]
        cn = f"l2opt_{name}_{st}"
        subprocess.run(["docker", "rm", "-f", cn], capture_output=True)
        RC.run(RC.dock("sh", "-c", f"python -u /work/c2d/couple2way_v2.py {name} {st} {RC.RES} {sim:g} {RC.XCH} > /work/c2d/logs/{name}_{st}.log 2>&1",
                       name=cn, threads=a.threads, detach=True), stdout=subprocess.DEVNULL)
        cons.append((cn, name, st))
        RC.log(f"开始 {name} {st}（模拟 {sim:g} h，{a.threads} 线程）")
    while cons:
        time.sleep(120)
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True)
        if r.returncode != 0:                   # Docker 接口偶发 500（2026-10-26 14:28）：这一轮不算，免得把还在跑的容器当成已退出
            continue
        live = r.stdout.split()
        for cn, name, st in list(cons):
            if cn not in live:
                ok = (RC.C2D / f"twoway_{name}_{st}.json").exists()
                RC.log(f"{'✓' if ok else '!'} {name} {st} {'完成' if ok else '容器退出但没有结果'}")
                cons.remove((cn, name, st))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
