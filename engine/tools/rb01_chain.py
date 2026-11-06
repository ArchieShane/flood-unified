# -*- coding: utf-8 -*-
"""RB-01 一二维五态整条链：建模 → 一维 L1 → 建 L2 → 算 L1/L2/L2P → 设计并算 v4 → 设计并算 v5 → 后处理 → 系统数据 → 交付。

H60 那次是手工一步步跑的；2026-10-19 用户确认 60 mm 是笔误、按 61 mm 全部重算（--tag H61 --rain 61），整理成这一条。
已有产物的步骤跳过（断了重跑接着走）；--force 全部重做。报告（report_rb01.py）要先有系统截图，不在链里。
用法：python tools/rb01_chain.py --tag H61 --rain 61 [--dur 1] [--from 步骤名] [--force]
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
HOST_WORK = os.environ.get("SWBRAIN_SWMM_WORK_HOST") or WORK.as_posix()
IMAGE = "anuga-swmm:1.0"
PY = sys.executable
RES, XCH = 15, 60
STATES = ["L1", "L2", "L2P", "L2S4", "L2S5"]


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def clean_env() -> dict:
    """子进程环境：去掉指向别家 PROJ / GDAL 数据的变量。2026-10-19 这台机器的系统变量 PROJ_LIB / GDAL_DATA 指向 PostgreSQL 18 自带的
    旧 proj.db，rasterio 读到就报「comes from another PROJ installation」；rasterio / pyproj 的 wheel 自带数据，不需要这两个变量。
    系统变量不动（PostgreSQL 可能要用），只在这条链的子进程里去掉。"""
    e = dict(os.environ)
    for k in ("PROJ_LIB", "PROJ_DATA", "GDAL_DATA"):
        v = e.get(k, "")
        if v and "postgresql" in v.lower():
            e.pop(k)
    return e


def run(cmd, **kw):
    log("$ " + " ".join(map(str, cmd)))
    r = subprocess.run(list(map(str, cmd)), cwd=ROOT, env=clean_env(), **kw)
    if r.returncode:
        raise SystemExit(f"! 失败（{r.returncode}）：{cmd}")


def dock(*cmd, name=None, threads=6, detach=False):
    return ["docker", "run", "--rm", *(["-d", "--name", name] if detach else []), "-e", f"OMP_NUM_THREADS={threads}",
            "-e", "C2D_DEM_RAW=1", "-e", "C2D_SAVE_UV=1", "-v", f"{HOST_WORK}:/work", IMAGE, *map(str, cmd)]


def compute(name, states, sim_h):
    """docker 并行算几个态，等全部结束；没出 twoway_*.json 的报错"""
    thr = max(2, min(8, (os.cpu_count() or 8) // max(len(states), 1) - 1))
    cons = []
    for s in states:
        cn = f"rb01chain_{name}_{s}"
        subprocess.run(["docker", "rm", "-f", cn], capture_output=True)
        run(dock("sh", "-c", f"python -u /work/c2d/couple2way_v2.py {name} {s} {RES} {sim_h:g} {XCH} > /work/c2d/logs/{name}_{s}.log 2>&1",
                 name=cn, threads=thr, detach=True), stdout=subprocess.DEVNULL)
        cons.append(cn)
    while cons:
        time.sleep(60)
        live = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
        for s in states:
            lg = C2D / "logs" / f"{name}_{s}.log"
            tail = lg.read_text(encoding="utf-8", errors="replace").replace("\r", "\n").strip().splitlines()[-1:] if lg.exists() else ["（还没有日志）"]
            log(f"  {s}: {tail[0][:100] if tail else ''}")
        cons = [c for c in cons if c in live]
    bad = [s for s in states if not (C2D / f"twoway_{name}_{s}.json").exists()]
    if bad:
        raise SystemExit(f"! {bad} 容器已退出但没有结果，看 {C2D / 'logs'}")


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True); ap.add_argument("--rain", type=float, required=True); ap.add_argument("--dur", type=float, default=1.0)
    ap.add_argument("--tail", type=float, default=4.0, help="雨停后再模拟几小时")
    ap.add_argument("--from", dest="start", default=None); ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    name = f"RB01_{a.tag}"; sim_h = a.dur + a.tail; tl = a.tag.lower()
    (C2D / "logs").mkdir(parents=True, exist_ok=True)
    bargs = ["--tag", a.tag, "--rain", f"{a.rain:g}", "--dur", f"{a.dur:g}", "--tail", f"{a.tail:g}"]
    out = ROOT / "out"
    steps = [  # (名, 完成标志, 动作)
        ("build_l1", [WORK / f"{name}_L1.inp", out / f"rb01_inputs_{a.tag}_L1.json"], lambda: run([PY, HERE / "rb01_build.py", "l1", *bargs])),
        ("build_l2p", [WORK / f"{name}_L2P.inp"], lambda: run([PY, HERE / "rb01_build.py", "l2p", *bargs])),
        ("run1d_l1", [C2D / f"{name}_L1_1d.rpt"], lambda: run(dock("python", "/work/c2d/_run1d.py", f"{name}_L1", f"{name}_L1_1d", threads=2))),
        ("build_l2", [WORK / f"{name}_L2.inp"], lambda: run([PY, HERE / "rb01_build.py", "l2", *bargs])),
        ("compute_base", [C2D / f"twoway_{name}_{s}.json" for s in ("L1", "L2", "L2P")], lambda: compute(name, ["L1", "L2", "L2P"], sim_h)),
        ("design_v4", [WORK / f"{name}_L2S4.inp", out / f"rb01_L2S4_inputs_{a.tag}.json"], lambda: run([PY, HERE / "rb01_design_l2s.py", "v4", "--tag", a.tag])),
        ("compute_v4", [C2D / f"twoway_{name}_L2S4.json"], lambda: compute(name, ["L2S4"], sim_h)),
        ("design_v5", [WORK / f"{name}_L2S5.inp", out / f"rb01_L2S5_inputs_{a.tag}.json"], lambda: run([PY, HERE / "rb01_design_l2s.py", "v5", "--tag", a.tag])),
        ("compute_v5", [C2D / f"twoway_{name}_L2S5.json"], lambda: compute(name, ["L2S5"], sim_h)),
        ("tif", [C2D / "tif" / f"{name}_{s}_depth_max.tif" for s in STATES], lambda: run([PY, HERE / "npz_to_tif.py", name, *STATES])),
        ("sww", [C2D / "anuga_out" / f"{name}_{s}.sww" for s in STATES],
         lambda: [run(dock("sh", "-c", f"python -u /work/c2d/npz_to_sww.py {name} {s} {RES} > /work/c2d/logs/sww_{name}_{s}.log 2>&1", threads=4)) for s in STATES]),
        ("stats", [out / f"rb01_stats_{a.tag}.json"], lambda: run([PY, HERE / "rb01_post.py", "--tag", a.tag])),
        ("figs", [out / f"rb01_figs_{tl}" / "diff_L2S5_L2P.png"], lambda: run([PY, HERE / "rb01_figs.py", "--tag", a.tag])),
        ("frames", [ROOT / "console" / "data" / f"flood2d_rb01_{tl}" / "manifest.json"],
         lambda: run([PY, HERE / "pilot_frames.py", "--name", name, "--states", ",".join(STATES), "--out", f"flood2d_rb01_{tl}",
                      "--region", "RB-01 汇水区（Parsons RB-01 模型子汇水区并集，11.39 km²）", "--scen", f"{a.rain:g}mm/{a.dur:g}h", "--stats-tag", a.tag])),
        ("console", [ROOT / "console" / "data" / f"flood2d_rb01_{tl}" / "rb01_stats.json"], lambda: run([PY, HERE / "rb01_console.py", "--tag", a.tag])),
        ("ponding", [], lambda: run([PY, "-m", "swbrain.ponding", "--name", name])),
        ("delivery", [], lambda: run([PY, HERE / "export_delivery_rb01.py", "--tag", a.tag])),
    ]
    names = [s[0] for s in steps]
    i0 = names.index(a.start) if a.start else 0
    log(f"{name} · {a.rain:g} mm / {a.dur:g} h · 模拟 {sim_h:g} h · 步骤 {' → '.join(names[i0:])}")
    t0 = time.time()
    for nm, done, act in steps[i0:]:
        if done and not a.force and all(p.exists() for p in done):
            log(f"= {nm}：已有产物，跳过"); continue
        log(f"▶ {nm}"); t = time.time(); act(); log(f"✓ {nm}（{(time.time() - t) / 60:.1f} min）")
    log(f"全部完成（{(time.time() - t0) / 60:.1f} min）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
