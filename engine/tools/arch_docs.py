# -*- coding: utf-8 -*-
"""排水智能体技术架构说明：中英文 Word + PPT 四个文件（2026-10-28 用户：「word 版本和 ppt 版本，需要各支持中英文两套」）。

内容与网页版（claude.ai artifact「排水智能体技术架构」）一致：分层架构、三道防线（RD-A）、一二维模型参与的六个环节、技术组合流程、
优化技术、选型与实现细节、三个反馈回路与局限。架构图、流程图用 matplotlib 按语言各画一张，嵌进 Word / PPT。
用法：python -X utf8 tools/arch_docs.py [--out out/交付/系统架构说明_20261028]
"""
from __future__ import annotations

import argparse
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
FONT = {"zh": "Microsoft YaHei", "en": "Segoe UI"}
COL = {"l1": "#3d5a66", "l2": "#1d7f5f", "l3": "#b5482b", "water": "#0f6e86", "water_soft": "#e2f0f3", "sand": "#b9893f", "sand_soft": "#f6eedf",
       "ai": "#6a4fb3", "ai_soft": "#ece7f7", "ink": "#152224", "muted": "#56696c", "line": "#c9d6d8", "panel": "#ffffff", "g_soft": "#eef3f4",
       "o_soft": "#e4f3ec", "r_soft": "#f8e6e0"}

