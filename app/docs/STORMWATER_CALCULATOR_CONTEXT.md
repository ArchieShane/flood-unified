# 雨水调蓄计算器 — 项目上下文交接文档

> 本文档汇总 2026-09-19 会话中确认的全部事实、数据源现状、计算方法和未决问题，供 code 模式下继续开发使用。
> 所有数值均经实际读取/计算验证，未经验证的推测均已标注「待确认」。

---

## 1. 项目目标

把一份 Excel 筛查模型（`Stormwater model v2.xlsx`）转化为可用的网页计算器，用于：

1. 按「积水点 × 降雨重现期」测算所需调蓄容量
2. 当调蓄能力不足时，测算所需的移动泵车与罐车响应规模（"第三道防线"）
3. 为 846 个真实积水点批量填补目前缺失的**逐点管网能力（Qcap）**

**当前阶段**：设计已完成，功能未实现。设计稿见 `https://claude.ai/artifact/LbHP1SN5pqBb2YSKbxhss4`

**核心价值主张**：现有台账里 `Hotspot Network Design Technical Capacity (Current)` 这一列是区域级一刀切的文本（如「Estimated between 30-39mm」），本项目用管网数据库的 26 万条管段逐段算出真实 Qcap，把这一列换成 846 个可信数字。

---

## 2. 数据源总览

| # | 数据源 | 形态 | 状态 | 用途 |
|---|--------|------|------|------|
| A | `Stormwater model v2.xlsx` | 6 个工作表的 Excel 模型 | 已完整解析 | 计算方法的唯一权威来源 |
| B | PostgreSQL `syq-pipe` 库 | 26 万条管段 + 全资产台账 | 部分探查 | 计算逐点 Qcap、调蓄容量、低洼点 |
| C | 3 份 Hotspot Analysis 台账 | 846 个积水点 | 已完整解析 | 积水点清单、车队资源、官方风险分级 |

**重要**：A 来自迪拜市政局体系，B 和 C 是阿布扎比体系。两者的设计标准和度量单位不同，见 §6.1。

---

## 3. 数据源 A：Excel 计算模型

### 3.1 工作表结构

| 工作表 | 内容 |
|--------|------|
| `Instructions` | 使用说明、颜色图例、局限声明 |
| `Assumptions` | 全局假设参数 + Cf 调整系数表 |
| `IDF_Dubai` | 迪拜 IDF 降雨强度表（7 重现期 × 15 历时） |
| `Hotspots` | 积水点登记表（6 个示例行 + 5 个空行） |
| `Scenario_Calc` | 计算引擎（30 行 = 6 点 × 5 重现期，26 列） |
| `Fleet_Summary` | 按重现期汇总的全网车队需求 |

已产出中文版：`雨水排放模型_v2_中文版.xlsx`（公式与格式原样保留，工作表标签保留英文以免破坏公式引用）。

### 3.2 完整计算链路

对每个「积水点 × 重现期」组合，按以下 6 步计算：

```
1. 历时档位选择   duration = 向下对齐(tc, IDF历时档位)
                  Excel: INDEX(durations, MATCH(tc, durations, 1))
                  ⚠ MATCH type=1 取「小于等于 tc 的最大档位」

2. 降雨强度查表   I = IDF[return_period][duration]            单位 mm/hr

3. 峰值流量       C_adj = MIN(C × Cf[return_period], 1.0)
                  Q = C_adj × I × A / 360                      单位 m³/s
                  （推理公式法 Rational Method；A 单位为公顷）

4. 超量流量       Q_excess = MAX(0, Q - Qcap)                  单位 m³/s

5. 积水体积       V_flood = Q_excess × (duration × 60) × shape_factor
                  V_required = V_flood × sed_factor            单位 m³
                  shape_factor = 0.5（三角形过程线近似）
                  sed_factor  = 1.05（淤积裕量）

6. 缺口与车队     shortfall = MAX(0, V_required - pond_available)
                  rate      = shortfall / drawdown_hours        单位 m³/hr
                  pumps     = dispatch(rate, 12″/8″/6″ 分档表)      见 pumps.py
                  tanker_loads = CEILING(shortfall / tanker_capacity)
```

**注意 step 1 的副作用**：duration 同时决定了 step 2 的强度和 step 5 的历时。tc 向下取档使强度偏高（峰值流量偏保守）但历时偏短（积水体积偏小，**调蓄容量偏不保守**）。这是原模型的已知缺陷，是否改为线性插值见 §7 决策 2。

### 3.3 IDF 表（迪拜，mm/hr）

来源：Dubai Municipality, Stormwater Design Guidelines, Doc. DM-WSA-SRPD-SW1, Version 2, July 2024, Table 0-2。
哈达（Hatta）地区应改用同文件 Table 0-3（强度显著更高，原表未收录）。

历时档位（分钟）：`[5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 360, 720, 1440]`

