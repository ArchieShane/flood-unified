# -*- coding: utf-8 -*-
"""Generate pit record map — DTM + pit outline for filing (user 10-28 'export a backup image combining this location with the map')."""
import argparse, datetime, json, pathlib, sys
import matplotlib.pyplot as plt, numpy as np, rasterio
from rasterio.windows import from_bounds
from shapely.geometry import shape

HERE = pathlib.Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import rb01_build as RB

OUT, DTM = ROOT / "out" / "bldg", {"RDA": RB.DTM}
PITS = {"PIT-RDA-01": OUT / "pit_230387.geojson"}


def plot(pid, gj_path, dtm_path, out_png):
    ft = json.loads(gj_path.read_text(encoding="utf-8"))["features"][0]
    pr, geo = ft["properties"], shape(ft["geometry"])
    cx, cy = geo.centroid.x, geo.centroid.y
    with rasterio.open(dtm_path) as ds:
        buf = 600.0; b = (cx - buf, cy - buf, cx + buf, cy + buf)
        win = from_bounds(*b, ds.transform).round_offsets().round_lengths()
        z = ds.read(1, window=win).astype(float); extent = ds.window_bounds(win)
    z[z == 0] = np.nan
    fig, ax = plt.subplots(1, 1, figsize=(11, 9), dpi=120)
    im = ax.imshow(z, extent=extent, cmap="terrain", origin="upper", interpolation="bilinear")
    from matplotlib.patches import Polygon as MPoly
    pts = np.array(geo.exterior.coords)
    ax.add_patch(MPoly(pts, closed=True, fill=False, edgecolor="red", linewidth=3.0, label="pit outline"))
    ax.plot(cx, cy, "ro", ms=10, label=f"center ({int(cx)}, {int(cy)})")
    ring = pr.get("ring_median_m", "TBD")
    if isinstance(ring, (int, float)):
        ax.text(cx + 30, cy + 30, f"{ring} m", color="white", fontsize=11, weight="bold",
                bbox=dict(facecolor="black", alpha=0.8, boxstyle="round,pad=0.4"))
    ax.set_xlabel("UTM 40N Easting (m)", fontsize=11); ax.set_ylabel("UTM 40N Northing (m)", fontsize=11)
    ax.set_title(f"RD-A Pit Record ({pid})", fontsize=13, weight="bold"); ax.legend(loc="upper left", fontsize=10)
    fig.colorbar(im, ax=ax, label="elevation (m)", shrink=0.85)
    area_m2, bot, vol = pr.get("area_m2", int(geo.area)), pr.get("pit_bottom_m", "TBD"), pr.get("vol_m3", "TBD")
    info = f"{pid} | center ({int(cx)}, {int(cy)}) | {area_m2} m² | {vol} m³ | bottom {bot} m, ring {ring} m"
    fig.text(0.5, 0.96, info, ha="center", fontsize=11, weight="bold")
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fig.text(0.5, 0.01, f"generated: {ts} | tools/bldg_pit_record_map.py", ha="center", fontsize=9, color="gray")
    plt.tight_layout(rect=[0, 0.02, 1, 0.95]); fig.savefig(out_png, dpi=120, bbox_inches="tight"); plt.close(fig)
    print(f"→ {out_png}")


def main():
    sys.stdout.reconfigure(encoding="utf-8"); ap = argparse.ArgumentParser()
    ap.add_argument("--pid", required=True, choices=list(PITS.keys())); a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True); plot(a.pid, PITS[a.pid], DTM["RDA"], OUT / f"{a.pid}_record.png")


if __name__ == "__main__":
    main()
