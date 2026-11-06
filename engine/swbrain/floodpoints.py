"""本系统自己的积水点（swbrain.flood_points/1，model.type = "system"）——第三道防线的输入，替代「只涨不退、命中客户热点 22%」的世界模型输出。

点 = 客户热点清单。每个点在给定重现期下：
- 有管网匹配（150 m 内主干管段，见 tools.hotspot_workbook.hotspots_from_client）的：
  调蓄缺口 = 手册 3.6 梯形过程线对 ≥tc 的各历时取最大的超出体积 ×1.05（4.2 沉沙余量）− 现有塘容；
  过程线：超出入流从 tc 起持续到临界历时，雨停后管网按满管能力 Qcap 退水（体积 ÷ Qcap）。
- 没有管网匹配的（艾因、Dhafra、部分 ADM）：体积 = DTM 灌到溢出的填洼体积（上限代理），3 小时灌满，无管网不退水。
- 深度与面积来自 DTM 洼地：深度按 √(体积/填洼体积) 缩放洼地最大深度；缺口超过洼地容积的记 spill_m3（会漫到邻区）。
- 处置时限 target_drain_min 按客户预案 A 分级目标：极高 120、高 240、中 600、低 600 分钟。
只保留待排体积 ≥ min_volume_m3 的点。"""
from __future__ import annotations
import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import standards, gwm, scenarios

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "out" / "state"
K = 2.77e-3                                   # WA-726-1 3.3.2：Q(m³/s) = 2.77×10⁻³·C·i(mm/h)·A(ha)
DUR = [5, 10, 15, 30, 60, 120, 180, 360, 720, 1440]
SED = 1.05
TARGET_MIN = {"Very High": 120, "High": 240, "Medium": 600, "Low": 2160}   # 客户预案 A 表头 <2h / <4h / <10h / >10h；ADM 993 事件四级 KPI 时限 2 / 4 / 10 / 36 h（2026-09-22 会议），只在 61 mm/24 h 设计强度下成立
TUNNEL_WORDS = ("نفق", "tunnel", "underpass", "Tunnel", "Underpass")
DEFAULTS = {"min_volume_m3": 100.0, "flat_depth_m": 0.10, "proxy_fill_min": 180}


def params(cfg: dict) -> dict:
    p = dict(DEFAULTS); p.update({k: v for k, v in cfg.get("dispatch", {}).get("system_points", {}).items() if k in p}); return p


def available() -> bool:
    return (STATE / "es.parquet").exists() and (STATE / "cs.parquet").exists()


# ---------------------------------------------------------------- 匹配（与重现期无关，可缓存）
def match_hotspots(cfg: dict, es: pd.DataFrame | None = None, cs: pd.DataFrame | None = None) -> pd.DataFrame:
    """客户热点 × 本系统管网（面积、tc、Qcap、塘容、C）× DTM 洼地。run 里把当次的 es/cs 传进来，别读上一轮的 parquet。"""
    from . import hotspots as hsm
    from .tools.hotspot_workbook import hotspots_from_client
    hs_path = ROOT / cfg["hotspots"]["file"]
    hs = hsm.load(hs_path)
    m = hotspots_from_client(hs_path, None, "", radius_m=500.0, es=es, cs=cs).rename(columns={"id": "hotspot_id"})
    # hotspots_from_client 已带 region / 部分地形列；只补它没有的，避免 _x/_y 后缀
    want = ["criticality", "location", "area", "region", "network_available", "root_cause", "planA_pumps", "planA_tankers"]
    add = [c for c in want if c in hs.columns and c not in m.columns]
    m = m.merge(hs[["hotspot_id"] + add], on="hotspot_id", how="left")
    tp = STATE / "hotspot_terrain.parquet"
    tcols = ["dtm_z", "depth_max_m", "pond_volume_m3", "pond_area_m2", "spill_z", "terrain_note"]
    if tp.exists():
        ht = pd.read_parquet(tp)
        need = [c for c in tcols if c not in m.columns and c in ht.columns]
        if need:
            m = m.merge(ht[["hotspot_id"] + need], on="hotspot_id", how="left")
    for c in tcols:
        if c not in m.columns:
            m[c] = np.nan if c != "terrain_note" else None
    m["is_tunnel"] = m["location"].astype(str).apply(lambda s: any(w in s for w in TUNNEL_WORDS)) | (m["class"] == "underpass")
    return m


