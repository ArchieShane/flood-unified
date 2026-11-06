# 会思考的排水系统（swbrain）· 阿布扎比

对阿布扎比市（ADM）雨水管网做「控-监-测」式的智能研判：从台账与管网图算出每段管、每个汇水区、每个片区的排水状态，再给出**在什么时间、什么位置、加或调什么设施**的建议，每条带前后对比、优先级、置信度、依据与假设，等人确认。

**承诺的写法。** 这套系统不承诺「不会有内涝」，它交付三样能检验的东西：按既定标准分年实施的方案（每步带成本与预期效果）、实施后的剩余风险图（`out/residual.geojson`）、每场雨后的验证协议（`out/验证协议.md`，对账结果进 `exchange/verification/`）。汇总里那句承诺是自动生成的：「在 X 年内按目标标准把可接受深度以上的积水消除，剩余风险有图，每场雨后有对账」，X 由达标总成本除以年度预算得出，六个就绪条件全部就绪前它是目标不是保证。

**中英双语。** 页面右上角一键切换，或加 `?lang=en`。界面文字由 `console/i18n.js` 词典负责；建议标题、时间窗、依据、就绪度、承诺句、验证协议这些系统生成的内容由 `swbrain/i18n.py` 在生成时同时产出英文（`*_en` 字段），接口一并返回。英文版可信度说明在 `docs/Credibility_EN.md`，英文验证协议在 `out/Verification_Protocol_EN.md`。

**三道防线。** 第一道基础设施建设（建议、排期、达标分期、剩余风险）；第二道预报后预置（按预警等级选驻地、分泵，`dispatch.preposition`）；第三道临时应急调度（积水区分诊 → 时变路网行程 → 分派 MILP → 调度单，`dispatch.solve_dispatch`）。积水区来自世界模型（`flood_points` 契约），泵队来自 `exchange/fleet.json`，两者缺失时用 `simulate.py` 造模拟数据并在页面标记。求解器用 scipy 自带的 HiGHS。**防线 2+** 是热点群的多态一二维对比（C1 等），见下文。

**分工：本系统是优化侧（what-if）。** 预测侧（一维二维水动力模型 + 城市内涝世界模型）在另一个项目里，两边靠 `exchange/` 目录下的三种 JSON 交换：预测侧给基线状态，本系统发干预集合请求，预测侧回每个集合的 KPI。没有预测结果时本系统用自己的曼宁 + 推理公式粗算兜底。接口见 `docs/接口约定.md`。热点群的一二维水动力计算（SWMM + ANUGA）也在本仓库，结果交系统展示，也能经计算队列排队跑，见下文「一二维热点群计算（C1）」。

**智能体。** 系统带 5 个智能体（主智能体 + 调度研判 / 一二维计算 / 报告交付 / 数据质检），它们**只通过本体动作行动**：以「积水点」为核心的对象 / 关系 / 规则 / 动作声明在 `swbrain/ontology/schema.py`，每一次调用都过 `Runtime.act`（权限、校验、审计的唯一出口）。架构见 `docs/智能体架构.md` 与 `docs/系统架构设计.md`。

思路来自上海黄浦江挡潮闸《控-监-测》科研任务书，对象换成阿布扎比真正有的东西：雨水管网、进水口、出水口（拍门）、泵站、塘与渗井。

## English summary

Optimisation side of a "thinking drainage system" for Abu Dhabi's stormwater network: reads the DMT/ADM asset register (260k pipe reaches) read-only, builds a directed drainage graph, computes each reach's capacity rainfall, and proposes **when, where and which facility to add or adjust** — with cost, timing window, before/after effect, confidence and evidence. Pairs with an external 1D/2D hydrodynamic prediction model through JSON exchange files (`docs/接口约定.md`). Five ontology-driven agents (a dispatcher plus hydraulics / delivery / QA specialists) act only through ontology actions. Bilingual console (zh/en) with live recomputation. See `docs/Credibility_EN.md` for what is real data, what is assumed, and how to verify.

