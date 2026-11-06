---
name: server2-15m-runs
description: 2026-10-08 起服务器 2 改跑 C1 15 m 五场雨（计划表 v25 15m格网第 1–8、17–20、25–32 行），M61/M96H1 按用户要求重算
metadata:
  node_type: memory
  type: project
  originSessionId: 337dcc3f-f5af-49c8-8a6b-cbd079f802c0
  modified: 2026-10-08T06:56:38.381Z
---

2026-10-08 用户安排服务器 2 跑推演计划表 v25「15m格网」第 1–8、17–20、25–32 行，共 20 个推演：C1 15 m，情景 M32H1 / M32H2 / M61 / M96H1 / M96H2，各 4 态。
脚本是 thinking-drainage-system/tools/chains/c1_15m_server2.sh（流程是 reset → start），调度方式与服务器 3 的 c1_15m_server3.sh 相同（c1_batch.py --runner c1_run_15m.py，8 槽 × 4 线程）。

部署包 2026.10.06 自带 M61 和 M96H1 的 15 m 旧结果。用户明确选了"全部重算"，没有复用。重算沿用原情景名，旧结果由 reset 挪到 /work/_bak_15m_reset 和 /app/out/_bak_15m_reset，没有删除。

2026-10-10 查：20 个态 + 5 场 post 全部完成，调度器已停，机器空闲。

**Why:** 用户要求这两组重算，同时交付目录名不能和旧的冲突。
**How to apply:** 问到服务器 2 的进度时，用 `bash c1_15m_server2.sh status`。等新结果确认没问题后，再提醒用户删除备份目录。相关：[[server2-g5-stopped]]
