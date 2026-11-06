# -*- coding: utf-8 -*-
"""C1 的 L1：计算域与计算域内的现状泵站（参数对齐页 A3、C9）。

计算域（A3，用户 2026-09-28 选①）：8 个热点（C1 的 7 个 + ADM-487）的地表 + 管网汇水区（hotspot_catch_C1.geojson，不含「合计」行）
  并集外扩 300 m；ADM-90 的地表汇水区未收敛（碰到窗口边），窗口外的来水算不进去——列为局限。
现状泵站（C9）：客户 GDB PS_PUMP，落在计算域内的全部泵站。流量规则（用户 2026-09-28 定）：
  客户备注（COMMENTS 里的 Cpcty::NxQ）优先 → 没有就用 Parsons 同址泵（80 m 内）的 PUMP_FLOW → 都没有就不建泵、列为局限；
  备注「Out of Service」的按停用（流量 0）；备注写几台就几台同时运行；启停 = 井深的 50% / 10%（井深 RESERVIOR_DEPTH_METER）。
排放点：
  ① 客户 GDB 里从泵站起的压力管（PIPE_SUBTYPE = PressrLine，一端在泵站 10 m 内），逐段追到头 → 末端；
     全市模型把压力管当重力管读进来了，建模时把这根管换成泵（吸水井 → 压力管末端）
  ② 没有 ①（或追下来不到 30 m）：Parsons 模型里同一泵站的压力管（起点 30 m 内），逐段追到头
  ③ 都没有：排放点未知——列出来，由用户定
用法：python tools/c1_existing_pumps.py
产物：out/c1_domain.geojson、out/c1_existing_pumps.json / .csv
"""
from __future__ import annotations

import csv
import glob
import json
import math
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); import datapaths  # noqa: E402  客户数据位置：config.toml [data]
GDB = str(datapaths.get("gdb_dir"))
PARSONS = datapaths.parsons("26092026")
WORK = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))


def domain():
    import geopandas as gpd
    from shapely.ops import unary_union
    g = gpd.read_file(ROOT / "console" / "data" / "hotspot_catch_C1.geojson").to_crs(32640)
    g = g[g.kind != "合计"]
    d = unary_union(g.geometry.buffer(0)).buffer(300.0)
    if d.geom_type == "MultiPolygon":
        d = max(d.geoms, key=lambda x: x.area)
    return d


def model_nodes(bounds):
    x0, y0, x1, y1 = bounds
    xy = {}
    for p in sorted(WORK.glob("City_L1_v2_p*.inp")):
        cur = None
        for ln in open(p, encoding="utf-8", errors="ignore"):
            s = ln.strip()
            if s.startswith("["):
                cur = s.upper(); continue
            if cur == "[COORDINATES]" and s and not s.startswith(";"):
                q = s.split()
                x, y = float(q[1]), float(q[2])
                if x0 <= x <= x1 and y0 <= y <= y1:
                    xy[q[0]] = (x, y)
    return xy


def capacity(comment):
    m = re.search(r"Cpcty::\s*(\d+)\s*[xX×]\s*(\d+(?:\.\d+)?)", str(comment or ""))
    return (int(m[1]), float(m[2])) if m else None


def seg_list(gdf, tag=None):
    out = []
    for _, l in gdf.iterrows():
        g = l.geometry
        for part in (g.geoms if g.geom_type == "MultiLineString" else [g]):
            cs = list(part.coords)
            out.append(((cs[0][0], cs[0][1]), (cs[-1][0], cs[-1][1]), part.length, l.get("model") if tag else None, l.get("UNITID")))
    return out


