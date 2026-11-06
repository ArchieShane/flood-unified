"""假设总账：一个地方列出「当前这页 / 这个答案依赖哪些模拟、假设、占位、未核实的输入」。

来源三类：
  配置里的标记   [dispatch.resources].simulated、[dispatch.bridges].simulated、[dispatch.contractors].simulated、[cost] 占位单价、[scenarios.events.*] 的缺省雨量假设、IDF 来源、[traffic.simulated]
  运行时的事实   路况实际用的是模拟曲线还是 Google；车队来自预案 A 且 AAM / DRM 的泵型按比例拆分；热点坐标 xlsx 未经 KMZ 修正的比例；生命周期里推演估计占比
  数据缺口       客户还没给的：逐点积水记录、市区站雨量、桥梁台账、承包商边界、993 / check-in 实时源
每条：{id, area, level: simulated|assumed|placeholder|unverified|missing, what, value, source, affects, fix}。level 的含义：
  simulated  = 我们造的数（有依据、有量级，但不是客户的）；assumed = 参数取值是假设；placeholder = 占位等替换（如单价）；
  unverified = 有数但没核过；missing = 缺，功能退化或按缺省。"""
from __future__ import annotations


def _n(cfg: dict, *path, default=None):
    cur = cfg or {}
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def register(cfg: dict, state=None, key: str | None = None) -> list[dict]:
    items: list[dict] = []

    def add(id_, area, level, what, value=None, source=None, affects=None, fix=None, what_en=None):
        items.append({"id": id_, "area": area, "level": level, "what": what, "what_en": what_en, "value": value, "source": source, "affects": affects or [], "fix": fix})
    D = cfg.get("dispatch") or {}
    # ---- 资源
    R = D.get("resources") or {}
    if R.get("simulated", True):
        add("resources", "第三道 · 资源", "simulated", "运输车辆、班组人数、软管 / 燃油库存、费率按行业常规模拟（预案 A 只有泵数与部分人力）", {k: R.get(k) for k in ("units_per_vehicle_heavy", "units_per_vehicle_light", "crew_per_unit_if_missing", "on_duty_share", "hose_m_per_unit", "fuel_l_per_unit") if k in R},
            "[dispatch.resources]", ["dispatch_plan", "resource_check", "staging_status"], "fleet.json 的 depots[].resources 逐项覆盖，或客户给车辆 / 班组台账", "Vehicles, crews, hose / fuel stocks and rates are simulated from industry norms (Plan A only gives pump counts and some manpower)")
    fleet = getattr(state, "fleet", None) if state is not None else None
    if isinstance(fleet, dict):
        if fleet.get("size_split_simulated") or any(d.get("size_split_simulated") for d in (fleet.get("depots") or []) if isinstance(d, dict)):
            add("fleet_split", "第三道 · 泵队", "simulated", "AAM / DRM 预案 A 只有泵总数，12 / 8 / 6 寸按 14 / 11 / 75% 拆分", "14% / 11% / 75%", "hotspots.fleet_from_plan_a", ["dispatch_plan"], "两家市政给分型号台数", "AAM / DRM Plan A gives pump totals only; 12/8/6-inch split is simulated at 14/11/75%")
        add("fleet_source", "第三道 · 泵队", "assumed", "泵队与驻地由客户预案 A 按最近民防中心归并生成，不是实际驻地台账", {"depots": len(fleet.get("depots") or []), "units": len(fleet.get("units") or [])}, "exchange/fleet.plan_a.json", ["dispatch_plan", "preposition_plan", "staging_status"], "客户提供 exchange/fleet.json（swbrain.fleet/1）", "Fleet and depots are generated from Plan A folded to the nearest civil-defence centre, not an actual depot register")
    # ---- 管网覆盖
    cov = None
    if state is not None:
        try:
            cs = getattr(state, "cs", None)
            hs = getattr(state, "hotspots", None)
            if cs is not None and hs is not None and len(cs) and len(hs):
                lo, hi = float(cs["lon"].min()), float(cs["lon"].max())
                miss = sorted({str(r) for r, g in hs.groupby("region") if not ((g["lon"] >= lo) & (g["lon"] <= hi)).any()})
                if miss:
                    n_hs = int(hs[hs["region"].isin(miss)].shape[0])
                    n_claim = int(hs[hs["region"].isin(miss)]["network_available"].fillna(False).astype(bool).sum()) if "network_available" in hs.columns else None
                    cov = {"regions": miss, "hotspots": n_hs, "client_says_has_network": n_claim,
                           "terminal_lon_range": [round(lo, 3), round(hi, 3)]}
        except Exception:
            cov = None
    if cov:
        add("network_coverage", "数据底座 · 管网覆盖", "missing",
            f"管网台账只覆盖阿布扎比（终点经度 {cov['terminal_lon_range'][0]}–{cov['terminal_lon_range'][1]}），"
            f"{'、'.join(cov['regions'])} 一个终点都没有。这 {cov['hotspots']} 个热点的「排放去处」判不了，"
            f"系统按最保守办：当成没有去处、只能罐车拉走。"
            + (f"客户清单里其中 {cov['client_says_has_network']} 个标注「有管网」，与本系统的判定相反。" if cov.get("client_says_has_network") else ""),
            cov, "cs 终点坐标范围 vs 热点坐标",
            ["dispatch_plan", "recession_options", "preposition_plan"],
            "要 AAM / DRM 的雨水管网 GIS（见 docs/向客户要的数据 B 组）；拿到之前，这两区的罐车数只能当量级参考",
            f"The pipe register covers Abu Dhabi only; {'/'.join(cov['regions'])} have no terminal at all, so discharge feasibility cannot be judged for those {cov['hotspots']} hotspots — the system falls back to the most conservative reading (tanker only)")

    # ---- 桥、承包商
    B = D.get("bridges") or {}
    if B.get("simulated", True):
        add("bridges", "第三道 · 路网", "placeholder", "限重桥 / 净高不足的路段是两条模拟占位，不是台账", [b.get("name") for b in (B.get("items") or [])], "[dispatch.bridges]", ["dispatch_plan"], "exchange/bridges.json（swbrain.bridges/1）", "Weight-limited bridges are two simulated placeholders, not a register")
    C = D.get("contractors") or {}
    if C.get("simulated", True):
        add("contractors", "第三道 · 承包商", "simulated", "承包商服务边界按预案 A 片区 / 市政模拟；跨区工单费是假设", {"mode": C.get("mode"), "work_order_aed": C.get("work_order_aed")}, "[dispatch.contractors]", ["dispatch_plan"], "exchange/contractors.json（swbrain.contractors/1）", "Contractor boundaries are simulated from Plan A areas; the cross-zone work-order fee is an assumption")
    # ---- 费率
    cost = cfg.get("cost") or {}
    if cost:
        add("cost_rates", "费用", "placeholder", "台时、动员、人工、燃油、换管等单价全是占位，不是 ADM / ADPIC 费率表", {k: cost.get(k) for k in list(cost)[:6]}, "[cost] · [dispatch.cost]", ["dispatch_plan", "cost_summary", "recession_options", "proposal_whatif"], "客户费率表", "All unit costs are placeholders, not the ADM / ADPIC rate book")
    # ---- 雨量与情景
    ev = _n(cfg, "scenarios", "events", default={}) or {}
    for k, e in ev.items():
        add(f"event_rain_{k}", "情景 · 实测事件", "assumed", f"{e.get('label') or k}：有 Parsons 加权站值的片区用站值，其余片区 24 h 雨量取缺省 {e.get('p24_default_mm')} mm 为假设", {"p24_default_mm": e.get("p24_default_mm"), "by_area": e.get("p24_by_area")}, e.get("source"), ["set_scenario", "dispatch_plan", "event_review", "point_history"], "客户给市区各站雨量（NCM_Rainfall Data）", f"{e.get('label_en') or k}: areas without a Parsons station value use the default {e.get('p24_default_mm')} mm")
    idf = cfg.get("idf") or {}
    add("idf", "情景 · 设计雨", "unverified", "重现期 ↔ 雨量按 DMT WA-726-1 表 3-6 B 区；「相当于几年一遇」是表上对数插值", idf.get("source"), "[idf]", ["rain_to_scenario", "event_review"], "无需替换；注意只对 B 区成立", "Return-period equivalence is log-interpolated on the DMT WA-726-1 Table 3-6 zone-B IDF")
    # ---- 路况
    T = cfg.get("traffic") or {}
    prov_eff = None
    if state is not None and getattr(state, "traffic", None):
        prov_eff = (state.traffic.get("meta") or {}).get("provider")
    if prov_eff in (None, "simulated") and str(T.get("provider", "simulated")).lower() != "none":
        add("traffic", "第三道 · 路况", "simulated", "行程时间按阿布扎比时段曲线 × 雨天加成放大，不是 Google 实时路况（没有 GOOGLE_MAPS_API_KEY）", {"rain_uplift": _n(cfg, "traffic", "simulated", "rain_uplift")}, "[traffic] · swbrain/traffic.py", ["dispatch_plan", "replan", "traffic_status"], "设环境变量 GOOGLE_MAPS_API_KEY 后重启", "Travel times use a simulated time-of-day curve × rain uplift, not Google live traffic")
    # ---- 热点坐标
    hs = getattr(state, "hotspots", None) if state is not None else None
    if hs is not None and "coord_source" in getattr(hs, "columns", []):
        vc = hs["coord_source"].value_counts().to_dict(); n_x = int(vc.get("xlsx", 0))
        if n_x:
            add("hotspot_coords", "积水点 · 坐标", "unverified", f"{n_x} 个热点（多为 AAM / DRM）只有表格坐标，未经 KMZ 修正", vc, "hotspots.apply_kmz_coords", ["inspect_point", "list_points", "dispatch_plan"], "AAM / DRM 的 KMZ 或修正坐标", f"{n_x} hotspots (mostly AAM / DRM) have spreadsheet coordinates only, not KMZ-corrected")
    # ---- 历史记录 / 生命周期
    onto = getattr(state, "ontology", None) if state is not None else None
    if onto is not None:
        try:
            eps = list(onto.load_episodes().values())
            n_est = sum(1 for e in eps if e.get("source") == "system_estimate"); n_rec = sum(1 for e in eps if e.get("observed_flooded") is not None)
            if eps and n_rec == 0:
                add("episodes", "生命周期 · 历史记录", "missing", f"历史降雨事件里 {len(eps)} 条积水经历全是模型推演估计，没有一条客户的逐点记录；复发率、命中率 / 漏报都算不了", {"episodes": len(eps), "system_estimate": n_est, "with_record": n_rec}, "swbrain/lifecycle.py", ["point_history", "event_review", "lifecycle_overview"], "exchange/episodes.json（swbrain.episodes/1）：事件报告表 / 呼叫中心 / 卫星洪涝面经 episodes_import 导入", f"All {len(eps)} flood episodes in historical events are model estimates; no per-point client record yet")
            elif eps:
                add("episodes", "生命周期 · 历史记录", "unverified", f"{len(eps)} 条积水经历里 {n_rec} 条有客户 / 现场记录，其余 {n_est} 条是推演估计", {"episodes": len(eps), "with_record": n_rec, "system_estimate": n_est}, "swbrain/lifecycle.py", ["point_history", "event_review"], None, f"{n_rec} of {len(eps)} episodes have a record; the rest are model estimates")
        except Exception:
            pass
    # ---- 外部实时源
    try:
        from . import feeds as _feeds
        st = _feeds.status(cfg)
        empty = [n for n, v in st.items() if not v.get("rows") and n in ("incidents", "checkins")]
        if empty:
            add("feeds_live", "第三道 · 实时源", "missing", "993 报案与承包商 check-in 还没有接入（拉的地址和推送都还没数据）；重规划与到位率是 plan only", empty, "[feeds]", ["replan", "staging_status"], "ADM 应急系统给 url，或推到 /api/feed/incidents、/api/feed/checkins", "993 incidents and contractor check-ins are not connected yet; replanning and readiness are plan-only")
    except Exception:
        pass
    # ---- 世界模型
    man = getattr(state, "state_manifest", None) if state is not None else None
    if man is None:
        add("state_manifest", "数据底座", "unverified", "out/state 没有清单（manifest.json），说不出这批数据是什么时候、用哪份配置、从哪个库算的", None, "swbrain/run.py", [], "重跑 python -m swbrain.run 会写清单", "out/state has no manifest.json, so its build time, config and source database are unknown")
    else:
        add("state_manifest", "数据底座", "unverified", f"管网与筛查结果来自 {man.get('built_at')} 的批处理（{man.get('counts', {}).get('edges')} 段、提交 {man.get('commit')}）；源库是台账快照，没有时序", man.get("counts"), "out/state/manifest.json", [], "源库更新后重跑 python -m swbrain.run", f"Network and screening results come from the batch run at {man.get('built_at')}; the source register is a snapshot without time series")
    add("gwm", "预测侧 · 世界模型", "unverified", "世界模型（GWM）水深只涨不退、体积偏大，只作空间对照，不进调度", None, "swbrain/gwmcompare.py", ["compare_world_model", "world_model_new_points"], "等预测侧新版本", "The world model's depth never recedes and volumes run high; used for spatial cross-checks only")
    # ---- 资产台账
    try:
        from . import assets as _FA
        _fs = _FA.summary()
        if _fs["kinds_missing"]:
            add("asset_register", "数据底座 · 资产台账", "missing",
                f"客户 L1/L2/L3 分级里的 {_fs['kinds_declared']} 种资产，只有 {_fs['kinds_with_data']} 种有数据"
                f"（{_fs['n']} 件，全是塘）。管线由 Reach 承载、移动泵与罐车由 PumpUnit 承载，其余 "
                f"{len(_fs['kinds_missing'])} 种——泵站、出水口、雨水口、坝、干河谷、明渠、生态格室、地面管、"
                f"溢流堰、土堤、Jersey 护栏、沙袋、小型挡水、固定泵、吊车——一件都没有。"
                f"没有台账，L1 的现状说不全，L2 也只能谈塘、谈不了挡水与导流。"
                f"另外雨水口只覆盖阿布扎比：845 个热点里 ADM 有 425/506（84%）在 200 m 内有雨水口，"
                f"AAM 0/184、DRM 0/155——而这 420 个里有 276 个是客户自己标注「有管网」的。"
                f"和排放终点查出来的边界完全一致（见 network_coverage）。",
                {"kinds_declared": _fs["kinds_declared"], "kinds_with_data": _fs["kinds_with_data"],
                 "n_facilities": _fs["n"], "by_source": _fs["by_source"],
                 "missing": _fs["kinds_missing"]},
                "swbrain/assets.py", ["asset_register", "pond_plan"],
                "客户给 exchange/assets.json 全量资产台账（编号、种类、层级、交付方、坐标、能力、状态）",
                f"Only {_fs['kinds_with_data']} of {_fs['kinds_declared']} asset kinds in the client's L1/L2/L3 framework have any data")
    except Exception:
        pass

    # ---- 第二道防线（滞蓄塘）
    PD = cfg.get("ponds") or {}
    PC = PD.get("cost") or {}
    add("pond_unit_cost", "第二道 · 造价", "placeholder",
        "塘的开挖、进水管、泵的单价全是占位值，不是客户 BoQ；方案之间的相对优劣可信，绝对造价不可引用",
        {"excavate_aed_m3": PC.get("excavate_aed_m3"), "pipe_aed_m": PC.get("pipe_aed_m"),
         "pump_capital_aed": PC.get("pump_capital_aed"), "hose_aed_m": PC.get("hose_aed_m")},
        "[ponds.cost]", ["pond_plan"], "客户给市政工程 BoQ 或近三年同类合同单价",
        "Excavation, inlet pipe and pump unit rates are placeholders, not a client BoQ; relative ranking of options is usable, absolute cost is not")
    add("pond_land", "第二道 · 选址", "missing",
        "MILP 塘规划器（swbrain/ponds.py）还没接用地约束：候选塘位来自 5 m DTM 的地形洼地，"
        "占地上限拿洼地范围当代理，没查权属 / 规划用地。374 个积水点的 land_use 全是 road（360）"
        "或 tunnel（14）——车行道和隧道里不能挖塘，所以模型不给「就地挖、水自己淌进来」"
        "这种零造价方案，一律要求把水送过去。"
        "四态对比里防线2+ 的塘位走的是另一条路（见 pond_land_l2p），已经加了用地约束；"
        "把那套判据接进 MILP 是待办。",
        {"land_use": {"road": 360, "tunnel": 14}, "max_pond_area_m2": PD.get("max_pond_area_m2")},
        "swbrain/ponds.py:conveyance_options", ["pond_plan"],
        "把 swbrain/landuse.py 的用地判据接进 MILP 候选集；客户给候选地块的权属 / 规划用地图层（要数清单 A18）",
        "The MILP pond planner has no land constraint yet: candidates come from DTM depressions with no tenure or zoning check. The Line-2+ siting in the three-state comparison uses a separate, land-constrained path (see pond_land_l2p)")
    add("pond_land_l2p", "第二道+ · 选址", "assumed",
        "四态对比里防线2+ 的塘位按用地约束定（swbrain/landuse.py，两级判据）："
        "一级用 makani 库 public.ud_plot 的 Public Open Space / Recreational / Agricultural 地块，"
        "塘的轮廓就是地块本身；二级（台账没覆盖的地方）反过来排除——建筑轮廓退让 25 m、"
        "路廊退让 20 m、台账明确标了不可挖的地块按边界排掉，剩下的栅格里找方形。"
        "**二级是兜底不是等价物**：权属、规划、地下管线都没查，「没建筑、不在路上」不等于"
        "「这块地能用」。三个区 26 个塘复核：零压建筑、零压禁挖地块、离最近道路最少 21 m；"
        "但其中 25 个落在 ud_plot 没有覆盖的地方，只有兜底判据背书。",
        {"ok_types": ["Public Open Space", "Recreational", "Agricultural"],
         "build_buffer_m": 25, "road_buffer_m": 20,
         "plots_in_registry": {"Shaliela": 34, "RB3": 7, "Mussafah": 0},
         "ponds_on_fallback_only": 25, "ponds_total": 26},
        "swbrain/landuse.py", ["three_lines"],
        "客户给候选地块的权属 / 规划用地图层 —— ud_plot 对工业区（Mussafah）零覆盖",
        "Line-2+ pond siting is land-constrained (swbrain/landuse.py): tier 1 uses makani ud_plot open-space/recreational/agricultural parcels; tier 2 is a fallback mask excluding buildings (25 m), road corridors (20 m) and parcels the registry marks non-diggable. The fallback is not a tenure record: 25 of 26 ponds rest on it alone")
    add("pond_conveyance", "第二道+ · 输水", "missing",
        "防线2+ 的 SWMM 模型把塘用**零长度孔口**直接挂在积水节点上（带闸，塘满关闸）。"
        "用地约束把塘的轮廓挪到了能挖的地上——最远挪了 658 m（RB3 NP3）——但那段输水管"
        "在模型里不存在：管长、水头损失、埋深、穿越、造价一样都没算。"
        "所以三态对比里 ③ 的削减量是**塘本身能做到的上限**，不是把水送过去之后的净效果。"
        "26 个塘里 14 个挪了 100 m 以上，这一条对它们尤其要紧。",
        {"link_type": "ORIFICE (zero length)", "max_move_m": 658,
         "ponds_moved_over_100m": 14, "ponds_total": 26},
        "tools/dig_pond3.py:dig3", ["three_lines"],
        "把输水管建进模型（按实际路由定线、定管径、算水头损失），或客户给可接受的输水方案",
        "The Line-2+ SWMM model attaches each pond to its flooded node with a zero-length gated orifice. Land siting moves the footprint up to 658 m away, but that conveyance pipe is not in the model: no length, head loss, depth, crossings or cost. State 3 therefore shows the pond's upper bound, not the net effect after conveyance")
    add("pond_storage", "第二道 · 库容", "assumed",
        "候选塘位的天然库容与洼地范围来自 5 m DTM 的填洼分析，不是实测地形；标 flat 的（353 个热点）"
        "没有真洼地、标 regional 的（5 个）洼地漫到分析窗口边界，两类都不当候选。塘的水位—库容关系"
        "按设计水深 2.5 m 的等深棱柱简化，没有实际塘型。",
        {"design_depth_m": PD.get("design_depth_m"), "excluded": PD.get("exclude_terrain_note")},
        "out/state/hotspot_terrain.parquet", ["pond_plan"],
        "客户给候选塘位的实测地形或初步塘型设计",
        "Candidate pond storage and footprint come from priority-flood analysis on the 5 m DTM, not surveyed terrain; the stage-storage relation is simplified to a prism at the design depth")
    add("flood_states", "积水点 · 过程线", "assumed", "积水过程线来自本系统水量平衡（管网缺口 × DTM 洼地），不是水动力模型；只覆盖对上管网、体积 ≥ 100 m³ 的点", None, "swbrain/floodpoints.py", ["inspect_point", "dispatch_plan", "recession_options"], "Parsons 1D 模型结果表对照（已做四个汇水区）", "Flood hydrographs come from this system's water balance (network gap × DTM depression), not a hydraulic model; only points matched to the network with ≥ 100 m³")
    # L2+B 的定容是三层，其中只有两层有依据 —— 没登记就等于藏着。
    add("l2b_sizing", "L2+B · 定容", "assumed",
        "箱涵库容 = 服务范围内溢流量 × 1.5（聚类系数）× 1.05（手册 §4.2 沉沙）；"
        "模型里的几何容积再含 0.30 m 超高（×1.15），**超高不计可用库容**。"
        "1.5 这一条手册里没有，想覆盖的是峰值同时性与进水口吞吐不足，**未经标定**",
        {"cluster_mult": 1.5, "sediment": 1.05, "freeboard_m": 0.30,
         "usable_over_gap": 1.575, "geometric_over_gap": 1.8113},
        "tools/dig_pond3.py:cluster_sites(mult)",
        ["state_table", "l2plus_why", "three_lines"],
        "按客户可接受的溢流保证率标定聚类系数，或改为按进水口吞吐能力反算",
        "Box storage = overflow served x 1.5 (clustering factor) x 1.05 (manual 4.2 sediment); "
        "the model's geometric volume further includes 0.30 m freeboard (x1.15), which is not usable storage. "
        "The 1.5 factor is not in the manual and has not been calibrated")
    return items


def summarize(items: list[dict]) -> dict:
    by = {}
    for it in items:
        by[it["level"]] = by.get(it["level"], 0) + 1
    return {"n": len(items), "by_level": by, "areas": sorted({it["area"] for it in items})}
