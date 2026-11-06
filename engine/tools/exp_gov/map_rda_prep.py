"""RD-A 治理前后管网图的数据准备（系统 Python）：写 D:/swbrain_work/exp_gov/09_maps/rda_maps.gpkg，供 map_rda_export.py 在 ArcGIS Pro 里出图。

  python tools/exp_gov/map_rda_prep.py [治理后导出目录，默认 07_export]
图层：
  domain        RD-A 范围
  raw_pipes     DMT 原始管（范围内）：up_minus_dtm = 原始管底 − 地面（m），to_outfall = 按 UN 容差几何上能连到出水口（0/1）
  raw_dangling  原始管的悬空端点（UN 容差内不碰别的管和点）
  raw_outfalls  原始出水口
  gov_lines     治理后的管（范围外扩 300 m）：traced = 挂上子网（1/0），sub = 子网名
  gov_outfalls  治理后的出水口；gov_pumps 泵
"""
import collections
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy.spatial import cKDTree

W = "D:/swbrain_work/exp_gov"
EXP = sys.argv[1] if len(sys.argv) > 1 else "07_export"
OUT = f"{W}/09_maps/rda_maps.gpkg"
TOL = 0.0021
dom = gpd.read_file("D:/阿布扎比/排水智能体/out/dataqc/RD-A/input/rda_domain.geojson").to_crs(32640)
D = dom.geometry.iloc[0]
bb = D.buffer(1500).bounds
src = f"{W}/src_dmt.gdb"
P = gpd.read_file(src, layer="PIPELINE", bbox=bb, columns=["UNITID", "STATUS", "INVERT_LEV", "INVERT_L_1"], fid_as_index=True)
pts = []
for lay in ["MANHOLECHAMBER", "INLET", "CATCHBASIN", "SW_CAPPEDEND", "OUTFALL", "PUMP", "SOAKAWAY"]:
    g = gpd.read_file(src, layer=lay, bbox=bb, columns=["UNITID"])
    g["lay"] = lay
    pts.append(g)
Pt = pd.concat(pts, ignore_index=True)
E = np.array([c for g in P.geometry.values for c in shapely.get_coordinates(g)[[0, -1]]])
te, tp = cKDTree(E), cKDTree(shapely.get_coordinates(Pt.geometry.values))
par = list(range(len(P)))


def root(x):
    while par[x] != x:
        par[x] = par[par[x]]
        x = par[x]
    return x
for a, b in te.query_pairs(TOL):
    par[root(a // 2)] = root(b // 2)
at = collections.defaultdict(list)
for i, e in enumerate(E):
    for j in tp.query_ball_point(e, TOL):
        at[j].append(i // 2)
for ps in at.values():
    for p in ps[1:]:
        par[root(p)] = root(ps[0])
ofc = {root(i // 2) for j in Pt.index[Pt.lay == "OUTFALL"] for i in te.query_ball_point(shapely.get_coordinates(Pt.geometry.iloc[j])[0], TOL)}
P["to_outfall"] = [int(root(i) in ofc) for i in range(len(P))]
dang = [i for i, e in enumerate(E) if tp.query(e)[0] > TOL and not [j for j in te.query_ball_point(e, TOL) if j // 2 != i // 2]]
# 原始管底 − 地面：用 s2a 采的 DTM（管两端）
dtm = pd.read_csv(f"{W}/02_prep/dtm_at_dmt.csv", dtype={"oid": str})
dtm = dtm[dtm.layer == "PIPELINE"].set_index("oid").dtm
ds = np.array([dtm.get(f"{o}_s", np.nan) for o in P.index], float)
P["up_minus_dtm"] = np.round(pd.to_numeric(P.INVERT_LEV, errors="coerce").values - ds, 2)
P["in_domain"] = P.intersects(D).astype(int)
raw = P[P.in_domain == 1].copy()
dpts = gpd.GeoDataFrame(geometry=gpd.points_from_xy(E[dang, 0], E[dang, 1]), crs=32640)
dpts = dpts[dpts.intersects(D)]
rof = Pt[(Pt.lay == "OUTFALL") & Pt.intersects(D.buffer(1500))]
print(f"原始：范围内管 {len(raw)}，几何上连得到出水口 {int(raw.to_outfall.sum())}，连不到 {int((raw.to_outfall == 0).sum())}；"
      f"悬空端点 {len(dpts)}；管底 − 地面 中位 {np.nanmedian(raw.up_minus_dtm):.1f} m")
gb = D.buffer(300)
G = gpd.read_file(f"{W}/{EXP}/Stormwater_AD_reporting.gdb", layer="StormwaterLine", bbox=gb.bounds, columns=["assetid", "smasubnetworkname", "d_assetgroup"])
G = G[G.intersects(gb)].copy()
G["traced"] = (G.smasubnetworkname.notna() & (G.smasubnetworkname != "Unknown")).astype(int)
G = G.rename(columns={"smasubnetworkname": "sub", "d_assetgroup": "grp"})
G["in_domain"] = G.intersects(D).astype(int)
dev = gpd.read_file(f"{W}/{EXP}/Stormwater_AD_reporting.gdb", layer="StormwaterDevice", bbox=D.buffer(1500).bounds, columns=["assetid", "d_assetgroup", "smasubnetworkname"])
inD = G.intersects(D)
print(f"治理后：范围内管 {int(inD.sum())}，挂上子网 {int(G[inD].traced.sum())}")
for name, df in (("domain", dom[["geometry"]]), ("raw_pipes", raw[["UNITID", "STATUS", "up_minus_dtm", "to_outfall", "geometry"]]),
                 ("raw_pipes_all", P[["UNITID", "STATUS", "up_minus_dtm", "to_outfall", "in_domain", "geometry"]]),   # 范围外扩 1.5 km 的全部原始管（局部放大图用）
                 ("raw_dangling", dpts), ("raw_outfalls", rof[["UNITID", "geometry"]]), ("gov_lines", G[["assetid", "sub", "grp", "traced", "in_domain", "geometry"]]),
                 ("gov_outfalls", dev[dev.d_assetgroup == "Outfall"][["assetid", "smasubnetworkname", "geometry"]]),
                 ("gov_pumps", dev[dev.d_assetgroup == "Pump"][["assetid", "geometry"]])):
    gpd.GeoDataFrame(df, crs=32640).to_file(OUT, layer=name, driver="GPKG")
print("写出", OUT)
