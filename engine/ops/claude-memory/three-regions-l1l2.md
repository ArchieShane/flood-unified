---
name: three-regions-l1l2
description: Yas bridges / Al Bateen / Bani Yas 三个区域只做 L1+L2 的口径、脚本与上传包（2026-10-11 准备完毕，还没上服务器）
metadata:
  node_type: memory
  type: project
  originSessionId: f6ad5f48-0705-463d-bd24-39f7c6849077
  modified: 2026-10-12T06:11:29.450Z
---

Yas bridges interchange（汇水区 RBE1-OF1）、Al Bateen（RD-A）、Bani Yas（RD 14）三个区域，用户 2026-10-11 定的口径：
- 计算域 = 客户汇水区图层（`data/FW__Hot_Spots_506/` 下三个 KMZ）里那一个多边形，不外扩（22.07 / 3.21 / 15.45 km²）。
- **只做 L1 和 L2**；L2 = L1 + 已建成塘（全市 L2 口径），**不是** C1 那套"移动泵"，所以不需要顾问图纸里的泵点位；L2P / L2S 不做。
- 管网用全市 GDB 按计算域截取（连通块整块取，同 C1）；产流 CN 82 / Tc 30 min（全市值，客户没给这三处参数）；15 m 网格；雨量按计划表 33/61/96 mm × 1/2/24 h。
- 已建成塘落进计算域的只有 BNY 的 Baniyas Lake（SW_BYA_EB2_06_PND_41，56.2 万 m³）；YAS、BAT 区内没有，L2 模型同 L1，用户要求照跑。BAT 的泵站 ADM_RD-A 没有流量数据，没建泵。
- 用户说"二维不直接下雨"（Mussafah 那套）时我实现的是 C1 那套：离井超过服务半径的地面净雨直接下在二维（BNY 37%、BAT 约一半面积），没有逐汇水区子汇水区参数可用。

脚本：`tools/rg_run_15m.py`（runner）、`rg_regions.py`（生成 cases/regions/ 案例数据）、`rg_post.py`、`tools/chains/rg_15m_server{2,3}.sh`；`c1_batch.py` 加了 runner 可只做 L1/L2 的两行。上传包 `upload/regions_15m/`。计划表 `C:\freedo\zn-260930\推演计划表_2026-10_v37（10-12 起 15m 74–127 行全标已完成）.xlsx`（15m格网 73–126 行：服务器2 跑 16 场、服务器3 跑 11 场，10-16 起）。

**进度（2026-10-12）：** 服务器 2（16 场）、服务器 3（11 场）的 pre/build/compute(L1+L2)/post 全部 ✓，调度器已退出、无计算容器。成果在各机 /mnt/flood/swbrain/regions_15m 一带，10-12 已 sync 到 NAS 并 verify 全部一致（服务器 2/3），本机已清理（10-12，删 sww/tif，保留 npz）。

**Why:** 区别于 Mussafah（有客户水力模型）和 C1（有顾问移动泵图纸），这三处只有汇水区多边形。
**How to apply:** 本机 rasterio 被系统策略拦（DLL load failed），读 DEM / 出图的步骤只能在容器或服务器上跑；本机能测 build（把 `write_dem` 打桩）、docker 里 couple2way_v2、rg_post。相关：[[mussafah-15m-runs]]、[[c1-5m-servers]]。

**10-12 flood-viewer 接入三区域：** 目录 flood2d_rg_<区域>_m..，区域代码 rg_yas/rg_bat/rg_bny，统计文件 rg_stats.json（同 ms 结构）；server.py + compose 已改，更新包 upload/flood-viewer-update.zip，已部署，三区域可见（10-12）。
