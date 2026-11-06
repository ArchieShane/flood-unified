"""情景目录：一个 key 同时驱动管网筛查（15 分钟雨强，可按片区不同）、积水点（各历时雨强）与第二、三道防线。

三类：
- 表 3-6 重现期："5" "10" "25" "50" "100"（[idf.return_periods]）；
- 重现期 × 气候变化系数："10cc"（[scenarios.cc]）：DMT 现行评估口径（Parsons 2026-09），10 年 24 h 51.12 mm × 1.2 = 61.34 mm，所有历时雨强同比 ×1.2；
- 实测事件："2024-04" "2026-03"（[scenarios.events]）：按 Parsons 用 NCM 雨量站 Thiessen 加权得到的分区 24 h 雨量，与 10 年 IDF 的 24 h 雨量（51.12 mm）之比，
  等比缩放 10 年 IDF 曲线；片区（管网）或热点区域（积水点）没给雨量的用 p24_default_mm（假设，来源写在 source）。
雨强 i(d) = 表 3-7 公式 i(base_rp, d) × factor(zone / area)。"""
from __future__ import annotations
import numpy as np
import pandas as pd

from . import standards


def _rp_rows(cfg: dict) -> dict:
    return (cfg.get("idf") or {}).get("return_periods") or {}


def catalog(cfg: dict) -> list[dict]:
    """页面开关用：按顺序列出全部情景（重现期 → 气候变化 → 实测事件）。"""
    out = []
    for k in sorted(_rp_rows(cfg), key=lambda x: int(x)):
        out.append(get(cfg, k))
    for k in (cfg.get("scenarios") or {}).get("cc", {}):
        out.append(get(cfg, k))
    for k in (cfg.get("scenarios") or {}).get("events", {}):
        out.append(get(cfg, k))
    return out


def keys(cfg: dict) -> list[str]:
    return [s["key"] for s in catalog(cfg)]


def get(cfg: dict, key) -> dict:
    """情景描述：key / kind / base_rp / factor（缺省）/ i15_mmh / p24_mm / label / label_en / source，事件另有 p24_by_zone / p24_by_area / p24_ref_mm。"""
    key = str(key)
    rows = _rp_rows(cfg); S = cfg.get("scenarios") or {}
    if key in rows:
        r = rows[key]
        return {"key": key, "kind": "rp", "base_rp": int(key), "factor": 1.0, "i15_mmh": float(r["i15_mmh"]), "p24_mm": float(r["p24_mm"]),
                "label": f"{key} 年", "label_en": f"{key}-yr", "source": (cfg.get("idf") or {}).get("source"), "source_en": (cfg.get("idf") or {}).get("source_en")}
    if key in S.get("cc", {}):
        c = S["cc"][key]; base = str(int(c["base_rp"])); f = float(c["factor"])
        if base not in rows:
            raise KeyError(f"[scenarios.cc.{key}] base_rp={base} 不在 [idf.return_periods]")
        return {"key": key, "kind": "rp_cc", "base_rp": int(base), "factor": f, "i15_mmh": round(float(rows[base]["i15_mmh"]) * f, 1), "p24_mm": round(float(rows[base]["p24_mm"]) * f, 2),
                "label": c.get("label", f"{base} 年 ×{f:g}"), "label_en": c.get("label_en", f"{base}-yr ×{f:g}"), "source": c.get("source"), "source_en": c.get("source_en")}
    if key in S.get("events", {}):
        e = S["events"][key]; base = str(int(e.get("base_rp", 10))); ref = float(e.get("p24_ref_mm", rows[base]["p24_mm"] if base in rows else 51.12))
        if base not in rows:
            raise KeyError(f"[scenarios.events.{key}] base_rp={base} 不在 [idf.return_periods]")
        dflt = float(e.get("p24_default_mm", ref)); f = dflt / ref
        return {"key": key, "kind": "event", "base_rp": int(base), "factor": f, "p24_ref_mm": ref, "p24_default_mm": dflt,
                "i15_mmh": round(float(rows[base]["i15_mmh"]) * f, 1), "p24_mm": round(dflt, 2),
                "p24_by_zone": {str(k): float(v) for k, v in (e.get("p24_by_zone") or {}).items()}, "p24_by_area": {str(k): float(v) for k, v in (e.get("p24_by_area") or {}).items()},
                # 有实测雨型时直接给 15 分钟峰值强度（DMT SWMM 雨量计序列），比按 24 h 雨量等比缩放更准
                "i15_by_zone": {str(k): float(v) for k, v in (e.get("i15_by_zone") or {}).items()}, "i15_by_area": {str(k): float(v) for k, v in (e.get("i15_by_area") or {}).items()},
                "i15_ref_mmh": float(rows[base]["i15_mmh"]), "i15_source": e.get("i15_source"), "i15_source_en": e.get("i15_source_en"),
                "label": e.get("label", key), "label_en": e.get("label_en", key), "source": e.get("source"), "source_en": e.get("source_en"), "date": e.get("date")}
    raise KeyError(f"未知情景 {key!r}，可选 {keys(cfg)}")


