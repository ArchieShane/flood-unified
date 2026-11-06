# -*- coding: utf-8 -*-
"""艾因新版客户库（AAM_SW_Data_25092026.gdb，原始版）→ 艾因推演用的单文件 GPKG（2026-10-17）。

原始版没有 Processed 版里的 StartCode / EndCode / StartX.. / Diameter / PointCode 等处理字段，这里补上，口径对齐旧 Processed 版：
  · 端点码 = "<X 取整>_<Y 取整>"（直接截断，不四舍五入；旧版 UNITID 重叠的管逐根对过）
  · 端点坐标取**几何**首末点，不取属性 XCOORD_START 等（22 行属性 X/Y 颠倒、378 行与几何差 > 5 m）
  · StartElev / EndElev 置 NaN（建网时地面一律取 5 m DTM，DTM 全覆盖；见 build_city1d.build_nodes）
  · UNITID 重复的管只留第一根
  · 出水口 PointCode 由点位坐标按同一规则生成
输出图层（名字同客户库，下游 build_city1d / aa_cases 直接读）：PIPELINE（只留建网需要的列）、OUTFALL、PS_PUMP、PS_WET_WELL_AREA、POND。
用法：python tools/aa_preprocess.py [GDB 路径] [输出 gpkg]
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pyogrio
import shapely

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "2026-10-17" / "AAM_SW_Data_25092026.gdb"
OUT = ROOT / "data" / "2026-10-17" / "AAM_SW_Processed.gpkg"
KEEP = ["UNITID", "STATUS", "PIPE_SUBTYPE", "PIPE_DIAMETER", "INVERT_LEVEL_UPSTREAM", "INVERT_LEVEL_DOWNSTREAM"]


def code(x, y):
    return np.char.add(np.char.add(np.floor(x).astype(np.int64).astype(str), "_"), np.floor(y).astype(np.int64).astype(str))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    src = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
    out = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else OUT
    if out.exists():
        out.unlink()
    g = pyogrio.read_dataframe(src, layer="PIPELINE", columns=KEEP + ["PIPE_LENGTH"])
    n0 = len(g)
    g = g.drop_duplicates("UNITID", keep="first").copy()
    print(f"管 {n0:,}，去 UNITID 重复 {n0 - len(g)} 根，留 {len(g):,}")
    geoms = shapely.force_2d(g.geometry.values)
    geoms = np.array([shapely.line_merge(m) if shapely.get_type_id(m) == 5 else m for m in geoms], dtype=object)  # MultiLineString → LineString（接得上的合并）
    s = np.array([shapely.get_coordinates(shapely.get_geometry(m, 0))[0] if shapely.get_type_id(m) == 5 else shapely.get_coordinates(m)[0] for m in geoms])
    e = np.array([shapely.get_coordinates(shapely.get_geometry(m, shapely.get_num_geometries(m) - 1))[-1] if shapely.get_type_id(m) == 5 else shapely.get_coordinates(m)[-1] for m in geoms])
    g["StartX"], g["StartY"], g["EndX"], g["EndY"] = s[:, 0], s[:, 1], e[:, 0], e[:, 1]
    g["StartCode"], g["EndCode"] = code(s[:, 0], s[:, 1]), code(e[:, 0], e[:, 1])
    g["StartElev"] = np.nan
    g["EndElev"] = np.nan
    g["LineLength"] = shapely.length(geoms)
    g["Diameter"] = g.PIPE_DIAMETER
    g = g.drop(columns=["PIPE_LENGTH"])
    g = g.set_geometry(geoms, crs="EPSG:32640")
    pyogrio.write_dataframe(g, out, layer="PIPELINE", driver="GPKG")
    print(f"PIPELINE → {out}")

    of = pyogrio.read_dataframe(src, layer="OUTFALL")
    of["PointCode"] = code(of.XCOORD.values, of.YCOORD.values)
    of["X_Coord"], of["Y_Coord"] = of.XCOORD, of.YCOORD
    pyogrio.write_dataframe(of, out, layer="OUTFALL", driver="GPKG", append=True)
    nodes = set(g.StartCode) | set(g.EndCode)
    print(f"OUTFALL {len(of)} 个，端点码能对上管网的 {sum(1 for k in of.PointCode if k in nodes)}")
    for lyr in ("PS_PUMP", "PS_WET_WELL_AREA", "POND"):
        d = pyogrio.read_dataframe(src, layer=lyr)
        pyogrio.write_dataframe(d, out, layer=lyr, driver="GPKG", append=True)
        print(f"{lyr} {len(d)} → {out.name}")
    print(f"完成，{out.stat().st_size / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
