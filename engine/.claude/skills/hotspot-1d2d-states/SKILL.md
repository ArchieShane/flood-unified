---
name: hotspot-1d2d-states
description: 对一个热点群（如 C1）做 L1 / L2 / L2+planned / L2+suggested 四个态的一二维耦合水动力计算（SWMM 5.2 + ANUGA，docker anuga-swmm），从定计算域、参数对齐、建模、并行计算、后处理质检到系统 / 报告 / 交付目录。用户说「对 XX 热点群跑 L1 L2 L2+」「按某个汇水区重算」「设计暴雨下能不能不溢流」「做一二维」时用。仓库内通用，以 C1 为示例。
---

# 热点群一二维多态计算（L1 / L2 / L2+）

**一句话**：同一个计算域、同一场雨，只差设施，算四个态，比较积水减了多少、代价在哪。
C1 示例：8 个热点（ADM-21 / 90 / 94 / 104 / 192 / 202 / 486 / 487），计算域 = 客户 3 个水力模型（RB3 + Musaffah_RD09 + B_C）的子汇水区并集 38.1 km²。

## 铁律（先读）
1. **参数先对齐，再开跑**。20 项参数（见 `references/parameters.md`）逐项和用户确认，记到参数对齐页；用户授权自动推进后自己定的每一项都要**当场告诉用户**并在记录里标「Claude 自定」。未确认不跑，用户没回答不算同意。
2. **只跑用户批准的**。换计算域、换雨、加态、重算，都要用户明说；「暂停」用 `docker pause`（保留进度），不要 `docker stop`。
3. **汇水区以客户水力模型的子汇水区为准**（客户 22 个模型 zip），不要用手描的面（flood 库 catchment.catchment、截图描绘都是手描）。找数据时先把客户给过的所有包（含 zip）按**不同字段结构**都翻一遍，再考虑让用户想办法。
4. **结论要有核算**：凡是「A 比 B 小是因为…」这种话，先用数据算出来再写（例：C1 原计算域 25.5 ha 里 21.3 ha 在客户单元以外）。
5. **不提交 git、不写客户数据库、不改防火墙**，除非用户明说。

## 流程
**换机器先快速验证**：`python tools/c1_pipeline.py selftest`（合成算例约 2 分钟，不要客户数据，14 项自动判定）。
**换机器从头复现 C1**：`python tools/c1_pipeline.py check` → `run all --tag M61`（步骤、依赖、耗时见 README「一二维热点群计算（C1）」）。新热点群照 `c1_pipeline.py` 的步骤表改。