# ---------------------------------------------------------------- 体积（手册 3.6）
def excess_volume(area_ha: float, c: float, tc_min: float, qcap: float, scen, cfg: dict, area: str | None = None) -> dict:
    """对历时 = tc 及 ≥ tc 的各档 IDF 历时算梯形超出体积，取最大。scen 是情景 key 或 scenarios.get 的结果；area 是热点所在区域（事件情景按区域取雨量）。
    返回 V_m3（未加沉沙）、临界历时、峰值超出流量、临界 Q。"""
    tc = max(float(tc_min), 5.0)
    best = {"V_m3": 0.0, "d_star_min": tc, "excess_m3s": 0.0, "q_star_m3s": 0.0, "q_tc_m3s": 0.0}
    s = scen if isinstance(scen, dict) else scenarios.get(cfg, scen)
    for d in sorted(set([tc] + [float(x) for x in DUR if x >= tc])):
        i = scenarios.intensity(cfg, s, d, area=area)
        q = K * c * i * area_ha
        if d == tc:
            best["q_tc_m3s"] = q
        if q <= qcap:
            continue
        e = q - qcap
        v = e * ((d - tc) + tc * e / q) * 60.0
        if v > best["V_m3"]:
            best.update({"V_m3": v, "d_star_min": d, "excess_m3s": e, "q_star_m3s": q})
    return best


def hydrograph_network(tc: float, d_star: float, excess_m3s: float, q_star: float, v_req: float, qcap: float) -> list[dict]:
    """(0,0) → tc 起超出入流 → d* 达到 v_req → d*+tc 入流归零 → 管网按 Qcap 退水到 0。"""
    tc = max(tc, 5.0); e_h = excess_m3s * 3600.0
    r = (excess_m3s / q_star) if q_star > 0 else 1.0
    rise = tc * r; flat = max(d_star - tc, 0.0)
    v_tc = v_req * (rise / (rise + flat)) if (rise + flat) > 0 else v_req
    pts = [{"t_min": 0, "depth_m": 0.0, "volume_m3": 0, "inflow_m3h": 0},
           {"t_min": round(tc), "depth_m": None, "volume_m3": round(v_tc), "inflow_m3h": round(e_h)},
           {"t_min": round(d_star), "depth_m": None, "volume_m3": round(v_req), "inflow_m3h": round(e_h)},
           {"t_min": round(d_star + tc), "depth_m": None, "volume_m3": round(v_req), "inflow_m3h": 0}]
    if qcap > 1e-6:
        t_end = d_star + tc + v_req / (qcap * 3600.0) * 60.0
        pts.append({"t_min": round(min(t_end, 2880)), "depth_m": 0.0, "volume_m3": 0 if t_end <= 2880 else round(v_req * (1 - (2880 - d_star - tc) / (t_end - d_star - tc))), "inflow_m3h": 0})
    else:
        pts.append({"t_min": 2880, "depth_m": None, "volume_m3": round(v_req), "inflow_m3h": 0})
    return pts


_HYETO_CACHE: dict = {}


def load_hyetographs(cfg: dict) -> dict:
    """out/dmt_model/rain_events.json（python -m swbrain.dmtmodel 生成）：{情景 key: {区域: {dt_min, cum_mm, depth_mm}}}。没有就空。"""
    path = str((cfg.get("scenarios") or {}).get("hyetographs_file") or (ROOT / "out" / "dmt_model" / "rain_events.json"))
    key = (path, os.path.getmtime(path) if os.path.exists(path) else None)
    if key not in _HYETO_CACHE:
        _HYETO_CACHE.clear()
        _HYETO_CACHE[key] = json.loads(open(path, encoding="utf-8").read()) if os.path.exists(path) else {}
    return _HYETO_CACHE[key]


def event_hyetograph(cfg: dict, scen: dict, area: str | None) -> dict | None:
    """事件情景在某区域的累计雨型；该区域没有实测就拿有实测区域里 24 h 雨量最大的那条按本区域 24 h 雨量等比缩放（shape 借用，量按 Parsons 分区值）。"""
    if scen.get("kind") != "event":
        return None
    H = load_hyetographs(cfg).get(scen["key"]) or {}
    if not H:
        return None
    if area in H:
        return {**H[area], "scaled_from": None}
    ref_area = max(H, key=lambda a: H[a]["depth_mm"]); ref = H[ref_area]
    target = float(scenarios.p24(cfg, scen, area=area)) if area is not None else float(scen.get("p24_default_mm") or ref["depth_mm"])
    f = target / float(ref["depth_mm"]) if ref["depth_mm"] else 1.0
    return {"dt_min": ref["dt_min"], "cum_mm": [x * f for x in ref["cum_mm"]], "depth_mm": round(target, 2), "scaled_from": ref_area, "scale": round(f, 3)}


