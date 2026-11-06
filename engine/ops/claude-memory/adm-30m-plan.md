---
name: adm-30m-plan
description: 10-15 用户按 ADM 汇水区表新建 30 m 计划表（L1/L2、61 mm/1 h），3 台服务器分工；runner 未写
metadata:
  type: project
---

10-15 用户贴 ADM 汇水区表，要 3 台服务器空闲时跑表中区域的 L1+L2，格网 30 m（后期可改重跑），只跑 1 h「60 mm」（用户 10-15 确认不是 61，就按表里的 60 mm / 1 h（注意项目里没有 60 mm 雨型，runner 要新建））。新计划表 `推演计划表_30m_2026-10-15_v2.xlsx`（生成脚本 _build/mk_plan30.py）：13 个区域 × L1/L2 = 26 态；服务器1=MI-8/RD-F/RD-C/Qasr Al Bahr-1/RD-G/RD-A，服务器2=RB-1/RBE1-OF1/RD-I，服务器3=RB-3/MBZ/YAS OUTFALL/RD-14，计划 10-16 开跑。不排（用户10-15：没找到边界的不推演）：Bani Yas leak、RD-2、Al Riyadh、AAM，已从表中去掉。
**Why:** 缺 30 m runner 与上传包，尚未写；MBZ 边界为推断、MI-8 KMZ 251 km² 与用户表 36.53 km² 不一致需确认。
**How to apply:** 用户确认计划表后再写 runner（参考 rg_run_15m.py，域取 KMZ 多边形，格网 30 m）。相关：[[three-regions-l1l2]]。

**10-15 补充：** 计划表只用 ADM 汇水区名（RD-A 整块，Al Bateen 等只是里面的热点，不单独当区域）。成果必须输出积水点：积水点 = console/data/hotspots.geojson 里落在汇水区多边形内的 ADM 热点，13 区共 173 个（MI-8 58、RD-G 18、RBE1-OF1 15、RD-F/RD-I 14…，见 _build/hs_by_catchment.json）；runner 后处理沿用 rg_post.py 的 200 m 积水点统计，新增积水点结果表 + geojson 图层。v3 计划表有「积水点清单」「成果输出」两页。

**10-15 晚客户更新第二版表 → 计划表 v5（推演计划表_30m_2026-10-15_v5.xlsx，有「客户更新对照」页）：** RD-2 边界改「是」、加入 30 m（127 km²，86 个积水点，放服务器1）；MBZ「推断」→「是」；RD-14「子区」→「是」；Al Riyadh 客户说「是」但项目里仍无边界文件，不排，要向客户要；AAM 变 L1 / 100 m、边界空，待确认；RD-A 备注「Al Bateen 区域比 RD-A 区域大」→ 15 m 的 Al Bateen（按 RD-A 多边形）可能覆盖不全；表里的「机器4」我们没有。现 14 区 × L1/L2 = 28 态，服务器1=RD-2+RD-A/F/C/G/Qasr，服务器2=MI-8/RB-1/RBE1-OF1，服务器3=RB-3/MBZ/YAS OUTFALL/RD-14/RD-I。

**10-15 再更新 → v6（推演计划表_30m_2026-10-15_v6.xlsx，有「面积核对」页）：** 用户判定 RD-A 就是单独计算区域（客户 Al Bateen 备注是错的），Al Riyadh、AAM 不做。面积核对：KMZ 多边形对得上客户的 10 个（RD-A/F/C/I/G/Qasr/RB-1/RBE1-OF1/RD-14/RD-2）；MBZ 改用客户模型 Musaffah_RD09 子汇水区并集（15.67 km²，7 个积水点）；YAS OUTFALL 改用 Parsons Airport 01_02 并集（13.42 km²、4 块、0 个积水点）；RB-3（客户 15.56，KMZ 8.0，模型 18.61）、MI-8（客户 36.53，KMZ 250.78）没有任何数据对得上，标「待确认」先不跑，等客户给边界。备用计算域 geojson 在 _build/domains/。先跑 24 态。

**10-15 晚 → v7（推演计划表_30m_2026-10-15_v7.xlsx）：** 用户给了客户地图，面积 MI-8 250.62 / RB-3 7.99 / MBZ 18.07 / YAS OUTFALL 25.86 / RB-1 18.42 / RD-2 126.99，与 KMZ 多边形全部一致 → 客户计划表里 MI-8(36.53)、RB-3(15.56)、MBZ(15.26)、YAS(13.02) 是写错的；**14 个区计算域全部用 KMZ 多边形**（前面「MBZ 用 Musaffah_RD09 并集 / YAS 用 Airport 01_02 并集 / RB-3、MI-8 待确认」作废，_build/domains/ 备用文件不用）。积水点共 259 个。分工：服务器1=MI-8（250.6 km²）；服务器2=RD-2+YAS+RB-1（171）；服务器3=RBE1-OF1/MBZ/RD-14/RB-3/RD-A/F/C/G/I/Qasr（104，20 态）。28 态全部可开跑，runner 与上传包还没写。data/261015 里的两份数据与旧数据逐文件一致，没有新内容。

