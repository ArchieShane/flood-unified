"""RD-A 模拟积水点清单导出（Excel）：L2+planned 与 L2+suggested 各一页，中英文各一套，共四页。

每页三块：① 我们算出来的积水点清单（最大水深、峰值积水量、峰值时刻，与客户关注热点 200 m 内重合的行标橙）；
② 与热点重合的积水点 ↔ 热点明细；③ 一句话统计；④ 关注热点圆内积水量占全域的比例。
口径同页面「积水点」标签：depth 阈值默认 0.15 m、面积 ≥100 m²、持续 ≥0.5 h，热点 200 m 取成果自带的 ponding_near_hotspots。

用法：PYTHONPATH=src python -m stormwater.rda_ponding_xlsx [输出路径]
"""
from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from . import rda

SHEETS = (("L2P", "L2+planned"), ("OPTCS2", "L2+suggested"))
TOP_N = 8

NAVY = PatternFill("solid", fgColor="0F2A4A")
ORANGE = PatternFill("solid", fgColor="FDEBD8")
THIN = Side(style="thin", color="D0D5DD")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FONT = Font(bold=True, color="FFFFFF")

# 两套文案；带 {} 的是格式化占位
TXT = {
    "zh": {
        "suffix": "中文",
        "title": "{label}：模拟积水点清单",
        "note": "【推演场景】\n"
                "· 汇水区：RD-A；一二维耦合模拟（SWMM 一维管网 + ANUGA 二维地表，15 m 网格，共 65,210 个单元）。\n"
                "· 降雨：61 mm / 1 h 设计暴雨（5 分钟峰值强度 352 mm/h，扣除损失后净雨 44.2 mm），模拟总时长 11 h（雨后再算 10 h）。\n"
                "· 本页方案：{scene}\n"
                "【积水点如何定义】\n"
                "1. 积水单元：全程最大水深 ≥ {d:.2f} m 的网格单元（方案自带的地表塘占地不算，塘里有水是正常蓄水）。\n"
                "2. 连片：共边相邻的积水单元连成一片，一片就是一个积水点。\n"
                "3. 保留条件：连片面积 ≥ 100 m²，且「片内积水面积 ≥ 100 m²」的累计时长 ≥ 0.5 h；不满足的不算（排除零星、一闪而过的积水）。\n"
                "4. 最大水深：片内全程最大水深；峰值积水量：每 5 分钟一帧求 Σ（水深 × 单元面积），取最大值；峰值时刻：该最大值出现的时刻（从降雨开始计）。\n"
                "5. 编号（001、002…）按峰值积水量从大到小排，是模型算出来的积水点编号，不是客户热点编号；换方案后同一编号不是同一处。\n"
                "6. 橙色行：积水点内最近单元到客户关注热点（ADM-xxx）≤ 200 m。",
        "scene": {"L2P": "L2+planned——第二道防线，叠加客户已有计划（视同建成）的设施。",
                  "OPTCS2": "L2+suggested——第二道防线，叠加我们的推荐方案（53 项措施）。"},
        "s1": "① 模拟积水点清单（共 {n} 片，按峰值积水量从大到小；橙色 = 与历史关注热点 200 m 内重合）",
        "head": ["积水点", "最大水深 (m)", "峰值积水量 (m³)", "峰值时刻 (h)", "200 m 内热点数", "200 m 内关注热点（距离）"],
        "s2": "② 哪些积水点包含了客户重点关注的热点（{n} 片积水点与热点 200 m 内重合）",
        "hs": "{id}（{d} m）", "sep": "、",
        "s4": "④ 关注热点 200 m 圆内的地表峰值积水量占全域积水量的比例",
        "s4head": ["口径", "分子：热点圆内峰值积水量 (m³)", "分子峰值时刻", "分母：全域峰值积水量 (m³)", "分母峰值时刻 · 单元数", "占比"],
        "s4rows": ["不设阈值（所有水深都加；不扣剔除区；分母 = 页面右侧总览的「总积水量」）",
                   ],
        "s4cells": "@ {t:.2f} h · {n} 个单元",
        "s4note": "分子：落在任一关注热点 200 m 圆内的单元，每个单元只算一次（圆重叠处不重复），逐时刻水深×单元面积求和后取全时段最大；分母：全域所有单元同样求和取最大。分子、分母的峰值时刻不一定相同。",
        "s3": "③ 统计信息",
        "summary": "{n} 片积水点峰值积水量合计 ≈ {total:,} m³，前 {top_n} 片占 {top_p:.0%}；"
                   "其中 {k} 片与历史 ADM 关注热点 200 m 内重合（橙色），峰值积水量 {hv:,} m³，占合计 {hp:.1%}。",
    },
    "en": {
        "suffix": "EN",
        "title": "{label}: simulated ponding spots",
        "note": "Simulation scenario\n"
                "- Catchment: RD-A. Coupled 1D-2D model (SWMM 1D drainage network + ANUGA 2D surface flow, 15 m grid, 65,210 cells).\n"
                "- Rainfall: 61 mm / 1 h design storm (5-min peak intensity 352 mm/h; 44.2 mm net rain after losses); simulated for 11 h (10 h after the rain stops).\n"
                "- This sheet: {scene}\n"
                "How a ponding spot is defined\n"
                "1. Ponded cell: a grid cell whose maximum depth over the run is >= {d:.2f} m (the footprint of the scheme's own surface ponds is excluded - water there is intended storage).\n"
                "2. Patch: ponded cells that share an edge are joined into one patch; each patch is one spot.\n"
                "3. Kept only if the patch area is >= 100 m2 and the time its ponded area stays >= 100 m2 totals >= 0.5 h (filters out scattered or momentary ponding).\n"
                "4. Max depth: maximum depth in the patch over the run. Peak volume: sum of (depth x cell area) at each 5-min frame, maximum over time. Peak time: when that maximum occurs (from rain start).\n"
                "5. Spot IDs (001, 002...) rank by peak volume; they are model-generated spot numbers, not client hotspot IDs, and the same ID is not the same place under another scheme.\n"
                "6. Orange rows: the spot lies within 200 m of a client-watched hotspot (ADM-xxx).",
        "scene": {"L2P": "L2+planned - second line of defence plus the client's planned facilities (treated as built).",
                  "OPTCS2": "L2+suggested - second line of defence plus our recommended scheme (53 measures)."},
        "s1": "1. Simulated ponding spots ({n} in total, by peak volume; orange = within 200 m of a historical hotspot)",
        "head": ["Spot (ID)", "Max depth (m)", "Peak volume (m³)", "Peak time (h)", "Hotspots within 200 m", "Hotspots within 200 m (distance)"],
        "s2": "2. Spots that contain client-watched hotspots ({n} spots within 200 m of a hotspot)",
        "hs": "{id} ({d} m)", "sep": ", ",
        "s4": "4. Peak ponded volume inside the 200 m circles of watched hotspots, as a share of the whole domain",
        "s4head": ["Basis", "Numerator: volume in circles (m³)", "Numerator peak time", "Denominator: whole domain (m³)", "Denominator peak time · cells", "Share"],
        "s4rows": ["No threshold (all depths counted; exclusion zone not removed; denominator = 'total ponded volume' in the side overview)"],
        "s4cells": "@ {t:.2f} h · {n} cells",
        "s4note": "Numerator: cells within 200 m of any watched hotspot, each counted once (no double count where circles overlap); depth × cell area summed per time step, then the maximum over time. Denominator: the same for every cell in the domain. The two peak times need not coincide.",
        "s3": "3. Summary",
        "summary": "Total peak volume of the {n} spots ≈ {total:,} m³; the top {top_n} account for {top_p:.0%}. "
                   "{k} spot(s) overlap a historical ADM hotspot (within 200 m), peak volume {hv:,} m³ ({hp:.1%} of the total).",
    },
}


