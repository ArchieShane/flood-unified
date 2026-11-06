# -*- coding: utf-8 -*-
"""29.RB2 · L2 临时转输（地面管 + 固定泵）：在「PO 塘方案」之上，雨前在深积水低点放固定泵，用地面管把水抽走。

为什么是这个（2026-09-28）：29.RB2 集中调蓄压得住深积水、压不住大面积浅积水；而 ≥0.30 m 的深积水其实很集中——
最大两片占 44%、前 12 片占 77%，其中一片是封闭洼地（自己排不出去）。这正是客户 L2 框架里「地面管 + 固定泵」
（IDD / Municipality）对症的地方。用户同意：可以排到模型里的出水口（实际去处导出里没写），A 片可以抽到南边空地。

设计（每条都是假设，写进产物）：
  · 片 = L2+A 态全程最大水深 ≥0.30 m 的单元按 60 m 连成的簇；取 A / B / C / E / F 五片（D / G / H 离排放点 1.1–1.6 km，太远）
  · 泵 = 12 寸固定泵，额定 0.36 m³/s（与 Parsons 用的同型）；台数 = ceil(片内 ≥0.10 m 的水量 / (0.36 × 3 h))，1–6 台
  · 浮球：片内水深 > 0.05 m 才抽；抽多少受片内现有水量限制
  · 排放：B → O-9（146 m）、F → O-34（25 m）、C → EBDP-4（294 m）、E → EBDP-4（778 m）：到出水口就离开系统；
          A → 南边最近的空地（严格用地判据：避开路面、建筑、地块），且地面比片的最低点低 0.5 m 以上、在 29.RB2 外，水放回二维
  · 一维不动：RB2x_L2AC.inp = RB2x_L2A.inp（泵只作用在二维地面上）

产物：$WORK/RB2x_L2AC.inp、c2d/pilot_RB2x_L2AC.json（带 pumps2d）、out/RB2x_L2AC_design.json
用法：python tools/l2c_conveyance.py
"""
from __future__ import annotations