**10-15 夜 运行包已写好：** upload/ad_30m/server1|2|3/（各自含 ad_30m_serverN.sh、ad_run_30m.py、ad_regions.py、ad_post.py、c1_batch.py、cases/ad、h2d/；另有 .zip）。源码在 tools/ad_run_30m.py（由 rg_run_15m.py 改，生成脚本 _build/mk_ad_runner.py 只是起点，现以 tools 里的为准）、ad_regions.py、ad_post.py；打包 _build/mk_ad_packages.py。14 区代号 MI8/RD2/YASO/RB1/RBE1/MBZ/RD14/RB3/RDA/RDF/RDC/QAB1/RDI/RDG，情景名 <区>_M60H1。成果直写 NAS：result/<主机名>/<区域目录>_60mm1h_30m/（积水点结果表 csv + 图层 geojson、sww、tif、统计），推演全部完成后脚本自动 sww→shp+dat 放 shp_dat/<主机名>/…/<L1|L2>/，shp 用 --wet-only-shp --drop-threshold 0.01 筛掉最大水深<1 cm 的网格（用户 10-15 要求），转换镜像 h2d-convert:1.0（缺则用 h2d/ 离线构建）。线程按区域 AD_THREADS_MAP、32 核基准按核数缩放。本机用 RD-A 真跑通 build→compute(21 s)→sww→ad_post→h2d；整条调度（c1_batch + docker exec）、服务器上 NAS 挂载、MI-8/RD-2 的耗时内存都没实测。RD-2 有塘 SW_SCI_MSH44_PND_42（POND_WIRING 手写）、MI-8 里 SW_AWA_AWS_RZS_32 不建。

**10-15 夜 雨量改回 61 mm / 1 h**（用户再次改口；之前按表里写的 60 mm）：情景名 <区>_M61H1，目录 <区域>_61mm1h_30m，包与计划表 v7 已重生成。

**10-15 晚 61 mm/1 h 三台全部跑完：** 服务器1=MI8（1 个）、服务器2=RD2/YASO/RB1（3 个）、服务器3=其余 10 区，共 14/14 已交付 NAS（result 1/3/10 个情景），驱动日志 18:17/18:34/19:01 全部结束，三台空闲。shp_dat 计数（6/54/70）含早先批次的旧态，核对以 result 为准。除 61 mm/1 h 外的雨型还没排。

**10-16 viewer 接入 ADM：** 用户要在 flood-viewer 看 30 m 成果（逐帧播放）。原 ad_post 没出逐帧图是疏漏。新写 tools/ad_frames.py（读 c2d npz → 写 result/<主机>/runtime/console_data/flood2d_ad_<区>_m61h1g30/，区域代码 ad_<区>，含 manifest/stats/ad_stats.json、L1/L2 逐帧、diff），包 upload/ad_frames/{ad_frames.py,ad_frames.sh}（+zip），每台服务器跑 `bash ad_frames.sh`；server.py 已加 ad_ 区域标签 / ad_stats.json，ad_ 区域不取 C1 的雨型。本机只用合成数据 + 桩 rasterio 验过，真环境（anuga-swmm 镜像、MI-8 内存/耗时）没跑；viewer 要重新部署才有新 server.py；无雨型/设施/热点过程线。

**10-16 蓄水池盘点与重跑口径：** 项目 data 里塘去重后 221 个（已建成 23 / L1蓄水 17 / Parsons现状 19 / Parsons子模型 161 / 客户导出 1），清单 `蓄水池清单_2026-10-16.xlsx`（合并清单/汇总/ADM各区，脚本在 scratchpad 外的 TEMP，pond_inv.py）。用户口径：**分不清 planned 的一律按已建成（L2）算；有 planned 才做 suggested，没有就只到 L2**。Parsons 塘无已建/规划字段 → ADM 14 区全部只到 L2，没有 planned/suggested。ADM 区内共 77 个塘，5 m 内有客户管线可接入的只有 27 个（MI8 8、RD2 8、MBZ 4、RD-G 2、RB1 2、YASO 1、RB3 1、RD14 1；RBE1 0），现模型只建了 2 个（RD2、RD14）。重跑要新做接管线 / 库容 / 深度（ad_regions.POND_WIRING 现只覆盖 ponds_l2 的 23 座）。**尚未重跑。**

