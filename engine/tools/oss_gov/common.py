"""开源治理引擎 PoC 公共部分：路径、预处理类 → Expanded 映射、读要素。

只读 D:/swbrain_work/oss_gov/input/ 里的快照（o0_snapshot.py 复制的），产物写 D:/swbrain_work/oss_gov/out/。
不 import arcpy；只用系统 python 的 geopandas / pyogrio / shapely / numpy / scipy / pandas。
"""
import json
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pyogrio
import shapely

warnings.filterwarnings("ignore", message="Measured")

import os
WORK = Path(os.environ.get("OSS_GOV_WORK", r"D:/swbrain_work/oss_gov"))       # 开源路线完整流程（g_run.sh）传入自己的工作目录
INP = WORK / "input"
OUT = WORK / "out"
PREP = Path(os.environ.get("OSS_GOV_PREP", INP / "prep.gdb"))
AP = Path(os.environ.get("OSS_GOV_AP", INP / "Expanded_local.gdb"))
REP = INP / "Stormwater_AD_reporting.gdb"
UN_MG = INP / "Stormwater_AD.geodatabase"           # UN 移动库副本（SQLite，只读打开）
SUMMARY = INP / "Summary.geodatabase"               # UDMS SummarizeUNErrors 产物副本
TOL = 0.001                                         # UN 的 x / y / z 容差（m），见 UN 移动库 GDB_Items：XYTolerance = 0.001
# 重合判定口径（argv[1]）：
#   e1 = 欧氏距离 ≤ 0.001 m（官方容差字面口径）
#   c2 = 每个坐标轴都 ≤ 2 × 0.001 m（切比雪夫距离）。经验口径（推断）：UN 第八轮错误表里有 0.0022 m 的点位重叠 / 连通不唯一、
#        0.002–0.0028 m 的重复折点，说明 UN 实际把这个范围当重合；Esri 聚类时每个点最多移动 1 个容差，两点各移一次最多相差 2 个容差
MODES = {"e1": ("欧氏 ≤ 0.001 m", 2.0, TOL), "c2": ("各轴 ≤ 0.002 m", float("inf"), 2 * TOL + 1e-7)}
MODE = "c2"

# 照抄 tools/exp_gov/s3_mapping.py 的 MAP（只读引用，不 import 那个文件，因为它顶上 import arcpy）
# (预处理类, 类型值) → (目标类, 资产组, 资产类型)
MAP = {
    ("MH_VAULT", None): ("StructureJunction", "Sewer Storm Vault", "Manhole"),
    ("MH_CHANNEL", None): ("StormwaterDevice", "Manhole Channel", "Manhole Channel"),
    ("PIPE", "Gravity Pipe"): ("StormwaterLine", "Gravity Pipe", "Stormwater Gravity Pipe"),
    ("PIPE", "Perforated Pipe"): ("StormwaterLine", "Perforated Pipe", "Stormwater Perforated Pipe"),
    ("PIPE", "Force Pipe"): ("StormwaterLine", "Force Pipe", "Stormwater Force Pipe"),
    ("CATCH_BASIN", "Drop Inlet"): ("StormwaterDevice", "Catch Basin", "Drop Inlet"),
    ("CATCH_BASIN", "Infiltration"): ("StormwaterDevice", "Catch Basin", "Infiltration"),
    ("CATCH_BASIN", "Sediment Trap"): ("StormwaterDevice", "Catch Basin", "Sediment Trap"),
    ("END_CAP", None): ("StormwaterJunction", "Fitting", "End Cap"),
    ("OUTFALL", None): ("StormwaterDevice", "Outfall", "Non-Permit Outfall"),
    ("FLAP_GATE", None): ("StormwaterDevice", "Outlet", "Flap Gate"),
    ("PUMP", None): ("StormwaterDevice", "Pump", "Pump"),
    ("WETWELL", None): ("StormwaterDevice", "Connection", "Pipe Connection"),
    ("GEN_CONNECTION", None): ("StormwaterJunction", "Fitting", "Transition"),
    ("SYSTEM_OUTFLOW", None): ("StormwaterDevice", "System Outflow", "Outflow"),
    ("BMP_POINT", None): ("StormwaterDevice", "BMP Point", "BMP Point"),
    ("POND", "Retention Basin"): ("StructureBoundary", "BMP Boundary", "Retention Basin"),
    ("POND", "Detention Basin"): ("StructureBoundary", "BMP Boundary", "Detention Basin"),
    ("POND", "Sediment Trap"): ("StructureBoundary", "BMP Boundary", "Sediment Trap"),
}
# 规则表（B_Rules）里的类名：雨水域网络 1 用 Device / Junction / Line
RULE_FC = {"StormwaterDevice": "Device", "StormwaterJunction": "Junction", "StormwaterLine": "Line"}
# 预处理库里不进 UN 的类（记录 / 移动泵 / AECOM 汇水区）
NOT_LOADED = {"MOVABLE_PUMP", "AECOM_CATCHMENT", "EXCLUDED", "ISSUES", "CONFLICTS"}
COLS = ["GlobalID", "assetid", "assetstate", "src_layer", "src_oid", "src_key"]


class Timer:
    """记每段用时（秒），写进各步的 json。"""

    def __init__(self):
        self.t0 = self.t = time.time()
        self.log = {}

    def lap(self, name):
        now = time.time()
        self.log[name] = round(now - self.t, 2)
        self.t = now
        print(f"  [{name}] {self.log[name]} s", flush=True)

    def total(self):
        self.log["合计"] = round(time.time() - self.t0, 2)
        return self.log


def setup(argv):
    """按命令行选重合口径，产物放 out/<口径>/。"""
    global MODE, OUT
    MODE = argv[1] if len(argv) > 1 else "c2"
    assert MODE in MODES, MODES
    OUT = WORK / "out" / MODE
    OUT.mkdir(parents=True, exist_ok=True)
    print("重合口径", MODE, MODES[MODE][0], flush=True)
    return MODE


def prep_classes():
    """预处理库里的类及要素数。"""
    return {n: pyogrio.read_info(PREP, layer=n)["features"] for n, _ in pyogrio.list_layers(PREP)}


def read_prep(layer):
    """读一个预处理类（arrow 读），补上 Expanded 目标类 / 资产组 / 资产类型。"""
    info = pyogrio.read_info(PREP, layer=layer)
    want = [c for c in COLS + ["tgt_ag", "tgt_at", "anchor_x", "anchor_y", "anchor_assetid"] if c in info["fields"]]
    df = pyogrio.read_dataframe(PREP, layer=layer, columns=want, use_arrow=True)
    df["layer"] = layer
    df["fid"] = np.arange(len(df))          # 本类内顺序 = 装载顺序（推断：UN 里按这个顺序追加）
    typ = df["tgt_ag"] if "tgt_ag" in df else (df["tgt_at"] if "tgt_at" in df else pd.Series([None] * len(df)))
    m = [MAP.get((layer, t)) or MAP.get((layer, None)) for t in typ]
    df["un_class"] = [x[0] if x else None for x in m]
    df["ag"] = [x[1] if x else None for x in m]
    df["at"] = [x[2] if x else None for x in m]
    return df


def jdump(obj, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))


def norm_gid(s):
    return s.astype(str).str.upper().str.strip("{}")


def coords_xy(geoms):
    return shapely.get_coordinates(geoms)
