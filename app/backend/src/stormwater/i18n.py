"""中英双语 —— **在 API 边界上翻译**，领域模块一个字不改。

为什么不在 `idf.py` / `registry.py` / `pumps.py` 里加一套 `label_en` 字段：
那些模块是口径本身，往里塞第二语言会让「这个数出自哪张表」这件事变难读；
而且它们的中文串还被 `report.py` 的 CSV 与测试逐字引用，动了就得两边改。

这里的做法是：`api/main.py` 在把 dict 交给 FastAPI 之前，对**已知的那几个
展示字段**逐个过一遍 `tr()`。`tr()` 是精确匹配 + 少量正则（给带数字的动态串），
匹配不上就原样返回 —— 宁可一行中文漏到英文界面，也不要吞掉一句口径说明。

⚠ 哪些字段**不能**过 `tr()`：
  * facets 的 `value`（`未填写` 是协议值，`ScenarioRequest.matches()` 拿它比对）
  * `by_criticality` / `by_district` 的 key（同上）
  * 数据集 `key`、泵档 `key`、各种 `*_basis` 的 key
只翻 label / source / notes / reason / basis(人读的那个) / reference / caveats /
limitations / warnings / detail。

改了任一被引用的中文串，这里的键要跟着改，否则那一条会悄悄退回中文。
"""

from __future__ import annotations

import re

Lang = str  # "zh" | "en"

DEFAULT_LANG = "zh"
SUPPORTED = ("zh", "en")


def resolve_lang(lang: str | None, accept_language: str | None = None) -> Lang:
    """`?lang=` 优先，其次 `Accept-Language`，都认不出就用中文。"""
    for candidate in (lang, accept_language):
        if not candidate:
            continue
        head = candidate.split(",")[0].strip().lower()
        if head.startswith("zh"):
            return "zh"
        if head.startswith("en"):
            return "en"
    return DEFAULT_LANG


# ---------------------------------------------------------------------------
# 精确匹配表：中文原串 → 英文
# ---------------------------------------------------------------------------

