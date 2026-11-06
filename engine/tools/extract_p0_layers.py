#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P0 图层提取工具：从 Makani 提取核心 GIS 数据

优先级：🔴 P0（今晚必须完成）

目标：
1. 提取建筑物（udm_building）
2. 提取海域（udm_seasurface + udm_seashore）
3. 提取道路（udm_roadcentreline）
4. 范围：RD-A 汇水区 + 5km 缓冲
5. 输出：GeoPackage 格式（.gpkg）

输出文件：
- data/gis/buildings.gpkg
- data/gis/sea.gpkg
- data/gis/roads.gpkg
- data/gis/dtm_rda.tif（裁剪后的 DTM）
"""

import sys
import psycopg2
import geopandas as gpd
from shapely.geometry import box
from pathlib import Path
import rasterio
from rasterio.mask import mask
import json

# 读取配置
try:
    import tomllib
except ImportError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def load_config():
    """加载数据库配置"""
    config_file = ROOT / "config.toml"
    with open(config_file, "rb") as f:
        config = tomllib.load(f)
    return config["source"]["makani"]

def get_rda_bounds():
    """获取 RD-A 汇水区边界（+ 5km 缓冲）

    TODO: 从现有数据读取 RD-A 边界
    临时方案：基于已知大概位置估算
    """
    # RD-A 汇水区大致在阿布扎比岛东部
    # 临时使用估算范围（后续从 out/l2opt/RDA_96B 中读取精确边界）

    # 中心点大约 (54.37, 24.47)，UTM 坐标大约 (259000, 2707000)
    # 汇水区大约 5 km × 5 km
    # 加 5 km 缓冲 = 15 km × 15 km

    center_x = 259000
    center_y = 2707000
    half_size = 7500  # 15 km / 2

    bounds = box(
        center_x - half_size,
        center_y - half_size,
        center_x + half_size,
        center_y + half_size
    )

    return bounds

def extract_buildings(conn, bounds, output_file):
    """提取建筑物"""
    print("\n[1/4] 提取建筑物...")

    # 构建 SQL（空间查询）
    sql = f"""
        SELECT
            gid,
            ST_Transform(geom, 32640) as geometry,
            height,
            use_type,
            build_year,
            area_m2
        FROM public.udm_building
        WHERE ST_Intersects(
            ST_Transform(geom, 32640),
            ST_GeomFromText('{bounds.wkt}', 32640)
        )
    """

    print(f"  执行空间查询...")
    gdf = gpd.read_postgis(sql, conn, geom_col='geometry', crs='EPSG:32640')

    print(f"  提取到 {len(gdf):,} 个建筑")

    if len(gdf) > 0:
        # 保存为 GeoPackage
        gdf.to_file(output_file, driver="GPKG", layer="buildings")
        print(f"  ✅ 已保存: {output_file}")

        # 统计
        print(f"  统计信息:")
        total_area = gdf.geometry.area.sum()
        print(f"    - 总面积: {total_area/1e6:.2f} km²")
    else:
        print(f"  ⚠️ 未提取到建筑（可能范围不对）")

    return gdf

def extract_sea(conn, bounds, output_file):
    """提取海域"""
    print("\n[2/4] 提取海域...")

    # 提取海域表面
    sql_surface = f"""
        SELECT
            gid,
            ST_Transform(geom, 32640) as geometry,
            'surface' as type
        FROM public.udm_seasurface
        WHERE ST_Intersects(
            ST_Transform(geom, 32640),
            ST_GeomFromText('{bounds.wkt}', 32640)
        )
    """

    # 提取海岸线
    sql_shore = f"""
        SELECT
            gid,
            ST_Transform(geom, 32640) as geometry,
            'shore' as type
        FROM public.udm_seashore
        WHERE ST_Intersects(
            ST_Transform(geom, 32640),
            ST_GeomFromText('{bounds.wkt}', 32640)
        )
    """

    print(f"  提取海域表面...")
    gdf_surface = gpd.read_postgis(sql_surface, conn, geom_col='geometry', crs='EPSG:32640')
    print(f"    - 海域面: {len(gdf_surface)} 个")

    print(f"  提取海岸线...")
    gdf_shore = gpd.read_postgis(sql_shore, conn, geom_col='geometry', crs='EPSG:32640')
    print(f"    - 海岸线: {len(gdf_shore)} 条")

    # 合并
    if len(gdf_surface) > 0 or len(gdf_shore) > 0:
        # 分别保存为不同图层
        if len(gdf_surface) > 0:
            gdf_surface.to_file(output_file, driver="GPKG", layer="sea_surface")
        if len(gdf_shore) > 0:
            gdf_shore.to_file(output_file, driver="GPKG", layer="sea_shore")

        print(f"  ✅ 已保存: {output_file}")
    else:
        print(f"  ⚠️ 未提取到海域数据")

    return gdf_surface, gdf_shore

def extract_roads(conn, bounds, output_file):
    """提取道路"""
    print("\n[3/4] 提取道路...")

    sql = f"""
        SELECT
            gid,
            ST_Transform(geom, 32640) as geometry,
            road_name,
            road_class,
            width,
            surface_type
        FROM public.udm_roadcentreline
        WHERE ST_Intersects(
            ST_Transform(geom, 32640),
            ST_GeomFromText('{bounds.wkt}', 32640)
        )
    """

    print(f"  执行空间查询...")
    gdf = gpd.read_postgis(sql, conn, geom_col='geometry', crs='EPSG:32640')

    print(f"  提取到 {len(gdf):,} 条道路")

    if len(gdf) > 0:
        # 保存
        gdf.to_file(output_file, driver="GPKG", layer="roads")
        print(f"  ✅ 已保存: {output_file}")

        # 统计
        total_length = gdf.geometry.length.sum()
        print(f"  统计信息:")
        print(f"    - 总长度: {total_length/1000:.1f} km")
    else:
        print(f"  ⚠️ 未提取到道路（可能范围不对）")

    return gdf

def clip_dtm(dtm_path, bounds, output_file):
    """裁剪 DTM 到 RD-A 范围"""
    print("\n[4/4] 裁剪 DTM...")

    print(f"  源文件: {dtm_path}")
    print(f"  输出: {output_file}")

    with rasterio.open(dtm_path) as src:
        # 裁剪
        out_image, out_transform = mask(src, [bounds], crop=True, all_touched=True)
        out_meta = src.meta.copy()

        # 更新元数据
        out_meta.update({
            "driver": "GTiff",
            "height": out_image.shape[1],
            "width": out_image.shape[2],
            "transform": out_transform,
            "compress": "lzw"
        })

        # 保存
        with rasterio.open(output_file, "w", **out_meta) as dest:
            dest.write(out_image)

        file_size = Path(output_file).stat().st_size / (1024**2)
        print(f"  ✅ 已保存: {output_file} ({file_size:.1f} MB)")
        print(f"  裁剪后尺寸: {out_image.shape[2]} × {out_image.shape[1]}")

def main():
    """主流程"""
    print("=" * 80)
    print("P0 核心图层提取（RD-A 汇水区）")
    print("=" * 80)

    # 输出目录
    output_dir = ROOT / "data" / "gis"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 获取范围
    print("\n[0/4] 确定提取范围...")
    bounds = get_rda_bounds()
    print(f"  范围（UTM 32640）: {bounds.bounds}")
    print(f"  面积: {bounds.area / 1e6:.2f} km²")

    # 连接数据库
    print("\n连接 Makani 数据库...")
    cfg = load_config()
    try:
        conn = psycopg2.connect(
            host=cfg["host"],
            port=cfg["port"],
            user=cfg["user"],
            password=cfg["password"],
            dbname=cfg["dbname"]
        )
        print("  ✅ 连接成功")
    except Exception as e:
        print(f"  ❌ 连接失败: {e}")
        return

    try:
        # 提取建筑物
        buildings_file = output_dir / "buildings.gpkg"
        buildings = extract_buildings(conn, bounds, buildings_file)

        # 提取海域
        sea_file = output_dir / "sea.gpkg"
        sea_surface, sea_shore = extract_sea(conn, bounds, sea_file)

        # 提取道路
        roads_file = output_dir / "roads.gpkg"
        roads = extract_roads(conn, bounds, roads_file)

    finally:
        conn.close()
        print("\n数据库连接已关闭")

    # 裁剪 DTM
    dtm_source = Path(r"D:\阿布扎比\DTM_z40_customer\AUH_DTM_5m_Z40.TIF")
    dtm_output = output_dir / "dtm_rda.tif"

    if dtm_source.exists():
        clip_dtm(dtm_source, bounds, dtm_output)
    else:
        print(f"\n⚠️ DTM 源文件不存在: {dtm_source}")

    # 生成报告
    print("\n" + "=" * 80)
    print("提取完成")
    print("=" * 80)

    report = {
        "extraction_time": str(Path(__file__).stat().st_mtime),
        "bounds_wkt": bounds.wkt,
        "bounds_area_km2": bounds.area / 1e6,
        "files": {
            "buildings": str(buildings_file),
            "sea": str(sea_file),
            "roads": str(roads_file),
            "dtm": str(dtm_output)
        },
        "counts": {
            "buildings": len(buildings) if len(buildings) > 0 else 0,
            "sea_surface": len(sea_surface) if len(sea_surface) > 0 else 0,
            "sea_shore": len(sea_shore) if len(sea_shore) > 0 else 0,
            "roads": len(roads) if len(roads) > 0 else 0
        }
    }

    report_file = output_dir / "extraction_report.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\n📊 报告已保存: {report_file}")

    print("\n下一步:")
    print("  1. 检查提取的数据（用 QGIS 打开 .gpkg 文件）")
    print("  2. 验证坐标系和覆盖范围")
    print("  3. 如果数据正确，继续开发约束引擎")

if __name__ == "__main__":
    main()
