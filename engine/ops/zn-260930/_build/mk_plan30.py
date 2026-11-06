import datetime as dt, json, openpyxl
HS = json.load(open('C:/freedo/zn-260930/_build/hs_by_catchment.json', encoding='utf-8'))
HSKEY = {'B7':'MI-8','A2':'RD-F','A3':'RD-C','A4':'Qasr Al Bahr-1','A6':'RD-G','A1':'RD-A','B10':'RB-1','B12':'RBE1-OF1','A5':'RD-I','B8':'RB-3','B9':'MBZ','B11':'YAS OUTFALL','B15':'RD 14','B14':'RD-2'}
_h=json.load(open('C:/freedo/zn-260930/thinking-drainage-system/cases/ad/hotspots_ad.json',encoding='utf-8'))['hotspots']
HS['Pond 1/2A/2B/3/4/A/B/c/D']={'n':sum(1 for h in _h if h['region']=='ALR'),'ids':[h['hotspot_id'] for h in _h if h['region']=='ALR']}
HSKEY['B16']='Pond 1/2A/2B/3/4/A/B/c/D'
HSN = lambda a: HS[HSKEY[a]]['n'] if a in HSKEY else None

from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
D = lambda m, d: dt.datetime(2026, m, d)
G = PatternFill("solid", fgColor="FF1F7A4D"); L = PatternFill("solid", fgColor="FFEAF4EE")
Y = PatternFill("solid", fgColor="FFFFF2CC"); R = PatternFill("solid", fgColor="FFFDE2E2")
thin = Side(style="thin", color="FFBFBFBF"); BD = Border(left=thin, right=thin, top=thin, bottom=thin)
HDR = ['序号','区域 (Compute domain)','ADM 序号','汇水区','面积 (km2)','catchment 边界','降雨情景','格网','方案','计算节点','服务器 IP','启动日期','预计算完','区域截止','状态','实际完成','备注','积水点数']
RAIN = "61 mm / 1 h"
# (ADM序号, 区域, 面积, 边界, 服务器, 启, 完, 备注)
AREAS = [
 # 服务器1：MI-8 单独一块最大的
 ("B7","MI-8 (RD-10) – Mussafah",250.62,"是","服务器1",D(10,16),D(10,18),"面积以客户地图 250.62 km² 为准（客户表 36.53 km² 写错），与 KMZ 多边形 250.78 km² 一致；本批最大一块"),
 # 服务器2
 ("B14","RD-2 (PS-4 / RD6, 7 & 8)",126.99,"是","服务器2",D(10,16),D(10,18),"客户 10-15 更新：边界由「推断」改为「是」、加入 30 m 推演；KMZ 多边形 127.07 km²，与表一致；PS-4、LS-01/02/03 只有点没有汇水区面"),
 ("B11","YAS OUTFALL – AUH Airport",25.86,"是","服务器2",D(10,16),D(10,17),"面积以客户地图 25.86 km² 为准（客户表 13.02 km² 写错），与 KMZ 多边形 25.88 km² 一致；与 RB-1、RBE1-OF1 相邻，未发现重叠"),
 ("B10","RB-1",18.42,"是","服务器2",D(10,16),D(10,17),"KMZ 边界、管网 158 km、热点 8 个；客户模型 RB-01"),
 ("B16","Al Riyadh City – all ponds",76.15,"是","服务器2",D(10,17),D(10,19),"用户 10-17 要求在服务器2 上跑；边界 = Al Wathba catchments.kmz 里 9 块 Pond 汇水区（Pond 1/2A/2B/3/4/A/B/c/D，合计 76.19 km²，彼此有少量重叠，并集 75.42 km²，客户 76.15）；域内 13 座全市 L2 已建成塘；包 upload/ad_30m/server2_alr.zip"),
 # 服务器3
 ("B12","RBE1-OF1 (Yas2 – Al Falah)",22.06,"是","服务器3",D(10,16),D(10,17),"计算域 = ADM 汇水区 RBE1-OF1 整块，立交等热点只是其中的点位"),
 ("B9","MBZ (RD-9) – Mussafah / MBZ",18.07,"是","服务器3",D(10,16),D(10,17),"面积以客户地图 18.07 km² 为准（客户表 15.26 km² 写错），与 KMZ 多边形 18.08 km² 一致；客户 10-15 把边界由「推断」改为「是」"),
 ("B15","RD-14",15.44,"是","服务器3",D(10,16),D(10,17),"计算域 = ADM 汇水区 RD-14；客户 10-15 把「子区」改为「是」"),
 ("B8","RB-3",7.99,"是","服务器3",D(10,16),D(10,16),"面积以客户地图 7.99 km² 为准（客户表 15.56 km² 写错），与 KMZ 多边形 8.00 km² 一致"),
 ("A1","RD-A",3.21,"是","服务器3",D(10,16),D(10,16),"RD-A 是单独的计算区域（与 Al Bateen 无关）；KMZ 3.21 km² 与客户一致；泵站 ADM_RD-A 无流量数据，不建泵"),
 ("A2","RD-F",5.44,"是","服务器3",D(10,16),D(10,16),"KMZ 边界、管网 116 km、热点 14 个"),
 ("A3","RD-C",1.94,"是","服务器3",D(10,16),D(10,16),"KMZ 边界、管网 51 km、热点 4 个"),
 ("A4","Qasr Al Bahr-1",1.52,"是","服务器3",D(10,16),D(10,16),"GDB 里无泵站，热点 1 个"),
 ("A5","RD-I",5.79,"是","服务器3",D(10,16),D(10,16),"KMZ 边界、管网 138 km、热点 14 个"),
 ("A6","RD-G",4.28,"是","服务器3",D(10,16),D(10,16),"KMZ 边界、管网 116 km、热点 18 个"),
]
HOLD = [  # 暂不排：缺边界 / 缺数据
 ("B13","Bani Yas leak","—","暂未找到","“leak”未定义、边界暂未找到；用户表里无模拟场景，先不排"),
 ("B14","RD-2 (PS-4 / RD6, 7 & 8)",126.99,"推断","用户表里无模拟场景；多边形 127 km² 偏大、边界待确认，先不排"),
 ("B16","Al Riyadh City – all ponds",76.15,"否","无边界、无塘位置和库容，需向 ADM 要，先不排"),
 ("AAM","AAM","—","—","用户表里只写 L1、无面积，口径不明，先不排"),
]
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "30m格网"
ws.append(HDR)
for c in ws[1]:
    c.font = Font(b=True, color="FFFFFFFF"); c.fill = G; c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True); c.border = BD