EN: dict[str, str] = {
    # —— hyetograph.py：派生雨型（只看图）——
    "来源：ADM-DMT《Rainfall Analysis Report (Phase B) V04》Table 3-6 - "
    "Recommended IDF and DDF tables for use in Zone B":
        "Source: ADM-DMT Rainfall Analysis Report (Phase B) V04, Table 3-6 - "
        "Recommended IDF and DDF tables for use in Zone B",
    "官方 DDF 累计雨量 → 对数历时–对数雨量插值到时间步长 → 交替块法排列（峰值位置可调）":
        "Official DDF cumulative depth → log-duration / log-depth interpolation to the time step "
        "→ alternating-block arrangement (adjustable peak position)",
    "总雨量对齐官方 DDF；时间分配是模型派生（对数插值 + 交替块法），"
    "不是官方逐 5 分钟观测雨型，不可按官方雨型对外标注。":
        "Total depth matches the official DDF; the temporal distribution is model-derived "
        "(log interpolation + alternating block) and is not an official 5-minute observed "
        "hyetograph — do not label it as official.",
    "总雨量需大于 0": "Total depth must be greater than 0",
    "历时需在 1–1440 min 之间（DDF 表上限 24 h）":
        "Duration must be 1–1440 min (the DDF table stops at 24 h)",
    "时间步长只能取 5 / 10 / 15 min": "Time step must be 5, 10 or 15 min",
    "历时需为步长的整数倍，且至少 3 步":
        "Duration must be a whole multiple of the step, with at least 3 steps",
    "峰值位置需在 0–100% 之间": "Peak position must be between 0 and 100%",

    # —— idf.py：汇水区优先级（ADQCC Table 3-3）——
    "主干道 / 下穿 / 机场 / 涵洞（1×）": "Major roads / underpasses / airports / culverts (1×)",
    "高密度区 ≥50% 不透水（2×）": "High density ≥50% impervious (2×)",
    "低密度区 ≤50% 不透水（3×）": "Low density ≤50% impervious (3×)",
    "工业区（5×）": "Industrial (5×)",
    "开阔地 / 公园（12×）": "Open land / parks (12×)",

    # —— idf.py：迪拜 DM ——
    "迪拜 DM 导则（原 Excel 所用的表）": "Dubai DM guideline (the table used by the original Excel)",
    "⚠ 这是迪拜标准。846 个积水点位于阿布扎比，法定标准是 ADQCC WA-726-1，"
    "本数据集仅用于复现原 Excel 模型做对照，产出不可直接对外报送。":
        "⚠ This is a Dubai standard. The 846 hotspots are in Abu Dhabi, where the statutory "
        "standard is ADQCC WA-726-1. This dataset exists only to reproduce the original Excel "
        "model for comparison; its output must not be issued externally.",
    "数据来自 Stormwater model v2.xlsx 的 IDF_Dubai 工作表（105 格逐格核对一致），"
    "但真正的出处是上面那份 DM 导则——原文我们没有，无法核算法与适用范围。":
        "The figures come from the IDF_Dubai sheet of Stormwater model v2.xlsx (all 105 cells "
        "verified cell by cell), but the real source is the DM guideline above — we do not have "
        "the original text, so neither the method nor its range of validity can be checked.",
    "历时向下取档：强度偏高（流量偏保守）但历时偏短（调蓄容量偏不保守）。":
        "Duration snaps down a step: intensity comes out high (conservative for flow) but the "
        "duration comes out short (unconservative for storage volume).",
    "哈达（Hatta）地区应改用同文件 Table 0-3，本表未收录。":
        "The Hatta area should use Table 0-3 of the same document, which is not included here.",

    # —— idf.py：ADQCC 两个分区 ——
    "Zone A / Zone B 的分界线约为海拔 200 m 等高线（标准 §3.2.4，精确边界见 Figure 3-2）。"
    "Zone A 为东北部山区，Zone B 为其余平原区。":
        "The Zone A / Zone B boundary follows roughly the 200 m contour (standard §3.2.4; the "
        "exact boundary is Figure 3-2). Zone A is the north-eastern mountains, Zone B the "
        "remaining lowland.",
    "ADQCC Zone A（东北山区）": "ADQCC Zone A (north-eastern mountains)",
    "ADQCC Zone B（平原区，含阿布扎比市区）": "ADQCC Zone B (lowland, includes Abu Dhabi city)",
    "按 DEM 采样，846 个积水点中约 149 个落在 Zone A（其中艾因 AAM 148 个）。":
        "Sampled from the DEM, about 149 of the 846 hotspots fall in Zone A (148 of them in "
        "Al Ain, AAM).",
    "846 个积水点中约 676 个落在 Zone B（ADM 全部 502 个）。":
        "About 676 of the 846 hotspots fall in Zone B (all 502 of ADM among them).",
    "台账 Hotspot Network Rain Design Event 引用的 44.88 / 51.12 / 69.60 / 81.84 mm"
    " 就是本区 DDF 表的 24 小时列。":
        "The 44.88 / 51.12 / 69.60 / 81.84 mm quoted in the ledger's Hotspot Network Rain "
        "Design Event column is the 24-hour column of this zone's DDF table.",
    "ADQCC 不设 Cf 频率调整；C 直接取 Table 3-8（城市）/ Table 3-9（乡村）。":
        "ADQCC has no Cf frequency adjustment; C comes straight from Table 3-8 (urban) / "
        "Table 3-9 (rural).",

    # —— idf.py：ADQCC Table 3-2 设计重现期 ——
    "所有由雨水管网服务的区域（默认）": "All areas served by the storm network (default)",
    "主干道、宫殿、渗井及其他关键区域": "Major roads, palaces, soakaways and other critical areas",
    "次干道涵洞、无出口的泻湖与渗井、机场、港口（另需校核 50 年）":
        "Secondary-road culverts, lagoons and soakaways with no outlet, airports, ports "
        "(also check against the 50-year event)",
    "下穿道与地下车库坡道、主干道涵洞（另需校核 100 年）":
        "Underpasses and basement ramps, major-road culverts (also check against the "
        "100-year event)",

    # —— scenario.py：自定义降雨 ——
    "自定义降雨（非标准口径）": "Custom storm (non-standard basis)",
    "操作员输入 —— 不属 ADQCC 或迪拜 DM 任何一套导则":
        "Operator input — belongs to neither the ADQCC nor the Dubai DM guideline",
    "无 Cf 频率调整；设计历时取 max(tc × M, 给定历时)。":
        "No Cf frequency adjustment; design duration = max(tc × M, the given duration).",

    # —— pumps.py：分档表 ——
    # 展示名给的是通俗分级 + 口径，不是裸口径 —— 客户看不懂「8″」指什么。
    "大型泵(12吋)": 'large pump (12")',
    "中型泵(8吋)": 'medium pump (8")',
    "小型泵(6吋)": 'small pump (6")',
    # 单档（配置式）口径的展示名与性质 —— 排量是操作员在页面上设的
    "移动泵": "mobile pump",
    "页面设定": "Set on page",
    "页面设定的单泵抽排能力；在册台数取 12″/8″/6″ 三档合计":
        'Per-pump capacity set on the page; fleet count is the 12"/8"/6" total',
    "实测": "Measured",
    "铭牌折减": "Derated nameplate",
    "Xylem Godwin CD300M，5,990 USgpm = 1,360 m³/h":
        "Xylem Godwin CD300M, 5,990 USgpm = 1,360 m³/h",
    "Xylem Godwin CD200M，2,290 USgpm = 520 m³/h":
        "Xylem Godwin CD200M, 2,290 USgpm = 520 m³/h",
    "Xylem Godwin CD150M，1,770 USgpm = 403 m³/h（取整 400）":
        "Xylem Godwin CD150M, 1,770 USgpm = 403 m³/h (rounded to 400)",
    "操作员指定的单一排量，无分档依据":
        "A single capacity given by the operator, with no per-tier basis",

    # —— registry.py：剖面各字段的出处说明 ——
    "**仅当该点没有地籍数据时才用本假设**。已由 catchment_batch.py 从 Voronoi "
    "汇水单元里的已开发面积逐点反算的点会覆盖它，出处见每个点的 catchment_basis":
        "**Used only where a point has no cadastral data.** Points already back-calculated by "
        "catchment_batch.py from the developed area inside their Voronoi catchment cell override "
        "it; see each point's catchment_basis for provenance",
    "**仅当该点没有地籍数据时才用本假设**。已逐点反算的点按 ADQCC Table 3-8 "
    "分项面积加权，出处见每个点的 catchment_basis":
        "**Used only where a point has no cadastral data.** Points already back-calculated are "
        "area-weighted by the ADQCC Table 3-8 components; see each point's catchment_basis for "
        "provenance",
    "**仅当该点没有地籍数据时才用本假设**。已逐点反算的点按 ADQCC §3.2.3 "
    "「入网时间 + 管内流行时间」计算，出处见每个点的 catchment_basis":
        "**Used only where a point has no cadastral data.** Points already back-calculated follow "
        "ADQCC §3.2.3, “inlet time + travel time in pipe”; see each point's catchment_basis for "
        "provenance",
    "**仅当该点没有管网数据时才用本假设**。已由 qcap_batch.py 从管网拓扑逐点"
    "反算的点会覆盖它，出处见每个点的 qcap_basis":
        "**Used only where a point has no network data.** Points already back-calculated by "
        "qcap_batch.py from the network topology override it; see each point's qcap_basis for "
        "provenance",
    "**仅当该点没有设施数据时才用本假设**。已由 storage_batch.py 从管网库"
    "逐点反算的点会覆盖它，出处见每个点的 storage_basis":
        "**Used only where a point has no asset data.** Points already back-calculated by "
        "storage_batch.py from the network database override it; see each point's storage_basis "
        "for provenance",

    # —— registry.py：逐点积水量的归属口径（ponding_batch.py）——
    "演算 —— 点落在洼地格上，水量取该洼地（多点共用时按最近点切分）":
        "Simulated — the point sits on a depression cell; the volume is that depression's "
        "(split by nearest point where several hotspots share one)",
    "演算 —— 点所在格按 D8 汇入某个洼地，水量取该洼地里划给本点的部分":
        "Simulated — the point's cell drains into a depression under D8; the volume is the "
        "share of that depression assigned to this point",
    "演算 —— 点既不在洼地内也不汇入洼地，只报本格水量，不挂洼地":
        "Simulated — the point neither sits in nor drains to a depression; only its own cell's "
        "volume is reported, with no depression attached",
    "无 —— 该片尚未跑过 ANUGA 二维演算":
        "None — no ANUGA 2D simulation has been run for this district yet",

    # —— api/main.py：/api/hotspots 的 ponding 块 ——
    "ponding_batch.py：ANUGA 二维演算成果（15 min 一帧）按地形洼地 + D8 汇流"
    "逐点切分，积水总量取 Σ 正 ΔV（净累计进水量），积水时长取水深退到 "
    "0.01 m 以下的时刻":
        "ponding_batch.py: ANUGA 2D results (one frame per 15 min) split per point by terrain "
        "depressions plus D8 flow routing; total ponded volume is Σ positive ΔV (net cumulative "
        "inflow) and the ponding duration is the time the depth falls below 0.01 m",
    "只覆盖跑过 ANUGA 的片区，845 个点里只挂得上 18 个（2.1%）。":
        "Covers only districts with an ANUGA run — 18 of the 845 points (2.1%).",
    "重现期由该 run 自己定（Al Bateen 是 100 年一遇），"
    "与页面上选的那一级无关 —— 两者不是一回事。":
        "The return period belongs to the run itself (1-in-100-year for Al Bateen) and is "
        "unrelated to the level selected on the page — they are not the same thing.",
    "81% 的积水量落在台账里没有点的洼地上，逐点数字只可用于筛查与排序。":
        "81% of the ponded volume sits in depressions with no hotspot in the ledger; per-point "
        "figures are for screening and ranking only.",
    "20 m 计算网格会把相邻的积水坑并粗，不可作为单点设计依据。":
        "The 20 m computation grid merges adjacent ponds; not a basis for single-point design.",
    "该 run 的 SWMM 注入比预定值少 16.6 万 m³，绝对量偏小，"
    "修好之前只能看相对排序。":
        "This run's SWMM injection fell 165,575 m³ short of the prescribed volume, so absolute "
        "quantities are understated — until that is fixed, use relative ranking only.",

    # —— api/main.py：通用局限（交接文档 §3.7）——
    "使用推理公式法 + 简化三角形过程线，属筛查与排序工具，非最终设计。":
        "Uses the rational method with a simplified triangular hydrograph — a screening and "
        "ranking tool, not a final design.",
    "导则要求调蓄池与泵站的实际设计必须采用 InfoSWMM（或认可的等效软件）建模报批。":
        "The guideline requires the actual design of storage basins and pump stations to be "
        "modelled and submitted using InfoSWMM (or accepted equivalent software).",
    "推理公式法仅适用于汇水面积 < 80 公顷的汇水区，超过须拆分子汇水区或改用 SCS 过程线法。":
        "The rational method applies only to catchments under 80 ha; beyond that, split into "
        "sub-catchments or switch to the SCS hydrograph method.",
    "过程线形状系数 0.50，以及页面上那几个旋钮的默认值（每点 1 台泵、单泵 300 m³/hr、"
    "每点 1 辆罐车、单车 15 m³ / 周转 60 min）均为假设值，须替换为实测值 —— "
    "正因如此才摆在页面上让人改。":
        "The hydrograph shape factor of 0.50 and the defaults behind the knobs on the page "
        "(1 pump per hotspot at 300 m³/hr, 1 tanker per hotspot at 15 m³ and a 60-minute "
        "round trip) are all assumptions and must be replaced by measured values — which "
        "is precisely why they are exposed as editable inputs.",
    "移动泵与罐车是**串联**的一条链，不是两个可选方案：泵把积水抽进罐车、罐车运走，"
    "所以外运能力取两侧较小值，泵数与车数同时都要。默认参数下单泵 300 m³/hr、"
    "单车持续外运仅 15 m³/hr —— 喂饱一台泵要 20 辆车，瓶颈几乎总在罐车侧。":
        "Mobile pumps and tankers form a chain in **series**, not two alternatives: the "
        "pump lifts the water into a tanker and the tanker hauls it away, so the removal "
        "rate is the lower of the two sides and both the pumps and the tankers are needed "
        "at once. On the default figures one pump moves 300 m³/hr while one tanker sustains "
        "only 15 m³/hr — it takes 20 tankers to keep a single pump fed, so the bottleneck "
        "is almost always the tanker side.",
    "第一道防线只对跑过二维水动力演算的点出结论；其余点标为「无标定」，不外推、不出数。"
    "当前 845 个台账点里 18 个有标定（仅 Al Bateen 100 年一遇一场），所有水量与车队数字都只是这批点的合计。":
        "Line 1 only produces a verdict for points covered by a 2D hydrodynamic run; every "
        "other point is marked “not calibrated” and is left blank rather than extrapolated. "
        "At present 18 of the 845 ledger points are calibrated (Al Bateen, 100-year event "
        "only), so every volume and fleet figure below is the total for that subset alone.",
    # —— 积水量归属口径（`ponding_curve.PondingCurve.attribution_basis`）——
    # 这三个串由 `ponding_batch.py` 写死，是**展示文案**不是协议值，故在 API
    # 边界上翻。前端靠 `l1_basis` 分支，不靠这三个串判断。
    "点内": "in depression",
    "汇流": "routed in",
    "坡面直排": "overland",

    # —— rational.evaluate() 在水动力口径下给的几条 note ——
    "本点没有二维水动力演算覆盖，三道防线不出结论 —— "
    "积水量无从得知，光有管网能力 Qcap 算不出第三道要排多少。":
        "This point is not covered by a 2D hydrodynamic run, so no verdict is issued for any "
        "of the three lines — without a simulated ponding volume, the pipe capacity Qcap "
        "alone cannot tell you how much Line 3 has to pump.",
    "归属口径为「坡面直排」：本点未挂上任何地形洼地，积水量只是所在格的水量，"
    "是下限而非该点实际积水量。":
        "Attribution is “overland”: this point is not attached to any terrain depression, so "
        "the ponded volume is only that of its own cell — a lower bound, not the actual "
        "volume ponding at this point.",
    "演算到结束仍未退干 —— 重力自排无出路，须泵排收尾。":
        "Still wet when the simulation ended — gravity drainage has no outlet here and pumping "
        "is required to finish.",
    "移动泵按零响应时间、与地表退水同在第 0 小时起算；把到场时间计入后所需台数上升。":
        "Mobile pumps are assumed to start at hour 0 alongside the surface recession, with "
        "zero response time; counting travel and set-up time raises the number required.",
    "时限 T 默认 2 小时，与二维演算不相容 —— 实测等效排空时间常数中位 12.4 小时。"
    "车队规模对 T 是先升后降的非单调曲线，T 应作为服务水平目标显式选定。":
        "The default 2-hour window T is incompatible with the 2D results — the measured "
        "equivalent drain-down time constant has a median of 12.4 hours. Fleet size rises "
        "then falls with T rather than decreasing monotonically, so T should be chosen "
        "explicitly as a service-level target.",

    # —— api/main.py：/api/hotspots 的各项 source / rule ——
    "落在有管网数据区域内 = 该点 5 km 内有雨水管段（qcap_batch.py 逐点实算）。"
    "不用行政区判 —— 台账记为阿布扎比市的点里有 5 个在数据区之外。":
        "Inside the area with network data = a storm pipe exists within 5 km of the point "
        "(computed per point by qcap_batch.py). Not decided by municipality — 5 points recorded "
        "as Abu Dhabi in the ledger fall outside the data area.",
    "coverage_batch.py：sw_edge 缓冲成面，落图用；逐点判定以 in_coverage 为准":
        "coverage_batch.py: sw_edge buffered into polygons, for mapping only; the per-point "
        "decision is in_coverage",
    "district_batch.py：积水点落在 geoserver_ref.udm_community 的哪个社区面里，"
    "按 districtnameeng 聚合成片区。⚠ 不是台账的 area 自由文本列":
        "district_batch.py: which geoserver_ref.udm_community polygon each hotspot falls in, "
        "aggregated into districts by districtnameeng. ⚠ Not the ledger's free-text area column",
    "DEM_UAE_UTM40.tif 逐点采样高程，> 200 m 判为 Zone A":
        "Elevation sampled per point from DEM_UAE_UTM40.tif; above 200 m counts as Zone A",
    "catchment_batch.py：积水点 Voronoi 单元 ∩ 250 m 圆，按 udm_plot / "
    "udm_building / udm_roadedge / udm_landscape / udm_parking / "
    "udm_pedestrianarea 统计下垫面，C 取 ADQCC Table 3-8 分项加权":
        "catchment_batch.py: the hotspot's Voronoi cell ∩ a 250 m circle, with ground cover "
        "tallied from udm_plot / udm_building / udm_roadedge / udm_landscape / udm_parking / "
        "udm_pedestrianarea; C is area-weighted per ADQCC Table 3-8",
    "storage_batch.py：250 m 内的 pond / soakaway / sw_reservoirstructure；"
    "深度与尺寸缺的按 storage_dims.py 的标准缺省值目录补，"
    "出处见 docs/STORAGE_DIMENSION_BASIS.md":
        "storage_batch.py: pond / soakaway / sw_reservoirstructure within 250 m; missing depths "
        "and dimensions are filled from the standard default catalogue in storage_dims.py — see "
        "docs/STORAGE_DIMENSION_BASIS.md for provenance",

    # —— api/main.py：筛不到点时的三段提示 ——
    "筛选条件没有匹配到任何积水点": "No hotspot matches the current filter",
    "当前只算有管网数据的区域（艾因与西部区全部在区域外），"
    "要把它们按假设剖面算进来，请在「模型输入」里关掉该项":
        "Only areas with network data are computed at the moment (Al Ain and Al Dhafra lie "
        "entirely outside); to pull them in on assumed profiles, turn that option off under "
        "Model inputs",
    "当前片区筛选：": "Current district filter: ",

    # —— api/main.py：探活 ——
    "前后端已分离，页面由 frontend/（Vue 3 + Vite）单独部署":
        "Frontend and backend are separate; the page is deployed from frontend/ (Vue 3 + Vite)",

    # —— api/main.py：推理公式法 × 二维演算的并列对照 ——
    "推理公式法的来水量按 shape × Q_peak × D 重算（tc 历时内设计峰值过程线的"
    "体积，筛查口径），ANUGA 的积水量是整场雨演算下来实际滞留的水（含管网"
    "溢流注入与汇流范围外来水）—— 历时口径不同，比值不是误差。第一道防线"
    "现已改读演算时程，不再由推理公式法与 Qcap 推算。":
        "The rational-method inflow is recomputed as shape × Q_peak × D (the volume under "
        "the design peak hydrograph over the time of concentration — a screening figure); "
        "the ANUGA volume is the water actually impounded over the whole storm, including "
        "network overflow injected at the surface and inflow from beyond the catchment. The "
        "two cover different durations — the ratio is not an error. Line 1 now reads the "
        "simulated time series directly, and is no longer derived from the rational method "
        "or from Qcap.",

    # —— model_ponding.py：四类成因（`mechanism_key` 是协议值，只翻展示串）——
    "地形深坑/构筑物凹陷": "Terrain sink / structural depression",
    "地表汇水洼地": "Surface catchment depression",
    "管网溢流主导": "Network-overflow dominated",
    "滨海低地滞水": "Coastal lowland impoundment",

    # —— sim_sww.py：多态交付的方案名（`run.variant_label`，`sim_sww.variant_label()` 拼出）——
    "L1 既有管网": "L1 Existing network",
    "L2 已建成调蓄（≡ L1）": "L2 Built storage (≡ L1)",
    "L2+planned 规划方案": "L2+planned Planned scheme",
    "L2+suggested 规划方案 + 本系统箱涵": "L2+suggested Planned scheme + our box culverts",
    # sim_city.py：全市一二维两态
    "L2 已建成塘（22 座）": "L2 Built ponds (22)",
    # sim_native.py：AAM 原生计算结果两态
    "L2 已确认雨水塘（1 座）": "L2 Confirmed rain pond (1)",
    # sim_c1.py：C1 热点群四态
    "L1 现状管网 + 现状泵站": "L1 Existing network + existing pump stations",
    "L2 加 OP-02 移动泵": "L2 Plus OP-02 mobile pumps",
    "L2+planned Parsons 方案": "L2+planned Parsons scheme",
    "L2+suggested 转输与地面取水泵": "L2+suggested Transfer and surface-intake pumps",

    # —— model_ponding.py：run 自述 ——
    "AL BATEEN（ADM District 147 / DMT 1300）": "AL BATEEN (ADM District 147 / DMT 1300)",
    "Abu Dhabi 2022 Zone B DDF（Table 3-6）": "Abu Dhabi 2022 Zone B DDF (Table 3-6)",
    "SWMM → ANUGA 单向耦合，映射节点 12,553 个，预设溢流 288,838 m³":
        "SWMM → ANUGA one-way coupling, 12,553 mapped nodes, 288,838 m³ preset overflow",
    "最大水深 ≥ 0.30 m，或水深 ≥ 0.15 m 持续 ≥ 3 h":
        "Max depth ≥ 0.30 m, or depth ≥ 0.15 m sustained for ≥ 3 h",

    # —— model_ponding.py：使用边界 ——
    "诊断性成果，非工程审定值；5.39 m 等极值来自 DTM 深坑，须影像/现场核实。":
        "A diagnostic result, not an engineering-approved value; extremes such as 5.39 m come "
        "from DTM sinks and must be checked against imagery or on site.",
    "单向耦合（SWMM→ANUGA），无地表回流入管网的动态反馈，深坑水深偏保守（偏大）。":
        "One-way coupling (SWMM→ANUGA) with no dynamic return flow from the surface into the "
        "network, so depths in sinks come out conservative (too large).",
    "20 m 计算网格由 5 m DTM 聚合，单格深坑的绝对水深不可直接作设计依据。":
        "The 20 m computational grid is aggregated from a 5 m DTM; the absolute depth of a "
        "single-cell sink must not be used directly as a design basis.",
    "退水采用 120 min 时间尺度的概化排水项，未按实测退水过程率定。":
        "Recession uses a generalised drainage term on a 120 min time scale, not calibrated "
        "against an observed drawdown record.",
    "雨型峰值位置 40% 为假设值，非发布资料。":
        "The 40% peak position of the hyetograph is an assumption, not published data.",
    "只有 Al Bateen 一个 run，不覆盖其余片区 —— 这里没有点不等于那里不积水。":
        "Only one run exists (Al Bateen); no other district is covered — the absence of a "
        "cluster elsewhere does not mean that place does not flood.",
    # 仿真任务（simulation.py / api/simulations.py）
    "没有这个仿真任务": "Simulation not found",
    "只有草稿可以修改或删除": "Only drafts can be edited or deleted",
    "排队中或正在计算的任务不能删除，请先取消": "Queued or running runs cannot be deleted — cancel them first",
    "AR 名下的交付成果不能删除": "Deliverables owned by AR cannot be deleted",
    "只有草稿或排队中的任务可以取消": "Only drafts or queued runs can be cancelled",
    "任务状态不能这样流转": "This status change is not allowed",
    "NCM 预报降雨尚未接入（路线图）": "NCM forecast rainfall is not available yet (roadmap)",
    "设计暴雨需选一个预设，或同时给出自定义雨量与历时":
        "A design storm needs a preset, or both a custom depth and duration",
    "实测回放需指明回放的降雨事件": "An observed event replay needs the rainfall event to replay",
    "未知的雨型预设": "Unknown storm preset",
    "未知的防线": "Unknown line of defence",
    "有资源编号不存在": "One or more resource ids do not exist",
    "勾选了所选防线之后的资源（第一道不能带第二道的资源）":
        "Resources selected beyond the chosen line of defence (Line 1 cannot include Line 2 resources)",
    "未知的操作人": "Unknown user",
    "只有已完成的任务可以追加分析": "Analyses can only be added to completed runs",
    "Gap 分析要给小时数，Validation 不给": "A gap analysis needs hours; a validation does not",
    "这个任务没有结果图层": "This run has no result layers",
    "这个任务没有第二道防线信息": "This run has no Line 2 information",
    "这一态模型里没有储水设施": "This state has no storage facilities in its model",
    "这一态没有交付设施 GIS": "This state has no delivered facility GIS",
    "这个任务没有积水点": "This task has no hotspots",
    "这个任务没有应对设备配置数据": "This task has no response-equipment data",
    "这个任务没有各态积水量（ponding_ge.json）": "This task has no per-state ponding volumes (ponding_ge.json)",
    "这个任务没有管线图层（pipes.geojson）": "This task has no pipe layer (pipes.geojson)",
    "这个任务没有推演参数（params.json）": "This task has no run parameters (params.json)",
    "这个任务没有成果位置记录（outputs.json）": "This task has no output-location record (outputs.json)",
    "这个任务没有模拟积水点": "This task has no simulated ponding points",
    "没有这一帧": "No such frame",
    "这份成果算不出溢水点": "Overflow points cannot be derived from this run's outputs",
    "这份成果拆不出残留积水的成因": "Residual ponding cannot be broken down for this run's outputs",
    "这个任务不是多态成果，没有方案效果可比": "This run has no scheme states to compare",
    "这份成果没有这个方案态": "This run has no such scenario state",
    "找不到这个积水点（态或阈值变了？）": "Ponding point not found (state or thresholds changed?)",
    "找不到这个热点": "Hotspot not found",
    "没有拟建调蓄池数据": "No proposed pond data",
}

