# -*- coding: utf-8 -*-
"""全市 L1 / L2 典型统计值 → 交付表（每项带口径、统计口径说明与数据来源）。

为什么（2026-09-28）：用户要把全市 L1 / L2 有意义的统计值输出到结果文件，并注明统计口径。
一维（管网溢流，8 片，CN 82）与一二维（地面积水，整网耦合，客户分区 CN / Tc）分开列，不跨口径相减。

产物：out/交付/全市_61mm2h/对比_缺口分析/全市L1L2统计值.xlsx、.csv
用法：python tools/city_stats.py
"""
from __future__ import annotations

import csv
import json
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

D = ROOT / "out" / "交付" / "全市_61mm2h"
J = lambda p: json.loads(pathlib.Path(p).read_text(encoding="utf-8"))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    G = J(ROOT / "out/city_gap_v2.json"); c = G["city"]; L1, L2 = c["L1"], c["L2"]; pn = c["pond_networks"]
    ST = J(ROOT / "console/data/flood2d_city2d/stats.json"); MF = J(ROOT / "console/data/flood2d_city2d/manifest.json")
    Z = J(ROOT / "out/city2d_zones_R61D2.json"); CS = J(ROOT / "console/data/city_series.json")
    TW = {s: J(WORK / "c2d" / f"twoway_City2D_{s}.json") for s in ("L1", "L2")}
    fr = {x["state"]: x["frames"] for x in MF["states"]}
    ce = {}
    for s in ("L1", "L2"):
        v = []
        for i in range(1, 9):
            m = re.search(r"Flow Routing Continuity.*?Continuity Error \(%\) \.+\s+(-?[\d.]+)", (WORK / f"City_{s}_v2_p{i}.rpt").read_text(errors="ignore"), re.S)
            v.append(float(m.group(1)))
        ce[s] = v
    chg = {}
    with open(D / "对比_缺口分析" / "溢水点_L1对L2.csv", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            chg[r["变化"]] = chg.get(r["变化"], 0) + 1
    nsp = {s: sum(1 for _ in open(D / f / "一维" / "溢水点" / "溢水点汇总.csv", encoding="utf-8-sig")) - 1
           for s, f in (("L1", "L1_既有管网"), ("L2", "L2_加22座已建成塘"))}
    sysq = {s: np.array(CS["sys"][s], float) for s in ("L1", "L2")}; tq = CS["t_h"]
    hmax = {}
    for s in ("L1", "L2"):
        z = np.load(WORK / "c2d" / f"City2D_{s}_2d.npz")
        i = int(np.argmax(z["hmax"])); x, y = (float(v) for v in z["xy"][i])
        lo, la = tr.transform(x, y); hmax[s] = (float(z["hmax"][i]), la, lo)
    P2 = ST["L2"]["ponds"]
    store = sum(p.get("vol_end_m3", 0) for p in P2); cap = sum(p.get("vol_design_m3", 0) for p in P2)
    full = sum(1 for p in P2 if (p.get("fill_pct") or 0) >= 99); low = sum(1 for p in P2 if (p.get("fill_pct") or 0) < 30)
    S1, S2 = ST["L1"], ST["L2"]; A = Z["all"]
    s1d = "一维 8 片 City_*_v2_p*.rpt（30 h 窗口）"; s1t = "一维 City_*_v2ts_p*.out（6 h，逐 5 分钟）"; s2d = "一二维 twoway_City2D_*.json / City2D_*_2d.npz"
    R = []   # (类别, 指标, 口径, 单位, L1, L2, 统计口径说明, 来源)

    def add(cat, k, basis, u, a, b, note, src):
        R.append((cat, k, basis, u, a, b, note, src))

    # ---------------- 一维
    add("水量", "产流总量（注入管网）", "一维", "m³", L1["inflow_m3"], L2["inflow_m3"], "各片 SWMM 连续性表的外部入流（净雨经单位线汇流后按节点注入）之和；两态应相同，差 0.002% 是分片数值噪声", s1d)
    add("水量", "洪水量（溢出井盖）", "一维", "m³", L1["flood_m3"], L1["flood_m3"] - c["diff_m3"],
        "Node Flooding Summary 各井累计溢流之和；全市模型冒出井盖即损失（ALLOW_PONDING NO）。L2 只在接塘的 15 块管网上取 L2 自己的数，其余取 L1（分片数值噪声记 0）", s1d)
    add("水量", "洪水量占产流", "一维", "%", round(L1["flood_m3"] / L1["inflow_m3"] * 100, 2), round((L1["flood_m3"] - c["diff_m3"]) / L1["inflow_m3"] * 100, 2), "洪水量 ÷ 产流总量", s1d)
    add("水量", "出水口排走", "一维", "m³", L1["outflow_m3"], L2["outflow_m3"], "各片连续性表的出流之和（244 个出水口）", s1d)
    add("水量", "窗口末管网与塘内滞水", "一维", "m³", L1["stored_end_m3"], L2["stored_end_m3"], "各片连续性表的末时蓄水之和（30 h 末）", s1d)
    add("水量", "峰值滞水", "一维", "m³", round(L1["peak_stored_m3"]), round(L2["peak_stored_m3"]), "全系统（管网 + 塘）同一时刻蓄水的最大值", s1d)
    add("水量", "接塘 15 块管网的洪水量", "一维", "m³", pn["L1_m3"], pn["L2_m3"], "只在接了塘的管网连通块上合计；全市 L1 → L2 的差值全部出在这里", s1d)
    add("水量", "落在客户 39 个汇水区之外的洪水量", "一维", "m³", G["outside_client_catch_L1_m3"], None, "按溢水井位置判；只算 L1", s1d)
    add("过程", "全市溢流速率峰值", "一维", "m³/s", round(float(sysq["L1"].max()), 1), round(float(sysq["L2"].max()), 1), "全系统溢流速率（逐 5 分钟）的最大值", s1t)
    add("过程", "全市溢流速率峰值时刻", "一维", "h", tq[int(sysq["L1"].argmax())], tq[int(sysq["L2"].argmax())], "从 0 h（降雨起点）算", s1t)
    add("过程", "系统排空时长（主网）", "一维", "h", L1["emptying_majority_h"], L2["emptying_majority_h"], "全系统蓄水从峰值落到「死水位 + 10%」所需时间（与 Parsons 报告口径不一定相同，报告没给定义）", s1d)
    add("溢水点", "有溢流的井（任何溢流）", "一维", "口", L1["n_flooded_nodes"], L2["n_flooded_nodes"], "Node Flooding Summary 里出现的井（30 h 窗口，不设门槛）；两态各自的原始计数，未按「不接塘的管网 L2 取 L1」处理，分片不同带来约 ±0.2% 的噪声，L1 / L2 差以下面逐井对比为准", s1d)
    add("溢水点", "溢水点（6 h 内累计 ≥ 1 m³）", "一维", "口", nsp["L1"], nsp["L2"], "逐井表的门槛；两态各自的原始计数（同上，有分片噪声）；6 h 版与 30 h 版累计溢流差约 1.2%", s1t)
    for k in ("不再溢流", "减少", "新增溢流", "增加"):
        add("溢水点", f"L2 相对 L1：{k}", "一维", "口", None, chg.get(k, 0), "逐井比 6 h 累计溢流：不再溢流 = L1 ≥ 1 m³ 而 L2 < 1 m³；减少 / 增加 = 差 ≥ 1 m³；不接塘的管网 L2 取 L1", s1t)
    add("能力", "管道满管容积", "一维", "m³", round(L1["storage_detail"]["pipe_m3"]), round(L2["storage_detail"]["pipe_m3"]), "Σ 断面积 × 管长（按几何算）；L2 多出的是接塘短管", s1d)
    add("能力", "塘库容（一维里的塘）", "一维", "m³", 0, round(L2["storage_detail"]["storage_m3"]),
        "一维里 17 座塘的蓄水曲线积分（PND_40、RZS_25/28/33/35 没有进水管不在一维）；含 Baniyas Lake 561,690 m³（台账为 0，按 DTM 轮廓能兜住的水取）", s1d)
    add("数值", "连续性误差（各片）", "一维", "%", f"{min(ce['L1']):.2f}–{max(ce['L1']):.2f}", f"{min(ce['L2']):.2f}–{max(ce['L2']):.2f}", "SWMM Flow Routing Continuity Error，8 片的范围", s1d)
    # ---------------- 一二维
    a1, a2 = TW["L1"]["acc_m3"], TW["L2"]["acc_m3"]
    add("水量", "管网溢流进地面（1D→2D）", "一二维", "m³", S1["flood_1d_to_2d_m3"], S2["flood_1d_to_2d_m3"], "6 h 内一维冒出井盖、交给二维的水量累计", s2d)
    add("水量", "地面水经井盖回管网（2D→1D）", "一二维", "m³", round(a1["2to1"]), round(a2["2to1"]), "井盖孔口 / 淹没孔口回流（Cd 0.6、DN600）累计", s2d)
    add("水量", "直接落在二维地面的净雨", "一二维", "m³", S1["rain_on_grid_m3"], S2["rain_on_grid_m3"], "无管网覆盖的地面（离最近井 > 46 m，872 km²）上 CN 82 净雨；两态相同", s2d)
    add("水量", "地面水流进塘", "一二维", "m³", S1["into_ponds_surface_m3"], S2["into_ponds_surface_m3"], "塘占地轮廓内的地面水直接进塘（塘满为止）", s2d)
    add("水量", "从计算域边界流出", "一二维", "m³", S1["boundary_out_m3"], S2["boundary_out_m3"], "二维水量对账的余项（自由出流边界）", s2d)
    add("水量", "6 h 末地面上的水", "一二维", "m³", S1["final_vol_m3"], S2["final_vol_m3"], "6 h 末二维计算域内水量（含直接落地的雨）", s2d)
    add("面积", "积水 ≥0.10 m 最大面积（全程最大）", "一二维", "ha", S1["max_area_ge_0p10_ha"], S2["max_area_ge_0p10_ha"], "各三角单元全程最大水深 ≥ 0.10 m 的单元面积之和（不同单元的最大可出现在不同时刻）", s2d)
    add("面积", "积水 ≥0.30 m 最大面积（全程最大）", "一二维", "ha", S1["max_area_ge_0p30_ha"], S2["max_area_ge_0p30_ha"], "同上，阈值 0.30 m", s2d)
    add("面积", "深水 ≥0.8 m 面积（全程最大）", "一二维", "ha", A["L1_ge_0p80_ha"], A["L2_ge_0p80_ha"], "同上，阈值 0.80 m", s2d)
    add("面积", "积水 ≥0.10 m 同一时刻最大面积", "一二维", "ha", S1["peak_wet_ge_0p10_ha"], S2["peak_wet_ge_0p10_ha"], "逐 5 分钟帧里 ≥ 0.10 m 面积的最大值（同一时刻）", s2d)
    add("面积", "该时刻", "一二维", "h", S1["peak_wet10_t_h"], S2["peak_wet10_t_h"], "从 0 h 算", s2d)
    add("面积", "6 h 末 ≥0.10 m 面积", "一二维", "ha", fr["L1"][-1]["wet10_ha"], fr["L2"][-1]["wet10_ha"], "最后一帧；是同一时刻最大面积的 94%，6 h 内没有退", s2d)
    add("面积", "曾经湿过的面积（≥0.02 m）", "一二维", "ha", S1["wet_ever_ha"], S2["wet_ever_ha"], "全程最大水深 ≥ 0.02 m（干湿阈值）；含直接落地的雨水膜，只作参考", s2d)
    add("水深", "最大水深", "一二维", "m", round(hmax["L1"][0], 2), round(hmax["L2"][0], 2), "全部三角单元全程最大水深的最大值", s2d)
    add("水深", "最大水深位置（纬度, 经度）", "一二维", "", f"{hmax['L1'][1]:.5f}, {hmax['L1'][2]:.5f}", f"{hmax['L2'][1]:.5f}, {hmax['L2'][2]:.5f}", "一处与塘无关的洼地", s2d)
    add("差值", "最大水深变浅 ≥ 5 cm 的面积", "一二维", "ha", None, A["shallower_ha"], "L2 − L1 全程最大水深 ≤ −0.05 m 的单元面积", s2d)
    add("差值", "其中在塘周边 1 km 内", "一二维", "ha", None, Z["shallower_within_1km_of_ponds_ha"], "台账塘轮廓外扩 1 km", s2d)
    add("差值", "最大水深变深 ≥ 5 cm 的面积", "一二维", "ha", None, A["deeper_ha"], "L2 − L1 ≥ +0.05 m；数值噪声量级", s2d)
    add("塘", "塘蓄住的水（6 h 末）", "一二维", "m³", 0, round(store), f"22 座塘 6 h 末蓄水之和；总库容 {cap:,.0f} m³", s2d)
    add("塘", "塘满度", "一二维", "%", None, round(store / cap * 100, 1), "6 h 末蓄水 ÷ 库容（库容：台账，Baniyas Lake 按 DTM）", s2d)
    add("塘", "灌满的塘 / 装不到三成的塘", "一二维", "座", None, f"{full} / {low}", "满度 ≥ 99% / < 30%", s2d)
    add("数值", "一维侧水量差", "一二维", "%", "−0.7", "−0.7", "交给二维的水 vs SWMM 自己统计的溢流", s2d)
    add("计算", "模拟时长 / 网格", "一二维", "", "0 → 6 h；632 万三角单元（平均 195 m²）", "同左", "见 计算设置.md", s2d)

    def chg_(a, b):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a:
            return b - a, round((b - a) / a * 100, 2)
        return None, None
    rows = [[cat, k, basis, u, a, b, *chg_(a, b), note, src] for cat, k, basis, u, a, b, note, src in R]
    hdr = ["类别", "指标", "口径", "单位", "L1", "L2", "L2−L1", "变化 %", "统计口径说明", "数据来源"]
    out = D / "对比_缺口分析"
    with open(out / "全市L1L2统计值.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh); w.writerow(hdr); w.writerows(rows)
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    wb = Workbook(); ws = wb.active; ws.title = "统计值"
    ws.append(hdr)
    for r in rows:
        ws.append(r)
    for cl in ws[1]:
        cl.font = Font(bold=True)
    for col, wd in zip("ABCDEFGHIJ", (8, 34, 8, 7, 16, 16, 14, 9, 70, 34)):
        ws.column_dimensions[col].width = wd
    for row in ws.iter_rows(min_row=2):
        row[8].alignment = Alignment(wrap_text=True, vertical="top")
    w2 = wb.create_sheet("口径总说明")
    for t in ["61 mm / 2 h；L1 = 既有管网，L2 = 同一张网上加已建成塘（一维 17 座、一二维 22 座）",
              "一维：SWMM 5.2.4，全市分 8 片，产流统一 SCS CN 82 + 单位线，演算 30 h（逐时过程那一版 6 h）",
              "一二维：SWMM 5.2.4 + ANUGA 4.0.1 双向耦合，整网一个模型，产流用客户分区 CN / Tc，模拟 6 h，632 万三角单元",
              "两种口径的产流设置不同：一维与一二维的数不要互相相减，只看各自内部的 L1 → L2",
              "面积都按三角单元真实面积加权；积水面积用 ≥0.10 / ≥0.30 m（直接落地的雨会在全域形成 2–3 cm 水膜，按 0.02 m 会算成淹没）",
              "塘在模型里是 0.30 m 深的平底蓄水节点（面积 = 库容 ÷ 0.3）：库容对，塘内水位与顶托不真实",
              "完整输入参数见 计算设置.md"]:
        w2.append([t])
    wb.save(out / "全市L1L2统计值.xlsx")
    print(f"→ {out / '全市L1L2统计值.xlsx'}（{len(rows)} 项）")
    for r in rows:
        print("  ", r[2], r[1], r[4], r[5], r[7] if r[7] is not None else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
