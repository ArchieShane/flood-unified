"""派生雨型（只供页面看图，不进任何计算链）：仿真详情页的雨型图用。前缀 `/api/hyetograph`。

原本在指挥中心的主 API 里，「只保留仿真模块」时一并删掉，前端的雨型图因此 404；这里补回，逻辑见 `hyetograph.py`。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from .. import hyetograph
from ..i18n import resolve_lang, tr
from ..idf import ADQCC_ZONE_B_DDF_DEPTH_MM

router = APIRouter(prefix="/api/hyetograph", tags=["hyetograph"])


def _lang(lang: str | None = None, accept_language: str | None = Header(default=None, alias="Accept-Language")) -> str:
    return resolve_lang(lang, accept_language)


Lang = Depends(_lang)


@router.get("/presets")
def hyetograph_presets(lang: str = Lang) -> dict:
    """雨型图页签的底板：官方 DDF 累计雨量表、9 个预设（带等效重现期）与证据边界。"""
    return {
        "source": tr(hyetograph.SOURCE, lang),
        "method": tr(hyetograph.METHOD, lang),
        "boundary": tr(hyetograph.BOUNDARY, lang),
        "durations_min": list(hyetograph.DURATIONS_MIN),
        "return_periods": list(hyetograph.RETURN_PERIODS),
        "ddf_depth_mm": {rp: list(vals) for rp, vals in sorted(ADQCC_ZONE_B_DDF_DEPTH_MM.items(), reverse=True)},
        "preset_depths_mm": list(hyetograph.PRESET_DEPTHS_MM),
        "preset_durations_min": list(hyetograph.PRESET_DURATIONS_MIN),
        "default": {"depth_mm": hyetograph.DEFAULT_PRESET[0], "duration_min": hyetograph.DEFAULT_PRESET[1]},
        "step_choices_min": list(hyetograph.STEP_CHOICES_MIN),
        "default_peak_pct": hyetograph.DEFAULT_PEAK_PCT,
        "presets": hyetograph.presets(),
    }


@router.get("")
def hyetograph_series(
    depth_mm: float,
    duration_min: float,
    step_min: int | None = None,
    peak_pct: float = hyetograph.DEFAULT_PEAK_PCT,
    lang: str = Lang,
) -> dict:
    """一条派生雨型：每步起点、雨深、雨强，以及 Zone B 等效重现期。`step_min` 省略时按历时取默认。参数不合法回 422。"""
    step = step_min if step_min is not None else hyetograph.default_step(duration_min)
    try:
        return hyetograph.build(depth_mm, duration_min, step, peak_pct).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=tr(str(exc), lang)) from exc
