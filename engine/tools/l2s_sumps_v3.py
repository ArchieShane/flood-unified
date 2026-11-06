# -*- coding: utf-8 -*-
"""L2+suggested 地面泵 v3（2026-10-24）：一维方案二维验证后，热点附近还有退不掉的封闭洼坑（管网收不进去），配地面泵。
按 DOM-B11「地面泵按小泵站处理」：和 l2s_candidates_v3 的 ps_cluster 走同一套可建性检查（A 手册 / B 领域知识 / C 空间）。

步骤：
  1 积水片   方案态在各验证雨下的积水点（swbrain.ponding，水深阈值 0.10 m = 判据 C），取离热点 ≤ 200 m、雨后还有残留（≥ --min-vol）的片，跨雨合并
  2 分类     DTM 填 0（最深点 DTM 恰为 0.00 m，或片内这类格 ≥ 30 %）→ 不配泵，评估剔除；
             私人地块内深坑（最深点在已建地块内且坑底 < 0 m）→ 不配泵，评估剔除；
             隧道引道（离 udm_roadtunnel ≤ 50 m）→ 用户 2026-10-24 选 (b)：作为新建泵站计入方案，标注「待客户确认是否有现状泵站」
  3 取水口   每片最深点设雨水井 + 取水口；设计流量 q = max(残留 / 1 h, 峰值 / 3 h)，下限 0.05 m³/s（Claude 自定，同 l2opt_add_sumps）
  4 合建     同一热点组、相距 ≤ 300 m 的取水口合用一座泵站（DOM-B10），取水口 → 重力管 → 湿井
  5 泵站     场地、供电、临路、不积水、湿井深、配泵、压力管流速、排口：同 ps_cluster；出水只去排海口（DOM-B7）
  不过检查的：不配泵，列在 unresolved 里说明原因
产物：$WORK/<前缀>_<雨>_<新态>.inp（= 方案态 inp 原样）、c2d/pilot_<前缀>_<雨>_<新态>.json（方案态 + pumps2d）、
      out/l2opt/<运行>/sumps_<新态>.json（泵站、合规表、未解决、剔除）、exclude_<新态>.geojson（评估剔除区）
用法：python tools/l2s_sumps_v3.py --run RDA_96C --state OPTC --new OPTCS --storms S96D1,S96D24 --hs ADM-75,... --groups "A+B,C,..."
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import shutil
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
import l2s_candidates_v3 as V3  # noqa: E402
from swbrain.ontology import constraints as K  # noqa: E402
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
PH_INTAKE = 50_000          # 取水井 + 格栅雨水口（占位，同 l2opt_add_sumps 的 sump）
Q_MIN = 0.05


def bl(cid, zh, ok, detail, src):
    return {"check": cid, "zh": zh, "result": ok if isinstance(ok, str) else ("pass" if ok else "fail"), "detail": detail, "refs": [{"id": src}]}


class Ctx:
    """泵站设计要用的空间上下文：Site + 排口岸点（同 l2s_candidates_v3）+ 临路掩膜"""

    def __init__(self, site):
        import geopandas as gpd
        from shapely.geometry import box as _box
        dv = V3.dv
        self.site = site
        sh = gpd.GeoSeries([g.boundary if g.geom_type in ("Polygon", "MultiPolygon") else g for g in site.L["shoreline"].geometry], crs=32640).union_all().intersection(_box(*site.bbox))
        excl = site.L["coast_excl"]; ex_pts = excl[excl.src != "waterfront_protection_overlay_line"].geometry.union_all()
        wfl = excl[excl.src == "waterfront_protection_overlay_line"].geometry.union_all() if (excl.src == "waterfront_protection_overlay_line").any() else None
        B9 = dv("DOM-B9"); self.osites = []
        for ln in getattr(sh, "geoms", [sh]):
            for dd in np.arange(0, ln.length, B9["site_step_m"]):
                q = ln.interpolate(dd)
                if ex_pts is not None and q.distance(ex_pts) < B9["exclude_near_beach_or_facility_m"]:
                    continue
                self.osites.append({"id": f"SEA{len(self.osites):03d}", "x": q.x, "y": q.y, "new": True, "wfl": wfl is not None and q.distance(wfl) < B9["waterfront_line_review_m"]})
        self.otree = np.array([[o["x"], o["y"]] for o in self.osites])
        self.near_road = site.dist_road <= dv("DOM-PS-SITE")["road_access_max_m"] + 10.0


def design_station(ctx, ps_):
    """一组取水口（各带 x, y, z, q, class, ids, why, geom, hotspot，可选 vol_peak_m3 / vol_end_m3 / hmax_m）→ 一座小泵站。
    返回 (True, 泵站, 合规表) 或 (False, 原因, 合规表)。检查同 l2s_candidates_v3 的 ps_cluster（DOM-B11）"""
    dv = V3.dv; site = ctx.site; B1 = dv("DOM-B1"); PH = V3.PH
    q = sum(p["q"] for p in ps_); n, qe = V3.pumps_for(q)
    cx0 = float(np.mean([p["x"] for p in ps_])); cy0 = float(np.mean([p["y"] for p in ps_]))
    need_area = dv("DOM-PS-SITE")["min_site_m2"] + max(0.0, q - 1.0) * dv("DOM-PS-SITE")["extra_m2_per_m3s_above_1"]
    got = None
    for rad in (150.0, 300.0):
        for d, cells, px, py in site.find_site(cx0, cy0, rad, need_area, V3.bclass(8.0)[0], extra_ok=ctx.near_road, max_sites=8):
            if site.flooded(px, py) or site.power_m(px, py) > dv("DOM-B5")["max_distance_to_substation_m"]:
                continue
            got = (cells, px, py); break
        if got:
            break
    if not got:
        return False, "取水口 300 m 内没有合格泵站场地（可开挖、离建筑 ≥ B、临路、供电 ≤ 1 km、不积水）", []
    cells, px, py = got
    Ls = [site.road_dist((p["x"], p["y"]), (px, py)) or math.hypot(p["x"] - px, p["y"] - py) * 1.3 for p in ps_]
    inlet = min(p["z"] - B1["tank_cover_m"] - B1["pipe_fall_per_m"] * Li - B1["loss_m"] for p, Li in zip(ps_, Ls))
    zps = site.ground(px, py); wwbot = inlet - 1.5; wwd = zps - wwbot
    best = None
    for j in np.argsort(np.hypot(ctx.otree[:, 0] - px, ctx.otree[:, 1] - py))[:4]:
        o = ctx.osites[j]; L = site.road_dist((px, py), (o["x"], o["y"]))
        if L is not None and (best is None or L < best[1]):
            best = (o, L)
    if best is None:
        return False, "泵站到排口没有沿路路径（DOM-B8）", []
    o, L = best; dn, m, v = V3.size_fm(n * qe); rate, rid = V3.fm_rate_usd(dn)
    wwv = 0.25 * qe * 3600.0 / K.dec("DEC-09")
    items = K.check("PumpStation", {"gravity_infeasible": True, "pump_count": n, "pump_capacity_m3s": qe, "design_flow_m3s": q,
                                    "wetwell_volume_m3": wwv, "surface_site_m2": len(cells) * 4})["items"]
    items += K.check("PressurePipe", {"velocity_peak_ms": v, "material": "HDPE", "clearance_to_utilities_m": None, "clearance_to_building_m": None})["items"]
    items += K.check("Outfall", {"is_new": True, "soffit_m_nadd": -0.8, "nrv_chamber_cover_m_nadd": 1.5, "flap_valve": True})["items"]
    items += [bl("B11_surface_pump", "地面泵按小泵站处理", "pass", f"{len(ps_)} 个取水口合建", "DOM-B11"),
              bl("B10_wetwell_depth", "湿井深 ≤ 10 m", wwd <= dv("DOM-B10")["max_wetwell_depth_m"], f"{wwd:.1f} m", "DOM-B10"),
              bl("B5_power", "泵站供电 ≤ 1 km", site.power_m(px, py) <= 1000, f"{site.power_m(px, py):.0f} m", "DOM-B5"),
              bl("B6_flood", "泵站场地不积水", not site.flooded(px, py), "", "DOM-B6"),
              bl("PS_access", "泵站场地临路（≤ 20 m）", min(site.dist_road[r, c] for r, c in cells) <= 20, f"{min(site.dist_road[r, c] for r, c in cells):.0f} m", "DOM-PS-SITE"),
              bl("B9_waterfront", "排口离滨水保护线 ≥ 50 m", "review" if o["wfl"] else "pass", o["id"], "DOM-B9"),
              bl("C_land", "用地许可", "review", f"泵站 {site.land_mix(cells)}", "WA726-1-127")]
    if any(p["class"] == "隧道引道" for p in ps_):
        items.append(bl("C_underpass", "下穿通道是否已有现状泵站", "review", "客户问题清单第 6 条；有则改为 L1、从方案造价扣除", "docs/RD-A_建模说明.md 第八节"))
    status = "不可建" if any(i["result"] == "fail" for i in items) else ("有条件可建" if any(i["result"] in ("unknown", "review") for i in items) else "可建")
    if status == "不可建":
        return False, "；".join(i["zh"] + "：" + str(i["detail"]) for i in items if i["result"] == "fail"), items
    ocost, oid = V3.outfall_cost_usd(dn)
    glink = sum(PH["gpipe_m"].get(V3.V5.pipe_d(p["q"], 0.002, Li), 2800) * Li for p, Li in zip(ps_, Ls))
    cost = (PH["station_base"] + n * PH["pump_per_036"] * (qe / 0.361) ** 0.7 + wwv * 3 * PH["rc_wetwell_m3"] + rate * L * m
            + PH["air_valve"] * max(1, int(L / 1000)) + glink + ocost + PH_INTAKE * len(ps_))
    return True, {"status": status, "hotspot": ps_[0]["hotspot"], "classes": sorted({p["class"] for p in ps_}), "q_m3s": round(q, 3),
                  "n_pumps": n, "pump_each_m3s": round(qe, 3), "wetwell_m3": round(wwv, 1), "wetwell_depth_m": round(wwd, 1), "ps_site_m2": len(cells) * 4,
                  "ps_x": round(px, 1), "ps_y": round(py, 1), "outfall_site": o["id"], "outfall_new": True, "of_xy": [round(o["x"], 1), round(o["y"], 1)], "pipe_m": round(L),
                  "main_d_m": dn, "n_mains": m, "v_ms": v, "cost_usd": round(cost),
                  "cost_basis": f"压力管 {rid}、排口 {oid}（第三卷 p349）；泵站、湿井、取水井、重力连接管：占位",
                  "intakes": [{"patches": "/".join(p["ids"]), "class": p["class"], "why": p["why"], "x": round(p["x"], 1), "y": round(p["y"], 1), "z": p["z"],
                               "q_m3s": p["q"], "tune": p.get("tune_note", ""), "vol_peak_m3": p.get("vol_peak_m3"), "vol_end_m3": p.get("vol_end_m3"),
                               "hmax_m": p.get("hmax_m"), "link_m": round(Li),
                               "poly": [list(c) for c in max(getattr(p["geom"], "geoms", [p["geom"]]), key=lambda q_: q_.area).exterior.coords]}
                              for p, Li in zip(ps_, Ls)]}, items


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="RDA"); ap.add_argument("--run", required=True)
    ap.add_argument("--state", required=True); ap.add_argument("--new", required=True)
    ap.add_argument("--storms", default="S96D1,S96D24"); ap.add_argument("--hs", required=True); ap.add_argument("--groups", required=True)
    ap.add_argument("--radius", type=float, default=200.0); ap.add_argument("--min-vol", type=float, default=30.0)
    ap.add_argument("--min-area", type=float, default=50.0)
    ap.add_argument("--tune-from", default="", help="上一轮带地面泵的态（如 OPTCS）：按它的二维结果给峰值时仍 ≥ 0.30 m 的取水口加流量（判据 A）")
    ap.add_argument("--sitedata", default=str(ROOT / "out/rda/sitedata.gpkg")); ap.add_argument("--domain", default=str(ROOT / "out/dataqc/RD-A/input/rda_domain.geojson"))
    a = ap.parse_args(argv)
    import geopandas as gpd
    import rasterio
    from pyproj import Transformer
    from shapely.geometry import LineString, Point, mapping, shape
    from shapely.ops import transform as stf, unary_union
    from swbrain import ponding
    T = Transformer.from_crs(4326, 32640, always_xy=True); TI = Transformer.from_crs(32640, 4326, always_xy=True)
    W = lambda g: mapping(stf(lambda x, y, z=None: TI.transform(x, y), g))   # noqa: E731
    dv = V3.dv
    H = __import__("hotspot_src").load(32640).set_index("hotspot_id")
    hs = a.hs.split(","); groups = [g.split("+") for g in a.groups.split(",")]
    grp = {h: gi for gi, g in enumerate(groups) for h in g}
    # ---- 1 积水片（跨雨合并）
    patches = []
    for storm in a.storms.split(","):
        r = ponding.compute(f"{a.prefix}_{storm}", a.state, depth=0.10, min_area=a.min_area, min_dur=0.25, geometry=True)
        for f in r["feats"]:
            g = stf(lambda xx, yy, zz=None: T.transform(xx, yy), shape(f["geom"]))
            d = {h: H.loc[h].geometry.distance(g) for h in hs}
            h = min(d, key=d.get)
            if d[h] > a.radius or f["vol_end_m3"] < a.min_vol:
                continue
            x, y = T.transform(f["lon"], f["lat"])
            hit = next((p for p in patches if p["geom"].intersects(g)), None)
            if hit:
                hit["geom"] = hit["geom"].union(g); hit["storms"].append(storm); hit["ids"].append(f"{storm}:{f['id']}")
                for k in ("vol_peak_m3", "vol_end_m3", "hmax_m"):
                    hit[k] = max(hit[k], f[k])
                if f["hmax_m"] >= hit["hmax_m"]:
                    hit["x"], hit["y"], hit["z"] = x, y, f["z_m"]
                continue
            patches.append({"ids": [f"{storm}:{f['id']}"], "storms": [storm], "hotspot": h, "hs_dist_m": round(d[h]), "geom": g, "x": x, "y": y, "z": f["z_m"],
                            "vol_peak_m3": f["vol_peak_m3"], "vol_end_m3": f["vol_end_m3"], "hmax_m": f["hmax_m"]})
    print(f"热点 {a.radius:g} m 内雨后有残留的积水片 {len(patches)} 处（{a.storms}）")
    # ---- 2 分类
    dom = gpd.read_file(a.domain).to_crs(32640)
    site = V3.Site(a.sitedata, dom, a.prefix, a.storms.split(",")[0], "L2P")
    tun = site.L["no_dig"]; tun = tun[tun.src == "udm_roadtunnel"]
    P = site.L["plots"]; built = P[P.construction_status.astype(str).str.lower().str.startswith("constructed")]
    dtm = rasterio.open(V3.DTM)
    excluded, keep = [], []
    for p in patches:
        g = p["geom"]; b = g.bounds
        win = rasterio.windows.from_bounds(*b, dtm.transform); arr = dtm.read(1, window=win, boundless=True)
        from rasterio.features import geometry_mask
        m = ~geometry_mask([g], out_shape=arr.shape, transform=dtm.window_transform(win), all_touched=True)
        zero = float((arr[m] == 0.0).mean()) if m.any() else 0.0
        pt = Point(p["x"], p["y"])
        if zero >= 0.30 or abs(p["z"]) < 1e-6:
            p["class"] = "DTM 填 0"; p["why"] = f"最深点高程 {p['z']:.2f} m，片内 {zero:.0%} 的 DTM 格恰为 0.00 m，判断为缺测填 0"; excluded.append(p); continue
        if p["z"] < 0 and built.intersects(pt).any():
            p["class"] = "私人地块内深坑"; p["why"] = f"最深点 {p['z']:.2f} m 在已建地块内（地下室坡道 / 基坑）"; excluded.append(p); continue
        dt = float(tun.distance(pt).min()) if len(tun) else 1e9
        p["class"] = "隧道引道" if dt <= 50 else "地面洼坑"
        p["why"] = f"离道路隧道 {dt:.0f} m；用户 2026-10-24 选 (b)，作为新建泵站计入方案，待客户确认是否有现状泵站" if dt <= 50 else ""
        p["q"] = round(max(p["vol_end_m3"] / 3600.0, p["vol_peak_m3"] / (3 * 3600.0), Q_MIN), 3)
        p["geom"] = p["geom"].buffer(5.0)                                   # 取水范围外扩 5 m：片边格子也抽得到（10-24 首轮 ADM-161 / ADM-76 边格残留 0.11–0.16 m）
        keep.append(p)
    if a.tune_from:                                                         # 判据 A：峰值时超 0.25 m 的水量 ÷ 涨水历时 = 还差的抽速
        import shapely
        for storm in a.storms.split(","):
            z = np.load(C2D / f"{a.prefix}_{storm}_{a.tune_from}_2d.npz"); xy = z["xy"].astype(float); ar = z["area"].astype(float)
            fr = z["frames"].astype(np.float32); dt = float(np.diff(z["t_h"]).mean())
            for p in keep:
                c = shapely.contains_xy(p["geom"].buffer(10.0), xy[:, 0], xy[:, 1])
                if not c.any():
                    continue
                hc = fr[:, c]; ex = (np.clip(hc - 0.25, 0, None) * ar[c]).sum(1)
                wet = np.flatnonzero(hc.max(1) >= 0.30)
                dur = max(float((int(ex.argmax()) - wet[0]) * dt), 0.25) if len(wet) else 0.0      # 涨水历时：从超 0.30 m 到峰值（下限 0.25 h）
                if dur > 0:
                    add = float(ex.max() / (dur * 3600.0))
                    if add > p.get("q_add", 0.0):
                        p["q_add"] = add; p["tune_note"] = f"{a.tune_from} {storm} 峰值 {float(hc.max()):.2f} m，超 0.30 m 到峰值 {dur:.2f} h，加 {add:.3f} m³/s"
        for p in keep:
            if p.get("q_add"):
                p["q"] = round(p["q"] + p["q_add"], 3)
                print(f"  加流量 {'/'.join(p['ids'])} {p['hotspot']}：{p['tune_note']} → {p['q']} m³/s")
    for p in excluded:
        print(f"  剔除 {'/'.join(p['ids'])} {p['hotspot']}：{p['class']}，{p['why']}")
    # ---- 4 合建（同组、≤ 300 m，单链聚类）
    R = dv("DOM-B10")["cluster_radius_m"]
    par = list(range(len(keep)))

    def root(i):
        while par[i] != i:
            par[i] = par[par[i]]; i = par[i]
        return i
    for i in range(len(keep)):
        for j in range(i + 1, len(keep)):
            if grp[keep[i]["hotspot"]] == grp[keep[j]["hotspot"]] and math.hypot(keep[i]["x"] - keep[j]["x"], keep[i]["y"] - keep[j]["y"]) <= R:
                par[root(i)] = root(j)
    cl = collections.defaultdict(list)
    for i, p in enumerate(keep):
        cl[root(i)].append(p)
    ctx = Ctx(site)
    stations, unresolved, comp = [], [], {}
    for ps_ in cl.values():
        ok, res, items = design_station(ctx, ps_)
        if not ok:
            unresolved.append({"patches": "/".join(i for p in ps_ for i in p["ids"]), "hotspot": ps_[0]["hotspot"], "q_m3s": round(sum(p["q"] for p in ps_), 3), "why": res})
            continue
        sid = f"SP{len(stations) + 1:02d}"
        comp[sid] = items
        stations.append({"id": sid, **res})
    for s in stations:
        print(f"  {s['id']} {s['hotspot']} {s['classes']}：{len(s['intakes'])} 个取水口，{s['q_m3s']} m³/s，{s['n_pumps']} 台 × {s['pump_each_m3s']}，"
              f"湿井 {s['wetwell_depth_m']} m，压力管 {s['pipe_m']} m DN{int(s['main_d_m'] * 1000)}×{s['n_mains']} → {s['outfall_site']}，{s['cost_usd'] / 1e6:.2f} M USD，{s['status']}")
    for u in unresolved:
        print(f"  ! 未解决 {u['patches']} {u['hotspot']}：{u['why']}")
    # ---- 产物
    o = ROOT / "out" / "l2opt" / a.run
    base_rec = json.loads((o / f"variant_{a.storms.split(',')[0]}_{a.state}.json").read_text(encoding="utf-8"))
    for storm in a.storms.split(","):
        name = f"{a.prefix}_{storm}"
        shutil.copyfile(WORK / f"{name}_{a.state}.inp", WORK / f"{name}_{a.new}.inp")
        meta = json.loads((C2D / f"pilot_{name}_{a.state}.json").read_text(encoding="utf-8"))
        meta["state"] = a.new
        p2 = list(meta.get("pumps2d") or [])
        for s in stations:
            for k, it in enumerate(s["intakes"]):
                p2.append({"id": f"{s['id']}_{k}", "q_m3s": it["q_m3s"], "off_m": 0.03, "sump_polygon": it["poly"], "xy": [it["x"], it["y"]], "to_xy": None, "pond_cap_frac": 0.9})
        meta["pumps2d"] = p2
        (C2D / f"pilot_{name}_{a.new}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    ex = [{"type": "Feature", "properties": {"patches": "/".join(p["ids"]), "class": p["class"], "why": p["why"], "hotspot": p["hotspot"]}, "geometry": mapping(p["geom"])} for p in excluded]
    from rasterio.features import shapes as _shapes
    for p in [q for q in excluded if q["class"] == "DTM 填 0"]:          # 整片 DTM = 0 区（其他态可能淹到片外）
        x0, y0, x1, y1 = p["geom"].buffer(300).bounds
        win = rasterio.windows.from_bounds(x0, y0, x1, y1, dtm.transform); arr = dtm.read(1, window=win, boundless=True)
        zs = [shape(g).buffer(2.5) for g, v in _shapes((arr == 0.0).astype("uint8"), mask=arr == 0.0, transform=dtm.window_transform(win)) if v == 1]
        if zs:
            ex.append({"type": "Feature", "properties": {"patches": "/".join(p["ids"]), "class": "DTM 填 0 区", "why": "DTM 恰为 0.00 m 的连片区", "hotspot": p["hotspot"]},
                       "geometry": mapping(unary_union(zs))})
    (o / f"exclude_{a.new}.geojson").write_text(json.dumps({"type": "FeatureCollection", "crs_epsg": 32640, "features": ex}, ensure_ascii=False), encoding="utf-8")
    feats = []
    for s in stations:
        feats.append({"type": "Feature", "properties": {"id": s["id"], "kind": "surface_ps"}, "geometry": W(Point(s["ps_x"], s["ps_y"]))})
        feats.append({"type": "Feature", "properties": {"id": s["id"] + "_main", "kind": "link"}, "geometry": W(LineString([(s["ps_x"], s["ps_y"]), tuple(s["of_xy"])]))})
    (o / f"sumps_{a.new}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False), encoding="utf-8")
    tot = base_rec["cost_usd"] + sum(s["cost_usd"] for s in stations)
    (o / f"sumps_{a.new}.json").write_text(json.dumps({"base_state": a.state, "base_cost_usd": base_rec["cost_usd"], "stations": stations, "unresolved": unresolved,
                                                       "excluded": [{k: v for k, v in p.items() if k != "geom"} for p in excluded], "compliance": comp,
                                                       "cost_usd": tot, "rule": __doc__.split("产物")[0].strip()}, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"→ {a.new}：{a.state} + 地面泵站 {len(stations)} 座（取水口 {sum(len(s['intakes']) for s in stations)} 个），未解决 {len(unresolved)} 处，剔除 {len(excluded)} 处；"
          f"合计占位造价 {tot / 1e6:.2f} M USD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