```json
{
  "2":   [59.90, 45.22, 37.15, 31.10, 23.57, 17.44, 13.87, 10.22,  8.18,  5.92,  4.73,  3.96,  3.38, 1.93, 1.00],
  "5":   [97.41, 76.98, 63.34, 53.54, 41.68, 31.78, 25.35, 18.66, 15.11, 10.81,  8.62,  7.21,  6.22, 3.68, 1.93],
  "10":  [118.00, 97.61, 80.41, 68.58, 54.72, 42.82, 34.22, 25.18, 20.57, 14.60, 11.61,  9.73,  8.44, 5.13, 2.71],
  "20":  [131.23, 115.25, 95.09, 82.01, 67.36, 54.26, 43.46, 31.95, 26.37, 18.54, 14.70, 12.34, 10.79, 6.75, 3.59],
  "25":  [133.66, 120.18, 99.22, 85.92, 71.32, 58.03, 46.52, 34.20, 28.31, 19.84, 15.72, 13.20, 11.58, 7.31, 3.90],
  "50":  [169.22, 152.15, 125.61, 108.78, 90.30, 73.47, 58.89, 43.29, 35.85, 25.12, 19.91, 16.71, 14.65, 9.25, 4.94],
  "100": [209.20, 188.11, 155.29, 134.49, 111.63, 90.83, 72.81, 53.52, 44.32, 31.06, 24.61, 20.66, 18.12, 11.44, 6.10]
}
```

### 3.4 径流系数调整系数 Cf

来源：同文件 Table 0-6（引自 TxDOT, 2011）。`C × Cf` 上限截断为 1.0。

| 重现期 (yr) | Cf |
|---|---|
| 5 | 1.00 |
| 10 | 1.00 |
| 25 | 1.10 |
| 50 | 1.20 |
| 100 | 1.25 |

⚠ IDF 表有 2 年和 20 年两行，但 Cf 表没有对应值。原 Excel 用 `IFERROR(..., 1)` 回退为 1.0。`Fleet_Summary` 只汇总 5/10/25/50/100 五档。

### 3.5 全局假设参数

| 参数 | 默认值 | 性质 | 来源 / 处置 |
|------|--------|------|------------|
| 淤积裕量系数 | 1.05 | 有出处 | DM 导则 Sec 5.1，可固定 |
| 过程线形状系数 | 0.50 | **假设值** | 三角形近似；真值需 InfoSWMM 演算 |
| 目标排空时间 | 2 hr | 运营设定 | 应替换为实际应急响应 SLA |
| 泵车工况扬程折减 | 0.65 | **推算系数** | TDH 10–20 m 下离心泵约为铭牌的 55–75% |
| 泵车出勤占空 | 0.85 | **推算系数** | 加油、转场、清堵占用的时间 |
| 罐车容量 | 15 m³/车次 | **假设值** | 应替换为实际车队单车载量 |

移动泵不再用「全车队一个排量」。额定排量按口径分三档，
额定 = 铭牌最大流量 × 工况折减 × 出勤占空：12″ 751 / 8″ 287 / 6″ 221 m³/hr
（铭牌 1,360 / 520 / 400，出处 Xylem Godwin CD 系列）。三档**都还是铭牌折减值**，
DMT 给出实测排量后填进 `PumpTier.measured_m3hr` 即绕过折减 —— 见 `pumps.py` 与数据需求清单 §02。

派车规则：在总排量不小于需求的组合里，取「总排量 + 台数 × 最小档排量」最小的那个。
原口径（统一 300 m³/hr）可用 `Assumptions(pump_capacity=300)` 复现，回归测试锁着。

### 3.6 示例点 H1 的完整验算（回归测试基准）

输入：`A=12 ha, C=0.75, tc=20 min, Qcap=1.8 m³/s, pond_available=0`

| 重现期 | I (mm/h) | C_adj | Q (m³/s) | 超量 | 所需调蓄 (m³) | 缺口 (m³) | 泵 | 罐车 |
|---|---|---|---|---|---|---|---|---|
| 5 | 53.54 | 0.7500 | 1.339 | 0 | 0 | 0 | 0 | 0 |
| 10 | 68.58 | 0.7500 | 1.715 | 0 | 0 | 0 | 0 | 0 |
| 25 | 85.92 | 0.8250 | 2.363 | 0.563 | 354.6 | 354.6 | 1 | 24 |
| 50 | 108.78 | 0.9000 | 3.263 | 1.463 | 921.9 | 921.9 | 2 | 62 |
| 100 | 134.49 | 0.9375 | 4.203 | 2.403 | 1513.8 | 1513.8 | 3 | 101 |

**实现后必须用这张表做回归测试。**已用 LibreOffice 重算原 Excel 逐格比对确认一致。

### 3.7 原模型的局限声明（必须在 UI 保留）

- 使用推理公式法 + 简化三角形过程线，属**筛查与排序工具**，非最终设计
- DM 导则要求调蓄池和泵站的实际设计必须采用 InfoSWMM（或 DM 认可的等效软件）建模并报批（Sec 5.8）
- 推理公式法仅适用于**汇水面积 < 80 公顷**的汇水区，超过须拆分子汇水区或改用过程线法

