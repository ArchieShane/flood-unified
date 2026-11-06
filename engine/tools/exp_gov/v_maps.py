"""RD-A 治理前后验证：出图（最大水深三联图 + 治理后减治理前差值图 + 地面存水过程线）。

用法（普通 Python，需 geopandas / matplotlib）：
  python tools/exp_gov/v_maps.py --storm D61 --dir-rg D:/swbrain_work/swmm/c2d/archive_vg_v3 --out out/exp_gov/verify/figs_v3
A 模型取 $SWBRAIN_SWMM_WORK/c2d；R / G 取 --dir-rg（默认同 A）。
"""
import argparse
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))
NAMES = {"A": "① AECOM 原样", "R": "② 治理前（DMT 原样）", "G": "③ 治理后", "N": "④ 只治理 DMT（对照）", "M": "⑤ 治理后管网 + AECOM 产流（对照）"}
STORMS = {"D61": "61.34 mm 设计暴雨", "E26": "72 mm（2026 年雨）", "E24": "82.2 mm（2024 年雨）"}


def load(path):
    z = np.load(path)
    return {k: z[k] for k in ("xy", "area", "hmax", "frames", "t_h")}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--storm", required=True)
    ap.add_argument("--dir-rg", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap, TwoSlopeNorm
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False

    c2d = WORK / "c2d"
    drg = Path(a.dir_rg) if a.dir_rg else c2d
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    D = {"A": load(c2d / f"VG_{a.storm}_A_2d.npz"),
         "R": load(drg / f"VG_{a.storm}_R_2d.npz"),
         "G": load(drg / f"VG_{a.storm}_G_2d.npz")}
    dom = gpd.read_file(ROOT / "out/dataqc/RD-A/input/rda_domain.geojson").to_crs(32640)
    H = gpd.read_file(ROOT / "console/data/hotspots.geojson").to_crs(32640)
    H = H[H.intersects(dom.geometry.iloc[0].buffer(200))]
    xy = D["G"]["xy"]
    same = all(len(d["xy"]) == len(xy) and np.allclose(d["xy"], xy) for d in D.values())
    print("三个模型网格相同：", same)

    # 1) 最大水深三联图
    bounds = [0.05, 0.15, 0.30, 0.50, 1.0, 10]
    cmap = ListedColormap(["#c6dbef", "#6baed6", "#2171b5", "#08519c", "#08306b"])
    norm = BoundaryNorm(bounds, cmap.N)
    fig, axs = plt.subplots(1, 3, figsize=(18, 5.6), constrained_layout=True)
    for ax, m in zip(axs, "ARG"):
        d = D[m]; k = d["hmax"] >= 0.05
        dom.boundary.plot(ax=ax, color="#555", lw=0.8)
        sc = ax.scatter(d["xy"][k, 0], d["xy"][k, 1], c=d["hmax"][k], s=2.2, cmap=cmap, norm=norm, linewidths=0)
        H.plot(ax=ax, marker="o", facecolor="none", edgecolor="#d62728", markersize=60, lw=1.2)
        ha015 = d["area"][d["hmax"] >= 0.15].sum() / 1e4; ha030 = d["area"][d["hmax"] >= 0.30].sum() / 1e4
        ax.set_title(f"{NAMES[m]}\n≥0.15 m {ha015:.1f} ha · ≥0.30 m {ha030:.1f} ha", fontsize=12)
        ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    cb = fig.colorbar(sc, ax=axs, shrink=0.7, ticks=bounds[:-1])
    cb.set_label("最大水深 m")
    fig.suptitle(f"RD-A 最大水深对比 — {STORMS.get(a.storm, a.storm)} / 1 h（红圈 = 客户热点）", fontsize=14)
    p1 = out / f"{a.storm}_最大水深三联图.png"; fig.savefig(p1, dpi=130); plt.close(fig)

    # 2) 治理后减治理前（同一网格时才画）
    if same:
        diff = D["G"]["hmax"] - D["R"]["hmax"]
        k = (np.abs(diff) >= 0.05) & ((D["G"]["hmax"] >= 0.05) | (D["R"]["hmax"] >= 0.05))
        fig, ax = plt.subplots(figsize=(9, 8), constrained_layout=True)
        dom.boundary.plot(ax=ax, color="#555", lw=0.8)
        sc = ax.scatter(xy[k, 0], xy[k, 1], c=diff[k], s=2.2, cmap="RdBu_r", norm=TwoSlopeNorm(0, -1, 1), linewidths=0)
        H.plot(ax=ax, marker="o", facecolor="none", edgecolor="k", markersize=60, lw=1)
        ar = D["G"]["area"]
        dn = ar[diff <= -0.05].sum() / 1e4; up = ar[diff >= 0.05].sum() / 1e4
        ax.set_title(f"治理后 − 治理前 最大水深差（{STORMS.get(a.storm, a.storm)}）\n"
                     f"蓝 = 治理后更浅（≥5 cm）{dn:.1f} ha · 红 = 治理后更深 {up:.1f} ha", fontsize=12)
        ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
        fig.colorbar(sc, ax=ax, shrink=0.7, label="水深差 m")
        p2 = out / f"{a.storm}_治理后减治理前.png"; fig.savefig(p2, dpi=130); plt.close(fig)
        print(f"更浅 {dn:.2f} ha，更深 {up:.2f} ha")

    # 3) 地面存水过程线
    fig, ax = plt.subplots(figsize=(9, 4.5), constrained_layout=True)
    fn = (drg / f"VG_{a.storm}_N_2d.npz")
    if fn.exists():
        D["N"] = load(fn)                                  # 对照：只治理 DMT、不融合 AECOM
    fm = (drg / f"VG_{a.storm}_M_2d.npz")
    if fm.exists():
        D["M"] = load(fm)                                  # 对照：治理后管网 + AECOM 产流
    for m, c, ls in zip("ARGNM", ["#7f7f7f", "#d62728", "#1f77b4", "#ff7f0e", "#2ca02c"], ["-", "-", "-", "-", "--"]):
        if m not in D:
            continue
        d = D[m]; v = (d["frames"].astype(float) * d["area"]).sum(1) / 1e4
        ax.plot(d["t_h"], v, color=c, lw=2, ls=ls, label=NAMES[m])
    ax.axvspan(0, 1, color="#eee", label="降雨 1 h")
    ax.set_xlabel("时间 h"); ax.set_ylabel("地面存水 万 m³"); ax.grid(alpha=0.3); ax.legend()
    ax.set_title(f"地面存水随时间变化 — {STORMS.get(a.storm, a.storm)}")
    p3 = out / f"{a.storm}_地面存水过程.png"; fig.savefig(p3, dpi=130); plt.close(fig)
    print("出图：", p1, p3)

    # 4) ⑤ − ①：产流相同时，治理后管网和 AECOM 管网的差别
    if "M" in D and len(D["M"]["xy"]) == len(D["A"]["xy"]) and np.allclose(D["M"]["xy"], D["A"]["xy"]):
        diff = D["M"]["hmax"] - D["A"]["hmax"]
        k = (np.abs(diff) >= 0.05) & ((D["M"]["hmax"] >= 0.05) | (D["A"]["hmax"] >= 0.05))
        fig, ax = plt.subplots(figsize=(9, 8), constrained_layout=True)
        dom.boundary.plot(ax=ax, color="#555", lw=0.8)
        sc = ax.scatter(D["A"]["xy"][k, 0], D["A"]["xy"][k, 1], c=diff[k], s=2.2, cmap="RdBu_r", norm=TwoSlopeNorm(0, -1, 1), linewidths=0)
        H.plot(ax=ax, marker="o", facecolor="none", edgecolor="k", markersize=60, lw=1)
        ar = D["A"]["area"]
        dn = ar[diff <= -0.05].sum() / 1e4; up = ar[diff >= 0.05].sum() / 1e4
        ax.set_title(f"⑤ 治理后管网 + AECOM 产流 − ① AECOM 最大水深差（{STORMS.get(a.storm, a.storm)}）\n"
                     f"蓝 = ⑤ 更浅（≥5 cm）{dn:.1f} ha · 红 = ⑤ 更深 {up:.1f} ha", fontsize=12)
        ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
        fig.colorbar(sc, ax=ax, shrink=0.7, label="水深差 m")
        p4 = out / f"{a.storm}_对照5减AECOM.png"; fig.savefig(p4, dpi=130); plt.close(fig)
        print(f"⑤ − ①：更浅 {dn:.2f} ha，更深 {up:.2f} ha →", p4)


if __name__ == "__main__":
    main()
