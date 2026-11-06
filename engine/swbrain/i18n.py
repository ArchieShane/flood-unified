"""中英双语：系统生成的内容在生成时就同时给出英文版本。

界面文字由前端词典负责；这里只管由 Python 生成的数据：建议（kind/title/when/reason/benefit/caveats/键名）、
情景名、终点类型、坡度标记、就绪度与承诺句、验证协议。
英文标题按建议类型用模板从结构化字段重排，不做机器翻译。
"""
from __future__ import annotations
import re
import pandas as pd

KIND_EN = {
    "核实终点（疑似漏登出水口/泵站）": "Verify terminal (suspected unregistered outfall/pump)",
    "核实拓扑（疑似流向/连接错误）": "Verify topology (suspected flow-direction/connection error)",
    "核实拓扑（疑似缺失连接）": "Verify topology (suspected missing link)",
    "数据治理/补测": "Data fix / re-survey",
    "扩管改造": "Pipe upsizing",
    "新增调蓄塘/池": "New detention pond/tank",
    "新增渗井群": "New soakaway cluster",
    "出水口增泵/改闸": "Outfall pump / controlled gate",
    "泵站增容校核": "Pump station capacity check",
    "连通至有出路管网": "Connect to drained network",
    "就地消纳（渗井/塘）": "Local infiltration/storage",
    "雨季前维护排序": "Pre-season maintenance priority",
    "事件前临时泵位": "Pre-event mobile pump position",
}
WHEN_CLASS_EN = {"立即": "Immediately", "雨季前": "Before rainy season", "事件前": "Before event", "并入在批": "Fold into planned project", "资本计划": "Capital plan"}
SCENARIO_EN = {"常规": "Regular (5-yr)", "强降雨": "Heavy (25-yr)", "极端": "Extreme (50-yr)", "百年一遇": "100-year", "法定标准": "Code standard (Table 3-2)"}
TERMINAL_EN = {"出水口": "outfall", "泵站": "pump station", "渗井": "soakaway", "封堵端": "capped end", "断头": "dead end"}
VALUE_EN = {
    "ok": "ok", "逆坡": "adverse slope", "过平": "too flat", "无标高": "no invert level", "DTM地面坡度": "ground slope from 5 m DTM (no invert level)", "asset": "asset", "invert": "invert", "guess": "guessed", "by_hops": "by hops", "by_invert": "by invert",
    "inlet": "inlet", "road": "road", "dtm": "DTM catchment", "台账上无": "none in register", "核实后补登": "register after verification", "无": "none", "连通": "connected", "就地消纳": "local infiltration",
    "补登后有": "present after registration", "汇水区终点附近，优先市政/未定用途地块": "near the catchment terminal, prefer utility/undesignated plots",
    "对照 GIS 与现场，补登缺失管段或终点资产": "check GIS against site; register the missing pipe or terminal asset",
    "对照 GIS 与现场补登连接管段": "check GIS against site; register the missing link",
    "补测管底标高与流向，优先承载雨强不足段与出水口上游主干": "re-survey invert levels and flow direction, starting with under-capacity reaches and trunks upstream of outfalls",
    "建议": "proposed", "sw_edge 拓扑终点分类": "sw_edge terminal classification", "sw_edge 拓扑终点分类 + 最近有出路节点距离": "sw_edge terminal classification + distance to nearest drained node",
    "pipe.sw_edge": "pipe.sw_edge", "graph.build_dag": "graph.build_dag", "sw_edge 汇水累加 + pond/soakaway/sw_reservoirstructure": "sw_edge catchment accumulation + pond/soakaway/sw_reservoirstructure",
    "pipe.inlet.cover_level（MSL=100 基准）+ outfall.invert_level": "pipe.inlet.cover_level (MSL=100 datum) + outfall.invert_level",
    "pipe.inlet.cover_level + catchment_state": "pipe.inlet.cover_level + catchment_state", "sw_edge.slope_flag / orient / dir_conflict": "sw_edge.slope_flag / orient / dir_conflict",
    "zone_state + liveability.fact_oi_indicators(sewage_blocakge_sws, 2024-01~04)": "zone_state + liveability.fact_oi_indicators (sewage_blocakge_sws, 2024-01..04)",
}
KEY_EN = {
    "现管径mm": "current diameter mm", "建议管径mm": "proposed diameter mm", "算得所需管径mm": "required diameter mm", "坡度": "slope", "曼宁n": "Manning n",
    "同终点不足段数": "under-capacity reaches to same terminal", "同终点不足长度km": "their length km", "承载雨强mm/h": "capacity rainfall mm/h",
    "汇水面积ha": "catchment ha", "设计雨深mm": "design depth mm", "现有蓄渗容量m3": "existing storage m³", "需补容量m3": "storage to add m³", "选址": "siting",
    "径流量m3": "runoff m³", "蓄渗容量m3": "storage m³", "出水口类型": "outfall type", "管底相对MSL_m": "invert rel. MSL m", "高潮位假设_m": "assumed high tide m",
    "最低一成进水口相对MSL_m": "lowest-10% inlet cover rel. MSL m", "上游汇水面积ha": "upstream catchment ha", "现有泵数": "existing pumps", "高潮净空m": "tidal freeboard m",
    "高潮时可排流量m3s": "dischargeable at high tide m³/s", "上游管长km": "upstream pipe km", "上游进水口数": "upstream inlets", "距最近有出路节点m": "distance to nearest drained node m",
    "距有出路节点m": "distance to drained node m", "建议": "action", "出路": "outlet", "需消纳径流m3": "runoff to absorb m³", "问题管长km": "affected pipe km", "数据问题份额": "share with data issues",
    "gid样例": "sample gids", "总数": "count", "承载雨强中位mm/h": "median capacity rainfall mm/h", "无出路份额": "no-outlet share", "高潮易涝出水口数": "tide-exposed outfalls",
    "疑似漏登终点数": "suspected unregistered terminals", "每百公里堵塞数": "blockages per 100 km", "蓄渗缺口m3": "storage deficit m³", "风险分": "risk score",
    "井盖标高相对MSL_m": "cover level rel. MSL m", "低于250m邻域中位m": "below 250 m neighbourhood median m", "来源": "source", "坡度标记": "slope flag", "方向来源": "direction source",
    "汇水面积法": "catchment method", "终点类型": "terminal type", "汇入边数": "inflow edges", "汇水区进水口数": "inlets in catchment", "终点表": "terminal table",
    "sw_node_outfall 覆盖": "covered by sw_node_outfall", "模型冒溢量m3": "model overflow m³",
}
for _k in list(KEY_EN):
    if _k.startswith("不足份额_") or _k.startswith("能力比_") or _k.startswith("设计流量_") or _k.startswith("汇水区不足管长km_"):
        pass