Quick check on a new machine, no client data needed: `python tools/c1_pipeline.py selftest` (a synthetic 800 × 600 m case runs the full 1D/2D chain in about 2 minutes and self-checks against the C1 QA criteria). 1D/2D hotspot-cluster runs (SWMM + ANUGA, four facility states, C1 as the worked example) are reproducible from the client data with `python tools/c1_pipeline.py check` / `run all --tag M61` — see the section 「一二维热点群计算（C1）」 below.

Setup: `pip install -r requirements.txt`, copy `config.example.toml` to `config.toml` and fill in database hosts and passwords, then `python -m swbrain.run` and `python -m swbrain.serve`.

## 交付方式（数据都另外提供）

- **源码方式**：`git clone` 后运行 `scripts/install.ps1`（Windows）或 `scripts/install.sh`。它依次做：建虚拟环境、按锁定版本装依赖、构建计算镜像、生成配置、自检、快速验证。
- **部署包方式**：拿到 `tools/make_release.py` 打出的包，里面有两个镜像、启动文件和说明。只要装了 Docker，运行 `start.ps1` / `start.sh` 就能起来，不用装 Python。

两种方式的完整步骤、数据怎么放、口令端口、常见问题，都在 `docs/部署与交付.md`。装好后先跑 `python -m swbrain.doctor`（安装自检），它会告诉你下面几种用法哪些已经就绪、还缺什么。

## 先看这里：四种用法，各要什么

| 用法 | 要什么 | 从哪开始 |
|---|---|---|
| **快速验证**（换机器先做这个） | Docker + Python ≥ 3.11；**不要任何客户数据、数据库或密钥** | `python tools/c1_pipeline.py selftest`：合成算例约 2 分钟跑通一二维全链路并自动判定（下一节）。也可以在对话里说「申请跑一次快速验证」，由管理端批准后执行 |
| **一二维热点群计算**（C1 等） | 上面的 + 客户数据（清单 `docs/C1_资料清单.md`）；不要数据库 | 「一二维热点群计算（C1）」一节 |
| **完整系统**（三道防线研判、控制台全部页签） | 客户 PostGIS 库（pipe / makani / liveability / flood）的只读账号 + 本地 PostGIS 输出库 | 「一分钟跑起来」一节 |
| 智能体用大模型规划 | 上面任一种 + 密钥（`DEEPSEEK_API_KEY` / OpenAI / `ANTHROPIC_API_KEY`，见 `[agent]`）+ `deepseek-harness-sdk`；没有密钥退回规则规划器，照样能用 | 「多智能体与管理端」一节 |

Python 依赖：`pip install -r requirements.txt`（下限）；要和开发机完全一致用 `pip install -r requirements.lock.txt`（钉死版本）。计算镜像版本钉在 `docker/anuga-swmm/requirements.txt`。只在 Windows 11（Git Bash / PowerShell）上验证过；Linux / Mac 没测。

## 快速验证（约 2 分钟，不要客户数据）

```bash
docker build -t anuga-swmm:1.0 docker/anuga-swmm       # 一次，约 2 分钟（要联网）
pip install -r requirements.txt
python tools/c1_pipeline.py selftest                   # = python tools/demo_case.py
```

`tools/demo_case.py` 现场合成一个 800 × 600 m 的小算例：
- **场地**：5 m 地形，西高东低，一个 1.2 m 深的洼地当热点；13 口井的管网故意偏小，东边界是自由出水口。
- **雨**：61 mm / 2 h，SCS CN 82，净雨 23.5 mm，和 C1 同一口径。
- **两个态**：L1 现状；L2P 加一座塘和一台泵。

计算用的是和 C1 **同一套**东西：
- 一维 `.inp` 用同一个函数写；
- 二维用同一个镜像里的同一个耦合程序 `couple2way_v2.py`；
- 后处理同样出 GeoTIFF 和 `.sww`。

算完按 C1 的质检标准逐项判定，全部通过时退出码为 0，判定表写在 `out/demo/verify.json`。检查项：
- 帧数够；
- 一维连续性误差 < 2 %；
- 一二维交换对账差 < 0.5 %；
- 无 NaN、水深合理；
- 一维冒水确实交给了二维；
- 塘收到水；
- L2P 热点比 L1 浅；
- GeoTIFF / `.sww` 能导出。

