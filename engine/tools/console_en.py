# -*- coding: utf-8 -*-
"""系统数据的英文字段（*_en）：把生成器写出的中文说明（设施说明、造价口径、雨型来源、L3 后果说明、导航步骤…）翻成英文，
前端英文界面用 F(o, 'why') 读 why_en，中文界面不受影响（2026-10-28 用户：「确保排水智能体系统本身也能够完美支持中英文」）。

只做两件事，都只「加」字段、不改原有字段：
  1. 生成器调用 add_en(obj)：对 TEXT_KEYS 里的中文字段补 <键>_en（steps 列表补 steps_en）；
  2. 命令行对已有的 console/data JSON 补字段（纯文本处理，不重算任何模型）：
       python -X utf8 tools/console_en.py console/data/flood2d_rda_*/rda_stats.json ...
翻译是按生成器模板写的短语表（不是机器翻译）；翻不干净（还剩中文）的不写 _en，前端退回中文，--report 会列出来。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

CJK = re.compile("[一-鿿]")
TEXT_KEYS = {"why", "label", "basis", "note", "source", "eval_basis", "level_why", "how", "from", "to", "src", "q_src", "catch_note", "rain", "crs_note", "area_note", "name", "who", "evidence", "wall", "why_type", "why_here", "why_size"}

# 待办（compliance 的 zh）
TODO = [
    ("池底低于 0 m NADD 须地下水资料（抗浮 / 降水）", "tank floor below 0 m NADD: groundwater data needed (uplift / dewatering)"),
    ("池底低于 0 m NADD 须地下水资料", "tank floor below 0 m NADD: groundwater data needed"),
    ("开发商用地外：需主管部门和业主许可", "outside developer land: authority and owner permission needed"),
    ("场地平面须经 DPM 与城市规划批准", "site plan needs DPM and urban-planning approval"),
    ("管位预留（Service Reservation）须经城市规划批准", "service reservation needs urban-planning approval"),
    ("新建排海口：EAD 写明位置的专项批准 + TCA-AD 考古报告", "new sea outfall: site-specific EAD approval + TCA-AD archaeology report"),
    ("重力优先：只有重力接不上才可设泵站，并做 NPV 比选", "gravity first: a pump station only where gravity cannot connect, with an NPV comparison"),
    ("下穿通道是否已有现状泵站", "whether the underpass already has a pump station"),
    ("与现状管线平面净距 ≥ ", "horizontal clearance to existing utilities ≥ "),
    ("与建筑基础净宽 ≥ B（图 4-1）", "clear width to building foundations ≥ B (Fig. 4-1)"),
    ("用地许可", "land-use permit"),
    # L3 未派原因（rda_l3 的 why）
    ("班组 / 资源已占满或派了也赶不上，求解器未选", "crews / resources fully used, or would arrive too late; not selected by the solver"),
    ("1.5 km 内无排放去处，罐车不够", "no discharge point within 1.5 km and not enough tankers"),
    ("行程不可达（积水断路或路网缺段）", "unreachable (road flooded or missing road-network link)"),
]
LAND = [("无地块记录", "no plot record"), ("未建地块", "vacant plot"), ("公共用地", "public land"), ("公园", "park"),
        ("二级b", "tier 2b"), ("二级", "tier 2"), ("一级", "tier 1")]

# 整句对照（图层元数据 source / note 等，一条一句）
EXACT = {
    "Parsons_Model Data Export/26092026 各模型子汇水区，裁到 C1 计算域": "Parsons_Model Data Export/26092026 sub-catchments of each model, clipped to the C1 domain",
    "模型之间边界有重叠；RUNOFF_VOL / Q_(MAX) 是 Parsons 导出时的结果字段，对应哪一场雨未注明": "Boundaries overlap between models; RUNOFF_VOL / Q_(MAX) are result fields in the Parsons export, and the storm they refer to is not stated",
    "按客户 QGIS 截图描绘（照片配准到 flood 库 catchment.catchment），非客户原始几何；2026-09-29": "Traced from the client's QGIS screenshots (photo georeferenced to flood DB catchment.catchment), not the client's original geometry; 2026-09-29",
    "客户邮件附件 FW: Hot Spots 506 的 4 个汇水区 KMZ（Al Wathba / Mu / Shahamah_Zayed / island）": "The 4 catchment KMZs attached to the client email FW: Hot Spots 506 (Al Wathba / Mu / Shahamah_Zayed / island)",
    "名称来历见 label_src：name = KMZ 自带；point = 用落在面里的点名（推断）；attr = 规划属性": "Name origin in label_src: name = from the KMZ; point = name of a point falling inside the polygon (inferred); attr = planning attribute",
    "客户 flood 库 catchment.catchment": "Client flood DB catchment.catchment",
    "管网汇水域（244 个出水口反向广搜 + 最近节点服务面积），P=61 mm，CN=82": "Network catchments (reverse breadth-first search from 244 outfalls + nearest-node service areas), P = 61 mm, CN = 82",
    "shapefile 无 .prj，按 EPSG:32640 处理": "shapefile has no .prj; treated as EPSG:32640",
    "地表：5 m DTM 填洼 + D8，出口为热点周边 50 m；管网：热点周边 200 m 内溢水井的全部上游井、各自 46 m 内最近的地面": "Surface: 5 m DTM, depression-filled + D8, outlet = 50 m around the hotspot; network: all manholes upstream of the overflowing manholes within 200 m of the hotspot, each with the nearest ground within 46 m",
    "61 mm / 2 h，SCS CN 82 → 净雨 23.53 mm": "61 mm / 2 h, SCS CN 82 → effective rain 23.53 mm",
    "61 mm · SCS CN 82 → 净雨 23.53 mm": "61 mm · SCS CN 82 → effective rain 23.53 mm",
    "从客户 5 m DTM 按 D8 划的地表汇水区；不含管网转输，平地上边界是指示性的": "Surface catchments delineated with D8 on the client 5 m DTM; network transfers not included, boundaries on flat ground are indicative",
    "why_Mussafah_R61D2.json（L2+A 只写模型读出的事实，不替顾问编理由）": "why_Mussafah_R61D2.json (for L2+A only facts read from the model are given; no reasons are invented on the consultant's behalf)",
    "客户 22 个水力模型的 Catchment 图层并集（OneDrive_1_9-26-2026）": "Union of the Catchment layers of the client's 22 hydraulic models (OneDrive_1_9-26-2026)",
    "Parsons_Model Data Export.rar → Critical Hotspots（SHP + PDF 图纸，2026-09-10 版）": "Parsons_Model Data Export.rar → Critical Hotspots (SHP + PDF drawings, 2026-09-10 issue)",
    "塘的库容是面积 × 1 m 的占位值（Depth 都是 1.0）": "Pond capacity is a placeholder of area × 1 m (Depth is 1.0 everywhere)",
    "客户 flood 库 stormwater_drainage.pond —— informigration 无一为 EXISTING，挂在道路工程下（2021–2023 建成），属 L2 CAPEX": "Client flood DB stormwater_drainage.pond — none is EXISTING in informigration; they sit under road projects (built 2021–2023) and count as L2 CAPEX",
    "客户 catchment.catchment 的 39 个多边形是建模研究范围，不是全市排水分区；塘不在其中不等于没有汇水区": "The 39 polygons of the client's catchment.catchment are modelling study areas, not citywide drainage zones; a pond outside them still has a catchment",
    "按客户QGIS截图描绘（配准到flood库catchment.catchment，非原始几何）；v2外扩20%；v3北段对齐街区、南段对齐高速立交路面；v4与MBZ/ADGC-10融合（切重叠、补空洞）；2026-09-29": "Traced from the client's QGIS screenshots (georeferenced to flood DB catchment.catchment, not the original geometry); v2 buffered by 20%; v3 north part aligned to blocks, south part to the motorway interchange; v4 merged with MBZ / ADGC-10 (overlaps cut, gaps filled); 2026-09-29",
    "客户 flood 库 stormwater_drainage.sw_reservoirstructure —— informigration 15/20 为 EXISTING，属 L1": "Client flood DB stormwater_drainage.sw_reservoirstructure — 15 of 20 are EXISTING in informigration; counted as L1",
    "catchments_sw（客户汇水区）+ 耦合 DEM 外框": "catchments_sw (client catchments) + coupled DEM extent",
    "v3：第一张照片描 + 外扩 20 % + 对齐道路（用户确认过）": "v3: traced from the first photo + 20 % buffer + aligned to roads (confirmed by the user)",
    "照片描，面积按 Shape_Area 定": "traced from the photo, area set by Shape_Area",
    # scope.geojson（Mussafah_00 试点范围）
    "同属穆萨法，**本次没有建模**": "Also part of Mussafah; **not modelled in this run**",
    "本次一二维耦合覆盖的就是这一块": "This is the area covered by this 1D–2D coupled run",
    "客户一维模型的 833 个节点各取 100 m 缓冲的并集。管网铺得比汇水区多边形大 —— 新增设施是按**积水节点**布的，所以有几座落在汇水区多边形之外。":
        "Union of 100 m buffers around the 833 nodes of the client 1D model. The network extends beyond the catchment polygon — new facilities are placed at **flooding nodes**, so a few fall outside the catchment polygon.",
    "ANUGA 网格 15 m（源 DTM 5 m），258,332 个三角单元。「最大淹没面积」的分母是这个域，不是汇水区。":
        'ANUGA mesh 15 m (source DTM 5 m), 258,332 triangles. The denominator of "maximum flooded area" is this domain, not the catchment.',
    "客户台账记 148.8 ha，几何只有 82.0 ha（0.55×）——**以几何为准**": "The client register says 148.8 ha, the geometry only 82.0 ha (0.55×) — **the geometry is used**",
    "Mussafah_00 一维管网范围": "Mussafah_00 1D network extent",
    "Mussafah_00 二维计算域": "Mussafah_00 2D model domain",
    # l2plus.geojson（Mussafah_00 的 L2+ 设施）
    "Parsons 顾问设计": "designed by the consultant Parsons", "报告原文 + 从交付模型读出": "report text + read from the delivered model",
    "直壁": "vertical walls", "本系统设计": "designed by this system", "本系统算出，依据逐条可复核": "computed by this system; every basis can be checked",
    "报告原文：「在开放空间挖 2 m 深、3:1 边坡的临时塘，12 寸移动泵在雨峰把重力管网的水抽进塘」（2027 雨季前实施）":
        'Report text: "temporary ponds dug 2 m deep with 3:1 side slopes in open spaces; 12-inch mobile pumps lift water from the gravity network into the ponds at the rain peak" (to be in place before the 2027 rainy season)',
    "报告原文：「proposed within available open spaces, **subject to site suitability**」——逐座的选址理由报告未给":
        'Report text: "proposed within available open spaces, **subject to site suitability**" — the report gives no site-by-site reasons',
    "§4.14.1(p)：管内底应高出塘底 ≥ 0.30 m": "§4.14.1(p): pipe invert should be ≥ 0.30 m above the pond floor",
    "§4.14.1(b)：进出水高差宜 1.8–2.4 m": "§4.14.1(b): inlet–outlet level difference preferably 1.8–2.4 m",
    "§4.14.1(f)：须设应急溢流": "§4.14.1(f): an emergency spillway is required",
    "§4.14.1(d)：需留 5 m 检修通道": "§4.14.1(d): a 5 m maintenance access is required",
    "WA-726-1 第三版 §4.2：建成区内或紧邻建成区**不得设置开敞式塘**": "WA-726-1 3rd edition §4.2: **no open ponds** within or next to built-up areas",
    "§4.2 B：离线调蓄应为混凝土箱涵": "§4.2 B: offline storage should be a concrete box culvert",
    "§4.2：容积另加 5% 沉沙": "§4.2: add 5% to the volume for sediment",
}

N = r"[\d.,]+"
# (正则, 替换)，按顺序套；长句在前
RULES = [
    # ---- 设施说明（rda_console / rb01_console / export_rda_61_full 的模板）
    (r"热点表 Plan A 预置移动泵：吸水井 = 热点最近的井，排到沿管往下游第一口在 L(\d) 一维结果里不溢流的井",
     r"Hotspot-table Plan A pre-positioned mobile pump: suction well = manhole nearest the hotspot; discharges to the first manhole downstream that does not overflow in the L\1 1D result"),
    (r"下游第一口不溢流的井", "first non-overflowing manhole downstream"),
    (r"现有 RD-A 泵站（全市管网 (\S+)，注记 (.+?)）：湿井 (\S+) → 出水口 (\S+)；泵曲线最大流量取客户 (\S+) 曲线",
     r"Existing RD-A pump station (citywide network \1, annotated \2): wet well \3 → outfall \4; maximum flow from the client \5 curve"),
    (r"客户 SewerGEMS 情景「Temporary Intervention」（inp (\S+)）里的泵：吸水井 (\S+) → 出水口 (\S+)；设施台账状态「设计中」、挂 (\S+) 等热点（可信度弱）。同情景的泵 (\S+) 判「只是方案」，未纳入",
     r'Pump in the client SewerGEMS scenario "Temporary Intervention" (inp \1): suction well \2 → outfall \3; facility register status "in design", linked to hotspots such as \4 (weak link). Pump \5 in the same scenario was judged "proposal only" and is not included'),
    (r"地面取水泵：二维里热点附近退不掉的低洼积水（井口比坑底高，管网收不进去），抽到海边出水口 (\S+)",
     r"Surface intake pump: low-lying water near the hotspot that does not drain in 2D (manhole rims sit above the pit floor, so the network cannot take it), pumped to sea outfall \1"),
    (r"；手工补点", "; point added manually"),
    (r"O-(\S+) 在塘 (\S+) 里 → 打进塘", r"O-\1 lies in pond \2 → pumps into the pond"),
    (r"O-(\S+) 自由出流（离开系统）", r"O-\1 free outfall (leaves the system)"),
    (r"模型 PUMP_FLOW = ", "model PUMP_FLOW = "),
    (r"按 (\d+) 英寸 (" + N + r") L/s（Claude 自定）", r"taken as \1-inch \2 L/s (set by Claude)"),
    (r"PO 塘方案（来源待确认）：塘底 = 轮廓内 DTM 最低点、(" + N + r") m 深",
     r"PO pond scheme (source unconfirmed): pond floor = lowest DTM point inside the outline, \1 m deep"),
    (r"；塘面在计算域外，只在一维收水", "; pond surface outside the 2D domain, takes water in 1D only"),
    (r"新塘（OSM 未标注）", "new pond (not mapped in OSM)"), (r"新塘（明确空地）", "new pond (clearly open land)"),
    (r"，重力接 (\S+)；挖方 (" + N + r") m³", r", gravity-fed from \1; excavation \2 m³"),
    (r"ADM-(\d+) (" + N + r") m 内溢流第 (\d+) 多的井（L2\+planned 溢流 (" + N + r") m³，峰 (" + N + r") m³/s）：(\d+) 台 (\d+) 英寸固定泵转输到塘 (\S+)（(" + N + r") m；可用 (" + N + r") m³，分给这一处 (" + N + r") m³，余 (" + N + r") m³）",
     r"Manhole with the no. \3 largest overflow within \2 m of ADM-\1 (L2+planned overflow \4 m³, peak \5 m³/s): \6 × \7-inch fixed pumps transfer to pond \8 (\9 m; available \g<10> m³, allocated here \g<11> m³, remaining \g<12> m³)"),
    (r"比周边低 (" + N + r") m，最大积水 (" + N + r") m³ / (" + N + r") ha：(\d+) 台 (\d+) 英寸地面取水泵，按 (" + N + r") h 抽完，打进塘 (\S+)（(" + N + r") m；可用 (" + N + r") m³，分给这一处 (" + N + r") m³，余 (" + N + r") m³），塘满即停",
     r"\1 m lower than its surroundings, max ponding \2 m³ / \3 ha: \4 × \5-inch surface intake pumps emptying it within \6 h into pond \7 (\8 m; available \9 m³, allocated here \g<10> m³, remaining \g<11> m³); stop when the pond is full"),
    (r"重力管 (\S+) → (\S+)（Ø(" + N + r") m，进口 = 井口 − (" + N + r") m，拍门）", r"Gravity pipe \1 → \2 (Ø\3 m, inlet = rim − \4 m, flap valve)"),
    (r"重力管 井 (\S+) → 地下池，长 (\S+) m，管径 (\S+) m", r"Gravity pipe from manhole \1 → underground tank, length \2 m, diameter \3 m"),
    (r"重力管 井 (\S+) → 新塘（n (" + N + r")、拍门）", r"Gravity pipe from manhole \1 → new pond (n \2, flap valve)"),
    (r"井边泵站 (\S+) 台 → 压力管 (\S+) m DN(\d+) → 地下池", r"Manhole-side pump station, \1 pumps → rising main \2 m DN\3 → underground tank"),
    (r"井边泵站 (\S+) 台 → 压力管 (\S+) m → 地下池", r"Manhole-side pump station, \1 pumps → rising main \2 m → underground tank"),
    (r"截流泵 井 (\S+) → 新塘", r"Interception pump at manhole \1 → new pond"),
    (r"截流泵 井 (\S+) → 海边出水口 (\S+)（尾水 (" + N + r") m）", r"Interception pump at manhole \1 → sea outfall \2 (tailwater \3 m)"),
    (r"；压力管 DN(\d+) × (\S+)，扬程 (\S+) m", r"; rising main DN\1 × \2, head \3 m"),
    (r"新塘（(泵送|重力接)，井 (\S+)，热点 (\S+)）", lambda m: f"New pond ({'pumped' if m[1] == '泵送' else 'gravity-fed'}, manhole {m[2]}, hotspot {m[3]})"),
    (r"(重力进水地下调蓄池|井边泵站 \+ 地下调蓄池)（截流井 (\S+)，热点 (\S+)，开挖 (\S+) m）",
     lambda m: f"{'Gravity-fed underground storage tank' if m[1].startswith('重力') else 'Manhole-side pump station + underground storage tank'} (interception manhole {m[2]}, hotspot {m[3]}, excavation {m[4]} m)"),
    (r"泵站 → 新建排海口：截流井 ", "Pump station → new sea outfall: interception manholes "),
    (r" → 湿井 → ", " → wet well → "),
    (r"(\d+) 台 × ", r"\1 pumps × "),
    (r"（流速 (" + N + r") m/s）", r" (velocity \1 m/s)"),
    (r"→ 新建排海口 ", "→ new sea outfall "),
    (r"；场地 (" + N + r") m²", r"; site \1 m²"),
    (r"地面取水口（(地面洼坑|隧道引道)）→ 地面泵站 ", lambda m: f"Surface intake ({'surface depression' if m[1] == '地面洼坑' else 'tunnel approach'}) → surface pump station "),
    (r"离道路隧道 (" + N + r") m；用户 (\S+) 选 \(b\)，作为新建泵站计入方案，待客户确认是否有现状泵站",
     r"\1 m from a road tunnel; user chose (b) on \2: counted as a new pump station in the scheme, client to confirm whether one already exists"),
    (r"地面泵站（按小泵站过可建性检查，DOM-B11）：(\d+) 个取水口", lambda m: f"Surface pump station (buildability checked as a small pump station, DOM-B11): {m[1]} intake" + ("" if m[1] == "1" else "s")),
    (r"地面泵站：(\d+) 个取水口", lambda m: f"Surface pump station: {m[1]} intake" + ("" if m[1] == "1" else "s")),
    (r"，压力管 (\S+) m → ", r", rising main \1 m → "),
    (r"→ 压力管 (\S+) m DN", r"→ rising main \1 m DN"),
    (r"；用地 ", "; land use "),
    (r"；?占位造价 (\S+) M USD", r"; placeholder cost USD \1 M"),
    # 造价口径（括号内 / cost items）
    (r"压力管 (WA\S+?) 外推、排口 (WA\S+?) 按面积外推（第三卷 p349）", r"rising main \1 extrapolated, outfall \2 extrapolated by area (Vol. III p349)"),
    (r"压力管 (WA\S+?)、排口 (WA\S+?)（第三卷 p349）", r"rising main \1, outfall \2 (Vol. III p349)"),
    (r"压力管 (WA\S+?) 外推（第三卷 p349）", r"rising main \1 extrapolated (Vol. III p349)"),
    (r"压力管 (WA\S+?)（第三卷 p349）", r"rising main \1 (Vol. III p349)"),
    (r"；其余占位", "; others placeholder"),
    (r"(调蓄池|池体) 350 USD/m³、重力管、排空泵：占位单价", "tank 350 USD/m³, gravity pipe, emptying pump: placeholder unit rates"),
    (r"压力管、排海口按 WA-726 第三卷 p349 单价", "rising mains and sea outfalls at WA-726 Vol. III p349 rates"),
    (r"压力管按 WA-726 第三卷 p349 单价", "rising mains at WA-726 Vol. III p349 rates"),
    (r"池体、泵站、湿井：占位", "tank, pump station, wet well: placeholder"),
    (r"泵站、湿井、取水井、重力连接管：占位", "pump station, wet well, intake wells, gravity connections: placeholder"),
    (r"泵站、湿井、取水井：占位", "pump station, wet well, intake wells: placeholder"),
    (r"泵站、湿井、重力连接管：占位", "pump station, wet well, gravity connections: placeholder"),
    (r"^占位单价$", "placeholder unit rates"),
    (r"；有条件可建，待办：", "; conditionally buildable, to do: "),
    (r"；有条件可建$", "; conditionally buildable"), (r"^有条件可建$", "conditionally buildable"),
    # 造价分项名
    (r"^重力进水地下调蓄池$", "Gravity-fed underground storage tank"),
    (r"^井边泵站 \+ 地下调蓄池$", "Manhole-side pump station + underground storage tank"),
    (r"^地面泵站 → 新建排海口$", "Surface pump station → new sea outfall"),
    (r"^泵站 → 新建排海口$", "Pump station → new sea outfall"),
    (r"^(\d+) 项一维措施 \+ (\d+) 处地面泵$", r"\1 1D measures + \2 surface pumps"),
    # 造价说明
    (r"^占位造价：只用于方案比较，不是预算。压力管、排海口用 WA-726 第三卷附录 L 单价，其余为占位单价；未含 NPV（运维、更换），NPV 对比表随成果说明给出。$",
     "Placeholder costs for comparing schemes only, not a budget. Rising mains and sea outfalls use the WA-726 Vol. III Appendix L rates, everything else placeholder rates; NPV (O&M, replacement) is not included — the NPV comparison table comes with the results note."),
    (r"^v1 已作废（单一出水口、未按手册和空间约束），造价只作历史对照。$",
     "v1 is withdrawn (single outfall, manual and spatial constraints not applied); its cost is kept for historical comparison only."),
    (r"^61 mm / 1 h 推荐方案：当前方案（按 96 mm 优化）以 AECOM 1 h 设计雨为控制雨剪枝（删 (\d+)、换 (\d+)），地面泵站全部保留。占位造价，只用于方案比较。$",
     r"61 mm / 1 h recommended scheme: the current scheme (optimised for 96 mm) pruned with the AECOM 1 h design storm as the controlling storm (\1 removed, \2 replaced); all surface pump stations kept. Placeholder costs, for comparing schemes only."),
    # 雨型来源 / 判据口径
    (r"客户 Mussafah 模型「10 年一遇含 CC (\S+) h」雨型等比放大", r"Client Mussafah model 10-yr incl. CC \1 h storm pattern, scaled proportionally"),
    (r"AECOM RD-A 模型「10yrs\.\+20%CC 1 h」设计雨原样（3 分钟一段）", 'AECOM RD-A model "10yrs.+20%CC 1 h" design storm as supplied (3-minute steps)'),
    (r"剔除 ADM-80 的 DTM 填 0 区（(\S+)）", r"excluding the DTM zero-filled area at ADM-80 (\1)"),
    (r"^全域$", "whole domain"),
    # ---- C1 设施说明（c1_design_l2s* / c1_console 的模板：why / how / label / p.from / p.to / p.src）
    (r"片内井在 L2\+planned 里溢流 (" + N + r") m³（≥ 这一处最大积水量 (" + N + r") m³ 的一半）：管网接不住，用 (\d+) 台 (\d+) 英寸固定泵从 (\S+) 转输到规划塘 (\S+?)（",
     r"Manholes in this patch overflow \1 m³ in L2+planned (≥ half of the \2 m³ peak ponding here): the network cannot take it, so \3 × \4-inch fixed pumps transfer from \5 to planned pond \6 ("),
    (r"地表浅积水（比周边低 (" + N + r") m）（片内井溢流 (" + N + r") m³ 不到一半）：",
     r"Shallow surface ponding (\1 m below its surroundings; manhole overflow in the patch \2 m³, less than half): "),
    (r"比周边低 (" + N + r") m 的洼地 / 地道，积水主要来自地表（片内井溢流 (" + N + r") m³ 不到一半）：",
     r"Depression / underpass \1 m below its surroundings, water mainly from the surface (manhole overflow in the patch \2 m³, less than half): "),
    (r"(\d+) 台 (\d+) 英寸地面取水泵，按 (" + N + r") h 抽完 (" + N + r") m³，", r"\1 × \2-inch surface intake pumps emptying \4 m³ within \3 h, "),
    (r"经地面管直接打进规划塘 (\S+?)（", r"via surface pipes straight into planned pond \1 ("),
    (r"经地面管排到规划塘 (\S+?)（", r"via surface pipes to planned pond \1 ("),
    (r"(" + N + r") m；可用 (" + N + r") m³，分给这一处 (" + N + r") m³，余 (" + N + r") m³", r"\1 m; available \2 m³, allocated here \3 m³, remaining \4 m³"),
    (r"(" + N + r") m，L2\+planned 末满度 (" + N + r")%", r"\1 m, L2+planned end fill \2%"),
    (r"（塘满即停）", " (stop when the pond is full)"),
    (r"管网接不住（溢流 (" + N + r") m³），但 (" + N + r") km 内没有有余量的塘、(" + N + r") km 内没有出水口：箱涵 (" + N + r") m³ 接在 (\S+)",
     r"The network cannot take it (overflow \1 m³), but there is no pond with spare capacity within \2 km and no outfall within \3 km: box culvert \4 m³ connected at \5"),
    (r"无出口管网（(" + N + r") 节点）的最低井", r"lowest manhole of an outlet-less network (\1 nodes)"),
    (r"pond_link 认出的进水管", "inflow pipe identified by pond_link"),
    (r"从塘 (\S+) 抽", r"pumped from pond \1"),
    (r"（压力管网终点，(" + N + r") m）", r" (end of the rising-main network, \1 m)"),
    (r"（路线网找不到终点 → 最近的规划塘（Claude 自定），(" + N + r") m）", r" (no end found in the route network → nearest planned pond (set by Claude), \1 m)"),
    (r"规划塘 ", "planned pond "),
    (r"出水口 (\S+)（推断，(\S+) 相距 (" + N + r") m）", r"outfall \1 (inferred, \3 m from \2)"),
    (r"客户 GDB 压力管", "client GDB rising main"),
    (r"抽出计算域（新建出水口）", "pumped out of the domain (new outfall)"),
    (r"最近的现状出水口（(" + N + r") km 内无不溢流的下游井）", r"nearest existing outfall (no non-overflowing downstream manhole within \1 km)"),
    (r"湿井 (\S+)（(" + N + r") m）", r"wet well \1 (\2 m)"),
    (r"(" + N + r") L/s（PDF 版无对应泵站，取同类中位数）", r"\1 L/s (no matching station in the PDF version; median of similar stations)"),
    (r"PDF 版 (\S+)（相距 (" + N + r") m）单泵", r"PDF version \1 (\2 m away), single pump"),
    (r"Parsons 同址泵 PUMP_FLOW（", "Parsons co-located pump PUMP_FLOW ("),
    (r"^停用（客户备注 Out of Service）$", 'out of service (client note "Out of Service")'),
    (r"客户备注 ", "client note "),
    (r"^未建泵$", "pump not built"),
    (r"^无数据：不建泵，列为局限$", "no data: no pump modelled, listed as a limitation"),
    (r"^8 个热点（C1 的 7 个 \+ ADM-487）的地表 \+ 管网汇水区外扩 300 m；ADM-90 地表汇水区未收敛$",
     "Surface + network catchments of 8 hotspots (C1's 7 + ADM-487), buffered by 300 m; the ADM-90 surface catchment did not converge"),
    # ---- l2plus.geojson 本系统箱涵的选址 / 规模说明
    (r"^选址不是挑地块挑出来的，是\*\*按这场雨的积水分布聚类\*\*出来的：这一片有 (\d+) 个积水节点、合计缺口 (" + N + r") m³（占本区总缺口 (" + N + r")%），箱涵就设在这一片的重心附近，离积水点近，水才进得来。再按用地挪了 (" + N + r") m，落到\*\*不压路面、建筑、地块、市政设施用地和铁路红线\*\*的空地上（避开了道路红线）。空地是按客户 makani 图层判的，\*\*权属与地下管线没有核\*\*。$",
     r"The site was not picked plot by plot; it comes from **clustering the ponding of this storm**: this cluster has \1 flooding nodes with a combined shortfall of \2 m³ (\3% of the area's total), and the box culvert sits near the cluster's centroid, close to the flooding points so that water can actually get in. It was then moved \4 m for land use onto open land that **does not sit on roads, buildings, plots, utility land or the railway corridor** (road reserves avoided). Open land is judged from the client's makani layer; **ownership and underground utilities have not been checked**."),
    (r"^起点是\*\*这一片实测的缺口量 (" + N + r") m³\*\*，再乘三个因子：①×1\.5 聚类系数（覆盖峰值同时性与进水口吞吐不足，\*\*这一条是假设，手册里没有，未经标定\*\*）；②×1\.05 沉沙（§4\.2：容积另加 5% 沉沙）；③×1\.15 超高 0\.30 m —— \*\*超高是要空着的，不计入可用库容\*\*。所以设计可用库容 (" + N + r") m³，模型里的几何容积 (" + N + r") m³。$",
     r"The starting point is **the measured shortfall of this cluster, \1 m³**, multiplied by three factors: ① ×1.5 clustering factor (covers peak coincidence and limited inlet capacity; **this one is an assumption, not in the manual and not calibrated**); ② ×1.05 sediment (§4.2: add 5% to the volume for sediment); ③ ×1.15 freeboard 0.30 m — **freeboard stays empty and is not counted as usable storage**. Hence a usable design storage of \2 m³ and a geometric volume in the model of \3 m³."),
    # ---- L3 后果说明（rda_l3 的 level_why）
    (r"(tunnel|ambulance|hospital|metro) (" + N + r") m 内，路面 / 建筑处 ≥(" + N + r") m", r"\1 within \2 m, ≥\3 m on roads / at buildings"),
    (r"≥(" + N + r") m 路面 (" + N + r") m²", r"≥\1 m on roads over \2 m²"),
    (r"只在空地 / 未建地块 / 公园", "only on open land / vacant plots / parks"),
    (r"≥(" + N + r") m 碰到 (\d+) 栋建筑", r"≥\1 m reaching \2 buildings"),
    (r"碰到 (\d+) 栋建筑", r"reaches \1 buildings"),
    (r"淹到输电变电站", "floods a power substation"),
    (r"已建成地块内 ≥(" + N + r") m", r"≥\1 m inside built plots"),
    (r"；客户热点重要度 ", "; client hotspot criticality "),
]
RULES = [(re.compile(a), b) for a, b in RULES]

# ---- 导航步骤（rda_routes 的路线说明）
ORD = {"一": "1st", "二": "2nd", "三": "3rd", "四": "4th", "五": "5th", "六": "6th"}
DIR = {"东南": "southeast", "东北": "northeast", "西南": "southwest", "西北": "northwest", "东": "east", "西": "west", "南": "south", "北": "north"}
LR = {"左": "left", "右": "right"}
STEP_RULES = [
    (r"^从 (.+?) 出发$", r"Depart from \1"),
    (r"^到达 (.+?)，目的地位于道路(左|右)侧$", lambda m: f"Arrive at {m[1]}; destination on the {LR[m[2]]}"),
    (r"，然后再", ", then "), (r"然后再", ", then "), (r"，然后", ", then "), (r"然后", ", then "),
    (r"在环岛处，从第(.)个出口转出", lambda m: f"At the roundabout, take the {ORD.get(m[1], m[1])} exit"),
    (r"在交通信号灯处，于岔路口靠(左|右)行驶进入 ", lambda m: f"At the traffic lights, keep {LR[m[1]]} at the fork onto "),
    (r"在交通信号灯处，沿 (.+?) 向(左|右)转", lambda m: f"At the traffic lights, turn {LR[m[2]]} onto {m[1]}"),
    (r"在交通信号灯处调头", "At the traffic lights, make a U-turn"),
    (r"在 (.+?) 处调头", r"Make a U-turn at \1"),
    (r"调头", "Make a U-turn"),
    (r"沿 (.+?) 往回行驶", r"head back along \1"),
    (r"在岔路口靠(左|右)侧行驶", lambda m: f"Keep {LR[m[1]]} at the fork"),
    (r"靠(左|右)侧行驶", lambda m: f"Keep {LR[m[1]]}"),
    (r"向(左|右)转进入 ", lambda m: f"Turn {LR[m[1]]} onto "),
    (r"向(左|右)急转沿 (.+?) 行驶", lambda m: f"Turn sharp {LR[m[1]]} onto {m[2]}"),
    (r"向(左|右)急转", lambda m: f"Turn sharp {LR[m[1]]}"),
    (r"向(左|右)前方沿 (.+?) 行驶", lambda m: f"Bear {LR[m[1]]} onto {m[2]}"),
    (r"向(左|右)前方行驶", lambda m: f"Bear {LR[m[1]]}"),
    (r"(左|右)转继续沿 (.+?) 行驶", lambda m: f"Turn {LR[m[1]]} to stay on {m[2]}"),
    (r"(左|右)转沿 (.+?) 行驶", lambda m: f"Turn {LR[m[1]]} onto {m[2]}"),
    (r"(左|右)转", lambda m: f"Turn {LR[m[1]]}"),
    (r"继续向前沿 (.+?) 行驶", r"Continue straight on \1"),
    (r"继续向前行驶", "Continue straight"),
    (r"向前沿 (.+?) 行驶", r"Go straight on \1"),
    (r"向(东南|东北|西南|西北|东|西|南|北)沿 (.+?) 行驶", lambda m: f"Head {DIR[m[1]]} on {m[2]}"),
    (r"向(东南|东北|西南|西北|东|西|南|北)行驶", lambda m: f"Head {DIR[m[1]]}"),
    (r"通过(左|右)侧 (.+?) 出口", lambda m: f"Take exit {m[2]} on the {LR[m[1]]}"),
    (r"通过(左|右)侧出口", lambda m: f"Take the exit on the {LR[m[1]]}"),
    (r"通过出口", "Take the exit"),
    (r"进入环岛", "Enter the roundabout"),
    (r"，?进入高速公路，?", " onto the motorway "),
    (r"，进入 ", " onto "), (r"进入 ", "onto "),
    (r"，前往 ", " towards "), (r" ?前往 ", " towards "),
    (r"，", ", "),
]
STEP_RULES = [(re.compile(a), b) for a, b in STEP_RULES]
PUNC = [("（", " ("), ("）", ")"), ("；", "; "), ("：", ": "), ("，", ", "), ("、", ", "), ("「", '"'), ("」", '"'), ("。", ". ")]


def _punc(s):
    for a, b in PUNC:
        s = s.replace(a, b)
    return re.sub(r" {2,}", " ", s).replace(" )", ")").strip()


def zh2en(s):
    """中文说明 → 英文；翻不干净返回 None。"""
    if not isinstance(s, str) or not CJK.search(s):
        return None
    if s in EXACT:
        return EXACT[s]
    out = s
    for a, b in TODO:
        out = out.replace(a, b)
    for a, b in LAND:
        out = out.replace(f"'{a}'", f"'{b}'")
    for r, b in RULES:
        out = r.sub(b, out)
    out = _punc(out)
    return None if CJK.search(out) else out


def step_en(s):
    if not isinstance(s, str) or not CJK.search(s):
        return s
    out = s
    for r, b in STEP_RULES:
        out = r.sub(b, out)
    out = re.sub(r" {2,}", " ", out).strip()
    out = out[:1].upper() + out[1:]
    out = re.sub(r", then ([A-Z])", lambda m: ", then " + m[1].lower(), out)
    return None if CJK.search(out) else out


def add_en(o, miss=None):
    """递归补 *_en（只加不改）；miss 收集翻不干净的原文。返回补了几处。"""
    n = 0
    if isinstance(o, dict):
        for k in list(o.keys()):
            v = o[k]
            if k in TEXT_KEYS and isinstance(v, str) and CJK.search(v):
                e = zh2en(v)
                if e is not None:
                    if o.get(k + "_en") != e:
                        o[k + "_en"] = e; n += 1
                elif miss is not None:
                    miss.append(v)
            elif k == "steps" and o.get("source") == "local" and o.get("steps_en"):
                pass                                                   # 本地路径服务自带英文导航（同一次求解），不用短语表覆盖
            elif k in ("steps", "rules", "notes") and isinstance(v, list) and any(isinstance(x, str) and CJK.search(x) for x in v):
                e = [(step_en if k == "steps" else (lambda x: x if not (isinstance(x, str) and CJK.search(x)) else zh2en(x)))(x) for x in v]
                if all(x is not None for x in e):
                    if o.get(k + "_en") != e:
                        o[k + "_en"] = e; n += 1
                elif miss is not None:
                    miss += [x for x, y in zip(v, e) if y is None]
            elif not k.endswith("_en"):
                n += add_en(v, miss)
    elif isinstance(o, list):
        for x in o:
            n += add_en(x, miss)
    return n


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    args = argv if argv is not None else sys.argv[1:]
    report = "--report" in args
    files = [a for a in args if not a.startswith("--")]
    tot_miss = {}
    for f in files:
        p = pathlib.Path(f)
        d = json.loads(p.read_text(encoding="utf-8"))
        miss = []
        n = add_en(d, miss)
        if n:
            indent = 1 if p.read_text(encoding="utf-8").startswith("{\n") else None
            p.write_text(json.dumps(d, ensure_ascii=False, indent=indent), encoding="utf-8")
        print(f"{p}: +{n} 处 _en，{len(miss)} 处未翻")
        for m in miss:
            tot_miss[m] = tot_miss.get(m, 0) + 1
    if report and tot_miss:
        print("—— 未翻（前端退回中文）：")
        for m, c in sorted(tot_miss.items(), key=lambda x: -x[1])[:60]:
            print(f"  {c} × {m[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
