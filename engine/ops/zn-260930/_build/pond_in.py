import sys, json, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0,'tools'); sys.path.insert(0,'.')
import geopandas as gpd
import ad_regions as A
ps = gpd.read_file('../upload/city_250m/data/ponds_l2.geojson').to_crs(32640)
print("塘", len(ps))
for reg in A.REGIONS:
    D = A.domain_utm(reg)
    hit = ps[ps.intersects(D)]
    print(reg, len(hit), list(hit.unitid))