---

## 4. 数据源 B：管网 PostgreSQL 数据库

### 4.1 连接信息

```
host:     10.255.254.109
port:     35433
database: syq-pipe
schema:   dmt_irrigationstormwater_abudhabi
user:     postgres
```

> ⚠ 凭据请移入环境变量 / `.env`，不要硬编码进代码库。
> ⚠ 该地址为内网 IP，仅能从公司网络内访问。
> ⚠ `syq-pipe` 是本项目的工作库，内含 `dmt_irrigationstormwater_abudhabi` schema 的完整副本
>   （110 表 / 6,377,256 行，与原始 `pipe` 库逐表核对一致）。原始 `pipe` 库保持只读，不要写入。

### 4.2 Schema 概况

约 115 张表（`syq-pipe` 内实为 110 张），Esri SDE 导出特征（普遍带 `shape_length` / `shape_area` 字段）。

**几何存储已确认（2026-09-19）**：是原生 PostGIS 3.5.2。70 张表带 `geom` 列，类型为 `geometry(MultiPointZ|MultiLineStringZ|MultiPolygonZ, 32640)`，**SRID 全库统一 32640**（WGS 84 / UTM zone 40N，阿布扎比适用），且每张表都建有 GiST 索引。早前"`geometry_columns` 视图不存在 → 不是原生 PostGIS"的判断是错的：该视图在库内存在且有 70 条记录。

表分三类：`ir_*` 灌溉资产、`sw_*` 雨水资产、以及两者共用的通用资产表（`pipeline`、`chamber`、`manholechamber` 等）。

### 4.3 核心表：`pipeline`（84 列，260,725 行）

**Qcap 计算所需的字段全部齐备：**

| 字段 | 类型 | 用途 |
|------|------|------|
| `pipe_diameter` | double precision | 断面尺寸 ⚠ 单位待确认（mm 还是 m） |
| `gradient` | double precision | 坡度 ⚠ 单位待确认（小数还是 1:X 的 X） |
| `invert_level_upstream` | double precision | 上游管底高程 |
| `invert_level_downstream` | double precision | 下游管底高程 |
| `difference_in_invert` | double precision | 管底高差（可用于反算坡度校验） |
| `pipe_length` | double precision | 管长 |
| `shape_length` | double precision | 几何长度（与 pipe_length 交叉校验） |
| `pipe_material` | varchar | 管材 → 映射曼宁糙率 n |
| `backfill_material` | varchar | 回填材料（无关） |

**曼宁公式**（满流圆管）：

```
Q = (1/n) × A × R^(2/3) × S^(1/2)
其中 A = π(D/2)²,  R = D/4,  S = 坡度
```

验证示例：DN1200 混凝土管，S=0.002，n=0.013 → A=1.131 m², R=0.3 m → **Q ≈ 1.74 m³/s**（接近原 Excel 示例点填的 1.8）

### 4.4 管材分布与糙率映射（已完整统计，260,725 行）

| pipe_material | 条数 | 占比 |
|---|---|---|
| GRP | 140,034 | 53.7% |
| UPVC | 59,990 | 23.0% |
| Undefined | 23,293 | 8.9% |
| Reinforced Concrete | 12,053 | 4.6% |
| Other | 7,305 | 2.8% |
| Vitrified Clay | 6,409 | 2.5% |
| uPVC | 4,693 | 1.8% |
| PVC | 4,575 | 1.8% |
| HDPI | 608 | 0.2% |
| (null) | 535 | 0.2% |
| AC | 384 | 0.1% |
| Concrete | 380 | 0.1% |
| Ductile Iron | 163 | — |
| `' '`（空格） | 116 | — |
| GRC | 94 | — |
| HDPE | 39 | — |
| Glass Reinforced Epoxy | 34 | — |
| Steel | 8 | — |
| `'160 mm ∅ uPVC SLOTTED DRAIN PIPE, PN10\r\n'` | 8 | — |
| NDRC | 3 | — |
| Solid_uPVC | 1 | — |

**已识别的脏数据：**
- `UPVC` / `uPVC` / `Solid_uPVC` 同物异名
- `Concrete` / `Reinforced Concrete` / `GRC` / `NDRC` 需归并策略
- `HDPI`(608) 与 `HDPE`(39) 数量倒挂，**`HDPI` 基本确定是拼写错误**
- 8 条把完整规格描述塞进材质字段
- 无有效材质合计 **31,249 条（12.0%）**：`Undefined` + `Other` + null + 空格

**糙率映射（可直接使用）：**

