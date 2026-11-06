"""本体（Ontology）声明：以「积水点」为核心的对象类型、属性、关系、规则与动作。

借 Palantir Foundry Ontology 的骨架：
- 对象类型（Object Type）：带主键、标题属性和一组带类型的属性；每个对象类型声明由 runtime 的哪个装载器物化。
- 关系（Link Type）：对象之间有方向、有基数的连接，靠属性值对齐（外键）。
- 规则（Rule）：可执行的判据。derive = 派生属性（等级、目标时限、接力折减…），validate = 数据校验（坐标错行、无管网却有终点…），
  trigger = 触发建议（III 级未达标→升级处置、观测与推演冲突→待核）。每条规则都是对象字典上的纯函数，能单独解释、单独测试。
- 动作（Action Type）：智能体唯一能对系统做的事。参数带类型与范围、前置条件、副作用声明（只读 / 写 out/…），执行记录进动作日志。
这里只做声明；装载、关系遍历、规则求值与动作执行在 runtime.py，智能体（规划器 + 回答）在 agent.py。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

# ---------------------------------------------------------------- 元模型


from .. import lifecycle as LC


@dataclass(frozen=True)
class Property:
    name: str
    type: str                     # str | int | float | bool | enum | list | dict | geo
    zh: str
    en: str = ""
    desc: str = ""
    derived_by: str | None = None  # 由哪条规则派生（可执行规则的 api）
    source: str = ""              # 数据来源（文件 / 模块）


INLINE = "(get/search 内联)"      # 这三类没有单独的装载方法，在 runtime.get/search 里就地取数


@dataclass(frozen=True)
class ObjectType:
    api: str
    zh: str
    en: str
    pk: str
    title: str
    desc: str
    properties: tuple[Property, ...]
    loader: str                   # runtime 里的装载方法名
    scoped: bool = False          # True = 随情景变化（主键含情景）

    def prop(self, name: str) -> Property | None:
        return next((p for p in self.properties if p.name == name), None)


@dataclass(frozen=True)
class LinkType:
    api: str
    zh: str
    en: str
    src: str
    dst: str
    card: str                     # one | many（从 src 看 dst 的基数）
    how: str                      # 怎么对齐


@dataclass(frozen=True)
class Rule:
    api: str
    zh: str
    en: str
    applies_to: str
    kind: str                     # derive | validate | trigger
    logic_zh: str
    logic_en: str
    source: str                   # 代码 / 规范出处
    fn: Callable[[dict, dict], Any]   # (对象字典, 上下文{cfg, runtime, scenario}) → 值；validate/trigger 返回 None 表示不触发


@dataclass(frozen=True)
class Parameter:
    name: str
    type: str                     # str | int | float | bool | enum | fp_id | scenario | zone | pid | rule
    zh: str
    en: str = ""
    required: bool = False
    choices: tuple[str, ...] | None = None
    lo: float | None = None
    hi: float | None = None
    default: Any = None


@dataclass(frozen=True)
class ActionType:
    api: str
    zh: str
    en: str
    desc_zh: str
    desc_en: str
    params: tuple[Parameter, ...]
    preconditions: tuple[str, ...] = ()      # 前置条件（人读；执行时由 runtime 对应校验）
    side_effect: str = "none"                # none | 写 out/…（永不写源库）
    intents: tuple[str, ...] = ()            # 规则规划器的意图关键词（中英）
    examples: tuple[str, ...] = ()           # 示例问法
    ui: str = ""                             # 控制台联动说明
    owner: str = "dispatch"                  # 归哪个智能体（swbrain/agents/registry.py）：dispatch / hydro / report / qa

    def param(self, name: str) -> Parameter | None:
        return next((p for p in self.params if p.name == name), None)


# ---------------------------------------------------------------- 对象类型
P = Property
OBJECT_TYPES: tuple[ObjectType, ...] = (
    ObjectType("FloodPoint", "积水点", "Flood point", "fp_id", "location",
               "核心对象：一个会积水的地点。来自客户 845 个热点（ADM/AAM/DRM）并被本系统补齐地形、匹配管段与全网溢流；主键沿用客户编号（ADM-62）。与情景无关的静态属性放这里，随雨变的放 FloodState。",
               (P("fp_id", "str", "编号", "id", source="客户热点清单 v4.4"), P("region", "enum", "所属市政", "authority", "ADM / AAM / DRM"), P("area", "str", "片区（客户）", "area"), P("location", "str", "位置描述", "location"),
                P("lon", "float", "经度", "lon", "KMZ 修正后的坐标", source="506 Hot Spots AUH.kmz"), P("lat", "float", "纬度", "lat"), P("coord_source", "enum", "坐标来源", "coordinate source", "kmz = 已用 KMZ 修正；xlsx = 只有表格坐标"),
                P("criticality", "enum", "客户重要度", "client criticality", "Very High / High / Medium / Low"), P("root_cause", "str", "根因（归类）", "root cause"), P("network_available", "bool", "客户标注有无管网", "network available (client)"),
                P("planA_pumps", "int", "预案 A 泵数", "Plan A pumps"), P("planA_pump12", "int", "预案 A 12 寸泵", "Plan A 12in"), P("planA_pump8", "int", "预案 A 8 寸泵", "Plan A 8in"), P("planA_pump6", "int", "预案 A 6 寸泵", "Plan A 6in"), P("planA_tankers", "int", "预案 A 罐车", "Plan A tankers"), P("planA_manpower", "int", "预案 A 人力", "Plan A manpower"),
                P("idd_planned_flag", "enum", "IDD 干预计划", "IDD plan", "yes / assessing / no"), P("idd_category", "str", "IDD 类别", "IDD category"), P("idd_stage", "str", "IDD 阶段", "IDD stage"), P("idd_owner", "str", "IDD 责任方", "IDD owner"), P("idd_completion", "str", "IDD 完成时间", "IDD completion"), P("design_event_future", "str", "目标设计事件", "target design event"),
                P("dtm_z", "float", "DTM 地面高程 m", "DTM ground m", source="5 m DTM"), P("low_z", "float", "洼地最低点 m", "depression low m"), P("spill_z", "float", "洼地溢出高程 m", "spill level m"), P("depth_max_m", "float", "洼地最大深 m", "depression depth m"), P("pond_volume_m3", "float", "洼地容积 m³", "depression volume m³"),
                P("edge_gid", "int", "匹配管段 gid", "matched reach gid", source="floodpoints.match_hotspots"), P("nf_spill_m3_10", "float", "10 年全网路由溢流 m³", "10-yr routed spill m³", source="networkflood"), P("nf_backwater_10", "bool", "10 年下游顶托", "10-yr backwater"),
                P("matched", "bool", "本系统有算出积水过程", "has system flood state", derived_by="has_state"),
                P("n_events", "int", "历史降雨事件数", "historical rain events", "RainEvent(kind=historical) 的个数"), P("n_flooded", "int", "有记录积过水的事件数", "events with recorded flooding"),
                P("recurrence", "str", "复发率（有观测的事件里积水几次）", "recurrence (flooded / observed events)", derived_by="recurrence"), P("recurrence_model", "str", "模型复发率（推演积水 / 事件数）", "model recurrence"),
                P("last_flooded", "str", "最近一次有记录积水", "last recorded flooding"), P("lifecycle_status", "enum", "当前生命周期状态", "lifecycle status", "平时 idle，或进行中 / 预报事件下的阶段：alerted / staged / reported / verified / dispatched / arrived / pumping / dry / closed / reviewed", derived_by="lifecycle_status"),
                P("treatment_status", "enum", "工程治理状态", "treatment status", "none / proposed / approved / under_construction / built / verified / rejected（相关工程建议里最靠后的状态）", derived_by="treatment_status")),
               "load_flood_points"),
    ObjectType("FloodState", "积水状态", "Flood state", "state_id", "fp_id",
               "积水点在某个情景（重现期 / 实测事件）下的状态：体积、水深、等级、可派性、派单与结果。主键 fp_id@情景。",
               (P("state_id", "str", "状态主键", "id"), P("fp_id", "str", "积水点", "flood point"), P("scenario_key", "str", "情景", "scenario"),
                P("volume_m3", "float", "峰值体积 m³", "peak volume m³", source="floodpoints 水量平衡"), P("max_depth_m", "float", "峰值水深 m", "peak depth m"), P("duration_min", "int", "积水时长 min（无泵）", "flood duration (no pumps)"), P("spill_m3", "float", "上游溢流量 m³", "upstream spill m³"),
                P("source", "enum", "算法来源", "method", "network_gap = 管网缺口；dtm_proxy = 无管网按洼地"), P("top_kind", "str", "300 m 内最重要设施", "top facility within 300 m"), P("facilities", "list", "300 m 内设施", "facilities within 300 m"),
                P("level", "enum", "应急等级", "emergency level", "III / II / I（WA-726-3 表 11-1）", derived_by="emergency_level"), P("level_priority", "enum", "处置优先级", "priority", "A / B / C"), P("target_drain_min", "float", "目标排干时限 min", "target drain time", derived_by="target_drain_time"), P("priority", "float", "调度优先级分", "dispatch priority"),
                P("dispatchable", "bool", "可派泵", "dispatchable", derived_by="dispatchable"), P("triage", "str", "分诊结论", "triage"), P("discharge_to", "str", "排放去处", "discharge point"), P("discharge_dist_m", "float", "排放距离 m", "discharge distance m"), P("relay_needed", "bool", "需接力排放", "relay needed", derived_by="relay_factor"),
                P("volume_now_m3", "float", "决策时刻体积 m³", "volume at decision time"), P("inflow_now_m3h", "float", "决策时刻入流 m³/h", "inflow at decision time"), P("required_rate_m3h", "float", "需排量 m³/h", "required pump rate"),
                P("served", "bool", "已派", "served"), P("units_assigned", "int", "派泵台数", "units assigned"), P("tanker_served", "bool", "罐车覆盖", "served by tankers"), P("first_arrive_min", "float", "首台到位 min", "first arrival"), P("done_min", "float", "排干 min", "drained at"), P("within_target", "bool", "达标", "within target", derived_by="within_target"),
                P("gwm_core", "str", "对上的世界模型推演核", "world-model core"), P("gwm_dist_m", "float", "到推演核距离 m", "distance to core"), P("gwm_depth_m", "float", "推演核水深 m", "core depth m"), P("world_model_confirmed", "bool", "世界模型印证", "world-model confirmed", derived_by="world_model_confirmed")),
               "load_states", scoped=True),
    ObjectType("Scenario", "情景", "Scenario", "key", "label",
               "一场雨：重现期（5/10/25/50/100 年）、重现期 + 气候变化、或实测事件（2024-04、2026-03）。带该情景下全网与调度的汇总。",
               (P("key", "str", "键", "key"), P("kind", "enum", "类型", "kind", "rp / rp_cc / event"), P("label", "str", "名称", "label"), P("label_en", "str", "名称（英）", "label (en)"), P("i15_mmh", "float", "15 分钟雨强 mm/h", "15-min intensity"), P("p24_mm", "float", "24 h 雨量 mm", "24 h depth"), P("source", "str", "雨量来源", "rain source"),
                P("n_points", "int", "积水点数", "flood points"), P("points_by_level", "dict", "按等级", "by level"), P("n_served", "int", "已派点数", "points served"), P("units_used", "int", "用泵台数", "pumps used"), P("critical_within_target", "int", "III 级达标数", "level III within target"), P("critical_total", "int", "III 级总数", "level III total"),
                P("n_edges_over", "int", "超载管段数", "reaches over capacity"), P("surcharge_share", "float", "超载管长份额", "overloaded length share"), P("spill_total_m3", "float", "全网路由溢流 m³", "routed spill m³"), P("decision_time_min", "float", "决策时刻 min", "decision time"), P("cost_total_aed", "float", "应急成本 AED", "response cost AED"),
                P("alert_level", "enum", "预警等级", "alert level", "蓝 / 黄 / 橙 / 红", derived_by="alert_level")),
               INLINE),
    ObjectType("Reach", "管段", "Reach", "gid", "unitid", "雨水管网的一段（源库 sw_edge），带设计校核与 10 年全网路由结果。",
               (P("gid", "int", "gid", "gid"), P("unitid", "str", "台账编号", "unit id"), P("zone_name", "str", "片区", "zone"), P("len_m", "float", "长度 m", "length"), P("pipe_diameter", "float", "管径 mm", "diameter"), P("pipe_material", "str", "材质", "material"), P("slope", "float", "坡度", "slope"), P("q_full", "float", "满管流量 m³/s", "full-pipe flow"),
                P("terminal_kind", "str", "下游终点类型", "terminal kind"), P("design_class", "str", "设计类别", "design class"), P("design_rp_yr", "int", "法定重现期", "statutory RP"), P("ratio_design", "float", "设计流量/能力", "design ratio"), P("compliant", "bool", "法定标准达标", "compliant", derived_by="reach_compliance"),
                P("n_inlets_up", "int", "上游进水口数", "upstream inlets"), P("area_m2", "float", "汇水面积 m²", "catchment m²"), P("nf_spill_m3_10", "float", "10 年路由溢流 m³", "10-yr spill"), P("nf_backwater_10", "bool", "10 年下游顶托", "10-yr backwater")),
               INLINE),
    ObjectType("Zone", "片区", "Zone", "zone_name", "zone_name", "管网片区（sw_edge.zone_name），带风险分、超载份额、蓄渗缺口、堵塞率。",
               (P("zone_name", "str", "片区", "zone"), P("district_name", "str", "行政区", "district"), P("n_edges", "int", "管段数", "reaches"), P("len_km", "float", "管长 km", "length km"), P("risk_score", "float", "风险分", "risk score"), P("over_share_常规", "float", "常规雨超载份额", "over share (routine)"), P("over_share_极端", "float", "极端雨超载份额", "over share (extreme)"),
                P("storage_deficit_m3", "float", "蓄渗缺口 m³", "storage deficit"), P("n_tide_outfalls", "int", "高潮暴露出水口", "tide-exposed outfalls"), P("n_suspect_outlets", "int", "疑似漏登终点", "suspect terminals"), P("blockage_per_100km", "float", "堵塞/100 km", "blockages per 100 km"), P("n_flood_points", "int", "积水点数", "flood points", derived_by="zone_points")),
               INLINE),
    ObjectType("PumpUnit", "泵 / 罐车", "Pump unit", "unit_id", "unit_id", "一台可派的移动泵或罐车（客户预案 A 的泵队按驻地展开）。",
               (P("unit_id", "str", "编号", "id"), P("model", "str", "型号", "model"), P("kind", "enum", "种类", "kind", "pump / tanker"), P("rate_m3h", "float", "额定排量 m³/h", "nominal rate"), P("setup_min", "float", "架设 min", "setup"), P("depot_id", "str", "驻地", "depot"), P("duty_aed_h", "float", "台班费 AED/h", "hourly rate")),
               "load_units"),
    ObjectType("Depot", "驻地 / 预置点", "Depot", "depot_id", "name", "泵队驻地（民防中心等）与预置候选驻地。",
               (P("depot_id", "str", "编号", "id"), P("name", "str", "名称", "name"), P("kind", "enum", "种类", "kind", "depot / staging"), P("area", "str", "片区", "area"), P("lon", "float", "经度", "lon"), P("lat", "float", "纬度", "lat"), P("capacity_units", "int", "容量（台）", "capacity"), P("plan_a", "dict", "预案 A 资源", "Plan A resources")),
               "load_depots"),
    ObjectType("Assignment", "派单", "Assignment", "assignment_id", "fp_id", "某情景、某决策时刻下，一台泵到一个积水点的派遣（波次、行程、到位、排干、达标）。",
               (P("assignment_id", "str", "主键", "id"), P("scenario_key", "str", "情景", "scenario"), P("wave", "int", "波次", "wave"), P("unit_id", "str", "泵", "unit"), P("model", "str", "型号", "model"), P("fp_id", "str", "积水点", "flood point"), P("depot_id", "str", "出发驻地", "from depot"),
                P("depart_min", "float", "出发 min", "depart"), P("arrive_min", "float", "到位 min", "arrive"), P("pumping_from_min", "float", "开泵 min", "pumping from"), P("done_min", "float", "排干 min", "done"), P("rate_m3h", "float", "折减后排量 m³/h", "effective rate"), P("relay_stages", "int", "接力级数", "relay stages"), P("within_target", "bool", "达标", "within target")),
               "load_assignments", scoped=True),
    ObjectType("Proposal", "工程建议", "Proposal", "pid", "title", "第一道防线的基础设施建议（扩管 / 调蓄 / 核实终点 / 维护 …），带优先级、置信度、法定依据与对应客户热点。",
               (P("pid", "int", "编号", "id"), P("kind", "str", "类型", "kind"), P("title", "str", "标题", "title"), P("zone_name", "str", "片区", "zone"), P("gid", "int", "管段 gid", "reach gid"), P("terminal", "int", "终点节点", "terminal node"), P("priority", "float", "优先级", "priority"), P("confidence", "str", "置信度", "confidence"),
                P("when_class", "str", "分期", "phase"), P("status", "enum", "状态", "status", "建议 → 已批准 → 在建 → 已建成 → 已验证有效（或 已否决）；set_proposal_status 动作推进，记 out/proposal_status.jsonl"), P("status_at", "str", "状态时间", "status changed at"), P("status_note", "str", "状态备注", "status note"), P("regulatory_flag", "bool", "法定标准要求", "statutory"), P("client_hotspot_id", "str", "对应客户热点", "client hotspot"), P("client_criticality", "str", "热点重要度", "hotspot criticality")),
               "load_proposals"),
    ObjectType("GwmCore", "世界模型推演核", "World-model core", "core_id", "fp_id", "世界模型（GWM）250 m 格网推演聚成的积水核，带与客户热点 / 本系统的对照标签。主键 run_id:fp_id。",
               (P("core_id", "str", "主键", "id"), P("run_id", "str", "推演", "run"), P("fp_id", "str", "核编号", "core id"), P("lon", "float", "经度", "lon"), P("lat", "float", "纬度", "lat"), P("max_depth_m", "float", "最大水深 m", "max depth"), P("volume_m3", "float", "体积 m³", "volume"), P("area_m2", "float", "面积 m²", "area"),
                P("first_wet_min", "int", "起积 min", "wet from"), P("peak_min", "int", "到峰 min", "peak at"), P("agreement", "enum", "对照", "agreement", "both / hotspot / system / model_only"), P("hs_ids", "list", "对上的热点", "matched hotspots"), P("sys_ids", "list", "对上的本系统点", "matched system points")),
               "load_gwm_cores", scoped=True),
    ObjectType("Alert", "预警提示单", "Alert advisory", "alert_id", "headline", "按预报雨量代入规则层得到的等级与分角色提示（issue_alert 动作的产物，落 out/alerts/）。",
               (P("alert_id", "str", "主键", "id"), P("issued_at", "str", "签发时间", "issued at"), P("p24_mm", "float", "预报 24 h 雨量", "forecast 24 h mm"), P("lead_h", "float", "提前量 h", "lead time"), P("scenario_key", "str", "对应情景", "scenario"), P("level", "enum", "等级", "level", "蓝 / 黄 / 橙 / 红"), P("headline", "str", "标题", "headline"), P("n_actions", "int", "提示条数", "actions")),
               "load_alerts"),
    ObjectType("Observation", "现场观测", "Field observation", "obs_id", "fp_id", "现场对某积水点的一次观测（record_observation 动作写入 out/observations.jsonl），是校核推演的地面真值。",
               (P("obs_id", "str", "主键", "id"), P("fp_id", "str", "积水点", "flood point"), P("observed_at", "str", "观测时间", "observed at"), P("depth_m", "float", "水深 m", "depth"), P("status", "enum", "状态", "status", "flooding / receding / dry"), P("note", "str", "备注", "note"), P("reporter", "str", "上报人", "reporter")),
               "load_observations"),
    ObjectType("RainEvent", "降雨事件", "Rain event", "event_id", "label",
               "一场真实（或预报中 / 进行中）的雨：历史事件 2024-04、2026-03 来自 [scenarios.events]，客户补的历史事件来自 exchange/events.json，值班开的预报 / 进行中事件来自 open_event 动作（out/events.jsonl）。每个事件对上一个情景做模型这一侧；一场雨过后按它的积水经历与推演对账。",
               (P("event_id", "str", "编号", "id"), P("kind", "enum", "类型", "kind", "historical / forecast / live"), P("label", "str", "名称", "label"), P("label_en", "str", "名称（英）", "label (en)"), P("date", "str", "日期", "date"),
                P("start", "str", "窗口起", "window start"), P("end", "str", "窗口止", "window end"), P("p24_mm", "float", "24 h 雨量 mm（缺省片区）", "24 h depth mm"), P("p24_by_area", "dict", "分片区 24 h 雨量", "24 h depth by area"),
                P("rp_equiv_yr", "float", "相当于几年一遇", "return-period equivalent (yr)"), P("scenario_key", "str", "对应情景", "scenario"), P("alert_level", "enum", "签发的预警等级", "issued alert level", "蓝 / 黄 / 橙 / 红"),
                P("source", "str", "雨量来源", "rain source"), P("origin", "enum", "记录来源", "record origin", "config / client / log"), P("impacted_areas", "list", "公开报道点名的积水片区", "areas named in public reports", "片区级证据，不是逐点记录；用 lifecycle.AREA_KEYWORDS 对上热点的 area / location"), P("opened_at", "str", "开启时间", "opened at"), P("closed_at", "str", "关闭时间", "closed at"),
                P("n_episodes", "int", "经历数", "episodes"), P("n_observed_flooded", "int", "有记录积水的点", "points recorded flooded"), P("n_model_flooded", "int", "模型推演积水的点", "points modelled flooded"),
                P("pod", "float", "命中率 POD", "probability of detection"), P("far", "float", "误报率 FAR", "false-alarm ratio"), P("verification", "enum", "对账结论", "verification verdict", "pass / fail / insufficient（[verification] 阈值）", derived_by="event_verification")),
               "load_events"),
    ObjectType("FloodEpisode", "积水经历", "Flood episode", "episode_id", "fp_id",
               "一个积水点在一场雨里的一次经历：预警待命 → 已预置 → 已上报 → 已核实 → 已派单 → 到位 → 抽排 → 退干 → 关闭 → 复盘 的时间戳，现场观测到的水深 / 时长、实际派泵与退干，来源（客户记录 / 现场观测 / 993 / 本系统日志 / 推演估计），以及模型在该事件情景下的推演与二者的对账。主键 fp_id@event_id。",
               (P("episode_id", "str", "主键", "id"), P("fp_id", "str", "积水点", "flood point"), P("event_id", "str", "事件", "event"), P("status", "enum", "阶段", "stage", "none / estimated / alerted / staged / reported / verified / dispatched / arrived / pumping / dry / closed / reviewed", derived_by="episode_status"),
                P("stages", "dict", "各阶段时间", "stage timestamps"), P("observed_flooded", "bool", "有记录积水", "recorded flooded"), P("obs_depth_max_m", "float", "观测最大水深 m", "observed max depth"), P("obs_duration_min", "float", "观测积水时长 min", "observed duration"),
                P("units_sent", "int", "实际派泵台数", "units actually sent"), P("first_arrival_min", "float", "实际首台到位 min", "actual first arrival"), P("drain_min", "float", "实际退干 min", "actual drain time"),
                P("source", "enum", "主来源", "primary source", "client_record / observation / 993 / log / area_report / system_estimate"), P("sources", "list", "全部来源", "all sources"),
                P("area_reported", "bool", "所在片区有公开报道积水", "area named in public flood reports", "片区级，不算该点的记录，不进对账"), P("area_report_names", "list", "报道点名的片区", "reported area names"),
                P("model_flooded", "bool", "模型推演积水", "model flooded"), P("model_depth_m", "float", "模型水深 m", "model depth"), P("model_duration_min", "float", "模型无泵时长 min", "model duration"), P("model_served", "bool", "模型已派", "model served"), P("model_done_min", "float", "模型排干 min", "model drained at"),
                P("agreement", "enum", "对账", "agreement", "hit / miss / false_alarm / correct_negative / unknown", derived_by="episode_agreement"), P("notes", "list", "备注", "notes")),
               "load_episodes"),
    ObjectType("Asset", "设施", "Asset", "asset_id", "name",
               "地表水管理资产（客户图上的 SW Assets）：客户 L1/L2/L3 分级里的一件实体资产。L1 既有永久（管线、泵站、出水口、雨水口、坝、干河谷、明渠、生态格室、塘），"
               "L2 雨前临时 / 过渡（按交付方分 CAPEX / IDD / 市政：塘、地面管、溢流堰、土堤、Jersey 护栏、沙袋、固定泵、生态格室…），"
               "L3 雨中应急（罐车、移动泵、吊车）。管线已有 Reach、移动泵与罐车已有 PumpUnit，不在这里重复建模；"
               "其余二十来种资产统一收在这个类型里，种类差异放 kind，分级放 level，谁出钱谁建放 delivery——"
               "客户以后加新种类不必改本体结构。**目前只有塘有数据**，其余种类的台账客户还没给（假设总账 asset_register）。",
               (P("asset_id", "str", "编号", "id"), P("kind", "enum", "种类", "kind", "pond / pump_station / outfall / inlet / dam / wadi / ditch / geocell / surface_pipeline / spillway / bund / jersey_barrier / sandbag / small_barrier / stationary_pump / crane …（swbrain.assets.KINDS）"),
                P("kind_zh", "str", "种类（中）", "kind (zh)"), P("kind_en", "str", "种类（英）", "kind (en)"),
                P("level", "enum", "层级", "level", "L1 既有永久 / L2 雨前临时 / L3 雨中应急（客户 2026-09-25 分级图）"),
                P("delivery", "enum", "交付方", "delivery", "capex / idd / municipality / erp / existing——客户把 L2 按这三条线分开列，因为出钱、施工、运维的主体不同"),
                P("name", "str", "名称", "name"), P("name_en", "str", "名称（英）", "name (en)"),
                P("lon", "float", "经度", "lon"), P("lat", "float", "纬度", "lat"),
                P("zone_name", "str", "片区", "zone"), P("area", "str", "行政片区", "area"),
                P("status", "enum", "状态", "status", "planned 规划中 → approved 已批准 → under_construction 在建 → built 已建成 → verified 已验证有效（或 rejected 已否决）；与 Proposal 同一套状态机"),
                P("capacity_m3", "float", "库容 m³", "storage capacity"),
                P("intake_m3h", "float", "进水能力 m³/h", "intake capacity", "有效库容 = min(库容, 进水能力 × 雨峰窗口)——塘能不能起作用由这一项决定，不是库容"),
                P("footprint_m2", "float", "占地 m²", "footprint"), P("cost_aed", "float", "造价 AED", "cost"),
                P("source", "enum", "来源", "source", "client_register 客户台账 / client_model 客户 1D 模型 / client_report 顾问报告 / system_plan 本系统规划"),
                P("source_zh", "str", "来源（中）", "source (zh)"),
                P("model_ref", "str", "对应模型对象", "model object"), P("serves_fp_ids", "list", "保护的积水点", "protects"),
                P("note", "str", "备注", "note")),
               "load_assets"),

    # ========== L2 设施详细建模（2026-10-22 基于 WA-726-1/2）==========
    ObjectType("Pond", "调蓄池", "Detention Pond", "pond_id", "name",
               "雨水调蓄池或滞留池（L2-CAPEX 设施）。约束见 WA-726 第一卷 4.2（p57）、4.14（p74–78）、附录 A5 表 A5-1（p194），第二卷 p79–83。"
               "已开发区内或附近不得建开敞塘：城区只用地下箱涵 / 土工格室，或游乐 / 公园地块上的景观化临时蓄洪地（pond_type）。阈值见 constraints/wa726.json。",
               (P("pond_id", "str", "编号", "id"), P("name", "str", "名称", "name"),
                P("pond_type", "enum", "池型", "type", "detention 滞留池（有出口）/ retention 蓄积池（无出口）/ underground 地下箱涵 / geocellular 土工格室模块池"),
                P("lon", "float", "经度", "lon"), P("lat", "float", "纬度", "lat"), P("x_utm", "float", "UTM X m", "UTM X"), P("y_utm", "float", "UTM Y m", "UTM Y"),
                P("design_volume_m3", "float", "设计容积 m³", "design volume", "基于 SCS 单位线法计算的多个暴雨历时的最大所需容积"),
                P("required_volume_m3", "float", "所需调蓄容积 m³", "required volume"), P("in_developed_area", "bool", "位于已开发区内或附近", "in/near developed area"), P("drain_mode", "enum", "雨后排空方式", "drain mode", "gravity / pump"), P("access_width_m", "float", "检修通道宽 m", "access width"), P("side_slope_h_per_v", "float", "边坡 H/V", "side slope"), P("fenced", "bool", "有围栏", "fenced"), P("public_access", "bool", "公众可达", "public access"), P("has_emergency_spillway", "bool", "有应急溢洪道", "emergency spillway"), P("gw_below_base_m", "float", "地下水低于池底 m", "groundwater below base"), P("safety_margin_pct", "float", "积砂裕量 %", "sediment margin", "WA-726 第一卷 4.2 p57：塘和离线调蓄容积 +5 %（积砂）"),
                P("effective_volume_m3", "float", "有效库容 m³", "effective volume", "≥ 所需容积 × 1.05"),
                P("freeboard_m", "float", "超高 m", "freeboard", "DEC-02：正常 1.0 m，应急 0.6 m"),
                P("total_depth_m", "float", "总深度 m", "total depth"), P("design_return_period_yr", "int", "设计重现期 年", "design RP", "一般区域 5 年，重要区域 25 年"),
                P("bottom_area_m2", "float", "池底面积 m²", "bottom area"), P("top_area_m2", "float", "池顶面积 m²", "top area"), P("footprint_m2", "float", "总占地 m²", "footprint"),
                P("side_slope_ratio", "str", "边坡比（文字）", "side slope text"),
                P("slope_protection", "enum", "护坡类型", "slope protection", "gabion 石笼 / concrete_block 混凝土块 / vegetation 植草 / geotextile 土工布"),
                P("bottom_elevation_m", "float", "池底标高 m", "bottom elevation"), P("bottom_slope_pct", "float", "池底坡度 %", "bottom slope"),
                P("has_outlet", "bool", "有出口", "has outlet"), P("max_outflow_m3s", "float", "最大出流 m³/s", "max outflow"),
                P("outlet_structure", "enum", "出流结构", "outlet structure", "weir 堰 / orifice 孔口 / pipe 管道 / pump 泵"),
                P("auto_drain", "bool", "自动排空", "auto drain", "WA-726-1 要求：暴雨后需能立即自动排空"),
                P("serves_fp_ids", "list", "保护的积水点", "protects"), P("status", "enum", "状态", "status", "planned / approved / under_construction / built / verified / rejected"),
                P("total_cost_aed", "float", "总造价 AED", "total cost"),
                P("compliant", "bool", "符合规范", "compliant", derived_by="pond_compliance"), P("constraint_violations", "list", "违反的约束", "violations", derived_by="pond_compliance"),
                P("geometry", "geo", "几何", "geometry")),
               "load_ponds"),

    ObjectType("Manhole", "检查井", "Manhole", "manhole_id", "manhole_id",
               "雨水检查井。按 WA-726-2 第 17-22 页标准图设计。用于管道转角、变径、接入、维护检修。",
               (P("manhole_id", "str", "编号", "id"), P("name", "str", "名称", "name"),
                P("lon", "float", "经度", "lon"), P("lat", "float", "纬度", "lat"), P("x_utm", "float", "UTM X m", "UTM X"), P("y_utm", "float", "UTM Y m", "UTM Y"),
                P("manhole_type", "enum", "井型", "type", "circular 圆形 / rectangular 矩形（深度 > 6000mm）"),
                P("shaft_diameter_mm", "int", "井筒直径 mm", "shaft diameter", "根据管径，见 WA-726-2 Table"),
                P("depth_mm", "int", "井深 mm", "depth"), P("max_depth_mm", "int", "最大允许深度 mm", "max depth"),
                P("cover_diameter_mm", "int", "井盖直径 mm", "cover diameter", "750mm 或 900mm"),
                P("upstream_reach_ids", "list", "上游管段", "upstream reaches"), P("downstream_reach_id", "str", "下游管段", "downstream reach"),
                P("ground_elevation_m", "float", "地面标高 m", "ground elevation"), P("invert_in_m", "float", "进口底标高 m", "invert in"), P("invert_out_m", "float", "出口底标高 m", "invert out"),
                P("cost_aed", "float", "造价 AED", "cost"),
                P("compliant", "bool", "符合规范", "compliant"),
                P("geometry", "geo", "几何", "geometry")),
               "load_manholes"),

    ObjectType("GravityPipe", "重力管", "Gravity Pipe", "pipe_id", "pipe_id",
               "雨水重力管道。按 WA-726-1 Section 4.3 设计。约束见 WA-726 第一卷 4.1.4（图 4-1，p56–57）、4.3（p59–63）。",
               (P("pipe_id", "str", "编号", "id"), P("name", "str", "名称", "name"),
                P("upstream_node", "str", "上游节点", "upstream node"), P("downstream_node", "str", "下游节点", "downstream node"),
                P("length_m", "float", "长度 m", "length", "实际路径长度"), P("route_geometry", "geo", "路径几何", "route"),
                P("diameter_mm", "int", "管径 mm", "diameter"), P("material", "enum", "材质", "material", "hdpe HDPE / rcc 钢筋混凝土 / di 球墨铸铁"),
                P("slope", "float", "坡度", "slope"), P("invert_up_m", "float", "上游底标高 m", "invert upstream"), P("invert_down_m", "float", "下游底标高 m", "invert downstream"),
                P("design_flow_m3s", "float", "设计流量 m³/s", "design flow"),
                P("velocity_peak_ms", "float", "峰值流速 m/s", "peak velocity"),
                P("velocity_compliant", "bool", "流速达标", "velocity OK", "峰值 ≥ 0.75 m/s（WA-726 第一卷 p60）"),
                P("clearance_to_utilities_m", "float", "与现状管线净距 m", "clearance", "≥ 0.5 m（第一卷 p57）"), P("clearance_to_building_m", "float", "与建筑基础净宽 m", "clearance to buildings", "≥ 图 4-1 的 B"), P("cover_m", "float", "覆土 m", "cover"), P("depth_m", "float", "埋深 m", "depth"), P("clearance_compliant", "bool", "净距达标", "clearance OK"),
                P("total_cost_aed", "float", "总造价 AED", "total cost"),
                P("compliant", "bool", "符合规范", "compliant", derived_by="gravity_pipe_compliance"), P("constraint_violations", "list", "违反的约束", "violations", derived_by="gravity_pipe_compliance")),
               "load_gravity_pipes"),

    ObjectType("PumpStation", "泵站", "Pump Station", "ps_id", "name",
               "雨水泵站。约束见 WA-726 第一卷第 6 章（p97–117）、第二卷 p62–63、p99–103。须地面独立电气房和重型车辆可进出的场地。",
               (P("ps_id", "str", "编号", "id"), P("name", "str", "名称", "name"),
                P("lon", "float", "经度", "lon"), P("lat", "float", "纬度", "lat"), P("x_utm", "float", "UTM X m", "UTM X"), P("y_utm", "float", "UTM Y m", "UTM Y"),
                P("ps_type", "enum", "泵站类型", "type", "surface 地面泵站 / underground 地下泵站 / prefab 一体化预制"),
                P("wetwell_volume_m3", "float", "集水井容积 m³", "wetwell volume", "V ≥ 0.25·Q·T（第一卷 p99；T 按 DEC-09 取 600 s）"),
                P("pump_count", "int", "泵数量", "pump count", "≥ 2 台同型号，坏一台时 ≥ 66 %（DEC-04）"), P("pump_capacity_m3s", "float", "单泵流量 m³/s", "pump capacity"),
                P("pump_head_m", "float", "设计扬程 m", "design head", "= 静扬程 + 管道损失 + 出口水位 + 裕量"),
                P("pressure_pipe_length_m", "float", "压力管长度 m", "pressure pipe length"), P("pressure_pipe_diameter_mm", "int", "压力管管径 mm", "pressure pipe diameter"),
                P("head_loss_m", "float", "管道水头损失 m", "head loss"),
                P("discharge_to", "enum", "排至", "discharge to", "pond 调蓄池 / outfall 出水口 / existing_network 现有管网"),
                P("footprint_m2", "float", "占地 m²", "footprint", "地面泵站 20-200 m²"), P("power_requirement_kw", "float", "用电功率 kW", "power"),
                P("distance_to_grid_m", "float", "到电网距离 m", "distance to grid"),
                P("gravity_infeasible", "bool", "重力接不上（已论证）", "gravity infeasible"), P("design_flow_m3s", "float", "设计流量 m³/s", "design flow"), P("surface_site_m2", "float", "地面场地 m²", "surface site"), P("hydraulic_compatible", "bool", "液压匹配", "hydraulic OK", "与现有系统液压约束匹配"),
                P("total_cost_aed", "float", "总造价 AED", "total cost"),
                P("compliant", "bool", "符合规范", "compliant", derived_by="pump_station_compliance"), P("constraint_violations", "list", "违反的约束", "violations", derived_by="pump_station_compliance"),
                P("geometry", "geo", "几何", "geometry")),
               "load_pump_stations"),

    ObjectType("PressurePipe", "压力管", "Pressure Pipe", "pressure_pipe_id", "pressure_pipe_id",
               "从泵站到出水口/调蓄池的压力输水管。约束见 WA-726 第一卷第 7 章（p118–129）：峰值流速 ≤ 2.0 m/s、材质 DI/GRP/HDPE、阀室不在车行道。",
               (P("pressure_pipe_id", "str", "编号", "id"), P("name", "str", "名称", "name"),
                P("upstream_node", "str", "上游节点", "upstream", "泵站ID"), P("downstream_node", "str", "下游节点", "downstream", "出水口/调蓄池"),
                P("length_m", "float", "长度 m", "length"), P("diameter_mm", "int", "管径 mm", "diameter"),
                P("material", "enum", "材质", "material", "hdpe / di / steel"),
                P("design_flow_m3s", "float", "设计流量 m³/s", "flow"), P("design_velocity_ms", "float", "设计流速 m/s", "velocity", "峰值 ≤ 2.0、应急 ≤ 2.5 m/s"),
                P("total_head_loss_m", "float", "总水头损失 m", "head loss"), P("velocity_peak_ms", "float", "峰值流速 m/s", "peak velocity"), P("depth_m", "float", "埋深 m", "depth"), P("clearance_to_utilities_m", "float", "与现状管线净距 m", "clearance to utilities"), P("clearance_to_building_m", "float", "与建筑基础净宽 m", "clearance to buildings"),
                P("total_cost_aed", "float", "总造价 AED", "cost"), P("geometry", "geo", "几何", "geometry")),
               "load_pressure_pipes"),

    ObjectType("Inlet", "雨水口", "Storm Inlet", "inlet_id", "inlet_id",
               "地面雨水收集口。按 WA-726-2 第45-46页设计。",
               (P("inlet_id", "str", "编号", "id"), P("lon", "float", "经度", "lon"), P("lat", "float", "纬度", "lat"),
                P("inlet_type", "enum", "类型", "type", "grate篦子式 / curb路缘石式 / combination组合式"),
                P("capacity_m3s", "float", "进水能力 m³/s", "capacity"),
                P("connected_reach_id", "str", "连接管段", "reach"), P("geometry", "geo", "几何", "geometry")),
               "load_inlets"),

    ObjectType("Outfall", "出水口", "Outfall", "outfall_id", "outfall_id",
               "雨水系统排放出口。约束见 WA-726 第一卷 4.8（p67–68）、8.4（p131）。",
               (P("outfall_id", "str", "编号", "id"), P("name", "str", "名称", "name"),
                P("lon", "float", "经度", "lon"), P("lat", "float", "纬度", "lat"),
                P("outfall_type", "enum", "类型", "type", "sea排海 / wadi干河谷 / downstream_network下游管网"),
                P("tailwater_type", "enum", "尾水类型", "tailwater", "tidal潮汐 / fixed固定 / free自由出流"),
                P("tailwater_high_m", "float", "最高尾水位 m", "high tailwater"), P("is_new", "bool", "新建", "new"), P("soffit_m_nadd", "float", "管顶标高 m NADD", "soffit"), P("nrv_chamber_cover_m_nadd", "float", "止回阀井盖标高 m NADD", "NRV chamber cover"), P("flap_valve", "bool", "有拍门", "flap valve"),
                P("design_capacity_m3s", "float", "设计排放能力 m³/s", "capacity"),
                P("connected_reach_ids", "list", "连接管段", "reaches"),
                P("status", "enum", "状态", "status", "existing既有 / planned规划 / built已建"),
                P("geometry", "geo", "几何", "geometry")),
               "load_outfalls"),

    ObjectType("Valve", "阀门", "Valve", "valve_id", "valve_id",
               "管道系统阀门（闸阀、止回阀等）。",
               (P("valve_id", "str", "编号", "id"), P("valve_type", "enum", "类型", "type", "gate闸阀 / check止回阀 / butterfly蝶阀"),
                P("diameter_mm", "int", "口径 mm", "diameter"), P("on_pipe_id", "str", "所在管道", "pipe"), P("cost_aed", "float", "造价 AED", "cost"),
                P("geometry", "geo", "几何", "geometry")),
               "load_valves"),

    ObjectType("Weir", "堰", "Weir", "weir_id", "weir_id",
               "溢流堰或出流控制堰。用于调蓄池出口等处控制最大出流。",
               (P("weir_id", "str", "编号", "id"), P("weir_type", "enum", "堰型", "type", "rectangular矩形 / vnotch V型 / broad_crested宽顶"),
                P("crest_length_m", "float", "堰顶长度 m", "length"), P("crest_elevation_m", "float", "堰顶标高 m", "elevation"),
                P("design_flow_m3s", "float", "设计流量 m³/s", "flow"), P("location", "str", "位置", "location", "pond_id或manhole_id"),
                P("geometry", "geo", "几何", "geometry")),
               "load_weirs"),
    # =====================================================================

    ObjectType("Catchment", "汇水区", "Catchment", "catchment_id", "name",
               "一个排水汇水区单元（2026-09-29 加）。几套来源并存、各带可信度：**客户 22 个水力模型的子汇水区（2026-09-30 用户定为准，最可信）**、客户 QGIS 的 Catchment 图层（按截图描绘，RB-3 / MBZ / ADGC-10）、"
               "客户邮件 KMZ（169 个面）、客户 flood 库 catchment.catchment（39 个建模研究范围，已知漏 ADM-94 / 192 / 487）。"
               "积水点落在哪个汇水区、汇水区里有哪些积水点和设施、同一来源的面有没有重叠，都从这里求（swbrain.drainage_units）。",
               (P("catchment_id", "str", "编号", "id", "来源前缀 + 编号：CL:147 = 客户图层 OBJECTID 147；KMZ:12；DB:6"), P("name", "str", "名称", "name", "客户图层取 New_Number（RB-3）"),
                P("title", "str", "全称", "title", "客户图层的 Title / Model_Name（RAHA BEACH OUTFALL-3）"),
                P("source", "enum", "来源", "source", "model 客户水力模型子汇水区（最可信）/ client_layer 客户图层（按截图描绘）/ kmz 客户邮件 KMZ / db39 客户 flood 库 39 面（手描）"), P("source_zh", "str", "来源（中）", "source (zh)"),
                P("confidence", "str", "可信度", "confidence"), P("objectid", "int", "客户 OBJECTID", "client OBJECTID"), P("new_number", "str", "新编号", "new number"), P("old_number", "str", "旧编号", "old number"),
                P("hydroid", "int", "HYDROID", "HYDROID"), P("group", "str", "KMZ 分组", "KMZ group"), P("area_ha", "float", "面积 ha", "area ha"), P("shape_area_m2", "float", "客户属性面积 m²", "client Shape_Area"),
                P("lon", "float", "代表点经度", "lon"), P("lat", "float", "代表点纬度", "lat"),
                P("n_flood_points", "int", "面内积水点数", "flood points inside", derived_by="catchment_point_count"), P("fp_ids", "list", "面内积水点", "flood points inside")),
               "load_catchments"),
    ObjectType("AnalysisDomain", "分析范围", "Analysis domain", "domain_id", "name",
               "一次一二维计算用的范围（2026-09-29 加）：C1 计算域（8 个热点汇水区外扩 300 m，42.8 km²）、C1 客户单元版（RB-3 + MBZ + ADGC-10 融合，20.3 km²）。"
               "带应当包含的热点与对应的模型情景名，漏了谁由规则 domain_missing_expected 报。",
               (P("domain_id", "str", "编号", "id"), P("name", "str", "名称", "name"), P("area_km2", "float", "面积 km²", "area km²"),
                P("model_tags", "list", "对应模型情景", "model runs", "C1_R61 / C1_R96 / C1_D24 / C1_U61"), P("expected_fp_ids", "list", "应当包含的热点", "hotspots it should contain"),
                P("unit_ids", "list", "组成它的汇水区单元", "catchment units"), P("fp_ids", "list", "范围内的积水点", "flood points inside"), P("source_file", "str", "几何文件", "geometry file"),
                P("lon", "float", "代表点经度", "lon"), P("lat", "float", "代表点纬度", "lat")),
               "load_domains"),
)
OT = {o.api: o for o in OBJECT_TYPES}

# ---------------------------------------------------------------- 关系
LINK_TYPES: tuple[LinkType, ...] = (
    LinkType("states", "各情景下的状态", "states", "FloodPoint", "FloodState", "many", "FloodState.fp_id = FloodPoint.fp_id"),
    LinkType("point", "所属积水点", "flood point", "FloodState", "FloodPoint", "one", "FloodState.fp_id"),
    LinkType("scenario", "情景", "scenario", "FloodState", "Scenario", "one", "FloodState.scenario_key"),
    LinkType("drains_via", "排入管段", "drains via", "FloodPoint", "Reach", "one", "FloodPoint.edge_gid = Reach.gid"),
    LinkType("zone", "所在片区", "zone", "FloodPoint", "Zone", "one", "经 drains_via 的 Reach.zone_name"),
    LinkType("reach_zone", "所在片区", "zone", "Reach", "Zone", "one", "Reach.zone_name"),
    LinkType("assignments", "派单", "assignments", "FloodState", "Assignment", "many", "Assignment.fp_id + scenario_key"),
    LinkType("unit", "泵", "unit", "Assignment", "PumpUnit", "one", "Assignment.unit_id"),
    LinkType("depot", "出发驻地", "depot", "Assignment", "Depot", "one", "Assignment.depot_id"),
    LinkType("proposals", "相关工程建议", "proposals", "FloodPoint", "Proposal", "many", "Proposal.client_hotspot_id = fp_id，或 Proposal.gid = 匹配管段"),
    LinkType("zone_proposals", "片区建议", "proposals", "Zone", "Proposal", "many", "Proposal.zone_name"),
    LinkType("gwm_core", "对上的推演核", "world-model core", "FloodState", "GwmCore", "one", "gwmcompare.hotspot_hits"),
    LinkType("observations", "现场观测", "observations", "FloodPoint", "Observation", "many", "Observation.fp_id"),
    LinkType("alerts", "预警", "alerts", "Scenario", "Alert", "many", "Alert.scenario_key"),
    LinkType("staging_sites", "覆盖它的预置驻地（红级）", "staging sites", "FloodState", "Depot", "many", "preposition.sites[].covers 含 fp_id"),
    LinkType("episodes", "历次积水经历", "episodes", "FloodPoint", "FloodEpisode", "many", "FloodEpisode.fp_id"),
    LinkType("ep_point", "所属积水点", "flood point", "FloodEpisode", "FloodPoint", "one", "FloodEpisode.fp_id"),
    LinkType("ep_event", "所在事件", "rain event", "FloodEpisode", "RainEvent", "one", "FloodEpisode.event_id"),
    LinkType("ep_state", "模型在该事件情景下的状态", "model state", "FloodEpisode", "FloodState", "one", "FloodState(fp_id @ RainEvent.scenario_key)"),
    LinkType("event_episodes", "这场雨里的经历", "episodes", "RainEvent", "FloodEpisode", "many", "FloodEpisode.event_id"),
    LinkType("event_scenario", "对应情景", "scenario", "RainEvent", "Scenario", "one", "RainEvent.scenario_key"),
    LinkType("scenario_event", "对应的真实事件", "rain event", "Scenario", "RainEvent", "one", "RainEvent.scenario_key = Scenario.key"),
    LinkType("in_zone", "所在片区", "zone", "Asset", "Zone", "one", "Asset.zone_name"),
    LinkType("protects", "保护的积水点", "protects", "Asset", "FloodPoint", "many", "Asset.serves_fp_ids"),
    LinkType("discharges_to", "排放去处", "discharges to", "FloodState", "Asset", "one",
             "FloodState.discharge_to = Asset.asset_id（出水口 / 渗井 / 泵站的 terminal_unitid）。有了它才回答得了「这个点的水排到哪、那个终点负荷多少」——以前只能在代码里算"),
    LinkType("protected_by", "保护它的排水资产", "protected by", "FloodPoint", "Asset", "many",
             "反向：Asset.serves_fp_ids 含该点。别和 FloodState.facilities 混——那是附近的**受保护设施**（医院、学校，out/state/facilities.parquet），意思正相反"),
    LinkType("catchments", "所在汇水区", "catchments", "FloodPoint", "Catchment", "many",
             "积水点坐标落在哪些汇水区单元里（几套来源各给一个，按可信度排序：客户图层 > KMZ > flood 库 39 面）"),
    LinkType("catchment_points", "面内积水点", "flood points", "Catchment", "FloodPoint", "many", "积水点坐标落在面内"),
    LinkType("catchment_assets", "面内设施", "assets", "Catchment", "Asset", "many", "Asset 坐标落在面内"),
    LinkType("domain_units", "组成它的汇水区", "catchment units", "AnalysisDomain", "Catchment", "many", "AnalysisDomain.unit_ids"),
    LinkType("domain_points", "范围内的积水点", "flood points", "AnalysisDomain", "FloodPoint", "many", "积水点坐标落在范围内"),
)
LT = {l.api: l for l in LINK_TYPES}


# ---------------------------------------------------------------- 规则（可执行）
def _f(x, d=0.0):
    try:
        v = float(x)
        return d if not np.isfinite(v) else v
    except (TypeError, ValueError):
        return d


def r_emergency_level(o: dict, ctx: dict):
    cc, tk, d, lu = o.get("criticality") or o.get("client_criticality"), o.get("top_kind"), _f(o.get("max_depth_m")), o.get("land_use")
    if cc == "Very High" or (tk in ("hospital", "tunnel", "metro") and d >= 0.3) or (lu == "tunnel" and d >= 0.3):
        return "III"
    if cc == "High" or (isinstance(tk, str) and tk not in ("", "none")) or d >= 0.3 or lu in ("tunnel", "access_road"):
        return "II"
    return "I"


def r_target_drain(o: dict, ctx: dict):
    T = ((ctx.get("cfg") or {}).get("dispatch") or {}).get("target_drain_min") or {"hospital": 120, "tunnel": 180, "metro": 180, "school": 240, "ambulance": 180, "default": 360}
    crit = {"Very High": 120, "High": 240, "Medium": 600, "Low": 600}
    t_fac = min([float(T.get(k, T.get("default", 360))) for k in (o.get("facilities") or []) if k in T] or [float(T.get("default", 360))])
    cc = o.get("criticality") or o.get("client_criticality")
    return float(min(t_fac, crit.get(cc, 600)))


def r_dispatchable(o: dict, ctx: dict):
    return bool(o.get("discharge_to")) and _f(o.get("volume_now_m3")) > 0 and str(o.get("triage") or "").startswith(("可派泵", "dispatchable"))


def r_relay(o: dict, ctx: dict):
    D = ((ctx.get("cfg") or {}).get("dispatch") or {})
    beyond, hose, f, fmin = float(D.get("relay_beyond_m", 300)), float(D.get("relay_hose_m", 100)), float(D.get("relay_rate_factor", 0.85)), float(D.get("relay_min_factor", 0.4))
    d = _f(o.get("discharge_dist_m"), float("nan"))
    if not np.isfinite(d) or d <= beyond:
        return {"relay_needed": False, "stages": 0, "factor": 1.0}
    st = int(np.ceil((d - beyond) / hose))
    return {"relay_needed": True, "stages": st, "factor": round(max(f ** st, fmin), 3)}


def r_within_target(o: dict, ctx: dict):
    if o.get("done_min") is None or o.get("target_drain_min") is None:
        return None
    return bool(_f(o["done_min"]) <= _f(o["target_drain_min"]) + 60)


def r_world_model(o: dict, ctx: dict):
    return bool(o.get("gwm_core")) and _f(o.get("gwm_dist_m"), 9e9) <= 300.0


def _thr(x, default):
    """[alerts.*] 既可以是 {黄:…, 橙:…, 红:…} 也可以是升序列表。"""
    if isinstance(x, dict):
        vals = [x.get(k) for k in ("黄", "橙", "红")]
        return [float(v) for v in vals if v is not None] or default
    if isinstance(x, (list, tuple)) and x:
        return [float(v) for v in x]
    return default


def r_alert_level(o: dict, ctx: dict):
    A = ((ctx.get("cfg") or {}).get("alerts") or {})
    rain, iii, spill = _thr(A.get("rain_mm"), [45, 70, 90]), _thr(A.get("iii_points") or A.get("level3_points"), [75, 100, 150]), _thr(A.get("spill_m3"), [1.5e6, 3e6, 5e6])
    def grade(v, th):
        return sum(1 for t in th if v >= float(t))
    p = _f(o.get("p24_mm")); n3 = _f((o.get("points_by_level") or {}).get("III")); sp = _f(o.get("spill_total_m3"))
    g = max(grade(p, rain), grade(n3, iii), grade(sp, spill))
    return ["蓝", "黄", "橙", "红"][g]


def r_reach_compliance(o: dict, ctx: dict):
    r = o.get("ratio_design")
    if r is None or not np.isfinite(_f(r, float("nan"))):
        return None
    return bool(_f(r) <= 1.0)


def r_coordinate_suspect(o: dict, ctx: dict):
    lo, la, lx, ly = o.get("lon"), o.get("lat"), o.get("lon_xlsx"), o.get("lat_xlsx")
    if any(v is None or not np.isfinite(_f(v, float("nan"))) for v in (lo, la, lx, ly)):
        return None
    d = float(np.hypot((_f(lo) - _f(lx)) * 111320 * np.cos(np.radians(_f(la))), (_f(la) - _f(ly)) * 110540))
    return {"shift_m": round(d)} if d > 200 else None


def r_no_network_but_terminal(o: dict, ctx: dict):
    if o.get("network_available") is False and o.get("edge_gid") is not None:
        return {"edge_gid": o.get("edge_gid")}
    return None


def r_needs_escalation(o: dict, ctx: dict):
    if o.get("level") != "III":
        return None
    if o.get("served") and o.get("within_target"):
        return None
    if not o.get("served"):
        why = "III 级未派（" + str(o.get("triage") or "") + "）"; why_en = "Level III not served (" + str(o.get("triage") or "") + ")"
        sug = ["preposition_plan", "upsize_whatif"] if not str(o.get("triage") or "").startswith("无需") else []
    else:
        why = "III 级已派但排干超过目标时限"; why_en = "Level III served but drained after target"
        sug = ["upsize_whatif", "replay_point"]
    return {"why": why, "why_en": why_en, "suggest": sug} if sug or not o.get("served") else None


def r_observation_conflict(o: dict, ctx: dict):
    obs = o.get("_latest_observation"); st = o.get("_state") or {}
    if not obs:
        return None
    if obs.get("status") == "dry" and st.get("level") == "III" and _f(st.get("volume_m3")) > 0:
        return {"why": f"现场 {obs.get('observed_at')} 报无积水，但推演为 III 级 {st.get('volume_m3')} m³", "why_en": f"Field report {obs.get('observed_at')} says dry, model says level III {st.get('volume_m3')} m³"}
    if obs.get("status") == "flooding" and _f(obs.get("depth_m")) >= 0.3 and _f(st.get("max_depth_m")) < 0.1:
        return {"why": f"现场水深 {obs.get('depth_m')} m，推演不到 0.1 m", "why_en": f"Field depth {obs.get('depth_m')} m, model under 0.1 m"}
    return None


def r_tanker_viable(o: dict, ctx: dict):
    T = ((ctx.get("cfg") or {}).get("dispatch") or {}).get("tankers") or {}
    if not o.get("tanker_served"):
        return None
    return {"max_haul_km": T.get("max_haul_km", 15), "horizon_h": float(T.get("horizon_min", 2880)) / 60}


def r_episode_status(o: dict, ctx: dict):
    return LC.derive_status(o, (o.get("_event") or {}))


def r_episode_agreement(o: dict, ctx: dict):
    return LC.derive_agreement(o)


def r_recurrence(o: dict, ctx: dict):
    return LC.point_summary(o.get("_episodes") or [], o.get("_events") or {}).get("recurrence")


def r_lifecycle_status(o: dict, ctx: dict):
    return LC.point_summary(o.get("_episodes") or [], o.get("_events") or {}).get("lifecycle_status")


def r_treatment_status(o: dict, ctx: dict):
    return LC.treatment_status(o.get("_proposals") or [])


def r_event_verification(o: dict, ctx: dict):
    return LC.verification(o.get("_episodes") or [], ctx.get("cfg") or {}).get("verdict")


def r_history_vs_model(o: dict, ctx: dict):
    bad = [e for e in (o.get("_episodes") or []) if e.get("agreement") in ("miss", "false_alarm")]
    if not bad:
        return None
    zh = "；".join(f"{e['event_id']} {LC.AGREEMENT_ZH.get(e['agreement'])}（记录 {'积水' if e.get('observed_flooded') else '无积水'}，模型 {'积水' if e.get('model_flooded') else '不积水'}）" for e in bad)
    en = "; ".join(f"{e['event_id']} {e['agreement']} (record {'flooded' if e.get('observed_flooded') else 'dry'}, model {'flooded' if e.get('model_flooded') else 'dry'})" for e in bad)
    return {"why": zh, "why_en": en, "events": [e["event_id"] for e in bad]}


def r_repeat_offender(o: dict, ctx: dict):
    hist = [e for e in (o.get("_episodes") or []) if ((o.get("_events") or {}).get(e.get("event_id")) or {}).get("kind", "historical") == "historical" and (e.get("observed_flooded") is not None or e.get("model_flooded") is not None)]
    n_ev = len(hist)                                                    # 只数有证据（记录或模型）的历史事件；没映射情景又没记录的事件不算
    if n_ev < 2:
        return None
    flooded = [e for e in hist if (e.get("observed_flooded") is True) or (e.get("observed_flooded") is None and e.get("model_flooded"))]
    if len(flooded) < n_ev:
        return None
    ts = LC.treatment_status(o.get("_proposals") or [])
    if ts in ("approved", "under_construction", "built", "verified") or str(o.get("idd_planned_flag") or "").lower() == "yes":
        return None
    basis = "有记录" if all(e.get("observed_flooded") is True for e in flooded) else "含推演估计"
    return {"why": f"{n_ev} 场历史降雨每场都积水（{basis}），没有已批准的工程建议、也不在 IDD 计划里", "why_en": f"Flooded in all {n_ev} historical events ({'recorded' if basis == '有记录' else 'incl. model estimates'}), with no approved proposal and no IDD plan",
            "suggest": ["upsize_whatif", "proposal_whatif"], "events": [e["event_id"] for e in flooded]}


def r_ready_to_close(o: dict, ctx: dict):
    st = o.get("status")
    if st in ("closed", "reviewed", "none", "estimated"):
        return None
    if o.get("reflooded"):
        return None                       # 退干之后又涨回来了：这一场还没完，不能关（2026-09-23 修）
    obs = o.get("_latest_observation") or {}
    if "dry" in (o.get("stages") or {}) or obs.get("status") == "dry":
        return {"why": f"已退干（{(o.get('stages') or {}).get('dry') or obs.get('observed_at')}），可关闭", "why_en": f"Drained at {(o.get('stages') or {}).get('dry') or obs.get('observed_at')}; ready to close", "suggest": ["close_point"]}
    return None


def r_treatment_effective(o: dict, ctx: dict):
    ts = LC.treatment_status(o.get("_proposals") or [])
    if ts not in ("built", "verified"):
        return None
    built_at = max((str(p.get("status_at") or "") for p in (o.get("_proposals") or []) if p.get("status") in ("已建成", "已验证有效")), default="")
    after = [e for e in (o.get("_episodes") or []) if str(((o.get("_events") or {}).get(e.get("event_id")) or {}).get("start") or "") > built_at[:10] and e.get("observed_flooded") is not None]
    if not after:
        return {"why": "工程已建成，等下一场雨的记录验证", "why_en": "Works built; awaiting the next rain's record to verify", "suggest": []}
    if all(e.get("observed_flooded") is False for e in after):
        return {"why": f"建成后 {len(after)} 场雨都没积水，可考虑降级 / 销号", "why_en": f"Dry in all {len(after)} rains since completion; consider downgrading / retiring the point", "suggest": ["point_history"]}
    return {"why": f"建成后仍有 {sum(1 for e in after if e.get('observed_flooded'))} 场积水，治理未见效", "why_en": f"Still flooded in {sum(1 for e in after if e.get('observed_flooded'))} rain(s) since completion; treatment not effective", "suggest": ["point_history", "upsize_whatif"]}


# ============================================================================
# L2 设施校验规则（2026-10-23 重写）：阈值一律从 WA-726 约束登记表（constraints/wa726.json）和取值决定（constraints/decisions.json）读，
# 代码里不写数值。2026-10-22 那版的 4 条（安全裕量 5 %「出自 4.2.1」、干舷 0.3 m、边坡 1:3「推断」、集水井 3 分钟「推断」）出处没核实，已替换。
# ============================================================================

def _facility_rule(facility: str):
    def fn(o: dict, ctx: dict):
        from . import constraints as K
        r = K.check(facility, o)
        if r["compliant"] and not r["pending"]:
            return None
        return {"compliant": r["compliant"], "constraint_violations": r["fails"], "pending": r["pending"], "items": r["items"],
                "why": ("不合规：" + "；".join(r["fails"])) if r["fails"] else ("待核：" + "；".join(r["pending"][:6])),
                "why_en": (f"{len(r['fails'])} WA-726 check(s) failed" if r["fails"] else f"{len(r['pending'])} WA-726 check(s) need data or review")}
    fn.__name__ = f"check_{facility.lower()}_wa726"
    return fn


def r_catchment_uncovered(o, ctx):
    """积水点不在任何客户汇水区单元里（三套来源都没有）。"""
    if o.get("lon") is None or o.get("lat") is None or o.get("_catchments") is None:
        return None
    if o.get("region") in ("AAM", "DRM"):            # 阿莱茵 / 宰夫拉客户没给过任何汇水区数据：整片缺，不逐点报（假设总账里记一条）
        return None
    if o["_catchments"]:
        return None
    return {"why": "不在任何客户汇水区单元里（客户图层 / KMZ / flood 库 39 面都没有）：汇水区数据缺口", "why_en": "Not inside any client catchment unit (client layer / KMZ / flood DB): catchment data gap",
            "suggest": ["catchment_info"]}


def r_client_db_missing(o, ctx):
    """客户模型（最权威；没有模型时退到客户图层）里有、flood 库 39 面里没有：客户库漏登（ADM-94 / 192 / 487 就是这样被发现的）。"""
    cs = o.get("_catchments")
    if not cs:
        return None
    cl = [c for c in cs if c.get("source") == "model"] or [c for c in cs if c.get("source") == "client_layer"]
    db = [c for c in cs if c.get("source") == "db39"]
    if not cl or db:
        return None
    names = "、".join(f"{c.get('name')}（{c.get('title') or ''}）" for c in cl)
    src = "客户水力模型" if cl[0].get("source") == "model" else "客户 Catchment 图层"
    return {"why": f"在{src}的 {names} 里，但不在客户 flood 库 catchment.catchment 的任何一个面里：库里漏登",
            "why_en": f"Inside client catchment {names} but in none of the flood DB catchment.catchment polygons: missing from the DB", "suggest": ["catchment_info"]}


def r_catchment_point_count(o, ctx):
    return len(o.get("fp_ids") or [])


def r_catchment_overlap(o, ctx):
    ov = o.get("_overlaps")
    if not ov:
        return None
    return {"why": "同一来源里和 " + "、".join(f"{x['name']}（{x['overlap_ha']} ha）" for x in ov[:5]) + " 重叠", "why_en": "Overlaps " + ", ".join(f"{x['name']} ({x['overlap_ha']} ha)" for x in ov[:5]) + " in the same source",
            "overlaps": ov}


def r_domain_missing_expected(o, ctx):
    miss = o.get("_missing")
    if not miss:
        return None
    return {"why": "应当包含的热点不在范围里：" + "、".join(f"{m['fp_id']}（离边界 {m['dist_m']} m）" for m in miss), "why_en": "Expected hotspots outside the domain: " + ", ".join(f"{m['fp_id']} ({m['dist_m']} m out)" for m in miss),
            "missing": miss}


RULES: tuple[Rule, ...] = (
    Rule("emergency_level", "应急等级", "Emergency level", "FloodState", "derive",
         "III 级：客户重要度 Very High，或 300 m 内有医院/隧道/地铁且水深 ≥ 0.3 m，或隧道用地且 ≥ 0.3 m；II 级：High，或 300 m 内有重点设施，或 ≥ 0.3 m，或隧道/地块出口；其余 I 级。单靠水深最多到 II 级。",
         "III: client Very High, or hospital/tunnel/metro within 300 m with depth ≥ 0.3 m, or tunnel land use ≥ 0.3 m; II: High, or any key facility within 300 m, or ≥ 0.3 m, or tunnel/access road; else I. Depth alone caps at II.",
         "dispatch.emergency_level · WA-726-3 表 11-1 / 附录 H-1", r_emergency_level),
    Rule("target_drain_time", "目标排干时限", "Target drain time", "FloodState", "derive",
         "取 300 m 内设施的时限（医院 120 / 隧道 180 / 地铁 180 / 学校 240 / 其余 360 min）与客户重要度时限（Very High 120 / High 240 / 其余 600）的较小者。",
         "Minimum of facility targets (hospital 120 / tunnel 180 / metro 180 / school 240 / else 360 min) and client-criticality targets (Very High 120 / High 240 / else 600).",
         "[dispatch.target_drain_min] · floodpoints targets_min", r_target_drain),
    Rule("dispatchable", "可派泵", "Dispatchable", "FloodState", "derive",
         "决策时刻还有水、1.5 km 内有可接纳的排放去处、且入流不超过可派排量；否则分诊为等待雨峰 / 无排放去处 / 已基本退水。",
         "Water remains at decision time, an acceptable discharge point within 1.5 km, and inflow does not exceed deployable rate; otherwise triaged as wait-for-peak / no discharge / already receded.",
         "dispatch.feasibility", r_dispatchable),
    Rule("relay_factor", "接力排放折减", "Relay discharge derating", "FloodState", "derive",
         "排放距离超过 300 m 的部分每 100 m 加一级接力，每级泵排量 × 0.85，不低于 0.4，每级多一次架设时间。",
         "Each 100 m of discharge distance beyond 300 m adds a relay stage; rate × 0.85 per stage, floor 0.4, one extra setup per stage.",
         "dispatch.relay_factor · [dispatch] relay_*", r_relay),
    Rule("within_target", "是否达标", "Within target", "FloodState", "derive",
         "排干时刻 ≤ 目标时限 + 60 min 宽限。", "Drained at ≤ target + 60 min grace.", "dispatch.plan_from_assignment", r_within_target),
    Rule("world_model_confirmed", "世界模型印证", "World-model confirmed", "FloodState", "derive",
         "该情景对应的 GWM 推演里，有积水核在 300 m 内（点在核内算 0 m）。", "A GWM ponding core of the matching run lies within 300 m (inside = 0 m).", "gwmcompare.compare", r_world_model),
    Rule("alert_level", "预警等级", "Alert level", "Scenario", "derive",
         "取三条判据的最高档：24 h 雨量 ≥ 45/70/90 mm；III 级点数 ≥ 75/100/150；全网路由溢流 ≥ 150/300/500 万 m³ → 黄/橙/红，否则蓝。",
         "Highest of three: 24 h rain ≥ 45/70/90 mm; level-III points ≥ 75/100/150; routed spill ≥ 1.5/3/5 M m³ → yellow/orange/red, else blue.",
         "alerts.level_from_rules · [alerts]", r_alert_level),
    Rule("reach_compliance", "法定标准达标", "Statutory compliance", "Reach", "derive",
         "按设计类别的法定重现期（下穿 50 / 主干 25 / 一般 10 年…）算设计流量，满管能力 ≥ 设计流量即达标。",
         "Design flow at the statutory return period of the reach class (underpass 50 / arterial 25 / local 10 yr…); compliant when full-pipe capacity ≥ design flow.",
         "standards · WA-726-1 表 3-2", r_reach_compliance),
    Rule("coordinate_suspect", "坐标可疑（表格错行）", "Suspect coordinates", "FloodPoint", "validate",
         "客户表格坐标与 KMZ 坐标相差 > 200 m（v4.4 表格 ADM 页坐标整体错一行）。", "Spreadsheet vs KMZ coordinates differ by > 200 m (v4.4 ADM sheet shifted one row).", "hotspots.apply_kmz_coords", r_coordinate_suspect),
    Rule("no_network_but_terminal", "客户标无管网但对上了管段", "No-network flag but reach matched", "FloodPoint", "validate",
         "客户标注「无管网」，本系统却在匹配半径内对上了管段：需现场核对哪边对。", "Client says no network but a reach matched within radius: verify on site.", "hotspots.stats adm_vs_network", r_no_network_but_terminal),
    Rule("needs_escalation", "III 级需升级处置", "Level III needs escalation", "FloodState", "trigger",
         "III 级点未派泵、或派了但排干超时 → 建议预置驻地 / 换管 what-if / 重演。", "Level III not served, or served but late → suggest staging, upsize what-if, replay.", "本体规则层", r_needs_escalation),
    Rule("observation_conflict", "观测与推演冲突", "Observation conflicts with model", "FloodPoint", "trigger",
         "最近一次现场观测为无积水而推演 III 级有水，或现场 ≥ 0.3 m 而推演 < 0.1 m → 标待核。", "Latest field report dry while model has level III water, or field ≥ 0.3 m while model < 0.1 m → flag for review.", "本体规则层 · out/observations.jsonl", r_observation_conflict),
    Rule("tanker_viable", "罐车可行", "Tanker viable", "FloodState", "derive",
         "罐车只派给 15 km 内、且到达 + 抽干在 48 h 内的点；否则不派。", "Tankers only to points within 15 km haul whose arrive + drain fits 48 h.", "dispatch.assign_tankers · [dispatch.tankers]", r_tanker_viable),
    Rule("episode_status", "经历阶段", "Episode stage", "FloodEpisode", "derive",
         "取已到达的最后一个阶段（alerted → staged → reported → verified → dispatched → arrived → pumping → dry → closed → reviewed）；没有任何阶段时：有记录不积水 = none，有记录积水 = verified，只有模型推演 = estimated（预报 / 进行中事件为 alerted）。",
         "Latest stage reached in the chain; with no stage: recorded dry = none, recorded flooded = verified, model only = estimated (alerted for forecast / live events).", "lifecycle.derive_status", r_episode_status),
    Rule("episode_agreement", "记录 vs 模型", "Record vs model", "FloodEpisode", "derive",
         "记录积水且模型积水 = 命中；记录积水模型不积水 = 漏报；记录无积水模型积水 = 误报；都无 = 都说没积水；缺任一方 = 无观测。", "Both flooded = hit; recorded but not modelled = miss; modelled but not recorded = false alarm; neither = correct negative; either side missing = unknown.", "lifecycle.derive_agreement", r_episode_agreement),
    Rule("recurrence", "复发率", "Recurrence", "FloodPoint", "derive",
         "有观测记录的历史事件里积水了几次（k/n）；模型口径的复发率另给。", "Flooded in k of n historical events with a record; model-based recurrence given separately.", "lifecycle.point_summary", r_recurrence),
    Rule("lifecycle_status", "当前生命周期状态", "Lifecycle status", "FloodPoint", "derive",
         "有未关闭的预报 / 进行中事件时 = 该点在这场雨里的阶段，否则 = 平时。", "Stage in the latest open forecast / live event, else idle.", "lifecycle.point_summary", r_lifecycle_status),
    Rule("treatment_status", "工程治理状态", "Treatment status", "FloodPoint", "derive",
         "相关工程建议里最靠后的状态：建议 → 已批准 → 在建 → 已建成 → 已验证有效；没有建议 = none。", "Furthest status among related proposals: proposed → approved → under construction → built → verified; none if no proposal.", "lifecycle.treatment_status", r_treatment_status),
    Rule("event_verification", "雨后对账", "Post-event verification", "RainEvent", "derive",
         "有记录的经历里：POD = 命中 /(命中+漏报)，FAR = 误报 /(命中+误报)，水深 RMSE；POD ≥ 0.8、FAR ≤ 0.3、RMSE ≤ 0.1 m 为 pass，记录数不足 10 为 insufficient。", "Over recorded episodes: POD = hits/(hits+misses), FAR = false alarms/(hits+false alarms), depth RMSE; pass at POD ≥ 0.8, FAR ≤ 0.3, RMSE ≤ 0.1 m; insufficient below 10 records.", "lifecycle.verification · [verification]", r_event_verification),
    Rule("history_vs_model", "历史记录与推演不符", "History disagrees with model", "FloodPoint", "validate",
         "某场历史雨里记录积水而模型说不积水（漏报），或记录无积水而模型说积水（误报）→ 该点的水量平衡 / 管段匹配待核。", "Recorded flooded but modelled dry (miss), or recorded dry but modelled flooded (false alarm) in a historical rain → check the point's water balance / reach match.", "本体规则层 · lifecycle", r_history_vs_model),
    Rule("repeat_offender", "屡次积水未治理", "Repeat flooding, untreated", "FloodPoint", "trigger",
         "≥ 2 场有证据（记录或模型）的历史雨每场都积水，且没有已批准的工程建议、也不在 IDD 计划里 → 建议进 IDD（换管 what-if / 建议评估）。", "Flooded in every historical rain with evidence (record or model, ≥ 2) with no approved proposal and no IDD plan → propose for IDD (upsize what-if / proposal evaluation).", "本体规则层 · lifecycle", r_repeat_offender),
    Rule("ready_to_close", "可关闭", "Ready to close", "FloodEpisode", "trigger",
         "处置中的经历已有退干时间戳、或最近一次现场观测为 dry → 建议 close_point。", "An active episode with a drained timestamp or a latest dry observation → suggest close_point.", "本体规则层 · lifecycle", r_ready_to_close),
    Rule("treatment_effective", "治理是否见效", "Treatment effectiveness", "FloodPoint", "trigger",
         "工程已建成后：其后的历史雨都没积水 → 可考虑降级 / 销号；仍积水 → 治理未见效；还没经过雨 → 待验证。", "After works are built: dry in all later rains → consider downgrading / retiring; still flooded → not effective; no rain yet → pending.", "本体规则层 · lifecycle", r_treatment_effective),
    Rule("catchment_uncovered", "不在任何汇水区里", "Outside every catchment", "FloodPoint", "validate",
         "阿布扎比市政（ADM）的积水点坐标不在任何客户汇水区单元里（客户图层、KMZ、flood 库 39 面都没有）→ 汇水区数据缺口，要补。阿莱茵（AAM）、宰夫拉（DRM）客户没给过汇水区，整片缺，不逐点报。",
         "The point lies in no client catchment unit (client layer, KMZ, flood DB) → catchment data gap to fill.", "本体规则层 · swbrain.drainage_units", r_catchment_uncovered),
    Rule("client_db_missing", "客户库漏登", "Missing from the client DB", "FloodPoint", "validate",
         "积水点在客户水力模型的子汇水区里（没有模型时看客户 Catchment 图层），却不在客户 flood 库 catchment.catchment 的任何面里 → 库里漏登（写库要人批准）。2026-09-29 用户发现 ADM-94 / 192 / 487 漏登后加；2026-09-30 改为以模型为准。",
         "The point is inside a client Catchment-layer unit but in no flood-DB catchment.catchment polygon → missing from the DB; add it from the layer (writing the DB needs human approval).",
         "本体规则层 · swbrain.drainage_units", r_client_db_missing),
    Rule("catchment_point_count", "面内积水点数", "Flood points inside", "Catchment", "derive",
         "积水点坐标落在面内的个数。", "Number of flood points whose coordinates fall inside.", "swbrain.drainage_units", r_catchment_point_count),
    Rule("catchment_overlap", "汇水区重叠", "Catchment overlap", "Catchment", "validate",
         "同一来源里和别的单元重叠超过自身面积 1 % → 边界待核（汇水区按定义不应重叠）。", "Overlaps another unit of the same source by > 1 % of its area → check boundaries (catchments should not overlap).",
         "swbrain.drainage_units.overlaps_same_source", r_catchment_overlap),
    Rule("domain_missing_expected", "分析范围漏热点", "Domain misses expected hotspots", "AnalysisDomain", "validate",
         "分析范围应当包含的热点有不在范围里的 → 列出并给离边界的距离（C1 客户单元版的 ADM-21 在外约 65 m，用户 2026-09-29 定为不包进来）。",
         "Hotspots the domain should contain but does not → listed with distance to the boundary.", "swbrain.drainage_units.DOMAINS", r_domain_missing_expected),

    # L2 设施校验规则（2026-10-23）：逐条读 WA-726 约束登记表；结果分 pass / fail / unknown（缺数据）/ review（要人工或审批）
    Rule("pond_compliance", "调蓄设施规范校验", "Storage compliance (WA-726)", "Pond", "validate",
         "建成区不建开敞塘（4.2 p57）、容积 +5 % 积砂（p57）、雨后自动排空（p57）；地面塘另查检修道 ≥5 m（4.14 p75）、边坡、超高、应急溢洪道、公众安全设施；地下池查地下水。",
         "No open lagoons in/near developed areas, +5 % sediment volume, automatic emptying; surface ponds also access width, side slope, freeboard, spillway, public safety; tanks groundwater.",
         "swbrain/ontology/constraints（wa726.json + decisions.json），docs/WA726_设施约束登记表_20261023.md", _facility_rule("Pond")),
    Rule("pump_station_compliance", "泵站规范校验", "Pump station compliance (WA-726)", "PumpStation", "validate",
         "重力优先（2.1 p29）、≥2 台同型号泵且坏一台 ≥66 %、能力 ≥ 设计流量 ×1.1（6.3 p100–101）、湿井 V ≥ 0.25QT（p99）、地面电气房与重型车辆场地（p108；第二卷 p99）、场地平面报批。",
         "Gravity first, ≥2 identical pumps with ≥66 % one-out, capacity ≥1.1 × design, wet well V ≥ 0.25QT, surface electrical building and heavy-vehicle site, site-plan approval.",
         "swbrain/ontology/constraints（wa726.json + decisions.json），docs/WA726_设施约束登记表_20261023.md", _facility_rule("PumpStation")),
    Rule("pressure_pipe_compliance", "压力管规范校验", "Pumping main compliance (WA-726)", "PressurePipe", "validate",
         "峰值流速 ≤2.0、应急 ≤2.5 m/s（7.3.1 p119）、材质 DI/GRP/HDPE、与管线净距 ≥500 mm（p57）、与建筑净宽 ≥ 图 4-1 的 B、阀室不在车行道（p128）、管位预留报批。",
         "Peak ≤2.0 / emergency ≤2.5 m/s, DI/GRP/HDPE, ≥500 mm to utilities, ≥ Figure 4-1 B to buildings, valve chambers off carriageway, service reservation approval.",
         "swbrain/ontology/constraints（wa726.json + decisions.json），docs/WA726_设施约束登记表_20261023.md", _facility_rule("PressurePipe")),
    Rule("gravity_pipe_compliance", "重力管规范校验", "Gravity pipe compliance (WA-726)", "GravityPipe", "validate",
         "最小覆土 1.2 m（p63）、峰值流速 ≥0.75 m/s（p60）、与管线净距 ≥500 mm、与建筑净宽 ≥ 图 4-1 的 B。",
         "Min cover 1.2 m, peak velocity ≥0.75 m/s, ≥500 mm to utilities, ≥ Figure 4-1 B to buildings.",
         "swbrain/ontology/constraints（wa726.json + decisions.json），docs/WA726_设施约束登记表_20261023.md", _facility_rule("GravityPipe")),
    Rule("outfall_compliance", "排海口规范校验", "Sea outfall compliance (WA-726)", "Outfall", "validate",
         "管顶低于 MLLW −0.7 m（4.8.2 p68）、止回阀井盖高于 +1.3 m（p68）、拍门（p125）；新建的须取得 EAD 位置专项批准与 TCA-AD 考古报告（p131；第三卷 p127）。",
         "Soffit below MLLW −0.7 m, NRV chamber cover above +1.3 m, flap valve; new outfalls need EAD location approval and TCA-AD archaeology report.",
         "swbrain/ontology/constraints（wa726.json + decisions.json），docs/WA726_设施约束登记表_20261023.md", _facility_rule("Outfall")),
)
RL = {r.api: r for r in RULES}

# ---------------------------------------------------------------- 动作
Pm = Parameter
SCEN = Pm("scenario", "scenario", "情景（5/10/25/50/100 年、10cc、2024-04、2026-03；缺省当前）", "scenario key (default: current)")
ACTIONS: tuple[ActionType, ...] = (
    ActionType("inspect_point", "查看积水点", "Inspect flood point", "一个积水点在某情景下的全貌：静态属性、状态、规则判定、管段/片区/派单/推演核/建议/观测。", "Everything about one flood point in a scenario: properties, state, rule verdicts, reach/zone/assignments/world-model core/proposals/observations.",
               (Pm("fp_id", "fp_id", "积水点编号，如 ADM-62", "flood point id", required=True), SCEN), side_effect="none",
               intents=("查看", "看看", "情况", "怎么样", "详情", "inspect", "show", "what about", "status of", "tell me about"), examples=("ADM-62 在 50 年一遇下是什么情况", "Show ADM-3 for the 100-year storm"), ui="打开单热点详情、切到该情景"),
    ActionType("list_points", "筛选积水点", "List flood points", "按等级 / 重要度 / 片区 / 市政 / 是否可派 / 是否已派筛选并排序。", "Filter flood points by level / criticality / zone / authority / dispatchable / served and rank them.",
               (SCEN, Pm("level", "enum", "应急等级", "level", choices=("III", "II", "I")), Pm("criticality", "enum", "客户重要度", "criticality", choices=("Very High", "High", "Medium", "Low")), Pm("region", "enum", "市政", "authority", choices=("ADM", "AAM", "DRM")),
                Pm("zone", "zone", "片区名", "zone"), Pm("dispatchable", "bool", "只看可派泵", "dispatchable only"), Pm("served", "bool", "只看已派 / 未派", "served"), Pm("order_by", "enum", "排序", "order by", choices=("priority", "volume_m3", "max_depth_m", "done_min"), default="priority"), Pm("limit", "int", "条数", "limit", lo=1, hi=100, default=10)),
               intents=("哪些", "列出", "清单", "最严重", "前", "排名", "有多少", "list", "which", "top", "how many", "worst"), examples=("50 年一遇下最严重的 10 个 III 级点", "Which Very High points are still unserved at 100 years?"), ui="切到第三道并高亮"),
    ActionType("set_scenario", "切换情景", "Set scenario", "把控制台与后续回答切到某个情景。", "Switch the console and following answers to a scenario.",
               (Pm("scenario", "scenario", "情景", "scenario", required=True),), intents=("切到", "换成", "切换", "switch to", "set scenario"), examples=("切到 100 年一遇",), ui="控制台切重现期"),
    ActionType("rain_to_scenario", "预报雨量转情景", "Forecast rain to scenario",
               "把预报的 24 h 雨量换成系统口径：按 DMT WA-726-1 表 3-6（B 区）对数插值的年一遇、最贴近的设计情景（含 10 年 + 20% CC）与偏安全一侧的情景、可比的实测事件，再用三条判据（雨量 / 该情景 III 级点数 / 全网溢流）给出预警等级与预置窗口建议；缺省把控制台切到最贴近的情景。值班时「预报 X mm，相当于几年一遇、该用哪个情景」用它。",
               "Converts a forecast 24-h rainfall into system terms: the log-interpolated return period on DMT WA-726-1 Table 3-6 (zone B), the nearest design scenario (incl. 10-yr + 20% CC) and the safe-side one, comparable observed events, then the alert level from the three rules (rain / level-III count / network spill) and whether the pre-positioning window is open; switches the console to the nearest scenario by default.",
               (Pm("p24_mm", "float", "预报 24 h 雨量（mm）", "forecast 24-h rainfall (mm)", required=True, lo=1, hi=400), Pm("lead_days", "float", "距降雨几天（可选）", "days until the rain (optional)", lo=0, hi=30),
                Pm("apply", "bool", "是否把控制台与后续回答切到该情景", "switch the console and following answers to it", default=True)),
               intents=("几年一遇", "多少年一遇", "年一遇", "相当于", "预报", "预报雨量", "用哪个情景", "哪个情景", "该用什么情景", "return period", "forecast", "which scenario", "how rare"),
               examples=("预报三天后有 60 mm 的雨，相当于几年一遇？该用哪个情景准备？", "The forecast says 75 mm in 24 h: what return period is that and which scenario should we use?"), ui="控制台切到最贴近的情景"),
    ActionType("dispatch_plan", "应急调度", "Dispatch plan", "某情景、某决策时刻的调度结果：KPI、波次、（给了积水点就只看它的）派单。", "Dispatch solution for a scenario at a decision time: KPIs, waves, and the assignments (for one point if given).",
               (SCEN, Pm("decision_time_min", "float", "决策时刻（事件开始后分钟；缺省雨峰后 30 min）", "decision time (min after start)", lo=0, hi=2880), Pm("fp_id", "fp_id", "只看这个点", "only this point"),
                Pm("objective", "enum", "目标：fastest 最快退水 / balanced 平衡 / cheapest 最省钱（帕累托三点）", "objective: fastest / balanced / cheapest", choices=("fastest", "balanced", "cheapest"), default="balanced"),
                Pm("budget_aed", "float", "预算上限（AED，可选）", "budget cap (AED, optional)", lo=0),
                Pm("robust", "bool", "鲁棒：按 [dispatch.robust] 的多情景权重取期望（一张单同时对几种雨），并把这张单放到每个情景上重演、与只按当前情景优化的单对照", "robust: expectation over the weighted scenarios in [dispatch.robust] (one plan for several rains), replayed per scenario and compared with the single-scenario plan", default=False)),
               intents=("派泵", "派几台", "调度", "派车", "泵队", "几台泵", "派单", "鲁棒", "同时应对", "几种雨", "三种雨", "dispatch", "pumps", "how many pumps", "assign", "robust"), examples=("ADM-62 派几台泵、几点能排干", "Dispatch KPIs for the 50-year storm at minute 240", "给一张同时应对 10cc / 25 年 / 50 年的鲁棒调度单"), ui="切到第三道、设决策时刻"),
    ActionType("resource_check", "资源盘点", "Resource check",
               "第三道防线的资源够不够：各驻地的泵 / 运输车 / 班组人数 / 软管 / 燃油 / 罐车库存与本情景调度的占用，缺口（人手、换班、车次），以及哪些是模拟值。",
               "Whether line-3 resources suffice: per-depot pumps / vehicles / crews / hose / fuel / tankers versus this scenario's plan, the gaps (staff, shift relief, trips), and which figures are simulated.",
               (SCEN, Pm("objective", "enum", "按哪个目标的方案盘点", "plan objective", choices=("fastest", "balanced", "cheapest"), default="balanced")),
               intents=("资源", "人手", "人员够", "车够", "车辆", "班组", "软管", "燃油", "物资", "够不够", "resources", "crews", "vehicles", "staff", "enough"), examples=("人手和车够不够？各驻地的资源占用",), ui="切到第三道防线"),
    ActionType("replan", "现场重规划", "Replan from the field",
               "事中重规划：现场报「已退干」的点、故障的泵、993 报案，从当前时刻重解——已出发的派单锁定，未出发的作废重排；报案匹配到积水点后加权。观测文件里登记为 dry 的点与 exchange/incidents.json 自动并入。",
               "Mid-event replanning: points reported dry, failed pumps and 993 incidents are applied from the current time; dispatched units are locked, undispatched ones re-solved; matched incidents get a priority boost. Observations marked dry and exchange/incidents.json are merged automatically.",
               (SCEN, Pm("t_now_min", "float", "现在是事件开始后第几分钟", "current time (min after start)", required=True, lo=0, hi=2880), Pm("dry_points", "str", "已退干的积水点编号，逗号分隔", "points reported dry, comma-separated"),
                Pm("failed_units", "str", "故障泵编号，逗号分隔", "failed unit ids, comma-separated"), Pm("incidents", "str", "993 报案：积水点编号或 lon,lat，分号分隔；加 ! 表示已核实", "993 incidents: ids or lon,lat separated by ';' (append ! if verified)"),
                Pm("objective", "enum", "目标", "objective", choices=("fastest", "balanced", "cheapest"), default="balanced")),
               intents=("重规划", "重新规划", "重排", "退干了", "已退干", "泵坏了", "故障", "报案", "993", "replan", "re-plan", "reported dry", "broke down", "incident"), examples=("现在 300 分钟，ADM-4 已退干、PA-016 坏了，重排一下",), ui="切到第三道防线"),
    ActionType("stress_test", "预案压力测试", "Stress test the plan",
               "把当前情景的调度单原样放到更重的情景上重演：哪些点退不干或超时、还差多少排量与台数——回答「雨比预期大时还差多少」。",
               "Replays this scenario's dispatch plan on a heavier scenario's hydrographs: which points no longer drain or miss target, and how much more pumping (units) is needed; answers 'how much more if the rain is heavier'.",
               (SCEN, Pm("worse_scenario", "str", "更重的情景键（缺省：下一档设计情景）", "heavier scenario key (default: next design scenario)"), Pm("objective", "enum", "按哪个目标的方案", "plan objective", choices=("fastest", "balanced", "cheapest"), default="balanced")),
               intents=("压力测试", "雨更大", "雨比预期大", "更重", "还差多少", "stress", "heavier rain", "worse case", "how much more"), examples=("如果雨比预报大一档，现在这套方案还差多少泵？",), ui="切到第三道防线"),
    ActionType("staging_status", "预置到位：需要 vs ERP 计划 vs check-in", "Staging status: need vs ERP plan vs check-in",
               "各驻地：本系统按预警等级算出的预置台数（需要）、客户预案 A 的计划台数（ERP 计划，11/1 到位）、承包商到场 check-in 台数（exchange/checkins.json），以及预置到位率；没有 check-in 时标 plan only。",
               "Per depot: units this system says to pre-position (need), the client's Plan A count (ERP plan, in place by 1 Nov), contractor check-ins (exchange/checkins.json) and the readiness rate; marked plan only when no check-ins exist.",
               (SCEN, Pm("alert_level", "enum", "预警等级", "alert level", choices=("蓝", "黄", "橙", "红"), default="橙")),
               intents=("到位率", "预置到位", "check-in", "签到", "ERP", "计划 vs", "到位了没", "staging status", "readiness", "checked in"), examples=("橙色预警下各驻地预置到位了没？",), ui="切到第二道防线"),
    ActionType("traffic_status", "路况", "Traffic",
               "现在调度用的路况：来源（Google Routes 实时 / 预测、模拟时段曲线、关）、出发时刻、各驻地的拥堵系数（带路况行程 ÷ 自由流，行程矩阵按驻地放大）、Google 时对当前调度单逐对精算的偏差（哪些行程实际比模型慢）；refresh=true 重新取一次。",
               "Traffic used by the dispatcher: source (Google Routes live / predictive, simulated time-of-day curve, off), departure time, per-depot congestion factor (traffic ÷ free-flow, applied row-wise to the travel matrix), and with Google the pair-by-pair check of the current plan (which trips run slower than modelled); refresh=true re-fetches.",
               (SCEN, Pm("refresh", "bool", "重新取一次路况（Google 按元素计费）", "re-fetch traffic (Google bills per element)", default=False),
                Pm("depart_at", "str", "出发时刻（当地时间 ISO，如 2026-09-25T14:00；缺省现在 + 决策时刻）", "departure time (local ISO; default now + decision time)")),
               intents=("路况", "交通", "堵车", "拥堵", "谷歌", "Google", "实时路况", "预测路况", "traffic", "congestion", "jam", "google maps", "routes api"), examples=("现在路况怎么样，派车行程按什么系数算的？", "Refresh traffic and show which depots are congested"), ui="切到第三道防线",
               side_effect="refresh=true 时调用 Google Routes（按元素计费）并写 out/traffic_cache.json，之后所有情景的行程矩阵按新系数算"),
    ActionType("assumptions", "假设总账", "Assumption register",
               "列出当前情景 / 当前回答依赖的全部模拟、假设、占位、未核实与缺失的输入：资源与泵队、限重桥、承包商边界、费率、实测事件的缺省雨量、IDF、路况、热点坐标、历史积水记录、实时源、世界模型、过程线口径；每条带来源与怎么替换。",
               "Lists every simulated, assumed, placeholder, unverified or missing input behind the current scenario / answer (resources and fleet, bridges, contractor boundaries, rates, event rainfall defaults, IDF, traffic, hotspot coordinates, historical flood records, live feeds, world model, hydrograph basis), each with its source and how to replace it.",
               (SCEN,), intents=("假设", "模拟的", "哪些是真的", "哪些是假的", "占位", "可信", "依据是什么", "assumptions", "simulated", "what is real", "placeholder", "how reliable"),
               examples=("现在这些数字里哪些是模拟的、哪些是真的？",), ui="打开假设面板"),
    ActionType("feeds_status", "外部源接入状态", "External feeds status",
               "993 报案、承包商 check-in、桥梁限重台账、承包商服务边界四个外部源：现在是拉（url）还是推 / 文件、有多少行、最近更新、推送地址；ADM 应急系统开放接口后只需填 url。",
               "The four external feeds (993 incidents, contractor check-ins, bridge weight register, contractor boundaries): pull URL or push/file, row counts, last update, push endpoints; once ADM opens its interface only the url needs filling.",
               (), intents=("接入", "接进来", "数据源", "外部源", "接口", "推送", "报案接口", "feeds", "integration", "data source", "hooked up", "endpoint"), examples=("993 报案和 check-in 现在接进来了吗？",), ui=""),
    ActionType("point_history", "积水点历史", "Flood point history",
               "一个积水点的生命周期：历次降雨事件里的经历（是否积水、水深、时长、实际派泵与退干、来源）、模型对同一场雨的推演与对账（命中 / 漏报 / 误报）、复发率、最近一次积水、当前状态、工程治理状态与触发的规则（屡次积水未治理、治理是否见效）。",
               "One flood point's life: its episodes in each rain event (flooded or not, depth, duration, pumps and drain time, source), the model's prediction for the same rain and the agreement, recurrence, last flooding, current status, treatment status and fired rules.",
               (Pm("fp_id", "fp_id", "积水点", "flood point", required=True),), intents=("历史", "以前", "过去", "几次", "复发", "2024", "2026年3月", "上次", "生命周期", "经历", "history", "past", "before", "recurrence", "lifecycle", "previous"),
               examples=("ADM-62 以前积过几次水？2024 年 4 月那场它什么情况", "History of ADM-62: did it flood in April 2024?"), ui="打开单热点详情"),
    ActionType("event_review", "降雨事件复盘", "Rain event review",
               "一场雨的复盘：雨量与相当于几年一遇、多少点有记录积水 / 模型推演积水、按阶段的漏斗、记录 vs 模型的对账（POD / FAR / 水深 RMSE 与 pass / fail / insufficient）、漏报与误报的点、连续不合格是否要重校准。缺省最近一场历史事件。",
               "Review of one rain: depth and return-period equivalent, points recorded / modelled flooded, stage funnel, record-vs-model verification (POD / FAR / depth RMSE, pass / fail / insufficient), missed and false-alarm points, and whether recalibration is due.",
               (Pm("event_id", "str", "事件（2024-04、2026-03，或开的预报 / 进行中事件；缺省最近一场）", "event id (default: latest)"),), intents=("复盘", "对账", "那场雨", "事件回顾", "漏报", "误报", "命中率", "review", "verification", "that storm", "post-event", "hindcast"),
               examples=("复盘 2024 年 4 月那场雨：模型对上了多少、漏了多少", "Review the April 2024 event: hits, misses, false alarms"), ui="切到该事件对应的情景"),
    ActionType("lifecycle_overview", "生命周期漏斗", "Lifecycle funnel",
               "某场雨（缺省进行中 / 预报事件，没有就最近一场历史事件）里所有积水点按阶段的分布：预警待命 / 已预置 / 已上报 / 已核实 / 已派单 / 到位 / 抽排 / 退干 / 关闭 / 复盘，以及按市政的拆分。",
               "Distribution of all flood points by lifecycle stage in one rain (default the open forecast / live event, else the latest historical one), split by authority.",
               (Pm("event_id", "str", "事件", "event id"),), intents=("漏斗", "各阶段", "多少个已关闭", "处置进度", "进度", "funnel", "progress", "how many closed", "stages"), examples=("现在这场雨各阶段各有多少点？",), ui="切到第三道防线"),
    ActionType("update_episode", "登记生命周期节点", "Log a lifecycle stage",
               "给某积水点在某事件里登记一个阶段（已上报 / 已核实 / 已派单 / 到位 / 抽排 / 已退干 / 已复盘 …）及时间、水深、实际派泵台数、备注；写 out/episodes.jsonl。批量的客户历史记录走 exchange/episodes.json（契约 swbrain.episodes/1，POST /api/feed/episodes）。",
               "Log a stage for a flood point in an event (reported / verified / dispatched / arrived / pumping / dry / reviewed …) with time, depth, units sent and note; written to out/episodes.jsonl. Bulk client records go through exchange/episodes.json (swbrain.episodes/1, POST /api/feed/episodes).",
               (Pm("fp_id", "fp_id", "积水点", "flood point", required=True), Pm("stage", "enum", "阶段", "stage", required=True, choices=("alerted", "staged", "reported", "verified", "dispatched", "arrived", "pumping", "dry", "reviewed")),
                Pm("event_id", "str", "事件（缺省进行中 / 预报事件）", "event (default: open event)"), Pm("at", "str", "时间（ISO；缺省现在）", "time (ISO; default now)"), Pm("depth_m", "float", "水深 m", "depth m", lo=0, hi=5),
                Pm("units", "int", "实际派泵台数", "units sent", lo=0, hi=50), Pm("note", "str", "备注", "note")),
               side_effect="写 out/episodes.jsonl", intents=("登记", "记一下", "到位了", "开始抽", "上报了", "核实了", "log stage", "mark", "arrived", "pumping started", "reported"), examples=("ADM-62 两台泵 14:30 到位了，记一下",), ui="打开单热点详情"),
    ActionType("close_point", "关闭积水点（本次事件）", "Close the flood point (this event)",
               "把某积水点在某事件里的经历关闭：前置条件是已有退干时间戳或最近一次现场观测为 dry；写 out/episodes.jsonl。关闭不删点，只结束这一次经历，历史保留。",
               "Close a flood point's episode in an event: requires a drained timestamp or a latest dry observation; written to out/episodes.jsonl. Closing ends this episode only; the point and its history remain.",
               (Pm("fp_id", "fp_id", "积水点", "flood point", required=True), Pm("event_id", "str", "事件（缺省进行中 / 预报事件）", "event (default: open event)"), Pm("note", "str", "备注", "note")),
               preconditions=("该经历已有 dry 阶段，或最近一次现场观测为 dry",), side_effect="写 out/episodes.jsonl", intents=("关闭", "销号", "结束", "close", "close out", "sign off"), examples=("ADM-62 已经干了，关闭",), ui="打开单热点详情"),
    ActionType("set_proposal_status", "推进工程建议状态", "Advance a proposal's status",
               "把一条工程建议推进到 已批准 / 在建 / 已建成 / 已验证有效（或 已否决）；写 out/proposal_status.jsonl；相关积水点的工程治理状态与「治理是否见效」规则随之变化。",
               "Move a proposal to approved / under construction / built / verified (or rejected); written to out/proposal_status.jsonl; the related flood points' treatment status and the effectiveness rule follow.",
               (Pm("pid", "pid", "建议编号", "proposal id", required=True), Pm("status", "enum", "状态", "status", required=True, choices=("建议", "已批准", "在建", "已建成", "已验证有效", "已否决")), Pm("note", "str", "备注", "note")),
               side_effect="写 out/proposal_status.jsonl", intents=("批准了", "建成了", "在建", "完工", "否决", "approved", "built", "under construction", "completed", "rejected"), examples=("建议 12 已经建成了，记一下",), ui="切到第一道并展开该建议"),
    ActionType("open_event", "开启降雨事件", "Open a rain event",
               "值班时为预报中 / 进行中的一场雨建一个事件：日期、24 h 雨量、类型（forecast / live）；自动算相当于几年一遇并对上最贴近的情景，所有该情景下推演积水的点进入「预警待命」。写 out/events.jsonl。",
               "Open an event for a forecast / ongoing rain: date, 24 h depth, kind (forecast / live); the return-period equivalent and nearest scenario are derived and every point modelled flooded in that scenario becomes 'on alert'. Written to out/events.jsonl.",
               (Pm("label", "str", "名称（如 2026-09-25 预报强降雨）", "label", required=True), Pm("date", "str", "日期 YYYY-MM-DD", "date", required=True), Pm("p24_mm", "float", "预报 / 实测 24 h 雨量 mm", "24 h depth mm", required=True, lo=1, hi=400),
                Pm("kind", "enum", "类型", "kind", choices=("forecast", "live"), default="forecast"), Pm("event_id", "str", "编号（缺省 EV-日期）", "id (default EV-date)"), Pm("scenario", "scenario", "指定情景（缺省按雨量最贴近的）", "scenario (default: nearest by depth)")),
               side_effect="写 out/events.jsonl", intents=("开事件", "建事件", "新事件", "这场雨", "开始记录", "open event", "new event", "start event"), examples=("给 9 月 25 日预报的 60 mm 开一个事件",), ui="切到对应情景"),
    ActionType("preposition_plan", "预置驻地", "Staging plan", "按预警等级给预置驻地、覆盖点数与预置价值（少积水多少小时、花多少钱）。", "Staging sites by alert level, covered points and the value of pre-positioning (flood-hours saved, cost).",
               (SCEN, Pm("alert_level", "enum", "预警等级", "alert level", choices=("蓝", "黄", "橙", "红"), default="红")), intents=("预置", "驻地", "提前放", "预布", "staging", "pre-position", "preposition"), examples=("红色预警下预置哪几个驻地", "Staging plan for orange alert, 25-year"), ui="切到第二道"),
    ActionType("upsize_whatif", "换管 what-if", "Upsize what-if", "把积水点的匹配管段放大 1–2 级管径，看积水峰值与退干时间怎么变（对比无泵 / 派泵）。", "Upsize the matched reach by 1–2 diameter steps and compare peak volume and drain time with no-pump / pumped.",
               (Pm("fp_id", "fp_id", "积水点", "flood point", required=True), Pm("dn_steps", "int", "放大几级", "diameter steps", lo=1, hi=3, default=2), SCEN), intents=("换管", "扩管", "放大管径", "管径", "upsize", "bigger pipe", "diameter"), examples=("ADM-62 换大两级管径能不能不积水",), ui="打开单热点详情"),
    ActionType("proposal_whatif", "工程建议 what-if", "Proposal what-if", "把一条第一道建议套到全网代理评估器上，看能力比 / 蓄渗缺口 / 高潮暴露的 KPI 变化与造价。", "Apply a line-1 proposal to the network surrogate and see KPI deltas and cost.",
               (Pm("pid", "pid", "建议编号", "proposal id", required=True),), intents=("建议", "方案", "工程", "proposal", "what if we build"), examples=("建议 12 做了之后全网 KPI 变多少",), ui="切到第一道并展开该建议"),
    ActionType("issue_alert", "签发预警提示", "Issue alert advisory", "按预报 24 h 雨量代入规则层：等级、分角色提示、时间线；落 out/alerts/。", "Run the forecast 24 h rainfall through the rule layer: level, role prompts, timeline; written to out/alerts/.",
               (Pm("p24_mm", "float", "预报 24 h 雨量 mm", "forecast 24 h rainfall mm", required=True, lo=0.1, hi=400), Pm("lead_h", "float", "提前量 h", "lead time h", lo=1, hi=120, default=24), Pm("area", "str", "片区 / 地区（可选）", "area (optional)")),
               preconditions=("雨量在 0–400 mm 之间",), side_effect="写 out/alerts/advisory_*.json", intents=("预警", "预报", "明天下", "签发", "alert", "forecast", "advisory", "warning"), examples=("预报明天 80 mm，要发什么级别的预警", "Forecast 60 mm in 24 h, what alert?"), ui="切到第二道、填预报"),
    ActionType("compare_world_model", "世界模型对照", "World-model cross-check", "该情景下世界模型推演核与客户热点、本系统积水点的互相印证。", "How the GWM run agrees with client hotspots and system flood points in this scenario.",
               (SCEN,), intents=("世界模型", "GWM", "推演核", "印证", "对照", "world model", "cross-check", "agree"), examples=("50 年一遇世界模型印证了多少客户热点",), ui="切到数据与可信度并并排显示"),
    ActionType("record_observation", "登记现场观测", "Record field observation", "把一次现场观测写进 out/observations.jsonl，并立刻用「观测与推演冲突」规则复核。", "Append a field observation to out/observations.jsonl and re-check the observation-conflict rule.",
               (Pm("fp_id", "fp_id", "积水点", "flood point", required=True), Pm("status", "enum", "状态", "status", required=True, choices=("flooding", "receding", "dry")), Pm("depth_m", "float", "水深 m", "depth m", lo=0, hi=5, default=0), Pm("note", "str", "备注", "note"), Pm("reporter", "str", "上报人", "reporter")),
               preconditions=("积水点存在", "水深 0–5 m", "状态 ∈ flooding/receding/dry"), side_effect="写 out/observations.jsonl", intents=("现场", "观测", "登记", "上报", "实际水深", "已退水", "无积水", "observed", "report", "on site", "field"), examples=("现场看 ADM-62 水深 0.4 m 还在积水，记一下", "ADM-3 is dry on site, log it"), ui="刷新单热点详情"),
    ActionType("generate_report", "生成报告", "Generate report", "把某情景的五页结论导出为自包含 HTML。", "Export the scenario as a self-contained HTML report.",
               (SCEN, Pm("lang", "enum", "语言", "language", choices=("zh", "en"), default="zh")), intents=("报告", "导出", "汇报", "report", "export"), examples=("出一份 50 年一遇的报告",), ui="新窗口打开报告"),
    ActionType("explain_rule", "解释规则", "Explain rule", "某条规则怎么判、依据什么。", "How a rule decides and what it is based on.",
               (Pm("rule", "rule", "规则名（等级 / 时限 / 可派 / 接力 / 预警 / 达标 …）", "rule name", required=True),), intents=("为什么", "怎么判", "怎么算", "规则", "依据", "why", "how is", "rule", "criteria"), examples=("为什么 ADM-62 是 III 级", "How is the alert level decided?"), ui=""),
    ActionType("zone_overview", "片区概览", "Zone overview", "片区的风险分、超载份额、蓄渗缺口、积水点与建议。", "Zone risk, overload share, storage deficit, flood points and proposals.",
               (Pm("zone", "zone", "片区名", "zone", required=True), SCEN), intents=("片区", "zone", "district"), examples=("Mussafah 片区怎么样",), ui="切到总览并选片区"),
    ActionType("catchment_info", "汇水区查询", "Catchment lookup",
               "积水点在哪个汇水区（几套来源分别给、带可信度）；某个汇水区里有哪些积水点和设施、和同来源的面有没有重叠；某个分析范围（C1 / C1 客户单元版）由哪些汇水区组成、漏了哪些热点。只读。",
               "Which catchment a flood point is in (per source, with confidence); which flood points and assets a catchment holds and whether it overlaps others; which units make up an analysis domain (C1 / C1 client units) and which hotspots it misses. Read-only.",
               (Pm("fp_id", "fp_id", "积水点编号", "flood point"), Pm("catchment", "str", "汇水区名称 / 编号（RB-3、MBZ、CL:147 …）", "catchment name / id"),
                Pm("domain", "enum", "分析范围", "analysis domain", choices=("C1", "C1U", "C1M"))),
               intents=("汇水区", "集水区", "流域", "哪个汇水区", "属于哪个", "catchment", "drainage area", "watershed", "分析范围", "计算域"),
               examples=("ADM-94 在哪个汇水区？", "RB-3 里有哪些积水点？", "C1 客户单元版漏了哪些热点？"), ui="高亮相关积水点"),
    ActionType("count_points", "积水点盘点", "Flood point inventory",
               "现在有多少个积水点：客户清单总数（按市政）、本情景下算出积水过程的点数、按等级 / 已派 / 可派 / 世界模型印证的分布，以及世界模型推演核数与模型独有数；地图上同时点亮积水区与客户热点。",
               "How many flood points there are: client list totals by authority, points with a flood state in this scenario, breakdown by level / served / dispatchable / world-model confirmed, plus world-model cores and model-only cores; lights up the flood and hotspot layers on the map.",
               (SCEN,), intents=("多少个积水点", "有多少积水点", "几个积水点", "积水点数量", "现在有多少", "盘点", "how many flood points", "how many points", "inventory", "count"), examples=("现在有多少个积水点？", "How many flood points are there now?"), ui="点亮积水区 + 客户热点图层并缩放到全部"),
    ActionType("world_model_new_points", "世界模型新增积水点", "New flood points from the world model",
               "世界模型推演后新增了多少积水点：推演核里 300 m 内既没有客户热点也没有本系统积水点的「模型独有」核，按体积 / 水深排序，各自最近的客户热点与本系统积水点；地图上高亮这些核。",
               "How many flood points the world-model rollout adds: cores with neither a client hotspot nor a system flood point within 300 m (model-only), ranked by volume / depth, with the nearest hotspot and system point for each; highlighted on the map.",
               (SCEN, Pm("limit", "int", "列出前几个", "how many to list", lo=1, hi=50, default=10), Pm("order_by", "enum", "排序", "order by", choices=("volume_m3", "max_depth_m"), default="volume_m3")),
               intents=("世界模型新增", "推演后新增", "新增了多少", "新增积水点", "模型独有", "多了多少", "new flood points", "world model added", "model-only", "additional points"), examples=("通过世界模型推演后新增了多少积水点？", "How many new flood points does the world model add?"), ui="开世界模型推演层，高亮模型独有的核"),
    ActionType("recession_batch", "一批积水点怎样最快退水（含成本与代价）", "Fastest recession for a set of points (with cost)",
               "对一批积水点（给编号，或按等级 / 重要度 / 片区筛前 N 个）逐点算退水方案，汇总：每点最快手段与退干时刻、能否达标、需要几台泵、运维费用、单点最优对全局的代价；地图上高亮这些点。",
               "Runs recession options for a set of points (by id, or the top N by level / criticality / zone) and aggregates: fastest measure and drain time per point, target compliance, pumps needed, operating cost, and what single-point optima cost the network; highlights the points on the map.",
               (Pm("fp_ids", "str", "积水点编号，逗号分隔（可选；不给则按筛选取）", "comma-separated ids (optional)"), SCEN, Pm("level", "enum", "应急等级", "level", choices=("III", "II", "I")), Pm("criticality", "enum", "客户重要度", "criticality", choices=("Very High", "High", "Medium", "Low")), Pm("zone", "zone", "片区", "zone"),
                Pm("limit", "int", "最多几个点", "max points", lo=1, hi=20, default=5), Pm("with_upsize", "bool", "是否算换管造价", "include upsizing", default=True)),
               intents=("加快", "退水速度", "退得更快", "提速", "这些点", "这几个点", "怎么处置", "speed up", "faster", "these points", "accelerate"), examples=("怎么加快这些积水点的退水速度？", "How do we speed up recession at these points?"), ui="切到第三道，高亮这些点"),
    ActionType("cost_summary", "成本与代价", "Cost and trade-offs",
               "需要投入的成本和代价：给了积水点就汇总它们最快手段的运维费用与换管造价、抢走的泵与被推迟的点；没给就给该情景的全局账——第三道调度费用、各级预警的预置费用与价值、客户预案 A 的费用。",
               "What it costs: for given points, the operating cost of their fastest measures, upsizing capital, pumps taken and points delayed; without points, the scenario-level bill — dispatch cost, staging cost and value per alert level, client Plan A cost.",
               (Pm("fp_ids", "str", "积水点编号，逗号分隔（可选）", "comma-separated ids (optional)"), SCEN, Pm("limit", "int", "最多几个点", "max points", lo=1, hi=20, default=5)),
               intents=("成本", "代价", "费用", "多少钱", "投入", "预算", "cost", "budget", "price", "AED", "trade-off"), examples=("需要投入的成本和代价是怎么样的？", "What would it cost and what is the trade-off?"), ui="切到第三道并高亮相关点"),
    ActionType("recession_options", "退水方案：什么时候退、怎样最快", "Recession options: when and how fastest",
               "围绕一个积水点的两个终极问题：什么时候退水（自然 / 全局最优给它的 / 单点最优 / 预置 / 罐车 / 换管各自的退干时刻），用什么手段最快；并说清单点最优会从全局调度单里抢走谁的泵、别的点推迟多久。",
               "The two ultimate questions for a flood point: when does it recede (natural / global-optimal dispatch / single-point optimum / pre-positioned / tankers / upsizing) and what drains it fastest; plus what the single-point optimum costs the rest of the network.",
               (Pm("fp_id", "fp_id", "积水点", "flood point", required=True), SCEN, Pm("decision_time_min", "float", "决策时刻（事件开始后分钟；缺省雨峰后 30 min）", "decision time (min after start)", lo=0, hi=2880), Pm("strict", "bool", "单点最优严格版：把该点权重放大重解全网，给出抢了谁的泵、推迟了谁、多花多少（多算 10–20 s）", "strict single-point optimum: re-solve the network with this point weighted up (adds 10–20 s)", default=False)),
               intents=("什么时候退", "何时退", "退水", "什么时候能退", "最快", "怎样最快", "怎么最快", "什么手段", "when will", "when does", "recede", "fastest", "how soon", "quickest"), examples=("ADM-62 什么时候能退水，怎样最快", "When will ADM-62 recede and what drains it fastest?"), ui="打开单热点详情（含退水方案表）"),
    ActionType("replay_point", "单点重演", "Replay one point", "按调度单（或手工指定的泵）重演积水点体积过程线：无泵 vs 有泵的退干时刻与积水时长。", "Replay the point's volume with the dispatched (or manually specified) pumps: drain time and flood hours, base vs pumped.",
               (Pm("fp_id", "fp_id", "积水点", "flood point", required=True), Pm("pumps", "str", "手工泵组，格式 型号:台数@到位分钟，如 12in:2@240,8in:1@260（可选，缺省用调度单）", "manual pumps as model:count@arrive_min, e.g. 12in:2@240 (optional; default = dispatch plan)"), SCEN), intents=("重演", "过程线", "多久", "replay", "hydrograph", "how long"), examples=("ADM-62 用两台 12 寸泵多久能排干", "How long until ADM-62 drains with two 12in pumps?"), ui="打开单热点详情"),
    ActionType("trace_downstream", "下游追溯", "Trace downstream", "积水点匹配管段往下游到出水口 / 泵站的路径与长度。", "Path from the matched reach downstream to the outfall / pump station.",
               (Pm("fp_id", "fp_id", "积水点", "flood point", required=True),), intents=("下游", "排到哪", "出水口", "去哪", "downstream", "outfall", "where does"), examples=("ADM-62 的水最后排到哪里",), ui="地图上画下游路径"),
    # ---- L1 / L2 / L2+ 规划结果（2026-09-28；数据 console/data/l2plus_catalog.json，与控制台「防线 2+」与在线报告同源）
    ActionType("state_gap", "各态缺口对比", "Gap across states",
               "某片区 L1 → L2 → L2+planned → L2+suggested 各态的溢出量 / 最大积水面积 / 最大水深 / 退水，每一步只和上一态比；对照态（L2+B）和它的父态比。不给片区就列出有结果的片区（Mussafah_00 / 29.RB2 / Madinat Al Riyad）。",
               "L1 → L2 → L2+planned → L2+suggested for a region: overflow, max flooded area, max depth, recession, each step against the previous state; reference states (L2+B) against their parent. Without a region, lists the regions with results.",
               (Pm("region", "str", "片区（Mussafah_00 / 29.RB2 / Madinat Al Riyad）", "region"),),
               intents=("五态", "四态", "几个态", "各态", "缺口对比", "态对比", "l2+", "l1 和 l2", "l1、l2", "gap", "states compared", "mitigation states"),
               examples=("Mussafah 五态的缺口对比是怎样的？", "Compare the states for 29.RB2"), ui="切到防线 2+ 对应视图（Mussafah 为五态工作台）"),
    ActionType("city_gap", "全市 L1 / L2 差值", "Citywide L1 / L2 gap",
               "全市 L1（既有管网）与 L2（加 22 座已建成塘）的溢流、差值、接塘管网、冒水井增减、蓄存能力与排空，以及全市一二维的进度。一维口径，差值是下限。",
               "Citywide L1 (existing network) vs L2 (plus 22 built ponds): overflow, difference, pond-connected networks, manholes better / worse, storage and emptying, and the status of the citywide 1D/2D run. 1D basis; the difference is a lower bound.",
               (), intents=("全市", "全城", "citywide", "whole city", "全市 l1", "全市l1"), examples=("全市 L1 和 L2 差多少？", "What is the citywide L1 vs L2 gap?"), ui="切到防线 2+「全市 L1 / L2」差值图"),
    ActionType("facility_plan", "各态设施清单", "Facilities of a state",
               "某片区某一态新增了哪些设施（塘 / 集水井 / 地下箱涵）：位置、库容、底 / 顶标高、用地、挪动距离、来源（Parsons / PO 塘方案 / 本系统）；地图上标出。不给态就取主线终点。",
               "Which facilities a state adds in a region (ponds / wells / underground boxes): location, volume, levels, land, how far moved, source (Parsons / PO scheme / ours); marked on the map. Defaults to the end of the main chain.",
               (Pm("region", "str", "片区", "region", required=True), Pm("state", "str", "态（L2 / L2+planned / L2+B / L2+suggested，可加「放宽」；旧名 L2+A、L2+A+B 也认）", "state")),
               intents=("设施", "箱涵", "临时塘", "集水井", "建哪些", "放在哪", "设施清单", "facilities", "box culvert", "boxes", "which ponds"),
               examples=("Mussafah 的 L2+suggested 要建哪些设施？", "Which boxes does L2+B add in 29.RB2?"), ui="在地图上标出这些设施"),
    ActionType("as_built_next", "现有方案做完还要补什么", "What to add on top of the current plan",
               "把 L2+planned（Mussafah：Parsons 方案；29.RB2：PO 塘方案，来源待确认）当既成事实，在它之上本系统还要补哪些设施、补完缺口 / 面积 / 水深再降多少，以及两套设施的重叠。",
               "Taking L2+planned as built (Mussafah: the Parsons scheme; 29.RB2: the PO scheme, source unconfirmed), which facilities we still add on top, how much the gap / area / depth fall further, and how the two overlap.",
               (Pm("region", "str", "片区（Mussafah_00 / 29.RB2）", "region", required=True),),
               intents=("既成事实", "还要补", "还需要补", "还要做什么", "之上", "做完以后", "做完之后", "on top of", "as built", "what else", "still need"),
               examples=("如果 Parsons 方案已经定了，Mussafah 还要补什么？", "On top of the PO scheme, what does 29.RB2 still need?"), ui="切到 L2+suggested、对比基准 L2+planned，并标出补的设施"),
    ActionType("asset_kind", "资产种类是什么", "What an asset type is",
               "一种地表水管理资产（箱涵、塘、湿井、泵站、土堤、Jersey 护栏、地面管、移动泵 …）的定义、功能、客户 L1/L2/L3 分级、在模型里怎么表达、关键参数，带剖面示意图或客户分级图里的实景图。",
               "What a surface-water asset type is (box culvert, pond, wet well, pump station, bund, Jersey barrier, surface pipeline, mobile pump …): definition, functions, client L1/L2/L3 level, how it is modelled, key parameters, with a schematic or the client's photo.",
               (Pm("kind", "str", "资产种类（中文名 / 英文名 / 代码）", "asset type", required=True),),
               intents=("是什么", "什么是", "什么叫", "有什么区别", "what is", "what's a", "difference between"),
               examples=("箱涵是什么？", "What is a box culvert?"), ui="打开本体模型页的资产图鉴并选中这一种"),
    # ---- 多智能体（2026-09-30）：一二维计算 / 报告交付 / 数据质检三个专业智能体的动作（owner 标明归属；实现在 actions/hydro.py）
    ActionType("hydro_cases", "一二维情景总览", "1D/2D cases overview",
               "热点群一二维计算（C1）有哪些情景（原计算域 61 / 96 mm、模型版、设计暴雨、客户单元版）、每个情景哪些态已建模 / 已算完 / 已后处理、正在算的进度，以及 8 个热点汇水区 ≥0.30 m 积水面积按态的变化。只读。",
               "Which 1D/2D cases exist for the hotspot cluster (C1), which states are built / computed / post-processed, what is running, and the ≥0.30 m flooded area of the 8 hotspot catchments by state. Read-only.",
               (Pm("tag", "enum", "情景", "case", choices=("R61", "R96", "M61", "D24", "U61")),),
               intents=("一二维", "有哪些情景", "哪些态", "算了哪些", "算完了吗", "多态", "四个态", "1d/2d", "cases"), examples=("C1 一二维算了哪些情景？", "模型版四个态算完了吗？"), owner="hydro"),
    ActionType("hydro_result", "一二维结果", "1D/2D results",
               "某个情景各态的一二维结果：8 个热点汇水区（或某个热点的汇水区与 200 m 内）≥0.10 / ≥0.30 m 积水面积、最大积水量、最深；三个重点热点（ADM-94 / 487 / 192）200 m 内的最深。统计口径见 out/c1_stats_<tag>.json 的 koujing。只读。",
               "1D/2D results of one case by state: ≥0.10 / ≥0.30 m flooded area, peak volume and max depth for the 8 hotspot catchments (or one hotspot's catchment and 200 m ring), plus max depth near the three priority hotspots. Read-only.",
               (Pm("tag", "enum", "情景", "case", required=True, choices=("R61", "R96", "M61", "D24", "U61")), Pm("state", "enum", "态", "state", choices=("L1", "L2", "L2P", "L2S", "L2S3")),
                Pm("hotspot", "fp_id", "热点编号（ADM-94 等）", "hotspot id")),
               intents=("积水面积", "减了多少", "效果", "结果", "最大积水", "水深变化", "flooded area", "result"), examples=("模型版 L2+planned 比 L1 积水减了多少？", "M61 下 ADM-94 各态水深"), owner="hydro"),
    ActionType("hydro_ponding", "二维模拟积水点", "Simulated ponding points",
               "从一二维结果按水深 / 连片面积 / 持续时间阈值判出的模拟积水点（编号 SIM-态-序号，不是客户热点编号）：每片的位置、最大水深、面积、峰值积水量、退水、200 m 内的客户热点。"
               "不给运行就列有哪些运行；不给态就给各态的个数与合计；给了客户热点只看它 200 m 内的。阈值缺省 0.15 m / 100 m² / 0.5 h（试算值，未与客户对齐）。只读。",
               "Ponding points derived from 1D/2D results by depth / patch area / duration thresholds (ids SIM-state-nn, not client hotspot ids): location, max depth, area, peak volume, recession, client hotspots within 200 m. "
               "Without a run it lists runs; without a state it gives counts per state; with a client hotspot only points within 200 m of it. Defaults 0.15 m / 100 m² / 0.5 h (trial values). Read-only.",
               (Pm("run", "str", "运行名（RB01_H60、C1_M61 等）", "run name"), Pm("state", "str", "态（L1 / L2 / L2P / L2S5 等）", "state"),
                Pm("hotspot", "str", "客户热点编号（ADM-140 等）：只看它 200 m 内", "client hotspot id"),
                Pm("depth", "float", "水深阈值 m", "depth threshold m", lo=0.01, hi=5, default=0.15), Pm("min_area", "float", "最小连片面积 m²", "min patch area m²", lo=0, hi=1e6, default=100.0),
                Pm("min_dur", "float", "最短持续 h", "min duration h", lo=0, hi=72, default=0.5), Pm("top", "int", "列前几个", "top n", lo=1, hi=50, default=8)),
               intents=("模拟积水点", "积水点清单", "二维积水点", "sim-", "哪些地方积水", "积水量最大", "ponding"), examples=("RB-01 L1 的模拟积水点有哪些？", "ADM-140 附近 L2+suggested 还有哪些模拟积水点？"), owner="hydro"),
    ActionType("hydro_progress", "计算进度", "Run progress",
               "机器上正在算的一二维计算（含命令行后台链起的）：情景、态、算到几小时、百分比、是否暂停、剩余时间粗估；以及计算申请队列里没结束的任务。只读。",
               "1D/2D runs in progress on the machine (including ones started from the command line): case, state, simulated hours, percent, paused or not, rough time to finish; plus unfinished requests in the queue. Read-only.",
               (), intents=("进度", "算到", "还要多久", "什么时候算完", "在算什么", "队列", "progress", "eta", "running"), examples=("现在一二维算到哪了？", "模型版还要多久算完？"), owner="hydro"),
    ActionType("hydro_submit", "提交计算申请", "Submit a run request",
               "提交一条一二维计算申请（建模 → 计算；可选接着设计并算 L2+suggested v3）；类型 selftest = 快速验证（合成算例约 2 分钟跑通全链路、14 项判定，不用填情景）。只进队列、状态「待批准」，要管理员在管理端批准后才会起计算。",
               "Submit a 1D/2D run request (build → compute; optionally then design and compute L2+suggested v3); kind selftest = quick verification (synthetic case, ~2 min, 14 checks, no case needed). It only enters the queue as pending; it runs after an admin approves it in the admin console.",
               (Pm("tag", "enum", "情景（selftest 不用填）", "case (not needed for selftest)", choices=("R61", "R96", "M61", "D24", "U61")), Pm("states", "str", "态，逗号分隔（L1,L2,L2P,L2S3；缺省 L1,L2,L2P）", "states, comma separated"),
                Pm("kind", "enum", "类型", "kind", choices=("compute", "post", "prep", "selftest"), default="compute"), Pm("then_v3", "bool", "算完接着设计并算 v3", "then design and compute v3", default=False),
                Pm("reason", "str", "为什么要算（给批准的人看）", "why (for the approver)"),
                # 2026-10：计算参数（不填 = 预置情景原样）。计算域是任意范围面：行政区 / 汇水区（按编号多选）；画的、上传的在管理端「新建计算」里给
                Pm("domain_source", "enum", "计算域来源（不填 = 用预置情景的计算域）", "domain source", choices=("admin", "catch")),
                Pm("domain_ids", "str", "行政区 / 汇水区编号，逗号分隔（汇水区如 MODEL:RB3）", "admin / catchment ids, comma separated"),
                Pm("buffer_m", "float", "计算域外扩 m", "domain buffer m", lo=0, hi=5000), Pm("res", "float", "二维计算网格 m（缺省 15）", "2D mesh size m", lo=3, hi=100),
                Pm("tif_res", "float", "GeoTIFF 栅格 m（缺省 = 计算网格）", "GeoTIFF cell m", lo=1, hi=200), Pm("sww_res", "float", ".sww 输出网格 m（缺省 = 计算网格）", ".sww mesh m", lo=3, hi=200),
                Pm("rain", "float", "雨量 mm", "rainfall mm", lo=1, hi=600), Pm("dur", "float", "降雨历时 h（模拟到历时 + 4 h）", "duration h", lo=0.25, hi=72),
                Pm("run", "str", "运行名（不填自动生成）", "run name (auto if empty)")),
               side_effect="写 out/agents/jobs/（一条待批准的申请；不起计算）", intents=("提交计算", "申请计算", "重算", "跑一下", "开跑", "再算", "快速验证", "自检", "submit", "rerun", "selftest"),
               examples=("申请重算模型版 L1 L2 L2+planned", "申请跑一次快速验证", "Submit a D24 rerun"), owner="hydro"),
    ActionType("delivery_status", "交付物清单", "Deliverables",
               "各情景的交付目录在哪、有多少文件多大、含哪些态、有没有 readme / 计算设置 / 资料清单 / 统计值表；在线报告与资料清单的链接。只读。",
               "Where each case's delivery folder is, file count and size, which states, whether readme / settings / materials list / statistics workbook exist; links to the online report and materials list. Read-only.",
               (Pm("tag", "enum", "情景", "case", choices=("R61", "R96", "M61", "D24", "U61")),),
               intents=("交付", "交付物", "交付目录", "报告", "资料清单", "在哪里", "deliverable", "delivery", "report"), examples=("模型版的交付目录在哪？", "C1 报告的链接"), owner="report"),
    ActionType("report_submit", "申请重出交付", "Request re-delivery",
               "申请重做某个情景的后处理与交付目录（统计值、GeoTIFF、.sww、过程线、交付目录）。只进队列、待批准，要管理员在管理端批准后才执行。",
               "Request re-running a case's post-processing and delivery folder. Queued as pending; runs after admin approval.",
               (Pm("tag", "enum", "情景", "case", required=True, choices=("R61", "R96", "M61", "D24", "U61")), Pm("reason", "str", "为什么（给批准的人看）", "why")),
               side_effect="写 out/agents/jobs/（一条待批准的申请）", intents=("重出交付", "重新导出", "重做后处理", "更新交付", "re-export", "regenerate delivery"),
               examples=("申请重出模型版的交付目录",), owner="report"),
    ActionType("qa_data", "数据齐全检查", "Data readiness",
               "一二维热点群计算要的客户数据（管网 GDB、DTM、热点表、客户 22 个模型、Mussafah 模型、Parsons 交付…）在不在、docker 镜像与 Python 依赖齐不齐、某个情景的每一步做完没有（= tools/c1_pipeline.py check）。只读。",
               "Whether the client data, docker image and Python packages needed for hotspot 1D/2D runs are present, and which pipeline steps of a case are done (= tools/c1_pipeline.py check). Read-only.",
               (Pm("tag", "enum", "情景", "case", choices=("R61", "R96", "M61", "D24", "U61")),),
               intents=("数据齐", "缺数据", "缺什么", "能不能复现", "复现", "环境", "readiness", "reproduce"), examples=("换台机器能复现 C1 吗？缺什么？",), owner="qa"),
    ActionType("qa_results", "结果质检", "Result QA",
               "某个情景每个态的计算结果质检：算完（帧数够）、一维连续性误差 < 2 %、一二维交换对账差 < 0.2 %、L2+suggested 比 L2+planned 变深的面积（v3 应接近 0）。给每项的值和标准。只读。",
               "QA of each state of a case: finished (frames), 1D continuity error < 2 %, 1D-2D exchange balance < 0.2 %, L2+suggested side effects vs L2+planned. Values and criteria for each check. Read-only.",
               (Pm("tag", "enum", "情景", "case", required=True, choices=("R61", "R96", "M61", "D24", "U61")), Pm("state", "enum", "态", "state", choices=("L1", "L2", "L2P", "L2S", "L2S3"))),
               intents=("质检", "连续性", "对账", "水量平衡", "副作用", "过没过", "可信吗", "qa", "continuity", "balance"), examples=("模型版结果质检过了吗？", "R61 v3 有没有副作用"), owner="qa"),
)
AT = {a.api: a for a in ACTIONS}


# ---------------------------------------------------------------- 导出
def to_dict() -> dict:
    """本体全貌（给 /api/ontology 与文档）。"""
    return {
        "core": "FloodPoint",
        "object_types": [{"api": o.api, "zh": o.zh, "en": o.en, "pk": o.pk, "title": o.title, "desc": o.desc, "scoped": o.scoped, "loader": o.loader,
                          "properties": [{"name": p.name, "type": p.type, "zh": p.zh, "en": p.en, "desc": p.desc, "derived_by": p.derived_by, "source": p.source} for p in o.properties]} for o in OBJECT_TYPES],
        "link_types": [l.__dict__ for l in LINK_TYPES],
        "rules": [{k: v for k, v in r.__dict__.items() if k != "fn"} for r in RULES],
        "actions": [{"api": a.api, "zh": a.zh, "en": a.en, "desc_zh": a.desc_zh, "desc_en": a.desc_en, "side_effect": a.side_effect, "preconditions": list(a.preconditions), "intents": list(a.intents), "examples": list(a.examples), "ui": a.ui,
                     "params": [{"name": p.name, "type": p.type, "zh": p.zh, "en": p.en, "required": p.required, "choices": list(p.choices) if p.choices else None, "lo": p.lo, "hi": p.hi, "default": p.default} for p in a.params]} for a in ACTIONS],
    }


def mermaid() -> str:
    """关系图（文档用）。"""
    lines = ["graph LR"]
    for o in OBJECT_TYPES:
        lines.append(f'  {o.api}["{o.zh}<br/>{o.api}"]')
    for l in LINK_TYPES:
        arrow = "-->" if l.card == "one" else "-.->"
        lines.append(f"  {l.src} {arrow}|{l.zh}| {l.dst}")
    lines.append("  classDef core fill:#fde68a,stroke:#b45309,stroke-width:2px;")
    lines.append("  class FloodPoint core;")
    return "\n".join(lines)


def check() -> list[str]:
    """声明自检：主键 / 标题属性存在、关系两端存在、规则与动作引用的类型与属性存在、api 唯一。"""
    errs = []
    apis = [o.api for o in OBJECT_TYPES] + [l.api for l in LINK_TYPES] + [r.api for r in RULES] + [a.api for a in ACTIONS]
    dup = {x for x in apis if apis.count(x) > 1}
    if dup:
        errs.append(f"api 重名：{sorted(dup)}")
    for o in OBJECT_TYPES:
        if o.prop(o.pk) is None:
            errs.append(f"{o.api}: 主键 {o.pk} 不在属性里")
        if o.prop(o.title) is None:
            errs.append(f"{o.api}: 标题属性 {o.title} 不在属性里")
        for p in o.properties:
            if p.derived_by and p.derived_by not in RL and p.derived_by not in ("has_state", "zone_points"):
                errs.append(f"{o.api}.{p.name}: derived_by {p.derived_by} 不是规则")
    for l in LINK_TYPES:
        for side in (l.src, l.dst):
            if side not in OT:
                errs.append(f"关系 {l.api}: {side} 不是对象类型")
    for r in RULES:
        if r.applies_to not in OT:
            errs.append(f"规则 {r.api}: applies_to {r.applies_to} 不存在")
    for a in ACTIONS:
        for p in a.params:
            if p.type == "enum" and not p.choices:
                errs.append(f"动作 {a.api}.{p.name}: enum 没有 choices")
            if p.default is not None and p.choices and p.default not in p.choices:
                errs.append(f"动作 {a.api}.{p.name}: 默认值不在 choices")
            if p.lo is not None and p.hi is not None and p.lo > p.hi:
                errs.append(f"动作 {a.api}.{p.name}: lo {p.lo} > hi {p.hi}")
    errs += check_runtime()
    return errs


def check_runtime() -> list[str]:
    """声明与实现对不对得上。2026-09-23 之前这三样都不查，所以自检永远绿：
    Scenario / Reach / Zone 的 loader 名根本不存在，而它们会经 /api/ontology 给客户看到。"""
    errs = []
    try:
        from .runtime import Runtime
    except Exception as ex:                       # 循环 import 或语法错时不要把自检带崩
        return [f"装载 Runtime 失败：{type(ex).__name__}: {ex}"]
    for o in OBJECT_TYPES:
        if o.loader and o.loader != INLINE and not hasattr(Runtime, o.loader):
            errs.append(f"{o.api}: loader {o.loader} 在 Runtime 上不存在")
    for a in ACTIONS:
        if not hasattr(Runtime, "_act_" + a.api):
            errs.append(f"动作 {a.api}: 缺实现 _act_{a.api}")
        if not hasattr(Runtime, "_sum_" + a.api):
            errs.append(f"动作 {a.api}: 缺小结 _sum_{a.api}")
    return errs
