# 雨水调蓄计算器

把 `Stormwater model v2.xlsx` 的筛查模型转成网页计算器，并为 845 个积水点
批量填补目前缺失的逐点管网能力（Qcap）。

完整背景、数据源现状、计算方法与待决策项见 **[docs/STORMWATER_CALCULATOR_CONTEXT.md](docs/STORMWATER_CALCULATOR_CONTEXT.md)**。
下文只讲怎么跑起来。

## 快速开始

**前后端分离**：后端（FastAPI）在 `backend/` 下，只出 JSON；页面是 `frontend/` 下独立的
Vue 3 + Vite 工程。两个工程各有自己的依赖与环境文件（`backend/.env` / `frontend/.env`）。
两边各起各的进程，开发时要起两个。

### 后端 API

```bash
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env      # 填入数据库密码
.venv/bin/python -m pytest
./start.sh                # 后台起在 8101；或 ./start.sh -f 前台带 --reload
```

API 文档：http://127.0.0.1:8101/docs

### 前端页面

```bash
cd frontend
npm install
cp .env.example .env      # 默认即可；只有要绕开代理直连后端时才动它
npm run dev
```

指挥中心（电子地图 + 845 个积水点）：http://localhost:5173/
单点快算页面：http://localhost:5173/calculator

前端默认走 **Vite 代理**：浏览器请求的是同源的 `/api/*`，由 Vite 转发到
`http://127.0.0.1:8101`（改后端端口见 `frontend/.env` 的 `VITE_API_PROXY`）。
这条路不触发 CORS。要跨机联调、或把前端构建成静态站单独部署，就在
`frontend/.env` 填 `VITE_API_BASE=http://<后端地址>`，此时请求变成跨源，
`backend/.env` 的 `CORS_ORIGINS` 必须放行前端的源（默认已放行 5173 的两种写法）。

构建产物：`cd frontend && npm run build` → `frontend/dist/`，交给任意静态服务器即可。

指挥中心页面用 Leaflet + Esri / OpenStreetMap 瓦片做底图，**瓦片需要外网**；
Leaflet 本身已随前端打包，不再依赖 cdnjs。内网环境下底图空白，其余功能照常。

## 目录结构

```
backend/                         后端工程（FastAPI + 计算内核，独立部署）
  start.sh                       起 / 停 / 看后端服务（默认 8101）
  requirements.txt  pytest.ini   Python 依赖与测试配置
  .env                           数据库凭据与 CORS 白名单（不进代码库）
  data/                          后端自带的数据（跟着后端一起部署）
    DEM_UAE_UTM40/               DEM 栅格，采样高程判 IDF 分区（1.1G）
    processed/                   各 batch 脚本的产物，API 运行期要读（不进代码库）
  src/stormwater/
    constants.py   全局假设参数（§3.5）—— 标注「假设值」的必须可覆盖
    idf.py         可切换的 IDF 数据集：ADQCC Zone A/B（公式）+ 迪拜 DM（查表）
    rational.py    推理公式法 6 步计算链（§3.2）+ 三道防线判定
    ponding_curve.py 逐点积水量时程 —— **积水量的唯一数据源**；取不到就整块不出
                     结论（Qcap 为什么退出水量平衡，证据链在模块头注释）
    manning.py     曼宁 Qcap + 管材糙率映射 + 单位归一化（§4.3–4.6）
    config.py      环境变量（凭据不进代码库）
    db.py          管网库只读访问 + §4.6 的两条阻塞查询
    hotspots.py    三份台账合并清洗 → backend/data/processed/hotspots_845.csv（可重跑）
    registry.py    读标准表 + DEM 判 Zone A/B + 合并 Qcap 批算 + 装配成计算输入
    qcap_batch.py  从管网拓扑批量反算逐点 Qcap → backend/data/processed/hotspot_qcap.csv（可重跑）
    storage_batch.py 批量反算逐点可用调蓄 → backend/data/processed/hotspot_storage.csv（可重跑）
    storage_dims.py  调蓄设施深度/尺寸缺省值目录（带出处）→ backend/data/processed/storage_asset_dims.csv（可重跑）
    catchment_batch.py 从地籍与地物图层批量反算逐点 A / C / tc → backend/data/processed/hotspot_catchment.csv（可重跑）
    coverage_batch.py 把「有管网数据的区域」画成面 → backend/data/processed/network_coverage.geojson（可重跑）
    network_batch.py 把雨水管网本身画成线 → backend/data/processed/network_mains.geojson
                     + network_laterals.geojson（可重跑）
    district_batch.py 按官方社区面几何给逐点打行政片区标签 → backend/data/processed/hotspot_district.csv
                     + district_boundaries.geojson（可重跑）
    scenario.py    按一场雨批算整份台账 → 三道防线归类（共用时限 T）+ 本场总账
                   ⚠ 无标定的点单列 line_counts["uncalibrated"]，水量与泵数不并入总账
    report.py      把场景计算结果导出成中文文档 + 中文表头明细表（可重跑）
    i18n.py        中英对照表：只在 API 边界上翻展示文案，领域模块与协议值一个字不动
    api/main.py    HTTP API：台账落图 / 批量场景 / 单点多重现期 / 算前默认降雨参数
                   + 单点快算 + 并排对照。只出 JSON，不再返回任何页面
  tests/
    test_rational.py      §3.6 的 H1 表锁死为回归基准（显式走迪拜数据集）
                          + TestDefenceHydrodynamic：第一道只读演算时程、Qcap 不得
                          再影响任何一道防线、没跑过演算的点必须不出结论
    test_idf_datasets.py  ADQCC 公式还原已公布 DDF 表 + 两套方法的差异不可被抹平
    test_manning.py       DN1200 验证示例 + 糙率分支顺序 + 单位误判
    test_point_curve.py   单点多重现期接口：与全域批算逐字段相等 + 「撑到第几级」的口径
    test_storm_preset.py  算之前摆出的默认雨量与历时，必须就是算出来用的那两个数
    test_hotspots.py      845 行口径 + 四种脏坐标写法 + 跨区枚举归一
    test_scenario.py      落图坐标范围 + 实测与假设不得互相冒充 + 汇总等于逐点之和
    test_catchment.py     ADQCC Table 3-8/3-9 取值逐条锁死 + 面积不重复计 + tc 的 10 min 下限
    test_district.py      片区按几何判而非台账 area 列 + AL BATEEN 重名的两个区不得混算
    test_i18n.py          中英切换：该翻的必须有英文、协议值不许翻、换语言不改数字
frontend/                        前端工程（Vue 3 + Vite + Pinia + Leaflet，独立部署）
  src/api/                       后端调用封装（client.js 收口 API base、语言参数与报错口径）
  src/i18n/                      中英文案（index.js 是 locale + t()，zh.js / en.js 逐键对照）
  src/stores/scenario.js         全局状态：台账、筛选、模型输入、两种模式的计算结果
  src/views/CommandCentre.vue    指挥中心：电子地图 + 845 点落图 + 单点多情景（默认）/ 全域批算
  src/views/CalculatorView.vue   单点快算页面（/calculator）
  src/components/MapView.vue     Leaflet 地图：底图 / 覆盖面 / 片区轮廓 / 845 个点
  src/components/HotspotSearch.vue  积水点搜索（编号 / 位置 / 片区，按 / 聚焦，↑↓ + Enter 定位）
  src/components/calc/           场景计算器侧栏（单点面板 / 批算面板 / 计算链 / 筛选 / 输入）
  src/components/brief/          研判简报侧栏（单点逐级指令 / 全域三道防线）
  src/components/drawer/         模型输入抽屉（汇水剖面 / 全局假设 / IDF 只读表 / 数据口径）
  src/styles/                    两套样式表，各自以 body.theme-cc / body.theme-calc 收口
docs/
  DMT_DATA_REQUEST.md     向 DMT 索取的数据缺口（阿布扎比市口径 13 项，文本版，可 grep / diff）
  DMT_DATA_REQUEST.html   同上，排版成品，发送 / 打印用
  SCENARIO_RESULTS.md     场景计算结果中文报告（由 report.py 生成，勿手改）
docs/standards/
  ADQCC_WA-726-1_StormwaterDesignManual.pdf   法定标准原文（第三版 2022-04）
```

