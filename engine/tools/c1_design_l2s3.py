# -*- coding: utf-8 -*-
"""C1 的 L2+suggested **v3**（用户 2026-09-29「v3也可以做一下」）——在 v2（tools/c1_design_l2s.py，原样保留）上改三处，
只治 v2 的副作用（小塘被灌爆、放到塘面上的水漫出塘外，61 mm 2.0 ha / 96 mm 11.4 ha 变深）：
  ① 设计时按塘的剩余库容分配：每个塘可用 = 0.9 × 设计容积 ×（1 − L2+planned 末满度）；每处措施要的量
     转输 = 片内井在 L2+planned 的溢流量，地面取水 = 1.5 × 这一处最大积水量（v2 实测抽水量是最大积水量的 1.0–1.7 倍，取 1.5 为 Claude 自定）；
     按最大积水量从大到小依次分，1.5 km 内装得下的最近的塘；都装不下 → 转输排 3 km 内新建出水口、再没有用箱涵；地面取水排最近的新建出水口
  ② 转输泵（一维）进塘加控制规则：塘水深 ≥ 0.90 m（满 1.00 m）停泵，优先级最高；吸水井 > 0.30 m 开、< 0.05 m 停
  ③ 地面取水泵进塘：直接打进塘的一维节点（couple2way_v2 pumps2d.to_pond），每步按塘剩余库容封顶，塘满即停；不再放到塘面二维单元上
产物：$WORK/C1_R{r}_L2S3.inp、c2d/pilot_C1_R{r}_L2S3.json、out/c1_L2S3_inputs_R{r}.json
以下为 v2 原说明：
C1 的 L2+suggested：按**这场雨** L2+planned 的残余积水设计措施（参数对齐页 C12，用户 2026-09-29：转输 / 挡水为主，
箱涵只在必要处；两场雨各设计一套；授权自动推进，方案发给用户看、不等确认）。

1. 残余积水点：L2+planned 全程最大水深 ≥ 0.30 m、落在 8 个热点汇水区（并集）内、不在规划塘占地里的单元；
   25 m 内相邻的单元连成一处；**只处理离 8 个热点 500 m 内**、且最大积水 ≥ 200 m³ 或面积 ≥ 0.05 ha 的（Claude 自定：
   ADM-90 的地表汇水区伸进内陆几公里，那里的空地洼地给土堤 / 泵没有意义；不处理的也列出来）
2. 每处判断来水与对策（每处都写理由）：
   · 片内的井在 L2+planned 里溢流 ≥ 这一处最大积水量的 50% → 管网接不住：**转输**
     固定泵（一维）：从溢流最多的井抽，流量 = 片内各井最大溢流率之和（按 12 英寸泵 0.36 m³/s 取整台），
     排到 1.5 km 内还有余量（L2+planned 末满度 < 80%）的规划塘，没有就排到 3 km 内的 PROPOSED OUTFALL（新建出水口），
     再没有 → 箱涵
   · 否则是地表来水：片子最低处比 150 m 环带地面中位低 ≥ 1.0 m（地道 / 封闭洼地）→ **挡水**：
     沿洼地边缘（片子外扩 5–15 m 的环带）起土堤，堤顶 = 环带地面中位 + 0.30 m；
     不到 1.0 m 的浅积水 → **地面取水泵**（二维取水点，couple2way_v2 pumps2d）：按 3 h 抽完定台数，排到最近的出水口（离开系统）
   · 箱涵：一维蓄水节点，接在片内最低的井上，容积 = 这一处最大积水量（取整到 500 m³），深 3 m
3. 产物：$WORK/C1_R{r}_L2S.inp、c2d/pilot_C1_R{r}_L2S.json（带 dem_edits / pumps2d）、out/c1_L2S_inputs_R{r}.json（措施清单、理由、地图用 map）
来源说明：12 英寸泵额定流量 0.36 m³/s 沿用 29.RB2 L2+A+C 的取值（Parsons 交付模型泵曲线在 23 m 扬程时 361 L/s）
用法：python tools/c1_design_l2s.py --rain 61|96 [--base L2P]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from build_c1_l2 import read_inp  # noqa: E402

WORK = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))
C2D = WORK / "c2d"
Q12 = 0.36
DRAIN_H = 3.0
OFF_M = 0.05
TH = 0.30
LINK = 25.0
MIN_HA = 0.05
MIN_VOL = 200.0
NEAR_R = 500.0       # Claude 自定（2026-09-29）：只处理 8 个热点 500 m 内的残余积水
DEEP_M = 1.0
POND_R, OF_R = 1500.0, 3000.0
OUT = "L2S3"
SAFE = 0.9           # 塘可用库容打九折
SUMP_K = 1.5         # 地面取水要的量 = 1.5 × 最大积水量
POND_STOP_M = 0.90   # 塘水深到 0.90 m 停转输泵
USE_BUND = False      # v2（2026-09-29）：v1 的土堤把地表水挡在堤外，61 mm 下堤外 3.9 ha 变深（ADM-90 旁 +0.6 m）→ 改为地面取水泵排到塘 / 出水口


def node_flood(rpt):
    t = pathlib.Path(rpt).read_text(errors="ignore")
    m = re.search(r"Node Flooding Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        q = ln.split()
        if len(q) >= 6:
            out[q[0]] = {"hours": float(q[1]), "rate_m3s": float(q[2]), "vol_m3": float(q[5]) * 1000.0}
    return out


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--rain", type=int, required=True); ap.add_argument("--base", default="L2P")
    ap.add_argument("--tag", default=None, help="情景名，缺省 R{雨量}；设计暴雨 61.34 mm / 24 h 用 D24")
    a = ap.parse_args(argv)
    import geopandas as gpd
    import numpy as np
    from matplotlib.path import Path as MPath
    from pyproj import Transformer
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    from shapely.geometry import MultiPoint, Point
    from shapely.ops import unary_union
    a.tag = getattr(a, "tag", None) or f"R{a.rain}"
    name = f"C1_{a.tag}"; base = a.base
    tr84 = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    ll = lambda x, y: [round(v, 6) for v in tr84.transform(x, y)[::-1]]
    z = np.load(C2D / f"{name}_{base}_2d.npz"); xy = z["xy"].astype(float); area = z["area"].astype(float)
    hm = z["hmax"].astype(float); elev = z["elev"].astype(float); fr = z["frames"]
    meta = json.loads((C2D / f"pilot_{name}_{base}.json").read_text(encoding="utf-8"))
    tw = json.loads((C2D / f"twoway_{name}_{base}.json").read_text(encoding="utf-8"))
    nf = node_flood(C2D / f"{name}_{base}_2way.rpt")
    S = read_inp(WORK / f"{name}_{base}.inp")
    nxy = {r[0]: (float(r[1]), float(r[2])) for r in S["[COORDINATES]"]}
    junc = {r[0]: (float(r[1]), float(r[2])) for r in S["[JUNCTIONS]"]}
    CA = gpd.read_file(ROOT / "console" / "data" / "hotspot_catch_C1.geojson").to_crs(32640); CA = CA[CA.kind != "合计"]
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    hs = list(dict.fromkeys(CA.hotspot_id))
    U = unary_union(list(CA.geometry.buffer(0)))
    inside = np.zeros(len(xy), bool)
    for part in (U.geoms if hasattr(U, "geoms") else [U]):
        inside |= MPath(np.array(part.exterior.coords)[:, :2]).contains_points(xy)
    pond_cells = np.zeros(len(xy), bool)
    for p in meta.get("ponds", []):
        fp = p["footprint"]; polys = fp["coordinates"] if fp["type"] == "MultiPolygon" else [fp["coordinates"]]
        for poly in polys:
            pond_cells |= MPath(np.array(poly[0])[:, :2]).contains_points(xy)
    wet = np.flatnonzero((hm >= TH) & inside & ~pond_cells)
    print(f"{name} {base}：残余 ≥{TH} m 单元 {len(wet):,}（{area[wet].sum() / 1e4:.1f} ha）")
    T = cKDTree(xy[wet]); pairs = T.query_pairs(LINK, output_type="ndarray")
    from scipy.sparse import coo_matrix
    G = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(wet), len(wet)))
    nc, lab = connected_components(G, directed=False)
    trAll = cKDTree(xy); trN = cKDTree(np.array([nxy[k] for k in junc if k in nxy])); jn = [k for k in junc if k in nxy]
    pond_fill = {p["unitid"]: (p.get("fill_pct") or 0) for p in tw.get("ponds", [])}
    pond_xy = {p["node"]: p["xy"] for p in meta.get("ponds", [])}
    pond_vol = {p["node"]: float(p["vol"]) for p in meta.get("ponds", [])}
    budget = {k: SAFE * pond_vol[k] * (1 - pond_fill.get(k, 0) / 100.0) for k in pond_xy}
    budget0 = dict(budget)

    def pick_pond(cx, cy, need):
        c = sorted((math.hypot(cx - px, cy - py), k) for k, (px, py) in pond_xy.items() if budget[k] >= need)
        if c and c[0][0] <= POND_R:
            d, k = c[0]; budget[k] -= need
            return k, f"规划塘 {k}（{d:.0f} m；可用 {budget0[k]:,.0f} m³，分给这一处 {need:,.0f} m³，余 {budget[k]:,.0f} m³）"
        return None, None
    OF = json.loads((ROOT / "out" / "c1_proposed_outfalls.json").read_text(encoding="utf-8"))["outfall_labels_utm"]
    FW = fr[:, wet].astype(np.float32) * area[wet].astype(np.float32)        # 只取残余积水单元的逐帧水量，一次读完
    pos = {int(c): i for i, c in enumerate(wet)}
    vol_series = lambda cells: FW[:, [pos[int(c)] for c in cells]].sum(1)
    measures, skipped = [], []
    order = sorted(range(nc), key=lambda c: -float(vol_series(wet[lab == c]).max()))
    for c in order:
        cells = wet[lab == c]; A = float(area[cells].sum())
        cx, cy = (xy[cells] * area[cells, None]).sum(0) / A
        dh = {h: math.hypot(cx - H.loc[h].geometry.x, cy - H.loc[h].geometry.y) for h in hs}; nh = min(dh, key=dh.get)
        V = float(vol_series(cells).max())
        if dh[nh] > NEAR_R:
            skipped.append({"cx": round(cx), "cy": round(cy), "ll": ll(cx, cy), "area_ha": round(A / 1e4, 3), "peak_vol_m3": round(V), "near_hotspot": nh,
                            "near_dist_m": round(dh[nh]), "why": f"离最近热点 {dh[nh]:.0f} m > {NEAR_R:.0f} m（多为内陆空地洼地），不处理"}); continue
        if V < MIN_VOL and A < MIN_HA * 1e4:
            skipped.append({"cx": round(cx), "cy": round(cy), "ll": ll(cx, cy), "area_ha": round(A / 1e4, 3), "peak_vol_m3": round(V), "near_hotspot": nh,
                            "near_dist_m": round(dh[nh]), "why": f"很小（最大积水 {V:.0f} m³ < {MIN_VOL:.0f} m³ 且 < {MIN_HA} ha），不处理"}); continue
        hull = MultiPoint([tuple(p) for p in xy[cells]]).convex_hull.buffer(8.0)
        nodes_in = [jn[i] for i in trN.query_ball_point((cx, cy), max(40.0, math.sqrt(A / math.pi) + 25.0)) if hull.buffer(20).contains(Point(nxy[jn[i]]))]
        fl = [(nf[k]["vol_m3"], nf[k]["rate_m3s"], k) for k in nodes_in if k in nf]
        fv = sum(v for v, _, _ in fl); fr_rate = sum(r for _, r, _ in fl)
        ring = trAll.query_ball_point((cx, cy), math.sqrt(A / math.pi) + 150.0)
        ring = [i for i in ring if not hull.contains(Point(xy[i]))]
        g_ring = float(np.median(elev[ring])) if ring else float(elev[cells].max())
        floor = float(elev[cells].min()); depth = g_ring - floor
        base_rec = {"cx": round(cx, 1), "cy": round(cy, 1), "ll": ll(cx, cy), "near_hotspot": nh, "near_dist_m": round(dh[nh]),
                    "area_ha": round(A / 1e4, 2), "peak_vol_m3": round(V), "overflow_1d_m3": round(fv), "overflow_rate_m3s": round(fr_rate, 3),
                    "depression_m": round(depth, 2), "floor_m": round(floor, 2), "ring_ground_m": round(g_ring, 2)}
        mid = f"S{len(measures) + 1:02d}"
        if fv >= 0.5 * V and fl:
            src = max(fl)[2]; n = max(1, math.ceil(fr_rate / Q12)); q = n * Q12
            tgt, how = None, None
            tgt, how = pick_pond(cx, cy, fv)
            if tgt is None:
                d_of = [(math.hypot(cx - ox, cy - oy), j) for j, (ox, oy) in enumerate(OF)]; d_of.sort()
                if d_of[0][0] <= OF_R:
                    tgt, how = f"X_S{mid}_OF{d_of[0][1]}", f"PROPOSED OUTFALL #{d_of[0][1]}（{d_of[0][0]:.0f} m，新建出水口）"
            if tgt:
                measures.append({**base_rec, "id": mid, "kind": "conv", "from": src, "to": tgt, "n_pumps": n, "q_m3s": round(q, 3),
                                 "why": f"片内井在 L2+planned 里溢流 {fv:,.0f} m³（≥ 这一处最大积水量 {V:,.0f} m³ 的一半）：管网接不住，用 {n} 台 12 英寸固定泵从 {src} 转输到{how}",
                                 "target": how, "of_xy": OF[int(tgt.split('OF')[-1])] if tgt.startswith("X_") else None}); continue
            low = min(nodes_in, key=lambda k: junc[k][0])
            vol = max(500, int(math.ceil(V / 500.0)) * 500)
            measures.append({**base_rec, "id": mid, "kind": "box", "node": low, "vol_m3": vol,
                             "why": f"管网接不住（溢流 {fv:,.0f} m³），但 1.5 km 内没有有余量的塘、3 km 内没有出水口：箱涵 {vol:,} m³ 接在 {low}"}); continue
        if depth >= DEEP_M and USE_BUND:
            ringpoly = hull.buffer(15.0).difference(hull.buffer(5.0))
            rp = ringpoly if ringpoly.geom_type == "Polygon" else max(ringpoly.geoms, key=lambda g: g.area)
            crest = g_ring + 0.30
            measures.append({**base_rec, "id": mid, "kind": "bund", "crest_m": round(crest, 2),
                             "polygon": [[round(x, 1), round(y, 1)] for x, y in rp.exterior.coords], "hole": [[round(x, 1), round(y, 1)] for x, y in hull.buffer(5.0).exterior.coords],
                             "why": f"积水主要来自地表（片内井溢流 {fv:,.0f} m³ < 最大积水量 {V:,.0f} m³ 的一半），片子是比周边低 {depth:.1f} m 的洼地 / 地道：沿边缘起土堤，堤顶 {crest:.2f} m（周边地面中位 + 0.30 m），挡住外来地表水"}); continue
        n = max(1, math.ceil(V / (Q12 * DRAIN_H * 3600.0)))
        d_of = [(math.hypot(cx - ox, cy - oy), j) for j, (ox, oy) in enumerate(OF)]; d_of.sort()
        # v2：排去处 = 1.5 km 内满度 < 80% 的规划塘（放进塘的占地单元，由塘接住）；没有才排到出水口（离开系统）
        to_pond, tgt = pick_pond(cx, cy, SUMP_K * V)
        to_xy = pond_xy[to_pond] if to_pond else None
        if to_pond is None:
            tgt = f"PROPOSED OUTFALL #{d_of[0][1]}（{d_of[0][0]:.0f} m）"
        kind_txt = f"比周边低 {depth:.1f} m 的洼地 / 地道，积水主要来自地表" if depth >= DEEP_M else f"地表浅积水（比周边低 {depth:.1f} m）"
        measures.append({**base_rec, "id": mid, "kind": "sump", "n_pumps": n, "q_m3s": round(n * Q12, 3), "to_xy": to_xy, "to_pond": to_pond,
                         "sump_polygon": [[round(x, 1), round(y, 1)] for x, y in hull.exterior.coords],
                         "why": f"{kind_txt}（片内井溢流 {fv:,.0f} m³ 不到一半）：{n} 台 12 英寸地面取水泵，按 3 h 抽完 {V:,.0f} m³，经地面管直接打进{tgt}（塘满即停）" if to_pond else f"{kind_txt}（片内井溢流 {fv:,.0f} m³ 不到一半）：{n} 台 12 英寸地面取水泵，按 3 h 抽完 {V:,.0f} m³，1.5 km 内没有装得下的塘，经地面管排到{tgt}"})
    # ---- 写模型：L2S = base 的 .inp + 转输泵 + 箱涵；二维：dem_edits（挡水）+ pumps2d（地面取水）
    txt = (WORK / f"{name}_{base}.inp").read_text(encoding="utf-8", errors="ignore").replace("· L2+planned", "· L2+suggested v3（L2+planned + 转输 / 地面取水 / 箱涵，按塘剩余库容分配）", 1)
    outs_new, pumps, sto, crv, rules = [], [], [], [], []
    for m in measures:
        if m["kind"] == "conv":
            to = m["to"]
            if to.startswith("X_"):
                ox, oy = m["of_xy"]; outs_new.append((to, ox + 3.0, oy + 3.0))
            if to in pond_xy:
                pumps.append(f"{m['id']}_P  {m['from']}  {to}  PC_{m['id']}  OFF  0  0")
                rules.append(f"RULE R_{m['id']}_FULL\nIF NODE {to} DEPTH >= {POND_STOP_M:.2f}\nTHEN PUMP {m['id']}_P STATUS = OFF\nPRIORITY 3\n\n"
                             f"RULE R_{m['id']}_ON\nIF NODE {m['from']} DEPTH > 0.30\nAND NODE {to} DEPTH < {POND_STOP_M:.2f}\nTHEN PUMP {m['id']}_P STATUS = ON\nPRIORITY 2\n\n"
                             f"RULE R_{m['id']}_OFF\nIF NODE {m['from']} DEPTH < 0.05\nTHEN PUMP {m['id']}_P STATUS = OFF\nPRIORITY 2\n")
            else:
                pumps.append(f"{m['id']}_P  {m['from']}  {to}  PC_{m['id']}  ON  0.30  0.05")
            crv.append(f"PC_{m['id']}  PUMP2  0  {m['q_m3s']:.4f}\nPC_{m['id']}  100  {m['q_m3s']:.4f}")
        if m["kind"] == "box":
            nd = f"BOX_{m['id']}"; zb = junc[m["node"]][0] - 0.5; A = m["vol_m3"] / 3.0
            sto.append(f"{nd}  {zb:.3f}  3.000  0  TABULAR  CRV_{nd}  0  0  0")
            crv.append(f"CRV_{nd}  STORAGE  0  {A:.1f}\nCRV_{nd}  3  {A:.1f}")
            txt = txt.replace("[CONDUITS]\n", f"[CONDUITS]\nBX_{m['id']}  {m['node']}  {nd}  10  0.013  {junc[m['node']][0]:.3f}  {zb:.3f}  0  0\n", 1)
            txt = txt.replace("[XSECTIONS]\n", f"[XSECTIONS]\nBX_{m['id']}  RECT_CLOSED  1.5  3.0  0  0\n", 1)
            x0, y0 = nxy[m["node"]]; outs_new.append((nd, x0 + 2.0, y0 + 2.0, "coord_only"))
    if sto:
        if "[STORAGE]\n" in txt:
            txt = txt.replace("[STORAGE]\n", "[STORAGE]\n" + "\n".join(sto) + "\n", 1)
        else:                                   # 基准态没有塘就没有 [STORAGE]：放在 [CONDUITS] 前面（节点要先定义）
            txt = txt.replace("[CONDUITS]\n", "[STORAGE]\n" + "\n".join(sto) + "\n\n[CONDUITS]\n", 1)
    real_out = [o for o in outs_new if len(o) == 3]
    if real_out:
        txt = txt.replace("[OUTFALLS]\n", "[OUTFALLS]\n" + "".join(f"{n}  -2.000  FREE  NO\n" for n, _, _ in real_out), 1)
    txt = txt.replace("[COORDINATES]\n", "[COORDINATES]\n" + "".join(f"{o[0]}  {o[1]:.2f}  {o[2]:.2f}\n" for o in outs_new), 1)
    if pumps:
        txt = txt.replace("[PUMPS]\n", "[PUMPS]\n" + "\n".join(pumps) + "\n", 1)
    if crv:
        txt = txt.replace("[CURVES]\n", "[CURVES]\n" + "\n".join(crv) + "\n", 1)
    if rules:
        assert "[CONTROLS]" not in txt
        txt = txt.rstrip("\n") + "\n\n[CONTROLS]\n" + "\n".join(rules) + "\n"
    (WORK / f"{name}_{OUT}.inp").write_text(txt, encoding="utf-8")
    meta2 = dict(meta); meta2["state"] = OUT
    meta2["dem_edits"] = [{"id": m["id"], "polygon": m["polygon"], "crest_m": m["crest_m"]} for m in measures if m["kind"] == "bund"]
    meta2["pumps2d"] = [{"id": m["id"], "q_m3s": m["q_m3s"], "off_m": OFF_M, "sump_polygon": m["sump_polygon"], "xy": [m["cx"], m["cy"]],
                         "to_xy": None, "to_pond": m.get("to_pond"), "pond_cap_frac": POND_STOP_M, "to_node": m.get("to_pond") or "PROPOSED OUTFALL"} for m in measures if m["kind"] == "sump"]
    (C2D / f"pilot_{name}_{OUT}.json").write_text(json.dumps(meta2, ensure_ascii=False), encoding="utf-8")
    mp = []
    for m in measures:
        e = {"kind": m["kind"], "id": m["id"], "ll": m["ll"], "label": m["why"], "how": m["why"]}
        if m["kind"] in ("conv", "sump"):
            e["q_lps"] = round(m["q_m3s"] * 1000)
        if m["kind"] == "conv":
            if m["to"] in pond_xy:
                e["to_ll"] = ll(*pond_xy[m["to"]])
            elif m.get("of_xy"):
                e["to_ll"] = ll(*m["of_xy"])
        if m["kind"] == "sump" and m.get("to_xy"):
            e["to_ll"] = ll(*m["to_xy"])
        if m["kind"] == "box":
            e["vol_m3"] = m["vol_m3"]
        if m["kind"] == "bund":
            e["line"] = [ll(x, y) for x, y in m["hole"]]
        mp.append(e)
    info = {"rain_mm": a.rain, "base": base, "th_m": TH, "n_residual_spots": len(measures) + len(skipped), "measures": measures, "skipped": skipped, "map": mp,
            "summary": {k: sum(1 for m in measures if m["kind"] == k) for k in ("conv", "bund", "sump", "box")},
            "q_total_m3s": round(sum(m.get("q_m3s", 0) for m in measures if m["kind"] in ("conv", "sump")), 2),
            "box_total_m3": sum(m.get("vol_m3", 0) for m in measures if m["kind"] == "box"),
            "version": "v3", "pond_budget_m3": {k: round(budget0[k]) for k in budget0}, "pond_left_m3": {k: round(budget[k]) for k in budget},
            "n_rules": len(rules)}
    (ROOT / "out" / f"c1_{OUT}_inputs_{a.tag}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print("  塘分配（可用 → 余）：" + "；".join(f"{k} {budget0[k]:,.0f}→{budget[k]:,.0f}" for k in budget0))
    print(f"  措施 {len(measures)} 处：{info['summary']}；泵合计 {info['q_total_m3s']} m³/s；箱涵 {info['box_total_m3']:,} m³；不处理 {len(skipped)} 处")
    for m in measures:
        print(f"    {m['id']} {m['kind']:<5} {m['near_hotspot']} {m['near_dist_m']} m · {m['area_ha']} ha · 最大积水 {m['peak_vol_m3']:,} m³ · {m['why']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