import json
import math
import pathlib
import shutil
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
Q12 = 0.36            # 12 寸固定泵额定流量 m³/s（交付模型里 Parsons 泵曲线扬程 23 m 时 361 L/s）
DRAIN_H = 3.0         # 按 3 小时抽完定台数
OFF_M = 0.05          # 浮球：片内水深低于这个就停
SITES = {             # 片 → 排放去处（出水口节点名；或 None = 找空地）
    "B": ("O-9", (258042.34, 2690637.88)), "F": ("O-34", (261894.58, 2690985.39)),
    "C": ("EBDP-4", (259388.58, 2690426.12)), "E": ("EBDP-4", (258933.47, 2691019.39)),
    "A": (None, (261476.65, 2688410.49)),
}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    from scipy.spatial import cKDTree
    from shapely import contains_xy
    from shapely.geometry import MultiPoint, Point
    from shapely.ops import unary_union
    from shapely.validation import make_valid
    from sklearn.cluster import DBSCAN
    from pyproj import Transformer
    from swbrain import db, landuse as LU
    tr = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    C0 = gpd.read_file(ROOT / "console" / "data" / "catchments_sw.geojson").to_crs(32640)
    reg = unary_union([make_valid(g).buffer(0) for g in C0[C0.label == "29.RB2"].geometry])
    z = np.load(C2D / "RB2x_L2A_2d.npz")
    h, xy, a, el = z["hmax"].astype(float), z["xy"].astype(float), z["area"].astype(float), z["elev"].astype(float)
    inr = contains_xy(reg, xy[:, 0], xy[:, 1])
    deep = inr & (h >= 0.30)
    lab = DBSCAN(eps=60, min_samples=3).fit(xy[deep]).labels_
    di = np.flatnonzero(deep)
    tree_all = cKDTree(xy)
    import re
    t = (WORK / "RB2x_L2A.inp").read_text(encoding="utf-8")
    coords = {r.split()[0]: (float(r.split()[1]), float(r.split()[2])) for r in
              re.search(r"\[COORDINATES\]\n(.*?)(?=\n\[|\Z)", t, re.S).group(1).splitlines() if r.strip() and not r.startswith(";")}
    pumps, rows, skipped = [], [], []
    for sid, (to_node, cxy) in SITES.items():
        # 这一片：离给定中心最近的那个簇
        k = lab[int(np.argmin(np.hypot(xy[di, 0] - cxy[0], xy[di, 1] - cxy[1])))]
        cells = di[lab == k]
        hull = MultiPoint([tuple(p) for p in xy[cells]]).convex_hull.buffer(30.0)
        inhull = np.flatnonzero(contains_xy(hull, xy[:, 0], xy[:, 1]))
        vol = float((np.maximum(h[inhull] - OFF_M, 0) * a[inhull] * (h[inhull] >= 0.10)).sum())
        n = int(min(6, max(1, math.ceil(vol / (Q12 * DRAIN_H * 3600)))))
        low = float(el[inhull].min())
        p = {"id": f"TP-{sid}", "site": sid, "n_pumps": n, "q_m3s": round(n * Q12, 3), "off_m": OFF_M,
             "sump_polygon": [[round(x, 1), round(y, 1)] for x, y in hull.exterior.coords], "xy": [round(float(v), 1) for v in hull.centroid.coords[0]],
             "deep_area_ha": round(float(a[cells].sum()) / 1e4, 2), "vol_ge010_m3": round(vol), "low_m": round(low, 2)}
        if to_node:
            ox, oy = coords[to_node]
            p.update({"to_node": to_node, "pipe_m": round(float(hull.exterior.distance(Point(ox, oy)))), "to_xy": None})
        else:
            # 找空地：严格判据的 free1，在 29.RB2 外、比片最低点低 0.5 m 以上，取最近
            cx, cy = p["xy"]; R = 2500.0
            free1, free2, tf, _ = LU.strict_land_masks(db.load_config(None), (cx - R, cy - R, cx + R, cy + R), log=lambda *x: None)
            x0, y1, res = tf
            rr, cc = np.nonzero(free1)
            px = x0 + (cc + 0.5) * res; py = y1 - (rr + 0.5) * res
            ok = ~contains_xy(reg, px, py)
            _, near = tree_all.query(np.c_[px[ok], py[ok]])
            lower = el[near] < low - 0.5
            cand = np.c_[px[ok], py[ok]][lower]
            if not len(cand):
                # 2026-09-28 实测 A 片：它就是这一带的区域低点，南边沙漠都比它高，抽过去会流回来；最近的出水口 2.3 km，
                # 2 m³/s 量级要 DN600 以上的干管——那是正式工程，不是雨前临时铺的地面管。如实剔掉，结论里单列
                skipped.append({"site": sid, "why": "2.5 km 内没有比片的最低点低 0.5 m 的空地（区域低点）；最近出水口 2.3 km，临时地面管输不了",
                                "deep_area_ha": round(float(a[cells].sum()) / 1e4, 2), "vol_ge010_m3": round(vol)})
                print(f"  ✗ {sid}：{skipped[-1]['why']}"); continue
            d = np.hypot(cand[:, 0] - cx, cand[:, 1] - cy); j = int(np.argmin(d))
            p.update({"to_node": None, "to_xy": [round(float(cand[j, 0]), 1), round(float(cand[j, 1]), 1)], "to_cells": 20,
                      "pipe_m": round(float(d[j])), "to_ground_m": round(float(el[tree_all.query(cand[j])[1]]), 2)})
        lo, la = tr.transform(*p["xy"])
        p["lonlat"] = [round(lo, 6), round(la, 6)]
        if p["to_xy"]:
            lo2, la2 = tr.transform(*p["to_xy"]); p["to_lonlat"] = [round(lo2, 6), round(la2, 6)]
        elif p["to_node"]:
            lo2, la2 = tr.transform(*coords[p["to_node"]]); p["to_lonlat"] = [round(lo2, 6), round(la2, 6)]
        pumps.append(p)
        print(f"  {p['id']}: 深积水 {p['deep_area_ha']} ha、≥0.10 m 水量 {vol:,.0f} m³ → {n} 台 × 0.36 = {p['q_m3s']} m³/s，"
              f"排到 {p['to_node'] or '空地 ' + str(p['to_xy'])}，地面管 {p['pipe_m']:,} m")
    shutil.copy(WORK / "RB2x_L2A.inp", WORK / "RB2x_L2AC.inp")
    meta = json.loads((C2D / "pilot_RB2x_L2A.json").read_text(encoding="utf-8"))
    meta["state"] = "L2AC"; meta["pumps2d"] = pumps
    meta.setdefault("inferred", []).append(
        f"L2+A+C：PO 塘方案（交付模型原件，设计方未确认）之上，{len(pumps)} 处深积水低点雨前放 12 寸固定泵（合计 {sum(p['n_pumps'] for p in pumps)} 台），"
        "地面管排到出水口（实际去处导出里没写）或南边空地；泵只作用在二维地面上")
    (C2D / "pilot_RB2x_L2AC.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (ROOT / "out" / "RB2x_L2AC_design.json").write_text(json.dumps({"pumps": pumps, "skipped": skipped, "q12_m3s": Q12, "drain_h": DRAIN_H, "off_m": OFF_M,
        "note": "每条都是假设：台数按 3 h 抽完片内 ≥0.10 m 的水量定；排到出水口 = 离开系统（出水口实际去处导出里没写）"}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"合计 {sum(p['n_pumps'] for p in pumps)} 台、{sum(p['q_m3s'] for p in pumps):.2f} m³/s、地面管 {sum(p['pipe_m'] for p in pumps):,} m → pilot_RB2x_L2AC.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
