import geopandas as gpd, pandas as pd, glob, os, json, warnings
warnings.filterwarnings('ignore')
os.chdir('C:/freedo/zn-260930/thinking-drainage-system/data')
S=[]   # (优先级, 类别, 来源名, 文件, 默认crs)
def add(pri,cat,src,f,crs=32640): S.append((pri,cat,src,f,crs))
add(1,'已建成塘（L2 用）','261014 ponds_l2','261014/data/ponds_l2.geojson',4326)
add(2,'L1 蓄水设施','261014 reservoirs_l1','261014/data/reservoirs_l1.geojson',4326)
add(3,'Parsons 现状塘','Existing Storage ADM-Line','Parsons_Model Data Export/Existing Storage/Ex Ponds ADM Line SHP/Ex_Ponds_ADM-Line.shp')
add(3,'Parsons 现状塘','Existing Storage RB2','Parsons_Model Data Export/Existing Storage/Ex Ponds RB2/EX_Ponds.shp')
add(3,'Parsons 现状塘','Existing Storage RCN','Parsons_Model Data Export/Existing Storage/RCN Existing Ponds SHP/Ponds.shp')
for f in sorted(x.replace(chr(92),'/') for x in glob.glob('Parsons_Model Data Export/26092026/*/*.shp')):
    if os.path.basename(f).lower().startswith('pond') and 'Table' not in f or 'Pond Table' in f: add(4,'Parsons 子模型塘','Parsons 26092026/'+f.split('/')[2],f)
for f in sorted(x.replace(chr(92),'/') for x in glob.glob('261015/汇水区报告-Parsons/*/*.shp')):
    b=os.path.basename(f).lower()
    if b.startswith('pond'): add(5,'Parsons 子模型塘（261015 版）','261015/'+f.split('/')[2],f)
for f in sorted(x.replace(chr(92),'/') for x in glob.glob('Hydraulic Model Data Export/*/*.shp')):
    if 'pond' in os.path.basename(f).lower(): add(6,'客户模型导出塘','HydExport/'+f.split('/')[1],f)
parts=[]
for pri,cat,src,f,crs in S:
    g=gpd.read_file(f)
    if g.crs is None: g=g.set_crs(crs)
    g=g.to_crs(32640); g=g[~g.geometry.is_empty & g.geometry.notna()].copy()
    g['geometry']=g.geometry.force_2d() if hasattr(g.geometry,'force_2d') else g.geometry
    g=g.explode(index_parts=False) if False else g
    low={c.lower():c for c in g.columns}
    pick=lambda *ks:next((g[low[k]] for k in ks if k in low),None)
    g['pid']=pick('unitid','label','id','name'); g['vol_m3']=pick('volume_m3','mx_storage','max_stor'); g['depth_m']=pick('depth_m','depth_avg')
    for c in ('pid','vol_m3','depth_m'):
        if c not in g: g[c]=None
    g['pri']=pri;g['cat']=cat;g['src']=src;g['file']=f
    parts.append(g[['pri','cat','src','file','pid','vol_m3','depth_m','geometry']])
A=pd.concat(parts,ignore_index=True); A=gpd.GeoDataFrame(A,crs=32640)
A['area']=A.geometry.area
print('原始要素',len(A))
# 去重：优先级从高到低，新的塘若与已保留塘重叠面积 > 30% 较小者，则视为同一个，记录来源
A=A.sort_values(['pri','src']).reset_index(drop=True)
keep=[];srcs={}
sidx=A.sindex
kept_geoms=[]
import shapely
for i,r in A.iterrows():
    g=r.geometry; hit=None
    for j in keep:
        k=A.geometry[j]
        if g.bounds[2]<k.bounds[0] or k.bounds[2]<g.bounds[0] or g.bounds[3]<k.bounds[1] or k.bounds[3]<g.bounds[1]: continue
        try: ia=g.intersection(k).area
        except Exception: ia=0
        if ia>0.3*min(g.area,k.area): hit=j;break
    if hit is None: keep.append(i); srcs[i]={r.src}
    else: srcs[hit].add(r.src)
U=A.loc[keep].copy(); U['sources']=[ '; '.join(sorted(srcs[i])) for i in keep]; U['n_src']=[len(srcs[i]) for i in keep]
U['area_m2']=U.area.round(0); U['pid']=U.pid.astype(str)
print('去重后',len(U)); print(U.groupby('cat').size())
D=U.to_crs(4326)
# ADM 14 区
reg={}
for f in glob.glob('C:/freedo/zn-260930/thinking-drainage-system/cases/ad/*_domain.geojson'):
    k=os.path.basename(f).split('_')[0]; dg=gpd.read_file(f).to_crs(4326).unary_union
    reg[k]=dg
D['adm']=D.geometry.representative_point().apply(lambda p:','.join(k for k,g in reg.items() if g.contains(p)))
print('\nADM 各区落入的塘（去重后，按代表点）')
for k in sorted(reg):
    m=D[D.adm.str.split(',').apply(lambda x:k in x)]
    print(k,len(m),dict(m.cat.value_counts()))
D.to_file(os.environ['TEMP']+'/ponds_all.geojson',driver='GeoJSON')
D.drop(columns='geometry').to_pickle(os.environ['TEMP']+'/ponds_all.pkl')
