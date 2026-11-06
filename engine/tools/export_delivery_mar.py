# -*- coding: utf-8 -*-
"""Madinat Al Riyad 一二维 L1 / L2 交付目录：out/交付/MadinatAlRiyad_61mm2h/（结构同全市）。

用法：python tools/export_delivery_mar.py [--no-big]
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
N = "Pilot_MAR2"
D = ROOT / "out" / "交付" / "MadinatAlRiyad_61mm2h"
FR = ROOT / "console" / "data" / "flood2d_pilot"
STATES = {"L1": ("L1_既有管网", "既有管网（从客户 GDB 重建），没有塘"), "L2": ("L2_加20座已建成塘", "同一张网上加 20 座已建成塘（客户台账，194 万 m³）")}


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
    assert ST.get("name") == N, f"flood2d_pilot 不是 {N} 的结果"
    done = []
    cp(C2D / f"dem_{N}.tif", D / "公共" / "二维地形_30m.tif", done)
    for st, (folder, desc) in STATES.items():
        F = D / folder; E = F / "一二维"; print(folder)
        cp(WORK / f"{N}_{st}.inp", E / "耦合的一维" / f"{st}_耦合一维模型.inp", done)
        cp(C2D / f"{N}_{st}_2way.rpt", E / "耦合的一维" / f"{st}_耦合一维报告.rpt", done)
        cp(C2D / f"twoway_{N}_{st}.json", E / f"{st}_耦合运行记录.json", done)
        if not a.no_big:
            cp(C2D / f"{N}_{st}_2d.npz", E / f"{st}_二维水深帧与最大水深.npz", done)
            cp(C2D / "anuga_out" / f"{N}_{st}.sww", E / f"{st}_二维水深时序.sww", done)
        cp(C2D / "tif" / f"{N}_{st}_depth_frames.tif", E / "GIS" / f"{st}_水深逐帧_72波段.tif", done)
        cp(C2D / "tif" / f"{N}_{st}_depth_frames.json", E / "GIS" / f"{st}_水深逐帧_72波段_波段时刻.json", done)
        cp(C2D / "tif" / f"{N}_{st}_depth_max.tif", E / "GIS" / f"{st}_全程最大水深.tif", done)
        s = next(x for x in MF["states"] if x["state"] == st)
        for f in s["frames"]:
            cp(FR / f["png"], E / "逐帧水深图" / f"t{f['t_h']:05.2f}h.png", done)
        cp(FR / s["max_png"], E / "全程最大水深.png", done)
        (E / "逐帧水深图" / "范围.json").write_text(json.dumps({"bounds_latlon": s["bounds"], "crs": "EPSG:4326",
            "出图阈值_m": MF.get("show_m"), "色标上限_m": MF.get("vmax_m")}, ensure_ascii=False, indent=1), encoding="utf-8")
        with open(E / f"{st}_积水面积时序.csv", "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh); w.writerow(["时刻_h", "积水≥0.02m面积_ha", "积水≥0.10m面积_ha", "至此最大水深_m"])
            for f in s["frames"]:
                w.writerow([f["t_h"], f["wet_ha"], f["wet10_ha"], f["dmax_m"]])
        if st == "L2":
            with open(E / "塘蓄水.csv", "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh); w.writerow(["塘", "台账库容_m3", "6h末蓄水_m3", "满度_%", "其中地面漫流进塘_m3", "溢出_m3"])
                for p in sorted(ST["L2"]["ponds"], key=lambda p: -(p.get("vol_design_m3") or 0)):
                    w.writerow([p["unitid"], round(p.get("vol_design_m3") or 0), round(p.get("vol_end_m3") or 0), round(p.get("fill_pct") or 0, 1),
                                round(p.get("surface_captured_m3") or 0), round(p.get("overflow_m3") or 0)])
        r = ST[st]
        (F / "摘要.md").write_text("\n".join([
            f"# {st} · Madinat Al Riyad · 61 mm / 2 h", "", desc, "", "| 指标（一二维，计算域 385 km²） | 数值 |", "|---|---|",
            f"| 积水 ≥0.10 m 最大面积 | {r['max_area_ge_0p10_ha']:,.0f} ha |", f"| 积水 ≥0.30 m 最大面积 | {r['max_area_ge_0p30_ha']:,.1f} ha |",
            f"| 最大水深 | {r['max_depth_m']:.2f} m |", f"| 管网溢流进地面 | {r['flood_1d_to_2d_m3']:,.0f} m³ |",
            f"| 6 h 末地面上的水 | {r['final_vol_m3']:,.0f} m³ |", f"| 地面漫流进塘 | {r['into_ponds_surface_m3']:,.0f} m³ |", "",
            "## 文件", "",
            "- `一二维/*_二维水深时序.sww`：ANUGA 标准格式，每 5 分钟一帧；**只有水位与地面高程，没有流速**（计算时为省盘关了 .sww，事后按同一张三角网重建写出；xmomentum / ymomentum 全为 0）",
            "- `一二维/GIS/`：30 m GeoTIFF（EPSG:32640）——72 波段逐帧水深、全程最大水深；每格取格内三角单元的最大值，用栅格数面积会偏大",
            "- `一二维/*_二维水深帧与最大水深.npz`：每 5 分钟一帧的三角单元形心水深、全程最大水深、单元面积与形心坐标（UTM 40N）",
            "- `一二维/耦合的一维/`：耦合时一维侧的模型与报告；`*_耦合运行记录.json`：逐时刻积水面积、交换水量、水量对账",
            "- `一二维/逐帧水深图/`、`全程最大水深.png`、`*_积水面积时序.csv`：系统页面播放用的就是这些"]) + "\n", encoding="utf-8")
    Gd = D / "对比_缺口分析"; Gd.mkdir(parents=True, exist_ok=True)
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook(); ws = wb.active; ws.title = "一二维对照"
    ws.append(["指标", "单位", "L1", "L2", "L2−L1", "%"])
    for zh, k, u in (("积水≥0.10m最大面积", "max_area_ge_0p10_ha", "ha"), ("积水≥0.30m最大面积", "max_area_ge_0p30_ha", "ha"), ("最大水深", "max_depth_m", "m"),
                     ("管网溢流进地面", "flood_1d_to_2d_m3", "m³"), ("6h末地面上的水", "final_vol_m3", "m³"), ("地面漫流进塘", "into_ponds_surface_m3", "m³")):
        x, y = ST["L1"][k], ST["L2"][k]
        ws.append([zh, u, x, y, y - x, round((y - x) / x * 100, 2) if x else None])
    w3 = wb.create_sheet("口径说明")
    for n in ["61 mm / 2 h；计算域按水文边界划（20 座塘的地表汇水区 ∪ 管网服务范围，外扩 300 m，385 km²）；30 m 网格；模拟 6 h；SWMM 与 ANUGA 双向耦合",
              "积水面积按 ≥0.10 / ≥0.30 m 统计（64% 的地面直接在二维上下雨，按 0.02 m 会把雨水膜算成淹没）",
              "塘接为无出口管网的末端是按位置与标高推断的，台账没有这么写",
              "PND_38、RZS_18 的汇水区到 25 km 窗口仍未收敛，这两座塘的来水是下限"] + list(ST.get("inferred") or []):
        w3.append([n])
    for w in wb.worksheets:
        for c in w[1]:
            c.font = Font(bold=True)
    wb.save(Gd / "L1L2对照.xlsx"); done.append(Gd / "L1L2对照.xlsx")
    (D / "readme.md").write_text("\n".join([
        "# Madinat Al Riyad · 61 mm / 2 h · 一二维 L1 / L2", "",
        "- `L1_既有管网/`、`L2_加20座已建成塘/`：各态的一二维结果（.sww、GeoTIFF、水深帧 npz、逐帧图、积水面积时序、耦合的一维模型与报告；L2 另有塘蓄水表）",
        "- `对比_缺口分析/L1L2对照.xlsx`", "- `公共/二维地形_30m.tif`", "",
        f"结论：L2 比 L1 管网溢流少 {abs(ST['gap_L1_to_L2']['flood_1d_to_2d_m3']['pct']):.1f}%，最大水深低 {abs(ST['gap_L1_to_L2']['max_depth_m']['pct']):.1f}%；"
        f"20 座塘的水有六成来自地面漫流。", "在线报告：https://claude.ai/artifact/NjMBR3ueF7RBUGYQJGm9bY（「一二维试跑：Madinat Al Riyad」一节）"]) + "\n", encoding="utf-8")
    tot = sum(p.stat().st_size for p in done if p.exists())
    print(f"→ {D}：{len(done)} 个文件，{tot / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
