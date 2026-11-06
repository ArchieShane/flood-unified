---
name: servers23-g5-resume
description: 2026-10-12 用户决定服务器 2/3 15 m 跑完后改跑 C1 5 m 加速，推翻 10-08 的"5 m 只由服务器 1 跑"
metadata:
  type: project
---

2026-10-12 用户说 15 m 都跑完了，服务器 2/3 可以利用起来加速 5 m。推翻 [[c1-5m-servers]] 里 10-08 "5 m 只由服务器 1" 的分工，也等于恢复 [[server2-g5-stopped]] 叫停的 G5。
准备的上传包 upload/server23_g5(.zip)：c1_g5_small.sh（缺省 2 槽 × 10 线程、每容器限 22 GB、TAGS 必填）。建议分工：服务器 2 跑 M33H1G5+M33H2G5，服务器 3 跑 M33H24G5，服务器 1 的 TAGS 不含 M33。尚未上传、未开跑。

**Why:** 5 m 全部算完要到 11-28，各区域截止赶不上。
**How to apply:** 32 核 / 64 GB 的机器 SLOTS ≤ 3（单态 15–18 GB，4 态曾 OOM）。整场雨只放一台机器。Mussafah / 三区域 5 m 还没有 5 m 的 runner（ms_run_15m.py / rg_run_15m.py 都写死 15），要另做。
