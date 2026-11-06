---
name: server-run-delivery-checklist
description: 给服务器出推演脚本时必须一次做全的交付链（输出位置、shp+dat、flood-viewer 展示）——2026-10-17 艾因一次漏了一串
metadata:
  node_type: memory
  type: feedback
  originSessionId: e0cc537f-82a3-4864-8e63-3ffa7bb91cdb
  modified: 2026-10-17T12:24:33.174Z
---

用户 2026-10-17 在艾因 250 m 上批评：输出目录没讲清、shp+dat 没自动转、没自动进 flood-viewer，上 viewer 的过程中又连续漏了好几处。以后给服务器写/改“跑一批推演”的脚本，**交付链必须一次做全并先验证，不要等用户踩一个我再补一个**：

1. **一开始就写明所有输出在哪**（容器 out/交付、NAS result、shp_dat、viewer 用的 console_data），脚本结束时也打印出来。
2. **shp + dat 要自动转**（算完后，参数口径写明：缺省 --no-csv --wet-only-shp --drop-threshold 0.01，shp 过滤、dat 全域），不是让用户手动。
3. **flood-viewer 要自动可见**，对照 flood-viewer/server/server.py 和 web/app.js 逐项核：目录名必须 `flood2d_<区域>_m<mm>h<h>g<网格米>`（缺 g 会被当 15 m）；统计文件要有 `zones`，键 `全域|all`、`<热点>|200m`、`N 个热点|200m`（没有热点分区只显示一张卡）；区域显示名在 REGION_LABELS；side_lookup 要给新区域排除（否则撞 C1 同名 tag）；server.py/app.js 靠 compose 挂载，改了要 `docker compose up -d --force-recreate`；viewer 端口以实测为准（这台是 8000，不是 8002）。
4. **自己先对着代码验证，再告诉用户“能显示”**；别把推断说成结论。
5. **别给容易误匹配的 sed**（把 swbrain 的 compose 改了）；先让用户用 docker inspect 确认目录；给用户的命令一条一块、不要多行 python -c（换行会被转成 <br/>）；提到“我写的文件”必须真的交给用户（SendUserFile），不能只说在我这边目录里。
6. 服务器没有 kill/pgrep 等命令（容器里），别假设有。

**Why:** 用户在服务器上一步步照做，我的疏漏会让他反复返工、对结果失去信心。
**How to apply:** 参考做法见 [[alain-new-gdb-1017]]、[[flood-viewer]]、[[nas-result-sync]]；ADM 30 m 脚本（upload/ad_30m/server1/ad_30m_server1.sh）已经是“算完→转 shp+dat→逐帧图进 NAS”一条龙，新脚本照它的链条。
- 10-17 艾因 统计值.csv 必须是表头「区域,口径,指标,<各态>」（同 c1/ms/rg，含逐热点行），旧 aa_post 少了区域列、下游按此解析全部对不上并跳过积水点结果表；已改 aa_post + aa_hotspots.write_stats_csv。下游解析代码不在本仓库
- 10-17 艾因交付 公共/ 也必须有 计算域.geojson(EPSG:4326)/现状泵站.json/已建成塘.json/积水点.json（读端 sim_scope 假定都有；之前只有 输入参数.json）；已加进 aa_hotspots.write_public 与 aa_post
- 10-18 用户要求：rerun-l2/start 都要自动 同步NAS(nas_delivery 直接拷+触发 sync_to_nas.sh)、进 viewer、不过滤1cm shp+dat(H2D_ARGS 缺省 --no-csv)、交付文件一个不少(verify_delivery 逐项核)；已写 finalize；aa_accept 做 L2 不比 L1 差的验收；aa_spill_selftest 开跑前自检
