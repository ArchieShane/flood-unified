import sys, json, glob, os
sys.path.insert(0,'tools'); sys.path.insert(0,'.')
import geopandas as gpd
from build_catchment_map import kmz_polygons
H = gpd.read_file('console/data/hotspots.geojson').to_crs(4326)
names = ["MI-8","RD-F","RD-C","Qasr Al Bahr-1","RD-G","RD-A","RB-1","RBE1-OF1","RD-I","RB-3","MBZ","YAS OUTFALL","RD-14","RD 14","RD-2"]
allp = {}
for k in glob.glob('data/FW__Hot_Spots_506/*.kmz'):
    try:
        for t in kmz_polygons(os.path.basename(k)):
            allp.setdefault(t[0], []).append((os.path.basename(k), t[1].geom_type, round(t[1].area*1e4,1)))
    except Exception as e: print(k, e)
for n,v in sorted(allp.items()): print(n, v[:3])
