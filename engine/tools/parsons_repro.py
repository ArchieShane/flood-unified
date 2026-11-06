# -*- coding: utf-8 -*-
"""Mussafah_00 · Parsons 报告复现对照：在 **Parsons 自己的雨**（10 年一遇 + 20% CC，61.34 mm / 24 h）下，
把报告里的数、客户交付的 Parsons 模型跑出来的数、我们剥出来的 L1 基准跑出来的数放在一起。

为什么要有这张表：交付目录里的 L2+A 是「Parsons 的设施 + 我们的雨（61 mm / 2 h）+ 我们的二维」，
不是复现。没有这张表，L2+A 的数很容易被当成对 Parsons 报告的验证去引用。

报告：D:/世界模型/DMT_Main land Catchment Assessment_Mussafah_Rev.00_17Sep 2026.pdf（Rev.00，2026-09-17）
我们的两次运行（已有结果，不重跑）：
  $WORK/Mussafah.inp           客户交付的 Parsons「治理后」模型原件（含 STO-PO3 / W-4 / W-6 / 2 台泵）
  $WORK/Mussafah_baseline.inp  我们从原件剥掉 Parsons 措施得到的 L1（tools/strip_baseline.py），不是 Parsons 的现状模型
入流是交付模型自带的过程线，tools/runoff.py 用 61.34 mm / 24 h 反算复现到 +0.01%。

产物：out/交付/Mussafah_00_61mm2h/对比_缺口分析/Parsons复现对照.{json,md,xlsx}
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import parsons_table as PT  # noqa: E402

D = ROOT / "out" / "交付" / "Mussafah_00_61mm2h" / "对比_缺口分析"
PDF = "DMT_Main land Catchment Assessment_Mussafah_Rev.00_17Sep 2026.pdf"

# 报告原文数字，(值, 出处)。报告前后不一致的地方两个都记
REPORT = {
    "flood_E": [(832, "表 4 / 表 7 / 表 9 / 表 17")],
    "flood_M": [(302.3, "表 12 / 表 17"), (3324, "表 14「Flood Volume for Mitigation System」")],
    "rec_maj_E": [(2.21, "表 4 / 表 7 / 表 17")],
    "rec_maj_M": [(1.17, "表 12 / 表 14 / 表 17")],
    "rec_res_E": [(17, "表 17"), (8, "表 7 / 第 33 页正文")],
    "rec_res_M": [(7, "表 17"), (4, "表 14"), (5, "第 33 页正文「最长 5 h」")],
    "empty_E": [(22.0, "表 4 / 表 17 / §15"), (17, "§9 效益「17 → 13 h」")],
    "empty_M": [(19, "表 12 / 表 17 / §15"), (13, "§9")],
    "stor_E": [(11354, "表 1 / 表 7 / 表 14 地下管网容积"), (21323, "表 17（= 表 9 的排放量 21,323.71，不是容积）")],
    "stor_M": [(12254, "表 14 = 11,354 + 900"), (22223, "表 17")],
    "pond": [(900, "表 14 / 表 17「+900 m³」")],
    "serv_E": [(25, "表 7 / 表 17")],
    "serv_M": [(30, "表 17"), (35, "表 14")],
    "pumps_E": [(0, "表 17（只算临时泵）")],
    "pumps_M": [(2, "表 17「1+1 at Outfall」/ §14「2 台 12 寸移动泵」")],
}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    E = PT.one("Mussafah", "_baseline", log=print)
    M = PT.one("Mussafah", "", log=print)
    if not E or not M:
        return 1
    pond_model = 1200.0                                   # [STORAGE] STO-PO3 1.5 m × 800 m²（export_facilities 同口径）
    rp = lambda k: "；".join(f"{v:,g}（{src}）" for v, src in REPORT[k])
    rows = [
        # 项, 报告现状, 报告缓解后, 我们 L1, 我们交付模型, 单位, 读法
        ("洪水量", rp("flood_E"), rp("flood_M"), E["flood_nodesum_m3"], M["flood_nodesum_m3"], "m³",
         "缓解后：交付模型 3,209 与报告表 14 的 3,324 差 3.5%，对得上；与表 12 / 17 的 302.3 差 10 倍。"
         "现状：我们的 L1 4,059 是报告 832 的 4.9 倍——L1 是我们剥出来的，不是 Parsons 的现状模型，"
         "而报告现状 832 比它自己缓解后 3,324 还小，说明报告两处用的不是同一个口径或同一版模型。"
         "我们的洪水量 = Node Flooding Summary 各井累计（模型 ALLOW_PONDING YES，Flooding Loss 只有 0 / 20 m³）"),
        ("退水（主网）", rp("rec_maj_E"), rp("rec_maj_M"), E["recession_majority_h"], M["recession_majority_h"], "h",
         "方向一致（缓解后都缩短约 2/3），绝对值我们长 3–4 倍。判据不同：我们按全系统滞水量从峰值落到 10%，报告没给定义"),
        ("退水（残余上游）", rp("rec_res_E"), rp("rec_res_M"), E["recession_residual_h"], M["recession_residual_h"], "h",
         "我们 11.3 → 4.1 h，落在报告自己两套数（8 → 4 / 5 h 与 17 → 7 h）之间"),
        ("系统排空（主网）", rp("empty_E"), rp("empty_M"), E["emptying_majority_h"], M["emptying_majority_h"], "h",
         "对不上：报告缓解后缩短，我们反而长 1 h。我们按管内蓄水量落到「死水位 + 10%」算，"
         "缓解后死水位从 2,764 降到 1,541 m³，判据线变低所以更晚到；报告没给定义"),
        ("蓄存能力", rp("stor_E"), rp("stor_M"), E["storage_total_m3"], M["storage_total_m3"], "m³",
         "管网容积对得上：我们按几何算 11,126 m³，报告 11,354（−2.0%）。缓解后我们 +2,407 m³（塘 1,200 + 两口集水井 478 + 新管），"
         "报告 +900。表 17 的 21,323 其实是表 9 的排放量，不是容积"),
        ("临时塘库容", rp("pond"), "", None, pond_model, "m³",
         "交付模型里 STO-PO3 是 1.5 m × 800 m² = 1,200 m³，报告写 900 m³——报告和交付模型可能不是同一版"),
        ("临时泵", rp("pumps_E"), rp("pumps_M"), E["n_pumps"], M["n_pumps"], "台",
         "都是 +2 台。我们数的是模型里全部泵（L1 已有 RD-27 / RD-34 两座既有泵站 4 台），报告只数临时泵"),
        ("降雨服务水平", rp("serv_E"), rp("serv_M"), None, None, "mm", "未算：要二分搜索、每问重跑一次 SWMM，可以补"),
    ]
    out = {"report": PDF, "scenario": "10 年一遇 + 20% CC，61.34 mm / 24 h（Parsons 情景 1）",
           "ours_existing": "Mussafah_baseline：我们从交付原件剥掉 Parsons 措施得到的 L1（不是 Parsons 的现状模型）",
           "ours_mitigated": "Mussafah：客户交付的 Parsons 治理后模型原件，原样运行",
           "sim_h": E["sim_h"], "peak_ponded_m3": [E["peak_ponded_m3"], M["peak_ponded_m3"]],
           "dead_storage_m3": [E["dead_storage_m3"], M["dead_storage_m3"]],
           "rows": [dict(zip(("item", "report_existing", "report_mitigated", "ours_L1", "ours_delivered", "unit", "reading"), r)) for r in rows],
           "conclusion": [
               "缓解后洪水量复现了报告表 14（3,209 vs 3,324 m³，−3.5%）；报告表 12 / 17 的 302.3 m³ 我们复现不出",
               "现状洪水量没有复现（我们的 L1 4,059 vs 报告 832）：我们没有 Parsons 的现状模型，L1 是剥出来的",
               "管网容积对得上（11,126 vs 11,354 m³，−2.0%）；退水方向一致、绝对值不同；系统排空方向相反",
               "报告自身有 6 处前后不一致（缓解后洪水量、残余退水、系统排空、蓄存能力、服务水平、塘容），"
               "在拿到 Parsons 的现状模型和指标定义之前，报告的数只能作参考，不能当作复现目标",
               "交付目录里的 L2+A（61 mm / 2 h + 二维）不是复现，是「Parsons 的设施遇到更猛的雨」"],
           "ask_client": ["Parsons 的现状（治理前）模型 .inp",
                          "报告「Total Flood Volume」「Recession Time」「System Emptying Time」的计算定义",
                          "表 12 / 17（302.3 m³）与表 14（3,324 m³）哪个是正确的缓解后洪水量",
                          "STO-PO3 的设计库容是 900 还是 1,200 m³"]}
    D.mkdir(parents=True, exist_ok=True)
    (D / "Parsons复现对照.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    f = lambda v, u: "—" if v is None else (f"{v:,.2f}" if u == "h" else f"{v:,.0f}")
    md = [f"# Mussafah_00 · Parsons 报告复现对照", "",
          f"**同一场雨**：{out['scenario']}。报告：`{PDF}`。", "",
          "| 项 | 报告 现状 | 报告 缓解后 | 我们 L1（剥出来的） | 我们 交付模型原件 | 单位 |", "|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r[0]} | {r[1] or '—'} | {r[2] or '—'} | {f(r[3], r[5])} | {f(r[4], r[5])} | {r[5]} |")
    md += ["", "## 逐项读法", ""] + [f"- **{r[0]}**：{r[6]}" for r in rows]
    md += ["", "## 结论", ""] + [f"{i}. {c}" for i, c in enumerate(out["conclusion"], 1)]
    md += ["", "## 要向客户要的", ""] + [f"- {c}" for c in out["ask_client"]]
    md += ["", "## 我们两次运行是什么", "", f"- 现状列：{out['ours_existing']}", f"- 缓解后列：{out['ours_mitigated']}",
           f"- 入流：交付模型自带的过程线（`tools/runoff.py` 用 61.34 mm / 24 h 反算复现到 +0.01%）；演算 {E['sim_h']:.0f} h",
           "- 各项判据见 `tools/parsons_table.py` 模块说明", ""]
    (D / "Parsons复现对照.md").write_text("\n".join(md), encoding="utf-8")

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    wb = Workbook(); ws = wb.active; ws.title = "复现对照"
    ws.append([f"同一场雨：{out['scenario']}"]); ws.append([])
    ws.append(["项", "报告 现状", "报告 缓解后", "我们 L1（剥出来的）", "我们 交付模型原件", "单位", "读法"])
    for c in ws[3]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([r[0], r[1], r[2], r[3] if r[3] is None else round(r[3], 2), r[4] if r[4] is None else round(r[4], 2), r[5], r[6]])
    ws.append([]); ws.append(["结论"])
    for c in out["conclusion"]:
        ws.append([c])
    ws.append([]); ws.append(["要向客户要的"])
    for c in out["ask_client"]:
        ws.append([c])
    for col, w in zip("ABCDEFG", (16, 34, 40, 18, 18, 6, 90)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=4):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(D / "Parsons复现对照.xlsx")
    for r in rows:
        print(f"  {r[0]:<10} 报告 {r[1][:30]:<32} → {r[2][:30]:<32} | 我们 {f(r[3], r[5]):>8} → {f(r[4], r[5]):>8} {r[5]}")
    print(f"→ {D / 'Parsons复现对照.md'}（及 .json / .xlsx）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
