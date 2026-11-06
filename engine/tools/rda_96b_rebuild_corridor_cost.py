#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""重建 corridor_cost.tif：正确区分路面（可通行）和建筑（不可通行）

问题：l2opt_land.py 把路面、建筑外扩、海面全归为 cls=0，无法区分。
      41.8% 的井口在 cls=0，它们大多在路面（合理），但被标记为不可通行。

解决：直接从 Makani 读建筑和海域栅格化，路面设为最低成本（1.0）。
"""
import sys, json, numpy as np, rasterio
from pathlib import Path
from pyproj import Transformer
from rasterio.features import rasterize
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

# 成本定义（每米实际长度的等效成本）
COST_ROAD = 1.0          # 路面（最优）
COST_LAND_1 = 2.5        # 一级用地（公共开放、休闲、农业）
COST_LAND_2 = 4.0        # 二级用地（未建开发地块）
COST_NEARSHORE = 10.0    # 近岸海底（200 m 内）
COST_IMPASSABLE = 1e9    # 不可通行（建筑外扩 5 m、远海、禁区）

def main():
    run = "RDA_96B"
    res = 5.0
    nearshore_m = 200
    building_buffer_m = 5

    print(f"Rebuilding corridor_cost.tif for {run}")
    print(f"  Resolution: {res} m")
    print(f"  Building buffer: {building_buffer_m} m")
    print(f"  Near-shore: {nearshore_m} m\n")

    # 从 land_cls 读取范围和网格
    land_cls_file = ROOT / "out" / "l2opt" / run / "land_cls.tif"
    with rasterio.open(land_cls_file) as src:
        land_cls = src.read(1)
        tf = src.transform
        H, W = land_cls.shape
        bbox = src.bounds
        crs_wkt = src.crs.to_wkt()

    print(f"[1/4] Grid: {W} × {H}, bbox: {[int(v) for v in bbox]}")

    # 初始化成本：从 land_cls 继承基础分类
    cost = np.full((H, W), COST_IMPASSABLE, dtype=np.float32)

    # land_cls = 4（道路红线/管线走廊）→ 中等成本
    cost[land_cls == 4] = COST_LAND_2

    # land_cls = 1（一级用地）→ 中等成本
    cost[land_cls == 1] = COST_LAND_1

    # land_cls = 2, 3（二级用地）→ 较高成本
    cost[(land_cls == 2) | (land_cls == 3)] = COST_LAND_2

    # land_cls = 0（路面/建筑/海）→ 暂时设为路面成本，后面会覆盖建筑和海
    cost[land_cls == 0] = COST_ROAD

    print(f"  Base cost from land_cls: passable={( cost < COST_IMPASSABLE).sum()} cells")

    # 从 GIS 读取建筑并外扩
    print(f"\n[2/4] Loading buildings from GIS...")
    buildings_file = ROOT / "data" / "gis" / "buildings.gpkg"
    if buildings_file.exists():
        import geopandas as gpd
        buildings = gpd.read_file(buildings_file)
        print(f"  Loaded {len(buildings)} buildings")

        # 外扩 5 m
        buildings_buffered = buildings.copy()
        buildings_buffered['geometry'] = buildings.geometry.buffer(building_buffer_m)

        # 栅格化
        building_mask = rasterize(
            [(geom, 1) for geom in buildings_buffered.geometry],
            out_shape=(H, W),
            transform=tf,
            fill=0,
            dtype='uint8'
        )

        # 建筑外扩区域不可通行
        cost[building_mask > 0] = COST_IMPASSABLE
        print(f"  Buildings + {building_buffer_m}m buffer: {building_mask.sum()} cells blocked")
    else:
        print(f"  WARNING: {buildings_file} not found")

    # 海域处理
    print(f"\n[3/4] Processing sea...")
    sea_file = ROOT / "data" / "gis" / "sea.gpkg"
    if sea_file.exists():
        import geopandas as gpd
        sea = gpd.read_file(sea_file, layer='surface')
        print(f"  Loaded {len(sea)} sea polygons")

        if len(sea) > 0:
            # 栅格化海域
            sea_mask = rasterize(
                [(geom, 1) for geom in sea.geometry],
                out_shape=(H, W),
                transform=tf,
                fill=0,
                dtype='uint8'
            )

            # 从海岸线生成距离场
            sea_edge = ndimage.binary_dilation(sea_mask, iterations=1) & ~sea_mask.astype(bool)
            dist_from_shore = ndimage.distance_transform_edt(~sea_edge) * res

            # 近岸段（≤ nearshore_m）：允许通过，高成本
            nearshore = sea_mask & (dist_from_shore <= nearshore_m)
            cost[nearshore] = COST_NEARSHORE

            # 远海：不可通行
            far_sea = sea_mask & (dist_from_shore > nearshore_m)
            cost[far_sea] = COST_IMPASSABLE

            print(f"  Near-shore (≤{nearshore_m}m): {nearshore.sum()} cells")
            print(f"  Far sea (>{nearshore_m}m): {far_sea.sum()} cells blocked")
    else:
        print(f"  WARNING: {sea_file} not found")

    # DTM nodata 处理
    print(f"\n[4/4] Checking DTM nodata...")
    dtm_file = Path(r"D:\阿布扎比\DTM_z40_customer\AUH_DTM_5m_Z40.TIF")
    if dtm_file.exists():
        from rasterio.warp import reproject, Resampling
        from rasterio.crs import CRS
        with rasterio.open(dtm_file) as dtm_src:
            dtm = np.full((H, W), np.nan, dtype=np.float32)
            reproject(
                dtm_src.read(1).astype(np.float32), dtm,
                src_transform=dtm_src.transform, src_crs=dtm_src.crs,
                dst_transform=tf, dst_crs=CRS.from_wkt(crs_wkt),
                src_nodata=dtm_src.nodata, dst_nodata=np.nan,
                resampling=Resampling.nearest
            )
        nodata_mask = np.isnan(dtm)
        cost[nodata_mask] = COST_IMPASSABLE
        print(f"  DTM nodata: {nodata_mask.sum()} cells blocked")

    # 统计
    print(f"\n  Final cost stats:")
    print(f"    Road (cost={COST_ROAD}): {(cost == COST_ROAD).sum()} cells")
    print(f"    Land-1 (cost={COST_LAND_1}): {(cost == COST_LAND_1).sum()} cells")
    print(f"    Land-2 (cost={COST_LAND_2}): {(cost == COST_LAND_2).sum()} cells")
    print(f"    Near-shore (cost={COST_NEARSHORE}): {(cost == COST_NEARSHORE).sum()} cells")
    print(f"    Impassable: {(cost >= COST_IMPASSABLE).sum()} cells")
    print(f"    Passable total: {(cost < COST_IMPASSABLE).sum()} cells")

    # 保存
    out_file = ROOT / "out" / "l2opt" / run / "corridor_cost.tif"
    prof = {
        "driver": "GTiff",
        "height": H,
        "width": W,
        "count": 1,
        "dtype": "float32",
        "crs": CRS.from_wkt(crs_wkt),
        "transform": tf,
        "compress": "deflate",
        "nodata": COST_IMPASSABLE
    }
    with rasterio.open(out_file, "w", **prof) as w:
        w.write(cost, 1)

    print(f"\n→ {out_file}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
