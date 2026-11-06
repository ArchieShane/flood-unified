# -*- coding: utf-8 -*-
"""把 Mussafah_00（61 mm / 2 h）的结果按 L1 / L2 / L2+A / L2+B **复制**成一个交付目录。

为什么要单独一个目录：原始结果是按「哪个脚本在哪一步产出」放的，同一态散在 4 处，
文件名还带着历史叫法——一维 L1 叫 baseline、L2+A 叫 Mussafah_R61D2、L2+B 叫 L2box；
**二维文件名里的 L2 实际是 L2+A**。用户看不出哪份是哪一态（2026-09-26 提出）。
这里只复制、改名、加说明，原始目录一个文件都不动，所有脚本照常能跑。

结构：
  00_说明.md
  对比_缺口分析/   四态缺口表.xlsx、四态缺口表.json
  L1_既有管网/      一维/ 二维/ 摘要.md
  L2_已建成调蓄/    说明.md（区内 0 座已建成塘，≡ L1，不重复放文件）
  L2+planned/ 一维/ 二维/ 设施/ 摘要.md
  L2+B_本系统箱涵/  一维/ 二维/ 设施/ 摘要.md

用法：python tools/export_delivery.py [--dst 目录] [--no-sww]
  --no-sww  不复制二维 .sww（三个合计约 5 GB），只在说明里写原始路径
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

SCEN = "R61D2"
# 态 → (目录名, 一维文件前缀, 二维标签, 逐帧目录, manifest 里的态名)
STATES = {
    "L1":   ("L1_既有管网",       f"Mussafah_baseline_{SCEN}_72h", "L1",  "L1",   "L1"),
    "L2+A": ("L2+planned",  f"Mussafah_{SCEN}_72h",          "L2",  "L2pA", "L2+A"),
    "L2+A+B": ("L2+suggested", f"Mussafah_L2AB_{SCEN}_72h", "L2AB", "L2pAB", "L2+A+B"),
    "L2+B": ("L2+B_本系统箱涵",   f"Mussafah_L2box_{SCEN}_72h",    "L2B", "L2pB", "L2+B"),
}
ORDER5 = ("L1", "L2", "L2+A", "L2+A+B", "L2+B")            # 主线 L1→L2→L2+A→L2+A+B，L2+B 是对照（2026-09-27 由四态改五态）
PAIRS5 = ["L1->L2", "L2->L2+A", "L2+A->L2+A+B", "L1->L2+A+B", "L2+A->L2+B", "L1->L2+B"]
DESC = {"L1": "既有永久设施：826 根管、822 个井、2 座泵站（RD-27 / RD-34，4 台泵）、3 个出水口。什么都不做的底。",
        "L2": "已建成的临时 / 中间措施。Mussafah_00 区内 0 座已建成塘（最近一座 7.3 km 外），本态 ≡ L1。",
        "L2+A": "Parsons 2026-09-17 报告的 2027 过渡方案：1 座塘 STO-PO3 + 2 口集水井 W-4 / W-6 + 2 台泵。规划设计，未建。",
        "L2+A+B": "把 Parsons 方案当既成事实，在它之上再加本系统 6 座地下箱涵（只对 Parsons 做完之后还在冒水的井选址，避开 Parsons 设施占地和取水井）。主线的终点。",
        "L2+B": "对照：不做 Parsons，从 L1 出发单做本系统 6 座地下调蓄箱涵（NP1–NP6），重力进水、带闸、应急溢流、排空泵；按积水分布选址，避开路面、建筑和地块。"}


def cp(src: pathlib.Path, dst: pathlib.Path, log, done: list):
    if not src.exists():
        log(f"  ! 缺 {src}"); return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    done.append((dst, src))


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="按五态整理 Mussafah_00 交付目录")
    ap.add_argument("--dst", default=str(ROOT / "out" / "交付" / "Mussafah_00_61mm2h"))
    ap.add_argument("--no-sww", action="store_true")
    ap.add_argument("--only", default="", help="只复制这几态的结果文件（逗号分隔，如 L2+A+B）；说明与对比表照样全量重写")
    a = ap.parse_args(argv)
    D = pathlib.Path(a.dst)
    log = print
    C2D = WORK / "c2d"
    st = json.loads((ROOT / "out" / f"state_table_Mussafah_00_{SCEN}.json").read_text(encoding="utf-8"))
    mf = json.loads((ROOT / "console" / "data" / "flood2d" / "manifest.json").read_text(encoding="utf-8"))
    why = json.loads((ROOT / "out" / "l2plus" / f"why_Mussafah_{SCEN}.json").read_text(encoding="utf-8"))
    T = st["table"]
    done: list = []

    def val(k, s):
        return (T.get(k) or {}).get("values", {}).get(s)

    only = {x.strip() for x in a.only.split(",") if x.strip()}
    for key, (folder, p1d, tag2d, fr, mfs) in STATES.items():
        if only and key not in only:
            continue
        F = D / folder
        log(f"{folder}")
        for ext, nm in ((".inp", "模型"), (".rpt", "报告"), (".out", "时序结果")):
            cp(WORK / f"{p1d}{ext}", F / "一维" / f"{key}_一维{nm}_72h{ext}", log, done)
        for ext, nm in ((".inp", "模型"), (".rpt", "报告"), (".out", "时序结果")):
            cp(C2D / f"Mussafah_{tag2d}_{SCEN}_2way{ext}", F / "二维" / "耦合的一维" / f"{key}_耦合一维{nm}{ext}", log, done)
        cp(C2D / f"twoway_Mussafah_{tag2d}_{SCEN}.json", F / "二维" / f"{key}_耦合运行记录.json", log, done)
        cp(C2D / f"dem_Mussafah_{tag2d}_{SCEN}.tif", F / "二维" / f"{key}_二维地形.tif", log, done)
        if not a.no_sww:
            cp(C2D / "anuga_out" / f"Mussafah_{tag2d}_{SCEN}_2way.sww", F / "二维" / f"{key}_二维水深时序.sww", log, done)
        s = next(x for x in mf["states"] if x["state"] == mfs)
        fd = F / "二维" / "逐帧水深图"
        for f in s["frames"]:
            cp(ROOT / "console" / "data" / "flood2d" / f["png"], fd / f"t{f['t_h']:05.2f}h.png", log, done)
        (fd / "范围.json").write_text(json.dumps({"bounds_latlon": s["bounds"], "crs": "EPSG:4326",
            "色标": f"0–{mf['vmax_m']} m，开方取色", "干湿阈值_m": mf["dry_m"]}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        with open(F / "二维" / f"{key}_积水面积与水深时序.csv", "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh); w.writerow(["时刻_h", "积水面积_ha", "至此最大水深_m"])
            for f in s["frames"]:
                w.writerow([f["t_h"], f["wet_ha"], f["dmax_m"]])
        # 设施
        if key == "L2+A":
            (F / "设施").mkdir(parents=True, exist_ok=True)
            (F / "设施" / "Parsons设施核对.json").write_text(
                json.dumps(why["parsons"], ensure_ascii=False, indent=1), encoding="utf-8")
        if key == "L2+B":
            (F / "设施").mkdir(parents=True, exist_ok=True)
            (F / "设施" / "箱涵选址与定容理由.json").write_text(
                json.dumps({"ours": why["ours"], "rules": why.get("rules")}, ensure_ascii=False, indent=1), encoding="utf-8")
            cp(ROOT / "out" / "l2plus" / f"box_land_check_{SCEN}.json", F / "设施" / "箱涵用地复核.json", log, done)
            cp(ROOT / "out" / "three_lines" / f"Mussafah.L2B.{SCEN}.ponds.geojson", F / "设施" / "箱涵占地轮廓与标高.geojson", log, done)
        if key == "L2+A+B":
            cp(ROOT / "out" / "three_lines" / f"Mussafah.L2AB.{SCEN}.ponds.geojson", F / "设施" / "箱涵占地轮廓与标高.geojson", log, done)
            cp(ROOT / "out" / "three_lines" / "Mussafah_L2AB_build.json", F / "设施" / "选址记录.json", log, done)
        # 摘要
        S2 = st["states"].get(key) or {}
        lines = [f"# {key}" + (f" · {folder.split('_', 1)[1]}" if "_" in folder else ""), "", DESC[key], "",
                 "| 指标 | 数值 |", "|---|---|",
                 f"| 最大积水面积（二维） | {s['peak_wet_ha']:.2f} ha，t = {s['peak_wet_t_h']:.2f} h |",
                 f"| 最大积水深度（二维） | {s['peak_depth_m']:.3f} m，t = {s['peak_depth_t_h']:.2f} h |"]
        for k in ("洪水量(节点累计)", "峰值滞水", "退水(主网)", "蓄存能力", "临时泵", "二维·一维交出水量"):
            m = T.get(k)
            if m and val(k, key) is not None:
                v = val(k, key); d = 2 if m["unit"] == "h" else 0
                lines.append(f"| {k} | {v:,.{d}f} {m['unit']} |")
        if S2.get("2d_n_tri"):
            lines += ["", f"二维：{S2.get('2d_res_m', 15):.0f} m 网格、{S2['2d_n_tri']:,} 个三角单元，模拟到 {S2.get('2d_end_h', 0):.0f} h。"
                      "淹没面积的分母是 1,453 ha 的二维计算域，不是 82 ha 的汇水区。"]
        lines += ["", "## 文件", "", "- `一维/`：72 小时一维 SWMM（ALLOW_PONDING YES）的模型、报告、时序结果",
                  "- `二维/耦合的一维/`：一二维双向耦合时用的一维模型（ALLOW_PONDING NO）及其结果",
                  "- `二维/*_耦合运行记录.json`：逐时刻积水面积、最大水深、1D↔2D 交换水量、水量闭合",
                  "- `二维/*_二维水深时序.sww`：ANUGA 全部时刻的水深（NetCDF，可用 ANUGA / QGIS Crayfish 打开）",
                  "- `二维/*_积水面积与水深时序.csv`、`二维/逐帧水深图/`：从 .sww 抽出的逐帧结果（系统页面播放用的就是这些）"]
        if key in ("L2+A", "L2+A+B", "L2+B"):
            lines.append("- `设施/`：这一态新增设施（相对 L1）的蓄水设施 / 管线闸堰 GIS、泵、控制规则，以及核对 / 理由；见 00_说明.md「L2+ 设施数据」")
        if key == "L2+A":
            lines.insert(1, "\n> **不是复现 Parsons 报告**：Parsons 的设施 + 61 mm / 2 h + 二维。和报告数字的对照见 `../对比_缺口分析/Parsons复现对照.xlsx`。\n")
        (F / "摘要.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    (D / "L2_已建成调蓄").mkdir(parents=True, exist_ok=True)
    (D / "L2_已建成调蓄" / "说明.md").write_text(
        f"# L2 · 已建成调蓄\n\n{DESC['L2']}\n\n所以这一态的所有结果都与 `L1_既有管网/` 相同，不重复放文件。\n"
        "全市 23 座已建成塘与汇水区的关系见系统地图 `?view=l2c`。\n", encoding="utf-8")

    # 对比 / 缺口
    G = D / "对比_缺口分析"; G.mkdir(parents=True, exist_ok=True)
    for old in ("四态缺口表.json", "四态缺口表.xlsx"):
        (G / old).unlink(missing_ok=True)
    (G / "五态缺口表.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        wb = Workbook(); ws = wb.active; ws.title = "五态对照"
        ws.append(["指标", "单位", "L1 既有", "L2 已建成", "L2+A Parsons", "L2+A+B 在其上加箱涵", "L2+B 对照（单做箱涵）", "越低越好？"])
        for k, m in T.items():
            ws.append([k, m["unit"]] + [m["values"].get(s) for s in ORDER5]
                      + ["是" if m.get("better") == "low" else "否"])
        ws2 = wb.create_sheet("相邻两态的缺口")
        pairs = PAIRS5
        ws2.append(["指标"] + [f"{p.replace('->', '→')} 差值" for p in pairs] + [f"{p.replace('->', '→')} %" for p in pairs])
        for k, g in st.get("gaps", {}).items():
            ws2.append([k] + [(g.get(p) or {}).get("abs") for p in pairs] + [(g.get(p) or {}).get("pct") for p in pairs])
        ws3 = wb.create_sheet("最大积水时序")
        S3 = ("L1", "L2+A", "L2+A+B", "L2+B")
        ws3.append(["时刻_h"] + [f"{s} 面积_ha" for s in S3] + [f"{s} 最大水深_m" for s in S3])
        ser = {x["state"]: {f["t_h"]: f for f in x["frames"]} for x in mf["states"]}
        for t in sorted({t for v in ser.values() for t in v}):
            ws3.append([t] + [(ser.get(s, {}).get(t) or {}).get("wet_ha") for s in S3]
                       + [(ser.get(s, {}).get(t) or {}).get("dmax_m") for s in S3])
        ws4 = wb.create_sheet("口径说明")
        for k, v in {**st.get("notes", {}), **st.get("empty", {})}.items():
            ws4.append([k, v.replace("**", "")])
        for w in (ws, ws2, ws3, ws4):
            for c in w[1]:
                c.font = Font(bold=True); c.fill = PatternFill("solid", fgColor="E6EFFC")
            w.column_dimensions["A"].width = 22
        ws4.column_dimensions["B"].width = 120
        wb.save(G / "五态缺口表.xlsx")
    except ImportError:
        log("  ! 没有 openpyxl，只出 json")

    # 总说明
    rows = []
    for key in ORDER5:
        rows.append(f"| {key} | {val('二维·峰值淹没面积', key):.2f} ha | {val('二维·峰值水深', key):.3f} m | "
                    f"{val('洪水量(节点累计)', key):,.0f} m³ | {val('峰值滞水', key):,.0f} m³ |")
    readme = f"""# Mussafah_00 · 61 mm / 2 h · 五态结果

