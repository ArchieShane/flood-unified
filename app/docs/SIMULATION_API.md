# 仿真模块接口文档

> 路由：`backend/src/stormwater/api/simulations.py`（前缀 `/api/simulations`，Swagger 标签 `simulations`）
> 业务逻辑：`simulation.py`（任务读写）、`sim_results.py`（结果图层）、`sim_ponding.py`（溢水点）、
> `sim_residual.py`（残留积水归因）、`sim_effect.py`（多态方案效果）、`sim_mock.py`（模拟进度）；
> 拟建调蓄池图层也在 `sim_results.py`
> 样例响应取自 2026-09-27 的本地库：#101（全市 61 mm / 2 h、250 m、单一交付）与 #191（Mussafah 四态交付）。

---

## 0. 通用约定

| 项 | 约定 |
|---|---|
| Base URL | `http://127.0.0.1:8101`；前端经 Vite 代理走 `/api` |
| 登录态 | **无**。「我的」靠 `?user=<操作人缩写>`（如 `ZN`），写操作靠表单的 `created_by` |
| 语言 | `?lang=zh|en` 或 `Accept-Language`，默认中文。只翻：错误信息、防线 / 资源名、方案态名、成因展示文案、溢水点名 |
| 协议值（不翻） | `status`、`rain_source`、`defence_line_key`、`tab`、`kind`、`mechanism_key`、残留分类 `class`、`deviations` 各 key、态 `key` |
| 时间 | 返回一律带时区的 ISO 8601，按阿布扎比本地时间（`+04:00`）；`model_start_utc` 入参不带时区按 UTC 理解 |
| 数据库 | 只写 `syq-pipe` 的 `simulation` schema（`search_path` 锁死），表结构见 `sql/simulation_schema.sql` |
| 错误体 | `{"detail": "<按语言翻好的一句话>"}`；参数校验失败为 FastAPI 标准 422（`detail` 是数组） |
| 多态任务 | 一个任务涵盖几种工况（如 Mussafah 的 `L1 / L2 / L2+A / L2+B`）。结果图层接口认 `?state=<态 key>`，不给取默认态；**单一交付给了 `state` 会 404** |

### 0.1 任务状态机

```
draft ──submit──▶ queued ──(算力侧)──▶ running ──(算力侧)──▶ done
  │                 │  └──────(算力侧)──▶ failed / cancelled   └──▶ failed / cancelled
  └──cancel──▶ cancelled ◀──cancel──┘
```

| 操作 | 允许的当前状态 | 否则 |
|---|---|---|
| 改草稿 `PUT` / 提交 `submit` | `draft` | 409 只有草稿可以修改或删除 |
| 删除 `DELETE` | `draft` `done` `failed` `cancelled` | 409 排队中或正在计算的任务不能删除，请先取消 |
| 取消 `cancel` | `draft` `queued` | 409 只有草稿或排队中的任务可以取消 |
| 回写状态 `PATCH status` | `queued`→`running/cancelled/failed`；`running`→`done/failed/cancelled` | 409 任务状态不能这样流转 |
| 追加分析 / 回写结果 | `done` | 409 只有已完成的任务可以追加分析 |

> 算力侧未接入前，`sim_mock.py` 在 API 进程里后台替它推进 `queued → running → done`（单机串行、每个任务默认 10 min），
> 只推状态不编结果。接真实算力侧时必须设 `SIM_MOCK_PROGRESS=0`。

### 0.2 三道防线

`defence_line_key` 取 `line1`（现状永久管网）→ `line2_current`（第二道·现状临时措施）→ `line2_planned`（第二道·规划，视同建成）。
**累加口径**：第 k 道的任务带上前 k 道的资源；第一道不能勾第二道的资源（422）。

---

## 1. 接口总览

| # | 方法 | 路径 | 用途 | 调用方 |
|---|---|---|---|---|
| 1 | GET | `/api/simulations/options` | 向导各步的选项与默认值 | 前端 |
| 2 | GET | `/api/simulations/summary` | 首页：KPI、同场雨对比、队列、New results | 前端 |
| 3 | GET | `/api/simulations` | 任务列表（按标签筛） | 前端 |
| 4 | POST | `/api/simulations/estimate` | 确认页的预计耗时 / 就绪时间（不落库） | 前端 |
| 5 | POST | `/api/simulations` | 存草稿；`submit=true` 存完直接提交 | 前端 |
| 6 | GET | `/api/simulations/{id}` | 单个任务详情 | 前端 |
| 7 | PUT | `/api/simulations/{id}` | 改草稿（整表单覆盖） | 前端 |
| 8 | DELETE | `/api/simulations/{id}` | 删草稿或已结束的任务 | 前端 |
| 9 | POST | `/api/simulations/{id}/submit` | 草稿 → 排队 | 前端 |
| 10 | POST | `/api/simulations/{id}/cancel` | 取消草稿 / 排队中的任务 | 前端 |
| 11 | PATCH | `/api/simulations/{id}/status` | 回写状态 / 进度 / 起止时间 | **算力侧** |
| 12 | PUT | `/api/simulations/{id}/result` | 回写结果指标 | **算力侧** |
| 13 | POST | `/api/simulations/{id}/analyses` | 完成后追加分析 | 前端 |
| 14 | POST | `/api/simulations/{id}/open` | 记首次打开时间 | 前端 |
| 15 | GET | `/api/simulations/{id}/layers` | 结果图层元数据 | 前端 |
| 16 | GET | `/api/simulations/{id}/layers/max-depth` | 逐格最大水深 GeoJSON | 前端 |
| 17 | GET | `/api/simulations/{id}/layers/frames/{index}` | 第 index 帧水深（紧凑数组） | 前端 |
| 18 | GET | `/api/simulations/{id}/layers/inflow` | 模型实际用的总入流过程线 | 前端 |
| 19 | GET | `/api/simulations/{id}/layers/ponding` | 重算的易积水片区（溢水点） | 前端 |
| 20 | GET | `/api/simulations/{id}/layers/residual` | 演算末残留积水逐块归因 | 前端 |
| 21 | GET | `/api/simulations/{id}/states/effect` | 多态任务逐时刻的方案效果 | 前端 |
| 22 | GET | `/api/simulations/layers/proposed-ponds` | 拟建调蓄池多边形（与任务无关） | 前端 |