def scenario_en(name: str) -> str:
    return SCENARIO_EN.get(name, name)


def key_en(k: str) -> str:
    if k in KEY_EN:
        return KEY_EN[k]
    for zh, en in (("能力比_", "capacity ratio "), ("设计流量_", "design flow "), ("不足份额_", "under-capacity share "), ("汇水区不足管长km_", "under-capacity km in catchment ")):
        if k.startswith(zh):
            rest = k[len(zh):].replace("_m3s", " m³/s")
            return en + scenario_en(rest)
    return k


def value_en(v):
    if isinstance(v, str):
        return VALUE_EN.get(v, v)
    return v


def dict_en(d):
    if not isinstance(d, dict):
        return d
    return {key_en(k): value_en(v) for k, v in d.items()}


def when_en(when: str) -> str:
    if not isinstance(when, str):
        return when
    if when.startswith("立即"):
        return "Immediately (verify register first)" if "核实" in when else "Immediately"
    m = re.search(r"雨季前（(\d{4}-\d{2}-\d{2}) 前完成）", when)
    if m:
        return f"Before the rainy season (complete by {m.group(1)})"
    m = re.search(r"≥ (\d+) mm", when)
    if when.startswith("事件前"):
        return f"24–72 h before an event (trigger: NCM forecast ≥ {m.group(1) if m else 20} mm in 24 h)"
    m = re.search(r"「(.+?)」", when)
    if when.startswith("并入在批") and m:
        return f"Fold into planned project “{m.group(1)}” at its next approval milestone"
    if when.startswith("下一年度资本计划"):
        return "Next annual capital plan (IFP approval cycle)"
    return when


