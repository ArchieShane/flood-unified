"""本体运行时的公共件：动作错误、清洗 / 取数 / 格式化助手、目标名、v2 与路况摘要。
runtime.py 与 actions/*.py 都从这里取，避免循环导入；runtime.py 会把它们原样 re-export，老的 `from .runtime import clean` 照样能用。"""
from __future__ import annotations
import difflib
import json
import math
import re
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import schema as S
from .. import scenarios
from ..gwmcompare import _xy

ROOT = Path(__file__).resolve().parents[2]
_FP_RE = re.compile(r"\b(ADM|AAM|DRM|HS)\s*[-_ ]?\s*(\d{1,4})\b", re.I)


class ActionError(ValueError):
    """参数不合法 / 前置条件不满足 / 对象不存在。"""


def clean(v):
    """numpy / NaN / DataFrame 行 → 可 JSON 的 Python 值。"""
    if isinstance(v, dict):
        return {str(k): clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, np.ndarray):
        return [clean(x) for x in v.tolist()]
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if not math.isfinite(f) else f
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if v is pd.NaT:
        return None
    return v


def _f(x, d=None):
    try:
        f = float(x)
        return d if not math.isfinite(f) else f
    except (TypeError, ValueError):
        return d


OBJ_ZH = {"fastest": "最快退水", "balanced": "平衡", "cheapest": "最省钱"}
OBJ_EN = {"fastest": "Fastest recession", "balanced": "Balanced", "cheapest": "Lowest cost"}


def _v2_brief(v2):
    """v2 调度结果给智能体 / 控制台卡片的精简版：目标、三方案、费用拆解、资源占用合计、未处置原因、人手缺口。"""
    if not isinstance(v2, dict) or not v2:
        return None
    util = v2.get("utilisation") or {}; sch = v2.get("schedule") or {}; res = v2.get("resources") or {}
    return {"objective": v2.get("objective"), "objective_zh": v2.get("objective_zh"), "objective_en": v2.get("objective_en"), "value_aed_per_priority_hour": v2.get("value_aed_per_priority_hour"),
            "n_windows": len(v2.get("windows") or []), "windows": [{k: w.get(k) for k in ("window", "t_min", "status", "n_candidates", "points_served", "units", "ms")} for w in (v2.get("windows") or [])],
            "pareto": v2.get("pareto"), "cost": v2.get("cost"), "unserved_reasons": v2.get("unserved_reasons"), "budget_aed": v2.get("budget_aed"),
            "resources_simulated": res.get("simulated"), "totals": res.get("totals"),
            "used": {"units": sum(u.get("units_used", 0) for u in util.values()), "tankers": sum(u.get("tankers_used", 0) for u in util.values()), "crews": sum(u.get("crews_used", 0) for u in util.values()),
                     "hose_m": sum(u.get("hose_used_m", 0) for u in util.values()), "fuel_l": sum(u.get("fuel_used_l", 0) for u in util.values())},
            "by_depot": [{"depot_id": d, "name": u.get("name"), "units": f"{u.get('units_used')}/{u.get('units_total')}", "crews": f"{u.get('crews_used')}/{u.get('crews_total')}", "tankers": f"{u.get('tankers_used')}/{u.get('tankers_total')}"} for d, u in list(util.items())[:12]],
            "persons_shortage": sch.get("persons_shortage", 0), "relief_persons": sch.get("relief_crews_needed", 0), "trips": len(sch.get("trips") or []),
            "mode": v2.get("mode"), "cross_zone_units": v2.get("cross_zone_units"), "silo_vs_pooled": v2.get("silo_vs_pooled"), "contractors": v2.get("contractors"), "robust": v2.get("robust")}


def _traffic_brief(tr):
    if not isinstance(tr, dict) or not tr:
        return None
    return {k: tr.get(k) for k in ("provider", "simulated", "departure_local", "factor_min", "factor_max", "factor_mean", "elements", "note", "refreshed_at", "routing_preference")}


def _fmt(v, nd=0):
    f = _f(v)
    if f is None:
        return "—"
    return f"{f:,.{nd}f}"


def norm_fp_id(s) -> str | None:
    m = _FP_RE.search(str(s or ""))
    if not m:
        return None
    a, n = m.group(1).upper(), int(m.group(2))
    return f"{'ADM' if a == 'HS' else a}-{n}"


def merge_ui(acc: dict, new: dict | None) -> dict:
    """把一轮里多步动作的控制台联动合成一份。默认后一步覆盖前一步；
    带 pin 的一步（如 as_built_next：切到 L2+A+B、标出补的设施）是这轮问题的主答案，
    后面再调的查询动作（facility_plan L2+A 之类）不许盖掉它的视图 / 态 / 基准 / 高亮。"""
    new = dict(new or {})
    if not new:
        return acc
    if acc.get("pin") and not new.get("pin"):
        for k in ("tab", "l2p", "st", "base", "m", "highlight", "fit"):
            new.pop(k, None)
    acc.update(new)
    return acc

