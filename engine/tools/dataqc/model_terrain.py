# -*- coding: utf-8 -*-
"""模型地形体检：模型**实际用的**地形，逐单元对原始 DTM。只读。

为什么要有：原始 DTM 干净，不代表模型地形干净。重采样、取块平均、赋给三角网这几步都可能出错，
比如 NoData（±3.4e38）混进平均变成「高墙」，或者负的大数变成「深坑」（adm_city_250m_v2 就是这样，2026-10-21）。
raster.py 的 R1–R14 只查原始栅格，查不到这一步。

输入（三选一，按扩展名认）：
  *.npz   二维结果的逐帧文件：要有 xy（单元形心）和 elev；有 area 时窗口边长取 sqrt(area)，没有就用 --cell
  *.sww   ANUGA 结果：x、y（加 xllcorner / yllcorner）、volumes、elevation（顶点）或 elevation_c（单元）
  栅格     重采样后的 DEM（GeoTIFF 等）：每格一个单元，窗口 = 格子本身
参照：原始 DTM 在单元范围内（以形心为中心、边长 = 单元边长的方窗）有效值的平均。
      原始 DTM 先按「单元边长 / 6」平均降采样，再用积分图求每个窗口的平均，全市 9 万个单元几十秒。
坐标系：npz、sww 不带坐标系，缺省认为与 DTM 相同；不同时用 --terrain-crs 给 EPSG。

判定：
  M1 无效值        非有限数、|z| > 1e30、等于栅格声明的 NoData                       → 严重
  M2 越界          低于 高程下限 或高于 高程上限（同 R6）                             → 严重
  M3 参照缺失      单元范围内原始 DTM 全是 NoData 或在图外（海上常见），无从核对    → 提示
  M4 与 DTM 偏差   |单元高程 − 参照| 超过 模型地形偏差提示 的单元分两种（只算 M1、M2、M3 以外的）：
                   - 超出周边地形范围：单元高程比形心周围方窗内原始 DTM 的最低还低、或最高还高（方窗边长 = 2 × max(单元边长,
                     --block 给的取块平均块大小)：块平均不可能超出块内的最低～最高，所以超出就是处理错），
                     且差过 模型地形偏差提示 → 处理错（假墙、假坑）；差过 模型地形偏差严重 → 严重，否则提示
                   - 平滑：差得多但仍在周边地形范围内（沙丘、坡地上取块平均的正常结果）→ 提示
  M5 系统偏差      全部可比单元偏差的中位数超过 模型地形系统偏差 → 需处理（基准不一致）
  M6 偏差分布      分位数，供判断（提示）

在仓库里：python -m tools.dataqc.model_terrain --terrain <npz|sww|tif> --dtm <原始 DTM> --out <目录> [--gpkg]
独立包里：python -m dataqc.model_terrain ...
产物：<out>/model_terrain.json、model_terrain.md（报告），--gpkg 时加 model_terrain.gpkg（问题单元点层）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter

import numpy as np

from .thresholds import Thresholds

BAD = 1e30
N_SAMPLE = 20
TILE_M = 5000.0


# ------------------------------------------------------------------ 读模型地形
def read_terrain(path, cell=None):
    """返回 dict(x, y, z, side, kind, nodata)：每个单元的形心、高程、窗口边长（米）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".npz":
        d = np.load(path)
        if "xy" not in d.files or "elev" not in d.files:
            raise SystemExit("npz 里要有 xy 和 elev（现有：%s）" % ", ".join(d.files))
        xy = d["xy"].astype(float)
        if "area" in d.files:
            side = np.sqrt(np.abs(d["area"].astype(float)))
        elif cell:
            side = np.full(len(xy), float(cell))
        else:
            raise SystemExit("npz 里没有 area，要用 --cell 给单元边长（米）")
        return dict(x=xy[:, 0], y=xy[:, 1], z=d["elev"].astype(float), side=side, kind="二维单元（npz）", nodata=None)
    if ext == ".sww":
        try:                                            # 有 netCDF4 用它；没有就用 scipy（ANUGA 的 sww 是 NetCDF3，scipy 读得了）
            import netCDF4
            ds = netCDF4.Dataset(path)
        except ImportError:
            from scipy.io import netcdf_file
            ds = netcdf_file(path, "r", mmap=False)
        att = lambda k: float(getattr(ds, k, 0.0) if not isinstance(getattr(ds, k, 0.0), bytes) else getattr(ds, k).decode())
        x = np.asarray(ds.variables["x"][:], dtype=float) + att("xllcorner")
        y = np.asarray(ds.variables["y"][:], dtype=float) + att("yllcorner")
        tri = np.asarray(ds.variables["volumes"][:]).astype(int)
        if "elevation_c" in ds.variables:
            zc = np.asarray(ds.variables["elevation_c"][:], dtype=float)
            zc = zc[0] if zc.ndim == 2 else zc
        else:
            ze = np.asarray(ds.variables["elevation"][:], dtype=float)
            ze = ze[0] if ze.ndim == 2 else ze
            zc = ze[tri].mean(axis=1)
        cx, cy = x[tri].mean(axis=1), y[tri].mean(axis=1)
        a = 0.5 * np.abs((x[tri[:, 1]] - x[tri[:, 0]]) * (y[tri[:, 2]] - y[tri[:, 0]]) - (x[tri[:, 2]] - x[tri[:, 0]]) * (y[tri[:, 1]] - y[tri[:, 0]]))
        ds.close()
        return dict(x=cx, y=cy, z=zc, side=np.sqrt(a), kind="二维单元（sww）", nodata=None)
    import rasterio
    with rasterio.open(path) as ds:
        if ds.width * ds.height > 60_000_000:
            raise SystemExit("栅格 %d × %d 太大：先裁到计算域再查" % (ds.width, ds.height))
        z = ds.read(1).astype(float)
        rows, cols = np.mgrid[0:ds.height, 0:ds.width]
        xs, ys = rasterio.transform.xy(ds.transform, rows.ravel(), cols.ravel())
        res = abs(ds.res[0])
        return dict(x=np.asarray(xs), y=np.asarray(ys), z=z.ravel(), side=np.full(z.size, res),
                    kind="栅格（%g m）" % res, nodata=ds.nodata, crs=ds.crs)