REASON_EN = {
    "不花工程钱，但决定上面所有工程建议的可信度": "No construction cost, but it decides how much the engineering proposals can be trusted",
    "环路使汇水累加失真": "Cycles distort the catchment accumulation",
    "工程类；渗蓄制汇水区靠渗蓄消纳，缺口越大越应排在前": "Engineering; infiltration-based catchments rely on storage, the larger the deficit the earlier it goes",
    "工程类；高潮遇暴雨时拍门不开、上游满管，是沿海排水最典型的失效": "Engineering; flap gates stay shut when high tide meets heavy rain and the upstream surcharges — the classic coastal drainage failure",
    "这么大的汇水区不可能真的没有出路；八成是台账里少了一段管或一个出水口/泵站。核实后上游所有建议的可信度都会变": "A catchment this large cannot truly lack an outlet; most likely a pipe, outfall or pump station is missing from the register. Verifying it changes the confidence of every upstream proposal",
    "工程类；先核实是台账漏登还是真断头，再决定连通或消纳": "Engineering; first verify whether this is a register gap or a real dead end, then connect or absorb locally",
    "几米的缺口不是工程，是台账；补登后这一支的汇水归属就通了": "A gap of a few metres is a register issue, not a project; once registered, the branch drains",
    "管径跳三倍以上不是设计问题，是台账问题；核实后这一支的所有建议都会变": "A threefold jump in required diameter is a register problem, not a design one; verifying it changes every proposal on this branch",
    "维护窗口在雨季前；排序依据是承载雨强不足份额、无出路份额、高潮易涝出水口数与去年堵塞率": "Maintenance window is before the rainy season; ranking uses under-capacity share, no-outlet share, tide-exposed outfalls and last year's blockage rate",
    "调度类：不改设施，按预报把移动泵与人员预置到低点": "Operations; no asset change, pre-position mobile pumps and crews at low points on forecast",
}
CAVEAT_EN = {
    "推理公式+曼宁满管流的规划级筛查；雨强取 DMT WA-726-1 (2022) B 区 IDF，单口汇水面积为假设值；扩管前先看下游段能力": "Planning-level screening (rational method + Manning full-pipe); rainfall from the DMT WA-726-1 (2022) Zone B IDF, per-inlet catchment is an assumption; check downstream capacity before upsizing",
    "渗井容积按尺寸估算、缺尺寸取默认值；土壤渗透率未计；终点若实为未登记的连接，缺口会被高估": "Soakaway volumes estimated from dimensions (defaults where missing); soil permeability not included; if the terminal is an unregistered connection the deficit is overstated",
    "潮位取 DMT 手册设计高潮 +1.3 m NADD（表 4-5）；井盖标高为台账值": "Tide level is the DMT manual design high tide +1.3 m NADD (Table 4-5); cover levels are register values",
    "断头可能是数据漏登（源库有 1.3 万条拓扑问题记录），列为先核实项": "Dead ends may be register gaps (the source holds 13,000 topology issue records); listed as verify-first",
    "堵塞统计只覆盖 2024 年 1–4 月": "Blockage statistics cover Jan–Apr 2024 only",
    "井盖标高是台账值，需现场核": "Cover levels are register values; confirm on site",
    "": "",
}


def _num(s: str, pat: str, default=None):
    m = re.search(pat, s or "")
    return m.group(1) if m else default


