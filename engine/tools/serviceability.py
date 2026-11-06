# -*- coding: utf-8 -*-
"""降雨服务水平：这套系统在多大的雨下才开始涝。

顾问报告里那一行「Precipitation Serviceability 25 mm → 30 mm」没有给定义。
本工具的判据写死在这里，**看数之前先看判据**：

    在给定历时下二分搜索雨深 P，使得**洪水量 ≤ 产流量 × thresh**（默认 1%）。
    落在这个门槛上的最大 P，就是这套系统的「服务水平」。

为什么不是「洪水量为 0」：管网里总有个别低点会冒一点水，卡死 0 会把服务水平
判到不合理的低位，而且对网格与数值噪声过敏。1% 是工程上「基本不涝」的量级，
换阈值数会变，所以阈值随结果一起报。

每问一次雨深要**重算产流并重跑一次 SWMM**，不是拿现成结果缩放 ——
SCS 的净雨在时间上是非线性的（前期雨先填初损），按比例缩放会把峰型弄歪。

演算窗口默认 12 h：2 小时的雨，洪水量在 12 h 内早已定型，
而退水类指标才需要 72 h（见 tools/parsons_table.py）。

用法：
    python tools/serviceability.py Mussafah --state _baseline_R61D2
    python tools/serviceability.py Mussafah --state _L2box_R61D2 --iters 7
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from workdir import WORK  # noqa: E402

DUR_H = 2.0
SIM_H = 12.0
THRESH = 0.01          # 洪水量 ≤ 产流量的百分之几算「不涝」
P_LO, P_HI = 5.0, 90.0
DT_MIN = 1.0           # 入流步长；5 min 解不出 Tc=10 min 的单位线（削峰 5.2%）


def _set_end(text: str, sim_h: float) -> str:
    import datetime as dt

    d0 = re.search(r"START_DATE\s+(\S+)", text)
    t0 = re.search(r"START_TIME\s+(\S+)", text)
    if not d0:
        return text
    s = dt.datetime.strptime(f"{d0.group(1)} {t0.group(1) if t0 else '00:00:00'}",
                             "%m/%d/%Y %H:%M:%S")
    e = s + dt.timedelta(hours=sim_h)
    text = re.sub(r"END_DATE\s+\S+", f"END_DATE  {e.strftime('%m/%d/%Y')}", text, count=1)
    text = re.sub(r"END_TIME\s+\S+", f"END_TIME  {e.strftime('%H:%M:%S')}", text, count=1)
    return text


def flood_m3(rpt: pathlib.Path) -> tuple[float, float]:
    """(洪水量, 入流量)。两种口径都取：ALLOW_PONDING NO 时用 Flooding Loss，
    YES 时那一项恒为 0，只能用 Node Flooding Summary 的累计量。"""
    t = rpt.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"Flow Routing Continuity(.*?)Continuity Error", t, re.S)
    loss = inflow = 0.0
    if m:
        k = re.search(r"Flooding Loss \.+\s*(-?[\d.]+)", m.group(1))
        loss = float(k.group(1)) * 10_000 if k else 0.0
        for key in ("External Inflow", "Wet Weather Inflow"):
            k2 = re.search(re.escape(key) + r" \.+\s*(-?[\d.]+)", m.group(1))
            if k2:
                inflow += float(k2.group(1)) * 10_000
    if loss <= 0:
        b = re.search(r"Node Flooding Summary(.*?)\n\s*\n\s*\n", t, re.S)
        if b:
            for ln in b.group(1).splitlines():
                c = ln.split()
                if len(c) >= 7 and re.match(r"^[\w\-.]+$", c[0]) and re.match(r"^[\d.]+$", c[1]):
                    try:
                        loss += float(c[-2]) * 1000
                    except ValueError:
                        pass
    return loss, inflow


def run_at(model: str, state: str, P: float, cfg, subs, dur_h: float,
           sim_h: float, log=print) -> tuple[float, float]:
    """在雨深 P 下重算产流、重跑一次，返回 (洪水量, 入流量)。"""
    import runoff as R
    from swmm.toolkit import solver

    src = WORK / f"{model}{state}.inp"
    dt_h = DT_MIN / 60.0
    shape = R.read_shape(WORK / f"{model}_baseline.inp", dur_h, log=lambda *a: None)
    t, P_cum = R.hyetograph(P, dur_h, dt_h, shape, 0.0)
    H = R.node_hydrographs(subs, t, P_cum, dt_h, log=lambda *a: None)
    Hk = R.keep_known_nodes(H, src, dt_h, log=lambda *a: None)
    tmp = WORK / f"_svc_{model}{state}.inp"
    R.write_inflows(src, tmp, Hk, t, log=lambda *a: None)
    tmp.write_text(_set_end(tmp.read_text(encoding="utf-8", errors="replace"), sim_h),
                   encoding="utf-8")
    t0 = time.time()
    solver.swmm_run(str(tmp), str(tmp.with_suffix(".rpt")), str(tmp.with_suffix(".out")))
    fl, inf = flood_m3(tmp.with_suffix(".rpt"))
    log(f"    P={P:>5.1f} mm　入流 {inf:>11,.0f}　洪水量 {fl:>11,.0f} m³"
        f"　占 {fl / inf * 100 if inf else 0:>5.2f}%　{(time.time() - t0) / 60:.1f} min")
    return fl, inf


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="二分搜索降雨服务水平")
    ap.add_argument("model", choices=["Mussafah", "Shaliela", "RB3"])
    ap.add_argument("--state", required=True, help="状态后缀，如 _baseline_R61D2")
    ap.add_argument("--dur", type=float, default=DUR_H)
    ap.add_argument("--sim", type=float, default=SIM_H)
    ap.add_argument("--thresh", type=float, default=THRESH)
    ap.add_argument("--iters", type=int, default=6)
    a = ap.parse_args(argv)

    from swbrain import db
    import runoff as R
    cfg = db.load_config(None)
    subs, derived = R.subcatchments(cfg, a.model, log=lambda *a2: None)
    print(f"{a.model}{a.state}：二分搜索「洪水量 ≤ 产流量 × {a.thresh:.0%}」的最大雨深")
    print(f"  历时 {a.dur:g} h　演算 {a.sim:g} h　子汇水区 {len(subs):,}"
          + ("（面积由交付水量反算）" if derived else ""))

    lo, hi = P_LO, P_HI
    best = None
    for it in range(a.iters):
        mid = 0.5 * (lo + hi)
        fl, inf = run_at(a.model, a.state, mid, cfg, subs, a.dur, a.sim)
        if inf <= 0:
            print("  入流为 0，停"); break
        if fl <= inf * a.thresh:
            best = mid; lo = mid          # 还撑得住，往大了试
        else:
            hi = mid
    if best is None:
        print(f"\n  **服务水平 < {P_LO:g} mm** —— 搜索下界都撑不住")
    else:
        print(f"\n  **降雨服务水平 ≈ {best:.1f} mm / {a.dur:g} h**"
              f"（判据：洪水量 ≤ 产流量的 {a.thresh:.0%}）")
        print(f"  区间收敛到 [{lo:.1f}, {hi:.1f}] mm，再细要加 --iters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