2026-09-30 在开发机上 14 项全部通过，计算 78 s。不起 docker 的那一半（生成模型、SWMM 一维跑完、泵起作用）在 `tests/test_demo_case.py` 里。同样的算例也是一条**可排队的任务**：在对话里说「申请跑一次快速验证」，管理端批准后由独立进程执行，跑完判定摘要挂回任务，问进度就能答「14 项过了几项」。

## 一分钟跑起来

```bash
pip install -r requirements.txt
cp config.example.toml config.toml        # 填入真实主机与口令；config.toml 不入库
python -m pytest tests -q            # 全套测试（约半分钟）
python -m swbrain.run                # 取数 → 建图 → 状态 → 建议 → 输出（约 1 分钟）
python -m swbrain.serve              # 控制台 + 当场计算 API，打开 http://127.0.0.1:8766/
#   config [serve].access_key 设了口令后：缺省监听 0.0.0.0，地址首次要带 ?key=…（或在首页输入），之后 30 天 cookie；
#   启动后在后台把全部情景（重现期、+CC、两场实测雨）预热好，切换不用等；--no-warm 关掉
```

- 源库只读：`pipe`（管网图与资产）、`makani_sync_full`（在批雨水结构、区界）、`liveability_data_20260730`（分区堵塞统计）。连接串在 `config.toml`。
- 输出：本地 PostGIS `sw_brain`（schema `swbrain`：run / edge_state / catchment_state / zone_state / proposal / plan）、`out/*.geojson|csv|json`（含 plan.csv、schedule.csv）、控制台数据、`exchange/requests/<request_id>.json`。
- 接预测侧：把基线预测放到 `exchange/predictions/`，把评估结果放到 `exchange/results/`，重跑即生效。样例在 `exchange/examples/`。
- 不写任何源库。

## 一二维热点群计算（C1）：在自己机器上复现

对一个热点群（示例 C1：ADM-21 / 90 / 94 / 104 / 192 / 202 / 486 / 487）在同一计算域、同一场雨下算几个态——L1 现状、L2 + 移动泵、L2+planned 顾问（Parsons）方案、L2+suggested 按残余积水补的措施（v2 / v3 两版）——一维 SWMM 5.2 与二维 ANUGA 双向耦合，比较积水减了多少。计算在命令行（docker）里跑，结果写成静态文件交系统 C1 页签展示；也可以在对话里让一二维计算智能体提交申请，管理员在管理端批准后执行（见「多智能体与管理端」）。

**1. 准备**

| 要什么 | 说明 |
|---|---|
| Docker | 计算镜像从仓库构建：`docker build -t anuga-swmm:1.0 docker/anuga-swmm`（约 2 分钟，要联网；ANUGA 4.0.1 + swmm-toolkit 0.17.0，版本全部钉死） |
| Python ≥ 3.11 | `pip install -r requirements.txt`（本机做建模、后处理；实测 3.14） |
| 机器 | 三个态并行时每个 6 线程，建议 ≥ 20 线程；每个情景的工作目录约 7 GB，交付目录另 3 GB，留 ≥ 30 GB |
| 工作目录 | `SWBRAIN_SWMM_WORK` 指向一个**纯英文路径**（SWMM 不认中文路径），如 `D:\swbrain_work\swmm`；缺省就是它 |
| 客户数据 | **不入库**，向客户要（清单与每项用途见 `docs/C1_资料清单.md`），把位置写进 `config.toml`：`[data]` 六项（管网 GDB 压缩包与解压版、热点工作簿目录、客户 22 个水力模型 zip、客户 SWMM 导出目录、Parsons 交付解压目录）+ `[terrain].dtm_path`（5 m DTM）+ `[hotspots].coords_kmz`（热点坐标修正 KMZ）。也可用环境变量 `SWBRAIN_DATA_<键大写>` 覆盖 |

```bash
cp config.example.toml config.toml        # 填 [data]、[terrain].dtm_path、[hotspots].coords_kmz
docker build -t anuga-swmm:1.0 docker/anuga-swmm
export SWBRAIN_SWMM_WORK=D:/swbrain_work/swmm   # Windows PowerShell：$env:SWBRAIN_SWMM_WORK='D:\swbrain_work\swmm'
python tools/c1_pipeline.py check         # 逐项检查数据、镜像、依赖，以及每一步的产物在不在（只读）
```