def hydrograph_from_hyetograph(area_ha: float, c: float, tc_min: float, qcap: float, hyeto: dict, out_dt_min: float = 15.0, t_max_min: float = 2880.0) -> dict:
    """逐 dt 的推理公式 + 满管出流水量平衡：入流 Q_k = K·C·A·ī_k（ī 是过去 tc 内的平均雨强，即推理公式的历时=tc），出流 ≤ Q_cap，
    V_{k+1} = max(V_k + (Q_k − Q_cap)·dt, 0)，雨停后继续按 Q_cap 退到 0 或 48 h。返回 15 分钟过程线与峰值。"""
    dt = float(hyeto["dt_min"]); cum = np.asarray(hyeto["cum_mm"], dtype=float)
    inc = np.diff(cum, prepend=0.0); i_step = inc / dt * 60.0                      # mm/h
    w = max(int(round(max(float(tc_min), 5.0) / dt)), 1)
    i_avg = np.convolve(i_step, np.ones(w) / w, mode="full")[:len(i_step)]        # 过去 tc 内平均
    n_rain = len(i_step); n_total = int(t_max_min / dt) + 1
    Q = np.zeros(n_total); Q[:n_rain] = K * c * i_avg * area_ha
    if n_rain < n_total:                                                            # 雨停后 tc 内的尾巴
        tail = np.convolve(inc, np.ones(w) / w, mode="full")[n_rain:n_rain + w] / dt * 60.0
        Q[n_rain:n_rain + len(tail)] = K * c * tail * area_ha
    V = np.zeros(n_total); v = 0.0; t_end = None
    for k in range(n_total):
        v = max(v + (Q[k] - qcap) * dt * 60.0, 0.0); V[k] = v
        if k > 0 and v <= 0 and V[k - 1] > 0 and Q[k] <= qcap and k * dt >= n_rain * dt:
            t_end = k * dt
    kp = int(np.argmax(V)); v_peak = float(V[kp])
    step = max(int(round(out_dt_min / dt)), 1)
    idx = list(range(0, n_total, step))
    if t_end is not None:
        idx = [i for i in idx if i * dt <= t_end + out_dt_min] + ([int(t_end / dt)] if int(t_end / dt) not in idx else [])
    pts = [{"t_min": round(i * dt), "depth_m": None, "volume_m3": round(float(V[i])), "inflow_m3h": round(float(Q[i]) * 3600.0)} for i in sorted(set(idx))]
    pts[0]["depth_m"] = 0.0
    return {"hydrograph": pts, "v_peak_m3": v_peak, "t_peak_min": kp * dt, "t_end_min": t_end, "q_peak_m3s": float(Q.max()), "excess_peak_m3s": float(max(Q.max() - qcap, 0.0))}


def hydrograph_proxy(v: float, fill_min: float) -> list[dict]:
    return [{"t_min": 0, "depth_m": 0.0, "volume_m3": 0, "inflow_m3h": 0},
            {"t_min": round(fill_min), "depth_m": None, "volume_m3": round(v), "inflow_m3h": round(v / (fill_min / 60.0))},
            {"t_min": 2880, "depth_m": None, "volume_m3": round(v), "inflow_m3h": 0}]


def _depth_area(v: float, r: pd.Series, flat_depth: float) -> tuple[float, float, float]:
    """按 DTM 洼地把体积换成深度与面积；返回 (depth_m, area_m2, spill_m3)。"""
    pv, pa, dm = float(r.get("pond_volume_m3") or 0), float(r.get("pond_area_m2") or 0), float(r.get("depth_max_m") or 0)
    if pv > 0 and dm >= 0.05 and pa > 0:
        frac = min(v / pv, 1.0)
        return round(dm * np.sqrt(frac), 2), round(pa * frac, 0), round(max(v - pv, 0.0), 0)
    return flat_depth, round(v / flat_depth, 0), 0.0


def _fill_depths(h: list[dict], depth: float, v_peak: float) -> list[dict]:
    for p in h:
        if p["depth_m"] is None:
            p["depth_m"] = round(depth * np.sqrt(p["volume_m3"] / v_peak), 2) if v_peak > 0 else 0.0
    return h


