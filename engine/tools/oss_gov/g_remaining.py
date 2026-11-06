"""开源路线：剩余问题分析（同 tools/exp_gov/s7_remaining.py 的分类和口径，读开源导出的报表库，不用 arcpy）。

  python tools/oss_gov/g_remaining.py <工作目录>

A 已挂子网；B 有出水口、子网没更新；C 无出水口、终点是渗井 / 塘；D 孤立或断开（D1 ≤ 0.5 m、D2 0.5–5 m、D3 > 5 m）。
连通口径同第十四轮：管端坐标（0.001 m 取整）相同即相连；中间折点上有别的线的端点或有点才相连。
输出 06_validate/remaining.json、remaining_isolated.csv（字段同 ArcGIS 路线）。
"""
import collections
import csv
import json
import os
import sys
import warnings

import numpy as np
import pyogrio
import shapely
from scipy.spatial import cKDTree

warnings.filterwarnings("ignore", message="Measured")
INFILTRATION = 204          # Catch Basin / Infiltration 的资产类型代码（同 s7）


def main(work):
    sys.stdout.reconfigure(encoding="utf-8")
    rg = os.path.join(work, "07_export", "Stormwater_AD_reporting.gdb")
    k = lambda x, y: (round(float(x), 3), round(float(y), 3))
    par = {}

    def root(x):
        while par.setdefault(x, x) != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    L = pyogrio.read_dataframe(rg, layer="StormwaterLine", columns=["smasubnetworkname"])
    L = L[L.geometry.notna()]
    D = pyogrio.read_dataframe(rg, layer="StormwaterDevice", columns=["d_assetgroup", "assettype"])
    J = pyogrio.read_dataframe(rg, layer="StormwaterJunction", columns=[])
    xy, idx = shapely.get_coordinates(L.geometry.values, return_index=True)
    first = np.r_[0, np.flatnonzero(idx[1:] != idx[:-1]) + 1]
    last = np.r_[first[1:] - 1, len(idx) - 1]
    hubs = {k(*xy[a]) for a in first} | {k(*xy[b]) for b in last}
    dxy = shapely.get_coordinates(D.geometry.values)
    hubs |= {k(*p) for p in dxy} | {k(*p) for p in shapely.get_coordinates(J.geometry.values)}
    lines = []
    lens = shapely.length(L.geometry.values)
    for a, b, sub, ln in zip(first, last, L.smasubnetworkname, lens):
        ka, kb = k(*xy[a]), k(*xy[b])
        par[root(ka)] = root(kb)
        for v in range(a + 1, b):
            kv = k(*xy[v])
            if kv in hubs:
                par[root(kv)] = root(ka)
        lines.append((ka, kb, sub, float(ln)))
    of_r, sink_r, pt_kind = set(), set(), {}
    for p, ag, at in zip(dxy, D.d_assetgroup, D.assettype):
        kp = k(*p)
        pt_kind[kp] = ag
        if kp not in par:
            continue
        if ag == "Outfall":
            of_r.add(root(kp))
        elif (ag == "Catch Basin" and at == INFILTRATION) or ag == "BMP Point":
            sink_r.add(root(kp))
    comp = collections.defaultdict(list)
    for i, (a, b, sub, ln) in enumerate(lines):
        comp[root(a)].append(i)
    res = collections.Counter()
    iso = []
    for r, ix in comp.items():
        for i in ix:
            sub = lines[i][2]
            if sub not in (None, "", "Unknown"):
                res["A 已挂子网"] += 1
            elif r in of_r:
                res["B 有出水口、子网没更新"] += 1
            elif r in sink_r:
                res["C 无出水口、终点是渗井 / 塘 / Geocell"] += 1
            else:
                res["D 孤立或断开"] += 1
        if r not in of_r and r not in sink_r:
            iso.append(r)
    ends = np.array([e for a, b, *_ in lines for e in (a, b)])
    owner = [root(e) for a, b, *_ in lines for e in (a, b)]
    tree = cKDTree(ends)
    rows, sub_d = [], collections.Counter()
    for r in iso:
        ix = comp[r]
        pts = list({e for i in ix for e in lines[i][:2]})
        best = np.inf
        for p in pts:
            for d, j in zip(*tree.query(p, k=8)):
                if np.isfinite(d) and owner[j] != r:
                    best = min(best, d)
                    break
        laterals = all(pt_kind.get(lines[i][0]) == "Catch Basin" or pt_kind.get(lines[i][1]) == "Catch Basin" for i in ix)
        cls = "D1 ≤0.5 m" if best <= 0.5 else ("D2 0.5–5 m" if best <= 5 else "D3 >5 m")
        sub_d[cls] += len(ix)
        sub_d[f"{cls}｜系统数"] += 1
        if laterals:
            sub_d["其中：全是雨水口连接管的系统（管数）"] += len(ix)
        sz = "1 根" if len(ix) == 1 else ("2–5 根" if len(ix) <= 5 else ("6–50 根" if len(ix) <= 50 else ">50 根"))
        sub_d[f"规模 {sz}（管数）"] += len(ix)
        rows.append({"root_x": r[0], "root_y": r[1], "n_pipes": len(ix), "length_m": round(sum(lines[i][3] for i in ix), 1),
                     "gap_to_other_m": round(float(best), 3) if np.isfinite(best) else None, "class": cls, "all_laterals": laterals})
    out = {"engine": "oss", "by_class": dict(res), "isolated_detail": dict(sub_d), "systems_total": len(comp),
           "systems_with_outfall": len(set(comp) & of_r), "systems_with_sink_only": len((set(comp) & sink_r) - of_r)}
    os.makedirs(os.path.join(work, "06_validate"), exist_ok=True)
    json.dump(out, open(os.path.join(work, "06_validate", "remaining.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    with open(os.path.join(work, "06_validate", "remaining_isolated.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["root_x"])
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
