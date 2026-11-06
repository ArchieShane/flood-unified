from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
wb=Workbook()
H=PatternFill('solid',fgColor='1F4E78'); HF=Font(bold=True,color='FFFFFF')
Y=PatternFill('solid',fgColor='FFF2CC'); B=Side(style='thin',color='999999'); bd=Border(left=B,right=B,top=B,bottom=B)
def hdr(ws,row,vals):
    for i,v in enumerate(vals,1):
        c=ws.cell(row,i,v); c.fill=H; c.font=HF; c.alignment=Alignment(horizontal='center',vertical='center',wrap_text=True); c.border=bd
# 参数
p=wb.active; p.title='参数'
rows=[('C1 实测:情形数',4,'个','C1 区域已跑情形数'),
('C1 实测:占用磁盘',1.2,'TB','"1.+T",请按实际填(如1.3)'),
('单场体量(C1基准)','=B3/B2','TB/场','50cm DTM、5m格网'),
('每区情形数',9,'个',''),
('每情形方案数(L2、L2planned)',2,'个',''),
('安全冗余系数',1.2,'倍','预留重算/试算/中间文件'),
('备份副本数',1,'份','如需NAS内再留一份备份则填2')]
hdr(p,1,['参数','数值','单位','说明'])
for i,r in enumerate(rows,2):
    for j,v in enumerate(r,1):
        c=p.cell(i,j,v); c.border=bd
    p.cell(i,2).fill=Y
p.cell(4,2).fill=PatternFill(); 
p['A11']='黄色格为可调输入。各区"面积折算系数"在明细表中填(=该区面积/C1面积,默认1即按C1同等体量)。'
for col,w in zip('ABCD',(30,12,10,40)): p.column_dimensions[col].width=w
def detail(name,n,prefix):
    ws=wb.create_sheet(name)
    hdr(ws,1,['序号','区域','面积折算系数\n(相对C1)','情形数','方案数','场次','单场体量(TB)','小计(TB)'])
    for i in range(n+1):
        r=i+2
        city=(i==n)
        ws.cell(r,1,i+1)
        ws.cell(r,2,f'{prefix}全市' if city else f'{prefix}汇水区{i+1:02d}')
        ws.cell(r,3,1).fill=Y
        ws.cell(r,4,'=参数!$B$5'); ws.cell(r,5,'=参数!$B$6')
        ws.cell(r,6,f'=D{r}*E{r}')
        ws.cell(r,7,f'=参数!$B$4*C{r}'); ws.cell(r,8,f'=F{r}*G{r}')
        for c in range(1,9): ws.cell(r,c).border=bd
        for c in (7,8): ws.cell(r,c).number_format='0.00'
    t=n+3
    ws.cell(t,2,'合计').font=Font(bold=True)
    ws.cell(t,6,f'=SUM(F2:F{n+2})'); ws.cell(t,8,f'=SUM(H2:H{n+2})'); ws.cell(t,8).number_format='0.0'
    for c,w in zip('ABCDEFGH',(6,18,14,8,8,8,14,12)): ws.column_dimensions[c].width=w
    ws.row_dimensions[1].height=32; ws.freeze_panes='A2'
    ws.cell(n+2,3).comment=None
    return t
ta=detail('阿布扎比明细',50,'ADM ')
tb=detail('艾因明细',13,'AA ')
s=wb.create_sheet('汇总',0)
hdr(s,1,['城市','汇水区数','全市','总场次','基础体量(TB)','含冗余(TB)','含备份(TB)'])
for r,(nm,sh,t,k) in enumerate([('阿布扎比','阿布扎比明细',ta,50),('艾因','艾因明细',tb,13)],2):
    s.cell(r,1,nm); s.cell(r,2,k); s.cell(r,3,1)
    s.cell(r,4,f"='{sh}'!F{t}"); s.cell(r,5,f"='{sh}'!H{t}")
    s.cell(r,6,f'=E{r}*参数!$B$7'); s.cell(r,7,f'=F{r}*参数!$B$8')
s.cell(4,1,'合计')
for c in 'BCDEFG': s[f'{c}4']=f'=SUM({c}2:{c}3)'
for r in range(1,5):
    for c in range(1,8):
        s.cell(r,c).border=bd
        if r>1 and c>=5: s.cell(r,c).number_format='0.0'
for c in range(1,8): s.cell(4,c).font=Font(bold=True)
s['A6']='申请扩容容量(TB,向上取整,建议再加NAS现有使用量)'; s['A6'].font=Font(bold=True)
s['G6']='=ROUNDUP(G4,0)'; s['G6'].font=Font(bold=True,color='C00000')
s['A8']='估算依据:C1(50cm DTM,5m格网)4个情形占用约1.2TB,折合单场约0.3TB;各区按"面积折算系数"线性折算。'
s['A9']='注:全市行默认按C1同等体量,若全市格网较粗(如250m),请在明细表该行下调折算系数。'
for c,w in zip('ABCDEFG',(14,10,8,10,14,14,14)): s.column_dimensions[c].width=w
wb.save('NAS扩容申请测算表.xlsx')