**10-16 用户选方案 2（所有塘都建，含不连管网的）→ 代码已改、包已刷新，服务器尚未重跑：** cases/ad/ponds_all.geojson（221 个去重塘，生成脚本 _build/ponds/）；ad_regions.write_ponds 改读它（域内代表点，76 座，RZS_32 仍不建）；ad_run_30m.auto_wire 在管网里找塘占地外 8 m 内的井接拍门管（最多 4 个），找不到 = 独立蓄水体（SWMM 孤立 storage 节点已在 anuga-swmm 镜像验过能收 set_total_inflow 并满库）；库容取法：来源库容/占地（0.1–5 m 内）→ 来源深度 → 缺省 1.0 m 平底。本机 network() 9 个区全部跑通：接管 25、独立 51（RB1 1/6、RD14 1/0、RB3 0/4、YASO 0/4、RBE1 0/2、MBZ 5/7、RDG 2/0、RD2 8/24、MI8 8/4）。compute_step 改成已有结果的态不重算；网络缓存改名 ad_net2_*.pkl。要重做 L2 的区：MI8、RD2、YASO、RB1、RBE1、MBZ、RB3、RDG（RD14 塘没变、无塘的 5 区不动）。服务器步骤：刷新包 → `bash ad_reset_l2.sh`（先列出）→ `go` → `bash ad_30m_server<N>.sh start` → `FORCE=1 bash ad_frames.sh`。旧 L2 结果被 mv 到 $NAS_BASE/old_L2_1016/。完整 build/compute 本机没跑（缺 rasterio）。

**10-16 晚补充：** 用户给 ponds.zip（Parsons 21 个子模型 152 个塘面，261015 版，字段统一 storage_ma/depth_avg）。对比：152 个全在我的 221 里；我多 69 个（Parsons 旧版 30、现状塘、L1 蓄水、全市 L2 等）；它的库容/深度更全 → 匹配上的 151 个改用它的属性（建模用库容 66 座来自来源、7 座缺省 1 m；它里面库容=深度=0 的 PD201 不建，RD2 现 31 座）。server sh 加了 frames_all（驱动末尾自动出 viewer 逐帧图，ad_stats 比逐帧图新就重做，`bash ad_30m_serverN.sh frames` 手动），包含 ad_frames.py；结果仍写原目录（reset 只把旧 L2 mv 到 old_L2_1016 备份，保证 shp 重新转换）。

**10-17 用户确认：** upload/ad_30m 三台脚本（含 L2 重跑口径）已在服务器上执行完，客户表 A1–A6、B7–B12、B14、B15 共 28 态都已跑。剩 B13 Bani Yas leak、B16 Al Riyadh ponds 没跑。B16 边界 = `Al Wathba catchments.kmz` 里 Pond 1/2A/2B/3/4/A/B/c/D 共 9 块，合计 76.19 km²（客户 76.15）；B13 = 109 库（flood 库 10.255.254.109:5443，catchment.catchment）里的 `RD RB2`（3 块，131.31+1.15+0.36 km²，大块即 RD-2/B14），所以 B13 与 B14 基本同一域。两项是否排进、B13 怎么处理，待用户定。

**10-17 B16 在服务器2上跑（用户要求）：** ad_regions.REGIONS 加 `ALR`（feats = 9 块 Pond 并集 75.42 km²，重叠已去；客户 76.15），域内 13 座全市 L2 塘（新 `linked_wiring()` 按 out/pondlink/ponds_linked.json 推接法，不再手写）、17 个积水点（hotspots_ad.json 重生成共 276）、无现状泵。包 `upload/ad_30m/server2_alr.zip`（= server2 目录 + `ad_30m_server2_alr.sh`：TAGS=ALR_M61H1，SLOTS=2，每态 16 线程）。本机验到：域/塘/热点生成、network("ALR") 建网通过（39,249 节点、21 根接塘管、0 出水口）；DEM/整条 build/compute 没跑（缺 rasterio）。Pond 14/10/11 等不在域内；Parsons 的 Al Wathba 塘（ponds_all 只覆盖 ADM 14 区）没建。viewer 的 ad_ALR 区域标签没加。

**10-17 96 mm / 2 h 批：** 计划表 v9（推演计划表_30m_2026-10-17_v9.xlsx）加「推演2h96mm」页（15 区含 B16 × L1/L2 = 30 态，分工同 61 mm/1 h，日期待定）。ad_run_30m.RAINS 加 (96,2)，情景名 <区>_M96H2，成果目录 <区域>_96mm2h_30m；雨型取 Mussafah 基准 .inp 的 2 h 那条（累计 51.9 mm 等比放大，本机验过）。包 `upload/ad_30m/server{1,2,3}_m96h2.zip`，脚本 `ad_30m_server{N}_m96h2.sh`（服务器2 带 RD2/ALR/YASO/RB1 共 8 态，每态线程 8/5/2/1）。本机只验了雨型与 SCEN，build/compute 未跑；服务器尚未执行。