## 打包部署

后端是自包含的：代码、依赖清单、数据全在 `backend/` 里，压这一个目录就能搬。

```bash
tar czf backend.tar.gz \
  --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
  --exclude='logs' --exclude='.env' \
  backend/
```

排除的四样东西的理由：`.venv` 里全是本机绝对路径（搬过去 `bin/` 下的入口脚本会指向不存在的
解释器），目标机上重建；`logs/` 是运行产物；`.env` 带数据库密码，走目标机的配置渠道另发。

目标机上：

```bash
tar xzf backend.tar.gz && cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env      # 填数据库密码与 CORS_ORIGINS
./start.sh
```

`docs/` 不用带 —— 只有 `hotspots.py`（读三份台账原件）和 `report.py`（写报告）碰它，
两个都是开发机上重跑的批处理脚本，API 运行期不读。

**包大小**：`backend/data/DEM_UAE_UTM40/` 占 1.1G，是压缩包的全部重量。API 运行期用它采样
高程判 ADQCC Zone A/B —— 845 个点里 164 个判为 Zone A。DEM 缺失时
[registry.py](backend/src/stormwater/registry.py) 会**静默**退回全体 Zone B，不报错、
不记日志，那 164 个点的雨强会偏小。默认口径的 501 个 in-coverage 点全在 Zone B，
所以只跑默认范围时不带 DEM 结果不变；但这是个容易踩的哑雷。

## 中英文切换

顶栏右侧的 **中 / EN** 开关切整套界面；`/calculator` 页面右上角也有一个同样的开关。
选择存在 `localStorage`，首次打开按浏览器语言猜（`zh*` → 中文，其余 → 英文）。

切换会把**后端产出的那批文案**一并换掉 —— IDF 数据集的口径说明、`limitations`、
`caveats`、出处标签、泵分档的依据与来源，都是随计算结果发回来的，故换语言时前端会
按 `?lang=` 重取台账、并用**同一套输入**重跑已经算过的场景。输入一个字节都不改，
重算出来的数字与原来逐字段相等（`test_i18n.py::test_numbers_do_not_change_with_language` 钉死这一点）。

后端的翻译发生在 **API 边界**（`src/stormwater/i18n.py`），领域模块里的中文一个字没动：

- `?lang=zh|en` 优先，没给就看 `Accept-Language`，都认不出用中文；
- `i18n.EN` 是**按原串精确匹配**的表 —— 改了 `idf.py` / `pumps.py` / `registry.py`
  里的一句中文，这里的键要跟着改，否则那一条会悄悄退回中文（`test_i18n.py` 会红）；
- **协议值不翻**：facets 的 `value`（`未填写` 是后端生成、`ScenarioRequest.matches()`
  拿它比对筛选条件的）、数据集 `key`、泵档 `key`、各种 `*_basis` 的 key。翻了它们，
  英文模式下按「严重度」筛就一个点都筛不到。

前端文案在 `frontend/src/i18n/`：`zh.js` 是口径的来源，`en.js` 逐键对照，缺键回落中文。

## 中文导出

页面上的「导出 CSV」按钮导出的就是中文表头（含筛选条件与「模型输入」抽屉里的改动），
文件名形如 `场景计算结果_25年一遇_501点.csv`（点数随计算范围变）。
⚠ 导出跟着界面语言走：切到 EN 导的是 snake_case 英文表头（`scenario_results_25yr_501_points.csv`），
**列的顺序与含义不变**，但列名与下面 `report.py` 的产出不再同名，两边对不了 diff —— 要 diff 请用中文界面导。要在命令行拿默认口径的结果（同样只算有管网数据的 501 个点）：

```bash
cd backend && PYTHONPATH=src .venv/bin/python -m stormwater.report
```

两边导出的 CSV **列名与出处措辞逐列一致**，可以直接 diff；脚本这边固定跑未改动的
默认假设（对应页面的「模型输入 v1.0」）。产出两份，都由计算侧数据生成，**不要手改**：

- `docs/SCENARIO_RESULTS.md` —— 中文报告：五档重现期汇总、三道防线、分区分级、
  车队对照、Qcap 与调蓄的出处分布、假设参数与局限声明；
- `backend/data/processed/scenario_points_zh.csv` —— 主口径（25 年一遇、自动分区）逐点明细，
  501 行 × 51 列，中文表头，UTF-8 BOM，Excel 双击可开。

主口径写在 `report.py` 的 `MAIN_RETURN_PERIOD` / `MAIN_DATASET`，改它等于改对外口径。

## 对外数据需求

阻塞项与数据缺口已整理成 **[docs/DMT_DATA_REQUEST.md](docs/DMT_DATA_REQUEST.md)**
（**阿布扎比市口径**，13 项，按优先级排列，每项附实测现状、影响面与具体索取内容）。
现在只剩**一项阻塞** —— §09 市区汇水区划分成果或高精度地形 —— 不补齐则对应结论
不具备对外报送条件。§03 调蓄设施深度与尺寸 2026-09-19 已用公开标准的缺省值兜住
（见 [docs/STORAGE_DIMENSION_BASIS.md](docs/STORAGE_DIMENSION_BASIS.md)），降为第二优先，
仍需 DMT 的实测值固化。§01（艾因与西部区管网）本轮移出范围、编号保留，
范围扩回全域即恢复为第一优先阻塞项。

