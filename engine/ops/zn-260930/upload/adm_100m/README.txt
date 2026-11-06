ADM 全市 100 m · 33 / 61 / 96 mm × 1 / 2 / 24 h（9 场）· 只算 L2（2026-10-20，三台服务器共用本包）

由 adm_250m 包改来：只把二维网格 250 m 换成 100 m，其余口径不变（全市 1,234 km² 计算域 / CN 82 / Tc 30 min / 曼宁 0.03 / 现状泵站 + 已建成塘 / 积水点结果表 / 终止 = 历时 + 4 h）。
所有文件名、缓存名、容器名都带 100m / AY（不覆盖 250 m 的 AX）：adm100_run.py adm100_post.py adm_100m_server.sh adm_100m_cleanup.sh，cases/ad100，容器 aypipe_*，文件 AY_<标签>_L2.*，DEM 缓存 dem_AY100.tif，一维网缓存 ay_net_CITY.pkl（第一次要重做 prep，约十几分钟）。
交付目录：$NAS_BASE/result/<主机>/ADM_City_<雨量>mm<历时>h_100m/（与 250 m 的目录不重名）。不做 L1、不进 flood-viewer / shp+dat / flood-aio。

分工（固定，cases/ad100/adm100_all.tsv 最后一列；按主机名认服务器，认不出用 SERVER=1|2|3）：
  服务器 1（aim01，48 核 / 188 GB） CITY_M33H24  CITY_M61H24  CITY_M96H24     （24 h 雨，同时 2 个态）
  服务器 2（uca03，32 核 / 64 GB）  CITY_M33H1   CITY_M61H2   CITY_M96H2      （同时 2 个态）
  服务器 3（uca06，32 核 / 64 GB）  CITY_M33H2   CITY_M61H1   CITY_M96H1      （同时 2 个态）

【依据：250 m 实测】
  耗时（挂钟）：33H1 2.1 h、33H2 2.5 h、61H1 2.9 h、96H1 2.9 h、61H2/96H2 3.2 h；33H24 5.6 h、61H24 8.3 h、96H24 9.5 h。
  内存：约每个耦合步涨 ~25 MB（1 h/2 h 雨终值 ≈ 11–13 GB；24 h 雨终值 ≈ 57 GB/态）。涨幅大概率来自一维网（26 万节点）而非二维单元数，但没验证。
  100 m 单元数约 6 倍（≈ 57 万三角形）、时间步更小，耗时预计比 250 m 长数倍（粗估 1–2 h 雨 1 天上下，24 h 雨数天），内存终值预计比 250 m 高但同量级。以上都是估算，开跑后以 status / docker stats 为准。
  所以缺省：三台都 SLOTS=2、MIN_FREE_GB=40（新开态前要求可用内存）；服务器 1 只放 24 h 场次，3 个态里 2 个先跑，内存够再 SLOTS=3 重新 start。

【三台各做一遍】解压后在包目录里：
  1. bash adm_100m_server.sh check
  2. bash adm_100m_server.sh start      后台跑：prep → build → 一二维耦合计算 → 后处理（直接写 NAS）→ 逐项核对
  3. bash adm_100m_server.sh status / log / verify / finalize（同 250 m 包）
  先看每台开跑后头一两小时的 docker stats 和 status，内存涨得太快就 docker pause 一个态或调 SLOTS 后重新 start（同一条命令接着跑，不用重来）。
  .sh 必须 LF；容器里没有 kill / pgrep；暂停用 docker pause，不要 docker stop。

【算完以后：英文版 + L1 命名】用 adm_100m_export 包（任一台，三台共用 NAS）：
  bash adm_100m_export_en.sh list
  bash adm_100m_export_en.sh export /目标目录               （不在 NAS 的 result / shp_dat 里面）
  bash adm_100m_export_en.sh rename /目标目录               预览，再加 --apply 真改（L2 → L1，文件名和目录名）
  注意：包里正文（README / csv 表头 / calc_settings.md）仍写 L2，只有名字改成 L1。
  .sww / .npz 在 100 m 下比 250 m 大很多（约 6 倍帧体积），导出前先 df -h。

【彻底停止并删除本批】bash adm_100m_cleanup.sh（只列）→ bash adm_100m_cleanup.sh go。
