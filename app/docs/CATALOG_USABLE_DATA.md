# 归档盘里能用上的数据清单

对 `docs/catalog_tree.html`（阿布扎比雨水档案目录树）逐项比对
[DMT_DATA_REQUEST.md](DMT_DATA_REQUEST.md) 的 13 项缺口后整理。

| | |
|---|---|
| 核对日期 | 2026-09-19 |
| 清单来源 | `docs/catalog_tree.html` 内嵌目录树，解出 55,718 条路径 |
| 归档盘根目录 | `D:\__!!_flood` |
| 规模 | 4,989 目录 / 50,729 文件 / 821 GB / 最大深度 15 |

> ⚠ **本清单的全部判断只基于目录名与文件名，没有打开过任何一个文件。**
> 每项都给了「验证动作」——必须先做完验证，才能把它当成数据源写进模型。
>
> ⚠ 清单本身不完整：导出时有 **42 个文件名含非拉丁字符已丢失**
> （目录树里记为 `damaged`），另有 11,893 个文件体积记为 0 MB（源清单精确到 0.01 MB）。

---

## 一览

| # | 缺口 | 判定 | 最短路径的候选 |
|---|---|---|---|
| 09 | 汇水区划分 | **能** | `ADM Assets/19.01.2022/Catchment Area/Catchment/Catchment.shp` |
| 09 | 高精度地形 | **部分** | `SDD/DTM - from FGIC/DTM_50cm_2024/`（**主 TIF 缺失**） |
| 03 | 调蓄设施深度/尺寸 | **部分** | `2021.11.30 - GIS data.../SW_SKWAY.dbf` + `SW_RESERV STRUCT.dbf` |
| 02 | 移动泵车排量 | **不能** | —— |
| 04 | 罐车容量 | **不能** | —— |
| 05 | 管网流向 | **能** | 94 套 CivilStorm 工程的 `.stsw.sqlite` |
| 06 | 排放口旱谷水位 | **部分** | `FlexTable_ Outfall*.pdf` ×88 + `SW_OUTFALL.dbf` 两版 |
| 08 | 曼宁糙率法定值 | **能** | `Stormwater_ADQCC.../WA-726-2.pdf`（项目里只有 -1） |
| 13.1 | 5 个社区管网 | **部分** | 模型覆盖 Rahba/Shahama，另 3 个社区无对应 |
| 13.2 | 管底高程 | **能** | `ADM Assets/21.02.2022/.../SWPIPE.dbf` + 逐片模型 |
| 13.3 | 支管几何 | **能** | 同上 |
| 13.4 | 设计坡度 | **部分** | Al Rahbah 片区模型 |
| 13.5 | 管材 | **能** | 同 13.2 |
| 07 | 台账字段定义 | **能** | `Stormwater_vivek/Copy of Data Dictionary...xlsx` |
| 10 | 用地→C 对照 | **能** | `ADM Spatial Data/2022.9.14 - Landuse.../SHAPE/PLOT.shp` |
| 11 | 过程线形状系数 | **不能** | 只能从模型演算结果反推 |
| 12 | 本地雨量记录 | **部分** | `Rainfall Analysis Report _Phase B__V04.pdf` |
| 01 | 艾因/西部区管网 | **可能** | `Master Plan_Models_Al Ain/Al Ain.zip`（20 GB） |

以下路径全部相对于归档盘根目录 `D:\__!!_flood`。为省版面，
`IDD/Haydraulic Models-SWMP-IDS/Phase D Rev03/` 一律缩写为 **`«PD»/`**。

---

## 零 · 一次解多项的两个来源

### 0.1 «PD» —— 阿布扎比城市雨水总体规划（USWMP）全套

这是整个归档盘的核心，单独一项就同时覆盖 §05 / §08 / §09 / §13.2 / §13.3 / §13.5。
Idroesse 为 ADM 做的 USWMP，Phase A–E 齐全：

```
«PD»/1. USWMPR Rev03/Appendices R03/Appendix C – Hydraulic Models' CivilStorm Files/    ← 94 套模型
«PD»/1. USWMPR Rev03/Appendices R03/Appendix D – Proposed Solutions' Drawings/
«PD»/2. Collected Data/                                                                  ← 52 家单位提交的原始资料
«PD»/3. USWMP Phases/PHASE A Data Collection Report/
«PD»/3. USWMP Phases/PHASE B Hydraulic Modeling Report/
«PD»/3. USWMP Phases/PHASE C Existing System Assessment Report/
«PD»/3. USWMP Phases/PHASE E 2DRTFMR/
```

**关键点：模型文件是可直接解析的开放格式，不需要 Bentley 软件。**

