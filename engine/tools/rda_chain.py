# -*- coding: utf-8 -*-
"""RD-A 基线整条链（2026-10-20）：9 场雨 × 四态（L1 / L2 / L2P / L2PB）建模 → 一维 L1（L2 选接收井用）→ 一二维 → 热点指标。

雨：33 / 61 / 96 mm × 1 / 2 / 24 h（用户约定），标签 S{雨量}D{历时}；雨后模拟 10 h（同优化试点：判据 C 看雨停后 6 h 能否退水）。
先算优化要用的两场控制雨（S96D1、S96D24），再算其余。已有结果的跳过；--force 重做。
用法：python tools/rda_chain.py [--storms S96D1,S96D24,...] [--states L1,L2,L2P,L2PB] [--par 4] [--threads 5] [--tail 10]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from rb01_chain import clean_env, log  # noqa: E402
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
HOST_WORK = os.environ.get("SWBRAIN_SWMM_WORK_HOST") or WORK.as_posix()
IMAGE = "anuga-swmm:1.0"
PY = sys.executable
RES, XCH = 15, 60
ALL = ["S96D1", "S96D24", "S61D1", "S61D2", "S61D24", "S33D1", "S33D2", "S33D24", "S96D2"]


def run(cmd):
    log("$ " + " ".join(map(str, cmd)))
    r = subprocess.run(list(map(str, cmd)), cwd=ROOT, env=clean_env())
    if r.returncode:
        raise SystemExit(f"! 失败（{r.returncode}）：{cmd}")


def parse(tag):
    mm, h = tag[1:].split("D")
    return float(mm), float(h)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--storms", default=",".join(ALL)); ap.add_argument("--states", default="L1,L2,L2P,L2PB")
    ap.add_argument("--par", type=int, default=4); ap.add_argument("--threads", type=int, default=5); ap.add_argument("--tail", type=float, default=10.0)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    (C2D / "logs").mkdir(parents=True, exist_ok=True)
    states = a.states.split(",")
    jobs, live = [], {}
    running = set(subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split())
    for tag in a.storms.split(","):
        mm, h = parse(tag); name = f"RDA_{tag}"
        bargs = ["--tag", tag, "--rain", f"{mm:g}", "--dur", f"{h:g}", "--tail", f"{a.tail:g}"]
        if a.force or not (WORK / f"{name}_L1.inp").exists():
            run([PY, HERE / "rda_build.py", "l1", *bargs])
        rpt = C2D / f"{name}_L1_1d.rpt"
        if a.force or not rpt.exists():
            run([PY, "-c", "import swmm.toolkit.solver as s,sys; s.swmm_run(sys.argv[1], sys.argv[2], sys.argv[3])",
                 WORK / f"{name}_L1.inp", rpt, C2D / f"{name}_L1_1d.out"])
        for st in states:
            if st != "L1" and (a.force or not (WORK / f"{name}_{st}.inp").exists()):
                run([PY, HERE / "rda_build.py", st.lower(), *bargs])
            cn = f"rdachain_{tag}_{st}"
            if cn in running:                       # 上一次链留下、还在跑的容器：接着等它，不重开
                live[cn] = (tag, st); log(f"  接着等 {tag} {st}（容器还在跑）")
            elif a.force or not (C2D / f"twoway_{name}_{st}.json").exists():
                jobs.append((tag, st, mm, h))
    log(f"二维 {len(jobs)} 个：{[f'{t}_{s}' for t, s, *_ in jobs]}")
    while jobs or live:
        while jobs and len(live) < a.par:
            tag, st, mm, h = jobs.pop(0); name = f"RDA_{tag}"; cn = f"rdachain_{tag}_{st}"
            subprocess.run(["docker", "rm", "-f", cn], capture_output=True)
            sim = h + a.tail
            cmd = ["docker", "run", "--rm", "-d", "--name", cn, "-e", f"OMP_NUM_THREADS={a.threads}", "-e", "C2D_DEM_RAW=1", "-e", "C2D_SAVE_UV=1",
                   "-v", f"{HOST_WORK}:/work", IMAGE, "sh", "-c",
                   f"python -u /work/c2d/couple2way_v2.py {name} {st} {RES} {sim:g} {XCH} > /work/c2d/logs/{name}_{st}.log 2>&1"]
            subprocess.run(cmd, capture_output=True)
            live[cn] = (tag, st); log(f"  开始 {tag} {st}（模拟 {sim:g} h）")
        time.sleep(60)
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True)
        up = r.stdout.split()
        if r.returncode or not up:
            # 2026-10-20：docker ps 偶尔失败或返回空，链把 8 个还在跑的容器当成「失败」又开新的，CPU 超订。这一轮不判
            log(f"  docker ps 没拿到列表（{r.returncode}），这一轮跳过"); continue
        for cn in [c for c in live if c not in up]:
            tag, st = live.pop(cn)
            ok = (C2D / f"twoway_RDA_{tag}_{st}.json").exists()
            log(f"  {'完成' if ok else '! 失败'} {tag} {st}")
            if ok:
                subprocess.run([PY, HERE / "rda_baseline_summary.py", "--tag", tag, "--state", st], cwd=ROOT, env=clean_env(), capture_output=True)
    log("全部完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
