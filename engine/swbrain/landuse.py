# -*- coding: utf-8 -*-
"""可挖塘的地：土地利用约束。

塘不能随便挖。原先只按积水分布聚类定塘位，算出来的重心可能落在高速公路、住宅区、
厂房上——模型里画得出来，现实里挖不了。

两级判据，因为用地数据的覆盖不均匀：

  一级（有地块数据的地方）  makani 库 public.ud_plot，82,675 个地块，SRID 32640
      level_1_allocation_type_eng 取 Public Open Space / Recreational / Agricultural
      住宅、商业、工业、公建、公用事业一律不碰。
      实测覆盖：Shaliela 1,023 个地块（1,018 个有用地类型）、RB3 559 个（只有 172 个有）、
      Mussafah **1 个** —— 工业区基本不在这份台账里。

  二级（没有地块数据的地方）  反过来排除：public.bas_building 建筑轮廓（SRID 4326）
      加 out/state/road_edges.parquet 路网，缓冲之后取补集当作可用地。
      这是兜底，不是等价物——「没建筑、不在路上」不等于「这块地能用」，
      权属、规划、地下管线都没查（见假设总账 pond_land）。

源库只读，这里只做 SELECT。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 能挖塘的用地类型。保守取值：宁可少给候选，不给一个挖不了的
OK_TYPES = ("Public Open Space", "Recreational", "Agricultural")
# 建筑与道路的退让（米）——兜底判据用
BUILD_BUFFER_M = 25.0
ROAD_BUFFER_M = 20.0


def params(cfg: dict) -> dict:
    d = {"min_plot_m2": 800.0, "search_radius_m": 800.0, "build_buffer_m": BUILD_BUFFER_M,
         "road_buffer_m": ROAD_BUFFER_M, "dir": "out/landuse"}
    d.update((cfg.get("landuse") or {}))
    return d


def _dir(cfg: dict) -> Path:
    p = Path(params(cfg)["dir"])
    return p if p.is_absolute() else ROOT / p


def export_plots(cfg: dict, bbox_utm: tuple[float, float, float, float], name: str,
                 log=print) -> Path:
    """把一个范围内的可用地块导成 GeoJSON（UTM 40N）。只读源库。"""
    import psycopg
    P = params(cfg)
    c = (cfg.get("source") or {}).get("makani") or {}
    out = _dir(cfg); out.mkdir(parents=True, exist_ok=True)
    f = out / f"{name}.plots.geojson"
    x0, y0, x1, y1 = bbox_utm
    with psycopg.connect(host=c["host"], port=c["port"], user=c["user"], password=c["password"],
                         dbname=c["dbname"], connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select json_build_object('type','FeatureCollection','features',
                  coalesce(json_agg(json_build_object(
                    'type','Feature',
                    'geometry', st_asgeojson(st_transform(shape, 4326))::json,
                    'properties', json_build_object(
                       'plot_id', coalesce(md_plot_id::text, udm_plotnumber::text, objectid::text),
                       'use', level_1_allocation_type_eng,
                       'area_m2', round(st_area(shape)::numeric, 0),
                       'x', round(st_x(st_centroid(shape))::numeric, 1),
                       'y', round(st_y(st_centroid(shape))::numeric, 1)))), '[]'::json))
                from public.ud_plot
                where shape && st_makeenvelope(%s,%s,%s,%s,32640)
                  and level_1_allocation_type_eng = any(%s)
                  and st_area(shape) >= %s
            """, (x0, y0, x1, y1, list(OK_TYPES), float(P["min_plot_m2"])))
            gj = cur.fetchone()[0]
    n = len(gj.get("features") or [])
    f.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    log(f"  {name}: 可用地块 {n} 个 → {f.name}")
    return f


