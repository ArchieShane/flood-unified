import json,glob,re,os,sys
import geopandas as gpd
sys.stdout.reconfigure(encoding='utf-8')
CA='C:/freedo/zn-260930/thinking-drainage-system/cases/ad/'
g=gpd.read_file(CA+'ponds_all.geojson')
# 哪些塘进了哪个区的 L2
used={}
REGS={os.path.basename(f).split('_')[0] for f in glob.glob('C:/freedo/zn-260930/upload/ad_30m/server1/cases/ad/*_ponds.json')}
for f in glob.glob(CA+'*_ponds.json'):
    reg=os.path.basename(f).split('_')[0]
    if reg not in REGS: continue
    d=json.load(open(f,encoding='utf-8'))
    for p in d['ponds']:
        m=re.fullmatch(r'PD(\d+)',p['unitid'])
        key=('seq',int(m.group(1))) if m else ('pid',p['unitid'])
        used.setdefault(key,[]).append((reg,p['vol_model_m3'],p['wiring'].get('auto',False)))
    for s in d['not_modelled']:
        m=re.fullmatch(r'PD(\d+)',s['unitid'])
        key=('seq',int(m.group(1))) if m else ('pid',s['unitid'])
        used.setdefault(key,[]).append((reg,None,'not:'+s['why']))
feats=[]
for _,r in g.iterrows():
    u=used.get(('seq',int(r.seq)),[])+used.get(('pid',r.pid),[]) if r['cat'].startswith('已建成') else used.get(('seq',int(r.seq)),[])
    geom=r.geometry.simplify(0.00001)
    nz=lambda v: None if v is None or v!=v else round(float(v),2)
    props=dict(seq=int(r.seq),pid=r.pid,cat=r['cat'],sources=r.sources,attr=r.attr_src,vol=nz(r.vol_m3),depth=nz(r.depth_m),area=nz(r.area_m2),
      adm=r.adm,runs=[{'reg':a,'vol':b,'note':('自动接管' if c is True else ('全市L2接法' if c is False else c))} for a,b,c in u])
    feats.append({'type':'Feature','properties':props,'geometry':json.loads(gpd.GeoSeries([geom]).to_json())['features'][0]['geometry']})
doms=[]
for f in sorted(glob.glob(CA+'*_domain.geojson')):
    if os.path.basename(f).split('_')[0] not in REGS: continue
    d=json.load(open(f,encoding='utf-8'))
    for ft in d['features']:
        ft['properties']={'reg':os.path.basename(f).split('_')[0],'name':ft['properties'].get('name'),'km2':ft['properties'].get('area_km2')}
        doms.append(ft)
from pyproj import Transformer
T=Transformer.from_crs(32640,4326,always_xy=True)
pumps=[]
for f in sorted(glob.glob('C:/freedo/zn-260930/upload/ad_30m/server1/cases/ad/*_existing_pumps.json')):
    reg=os.path.basename(f).split('_')[0]
    for st in json.load(open(f,encoding='utf-8'))['stations']:
        lon,lat=T.transform(st['x'],st['y'])
        n,q=st.get('n_pumps'),st.get('q_each_lps')
        k='built' if n and q else ('off' if n==0 or '停用' in str(st['q_src']) else 'nodata')
        pumps.append(dict(reg=reg,unitid=st['unitid'],ps=st['ps_id'],status=st['status'],kind=k,n=n,q=q,q_src=st['q_src'],comment=st.get('comment'),
          well_depth=st.get('well_depth_m'),discharge=st.get('discharge_src'),forcemain=st.get('forcemain_m'),lon=lon,lat=lat))
json.dump({'pumps':pumps,'ponds':{'type':'FeatureCollection','features':feats},'domains':{'type':'FeatureCollection','features':doms}},open('data.json','w',encoding='utf-8'),ensure_ascii=False,separators=(',',':'))
print(len(feats),len(doms),os.path.getsize('data.json')//1024,'KB')
