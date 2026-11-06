# -*- coding: utf-8 -*-
"""全域扫描地形深坑（用户 10-28「可以扫」）：比周边低 ≥ 1 m、面积 ≥ 100 m²，逐个对照 Makani 分类。排除：已在评估剔除区内的、已登记的。
处理方式和备案图还没生成，只出清单；用户过目后再决定填法。

深坑     DTM 填洼（skimage 形态学重建）后深 ≥ 1.0 m、容积 ≥ 100 m³、在计算域内的连片洼地，中心 (x_utm, y_utm)，轮廓、周边地面中位数、坑底高程
周边地面 轮廓外扩 20 m 带内、高于轮廓内 90 % 分位的像元，中位数（同 pit_230387.geojson 的生成逻辑）
排除     在现有评估剔除区（`exclude_OPTCS`）内的；在已登记的 DEM_FILLS 内的；DTM = 0（缺测）的像元数超过坑面积 10 % 的
Makani   每个坑查半径 50 m 内的 bas_building (主类)、bdms_bldg_landuse (用地类)、施工地块（bas_worksite、underConstruction）
产物：out/bldg/pits_scan_<域>.csv（坑清单 + Makani 要素，供人工过目）、out/bldg/pits_scan_<域>.geojson（轮廓，加载进 QGIS）
用法：python tools/dtm_pit_scan.py --domain RDA
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

import numpy as np
import rasterio
from scipy import ndimage
from shapely.geometry import Polygon, shape, mapping
from shapely.ops import unary_union
from skimage import morphology

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import rb01_build as RB  # noqa: E402
from swbrain import db  # noqa: E402

OUT = ROOT / "out" / "bldg"
DTM = {"RDA": pathlib.Path(RB.DTM), "RB01": pathlib.Path(RB.DTM)}
DOMAIN = {"RDA": ROOT / "out" / "dataqc" / "RD-A" / "input" / "rda_domain.geojson",
          "RB01": ROOT / "data" / "RB-01" / "RB-01.geojson"}
EXCLUDE = {"RDA": ROOT / "out" / "l2opt" / "RDA_96C" / "exclude_OPTCS.geojson"}
REGISTERED = {"RDA": [ROOT / "out" / "bldg" / "pit_230387.geojson"]}


def scan_pits(dtm_path, domain_poly, exclude_polys, registered_polys, depth_th=1.0, vol_th=100.0, nodata_tol=0.1):
    """DTM 填洼 + 连通域标记，输出每个坑：轮廓 Polygon、周边地面中位数、坑底、容积"""
    # 只读取计算域范围的 DTM，避免加载整个 25 GB 文件
    with rasterio.open(dtm_path) as ds:
        from rasterio.features import rasterize
        from rasterio.windows import from_bounds
        D = domain_poly.bounds
        win = from_bounds(*D, ds.transform).round_offsets().round_lengths()
        print(f"  读取 DTM 窗口：{win.height} × {win.width} = {win.height * win.width / 1e6:.1f} M 像元", flush=True)
        z = ds.read(1, window=win).astype(float)
        b = ds.window_bounds(win); res = ds.res[0]
        mask = rasterize([mapping(domain_poly)], out_shape=z.shape, transform=rasterio.transform.from_bounds(*b, z.shape[1], z.shape[0]), dtype=np.uint8)
    z[mask == 0] = np.nan
    zero_mask = (z == 0.0) & (mask > 0)
    # 填洼（形态学重建：seed = z + tiny offset，structure = z；填完后 filled − z = 洼深）
    valid = ~np.isnan(z)
    filled = morphology.reconstruction(z + 1e-6, z, method="erosion").astype(z.dtype)
    filled[~valid] = np.nan
    depth = filled - z
    depth[~valid] = 0.0
    # 连通域
    pit_mask = (depth >= depth_th) & valid
    labels, n = ndimage.label(pit_mask, structure=np.ones((3, 3), int))
    print(f"  填洼后 ≥ {depth_th} m 连通域 {n} 个")
    pits = []
    for i in range(1, n + 1):
        sel = labels == i
        vol = float((depth[sel] * res * res).sum())
        if vol < vol_th:
            continue
        if exclude_polys and any(Polygon(np.argwhere(sel)[:, ::-1] * res + [b[0], b[1]]).intersects(ex) for ex in exclude_polys):
            continue
        if registered_polys and any(Polygon(np.argwhere(sel)[:, ::-1] * res + [b[0], b[1]]).intersects(rg) for rg in registered_polys):
            continue
        if zero_mask[sel].sum() / sel.sum() > nodata_tol:
            continue
        # 轮廓（转成 UTM 坐标）
        from rasterio.features import shapes as _sh
        for poly, v in _sh((sel).astype(np.uint8), transform=rasterio.transform.from_bounds(*b, z.shape[1], z.shape[0])):
            if v == 1:
                poly = shape(poly).buffer(0)
                break
        # 周边地面：外扩 20 m 带内、高于坑内 90 % 分位的像元
        buf = poly.buffer(20.0)
        ring_z = []
        for r, c in np.argwhere((rasterio.features.rasterize([mapping(buf)], out_shape=z.shape,
                                 transform=rasterio.transform.from_bounds(*b, z.shape[1], z.shape[0]), dtype=np.uint8) > 0) & ~sel & valid):
            ring_z.append(z[r, c])
        p90 = float(np.percentile(z[sel], 90)) if sel.any() else float('nan')
        ring_z = [v for v in ring_z if v > p90] or ring_z
        ring_med = float(np.median(ring_z)) if ring_z else float('nan')
        pit_bot = float(z[sel].min())
        cx, cy = poly.centroid.x, poly.centroid.y
        pits.append({"poly": poly, "x": cx, "y": cy, "area_m2": int(poly.area), "ring_median_m": round(ring_med, 2),
                     "pit_bottom_m": round(pit_bot, 2), "vol_m3": int(vol)})
    print(f"  筛后（容积 ≥ {vol_th} m³、不在剔除 / 已登记区、nodata < {nodata_tol * 100:.0f} %）{len(pits)} 个")
    return pits


def makani_context(x, y, rad_m=50.0):
    """返回 (x,y) 半径 rad_m 内的 Makani 要素：建筑主类（前 3）、用地类（前 3）、施工标记"""
    from swbrain import db
    conn = db.connect_source(db.load_config(None), "makani", timeout_ms=120000)
    pt = f"st_setsrid(st_point({x / 40000 - 180},{y}),4326)"  # 粗糙经纬度估算，只用于查询
    B, L, W = [], [], []
    with conn, conn.cursor() as cur:
        cur.execute(f"select c_uom, cf_desc from public.bas_building where st_dwithin(st_transform(shape,32640),st_transform({pt},32640),{rad_m})")
        B = cur.fetchall()
        cur.execute(f"select c_landuse from public.bdms_bldg_landuse where st_dwithin(st_transform(shape,32640),st_transform({pt},32640),{rad_m})")
        L = cur.fetchall()
        cur.execute(f"select f_name from public.bas_worksite where st_dwithin(st_transform(shape,32640),st_transform({pt},32640),{rad_m})")
        W = cur.fetchall()
    from collections import Counter
    B_top = [f"{k} ({v})" for k, v in Counter(r[0] for r in B if r[0]).most_common(3)]
    L_top = [f"{k} ({v})" for k, v in Counter(r[0] for r in L if r[0]).most_common(3)]
    W_any = any("construction" in str(r[0]).lower() or "underConstruction" in str(r[0]) for r in W) or \
            any("underConstruction" in str(r[1]) for r in B if len(r) > 1)
    return {"bld_uom": "; ".join(B_top) or "无", "landuse": "; ".join(L_top) or "无", "construction": "是" if W_any else "否"}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True, choices=list(DOMAIN.keys()))
    ap.add_argument("--depth", type=float, default=1.0, help="深度阈值 m")
    ap.add_argument("--vol", type=float, default=100.0, help="容积阈值 m³")
    a = ap.parse_args()
    import geopandas as gpd
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"扫描 {a.domain} 地形深坑（≥ {a.depth} m、≥ {a.vol} m³）")
    D = shape(json.loads(pathlib.Path(DOMAIN[a.domain]).read_text(encoding="utf-8"))["features"][0]["geometry"])
    EX = [shape(f["geometry"]) for f in json.loads(pathlib.Path(EXCLUDE[a.domain]).read_text(encoding="utf-8"))["features"]] if a.domain in EXCLUDE else []
    RG = [shape(json.loads(p.read_text(encoding="utf-8"))["features"][0]["geometry"]) for p in REGISTERED.get(a.domain, [])]
    print(f"  计算域 {D.area / 1e6:.2f} km²，剔除区 {len(EX)} 个，已登记 {len(RG)} 个")
    pits = scan_pits(DTM[a.domain], D, EX, RG, a.depth, a.vol)
    for i, p in enumerate(pits, 1):
        ctx = makani_context(p["x"], p["y"])
        p.update(ctx, id=f"PIT-{a.domain}-{i:02d}")
        print(f"  {p['id']}: ({p['x']:.0f}, {p['y']:.0f}), {p['area_m2']} m², {p['vol_m3']} m³, 周边 {p['ring_median_m']} m, 坑底 {p['pit_bottom_m']} m, {ctx['bld_uom'][:40]}")
    # CSV
    fcsv = OUT / f"pits_scan_{a.domain}.csv"
    with fcsv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, ["id", "x", "y", "area_m2", "vol_m3", "ring_median_m", "pit_bottom_m", "bld_uom", "landuse", "construction"])
        w.writeheader(); w.writerows({k: p[k] for k in w.fieldnames} for p in pits)
    # GeoJSON
    fgj = OUT / f"pits_scan_{a.domain}.geojson"
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {k: p[k] for k in ["id", "x", "y", "area_m2", "vol_m3", "ring_median_m", "pit_bottom_m", "bld_uom", "landuse", "construction"]},
         "geometry": mapping(p["poly"])} for p in pits]}
    fgj.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    print(f"→ {fcsv}\n→ {fgj}")


if __name__ == "__main__":
    main()
