# -*- coding: utf-8 -*-
"""自检：在一份合成数据上跑一遍全部判定，核对该报的都报出来了。不碰任何客户数据。

    python -m dataqc.selftest

装完依赖先跑这个。全绿说明环境齐、判定正常；有红字就是环境缺东西或判定被改坏了。
"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, Point

from . import network, raster
from .thresholds import Thresholds

# ── 合成管网：20 条管、14 个井，故意埋 8 处已知问题 ──
# 每行：(起点井, 终点井, 管径 mm, 上游管底, 下游管底, 材质, 属性管长, 上游编号, 下游编号, 起点x)
pipes = [
    ("P01", "W1", "W2", 300, 10.0, 9.5, "uPVC", 50.5, "W1", "W2", 0),
    ("P02", "W2", "W3", 300, 9.5, 9.0, "uPVC", 50.5, "W2", "W3", 50),
    ("P03", "W3", "OUT1", 400, 9.0, 8.5, "uPVC", 50.5, "W3", "OUT1", 100),
    ("P04", "W1", "W4", None, 10.0, 9.6, "uPVC", 40.4, "W1", "W4", 0),          # A1 缺管径
    ("P05", "W5", "W6", 300, 9.8, 9.4, "undefined", 40.4, "W5", "W6", 0),        # A7 占位材质
    ("P06", "W6", "W7", 300, 9.4, 9.0, "uPVC", 40.4, "W6", "W7", 40),
    ("P07", "W7", "W8", 300, 9.0, 8.6, "uPVC", 40.4, "W7", "W8", 80),
    ("P08", "W9", "W10", 300, 9.0, 9.5, "uPVC", 50.5, "W9", "W10", 0),           # D1 逆坡
    ("P09", "W10", "W11", 300, None, 9.0, "uPVC", 50.5, "W10", "W11", 50),       # A4 缺管底标高
    ("P10", "W12", "W13", 300, 9.0, 8.5, "uPVC", 50.5, "W12", "W99", 0),         # C8 编号指向不存在的井
    ("P11", "W13", "OUT2", 250, 8.5, 8.0, "uPVC", 50.5, "W13", "OUT2", 50),
    ("P12", "W1", "W14", 300, 10.0, 9.5, "uPVC", 50.5, "W1", "W14", 0),          # 孤立子网（无出口）
    ("P13", "W14", "W15", 300, 9.5, 9.0, "uPVC", 50.5, "W14", "W15", 50),
    ("P14", "W16", "W17", 300, 9.0, 8.5, "uPVC", 50.5, "W16", "W17", 0),         # 悬挂端点（W17 处没接上别的管）
]
nodes = [
    ("W1", 0, 0), ("W2", 50, 0), ("W3", 100, 0), ("W4", 0, -40), ("W5", 0, 20), ("W6", 40, 20), ("W7", 80, 20),
    ("W8", 120, 20), ("W9", 0, 60), ("W10", 50, 60), ("W11", 100, 60), ("W12", 0, 120), ("W13", 50, 120),
    ("W14", 0, -80), ("W15", 50, -80), ("W16", 200, 200), ("W17", 250, 200),
    ("W18", 50, 0.02),          # B8 与 W2 相距 2 cm（不同编号）
]
outfalls = [("OUT1", 150, 0), ("OUT2", 60, 90)]   # 不与任何井重合：井离得太近会被 B8（重复节点）报出来
EXPECTED = {
    "A1": 1,      # P04 缺管径
    "A4": 1,      # P09 缺管底标高
    "A7": 1,      # P05 材质 undefined
    "B8": 1,      # W2 ≈ W18
    "C8": 1,      # P10 的下游编号 W99 不存在
    "D1": 1,      # P08 逆坡（下游比上游高）
}


def make_case(case: str) -> tuple[str, str]:
    """写一份合成数据，返回 (管网 gpkg, DTM tif)。case 是目录，数据写在它下面。"""
    os.makedirs(case, exist_ok=True)
    gpkg = os.path.join(case, "net.gpkg")
    ns = []
    for uid, x, y in nodes:
        ns.append({"UNITID": uid, "INVERT_LEVEL": 8.0, "COVER_LEVEL": 12.0, "geometry": Point(x, y),
                   "layer": "CHAMBER"})
    for uid, x, y in outfalls:
        ns.append({"UNITID": uid, "INVERT_LEVEL": 7.5, "COVER_LEVEL": 12.0, "geometry": Point(x, y), "layer": "OUTFALL"})
    chan = [r for r in ns if r["layer"] == "CHAMBER"]
    out = [r for r in ns if r["layer"] == "OUTFALL"]
    for rows, name in ((chan, "CHAMBER"), (out, "OUTFALL")):
        g = gpd.GeoDataFrame(pd.DataFrame(rows).drop(columns=["layer"]), geometry="geometry", crs=32640)
        g.to_file(gpkg, layer=name, driver="GPKG")
    ls = []
    for uid, us, ds, dia, inv_us, inv_ds, mat, ln, us_id, ds_id, x0 in pipes:
        # P08 在 y=60 那一排（逆坡那条），P14 在 y=200（悬挂端点那条），其余在 y=0
        y = 0 if uid not in ("P08", "P14") else (60 if uid == "P08" else 200)
        ls.append({"UNITID": uid, "ASSET_BEFORE": us_id, "ASSET_AFTER": ds_id, "PIPE_DIAMETER": dia,
                   "INVERT_LEVEL_UPSTREAM": inv_us, "INVERT_LEVEL_DOWNSTREAM": inv_ds, "PIPE_LENGTH": ln,
                   "PIPE_MATERIAL": mat, "STATUS": "In Service",
                   "geometry": LineString([(x0, y), (x0 + 50, y)])})
    gpd.GeoDataFrame(pd.DataFrame(ls), geometry="geometry", crs=32640).to_file(gpkg, layer="PIPELINE", driver="GPKG")
    # DTM：40×40，5 m。一个尖峰、一个深坑、一片低于 −5 m、一片无数据
    tif = os.path.join(case, "dtm.tif")
    import rasterio
    from rasterio.transform import from_origin
    z = np.full((40, 40), 10.0, dtype="float32")
    z[10, 10] = 20.0                 # 单点尖峰（比 8 邻格高 10 m）
    z[20, 20] = -3.0                 # 单点深坑（比 8 邻格低 13 m）
    z[30:33, 5:9] = -8.0             # 连片低于 −5 m
    z[35:40, 35:40] = -9999.0        # 无数据
    with rasterio.open(tif, "w", driver="GTiff", height=40, width=40, count=1, dtype="float32", crs=32640,
                       transform=from_origin(0, 250, 5, 5), nodata=-9999.0) as ds:
        ds.write(z, 1)
    return gpkg, tif


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("dataqc 自检：在合成数据上跑一遍全部判定\n  合成数据写到临时目录：", end="", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        print(tmp)
        shp, tif = make_case(tmp)
        print("  读管网 …", flush=True)
        net = network.Network(shp, log=lambda *a: None)
        res = network.check_network(net, Thresholds(), tolerance=0.5, has_dtm=True)
        C = res["checks"]
        bad = []
        for k, want in EXPECTED.items():
            got = C[k]["count"]
            ok = got == want
            print("  %-4s 应报 %-4d 实报 %-4d  %s" % (k, want, got, "OK" if ok else "不符"))
            if not ok:
                bad.append("%s 应 %d 实 %d" % (k, want, got))
        for k in ("A1", "A4", "A7", "C4", "C8", "D1"):
            if k not in C:
                bad.append("没算出 %s" % k)
        print("  管网：%d 条管段，%d 个节点，%d 个连通分量，%d 条所在的子网没有出口" % (
            res["summary"]["links"], res["summary"]["nodes"], res["summary"]["components"], C["C4"]["count"]))
        print("  读 DTM …", flush=True)
        import rasterio
        with rasterio.open(tif) as ds:
            net.sample_dtm(ds)
            R = raster.scan(tif, Thresholds())      # 范围不给 ＝ 全图；给窄了尖峰会落在窗口边上，8 邻格不全就不判
        ck = {c["code"]: c for c in R["checks"]}
        for code, want in (("R9", 1), ("R10", 1)):
            got = int(str(ck[code]["value"]).split("/")[0].replace(",", ""))
            print("  %-4s 应报 %-4d 实报 %-4d  %s" % (code, want, got, "OK" if got == want else "不符"))
            if got != want:
                bad.append("%s 应 %d 实 %d" % (code, want, got))
        n_lows = len(R["lows"])
        print("  R12 低于 −5 m 的连片区：%d 处 %s" % (n_lows, "OK" if n_lows == 1 else "不符（应 1）"))
        if n_lows != 1:
            bad.append("R12 应 1 实 %d" % n_lows)
        # 模型地形：合成 2 km × 2 km 平地 DTM（10 m），20 m 单元；埋一个 NoData 高墙、一个 −661 m 深坑、一个抬高 10 m 的格
        print("  模型地形 …", flush=True)
        from . import model_terrain
        import rasterio
        from rasterio.transform import from_origin
        dtm2 = os.path.join(tmp, "flat.tif")
        with rasterio.open(dtm2, "w", driver="GTiff", height=400, width=400, count=1, dtype="float32", crs=32640,
                           transform=from_origin(0, 2000, 5, 5), nodata=-9999.0) as ds:
            ds.write(np.full((400, 400), 10.0, dtype="float32"), 1)
        gx, gy = np.meshgrid(np.arange(50, 1950, 20.0), np.arange(50, 1950, 20.0))
        xy = np.c_[gx.ravel(), gy.ravel()]
        elev = np.full(len(xy), 10.0)
        elev[0], elev[1], elev[2] = 3.4e38, -661.0, 20.0
        npz = os.path.join(tmp, "mesh_2d.npz")
        np.savez(npz, xy=xy, elev=elev, area=np.full(len(xy), 400.0))
        mt, _, _, _ = model_terrain.run(npz, dtm2, Thresholds(), log=lambda *a: None)
        got = {c["code"]: c.get("cells") for c in mt["checks"]}
        for code, want in (("M1", 1), ("M2", 1), ("M4", 1), ("M4b", 0)):
            print("  %-4s 应报 %-4d 实报 %-4d  %s" % (code, want, got[code], "OK" if got[code] == want else "不符"))
            if got[code] != want:
                bad.append("%s 应 %d 实 %d" % (code, want, got[code]))
        # 报告能不能出来
        from . import report
        out = os.path.join(tmp, "report.html")
        report.build_report(out, net_result=res, net_meta=net.meta(), dtm=R, dtm_scope_note="自检", thr=Thresholds())
        size = os.path.getsize(out)
        print("  报告：%s（%s 字节）%s" % (os.path.basename(out), format(size, ","), "OK" if size > 20000 else "太小，可疑"))
        if size <= 20000:
            bad.append("报告只有 %d 字节" % size)
    print()
    if bad:
        print("自检不通过：")
        for x in bad:
            print("  - " + x)
        return 1
    print("自检全部通过。可以拿真实数据跑了（见 使用说明.md）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
