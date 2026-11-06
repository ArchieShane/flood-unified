# 智能体运行时：DeepSeek Harness 接入（2026-09-21）

> 用 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（dsh，「everything is a plugin」的 agent 运行时）把排水智能体从「规则规划器」变成真正的智能体：模型循环、工具调用、会话、技能、审批、Web UI 都由 dsh 提供；排水系统这边只提供**本体**——以积水点为核心的动作与对象，通过 MCP 接进去。

## 1. 架构

```
用户 ──► dsh Web UI (3080)  ─┐                      ┌─► swbrain.mcp_server（stdio，薄桥）──► 排水智能体 HTTP 服务 (8766)
用户 ──► 排水控制台「智能体」抽屉 ──► /api/agent ──► dsh sdk profile（Python SDK，JSON-RPC） ┘        ontology.runtime → 本体动作 / 对象 / 规则
                                                        │
                                                        └─► 模型路由：deepseek-official（DEEPSEEK_API_KEY）或 openai / anthropic（llm-pi-ai，settings.yaml）
```

- **一套本体，两个入口**：dsh 的 Web UI 是完整的交互入口（流式回答、每次工具调用可视、多会话、计划 / 待办、审批、技能）；排水控制台的抽屉是同一个 agent 的轻量入口，答案带控制台联动（切情景、打开单热点详情）。
- **MCP 服务器是薄桥**：`swbrain/mcp_server.py` 不装载管网状态，所有调用转发到运行中的 HTTP 服务，数字与控制台各页同源、同缓存；服务器 `instructions` 由本体声明自动生成，进入系统提示。
- **预设（agent preset）**：`agent/dsh/presets/drainage/agent.cordis.yml` 是 dsh 的 agent 面组合——人设、`dsh-mcp-client` 行（swbrain）、`drainage-flood-point` 技能、只读文件工具、上下文压缩。宿主面（模型路由、持久化、沙箱、审批）用 dsh 自带的 web / sdk profile。
- **规划器顺序**：`dsh → llm（Anthropic 直连）→ rules（关键词）`，前者不可用自动回落并在回答里注明。三者能做的动作完全一样，都记入 `out/ontology_actions.jsonl`。

## 2. 安装与启动

```powershell
pip install deepseek-harness-sdk          # 自带 dsh 运行时（Windows x64 / Linux / macOS）：供 sdk / 无头用，无需 Node
npm i -g @deepseek-ai/dsh                 # Web UI 要 npm 版（SDK 运行时没打包 Web 前端）；需要 Node ≥ 22.19
$env:OPENAI_API_KEY = "..."               # 或 $env:DEEPSEEK_API_KEY；密钥只在环境变量
python -m swbrain.serve --port 8766       # 排水智能体 HTTP 服务（本体、MCP 桥的后端）
python -m swbrain.dsh_bridge --render     # 渲染预设 / 补丁 / settings.yaml 到 out/dsh_home（幂等）
python -m swbrain.dsh_bridge --web        # 启动 dsh Web UI，http://127.0.0.1:3080，默认预设 = 排水智能体
python -m swbrain.dsh_bridge --ask "ADM-62 什么时候能退水，怎样最快"   # 无头一问（sdk profile）
python -m swbrain.mcp_server --selftest --key <口令>                 # MCP 桥自测
```

配置 `[agent.dsh]`：`enabled`、`home`（SDK）、`home_web`（Web UI）、`provider` / `model`（缺省按密钥自动选：deepseek-official/deepseek-flash 或 openai/gpt-5.1）、`web_port`、`initialize_timeout_s`。

## 3. 文件