## IDF 数据集

页面顶部可切换三套，切换的不只是数字，**方法本身也不同**：

| | 迪拜 DM | ADQCC Zone A / B |
|---|---|---|
| 历时 | 向下取档到 15 个档位 | 连续公式，tc × M |
| 强度 | 查表 | i = α / (t·M)^β |
| tc 乘数 | 无此概念 | Table 3-3，按汇水区优先级 1×–12× |
| Cf 频率调整 | 有（TxDOT） | **无**，C 直接取 Table 3-8/3-9 |

默认 **ADQCC Zone B**（平原区）。Zone A/B 以海拔 200 m 等高线分界。

⚠ 分区点数有两个版本，尚未对齐：本文档早先记的是「艾因 AAM 148 个点落在 Zone A」，
而 `registry.py` 现在用 `DEM_UAE_UTM40.tif` 最近邻采样 845 个点，判出 **Zone A 164 个**
（AAM 163 + DRM 1，其余 681 个为 Zone B）。差异应出自采样方式或早先的点集口径，
用于对外报送前需要复核一次。

迪拜表保留的唯一理由是复现原 Excel 做回归对照，**产出不可对外报送**。
同一个 H1 点，迪拜算出的所需调蓄合计比 ADQCC Zone B 高 65%。

## 指挥中心页面

前端工程的 `/`（开发期 http://localhost:5173/），按
`prototype/gemini-code-1789822474008.html` 实现，与原型的差别只有一处：
底图换成真实电子地图，845 个积水点按 WGS84 坐标落图（原型是示意 SVG + 6 个假点）。

### 两套积水点数据，页面左上角切换

地图左上角有一组页签，切的是**看哪一套积水点**。两套走**同一条计算链**（同一个
`HotspotRecord`、同一个 `run_batch()`），但不是同一批地物，故不叠画、不合并、
不互相排序 —— 那个差集本身就是要给人看的东西：

| | 历史上报（默认） | 模型识别 |
|---|---|---|
| 来源 | ADM / AAM / DRM 历史上报的台账 | ANUGA 二维水动力演算成果反算 |
| 数量 | 845 个点（ADM 506 + AAM 184 + DRM 155） | 107 个片区（1,378 个 20 m 格） |
| 覆盖 | 全酋长国三个市政区 | 只有 Al Bateen 一个 run，陆域 996.6 ha |
| 粒度 | 一个坐标点 | 20 m 格八连通聚成的多边形 |
| 回答 | 「历史上这里报过积水」 | 「100 年一遇下这里会积多深、滞多久」 |
| 参与计算 | 是 | **是** —— 见下 |

两路的接口是同一个，只差一个 `source` 参数：`GET /api/hotspots?source=model`、
`POST /api/scenario {"source": "model"}`；`/api/point-curve` 与 `/api/storm-preset`
按 uid 前缀自动认（`MDL-` 是片区）。前端的计算器、搜索、筛选、KPI、研判简报
两路共用同一套组件，只是点集不同。

#### 片区的计算输入是怎么补出来的

计算链要五个输入，片区身上本来一个都没有。`model_cluster_batch.py` 把片区代表点
灌进 `hotspot.model_clusters`，然后跑**台账那一路一模一样的四个批算**
（SQL 已参数化，判据一行没改）：

| 输入 | 片区的来源 | 与台账 845 点 |
|---|---|---|
| 汇水面积 A | 20 m DTM 上 **D8 汇流实算**的边界 ∩ UDM 地物 | 台账是 Voronoi 几何代理 |
| 径流系数 C | UDM 地物图层面积加权（ADQCC Table 3-8） | **同法** |
| 集流时间 t<sub>c</sub> | 坡面流 + 管内流行 | **同法** |
| 管网能力 Q<sub>cap</sub> | `sw_edge` 实测内底反算 | **同法** |
| 可用调蓄 | 250 m 内 pond / soakaway / reservoir | **同法** |

即**五项里四项与台账逐字段同法**，只有汇水区边界不同 —— 而那一项片区反而更实在：
台账点只是一个坐标，没有地形可依，边界只能等分；片区来自一次二维演算，地形就在
手上，故走 priority-flood 填洼 → 八连通标洼地 → D8 逐格追踪终点，得到真汇流范围。
实测下来 **D8 合计 363 ha、Voronoi 代理合计 859 ha，代理高估 2.4 倍**，
两个数逐点并列存着（`area_measured_ha` 与 `area_voronoi_ha`），页面上并排显示。

⚠ 两套的 A **不可直接比大小**（不是同一种边界），逐点自述在 `catchment_boundary`：
台账 `voronoi_250m`，片区 `d8_depression`，18 个无宿主洼地的片区退化为 `cluster_only`。
可比的是它们各自算出来的三道防线结论。

#### 覆盖率：片区是目前唯一能全员出结论的点集

⚠ 第一道防线现在**只认二维演算的积水量时程**（`defence_basis="hydrodynamic"`，
见 `ponding_curve.py`）——旧的「管网按满流 Q<sub>cap</sub> 恒排 T 小时」口径已被删除。
这把覆盖率变成了决定性的东西：

| | 跑过二维演算 | 能出三道防线结论 |
|---|---|---|
| 历史上报 845 点 | 18 个（2.1%） | 18 个，其余 827 个标「无水动力标定」不出数 |
| 模型识别 107 片区 | **107 个（100%）** | **全部** —— 它们本来就是从这次演算里识别出来的 |

片区的积水量时程写在 `model_cluster_ponding_series.csv`（7,360 行），由
`ponding_curve.load_all_curves()` 与台账那份并进同一张表（uid 互不相交）。

100 年一遇下，107 个片区里 **105 个落到第三道**（2 个管网自己在 T 内排完），
合计需 237 台泵、6,829 车次。

#### 拿实测积水量校核推理公式法

片区身上有一个台账点几乎拿不到的量：**ANUGA 逐帧求和出来的实际积水量**。
批算结果里因此多了一块 `ponding_check`，把两个数并排摆出来。100 年一遇下：

| | 全部 107 个片区 |
|---|---|
| 推理公式法 · 来水量（shape × Q<sub>peak</sub> × D） | 43,197 m³ |
| ANUGA 实算 · 积水量 | 203,798 m³ |
| 比值 | **4.72×** |

⚠ 推理那一侧**是重算的，不能读 `runoff_volume_m3`** —— 水动力口径下那一列已经是
演算读出来的全程净入流，拿它跟演算比是同一个数跟自己比（`test_flags_a_different_storm`
就是这一条的守门测试）。Q<sub>peak</sub> 与设计历时 D 仍随重现期走，且只作峰值诊断。