| 扩展名 | 数量 | 是什么 | 能读出什么 |
|---|---:|---|---|
| `.stsw.sqlite` | 436 | Bentley CivilStorm/SewerGEMS 的 SQLite 库 | 管段（上下游管底、管径、管材、糙率）、汇水区（面积、C、tc）、调蓄、排放口 |
| `.inp` | 407 | EPA SWMM 输入文本 | `[SUBCATCHMENTS]` 面积 · `[CONDUITS]` 糙率与长度 · `[XSECTIONS]` 断面 · `[STORAGE]` · `[OUTFALLS]` |
| `.stsw` / `.dwh` | 433 / 780 | 工程文件与结果库 | 需 Bentley 打开 |
| `FlexTable_*.pdf` | 300+ | 模型导出的表格快照 | 不装软件也能核对数量级 |

FlexTable 分布：`Outfall` ×88 · `Conduit` ×82 · `Manhole` ×80 · `Catch Basin` ×34 ·
`Wet Well` ×22 · `Pump` ×21 · `Catchment` ×20 · `Pressure Pipe` ×13。

**验证动作** —— 拿 `53.Roads & Infrastructure in Al Falah FA-05` 的 `.stsw.sqlite`（**14.23 MB，110 个 sqlite 里最小的一档，且正压 §13.2 的 Al Falah 片区**）做试点：
`sqlite3` 打开列表，确认有无 conduit / catchment 表及其字段名。这一步决定后面 93 套怎么批处理。
试点成功再取大的：`45A. Riyadh City North`（213 MB）与 `45B_Riyadh City South`（511 MB）对应 §13.2 完整率 0.0% 的两个前缀。

### 0.2 «PD»/2. Collected Data/ADM Assets/ —— ADM 自己的资产导出

按提交日期分四批，其中三批直接可用：

```
«PD»/2. Collected Data/ADM Assets/19.01.2022/Catchment Area/Catchment/Catchment.shp   2.15 MB   ← §09
«PD»/2. Collected Data/ADM Assets/21.02.2022/Existing SW Network/                              ← §13.2/13.5
«PD»/2. Collected Data/ADM Assets/12.01.2022/Wet Spots/2019/                                   ← 积水点历史口径
«PD»/2. Collected Data/ADM Assets/12.01.2022/PS_Data/                                          ← 泵站
```

---

## 09 · 汇水区划分成果（阻塞项）

### 9.1 整市口径 —— 唯一一份

```
«PD»/2. Collected Data/ADM Assets/19.01.2022/Catchment Area/Catchment/Catchment.shp    2.15 MB
«PD»/2. Collected Data/ADM Assets/19.01.2022/Catchment Area/Catchment/Catchment.dbf    0.12 MB
«PD»/2. Collected Data/ADM Assets/19.01.2022/Catchment Area/Catchment/Catchment.prj
```
同一份在 Phase A 报告附件里有副本：
`«PD»/3. USWMP Phases/PHASE A Data Collection Report/Appendices/Appendix B - Data Collection/Appendix B.2 Collected Data/ADM Assets/19.01.2022/Catchment Area/Catchment/`

**这是 §09 最直接的答案**：ADM 自己交出来的汇水区图层，2022 年 1 月版。
`.dbf` 只有 0.12 MB，说明要素数不多（几百个量级），要确认粒度够不够 12 ha 尺度。

**验证动作** —— 读 `.dbf` 的字段名与要素数，读 `.prj` 确认坐标系，
用 `hotspots_845.csv` 的 501 个点做空间连接，看落入率。

### 9.2 逐点汇水面积 —— 最便宜的一次验证

```
Flood Management/To DMT 19Sep2026/Emails and Additional Files/SW Resilience Program - ADM/Catchment area Hotspots-Mainland.xlsx    0.03 MB
```
文件名直译就是「积水点的汇水面积（内陆片）」。**30 KB，全盘里性价比最高的一个文件，
应当第一个打开。** 若真是逐点面积，§09 的一级缺口当场解掉一半。

### 9.3 分片汇水区 shapefile —— 155 个

按提交单位聚合（`catchment` / `subcatch` / `subbasin` 命名的 `.shp`）：