# ---------------------------------------------------------------------------
# 动态串：带数字的那几条走正则
# ---------------------------------------------------------------------------

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # idf.IdfDataset._check_return_period / storm-preset 的 422
    (re.compile(r"^(?P<ds>.+?) 无 (?P<rp>\d+) 年重现期，可选：(?P<opts>.+)$"),
     "{ds} has no {rp}-year return period; available: {opts}"),
    (re.compile(r"^(?P<ds>.+?) 无 (?P<rp>\d+) 年重现期$"),
     "{ds} has no {rp}-year return period"),
    # sim_sww.import_all：多态任务的方案名，如「4 态：L1 / L2 / L2+planned / L2+suggested」
    (re.compile(r"^(?P<n>\d+) 态：(?P<keys>.+)$"), "{n} states: {keys}"),
    # scenario.CustomStormIdf.notes[0]
    (re.compile(r"^自定义：(?P<p>[\d.]+) mm / (?P<d>[\d.]+) min，结果不可对外报送，仅用于压力测试。$"),
     "Custom: {p} mm / {d} min. Results must not be issued externally — stress testing only."),
    # rational.evaluate() 的两条 note
    (re.compile(r"^(?P<ds>.+?) 没有设计历时乘数这一概念，已忽略优先级「(?P<pr>.+?)」。$"),
     "{ds} has no design-duration multiplier, so the priority “{pr}” was ignored."),
    (re.compile(r"^设计历时 (?P<dur>[\d.]+) min 长于时限 (?P<win>[\d.]+) h —— "
                r"雨还没下完时限就到了，本点结论只能当下限看。$"),
     "The design duration of {dur} min is longer than the {win} h window — the window closes "
     "before the storm ends, so this point's verdict can only be read as a lower bound."),
    # rational.evaluate() 水动力口径下两条带数字的 note
    (re.compile(r"^峰值积水出现在 t=(?P<t>[\d.]+) min，晚于时限 (?P<win>[\d.]+) h —— "
                r"总量按全程 (?P<tot>[\d,]+) m³ 计，但时限一到地面上只有 (?P<vt>[\d,]+) m³，"
                r"其余是 T 之后才到的水：设备按全程量配，作业跨出时限。$"),
     "Peak ponding occurs at t={t} min, after the {win} h window — the total is taken over "
     "the whole run ({tot} m³), but only {vt} m³ is on the ground when the window closes; "
     "the rest arrives after T. Size the fleet for the whole-run total, but the work runs "
     "past the window."),
    (re.compile(r"^本点的二维演算是 (?P<rp>\d+) 年一遇那一场（run (?P<run>.+?)），"
                r"与所选的 (?P<sel>\d+) 年一遇不是同一场雨，第一道防线不出结论。$"),
     "The 2D run for this point is the {rp}-year event (run {run}), which is not the same "
     "storm as the selected {sel}-year event, so Line 1 issues no verdict."),
    # api/main.py 的 /api/calc 警告
    (re.compile(r"^汇水面积 (?P<a>[\d.]+) ha 超出推理公式法适用上限 80 ha，应改用 SCS 法。$"),
     "A catchment of {a} ha exceeds the rational method's 80 ha limit; use the SCS method "
     "instead."),
    # 各接口的 404 / 422
    (re.compile(r"^台账里没有编号 (?P<uid>.+)$"), "No hotspot with id {uid} in the ledger"),
    (re.compile(r"^未知 IDF 数据集 (?P<key>.+)$"), "Unknown IDF dataset {key}"),
    (re.compile(r"^未知 IDF 数据集 (?P<key>.+?)，可选：(?P<opts>.+)$"),
     "Unknown IDF dataset {key}; available: {opts}"),
    (re.compile(r"^(?P<uid>.+?) 在所选数据集下没有任何可算的重现期$"),
     "{uid} has no computable return period under the selected dataset"),
    # poi.label()：仿真溢水点的名字（POI 名是英文原名，不翻）
    (re.compile(r"^(?P<poi>.+)附近(?P<d>\d+)米的溢水点$"), "Overflow point {d} m from {poi}"),
)


