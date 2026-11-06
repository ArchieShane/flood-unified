"""RD-A 局部放大图的数据（系统 Python）：选一处「治理前断开、治理后接通」的位置，写 09_maps/rda_zoom.gpkg，供 map_rda_zoom_export.py 出图。

  python tools/exp_gov/map_rda_zoom_prep.py <中心 x> <中心 y> <半宽 m> [治理后导出目录，默认 07_export]
图层：raw_ok / raw_bad（原始管：连得到 / 连不到出水口）、raw_bad_abandoned（其中 DMT 记为废弃的）、raw_dangling、raw_mh、raw_inlet、raw_outfall、
      gov_dmt（治理后 DMT 的管，traced）、gov_aecom（AECOM 补的管）、gov_excluded（被排除的原始管）、gov_mh、gov_inlet、gov_outfall、box（出图范围）
"""
import sys

import geopandas as gpd
import pyogrio
import shapely
from shapely.geometry import box as sbox

W = "D:/swbrain_work/exp_gov"
cx, cy, hw = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
EXP = sys.argv[4] if len(sys.argv) > 4 else "07_export"
B = sbox(cx - hw * 1.4, cy - hw, cx + hw * 1.4, cy + hw)       # 版面横向，宽:高 ≈ 1.4
bb = B.bounds
OUT = f"{W}/09_maps/rda_zoom.gpkg"
src = f"{W}/src_dmt.gdb"
M = f"{W}/09_maps/rda_maps.gpkg"
raw = gpd.read_file(M, layer="raw_pipes_all", bbox=bb)
sub = pyogrio.read_dataframe(src, layer="PIPELINE", columns=["UNITID", "PIPE_SUBTY"], bbox=bb, read_geometry=False)
raw = raw.merge(sub, on="UNITID", how="left")
X = pyogrio.read_dataframe(f"{W}/{EXP}/governance_records.gdb", layer="EXCLUDED", read_geometry=False)
excl = set(X.assetid)
lay = {
    "raw_ok": raw[raw.to_outfall == 1],
    "raw_bad": raw[(raw.to_outfall == 0) & (raw.PIPE_SUBTY != "Abandoned")],
    "raw_bad_abandoned": raw[(raw.to_outfall == 0) & (raw.PIPE_SUBTY == "Abandoned")],
    "raw_dangling": gpd.read_file(M, layer="raw_dangling", bbox=bb),
    "raw_mh": gpd.read_file(src, layer="MANHOLECHAMBER", bbox=bb, columns=["UNITID"]),
    "raw_inlet": gpd.read_file(src, layer="INLET", bbox=bb, columns=["UNITID"]),
    "raw_outfall": gpd.read_file(src, layer="OUTFALL", bbox=bb, columns=["UNITID"]),
    "raw_pump": gpd.read_file(src, layer="PUMP", bbox=bb, columns=["UNITID", "FLOW_RATE", "NO_OF_PUMP"]),
}
rg = f"{W}/{EXP}/Stormwater_AD_reporting.gdb"
G = gpd.read_file(rg, layer="StormwaterLine", bbox=bb, columns=["assetid", "smasubnetworkname"])
G["traced"] = (G.smasubnetworkname.notna() & (G.smasubnetworkname != "Unknown")).astype(int)
aec = G.assetid.astype(str).str.startswith("AECOM")
Gt = gpd.read_file(rg, layer="StormwaterLine", bbox=bb, columns=["assetid", "d_assetgroup"])
force = set(Gt[Gt.d_assetgroup == "Force Pipe"].assetid)
lay["gov_dmt"] = G[~aec]
lay["gov_aecom"] = G[aec & ~G.assetid.isin(force)]
lay["gov_force"] = G[G.assetid.isin(force)]
lay["gov_excluded"] = raw[raw.UNITID.isin(excl)]
dev = gpd.read_file(rg, layer="StormwaterDevice", bbox=bb, columns=["assetid", "d_assetgroup", "d_assettype"])
lay["gov_mh"] = dev[dev.d_assetgroup == "Manhole Channel"]
lay["gov_inlet"] = dev[dev.d_assetgroup == "Catch Basin"]
lay["gov_outfall"] = dev[dev.d_assetgroup == "Outfall"]
lay["gov_pump"] = dev[dev.d_assetgroup == "Pump"]
lay["gov_wetwell"] = dev[(dev.d_assetgroup == "Connection") & (dev.d_assettype == "Pipe Connection")]
lay["box"] = gpd.GeoDataFrame(geometry=[B], crs=32640)
import os
if os.path.exists(OUT):
    os.remove(OUT)
for k, df in lay.items():
    df = gpd.GeoDataFrame(df, crs=32640)
    if len(df) == 0:
        df = gpd.GeoDataFrame({"dummy": []}, geometry=gpd.GeoSeries([], crs=32640), crs=32640)
    df.to_file(OUT, layer=k, driver="GPKG")
    print(k, len(df))
