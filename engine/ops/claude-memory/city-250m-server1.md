---
name: city-250m-server1
description: 10-14 用户让全市 250 m 改在服务器1 全力跑；runner city_run_250m.py / ct_post.py 已写，上传包 upload/city_250m，未上传
metadata:
  type: project
---

10-14 用户说服务器1 已空闲，全市 250 m（18 态）改由服务器1 全力跑（原排服务器2 10-19）。已写 tools/city_run_250m.py（前缀 CT、情景 CITY_M<雨量>H<历时>、250 m DEM 由 50 cm DTM 块平均一份共用）、tools/ct_post.py、tools/chains/city_250m_server1.sh，包在 upload/city_250m(.zip)。产流缺省 v2（客户子汇水区参数，同 30 m 版，要 out/citywide/node_runoff.json），CITY_RUNOFF=v1 可改统一 CN82。缺省 5 槽×8 线程；先 TAGS=CITY_M61H2 试跑量内存/耗时再全排。
**Why:** 只在本机做了 import 和 c1_batch plan 干跑，没在容器里实跑过；dock 自带 C2D_DEM_RAW=1，所以 DEM 必须预先平均到 250 m。
**How to apply:** 计划表 v41 的 250m 页还是服务器2/10-19，用户确认后另存 v42 改。相关：[[city-250m-runs]]。