| 路径 | 作用 |
|---|---|
| `swbrain/mcp_server.py` | MCP stdio 服务器：20 个工具（16 个本体动作 + get_object / search_objects / follow_link / ontology_overview），instructions 由本体生成 |
| `swbrain/dsh_bridge.py` | 渲染模板到 `$DSH_HOME`；启动 Web；`DshBridge` 经 Python SDK 驱动 sdk profile；`StepCollector` 把工具事件转成控制台步骤 |
| `agent/dsh/presets/drainage/` | 预设：`preset.yml`、`agent.cordis.yml`、`skills/drainage-flood-point/SKILL.md` |
| `agent/dsh/web.patch.yml` / `sdk.patch.yml` | Web / sdk 配置树的覆盖层（默认模型、默认预设；sdk 的人设 + MCP + 技能） |
| `agent/dsh/settings.yaml` | `$DSH_HOME/settings.yaml` 初始版本（模型路由；只在不存在时写） |
| `scripts/dsh_web.ps1` | 一键启动 Web UI |
| `out/dsh_home/` | SDK 用的 Harness 主目录（控制台抽屉 / 无头一问的会话与运行时包桩；不入库） |
| `out/dsh_home_web/` | Web UI 用的 Harness 主目录（`[agent.dsh].home_web`）。与 SDK 分开：SDK 运行时写的会话在 Web UI 里打不开、它写的包桩会让预设装不上 |

## 4. 真机验证（2026-09-21，openai/gpt-5.1，经 sdk profile）

问「ADM-62 什么时候能退水，怎样最快？」：agent 先加载 `drainage-flood-point` 技能，再调 `mcp__swbrain__recession_options(fp_id=ADM-62, scenario=25)`，27 s 内给出六种手段的退干时刻、单点最优对全局的代价（抢走 PA-085、ADM-156 推迟 1.6 h）、要几台泵达标、最快手段（预置 3 台 12 寸泵，5.3 h 退干），并标出假设。第二问「ADM-3 在 50 年一遇下派几台泵」10 s。

**2026-09-22 真机（deepseek-official/deepseek-flash，`DEEPSEEK_API_KEY` 在用户级环境变量）**：控制台入口首问 82 s（含运行时启动），skill → count_points → list_points 三步、10 个地图高亮；Harness Web UI 新会话 5 s 出完整回答（世界模型新增 95 核、前 10 表、口径说明）。换模型前建的旧会话会钉在旧模型上，要点「新会话」。

## 5. 换模型

- DeepSeek 官方：环境变量 `DEEPSEEK_API_KEY`，`[agent.dsh] provider="deepseek-official" model="deepseek-v4-pro"`（或 Web UI Settings → Models 里填 key）。
- OpenAI / Anthropic / 自建 OpenAI 兼容网关：`out/dsh_home/settings.yaml` 的 `llm-pi-ai.providers`（见 dsh 文档 providers.md），`[agent.dsh] provider/model` 指到该路由。

### 5.1 「明明设了 DEEPSEEK_API_KEY，页面却显示 gpt-5.1」（2026-09-23 排查）

- 原因：`setx` 写的是注册表里的用户级变量，只有之后**新开**的进程才带它。从一个早就开着的终端（Claude Code 的工具 shell、旧的 PowerShell 窗口、任务计划）再拉起 `swbrain.serve`，进程环境里没有这个变量，`dsh_bridge.settings()` 就按「有 OPENAI_API_KEY」选了 openai/gpt-5.1，控制台抽屉与渲染出的 `out/dsh_home/patches/sdk.patch.yml` 都跟着写 gpt-5.1。
- 修法：`swbrain/envkeys.py`——密钥先看进程环境，没有就查用户级 / 机器级注册表（HKCU\Environment、HKLM\…\Session Manager\Environment），找到写回 `os.environ` 让 dsh 子进程继承；`dsh_bridge.settings()`、`DshBridge._start()` 的子进程环境、`traffic.api_key()`（Google 路况密钥）都走它。密钥不落盘、不进日志。
- 老会话：Harness Web UI 里在换模型之前建的会话钉在 gpt-5.1，新建会话才是 deepseek-flash；控制台抽屉重启服务后即是 deepseek。
- 密钥的持久位置就是**用户级环境变量**（`setx DEEPSEEK_API_KEY …`，注册表 HKCU\Environment），不进仓库、不进 config.toml、不落文件。查在哪一层：`python -m swbrain.dsh_bridge --keys`（只报来源不打印值）；`/api/ontology` 的 `dsh.key_source` 同义。`scripts/dsh_web.ps1` 启动前也会把用户级密钥补进进程环境。

