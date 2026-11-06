---
name: adm-city-100m-merged-data
description: 2026-10-20 排查 data/合并结果_20261020 能否支撑 ADM 全市 100 m 只算 L2（只用该目录数据、全部参与计算）：结论与待用户定的口径
metadata:
  node_type: memory
  type: project
  originSessionId: fed2c619-8ec0-443a-9d01-28df98274374
  modified: 2026-10-20T18:30:41.239Z
---

用户 10-20 要求按旧 100 m 包（upload/adm_100m，已在三台清理掉）的脚本和交付标准重做全市 L2 100 m 推演，**只能用 `thinking-drainage-system/data/合并结果_20261020`，且其中数据都要参与计算**；先排查+出计划，用户确认后才写脚本（本会话只做了排查，没写脚本）。

排查事实（15 个 shp，EPSG:32640）：
- 合并 = 旧 GDB 中被 AECOM(22)/Parsons(25) 子模型覆盖的管换成模型管（旧 PIPELINE 26.07 万→GDB 部分 14.37 万 + 模型 14.99 万）；字段按来源不同（模型部分列名有十几种变体、节点 ID 只在子模型内唯一）。
- GDB 部分无 StartCode/EndCode/StartElev，只能按端点坐标吸附建拓扑（0.5 m 吸附后 2,521 个连通块，旧库 2,599；最大块 5.5 万管 vs 旧 3.1 万）；模型管按名字解析 96%，Mussafah-01/-02、RD-26A 需坐标。
- 高程：GDB 部分仍是 +100 基准（旧代码 DATUM=100），模型部分与 DTM 同基准（节点地面−DTM 中位 ≈ −0.3 m）。
- Catchment 26.9 万多边形仅覆盖 444 km²（域 1,234 km² 的 36%），重叠 1.63 倍，CN 有 0（2,709）、TC 最大 600、599 个无效几何，25 万有 OUT_NODE。
- Pond 183（GDB 23 有容积；Parsons 160 无容积/塘底，只有 36 个 DEPTH_MAX），旧 ponds_all 是 470 个，不在合并目录里。
- Pump：GDB 93 条几乎无流量（FLOW_RATE>0 仅 1）；模型 485 条中 268 有 PUMP_FLOW，Pump 是点、无上下游节点，靠 PressurePipe/PressureJunction/WetWell 名字关联；MOVABLE_PUMP 88 条（流量 550–2050）。
- INLET 16.7 万、CatchBasin 5.5 万、SOAKAWAY 993（有 L/W/D）、SW_CAPPEDEND 2.3 万、PETROLINTERCEPTOR 20：旧模型都没用，要新定义怎么“参与”。
- 合并目录没有 DEM、雨型、计算域、泵站流量表 → “只用该目录”与二维 DTM/雨型冲突，须用户定。

**Why:** 用户要判断“只用这批数据”能否成立再开写。**How to apply:** 用户回复 7 个口径问题后再写脚本；读 shp 用 PowerShell 跑 .venv python（Bash 下 pyogrio 被拦）。相关 [[adm-city-250m-l2]]、[[adm-l2-planned-data-check]]、[[server-run-delivery-checklist]]。

**用户 10-20 的答复（更正：本次是 250 m，不是 100 m）：** DTM 用服务器上的；雨型=9 场（33/61/96 mm × 1/2/24 h）；计算域=“DTM 全市”→汇水区不参与；塘用面积×最大深度、无深度取 GDB 塘中位深度并逐个标“估算”；无流量的泵不建、进局限清单；可移动泵不参与；INLET/封端/渗井/油水分离器按推荐；交付无中文、目录和文件名 L2→L1。待确认：AUH_DTM_5m_Z40.TIF 有效面积约 69,657 km²（旧全市域仅 1,233.6 km²），“全市”是否仍用旧 1,233.6 km² 域。

**10-20 已交付脚本包（计算域沿用旧 1,233.6 km²，用户确认）：** `upload/adm_250m_v2/`(+zip 14.6 MB)，runner adm250v2_run.py / post adm250v2_post.py / couple2way_v3.py（加雨水口交换面积）/ adm_250m_v2_server.sh+cleanup；前缀 AV、容器 avpipe、内部态名 L1、成果 `ADM_City_<mm>mm<h>h_250m_v2`、全英文且 post 末尾扫中文；分工同 10-19 版。网络由 `thinking-drainage-system/tools/adm250v2_net.py` 在开发机从合并 shp 重建（cases/ad250v2/av_net_CITY.pkl；27.6 万节点、28.2 万管；159 泵组、180 塘+22 渗井、雨水口 15.9 万井带面积）。未实测：容器内耦合/compute/post、内存耗时；110 个泵组无排放点按抽出计算域（78.6 m³/s）；117 个塘估算容积。
