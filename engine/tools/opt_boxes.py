# -*- coding: utf-8 -*-
"""L2+ 箱涵库容优化（第 1 步：位置不动，只调各座之间的库容分配）。

为什么（2026-09-28）：现有箱涵是「按缺口 × 1.5 定容」一次设计、一次验证出来的，峰值蓄水显示 NP4–6 装满、NP1 / NP3 只装到两三成，
不是最优。这里用差分进化在一维模型上搜库容分配，目标 = 井盖溢流总量最小（一维没有积水面积，先用溢流代替，最后用一二维确认）。

约束与假设（都写进产物）：
  · 总可用库容固定（= 起点方案的总量）；每座在起点的 lo–hi 倍之间；可用水深 2.0 m 不变，所以库容只改平面面积
  · 进水管 / 孔口 / 闸门规则 / 排空泵不动（第 2 步再调）
  · 放大后的占地**没有复核用地**：结果里标出每座的新面积，要回到用地图层核
  · 雨只用 61 mm / 2 h；演算截到 12 h（与 72 h 的溢流差 < 0.1%）

用法：python tools/opt_boxes.py --base Mussafah_L2box_R61D2_72h --iters 8 --pop 5 --jobs 5
产物：$WORK/opt/<base>/ 下各次模型；out/opt/<base>_opt.json（每次评估、最优解、起点对照）
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import pathlib
import re
import sys
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

USABLE_D = 2.0      # 可用水深 m（超高 0.3 m 不算）


def load(base):
    t = (WORK / f"{base}.inp").read_text(encoding="utf-8", errors="ignore")
    t = re.sub(r"^END_DATE\s+\S+", "END_DATE  01/01/2000", t, flags=re.M)
    t = re.sub(r"^END_TIME\s+\S+", "END_TIME  12:00:00", t, count=1, flags=re.M)
    t = re.sub(r"^THREADS\s+\d+", "THREADS 4", t, flags=re.M)
    boxes = {}
    for m in re.finditer(r"^(NP\d+)_Curve\s+Storage\s+0\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$", t, re.M):
        boxes[m.group(1)] = float(m.group(2))
    return t, boxes


def with_areas(t, areas):
    for k, a in areas.items():
        t = re.sub(rf"^({k}_Curve\s+Storage\s+0\s+)[\d.]+(\s+[\d.]+\s+)[\d.]+\s*$", rf"\g<1>{a:.1f}\g<2>{a:.1f}", t, flags=re.M)
    return t


def node_flood(rpt):
    t = pathlib.Path(rpt).read_text(errors="ignore")
    blk = t.split("Node Flooding Summary", 1)[1].split("Storage Volume Summary", 1)[0]
    tot = n = 0
    for ln in blk.splitlines():
        p = ln.split()
        if len(p) == 7 and ":" in p[4]:
            tot += float(p[5]); n += 1
    peak = {}
    sv = t.split("Storage Volume Summary", 1)[1].split("Outfall Loading Summary", 1)[0]
    for ln in sv.splitlines():
        p = ln.split()
        if len(p) == 10 and p[0].startswith("NP") and ":" in p[8]:
            peak[p[0]] = float(p[5]) * 1000
    return tot * 1000, n, peak


def _eval(args):
    t, areas, d, tag = args
    from swmm.toolkit import solver
    p = d / f"{tag}.inp"
    p.write_text(with_areas(t, areas), encoding="utf-8")
    try:
        solver.swmm_run(str(p), str(d / f"{tag}.rpt"), str(d / f"{tag}.out"))
        f, n, pk = node_flood(d / f"{tag}.rpt")
    except Exception as e:  # noqa: BLE001
        return tag, areas, None, 0, {}, str(e)
    (d / f"{tag}.out").unlink(missing_ok=True)
    return tag, areas, f, n, pk, None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="Mussafah_L2box_R61D2_72h")
    ap.add_argument("--iters", type=int, default=8)
    ap.add_argument("--pop", type=int, default=5, help="差分进化种群系数（种群 = pop × 箱涵数）")
    ap.add_argument("--jobs", type=int, default=5)
    ap.add_argument("--lo", type=float, default=0.3)
    ap.add_argument("--hi", type=float, default=2.0)
    a = ap.parse_args()
    t, A0 = load(a.base)
    ids = sorted(A0); A0v = np.array([A0[k] for k in ids]); TOT = float(A0v.sum())
    d = WORK / "opt" / a.base; d.mkdir(parents=True, exist_ok=True)
    print(f"{a.base}：{len(ids)} 座箱涵，总可用库容 {TOT * USABLE_D:,.0f} m³（固定）；每座 {a.lo}–{a.hi} 倍")
    hist = []
    pool = mp.Pool(a.jobs)
    k = [0]

    def to_areas(x):
        # x = 各座相对起点的倍数 → 归一到总面积不变，再截到上下限（截完再归一一次，偏差很小）
        w = np.clip(np.asarray(x), a.lo, a.hi) * A0v
        for _ in range(5):
            w = np.clip(w * TOT / w.sum(), a.lo * A0v, a.hi * A0v)
        return {i: float(v) for i, v in zip(ids, w)}

    def batch(X):
        jobs = []
        for x in X:
            k[0] += 1; jobs.append((t, to_areas(x), d, f"e{k[0]:04d}"))
        out = []
        for tag, ar, f, n, pk, err in pool.map(_eval, jobs):
            hist.append({"tag": tag, "areas_m2": ar, "vol_m3": {i: round(v * USABLE_D) for i, v in ar.items()},
                         "flood_m3": f, "n_flood_nodes": n, "peak_m3": pk, "err": err})
            out.append(f if f is not None else 1e12)
        return out

    t0 = time.time()
    f0 = batch([np.ones(len(ids))])[0]
    print(f"起点：溢流 {f0:,.0f} m³（{time.time() - t0:.0f} s）", flush=True)
    from scipy.optimize import differential_evolution

    def vec(X):          # scipy 以 (n_params, S) 传入整批
        return np.array(batch(np.asarray(X).T))

    gen = [0]

    def cb(xk, convergence=None):
        gen[0] += 1
        best = min((h for h in hist if h["flood_m3"] is not None), key=lambda h: h["flood_m3"])
        print(f"  第 {gen[0]} 代：已评估 {len(hist)} 次，最好 {best['flood_m3']:,.0f} m³（{(best['flood_m3'] / f0 - 1) * 100:+.1f}%）"
              f"　{time.time() - t0:.0f} s", flush=True)

    differential_evolution(vec, bounds=[(a.lo, a.hi)] * len(ids), vectorized=True, popsize=a.pop,
                                 maxiter=a.iters, seed=1, polish=False, callback=cb, updating="deferred", init="sobol")
    pool.close()
    ok = [h for h in hist if h["flood_m3"] is not None]
    best = min(ok, key=lambda h: h["flood_m3"])
    base_h = hist[0]
    out = {"base": a.base, "objective": "井盖溢流总量最小（一维，12 h）", "total_usable_m3": round(TOT * USABLE_D),
           "bounds_x": [a.lo, a.hi], "n_eval": len(hist), "wall_s": round(time.time() - t0),
           "start": {"flood_m3": base_h["flood_m3"], "vol_m3": base_h["vol_m3"], "peak_m3": base_h["peak_m3"]},
           "best": {"tag": best["tag"], "flood_m3": best["flood_m3"], "vol_m3": best["vol_m3"], "areas_m2": best["areas_m2"], "peak_m3": best["peak_m3"]},
           "gain_pct": round((best["flood_m3"] / f0 - 1) * 100, 2),
           "assumptions": ["箱涵位置、进水管、孔口、闸门规则、排空泵不动；只改平面面积（可用水深 2.0 m）",
                           "放大后的占地没有复核用地", "雨只用 61 mm / 2 h", "目标是一维井盖溢流总量，不是积水面积"],
           "history": hist}
    od = ROOT / "out" / "opt"; od.mkdir(parents=True, exist_ok=True)
    (od / f"{a.base}_opt.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n最优 {best['tag']}：溢流 {best['flood_m3']:,.0f} m³，比起点 {out['gain_pct']:+.1f}%；共 {len(hist)} 次、{out['wall_s']} s")
    for i in ids:
        print(f"  {i}: {base_h['vol_m3'][i]:>6,} → {best['vol_m3'][i]:>6,} m³　峰值蓄水 {base_h['peak_m3'].get(i, 0):>6,.0f} → {best['peak_m3'].get(i, 0):>6,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