## 6. 边界

- dsh 处于 developer preview，会有不兼容变更；本接入依赖的行 id / 配置键（`agent-default-model`、`agent-presets`、`system-prompt.personaPrefix`、`skill-filesystem.customSkillDirs`、`dsh-mcp-client`）以 0.1.5 为准，升级后跑 `dsh --profile web --dump-default-config` 核对。
- 首次问到某个情景要等它的调度上下文（预热没完时 40–110 s），MCP 工具超时设为 5 分钟。
- Web UI 与控制台抽屉是两套会话；抽屉按页面会话 id 保留上下文。
- 用户预设放在 `$DSH_HOME/.agent-presets/drainage/`，dsh 0.1.5 从那里解析不到 `@deepseek-ai/dsh-*`（报 `agent-preset/invalid … Cannot find package '@deepseek-ai/dsh-persona'`，表现为 Web UI 里选工作区 / 新建会话没反应）。`dsh_bridge.render()` 会在 `$DSH_HOME/node_modules` 建一个目录联接指到 npm 版 dsh 的真实包目录来解决；注意 `$DSH_HOME/profiles/node_modules` 里是 SDK 运行时写的桩（指向 exe 快照），npm 的 web 进程用不了。
- 局域网（2026-10-02）：dsh 只肯听 127.0.0.1（`--host 0.0.0.0` 它直接拒绝启动：能执行代码）。局域网走控制台服务里的反向代理 `swbrain/dsh_lanproxy.py`，
  听 `[agent.dsh].lan_port`（缺省 3081，0 = 不开），**只放管理员**（统一身份要 SWBRAIN.ADMIN，共享口令要管理员口令 cookie），防跨站（Origin 必须是代理自己），
  把 Host / Origin 改成 127.0.0.1:3080 再转给 dsh，控制台的 `swb_*` cookie 不交给 dsh，WebSocket 原样转发。dsh 自己的一次性 token 照样要：
  局域网管理员在控制台点「在 Harness 打开」，拿到的是 `http://10.0.0.47:3081/?token=…`；非管理员从局域网点会被告知用控制台对话。
- Web UI 的地址带一次性 token（启动日志 `out/dsh_web.log` 里 `dsh web: http://127.0.0.1:3080/?token=…`），根路径直接访问是 401；控制台「在 Harness 打开」按钮取的就是这个地址。`dsh_bridge.launch_args(kind="web")` 优先用 npm 全局 dsh，sdk 用 Python 运行时。
- **原生 Web UI 入口：服务托管 + 先登录 + 只读（2026-10-02）**。两个入口（控制台抽屉、Harness 原生 Web UI）现在都从 8766 进：
  - 控制台抽屉点「在 Harness 打开」→ `GET /api/harness`（要登录）→ 没起就 `?launch=1` 让服务把它拉起来（`dsh_bridge.WebBridge`，实测 5 s 起来）→ 返回带一次性 token 的地址，浏览器开它。手动 `python -m swbrain.dsh_bridge --web` 仍然可以。
  - **只读**：dsh 的 MCP 客户端 `headers` 是静态配置（`z.dict(String)`），按会话传不了身份；那边又只有一个一次性 token，分不出是谁在用。所以这个入口只读——`WebBridge` 在启动前把一张共享的**只读凭据**写进 `out/dsh_home_web/.delegation`（`Identity.readonly_token()`），MCP 桥带上它，服务端判成只读主体：读动作按 `SWBRAIN.VIEW` 放行，会写数据的一律拒并提示到控制台用平台账号做。共享口令模式下不写凭据文件（那时走 `?key=`）。
  - 为什么不做 `/harness/` 反向代理：Web UI 是单页应用，`<base href="/">` 与前端请求都写死根路径，挂子路径要改写 HTML 与前端包。这里改成「登录后才把地址交出去」，token 只在服务端内存过一手。
  - 局限：这只解决了「谁能进、能做什么」，没解决「记成谁」——原生 Web UI 里的动作记 `sdip:harness-web`，控制台里的记真实主体号。要记真实主体号，得等 dsh 支持按会话传 MCP 请求头。

