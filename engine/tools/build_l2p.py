# -*- coding: utf-8 -*-
"""防线2+ 两种构筑物形式各建一套，供领导做权衡。

  box     地下离线调蓄箱涵（就地）。建成区内合规（手册 §4.2 B）。库容能做满，造价高。
  lagoon  地面开敞塘。手册 §4.2「Open lagoons shall not be permitted within or close to
          developed areas」，所以只落在台账 Public Open Space / Recreational / Agricultural
          的地块上。实测这类地块离积水点中位 2.4–2.9 km，所以它自带一条输水干管。

用法：python tools/build_l2p.py [box|lagoon|both] [汇水区…]
"""
import json, os, pathlib, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from workdir import OUT, WORK
from dig_pond import read_sections, parse_nodes
from dig_pond3 import cluster_sites
import dig_pond5 as D5
from swbrain import db, landuse as LU

PLAN = {"Mussafah": (6, 250.0), "Shaliela": (10, 400.0), "RB3": (10, 400.0)}
SUFFIX = {"box": "_L2box", "lagoon": "_L2lag"}
# 换降雨场景时由 tools/rain_scenario.py 传进来：基准来自哪个场景、排空从第几小时起判、输出带什么后缀。
# 排空起判时刻必须跟着雨峰走——24 小时雨峰在第 12 h 所以定 14 h，2 小时雨峰在第 1.1 h，
# 沿用 14 h 会让塘空等十几个小时。
BASE_TAG = os.environ.get("L2P_BASE_TAG", "")
OUT_TAG = os.environ.get("L2P_OUT_TAG", "")
EMPTY_H = int(os.environ.get("L2P_EMPTY_H", "14"))
_b = f"_{BASE_TAG}" if BASE_TAG else ""
_o = f"_{OUT_TAG}" if OUT_TAG else ""
LAGOON_RADIUS_M = 8000.0     # 合规地块离积水点 0.4–7 km，搜索半径必须放到公里级


def lagoon_sites(cfg, name, raw, log=print):
    """只吸附到台账的开放/游憩/农用地块，**不兜底**。吸不到就如实报挖不了。"""
    from pyproj import Transformer
    to_wgs = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    c2 = {**cfg, "landuse": {**(cfg.get("landuse") or {}),
                             "search_radius_m": LAGOON_RADIUS_M}}
    xy, ar, ft = LU.load_plots(cfg, name)
    ok, used = [], set()
    for s in raw:
        if not len(ft):
            s["skip"] = "台账里这个汇水区没有任何开放/游憩/农用地块"; ok.append(s); continue
        hit = LU.snap_to_plot(c2, name, s["x"], s["y"], s["vol_m3"] / D5.DEPTH, used)
        if hit is None:
            s["skip"] = f"{LAGOON_RADIUS_M / 1000:.0f} km 内没有装得下的合规地块"
            ok.append(s); continue
        used.add(hit["plot_id"])
        cap = min(s["vol_m3"], hit["area_m2"] * D5.DEPTH)
        s.update({"x": hit["x"], "y": hit["y"], "vol_m3": cap,
                  "land": "plot", "plot_id": hit["plot_id"], "land_use": hit["use"],
                  "plot_area_m2": hit["area_m2"], "moved_m": hit["dist_m"],
                  "footprint": hit["geometry"],
                  "capped_by_land": cap < s["vol_m3"] - 1})
        ok.append(s)
    return ok


BOX_MAX_MOVE_M = 400.0       # 箱涵离聚类重心最多挪多远；再远就不是「就地」了，进水管也拉太长
BOX_GAP_M = 10.0             # 两座箱涵之间留的净距
# 放宽口径（只作对比）：前两档都放不下时，借用还没盖楼的已分配地块，要业主 / 规划部门同意
BOX_VACANT = os.environ.get("L2P_VACANT", "") == "1"


LAND_TXT = {"open_land": "空地", "road_reserve": "道路红线（需道路主管部门批准）",
            "vacant_plot": "未盖楼的已分配地块（需业主 / 规划部门同意）"}


