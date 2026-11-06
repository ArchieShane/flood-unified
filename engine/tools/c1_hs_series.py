# -*- coding: utf-8 -*-
"""C1 客户最关注的 3 个热点（ADM-94 / ADM-487 / ADM-192，客户台账 criticality 都是 Very High）逐帧水深过程（用户 2026-09-29）。
每个热点、每场雨、每个态，每 5 分钟一个值：
  pt   = 热点坐标所在三角单元的水深（形心水深）
  r50  = 热点 50 m 内最深的水深（热点坐标是一个点，积水坑常偏几十米）
  a200 = 热点 200 m 内水深 ≥ 0.10 m 的面积（ha）
另给统计：最深、何时最深、≥0.30 m 持续多久、6 h 末还剩多深（按 r50）。
产物：console/data/c1_hs_series.json
用法：python tools/c1_hs_series.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

HS = ["ADM-94", "ADM-487", "ADM-192"]
STATES = ["L1", "L2", "L2P", "L2S", "L2S3"]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    from scipy.spatial import cKDTree
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    out = {"hotspots": {h: {"location": H.loc[h, "location"], "criticality": H.loc[h, "criticality"], "root_cause_zh": H.loc[h, "root_cause_zh"]} for h in HS},
           "note": "pt = 热点坐标所在单元水深；r50 = 50 m 内最深；a200 = 200 m 内 ≥0.10 m 面积（ha）。形心水深，每 5 分钟一帧"}
    for tag in ("R61", "R96", "U61", "D24", "M61"):                 # U61 = 客户单元版计算域（2026-09-29）
        name = f"C1_{tag}"; R = {}
        tr = idx = None
        for st in STATES:
            f = WORK / "c2d" / f"{name}_{st}_2d.npz"
            if not f.exists():
                continue
            z = np.load(f)
            if tr is None:
                xy = z["xy"].astype(float); ar = z["area"].astype(float); tr = cKDTree(xy)
                idx = {h: {"pt": int(tr.query((H.loc[h].geometry.x, H.loc[h].geometry.y))[1]),
                           "r50": np.array(tr.query_ball_point((H.loc[h].geometry.x, H.loc[h].geometry.y), 50.0)),
                           "r200": np.array(tr.query_ball_point((H.loc[h].geometry.x, H.loc[h].geometry.y), 200.0))} for h in HS}
                R["t_h"] = [round(float(x), 4) for x in z["t_h"]]
            fr = z["frames"]
            for h in HS:
                I = idx[h]
                pt = fr[:, I["pt"]].astype(float)
                r50 = fr[:, I["r50"]].astype(float).max(1)
                a200 = ((fr[:, I["r200"]].astype(float) >= 0.10) * ar[I["r200"]]).sum(1) / 1e4
                t = np.array(R["t_h"]); dt = float(t[1] - t[0]) if len(t) > 1 else 5 / 60
                k = int(np.argmax(r50))
                R.setdefault(h, {})[st] = {"pt": [round(v, 3) for v in pt], "r50": [round(v, 3) for v in r50], "a200": [round(v, 2) for v in a200],
                                           "peak_m": round(float(r50.max()), 2), "peak_t_h": round(float(t[k]), 2), "peak_pt_m": round(float(pt.max()), 2),
                                           "h_ge30": round(float((r50 >= 0.30).sum() * dt), 2), "end_m": round(float(r50[-1]), 2),
                                           "a200_max_ha": round(float(a200.max()), 2)}
            print(f"{name} {st}：" + "；".join(f"{h} 最深 {R[h][st]['peak_m']} m @ {R[h][st]['peak_t_h']} h，≥0.30 m {R[h][st]['h_ge30']} h，末 {R[h][st]['end_m']} m" for h in HS))
        if R:
            out[tag] = R
    (ROOT / "console" / "data" / "c1_hs_series.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
