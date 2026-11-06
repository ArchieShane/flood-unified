#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P0 layer extraction tool (simplified)"""

import sys
import psycopg2
import geopandas as gpd
from shapely.geometry import box
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent

def load_config():
    config_file = ROOT / "config.toml"
    with open(config_file, "rb") as f:
        config = tomllib.load(f)
    return config["source"]["makani"]

def get_rda_bounds(run="RDA_96B", buffer_m=2000):
    """Extent of the run's candidates (wells, pipes, ponds, outfalls) + buffer.
    (2026-10-22: the earlier hard-coded box was ~20 km east of RD-A and gave meaningless checks.)"""
    c = gpd.read_file(ROOT / "out" / "l2opt" / run / "candidates.geojson").to_crs(32640)
    x0, y0, x1, y1 = c.total_bounds
    return box(x0 - buffer_m, y0 - buffer_m, x1 + buffer_m, y1 + buffer_m)

def main():
    print("=" * 80)
    print("P0 Core Layers Extraction (RD-A)")
    print("=" * 80)

    output_dir = ROOT / "data" / "gis"
    output_dir.mkdir(parents=True, exist_ok=True)

    bounds = get_rda_bounds()
    print(f"\nExtraction bounds: {bounds.bounds}")

    print("\nConnecting to database...")
    cfg = load_config()
    conn = psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        dbname=cfg["dbname"]
    )
    print("  OK")

    try:
        # 1. Buildings
        print("\n[1/3] Buildings...")
        sql = f"""
            SELECT
                guid,
                ST_Transform(shape, 32640) as geometry
            FROM public.udm_building
            WHERE ST_Intersects(
                ST_Transform(shape, 32640),
                ST_GeomFromText('{bounds.wkt}', 32640)
            )
        """
        buildings = gpd.read_postgis(sql, conn, geom_col='geometry', crs='EPSG:32640')
        print(f"  Extracted: {len(buildings):,}")

        if len(buildings) > 0:
            file = output_dir / "buildings.gpkg"
            buildings.to_file(file, driver="GPKG")
            print(f"  Saved: {file}")

        # 2. Sea
        print("\n[2/3] Sea...")
        sql = f"""
            SELECT
                guid,
                ST_Transform(shape, 32640) as geometry
            FROM public.udm_seasurface
            WHERE ST_Intersects(
                ST_Transform(shape, 32640),
                ST_GeomFromText('{bounds.wkt}', 32640)
            )
        """
        sea = gpd.read_postgis(sql, conn, geom_col='geometry', crs='EPSG:32640')
        print(f"  Extracted: {len(sea):,} polygons")

        sql = f"""
            SELECT
                guid,
                ST_Transform(shape, 32640) as geometry
            FROM public.udm_seashore
            WHERE ST_Intersects(
                ST_Transform(shape, 32640),
                ST_GeomFromText('{bounds.wkt}', 32640)
            )
        """
        shore = gpd.read_postgis(sql, conn, geom_col='geometry', crs='EPSG:32640')
        print(f"  Extracted: {len(shore):,} lines")

        if len(sea) > 0 or len(shore) > 0:
            file = output_dir / "sea.gpkg"
            if len(sea) > 0:
                sea.to_file(file, driver="GPKG", layer="surface")
            if len(shore) > 0:
                shore.to_file(file, driver="GPKG", layer="shore")
            print(f"  Saved: {file}")

        # 3. Roads
        print("\n[3/3] Roads...")
        sql = f"""
            SELECT
                guid,
                ST_Transform(shape, 32640) as geometry
            FROM public.udm_roadcentreline
            WHERE ST_Intersects(
                ST_Transform(shape, 32640),
                ST_GeomFromText('{bounds.wkt}', 32640)
            )
        """
        roads = gpd.read_postgis(sql, conn, geom_col='geometry', crs='EPSG:32640')
        print(f"  Extracted: {len(roads):,}")

        if len(roads) > 0:
            file = output_dir / "roads.gpkg"
            roads.to_file(file, driver="GPKG")
            total_km = roads.geometry.length.sum() / 1000
            print(f"  Saved: {file} ({total_km:.1f} km)")

    finally:
        conn.close()

    print("\n" + "=" * 80)
    print("P0 Layer Extraction Complete")
    print("=" * 80)
    print(f"\nOutput directory: {output_dir}")
    print("  - buildings.gpkg")
    print("  - sea.gpkg")
    print("  - roads.gpkg")

if __name__ == "__main__":
    main()