| 数量 | 合计 | 位置 |
|---:|---:|---|
| 78 | 22.9 MB | `«PD»/2. Collected Data/ADM DTR/Model for IDS/` （40 个开发项目，见下） |
| 44 | 1.3 MB | `«PD»/2. Collected Data/ADM DTR/Kizad/` |
| 8 | 1.1 MB | `«PD»/2. Collected Data/ADM DTR/Aecom/` |
| 3 | 2.3 MB | `«PD»/2. Collected Data/ALDAR/Al Ghadeer/DDR Storm Water/Appendix F - Hydraulic Calculations/03 Shapefiles/` |
| 3 | 0.2 MB | `«PD»/3. USWMP Phases/PHASE C Existing System Assessment Report/` |
| 1 | **9.85 MB** | `«PD»/2. Collected Data/ADM DTR/DAR/Zayed City Model/Updated stormwater integrated model (mxd) - Without RB3 - updated - 25-6-2015/Shapefiles/catchments.shp` |
| 1 | 5.99 MB | `«PD»/2. Collected Data/ADM DTR/Model for IDS/Tebodin/Mafraq Industrial/Shapefiles/Existing/catchments.shp` |
| 其余 | | PARSONS / BUNYA / AL TORATH / ADM 250 Villa |

`Model for IDS` 下覆盖的片区（括号内为汇水区 shp 份数）：
Jubail Island(4) · Merief(4) · 440 plots Alsadr(4) · Nour Garden Masdar(4) · MIRRNI(4) ·
Mina Tunnel(3) · Alsadr(3) · Masdar Phase2(3) · 68 plots Shahama(3) · MBZ-Z35(3) · Yas South(3) ·
Manazel Reef Villas-2(2) · Makers Distt(2) · Jinan(2) · MBZ-Zone-B(2) · MBZ-Z30(2) ·
250 villas Samha(2) · Zayed City Sector-39(2) · Khalifa City-Contract-232-3(2) · Yas North(2) ·
Mayan(2) · Hadeel(2) · 另 17 个单份项目

**Zayed City Model 的 9.85 MB `catchments.shp` 值得单独试** —— 体量远超其余，
且正对 §13.2 里缺高程最多的 MZ4 / MZ8 片区。

### 9.4 InfoSWMM 模型库里的汇水区表

14 份 `SUBCATCH.DBF`，分布在 7 个 `.ISDB` 模型库：
`RAHABEACH3_15-09-14.ISDB` · `RAHABEACH_1_ALL.ISDB` · `RB2_INTERGRATED.ISDB` ·
`RD_ABHI_QAB+DELMA+SALAM+PH1.ISDB` · `RD_CJNOQR.ISDB` · `YAS1+ACRES.ISDB` · `Yas_1.ISDB`

典型路径：
```
«PD»/2. Collected Data/ADM DTR/DAR/South Spine Model H - 17-10-2022/wetransfer_southspine-h-model_2022-10-15_0935/RB-3-Model-Feb 2019/Model/RAHABEACH3_15-09-14.ISDB/SUBCATCH.DBF
```
同目录还有 `LUCOEFF.DBF`（用地—径流系数对照表，见 §10）与 `LANDUSE.DBF`。

### 9.5 模型导出的汇水区表格

20 份 `FlexTable_ Catchment Table.pdf`，含按重现期分档的版本
（`1 in 2 years FlexTable_ Catchment Table.pdf` ×3、`1 in 5 years` ×3）。
不装 Bentley 也能直接读到面积与 C 值。

---

## 09 · 高精度地形

### 9.6 50 cm DTM —— **主文件缺失**

```
SDD/DTM - from FGIC/DTM_50cm_2024/AUH_DTM.tfw           0 MB   ← 坐标配准文件
SDD/DTM - from FGIC/DTM_50cm_2024/AUH_DTM.TIF.aux.xml   0 MB   ← 金字塔/统计
SDD/DTM - from FGIC/DTM_50cm_2024/AUH_DTM.TIF.xml       0.01 MB ← 元数据
```

**`AUH_DTM.TIF` 本体不在清单里。** 三个附属文件齐全、主栅格没有 ——
要么拷贝时漏了，要么它就是那 42 个丢失文件之一。这是全盘最刺眼的一处缺口：
**50 cm 分辨率正好是 §09 要的「1 m 或 2 m DEM」，而且是 2024 年的新成果。**
`.TIF.xml` 只有 10 KB，能读出范围、坐标系与垂直基准，**先读它**。

### 9.7 其余地形

```
SDD/DTM - from FGIC/Other_DTM/DTM_DSM_Images.zip    4,385.96 MB
SDD/DTM - from FGIC/DTM_DSM_Metadata.pdf                0.16 MB
```
4.4 GB 的压缩包，名字含 DTM + DSM + Images。**先读那份 0.16 MB 的元数据 PDF**
确认分辨率与范围，再决定要不要解 4.4 GB。

### 9.8 等高线与地形测量

- 等高线：41 条路径，全部是单项目 DWG
  （`XR_Contour.dwg`、`contour-1/2/3.dwg`、`RS-02-00001-*_Contour Map.dwg/pdf`），
  **没有整市等高线成果** —— 与数据库里「市区 0 条」的实测一致。