```sql
with m as (
  select *, btrim(coalesce(pipe_material, '')) as mat
  from dmt_irrigationstormwater_abudhabi.pipeline
)
select *,
  case
    when mat ~* 'grp|glass reinforced|^gre$'  then 0.010   -- 玻璃钢/环氧
    when mat ~* 'pvc'                         then 0.010   -- UPVC/uPVC/PVC/含描述文本
    when mat ~* 'hdp[ei]|polyethylene'        then 0.010   -- HDPE（含 HDPI 拼写错误）
    when mat ~* 'vitrified|clay'              then 0.012   -- 陶土
    when mat ~* 'ductile|steel|^ac$|asbestos' then 0.012   -- 球墨铸铁/钢/石棉水泥
    when mat ~* 'concrete|^grc$|^ndrc$'       then 0.013   -- 混凝土类
    else 0.013                                             -- 未知，保守取值
  end as manning_n
from m;
```

⚠ 分支顺序有意义：玻璃钢分支必须在混凝土分支之前，否则 `Glass Reinforced Concrete`(GRC) 与 `Glass Reinforced Plastic`(GRP) 会被同一规则吃掉。

**敏感度**：n 从 0.010 到 0.013，Qcap 相差约 18%。未知材质统一取 0.013 方向保守（Qcap 偏小 → 更易判定超载）。
**待确认**：ADQCC 导则是否规定统一取 n = 0.013（部分导则不看材质，按长期糙率统一设计）。若是，上述映射作废，全取 0.013。

### 4.5 其他可用的表

| 表 | 关键字段 | 用途 |
|---|---|---|
| `boxculvert` (73列) ⚠**空表 0 行** | `width`, `culvert_top_level`, `culvert_bottom_level`, `culvert_slope`, `length` | 方涵 Qcap（高度 = top − bottom） |
| `ditch` (68列) ⚠**空表 0 行** | `ditch_bottom_width`, `ditch_top_width`, `top_level`, `bottom_level`, `gradient_avg` | 明渠 Qcap（梯形断面） |
| `gratedchanneldrainage` (76列) | `channel_width`, `diameter`, `cover_level_upstream/downstream`, `outlet_level` | 线性排水沟 |
| `pond` (68列) | `shape_area`, `bottom_floor_level`, `cover_level`, `side_slope`, `floor_slope` | **可用调蓄容量** |
| `sw_reservoirstructure` (67列) | `max_water_level`, `bed_level`, `reservoir_height`, `free_board_height_m`, `shape_area` | **可用调蓄容量**（更直接） |
| `soakaway` (70列) | `soakaway_diameter/length/width`, `invert_level`, `bottom_level` | 渗井调蓄 |
| `collectivetank` (64列) | `tank_height`, `invert_level`, `cover_level` | 集水箱 |
| `manholechamber` (78列) ⚠**空表 0 行** | `ground_level`, `cover_level`, `invert_level` | ~~**低洼点识别**~~ —— 表是空的，低洼点只能改用 `chamber`(156行) / `catchbasin`(34,292行) / DEM |
| `chamber` (87列) | `groundlevel`, `coverlevel`, `invertlevel` | 同上 |
| `sw_junction` (64列) | `cover_level`, `invert_level` | 雨水节点，拓扑 |
| `sw_node` (25列) | — | 节点（列最少，可能是拓扑核心表） |
| `outfall` (68列) | `invert_level`, **`wadi_average_level`**, `pipe_diameter` | **下游顶托判断**（旱谷水位 vs 排放口管底） |
| `catchbasin` (68列) | `cover_level`, `invert_level`, `cover_size` | 雨水口 |
| `inlet` (79列) | `invert_level`, `outlet_diameter`, `inlet_size` | 进水口进水能力 |
| `movable_pump` (60列) ⚠**空表 0 行** | `discharge_flange_size`, `suction_size` | **移动泵车台账**（流量字段待查） |
| `sw_pumpingstationstructure` (126列) | `ave_daily_capacity`, `ult_daily_capacity`, `design_hs_level` | 固定雨水泵站能力 |
| `pumpingstation` (115列) | `total_capacity`, `ave/min/ult_daily_capacity` | 泵站总体能力 |
| `weather_station` (55列) ⚠**空表 0 行** | — | 可能有雨量记录，用于校核本地 IDF |

### 4.6 数据库待办查询（尚未执行）

```sql
-- ① pipeline 全 84 列，重点找：雨水/灌溉区分字段、上下游节点字段、几何列 udt_name
select ordinal_position, column_name, data_type, udt_name
from information_schema.columns
where table_schema = 'dmt_irrigationstormwater_abudhabi'
  and table_name = 'pipeline'
order by ordinal_position;

-- ② 单位与数据质量确认（最高优先级）
select count(*) as total,
       count(pipe_diameter) as dia_n,
       count(gradient) as grad_n,
       count(invert_level_upstream) as ivu_n,
       count(pipe_length) as len_n,
       count(pipe_material) as mat_n,
       min(pipe_diameter) as dia_min, max(pipe_diameter) as dia_max,
       min(gradient)      as grad_min, max(gradient)      as grad_max,
       min(pipe_length)   as len_min,  max(pipe_length)   as len_max
from dmt_irrigationstormwater_abudhabi.pipeline;
```

