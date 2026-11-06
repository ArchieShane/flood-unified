# -*- coding: utf-8 -*-
"""RD-A 子汇水区去重 + 合成 CN（2026-10-20，用户：「第一项完全可以按照你的建议来」）。

问题：客户 SewerGEMS 模型把 1,914 个地块各拆成透水（12SU####，CN 61–86）与不透水（12SUI####，CN 95）两块，几何相同、面积都按整块计
      （617 ha 对 311 ha），客户入流是双倍面积。
做法：每个地块只算一次面积，按不透水比例 f 拆：不透水部分 f·A 用 CN 95、透水部分 (1 − f)·A 用 CN透水，两部分各算径流再相加
      （WA-726 第一卷 3.4.1 节：城区透水面不是「良好草地」时，透水、不透水分别估 CN、分别算径流、相加；客户也是这个做法，只是没拆面积）。
      2026-10-20 先提的是合成一个 CN（f·95 + (1 − f)·CN透水），改成分开算：CN 平均是非线性的，合成 CN 会低估小雨的径流。合成 CN 仍记在 cn_composite 列。
      不成对的 75 块原样。出口节点、汇流时间两块相同（已核：0 对不同）。
f（不透水比例）从 Makani 现状（只读）在 1 m 栅格上数，Claude 自定的口径：
  不透水 = 建筑（bas_building、bdms_bldg_landuse、udm_bldroofsurface）∪ 道路（路面 / 路肩 / 交通岛 / 路口 / 干道车行道、路肩、中分带）∪ 停车（ud_parkingarea、udm_parking）
  透水   = 绿地（ud_greenarea、upc_greenarea、udm_park、udm_irrigationsurface、udm_puttinggreen）∪ 未建地块（udm_plot 未建 / upc_plot Vacant）里没被前两类压住的部分 ∪ 水面
  其余（院内硬地、人行道、广场等 Makani 没单独画的）按不透水 —— 岛上是密集城区，这部分多为硬化地面
  另记下限 f_min = 只数「不透水」三类，供对照
用法：python tools/rda_catchments.py --out out/rda/catchments_composite.gpkg
产物：<out>（每地块一块：label、out_node_id、area_m2、cn、tc_min、f、f_min、cn_perv、src），同名 .json（统计与口径）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from swbrain import client_data  # noqa: E402
SHP = client_data.model("RD-A") / "2026-1hr/Catchment.shp"
RES = 1.0
BLD = (("bas_building", 4326), ("bdms_bldg_landuse", 4326), ("udm_bldroofsurface", 4326))
PARK = (("ud_parkingarea", 32640), ("udm_parking", 4326))
GREEN = (("ud_greenarea", 32640), ("upc_greenarea", 32640), ("udm_park", 4326), ("udm_irrigationsurface", 4326), ("udm_puttinggreen", 4326))
WATER = (("udm_watersurface", 4326), ("udm_seasurface", 4326), ("udm_fountainsurface", 4326))


def srid_of(cur, table):
    cur.execute("select find_srid('public', %s, 'shape')", (table,))
    return cur.fetchone()[0]


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "out/rda/catchments_composite.gpkg"))
    a = ap.parse_args(argv)
    import geopandas as gpd
    import pandas as pd
    from pyproj import Transformer
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from shapely import wkb
    from swbrain import db
    from swbrain.landuse import PAVE_TABLES

    cat = gpd.read_file(SHP).set_crs(32640, allow_override=True)
    cat["wkb"] = cat.geometry.normalize().to_wkb()
    x0, y0, x1, y1 = cat.total_bounds + np.array([-5, -5, 5, 5])
    W, H = int(np.ceil((x1 - x0) / RES)), int(np.ceil((y1 - y0) / RES))
    tf = from_origin(x0, y1, RES, RES)
    tw = Transformer.from_crs(32640, 4326, always_xy=True)
    lo0, la0 = tw.transform(x0, y0); lo1, la1 = tw.transform(x1, y1)

    def env(srid):
        return f"st_makeenvelope({lo0},{la0},{lo1},{la1},4326)" if srid == 4326 else f"st_makeenvelope({x0},{y0},{x1},{y1},32640)"

    cfg = db.load_config(None)
    conn = db.connect_source(cfg, "makani", timeout_ms=600_000)
    counts = {}

    def layer(cur, tables, where=""):
        geoms = []
        for t, srid in tables:
            try:
                cur.execute(f"select st_asbinary(st_transform(st_makevalid(shape),32640)) from public.{t} where shape && {env(srid)} and not st_isempty(shape){where}")
                g = [wkb.loads(bytes(r[0])) for r in cur.fetchall() if r[0]]
            except Exception as e:                       # 表不存在 / 坐标系不对：记下，不中断
                conn.rollback(); counts[t] = f"读不到：{str(e).splitlines()[0][:80]}"; continue
            g = [x for x in g if x.geom_type in ("Polygon", "MultiPolygon", "GeometryCollection") and not x.is_empty]
            counts[t] = len(g); geoms += g
        return geoms

    def ras(geoms):
        if not geoms:
            return np.zeros((H, W), bool)
        return rasterize(((g, 1) for g in geoms), out_shape=(H, W), transform=tf, fill=0, dtype="uint8").astype(bool)

    with conn, conn.cursor() as cur:
        imp = ras(layer(cur, BLD)) | ras(layer(cur, PAVE_TABLES)) | ras(layer(cur, PARK))
        green = ras(layer(cur, GREEN)) | ras(layer(cur, WATER))
        vac = ras(layer(cur, (("udm_plot", 4326),), " and construction_status ilike 'not constructed%'")) | \
              ras(layer(cur, (("upc_plot", 32640),), " and existing_land_use ilike 'vacant%'"))
    perv = (green | vac) & ~imp
    print("Makani 要素数：", counts)

    # 地块：成对的合成一块
    rows = []
    for _, g in cat.groupby("wkb", sort=False):
        imp_r = g[g.LABEL.str.startswith("12SUI")]
        pv = g[~g.LABEL.str.startswith("12SUI")]
        base = pv.iloc[0] if len(pv) else g.iloc[0]
        rows.append({"label": base.LABEL, "labels": ",".join(g.LABEL), "out_node_id": int(base.OUT_NODEID), "out_node": str(base.OUT_NODE),
                     "area_m2": float(base.geometry.area), "tc_min": float(base.TC), "cn_perv": float(base.SCS_CN),
                     "paired": len(g) == 2 and len(imp_r) == 1 and len(pv) == 1,
                     "same_outlet": len(set(g.OUT_NODEID)) == 1, "same_tc": len(set(g.TC)) == 1, "geometry": base.geometry})
    P = gpd.GeoDataFrame(rows, crs=32640)
    shapes = [(geom, i + 1) for i, geom in enumerate(P.geometry)]
    pid = rasterize(shapes, out_shape=(H, W), transform=tf, fill=0, dtype="int32")
    n = len(P) + 1
    tot = np.bincount(pid.ravel(), minlength=n).astype(float)
    n_imp = np.bincount(pid.ravel(), weights=imp.ravel(), minlength=n)
    n_perv = np.bincount(pid.ravel(), weights=perv.ravel(), minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        f_min = np.where(tot > 0, n_imp / tot, np.nan)[1:]
        f = np.where(tot > 0, 1.0 - n_perv / tot, np.nan)[1:]
    P["f_min"] = np.round(f_min, 3); P["f"] = np.round(f, 3)
    # 成对的才合成；不成对的原样（其 CN 就是客户给的）
    P["cn"] = np.where(P.paired, np.round(P.f * 95.0 + (1 - P.f) * P.cn_perv, 2), P.cn_perv)        # 合成 CN，只作记录
    P = P.rename(columns={"cn": "cn_composite"})
    P["src"] = np.where(P.paired, "成对合成：f·95 + (1−f)·CN透水（f 取 Makani）", "不成对：客户原值")
    small = P.paired & (tot[1:] < 4)                      # 小于 4 格的地块栅格数不准：f 取全域成对地块面积加权
    if small.any():
        fw = float(np.average(P.f[P.paired & ~small], weights=P.area_m2[P.paired & ~small]))
        P.loc[small, "f"] = round(fw, 3)
        P.loc[small, "cn_composite"] = np.round(fw * 95 + (1 - fw) * P.cn_perv[small], 2)
        P.loc[small, "src"] = "成对合成：地块太小，f 取全域加权值"
    out = pathlib.Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    P.to_file(out, layer="catchments")
    # 产流用的子汇水区表：成对的拆成不透水 / 透水两行，面积按 f 分；不成对的原样一行
    subs = []
    for r in P.itertuples():
        if r.paired:
            subs.append((r.label.replace("12SU", "12SUI", 1), r.out_node_id, r.area_m2 * r.f, 95.0, r.tc_min, "不透水"))
            subs.append((r.label, r.out_node_id, r.area_m2 * (1 - r.f), r.cn_perv, r.tc_min, "透水"))
        else:
            subs.append((r.label, r.out_node_id, r.area_m2, r.cn_perv, r.tc_min, "不成对"))
    pd.DataFrame(subs, columns=["label", "node", "area_m2", "cn", "tc_min", "part"]).to_csv(out.with_suffix(".subs.csv"), index=False, encoding="utf-8")
    pr = P[P.paired]
    st = {"rule": __doc__.split("用法")[0].strip(), "makani_counts": counts, "res_m": RES,
          "n_client": len(cat), "n_plots": len(P), "n_paired": int(P.paired.sum()), "n_single": int((~P.paired).sum()),
          "area_client_ha": round(float(cat.A.sum()), 2), "area_ha": round(float(P.area_m2.sum() / 1e4), 2),
          "f_aw": round(float(np.average(pr.f, weights=pr.area_m2)), 3), "f_min_aw": round(float(np.average(pr.f_min, weights=pr.area_m2)), 3),
          "f_quantiles": {q: round(float(np.nanpercentile(pr.f, q)), 3) for q in (5, 25, 50, 75, 95)},
          "cn_composite_aw": round(float(np.average(P.cn_composite, weights=P.area_m2)), 2),
          "pairs_diff_outlet": int((P.paired & ~P.same_outlet).sum()), "pairs_diff_tc": int((P.paired & ~P.same_tc).sum()),
          "small_plots_fallback": int(small.sum())}
    out.with_suffix(".json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in st.items() if k not in ("rule", "makani_counts")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
