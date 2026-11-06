# -*- coding: utf-8 -*-
"""C1 的 L2+suggested **v4**（2026-10-06）——按「关键积水点尽快消退、且不把别处搞得更严重」设计。

v4 与 v1–v3 的根本差别（前面三版从没量过这两件事）：

  A. **水为什么不退**：L2+planned 在 6 h 末（雨停后 4 h）二维域里还剩 535,351 m³，
     比 2 h 只少 16.7 %；全程边界只放走 97,961 m³。域内 91 % 的滞留水（22.04 ha / 86,690 m³）
     在 2→6 h 之间**水深几乎没有下降**。不是「降得不够多」，是**根本不退**。
     → 判据：光在洼地里抽水（v1/v2/v3 的地面取水泵）只是把水从一个坑挪到另一个坑，
       必须把水**移出二维域**（进塘 = 一维蓄水）或**在冒出来之前就截住**。
  B. **水从哪来**：L2+planned 全场一维溢流井 55,861 座、合计 1,841,015 m³，
     而规划塘只接住 17,287 m³（占 0.9 %）。积水的主因是**井溢流**，不是塘不够。

v4 的两条措施（都要求「往塘里排」，绝不放回地面）：

  ① 【快排 · 地面】按八个热点地表汇水区内的残余积水片（L2+planned 全程 ≥0.30 m 最大值）
     布二维取水泵，按 `--clear-h`（缺省 1.5 h）而不是 v3 的 3 h 定泵量 → 退水更快；
     出水口一律接管到**还有余量的规划塘的一维节点**（couple2way_v2 的 `to_pond`，按塘剩余库容封顶、塘满即停）。
  ② 【截流 · 管网】每个热点 500 m 环内**溢流最多的井**（按溢流量排序取到覆盖 80 %，
     每个热点最多 `--max-conv` 口）装一维固定泵，直接从井里转输到塘 —— 在冒上地面之前把水拿走。

**不新增副作用**：不改地形（v1 的土堤在 ADM-90 旁造成堤外 3.9 ha 变深，已废）；
所有泵只往塘 / 出水口排，不往地面排；塘按剩余库容封顶（`--pond-cap` 缺省 0.90）。
跑完必须过 `tools/c1_l2s_side_effects.py`（比 L2+planned 变深 ≥0.05 m 的总面积应 ≈ 0）。

Claude 自定的参数（用户 2026-10-06 要我按能实际解决问题的办法做，逐条列在这里以便回溯）：
  CLEAR_H=1.5 h（清空时限，v3 是 3 h）、NEAR_R=500 m（沿用 v3）、
  MAX_CONV_PER_HS=4 口井/热点、CONV_COVER=0.80、OFF_M=0.03 m（抽到 3 cm 停，比 v3 的 5 cm 退得更干净）、
  POND_CAP=0.90、POND_R 阶梯 1500 → 3000 m → 出水口、LINK=150 m（把 25 m 的碎片并成可布泵的作业面）。

用法：python tools/c1_design_l2s4.py --rain 61 [--tag M61] [--base L2P] [--dry]
产物：$WORK/C1_{tag}_L2S4.inp、c2d/pilot_C1_{tag}_L2S4.json、out/c1_L2S4_inputs_{tag}.json
"""
from __future__ import annotations

import argparse
import json
import os
import math
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from build_c1_l2 import read_inp  # noqa: E402

WORK = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))
C2D = WORK / "c2d"
OUT = "L2S4"

Q12 = 0.36           # 12 英寸泵额定流量 m³/s（沿用 29.RB2 / Parsons 泵曲线 361 L/s @23 m）
CLEAR_H = 1.5        # ① 地面取水：把这一片的水在这么多小时内抽完（v3 是 3 h）
NEAR_R = 500.0       # 只处理八个热点 500 m 以内的残余积水（沿用 v3）
TH = 0.30            # 积水判据 m
LINK = 150.0         # 残余片的连接半径：把 25 m 的碎片并成能布泵的作业面
MIN_VOL = 100.0      # 小于这个最大积水量的片不布泵
OFF_M = 0.03         # 抽到 3 cm 停
CONV_COVER = 0.80    # ② 截流：每个热点取溢流量前 80 %
MAX_CONV_HS = 4      # ② 每个热点最多几口井
POND_R, OF_R = 1500.0, 3000.0
POND_CAP = 0.90      # 塘占用上限 = 设计容积 × 这个系数
MAX_PUMP_PATCH = 8   # 单片最多几台
HS = ["ADM-%s" % n for n in (21, 90, 94, 104, 192, 202, 486, 487)]