# ------------------------------------------------------------------ 内容（中英文）
T = {
 "zh": {
  "title": "排水智能体技术架构",
  "sub": "会思考的排水系统 · 阿布扎比城市内涝防治 · 以 RD-A 汇水区为例",
  "date": "2026-10-28",
  "lead": "一二维水动力模型是核心引擎，但系统靠的是多项技术的组合：数据治理把客户数据变成可建模的资产，知识与约束把设计手册和空间条件变成规则，一二维模型给出“会淹到哪里”，优化技术给出“建什么、派什么”，三道防线把规划、雨前和雨中串成闭环。",
  "status": [("已实现", "在 RD-A 上跑通、有交付"), ("原型", "跑通但数据或功能不全"), ("设计中", "设计已审定，未实施")],
  "h": ["一、分层架构", "二、三道防线在技术上怎么实现（RD-A）", "三、一二维水动力模型参与的六个环节", "四、技术怎么组合：从数据到决策", "五、按问题结构选优化方法",
        "六、每项技术的选型和实现细节", "七、三个反馈回路与当前局限"],
  "layers": [
   ("应用与交付", ["系统页面", "接口", "智能体动作", "交付包", "飞渡 DTS"]),
   ("三道防线决策", ["L1 现状评估", "L2 规划优化", "L3 应急调度"]),
   ("分析与评估", ["积水点提取", "判据 A–D", "后果分级", "热点对照", "造价与全寿命成本"]),
   ("模型与仿真", ["一二维耦合水动力\nSWMM + ANUGA", "产流 SCS-CN", "洼坑代理", "代理模型（图神经网络）"]),
   ("知识与约束", ["WA-726 约束登记表\n（820 条）", "领域规则", "空间约束（Makani）", "设施本体"]),
   ("数据治理", ["ArcGIS UN 路径", "开源路径", "治理台账 · 区域检查\n一二维验证"]),
   ("数据源", ["DMT 资产库", "顾问模型", "DTM 5 m", "Makani", "客户热点表", "路网与重点单位"])],
  "loops_short": ["回路①：一二维模型验证治理效果", "回路②：L3 兜不住的残余回到 L2", "回路③：二维结果训练代理模型"],
  "lines": [
   ("L1 现状：管网本身能扛多少", "现有管网、泵站、塘在各场雨下哪里积水、积多深、多久退",
    "治理后数据建模 → 一二维耦合计算 → 积水点提取 → 与客户热点对照",
    "计算域 3.18 km²、65,210 个三角单元；9 场雨 + 3 场设计雨；61 mm / 1 h 下 11 个热点仅 1 个达标"),
   ("L2 规划：建什么最省又有效", "在约束内选出一组新设施，让客户热点和高后果积水点达标、成本最低",
    "三层约束候选生成 → 一维批量筛选 → 分组贪心 + 剪枝 → 地面泵站设计 → 二维验收 → 造价与 NPV；深度强化学习替代搜索段（设计中）",
    "三档：手册口径约 0.94 亿、AECOM 61 mm / 1 h 约 1.28 亿、96 mm 极端雨约 1.53 亿美元（占位单价）"),
   ("L3 应急：雨中派谁去哪", "L2 之后仍然积水的点，用泵、罐车、班组能否按时处置",
    "残余积水点 → 后果分级 → 处置包退水重演 → 滚动 MILP 派单 → 受淹路网行程 + 在线卡车路线",
    "AECOM 设计雨下最终方案：21 个残余点，17 个自然退干、2 个 L3 按时，出动 3 台泵")],
  "lines_lab": ["问题", "技术", "RD-A"],
  "lines_note": "三道防线是对同一份二维结果逐级使用：L1 的结果是 L2 优化的基线，L2 方案的残余积水就是 L3 调度的输入，L3 处置不了的再回到 L2 当新的优化目标。",
  "roles_head": ["环节", "用它做什么", "RD-A 的实例"],
  "roles": [
   ("① 数据治理验证", "用治理前、治理后的数据各建一次模型，比较排水能力和积水，证明治理有效、找出没治好的地方", "治理前出水口排水 0、雨后 4 h 只排掉 27–34 %；治理后为 AECOM 的 97–103 %、63–64 %"),
   ("② L1 现状基线", "现状在各场雨下的积水范围、水深、退水过程", "9 场雨 × L1 / L2 / L2+planned，共 27 个算例"),
   ("③ L2 方案验收", "优化在一维和代理上做，最终必须二维验收判据 A–D；发现一维看不到的地面积水", "只做管网侧措施的方案只过 3 个热点，补地面泵站后过 10 个"),
   ("④ 代理模型训练", "二维结果作为真值，训练代理模型学习“一维 + 洼坑 → 二维判据”", "已有约 180 个二维算例可用（RD-A 128 个）"),
   ("⑤ L3 输入", "每个残余积水片的水量过程线，决定派几台泵、多久抽干", "处置包按过程线做退水重演"),
   ("⑥ 交付与展示", "水深动画、积水点、.sww、飞渡 DTS 三维", "61 mm / 1 h 全套成果，4 个状态 × 2 种雨型")],
  "flow": {"src": "数据源", "gov": "数据治理", "mod": "一二维建模\nSWMM + ANUGA", "ana": "积水点 · 判据\n后果分级", "kn": "WA-726 · 领域规则\n空间约束",
           "l2a": "三层约束候选", "l2b": "一维筛选\n贪心 + 剪枝", "l2c": "地面泵站\n二维验收", "rl": "深度强化学习\n（设计中）",
           "l3a": "处置包\n退水重演", "l3b": "滚动 MILP\n派单", "l3c": "受淹路网\n在线卡车路线", "L2": "L2 规划", "L3": "L3 应急",
           "r1": "① 验证治理", "r2": "② 兜不住的回到 L2", "r3": "③ 二维验收结果\n作训练样本"},
  "opt_head": ["方法", "用在哪", "为什么适合", "状态"],
  "opt": [
   ("分组贪心 + 回头剪枝", "L2 汇水区内方案组合（现行）", "措施之间相互影响强，只能靠模拟评估；贪心每轮按“每美元降低的溢流”挑，剪枝删掉贡献小的、换成更省的", "已实现"),
   ("混合整数规划（MILP，HiGHS）", "L3 滚动派单；雨前预置选址；全市方案组合", "处置效果可预先算成“处置包”，资源约束是线性的；求解器给出可证明的最优解，秒级、可解释", "已实现（全市组合：设计中）"),
   ("CP-SAT（OR-Tools）", "L3 车辆与班组排班", "车次、换班这类排程约束用约束规划更自然；装了 OR-Tools 时启用，否则退回贪心轮转", "原型"),
   ("最短路径（Dijkstra）", "L3 行程时间矩阵", "本地路网 41.5 万段，积水 ≥0.45 m 断路、浅水减速；按卡车实测乘 1.70", "已实现"),
   ("在线路径服务（ArcGIS Online）", "L3 派单的实际路线与导航", "卡车出行模式、积水多边形障碍、逐段导航；结果缓存，断网退回本地路网", "已实现"),
   ("代理模型（图神经网络）", "L2 快速评估", "一维筛选的单项效果作先验，共享特征学习叠加修正；2,315 个样本时验证集 R² 0.98", "原型"),
   ("深度强化学习（掩码 PPO + MCTS）", "L2 汇水区内方案设计", "组合空间巨大、相互作用非线性；一个图策略用于任意汇水区；二维主动学习防止代理模型被钻空子", "设计中")],
  "tech_head": ["技术点", "选型", "实现细节", "代码位置"],
  "tech": [
   ("数据治理", "ArcGIS Pro 3.7 · Utility Network Expanded（arcpy.un、untools、UDMS）；开源并行路径（GeoPandas / Shapely）", "全市 DMT 统一治理到第 14 轮，可追踪到出水口 90.0 %；每个汇水区出区域检查（RD-A 99.9 %、网络错误 0）；顾问模型按“DMT 为底、顾问补参数”融合", "tools/exp_gov/、tools/dgov/"),
   ("一维管网", "EPA SWMM 5.2（swmm-toolkit）", "AECOM SewerGEMS 导出换算公制；动力波；多进程批量，一次 RD-A 一维约 50 s", "tools/rda_build.py、tools/l2opt_screen.py"),
   ("二维地表", "ANUGA（有限体积、非结构三角网）", "网格参数 15 m（单元 ≤75 m²），高程取 5 m DTM；每 5 分钟存一帧水深与流速；雨停后再算 10 h", "tools/couple2way_v2.py"),
   ("一二维耦合", "自研双向耦合 · Docker 镜像", "每 60 s 交换：检查井溢流进二维，地面积水按井口过流回管网；二维地面泵站按取水口抽排；多算例排队并行，监控容错", "tools/couple2way_v2.py、tools/l2opt_run2d.py"),
   ("产流", "SCS-CN + SCS 单位线", "每地块按 Makani 不透水比例合成 CN（不透水 95，透水用客户 CN）", "tools/rda_catchments.py"),
   ("积水点", "自研连片算法", "最大水深 ≥ 阈值的单元按三角网相邻连片，扣塘占地，面积 ≥100 m²、持续 ≥0.5 h；阈值可在页面调", "swbrain/ponding.py"),
   ("验收判据", "判据 A–D（建议值，待客户确认）", "热点 200 m 内：A 无 ≥0.30 m 积水；B 浅水面积 × 时间 ≤ 客户计划的 10 %；C 雨后 6 h 退到 0.10 m 以下；D 全域别处不变糟", "tools/l2opt_eval2d.py"),
   ("约束知识", "WA-726 约束登记表 + 领域规则 + Makani 空间数据", "820 条手册约束结构化；候选生成时逐条判定，输出合规检查表（通过 / 需审批 / 缺数据）", "swbrain/ontology/constraints/、tools/l2s_candidates_v3.py"),
   ("L2 设施设计", "水力定尺寸", "压力管流速 ≤1.8 m/s 选管径与并联根数；泵按扬程与工作点选；地下池按截流量定容；地面泵站按二维峰值定流量，可多轮调整", "tools/l2opt_hydraulics.py、tools/l2s_sumps_v3.py"),
   ("后果分级", "暴露叠加规则", "积水片与医院、急救、隧道、地铁、变电站、道路、建筑叠加，分关键 / 高 / 中 / 无暴露；客户热点重要度只升不降", "tools/rda_l3.py"),
   ("L3 调度", "SciPy milp（HiGHS）· 滚动时域", "每 60 min 一个窗口；处置包含泵型 × 台数或罐车；约束含班组、车次、软管、燃油、预算；目标在“最快 / 平衡 / 最省”之间可选", "swbrain/dispatch.py、swbrain/dispatch2.py"),
   ("路网与路线", "SciPy Dijkstra + ArcGIS Online Route（卡车模式）", "积水按水深断路或减速；在线路线带积水多边形障碍，返回路线和中文导航；966 条实测得出卡车 / 本地 = 1.70", "swbrain/travel.py、swbrain/online_route.py"),
   ("深度强化学习", "PyTorch 2.9（CUDA 12.9）· RTX 5090 · 图神经网络", "状态 = 汇水区图 + 已选措施 + 预测效果；动作 = 选措施或停止（全部措施类型）；约束用动作屏蔽；奖励 = 判据通过概率 − λ × 全寿命成本；三级保真 + 主动学习", "tools/l2s_rl_data.py、tools/l2s_rl_model.py"),
   ("服务与页面", "Python 服务 · 原生 JavaScript + Leaflet · 中英双语 · 统一认证", "接口按需计算并缓存（积水点、调度）；长任务进任务队列；页面按阶段（L1 / L2 / L3）组织", "swbrain/serve.py、swbrain/routes.py、console/")],
  "loops": [
   ("治理 ⇄ 一二维验证", "治理后用一二维模型比较排水能力和积水；对不上的追到具体的井和管段，回到治理规则修正。RD-A 就是这样查出了封堵端和产流口径问题。"),
   ("L3 → L2", "L3 调度后，处置不了或“关键点建议 L2 兜住”的残余积水，作为高后果积水点加入 L2 的优化目标。RD-A 第一轮就补了 3–6 座地面泵站。"),
   ("二维 → 代理模型 → 强化学习", "强化学习在代理模型上搜索，选出的方案再用二维验收，结果加回训练集，直到预测与实算一致。")],
  "limits": "当前局限：模型未率定，缺观测与历史积水记录；L3 的车队、驻地、班组多为模拟值（已登记数据缺口）；造价为占位单价；深度强化学习处于设计与原型阶段。",
  "fig1": "图 1　分层架构", "fig2": "图 2　技术组合流程与反馈回路", "legend_title": "状态标识",
 },
 "en": {
  "title": "Drainage Agent Technical Architecture",
  "sub": "A thinking drainage system · Urban flood management for Abu Dhabi · RD-A catchment as the example",
  "date": "28 October 2026",
  "lead": "The 1D-2D hydrodynamic model is the core engine, but the system works through a combination of technologies: data governance turns client data into model-ready assets; knowledge and constraints turn the design manual and spatial conditions into rules; the 1D-2D model shows where it floods; optimisation decides what to build and what to dispatch; and the three lines of defence connect planning, pre-event and in-event action into one loop.",
  "status": [("Implemented", "running on RD-A with deliverables"), ("Prototype", "running, but data or features incomplete"), ("Designed", "design approved, not yet implemented")],
  "h": ["1. Layered architecture", "2. How the three lines of defence are implemented (RD-A)", "3. Six places where the 1D-2D model is used", "4. How the technologies combine: from data to decisions",
        "5. Optimisation methods chosen by problem structure", "6. Technology choices and implementation details", "7. Three feedback loops and current limitations"],
  "layers": [
   ("Applications & delivery", ["Web console", "APIs", "Agent actions", "Delivery packages", "Freedo DTS"]),
   ("Three lines of defence", ["L1 existing-system assessment", "L2 planning optimisation", "L3 emergency dispatch"]),
   ("Analysis & assessment", ["Ponding extraction", "Criteria A–D", "Consequence levels", "Hotspot comparison", "Cost & whole-life cost"]),
   ("Models & simulation", ["Coupled 1D-2D\nSWMM + ANUGA", "Runoff SCS-CN", "Pit proxy", "Surrogate (graph neural network)"]),
   ("Knowledge & constraints", ["WA-726 register\n(820 items)", "Domain rules", "Spatial constraints (Makani)", "Facility ontology"]),
   ("Data governance", ["ArcGIS UN path", "Open-source path", "Ledger · region checks\n1D-2D validation"]),
   ("Data sources", ["DMT asset database", "Consultant models", "DTM 5 m", "Makani", "Client hotspot list", "Roads & key facilities"])],
  "loops_short": ["Loop 1: the 1D-2D model validates governance", "Loop 2: residuals L3 cannot handle return to L2", "Loop 3: 2D results train the surrogate"],
  "lines": [
   ("L1 existing: what the network can carry", "Where the existing network, pump stations and ponds flood under each storm, how deep and for how long",
    "Model from governed data → coupled 1D-2D run → ponding extraction → comparison with client hotspots",
    "Domain 3.18 km², 65,210 triangles; 9 storms + 3 design storms; at 61 mm / 1 h only 1 of 11 hotspots passes"),
   ("L2 planning: what to build for least cost", "Choose a set of new facilities within the constraints so that client hotspots and high-consequence ponding pass, at least cost",
    "Candidates under three constraint layers → batch 1D screening → grouped greedy + pruning → surface pump stations → 2D acceptance → cost and NPV; deep RL to replace the search step (designed)",
    "Three levels: WA-726 design storms about USD 94 M; AECOM 61 mm / 1 h about USD 128 M; 96 mm extreme storm about USD 153 M (placeholder rates)"),
   ("L3 emergency: who goes where during the storm", "Whether pumps, tankers and crews can clear the ponding left after L2 within the deadlines",
    "Residual ponding → consequence levels → response packages replayed against drainage → rolling MILP dispatch → flooded road network + online truck routes",
    "Final scheme under the AECOM design storm: 21 residual points, 17 drain naturally, 2 cleared on time by L3, 3 pumps dispatched")],
  "lines_lab": ["Question", "Technology", "RD-A"],
  "lines_note": "The three lines use the same 2D results in sequence: L1 results are the baseline for L2 optimisation, the residual ponding of an L2 scheme is the input to L3 dispatch, and what L3 cannot handle returns to L2 as a new target.",
  "roles_head": ["Step", "What the model does", "RD-A example"],
  "roles": [
   ("1 Governance validation", "Build the model from data before and after governance and compare drainage capacity and ponding, proving the governance works and locating what is still wrong", "Before: outfall discharge 0, 27–34 % drained 4 h after the rain; after: 97–103 % of AECOM, 63–64 %"),
   ("2 L1 baseline", "Ponding extent, depth and recession of the existing system under each storm", "9 storms × L1 / L2 / L2+planned, 27 runs"),
   ("3 L2 acceptance", "Optimisation runs on 1D and the surrogate; every scheme must pass criteria A–D in 2D, which also reveals surface ponding that 1D cannot see", "Network-only scheme: 3 hotspots pass; with surface pump stations: 10"),
   ("4 Surrogate training", "2D results are the ground truth for learning “1D + pits → 2D criteria”", "About 180 2D runs available (128 for RD-A)"),
   ("5 L3 input", "Volume hydrograph of each residual ponding patch, which sets how many pumps and how long to drain", "Response packages are replayed against each hydrograph"),
   ("6 Delivery & display", "Depth animation, ponding points, .sww and Freedo DTS 3D", "61 mm / 1 h full package: 4 states × 2 storm patterns")],
  "flow": {"src": "Data sources", "gov": "Data governance", "mod": "1D-2D model\nSWMM + ANUGA", "ana": "Ponding · criteria\nconsequence", "kn": "WA-726 · domain rules\nspatial constraints",
           "l2a": "Candidates under\n3 constraint layers", "l2b": "1D screening\ngreedy + pruning", "l2c": "Surface pumps\n2D acceptance", "rl": "Deep RL\n(designed)",
           "l3a": "Response packages\ndrainage replay", "l3b": "Rolling MILP\ndispatch", "l3c": "Flooded roads\nonline truck routes", "L2": "L2 planning", "L3": "L3 emergency",
           "r1": "1 validate governance", "r2": "2 residuals back to L2", "r3": "3 2D results as\ntraining samples"},
  "opt_head": ["Method", "Where", "Why it fits", "Status"],
  "opt": [
   ("Grouped greedy + pruning", "L2 scheme design within a catchment (current)", "Measures interact strongly and can only be evaluated by simulation; greedy picks by overflow reduction per dollar, pruning drops weak measures or swaps in cheaper ones", "Implemented"),
   ("Mixed-integer programming (MILP, HiGHS)", "L3 rolling dispatch; pre-event siting; citywide portfolio", "Response effects can be precomputed as packages and resource limits are linear; the solver returns a provably optimal plan in seconds and can be explained", "Implemented (citywide: designed)"),
   ("CP-SAT (OR-Tools)", "L3 vehicle and crew scheduling", "Trip and shift constraints are natural in constraint programming; used when OR-Tools is installed, otherwise a greedy rotation", "Prototype"),
   ("Shortest path (Dijkstra)", "L3 travel-time matrix", "Local network of 415,000 links; water ≥0.45 m closes a road, shallower water slows it; scaled ×1.70 from measured truck times", "Implemented"),
   ("Online routing (ArcGIS Online)", "Actual routes and directions for L3 dispatch", "Trucking mode, flood polygons as barriers, turn-by-turn directions; cached, falls back to the local network offline", "Implemented"),
   ("Surrogate (graph neural network)", "Fast L2 evaluation", "Single-measure effects from 1D screening as a prior, shared features learn the interaction; validation R² 0.98 with 2,315 samples", "Prototype"),
   ("Deep RL (masked PPO + MCTS)", "L2 scheme design within a catchment", "Huge combinatorial space with nonlinear interaction; one graph policy for any catchment; 2D active learning keeps the surrogate honest", "Designed")],
  "tech_head": ["Component", "Choice", "Implementation", "Code"],
  "tech": [
   ("Data governance", "ArcGIS Pro 3.7 · Utility Network Expanded (arcpy.un, untools, UDMS); parallel open-source path (GeoPandas / Shapely)", "Citywide DMT governed to round 14, 90.0 % traceable to an outfall; a region check per catchment (RD-A 99.9 %, 0 network errors); consultant models fused as “DMT as base, consultant parameters added”", "tools/exp_gov/, tools/dgov/"),
   ("1D network", "EPA SWMM 5.2 (swmm-toolkit)", "AECOM SewerGEMS export converted to SI; dynamic wave; multiprocess batches, about 50 s per RD-A 1D run", "tools/rda_build.py, tools/l2opt_screen.py"),
   ("2D surface", "ANUGA (finite volume, unstructured triangles)", "15 m mesh parameter (cells ≤75 m²), elevations from the 5 m DTM; depth and velocity stored every 5 min; 10 h simulated after the rain", "tools/couple2way_v2.py"),
   ("1D-2D coupling", "In-house two-way coupling · Docker image", "Exchange every 60 s: manhole overflow enters 2D, surface water returns through manhole inflow; 2D surface pumps at intakes; queued parallel runs with monitoring", "tools/couple2way_v2.py, tools/l2opt_run2d.py"),
   ("Runoff", "SCS-CN + SCS unit hydrograph", "Composite CN per plot from the Makani impervious share (impervious CN 95, pervious client CN)", "tools/rda_catchments.py"),
   ("Ponding points", "In-house patch algorithm", "Cells with max depth ≥ threshold joined by mesh adjacency, ponds removed, ≥100 m² and ≥0.5 h; thresholds adjustable on the page", "swbrain/ponding.py"),
   ("Acceptance criteria", "Criteria A–D (proposed, to be confirmed by the client)", "Within 200 m of a hotspot: A no ponding ≥0.30 m; B area × time of ≥0.15 m ≤10 % of planned; C below 0.10 m within 6 h after rain; D nowhere worse across the domain", "tools/l2opt_eval2d.py"),
   ("Constraint knowledge", "WA-726 register + domain rules + Makani spatial data", "820 manual constraints structured; each candidate is checked item by item, producing a compliance table (pass / needs approval / data missing)", "swbrain/ontology/constraints/, tools/l2s_candidates_v3.py"),
   ("L2 facility design", "Hydraulic sizing", "Rising mains sized for ≤1.8 m/s with parallel lines if needed; pumps by head and operating point; tanks by intercepted volume; surface pumps sized from 2D peaks and tuned in rounds", "tools/l2opt_hydraulics.py, tools/l2s_sumps_v3.py"),
   ("Consequence levels", "Exposure overlay rules", "Ponding overlaid with hospitals, ambulance, tunnels, metro, substations, roads, buildings: critical / high / medium / no exposure; client hotspot importance can only raise a level", "tools/rda_l3.py"),
   ("L3 dispatch", "SciPy milp (HiGHS) · rolling horizon", "One window per 60 min; packages of pump type × count or tankers; constraints on crews, trips, hoses, fuel, budget; objective selectable: fastest / balanced / cheapest", "swbrain/dispatch.py, swbrain/dispatch2.py"),
   ("Roads & routes", "SciPy Dijkstra + ArcGIS Online Route (trucking)", "Roads closed or slowed by depth; online routes with flood polygon barriers and directions; 966 routes give truck / local = 1.70", "swbrain/travel.py, swbrain/online_route.py"),
   ("Deep RL", "PyTorch 2.9 (CUDA 12.9) · RTX 5090 · graph neural networks", "State = catchment graph + chosen measures + predicted effects; actions = add a measure or stop (all measure types); constraints by action masking; reward = criteria pass probability − λ × whole-life cost; three fidelity levels + active learning", "tools/l2s_rl_data.py, tools/l2s_rl_model.py"),
   ("Service & console", "Python service · vanilla JavaScript + Leaflet · Chinese/English · single sign-on", "APIs compute on demand with caching (ponding, dispatch); long jobs go to a task queue; pages organised by stage (L1 / L2 / L3)", "swbrain/serve.py, swbrain/routes.py, console/")],
  "loops": [
   ("Governance ⇄ 1D-2D validation", "After governance, the 1D-2D model compares drainage capacity and ponding; mismatches are traced to specific manholes and pipes and fed back into the rules. This is how RD-A revealed the capped-end and runoff issues."),
   ("L3 → L2", "Residual ponding that L3 cannot clear, or critical points better held by L2, become high-consequence targets for L2. RD-A's first round added 3–6 surface pump stations."),
   ("2D → surrogate → RL", "RL searches on the surrogate; selected schemes are verified in 2D and the results go back into training until predictions match the simulations.")],
  "limits": "Current limitations: the model is not yet calibrated (no observations or flood records); L3 fleet, depots and crews are mostly simulated (gaps registered); costs use placeholder rates; deep RL is at design and prototype stage.",
  "fig1": "Figure 1  Layered architecture", "fig2": "Figure 2  How the technologies combine, with feedback loops", "legend_title": "Status",
 }}