15–20 认 `?state=`；21 不认（它本身就是跨态比较）；22 与任务无关，也不认。

---

## 2. 目录与首页

### 2.1 `GET /api/simulations/options` — 向导选项

无参数（除 `lang`）。

```jsonc
{
  "rain_sources": [
    {"key": "design_storm", "available": true},
    {"key": "observed_replay", "available": true},
    {"key": "ncm_forecast", "available": false}      // 路线图，提交会 422
  ],
  "storm_presets": [                                   // 9 格：3 雨量 × 3 历时
    {"key": "33mm_2h", "depth_mm": 33.0, "duration_h": 2.0,
     "exact_depth_mm": null,                          // 设计基准格有实际雨深（61.3），以它为准
     "label": "33 mm in 2 h", "is_design_basis": false}
  ],
  "storm_depths_mm": [33.0, 61.0, 96.0],
  "storm_durations_h": [24.0, 2.0, 1.0],
  "defence_lines": [
    {"key": "line1", "label": "第一道防线", "subtitle": "Existing permanent network", "badge": null,
     "resources": [{"id": 1, "key": "pipelines", "label": "管道", "quantity": 449581}]}
  ],
  "users": [{"id": 1, "initials": "ZN", "display_name": null}],
  "choices": {                                          // 各下拉框的取值
    "temporal_pattern": ["Zone B official DDF alternating-block (2022)"],
    "spatial_distribution": ["Uniform citywide"],
    "extent": [{"extent": "City-wide", "grid_m": 250, "terrain": "5 m DTM"}],
    "tide": [{"tide_label": "HAT 1.3 m NADD", "tide_level_m": 1.3, "tide_datum": "NADD", "gate_close_above_m": 1.3}],
    "after_rain_mode": ["until_drained", "fixed"]
  },
  "defaults": { /* 与表单同名字段的默认值，见 §3.1；resource_ids 默认勾第一道全部资源 */ }
}
```

### 2.2 `GET /api/simulations/summary` — 首页

```jsonc
{
  "runs": {"total": 14, "done": 14, "running": 0, "queued": 0, "failed": 0},   // total 只数 queued/running/done
  "storms": {
    "total": 9,                                  // 雨型格子总数
    "modelled": [{"key": "33mm_2h", "depth_mm": 33.0, "duration_h": 2.0}],   // 已有完成任务的格子
    "pending_count": 0,                          // 还没完成、但在跑/排队的格子数
    "pending_by": null                           // 这些格子最晚何时出结果
  },
  "new_results": {                               // 本周完成且从未点开过（opened_at 为空）
    "count": 6,
    "items": [{
      "id": 2, "rain_label": "61 mm in 1 h", "storm_preset_key": "61mm_1h", "depth_mm": 61.0, "duration_h": 1.0,
      "rain_source": "design_storm", "defence_line_key": "line1", "defence_line_label": "第一道防线",
      "started_at": "...", "ended_at": "...", "created_by": "AL",
      "result": null,                            // run_result 行（去掉 run_id / updated_at）
      "analyses": [{"kind": "validation", "gap_hours": null}],
      "baseline_still_pond": null                // 同雨型、上一道防线最近一次的 still pond 数
    }]
  },
  "validation": null,        // 最近一次有命中率的验证：{id, event, hit_rate, missed, false_alarms, ended_at, previous}
  "same_storm": [            // 每雨型 × 每道防线最近一次完成任务的 still pond 数；防线齐全的排前
    {"storm_preset_key": "61mm_2h", "depth_mm": 61, "duration_h": 2,
     "lines": [{"defence_line_key": "line1", "still_pond_count": 141, "run_id": 3}]}   // 示意：当前库里 still pond 均未回写，此数组为空
  ],
  "queue": [                 // 甘特：running 按实际开始；queued 在同一算力上接着串行排
    {"id": 9, "status": "queued"   /* 示意：当前库里无排队任务 */, "rain_label": "...", "storm_preset_key": "...", "depth_mm": 61, "duration_h": 2,
     "rain_source": "design_storm", "defence_line_key": "line1", "progress_pct": 0,
     "start": "...", "end": null}        // 没有预计耗时就不给 end，前端只画起点
  ]
}
```

---

## 3. 任务 CRUD 与状态流转

### 3.1 表单 `SimulationForm`（POST / PUT / estimate 共用）

| 字段 | 类型 | 必填 | 约束 / 说明 |
|---|---|---|---|
| **① 降雨情景** ||||
| `rain_source` | enum | | `design_storm`（默认）/ `observed_replay` / `ncm_forecast`（422） |
| `storm_preset_key` | string | 条件 | 设计暴雨二选一：给预设（如 `61mm_2h`），雨量 / 历时以预设为准 |
| `depth_mm` | number | 条件 | 自定义暴雨：`0 < x ≤ 1000`，须与 `duration_h` 同给 |
| `duration_h` | number | 条件 | `0 < x ≤ 72` |
| `replay_event_ref` | string | 条件 | `observed_replay` 时必填 |
| `temporal_pattern` | string | | 雨型 |
| `peak_position_pct` | number | | `0–100` |
| `spatial_distribution` | string | | |
| **② 范围与边界** ||||
| `extent` | string | ✔ | 非空，如 `City-wide` |
| `grid_m` | number | ✔ | `> 0` |
| `terrain` | string | | |
| `tide_level_m` / `tide_datum` / `tide_label` | | | 潮位边界 |
| `gate_close_above_m` | number | | 闸门关闭水位 |
| `model_start_utc` | datetime | | 不带时区按 UTC |
| `after_rain_mode` | enum | | `until_drained` / `fixed` |
| `after_rain_max_h` | number | | `0–240` |
| `baseflow_pct` | number | | `0–100` |
| `baseflow_min_dia_mm` | int | | `≥ 0` |
| `baseflow_note` | string | | |
| **③ 防线** ||||
| `defence_line_key` | enum | | `line1`（默认）/ `line2_current` / `line2_planned` |
| `queue_all_lines` | bool | | true = 提交时三道各排一个（Queue all three） |
| `resource_ids` | int[] | | 只能属于所选防线及之前；`queue_all_lines=true` 时三道都可勾 |
| **④ 确认** ||||
| `compute_target` | string | | 缺省 `H100 pair` |
| `created_by` | string | | 操作人缩写，须在 `users` 里 |