- **浏览器连接上限（2026-09-22 排查「Web UI 卡死、选了工作区也没法输入」）**：Chrome 对同一主机的 HTTP/1.1 连接上限是 6 条，而 Web UI 每个标签页常驻 5～6 条流式长连接（`$events`、`session/control`、`session/follow`、`workspace/follow`、`workspaceFiles/changes`，再加 HMR 的 `/plugins/events`）。占满后所有普通请求（建会话、发消息）排队不返回，页面看起来像卡死；服务端其实是好的（命令行直接调 `session/create` 只要 0.5 s）。`agent/dsh/web.patch.yml` 把 `client-hmr`、`ui-sidebar-files` 关掉，把每页常驻连接降到 4 条以内；**同一浏览器只开一个 Harness 标签页**，多开一个就又满了。若一定要两个页面，用不同主机名（`127.0.0.1` / `localhost` / 局域网 IP 各是一个连接池，token 登录对三者都放行）。
- **工作目录**：预设的工作区是 `$DSH_HOME_WEB/workspace`（README + 四份文档副本），由 `render()` 预先登记进 `storages/workspace.json`，打开页面就带着「排水智能体」工作区，不用走系统目录选择框；不要把仓库根设成工作区（`out/` 几十 GB、`node_modules` 联接，文件树会扫很久）。
- **模型账户**：Harness 自己不会退回规则规划器；模型报 `PI_AI_ERROR`（如 OpenAI `credit_balance_exhausted`）时 Web UI 显示「本轮运行失败」。控制台入口的 `Agent` 会把这类失败当成 dsh 不可用，退回 llm / rules 规划器继续答（`DshBridge.chat` 把「没回复也没调工具」当失败抛出）。
- **路况 / 鲁棒 / 外部源（2026-09-22 夜）**：动作 `traffic_status(refresh, depart_at)` 说现在行程按什么放大（Google Routes 实时 / 预测，或没密钥时的模拟时段曲线）、各驻地拥堵系数、Google 时对当前调度单的逐对精算；`dispatch_plan(robust=true)` 给一张同时对 10cc / 25 / 50 年的鲁棒单并逐情景与单情景的单对照；`feeds_status()` 说 993 报案 / check-in / 桥梁台账 / 承包商边界四个源各自是拉还是推、有没有数据。三者都验过 deepseek-flash 会正确选动作并把「没密钥所以是模拟」「接口就绪但还没喂数」说清楚。
- **生命周期层（2026-09-23）**：`point_history`（这个点历史上几场雨积过没积过、模型对不对、复发率、治理状态）、`event_review`（复盘 2024-04 / 2026-03：POD / FAR / 漏报 / 误报）、`lifecycle_overview`、`open_event` / `update_episode` / `close_point` / `set_proposal_status`。历史两场的逐点真实记录客户还没给，回答里会明说「推演估计」。
- 内测声明弹窗每个主目录只弹一次（dsh 记在 `settings.yaml` 的 `ui-onboarding.welcomeNoticeVersion`），点「继续」即可。

## 7. 客户五问与地图互动

控制台右侧对话框是「对话 + 地图」的入口：智能体每一步动作的结果都带 `ui`，控制台自动切情景、开图层、在地图上编号高亮点位、打开单热点详情；气泡里列出被标记的点，点一下定位。Harness Web UI 里问同样的问题，回答会附 `console_link`，点开就是同一张图。

| 客户问 | 智能体做 |
|---|---|
| 现在有多少个积水点？ | `count_points`：客户清单 845（ADM/AAM/DRM），本情景有积水过程的点数、按等级 / 已派 / 世界模型印证；点亮积水区 + 客户热点 |
| 通过世界模型推演后新增了多少积水点？ | `world_model_new_points`：模型独有核数、合计体积、按体积排序的前 N 个及各自最近的客户热点 / 本系统积水点；开世界模型层并高亮 |
| 其中最严重的积水点有哪些？ | 上一步的排序直接说；或 `list_points(level=III)` |
| 怎么加快这些验证的积水点的退水速度？ | `recession_batch(fp_ids)`：每点最快手段、退干时刻、达标、台数、费用、抢泵代价；高亮这些点并切到第三道 |
| 需要投入的成本和代价是怎么样的？ | `cost_summary(fp_ids)`：这批点的运维费 / 全部预置 / 全部事中 / 换管造价 / 被推迟的点；不带编号给情景全局账 |