n = 0
for a in AREAS:
    for plan in ("L1", "L2"):
        n += 1; r = n + 1
        ws.append([n, a[1], a[0], a[1], a[2], a[3], RAIN, "30 m", plan, a[4],
                   f'=IF(VLOOKUP(J{r},机器配置!$A:$B,2,FALSE)="","待填",VLOOKUP(J{r},机器配置!$A:$B,2,FALSE))',
                   a[5], a[6], None, ("未开始" if a[0]=="B16" else "已完成"), None, a[7] if plan == "L1" else None, HSN(a[0])])
last = ws.max_row
for row in ws.iter_rows(min_row=2, max_row=last):
    hold = row[14].value in ("待确认", "不做")
    for c in row:
        c.border = BD; c.fill = R if hold else L
        c.alignment = Alignment(horizontal="left" if c.column in (2, 4, 17) else "center", vertical="center", wrap_text=c.column == 17)
        if c.column in (12, 13, 14, 16): c.number_format = "yyyy-mm-dd"
for k, w in dict(A=6,B=30,C=9,D=30,E=10,F=12,G=13,H=7,I=7,J=11,K=16,L=12,M=12,N=12,O=10,P=12,Q=60,R=9).items(): ws.column_dimensions[k].width = w
ws.freeze_panes = "C2"; ws.auto_filter.ref = f"A1:R{last}"
dv = DataValidation(type="list", formula1="机器配置!$A$2:$A$4", allow_blank=True); dv.add(f"J2:J{last}"); ws.add_data_validation(dv)
dv2 = DataValidation(type="list", formula1='"未开始,进行中,已完成,需重跑,待确认,不做"', allow_blank=True); dv2.add(f"O2:O{last}"); ws.add_data_validation(dv2)

# ---- 推演2h96mm：同 15 个汇水区 × L1/L2，96 mm / 2 h，30 m；服务器分工同 61 mm / 1 h，启动日期待定
ws2 = wb.create_sheet("推演2h96mm", 1)
ws2.append(HDR)
for c in ws2[1]:
    c.font = Font(b=True, color="FFFFFFFF"); c.fill = G; c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True); c.border = BD