服务端自动补：`rain_label`（列表显示名，预设显示名义雨量如 `61 mm in 2 h`）、`est_run_count` / `est_runtime_h` / `est_ready_at`。

### 3.2 `POST /api/simulations/estimate` — 预计耗时（不落库）

Body：`SimulationForm`（只用到 `extent`、`grid_m`、`queue_all_lines`、`compute_target`）。

```json
{"est_run_count": 3, "est_runtime_h": 4.5, "compute_target": "H100 pair",
 "est_ready_at": "2026-09-28T01:21:40+04:00", "basis_runs": 10}
```

- `est_runtime_h` = 同范围、同网格已完成任务的**中位**耗时；没有同口径历史（`basis_runs = 0`）时为 `null`，不拿别的网格顶替。
- `est_ready_at` = 此刻 + 耗时 × 任务数（串行）。

### 3.3 `POST /api/simulations` — 新建（→ 201）

Body：`SimulationForm` + `submit: bool`（默认 false；true = 确认页的「Run」，存完直接提交）。
返回：§3.4 的任务详情 + `queued_ids: int[]`（未提交为 `[]`）。

### 3.4 `GET /api/simulations/{id}` — 任务详情

返回 `run` 表整行（含 §3.1 全部表单字段）外加：

```jsonc
{
  "id": 191, "status": "done", "progress_pct": 100.0, "batch_id": null,
  "rain_label": "61 mm in 2 h", "defence_line_label": "第二道防线（规划）",
  "est_run_count": 4, "est_runtime_h": 8.3, "est_ready_at": null,
  "started_at": "2026-09-26T13:51:34+04:00", "ended_at": "2026-09-26T22:07:11+04:00",
  "ended_is_estimate": false,          // true = ended_at 是预计值
  "opened_at": "...", "created_at": "...", "updated_at": "...",
  "created_by": "ZN",                  // 操作人缩写（不是 id）
  "variant_label": "4 态：L1 / L2 / L2+A / L2+B",
  "has_layers": true,                  // 有无结果图层；交付目录路径不外露
  "states": [                          // 多态任务才有，单一交付为 null
    {"key": "L2", "label": "L2 已建成调蓄（≡ L1）", "defence_line_key": "line2_current",
     "defence_line_label": "第二道防线（现状）", "same_as": "L1"}     // same_as：与哪一态完全相同
  ],
  "state_default": "L1",
  "resources": [{"id": 1, "defence_line_key": "line1", "key": "pipelines", "label": "管道", "quantity": 449581}],
  "resource_ids": [1, 2],
  "analyses": [{"id": 5, "kind": "gap", "gap_hours": 6}],
  "result": {                          // 未回写为 null；字段见 §3.10
    "run_id": 191, "overflow_node_count": 671, "still_pond_count": null, "...": "..."
  },
  "line_runs": [                       // 详情页右上角的防线切换：同一场雨在三道防线上各取一个任务
    {"defence_line_key": "line1", "label": "第一道防线", "run_id": null, "status": null, "has_layers": false}
  ]
}
```

`line_runs` 匹配规则：先认同批 `batch_id`；再找同降雨（预设键 / 自定义雨深+历时 / 回放事件号）、同范围、同网格的任务；
不含草稿 / 取消 / 失败；有图层的优先，其次已完成、再按最近。找不到的那道 `run_id = null`，不拿别的雨顶替。

### 3.5 `GET /api/simulations` — 任务列表

| 参数 | 默认 | 说明 |
|---|---|---|
| `tab` | `all` | `all`（非草稿）/ `running` / `done` / `queued` / `replays`（实测回放）/ `mine` / `drafts` |
| `user` | | 操作人缩写；`tab=mine` 时必填（不给则一条不返，不回落成全部），同时决定 `counts.mine` |
| `limit` | 50 | 1–500 |
| `offset` | 0 | |

```jsonc
{
  "tab": "all", "total": 14,
  "counts": {"all": 14, "running": 0, "done": 14, "queued": 0, "replays": 0, "mine": 0, "drafts": 1},
  "items": [{
    "id": 191, "status": "done", "progress_pct": 100.0, "batch_id": null,
    "rain_source": "design_storm", "rain_label": "61 mm in 2 h", "storm_preset_key": "61mm_2h",
    "depth_mm": 61.0, "duration_h": 2.0,
    "defence_line_key": "line2_planned", "defence_line_label": "第二道防线（规划）", "variant_label": "...",
    "extent": "Mussafah_00", "grid_m": 15.0, "has_layers": true,
    "started_at": "...", "ended_at": "...", "ended_is_estimate": false, "opened_at": "...",
    "created_by": null, "created_at": "...",
    "analyses": [{"kind": "validation", "gap_hours": null}],
    "states": ["L1", "L2", "L2+A", "L2+B"]      // 多态任务的态 key；列表的防线列据此写「全部防线」
  }]
}
```

排序：`coalesce(started_at, created_at)` 倒序。

### 3.6 `PUT /api/simulations/{id}` — 改草稿

Body：`SimulationForm` 全量（**整表单覆盖，不做字段级合并**）。返回任务详情。仅 `draft`。

### 3.7 `DELETE /api/simulations/{id}` — 删除（→ 204）

仅 `draft` / `done` / `failed` / `cancelled`。资源勾选、分析、结果指标随外键级联删除；交付目录只读，不动（重新登记即可找回）。

### 3.8 `POST /api/simulations/{id}/submit` — 提交