**② 的判读规则：**
- `pipe_diameter` 最大值约 2400 → 单位是 **mm**；约 2.4 → 单位是 **m**
- `gradient` 范围在 0.0005–0.05 → **小数坡度**；范围在 100–2000 → **1:X 的分母**（需取倒数）

这两项判错会导致 Qcap 出现数量级错误。

### 4.7 数据库的关键缺口

| 缺失项 | 说明 |
|---|---|
| **汇水面积 A** | 无 catchment / subcatchment 表。需用 DEM + 管网拓扑自动划分 |
| **径流系数 C / 土地利用** | 无相关图层 |
| **管网拓扑** | `pipeline` 的上下游节点字段名未知，待 ① 确认。无拓扑则无法找瓶颈段、无法算 tc 的管道段 |
| ~~**几何数据可用性**~~ | ~~非原生 PostGIS，几何存储方式待确认~~ → **已解决**：原生 PostGIS 3.5.2，SRID 32640，见 §4.2 |
| **雨水/灌溉区分** | `pipeline` 混装两套管网（UPVC+PVC 合计 6.9 万条多为灌溉支管），必须先筛选 |

---

## 5. 数据源 C：846 个积水点台账

三份 Excel，同一模板但列结构不一致：

| 文件 | 区域 | 主工作表 | 行数 | 列数 |
|---|---|---|---|---|
| `20260909 ADM Stormwater Hotspot Analysis v4.4.xlsx` | Abu Dhabi | `ADM Hotspot Inventory` | 507 | 63 |
| `20260909_AAM_Stormwater_Hotspot_Analysis_v4.2.xlsx` | Al Ain | `Analysis` | 184 | 63 |
| `20260909_DRM_Stormwater_Hotspot_Analysis_v4.2.xlsx` | Al Dhafra | `Analysis` | 155 | 61 |

**表头在第 3 行**（第 1–2 行是分组标题和填写说明），读取时须 `header=2`（pandas 0-based）。

### 5.1 公共字段结构

```
A  Hotspot_ID / #              序号
B  Hotspot_Area                区域名
C  Hotspot_Location            位置描述（含阿拉伯语）
D  Latitude                    纬度
E  Longitude                   经度
F  Criticality                 Very High / High / Medium / Low
G  Developer_Private_Area      是否开发商私有区域
H  Developer Name
I  Network_Available           Yes / No
J  Root_Cause                  根因
K  Root_Cause_Details          根因详述
---- 应急响应计划 A（≤1-in-10yr 事件）----
   Manpower_Qty, Manpower Active-duty AED, Manpower Standby AED, Total Manpower_Budget_AED
   Pumps_Qty, [ADM 额外分档: Diesel Pump 12/8/6 inch Qty + AED/Day]
   Total Pump Daily Rate, Total Pump Active Duty AED, Total Pump Standby AED, Pumps_Budget_AED
   Tankers_Qty / Suction Tankers (NO'S), Tankers_Budget_AED
   Concrete_Barrier_AED, Sand_Barrier_AED, Detention_Ponds_AED, Collection_Chamber_AED
---- 应急响应计划 B（>1-in-10yr 事件）----
   Manpower_Qty2, Manpower_Budget_AED3, Pumps_Qty4, Pumps_Budget_AED5,
   Tankers_Qty6, Tankers_Budget_AED7
---- IDD 干预计划 ----
   IDD Intervention Planned, IDD_Design_Category, [AAM/DRM: Design Solution],
   Type of Intervention, Additional Budget Requirment, Additional Budget Requested,
   Hotspot Network Rain Design Event (Current),
   Hotspot Network Design Technical Capacity (Current),      ← Qcap 目标列
   Hotspot Network Rain Design Event (Future),
   Hotspot Network Design Technical Capacity (Future),
   Flood Volume Capacity after Intervention,
   Associated recovery time after intervention,
   Planned_IDD_Mobilization_Date, [ADM: Phase1 Completion Date], Planned_IDD_Completion_Date,
   Project Stage, Approval from IDAS secured, [ADM: Design Description],
   Actual_Mobilization_Date, Actual_Completion_Date, Intervention status, IDD_Owner_Name
---- 各区独有尾列 ----
   AAM: Is_Private_Developer, Root_Cause_Category, IDD_Covered, IDD_Completion_Date2, IDD_Timeline_Status
   DRM: Project, Consultant, Notes
   ADM: Column1
```

**响应时限要求**（表头分组标题）：
- 计划 A（≤1-in-10yr）：Very High < 2h，High < ...（原文被截断，需从文件重读完整）
- 计划 B（>1-in-10yr）：Very High < 4h，High < ...

### 5.2 统计汇总

**积水点总数 846**

| 区域 | 总数 | Very High | High | Medium | Low |
|---|---|---|---|---|---|
| ADM | 507 | 36 | 129 | 325 | 16 |
| AAM | 184 | 89 | 60 | 32 | 3 |
| DRM | 155 | 8 | 62 | 50 | 35 |
| **合计** | **846** | **133** | **251** | **407** | **54** |

