"""第三道防线的资源盘点：运输车辆、班组（人员）、物资、罐车与费率。

客户目前只给了预案 A 的分片区泵数、罐车数、人员数（`exchange/hotspots`）。其余按行业常规模拟，整包标 `simulated=True`，
每一项都写明依据，等 DMT / 承包商给台账后替换（config `[dispatch.resources]`）。

模拟依据（阿布扎比抢险常规，WA-726-3 附录 H 与承包商惯例）：
- 运输：拖挂 / 平板车运泵，12 寸泵一车一台（带吊装），8 寸一车两台，6 寸三台一组一车；一辆车在 90 min 窗口内可跑 2 趟。
- 人员：12 寸泵 3 人、8 寸 2 人、6 寸组 6 人（型号表）；无人员数的片区按每台泵 2.5 人估；当班 50%，其余 60 min 内可召回；12 h 班次、30 min 交接。
- 物资：软管每台 250 m 库存，铺设 = 50 m 基础 + 每级接力 100 m；燃油每台 400 L 库存，消耗 12 寸 25 / 8 寸 12 / 6 寸组 18 L/h；照明每 2.5 台泵一套；路障每个热点 2 套。
- 费率（占位，等费率表）：人工 35 AED/人·h，车辆 4.0 / 2.5 AED/km（重 / 轻），柴油 2.6 AED/L，夜班 ×1.25（19:00–06:00）。
"""
from __future__ import annotations
import math
from collections import defaultdict

import numpy as np

DEFAULTS = {
    "simulated": True,
    "regions": ["ADM", "AAM", "DRM"],
    "units_per_vehicle_heavy": 2, "units_per_vehicle_light": 2, "trips_per_window": 2,
    "carry": {"diesel-12in": 1, "diesel-8in": 2, "diesel-6in-x3": 1},
    "crew_per_unit_if_missing": 2.5, "on_duty_share": 0.5, "callable_mobilization_min": 60, "shift_h": 12, "handover_min": 30,
    "hose_m_per_unit": 250, "hose_base_m": 50, "hose_per_stage_m": 100,
    "fuel_l_per_unit": 400, "fuel_l_per_h": {"diesel-12in": 25, "diesel-8in": 12, "diesel-6in-x3": 18}, "fuel_l_per_h_default": 12,
    "lights_per_unit": 0.4, "barriers_per_hotspot": 2,
    "crew_aed_h": 35.0, "vehicle_aed_km": {"heavy": 4.0, "light": 2.5}, "vehicle_speed_kmh": 35.0, "fuel_aed_l": 2.6,
    "night_premium": 1.25, "night_hours": [19, 6],
}


def params(cfg: dict) -> dict:
    R = dict(DEFAULTS)
    user = ((cfg.get("dispatch") or {}).get("resources") or {})
    for k, v in user.items():
        R[k] = ({**DEFAULTS[k], **v} if isinstance(v, dict) and isinstance(DEFAULTS.get(k), dict) else v)
    return R


def fuel_l_per_h(model: str, R: dict) -> float:
    m = str(model)
    for k, v in (R.get("fuel_l_per_h") or {}).items():
        if k in m:
            return float(v)
    return float(R.get("fuel_l_per_h_default", 12))


def carry_of(model: str, R: dict) -> int:
    m = str(model)
    for k, v in (R.get("carry") or {}).items():
        if k in m:
            return max(1, int(v))
    return 1


def contractor_map(cfg: dict) -> tuple[dict, dict, dict | None]:
    """exchange/contractors.json（契约 swbrain.contractors/1）：rows [{depot_id | region[/area], contractor, boundary?}] → (按驻地, 按 (region, area), 来源)。没有文件就两张空表。"""
    try:
        from . import feeds as _feeds
        f = _feeds.load(cfg, "contractors")
    except Exception:
        return {}, {}, None
    by_depot, by_area = {}, {}
    for r in f.get("rows") or []:
        c = r.get("contractor")
        if not c:
            continue
        if r.get("depot_id"):
            by_depot[str(r["depot_id"])] = str(c)
        elif r.get("region"):
            for a in (str(r.get("area") or "").split("/") or [""]):
                by_area[(str(r["region"]), a.strip())] = str(c)
    src = {k: f.get(k) for k in ("source", "as_of", "url") if f.get(k) is not None} if (by_depot or by_area) else None
    return by_depot, by_area, src