| 步 | 做什么 | 工具（`tools/`） | 产物 | 验收 |
|---|---|---|---|---|
| 0 | 环境 | docker 镜像 `anuga-swmm:1.0`；工作目录 `$SWBRAIN_SWMM_WORK`（**不能有中文**，C1 用 `D:\swbrain_work\swmm`） | — | `references/environment.md` |
| 1 | 定计算域 | `model_units_build.py`（客户模型子汇水区 → 汇水区单元 + 计算域）；备选 `hotspot_catchments.py`（地表 D8 + 管网汇水区外扩） | `out/units_from_models/*.geojson` | 所有目标热点在域内；无空洞；和客户汇水区的关系说清楚 |
| 2 | 参数对齐 | 参数对齐页（artifact db `answers`）| `out/c1_params_answers.json` 快照 | 20 项都 agreed |
| 3 | 建 L1 | `build_c1.py --rain R --tag T [--dur 24] --domain <计算域>` | `$WORK/C1_T_L1.inp`、`c2d/pilot_C1_T_L1.json`、`c2d/dem_C1_T.tif` | 管网整块取、现状泵站落位、`_chk_inp.py` 能读 |
| 4 | 建 L2 | `build_c1_l2.py --rain R --tag T` | `C1_T_L2.inp` | 移动泵吸水井距 < 50 m |
| 5 | 建 L2+planned | `build_c1_l2p.py --rain R --tag T --l2tag T`；域外塘面标 `no_surface` | `C1_T_L2P.inp` | 泵站 / 塘 / 压力管落位清单 |
| 6 | 算 L1 / L2 / L2+planned | `couple2way_v2.py`（docker，三态并行） | `c2d/twoway_*.json`、`*_2d.npz`、`*_2way.rpt` | `references/run-and-monitor.md` |
| 7 | 设计并算 L2+suggested v3 | `c1_design_l2s3.py --rain R --tag T --base L2P` → 同上开跑（链条自动接） | `C1_T_L2S3.inp` | 措施清单 + 塘分配 |
| 8 | 后处理 | `c1_post.py --tag T`、`c1_l2s_side_effects.py`、`npz_to_tif.py`、`npz_to_sww.py`（docker）、`c1_hs_series.py` | `out/c1_stats_T.*`、`console/data/flood2d_c1_t/` | `references/post-and-qc.md` 全部过 |
| 8b | 二维模拟积水点 | `ponding_points.py --name <运行>`（逻辑在 `swbrain/ponding.py`；c1_pipeline 的 post 步骤自动带）；系统里 `/api/ponding`、动作 `hydro_ponding`、RB-01 页「二维模拟积水点」可调阈值重算 | `out/ponding/<运行>_d<水深>.csv / .geojson`、`out/ponding/cache/` | 编号 SIM-态-序号，**不能和客户热点 ADM-xxx 混**；阈值（缺省 0.15 m / 100 m² / 0.5 h）未与客户对齐前标「试算」 |
| 8c | 飞渡 DTS | `swbrain/dts_h2d.py`（原 hydro2d_convert.py）：交付导出（export_delivery_rb01 / c1，C1 的 `--no-big` 时不出）与自定义运行 post（`tools/dts_export.py`）自动写 `<态>/飞渡DTS/` | `shp/`（三角网，ID + Elev）、`dat/RESULTSnnn.dat`（28 字节记录 ID,h,u,v）、`h2d_index.json`、顶层 `飞渡DTS说明.md` | 不出 dat 同名 CSV（用户 2026-10-18 定）；每态约 410 MB、20 s（RB-01 规模）；没有 `.sww` 的态跳过 |
| 9 | 系统 / 报告 / 交付 | 控制台 C1 页签、`console_shot.py`、`report_c1.py`、`export_delivery_c1.py --tag T`（自动带 `c1_facility_gis.py`） | 报告 artifact、`out/交付/…` | `references/deliverables.md` |

步骤 6–8 用 `tools/chains/*.sh` 串成后台链（例：`c1m_l2s3.sh` 等 L2P 算完 → 设计 v3 → 开跑；`c1m_post.sh` 等 v3 算完 → 全套后处理与交付）。**不要编辑正在运行的链脚本**（bash 边读边跑），要改就停掉、另起一份。

## 情景名（tag）约定
`R61` / `R96` = 原计算域 2 h 雨；`U61` = 客户单元版；`M61` = 模型版；`D24` = 设计暴雨 61.34 mm / 24 h（SCS II）。新的计算域或雨，起一个新前缀，**不覆盖已有结果**；各工具的 tag 分支要同步补（c1_post / c1_facility_gis / c1_hs_series / export_delivery_c1 / 控制台 `C1V.r`）。

## 各态是什么
见 `references/states.md`。要点：L1 = 现状；L2 = L1 + 雨前布设的移动泵；L2+planned = 顾问（Parsons）方案；L2+suggested = 按 L2+planned 残余积水设计的补充措施，**用 v3 规则**（按塘剩余库容分配、塘满停泵、地面取水泵直接打进塘的一维节点）。v1 土堤、v2 无库容约束都已弃用，原因写在那里。

## 常见坑
见 `references/pitfalls.md`（中文路径、Git Bash 改写路径、heredoc 转义、PROJ 冲突、一次性令牌、Shapefile 字段名 10 字节……）。遇到怪现象先翻它。