def node_flood(rpt):
    t = pathlib.Path(rpt).read_text(errors="ignore")
    m = re.search(r"Node Flooding Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        q = ln.split()
        if len(q) >= 6:
            try:
                out[q[0]] = {"hours": float(q[1]), "rate_m3s": float(q[2]), "vol_m3": float(q[5]) * 1000.0}
            except ValueError:
                pass
    return out


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--rain", type=int, required=True)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--base", default="L2P")
    ap.add_argument("--clear-h", type=float, default=CLEAR_H)
    ap.add_argument("--near-r", type=float, default=NEAR_R)
    ap.add_argument("--max-conv", type=int, default=MAX_CONV_HS)
    ap.add_argument("--no-conv", action="store_true", help="只做地面快排，不做管网截流")
    ap.add_argument("--dry", action="store_true", help="只算措施清单，不写模型")
    a = ap.parse_args(argv)
    a.tag = a.tag or f"R{a.rain}"
    name = f"C1_{a.tag}"
    base = a.base

    import geopandas as gpd
    from matplotlib.path import Path as MPath
    from pyproj import Transformer
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    from shapely.geometry import MultiPoint, Point
    from shapely.ops import unary_union

    tr84 = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    ll = lambda x, y: [round(v, 6) for v in tr84.transform(x, y)[::-1]]   # noqa: E731

    z = np.load(C2D / f"{name}_{base}_2d.npz")
    xy = z["xy"].astype(float); area = z["area"].astype(float)
    hm = z["hmax"].astype(float); elev = z["elev"].astype(float); fr = z["frames"]
    t_h = z["t_h"]
    meta = json.loads((C2D / f"pilot_{name}_{base}.json").read_text(encoding="utf-8"))
    tw = json.loads((C2D / f"twoway_{name}_{base}.json").read_text(encoding="utf-8"))
    nf = node_flood(C2D / f"{name}_{base}_2way.rpt")
    S = read_inp(WORK / f"{name}_{base}.inp")
    junc = {r[0]: (float(r[1]), float(r[2])) for r in S["[JUNCTIONS]"]}
    nxy = {r[0]: (float(r[1]), float(r[2])) for r in S["[COORDINATES]"]}
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    CA = gpd.read_file(ROOT / "console" / "data" / "hotspot_catch_C1.geojson").to_crs(32640)
    CA = CA[CA.kind != "合计"]

    # 计算域 = 八个热点的地表汇水区并集（客户说的「C1 范围内」）
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
    print(f"{name} {base}：残余 ≥{TH} m 单元 {len(wet):,}（{area[wet].sum()/1e4:.2f} ha）")
    T = cKDTree(xy[wet]); pr = T.query_pairs(LINK, output_type="ndarray")
    G = coo_matrix((np.ones(len(pr)), (pr[:, 0], pr[:, 1])), shape=(len(wet), len(wet)))
    nc, lab = connected_components(G, directed=False)
    print(f"  {LINK:.0f} m 连接 → {nc} 片作业面")

    trAll = cKDTree(xy)
    trN = cKDTree(np.array([nxy[k] for k in junc if k in nxy])); jn = [k for k in junc if k in nxy]
    # 塘：可用库容 = 设计容积 × POND_CAP − L2+planned 末态已占
    pond_xy = {p["node"]: p["xy"] for p in meta.get("ponds", [])}
    pfill = {p["unitid"]: (p.get("fill_pct") or 0) for p in tw.get("ponds", [])}
    budget = {k: POND_CAP * float(p["vol"]) * (1 - pfill.get(p["unitid"], 0) / 100.0) for p in meta.get("ponds", []) for k in [p["node"]]}
    budget0 = dict(budget)
    OF = json.loads((ROOT / "out" / "c1_proposed_outfalls.json").read_text(encoding="utf-8"))["outfall_labels_utm"]

    def pick_pond(cx, cy, need):
        """按距离阶梯找还有余量的塘；都不行再找出水口。"""
        for R in (POND_R, OF_R):
            c = sorted((math.hypot(cx - px, cy - py), k) for k, (px, py) in pond_xy.items() if budget[k] >= need)
            if c and c[0][0] <= R:
                d, k = c[0]; budget[k] -= need
                return k, f"规划塘 {k}（{d:.0f} m；可用 {budget0[k]:,.0f} m³，分给这一处 {need:,.0f} m³，余 {budget[k]:,.0f} m³）"
        d_of = sorted((math.hypot(cx - ox, cy - oy), i) for i, (ox, oy) in enumerate(OF))
        if d_of and d_of[0][0] <= OF_R:
            d, i = d_of[0]
            return f"X_OF{i}", f"PROPOSED OUTFALL #{i}（{d:.0f} m，新建出水口，离开系统）"
        return None, None

    FW = fr[:, wet].astype(np.float32) * area[wet].astype(np.float32)
    pos = {int(c): i for i, c in enumerate(wet)}
    vol_series = lambda cells: FW[:, [pos[int(c)] for c in cells]].sum(1)   # noqa: E731

    measures, skipped = [], []
    order = sorted(range(nc), key=lambda c: -float(vol_series(wet[lab == c]).max()))
    for c in order:
        cells = wet[lab == c]; A = float(area[cells].sum())
        cx, cy = (xy[cells] * area[cells, None]).sum(0) / A
        dh = {h: math.hypot(cx - H.loc[h].geometry.x, cy - H.loc[h].geometry.y) for h in HS}
        nh = min(dh, key=dh.get)
        V = float(vol_series(cells).max())
        rec = {"cx": round(cx, 1), "cy": round(cy, 1), "ll": ll(cx, cy), "near_hotspot": nh,
               "near_dist_m": round(dh[nh]), "area_ha": round(A / 1e4, 3), "peak_vol_m3": round(V)}
        if dh[nh] > a.near_r:
            skipped.append({**rec, "why": f"离最近热点 {dh[nh]:.0f} m > {a.near_r:.0f} m，不处理"}); continue
        if V < MIN_VOL:
            skipped.append({**rec, "why": f"很小（最大积水 {V:.0f} m³ < {MIN_VOL:.0f} m³），不处理"}); continue
        hull = MultiPoint([tuple(p) for p in xy[cells]]).convex_hull.buffer(8.0)
        ring = trAll.query_ball_point((cx, cy), math.sqrt(A / math.pi) + 150.0)
        ring = [i for i in ring if not hull.contains(Point(xy[i]))]
        g_ring = float(np.median(elev[ring])) if ring else float(elev[cells].max())
        depth = g_ring - float(elev[cells].min())
        n = min(MAX_PUMP_PATCH, max(1, math.ceil(V / (Q12 * a.clear_h * 3600.0))))
        tgt, how = pick_pond(cx, cy, V)
        if tgt is None:
            skipped.append({**rec, "why": f"1.5 / 3 km 内没有装得下 {V:,.0f} m³ 的塘或出水口（塘都满了），暂不处理"}); continue
        measures.append({**rec, "id": f"S{len(measures)+1:02d}", "kind": "sump", "n_pumps": n, "q_m3s": round(n * Q12, 3),
                         "to_pond": tgt if not tgt.startswith("X_") else None, "of_xy": OF[int(tgt.split("OF")[-1])] if tgt.startswith("X_") else None,
                         "depth_m": round(depth, 2), "ring_ground_m": round(g_ring, 2),
                         "sump_polygon": [[round(x, 1), round(y, 1)] for x, y in hull.exterior.coords],
                         "why": f"比周边低 {depth:.2f} m，最大积水 {V:,.0f} m³ / {A/1e4:.2f} ha：{n} 台 12 英寸地面取水泵，"
                                f"按 {a.clear_h:g} h 抽完（{n*Q12:.2f} m³/s），直接打进{how}，塘满即停"})

    # ② 管网截流：每个热点 500 m 环内溢流最多的井（同一口井只装一台泵，避免跨热点重复）
    convs = []
    if not a.no_conv:
        used = set()
        for h in HS:
            px, py = H.loc[h].geometry.x, H.loc[h].geometry.y
            sel = [k for k in jn if k not in used and math.hypot(nxy[k][0] - px, nxy[k][1] - py) <= a.near_r and k in nf]
            if not sel:
                continue
            sel.sort(key=lambda k: -nf[k]["vol_m3"])
            tot = sum(nf[k]["vol_m3"] for k in sel); cv = 0.0; take = []
            for k in sel:
                if cv >= CONV_COVER * tot or len(take) >= a.max_conv:
                    break
                cv += nf[k]["vol_m3"]; take.append(k)
            for rank, k in enumerate(take, 1):
                v = nf[k]; q = max(1, math.ceil(v["rate_m3s"] / Q12))
                tgt, how = pick_pond(nxy[k][0], nxy[k][1], max(v["vol_m3"], 200.0))
                if tgt is None or tgt.startswith("X_"):
                    continue                              # 塘装不下就不动这口井（绝不往地面排）
                used.add(k)
                convs.append({"id": f"C{len(convs)+1:02d}", "node": k, "to_pond": tgt, "n_pumps": q,
                              "q_m3s": round(q * Q12, 3), "vol_m3": round(v["vol_m3"]), "rate_m3s": round(v["rate_m3s"], 2),
                              "near_hotspot": h, "near_dist_m": round(math.hypot(nxy[k][0] - px, nxy[k][1] - py)),
                              "why": f"{h} 500 m 内溢流第 {rank} 多的井（L2+planned 溢流 {v['vol_m3']:,.0f} m³，峰 {v['rate_m3s']:.2f} m³/s）："
                                     f"{q} 台 12 英寸固定泵在冒出地面之前转输到{how}"})

    q_sum = sum(m["q_m3s"] for m in measures) + sum(m["q_m3s"] for m in convs)
    n_pump = sum(m["n_pumps"] for m in measures) + sum(m["n_pumps"] for m in convs)
    print(f"  ① 地面快排 {len(measures)} 处（{sum(m['n_pumps'] for m in measures)} 台，"
          f"{sum(m['q_m3s'] for m in measures):.2f} m³/s）；不处理 {len(skipped)} 处")
    print(f"  ② 管网截流 {len(convs)} 口井（{sum(m['n_pumps'] for m in convs)} 台，{sum(m['q_m3s'] for m in convs):.2f} m³/s）")
    print(f"  合计 {n_pump} 台 / {q_sum:.2f} m³/s；塘分配后余量：" +
          "、".join(f"{k} {budget[k]:,.0f}" for k in sorted(budget, key=lambda k: budget[k])[:5]))
    for m in measures:
        print(f"    {m['id']} {m['near_hotspot']} {m['near_dist_m']} m · {m['area_ha']} ha · {m['peak_vol_m3']:,} m³ · {m['why']}")
    for m in convs:
        print(f"    {m['id']} {m['near_hotspot']} {m['near_dist_m']} m · {m['why']}")
    if a.dry:
        return 0

    # ---------------------------------------------------------------- 写模型
    txt = (WORK / f"{name}_{base}.inp").read_text(encoding="utf-8", errors="ignore").replace(
        "· L2+planned", "· L2+suggested v4（L2+planned + 地面快排 + 管网截流，全部排入规划塘）", 1)
    outs_new, pumps, crv, rules = [], [], [], []
    for m in convs:                                        # ② 转输：一维泵 + 控制规则
        pumps.append(f"{m['id']}_P  {m['node']}  {m['to_pond']}  PC_{m['id']}  OFF  0  0")
        crv.append(f"PC_{m['id']}  PUMP2  0  {m['q_m3s']:.4f}\nPC_{m['id']}  100  {m['q_m3s']:.4f}")
        rules.append(f"RULE R_{m['id']}_FULL\nIF NODE {m['to_pond']} DEPTH >= 0.90\nTHEN PUMP {m['id']}_P STATUS = OFF\nPRIORITY 3\n\n"
                     f"RULE R_{m['id']}_ON\nIF NODE {m['node']} DEPTH > 0.30\nAND NODE {m['to_pond']} DEPTH < 0.90\nTHEN PUMP {m['id']}_P STATUS = ON\nPRIORITY 2\n\n"
                     f"RULE R_{m['id']}_OFF\nIF NODE {m['node']} DEPTH < 0.05\nTHEN PUMP {m['id']}_P STATUS = OFF\nPRIORITY 2\n")
    if pumps:
        if "[PUMPS]\n" not in txt:
            raise SystemExit(f"{base} 的 .inp 里没有 [PUMPS] 段，v4 的截流泵没法加")
        txt = txt.replace("[PUMPS]\n", "[PUMPS]\n" + "\n".join(pumps) + "\n", 1)
    if crv:
        txt = txt.replace("[CURVES]\n", "[CURVES]\n" + "\n".join(crv) + "\n", 1)
    if rules:
        if "[CONTROLS]" not in txt:                        # 基准态没有 [CONTROLS] 段：补一段
            txt = txt.rstrip("\n") + "\n\n[CONTROLS]\n" + "\n".join(rules) + "\n"
        else:
            txt = txt.replace("[CONTROLS]\n", "[CONTROLS]\n" + "\n".join(rules) + "\n", 1)
        if re.search(r"^CONTROLS\s+NO", txt, re.M):        # 基准态 OPTIONS 是 CONTROLS NO：不打开规则不生效
            txt = re.sub(r"^CONTROLS\s+NO", "CONTROLS  YES", txt, count=1, flags=re.M)
    (WORK / f"{name}_{OUT}.inp").write_text(txt, encoding="utf-8")

    meta2 = dict(meta)
    meta2["state"] = OUT
    meta2["dem_edits"] = []                                 # v4 不改地形
    meta2["pumps2d"] = [{"id": m["id"], "q_m3s": m["q_m3s"], "off_m": OFF_M, "sump_polygon": m["sump_polygon"],
                         "xy": [m["cx"], m["cy"]], "to_xy": None, "to_pond": m.get("to_pond"),
                         "pond_cap_frac": POND_CAP, "to_node": m.get("to_pond") or "PROPOSED OUTFALL"}
                        for m in measures]
    (C2D / f"pilot_{name}_{OUT}.json").write_text(json.dumps(meta2, ensure_ascii=False), encoding="utf-8")

    info = {"rain_mm": a.rain, "base": base, "version": "v4", "th_m": TH, "clear_h": a.clear_h, "near_r_m": a.near_r,
            "link_m": LINK, "max_conv_hs": a.max_conv, "conv_cover": CONV_COVER, "pond_cap": POND_CAP, "off_m": OFF_M,
            "n_patches": nc, "measures": measures, "convs": convs, "skipped": skipped,
            "summary": {"sump": len(measures), "conv": len(convs), "skipped": len(skipped)},
            "n_pumps": n_pump, "q_total_m3s": round(q_sum, 2),
            "pond_budget_m3": {k: round(v) for k, v in budget0.items()}, "pond_left_m3": {k: round(v) for k, v in budget.items()},
            "map": [{"kind": "sump", "id": m["id"], "ll": m["ll"], "label": m["why"], "q_lps": round(m["q_m3s"] * 1000)} for m in measures]
                   + [{"kind": "conv", "id": m["id"], "ll": ll(*nxy[m["node"]]), "label": m["why"], "q_lps": round(m["q_m3s"] * 1000)} for m in convs]}
    (ROOT / "out" / f"c1_{OUT}_inputs_{a.tag}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写 {WORK / f'{name}_{OUT}.inp'}、c2d/pilot_{name}_{OUT}.json、out/c1_{OUT}_inputs_{a.tag}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
