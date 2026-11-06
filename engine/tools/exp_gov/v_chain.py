"""RD-A 治理前后验证：跑一二维（docker anuga-swmm:1.0，couple2way_v2），跑完算指标（v_metrics）。

  python tools/exp_gov/v_chain.py --runs E24:A,E24:R,... [--par 3] [--threads 5]

参数同 RD-A 基线：15 m 网格、交换 60 s、DTM 原值、存流速；模拟 = 1 h 雨 + 4 h（V-9…V-12）。
已有 twoway 结果的跳过；正在跑的容器接着等。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from rb01_chain import clean_env, log  # noqa: E402
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
HOST_WORK = os.environ.get("SWBRAIN_SWMM_WORK_HOST") or WORK.as_posix()
IMAGE, RES, XCH, SIM = "anuga-swmm:1.0", 15, 60, 5.0


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--par", type=int, default=3)
    ap.add_argument("--threads", type=int, default=5)
    a = ap.parse_args(argv)
    (C2D / "logs").mkdir(parents=True, exist_ok=True)
    runs = [tuple(x.split(":")) for x in a.runs.split(",")]
    running = set(subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split())
    jobs, live = [], {}
    for storm, model in runs:
        name = f"VG_{storm}"
        cn = f"vg_{storm}_{model}".lower()
        if not (WORK / f"{name}_{model}.inp").exists():
            r = subprocess.run([sys.executable, HERE / "v_build.py", "--model", model, "--storm", storm], cwd=ROOT, env=clean_env())
            if r.returncode:
                log(f"! 建模失败 {storm} {model}"); continue
        if cn in running:
            live[cn] = (storm, model)
        elif not (C2D / f"twoway_{name}_{model}.json").exists():
            jobs.append((storm, model))
    log(f"一二维 {len(jobs)} 个：{jobs}；接着等 {list(live.values())}")
    while jobs or live:
        while jobs and len(live) < a.par:
            storm, model = jobs.pop(0)
            name, cn = f"VG_{storm}", f"vg_{storm}_{model}".lower()
            subprocess.run(["docker", "rm", "-f", cn], capture_output=True)
            cmd = ["docker", "run", "--rm", "-d", "--name", cn, "-e", f"OMP_NUM_THREADS={a.threads}", "-e", "C2D_DEM_RAW=1", "-e", "C2D_SAVE_UV=1",
                   "-v", f"{HOST_WORK}:/work", IMAGE, "sh", "-c",
                   f"python -u /work/c2d/couple2way_v2.py {name} {model} {RES} {SIM:g} {XCH} > /work/c2d/logs/{name}_{model}.log 2>&1"]
            subprocess.run(cmd, capture_output=True)
            live[cn] = (storm, model)
            log(f"  开始 {storm} {model}")
        time.sleep(60)
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True)
        up = r.stdout.split()
        if r.returncode or not up:
            continue
        for cn in [c for c in live if c not in up]:
            storm, model = live.pop(cn)
            ok = (C2D / f"twoway_VG_{storm}_{model}.json").exists()
            log(f"  {'完成' if ok else '! 失败'} {storm} {model}")
            if ok:
                subprocess.run([sys.executable, HERE / "v_metrics.py", "--storm", storm, "--model", model], cwd=ROOT, env=clean_env())
    log("全部完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