# ---------------------------------------------------------------- 构建契约
def build(cfg: dict, rp, matched: pd.DataFrame | None = None) -> dict:
    """rp：情景 key（"10"、"10cc"、"2024-04"…），兼容整数重现期。"""
    P = params(cfg)
    S = scenarios.get(cfg, rp); key = S["key"]; lab, lab_en = S["label"], S["label_en"]
    m = matched if matched is not None else match_hotspots(cfg)
    pts = []; n_net = n_proxy = 0; v_total = 0.0; spill_total = 0.0; t_peak = []
    for _, r in m.iterrows():
        crit = r.get("criticality"); target = TARGET_MIN.get(crit, 600)
        has_net = pd.notna(r.get("area_ha")) and pd.notna(r.get("qcap")) and pd.notna(r.get("tc_min"))
        if has_net:
            c = float(r["c"]) if pd.notna(r.get("c")) else float(cfg["model"]["runoff_c"])
            hy = event_hyetograph(cfg, S, r.get("area"))
            if hy is not None:                                    # 实测事件：按 DMT 雨量计的 5 分钟雨型逐步做水量平衡
                hg = hydrograph_from_hyetograph(float(r["area_ha"]), c, float(r["tc_min"]), float(r["qcap"]), hy)
                v_req = hg["v_peak_m3"] * SED
                v = max(v_req - float(r.get("pond_m3") or 0.0), 0.0)
                if v < P["min_volume_m3"]:
                    continue
                h = hg["hydrograph"]
                if v_req > 0:                                     # 减塘容后按比例缩体积
                    fac = v / v_req; h = [{**q, "volume_m3": round(q["volume_m3"] * fac)} for q in h]
                src = "network_gap"; n_net += 1; t_peak.append(hg["t_peak_min"])
                sc_txt = f"（{hy['scaled_from']} 雨型按本区 24 h {hy['depth_mm']} mm 缩放）" if hy.get("scaled_from") else f"（DMT 雨量计实测 {hy['depth_mm']} mm）"
                note = (f"{lab}：按实测雨型逐 5 分钟水量平衡{sc_txt}，匹配管段能力 {float(r['qcap']):.3f} m³/s，上游 {float(r['area_ha']):.1f} ha（C {c:.2f}），"
                        f"峰值在 {hg['t_peak_min']:.0f} min，调蓄缺口 {v:,.0f} m³（含 5% 沉沙，减塘容 {float(r.get('pond_m3') or 0):,.0f}）")
                note_en = (f"{lab_en}: 5-min water balance on the observed hyetograph{' (shape from ' + hy['scaled_from'] + ' scaled to ' + str(hy['depth_mm']) + ' mm)' if hy.get('scaled_from') else ' (DMT gauge, ' + str(hy['depth_mm']) + ' mm)'}, "
                           f"matched reach capacity {float(r['qcap']):.3f} m³/s, upstream {float(r['area_ha']):.1f} ha (C {c:.2f}), peak at {hg['t_peak_min']:.0f} min, "
                           f"storage shortfall {v:,.0f} m³ (incl. 5% sediment, less pond {float(r.get('pond_m3') or 0):,.0f})")
                ex = None
            else:
                ex = excess_volume(float(r["area_ha"]), c, float(r["tc_min"]), float(r["qcap"]), S, cfg, area=r.get("area"))
                v_req = ex["V_m3"] * SED
                v = max(v_req - float(r.get("pond_m3") or 0.0), 0.0)
                if v < P["min_volume_m3"]:
                    continue
                h = hydrograph_network(float(r["tc_min"]), ex["d_star_min"], ex["excess_m3s"], ex["q_star_m3s"], v, float(r["qcap"]))
                src = "network_gap"; n_net += 1; t_peak.append(ex["d_star_min"] + max(float(r["tc_min"]), 5.0))
                note = (f"{lab}：匹配管段能力 {float(r['qcap']):.3f} m³/s，上游 {float(r['area_ha']):.1f} ha（C {c:.2f}），临界历时 {ex['d_star_min']:.0f} min，"
                        f"调蓄缺口 {v:,.0f} m³（含 5% 沉沙，减塘容 {float(r.get('pond_m3') or 0):,.0f}）")
                note_en = (f"{lab_en}: matched reach capacity {float(r['qcap']):.3f} m³/s, upstream {float(r['area_ha']):.1f} ha (C {c:.2f}), critical duration {ex['d_star_min']:.0f} min, "
                           f"storage shortfall {v:,.0f} m³ (incl. 5% sediment, less pond {float(r.get('pond_m3') or 0):,.0f})")
        else:
            v = float(r.get("pond_volume_m3") or 0.0)
            if not np.isfinite(v) or v < P["min_volume_m3"]:
                continue
            h = hydrograph_proxy(v, P["proxy_fill_min"]); src = "dtm_proxy"; n_proxy += 1; t_peak.append(P["proxy_fill_min"])
            note = f"无管网匹配：按 5 m DTM 灌到溢出的填洼体积 {v:,.0f} m³（上限代理），{P['proxy_fill_min']/60:.0f} 小时灌满，无管网不退水"
            note_en = f"No network match: DTM fill-to-spill volume {v:,.0f} m³ (upper-bound proxy), fills in {P['proxy_fill_min']/60:.0f} h, no drainage"
        depth, area, spill = _depth_area(v, r, P["flat_depth_m"])
        h = _fill_depths(h, depth, v)
        v_total += v; spill_total += spill
        pts.append({"fp_id": str(r["hotspot_id"]), "geometry": {"type": "Point", "coordinates": [float(r["lon"]), float(r["lat"])]}, "lon": float(r["lon"]), "lat": float(r["lat"]),
                    "area_m2": area, "max_depth_m": depth, "volume_m3": round(v, 0), "duration_min": int(min(h[-1]["t_min"], 2880)), "hydrograph": h,
                    "land_use": "tunnel" if bool(r.get("is_tunnel")) else "road", "note": note, "note_en": note_en,
                    "source": src, "client_hotspot_id": str(r["hotspot_id"]), "client_criticality": crit, "target_drain_min": int(target),
                    "spill_m3": spill, "region": r.get("region"), "location": r.get("location")})
    sug = int(round(float(np.median(t_peak)) + 30)) if t_peak else int(cfg["dispatch"]["decision_time_min"])
    stats = {"n_points": len(pts), "n_network_gap": n_net, "n_dtm_proxy": n_proxy, "n_hotspots": int(len(m)), "volume_total_m3": round(v_total, 0), "spill_total_m3": round(spill_total, 0),
             "by_region": {k: int(v) for k, v in pd.Series([p["region"] for p in pts]).value_counts().items()} if pts else {},
             "targets_min": TARGET_MIN, "min_volume_m3": P["min_volume_m3"],
             "hydrograph_basis": ("observed_hyetograph" if (S["kind"] == "event" and load_hyetographs(cfg).get(key)) else "manual_3.6_trapezoid")}
    return {"schema": "swbrain.flood_points/1", "run_id": f"system-{key}-{time.strftime('%Y%m%dT%H%M%S')}",
            "model": {"name": "会思考的排水系统：管网缺口 + DTM 洼地 + 客户热点", "name_en": "Thinking drainage system: network shortfall + DTM depressions + client hotspots", "type": "system", "version": "1"},
            "network_version": None,
            "scenario": {"name": f"{lab}（本系统积水点）", "name_en": f"{lab_en} (system flood points)", "return_period_yr": int(S["base_rp"]), "scenario_key": key, "scenario_kind": S["kind"],
                         "rain_depth_mm": S.get("p24_mm"), "rain_factor_default": S.get("factor"), "rain_source": S.get("source"),
                         "duration_min": 1440, "tide_m": cfg["model"]["tide_high_m"], "event_id": (key if S["kind"] == "event" else None), "start": None, "suggested_decision_time_min": sug},
            "points": pts, "stats": stats}


