# 系统、报告、交付

## 系统（控制台 C1 页签）
- 数据目录 `console/data/flood2d_c1_<tag小写>/`（manifest、stats、c1_stats、逐帧 PNG、差值图）；新 tag 要在 `console/app.js` 的 `C1V.r`、`c1Dir`、`c1Mm`、雨段按钮和 `i18n.js` 里加上，并把 `index.html` 里 `app.js?v=` / `i18n.js?v=` 版本号加一。
- 有：多态切换、对比基准、逐帧 / 全程最大 / 差值、积水面积曲线、雨型图（`c1_hyetograph.py`）、重点热点过程线、各热点表、设施符号（类型分形状颜色，点开看参数与实际表现）。
- 截图：`python tools/console_shot.py out/c1_shots/<tag>_<态>.jpg "tab=line2p&v=c1&r=<tag>&st=<态>" --js "…收起图层面板；点全程最大…" --wait 8`。
- 改完 JS 先 `node -e "new Function(require('fs').readFileSync('console/app.js','utf8'))"` 查语法（i18n 英文里的撇号会把整个文件弄坏）。

## 报告（`tools/report_c1.py` → `out/c1_report.html` → Artifact 发布到同一个 URL）
章节：先说结论 · 降雨（雨型）· 结果（各态表 + 截图）· 各计算域版本一节 · 重点热点水深过程 · 各热点 · 四个态分别是什么 · L2+suggested 措施与副作用 · 设施实际发挥 · 设施容量核算 · 水量对账 · 计算设置（20 项）· 要一起看的保留。
每改一次重新发布（同一 URL），告诉用户版本号。

## 交付目录（`python tools/export_delivery_c1.py --tag <tag>`）
```
out/交付/C1_<版本>_61mm2h/
  readme.md  计算设置.md  统计值/
  公共/  计算域.geojson  二维地形_5mDTM原值.tif  8个热点的地表与管网汇水区.geojson  <汇水区文件夹>/
  <态>/  摘要.md  设施.csv
         一二维/  *_二维水深与流速时序.sww  *_二维水深帧与最大水深.npz  *_耦合运行记录.json  *_积水面积时序.csv  耦合的一维/（.inp .rpt）  GIS/（GeoTIFF）  逐帧水深图/
         设施GIS/  蓄水池_规划塘  蓄水池_箱涵  泵站与泵点  排水去向  地面取水片（GeoJSON WGS84 + Shapefile UTM 40N）
```
Shapefile 字段名最长 10 字节——用英文短名，GeoJSON 保留中文，对照表写进说明。文本字段最长 254 字节。