草稿 → `queued`。返回任务详情 + `queued_ids`（本草稿在最前）。
`queue_all_lines = true` 时按三道防线各排一个，共用新 `batch_id`，其余两个复制本草稿的雨情与边界；
每个任务取勾选集中属于本道及之前各道的资源，**某道一项没勾的按整道全选**。

### 3.9 `POST /api/simulations/{id}/cancel` — 取消

`draft` / `queued` → `cancelled`。返回任务详情。

### 3.10 `PATCH /api/simulations/{id}/status` — 算力侧回写状态

```json
{"status": "running", "progress_pct": 35, "started_at": "...", "ended_at": "...", "ended_is_estimate": true}
```

全部可选。`status` 取 `running` / `done` / `failed` / `cancelled`，只许按 §0.1 流转；不给 `status` 即只更新进度。
- 转 `running` 且从未开始过、又没给 `started_at` → 记此刻；
- 转 `done` → `progress_pct` 缺省 100、`ended_at` 缺省此刻、`ended_is_estimate` 强制 false。

返回任务详情。

### 3.11 `PUT /api/simulations/{id}/result` — 算力侧回写结果指标

仅 `done`。**整行覆盖**（没给的字段置空）。返回任务详情。

| 字段 | 类型 | 说明 |
|---|---|---|
| `still_pond_count` | int ≥ 0 | 仍积水点数 |
| `drained_volume_m3` | number ≥ 0 | 排水量 |
| `overflow_node_count` | int ≥ 0 | 管井溢出点数（登记交付时取 SWMM Node Flooding Summary 行数） |
| `ponding_duration_h` | number ≥ 0 | 积水时长 |
| `validation_event` | string | 验证事件 |
| `validation_hit_rate_pct` | 0–100 | 命中率 |
| `validation_missed` / `validation_false` | int ≥ 0 | 漏报 / 误报数 |

> 登记进来的交付只回写成果里直接有的（目前只有 `overflow_node_count`），其余留空，不拿格子数顶替。

### 3.12 `POST /api/simulations/{id}/analyses` — 追加分析（→ 201）

```json
{"kind": "gap", "gap_hours": 6}
```

`kind`：`validation` / `gap` / `erp`。`gap` 必须给 `gap_hours`（0–240），其余不许给（否则 422）。仅 `done`；重复追加静默忽略。返回任务详情。

### 3.13 `POST /api/simulations/{id}/open` — 记打开

点「Open ›」时调用，只记第一次打开时间（`opened_at`），首页 New results 据此消掉。任何状态可调。返回任务详情。

---

## 4. 结果图层（只读，需 `has_layers = true`）

交付目录登记后挂在任务的 `run.output_dir`（相对 `backend/data/`，不外露）。没登记的任务、越界路径都返 404「这个任务没有结果图层」。

### 4.1 `GET /{id}/layers` — 图层元数据

参数：`state`（可选）。

```jsonc
{
  "run_ref": "abu-dhabi-citywide-coupled-61mm-2h-250m-one-way-land-masked-74h",
  "solver": "ANUGA 2D",
  "status": "partial_completed_last_window_missing",
  "claim_boundary": "Partial one-way SWMM→ANUGA result: ...",   // 页面须原样标出（诊断性、未标定）
  "domain": {"bounds_epsg32640": [...], "area_m2": ..., "cell_size_m": 250.0, "active_land_cells": 16761,
             "simulation_duration_minutes": 4435.0, "output_step_minutes": 5.0, "...": "..."},
  "results": {"maximum_depth_m": 0.908, "inundated_cells_ge_0_01m": 3537, "inundated_area_ge_0_01m2": ...,
              "final_surface_volume_m3": ..., "...": "..."},
  "coupling": {"mode": "one_way_swmm_to_anuga", "quality_passed": false, "window_count": 887, "...": "..."},
  "model_configuration": {"rainfall_total_mm": 61.0, "rainfall_duration_minutes": 120, "model_cell_size_m": 250.0, "...": "..."},
  "delivery": {"completed_window_count": 887, "expected_window_count": 888, "missing_tail_seconds": 300.0,
               "complete_recession_verified": false},          // v2 交付才有，v1 为 null
  "admission": {"partial_result_view_allowed": true, "engineering_prediction_claim_allowed": false},
  "partial": true,            // 缺末尾窗口：页面要标「退水未验证」
  "has_inflow": false,        // true = 有逐井入流过程线，详情页画 /layers/inflow 而不是派生雨型
  "bounds": [[24.2767, 54.3094], [24.6078, 54.7628]],   // [[南, 西], [北, 东]]，Leaflet fitBounds 直接用
  "cell_count": 3537,
  "frames": [{"index": 0, "time_minutes": 5.0}]          // 时间轴：帧号 + 自起算分钟
}
```

`domain` / `results` / `coupling` / `model_configuration` 是交付摘要原样透传（v1 / v2 已由 `sim_delivery.load_summary` 归一），字段随交付版本略有出入。

### 4.2 `GET /{id}/layers/max-depth` — 逐格最大水深

参数：`state`。`Content-Type: application/geo+json`，原文件直出（全市 250 m 约 0.5 MB）。**几何只在这里给一次**，帧 / 溢水点 / 残留块都按 `cell_id` 引用它。

```jsonc
{"type": "FeatureCollection", "features": [{
  "type": "Feature",
  "geometry": {"type": "Polygon", "coordinates": [[[54.6814, 24.6078], "..."]]},   // WGS84 方格
  "properties": {"cell_id": 339, "depth_m": 0.0199, "maximum_depth_m": 0.0199,
                 "maximum_depth_time_seconds": 18600.0, "maximum_depth_time_minutes": 310.0,
                 "final_depth_m": 0.00013, "model_cell_size_m": 250.0, "inundation_threshold_m": 0.01}
}]}
```

### 4.3 `GET /{id}/layers/frames/{index}` — 单帧水深

参数：`state`。`index` 取自 `/layers` 的 `frames[].index`，不存在 404「没有这一帧」。
响应头 `Cache-Control: private, max-age=3600`（成果只读，帧不会变）。

