# -*- coding: utf-8 -*-
"""建筑处理第 1 步（用户 10-28 同意）：查 DTM 在建筑占地上是不是裸地。

逐栋 Makani 建筑：占地内 DTM 像元中位数 − 楼外环（外扩 2–10 m、扣掉所有建筑）像元中位数 = dz。
dz ≈ 0 → DTM 是裸地，可直接挖洞；dz 明显 > 0（米级）→ DTM 里残留屋顶，要先压回地面。
占地内没有像元中心（< 约 25 m² 的小楼）的跳过、单独计数。
用法：python tools/bldg_dtm_check.py   产物：out/bldg/dtm_check_RDA.json、.csv
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE / "exp_gov")); sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import rb01_build as RB  # noqa: E402
import rda_catchments as RC  # noqa: E402
import v_build as VB  # noqa: E402

OUT = ROOT / "out" / "bldg"
RING = (2.0, 10.0)


def buildings(D):
    """计算域内 Makani 建筑（三张表合并、溶解），UTM 40N"""
    from pyproj import Transformer
    from shapely import wkb
    from shapely.ops import unary_union
    from swbrain import db
    x0, y0, x1, y1 = D.bounds
    tw = Transformer.from_crs(32640, 4326, always_xy=True)
    lo0, la0 = tw.transform(x0, y0); lo1, la1 = tw.transform(x1, y1)
    conn = db.connect_source(db.load_config(None), "makani", timeout_ms=600_000)
    g, cnt = [], {}
    with conn, conn.cursor() as cur:
        for t, s in RC.BLD:
            env = f"st_makeenvelope({lo0},{la0},{lo1},{la1},4326)" if s == 4326 else f"st_makeenvelope({x0},{y0},{x1},{y1},32640)"
            cur.execute(f"select st_asbinary(st_transform(st_makevalid(shape),32640)) from public.{t} where shape && {env} and not st_isempty(shape)")
            gg = [wkb.loads(bytes(r[0])) for r in cur.fetchall() if r[0]]
            gg = [x for x in gg if x.geom_type in ("Polygon", "MultiPolygon", "GeometryCollection") and not x.is_empty]
            cnt[t] = len(gg); g += gg
    U = unary_union([x.buffer(0) for x in g]).intersection(D)
    parts = [p for p in getattr(U, "geoms", [U]) if p.geom_type == "Polygon" and p.area > 0]
    return parts, U, cnt


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds
    OUT.mkdir(parents=True, exist_ok=True)
    cat = gpd.read_file(VB.SHP / "Catchment.shp").set_crs(32640, allow_override=True)
    D = RB.domain(cat)
    parts, U, cnt = buildings(D)
    print(f"Makani 建筑要素 {cnt} → 溶解后 {len(parts):,} 栋、{U.area / 1e4:.1f} ha（占计算域 {U.area / D.area:.1%}）", flush=True)
    with rasterio.open(RB.DTM) as ds:
        b = np.array(D.bounds) + [-20, -20, 20, 20]
        win = from_bounds(*b, ds.transform).round_offsets().round_lengths()
        z = ds.read(1, window=win, masked=True).astype(float).filled(np.nan)
        tf = ds.window_transform(win)
    z[z == 0] = np.nan                              # DTM = 0 是已知缺测（全表 3.1），不参与比较
    shp = z.shape
    bmask = ~geometry_mask([U], out_shape=shp, transform=tf)
    rows, skipped = [], 0
    for i, p in enumerate(parts):
        inside = ~geometry_mask([p], out_shape=shp, transform=tf)
        zi = z[inside]; zi = zi[np.isfinite(zi)]
        if zi.size == 0:
            skipped += 1; continue
        ring = ~geometry_mask([p.buffer(RING[1]).difference(p.buffer(RING[0]))], out_shape=shp, transform=tf) & ~bmask
        zr = z[ring]; zr = zr[np.isfinite(zr)]
        if zr.size < 3:
            skipped += 1; continue
        rows.append((i, round(p.area, 1), int(zi.size), round(float(np.median(zi)), 3), round(float(np.median(zr)), 3),
                     round(float(np.median(zi) - np.median(zr)), 3), round(float(zi.max() - np.median(zr)), 3),
                     round(p.centroid.x, 1), round(p.centroid.y, 1)))
    a = np.array([r[1] for r in rows]); dz = np.array([r[5] for r in rows]); dmax = np.array([r[6] for r in rows])
    w = a / a.sum()
    q = lambda v: {f"p{k}": round(float(np.percentile(v, k)), 3) for k in (5, 25, 50, 75, 95)}
    res = {"domain_km2": round(D.area / 1e6, 3), "makani_counts": cnt, "n_buildings": len(parts),
           "bld_area_ha": round(U.area / 1e4, 1), "n_checked": len(rows), "n_skipped_small": skipped,
           "ring_m": RING, "dz_median_in_minus_ring": q(dz), "dz_max_in_minus_ring": q(dmax),
           "share_dz_gt": {f"{t}m": round(float((dz > t).mean()), 4) for t in (0.3, 1.0, 3.0)},
           "area_share_dz_gt": {f"{t}m": round(float(w[dz > t].sum()), 4) for t in (0.3, 1.0, 3.0)},
           "share_dz_lt_-0.3m": round(float((dz < -0.3).mean()), 4)}
    (OUT / "dtm_check_RDA.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(OUT / "dtm_check_RDA.csv", "w", encoding="utf-8-sig") as f:
        f.write("id,area_m2,n_px,z_in_med,z_ring_med,dz_med,dz_max,x,y\n")
        for r in rows:
            f.write(",".join(map(str, r)) + "\n")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
