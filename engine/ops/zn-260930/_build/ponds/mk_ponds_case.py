# 把去重后的塘（pond_inventory.py → ponds_all.geojson，pond_pipes.py → ponds_pipes.pkl）写成 cases/ad/ponds_all.geojson，给 ad_regions.write_ponds 用。
# 字段：seq（与「蓄水池清单」序号一致）、pid、cat、vol_m3、depth_m、area_m2、pipes_5m、attr_src
# 10-16 用户给了 ponds.zip（Parsons 21 个子模型的 152 个塘面，字段统一：storage_ma 库容 m³、depth_avg 平均深度 m；是 261015 版）：
# 和我的塘按重叠 >30% 较小者对上的，库容 / 深度改用它的（它是新版、字段齐；我原来的 Existing Storage RB2 是旧版）。
import geopandas as gpd, pandas as pd, os
T = os.environ["TEMP"]
D = gpd.read_file(T + "/ponds_all.geojson"); K = pd.read_pickle(T + "/ponds_pipes.pkl")
D["pipes_5m"] = K["pipes_5m"].values
D["seq"] = range(1, len(D) + 1)
for c in ("vol_m3", "depth_m"):
    D[c] = pd.to_numeric(D[c], errors="coerce")
D["attr_src"] = "原始来源"
U = gpd.read_file(T + "/ponds_zip/ponds.shp").to_crs(32640)
Dm = D.to_crs(32640); s = U.sindex; n = 0
for i, r in Dm.iterrows():
    for j in s.query(r.geometry, predicate="intersects"):
        u = U.iloc[j]
        if r.geometry.intersection(u.geometry).area > 0.3 * min(r.geometry.area, u.geometry.area):
            D.loc[i, "vol_m3"] = float(u["storage_ma"]); D.loc[i, "depth_m"] = float(u["depth_avg"])
            D.loc[i, "attr_src"] = f"ponds.zip {u['model']}/{u['label']}"; n += 1
            break
D = D[["seq", "pid", "cat", "src", "sources", "vol_m3", "depth_m", "area_m2", "pipes_5m", "attr_src", "adm", "geometry"]]
D.to_file("C:/freedo/zn-260930/thinking-drainage-system/cases/ad/ponds_all.geojson", driver="GeoJSON")
print(len(D), "其中用 ponds.zip 属性的", n)