def title_en(p: dict) -> str:
    kind, zone = p.get("kind"), p.get("zone_name")
    zone = zone if isinstance(zone, str) and zone.strip() else "Unzoned"
    a = p.get("action") or {}
    t = p.get("title") or ""
    uid = _num(t, r"[：:]\s*(?:断头|封堵端|渗井|出水口|进水口|管段|DN\d+ 管段)?(?:汇水区)?\s*([A-Za-z0-9_\-]+|节点\d+)") or ""
    uid = uid.replace("节点", "node ")
    tk = TERMINAL_EN.get(_num(t, r"：(断头|封堵端|渗井|出水口)") or "", "")
    if kind == "核实终点（疑似漏登出水口/泵站）":
        d = a.get("距最近有出路节点m")
        return f"{zone}: dead end {uid} drains {a.get('上游汇水面积ha', '?')} ha / {a.get('上游管长km', '?')} km with no registered outlet" + (f"; nearest drained node {d:.0f} m away" if d is not None else "")
    if kind == "核实拓扑（疑似流向/连接错误）":
        return f"{zone}: DN{int(a.get('现管径mm', 0))} pipe {uid} is computed to carry {a.get('上游汇水面积ha', '?')} ha / {_num(t, r'(\d+) 个进水口', '?')} inlets and would need DN{int(a['算得所需管径mm']) if a.get('算得所需管径mm') else '≥3000'}; most likely a flow-direction or connection error"
    if kind == "核实拓扑（疑似缺失连接）":
        return f"{zone}: {tk or 'dead end'} {uid} drains {a.get('上游汇水面积ha', '?')} ha with no outlet, yet a drained node lies {a.get('距有出路节点m', '?')} m away; most likely a missing link in the register"
    if kind == "数据治理/补测":
        if "打断有向环" in t:
            return f"Removed the direction of {a.get('总数', '?')} edges to break cycles; verify their flow direction"
        return f"{zone}: {_num(t, r'(\d+)% 管长', '?')}% of pipe length lacks invert levels or has unknown/conflicting direction; re-survey before engineering"
    if kind == "扩管改造":
        return f"{zone}: upsize pipe {uid} from DN{int(a.get('现管径mm', 0))} to DN{int(a['建议管径mm']) if a.get('建议管径mm') else '≥3000'}"
    if kind in ("新增调蓄塘/池", "新增渗井群"):
        return f"{zone}: add {float(a.get('需补容量m3', 0)):,.0f} m³ of storage/infiltration in {tk or 'the'} catchment {uid}"
    if kind in ("出水口增泵/改闸", "泵站增容校核"):
        fb = _num(t, r"净空 (-?[\d.]+) m", "?")
        return f"{zone}: outfall {uid} — lowest 10% of inlets have only {fb} m freeboard above high tide; " + ("existing pump station needs a capacity check" if kind == "泵站增容校核" else "add a pump or a controlled gate")
    if kind in ("连通至有出路管网", "就地消纳（渗井/塘）"):
        d = a.get("距有出路节点m")
        return f"{zone}: {tk or 'dead end'} {uid} drains {a.get('上游汇水面积ha', '?')} ha with no outlet" + (f"; nearest drained node {d:.0f} m away" if d is not None else "")
    if kind == "雨季前维护排序":
        return f"{zone}: risk score {_num(t, r'风险分 (\d+)', '?')}; prioritise desilting, flap-gate and inlet checks"
    if kind == "事件前临时泵位":
        return f"{zone}: inlet {uid} sits {_num(t, r'低 ([\d.]+) m', '?')} m below its 250 m neighbourhood median and its catchment is overloaded in heavy rain; pre-position a mobile pump"
    return f"{KIND_EN.get(kind, kind)} — {zone}"


def benefit_en(p: dict) -> str:
    b, kind = p.get("benefit") or "", p.get("kind")
    n = _num(b, r"(\d[\d,]*) 个进水口")
    if kind == "扩管改造":
        m = re.search(r"从 (\d+) 提到 ≥(\d+)", b)
        return f"Capacity rainfall for {n} upstream inlets rises from {m.group(1)} to ≥{m.group(2)} mm/h" if (n and m) else "Needs hydraulic-model check"
    if kind in ("新增调蓄塘/池", "新增渗井群"):
        d = _num(b, r"在 (\d+) mm")
        return f"Catchment serving {n} inlets no longer overflows in a {d} mm event"
    if kind in ("出水口增泵/改闸", "泵站增容校核"):
        km = _num(b, r"([\d.]+) km")
        return f"Keeps the outlet for {n} inlets / {km} km of network open at high tide"
    if kind in ("核实终点（疑似漏登出水口/泵站）", "核实拓扑（疑似缺失连接）"):
        return f"Catchment assignment confirmed for {n} inlets"
    if kind == "核实拓扑（疑似流向/连接错误）":
        return "Removes one topology error that misstates the whole branch"
    if kind == "数据治理/补测":
        return "Consolidation error removed" if "累加" in b else "Confidence of engineering proposals in this zone rises from 0.5 to above 0.9"
    if kind in ("连通至有出路管网", "就地消纳（渗井/塘）"):
        return f"{n} inlets gain an outlet"
    if kind == "雨季前维护排序":
        return "Puts limited maintenance crews where failure is most likely"
    if kind == "事件前临时泵位":
        return "Shortens ponding time at the low point"
    return b


