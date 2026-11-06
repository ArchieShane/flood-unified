# -*- coding: utf-8 -*-
"""强化学习试算专用的简化一维模型（2026-10-30 用户同意）：完整模型（rl_region_build 的 RLF_*.inp）→ RLF_*_<态>S.inp。

只为一维样本（F1）提速，二维验收仍用完整模型：
  ① 叶子雨水口并入下游井：度为 1 的雨水口（治理后资产库 Catch Basin）连同它那根支管删掉，入流过程线加到所接的节点上；反复做，直到没有可并的
     （阿布扎比 Type-2 雨水口串联，WA726-2-113，一串会一层层并掉）。雨水口只决定地面水怎么进管，这件事在二维交换里才用得上
  ② 只算到雨停后 tail 小时（缺省 2 h）
  ③ 步长放宽到客户原值：1 s / 0.75 / 0.5 s
用法：python tools/rl_sample_model.py --region C1 --name RLF_C1_M61D1 --state L1 --dur 1 [--tail 2] [--smoke]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys
import time
from collections import defaultdict

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
from workdir import WORK  # noqa: E402

ROUTING = {"ROUTING_STEP": "0.5", "VARIABLE_STEP": "0.5", "MINIMUM_STEP": "0.1"}   # 同 F 系列 1-12。客户原值 1 s / 0.75 / 0.5 s 在 RB-01 简化版上连续性误差 6.2 %（2026-10-30 实测），不用
LEAD_MAX_D = 0.5                       # 只并管径 ≤ 500 mm 的支管（WA726-1-172：雨水口只接 ≤ 500 mm）


def secs(text):
    order, S, cur = ["__head__"], {"__head__": []}, "__head__"
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s.upper()
            if cur not in S:
                S[cur] = []; order.append(cur)
            continue
        S[cur].append(ln)
    return order, S


def rows(S, k):
    return [ln for ln in S.get(k, []) if ln.strip() and not ln.strip().startswith(";")]


def simplify(full: pathlib.Path, out: pathlib.Path, inlets: set, end_h: float):
    order, S = secs(full.read_text(encoding="utf-8", errors="ignore"))
    J = {ln.split()[0] for ln in rows(S, "[JUNCTIONS]")}
    jinv = {ln.split()[0]: float(ln.split()[1]) for ln in rows(S, "[JUNCTIONS]")}
    jmd = {ln.split()[0]: float(ln.split()[2]) for ln in rows(S, "[JUNCTIONS]")}
    jrim = {k: jinv[k] + jmd[k] for k in jinv}                                    # 冒水高度（并入后取被并雨水口里最低的）
    xs = {ln.split()[0]: ln.split() for ln in rows(S, "[XSECTIONS]")}
    co = {ln.split()[0]: ln.split() for ln in rows(S, "[CONDUITS]")}
    special = {ln.split()[1] for ln in rows(S, "[PUMPS]")} | {ln.split()[2] for ln in rows(S, "[PUMPS]")}
    for sec in ("[WEIRS]", "[ORIFICES]", "[OUTLETS]"):
        for ln in rows(S, sec):
            special |= {ln.split()[1], ln.split()[2]}
    inc = defaultdict(set)
    for c, r in co.items():
        inc[r[1]].add(c); inc[r[2]].add(c)
    infl = {ln.split()[0]: ln.split()[2] for ln in rows(S, "[INFLOWS]")}          # 节点 → 时间序列名
    ts = defaultdict(dict)
    for ln in rows(S, "[TIMESERIES]"):
        q = ln.split()
        ts[q[0]][q[-2]] = ts[q[0]].get(q[-2], 0.0) + float(q[-1])
    merged_into, removed_c, removed_n = {}, set(), set()
    changed = True
    while changed:
        changed = False
        for n in list(J - removed_n):
            if n not in inlets or n in special or len(inc[n]) != 1:
                continue
            c = next(iter(inc[n])); r = co[c]
            if xs.get(c, ["", "CIRCULAR", "9"])[1] not in ("CIRCULAR",) or float(xs[c][2]) > LEAD_MAX_D:
                continue
            other = r[2] if r[1] == n else r[1]
            if n in infl:                                                         # 入流过程线加到下游节点
                src = ts.pop(infl.pop(n))
                if other in infl:
                    tgt = ts[infl[other]]
                    for k, v in src.items():
                        tgt[k] = tgt.get(k, 0.0) + v
                else:
                    infl[other] = f"TS_{other}"; ts[infl[other]] = src
            if other in jrim and n in jrim and jrim[n] < jrim[other] and jrim[n] > jinv[other] + 0.05:
                jrim[other] = jrim[n]                                             # 冒水门槛不抬高：溢流仍在雨水口的格栅高度发生
            merged_into[n] = other; removed_n.add(n); removed_c.add(c)
            inc[other].discard(c); inc[n].clear(); changed = True
    keep_row = lambda sec, ln: not (ln.strip() and not ln.strip().startswith(";") and (   # noqa: E731
        (sec in ("[JUNCTIONS]", "[COORDINATES]") and ln.split()[0] in removed_n) or
        (sec in ("[CONDUITS]", "[XSECTIONS]", "[LOSSES]", "[VERTICES]") and ln.split()[0] in removed_c)))
    for sec in order:
        if sec in ("[INFLOWS]", "[TIMESERIES]"):
            continue
        S[sec] = [ln for ln in S[sec] if keep_row(sec, ln)]
    lowered = 0
    for i, ln in enumerate(S["[JUNCTIONS]"]):
        q = ln.split()
        if q and not q[0].startswith(";") and q[0] in jrim and abs(jrim[q[0]] - (jinv[q[0]] + jmd[q[0]])) > 1e-6:
            q[2] = f"{jrim[q[0]] - jinv[q[0]]:.3f}"; S["[JUNCTIONS]"][i] = "  ".join(q); lowered += 1
    S["[INFLOWS]"] = [f"{n}  FLOW  {t}  FLOW  1.0  1.0  0" for n, t in sorted(infl.items())]

    def tkey(s):                                                                  # 时刻：小数小时，或 hh:mm
        if ":" in s:
            h, m = s.split(":")[:2]
            return int(h) + int(m) / 60
        return float(s)
    S["[TIMESERIES]"] = [f"{t}  {k}  {v:.6f}" for t, d in sorted(ts.items()) for k, v in sorted(d.items(), key=lambda kv: tkey(kv[0]))]
    txt = "\n".join((k + "\n" if k != "__head__" else "") + "\n".join(S[k]) for k in order) + "\n"
    for k, v in ROUTING.items():
        txt = re.sub(rf"^{k}\s+\S+", f"{k}  {v}", txt, flags=re.M)
    eh, em = int(end_h), int(round((end_h % 1) * 60))
    txt = re.sub(r"^END_TIME\s+\S+", f"END_TIME  {eh:02d}:{em:02d}:00", txt, flags=re.M)
    out.write_text(txt, encoding="utf-8")
    return {"nodes_before": len(J), "nodes_removed": len(removed_n), "conduits_removed": len(removed_c), "nodes_after": len(J) - len(removed_n), "rim_lowered": lowered,
            "end_h": end_h, "routing": ROUTING}


def smoke(inp):
    from swmm.toolkit import solver
    bd = WORK / "bench"; bd.mkdir(exist_ok=True); dst = bd / f"smoke_{inp.name}"; shutil.copy2(inp, dst)
    t0 = time.time(); solver.swmm_run(str(dst), str(dst.with_suffix(".rpt")), str(dst.with_suffix(".out"))); dt = time.time() - t0
    txt = dst.with_suffix(".rpt").read_text(encoding="utf-8", errors="ignore")
    ce = re.findall(r"Continuity Error \(%\) \.+\s+(-?[\d.]+)", txt); fl = re.search(r"Flooding Loss \.+\s+([\d.]+)\s+([\d.]+)", txt)
    return {"wall_s": round(dt, 1), "continuity_flow_pct": float(ce[-1]) if ce else None, "flooding_10e3m3": float(fl.group(2)) if fl else None,
            "errors": re.findall(r"ERROR \d+.*", txt)[:5]}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True); ap.add_argument("--name", required=True); ap.add_argument("--state", default="L1")
    ap.add_argument("--dur", type=float, required=True); ap.add_argument("--tail", type=float, default=2.0); ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args(argv)
    cfg = json.loads((HERE / "rl_regions" / f"{a.region}.json").read_text(encoding="utf-8"))
    X = json.loads((ROOT / cfg["gov_dir"] / "extract_gov.json").read_text(encoding="utf-8"))
    inlets = {re.sub(r"\s+", "_", str(d["assetid"])) for d in X["devices"] if d.get("group") == "Catch Basin" and d.get("assetid")}
    full = WORK / f"{a.name}_{a.state}.inp"; out = WORK / f"{a.name}_{a.state}S.inp"
    info = simplify(full, out, inlets, a.dur + a.tail)
    print(f"{out.name}：节点 {info['nodes_before']:,} → {info['nodes_after']:,}（并掉雨水口 {info['nodes_removed']:,}）；算到 {info['end_h']} h")
    if a.smoke:
        info["smoke_1d"] = smoke(out); print("一维冒烟：", info["smoke_1d"])
    o = ROOT / "out/l2s_rl" / cfg["key"] / "model" / f"sample_{a.name}_{a.state}S.json"
    o.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
