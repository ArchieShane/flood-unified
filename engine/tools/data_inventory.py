#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据摸底工具：盘点 Makani 数据库中的 GIS 图层

目的：
1. 连接 Makani PostGIS 数据库
2. 列出所有可用的 GIS 图层
3. 评估数据完整性（记录数、几何类型、坐标系）
4. 生成数据清单

输出：
- GIS数据清单.xlsx
- 控制台日志
"""

import sys
import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
from pathlib import Path

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

def connect_makani():
    """连接 Makani 数据库"""
    cfg = load_config()
    conn = psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        dbname=cfg["dbname"]
    )
    return conn

def list_schemas(conn):
    """列出所有 schema"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
            ORDER BY schema_name
        """)
        return [row["schema_name"] for row in cur.fetchall()]

def list_spatial_tables(conn, schema="public"):
    """列出指定 schema 中的空间表"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                f_table_schema AS schema,
                f_table_name AS table_name,
                f_geometry_column AS geom_column,
                type AS geom_type,
                srid,
                coord_dimension AS dims
            FROM geometry_columns
            WHERE f_table_schema = %s
            ORDER BY f_table_name
        """, (schema,))
        return cur.fetchall()

def get_table_stats(conn, schema, table, geom_column):
    """获取表的统计信息"""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 记录数
        cur.execute(f'SELECT COUNT(*) as count FROM "{schema}"."{table}"')
        count = cur.fetchone()["count"]

        # 边界范围
        try:
            cur.execute(f"""
                SELECT
                    ST_XMin(extent) as xmin,
                    ST_YMin(extent) as ymin,
                    ST_XMax(extent) as xmax,
                    ST_YMax(extent) as ymax
                FROM (
                    SELECT ST_Extent("{geom_column}"::geometry) as extent
                    FROM "{schema}"."{table}"
                ) t
            """)
            extent = cur.fetchone()
        except:
            extent = None

        return {
            "count": count,
            "extent": extent
        }

def inventory_gis_data():
    """完整盘点 GIS 数据"""
    print("=" * 80)
    print("Makani GIS 数据盘点")
    print("=" * 80)

    # 连接数据库
    print("\n[1/4] 连接数据库...")
    try:
        conn = connect_makani()
        print("  ✅ 连接成功")
    except Exception as e:
        print(f"  ❌ 连接失败: {e}")
        return

    # 列出 schemas
    print("\n[2/4] 扫描 schemas...")
    schemas = list_schemas(conn)
    print(f"  找到 {len(schemas)} 个 schema:")
    for s in schemas[:10]:
        print(f"    - {s}")
    if len(schemas) > 10:
        print(f"    ... 还有 {len(schemas) - 10} 个")

    # 扫描空间表
    print("\n[3/4] 扫描空间表...")
    all_tables = []

    for schema in schemas:
        print(f"\n  Schema: {schema}")
        try:
            tables = list_spatial_tables(conn, schema)
            print(f"    找到 {len(tables)} 个空间表")

            for table in tables:
                print(f"      - {table['table_name']} ({table['geom_type']}, SRID={table['srid']})", end="")

                # 获取统计信息
                try:
                    stats = get_table_stats(
                        conn,
                        table['schema'],
                        table['table_name'],
                        table['geom_column']
                    )
                    print(f" - {stats['count']:,} 条记录")

                    all_tables.append({
                        "Schema": table['schema'],
                        "Table": table['table_name'],
                        "Geometry Column": table['geom_column'],
                        "Geometry Type": table['geom_type'],
                        "SRID": table['srid'],
                        "Record Count": stats['count'],
                        "XMin": stats['extent']['xmin'] if stats['extent'] else None,
                        "YMin": stats['extent']['ymin'] if stats['extent'] else None,
                        "XMax": stats['extent']['xmax'] if stats['extent'] else None,
                        "YMax": stats['extent']['ymax'] if stats['extent'] else None,
                    })
                except Exception as e:
                    print(f" - 错误: {e}")
        except Exception as e:
            print(f"    扫描失败: {e}")

    conn.close()

    # 生成报告
    print("\n[4/4] 生成报告...")

    if all_tables:
        df = pd.DataFrame(all_tables)

        # 保存 Excel
        output_file = ROOT / "docs" / "GIS数据清单.xlsx"
        df.to_excel(output_file, index=False, engine='openpyxl')
        print(f"  ✅ 已保存: {output_file}")

        # 打印重点表
        print("\n" + "=" * 80)
        print("重点 GIS 图层（用于排水优化）")
        print("=" * 80)

        # 建筑物
        buildings = df[df['Table'].str.contains('building', case=False, na=False)]
        if not buildings.empty:
            print("\n📍 建筑物相关：")
            for _, row in buildings.iterrows():
                print(f"  {row['Schema']}.{row['Table']}: {row['Record Count']:,} 条 ({row['Geometry Type']})")

        # 海域/水体
        water = df[df['Table'].str.contains('sea|water|ocean', case=False, na=False)]
        if not water.empty:
            print("\n🌊 海域/水体相关：")
            for _, row in water.iterrows():
                print(f"  {row['Schema']}.{row['Table']}: {row['Record Count']:,} 条 ({row['Geometry Type']})")

        # 道路
        roads = df[df['Table'].str.contains('road|street|highway', case=False, na=False)]
        if not roads.empty:
            print("\n🛣️ 道路相关：")
            for _, row in roads.iterrows():
                print(f"  {row['Schema']}.{row['Table']}: {row['Record Count']:,} 条 ({row['Geometry Type']})")

        # 地块
        parcels = df[df['Table'].str.contains('plot|parcel|land', case=False, na=False)]
        if not parcels.empty:
            print("\n🏞️ 地块/用地相关：")
            for _, row in parcels.iterrows():
                print(f"  {row['Schema']}.{row['Table']}: {row['Record Count']:,} 条 ({row['Geometry Type']})")

        # 绿地
        green = df[df['Table'].str.contains('green|park|landscape', case=False, na=False)]
        if not green.empty:
            print("\n🌳 绿地相关：")
            for _, row in green.iterrows():
                print(f"  {row['Schema']}.{row['Table']}: {row['Record Count']:,} 条 ({row['Geometry Type']})")

        print("\n" + "=" * 80)
        print(f"总计：{len(df)} 个空间表")
        print("=" * 80)
    else:
        print("  ⚠️ 未找到空间表")

if __name__ == "__main__":
    inventory_gis_data()
