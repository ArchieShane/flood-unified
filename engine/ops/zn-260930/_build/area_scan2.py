import glob, warnings, json
warnings.filterwarnings("ignore")
import geopandas as gpd
out=[]
seen=set()
for f in sorted(glob.glob('data/**/*.shp', recursive=True)):
    try:
        g = gpd.read_file(f)
    except Exception as e:
        out.append((f,'ERR',str(e)[:60])); continue
    if g.empty or not g.geom_type.isin(["Polygon","MultiPolygon"]).any(): continue
    b = g.total_bounds
    crs = g.crs.to_string() if g.crs else None
    if g.crs is None:
        g = g.set_crs(32640)   # 假定 UTM 40N，下面按坐标范围核对
    else:
        g = g.to_crs(32640)
    a = g.geometry.area.sum()/1e6
    key=(round(a,2),len(g))
    tag = "(同上)" if key in seen else ""
    seen.add(key)
    print(f"{a:9.2f} km² n={len(g):6d} crs={crs} bounds={[round(x) for x in b]} {f[5:]} {tag}")
