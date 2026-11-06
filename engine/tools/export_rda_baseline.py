# -*- coding: utf-8 -*-
"""RD-A 9 场雨 × L1 / L2 / L2+planned 的一二维结果交付（2026-10-27 用户：「针对 RD-A 区域的 9 种降雨模式的 L1、L2、L2+planned 的一二维水动力模型的
计算结果……结果里必须带有模型运算出的积水点的信息」）。

复用 tools/export_delivery_rda.py 的各部件（同一格式），只出三个基线状态，不含 L2+suggested：
  <雨>/<状态>/一二维/      .npz（二维结果）、.inp、耦合 .rpt、耦合交换记录、二维设置（.sww 只有当时保留了的才有）
  <雨>/<状态>/水深栅格/    最大水深、逐帧水深 GeoTIFF（缺的先用 tools/npz_to_tif.py 生成）
  <雨>/<状态>/积水点/      两档阈值（0.15 / 0.30 m）的积水点清单、面、点、与客户热点的对应、评估剔除区标记、L3 承接与派单
  <雨>/<状态>/设施/        本状态全部设施
  <雨>/公共/               计算域、客户热点、DTM、评估剔除区、降雨过程、判据评估
根目录：README.md、RD-A_9场雨_L1_L2_L2planned_结果汇总.xlsx（27 个算例一张表）
只读计算结果，不改任何计算。
用法：python -X utf8 tools/export_rda_baseline.py --out "out/交付/RDA_9场雨_L1_L2_L2planned_20261027" [--storms S33D1,...]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import export_delivery_rda as X  # noqa: E402
from swbrain import ponding  # noqa: E402

STORMS = ["S33D1", "S33D2", "S33D24", "S61D1", "S61D2", "S61D24", "S96D1", "S96D2", "S96D24"]
STATES = ["L1", "L2", "L2P"]
ZH = {"L1": "L1 现状", "L2": "L2 雨前预置泵", "L2P": "L2+planned 客户计划"}


def label(storm):
    mm, h = storm[1:].split("D")
    return f"{mm}mm{h}h"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--storms", default=",".join(STORMS))
    a = ap.parse_args()
    root = pathlib.Path(a.out); root.mkdir(parents=True, exist_ok=True)
    rows, readme_rows = [], []
    for storm in a.storms.split(","):
        run = f"RDA_{storm}"; out = root / f"RDA_{label(storm)}"; out.mkdir(parents=True, exist_ok=True)
        miss = [st for st in STATES if not (X.C2D / "tif" / f"{run}_{st}_depth_max.tif").exists()]
        if miss:
            print(storm, "生成水深栅格", miss, flush=True)
            subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools/npz_to_tif.py"), run, *miss], check=True, stdout=subprocess.DEVNULL)
        stats = json.loads((ROOT / f"console/data/flood2d_rda_{storm.lower()}/rda_stats.json").read_text(encoding="utf-8"))
        L3 = json.loads((ROOT / f"console/data/flood2d_rda_{storm.lower()}/l3.json").read_text(encoding="utf-8"))
        dirs = {s: out / X.NM[s] for s in STATES}
        ponding.export_delivery(run, dirs, out, pond_scope="state")
        ex = X.exclude_zones()
        for st, d in dirs.items():
            X.model_files(run, st, d)
            X.ponding_extra(st, d, L3, ex)
            X.facilities(run, st, d, stats["facilities"])
        h, _ = X.common(run, storm, out)
        sww = [st for st in STATES if (X.C2D / "anuga_out" / f"{run}_{st}.sww").exists()]
        if sww:                                                  # 飞渡 DTS：只有保留了 .sww 的状态能出
            from swbrain import dts_h2d
            dts_h2d.export_delivery(run, X.C2D / "anuga_out", {s: dirs[s] for s in sww}, out, names={s: ZH[s] for s in sww})
        E = json.loads((X.OPT / f"eval2d_{storm}_ex.json").read_text(encoding="utf-8"))["states"]
        for st in STATES:
            e = E[st]; d = dirs[st]
            P = {dep: json.loads((d / "积水点" / f"ponding_points_d{dep:.2f}.geojson").read_text(encoding="utf-8"))["features"] for dep in ponding.DELIVERY_DEPTHS}
            l3 = L3["states"].get(st, {})
            isl = (l3.get("dispatch") or {}).get("island", {}).get("counts", {}); adm = (l3.get("dispatch") or {}).get("adm", {}).get("counts", {})
            rows.append({"降雨": f"{h['rain_mm']:.0f} mm / {storm.split('D')[1]} h", "运行名": run, "状态": ZH[st],
                         "5 分钟峰值雨强 mm/h": h["peak_mmph"], "模拟时长 h": h["sim_h"],
                         "全域 ≥0.30 m 面积 ha": e["domain"]["ge030_ha"], "峰值地面积水量 m³": e["domain"].get("peak_vol_m3"),
                         "积水点 ≥0.15 m 个数": len(P[0.15]), "积水点 ≥0.15 m 峰值积水量合计 m³": round(sum((f["properties"].get("vol_peak_m3") or 0) for f in P[0.15])),
                         "积水点 ≥0.30 m 个数": len(P[0.30]), "客户热点四项全过（/11）": sum(all(v["pass"].values()) for v in e["hotspots"].values()),
                         "L3（仅 Island）按时 / 超时 / 未派": f"{isl.get('L3 按时退干', 0)} / {isl.get('L3 已派、超时', 0)} / {isl.get('L3 未派', 0)}",
                         "L3（ADM 全部驻地）按时 / 超时 / 未派": f"{adm.get('L3 按时退干', 0)} / {adm.get('L3 已派、超时', 0)} / {adm.get('L3 未派', 0)}",
                         "自然退干点数": isl.get("自然退干", 0), ".sww / 飞渡DTS": "有" if st in sww else "无（未保留 .sww）",
                         "文件夹": f"RDA_{label(storm)}/{X.NM[st]}"})
        readme_rows.append(f"| `RDA_{label(storm)}/` | {h['rain_mm']:.0f} mm / {storm.split('D')[1]} h | {h['peak_mmph']} | {h['sim_h']} | {'、'.join(sww) or '无'} |")
        print("→", out, flush=True)
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "27个算例汇总"
    ws.append(list(rows[0])); [ws.append(list(r.values())) for r in rows]
    for c in ws.columns:
        ws.column_dimensions[c[0].column_letter].width = max(10, min(36, max(len(str(x.value or "")) for x in c) + 2))
    ws2 = wb.create_sheet("字段说明")
    for k, v in (("全域 ≥0.30 m 面积 ha", "计算域内全程最大水深 ≥0.30 m 的面积，已扣评估剔除区（DTM 填 0、私人深坑、塘占地）"),
                 ("峰值地面积水量 m³", "地面积水量（水深 × 面积，逐帧合计）的最大值"),
                 ("积水点", "从二维结果按阈值动态计算：最大水深 ≥ 阈值、连片 ≥100 m²、超阈值 ≥0.5 h，本状态塘占地不算；明细见各状态 积水点/ 文件夹"),
                 ("客户热点四项全过", "判据 A–D（热点 200 m 内：A 无 ≥0.30 m 积水；B ≥0.15 m 面积×时间 ≤ L2+planned 的 10 %；C 雨停后 6 h 内退到 0.10 m 以下；D 全域比 L2+planned 加深 ≥5 cm 的面积 ≤0.05 ha）；以 L2+planned 为基线，所以 L2+planned 的 B、D 天然满足"),
                 ("L3", "残余积水点交给第三道防线调度求解器的结果；行程时间按 ArcGIS Online 卡车路线实测校正（本地路网 × 1.70）")):
        ws2.append([k, v])
    xl = root / "RD-A_9场雨_L1_L2_L2planned_结果汇总.xlsx"; wb.save(xl)
    (root / "README.md").write_text(README.format(gen=time.strftime("%Y-%m-%d %H:%M"), storm_rows="\n".join(readme_rows)), encoding="utf-8")
    print("→", xl)


README = """# RD-A 9 场雨 · L1 / L2 / L2+planned 一二维结果（含二维模拟积水点）