STATUS_KEY = {"已实现": "ok", "原型": "proto", "设计中": "plan", "Implemented": "ok", "Prototype": "proto", "Designed": "plan"}


# ------------------------------------------------------------------ 图
def box(ax, x, y, w, h, text, fc, ec, fs, fn, bold=False, tc=None):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.004,rounding_size=0.008", fc=fc, ec=ec, lw=1.0))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, fontname=fn, color=tc or COL["ink"], fontweight="bold" if bold else "normal", wrap=True)


def fig_layers(L, path):
    fn = FONT[L["_lang"]]
    fig, ax = plt.subplots(figsize=(13, 7.4), dpi=170); ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    rows = L["layers"]; n = len(rows); top, bot = 0.93, 0.10; h = (top - bot) / n
    fills = [COL["sand_soft"], COL["g_soft"], COL["panel"], COL["water_soft"], COL["panel"], COL["panel"], COL["panel"]]
    for i, (name, cells) in enumerate(rows):
        y = top - (i + 1) * h + 0.008; hh = h - 0.016
        box(ax, 0.0, y, 0.16, hh, name, COL["panel"], COL["line"], 10.5, fn, bold=True)
        x0, x1 = 0.175, 0.995; w = (x1 - x0 - 0.008 * (len(cells) - 1)) / len(cells)
        for j, c in enumerate(cells):
            fc, ec, tc = fills[i], COL["line"], None
            if i == 1:
                fc = [COL["g_soft"], COL["o_soft"], COL["r_soft"]][j]; ec = [COL["l1"], COL["l2"], COL["l3"]][j]
            if i == 3:
                ec = COL["water"]
                if j == 3:
                    fc, ec = COL["ai_soft"], COL["ai"]
            box(ax, x0 + j * (w + 0.008), y, w, hh, c, fc, ec, 9.2, fn)
    ax.text(0.0, 0.055, "   ".join(L["loops_short"]), fontsize=9, fontname=fn, color=COL["water"])
    ax.text(0.0, 0.015, L["fig1"], fontsize=10, fontname=fn, color=COL["muted"])
    fig.savefig(path, bbox_inches="tight", facecolor="white"); plt.close(fig)


