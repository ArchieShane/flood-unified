---
name: server2-g5-stopped
description: 服务器 2 的 C1 5 m 网格（M33H1G5 / M96H24G5）推演于 2026-10-08 被用户叫停
metadata:
  node_type: memory
  type: project
  originSessionId: 5e156d78-f7d4-4577-99da-9f8a6c73f887
  modified: 2026-10-08T06:47:42.900Z
---

2026-10-08 用户决定停掉服务器 2（32 核 / 64 GB）上的 C1 5 m 网格推演（c1_g5_server2.sh，M33H1G5 + M96H24G5），理由是"这台机器跑的是 5 米格网"。

停之前的状态：M33H1G5 的 L1 / L2 / L2P 已算完，L2S3 的 inp 已设计好、计算在跑；M96H24G5 的 L1 因 4 态并发 OOM 被杀（单态约 18 GB），L2 / L2P 在跑。

**Why:** 5 m 网格每个态约 15–18 GB 内存、96 mm/24 h 单态要几十小时，代价太高（用户没细说原因）。
2026-10-08 用户又要求把这批运行结果也删掉（R96 L1 共用底座除外）。

**How to apply:** 不要默认在服务器 2 上恢复 G5 任务；G5 结果视为已删除，不要引用。若要重启，提醒 SLOTS ≤ 3。