# ------------------------------------------------------------------ 参照：原始 DTM 在单元范围内的平均
def reference_mean(dtm_path, x, y, side, log=print, block=None):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds
    pad = float(np.nanmax(side)) / 2 + 10
    with rasterio.open(dtm_path) as ds:
        nd = ds.nodata
        x0, x1 = float(np.nanmin(x)) - pad, float(np.nanmax(x)) + pad
        y0, y1 = float(np.nanmin(y)) - pad, float(np.nanmax(y)) + pad
        win = from_bounds(x0, y0, x1, y1, ds.transform)
        native = abs(ds.res[0])
        step = max(native, float(np.nanmedian(side)) / 6.0)
        w = max(1, int(math.ceil((x1 - x0) / step)))
        h = max(1, int(math.ceil((y1 - y0) / step)))
        log("  原始 DTM：%g m，按 %.1f m 平均降采样读 %d × %d 格" % (native, step, w, h))
        z = ds.read(1, window=win, out_shape=(h, w), resampling=Resampling.average, boundless=True,
                    fill_value=nd if nd is not None else np.nan, masked=True)
        bad = np.ma.getmaskarray(z) | ~np.isfinite(z.filled(np.nan)) | (np.abs(z.filled(0)) > BAD)
        if nd is not None:
            bad |= z.filled(nd) == np.float32(nd)
        v = np.where(bad, 0.0, z.filled(0).astype(float))
        c = (~bad).astype(float)
    sv = np.zeros((h + 1, w + 1))
    sc = np.zeros((h + 1, w + 1))
    sv[1:, 1:] = v.cumsum(0).cumsum(1)
    sc[1:, 1:] = c.cumsum(0).cumsum(1)
    col = (x - x0) / step
    row = (y1 - y) / step
    half = np.maximum(side / 2 / step, 0.5)
    c0 = np.clip(np.floor(col - half).astype(int), 0, w)
    c1 = np.clip(np.ceil(col + half).astype(int), 0, w)
    r0 = np.clip(np.floor(row - half).astype(int), 0, h)
    r1 = np.clip(np.ceil(row + half).astype(int), 0, h)
    S = sv[r1, c1] - sv[r0, c1] - sv[r1, c0] + sv[r0, c0]
    N = sc[r1, c1] - sc[r0, c1] - sc[r1, c0] + sc[r0, c0]
    with np.errstate(invalid="ignore", divide="ignore"):
        ref = np.where(N > 0, S / np.maximum(N, 1), np.nan)
    # 周边地形范围：2 倍单元边长（中位）方窗内的最低、最高（降采样格上）
    from scipy import ndimage
    k = max(3, int(round(2 * max(float(np.nanmedian(side)), block or 0) / step)) | 1)
    lo = ndimage.minimum_filter(np.where(bad, np.inf, v), size=k, mode="nearest")
    hi = ndimage.maximum_filter(np.where(bad, -np.inf, v), size=k, mode="nearest")
    ri = np.clip(row.astype(int), 0, h - 1)
    ci = np.clip(col.astype(int), 0, w - 1)
    zlo, zhi = lo[ri, ci], hi[ri, ci]
    zlo = np.where(np.isfinite(zlo), zlo, np.nan)
    zhi = np.where(np.isfinite(zhi), zhi, np.nan)
    return ref, step, zlo, zhi