n2 = 0
for a in AREAS:
    for plan in ("L1", "L2"):
        n2 += 1; r = n2 + 1
        ws2.append([n2, a[1], a[0], a[1], a[2], a[3], "96 mm / 2 h", "30 m", plan, a[4],
                    f'=IF(VLOOKUP(J{r},机器配置!$A:$B,2,FALSE)="","待填",VLOOKUP(J{r},机器配置!$A:$B,2,FALSE))',
                    None, None, None, "未开始", None, ("域、塘、泵站同 61 mm / 1 h；只换雨量与历时（模拟时长 = 历时 + 4 h = 6 h）" if plan == "L1" else None), HSN(a[0])])
last2 = ws2.max_row
for row in ws2.iter_rows(min_row=2, max_row=last2):
    for c in row:
        c.border = BD; c.fill = L
        c.alignment = Alignment(horizontal="left" if c.column in (2, 4, 17) else "center", vertical="center", wrap_text=c.column == 17)
        if c.column in (12, 13, 14, 16): c.number_format = "yyyy-mm-dd"
for k, w in dict(A=6,B=30,C=9,D=30,E=10,F=12,G=13,H=7,I=7,J=11,K=16,L=12,M=12,N=12,O=10,P=12,Q=60,R=9).items(): ws2.column_dimensions[k].width = w
ws2.freeze_panes = "C2"; ws2.auto_filter.ref = f"A1:R{last2}"
dv3 = DataValidation(type="list", formula1="机器配置!$A$2:$A$4", allow_blank=True); dv3.add(f"J2:J{last2}"); ws2.add_data_validation(dv3)
dv4 = DataValidation(type="list", formula1='"未开始,进行中,已完成,需重跑,待确认,不做"', allow_blank=True); dv4.add(f"O2:O{last2}"); ws2.add_data_validation(dv4)

# 进度汇总（按汇水区）
sm = wb.create_sheet("进度汇总")
sm.append(['ADM 序号','汇水区','面积 (km2)','格网','总推演数','已完成','进行中','需重跑','完成率','计算节点','启动','预计算完','积水点数'])
for c in sm[1]: c.font = Font(b=True, color="FFFFFFFF"); c.fill = G; c.alignment = Alignment(horizontal="center"); c.border = BD
for i, a in enumerate(AREAS, start=2):
    f = "'30m格网'!$D$2:$D$%d" % last
    sm.append([a[0], a[1], a[2], "30 m",
      f'=COUNTIFS({f},B{i})', f'=COUNTIFS({f},B{i},\'30m格网\'!$O$2:$O${last},"已完成")',
      f'=COUNTIFS({f},B{i},\'30m格网\'!$O$2:$O${last},"进行中")', f'=COUNTIFS({f},B{i},\'30m格网\'!$O$2:$O${last},"需重跑")',
      f'=IFERROR(F{i}/E{i},0)', a[4], a[5], a[6], HSN(a[0])])
t = len(AREAS) + 2
sm.append(["合计", None, f"=SUM(C2:C{t-1})", None] + [f"=SUM({c}2:{c}{t-1})" for c in "EFGH"] + [f"=IFERROR(F{t}/E{t},0)", None, None, None, f"=SUM(M2:M{t-1})"])
for row in sm.iter_rows(min_row=2):
    for c in row:
        c.border = BD; c.alignment = Alignment(horizontal="center")
        if c.column in (11, 12): c.number_format = "yyyy-mm-dd"
        if c.column == 9: c.number_format = "0%"
for k, w in dict(A=9,B=30,C=11,D=7,E=10,F=8,G=8,H=8,I=8,J=11,K=12,L=12).items(): sm.column_dimensions[k].width = w


# 积水点清单
import geopandas as gpd
Hg = gpd.read_file("C:/freedo/zn-260930/thinking-drainage-system/console/data/hotspots.geojson").set_index("hotspot_id")
hl = wb.create_sheet("积水点清单")
hl.append(['汇水区','积水点编号','位置 (location)','所属片区 (area)','重要性','有管网数据','成因','规划设计事件'])
for c in hl[1]: c.font = Font(b=True, color="FFFFFFFF"); c.fill = G; c.alignment = Alignment(horizontal="center"); c.border = BD
for a in AREAS:
    for hid in HS.get(HSKEY[a[0]], {}).get("ids", []):
        r = Hg.loc[hid]
        hl.append([a[1], hid, r["location"], r["area"], r["criticality"], "是" if r["network_available"] else "否", r["root_cause_zh"], r["design_event_future"]])
for row in hl.iter_rows(min_row=2):
    for c in row: c.border = BD; c.fill = L; c.alignment = Alignment(vertical="center")