- 地形测量：218 个文件命中 `topo`，如
  `06.TOPOGRAPHIC SURVEY-AIC-MUS-MBS-TS-000..008.pdf`（Musaffah）、
  `App - 25F Topo. Survey` / `App - 26F Topo. Survey`（PARSONS，Al Nahda / Al Wathba Farms）。
  都是项目级、非连续覆盖，**不能拿来划全市汇水区**，但可用于校核个别点。

---

## 03 · 调蓄设施深度与尺寸（阻塞项）

数据库 2026 版里 `pond` / `soakaway` / `sw_reservoirstructure` 的深度全部无效。
归档盘里有 **2021 年的同类图层**，属性可能还没被抹平：

```
«PD»/2. Collected Data/ADM Spatial Data/2021.11.30 - GIS data of the storm water system/Shape files/
    SW_SKWAY.shp            0.03 MB    SW_SKWAY.dbf            3.41 MB   ← 渗井
    SW_RESERV STRUCT.shp    0 MB       SW_RESERV STRUCT.dbf    0.04 MB   ← 调蓄构筑物
    SW_COLLECTIVETANK.shp   0 MB       SW_COLLECTIVETANK.dbf   0.01 MB   ← 集水池
```

**这是 §03 最有希望的一条线**：`SW_SKWAY.dbf` 有 3.41 MB，字段不会少；
若 2021 版里 `soakaway_diameter` / `length` / `width` 不是 0、
或 `cover_level` 与 `bottom_level` 不相等，就能直接补上 2026 版丢掉的深度。

同目录还有整套 2021 年管网图层与一个文件地理数据库：
```
«PD»/2. Collected Data/ADM Spatial Data/2021.11.30 - GIS data of the storm water system/Shape files/
    SW_PIPE.shp            24.97 MB    SW_PIPE.dbf           703.62 MB
    SW_INLET.shp            4.81 MB    SW_INLET.dbf          482.62 MB
    SW_MANHOLECHAMBER.shp   1.07 MB    SW_MANHOLECHAMBER.dbf 101.39 MB
    SW_CATCHBASIN.shp       1.08 MB    SW_CATCHBASIN.dbf      86.90 MB
    SW_CAPPEDEND.shp        0.57 MB    SW_CAPPEDEND.dbf       38.80 MB
    SW_GRATE CHNL DRN.shp   0.10 MB    SW_GRATE CHNL DRN.dbf   2.34 MB
    SW_OUTFALL.shp          0.01 MB    SW_OUTFALL.dbf          0.69 MB
    SW_PUMPSTATION / SW_JUNCTION / SW_DHGCHMB / SW_FRDISCHPNT / SW_PTRLINTCP
    Existing Storm Water.gdb/                                 240.42 MB（103 个文件）
```

其他调蓄相关：
```
«PD»/2. Collected Data/ALDAR/Al Ghadeer/DDR Storm Water/Appendix F - Hydraulic Calculations/03 Shapefiles/1 in 25yrs Scenario - Pond Sizing/
    Catchments_1in25yrs.shp / Nodes / Pipes / Manholes / Outfall     ← 调蓄池定容成果
«PD»/2. Collected Data/PARSONS/Delma Al Muroor/Delma Al Muroor_SW_Rev02.stsw/Shape files/Pond.shp
«PD»/2. Collected Data/ADM DTR/Model for IDS/Tebodin/Mina Tunnel/Model-tunnel 50 years/Pond.shp
«PD»/2. Collected Data/ADM DTR/Model for IDS/Tebodin/Mina Tunnel/Model-tunnel 100 years/Pond.shp
«PD»/2. Collected Data/ADM DTR/Model for IDS/Arcadis/Saraya Development/Appendix F Model/model shape files/Reservoir.shp
«PD»/1. USWMPR Rev03/.../Appendix C – Hydraulic Models' CivilStorm Files/63 Shakbouth Medical City_Pond/
Flood Management/To DMT 19Sep2026/Emails and Additional Files/SW Resilience Program - ADM/Pump Rental + Pond reference rate.pdf
```

**验证动作** —— 读 `SW_SKWAY.dbf` 的字段与前 100 行，直接对比数据库 `soakaway` 表的同名字段。

---

## 05 / 08 / 13.2 / 13.3 / 13.5 · 管网属性（流向 · 糙率 · 管底高程 · 管材）

这五项是同一批数据解的。

### 第一来源 · 2022 年 ADM 管网导出（独立于 2026 版）