**2. 跑**

```bash
python tools/c1_pipeline.py plan --tag M61        # 列出全部步骤和实际命令
python tools/c1_pipeline.py run all --tag M61     # 从头到尾；已有产物的步骤自动跳过，中断后重跑接着做
```

`run all` 的顺序与大致耗时（20 线程机器）：

| 步骤 | 做什么 | 耗时 |
|---|---|---|
| `prep:*` | 客户数据 → 中间数据：热点位置、设计雨型（客户 Mussafah 模型剥掉治理措施）、全市一维重建并算一遍、8 个热点的汇水区、Parsons 设施（塘、27 座泵站、OP-02 移动泵点）、现状泵站、客户模型汇水区与模型版计算域、入库的手工记录（`cases/C1/`） | 约 1.5 h（大头是全市一维重建并 8 份并行计算，实测 70 分钟） |
| `build:R96:L1` + `compute:R96:L1` | **隐含依赖**：L2 / L2+planned 的接收井按 96 mm 下 L1 不溢流的井选（两场雨布置一致），所以先算 R96 的 L1 | 约 3.5 h |
| `build:M61` | 建 L1 / L2 / L2+planned 三个态，读入检查 | 几分钟 |
| `compute:M61:L1,L2,L2P` | 三态并行一二维计算，15 m 网格，模拟 6 h | 约 3–4.5 h |
| `design:M61` → `compute:M61:L2S3` | 按 L2+planned 的残余积水设计 L2+suggested v3，再算 | 约 4.5 h |
| `post:M61` | 统计值（每项带口径）、GeoTIFF、`.sww`、过程线、系统数据、交付目录 `out/交付/C1_模型版_61mm2h/` | 约 30 分钟 |

也可以单步跑，如 `run prep`、`run build:M61`、`run compute:M61:L1,L2,L2P`、`run post:M61`；`--dry` 只打印命令，`--force` 重做。

**情景（`--tag`）**：`M61` 模型版计算域（客户 3 个水力模型 RB3 + Musaffah_RD09 + B_C 的子汇水区并集，38.1 km²，**以此为准**）· `R61` / `R96` 原计算域（8 个热点汇水区外扩 300 m，42.8 km²）61 / 96 mm / 2 h · `U61` 客户单元版（照片描图，不能从原始数据重建，已被模型版取代）· `D24` 设计暴雨 61.34 mm / 24 h — **已作废**：那次用的还是原计算域，范围不对，2026-10-02 起系统里只保留这个情景名与一句说明，不再提供那批结果；要模型版计算域下的设计暴雨，得按 M61 建模重算（单态约 21 h）。

**两个 L2+suggested 版本**：v2（`tools/c1_design_l2s.py`，固定泵转输 + 地面取水泵）与 v3（`tools/c1_design_l2s3.py`，处数与位置同 v2，只改去向与控制：按规划塘剩余库容分配、塘水深到 0.9 m 停泵、地面取水泵直接打进塘的一维节点）。**原计算域交付以 v3 为准，v2 留作历史**（2026-10-02 用户定）：v2 在小塘周边把邻域泡深，v3 把它压下去（比 L2+planned 变深的面积：61 mm 0.5 → 0.0 ha，96 mm 0.7 → 0.04 ha）。交付目录里两版都在，`计算设置.md` 的「C12 补充」写明以 v3 为准。

**3. 看结果**

- 系统：`python -m swbrain.serve` → 「防线2+ 多态对比」→ C1。没有客户数据库（跑不了 `swbrain.run`）时也能看：`python -m http.server 8771 --bind 127.0.0.1 -d console`（只给本机看），打开 `http://127.0.0.1:8771/?r=M61`，页面直接进 C1 页签（其余页签要 `swbrain.run` 的研判数据）。
- 交付目录：`out/交付/C1_<版本>/`，含各态的一二维成果（`.sww`、`.npz`、GeoTIFF）、耦合的一维模型、统计值 xlsx、设施 GIS、`计算设置.md`、`资料清单.md`。
- 质检标准、统计口径、各态的做法和常见坑：`.claude/skills/hotspot-1d2d-states/`（在仓库目录里开 Claude Code 会自动加载这个 skill）。

