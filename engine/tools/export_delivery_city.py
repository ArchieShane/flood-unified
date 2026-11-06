# -*- coding: utf-8 -*-
"""全市 L1 / L2 交付目录：out/交付/全市_61mm2h/。一维（8 片 × 2 态）与一二维（30 m、1,234 km²）的结果、对比表与说明。

用法：python tools/export_delivery_city.py [--no-big]   （--no-big 不拷二维水深帧 npz 与耦合报告，省约 2 GB）
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
D = ROOT / "out" / "交付" / "全市_61mm2h"
FR = ROOT / "console" / "data" / "flood2d_city2d"
STATES = {"L1": ("L1_既有管网", "既有管网，没有塘"), "L2": ("L2_加22座已建成塘", "同一张网上加 22 座已建成塘（客户台账）")}


def cp(src, dst, done):
    src = pathlib.Path(src)
    if not src.exists():
        print(f"  ! 缺 {src}"); return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst); done.append(dst)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--no-big", action="store_true"); a = ap.parse_args()
    ST = json.loads((FR / "stats.json").read_text(encoding="utf-8"))
    MF = json.loads((FR / "manifest.json").read_text(encoding="utf-8"))
    Z = json.loads((ROOT / "out" / "city2d_zones_R61D2.json").read_text(encoding="utf-8"))
    G = json.loads((ROOT / "out" / "city_gap_v2.json").read_text(encoding="utf-8"))
    done = []
    cp(C2D / "dem_City2D.tif", D / "公共" / "二维地形_30m.tif", done)
    for st, (folder, desc) in STATES.items():
        F = D / folder; print(folder)
        # 一维：全市 8 片（v2 = 报告与差值用的那一版；v2ts = 同设置、存逐时过程，系统逐时播放用）
        for i in range(1, 9):
            cp(WORK / f"City_{st}_v2_p{i}.inp", F / "一维" / "模型" / f"{st}_片{i}.inp", done)
            cp(WORK / f"City_{st}_v2_p{i}.rpt", F / "一维" / "报告" / f"{st}_片{i}.rpt", done)
            if not a.no_big:
                cp(WORK / f"City_{st}_v2ts_p{i}.out", F / "一维" / "逐时过程" / f"{st}_片{i}.out", done)
        cp(WORK / f"City2D_{st}.inp", F / "一二维" / "耦合的一维" / f"{st}_耦合一维模型.inp", done)
        cp(C2D / f"twoway_City2D_{st}.json", F / "一二维" / f"{st}_耦合运行记录.json", done)
        if not a.no_big:
            cp(C2D / f"City2D_{st}_2d.npz", F / "一二维" / f"{st}_二维水深帧与最大水深.npz", done)
            cp(C2D / f"City2D_{st}_2way.rpt", F / "一二维" / "耦合的一维" / f"{st}_耦合一维报告.rpt", done)
            # 标准格式（2026-09-28 补）：.sww 由 tools/npz_to_sww.py 从 npz 重建（只有水位、地面高程，无流速）
            cp(C2D / "anuga_out" / f"City2D_{st}.sww", F / "一二维" / f"{st}_二维水深时序.sww", done)
        # GeoTIFF（tools/npz_to_tif.py）：30 m、EPSG:32640
        cp(C2D / "tif" / f"City2D_{st}_depth_frames.tif", F / "一二维" / "GIS" / f"{st}_水深逐帧_72波段.tif", done)
        cp(C2D / "tif" / f"City2D_{st}_depth_frames.json", F / "一二维" / "GIS" / f"{st}_水深逐帧_72波段_波段时刻.json", done)
        cp(C2D / "tif" / f"City2D_{st}_depth_max.tif", F / "一二维" / "GIS" / f"{st}_全程最大水深.tif", done)
        s = next(x for x in MF["states"] if x["state"] == st)
        for f in s["frames"]:
            cp(FR / f["png"], F / "一二维" / "逐帧水深图" / f"t{f['t_h']:05.2f}h.png", done)
        cp(FR / s["max_png"], F / "一二维" / "全程最大水深.png", done)
        (F / "一二维" / "逐帧水深图" / "范围.json").write_text(json.dumps({"bounds_latlon": s["bounds"], "crs": "EPSG:4326",
            "出图阈值_m": MF.get("show_m"), "色标上限_m": MF.get("vmax_m")}, ensure_ascii=False, indent=1), encoding="utf-8")
        with open(F / "一二维" / f"{st}_积水面积时序.csv", "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh); w.writerow(["时刻_h", "积水≥0.02m面积_ha", "积水≥0.10m面积_ha", "至此最大水深_m"])
            for f in s["frames"]:
                w.writerow([f["t_h"], f["wet_ha"], f["wet10_ha"], f["dmax_m"]])
        if st == "L2":
            with open(F / "一二维" / "塘蓄水.csv", "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh); w.writerow(["塘", "台账库容_m3", "6h末蓄水_m3", "满度_%", "其中地面漫流进塘_m3", "溢出_m3"])
                for p in sorted(ST["L2"]["ponds"], key=lambda p: -(p.get("vol_design_m3") or 0)):
                    w.writerow([p["unitid"], round(p.get("vol_design_m3") or 0), round(p.get("vol_end_m3") or 0), round(p.get("fill_pct") or 0, 1),
                                round(p.get("surface_captured_m3") or 0), round(p.get("overflow_m3") or 0)])
        r, zz = ST[st], Z["all"]
        lines = [f"# {st} · 全市 · 61 mm / 2 h", "", desc, "", "| 指标（一二维，计算域 1,234 km²） | 数值 |", "|---|---|",
                 f"| 积水 ≥0.10 m 最大面积 | {r['max_area_ge_0p10_ha']:,.0f} ha |", f"| 积水 ≥0.30 m 最大面积 | {r['max_area_ge_0p30_ha']:,.1f} ha |",
                 f"| 深水 ≥0.8 m 面积 | {zz[st + '_ge_0p80_ha']:,.1f} ha |", f"| 最大水深 | {r['max_depth_m']:.2f} m |",
                 f"| 管网溢流进地面 | {r['flood_1d_to_2d_m3']:,.0f} m³ |", f"| 6 h 末地面上的水 | {r['final_vol_m3']:,.0f} m³ |",
                 f"| 地面漫流进塘 | {r['into_ponds_surface_m3']:,.0f} m³ |", "",
                 "## 文件", "", "- `一维/模型/`、`一维/报告/`：全市一维 8 片（SWMM 5.2，客户 GDB 全量重建）；`一维/逐时过程/`：同设置存了逐时结果的 .out（系统逐时播放用）",
                 "- `一维/溢水点/`：每个溢水点（6 h 内溢流 ≥ 1 m³ 的井）的汇总表，与每 5 分钟的溢流流量、井内水深逐时表（tools/city_node_series.py）",
                 "- `一二维/耦合的一维/`：一二维耦合时一维侧的整网模型与报告",
                 "- `一二维/*_二维水深时序.sww`：ANUGA 标准格式，每 5 分钟一帧；**只有水位与地面高程，没有流速**（计算时为省盘关了 .sww、只存了形心水深，事后按同一张三角网重建写出；xmomentum / ymomentum 全为 0）",
                 "- `一二维/GIS/`：30 m GeoTIFF（EPSG:32640）——72 波段逐帧水深（波段时刻见同名 .json）、全程最大水深；每格取格内三角单元的最大值，用栅格数面积会偏大",
                 "- `一二维/*_耦合运行记录.json`：逐时刻积水面积、最大水深、1D↔2D 交换水量、水量对账",
                 "- `一二维/*_二维水深帧与最大水深.npz`：每 5 分钟一帧的三角单元水深、全程最大水深、单元面积与形心坐标（UTM 40N）",
                 "- `一二维/逐帧水深图/`、`全程最大水深.png`、`*_积水面积时序.csv`：系统页面播放用的就是这些"]
        (F / "摘要.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # 对比
    Gd = D / "对比_缺口分析"; Gd.mkdir(parents=True, exist_ok=True)
    cp(FR / "diff.png", Gd / "L2减L1_最大水深差.png", done)
    (Gd / "L2减L1_最大水深差_范围.json").write_text((FR / "diff.json").read_text(encoding="utf-8"), encoding="utf-8")
    cp(ROOT / "out" / "city2d_zones_R61D2.json", Gd / "分区统计_客户汇水区与塘周边.json", done)
    cp(ROOT / "out" / "city_gap_v2.json", Gd / "一维_全市L1L2差值.json", done)
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook(); ws = wb.active; ws.title = "一二维对照"
    keys = [("积水≥0.10m最大面积", "max_area_ge_0p10_ha", "ha"), ("积水≥0.30m最大面积", "max_area_ge_0p30_ha", "ha"),
            ("最大水深", "max_depth_m", "m"), ("管网溢流进地面", "flood_1d_to_2d_m3", "m³"), ("6h末地面上的水", "final_vol_m3", "m³"),
            ("地面漫流进塘", "into_ponds_surface_m3", "m³")]
    ws.append(["指标", "单位", "L1", "L2", "L2−L1", "%"])
    for zh, k, u in keys:
        x, y = ST["L1"][k], ST["L2"][k]
        ws.append([zh, u, x, y, y - x, round((y - x) / x * 100, 2) if x else None])
    ws.append(["深水≥0.8m面积", "ha", Z["all"]["L1_ge_0p80_ha"], Z["all"]["L2_ge_0p80_ha"], round(Z["all"]["L2_ge_0p80_ha"] - Z["all"]["L1_ge_0p80_ha"], 1),
               round((Z["all"]["L2_ge_0p80_ha"] / Z["all"]["L1_ge_0p80_ha"] - 1) * 100, 2)])
    c = G["city"]
    ws.append([]); ws.append(["一维（SWMM，8 片 × 2 态）"])
    ws.append(["洪水量（溢出井盖）", "m³", c["L1"]["flood_m3"], c["L1"]["flood_m3"] - c["diff_m3"], -c["diff_m3"], -round(c["diff_pct"], 2)])
    for sh, rows, cols in (("塘周边1km", Z["ponds"], ("unitid", "district")), ("客户汇水区", Z["catchments"], ("label",))):
        w2 = wb.create_sheet(sh)
        hdr = list(cols) + ["面积_ha", "L1≥0.10_ha", "L2≥0.10_ha", "L1≥0.30_ha", "L2≥0.30_ha", "L1≥0.80_ha", "L2≥0.80_ha", "变浅≥5cm_ha", "变深≥5cm_ha", "L1最大水深_m", "L2最大水深_m"]
        w2.append(hdr)
        for r in rows:
            w2.append([r.get(k) for k in cols] + [r[k] for k in ("area_ha", "L1_ge_0p10_ha", "L2_ge_0p10_ha", "L1_ge_0p30_ha", "L2_ge_0p30_ha",
                                                              "L1_ge_0p80_ha", "L2_ge_0p80_ha", "shallower_ha", "deeper_ha", "L1_max_m", "L2_max_m")])
    w3 = wb.create_sheet("口径说明")
    for n in ["61 mm / 2 h；二维 30 m 网格 632 万三角单元，雨直接下在二维地面上，模拟 6 h；一维 SWMM 与二维 ANUGA 双向耦合",
              "面积按三角单元真实面积加权；积水面积按 ≥0.10 / ≥0.30 m 统计（按 0.02 m 会把雨水膜算成淹没）",
              "最大水深 3.82 m 两态相同，是一处与塘无关的洼地",
              "变浅 / 变深：L2 − L1 全程最大水深 |Δ| ≥ 5 cm；塘周边 = 台账塘轮廓外扩 1 km（相邻塘的周边会重叠）",
              "水量对账：一维侧交给二维的水与 SWMM 溢流差 −0.7%；二维余项是从计算域边界流出的水（约 207 万 m³）",
              "6 h 末 ≥0.10 m 积水面积还在峰值的 94%，退水要把二维算长才有数",
              "台账与模型的塘不完全一致（2026-09-28 核）：① Baniyas Lake 台账库容为 0，模型按 5 m DTM 算「塘轮廓内现状地形能兜住的水」取 56.2 万 m³，一维、一二维都在；② 一维里只有 17 座塘——PND_40、RZS_25、RZS_28、RZS_33、RZS_35 没有进水管，一维不含；一二维 22 座都在（地面水可直接进塘；PND_40 取 787 m³，台账深 5.87 m、库容字段 129 m³，待核）；③ 塘在模型里是深 0.30 m 的平底蓄水节点、面积 = 库容 ÷ 0.3：库容对，塘内水位与对管网的顶托不真实。塘总库容：台账 22 座 194 万 m³（Baniyas Lake 记 0）、一维 17 座 245 万 m³、一二维 22 座 251 万 m³",
              "塘接为无出口管网的末端是按位置与标高推断的，台账没有这么写"]:
        w3.append([n])
    for w in wb.worksheets:
        for cl in w[1]:
            cl.font = Font(bold=True)
    wb.save(Gd / "全市L1L2对照.xlsx"); done.append(Gd / "全市L1L2对照.xlsx")
    readme = ["# 全市 · 61 mm / 2 h · L1 / L2", "",
              "- `L1_既有管网/`、`L2_加22座已建成塘/`：各态的一维（8 片模型、报告、逐时过程）与一二维结果（耦合模型与报告、运行记录、水深帧、逐帧图、积水面积时序；L2 另有塘蓄水表）",
              "- `对比_缺口分析/全市L1L2统计值.xlsx`（及 .csv）：全市 L1 / L2 典型统计值 43 项，每项注明口径（一维 / 一二维）、统计口径说明与数据来源（tools/city_stats.py）",
              "- `对比_缺口分析/`：一二维对照表（全市、塘周边 1 km、客户汇水区三张）、L2 − L1 最大水深差图、一维全市差值、溢水点_L1对L2.csv（逐井累计溢流、峰值、时长对比）",
              "- `公共/二维地形_30m.tif`：二维计算用的地形", "",
              "结论：从全市看已建成塘的作用很小（积水 ≥0.30 m 面积 −2.8%、管网溢流 −2.0%）；在塘周边 1 km 内，≥0.30 m 面积普遍少四到七成。",
              "在线报告：https://claude.ai/artifact/NjMBR3ueF7RBUGYQJGm9bY ；系统：防线 2+ → 全市一二维。"]
    (D / "readme.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    tot = sum(p.stat().st_size for p in done if p.exists())
    print(f"→ {D}：{len(done)} 个文件，{tot / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
