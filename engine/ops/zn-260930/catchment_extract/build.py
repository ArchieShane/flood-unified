import sys,json,glob,os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,'C:/freedo/zn-260930/thinking-drainage-system/tools'); sys.path.insert(0,'C:/freedo/zn-260930/thinking-drainage-system')
import geopandas as gpd, pandas as pd
from shapely.ops import unary_union
import ad_regions as A
from build_catchment_map import kmz_polygons
KDIR='C:/freedo/zn-260930/thinking-drainage-system/data/FW__Hot_Spots_506/'
CA='C:/freedo/zn-260930/upload/ad_30m/server1/cases/ad/'
hs=json.load(open(CA+'hotspots_ad.json',encoding='utf-8'))
rows=[];geoms=[]
order=['MI8','RD2','YASO','RB1','RBE1','MBZ','RD14','RB3','RDA','RDF','RDC','QAB1','RDI','RDG','ALR']
for reg in order:
    r=A.REGIONS[reg]; names=r.get('feats') or [r['feat']]
    P=[t for t in kmz_polygons(r['kmz']) if t[0] in names and t[1].geom_type=='Polygon']
    u=gpd.GeoSeries([unary_union([t[1].buffer(0) for t in P])],crs=4326)
    km=u.to_crs(32640).area.iloc[0]/1e6
    pj=json.load(open(CA+f'{reg}_ponds.json',encoding='utf-8')); pm=json.load(open(CA+f'{reg}_existing_pumps.json',encoding='utf-8'))
    nh=sum(1 for h in (hs if isinstance(hs,list) else hs.get('hotspots',[])) if h.get('region')==reg) if isinstance(hs,list) else None
    rows.append({'区域代号':reg,'ADM 序号':r['adm'],'汇水区名称':r['title'],'来源文件（全路径）':(KDIR+r['kmz']).replace('/',chr(92)),
      '文件名':r['kmz'],'文件内图层/多边形名':'；'.join(names),'取用多边形个数':len(P),'KMZ 内图层路径':'；'.join(sorted({str(t[2]) for t in P})),
      '处理方式':'并集（多块 Pond 汇水区）' if len(P)>1 else '直接取用，不外扩',
      '客户地图面积 km²':r['km2'],'KMZ 多边形面积 km²':round(km,2),'面积差 km²':round(km-r['km2'],2),
      '域内塘数（L2）':len(pj['ponds']),'域内现状泵站数':len(pm['stations']),'提取文件':f'catchments_30m.geojson / .shp（字段 reg={reg}）'})
    geoms.append((reg,r,u.iloc[0]))
G=gpd.GeoDataFrame([{'reg':a,'adm':b['adm'],'name':b['title'],'src_kmz':b['kmz'],'src_feat':'; '.join(b.get('feats') or [b['feat']]),'km2_cli':b['km2'],'km2_kmz':round(g.to_crs if False else gpd.GeoSeries([g],crs=4326).to_crs(32640).area.iloc[0]/1e6,2)} for a,b,g in geoms],geometry=[g for *_,g in geoms],crs=4326)
G.to_file('catchments_30m.geojson',driver='GeoJSON'); G.to_file('catchments_30m.shp',encoding='utf-8')
df=pd.DataFrame(rows)
with pd.ExcelWriter('汇水区来源对照_30m.xlsx',engine='openpyxl') as w:
    df.to_excel(w,sheet_name='汇水区来源',index=False)
    s=df.groupby(['文件名','来源文件（全路径）']).agg(汇水区数=('区域代号','count'),区域=('区域代号',lambda x:'、'.join(x))).reset_index()
    s.to_excel(w,sheet_name='按来源文件汇总',index=False)
    for ws in w.book.worksheets:
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width=min(60,max(10,max(len(str(c.value or '')) for c in col)*1.6))
        ws.freeze_panes='A2'
print(df[['区域代号','文件名','文件内图层/多边形名','客户地图面积 km²','KMZ 多边形面积 km²']].to_string()); print(s.to_string())
