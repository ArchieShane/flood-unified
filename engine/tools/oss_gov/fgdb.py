"""开源路线写文件地理数据库（GDAL OpenFileGDB）的唯一入口：坐标网格和 ArcGIS 完全一致。

为什么必须一致：DMT 坐标多数到毫米，约 1/10 的坐标第三位小数是 5，正好在「按厘米取整」的边界上（子网命名、剩余分析都按厘米取整判相连）。
GDAL 缺省网格写出的 271161.257 读回来是 271161.25699997，取整方向就反了——RB-01 前的第一次全流程试跑，子网名因此差了 19 个。
网格取 ArcGIS 为 UTM 40N（EPSG:32640）建库时的缺省值（读自 ArcGIS 写的 prep.gdb 的 GDB_Items）。
"""
import pyogrio

GRID = {"XORIGIN": -5120900, "YORIGIN": -9998100, "XYSCALE": 10000, "XYTOLERANCE": 0.001,
        "ZORIGIN": -100000, "ZSCALE": 10000, "ZTOLERANCE": 0.001}


def grid_unknown_sr(bounds):
    """ArcGIS 导入没有 .prj 的数据时自己算的坐标网格（由 RD-A AECOM 7 层反推，逐层核对一致）：
    原点 = 范围最小值 − 2.5 % × 该方向范围；精度 = 2^53 ÷（1.05 × 长边）；单点层（范围 0）：原点 = 坐标 − 0.105、长边按 0.2"""
    x0, y0, x1, y1 = bounds
    W, H = x1 - x0, y1 - y0
    if W == 0 and H == 0:
        return {"XORIGIN": x0 - 0.105, "YORIGIN": y0 - 0.105, "XYSCALE": 2 ** 53 / 0.21}
    return {"XORIGIN": x0 - 0.025 * W, "YORIGIN": y0 - 0.025 * H, "XYSCALE": 2 ** 53 / (1.05 * max(W, H))}


def write(df, gdb, layer, grid=None, **kw):
    """pyogrio.write_dataframe 到 .gdb，带 ArcGIS 的坐标网格（只对有几何的层生效）；grid 给了就用它（如没有 .prj 的源数据）"""
    opts = dict(kw.pop("layer_options", {}) or {})
    if "geometry" in getattr(df, "columns", []):
        g = dict(GRID, **(grid or {}))
        opts.update({k: repr(float(v)) if not isinstance(v, int) else str(v) for k, v in g.items()})
    pyogrio.write_dataframe(df, str(gdb), layer=layer, driver="OpenFileGDB", layer_options=opts or None, **kw)
