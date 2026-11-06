import geopandas as gpd, os
T=os.environ['TEMP']
D=gpd.read_file(T+'/ponds_all.geojson').to_crs(32640).reset_index(drop=True)
g='thinking-drainage-system/data/drainage_network_gdb/DMT_StormWater_AbuDhabi_Processed.gdb'
P=gpd.read_file(g,layer='PIPELINE',columns=['INVERT_LEVEL_UPSTREAM'],bbox=tuple(D.total_bounds))[['geometry']]
P['d']=1
B=D[['geometry']].copy(); B['geometry']=B.buffer(5); B['i']=B.index
sj=gpd.sjoin(B,P,predicate='intersects',how='left')
D['pipes_5m']=sj.groupby('i').d.sum().reindex(D.index).fillna(0).astype(int)
print('5 m 内有管线:',(D.pipes_5m>0).sum(),'/',len(D))
print(D.groupby('cat').apply(lambda x:f"{(x.pipes_5m>0).sum()}/{len(x)}").to_string())
m=D[D.adm.str.len()>0]; print('ADM 14区内:',(m.pipes_5m>0).sum(),'/',len(m))
D.drop(columns='geometry').to_pickle(T+'/ponds_pipes.pkl')
