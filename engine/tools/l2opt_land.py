# -*- coding: utf-8 -*-
"""L2+suggested 优化试点 · 塘址土地分级（Makani 库，只读）。5 m 栅格，每格一类：

  0 不能用   路面 / 路肩 / 交通岛 / 路口 / 干道车行道与中分带外扩 5 m、建筑外扩 5 m、
             市政设施用地、铁路红线、现有塘、海面 / 水面 / 海岸带、DTM ≤ 0.2 m（海面）或无值
  1 一级     公共开放空间 / 休闲 / 农业用地（ud_plot 一级分配类型），udm_plot 编码 15（公共用地）里未建、不是清真寺的地块
  2 二级     未建的开发地块（udm_plot 建设状态 Not Constructed、upc_plot Vacant），要业主 / 规划部门同意
  3 二级 b   没有任何地块记录、不在路上、没有建筑的地（多半是政府或市政地，权属要核）
  4 次选     干道红线 / 管线走廊预留带 / 规划红线（不动路面，但要道路主管部门批准，下面可能有管线）
  9 不用     其余已分配地块（住宅、商业、投资、工业、清真寺、公建、已建或在建）

udm_plot.primaryuseeng 是编码，按 2026-10-19 在 RB-01 范围与 ud_plot 英文类型叠合反推：
13 住宅、2 商业 / 投资、9 工业、12 清真寺、0 / 11 农场牧场、15 公共用地（多为 Public Open Space，含清真寺与公建）、
16 市政设施（多为 Utility）、17 投资用地；18 只有 34 块、没对上，按「不用」。

用法：python tools/l2opt_land.py --run RB01_H61 [--res 5]
产物：out/l2opt/<运行>/land_cls.tif（类别）、land_parcel.tif（地块序号）、land_parcels.json（序号 → 表 / 编号 / 类型 / 状态）、land_stats.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

T1_TYPES = ("Public Open Space", "Recreational", "Agricultural")
UTILITY_TABLES = (("ud_utilityplots", 32640), ("upc_utilityplots", 32640), ("utility_plot", 4326), ("utility_plots_1", 4326), ("etihadrailrow", 4326))
# 海面 / 水面 / 海岸带（2026-10-19 第一版没扣，「无地块记录」里约 40 % 落在海里——DTM 把海面压成 0）
WATER_TABLES = (("udm_seasurface", 4326), ("udm_watersurface", 4326), ("udm_canalwatercoursesurface", 4326), ("udm_seashore", 4326),
                ("udm_streamwatercoursesurface", 4326), ("udm_wadiwatercoursesurface", 4326))
SEA_Z = 0.2          # DTM ≤ 这个高程按海面 / 潮间带处理
PAVE_BUF, BLD_BUF = 5.0, 5.0
NAMES = {0: "不能用", 1: "一级（公共开放 / 休闲 / 农业）", 2: "二级（未建开发地块）", 3: "二级 b（无地块记录）", 4: "次选（道路红线 / 管线走廊）", 9: "不用（已分配地块）"}


def classify(cfg, bbox, res=5.0, pond_geoms=(), log=print):
    from pyproj import Transformer
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from scipy import ndimage
    from shapely import wkb
    from swbrain import db
    from swbrain.landuse import PAVE_TABLES, RESERVE_TABLES

    x0, y0, x1, y1 = bbox
    W, H = int(np.ceil((x1 - x0) / res)), int(np.ceil((y1 - y0) / res))
    tf = from_origin(x0, y1, res, res)
    tw = Transformer.from_crs(32640, 4326, always_xy=True)
    lo0, la0 = tw.transform(x0, y0); lo1, la1 = tw.transform(x1, y1)

    def env(srid):
        return f"st_makeenvelope({lo0},{la0},{lo1},{la1},4326)" if srid == 4326 else f"st_makeenvelope({x0},{y0},{x1},{y1},32640)"

    def fetch(cur, table, srid, cols=()):
        sel = "".join(f", {c}" for c in cols)
        cur.execute(f"select st_asbinary(st_transform(st_makevalid(shape),32640)){sel} from public.{table} where shape && {env(srid)} and not st_isempty(shape)")
        return [(wkb.loads(bytes(r[0])), *r[1:]) for r in cur.fetchall() if r[0]]

    def mask(geoms, buf=0.0):
        geoms = [g for g in geoms if g is not None and not g.is_empty]
        if not geoms:
            return np.zeros((H, W), bool)
        m = rasterize(((g, 1) for g in geoms), out_shape=(H, W), transform=tf, fill=0, all_touched=True, dtype="uint8").astype(bool)
        k = int(round(buf / res))
        if k > 0:
            yy, xx = np.ogrid[-k:k + 1, -k:k + 1]
            m = ndimage.binary_dilation(m, yy * yy + xx * xx <= k * k)
        return m

    conn = db.connect_source(cfg, "makani", timeout_ms=600_000)
    with conn, conn.cursor() as cur:
        udm = fetch(cur, "udm_plot", 4326, ("objectid", "primaryuseeng", "elms_landuse_const", "construction_status", "communityeng"))
        ud = fetch(cur, "ud_plot", 32640, ("objectid", "level_1_allocation_type_eng", "udm_sectorno_plotno"))
        upc = fetch(cur, "upc_plot", 32640, ("objectid_1", "existing_land_use", "built_status_code", "plot_id"))
        util = sum((fetch(cur, t, s) for t, s in UTILITY_TABLES), [])
        pave = sum((fetch(cur, t, s) for t, s in PAVE_TABLES), [])
        rsv = sum((fetch(cur, t, s) for t, s in RESERVE_TABLES), [])
        bld = fetch(cur, "bas_building", 4326) + fetch(cur, "bdms_bldg_landuse", 4326)
        water = sum((fetch(cur, t, s) for t, s in WATER_TABLES), [])
    log(f"  Makani：udm_plot {len(udm):,}、ud_plot {len(ud):,}、upc_plot {len(upc):,}、市政 / 铁路 {len(util):,}、路面类 {len(pave):,}、红线 {len(rsv):,}、"
        f"建筑 {len(bld):,}、水面 / 海岸 {len(water):,}")

    # 地块逐个定级，记下序号供归属
    parcels, t1, t2, other = [], [], [], []
    for g, oid, use, const, status, com in udm:
        rec = {"table": "udm_plot", "id": oid, "use_code": use, "use": const, "status": status, "community": com}
        if use == 16:
            lv = 9; util.append(g)
        elif use == 15 and const != "MASJED" and status == "Not Constructed":
            lv = 1
        elif status == "Not Constructed" and use not in (12, 16) and const != "MASJED":
            lv = 2
        else:
            lv = 9
        rec["level"] = lv; parcels.append((g, rec))
    for g, oid, typ, pno in ud:
        rec = {"table": "ud_plot", "id": oid, "use": typ, "plot": pno}
        if typ == "Utility":
            lv = 9; util.append(g)
        else:
            lv = 1 if typ in T1_TYPES else 9
        rec["level"] = lv; parcels.append((g, rec))
    for g, oid, lu, built, pid in upc:
        lv = 2 if built == "Vacant" else 9
        parcels.append((g, {"table": "upc_plot", "id": oid, "use": lu, "status": built, "plot": pid, "level": lv}))

    cls = np.full((H, W), 3, np.uint8)                                  # 先当「无地块记录」
    pid = np.full((H, W), -1, np.int32)
    order = sorted(range(len(parcels)), key=lambda i: {9: 0, 2: 1, 1: 2}[parcels[i][1]["level"]])   # 9 先画，2、1 后画覆盖（一级优先）
    for i in order:
        g, rec = parcels[i]
        m = mask([g])
        if rec["level"] == 9:
            cls[m & (cls != 1) & (cls != 2)] = 9
        else:
            cls[m & ((cls == 3) | (cls == 9) | (cls == 2) & (rec["level"] == 1))] = rec["level"]
            pid[m & (cls == rec["level"])] = i
    r = mask([g for g, *_ in rsv]); cls[r & (cls == 3)] = 4
    hard = (mask([g for g, *_ in pave], PAVE_BUF) | mask([g for g, *_ in bld], BLD_BUF) | mask([g if not isinstance(g, tuple) else g[0] for g in util])
            | mask(list(pond_geoms)) | mask([g for g, *_ in water]))
    cls[hard] = 0
    pid[hard] = -1
    stats = {NAMES[k]: round(float((cls == k).sum()) * res * res / 1e4, 1) for k in NAMES}
    return cls, pid, tf, [rec for _, rec in parcels], stats


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--run", default="RB01_H61"); ap.add_argument("--res", type=float, default=5.0)
    a = ap.parse_args(argv)
    import rasterio
    from shapely.geometry import shape
    from swbrain import db
    cfg = db.load_config(None)
    C2D = WORK / "c2d"
    with rasterio.open(C2D / f"dem_{a.run}.tif") as d0:
        bb = tuple(d0.bounds)
    ponds = []
    for p in sorted(C2D.glob(f"pilot_{a.run}_L2P.json")):
        ponds = [shape(q["footprint"]) for q in json.loads(p.read_text(encoding="utf-8")).get("ponds", [])]
    print(f"{a.run}：土地分级，范围 {[round(v) for v in bb]}，{a.res:g} m，现有塘 {len(ponds)} 个")
    cls, pid, tf, recs, stats = classify(cfg, bb, a.res, ponds)
    with rasterio.open(C2D / f"dem_{a.run}.tif") as d0:                # DTM 无值（海里等）的格子不能用
        from rasterio.warp import Resampling, reproject
        z = np.full(cls.shape, np.nan, np.float32)
        reproject(d0.read(1).astype(np.float32), z, src_transform=d0.transform, src_crs=d0.crs, dst_transform=tf, dst_crs="EPSG:32640",
                  src_nodata=d0.nodata, dst_nodata=np.nan, resampling=Resampling.nearest)
    bad = ~np.isfinite(z) | (z < -100) | (z > 1000) | (z <= SEA_Z)
    cls[bad] = 0; pid[bad] = -1
    out = ROOT / "out" / "l2opt" / a.run; out.mkdir(parents=True, exist_ok=True)
    prof = {"driver": "GTiff", "height": cls.shape[0], "width": cls.shape[1], "count": 1, "crs": "EPSG:32640", "transform": tf, "compress": "deflate"}
    with rasterio.open(out / "land_cls.tif", "w", dtype="uint8", nodata=255, **prof) as w:
        w.write(cls, 1)
    with rasterio.open(out / "land_parcel.tif", "w", dtype="int32", nodata=-1, **prof) as w:
        w.write(pid, 1)
    (out / "land_parcels.json").write_text(json.dumps(recs, ensure_ascii=False, default=str), encoding="utf-8")
    stats = {k: round(float((cls == c).sum()) * a.res * a.res / 1e4, 1) for c, k in NAMES.items()}
    (out / "land_stats.json").write_text(json.dumps({"run": a.run, "res_m": a.res, "bbox": bb, "area_ha": stats, "names": NAMES}, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in stats.items():
        print(f"  {k:<22} {v:>8,.1f} ha")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
