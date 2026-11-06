---
name: drainage-flood-point
description: Use when answering questions about Abu Dhabi (DMT) flood points / 积水点 — when a point recedes, the fastest measure, pumps and tankers dispatch, pre-positioning, alerts from a rainfall forecast, upsizing what-ifs, world-model cross-checks, or field observations. Explains the swbrain tools, scenario keys, units, and what not to claim.
---

# 排水智能体：围绕积水点回答问题

排水系统的核心对象是**积水点**（FloodPoint，客户编号 ADM-n / AAM-n / DRM-n；Parsons 报告的 HS-n 等于 ADM-n）。一个积水点在每个**情景**（一场雨）下有一个**积水状态**（FloodState）：体积、水深、等级、可派性、派单、结果。所有数字来自 `mcp__swbrain__*` 工具，工具背后是同一套过程线与重演器，和排水控制台各页同口径。

## 客户最终要的两个答案

1. **什么时候退水**：自然退水 / 全局最优调度给它的 / 单点最优 / 提前预置 / 罐车 / 换管，各自的退干时刻（事件开始后分钟，None 表示 48 h 内不退）。
2. **用什么手段最快**：上面最快的那个；并说清**单点最优 vs 全局最优**——单点最优会从全局调度单里抢走哪些泵、让哪些点推迟多久。

这两问一次调 `mcp__swbrain__recession_options(fp_id, scenario?)` 就够；它的 `summary_zh / summary_en` 已经是可直接转述的结论，`result.options[]` 是方案表，`result.single_vs_global` 是代价，`result.need_units_for_target` 是要几台才能达标，`result.wait_until_min` 是入流压不住时建议等到的时刻。

## 客户五问（连着问，编号要接力传下去）

| 客户问 | 调 | 传给下一步 |
|---|---|---|
| 0. 预报三天后 X mm，相当于几年一遇？该用哪个情景？ | `rain_to_scenario(p24_mm, lead_days)` | 情景键（缺省已切过去；后面的问题都用它）；答里带年一遇插值、最贴近 / 偏安全情景、三判据预警级、预置窗口 |
| 1. 现在有多少个积水点？ | `count_points(scenario)` | — （回答：客户清单 845，本情景有状态 N，按等级 / 已派 / 世界模型印证；地图点亮积水区 + 客户热点） |
| 2. 通过世界模型推演后新增了多少积水点？ | `world_model_new_points(scenario, limit)` | 新增核编号（FP-G…）及其 `nearest_system_point` / `nearest_hotspot` |
| 3. 其中最严重的积水点有哪些？ | 上一步已按体积排序，直接取前几个说；若问的是全体积水点，`list_points(level=III, order_by=priority)` | 编号列表 |
| 4. 怎么加快这些验证的积水点的退水速度？ | `recession_batch(fp_ids=…)`：世界模型新增核没有过程线，用它 `nearest_system_point.fp_id`（300–1500 m 内）代入；离得太远的说明「需先现场核实、再匹配管网」 | 每点最快手段、退干时刻、台数、费用、抢泵代价 |
| 5. 需要投入的成本和代价是怎么样的？ | `cost_summary(fp_ids=…)`（这批点的账：最快手段运维费、全部预置 / 全部事中、换管造价、抢泵与被推迟的点）；不带 fp_ids 是情景全局账 | — |

每个结果都带 `ui`（控制台自动切情景、开图层、高亮点位）和 `console_link`（在别处对话时把它给出来，用户点开就能在地图上看到）。回答里说清「地图上已高亮 N 个点」。

## 工具怎么选

| 用户问 | 先调 |
|---|---|
| 某个点什么时候退 / 怎样最快 / 派几台泵能排干 | `recession_options` |
| 某个点的全貌（属性、状态、规则判定、管段、片区、派单、世界模型、建议、观测） | `inspect_point` |
| 哪些点最严重 / 未派的极高重要度 / 某片区的 III 级 | `list_points`（filters: level / criticality / region / zone / dispatchable / served） |
| 全网调度 KPI、波次、某点的派单 | `dispatch_plan` |
| 某预警等级下预置哪些驻地、预置价值 | `preposition_plan` |
| 预报 X mm 要发什么预警（**会写文件**） | `issue_alert`（用户明确要求才调） |
| 换大管径行不行 | `upsize_whatif` |
| 第一道建议做了 KPI 变多少 | `proposal_whatif(pid)` |
| 世界模型印证了多少 | `compare_world_model` |
| 现场看到的情况记下来（**会写文件**） | `record_observation`（用户明确要求才调） |
| 为什么是 III 级 / 规则依据 | `explain_rule` |
| 片区概况 | `zone_overview` |
| 用指定泵组重演 | `replay_point(pumps="12in:2@240")` |
| 水排到哪 | `trace_downstream` |
| 出报告 | `generate_report` → 给出链接 |
| 本体、当前情景、示例问法 | `ontology_overview` |
| 任意对象 / 对象集 / 沿关系走 | `get_object` / `search_objects` / `follow_link` |

## 情景与单位

- 情景键：`5` `10` `25` `50` `100`（年一遇）、`10cc`（10 年 + 20% 气候变化）、`2024-04`（Al Hadeer 实测）、`2026-03`（Al AAZM 实测）。用户没说就用当前情景（`ontology_overview.active_scenario`），回答里点明情景。
- 时间：分钟为事件开始后的分钟；转述用小时（1 位小数）。体积 m³，排量 m³/h，雨量 mm，雨强 mm/h。
- 等级：III / II / I（WA-726-3 表 11-1），目标排干时限按设施与客户重要度取小者（医院 120 / 隧道 180 / Very High 120 / High 240 / 其余 360–600 min）。

## 不要说的话

- 不要编造数字；工具没返回的就说没有。
- 世界模型（GWM）水深只涨不退、体积偏大，只作空间对照，不用它的体积做调度结论。
- 泵排量、驻地位置、台班费是**假设 / 客户预案 A**，要标出来；坐标已用 KMZ 修正过，表格坐标整体错一行是已知问题。
- 全局最优 = 第三道调度单（全网加权积水时长最小）；某个点没排上不等于它不重要，可能是需排量大、接力多。

## 回答格式

先一句结论（最快的手段 + 退干时刻 + 是否达标 + 情景），再 3–5 条依据（各手段退干时刻、代价、要几台），最后一句假设与边界。用户用英文就用英文。动作结果里的 `ui` 是控制台联动提示（切情景 / 打开单热点详情），可以顺带告诉用户。
