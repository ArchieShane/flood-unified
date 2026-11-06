---
name: adm-l2-planned-data-check
description: 2026-10-20 排查 data/261020 能否支撑 ADM 全市 L2+planned：只有 AECOM 设计模型里 3 处有规划塘泵，Parsons 无新信息
metadata:
  node_type: memory
  type: project
  originSessionId: aa9be9ca-f72c-4122-a16d-012776f57132
  modified: 2026-10-20T06:39:46.117Z
---

用户 10-20 要在服务器上跑 ADM 全市 L2+planned（L2 已跑完），先排查 `data/261020/一级汇水区`（AECOM + Parsons）。结论写在 `C:\freedo\zn-260930\L2+planned数据排查_2026-10-20.md`。
- Parsons 25 个子模型 412 个文件与旧数据逐字节相同，无状态字段 → 无新信息，口径仍是“分不清按已建成”。
- 规划设施只在 AECOM 设计模型（SewerGEMS sqlite + 最新方案 inp，坐标/单位是英尺）：Al Bahia（inp 50603：GT-01/03/04+Polygon-1，W-7+PMP-2）、C13 Right（inp 35579：GT-1/2/5/6/8，无泵）、RD-A（inp 51480：GT-1～11 + W-6/PMP-18）。共 22 个蓄水节点约 35,800 m³、2 座泵。
- 待定：Al Bahia 高程基准比 DEM 高约 95 m；GT-03 与 Polygon-1 疑重复；C13 版本/容积三处不一致；C13 泵缺、C13 Left 无模型；RD-A 两版本；泵曲线是软件生成。

**Why:** 用户要判断数据够不够再上服务器。
**How to apply:** 用户回复这些问题后再写 L2+planned runner；读 shp/GDB 要用 PowerShell 跑 venv python（Bash 下 pyogrio 被应用控制策略拦）。相关 [[adm-city-250m-l2]]、[[adm-30m-plan]]。
