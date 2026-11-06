# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

雨水调蓄计算器 / 城市洪涝应对指挥中心（阿布扎比）。把 `docs/Stormwater model v2.xlsx` 的筛查模型做成网页，
对积水点逐点判「三道防线」。项目的注释、文档、导出、回复**一律用中文**。
完整背景与决策记录在 `README.md` 与 `docs/STORMWATER_CALCULATOR_CONTEXT.md`，改口径前先读相关小节。

## 常用命令

后端（`backend/`，FastAPI，Python venv 在 `backend/.venv`）：

```bash
cd backend && .venv/bin/python -m pytest                                   # 全部测试（pytest.ini 已设 pythonpath=src）
cd backend && .venv/bin/python -m pytest tests/test_rational.py::TestDefenceHydrodynamic -q   # 单个测试
cd backend && ./start.sh            # 后台起 API 在 8101；-f 前台带 --reload；stop / status
cd backend && PYTHONPATH=src .venv/bin/python -m stormwater.<模块>          # 跑批处理脚本，如 qcap_batch / report
```

前端（`frontend/`，Vue 3 + Vite + Pinia + Leaflet）：`npm run dev`（5173，`/api` 经 Vite 代理到 8101）、`npm run build`。
前端没有测试、也没有 lint 配置。`.claude/launch.json` 已配好 `frontend` 与 `api` 两个 preview 服务器。

- API 文档：http://127.0.0.1:8101/docs ；页面：`/` 指挥中心，`/calculator` 单点快算。
- 数据库（`syq-pipe`，内网 PostGIS）只有批处理脚本用；凭据在 `backend/.env`（见 `.env.example`）。
- 后端镜像：`backend/docker-build.sh`（linux/amd64，DEM 1.1G 会进镜像）。

## 架构要点

**离线批算 → CSV → 在线计算**。API 运行期不连数据库：一组 `*_batch.py` 脚本从管网库 / 演算成果
反算逐点输入，写进 `backend/data/processed/`（不进代码库），`registry.py` 离线读取并装配成
`HotspotRecord`。上游台账由 `hotspots.py` 生成 `hotspots_845.csv`；**台账换源后必须重跑全部逐点批算**
（`hotspots --load-db` 后依次 qcap / catchment / storage / coverage / district / ponding），模型片区一路用
`model_cluster_batch.py` 一次跑完。缺文件时部分测试会 skip 而非失败。

**计算链**：`idf.py`（ADQCC Zone A/B 公式 + 迪拜查表，方法不同，不只是数字不同）→ `rational.py`
（推理公式法 6 步 + 三道防线 `evaluate()`）→ `scenario.py` 的 `run_batch()` 批算整份台账并出总账。
`/api/scenario`（全域）、`/api/point-curve`（单点）、`/api/storm-preset`（算前默认雨）都走同一条链，
数字必须逐字段相等（`test_point_curve.py`、`test_storm_preset.py` 锁死）。

**两套点集，同一条链**：845 个历史上报台账点（ADM 506 + AAM 184 + DRM 155）与 107 个 ANUGA 模型片区
（uid 前缀 `MDL-`），靠 `source` 参数分流，不叠加、不合并。默认只算有管网数据的 501 点
（`coverage_only`，判据是 `qcap_basis != 'no_coverage'`，**不是行政区**；覆盖面 GeoJSON 只用于落图）。

**三道防线口径（最容易改错的地方）**：
- 三道共用时限 T（`Assumptions.drawdown_hours`，默认 2 h）。
- 第一道只读二维演算的积水量时程（`ponding_curve.py` 是积水量的唯一来源，证据链在其模块头）：
  R1 = max(0, V全程 − R × T)，R 取演算退水期平均速率。**Qcap 不参与任何一道防线**，只作峰值诊断
  （`test_qcap_no_longer_moves_any_defence_line` 是总闸）。旧的 `defence_basis="window"`（Qcap×T）已删除，不要恢复。
- 没有演算覆盖（或重现期不一致）的点不出结论：`line=None`、`l1_basis="uncalibrated"`，单列
  `line_counts["uncalibrated"]`，不外推、不回退旧公式。`/calculator` 与原 Excel 回归恒用 `defence_basis="peak"`。
- 第三道是「泵 → 罐车」串联链，能力取 min(泵侧, 罐车侧)；罐车**车次 ≠ 辆数**，辆数要靠往返周转折算。
- 迪拜数据集只为复现原 Excel 做回归（`test_rational.py` 的 H1 基准），产出不可对外报送。

**中英双语**：翻译只发生在 API 边界（`i18n.py`，`?lang=` / `Accept-Language`）。`i18n.EN` 按中文原串精确匹配 ——
改了 `idf.py` / `pumps.py` / `registry.py` 等处的中文文案，必须同步改键，否则 `test_i18n.py` 会红。
**协议值不翻**：facets 的 `value`（如 `未填写`）、数据集 `key`、泵档 `key`、各 `*_basis`。
前端文案在 `frontend/src/i18n/`，`zh.js` 为准、`en.js` 逐键对照；切语言要重取后端文案并用同一套输入重算。

**前端**：`src/stores/scenario.js` 是全局状态（台账、筛选、模型输入、两种模式的结果）；`src/api/client.js`
收口 API base 与语言参数。改输入、换点、换 IDF 后已算结果一律作废，不得拿别的重现期数字顶替。

## 注意事项

- 数据库几何统一为 EPSG:32640、列名 `geom`；空间判断回 `sw_edge`，落图用的 GeoJSON 是简化过的。
- `docs/SCENARIO_RESULTS.md` 与 `scenario_points_zh.csv` 由 `report.py` 生成，勿手改；对外主口径在
  `report.py` 的 `MAIN_RETURN_PERIOD` / `MAIN_DATASET`。
- DEM（`backend/data/DEM_UAE_UTM40/`）缺失时 `registry.py` 会**静默**全体退回 Zone B。
- ADM 台账坐标取自 `backend/data/FW__Hot_Spots_506/` 的 KMZ，不取 Excel（Excel 坐标列整体错位一行）。
- 点数是 845 不是 846（ADM 末行 `Outfalls` 是汇总行）；845 / 501 / 107 三个口径不要混。
- `psql` / `pg_dump` 不在 PATH 上（Homebrew keg-only），要写全路径。
