# -*- coding: utf-8 -*-
"""ADM 全市 100 m（治理后）L2：把剩余积水分成两档、各出一个设备清单 JSON（2026-10-28）。
  第 1 档  = 总积水量的 45%：固定设施（泵站、压力管、外排管、蓄水池）
  第 2 档  = 总积水量的 20%：移动泵 + 吸污车（拉水）
总积水量 = 全市净雨量（2D 直接落雨 + 进管网的净雨，取 L1 行避免塘的进水重复计），来自 statistics/summary.csv；L2 已排走 / 暂存的部分不再算。
用法：python tools/adm100a_equipment_split.py <成果目录 ADM_City_61mm1h_100m_after> <输出目录>
"""
import csv, json, math, pathlib, sys

SHARE_FIXED, SHARE_MOBILE = 0.45, 0.20      # 两档之比 45 : 20（占 65% 的剩余水量，即 L2 之后的）
TH = 0.15                                    # 水深 < 0.15 m 算没有积水（用户 10-29 定）
V_PIPE = 2.0                                 # 手册 6.4：压力管正常峰值流速 1–2.0 m/s，应急最多 2.5 m/s（PDF 第 119 页）
V_PIPE_EMERGENCY = 2.5
STANDBY_PER_STATION = 1                      # 手册 6.3.4：每站至少 2 台，允许超配补偿故障；这里每站加 1 台备用（假设）
POND_EMPTY_DAYS = 7                          # 手册 4.14：干塘雨后一周内排空（PDF 第 74 页）
POND_EMPTY_PUMP_M3S = 0.5                    # 假设：塘排空泵单台流量
HAT_NADD = 1.3                               # 手册 4.8.1：最高天文潮位 +1.3 m NADD
DN_LIST = [600, 800, 900, 1000, 1200, 1400, 1600, 1800, 2000, 2400, 3000, 3500]
HEAD_M, EFF = 10.0, 0.65
POND = {"volume_m3": 50000, "depth_m": 4.7, "freeboard_silt_factor": 1.2, "footprint_factor": 1.3}
FIXED_PUMP = {"unit_flow_m3s": 2.0, "pumps_per_station": 4}
PIPE_LEN_PER_STATION_M = 400
OUTFALL = {"dn_mm": 3000, "velocity_ms": 3.0, "length_each_m": 500}
MOBILE = {"mobile_median": 1.47, "mobile_large": 2.05}
HOSE_LEN_PER_PUMP_M = 200
CRANE_PER_PUMPS = 10                          # 假设：每 10 台移动泵配 1 台起重机（L3 定义含起重机；无标准可依，占位）
TANKER = {"capacity_m3": 30.0, "round_trip_h": 1.5}      # 大号罐车 30 m³（用户 10-29 定：全部换成大号车）；往返 1.5 h 为假设
EXISTING_MOVABLE = {"count": 88, "total_flow_m3s": 114.08}


def dn_for(q, v=None):
    v = v or V_PIPE
    for d in DN_LIST:
        if math.pi * (d / 1000) ** 2 / 4 * v >= q:
            return d
    return DN_LIST[-1]


def summary(base):
    rows = list(csv.reader(open(base / "statistics" / "summary.csv", encoding="utf-8-sig")))

    def g(key, col):
        return float(next(r for r in rows[1:] if key in r[1])[col])
    L1, L2 = 2, 3
    rain2d = g("rain falling directly on 2D", L1)
    n1d = g("1D: external inflow", L1) - g("2D to 1D", L1)
    return {"rain_on_2d_m3": rain2d, "rain_into_1d_m3": n1d, "total_m3": rain2d + n1d,
            "L2_leaves_domain_m3": g("boundary outflow", L2) + g("1D: outfall outflow", L2), "L2_into_ponds_m3": g("taken into ponds", L2),
            "L2_surface_retained_5h_m3": g("retained in the 2D domain", L2)}


