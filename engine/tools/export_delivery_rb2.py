# -*- coding: utf-8 -*-
"""29.RB2 · 61 mm / 2 h 七态结果按态整理成交付目录（**复制**，原始目录不动）。结构与 Mussafah_00 那份一致。

  00_说明.md
  L1_既有管网/ L2_已建成BNS塘/ L2+planned/ L2+suggested/ L2+suggested_放宽_…/ L2+B_单做箱涵_对照/ L2+B放宽_…
     各态：一维/（模型）  二维/（耦合的一维报告、运行记录、水深帧、积水面积时序）  设施/（相对 L1 新增）  摘要.md
  公共/二维地形.tif
  对比_缺口分析/七态缺口表.xlsx / .json、设施总表.xlsx

用法：python tools/export_delivery_rb2.py
"""
from __future__ import annotations

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
import export_facilities as EF  # noqa: E402

C2D = WORK / "c2d"
D = ROOT / "out" / "交付" / "29.RB2_61mm2h"
# 内部键 → (目录名, 显示名, 父态)
STATES = {
    "L1":    ("L1_既有管网", "L1", None),
    "L2":    ("L2_已建成BNS塘", "L2", "L1"),
    "L2A":   ("L2+planned", "L2+A", "L2"),
    "L2AB":  ("L2+suggested", "L2+A+B", "L2A"),
    "L2AB2": ("L2+suggested_放宽_可借用未盖楼地块", "L2+A+B（放宽）", "L2A"),
    "L2AC":  ("L2+planned+C_加临时固定泵与地面管", "L2+A+C", "L2A"),
    "L2B":   ("L2+B_单做箱涵_对照", "L2+B", "L2"),
    "L2B2":  ("L2+B放宽_单做箱涵_对照_可借用未盖楼地块", "L2+B（放宽）", "L2"),
}
BOXES = {"L2AB": "RB2x.L2AB.ponds.geojson", "L2AB2": "RB2x.L2AB2.ponds.geojson",
         "L2B": "RB2x.L2B.R61D2.ponds.geojson", "L2B2": "RB2x.L2B.R61D2v.ponds.geojson"}