def add_english(props: pd.DataFrame) -> pd.DataFrame:
    if not len(props):
        return props
    p = props.copy()
    recs = p.to_dict(orient="records")
    p["kind_en"] = [KIND_EN.get(r["kind"], r["kind"]) for r in recs]
    p["when_class_en"] = [WHEN_CLASS_EN.get(r["when_class"], r["when_class"]) for r in recs]
    p["when_en"] = [when_en(r["when"]) for r in recs]
    p["title_en"] = [title_en(r) for r in recs]
    p["benefit_en"] = [benefit_en(r) for r in recs]
    def _reason(x):
        if x in REASON_EN:
            return REASON_EN[x]
        m = re.search(r"同一终点下共 (\d+) 段", x or "")
        if m:
            return f"Engineering: needs approval; {m.group(1)} reaches draining to the same terminal are under capacity — upgrade the run as one project"
        return x
    p["when_reason_en"] = [_reason(r.get("when_reason")) for r in recs]
    p["caveats_en"] = [CAVEAT_EN.get(r.get("caveats") or "", r.get("caveats")) for r in recs]
    p["action_en"] = [dict_en(r.get("action")) for r in recs]
    p["before_en"] = [dict_en(r.get("before")) for r in recs]
    p["after_en"] = [dict_en(r.get("after")) for r in recs]
    p["evidence_en"] = [dict_en(r.get("evidence")) for r in recs]
    return p


# ---------- 就绪度、承诺 ----------
READY_EN = {
    "验收标准已定义": "Acceptance standard defined", "物理基线可信": "Physical baseline trustworthy", "预测模型有观测校准": "Prediction model calibrated to observations",
    "不确定性进入优化": "Uncertainty in the optimisation", "实施后有验证与运行规则": "Post-implementation verification and operating rules", "雨后回灌闭环": "Post-event feedback loop",
    "就绪": "Ready", "部分": "Partial", "缺": "Missing",
}


def readiness_en(rows: list[dict], cfg: dict, es_stats: dict) -> list[dict]:
    S = cfg["standard"]
    fix = {
        "验收标准已定义": ("Config has target scenario “%s” and per-land-use acceptable depths, but rainfall and depth thresholds are placeholders not yet confirmed by DMT" % scenario_en(S["target_scenario"]),
                    "DMT confirms the standard in writing; replace rainfall with ADM IDF"),
        "物理基线可信": ("%d suspected unregistered terminals carry %s km; %d%% of pipe length lacks invert levels or has guessed direction; DEM %s" % (es_stats["n_suspect"], es_stats["suspect_km"], es_stats["bad_pct"], "present" if es_stats["dem"] else "absent"),
                   "Verify the “Immediately” proposals; re-survey levels; put a DEM in exchange/dem/"),
        "预测模型有观测校准": ("Baseline predictions: %d; observed events: %d; pump telemetry %s" % (es_stats["n_preds"], es_stats["n_obs"], "present" if es_stats["telemetry"] else "absent"),
                      "Prediction side supplies baselines; observations after every event (event_observation); connect SCADA"),
        "不确定性进入优化": ("Proposals are weighted across %d scenarios, but the weights are relative, not measured probabilities; climate factor %s is an assumption" % (len(S["scenario_weights"]), S["climate_factor"]),
                     "Derive annual probabilities per scenario from IDF and climate scenarios"),
        "实施后有验证与运行规则": ("No telemetry; pump station telemetry_linked is empty; “before event” actions can only be forecast-triggered",
                        "Connect ADM SCADA; write pump/gate rules into the operations layer"),
        "雨后回灌闭环": ("Comparison workflow and file formats defined (exchange/observations → exchange/verification); events reconciled: %d" % es_stats["n_obs"],
                   "Deliver one observation file after the first rain; the loop starts"),
    }
    out = []
    for r in rows:
        ev, todo = fix.get(r["条件"], (r["证据"], r["补什么"]))
        out.append({**r, "condition_en": READY_EN.get(r["条件"], r["条件"]), "status_en": READY_EN.get(r["状态"], r["状态"]), "evidence_en": ev, "todo_en": todo})
    return out


def commitment_en(cfg: dict, standard_plan: dict, readiness_rows: list[dict]) -> dict:
    S = cfg["standard"]
    years = standard_plan.get("years")
    n_ready = sum(1 for r in readiness_rows if r["状态"] == "就绪")
    n_missing = sum(1 for r in readiness_rows if r["状态"] == "缺")
    tgt = scenario_en(S["target_scenario"])
    if years is None:
        s = f"The time to meet the “{tgt}” standard cannot be stated yet (cost or budget missing)"
    elif years > S["max_years"]:
        s = f"Meeting the “{tgt}” standard at {S['annual_budget_aed']/1e9:.1f} bn AED/yr takes {years} years, beyond the {S['max_years']}-year cap: raise the budget, relax the acceptable depth, or lower the return period"
    else:
        s = f"Within {years} years, eliminate ponding above the acceptable depth to the “{tgt}” standard; residual risk is mapped (residual.geojson) and every rain event is reconciled (exchange/verification/)"
    return {"commitment_en": s, "readiness_en": f"{n_ready}/6 ready, {n_missing}/6 missing",
            "note_en": "Years are computed from placeholder unit costs and a static cost-to-standard; proposals will change after prediction-model evaluation; until all conditions are ready this is a target, not a guarantee"}