def export_blocked_plots(cfg: dict, bbox_utm: tuple[float, float, float, float], name: str,
                        log=print) -> Path:
    """台账里**明确不是**可挖用地的地块（住宅/商业/工业/公建/公用事业…）。

    兜底掩膜只看得见建筑和路，看不见权属：实测有两个塘（Shaliela NP5、RB3 NP5）
    落在台账标 Commercial 的地块上——地上确实空着，地却不是能挖的。
    把这类地块也烧进掩膜排掉。

    用地类型为空的地块不算「明确不可用」，仍交给建筑/路的兜底判据——
    台账覆盖本来就稀（RB3 559 个地块里只有 172 个填了类型），全按不可用会把兜底废掉。
    """
    import psycopg
    c = (cfg.get("source") or {}).get("makani") or {}
    out = _dir(cfg); out.mkdir(parents=True, exist_ok=True)
    f = out / f"{name}.blocked.geojson"
    x0, y0, x1, y1 = bbox_utm
    with psycopg.connect(host=c["host"], port=c["port"], user=c["user"], password=c["password"],
                         dbname=c["dbname"], connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select json_build_object('type','FeatureCollection','features',
                  coalesce(json_agg(json_build_object(
                    'type','Feature',
                    'geometry', st_asgeojson(shape)::json,
                    'properties', json_build_object('use', level_1_allocation_type_eng))), '[]'::json))
                from public.ud_plot
                where shape && st_makeenvelope(%s,%s,%s,%s,32640)
                  and level_1_allocation_type_eng is not null
                  and level_1_allocation_type_eng <> all(%s)
            """, (x0, y0, x1, y1, list(OK_TYPES)))
            gj = cur.fetchone()[0]
    n = len(gj.get("features") or [])
    f.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    log(f"  {name}: 台账里不可挖的地块 {n} 个 → {f.name}")
    return f


def export_buildings(cfg: dict, bbox_wgs: tuple[float, float, float, float], name: str,
                     log=print) -> Path:
    """建筑轮廓（兜底判据用）。bas_building 是 SRID 4326，别拿 32640 的包络去查它。"""
    import psycopg
    c = (cfg.get("source") or {}).get("makani") or {}
    out = _dir(cfg); out.mkdir(parents=True, exist_ok=True)
    f = out / f"{name}.buildings.geojson"
    with psycopg.connect(host=c["host"], port=c["port"], user=c["user"], password=c["password"],
                         dbname=c["dbname"], connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select json_build_object('type','FeatureCollection','features',
                  coalesce(json_agg(json_build_object(
                    'type','Feature',
                    'geometry', st_asgeojson(shape)::json,
                    'properties', json_build_object('id', objectid))), '[]'::json))
                from public.bas_building
                where shape && st_makeenvelope(%s,%s,%s,%s,4326)
            """, bbox_wgs)
            gj = cur.fetchone()[0]
    n = len(gj.get("features") or [])
    f.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    log(f"  {name}: 建筑 {n} 个 → {f.name}")
    return f


def load_plots(cfg: dict, name: str):
    """可用地块 → (中心点 UTM 数组, 面积数组, 原始 feature 列表)。没有就返回空。"""
    import numpy as np
    f = _dir(cfg) / f"{name}.plots.geojson"
    if not f.exists():
        return np.zeros((0, 2)), np.zeros(0), []
    gj = json.loads(f.read_text(encoding="utf-8"))
    ft = gj.get("features") or []
    if not ft:
        return np.zeros((0, 2)), np.zeros(0), []
    xy = np.array([[float(x["properties"]["x"]), float(x["properties"]["y"])] for x in ft])
    ar = np.array([float(x["properties"]["area_m2"]) for x in ft])
    return xy, ar, ft


