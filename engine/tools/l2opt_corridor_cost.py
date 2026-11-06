#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L2+suggested 优化试点 · 管线通行成本栅格

和 land_cls.tif 同网格（5 m），给每个格子一个通行成本（单位 m 的等效长度），用于最小成本路径。

成本定义：
  0（或极小值）：不可通行（建筑外扩 5 m、海面、水面、禁建区）
  1.0：道路路面、路肩、管线走廊（低成本，需审批）
  1.5：道路红线外扩（次选）
  2.5：一级用地（公共开放空间、休闲、农业，可用但成本高）
  4.0：二级用地（未建开发地块、无地块记录地，需业主/规划部门同意）
  10.0（或近岸海底）：排海口最后 200 m 的近岸海底段，单独计价
  INF：远海（超过 200 m）、DTM 无值

压力管和重力管共用一张成本面。海底段在候选生成时单独检查长度和成本。
"""
from __future__ import annotations
import argparse
import numpy as np
import rasterio
from pathlib import Path
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent

# 成本系数（每米实际长度对应的等效成本，单位 m）
COST_ROAD = 1.0          # 道路路面、路肩、管线走廊（最优）
COST_ROAD_BUFFER = 1.5   # 道路红线外扩（次选）
COST_LAND_1 = 2.5        # 一级用地（公共开放、休闲、农业）
COST_LAND_2 = 4.0        # 二级用地（未建开发地块、无地块记录）
COST_NEARSHORE = 10.0    # 近岸海底（200 m 以内，排海口用）
COST_IMPASSABLE = 1e9    # 不可通行（建筑外扩、远海、禁建区）

def build_cost_raster(land_cls_file, sea_shapefile, dtm_file, bbox, res=5.0, nearshore_m=200, log=print):
    """
    从 land_cls.tif、sea.gpkg、DTM 构建通行成本栅格。

    Args:
        land_cls_file: land_cls.tif（l2opt_land.py 输出）
        sea_shapefile: sea.gpkg layer=surface
        dtm_file: DTM 文件
        bbox: (x0, y0, x1, y1) UTM 32640
        res: 分辨率 m
        nearshore_m: 近岸海底段距离 m（从海岸线向海量）

    Returns:
        cost: (H, W) 成本栅格
        transform: rasterio affine transform
    """
    import geopandas as gpd
    from rasterio.features import rasterize
    from rasterio.transform import from_origin

    log("Building corridor cost raster...")

    x0, y0, x1, y1 = bbox
    W = int(np.ceil((x1 - x0) / res))
    H = int(np.ceil((y1 - y0) / res))
    tf = from_origin(x0, y1, res, res)

    # 1. 从 land_cls 初始化成本
    log(f"  Reading land_cls: {land_cls_file}")
    with rasterio.open(land_cls_file) as src:
        land_cls = src.read(1)

    # land_cls 编码（见 l2opt_land.py）:
    #   0 = 不能用（路面/建筑外扩/海/水/铁路/现有塘/DTM≤0.2）
    #   1 = 一级（公共开放空间/休闲/农业）
    #   2 = 二级（未建开发地块）
    #   3 = 二级 b（无地块记录）
    #   4 = 次选（道路红线/管线走廊）
    #   9 = 不用（已分配地块）
    # 255 = nodata

    cost = np.full((H, W), COST_IMPASSABLE, dtype=np.float32)

    # land_cls = 4（道路红线/管线走廊）→ 低成本
    cost[land_cls == 4] = COST_ROAD_BUFFER

    # land_cls = 1（一级用地）→ 中等成本
    cost[land_cls == 1] = COST_LAND_1

    # land_cls = 2, 3（二级用地）→ 较高成本
    cost[(land_cls == 2) | (land_cls == 3)] = COST_LAND_2

    # land_cls = 0（路面部分）提取为最低成本
    # 但 land_cls=0 包含了路面、建筑外扩、海面等，需要细分
    # 这里假设 land_cls=0 且 DTM > 0.2 的是路面（简化）
    # 更精确的做法是从 Makani 再取一次道路面，这里先简化
    # 暂时：land_cls=0 都视为不可通行，只有 4/1/2/3 可走

    # 2. 海域处理：近岸 200 m 内允许通过（排海口用），成本高
    log(f"  Processing sea area: {sea_shapefile}")
    if Path(sea_shapefile).exists():
        sea = gpd.read_file(sea_shapefile, layer='surface')
        if len(sea) > 0:
            # 栅格化海域
            sea_mask = rasterize(
                [(geom, 1) for geom in sea.geometry],
                out_shape=(H, W),
                transform=tf,
                fill=0,
                dtype='uint8'
            )

            # 从海岸线向海生成距离场
            # 海岸 = 海域边缘（海 mask 的梯度）
            sea_edge = ndimage.binary_dilation(sea_mask, iterations=1) & ~sea_mask.astype(bool)
            dist_from_shore = ndimage.distance_transform_edt(~sea_edge) * res

            # 近岸段（≤ nearshore_m）：允许通过，成本高
            nearshore = sea_mask & (dist_from_shore <= nearshore_m)
            cost[nearshore] = COST_NEARSHORE

            # 远海（> nearshore_m）：不可通行
            far_sea = sea_mask & (dist_from_shore > nearshore_m)
            cost[far_sea] = COST_IMPASSABLE

            log(f"    Near-shore ({nearshore_m}m): {nearshore.sum()} cells")
            log(f"    Far sea: {far_sea.sum()} cells")

    # 3. DTM 处理：无值区域不可通行
    log(f"  Checking DTM nodata: {dtm_file}")
    if Path(dtm_file).exists():
        with rasterio.open(dtm_file) as dtm_src:
            # 裁剪到 bbox 并重采样到 5 m
            from rasterio.warp import reproject, Resampling
            dtm = np.full((H, W), np.nan, dtype=np.float32)
            reproject(
                source=rasterio.band(dtm_src, 1),
                destination=dtm,
                src_transform=dtm_src.transform,
                src_crs=dtm_src.crs,
                dst_transform=tf,
                dst_crs='EPSG:32640',
                resampling=Resampling.bilinear
            )
            nodata_mask = np.isnan(dtm)
            cost[nodata_mask] = COST_IMPASSABLE
            log(f"    DTM nodata: {nodata_mask.sum()} cells")

    # 4. 建筑外扩（从 land_cls=0 里已经包含了，这里不需要重复）

    # 统计
    log(f"  Cost raster stats:")
    log(f"    Road/corridor (cost={COST_ROAD_BUFFER}): {(cost == COST_ROAD_BUFFER).sum()} cells")
    log(f"    Land-1 (cost={COST_LAND_1}): {(cost == COST_LAND_1).sum()} cells")
    log(f"    Land-2 (cost={COST_LAND_2}): {(cost == COST_LAND_2).sum()} cells")
    log(f"    Near-shore (cost={COST_NEARSHORE}): {(cost == COST_NEARSHORE).sum()} cells")
    log(f"    Impassable: {(cost >= COST_IMPASSABLE).sum()} cells")

    return cost, tf

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="Run name (e.g., RDA_96B)")
    ap.add_argument("--res", type=float, default=5.0, help="Resolution m (default 5)")
    ap.add_argument("--nearshore", type=float, default=200.0, help="Near-shore distance m (default 200)")
    a = ap.parse_args()

    # 输入文件
    out_dir = ROOT / "out" / "l2opt" / a.run
    land_cls_file = out_dir / "land_cls.tif"
    sea_file = ROOT / "data" / "gis" / "sea.gpkg"
    dtm_file = Path(r"D:\阿布扎比\DTM_z40_customer\AUH_DTM_5m_Z40.TIF")

    if not land_cls_file.exists():
        print(f"ERROR: {land_cls_file} not found. Run l2opt_land.py first.")
        return 1

    # 读取 bbox（从 land_cls）
    with rasterio.open(land_cls_file) as src:
        bounds = src.bounds
        bbox = (bounds.left, bounds.bottom, bounds.right, bounds.top)
        res = src.res[0]

    print(f"Run: {a.run}")
    print(f"Bbox: {bbox}")
    print(f"Resolution: {res} m")

    # 构建成本栅格
    cost, tf = build_cost_raster(land_cls_file, sea_file, dtm_file, bbox, res, a.nearshore)

    # 保存
    out_file = out_dir / "corridor_cost.tif"
    prof = {
        "driver": "GTiff",
        "height": cost.shape[0],
        "width": cost.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:32640",
        "transform": tf,
        "compress": "deflate",
        "nodata": COST_IMPASSABLE
    }
    with rasterio.open(out_file, "w", **prof) as w:
        w.write(cost, 1)

    print(f"→ {out_file}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
