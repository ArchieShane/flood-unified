---
name: rain-32-to-33-correction
description: 2026-10-10 客户把 32 mm 更正为 33 mm：所有 M32 情景改 M33 重跑，各服务器进度、待办和踩过的坑
metadata:
  node_type: memory
  type: project
  originSessionId: 6423d033-2e8d-4eb4-b2ab-277182d1f5ab
  modified: 2026-10-10T13:43:18.623Z
---

2026-10-10 客户说 32 mm 给错了，应为 33 mm。脚本已把 M32* 全改成 M33*（含 G5 与 Mussafah），计划表另存 v34（v33 被 Excel 占着写不进），上传包 upload/m33_rerun(.zip)（三台通用，README.txt 有命令）。

- 服务器 2/3 的 Mussafah M32 数据已删（用户明确要求）；M61/M96 的 Mussafah 结果有效、保留。NAS 上 C1 的 32 mm 旧结果不删，只标作废（flood-viewer 是否下架待用户定）。
- 10-10 13:30 服务器 3 已开跑 C1 M33H24 + Mussafah M33H1/H2/H24（各 4 槽）；13:40 服务器 2 已开跑 C1 M33H1 + M33H2（8 槽）。服务器 2 的 M33H1 有部署包旧结果（10-02/10-07），已挪到 /work/_bak_33h1_old、/app/out/_bak_33h1_old，没删。
- 服务器 1（5 m）用户定「做法 A」：现有调度器（61H1G5 96H1G5 96H2G5）自己跑完（约 10-14）后再传包，用 `TAGS="M33H1G5 M33H2G5" bash c1_g5_server1.sh start`，不要中途重启调度器。之后 24 h 雨再排。

**Why:** 重启调度器会打断建模/设计/后处理这类非容器子进程；5 m 单态数天，不值得冒险。
**How to apply:** 问进度先看各台 `status`。踩过的坑：docker exec 的 heredoc 要加 `-i`；容器里没有 pkill，用 python 扫 /proc；杀掉的调度器会留僵尸进程（PPid 1），`os.kill(pid,0)` 仍成功，status 误报「在跑」、start 报「已有调度器」，此时删 /work/c2d/logs/*.lock 再 start；调度器启动时把已有产物的任务记成 done，之后再挪走旧结果不会重算，必须杀掉重启。相关：[[c1-5m-servers]] [[server2-15m-runs]] [[mussafah-15m-runs]]

**进展（2026-10-11）：** 服务器 2/3 的 33 mm 重跑 10-10 晚已全部算完（日志无失败，耗时远快于估算：L2S3 1–3 h），10-11 06:30–06:39 两台已 sync_to_nas 并 verify 一致，未清理本机。NAS 上旧 32 mm 仍在（rsync 不删）。服务器 1 的 5 m 33 mm 两场仍待现批跑完后开。服务器 2/3 空闲，下一批可排 Yas / Al Bateen / Bani Yas 的 15 m。

**NAS 下架（2026-10-11）：** 用户要求删 NAS 上 15 m 的 32 mm 旧结果、5 m（…G5）保留；已用 `nas_remove_32mm.sh`（在 upload/m33_rerun/）在 NAS 上真删（约 62 GB，list 复查为空；Mussafah 的 M32 也一并删了）。flood-viewer 加了 EXCLUDE_MM=32 / EXCLUDE_GRID=15（仅 15 m 下架，需重新部署才生效）。**待办：** 服务器 2/3 本机仍有 15 m 的 32 mm 残留（C1 的 + Mussafah 的 flood2d_ms_m32*、ms_*_M32*、dem_MS_M32*、pilot/twoway_MS_M32* 等），下次 sync_to_nas.sh 会传回 NAS，需先清本机或给 sync 加排除。

**本机清理（2026-10-11）：** 用户已在服务器 2/3 上执行 `local_remove_32mm.sh`（清本机 15 m 的 32 mm 残留，上一条「待办」已解决）和 `clean_synced.sh`（清已同步到 NAS 的 交付/C1_*、anuga_out/*.sww、tif/*，保留 npz）。服务器 2 的 `_bak_15m_reset`、`_bak_33h1_old` 未同步 NAS、未删，是否删待用户定。服务器 2/3 现在空闲，数据都在 NAS（sync_to_nas verify 通过）。
