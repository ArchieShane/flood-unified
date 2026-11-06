---
name: adm-rest-49-tasks
description: 2026-10-19 ADM 剩余汇水区（库图层未推演 44 条→49 任务）61mm/1h L1+L2，<1km² 用 5 m 其余 30 m；一条龙脚本、发布到 flood-aio 的 id 命名、踩坑清单
metadata:
  node_type: memory
  type: project
  originSessionId: 68efe3ba-c8d2-4c74-9ed1-b9b1c48b72d2
  modified: 2026-10-19T08:01:24.521Z
---

用户 10-19 要求：库图层 57 条里未推演的 44 条，1 h 61 mm，<1 km² 5 m、其余 30 m，L1+L2；计划写日期（4 天含冗余，10-20~10-23）、带服务器配置（1=48核/188G aim01，2、3=32核/64G uca03/uca06，服务器1 权重 1.5）。OLD 是 6 块不相连多边形 → 拆 OLD1–6，共 49 任务（服务器1 20、2 12、3 17）。
成果包 `upload/adm_rest/`(+zip)：adm_rest_server.sh（三台共用，按主机名认服务器，算完一个收尾一个：逐帧图→转 shp+dat 不过滤 1cm→逐项核对）、adm_rest_publish.sh（服务器1 上手动跑：登记 flood-aio→shp_dat 改名 `shp_dat/floodh2d/<id>/{L1,L2}`→viewer 重扫→adm_rest_id_map.csv）。用户选定：id 目录=登记后直接改名；触发=全部算完手动跑一次 publish。
代码：tools/adm_rest_regions.py（生成 cases/ad/rest_regions.json 等，用 `.venv/Scripts/python.exe`——系统 python 的 numpy.random 被应用控制策略拦）、ad_run_30m.py 加每区域 res。计划表 `ADM剩余汇水区_61mm1h_计划_2026-10-19_v8.xlsx`（生成 _build/mk_adm_rest_plan_v8.py）。

**Why:** 用户要求规避之前所有坑。**踩到/规避的坑（以后同类脚本照做）：**
- ad_post 原来不把 `_2d.npz` 放进交付目录，flood-aio 登记（sim_c1）读它，之前靠 ad_copy_npz.sh 事后补；现 ad_post 直接拷。
- c1_batch 的 prep 步骤产物已在就跳过 → 老服务器上新区域数据进不了 /work/c2d/ad_cases；BATCH_PRE 加了 rest_regions.json 当标记。
- build 步骤「产物已在则跳过」，即使 chk 失败过；改代码重建要 `--force`。
- 截取管网无出水口（OLD1–6、SDPHASE1）→ SWMM ERROR 145；加不连管的占位出水口（管网保持封闭），在 network() 里。
- 转 shp+dat 不要扫整个 result（会把别的旧交付也转了）→ 按任务清单点名转；不过滤 1cm = H2D_ARGS 缺省 --no-csv。
- 重名区域（OUTLET×6）→ 交付目录加库 id；代号不能含 "_"（tag 按第一个 "_" 拆）；交付名要符合 `<区域>_<mm>mm<h>h_<网格>m`（flood-aio parse_delivery_name）。
- flood-viewer 的 server.py 显示名写死 → 改成 ad_ 区域取统计里的 title；新 server.py 在 package viewer_update/，靠 compose 挂载要 force-recreate。
- flood-aio 容器需有 sim_c1 的 `_stats_rows_ad`（v5/v6 镜像有，v4 要 docker cp aio_patch）；重复登记按 output_dir 更新、id 不变。
- 容器里没 kill/pgrep；.sh 必须 LF；Windows 本机跑要用 PowerShell 或 MSYS_NO_PATHCONV=1。
**实测：** 本机 5 m 的 0.2 km² 约 140 s（L1+L2），30 m 的 1.3 km² 约 30 s；BAG 全链（含登记、改名为 floodh2d/1、viewer）本机真跑通；服务器上的整条调度/NAS 挂载/aio 实机未跑。
**How to apply:** 相关 [[adm-30m-plan]]、[[server-run-delivery-checklist]]、[[flood-viewer]]、[[nas-result-sync]]。
