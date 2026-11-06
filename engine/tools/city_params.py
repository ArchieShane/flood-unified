# -*- coding: utf-8 -*-
"""全市 L1 / L2 的「计算设置」——一维（管网溢流）与一二维（地面积水）两套输入参数，**报告与系统共用这一份**。

为什么（2026-09-28）：用户指出报告与系统里没写模拟时长等输入参数，别人只能猜。能从模型文件读的都现读
（.inp 的 [OPTIONS]、对象数、耦合日志），读不到的（二维糙率、边界、井盖交换）取自 tools/couple2way_v2.py 的常量，
并在 "src" 里注明出处。

产物：console/data/city_params.json、out/交付/全市_61mm2h/计算设置.md
用法：python tools/city_params.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"


def opts(inp):
    t = pathlib.Path(inp).read_text(encoding="utf-8", errors="replace")
    sec = t.split("[OPTIONS]", 1)[1].split("\n[", 1)[0]
    o = {}
    for ln in sec.splitlines():
        p = ln.split()
        if len(p) >= 2 and not ln.startswith(";"):
            o[p[0]] = " ".join(p[1:])
    cnt = {}
    for s in ("JUNCTIONS", "OUTFALLS", "STORAGE", "CONDUITS", "INFLOWS"):
        m = re.search(rf"^\[{s}\]\n(.*?)(?=^\[|\Z)", t, re.S | re.M)
        cnt[s] = sum(1 for ln in (m.group(1).splitlines() if m else []) if ln.strip() and not ln.startswith(";"))
    grp = len(set(re.findall(r"^(TS_\d+_\d+)\s", t, re.M)))
    return o, cnt, grp


def dur_h(o):
    f = "%m/%d/%Y %H:%M:%S"
    a = datetime.strptime(f"{o['START_DATE']} {o['START_TIME']}", f); b = datetime.strptime(f"{o['END_DATE']} {o['END_TIME']}", f)
    return (b - a).total_seconds() / 3600


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    o1, n1, _ = opts(WORK / "City_L1_v2_p1.inp")
    oT, _, _ = opts(WORK / "City_L1_v2ts_p1.inp")
    n1d = {"JUNCTIONS": 0, "CONDUITS": 0, "OUTFALLS": 0}
    for i in range(1, 9):
        _, c, _ = opts(WORK / f"City_L1_v2_p{i}.inp")
        for k in n1d:
            n1d[k] += c[k]
    o2, n2, grp = opts(WORK / "City2D_L1.inp")
    _, n2b, _ = opts(WORK / "City2D_L2.inp")
    tw = {s: json.loads((C2D / f"twoway_City2D_{s}.json").read_text(encoding="utf-8")) for s in ("L1", "L2")}
    meta = json.loads((C2D / "pilot_City2D_L1.json").read_text(encoding="utf-8"))
    m2 = json.loads((C2D / "pilot_City2D_L2.json").read_text(encoding="utf-8"))
    log = (C2D / "run_City2D_L1.log").read_text(encoding="utf-8", errors="replace")
    rain_pct = re.search(r"直接下雨的单元 ([\d.]+)%（([\d.]+) km²）", log)
    n_ex = re.search(r"参与交换 ([\d,]+)", log)
    exc = meta["rain_excess_mm"]; tr = meta["rain_t_h"]
    import numpy as np
    ta = np.load(C2D / "City2D_L1_2d.npz")["area"].astype(float)
    nv = 3173868
    swwf = C2D / "anuga_out" / "City2D_L1.sww"
    if swwf.exists():
        try:
            from netCDF4 import Dataset
            nv = len(Dataset(swwf).dimensions["number_of_points"])
        except Exception:
            pass
    from PIL import Image
    png = Image.open(ROOT / "console" / "data" / "flood2d_city2d" / "L1" / "max.png").size
    i0 = next(i for i, v in enumerate(exc) if v > 0)
    P = {
        "title": "全市 L1 / L2 · 计算设置",
        "common": [
            ["情景", "61 mm / 2 h（与 Mussafah_00 同一场雨）"],
            ["雨型", "客户模型自带的 2 小时设计雨型"],
            ["L1", "既有管网（客户 GDB 全量重建，地面高程取客户 5 m DTM），没有塘"],
            ["L2", f"同一张网上加已建成塘（客户台账 22 座；一维 17 座、一二维 {len(m2['ponds'])} 座，见下一行）；塘作为无出口管网的末端，接法按位置与标高推断（共 {len(m2.get('links_to_ponds') or [])} 条接塘管）"],
            ["塘与台账的差异", "台账与模型的塘不完全一致（2026-09-28 核）：① Baniyas Lake 台账库容为 0，模型按 5 m DTM 算「塘轮廓内现状地形能兜住的水」取 56.2 万 m³，一维、一二维都在；② 一维里只有 17 座塘——PND_40、RZS_25、RZS_28、RZS_33、RZS_35 没有进水管，一维不含；一二维 22 座都在（地面水可直接进塘；PND_40 取 787 m³，台账深 5.87 m、库容字段 129 m³，待核）；③ 塘在模型里是深 0.30 m 的平底蓄水节点、面积 = 库容 ÷ 0.3：库容对，塘内水位与对管网的顶托不真实。塘总库容：台账 22 座 194 万 m³（Baniyas Lake 记 0）、一维 17 座 245 万 m³、一二维 22 座 251 万 m³"],
        ],
        "oneD": [
            ["用途", "管网溢流：各态管网接不住多少水、冒在哪口井"],
            ["软件", "EPA SWMM 5.2.4"],
            ["管网", f"{n1d['CONDUITS']:,} 根管、{n1d['JUNCTIONS']:,} 个井、{n1d['OUTFALLS']:,} 个出水口；按管网连通块分 8 片分别算"],
            ["产流", "SCS 曲线法 CN 82（客户值）+ SCS 单位线，汇流时间 30 min；按最近节点法把面积分给井，净雨过程线按节点注入（无子汇水区）"],
            ["演算", f"动力波（{o1.get('FLOW_ROUTING')}），步长 {o1.get('ROUTING_STEP')}，冒出井盖即损失（ALLOW_PONDING {o1.get('ALLOW_PONDING')}）"],
            ["模拟时长", f"报告与差值那一版：{dur_h(o1):.0f} h（{o1['START_TIME'][:5]} 起），报告步长 {o1.get('REPORT_STEP')[:5]}；"
                         f"逐时过程那一版：{dur_h(oT):.0f} h，每 {oT.get('REPORT_STEP')[3:5]} 分钟存一次全部井和管（逐井表、系统逐时播放用）"],
            ["结果格网", "逐井（溢流、水深）与逐管（流量、满管度）；系统里累计图按 1 km 方格汇总、逐时播放按 500 m 方格汇总，放大到街道级看逐井逐管"],
            ["说明", "30 h 窗口是建模脚本的结束时刻写错（原意 6 h），溢流 5 h 内定型；6 h 版累计溢流少约 1.2%"],
        ],
        "twoD": [
            ["用途", "地面积水：最大积水面积、水深、逐帧推演"],
            ["软件", "SWMM 5.2.4（一维）+ ANUGA 4.0.1（二维），双向耦合（tools/couple2way_v2.py）"],
            ["一维侧", f"整网一个模型：{n2['JUNCTIONS']:,} 个井、{n2['CONDUITS']:,} 根管、{n2['OUTFALLS']:,} 个出水口；L2 另有 {n2b['STORAGE']} 座塘"],
            ["产流", f"有管网覆盖的地面：用客户各子汇水区自己的面积 / CN / Tc，按 (CN, Tc) 分 {grp} 组各给一条过程线，按节点注入一维；"
                     "客户汇水区以外退回 CN 82、Tc 30 min"],
            ["直接下在二维上的雨", f"离最近井超过服务半径 {meta['R_served_m']:.0f} m 的地面：{rain_pct.group(1) if rain_pct else '—'}% 的单元（{rain_pct.group(2) if rain_pct else '—'} km²），"
                                  f"净雨按 CN 82 算、共 {exc[-1]:.1f} mm，{tr[i0]:.2f}–2.00 h 下完"],
            ["演算", f"一维动力波，步长 {o2.get('ROUTING_STEP')}，ALLOW_PONDING {o2.get('ALLOW_PONDING')}（冒出的水交给二维）；二维 ANUGA 浅水方程"],
            ["模拟时长", f"{o2['START_TIME'][:5]} → {tw['L1']['end_h']:.0f} h（{dur_h(o2):.0f} h）；二维每 5 分钟存一帧（共 72 帧）"],
            ["耦合交换步", f"{tw['L1']['dt_c']:.0f} s（共 {int(tw['L1']['end_h'] * 3600 / tw['L1']['dt_c'])} 步）"],
            ["计算域", "全市管网服务范围 ∪ 23 座塘的地表汇水区，外扩 300 m；1,234 km²"],
            ["计算网格", f"非结构三角网，{tw['L1']['n_tri']:,} 个三角单元；单元面积上限 {tw['L1']['res_m'] ** 2 / 3:.0f} m²（按 {tw['L1']['res_m']:.0f} m 网格设定：{tw['L1']['res_m']:.0f}² ÷ 3），"
                         f"实际平均 {ta.mean():.0f} m²（中位 {np.median(ta):.0f}，5%–95% 为 {np.percentile(ta, 5):.0f}–{np.percentile(ta, 95):.0f} m²），相当于边长约 {(4 * ta.mean() / 3 ** 0.5) ** 0.5:.0f} m 的等边三角形"],
            ["地形", f"客户 5 m DTM（AUH_DTM_5m）先按 {tw['L1']['res_m']:.0f} m 取平均、再赋给三角单元——地形的有效分辨率是 {tw['L1']['res_m']:.0f} m，不是 5 m（窄沟、路缘石这类小于 30 m 的起伏看不到）"],
            ["糙率", "曼宁 n = 0.030，全域统一（未分下垫面）"],
            ["边界", "自由出流（Dirichlet，边界水位 = 域内最低地面 − 1 m，水只出不进）"],
            ["井盖交换", f"{n_ex.group(1) if n_ex else '—'} 个井盖全部参与；1D→2D：冒出井盖的水分给最近 4 个单元；2D→1D：孔口 / 淹没孔口，Cd 0.6、DN600 井盖，截断到格内实有水量"],
            ["塘（L2）", "塘占地轮廓内的地面水直接进塘（塘在一维，DTM 不挖，同一份库容不算两次），塘满为止"],
            ["统计口径", "干湿阈值 0.02 m；积水面积按 ≥0.10 / ≥0.30 m 统计；面积按三角单元真实面积加权"],
            ["结果格网", f"npz 与 .sww：和计算网格相同，逐三角单元（.sww 另有 {nv:,} 个顶点）；GeoTIFF：{tw['L1']['res_m']:.0f} m 规则栅格（EPSG:32640，每格取格内三角单元的最大值）；"
                         f"系统逐帧图：{tw['L1']['res_m']:.0f} m 栅格投到经纬度，约 {png[0]:,} × {png[1]:,} 像素"],
            ["输出", "每 5 分钟一帧（72 帧）的形心水深 + 全程最大水深（npz）；ANUGA 自身的 .sww 计算时关掉了，事后由 npz 重建（只有水位，无流速）"],
            ["耗时", f"单态约 {tw['L1']['wall_s'] / 3600:.0f} h（单机）"],
        ],
        "basis_note": "一维与一二维的产流口径不同（一维统一 CN 82；一二维用客户分区 CN / Tc），两边的溢流量不能直接相减；各自内部的 L1 → L2 差值可比",
    }
    # 英文版（系统英文界面读 *_en，2026-10-28；与上面逐行对应）
    P["title_en"] = "Citywide L1 / L2 · model setup"
    P["common_en"] = [
        ["Scenario", "61 mm / 2 h (same storm as Mussafah_00)"],
        ["Storm pattern", "the 2-hour design storm pattern supplied with the client model"],
        ["L1", "existing network (fully rebuilt from the client GDB, ground levels from the client 5 m DTM), no ponds"],
        ["L2", f"the same network plus built ponds (22 in the client register; 17 in 1D, {len(m2['ponds'])} in 1D–2D, see next row); ponds act as the ends of outlet-less networks, connections inferred from position and levels ({len(m2.get('links_to_ponds') or [])} pond connections in total)"],
        ["Ponds vs register", "Ponds in the register and in the model do not fully match (checked 2026-09-28): ① Baniyas Lake has 0 capacity in the register; the model takes what the existing ground inside the pond outline can hold on the 5 m DTM, 562,000 m³, in both 1D and 1D–2D; ② only 17 ponds are in 1D — PND_40, RZS_25, RZS_28, RZS_33 and RZS_35 have no inflow pipe and are left out of 1D; all 22 are in 1D–2D (surface water can flow straight in; PND_40 taken as 787 m³, register depth 5.87 m and capacity field 129 m³, to be checked); ③ in the model a pond is a flat-bottomed 0.30 m deep storage node with area = capacity ÷ 0.3: the capacity is right, but the water level in the pond and its backwater on the network are not realistic. Total pond capacity: register 22 ponds 1.94 million m³ (Baniyas Lake recorded as 0), 1D 17 ponds 2.45 million m³, 1D–2D 22 ponds 2.51 million m³"],
    ]
    P["oneD_en"] = [
        ["Purpose", "network overflow: how much water the network cannot take in each state, and at which manholes it spills"],
        ["Software", "EPA SWMM 5.2.4"],
        ["Network", f"{n1d['CONDUITS']:,} pipes, {n1d['JUNCTIONS']:,} manholes, {n1d['OUTFALLS']:,} outfalls; run as 8 separate parts split by network connectivity"],
        ["Runoff", "SCS curve number CN 82 (client value) + SCS unit hydrograph, time of concentration 30 min; area assigned to manholes by nearest node, runoff hydrographs injected at nodes (no sub-catchments)"],
        ["Routing", f"dynamic wave ({o1.get('FLOW_ROUTING')}), step {o1.get('ROUTING_STEP')}, water spilling from a manhole is lost (ALLOW_PONDING {o1.get('ALLOW_PONDING')})"],
        ["Simulation length", f"report and difference run: {dur_h(o1):.0f} h (from {o1['START_TIME'][:5]}), report step {o1.get('REPORT_STEP')[:5]}; "
                              f"time-series run: {dur_h(oT):.0f} h, all manholes and pipes saved every {oT.get('REPORT_STEP')[3:5]} min (per-manhole tables and hourly playback in the system)"],
        ["Result grid", "per manhole (overflow, depth) and per pipe (flow, fullness); in the system the cumulative map is summed on a 1 km grid and the hourly playback on a 500 m grid; zoom to street level to see individual manholes and pipes"],
        ["Note", "the 30 h window comes from a wrong end time in the modelling script (6 h was intended); overflow settles within 5 h, and the 6 h run gives about 1.2% less cumulative overflow"],
    ]
    P["twoD_en"] = [
        ["Purpose", "surface flooding: maximum flooded area, depths and frame-by-frame evolution"],
        ["Software", "SWMM 5.2.4 (1D) + ANUGA 4.0.1 (2D), two-way coupled (tools/couple2way_v2.py)"],
        ["1D side", f"one model for the whole network: {n2['JUNCTIONS']:,} manholes, {n2['CONDUITS']:,} pipes, {n2['OUTFALLS']:,} outfalls; L2 adds {n2b['STORAGE']} ponds"],
        ["Runoff", f"ground covered by the network: each client sub-catchment's own area / CN / Tc, grouped by (CN, Tc) into {grp} groups with one hydrograph each, injected into 1D at nodes; "
                   "outside the client catchments CN 82 and Tc 30 min"],
        ["Rain falling directly on 2D", f"ground farther than the {meta['R_served_m']:.0f} m service radius from the nearest manhole: {rain_pct.group(1) if rain_pct else '—'}% of cells ({rain_pct.group(2) if rain_pct else '—'} km²), "
                                        f"effective rain by CN 82, {exc[-1]:.1f} mm in total, falling {tr[i0]:.2f}–2.00 h"],
        ["Routing", f"1D dynamic wave, step {o2.get('ROUTING_STEP')}, ALLOW_PONDING {o2.get('ALLOW_PONDING')} (spilled water handed to 2D); 2D ANUGA shallow-water equations"],
        ["Simulation length", f"{o2['START_TIME'][:5]} → {tw['L1']['end_h']:.0f} h ({dur_h(o2):.0f} h); 2D saves a frame every 5 min (72 frames)"],
        ["Coupling step", f"{tw['L1']['dt_c']:.0f} s ({int(tw['L1']['end_h'] * 3600 / tw['L1']['dt_c'])} steps)"],
        ["Model domain", "citywide network service area ∪ surface catchments of 23 ponds, buffered by 300 m; 1,234 km²"],
        ["Mesh", f"unstructured triangles, {tw['L1']['n_tri']:,} cells; maximum cell area {tw['L1']['res_m'] ** 2 / 3:.0f} m² (set from a {tw['L1']['res_m']:.0f} m grid: {tw['L1']['res_m']:.0f}² ÷ 3), "
                 f"actual mean {ta.mean():.0f} m² (median {np.median(ta):.0f}, 5%–95% {np.percentile(ta, 5):.0f}–{np.percentile(ta, 95):.0f} m²), equivalent to equilateral triangles with sides of about {(4 * ta.mean() / 3 ** 0.5) ** 0.5:.0f} m"],
        ["Terrain", f"client 5 m DTM (AUH_DTM_5m) averaged to {tw['L1']['res_m']:.0f} m first, then assigned to the cells — the effective terrain resolution is {tw['L1']['res_m']:.0f} m, not 5 m (narrow channels, kerbs and other features under 30 m are not resolved)"],
        ["Roughness", "Manning n = 0.030, uniform over the domain (no land-cover split)"],
        ["Boundary", "free outflow (Dirichlet, boundary stage = lowest ground in the domain − 1 m; water can leave but not enter)"],
        ["Manhole exchange", f"all {n_ex.group(1) if n_ex else '—'} manhole covers take part; 1D→2D: spilled water shared among the 4 nearest cells; 2D→1D: orifice / submerged orifice, Cd 0.6, DN600 cover, capped at the water actually in the cell"],
        ["Ponds (L2)", "surface water inside a pond's footprint goes straight into the pond (the pond is in 1D, the DTM is not excavated, so the same capacity is not counted twice) until the pond is full"],
        ["Statistics", "wet/dry threshold 0.02 m; flooded area counted at ≥0.10 / ≥0.30 m; areas weighted by actual triangle area"],
        ["Result grid", f"npz and .sww: same as the mesh, per triangle (the .sww also has {nv:,} vertices); GeoTIFF: {tw['L1']['res_m']:.0f} m regular raster (EPSG:32640, each cell takes the maximum of the triangles inside it); "
                        f"system frames: the {tw['L1']['res_m']:.0f} m raster projected to lon/lat, about {png[0]:,} × {png[1]:,} pixels"],
        ["Output", "centroid depth every 5 min (72 frames) + maximum depth over the run (npz); ANUGA's own .sww was switched off during the run and rebuilt from the npz afterwards (stage only, no velocities)"],
        ["Run time", f"about {tw['L1']['wall_s'] / 3600:.0f} h per state (single machine)"],
    ]
    P["basis_note_en"] = ("runoff is computed differently in 1D and 1D–2D (1D uses CN 82 throughout; 1D–2D uses the client's zoned CN / Tc), so overflow volumes from the two cannot be subtracted directly; "
                          "the L1 → L2 difference within each is comparable")
    (ROOT / "console" / "data" / "city_params.json").write_text(json.dumps(P, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# {P['title']}", ""]
    for sec, lab in (("common", "共同"), ("oneD", "一维（管网溢流）"), ("twoD", "一二维（地面积水）")):
        md += [f"## {lab}", "", "| 项 | 设置 |", "|---|---|"] + [f"| {a} | {b} |" for a, b in P[sec]] + [""]
    md += [f"**注意**：{P['basis_note']}。", ""]
    d = ROOT / "out" / "交付" / "全市_61mm2h"
    if d.exists():
        (d / "计算设置.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
