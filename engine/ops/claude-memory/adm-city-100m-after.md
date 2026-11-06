---
name: adm-city-100m-after
description: 2026-10-26 全市治理后数据 100 m 61mm/1h L1+L2，只服务器1；包 upload/adm_100m_after，一条龙含 shp+dat 与 viewer；未上服务器
metadata:
  node_type: memory
  type: project
  originSessionId: 0b1cf234-81e3-4c7d-96a7-5339865e3c8f
  modified: 2026-10-25T21:33:49.396Z
---

用户 10-26 要用 Z:\flood\全市治理后数据_20261025（GDB，含 LEVEL/PUMP_UNIT/PUMP_CURVE/WETWELL）重跑全市 100 m、61 mm/1 h、L1/L2，只服务器1，DTM 用服务器上的，结果自动转 shp+dat、输出 result、自动进 flood-viewer。
用户的口径答复：L1=既有管网+有流量泵（无塘），L2=L1+塘/渗井，**不看 LEVEL**，管网类只剔除 Abandoned/Expired/Removed（Proposed 照用，同 10-21 v2）；缺参数泵塘沿用旧规则（泵要流量/曲线才建，塘估算并标 estimated）；viewer 新区域“Abu Dhabi 全市（治理后）”目录 flood2d_ad_cityafter_m61h1g100；计算域 1,233.6 km²、CN82/Tc30、Mussafah 雨型同前。
产物：tools/adm100a_net.py（开发机重建 pkl）、upload/adm_100m_after(+.zip)：adm100a_run/post/frames.py、adm_100m_after_server.sh/cleanup.sh、README.txt。前缀 AF、容器 afpipe、标签 CITYAFTER_M61H1。shp_dat 在 NAS/shp_dat（与 result 同级，H2D_ARGS 缺省 --no-csv 不过滤 1 cm）。
数据事实：只能建 38 个泵组（旧版 159；治理后 GDB 无 PUMP_FLOW，FLOW_RATE<10 是泵台数，曲线只 ~30 站有）；塘 148 座 145 座估算；RD-DY 泵机组重复登记已去重。
**Why/How to apply:** 本机用 4.5×3.5 km 小域（含 1 泵 3 塘）跑通 compute→post→frames→h2d→server.sh verify→viewer scan；全市内存/耗时未实测（两态同时算每态 22 线程，约 57 万三角单元）。相关 [[adm-city-100m-merged-data]]、[[server-run-delivery-checklist]]、[[flood-viewer]]。
