import sys, warnings, itertools
warnings.filterwarnings("ignore")
sys.path.insert(0,'tools'); sys.path.insert(0,'.')
import geopandas as gpd
from shapely.ops import unary_union
from build_catchment_map import kmz_polygons
P = {}
for t in kmz_polygons("Shahamah_Zayed catchments.kmz"):
    if t[1].geom_type=="Polygon": P.setdefault(t[0], []).append(t[1].buffer(0))
G = {k: gpd.GeoSeries([unary_union(v)], crs=4326).to_crs(32640).iloc[0] for k,v in P.items()}
A = lambda g: g.area/1e6
names=["MI-8","MBZ","RB-3","YAS OUTFALL","RB-1","RBE1-OF1","RD-2","RB-4","SEA OUTFALL","SHALIELA","Al Bahya","Al Sader","Ghantout-1"]
print("面积", {n: round(A(G[n]),2) for n in names if n in G})
print("两两交叠 km²")
for a,b in itertools.combinations([n for n in names if n in G],2):
    i=A(G[a].intersection(G[b]))
    if i>0.05: print(f"  {a} ∩ {b} = {i:.2f}")
targets={"MI-8":36.53,"RB-3":15.56,"MBZ":15.26,"YAS OUTFALL":13.02}
print("A 去掉其它多边形后的面积")
others=lambda n:[m for m in G if m!=n]
for n,t in targets.items():
    for sel in (["MBZ","RB-3","RB-1","RBE1-OF1","YAS OUTFALL","RD-2","RB-4"],):
        rest=unary_union([G[m] for m in sel if m!=n])
        print(f"  {n}: 整块 {A(G[n]):.2f}, 去掉同组其它区后 {A(G[n].difference(rest)):.2f}，客户 {t}")
