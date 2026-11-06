---
name: nas-result-sync
description: 三台服务器都挂了 NAS 在 /media/pop-fs/flood_data/result/，成果按主机名分子目录同步（tools/chains/sync_to_nas.sh）
metadata:
  node_type: memory
  type: project
  originSessionId: 77d490f3-2b60-4b81-9120-963f98b86191
  modified: 2026-10-10T07:39:30.069Z
---

2026-10-10 用户：每台计算服务器都挂了 NAS，路径是 `/media/pop-fs/flood_data/result/`，所有成果数据都要放到这里（本地盘只有 1 T）。最终目的是把三台的成果汇总到一处展示。

写了 `tools/chains/sync_to_nas.sh`（check/start/status/verify）。它把 /work/c2d、/app/out、/app/console/data 三处挂载同步到 `result/<主机名>/{work/c2d,runtime/out,runtime/console_data}`，不传 out/cache、out/state、_bak_*，只复制、不删本机文件。

2026-10-10：服务器 2 同步完成并 verify 通过（c2d 33.8 GB、out 62.4 GB、console_data 0.16 GB）。本地清理用 `tools/chains/clean_synced.sh`（list/run）：只删 交付/C1_*、c2d/anuga_out/*.sww、c2d/tif/*，缺省保留 npz（c1_batch 以 twoway json + npz 判断态已算完，删了会重算）。_bak_15m_reset 没同步到 NAS。

**Why:** console_data 里的 c1_hs_series.json、c1_hyeto.json 每台机器只含本机情景，直接合并会互相覆盖；三台镜像版本也不同。
**How to apply:** 做汇总展示时，以各子目录为源合并；那两个文件要在展示机上用 c1_hs_series.py / c1_hyetograph.py 重新生成（要读 npz）。服务器 2 的 M61/M96H1 15 m 结果是重算的新版，以它为准。相关：[[c1-5m-servers]] [[server2-15m-runs]]
