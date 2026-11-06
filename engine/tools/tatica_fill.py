"""把不需要重跑的数填进 Tatica 的问询表（DMT 2026-10-19 转来），另存草稿；只写黄色格，不动他们的数据和公式。

  python tools/tatica_fill.py <问询表.xlsx> <result-017 目录> <tatica_capacity.json> <tatica_dn300.json> <输出.xlsx> [en|zh]

zh：中文版。我们填的内容直接写中文；Tatica 原文按 tools/tatica_zh.json 替换，工作表名、公式、超链接、下拉列表同步改。
需要重跑才有的格（P–T 列、Drain-down G/H、Hotspot H/I、Pump stations 里 DMT 的资产数）留空，在邮件里说明。
"""
import csv
import json
import sys
from pathlib import Path

import openpyxl

FOLDER = {  # 表里的模型名 → result-017 目录
    "Al_Riyadh": "Al_Riyadh_ponds_61mm1h_30m", "MBZ": "MBZ_61mm1h_30m", "MI-8": "MI-8_61mm1h_30m",
    "Qasr_Al_Bahr-1": "Qasr_Al_Bahr-1_61mm1h_30m", "RB-1": "RB-1_61mm1h_30m", "RB-3": "RB-3_61mm1h_30m",
    "RBE1-OF1": "RBE1-OF1_61mm1h_30m", "RD-14": "RD-14_61mm1h_30m", "RD-2": "RD-2_61mm1h_30m", "RD-A": "RD-A_61mm1h_30m",
    "RD-C": "RD-C_61mm1h_30m", "RD-F": "RD-F_61mm1h_30m", "RD-G": "RD-G_61mm1h_30m", "RD-I": "RD-I_61mm1h_30m",
    "YAS_OUTFALL": "YAS_OUTFALL_61mm1h_30m", "Al Ain": "AlAin_Model_61mm1h_250m",
}
END_H = 5.0

