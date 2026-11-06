"""单热点详情（PoC 议程第 3 项）：一个热点 → 匹配管段、上游汇流树、DTM 洼地面、三条退水曲线（无泵 / 派泵 / 换管）。

纯函数部分放这里（可测），读状态与 DTM 的部分在 serve.State.hotspot_detail。
换管：按 [cost].pipe_aed_per_m_by_dn 的管径序列往上取 1、2 档，用曼宁公式（同坡度、同糙率）算新满管能力，再用与积水点同样的办法
（重现期情景：手册 3.6 梯形；实测事件：雨量计 5 分钟水量平衡）重算过程线，与无泵、派泵曲线放在同一张图上。
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from . import floodpoints as fpm, hydraulics as hy, replay, scenarios

GRID_MIN = 15.0


def next_dns(dn_mm: float | None, cfg: dict, steps: int = 2) -> list[int]:
    """当前管径往上的 steps 档标准管径（来自 [cost].pipe_aed_per_m_by_dn 的键）。"""
    seq = sorted(int(k) for k in ((cfg.get("cost") or {}).get("pipe_aed_per_m_by_dn") or {"300": 0, "400": 0, "500": 0, "600": 0, "800": 0, "1000": 0, "1200": 0, "1500": 0, "2000": 0, "3000": 0}))
    d = float(dn_mm or 300.0)
    bigger = [x for x in seq if x > d + 1e-6]
    return bigger[:steps]


def hydrograph_for_capacity(cfg: dict, S: dict, row: dict, qcap: float) -> tuple[list[dict], float]:
    """按积水点同样的口径，给定管段能力算体积过程线；返回 (过程线, 缺口体积)。row 要有 area_ha / c / tc_min / pond_m3 / area。"""
    area_ha = float(row["area_ha"]); c = float(row.get("c") or cfg["model"]["runoff_c"]); tc = float(row["tc_min"]); pond = float(row.get("pond_m3") or 0.0)
    hy_ = fpm.event_hyetograph(cfg, S, row.get("area"))
    if hy_ is not None:
        hg = fpm.hydrograph_from_hyetograph(area_ha, c, tc, qcap, hy_)
        v_req = hg["v_peak_m3"] * fpm.SED; v = max(v_req - pond, 0.0)
        h = hg["hydrograph"]
        if v_req > 0:
            f = v / v_req; h = [{**q, "volume_m3": round(q["volume_m3"] * f)} for q in h]
        return h, v
    ex = fpm.excess_volume(area_ha, c, tc, qcap, S, cfg, area=row.get("area"))
    v_req = ex["V_m3"] * fpm.SED; v = max(v_req - pond, 0.0)
    if v <= 0:
        return [{"t_min": 0, "depth_m": 0.0, "volume_m3": 0, "inflow_m3h": 0}, {"t_min": 60, "depth_m": 0.0, "volume_m3": 0, "inflow_m3h": 0}], 0.0
    return fpm.hydrograph_network(tc, ex["d_star_min"], ex["excess_m3s"], ex["q_star_m3s"], v, qcap), v


def _resample(r: dict, grid: np.ndarray, key: str = "base_m3") -> list[float]:
    t = np.asarray(r["t_min"], dtype=float); v = np.asarray(r[key], dtype=float)
    return [round(float(x), 0) for x in np.interp(grid, t, v, left=0.0, right=float(v[-1]) if len(v) else 0.0)]


def curves_no_reach(base_hyd: list[dict], plan_rows: pd.DataFrame | list[dict] | None) -> dict:
    """没匹配到管段的点（客户标无管网，按 5 m DTM 洼地估算过程线）：只有无泵 / 派泵两条曲线，没有换管方案。与 curves() 同一输出形状。"""
    pumps = replay.pumps_from_plan(plan_rows) if plan_rows is not None and len(plan_rows) else []
    r_base = replay.replay(base_hyd, [])
    r_pump = replay.replay(base_hyd, pumps) if pumps else None
    t_end = max([float(r_base["t_min"][-1])] + ([float(r_pump["t_min"][-1])] if r_pump else []) + [60.0])
    grid = np.arange(0.0, min(t_end, 2880.0) + GRID_MIN / 2, GRID_MIN)
    def kpi(r, key):
        k = r["kpi"][key]
        return {"peak_m3": k["peak_m3"], "t_empty_min": k["t_empty_min"], "flood_h": k["flood_h"], "never_empties": k["t_empty_min"] is None}
    return {"t_min": [float(x) for x in grid], "base": _resample(r_base, grid, "base_m3"), "base_kpi": kpi(r_base, "base"),
            "pump": (_resample(r_pump, grid, "pump_m3") if r_pump else None), "pump_kpi": (kpi(r_pump, "pump") if r_pump else None),
            "pumps": [{"label": p.get("label"), "arrive_min": p["arrive_min"], "rate_m3h": p["rate_m3h"]} for p in pumps],
            "upsized": [], "reach": {}, "no_reach": True, "no_gap": False}


def curves(cfg: dict, S: dict, row: dict, reach: dict, base_hyd: list[dict], plan_rows: pd.DataFrame | list[dict] | None, dn_steps: int = 2) -> dict:
    """无泵 / 派泵 / 换管 各一条体积曲线，统一到 15 分钟网格；每条带 KPI（峰值、退干、积水时长）。"""
    pumps = replay.pumps_from_plan(plan_rows) if plan_rows is not None and len(plan_rows) else []
    r_base = replay.replay(base_hyd, [])
    r_pump = replay.replay(base_hyd, pumps) if pumps else None
    opts = []
    dn0 = reach.get("pipe_diameter"); slope = float(reach.get("slope") or cfg["model"]["default_slope"]); n = float(reach.get("n_manning") or 0.013)
    for dn in next_dns(dn0, cfg, dn_steps):
        q_new = float(hy.manning_full_flow(np.array([dn / 1000.0]), np.array([slope]), np.array([n]))[0])
        h_new, v_new = hydrograph_for_capacity(cfg, S, row, q_new)
        r_new = replay.replay(h_new, [])
        opts.append({"dn_mm": int(dn), "q_full_m3s": round(q_new, 3), "shortfall_m3": round(v_new, 0), "r": r_new})
    t_end = max([float(r_base["t_min"][-1])] + ([float(r_pump["t_min"][-1])] if r_pump else []) + [float(o["r"]["t_min"][-1]) for o in opts] + [60.0])
    grid = np.arange(0.0, min(t_end, 2880.0) + GRID_MIN / 2, GRID_MIN)
    def kpi(r, key):
        k = r["kpi"][key]
        return {"peak_m3": k["peak_m3"], "t_empty_min": k["t_empty_min"], "flood_h": k["flood_h"], "never_empties": k["t_empty_min"] is None}
    out = {"t_min": [float(x) for x in grid], "base": _resample(r_base, grid, "base_m3"), "base_kpi": kpi(r_base, "base"),
           "pump": (_resample(r_pump, grid, "pump_m3") if r_pump else None), "pump_kpi": (kpi(r_pump, "pump") if r_pump else None),
           "pumps": [{"label": p.get("label"), "arrive_min": p["arrive_min"], "rate_m3h": p["rate_m3h"]} for p in pumps],
           "upsized": [{"dn_mm": o["dn_mm"], "q_full_m3s": o["q_full_m3s"], "shortfall_m3": o["shortfall_m3"], "curve": _resample(o["r"], grid, "base_m3"), "kpi": kpi(o["r"], "base")} for o in opts],
           "reach": {"dn_mm": dn0, "q_full_m3s": reach.get("q_full"), "slope": slope, "n_manning": n}}
    return out
