ADM 全市 100 m · 61 mm / 1 h · L1 + L2 · 治理后数据（Z:\flood\全市治理后数据_20261025）· 只在服务器 1 跑（2026-10-26）

【口径（用户 10-26 确认）】
  数据     一维管网 / 节点 / 出水口 / 泵 / 塘 / 雨水口 / 渗井全部来自治理后 GDB。开发机上已用 tools/adm100a_net.py 重建成 cases/ad100a/af_net_CITYAFTER.pkl
           （28.4 万节点、28.3 万根重力管、308 个出水口节点），服务器不读 GDB。DTM 用服务器上的（二维地形；build 时还会用服务器 DTM 重取节点地面）。
  其余     雨型 = 客户 Mussafah 模型的设计雨型；计算域 = 全市 1,233.6 km²（同之前全市 100/250 m）；产流 SCS CN 82 / Tc 30 min（数据里没有汇水区）；
           泵 / 塘 / 渗井 / 雨水口的接法同 10-21 的全市 v2；可移动泵不参与。
  L1 / L2  沿用旧口径：L1 = 既有管网 + 有流量的泵（无塘、无渗井）；L2 = L1 + 全部塘（148 座）+ 渗井（22 个有尺寸）。两态管网 / 泵相同。
           不看 LEVEL 字段（管网类只剔除 Abandoned / Expired / Removed，Proposed 照用，同上次）。塘全部进 L2，不论 LEVEL 是 L1 还是 L2。

【数据里的事实，会影响结果（也写进了交付的 common/ 和 calculation_settings.md）】
  · 泵：312 个泵站记录里只有 38 个能建（流量来自 泵机组表 + 泵曲线表：曲线半扬程处流量，按机组加总；没有曲线时用台账 FLOW_RATE ≥ 10 L/s）。
        其余 254 个（含 L2 新增的 67 个）没有曲线 / 流量，没建，列在 common/pumps_not_built.csv（附 COMMENTS 里的容量文字，如 "Cpcty::60x2"，供你对照）。
        上一版（合并结果）建了 159 个泵组，这次只有 38 个：治理后 GDB 里没有 PUMP_FLOW。FLOW_RATE 里 < 10 的值是泵台数（和 COMMENTS 对不上），没当流量用。
        RD-DY 的 TP-1 / TP-2 两个站点记录列了同一批 3 台泵，只算一次。
  · 塘：148 座里只有 3 座有可信深度、约 22 座有库容，145 座的容积 / 深度是估算（面积 × 4.70 m 中位深度；深度 > 30 m 的当数据错误），common/storage_objects.json 逐个标 estimated。
  · 出水口：数据里有 FIXED / POND / FREE 三种边界，这次全部按自由出流（同上次）。
  · 压力管（PIPE_SUBTY = PressrLine）只用来找泵的吸水井和排放点；找不到排放点的泵组按"抽出计算域"（新建自由出水口）处理。

【一条龙，start 之后不用再人工操作】
  建 L1/L2 → 两态同时一二维耦合计算 → 后处理直接写 NAS → 逐项核对 → .sww 转 shp + dat → 逐帧图写进 flood-viewer 数据目录 → 总核对
  做法：解压后在包目录里
    bash adm_100m_after_server.sh check      # 只读检查（先看这个）
    bash adm_100m_after_server.sh start      # 后台开跑
    bash adm_100m_after_server.sh status / log / plan / finalize / convert / frames / verify
    bash adm_100m_after_cleanup.sh           # 只列；加 go 才真停真删本批（含 NAS 上的交付、shp_dat、viewer 目录）
  .sh 必须 LF；容器里没有 kill / pgrep；暂停用 docker pause，不要 docker stop。

【输出位置（脚本结束时也会打印）】
  交付      $NAS_BASE/result/<主机>/ADM_City_61mm1h_100m_after/    L1_existing_facilities/{1D,2D}、L2_with_ponds/{1D,2D}、hotspots/、statistics/、common/、00_README.md、calculation_settings.md（全英文）
  shp+dat   $NAS_BASE/shp_dat/<主机>/ADM_City_61mm1h_100m_after/{L1,L2}/{shp,dat}（和 result 同级；H2D_ARGS 缺省 --no-csv，不过滤 1 cm）
  viewer    $NAS_BASE/result/<主机>/runtime/console_data/flood2d_ad_cityafter_m61h1g100/（逐帧图 + manifest / stats / ad_stats.json）
            flood-viewer 里：区域「ADM · Abu Dhabi 全市（治理后）」→ 100 m 网格 → 61 mm / 1 h，L1 / L2 两态 + 差值图。目录是 ad_ 开头的约定，flood-viewer 不用改代码，最迟 2 分钟自动出现（GET /api/rescan 可立即重扫）。
  NAS_BASE 缺省 /media/pop-fs/flood_data

【本机已验证 / 未验证】
  已验证：重建网络；build（.inp 56 MB，SWMM 5.2 两态都能读入）；用同一套代码在一个 4.5 × 3.5 km 小范围（含 1 座泵、3 座塘）做过 compute → post → 逐帧图 → shp+dat 的整条链（见交付说明里的试跑结果）。
  未验证：全市规模的内存 / 耗时（约 57 万三角单元 + 28 万节点整网 SWMM，两态同时算，每态 22 线程；一维 SWMM 最多 8 线程）。开跑后头一两小时看 status 和 docker stats，吃紧先 docker pause 一个态。
  服务器 NAS 的 shp_dat / result 写入、h2d 转换镜像（缺则用 h2d/ 离线构建）、flood-viewer 对新目录的实际显示，要在服务器上才能验证。