def write(fp: dict, cfg: dict) -> Path:
    d = ROOT / cfg["exchange"]["dir"] / "predictions"; d.mkdir(parents=True, exist_ok=True)
    p = d / f"flood_points.system-{fp['scenario'].get('scenario_key', fp['scenario'].get('return_period_yr'))}.json"
    p.write_text(json.dumps(fp, ensure_ascii=False), encoding="utf-8")
    return p


def main(argv=None):
    import tomllib
    from . import contracts
    ap = argparse.ArgumentParser(description="本系统积水点：客户热点 × 管网缺口 × DTM 洼地")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--rp", default="10", help="情景 key：10 / 10cc / 2024-04 …")
    a = ap.parse_args(argv)
    cfg = tomllib.loads(Path(a.config).read_text(encoding="utf-8"))
    t0 = time.time(); m = match_hotspots(cfg); t1 = time.time()
    fp = build(cfg, a.rp, m); p = write(fp, cfg)
    contracts.load_flood_points(p)                      # 走一遍契约校验
    print(json.dumps(fp["stats"], ensure_ascii=False, indent=2))
    print(f"匹配 {t1-t0:.1f}s，构建 {time.time()-t1:.1f}s → {p}")
    df = pd.DataFrame(fp["points"])
    print(df.sort_values("volume_m3", ascending=False).head(10)[["fp_id", "client_criticality", "source", "volume_m3", "max_depth_m", "area_m2", "spill_m3", "target_drain_min", "duration_min"]].to_string(index=False))


if __name__ == "__main__":
    main()
