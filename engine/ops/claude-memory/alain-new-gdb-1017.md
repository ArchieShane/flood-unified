---
name: alain-new-gdb-1017
description: 2026-10-17 艾因新一版数据 data/2026-10-17（AAM_SW_Data_25092026.gdb + AAM_stormwater_vector.gpkg）与旧版差异、缺项清单
metadata:
  node_type: memory
  type: project
  originSessionId: e0cc537f-82a3-4864-8e63-3ffa7bb91cdb
  modified: 2026-10-17T08:51:30.276Z
---

10-17 用户给了艾因新数据，要跑“全市 250 m 61mm/1h”先查缺（我按艾因 ALAIN_M61H1 理解，未再确认）。新 GDB 是**原始版**：没有 Processed 里的 StartCode/EndCode/StartX.../StartElev/Diameter/PointCode，现有 aa_cases/aa_run_250m 直接读会失败，要先写预处理（StartCode=起点坐标取整 X_Y，坐标用几何不用属性——22 行属性 X/Y 颠倒、378 行与几何差 >5 m）。
新旧差异：管 188,621（旧 177,318，UNITID 仅新 12,285/仅旧 991，9 个重复）；全为 Active；有管内底 16,256（8.6%，旧 4,103）；INLET 10.4 万（旧 16.2 万）、MANHOLE 3.9 万（旧 9.7 万）大减；PS_PUMP 58 台/29 站（旧 50），湿井 30 个、4 个深度 0；POND 仍 1 座；SPILLWAY 498 面（新）；gpkg 里 aam_pond_existing 13 个塘（GDB 只有 1 个）。
**Why:** 艾因脚本按旧 Processed 库写死。**How to apply:** 跑之前先做预处理 + 重跑 aa_cases；相关 [[alain-250m-runs]]。

**10-17 后续（用户定：不在本机跑，上服务器跑）**：已写 tools/aa_preprocess.py → data/2026-10-17/AAM_SW_Processed.gpkg（端点码与旧版 17.6 万根重叠管 100% 一致，节点 202,215，出水口能对上 343/400）；aa_cases.py / aa_run_250m.py 已改读该 gpkg；重生成 cases/alain（29 站泵、1 塘；新增两座泵站 ID 填 "NA"/"Others"，改名 <前缀>_PSS_NEW，均 4×240 L/s、扬程 16 m、湿井 16.05 m，疑似占位值）。上传包 upload/aa_250m 已刷新（gpkg 53 MB 取代旧 630 MB gdb），aa_250m_server.sh 缺省 TAGS 首个 = ALAIN_M61H1；旧 json 备份在 cases/alain_old1015。服务器上还没跑。

**塘（10-17 用户要求“用上全部的塘”）**：aa_cases.py 现生成 14 座塘 = GDB POND 1 座 + AAM_stormwater_vector.gpkg/aam_pond_existing 13 个（含在建 9 个、天然湖 Zakher/Duck）。gpkg 只有点位+面积+最大深度+库容，没有塘底和轮廓：轮廓取等面积圆，等截面面积=库容/深度，塘底按 DTM（圆内起伏≥半深取 P10，否则中位数−深）；1D 进水只有 3 座接上出水口（≤150 m 或放宽 500 m），其余 10 座离管网 0.2–1.7 km 没接，靠二维占地内地表水进塘（couple2way_v2）。Duck Lake 面积×深 与台账库容差 10 倍，库容以台账为准。
- 10-17：aa_250m_server.sh 已重写（后台驱动，算完自动 sww→shp+dat 写 $NAS_BASE/shp_dat/<主机>/艾因_模型版_<雨量>mm<历时>h_250m/<L1|L2>/，h2d/ 来自 ad_30m；aa_post 本身不转 shp/dat）；包 upload/aa_250m(.zip) 已含新库、14 塘、h2d
- 10-17 用户选方案2 且「不用修改」：按现状展示，代码/说明不改（已撤销我加的 no_surface 与说明段）。M61H1 实测：14 塘仅 4 座全在二维域内、3 座部分、7 座域外；L1/L2 几乎无差别；结果在服务器 /media/pop-fs/flood_data/shp_dat/<主机>/艾因_模型版_61mm1h_250m/
- 10-17 viewer 艾因：补热点统计用 tools/aa_hotspots.py + cases/alain/aa_hotspots.json（AAM 184 热点），包 upload/flood-viewer-update_aa；viewer 在 /mnt/AI/flood/swbrain/flood-viewer、端口 8000，server.py/app.js 靠 compose 挂载；NAS 里目录须 flood2d_aa_alain_m61h1g250
- 10-18 用户选方案2：L2 给有一维进水的塘加溢流堰（堰顶0.98×深，宽20m，溢出系统，Claude 自定假设）：aa_run_250m.add_pond_spill；服务器脚本新增 rerun-l2（只重算 L2，备份旧结果）。未在服务器验证，SWMM 读入由 build 的 chk 步骤把关
- 10-18 用户：验收只需确认 L2 溢水量(一维域内井溢流) < L1，其余仅提示（aa_accept 已改）；自检 L2_spill 冒水 ≤ L1×1.05+0.005；堰顶=进水管出口内底（本机 anuga-swmm 真 SWMM 验证）
- 10-18 用户改口径：排进塘的管在 L1 不是出水口（封闭），L2 接塘，无溢流堰；点塘不接一维；L1+L2 都重算（rerun-l2 同名）。本机真 SWMM：合成 L1 .337/L2 .187；真实块(428节点) L1 5519/L2 54 m³