# ------------------------------------------------------------------ 判定
def check(T, ref, thr: Thresholds, zlo=None, zhi=None):
    z = T["z"]
    m1 = ~np.isfinite(z) | (np.abs(z) > BAD)
    if T.get("nodata") is not None:
        m1 |= z == np.float32(T["nodata"])
    zz = np.where(m1, np.nan, z)
    m2 = ~m1 & ((zz < thr.elev_min) | (zz > thr.elev_max))
    m3 = ~m1 & ~m2 & ~np.isfinite(ref)
    ok = ~m1 & ~m2 & ~m3
    dz = np.where(ok, zz - ref, np.nan)
    big = ok & (np.abs(dz) > thr.terrain_diff_warn)
    if zlo is None:
        outside = big
    else:
        with np.errstate(invalid="ignore"):
            outside = big & ((zz < zlo - thr.terrain_diff_warn) | (zz > zhi + thr.terrain_diff_warn))
    crit = outside & (np.abs(dz) > thr.terrain_diff_crit)
    warn = outside & ~crit
    smooth = big & ~outside
    med = float(np.nanmedian(dz)) if ok.any() else float("nan")
    q = {k: round(float(np.nanpercentile(dz, p)), 3) for k, p in (("p1", 1), ("p5", 5), ("p50", 50), ("p95", 95), ("p99", 99))} if ok.any() else {}
    return dict(m1=m1, m2=m2, m3=m3, crit=crit, warn=warn, smooth=smooth, dz=dz, median=med, quant=q, ok=ok)


def _samples(T, idx, ref, dz=None):
    out = []
    for i in idx[:N_SAMPLE]:
        out.append({"x": round(float(T["x"][i]), 1), "y": round(float(T["y"][i]), 1),
                    "z": float(T["z"][i]) if np.isfinite(T["z"][i]) else str(T["z"][i]),
                    "dtm_mean": None if not np.isfinite(ref[i]) else round(float(ref[i]), 2),
                    "dz": None if dz is None or not np.isfinite(dz[i]) else round(float(dz[i]), 2)})
    return out