def fig_flow(L, path):
    fn = FONT[L["_lang"]]; F = L["flow"]
    fig, ax = plt.subplots(figsize=(13, 6.6), dpi=170); ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    B = {}

    def bx(k, x, y, w, h, fc, ec, tc=None):
        box(ax, x, y, w, h, F[k], fc, ec, 9.2, fn, tc=tc); B[k] = (x, y, w, h)
    bx("src", 0.00, 0.47, 0.11, 0.14, COL["sand_soft"], COL["sand"])
    bx("gov", 0.14, 0.47, 0.11, 0.14, COL["g_soft"], COL["l1"])
    bx("mod", 0.28, 0.47, 0.12, 0.14, COL["water_soft"], COL["water"])
    bx("ana", 0.43, 0.47, 0.12, 0.14, COL["panel"], COL["muted"])
    bx("kn", 0.28, 0.80, 0.14, 0.12, COL["panel"], COL["sand"])
    ax.text(0.61, 0.95, F["L2"], fontsize=10, fontname=fn, color=COL["l2"], fontweight="bold")
    bx("l2a", 0.61, 0.79, 0.11, 0.14, COL["o_soft"], COL["l2"]); bx("l2b", 0.745, 0.79, 0.11, 0.14, COL["o_soft"], COL["l2"]); bx("l2c", 0.88, 0.79, 0.115, 0.14, COL["o_soft"], COL["l2"])
    bx("rl", 0.745, 0.52, 0.11, 0.13, COL["ai_soft"], COL["ai"])
    ax.text(0.61, 0.33, F["L3"], fontsize=10, fontname=fn, color=COL["l3"], fontweight="bold")
    bx("l3a", 0.61, 0.13, 0.11, 0.15, COL["r_soft"], COL["l3"]); bx("l3b", 0.745, 0.13, 0.11, 0.15, COL["r_soft"], COL["l3"]); bx("l3c", 0.88, 0.13, 0.115, 0.15, COL["r_soft"], COL["l3"])

    def arr(a, b, c=COL["ink"], ls="-", rad=0.0, side=("r", "l")):
        xa, ya, wa, ha = B[a]; xb, yb, wb, hb = B[b]
        pa = {"r": (xa + wa, ya + ha / 2), "l": (xa, ya + ha / 2), "t": (xa + wa / 2, ya + ha), "b": (xa + wa / 2, ya)}[side[0]]
        pb = {"r": (xb + wb, yb + hb / 2), "l": (xb, yb + hb / 2), "t": (xb + wb / 2, yb + hb), "b": (xb + wb / 2, yb)}[side[1]]
        ax.annotate("", xy=pb, xytext=pa, arrowprops=dict(arrowstyle="-|>", color=c, lw=1.3, ls=ls, connectionstyle=f"arc3,rad={rad}"))
    arr("src", "gov"); arr("gov", "mod"); arr("mod", "ana")
    arr("ana", "l2a", COL["l2"], rad=-0.25, side=("t", "l")); arr("kn", "l2a", COL["sand"], rad=0.0, side=("r", "l"))
    arr("l2a", "l2b", COL["l2"]); arr("l2b", "l2c", COL["l2"])
    arr("ana", "l3a", COL["l3"], rad=0.25, side=("b", "l")); arr("l3a", "l3b", COL["l3"]); arr("l3b", "l3c", COL["l3"])
    arr("mod", "gov", COL["water"], ls="--", rad=-0.6, side=("b", "b")); ax.text(0.27, 0.36, F["r1"], fontsize=8.6, fontname=fn, color=COL["water"], ha="center")
    arr("l3c", "l2c", COL["l3"], ls="--", side=("t", "b")); ax.text(0.93, 0.55, F["r2"], fontsize=8.6, fontname=fn, color=COL["l3"], ha="center", va="center", rotation=90)
    arr("l2c", "rl", COL["ai"], ls="--", rad=-0.2, side=("b", "r")); ax.text(0.80, 0.475, F["r3"].replace("\n", " "), fontsize=8.4, fontname=fn, color=COL["ai"], ha="center", va="top")
    arr("rl", "l2b", COL["ai"], ls="--", side=("t", "b"))
    ax.text(0.0, 0.02, L["fig2"], fontsize=10, fontname=fn, color=COL["muted"])
    fig.savefig(path, bbox_inches="tight", facecolor="white"); plt.close(fig)


