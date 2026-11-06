"""剩余问题分析（只读）：治理后 Utility Network 里还没挂上子网的管，按所在连通系统分类，再细分原因。

  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/s7_remaining.py <工作目录>

连通系统：管端坐标（0.001 m）重合即相连（设备在管端上，同坐标）。
分类：
  A 已挂子网
  B 系统有出水口，但子网没更新（剩余错误挡住）
  C 系统没有出水口，终点是渗井 / 塘 / Geocell（内陆排水系统，Expanded 只把出水口设为控制器）
  D 系统没有出水口、也没有渗井 / 塘：孤立或断开的系统。细分：
     D1 离别的系统端点 ≤ 0.5 m（几乎接上，治理可补）
     D2 0.5–5 m（可能是数字化断开，需人工核）
     D3 > 5 m（真孤立：可能是只录了一段、或另一端在 DMT 资产库外）
     并统计规模（管数）、是否全由雨水口连接管组成（两端都是雨水口 / 雨水井）
输出 06_validate/remaining.json 和 remaining_isolated.csv（每个 D 类系统一行，带代表坐标，便于上图核对）
"""
import collections
import csv
import json
import os
import sys

import arcpy
import numpy as np
from scipy.spatial import cKDTree


def main(work):
    sys.stdout.reconfigure(encoding="utf-8")
    fd = os.path.join(work, "05_un", "Stormwater_AD.geodatabase", "main.UtilityNetwork") + "/"
    k = lambda p: (round(p.X, 3), round(p.Y, 3))
    par = {}

    def root(x):
        while par.setdefault(x, x) != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    lines = []
    rows_ = [r for r in arcpy.da.SearchCursor(fd + "main.StormwaterLine", ["SHAPE@", "smasubnetworkname", "SHAPE@LENGTH"]) if r[0] is not None]
    # 第十四轮：中间折点上有别的线的端点或有点才算连通（UN 规则）
    hubs = {k(g.firstPoint) for g, *_ in rows_} | {k(g.lastPoint) for g, *_ in rows_}
    for jc in ("main.StormwaterDevice", "main.StormwaterJunction"):
        hubs |= {k(g.firstPoint) for (g,) in arcpy.da.SearchCursor(fd + jc, ["SHAPE@"]) if g is not None}
    for g, sub, ln in rows_:
        a, b = k(g.firstPoint), k(g.lastPoint)
        par[root(a)] = root(b)
        for part in g:
            for p in part:
                if p is not None and k(p) in hubs:
                    par[root(k(p))] = root(a)
        lines.append((a, b, sub, ln))
    codes = {s["Name"]: c for c, s in arcpy.da.ListSubtypes(fd + "main.StormwaterDevice").items()}
    of_r, sink_r = set(), set()
    pt_kind = {}
    for g, ag, at in arcpy.da.SearchCursor(fd + "main.StormwaterDevice", ["SHAPE@", "ASSETGROUP", "ASSETTYPE"]):
        p = k(g.firstPoint)
        pt_kind[p] = ag
        if p not in par:
            continue
        if ag == codes["Outfall"]:
            of_r.add(root(p))
        elif (ag == codes["Catch Basin"] and at == 204) or ag == codes["BMP Point"]:
            sink_r.add(root(p))
    comp = collections.defaultdict(list)
    for i, (a, b, sub, ln) in enumerate(lines):
        comp[root(a)].append(i)
    res = collections.Counter()
    iso = []
    for r, idx in comp.items():
        for i in idx:
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
    # D 类：到别的系统端点的最近距离
    ends = np.array([e for a, b, *_ in lines for e in (a, b)])
    owner = [root(e) for a, b, *_ in lines for e in (a, b)]
    tree = cKDTree(ends)
    cb = codes["Catch Basin"]
    rows = []
    sub_d = collections.Counter()
    for r in iso:
        idx = comp[r]
        pts = list({e for i in idx for e in lines[i][:2]})
        best = np.inf
        for p in pts:
            for d, j in zip(*tree.query(p, k=8)):
                if np.isfinite(d) and owner[j] != r:
                    best = min(best, d)
                    break
        laterals = all(pt_kind.get(lines[i][0]) == cb or pt_kind.get(lines[i][1]) == cb for i in idx)
        cls = "D1 ≤0.5 m" if best <= 0.5 else ("D2 0.5–5 m" if best <= 5 else "D3 >5 m")
        sub_d[cls] += len(idx)
        sub_d[f"{cls}｜系统数"] += 1
        if laterals:
            sub_d["其中：全是雨水口连接管的系统（管数）"] += len(idx)
        sz = "1 根" if len(idx) == 1 else ("2–5 根" if len(idx) <= 5 else ("6–50 根" if len(idx) <= 50 else ">50 根"))
        sub_d[f"规模 {sz}（管数）"] += len(idx)
        rows.append({"root_x": r[0], "root_y": r[1], "n_pipes": len(idx), "length_m": round(sum(lines[i][3] for i in idx), 1),
                     "gap_to_other_m": round(float(best), 3) if np.isfinite(best) else None, "class": cls, "all_laterals": laterals})
    out = {"by_class": dict(res), "isolated_detail": dict(sub_d), "systems_total": len(comp), "systems_with_outfall": len(set(comp) & of_r),
           "systems_with_sink_only": len((set(comp) & sink_r) - of_r)}
    os.makedirs(os.path.join(work, "06_validate"), exist_ok=True)
    json.dump(out, open(os.path.join(work, "06_validate", "remaining.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    with open(os.path.join(work, "06_validate", "remaining_isolated.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
