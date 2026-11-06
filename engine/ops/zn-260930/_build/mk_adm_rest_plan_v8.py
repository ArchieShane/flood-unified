# -*- coding: utf-8 -*-
"""ADM 剩余汇水区计划表 v8：49 个任务（OLD 拆 6 块），每条到日期，带服务器配置。数据取 cases/ad/rest_regions.json。"""
import datetime as dt, json, math
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

R = "C:/freedo/zn-260930/"
reg = json.load(open(R + "thinking-drainage-system/cases/ad/rest_regions.json", encoding="utf-8"))
ov = {}
for it in json.load(open(R + "_build/rest_plan_items.json", encoding="utf-8")):
    ov[it["lid"]] = it["ov"]
T0 = dt.datetime(2026, 10, 20)
items = [dict(code=c, **v) for c, v in reg.items()]
for it in items:
    it["w"] = max(it["km2"] * (36 if it["res"] == 5 else 1), 0.5)
tot = {s: sum(i["w"] for i in items if i["server"] == s) for s in (1, 2, 3)}
cum = {1: 0.0, 2: 0.0, 3: 0.0}
G = PatternFill("solid", fgColor="FF1F7A4D"); Y = PatternFill("solid", fgColor="FFFFF2CC")
thin = Side(style="thin", color="FFBFBFBF"); BD = Border(left=thin, right=thin, top=thin, bottom=thin)
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "计划"
ws.append(["开始日期", "预计结束", "序号", "任务标签", "汇水区", "库 id", "面积 km²", "格网", "降雨", "方案", "服务器", "ADM积水点数", "与已推演区重叠", "交付目录名（NAS / flood-aio 交付名）", "备注"])
for c in ws[1]:
    c.font = Font(b=True, color="FFFFFFFF"); c.fill = G; c.alignment = Alignment(horizontal="center", wrap_text=True); c.border = BD
n = 0
cur = {}
for it in sorted(items, key=lambda x: (x["server"], 0)):          # 保持生成顺序：每台内大的在前
    k = it["server"]; c0 = cum[k]; cum[k] += it["w"]
    d0 = min(3, int(c0 / tot[k] * 4)); d1 = min(3, max(d0, math.ceil(cum[k] / tot[k] * 4) - 1))
    st, en = T0 + dt.timedelta(days=d0), T0 + dt.timedelta(days=d1); cur[k] = en
    n += 1
    note = []
    if it["res"] == 5: note.append("<1 km²，5 m 精度")
    if "part" in it["title"]: note.append("OLD 是 6 块互不相连的多边形，二维域只能是单个多边形，拆成 6 个任务")
    if ov.get(it["libid"]) and ov[it["libid"]] >= 0.1: note.append(f"与已推演区重叠 {ov[it['libid']]:.0%}，边缘水深以已推演区为准")
    if it["hs_n"] == 0: note.append("无 ADM 积水点")
    if it["feat"] == "Mussafah_00": note.append("15 m 做过，此为 30 m")
    if it["feat"] == "B_C": note.append("多边形里 400 m² 的洞已填")
    ws.append([st, en, n, f"{it['code']}_M61H1", it["title"], it["libid"], it["km2"], f"{it['res']} m", "61 mm / 1 h", "L1+L2", f"服务器{k}", it["hs_n"], ov.get(it["libid"]),
               f"{it['dir']}_61mm1h_{it['res']}m", "；".join(note)])
    if it["res"] == 5:
        ws.cell(ws.max_row, 8).fill = Y
for row in ws.iter_rows(min_row=2):
    for c in row:
        c.border = BD
    row[0].number_format = row[1].number_format = "yyyy-mm-dd"
for col, w in zip("ABCDEFGHIJKLMNO", [12, 12, 6, 22, 24, 7, 10, 7, 12, 8, 10, 12, 14, 30, 44]):
    ws.column_dimensions[col].width = w
s = wb.create_sheet("汇总")
s.append(["服务器", "任务数", "30 m 面积 km²", "5 m 个数", "预计结束"])
for k in (1, 2, 3):
    sel = [i for i in items if i["server"] == k]
    s.append([f"服务器{k}", len(sel), round(sum(i["km2"] for i in sel if i["res"] == 30), 1), sum(1 for i in sel if i["res"] == 5), cur[k].strftime("%m-%d")])
s.append([]); s.append(["合计", len(items), round(sum(i["km2"] for i in items if i["res"] == 30), 1), sum(1 for i in items if i["res"] == 5)])
s.append([]); s.append(["说明", "按 4 天（10-20~10-23）排，含冗余时间：本机实测单个任务很快（0.2 km² 的 5 m 约 2.5 分钟、1.3 km² 的 30 m 约 30 秒，L1+L2），实际预计几个小时到一天，多出来的时间应对实测变慢和重跑。"])
for cell in s[1]:
    cell.font = Font(b=True, color="FFFFFFFF"); cell.fill = G
c = wb.create_sheet("服务器配置")
c.append(["服务器", "主机名", "CPU 核数", "内存", "算力权重", "说明"])
for r in [["服务器1", "AZPDW006aim01", "48", "188 GB", 1.5, "更强，分配最多；flood-aio / flood-viewer 也在这台，最后的发布在这台做"],
          ["服务器2", "AZPDW006uca03", "32", "64 GB", 1.0, ""],
          ["服务器3", "AZPDW006uca06", "32", "64 GB", 1.0, ""]]:
    c.append(r)
for cell in c[1]:
    cell.font = Font(b=True, color="FFFFFFFF"); cell.fill = G
for col, w in zip("ABCDEF", [10, 18, 10, 10, 10, 60]):
    c.column_dimensions[col].width = w
f = wb.create_sheet("自动化流程")
for r in [["步骤", "谁做", "做什么", "输出位置"],
          [1, "各服务器 start 之后自动", "建模 → L1/L2 二维计算 → 后处理（.sww、.npz、最大水深 tif、积水点结果表与图层、统计）直接写 NAS", "result/<主机>/<区域目录>_61mm1h_<网格>m/"],
          [2, "同上，每个任务算完就接着做", "逐帧图进 flood-viewer", "result/<主机>/runtime/console_data/flood2d_ad_<代号>_m61h1g<网格>/"],
          [3, "同上", ".sww → shp+dat（不过滤 1 cm）", "shp_dat/<主机>/<区域目录>_61mm1h_<网格>m/<L1|L2>/{shp,dat}"],
          [4, "同上", "逐项核对（交付文件、npz、shp+dat、viewer），缺一项都不算完成", "驱动日志 + .fin_server<N>/"],
          [5, "服务器1 上跑 adm_rest_publish.sh go（三台都算完后）", "登记到 flood-aio（得到任务 id）→ shp_dat 改名成 id → viewer 重扫 → 对照表", "shp_dat/floodh2d/<任务 id>/{L1,L2}/；adm_rest_id_map.csv"]]:
    f.append(r)
for cell in f[1]:
    cell.font = Font(b=True, color="FFFFFFFF"); cell.fill = G
for col, w in zip("ABCD", [6, 36, 70, 60]):
    f.column_dimensions[col].width = w
wb.save(R + "ADM剩余汇水区_61mm1h_计划_2026-10-19_v8.xlsx")
print(len(items), {k: (len([i for i in items if i["server"] == k]), cur[k].strftime("%m-%d")) for k in (1, 2, 3)})