def main():
    base = pathlib.Path(sys.argv[1]); out = pathlib.Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    import numpy as np
    s = summary(base); V = s["total_m3"]
    z = np.load(next((base / "L2_with_ponds" / "2D").glob("*_L2_2d.npz"))); a = z["area"].astype(float); fr = z["frames"]
    ex = []
    for i in range(len(z["t_h"])):
        h = fr[i].astype(float); m = h >= TH
        ex.append(float(((h[m] - TH) * a[m]).sum()))
    Vrem = max(ex)
    tot = SHARE_FIXED + SHARE_MOBILE
    v1, v2 = Vrem * SHARE_FIXED / tot, Vrem * SHARE_MOBILE / tot
    inputs = {"dry_definition": "a cell has no ponding when its depth is below 0.15 m; the volume to remove is sum((depth - 0.15 m) x cell area) over cells with depth >= 0.15 m",
              "threshold_depth_m": TH, "remaining_ponding_after_L2_m3": round(Vrem), "remaining_basis": "max over the 60 frames (ponding is still growing at 5 h, the end of the run, so this is a lower bound)",
              "split": f"{SHARE_FIXED:g} : {SHARE_MOBILE:g} of the remaining volume (tier 1 = {SHARE_FIXED / tot:.1%}, tier 2 = {SHARE_MOBILE / tot:.1%})",
              "volume_fixed_m3": round(v1), "volume_mobile_m3": round(v2),
              "total_net_rain_m3": round(V), "L2_leaves_domain_m3": round(s["L2_leaves_domain_m3"]), "L2_into_ponds_m3": round(s["L2_into_ponds_m3"]), "L2_surface_retained_5h_m3": round(s["L2_surface_retained_5h_m3"])}
    common = {"generated": "2026-10-28",
              "scope": "ADM city 100 m (post-improvement data), 61 mm / 1 h, L2. Whole-city totals, no locations; counts are a lower bound (water is spread over many separate low spots).",
              "inputs": inputs}
    # ---------------- 第 1 档：固定设施
    sc = []
    for T in (12, 24, 48, 72):
        q = v1 / (T * 3600.0)
        for ext in (1.0, 0.5, 0.3, 0.0):
            n_pump = math.ceil(q / FIXED_PUMP["unit_flow_m3s"]); n_st = math.ceil(n_pump / FIXED_PUMP["pumps_per_station"])
            q_st = FIXED_PUMP["unit_flow_m3s"] * FIXED_PUMP["pumps_per_station"]
            q_out = OUTFALL["velocity_ms"] * math.pi * (OUTFALL["dn_mm"] / 1000) ** 2 / 4
            n_out = math.ceil(q * ext / q_out) if ext > 0 else 0
            vp = v1 * POND["freeboard_silt_factor"] * (1 - ext)
            n_pond = math.ceil(vp / POND["volume_m3"]) if vp > 0 else 0
            area = n_pond * POND["volume_m3"] / POND["depth_m"] / 1e4
            n_p = n_st * FIXED_PUMP["pumps_per_station"]
            sc.append({"id": f"T{T}h_ext{int(ext * 100)}", "drain_time_h": T, "external_share": ext, "volume_to_remove_m3": round(v1), "total_flow_required_m3s": round(q, 1),
                       "pump_stations": {"count": n_st, "duty_pumps_per_station": FIXED_PUMP["pumps_per_station"], "standby_pumps_per_station": STANDBY_PER_STATION, "pump_unit_flow_m3s": FIXED_PUMP["unit_flow_m3s"], "duty_pumps_total": n_p, "standby_pumps_total": n_st * STANDBY_PER_STATION, "mobile_generator_junction_boxes": n_st, "pumps_total": n_p + n_st * STANDBY_PER_STATION,
                                         "station_flow_m3s": q_st, "power_total_mw_assumed": round(9.81 * 1000 * FIXED_PUMP["unit_flow_m3s"] * HEAD_M / EFF * n_p / 1e6, 1)},
                       "pressure_pipe": {"one_main_per_station": True, "dn_mm": dn_for(q_st), "count": n_st, "length_each_m_assumed": PIPE_LEN_PER_STATION_M,
                                         "total_length_km": round(n_st * PIPE_LEN_PER_STATION_M / 1000, 1), "design_velocity_ms": V_PIPE, "emergency_max_velocity_ms": V_PIPE_EMERGENCY},
                       "external_discharge": {"share": ext, "flow_m3s": round(q * ext, 1),
                                              "outfall_pipe": {"dn_mm": OUTFALL["dn_mm"], "unit_flow_m3s": round(q_out, 1), "count": n_out, "length_each_m_assumed": OUTFALL["length_each_m"],
                                                               "total_length_km": round(n_out * OUTFALL["length_each_m"] / 1000, 1)},
                                              "non_return_valve_chambers": n_out, "trash_screens": n_out, "safety_grills": n_out, "tailwater_design_level_m_NADD": HAT_NADD},
                       "ponds": {"count": n_pond, "volume_each_m3": POND["volume_m3"], "depth_m": POND["depth_m"], "total_volume_m3": n_pond * POND["volume_m3"],
                                 "area_each_ha": round(POND["volume_m3"] / POND["depth_m"] / 1e4, 2), "net_area_ha": round(area, 1),
                                 "area_with_slopes_roads_ha": round(area * POND["footprint_factor"], 1), "emergency_spillways": n_pond,
                                 "emptying": {"within_days": POND_EMPTY_DAYS, "flow_m3s": round(n_pond * POND["volume_m3"] / (POND_EMPTY_DAYS * 86400.0), 2), "pump_unit_flow_m3s_assumed": POND_EMPTY_PUMP_M3S,
                                              "pumps": math.ceil(n_pond * POND["volume_m3"] / (POND_EMPTY_DAYS * 86400.0) / POND_EMPTY_PUMP_M3S) if n_pond else 0}}})
    d1 = {"title": "Tier 1 (45 / 65 of the remaining ponding, depth >= 0.15 m): fixed facilities - pump stations, pressure pipes, outfall pipes, ponds", **common,
          "assumptions": {"fixed_pump": FIXED_PUMP, "pressure_pipe_velocity_ms": V_PIPE, "pressure_pipe_length_each_m": PIPE_LEN_PER_STATION_M, "outfall_pipe": OUTFALL,
                          "pump_head_m": HEAD_M, "pump_efficiency": EFF, "pond": POND,
                          "manual": {"source": "Abu Dhabi DMT Stormwater & Subsoil Drainage Systems, Vol. 1 Design Manual WA-726-1, 3rd edition, April 2022", "pressure_main_velocity": "1-2.0 m/s at peak, max 2.5 m/s in emergencies (6.4, PDF p.119)", "pump_station": "at least 2 pumps per station; 3 pumps at 50% each (6.3.4, PDF p.101)",
                                     "outfall": "HAT +1.3 m NADD as tailwater; non-return valve chamber above HAT, trash screen (bars 100 mm), safety grill, pipe soffit below mean spring low water (4.8, PDF p.67-68)", "dry_pond": "emptied within one week; emergency spillway; access width >= 5 m (4.14, PDF p.74-75)", "soakaways": "not permitted in new systems except by study (4.12); the 22 modelled soakaways are not extended"},
                          "notes": ["Pumps are needed in every scenario (also to fill ponds); external_share only changes outfall pipes and ponds.",
                                    "Pipe lengths, pump head and efficiency are placeholders (no location data).", "The manual gives no share of ponding that can be discharged externally; the external shares are planning scenarios (about 27% of the remaining ponding lies within 2 km of a FIXED or FREE outfall, 52% within 5 km)."]},
          "scenarios": sc}
    (out / "ADM_100m_after_L2_tier1_fixed.json").write_text(json.dumps(d1, ensure_ascii=False, indent=1), encoding="utf-8")
    # ---------------- 第 2 档：罐车全部运走（大号车）+ 装车泵 + 起重机
    sc = []
    for T in (24, 12, 6, 2):
        trips = max(1, math.floor(T / TANKER["round_trip_h"] + 1e-9))      # 每辆车在 T 内只能跑完整趟
        per_truck = TANKER["capacity_m3"] * trips
        n_truck = math.ceil(v2 / per_truck)
        q = v2 / (T * 3600.0)
        mp = {}
        for k, qp in MOBILE.items():
            n = math.ceil(q / qp)
            mp[k] = {"unit_flow_m3s": qp, "count_needed": n, "existing_available": EXISTING_MOVABLE["count"], "additional_needed": max(0, n - EXISTING_MOVABLE["count"]),
                     "power_total_mw_assumed": round(9.81 * 1000 * qp * HEAD_M / EFF * n / 1e6, 1)}
        sc.append({"id": f"T{T}h", "drain_time_h": T, "volume_to_remove_m3": round(v2), "volume_by_tankers_m3": round(v2),
                   "tankers": {"count": n_truck, "capacity_m3_each": TANKER["capacity_m3"], "round_trip_h": TANKER["round_trip_h"], "trips_each": trips},
                   "loading_pumps": {"role": "fill the tankers at the low spots; flow = volume / drain time", "flow_required_m3s": round(q, 1), "options": mp},
                   "cranes": {"count": math.ceil(mp["mobile_median"]["count_needed"] / CRANE_PER_PUMPS), "basis": f"1 crane per {CRANE_PER_PUMPS} loading pumps (median size), placeholder assumption"}})
    d2 = {"title": "Tier 2 (20 / 65 of the remaining ponding, depth >= 0.15 m): all hauled away by large tankers; mobile pumps are the loading pumps; cranes", **common,
          "assumptions": {"tanker": TANKER, "loading_pump_units_m3s": MOBILE, "existing_movable_pumps": EXISTING_MOVABLE, "crane_per_loading_pumps": CRANE_PER_PUMPS, "pump_head_m": HEAD_M, "pump_efficiency": EFF,
                          "notes": ["All of tier 2 is hauled by tankers (user decision); no discharge lines or hoses.", "Tanker capacity 30 m3 (large) and the 1.5 h round trip are assumptions; ",
                                    "L3 in the L1/L2/L3 definition = tankers, mobile pumps, cranes; the crane count is a placeholder (no standard in the manual or the data).", "Tanker dumping sites and their capacity are not modelled."]},
          "scenarios": sc}
    (out / "ADM_100m_after_L2_tier2_mobile.json").write_text(json.dumps(d2, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(inputs, ensure_ascii=False, indent=1))


main()