**复现核验（2026-09-30）**：在干净的 git 工作副本 + 空工作目录里，从头跑了全部 10 个 `prep:*` 步骤和 `build:R96:L1`，产物与原结果对比：设计雨型、Parsons 设施、27 座泵站、OP-02 泵点、客户模型汇水区、模型版计算域、8 个热点汇水区、原计算域、现状泵站、R96 L1 的一维模型与二维设置**逐字节相同**；二维地形像素、范围、坐标系完全一致（只差文件元数据）；热点位置与共有字段相同（原文件另有 `swbrain.run` 合并的研判字段，C1 不用）。核验中发现并修掉 5 处挡路：新仓库缺 `out/` 等目录、模型版汇水区漏了前置步骤 `units_from_models.py`、热点管网汇水区依赖全市交付目录里的溢水点表（改为从全市一维 `.rpt` 取，实测 69,157 口井逐口相同）、热点汇水区依赖 flood 库导出（只用于注记重叠，改为可缺）、控制台没有数据库打不开。一二维计算本身没有在沙箱里重跑（每个态 3–4.5 h）；镜像已按 Dockerfile 重建并验证能读入 C1 模型、跑通 ANUGA 算例。

## 目录

分层与依赖关系（六层、每层的接口）见 `docs/系统架构设计.md`；本体清单见 `docs/本体模型.md`。下面只列主要的模块。

| 文件 | 层 | 做什么 |
|---|---|---|
| `swbrain/extract.py` | 测 | 只读取数：边、顶点、进水口标高、出水口、塘/渗井/泵、在批结构、区界、堵塞统计 |
| `swbrain/graph.py` | 监 | 定向（资产方向 → 标高 → 到出水口跳数）、打断环、沿流向累加汇水面积、终点归属与分类 |
| `swbrain/hydraulics.py` | 监 | 曼宁满管流、推理公式、坡度与标记、所需管径 |
| `swbrain/indicators.py` | 监 | 管段状态（承载雨强）→ 汇水区状态（蓄渗缺口、高潮净空、疑似漏登终点）→ 片区状态（风险分） |
| `swbrain/networkflood.py` · `swbrain/floodpoints.py` | 监 | 全网溢流路由；本系统自己的积水点（第三道防线的输入） |
| `swbrain/decisions.py` | 控 | 十一类建议：核实终点、核实拓扑、数据治理、扩管、调蓄/渗井、出水口增泵改闸、泵站校核、连通/消纳、雨季前维护、事件前泵位 |
| `swbrain/whatif.py` · `swbrain/standards.py` · `swbrain/verification.py` | 控 | 建议→干预集合→成本与组合、排期、按标准达标分期、剩余风险；六个就绪条件与验证协议 |
| `swbrain/dispatch.py` · `swbrain/dispatch2.py` | 控 | 第二道防线（预置最大覆盖选址）与第三道防线（处置包 + 资源 + 时变路网 + 滚动窗口 + 鲁棒目标的 MILP） |
| `swbrain/travel.py` · `swbrain/traffic.py` · `swbrain/resources.py` | 监 | 时变路网行程（本地 Dijkstra，积水路段加罚或断开）；Google Routes 路况（无密钥时用标注过的模拟曲线）；车队 / 班组 / 物资 / 罐车 |
| `swbrain/recession.py` · `swbrain/replay.py` · `swbrain/lifecycle.py` | 监 | 退水方案（自然 / 全局最优 / 单点最优 / 预置 / 罐车 / 换管）；退水重演；降雨事件 × 积水经历的生命周期 |
| `swbrain/ontology/schema.py` | 本体 | 声明：对象类型 17 · 关系 31 · 规则 28 · 动作 49 |
| `swbrain/ontology/runtime.py` + `ontology/actions/*.py` | 本体 | `Runtime.act`：权限、校验、执行、中英小结、审计的唯一出口；动作按域分文件 |
| `swbrain/agents/registry.py` · `orchestrator.py` | 智能体 | 注册表（有哪几个智能体、能用哪些动作）与主智能体分派 + 汇总 + 轨迹 |
| `swbrain/agents/computejobs.py` · `admin_routes.py` | 智能体 | 一二维计算任务队列（申请 → 管理端批准 → 独立进程执行）与管理端接口 |
| `swbrain/mcp_server.py` · `dsh_bridge.py` | 智能体 | MCP 薄桥（工具＝本体动作）与 DeepSeek Harness 接入 |
| `swbrain/dsh_lanproxy.py` | 智能体 | Harness 原生 Web UI 的局域网入口（反向代理，只放管理员） |
| `swbrain/routes.py` | 出 | 接口表（55 个接口）：一处声明、一处解析参数、一处写说明；`GET /api` 自描述 |
| `swbrain/serve.py` · `state.py` | 出 | HTTP 与身份 / 口令；状态装载、按情景算上下文与调度、落盘缓存 |
| `swbrain/artifactcache.py` · `jobs.py` | 出 | 算过的东西按「代码 + 配置 + 状态」指纹落盘；长求解进子进程，`?async=1` + `/api/jobs/<id>` 轮询 |
| `swbrain/identity.py` | 身份 | 统一身份（SDIP）：平台账号登录、按 `SWBRAIN.*` 职能判权、写动作前现查会话 |
| `swbrain/i18n.py` · `assumptions.py` · `doctor.py` | 出 | 系统生成内容的英文版；假设总账；安装自检 |
| `console/index.html` · `app.js` · `i18n.js` · `admin.html` | 出 | 控制台：12 个页签（总览 / 第一道 / 建议 / 排期 / 达标分期 / 剩余风险 / 第二道 / 第三道 / 预置与应急 / 防线2+ / 数据与可信度 / 本体模型），Leaflet 地图，数据按页签懒加载；管理端 |
| `tools/c1_pipeline.py` + `tools/` | 计算 | 一二维全流程与全部建模 / 后处理 / 交付脚本；`tools/demo_case.py` 是快速验证 |
| `docs/系统架构设计.md` · `docs/智能体架构.md` · `docs/本体模型.md` | | 架构总览、智能体与本体、本体清单 |
| `docs/可信度说明.md` · `docs/部署与交付.md` · `docs/接口约定.md` | | 哪些是真数据哪些是假设；安装与部署；与预测侧的契约 |