```
«PD»/2. Collected Data/ADM Assets/21.02.2022/Existing SW Network/
    SWPIPE.shp              3.53 MB    SWPIPE.dbf             70.45 MB   ← 管段
    SW_INLET.shp            0.57 MB    SW_INLET.dbf           43.18 MB
    SW_MANHOLECHAMBER.shp   0.15 MB    SW_MANHOLECHAMBER.dbf   9.10 MB
    SW_CATCHBASIN.shp       0.15 MB    SW_CATCHBASIN.dbf       9.10 MB
    SW_CAPPEDEND.shp        0.03 MB    SW_CAPPEDEND.dbf        1.38 MB
    SW_OUTFALL.shp          0 MB       SW_OUTFALL.dbf          0.05 MB
    SW_PMPSTN.shp           0 MB       SW_PMPSTN.dbf           0.01 MB
```
加上 §03 里列的 2021 年版 `SW_PIPE.dbf`（703 MB），**一共有两份独立于 2026 年导出的管网属性表**。
§13.2 说的是「导出时整批丢了高程」，那么更早的两版很可能还带着。

**验证动作** —— 按 §13.2 的 9 个资产前缀
（`SW_MAR_RD66` / `SW_MAR_RD40` / `SW_AFL_FA2` / `SW_ZCI_MZ8` / `SW_ZPO_FRZ` /
`SW_ZCI_MZ4` / `SW_AFL_FA7` / `SW_ANH_NA5` / `SW_AFL_FA1`）
在 `SWPIPE.dbf` 里筛，统计 invert 字段的非空率。这一条能直接判定 §13.2 解不解得掉。

### 第二来源 · 94 套 CivilStorm 模型

`«PD»/1. USWMPR Rev03/Appendices R03/Appendix C – Hydraulic Models' CivilStorm Files/` 下逐片模型。
与 §13 问题点的对应关系：

| §13 问题片区 | 缺什么 | 对应模型目录 |
|---|---|---|
| Madinat Al Riyad（`SW_MAR_RD66`/`RD40`，完整率 **0.0%**） | 管底高程 | `45A. Riyadh City North (RCN )`（sqlite **213 MB** / inp 195 MB）· `45B_Riyadh City South` |
| Zayed Port FREEZONE（`SW_ZPO_FRZ`，7.2%） | 管底高程 | `82.Mina`（另见下方 as-built 纵断面） |
| Zayed City MZ4 / MZ8 / MZ12 / MZ28 | 管底高程 + 支管 | `«PD»/2. Collected Data/ADM DTR/DAR/Zayed City Model/`（catchments.shp 9.85 MB） |
| Al Falah FA1 / FA2 / FA7 | 管底高程 | `53.Roads & Infrastructure in Al Falah FA-05` · `«PD»/2. Collected Data/DAR AL HANDASAH/Al Falah - Stormwater - 20.10.21/Model/` |
| Al Wathbah NA5 / CT2 / CT3 | 管底高程 | `30.CT2_CT3 Wathba` · `45.South Shamkha and Wathba` |
| Al Reef / Rawdat Al Reef（§13.3） | 支管几何 | `62.Reef Community Connection` · `«PD»/2. Collected Data/PARSONS/Data Received 24-Jan-2022/Rawadt Al Reef Issue 01 DDR (ADM)/` |
| Al Rahbah（§13.4 平坡 2 条） | 设计坡度 | `89. Al Rahba and Shahama Farms` |
| Al Shamkhah SH28（§13.3） | 支管几何 | `78.Shamkha Stage - 4` · `67.Motor World Downtown Shamkha` · `«PD»/2. Collected Data/ADM DTR/Al Shamkha SH20 to SH29_KN Intl/` |
| Khalifa City（§13.5 取值管无材质最多，8 个点） | 管材 | `«PD»/2. Collected Data/PARSONS/Khalifa City Upgrade Construction DWGs-26-Nov-2022.zip`（8.2 GB）· `«PD»/2. Collected Data/OTAK/Otak Projects/KhalifaA_SE45-As built/` |

### 第三来源 · 竣工纵断面图（最直接的管底高程）

```
«PD»/2. Collected Data/ABU DHABI PORTS/2022.09.26/MZ STORM WATER DRAINAGE/AS BUILT DRAWINGS/
    Network/Profiles/pdf/AB-159-6-13.pdf      4.61 MB   ← 纵断面 = 逐段管底高程
    Network/Plans/pdf/AB-159-6-01.pdf        10.34 MB
    Surface/Civil/pdf/  ·  Surface/Structural/pdf/  ·  Drawing list/
```
**MZ = Mina Zayed = Zayed Port**，正对 §13.2 的 `SW_ZPO_FRZ`（124 条缺高程）。
纵断面图是竣工资料里唯一直接标管底高程的图种。

