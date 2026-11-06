---
name: c1-5m-servers
description: C1 5 m 网格推演的服务器分工、镜像版本和 M61G5 的状态（2026-10-08）
metadata:
  node_type: memory
  type: project
  originSessionId: eab839c0-d870-4b47-9877-b3a7cd92b09a
  modified: 2026-10-10T06:44:56.935Z
---

C1 模型版计算域 5 m 网格（50 cm DTM）推演分布在多台服务器上：
- 服务器 1（AZPDW006aim01，48 核 / 188 GB，swbrain-2026.10.03 镜像）：M61G5（61 mm / 2 h），用旧脚本 `tools/c1_run_m61_5m.py` 手动逐步跑。2026-10-08 四态 + post 全部完成，交付在 `/app/out/交付/C1_模型版_61mm2h_5m`。每态约 19 h（L2S3 16.4 h），8 线程、约 11 GB 内存。
- 服务器 1 下一批（2026-10-08 安排）：推演计划表 v27 5m格网第 1–8 行 = M32H1G5 + M32H2G5 各四态，用 `tools/chains/c1_g5_server1.sh`（c1_batch.py + c1_run_g5.py，缺省 6 槽 × 7 线程），进度用 `bash c1_g5_server1.sh status`。2026-10-10 查：8 态 + post 全部完成，调度器已停、机器空闲（164 GB 可用）。下一批按 v28 是第 9–16 行 M32H24G5 + M61H1G5，但脚本 TAGS 写死、c1_run_g5.py 的 SCEN 里还没这两个情景，要先改。
- 2026-10-10 已改好（还没上服务器）：SCEN 加了 M32H24G5 / M61H1G5 / M61H24G5；c1_g5_server1.sh 的 TAGS 改成环境变量，缺省 "M32H24G5 M61H1G5 M61H24G5"（v29 第 9–16 行 + 21–24 行，M61H24G5 用来补空槽），新增 check、df、锁文件检查。上传包在 upload/server1_g5_batch2/。看第一批要 `TAGS="M32H1G5 M32H2G5" bash c1_g5_server1.sh status`。用户周一 10-12 才看进度。
- 2026-10-10 下午用户说服务器 1 可开下一阶段：缺省 TAGS 扩成 C1 剩下六场一次排完（M32H24G5 M61H1G5 M61H24G5 M96H1G5 M96H2G5 M96H24G5，v30 第 9–16、21–36 行），SCEN 加了 M96H1G5/M96H2G5；start 加了磁盘门槛 MIN_DISK_GB（缺省 400）。上传包 upload/server1_g5_batch2(.zip) 已更新，附 sync_to_nas.sh / clean_synced.sh。服务器 1 还没同步过 NAS。估计 C1 5 m 全部 10-25 前后算完（24 h 雨单态约 4 天是推算，待首日日志校准）。
- 服务器 2（AZPDW006uca03，32 核 / 64 GB，2026.10.06 镜像）：原跑 M33H1G5 + M96H24G5，已叫停（见 [[server2-g5-stopped]]），现跑 15 m（[[server2-15m-runs]]）。
- 服务器 3（AZPDW006uca06）：15 m 的 M32H24 + M61H1 已于 2026-10-08 前全部算完（含 post）；之后在跑 M61H24 + M96H24（`c1_15m_server3.sh`）；查它们进度要 `TAGS="M61H24 M96H24" bash c1_15m_server3.sh status`，不带 TAGS 只显示已完成的 M32H24/M61H1。2026-10-10 查：M61H24 + M96H24 八态 + post 全部完成，服务器 3 空闲。网页要显示 15 m 新情景，需把改过的 console/app.js、i18n.js docker cp 进 swbrain:/app/console/。
- 2026-10-08 用户定：5 m 全部只由服务器 1 跑（含原排给服务器 2/3 的 Yas、Al Bateen），服务器 2/3 只跑 15 m。计划表 v28 已按 6 槽重排，5 m 预计 11-28 全部算完（C1 10-25、Mussafah 11-08、Yas 11-14、Al Bateen 11-21、Bani Yas 11-28），各区域截止都赶不上。

**Why:** 镜像版本不同，同一场雨的四个态不要跨服务器混算。旧脚本 c1_run_m61_5m.py 原先缺少 `_5m` 目录后缀，曾把 5 m 交付写进 15 m 的 C1_模型版_61mm2h（已用 .bak-20261005 恢复，2026-10-08 修好脚本）。
**How to apply:** 估服务器 2 的耗时时，拿 M61G5 有水阶段每 5 分钟模拟段约 800–1,100 s 作参考。plan 里 post 显示 ✓ 只说明 out/c1_stats_<tag>.json 存在，不代表交付已完成。

- 2026-10-10 下午计划表另存 v31：用户问「从现在起跑 3 天能跑哪些」→ 缺省 TAGS 改成 M61H1G5 M96H1G5 M96H2G5（约 10-14 算完），随后 24 h 雨 M32H24G5→M61H24G5→M96H24G5（按每态约 88 h 估，约 10-26 算完）。Mussafah 及之后的 5 m 行仍是 v30 日期，没重排。上传包改为 upload/server1_update(.zip)，server1_g5_batch2 已过时。