（DRM 与 ADM 的数字已做大小写/空格归一，原始值见 §5.5）

**根因分布**

| 区域 | 分类 |
|---|---|
| ADM | 管网需升级 335 / 无排水管网 171 |
| AAM | 设计能力不足 139 / 无排水管网 34 / 地形与道路工程 11 |
| DRM | 设计能力不足 108 / 无排水管网 36 / 地形与道路工程 11 |

**管网可用性**：ADM 有 335 / 无 171；AAM 有 139 / 无 45；DRM 有 120 / 无 35

### 5.3 已配置车队资源（计划 A）

| 区域 | 泵总数 | 12″ | 8″ | 6″ | 罐车 | 人力 |
|---|---|---|---|---|---|---|
| ADM | 844 | 144 | 121 | 579 | 165 | 0 |
| AAM | 47 | — | — | — | 170 | 0 |
| DRM | 99 | — | — | — | 192 | 588 |
| **合计** | **990** | 144 | 121 | 579 | **527** | 588 |

AAM/DRM 未按口径分档（列存在但全为 0），泵型分布未知。

### 5.4 费率与时长假设（ADM `ADM Rate Assumptions - Hourly` / `Assumptions`）

| 资源 | Duty AED/hr | Standby AED/hr |
|---|---|---|
| 12″ 泵 | 90 | 45 |
| 8″ 泵 | 60 | 30 |
| 6″ 泵 | 36 | 18 |
| 电泵 & 发电机 | 40 | 20 |
| 人工 | 18 | 9 |
| 罐车 | 250 | 125 |

**时长假设**：活跃降雨 10 天 + 20% 应急系数 = 12 天；待命 90 天（3 个月）；每天 24 小时；待命成本系数 0.5。
**排放口与倒虹吸附加**：12″ 泵 69 台，8″ 泵 20 台，人力 50 人（按排放口数量的 50%）。

⚠ **此处只有租金费率，没有泵的流量（m³/hr）。** 原模型假设的「单泵 300 m³/hr」已撤销，改为按 12″/8″/6″ 分档（§3.5）；但分档值仍取自厂商规格书的铭牌折减，**不是实测**。数据库 `movable_pump` 表为空表（0 行），实测排量仍需 DMT 提供 —— 数据需求清单 §02。

### 5.5 数据质量问题清单（合并前必须处理）

**枚举值大小写与空格不统一**

| 字段 | 原始值 | 条数 |
|---|---|---|
| ADM `Criticality` | `'Medium   '`（3 个尾随空格） | 310 |
| ADM `Criticality` | `'Medium'` | 15 |
| DRM `Criticality` | `'High'` / `'high'` / `'high '` | 31 / 30 / 1 |
| DRM `Criticality` | `'Low'` / `'low'` | 11 / 24 |
| DRM `Criticality` | `'Very High'` / `'very high'` | 3 / 5 |

**根因拼写不统一**
- ADM: `Absense of Drainage Network`（拼写错误）
- AAM: `Absence of Drainage Network`
- DRM: `Absence of drainage network`
- AAM: `Inadequate Design Capacity` vs DRM: `Inadequate design capacity`
- DRM: `Topography & Road Engineering   `（尾随空格）

**数值字段被填成自由文本**
- `Hotspot Network Design Technical Capacity (Current)`：`Estimated between 30-39mm`、`Absense of Drainage Network`、`No`、`N/A`、`44.88mm & check on 51.12mm`
- `Flood Volume Capacity after Intervention`：`POST detailed design`、`the information will be provided by end of next week`
- `Additional Budget Requested`：`AED450M 2026 cashflow required, email to SW Taskforce 28 July`
- 大量 `TBC`、`N/A`、`-`、`future project`

**串列错误**
- ADM `Intervention status` 列存的是数字 `47`、`49`、`119`，而非规定的枚举值（Not Started / On Track / Delayed / Completed）

**口径不一致**
- ADM 的 `Assumptions` 汇总表按 251 个点统计，但 `ADM Hotspot Inventory` 有 507 行。从 sheet 名（`251`、`Full List (inc previous 255)`）推断为「原 251 个 + 新增 255 个」，汇总表未跟上新增部分
- ADM 文件共 10 个工作表，存在多份重复清单（`Full List`、`ADM List`、`251`、`Sheet2`），需确认哪份是权威版本

### 5.6 关键发现：Qcap 列是空心的

`Hotspot Network Design Technical Capacity (Current)` 的实际填充情况：

| 区域 | 非空条数 | 取值分布 |
|---|---|---|
| ADM | 343 | `Estimated between 30-39mm` × 172；`Absense of Drainage Network` × 171 |
| AAM | 161 | `23mm depth for 24hr` × 161（**全部相同**） |
| DRM | 47 | `51.12 mm depth for 24hr` × 28；`No` × 14；`44.88mm & check on 51.12mm` × 5 |

**没有任何一个积水点是单独评估的**，全部套用区域级估值。这正是本项目要填补的缺口。

