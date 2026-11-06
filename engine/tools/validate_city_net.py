# -*- coding: utf-8 -*-
"""检验：从 GDB 重建的全市管网，在 Mussafah_00 一块上能不能复现客户模型。

为什么要先做这一步（2026-09-27）：Madinat Al Riyad 试跑里，重建的管网单独跑一维时 93% 的水从井盖冒出，
接塘管基本没流量。如果重建网本身失真，全市 Gap 1（L1 溢流量）就会被系统性放大，后面的数全不可信。

只比**管网**，不比产流：两边的产流算法不同（客户按自己的汇水区 + SCS 单位线，重建网按最近节点摊面积），
直接比溢流量会把两件事混在一起。所以把客户模型**逐节点的入流过程原样注入**重建网里对应的井
（按坐标就近配对），同一场雨（61 mm / 2 h）、同样的窗口、同样 ALLOW_PONDING NO，再比溢流量。
差出来的只能归因于管网：拓扑、管径、管底标高、以及——重建网里**没有泵站**（客户模型有 RD-27 / RD-34）。

用法：python tools/validate_city_net.py [--hours 12]
产物：$WORK/Val_Mussafah_{client,city}.inp/.rpt，out/validate_city_net_Mussafah.json
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import build_city1d as B  # noqa: E402
from workdir import WORK  # noqa: E402

CLIENT = "Mussafah_baseline_R61D2.inp"      # 客户原件剥掉缓解措施后的 L1，产流已换成 61 mm / 2 h
MATCH_M = 5.0


def sections(txt):
    out, cur = collections.defaultdict(list), None
    for ln in txt.splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1].upper(); continue
        if cur and s and not s.startswith(";"):
            out[cur].append(ln)
    return out


def facts(rpt: pathlib.Path) -> dict:
    t = rpt.read_text(encoding="utf-8", errors="replace")
    blk = re.search(r"Flow Routing Continuity(.*?)Continuity Error \(%\)\s*\.+\s*(-?[\d.]+)", t, re.S)
    g = lambda k: float(re.search(re.escape(k) + r"\s*\.+\s*(-?[\d.]+)\s+(-?[\d.]+)", blk.group(1)).group(2)) * 1000.0
    nf = re.search(r"Node Flooding Summary(.*?)\n\s*\*{5,}", t, re.S)
    n_fl = len([l for l in (nf.group(1).splitlines() if nf else []) if re.match(r"\s+\S+\s+\d", l)])
    return {"inflow_m3": g("External Inflow") + g("Wet Weather Inflow"), "outflow_m3": g("External Outflow"),
            "flood_m3": g("Flooding Loss"), "final_stored_m3": g("Final Stored Volume"),
            "continuity_pct": float(blk.group(2)), "n_flooded_nodes": n_fl}


def run(inp: pathlib.Path):
    from swmm.toolkit import solver
    t = time.time()
    solver.swmm_run(str(inp), str(inp.with_suffix(".rpt")), str(inp.with_suffix(".out")))
    return time.time() - t


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    a = ap.parse_args(argv)
    H = int(a.hours)
    src = (WORK / CLIENT).read_text(encoding="utf-8", errors="replace")
    S = sections(src)

    # ---- ① 客户模型：只改 ALLOW_PONDING NO 和结束时间，其余原样
    end = f"01/01/2000\nEND_TIME {H:02d}:00:00" if H < 24 else "01/02/2000"
    cl = re.sub(r"^ALLOW_PONDING\s+\S+", "ALLOW_PONDING NO", src, flags=re.M)
    cl = re.sub(r"^END_DATE\s+\S+\s*\nEND_TIME\s+\S+", f"END_DATE {end}", cl, flags=re.M)
    f_cl = WORK / "Val_Mussafah_client.inp"; f_cl.write_text(cl, encoding="utf-8")

    # ---- ② 重建网：取与客户节点重合的连通块，注入客户逐节点入流
    xyc = {r.split()[0]: (float(r.split()[1]), float(r.split()[2])) for r in S["COORDINATES"]}
    inflows = [r.split() for r in S["INFLOWS"]]
    df = B.read_pipes(None, log=lambda *x: None)
    xy_all, ground, inv, (vu, vd) = B.build_nodes(df, log=lambda *x: None)
    keys = list(xy_all); P = np.array([xy_all[k] for k in keys])
    from scipy.spatial import cKDTree
    tree = cKDTree(P)
    cn = [k for k in xyc if k in {r[0] for r in inflows} or True]
    Q = np.array([xyc[k] for k in cn]); d, j = tree.query(Q)
    match = {k: keys[jj] for k, dd, jj in zip(cn, d, j) if dd <= MATCH_M}
    print(f"客户节点 {len(cn):,}，{MATCH_M:g} m 内能对上重建网的井 {len(match):,}（{len(match) / len(cn) * 100:.0f}%）；"
          f"距离中位 {np.median(d):.2f} m")
    sc = df.StartCode.astype("string").fillna("").values; ec = df.EndCode.astype("string").fillna("").values
    par = {}

    def f(x):
        par.setdefault(x, x)
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x
    for u, v in zip(sc, ec):
        if u and v and u != v:
            par[f(u)] = f(v)
    roots = {f(v) for v in match.values()}
    keep = {n for n in xy_all if f(n) in roots}
    xy = {n: xy_all[n] for n in keep}
    outs = B.read_outfalls(set(xy), log=lambda *x: None)
    ln = df.LineLength.astype("float64").values; dia = df.PIPE_DIAMETER.astype("float64").values / 1000.0
    pipes = []
    for i in range(len(df)):
        n1, n2 = sc[i], ec[i]
        if n1 == n2 or n1 not in keep or n2 not in keep:
            continue
        z1, z2 = float(vu[i]), float(vd[i])
        if z2 > z1:
            n1, n2, z1, z2 = n2, n1, z2, z1
        z1 = max(z1, inv[n1]); z2 = max(z2, inv[n2])
        pipes.append({"id": f"c_{i}", "n1": n1, "n2": n2, "len": float(ln[i]) if ln[i] > 0 else B.MIN_LEN,
                      "z1": z1, "z2": z2, "d": float(dia[i] if dia[i] > 0 else 0.3), "uid": ""})
    of_dia = {}
    for c in pipes:
        for nd in (c["n1"], c["n2"]):
            if nd in outs and c["d"] > of_dia.get(nd, 0.0):
                of_dia[nd] = c["d"]
    B.SIM_H = a.hours
    f_ct = WORK / "Val_Mussafah_city.inp"
    B.unit_runoff = lambda: [("00:00", 0.0)]
    B.write_inp(f_ct, xy, ground, inv, pipes, outs, {k: 0.0 for k in xy}, None, of_dia, log=lambda *x: None)
    txt = f_ct.read_text(encoding="utf-8")
    txt = txt.replace("ROUTING_STEP  00:00:30", "ROUTING_STEP  00:00:30\nLENGTHENING_STEP  10\nTHREADS  8", 1)
    # 客户入流：LPS → CMS 用 Sfactor 0.001；没对上的节点单列，水量要算清
    used, lost, rows = set(), [], []
    for r in inflows:
        node, ts = r[0], r[2]
        k = match.get(node)
        if k is None or k not in keep:
            lost.append(node); continue
        sf = float(r[5]) if len(r) > 5 else 1.0
        rows.append(f"{k}  FLOW  {ts}  FLOW  1.0  {sf * 0.001:.6g}")
        used.add(ts)
    ts_rows = [r for r in S["TIMESERIES"] if r.split()[0] in used]
    txt = re.sub(r"\[INFLOWS\]\n.*?\n\n", "[INFLOWS]\n" + "\n".join(rows) + "\n\n", txt, count=1, flags=re.S)
    txt = re.sub(r"\[TIMESERIES\]\n.*?\n\n", "[TIMESERIES]\n" + "\n".join(ts_rows) + "\n\n", txt, count=1, flags=re.S)
    f_ct.write_text(txt, encoding="utf-8")
    print(f"重建网：连通块 {len(roots)}、井 {len(xy):,}、管 {len(pipes):,}、出水口 {len(outs)}；"
          f"入流节点 {len(rows)} / {len(inflows)}（没对上 {len(lost)}）")

    # 管网本身的对照：管数、总长、管径
    cc = S["CONDUITS"]; cx = {r.split()[0]: r.split() for r in S["XSECTIONS"]}
    cl_len = sum(float(r.split()[3]) for r in cc)
    cl_d = [float(cx[r.split()[0]][2]) for r in cc if r.split()[0] in cx and cx[r.split()[0]][1].upper() == "CIRCULAR"]
    net = {"client": {"conduits": len(cc), "length_km": round(cl_len / 1000, 1), "dia_median_m": float(np.median(cl_d)) if cl_d else None,
                      "pumps": len(S["PUMPS"]), "outfalls": len(S["OUTFALLS"]), "junctions": len(S["JUNCTIONS"])},
           "city": {"conduits": len(pipes), "length_km": round(sum(p["len"] for p in pipes) / 1000, 1),
                    "dia_median_m": float(np.median([p["d"] for p in pipes])), "pumps": 0, "outfalls": len(outs), "junctions": len(xy)}}

    res = {}
    for nm, fp in (("client", f_cl), ("city", f_ct)):
        s = run(fp); r = facts(fp.with_suffix(".rpt")); r["run_s"] = round(s, 1); res[nm] = r
        print(f"  {nm:<7} 入流 {r['inflow_m3']:>10,.0f}  排走 {r['outflow_m3']:>10,.0f}  溢流 {r['flood_m3']:>10,.0f}"
              f"（{r['flood_m3'] / max(r['inflow_m3'], 1) * 100:.0f}%）  溢流井 {r['n_flooded_nodes']:>5}  误差 {r['continuity_pct']}%  {s:.0f}s")
    out = {"hours": a.hours, "match_m": MATCH_M, "matched_nodes": len(match), "client_nodes": len(cn),
           "inflow_nodes_used": len(rows), "inflow_nodes_lost": lost, "network": net, "runs": res,
           "flood_ratio_city_over_client": res["city"]["flood_m3"] / max(res["client"]["flood_m3"], 1)}
    (ROOT / "out" / "validate_city_net_Mussafah.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(net, ensure_ascii=False))
    print(f"重建网溢流 / 客户模型溢流 = {out['flood_ratio_city_over_client']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