ANSWERS = {
    "Q1": (
        "No. Our build does not fill missing diameters with DN300 in result-017. Every pipe in the PIPELINE layer of the DMT GDB we were given "
        "(DMT_StormWater_AbuDhabi_Processed.gdb) has a PIPE_DIAMETER, and 142,634 of its 260,725 pipes (55%) are 300 mm. We traced every modelled "
        "conduit back to its GDB row: the 300 mm fallback in our builder was never used. Whether 300 mm was entered as a default before the GDB "
        "was handed over cannot be seen in the data; please could DMT confirm. The GDB has no cross-section shape field, so all pipes, including "
        "any box culverts, are modelled as circular with the GDB diameter. Related: 12.4% of GDB pipes have no invert level; at those ends the "
        "invert is set 1.61 m (median measured cover) below DTM ground."),
    "Q2": (
        "Correct, all outfalls are FREE, and this overstates capacity, so columns J-K are optimistic for the coastal models. Most coastal "
        "outfall inverts are 0.7 to 5 m below datum (e.g. RD-2 OUT_247191_2691443 at -3.95 m), i.e. below mean sea level; WA-726-1 Sec. 4.8.2 "
        "puts outfall soffits below Mean Spring Low Water (-0.7 m NADD). A FREE outfall lets water fall freely as if the sea were below the invert. "
        "WA-726-1 Sec. 4.8 sets the design check as a fixed tailwater at the outfall at +1.3 m NADD (Table 4-5 levels plus 0.5 m for sea-level "
        "rise), with non-return valves. We propose two reruns with a fixed tailwater on the coastal outfalls: 0.0 m NADD (about mean sea level) "
        "and +1.3 m NADD with flap valves. No tide time series is needed for this. We need DMT to confirm: (a) which outfalls discharge to sea "
        "(vs. lakes, ponds or land); (b) which have flap or duckbill valves; (c) that the GDB invert levels (minus 100 m) and the DTM are on NADD, "
        "as the model assumes. Al Ain and Al Riyadh are inland and not affected. Real-time tide is an operational input for the L3 system and is "
        "not needed for these planning figures."),
    "Q3": (
        "The 14 stations were left out because DMT's database lacks what is needed to model them (pump count and rate, wet-well depth, or "
        "discharge point); 2 are out of service. DMT's notes can be read in more than one way and give no units: e.g. 'Cpcty::350&80x2' may be "
        "350 + 2x80 = 510 L/s. As these are DMT asset data we would rather not set them ourselves. If DMT confirms, per station, the capacity "
        "(L/s), duty pumps, wet-well depth and discharge point, we will add them and give the capacity with and without them (column R). "
        "Station 100 (RD-2) has a known capacity (4 x about 1,059 L/s); only the wet-well depth is missing."),
    "Q4": (
        "Correct. Clipped to the domain, the Al Riyadh network has no outfall of its own in the DMT GDB (its outfalls lie outside). SWMM needs "
        "one, so the model has a placeholder outfall (X_DUMMY) that no pipe reaches. In L1 all water stays in the pipes or overflows to the 2D "
        "surface. In L2 the 13 built ponds take water through flap-valved inlet pipes and from the surface over their footprint; they have no "
        "outlet. The L2 row is filled: conveyance 0, storage = pipes + manholes + pond volume."),
    "Q5": (
        "Two parts. (1) 'Hours Capacity Limited > 0' for 94-98% of pipes is mostly a reporting effect: most pipes are flagged for 0.01 h (one "
        "routing step). Pipes limited for more than 1 h: 1.4-11.2%; more than 3 h: 0.0-0.7% (sheet 'Network in your files', Comments). "
        "(2) 'External Inflow' in the .rpt is not the rain entering the network. It is net rain injected at the manholes plus water that "
        "overflowed to the 2D surface and re-entered the network through manholes. RD-2: 2.87 million m3 rain + 2.31 million m3 re-entry = "
        "5.17 million m3. Measured against the rain injected, outfalls carried 8-82% within 5 h (RD-2 23%; Al Riyadh 0% as it has no outfall). "
        "The network is still heavily loaded: outfall capacity per served area is 1.4-26 mm/h (column M; most models below 10 mm/h) against a 61 mm/h storm. On the "
        "inflow method: injecting net rain straight into manholes has no inlet (gully) capacity limit, so the network fills faster than in reality; "
        "this tends to overstate pipe loading and understate early surface ponding. Inlet capacity limits are not modelled."),
    "Q6": (
        "Yes. Each 1D model takes every connected network block that touches the domain, so it includes network outside the 2D domain; "
        "water that overflows from manholes outside the domain is counted as lost (not routed on the surface). The capacity figures (columns "
        "J-N) refer to the whole 1D model, and column I is the area served by that whole model (sum of manhole service areas, radius 32-50 m); "
        "the part inside the 2D domain is in Comments (e.g. MBZ 104.9 km2 served, 12.1 km2 inside the domain). Several outfalls are shared by "
        "more than one model (e.g. OUT_247191_2691443 in MBZ, MI-8, RD-14 and RD-2), so capacities must not be added across catchments."),
}