**这个比值不是误差，是口径差**：推理公式法给的是 t<sub>c</sub> 历时（本批中位 13 min）
内那一段设计峰值过程线的体积；ANUGA 给的是整场 180 min 雨演算下来实际滞留的水，
还含管网溢流注入与汇流范围之外流进来的水。中间那一档可以自己验：
全场 60.33 mm × C × D8 汇流面积 = 147,655 m³。这正是第一道防线改读演算时程的
原因之一（完整证据链见 `ponding_curve.py` 模块头）。

页面选的重现期与该 run 的重现期不一致时，对照块会把 `comparable` 打成 false
并说破 —— 不同级别的两个数摆在一起会让人误以为是同一场雨。

#### 片区独有的那些量

模型这一路的落图与水力指标走 `GET /api/model-ponding`（`model_ponding.py` 读
`backend/data/analysis_ponding/ponding_hotspots.geojson`）。片区按四类成因分色，
点开任一片区，计算器面板里除了三道防线结论，还会多出一块它独有的指标：
分级历时、残余水深、洼深与洼地库容、SWMM 注入量、危险指数，以及汇水区边界的
两个口径。判据与算法见同目录的 `算法与成果说明.docx` 与 `易积水分析报告.md`。

⚠ 该成果自述为**诊断性**（`completed_diagnostic_not_engineering_admitted`），
不是工程审定值；5.39 m 这类极值来自 DTM 深坑，须影像 / 现场核实；且只有
Al Bateen 一个 run，**别的区没有片区不等于那里不积水**。这几句随接口一起发回，
页面原样展示。

#### 重跑这一路

```bash
# 需要数据库（写 hotspot.model_clusters 与带 _model* 后缀的四张工作表）
# 一并产出逐片区积水量时程 —— 第一道防线直接读它，缺了片区就不出结论
backend/.venv/bin/python -m stormwater.model_cluster_batch
```

### 台账那一路的两种交互，侧栏顶部切换

**单点 · 多情景（默认）** —— 三步走，每一步都由人推进，页面不替人开算：

1. **选点**：点地图上任一积水点（或用搜索框），侧栏先摆出该点的逐点输入 ——
   有无管网、Q<sub>cap</sub>、可用调蓄、A / C / t<sub>c</sub>，每项带出处标签；
2. **选一场雨**：选 5 / 10 / 25 / 50 / 100 年一遇中的一级，页面随即取
   `POST /api/storm-preset`，把这一级**默认的雨量 P、历时 D 与强度 I** 只读地摆出来
   —— 这三个数由导则与该点 t<sub>c</sub> 定死（历时 = t<sub>c</sub> × M，强度按
   Table 3-5/3-7 公式反查，P = I × D ÷ 60），没有可选余地，故不给输入框。
   要脱开导则做压力测试，只能切「自定义」那一档，那里 P 与 D 才可改；
3. **按「计算」**：`POST /api/point-curve` 只算选中的这一级，出完整计算链、
   三道防线结论与调度指令。

- 算过的级留在侧栏里按级累积（重现期按钮带 ✓），想比较几级就把几级各算一遍；
- 没算过的级**不摆任何结论** —— 页面不拿别的重现期的数字顶替；
- 换点、换 IDF 口径、改场景假设或模型输入，已算的结果一律作废并提示重算，
  旧口径的数字不会留在屏幕上；
- 摆出来的雨就是算出来用的雨：预览与计算结果的历时、强度、逐点输入逐字段相等，
  由 `backend/tests/test_storm_preset.py` 锁死。

**全域批算** —— 选一场雨，把筛选范围内的点整体发给 `POST /api/scenario`，
算完地图按三道防线重新着色，给出本场总账与台账对照。

两种模式共用同一套模型输入与同一条计算链（`/api/point-curve` 逐行走的就是
`run_batch()`），同一个点、同一场雨的数字**逐字段相等** —— 由
`backend/tests/test_point_curve.py` 锁死。

- 左侧 KPI、右侧 420 px 侧栏、研判简报、模型输入抽屉、三道防线卡片沿用原型布局；
- 顶部切 IDF 数据集（含 **自动分区**：按 DEM 高程逐点判 Zone A / B），侧栏选重现期或自定义雨；
- 导出的 CSV 每行自带 `dataset_key` 与模型版本，避免两套口径的数字混在一起。

底图可在深灰 / 浅灰 / OpenStreetMap / 影像之间切换，用的都是免密钥瓦片
（Esri ArcGIS Online、OpenStreetMap）—— CARTO 的 basemaps 现在要 API key，已弃用。

## 三道防线：共用一个时限 T

问的是**每一个积水点**三件事，三件事共用同一个时限 T（`Assumptions.drawdown_hours`，
默认 **2 小时**，页面「模型输入」抽屉里可改；改一个值三道一起变）：

| | 问题 | 判据 | 逐点输出 |
|---|---|---|---|
| 第一道 | 管网这条腿在工期 T 内能分担多少 | R1 = max(0, V<sub>全程</sub> − R × T)，V 与 R 都读二维演算的积水时程；Q<sub>cap</sub> **不参与** | 全程积水量 / 管网 T 内排走 / 实测退干需几小时 / 剩余量 |
| 第二道 | 剩余量现有调蓄接不接得住 | 剩余 × 淤积裕量 ≤ 可用调蓄 | 需调蓄 / 实际吸收 / 能否接住 |
| 第三道 | 页面配的那套「泵 + 罐车」够不够；不够该配多少 | 链的外运能力 = min(泵侧, 罐车侧) | 待排水量 / 速率 / 两侧能力 / 链实际外运 / 瓶颈在哪侧 / 够不够 / 清不完多少 / 清完要几小时 / 该配几台泵几辆车 |

#### 第三道是一条串联的链，不是两个可选方案

**移动泵和罐车不是「或」的关系** —— 泵把积水抽进罐车，罐车拉到受纳点倒掉再回来。
两段串在一起，缺一段就是零，通过量由**瓶颈段**定：

```
链的外运能力 = min(泵侧抽排能力, 罐车侧持续外运能力)

泵侧   = 每点台数 × 单泵抽排能力              连续流量
罐车侧 = 每点辆数 × (单车载量 / 往返周转)      离散往返折成的持续能力
```

所以 `pumps_required` 与 `tankers_required` 是**同时都要**的一对；泵配得再多，
车不够就只能停机等车。默认参数下两侧差着一个量级 —— 一台泵 300 m³/h，一辆
15 m³ / 60 min 的罐车持续外运只有 15 m³/h：

> **喂饱一台泵要 20 辆罐车**（`Assumptions.tankers_per_pump`）。

