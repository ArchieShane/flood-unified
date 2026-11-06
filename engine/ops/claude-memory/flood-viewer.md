---
name: flood-viewer
description: 2026-10-10 新写的独立展示前端 flood-viewer（三个下拉框看 NAS 成果），位置、数据约定、未验证项
metadata:
  type: project
---

用户 2026-10-10 要求新写一套前端（不替换原 console），只有三个下拉框：区域 → 格网精度 → 模拟雨量，选完展示 C1 页同样内容，最终 docker 部署。
实现在 `C:\freedo\zn-260930\flood-viewer\`：标准库 Python 服务 server/server.py + 原生 JS/Leaflet 前端 web/，Dockerfile + docker-compose.yml（NAS result/ 只读挂 /data，缺省端口 8090）。说明见该目录 README.md。

数据约定：扫 `<DATA_ROOT>/<主机名>/runtime/console_data/flood2d_<区域>_m<mm>[h<h>][g<网格>]`；区域 c1 / ms；多机重复取 manifest 最新；c1_hyeto.json / c1_hs_series.json 跨主机按键合并；旧口径目录（r61/u61/d24）不展示。

**Why:** 原页面功能多、情景选择逻辑有问题。
**How to apply:** 没看到 NAS 真实目录，路径布局是按 [[nas-result-sync]] 的 sync_to_nas.sh 推断的；Docker 镜像本机没起 daemon，尚未构建验证；用 C1 本地 15 m 数据验证过服务和页面。Mussafah（ms_stats.json）没有设施/雨型/热点过程线，页面对应块为空。