- **预报雨量转情景（2026-09-22 起）**：值班第一句「预报三天后 X mm，相当于几年一遇、该用哪个情景」由动作 `rain_to_scenario(p24_mm, lead_days, apply)` 回答：表 3-6（B 区）两点对数插值的年一遇、最贴近的设计情景（同样近优先 10 年 + 20% CC）与偏安全一侧、可比实测事件、三判据（雨量 / 该情景 III 级点数 / 全网溢流）的预警等级、预置窗口（≥1 天开着、≥6 h 很紧、否则只能事中）；缺省顺手把控制台与后续回答切到该情景。规则规划器：带 mm 的问句只要不是「发 / 签发预警」就走它。
- **区域片区**：AAM / DRM 与没匹配到管段的 ADM 点没有管网片区，按客户清单 `area` 归并成 `Zone.kind=area`（50 个，如 Hili、Al Jimi、Madinat Zayed City），只有点的统计，没有管长 / 风险分等管网指标；`zone_overview` 对它们在地图上高亮该片区的点。注意与 ADM 管网片区同名的情况（如岛上的 Madinat Zayed 与 Al Dhafra 的 Madinat Zayed City），问的时候带全名。

- **对话框的回答怎么呈现（2026-09-22 起）**：回答正文按 Markdown 渲染（表格 / 标题 / 列表 / 链接）；每一步本体动作按类型出「结果卡片」——盘点 / 情景 / 片区出分级条形图与指标块，调度出 KPI 块 + 分诊条，预置出「预置 vs 事后」对比条 + 驻地表，退水方案出各手段退干时刻的时间条（最快的高亮、达标打勾、带费用），换管出峰值对比，预报雨量出「表 3-6 曲线 + 预报线 + 年一遇」SVG 与三判据表，成本汇总出费用块 + 逐点表；所有带编号的点、驻地行都能点：飞到地图、高亮、打开单热点详情，驻地行高亮它覆盖的点。气泡工具条有「在地图上看」（重放这条回答的地图指令）、「清除标记」、「打开报告」。卡片取数：两条规划器路径的 `steps[i].result` 都带结构化结果（规则路径 `_brief` 放宽到 40 项；Harness 路径 `StepCollector` 解析 MCP 工具结果的 `result`）。
- **回答的结构（第二轮）**：结论先行——长回答只显示「结论」小节（或前两段），其余折叠在「展开完整回答」里，切分不会切到表格中间；结果卡片紧跟结论，卡片标题可折叠；回答末尾按本轮用过的动作给 2–3 个「接着问」建议（编号、片区自动代入，问过的不再出）；等待气泡带秒表，超过 15 s 提示第一次进情景要算调度上下文；预置卡片是「雨前预置 vs 事后再派」成对条形图；退水卡片标出目标时限与宽限截止。无头 Chrome 复验方式：`chrome --headless=new --virtual-time-budget=120000 --screenshot=… --dump-dom "http://localhost:8766/?key=…&agent=1&ask=<问题>"`，DOM 里数 `.agcard / .agnext / .pairs`。
- **本体模型页签**：控制台左栏「本体模型」：以积水点为核心的 SVG 关系图（无外部依赖），点对象看属性 / 关系 / 作用于它的规则 / 相关动作，点动作旁的示例直接发给对话框；数据来自 `/api/ontology`。

