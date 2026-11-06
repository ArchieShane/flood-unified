ADM 全市 250 m v2 · 33 / 61 / 96 mm × 1 / 2 / 24 h（9 场）· 只算 L1 · 一维网络全部来自「合并结果_20261020」（2026-10-20，三台服务器共用本包）

【和 10-19 版 250 m（adm_250m）的区别】用户 10-20 定
  数据     一维管网 / 节点 / 出水口 / 泵 / 塘 / 渗井 / 雨水口全部来自 data/合并结果_20261020 的 15 个 shp。开发机上已用 tools/adm250v2_net.py 一次性重建成
           cases/ad250v2/av_net_CITY.pkl（27.6 万节点、28.2 万根管），服务器不再读客户 GDB。另外三样「非矢量管网」输入沿用：DTM（二维地形与节点地面）、
           客户 Mussafah 模型里的设计雨型、全市计算域 1,233.6 km²（和 10-19 版同一个多边形）。
  汇水区   Catchment 不参与（产流仍是全市 CN 82 / Tc 30 min，注入节点服务面积；无管网覆盖处净雨直接下在二维）。
  泵       合并数据里有流量（Pump.PUMP_FLOW，L/s）的才建：159 个泵组（257 条泵记录）；其余 300 条列入局限清单（common/pumps_not_built.csv）。可移动泵不参与。
  塘       183 个里域内未废弃 180 个；容积 = 塘多边形面积 × 最大深度，缺深度取 GDB 塘的中位深度（5.29 m，只有 4 个 GDB 塘有深度），共 117 个标「估算」（common/storage_objects.json 逐个标）。
  渗井     993 个里只有 22 个有可用的 L×W×D，作孤立蓄水（只收所在二维单元的地表水，不下渗）。
  雨水口   INLET 16.1 万 + 集水井 5.3 万，吸附到 8 m 内最近的井，给该井的 2D→1D 孔口加面积（每个 0.10 m²，假设值）；用 couple2way_v3.py。
  命名     交付里的「L2」一律叫 L1（程序内部态名就是 L1）；路径和内容都是英文，后处理结尾自动扫描，有中文就不写完成标记；成果目录名加 _v2，不和 10-19 的结果重名。

【固定分工】cases/ad250v2/adm250v2_all.tsv 最后一列（按主机名认服务器，认不出用 SERVER=1|2|3）
  服务器 1（aim01，48 核 / 188 GB）  CITY_M33H24  CITY_M61H24  CITY_M96H24  CITY_M33H1
  服务器 2（uca03，32 核 / 64 GB）   CITY_M61H2   CITY_M96H2
  服务器 3（uca06，32 核 / 64 GB）   CITY_M33H2   CITY_M61H1   CITY_M96H1

【三台各做一遍】解压后在包目录里：
  1. bash adm_250m_v2_server.sh plan      只列任务
  2. bash adm_250m_v2_server.sh check     容器 / 镜像 / NAS 可写 / 数据
  3. bash adm_250m_v2_server.sh start     后台跑：prep → build → 一二维耦合计算 → 后处理（直接写 NAS）→ 逐项核对
  4. bash adm_250m_v2_server.sh status / log / verify / finalize
  .sh 必须 LF；容器里没有 kill / pgrep；暂停用 docker pause，不要 docker stop。彻底停止并删除本批：bash adm_250m_v2_cleanup.sh（只列）→ bash adm_250m_v2_cleanup.sh go。

【输出位置】$NAS_BASE/result/<主机>/ADM_City_<雨量>mm<历时>h_250m_v2/
  00_README.md  calculation_settings.md（含局限）
  L1_existing_facilities/1D/   AV_CITY_M..H.._L1.inp、_1D_report.rpt
  L1_existing_facilities/2D/   .sww、_2d.npz、_max_depth.tif、L1_coupled_run_record.json
  hotspots/   hotspot_results.csv、hotspot_layer.geojson（ADM 积水点只用来报结果，不进计算）
  statistics/ summary.csv、run_stats.json
  common/     domain.geojson、storage_objects.json、pumps_built.json、pumps_not_built.csv、data_usage_audit.csv（15 个图层逐个：几个、域内几个、用了几个、怎么用、没用的原因）、hotspots.json
  不做：L1 以外的态与对比、逐帧图、shp+dat、flood-viewer / flood-aio、xlsx（openpyxl 默认主题带中文字体名）。

【没有实测的】（本机没有 docker / ANUGA）
  · 耦合计算（couple2way_v3.py）和 compute / post 在容器里的整条链没跑过；本机验证过：重建网络、build（.inp 55 MB，SWMM 5.2 打开并跑 1 h 模拟通过）、
    c1_batch 的 plan、后处理用合成二维结果跑通（含中文扫描能拦住真的中文）。
  · 内存 / 耗时：节点比 10-19 版多约 9%，另有 ~200 个蓄水节点，没实测；SLOTS 缺省 = 内存 GB / 60（188 GB → 3，64 GB → 1）。开跑后头一两小时看 status 和 docker stats。
  · 泵流量单位按 L/s（Parsons 模型 FLOW_UNITS = LPS）；AECOM 部分的单位没有文件佐证。
  · 159 个泵组里 110 个（合计 78.6 m³/s）在数据里找不到排放点，按「抽出计算域」处理（新建自由出水口），另 49 个（79.4 m³/s）排到压力管末端的管网节点；抽出计算域的部分对积水结果影响很大，见 calculation_settings.md。

【算完后收集到与 result 平级的英文目录】任一台：bash adm_250m_v2_collect.sh（只列）→ bash adm_250m_v2_collect.sh copy；目标 $NAS_BASE/adm_city_250m_v2/（EXPORT_NAME 可改），写 MANIFEST.csv。
