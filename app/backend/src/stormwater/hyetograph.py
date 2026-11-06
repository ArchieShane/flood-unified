"""Zone B 派生雨型 —— **只供页面看图**，不进任何计算链（三道防线、批算、单点都不读它）。

来源：ADM-DMT《Rainfall Analysis Report (Phase B) V04》Table 3-6 - Recommended IDF and DDF
tables for use in Zone B；交付包在 docs/Zone_B官方DDF与雨型图_20260926/，其 README 划了使用边界：
DDF 累计雨量是官方数，逐步时间分配是**模型派生**，不能标成官方观测雨型。

生成方法（与交付包逐值一致，`test_hyetograph.py` 锁死）：
  1. 按总雨量 P 在历时 D 上反查等效重现期，在 6 条官方 DDF 之间按 ln(重现期) 插值出一条 DDF；
  2. 这条 DDF 缩放到 D 时刻恰等于 P，再按 ln(历时)–ln(雨量) 插值到时间步长；
  3. 逐步增量按交替块法排开：最大块落在峰值位置，次大在右、再次在左，一侧排满后全落另一侧。
超出 100 年的场景用 100 年曲线的形状放大、低于 2 年的用 2 年曲线形状缩小，结果标 `kind`，页面据此写「外推」。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from .idf import ADQCC_TABLE_DURATIONS_MIN, ADQCC_ZONE_B_DDF_DEPTH_MM

DURATIONS_MIN: tuple[int, ...] = ADQCC_TABLE_DURATIONS_MIN
RETURN_PERIODS: tuple[int, ...] = tuple(sorted(ADQCC_ZONE_B_DDF_DEPTH_MM))

#: 9 个预设 = 雨量 × 历时。页面按「行 = 雨量、列 = 历时」排成 3×3，顺序即展示顺序。
PRESET_DEPTHS_MM: tuple[float, ...] = (33.0, 61.0, 96.0)
PRESET_DURATIONS_MIN: tuple[int, ...] = (1440, 120, 60)
DEFAULT_PRESET: tuple[float, int] = (96.0, 1440)

STEP_CHOICES_MIN: tuple[int, ...] = (5, 10, 15)
DEFAULT_PEAK_PCT = 40.0
MAX_DURATION_MIN = DURATIONS_MIN[-1]

SOURCE = (
    "来源：ADM-DMT《Rainfall Analysis Report (Phase B) V04》Table 3-6 - "
    "Recommended IDF and DDF tables for use in Zone B"
)
METHOD = "官方 DDF 累计雨量 → 对数历时–对数雨量插值到时间步长 → 交替块法排列（峰值位置可调）"
BOUNDARY = (
    "总雨量对齐官方 DDF；时间分配是模型派生（对数插值 + 交替块法），"
    "不是官方逐 5 分钟观测雨型，不可按官方雨型对外标注。"
)

ERR_DEPTH = "总雨量需大于 0"
ERR_DURATION = "历时需在 1–1440 min 之间（DDF 表上限 24 h）"
ERR_STEP_CHOICE = "时间步长只能取 5 / 10 / 15 min"
ERR_STEP_MULTIPLE = "历时需为步长的整数倍，且至少 3 步"
ERR_PEAK = "峰值位置需在 0–100% 之间"


def default_step(duration_min: float) -> int:
    """3 h 以内 5 min（与交付包一致），更长用 15 min，免得一天 288 根柱挤成一片。"""
    return 15 if duration_min > 180 else 5


def _interp(x: float, xs: list[float], ys: list[float]) -> float:
    """分段线性插值，两端按端段斜率外推。xs 须升序。"""
    n = len(xs)
    i = 1
    if x > xs[0]:
        while i < n - 1 and x > xs[i]:
            i += 1
    return ys[i - 1] + (ys[i] - ys[i - 1]) * (x - xs[i - 1]) / (xs[i] - xs[i - 1])


_LN_DUR = [math.log(d) for d in DURATIONS_MIN]
_LN_RP = [math.log(r) for r in RETURN_PERIODS]


def _depth_at(t: float, col: list[float]) -> float:
    """一条 DDF（各历时档位上的雨量）在任意历时 t 的累计雨量，ln–ln 插值。"""
    return math.exp(_interp(math.log(t), _LN_DUR, [math.log(v) for v in col]))


def _column_at_ln_rp(ln_rp: float) -> list[float]:
    """按 ln(重现期) 在 6 条官方 DDF 之间插出一条。"""
    return [
        math.exp(_interp(ln_rp, _LN_RP, [math.log(ADQCC_ZONE_B_DDF_DEPTH_MM[rp][i]) for rp in RETURN_PERIODS]))
        for i in range(len(DURATIONS_MIN))
    ]


@dataclass(frozen=True)
class Equivalent:
    """场景在 Zone B DDF 上的等效重现期。

    kind：within = 落在 2–100 年之间（years 有值）；
          above = 超过 100 年（ratio = P ÷ 100 年雨量）；below = 不足 2 年（ratio = P ÷ 2 年雨量）。
    """

    kind: Literal["within", "above", "below"]
    years: float | None
    ratio: float | None
    ln_rp: float = field(repr=False)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "years": self.years, "ratio": self.ratio}


def equivalent_return_period(depth_mm: float, duration_min: float) -> Equivalent:
    v = [_depth_at(duration_min, [ADQCC_ZONE_B_DDF_DEPTH_MM[rp][i] for i in range(len(DURATIONS_MIN))])
         for rp in RETURN_PERIODS]
    # exp(ln()) 往返有 1e-15 量级误差：恰在端点上的场景（96 mm / 24 h = 100 年）不能判成「超百年 ×1.00」
    tol = 1e-9 * depth_mm
    if depth_mm < v[0] - tol:
        return Equivalent("below", None, depth_mm / v[0], _LN_RP[0])
    if depth_mm > v[-1] + tol:
        return Equivalent("above", None, depth_mm / v[-1], _LN_RP[-1])
    ln_rp = min(_LN_RP[-1], max(_LN_RP[0], _interp(math.log(depth_mm), [math.log(x) for x in v], _LN_RP)))
    return Equivalent("within", math.exp(ln_rp), None, ln_rp)


def validate(depth_mm: float, duration_min: float, step_min: int, peak_pct: float) -> None:
    """不合法抛 ValueError（中文，API 边界再翻译）。"""
    if not depth_mm > 0:
        raise ValueError(ERR_DEPTH)
    if not 0 < duration_min <= MAX_DURATION_MIN:
        raise ValueError(ERR_DURATION)
    if step_min not in STEP_CHOICES_MIN:
        raise ValueError(ERR_STEP_CHOICE)
    if duration_min % step_min != 0 or duration_min / step_min < 3:
        raise ValueError(ERR_STEP_MULTIPLE)
    if not 0 <= peak_pct <= 100:
        raise ValueError(ERR_PEAK)


@dataclass(frozen=True)
class Hyetograph:
    depth_mm: float
    duration_min: float
    step_min: int
    peak_pct: float
    t_min: list[float]  # 每步起点
    step_depth_mm: list[float]  # 每步雨深
    intensity_mm_hr: list[float]  # 该步平均雨强
    peak_index: int  # 雨深最大那一步
    peak_intensity_5min_mm_hr: float  # 峰值 5 分钟雨强，与步长无关，见 build()
    equivalent: Equivalent

    def to_dict(self) -> dict:
        return {
            "depth_mm": self.depth_mm,
            "duration_min": self.duration_min,
            "step_min": self.step_min,
            "peak_pct": self.peak_pct,
            "t_min": self.t_min,
            "step_depth_mm": self.step_depth_mm,
            "intensity_mm_hr": self.intensity_mm_hr,
            "peak_index": self.peak_index,
            "peak_intensity_5min_mm_hr": self.peak_intensity_5min_mm_hr,
            "equivalent": self.equivalent.to_dict(),
        }


def build(depth_mm: float, duration_min: float, step_min: int, peak_pct: float = DEFAULT_PEAK_PCT) -> Hyetograph:
    validate(depth_mm, duration_min, step_min, peak_pct)
    eq = equivalent_return_period(depth_mm, duration_min)
    col = _column_at_ln_rp(eq.ln_rp)
    scale = depth_mm / _depth_at(duration_min, col)
    n = round(duration_min / step_min)

    inc: list[float] = []
    prev = 0.0
    for i in range(1, n + 1):
        cum = _depth_at(step_min * i, col) * scale
        inc.append(cum - prev)
        prev = cum
    inc.sort(reverse=True)

    # 对外报的峰值雨强固定取 5 分钟口径：步长只是画图选择（24 h 用 15 min 是怕柱子挤成一片），
    # 不能因此把 324 mm/h 报成 139 mm/h；5 min 又是官方 DDF 最细的一档，再细就没有锚点。
    # 交替块法下最强 5 分钟 = 缩放后 DDF 在 5 min 处的雨量，步长为 5 时即最大那一块。
    peak_5min = _depth_at(DURATIONS_MIN[0], col) * scale * 60 / DURATIONS_MIN[0]

    out = [0.0] * n
    pk = min(n - 1, math.floor(peak_pct / 100 * n))
    out[pk] = inc[0]
    left, right = pk - 1, pk + 1
    for k in range(1, n):
        if (k % 2 == 1 and right < n) or left < 0:
            out[right] = inc[k]
            right += 1
        else:
            out[left] = inc[k]
            left -= 1

    return Hyetograph(
        depth_mm=depth_mm,
        duration_min=duration_min,
        step_min=step_min,
        peak_pct=peak_pct,
        t_min=[i * step_min for i in range(n)],
        step_depth_mm=out,
        intensity_mm_hr=[d * 60 / step_min for d in out],
        peak_index=pk,
        peak_intensity_5min_mm_hr=peak_5min,
        equivalent=eq,
    )


def presets() -> list[dict]:
    """9 个预设，按行（雨量）优先展开，各带默认步长、峰值位置与等效重现期。"""
    return [
        {
            "depth_mm": p,
            "duration_min": d,
            "step_min": default_step(d),
            "peak_pct": DEFAULT_PEAK_PCT,
            "equivalent": equivalent_return_period(p, d).to_dict(),
        }
        for p in PRESET_DEPTHS_MM
        for d in PRESET_DURATIONS_MIN
    ]