def box_sites_strict(cfg, name, raw, depth_m=2.0, log=print):
    """箱涵落位：先找避开红线的空地，找不到才用道路红线，再找不到如实剔掉。

    旧做法（dig_pond4.site_on_land）在没有地块台账的区退回到「建筑 + 路网中心线 30 m」的兜底，
    会把箱涵放进已分配的地块和快速路红线里。这里用 landuse.strict_land_masks 的真实面。
    """
    from pyproj import Transformer
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from shapely.geometry import Polygon
    to_wgs = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    pad = BOX_MAX_MOVE_M + 200.0
    xs = [s["x"] for s in raw]; ys = [s["y"] for s in raw]
    bb = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    if BOX_VACANT:
        free1, free2, free3, tf, stats = LU.strict_land_masks(cfg, bb, log=log, vacant=True)
    else:
        (free1, free2, tf, stats), free3 = LU.strict_land_masks(cfg, bb, log=log), None
    x0, y1, res = tf
    ok, dropped = [], []
    for s in raw:
        need = s["vol_m3"] / depth_m
        sq, tier = LU.fit_square(free1, tf, s["x"], s["y"], need, max_r_m=BOX_MAX_MOVE_M), "open_land"
        if not sq:
            sq, tier = LU.fit_square(free2, tf, s["x"], s["y"], need, max_r_m=BOX_MAX_MOVE_M), "road_reserve"
        if not sq and free3 is not None:
            sq, tier = LU.fit_square(free3, tf, s["x"], s["y"], need, max_r_m=BOX_MAX_MOVE_M), "vacant_plot"
        if not sq:
            s["skip"] = f"{BOX_MAX_MOVE_M:.0f} m 内没有避开路面、建筑和地块的空地"
            dropped.append(s); continue
        h = sq["side_m"] / 2
        ring = [(sq["x"] - h, sq["y"] - h), (sq["x"] + h, sq["y"] - h),
                (sq["x"] + h, sq["y"] + h), (sq["x"] - h, sq["y"] + h), (sq["x"] - h, sq["y"] - h)]
        lo, la = to_wgs.transform([p[0] for p in ring], [p[1] for p in ring])
        s.update({"x": sq["x"], "y": sq["y"], "land": tier, "moved_m": sq["dist_m"],
                  "capped_by_land": False, "land_use": tier,
                  "footprint": {"type": "Polygon", "coordinates": [list(zip(lo, la))]}})
        ok.append(s)
        # 占掉的地（连同净距）从两张图里扣掉，后面的箱涵不能叠上来
        used = rasterize([Polygon(ring).buffer(BOX_GAP_M)], out_shape=free1.shape,
                         transform=from_origin(x0, y1, res, res),
                         fill=0, default_value=1, all_touched=True).astype(bool)
        free1 &= ~used; free2 &= ~used
        if free3 is not None:
            free3 &= ~used
    for i, s in enumerate(ok, 1):
        log(f"  第 {i} 座（{s['vol_m3']:,.0f} m³）: 挪 {s['moved_m']:.0f} m → "
            f"{LAND_TXT[s['land']]}")
    return ok, dropped, stats