def factor(scen: dict, zone: str | None = None, area: str | None = None) -> float:
    """该情景在某片区（管网）或某热点区域（积水点）的雨强倍数。事件：先看 area，再看 zone，再缺省。"""
    if scen["kind"] != "event":
        return float(scen["factor"])
    ref = float(scen["p24_ref_mm"]); iref = float(scen.get("i15_ref_mmh") or 0) or None
    if area is not None:
        if iref and area in (scen.get("i15_by_area") or {}):
            return scen["i15_by_area"][area] / iref
        if area in scen["p24_by_area"]:
            return scen["p24_by_area"][area] / ref
    if zone is not None:
        if iref and zone in (scen.get("i15_by_zone") or {}):
            return scen["i15_by_zone"][zone] / iref
        if zone in scen["p24_by_zone"]:
            return scen["p24_by_zone"][zone] / ref
    return float(scen["factor"])


def intensity(cfg: dict, scen, duration_min: float, zone: str | None = None, area: str | None = None) -> float:
    """某情景、某历时（分钟）的雨强 mm/h。"""
    s = scen if isinstance(scen, dict) else get(cfg, scen)
    return standards.idf_intensity(cfg, s["base_rp"], duration_min) * factor(s, zone, area)


def p24(cfg: dict, scen, zone: str | None = None, area: str | None = None) -> float:
    s = scen if isinstance(scen, dict) else get(cfg, scen)
    if s["kind"] == "event":                                  # 直接给 Parsons 分区 24 h 雨量；有 i15 实测时 factor 是 i15 比，不能反推 p24
        if area is not None and area in s["p24_by_area"]:
            return float(s["p24_by_area"][area])
        if zone is not None and zone in s["p24_by_zone"]:
            return float(s["p24_by_zone"][zone])
        return float(s.get("p24_default_mm") or s["p24_ref_mm"] * s["factor"])
    return float(s["p24_mm"])


def zone_factors(cfg: dict, scen, zones) -> np.ndarray:
    """管网筛查用：每段管所在片区的雨强倍数。"""
    s = scen if isinstance(scen, dict) else get(cfg, scen)
    z = pd.Series(zones)
    if s["kind"] != "event":
        return np.full(len(z), float(s["factor"]))
    m = {k: factor(s, zone=k) for k in set(s["p24_by_zone"]) | set(s.get("i15_by_zone") or {})}     # 有实测 15 分钟峰值的片区优先用它
    return z.map(m).fillna(float(s["factor"])).astype(float).values


def label(cfg: dict, scen, lang: str = "zh") -> str:
    s = scen if isinstance(scen, dict) else get(cfg, scen)
    return s["label_en"] if lang == "en" else s["label"]