for k, w in dict(A=22,B=12,C=38,D=20,E=12,F=11,G=22,H=34).items(): hl.column_dimensions[k].width = w
hl.freeze_panes = "A2"; hl.auto_filter.ref = f"A1:H{hl.max_row}"

# 成果输出
ot = wb.create_sheet("成果输出")
for line in [
 ["每个汇水区、每个态（L1 / L2）交付的内容"],
 ["积水点 = ADM 热点（项目 console/data/hotspots.geojson）里落在该汇水区多边形内的点，清单见“积水点清单”页（14 个汇水区共 %d 个）。" % sum(HSN(a[0]) or 0 for a in AREAS)],
 [],
 ["成果","内容"],
 ["积水点结果表（xlsx + csv）","每个积水点 × 每个态一行：离该点 200 m 内的最大水深、≥0.10 m / ≥0.30 m 积水面积、最大积水量及出现时刻、是否积水（最大水深 ≥ 0.30 m）；附重要性、位置、成因，L1 → L2 的水深变化"],
 ["积水点图层（geojson / shp）","积水点位置 + 上表指标作为属性，GIS 里可直接叠图；另附最深积水点及其离最近积水点的距离"],
 ["汇水区汇总","每个汇水区：积水点总数、积水点数（L1 / L2）、全域最大水深、积水面积、最大积水量、一维溢流"],
 ["二维结果","最大水深栅格（30 m）、水深时程、L1 与 L2 对比图；计算域、现状泵站、塘位置"],
 ["说明文件","00_说明.md（口径、参数）、计算设置.md；改格网或雨量重跑时产物目录带格网后缀，不覆盖旧结果"],
 [],
 ["做法：沿用 rg_post.py 的积水点统计（HS_R = 200 m），新增上面的积水点结果表和图层输出；30 m 格网单元较粗，200 m 内只有几个单元，结果表里同时给出该点最近单元的水深。"],
]: ot.append(line)
ot["A1"].font = Font(b=True, size=12)
for c in ot[4]: c.font = Font(b=True, color="FFFFFFFF"); c.fill = G
ot.column_dimensions["A"].width = 32; ot.column_dimensions["B"].width = 120
for row in ot.iter_rows(min_row=5, max_row=9):
    for c in row: c.alignment = Alignment(wrap_text=True, vertical="top"); c.border = BD

cg = wb.create_sheet("客户更新对照", 0)
cg.append(["客户 10-15 第二版表 vs 第一版：改了什么、我们怎么处理"])
cg.append([])
cg.append(["项目","第一版","第二版","对计划表的影响 / 处理"])
rows = [
 ["RD-2 (PS-4 / RD6, 7 & 8)","边界「推断」、无模拟场景、10-17 机器2","边界「是」、1h 61mm L2、30 m、10-17 机器2","新增进计划：L1+L2 共 2 态，KMZ 多边形 127.07 km²，内有 86 个积水点；最大一块，放服务器1"],
 ["MBZ (RD-9)","边界「推断」、面积 15.26","边界「是」、面积 15.26","边界列改为「是」；客户地图上是 18.07 km²，与 KMZ 一致，表里 15.26 写错，用 KMZ"],
 ["RD-14","边界「子区」","边界「是」","边界列改为「是」，其余不变（客户表里仍是 15 m / 9 场景一行，我们 30 m 是另加的）"],
 ["Al Riyadh City – all ponds","边界「否」","边界「是」、1h 61mm L2、30 m","用户 10-17 改：做。边界 = Al Wathba KMZ 里 9 块 Pond 并集；加进计划，服务器2，L1+L2 共 2 态"],
 ["AAM","L1、网格未写","L1、100 m","用户 10-15 定：不做"],
 ["RD-A（机器列备注）","—","备注：Al Bateen 区域比 RD-A 区域大","用户 10-15 判断：客户写错，RD-A 就是单独的计算区域，与 Al Bateen 无关。我们按 RD-A 汇水区（3.21 km²，与客户一致）计算，不改；15 m 已有的成果也不用重算。"],
 ["MI-8、RB-3、YAS OUTFALL（面积）","表里 36.53 / 15.56 / 13.02 km²","同","用户 10-15 提供的客户地图上是 250.62 / 7.99 / 25.86 km²，与 KMZ 多边形一致 → 表里面积写错，计算域用 KMZ，三个区不再「待确认」，全部开跑"],
 ["机器 4","RB-1、AAM 写机器4","同","我们只有 3 台服务器，没有机器 4；已按面积重新分给服务器1/2/3，与客户表里的机器列不再对应"],
 ["RD-F、RD-C、Qasr、RD-I、RD-G、MI-8、RB-1、YAS OUTFALL、RBE1-OF1","—","面积、场景、格网、完成日期、边界均与第一版一致","无变化"],
 ["Bani Yas leak (B13)","暂未找到","—","用户 10-17：它在 109 库（flood 库 catchment.catchment）里，对应 `RD RB2`（3 块：131.31 + 1.15 + 0.36 km²，大块就是 B14 RD-2）。与 B14 是同一区域 → 不另跑，结果共用 B14 RD-2；表里标注「同 B14」"],
]
for r in rows: cg.append(r)
cg["A1"].font = Font(b=True, size=12)
for c in cg[3]: c.font = Font(b=True, color="FFFFFFFF"); c.fill = G; c.alignment = Alignment(horizontal="center"); c.border = BD
for row in cg.iter_rows(min_row=4):
    for c in row: c.alignment = Alignment(wrap_text=True, vertical="top"); c.border = BD; c.fill = L
