"""世界模型接入前的模拟器：用真实的汇水区状态、隧道与重点单位位置，造一份符合 flood_points 契约的积水区分布，
和一份符合 fleet 契约的移动泵队台账。所有输出都带 simulated=true，页面上会明确标「模拟」。

积水区怎么造（不是随机撒点）：
  · 全部 77 条道路隧道：内涝致死最集中的场所，深 0.6–1.5 m，退水慢；
  · 目标情景下承载不足份额最高、进水口最多的汇水区终点附近：深 0.2–0.6 m，面积与汇水区规模挂钩；
  · 少量医院/学校门前的浅积水：用来检验优先级逻辑。
过程线：三角形涨落，雨峰在事件开始后 180 分钟，退水历时按类型不同。
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from .contracts import SCHEMA_FLOOD_POINTS, SCHEMA_FLEET


def _circle(lon, lat, r_m, n=14):
    k = 111000.0
    pts = [[lon + r_m * math.cos(a) / (k * math.cos(math.radians(lat))), lat + r_m * math.sin(a) / k] for a in np.linspace(0, 2 * math.pi, n, endpoint=False)]
    pts.append(pts[0])
    return {"type": "Polygon", "coordinates": [pts]}


def _hydrograph(volume_m3, peak_min, recession_min, total_min, depth_max):
    """三角形过程线：0 → 峰 → 退到 0。入流按体积变化率给。"""
    ts = sorted(set([0, peak_min // 2, peak_min, peak_min + recession_min // 3, peak_min + 2 * recession_min // 3, min(peak_min + recession_min, total_min)]))
    out = []
    for i, t in enumerate(ts):
        if t <= peak_min:
            frac = t / peak_min
        else:
            frac = max(0.0, 1 - (t - peak_min) / recession_min)
        vol = volume_m3 * frac
        if i == 0:
            inflow = 0.0
        else:
            dt_h = (t - ts[i - 1]) / 60.0
            inflow = max(0.0, (vol - out[-1]["volume_m3"]) / dt_h) if dt_h > 0 else 0.0
        out.append({"t_min": int(t), "depth_m": round(depth_max * frac, 3), "volume_m3": round(vol, 0), "inflow_m3h": round(inflow, 0)})
    return out


def simulate_flood_points(cs: pd.DataFrame, facilities: pd.DataFrame, cfg: dict, target: str) -> dict:
    S = cfg["dispatch"]["simulation"]
    rng = np.random.default_rng(S["seed"])
    pts = []
    peak, total = 180, 480
    # 1) 隧道
    tun = facilities[facilities["kind"] == "tunnel"].reset_index(drop=True)
    for i, r in tun.iterrows():
        depth = float(rng.uniform(0.6, 1.5)); area = float(np.clip(r["size"] * rng.uniform(0.3, 0.8), 800, 6000)) if r["size"] > 0 else float(rng.uniform(1200, 4000))
        vol = area * depth * 0.6
        pts.append({"fp_id": f"FP-T{i+1:03d}", "geometry": _circle(r["lon"], r["lat"], math.sqrt(area / math.pi)), "lon": float(r["lon"]), "lat": float(r["lat"]), "area_m2": round(area),
                    "max_depth_m": round(depth, 2), "volume_m3": round(vol), "duration_min": int(peak + 600), "land_use": "tunnel",
                    "hydrograph": _hydrograph(vol, peak, 600, total + 360, depth), "note": "道路隧道低点（模拟）"})
    # 2) 承载不足最重的汇水区终点
    c = cs.dropna(subset=["lon", "lat"]).copy()
    c = c[c["area_m2"] > 2e4]
    c["score"] = c[f"over_share_{target}"].fillna(0) * np.log1p(c["n_inlets"].fillna(0))
    c = c.sort_values("score", ascending=False)
    n_c = max(S["n_points"] - len(tun) - 10, 20)
    for i, r in c.head(n_c).iterrows():
        depth = float(rng.uniform(0.2, 0.6)); area = float(np.clip(r["area_m2"] * rng.uniform(0.01, 0.03), 3000, 120000))
        vol = area * depth * 0.5
        pts.append({"fp_id": f"FP-C{len(pts)+1:03d}", "geometry": _circle(r["lon"], r["lat"], math.sqrt(area / math.pi)), "lon": float(r["lon"]), "lat": float(r["lat"]), "area_m2": round(area),
                    "max_depth_m": round(depth, 2), "volume_m3": round(vol), "duration_min": int(peak + 360), "land_use": "road",
                    "hydrograph": _hydrograph(vol, peak, 360, total, depth), "note": f"汇水区终点 {r['terminal_unitid'] or r['terminal']}，{target}下承载不足份额 {r[f'over_share_{target}']:.0%}（模拟）",
                    "terminal": int(r["terminal"])})
    # 3) 医院/学校门前浅积水
    fac = facilities[facilities["kind"].isin(["hospital", "school"])].sample(10, random_state=S["seed"])
    for i, r in fac.iterrows():
        depth = float(rng.uniform(0.15, 0.35)); area = float(rng.uniform(1500, 6000)); vol = area * depth * 0.5
        pts.append({"fp_id": f"FP-F{len(pts)+1:03d}", "geometry": _circle(r["lon"] + 0.0006, r["lat"] + 0.0004, math.sqrt(area / math.pi)), "lon": float(r["lon"] + 0.0006), "lat": float(r["lat"] + 0.0004),
                    "area_m2": round(area), "max_depth_m": round(depth, 2), "volume_m3": round(vol), "duration_min": int(peak + 240), "land_use": "access_road",
                    "hydrograph": _hydrograph(vol, peak, 240, total, depth), "note": f"{r['kind']} {r['name']} 门前（模拟）"})
    return {"schema": SCHEMA_FLOOD_POINTS, "run_id": f"SIM-{S['scenario_name']}", "simulated": True,
            "model": {"name": "simulate.py", "type": "simulated", "version": "0.1", "note": "世界模型接入前的占位，隧道与终点位置真实，深度体积过程线为模拟"},
            "network_version": cfg["exchange"]["network_version"],
            "scenario": {"name": S["scenario_name"], "return_period_yr": S["return_period_yr"], "rain_depth_mm": 120, "duration_min": total, "tide_m": cfg["model"]["tide_high_m"], "start": "2026-10-01T02:00:00+04:00"},
            "points": pts, "road_impacts": []}


def simulate_fleet(facilities: pd.DataFrame, cfg: dict) -> dict:
    """驻地取民防站（真实位置），限制在隧道群中心 30 km 内，用最远点采样选出分布均匀的若干站；泵按型号轮流分配。"""
    S = cfg["dispatch"]["simulation"]
    cd = facilities[facilities["kind"] == "civil_defense"].dropna(subset=["lon", "lat"]).reset_index(drop=True)
    tun = facilities[facilities["kind"] == "tunnel"].dropna(subset=["lon", "lat"])
    if len(tun):
        c_lon, c_lat = float(tun["lon"].median()), float(tun["lat"].median())
        dkm = np.hypot((cd["lon"] - c_lon) * 111 * np.cos(np.radians(24.4)), (cd["lat"] - c_lat) * 111)
        cd = cd[dkm <= 30].reset_index(drop=True) if (dkm <= 30).sum() >= S["n_depots"] else cd
    cd["name"] = [n if isinstance(n, str) and n.strip() else f"Civil Defence Centre #{i+1}" for i, n in enumerate(cd["name"])]
    xy = np.c_[cd["lon"].values * np.cos(np.radians(24.4)), cd["lat"].values]
    chosen = [int(np.argmin(np.abs(xy[:, 0] - xy[:, 0].mean()) + np.abs(xy[:, 1] - xy[:, 1].mean())))]
    while len(chosen) < min(S["n_depots"], len(cd)):
        d = np.min(np.linalg.norm(xy[:, None, :] - xy[chosen][None, :, :], axis=2), axis=1)
        chosen.append(int(np.argmax(d)))
    models = [
        {"model": "trailer-500", "rate_m3h": 500, "head_m": 12, "hose_m": 100, "setup_min": 20, "crew": 2, "weight_t": 3.5, "vehicle_class": "light"},
        {"model": "truck-1500", "rate_m3h": 1500, "head_m": 15, "hose_m": 150, "setup_min": 30, "crew": 3, "weight_t": 12, "vehicle_class": "heavy"},
        {"model": "heavy-3000", "rate_m3h": 3000, "head_m": 20, "hose_m": 300, "setup_min": 40, "crew": 4, "weight_t": 22, "vehicle_class": "heavy"},
    ]
    depots = [{"depot_id": f"D{j+1:02d}", "name": str(cd.loc[i, "name"] or f"Civil Defence {i}"), "lon": float(cd.loc[i, "lon"]), "lat": float(cd.loc[i, "lat"]), "capacity_units": 8} for j, i in enumerate(chosen)]
    units, k = [], 0
    for m, n in S["units"].items():
        for _ in range(int(n)):
            d = depots[k % len(depots)]
            units.append({"unit_id": f"P-{k+1:03d}", "model": m, "depot_id": d["depot_id"], "status": "available", "lon": d["lon"], "lat": d["lat"]})
            k += 1
    return {"schema": SCHEMA_FLEET, "simulated": True, "as_of": "2026-10-01", "models": models, "depots": depots, "units": units,
            "note": "驻地为真实民防站位置，型号与数量为模拟；客户提供台账后替换 exchange/fleet.json"}
