# -*- coding: utf-8 -*-
"""C1 的 L2+suggested **v5**（2026-10-07）——v4 删泵 + 低处新建塘（CAPEX Ponds）靠重力接井。

用户 2026-10-07 定的方向（「按照你的建议来」，L2 手段以客户给的措施清单为准）：
  ① v4 里几乎不出力的 8 台泵去掉：C29（一次没开）、C01 / C05 / C11 / C16 / C26 / C32（运行 < 5 %）、S07（< 100 m³）。
  ② 把「往高处现有塘抽水」改成「在井附近找更低的空地挖新塘，井 → 塘 一根重力管（清单的 Surface Pipelines / Ditches）」：
     核对过 32 口截流井只有 C24 比它的塘停泵水位高（0.17 m、1.4 km，没用），现有塘都在高处，所以要新塘。
     能走重力的井：去掉它的泵，换成重力管 + 新塘；走不了重力的井：保留 v4 的泵。
  ③ 土堤 / 隔离墩 / 沙袋这轮不用（只挡不减量）。
  地形 = 客户 5 m DTM（D:/阿布扎比/DTM_z40_customer，与模型井口标高中位差 0.00 m）；
  土地利用 = OpenStreetMap（out/osm/*.json，2026-10-07 下载；内网土地库连不上，用户同意先用 OSM 验证）。

塘址判据（全是 Claude 自定，逐条列出以便回溯 / 让用户改）：
  · 能用的地 = 不在建筑（外扩 3 m）、道路（按等级外扩 2–15 m）、水体 / 湿地、现有规划塘里；
      一类「明确的空地」：OSM landuse=grass/greenery/meadow、natural=sand/scrub、leisure=park/garden；
      二类「OSM 没标注的地」：不在任何 OSM 面里（多半是空地，也可能是 OSM 漏画的），要现场 / 内网库确认；
      其余（工业 / 居住 / 商业用地的地块内、停车场、学校、医院、清真寺、运动场、军事、墓地……）一律不用。
  · 至少 20 m 宽（腐蚀 2 格 5 m 网格后还在的才能做塘心），避开路中隔离带那种细长绿地。
  · 重力：塘顶（占地地面中位数）≤ 井口 − HEAD_M（0.5 m）；重力管进口 = 井口 − HEAD_M，出口 = 塘底，管上装拍门（防倒灌）。
  · 塘深 POND_D = 1.5 m（直立壁，同规划塘的建模方式；阿布扎比地下水浅，再深要勘察）；
    容积 = SIZE_K（2.0）× 这口井 L2+planned 溢流量（重力管在冒井之前 0.5 m 就开始分流，比溢流量多走水）。
  · 重力管长 = 直线 × 1.2，≤ PIPE_MAX（500 m）；管径按「塘满时的水力坡度」通过 1.25 × 溢流峰值取 0.30–0.90 m 里最小的一档。
  · 同一个塘可以接几口井（新塘心 150 m 内已有新塘且高程够，就合并、扩大）。

用法：python tools/c1_design_l2s5.py --tag M61 [--dry]              # 按 M61 L2+planned + v4 的结果设计
      python tools/c1_design_l2s5.py --tag M96 --from M61           # 同一套措施套到 96 mm 的 L2+planned 上验证
产物：$WORK/C1_{tag}_L2S5.inp、c2d/pilot_C1_{tag}_L2S5.json、out/c1_L2S5_inputs_{tag}.json、console/data/c1_l2s5_ponds_{tag}.geojson
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from build_c1_l2 import read_inp  # noqa: E402
from c1_design_l2s4 import node_flood  # noqa: E402

WORK = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))
C2D = WORK / "c2d"
OSM = ROOT / "out" / "osm"
OUT = "L2S5"

DROP = {"C29", "C01", "C05", "C11", "C16", "C26", "C32", "S07"}   # ① v4 里几乎不出力的泵
HEAD_M = 0.5        # 塘顶至少比井口低这么多；重力管进口也设在井口下这么多
POND_D = 1.5        # 新塘深 m
SIZE_K = 2.0        # 新塘容积 = 这个系数 × 井的 L2+planned 溢流量
MIN_VOL = 400.0     # 新塘最小容积 m³
PIPE_MAX = 500.0    # 重力管最长 m（直线 × ROUTE_K）
ROUTE_K = 1.2
MERGE_R = 150.0     # 新井离已有新塘这么近就并进去
PIPE_D = (0.30, 0.45, 0.60, 0.75, 0.90)
N_MAN = 0.012       # 新建重力管：WA-726 表 4-1 GRP（良好）0.012（2026-10-19 起；之前自定 0.013）
OPEN1 = {("landuse", "grass"), ("landuse", "greenery"), ("landuse", "meadow"), ("natural", "sand"),
         ("natural", "scrub"), ("leisure", "park"), ("leisure", "garden")}
WATER = {("natural", "water"), ("natural", "wetland"), ("natural", "beach")}
SKIP = {("natural", "peninsula"), ("natural", "bay"), ("natural", "coastline"), ("natural", "cliff"), ("natural", "tree_row")}
DISTRICT_M2 = 5e5     # 比这大的 landuse 面是片区标签（如整个 Musaffah 工业区 49.7 km²），不是地块，按「没标注」处理
ROAD_W = {"motorway": 15, "trunk": 15, "primary": 12, "secondary": 10, "tertiary": 8, "motorway_link": 6, "trunk_link": 6,
          "primary_link": 6, "secondary_link": 6, "tertiary_link": 6, "residential": 5, "unclassified": 5, "service": 4,
          "construction": 6, "living_street": 4, "pedestrian": 3, "track": 3, "raceway": 5}


def osm_layers():
    """OSM 原始 JSON（out geom）→ {(key, value): [shapely 面…]}、道路线。relation 只取 outer 环拼面。"""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import polygonize, unary_union
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    P = lambda g: [tr.transform(p["lon"], p["lat"]) for p in g]   # noqa: E731
    polys, roads, seen = {}, [], set()
    files = sorted(OSM.glob("*.json"))
    if not files:
        raise SystemExit(f"{OSM} 里没有 OSM 数据")
    for f in files:
        for e in json.loads(f.read_text(encoding="utf-8"))["elements"]:
            if (e["type"], e["id"]) in seen:
                continue
            seen.add((e["type"], e["id"]))
            t = e.get("tags", {})
            key = next(((k, t[k]) for k in ("building", "landuse", "leisure", "natural", "amenity", "aeroway", "highway") if k in t), None)
            if key is None:
                continue
            if key[0] == "building":
                key = ("building", "*")
            if e["type"] == "way" and "geometry" in e:
                xy = P(e["geometry"])
                if key[0] == "highway" and t.get("area") != "yes":
                    if len(xy) >= 2:
                        roads.append((LineString(xy), ROAD_W.get(key[1], 0)))
                    continue
                if len(xy) >= 4 and xy[0] == xy[-1]:
                    g = Polygon(xy).buffer(0)
                    if not g.is_empty:
                        polys.setdefault(key, []).append(g)
            elif e["type"] == "relation":
                rings = [LineString(P(m["geometry"])) for m in e.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
                if rings:
                    g = unary_union(list(polygonize(unary_union(rings))))
                    if not g.is_empty:
                        polys.setdefault(key, []).append(g)
    return polys, roads


def land_mask(shape, transform):
    """5 m 网格上的土地分类：0 不能用、1 明确空地、2 OSM 没标注、3 用地地块内的空地（工业 / 居住 / 商业…，缺省不用）。"""
    from rasterio.features import rasterize
    polys, roads = osm_layers()
    R = lambda geoms: rasterize(((g, 1) for g in geoms), out_shape=shape, transform=transform, fill=0, dtype="uint8").astype(bool)  # noqa: E731
    polys = {k: v for k, v in polys.items() if k not in SKIP}
    for k in [k for k in polys if k[0] == "landuse"]:
        polys[k] = [g for g in polys[k] if g.area <= DISTRICT_M2 or k in OPEN1 or k[1] in ("military", "cemetery")]
    any_poly = R([g for v in polys.values() for g in v])
    open1 = R([g for k, v in polys.items() if k in OPEN1 for g in v])
    hard = R([g.buffer(3.0) for g in polys.get(("building", "*"), [])])
    hard |= R([g for k, v in polys.items() if k in WATER or k[0] in ("aeroway", "amenity") or
               (k[0] == "leisure" and k not in OPEN1) or k[1] in ("military", "cemetery", "construction") for g in v])
    hard |= R([ln.buffer(w) for ln, w in roads if w > 0])
    cls = np.zeros(shape, np.uint8)
    cls[~any_poly] = 2
    cls[any_poly] = 3
    cls[open1] = 1
    cls[hard] = 0
    stat = {k: int(v) for k, v in zip(("hard", "open", "untagged", "parcel"), np.bincount(cls.ravel(), minlength=4))}
    return cls, stat, polys


def pipe_d(q, s, L):
    """满管 Manning：通过 q 的最小一档管径；都不够给最大一档。"""
    for d in PIPE_D:
        a_ = math.pi * d * d / 4.0
        if a_ * (d / 4.0) ** (2 / 3) * math.sqrt(max(s, 1e-5)) / N_MAN >= q:
            return d
    return PIPE_D[-1]


def site_ponds(wells, nxy, rim_of, nf, dtm, transform, cls, pond_mask):
    """给每口截流井找一处重力能进的新塘址。wells 按溢流量从大到小。返回 (新塘列表, 每口井的结论)。

    做法：可用地（cls 1 / 2，不在已有塘里）且地面 ≤ 井口 − HEAD_M 的格子连成块；
    从井出发按直线距离找最近的、20 m 宽以上的格子做塘心，所在块够大就从塘心往外长出需要的面积。
    已有新塘离井更近（或差不多近，≤ +MERGE_R）且塘顶够低 → 并进去（一个塘接几口井）。"""
    from scipy import ndimage
    from rasterio.transform import rowcol, xy as rc2xy
    res = transform.a
    free = ((cls == 1) | (cls == 2)) & ~pond_mask & np.isfinite(dtm)
    ponds, verdict = [], {}
    for k in wells:
        x0, y0 = nxy[k]; zr = rim_of[k]; zt = zr - HEAD_M
        need = max(MIN_VOL, SIZE_K * nf[k]["vol_m3"]); a_need = need / POND_D
        r0, c0 = rowcol(transform, x0, y0)
        R = int(PIPE_MAX / ROUTE_K / res) + 1
        r_a, c_a = max(r0 - R, 0), max(c0 - R, 0)
        sl = (slice(r_a, r0 + R + 1), slice(c_a, c0 + R + 1))
        elig = free[sl] & (dtm[sl] <= zt)
        core = ndimage.binary_erosion(elig, iterations=2)
        lab, n = ndimage.label(elig)
        cand = None
        if n:
            sizes = np.bincount(lab.ravel())[1:] * res * res
            rr, cc = np.nonzero(core & np.isin(lab, np.flatnonzero(sizes >= a_need) + 1))
            if len(rr):
                gx, gy = rc2xy(transform, rr + r_a, cc + c_a)
                dist = np.hypot(np.asarray(gx) - x0, np.asarray(gy) - y0) * ROUTE_K
                i = int(np.argmin(dist))
                if dist[i] <= PIPE_MAX:
                    cand = (float(dist[i]), int(rr[i]), int(cc[i]))
        best = None
        for p in ponds:
            d = math.hypot(p["xy"][0] - x0, p["xy"][1] - y0) * ROUTE_K
            if d <= PIPE_MAX and p["z_top"] <= zt and (best is None or d < best[0]):
                best = (d, p)
        if best and best[1]["area_m2"] * POND_D - best[1]["need_m3"] < need:
            best = None                                    # 那个塘装不下这口井，不并
        if best and (cand is None or best[0] <= cand[0] + MERGE_R):
            p = best[1]
            p["wells"].append(k); p["need_m3"] += need
            verdict[k] = {"how": "gravity", "pond": p["id"], "L_m": round(best[0]), "merge": True}
            continue
        if cand is None:
            verdict[k] = {"how": "pump", "why": f"{PIPE_MAX:.0f} m 管长内没有比井口（{zr:.2f} m）低 {HEAD_M} m、"
                                                 f"20 m 宽以上、连片 ≥ {a_need:,.0f} m² 的可用地"}
            continue
        dist, r1, c1 = cand
        rr, cc = np.nonzero(lab == lab[r1, c1])
        order = np.argsort(np.hypot(rr - r1, cc - c1))[: max(1, int(math.ceil(a_need / (res * res))))]
        fp = np.zeros(dtm.shape, bool)
        fp[rr[order] + r_a, cc[order] + c_a] = True
        g = dtm[fp]
        px, py = rc2xy(transform, r1 + r_a, c1 + c_a)
        n_p = len(ponds) + 1
        ponds.append({"id": f"NP{n_p:02d}", "node": f"PND_V5_{n_p:02d}", "xy": (float(px), float(py)), "mask": fp,
                      "z_top": float(np.percentile(g, 90)), "z_ground_min": float(g.min()), "area_m2": float(fp.sum() * res * res),
                      "wells": [k], "need_m3": need,
                      "land": "明确空地" if (cls[fp] == 1).mean() >= 0.5 else "OSM 未标注"})
        verdict[k] = {"how": "gravity", "pond": ponds[-1]["id"], "L_m": round(dist), "merge": False}
        free &= ~fp
    return ponds, verdict


def design(tag, dry=False):
    """按 C1_{tag} 的 L2+planned / v4 结果出 v5 的措施清单（新塘 + 重力管 + 保留的泵）。"""
    import rasterio
    from rasterio.features import rasterize, shapes
    from rasterio.windows import from_bounds
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union
    name = f"C1_{tag}"
    v4 = json.loads((ROOT / "out" / f"c1_L2S4_inputs_{tag}.json").read_text(encoding="utf-8"))
    nf = node_flood(C2D / f"{name}_L2P_2way.rpt")
    S = read_inp(WORK / f"{name}_L2P.inp")
    rim_of = {r[0]: float(r[1]) + float(r[2]) for r in S["[JUNCTIONS]"]}
    nxy = {r[0]: (float(r[1]), float(r[2])) for r in S["[COORDINATES]"]}
    meta = json.loads((C2D / f"pilot_{name}_L2P.json").read_text(encoding="utf-8"))

    with rasterio.open(C2D / f"dem_{name}.tif") as d0:
        bb = d0.bounds
    dtm_path = pathlib.Path(r"D:/阿布扎比/DTM_z40_customer/AUH_DTM_5m_Z40.TIF")
    with rasterio.open(dtm_path) as ds:
        win = from_bounds(*bb, ds.transform).round_offsets().round_lengths()
        dtm = ds.read(1, window=win).astype(float); tf = ds.window_transform(win)
        dtm[(dtm < -100) | (dtm > 1000)] = np.nan
    cls, land_stat, _ = land_mask(dtm.shape, tf)
    pond_mask = rasterize(((p["footprint"], 1) for p in meta.get("ponds", [])), out_shape=dtm.shape, transform=tf,
                          fill=0, dtype="uint8").astype(bool)
    pond_mask = __import__("scipy.ndimage", fromlist=["x"]).binary_dilation(pond_mask, iterations=2)

    keep = [c for c in v4["convs"] if c["id"] not in DROP]
    sumps = [m for m in v4["measures"] if m["id"] not in DROP]
    wells = sorted((c["node"] for c in keep), key=lambda k: -nf[k]["vol_m3"])
    ponds, verdict = site_ponds(wells, nxy, rim_of, nf, dtm, tf, cls, pond_mask)

    # 管：每口重力井一根，管径按塘满时（塘顶）的水头、1.25 × 溢流峰值
    pipes = []
    for c in keep:
        v = verdict[c["node"]]; c["v5"] = v
        if v["how"] != "gravity":
            continue
        p = next(q for q in ponds if q["id"] == v["pond"])
        zin = rim_of[c["node"]] - HEAD_M
        L = max(v["L_m"], 10.0)
        d = pipe_d(1.25 * nf[c["node"]]["rate_m3s"], (zin - p["z_top"]) / L, L)
        pipes.append({"id": f"GP_{c['id']}", "from": c["node"], "to": p["node"], "L_m": round(L), "d_m": d,
                      "z_in": round(zin, 3), "conv": c["id"], "pond": p["id"]})
        c["v5"].update(d_m=d, z_in=round(zin, 3))
    for p in ponds:                                       # 塘：底 = 塘顶 − 深；容积 = 占地 × 深
        p["z_floor"] = round(p["z_top"] - POND_D, 3)
        p["vol"] = round(p["area_m2"] * POND_D)
        polys = [shape(g) for g, val in shapes(p["mask"].astype("uint8"), mask=p["mask"], transform=tf) if val == 1]
        p["footprint"] = mapping(unary_union(polys))
        p["cut_m3"] = round(float(np.nansum(np.maximum(dtm[p["mask"]] - p["z_floor"], 0))) * tf.a * tf.a)
    n_grav = sum(1 for c in keep if c["v5"]["how"] == "gravity")
    pumps_left = [c for c in keep if c["v5"]["how"] == "pump"]
    print(f"{name} v5：v4 截流井 {len(v4['convs'])} → 去掉不出力 {len(v4['convs']) - len(keep)} → {len(keep)} 口："
          f"重力进新塘 {n_grav}、保留泵 {len(pumps_left)}；地面泵 {len(v4['measures'])} → {len(sumps)}")
    print(f"  土地（5 m 格）：{land_stat}")
    for p in ponds:
        print(f"  {p['id']} {p['node']} 占地 {p['area_m2']:,.0f} m² 塘顶 {p['z_top']:.2f} 底 {p['z_floor']:.2f} 容积 {p['vol']:,} m³ "
              f"（要 {p['need_m3']:,.0f}）挖方 {p['cut_m3']:,} m³ · {p['land']} · 接 {', '.join(p['wells'])}")
    for c in keep:
        v = c["v5"]
        print(f"  {c['id']} {c['node']} 井口 {rim_of[c['node']]:.2f} 溢流 {nf[c['node']]['vol_m3']:,.0f} m³ → " +
              (f"重力 {v['pond']} {v['L_m']} m Ø{v['d_m']:.2f}" + ("（并塘）" if v["merge"] else "") if v["how"] == "gravity" else f"保留泵：{v['why']}"))
    return dict(v4=v4, keep=keep, sumps=sumps, ponds=ponds, pipes=pipes, land_stat=land_stat, rim_of=rim_of)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="M61")
    ap.add_argument("--dry", action="store_true", help="只出措施清单，不写模型")
    ap.add_argument("--from", dest="src", default=None, help="措施取自哪个情景的设计（缺省 = --tag 自己）；96 mm 验证用 --tag M96 --from M61")
    a = ap.parse_args(argv)
    src = a.src or a.tag
    D = design(src, a.dry)
    if a.dry:
        return 0
    write_model(a.tag, src, D)
    return 0


def write_model(tag, src, D):
    """在 C1_{tag}_L2P 上加 v5：保留的截流泵（规则同 v4）、重力管 + 拍门、新塘（一维蓄水 + 二维占地收水）、保留的地面泵。"""
    name = f"C1_{tag}"
    txt = (WORK / f"{name}_L2P.inp").read_text(encoding="utf-8", errors="ignore").replace(
        "· L2+planned", "· L2+suggested v5（L2+planned + v4 删泵 + 低处新塘重力接井）", 1)
    pumps, crv, rules = [], [], []
    for m in D["keep"]:
        if m["v5"]["how"] != "pump":
            continue
        pumps.append(f"{m['id']}_P  {m['node']}  {m['to_pond']}  PC_{m['id']}  OFF  0  0")
        crv.append(f"PC_{m['id']}  PUMP2  0  {m['q_m3s']:.4f}\nPC_{m['id']}  100  {m['q_m3s']:.4f}")
        rules.append(f"RULE R_{m['id']}_FULL\nIF NODE {m['to_pond']} DEPTH >= 0.90\nTHEN PUMP {m['id']}_P STATUS = OFF\nPRIORITY 3\n\n"
                     f"RULE R_{m['id']}_ON\nIF NODE {m['node']} DEPTH > 0.30\nAND NODE {m['to_pond']} DEPTH < 0.90\nTHEN PUMP {m['id']}_P STATUS = ON\nPRIORITY 2\n\n"
                     f"RULE R_{m['id']}_OFF\nIF NODE {m['node']} DEPTH < 0.05\nTHEN PUMP {m['id']}_P STATUS = OFF\nPRIORITY 2\n")
    sto = "".join(f"{p['node']}  {p['z_floor']:.3f}  {POND_D:.3f}  0  FUNCTIONAL  0  0  {p['area_m2']:.1f}  0  0\n" for p in D["ponds"])
    cond = "".join(f"{q['id']}  {q['from']}  {q['to']}  {q['L_m']:.1f}  {N_MAN}  {q['z_in']:.3f}  "
                   f"{next(p['z_floor'] for p in D['ponds'] if p['id'] == q['pond']):.3f}  0  0\n" for q in D["pipes"])
    xs = "".join(f"{q['id']}  CIRCULAR  {q['d_m']:.3f}  0  0  0  1\n" for q in D["pipes"])
    los = "".join(f"{q['id']}  0.5  1.0  0  YES  0\n" for q in D["pipes"])     # 拍门：塘满了不倒灌回井
    crd = "".join(f"{p['node']}  {p['xy'][0]:.2f}  {p['xy'][1]:.2f}\n" for p in D["ponds"])

    def add(sec, body, before="[CONDUITS]"):
        nonlocal txt
        if not body:
            return
        if f"{sec}\n" in txt:
            txt = txt.replace(f"{sec}\n", f"{sec}\n" + body, 1)
        else:
            txt = txt.replace(before, f"{sec}\n{body}\n{before}", 1)
    add("[STORAGE]", sto); add("[CONDUITS]", cond, "[PUMPS]"); add("[XSECTIONS]", xs, "[INFLOWS]")
    add("[LOSSES]", los, "[INFLOWS]"); add("[COORDINATES]", crd)
    add("[PUMPS]", "\n".join(pumps) + "\n" if pumps else "", "[XSECTIONS]")
    add("[CURVES]", "\n".join(crv) + "\n" if crv else "", "[TIMESERIES]")
    if rules:
        txt = (txt.replace("[CONTROLS]\n", "[CONTROLS]\n" + "\n".join(rules) + "\n", 1) if "[CONTROLS]" in txt
               else txt.rstrip("\n") + "\n\n[CONTROLS]\n" + "\n".join(rules) + "\n")
        txt = re.sub(r"^CONTROLS\s+NO", "CONTROLS  YES", txt, count=1, flags=re.M)
    (WORK / f"{name}_{OUT}.inp").write_text(txt, encoding="utf-8")

    meta = json.loads((C2D / f"pilot_{name}_L2P.json").read_text(encoding="utf-8"))
    m4 = json.loads((C2D / f"pilot_C1_{src}_L2S4.json").read_text(encoding="utf-8"))
    meta["state"] = OUT
    meta["dem_edits"] = []                                 # 不改地形；新塘占地内的地表水由耦合程序收进塘（同规划塘）
    meta["ponds"] = list(meta.get("ponds", [])) + [{"node": p["node"], "unitid": p["node"], "vol": p["vol"], "xy": list(p["xy"]),
                                                    "footprint": p["footprint"], "new_v5": True} for p in D["ponds"]]
    meta["pumps2d"] = [q for q in m4["pumps2d"] if q["id"] not in DROP]
    (C2D / f"pilot_{name}_{OUT}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {k: p[k] for k in ("id", "node", "area_m2", "z_top", "z_floor", "vol", "need_m3", "cut_m3", "land", "wells")},
         "geometry": __import__("shapely.geometry", fromlist=["x"]).mapping(__import__("shapely.ops", fromlist=["x"]).transform(
             lambda x, y, z=None: tr.transform(x, y), __import__("shapely.geometry", fromlist=["x"]).shape(p["footprint"])))}
        for p in D["ponds"]]}
    (ROOT / "console" / "data" / f"c1_l2s5_ponds_{tag}.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    info = {"version": "v5", "tag": tag, "from": src, "drop": sorted(DROP), "head_m": HEAD_M, "pond_d_m": POND_D, "size_k": SIZE_K,
            "pipe_max_m": PIPE_MAX, "route_k": ROUTE_K, "land_stat_cells5m": D["land_stat"],
            "convs": [{k: v for k, v in c.items()} for c in D["keep"]], "sumps": [m["id"] for m in D["sumps"]],
            "ponds": [{k: v for k, v in p.items() if k not in ("mask", "footprint")} for p in D["ponds"]], "pipes": D["pipes"],
            "n_pumps": sum(c["n_pumps"] for c in D["keep"] if c["v5"]["how"] == "pump") + sum(m["n_pumps"] for m in D["sumps"]),
            "q_total_m3s": round(sum(c["q_m3s"] for c in D["keep"] if c["v5"]["how"] == "pump") + sum(m["q_m3s"] for m in D["sumps"]), 2)}
    (ROOT / "out" / f"c1_{OUT}_inputs_{tag}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"\n已写 {WORK / f'{name}_{OUT}.inp'}、c2d/pilot_{name}_{OUT}.json、out/c1_{OUT}_inputs_{tag}.json："
          f"泵 {info['n_pumps']} 台 / {info['q_total_m3s']} m³/s，新塘 {len(D['ponds'])} 座，重力管 {len(D['pipes'])} 根")


if __name__ == "__main__":
    raise SystemExit(main())
