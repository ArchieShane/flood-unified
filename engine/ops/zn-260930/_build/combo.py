import sys, glob, warnings, itertools, os
warnings.filterwarnings("ignore")
sys.path.insert(0,'tools'); sys.path.insert(0,'.')
import geopandas as gpd, numpy as np
from shapely.ops import unary_union
from build_catchment_map import kmz_polygons
def kp(k, n):
    t=[t[1] for t in kmz_polygons(k) if t[0]==n and t[1].geom_type=="Polygon"]
    return gpd.GeoSeries([unary_union(t).buffer(0)], crs=4326).to_crs(32640).iloc[0]
KMZ = {"MI-8":("Shahamah_Zayed catchments.kmz",36.53),"RB-3":("Shahamah_Zayed catchments.kmz",15.56),"MBZ":("Shahamah_Zayed catchments.kmz",15.26),"YAS OUTFALL":("Shahamah_Zayed catchments.kmz",13.02),
       "RD-F":("island catchment.kmz",5.44),"RD-C":("island catchment.kmz",1.94),"Qasr Al Bahr-1":("island catchment.kmz",1.52),"RD-I":("island catchment.kmz",5.79),"RD-G":("island catchment.kmz",4.28),"RD-A":("island catchment.kmz",3.21),
       "RB-1":("Shahamah_Zayed catchments.kmz",18.40),"RBE1-OF1":("Shahamah_Zayed catchments.kmz",22.06),"RD 14":("Al Wathba catchments.kmz",15.44),"RD-2":("Shahamah_Zayed catchments.kmz",126.99)}
print("KMZ 多边形面积 vs 客户表")
D={}
for n,(k,c) in KMZ.items():
    D[n]=kp(k,n); a=D[n].area/1e6; print(f"  {n:15s} KMZ {a:7.2f}  客户 {c:7.2f}  差 {100*(a-c)/c:+.1f}%")
# 模型子汇水区图层（含无 CRS 的按 UTM40N 处理，且去重）
L={}; seen=set()
for f in sorted(glob.glob('data/**/*.shp', recursive=True)):
    b=os.path.basename(f).lower()
    if not any(s in b for s in ("catch","cat.shp","ca.shp","catchment table")): continue
    g=gpd.read_file(f)
    if g.empty or not g.geom_type.isin(["Polygon","MultiPolygon"]).any(): continue
    g = g.set_crs(32640) if g.crs is None else g.to_crs(32640)
    key=(len(g),round(g.geometry.area.sum()))
    if key in seen: continue
    seen.add(key)
    u=unary_union(g.geometry.buffer(0).values)
    L[f.split('data'+os.sep)[-1] if False else f[5:]]=u
print("\n模型子汇水区图层 与 KMZ 多边形的重叠（该图层有多少面积落在多边形内）")
for n in ("MI-8","RB-3","MBZ","YAS OUTFALL"):
    print(" ==",n, f"客户 {KMZ[n][1]}")
    ins=[]
    for k,u in L.items():
        i=u.intersection(D[n]).area/1e6; t=u.area/1e6
        if i>0.3: print(f"    {i:7.2f}/{t:7.2f} km² 在内  {k}"); ins.append((k,u,t,i))
    full=[x for x in ins if x[3]/x[2]>0.8]
    best=None
    for r in range(1,min(6,len(full))+1):
        for c in itertools.combinations(full,r):
            a=unary_union([x[1] for x in c]).area/1e6
            if abs(a-KMZ[n][1])/KMZ[n][1]<0.03: print(f"    ★ 组合 {[x[0].split(os.sep)[-2] for x in c]} 并集 {a:.2f} km²")

print("\n---- 补充")
rd10 = kp("Al Wathba catchments.kmz","RD 10"); print("KMZ 'RD 10' (Al Wathba) 面积", round(rd10.area/1e6,2))
for n,u in L.items():
    if "Musaffah 09" in n: M09=u
    if "RB3" in n: RB3=u
    if "Musaffah_RD09" in n: RD09=u
    if "Mussafah_10" in n: M10=u
print("RD-10 ∩ Musaffah09 CAT", round(rd10.intersection(M09).area/1e6,2), " MI-8 KMZ ∩ M09", round(D["MI-8"].intersection(M09).area/1e6,2))
print("M09 CAT 去掉 RD09 后", round(M09.difference(RD09).area/1e6,2), "  RD-10 去掉 M09?", round(rd10.difference(M09).area/1e6,2))
print("RB3 模型 并集", round(RB3.area/1e6,2), " 去掉 RD09", round(RB3.difference(RD09).area/1e6,2), " 去掉 MBZ KMZ", round(RB3.difference(D["MBZ"]).area/1e6,2), " ∩ RB-3 KMZ", round(RB3.intersection(D["RB-3"]).area/1e6,2))
rb4 = kp("Shahamah_Zayed catchments.kmz","RB-4")
print("RB3 模型去掉 RD09 与 MBZ", round(RB3.difference(unary_union([RD09,D["MBZ"]])).area/1e6,2), " KMZ RB-3∪RB-4", round(unary_union([D["RB-3"],rb4]).area/1e6,2))