def trace(segs, start, r0, tol=3.0):
    """从泵站 r0 m 内的压力管端点出发，沿相连的段走，返回离泵站最远的端点、累计长度、来源模型。"""
    import math
    d = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1])
    first = [(i, s) for i, s in enumerate(segs) if min(d(s[0], start), d(s[1], start)) < r0]
    if not first:
        return None, 0.0, None, []
    best = (None, 0.0, None, [])
    for i, s in first:
        cur = s[1] if d(s[0], start) <= d(s[1], start) else s[0]
        used, L, src = {i}, s[2], s[3]
        far = cur
        while True:
            nxt = [(j, t) for j, t in enumerate(segs) if j not in used and min(d(t[0], cur), d(t[1], cur)) < tol]
            if not nxt:
                break
            j, t = nxt[0]; used.add(j); L += t[2]
            cur = t[1] if d(t[0], cur) <= d(t[1], cur) else t[0]
            if d(cur, start) > d(far, start):
                far = cur
        if d(far, start) > (d(best[0], start) if best[0] else -1):
            best = (far, L, src, [segs[k][4] for k in used])
    return best


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    import numpy as np
    import pandas as pd
    from scipy.spatial import cKDTree
    from shapely.geometry import Point
    D = domain()
    gpd.GeoDataFrame({"name": ["C1 计算域"], "area_km2": [round(D.area / 1e6, 2)]}, geometry=[D], crs=32640).to_crs(4326) \
        .to_file(ROOT / "out" / "c1_domain.geojson", driver="GeoJSON")
    bb = tuple(D.bounds)
    print(f"计算域 {D.area / 1e6:.1f} km²，包络 {(bb[2] - bb[0]) / 1000:.1f} × {(bb[3] - bb[1]) / 1000:.1f} km")
    PS = gpd.read_file(GDB, layer="PS_PUMP", bbox=bb).to_crs(32640)
    PS = PS[PS.within(D)]
    L = gpd.read_file(GDB, layer="PIPELINE", bbox=bb).to_crs(32640)
    PL = L[L.PIPE_SUBTYPE.astype(str) == "PressrLine"]
    # Parsons 泵与压力管
    pp, pr = [], []
    for m in PARSONS.iterdir():
        if not m.is_dir():
            continue
        for shp in glob.glob(str(m / "**" / "*.shp"), recursive=True):
            n = pathlib.Path(shp).stem
            if re.match(r"(?i)^(pumps?|pump table)$", n) or re.match(r"(?i)^(pressure.?pipes?|pr pipes|pressure pipe table)$", n):
                x = gpd.read_file(shp, bbox=bb)
                x = x.set_crs(32640, allow_override=True) if x.crs is None else x.to_crs(32640)
                if not len(x):
                    continue
                x["model"] = m.name
                (pp if "ump" in n.lower() else pr).append(x)
    PP = gpd.GeoDataFrame(pd.concat(pp), crs=32640) if pp else None
    PR = gpd.GeoDataFrame(pd.concat(pr), crs=32640) if pr else None
    gdb_segs = seg_list(PL); par_segs = seg_list(PR, tag=True) if PR is not None else []
    xy = model_nodes(bb); names = list(xy); tr = cKDTree(np.array([xy[k] for k in names]))
    near_node = lambda p: (names[tr.query(p)[1]], float(tr.query(p)[0]))
    rows = []
    for _, r in PS.iterrows():
        c = r.geometry
        cap = capacity(r.COMMENTS)
        oos = "out of service" in str(r.COMMENTS).lower()
        parsons = []
        if PP is not None:
            d = PP.distance(c)
            for _, q in PP[d < 80].iterrows():
                f = q.get("PUMP_FLOW")
                parsons.append({"model": q.model, "flow_lps": float(f) if f is not None and not pd.isna(f) else None, "dist_m": round(float(q.geometry.distance(c)))})
        if oos:
            n_p, q_p, src = 0, 0.0, "停用（客户备注 Out of Service）"
        elif cap:
            n_p, q_p, src = cap[0], cap[1], f"客户备注 {cap[0]}×{cap[1]:g}"
        elif any(p["flow_lps"] for p in parsons):
            fl = [p["flow_lps"] for p in parsons if p["flow_lps"]]
            n_p, q_p, src = len(fl), round(sum(fl) / len(fl), 1), "Parsons 同址泵 PUMP_FLOW（" + " + ".join(f"{v:g}" for v in fl) + "）"
        else:
            n_p, q_p, src = None, None, "无数据：不建泵，列为局限"
        wet, dwet = near_node((c.x, c.y))
        # 排放点：压力管一段接一段追到头（端点 3 m 内算相连），客户 GDB 优先，其次 Parsons
        dis, how, pipe_len, fm_ids = None, None, None, []
        for segs, label in ((gdb_segs, "客户 GDB 压力管"), (par_segs, "Parsons 压力管")):
            end, length, fm, used_ids = trace(segs, (c.x, c.y), 10.0 if label.startswith("客户") else 30.0)
            if end is not None and math.hypot(end[0] - c.x, end[1] - c.y) > 30:
                dis, how, pipe_len = near_node(end), label + (f"（{fm}）" if fm else ""), round(length)
                fm_ids = [u for u in used_ids if u] if label.startswith("客户") else []
                break
        depth = float(r.RESERVIOR_DEPTH_METER or 0)
        rows.append({"unitid": r.UNITID, "ps_id": r.PUMP_STATION_ID, "status": r.STATUS, "migration": r.INFORMIGRATION,
                     "comment": r.COMMENTS, "n_pumps": n_p, "q_each_lps": q_p, "q_src": src, "parsons": parsons,
                     "wetwell_node": wet, "wetwell_dist_m": round(dwet), "well_depth_m": depth,
                     "on_m": round(0.5 * depth, 2) if depth else None, "off_m": round(0.1 * depth, 2) if depth else None,
                     "depth_note": "井深 2.74 m 存疑（180 kW 大泵站）" if r.UNITID.endswith("PSS_98") else ("客户库没有井深" if not depth else ""),
                     "discharge_node": dis[0] if dis else None, "discharge_dist_m": round(dis[1]) if dis else None,
                     "discharge_src": how or "未知：需要用户定", "forcemain_m": pipe_len, "forcemain_unitids": fm_ids,
                     "x": round(c.x, 1), "y": round(c.y, 1)})
    (ROOT / "out" / "c1_existing_pumps.json").write_text(json.dumps({"domain_km2": round(D.area / 1e6, 2), "rule": __doc__.split("现状泵站")[1].split("用法")[0].strip(),
                                                                    "stations": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(ROOT / "out" / "c1_existing_pumps.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=[k for k in rows[0] if k != "parsons"]); w.writeheader()
        w.writerows([{k: v for k, v in r.items() if k != "parsons"} for r in rows])
    for r in rows:
        print(f'{r["ps_id"]!s:<10} {r["unitid"]:<24} {r["n_pumps"]}×{r["q_each_lps"]} L/s  [{r["q_src"]}]  井 {r["wetwell_node"]} ({r["wetwell_dist_m"]} m) 深 {r["well_depth_m"]}'
              f'  → {r["discharge_node"]} ({r["discharge_src"]}，{r["forcemain_m"]} m) {r["depth_note"]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
