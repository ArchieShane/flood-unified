import sys, json
sys.path.insert(0,'tools'); sys.path.insert(0,'.')
import geopandas as gpd
from build_catchment_map import kmz_polygons
H = gpd.read_file('console/data/hotspots.geojson').to_crs(4326)
d = json.load(open('C:/freedo/zn-260930/_build/hs_by_catchment.json', encoding='utf-8'))
for k, n in (("Shahamah_Zayed catchments.kmz","RD-2"),("Al Wathba catchments.kmz","RD 2")):
    for t in kmz_polygons(k):
        if t[0]==n and t[1].geom_type=="Polygon":
            g = gpd.GeoSeries([t[1].buffer(0)], crs=4326); a=g.to_crs(32640).area.iloc[0]/1e6
            m = H[H.within(g.iloc[0])]
            print(k, n, round(a,2), len(m), m.criticality.value_counts().to_dict())
            if n=="RD-2":
                d["RD-2"]={"km2":round(a,2),"n":len(m),"ids":sorted(m.hotspot_id,key=lambda s:int(s.split('-')[1]) if s.split('-')[1].isdigit() else 0),"crit":m.criticality.value_counts().to_dict()}
json.dump(d, open('C:/freedo/zn-260930/_build/hs_by_catchment.json','w',encoding='utf-8'), ensure_ascii=False)
