"""一二维建模数据治理管道（按 docs/一二维建模数据标准_v0.1.md）。

分层：0 原始层（raw）→ 1 标准化层（standardize）→ 2 清洗层 → 3 融合层 → 4 加工层 → 5 资产层。
现在有：
  python -m tools.dgov.raw register --source <id>          登记原始数据：文件哈希、图层、字段、Esri 加工历史
  python -m tools.dgov.standardize --source <id> --extent <名>   按映射配置转成标准结构（GeoPackage），逐值带来源和置信度
  python -m tools.dgov.profile --gpkg <标准化结果>           完整性、置信度分布报告
配置在 tools/dgov/config/：sources.yaml（数据源登记）、extents.yaml（范围）、map_*.yaml（每个来源一份映射）。
产物在 out/dgov/（不入库）。
"""
STD_VERSION = "0.2"
