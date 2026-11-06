# -*- coding: utf-8 -*-
"""L2+suggested v4 判定：按用户的验收口径量 v4 相对 L2+planned 的效果。只读。

用户 2026-10-06 的口径：「关键积水点的积水面积和水深在尽量短的时间内消退或者严重程度缓解，
但不能导致这几个积水点之外的其它区域积水情况变得更严重。」

输出的四张表：
  A. 关键积水点的退水（每个热点 200/300 m 环内）：峰值面积、峰时、退到峰值 10% 的时刻、
     退到 0 的时刻、峰值水深、末帧水深/面积 ——  逐态对比 L1 / L2 / L2P / L2S3 / L2S4
  B. 「别处不能更严重」：v4 相对 L2+planned，水深变深 ≥5 cm 的面积与地点
  C. 全域退水：域内存水量 / 湿面积在 2h→6h 的变化
  D. 一维侧：井溢流总量（v4 的截流泵应该把它降下来）

用法：python tools/c1_l2s4_compare.py --tag M61 [--state L2S4]
产物：out/c1_L2S4_verdict_<tag>.json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
W = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm")) / "c2d"
TH = 0.30
KS = ["ADM-%s" % n for n in (21, 90, 94, 104, 192, 202, 486, 487)]
HS3 = ["ADM-94", "ADM-487", "ADM-192"]
BAND = 0.05          # 「变严重」判据：水深比 L2+planned 深这么多 m


def node_flood(rpt):
    t = pathlib.Path(rpt).read_text(errors="ignore")
    m = re.search(r"Node Flooding Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    tot = 0.0
    n = 0
    for ln in (m.group(1).splitlines() if m else []):
        q = ln.split()
        if len(q) >= 6:
            try:
                tot += float(q[5]) * 1000.0; n += 1
            except ValueError:
                pass
    return n, tot


def series(tag, st):
    p = W / f"twoway_C1_{tag}_{st}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    s = d["series"]
    return (np.array([r["t_h"] for r in s]), np.array([r["vol_m3"] for r in s]), np.array([r["wet_m2"] for r in s]),
            d.get("boundary_out_m3"), d.get("acc_m3", {}))


def recede(t, A):
    """峰值、峰时、退到峰值 10% 且不再回升的时刻、退到 0 的时刻"""
    pk = int(A.argmax())
    ap = float(A[pk])
    if ap <= 0:
        return ap, float(t[pk]), float("nan"), float("nan")
    thr = 0.1 * ap
    t10 = tz = float("nan")
    for i in range(pk, len(A)):
        if t10 != t10 and A[i] <= thr and A[i:].max() <= thr:
            t10 = float(t[i])
        if tz != tz and A[i] <= 0.0005 and A[i:].max() <= 0.0005:
            tz = float(t[i])
    return ap, float(t[pk]), t10, tz


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="M61")
    ap.add_argument("--state", default="L2S4")
    ap.add_argument("--states", default=None, help="对比的态，逗号分隔")
    a = ap.parse_args(argv)
    sts = (a.states.split(",") if a.states else ["L1", "L2", "L2P", "L2S3", a.state])
    import geopandas as gpd
    from pyproj import Transformer
    from scipy.spatial import cKDTree

    T = Transformer.from_crs(4326, 32640, always_xy=True)
    H = gpd.read_file(ROOT / "console/data/hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    HP = {k: (H.loc[k].geometry.x, H.loc[k].geometry.y) for k in KS}
    out = {"tag": a.tag, "state": a.state, "th_m": TH, "bands": {}}

    # ---------------- A/B 逐单元量（只读 base 与结果两次）
    base = np.load(W / f"C1_{a.tag}_L2P_2d.npz")
    bxy = base["xy"].astype(float); bar = base["area"].astype(float); bh = base["hmax"].astype(float)
    res = np.load(W / f"C1_{a.tag}_{a.state}_2d.npz")
    rxy = res["xy"].astype(float); rar = res["area"].astype(float); rh = res["hmax"].astype(float)
    assert len(bxy) == len(rxy), "两个文件的单元数不一致"
    dh = rh - bh
    worse = dh >= BAND
    out["worse_ha"] = float(rar[worse].sum() / 1e4)
    out["worse_max_dh"] = float(dh.max())
    out["better_ha"] = float(rar[dh <= -BAND].sum() / 1e4)
    out["worse_by_hotspot"] = {}
    TH_t = cKDTree(np.array([HP[k] for k in KS]))
    if worse.any():
        _, wi = TH_t.query(bxy[worse])
        for k, c in zip(np.array(KS)[wi], rar[worse]):
            out["worse_by_hotspot"][k] = round(out["worse_by_hotspot"].get(k, 0) + float(c) / 1e4, 3)

    print("=" * 100)
    print("B. 「别处不能更严重」：%s 相对 L2+planned，水深变深 ≥%.2f m 的面积 = %.2f ha（最大 +%.2f m）；变浅 ≥%.2f m = %.2f ha" % (
        a.state, BAND, out["worse_ha"], out["worse_max_dh"], BAND, out["better_ha"]))
    if out["worse_by_hotspot"]:
        print("   变深的地方按最近热点分：" + "；".join(f"{k} {v} ha" for k, v in sorted(out["worse_by_hotspot"].items(), key=lambda x: -x[1])))

    # ---------------- A. 关键积水点退水
    print("\n" + "=" * 100)
    print("A. 关键积水点的水深 / 积水面积 / 退水时间（★ = 交付报告里的三个重点热点）")
    cache = {}
    for st in sts:
        p = W / f"C1_{a.tag}_{st}_2d.npz"
        if not p.exists():
            print("   （%s 没有结果，跳过）" % st); continue
        z = np.load(p, allow_pickle=True)
        cache[st] = (z["xy"].astype(float), z["area"].astype(float), z["frames"].astype(np.float32), z["t_h"])
    for R in (200.0, 300.0):
        print("\n  ---- 每个热点 %.0f m 环内 ----" % R)
        hdr = "  %-9s %-6s %8s %6s %8s %8s %8s %8s %8s" % ("热点", "态", "峰值面积", "峰时h", "退10%h", "退0h", "峰值水深", "末帧水深", "末帧面积")
        for k in KS:
            print("  %-9s%s" % (k, " ★" if k in HS3 else ""))
            if k == KS[0]:
                print(hdr)
            for st, (xy, ar, F, t) in cache.items():
                idx = np.array(cKDTree(xy).query_ball_point(HP[k], R))
                if not len(idx):
                    print("     %-6s （环内无单元）" % st); continue
                h = F[:, idx]; aa = ar[idx]
                A = (h >= TH) @ aa / 1e4
                D = h.max(1)
                pka, pkt, t10, tz = recede(t, A)
                print("     %-6s %8.3f %6.1f %8s %8s %8.2f %8.2f %8.3f" % (
                    st, pka, pkt, ("%.1f" % t10) if t10 == t10 else ">6", ("%.1f" % tz) if tz == tz else ">6",
                    D.max(), D[-1], A[-1]))
            out["bands"].setdefault("%g m" % R, {})[k] = {
                st: dict(zip(("peak_area_ha", "peak_t_h", "t_to_10pct_h", "t_to_zero_h", "peak_depth_m", "end_depth_m", "end_area_ha"),
                             [round(float(x), 4) if x == x else None for x in _m] + [round(float(D[-1]), 4), round(float(A[-1]), 4)]))
                for st, _m, D, A in [(st,
                                      recede(t, (F[:, np.array(cKDTree(xy).query_ball_point(HP[k], R))] >= TH)
                                             @ ar[np.array(cKDTree(xy).query_ball_point(HP[k], R))] / 1e4),
                                      F[:, np.array(cKDTree(xy).query_ball_point(HP[k], R))].max(1),
                                      (F[:, np.array(cKDTree(xy).query_ball_point(HP[k], R))] >= TH)
                                      @ ar[np.array(cKDTree(xy).query_ball_point(HP[k], R))] / 1e4)
                                     for st, (xy, ar, F, t) in cache.items()]}

    # ---------------- C. 全域退水
    print("\n" + "=" * 100)
    print("C. 全域退水（雨 0–2 h）")
    print("  %-6s %11s %11s %8s %11s %11s %11s" % ("态", "2h 存水 m³", "6h 存水 m³", "消退%", "2h 淹没 ha", "6h 淹没 ha", "边界出流 m³"))
    for st in sts:
        s = series(a.tag, st)
        if not s:
            continue
        t, v, w, bo, acc = s
        i2 = int(np.argmin(abs(t - 2.0)))
        print("  %-6s %11.0f %11.0f %7.1f%% %11.1f %11.1f %11.0f" % (
            st, v[i2], v[-1], (1 - v[-1] / v[i2]) * 100 if v[i2] else 0, w[i2] / 1e4, w[-1] / 1e4, bo or 0))

    # ---------------- D. 一维井溢流
    print("\n" + "=" * 100)
    print("D. 一维侧：井溢流（v4 的截流泵应该把它降下来）")
    for st in sts:
        rp = W / f"C1_{a.tag}_{st}_2way.rpt"
        if not rp.exists():
            continue
        n, tot = node_flood(rp)
        print("  %-6s 溢流井 %6d 座，合计 %10.0f m³" % (st, n, tot))

    (ROOT / "out" / f"c1_{a.state}_verdict_{a.tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n已写 out/c1_%s_verdict_%s.json" % (a.state, a.tag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
