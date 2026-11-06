# -*- coding: utf-8 -*-
"""C1 L2+suggested v4 对照 L2+planned 的客户文档用图与数（2026-10-06）→ docs/figs/c1_v4/。

配合 docs/C1_L2S4_vs_L2P_EN.md 用。文档里的数字全部来自本脚本写出的 metrics.json，不手打：
  · 二维：$SWBRAIN_SWMM_WORK/c2d/C1_M61_{L1,L2P,L2S3,L2S4}_2d.npz（形心、面积、高程、最大水深、72 帧水深）
  · 一维泵：C1_M61_L2S4_2way.rpt 的 Pumping Summary；塘末态与二维取水泵：twoway_C1_M61_L2S4.json
  · 措施：out/c1_L2S4_inputs_M61.json；热点：console/data/hotspots.geojson；
    热点汇水区：console/data/hotspot_catch_C1.geojson；塘轮廓：pilot_C1_M61_L2P.json

验收口径（用户 2026-10-06）：关键积水点的积水面积和水深尽快消退 / 严重程度缓解，且不让别处变严重。
所以主指标是「热点 200 m 环内」的深水面积、峰值水深、≥0.15 m 的持续时间与面积×时长，以及「比 L2+planned 变深」的面积；
客户报告用过的「八个热点汇水区并集 ≥0.30 m 面积」也照算照列，并说明为什么它反映不出热点处的变化。

用法：python tools/c1_v4_report.py           # 出图 + metrics.json
      python tools/c1_v4_report.py --check   # 只算数、打印，不画
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))
C2D = WORK / "c2d"
OUT = ROOT / "docs" / "figs" / "c1_v4"
TAG = "M61"
STATES = ["L1", "L2P", "L2S3", "L2S4"]
LABEL = {"L1": "L1 existing", "L2P": "L2+planned (consultant)", "L2S3": "L2+suggested v3", "L2S4": "L2+suggested v4"}
COLOR = {"L1": "#9aa5ad", "L2P": "#b07a1c", "L2S3": "#7c8f9e", "L2S4": "#15803d"}
KS = ["ADM-%s" % n for n in (21, 90, 94, 104, 192, 202, 486, 487)]
RING = 200.0
TH = 0.30
BAND = 0.05
WIN = (247700.0, 252700.0, 2698600.0, 2701700.0)      # 八个热点所在的放大窗（UTM 40N）


def load_state(st):
    z = np.load(C2D / f"C1_{TAG}_{st}_2d.npz")
    return {"xy": z["xy"].astype(float), "area": z["area"].astype(float), "elev": z["elev"].astype(float),
            "hmax": z["hmax"].astype(float), "F": z["frames"].astype(np.float32), "t": z["t_h"].astype(float)}


def hotspots():
    from pyproj import Transformer
    T = Transformer.from_crs(4326, 32640, always_xy=True)
    hj = json.loads((ROOT / "console/data/hotspots.geojson").read_text(encoding="utf-8"))
    H = {}
    for f in hj["features"]:
        p = f["properties"]
        if p.get("hotspot_id") in KS:
            H[p["hotspot_id"]] = {"xy": T.transform(*f["geometry"]["coordinates"]), "crit": p.get("criticality")}
    return H


def catch_union():
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform as shp_tf, unary_union
    T = Transformer.from_crs(4326, 32640, always_xy=True)
    g = json.loads((ROOT / "console/data/hotspot_catch_C1.geojson").read_text(encoding="utf-8"))
    return unary_union([shp_tf(T.transform, shape(f["geometry"])).buffer(0) for f in g["features"] if f["properties"].get("kind") != "合计"])


def pumping(rpt):
    """Pumping Summary → {泵名: (抽水量 m³, 运行时间占比 %)}（10^6 ltr 列 ×1000）"""
    t = pathlib.Path(rpt).read_text(errors="ignore")
    i = t.find("Pumping Summary")
    out = {}
    for ln in t[i:].splitlines()[8:]:
        q = ln.split()
        if not q or not re.match(r"^[A-Za-z0-9_]+$", q[0]):
            if out:
                break
            continue
        if len(q) >= 7:
            try:
                out[q[0]] = (float(q[6]) * 1000.0, float(q[1]))
            except ValueError:
                pass
    return out


def flooding_total(rpt):
    """Node Flooding Summary 的总溢流量 m³（10^6 ltr 列 ×1000）"""
    t = pathlib.Path(rpt).read_text(errors="ignore")
    m = re.search(r"Node Flooding Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    tot = 0.0
    for ln in (m.group(1).splitlines() if m else []):
        q = ln.split()
        if len(q) >= 6:
            try:
                tot += float(q[5]) * 1000.0
            except ValueError:
                pass
    return tot


def raster(xy, v, win, cell, rmax=12.0):
    """形心值 → 规则格（最近形心，rmax 外为空）。只为出图。"""
    from scipy.spatial import cKDTree
    x0, x1, y0, y1 = win
    gx = np.arange(x0 + cell / 2, x1, cell); gy = np.arange(y1 - cell / 2, y0, -cell)
    X, Y = np.meshgrid(gx, gy)
    sel = (xy[:, 0] >= x0 - 50) & (xy[:, 0] <= x1 + 50) & (xy[:, 1] >= y0 - 50) & (xy[:, 1] <= y1 + 50)
    d, i = cKDTree(xy[sel]).query(np.c_[X.ravel(), Y.ravel()], distance_upper_bound=rmax)
    out = np.full(X.size, np.nan)
    ok = np.isfinite(d)
    out[ok] = v[sel][i[ok]]
    return out.reshape(X.shape)


# ---------------------------------------------------------------- 指标
def metrics(S, H, U):
    from scipy.spatial import cKDTree
    from shapely import contains_xy
    xy = S["L2P"]["xy"]
    for st in STATES:
        assert len(S[st]["xy"]) == len(xy), f"{st} 的单元数与 L2P 不同"
    tree = cKDTree(xy)
    rings = {k: np.array(tree.query_ball_point(H[k]["xy"], RING)) for k in KS}
    ring_any = np.zeros(len(xy), bool)
    for k in KS:
        ring_any[rings[k]] = True
    inside = contains_xy(U, xy[:, 0], xy[:, 1])
    M = {"tag": TAG, "ring_m": RING, "th_m": TH, "hotspots": {}, "states": {}}
    for st in STATES:
        s = S[st]; ar = s["area"]; F = s["F"]; t = s["t"]; dt = float(t[1] - t[0])
        i2 = int(np.argmin(abs(t - 2.0)))
        vol = (F * ar).sum(1)
        M["states"][st] = {
            "manhole_flooding_m3": round(flooding_total(C2D / f"C1_{TAG}_{st}_2way.rpt")),
            "union_ge030_ha": round(float(ar[(s["hmax"] >= TH) & inside].sum() / 1e4), 2),
            "rings_ge030_ha": round(float(ar[(s["hmax"] >= TH) & ring_any].sum() / 1e4), 3),
            "domain_vol_2h_m3": round(float(vol[i2])), "domain_vol_6h_m3": round(float(vol[-1])),
            "domain_recede_pct": round(float((1 - vol[-1] / vol[i2]) * 100), 1)}
        for k in KS:
            idx = rings[k]; h = F[:, idx]; a = ar[idx]; D = h.max(1)
            A30 = (h >= TH) @ a / 1e4
            M["hotspots"].setdefault(k, {"crit": H[k]["crit"]})[st] = {
                "area_ge030_ha": round(float(ar[idx][s["hmax"][idx] >= TH].sum() / 1e4), 3),
                "peak_depth_m": round(float(D.max()), 2), "end_depth_m": round(float(D[-1]), 2),
                "hours_ge015": round(float((D >= 0.15).sum() * dt), 1), "hours_ge010": round(float((D >= 0.10).sum() * dt), 1),
                "areatime_ge015_hah": round(float(((h >= 0.15) @ a).sum() * dt / 1e4), 2),
                "areatime_ge030_hah": round(float(A30.sum() * dt), 3),
                "series_t": [round(float(x), 3) for x in t], "series_depth": [round(float(x), 3) for x in D]}
    dh = S["L2S4"]["hmax"] - S["L2P"]["hmax"]; ar = S["L2P"]["area"]
    M["side"] = {"deeper_ge005_ha": round(float(ar[dh >= BAND].sum() / 1e4), 2), "max_increase_m": round(float(dh.max()), 3),
                 "shallower_ge005_ha": round(float(ar[dh <= -BAND].sum() / 1e4), 2),
                 "deeper_ge002_ha": round(float(ar[dh >= 0.02].sum() / 1e4), 3)}
    # 残余积水离热点多远（L2S4 汇水区并集里仍 ≥0.30 m 的单元）
    from scipy.spatial import cKDTree as KD
    HT = KD(np.array([H[k]["xy"] for k in KS]))
    rem = np.flatnonzero((S["L2S4"]["hmax"] >= TH) & inside)
    dist = HT.query(xy[rem])[0]
    bands = [(0, 200), (200, 500), (500, 1000), (1000, 2000), (2000, 1e9)]
    M["union_remaining_by_distance_ha"] = {f"{int(a)}-{int(b) if b < 1e9 else 'max'} m": round(float(ar[rem][(dist >= a) & (dist < b)].sum() / 1e4), 2) for a, b in bands}
    # 措施与泵
    inp = json.loads((ROOT / "out" / f"c1_L2S4_inputs_{TAG}.json").read_text(encoding="utf-8"))
    pv = pumping(C2D / f"C1_{TAG}_L2S4_2way.rpt")
    tw = json.loads((C2D / f"twoway_C1_{TAG}_L2S4.json").read_text(encoding="utf-8"))
    conv_m3 = {m["id"]: round(pv.get(m["id"] + "_P", (0.0, 0.0))[0]) for m in inp["convs"]}
    conv_util = {m["id"]: pv.get(m["id"] + "_P", (0.0, 0.0))[1] for m in inp["convs"]}
    sump_m3 = {q["id"]: round(q["pumped_m3"]) for q in tw.get("pumps2d") or []}
    twP = json.loads((C2D / f"twoway_C1_{TAG}_L2P.json").read_text(encoding="utf-8"))
    M["union_area_ha"] = round(float(U.area / 1e4))
    M["l2p_ponds"] = [{"pond": p["unitid"], "fill_pct": round(p.get("fill_pct") or 0, 1)} for p in twP.get("ponds") or []]
    M["measures"] = {"n_sump": len(inp["measures"]), "n_conv": len(inp["convs"]), "n_pumps": inp["n_pumps"], "q_total_m3s": inp["q_total_m3s"],
                     "sump_q_m3s": round(sum(m["q_m3s"] for m in inp["measures"]), 2), "conv_q_m3s": round(sum(m["q_m3s"] for m in inp["convs"]), 2),
                     "conv_pumped_m3": round(sum(conv_m3.values())), "sump_pumped_m3": round(sum(sump_m3.values())),
                     "conv_never_on": [k for k, v in conv_m3.items() if v <= 0],
                     "conv_running_lt5pct": sorted(k for k, v in conv_util.items() if v < 5.0),
                     "sump_lt100m3": sorted(k for k, v in sump_m3.items() if v < 100),
                     "ponds": [{"pond": p["unitid"], "fill_pct": round(p.get("fill_pct") or 0, 1), "overflow_m3": round(p.get("overflow") or 0)} for p in tw.get("ponds") or []]}
    return M


# ---------------------------------------------------------------- 图
def _style(plt):
    plt.rcParams.update({"figure.dpi": 200, "savefig.dpi": 200, "font.size": 9, "font.family": "DejaVu Sans",
                         "axes.spines.top": False, "axes.spines.right": False})


def _hs(ax, H, label=True):
    import matplotlib.patches as mp
    for k in KS:
        x, y = H[k]["xy"]
        ax.add_patch(mp.Circle((x, y), RING, fill=False, ec="#111", lw=0.8, ls="--"))
        ax.plot(x, y, marker="*", ms=8, mfc="#d4202c" if H[k]["crit"] == "Very High" else "#f08c00", mec="k", mew=0.5, zorder=6)
        if label:
            ax.text(x + 120, y + 110, k, fontsize=7, zorder=7, bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8))


def _ponds(ax, alpha=0.35):
    meta = json.loads((C2D / f"pilot_C1_{TAG}_L2P.json").read_text(encoding="utf-8"))
    for p in meta.get("ponds") or []:
        fp = p["footprint"]; polys = fp["coordinates"] if fp["type"] == "MultiPolygon" else [fp["coordinates"]]
        for poly in polys:
            a = np.array(poly[0])[:, :2]
            ax.fill(a[:, 0], a[:, 1], fc="#6fa8dc", ec="#1f5f96", lw=0.6, alpha=alpha, zorder=3)
    return {p["node"]: p["xy"] for p in meta.get("ponds") or []}


def _frame(ax, win=WIN):
    ax.set_xlim(win[0], win[1]); ax.set_ylim(win[2], win[3]); ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(True); s.set_color("#999"); s.set_linewidth(0.6)
    x0, y0 = win[0] + 150, win[2] + 150
    ax.plot([x0, x0 + 1000], [y0, y0], color="k", lw=2)
    ax.text(x0 + 500, y0 + 60, "1 km", ha="center", fontsize=7)


def fig_measures(S, H, U, plt):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from pyproj import Transformer
    T = Transformer.from_crs(4326, 32640, always_xy=True)
    inp = json.loads((ROOT / "out" / f"c1_L2S4_inputs_{TAG}.json").read_text(encoding="utf-8"))
    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    s = S["L2P"]
    ax.imshow(raster(s["xy"], s["elev"], WIN, 10.0), extent=WIN, cmap="Greys_r", vmin=0, vmax=12, alpha=0.45, zorder=1)
    pxy = _ponds(ax, 0.55)
    loc = {e["id"]: T.transform(e["ll"][1], e["ll"][0]) for e in inp["map"]}
    for m in inp["convs"]:
        x, y = loc[m["id"]]; px, py = pxy[m["to_pond"]]
        ax.plot([x, px], [y, py], color="#7b3fa0", lw=0.6, alpha=0.55, zorder=4)
        ax.plot(x, y, "o", ms=4.2, mfc="#7b3fa0", mec="white", mew=0.4, zorder=5)
    for m in inp["measures"]:
        x, y = m["cx"], m["cy"]; px, py = pxy[m["to_pond"]]
        ax.plot([x, px], [y, py], color="#0b7a3e", lw=0.9, ls="--", alpha=0.8, zorder=4)
        ax.plot(x, y, "^", ms=6.5, mfc="#0b7a3e", mec="white", mew=0.5, zorder=5)
    _hs(ax, H)
    ax.legend(handles=[Line2D([], [], marker="o", ls="-", color="#7b3fa0", mfc="#7b3fa0", ms=5, lw=0.8, label=f"Manhole interception pump → pond  ({len(inp['convs'])})"),
                       Line2D([], [], marker="^", ls="--", color="#0b7a3e", ms=6, lw=0.9, label=f"Surface sump pump → pond  ({len(inp['measures'])})"),
                       Patch(fc="#6fa8dc", ec="#1f5f96", label="Planned pond (consultant)"),
                       Line2D([], [], marker="*", ls="", mfc="#d4202c", mec="k", ms=9, label="Hotspot (red very high / orange high), 200 m ring")],
              loc="upper right", fontsize=6.8, frameon=True, framealpha=0.92)
    _frame(ax)
    ax.set_title("L2+suggested v4 measures, added on top of L2+planned", fontsize=9.5, loc="left")
    fig.tight_layout(); p = OUT / "fig1_measures.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig); return p


def fig_depth(S, H, plt):
    from matplotlib.colors import BoundaryNorm, ListedColormap
    lv = [0.05, 0.10, 0.15, 0.30, 0.50, 1.0, 3.0]
    cm = ListedColormap(["#d6ecfa", "#9ccbee", "#5aa3dc", "#1f6fb5", "#0b3d7a", "#081f45"])
    nm = BoundaryNorm(lv, cm.N)
    fig, axs = plt.subplots(1, 2, figsize=(7.4, 2.9))
    for ax, st in zip(axs, ["L2P", "L2S4"]):
        s = S[st]; r = raster(s["xy"], s["hmax"], WIN, 8.0)
        r[r < lv[0]] = np.nan
        im = ax.imshow(r, extent=WIN, cmap=cm, norm=nm, zorder=2)
        _ponds(ax, 0.25); _hs(ax, H, label=False); _frame(ax)
        ax.set_title(LABEL[st], fontsize=9, loc="left")
    cb = fig.colorbar(im, ax=axs, orientation="horizontal", fraction=0.05, pad=0.04, ticks=lv)
    cb.set_label("Maximum water depth over the event (m)", fontsize=8); cb.ax.tick_params(labelsize=7)
    p = OUT / "fig2_max_depth.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig); return p


def fig_diff(S, H, M, plt):
    from matplotlib.colors import TwoSlopeNorm
    s0, s1 = S["L2P"], S["L2S4"]
    d = s1["hmax"] - s0["hmax"]
    wet = np.maximum(s0["hmax"], s1["hmax"]) >= 0.05
    r = raster(s0["xy"], np.where(wet, d, np.nan), WIN, 8.0)
    r[np.abs(r) < 0.01] = np.nan
    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    ax.imshow(raster(s0["xy"], np.where(wet, 1.0, np.nan), WIN, 8.0), extent=WIN, cmap="Greys", vmin=0, vmax=4, zorder=1)
    im = ax.imshow(r, extent=WIN, cmap="RdBu_r", norm=TwoSlopeNorm(vcenter=0.0, vmin=-0.30, vmax=0.30), zorder=2)
    _ponds(ax, 0.2); _hs(ax, H); _frame(ax)
    sd = M["side"]
    ax.text(0.01, 0.99, f"Deeper by ≥ 5 cm: {sd['deeper_ge005_ha']:.2f} ha   (largest increase anywhere: +{sd['max_increase_m'] * 100:.0f} cm)\n"
                        f"Shallower by ≥ 5 cm: {sd['shallower_ge005_ha']:.2f} ha",
            transform=ax.transAxes, va="top", fontsize=7.6, bbox=dict(fc="white", ec="#999", lw=0.5, alpha=0.95), zorder=8)
    cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02, extend="both")
    cb.set_label("Change in maximum depth, v4 − L2+planned (m)\nblue = shallower, red = deeper", fontsize=7.5); cb.ax.tick_params(labelsize=7)
    ax.set_title("Change in maximum depth (grey = wet in either case, change < 1 cm)", fontsize=8.8, loc="left")
    fig.tight_layout(); p = OUT / "fig3_depth_change.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig); return p


def fig_series(M, plt):
    fig, axs = plt.subplots(2, 4, figsize=(7.6, 3.9), sharex=True, sharey=True)
    for ax, k in zip(axs.ravel(), KS):
        for st, ls, lw in (("L2P", "-", 1.4), ("L2S3", ":", 1.1), ("L2S4", "-", 1.8)):
            r = M["hotspots"][k][st]
            ax.plot(r["series_t"], r["series_depth"], ls=ls, lw=lw, color=COLOR[st], label=LABEL[st])
        ax.axhline(0.30, color="#b23a2e", lw=0.6, ls="--"); ax.axhline(0.15, color="#999", lw=0.5, ls=":")
        ax.axvspan(0, 2, color="#e8f0f7", zorder=0)
        ax.set_title(k + ("  ★" if M["hotspots"][k]["crit"] == "Very High" else ""), fontsize=8, loc="left", pad=2)
        ax.tick_params(labelsize=7); ax.set_xlim(0, 6); ax.set_ylim(0, 0.55)
    for ax in axs[:, 0]:
        ax.set_ylabel("Max depth in 200 m ring (m)", fontsize=7)
    for ax in axs[1]:
        ax.set_xlabel("Hours from start of rain", fontsize=7)
    h, l = axs[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=3, fontsize=7.4, frameon=False, bbox_to_anchor=(0.5, -0.02))
    fig.text(0.99, 0.995, "shaded = rain (0–2 h);  red dashed = 0.30 m;  grey dotted = 0.15 m;  ★ = very high criticality",
             ha="right", va="top", fontsize=6.6, color="#555")
    fig.tight_layout(rect=(0, 0.06, 1, 0.97)); p = OUT / "fig4_depth_over_time.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig); return p


def fig_union(S, H, U, M, plt):
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch
    from shapely import contains_xy
    s0, s1 = S["L2P"], S["L2S4"]
    win = (247300.0, 256300.0, 2692900.0, 2704100.0)
    ins = contains_xy(U, s0["xy"][:, 0], s0["xy"][:, 1])
    a = (s0["hmax"] >= TH) & ins; b = (s1["hmax"] >= TH) & ins
    cls = np.full(len(ins), np.nan); cls[a & ~b] = 1.0; cls[b] = 2.0
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(7.4, 4.3), gridspec_kw={"width_ratios": [1.05, 1]})
    for g in getattr(U, "geoms", [U]):
        x, y = g.exterior.xy; ax.plot(x, y, color="#444", lw=0.5, zorder=3)
    # 40 m 一格、最近 30 m 内的形心：单个 15 m 单元在 9 km 宽的图上看不见，放大到能看见的尺寸（只为显示，统计按单元）
    ax.imshow(raster(s0["xy"], cls, win, 40.0, rmax=30.0), extent=win, cmap=ListedColormap(["#2b8cbe", "#d7301f"]),
              vmin=0.5, vmax=2.5, zorder=2, interpolation="nearest")
    for k in KS:
        x, y = H[k]["xy"]; ax.plot(x, y, "*", ms=6, mfc="#ffd400", mec="k", mew=0.4, zorder=6)
    ax.add_patch(plt.Rectangle((WIN[0], WIN[2]), WIN[1] - WIN[0], WIN[3] - WIN[2], fill=False, ec="k", lw=0.8, ls="--", zorder=5))
    _frame(ax, win)
    ax.legend(handles=[Patch(fc="#d7301f", label="still ≥ 0.30 m under v4"), Patch(fc="#2b8cbe", label="≥ 0.30 m under L2+planned, cleared by v4"),
                       plt.Line2D([], [], marker="*", ls="", mfc="#ffd400", mec="k", ms=8, label="hotspot"),
                       Patch(fc="none", ec="k", ls="--", label="cluster window (Figs 1–3)")], loc="upper right", fontsize=6.4, framealpha=0.92)
    ax.set_title("8 hotspot catchments (union): cells ≥ 0.30 m", fontsize=8.6, loc="left")
    d = M["union_remaining_by_distance_ha"]
    ks = list(d); vs = [d[k] for k in ks]
    bx.barh(range(len(ks)), vs, color=["#15803d" if i == 0 else "#d7301f" for i in range(len(ks))])
    for i, v in enumerate(vs):
        bx.text(v + 0.1, i, f"{v:.2f} ha", va="center", fontsize=7.5)
    bx.set_yticks(range(len(ks))); bx.set_yticklabels([k.replace("-max", "+") for k in ks], fontsize=7.5); bx.invert_yaxis()
    bx.set_xlabel("Area still ≥ 0.30 m under v4 (ha)", fontsize=7.5); bx.tick_params(labelsize=7)
    bx.set_xlim(0, max(vs) * 1.35 if max(vs) else 1)
    bx.set_title("…by distance to the nearest hotspot", fontsize=8.6, loc="left")
    fig.tight_layout(); p = OUT / "fig5_catchment_union.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig); return p


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--check", action="store_true"); a = ap.parse_args(argv)
    H = hotspots(); U = catch_union()
    S = {st: load_state(st) for st in STATES}
    M = metrics(S, H, U)
    OUT.mkdir(parents=True, exist_ok=True)
    hs = M["hotspots"]
    M["totals"] = {st: {"areatime_ge015_hah": round(sum(hs[k][st]["areatime_ge015_hah"] for k in KS), 2),
                        "areatime_ge030_hah": round(sum(hs[k][st]["areatime_ge030_hah"] for k in KS), 3)} for st in STATES}
    M["totals"]["peak_down_ge005"] = [k for k in KS if hs[k]["L2P"]["peak_depth_m"] - hs[k]["L2S4"]["peak_depth_m"] >= 0.05 - 1e-9]
    M["totals"]["still_ge010_at_6h_v4"] = [k for k in KS if hs[k]["L2S4"]["end_depth_m"] >= 0.10]
    M["measures"]["pumped_to_ponds_m3"] = M["measures"]["conv_pumped_m3"] + M["measures"]["sump_pumped_m3"]
    slim = json.loads(json.dumps(M))
    for k in KS:
        for st in STATES:
            slim["hotspots"][k][st].pop("series_t"); slim["hotspots"][k][st].pop("series_depth")
    (OUT / "metrics.json").write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: slim[k] for k in ("states", "side", "union_remaining_by_distance_ha", "measures")}, ensure_ascii=False, indent=1))
    for k in KS:
        print(k, {st: {x: slim["hotspots"][k][st][x] for x in ("area_ge030_ha", "peak_depth_m", "end_depth_m", "hours_ge015", "hours_ge010", "areatime_ge015_hah")}
                  for st in ("L2P", "L2S3", "L2S4")})
    if a.check:
        return 0
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _style(plt)
    for f in (lambda: fig_measures(S, H, U, plt), lambda: fig_depth(S, H, plt), lambda: fig_diff(S, H, M, plt),
              lambda: fig_series(M, plt), lambda: fig_union(S, H, U, M, plt)):
        print("写出", f())
    (OUT / "来源.txt").write_text("本目录的图与 metrics.json 由 tools/c1_v4_report.py 生成；数据见脚本头注释。\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
