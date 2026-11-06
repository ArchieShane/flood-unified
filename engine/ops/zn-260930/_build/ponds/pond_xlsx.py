import pandas as pd, geopandas as gpd, os
T=os.environ['TEMP']
D=gpd.read_file(T+'/ponds_all.geojson'); K=pd.read_pickle(T+'/ponds_pipes.pkl')
D['lon']=D.geometry.representative_point().x.round(6); D['lat']=D.geometry.representative_point().y.round(6)
D=D.drop(columns='geometry'); D['pipes_5m']=K['pipes_5m'].values
D['状态']='L2（已建成；数据里没有明确区分 planned，按已建成算）'
D['能否接入现有管网']=D.pipes_5m.apply(lambda n:'可（5 m 内有管线）' if n>0 else '否（5 m 内无客户管线）')
ADM='MI8 RD2 YASO RB1 RBE1 MBZ RD14 RB3 RDA RDF RDC QAB1 RDI RDG'.split()
D.insert(0,'序号',range(1,len(D)+1))
out=D.rename(columns={'pid':'编号/名称','cat':'类别','src':'主来源','sources':'所有来源（重叠合并）','n_src':'来源数','area_m2':'面积m²','vol_m3':'库容m³（来源给的）','depth_m':'深度m（来源给的）','adm':'所在ADM区','pipes_5m':'5m内管线数','file':'文件'})
out=out[['序号','编号/名称','类别','状态','所在ADM区','能否接入现有管网','5m内管线数','面积m²','库容m³（来源给的）','深度m（来源给的）','lon','lat','所有来源（重叠合并）','文件']]
rows=[]
for k in ADM:
    m=out[out['所在ADM区'].str.split(',').apply(lambda x:k in x)]
    rows.append({'ADM区':k,'塘总数':len(m),'可接入管网':int((m['5m内管线数']>0).sum()),'当前模型已建':{'RD2':1,'RD14':1}.get(k,0)})
adm=pd.DataFrame(rows)
summ=out.groupby('类别').agg(去重后个数=('序号','size'),可接入管网=('5m内管线数',lambda s:int((s>0).sum()))).reset_index()
with pd.ExcelWriter('蓄水池清单_2026-10-16.xlsx') as w:
    out.to_excel(w,sheet_name='合并清单',index=False); summ.to_excel(w,sheet_name='汇总',index=False); adm.to_excel(w,sheet_name='ADM各区',index=False)
print(summ.to_string()); print(adm.to_string())
