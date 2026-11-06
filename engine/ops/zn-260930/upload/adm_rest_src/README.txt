ADM 剩余汇水区 · 61 mm / 1 h · L1+L2 · <1 km² 用 5 m、其余 30 m（2026-10-19，三台服务器共用本包）

范围：客户 flood 库图层 57 条里未推演的 44 条；其中 OLD 是 6 块互不相连的多边形，二维计算域必须是单个多边形，所以拆成 OLD1…OLD6 共 6 个任务 → 一共 49 个任务（5 m 的 8 条，其余 30 m）。
分工见 cases/ad/rest_all.tsv（最后一列是服务器号）；脚本按主机名认服务器（aim01=1、uca03=2、uca06=3），认不出用 SERVER=1|2|3。
口径同上一批 ADM 14 个汇水区（CN 82 / Tc 30 min / 曼宁 0.03 / Mussafah 1 h 雨型放大到 61 mm / 全市管网截取 / 现状泵站 + 域内已建成塘）。

【三台服务器各做一遍】（解压后在包目录里）
  1. bash adm_rest_server.sh check      看容器 / 镜像 / NAS / 本机任务清单有没有 ✗
  2. bash adm_rest_server.sh start      放后台，之后不用人工操作：每个任务算完 → 后处理写 NAS（含 .sww 和 .npz）→ 逐帧图进 flood-viewer → .sww 转 shp+dat（不过滤 1 cm）→ 逐项核对
  3. bash adm_rest_server.sh status     进度；tail -f adm_rest_server<N>.driver.log 看驱动日志；bash adm_rest_server.sh log 看调度日志
  任何一步缺文件，驱动日志里会有 ✗ + 缺哪一项；修好后 bash adm_rest_server.sh finalize 补收尾，bash adm_rest_server.sh verify 只核对。

【服务器 1 多做两件事】
  A. flood-viewer 更新（新区域不用再改代码，显示名自动取统计文件里的名字；不更新则区域显示成 AD_<代号> 大写，数据照样能看）：
       cp viewer_update/server.py <viewer 部署目录>/server/server.py     （viewer 在 /mnt/AI/flood/swbrain/flood-viewer，server.py / app.js 靠 compose 挂载）
       cd <viewer 部署目录> && docker compose up -d --force-recreate        （端口以实际为准，上次是 8000）
  B. 三台都算完、核对通过之后，发布到 flood-aio（登记 → shp_dat 改名成任务 id → viewer 重扫）：
       AIO_DIR=/mnt/AI/flood/flood-aio-v4 bash adm_rest_publish.sh          只读，列每个任务卡在哪一步
       AIO_DIR=/mnt/AI/flood/flood-aio-v4 bash adm_rest_publish.sh go       真做（可重复执行，没算完的跳过、下次补）
       AIO_DIR=/mnt/AI/flood/flood-aio-v4 bash adm_rest_publish.sh verify   核对：库里有任务、shp_dat/floodh2d/<id>/{L1,L2} 齐、viewer 里有
     结果：shp+dat 在 /media/pop-fs/flood_data/shp_dat/floodh2d/<flood-aio 任务 id>/{L1,L2}/{shp,dat}，对照表 adm_rest_id_map.csv（id ↔ 范围 ↔ 目录）。
     flood-aio 容器里 sim_c1.py 若还是旧的（没有 AD 交付的读取分支），go 会先把 aio_patch/ 里的三个文件拷进去并重启一次容器。

输出位置（脚本结束时也打印）：
  交付目录     /media/pop-fs/flood_data/result/<主机>/<区域目录>_61mm1h_<网格>m/
  viewer 用    /media/pop-fs/flood_data/result/<主机>/runtime/console_data/flood2d_ad_<代号小写>_m61h1g<网格>/
  shp+dat      转换时 /media/pop-fs/flood_data/shp_dat/<主机>/<区域目录>_61mm1h_<网格>m/<L1|L2>/ → 发布后改名成 shp_dat/floodh2d/<id>/<L1|L2>/

要注意：
  · 区域目录名里 OUTLET 带库 id（OUTLET-63 等，库里有 6 个都叫 OUTLET），OLD 拆块是 OLD-1…OLD-6，其余按名称（空格换成 _）。
  · 计算域内没有塘的区域，L2 的模型与 L1 相同，两态结果一致（和上一批的 RD-A 等一样）；塘清单只覆盖 ponds_all（ADM 14 区那批来源），域内有塘才建。
  · 本机实测：0.2 km² 的 5 m 任务（L1+L2 同时算）约 2.5 分钟，1.3 km² 的 30 m 约 30 秒；服务器上的耗时以 status 为准，计划表按 4 天留了冗余。
  · B_C 多边形里一个 400 m² 的洞被填掉了。

【一个任务出问题，其他任务怎么办】
  · 调度器（c1_batch）按「任务 × 步骤」排依赖：某一步失败只让这个任务自己的下游步骤不跑（日志里「－ … 不跑了（上游失败）」），别的任务照常算；修好后重新 bash adm_rest_server.sh start（同一条命令），已完成的步骤跳过。本机用故意写坏一个任务的塘文件验证过：该任务 build 失败，另一个任务算完、后处理、交付都正常。
  · 收尾（逐帧图 / 转 shp+dat / 核对）也是每个任务独立：一个任务收尾失败只记 ✗，不影响别的；转换 3 小时、逐帧图 2 小时超时自动放弃该任务这一步（CONV_TIMEOUT / FRAMES_TIMEOUT 可改）。
  · 各任务的文件名、计算容器名（adpipe_<任务>_<态>）、NAS 目录都按任务隔离，互不覆盖。
  · 做不到隔离的共享项：开跑前的 prep 步骤（所有任务都依赖它，失败 = 全部不跑，但一开始就能看到）；开跑前的文件齐全检查（缺任何一个任务的案例文件整批不开跑，包里是齐的）；整台机器层面的事（内存被系统杀、docker 重启、断电、NAS 满）会同时影响在算的任务；内存风险用「新开一个态前要求可用内存 ≥ 20 GB」降低，出了事同一条 start 命令接着跑（在算的计算容器是独立的，驱动 / 调度断了它们也会算完，start 会认领）。

【彻底停止并删除本批】每台服务器各跑一次：bash adm_rest_cleanup.sh（只列）→ 确认后 bash adm_rest_cleanup.sh go（真停真删，不可恢复）。
  只动本机分到的本批任务：停驱动 / 调度器 / 本批容器，删 /work 里本批文件、NAS 上本批的交付目录 + shp_dat + viewer 目录；不碰以前批次，也不碰公用准备产物。已 publish 到 flood-aio 的任务不在范围内。
