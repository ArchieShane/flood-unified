# -*- coding: utf-8 -*-
"""Mussafah_00 五态的「计算设置」——一维 72 h 与一二维耦合两套输入参数、模拟时长、结果分辨率。报告与交付共用这一份。

为什么（2026-09-28）：用户要在 Mussafah_00 报告里补 L1 / L2 / L2+ 一二维运算的背景信息（模拟时长、结果分辨率等）。
能从模型文件读的现读（.inp 的 [OPTIONS]、各类对象数、耦合运行记录、DEM、帧清单），读不到的取自 tools/couple2way.py 的常量。

产物：console/data/mussafah_params.json、out/交付/Mussafah_00_61mm2h/计算设置.md
用法：python tools/mussafah_params.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import sys
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
ST1 = {"L1": "Mussafah_baseline_R61D2_72h", "L2+A": "Mussafah_R61D2_72h", "L2+A+B": "Mussafah_L2AB_R61D2_72h", "L2+B": "Mussafah_L2box_R61D2_72h"}
ST2 = {"L1": "L1", "L2+A": "L2", "L2+A+B": "L2AB", "L2+B": "L2B"}     # 二维文件名里的 L2 实际是 L2+A


def sec(t, s):
    m = re.search(rf"^\[{s}\]\n(.*?)(?=^\[|\Z)", t, re.S | re.M)
    return [ln for ln in (m.group(1).splitlines() if m else []) if ln.strip() and not ln.startswith(";")]


def opts(t):
    return {p[0]: " ".join(p[1:]) for p in (ln.split() for ln in sec(t, "OPTIONS")) if len(p) >= 2}


def dur_h(o):
    f = "%m/%d/%Y %H:%M:%S"
    return (datetime.strptime(f"{o['END_DATE']} {o['END_TIME']}", f) - datetime.strptime(f"{o['START_DATE']} {o['START_TIME']}", f)).total_seconds() / 3600


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pd = os.path.join(os.path.dirname(importlib.util.find_spec("rasterio").origin), "proj_data")
    os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = pd
    import rasterio
    from PIL import Image
    n1 = {}
    for st, f in ST1.items():
        t = (WORK / f"{f}.inp").read_text(encoding="utf-8", errors="replace")
        n1[st] = {s: len(sec(t, s)) for s in ("JUNCTIONS", "CONDUITS", "OUTFALLS", "STORAGE", "PUMPS", "ORIFICES", "WEIRS", "INFLOWS")}
        n1[st]["RULES"] = len(re.findall(r"^RULE\s", t, re.M))
        if st == "L1":
            o1 = opts(t)
    tc = (C2D / "Mussafah_L2_R61D2_2way.inp").read_text(encoding="utf-8", errors="replace"); oc = opts(tc)
    tw = {st: json.loads((C2D / f"twoway_Mussafah_{k}_R61D2.json").read_text(encoding="utf-8")) for st, k in ST2.items()}
    with rasterio.open(C2D / "dem_Mussafah_L1_R61D2.tif") as d:
        dres, Lx, Ly = d.res[0], d.width * d.res[0], d.height * d.res[1]
    res = tw["L1"]["res_m"]; ntri = tw["L1"]["n_tri"]
    mf = json.loads((ROOT / "console" / "data" / "flood2d" / "manifest.json").read_text(encoding="utf-8"))
    fr = mf["states"][0]["frames"]; png = Image.open(ROOT / "console" / "data" / "flood2d" / fr[0]["png"]).size
    stn = lambda s: {"L1": "L1", "L2+A": "L2+planned", "L2+A+B": "L2+suggested", "L2+B": "L2+B"}[s]
    cnt = lambda st: (f"{n1[st]['JUNCTIONS']} 个井、{n1[st]['CONDUITS']} 根管段、{n1[st]['OUTFALLS']} 个出水口、{n1[st]['STORAGE']} 个蓄水节点、"
                      f"{n1[st]['PUMPS']} 台泵" + (f"、{n1[st]['ORIFICES']} 个闸孔、{n1[st]['WEIRS']} 个堰" if n1[st]['ORIFICES'] else "") + f"、{n1[st]['RULES']} 条控制规则")
    P = {
        "title": "Mussafah_00 · 计算设置",
        "common": [
            ["情景", "61 mm / 2 h（DMT 手册 B 区 2 h 百年一遇 55.0 mm 的 1.11 倍）；雨型取客户模型自带的同历时设计雨型"],
            ["研究范围", "Mussafah_00 汇水区 82 ha（客户汇水区图层 Mussafah 8 个编号子区之一）"],
            ["五态", "L1 既有管网 → L2 已建成调蓄（区内 0 座，≡ L1）→ L2+planned（Parsons 2027 过渡方案）→ L2+suggested（在其上加本系统 6 座箱涵）；"
                     "L2+B 从 L2 出发、单做本系统箱涵作对照"],
            ["一维模型来源", "客户交付的 Mussafah 模型（含 Parsons 缓解措施）。L1 = 剥掉 Parsons 的临时塘、集水井和配套泵得到；既有泵站 RD-27 / RD-34 保留"],
        ],
        "oneD": [
            ["用途", "管网：溢出量、退水、排空、峰值蓄水（设施逐座装了多少）"],
            ["软件", "EPA SWMM 5.2.4"],
            ["规模（各态）", "；".join(f"{stn(s)}：{cnt(s)}" for s in ST1)],
            ["产流", f"交付模型没有子汇水区（产流是顾问在上游算好按节点注入的），按他们的参数重建：SCS 曲线法（逐汇水区 CN）+ SCS 单位线（逐汇水区 Tc），"
                     f"按 {n1['L1']['INFLOWS']} 个节点注入；在顾问原设计雨上对账，产流量 35,366 → 35,368 m³（+0.01%）"],
            ["演算", f"动力波（{o1.get('FLOW_ROUTING')}），步长 {o1.get('ROUTING_STEP')}（1 s），流量单位 {o1.get('FLOW_UNITS')}；"
                     f"地面积水开（ALLOW_PONDING {o1.get('ALLOW_PONDING')}）：冒出井盖的水在井上积着、能回落"],
            ["模拟时长", f"{dur_h(o1):.0f} h（{o1['START_TIME'][:5]} 起），五态相同"],
            ["结果分辨率", f"逐井、逐管、逐蓄水节点；每 {int(o1.get('REPORT_STEP')[3:5])} 分钟存一次全部对象（.out），共 {int(dur_h(o1) * 12)} 个时刻"],
        ],
        "twoD": [
            ["用途", "地面：最大积水面积、最大水深、逐帧推演、街面退水"],
            ["软件", "SWMM 5.2.4（一维）+ ANUGA 4.0.1（二维），同进程逐步双向耦合（tools/couple2way.py）"],
            ["一维侧", f"与上面同一张网，但地面积水关（ALLOW_PONDING {oc.get('ALLOW_PONDING')}）：冒出井盖的水交给二维"],
            ["模拟时长", f"一维从 0 h 起；二维从 {tw['L1']['start_h']:.1f} h 接入（一维开始冒水之前），L1 算到 {tw['L1']['end_h']:.0f} h，"
                         f"L2+planned / L2+suggested / L2+B 算到 {tw['L2+A']['end_h']:.0f} h；五态对比时二维指标统一截到 5 h 取峰值（L2 ≡ L1）"],
            ["耦合交换步", f"{tw['L1']['dt_c']:.0f} s"],
            ["计算域", f"矩形 {Lx / 1000:.2f} × {Ly / 1000:.2f} km = {Lx * Ly / 1e4:,.0f} ha（把汇水区及周边一并算进来；面积统计的分母是它，不是 82 ha 的汇水区）"],
            ["计算网格", f"规则三角网：{res:.0f} m × {res:.0f} m 的方格各切成 4 个三角形，共 {ntri:,} 个三角单元（每个 {res * res / 4:.2f} m²）"],
            ["地形", f"客户 5 m DTM（AUH_DTM_5m）裁到计算域，按三角单元形心取最近的 5 m 格"],
            ["糙率", "曼宁 n = 0.030，全域统一（未分下垫面）"],
            ["边界", "四边 Transmissive（照抄内侧状态）；全市那一版改成了自由出流"],
            ["降雨", "二维上不直接下雨：地面的水只来自井盖冒水。汇水区的净雨全部按节点注入一维；计算域里汇水区以外的地面在二维上没有雨"],
            ["井盖交换", f"只让一维里会冒水的井参与（各态 {min(v['n_coupled'] for v in tw.values())}–{max(v['n_coupled'] for v in tw.values())} 口）；"
                         "1D→2D：冒出的水放进井周 30 m × 30 m 的方块；2D→1D：孔口 / 淹没孔口，Cd 0.6、DN600 井盖，截断到格内实有水量"],
            ["设施", "塘、集水井、箱涵全部留在一维，DTM 不挖（同一份库容不算两次）"],
            ["统计口径", "水深取三角单元形心值，< 0.02 m 算干；面积按单元真实面积加权"],
            ["结果分辨率", f"ANUGA .sww：逐三角单元（{ntri:,} 个），每 {tw['L1']['dt_c']:.0f} s 一帧，含水位与流速（x / y 动量）；"
                           f"系统与本页的逐帧图：每 {(fr[1]['t_h'] - fr[0]['t_h']) * 60:.0f} 分钟一帧（共 {len(fr)} 帧），图像 {png[0]} × {png[1]} 像素（约 {Lx / png[0]:.0f} m / 像素）"],
            [".sww 的坐标", "UTM 40N（EPSG:32640），但文件头里的 UTM 分带写成了 -1：在 QGIS 等软件里打开时要手动指定 EPSG:32640"],
        ],
        "note": "五态共用同一张管网、同一场雨，只差设施；差值只归因于设施",
    }
    (ROOT / "console" / "data" / "mussafah_params.json").write_text(json.dumps(P, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# {P['title']}", ""]
    for k, lab in (("common", "共同"), ("oneD", "一维（72 h）"), ("twoD", "一二维耦合")):
        md += [f"## {lab}", "", "| 项 | 设置 |", "|---|---|"] + [f"| {a} | {b} |" for a, b in P[k]] + [""]
    md += [f"{P['note']}。", ""]
    d = ROOT / "out" / "交付" / "Mussafah_00_61mm2h"
    if d.exists():
        (d / "计算设置.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