def snap_to_plot(cfg: dict, name: str, x: float, y: float, need_m2: float,
                 used: set | None = None):
    """把一个理想塘位吸附到最近的、装得下的可用地块上。

    装不下就退而求其次找大的；一个地块只用一次（used 记已占用的 plot_id）。
    找不到就返回 None —— 那个位置挖不了塘，如实说，不要硬塞。
    """
    import numpy as np
    P = params(cfg)
    xy, ar, ft = load_plots(cfg, name)
    if not len(ft):
        return None
    used = used or set()
    d = np.hypot(xy[:, 0] - x, xy[:, 1] - y)
    ok = (d <= float(P["search_radius_m"])) & (ar >= need_m2)
    for i in range(len(ft)):
        if ft[i]["properties"]["plot_id"] in used:
            ok[i] = False
    if not ok.any():                                  # 放宽：允许多块拼，先给最近的够大的一块
        ok = (d <= float(P["search_radius_m"]) * 1.5)
        for i in range(len(ft)):
            if ft[i]["properties"]["plot_id"] in used:
                ok[i] = False
        if not ok.any():
            return None
    i = int(np.argmin(np.where(ok, d, np.inf)))
    p = ft[i]["properties"]
    return {"plot_id": p["plot_id"], "use": p["use"], "area_m2": float(p["area_m2"]),
            "x": float(p["x"]), "y": float(p["y"]), "dist_m": float(d[i]),
            "geometry": ft[i]["geometry"], "fits": float(p["area_m2"]) >= need_m2}


# ---------------------------------------------------------------- 兜底：空地栅格
def open_land_mask(cfg: dict, name: str, bbox_utm, res: float = 10.0, log=print):
    """没有地块台账的地方，反过来排除：建筑退让 + 路廊退让之后剩下的就是候选空地。

    这是**兜底判据，不是等价物**。「没建筑、不在路上」不等于「这块地能用」——
    权属、规划、地下管线都没查。但它至少能把「挖在厂房里、挖在高速上」这类明显不可行的排掉，
    而且实测能把 Parsons 自己在 Mussafah 选的塘位（STO-PO3，60 m 内零建筑）识别出来。

    返回 (mask, transform)：mask 为 True 的格子是候选空地。
    """
    import json
    import numpy as np
    import pandas as pd
    from pyproj import Transformer
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from scipy import ndimage
    from shapely.geometry import LineString, shape as _shape
    from shapely.ops import transform as _reproj

    P = params(cfg)
    x0, y0, x1, y1 = bbox_utm
    W = max(int((x1 - x0) / res), 4); H = max(int((y1 - y0) / res), 4)
    free = np.ones((H, W), bool)
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)

    aff = from_origin(x0, y1, res, res)

    def burn(geoms, rad_m):
        """把一批**真实几何**烧进栅格再按退让距离膨胀，标成不可用。返回烧进去的个数。"""
        if not geoms:
            return 0
        m = rasterize(geoms, out_shape=(H, W), transform=aff, fill=0,
                      default_value=1, all_touched=True).astype(bool)
        if not m.any():
            return 0
        k = max(int(round(rad_m / res)), 1)
        yy, xx = np.ogrid[-k:k + 1, -k:k + 1]
        free[ndimage.binary_dilation(m, yy * yy + xx * xx <= k * k)] = False
        return len(geoms)

    # 建筑：烧**真实轮廓**。
    # 原先只取外环第一个顶点打一个固定半径的圆（注释写的是「质心 + 面积反推半径」，代码没做到）——
    # Mussafah 是工业区，一座仓库上百米长，角上那个 25 m 的圆盖不住，塘就挖进了厂房：
    # 独立查 bas_building，NP1 压掉 333 m²，占它自己面积的 26%。
    f = _dir(cfg) / f"{name}.buildings.geojson"
    nb = 0
    if f.exists():
        gj = json.loads(f.read_text(encoding="utf-8"))
        gs = []
        for ft in (gj.get("features") or []):
            g = ft.get("geometry")
            if not g:
                continue
            try:
                gs.append(_reproj(to_utm.transform, _shape(g)))
            except Exception:
                pass
        nb = burn(gs, float(P["build_buffer_m"]))

    # 台账里明确不可挖的地块：直接按地块边界排掉，不加退让（边界本身就是界）
    bf = _dir(cfg) / f"{name}.blocked.geojson"
    nblk = 0
    if bf.exists():
        bj = json.loads(bf.read_text(encoding="utf-8"))
        bs = []
        for ft in (bj.get("features") or []):
            g = ft.get("geometry")
            if not g:
                continue
            try:
                bs.append(_shape(g))                  # 这份导出就是 32640，不用投影
            except Exception:
                pass
        nblk = burn(bs, 0.0)

    # 路：把边的两端连成线段再烧。原先只打中点和顶点，长边中段是漏的——
    # 42% 的边超过 60 m，三个 20 m 的圆中间空得下一个塘。
    nr = 0
    ve = ROOT / "out" / "state" / "road_vertices.parquet"
    ee = ROOT / "out" / "state" / "road_edges.parquet"
    if ve.exists() and ee.exists():
        try:
            v = pd.read_parquet(ve, columns=["vid", "lon", "lat"]).set_index("vid")
            e = pd.read_parquet(ee, columns=["source", "target"])
            e = e.join(v.rename(columns={"lon": "slon", "lat": "slat"}), on="source")
            e = e.join(v.rename(columns={"lon": "tlon", "lat": "tlat"}), on="target")
            e = e.dropna(subset=["slon", "slat", "tlon", "tlat"])
            sx, sy = to_utm.transform(e["slon"].values, e["slat"].values)
            tx, ty = to_utm.transform(e["tlon"].values, e["tlat"].values)
            pad = 300.0
            m = ((np.maximum(sx, tx) >= x0 - pad) & (np.minimum(sx, tx) <= x1 + pad) &
                 (np.maximum(sy, ty) >= y0 - pad) & (np.minimum(sy, ty) <= y1 + pad))
            segs = [LineString([(a, b), (c, d)]) for a, b, c, d
                    in zip(sx[m], sy[m], tx[m], ty[m]) if (a, b) != (c, d)]
            nr = burn(segs, float(P["road_buffer_m"]))
        except Exception as exc:                       # 路网没准备好也要能出图，但要说出来
            log(f"  {name}: 路网没烧进来（{exc}）")

    log(f"  {name}: 栅格 {H}×{W} @ {res:g}m｜建筑 {nb:,} 个、路段 {nr:,} 条、"
        f"台账禁挖地块 {nblk:,} 个退让后，"
        f"空地占 {free.mean() * 100:.1f}%")
    return free, (x0, y1, res)


