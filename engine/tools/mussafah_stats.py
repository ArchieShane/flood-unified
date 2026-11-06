# -*- coding: utf-8 -*-
"""Mussafah_00 五态典型统计值 → 交付表（每项带口径、统计口径说明与数据来源）。

为什么（2026-09-28）：用户要在 Mussafah_00 结果目录下输出有意义的统计值，并说明对应的统计口径。
一维（72 h，管网）与一二维（耦合，地面，统一截到 5 h）分开列。态的显示名：L2+A → L2+planned，L2+A+B → L2+suggested。

依赖：out/state_table_Mussafah_00_R61D2.json（一维 / 二维对照）、out/peak_storage_Mussafah_R61D2.json（峰值蓄水）、
      $WORK/c2d/mussafah_sww_stats.json（容器里从 .sww 算的二维统计：tools/mussafah_sww_stats.py，拷到 $WORK/c2d/ 后在 anuga-swmm 镜像里跑）、各态一维 .rpt
产物：out/交付/Mussafah_00_61mm2h/对比_缺口分析/五态统计值.xlsx、.csv
用法：python tools/mussafah_stats.py
"""
from __future__ import annotations

import csv
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

D = ROOT / "out" / "交付" / "Mussafah_00_61mm2h"
S5 = ["L1", "L2", "L2+A", "L2+A+B", "L2+B"]
DISP = {"L1": "L1", "L2": "L2", "L2+A": "L2+planned", "L2+A+B": "L2+suggested", "L2+B": "L2+B"}
RPT = {"L1": "Mussafah_baseline_R61D2_72h", "L2+A": "Mussafah_R61D2_72h", "L2+A+B": "Mussafah_L2AB_R61D2_72h", "L2+B": "Mussafah_L2box_R61D2_72h"}
J = lambda p: json.loads(pathlib.Path(p).read_text(encoding="utf-8"))


