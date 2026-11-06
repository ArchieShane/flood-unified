# -*- coding: utf-8 -*-
"""RB-01 结果图：各态全程最大水深 + L2+suggested v5 相对 L2+planned 的差值。产物 out/rb01_figs_{tag}/*.png（H60 的老图在 out/rb01_figs/）
用法：python tools/rb01_figs.py [--tag H61]"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from rb01_build import HS  # noqa: E402
from rb01_post import NAMES  # noqa: E402
from workdir import WORK  # noqa: E402


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", default="H61"); a = ap.parse_args(argv)
    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    name = f"RB01_{a.tag}"; C2D = WORK / "c2d"; out = ROOT / "out" / f"rb01_figs_{a.tag.lower()}"; out.mkdir(parents=True, exist_ok=True)
    from rb01_build import scen_label
    scen = scen_label(a.tag)
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640); H = H[H.hotspot_id.isin(HS)]
    meta = json.loads((C2D / f"pilot_{name}_L2S5.json").read_text(encoding="utf-8"))
    dom = np.array(meta["domain_polygon"] + [meta["domain_polygon"][0]])
    lev = [0.10, 0.20, 0.30, 0.50, 0.75, 1.0, 2.0]
    cmap = ListedColormap(["#c6dbef", "#9ecae1", "#6baed6", "#3182bd", "#08519c", "#08306b"]); norm = BoundaryNorm(lev, cmap.N)

    def base(ax):
        ax.plot(dom[:, 0], dom[:, 1], color="#334155", lw=0.8)
        ax.scatter(H.geometry.x, H.geometry.y, s=36, marker="*", color="#dc2626", zorder=5)
        for r in H.itertuples():
            ax.annotate(r.hotspot_id, (r.geometry.x, r.geometry.y), xytext=(4, 4), textcoords="offset points", fontsize=7, color="#7f1d1d")
        ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])

    Z = {s: np.load(C2D / f"{name}_{s}_2d.npz") for s in ("L1", "L2", "L2P", "L2S5")}
    xy = Z["L1"]["xy"]
    for s, z in Z.items():
        fig, ax = plt.subplots(figsize=(7.5, 6.2), dpi=150); base(ax)
        hm = z["hmax"].astype(float); m = hm >= 0.10
        sc = ax.scatter(xy[m, 0], xy[m, 1], c=hm[m], s=0.6, marker="s", cmap=cmap, norm=norm, linewidths=0)
        cb = fig.colorbar(sc, ax=ax, shrink=0.7); cb.set_label("全程最大水深 m")
        ax.set_title(f"RB-01 · {scen} · {NAMES[s]}", fontsize=10)
        fig.tight_layout(); fig.savefig(out / f"{s}_max.png"); plt.close(fig)
    d = Z["L2S5"]["hmax"].astype(float) - Z["L2P"]["hmax"].astype(float); m = np.abs(d) >= 0.05
    fig, ax = plt.subplots(figsize=(7.5, 6.2), dpi=150); base(ax)
    sc = ax.scatter(xy[m, 0], xy[m, 1], c=d[m], s=0.6, marker="s", cmap="RdBu_r", vmin=-0.5, vmax=0.5, linewidths=0)
    v5 = json.loads((ROOT / "out" / f"rb01_L2S5_inputs_{a.tag}.json").read_text(encoding="utf-8"))
    for p in v5["ponds"]:
        ax.scatter(*p["xy"], s=14, marker="s", facecolor="none", edgecolor="#15803d", lw=1)
    cb = fig.colorbar(sc, ax=ax, shrink=0.7); cb.set_label("v5 − L2+planned 最大水深差 m（|Δ| ≥ 5 cm）")
    ax.set_title("L2+suggested v5 相对 L2+planned（绿框 = v5 新塘）", fontsize=10)
    fig.tight_layout(); fig.savefig(out / "diff_L2S5_L2P.png"); plt.close(fig)
    print(f"→ {out}")


if __name__ == "__main__":
    main()