整理于 {dt.date.today():%Y-%m-%d}，由 `tools/export_delivery.py` 从原始结果**复制**而来；原始目录未改动。

## 五态是什么

主线 **L1 → L2 → L2+A → L2+A+B**：每一步在上一步的基础上叠加，相邻两态之差就是那一步的贡献。
**L2+B** 是并列的对照方案（不做 Parsons、单做箱涵），只和 L2 比。

| 态 | 目录 | 说明 |
|---|---|---|
| L1 | `L1_既有管网/` | {DESC['L1']} |
| L2 | `L2_已建成调蓄/` | {DESC['L2']} |
| L2+A | `L2+planned/` | {DESC['L2+A']} |
| L2+A+B | `L2+suggested/` | {DESC['L2+A+B']} |
| L2+B | `L2+B_本系统箱涵/` | {DESC['L2+B']} |

按客户的分级框架（Surface Water Management Levels），Parsons 的临时塘与预置泵、本系统的地下箱涵都属于 **L2**（用户 2026-09-28 定）；
「L2+A / L2+B」是本系统的方案标签，不是客户的分级。

## 主要结果

| 态 | 最大积水面积 | 最大积水深度 | 溢出量（缺口） | 街面峰值滞水 |
|---|---|---|---|---|
{chr(10).join(rows)}