def rpt_facts(p):
    t = pathlib.Path(p).read_text(encoding="utf-8", errors="ignore")
    blk = t.split("Flow Routing Continuity", 1)[1][:2500]
    g = lambda lab: float(re.search(rf"{lab} \.+\s+[-\d.]+\s+(-?[\d.]+)", blk).group(1)) * 1000   # 10^6 ltr → m³
    return {"inflow": g("External Inflow"), "outflow": g("External Outflow"), "floodloss": g("Flooding Loss"),
            "final_stored": g("Final Stored Volume"), "cont": float(re.search(r"Continuity Error \(%\) \.+\s+(-?[\d.]+)", blk).group(1))}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ST = J(ROOT / "out/state_table_Mussafah_00_R61D2.json")["table"]
    PK = J(ROOT / "out/peak_storage_Mussafah_R61D2.json")
    W2 = J(WORK / "c2d" / "mussafah_sww_stats.json")
    RF = {s: rpt_facts(WORK / f"{f}.rpt") for s, f in RPT.items()}
    RF["L2"] = RF["L1"]; W2["L2"] = W2["L1"]
    v = lambda k: {s: ST[k]["values"].get(s) for s in S5}
    same = lambda d: {**d, "L2": d["L1"]}
    fac = {}
    for s in ("L2+A", "L2+A+B", "L2+B"):
        rows = [r for r in PK["rows"] if r["state"] == s]
        fac[s] = {"n": len(rows), "cap": sum(r["cap_m3"] or 0 for r in rows), "peak": sum(r["peak_1d_m3"] for r in rows),
                  "full": sum(1 for r in rows if (r["fill_1d_pct"] or 0) >= 94.9)}
    fz = lambda key: {s: (fac[s][key] if s in fac else 0) for s in S5}
    s1 = "一维 72 h 各态 .rpt / .out"; s2 = "一二维耦合 .sww（截到 5 h）"; sT = "out/state_table_Mussafah_00_R61D2.json"
    R = []

    def add(cat, name, basis, unit, vals, note, src):
        R.append((cat, name, basis, unit, vals, note, src))

    # ---------------- 一维
    add("水量", "产流总量（注入管网）", "一维", "m³", {s: RF[s]["inflow"] for s in S5}, "SWMM 连续性表的外部入流：重建的 SCS 产流按 645 个节点注入；五态相同", s1)
    add("水量", "洪水量（节点累计）", "一维", "m³", v("洪水量(节点累计)"),
        "Node Flooding Summary 各井累计溢流之和。模型开了地面积水（ALLOW_PONDING YES），冒出的水会回落再冒，跨井相加同一股水可能重复计——是「涝得多厉害」的强度指标，五态可比，不要当路面水量读", sT)
    add("水量", "Flooding Loss（真实离开系统的水）", "一维", "m³", {s: RF[s]["floodloss"] for s in S5},
        "连续性表的 Flooding Loss：开了地面积水时近似为 0，只有井上积水能力被撑爆时才有；量级与上一行不可比", s1)
    add("水量", "出水口排走", "一维", "m³", {s: RF[s]["outflow"] for s in S5}, "连续性表的外部出流（2 个出水口）", s1)
    add("水量", "72 h 末系统内剩余", "一维", "m³", {s: RF[s]["final_stored"] for s in S5}, "连续性表的末时蓄水（含塘 / 箱涵内的水）", s1)
    add("过程", "街面峰值滞水", "一维", "m³", v("峰值滞水"), "全系统街面滞水（Σ 各井 PONDED_VOLUME）的最大值；已扣除塘 / 箱涵内的水，只算街面", sT)
    add("过程", "街面退水（主网）", "一维", "h", v("退水(主网)"), "街面滞水从峰值落到峰值的 10% 所用时间", sT)
    add("过程", "街面退水（残余上游）", "一维", "h", v("退水(残余上游)"), "街面滞水从峰值落到 < 峰值的 1% 且 < 50 m³ 所用时间", sT)
    add("过程", "系统排空（主网）", "一维", "h", v("系统排空(主网)"),
        "管内蓄水 VOLUME_STORED 从峰值落到「死水位 + 10% ×（峰值 − 死水位）」的时间。调蓄设施按设计就要把水存住再慢放，变长是设计意图", sT)
    add("能力", "蓄存能力（管网 + 蓄水节点）", "一维", "m³", v("蓄存能力"), "按几何算（管内容积 + 蓄水节点容积），含箱涵 0.30 m 超高", sT)
    add("能力", "泵（含既有泵站 RD-27 / RD-34 的 4 台）", "一维", "台", v("临时泵"), "按 .inp 逐个数；L1 的 4 台是既有永久泵站，其余是各态设施的配套泵", sT)
    add("设施", "新增蓄水设施（塘 / 集水井 / 箱涵）", "一维", "座", fz("n"), "相对 L1 新增的蓄水节点（L2+suggested 含 Parsons 的 3 座 + 本系统 6 座箱涵）", "out/peak_storage_Mussafah_R61D2.json")
    add("设施", "新增设施可用库容", "一维", "m³", fz("cap"), "塘按交付模型 1,200 m³（报告写 900）；箱涵不含 0.30 m 超高；集水井没有设计库容，用模型全容积（L2+suggested 里 W-4 没进水、容积读不出，不计）", "out/peak_storage_Mussafah_R61D2.json")
    add("设施", "新增设施峰值蓄水合计", "一维", "m³", fz("peak"), "各设施 72 h 内最大蓄水量之和（各自出现时刻不同）", "out/peak_storage_Mussafah_R61D2.json")
    add("设施", "装满（≥ 95%）的设施", "一维", "座", fz("full"), "峰值蓄水 ÷ 可用库容 ≥ 95%（箱涵 95% 即进水闸关闸水位）", "out/peak_storage_Mussafah_R61D2.json")
    add("数值", "连续性误差", "一维", "%", {s: RF[s]["cont"] for s in S5}, "SWMM Flow Routing Continuity Error", s1)
    # ---------------- 一二维
    w = lambda k: {s: W2[s].get(k) for s in S5}
    add("水量", "管网溢流进地面（1D→2D，到 5 h）", "一二维", "m³", w("acc_1to2_5h_m3"), "耦合运行里一维冒出井盖、交给二维的水量累计（0.5–5 h）", s2)
    add("水量", "地面水经井盖回管网（2D→1D，到 5 h）", "一二维", "m³", w("acc_2to1_5h_m3"), "井盖孔口 / 淹没孔口回流（Cd 0.6、DN600）累计", s2)
    add("水量", "地面上同一时刻最多的水", "一二维", "m³", w("peak_surface_vol_m3"), "逐帧 Σ 水深 × 单元面积 的最大值", s2)
    add("水量", "5 h 末地面上的水", "一二维", "m³", w("end_surface_vol_m3"), "最后一帧（5 h）", s2)
    add("面积", "同一时刻最大淹没面积（≥0.02 m）", "一二维", "ha", w("peak_wet_002_ha"),
        "逐帧里水深 ≥ 0.02 m（干湿阈值）的单元面积，取最大的那一帧；这是报告和系统里「最大积水面积」用的口径，含很薄的水层", s2)
    add("面积", "该时刻", "一二维", "h", w("peak_wet_t_h"), "从 0 h（降雨起点）算", s2)
    add("面积", "全程最大水深 ≥0.02 m 的面积", "一二维", "ha", w("max_area_ge_002_ha"), "各单元全程最大水深 ≥ 阈值的面积之和（不同单元的最大出现在不同时刻，所以比上一行大）", s2)
    add("面积", "全程最大水深 ≥0.10 m 的面积", "一二维", "ha", w("max_area_ge_010_ha"), "同上，阈值 0.10 m", s2)
    add("面积", "全程最大水深 ≥0.30 m 的面积", "一二维", "ha", w("max_area_ge_030_ha"), "同上，阈值 0.30 m", s2)
    add("面积", "全程最大水深 ≥0.80 m 的面积", "一二维", "ha", w("max_area_ge_080_ha"), "同上，阈值 0.80 m", s2)
    add("面积", "5 h 末仍淹没（≥0.02 m）", "一二维", "ha", w("end_wet_002_ha"), "最后一帧", s2)
    add("面积", "水深 ≥0.10 m 持续 ≥ 1 h 的面积", "一二维", "ha", w("area_dur10_ge_1h_ha"), "逐帧（1 分钟）累计水深 ≥ 0.10 m 的时长 ≥ 1 h 的单元面积", s2)
    add("水深", "最大水深", "一二维", "m", w("max_depth_m"), "全部三角单元全程最大水深的最大值", s2)
    add("流速", "最大流速", "一二维", "m/s", w("max_speed_ms"), "|动量| ÷ 水深，只算水深 > 0.05 m 的单元（更浅的流速没有意义）", s2)
    add("流速", "流速 ≥ 0.5 m/s 的面积", "一二维", "ha", w("area_speed_ge_05_ha"), "各单元全程最大流速 ≥ 0.5 m/s 的面积", s2)
    add("流速", "最大「水深 × 流速」", "一二维", "m²/s", w("max_hv_m2s"), "各单元逐帧 水深 × 流速 的最大值；常用作行人危险度的参考量，全场都低于 0.5", s2)
    add("过程", "地面开始积水", "一二维", "h", w("first_wet_h"), "第一次有单元水深 ≥ 0.02 m 的时刻", s2)
    add("计算", "参与交换的井", "一二维", "口", w("n_coupled"), "一维里会冒水、并落在二维域内的井（只有这些井和地面交换）", s2)
    add("计算", "计算域 / 网格", "一二维", "", {s: f"{W2[s]['domain_ha']:,.0f} ha；{W2[s]['n_tri']:,} 个三角单元" for s in S5}, "见 计算设置.md", s2)

    def ch(vals, a, b):
        x, y = vals.get(a), vals.get(b)
        return round((y - x) / x * 100, 1) if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x else None
    hdr = ["类别", "指标", "口径", "单位"] + [DISP[s] for s in S5] + ["L2+planned 相对 L2 %", "L2+suggested 相对 L2+planned %", "L2+B 相对 L2 %", "统计口径说明", "数据来源"]
    rows = []
    NOPCT = {"连续性误差", "该时刻", "地面开始积水", "计算域 / 网格"}      # 这几项算变化率没有意义
    for cat, name, basis, unit, vals, note, src in R:
        fmt = lambda x: round(x, 3) if isinstance(x, float) else x
        pc = [None] * 3 if name in NOPCT else [ch(vals, "L2", "L2+A"), ch(vals, "L2+A", "L2+A+B"), ch(vals, "L2", "L2+B")]
        rows.append([cat, name, basis, unit] + [fmt(vals.get(s)) for s in S5] + pc + [note, src])
    out = D / "对比_缺口分析"
    with open(out / "五态统计值.csv", "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.writer(fh); wr.writerow(hdr); wr.writerows(rows)
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    wb = Workbook(); ws = wb.active; ws.title = "统计值"; ws.append(hdr)
    for r in rows:
        ws.append(r)
    for c in ws[1]:
        c.font = Font(bold=True)
    for col, wd in zip("ABCDEFGHIJKLMN", (7, 34, 8, 6, 11, 11, 12, 13, 11, 11, 13, 11, 70, 30)):
        ws.column_dimensions[col].width = wd
    for row in ws.iter_rows(min_row=2):
        row[12].alignment = Alignment(wrap_text=True, vertical="top")
    w2 = wb.create_sheet("口径总说明")
    for t in ["61 mm / 2 h；Mussafah_00 汇水区 82 ha；五态共用同一张管网、同一场雨，只差设施",
              "L2 = L1：区内没有已建成的塘（最近一座在 7.3 km 外），两列数相同",
              "一维：SWMM 5.2.4，72 h，开地面积水（ALLOW_PONDING YES）；产流按顾问参数重建（SCS CN + 单位线），645 个节点注入",
              "一二维：SWMM + ANUGA 双向耦合，15 m 网格 258,332 个三角单元，计算域 1,453 ha（面积的分母是它，不是 82 ha 的汇水区）；二维上不直接下雨",
              "一二维各态算到的时长不同（L1 10 h，其余 5 h），这里统一截到 5 h",
              "「相对 %」按主线（L2 → L2+planned → L2+suggested）与对照（L2 → L2+B）各自的上一态算",
              "面积按三角单元真实面积加权；水深取形心值；< 0.02 m 算干",
              "完整输入参数见 计算设置.md"]:
        w2.append([t])
    wb.save(out / "五态统计值.xlsx")
    print(f"→ {out / '五态统计值.xlsx'}（{len(rows)} 项）")
    for r in rows:
        print("  ", r[2], r[1], r[4:9], r[9:12])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