def protocol_markdown_en(cfg: dict) -> str:
    V, S = cfg["verification"], cfg["standard"]
    depth = ", ".join(f"{k} ≤ {v} m" for k, v in S["acceptable_depth_m"].items())
    return f"""# Post-event verification protocol (generated from config)

## 1. When to reconcile
Any NCM rain gauge recording ≥ {V['event_rain_trigger_mm']:.0f} mm in 24 h defines an event to reconcile. Events are identified by date.

## 2. What to compare
The prediction side runs `prediction_state` before the event with forecast rain and after it with observed rain; the field team delivers an `event_observation` within 72 h (format: `exchange/examples/event_observation.example.json`). Sources: {", ".join(V['observation_sources'])}.

Three node-level metrics:
- Probability of detection POD = predicted-and-observed / observed; pass ≥ {V['pass_pod_min']}
- False alarm ratio FAR = predicted-not-observed / predicted; pass ≤ {V['pass_far_max']}
- Depth RMSE on points with depth on both sides; pass ≤ {V['pass_depth_rmse_m']} m

“Ponding” follows the acceptance standard: {depth}; duration ≤ {S['acceptable_duration_min']} min.

## 3. On failure
{V['recalibrate_after_failures']} consecutive failed events: the prediction side recalibrates (first checking that register fixes reached the model), and this system re-runs proposals and schedule against the new baseline. Passed events write observed ponding points into `proposal.evidence` for the next ranking.

## 4. Sign-off
Both sides generate `exchange/verification/<event_id>.json`; numbers must match. DMT confirms in `sw_brain.swbrain.verification`.
"""


# ---------------------------------------------------------------- 第二、三道防线：调度侧数据串
TRIAGE_EN = {
    "无需处置（已基本退水）": "No action needed (mostly receded)",
    "入流仍大于可派排量：先封路疏散，雨峰后再派": "Inflow still exceeds deployable pumping: close road and evacuate, dispatch after the peak",
    "1.5 km 内无可接纳的排放去处：等待管网退水或长距离接力": "No receiving discharge within 1.5 km: wait for network recession or a long relay",
    "可派泵（需接力排放）": "Dispatchable (relay discharge needed)",
    "可派泵": "Dispatchable",
}
TRAVEL_EN = {
    "路网 Dijkstra（积水路段加罚/断开）": "Road-network Dijkstra (flooded links penalised / cut)",
    "直线距离兜底": "Straight-line fallback",
}


def dispatch_en(s: str) -> str:
    return TRIAGE_EN.get(s) or TRAVEL_EN.get(s) or s


def scenario_name_en(name: str) -> str:
    """“50年一遇（模拟）” → “50-year (simulated)”；四个设计情景走 SCENARIO_EN。"""
    if not isinstance(name, str):
        return name
    if name in SCENARIO_EN:
        return SCENARIO_EN[name]
    out = re.sub(r"(\d+)\s*年一遇", r"\1-year", name)
    out = out.replace("（模拟）", " (simulated)").replace("(模拟)", " (simulated)")
    return out.strip()


def note_en(note: str) -> str:
    """模拟器与常见世界模型备注的英文；认不出的原样返回。"""
    if not isinstance(note, str) or not note:
        return note or ""
    if note == "道路隧道低点（模拟）":
        return "Road tunnel low point (simulated)"
    m = re.match(r"汇水区终点 (.+?)，(.+?)下承载不足份额 (\d+%)（模拟）", note)
    if m:
        return f"Catchment terminal {m.group(1)}, {scenario_name_en(m.group(2))} over-capacity share {m.group(3)} (simulated)"
    m = re.match(r"(\w+) (.+?) 门前（模拟）", note)
    if m:
        return f"In front of {m.group(1)} {m.group(2)} (simulated)"
    return note
