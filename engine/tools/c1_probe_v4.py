# -*- coding: utf-8 -*-
"""L2+suggested v4 的设计前探针（2026-10-06）。只读，不改任何模型。

回答四个问题（v1–v3 从来没量过前两个）：
  1. L2+planned 的水到底退不退？—— 逐帧域内存水量 / 湿面积 / 边界出流
  2. 关键积水点周围的水，有多大比例是「自己不退」的滞留水
  3. 每个关键热点的残余片：1D 溢流多少、洼地多深、到塘 / 出水口多远、塘还有多少库容
  4. v3 的 500 m 判据（按片重心）漏没漏片

用法：python tools/c1_probe_v4.py [--tag M61]
产物：out/c1_v4_probe_<tag>.json（设计输入清单）
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import math
import pathlib
import sys

import numpy as np
from pyproj import Transformer
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

ROOT = pathlib.Path(__file__).resolve().parent.parent
W = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm")) / "c2d"
TH = 0.30          # 积水判据（与交付报告一致）
LINK = 25.0        # 残余片内单元连接半径
STUCK_DH = 0.02    # 「不退」判据：2h→6h 水深下降不到这么多
KS = ["ADM-%s" % n for n in (21, 90, 94, 104, 192, 202, 486, 487)]
HS3 = ["ADM-94", "ADM-487", "ADM-192"]          # 交付报告里的三个重点热点


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="M61")
    a = ap.parse_args(argv)
    T = Transformer.from_crs(4326, 32640, always_xy=True)

    hj = json.loads((ROOT / "console/data/hotspots.geojson").read_text(encoding="utf-8"))
    HP = {}
    for f in hj["features"]:
        k = f["properties"].get("hotspot_id")
        if k and k.startswith("ADM-"):
            HP[k] = T.transform(*f["geometry"]["coordinates"])
    HXY = np.array([HP[k] for k in KS])
    TH_t = cKDTree(HXY)

    # ---------------------------------------------------------------- 1. 水退不退
    print("=" * 88)
    print("1) 二维域内存水量 / 湿面积 / 边界出流（雨 0–2 h，模拟到 6 h）")
    print("   %-6s %10s %10s %8s   %9s %9s   %s" % ("态", "2h m³", "6h m³", "消退%", "2h ha", "6h ha", "边界累计出流 m³"))
    tw_series = {}
    for st in ("L1", "L2", "L2P"):
        tw = json.loads((W / f"twoway_C1_{a.tag}_{st}.json").read_text(encoding="utf-8"))
        t = np.array([r["t_h"] for r in tw["series"]])
        v = np.array([r["vol_m3"] for r in tw["series"]])
        w = np.array([r["wet_m2"] for r in tw["series"]])
        i2 = int(np.argmin(abs(t - 2.0)))
        tw_series[st] = (t, v, w)
        print("   %-6s %10.0f %10.0f %7.1f%%   %9.1f %9.1f   %12.0f" % (
            st, v[i2], v[-1], (1 - v[-1] / v[i2]) * 100 if v[i2] else 0, w[i2] / 1e4, w[-1] / 1e4,
            tw.get("boundary_out_m3", 0)))

    # ---------------------------------------------------------------- 2/3. 残余片
    d = np.load(W / f"C1_{a.tag}_L2P_2d.npz", allow_pickle=True)
    xy = d["xy"].astype(float); ar = d["area"].astype(float); el = d["elev"].astype(float)
    hmax = d["hmax"].astype(float); F = d["frames"].astype(np.float32); t = d["t_h"]
    meta = json.loads((W / f"pilot_C1_{a.tag}_L2P.json").read_text(encoding="utf-8"))
    twP = json.loads((W / f"twoway_C1_{a.tag}_L2P.json").read_text(encoding="utf-8"))
    i2 = int(np.argmin(abs(t - 2.0)))
    H2, H6 = F[i2], F[-1]

    ponds = meta.get("ponds") or []
    PXY = np.array([p["xy"] for p in ponds], float)
    PVOL = np.array([float(p.get("vol") or 0) for p in ponds])
    fill = {p["unitid"]: (p.get("fill_pct") or 0) for p in twP.get("ponds", [])}
    PAVAIL = np.array([(1 - fill.get(p["unitid"], 0) / 100.0) * 0.9 * PVOL[i] for i, p in enumerate(ponds)])
    TP = cKDTree(PXY) if len(PXY) else None
    OFXY = np.array(json.loads((ROOT / "out/c1_proposed_outfalls.json").read_text(encoding="utf-8"))["outfall_labels_utm"], float)
    TO = cKDTree(OFXY)
    NXY = np.array(list(meta["nodes"].values()), float)
    TN = cKDTree(NXY)
    NMH = list(meta["nodes"].keys())

    # 一维每个节点的溢流量（L2+planned）
    import re
    nf = {}
    m = re.search(r"Node Flooding Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", (W / f"C1_{a.tag}_L2P_2way.rpt").read_text(errors="ignore"), re.S)
    for ln in (m.group(1).splitlines() if m else []):
        q = ln.split()
        if len(q) >= 6:
            try:
                nf[q[0]] = {"hours": float(q[1]), "rate_m3s": float(q[2]), "vol_m3": float(q[5]) * 1000.0}
            except ValueError:
                pass

    w = np.flatnonzero(hmax >= TH)
    print("\n2) L2+planned 残余 ≥%.2f m：%d 单元 %.2f ha %.0f m³" % (
        TH, len(w), ar[w].sum() / 1e4, (hmax[w] * ar[w]).sum()))
    tr = cKDTree(xy[w])
    pr = tr.query_pairs(LINK, output_type="ndarray")
    G = coo_matrix((np.ones(len(pr)), (pr[:, 0], pr[:, 1])), shape=(len(w), len(w)))
    nc, lab = connected_components(G, directed=False)

    print("   带* = 片内到最近热点的最近单元 ≤500 m（v4 判据）；v3 用的是重心 ≤500 m")
    rows = []
    for i in range(nc):
        cells = w[lab == i]
        A = float(ar[cells].sum())
        px, py = (xy[cells] * ar[cells, None]).sum(0) / A
        dn, inear = TH_t.query(xy[cells])
        j = int(dn.argmin()); nh = KS[inear[j]]; dmin = float(dn.min())
        dcen = float(TH_t.query([px, py])[0])
        v2 = float((H2[cells] * ar[cells]).sum()); v6 = float((H6[cells] * ar[cells]).sum())
        floor = float(el[cells].min())
        ring = tr.query_ball_point([px, py], np.sqrt(A / np.pi) + 150.0)
        ring = [q for q in ring if math.hypot(*(xy[w][q] - np.array([px, py]))) > np.sqrt(A / np.pi)]
        gring = float(np.median(el[w][ring])) if ring else float(el[cells].max())
        # 片内井溢流
        near = TN.query_ball_point([px, py], max(40.0, np.sqrt(A / np.pi) + 25.0))
        fl = [(nf[NMH[q]], NMH[q]) for q in near if NMH[q] in nf]
        fv = sum(x["vol_m3"] for x, _ in fl); fr_rate = sum(x["rate_m3s"] for x, _ in fl)
        rows.append(dict(i=i, A=A, v2=v2, v6=v6, x=px, y=py, nh=nh, dmin=dmin, dcen=dcen,
                         floor=floor, depth=gring - floor, fv=fv, frate=fr_rate,
                         dP=float(TP.query([px, py])[0]) if TP else -1,
                         iP=int(TP.query([px, py])[1]) if TP else -1,
                         dO=float(TO.query([px, py])[0]),
                         stuck=bool((H6[cells] - H2[cells] > -STUCK_DH).mean() > 0.5)))
    rows.sort(key=lambda r: -r["v6"])

    stuck_tot = sum(r["v6"] for r in rows if r["stuck"])
    print("   3) 其中「自己不退」的（2h→6h 水深基本没降）：%.2f ha %.0f m³ = 滞留水的 %.0f%%" % (
        sum(r["A"] for r in rows if r["stuck"]) / 1e4, stuck_tot,
        stuck_tot / max(sum(r["v6"] for r in rows), 1) * 100))

    print("\n4) 关键热点周围（每个热点 500 m 环内）的残余片")
    keypatch = []
    for k in KS:
        dk = np.hypot(xy[w, 0] - HP[k][0], xy[w, 1] - HP[k][1])
        sel = np.flatnonzero(dk <= 500.0)
        if not len(sel):
            print("   %-9s —（500 m 环内无 ≥%.2f m 残余）" % (k, TH)); continue
        pids = np.unique(lab[sel])
        tot = sum(rows[r]["v6"] for r in pids if rows[r]["nh"] == k or rows[r]["dmin"] <= 500)
        print("   %-9s 环内 %.2f ha / %.0f m³  %d 片" % (k, ar[w][sel].sum() / 1e4, sum(rows[r]["v6"] for r in pids), len(pids)))
        for p in pids[np.argsort(-np.array([rows[r]["v6"] for r in pids]))][:6]:
            r = rows[p]
            print("       #%-4d %6.2f ha %8.0f m³ | 洼地 %5.2f m | 1D溢流 %7.0f m³ %5.2f m³/s | →塘 %5.0f m(可用%7.0f) →出水 %5.0f m | %s" % (
                p, r["A"] / 1e4, r["v6"], r["depth"], r["fv"], r["frate"], r["dP"],
                PAVAIL[r["iP"]] if r["iP"] >= 0 else 0, r["dO"], "不退" if r["stuck"] else "会退"))
            keypatch.append(r)

    # v4 覆盖面：按「片内最近单元 ≤500 m」选
    inadm = [r for r in rows if r["dmin"] <= 500.0]
    print("\n5) 覆盖面（500 m 环内，按片内最近单元判）")
    print("   入选 %d 片：%.2f ha / %.0f m³（占残余水 %.0f%%）" % (
        len(inadm), sum(r["A"] for r in inadm) / 1e4, sum(r["v6"] for r in inadm),
        sum(r["v6"] for r in inadm) / max(sum(r["v6"] for r in rows), 1) * 100))
    out = {"tag": a.tag, "th_m": TH, "recession": {st: {"t": [float(x) for x in tw_series[st][0]],
                                                        "vol_m3": [float(x) for x in tw_series[st][1]],
                                                        "wet_m2": [float(x) for x in tw_series[st][2]]} for st in tw_series},
           "residual_ha": float(ar[w].sum() / 1e4), "residual_m3": float((hmax[w] * ar[w]).sum()),
           "stuck_ha": float(sum(r["A"] for r in rows if r["stuck"]) / 1e4), "stuck_m3": float(stuck_tot),
           "patches": [{k2: (round(v2, 3) if isinstance(v2, float) else v2) for k2, v2 in r.items() if k2 != "i"} for r in rows],
           "n_patches": nc}
    (ROOT / "out" / f"c1_v4_probe_{a.tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n已写 out/c1_v4_probe_%s.json（%d 片）" % (a.tag, nc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