def cp(src, dst, done):
    src = pathlib.Path(src)
    if not src.exists():
        print(f"  ! 缺 {src}"); return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst); done.append(dst)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    PF = json.loads((ROOT / "out" / "pilot_RB2x_R61D2.json").read_text(encoding="utf-8"))
    IN = json.loads((ROOT / "out" / "pilot_RB2x_R61D2_inregion.json").read_text(encoding="utf-8"))["states"]
    CAT = json.loads((ROOT / "console" / "data" / "l2plus_catalog.json").read_text(encoding="utf-8"))["regions"]["29.RB2"]
    MF = json.loads((ROOT / "console" / "data" / "flood2d_rb2" / "manifest.json").read_text(encoding="utf-8"))
    done = []
    cp(C2D / "dem_RB2x.tif", D / "公共" / "二维地形.tif", done)
    fac_res = {}
    have = {x["state"] for x in MF["states"]}
    for st in [k for k in STATES if k not in have]:
        print(f"  跳过 {st}：还没有逐帧结果（L2+A+C 等 Baniyas Lake 的决定）"); STATES.pop(st)
    for st, (folder, lab, parent) in STATES.items():
        F = D / folder; print(folder)
        cp(WORK / f"RB2x_{st}.inp", F / "一维" / f"{lab}_一维模型.inp", done)
        for ext, nm in ((".inp", "模型"), (".rpt", "报告")):
            cp(C2D / f"RB2x_{st}_2way{ext}", F / "二维" / "耦合的一维" / f"{lab}_耦合一维{nm}{ext}", done)
        cp(C2D / f"twoway_RB2x_{st}.json", F / "二维" / f"{lab}_耦合运行记录.json", done)
        cp(C2D / f"RB2x_{st}_2d.npz", F / "二维" / f"{lab}_二维水深帧与最大水深.npz", done)
        # 标准格式（2026-09-28 补）：.sww 由 tools/npz_to_sww.py 从 npz 重建（只有水位、地面高程，无流速）；GeoTIFF 由 tools/npz_to_tif.py
        cp(C2D / "anuga_out" / f"RB2x_{st}.sww", F / "二维" / f"{lab}_二维水深时序.sww", done)
        cp(C2D / "tif" / f"RB2x_{st}_depth_frames.tif", F / "二维" / "GIS" / f"{lab}_水深逐帧_72波段.tif", done)
        cp(C2D / "tif" / f"RB2x_{st}_depth_frames.json", F / "二维" / "GIS" / f"{lab}_水深逐帧_72波段_波段时刻.json", done)
        cp(C2D / "tif" / f"RB2x_{st}_depth_max.tif", F / "二维" / "GIS" / f"{lab}_全程最大水深.tif", done)
        s = next(x for x in MF["states"] if x["state"] == st)
        for f in s["frames"]:
            cp(ROOT / "console" / "data" / "flood2d_rb2" / f["png"], F / "二维" / "逐帧水深图" / f"t{f['t_h']:05.2f}h.png", done)
        (F / "二维" / "逐帧水深图" / "范围.json").write_text(json.dumps({"bounds_latlon": s["bounds"], "crs": "EPSG:4326",
            "出图阈值_m": MF.get("show_m"), "色标上限_m": MF.get("vmax_m")}, ensure_ascii=False, indent=1), encoding="utf-8")
        with open(F / "二维" / f"{lab}_积水面积时序.csv", "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh); w.writerow(["时刻_h", "积水≥0.02m面积_ha（计算域）", "积水≥0.10m面积_ha（计算域）", "至此最大水深_m"])
            for f in s["frames"]:
                w.writerow([f["t_h"], f["wet_ha"], f["wet10_ha"], f["dmax_m"]])
        if st != "L1":
            fp, notes, extra = {}, {}, {}
            m = json.loads((C2D / f"pilot_RB2x_{st}.json").read_text(encoding="utf-8"))
            for p in m.get("ponds", []):
                if p.get("footprint"):
                    fp[p["node"]] = p["footprint"]
                notes[p["node"]] = ("RB_02 模型的 PO 塘，设计方未确认" if str(p["unitid"]).startswith("PO")
                                    else "RB_02 模型的 BNS 塘，按已建成处理（用户 2026-09-27 同意）")
            if st in BOXES:
                g = json.loads((ROOT / "out" / "three_lines" / BOXES[st]).read_text(encoding="utf-8"))
                for f in g["features"]:
                    q = f["properties"]; fp[q["id"]] = f["geometry"]; notes[q["id"]] = "本系统设计的地下箱涵（L2）"
                    extra[q["id"]] = {"usable_m3": q["vol_m3"], "land": q.get("land"), "moved_m": q.get("moved_m")}
                cp(ROOT / "out" / "three_lines" / BOXES[st], F / "设施" / "箱涵占地轮廓与标高.geojson", done)
            if st == "L2AC":
                from pyproj import Transformer; _tr = Transformer.from_crs(4326, 32640, always_xy=True)
                des = json.loads((ROOT / "out" / "RB2x_L2AC_design.json").read_text(encoding="utf-8"))
                feats = []
                for q in des["pumps"]:
                    pr = {k: q.get(k) for k in ("id", "n_pumps", "q_m3s", "off_m", "to_node", "pipe_m", "deep_area_ha", "vol_ge010_m3", "low_m")}
                    feats.append({"type": "Feature", "properties": {**pr, "要素": "取水片（二维单元从这里抽水）"},
                                  "geometry": {"type": "Polygon", "coordinates": [q["sump_polygon"]]}})
                    if q.get("to_lonlat"):
                        feats.append({"type": "Feature", "properties": {"id": q["id"], "要素": "地面管（直线示意，未走线）", "pipe_m": q["pipe_m"], "to_node": q.get("to_node")},
                                      "geometry": {"type": "LineString", "coordinates": [q["xy"], [round(v, 1) for v in _tr.transform(*q["to_lonlat"])]]}})
                g = F / "设施" / "临时固定泵与地面管.geojson"; g.parent.mkdir(parents=True, exist_ok=True)
                g.write_text(json.dumps({"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:32640"}}, "features": feats},
                                        ensure_ascii=False), encoding="utf-8"); done.append(g)
                cp(ROOT / "out" / "RB2x_L2AC_design.json", F / "设施" / "临时固定泵设计与假设.json", done)
            fac_res[lab] = EF.export_state(WORK / "RB2x_L1.inp", WORK / f"RB2x_{st}.inp", F / "设施", lab, footprints=fp, notes=notes, extra=extra)
        # 摘要
        r, ri = PF[st], IN[st]
        lines = [f"# {lab} · 29.RB2 · 61 mm / 2 h", "", CAT["states"].get(lab, ""), "",
                 "| 指标 | 数值 |", "|---|---|",
                 f"| 积水 ≥0.30 m 面积（29.RB2 内） | {ri['area_ge_0p30_in_region_ha']:.1f} ha |",
                 f"| 积水 ≥0.10 m 面积（29.RB2 内） | {ri['area_ge_0p10_in_region_ha']:.1f} ha |",
                 f"| 深水 ≥0.8 m 面积（29.RB2 内） | {ri['area_ge_0p80_in_region_ha']:.2f} ha |",
                 f"| 最大水深（29.RB2 内） | {ri['max_depth_in_region_m']:.2f} m（一处封闭洼地，与方案无关） |",
                 f"| 管网溢流进地面（计算域） | {r['flood_1d_to_2d_m3']:,.0f} m³ |",
                 f"| 6 h 末地面上的水（计算域） | {r['final_vol_m3']:,.0f} m³ |",
                 f"| 地表进塘 | {r['into_ponds_surface_m3']:,.0f} m³ |", "",
                 f"对比基准：{STATES[parent][1] if parent else '—（主线起点）'}。",
                 "", "## 文件", "", "- `一维/`：这一态的 SWMM 模型（RB_02 shapefile 重建）",
                 "- `二维/耦合的一维/`：一二维双向耦合时一维侧的模型与报告",
                 "- `二维/*_耦合运行记录.json`：逐时刻积水面积、最大水深、1D↔2D 交换水量、水量对账",
                 "- `二维/*_二维水深帧与最大水深.npz`：每 5 分钟一帧的三角单元水深、全程最大水深、单元面积与形心坐标（UTM 40N）",
                 "- `二维/逐帧水深图/`、`*_积水面积时序.csv`：系统页面播放用的就是这些",
                 "- `二维/*_二维水深时序.sww`：ANUGA 标准格式，每 5 分钟一帧；**只有水位与地面高程，没有流速**（计算时为省盘关了 .sww、只存了形心水深，事后按同一张三角网重建写出；xmomentum / ymomentum 全为 0）",
                 "- `二维/GIS/`：30 m GeoTIFF（EPSG:32640）——72 波段逐帧水深（波段时刻见同名 .json）、全程最大水深；每格取格内三角单元的最大值，用栅格数面积会偏大"]
        if st != "L1":
            lines.append("- `设施/`：这一态相对 L1 新增的设施（蓄水设施 / 管线闸堰 GIS、泵、控制规则）")
        (F / "摘要.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 对比
    G = D / "对比_缺口分析"; G.mkdir(parents=True, exist_ok=True)
    (G / "多态缺口表.json").write_text(json.dumps({"domain": PF, "in_region": IN}, ensure_ascii=False, indent=1), encoding="utf-8")
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook(); ws = wb.active; ws.title = "多态对照"
    keys = [("积水≥0.30m面积（29.RB2 内）", "area_ge_0p30_in_region_ha", "ha", "in"), ("积水≥0.10m面积（29.RB2 内）", "area_ge_0p10_in_region_ha", "ha", "in"),
            ("深水≥0.8m面积（29.RB2 内）", "area_ge_0p80_in_region_ha", "ha", "in"), ("最大水深（29.RB2 内）", "max_depth_in_region_m", "m", "in"),
            ("管网溢流进地面（计算域）", "flood_1d_to_2d_m3", "m³", "pf"), ("6 h 末地面上的水（计算域）", "final_vol_m3", "m³", "pf"),
            ("地表进塘", "into_ponds_surface_m3", "m³", "pf")]
    ws.append(["指标", "单位"] + [STATES[s][1] for s in STATES])
    for zh, k, u, src in keys:
        ws.append([zh, u] + [(IN if src == "in" else PF)[s].get(k) for s in STATES])
    ws2 = wb.create_sheet("相对上一态")
    ws2.append(["指标"] + [f"{STATES[s][1]} vs {STATES[STATES[s][2]][1]} %" for s in STATES if STATES[s][2]])
    for zh, k, u, src in keys:
        T = IN if src == "in" else PF; row = [zh]
        for s in STATES:
            p0 = STATES[s][2]
            if p0:
                a, b = T[p0].get(k), T[s].get(k)
                row.append(round((b - a) / a * 100, 2) if a else None)
        ws2.append(row)
    ws3 = wb.create_sheet("口径说明")
    for n in CAT["notes"] + ["面积按三角单元真实面积加权；「29.RB2 内」不含 500 m 缓冲带",
                             "主线 L1 → L2 → L2+A → L2+A+B（严格 / 放宽）；L2+B（严格 / 放宽）从 L2 出发作对照"]:
        ws3.append([n])
    for w in (ws, ws2):
        for c in w[1]:
            c.font = Font(bold=True); c.fill = PatternFill("solid", fgColor="E6EFFC")
        w.column_dimensions["A"].width = 30
    ws3.column_dimensions["A"].width = 140
    wb.save(G / "七态缺口表.xlsx")
    EF.workbook(G / "设施总表.xlsx", fac_res)

    rows = "\n".join(f"| {lab} | `{folder}/` | {CAT['states'].get(lab, '')} | {IN[s]['area_ge_0p30_in_region_ha']:.1f} ha | "
                     f"{IN[s]['area_ge_0p80_in_region_ha']:.2f} ha | {PF[s]['flood_1d_to_2d_m3']:,.0f} m³ |"
                     for s, (folder, lab, _) in STATES.items())
    readme = f"""# 29.RB2 · 61 mm / 2 h · 七态结果

整理于 {dt.date.today():%Y-%m-%d}，由 `tools/export_delivery_rb2.py` 从原始结果**复制**而来；原始目录未改动。
在线报告：https://claude.ai/artifact/T9wuV4541wbdepwmhFdTiX 。系统里看：`http://127.0.0.1:8766/?tab=line2p&v=rb2`。

## 七态是什么

主线 **L1 → L2 → L2+A → L2+A+B（严格 / 放宽）**，每一步在上一步的基础上叠加；**L2+B（严格 / 放宽）**是不做 PO 塘、从 L2 出发单做箱涵的对照。
「严格」= 避开路面、建筑、已分配地块；「放宽」= 可借用还没盖楼的已分配地块（要业主 / 规划部门同意）。
按客户的分级框架，BNS 塘、PO 塘、本系统的地下箱涵都属于 **L2**。

| 态 | 目录 | 说明 | 积水 ≥0.30 m（29.RB2 内） | 深水 ≥0.8 m（29.RB2 内） | 管网溢流进地面 |
|---|---|---|---|---|---|
{rows}

## 看数之前必须知道

- **L2+A 是 RB_02 模型里的 24 座 PO 塘和配套泵，设计方没有文件写明**：Parsons 的 4 份报告里没有 RB2，编号与泵型只是像 Parsons；24 座里只有 PO-4、PO-18 在 29.RB2 内。
- 模型从客户 RB_02 的 shapefile 导出重建：导出里没有堰 / 孔口 / 出流口，塘的进出口、PO 泵到塘的连接是推断的；PO 泵的吸水井补成了湿井并加了塘满停泵。已向客户要完整的 RB_02 模型。
- 一维包含整个 RB_02 管网，二维只覆盖 29.RB2 外扩 500 m（39 km²，30 m 网格）：计算域外井的溢流不进二维。面积只算 29.RB2 内。
- 最大水深七态都是 1.08 m：29.RB2 内一处约 1 m 深的封闭洼地（24.3137°N 54.6535°E）灌满，与方案无关，所以深水看 ≥0.8 m 面积。
- 这一片溢流分散在 8,000 多口井上、一半出在雨水口：集中调蓄能压住深积水（≥0.8 m 面积最多少 68%），压不住大面积浅积水（≥0.10 m 只少 14%）；浅积水要靠 L2 的临时转输 / 挡导，以及长期的雨水口与支管扩容。
- 2026-09-28 更正：四个带箱涵的态第一版排空泵单位写错（放大 1000 倍），已改正重跑，本目录是改正后的结果。
- 箱涵定容的 1.5 倍聚类系数是假设、未标定；可用水深 2.0 m + 0.3 m 超高。

完整对照在 `对比_缺口分析/七态缺口表.xlsx`，各态新增设施并排在 `对比_缺口分析/设施总表.xlsx`。
重新整理：`python tools/export_delivery_rb2.py`。
"""
    (D / "00_说明.md").write_text(readme, encoding="utf-8")
    print(f"\n复制 {len(done)} 个文件，合计 {sum(p.stat().st_size for p in done) / 1024 ** 2:,.0f} MB → {D}")
    import delivery_rename                        # 态的显示名：L2+A → L2+planned，L2+A+B → L2+suggested（2026-09-28）
    delivery_rename.finalize(D)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
