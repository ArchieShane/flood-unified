"""与预测侧（一维二维水动力模型 / 城市内涝世界模型）之间的数据契约。

三种文件，都是 JSON，都带 schema 版本号：
  prediction_state  预测侧 → 本系统：某个情景、某个干预集合（基线为 null）下的网络状态与 KPI
  whatif_request    本系统 → 预测侧：请评估这些干预集合，在这些情景下
  whatif_result     预测侧 → 本系统：每个（干预集合 × 情景）的 KPI 与相对基线的变化

对象用源库的编号相认：管段 gid（pipe.sw_edge.gid）与 unitid，节点 vertex_id（sw_edge_vertices_pgr.id），出水口 unitid。
本文件只定义形状与校验，不做任何水力计算。
"""
from __future__ import annotations
import datetime as dt
import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

SCHEMA_PREDICTION = "swbrain.prediction_state/1"
SCHEMA_REQUEST = "swbrain.whatif_request/1"
SCHEMA_RESULT = "swbrain.whatif_result/1"
SCHEMA_OBSERVATION = "swbrain.event_observation/1"
SCHEMA_COMPARISON = "swbrain.event_comparison/1"
SCHEMA_FLOOD_POINTS = "swbrain.flood_points/1"
SCHEMA_HOTSPOTS = "swbrain.hotspots/1"          # 客户热点清单（DMT 各市政区 Stormwater Hotspot Analysis）→ 本系统：位置、重要度、根因、预案 A/B 资源、IDD 干预计划      # 世界模型/水动力模型 → 本系统：一个情景下的城市积水区分布与过程线
SCHEMA_FLEET = "swbrain.fleet/1"                    # 移动泵队台账：型号、驻地、状态
SCHEMA_DISPATCH = "swbrain.dispatch_plan/1"         # 本系统 → 现场：预置方案与应急调度单

# 干预类型与参数约定（预测侧按这些类型改它的模型输入）
INTERVENTION_TYPES = {
    "pipe_upsize":   {"target": ["gid", "unitid"], "params": ["diameter_mm"], "desc": "把这段管换成新管径，标高与走向不变"},
    "add_storage":   {"target": ["vertex_id", "lon", "lat"], "params": ["volume_m3", "invert_m", "outlet_type"], "desc": "在节点旁加调蓄（塘/池/渗井群），outlet_type=infiltration|orifice|pump"},
    "add_pump":      {"target": ["outfall_unitid", "vertex_id"], "params": ["capacity_m3s", "on_level_m", "off_level_m"], "desc": "在出水口前加泵站或给现有泵站增容"},
    "tide_gate":     {"target": ["outfall_unitid"], "params": ["mode"], "desc": "拍门改可控闸，mode=flap|controlled"},
    "connect":       {"target": ["from_vertex_id", "to_vertex_id"], "params": ["diameter_mm", "length_m", "invert_up_m", "invert_dn_m"], "desc": "新增一段管把无出路的支连到有出路的节点"},
    "fix_topology":  {"target": ["gid"], "params": ["action"], "desc": "数据修正：reverse|remove|relink，预测侧照改后重算基线"},
    "add_inlet":     {"target": ["lon", "lat", "to_vertex_id"], "params": ["count"], "desc": "补进水口"},
    "temp_pump":     {"target": ["vertex_id"], "params": ["capacity_m3s", "hours_before_event"], "desc": "事件前临时移动泵，调度类"},
    "pre_drain":     {"target": ["unitid"], "params": ["target_level_m", "hours_before_event"], "desc": "事件前把塘/池预排到目标水位，调度类"},
}

# KPI 名字表：两边都用这些键；预测侧能给几个给几个
KPI_KEYS = {
    "flood_volume_m3": "地面积水总量",
    "flooded_area_m2": "积水面积（深度 ≥ 阈值）",
    "max_depth_m": "最大积水深度",
    "affected_inlets": "受影响进水口数（所在节点冒溢）",
    "affected_buildings": "受影响建筑数（2D 网格深度 ≥ 阈值）",
    "surcharge_len_km": "满管/承压管长",
    "tide_locked_min": "出水口被顶托的总分钟数",
    "flood_duration_min": "积水历时（节点最大值）",
}


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Scenario:
    name: str                       # 例：常规 / 强降雨 / 极端 / 2024-04-16 实测
    rain_mmh: float | None = None   # 短历时雨强
    rain_depth_mm: float | None = None
    duration_min: float | None = None
    return_period_yr: float | None = None
    tide_m: float | None = None     # 相对 MSL
    event_id: str | None = None     # 世界模型里的事件编号
    start: str | None = None
    end: str | None = None


@dataclass
class Intervention:
    type: str
    target: dict[str, Any]
    params: dict[str, Any]
    proposal_pid: int | None = None
    note: str | None = None

    def validate(self):
        spec = INTERVENTION_TYPES.get(self.type)
        if spec is None:
            raise ValueError(f"未知干预类型 {self.type}，可用：{list(INTERVENTION_TYPES)}")
        if not any(k in self.target for k in spec["target"]):
            raise ValueError(f"{self.type} 的 target 至少要有 {spec['target']} 之一，现在是 {self.target}")
        return self


