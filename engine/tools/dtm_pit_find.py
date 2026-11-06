# -*- coding: utf-8 -*-
"""直接从 DTM 找深坑 - 不用形态学重建，用局部比较。

用法：python tools/dtm_pit_find.py --domain RDA --depth 1.0
产物：out/bldg/pits_found_RDA.json
"""
import argparse, json, pathlib, sys
import numpy as np, rasterio
from rasterio.windows import from_bounds
from scipy import ndimage
from shapely.geometry import shape

HERE = pathlib.Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import rb01_build as RB

OUT = ROOT / "out" / "bldg"
DTM = {"RDA": pathlib.Path(RB.DTM)}
DOMAIN = {"RDA": ROOT / "out" / "dataqc" / "RD-A" / "input" / "rda_domain.geojson"}


def find_pits(dtm_path, domain_poly, depth_th=1.0, radius_m=20.0):
    """找出所有比周边低 depth_th 的像元"""
    with rasterio.open(dtm_path) as ds:
        b = domain_poly.bounds
        win = from_bounds(*b, ds.transform).round_offsets().round_lengths()
        print(f"  读取 DTM: {win.height} x {win.width} = {win.height * win.width / 1e6:.1f} M 像元", flush=True)
        z = ds.read(1, window=win).astype(float)
        wb = ds.window_bounds(win); res = ds.res[0]
        tf = rasterio.transform.from_bounds(*wb, z.shape[1], z.shape[0])

    z[z == 0] = np.nan
    valid = ~np.isnan(z)
    print(f"  有效像元: {valid.sum()}", flush=True)

    # 每个像元和周边圆盘（半径 radius_m）的中位数比较
    rad_pix = int(radius_m / res)
    print(f"  计算局部中位数（半径 {rad_pix} 像元 = {rad_pix * res:.0f} m）...", flush=True)
    from scipy.ndimage import generic_filter
    def local_median(window):
        w = window[~np.isnan(window)]
        return np.median(w) if len(w) > 0 else np.nan
    # generic_filter 太慢，改用简单的 uniform_filter 近似
    # 用 percentile_filter 的 50% 分位数
    from scipy.ndimage import percentile_filter
    z_fill = z.copy(); z_fill[~valid] = np.nan
    z_local = percentile_filter(z_fill, 50, size=rad_pix * 2 + 1, mode='nearest')

    # 深度 = 周边中位数 - 本点
    depth = z_local - z
    depth[~valid] = 0
    print(f"  depth stats: max {np.nanmax(depth):.2f}, p99 {np.nanpercentile(depth, 99):.2f}, p95 {np.nanpercentile(depth, 95):.2f}", flush=True)

    # 标记深坑像元
    pit_mask = (depth >= depth_th) & valid
    print(f"  深度 >= {depth_th} m: {pit_mask.sum()} 像元", flush=True)

    # 连通域
    labels, n = ndimage.label(pit_mask, structure=np.ones((3, 3), int))
    print(f"  连通域: {n} 个", flush=True)

    pits = []
    for i in range(1, n + 1):
        sel = labels == i
        area_m2 = int(sel.sum() * res * res)
        if area_m2 < 100:
            continue
        rows, cols = np.where(sel)
        cx = float(wb[0] + cols.mean() * res)
        cy = float(wb[3] - rows.mean() * res)
        pit_bot = float(z[sel].min())
        ring_med = float(z_local[rows[len(rows) // 2], cols[len(cols) // 2]])
        depth_m = round(ring_med - pit_bot, 2)
        pits.append({"id": f"PIT-{len(pits) + 1:02d}", "x": int(cx), "y": int(cy),
                     "area_m2": area_m2, "pit_bottom_m": round(pit_bot, 2),
                     "ring_median_m": round(ring_med, 2), "depth_m": depth_m})
        print(f"  {pits[-1]['id']}: ({cx:.0f}, {cy:.0f}), {area_m2} m², 坑底 {pit_bot:.2f} m, 周边 {ring_med:.2f} m, 深 {depth_m} m", flush=True)

    return pits


def main():
    sys.stdout.reconfigure(encoding="utf-8"); ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True, choices=list(DOMAIN.keys()))
    ap.add_argument("--depth", type=float, default=1.0)
    ap.add_argument("--radius", type=float, default=20.0, help="周边比较半径（m）")
    a = ap.parse_args(); OUT.mkdir(parents=True, exist_ok=True)
    print(f"查找 {a.domain} 深坑（深度 >= {a.depth} m, 周边半径 {a.radius} m）", flush=True)
    D = shape(json.loads(pathlib.Path(DOMAIN[a.domain]).read_text(encoding="utf-8"))["features"][0]["geometry"])
    print(f"  计算域 {D.area / 1e6:.2f} km2", flush=True)
    pits = find_pits(DTM[a.domain], D, a.depth, a.radius)
    fout = OUT / f"pits_found_{a.domain}.json"
    fout.write_text(json.dumps({"domain": a.domain, "n_pits": len(pits), "pits": pits}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {fout} ({len(pits)} pits)", flush=True)


if __name__ == "__main__":
    main()
