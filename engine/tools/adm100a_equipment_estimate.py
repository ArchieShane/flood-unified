# -*- coding: utf-8 -*-
"""ADM 全市 100 m（治理后）L2 积水 → 排水设备总量估算（整体下限，不含位置）→ JSON（2026-10-28）。
读 L2 的 *_2d.npz 逐帧算积水量，按排完时间 T 和外排比例算：移动泵、压力管、外排出水口 / 排水管、蓄水池、泵功率。
用法：python tools/adm100a_equipment_estimate.py <成果目录 ADM_City_61mm1h_100m_after> <输出.json>
"""
import json, math, sys, pathlib
import numpy as np

TH = 0.15                      # 积水阈值 m（用户 10-29 定：水深 < 0.15 m 算没有积水）
T_HOURS = [2, 4, 6, 12, 24]    # 要求排完时间
EXT_SHARE = [1.0, 0.5, 0.3, 0.0]    # 外排比例（其余进塘）
PUMPS = {"mobile_median": 1.47, "mobile_large": 2.05}          # m3/s，MOVABLE_PUMP 中位 / 最大
V_PIPE, DN_LIST = 2.0, [600, 800, 900, 1000, 1200, 1400, 1600, 1800, 2000, 2400, 3000]
PIPE_LEN_PER_PUMP_M = 300      # 假设：每台泵压力管长度（无位置数据，占位，可由下游程序覆盖）
OUTFALL_DN, OUTFALL_V, OUTFALL_LEN_M = 3000, 3.0, 500          # 假设：外排排水管 DN3000、流速 3 m/s、每根长 500 m（占位）
HEAD_M, EFF = 10.0, 0.65       # 假设：泵扬程、效率（占位）
POND = {"volume_m3": 200000, "depth_m": 4.0, "freeboard_silt_factor": 1.2, "footprint_factor": 1.3}


def dn_for(q):
    for d in DN_LIST:
        if math.pi * (d / 1000) ** 2 / 4 * V_PIPE >= q:
            return d
    return DN_LIST[-1]


def main():
    base = pathlib.Path(sys.argv[1]); out = pathlib.Path(sys.argv[2])
    f = next((base / "L2_with_ponds" / "2D").glob("*_L2_2d.npz"))
    z = np.load(f); th = z["t_h"]; a = z["area"].astype(float); fr = z["frames"]
    series = []
    for i in range(len(th)):
        h = fr[i].astype(float); m = h >= TH
        series.append({"t_h": round(float(th[i]), 3), "surface_water_m3": round(float((h * a).sum())), "ponding_ge_0p15m_m3": round(float((h[m] * a[m]).sum())),
                       "excess_above_0p15m_m3": round(float(((h[m] - TH) * a[m]).sum())), "ponding_area_ge_0p15m_ha": round(float(a[m].sum()) / 1e4, 1)})
    at = lambda t: series[int(np.argmin(abs(th - t)))]
    v_design = max(s["excess_above_0p15m_m3"] for s in series)
    inputs = {"source": str(f.name), "res_m": 100, "domain_km2": round(float(a.sum()) / 1e6, 1), "threshold_depth_m": TH, "dry_definition": "a cell has no ponding when its depth is below 0.15 m; water above 0.15 m is what has to be removed",
              "at_rain_end_1h": at(1.0), "at_rain_end_plus_2h_3h": at(3.0), "at_end_of_run_5h": at(5.0),
              "design_volume_m3": round(v_design),
              "design_volume_basis": "max over frames of sum((depth-0.15 m)*cell area) for cells with depth >= 0.15 m; ponding is still growing at 5 h (end of the run), so this is a lower bound"}
    sc = []
    for T in T_HOURS:
        q = v_design / (T * 3600.0)
        for s in EXT_SHARE:
            q_ext = q * s
            eq = {"pumps": {}}
            for k, qp in PUMPS.items():
                n = math.ceil(q / qp); dn = dn_for(qp)
                eq["pumps"][k] = {"unit_flow_m3s": qp, "count": n, "pressure_pipe": {"dn_mm": dn, "count": n, "length_each_m_assumed": PIPE_LEN_PER_PUMP_M, "total_length_km": round(n * PIPE_LEN_PER_PUMP_M / 1000, 1), "design_velocity_ms": V_PIPE},
                                  "power_total_mw_assumed": round(9.81 * qp * 1000 * HEAD_M / EFF * n / 1e6, 1)}
            q_out = OUTFALL_V * math.pi * (OUTFALL_DN / 1000) ** 2 / 4
            n_out = math.ceil(q_ext / q_out) if q_ext > 0 else 0
            vp = v_design * POND["freeboard_silt_factor"] * (1 - s)
            n_pond = math.ceil(vp / POND["volume_m3"]) if vp > 0 else 0
            area_net = n_pond * POND["volume_m3"] / POND["depth_m"] / 1e4
            eq["external_discharge"] = {"share": s, "flow_m3s": round(q_ext, 1), "outfall_pipe": {"dn_mm": OUTFALL_DN, "unit_flow_m3s": round(q_out, 1), "count": n_out, "length_each_m_assumed": OUTFALL_LEN_M, "total_length_km": round(n_out * OUTFALL_LEN_M / 1000, 1)}}
            eq["ponds"] = {"count": n_pond, "volume_each_m3": POND["volume_m3"], "depth_m": POND["depth_m"], "total_volume_m3": n_pond * POND["volume_m3"],
                           "area_each_ha": round(POND["volume_m3"] / POND["depth_m"] / 1e4, 2), "net_area_ha": round(area_net, 1), "area_with_slopes_roads_ha": round(area_net * POND["footprint_factor"], 1)}
            sc.append({"id": f"T{T}h_ext{int(s * 100)}", "drain_time_h": T, "external_share": s, "total_flow_required_m3s": round(q, 1), "volume_to_remove_m3": round(v_design), **eq})
    doc = {"title": "ADM city 100 m (post-improvement data), 61 mm / 1 h, L2: equipment needed to drain the ponding (whole-city lower bound, no locations)",
           "generated": "2026-10-28", "standard": "all cells below 0.15 m; drain time T counted from the start of pumping",
           "standard_scenario_id": "T2h_ext100", "inputs": inputs, "assumptions": {
               "pump_units_m3s": PUMPS, "pressure_pipe_velocity_ms": V_PIPE, "pressure_pipe_length_each_m": PIPE_LEN_PER_PUMP_M, "outfall_pipe": {"dn_mm": OUTFALL_DN, "velocity_ms": OUTFALL_V, "length_each_m": OUTFALL_LEN_M},
               "pump_head_m": HEAD_M, "pump_efficiency": EFF, "pond": POND,
               "notes": ["Whole-city volume treated as one body of water: no locations, no network limits; real unit counts are higher.", "Pipe lengths, pump head and efficiency are placeholders (no location data): override downstream.",
                         "Pumps are needed in every scenario (also to fill ponds); external_share only changes the outfall pipes and the ponds.", "mobile pump sizes come from the MOVABLE_PUMP layer (median 1,470 L/s, max 2,050 L/s)."]},
           "reference": {"existing_movable_pumps": {"count": 88, "total_flow_m3s": 114.1, "hours_to_remove_design_volume": round(v_design / 114.1 / 3600, 1)},
                         "pumps_in_L2_model": {"groups": 38, "total_flow_m3s": 23.4}},
           "time_series": series, "scenarios": sc}
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print("written", out, "design volume", round(v_design))


main()
