---
name: alain-250m-runs
description: 2026-10-15 艾因（Al Ain）250 m 一二维 L1/L2 推演：口径、数据位置、脚本与上传包 upload/aa_250m，未上服务器
metadata:
  node_type: memory
  type: project
  originSessionId: 61f4ec00-2f49-4182-993c-9f17df250af5
  modified: 2026-10-15T11:12:44.168Z
---

用户 10-15 要做艾因 L1+L2，250 m 网格。定的：雨量沿用全市 9 场（33/61/96 mm × 1/2/24 h = 18 态）；计算域外扩 1000 m 连成大片（取最大一块 341 km²，域内节点 157,725/183,755）。泵站用户问能否给默认——台账 PS_PUMP 每台都有 FLOW_RATE，所以按真实流量建 27 座，只有 4 座湿井深为 0 的按中位数 3.75 m 补。

数据：管网 `data/DMTStormWater_AlAin_Processed.gdb`（有 StartCode 等处理字段，无面图层）；完整版 `data/261015/AA_WR/DMT_IrrigationStormWater_AlAin.gdb`（有 POND、PS_WET_WELL_AREA）。AA_WR 里 AlAin_RecycledWater / WaterDistribution 是供水，不用；AlDhafra 不是艾因。塘只有 1 座（ST_349606_2677365，45,000 m³、深 6.8 m、塘底 194.0 m），L2 = L1 + 这座塘，其进水出水口（349592_2677416）L2 里改接进塘。
艾因管内底是**真实高程**（全市是 DEM+100），脚本里 B.DATUM=0；仅 2.3% 的管有管内底，其余按埋深 1.61 m 补。无客户子汇水区参数 → 统一 CN 82 / Tc 30 min。
局限：692 座渗井没建；约 4 成节点的管网块没有出水口；27 泵里只有 3 座在台账里找得到压力管，其余按「抽出计算域」。

脚本：`tools/aa_cases.py`（生成 cases/alain/*.json）、`tools/aa_run_250m.py`（runner，tag ALAIN_M<mm>H<h>，前缀 AA）、`tools/aa_post.py`、包 `C:\freedo\zn-260930\upload\aa_250m`（含 630 MB 的艾因 gdb，chain 脚本 aa_250m_server.sh，缺省 3 槽×8 线程）。本机已测：network/build 通，docker 里 chk 读入 OK，L2 一维整跑试验在 scratchpad。
**Why:** 全市脚本按 DATUM=100 且只取最大一块、要全市塘/客户参数，艾因不能直接用。
**How to apply:** 还没上服务器、计划表没加艾因页；上服务器前先 TAGS=ALAIN_M61H2 试跑量内存耗时。相关：[[city-250m-server1]]、[[three-regions-l1l2]]。