def build(fleet: dict, hotspots, cfg: dict) -> dict:
    """车队 + 热点清单 → 每个驻地的车辆 / 班组 / 物资库存，加消耗系数与费率。全部可被 fleet.json 里的同名字段覆盖（真实台账优先）。"""
    R = params(cfg)
    models = {m["model"]: m for m in fleet.get("models", [])}
    units = [u for u in fleet.get("units", []) if u.get("status", "available") == "available"]
    by_depot = defaultdict(list)
    for u in units:
        by_depot[u.get("depot_id")].append(u)
    hs_by_area = {}
    if hotspots is not None and len(hotspots) and "area" in getattr(hotspots, "columns", []):
        hs_by_area = hotspots["area"].astype(str).value_counts().to_dict()
    cm_d, cm_a, cm_src = contractor_map(cfg)                        # 真实承包商服务边界（有文件才有）
    depots = {}
    for d in fleet.get("depots", []):
        did = d["depot_id"]; us = by_depot.get(did, [])
        heavy = [u for u in us if models.get(u["model"], {}).get("vehicle_class") == "heavy"]
        light = [u for u in us if models.get(u["model"], {}).get("vehicle_class") != "heavy"]
        real = d.get("resources") or {}                                   # fleet.json 里若有真实台账，逐项覆盖
        manpower = int((d.get("plan_a") or {}).get("manpower") or 0)
        persons = int(real.get("persons") or (manpower if manpower > 0 else round(len(us) * float(R["crew_per_unit_if_missing"]))))
        on_duty = int(real.get("on_duty") or round(persons * float(R["on_duty_share"])))
        areas = [a.strip() for a in str(d.get("area") or "").split("/") if a.strip()]
        n_hs = sum(hs_by_area.get(a, 0) for a in areas) if areas else 0
        region = str(d.get("region") or "ADM")
        real_c = real.get("contractor") or d.get("contractor") or cm_d.get(str(did)) or next((cm_a.get((region, a)) for a in areas if (region, a) in cm_a), None) or cm_a.get((region, ""))
        contractor = str(real_c or (f"SW-{region}-{areas[0]}" if region == "ADM" and areas else f"SW-{region}"))     # 承包商 = 真实边界文件 / fleet 台账，否则按片区（ADM）/ 市政（AAM、DRM）模拟
        contractor_real = bool(real_c)
        depots[did] = {
            "depot_id": did, "name": d.get("name"), "area": d.get("area"), "n_units": len(us), "region": region, "areas": areas, "contractor": contractor, "contractor_real": contractor_real,
            "vehicles": real.get("vehicles") or {"heavy": int(math.ceil(len(heavy) / float(R["units_per_vehicle_heavy"]))) if heavy else 0,
                                                  "light": int(math.ceil(len(light) / float(R["units_per_vehicle_light"]))) if light else 0},
            "crews": {"persons": persons, "on_duty": on_duty, "callable": max(persons - on_duty, 0), "source": ("plan_a" if manpower > 0 else "simulated") if not real.get("persons") else "fleet"},
            "materials": real.get("materials") or {"hose_m": int(len(us) * float(R["hose_m_per_unit"])), "fuel_l": int(len(us) * float(R["fuel_l_per_unit"])),
                                                    "lights": int(math.ceil(len(us) * float(R["lights_per_unit"]))), "barriers": int(max(n_hs, 1) * float(R["barriers_per_hotspot"]))},
            "tankers": int(real.get("tankers") if real.get("tankers") is not None else ((d.get("plan_a") or {}).get("tankers") or 0)),
        }
    total_units = sum(v["n_units"] for v in depots.values())
    return {
        "simulated": bool(R.get("simulated", True)) and not any((d.get("resources") for d in fleet.get("depots", []))),
        "depots": depots,
        "consumption": {"fuel_l_per_h": {m: fuel_l_per_h(m, R) for m in models}, "hose_base_m": float(R["hose_base_m"]), "hose_per_stage_m": float(R["hose_per_stage_m"]),
                        "carry": {m: carry_of(m, R) for m in models}, "trips_per_window": int(R["trips_per_window"]),
                        "shift_h": float(R["shift_h"]), "handover_min": float(R["handover_min"]), "callable_mobilization_min": float(R["callable_mobilization_min"])},
        "rates": {"crew_aed_h": float(R["crew_aed_h"]), "vehicle_aed_km": dict(R["vehicle_aed_km"]), "vehicle_speed_kmh": float(R["vehicle_speed_kmh"]), "fuel_aed_l": float(R["fuel_aed_l"]),
                  "night_premium": float(R["night_premium"]), "night_hours": list(R["night_hours"])},
        "totals": {"units": total_units, "vehicles_heavy": sum(v["vehicles"]["heavy"] for v in depots.values()), "vehicles_light": sum(v["vehicles"]["light"] for v in depots.values()),
                   "persons": sum(v["crews"]["persons"] for v in depots.values()), "on_duty": sum(v["crews"]["on_duty"] for v in depots.values()),
                   "hose_m": sum(v["materials"]["hose_m"] for v in depots.values()), "fuel_l": sum(v["materials"]["fuel_l"] for v in depots.values()),
                   "tankers": sum(v["tankers"] for v in depots.values())},
        "basis": "车辆 / 班组 / 物资按行业常规由预案 A 泵数与人数推出（模拟），费率为占位；fleet.json 的 depots[].resources 可逐项覆盖",
        "basis_en": "Vehicles / crews / materials derived from Plan A pump and staff counts using industry norms (simulated); rates are placeholders; depots[].resources in fleet.json overrides item by item",
    }


def is_night(t_event_min: float, event_start_hour: float, R: dict) -> bool:
    """事件开始后 t 分钟是否落在夜班时段（用于夜班加价与照明需求）。event_start_hour 是事件开始的本地小时。"""
    h = (float(event_start_hour) + float(t_event_min) / 60.0) % 24.0
    a, b = R["night_hours"]
    return (h >= a or h < b) if a > b else (a <= h < b)