```json
{"index": 12, "time_minutes": 65.0, "cells": [3671, 3857, "..."], "depth_m": [0.01, 0.013, "..."]}
```

`cells` 与 `depth_m` 等长，只列水深 ≥ 出图阈值（0.01 m）的格，水深保留 3 位小数。单帧约 8–20 KB。

### 4.4 `GET /{id}/layers/inflow` — 总入流过程线

参数：`state`。只有 `has_inflow = true` 的交付（`sim_sww` 抽的四态交付）有，否则 404。
内容是逐井 `[INFLOWS]` 累加的 5 min 平均流量与累计量：

```json
{
  "schema": "flood_management.sww_state_delivery.v1",
  "source": "L1_耦合一维模型.inp [INFLOWS]",
  "node_count": 645, "flow_units": "LPS", "step_min": 5,
  "t_min": [0.0, 5.0, "..."], "q_mean_m3s": [0.0, 0.0, "..."], "cum_volume_m3": [0.0, 0.0, "..."],
  "total_volume_m3": 34802.7, "peak_m3s": 20.512, "peak_time_min": 67.0,
  "first_inflow_min": 51.0, "last_inflow_min": 496.0,
  "rainfall_total_mm": 61.0, "rainfall_duration_minutes": 120.0,
  "catchment_area_ha": 82.0, "equivalent_depth_mm": 42.4
}
```

### 4.5 `GET /{id}/layers/ponding` — 易积水片区（溢水点）

参数：`state`。按 `docs/analysis_ponding/算法与成果说明.docx` 的七步从成果**重算**（`sim_ponding.py`），首次现算后缓存到
`data/processed/sim_ponding/`（全市 250 m 首次约十几秒）。算不出返 422「这份成果算不出溢水点」。

```jsonc
{
  "algo_version": 2,
  "run_ref": "...", "claim_boundary": "...",
  "inputs": {"cell_size_m": 250.0, "grid": [147, 186], "frames": 887, "frame_step_min": 5.0,
             "sim_end_min": 4435.0, "rainfall_mm": 61.0, "valid_cells": 16761, "depressions": 158},
  "deviations": ["terrain_from_sww", "swmm_from_rpt", "no_speed", "no_surface_rain",
                 "coarse_grid", "partial_run", "no_recession"],     // 偏离文档口径之处，页面须原样列出
  "criteria": {"select_max_depth_m": 0.3, "select_dur_depth_m": 0.15, "select_dur_h": 3.0},
  "summary": {"selected_cells": 314, "cluster_count": 102, "area_m2": 19625000.0,
              "by_mechanism": {"coastal_lowland": 101, "surface_depression": 1}},
  "clusters": [{
    "rank": 1, "score": 71.2,                              // 五因子积水指数，按它排名
    "mechanism_key": "coastal_lowland",                   // 协议值
    "mechanism": "滨海低地滞水",                            // 展示文案，随语言
    "name": "…附近1523米的溢水点",                          // 按最近 POI 起名，随语言
    "poi": {"objectid": 986, "name": "...", "type": "cemetery", "distance_m": 1523},
    "lat": 24.329791, "lon": 54.727114, "x": 269375.0, "y": 2692625.0,   // 代表点（最深格），x/y 为 EPSG:32640
    "cell_id": 23233, "cells": [23046, 23047, "..."], "cell_count": 4, "area_m2": 250000.0,
    "max_depth_m": 0.908, "mean_max_depth_m": 0.749, "area_ge_0_30m_m2": 250000.0,
    "peak_time_min": 4430.0,
    "dur_ge_0_10m_h": 72.58, "dur_ge_0_15m_h": 72.42, "dur_ge_0_30m_h": 71.67,
    "depth_6h_m": 0.836, "depth_12h_m": 0.895, "depth_24h_m": 0.905, "depth_end_m": 0.908,
    "drain_time_h": 73.92, "mean_speed_ms": null,          // 无流速输入时为 null（no_speed）
    "elev_min_m": 33.47, "sink_depth_m": 4.98, "basin_capacity_m3": 13597133.6, "basin_area_m2": 6312500.0,
    "swmm_inflow_m3": 2913.0, "rain_on_cluster_m3": 15250.0,
    "drained": false,                                     // 演算末前 40 min 以前已退干
    "recession_h": null                                   // 退水时长 = 退干时刻 − 峰现时刻；未退干为 null
  }],
  "recession": {"edges_h": [2.0, 4.0, 8.0], "counts": [0, 0, 0, 1], "undrained": 101, "median_h": 13.84}
}
```

| 字段 | 说明 |
|---|---|
| 入选判据 | 最大水深 ≥ 0.30 m **或** ≥ 0.15 m 持续 ≥ 3 h 的格八连通成片 |
| `mechanism_key` | `deep_sink` 地形深坑/构筑物凹陷 · `network_overflow` 管网溢流主导 · `coastal_lowland` 滨海低地滞水 · `surface_depression` 地表汇水洼地 · `other` |
| `deviations` | `terrain_from_sww` 高程取自 .sww · `land_mask_inferred` 陆域掩膜推断 · `swmm_from_rpt` 管网注入取自报告 · `no_speed` 无流速（指数去掉滞流度）· `no_surface_rain` 地表不直接降雨 · `coarse_grid` 网格 > 20 m（深坑规则不会触发）· `no_recession` 末帧水量 ≥ 峰值 90% · `partial_run` 交付缺末尾窗口 |
| `recession` | 已退干片区的退水时长直方图：`counts` 比 `edges_h` 多一格（最后一格为 > 末档）；`undrained` 单列；`median_h` 只在已退干的片区里取 |
| `cells` | 格子号，前端拿 max-depth 同号方格画范围，不另传几何 |

### 4.6 `GET /{id}/layers/residual` — 演算末残留积水归因

参数：`state`。只认 `sim_sww` 抽出的四态交付（要有 swmm_input / swmm_report / terrain_grid.npz），其余 422「这份成果拆不出残留积水的成因」。
末帧水深 ≥ 0.01 m 的格八连通成块，逐块说明「为什么退不掉」。