完整的 12 项指标与相邻两态的缺口在 `对比_缺口分析/五态缺口表.xlsx`；两套 L2+ 设施并排在 `对比_缺口分析/设施总表.xlsx`。

**统计值与口径**：`对比_缺口分析/五态统计值.xlsx`（及 .csv）列了一维与一二维的典型统计值 35 项，每项注明口径（一维 / 一二维）、统计口径说明与数据来源；
`计算设置.md` 是模拟时长、网格、分辨率等全部输入参数。

**两套设施有重叠**：L2+A+B 里 Parsons 的塘最高只装到 55%（单做 L2+A 时 93%）——上游的水先被箱涵接走了；如果箱涵要建，Parsons 那座塘可以考虑缩小或挪位。

## L2+A 和 Parsons 报告的关系（先看这个再引用 L2+A）

**L2+A 不是复现 Parsons 报告。**设施是 Parsons 设计的（取自客户交付的 Parsons 模型原件），雨换成 61 mm / 2 h、加了二维，
回答的是「Parsons 的设施遇到更猛的雨会怎样」。报告用的是 61.34 mm / 24 h。
在 Parsons 自己的雨下的对照见 `对比_缺口分析/Parsons复现对照.xlsx`（`.md` / `.json` 同内容）：缓解后洪水量对上了报告表 14（3,209 vs 3,324 m³，−3.5%），
现状洪水量对不上（我们的 L1 4,059 vs 报告 832 m³——L1 是我们剥出来的），报告自身有 6 处前后不一致。

