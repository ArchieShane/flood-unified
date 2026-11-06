# dataqc — 雨水管网与 DTM 数据质量检查

从 2026-09-27 的《雨水管网与 DTM 数据质量报告》还原：判定逻辑逐条移植自空间数据智能平台的
`m4-data`（`管网图层识别` / `管网体检` / `管网补充检查` / `栅格分块统计` / `栅格体检`，Java ＋ PostGIS），
报告生成器与模板还原自当时的 `生成报告_管线与DTM.py` / `报告模板_管线与DTM.html`。

**为什么要有这个包**：原链跑在平台里（PostGIS ＋ 上传的暂存库），换一台没有平台的机器就跑不了。
这里把判定全部用 geopandas / shapely / rasterio / scipy 重写，读 GDB 或 GeoPackage 或 shapefile，
不连库、不写客户数据。

## 两种版本（同一套判定）

| 版本 | 用什么 | 怎么跑 |
|---|---|---|
| **无 ArcGIS** | geopandas（pyogrio 或 fiona）＋ shapely ＋ rasterio ＋ scipy | `python -m tools.dataqc ...` —— 本仓库的常规 Python 即可 |
| **有 ArcGIS** | 同上，另加 arcpy 的拓扑规则与追踪网络做**独立复核** | `"D:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" -m tools.dataqc ... --arcgis` |

⚠️ 复核是**加项**，不是依赖：判定与数字两种情况完全一样。加 `--arcgis` 会在报告附录 A 里给出
「本程序 vs ArcGIS」的逐条对照（差异逐条给原因）。本机 ArcGIS Pro 只有 ArcInfo 产品、
没有追踪网络许可，所以连通性（C4）那一行照实标「没做」，拓扑规则那几行照常。

## 用法

```bash
# ① 整条链：管网 + DTM（DTM 在管网外框外扩 100 m 内统计，报告原口径）
python -m tools.dataqc \
  --pipes "D:/阿布扎比/DMT_StormWater_AbuDhabi_Processed.gdb" \
  --dtm   "D:/阿布扎比/DTM_z40_customer/AUH_DTM_5m_Z40.TIF" \
  --out out/dataqc/全市_管网与DTM --gpkg

# ② 只查 DTM，范围给一个面（C1 计算域、行政区、汇水区都行）
python -m tools.dataqc --dtm "...AUH_DTM_5m_Z40.TIF" \
  --domain out/units_from_models/c1_model_domain.geojson --domain-name "C1 模型版计算域" \
  --out out/dataqc/C1_DTM

# ③ 管网也裁到范围里（模型域内的一二维用得上）
python -m tools.dataqc --pipes ... --dtm ... --domain ... --clip-pipes --out out/dataqc/C1

# ④ 有 ArcGIS：同一个命令，换 ArcGIS Pro 的 Python 并加 --arcgis
"D:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" -m tools.dataqc \
  --pipes ... --arcgis --out "D:/swbrain_work/dq_arcgis"     # 路径别带中文，ArcGIS 工具认不了
```

产物：

| 文件 | 是什么 |
|---|---|
| `report.html` | 报告（单文件，数据内嵌，可直接发给客户） |
| `result.json` | 全部结果：逐项计数、**全量编号**、阈值清单、识别到的图层与字段映射 |
| `issues.gpkg` | 每类问题一个图层（`--gpkg`），字段与 2026-09-27 那份相同 |
| `arcgis/` | `--arcgis` 时：拓扑错误、追踪结果、对照表（`arcgis.json`） |

## 参数

| 参数 | 说明 |
|---|---|
| `--pipes` | 管网：`.gdb` 目录（含多套一层的情况）、`.gdb.zip`、`.gpkg`、shapefile 目录 |
| `--dtm` | DTM：GDAL 可读的栅格（本包用 rasterio） |
| `--domain` | 统计范围面：GeoJSON / GPKG / shp。DTM 只统计面内的格 |
| `--clip-pipes` | 管网也只取与面相交的管段、落在面内的节点 |
| `--dtm-scope` | `network`（管网外框外扩 100 m）／`domain`（计算域）／`full`（全图） |
| `--tolerance` | 连通判定容差，缺省 0.5 m（与 ArcGIS 追踪网络比对时用 0.001 m） |
| `--datum-offset` | 台账高程 − DTM 的基准差（米）；缺省从井盖与 DTM 自动估，取整米 |
| `--thresholds` | 阈值覆盖 JSON，**键用中文名**，例：`{"覆土下限": 0.5, "footprint_buffer": 30}` |
| `--extra` | 报告附加内容：`notes`（逐项说明）、`advice`（额外建议）、`dq`（与客户侧质量报告的对照表）、`footer` |
| `--gpkg` | 导出问题图层 GeoPackage |
| `--arcgis` | 加 arcpy 独立复核 |

阈值与出处、严重度、整改途径都在 `thresholds.py`，报告里第三节末照原样列出来。

## 数据源换一份要动什么

判定不写死 DMT：换一份数据不用改代码，靠两件事。

1. **认图层与字段**（`layers.py`）：按名字惯例认（`unitid`、`pipe_diameter`、`invert_level_upstream`、
   `asset_before`…），认到的结果原样进 `result.json` 的 `networkMeta.mapping`，认错了看得见；
   认不出的角色照实报「缺」，不猜。合并副本（`Merge*`）跳过。
