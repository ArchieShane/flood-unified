# tools — 防线2+ 的挖塘流水线

三态对比（`swbrain/threelines.py`，控制台「防线2+ 三态对比」页）读的是 `out/three_lines/` 下的
`.rpt` 和 `.nodes.parquet`。那些产物由这里的脚本跑出来，**一次几十分钟**，所以和只读产物的
`swbrain` 模块分开放。

## 数据从哪来

| 用途 | 路径 | 说明 |
|---|---|---|
| 客户 1D 水力模型 | `[dmt_model].dir` / `SWMM_Export/*.inp` | 2026-09-20 交付。Mussafah 18 MB、Shaliela 87 MB、RB3 142 MB |
| 客户管网 shapefile | `[dmt_model].dir` / `<模型>/` | 管线、检查井、雨水口、出水口、泵、湿井、塘。UTM 40N，无 `.prj`；**RB3 那份缺全部 `.dbf`** |
| 5 m DTM | `[terrain].dtm` | 只在 `swbrain/ponds.py`（另一条 MILP 路线）里用 |
| 用地 | makani 库 `public.ud_plot` / `public.bas_building` | 只读。见 `swbrain/landuse.py` |

工作目录默认 `out/l2plus/swmm`，放 `.inp` / `.rpt` / `.out`。这些是客户模型的派生物且很大
（RB3 一次运行产出 135 MB 的 `.out`），不入库。换目录设环境变量 `SWBRAIN_SWMM_WORK`。

## 两条链

**一维链**（防线1/2/2+，出街道积水量、积水节点、仍淹热点）
**1D/2D 链**（出最大积水面积——一维给不出这个指标）

## 跑的顺序

```bash
python tools/strip_baseline.py          # ① 从客户「治理后」模型剥离缓解措施 → *_baseline.inp
python tools/run_l2v.py --baseline      # ② 跑现状① 防线1（既有管网）
python tools/run_l2v.py --state L2      #    跑现状② 防线2（客户交付原件，不用改）
python -m swbrain.landuse               # ③ 导出可用地块 / 禁挖地块 / 建筑轮廓
python tools/dig_pond4.py [汇水区]      # ④ 按用地约束选址定容 → *_L2v.inp + *.L2P.ponds.geojson
python tools/run_l2v.py                 # ⑤ 跑现状③ 防线2+（默认），落到 out/three_lines/
python tools/check_land.py              # ⑥ 独立复核：塘压到建筑没有、离路多远、落在什么地块上
python tools/build_l2p.py both          # ④' 两种构筑物形式各建一套（箱涵 / 地面开敞塘）
python tools/run_l2v.py --state L2B     #    跑地下箱涵那一态
python tools/run_l2v.py --state L2L     #    跑地面开敞塘那一态
python -m swbrain.threelines            # ⑦ 四态对比表
```

### 1D/2D（需要 anuga-swmm:1.0 镜像）

```bash
docker build -t anuga-swmm:1.0 -                 # FROM anuga:4.0.1 + swmm-toolkit
python tools/couple_2d.py <区> <态>              # 抽溢流 Q(t)、按塘的表达处理地形、裁 DEM
python tools/make2way.py  <区> <态>              # 生成耦合用 .inp（ALLOW_PONDING 关掉）
MSYS_NO_PATHCONV=1 docker run --rm -v <WORK>:/work anuga-swmm:1.0     python /work/c2d/couple2way.py <区> <态> 15 20 60     # 双向耦合
python tools/natural_sinks.py <区>               # 水自然往哪汇 → 不用泵的塘该挖在哪
```

## 换降雨场景

客户交付的三个 `.inp` **没有 `[SUBCATCHMENTS]`**：产流是顾问在上游算好、按节点注入的入流序列。
所以**在那些模型里改雨量计不会改变任何结果**——雨量计是摆设。要换降雨场景，必须重建产流层。

```bash
python tools/runoff.py <区> --P 61.34 --dur 24        # 只算不写，用来对账
python tools/rain_scenario.py <区> --P 61 --dur 2 --tag R61D2   # 整条链重走
```

`tools/runoff.py` 用的是**客户自己的参数与方法**（Catchment 图层里全有）：

| | 来源 |
|---|---|
| 损失 | SCS 径流曲线法，逐汇水区 `SCS_CN`（`LOSS_ME = "SCS CN"`） |
| 汇流 | SCS 单位线，逐汇水区 `TC` 分钟（`UHM = "SCS Unit Hydrograph"`） |
| 面积 | `A_UNIFIED`（m²）—— **不是 `A`**，Shaliela 用 `A` 会漏掉 62% 的产流量 |
| 出流 | `OUT_NODE` |

**换雨之前必须先过复现这一关**（用他们的雨 61.34 mm / 24 h）：

```
反算出的设计雨深      61.34 mm，与 Parsons 报告一致（= 雨量计 51.12 × 1.2 气候变化）
产流量                Mussafah +0.01%　Shaliela +0.03%
全网入流洪峰          Shaliela −0.0%，峰现时刻三个区都一致
整条链 L1 街道积水    4,020 vs 用他们入流的 4,059 → −1.0%
```

### 踩过的坑

- **卷积错开一格**：`inc = diff(exc, prepend=0)` 让响应从该格**末端**起算，整条过程线晚一个时间步。
  改 `append` 后峰现时刻对交付结果的差归零。
