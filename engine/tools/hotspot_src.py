"""优化工具读客户热点的统一入口（2026-10-27）：console/data/hotspots.geojson，另可附加「模型高后果积水点」作为目标点。

用户 2026-10-27：优化目标扩大到「客户热点 + 模型算出的高后果积水点」。附加的目标点放在一个 GeoJSON 里（WGS84，属性 hotspot_id = HC-xx、criticality），
用环境变量 SWBRAIN_HOTSPOTS_EXTRA 指过来；不设时与原来完全一样（只读客户热点）。附加点不写进客户热点文件，系统页面不受影响。
"""
from __future__ import annotations
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLIENT = ROOT / "console" / "data" / "hotspots.geojson"


def load(crs: int | None = 32640):
    import geopandas as gpd
    import pandas as pd
    H = gpd.read_file(CLIENT)
    extra = os.environ.get("SWBRAIN_HOTSPOTS_EXTRA")
    if extra:
        X = gpd.read_file(extra)
        if X.crs is None:
            X = X.set_crs(4326)
        X = X.to_crs(H.crs)
        H = gpd.GeoDataFrame(pd.concat([H, X[[c for c in X.columns if c in H.columns or c == "geometry"]]], ignore_index=True), crs=H.crs)
    return H.to_crs(crs) if crs else H
