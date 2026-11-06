# -*- coding: utf-8 -*-
"""C1 L2+suggested v5 判定（2026-10-07）：按用户口径对比 61 mm（M61）与 96 mm（M96）。只读。

口径（用户 2026-10-06）：关键积水点的积水面积和水深尽快消退 / 严重程度缓解，且不让别处变严重。
  · 热点 200 m 环内：≥0.30 m 面积、峰值水深、≥0.15 m 持续时间、≥0.15 m 面积×时长、末帧水深
  · 别处：v5 相对 L2+planned（同一场雨）水深变深 ≥5 cm 的面积；61 mm 另列相对 v4
  · 新塘：末满度、溢出量；重力管：峰值流量、总水量；泵：台数 / 抽水量
用法：python tools/c1_l2s5_verdict.py
产物：out/c1_L2S5_verdict.json
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import c1_v4_report as R  # noqa: E402

C2D = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm")) / "c2d"
KS = R.KS
BAND = 0.05
RUNS = {"M61": ["L2P", "L2S4", "L2S5"], "M96": ["L2P", "L2S5"]}


def load(tag, st):
    z = np.load(C2D / f"C1_{tag}_{st}_2d.npz")
    return {"xy": z["xy"].astype(float), "area": z["area"].astype(float), "hmax": z["hmax"].astype(float),
            "F": z["frames"].astype(np.float32), "t": z["t_h"].astype(float)}


def link_flow(rpt, prefix):
    """Link Flow Summary 里名字以 prefix 开头的链：峰值流量 m³/s"""
    out = {}
    for ln in pathlib.Path(rpt).read_text(errors="ignore").splitlines():
        q = ln.split()
        if q and q[0].startswith(prefix) and len(q) >= 4 and q[1] == "CONDUIT":
            try:
                out[q[0]] = float(q[2])
            except ValueError:
                pass
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    from scipy.spatial import cKDTree
    H = R.hotspots(); U = R.catch_union()
    from shapely import contains_xy
    res = {}
    for tag, sts in RUNS.items():
        S = {st: load(tag, st) for st in sts}
        xy = S["L2P"]["xy"]; ar = S["L2P"]["area"]
        tree = cKDTree(xy)
        rings = {k: np.array(tree.query_ball_point(H[k]["xy"], R.RING)) for k in KS}
        anyr = np.zeros(len(xy), bool)
        for k in KS:
            anyr[rings[k]] = True
        inside = contains_xy(U, xy[:, 0], xy[:, 1])
        T = {"hotspots": {}, "states": {}, "side": {}}
        for st, s in S.items():
            F, t = s["F"], s["t"]; dt = float(t[1] - t[0])
            T["states"][st] = {"rings_ge030_ha": round(float(ar[(s["hmax"] >= 0.30) & anyr].sum() / 1e4), 3),
                               "union_ge030_ha": round(float(ar[(s["hmax"] >= 0.30) & inside].sum() / 1e4), 2),
                               "manhole_flooding_m3": round(R.flooding_total(C2D / f"C1_{tag}_{st}_2way.rpt"))}
            for k in KS:
                idx = rings[k]; h = F[:, idx]; a = ar[idx]; D = h.max(1)
                T["hotspots"].setdefault(k, {})[st] = {
                    "area_ge030_ha": round(float(a[s["hmax"][idx] >= 0.30].sum() / 1e4), 3),
                    "peak_m": round(float(D.max()), 2), "end_m": round(float(D[-1]), 2),
                    "h_ge015": round(float((D >= 0.15).sum() * dt), 1),
                    "at_ge015": round(float(((h >= 0.15) @ a).sum() * dt / 1e4), 2)}
        for st in sts:
            if st == "L2P":
                continue
            for ref in [r for r in ("L2P", "L2S4") if r in S and r != st]:
                dh = S[st]["hmax"] - S[ref]["hmax"]
                w = dh >= BAND
                near = {}
                if w.any():
                    HT = cKDTree(np.array([H[k]["xy"] for k in KS]))
                    d, i = HT.query(xy[w])
                    for kk, dd, aa in zip(np.array(KS)[i], d, ar[w]):
                        near.setdefault(kk, [0.0, 1e9]); near[kk][0] += aa / 1e4; near[kk][1] = min(near[kk][1], dd)
                T["side"][f"{st}_vs_{ref}"] = {"deeper_ge005_ha": round(float(ar[w].sum() / 1e4), 3),
                                               "max_increase_m": round(float(dh.max()), 3),
                                               "shallower_ge005_ha": round(float(ar[dh <= -BAND].sum() / 1e4), 2),
                                               "deeper_by_hotspot": {k: [round(v[0], 3), round(v[1])] for k, v in near.items()}}
        tw = json.loads((C2D / f"twoway_C1_{tag}_L2S5.json").read_text(encoding="utf-8"))
        T["new_ponds"] = [{k: (round(p[k], 1) if isinstance(p[k], float) else p[k]) for k in ("unitid", "vol_design_m3", "fill_pct", "overflow_m3", "surface_captured_m3")}
                          for p in tw["ponds"] if p["unitid"].startswith("PND_V5")]
        T["old_ponds"] = [{k: (round(p[k], 1) if isinstance(p[k], float) else p[k]) for k in ("unitid", "fill_pct", "overflow_m3")}
                          for p in tw["ponds"] if not p["unitid"].startswith("PND_V5")]
        T["gravity_peak_m3s"] = link_flow(C2D / f"C1_{tag}_L2S5_2way.rpt", "GP_")
        pv = R.pumping(C2D / f"C1_{tag}_L2S5_2way.rpt")
        T["pumps"] = {k: [round(v[0]), round(v[1], 1)] for k, v in pv.items() if re.match(r"^C\d\d_P$", k)}
        T["sumps"] = {q["id"]: round(q["pumped_m3"]) for q in tw.get("pumps2d") or []}
        res[tag] = T
        print("=" * 90); print(tag)
        print(json.dumps({k: T[k] for k in ("states", "side")}, ensure_ascii=False, indent=1))
        for k in KS:
            print(" ", k, {st: T["hotspots"][k][st] for st in sts})
        print("  新塘：", T["new_ponds"]); print("  老塘：", T["old_ponds"])
        print("  重力管峰值：", T["gravity_peak_m3s"]); print("  截流泵 [m³, %运行]：", T["pumps"]); print("  地面泵：", T["sumps"])
    (ROOT / "out" / "c1_L2S5_verdict.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
