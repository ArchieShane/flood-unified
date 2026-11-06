# -*- coding: utf-8 -*-
"""试跑（couple2way_v2）的结果 → 逐帧水深图、最大水深图、L1 / L2 对照与缺口。

输入：c2d/{名}_{态}_2d.npz（每 5 分钟一帧形心水深 + 全程最大水深）与 twoway_{名}_{态}.json。
口径与 Mussafah_00 的 sww_frames.py 相同：形心水深、< 0.02 m 算干、面积按三角单元真实面积加权、
色标共用（开方取色，0–VMAX）。「数从单元算，图从格子出」。

用法：python tools/pilot_frames.py [--name Pilot_MAR]
产物：console/data/flood2d_pilot/{L1,L2}/f###.png、max.png、manifest.json；out/pilot_Pilot_MAR_R61D2.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from sww_frames import DRY_M, VMAX, ramp  # noqa: E402
from workdir import WORK  # noqa: E402

OUT = ROOT / "console" / "data" / "flood2d_pilot"


SHOW_M = 0.10     # 出图阈值：这一片 64% 的地面直接下雨，0.02 m 会把整片雨水膜画出来，真积水反而看不清


def render(xy, val, cell, path, show=SHOW_M):
    """形心值 → 按格取最大 → 投到 WGS84 → PNG。返回 [[s, w], [n, e]]。"""
    from PIL import Image
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    x0, y0 = float(xy[:, 0].min()), float(xy[:, 1].min())
    nx = int(np.ceil((xy[:, 0].max() - x0) / cell)) + 1
    ny = int(np.ceil((xy[:, 1].max() - y0) / cell)) + 1
    col = np.clip(((xy[:, 0] - x0) / cell).astype(int), 0, nx - 1)
    row = np.clip(((xy[:, 1] - y0) / cell).astype(int), 0, ny - 1)
    g = np.zeros(nx * ny, np.float32)
    v = np.where(val >= show, val, 0.0).astype(np.float32)
    np.maximum.at(g, row * nx + col, v)
    g = g.reshape(ny, nx)[::-1]
    tr = from_origin(x0, y0 + ny * cell, cell, cell)
    dtr, dw, dh = calculate_default_transform("EPSG:32640", "EPSG:4326", nx, ny,
                                              left=x0, bottom=y0, right=x0 + nx * cell, top=y0 + ny * cell)
    w = np.zeros((dh, dw), np.float32)
    reproject(source=g, destination=w, src_transform=tr, src_crs="EPSG:32640", dst_transform=dtr,
              dst_crs="EPSG:4326", resampling=Resampling.max, src_nodata=0, dst_nodata=0)
    Image.fromarray(ramp(w), "RGBA").save(path, optimize=True)
    return [[round(dtr.f + dh * dtr.e, 6), round(dtr.c, 6)], [round(dtr.f, 6), round(dtr.c + dw * dtr.a, 6)]]


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Pilot_MAR")
    ap.add_argument("--states", default="L1,L2", help="逗号分隔，第一个是对比基准")
    ap.add_argument("--out", default="flood2d_pilot", help="console/data 下的输出目录")
    ap.add_argument("--region", default="Madinat Al Riyad（试跑框 14.4 × 14.1 km）")
    ap.add_argument("--scen", default="61mm/2h", help="降雨情景（写进 manifest）")
    ap.add_argument("--stats-tag", default="R61D2", help="out/pilot_{名}_{这个}.json")
    a = ap.parse_args(argv)
    states = a.states.split(",")
    OUT = ROOT / "console" / "data" / a.out
    C2D = WORK / "c2d"
    man = {"region": a.region, "scen": a.scen, "dry_m": DRY_M, "show_m": SHOW_M, "vmax_m": VMAX,
           "note": "形心水深，< 0.02 m 算干；面积按三角单元真实面积加权。塘接为无出口管网的末端是按位置与标高推断的",
           "states": []}
    stats = {}
    for st in states:
        tag = f"{a.name}_{st}"
        f = C2D / f"{tag}_2d.npz"
        if not f.exists():
            print(f"  缺 {f.name}"); continue
        z = np.load(f)
        tw = json.loads((C2D / f"twoway_{tag}.json").read_text(encoding="utf-8"))
        xy, area, fr, th, hmax = z["xy"].astype(float), z["area"].astype(float), z["frames"], z["t_h"], z["hmax"]
        cell = float(tw["res_m"])
        d = OUT / st; d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("*.png"):
            old.unlink()
        frames, bounds = [], None
        wet_s = np.array([float(area[fr[i].astype(float) >= DRY_M].sum()) for i in range(len(th))])
        wet10 = np.array([float(area[fr[i].astype(float) >= 0.10].sum()) for i in range(len(th))])   # 播放曲线用 ≥0.10 m
        dep_s = np.array([float(fr[i].astype(float).max()) for i in range(len(th))])
        dmax_run = np.maximum.accumulate(dep_s)
        for i in range(len(th)):
            bounds = render(xy, fr[i].astype(float), cell, d / f"f{i:03d}.png")
            frames.append({"t_h": round(float(th[i]), 3), "wet_ha": round(wet_s[i] / 1e4, 2), "wet10_ha": round(wet10[i] / 1e4, 2),
                           "dmax_m": round(float(dmax_run[i]), 3), "png": f"{st}/f{i:03d}.png"})
        render(xy, hmax.astype(float), cell, d / "max.png")
        ip, idp = int(wet10.argmax()), int(dep_s.argmax())      # 「最大面积那一帧」按 ≥0.10 m 挑，和数字卡一致
        iw = int(wet_s.argmax())                                  # 0.02 m 口径的峰值（含雨水膜），只作参考
        s = {"peak_wet_ha": round(wet_s[iw] / 1e4, 2), "peak_wet_t_h": round(float(th[iw]), 3),
             "max_depth_m": round(float(hmax.max()), 3),
             "wet_ever_ha": round(float(area[hmax >= DRY_M].sum()) / 1e4, 2),
             # 这一片有 64% 的地面是直接在二维上下雨的，雨水膜本身就 2–3 cm，
             # 按 0.02 m 算「淹没」会把整片沙漠都算进去。积水面积看 ≥0.10 / ≥0.30 m 两档
             "max_area_ge_0p10_ha": round(float(area[hmax >= 0.10].sum()) / 1e4, 2),
             "max_area_ge_0p30_ha": round(float(area[hmax >= 0.30].sum()) / 1e4, 2),
             "peak_wet_ge_0p10_ha": round(max(float(area[fr[i].astype(float) >= 0.10].sum()) for i in range(len(th))) / 1e4, 2),
             "flood_1d_to_2d_m3": round(tw["acc_m3"]["1to2"]), "back_to_1d_m3": round(tw["acc_m3"]["2to1"]),
             "rain_on_grid_m3": round(tw["acc_m3"]["rain"]), "into_ponds_surface_m3": round(tw["acc_m3"]["pond"]),
             "final_vol_m3": round(tw.get("final_vol_m3", 0)), "boundary_out_m3": round(tw.get("boundary_out_m3", 0)),
             "peak_wet10_t_h": round(float(th[int(wet10.argmax())]), 3),
             "ponds": tw.get("ponds", []), "wall_s": round(tw.get("wall_s", 0)), "n_tri": tw.get("n_tri"),
             "n_nodes": tw.get("n_nodes"), "res_m": tw.get("res_m"), "end_h": tw.get("end_h")}
        stats[st] = s
        man["states"].append({"state": st, "dir": st, "bounds": bounds, "frames": frames, "max_png": f"{st}/max.png",
                              "peak_wet10_ha": round(wet10[ip] / 1e4, 2), "peak_wet10_t_h": round(float(th[ip]), 3),
                              "peak_depth_m": s["max_depth_m"], "peak_frame": ip, "deep_frame": idp,
                              "n_frames": len(frames)})
        print(f"  {st}: {len(frames)} 帧　峰值淹没 {s['peak_wet_ha']:,.1f} ha @ {s['peak_wet_t_h']:.2f} h　"
              f"最大水深 {s['max_depth_m']:.2f} m　曾经淹过 {s['wet_ever_ha']:,.1f} ha")
    (OUT / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    b0 = states[0]
    for st in states[1:]:
        if b0 not in stats or st not in stats:
            continue
        g = {}
        for k in ("peak_wet_ge_0p10_ha", "max_area_ge_0p10_ha", "max_area_ge_0p30_ha", "max_depth_m",
                  "peak_wet_ha", "flood_1d_to_2d_m3", "into_ponds_surface_m3", "final_vol_m3"):
            a1, a2 = stats[b0][k], stats[st][k]
            g[k] = {b0: a1, st: a2, "L1": a1, "L2": a2, "abs": round(a2 - a1, 3),
                    "pct": round((a2 - a1) / a1 * 100, 1) if a1 else None}
        stats[f"gap_{b0}_to_{st}"] = g
        print(f"  —— {b0} → {st}")
        for k, v in g.items():
            print(f"  {k:<22} {b0} {v[b0]:>12,}  {st} {v[st]:>12,}  {v['pct'] if v['pct'] is not None else '—':>6}%")
    (ROOT / "out" / f"pilot_{a.name}_{a.stats_tag}.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    # 控制台用的那份：统计 + 试跑框 + 参与的塘（轮廓从 ponds_l2.geojson 按 unitid 取）
    meta = json.loads((C2D / f"pilot_{a.name}_{states[-1]}.json").read_text(encoding="utf-8"))
    dom_wgs = None
    if meta.get("domain_polygon"):                     # 控制台画计算域边界用（Leaflet 要 [lat, lng]）
        from pyproj import Transformer
        tr = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
        dom_wgs = [[round(la, 6), round(lo, 6)] for lo, la in (tr.transform(x, y) for x, y in meta["domain_polygon"])]
    (OUT / "stats.json").write_text(json.dumps({"name": a.name, "states": states, "box_utm": meta["box"], "domain_wgs": dom_wgs,
        "R_served_m": meta.get("R_served_m"), "domain_polygon": meta.get("domain_polygon"),
        "pond_ids": [p["unitid"] for p in meta["ponds"]], "links_to_ponds": meta.get("links_to_ponds"),
        "inferred": meta.get("inferred"),
        **stats}, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
