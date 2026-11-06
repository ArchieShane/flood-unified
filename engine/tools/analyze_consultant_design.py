import geopandas as gpd
import numpy as np
from pathlib import Path

consultant_dir = Path(r'D:\阿布扎比\排水智能体\20261019\test\RD-A\RD-A\2024-1hr')

print("咨询公司 RD-A 2024-1hr 设计分析\n" + "="*60)

# 1. 压力管
pp_file = consultant_dir / 'Pressure Pipe.shp'
if pp_file.exists():
    pp = gpd.read_file(pp_file)
    print(f"\n[1] 压力管 (Pressure Pipe): {len(pp)} 条")
    print(f"    字段: {list(pp.columns[:10])}")

    if len(pp) > 0:
        lengths = [g.length for g in pp.geometry if hasattr(g, 'length')]
        if lengths:
            print(f"    总长: {sum(lengths):.0f} m")
            print(f"    平均: {np.mean(lengths):.0f} m")
            print(f"    最长: {np.max(lengths):.0f} m")

            # 看看几何类型
            geom_types = pp.geometry.geom_type.value_counts()
            print(f"    几何类型: {dict(geom_types)}")
else:
    print(f"\n[1] 压力管文件不存在")

# 2. 重力管
conduit_file = consultant_dir / 'Conduit.shp'
if conduit_file.exists():
    conduit = gpd.read_file(conduit_file)
    print(f"\n[2] 重力管 (Conduit): {len(conduit)} 条")

    if len(conduit) > 0:
        lengths = [g.length for g in conduit.geometry if hasattr(g, 'length')]
        if lengths:
            print(f"    总长: {sum(lengths):.0f} m")
            print(f"    平均: {np.mean(lengths):.0f} m")

# 3. 检查井
mh_file = consultant_dir / 'Manholes.shp'
if mh_file.exists():
    mh = gpd.read_file(mh_file)
    print(f"\n[3] 检查井 (Manholes): {len(mh)} 个")

# 4. 泵和集水井
pump_file = consultant_dir / 'Pump.shp'
ww_file = consultant_dir / 'Wetwell.shp'
if pump_file.exists():
    pump = gpd.read_file(pump_file)
    print(f"\n[4] 泵站 (Pump): {len(pump)} 个")
if ww_file.exists():
    ww = gpd.read_file(ww_file)
    print(f"    集水井 (Wetwell): {len(ww)} 个")

print("\n" + "="*60)
print("咨询公司用了完整的设施清单，而我们的候选生成只有 5 种简化类型。")