@dataclass
class InterventionSet:
    set_id: str
    interventions: list[Intervention]
    label: str = ""
    cost_aed: float | None = None
    proposals: list[int] = field(default_factory=list)


@dataclass
class WhatIfRequest:
    request_id: str
    created_at: str
    network_version: str
    baseline_prediction_run: str | None
    scenarios: list[Scenario]
    intervention_sets: list[InterventionSet]
    schema: str = SCHEMA_REQUEST
    kpis_wanted: list[str] = field(default_factory=lambda: list(KPI_KEYS))
    reply_to: str | None = None    # 结果文件该放哪 / 该 POST 到哪

    @staticmethod
    def new(network_version: str, scenarios: list[Scenario], sets: list[InterventionSet], baseline: str | None, reply_to: str | None) -> "WhatIfRequest":
        for s in sets:
            for i in s.interventions:
                i.validate()
        return WhatIfRequest(request_id="wr-" + uuid.uuid4().hex[:12], created_at=_now(), network_version=network_version,
                             baseline_prediction_run=baseline, scenarios=scenarios, intervention_sets=sets, reply_to=reply_to)

    def dump(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def load_prediction_state(path: Path) -> dict:
    """读预测侧给的状态文件并做最小校验。返回原 dict（加 _path）。"""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if d.get("schema") != SCHEMA_PREDICTION:
        raise ValueError(f"{path}: schema 应为 {SCHEMA_PREDICTION}，实际 {d.get('schema')}")
    for k in ("run_id", "scenario", "network_version"):
        if k not in d:
            raise ValueError(f"{path}: 缺 {k}")
    for coll, key in (("nodes", "vertex_id"), ("edges", "gid"), ("outfalls", "unitid")):
        for i, row in enumerate(d.get(coll, []) or []):
            if key not in row and not (coll == "nodes" and "unitid" in row):
                raise ValueError(f"{path}: {coll}[{i}] 缺 {key}")
    d["_path"] = str(path)
    return d


def load_whatif_result(path: Path) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if d.get("schema") != SCHEMA_RESULT:
        raise ValueError(f"{path}: schema 应为 {SCHEMA_RESULT}")
    for k in ("request_id", "results"):
        if k not in d:
            raise ValueError(f"{path}: 缺 {k}")
    for i, r in enumerate(d["results"]):
        for k in ("set_id", "scenario", "kpis"):
            if k not in r:
                raise ValueError(f"{path}: results[{i}] 缺 {k}")
    d["_path"] = str(path)
    return d


def load_event_observation(path: Path) -> dict:
    """一场雨的实测：雨量、积水点、泵站运行。由现场/DMT/预测侧任何一方提供。"""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if d.get("schema") != SCHEMA_OBSERVATION:
        raise ValueError(f"{path}: schema 应为 {SCHEMA_OBSERVATION}")
    for k in ("event_id", "start", "end", "flood_points"):
        if k not in d:
            raise ValueError(f"{path}: 缺 {k}")
    d["_path"] = str(path)
    return d


def example_event_observation() -> dict:
    return {
        "schema": SCHEMA_OBSERVATION,
        "event_id": "2026-11-03",
        "start": "2026-11-03T02:00:00+04:00", "end": "2026-11-03T09:00:00+04:00",
        "rain": [{"station": "Abu Dhabi Intl", "total_mm": 48.0, "max_15min_mmh": 72.0}],
        "tide_max_m": 0.9,
        "flood_points": [
            {"vertex_id": 157832, "lon": 54.55, "lat": 24.40, "observed_depth_m": 0.25, "duration_min": 70, "source": "投诉", "flooded": True},
            {"vertex_id": 48630, "lon": 54.51, "lat": 24.42, "observed_depth_m": 0.0, "duration_min": 0, "source": "巡检", "flooded": False}
        ],
        "pump_runs": [{"station": "SW_AZA_E16_PSS_1", "runtime_min": 210, "max_wet_well_level_m": 1.8, "faults": 0}],
        "notes": "积水点来自 993 投诉与巡检打点，深度为现场估读",
    }


def example_prediction_state() -> dict:
    """给预测侧看的样例：他们照这个形状产出即可。"""
    return {
        "schema": SCHEMA_PREDICTION,
        "run_id": "hydro-2026-09-20-001",
        "model": {"name": "AD 1D2D", "type": "1D2D", "version": "0.9"},
        "network_version": "pipe@2026-09-15",
        "scenario": asdict(Scenario(name="强降雨", rain_mmh=60, duration_min=60, return_period_yr=10, tide_m=1.0)),
        "intervention_set_id": None,
        "nodes": [{"vertex_id": 157832, "unitid": "SW_MBZ_Z17_01_INL_18922", "max_depth_m": 0.32, "flood_volume_m3": 410.0, "flood_duration_min": 55, "max_hgl_m": 3.9}],
        "edges": [{"gid": 48630, "max_flow_m3s": 0.61, "max_fill_ratio": 1.0, "surcharge_min": 40}],
        "outfalls": [{"unitid": "SW_MUS_MN3_OFL_83", "tide_locked_min": 95, "max_discharge_m3s": 0.8}],
        "cells_geojson": "predictions/hydro-2026-09-20-001_depth.geojson",
        "kpis": {"flood_volume_m3": 182000, "flooded_area_m2": 1.2e6, "affected_inlets": 2140, "affected_buildings": 380, "surcharge_len_km": 610, "tide_locked_min": 95},
    }


def example_whatif_result(request: WhatIfRequest) -> dict:
    return {
        "schema": SCHEMA_RESULT,
        "request_id": request.request_id,
        "model": {"name": "AD 1D2D", "type": "1D2D", "version": "0.9"},
        "results": [
            {"set_id": s.set_id, "scenario": sc.name,
             "kpis": {"flood_volume_m3": 150000, "affected_inlets": 1700},
             "kpi_delta_vs_baseline": {"flood_volume_m3": -32000, "affected_inlets": -440},
             "prediction_run": f"hydro-whatif-{s.set_id}-{sc.name}"}
            for s in request.intervention_sets[:1] for sc in request.scenarios[:1]
        ],
    }


# ---------- 第二、三道防线：积水区、泵队、调度单 ----------
def load_flood_points(path: Path) -> dict:
    """世界模型给的积水区分布。points[i] 至少要有 fp_id、几何（polygon GeoJSON 或 lon/lat+area_m2）、max_depth_m、volume_m3；
    hydrograph 可选：[{t_min, depth_m, volume_m3, inflow_m3h}]，t_min 为距事件开始的分钟。"""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if d.get("schema") != SCHEMA_FLOOD_POINTS:
        raise ValueError(f"{path}: schema 应为 {SCHEMA_FLOOD_POINTS}")
    for k in ("run_id", "scenario", "points"):
        if k not in d:
            raise ValueError(f"{path}: 缺 {k}")
    for i, p in enumerate(d["points"]):
        if "fp_id" not in p or ("geometry" not in p and not ("lon" in p and "lat" in p)):
            raise ValueError(f"{path}: points[{i}] 缺 fp_id 或位置")
    d["_path"] = str(path)
    return d


def load_fleet(path: Path) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if d.get("schema") != SCHEMA_FLEET:
        raise ValueError(f"{path}: schema 应为 {SCHEMA_FLEET}")
    for k in ("depots", "units"):
        if k not in d:
            raise ValueError(f"{path}: 缺 {k}")
    d["_path"] = str(path)
    return d


def example_flood_points() -> dict:
    return {
        "schema": SCHEMA_FLOOD_POINTS,
        "run_id": "gwm-2026-10-01-050yr",
        "model": {"name": "GWM", "type": "world", "version": "0.3"},
        "network_version": "pipe@2026-09-15",
        "scenario": {"name": "50年一遇", "return_period_yr": 50, "rain_depth_mm": 120, "duration_min": 360, "tide_m": 1.1, "event_id": None, "start": "2026-10-01T02:00:00+04:00"},
        "points": [
            {"fp_id": "FP-0001", "geometry": {"type": "Polygon", "coordinates": [[[54.50, 24.41], [54.503, 24.41], [54.503, 24.412], [54.50, 24.412], [54.50, 24.41]]]},
             "lon": 54.5015, "lat": 24.411, "area_m2": 42000, "max_depth_m": 0.55, "volume_m3": 9800, "duration_min": 420,
             "hydrograph": [{"t_min": 0, "depth_m": 0.0, "volume_m3": 0, "inflow_m3h": 0}, {"t_min": 180, "depth_m": 0.55, "volume_m3": 9800, "inflow_m3h": 2600},
                            {"t_min": 300, "depth_m": 0.40, "volume_m3": 6900, "inflow_m3h": 400}, {"t_min": 420, "depth_m": 0.05, "volume_m3": 600, "inflow_m3h": 0}],
             "land_use": "road", "note": "下穿隧道口"}
        ],
        "road_impacts": [{"edge_id": 123456, "max_depth_m": 0.5, "t_from_min": 120, "t_to_min": 400}],
    }


def example_fleet() -> dict:
    return {
        "schema": SCHEMA_FLEET,
        "as_of": "2026-10-01",
        "models": [
            {"model": "trailer-500", "rate_m3h": 500, "head_m": 12, "hose_m": 100, "setup_min": 20, "crew": 2, "weight_t": 3.5, "vehicle_class": "light"},
            {"model": "truck-1500", "rate_m3h": 1500, "head_m": 15, "hose_m": 150, "setup_min": 30, "crew": 3, "weight_t": 12, "vehicle_class": "heavy"},
            {"model": "heavy-3000", "rate_m3h": 3000, "head_m": 20, "hose_m": 300, "setup_min": 40, "crew": 4, "weight_t": 22, "vehicle_class": "heavy"}
        ],
        "depots": [{"depot_id": "D01", "name": "Civil Defence Centre – Mussafah", "lon": 54.50, "lat": 24.35, "capacity_units": 8}],
        "units": [{"unit_id": "P-001", "model": "truck-1500", "depot_id": "D01", "status": "available", "lon": 54.50, "lat": 24.35}],
    }