瓶颈几乎总在罐车侧（`chain_bottleneck = "tanker"`），全域汇总用
`points_tanker_bound` 单列那些「再加泵一滴水也多排不出去」的点。

⚠ 两侧取整口径**不同，不要混用**：「T 内运得走多少」罐车按**整趟**算（半趟在时限内
没送到，不算数），而「清完要几小时」按**持续**能力算。

#### 第三道的两个问题

页面上「模型输入」里有五个车队旋钮，两套积水点（845 台账 / 107 模型片区）共用：

| 旋钮 | 默认 | 性质 |
|---|---:|---|
| 每个积水点配泵 | 1 台/点 | 运营配置 |
| 单泵抽排能力 | 300 m³/h | **假设值** —— 与在册车队加权平均 321 相差 7% |
| 每个积水点配罐车 | 1 辆/点 | 运营配置 —— 与泵**串联**，两侧取小 |
| 罐车单车储水能力 | 15 m³/车次 | **假设值** —— 应替换为实际单车载量 |
| 罐车单车往返周转 | 60 min/趟 | **假设值，本项目无任何数据支撑** —— 待 DMT 补 |

第三道据此并列回答两个问题，**两个数不可相互替代**：

* **已配的够不够** —— 这条链在 T 内运得走待排水量吗；不够就报
  `chain_residual_m3`（时限内清不完的量）、`chain_hours_required`（真要清完几小时）
  与 `chain_bottleneck`（卡在哪一侧，决定该加泵还是该加车）。
* **该配多少** —— `pumps_required` = ⌈抽排速率 / 单泵能力⌉，
  配套的车数取 max(`tankers_required` 按水量算, `tankers_to_feed_pumps` 喂饱泵要的)，
  即 `fleet_gap.tankers_required_binding`。

泵侧之差就是缺口（`fleet_gap.pumps_configured_gap`）。台账 `pumps_qty` / `tankers_qty`
是第三个数（实际列装记录），三者分开读。

#### 罐车：车次 ≠ 辆数

泵是**连续流量**（m³/h），时限 T 一乘就是运力；罐车是**离散往返**，T 换不成辆数，
必须先知道一趟多久 —— 这正是交接文档 §7 待决策第 5 条（「不纳入周转则『101 车次』
只是体积换算，非调度结论」），现已纳入：

```
车次   = ⌈待排水量 / 单车储水能力⌉          与 T 无关，纯体积换算
每辆趟数 = ⌊T × 60 / 单车往返周转⌋            T 在罐车这一侧唯一的入口
辆数   = ⌈车次 / 每辆趟数⌉                  调度结论
```

⚠ 每辆趟数为 0（周转长于时限）时**辆数不出数**（`tankers_required = null`），
不是 0 辆 —— 0 会被读成「不需要罐车」，实际含义是「这个时限下水根本运不走」。
泵抽出来的水没车拉走，这些点第三道防线**整条不成立**；且卡的是**时延**不是运力，
加车也跑不完一趟，只能放宽 T 或把受纳点设得更近。
全域汇总用 `points_tanker_infeasible` 单列这批点，此时辆数合计是下限。

把「单泵抽排能力」**清空**则退回 12″/8″/6″ 分档派车（`pumps.dispatch()`，按「总排量 +
台数罚项」取最优组合）—— 那是回答「采购该买哪几档」时唯一说得清的口径，逻辑一行没删，
只是不再是默认。此时「一台」取在册构成的加权平均 321 m³/h。

### 第一道为什么不再用 Q<sub>cap</sub> × T

原先第一道按「管网以满流能力 Q<sub>cap</sub> 恒排 T 小时」算水量。用 Al Bateen
RP100 那一个 run 里 10 个积水量 ≥500 m³ 的点实测，这个模型的**两个自变量都选错了**：

| 要预测的量 | 原先用的自变量 | 实测表现 | 真正的控制变量 | 实测表现 |
|---|---|---|---|---|
| 这个点会积多少水 | 汇水面积 A（Voronoi 代理） | 实测/预测比值 **0.00–8.29**，跨三个量级 | 地形洼地库容 | corr = **+0.982**（n = 18） |
| 积了多久排得完 | Q<sub>cap</sub>（管段满流能力） | 与实际排空速率 corr = **−0.156** | 等效排空时间常数 | 10 个点全落 10.2–16.8 h，中位 **12.4 h** |

Q<sub>cap</sub> 不但没有预测力，方向还是反的：ADM-376 的 Q<sub>cap</sub> 2.092 m³/s
是这批点里最高的，实测排空速率只有 0.011；ADM-380 的 Q<sub>cap</sub> 只有 0.430，
实测速率 0.253。

一维成果给出同向旁证：那份 SWMM 全城 run 在 RP100 下产流 6,336 ML，排放口出流
仅 34 ML，其余从检查井溢出 —— 而它的管道内底是「DEM 地面高程减固定埋深 1.10 m」、
坡度即地形坡度，满流能力比实测内底反算的还高 2.4 倍（见 `swmm_batch.py`）。
**比真实管网乐观得多的模型，都排不出去。**

所以 Q<sub>cap</sub> 退出三道防线的水量平衡，只留作峰值诊断
（`peak_exceeds_capacity`：Q > Q<sub>cap</sub> → 地面会积起来）。完整证据链见
`backend/src/stormwater/ponding_curve.py` 模块头注释。

> ⚠ 减法本身是做的 —— T 是**工期**，第一道 `min(V`<sub>`全程`</sub>`, R × T)` 就是那次
> 减法，只是 R 取**演算实测的退水速率**而非 Q<sub>cap</sub>。曾试过
> `min(V_in, max(Q`<sub>`cap`</sub>`×T, drained))`，已回退：ADM-380 在 T=2 h 演算说水还在
> 涨、一滴没退，公式却记管网已排 3,094 m³，且全批 112/123 个点泵需求归零。
> 双算的疑虑仍在（演算的 V(t) 本身是净值，上涨期已扣过一次管网排水），要解掉
> 得从 `.sww` 的动量场算毛通量。复盘见 `rational.evaluate()` 水动力分支注释。

### 没跑过演算的点不出结论

第一道只对**跑过二维演算、且演算的重现期与所选一致**的点成立。其余点
`line` 为 `null`、各量为 `None`、`l1_basis` 记 `uncalibrated`，在汇总里单列
`line_counts["uncalibrated"]`，**不外推、不回退旧公式**：外推需要逐点洼地库容，
而全域只有 30.35 m 的 Copernicus GLO-30 DEM，分辨不出 Al Bateen 那些 0.04–6 ha
的洼地。给个算不准的数，比不给更坏。