# ------------------------------------------------------------------ Word
def build_docx(L, figs, path):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor
    fn = FONT[L["_lang"]]
    d = Document()
    for s in d.sections:
        s.left_margin = s.right_margin = Cm(2); s.top_margin = s.bottom_margin = Cm(2)
    st = d.styles["Normal"]; st.font.name = fn; st.font.size = Pt(10.5); st.element.rPr.rFonts.set(qn("w:eastAsia"), fn)
    for hn in ("Title", "Heading 1", "Heading 2"):
        h = d.styles[hn]; h.font.name = fn; h.element.rPr.rFonts.set(qn("w:eastAsia"), fn); h.font.color.rgb = RGBColor(0x15, 0x22, 0x24)

    def para(t, size=None, color=None, bold=False, italic=False, align=None):
        p = d.add_paragraph(); r = p.add_run(t); r.bold = bold; r.italic = italic
        if size:
            r.font.size = Pt(size)
        if color:
            r.font.color.rgb = RGBColor.from_string(color.lstrip("#"))
        if align:
            p.alignment = align
        return p

    def table(head, rows, widths=None):
        t = d.add_table(rows=1, cols=len(head)); t.style = "Light Grid Accent 1"
        for i, h in enumerate(head):
            c = t.rows[0].cells[i]; c.text = ""; r = c.paragraphs[0].add_run(h); r.bold = True; r.font.size = Pt(9.5)
        for row in rows:
            cs = t.add_row().cells
            for i, v in enumerate(row):
                cs[i].text = ""; r = cs[i].paragraphs[0].add_run(str(v)); r.font.size = Pt(9.5)
        if widths:
            for row in t.rows:
                for i, w in enumerate(widths):
                    row.cells[i].width = Cm(w)
        return t
    d.add_heading(L["title"], 0)
    para(L["sub"], 11, COL["muted"]); para(L["date"], 10, COL["muted"])
    para(L["lead"], 11)
    para(L["legend_title"] + "：" if L["_lang"] == "zh" else L["legend_title"] + ":", 10, bold=True)
    for a, b in L["status"]:
        para(f"· {a} — {b}", 10, COL["muted"])
    d.add_heading(L["h"][0], 1); d.add_picture(str(figs["layers"]), width=Cm(17)); para(L["fig1"], 9.5, COL["muted"], align=WD_ALIGN_PARAGRAPH.CENTER)
    d.add_heading(L["h"][1], 1)
    for (nm, q, tech, rda), c in zip(L["lines"], (COL["l1"], COL["l2"], COL["l3"])):
        d.add_heading(nm, 2)
        for lab, v in zip(L["lines_lab"], (q, tech, rda)):
            p = d.add_paragraph(); r = p.add_run(lab + ("：" if L["_lang"] == "zh" else ": ")); r.bold = True; r.font.color.rgb = RGBColor.from_string(c.lstrip("#")); p.add_run(v)
    para(L["lines_note"], 10.5, italic=True)
    d.add_heading(L["h"][2], 1); table(L["roles_head"], L["roles"], [3.5, 7.5, 6])
    d.add_heading(L["h"][3], 1); d.add_picture(str(figs["flow"]), width=Cm(17)); para(L["fig2"], 9.5, COL["muted"], align=WD_ALIGN_PARAGRAPH.CENTER)
    d.add_heading(L["h"][4], 1); table(L["opt_head"], L["opt"], [3.6, 3.6, 7.3, 2.5])
    d.add_heading(L["h"][5], 1); table(L["tech_head"], L["tech"], [2.6, 4.2, 6.6, 3.6])
    d.add_heading(L["h"][6], 1)
    for nm, t in L["loops"]:
        p = d.add_paragraph(style="List Number"); r = p.add_run(nm + ("：" if L["_lang"] == "zh" else ": ")); r.bold = True; p.add_run(t)
    para(L["limits"], 10.5, COL["muted"])
    d.save(path)