## 参数要替换的五处

`config.toml` 里这三组是筛查假设，接到真实数据前请替换：

1. `[model.scenarios]` 设计雨强（mm/h，15 分钟）→ 用 ADM 排水设计标准的 IDF。
2. `tide_high_m` 高潮位 → 用 NCM/港务潮位表。
3. `inlet_area_m2` / `road_width_m` 汇水面积法 → 有 DEM 或汇水区划分后替换。
4. `[cost]` 单价与 `[whatif].budget_aed` 预算 → 用 ADM/ADPIC 的费率表与实际年度预算；`[whatif.kpi_weights]` 决定不同 KPI 减少量怎么折成一个收益分。
5. `[standard]` 验收标准：目标情景、按用地的可接受积水深度与历时、年度预算、情景权重 → 由 DMT 书面确认；`[verification]` 对账合格线 → 与预测侧商定。

## 状态与确认

所有建议落库时 `status='建议'`；人工在 `swbrain.proposal` 上改 `status`（采纳/否决/合并）并填 `decided_by`、`decision_note`。下一轮运行是新的 `run_id`，历史不覆盖。

## 多智能体与管理端

对话入口（控制台对话抽屉）先到**主智能体**，由它按领域词分派给四个专业智能体：**调度研判**（= 下面的排水智能体，DeepSeek Harness 驱动）、**一二维计算**（结果、进度、提交计算申请）、**报告交付**（交付目录、申请重出交付）、**数据质检**（数据齐不齐、结果质检）。每个智能体只能调自己名下的本体动作。管理端还能**新建智能体**（起名、写职责、给分派关键词，从现有 49 个动作里勾一组），但它造不出新动作能力。