其他 as-built：
```
«PD»/2. Collected Data/ADM DTR/Model for IDS/Parsons/Makers Distt/Annexure 5 - As Built of Existing SW Network/X-Existing Storm Water Network.dwg
«PD»/2. Collected Data/AL QUDRA/for Idroesse/Danet STORM WATER As-built (DWG)/AB-90003606-*.dwg
«PD»/2. Collected Data/OTAK/Otak Projects/KhalifaA_SE45-As built/
```
另有一份现成的管底高程表：
```
«PD»/3. USWMP Phases/PHASE C Existing System Assessment Report/Appendices/PHASE C HYDRAULICS MODELS/21.Musaffah_06/Musaffah 6_invert.xls    0.28 MB
```
同目录 `PHASE C HYDRAULICS MODELS/` 下还有 **52 套片区模型**，与 Appendix C 的 94 套是不同批次，可互为备份。

### 第四来源 · 模型导出表格

82 份 `FlexTable_ Conduit.pdf` / `FlexTable_ Conduit Table.pdf`，按片区分布。
CivilStorm 的 Conduit FlexTable 默认列就含 upstream/downstream invert、diameter、
Manning's n、material —— **§08 / §13.2 / §13.5 三项的核对材料，不装软件就能读**。

---

## 08 · 曼宁糙率的法定取值

```
Flood Management/Stormwater_ADQCC design standards& Specifications/WA-726-1.pdf     7.32 MB   ← 项目里已有
Flood Management/Stormwater_ADQCC design standards& Specifications/WA-726-2.pdf    45.18 MB   ← 缺
Flood Management/Stormwater_ADQCC design standards& Specifications/WA-726-3.pdf    16.37 MB   ← 缺
Flood Management/Stormwater_ADQCC design standards& Specifications/WA-726-4.pdf    12.93 MB   ← 缺
Flood Management/Stormwater_ADQCC design standards& Specifications/AlAin City_IDF curves/     ← 文件名已损坏（阿拉伯语）
```

项目 `docs/standards/` 下只放了 **WA-726-1**。§08 要确认的三件事
（陶土的 n、无有效管材的兜底 n、混凝土有衬/无衬的区分）
很可能落在 **-2（45 MB，最厚的一卷）** 里。

**验证动作** —— 在 WA-726-2/3/4 里找 Table 4-1 或糙率条款，确认 §08 三问的条款号。

---

## 06 · 排放口旱谷水位

数据库 `outfall` 表 254 条里 `wadi_average_level` 只有 4 条有值。归档盘里：

```
88 份 FlexTable_ Outfall*.pdf（含 1 in 5 / 1 in 10 year 分档版本）
«PD»/2. Collected Data/ADM Spatial Data/2021.11.30 .../Shape files/SW_OUTFALL.dbf      0.69 MB
«PD»/2. Collected Data/ADM Assets/21.02.2022/Existing SW Network/SW_OUTFALL.dbf        0.05 MB
Flood Management/Stormwater_vivek/Island outfall 1 (2) 1.pdf                           0.96 MB
«PD»/2. Collected Data/PARSONS/Al Sader PS/Appendix 26 - Outfall Pump Station Drawings/
```

⚠ **这是「部分」而不是「能」**：模型里排放口的边界条件多半是设定的潮位或自由出流，
不等于实测旱谷常水位。2021 版 `SW_OUTFALL.dbf`（0.69 MB，比 2022 版大 14 倍）
更值得一看 —— 字段可能更全。

---

## 07 · 台账「有无排水管网」字段的定义

```
Flood Management/Stormwater_vivek/Copy of Data Dictionary - Storm Water Asstes - V10.1 -26052026.xlsx    0.16 MB
```

**雨水资产数据字典 V10.1，2026-05-26 版。** 一份文件同时解两件事：
1. §07 —— `Network_Available` 的确切定义（「周边有管网」还是「已接入管网」）；
2. §03 —— 渗井 `soakaway_diameter` / `length` / `width` 的单位
   （现有值自相矛盾：`160` 配 `2.0`、`1200` 配 `6000`）。

**这是全盘性价比第二高的文件（160 KB）。**

---

## 10 · 用地分类到径流系数 C 的对照