# 严格判据用到的 makani 图层：(表, SRID, 退让 m)。都是**真实面**，不是中心线。
PAVE_TABLES = (("udm_roadsurface", 4326), ("udm_roadshoulder", 4326), ("udm_roadisland", 4326),
               ("udm_roadintersection", 4326), ("main_roads_carriageway_ifp2018_1", 4326),
               ("main_roads_shoulders_ifp2018_1", 4326), ("main_roads_median_ifp2018_1", 4326))
PLOT_TABLES = (("udm_plot", 4326), ("ud_plot", 32640), ("upc_plot", 32640),
               ("masterplan_ud_plot", 32640),
               # 市政设施用地（变电站、泵站等）与铁路红线：同样是别人的地。NP5 第一轮就压了一块 utility plot 的 14%
               ("utility_plots_1", 4326), ("utility_plot", 4326), ("ud_utilityplots", 32640),
               ("upc_utilityplots", 32640), ("etihadrailrow", 4326))
RESERVE_TABLES = (("main_roads_row_ifp2018_guidance_only", 4326),
                  ("main_roads_utility_corridor_ifp2018_reservation", 4326), ("upc_row", 32640))
# 放宽口径下可以借用的地块：普通开发地块里**还没盖楼**的那些（要地块业主 / 规划部门同意）。
# 市政设施用地和铁路红线不在里面，放宽了也不碰
VACANT_OK = {"udm_plot", "ud_plot", "upc_plot", "masterplan_ud_plot"}
PAVE_BUFFER_M = 5.0      # 基坑边离路面边的最小净距：开挖支护与施工围挡要占这一条