100 年一遇 · 2 小时 · 501 个点：**18 个点有标定**（7 个第一道退完 / 11 个要出动
车队 —— 全程积水量 57,527 m³ / 43 台泵 / 1,515 车次 / 760 辆罐车），**483 个点不出
结论**。所有水量与车队数字都只是那 18 个点的合计，看总账必须同时看
`calibrated_hotspots`。

⚠ 选 100 年以外的重现期，**一个点都不会有结论** —— 二维演算只跑过这一场。

⚠ **两个问题不要混**。「峰值会不会溢流」和「T 内退不退得完」是两回事：同一场雨里
**481 个点峰值超过管网能力**（地面会积起来），这个数对每个点都有；而「T 内退得完
没有」只有那 18 个点答得了。明细表用「峰值是否超管网能力」与「一道_能否时限内退完」
两列分别回答。

⚠ **时限 T = 2 h 与演算不相容**：实测等效排空时间常数中位 12.4 h，地表退水本身
就要半天。且车队规模对 T 是**先升后降的非单调曲线**，在 T ≈ 2 h 见顶（RP100 · 这
18 个点：T=0.5h 19 台 → 2h **50 台** → 6h 16 台）—— 那时水已经积起来、却还没时间退，
时限又短到抽排速率压不下来。旧口径下 V<sub>in</sub> 与 T 无关，泵数随 T 单调下降，
给不出这个结论。T 是**服务水平目标**，应由决策者显式选定。

原 Excel 的峰值口径仍可一键复现，用于保守对照，也是单点快算页
（`/calculator`，手填参数、不可能有演算覆盖）恒用的口径：

```python
Assumptions(defence_basis="peak")   # 只看峰值超量，回答「峰值会不会溢流」
```

⚠ 旧的 `defence_basis="window"`（Q<sub>cap</sub> × T）**已删除**，不是改默认值。
`backend/tests/test_rational.py` 同时锁死两套口径 —— 原 Excel 的 H1 基准走 `peak`，
水动力口径的行为由 `TestDefenceHydrodynamic` 守，其中
`test_qcap_no_longer_moves_any_defence_line` 是这次修正的总闸：把 Q<sub>cap</sub>
换成任何值，三道防线一个数都不许变。

## 当前状态

已实现：推理公式法计算链、统一时限的三道防线判定、可切换 IDF 数据集（ADQCC Zone A/B + 迪拜）、指挥中心页面
（845 点落图 / 默认算 501 点 + 批量场景 + 本场总账）、单点快算页面、曼宁 Qcap（圆管/方涵/梯形渠）、
糙率映射、三份台账合并清洗。前后端已分离：后端收在 `backend/`（只出 JSON），两个页面搬进 `frontend/`（Vue 3 + Vite），
两边各自部署。
逐点汇水面积 A / 径流系数 C / 集流时间 tc 也已从地籍与地物图层反算（845 个点全部有值）。
未实现：真正的汇水区划分 —— 现在用的是几何代理边界，见下。

**积水点标准表**：`cd backend && PYTHONPATH=src .venv/bin/python -m stormwater.hotspots` 生成
`backend/data/processed/hotspots_845.csv`（845 行 × 68 列，WGS84 经纬度，`qc_flags` 列留痕）。
**ADM 的 506 个坐标不取自 Excel，取自 `backend/data/FW__Hot_Spots_506/506 Hot Spots AUH.kmz`**
—— 原 Excel 的 `Latitude`/`Longitude` 两列相对名称列整体上移了一行，第 n 行的名字配的是
第 n+1 个点的坐标（最直白的两处：第 1 行 `Al Shahama Tunnel` 的坐标在 5 km 外的亚斯桥；
`ايكاد 3` / ICAD 3 在穆萨法 54.49°E，却被配上 54.83°E 瓦特巴的坐标，差 35 km）。
KMZ 每个 Placemark 的名称、点几何、`LATIDUE`/`LONGITUDE` 属性三者同出一行且互相自洽。
换源后逐点打 `coord_fixed_by_fw506`（505 个），并带出 `coord_source` /
`fw_priority_raw`（Very Important 151 / Important 355，与 Criticality 四档不是一套）/
`fw_center_raw`（5 个服务中心）三列。
**换源必须跟着重跑六个逐点批算**：`--load-db` 重灌 `hotspot.hotspots_845` 之后，
qcap / catchment / storage / coverage / district / ponding 一个都不能少。
点数是 **845 不是 846** —— ADM 表末行 `#`=`Outfalls`、区域 `Across` 是跨排放口的泵车
汇总行（72 台），不是积水点；剔除后 ADM 泵车合计 772，772+72=844 正好对上原表。

**逐点管网能力 Qcap 已经是实测值**：`cd backend && PYTHONPATH=src .venv/bin/python -m stormwater.qcap_batch` 从管网
拓扑逐点反算，产出 `backend/data/processed/hotspot_qcap.csv`，由 `registry.py` 离线读取
（API 因此不依赖数据库可达）。845 个点的出处分布：

| 出处 | 点数 | 含义 |
|---|---|---|
| `measured` | 434 | 250 m 内 LATERAL/SECONDARY 管道反算的最大满流能力 |
| `assumed_no_coverage` | 260 | 艾因 / 西部区管网尚未导出，只能用假设 |
| `ledger_no_network` | 84 | 同上，但台账声明无管网 → 取 0（低置信度） |
| `measured_no_network` | 50 | 该片有管网数据，此点 250 m 内确实没有管 → 取 0 |
| `assumed_uncomputable` | 17 | 附近有管但缺管底高程/平坡，或无收集级管道 |

**默认只算有管网数据的区域（501 / 845 个点）。** 页面与 `/api/scenario` 的
`coverage_only` 默认为 true：只算落在数据区内的点，另外 344 个画成空心圈、
不参与计算。判定用的是逐点到最近雨水管的距离（`qcap_basis != 'no_coverage'`），
**不是行政区** —— 台账记为阿布扎比市的点里有 5 个离管网 5 km 以上，同样在区域外。
区域面由 `coverage_batch.py` 从 `sw_edge` 缓冲生成，三层（`extent` 数据区 /
`network` 管段 250 m 可及 / `computable` 可反算 Qcap），**只用于落图，不用于判定**：
面简化过，边界上和批算对不上号。

### 把管网本身画到图上

地图工具条上的管网按钮（覆盖面按钮下面那个）把雨水管网线叠到底图上，
默认关着 —— 它是参照物不是主角，而且要下 1.6 MB。线由
`cd backend && PYTHONPATH=src .venv/bin/python -m stormwater.network_batch` 生成，
口径与 `qcap_batch.py` 一致（`subassetname='SW Network'` + 状态 Active/Existing），
**图上有线的地方就是 Qcap 反算够得着的地方**。

