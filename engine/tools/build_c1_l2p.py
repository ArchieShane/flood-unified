# -*- coding: utf-8 -*-
"""C1 的 L2+planned = L1 + Parsons PDF 版方案（参数对齐页 C11；用户 2026-09-28 授权自动推进、确认泵站落点）。

组成
  · 27 座固定泵站（extract_c1_design.py 落点）：单泵流量，**只开主用**（C11 选项「主泵合计 1,535 L/s」）；
    湿井 = 离泵站最近的现状井，井底加深到「地面 − 湿井深」，启停 = 湿井深 50% / 10%（D13）
  · 12 座地上衬砌塘（parsons_crit C1 pond）：塘底 = 轮廓内 5 m DTM 最低点，不挖；四周 1.0 m 围堤、直立塘壁，
    库容 = 面积 × 1.0 m（用户 2026-09-28 改 D14：原「只算现状地形兜得住的水」12 座合计仅 280 m³，
    与「ABOVE GROUND LINED POND」不符；1.0 m 取自 Parsons SHP 的 Depth）；塘满后溢到塘的占地（二维，P-4）
  · 排放路线（P-2，Claude 整理）：C1 的压力管 / 重力管 / 塘溢流管拼成一张网（端点 3 m 内算连通），从泵站 40 m 内的管端出发，
    沿网找最近的终点：某座塘（管端 15 m 内）或图上的 PROPOSED OUTFALL（标注 150 m 内的管端）
Claude 自定（均写进产物与参数对齐页）
  · 标了 (pond) 的 4 座（P5 / P8 / P12 / P19）从它旁边的塘抽，排到路线终点（不能排回自己的塘）；塘里水深 ≥ 0.20 m 开、≤ 0.05 m 停
  · 路线网里找不到终点的泵站：排到离泵站最近的规划塘（直线 1 km 内），再没有就排到下游第一口不溢流的井（同 L2 规则）
  · L2 的移动泵：与 PDF 泵站同址的 22 台被固定泵站取代；MP-16（PDF 版无对应）保留
  · 地道线性调蓄（P-3）：图纸只有示意插图、定不出位置与长度 → 不建，列为局限
用法：python tools/build_c1_l2p.py --rain 61|96
产物：$WORK/C1_R{r}_L2P.inp、c2d/pilot_C1_R{r}_L2P.json、out/c1_L2P_inputs_R{r}.json
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import pathlib
import sys
from collections import defaultdict

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from build_c1_l2 import MAX_PATH, flooded, read_inp  # noqa: E402

WORK = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))
POND_PUMPS = {"P5", "P8", "P12", "P19"}
BUND_M = 1.0          # 围堤高（D14 改，2026-09-28）


def pond_curve(poly, dem_path):
    """塘底、兜水上限、面积—水深曲线（5 m DTM）。"""
    import numpy as np
    import rasterio
    from rasterio.mask import mask
    from shapely.geometry import mapping
    with rasterio.open(dem_path) as ds:
        z, tf = mask(ds, [mapping(poly)], crop=True, filled=False)
        rim, _ = mask(ds, [mapping(poly.exterior.buffer(3.0))], crop=True, filled=False)
        cell = abs(ds.res[0] * ds.res[1])
    z = z[0].compressed().astype(float); rim = rim[0].compressed().astype(float)
    z = z[z > -100]; rim = rim[rim > -100]
    zmin, zrim = float(z.min()), float(rim.min())
    # 地上围堤、直立塘壁：面积 = 轮廓面积，深 BUND_M（现状地形兜水的深度 zrim − zmin 只作记录）
    A = float(poly.area)
    curve = [(0.0, A), (BUND_M, A)]
    return zmin, zrim, BUND_M, curve, A * BUND_M


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--rain", type=int, required=True)
    ap.add_argument("--tag", default=None, help="情景名，缺省 R{雨量}；设计暴雨 61.34 mm / 24 h 用 D24")
    ap.add_argument("--l2tag", default=None, help="保留的 MP-16 移动泵从哪个情景的 L2 输入取（缺省同 --tag）")
    a = ap.parse_args(argv)
    a.tag = a.tag or f"R{a.rain}"; a.l2tag = a.l2tag or a.tag
    import geopandas as gpd
    import numpy as np
    from scipy.spatial import cKDTree
    from shapely.geometry import Point, mapping
    from shapely.ops import unary_union
    src = WORK / f"C1_{a.tag}_L1.inp"
    S = read_inp(src)
    xy = {r[0]: (float(r[1]), float(r[2])) for r in S["[COORDINATES]"]}
    junc = {r[0]: (float(r[1]), float(r[2])) for r in S["[JUNCTIONS]"]}
    down = defaultdict(list)
    for r in S.get("[CONDUITS]", []):
        down[r[1]].append((r[2], float(r[3])))
    fl = flooded(WORK / "c2d" / "C1_R96_L1_2way.rpt")
    jn = [k for k in junc if k in xy]; trJ = cKDTree(np.array([xy[k] for k in jn]))
    G = gpd.read_file(ROOT / "console" / "data" / "parsons_crit.geojson").to_crs(32640); G = G[G.code == "C1"]
    PO = G[G.kind == "pond"].reset_index(drop=True)
    ST = gpd.read_file(ROOT / "console" / "data" / "c1_pumps.geojson").to_crs(32640)
    OF = json.loads((ROOT / "out" / "c1_proposed_outfalls.json").read_text(encoding="utf-8"))["outfall_labels_utm"]
    dem = WORK / "c2d" / f"dem_C1_{a.tag}.tif"

    # ---- 排放路线网
    net = unary_union(list(G[G.kind == "pipe"].geometry))
    segs = [s for s in (net.geoms if hasattr(net, "geoms") else [net])]
    pts, edges = [], []
    for s in segs:
        c = list(s.coords); pts += [c[0][:2], c[-1][:2]]
    P = np.array(pts); tr = cKDTree(P)
    par = list(range(len(P)))
    def f(i):
        while par[i] != i:
            par[i] = par[par[i]]; i = par[i]
        return i
    for i, j in tr.query_pairs(3.0):
        par[f(i)] = f(j)
    nid = [f(i) for i in range(len(P))]
    adj = defaultdict(list)
    for k, s in enumerate(segs):
        u, v = nid[2 * k], nid[2 * k + 1]
        adj[u].append((v, s.length)); adj[v].append((u, s.length))
    nodes = sorted(set(nid)); NP = {n: P[n] for n in nodes}
    term = {}
    for n in nodes:
        p = Point(NP[n])
        for k, g in PO.iterrows():
            if g.geometry.distance(p) <= 15:
                term[n] = ("pond", k)
        for j, (ox, oy) in enumerate(OF):
            if math.hypot(NP[n][0] - ox, NP[n][1] - oy) <= 150 and len(adj[n]) == 1:
                term[n] = ("outfall", j)

    # ---- 塘
    ponds = []
    for k, g in PO.iterrows():
        poly = g.geometry if g.geometry.geom_type == "Polygon" else max(g.geometry.geoms, key=lambda q: q.area)
        zmin, zrim, depth, curve, vol = pond_curve(poly, dem)
        ponds.append({"k": k, "node": f"PND_C1_{k:02d}", "area_ha": round(poly.area / 1e4, 2), "z_floor": zmin, "z_rim": zrim,
                      "depth": depth, "curve": curve, "vol": vol, "xy": [poly.representative_point().x, poly.representative_point().y],
                      "footprint": mapping(poly)})
    print(f"C1_{a.tag} L2+planned：规划塘 {len(ponds)} 座，围堤 {BUND_M:g} m，库容合计 {sum(p['vol'] for p in ponds):,.0f} m³")
    for p in ponds:
        print(f"    {p['node']}  {p['area_ha']:>5} ha  塘底 {p['z_floor']:.2f}  围堤顶 {p['z_floor'] + p['depth']:.2f}（现状地形兜水上限 {p['z_rim']:.2f}） 库容 {p['vol']:>9,.0f} m³")

    # ---- 泵站
    pumps, new_out = [], []
    for s in ST.itertuples():
        sx, sy = s.x_utm, s.y_utm
        own = None
        if s.id in POND_PUMPS:
            dd = PO.distance(Point(sx, sy)); own = int(dd.idxmin())
        starts = [n for n in nodes if math.hypot(NP[n][0] - sx, NP[n][1] - sy) <= 40]
        best, seen, pq = None, {}, []
        for n in starts:
            d0 = math.hypot(NP[n][0] - sx, NP[n][1] - sy); seen[n] = d0; heapq.heappush(pq, (d0, n))
        while pq:
            d, n = heapq.heappop(pq)
            if n in term and not (term[n][0] == "pond" and term[n][1] == own):
                best = (term[n], d, "压力管网终点"); break
            for m, L in adj[n]:
                if d + L < seen.get(m, 1e18):
                    seen[m] = d + L; heapq.heappush(pq, (d + L, m))
        if best is None:
            dd = PO.distance(Point(sx, sy))
            if own is not None:
                dd = dd.drop(own)
            if len(dd) and dd.min() <= 1000:
                best = (("pond", int(dd.idxmin())), float(dd.min()), "路线网找不到终点 → 最近的规划塘（Claude 自定）")
        if s.id in POND_PUMPS:
            frm, fz, on, off, how_from = ponds[own]["node"], None, 0.20, 0.05, f"从塘 {ponds[own]['node']} 抽"
        else:
            dj, ij = trJ.query((sx, sy)); frm = jn[ij]
            ground = junc[frm][0] + junc[frm][1]; wd = float(s.wetwell_depth_m)
            fz = min(junc[frm][0], ground - wd); on, off = 0.5 * wd, 0.1 * wd; how_from = f"湿井 {frm}（{dj:.0f} m）"
        if best is None:
            # 下游第一口不溢流的井（同 L2 规则）
            b2, seen2, pq2 = None, {frm: 0.0}, [(0.0, frm)]
            while pq2:
                d, k = heapq.heappop(pq2)
                if d > MAX_PATH:
                    break
                if k != frm and k in junc and k not in fl:
                    b2 = k; break
                for b, L in down.get(k, []):
                    if d + L < seen2.get(b, 1e18):
                        seen2[b] = d + L; heapq.heappush(pq2, (d + L, b))
            to, how_to = (b2, "下游第一口不溢流的井（Claude 自定）") if b2 else (None, "无去处，未建")
        elif best[0][0] == "pond":
            to, how_to = ponds[best[0][1]]["node"], f"规划塘 {ponds[best[0][1]]['node']}（{best[2]}，{best[1]:.0f} m）"
        else:
            j = best[0][1]; to = f"X_OF{j}_{s.id}".replace("-", "_")
            new_out.append((to, OF[j][0], OF[j][1])); how_to = f"PROPOSED OUTFALL #{j}（{best[2]}，{best[1]:.0f} m）"
        if to is None:
            print(f"    ! {s.id} 无去处，未建"); continue
        pumps.append({"id": f"L2P_{s.id}".replace("-", "_"), "station": s.id, "from": frm, "z_floor_new": fz, "to": to,
                      "q_lps": float(s.q_each_lps), "on": round(on, 2), "off": round(off, 2), "how_from": how_from, "how_to": how_to,
                      "wetwell": f"{s.wetwell_dia_m}×{s.wetwell_depth_m} m"})
    # 保留 MP-16 的移动泵
    l2 = json.loads((ROOT / "out" / f"c1_L2_inputs_{a.l2tag}.json").read_text(encoding="utf-8"))
    keep = [p for p in l2["pumps"] if p["site"] == "MP-16"]

    # ---- 写 .inp
    txt = src.read_text(encoding="utf-8", errors="ignore").replace("· L1 ·", "· L2+planned（L1 + Parsons PDF 版方案）·", 1)
    fix = {p["from"]: p["z_floor_new"] for p in pumps if p["z_floor_new"] is not None}
    lines = txt.split("\n"); i0 = lines.index("[JUNCTIONS]")
    for j in range(i0 + 1, len(lines)):
        if not lines[j].strip():
            break
        q = lines[j].split()
        if q[0] in fix:
            inv0, d0 = float(q[1]), float(q[2]); ground = inv0 + d0
            lines[j] = f"{q[0]}  {fix[q[0]]:.3f}  {max(ground - fix[q[0]], 0.5):.3f}  0  0  0"
    txt = "\n".join(lines)
    sto = "".join(f"{p['node']}  {p['z_floor']:.3f}  {max(p['depth'], 0.1):.3f}  0  TABULAR  CRV_{p['node']}  0  0  0\n" for p in ponds)
    txt = txt.replace("[CONDUITS]", "[STORAGE]\n" + sto + "\n[CONDUITS]", 1) if "[STORAGE]" not in txt else txt.replace("[STORAGE]\n", "[STORAGE]\n" + sto, 1)
    outs_all = new_out + [p["new_out"] for p in keep if p.get("new_out")]
    if outs_all:
        inv_guess = {n: min(ponds[0]["z_floor"], 0.0) - 1.0 for n, _, _ in outs_all}
        txt = txt.replace("[OUTFALLS]\n", "[OUTFALLS]\n" + "".join(f"{n}  {inv_guess[n]:.3f}  FREE  NO\n" for n, _, _ in outs_all), 1)
    crd = "".join(f"{n}  {x:.2f}  {y:.2f}\n" for n, x, y in outs_all) + "".join(f"{p['node']}  {p['xy'][0]:.2f}  {p['xy'][1]:.2f}\n" for p in ponds)
    txt = txt.replace("[COORDINATES]\n", "[COORDINATES]\n" + crd, 1)
    allp = pumps + [{"id": p["id"], "from": p["from"], "to": p["to"], "q_lps": p["q_lps"], "on": p["on"], "off": p["off"]} for p in keep]
    txt = txt.replace("[PUMPS]\n", "[PUMPS]\n" + "".join(f"{p['id']}  {p['from']}  {p['to']}  PC_{p['id']}  ON  {p['on']:.2f}  {p['off']:.2f}\n" for p in allp), 1)
    cv = "".join(f"PC_{p['id']}  PUMP2  0  {p['q_lps'] / 1000:.4f}\nPC_{p['id']}  100  {p['q_lps'] / 1000:.4f}\n" for p in allp)
    cv += "".join(f"CRV_{p['node']}  STORAGE  " + "\nCRV_{0}  ".format(p['node']).join(f"{h:.3f}  {ar:.1f}" for h, ar in p["curve"]) + "\n" for p in ponds)
    txt = txt.replace("[CURVES]\n", "[CURVES]\n" + cv, 1)
    out = WORK / f"C1_{a.tag}_L2P.inp"; out.write_text(txt, encoding="utf-8")
    meta = json.loads((WORK / "c2d" / f"pilot_C1_{a.tag}_L1.json").read_text(encoding="utf-8"))
    meta.update(state="L2P", ponds=[{"node": p["node"], "unitid": p["node"], "vol": p["vol"], "xy": p["xy"], "footprint": p["footprint"]} for p in ponds],
                pumps_l2p=pumps, note=meta.get("note", "") + "；L2+planned = L1 + Parsons PDF 版（build_c1_l2p.py）")
    (WORK / "c2d" / f"pilot_C1_{a.tag}_L2P.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    info = {"rain_mm": a.rain, "n_pumps": len(pumps), "q_total_lps": sum(p["q_lps"] for p in pumps), "kept_mobile": [p["id"] for p in keep],
            "ponds": [{k: v for k, v in p.items() if k not in ("footprint", "curve")} for p in ponds], "pumps": pumps,
            "not_built": ["地道线性调蓄（P-3，图纸无尺寸与位置）"]}
    (ROOT / "out" / f"c1_L2P_inputs_{a.tag}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  → {out.name}：固定泵站 {len(pumps)} 座（主用合计 {info['q_total_lps']:,.0f} L/s），保留移动泵 {info['kept_mobile']}")
    for p in pumps:
        print(f"    {p['station']:<6} {p['q_lps']:>5g} L/s  {p['how_from']} → {p['how_to']}  开 {p['on']} / 停 {p['off']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
