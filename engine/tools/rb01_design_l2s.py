# -*- coding: utf-8 -*-
"""RB-01 的 L2+suggested：按 C1 的 v4 → v5 规则（用户 2026-10-16「都按建议来」= 用 C1 最新 v5 规则）。

v5 的定义离不开 v4 的运行结果（「v4 里几乎不出力的泵去掉」），所以照 C1 的顺序走两步：
  v4   python tools/rb01_design_l2s.py v4   按 RB01_{tag}_L2P 结果：① 残余积水片（全程 ≥0.30 m、离 6 个热点 500 m 内、
       不在塘里、≥100 m³）布地面取水泵，1.5 h 抽完，打进还有余量的塘（1.5 km → 3 km）；② 每个热点 500 m 内溢流最多的井
       （覆盖 80 %、每个热点最多 4 口）装 12 英寸截流泵进塘；塘占用上限 90 %；不改地形。参数全部沿用 c1_design_l2s4。
  v5   python tools/rb01_design_l2s.py v5   按 v4 的运行结果：去掉没开过 / 运行 < 5 % / 抽水 < 100 m³ 的泵；
       截流井能在 500 m 管长内找到比井口低 0.5 m 的空地 → 换成「重力管 + 新塘（1.5 m 深，容积 2 × 溢流量）」，否则保留泵。
       塘址判据、管径、并塘规则全部沿用 c1_design_l2s5；土地 = OSM（out/osm_rb01，2026-10-16 下载）。
与 C1 不同处（Claude 自定，2026-10-16）：
  · 残余积水的范围 = 整个 RB-01 计算域（C1 是 8 个热点的地表汇水区；RB-01 本身就是一个汇水区）
  · RB-01 没有顾问的 PROPOSED OUTFALL：塘都装不下的那一处不布措施，列出来
产物：$WORK/RB01_{tag}_L2S4|L2S5.inp、c2d/pilot_…json、out/rb01_L2S4|L2S5_inputs_{tag}.json
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import c1_design_l2s4 as V4  # noqa: E402
import c1_design_l2s5 as V5  # noqa: E402
from build_c1_l2 import read_inp  # noqa: E402
from rb01_build import HS  # noqa: E402
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
V5.OSM = ROOT / "out" / "osm_rb01"
Q12, CLEAR_H, NEAR_R, TH, LINK, MIN_VOL, OFF_M = V4.Q12, V4.CLEAR_H, V4.NEAR_R, V4.TH, V4.LINK, V4.MIN_VOL, V4.OFF_M
CONV_COVER, MAX_CONV_HS, POND_R, OF_R, POND_CAP, MAX_PUMP_PATCH = V4.CONV_COVER, V4.MAX_CONV_HS, V4.POND_R, V4.OF_R, V4.POND_CAP, V4.MAX_PUMP_PATCH
IDLE_PCT, IDLE_M3 = 5.0, 100.0       # v5 去泵判据（C1 v5：一次没开 / 运行 < 5 % / < 100 m³）


def pond_cells(meta, xy):
    from matplotlib.path import Path as MPath
    m = np.zeros(len(xy), bool)
    for p in meta.get("ponds", []):
        if p.get("underground"):                # 地下调蓄池不占地表（2026-10-21）
            continue
        fp = p["footprint"]; polys = fp["coordinates"] if fp["type"] == "MultiPolygon" else [fp["coordinates"]]
        for poly in polys:
            m |= MPath(np.array(poly[0])[:, :2]).contains_points(xy)
    return m


def add_pumps(txt, pumps, crv, rules):
    if pumps:
        txt = txt.replace("[PUMPS]\n", "[PUMPS]\n" + "\n".join(pumps) + "\n", 1)
    if crv:
        txt = txt.replace("[CURVES]\n", "[CURVES]\n" + "\n".join(crv) + "\n", 1)
    if rules:
        txt = (txt.replace("[CONTROLS]\n", "[CONTROLS]\n" + "\n".join(rules) + "\n", 1) if "[CONTROLS]" in txt
               else txt.rstrip("\n") + "\n\n[CONTROLS]\n" + "\n".join(rules) + "\n")
        txt = re.sub(r"^CONTROLS\s+NO", "CONTROLS  YES", txt, count=1, flags=re.M)
    return txt


def conv_rule(m):
    return (f"RULE R_{m['id']}_FULL\nIF NODE {m['to_pond']} DEPTH >= 0.90\nTHEN PUMP {m['id']}_P STATUS = OFF\nPRIORITY 3\n\n"
            f"RULE R_{m['id']}_ON\nIF NODE {m['node']} DEPTH > 0.30\nAND NODE {m['to_pond']} DEPTH < 0.90\nTHEN PUMP {m['id']}_P STATUS = ON\nPRIORITY 2\n\n"
            f"RULE R_{m['id']}_OFF\nIF NODE {m['node']} DEPTH < 0.05\nTHEN PUMP {m['id']}_P STATUS = OFF\nPRIORITY 2\n")
def design_v4(tag):
    import geopandas as gpd
    from pyproj import Transformer
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    from shapely.geometry import MultiPoint, Point
    name = f"RB01_{tag}"; base = "L2P"
    tr84 = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    ll = lambda x, y: [round(v, 6) for v in tr84.transform(x, y)[::-1]]   # noqa: E731
    z = np.load(C2D / f"{name}_{base}_2d.npz")
    xy = z["xy"].astype(float); area = z["area"].astype(float); hm = z["hmax"].astype(float); elev = z["elev"].astype(float); fr = z["frames"]
    meta = json.loads((C2D / f"pilot_{name}_{base}.json").read_text(encoding="utf-8"))
    tw = json.loads((C2D / f"twoway_{name}_{base}.json").read_text(encoding="utf-8"))
    nf = V4.node_flood(C2D / f"{name}_{base}_2way.rpt")
    S = read_inp(WORK / f"{name}_{base}.inp")
    junc = {r[0] for r in S["[JUNCTIONS]"]}
    nxy = {r[0]: (float(r[1]), float(r[2])) for r in S["[COORDINATES]"]}
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    inpond = pond_cells(meta, xy)
    wet = np.flatnonzero((hm >= TH) & ~inpond)
    print(f"{name} {base}：残余 ≥{TH} m 单元 {len(wet):,}（{area[wet].sum() / 1e4:.2f} ha，整个计算域、塘外）")
    T = cKDTree(xy[wet]); pr = T.query_pairs(LINK, output_type="ndarray")
    G = coo_matrix((np.ones(len(pr)), (pr[:, 0], pr[:, 1])), shape=(len(wet), len(wet)))
    nc, lab = connected_components(G, directed=False)
    trAll = cKDTree(xy)
    pond_xy = {p["node"]: p["xy"] for p in meta["ponds"]}
    pfill = {p["unitid"]: (p.get("fill_pct") or 0) for p in tw.get("ponds", [])}
    budget = {p["node"]: POND_CAP * float(p["vol"]) * (1 - pfill.get(p["unitid"], 0) / 100.0) for p in meta["ponds"]}
    budget0 = dict(budget)

    def pick_pond(cx, cy, need):
        for R in (POND_R, OF_R):
            c = sorted((math.hypot(cx - px, cy - py), k) for k, (px, py) in pond_xy.items() if budget[k] >= need)
            if c and c[0][0] <= R:
                d, k = c[0]; budget[k] -= need
                return k, f"塘 {k}（{d:.0f} m；可用 {budget0[k]:,.0f} m³，分给这一处 {need:,.0f} m³，余 {budget[k]:,.0f} m³）"
        return None, None

    FW = fr[:, wet].astype(np.float32) * area[wet].astype(np.float32)
    measures, skipped = [], []
    for c in sorted(range(nc), key=lambda c: -float(FW[:, lab == c].sum(1).max())):
        sel = lab == c; cells = wet[sel]; A = float(area[cells].sum())
        cx, cy = (xy[cells] * area[cells, None]).sum(0) / A
        dh = {h: math.hypot(cx - H.loc[h].geometry.x, cy - H.loc[h].geometry.y) for h in HS}
        nh = min(dh, key=dh.get); V = float(FW[:, sel].sum(1).max())
        rec = {"cx": round(cx, 1), "cy": round(cy, 1), "ll": ll(cx, cy), "near_hotspot": nh, "near_dist_m": round(dh[nh]),
               "area_ha": round(A / 1e4, 3), "peak_vol_m3": round(V)}
        if dh[nh] > NEAR_R:
            skipped.append({**rec, "why": f"离最近热点 {dh[nh]:.0f} m > {NEAR_R:.0f} m"}); continue
        if V < MIN_VOL:
            skipped.append({**rec, "why": f"最大积水 {V:.0f} m³ < {MIN_VOL:.0f} m³"}); continue
        hull = MultiPoint([tuple(p) for p in xy[cells]]).convex_hull.buffer(8.0)
        ring = [i for i in trAll.query_ball_point((cx, cy), math.sqrt(A / math.pi) + 150.0) if not hull.contains(Point(xy[i]))]
        g_ring = float(np.median(elev[ring])) if ring else float(elev[cells].max())
        depth = g_ring - float(elev[cells].min())
        n = min(MAX_PUMP_PATCH, max(1, math.ceil(V / (Q12 * CLEAR_H * 3600.0))))
        tgt, how = pick_pond(cx, cy, V)
        if tgt is None:
            skipped.append({**rec, "why": f"3 km 内没有装得下 {V:,.0f} m³ 的塘（RB-01 没有规划出水口）"}); continue
        measures.append({**rec, "id": f"S{len(measures) + 1:02d}", "kind": "sump", "n_pumps": n, "q_m3s": round(n * Q12, 3),
                         "to_pond": tgt, "depth_m": round(depth, 2), "ring_ground_m": round(g_ring, 2),
                         "sump_polygon": [[round(x, 1), round(y, 1)] for x, y in hull.exterior.coords],
                         "why": f"比周边低 {depth:.2f} m，最大积水 {V:,.0f} m³ / {A / 1e4:.2f} ha：{n} 台 12 英寸地面取水泵，"
                                f"按 {CLEAR_H:g} h 抽完，打进{how}，塘满即停"})
    convs, used = [], set()
    for h in HS:
        px, py = H.loc[h].geometry.x, H.loc[h].geometry.y
        sel = sorted((k for k in junc if k in nf and k not in used and math.hypot(nxy[k][0] - px, nxy[k][1] - py) <= NEAR_R),
                     key=lambda k: -nf[k]["vol_m3"])
        tot = sum(nf[k]["vol_m3"] for k in sel); cv = 0.0; take = []
        for k in sel:
            if cv >= CONV_COVER * tot or len(take) >= MAX_CONV_HS:
                break
            cv += nf[k]["vol_m3"]; take.append(k)
        for rank, k in enumerate(take, 1):
            v = nf[k]; q = max(1, math.ceil(v["rate_m3s"] / Q12))
            tgt, how = pick_pond(nxy[k][0], nxy[k][1], max(v["vol_m3"], 200.0))
            if tgt is None:
                skipped.append({"node": k, "near_hotspot": h, "why": "截流：3 km 内没有装得下的塘"}); continue
            used.add(k)
            convs.append({"id": f"C{len(convs) + 1:02d}", "node": k, "to_pond": tgt, "n_pumps": q, "q_m3s": round(q * Q12, 3),
                          "vol_m3": round(v["vol_m3"]), "rate_m3s": round(v["rate_m3s"], 2), "near_hotspot": h,
                          "near_dist_m": round(math.hypot(nxy[k][0] - px, nxy[k][1] - py)),
                          "why": f"{h} 500 m 内溢流第 {rank} 多的井（L2+planned 溢流 {v['vol_m3']:,.0f} m³，峰 {v['rate_m3s']:.2f} m³/s）："
                                 f"{q} 台 12 英寸固定泵转输到{how}"})
    print(f"  ① 地面快排 {len(measures)} 处 / {sum(m['n_pumps'] for m in measures)} 台；② 截流 {len(convs)} 口 / "
          f"{sum(m['n_pumps'] for m in convs)} 台；不处理 {len(skipped)} 处")
    for m in measures + convs:
        print(f"    {m['id']} {m['near_hotspot']} {m['near_dist_m']} m · {m['why']}")
    txt = (WORK / f"{name}_{base}.inp").read_text(encoding="utf-8").replace("L2+planned", "L2+suggested v4（L2+planned + 地面快排 + 截流，进塘）", 1)
    txt = add_pumps(txt, [f"{m['id']}_P  {m['node']}  {m['to_pond']}  PC_{m['id']}  OFF  0  0" for m in convs],
                    [f"PC_{m['id']}  PUMP2  0  {m['q_m3s']:.4f}\nPC_{m['id']}  100  {m['q_m3s']:.4f}" for m in convs],
                    [conv_rule(m) for m in convs])
    (WORK / f"{name}_L2S4.inp").write_text(txt, encoding="utf-8")
    meta2 = dict(meta, state="L2S4", dem_edits=[])
    meta2["pumps2d"] = [{"id": m["id"], "q_m3s": m["q_m3s"], "off_m": OFF_M, "sump_polygon": m["sump_polygon"], "xy": [m["cx"], m["cy"]],
                         "to_xy": None, "to_pond": m["to_pond"], "pond_cap_frac": POND_CAP, "to_node": m["to_pond"]} for m in measures]
    (C2D / f"pilot_{name}_L2S4.json").write_text(json.dumps(meta2, ensure_ascii=False), encoding="utf-8")
    info = {"version": "v4", "tag": tag, "measures": measures, "convs": convs, "skipped": skipped,
            "n_pumps": sum(m["n_pumps"] for m in measures + convs), "q_total_m3s": round(sum(m["q_m3s"] for m in measures + convs), 2),
            "pond_budget_m3": {k: round(v) for k, v in budget0.items()}, "pond_left_m3": {k: round(v) for k, v in budget.items()},
            "scope_note": "残余积水范围 = 整个 RB-01 计算域（Claude 自定）"}
    (ROOT / "out" / f"rb01_L2S4_inputs_{tag}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  → {name}_L2S4.inp：{info['n_pumps']} 台 / {info['q_total_m3s']} m³/s")
def pump_util(rpt):
    """Pumping Summary → {泵: (运行 %, 抽水 m³)}"""
    t = pathlib.Path(rpt).read_text(errors="ignore")
    m = re.search(r"Pumping Summary\s*\n\s*\*+\s*\n.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*(?:-{10,}|\n)", t, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        q = ln.split()
        if len(q) >= 7:
            try:
                out[q[0]] = (float(q[1]), float(q[6]) * 1000.0)
            except ValueError:
                pass
    return out


def design_v5(tag):
    import rasterio
    from rasterio.features import rasterize, shapes
    from rasterio.windows import from_bounds
    from scipy import ndimage
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union
    name = f"RB01_{tag}"
    v4 = json.loads((ROOT / "out" / f"rb01_L2S4_inputs_{tag}.json").read_text(encoding="utf-8"))
    tw4 = json.loads((C2D / f"twoway_{name}_L2S4.json").read_text(encoding="utf-8"))
    pu = pump_util(C2D / f"{name}_L2S4_2way.rpt")
    drop, why = set(), {}
    for c in v4["convs"]:
        u, v = pu.get(f"{c['id']}_P", (0.0, 0.0))
        if u < IDLE_PCT or v < IDLE_M3:
            drop.add(c["id"]); why[c["id"]] = f"v4 运行 {u:.1f} %、抽 {v:,.0f} m³"
    pumped = {q["id"]: q["pumped_m3"] for q in tw4["pumps2d"]}
    for m in v4["measures"]:
        if pumped.get(m["id"], 0.0) < IDLE_M3:
            drop.add(m["id"]); why[m["id"]] = f"v4 抽 {pumped.get(m['id'], 0.0):,.0f} m³"
    print(f"{name} v5：v4 去掉不出力的 {len(drop)} 个：{why}")
    nf = V4.node_flood(C2D / f"{name}_L2P_2way.rpt")
    S = read_inp(WORK / f"{name}_L2P.inp")
    rim_of = {r[0]: float(r[1]) + float(r[2]) for r in S["[JUNCTIONS]"]}
    nxy = {r[0]: (float(r[1]), float(r[2])) for r in S["[COORDINATES]"]}
    meta = json.loads((C2D / f"pilot_{name}_L2P.json").read_text(encoding="utf-8"))
    with rasterio.open(C2D / f"dem_{name}.tif") as d0:
        bb = d0.bounds
    with rasterio.open("D:/阿布扎比/DTM_z40_customer/AUH_DTM_5m_Z40.TIF") as ds:
        win = from_bounds(*bb, ds.transform).round_offsets().round_lengths()
        dtm = ds.read(1, window=win).astype(float); tf = ds.window_transform(win)
        dtm[(dtm < -100) | (dtm > 1000)] = np.nan
    cls, land_stat, _ = V5.land_mask(dtm.shape, tf)
    pmask = rasterize(((p["footprint"], 1) for p in meta["ponds"]), out_shape=dtm.shape, transform=tf, fill=0, dtype="uint8").astype(bool)
    pmask = ndimage.binary_dilation(pmask, iterations=2)
    keep = [c for c in v4["convs"] if c["id"] not in drop]
    sumps = [m for m in v4["measures"] if m["id"] not in drop]
    wells = sorted((c["node"] for c in keep), key=lambda k: -nf[k]["vol_m3"])
    ponds, verdict = V5.site_ponds(wells, nxy, rim_of, nf, dtm, tf, cls, pmask)
    for i, p in enumerate(ponds, 1):
        p["node"] = f"PND_RB01_V5_{i:02d}"
    pipes = []
    for c in keep:
        v = verdict[c["node"]]; c["v5"] = v
        if v["how"] != "gravity":
            continue
        p = next(q for q in ponds if q["id"] == v["pond"])
        zin = rim_of[c["node"]] - V5.HEAD_M; L = max(v["L_m"], 10.0)
        d = V5.pipe_d(1.25 * nf[c["node"]]["rate_m3s"], (zin - p["z_top"]) / L, L)
        pipes.append({"id": f"GP_{c['id']}", "from": c["node"], "to": p["node"], "L_m": round(L), "d_m": d, "z_in": round(zin, 3), "conv": c["id"], "pond": p["id"]})
        c["v5"].update(d_m=d, z_in=round(zin, 3))
    for p in ponds:
        p["z_floor"] = round(p["z_top"] - V5.POND_D, 3); p["vol"] = round(p["area_m2"] * V5.POND_D)
        p["footprint"] = mapping(unary_union([shape(g) for g, val in shapes(p["mask"].astype("uint8"), mask=p["mask"], transform=tf) if val == 1]))
        p["cut_m3"] = round(float(np.nansum(np.maximum(dtm[p["mask"]] - p["z_floor"], 0))) * tf.a * tf.a)
    print(f"  土地（5 m 格）：{land_stat}")
    for p in ponds:
        print(f"  {p['id']} {p['node']} 占地 {p['area_m2']:,.0f} m² 顶 {p['z_top']:.2f} 底 {p['z_floor']:.2f} 容积 {p['vol']:,} m³（要 {p['need_m3']:,.0f}）"
              f" 挖方 {p['cut_m3']:,} m³ · {p['land']} · 接 {', '.join(p['wells'])}")
    for c in keep:
        v = c["v5"]
        print(f"  {c['id']} {c['node']} 井口 {rim_of[c['node']]:.2f} 溢流 {nf[c['node']]['vol_m3']:,.0f} m³ → "
              + (f"重力 {v['pond']} {v['L_m']} m Ø{v['d_m']:.2f}" if v["how"] == "gravity" else f"保留泵：{v['why']}"))
    # ---- 写模型（L2P + 保留的截流泵 + 重力管 / 拍门 + 新塘 + 保留的地面泵）
    txt = (WORK / f"{name}_L2P.inp").read_text(encoding="utf-8").replace("L2+planned", "L2+suggested v5（L2+planned + v4 删泵 + 低处新塘重力接井）", 1)
    kp = [c for c in keep if c["v5"]["how"] == "pump"]
    txt = add_pumps(txt, [f"{m['id']}_P  {m['node']}  {m['to_pond']}  PC_{m['id']}  OFF  0  0" for m in kp],
                    [f"PC_{m['id']}  PUMP2  0  {m['q_m3s']:.4f}\nPC_{m['id']}  100  {m['q_m3s']:.4f}" for m in kp], [conv_rule(m) for m in kp])
    zf = {p["id"]: p["z_floor"] for p in ponds}
    rep = {"[STORAGE]\n": "".join(f"{p['node']}  {p['z_floor']:.3f}  {V5.POND_D:.3f}  0  FUNCTIONAL  0  0  {p['area_m2']:.1f}  0  0\n" for p in ponds),
           "[CONDUITS]\n": "".join(f"{q['id']}  {q['from']}  {q['to']}  {q['L_m']:.1f}  {V5.N_MAN}  {q['z_in']:.3f}  {zf[q['pond']]:.3f}  0  0\n" for q in pipes),
           "[XSECTIONS]\n": "".join(f"{q['id']}  CIRCULAR  {q['d_m']:.3f}  0  0  0  1\n" for q in pipes),
           "[COORDINATES]\n": "".join(f"{p['node']}  {p['xy'][0]:.2f}  {p['xy'][1]:.2f}\n" for p in ponds)}
    for sec, body in rep.items():
        txt = txt.replace(sec, sec + body, 1)
    if pipes:
        txt = txt.replace("[INFLOWS]\n", "[LOSSES]\n" + "".join(f"{q['id']}  0.5  1.0  0  YES  0\n" for q in pipes) + "\n[INFLOWS]\n", 1)
    (WORK / f"{name}_L2S5.inp").write_text(txt, encoding="utf-8")
    m4 = json.loads((C2D / f"pilot_{name}_L2S4.json").read_text(encoding="utf-8"))
    meta2 = dict(meta, state="L2S5", dem_edits=[])
    meta2["ponds"] = list(meta["ponds"]) + [{"node": p["node"], "unitid": p["node"], "vol": p["vol"], "xy": list(p["xy"]), "footprint": p["footprint"], "new_v5": True} for p in ponds]
    meta2["pumps2d"] = [q for q in m4["pumps2d"] if q["id"] not in drop]
    (C2D / f"pilot_{name}_L2S5.json").write_text(json.dumps(meta2, ensure_ascii=False), encoding="utf-8")
    info = {"version": "v5", "tag": tag, "drop": why, "land_stat_cells5m": land_stat, "convs": keep, "sumps": [m["id"] for m in sumps],
            "ponds": [{k: v for k, v in p.items() if k not in ("mask",)} for p in ponds], "pipes": pipes,
            "n_pumps": sum(c["n_pumps"] for c in kp) + sum(m["n_pumps"] for m in sumps),
            "q_total_m3s": round(sum(c["q_m3s"] for c in kp) + sum(m["q_m3s"] for m in sumps), 2)}
    (ROOT / "out" / f"rb01_L2S5_inputs_{tag}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"  → {name}_L2S5.inp：泵 {info['n_pumps']} 台 / {info['q_total_m3s']} m³/s，新塘 {len(ponds)} 座，重力管 {len(pipes)} 根")


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("step", choices=["v4", "v5"]); ap.add_argument("--tag", default="H61")
    a = ap.parse_args(argv)
    (design_v4 if a.step == "v4" else design_v5)(a.tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
