import sys, glob, warnings, json, os
warnings.filterwarnings("ignore")
import geopandas as gpd
from shapely.ops import unary_union
H = gpd.read_file('console/data/hotspots.geojson').to_crs(32640)
d = json.load(open('C:/freedo/zn-260930/_build/hs_by_catchment.json', encoding='utf-8'))
os.makedirs('C:/freedo/zn-260930/_build/domains', exist_ok=True)
for key, f in (("MBZ","data/OneDrive_1_9-26-2026/Musaffah_RD09/Catchments.shp"),("YAS OUTFALL","data/Parsons_Model Data Export/26092026/Airport 01_02/catchment.shp")):
    g = gpd.read_file(f).set_crs(32640)
    u = unary_union(g.geometry.buffer(0).values)
    c = u.buffer(20).buffer(-20)          # 闭运算补子汇水区间缝隙
    polys = [p for p in (c.geoms if c.geom_type=="MultiPolygon" else [c])]
    big = max(polys, key=lambda p: p.area)
    m = H[H.within(c)]
    print(key, "并集", round(u.area/1e6,2), "闭运算后", round(c.area/1e6,2), "块数", len(polys), "积水点", len(m), m.criticality.value_counts().to_dict())
    d[key+"_model"] = {"km2": round(c.area/1e6,2), "n": len(m), "ids": sorted(m.hotspot_id, key=lambda s:int(s.split('-')[1]) if s.split('-')[1].isdigit() else 0), "crit": m.criticality.value_counts().to_dict(), "src": f}
    gpd.GeoDataFrame([{"name":key,"src":f}], geometry=[c], crs=32640).to_crs(4326).to_file(f"C:/freedo/zn-260930/_build/domains/{key.replace(' ','_')}_model.geojson", driver="GeoJSON")
json.dump(d, open('C:/freedo/zn-260930/_build/hs_by_catchment.json','w',encoding='utf-8'), ensure_ascii=False)
