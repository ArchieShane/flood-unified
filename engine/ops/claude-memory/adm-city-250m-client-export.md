---
name: adm-city-250m-client-export
description: 2026-10-19 全市 250 m L2 九场结果发客户：必须英文、路径和内容都不能有中文，包 upload/adm_250m_export（全部文件都发）
metadata:
  node_type: memory
  type: project
  originSessionId: a789a4c4-e89a-4f5d-9217-6bc78cf4dbee
  modified: 2026-10-19T13:47:10.879Z
---

用户 10-19：全市 250 m（[[adm-city-250m-l2]]）算完后要把结果发客户，**结果文件里不能有中文**（路径+内容），.sww/.npz/.inp 等全部都发。旧的 export_results_en / rename_en 只改路径名不改内容，不够用。
已写 `upload/adm_250m_export/`(+zip)：adm250_export_en.py（只读拷出 → 路径英文 → csv/xlsx/json/md/inp/rpt/tif 内容英文 → 扫描无中文 → README + MANIFEST sha256）、adm_250m_export_en.sh（在 anuga-swmm:1.0 容器里跑，NAS :ro）。三台共用 NAS，任一台跑一次收齐 9 场；9 场算完且 adm_250m_server.sh verify 通过后再做。

**Why:** 客户要英文交付；服务器正在跑，不动 adm250_post.py，另用导出脚本事后转换更安全。
**How to apply:** 踩过的坑——openpyxl 写的 xlsx 字符是数字字符引用（&#31215;），扫中文要先 html.unescape；openpyxl 默认 theme1.xml 带宋体等中文字体名要换；.sww 按字节扫会误报，要用 netCDF 属性；映射表没覆盖的中文会在结尾列出并返回非 0，发给我补。本机只用合成成果测过，没在服务器容器里跑过。