按干管等级分四层，各自有起画的缩放级，级别写在后端（`network_batch.CLASSES`）
并随数据下发，前端不另存一份：

| 层级 | 条数 | 长度 | 起画 zoom | 文件 |
|---|---:|---:|---:|---|
| `TRUNK_MAIN` 主干管 | 535 | 954 km | 9 | `network_mains.geojson`（1.6 MB，按钮一按就取） |
| `PRIMARY` 一级管 | 2,542 | 1,765 km | 11 | 同上 |
| `SECONDARY` 二级管 | 8,047 | 2,425 km | 12 | 同上 |
| `LATERAL` 支管 | 53,587 | 3,298 km | 14 | `network_laterals.geojson`（3.2 MB，放大到街区尺度才取） |

几何从 `sw_main` 出（已把 26 万条 `sw_edge` 串成干管，`st_linemerge` 后公共端点
只存一次，比逐段落图省 40% 体积），每一级合成**一个** MultiLineString Feature ——
Leaflet 画 6.6 万个 Polyline 对象要卡好几秒。代价是线上没有逐条 tooltip，
管网在这里是底图性质的参照，逐条属性去 Qcap 面板看。

⚠ 这份 GeoJSON 简化过 1 m，**只能落图**。量长度、算坡度、做空间判断一律回 `sw_edge`。

缩范围会同时缩掉车队结论，这是对的：25 年一遇、时限 2 h 下 501 点需 **128 台泵 / 4,059 车次**。
逐档对照因此给两个在册口径：`on_fleet`（全网在册，固定）与 `on_fleet_in_scope`
（只数参与计算的点），缩了范围要看后者，否则缺口会被系统性低估。

### 按片区筛选（官方行政边界）

指挥中心的「筛选」里有一个固定按钮「只看 AL BATEEN 区（阿布扎比市）」，
框定阿布扎比岛上的巴廷区，只看（并只算）这一片的 18 个点。**目前只做这一个片区**，
页面上没有通用片区选择器；后端 `/api/scenario` 的 `district` 参数是通用的，
要再开别的片区只需改页面，不用动后端。片区标签本身对 845 个点全量生成：
`cd backend && PYTHONPATH=src .venv/bin/python -m stormwater.district_batch` 把 845 个点逐点落进
`geoserver_ref.udm_community` 的社区面，按 `districtnameeng` 聚合成片区，
产出 `backend/data/processed/hotspot_district.csv`（逐点标签）与
`district_boundaries.geojson`（122 个含点片区的轮廓，落图用）。845 个点全部命中，
每个点恰好一个片区。批算结果按 `by_district` 单独汇总，
研判简报顶部会标明这份结论只覆盖该片区。

⚠ 搜索框查的仍是台账 `area` 列，**不含**官方片区：输「Al Bateen」只会命中艾因那 3 个点。
岛上那一片走按钮，不走搜索框。

⚠ **不要用台账的 `area` 列当片区**。那一列是各市政区自己填的自由文本，同一个名字
在不同市政区下不是同一片地方，粒度也不一致。以 Al Bateen 为例：

| | 台账 `area='Al Bateen'` | 官方片区 `ADM / AL BATEEN` |
|---|---|---|
| 点数 | 3（AAM-007/008/009） | 18 |
| 位置 | 艾因 Ghafat Al Shwaihi | 阿布扎比岛 |
| 台账里这些点的 `area` 写的是 | `Al Bateen` | `Island` / `Al Wathbah` |
| 有管网数据 | 0 个 | 18 个全部有 |

按 `area` 筛会把岛上真正的巴廷区 18 个点全漏掉，只拿到 3 个结论全是假设值的艾因点。
更要紧的是 **`AL BATEEN` 是全库唯一的重名片区**（ADM 与 AAM 各一个，相距约 130 km），
所以筛选 key 一律带市政区前缀（`ADM / AL BATEEN`），裸名字不是合法取值。

⚠ **台账的「有无排水管网」字段不可单独作准**：与管网几何逐点核对后，ADM 写「无管网」
的 171 个点里有 **135 个**、250 m 内实际有雨水管；写「有管网」的 335 个里有 28 个附近
没有管。旧逻辑直接采信该字段，把其中 130 个点误判为 Qcap=0、全面低估排水能力。
现在只在**完全没有管网数据的区域**才回退到这个字段，并标成低置信度。

**汇水面积 A、径流系数 C、集流时间 tc 已经逐点反算**：
`cd backend && PYTHONPATH=src .venv/bin/python -m stormwater.catchment_batch` 从 `geoserver_ref` 的地籍与地物图层
（`udm_plot` 41 万 / `udm_building` 38 万 / `udm_roadedge` 105 万 / `udm_landscape` /
`udm_parking` / `udm_pedestrianarea`）逐点统计下垫面，产出
`backend/data/processed/hotspot_catchment.csv`。**845 个点全部有值** —— 地籍图层覆盖三个市政区，
不像管网只有 ADM，所以这一项在艾因与西部区同样可用。

| | 口径 | 845 点 最小 / 中位 / 最大 |
|---|---|---|
| **A** | 积水点 Voronoi 单元 ∩ 250 m 圆，**只取其中已开发的部分** | 0.58 / **14.89** / 19.51 ha |
| **C** | 分项面积加权，取值逐条来自 **ADQCC Table 3-8**（屋面 0.85 / 沥青 0.90 / 联锁砖 0.78 / 灌溉绿地 0.15 / 带围墙住宅地块 0.20），未开发沙地用 Table 3-9 四项累加的 0.44 | 0.15 / **0.62** / 0.90 |
| **tc** | ADQCC §3.2.3「入网时间 + 管内流行时间」：入网时间 = 坡面流长 / 坡面流速（**下限 10 分钟**），管内时间 = 剩余流程 / 曼宁满流流速（n = 0.015） | 10.0 / **14.7** / 50.5 min |

坡面流长取 A /(4 × 单元内管长)（管网把汇水区切成宽 A/L 的条带）；坡度优先用管底高程
反算的实测值（437 个点），其余用 DEM 兜底（408 个点）。

对照旧假设：A 12 → 14.89 ha、C 0.75 → 0.62、tc 20 → 14.7 min。C×A 几乎没变
（9.0 → 9.2），所以全网量级站得住 —— **变的是每个点终于各有各的数**。

⚠ **这不是汇水区划分成果**。汇水**边界**用的是「相邻积水点 Voronoi 等分 + 250 m 截断」
这个几何代理，真正的边界要靠高精度地形或二维演算定（清单 §09 仍然缺）。
逐点数值可用于筛查与排序，**不可作为单点设计依据**。