ANSWERS_ZH = {
    "Q1": (
        "没有。result-017 建模时没有用 DN300 补缺失管径。我们拿到的 DMT GDB（DMT_StormWater_AbuDhabi_Processed.gdb）PIPELINE 图层里每根管都有 "
        "PIPE_DIAMETER，260,725 根管中有 142,634 根（55%）是 300 mm。我们把每根建模管段对回了它在 GDB 中的那一行：建模脚本里「缺管径补 300 mm」"
        "的分支一次都没有用到。这些 300 mm 是否是 GDB 交付前就填的默认值，数据里看不出来，请 DMT 确认。GDB 没有断面形状字段，所以所有管道"
        "（包括可能存在的箱涵）都按 GDB 管径的圆管建模。另外：GDB 中 12.4% 的管没有管底标高，这些管端的管底取 DTM 地面以下 1.61 m（实测埋深中位数）。"),
    "Q2": (
        "是的，所有出水口都是 FREE，这会高估能力，所以沿海模型的 J–K 列偏乐观。多数沿海出水口的管底在基准面以下 0.7–5 m（例如 RD-2 的 "
        "OUT_247191_2691443 为 -3.95 m），也就是低于平均海面；WA-726-1 第 4.8.2 节要求出水口管顶低于大潮平均低潮位（-0.7 m NADD）。FREE 出水口"
        "让水自由跌落，相当于假设海面低于管底。WA-726-1 第 4.8 节规定的设计校核方法是：出水口尾水固定在 +1.3 m NADD（表 4-5 潮位加 0.5 m "
        "海平面上升余量），并设止回阀。我们建议对沿海出水口做两组固定尾水位的重算：0.0 m NADD（约为平均海面）和 +1.3 m NADD 加拍门。这不需要"
        "潮位过程线。需要 DMT 确认：（a）哪些出水口排海（哪些排向湖、塘或陆地）；（b）哪些装有拍门或鸭嘴阀；（c）GDB 管底标高（减 100 m）和 "
        "DTM 是否都以 NADD 为基准（模型是这样假定的）。Al Ain 和 Al Riyadh 在内陆，不受影响。实时潮位是 L3 运行系统的输入，这些规划数值不需要。"),
    "Q3": (
        "这 14 座泵站没有建模，是因为 DMT 数据库缺少建模所需的数据（泵台数与流量、湿井深度或排放点），其中 2 座已停用。DMT 的备注可以有不止一种"
        "读法，而且没有单位：例如「Cpcty::350&80x2」可能是 350 + 2×80 = 510 L/s。这些是 DMT 的资产数据，我们不宜自行设定。如果 DMT 逐座确认"
        "能力（L/s）、工作泵台数、湿井深度和排放点，我们就把它们加进去，并给出有、无这些泵站时的能力（R 列）。100 号泵站（RD-2）能力已知"
        "（4 台 × 约 1,059 L/s），只缺湿井深度。"),
    "Q4": (
        "是的。截取到计算域后，Al Riyadh 的管网在 DMT GDB 里没有自己的出水口（它的出水口都在域外）。SWMM 至少需要一个出水口，所以模型里放了一个"
        "不连接任何管道的占位出水口（X_DUMMY）。L1 中所有的水要么留在管里，要么溢出到二维地面。L2 中 13 座已建成塘通过带拍门的进水管和塘占地内的"
        "地面进水，没有出口。L2 那一行已填：过流能力 0，蓄水量 = 管道 + 检查井 + 塘容。"),
    "Q5": (
        "分两点。（1）94–98% 的管段「Hours Capacity Limited > 0」，主要是报告口径造成的：多数管段只被标记了 0.01 h（一个计算步长）。受限超过 1 h 的"
        "管段占 1.4–11.2%，超过 3 h 的占 0.0–0.7%（见「文件中的管网」页备注）。（2）.rpt 里的「External Inflow」不是进入管网的雨水，而是注入检查井的"
        "净雨加上溢到二维地面后又经检查井流回管网的水。RD-2：净雨 287 万 m³ + 回流 231 万 m³ = 517 万 m³。以注入的净雨为分母，5 h 内经出水口排走"
        "8–82%（RD-2 23%；Al Riyadh 因没有出水口为 0%）。管网负荷依然很重：按服务面积折算的出水口能力为 1.4–26 mm/h（M 列；多数模型低于 "
        "10 mm/h），而暴雨是 61 mm/h。关于入流方式：把净雨直接注入检查井没有进水口（雨水篦子）能力的限制，管网比实际更快被灌满，倾向于高估管道"
        "负荷、低估早期地面积水。模型没有考虑进水口能力限制。"),
    "Q6": (
        "是的。每个一维模型都把与计算域相交的连通管网整块取进来，所以包含二维计算域外的管网；域外检查井溢出的水按损失计（不在地面上演进）。能力数值"
        "（J–N 列）对应整个一维模型，I 列是整个一维模型的服务面积（各检查井服务面积之和，服务半径 32–50 m）；其中在二维计算域内的部分写在备注里"
        "（例如 MBZ 服务 104.9 km²，其中 12.1 km² 在计算域内）。有几个出水口同时属于多个模型（例如 OUT_247191_2691443 同时在 MBZ、MI-8、RD-14 和 "
        "RD-2 中），所以各汇水区的能力不能相加。"),
}


