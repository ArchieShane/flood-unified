"""二维水动力模型识别出的**易积水片区**（与台账上报的积水点是两回事）。

项目里现在有两套「积水点」，口径完全不同，页面上必须分开看：

| | 台账上报点（`registry.py`） | 模型识别片区（本模块） |
|---|---|---|
| 来源 | ADM/AAM/DRM 历史上报的 845 个点位 | ANUGA 二维演算成果的逐格时序反算 |
| 覆盖 | 全酋长国三个市政区 | 只有 Al Bateen 一个 run（996.6 ha） |
| 粒度 | 一个坐标点 | 20 m 格八连通聚成的片区（多边形） |
| 回答的问题 | 「历史上这里报过积水」 | 「100 年一遇下这里会积水多深、滞多久」 |
| 参与计算 | 参与三道防线批算 | **不参与** —— 没有 A/C/tc/Qcap，只作展示与对照 |

两者**不是同一批地物**，也不该合并排序：台账点是行政记录，模型片区是演算结论，
一个点报过不代表模型会识别出来（反之亦然），这个差异本身就是要给人看的东西。

## 数据从哪来

`data/analysis_ponding/ponding_hotspots.geojson` —— 由 Al Bateen RP100 那一个 run
离线分析产出（算法见同目录 `算法与成果说明.docx` 与 `易积水分析报告.md`）：
397 帧水深快照重建逐格时序 → Priority-Flood 填洼 → SWMM 注入归因 → 八连通聚类
→ 规则树判成因 + 五因子评分。本模块只负责**读文件、标 key、算汇总**，
一个数都不重算 —— 重算的前提是拿到那 204 MB 原始快照，不在本仓库里。

判据（片区入选）：最大水深 ≥ 0.30 m **或** 水深 ≥ 0.15 m 持续 ≥ 3 h。
1,378 个格聚成 107 个片区。

## 为什么成因要另给一个 key

GeoJSON 里的 `mechanism` 是中文串（离线脚本写死的）。页面要双语，而
`i18n.py` 的规矩是「在 API 边界翻、协议值不翻」——故这里给每个片区补一个
`mechanism_key`（稳定英文 key，前端拿它上色、分组），`mechanism` 本身过 `tr()`
变成展示文案。前端一个中文串都不该硬编码。

⚠ 成果自述为**诊断性**（`completed_diagnostic_not_engineering_admitted`），
不是工程审定值；5.39 m 这类极值来自 DTM 深坑，须影像/现场核实。这句话跟着
接口一起发，页面必须原样展示。
"""

from __future__ import annotations

import json
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]

#: 离线分析的产出目录。GeoJSON 是唯一被接口读的文件，其余（CSV/PNG/DOCX）是给人看的。
ANALYSIS_DIR = BACKEND_DIR / "data" / "analysis_ponding"
OUTPUT_GEOJSON = ANALYSIS_DIR / "ponding_hotspots.geojson"

#: 成因中文串 → 稳定 key。**key 是协议值**（前端按它上色/分组），不随语言变。
#: 左边这几个串由离线脚本写死，改了那边这里要跟着改，否则会退化成 `other`。
MECHANISM_KEYS: dict[str, str] = {
    "地形深坑/构筑物凹陷": "deep_sink",
    "地表汇水洼地": "surface_depression",
    "管网溢流主导": "network_overflow",
    "滨海低地滞水": "coastal_lowland",
}

#: 页面上的排列顺序（图例、分组统计共用）—— 按处置优先级从高到低，不是按数量。
MECHANISM_ORDER = ["deep_sink", "network_overflow", "coastal_lowland", "surface_depression", "other"]

#: 这一次 run 的自述。数字全部抄自 `易积水分析报告.md` 与 run_receipt，
#: 页面靠它说清「这批点是哪一场雨、哪个区、什么解算器算出来的」。
RUN_META = {
    "run_id": "al-bateen-library-rp100-20m-20260920T122942Z-96h-priority",
    "district": "AL BATEEN",
    "district_key": "ADM / AL BATEEN",
    "area_label": "AL BATEEN（ADM District 147 / DMT 1300）",
    "land_area_ha": 996.6,
    "return_period_yr": 100,
    "duration_min": 180,
    "rain_total_mm": 60.33,
    "rain_peak_mmph": 323.9,
    "sim_hours": 99,
    "grid_m": 20,
    "solver": "ANUGA 2D",
    "storm": "Abu Dhabi 2022 Zone B DDF（Table 3-6）",
    "coupling": "SWMM → ANUGA 单向耦合，映射节点 12,553 个，预设溢流 288,838 m³",
    "criterion": "最大水深 ≥ 0.30 m，或水深 ≥ 0.15 m 持续 ≥ 3 h",
    "analysis_date": "2026-09-21",
    "status": "completed_diagnostic_not_engineering_admitted",
}

