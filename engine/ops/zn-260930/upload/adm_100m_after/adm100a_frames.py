# -*- coding: utf-8 -*-
"""ADM 全市 100 m（治理后数据）· L1 / L2 → flood-viewer 要的逐帧图（在 anuga-swmm 容器里跑，和 ad_post.py 一样读 /work/c2d）。

输入：c2d/{AD_<tag>}_{L1,L2}_2d.npz、twoway_*.json、pilot_*.json、ad_inputs_<tag>.json、ad_cases/hotspots_ad.json、ad_cases/<区>_domain.geojson
输出：$AD_RESULT_ROOT/$AD_HOST/runtime/console_data/flood2d_ad_<区>_m<雨量>h<历时>g<网格>/
        manifest.json、stats.json（计算域边界）、ad_stats.json（zones / deepest / balance / diffs）、{L1,L2}/f###.png、max.png、diff_L2_minus_L1.png
      这个目录名就是 flood-viewer 扫描的约定（区域代码 ad_<区>，如 ad_cityafter）。

用法：python adm100a_frames.py --tag CITYAFTER_M61H1 [--res 100]
色标、干湿阈值、出图阈值与 pilot_frames.py / sww_frames.py 相同（0.02 m 算干，< 0.10 m 不出图，开方取色 0–1.4 m），
本脚本不依赖那几份源码，单文件即可跑。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

import numpy as np

C2D = pathlib.Path(os.environ.get("AD_C2D", "/work/c2d"))
DRY_M, SHOW_M, VMAX = 0.02, 0.10, 1.40
TH = (0.10, 0.30)
HS_R = 200.0
DIFF_MIN, DIFF_MAX = 0.05, 0.50
STATES = ("L1", "L2")


def ramp(v):
    n = np.clip(v / VMAX, 0.0, 1.0)
    ncol = np.sqrt(n)
    stops = np.array([[125, 211, 252], [37, 130, 235], [29, 78, 216], [76, 29, 149]], dtype=np.float32)
    pos = np.linspace(0.0, 1.0, len(stops))
    rgb = np.empty(n.shape + (3,), dtype=np.float32)
    for c in range(3):
        rgb[..., c] = np.interp(ncol, pos, stops[:, c])
    a = np.where(v >= SHOW_M, 110 + 145 * np.sqrt(n), 0)
    out = np.zeros(n.shape + (4,), np.uint8)
    out[..., :3] = rgb.astype(np.uint8)
    out[..., 3] = np.clip(a, 0, 255).astype(np.uint8)
    return out


def _grid(xy, cell):
    x0, y0 = float(xy[:, 0].min()), float(xy[:, 1].min())
    nx = int(np.ceil((xy[:, 0].max() - x0) / cell)) + 1
    ny = int(np.ceil((xy[:, 1].max() - y0) / cell)) + 1
    col = np.clip(((xy[:, 0] - x0) / cell).astype(int), 0, nx - 1)
    row = np.clip(((xy[:, 1] - y0) / cell).astype(int), 0, ny - 1)
    return x0, y0, nx, ny, row * nx + col


def _warp(g, x0, y0, nx, ny, cell, resampling):
    from rasterio.transform import from_origin
    from rasterio.warp import calculate_default_transform, reproject
    tr = from_origin(x0, y0 + ny * cell, cell, cell)
    dtr, dw, dh = calculate_default_transform("EPSG:32640", "EPSG:4326", nx, ny, left=x0, bottom=y0, right=x0 + nx * cell, top=y0 + ny * cell)
    w = np.zeros((dh, dw), np.float32)
    reproject(source=g, destination=w, src_transform=tr, src_crs="EPSG:32640", dst_transform=dtr, dst_crs="EPSG:4326", resampling=resampling, src_nodata=0, dst_nodata=0)
    return w, [[round(dtr.f + dh * dtr.e, 6), round(dtr.c, 6)], [round(dtr.f, 6), round(dtr.c + dw * dtr.a, 6)]]


def render(geo, val, cell, path):
    from PIL import Image
    from rasterio.warp import Resampling
    x0, y0, nx, ny, idx = geo
    g = np.zeros(nx * ny, np.float32)
    np.maximum.at(g, idx, np.where(val >= SHOW_M, val, 0.0).astype(np.float32))
    w, b = _warp(g.reshape(ny, nx)[::-1], x0, y0, nx, ny, cell, Resampling.max)
    Image.fromarray(ramp(w), "RGBA").save(path, optimize=True)
    return b


def render_diff(geo, dh, cell, path):
    from PIL import Image
    from rasterio.warp import Resampling
    from scipy.ndimage import grey_dilation
    x0, y0, nx, ny, idx = geo
    v = np.where(np.abs(dh) >= DIFF_MIN, dh, 0.0).astype(np.float32)
    lo = np.zeros(nx * ny, np.float32); hi = np.zeros(nx * ny, np.float32)
    np.minimum.at(lo, idx, v); np.maximum.at(hi, idx, v)
    g = np.where(-lo >= hi, lo, hi).reshape(ny, nx)[::-1]
    w, b = _warp(g, x0, y0, nx, ny, cell, Resampling.nearest)
    q = np.clip(np.abs(w) / DIFF_MAX, 0, 1) ** 0.5
    rgba = np.zeros(w.shape + (4,), np.uint8)
    neg, pos = w < 0, w > 0
    rgba[neg] = np.stack([37 * (1 - q[neg]), 99 + 0 * q[neg], 235 - 60 * q[neg], 90 + 165 * q[neg]], -1).astype(np.uint8)
    rgba[pos] = np.stack([220 + 0 * q[pos], 38 + 0 * q[pos], 38 + 0 * q[pos], 90 + 165 * q[pos]], -1).astype(np.uint8)
    for c in range(4):
        rgba[..., c] = np.where(rgba[..., 3] > 0, rgba[..., c], grey_dilation(np.where(rgba[..., 3] > 0, rgba[..., c], 0), size=5))
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)
    return b


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", required=True); ap.add_argument("--res", type=float, default=100.0)
    ap.add_argument("--job-id", default=None, help="任务 id（= 交付目录名）；写进 manifest，flood-viewer 页面显示，和 shp_dat/<job_id>、management 页面对应")
    a = ap.parse_args()
    from pyproj import Transformer
    from scipy.spatial import cKDTree
    t0 = time.time()
    t = a.tag; reg = t.split("_")[0]; name = f"AF_{t}"
    host = os.environ.get("AD_HOST") or "unknown"
    root = pathlib.Path(os.environ.get("AD_RESULT_ROOT", "/media/pop-fs/flood_data/result"))
    meta_all = json.loads((C2D / "ad100a_cases" / "hotspots_ad.json").read_text(encoding="utf-8"))
    RG = meta_all["regions"][reg]
    HS = [h for h in meta_all["hotspots"] if h["region"] == reg]
    inp = json.loads((C2D / f"af_inputs_{t}.json").read_text(encoding="utf-8"))
    post = json.loads((C2D / f"af_stats_{t}.json").read_text(encoding="utf-8"))          # adm100a_post.py 的标记文件：deepest / balance 直接沿用
    rain, dur, res = inp["rain_mm"], inp["dur_h"], a.res
    states = [s for s in STATES if (C2D / f"{name}_{s}_2d.npz").exists()]
    if len(states) < 2:
        raise SystemExit(f"{name}：二维结果不全 {states}")
    D = root / host / "runtime" / "console_data" / f"flood2d_ad_{reg.lower()}_m{int(rain)}h{int(dur)}g{int(res)}"
    D.mkdir(parents=True, exist_ok=True)
    Z = {s: np.load(C2D / f"{name}_{s}_2d.npz") for s in states}
    TW = {s: json.loads((C2D / f"twoway_{name}_{s}.json").read_text(encoding="utf-8")) for s in states}
    z0 = Z[states[0]]; xy = z0["xy"].astype(float); area = z0["area"].astype(float)
    geo = _grid(xy, res)
    tr = cKDTree(xy)
    masks = {}
    for h in HS:
        m = np.zeros(len(xy), bool); m[tr.query_ball_point((h["x"], h["y"]), HS_R)] = True
        masks[f"{h['hotspot_id']}|200m"] = m
    if HS:
        masks[f"{len(HS)} 个热点|200m"] = np.any(list(masks.values()), axis=0)
    masks["全域|all"] = np.ones(len(xy), bool)
    masks = {k: np.flatnonzero(m) for k, m in masks.items()}               # 改用下标：全市 470 个热点 × 60 帧，布尔掩码太慢
    TITLE = "Abu Dhabi 全市（治理后）"
    man = {"job_id": a.job_id, "region": f"{TITLE}（{inp['domain_km2']} km²）", "scen": f"{rain:g}mm/{dur:g}h", "dry_m": DRY_M, "show_m": SHOW_M, "vmax_m": VMAX,
           "note": "形心水深，< 0.02 m 算干；面积按三角单元真实面积加权；< 0.10 m 不出图", "states": []}
    zones = {k: {} for k in masks}
    for s in states:
        z = Z[s]; fr = z["frames"]; th = np.asarray(z["t_h"], float); hm = z["hmax"].astype(float)
        d = D / s; d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("*.png"):
            old.unlink()
        wet = np.zeros(len(th)); wet10 = np.zeros(len(th)); dep = np.zeros(len(th)); frames = []; bounds = None
        mvol = {k: np.zeros(len(th)) for k in masks}
        for i in range(len(th)):
            f = fr[i].astype(float)
            wet[i] = area[f >= DRY_M].sum(); wet10[i] = area[f >= TH[0]].sum(); dep[i] = f.max()
            for k, m in masks.items():
                mvol[k][i] = (f[m] * area[m]).sum()
            bounds = render(geo, f, res, d / f"f{i:03d}.png")
        dmax = np.maximum.accumulate(dep)
        for i in range(len(th)):
            frames.append({"t_h": round(float(th[i]), 3), "wet_ha": round(wet[i] / 1e4, 2), "wet10_ha": round(wet10[i] / 1e4, 2), "dmax_m": round(float(dmax[i]), 3), "png": f"{s}/f{i:03d}.png"})
        render(geo, hm, res, d / "max.png")
        for k, m in masks.items():
            v = mvol[k]; i = int(v.argmax())
            hmk, ak = hm[m], area[m]
            zones[k][s] = {"max_depth_m": round(float(hmk.max()) if len(m) else 0.0, 3), "area_ge_0p10_ha": round(float(ak[hmk >= TH[0]].sum()) / 1e4, 2),
                           "area_ge_0p30_ha": round(float(ak[hmk >= TH[1]].sum()) / 1e4, 2), "peak_vol_m3": round(float(v[i])), "peak_vol_t_h": round(float(th[i]), 2),
                           "zone_area_ha": round(float(ak.sum()) / 1e4, 1)}
        ip, idp = int(wet10.argmax()), int(dep.argmax())
        man["states"].append({"state": s, "dir": s, "bounds": bounds, "frames": frames, "max_png": f"{s}/max.png", "peak_wet10_ha": round(wet10[ip] / 1e4, 2),
                              "peak_wet10_t_h": round(float(th[ip]), 3), "peak_depth_m": round(float(hm.max()), 3), "peak_frame": ip, "deep_frame": idp, "n_frames": len(frames)})
        print(f"  {s}: {len(frames)} 帧，最大水深 {hm.max():.2f} m（{time.time() - t0:.0f} s）", flush=True)
    diffs = []
    dh = Z["L2"]["hmax"].astype(float) - Z["L1"]["hmax"].astype(float)
    fn = "diff_L2_minus_L1.png"
    try:
        b = render_diff(geo, dh, res, D / fn)
    except Exception as e:                       # 区内没有塘：两态一致，差值全 0，画不出来不算失败
        print(f"  差值图没画出来：{e}"); b, fn = None, None
    diffs.append({"a": "L1", "b": "L2", "png": fn, "bounds": b, "shallower_ha": round(float(area[dh <= -DIFF_MIN].sum()) / 1e4, 2),
                  "deeper_ha": round(float(area[dh >= DIFF_MIN].sum()) / 1e4, 2), "max_abs_diff_m": round(float(np.abs(dh).max()), 3)})
    cs = {"job_id": a.job_id, "name": name, "tag": t, "region": reg, "title": TITLE, "rain_mm": rain, "dur_h": dur, "sim_h": inp["sim_h"], "res_m": res, "states": states,
          "state_names": {"L1": "L1", "L2": "L2"}, "zones": zones, "deepest": post.get("deepest", {}), "balance": post.get("balance", {}), "diffs": diffs,
          "koujing": {"水深": "三角单元形心水深（ANUGA stage − elevation）", "干湿": "< 0.02 m 算干；< 0.10 m 不出图",
                      "积水面积": "全程最大水深 ≥ 0.10 / ≥ 0.30 m 的单元面积之和（按三角单元真实面积）",
                      "最大积水量": "逐帧（每 5 分钟）Σ 水深 × 单元面积 的最大值",
                      "积水点 200 m": "离积水点（ADM 热点）坐标 200 m 内的单元；100 m 网格 200 m 内只有十来个单元，数值偏粗",
                      "全域": "二维计算域（全市 1,233.6 km²）",
                      "说明": "L1 = 既有管网 + 有流量的泵；L2 = L1 + 塘 / 渗井（治理后数据）。没有设施清单、雨型图、热点过程线，对应区块为空"}}
    (D / "ad_stats.json").write_text(json.dumps(cs, ensure_ascii=False), encoding="utf-8")
    (D / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    dom = None
    gj = json.loads((C2D / "ad100a_cases" / f"{reg}_domain.geojson").read_text(encoding="utf-8"))
    geoms = [f["geometry"] for f in gj["features"]] if gj.get("type") == "FeatureCollection" else [gj]
    rings = []
    for g in geoms:
        for poly in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]):
            rings.append(poly[0])
    if rings:
        ring = max(rings, key=len)
        dom = [[round(la, 6), round(lo, 6)] for lo, la in ring]                  # 域文件已是 EPSG:4326；Leaflet 要 [lat, lng]
    st0 = {s: {"n_tri": TW[s].get("n_tri"), "n_nodes": TW[s].get("n_nodes"), "end_h": TW[s].get("end_h"), "res_m": res} for s in states}
    (D / "stats.json").write_text(json.dumps({"name": name, "states": states, "domain_wgs": dom, **st0}, ensure_ascii=False), encoding="utf-8")   # 最后写（标记完成）
    print(f"{name}：已写 {D}（{time.time() - t0:.0f} s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
