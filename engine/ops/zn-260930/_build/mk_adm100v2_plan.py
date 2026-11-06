# -*- coding: utf-8 -*-
"""ADM 全市 100 m v2 计划表（2026-10-21）：同 250 m v2 的数据口径与交付内容，网格改 100 m。"""
import sys
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.stdout.reconfigure(encoding="utf-8")
OUT = r"C:\freedo\zn-260930\ADM全市100m_v2_计划_2026-10-21.xlsx"
# 250 m v2 实测（单态含 prep 摊销）：1 h 雨约 2.6–3.2 h，2 h 雨约 3.5–3.8 h，24 h 雨 3 态并行约 9–11 h
# 100 m 估算：二维单元 ×6.25、时间步约 ×2.5，一维 SWMM 与耦合交换不变 → 整体按 5–10 倍估（没实测，需先试跑）
H250 = {1: 2.9, 2: 3.7, 24: 10.0}
LO, HI = 5, 10
PLAN = [
    (1, "CITY_M33H24", 33, 24), (1, "CITY_M61H24", 61, 24), (1, "CITY_M96H24", 96, 24),
    (2, "CITY_M33H1", 33, 1), (2, "CITY_M61H1", 61, 1), (2, "CITY_M96H1", 96, 1),
    (3, "CITY_M33H2", 33, 2), (3, "CITY_M61H2", 61, 2), (3, "CITY_M96H2", 96, 2),
]
SRV = {1: ("服务器1", "AZPDW006aim01", 48, "188 GB"), 2: ("服务器2", "AZPDW006uca03", 32, "64 GB"), 3: ("服务器3", "AZPDW006uca06", 32, "64 GB")}
hdr = PatternFill("solid", fgColor="DDEBF7")
wb = openpyxl.Workbook()


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
rows = [["服务器", "任务（情景）", "雨量 mm", "历时 h", "态", "网格", "250 m v2 实测单态 h", "100 m 估算单态 h（没实测）", "交付目录（NAS result/<主机>/ 下）"]]
for s, tag, mm, h in PLAN:
    rows.append([SRV[s][0], tag, mm, h, "只推演 L2（交付里命名为 L1）", "100 m", H250[h], f"{H250[h]*LO:.0f}–{H250[h]*HI:.0f}", f"ADM_City_{mm}mm{h}h_100m_v2/"])
sheet(ws, rows, [10, 16, 9, 8, 14, 8, 16, 20, 36])

ws2 = wb.create_sheet("汇总")
rows = [["服务器", "任务数", "串行估算 h（下限–上限）", "任务", "说明"]]
for s in (1, 2, 3):
    ts = [p for p in PLAN if p[0] == s]
    t = sum(H250[p[3]] for p in ts)
    rows.append([SRV[s][0], len(ts), f"{t*LO:.0f}–{t*HI:.0f}", "、".join(p[1] for p in ts),
                 {1: "三场 24 h 雨：内存最大，100 m 下 .sww / 状态随步数涨，可能只能 1 槽串行（188 GB）；先试跑定槽数",
                  2: "三场 1 h 雨：最快，可 1 槽（64 GB）", 3: "三场 2 h 雨：1 槽"}[s]])
rows.append(["合计", len(PLAN), f"{sum(H250[p[3]] for p in PLAN)*LO:.0f}–{sum(H250[p[3]] for p in PLAN)*HI:.0f}", "", ""])
rows.append(["范围", "Abu Dhabi 全市（计算域 1,233.6 km²，同 250 m v2），100 m 二维网格，33/61/96 mm × 1/2/24 h = 9 场，只推演 L2（程序内部态名 L2；交付的目录、文件名、正文一律叫 L1，因用户 10-20 要求“L2 改成 L1、无中文”）；一维网络全部来自 合并结果_20261020（同 250 m v2：159 泵组、180 塘+22 渗井、雨水口面积），Catchment 不参与，CN 82 / Tc 30 min。", "", "", ""])
rows.append(["交付内容", "与 250 m v2 完全相同：L1_*（L2 结果，按 250 m v2 同名结构）/{1D,2D}、hotspots/、statistics/、common/（含 data_usage_audit.csv、pumps_not_built.csv）、00_README.md、calculation_settings.md；.sww/.npz/最大水深 tif；全英文无中文；成果目录加 _100m_v2；算完 collect 到平级英文目录 adm_city_100m_v2/ 并写 MANIFEST.csv。不做 L1/L2+planned 等其他态与对比、逐帧图、shp+dat、flood-viewer、flood-aio。", "", "", ""])
sheet(ws2, rows, [10, 60, 22, 60, 60])

ws3 = wb.create_sheet("风险与前置")
rows = [["#", "项", "说明 / 建议"],
        [1, "耗时没实测", "100 m 单元约 ×6.25、步长更小；估 5–10 倍 250 m 耗时。建议先在一台服务器试跑一场 33mm1h（约一两天内能看到速率），再定整体排期。"],
        [2, "24 h 场内存", "250 m 老版 24 h 单态峰值 57 GB；100 m 的二维输出与状态按单元数线性涨，预计远超此值，服务器 2/3（64 GB）放不下，故 24 h 全放服务器 1，且很可能 1 槽。可能需降低 .sww 输出帧频以控内存（会影响逐帧细节，需你定）。"],
        [3, "DEM", "需新取 100 m DEM（DTM 50 cm 块平均，prep 一次，缓存 dem_AV100.tif）；耦合程序按 100 m 网格读入需实测。"],
        [4, "沿用的未实测假设", "250 m v2 里的假设照旧：110 个泵组按抽出计算域、117 个塘容积估算、雨水口 0.10 m² 面积。若 250 m v2 结果核对后要调整，100 m 应在调整后再开跑，避免重算。"],
        [5, "任务分工原则", "按单态负荷均分：S1 三场 24 h，S2 三场 1 h，S3 三场 2 h。S2/S3 先做完可接手 S1 的积压（但 24 h 场内存不够，不能接）。"],
        [6, "包与脚本", "以 upload/adm_250m_v2 为基础派生 upload/adm_100m_v2（RES_M=100、前缀、成果名 _100m_v2、tsv、collect 脚本改名），不覆盖旧包。你确认本计划后再写、本机验证后上传。"]]
sheet(ws3, rows, [5, 20, 110])
wb.save(OUT)
print("ok", OUT)