**长时计算只能申请**：一二维计算 / 报告交付只能把申请写进队列（状态「待批准」），**管理端批准后才执行**，同时只跑一条；命令行后台链起的计算在跑时，队列里的执行进程会先等它让出机器。任务类型有四种：`compute`（建模 → 计算，可接着设计并算 v3）、`post`（后处理与交付）、`prep`（准备中间数据）、`selftest`（快速验证）。批准只看角色——有管理权限就能批，含自己提交的（提交人与批准人都留痕）。

管理端：控制台顶栏「管理端」→ `/admin.html`。身份有两种模式（`[identity].mode`）：`shared_key`（缺省）用 `config.toml` 的 `[serve].admin_key` 登录，和访问口令分开，为空 = 管理端关闭；`sdip`（统一身份）要求账号持有 `SWBRAIN.ADMIN`，两道共享口令一律不认。里面有四块：智能体清单与状态、配置（启用 / 停用智能体与动作、分派关键词）、会话与调用轨迹、计算任务队列（批准 / 驳回 / 暂停 / 恢复 / 取消，也能看机器上正在算的全部一二维计算）。架构总览见 `docs/智能体架构.md`，运维细节见 `docs/多智能体架构.md`。

## 智能体入口（本体驱动，运行时 = DeepSeek Harness）

控制台顶栏「智能体」打开对话抽屉（或 `?ask=ADM-62%20派几台泵`）。它只通过本体动作行动：以「积水点」为核心的对象 / 关系 / 规则 / 动作声明在 `swbrain/ontology/schema.py`，详见 `docs/本体模型.md`；本体与智能体怎么结合、有哪些智能体，见 `docs/智能体架构.md`。

规划器逐层降级，回答里注明用的是哪个：**DeepSeek Harness**（真 agent 运行时，经 MCP 拿本体动作）→ **直连大模型**（Anthropic Messages API，纯 urllib，密钥 `ANTHROPIC_API_KEY` 或 `[agent].api_key_env`）→ **规则规划器**（按动作的 `intents` 关键词 + 实体抽取）。三者动作一致、每步记 `out/ontology_actions.jsonl`。命令行：`python -m swbrain.ontology --check` 自检声明，`python -m swbrain.ontology.agent "ADM-62 在 50 年一遇下派几台泵"` 直接问。

真正的 agent 运行时用 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)：`pip install deepseek-harness-sdk` 后 `python -m swbrain.dsh_bridge --web` 启动 Web UI（默认预设 = 排水智能体，工具 = 本体动作经 MCP），控制台抽屉的对话也走它。原生 Web UI 那条入口**只读**（分不出是谁在用），且只听本机；局域网经控制台服务里的反向代理 `http://<局域网地址>:3081/` 打开，**只放管理员**（`[agent.dsh].lan_port = 0` 关闭）。见 `docs/智能体运行时_DeepSeek_Harness.md`。

外部平台（SDIP）可以把它的智能体的一次工具调用转发进来，走 `POST /api/tool/<工具名>`（请求体就是入参）：验平台令牌、查平台会话，不换第二次域；管理端停用、发起人职能、只读入口的写拦截三道闸照旧。

## 运行（2026-09-23 起）

- `python -m swbrain.serve --port 8766`：启动后先读 `out/cache/<指纹>/` 里算过的情景上下文与调度，同一份代码 / 配置 / 状态下重启几十秒可用；后台预热只补没算过的。
- 长求解（调度 60–110 s）在子进程里跑，页面走 `?async=1` + `/api/jobs/<id>` 轮询，求解期间别的接口照常响应。
- 顶栏「本页有 N 项假设」点开是假设总账（`/api/assumptions`）：哪些数字是模拟 / 假设 / 占位 / 未核实 / 缺失，各自怎么替换。
- 本地检查：`.\scripts\ci.ps1`（测试 + 前端语法 + 本体自检 + 配置漂移）；加 `-Integration` 跑真机集成测试。
- **身份与网络**：两种模式——`shared_key`（缺省，两道共享口令，只给本机与演示）或 `sdip`（平台账号登录，按 `SWBRAIN.VIEW / FIELD / ALERT / PLAN / ADMIN / FEED` 判权，动作日志记主体号）。默认 HTTP，**仅限可信局域网**；要出网或跑统一身份，配 `[serve].tls_cert` / `tls_key`（本机自签：`python -m swbrain.tlscert`）。
