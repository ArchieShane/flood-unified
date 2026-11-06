# -*- coding: utf-8 -*-
"""Mussafah_00 各态 .sww → 二维统计（容器内跑）。五态统一截到 5 h（L1 算到 10 h，其余 5 h）。
输出 /work/c2d/mussafah_sww_stats.json；tools/mussafah_stats.py 读它出交付表。"""
import json
import numpy as np
from netCDF4 import Dataset

CAP_H = 5.0
OUT = {}
for st, tag in (("L1", "L1"), ("L2+A", "L2"), ("L2+A+B", "L2AB"), ("L2+B", "L2B")):
    d = Dataset(f"/work/c2d/anuga_out/Mussafah_{tag}_R61D2_2way.sww")
    tw = json.load(open(f"/work/c2d/twoway_Mussafah_{tag}_R61D2.json", encoding="utf-8"))
    t = np.ma.filled(d.variables["time"][:], 0).astype(float) / 3600.0 + tw["start_h"]          # sww 的时间从二维接入算起
    k = np.flatnonzero(t <= CAP_H + 1e-6)
    F = lambda a: np.ma.filled(a, 0).astype(float)
    x = F(d.variables["x"][:]); y = F(d.variables["y"][:]); V = np.ma.filled(d.variables["volumes"][:], 0).astype(int)
    ar = 0.5 * np.abs((x[V[:, 1]] - x[V[:, 0]]) * (y[V[:, 2]] - y[V[:, 0]]) - (x[V[:, 2]] - x[V[:, 0]]) * (y[V[:, 1]] - y[V[:, 0]]))
    el = F(d.variables["elevation_c"][:])
    if el.ndim > 1:
        el = el[0]
    hmax = np.zeros(len(ar)); vmax = np.zeros(len(ar)); hvmax = np.zeros(len(ar)); first = np.full(len(ar), np.nan)
    dur10 = np.zeros(len(ar)); wet_t = []; vol_t = []
    dt = float(t[1] - t[0])
    for i in k:
        h = np.maximum(F(d.variables["stage_c"][i]) - el, 0.0)
        u = F(d.variables["xmomentum_c"][i]); w = F(d.variables["ymomentum_c"][i])
        sp = np.where(h > 0.05, np.hypot(u, w) / np.maximum(h, 1e-6), 0.0)
        hmax = np.maximum(hmax, h); vmax = np.maximum(vmax, sp); hvmax = np.maximum(hvmax, h * sp)
        first = np.where(np.isnan(first) & (h >= 0.02), t[i], first)
        dur10 += (h >= 0.10) * dt
        wet_t.append(float(ar[h >= 0.02].sum())); vol_t.append(float((h * ar).sum()))
    wet_t = np.array(wet_t); vol_t = np.array(vol_t); tk = t[k]
    ip = int(wet_t.argmax())
    OUT[st] = {
        "cap_h": CAP_H, "t_from_h": float(t[0]), "t_to_h": float(tk[-1]), "n_frames": int(len(k)), "frame_s": dt * 3600,
        "domain_ha": float(ar.sum() / 1e4), "n_tri": int(len(ar)),
        "peak_wet_002_ha": float(wet_t[ip] / 1e4), "peak_wet_t_h": float(tk[ip]),
        "max_area_ge_002_ha": float(ar[hmax >= 0.02].sum() / 1e4), "max_area_ge_010_ha": float(ar[hmax >= 0.10].sum() / 1e4),
        "max_area_ge_030_ha": float(ar[hmax >= 0.30].sum() / 1e4), "max_area_ge_080_ha": float(ar[hmax >= 0.80].sum() / 1e4),
        "max_depth_m": float(hmax.max()),
        "peak_surface_vol_m3": float(vol_t.max()), "end_surface_vol_m3": float(vol_t[-1]), "end_wet_002_ha": float(wet_t[-1] / 1e4),
        "max_speed_ms": float(vmax.max()), "area_speed_ge_05_ha": float(ar[vmax >= 0.5].sum() / 1e4),
        "area_hv_ge_05_ha": float(ar[hvmax >= 0.5].sum() / 1e4), "max_hv_m2s": float(hvmax.max()),
        "first_wet_h": float(np.nanmin(first)) if np.isfinite(first).any() else None,
        "area_dur10_ge_1h_ha": float(ar[dur10 >= 1.0].sum() / 1e4),
    }
    s = tw.get("series") or []
    at = [r for r in s if r["t_h"] <= CAP_H + 1e-6]
    if at:
        OUT[st]["acc_1to2_5h_m3"] = float(at[-1]["acc_1to2"]); OUT[st]["acc_2to1_5h_m3"] = float(at[-1]["acc_2to1"])
    OUT[st]["n_coupled"] = tw["n_coupled"]
    print(st, {k2: round(v, 3) if isinstance(v, float) else v for k2, v in OUT[st].items()}, flush=True)
json.dump(OUT, open("/work/c2d/mussafah_sww_stats.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
