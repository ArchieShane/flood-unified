# -*- coding: utf-8 -*-
"""换一个降雨场景，把四态对比整条链重走一遍。

**为什么不能只换入流**：塘的排空规则是按雨型调的（24 小时雨峰在第 12 h，所以排空从第 14 h
起判）。2 小时雨的峰在第 1.1 h，沿用那条规则会让塘一直空等到第 14 小时。
而且塘位本来就是按防线1 的积水分布聚类出来的——雨型一换，积水分布也换了。
所以整条链要重走：新雨 → 新的防线1 → 重新选址定容 → 两种构筑物 → 四态。

链条：
  ① 产流       tools/runoff.py 按新雨重算逐节点入流，写进 baseline 与客户原件
  ② 防线1      跑 baseline，得到这场雨下的积水分布
  ③ 防线2      跑客户原件（他们的塘 + 泵，换了雨）
  ④ 选址定容   按新的积水分布重新聚类，排空起始时刻按新雨峰定
  ⑤ 防线2+     两种构筑物各跑一遍

用法：python tools/rain_scenario.py <汇水区> --P 61 --dur 2 --tag R61D2 [--steps 12345]
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from workdir import OUT, WORK
import runoff as R

STATE_SRC = {"L1": "_baseline", "L2": "", "L2B": "_L2box", "L2L": "_L2lag"}


def make_inflows(cfg, model: str, P: float, dur: float, tag: str, dt_min: float = 1.0,
                 log=print):
    """按新雨算入流，写进 baseline 与客户原件两个 .inp。返回雨峰时刻（h）。"""
    src = WORK / f"{model}_baseline.inp"
    dt_h = dt_min / 60.0
    shape = R.read_shape(src, dur, log=log)
    t, P_cum = R.hyetograph(P, dur, dt_h, shape, 0.0)
    subs, derived = R.subcatchments(cfg, model, log=log)
    H = R.node_hydrographs(subs, t, P_cum, dt_h, log=log)
    Q = np.sum(list(H.values()), axis=0)
    ip = int(np.argmax(Q))
    tot = float(Q.sum() * dt_h * 3600)
    log(f"  产流 {tot:,.0f} m³　全网峰值 {Q.max():.2f} m³/s 在第 {t[ip]:.2f} h"
        + ("　（面积按交付水量反算）" if derived else ""))
    for st in ("L1", "L2"):
        s = WORK / f"{model}{STATE_SRC[st]}.inp"
        if s.exists():
            Hk = R.keep_known_nodes(H, s, dt_h, log=log)
            R.write_inflows(s, WORK / f"{model}{STATE_SRC[st]}_{tag}.inp", Hk, t, log=log)
    return float(t[ip]), tot, H, t


def run_swmm(inp: pathlib.Path, log=print) -> pathlib.Path:
    from swmm.toolkit import solver
    rpt, out = inp.with_suffix(".rpt"), inp.with_suffix(".out")
    t0 = time.perf_counter()
    log(f"  [{time.strftime('%H:%M:%S')}] 跑 {inp.name} …")
    solver.swmm_run(str(inp), str(rpt), str(out))
    log(f"  [{time.strftime('%H:%M:%S')}] {(time.perf_counter() - t0) / 60:.1f} min")
    return rpt


POND = re.compile(r"^(NP|PO|STO)[-_]?\d", re.I)


def facts(rpt: pathlib.Path) -> dict:
    t = rpt.read_text(encoding="utf-8", errors="replace")
    b = re.search(r"Node Flooding Summary(.*?)\n\s*\n\s*\n", t, re.S)
    tot = st = 0.0
    n = 0
    if b:
        for ln in b.group(1).splitlines():
            c = ln.split()
            if len(c) >= 7 and re.match(r"^[\w\-.]+$", c[0]) and re.match(r"^[\d.]+$", c[1]):
                try:
                    v = float(c[-2]) * 1000
                except ValueError:
                    continue
                tot += v
                n += 1
                if POND.match(c[0]) or c[0].startswith(("JI_", "JP_")):
                    st += v
    e = re.search(r"Continuity Error \(%\)\s*\.*\s+(-?\d[\d.]*)", t)
    return {"street_m3": tot - st, "struct_m3": st, "n_nodes": n,
            "continuity_pct": float(e.group(1)) if e else None}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="换降雨场景，重走四态对比")
    ap.add_argument("model", choices=["Mussafah", "Shaliela", "RB3"])
    ap.add_argument("--P", type=float, required=True, help="累计雨深 mm")
    ap.add_argument("--dur", type=float, required=True, help="历时 h")
    ap.add_argument("--tag", required=True, help="这个场景的后缀，如 R61D2")
    ap.add_argument("--dt", type=float, default=1.0, help="入流步长 min")
    ap.add_argument("--steps", default="12345", help="要跑哪几步（1 产流 2 防线1 3 防线2 4 选址 5 防线2+）")
    a = ap.parse_args()
    from swbrain import db
    cfg = db.load_config(None)
    m, tag = a.model, a.tag
    print(f"\n{'=' * 86}\n{m} · {a.P} mm / {a.dur} h（{tag}）")

    tpk = None
    if "1" in a.steps:
        print("\n① 产流")
        tpk, tot, H, t = make_inflows(cfg, m, a.P, a.dur, tag, a.dt)
    if "2" in a.steps:
        print("\n② 防线1")
        r = facts(run_swmm(WORK / f"{m}_baseline_{tag}.inp"))
        print(f"  街道积水 {r['street_m3']:,.0f} m³｜溢流节点 {r['n_nodes']:,}｜误差 {r['continuity_pct']}%")
    if "3" in a.steps:
        print("\n③ 防线2（客户的塘，换了雨）")
        r = facts(run_swmm(WORK / f"{m}_{tag}.inp"))
        print(f"  街道积水 {r['street_m3']:,.0f} m³｜溢流节点 {r['n_nodes']:,}｜误差 {r['continuity_pct']}%")
    if "4" in a.steps:
        print("\n④ 按这场雨重新选址定容")
        if tpk is None:                       # 没跑第①步时从雨型重算峰现时刻
            shape = R.read_shape(WORK / f"{m}_baseline.inp", a.dur, log=lambda *x: None)
            t_, P_ = R.hyetograph(a.P, a.dur, a.dt / 60.0, shape, 0.0)
            tpk = float(t_[int(np.argmax(np.diff(P_, append=P_[-1])))])
        empty_h = int(np.ceil(tpk)) + 2
        print(f"  雨峰在第 {tpk:.2f} h → 排空起判时刻定为第 {empty_h} h"
              f"（原 24 小时雨型下是第 14 h）")
        subprocess.run([sys.executable, str(HERE / "build_l2p.py"), "both", m],
                       env={**__import__("os").environ,
                            "L2P_BASE_TAG": tag, "L2P_EMPTY_H": str(empty_h),
                            "L2P_OUT_TAG": tag},
                       check=True)
    if "5" in a.steps:
        print("\n⑤ 防线2+（两种构筑物）")
        for st, nm in (("L2B", "地下箱涵"), ("L2L", "地面开敞塘")):
            f = WORK / f"{m}{STATE_SRC[st]}_{tag}.inp"
            if not f.exists():
                print(f"  {nm}：缺 {f.name}，跳过"); continue
            r = facts(run_swmm(f))
            print(f"  {nm}：街道积水 {r['street_m3']:,.0f} m³"
                  f"｜构筑物 {r['struct_m3']:,.0f}｜误差 {r['continuity_pct']}%")
