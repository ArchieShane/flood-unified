import sys, glob, os, json, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0,'tools'); sys.path.insert(0,'.')
import geopandas as gpd
from build_catchment_map import kmz_polygons
T = {"MI-8":36.53,"RB-3":15.56,"MBZ":15.26,"YAS OUTFALL":13.02,"RB-1":18.40,"RBE1-OF1":22.06,"RD-2":126.99,"RD-14":15.44,"RD-A":3.21,"RD-F":5.44,"RD-C":1.94,"Qasr":1.52,"RD-I":5.79,"RD-G":4.28}
res=[]
for f in glob.glob('data/**/*.shp', recursive=True):
    try:
        g = gpd.read_file(f)
        if g.empty or not g.geom_type.isin(["Polygon","MultiPolygon"]).all(): continue
        crs = g.crs
        if crs is None: continue
        g = g.to_crs(32640)
        a = g.geometry.area.sum()/1e6
        res.append((f.replace('data/',''), len(g), round(a,2)))
        # per-feature areas by attribute columns
    except Exception as e: pass
for n,c,a in sorted(res): print(f"{a:9.2f} km² {c:5d}  {n}")
json.dump(res, open('C:/freedo/zn-260930/_build/shp_areas.json','w'))
print("---- matches within 3% / dissolved")
for k,t in T.items():
    hit=[(n,a) for n,c,a in res if abs(a-t)/t<0.03]
    print(k,t,hit)
