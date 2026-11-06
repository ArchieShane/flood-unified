import json, openpyxl, datetime as dt
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
R='C:/freedo/zn-260930/'
wb0=openpyxl.load_workbook(R+'ADM汇水区推演清单_2026-10-17_v2.xlsx',data_only=True)
rows=[r for r in wb0['库图层57条'].iter_rows(min_row=2,values_only=True) if r[4]=='未推演']
ov={}
for r in wb0['汇水区清单'].iter_rows(min_row=2,values_only=True):
    if r[2] and r[1]=='未推演': ov.setdefault((r[5],r[6]),r[8])
items=[]
for i,(lid,lab,km,src,st,hs) in enumerate(rows):
    g='5 m' if km<1 else '30 m'
    w=km*(36 if km<1 else 1)
    items.append(dict(lid=lid,lab=lab,km=km,hs=hs,grid=g,w=w,ov=ov.get((lab,km)),src=src))
items.sort(key=lambda x:-x['w'])
load=[0,0,0]
for it in items:
    CAP=[1.5,1,1]
    k=min(range(3),key=lambda j:load[j]/CAP[j]); it['srv']=k+1; load[k]+=it['w']
print(load, len(items), sum(1 for i in items if i['grid']=='5 m'), sum(i['km'] for i in items if i['grid']=='30 m'), sum(i['hs'] for i in items))
G=PatternFill('solid',fgColor='FF1F7A4D');Y=PatternFill('solid',fgColor='FFFFF2CC')
thin=Side(style='thin',color='FFBFBFBF');BD=Border(left=thin,right=thin,top=thin,bottom=thin)
wb=openpyxl.Workbook();ws=wb.active;ws.title='计划'
RATE=30.0  # 折算 30m km²/小时(L1+L2)，估计值
T0=dt.datetime(2026,10,20)
H=['开始时间','预计结束','序号','库 id','汇水区','面积 km²','格网','降雨','方案','服务器','ADM积水点数','与已推演区重叠','备注']
ws.append(H)
for c in ws[1]: c.font=Font(b=True,color='FFFFFFFF');c.fill=G;c.alignment=Alignment(horizontal='center',wrap_text=True);c.border=BD
cur={1:T0,2:T0,3:T0}
import math
tot={k:sum(max(i['w'],0.5) for i in items if i['srv']==k) for k in (1,2,3)}
cum={1:0,2:0,3:0}
for n,it in enumerate(sorted(items,key=lambda x:(x['srv'],-x['w'])),1):
    k=it['srv'];w=max(it['w'],0.5);c0=cum[k];cum[k]+=w
    d0=min(3,int(c0/tot[k]*4));d1=min(3,max(d0,math.ceil(cum[k]/tot[k]*4)-1))
    st=T0+dt.timedelta(days=d0);en=T0+dt.timedelta(days=d1);cur[k]=en
    note=[]
    if it['grid']=='5 m': note.append('<1 km²，5 m 精度')
    if it['ov'] and it['ov']>=0.1: note.append(f"与已推演区重叠 {it['ov']:.0%}，边缘水深以已推演区为准")
    if it['hs']==0: note.append('无 ADM 积水点')
    if it['lab']=='Mussafah_00': note.append('15 m 做过，此为 30 m')
    ws.append([st,en,n,it['lid'],it['lab'],it['km'],it['grid'],'61 mm / 1 h','L1+L2',f"服务器{it['srv']}",it['hs'],it['ov'],'；'.join(note)])
    ws.cell(ws.max_row,7).fill=Y if it['grid']=='5 m' else PatternFill()
for row in ws.iter_rows(min_row=2):
    for c in row: c.border=BD
    row[0].number_format=row[1].number_format='yyyy-mm-dd'
for col,w in zip('ABCDEFGHIJKLM',[16,16,6,7,26,10,8,12,8,10,12,14,44]): ws.column_dimensions[col].width=w
s=wb.create_sheet('汇总')
for srv in (1,2,3):
    sel=[i for i in items if i['srv']==srv]
    s.append([f'服务器{srv}',cur[srv].strftime('结束 %m-%d')]+[len(sel),'个',round(sum(i['km'] for i in sel if i['grid']=='30 m'),1),'km²(30m)',sum(1 for i in sel if i['grid']=='5 m'),'个 5 m'])
s.append(['说明','起点按 10-20（runner/包写好并上传后）；按 4 天(10-20~10-23)排，服务器1 为 48 核、按 1.5 倍算力分配，各台按计算量(5 m 折 36 倍面积)顺序分配到各天，4 天含冗余时间，实测后修正'])
c=wb.create_sheet('服务器配置')
c.append(['服务器','主机名','CPU 核数','内存','算力权重','说明'])
for r in [['服务器1','AZPDW006aim01','48','188 GB',1.5,'更强，分配最多'],
          ['服务器2','AZPDW006uca03','32','64 GB',1.0,'内存 64 GB，同时在算的态数受限'],
          ['服务器3','AZPDW006uca06','32','64 GB',1.0,'同服务器2 配置']]: c.append(r)
c.append([]);c.append(['排期','10-20 开始、10-23 结束共 4 天；按估计只需约 1–2 天，多出来的时间是冗余（应对实测变慢、重跑、5 m 耗时不确定）'])
for cell in c[1]: cell.font=Font(b=True,color='FFFFFFFF');cell.fill=G
for col,w in zip('ABCDEF',[10,18,10,10,10,40]): c.column_dimensions[col].width=w
json.dump([dict(lid=i['lid'],label=i['lab'],km2=i['km'],res=5 if i['grid']=='5 m' else 30,server=i['srv'],hs=i['hs'],ov=i['ov'],
                start=None) for i in sorted(items,key=lambda x:(x['srv'],-x['w']))],open(R+'_build/rest_plan_items.json','w',encoding='utf-8'),ensure_ascii=False,indent=0)
wb.save(R+'ADM剩余汇水区_61mm1h_计划_2026-10-19_v7.xlsx')
for srv in (1,2,3): print(srv,[ (i['lab'],i['km'],i['grid']) for i in items if i['srv']==srv])
