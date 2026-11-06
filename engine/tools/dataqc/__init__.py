# -*- coding: utf-8 -*-
"""雨水管网与 DTM 数据质量检查（离线版）。

从 2026-09-27《雨水管网与 DTM 数据质量报告》的生成链还原：判定逻辑逐条移植自平台
m4-data 的 管网图层识别 / 管网体检 / 管网补充检查 / 栅格分块统计 / 栅格体检（Java），
报告生成器与模板还原自当时的 生成报告_管线与DTM.py / 报告模板_管线与DTM.html。

两种运行方式，判定是同一套代码：
  - 无 ArcGIS：纯 Python（geopandas / shapely / rasterio / scipy）；
  - 有 ArcGIS：同上，外加 arcpy 的拓扑规则与追踪网络逐条复核（报告附录 A）。

用法见 ``python -m tools.dataqc --help``。
"""