def pumps_of(root, folder):
    p = Path(root) / folder / "common" / "existing_pumps.json"
    return {s["ps_id"]: s for s in json.loads(p.read_text(encoding="utf-8")).get("stations", [])}


def pond_volume(root, folder):
    p = Path(root) / folder / "common" / "built_ponds.json"
    return sum(float(x.get("vol_model_m3") or 0) for x in json.loads(p.read_text(encoding="utf-8")).get("ponds", []))


def hotspot_rows(root, folder):
    with open(Path(root) / folder / "hotspots" / "hotspot_results.csv", encoding="utf-8-sig") as f:
        return {r["积水点"]: r for r in csv.DictReader(f)}


def localise(wb, zh):
    """中文版：Tatica 原有文字按译文表替换，工作表改中文名，公式、超链接、下拉列表同步改。"""
    sheets, fmap = zh.pop("_sheets"), zh.pop("_formula")
    for ws in wb:
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if not isinstance(v, str):
                    continue
                if v in zh:
                    c.value = zh[v]
                elif v.startswith("="):
                    for a, b in sheets.items():
                        v = v.replace(f"'{a}'!", f"'{b}'!").replace(f"{a}!", f"{b}!")
                    for a, b in fmap.items():
                        v = v.replace(a, b)
                    c.value = v
        for dv in ws.data_validations.dataValidation:
            dv.formula1 = '"' + ",".join(zh.get(x, x) for x in dv.formula1.strip('"').split(",")) + '"'
    for a, b in sheets.items():
        wb[a].title = b