### 5.7 合并后的标准表（已完成）

`backend/src/stormwater/hotspots.py` → `backend/data/processed/hotspots_845.csv`，845 行 × 68 列，可重跑。

⚠ **ADM 的坐标不取自 Excel**（2026-09-21 改）。ADM v4.4 的 `Latitude`/`Longitude` 两列
相对名称列**整体上移了一行** —— 第 n 行的名字配的是第 n+1 个点的坐标。第 1 行
`Al Shahama Tunnel` 的坐标落在 5 km 外的亚斯桥；`ايكاد 3`（ICAD 3，穆萨法 54.49°E）
被配上 54.83°E 瓦特巴「طريق الحفار」的坐标，差 35 km。改用 DMT 2026-09-20 交来的
`backend/data/FW__Hot_Spots_506/506 Hot Spots AUH.kmz`：每个 Placemark 的名称、点几何、
`LATIDUE`/`LONGITUDE` 属性同出一行且互相自洽；按序号 `م` ↔ `#` 对齐后名称 350/506 逐字相同、
服务中心 ↔ `Hotspot_Area` 499/506 对角、坐标与台账错一行的对照 494/505 逐位相同。
**506 个点里 505 个位置变了，中位位移 1.86 km，修正前的 ADM 逐点空间结果全部作废。**
新增三列 `coord_source` / `fw_priority_raw` / `fw_center_raw`。

**行数口径改为 845**：ADM 表 Excel 第 510 行 `#` 列写的是 `Outfalls`、区域写 `Across`，
只填了 72 台泵和待命费用，是跨排放口的汇总行，不是积水点。剔除后 ADM 泵车合计 772，
`772 + 72 = 844` 与原表对上；§5.2 的分级合计 `133+251+407+54 = 845` 也本就是 845。

**位置信息**：845 个点全部有 WGS84 经纬度，无缺失、无越界、无经纬度颠倒。
原表里 20 个解析失败的值不是缺失，是脏写法，已全部修复并在 `qc_flags` 留痕。

| `qc_flags` 标记 | 条数 | 含义 |
|---|---|---|
| `status_numeric_dropped` | 53 | ADM `Intervention status` 装的是数字（`122.0`），与 AAM/DRM 的状态词不是一回事，判定错列并置空 |
| `coord_low_precision` | 29 | 纬度小数位 ≤3（≈ >100 m，其中 5 个只有 2 位 ≈ 1.1 km），**匹配最近管段前必须重新定位** |
| `coord_repaired_degree` | 15 | AAM 值形如 `' 24.260988°'` |
| `coord_repaired_nbsp` | 4 | ADM 值前置不间断空格 `'\xa024.303228'` |
| `coord_duplicate` | 2 | ADM-505（Mussafah ايكاد 3）与 ADM-506（Al Wathbah 民防学院）坐标完全相同，相距几十公里，**必有一个错，待人工核实** |
| `coord_repaired_pair_split` | 1 | AAM `Hotspot_ID` 174 的纬度格里粘的是 `24.516553,55.498937` |

**枚举归一**（原文一律保留在 `*_raw` 列）：Criticality 大小写变体、`Permenent`→`Permanent`、
`On Tack`→`On Track`、`NO/YES`→`No/Yes`。Root_Cause 归到 AAM/DRM 的三分类，
其中 ADM 的 `The network needs an upgrade` 与 `Inadequate Design Capacity` 判定为同义 ——
**这是唯一一处跨区语义合并**，不认可可直接改 `normalize_root_cause`。

**坐标系提醒**：本表是 EPSG:4326，管网库是 SRID 32640（UTM 40N）。空间关联必须
`ST_Transform(ST_SetSRID(ST_MakePoint(lon, lat), 4326), 32640)`，不能直接用经纬度比距离。

---

## 6. 两个必须先解决的方法论冲突

### 6.1 设计标准：迪拜 DM vs 阿布扎比 ADQCC

积水点位于阿布扎比，执行标准是 **ADQCC WA726-1（2022 年 4 月）**（从 ADM 台账 `Hotspot Network Rain Design Event (Current)` 列读出）：

| 事件 | 降雨量 | 适用范围 |
|---|---|---|
| 1-in-5 yr | 44.88 mm / 24hr | 全部区域 |
| 1-in-10 yr | 51.12 mm / 24hr | 主干道、宫殿、渗井区 |
| 未来目标 | 1-in-10 yr + 20% 气候变化系数 ≈ **61.34 mm / 24hr** | 规划目标 |

§3.3 的迪拜 IDF 表**不适用于这批积水点**。需要获取 ADQCC 的完整 IDF 表（如果存在），或确认只能使用 24 小时总量口径。

### 6.2 计算范式：峰值流量法 vs 24 小时总量法

| | 迪拜 Excel 模型 | ADM 现行台账 |
|---|---|---|
| 输入 | 降雨强度 mm/hr | 降雨深度 mm/24hr |
| 方法 | 推理公式法（Rational Method） | 总量法 |
| 输出 | 峰值流量 m³/s | 能扛住多少 mm 降雨 |
| 关注 | 瞬时峰值是否超载 | 一场雨的总量能否消纳 |