- **步长**：5 min 解不出 `Tc = 10 min` 的单位线，削峰 5.2%。用 1 min。
- **节点名**：图层写 `RD 34`（空格），模型里是 `RD_34`（下划线），直接比会把整片汇水丢掉。
- **出流节点不在模型里**：剔除，但**必须报出丢掉多少水**，不能静默丢。
- **RB3 缺 .dbf**：面积由每个节点交付的水量反算（总量因此精确保持）；`TC` 由交付的全网过程线
  **标定**为 30 min —— 沿用另两区的中位数 10 min 会让洪峰高 **62.9%**。这是标定值，不是图层里的数。

### 为什么不能只换入流

塘的排空规则是按雨峰调的（24 h 雨峰在第 12 h 所以排空从第 14 h 起判；2 h 雨峰在第 1.1 h，
沿用会让塘空等十几个小时），而塘位本身又是按 L1 的积水分布聚类出来的。
所以 `rain_scenario.py` 把整条链重走：新雨 → 新的 L1 → 重新选址定容 → 两种构筑物。

## 分级口径（客户的 Surface Water Management Levels）

| 层 | 定义 | 对应模型 |
|---|---|---|
| **L1** | 既有永久设施：管线、**泵站**、出水口、雨水口、坝、Wadi、明渠、Geocell、**既有塘** | `*_baseline.inp` |
| **L2** | 临时/事前措施（CAPEX / IDD / 市政三类） | 下面两类是它的实例 |
| **L2+ ①** | Parsons **规划设计报告**已设计的（尚未建成） | `*.inp`（客户交付原件） |
| **L2+ ②** | 本系统设计的 | `*_L2box.inp` / `*_L2lag.inp` |
| L3 | 事件中的应急资源（罐车、移动泵、吊车） | 不在本推演 |

既有泵站属于 **L1**，不要剥掉——Mussafah 的 `RD_34` / `CH2` 是两座既有泵站的湿井，
不是塘（各约 10.5 m² × 7 m），带 4 台泵。

## 塘到底放在一维还是二维

判据只有一条：**一份水在同一时刻只能属于一个控制体**。一维蓄水节点的 `A_s(h)` 与
二维洼地的 `∬(η−z)dA` 是同一个物理库容的两种写法，两边都有就是重复计量——
这是质量守恒的错误，不是建模风格的选择。

| | 塘在哪 | DEM | 需要 |
|---|---|---|---|
| 离线箱涵 / 泵送的筑堤塘 | 一维 | 不挖（筑堤的抬成实心障碍） | 单向耦合够 |
| 与地表有交互的下挖式开敞塘 | 二维 | 挖到塘底，**一维里必须先删掉蓄水节点** | 双向耦合 |

本项目的塘全部留在一维，理由不是图省事：它们离积水点 1.3–8 km、客户模型没有
`[SUBCATCHMENTS]`（塘自己那片汇水的产流根本不存在）、进水只有专用管或泵——
与地表没有模型看得见的交互。

双向耦合的价值在**街道那一侧**：单向耦合里水从井盖冒出去就再也回不来，
地表水只涨不退，淹没范围被系统性高估。

## 各脚本

- **`workdir.py`** 工作目录解析。
- **`strip_baseline.py`** 客户交付的是「治理后」模型（含塘、泵、集水井）。防线1 要的是「什么都不做」的底，
  所以把这些缓解措施剥掉重建。剥的是哪些对象、为什么，脚本里有判据。
- **`dig_pond.py`** `.inp` 的读写与解析（分节、节点标高、坐标、溢流节点、满管流）。
- **`dig_pond3.py`** 选址聚类 + 挖塘。进水口是**带闸的孔口**（塘满关闸，水留在管网里，
  而不是穿过塘再从塘里冒出来）；另配排空泵，开泵条件里必须带上「接收节点已经退水」——
  只按时钟开泵会把水抽回还在超载的管网，形成管网→塘→泵→管网的死循环（实测污染过一整轮结果）。
- **`dig_pond4.py`** 在 `dig_pond3` 之上加土地利用约束：塘得挖在能挖的地上，轮廓是真实多边形。
  吸不到可用地的聚类如实报「这里挖不了」，不硬塞。
- **`run_l2v.py`** 跑模型，把 `.rpt` 和新的节点坐标落到 `out/three_lines/`。
  坐标每一态各存一份：新塘位挪了地方，沿用旧坐标会让新节点查不到坐标被静默丢掉。
- **`check_land.py`** 独立复核。**判据不用掩膜自己那份导出**，直接查 `bas_building` / `ud_plot`——
  第一版就是这么抓出「建筑只按外环第一个顶点打了个圆」的：Mussafah NP1 压掉 333 m²，
  占它自己面积的 26%。

## 已知缺口

- 塘和积水节点之间在模型里是**零长度孔口**。用地约束把塘挪到了能挖的地上（最远 658 m），
  但那段输水管的管长、水头损失、埋深、穿越、造价一样都没算。
  所以现状③ 的削减量是**塘本身能做到的上限**，不是把水送过去之后的净效果。
  见假设总账 `pond_conveyance`。
- 兜底的空地掩膜不是权属台账。`ud_plot` 对 Mussafah 工业区零覆盖，26 个塘里 25 个只有兜底判据背书。
  见假设总账 `pond_land_l2p`。
- 现状① 是从「治理后」模型剥离重建的，不是客户自己的 existing 模型。要拿到那份才算定论。
