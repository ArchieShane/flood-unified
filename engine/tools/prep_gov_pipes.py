#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""治理后数据预处理：生成兼容 build_city1d.py 的管网数据

输入：Stormwater_AD_reporting.gdb (治理后 Round 14)
输出：City_Gov_Pipes.csv（包含 StartCode/EndCode 的管线表）
用时：约 5-10 分钟（260,111 根管）
"""
import sys
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workdir import WORK

GDB_GOV = "D:/swbrain_work/exp_gov/07_export/Stormwater_AD_reporting.gdb"
OUT_CSV = WORK / "City_Gov_Pipes.csv"

def generate_node_code(x, y):
    """从坐标生成节点码（精度 1mm）"""
    return f"{int(round(x*1000))}_{int(round(y*1000))}"

def main():
    import pyogrio
    import pandas as pd

    t0 = time.time()
    print(f"=== 治理后数据预处理 ===")
    print(f"输入: {GDB_GOV}")
    print(f"输出: {OUT_CSV}")
    print()

    print("读取 StormwaterLine（260,111 根管）...")
    df = pyogrio.read_dataframe(GDB_GOV, layer="StormwaterLine")
    print(f"  读到 {len(df):,} 根管，耗时 {time.time()-t0:.1f} s")

    print("\n提取端点坐标并生成 StartCode/EndCode...")
    start_codes = []
    end_codes = []
    start_x, start_y = [], []
    end_x, end_y = [], []

    for i, geom in enumerate(df.geometry):
        if i % 50000 == 0 and i > 0:
            print(f"  处理 {i:,} / {len(df):,}...")

        # 处理 MultiLineString（取第一段）
        if geom.geom_type == 'MultiLineString':
            line = list(geom.geoms)[0]
        else:
            line = geom

        coords = list(line.coords)
        sx, sy = coords[0][0], coords[0][1]
        ex, ey = coords[-1][0], coords[-1][1]

        start_codes.append(generate_node_code(sx, sy))
        end_codes.append(generate_node_code(ex, ey))
        start_x.append(sx)
        start_y.append(sy)
        end_x.append(ex)
        end_y.append(ey)

    print(f"  完成，耗时 {time.time()-t0:.1f} s")

    print("\n构建输出表...")
    out = pd.DataFrame({
        "UNITID": df["dmt_unitid"],
        "StartCode": start_codes,
        "EndCode": end_codes,
        "StartX": start_x,
        "StartY": start_y,
        "EndX": end_x,
        "EndY": end_y,
        "StartElev": df["dmt_zcoord_sta"],
        "EndElev": df["dmt_zcoord_end"],
        "PIPE_DIAMETER": df["diameter"],
        "LineLength": df["measuredlength"],
        "INVERT_LEVEL_UPSTREAM": df["upelev"],
        "INVERT_LEVEL_DOWNSTREAM": df["downelev"],
        "STATUS": df["dmt_status"],
    })

    print(f"\n保存到 {OUT_CSV}...")
    out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    print(f"\n✓ 完成！")
    print(f"  总耗时: {time.time()-t0:.1f} s")
    print(f"  输出: {len(out):,} 行")
    print(f"  唯一起点码: {len(set(start_codes)):,}")
    print(f"  唯一终点码: {len(set(end_codes)):,}")
    print(f"  唯一节点码: {len(set(start_codes) | set(end_codes)):,}")

    print(f"\n下一步: 修改 build_city1d.py 读这个 CSV")

if __name__ == "__main__":
    main()
