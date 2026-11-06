# -*- coding: utf-8 -*-
"""C1 热点群一二维交付目录：out/交付/C1_{61,96}mm2h/（用户 2026-09-28 授权自动做）。已算完的态才出，没算完的跳过。

每场雨：
  <态>/一二维/         耦合的一维模型与报告、耦合运行记录（水量对账）、.sww（带流速）、GeoTIFF（15 m，逐帧 + 全程最大）、npz、逐帧图、积水面积时序
  <态>/设施.csv        这个态比上一个态多了哪些设施（泵、塘、转输、挡水、箱涵）
  <态>/摘要.md
  统计值/C1统计值.xlsx  每项带统计口径（c1_post.py），另有水量对账、最深点核查
  计算设置.md           参数对齐页全部 20 项的最终取值（含「Claude 自定」项）与局限
用法：python tools/export_delivery_c1.py [--rain 61|96] [--no-big]
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
FOLDER = {"L1": "L1_现状管网+现状泵站", "L2": "L2_加OP-02移动泵", "L2P": "L2+planned_Parsons方案", "L2S": "L2+suggested_转输与地面取水泵", "L2S3": "L2+suggested_v3_按塘余量分配"}
NAMES = {"L1": "L1", "L2": "L2", "L2P": "L2+planned", "L2S": "L2+suggested", "L2S3": "L2+suggested v3"}
DESC = {"L1": "现状管网（客户 GDB 重建）+ 客户库登记的 6 座现状泵站",
        "L2": "L1 + Parsons OP-02 的 23 台 12 英寸移动泵（雨前布设），排到下游不溢流的井",
        "L2P": "L1 + Parsons PDF 版方案：27 座固定泵站（只开主用）+ 12 座 1 m 围堤塘（443,274 m³）+ 压力管；保留 L2 的 MP-16",
        "L2S": "L2+planned + 按本场雨 L2+planned 残余积水设计的 v2 措施：管网接不住处固定泵转输、地表来水的洼地用地面取水泵，全部送进有余量的规划塘，没有出路的才用箱涵（v1 挡水土堤已弃用）",
        "L2S3": "L2+planned + v3 措施：处数与位置同 v2 的做法，去向按规划塘剩余库容分配、塘水深到 0.9 m 停泵、地面取水泵直接打进塘的一维节点（消掉 v2 在小塘周边的副作用）"}

# 2026-10-02 用户定：原计算域交付以 v3 为准（v2 留作历史）。C12 这条只写了 v2 的做法，这里把 v3 追加在计算设置里。
C12_V3_NOTE = ("- **C12 补充（2026-10-02 用户定：以 v3 为准）** L2+suggested 现有两版，**v3 是采用的那一版**，v2 留作历史："
               "v3 的措施处数与位置同 v2（同样按本场雨 L2+planned 的残余积水设计），只改去向与控制——去向按规划塘的剩余库容分配、"
               "塘水深到 0.9 m 停泵、地面取水泵直接打进塘的一维节点。v2 在水面小的塘周边把邻域泡深，v3 把它压下去"
               "（比 L2+planned 变深的面积：61 mm 由 v2 的 0.5 ha 降到 0.0 ha，96 mm 由 0.7 ha 降到 0.04 ha）。"
               "设计规则见 `tools/c1_design_l2s3.py`；两版的做法与结果对比见在线报告。")


def cp(src, dst, done):
    src = pathlib.Path(src)
    if not src.exists():
        print(f"  ! 缺 {src.name}"); return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst); done.append(dst)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--rain", type=int, choices=[61, 96]); ap.add_argument("--no-big", action="store_true")
    ap.add_argument("--tag", default=None, help="R61 / R96 / U61（客户单元版计算域，2026-09-29）")
    a = ap.parse_args()
    params = json.loads((ROOT / "out" / "c1_params_answers.json").read_text(encoding="utf-8")) if (ROOT / "out" / "c1_params_answers.json").exists() else {}
    tags = [a.tag] if a.tag else [f"R{a.rain}"] if a.rain else ["R61", "R96"]
    for tag in tags:
        U = tag.startswith("U"); DS = tag.startswith("D"); MV = tag.startswith("M")   # U61 客户单元版；D24 设计暴雨；M61 模型版（2026-09-30）
        r = 61 if DS else int(tag[1:])
        N = f"C1_{tag}"; FR = ROOT / "console" / "data" / f"flood2d_c1_{tag.lower()}"
        if not (FR / "c1_stats.json").exists():
            print(f"{N}：还没有后处理结果，跳过"); continue
        CS = json.loads((FR / "c1_stats.json").read_text(encoding="utf-8")); MF = json.loads((FR / "manifest.json").read_text(encoding="utf-8"))
        D = ROOT / "out" / "交付" / (f"C1_客户单元_{r}mm2h" if U else f"C1_模型版_{r}mm2h" if MV else "C1_设计暴雨_61.34mm24h" if DS else f"C1_{r}mm2h"); done = []
        cp(C2D / f"dem_{N}.tif", D / "公共" / "二维地形_5mDTM原值.tif", done)
        cp(ROOT / "console" / "data" / "c1_catch.geojson", D / "公共" / "8个热点的地表与管网汇水区.geojson", done)
        if MV:                                          # 模型版：计算域 = 三个客户水力模型的子汇水区并集；汇水区文件整套放进来
            MD = ROOT / "out" / "交付" / "C1汇水区_模型版"
            cp(ROOT / "out" / "units_from_models" / "c1_model_domain.geojson", D / "公共" / "计算域.geojson", done)
            for f in MD.iterdir():
                if f.is_file() and not f.name.startswith("全部22"):
                    cp(f, D / "公共" / "C1汇水区_模型版" / f.name, done)
        elif U:                                         # 客户单元版：计算域 = 三个客户单元融合；单元面与合并面整套放进来
            UD = ROOT / "out" / "交付" / "C1汇水区_客户单元"
            cp(UD / "C1_汇水区_合并.geojson", D / "公共" / "计算域.geojson", done)
            for f in UD.iterdir():
                if f.is_file():
                    cp(f, D / "公共" / "C1汇水区_客户单元" / f.name, done)
        else:
            cp(ROOT / "out" / "c1_domain.geojson", D / "公共" / "计算域.geojson", done)
            import c1_catchment_export                      # 2026-09-29 用户：C1 计算域当作 C1 汇水区，给面的导出（GeoJSON / Shapefile / KML）
            c1_catchment_export.export(D / "公共" / "C1汇水区")
        # 设施空间图层（2026-09-29 用户：结果里没有蓄水池的空间图层）在全部态导完后统一写，见文件末尾
        for st in CS["states"]:
            F = D / FOLDER[st]; E = F / "一二维"; print(f"{N} {st}")
            cp(WORK / f"{N}_{st}.inp", E / "耦合的一维" / f"{NAMES[st]}_耦合一维模型.inp", done)
            cp(C2D / f"{N}_{st}_2way.rpt", E / "耦合的一维" / f"{NAMES[st]}_耦合一维报告.rpt", done)
            cp(C2D / f"twoway_{N}_{st}.json", E / f"{NAMES[st]}_耦合运行记录.json", done)
            if not a.no_big:
                cp(C2D / f"{N}_{st}_2d.npz", E / f"{NAMES[st]}_二维水深帧与最大水深.npz", done)
                cp(C2D / "anuga_out" / f"{N}_{st}.sww", E / f"{NAMES[st]}_二维水深与流速时序.sww", done)
            cp(C2D / "tif" / f"{N}_{st}_depth_frames.tif", E / "GIS" / f"{NAMES[st]}_水深逐帧_72波段.tif", done)
            cp(C2D / "tif" / f"{N}_{st}_depth_frames.json", E / "GIS" / f"{NAMES[st]}_水深逐帧_波段时刻.json", done)
            cp(C2D / "tif" / f"{N}_{st}_depth_max.tif", E / "GIS" / f"{NAMES[st]}_全程最大水深.tif", done)
            s = next(x for x in MF["states"] if x["state"] == st)
            for f in s["frames"]:
                cp(FR / f["png"], E / "逐帧水深图" / f"t{f['t_h']:05.2f}h.png", done)
            cp(FR / s["max_png"], E / "全程最大水深.png", done)
            with open(E / f"{NAMES[st]}_积水面积时序.csv", "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh); w.writerow(["时刻_h", "积水≥0.02m面积_ha", "积水≥0.10m面积_ha", "至此最大水深_m"])
                for f in s["frames"]:
                    w.writerow([f["t_h"], f["wet_ha"], f["wet10_ha"], f["dmax_m"]])
            fac = CS.get("facilities", {}).get(st, [])
            if fac:
                with open(F / "设施.csv", "w", encoding="utf-8-sig", newline="") as fh:
                    w = csv.writer(fh); w.writerow(["编号", "类型", "流量_L/s", "库容_m3", "纬度", "经度", "说明"])
                    for x in fac:
                        w.writerow([x.get("id"), x.get("kind"), x.get("q_lps"), x.get("vol_m3"), (x.get("ll") or [None, None])[0], (x.get("ll") or [None, None])[1], x.get("how") or x.get("label")])
            z = CS["zones"]; b = CS["balance"][st]; dp = CS["deepest"][st]
            (F / "摘要.md").write_text("\n".join([
                f"# {NAMES[st]} · C1 热点群{'（客户单元版）' if U else '（模型版）' if MV else ''} · {'61.34 mm / 24 h（设计暴雨，SCS II 型）' if DS else f'{r} mm / 2 h'}", "", DESC[st], "",
                "| 指标 | 数值 |", "|---|---|",
                f"| 八个热点汇水区（并集）≥0.30 m 积水面积 | {z['C1 八个热点|catch'][st]['area_ge_0p30_ha']:,.1f} ha |",
                f"| 八个热点汇水区（并集）≥0.10 m 积水面积 | {z['C1 八个热点|catch'][st]['area_ge_0p10_ha']:,.1f} ha |",
                f"| 八个热点汇水区最大积水量 | {z['C1 八个热点|catch'][st]['peak_vol_m3']:,.0f} m³（t = {z['C1 八个热点|catch'][st]['peak_vol_t_h']} h） |",
                f"| 热点 200 m 内最大水深 | {z['C1 八个热点|200m'][st]['max_depth_m']:.2f} m |",
                f"| 全域最深点 | {dp['depth_m']} m（UTM {dp['x']}, {dp['y']}；地面 {dp['ground_m']} m，200 m 内地面中位 {dp['median_ground_200m']} m；离 {dp['near_hotspot']} {dp['near_dist_m']} m） |",
                f"| 管网冒到地面（1D→2D） | {b['1D→2D']:,} m³ |", f"| 地面回到管网（2D→1D） | {b['2D→1D']:,} m³ |",
                f"| 二维直接降雨 | {b['二维直接降雨']:,} m³ |", f"| 边界出流 | {b['边界出流（余项）']:,} m³ |", f"| {'28 h' if DS else '6 h'} 末地面上的水 | {b['域内留存（6 h 末）']:,} m³ |", "",
                "## 文件", "",
                "- `一二维/*.sww`：ANUGA 标准格式，每 5 分钟一帧，**含水位、地面高程与 x / y 向单宽流量（有流速）**",
                "- `一二维/GIS/`：15 m GeoTIFF（EPSG:32640），逐帧水深（多波段）与全程最大水深；每格取格内三角单元的最大值",
                "- `一二维/*.npz`：每 5 分钟一帧的三角单元形心水深与单宽流量、全程最大水深、单元面积与形心坐标（UTM 40N）",
                "- `一二维/耦合的一维/`：耦合时一维侧的 SWMM 模型与报告；`*_耦合运行记录.json`：逐时刻积水与交换水量、水量对账",
                "- `设施.csv`：这个态包含的设施（坐标为 WGS84）"]) + "\n", encoding="utf-8")
        # 统计值
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb = Workbook(); ws = wb.active; ws.title = "统计值"
        ws.append(["区域", "口径", "指标"] + [NAMES[s] for s in CS["states"]])
        with open(ROOT / "out" / f"c1_stats_{tag}.csv", encoding="utf-8-sig") as fh:
            for row in list(csv.reader(fh))[1:]:
                ws.append(row)
        wd = wb.create_sheet("最深点核查")
        wd.append(["态", "最大水深_m", "UTM_x", "UTM_y", "地面_m", "50m内最低地面_m", "200m内地面中位_m", "在直接降雨区", "最近热点", "距离_m"])
        for s, v in CS["deepest"].items():
            wd.append([NAMES[s], v["depth_m"], v["x"], v["y"], v["ground_m"], v["min_ground_50m"], v["median_ground_200m"], v["rain_on_grid_cell"], v["near_hotspot"], v["near_dist_m"]])
        wdf = wb.create_sheet("相邻态差值")
        wdf.append(["从", "到", "变浅面积_ha（Δh≤−5cm）", "变深面积_ha（Δh≥5cm）"])
        for d in CS["diffs"]:
            wdf.append([NAMES[d["a"]], NAMES[d["b"]], d["shallower_ha"], d["deeper_ha"]])
        wk = wb.create_sheet("统计口径")
        for k, v in CS["koujing"].items():
            wk.append([k, v])
        for w in wb.worksheets:
            for c in w[1]:
                c.font = Font(bold=True)
        (D / "统计值").mkdir(parents=True, exist_ok=True)
        wb.save(D / "统计值" / "C1统计值.xlsx"); done.append(D / "统计值" / "C1统计值.xlsx")
        # 计算设置
        L = [f"# C1 热点群{'（客户单元版）' if U else '（模型版）' if MV else ''} · {'61.34 mm / 24 h（设计暴雨，SCS II 型）' if DS else f'{r} mm / 2 h'} · 计算设置", ""] + ([
            "**客户单元版（用户 2026-09-29「都按建议，开始跑」）**：计算域 = 客户 Catchment 图层的 RB-3 + MBZ + ADGC-10 三个单元融合（20.31 km²，按截图描绘）；"
            "ADM-21 在域外 65 m 不包进来；一维管网沿用「触及计算域的连通块整块取」；按封闭汇水区、自由出流，域外地表来水不算；域外的 L2+planned 设施与 6 座现状泵站都保留"
            "（PND_C1_00 只有 6% 塘面在域内，只在一维里收水）；L2+suggested 用 v3 规则按这次的残余积水重新设计。以下各项其余不变。", ""] if U else []) + [ "参数来自 C1 参数对齐页（https://claude.ai/artifact/CkRHWiRfwvXCBvwBQuxNp4），逐项如下；「Claude 自定」是用户授权自动推进后 Claude 定的。", ""]
        for k in sorted(params, key=lambda x: (x[0], int(x[1:]))):
            v = params[k]
            L.append(f"- **{k}** {v.get('choice_label', '')}" + (f"：{v['value']}" if v.get("value") else "") + (f"（{v['note']}）" if v.get("note") else ""))
        if "L2S3" in CS["states"]:                                  # 2026-10-02 用户定：原计算域交付以 v3 为准，v2 留作历史
            L += ["", C12_V3_NOTE]
        L += ["", "## 局限", "",
              "- ADM-90 的地表汇水区未收敛（碰到窗口边），计算域外的来水算不进去",
              "- 产流统一 CN 82（Parsons 子汇水区 CN 中位 85），产流偏少",
              "- 现状泵站：RD-26 / 27 → OFL_137、PSS_98 / 99 抽出计算域是推断；PSS_20 / 21 无数据未建",
              "- 地道线性调蓄（Parsons 示意）未建：图纸定不出位置与尺寸",
              "- 计算域外的管网只算一维，冒出的水记为损失，不进二维"]
        (D / "计算设置.md").write_text("\n".join(L) + "\n", encoding="utf-8"); done.append(D / "计算设置.md")
        cp(ROOT / "docs" / "C1_资料清单.md", D / "资料清单.md", done)      # 用到的全部数据与资料（路径 + 用途），2026-09-30 用户要求放进交付目录
        (D / "readme.md").write_text("\n".join([f"# C1 热点群{'（客户单元版，计算域 RB-3 + MBZ + ADGC-10，20.31 km²）' if U else '（模型版，计算域 RB3 + Musaffah_RD09 + B_C 三个客户模型，38.10 km²）' if MV else ''} · {'61.34 mm / 24 h（设计暴雨，SCS II 型，模拟 28 h）' if DS else f'{r} mm / 2 h'} · 一二维 " + " / ".join(NAMES[s] for s in CS["states"]), "",
            *[f"- `{FOLDER[s]}/`：{DESC[s]}" for s in CS["states"]],
            "- `统计值/C1统计值.xlsx`：各热点与全域的积水统计（每项带口径）、最深点核查、相邻态差值、统计口径",
            "- `计算设置.md`：全部输入参数与局限", "- `资料清单.md`：计算用到的全部数据与资料（文件路径 + 用途）", "- `公共/`：计算域、8 个热点的汇水区、二维地形；`公共/C1汇水区/`：C1 汇水区的面（= 计算域，42.84 km²），GeoJSON / Shapefile（UTM 40N）/ KML 三种格式"]) + "\n", encoding="utf-8")
        tot = sum(p.stat().st_size for p in done if p.exists())
        print(f"→ {D}：{len(done)} 个文件，{tot / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    _rc = main()
    import c1_facility_gis                       # 各态的 设施GIS/（2026-09-29 补：蓄水池面、泵点、排水去向线、地面取水片）
    c1_facility_gis.main()
    raise SystemExit(_rc)
