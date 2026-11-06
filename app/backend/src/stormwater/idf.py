"""IDF 降雨强度数据集（交接文档 §3.3 / §3.4 + ADQCC WA-726-1 §3.2.4）。

本模块提供**可切换的多套 IDF 数据集**。切换的不只是数字，还有方法本身：

===========  ==========================  ==========================
             迪拜 DM                     阿布扎比 ADQCC
===========  ==========================  ==========================
历时取值     向下对齐到 15 个档位        连续公式，不取档
强度来源     查表                        i = α / (t·M)^β
tc 乘数 M    无此概念                    Table 3-3，按汇水区优先级
Cf 频率调整  有（Table 0-6，TxDOT）      **无**，C 直接用 Table 3-8/3-9
===========  ==========================  ==========================

因此每套数据集自带 `intensity()` 与 `runoff_adjustment_factor()`，
调用方只管选数据集，不需要知道背后是查表还是求值。

⚠ 846 个积水点在阿布扎比，法定标准是 ADQCC。迪拜表保留的唯一理由是
  **复现原 Excel 模型、做回归对照**，不可用于对外报送。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from bisect import bisect_right
from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# 公共出参
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdfLookup:
    """一次强度求值的结果。"""

    intensity_mm_hr: float
    #: 实际采用的降雨历时（分钟）。会进入积水体积计算，故必须随强度一起返回：
    #: 迪拜取档后是档位值，ADQCC 是 tc × M，两者含义不同。
    duration_min: float


class CatchmentPriority(int, Enum):
    """ADQCC Table 3-3 汇水区优先级 → 设计历时乘数 M。"""

    MAJOR_ROADS = 1        #: 主干道/快速路/下穿/机场/港口/涵洞/地下车库坡道
    HIGH_DENSITY = 2       #: 高密度区（不透水率 ≥ 50%）
    LOW_DENSITY = 3        #: 低密度区（≤ 50%）
    INDUSTRIAL = 4         #: 工业区（若不透水率 > 50% 则按低密度处理）
    OPEN_AREA = 5          #: 开阔地、公园、不常用地

    @property
    def tc_multiplier(self) -> float:
        return _TC_MULTIPLIER[self]

    @property
    def label(self) -> str:
        return _PRIORITY_LABEL[self]


_TC_MULTIPLIER: dict[CatchmentPriority, float] = {
    CatchmentPriority.MAJOR_ROADS: 1.0,
    CatchmentPriority.HIGH_DENSITY: 2.0,
    CatchmentPriority.LOW_DENSITY: 3.0,
    CatchmentPriority.INDUSTRIAL: 5.0,
    CatchmentPriority.OPEN_AREA: 12.0,
}

_PRIORITY_LABEL: dict[CatchmentPriority, str] = {
    CatchmentPriority.MAJOR_ROADS: "主干道 / 下穿 / 机场 / 涵洞（1×）",
    CatchmentPriority.HIGH_DENSITY: "高密度区 ≥50% 不透水（2×）",
    CatchmentPriority.LOW_DENSITY: "低密度区 ≤50% 不透水（3×）",
    CatchmentPriority.INDUSTRIAL: "工业区（5×）",
    CatchmentPriority.OPEN_AREA: "开阔地 / 公园（12×）",
}


# ---------------------------------------------------------------------------
# 数据集基类
# ---------------------------------------------------------------------------


class IdfDataset(ABC):
    """一套 IDF 数据 + 它自己的取值方法。"""

    key: str
    label: str
    source: str
    #: 该数据集可用的重现期（年）
    return_periods: tuple[int, ...]
    #: 是否支持 Table 3-3 的 tc 乘数
    supports_tc_multiplier: bool
    #: 是否对 C 做频率调整（Cf）
    supports_cf: bool
    #: UI 上需要原样展示的口径说明
    notes: tuple[str, ...] = ()

    @abstractmethod
    def intensity(self, return_period_yr: int, tc_min: float, tc_multiplier: float = 1.0) -> IdfLookup:
        """给定重现期与集流时间，返回降雨强度和实际采用的历时。"""

    def runoff_adjustment_factor(self, return_period_yr: int) -> float:
        """径流系数的频率调整系数 Cf。不支持该概念的数据集恒返回 1.0。"""
        return 1.0

    def _check_return_period(self, return_period_yr: int) -> None:
        if return_period_yr not in self.return_periods:
            raise ValueError(
                f"{self.label} 无 {return_period_yr} 年重现期，可选：{list(self.return_periods)}"
            )

    def _check_multiplier(self, tc_multiplier: float) -> None:
        if tc_multiplier <= 0:
            raise ValueError(f"tc 乘数必须为正，得到 {tc_multiplier}")
        if not self.supports_tc_multiplier and tc_multiplier != 1.0:
            raise ValueError(
                f"{self.label} 没有设计历时乘数这一概念（那是 ADQCC Table 3-3 的规定），"
                f"不能传 M={tc_multiplier}。要用乘数请切换到 ADQCC 数据集。"
            )

    def describe(self) -> dict:
        """供前端渲染数据集选择器。"""
        return {
            "key": self.key,
            "label": self.label,
            "source": self.source,
            "return_periods": list(self.return_periods),
            "supports_tc_multiplier": self.supports_tc_multiplier,
            "supports_cf": self.supports_cf,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# 迪拜 DM —— 查表 + 向下取档
# ---------------------------------------------------------------------------

#: 迪拜 IDF 表的历时档位（分钟）
DUBAI_DURATIONS_MIN: tuple[int, ...] = (
    5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 360, 720, 1440,
)

#: 降雨强度表 {重现期(年): (各历时档位对应的 mm/hr, ...)}
IDF_DUBAI: dict[int, tuple[float, ...]] = {
    2:   (59.90, 45.22, 37.15, 31.10, 23.57, 17.44, 13.87, 10.22, 8.18, 5.92, 4.73, 3.96, 3.38, 1.93, 1.00),
    5:   (97.41, 76.98, 63.34, 53.54, 41.68, 31.78, 25.35, 18.66, 15.11, 10.81, 8.62, 7.21, 6.22, 3.68, 1.93),
    10:  (118.00, 97.61, 80.41, 68.58, 54.72, 42.82, 34.22, 25.18, 20.57, 14.60, 11.61, 9.73, 8.44, 5.13, 2.71),
    20:  (131.23, 115.25, 95.09, 82.01, 67.36, 54.26, 43.46, 31.95, 26.37, 18.54, 14.70, 12.34, 10.79, 6.75, 3.59),
    25:  (133.66, 120.18, 99.22, 85.92, 71.32, 58.03, 46.52, 34.20, 28.31, 19.84, 15.72, 13.20, 11.58, 7.31, 3.90),
    50:  (169.22, 152.15, 125.61, 108.78, 90.30, 73.47, 58.89, 43.29, 35.85, 25.12, 19.91, 16.71, 14.65, 9.25, 4.94),
    100: (209.20, 188.11, 155.29, 134.49, 111.63, 90.83, 72.81, 53.52, 44.32, 31.06, 24.61, 20.66, 18.12, 11.44, 6.10),
}

#: 径流系数调整系数 Cf（Table 0-6，引自 TxDOT 2011）。
#: ⚠ IDF 表有 2 年和 20 年两行，但 Cf 表没有对应值 —— 原 Excel 用 IFERROR 回退 1.0。
CF_BY_RETURN_PERIOD: dict[int, float] = {5: 1.00, 10: 1.00, 25: 1.10, 50: 1.20, 100: 1.25}


def snap_duration(tc_min: float) -> int:
    """把集流时间 tc 向下对齐到迪拜 IDF 的历时档位。

    复现 Excel 的 ``INDEX(durations, MATCH(tc, durations, 1))``：
    MATCH type=1 取「小于等于 tc 的最大档位」。

    ⚠ 已知缺陷（交接文档 §3.2）：向下取档使强度偏高（峰值流量偏保守），
      但历时偏短（积水体积偏小，调蓄容量**偏不保守**）。
      ADQCC 数据集用连续公式，不存在这个问题。

    Raises:
        ValueError: tc 小于最小档位 5 分钟时，对应 Excel 的 #N/A。
    """
    if tc_min < DUBAI_DURATIONS_MIN[0]:
        raise ValueError(
            f"tc={tc_min} min 小于 IDF 最小历时档位 {DUBAI_DURATIONS_MIN[0]} min，"
            f"原 Excel 在此处返回 #N/A"
        )
    return DUBAI_DURATIONS_MIN[bisect_right(DUBAI_DURATIONS_MIN, tc_min) - 1]


class DubaiTabulatedIdf(IdfDataset):
    """迪拜市政局 IDF 表，向下取档查表。"""

    key = "dubai_dm"
    label = "迪拜 DM 导则（原 Excel 所用的表）"
    source = (
        "Dubai Municipality, Stormwater Design Guidelines, "
        "Doc. DM-WSA-SRPD-SW1, Version 2, July 2024, Table 0-2 / Table 0-6"
    )
    return_periods = (2, 5, 10, 20, 25, 50, 100)
    supports_tc_multiplier = False
    supports_cf = True
    notes = (
        "⚠ 这是迪拜标准。846 个积水点位于阿布扎比，法定标准是 ADQCC WA-726-1，"
        "本数据集仅用于复现原 Excel 模型做对照，产出不可直接对外报送。",
        "数据来自 Stormwater model v2.xlsx 的 IDF_Dubai 工作表（105 格逐格核对一致），"
        "但真正的出处是上面那份 DM 导则——原文我们没有，无法核算法与适用范围。",
        "历时向下取档：强度偏高（流量偏保守）但历时偏短（调蓄容量偏不保守）。",
        "哈达（Hatta）地区应改用同文件 Table 0-3，本表未收录。",
    )

    def intensity(self, return_period_yr: int, tc_min: float, tc_multiplier: float = 1.0) -> IdfLookup:
        self._check_return_period(return_period_yr)
        self._check_multiplier(tc_multiplier)
        duration = snap_duration(tc_min)
        idx = DUBAI_DURATIONS_MIN.index(duration)
        return IdfLookup(IDF_DUBAI[return_period_yr][idx], float(duration))

    def runoff_adjustment_factor(self, return_period_yr: int) -> float:
        """取 Cf。表中没有的重现期回退 1.0（复现原 Excel 的 IFERROR 行为）。"""
        return CF_BY_RETURN_PERIOD.get(return_period_yr, 1.0)


# ---------------------------------------------------------------------------
# 阿布扎比 ADQCC WA-726-1 —— 连续公式 i = α / (t·M)^β
# ---------------------------------------------------------------------------

#: 标准 Table 3-4 / 3-6 的历时列（分钟），仅用于展示与回归校核
ADQCC_TABLE_DURATIONS_MIN: tuple[int, ...] = (5, 10, 15, 30, 60, 120, 180, 360, 720, 1440)

#: Zone A 已公布的 DDF 降雨强度表（Table 3-4），用于校核公式实现
ADQCC_ZONE_A_TABLE: dict[int, tuple[float, ...]] = {
    100: (430.34, 251.91, 184.16, 107.80, 63.10, 36.94, 27.00, 15.81, 9.25, 5.42),
    50:  (367.24, 214.96, 157.15, 91.99, 53.84, 31.52, 23.04, 13.49, 7.89, 4.62),
    25:  (215.10, 132.37, 99.65, 61.32, 37.74, 23.23, 17.48, 10.76, 6.62, 4.07),
    10:  (135.38, 85.85, 65.76, 41.70, 26.44, 16.77, 12.85, 8.15, 5.16, 3.27),
    5:   (120.05, 76.01, 58.18, 36.84, 23.32, 14.77, 11.30, 7.16, 4.53, 2.87),
    2:   (74.44, 44.38, 32.79, 19.55, 11.65, 6.95, 5.13, 3.06, 1.82, 1.09),
}

#: Zone B 已公布的 DDF 降雨强度表（Table 3-6）
ADQCC_ZONE_B_TABLE: dict[int, tuple[float, ...]] = {
    100: (325.17, 189.81, 138.54, 80.87, 47.21, 27.56, 20.11, 11.74, 6.85, 4.00),
    50:  (277.48, 161.98, 118.22, 69.01, 40.28, 23.51, 17.16, 10.02, 5.85, 3.41),
    25:  (189.05, 113.38, 84.07, 50.42, 30.24, 18.13, 13.45, 8.06, 4.84, 2.90),
    10:  (127.64, 77.34, 57.70, 34.96, 21.18, 12.84, 9.57, 5.80, 3.52, 2.13),
    5:   (113.18, 68.48, 51.04, 30.88, 18.68, 11.30, 8.43, 5.10, 3.08, 1.87),
    2:   (48.39, 29.54, 22.13, 13.51, 8.25, 5.04, 3.77, 2.30, 1.41, 0.86),
}

#: Zone B 已公布的 DDF 累计雨量表（Table 3-6，mm），与上面的强度表同一张表的另一半。
#: 强度 × 历时 ÷ 60 与本表在 5 / 10 min 两档差 0.2%–0.44%（强度表舍入所致），其余逐格相等；
#: 派生雨型（`hyetograph.py`）按本表的雨量走，与 docs/Zone_B官方DDF与雨型图_20260926 的交付包一致。
ADQCC_ZONE_B_DDF_DEPTH_MM: dict[int, tuple[float, ...]] = {
    100: (26.99, 31.70, 34.64, 40.44, 47.21, 55.12, 60.33, 70.44, 82.20, 96.00),
    50:  (23.03, 27.05, 29.56, 34.51, 40.28, 47.02, 51.48, 60.12, 70.20, 81.84),
    25:  (15.69, 18.93, 21.02, 25.21, 30.24, 36.26, 40.35, 48.36, 58.08, 69.60),
    10:  (10.59, 12.92, 14.43, 17.48, 21.18, 25.68, 28.71, 34.80, 42.24, 51.12),
    5:   (9.39, 11.44, 12.76, 15.44, 18.68, 22.60, 25.29, 30.60, 36.96, 44.88),
    2:   (4.02, 4.93, 5.53, 6.76, 8.25, 10.08, 11.31, 13.80, 16.92, 20.64),
}

#: Zone B 24 小时降雨深度（Table 3-6 下半部分）——台账引用的就是这一列
ADQCC_ZONE_B_DEPTH_24H_MM: dict[int, float] = {
    100: 96.00, 50: 81.84, 25: 69.60, 10: 51.12, 5: 44.88, 2: 20.64,
}

#: Zone A 24 小时降雨深度（Table 3-4 下半部分）
ADQCC_ZONE_A_DEPTH_24H_MM: dict[int, float] = {
    100: 130.08, 50: 110.88, 25: 97.68, 10: 78.48, 5: 68.88, 2: 26.16,
}


class AdqccEquationIdf(IdfDataset):
    """ADQCC WA-726-1 的 IDF 公式：i = α / (t·M)^β，t 为小时。

    ⚠ 标准里公式表与 DDF 表的重现期集合不一致：Zone B 的公式表多一档 15 年，
      而 DDF 表没有。这里以**公式表**为准（`return_periods`），
      DDF 表只用于回归校核。
    """

    supports_tc_multiplier = True
    supports_cf = False

    def __init__(
        self,
        key: str,
        label: str,
        coefficients: dict[int, tuple[float, float]],
        published_table: dict[int, tuple[float, ...]],
        depth_24h_mm: dict[int, float],
        notes: tuple[str, ...],
    ) -> None:
        self.key = key
        self.label = label
        self.source = (
            "ADQCC / DMT, Stormwater & Subsoil Drainage Systems Volume I: Design Manual, "
            "WA-726-1, Third Edition, April 2022"
        )
        self.coefficients = coefficients
        self.published_table = published_table
        self.depth_24h_mm = depth_24h_mm
        self.return_periods = tuple(sorted(coefficients))
        self.notes = notes

    def intensity(self, return_period_yr: int, tc_min: float, tc_multiplier: float = 1.0) -> IdfLookup:
        self._check_return_period(return_period_yr)
        self._check_multiplier(tc_multiplier)
        if tc_min <= 0:
            raise ValueError(f"集流时间必须为正，得到 {tc_min}")
        alpha, beta = self.coefficients[return_period_yr]
        duration_min = tc_min * tc_multiplier
        duration_hr = duration_min / 60.0
        return IdfLookup(alpha / duration_hr**beta, duration_min)


#: Table 3-5 —— Zone A 的 α / β
ADQCC_ZONE_A_COEFFICIENTS: dict[int, tuple[float, float]] = {
    100: (63.096, 0.773),
    50:  (53.843, 0.773),
    25:  (37.739, 0.700),
    10:  (26.443, 0.657),
    5:   (23.325, 0.659),
    2:   (11.653, 0.746),
}

#: Table 3-7 —— Zone B 的 α / β。注意比 Zone A 多一档 15 年。
ADQCC_ZONE_B_COEFFICIENTS: dict[int, tuple[float, float]] = {
    100: (47.206, 0.777),
    50:  (40.284, 0.777),
    25:  (30.237, 0.738),
    15:  (22.327, 0.695),
    10:  (21.183, 0.723),
    5:   (18.685, 0.725),
    2:   (8.248, 0.712),
}

_ZONE_SPLIT_NOTE = (
    "Zone A / Zone B 的分界线约为海拔 200 m 等高线（标准 §3.2.4，精确边界见 Figure 3-2）。"
    "Zone A 为东北部山区，Zone B 为其余平原区。"
)

ADQCC_ZONE_A = AdqccEquationIdf(
    key="adqcc_zone_a",
    label="ADQCC Zone A（东北山区）",
    coefficients=ADQCC_ZONE_A_COEFFICIENTS,
    published_table=ADQCC_ZONE_A_TABLE,
    depth_24h_mm=ADQCC_ZONE_A_DEPTH_24H_MM,
    notes=(
        _ZONE_SPLIT_NOTE,
        "按 DEM 采样，846 个积水点中约 149 个落在 Zone A（其中艾因 AAM 148 个）。",
        "ADQCC 不设 Cf 频率调整；C 直接取 Table 3-8（城市）/ Table 3-9（乡村）。",
    ),
)

ADQCC_ZONE_B = AdqccEquationIdf(
    key="adqcc_zone_b",
    label="ADQCC Zone B（平原区，含阿布扎比市区）",
    coefficients=ADQCC_ZONE_B_COEFFICIENTS,
    published_table=ADQCC_ZONE_B_TABLE,
    depth_24h_mm=ADQCC_ZONE_B_DEPTH_24H_MM,
    notes=(
        _ZONE_SPLIT_NOTE,
        "846 个积水点中约 676 个落在 Zone B（ADM 全部 502 个）。",
        "台账 Hotspot Network Rain Design Event 引用的 44.88 / 51.12 / 69.60 / 81.84 mm"
        " 就是本区 DDF 表的 24 小时列。",
        "ADQCC 不设 Cf 频率调整；C 直接取 Table 3-8（城市）/ Table 3-9（乡村）。",
    ),
)

DUBAI_DM = DubaiTabulatedIdf()


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

#: 前端切换按钮的数据来源。顺序即 UI 展示顺序。
IDF_DATASETS: dict[str, IdfDataset] = {
    ds.key: ds for ds in (ADQCC_ZONE_B, ADQCC_ZONE_A, DUBAI_DM)
}

#: 默认数据集 —— 阿布扎比平原区，覆盖 846 个点里的绝大多数。
DEFAULT_DATASET_KEY = ADQCC_ZONE_B.key


def get_dataset(key: str | None = None) -> IdfDataset:
    """按 key 取数据集。传 None 取默认。"""
    if key is None:
        return IDF_DATASETS[DEFAULT_DATASET_KEY]
    try:
        return IDF_DATASETS[key]
    except KeyError:
        raise ValueError(
            f"未知 IDF 数据集 {key!r}，可选：{list(IDF_DATASETS)}"
        ) from None


#: ADQCC Table 3-2 —— 按设施类型选设计重现期。供 UI 预选默认档位。
DESIGN_RETURN_PERIOD_BY_FACILITY: tuple[tuple[int, str], ...] = (
    (5, "所有由雨水管网服务的区域（默认）"),
    (10, "主干道、宫殿、渗井及其他关键区域"),
    (25, "次干道涵洞、无出口的泻湖与渗井、机场、港口（另需校核 50 年）"),
    (50, "下穿道与地下车库坡道、主干道涵洞（另需校核 100 年）"),
)

#: Fleet_Summary 汇总用的重现期。两套数据集都有这五档。
FLEET_RETURN_PERIODS: tuple[int, ...] = (5, 10, 25, 50, 100)


# 向后兼容：原模块级函数保留，绑定到迪拜数据集。
def rainfall_intensity(return_period_yr: int, duration_min: int) -> float:
    """查迪拜 IDF 表取降雨强度（mm/hr）。duration_min 必须是精确的档位值。

    ⚠ 已废弃：新代码请用 ``get_dataset(key).intensity(rp, tc)``。
    """
    if return_period_yr not in IDF_DUBAI:
        raise ValueError(
            f"IDF 表无 {return_period_yr} 年重现期，可选：{sorted(IDF_DUBAI)}"
        )
    try:
        idx = DUBAI_DURATIONS_MIN.index(duration_min)
    except ValueError:
        raise ValueError(
            f"{duration_min} min 不是 IDF 历时档位，请先调用 snap_duration()"
        ) from None
    return IDF_DUBAI[return_period_yr][idx]


def runoff_adjustment_factor(return_period_yr: int) -> float:
    """取迪拜 Cf。表中没有的重现期回退 1.0。

    ⚠ 已废弃：新代码请用 ``get_dataset(key).runoff_adjustment_factor(rp)``。
    """
    return CF_BY_RETURN_PERIOD.get(return_period_yr, 1.0)


#: 保留旧名，避免外部引用断掉
DURATIONS_MIN = DUBAI_DURATIONS_MIN
