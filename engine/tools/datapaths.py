# -*- coding: utf-8 -*-
"""客户原始数据放在哪：一处定义，建模脚本都从这里取（2026-09-30，为了别人换机器能复现）。

优先级：环境变量 SWBRAIN_DATA_<键大写> > config.toml 的 [data] > 下面的缺省值（= C1 首次计算时这台机器上的位置）。
键与用途见 docs/C1_资料清单.md 第一节；`python tools/c1_pipeline.py check` 会逐项检查在不在。
"""
from __future__ import annotations

import os
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]

DEFAULTS = {
    # 客户雨水管网 GDB：压缩包（全市一维重建读）与解压版（C1 泵站、压力管、出水口读）
    "gdb_zip": "D:/阿布扎比/DMT_StormWater_AbuDhabi_Processed.gdb.zip",
    "gdb_dir": "D:/阿布扎比/DMT_StormWater_AbuDhabi_Processed.gdb/DMT_StormWater_AbuDhabi_Processed.gdb",
    # 客户热点工作簿目录（Stormwater Hotspot Analysis *.xlsx）
    "hotspot_xlsx_dir": "D:/世界模型/FloodData_Batch1/Flood Management Data - Origen - Batch1",
    # 客户 22 个水力模型（shapefile 的 zip）：汇水区以此为准
    "client_models_zip": "D:/世界模型/OneDrive_1_9-26-2026.zip",
    # 客户 SWMM 模型导出（含 Mussafah_00.inp：取设计雨型）
    "client_swmm_export": "D:/世界模型/Hydraulic Model Data Export/SWMM_Export",
    # Parsons 交付（已解压的 Parsons_Model Data Export.rar：Critical Hotspots/ 与 26092026/）
    "parsons_root": "D:/阿布扎比/Parsons_Model Data Export",
}
# 已在别的配置节里的两项：5 m DTM = [terrain].dtm_path，热点坐标修正 KMZ = [hotspots].coords_kmz


def _cfg() -> dict:
    p = ROOT / "config.toml"
    if not p.exists():
        p = ROOT / "config.example.toml"
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get(key: str) -> pathlib.Path:
    env = os.environ.get(f"SWBRAIN_DATA_{key.upper()}")
    if env:
        return pathlib.Path(env)
    if key == "dtm":
        v = (_cfg().get("terrain") or {}).get("dtm_path")
    elif key == "hotspot_kmz":
        v = (_cfg().get("hotspots") or {}).get("coords_kmz")
    else:
        v = (_cfg().get("data") or {}).get(key) or DEFAULTS[key]
    return pathlib.Path(v) if v else None


def parsons(*parts: str) -> pathlib.Path:
    return get("parsons_root").joinpath(*parts)


ALL = list(DEFAULTS) + ["dtm", "hotspot_kmz"]
