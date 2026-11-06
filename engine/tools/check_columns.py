import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from swbrain.db import load_config, connect_source  # noqa: E402

conn = connect_source(load_config(), "makani")

cur = conn.cursor()

# 查询 udm_building 的字段
cur.execute("""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name='udm_building'
    AND table_schema='public'
    ORDER BY ordinal_position
""")

print("udm_building 表字段:")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

# 查询几何字段
cur.execute("""
    SELECT f_geometry_column, type
    FROM geometry_columns
    WHERE f_table_name='udm_building'
""")

print("\n几何字段:")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

cur.close()
conn.close()