#: 这些捕获组本身就是展示名，填进模板前要再翻一次。
_NESTED = frozenset({"ds", "pr"})


#: 交付目录名里的中文片区名 → 库里 / API 里的英文范围名（`run.extent` 是数据，不随界面语言）
EXTENT_EN = {"艾因": "Al Ain", "阿尔艾因": "Al Ain", "阿布扎比": "Abu Dhabi", "阿布扎比岛": "Abu Dhabi Island",
             "全市": "City-wide", "扎夫拉": "Al Dhafra"}


#: 态标签 / 点名里出现过的中文片段 → 英文（长的在前）。库里、交付 JSON 里的数据名一律英文：
#: 新数据在源头写英文，旧数据由 `en_label()` 读出时兜底、`fix_labels_en.py` 一次性改写落盘文件。
LABEL_EN: tuple[tuple[str, str], ...] = (
    ("已确认雨水塘（1 座）", "Confirmed rain pond (1)"), ("已建成调蓄（≡ L1）", "Built storage (≡ L1)"),
    ("已建成塘（22 座）", "Built ponds (22)"), ("规划方案 + 本系统箱涵", "Planned scheme + our box culverts"),
    ("现状管网 + 现状泵站", "Existing network + existing pump stations"), ("加 OP-02 移动泵", "Plus OP-02 mobile pumps"),
    ("转输与地面取水泵", "Transfer and surface-intake pumps"), ("PO塘方案(来源待确认)", "PO pond scheme (source to be confirmed)"),
    ("汇水区（模型版：", "catchment (model version: "), ("Parsons 方案", "Parsons scheme"), ("规划方案", "Planned scheme"),
    ("已建成调蓄", "Built storage"), ("既有管网", "Existing network"), ("移动泵", "Mobile pumps"),
    ("加OP-02移动泵", "Plus OP-02 mobile pumps"), ("现状管网", "Existing network"), ("现状泵站", "existing pump stations"),
    ("雨前预置泵", "Pre-placed pumps"), ("客户计划", "Client plan"), ("我们的方案", "Our scheme"), ("推荐方案", "Recommended scheme"),
    ("现状", "Existing"), ("计算域", "computational domain"), ("）", ")"),
)


