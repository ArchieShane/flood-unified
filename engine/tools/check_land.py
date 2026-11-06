# -*- coding: utf-8 -*-
"""塘到底挖在了什么地方？——把用户那句「你现在有的把高速公路都挖了」变成可核的数。

对每个塘的**真实轮廓**（不是中心点）量三件事：
  1. 压到建筑没有（轮廓与建筑面相交的面积）
  2. 离最近的路多远（路网点到轮廓边界的距离）
  3. 落在哪类地块上（makani ud_plot；没覆盖的地方标「台账无覆盖」）

判据是「轮廓」而不是「中心点」：中心点避开了、边压在楼上，照样挖不了。
"""
import json, pathlib, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psycopg
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform as shp_transform
from swbrain import db

OUT = ROOT / "out" / "three_lines"
cfg = db.load_config(None)
c = cfg["source"]["makani"]
conn = psycopg.connect(host=c["host"], port=c["port"], user=c["user"], password=c["password"],
                       dbname=c["dbname"], connect_timeout=20)
to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True).transform

# 路网在 out/state 的 parquet 里，不在库里（landuse.py 用的也是这两份）
import pandas as pd
from scipy.spatial import cKDTree
_rv = []
for fn, lo, la in (("road_edges.parquet", "mid_lon", "mid_lat"), ("road_vertices.parquet", "lon", "lat")):
    q = ROOT / "out" / "state" / fn
    if q.exists():
        d = pd.read_parquet(q, columns=[lo, la]).dropna()
        x, y = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True).transform(
            d[lo].values, d[la].values)
        _rv.append(np.c_[x, y])
_RT = cKDTree(np.vstack(_rv)) if _rv else None


def rd_dist(g):
    """塘轮廓边界到最近路网点的距离（负数表示路点落在塘里）。"""
    if _RT is None:
        return None
    d, i = _RT.query([g.centroid.x, g.centroid.y], k=1)
    from shapely.geometry import Point
    pt = Point(*_RT.data[i])
    return -pt.distance(g.exterior) if g.contains(pt) else pt.distance(g)

print(f"{'区':<10}{'塘':<5}{'占地 m²':>9}{'压到建筑':>10}{'离最近路':>10}{'地块用地':>26}")
print("-" * 74)
bad = 0
for m in ("Mussafah", "Shaliela", "RB3"):
    f = OUT / f"{m}.L2P.ponds.geojson"
    if not f.exists():
        continue
    gj = json.loads(f.read_text(encoding="utf-8"))
    for ft in gj["features"]:
        g = shp_transform(to_utm, shape(ft["geometry"]))
        wkt = g.wkt
        with conn.cursor() as cur:
            # 建筑表 bas_building 的几何列叫 shape，且是 SRID 4326；地块 ud_plot 是 32640
            cur.execute("""
                with p as (select st_geomfromtext(%s, 32640) g),
                     w as (select st_transform(g, 4326) g from p)
                select
                  coalesce((select sum(st_area(st_transform(
                        st_intersection(b.shape, w.g), 32640)))
                     from public.bas_building b, w
                    where b.shape && w.g and st_intersects(b.shape, w.g)), 0)::float,
                  (select string_agg(distinct u.level_1_allocation_type_eng, ' / ')
                     from public.ud_plot u, p
                    where u.shape && p.g and st_intersects(u.shape, p.g))
            """, (wkt,))
            ov, use = cur.fetchone()
        dist = rd_dist(g)
        pr = ft["properties"]
        flag = ""
        if (ov or 0) > 1.0:
            flag = "  ← 压到建筑"; bad += 1
        if dist is not None and dist < 5.0:
            flag += ("  ← 路点落在塘里" if dist < 0 else "  ← 贴着路"); bad += 1
        print(f"{m:<10}{pr['id']:<5}{g.area:>9,.0f}{(ov or 0):>10,.0f}"
              f"{('%.0f m' % dist) if dist is not None else '>300 m':>10}"
              f"{str(use or '台账无覆盖')[:25]:>26}{flag}")
print("-" * 74)
print(f"压到建筑或贴着路的：{bad} 处")
conn.close()