def strict_land_masks(cfg: dict, bbox_utm, res: float = 5.0, log=print, vacant: bool = False):
    """箱涵选址用的严格判据。和 open_land_mask 的区别：

    open_land_mask 拿路网**中心线**加 30 m 当路，拿台账「禁挖地块」当地块——Mussafah 没有台账覆盖，
    结果六座箱涵里 NP1/NP2/NP5 压在已分配的工业地块上，NP3/NP4 整座落在快速路红线里（2026-09-26 查实）。
    这里直接用客户 makani 的真实面：
      · 路面 / 路肩 / 交通岛 / 路口 / 干道车行道与中分带，外扩 PAVE_BUFFER_M —— 不可用
      · 建筑轮廓外扩 build_buffer_m —— 不可用
      · 任何一套地块台账里的地块（已分配 / 规划中）、市政设施用地、铁路红线 —— 不可用，那是别人的地
      · 干道红线、管线走廊预留带、规划红线 —— **次选**：不动路面，但要道路主管部门批准，
        而且下面可能已经有管线

    返回 (free1, free2, tf, stats)：free1 是首选空地（避开红线），free2 允许用红线。
    vacant=True 时返回 (free1, free2, free3, tf, stats)：free3 在 free2 基础上再放开
    VACANT_OK 里**轮廓内没有任何建筑**的地块——放宽口径，只作对比。
    """
    import numpy as np
    from pyproj import Transformer
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from scipy import ndimage
    from shapely import wkb
    from . import db

    P = params(cfg)
    x0, y0, x1, y1 = bbox_utm
    W = max(int((x1 - x0) / res), 4); H = max(int((y1 - y0) / res), 4)
    aff = from_origin(x0, y1, res, res)
    to_wgs = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    lo0, la0 = to_wgs.transform(x0, y0); lo1, la1 = to_wgs.transform(x1, y1)

    def fetch(cur, table, srid):
        env = (f"st_makeenvelope({lo0},{la0},{lo1},{la1},4326)" if srid == 4326
               else f"st_makeenvelope({x0},{y0},{x1},{y1},32640)")
        cur.execute(f"select st_asbinary(st_transform(st_makevalid(shape),32640)) from public.{table} "
                    f"where shape && {env} and not st_isempty(shape)")
        return [wkb.loads(bytes(r[0])) for r in cur.fetchall() if r[0]]

    def mask_of(geoms, rad_m):
        if not geoms:
            return np.zeros((H, W), bool)
        m = rasterize(geoms, out_shape=(H, W), transform=aff, fill=0,
                      default_value=1, all_touched=True).astype(bool)
        k = int(round(rad_m / res))
        if k > 0:
            yy, xx = np.ogrid[-k:k + 1, -k:k + 1]
            m = ndimage.binary_dilation(m, yy * yy + xx * xx <= k * k)
        return m

    stats = {}
    conn = db.connect_source(cfg, "makani")
    with conn, conn.cursor() as cur:
        pave = sum((fetch(cur, t, s) for t, s in PAVE_TABLES), [])
        bld = fetch(cur, "bas_building", 4326)
        plots_by = {t: fetch(cur, t, s) for t, s in PLOT_TABLES}
        plots = sum(plots_by.values(), [])
        rsv = sum((fetch(cur, t, s) for t, s in RESERVE_TABLES), [])
    hard = (mask_of(pave, PAVE_BUFFER_M) | mask_of(bld, float(P["build_buffer_m"]))
            | mask_of(plots, 0.0))
    reserve = mask_of(rsv, 0.0)
    free2 = ~hard
    free1 = free2 & ~reserve
    stats = {"pave": len(pave), "buildings": len(bld), "plots": len(plots), "reserve": len(rsv),
             "free1_pct": round(free1.mean() * 100, 1), "free2_pct": round(free2.mean() * 100, 1)}
    log(f"  严格用地判据 {H}×{W} @ {res:g} m｜路面类 {len(pave):,}、建筑 {len(bld):,}、"
        f"地块 {len(plots):,}、红线/走廊 {len(rsv):,}｜首选空地 {stats['free1_pct']}%、"
        f"含红线 {stats['free2_pct']}%")
    if not vacant:
        return free1, free2, (x0, y1, res), stats
    from shapely.strtree import STRtree
    tree = STRtree(bld)
    keep, n_vac = [], 0
    for t, gs in plots_by.items():
        for g in gs:
            if t in VACANT_OK and not len(tree.query(g, predicate="intersects")):
                n_vac += 1
            else:
                keep.append(g)
    free3 = ~(mask_of(pave, PAVE_BUFFER_M) | mask_of(bld, float(P["build_buffer_m"])) | mask_of(keep, 0.0))
    stats.update({"vacant_plots": n_vac, "free3_pct": round(free3.mean() * 100, 1)})
    log(f"  放宽：没盖楼的开发地块 {n_vac:,} 块可借用 → 可用地 {stats['free3_pct']}%")
    return free1, free2, free3, (x0, y1, res), stats


