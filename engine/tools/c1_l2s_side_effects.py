# -*- coding: utf-8 -*-
"""L2+suggested 比 L2+planned 变深（Δh ≥ 5 cm）的地方归到最近的措施 / 塘，找副作用（2026-09-29：v2 在小塘周边溢出）。
用法：python tools/c1_l2s_side_effects.py --tag R61 [--state L2S3]
产物：out/c1_L2S_side_{tag}.json
"""
import argparse, json, os, pathlib, sys
from collections import defaultdict
import numpy as np
from scipy.spatial import cKDTree
ROOT = pathlib.Path(__file__).resolve().parent.parent
W = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm")) / "c2d"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", required=True); ap.add_argument("--state", default="L2S", help="L2S（v2）或 L2S3（v3）"); a = ap.parse_args()
    A = np.load(W / f"C1_{a.tag}_L2P_2d.npz"); B = np.load(W / f"C1_{a.tag}_{a.state}_2d.npz")
    xy = A["xy"].astype(float); ar = A["area"].astype(float); dh = B["hmax"].astype(float) - A["hmax"].astype(float)
    d = json.loads((ROOT / "out" / f"c1_{a.state}_inputs_{a.tag}.json").read_text(encoding="utf-8"))
    meta = json.loads((W / f"pilot_C1_{a.tag}_L2P.json").read_text(encoding="utf-8"))
    tw = json.loads((W / f"twoway_C1_{a.tag}_{a.state}.json").read_text(encoding="utf-8"))
    fill = {p["unitid"]: p for p in tw.get("ponds", [])}
    pts = [(f"{m['id']} 取水点", m["cx"], m["cy"]) for m in d["measures"]] + [(p["node"], *p["xy"]) for p in meta["ponds"]]
    T = cKDTree(np.array([[x, y] for _, x, y in pts])); deep = np.flatnonzero(dh >= 0.05)
    g = defaultdict(lambda: [0.0, 0.0])
    if len(deep):
        _, idx = T.query(xy[deep])
        for c, i in zip(deep, idx):
            k = pts[i][0]; g[k][0] += ar[c]; g[k][1] = max(g[k][1], dh[c])
    rows = [{"near": k, "deeper_ha": round(v[0] / 1e4, 2), "max_dh_m": round(v[1], 2),
             "pond_vol_m3": round(fill[k]["vol_design_m3"]) if k in fill else None, "pond_fill_pct": round(fill[k]["fill_pct"] or 0) if k in fill else None}
            for k, v in sorted(g.items(), key=lambda x: -x[1][0])]
    out = {"tag": a.tag, "deeper_total_ha": round(float(ar[deep].sum()) / 1e4, 2), "by_nearest": rows}
    (ROOT / "out" / f"c1_{a.state}_side_{a.tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