```jsonc
{
  "summary": {
    "end_minutes": 300.0, "trend_from_minutes": 240.0,        // 末帧时刻；「最后 1 h 变化」的起点
    "surface_volume_m3": 5470.4, "pond_count": 70, "pond_volume_m3": 5110.0,
    "by_class": {"no_inlet": {"count": 54, "volume_m3": 3592.8},
                 "below_rim": {"count": 10, "volume_m3": 786.2},
                 "not_taking": {"count": 6, "volume_m3": 731.0}},
    "overflow_m3": 9268.7,             // 一维 → 二维 溢出总量
    "return_m3": 3798.3,               // 二维 → 一维 回灌（耦合记录）
    "returned_back_calc_m3": 3749.0,   // 回灌（按报告反算，用于核对）
    "boundary_out_m3": 0.0,            // 边界出流（二维计算域无出流边界，≈0）
    "node_count": 861, "returning_node_count": 50,
    "storage_fill_pct": {"NP1": 23.6, "RD_34": 4.9}   // 各调蓄设施最大充满度，排除「容量不够」
  },
  "params": {"wet_m": 0.01, "rim_margin_m": 0.02, "trend_min": 60.0},
  "ponds": [{
    "rank": 1, "class": "no_inlet",
    "volume_m3": 601.6, "volume_before_m3": 589.9,          // 末帧 / 1 h 前的块水量
    "area_m2": 7425.0, "max_depth_m": 0.172, "floor_m": 2.88, "surface_m": 3.11,
    "inlets": [],                                           // 块内的井
    "nearest_inlet": {"id": "CB4498", "distance_m": 56, "rim_m": 3.89},
    "nearest_returning_inlet": {"id": "CB4632", "distance_m": 135},
    "nearest_storage": {"id": "RD_34", "distance_m": 83, "max_fill_pct": 4.9},
    "cell": 39110, "cells": [38452, 39108, "..."],
    "x": 248792.25, "y": 2695062.25, "lon": 54.523978, "lat": 24.34861,
    "poi": {"name": "The Armenian Church Of Abu Dhabi", "distance_m": 209}   // POI 原名，不翻
  }]
}
```

`class`（协议值，页面翻）：
- `no_inlet`：洼地里没有井；
- `below_rim`：块内有井，但最高水面仍低于最低井口（差 ≤ 2 cm 也算）；
- `not_taking`：水面高过井口 2 cm 以上，井却不收——耦合规则问题，需问交付方。

### 4.7 `GET /{id}/states/effect` — 多态方案效果

不认 `state`。非多态任务 422「这个任务不是多态成果，没有方案效果可比」。
口径：基线 = 第一态；溢水点清单 = 基线的 ponding 片区，其它态**不另识别**，用同一批格子号去各态**同一时刻**的水深里取数，逐态对基线做减法。
（不用退水时间或演算全程判据：各态演算时长不同，全程判据会造假消除。）

```jsonc
{
  "base": "L1",
  "checkpoints_h": [2.0, 4.0, 5.0],     // 自降雨开始的时刻档
  "default_h": 2.0,                     // = 三道防线共用时限 T
  "active_depth_m": 0.15,               // 片区最深格 ≥ 此值 = 此刻仍积水
  "wet_m": 0.01,                        // 地表积水量的计入阈值
  "points": [                           // 基线溢水点（顺序即各数组的下标顺序）
    {"rank": 1, "name": "…附近450米的溢水点", "lat": 24.349057, "lon": 54.526482, "cell_id": 38734, "max_depth_m": 0.559}
  ],
  "states": [{
    "key": "L2+B", "label": "L2+B 本系统箱涵", "defence_line_key": "line2_planned",
    "sim_end_minutes": 300.0,
    "checkpoints": [{
      "t_h": 2.0, "reached": true, "time_minutes": 120.0,     // reached=false 时下列字段全缺，不拿别的时刻顶替
      "surface_volume_m3": 5486.0,          // 此刻全域地表积水量
      "points_volume_m3": 1206.8,           // 此刻基线各片区积水量之和
      "active": [true, false, "..."],       // 与 points 等长
      "volume_m3": [214.6, 10.0, "..."],
      "depth_m": [0.294, 0.044, "..."],
      "volume_less_m3": 5875.9, "volume_less_pct": 51.7,      // 比基线少上地表的水
      "base_active_count": 9, "active_count": 5,
      "cleared": [5, 6, 9, 10],             // 基线此刻积水、本态不积水的点 rank
      "added": []                           // 反过来的（方案把水挤到别处）
    }],
    "outside_points": []                    // 本态自识别、与基线片区不相交的新片区（全程口径）
  }]
}
```

#191 实测（2 h 档）：`L2+A` 少 3.6%，`L2+B` 少 51.7% 且消除 4 个点；`L2` 与 `L1` 完全相同（`same_as: "L1"`）。

### 4.8 `GET /layers/proposed-ponds` — 拟建调蓄池

完整路径 `/api/simulations/layers/proposed-ponds`，**不带任务 id**，也不认 `state`、不要求 `has_layers`；详情页按需叠加。
路由注册在 `/{id}` 之前，不会被当成任务号。

数据源 `backend/data/temp/proposed_ponds.geojson`（EPSG:32640），服务端逐环转成 WGS84（坐标保留 7 位小数）；
只收 `Polygon` / `MultiPolygon`，其它几何跳过。按文件修改时间缓存，**换文件不用重启**。文件不在时 404「没有拟建调蓄池数据」。