for k, w in dict(A=34,B=30,C=40,D=90).items(): cg.column_dimensions[k].width = w

ck = wb.create_sheet("面积核对", 1)
ck.append(["客户地图面积（用户 10-15 提供的图）vs 我们掌握的数据（KMZ 汇水区多边形、客户 / Parsons 水力模型子汇水区）"])
ck.append([])
ck.append(["ADM 序号","汇水区","客户地图面积 km²","KMZ 多边形 km²","KMZ 差","模型子汇水区并集 km²","模型差","结论 / 计算域","积水点数"])
CK = [
 ("A1","RD-A",3.21,3.21,None,None,"KMZ 对得上，用 KMZ"),
 ("A2","RD-F",5.44,5.44,None,None,"KMZ 对得上，用 KMZ"),
 ("A3","RD-C",1.94,1.94,None,None,"KMZ 对得上，用 KMZ"),
 ("A4","Qasr Al Bahr-1",1.52,1.53,None,None,"KMZ 对得上，用 KMZ"),
 ("A5","RD-I",5.79,5.79,None,None,"KMZ 对得上，用 KMZ"),
 ("A6","RD-G",4.28,4.28,None,None,"KMZ 对得上，用 KMZ"),
 ("B10","RB-1",18.42,18.43,16.33,"Musaffah RB-01 模型 16.33","客户地图 18.42 与 KMZ 对得上，用 KMZ"),
 ("B12","RBE1-OF1",22.06,22.07,None,None,"KMZ 对得上，用 KMZ"),
 ("B15","RD-14",15.44,15.45,None,None,"KMZ 对得上，用 KMZ"),
 ("B14","RD-2",126.99,127.07,None,None,"KMZ 对得上，用 KMZ"),
 ("B9","MBZ",18.07,18.08,15.67,"Musaffah_RD09","客户地图 18.07 与 KMZ 18.08 对得上，用 KMZ（客户表 15.26 写错）"),
 ("B11","YAS OUTFALL",25.86,25.88,13.42,"Parsons Airport 01_02","客户地图 25.86 与 KMZ 25.88 对得上，用 KMZ（客户表 13.02 写错）"),
 ("B8","RB-3",7.99,8.00,18.61,"Hydraulic Export RB3","客户地图 7.99 与 KMZ 8.00 对得上，用 KMZ（客户表 15.56 写错）"),
 ("B16","Al Riyadh ponds",76.15,75.42,None,None,"9 块 Pond 求和 76.19，并集 75.42（有重叠）；客户 76.15 与求和一致，计算域取并集"),
 ("B7","MI-8",250.62,250.78,None,None,"客户地图 250.62 与 KMZ 250.78 对得上，用 KMZ（客户表 36.53 写错）"),
]
for aid, nm, cl, kz, md, mdn, concl in CK:
    r = ck.max_row + 1
    ck.append([aid, nm, cl, kz, f"=D{r}/C{r}-1", md, (f"=F{r}/C{r}-1" if md else None), concl, HSN(aid)])
for c in ck[3]: c.font = Font(b=True, color="FFFFFFFF"); c.fill = G; c.alignment = Alignment(horizontal="center", wrap_text=True); c.border = BD
for row in ck.iter_rows(min_row=4):
    bad = False
    for c in row:
        c.border = BD; c.fill = R if bad else L; c.alignment = Alignment(vertical="center", wrap_text=True)
        if c.column in (5, 7): c.number_format = "+0.0%;-0.0%;0.0%"