⚠ 未开发沙地（`area_bare_ha`）**不计入 A**：ADQCC §3.3.1 明说 Table 3-8 的取值适用于
「contributing to the network」的范围，而这些地块既无管网接入证据、砂土入渗也快。
845 个点里有 58 个单元过半是沙地，对这些点该选择影响最大 —— 要做敏感性对照，
CSV 里有 `area_incl_bare_ha` 与 `runoff_c_incl_bare` 两列。

⚠ 道路面积是 `udm_roadedge` 边线缓冲 12 m 得到的，不是实测路面多边形。
校核：全网合计折算成每条边线 7.4 m 有效宽度、道路占单元面积 **24.2%**，
在城市路网 20–30% 的常见区间内。

页面把实测与假设用不同颜色的标签分开显示，改参数走「模型输入」抽屉 ——
抽屉里 A / C / tc 与 Q<sub>cap</sub> 各自会标出该组**真正生效**的点数（已实测的点不读这些框）。

**第二道防线（可用调蓄）也已逐点反算**：`cd backend && PYTHONPATH=src .venv/bin/python -m stormwater.storage_batch`
扫 250 m 内的 `pond` / `soakaway` / `sw_reservoirstructure`，产出
`backend/data/processed/hotspot_storage.csv`。2026-09-19 补上深度与尺寸后，
**不再是全库 0，但也只有 43 个点有量**：

| 出处 | 点数 | 含义 |
|---|---|---|
| `measured_no_asset` | 422 | 该片有设施数据，此点 250 m 内没有调蓄设施 |
| `assumed_no_coverage` | 380 | 此点 5 km 内无任何设施记录（艾因 / 西部区全部，另有 **36 个市区点**） |
| `assumed_dimensions` | 42 | 设施位置实测，深度/尺寸库里没有，按标准缺省值目录估算 |
| `measured` | **1** | 台账 `pond.volume` 直接给了容量（36.5 万 m³） |

原先判「无一条通过深度校验」有一半是我们自己漏读：`pond` 23 个池里 **20 个填了
`volume`**，除以几何面积得到隐含深度 1.00–4.42 m（中位 **2.18 m**），正落在 ADQCC
§4.14.1(b) 推荐的 1.8–2.4 m 带内 —— 这是台账实测值，不是假设。剩下确实没有深度的
（渗井 949 座、地下蓄水构筑物 17 座），改用 `storage_dims.py` 的**缺省值目录**补，
每个数都有出处（ADQCC WA-726-1 §4.14.1/§4.12、WA-726-2 图 901–905、Ashghal
PWA IAN 048/14、BS 5911-3 等），逐条落在 `backend/data/processed/storage_asset_dims.csv`
（989 条设施）。完整取值与出处见 **`docs/STORAGE_DIMENSION_BASIS.md`**。

补数对车队结论只动了 2 台泵（25 年一遇 501 点：140 → 138 台，4,396 → 4,389 车次；
这组是 2026-09-19 在**修正坐标之前**的口径对照，只说明补数本身的影响量级，
现行数字见上文 128 台 / 4,059 车次）
—— 501 个计算点里只有 43 个点的 250 m 内有设施，且多数本来就被第一道防线排完。
**第二道防线的 0 从「数据不可用」变成了「确实几乎无处可用」**，这个区别对投资论证是实质性的。

⚠ `assumed_no_coverage` 的 380 个点里有 **36 个在阿布扎比市**（Mussafah 11 · Zayed 9 ·
Al Shahama 9 · Al Wathbah 5 · Island 2，最近设施 5.1–10.1 km），即**设施图层在市区内部
也不连续**，不只是艾因 / 西部区整片没有。这 36 个点的 0 是「没数据」，不是「实测没有」。

⚠ **没有把容量按距离判给积水点之外的口径**：半径卡在 250 m，是因为没有管网连通性证据 ——
一个 66 万 m³ 的调蓄池若按「1 km 内」判给某个点，会一次性抹平它的全部缺口。
⚠ 渗井默认只计**结构空腔**（单座 5.3 m³）；含砾石包裹层的乐观上界 7.5 m³ 记在
`optimistic_m3` 列。渗井多半已在为自己的汇水面服务，算成「额外可用调蓄」有重复计算风险。
⚠ 地下蓄水构筑物的 2.0 m 深度**无当地标准直接支持**，是锚定 ADQCC 高差带的工程判断，
17 座合计 12,285 m³ —— 这是目前假设量的主体，DMT 清单 §03 点名索要实测内净高。

**已知阻塞项**：

1. ~~`gradient` 列作废须自行反算~~ —— **已解决**：`syq-pipe` 的
   `dmt_irrigationstormwater_abudhabi_network.sw_edge` 里 `invert_up` / `invert_dn` /
   `len_m` 已抽出，并带 `invert_valid` 标记（227,678 条 / 87.0% 可用）。
   `pipe_diameter` 实测确认为 **mm**。`manning.py` 仍不做任何自动猜测，
   必须由调用方显式声明 `DiameterUnit` / `GradientConvention`。
2. `manning.py` 的糙率映射**尚未按 ADQCC Table 4-1 更新**（uPVC 0.011 / GRP 0.012 /
   HDPE 0.012 / 有衬混凝土 0.011 / 无衬混凝土 0.013 仅限艾因）。现值偏低
   → Qcap 偏大 → 偏不保守。改完须重跑 `stormwater.qcap_batch`。
3. ~~汇水面积 A 与径流系数 C 无逐点来源~~ —— **已缓解，未解除**：`catchment_batch.py`
   已从地籍与地物图层逐点反算 A / C / tc，845 个点全部有值。但汇水**边界**仍是
   「Voronoi 等分 + 250 m 截断」的几何代理，不是汇水区划分成果：市区等高线 0 条、
   无汇水区图层，**清单 §09 仍然是一级缺口**。逐点数只能横向排序，不能单点做设计。
4. **艾因 AAM 与西部区 DRM 的管网至今未导出**，339 个点没有任何管网几何，
   其中台账声明「有管网」的有 259 个（清单 §01，本轮市区口径下已移出范围）。
   市区内部另有空白（清单 §13，按缺失字段拆为五项）：5 个点最近雨水管在 5.7–27.2 km 外
   （已排除在 501 之外）、8 个点周边收集级管段全缺管底高程、8 个点 250 m 内只有过境干管
   没有 LATERAL/SECONDARY、1 个点仅有的 2 条次干管为平坡。**管径不是瓶颈** ——
   这 17 个点里没有一个是缺管径导致的。
5. Qcap 取的是「250 m 内收集系统级管道的最大满流能力」，属**筛查代理量**，
   不是下游瓶颈分析。要做严格的瓶颈追踪，须先修 `sw_edge` 的流向
   （15,984 条 `unknown` + 1,561 条 `dir_conflict`）。
