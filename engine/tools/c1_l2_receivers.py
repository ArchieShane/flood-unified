# -*- coding: utf-8 -*-
"""C1 的 L2：给 27 个 OP-02 移动泵点各找一口接收井（参数对齐页 C10：「送到附近不积水的现状下游井」）。

规则（列表交用户核对，核对前不进模型）：
  · 吸水井 = 离泵点最近的现状井（全市一维模型 City_L1_v2_p*.inp；节点名就是 UTM 坐标）
  · 接收井 = 从吸水井顺管道方向（SWMM 管道 from → to）往下游走，第一口在全市 L1 一维（61 mm / 2 h）里**不溢流**的井
    （不在 .rpt 的 Node Flooding Summary 里）；沿管网最远找 2 km，找不到就标「无」
  · 流量 = PDF 版同址泵站的单泵流量（C10），只开主用
口径提醒：全市 L1 一维没有泵站，也没有 C1 的现状泵站；C1 自己的 L1 算完后应复核这些井在 C1 L1 下是否仍不溢流。
用法：python tools/c1_l2_receivers.py
产物：out/c1_l2_receivers.json / .csv、console/data/c1_l2_receivers.geojson（泵点 → 接收井的连线）
"""
from __future__ import annotations

import csv
import heapq
import json
import math
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))
MAX_PATH_M = 2000.0


def sections(path, want):
    out, cur = {w: [] for w in want}, None
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if s.startswith("["):
                cur = s.upper() if s.upper() in want else None
                continue
            if cur and s and not s.startswith(";"):
                out[cur].append(s.split())
    return out


def flooded(rpt):
    t = pathlib.Path(rpt).read_text(errors="ignore")
    m = re.search(r"Node Flooding Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    if not m:
        return set()
    return {ln.split()[0] for ln in m.group(1).splitlines() if ln.strip()}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    from shapely.geometry import LineString
    mp = gpd.read_file(ROOT / "console" / "data" / "c1_op02_pumps.geojson").to_crs(32640)
    st = json.loads((ROOT / "out" / "c1_design_params.json").read_text(encoding="utf-8"))["stations"]
    xs, ys = list(mp["x_utm"]), list(mp["y_utm"])
    x0, x1, y0, y1 = min(xs) - 3000, max(xs) + 3000, min(ys) - 3000, max(ys) + 3000
    xy, down, flood, part_of = {}, {}, set(), {}
    for i in range(1, 9):
        inp = WORK / f"City_L1_v2_p{i}.inp"
        S = sections(inp, {"[JUNCTIONS]", "[OUTFALLS]", "[CONDUITS]", "[COORDINATES]"})
        cc = {r[0]: (float(r[1]), float(r[2])) for r in S["[COORDINATES]"] if len(r) >= 3}
        near = {k for k, (x, y) in cc.items() if x0 <= x <= x1 and y0 <= y <= y1}
        if not near:
            continue
        for k in near:
            xy[k] = cc[k]; part_of[k] = i
        kinds = {r[0]: "J" for r in S["[JUNCTIONS]"]} | {r[0]: "O" for r in S["[OUTFALLS]"]}
        for r in S["[CONDUITS]"]:
            a, b, L = r[1], r[2], float(r[3])
            if a in near or b in near:
                down.setdefault(a, []).append((b, L))
        flood |= flooded(WORK / f"City_L1_v2_p{i}.rpt") & near
        for k in near:
            if kinds.get(k) == "O":
                part_of[k] = (i, "outfall")
    print(f"C1 周边节点 {len(xy)}，其中 L1 溢流 {len(flood)}")
    ids = list(xy)
    # OP-02 移动泵点 ↔ PDF 版泵站：一对一最优配对（两版图大多同址，但有的相差几十米到上百米）
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    D = np.array([[math.hypot(t["x_utm"] - x, t["y_utm"] - y) for t in st] for x, y in zip(mp["x_utm"], mp["y_utm"])])
    ri, ci = linear_sum_assignment(D)
    match = {mp["id"].iloc[i]: int(j) for i, j in zip(ri, ci)}
    rows = []
    for s in mp.itertuples():
        px, py = s.x_utm, s.y_utm
        suc = min(ids, key=lambda k: (xy[k][0] - px) ** 2 + (xy[k][1] - py) ** 2)
        dsuc = math.hypot(xy[suc][0] - px, xy[suc][1] - py)
        # 顺管道往下游，按管长最短路找第一口不溢流的井
        best, seen, pq = None, {suc: 0.0}, [(0.0, suc)]
        while pq:
            d, k = heapq.heappop(pq)
            if d > MAX_PATH_M:
                break
            if k != suc and k not in flood:
                best = (k, d); break
            for b, L in down.get(k, []):
                nd = d + L
                if b in xy and nd < seen.get(b, 1e18):
                    seen[b] = nd; heapq.heappush(pq, (nd, b))
        q = st[match[s.id]]
        dq = math.hypot(q["x_utm"] - px, q["y_utm"] - py)
        r = {"id": s.id, "pdf_station": q["id"], "pdf_dist_m": round(dq), "q_lps": q["q_each_lps"],
             "x_utm": px, "y_utm": py, "suction": suc, "suction_dist_m": round(dsuc), "suction_flooded_L1": suc in flood,
             "receiver": best[0] if best else None, "path_m": round(best[1]) if best else None,
             "straight_m": round(math.hypot(xy[best[0]][0] - px, xy[best[0]][1] - py)) if best else None,
             "receiver_is_outfall": isinstance(part_of.get(best[0]), tuple) if best else None, "near_hotspot": s.near_hotspot}
        rows.append(r)
    (ROOT / "out" / "c1_l2_receivers.json").write_text(json.dumps({"rule": __doc__.split("规则")[1].split("用法")[0].strip(), "rows": rows},
                                                                  ensure_ascii=False, indent=1), encoding="utf-8")
    with open(ROOT / "out" / "c1_l2_receivers.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    lines = [(LineString([(r["x_utm"], r["y_utm"]), xy[r["receiver"]]]), r) for r in rows if r["receiver"]]
    g = gpd.GeoDataFrame([r for _, r in lines], geometry=[l for l, _ in lines], crs=32640).to_crs(4326)
    g.to_file(ROOT / "console" / "data" / "c1_l2_receivers.geojson", driver="GeoJSON")
    for r in rows:
        print(f'{r["id"]} ({r["pdf_station"]} 相距 {r["pdf_dist_m"]} m, {r["q_lps"]} L/s)  吸水井 {r["suction"]} {r["suction_dist_m"]} m {"溢流" if r["suction_flooded_L1"] else "不溢流"}'
              f'  → 接收井 {r["receiver"]}  管网 {r["path_m"]} m / 直线 {r["straight_m"]} m{"（出水口）" if r["receiver_is_outfall"] else ""}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