def _tiles(T, mask, side):
    c = Counter((int(T["x"][i] // TILE_M), int(T["y"][i] // TILE_M)) for i in np.flatnonzero(mask))
    out = []
    for (tx, ty), n in c.most_common(8):
        sel = mask & (T["x"] // TILE_M == tx) & (T["y"] // TILE_M == ty)
        out.append({"x": [tx * TILE_M, (tx + 1) * TILE_M], "y": [ty * TILE_M, (ty + 1) * TILE_M], "cells": n,
                    "area_km2": round(float((side[sel] ** 2).sum()) / 1e6, 2)})
    return out


def run(terrain, dtm, thr=None, cell=None, log=print, block=None):
    thr = thr or Thresholds()
    log("读模型地形", terrain)
    T = read_terrain(terrain, cell)
    n = len(T["z"])
    log("  %s：%s 个单元，边长中位 %.1f m" % (T["kind"], format(n, ","), float(np.nanmedian(T["side"]))))
    log("算原始 DTM 在每个单元范围内的平均")
    ref, step, zlo, zhi = reference_mean(dtm, T["x"], T["y"], T["side"], log=log, block=block)
    C = check(T, ref, thr, zlo, zhi)
    area = T["side"] ** 2
    f = lambda m: dict(cells=int(m.sum()), area_km2=round(float(area[m].sum()) / 1e6, 2))
    order = lambda m, key: np.flatnonzero(m)[np.argsort(-key[m])] if m.any() else np.array([], int)
    absdz = np.nan_to_num(np.abs(C["dz"]))
    absz = np.nan_to_num(np.abs(T["z"]), nan=np.inf, posinf=np.inf)
    checks = [
        dict(code="M1", name="无效值", sev="crit" if C["m1"].any() else "ok", **f(C["m1"]),
             note="非有限数、|z| > 1e30 或等于 NoData：在二维里是高墙或深坑", samples=_samples(T, order(C["m1"], absz), ref), tiles=_tiles(T, C["m1"], T["side"])),
        dict(code="M2", name="越界", sev="crit" if C["m2"].any() else "ok", **f(C["m2"]),
             note="低于 %g m 或高于 %g m" % (thr.elev_min, thr.elev_max), samples=_samples(T, order(C["m2"], absz), ref), tiles=_tiles(T, C["m2"], T["side"])),
        dict(code="M3", name="参照缺失", sev="info" if C["m3"].any() else "ok", **f(C["m3"]),
             note="单元范围内原始 DTM 全是 NoData 或在图外，无从核对（海上常见）", samples=_samples(T, np.flatnonzero(C["m3"]), ref), tiles=_tiles(T, C["m3"], T["side"])),
        dict(code="M4", name="超出周边地形", sev="crit" if C["crit"].any() else ("warn" if C["warn"].any() else "ok"),
             crit=f(C["crit"]), warn=f(C["warn"]), cells=int(C["crit"].sum() + C["warn"].sum()),
             note="单元高程超出周边原始 DTM 的最低～最高（方窗边长 %g m），且与块平均差过 %g m（过 %g m 为严重）：处理错，假墙或假坑" % (
                 2 * max(float(np.nanmedian(T["side"])), block or 0), thr.terrain_diff_warn, thr.terrain_diff_crit),
             samples=_samples(T, order(C["crit"] | C["warn"], absdz), ref, C["dz"]), tiles=_tiles(T, C["crit"] | C["warn"], T["side"])),
        dict(code="M4b", name="平滑", sev="info" if C["smooth"].any() else "ok", **f(C["smooth"]),
             note="与块平均差过 %g m、但仍在周边地形范围内：沙丘、坡地上取块平均的正常结果，细地形（沟、路缘）被抹平" % thr.terrain_diff_warn,
             tiles=_tiles(T, C["smooth"], T["side"])),
        dict(code="M5", name="系统偏差", sev="warn" if np.isfinite(C["median"]) and abs(C["median"]) > thr.terrain_bias else "ok",
             value=None if not np.isfinite(C["median"]) else round(C["median"], 3), note="可比单元偏差的中位数，超过 %g m 多半是基准不一致" % thr.terrain_bias),
        dict(code="M6", name="偏差分布", sev="info", value=C["quant"], note="单元高程 − 原始 DTM 块平均（m）"),
    ]
    valid = np.isfinite(T["z"]) & (np.abs(T["z"]) < BAD)
    res = dict(inputs=dict(terrain=terrain, dtm=dtm), kind=T["kind"], cells=n, comparable=int(C["ok"].sum()), ref_step_m=round(step, 2),
               side_median_m=round(float(np.nanmedian(T["side"])), 1),
               z_range=[float(np.min(T["z"][valid])), float(np.max(T["z"][valid]))] if valid.any() else None,
               z_range_all=[float(np.nanmin(T["z"])), float(np.nanmax(T["z"]))],
               thresholds={k: getattr(thr, k) for k in ("elev_min", "elev_max", "terrain_diff_warn", "terrain_diff_crit", "terrain_bias")},
               checks=checks)
    return res, T, ref, C


SEV = {"ok": "通过", "info": "提示", "warn": "需处理", "crit": "影响建模"}


def write_report(res, path, title=None):
    L = ["# %s" % (title or "模型地形体检"), "",
         "- 模型地形：`%s`（%s，%s 个单元，边长中位 %s m）" % (res["inputs"]["terrain"], res["kind"], format(res["cells"], ","), res["side_median_m"]),
         "- 原始 DTM：`%s`（按 %s m 平均降采样后求每个单元范围内的平均）" % (res["inputs"]["dtm"], res["ref_step_m"]),
         "- 可比单元：%s 个；单元高程范围（全部）%.4g ～ %.4g m" % (format(res["comparable"], ","), *res["z_range_all"]), "",
         "| 项 | 判定 | 结果 | 说明 |", "|---|---|---|---|"]
    for c in res["checks"]:
        if c["code"] == "M4":
            val = "严重 %s 个（%s km²），提示 %s 个（%s km²）" % (format(c["crit"]["cells"], ","), c["crit"]["area_km2"], format(c["warn"]["cells"], ","), c["warn"]["area_km2"])
        elif "cells" in c:
            val = "%s 个单元（%s km²）" % (format(c["cells"], ","), c["area_km2"])
        else:
            val = json.dumps(c["value"], ensure_ascii=False) if isinstance(c["value"], dict) else str(c["value"])
        L.append("| %s %s | %s | %s | %s |" % (c["code"], c["name"], SEV[c["sev"]], val, c["note"]))
    for c in res["checks"]:
        if c.get("tiles"):
            L += ["", "## %s %s：集中在哪（5 km 方格，前 8 个）" % (c["code"], c["name"]), "", "| x 范围 | y 范围 | 单元数 | 面积 km² |", "|---|---|---|---|"]
            L += ["| %.0f–%.0f | %.0f–%.0f | %d | %s |" % (*t["x"], *t["y"], t["cells"], t["area_km2"]) for t in c["tiles"]]
        if c.get("samples") and c["code"] in ("M1", "M2", "M4"):
            L += ["", "## %s %s：样例（最多 %d 个）" % (c["code"], c["name"], N_SAMPLE), "", "| x | y | 单元高程 | DTM 块平均 | 差 |", "|---|---|---|---|---|"]
            L += ["| %s | %s | %s | %s | %s |" % (s["x"], s["y"], s["z"], s["dtm_mean"], s["dz"]) for s in c["samples"]]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


def write_gpkg(T, ref, C, path, crs):
    import geopandas as gpd
    kinds = [("M1 无效值", C["m1"]), ("M2 越界", C["m2"]), ("M4 超出周边地形 严重", C["crit"]), ("M4 超出周边地形 提示", C["warn"]), ("M4b 平滑", C["smooth"])]
    for name, m in kinds:
        if not m.any():
            continue
        i = np.flatnonzero(m)
        g = gpd.GeoDataFrame({"z": T["z"][i], "dtm_mean": ref[i], "dz": C["dz"][i]},
                             geometry=gpd.points_from_xy(T["x"][i], T["y"][i]), crs=crs)
        g.to_file(path, layer=name, driver="GPKG")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m dataqc.model_terrain", description="模型实际用的地形 对 原始 DTM 逐单元体检（只读）")
    ap.add_argument("--terrain", required=True, help="模型地形：*_2d.npz、*.sww 或重采样后的 DEM 栅格")
    ap.add_argument("--dtm", required=True, help="原始 DTM 栅格")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--cell", type=float, help="npz 里没有 area 时的单元边长（米）")
    ap.add_argument("--terrain-crs", help="模型地形的 EPSG（缺省与 DTM 相同）")
    ap.add_argument("--block", type=float, help="模型给单元赋高程时取块平均的块大小（米），如先按 250 m 取块平均再赋给三角网就给 250；缺省按单元边长")
    ap.add_argument("--thresholds", help="阈值覆盖 JSON，如 {\"模型地形偏差严重\": 3}")
    ap.add_argument("--title", help="报告标题")
    ap.add_argument("--gpkg", action="store_true", help="导出问题单元点层")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    t0 = time.time()

    def log(*x):
        print("[%5.0fs]" % (time.time() - t0), *x, flush=True)

    import rasterio
    with rasterio.open(a.dtm) as ds:
        dcrs = ds.crs
    if a.terrain_crs and dcrs is not None and not rasterio.crs.CRS.from_user_input(a.terrain_crs).equals(dcrs):
        raise SystemExit("模型地形坐标系 %s 与 DTM %s 不同：先把模型地形换到 DTM 坐标系" % (a.terrain_crs, dcrs))
    thr = Thresholds.read_json(a.thresholds)
    os.makedirs(a.out, exist_ok=True)
    res, T, ref, C = run(a.terrain, a.dtm, thr, a.cell, log=log, block=a.block)
    with open(os.path.join(a.out, "model_terrain.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    write_report(res, os.path.join(a.out, "model_terrain.md"), a.title)
    if a.gpkg:
        write_gpkg(T, ref, C, os.path.join(a.out, "model_terrain.gpkg"), dcrs)
    for c in res["checks"]:
        n = c.get("cells", c.get("value"))
        log("%s %-6s %-4s %s" % (c["code"], c["name"], SEV[c["sev"]], n))
    log("→", a.out)


if __name__ == "__main__":
    main()
