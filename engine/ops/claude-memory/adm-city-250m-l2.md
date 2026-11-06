---
name: adm-city-250m-l2
description: 2026-10-19 ADM 全市 250 m 9 场只算 L2，三台固定分工，不进 viewer/aio、不转 shp+dat；包 upload/adm_250m
metadata:
  node_type: memory
  type: project
  originSessionId: a789a4c4-e89a-4f5d-9217-6bc78cf4dbee
  modified: 2026-10-19T13:28:49.451Z
---

用户 10-19 定：Abu Dhabi 全市（计算域 1,233.6 km²，同 city_run_250m）、33/61/96 mm × 1/2/24 h = 9 场、二维 250 m、**只算 L2**、ADM 口径（CN82/Tc30/现状泵站 + ponds_all 已建成塘/积水点表 470 个）。**不做** L1、逐帧图/flood-viewer、shp+dat、flood-aio（用户决定“暂时不展示”）。固定分工：S1 M33H24 M61H24 M96H24 M33H1（SLOTS 3）；S2 M61H2 M96H2；S3 M33H2 M61H1 M96H1。
包 `upload/adm_250m/`(+zip)：adm_250m_server.sh / adm_250m_cleanup.sh / adm250_run.py / adm250_post.py（由 ad_run_30m / ad_post 改，新文件名不覆盖老的，前缀 AX、容器 axpipe）/ cases/ad250。源码在 thinking-drainage-system/tools（adm250_regions.py 生成案例）；计划表 `ADM全市250m_L2_计划_2026-10-19.xlsx`，生成脚本 _build/mk_adm250_plan.py、mk_adm_250m_pkg.py。

**Why:** L1/L2 计算互相独立，只有后处理对比才要两态；viewer app.js 写死 L2 的上级是 L1，只有 L2 会显示不全，所以本批干脆不展示。
**How to apply:** 本机验证过 prep/build（253,307 节点、32 座泵、211 座塘、约 9.1 万三角单元）和用合成数据测 post、verify；docker 没起，compute/chk/cleanup/start 没实测，内存/耗时没实测（SLOTS = 内存GB/60 是猜的）。以后要展示，从交付目录 .sww/.npz 补，且 viewer/aio 要先支持“无 L1”。相关 [[server-run-delivery-checklist]]、[[adm-rest-49-tasks]]、[[city-250m-runs]]。
