# -*- coding: utf-8 -*-
"""防线 2+（第四版）：塘要挖在**能挖的地**上，而且是个面。

前三版的塘位是积水点聚类的加权重心——模型里画得出来，现实里可能落在高速、厂房、住宅上。
这一版加土地利用约束，两级判据（覆盖不均匀，只能分开处理）：

  一级  makani 库 public.ud_plot 的 Public Open Space / Recreational / Agricultural 地块。
        塘的轮廓 = **该地块的真实多边形**，库容 = min(需要, 地块面积 × 挖深)。
        实测覆盖：Shaliela 34 块可用、RB3 7 块、Mussafah **0 块**（工业区不在这份台账里）。

  二级  没有地块台账的地方，反过来排除：建筑**轮廓**退让 25 m、路廊退让 20 m、
        台账里明确标了不可挖的地块按边界排掉，剩下的栅格里找一块装得下的方形。
        轮廓 = 那个方形。这是兜底不是等价物——权属、规划、地下管线都没查。
        但它能把 Parsons 自己在 Mussafah 选的塘位（STO-PO3）识别出来（4 m 内即有空地），
        说明判据不离谱。

跑完用独立判据复核（tools/check_land.py：直接查 bas_building / ud_plot，
不用掩膜自己那份导出）——第一版就是这么抓出「建筑只按一个顶点打圆」的。

吸不到任何可用地的聚类，**如实报「这里挖不了」**，不硬塞一个挖不了的塘进模型。
"""
import json, math, pathlib, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))               # 兄弟脚本
sys.path.insert(0, str(HERE.parent))        # swbrain 包
from workdir import OUT, WORK
from dig_pond import read_sections, parse_nodes, parse_coords, flooding_nodes, full_flow
from dig_pond3 import cluster_sites, dig3, DN_TIERS
from swbrain import landuse as LU


def site_on_land(cfg, name, sites, depth_m=2.0, log=print):
    """把理想塘位吸附到可用地上；吸不到就剔掉。返回 (可建的, 剔掉的)。"""
    from pyproj import Transformer
    import pandas as pd
    to_wgs = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)

    d = pd.read_parquet(OUT / f"{name}.L1.nodes.parquet")
    x, y = to_utm.transform(d["lon"].values, d["lat"].values)
    pad = 800.0
    bb = (x.min() - pad, y.min() - pad, x.max() + pad, y.max() + pad)
    free = tf = None
    xy, ar, ft = LU.load_plots(cfg, name)
    if not len(ft):
        free, tf = LU.open_land_mask(cfg, name, bb, log=log)

    ok, dropped, used = [], [], set()
    for s in sites:
        need = s["vol_m3"] / depth_m                       # 需要的占地 m²
        hit = LU.snap_to_plot(cfg, name, s["x"], s["y"], need, used) if len(ft) else None
        if hit:
            used.add(hit["plot_id"])
            cap = min(s["vol_m3"], hit["area_m2"] * depth_m)
            s.update({"x": hit["x"], "y": hit["y"], "vol_m3": cap,
                      "area_m2": cap / depth_m, "land": "plot",
                      "plot_id": hit["plot_id"], "land_use": hit["use"],
                      "plot_area_m2": hit["area_m2"], "moved_m": hit["dist_m"],
                      "footprint": hit["geometry"], "capped_by_land": cap < s["vol_m3"] - 1})
            ok.append(s); continue
        if free is None:                                   # 有地块台账但吸不到 → 换兜底再试一次
            free, tf = LU.open_land_mask(cfg, name, bb, log=log)
        sq = LU.fit_square(free, tf, s["x"], s["y"], need)
        if sq:
            h = sq["side_m"] / 2
            ring = [[sq["x"] - h, sq["y"] - h], [sq["x"] + h, sq["y"] - h],
                    [sq["x"] + h, sq["y"] + h], [sq["x"] - h, sq["y"] + h],
                    [sq["x"] - h, sq["y"] - h]]
            lo, la = to_wgs.transform([p[0] for p in ring], [p[1] for p in ring])
            s.update({"x": sq["x"], "y": sq["y"], "land": "open_land",
                      "moved_m": sq["dist_m"], "capped_by_land": False,
                      "footprint": {"type": "Polygon", "coordinates": [list(zip(lo, la))]}})
            ok.append(s); continue
        dropped.append(s)
    return ok, dropped


def resize_inlets(sites, window_h=2.0, max_inlets=4):
    """库容被地块削小之后，进水管要跟着收——不然又回到「进水太大、塘变过水通道」。"""
    for s in sites:
        n = len(s["connect"])
        need = s["vol_m3"] / window_h
        s["dn_mm"] = next((d for d in DN_TIERS if full_flow(d / 1000.0) * n >= need), 1500)
        s["q_in_m3h"] = full_flow(s["dn_mm"] / 1000.0) * n
    return sites


if __name__ == "__main__":
    from swbrain import db
    cfg = db.load_config(None)
    PLAN = {"Mussafah": (6, 250.0), "Shaliela": (10, 400.0), "RB3": (10, 400.0)}
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for m, (n, rad) in PLAN.items():
        if only and m != only:
            continue
        raw = cluster_sites(WORK / f"{m}_baseline.inp", WORK / f"{m}_baseline.rpt",
                            n, radius_m=rad, mult=1.5, log=lambda *a: None)
        want = sum(s["vol_m3"] for s in raw)
        ok, dropped = site_on_land(cfg, m, raw)
        resize_inlets(ok)
        for i, s in enumerate(ok, 1):
            s["id"] = f"NP{i}"
        got = sum(s["vol_m3"] for s in ok)
        print(f"\n{m}：理想 {len(raw)} 个塘 {want:,.0f} m³ → 能挖 {len(ok)} 个 {got:,.0f} m³"
              f"（{got / want * 100:.0f}%）｜挖不了 {len(dropped)} 处")
        print(f"  {'塘':<5}{'库容 m³':>10}{'占地 m²':>10}{'用地':>12}{'挪了 m':>8}{'进水 m³/h':>11}  说明")
        for s in ok:
            lu = s.get("land_use") or ("空地（兜底）" if s["land"] == "open_land" else "—")
            print(f"  {s['id']:<5}{s['vol_m3']:>10,.0f}{s['area_m2']:>10,.0f}{str(lu)[:11]:>12}"
                  f"{s.get('moved_m', 0):>8,.0f}{s['q_in_m3h']:>11,.0f}"
                  f"  {'受地块削容' if s.get('capped_by_land') else ''}")
        if dropped:
            print('  挖不了的：' + '、'.join(f"{z['vol_m3']:,.0f} m³" for z in dropped))
        # 给地图用的塘轮廓
        gj = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": s["footprint"],
             "properties": {"id": s["id"], "vol_m3": round(s["vol_m3"]), "land": s["land"],
                            "land_use": s.get("land_use"), "plot_id": s.get("plot_id"),
                            "moved_m": round(s.get("moved_m", 0)),
                            "q_in_m3h": round(s["q_in_m3h"])}} for s in ok]}
        (OUT / f"{m}.L2P.ponds.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
        dig3(WORK / f"{m}_baseline.inp", WORK / f"{m}_L2v.inp", ok, log=lambda *a: None)
        print(f"  → {m}_L2v.inp + {m}.L2P.ponds.geojson")
