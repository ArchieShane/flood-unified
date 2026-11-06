ADM 全市 250 m（L2）成果 → 客户英文版导出（2026-10-19）

目的：把 9 场成果（33/61/96 mm × 1/2/24 h）整理成可以发给客户的一份：路径、文件内容里都没有中文。不动 NAS 上的原始成果（容器里 NAS 是只读挂载）。
发给客户的内容：全部（.sww、.npz、.inp、.rpt、最大水深 tif、积水点结果表 csv/xlsx、图层 geojson、统计、公共输入、说明）。

什么时候做：9 场都算完、三台的 bash adm_250m_server.sh verify 都通过之后。三台共用一个 NAS（result/<主机>/），在任意一台上跑一次就能收齐 9 场。

步骤（在服务器上，包目录里）：
  1. bash adm_250m_export_en.sh list
     列出 NAS 上找到的 9 场（哪台主机、多少个文件）；缺场次会标 ✗。
  2. bash adm_250m_export_en.sh export /你的目标目录 --dry-run
     只打印 源 -> 目标。目标目录要在 NAS 的 result / shp_dat 之外，且是空目录；体积大（.sww / .npz），先 df -h 看空间。
  3. bash adm_250m_export_en.sh export /你的目标目录
     拷贝 + 路径改英文 + 内容改英文 + 扫描有没有中文 + 写 README.md 和 MANIFEST.csv（每个文件的大小与 sha256）。9 场不齐会拒绝导出（--allow-partial 才导出已有的）。
     可重复执行：.sww / .npz 已拷过（同大小、不比源旧）会跳过。
     结尾必须看到：扫描 N 个文件：✓ 路径、文本内容、xlsx、tif 元数据、.sww 属性、.npz 成员名里都没有中文。
     如果列出「映射表没覆盖的字符串」或 ✗，把输出发给我补映射表后重跑，不要把有 ✗ 的目录发给客户。
  4. 需要时单独复查：bash adm_250m_export_en.sh verify /你的目标目录

改了什么（原始文件不变，只改拷贝件）：
  · 目录 / 文件名：L2_已建成调蓄 → L2_built_storage，积水点 → hotspots，统计 → stats，公共 → common，一维 / 二维 → 1d / 2d，最大水深 → max_depth …（积水点结果表 → hotspot_results 等）
  · csv / xlsx：表头、行名、状态值（"否" → no，"浅积水" → shallow ponding …）
  · json / geojson：键与值（泵站 / 塘的备注、水量对账项名称、路径字符串）
  · 00_说明.md / 计算设置.md：重写成英文版 00_README.md / calc_settings.md（数字取自原文件与 ad_stats.json）
  · .inp / .rpt：含中文的注释行去掉中文（.inp 第 2 行的标题注释改成英文；非注释行含中文会直接报错，不改模型）
  · tif：波段描述改英文
  · .sww / .npz：原样拷贝（只检查 NetCDF 属性 / zip 成员名里有没有中文）
  · 积水点的位置名里的阿拉伯文原样保留（不是中文）。

本机验证过：用上次合成成果在本机跑通整条 export + verify（含 xlsx、tif、.sww 属性、.inp / .rpt 注释行）；拿原始中文目录跑 verify 会报错。
没验证过：在服务器的 anuga-swmm:1.0 容器里跑（容器里要有 python 和 rasterio；.sww 用 scipy / netCDF4 读属性，没有就跳过这一项）；真实 9 场的耗时（.inp 约 60 MB × 9 要逐行处理，几分钟；sha256 对大文件也要时间，--no-hash 可省）。
