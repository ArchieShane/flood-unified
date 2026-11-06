ADM 全市 250 m · 33 / 61 / 96 mm × 1 / 2 / 24 h（9 场）· 只算 L2（2026-10-19，三台服务器共用本包）

范围：Abu Dhabi 全市（计算域沿用 city_run_250m 的全市 1,234 km²），二维 250 m 网格，只算 L2 = 既有管网 + 现状泵站 + 已建成塘，共 9 个任务（每场一个）。
口径同 ADM 汇水区各批：CN 82 / Tc 30 min / 曼宁 0.03 / 雨型取 Mussafah 同历时设计雨型等比放大 / 全市管网截取 / 现状泵站 + 域内已建成塘 / 积水点结果表。模拟终止 = 历时 + 4 h。
地形：50 cm DTM 先按 250 m 取块平均（小尺度起伏被抹平，深度 / 面积与 30 m、15 m、5 m 版不能直接比）。
本批【不做】：L1（没有 L2 − L1 对比）、逐帧图 / flood-viewer、shp + dat、flood-aio 登记。以后要展示，从交付目录里的 .sww / .npz 另补。

分工（固定，cases/ad250/adm250_all.tsv 最后一列；脚本按主机名认服务器，认不出用 SERVER=1|2|3）：
  服务器 1（aim01，48 核 / 188 GB） CITY_M33H24  CITY_M61H24  CITY_M96H24  CITY_M33H1     （4 场）
  服务器 2（uca03，32 核 / 64 GB）  CITY_M61H2   CITY_M96H2                                 （2 场）
  服务器 3（uca06，32 核 / 64 GB）  CITY_M33H2   CITY_M61H1   CITY_M96H1                    （3 场）
  耗时（估算，没实测）：1 h 雨 ≈ 4 h、2 h 雨 ≈ 5 h、24 h 雨 ≈ 9 h 每态；服务器 1 同时 3 个态，服务器 2 / 3 同时 1 个态（SLOTS 缺省 = 内存 GB / 60）。内存、耗时开跑后以 docker stats / status 为准。

【三台服务器各做一遍】（解压后在包目录里）
  1. bash adm_250m_server.sh check      看容器 / 镜像 / NAS / 本机任务清单有没有 ✗
  2. bash adm_250m_server.sh start      放后台，之后不用人工操作：第一次先 prep（读 GDB 重建全市一维网、截取计算域 / 塘 / 泵站、DTM 取平均成 250 m DEM，约十几分钟，之后有缓存）
                                         → 建 L2 → 一二维计算 → 后处理（积水点结果表与图层、统计、.sww、.npz、最大水深 tif）直接写 NAS → 逐项核对
  3. bash adm_250m_server.sh status     进度；tail -f adm_250m_server<N>.driver.log 看驱动日志；bash adm_250m_server.sh log 看调度日志
  任何一步缺文件，驱动日志里会有 ✗ + 缺哪一项；修好后 bash adm_250m_server.sh finalize 补核对，bash adm_250m_server.sh verify 只核对。
  可调：SLOTS（同时在算的态）、THREADS（每态线程，缺省 核数 / SLOTS 且 ≤ 16）、MIN_FREE_GB（新开一个态前要求的可用内存，缺省 20）、MIN_DISK_GB（/work 剩余，缺省 100）。

输出位置（脚本结束时也打印）：
  $NAS_BASE/result/<主机>/ADM_City_<雨量>mm<历时>h_250m/
      00_说明.md  计算设置.md
      积水点/积水点结果表.csv(.xlsx)  积水点图层.geojson
      统计/汇水区汇总.csv  ad_stats.json
      公共/计算域.geojson  现状泵站.json  已建成塘.json  积水点.json
      L2_已建成调蓄/一维/AX_<标签>_L2.inp  _一维报告.rpt
      L2_已建成调蓄/二维/AX_<标签>_L2.sww  _2d.npz  _最大水深.tif  L2_耦合运行记录.json
  NAS_BASE 缺省 /media/pop-fs/flood_data。

本包文件：adm_250m_server.sh  adm_250m_cleanup.sh  adm250_run.py  adm250_post.py  c1_batch.py  cases/ad250/*
  （adm250_run.py / adm250_post.py 是新文件，不覆盖容器里 ad_run_30m.py / ad_post.py / city_run_250m.py，不影响同机的其他批次；c1_batch.py 与 adm_rest 包里的相同。）
  容器里的前缀：文件 AX_<标签>_L2.*、计算容器 axpipe_<标签>_L2、调度日志 c1_batch_adm250_run.log。

要注意：
  · 全市是一个 26 万节点的整网 SWMM + 约 9 万个二维单元，单态内存与耗时没有实测过；64 GB 的服务器 2 / 3 默认只同时算 1 个态，服务器 1 同时 3 个。
  · 积水点结果表里 250 m 网格 200 m 内只有个位数单元，同时给了最近单元水深。
  · 容器里没有 kill / pgrep；.sh 必须 LF。

【一个任务出问题，其他任务怎么办】
  · 调度器（c1_batch）按「任务 × 步骤」排依赖：某一步失败只让这个任务自己的下游步骤不跑，别的任务照常算；修好后重新 bash adm_250m_server.sh start（同一条命令），已完成的步骤跳过。
  · 核对也是每个任务独立：一个任务有缺项只记 ✗，不影响别的。
  · 各任务的文件名、计算容器名（axpipe_<标签>_L2）、NAS 目录都按任务隔离，互不覆盖。
  · 做不到隔离的共享项：开跑前的 prep 步骤（失败 = 本机全部不跑，但一开始就能看到）；整台机器层面的事（内存被系统杀、docker 重启、断电、NAS 满）。内存风险用「新开一个态前要求可用内存 ≥ 20 GB」降低，出了事同一条 start 命令接着跑（在算的计算容器是独立的，驱动 / 调度断了它们也会算完，start 会认领）。
  · 暂停计算用 docker pause <容器>（保留进度），不要 docker stop。

【彻底停止并删除本批】每台服务器各跑一次：bash adm_250m_cleanup.sh（只列）→ 确认后 bash adm_250m_cleanup.sh go（真停真删，不可恢复）。
  只动本机分到的本批任务：停驱动 / 调度器 / 本批容器，删 /work 里本批文件、NAS 上本批的交付目录；不碰以前批次，也不碰公用准备产物（全市网 / DEM 缓存默认保留，DROP_CACHE=1 才删）。
