# -*- coding: utf-8 -*-
"""全域扫描地形深坑 - 简化版（避免段错误）：只输出坑的位置、大小、深度，不查 Makani。

用法：python tools/dtm_pit_scan_simple.py --domain RDA
产物：out/bldg/pits_scan_RDA_simple.json（坑清单）
"""
import argparse, json, pathlib, sys
import numpy as np, rasterio
from rasterio.windows import from_bounds
from scipy import ndimage
from shapely.geometry import shape
from skimage import morphology

HERE = pathlib.Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import rb01_build as RB

OUT = ROOT / "out" / "bldg"
DTM = {"RDA": pathlib.Path(RB.DTM)}
DOMAIN = {"RDA": ROOT / "out" / "dataqc" / "RD-A" / "input" / "rda_domain.geojson"}
EXCLUDE = {"RDA": ROOT / "out" / "l2opt" / "RDA_96C" / "exclude_OPTCS.geojson"}
REGISTERED = {"RDA": [ROOT / "out" / "bldg" / "pit_230387.geojson"]}


def scan_simple(dtm_path, domain_poly, depth_th=1.0, vol_th=100.0):
    """只输出坑的基本信息，不做复杂几何处理"""
    with rasterio.open(dtm_path) as ds:
        b = domain_poly.bounds
        win = from_bounds(*b, ds.transform).round_offsets().round_lengths()
        print(f"  读取 DTM 窗口：{win.height} × {win.width} = {win.height * win.width / 1e6:.1f} M 像元", flush=True)
        z = ds.read(1, window=win).astype(float)
        wb = ds.window_bounds(win); res = ds.res[0]
        tf = rasterio.transform.from_bounds(*wb, z.shape[1], z.shape[0])

    z[z == 0] = np.nan
    valid = ~np.isnan(z)
    print(f"  有效像元 {valid.sum()} / {valid.size}", flush=True)
    print(f"  填洼重建（先填充 NaN 为最大值）...", flush=True)
    # 形态学重建不能处理 NaN，先用一个大值填充，重建后再恢复
    z_work = z.copy()
    z_work[~valid] = z[valid].max() + 100.0
    seed = z_work + 1e-6
    print(f"  z_work range: {z_work.min():.2f} to {z_work.max():.2f}", flush=True)
    filled = morphology.reconstruction(seed, z_work, method="erosion").astype(z.dtype)
    filled[~valid] = np.nan
    depth = filled - z; depth[~valid] = 0.0

    print(f"  连通域标记...", flush=True)
    pit_mask = (depth >= depth_th) & valid
    labels, n = ndimage.label(pit_mask, structure=np.ones((3, 3), int))
    print(f"  找到 {n} 个连通域", flush=True)

    pits = []
    for i in range(1, n + 1):
        sel = labels == i
        vol = float((depth[sel] * res * res).sum())
        if vol < vol_th:
            continue
        rows, cols = np.where(sel)
        area_m2 = int(sel.sum() * res * res)
        cx = float(wb[0] + cols.mean() * res)
        cy = float(wb[3] - rows.mean() * res)
        pit_bot = float(z[sel].min())
        ring_z = []
        for r in range(max(0, rows.min() - 4), min(z.shape[0], rows.max() + 5)):
            for c in range(max(0, cols.min() - 4), min(z.shape[1], cols.max() + 5)):
                if not sel[r, c] and valid[r, c]:
                    ring_z.append(z[r, c])
        ring_med = float(np.median(ring_z)) if ring_z else float('nan')
        pits.append({"id": f"PIT-RDA-{len(pits) + 1:02d}", "x": int(cx), "y": int(cy),
                     "area_m2": area_m2, "vol_m3": int(vol), "pit_bottom_m": round(pit_bot, 2),
                     "ring_median_m": round(ring_med, 2), "depth_m": round(ring_med - pit_bot, 2) if not np.isnan(ring_med) else None})
        print(f"  {pits[-1]['id']}: ({cx:.0f}, {cy:.0f}), {area_m2} m², {int(vol)} m³, 深 {pits[-1]['depth_m']} m", flush=True)

    return pits


def main():
    sys.stdout.reconfigure(encoding="utf-8"); ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True, choices=list(DOMAIN.keys()))
    ap.add_argument("--depth", type=float, default=1.0); ap.add_argument("--vol", type=float, default=100.0)
    a = ap.parse_args(); OUT.mkdir(parents=True, exist_ok=True)
    print(f"扫描 {a.domain} 地形深坑（≥ {a.depth} m、≥ {a.vol} m³）", flush=True)
    D = shape(json.loads(pathlib.Path(DOMAIN[a.domain]).read_text(encoding="utf-8"))["features"][0]["geometry"])
    print(f"  计算域 {D.area / 1e6:.2f} km²", flush=True)
    pits = scan_simple(DTM[a.domain], D, a.depth, a.vol)
    fout = OUT / f"pits_scan_{a.domain}_simple.json"
    fout.write_text(json.dumps({"domain": a.domain, "n_pits": len(pits), "pits": pits}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {fout}（{len(pits)} 个坑）", flush=True)


if __name__ == "__main__":
    main()