#: 使用边界。与台账那边的 `LIMITATIONS` 一样：随结果一起发，页面原样展示。
CAVEATS = [
    "诊断性成果，非工程审定值；5.39 m 等极值来自 DTM 深坑，须影像/现场核实。",
    "单向耦合（SWMM→ANUGA），无地表回流入管网的动态反馈，深坑水深偏保守（偏大）。",
    "20 m 计算网格由 5 m DTM 聚合，单格深坑的绝对水深不可直接作设计依据。",
    "退水采用 120 min 时间尺度的概化排水项，未按实测退水过程率定。",
    "雨型峰值位置 40% 为假设值，非发布资料。",
    "只有 Al Bateen 一个 run，不覆盖其余片区 —— 这里没有点不等于那里不积水。",
]


def mechanism_key(label: str | None) -> str:
    """成因中文串 → key。认不出就归 `other`，不猜、也不吞掉那个片区。"""
    return MECHANISM_KEYS.get((label or "").strip(), "other")


def load_features() -> list[dict]:
    """读片区 GeoJSON，逐个补 `mechanism_key`。文件缺失返回空表。

    几何原样带出：107 个片区合计 1,378 个 20 m 方格（未融合），约 280 KB，
    比管网干管那 1.6 MB 小一个量级，前端一次取回即可，不必分层按需。
    """
    if not OUTPUT_GEOJSON.exists():
        return []
    fc = json.loads(OUTPUT_GEOJSON.read_text(encoding="utf-8"))
    feats = fc.get("features", []) or []
    for f in feats:
        props = f.setdefault("properties", {})
        props["mechanism_key"] = mechanism_key(props.get("mechanism"))
    return feats


def summarise(features: list[dict]) -> dict:
    """全区汇总。逐项都是对 `features` 的直接聚合，没有另一套口径。"""
    props = [f.get("properties", {}) for f in features]
    if not props:
        return {
            "count": 0, "by_mechanism": {}, "cells": 0, "area_ha": 0.0,
            "area_ge_0_30m_ha": 0.0, "max_depth_m": 0.0, "max_dur_ge_0_15m_h": 0.0,
            "never_drained": 0, "swmm_inflow_m3": 0.0,
        }
    by_mech: dict[str, int] = {}
    for p in props:
        k = p.get("mechanism_key", "other")
        by_mech[k] = by_mech.get(k, 0) + 1
    sim_h = RUN_META["sim_hours"]
    return {
        "count": len(props),
        "by_mechanism": by_mech,
        "cells": sum(p.get("cells", 0) for p in props),
        "area_ha": round(sum(p.get("area_m2", 0) for p in props) / 1e4, 2),
        "area_ge_0_30m_ha": round(sum(p.get("area_ge_0_30m_m2", 0) for p in props) / 1e4, 2),
        "max_depth_m": round(max(p.get("max_depth_m", 0) for p in props), 3),
        "max_dur_ge_0_15m_h": round(max(p.get("dur_ge_0_15m_h", 0) for p in props), 2),
        # 「99 h 仍未退干」—— 滨海那 3 个片区，全区唯一需要泵排才能收尾的一类
        "never_drained": sum(1 for p in props if (p.get("drain_time_h") or 0) >= sim_h),
        # ⚠ 这里**故意不给**洼地库容合计：`basin_capacity_m3` 是**逐洼地**的量，
        # 一个洼地里落了几个片区就会被记几遍（rank 5 与 10 同为 40,631.5 m³），
        # 相加得到的是个虚数。全区口径见报告的 991 个洼地合计，不在本接口里。
        "swmm_inflow_m3": round(sum(p.get("swmm_inflow_m3", 0) for p in props), 1),
    }