生成：{gen}。只含三个基线状态：L1 现状、L2 雨前预置泵、L2+planned 客户计划（不含 L2+suggested）。27 个算例的汇总见 `RD-A_9场雨_L1_L2_L2planned_结果汇总.xlsx`。

## 模型与精度

- **一维**：SWMM 5.2，管网来自 AECOM 的 RD-A SewerGEMS 模型（导出 inp 换算公制）；产流按土地利用的复合 CN 法（不透水部分 CN 95，透水部分用客户 CN）。
- **二维**：ANUGA，非结构三角网，**网格参数 15 m**：三角形面积上限 15² / 3 = 75 m²，计算域 3.18 km² 共 **65,210 个三角单元**（平均每个约 49 m²）；单元高程直接取 **5 m DTM** 原值；一二维每 60 s 交换一次。
- **输出**：每 5 分钟一帧水深与流速；水深栅格按 15 m 输出（UTM 40N，EPSG:32640）。
- **数据口径**：管网是 AECOM 顾问模型，不是用全市数据治理后的 DMT 重建的。治理前后对比（`docs/RD-A_治理前后一二维对比分析_终稿_20261026.md`，对照 ⑤）表明治理后的管网与 AECOM 管网一致，产流口径也相同，但形式上不是"用治理后数据建的模"。
- **降雨**：客户 Mussafah 模型「10 年一遇含 CC」同历时雨型等比放大到 33 / 61 / 96 mm，历时 1 h、2 h、24 h。雨停后再算 10 h。
- 模型**未率定**（缺观测数据），结果适合方案之间比较。