def en_label(text: str | None) -> str | None:
    """数据名里的中文片段换成英文；已是英文 / 空值原样返回。「N 态：」写法也归一。"""
    if not text or not re.search("[一-鿿]", text):
        return text
    out = re.sub(r"^(\d+) 态：", r"\1 states: ", text)
    for zh, en in sorted(LABEL_EN, key=lambda kv: -len(kv[0])):
        out = out.replace(zh, en)
    return out


def tr(text: str | None, lang: Lang) -> str | None:
    """一条串。中文模式、空值、以及匹配不上的串都原样返回。"""
    if lang == "zh" or not text:
        return text
    hit = EN.get(text)
    if hit is not None:
        return hit
    for pattern, template in _PATTERNS:
        m = pattern.match(text)
        if m:
            # 嵌套的展示名（数据集 label、优先级 label）也得翻 —— 否则会出现
            # 「迪拜 DM 导则（…） has no 1000-year return period」这种半截话。
            parts = {k: tr(v, lang) if k in _NESTED else v for k, v in m.groupdict().items()}
            return template.format(**parts)
    return text


def tr_all(items, lang: Lang) -> list:
    """一串串。`notes` / `limitations` / `warnings` 都走它。"""
    return [tr(x, lang) for x in (items or [])]


def tr_values(mapping: dict, lang: Lang) -> dict:
    """只翻 value，**key 原样留着** —— key 多半是协议值。"""
    return {k: tr(v, lang) for k, v in (mapping or {}).items()}


