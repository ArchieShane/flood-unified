# v7 -> v8：把 OLD 一行拆成 OLD-1…OLD-6 六行（二维计算域必须是单个多边形），序号重排，汇总页按 49 个任务重算
import openpyxl, copy
from openpyxl.styles import PatternFill, Border, Side
R='C:/freedo/zn-260930/'
wb=openpyxl.load_workbook(R+'ADM剩余汇水区_61mm1h_计划_2026-10-19_v7.xlsx')
ws=wb['计划']
rows=[[c.value for c in r] for r in ws.iter_rows(min_row=2)]
tsv=[l.rstrip('\n').split('\t') for l in open(R+'upload/adm_rest_src/cases/ad/rest_all.tsv',encoding='utf-8')]
old=[t for t in tsv if t[1].startswith('OLD')]
out=[]
for r in rows:
    if r[4]=='OLD':
        for t in old:
            out.append([r[0],r[1],None,r[3],t[2],float(t[4]),f'{t[3]} m',r[7],r[8],r[9],int(t[5]),r[11],'OLD 的 6 块互不相连多边形之一，单独建域'])
    else: out.append(r)
for i,r in enumerate(out,1): r[2]=i
Y=PatternFill('solid',fgColor='FFFFF2CC')
thin=Side(style='thin',color='FFBFBFBF');BD=Border(left=thin,right=thin,top=thin,bottom=thin)
ws.delete_rows(2,ws.max_row)
for r in out:
    ws.append(r)
    for c in ws[ws.max_row]: c.border=BD
    ws.cell(ws.max_row,1).number_format=ws.cell(ws.max_row,2).number_format='yyyy-mm-dd'
    if r[6]=='5 m': ws.cell(ws.max_row,7).fill=Y
s=wb['汇总']
for i,srv in enumerate(('服务器1','服务器2','服务器3'),1):
    sel=[r for r in out if r[9]==srv]
    s.cell(i,3).value=len(sel)
    s.cell(i,5).value=round(sum(r[5] for r in sel if r[6]=='30 m'),1)
    s.cell(i,7).value=sum(1 for r in sel if r[6]=='5 m')
s.cell(4,2).value=str(s.cell(4,2).value)+'。OLD 拆成 OLD-1…OLD-6 六个任务，共 49 个任务'
wb.save(R+'ADM剩余汇水区_61mm1h_计划_2026-10-19_v8.xlsx')
print(len(out),[[c.value for c in r] for r in s.iter_rows()][:3])