```jsonc
{
  "type": "FeatureCollection",
  "crs_note": "EPSG:4326（由 32640 转出）",
  "features": [{
    "type": "Feature",
    "geometry": {"type": "Polygon", "coordinates": [[[54.5236269, 24.3477864], "..."]]},   // WGS84 [经, 纬]
    "properties": {                          // 原样透传，不翻译、不改名
      "pond": "PN-1",
      "cluster_flood_m3_2026": 3208,         // 所在片区 2026 事件积水量
      "flooded_structures": 69,
      "design_volume_m3": 3000, "peak_stored_m3": 2212,
      "top_side_m": 49.1, "top_area_m2": 2415, "depth_m": 1.5, "side_slope_hv": "3:1", "invert_m": 2.41,
      "main_intake": "CB4483", "distance_to_intake_m": 54, "intakes": 8,
      "connecting_pipe_dn_mm": 600, "connecting_pipe_m": 844,
      "centre_utm40": [248730.4, 2694996.2], // 注意：这一项仍是 UTM 40N，没转
      "site": "open land: no plot, no building, ...",
      "note": "..."                          // 可选，现只有 PN-5 有
    }
  }]
}
```

当前文件 6 个池（PN-1 … PN-6），设计容积合计 8,900 m³。属性字段随上游文件走，前端取不到的字段按缺省处理。

---

## 5. 错误码一览

| HTTP | 中文 `detail` | English | 触发 |
|---|---|---|---|
| 404 | 没有这个仿真任务 | Simulation not found | id 不存在 |
| 404 | 这个任务没有结果图层 | This run has no result layers | 未登记交付 / 该交付缺这一输出（如 inflow） |
| 404 | 这份成果没有这个方案态 | This run has no such scenario state | `state` 不在清单里，或单一交付给了 `state` |
| 404 | 没有这一帧 | No such frame | 帧号越界 |
| 404 | 没有拟建调蓄池数据 | No proposed pond data | `proposed_ponds.geojson` 不在 |
| 409 | 只有草稿可以修改或删除 | Only drafts can be edited or deleted | PUT / submit 非草稿 |
| 409 | 排队中或正在计算的任务不能删除，请先取消 | Queued or running runs cannot be deleted — cancel them first | DELETE queued/running |
| 409 | 只有草稿或排队中的任务可以取消 | Only drafts or queued runs can be cancelled | cancel |
| 409 | 任务状态不能这样流转 | This status change is not allowed | PATCH status |
| 409 | 只有已完成的任务可以追加分析 | Analyses can only be added to completed runs | analyses / result 非 done |
| 422 | NCM 预报降雨尚未接入（路线图） | NCM forecast rainfall is not available yet (roadmap) | `rain_source=ncm_forecast` |
| 422 | 设计暴雨需选一个预设，或同时给出自定义雨量与历时 | … | 设计暴雨缺雨量 |
| 422 | 实测回放需指明回放的降雨事件 | An observed event replay needs the rainfall event to replay | 缺 `replay_event_ref` |
| 422 | 未知的雨型预设 | Unknown storm preset | |
| 422 | 未知的防线 | Unknown line of defence | |
| 422 | 有资源编号不存在 | One or more resource ids do not exist | |
| 422 | 勾选了所选防线之后的资源（第一道不能带第二道的资源） | … | |
| 422 | 未知的操作人 | Unknown user | `created_by` / `user` 不在 `app_user` |
| 422 | Gap 分析要给小时数，Validation 不给 | A gap analysis needs hours; a validation does not | |
| 422 | 这份成果算不出溢水点 | Overflow points cannot be derived from this run's outputs | ponding |
| 422 | 这份成果拆不出残留积水的成因 | Residual ponding cannot be broken down for this run's outputs | residual |
| 422 | 这个任务不是多态成果，没有方案效果可比 | This run has no scheme states to compare | effect |

---

## 附：登记交付（命令行，非 HTTP）

结果图层来自算力侧交付目录，须先登记为已完成任务（按 `output_dir` 去重，可重复执行）：

```bash
cd backend && PYTHONPATH=src .venv/bin/python -m stormwater.sim_results import data/temp/<交付目录> --by ZN
```

多态交付（如 Mussafah 四态）由 `sim_sww.py` 从 `.sww` 抽取并写 `states.json` 后登记；溢水点缓存可用
`python -m stormwater.sim_ponding <run_id>` 预热。

---

## 附：服务器一二维交付的接入（`sim_c1`，2026-10-12）

服务器（C1 / Mussafah / Yas / Al Bateen / Bani Yas）的成果按下面的约定读。登记命令不变：

```bash
cd backend && PYTHONPATH=src .venv/bin/python -m stormwater.sim_c1 import <交付目录> --by <缩写>   # 只抽不登记用 extract
```

| 项 | 约定 |
|---|---|
| 目录名 | `<片区…>_<雨深>mm<历时>h[_<网格>m \| _G<网格>]`，如 `C1_61mm2h`、`C1_模型版_61mm2h_5m`、`YAS_61mm24h_G5`（`sim_sww.parse_delivery_name`） |
| 网格 | 取各态耦合运行记录的 `res_m`（各态不一致报错；记录没写则取目录名里标的，再退回 15 m）。登记的 `grid_m` = 这个模型网格 |
| 抽取结果键 | `sim_deliveries/<片区>_<雨深>mm<历时>h[_<n>m]/`：网格不是 15 m 才加后缀，已抽好的 `C1_61mm2h` 不改名；同雨量的 15 m 与 5 m 因此并存、互不覆盖（任务靠 `grid_m` 区分） |
| 落图格网 | `max(res_m, 15 m)`：5 m 模型也落 15 m 格（逐帧 geojson 按 5 m 落图会大 9 倍），`domain.cell_size_m` 记落图格，`model_configuration.model_cell_size_m` 记模型网格 |
| 态 | 按目录实有的态读（前缀 `L1_` `L2_` `L2+planned` `L2+suggested`），缺的不报错；同一态有两个目录报错而不是悄悄挑一个。`L2+suggested_v3_按塘余量分配` 按前缀命中 |
| 态名 | C1 用 `STATES` 里已翻译的固定名；别的片区取目录名前缀后面的那段（不替交付方编名字，英文界面里不翻） |
| 设施GIS | 先找 `<态目录>/设施GIS/`，没有再退回旧样例的 `<交付>/../planned\|sugg/设施GIS` |
| 帧 | 24 h 雨模拟 28 h、5 min 一帧 = 336 帧。`frames / uh / vh`（帧 × 三角单元，float16）从 zip 里分块流式读（每块 ≤ 192 MB），不再整块解出；分块与整块的结果逐位相同（`test_sim_c1_fake.py`） |
| `._` 文件 | macOS 打包带的 `._*` 伴生文件一律忽略（样例里 `耦合的一维/` 有，原先会让 `*.rpt` 数出 2 个） |