两者**不能直接换算**——换算需先假定降雨时程分配曲线（如 SCS、Huff 或 ADQCC 指定的设计雨型）。

**决策点**：计算器按哪套走？
- 走 24 小时总量法：合规、与现有台账口径一致、结果可直接回填
- 走推理公式法：能算出峰值流量和逐点泵车数，但产出无法直接填回官方表格
- 两者并行：工作量翻倍，但能同时服务合规报送和应急调度

---

## 7. 待决策事项

| # | 决策 | 影响 |
|---|---|---|
| 1 | **计算范式**：ADQCC 24 小时总量法 vs 推理公式法峰值 vs 并行 | 决定整个数据模型和 UI 结构 |
| 2 | **IDF 历时**：向下取档（与原 Excel 一致）vs 线性插值（更准） | 插值更准但结果与原 Excel 对不上 |
| 3 | **糙率取值**：按材质映射 vs 统一 0.013 | 取决于 ADQCC 导则是否有统一规定 |
| 4 | **C 值**：是否内置土地利用 → C 值对照表 | 内置可降低误填，但需确认采用哪张表 |
| 5 | ~~**罐车周转**：是否纳入往返时间和车辆数~~ **已定：纳入** | 页面新增「罐车单车往返周转（min/趟，默认 60）」；辆数 = ⌈车次 / ⌊T×60/周转⌋⌉，车次与辆数分列。⚠ 60 min **本项目无数据支撑**，待 DMT 给实际周转记录 |
| 6 | **多套 IDF**：是否支持数据集切换（Hatta / 各酋长国） | 影响数据层设计 |
| 7 | **风险评分**：沿用台账现成的 Criticality vs 重建 5 项 0–3 打分 | 建议沿用 Criticality，已是官方口径 |

---

## 8. 下一步任务清单

### 阶段一：数据摸底（阻塞后续所有工作）

- [ ] 执行 §4.6 查询 ②，确认 `pipe_diameter` 和 `gradient` 的单位
- [ ] 执行 §4.6 查询 ①，找出：雨水/灌溉区分字段、上下游节点字段、几何列类型
- [ ] 查 `movable_pump` 表全字段，找 12″/8″/6″ 泵的实际流量
- [ ] 确认 ADQCC WA726-1 是否规定统一糙率 n
- [ ] 确认 ADM 那 10 个工作表里哪份清单是权威版本

### 阶段二：数据清洗与合并

- [x] 三份台账合并成一张 **845** 行标准表：列对齐、枚举归一、数值从自由文本中抽取
      → `backend/src/stormwater/hotspots.py` → `backend/data/processed/hotspots_845.csv`，见 §5.7
- [ ] 建立 `pipeline` 的糙率映射列（物化，不要每次查询现算）
- [ ] 筛出雨水管网子集（排除灌溉管）
- [ ] 批量计算全网逐段 Qcap（圆管 + 方涵 + 明渠三套公式）

### 阶段三：空间关联

- [ ] 按积水点经纬度匹配到最近/下游管段
- [ ] 沿拓扑找每个积水点的下游瓶颈段（Qcap 最小值）
- [ ] 从 `pond` / `sw_reservoirstructure` / `soakaway` 计算各点可用调蓄容量
- [ ] 用 `outfall.wadi_average_level` vs `invert_level` 标记受顶托风险的排放口

### 阶段四：计算器实现

- [ ] 实现 §3.2 的 6 步计算链
- [ ] 用 §3.6 的 H1 表做回归测试
- [ ] 单点快算界面（设计稿已完成）
- [ ] 多点批量 + 本场总账

### 仍无解的缺口

- [ ] **汇水面积 A**：需 DEM + 管网拓扑自动划分汇水区
- [ ] **径流系数 C**：需土地利用/不透水率数据
- [ ] **历史积水记录**：数据库和台账均无，需从运维工单或应急出勤记录另找

---

## 9. 环境备注

> 2026-09-19 更新：原记录的主机地址 `10.255.254.103` 是笔误，正确地址是
> **`10.255.254.109`**。下面的「无法访问」结论由此而来，现已作废。

- 本机（macOS，code 模式）**可直连** `10.255.254.109:35433`，已实测通过
- 云端沙箱仍无法访问该内网地址；需要云端执行的查询仍得回贴结果
- 凭据放在 `.env`（见 `.env.example`），不进代码库

---

## 10. 已产出物

| 文件 | 说明 |
|---|---|
| `雨水排放模型_v2_中文版.xlsx` | 原模型中文版，公式与格式原样保留 |
| `https://claude.ai/artifact/LbHP1SN5pqBb2YSKbxhss4` | 计算器设计稿（数据清单 + 计算链路图 + 界面设计） |
| 本文档 | 完整上下文交接 |

---

*生成于 2026-09-19*
