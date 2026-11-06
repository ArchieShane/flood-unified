# -*- coding: utf-8 -*-
"""把 couple2way_v2 的二维水深帧（npz）写成 GeoTIFF：每态一个多波段逐帧水深 + 一张全程最大水深。

为什么（2026-09-28）：客户要标准格式的二维结果；npz 要 Python 才能读。GeoTIFF 任何 GIS 都能开。
做法：三角单元形心水深 → 按网格尺寸落格、每格取最大 → UTM 40N（EPSG:32640）浮点栅格，0 = 干（< 0.02 m 也记 0），nodata = 计算域外。
每个波段的描述是时刻（h），另写同名 .json 列出各波段时刻。

--cell 设栅格尺寸（缺省 = 计算网格 res_m）：不比计算网细时落格取最大；比计算网细时改为格心线性插值（否则落格会留空洞）。

用法：python tools/npz_to_tif.py City2D L1 L2 [--cell 栅格m] [--out 目录]
产物：<out>/<名>_<态>_depth_frames.tif（多波段逐帧）、<名>_<态>_depth_max.tif（GDAL 对中文路径不稳，交付时再改名）（缺省 out = $WORK/c2d/tif）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

DRY = 0.02
NODATA = -9999.0


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import importlib.util
    import os
    pd = os.path.join(os.path.dirname(importlib.util.find_spec("rasterio").origin), "proj_data")
    os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = pd       # 本机 PostgreSQL 的旧 proj.db 会抢在前面
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_origin
    ap = argparse.ArgumentParser(); ap.add_argument("name"); ap.add_argument("states", nargs="+"); ap.add_argument("--out")
    ap.add_argument("--cell", type=float, default=None, help="栅格尺寸 m（缺省 = 计算网格 res_m）")
    a = ap.parse_args()
    out = pathlib.Path(a.out) if a.out else WORK / "c2d" / "tif"; out.mkdir(parents=True, exist_ok=True)
    crs = CRS.from_wkt(CRS.from_epsg(32640).to_wkt())
    for st in a.states:
        z = np.load(WORK / "c2d" / f"{a.name}_{st}_2d.npz")
        res = float(json.loads((WORK / "c2d" / f"twoway_{a.name}_{st}.json").read_text(encoding="utf-8"))["res_m"])
        cell = a.cell or res
        xy = z["xy"].astype(float)
        x0 = np.floor(xy[:, 0].min() / cell) * cell; y1 = np.ceil(xy[:, 1].max() / cell) * cell
        nx = int(np.ceil((xy[:, 0].max() - x0) / cell)) + 1; ny = int(np.ceil((y1 - xy[:, 1].min()) / cell)) + 1
        if cell >= res:
            # 栅格不比计算网细：形心落格、每格取最大
            col = np.clip(((xy[:, 0] - x0) / cell).astype(int), 0, nx - 1)
            row = np.clip(((y1 - xy[:, 1]) / cell).astype(int), 0, ny - 1)
            idx = row * nx + col
            inside = np.zeros(nx * ny, bool); inside[idx] = True

            def grid(v):
                g = np.zeros(nx * ny, np.float32)
                np.maximum.at(g, idx, np.where(v >= DRY, v, 0.0).astype(np.float32))
                g[~inside] = NODATA
                return g.reshape(ny, nx)
        else:
            # 栅格比计算网细：落格会留空洞，改为格心线性插值（权重算一次）；离最近形心超过一个计算网格的格算域外
            from scipy.spatial import Delaunay, cKDTree
            gx, gy = np.meshgrid(x0 + (np.arange(nx) + 0.5) * cell, y1 - (np.arange(ny) + 0.5) * cell)
            P = np.c_[gx.ravel(), gy.ravel()]
            inside = cKDTree(xy).query(P, distance_upper_bound=res)[0] < res
            tri = Delaunay(xy)
            s = tri.find_simplex(P); ok = inside & (s >= 0)
            Tm = tri.transform[s[ok]]
            b2 = np.einsum("ijk,ik->ij", Tm[:, :2], P[ok] - Tm[:, 2])
            W3 = np.c_[b2, 1.0 - b2.sum(1)]; V3 = tri.simplices[s[ok]]
            near_i = inside & ~ok
            near = cKDTree(xy).query(P[near_i])[1]

            def grid(v):
                g = np.full(nx * ny, NODATA, np.float32)
                g[ok] = (v[V3] * W3).sum(1); g[near_i] = v[near]
                m = inside & (g < DRY); g[m] = 0.0
                return g.reshape(ny, nx)

        prof = dict(driver="GTiff", width=nx, height=ny, dtype="float32", crs=crs, transform=from_origin(x0, y1, cell, cell),
                    nodata=NODATA, compress="deflate", predictor=3, tiled=True, blockxsize=512, blockysize=512, BIGTIFF="IF_SAFER")
        f1 = out / f"{a.name}_{st}_depth_max.tif"
        with rasterio.open(f1, "w", count=1, **prof) as ds:
            ds.write(grid(z["hmax"].astype(float)), 1); ds.set_band_description(1, "全程最大水深 m")
        T = z["t_h"].astype(float); F = z["frames"]
        f2 = out / f"{a.name}_{st}_depth_frames.tif"
        with rasterio.open(f2, "w", count=len(T), **prof) as ds:
            for i in range(len(T)):
                ds.write(grid(F[i].astype(float)), i + 1); ds.set_band_description(i + 1, f"t={T[i]:.2f} h")
        f2.with_suffix(".json").write_text(json.dumps({"bands": [{"band": i + 1, "t_h": round(float(t), 3)} for i, t in enumerate(T)],
            "unit": "m（水深）", "crs": "EPSG:32640", "cell_m": cell, "compute_res_m": res, "dry_m": DRY, "nodata": NODATA,
            "note": ("三角单元形心水深落到网格、每格取最大（一格约 4 个三角单元）；" if cell >= res else
                     "栅格比计算网细：格心由三角单元形心水深线性插值（不多出信息，只是更平滑）；") + "< 0.02 m 记 0；nodata = 计算域外。用栅格数积水面积会偏大，面积以按三角单元真实面积算的统计（对照表）为准"}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{a.name}_{st}：{nx}×{ny} @ {cell:g} m　最大水深 {f1.stat().st_size / 1e6:.0f} MB、逐帧 {len(T)} 波段 {f2.stat().st_size / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