def fit_square(free, tf, x: float, y: float, need_m2: float, max_r_m: float = 1200.0):
    """在空地栅格里找一块装得下 need_m2 的方形，离 (x,y) 最近的那块。

    先腐蚀出「一整块方形都空着」的中心点，再取最近的。找不到返回 None——
    那个位置挖不了，如实说。
    """
    import numpy as np
    from scipy import ndimage
    x0, y1, res = tf
    side = max(np.sqrt(need_m2), res * 2)
    # 腐蚀核取**奇数**：scipy 的偶数核原点不居中，返回的中心到一侧少半个格，
    # 塘的方形轮廓会探出空地边界（36 m 的塘实测探出 3 m，正好够压到旁边的楼）
    k = int(np.ceil(side / res))
    k = max(k + (k % 2 == 0), 1)
    fits = ndimage.binary_erosion(free, np.ones((k, k), bool))
    if not fits.any():
        return None
    H, W = free.shape
    cx = (x - x0) / res; cy = (y1 - y) / res
    rr, cc = np.nonzero(fits)
    d = np.hypot((cc + .5) - cx, (rr + .5) - cy) * res
    m = d <= max_r_m
    if not m.any():
        return None
    i = int(np.argmin(np.where(m, d, np.inf)))
    px = x0 + (cc[i] + .5) * res; py = y1 - (rr[i] + .5) * res
    return {"x": float(px), "y": float(py), "dist_m": float(d[i]),
            "side_m": float(side), "area_m2": float(side * side), "source": "open_land"}


def main(argv=None):
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import pandas as pd
    from pyproj import Transformer
    from . import db
    ap = argparse.ArgumentParser(description="导出可挖塘的地（土地利用约束）")
    ap.add_argument("--pad", type=float, default=800.0)
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    cfg = db.load_config(a.config)
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    tl = ROOT / "out" / "three_lines"
    for f in sorted(tl.glob("*.L1.nodes.parquet")):
        name = f.name.split(".")[0]
        d = pd.read_parquet(f)
        x, y = tr.transform(d["lon"].values, d["lat"].values)
        bb = (x.min() - a.pad, y.min() - a.pad, x.max() + a.pad, y.max() + a.pad)
        export_plots(cfg, bb, name)
        export_blocked_plots(cfg, bb, name)
        p = a.pad / 100000.0
        export_buildings(cfg, (d["lon"].min() - p, d["lat"].min() - p,
                               d["lon"].max() + p, d["lat"].max() + p), name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