def main(xlsx, root, cap_json, dn_json, out, lang="en"):
    zh = lang == "zh"
    T = (lambda en, cn: cn) if zh else (lambda en, cn: en)
    cap = json.loads(Path(cap_json).read_text(encoding="utf-8"))
    wb = openpyxl.load_workbook(xlsx)

    # ---- Catchment capacity
    ws = wb["Catchment capacity"]
    for r in range(5, 22):
        m, st = ws.cell(r, 1).value, ws.cell(r, 3).value
        c = cap.get(f"{m}|{st}")
        if not c:
            continue
        grav = {k: v for k, v in c["outfall_detail"].items() if not v["pump_discharge"]}
        q_out = sum(v["q_full_cms"] or 0 for v in grav.values())
        q_pump = sum(p["curve_max_cms"] for p in c["pumps"] if p["to_outfall"])
        internal = [p for p in c["pumps"] if not p["to_outfall"]]
        pv = pond_volume(root, FOLDER[m]) if st == "L2" else 0
        store = c["pipe_vol_m3"] + c["manhole_vol_m3"] + pv
        ws.cell(r, 9).value = round(c["served_km2"], 2)
        ws.cell(r, 10).value = round(q_out + q_pump, 1)
        ws.cell(r, 11).value = round(q_out, 1)
        ws.cell(r, 12).value = round(q_pump, 2)
        ws.cell(r, 14).value = round(store, -2)
        shared = sorted({o for o, v in grav.items() if v["also_in"]})
        inn, tot = c["served_in_domain_km2"], c["served_km2"]
        notes = [T(f"Whole 1D model; {inn:.1f} of {tot:.1f} km2 served lies inside the 2D domain.",
                   f"整个一维模型；服务面积 {tot:.1f} km² 中有 {inn:.1f} km² 在二维计算域内。")]
        if grav:
            pk = sum(v.get("max_cms", 0) for v in grav.values())
            notes.append(T(f"K = full-pipe (Manning) capacity of the pipes entering the {len(grav)} outfalls, FREE outfalls; "
                           f"peak simulated in the 61 mm run {pk:.1f} m3/s.",
                           f"K = 进入 {len(grav)} 个出水口的管段按曼宁公式算的满管能力，出水口为 FREE；61 mm 计算中的模拟峰值 {pk:.1f} m³/s。"))
        else:
            notes.append(T("No outfall: the network drains nowhere (placeholder outfall X_DUMMY only, question Q4).",
                           "没有出水口：管网无处可排（只有占位出水口 X_DUMMY，见问题 Q4）。"))
        below = sum(1 for v in grav.values() if v["invert_m"] < 0)
        if below:
            notes.append(T(f"{below} of the {len(grav)} outfalls have inverts below 0 m NADD (about mean sea level): with FREE outfalls K is optimistic "
                           "(question Q2).",
                           f"{len(grav)} 个出水口中有 {below} 个管底低于 0 m NADD（约为平均海面）：出水口按 FREE 计算，K 偏乐观（见问题 Q2）。"))
        if q_pump:
            notes.append(T("L = pumps that discharge out of the model.", "L = 把水抽出模型的泵。"))
        if internal:
            qi = sum(p["curve_max_cms"] for p in internal)
            notes.append(T(f"Not in J: {len(internal)} pump(s) moving water inside the network ({qi:.2f} m3/s).",
                           f"不计入 J：{len(internal)} 台在管网内部转输的泵（{qi:.2f} m³/s）。"))
        if shared:
            notes.append(T(f"{len(shared)} of the outfalls are also in other models; do not add across catchments.",
                           f"其中 {len(shared)} 个出水口也在其他模型中；各汇水区不能相加。"))
        pp, mh = c["pipe_vol_m3"], c["manhole_vol_m3"]
        notes.append(T(f"N = pipes full {pp:,.0f} + manholes {mh:,.0f} m3 (SWMM default manhole area 1.17 m2)" + (f" + ponds {pv:,.0f} m3" if pv else "") + ".",
                       f"N = 满管 {pp:,.0f} + 检查井 {mh:,.0f} m³（SWMM 默认井面积 1.17 m²）" + (f" + 塘 {pv:,.0f} m³" if pv else "") + "。"))
        notes.append(T("P-T need new runs (see e-mail).", "P–T 列需要重算（见邮件）。"))
        ws.cell(r, 22).value = ("" if zh else " ").join(notes)

    # ---- Network in your files
    ws = wb["Network in your files"]
    for r in range(5, 21):
        m = ws.cell(r, 1).value
        c = cap.get(f"{m}|L1")
        if not c:
            continue
        ext, rain, back = c["rpt"]["External Inflow"], c["rain_injected_m3"], c["coupled_2to1_m3"]
        sh, h1, h3 = c["rpt"]["External Outflow"] / rain, c["share_limited_over_1h"], c["share_limited_over_3h"]
        ws.cell(r, 20).value = T("Partly (see Comments)", "部分（见备注）")
        ws.cell(r, 21).value = T(
            f"Figures read correctly. Two points: (1) External Inflow {ext:,.0f} m3 = net rain injected {rain:,.0f} + 2D-to-1D re-entry {back:,.0f} m3 "
            f"(coupling); share of the rain injected that left through outfalls = {sh:.0%}. (2) Hours Capacity Limited > 1 h: {h1:.1%} of pipes; "
            f"> 3 h: {h3:.1%} (most flagged pipes are limited for 0.01 h).",
            f"数值读得对。两点说明：（1）External Inflow {ext:,.0f} m³ = 注入的净雨 {rain:,.0f} + 二维回流入管 {back:,.0f} m³（耦合）；"
            f"注入的净雨中经出水口排走的比例 = {sh:.0%}。（2）Hours Capacity Limited 超过 1 h 的管段占 {h1:.1%}，超过 3 h 的占 {h3:.1%}"
            "（被标记的管段大多只受限 0.01 h）。")

    # ---- Pump stations（只填我们模型里用了的数；漏掉的不代填 DMT 的资产数）
    ws = wb["Pump stations"]
    cache = {}
    for r in range(5, 36):
        m, ps, inm = ws.cell(r, 1).value, str(ws.cell(r, 2).value), ws.cell(r, 4).value or ""
        if m not in FOLDER:
            continue
        cache.setdefault(m, pumps_of(root, FOLDER[m]))
        s = cache[m].get(ps)
        if inm.startswith("Modelled") and s:
            ws.cell(r, 12).value = round(s["n_pumps"] * s["q_each_lps"], 1)
            ws.cell(r, 13).value = s.get("well_depth_m")
            dn = s.get("discharge_node")
            ws.cell(r, 14).value = dn if dn else T(f"X_{s['unitid']} (free outfall: discharge point unknown, water leaves the model)",
                                                   f"X_{s['unitid']}（自由出水口：排放点不明，水被抽出模型）")
            ws.cell(r, 16).value = T("As modelled in result-017 (fixed rate, on/off at 50%/10% of well depth). "
                                     "DMT to confirm capacity, discharge point and storm operation.",
                                     "result-017 中的建模取值（定流量，井深 50% 开、10% 停）。请 DMT 确认能力、排放点和暴雨时的运行情况。")
        elif ps == "100":
            ws.cell(r, 16).value = T("Not modelled only because DMT's database has no wet-well depth; capacity is known (Parsons' pumps at the same site, "
                                     "4 x about 1,059 L/s). If DMT gives the wet-well depth and discharge point, we add it directly.",
                                     "没建模只是因为 DMT 数据库没有湿井深度；能力已知（Parsons 同址的泵，4 台 × 约 1,059 L/s）。"
                                     "DMT 给出湿井深度和排放点后可直接加入。")
        elif "Out" in inm:
            ws.cell(r, 16).value = T("Out of service per DMT; not modelled.", "DMT 记为停用；未建模。")
        else:
            note = ws.cell(r, 10).value or ""
            ws.cell(r, 16).value = T(f"Not modelled. DMT note: '{note}'. Units and combination not stated; DMT to confirm capacity (L/s), duty pumps, "
                                     "wet-well depth and discharge point before we add it.",
                                     f"未建模。DMT 备注：「{note}」。没有写单位和组合方式；需 DMT 确认能力（L/s）、工作泵台数、湿井深度和排放点后再加入。")

    # ---- Hotspot drainage：最近检查井、出井管径、退水时间（积水点结果表：起始 + 持续）
    ws = wb["Hotspot drainage"]
    hs_cache, near = {}, {}
    for k, c in cap.items():
        if k.endswith("|L1"):
            near[k[:-3]] = {h["hotspot"]: h for h in c.get("hotspots", [])}
    for r in range(5, 281):
        m, h = ws.cell(r, 1).value, ws.cell(r, 2).value
        if m not in FOLDER or not h:
            continue
        hs_cache.setdefault(m, hotspot_rows(root, FOLDER[m]))
        row, nh = hs_cache[m].get(h), near.get(m, {}).get(h)
        if nh:
            ws.cell(r, 10).value = nh["node"]
            ws.cell(r, 11).value = nh["out_d_mm"]
        if row:
            t0, dur = row.get("L1 水深≥0.10 m 起始 h"), row.get("L1 水深≥0.10 m 持续 min")
            if t0 in (None, "", "nan") or dur in (None, "", "nan") or float(dur) == 0:
                ws.cell(r, 12).value = 0
            else:
                t1 = float(t0) + float(dur) / 60
                ws.cell(r, 12).value = "> 5" if t1 >= END_H - 1e-6 else round(t1, 2)
        if nh:
            d, n = nh["dist_m"], nh["manholes_within_200m"]
            ws.cell(r, 13).value = T(f"Nearest exchanging manhole {d:.0f} m away; {n} exchanging manholes within 200 m. "
                                     "Column L from 5-min 2D frames (hotspot_results.csv). H-I need a rerun with per-manhole exchange output.",
                                     f"最近的参与交换的检查井距 {d:.0f} m；200 m 内有 {n} 个参与交换的检查井。L 列取自二维每 5 分钟一帧的结果"
                                     "（hotspot_results.csv）。H–I 列需要重算并输出逐井交换量。")

    # ---- Questions
    ws = wb["Questions"]
    for r in range(5, 11):
        q = ws.cell(r, 1).value
        if q in ANSWERS:
            ws.cell(r, 7).value = (ANSWERS_ZH if zh else ANSWERS)[q]

    if zh:
        localise(wb, json.loads((Path(__file__).resolve().parent / "tatica_zh.json").read_text(encoding="utf-8")))
    wb.save(out)
    print("saved", out)


if __name__ == "__main__":
    main(*sys.argv[1:7])
