# -*- coding: utf-8 -*-
"""ADM 全市 250 m · L2 计划表（2026-10-19）：计划 / 汇总 / 服务器配置 / 自动化流程。"""
import sys
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.stdout.reconfigure(encoding="utf-8")
OUT = r"C:\freedo\zn-260930\ADM全市250m_L2_计划_2026-10-19.xlsx"
H = {1: 4.0, 2: 5.0, 24: 9.0}      # 单态估算耗时 h（沿用全市 250 m 估算，没实测）
PLAN = [
    (1, "CITY_M33H24", 33, 24), (1, "CITY_M61H24", 61, 24), (1, "CITY_M96H24", 96, 24), (1, "CITY_M33H1", 33, 1),
    (2, "CITY_M61H2", 61, 2), (2, "CITY_M96H2", 96, 2),
    (3, "CITY_M33H2", 33, 2), (3, "CITY_M61H1", 61, 1), (3, "CITY_M96H1", 96, 1),
]
SRV = {1: ("服务器1", "AZPDW006aim01", 48, "188 GB", 3), 2: ("服务器2", "AZPDW006uca03", 32, "64 GB", 1), 3: ("服务器3", "AZPDW006uca06", 32, "64 GB", 1)}
EST = {1: 13, 2: 10, 3: 13}      # 服务器1：3 槽并行（24 h×3 并行 9 h，再接 1 h 雨 4 h）；服务器2/3：1 槽串行

wb = openpyxl.Workbook()
hdr = PatternFill("solid", fgColor="DDEBF7")


def sheet(ws, rows, widths):
    for r in rows:
        ws.append(r)
    for c in ws[1]:
        c.font = Font(bold=True); c.fill = hdr
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")


ws = wb.active; ws.title = "计划"
rows = [["服务器", "任务（情景）", "雨量 mm", "历时 h", "态", "网格", "模拟时长 h", "单态估算耗时 h（没实测）", "交付目录（NAS result/<主机>/ 下）"]]
for s, tag, mm, h in PLAN:
    rows.append([SRV[s][0], tag, mm, h, "L2", "250 m", h + 4, H[h], f"ADM_City_{mm}mm{h}h_250m/"])
sheet(ws, rows, [10, 16, 9, 8, 6, 8, 12, 22, 34])
ws2 = wb.create_sheet("汇总")
rows = [["服务器", "任务数", "同时算的态", "串行估算 h", "预计墙钟 h（估算）", "任务"]]
for s in (1, 2, 3):
    ts = [p for p in PLAN if p[0] == s]
    rows.append([SRV[s][0], len(ts), SRV[s][4], sum(H[p[3]] for p in ts), EST[s], "、".join(p[1] for p in ts)])
rows.append(["合计", len(PLAN), "", sum(H[p[3]] for p in PLAN), "", ""])
rows.append(["说明", "范围：Abu Dhabi 全市（计算域 1,233.6 km²），250 m，33/61/96 mm × 1/2/24 h = 9 场，只算 L2；口径同 ADM 汇水区各批。不做 L1、逐帧图、shp+dat、flood-viewer、flood-aio。"
             "耗时是沿用的估算（1 h 雨 4 h、2 h 雨 5 h、24 h 雨 9 h 每态），内存与单态耗时开跑后以 status / docker stats 为准；开跑日期待定，计划表不写具体结束日。", "", "", "", ""])
sheet(ws2, rows, [10, 60, 12, 12, 16, 60])
ws3 = wb.create_sheet("服务器配置")
rows = [["服务器", "主机名", "CPU 核数", "内存", "同时算的态（SLOTS 缺省 = 内存 GB / 60，至少 1）", "每态线程（缺省 核数 / SLOTS，最多 16）"]]
for s in (1, 2, 3):
    rows.append([SRV[s][0], SRV[s][1], SRV[s][2], SRV[s][3], SRV[s][4], min(16, SRV[s][2] // SRV[s][4])])
sheet(ws3, rows, [10, 18, 10, 10, 40, 34])
ws4 = wb.create_sheet("自动化流程")
rows = [["步骤", "谁做", "做什么", "输出位置"],
        [0, "各服务器 start 之后自动（第一次）", "prep：读 GDB 重建全市一维网，截取计算域 / 塘 211 座 / 现状泵站 88 座（其中约 32 座有数据能建泵）；DTM 取平均成 250 m DEM（全市共用一份）", "容器 /work/c2d/ax_net_CITY.pkl、dem_AX250.tif"],
        [1, "同上", "建 L2 → L2 一二维耦合计算（每态一个容器 axpipe_<标签>_L2）", "容器 /work/c2d/"],
        [2, "同上，每场算完就接着做", "后处理：积水点结果表与图层（470 个积水点）、统计、.sww、.npz、最大水深 tif、计算设置，直接写 NAS", "result/<主机>/ADM_City_<雨量>mm<历时>h_250m/"],
        [3, "同上", "逐项核对（交付文件、.sww、.npz、tif、积水点表），缺一项都不算完成", "驱动日志 + .fin_server<N>/"],
        ["—", "本批不做", "L1、L2−L1 对比、逐帧图 / flood-viewer、shp+dat、flood-aio 登记；以后要展示从交付目录的 .sww / .npz 另补", ""]]
sheet(ws4, rows, [8, 30, 90, 50])
wb.save(OUT)
print("ok", OUT)
