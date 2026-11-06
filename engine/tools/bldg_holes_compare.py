# -*- coding: utf-8 -*-
"""建筑处理三态对比（同雨 A61D1、同一维、同 2 h 冒烟时长）：L1N 不处理 / L1 高糙率 n 0.5 / L1H 挖洞。

「楼上」= 单元形心落在挖洞多边形（bldg_holes_prep.py 的 holes）内；L1H 没有这种单元。
积水阈值 0.15 m（同 swbrain/ponding.py 缺省）。
用法：python tools/bldg_holes_compare.py   产物：out/bldg/compare_RDA.json、compare_RDA.png
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

NAME = "RDAG_A61D1"
STATES = (("L1N", "不处理"), ("L1", "高糙率 n 0.5"), ("L1H", "挖洞"))
TH = 0.15
OUT = ROOT / "out" / "bldg"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import rasterio
    from matplotlib.path import Path as MPath
    c2d = WORK / "c2d"
    holes = json.loads((c2d / f"pilot_{NAME}_L1H.json").read_text(encoding="utf-8"))["building_holes"]["holes"]
    with rasterio.open(c2d / "surf_RDAG.tif") as ds:
        surf = ds
        res = {}
        for st, lab in STATES:
            z = np.load(c2d / f"{NAME}_{st}_2d.npz")
            t = json.loads((c2d / f"twoway_{NAME}_{st}.json").read_text(encoding="utf-8"))
            xy, a, h = z["xy"].astype(float), z["area"].astype(float), z["hmax"].astype(float)
            onb = np.zeros(len(xy), bool)
            for p in holes:
                pp = np.array(p); m = (xy[:, 0] >= pp[:, 0].min()) & (xy[:, 0] <= pp[:, 0].max()) & (xy[:, 1] >= pp[:, 1].min()) & (xy[:, 1] <= pp[:, 1].max())
                if m.any():
                    onb[np.flatnonzero(m)[MPath(pp).contains_points(xy[m])]] = True
            cls = np.array([v[0] for v in surf.sample([tuple(q) for q in xy])], int)
            road = (cls == 1) & ~onb
            wet = h >= TH
            vol = (z["frames"].astype(float) * a).sum(1)
            acc = t["acc_m3"]
            res[st] = {"方法": lab, "单元数": int(len(xy)), "耗时_s": round(t["wall_s"]),
                       "积水面积_ha": round(a[wet].sum() / 1e4, 2),
                       "其中楼上_ha": round(a[wet & onb].sum() / 1e4, 2),
                       "楼外地面_ha": round(a[wet & ~onb].sum() / 1e4, 2),
                       "路面积水_ha": round(a[wet & road].sum() / 1e4, 2),
                       "路面积水单元平均最大水深_m": round(float(h[wet & road].mean()), 3) if (wet & road).any() else 0.0,
                       "楼外最大水深_m": round(float(h[~onb].max()), 2),
                       "楼外积水单元最大水深p95_m": round(float(np.percentile(h[wet & ~onb], 95)), 2) if (wet & ~onb).any() else 0.0,
                       "二维峰值水量_m3": round(float(vol.max())),
                       "1D冒到2D_m3": round(acc["1to2"]), "2D回1D_m3": round(acc["2to1"]),
                       "边界出流_m3": round(t["boundary_out_m3"]), "2h末二维留存_m3": round(t["final_vol_m3"])}
            res[st]["_xy"], res[st]["_h"], res[st]["_onb"] = xy, h, onb
    OUT.mkdir(parents=True, exist_ok=True)
    clean = {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in res.items()}
    (OUT / "compare_RDA.json").write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    keys = [k for k in clean["L1N"]]
    print("| 指标 | " + " | ".join(f"{st} {clean[st]['方法']}" for st, _ in STATES) + " |")
    print("|---|" + "---|" * len(STATES))
    for k in keys[1:]:
        print(f"| {k} | " + " | ".join(str(clean[st][k]) for st, _ in STATES) + " |")
    # 图：积水最多的 600 m 窗口，三态并排
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MP
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    r = res["L1N"]; w = r["_h"] >= TH
    W = 300.0
    cx, cy = r["_xy"][w][np.argmax([((np.abs(r["_xy"][w] - p) < W).all(1)).sum() for p in r["_xy"][w][::25]]) * 25]
    fig, ax = plt.subplots(1, 3, figsize=(18, 6.4))
    for k, (st, lab) in enumerate(STATES):
        q = res[st]; s = (np.abs(q["_xy"] - [cx, cy]) < W + 20).all(1) & (q["_h"] >= TH)
        sc = ax[k].scatter(q["_xy"][s, 0], q["_xy"][s, 1], c=q["_h"][s], s=5, cmap="Blues", vmin=TH, vmax=0.8)
        for p in holes:
            pp = np.array(p)
            if (np.abs(pp.mean(0) - [cx, cy]) < W + 60).all():
                ax[k].add_patch(MP(pp, fill=False, ec="#c0392b", lw=0.7))
        ax[k].set_xlim(cx - W, cx + W); ax[k].set_ylim(cy - W, cy + W); ax[k].set_aspect("equal")
        ax[k].set_title(f"{st} {lab}：积水 {clean[st]['积水面积_ha']} ha，楼上 {clean[st]['其中楼上_ha']} ha"); ax[k].set_xticks([]); ax[k].set_yticks([])
    fig.colorbar(sc, ax=ax, shrink=0.8, label="最大水深 m（≥0.15）")
    fig.suptitle("RD-A A61D1 L1 · 2 h · 红线 = Makani 建筑（挖洞轮廓）")
    fig.savefig(OUT / "compare_RDA.png", dpi=130, bbox_inches="tight")
    print(f"→ {OUT / 'compare_RDA.json'}、compare_RDA.png（窗口中心 {cx:.0f}, {cy:.0f}）")


if __name__ == "__main__":
    main()