## 文件夹

| 文件夹 | 降雨 | 5 分钟峰值雨强 mm/h | 模拟时长 h | 有 .sww / 飞渡 DTS 的状态 |
|---|---|---|---|---|
{storm_rows}

每场雨下有 `L1_现状/`、`L2_雨前预置泵/`、`L2+planned_客户计划/` 和 `公共/`。各状态文件夹里：

| 子文件夹 | 内容 |
|---|---|
| `一二维/` | `_二维结果.npz`（单元形心坐标、面积、高程、最大水深、每 5 分钟水深帧、流速）、`.inp`（SWMM 管网）、`_一二维耦合_SWMM报告.rpt`、`_耦合交换记录.json`、`_二维设置.json`；`.sww` 只有当时保留了的才有 |
| `水深栅格/` | `_最大水深.tif`、`_逐帧水深.tif`（多波段，每波段一个时刻，时刻见同名 json） |
| `积水点/` | **二维模拟积水点**，两档阈值（0.15 m、0.30 m），见下 |
| `设施/` | 本状态全部设施（GeoJSON + 清单） |

## 积水点（从二维结果按阈值动态计算）

1. 数据：`一二维/*_二维结果.npz` 里每个单元的全程最大水深和每 5 分钟水深帧。
2. 阈值：水深 0.15 m（`d0.15`）和 0.30 m（`d0.30`）两档；连片面积 ≥100 m²；超阈值时长 ≥0.5 h。
3. 做法：最大水深 ≥ 阈值的单元连成片 → 扣本状态塘的占地 → 去掉面积、时长不够的 → 编号 `SIM-<状态>-<序号>`（按峰值积水量从大到小），记最大水深、面积、峰值积水量、峰值时刻、超阈值时长、是否退干、200 m 内客户热点。
4. 文件：`积水点清单_d*.csv`（中文表头，Excel 直接打开）、`ponding_d*.csv`（英文字段）、`ponding_areas_d*.geojson`（积水范围面）、`ponding_points_d*.geojson`（最深点）、`ponding_near_hotspots_d*.csv`、`积水点_评估剔除区标记_d*.csv`、`L3承接与派单.csv / .geojson`、`未进入L3清单的积水点.csv`。
5. 用别的阈值重算：`python -m swbrain.ponding --name RDA_<雨> --states L1 --depth 0.2 --min-area 200 --min-dur 1 --pond-scope state --export`。
6. 没有 `.sww` 的算例，积水片按单元近邻判断连片（`adjacency = approx`），有 `.sww` 的按真实三角网相邻；积水总量相同，个别斜角相接的片可能一边算一片、一边算两片。
"""


if __name__ == "__main__":
    main()