def _short(pid: str) -> str:
    return pid.rsplit("-", 1)[-1]


def _hs_text(near: list[dict], tx: dict) -> str:
    return tx["sep"].join(tx["hs"].format(id=h["hotspot"], d=h["dist_m"]) for h in sorted(near, key=lambda x: float(x["dist_m"])))


def _put(ws, row: int, values: list, *, head=False, fill=None, bold_cols=(), numfmt=None) -> None:
    for i, v in enumerate(values, 1):
        c = ws.cell(row=row, column=i, value=v)
        c.border = BORDER
        if head:
            c.fill, c.font = NAVY, HEAD_FONT
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            continue
        if fill:
            c.fill = fill
        if i in bold_cols:
            c.font = Font(bold=True)
        if isinstance(v, (int, float)):
            c.alignment = Alignment(horizontal="right")
            if numfmt and i in numfmt:
                c.number_format = numfmt[i]
        else:
            c.alignment = Alignment(vertical="center", wrap_text=True)


def _section(ws, row: int, text: str, ncol: int) -> int:
    ws.cell(row=row, column=1, value=text).font = Font(bold=True, size=12)
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncol)
    return row + 1


def build_sheet(ws, key: str, label: str, lang: str) -> None:
    tx = TXT[lang]
    p = rda.ponding(key)
    pts = p["points"]
    n_hs = len(rda.hotspots()["features"])
    total = sum(r["vol_peak_m3"] for r in pts)
    ncol = 6

    ws.cell(row=1, column=1, value=tx["title"].format(label=label, d=p["depth_m"])).font = Font(bold=True, size=14)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    c = ws.cell(row=2, column=1, value=tx["note"].format(d=p["depth_m"], scene=tx["scene"][key]))
    c.alignment = Alignment(wrap_text=True, vertical="top")
    c.font = Font(color="475467")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncol)
    ws.row_dimensions[2].height = 205
    r = 4

    # ① 全部积水点
    r = _section(ws, r, tx["s1"].format(n=len(pts)), ncol)
    _put(ws, r, tx["head"], head=True)
    ws.row_dimensions[r].height = 32
    r += 1
    for x in pts:
        near = x["near_hotspots"]
        _put(ws, r, [_short(x["id"]), x["hmax_m"], x["vol_peak_m3"], x["t_peak_h"], len(near), _hs_text(near, tx) if near else ""],
             fill=ORANGE if near else None, bold_cols=(3,), numfmt={2: "0.00", 3: "#,##0", 4: "0.00"})
        r += 1
    r += 1

    # ② 重合明细
    hit = [x for x in pts if x["near_hotspots"]]
    r = _section(ws, r, tx["s2"].format(n=len(hit)), ncol)
    _put(ws, r, tx["head"], head=True)
    ws.row_dimensions[r].height = 32
    r += 1
    for x in sorted(hit, key=lambda x: -x["vol_peak_m3"]):
        _put(ws, r, [_short(x["id"]), x["hmax_m"], x["vol_peak_m3"], x["t_peak_h"], len(x["near_hotspots"]), _hs_text(x["near_hotspots"], tx)],
             fill=ORANGE, bold_cols=(1, 3), numfmt={2: "0.00", 3: "#,##0", 4: "0.00"})
        r += 1
    r += 1

    # ③ 统计
    top = sum(sorted((x["vol_peak_m3"] for x in pts), reverse=True)[:TOP_N])
    hit_vol = sum(x["vol_peak_m3"] for x in hit)
    c = ws.cell(row=r, column=1, value=tx["summary"].format(
        n=len(pts), total=total, top_n=TOP_N, top_p=top / total, k=len(hit), hv=hit_vol, hp=hit_vol / total))
    c.alignment = Alignment(wrap_text=True, vertical="top")
    c.font = Font(color="475467")
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)
    ws.row_dimensions[r].height = 34
    r += 1

    # ④ 关注热点圆内积水量占全域（口径同页面「积水点」标签的占比表）
    r += 1
    r = _section(ws, r, tx["s4"], ncol)
    _put(ws, r, tx["s4head"], head=True)
    ws.row_dimensions[r].height = 32
    r += 1
    d = p["depth_m"]
    b = rda.hotspot_buffer(key, d)
    for name, num, den, pct in (
        (tx["s4rows"][0], b["union_all"], b["domain_all"], b["union_all_pct"]),
    ):
        den_note = tx["s4cells"].format(t=den["t_peak_h"], n=den["n_cells"]) if "n_cells" in den else ""
        _put(ws, r, [name, num["vol_peak_m3"], f"@ {num['t_peak_h']:.2f} h", den["vol_peak_m3"], den_note, pct / 100],
             bold_cols=(6,), numfmt={2: "#,##0", 4: "#,##0", 6: "0.0%"})
        r += 1
    ws.cell(row=r, column=1, value=tx["s4note"]).font = Font(italic=True, color="667085")
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)

    for col, w in zip("ABCDEF", (34, 16, 18, 14, 22, 70)):
        ws.column_dimensions[col].width = w


def export(path: str | Path) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    for lang in ("zh", "en"):
        for key, label in SHEETS:
            name = f"{label.replace('+', '_')}_{TXT[lang]['suffix']}"
            build_sheet(wb.create_sheet(name), key, label, lang)
    wb.save(path)
    return Path(path)


if __name__ == "__main__":
    print(export(sys.argv[1] if len(sys.argv) > 1 else "RD-A_模拟积水点清单_L2planned_sugg.xlsx"))