# ---------------------------------------------------------------------------
# caveats：带统计数字，精确匹配够不着，改成两语各一套模板
# ---------------------------------------------------------------------------

CAVEATS: dict[str, tuple[str, ...]] = {
    "zh": (
        "汇水面积 A、径流系数 C、集流时间 tc 已逐点反算（catchment_batch.py）："
        "{catch_measured} 个点取自地籍与地物图层统计，"
        "{catch_undeveloped} 个点周边实测没有城市下垫面、"
        "按沙地口径计，{catch_no_landuse} 个点所在区域"
        "没有地籍数据仍走假设剖面（可在「模型输入」里改）。"
        "⚠ 这不是汇水区划分成果：边界用的是「相邻积水点 Voronoi 等分 + 250 m "
        "截断」的几何代理，可用于筛查排序，不可作为单点设计依据 —— 清单 §09 仍然缺。",

        "管网能力 Qcap 已逐点反算（qcap_batch.py）：{qcap_measured} 个点"
        "取自管网拓扑实测，{qcap_no_network} 个点实测确认周边 "
        "250 m 内无雨水管取 0，其余 {qcap_assumed}"
        " 个点所在区域没有管网数据（艾因 / 西部区未导出）仍走假设。",

        "台账的「有无排水管网」字段不可单独作准：与管网几何逐点核对后，ADM 写「无管网」的 "
        "171 个点里有 135 个、250 m 内实际有雨水管；写「有管网」的 335 个里有 28 个附近没有管。"
        "只有在完全没有管网数据的区域才回退到这个字段，并标为低置信度。",

        "第二道防线（可用调蓄）已逐点反算（storage_batch.py）：845 个点 250 m 内共找到 "
        "{assets_found} 条调蓄设施记录（调蓄池 / 渗井 / 蓄水构筑物），"
        "其中 {assets_usable} 条的容量有实测出处（主要是 pond.volume，"
        "÷ 几何面积得隐含深度中位 2.18 m，落在 ADQCC §4.14.1(b) 的 1.8–2.4 m 带内），"
        "另 {assets_assumed} 条的深度/尺寸库里没有，按公开标准的缺省值目录估算"
        "（storage_dims.py，出处见 docs/STORAGE_DIMENSION_BASIS.md），逐点标为 "
        "assumed_dimensions，与实测分列。结果：{store_measured} 个点实测有量、"
        "{store_assumed_dims} 个点为估算值、"
        "{store_no_asset} 个点实测确认 250 m 内没有设施、"
        "{store_no_coverage} 个点所在区域没有设施数据（三张设施表与管网"
        "一样只覆盖 ADM：艾因最近的渗井在 28 km 外，扎夫拉在 78 km 外）。"
        "⚠ 归属只按距离判，没有管网连通性证据；渗井多半已在服务自己的汇水面，"
        "算成额外调蓄有重复计算风险。",

        "台账坐标精度列 coord_precision_m 为 0 表示原表给了完整小数位，不代表实测精度。",

        "片区筛选按官方社区面几何判（district_batch.py），不用台账的 area 列 —— "
        "那一列是各市政区自己填的自由文本。以 Al Bateen 为例：台账里只有 3 行写了这个名字、"
        "全在艾因，而阿布扎比岛上真正的巴廷区 18 个点在台账里被并进了 area='Island'。"
        "两个 AL BATEEN 相距约 130 km，且只有 ADM 那 18 个点有实测管网数据，"
        "故片区的筛选 key 一律带市政区前缀（ADM / AL BATEEN）。",
    ),
    "en": (
        "Catchment area A, runoff coefficient C and time of concentration tc are back-calculated "
        "per point (catchment_batch.py): {catch_measured} points from cadastral and feature-layer "
        "statistics, {catch_undeveloped} points measurably have no urban ground cover nearby and "
        "are counted as sand, and {catch_no_landuse} points sit in areas with no cadastral data "
        "and still use the assumed profile (editable under Model inputs). ⚠ This is not a "
        "catchment delineation: the boundary is a geometric proxy — Voronoi bisection between "
        "neighbouring hotspots, truncated at 250 m. Usable for screening and ranking, not as the "
        "basis for a single-point design — list item §09 is still outstanding.",

        "Network capacity Qcap is back-calculated per point (qcap_batch.py): {qcap_measured} "
        "points from measured network topology, {qcap_no_network} points measurably have no storm "
        "pipe within 250 m and are set to 0, and the remaining {qcap_assumed} points sit in areas "
        "with no network data (Al Ain / Al Dhafra not exported) and still use assumptions.",

        "The ledger's “drainage network present” field is not authoritative on its own: checked "
        "point by point against the network geometry, 135 of the 171 ADM points marked “no "
        "network” do have a storm pipe within 250 m, and 28 of the 335 marked “network present” "
        "have none nearby. The field is used only where there is no network data at all, and is "
        "then flagged as low confidence.",

        "Line 2 (available storage) is back-calculated per point (storage_batch.py): "
        "{assets_found} storage asset records (ponds / soakaways / water-retaining structures) "
        "were found within 250 m of the 845 points. {assets_usable} of them have a measured source "
        "for their volume (mostly pond.volume; divided by the footprint area this implies a median "
        "depth of 2.18 m, inside the 1.8–2.4 m band of ADQCC §4.14.1(b)); the other "
        "{assets_assumed} have no depth or size in the database and were estimated from the "
        "default catalogue of published standards (storage_dims.py — see "
        "docs/STORAGE_DIMENSION_BASIS.md), tagged assumed_dimensions per point and kept separate "
        "from measured ones. Result: {store_measured} points with a measured volume, "
        "{store_assumed_dims} points estimated, {store_no_asset} points measurably with no asset "
        "within 250 m, and {store_no_coverage} points in areas with no asset data at all (like the "
        "network, the three asset tables cover only ADM: the nearest soakaway to Al Ain is 28 km "
        "away, to Al Dhafra 78 km). ⚠ Attribution is by distance alone, with no evidence of "
        "network connectivity; most soakaways already serve their own catchment, so counting them "
        "as extra storage risks double-counting.",

        "A coord_precision_m of 0 in the ledger means the source table gave full decimal places — "
        "it says nothing about survey accuracy.",

        "District filtering is decided from the official community polygons (district_batch.py), "
        "not from the ledger's area column, which each municipality fills in as free text. Take "
        "Al Bateen: only 3 rows in the ledger carry that name and all of them are in Al Ain, while "
        "the 18 points of the real Al Bateen on Abu Dhabi island were folded into area='Island'. "
        "The two AL BATEENs are about 130 km apart and only the 18 ADM points have measured "
        "network data, so district filter keys always carry the municipality prefix "
        "(ADM / AL BATEEN).",
    ),
}


def caveats(lang: Lang, **counts) -> list[str]:
    """`/api/hotspots` 的 caveats。两语各一套模板，同一批统计数字填进去。"""
    return [tpl.format(**counts) for tpl in CAVEATS[lang if lang in CAVEATS else DEFAULT_LANG]]