ck["A1"].font = Font(b=True, size=12)
ck.append([]); ck.append(["核对方法：KMZ 多边形按 UTM 40N 量面积；模型子汇水区取客户 / Parsons 水力模型（Hydraulic Model Data Export、OneDrive_1_9-26-2026、Parsons_Model Data Export）各图层的并集（去掉子汇水区重叠），闭运算 20 m 补缝；差在 ±3% 内算对得上。结论：14 个区的 KMZ 多边形与客户地图面积全部一致（差 ≤ 0.3%），客户计划表里 MI-8、RB-3、MBZ、YAS OUTFALL 四个面积写错，计算域全部用 KMZ 多边形。"])
for k, w in dict(A=9,B=16,C=13,D=14,E=9,F=16,G=9,H=70,I=9).items(): ck.column_dimensions[k].width = w

# 机器配置
mc = wb.create_sheet("机器配置")
mc.append(['计算节点','服务器 IP','负责区域','备注'])
for c in mc[1]: c.font = Font(b=True, color="FFFFFFFF"); c.fill = G; c.alignment = Alignment(horizontal="center"); c.border = BD
mc.append(["服务器1",None,"30 m：MI-8（250.6 km²，2 态）","AZPDW006aim01，48 核 / 188 GB；本批最大一块单独放这台"])
mc.append(["服务器2",None,"30 m：RD-2、YAS OUTFALL、RB-1（共 171 km²，6 态）；10-17 加 B16 Al Riyadh ponds（75.4 km²，2 态）","AZPDW006uca03，32 核 / 64 GB；10-15 按空闲排"])
mc.append(["服务器3",None,"30 m：RBE1-OF1、MBZ、RD-14、RB-3、RD-A/F/C/G/I、Qasr（共 104 km²，20 态）","AZPDW006uca06；小区域多、单态快"])
for row in mc.iter_rows(min_row=2):
    for c in row:
        c.fill = Y if c.column == 2 else L; c.border = BD; c.alignment = Alignment(vertical="center", wrap_text=True)
mc.append([]); mc.append(["在黄色格填入 IP，“30m格网”的服务器 IP 列自动更新；改派机器时直接改“30m格网”的计算节点列（下拉选择）。"])
mc.append(["30 m 格网可后期修改：改“格网”“降雨情景”后把状态改成“需重跑”，我按新参数重跑同一批（产物目录带格网后缀，不覆盖旧结果）。"])
for k, w in dict(A=11,B=16,C=70,D=50).items(): mc.column_dimensions[k].width = w

# 操作清单
op = wb.create_sheet("操作清单")
op.append(['查看时间','服务器','确认算完','启动','备注'])
for c in op[1]: c.font = Font(b=True, color="FFFFFFFF"); c.fill = G; c.alignment = Alignment(horizontal="center"); c.border = BD
op.append([dt.datetime(2026,10,15,9,0),"服务器1/2/3",None,"（待开跑）","10-15 新建 30 m 计划表：3 台服务器空闲，按用户表跑 14 个汇水区（含 10-15 客户新增的 RD-2）× L1/L2 × 61 mm / 1 h = 28 态；需先写 runner 与上传包，上传后 10-16 开跑。用户 10-15 定：没找到汇水区边界的不推演，Bani Yas leak（暂未找到）、Al Riyadh City、AAM 不做（用户 10-15 定）；10-15 客户更新后 RD-2 已加入，详见「客户更新对照」页"])
op.append([dt.datetime(2026,10,17,12,0),"服务器1/2/3","28 态已完成（用户 10-17 确认脚本已执行完）","服务器2：B16 待开跑","10-17 用户要求在服务器2 上跑 B16 Al Riyadh ponds：包 upload/ad_30m/server2_alr.zip（bash ad_30m_server2_alr.sh check / start）；B13 Bani Yas leak = 109 库 RD RB2，与 B14 同域，不另跑；完成日期未记，28 态的「实际完成」列留空"])
op.cell(2,1).number_format = "yyyy-mm-dd hh:mm"
for k, w in dict(A=17,B=14,C=30,D=40,E=80).items(): op.column_dimensions[k].width = w
for c in op[2]: c.alignment = Alignment(wrap_text=True, vertical="top")
op.freeze_panes = "A2"
out = r"C:\freedo\zn-260930\推演计划表_30m_2026-10-17_v9.xlsx"
wb.save(out); print(out, ws.max_row, n, ws2.max_row)