- **第三道防线 v2 相关动作（2026-09-22）**：`dispatch_plan(objective, budget_aed)` 三目标与预算；`resource_check` 人 / 车 / 物资够不够；`replan(t_now_min, dry_points, failed_units, incidents)` 现场重规划（已出发锁定、退干释放、故障剔除、993 报案加权）；`stress_test(worse_scenario)` 雨大一档还差多少；`staging_status(alert_level)` 需要 vs ERP 计划 vs check-in；`recession_options(strict=true)` 单点最优严格重解。数据契约：`exchange/incidents.json`（swbrain.incidents/1）、`exchange/checkins.json`（swbrain.checkins/1），示例在 exchange/examples。

## 8. 2026-09-22 DMT 值班演练（三天后约 60 mm 强降雨，12 问连问）

以 DMT 值班工程师身份经 `/api/agent`（deepseek-flash）连问：几年一遇与情景选择 → 点数分级与片区 → 前 10 上图 → 世界模型新增 → 全局调度 → 三天预置 → AAM-36 何时退水与单点/全局差 → ADM-297 换管（英文）→ 成本汇总 → 预警等级（不真发）→ 有无实时数据（英文）→ 给领导的报告。12 问全部答出，单问 12–32 s（第 2 问 81 s），关键数字与底层动作接口逐项一致。演练记录与评估在 `out/验证_2026-09-22_DMT演练/`（不入库）。

演练修掉的问题：`hotspot_detail` / 换管 what-if / 退水换管选项以前一律按激活情景算（问 10cc 给的是 50 年的数），现在带 `key`；`/api/objects?type=Zone` 遇 NaN 片区名 500；sdk profile 继承的 shell / 后台任务 / 联网工具在 `sdk.patch.yml` 里关掉（模型曾用 pwsh+grep 翻工作区）。暴露的数据边界：AAM/DRM 无片区归属、GWM 无 61 mm 版、87 点无管网、泵与单价为假设、无实时传感器；建议加 `rain_to_scenario` 动作。

## 9. L1 / L2 / L2+ 规划结果（2026-09-28）

此前 35 个动作都是积水点与调度（第三道防线），这几天做的 L1 / L2 / L2+ 规划结果智能体一个都调不到。加了 4 个动作（`swbrain/ontology/actions/plans.py`），只读 `console/data/l2plus_catalog.json`（`tools/l2plus_catalog.py` 从 state_table / pilot_frames / city_gap / 设施文件汇总，数字与控制台「防线 2+」和在线报告同源；**结果更新后要重跑这个脚本**）：

| 动作 | 答什么 | 控制台联动 |
|---|---|---|
| `state_gap(region?)` | 某片区各态的缺口；主线每一步只和上一态比，对照态（L2+B）和父态比；不给片区就列出有结果的片区 | 切到防线 2+ 对应视图（Mussafah 为五态工作台，停在 L2+A+B、基准 L2+A） |
| `city_gap()` | 全市 L1 / L2 差值、接塘管网、冒水井增减、蓄存与排空、全市一二维进度 | 切到「全市 L1 / L2」差值图 |
| `facility_plan(region, state?)` | 某态的设施：位置、库容、标高、用地、挪动、来源（Parsons / PO 塘方案 / 本系统） | 地图上标出设施（高亮类型 `fac`，点了不当积水点开详情） |
| `as_built_next(region)` | 把 L2+A 当既成事实还要补什么、补完再降多少、两套设施的重叠 | 同 facility_plan，且 `pin`：同一轮后续查询不盖掉它的视图 / 高亮 |

- 片区：Mussafah_00（L2+A = Parsons，有报告）、29.RB2（L2+A = **PO 塘方案，来源待确认**，不叫 Parsons）、Madinat Al Riyad（只有 L1 / L2）。
- 规则规划器也认这几类问法（「还要补」「既成事实」「全市 L1 和 L2」「哪些箱涵」「五态」），积水点编号的问法不受影响。
- 真机（deepseek-flash，2026-09-28）：「Parsons 定了以后 Mussafah 还要补什么」14 s，as_built_next → facility_plan → state_gap；「29.RB2 在 PO 塘方案之上还要补什么」11 s；「全市 L1 和 L2 差多少」5 s。发现模型会把「54% 溢流在客户汇水区外」拿去解释差值小，已在数据口径里写明两者无关。