```
«PD»/2. Collected Data/ADM Spatial Data/2022.9.14 - Landuse of AUH City Islands and Mainland/SHAPE/
    PLOT.shp     36.15 MB
    PLOT.dbf    322.66 MB     ← 宗地属性，含用地分类
    PLOT.shx      1.45 MB
```
同目录另有 **按分区出图的用地图册**，`3by4_<分区名>.pdf` 逐份，覆盖全部市区分区，例如：
`3by4_Zayed City.pdf` · `3by4_Mohamed Bin Zayed City.pdf` · `3by4_Al Falah.pdf` ·
`3by4_Khalifa City.pdf` · `3by4_Al Shahamah.pdf` · `3by4_Al Nahdah.pdf` ·
`3by4_Al Rahbah.pdf` · `3by4_Al Danah.pdf` · `3by4_Abu Dhabi Islands.pdf`（50.53 MB）…

这份 `PLOT` 图层与数据库里的 `udm_plot`（410,421 宗）应是同一份，
但**带的是 shapefile 原始属性，可能保留了数据库导出时归并掉的分类层级**。

用地—系数对照的现成表：InfoSWMM 模型库里的 `LUCOEFF.DBF` 与 `LANDUSE.DBF`
（7 个 `.ISDB` 库各一套），路径见 §9.4。**顾问当年怎么把用地映射到 C 值，直接写在这张表里。**

⚠ 仍解不掉的一半：§10 指出 506 个市区点只有 19.4% 落在宗地内，**积水点八成在道路上**。
宗地图层再全也答不了「道路段落的 C 取多少」，这一问仍需 DMT 认定。

---

## 12 · 本地雨量观测记录

```
«PD»/2. Collected Data/ADM-DMT/Rainfall Analysis Report _Phase B__V04.pdf              16.43 MB
Flood Management/To DMT 18Sep2026/NCM Rainfall Data/NCM - Climate Data.xlsx             0.21 MB
Flood Management/NCM_Rainfall Data/April 2024/NCM-Climate Daily Report-15-16-04-2024.pdf
Flood Management/NCM_Rainfall Data/April 2024/NCM-Climate Daily Report-16-17-04-2024.pdf
Flood Management/NCM_Rainfall Data/March 2026/National Center of Meteorology - Climate Daily Report_24..31st March 2026.pdf   （6 份）
Flood Management/Incident Reports Related to Severe Rainfall Events/Incidents Report 12-15 Feb 2024.xlsx
Flood Management/Incident Reports Related to Severe Rainfall Events/Incidents Report 14-20 April 2024.xlsx
Flood Management/Incident Reports Related to Severe Rainfall Events/Incidents Report 22-31 March 2026.xlsx
```

**`Rainfall Analysis Report _Phase B__V04.pdf` 是这一项的主力** —— 16 MB 的专项降雨分析报告，
§12 要的「已整编的频率分析成果」多半就在里面。

⚠ 判「部分」的理由：`NCM_Rainfall Data/` 下只是 8 份**日报 PDF**（2024-04 与 2026-03 两场暴雨），
不是 §12 要的逐时/逐分钟长序列。`NCM - Climate Data.xlsx` 只有 0.21 MB，装不下多站多年逐时数据。

---

## 01 · 艾因与西部区（本轮范围外，留痕）

```
Flood Management/Master Plan_Models_Al Ain/Al Ain.zip                          20,080.91 MB   ← 全盘最大文件
Flood Management/To DMT 19Sep2026/Emails and Additional Files/SW Resilience Program - AAM/Storm.rar      12.62 MB
Flood Management/To DMT 19Sep2026/Stormwater Contracts/Al Ain/Network/Volume 1..3.docx
Flood Management/To DMT 19Sep2026/Stormwater Contracts/Al Ain/Pump stations/
Flood Management/Stormwater_ADQCC design standards& Specifications/AlAin City_IDF curves/       ← 艾因 IDF 曲线
Flood Management/To DMT 19Sep2026/Stormwater Contracts/DRM/DRM-201-MZ .. DRM-207-GYT/           ← 西部区 6 份合同全套
```

**20 GB 的 `Al Ain.zip` 是艾因雨水总体规划模型**。README 记的是「艾因 AAM 与西部区 DRM 的管网
至今未导出，339 个点没有任何管网几何」—— 若这个压缩包里是 CivilStorm/InfoSWMM 模型，
那么**艾因的管网几何其实在手上，只是没进数据库**，§01 的性质会从「对方没给」变成「我方没解压」。

范围扩回全域前不必动它；一旦要扩，这是第一件事。

---

## 确认查不到的三项

