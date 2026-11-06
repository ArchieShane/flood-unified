"""第 2 步前置（第六轮，用户 2026-10-24 定统一高程基准）：给 DMT 每条记录采 DTM 地面高程，供 s2_prep 判断高程基准。

  python tools/exp_gov/s2a_dtm_sample.py <工作目录>       （系统 Python：rasterio；先用 ArcGIS 环境导出坐标）

DMT 的高程绝大多数是「实际高程 + 100 m」（井盖 − DTM：99.1 % 落在 95–105 m，中位 100 m；AECOM 模型和 DTM 为正常基准）。
产物 <工作目录>/02_prep/dtm_at_dmt.csv：layer, oid, x, y, dtm（管道采两端：oid 后缀 _s / _e）
"""
import csv
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import rb01_build as RB  # noqa: E402

ARC = r"C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe"
DUMP = r'''
import arcpy, csv, sys
g = sys.argv[1] + "/src_dmt.gdb/"
with open(sys.argv[1] + "/02_prep/dmt_xy.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["layer", "oid", "x", "y"])
    for lay in ("MANHOLECHAMBER", "INLET", "CATCHBASIN", "SOAKAWAY", "PETROLINTERCEPTOR", "SW_CAPPEDEND", "OUTFALL", "PUMP"):
        for oid, xy in arcpy.da.SearchCursor(g + lay, ["OID@", "SHAPE@XY"]):
            w.writerow([lay, oid, xy[0], xy[1]])
    for oid, s in arcpy.da.SearchCursor(g + "PIPELINE", ["OID@", "SHAPE@"]):
        if s is None:
            continue
        w.writerow(["PIPELINE", f"{oid}_s", s.firstPoint.X, s.firstPoint.Y])
        w.writerow(["PIPELINE", f"{oid}_e", s.lastPoint.X, s.lastPoint.Y])
    for oid, s in arcpy.da.SearchCursor(g + "POND", ["OID@", "SHAPE@"]):
        c = s.centroid
        w.writerow(["POND", oid, c.X, c.Y])
'''


def main(work):
    out = Path(work) / "02_prep"
    out.mkdir(parents=True, exist_ok=True)
    script = out / "_dump_xy.py"
    script.write_text(DUMP, encoding="utf-8")
    import os
    if os.environ.get("EXP_ENGINE") == "oss":            # 开源路线：同一段导出脚本，用开源替身跑（tools/oss_gov/run_shim.py）
        subprocess.run([sys.executable, str(HERE.parent / "oss_gov" / "run_shim.py"), str(script), work], check=True)
    else:
        subprocess.run([ARC, "-I", str(script), work], check=True)
    import pandas as pd
    import rasterio
    d = pd.read_csv(out / "dmt_xy.csv", dtype={"oid": str})
    with rasterio.open(RB.DTM) as ds:
        z = np.array([v[0] for v in ds.sample(zip(d.x, d.y))], float)
        z[(z < -100) | (z > 1000) | ~np.isfinite(z)] = np.nan
    d["dtm"] = np.round(z, 3)
    d.to_csv(out / "dtm_at_dmt.csv", index=False)
    print(len(d), "条；DTM 缺值", int(np.isnan(z).sum()))


if __name__ == "__main__":
    main(sys.argv[1])
