# -*- coding: utf-8 -*-
"""深坑组备案图：DTM + 坑轮廓 + 标注"""
import json, pathlib, sys
import matplotlib.pyplot as plt, numpy as np, rasterio
from rasterio.windows import from_bounds

HERE = pathlib.Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import rb01_build as RB

pits_data = json.loads((ROOT / "out" / "bldg" / "pits_found_RDA.json").read_text(encoding="utf-8"))
pits_of_interest = [26, 31, 32, 34, 35]  # PIT-26 = RDA-01 已填, 31/32/34/35 = 深坑组
pits = [p for p in pits_data["pits"] if int(p["id"].split("-")[1]) in pits_of_interest]

# 找包围盒
xs = [p["x"] for p in pits]; ys = [p["y"] for p in pits]
cx, cy = np.mean(xs), np.mean(ys)
buf = 400

with rasterio.open(RB.DTM) as ds:
    win = from_bounds(cx - buf, cy - buf, cx + buf, cy + buf, ds.transform).round_offsets().round_lengths()
    z = ds.read(1, window=win).astype(float)
    wb = ds.window_bounds(win)

z[z == 0] = np.nan
fig, ax = plt.subplots(figsize=(12, 10), dpi=120)
im = ax.imshow(z, extent=(wb[0], wb[2], wb[1], wb[3]), cmap="terrain", origin="upper")
for p in pits:
    color = "red" if p["id"] == "PIT-26" else "orange"
    label = f"{p['id']}\n{p['depth_m']} m" if p['depth_m'] else f"{p['id']}"
    ax.plot(p["x"], p["y"], "o", ms=12, color=color, mec="black", mew=2)
    ax.text(p["x"] + 20, p["y"], label, fontsize=10, weight="bold", color="white",
            bbox=dict(facecolor="black", alpha=0.7, boxstyle="round,pad=0.3"))
    if p["id"] == "PIT-26":
        ax.annotate("已填平 (PIT-RDA-01)", (p["x"], p["y"]), xytext=(80, -60), textcoords="offset points",
                    fontsize=9, color="red", weight="bold", arrowprops=dict(arrowstyle="->", color="red", lw=2))

ax.set_xlabel("UTM 40N Easting (m)", fontsize=11)
ax.set_ylabel("UTM 40N Northing (m)", fontsize=11)
ax.set_title("RD-A 深坑分布（≥ 1 m）：PIT-26 已填，PIT-31/32/34/35 待定", fontsize=13, weight="bold")
fig.colorbar(im, ax=ax, label="高程 (m)", shrink=0.8)
ax.text(0.02, 0.98, f"中心: ({int(cx)}, {int(cy)})\nPIT-32: 深 8.56 m（最深），距在建建筑 1 m\n判为施工基坑",
        transform=ax.transAxes, va="top", fontsize=9, color="white",
        bbox=dict(facecolor="black", alpha=0.7, boxstyle="round,pad=0.4"))
plt.tight_layout()
fout = ROOT / "out" / "bldg" / "pits_group_record.png"
fig.savefig(fout, dpi=130, bbox_inches="tight")
print(f"-> {fout}")
