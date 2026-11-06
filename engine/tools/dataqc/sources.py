# -*- coding: utf-8 -*-
"""读数据源：文件地理库（.gdb 或 .gdb.zip）、GeoPackage、shapefile 目录。只读。

列名一律转小写（与平台用 ogr2ogr 导入暂存库后的样子一致，判定规则按小写写的）。
有 pyogrio 用 pyogrio，没有（ArcGIS Pro 自带的环境）用 fiona。
"""
from __future__ import annotations

import os
import zipfile

import geopandas as gpd

try:
    import pyogrio  # noqa: F401
    _ENGINE = "pyogrio"
except ImportError:  # ArcGIS Pro 的 arcgispro-py3 里只有 fiona
    _ENGINE = "fiona"


def normalize_path(path: str) -> str:
    """把 .gdb.zip 变成 GDAL 能直接读的虚拟路径；目录与文件原样返回。"""
    p = os.path.abspath(path)
    if p.lower().endswith(".zip"):
        with zipfile.ZipFile(p) as z:
            inside = sorted({n.split("/")[0] for n in z.namelist() if n.split("/")[0].lower().endswith(".gdb")})
        if not inside:
            raise ValueError("zip 里没有 .gdb 目录：%s" % path)
        p = p.replace("\\", "/")
        return ("/vsizip/%s/%s" if _ENGINE == "pyogrio" else "zip://%s!%s") % (p, inside[0])
    # 解压时常多套一层：X.gdb/X.gdb/a00000001.gdbtable。外层没有 gdbtable 就往里找一层
    if os.path.isdir(p) and p.lower().endswith(".gdb") and not any(f.endswith(".gdbtable") for f in os.listdir(p)):
        inside = [dia for dia in os.listdir(p) if dia.lower().endswith(".gdb") and os.path.isdir(os.path.join(p, dia))]
        if len(inside) == 1:
            return os.path.join(p, inside[0])
    return p


def list_layers(path: str) -> list[tuple[str, str]]:
    """[(层名, 几何类型)]。shapefile 目录按文件列。"""
    p = normalize_path(path)
    if os.path.isdir(p) and not p.lower().endswith(".gdb"):
        return [(os.path.splitext(f)[0], _geom_type(os.path.join(p, f), None)) for f in sorted(os.listdir(p)) if f.lower().endswith(".shp")]
    if _ENGINE == "pyogrio":
        import pyogrio
        return [(str(n), str(g)) for n, g in pyogrio.list_layers(p)]
    import fiona
    return [(n, _geom_type(p, n)) for n in fiona.listlayers(p)]


def _geom_type(p: str, layer: str | None) -> str:
    if _ENGINE == "pyogrio":
        import pyogrio
        return str(pyogrio.read_info(p, layer=layer)["geometry_type"])
    import fiona
    with fiona.open(p, layer=layer) as src:
        return str(src.schema.get("geometry"))


def read_layer(path: str, layer: str) -> gpd.GeoDataFrame:
    p = normalize_path(path)
    if os.path.isdir(p) and not p.lower().endswith(".gdb"):
        gdf = gpd.read_file(os.path.join(p, layer + ".shp"))
    else:
        gdf = gpd.read_file(p, layer=layer)
    gdf.columns = [counter if counter == gdf.geometry.name else counter.lower() for counter in gdf.columns]
    return gdf