## L2+ 设施数据

每个 L2+ 态的 `设施/` 里，按「这一态比 L1 多了什么」从模型文件拆出来（`tools/export_facilities.py`）：
`蓄水设施.geojson`（塘 / 箱涵 / 集水井的轮廓、标高、库容；Parsons 的塘和井没有设计轮廓，按顶面面积画示意方形并在 `outline` 字段标明）、
`管线闸堰.geojson`（新增管、进水闸孔、应急溢流堰、泵）、`泵.csv`（泵曲线已换算成 m³/s）、`控制规则.csv` / `.txt`。
L2 这一态区内没有已建成设施，没有 `设施/`。

## 口径（看数之前必须知道）

- 雨：61 mm 在 2 小时内下完，超过 DMT 手册 B 区 2 h 百年一遇（55.0 mm）的 1.11 倍。
- 一维 SWMM 5.2.4，客户交付的 Mussafah 模型；L1 由我们从「治理后」原件剥离得到。
- 二维 ANUGA，15 m 网格、258,332 个三角单元，与 SWMM 同进程逐步**双向**耦合；水量闭合差 0.000 m³。
- 最大积水面积的分母是 1,453 ha 的二维计算域，不是 82 ha 的汇水区。
- 「溢出量」是 SWMM 各井溢出累加（ALLOW_PONDING YES 下同一股水可能重复计），是强度指标，
  不是路面积水体积；路面上的水看「街面峰值滞水」和二维面积、水深。
