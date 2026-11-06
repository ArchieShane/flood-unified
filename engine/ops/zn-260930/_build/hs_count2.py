import sys, os, json
sys.path.insert(0,'tools'); sys.path.insert(0,'.')
import geopandas as gpd
from build_catchment_map import kmz_polygons
H = gpd.read_file('console/data/hotspots.geojson').to_crs(4326)
KM = {"MI-8":"Shahamah_Zayed catchments.kmz","RD-F":"island catchment.kmz","RD-C":"island catchment.kmz","Qasr Al Bahr-1":"island catchment.kmz",
 "RD-G":"island catchment.kmz","RD-A":"island catchment.kmz","RB-1":"Shahamah_Zayed catchments.kmz","RBE1-OF1":"Shahamah_Zayed catchments.kmz","RD-I":"island catchment.kmz",
 "RB-3":"Shahamah_Zayed catchments.kmz","MBZ":"Shahamah_Zayed catchments.kmz","YAS OUTFALL":"Shahamah_Zayed catchments.kmz","RD 14":"Al Wathba catchments.kmz"}
out = {}
for n, k in KM.items():
    p = [t[1] for t in kmz_polygons(k) if t[0]==n and t[1].geom_type=="Polygon"]
    g = gpd.GeoSeries([p[0].buffer(0)], crs=4326)
    a = g.to_crs(32640).area.iloc[0]/1e6
    m = H[H.within(g.iloc[0])]
    out[n] = {"km2": round(a,2), "n": len(m), "ids": sorted(m.hotspot_id, key=lambda s:int(s.split('-')[1]) if s.split('-')[1].isdigit() else 0), "crit": m.criticality.value_counts().to_dict()}
    print(n, out[n]["km2"], out[n]["n"], out[n]["crit"], out[n]["ids"][:40])
json.dump(out, open('C:/freedo/zn-260930/_build/hs_by_catchment.json','w',encoding='utf-8'), ensure_ascii=False)