**体量评估**（C1 样例 15 m 落图格，L1 单态逐帧 geojson 219 MB / 72 帧 ≈ 3.0 MB/帧）：336 帧约 1 GB/态，四态一份交付约 4 GB（积水范围更大会更多）；
5 m 的 npz 约 800 万三角单元，分块读峰值内存估算 ≈ 1.3 GB（`n_tri × 90 B + 3 × 192 MB`），整块读 `frames+uh+vh` 要十几 GB。
逐帧 geojson 的格式（`{type, geometry: null, properties}` 逐格一个 Feature）没改；要压体量，下一步是改成紧凑数组并同步 `sim_results` / `sim_ponding` / `sim_residual` 三个读者。

### 已知口径差异（不是 bug）

- **系统储水能力**：交付没有 `analysis/state_stats.csv` 时，系统退回按 `.inp` 自算，数偏小（不含检查井），与客户口径不同。
- **页面上的「溢水点」**是系统按自己的口径（`sim_ponding` 七步）从成果重新识别的，不等于服务器统计里的热点。
- 计算域面积由三角单元面积求和得到（C1 约 42.7 km²），不再沿用文案里写死的 42.8。

### 新区域与 Mussafah 15 m

- Yas / Al Bateen / Bani Yas 只有 L1、L2 两态，`extract` / `import` 按实有态读。C1 才有的局限文案（ADM-90 等）只在 C1 写；别的片区写「口径见交付的计算设置.md」。
  态与防线的对应沿用 `L1 → 第一道`、`L2 → 第二道（现状）`，**L2 实际是什么措施要对交付的说明核一遍**，不对再改 `STATES`。
- **热点（积水点）每个片区都读**（`hotspots.json`，`/api/simulations/{id}/hotspots`）。逐态数取交付的统计值表：C1 是 `统计值/C1统计值.xlsx`，
  Mussafah 模型版与 Yas / Al Bateen / Bani Yas 是 `对比_缺口分析/统计值.csv`（区域 / 口径 / 指标 / 各态；区域名 `ADM-86` 不补零，并集行叫 `N 个热点`，`全域` 行与水量对账行单列）。
  坐标取台账 `hotspots_845.csv` 按 `ADM-<补零编号>` 对（`hotspot_id_raw` 在 ADM / AAM / DRM 间会重复，不能只用数字）；没有台账时退回交付的 `公共/热点.geojson`。
  汇水区面只有 C1 交付带，别的片区没有，此时只出「全域 / 离热点 200 m」口径（页面的口径标签自动少「热点汇水区」，积水点表里汇水区那几列显示「—」）。
  已知编号：Yas ADM-003 / 085 / 490 / 007；Al Bateen ADM-075（距域边 9 m）/ 076 / 377 / 379 / 380；Bani Yas ADM-049；Mussafah ADM-086 / 087 / 014 / 189（**未做落点验证**）。
- **Mussafah 模型版与 Yas / Al Bateen / Bani Yas 的交付（`ms_post.py` / `rg_post.py`）已接入**（2026-10-13）。它们的形状和 C1 不同：
  态目录下是 `一维/`（`<名>_<态记号>.inp`、`<名>_<态记号>_一维报告.rpt`）与 `二维/`（`<态记号>_耦合运行记录.json` + 一个 `.sww`），交付里**没有 npz**；
  npz 在 NAS 的 `<主机>/work/c2d/<名>_<态记号>_2d.npz`（态记号 L1 / L2 / L2P / L2S3 = L2+planned / L2+suggested）。
  `sim_c1.state_files` 同时认两种形状，npz 缺了就去 `work/c2d` 找：候选名依次是运行记录的 `tag`、交付里 `.inp` 的文件名、`*_<态记号>_2d.npz` 唯一匹配；
  多个匹配或一个都没有就报错，不悄悄挑。`work/c2d` 默认从交付目录往上找 `<主机>/work/c2d`，也可 `--c2d <目录>` 指定。
  同一套也兜住了 C1 以 `--no_big` 导出（`一二维/` 里没有 npz）的情形。
  这两种交付**地表不直接受雨**（运行记录 `acc_m3.rain = 0`，来水全是井口溢流）：摘要里不再写「离井 > 46 m 直接受雨」，`sim_ponding` 照记 `no_surface_rain`；
  C1 特有的局限（ADM-90 等）也只在 C1 写。范围名取目录名去掉口径标记（`模型版` `客户单元` `设计暴雨`）：`Mussafah_00_模型版_61mm2h` → `Mussafah_00`。
  **文件命名是照服务器脚本推的，没见过真实 NAS**：登记前先 `sim_probe` 看「work/c2d（按 … 找到）」那一行，对不上就把摘要发回来再调。

### 内网自检

```bash
python -m stormwater.sim_probe <交付目录 | /media/pop-fs/flood_data/result/<主机名>> --depth 4 --out 摘要.txt
```
登记统一用 `python -m stormwater.sim_import <交付目录> --by ZN`：按交付形状自动选 `sim_sww`（旧 .sww 交付，如 `Mussafah_00_61mm2h`）/ `sim_city`（`全市_…`）/ `sim_c1`（其余一二维 npz 交付）；`--kind` 只打印判定；`--c2d <目录>` 显式指定 npz 所在的 `work/c2d`。名字不是「片区_雨深mm历时h[_网格m]」形状的目录（如 `.bak-…` 备份）一律不识别。

只读、只依赖 numpy，不输出任何数据本体：目录清单、各 npz 的键 / shape / dtype / 帧数 / t_h 首末与步长、运行记录的 `res_m` 与 `end_h`、
`设施GIS` 与统计值文件有无、与抽取代码要求逐条对照的「可抽取 / 不可抽取」结论，以及上面两条口径差异。
