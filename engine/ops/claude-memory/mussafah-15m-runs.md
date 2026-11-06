---
name: mussafah-15m-runs
description: Mussafah_00 15 m 推演（服务器 2/3）的口径、脚本与上传包，2026-10-10 写好、还没上服务器
metadata:
  node_type: memory
  type: project
  originSessionId: 647cd78d-26bb-4361-96be-f704b10849ab
  modified: 2026-10-10T10:18:19.569Z
---

2026-10-10 用户对 Mussafah 15 m 推演回复「按建议」。按计划表 v29「15m格网」：
- 服务器 2 跑第 41–52、65–72 行：M96H24 M32H24 M96H2 M61H1 M32H2（`ms_15m_server2.sh`）
- 服务器 3 跑第 37–40、53–64 行：M61H24 M96H1 M61 M32H1（`ms_15m_server3.sh`）
- 调度：c1_batch.py --runner ms_run_15m.py，8 槽 × 4 线程。上传包合并为一个目录 upload/mussafah_15m/（两台都传整个目录，各跑自己的脚本）。计划 10-12 开跑，实际 2026-10-10 用户已在两台都执行了 start（还没看到 status 输出，是否真跑起来待确认）。计划表已另存 v30：这 36 行的日期整体提前 2 天，L1/L2/L2P 标为进行中，操作清单插了 10-10 14:30 两行。

口径：
- 计算域 = 客户 Musaffah_00 模型（Hydraulic Model Data Export）汇水区并集，50 m 闭运算后 155.2 ha。我原先建议用 Mussafah-01，那是错的：两者只重叠 1.9 ha。已向用户更正。
- 一维 = 客户 Mussafah_00.inp。它是 LPS、偏移按深度，build 时换成 CMS：入流 Sfactor 0.001、泵曲线 ÷1000。
- 井口低于 DTM 2.5 m 以上的节点（湿井 RD_34/CH2、泵出口、J-1）不参与井盖交换。
- L2S3 设计复用 c1_design_l2s3.py，在内存里打补丁：Parsons 塘只在一维里，地面取水泵不进塘、排向客户出水口。
- 文件前缀 MS_，容器名 mspipe_*，交付目录 out/交付/Mussafah_00_模型版_<r>mm<d>h/。

**Why:** C1 的 15 m 流程从头到尾写死了 C1，不能只改 TAGS 来跑 Mussafah。
**How to apply:** 查进度用 `bash ms_15m_server2.sh status`（服务器 3 同理）。本机 rasterio 被应用控制策略拦截、docker 没运行，所以耦合计算、DEM 裁剪、后处理出图都没在本机验证过。头一轮上服务器后要先看 build 日志里「埋地不交换」的节点名单是否合理。相关：[[server2-15m-runs]] [[c1-5m-servers]] [[nas-result-sync]]