def build(cfg, name, form, log=print):
    n, rad = PLAN[name]
    raw = cluster_sites(WORK / f"{name}_baseline{_b}.inp", WORK / f"{name}_baseline{_b}.rpt",
                        n, radius_m=rad, mult=1.5, log=lambda *a: None)
    want = sum(s["vol_m3"] for s in raw)
    if form == "lagoon":
        sites = lagoon_sites(cfg, name, raw, log)
    else:
        sites, dropped, _ = box_sites_strict(cfg, name, raw, log=log)
        sites += dropped
    for i, s in enumerate(sites, 1):
        s["id"] = f"NP{i}"
    D5.site_ground(cfg, [s for s in sites if not s.get("skip")], log=log)
    lines, idx = read_sections(WORK / f"{name}_baseline{_b}.inp")
    inv, _ = parse_nodes(lines, idx)
    D5.set_levels(sites, inv, form, log=log)
    D5.size_links(sites, form, log=log)
    live = [s for s in sites if not s.get("skip")]
    got = sum(s["vol_m3"] for s in live)
    log(f"\n{name} · {form}：理想 {want:,.0f} m³ → 能建 {len(live)}/{len(sites)} 个、"
        f"{got:,.0f} m³（{got / want * 100:.0f}%）")
    if live:
        log(f"  {'塘':<5}{'库容 m³':>10}{'塘底':>8}{'塘顶':>8}{'地面':>8}{'进水':>6}"
            f"{'提升 m':>8}{'泵台':>6}{'输水 m':>8}{'管 DN':>6}{'闸 DN':>7}{'灌满 min':>8}")
        for s in live:
            log(f"  {s['id']:<5}{s['vol_m3']:>10,.0f}{s['bottom_m']:>8.2f}{s['top_m']:>8.2f}"
                f"{(s.get('ground_m') or float('nan')):>8.2f}"
                f"{('重力' if s['inflow'] == 'gravity' else '提升'):>6}"
                f"{s['lift_m']:>8.1f}{s['n_pumps_in']:>6}{s['conveyance_m']:>8,.0f}"
                f"{s['dn_mm']:>6}{s['orifice_mm']:>7}{s['fill_min']:>8.0f}")
    for s in sites:
        if s.get("skip"):
            log(f"  ✗ {s['id']}: {s['skip']}（{s['vol_m3']:,.0f} m³ 没着落）")
    dst = WORK / f"{name}{SUFFIX[form]}{_o}.inp"
    D5.dig5(WORK / f"{name}_baseline{_b}.inp", dst, live, form,
            empty_start_h=EMPTY_H, log=log)
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": s.get("footprint"),
         "properties": {"id": s["id"], "form": form, "vol_m3": round(s["vol_m3"]),
                        "bottom_m": round(s["bottom_m"], 2), "top_m": round(s["top_m"], 2),
                        "ground_m": round(s.get("ground_m") or 0, 2),
                        "inflow": s["inflow"], "lift_m": round(s["lift_m"], 1),
                        "n_pumps_in": s["n_pumps_in"], "n_pumps_out": s["n_pumps_out"],
                        "conveyance_m": round(s["conveyance_m"]), "dn_mm": s["dn_mm"],
                        "orifice_mm": s["orifice_mm"], "fill_min": round(s["fill_min"]),
                        "land_use": s.get("land_use"), "plot_id": s.get("plot_id"),
                        "land": s.get("land"), "moved_m": round(s.get("moved_m") or 0)}}
        for s in live if s.get("footprint")]}
    key = ("L2B" if form == "box" else "L2L") + (f".{OUT_TAG}" if OUT_TAG else "")
    (OUT / f"{name}.{key}.ponds.geojson").write_text(json.dumps(gj, ensure_ascii=False),
                                                     encoding="utf-8")
    return {"want": want, "got": got, "n": len(live), "n_total": len(sites),
            "pumps_in": sum(s["n_pumps_in"] for s in live),
            "pumps_out": sum(s["n_pumps_out"] for s in live),
            "conveyance_m": sum(s["conveyance_m"] for s in live),
            "excav_m3": sum(s["area_m2"] * (s.get("ground_m", s["top_m"]) - s["bottom_m"])
                            for s in live)}


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    names = sys.argv[2:] or list(PLAN)
    cfg = db.load_config(None)
    forms = ["box", "lagoon"] if which == "both" else [which]
    summary = {}
    for form in forms:
        for name in names:
            print(f"\n{'=' * 92}\n{name} · {form}")
            summary[(name, form)] = build(cfg, name, form)
    print(f"\n{'=' * 92}\n两种形式对比\n")
    print(f"{'区':<10}{'形式':<8}{'能建':>7}{'库容 m³':>11}{'保住':>7}"
          f"{'开挖方 m³':>12}{'进水泵':>7}{'排空泵':>7}{'输水管 m':>10}")
    for (name, form), r in summary.items():
        print(f"{name:<10}{form:<8}{r['n']:>3}/{r['n_total']:<3}{r['got']:>11,.0f}"
              f"{r['got'] / r['want'] * 100:>6.0f}%{r['excav_m3']:>12,.0f}"
              f"{r['pumps_in']:>7}{r['pumps_out']:>7}{r['conveyance_m']:>10,.0f}")