| # | 需求 | 全盘搜索结果 |
|---|---|---|
| **02** | 移动泵车分档额定排量 | `mobile` / `godwin` / `fleet` / `truck` / `bowser` **全部 0 命中**。`pump` 的 1,917 条命中全是**固定泵站**（`FlexTable_ Pump.pdf` ×16、`PUMP CURVE - ISLAND/MAINLAND`、`«PD»/2. Collected Data/ADM Assets/12.01.2022/PS_Data/` 的泵站资产调查表、`SW Pumps Material Sheet.pdf`）。唯一沾边的是费率单：`Flood Management/To DMT 19Sep2026/.../Pump Rental + Pond reference rate.pdf`（1.26 MB）—— 与台账里已有的 12″90 / 8″60 / 6″36 AED/hr 同源，**没有流量字段** |
| **04** | 罐车单车容量 | 仅 `Flood Management/To DMT 19Sep2026/.../Tanker reference rate- OM contracts.pdf`（0.16 MB），同样只有费率 |
| **11** | 过程线形状系数 | `hydrograph` **0 命中**。理论上可从 94 套模型的 `.out` / `.hyg` 结果文件反推实际过程线形状，但那是新做一轮工作，不是「找到一份数据」 |

§02 / §04 **保持为对 DMT 的索取项**，措辞不用改。
§11 可以补一句：拟从既有模型的演算过程线反推，而非等对方给系数。

---

## 建议的打开顺序

按「单位工作量能解掉的缺口」排序，前五个加起来不到 500 MB：

| 序 | 文件 | 大小 | 解什么 |
|---:|---|---:|---|
| 1 | `.../SW Resilience Program - ADM/Catchment area Hotspots-Mainland.xlsx` | 0.03 MB | §09 逐点汇水面积 —— 若成立，一级阻塞项当场降级 |
| 2 | `Stormwater_vivek/Copy of Data Dictionary - Storm Water Asstes - V10.1.xlsx` | 0.16 MB | §07 字段定义 + §03 渗井单位 |
| 3 | `SDD/DTM - from FGIC/DTM_50cm_2024/AUH_DTM.TIF.xml` | 0.01 MB | 确认 50 cm DTM 的范围/基准，并查清主 TIF 去向 |
| 4 | `«PD»/2. Collected Data/ADM Assets/19.01.2022/Catchment Area/Catchment/Catchment.dbf` | 0.12 MB | §09 整市汇水区的粒度与字段 |
| 5 | `«PD»/2. Collected Data/ADM Spatial Data/2021.11.30 .../SW_SKWAY.dbf` | 3.41 MB | §03 渗井深度与尺寸是否在 2021 版里完好 |
| 6 | `«PD»/2. Collected Data/ADM Assets/21.02.2022/Existing SW Network/SWPIPE.dbf` | 70.45 MB | §13.2 —— 按 9 个资产前缀筛 invert 非空率 |
| 7 | `Flood Management/Stormwater_ADQCC.../WA-726-2.pdf` | 45.18 MB | §08 糙率条款 |
| 8 | `«PD»/.../Appendix C .../53.Roads & Infrastructure in Al Falah FA-05/2017_10_15_Model.stsw.sqlite` | 14.23 MB | 模型库结构试点（全 110 个 sqlite 里最便宜、又正压 §13.2 的 Al Falah 片区），决定 94 套怎么批处理 |
| 8b | `«PD»/.../Appendix C .../78.Shamkha Stage - 4/Shamkha_ponds.stsw.sqlite` | 15.46 MB | 文件名直指 ponds —— §03 调蓄设施在模型里有没有真实深度 |
| 9 | `«PD»/2. Collected Data/ADM-DMT/Rainfall Analysis Report _Phase B__V04.pdf` | 16.43 MB | §12 |
| 10 | `«PD»/2. Collected Data/ABU DHABI PORTS/.../AS BUILT DRAWINGS/Network/Profiles/pdf/` | 4.61 MB | §13.2 的 Zayed Port 片区 |

另有一份值得顺手一读的旁证：
```
«PD»/3. USWMP Phases/PHASE A Data Collection Report/Appendices/Appendix D - Data Gap and Adequacy Analysis/
    224_K_GEN_00_LP_005_A_00 Assessment of 2021 GIS Data.pdf    14.76 MB
```
**顾问当年对 2021 年 GIS 数据做过的缺口评估** —— 我们现在踩的坑，2022 年可能已经有人写过一遍。

---

## 遗留待查

- `AUH_DTM.TIF` 本体的去向（附属文件在、主栅格不在）
- 42 个因非拉丁文件名在导出时丢失的文件，其中已知一个是
  `Flood Management/Stormwater_ADQCC design standards& Specifications/AlAin City_IDF curves/` 下的 `.rar`（1.45 MB，艾因 IDF 曲线）
- `Flood Management/Stormwater_2026_GIS shapefiles/Stormwater_2026_GIS shapefiles.zip`（57.09 MB）——
  名字看是 2026 年的 GIS 导出，**可能就是 `syq-pipe` 数据库的来源包**，需确认是不是同一份
