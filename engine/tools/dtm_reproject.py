# -*- coding: utf-8 -*-
"""把别的坐标系 / 分辨率的 DTM 转成系统要的格式：EPSG:32640（UTM 40N）、米、float32、无效值 -9999（2026-10-04）。

系统里的井盖标高、汇水区、二维地形都按 EPSG:32640 的坐标去读 DTM；坐标系不对时每一块都落在图外，汇水区会全空。
新地形如 AUH_DTM.TIF（EPSG:3857、0.5 m、430 GB）不能直接用：一来坐标系不对，二来全市 0.5 m 太大，重算要读几百 GB。
这个脚本重投影并按目标分辨率取平均（降采样时 GDAL 会自动用旁边 .ovr 里的金字塔，读得很少）。

    # 在服务器上（脚本不在镜像里，经标准输入喂给容器里的 python；输入输出路径都是容器内路径）
    docker exec -i swbrain python - --src /media/pop-fs/flood_data/DTM_50cm_2024/AUH_DTM.TIF \\
        --dst /work/dtm/AUH_DTM_32640_2m.tif --res 2 < dtm_reproject.py
    # 在开发机上
    python tools/dtm_reproject.py --src <源> --dst <目标.tif> --res 2

--res        目标分辨率（米），缺省 2。想和旧版一致用 5；热点区域要更细可以另裁小块。
--bounds     目标范围 minx miny maxx maxy（EPSG:32640 米）。缺省 = 源 DTM 范围换算过去的外接矩形
--check      只打印源信息、目标范围与像元数、估算大小，不写文件

做完把 config.toml 的 [terrain].dtm_path 指向 --dst，再 bash rebuild-state.sh。
"""
from __future__ import annotations

import argparse
import math
import sys
import time

import numpy as np

DST_CRS = "EPSG:32640"
NODATA = -9999.0


def main(argv=None):
    ap = argparse.ArgumentParser(description="DTM 重投影为 EPSG:32640 并降采样")
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--res", type=float, default=2.0)
    ap.add_argument("--bounds", nargs=4, type=float, metavar=("MINX", "MINY", "MAXX", "MAXY"))
    ap.add_argument("--block", type=int, default=2048, help="每次处理的目标块边长（像元）")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true", help="目标文件已存在时覆盖")
    a = ap.parse_args(argv)
    import os
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.vrt import WarpedVRT
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window

    with rasterio.open(a.src) as src:
        print(f"源：{a.src}\n  坐标系 {src.crs}  分辨率 {src.res}  尺寸 {src.width}×{src.height}  无效值 {src.nodata}  类型 {src.dtypes[0]}  金字塔 {src.overviews(1)}")
        if src.crs is None:
            sys.exit("源文件没有坐标系，先用 gdal_edit 或 rasterio 写上，不要猜")
        if a.bounds:
            minx, miny, maxx, maxy = a.bounds
        else:
            minx, miny, maxx, maxy = transform_bounds(src.crs, DST_CRS, *src.bounds, densify_pts=41)
        r = a.res
        minx, miny = math.floor(minx / r) * r, math.floor(miny / r) * r
        maxx, maxy = math.ceil(maxx / r) * r, math.ceil(maxy / r) * r
        W, H = int(round((maxx - minx) / r)), int(round((maxy - miny) / r))
        print(f"目标：{DST_CRS}  分辨率 {r} m  范围 x {minx:.0f}–{maxx:.0f}  y {miny:.0f}–{maxy:.0f}  尺寸 {W}×{H}（{W * H / 1e9:.2f} 亿像元，未压缩约 {W * H * 4 / 1e9:.1f} GB）")
        if a.check:
            return 0
        if os.path.exists(a.dst) and not a.force:
            sys.exit(f"目标已存在：{a.dst}（加 --force 覆盖）")
        os.makedirs(os.path.dirname(os.path.abspath(a.dst)), exist_ok=True)
        tr = from_origin(minx, maxy, r, r)
        tmp = a.dst + ".part"
        prof = dict(driver="GTiff", height=H, width=W, count=1, dtype="float32", crs=DST_CRS, transform=tr, nodata=NODATA,
                    tiled=True, blockxsize=512, blockysize=512, compress="deflate", predictor=3, zlevel=6, BIGTIFF="YES")
        t0 = time.time()
        with WarpedVRT(src, crs=DST_CRS, transform=tr, width=W, height=H, resampling=Resampling.average,
                       src_nodata=src.nodata, nodata=NODATA, dtype="float32", warp_mem_limit=512,
                       num_threads=4) as vrt, rasterio.open(tmp, "w", **prof) as dst:
            B = a.block
            nb = math.ceil(H / B) * math.ceil(W / B)
            k = 0
            for row in range(0, H, B):
                for col in range(0, W, B):
                    w = Window(col, row, min(B, W - col), min(B, H - row))
                    arr = vrt.read(1, window=w)
                    arr = np.where(np.isfinite(arr) & (np.abs(arr) < 1e6), arr, NODATA).astype("float32")   # 3.4e38 之类的无效值一并清掉
                    if not (arr == NODATA).all():                                  # 全空的块不写，GeoTIFF 里就是稀疏的
                        dst.write(arr, 1, window=w)
                    k += 1
                    if k % 20 == 0 or k == nb:
                        el = time.time() - t0
                        print(f"  {k}/{nb} 块（{el / 60:.1f} 分钟，约剩 {el / k * (nb - k) / 60:.0f} 分钟）", flush=True)
        os.replace(tmp, a.dst)
    with rasterio.open(a.dst) as d:                                               # 抽样自检：有效像元比例、高程范围
        step = max(1, d.height // 400)
        s = d.read(1, out_shape=(d.height // step, d.width // step), resampling=Resampling.nearest)
        ok = s != NODATA
        print(f"完成：{a.dst}  {os.path.getsize(a.dst) / 1e9:.2f} GB，抽样有效像元 {ok.mean() * 100:.1f}%，高程 {s[ok].min():.1f} ~ {s[ok].max():.1f} m，用时 {(time.time() - t0) / 60:.1f} 分钟")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