2. **报告措辞**：DMT 专属的话（「客户原始资产库有 34,141 个检查井」之类）不在代码里，
   用 `--extra` 注入。`notes` 按检查编号覆盖说明，例如
   `{"notes": {"C8": "引用编号的类型：MH 95,178、其他格式 20,078…"}}`。

`--dtm-scope`、`--domain`、`--clip-pipes` 三个参数合起来就能回答「换一个数据源／换一个范围报告长什么样」。

## 判定口径（与平台逐条相同）

- **管网 A～F**：A 属性、B 几何、C 拓扑与连通、D 水力（重力管）、E 与 DTM 一致、F 设施。
  计数与 `D:/产品/code/app/target/管网体检_0.5.json`（平台在整份 DMT 雨水 GDB 上的正式结果）
  逐项对过：**57 项里 56 项完全一致**，剩下的 C10 只有分母不同
  （平台那处把 C9 的累计分母赋给了 C10，命中清单 2,687 条与顺序完全相同）。
- **DTM R1～R14**：R1～R7 看元数据（坐标系、高程基准、像元、NoData、覆盖、值域、金字塔），
  R8～R12 逐格算（足迹内无数据、单点尖峰、单点深坑、平地、低于 −5 m 的连片洼地），
  R13 陡坎、R14 坡度是本次加的（原报告没有）。
  另给高程统计：最低/最高（带坐标）、均值±标准差、0.1%～99.9% 分位、高程分布直方图。
- **连通性容差**：吸附用 `容差 + 1 µm` 吃浮点误差（毫米数字化的数据差 1 mm 算出来是 0.00100000016）；
  重合节点并成同一顶点，否则叠在同一点的出水口会让整片子网被误判成没有出口。
- **端点吸附**逐字照 Java 的网格实现（3×3 格、格内顺序、距离相等时后者胜）：
  用 cKDTree 找最近会与平台差几十到几百条。
- **层的读取顺序**按层名字母序，与平台从库里取的顺序一致 —— 它决定端点挂到哪个重合节点上。

## 模型地形体检（2026-10-21 加）

原始 DTM 干净，不代表模型实际用的地形干净：重采样、取块平均、赋给三角网这几步都可能出错。比如全市 250 m v2 那次：

- NoData（3.4e38）混进块平均，变成 937 个「高墙」单元；
- 还有 10 个 −661 m 的「深坑」单元；
- 原始 DTM 在那里其实是 0–11 m。

R1–R14 只查原始栅格，查不到这一步，所以另加 `model_terrain`：

```bash
python -m tools.dataqc.model_terrain --terrain <*_2d.npz | *.sww | 重采样后的 DEM.tif> --dtm D:/阿布扎比/DTM_z40_customer/AUH_DTM_5m_Z40.TIF --block 250 --out out/dataqc/<名字> --gpkg
```

- **参照**：原始 DTM 在单元范围内的平均。先按「单元边长 / 6」平均降采样，再用积分图求窗口平均；全市 9 万个单元约 10 秒。
- **`--block`**：模型赋高程时取块平均用的块大小。给了它，块平均值不可能超出块内原始地形的最低～最高，M4「超出周边地形」就只报真正的处理错误。不给就按单元边长算，沙丘区会多报一些。
- **判定**：
  - M1 无效值
  - M2 越界
  - M3 参照缺失
  - M4 超出周边地形（处理错）
  - M4b 平滑（取块平均的正常结果，提示）
  - M5 系统偏差（基准不一致）
  - M6 偏差分布
- **阈值**：`模型地形偏差提示` 1 m、`模型地形偏差严重` 5 m、`模型地形系统偏差` 0.5 m，可用 `--thresholds` 改。
- **产物**：`model_terrain.json`、`model_terrain.md`；加 `--gpkg` 时多一个 `model_terrain.gpkg`（问题单元点层）。
- **坐标系**：npz、sww 不带坐标系，缺省认为与 DTM 相同。
- **sww 读取**：用 netCDF4 读，没装就用 scipy 读（ANUGA 的 sww 是 NetCDF3）。
- 每次二维计算交付前跑一遍，M1、M2、M4 应为 0。

## 自检

```bash
python -m tools.dataqc --pipes <小样.gdb> --out /tmp/x            # 跑通
python - <<'EOF'                                                   # 与平台结果逐项对
import json; ref=json.load(open(r'D:/产品/code/app/target/管网体检_0.5.json',encoding='utf-8'))
mine=json.load(open('/tmp/x/result.json',encoding='utf-8'))['network']
print({k:(v['count'],v['total']) for k,v in ref['checks'].items()}=={k:(v['count'],v['total']) for k,v in mine['checks'].items()})
EOF
```

## 已知边界

- **ArcGIS 路径别带中文**：`arcpy` 的地理处理工具在中文路径下会报 `ERROR 000110` 一类
  （与 SWMM 同一个毛病）。网页与 JSON 不受影响，只是 arcpy 那一段要指到纯英文目录。
- **追踪网络要许可**：没有 ArcGIS Pro 的对应产品时 `CreateTraceNetwork` 能建但 `Trace` 报
  `ERROR 001785 No valid starting points found`，本包会照实记「没做」。
- **`.gdb` 里的 Merge 层**跳过不查（算进来每条管会算两遍）；它比各层多／少多少要素报在「数据概况」里。
- 平台那两项（B7 重复管段、C7 相交未连接、C11 端点落在管段中间）原来在 PostGIS 里算，
  这里用 shapely 的 STRtree 算（`crosses` 与 `dwithin`），口径相同。