- L2+B 箱涵的 1.5 倍聚类系数是假设，未标定；空地按客户 makani 图层判，权属与地下管线未核。

## 与原始文件的对应（历史命名，容易看错）

| 态 | 一维原始文件 | 二维原始文件 |
|---|---|---|
| L1 | `{WORK}\\Mussafah_baseline_{SCEN}_72h.*` | `{C2D}\\*Mussafah_L1_{SCEN}*` |
| L2+A | `{WORK}\\Mussafah_{SCEN}_72h.*` | `{C2D}\\*Mussafah_L2_{SCEN}*`（**文件名是 L2，实际是 L2+A**） |
| L2+A+B | `{WORK}\\Mussafah_L2AB_{SCEN}_72h.*` | `{C2D}\\*Mussafah_L2AB_{SCEN}*` |
| L2+B | `{WORK}\\Mussafah_L2box_{SCEN}_72h.*` | `{C2D}\\*Mussafah_L2B_{SCEN}*` |

系统里看：`http://127.0.0.1:8766/?tab=line2p`。重新整理：`python tools/export_delivery.py`。
"""
    (D / "00_说明.md").write_text(readme, encoding="utf-8")
    tot = sum(p.stat().st_size for p, _ in done)
    log(f"\n复制 {len(done)} 个文件，合计 {tot / 1024 ** 3:.2f} GB → {D}")
    import export_facilities                      # L2+ 设施：蓄水设施 / 管线闸堰 GIS、泵、控制规则、设施总表
    export_facilities.main([])
    import mussafah_params, mussafah_stats          # 计算设置.md、五态统计值（每项带统计口径）
    mussafah_params.main(); mussafah_stats.main()
    import delivery_rename                        # 态的显示名：L2+A → L2+planned，L2+A+B → L2+suggested（2026-09-28）
    delivery_rename.finalize(D)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
