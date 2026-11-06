# -*- coding: utf-8 -*-
"""C1 L2+suggested v5 对照 L2+planned 的客户文档用图与数（2026-10-07）→ docs/figs/c1_v5/。

配合 docs/C1_L2S5_vs_L2P_EN.md 用。文档里的数字全部来自本脚本写出的 metrics.json，不手打：
  · 二维：$SWBRAIN_SWMM_WORK/c2d/C1_{M61,M96}_{L2P,L2S4,L2S5}_2d.npz
  · 一维：C1_*_L2S5_2way.rpt（泵、重力管峰值流量、井溢流）；塘末态与地面泵：twoway_C1_*_L2S5.json
  · 措施：out/c1_L2S5_inputs_M61.json（96 mm 用同一套措施，--from M61）
口径同 v4（用户 2026-10-06）：热点 200 m 环内尽快退、变浅；别处不比 L2+planned 深 5 cm 以上。

用法：python tools/c1_v5_report.py [--check]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import c1_v4_report as R  # noqa: E402
from build_c1_l2 import read_inp  # noqa: E402

ROOT, C2D = R.ROOT, R.C2D
OUT = ROOT / "docs" / "figs" / "c1_v5"
KS, RING, TH, BAND, WIN = R.KS, R.RING, R.TH, R.BAND, R.WIN
RUNS = {"M61": ["L2P", "L2S4", "L2S5"], "M96": ["L2P", "L2S5"]}
LABEL = {"L2P": "L2+planned (consultant)", "L2S4": "L2+suggested v4 (previous)", "L2S5": "L2+suggested v5"}
COLOR = {"L2P": "#b07a1c", "L2S4": "#7c8f9e", "L2S5": "#15803d"}


def load(tag, st, rings):
    """读一个态：留最大水深，环内逐帧算好过程线就丢掉逐帧（省内存）。"""
    z = np.load(C2D / f"C1_{tag}_{st}_2d.npz")
    F = z["frames"].astype(np.float32); t = z["t_h"].astype(float); ar = z["area"].astype(float)
    hm = z["hmax"].astype(float); dt = float(t[1] - t[0])
    hs = {}
    for k, idx in rings.items():
        h = F[:, idx]; a = ar[idx]; D = h.max(1)
        hs[k] = {"area_ge030_ha": round(float(a[hm[idx] >= TH].sum() / 1e4), 3), "peak_depth_m": round(float(D.max()), 2),
                 "end_depth_m": round(float(D[-1]), 2), "hours_ge015": round(float((D >= 0.15).sum() * dt), 1),
                 "areatime_ge015_hah": round(float(((h >= 0.15) @ a).sum() * dt / 1e4), 2),
                 "series_t": [round(float(x), 3) for x in t], "series_depth": [round(float(x), 3) for x in D]}
    return {"hmax": hm, "hs": hs}


def gp_flow(rpt):
    out = {}
    for ln in pathlib.Path(rpt).read_text(errors="ignore").splitlines():
        q = ln.split()
        if q and q[0].startswith("GP_") and len(q) >= 3 and q[1] == "CONDUIT":
            out[q[0]] = float(q[2])
    return out


def metrics(H, U):
    from scipy.spatial import cKDTree
    from shapely import contains_xy
    z = np.load(C2D / "C1_M61_L2P_2d.npz")
    xy = z["xy"].astype(float); ar = z["area"].astype(float); elev = z["elev"].astype(float)
    tree = cKDTree(xy)
    rings = {k: np.array(tree.query_ball_point(H[k]["xy"], RING)) for k in KS}
    anyr = np.zeros(len(xy), bool)
    for k in KS:
        anyr[rings[k]] = True
    inside = contains_xy(U, xy[:, 0], xy[:, 1])
    inp = json.loads((ROOT / "out" / "c1_L2S5_inputs_M61.json").read_text(encoding="utf-8"))
    M = {"ring_m": RING, "th_m": TH, "runs": {}, "design": {}}
    HM = {}
    for tag, sts in RUNS.items():
        S = {}
        for st in sts:
            z2 = np.load(C2D / f"C1_{tag}_{st}_2d.npz", mmap_mode="r")
            assert len(z2["xy"]) == len(xy), f"{tag} {st} 的单元数与 M61 L2P 不同"
            S[st] = load(tag, st, rings)
        HM[tag] = {st: s["hmax"] for st, s in S.items()}
        T = {"states": {}, "hotspots": {k: {st: S[st]["hs"][k] for st in sts} for k in KS}, "side": {}}
        for st in sts:
            hm = S[st]["hmax"]
            T["states"][st] = {"rings_ge030_ha": round(float(ar[(hm >= TH) & anyr].sum() / 1e4), 3),
                               "union_ge030_ha": round(float(ar[(hm >= TH) & inside].sum() / 1e4), 2),
                               "areatime_ge015_hah": round(sum(S[st]["hs"][k]["areatime_ge015_hah"] for k in KS), 2),
                               "manhole_flooding_m3": round(R.flooding_total(C2D / f"C1_{tag}_{st}_2way.rpt"))}
        for ref in [r for r in ("L2P", "L2S4") if r in S]:
            dh = S["L2S5"]["hmax"] - S[ref]["hmax"]
            T["side"][f"vs_{ref}"] = {"deeper_ge005_ha": round(float(ar[dh >= BAND].sum() / 1e4), 2),
                                      "max_increase_m": round(float(dh.max()), 3),
                                      "shallower_ge005_ha": round(float(ar[dh <= -BAND].sum() / 1e4), 2)}
        tw = json.loads((C2D / f"twoway_C1_{tag}_L2S5.json").read_text(encoding="utf-8"))
        new = [p for p in tw["ponds"] if p["unitid"].startswith("PND_V5")]
        old = [p for p in tw["ponds"] if not p["unitid"].startswith("PND_V5")]
        pv = R.pumping(C2D / f"C1_{tag}_L2S5_2way.rpt")
        T["new_ponds"] = {"n": len(new), "design_m3": round(sum(p["vol_design_m3"] for p in new)),
                          "received_m3": round(sum(p["vol_end_m3"] + p["overflow_m3"] for p in new)),
                          "surface_m3": round(sum(p["surface_captured_m3"] for p in new)),
                          "n_full": sum(1 for p in new if (p["fill_pct"] or 0) >= 99.5),
                          "n_overflow": sum(1 for p in new if p["overflow_m3"] > 1), "overflow_m3": round(sum(p["overflow_m3"] for p in new)),
                          "fill_pct": {p["unitid"]: round(p["fill_pct"] or 0) for p in new}}
        T["old_ponds_overflow"] = {p["unitid"]: round(p["overflow_m3"]) for p in old if p["overflow_m3"] > 1}
        T["old_ponds_ge90"] = sorted(p["unitid"] for p in old if (p["fill_pct"] or 0) >= 90)
        g = gp_flow(C2D / f"C1_{tag}_L2S5_2way.rpt")
        T["gravity_peak_m3s"] = {"min": round(min(g.values()), 2), "max": round(max(g.values()), 2),
                                 "n_ge_036": sum(1 for v in g.values() if v >= 0.36)}
        T["pumped_m3"] = round(sum(v[0] for k, v in pv.items() if re.match(r"^C\d\d_P$", k)) + sum(q["pumped_m3"] for q in tw.get("pumps2d") or []))
        M["runs"][tag] = T
    keep = inp["convs"]; pumps = [c for c in keep if c["v5"]["how"] == "pump"]
    M["design"] = {"v4_pumps": 40, "v4_q_m3s": 14.4, "dropped": inp["drop"], "n_conv_kept": len(keep),
                   "n_gravity": len(inp["pipes"]), "n_conv_pump": len(pumps), "n_sump": len(inp["sumps"]),
                   "n_pumps": inp["n_pumps"], "q_m3s": inp["q_total_m3s"], "n_ponds": len(inp["ponds"]),
                   "pond_area_m2": [round(min(p["area_m2"] for p in inp["ponds"])), round(max(p["area_m2"] for p in inp["ponds"]))],
                   "pond_vol_m3": [round(min(p["vol"] for p in inp["ponds"])), round(max(p["vol"] for p in inp["ponds"]))],
                   "pond_vol_total_m3": round(sum(p["vol"] for p in inp["ponds"])),
                   "pond_floor_m": [round(min(p["z_floor"] for p in inp["ponds"]), 2), round(max(p["z_floor"] for p in inp["ponds"]), 2)],
                   "cut_m3": round(sum(p["cut_m3"] for p in inp["ponds"])),
                   "pipe_d_m": sorted({p["d_m"] for p in inp["pipes"]}), "pipe_L_m": [min(p["L_m"] for p in inp["pipes"]), max(p["L_m"] for p in inp["pipes"])],
                   "land": sorted({p["land"] for p in inp["ponds"]}), "head_m": inp["head_m"], "pond_d_m": inp["pond_d_m"], "size_k": inp["size_k"]}
    return M, {"xy": xy, "ar": ar, "elev": elev}, HM, inp


# ---------------------------------------------------------------- 图
def _ponds(ax, alpha=0.4, new=True):
    meta = json.loads((C2D / "pilot_C1_M61_L2S5.json").read_text(encoding="utf-8"))
    xy = {}
    for p in meta["ponds"]:
        fp = p["footprint"]; polys = fp["coordinates"] if fp["type"] == "MultiPolygon" else [fp["coordinates"]]
        nw = p.get("new_v5")
        if nw and not new:
            continue
        for poly in polys:
            a = np.array(poly[0])[:, :2]
            ax.fill(a[:, 0], a[:, 1], fc="#2ca25f" if nw else "#6fa8dc", ec="#006d2c" if nw else "#1f5f96", lw=0.7, alpha=0.85 if nw else alpha, zorder=3)
        xy[p["node"]] = p["xy"]
    return xy


def fig_measures(G, H, inp, plt):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    S = read_inp(R.WORK / "C1_M61_L2S5.inp")
    nxy = {r[0]: (float(r[1]), float(r[2])) for r in S["[COORDINATES]"]}
    v4 = json.loads((ROOT / "out" / "c1_L2S4_inputs_M61.json").read_text(encoding="utf-8"))
    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    ax.imshow(R.raster(G["xy"], G["elev"], WIN, 10.0), extent=WIN, cmap="Greys_r", vmin=0, vmax=12, alpha=0.45, zorder=1)
    pxy = _ponds(ax, 0.55)
    for p in inp["pipes"]:
        (x, y), (px, py) = nxy[p["from"]], pxy[p["to"]]
        ax.plot([x, px], [y, py], color="#006d2c", lw=1.4, zorder=4)
        ax.plot(x, y, "s", ms=4.2, mfc="#006d2c", mec="white", mew=0.4, zorder=5)
    for c in inp["convs"]:
        if c["v5"]["how"] != "pump":
            continue
        (x, y), (px, py) = nxy[c["node"]], pxy[c["to_pond"]]
        ax.plot([x, px], [y, py], color="#7b3fa0", lw=0.6, alpha=0.55, zorder=4)
        ax.plot(x, y, "o", ms=4.2, mfc="#7b3fa0", mec="white", mew=0.4, zorder=5)
    for m in v4["measures"]:
        if m["id"] not in inp["sumps"]:
            continue
        (px, py) = pxy[m["to_pond"]]
        ax.plot([m["cx"], px], [m["cy"], py], color="#0b7a3e", lw=0.9, ls="--", alpha=0.8, zorder=4)
        ax.plot(m["cx"], m["cy"], "^", ms=6.5, mfc="#0b7a3e", mec="white", mew=0.5, zorder=5)
    R._hs(ax, H)
    d = inp
    ax.legend(handles=[Line2D([], [], marker="s", ls="-", color="#006d2c", ms=5, lw=1.4, label=f"Gravity pipe, manhole → new pond  ({len(d['pipes'])})"),
                       Patch(fc="#2ca25f", ec="#006d2c", label=f"New pond (CAPEX)  ({len(d['ponds'])})"),
                       Line2D([], [], marker="o", ls="-", color="#7b3fa0", ms=5, lw=0.8, label=f"Manhole interception pump → pond  ({sum(1 for c in d['convs'] if c['v5']['how'] == 'pump')})"),
                       Line2D([], [], marker="^", ls="--", color="#0b7a3e", ms=6, lw=0.9, label=f"Surface sump pump → pond  ({len(d['sumps'])})"),
                       Patch(fc="#6fa8dc", ec="#1f5f96", label="Planned pond (consultant)"),
                       Line2D([], [], marker="*", ls="", mfc="#d4202c", mec="k", ms=9, label="Hotspot, 200 m ring")],
              loc="upper right", fontsize=6.5, frameon=True, framealpha=0.92)
    R._frame(ax)
    ax.set_title("L2+suggested v5 measures, added on top of L2+planned", fontsize=9.5, loc="left")
    fig.tight_layout(); p = OUT / "fig1_measures.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig); return p


def fig_change(G, H, HM, M, tag, n, plt):
    from matplotlib.colors import TwoSlopeNorm
    a, b = HM[tag]["L2P"], HM[tag]["L2S5"]
    wet = np.maximum(a, b) >= 0.05
    r = R.raster(G["xy"], np.where(wet, b - a, np.nan), WIN, 8.0)
    r[np.abs(r) < 0.01] = np.nan
    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    ax.imshow(R.raster(G["xy"], np.where(wet, 1.0, np.nan), WIN, 8.0), extent=WIN, cmap="Greys", vmin=0, vmax=4, zorder=1)
    im = ax.imshow(r, extent=WIN, cmap="RdBu_r", norm=TwoSlopeNorm(vcenter=0.0, vmin=-0.30, vmax=0.30), zorder=2)
    _ponds(ax, 0.2); R._hs(ax, H); R._frame(ax)
    sd = M["runs"][tag]["side"]["vs_L2P"]
    ax.text(0.01, 0.99, f"Deeper by ≥ 5 cm: {sd['deeper_ge005_ha']:.2f} ha   (largest increase anywhere: +{sd['max_increase_m'] * 100:.0f} cm)\n"
                        f"Shallower by ≥ 5 cm: {sd['shallower_ge005_ha']:.2f} ha",
            transform=ax.transAxes, va="top", fontsize=7.6, bbox=dict(fc="white", ec="#999", lw=0.5, alpha=0.95), zorder=8)
    cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02, extend="both")
    cb.set_label("Change in maximum depth, v5 − L2+planned (m)\nblue = shallower, red = deeper", fontsize=7.5); cb.ax.tick_params(labelsize=7)
    rain = "61 mm / 2 h" if tag == "M61" else "96 mm / 2 h"
    ax.set_title(f"{rain}: change in maximum depth (grey = wet in either case, change < 1 cm)", fontsize=8.6, loc="left")
    fig.tight_layout(); p = OUT / f"fig{n}_depth_change_{tag}.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig); return p


def fig_series(M, H, tag, n, plt, ymax):
    sts = RUNS[tag]
    fig, axs = plt.subplots(2, 4, figsize=(7.6, 3.9), sharex=True, sharey=True)
    for ax, k in zip(axs.ravel(), KS):
        for st in sts:
            r = M["runs"][tag]["hotspots"][k][st]
            ax.plot(r["series_t"], r["series_depth"], ls=":" if st == "L2S4" else "-", lw=1.8 if st == "L2S5" else 1.2, color=COLOR[st], label=LABEL[st])
        ax.axhline(0.30, color="#b23a2e", lw=0.6, ls="--"); ax.axhline(0.15, color="#999", lw=0.5, ls=":")
        ax.axvspan(0, 2, color="#e8f0f7", zorder=0)
        ax.set_title(k + ("  ★" if H[k]["crit"] == "Very High" else ""), fontsize=8, loc="left", pad=2)
        ax.tick_params(labelsize=7); ax.set_xlim(0, 6); ax.set_ylim(0, ymax)
    for ax in axs[:, 0]:
        ax.set_ylabel("Max depth in 200 m ring (m)", fontsize=7)
    for ax in axs[1]:
        ax.set_xlabel("Hours from start of rain", fontsize=7)
    h, l = axs[0, 0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=3, fontsize=7.4, frameon=False, bbox_to_anchor=(0.5, -0.02))
    rain = "61 mm / 2 h" if tag == "M61" else "96 mm / 2 h"
    fig.text(0.01, 0.995, rain, ha="left", va="top", fontsize=8.5, fontweight="bold")
    fig.text(0.99, 0.995, "shaded = rain (0–2 h);  red dashed = 0.30 m;  grey dotted = 0.15 m;  ★ = very high criticality",
             ha="right", va="top", fontsize=6.6, color="#555")
    fig.tight_layout(rect=(0, 0.06, 1, 0.96)); p = OUT / f"fig{n}_depth_over_time_{tag}.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig); return p


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--check", action="store_true"); a = ap.parse_args(argv)
    H = R.hotspots(); U = R.catch_union()
    M, G, HM, inp = metrics(H, U)
    OUT.mkdir(parents=True, exist_ok=True)
    slim = json.loads(json.dumps(M))
    for T in slim["runs"].values():
        for k in KS:
            for r in T["hotspots"][k].values():
                r.pop("series_t"); r.pop("series_depth")
    (OUT / "metrics.json").write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(slim, ensure_ascii=False, indent=1))
    if a.check:
        return 0
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    R._style(plt)
    for f in (lambda: fig_measures(G, H, inp, plt), lambda: fig_change(G, H, HM, M, "M61", 2, plt), lambda: fig_series(M, H, "M61", 3, plt, 0.55),
              lambda: fig_change(G, H, HM, M, "M96", 4, plt), lambda: fig_series(M, H, "M96", 5, plt, 0.85)):
        print("写出", f())
    (OUT / "来源.txt").write_text("本目录的图与 metrics.json 由 tools/c1_v5_report.py 生成；数据见脚本头注释。\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
