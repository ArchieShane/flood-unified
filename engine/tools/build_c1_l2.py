# -*- coding: utf-8 -*-
"""C1 的 L2 = L1 + Parsons OP-02 的 12 英寸移动泵（参数对齐页 C10；用户 2026-09-28「都按建议」、授权自动推进）。

点位与流量：tools/extract_c1_op02.py 的 27 个主用泵点；流量 = 一对一配对的 PDF 版泵站单泵流量（只开主用）；
  MP-16（PDF 版无对应）= 25 L/s；去掉塘边 4 点 MP-03 / 11 / 25 / 27 → 23 台
吸水井：离泵点最近的现状井
接收井：沿管道方向往下游，第一口在 **C1 L1 96 mm** 结果里不溢流的井（Node Flooding Summary 里没有它），最远 2 km 管长
  ——Claude 自定：两场雨都用 96 mm 的 L1 选井（96 mm 下不溢流的井 61 mm 下也不溢流，两场雨布置一致）
  ——Claude 自定：2 km 内找不到 → 排到最近的现状出水口（直线 2 km 内）；再没有 → 不建这台泵，列出来
启停：井中水深 > 1.0 m 开、< 0.3 m 停（L2-5）；吸水井本身不到 1.2 m 深的，开泵水深取井深的 80%（Claude 自定，否则泵永远不开）
用法：python tools/build_c1_l2.py --rain 61|96
产物：$WORK/C1_R{r}_L2.inp、c2d/pilot_C1_R{r}_L2.json、out/c1_L2_inputs_R{r}.json
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))
DROP = {"MP-03", "MP-11", "MP-25", "MP-27"}
MP16_Q = 25.0
MAX_PATH = 2000.0


def read_inp(path):
    S, cur = {}, None
    for ln in open(path, encoding="utf-8", errors="ignore"):
        s = ln.strip()
        if s.startswith("["):
            cur = s.upper(); S.setdefault(cur, []); continue
        if cur and s and not s.startswith(";"):
            S[cur].append(s.split())
    return S


def flooded(rpt):
    t = pathlib.Path(rpt).read_text(errors="ignore")
    m = re.search(r"Node Flooding Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    if not m:
        raise SystemExit(f"{rpt} 里没有 Node Flooding Summary（L1 还没跑完？）")
    return {ln.split()[0] for ln in m.group(1).splitlines() if ln.strip()}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--rain", type=int, required=True)
    ap.add_argument("--tag", default=None, help="情景名，缺省 R{雨量}；设计暴雨 61.34 mm / 24 h 用 D24（接收井仍按 C1 L1 96 mm 选，与 2 h 那组同一套布置）")
    a = ap.parse_args(argv)
    import geopandas as gpd
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    from scipy.spatial import cKDTree
    a.tag = a.tag or f"R{a.rain}"
    src = WORK / f"C1_{a.tag}_L1.inp"
    S = read_inp(src)
    xy = {r[0]: (float(r[1]), float(r[2])) for r in S["[COORDINATES]"]}
    junc = {r[0]: (float(r[1]), float(r[2])) for r in S["[JUNCTIONS]"]}          # 井底、深度
    outs = [r[0] for r in S["[OUTFALLS]"]]
    down = {}
    for r in S.get("[CONDUITS]", []):
        down.setdefault(r[1], []).append((r[2], float(r[3])))
    fl = flooded(WORK / "c2d" / "C1_R96_L1_2way.rpt")
    print(f"C1_{a.tag} L2：L1 模型 {len(junc):,} 井；按 C1 L1 96 mm 溢流的井 {len(fl):,}")
    mp = gpd.read_file(ROOT / "console" / "data" / "c1_op02_pumps.geojson").to_crs(32640)
    st = json.loads((ROOT / "out" / "c1_design_params.json").read_text(encoding="utf-8"))["stations"]
    Dm = np.array([[math.hypot(t["x_utm"] - x, t["y_utm"] - y) for t in st] for x, y in zip(mp.x_utm, mp.y_utm)])
    ri, ci = linear_sum_assignment(Dm)
    match = {mp.id.iloc[i]: (st[j], Dm[i, j]) for i, j in zip(ri, ci)}
    jn = [k for k in junc if k in xy]; tr = cKDTree(np.array([xy[k] for k in jn]))
    trO = cKDTree(np.array([xy[k] for k in outs])) if outs else None
    pumps, skipped = [], []
    for s in mp.itertuples():
        if s.id in DROP:
            continue
        q_st, dq = match[s.id]
        if s.id == "MP-16":
            q, qsrc = MP16_Q, "25 L/s（PDF 版无对应泵站，取同类中位数）"
        else:
            q, qsrc = float(q_st["q_each_lps"]), f"PDF 版 {q_st['id']}（相距 {dq:.0f} m）单泵"
        d0, i0 = tr.query((s.x_utm, s.y_utm)); suc = jn[i0]
        best, seen, pq = None, {suc: 0.0}, [(0.0, suc)]
        while pq:
            d, k = heapq.heappop(pq)
            if d > MAX_PATH:
                break
            if k != suc and k in junc and k not in fl:
                best = (k, d, "下游第一口不溢流的井"); break
            for b, L in down.get(k, []):
                if d + L < seen.get(b, 1e18):
                    seen[b] = d + L; heapq.heappush(pq, (d + L, b))
        if best is None and trO is not None:
            dO, iO = trO.query((s.x_utm, s.y_utm))
            if dO <= 2000:
                best = (outs[iO], dO, "最近的现状出水口（2 km 内无不溢流的下游井）")
        if best is None:
            skipped.append({"id": s.id, "why": "2 km 内既无不溢流的下游井也无出水口"}); continue
        if best[0] in outs:
            # SWMM 出水口只能挂一根管：在出水口旁另建一个自由出水口
            to = f"X_L2_{s.id}"; ox, oy = xy[best[0]]; new_out = (to, ox + 2.0, oy + 2.0)
        else:
            to, new_out = best[0], None
        depth = junc[suc][1]
        on = 1.0 if depth >= 1.2 else round(0.8 * depth, 2)
        pumps.append({"id": f"L2_{s.id}", "site": s.id, "from": suc, "suction_dist_m": round(float(d0)), "to": to, "new_out": new_out,
                      "receiver": best[0], "path_m": round(best[1]), "how": best[2], "q_lps": q, "q_src": qsrc,
                      "on": on, "off": 0.3 if on > 0.4 else round(0.3 * on, 2), "well_depth_m": round(depth, 2),
                      "x": s.x_utm, "y": s.y_utm, "near_hotspot": s.near_hotspot})
    # 写 .inp
    txt = src.read_text(encoding="utf-8", errors="ignore")
    txt = txt.replace("· L1 ·", "· L2（L1 + OP-02 移动泵）·", 1)
    newo = [p["new_out"] for p in pumps if p["new_out"]]
    if newo:
        inv_of = {r[0]: float(r[1]) for r in S["[OUTFALLS]"]}
        txt = txt.replace("[OUTFALLS]\n", "[OUTFALLS]\n" + "".join(f"{k}  {inv_of[p['receiver']] - 0.5:.3f}  FREE  NO\n"
                                                             for p in pumps if p["new_out"] for k in [p["new_out"][0]]), 1)
        txt = txt.replace("[COORDINATES]\n", "[COORDINATES]\n" + "".join(f"{k}  {x:.2f}  {y:.2f}\n" for k, x, y in newo), 1)
    pl = "".join(f"{p['id']}  {p['from']}  {p['to']}  PC_{p['id']}  ON  {p['on']:.2f}  {p['off']:.2f}\n" for p in pumps)
    cv = "".join(f"PC_{p['id']}  PUMP2  0  {p['q_lps'] / 1000:.4f}\nPC_{p['id']}  100  {p['q_lps'] / 1000:.4f}\n" for p in pumps)
    txt = txt.replace("[PUMPS]\n", "[PUMPS]\n" + pl, 1)
    txt = txt.replace("[CURVES]\n", "[CURVES]\n" + cv, 1)
    out = WORK / f"C1_{a.tag}_L2.inp"; out.write_text(txt, encoding="utf-8")
    meta = json.loads((WORK / "c2d" / f"pilot_C1_{a.tag}_L1.json").read_text(encoding="utf-8"))
    meta.update(state="L2", pumps_l2=pumps, note=meta.get("note", "") + "；L2 = L1 + OP-02 移动泵 23 台（build_c1_l2.py）")
    (WORK / "c2d" / f"pilot_C1_{a.tag}_L2.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    info = {"rain_mm": a.rain, "n_pumps": len(pumps), "q_total_lps": sum(p["q_lps"] for p in pumps), "skipped": skipped,
            "receiver_basis": "C1 L1 96 mm 的 Node Flooding Summary", "pumps": pumps}
    (ROOT / "out" / f"c1_L2_inputs_{a.tag}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  → {out.name}：移动泵 {len(pumps)} 台，合计 {info['q_total_lps']:,.0f} L/s；未建 {skipped}")
    for p in pumps:
        print(f"    {p['id']:<9} {p['q_lps']:>5g} L/s  {p['from']}（{p['suction_dist_m']} m，井深 {p['well_depth_m']}）→ {p['receiver']}  {p['path_m']} m  {p['how']}  开 {p['on']} / 停 {p['off']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
