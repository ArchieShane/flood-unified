#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为 RDA_96B 生成 land_cls.tif 和 corridor_cost.tif（一次性脚本）

l2opt_land.py 设计为从 c2d/dem_{run}.tif 读范围，但 RDA_96B 没有对应的 dem 文件。
这个脚本直接调用 classify() 并生成两张栅格。
"""
import sys
import json
import numpy as np
import rasterio
from pathlib import Path
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from l2opt_land import classify, SEA_Z
from l2opt_corridor_cost import build_cost_raster, COST_IMPASSABLE

def main():
    run = "RDA_96B"
    res = 5.0

    # 1. 从 candidates.json 计算 bbox
    print(f"Run: {run}")
    cands_file = ROOT / "out" / "l2opt" / run / "candidates.json"
    with open(cands_file, encoding='utf-8') as f:
        cands = json.load(f)

    tw = Transformer.from_crs(4326, 32640, always_xy=True)
    lons = [c['lon'] for c in cands['candidates']]
    lats = [c['lat'] for c in cands['candidates']]
    xs, ys = tw.transform(lons, lats)
    x0, y0 = np.min(xs), np.min(ys)
    x1, y1 = np.max(xs), np.max(ys)

    buffer = 2000
    bbox = (x0 - buffer, y0 - buffer, x1 + buffer, y1 + buffer)
    print(f"Bbox (UTM 32640): {[int(v) for v in bbox]}")
    print(f"Resolution: {res} m")

    # 2. 生成 land_cls.tif
    print("\n[1/2] Generating land_cls.tif...")
    from swbrain import db
    cfg = db.load_config(None)

    # 现有塘（从 pilot JSON，如果有）
    ponds = []
    C2D = ROOT.parent / "swbrain_work" / "swmm" / "c2d"
    for p in sorted(C2D.glob(f"pilot_RDA_*_L2P.json")):
        from shapely.geometry import shape
        ponds_data = json.loads(p.read_text(encoding="utf-8")).get("ponds", [])
        ponds.extend([shape(q["footprint"]) for q in ponds_data])
    print(f"  Existing ponds: {len(ponds)}")

    cls, pid, tf, recs, stats = classify(cfg, bbox, res, ponds, log=print)

    # DTM 无值处理
    dtm_file = Path(r"D:\阿布扎比\DTM_z40_customer\AUH_DTM_5m_Z40.TIF")
    print(f"  Checking DTM nodata: {dtm_file}")
    with rasterio.open(dtm_file) as d0:
        from rasterio.warp import Resampling, reproject
        from rasterio.crs import CRS
        # Bypass broken PROJ db in PostgreSQL 18 path
        utm40n_wkt = 'PROJCS["WGS 84 / UTM zone 40N",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",57],PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],PARAMETER["false_northing",0],UNIT["metre",1]]'
        z = np.full(cls.shape, np.nan, np.float32)
        reproject(
            d0.read(1).astype(np.float32), z,
            src_transform=d0.transform, src_crs=d0.crs,
            dst_transform=tf, dst_crs=CRS.from_wkt(utm40n_wkt),
            src_nodata=d0.nodata, dst_nodata=np.nan,
            resampling=Resampling.nearest
        )

    bad = ~np.isfinite(z) | (z < -100) | (z > 1000) | (z <= SEA_Z)
    cls[bad] = 0
    pid[bad] = -1
    print(f"    DTM bad cells: {bad.sum()}")

    # 保存 land_cls
    out_dir = ROOT / "out" / "l2opt" / run
    out_dir.mkdir(parents=True, exist_ok=True)

    prof = {
        "driver": "GTiff",
        "height": cls.shape[0],
        "width": cls.shape[1],
        "count": 1,
        "crs": "EPSG:32640",
        "transform": tf,
        "compress": "deflate"
    }

    with rasterio.open(out_dir / "land_cls.tif", "w", dtype="uint8", nodata=255, **prof) as w:
        w.write(cls, 1)

    with rasterio.open(out_dir / "land_parcel.tif", "w", dtype="int32", nodata=-1, **prof) as w:
        w.write(pid, 1)

    (out_dir / "land_parcels.json").write_text(
        json.dumps(recs, ensure_ascii=False, default=str), encoding="utf-8"
    )

    from l2opt_land import NAMES
    area_stats = {k: round(float((cls == c).sum()) * res * res / 1e4, 1) for c, k in NAMES.items()}
    (out_dir / "land_stats.json").write_text(
        json.dumps({
            "run": run,
            "res_m": res,
            "bbox": bbox,
            "area_ha": area_stats,
            "names": NAMES
        }, ensure_ascii=False, indent=1),
        encoding="utf-8"
    )

    print(f"  Land classification stats:")
    for k, v in area_stats.items():
        print(f"    {k:<22} {v:>8,.1f} ha")
    print(f"  → {out_dir / 'land_cls.tif'}")

    # 3. 生成 corridor_cost.tif
    print("\n[2/2] Generating corridor_cost.tif...")
    sea_file = ROOT / "data" / "gis" / "sea.gpkg"
    cost, _ = build_cost_raster(
        out_dir / "land_cls.tif",
        sea_file,
        dtm_file,
        bbox,
        res,
        nearshore_m=200,
        log=print
    )

    with rasterio.open(out_dir / "corridor_cost.tif", "w", dtype="float32", nodata=COST_IMPASSABLE, **prof) as w:
        w.write(cost, 1)

    print(f"  → {out_dir / 'corridor_cost.tif'}")
    print(f"\nDone. Files in {out_dir}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