# ------------------------------------------------------------------ PPT
def build_pptx(L, figs, path):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Pt
    fn = FONT[L["_lang"]]
    P = Presentation(); P.slide_width, P.slide_height = Emu(12192000), Emu(6858000)
    W, H = P.slide_width, P.slide_height
    blank = P.slide_layouts[6]
    rgb = lambda c: RGBColor.from_string(c.lstrip("#"))   # noqa: E731

    def tb(s, x, y, w, h, text, size=14, color=COL["ink"], bold=False):
        b = s.shapes.add_textbox(x, y, w, h); tf = b.text_frame; tf.word_wrap = True
        lines = text if isinstance(text, list) else [text]
        for i, t in enumerate(lines):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            r = p.add_run(); r.text = t; r.font.size = Pt(size); r.font.name = fn; r.font.bold = bold; r.font.color.rgb = rgb(color)
        return b

    def header(s, t, kicker=None):
        bar = s.shapes.add_shape(1, 0, 0, W, Emu(80000)); bar.fill.solid(); bar.fill.fore_color.rgb = rgb(COL["water"]); bar.line.fill.background()
        if kicker:
            tb(s, Emu(457200), Emu(230000), W - Emu(914400), Emu(300000), kicker, 11, COL["water"], True)
        tb(s, Emu(457200), Emu(480000), W - Emu(914400), Emu(600000), t, 24, COL["ink"], True)

    def table(s, head, rows, top, widths, size=10):
        x = Emu(457200); w = W - Emu(914400)
        shp = s.shapes.add_table(len(rows) + 1, len(head), x, top, w, Emu(400000 * (len(rows) + 1)))
        t = shp.table
        tot = sum(widths)
        for i, cw in enumerate(widths):
            t.columns[i].width = int(w * cw / tot)
        for i, h in enumerate(head):
            c = t.cell(0, i); c.text = h; c.fill.solid(); c.fill.fore_color.rgb = rgb(COL["water"])
            for p in c.text_frame.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(size); r.font.bold = True; r.font.name = fn; r.font.color.rgb = rgb("#ffffff")
        for ri, row in enumerate(rows, 1):
            for ci, v in enumerate(row):
                c = t.cell(ri, ci); c.text = str(v); c.fill.solid(); c.fill.fore_color.rgb = rgb("#ffffff" if ri % 2 else "#f2f6f7")
                for p in c.text_frame.paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(size); r.font.name = fn; r.font.color.rgb = rgb(COL["ink"])
    m = Emu(457200)
    # 1 封面
    s = P.slides.add_slide(blank)
    bg = s.shapes.add_shape(1, 0, 0, W, H); bg.fill.solid(); bg.fill.fore_color.rgb = rgb("#0f3d4a"); bg.line.fill.background()
    tb(s, m, Emu(2100000), W - 2 * m, Emu(900000), L["title"], 40, "#ffffff", True)
    tb(s, m, Emu(3050000), W - 2 * m, Emu(500000), L["sub"], 16, "#cfe6ea")
    tb(s, m, Emu(3600000), W - 2 * m, Emu(400000), L["date"], 14, "#cfe6ea")
    # 2 核心观点
    s = P.slides.add_slide(blank); header(s, L["h"][0].split("、")[-1] if L["_lang"] == "zh" else L["h"][0][3:], "OVERVIEW" if L["_lang"] == "en" else "总览")
    s.shapes.add_picture(str(figs["layers"]), m, Emu(1200000), width=W - 2 * m - Emu(3000000))
    tb(s, W - m - Emu(2900000), Emu(1300000), Emu(2900000), Emu(4500000), [L["lead"], "", *[f"{a}：{b}" if L["_lang"] == "zh" else f"{a}: {b}" for a, b in L["status"]]], 11, COL["muted"])
    # 3 三道防线
    s = P.slides.add_slide(blank); header(s, L["h"][1], "L1 · L2 · L3")
    cw = (W - 2 * m - Emu(300000)) // 3
    for i, ((nm, q, tech, rda), c) in enumerate(zip(L["lines"], (COL["l1"], COL["l2"], COL["l3"]))):
        x = m + i * (cw + Emu(150000))
        bar = s.shapes.add_shape(1, x, Emu(1250000), cw, Emu(60000)); bar.fill.solid(); bar.fill.fore_color.rgb = rgb(c); bar.line.fill.background()
        tb(s, x, Emu(1350000), cw, Emu(500000), nm, 14, c, True)
        tb(s, x, Emu(1900000), cw, Emu(3600000), [f"{L['lines_lab'][0]}：{q}" if L["_lang"] == "zh" else f"{L['lines_lab'][0]}: {q}", "",
                                                   f"{L['lines_lab'][1]}：{tech}" if L["_lang"] == "zh" else f"{L['lines_lab'][1]}: {tech}", "",
                                                   f"{L['lines_lab'][2]}：{rda}" if L["_lang"] == "zh" else f"{L['lines_lab'][2]}: {rda}"], 13)
    tb(s, m, Emu(5750000), W - 2 * m, Emu(700000), L["lines_note"], 11, COL["muted"])
    # 4 一二维六环节
    s = P.slides.add_slide(blank); header(s, L["h"][2], "1D-2D")
    table(s, L["roles_head"], L["roles"], Emu(1250000), [2.2, 5.2, 4.2], 11.5)
    # 5 流程图
    s = P.slides.add_slide(blank); header(s, L["h"][3], "FLOW" if L["_lang"] == "en" else "组合")
    s.shapes.add_picture(str(figs["flow"]), m, Emu(1200000), width=W - 2 * m)
    # 6 优化方法
    s = P.slides.add_slide(blank); header(s, L["h"][4], "OPTIMISATION" if L["_lang"] == "en" else "优化")
    table(s, L["opt_head"], L["opt"], Emu(1200000), [2.6, 2.6, 6.0, 1.8], 10.5)
    # 7–8 选型（分两页）
    half = (len(L["tech"]) + 1) // 2
    for k, part in enumerate((L["tech"][:half], L["tech"][half:])):
        s = P.slides.add_slide(blank); header(s, L["h"][5] + f"（{k + 1}/2）" if L["_lang"] == "zh" else L["h"][5] + f" ({k + 1}/2)", "STACK" if L["_lang"] == "en" else "选型")
        table(s, L["tech_head"], part, Emu(1200000), [1.6, 3.0, 5.6, 2.4], 10.5)
    # 9 回路与局限
    s = P.slides.add_slide(blank); header(s, L["h"][6], "LOOPS" if L["_lang"] == "en" else "闭环")
    y = Emu(1300000)
    for i, (nm, t) in enumerate(L["loops"]):
        tb(s, m, y, W - 2 * m, Emu(350000), f"{i + 1}  {nm}", 15, COL["water"], True)
        tb(s, m + Emu(300000), y + Emu(380000), W - 2 * m - Emu(300000), Emu(700000), t, 12)
        y += Emu(1250000)
    tb(s, m, Emu(5300000), W - 2 * m, Emu(900000), L["limits"], 11.5, COL["l3"])
    P.save(path)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=str(ROOT / "out/交付/系统架构说明_20261028"))
    a = ap.parse_args(); out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    names = {"zh": "排水智能体技术架构_中文", "en": "Drainage_Agent_Technical_Architecture_EN"}
    for lang in ("zh", "en"):
        L = dict(T[lang]); L["_lang"] = lang
        figs = {"layers": out / f"{names[lang]}_图1_分层架构.png" if lang == "zh" else out / f"{names[lang]}_Fig1_layers.png",
                "flow": out / f"{names[lang]}_图2_技术组合流程.png" if lang == "zh" else out / f"{names[lang]}_Fig2_flow.png"}
        fig_layers(L, figs["layers"]); fig_flow(L, figs["flow"])
        build_docx(L, figs, out / f"{names[lang]}.docx"); build_pptx(L, figs, out / f"{names[lang]}.pptx")
        print("→", out / f"{names[lang]}.docx", out / f"{names[lang]}.pptx")


if __name__ == "__main__":
    main()
