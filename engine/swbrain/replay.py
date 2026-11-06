"""退水重演：给一个积水点的过程线加上派到的泵，按 5 分钟步长重算水量平衡，比「无泵」与「有泵」的退干时刻、峰值、积水时长。

基线过程线 V(t) 拆成两支：净入流 e(t) = max(dV/dt, 0)（涨水段）与自然去水 n(t) = max(−dV/dt, 0)（退水段），
两支相减恰好重放出基线，所以「无泵」曲线与页面上的过程线一致；世界模型那种只涨不退的过程线 n≡0（只有泵能排），本系统的点雨停后按匹配管段能力退水。
泵：到位后按额定排量抽；水抽干了就只抽入流；水量不为负。
手动调度用 "8in:2@250,6in:1@300" 这样的串：型号:台数@到位分钟（事件开始后）。"""
from __future__ import annotations
import numpy as np
import pandas as pd

DT_MIN = 5.0
MIN_VOL_M3 = 50.0        # 低于这个量视为退干（与分诊「无需处置 <50 m³」一致）
T_MAX_MIN = 2880.0


def _grid(hyd: list[dict], t_end: float, dt: float):
    ts = np.array([float(h["t_min"]) for h in hyd]); vs = np.array([float(h["volume_m3"]) for h in hyd]); es = np.array([float(h.get("inflow_m3h", 0.0)) for h in hyd])
    o = np.argsort(ts); ts, vs, es = ts[o], vs[o], es[o]
    t = np.arange(0.0, t_end + dt / 2, dt)
    V = np.interp(t, ts, vs, left=0.0, right=float(vs[-1]))
    e = np.interp(t, ts, es, left=0.0, right=0.0)
    return t, V, e


def decompose(t: np.ndarray, V: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """基线体积曲线 → (净入流 e, 自然去水 n)，单位 m³/h；e − n 积分回去就是基线。"""
    dV = np.diff(V, prepend=V[:1]) / max(float(t[1] - t[0]) if len(t) > 1 else DT_MIN, 1e-9) * 60.0
    return np.maximum(dV, 0.0), np.maximum(-dV, 0.0)


def natural_removal(t: np.ndarray, V: np.ndarray, e: np.ndarray | None = None) -> np.ndarray:
    return decompose(t, V)[1]


def simulate(t: np.ndarray, e: np.ndarray, n: np.ndarray, pumps: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """返回 (水量序列, 实际抽水序列 m³/h)。"""
    dt_h = float(t[1] - t[0]) / 60.0 if len(t) > 1 else DT_MIN / 60.0
    V = np.zeros_like(t); P = np.zeros_like(t); v = 0.0
    arr = np.array([float(p["arrive_min"]) for p in pumps]); rate = np.array([float(p["rate_m3h"]) for p in pumps])
    # until_min：这台泵被抢占释放、调走的时刻（没有就是一直抽到视界）。
    # 2026-09-24 之前不认这个字段，被释放的泵在原点仍算抽到底，同一台泵同时出现在 A 点的水量和 B 点的调度单里。
    until = np.array([(float(p["until_min"]) if p.get("until_min") is not None and np.isfinite(float(p["until_min"])) else np.inf) for p in pumps])
    # 现场排量按时间预先算好：原来在时间循环里每一步都对泵做一次布尔筛选求和，
    # 而这条曲线只取决于到达 / 释放时刻，与水量无关（2026-09-24：这是整个求解里最热的一段）
    if len(pumps):
        cap_t = ((arr[None, :] <= t[:, None]) & (t[:, None] < until[None, :])) @ rate
    else:
        cap_t = np.zeros_like(t)
    for i in range(len(t)):
        cap = float(cap_t[i])
        avail = v / dt_h + e[i]                       # 这一步最多能拿走的
        take_n = min(n[i], avail); take_p = min(cap, max(avail - take_n, 0.0))
        v = max(v + (e[i] - take_n - take_p) * dt_h, 0.0)
        V[i] = v; P[i] = take_p
    return V, P


def kpi(t: np.ndarray, V: np.ndarray, min_vol: float = MIN_VOL_M3) -> dict:
    dt_h = float(t[1] - t[0]) / 60.0 if len(t) > 1 else DT_MIN / 60.0
    wet = V > min_vol
    ip = int(np.argmax(V)) if len(V) else 0
    after = np.nonzero(~wet[ip:])[0]
    t_empty = float(t[ip + after[0]]) if len(after) and V[ip] > min_vol else (0.0 if V[ip] <= min_vol else None)
    return {"peak_m3": round(float(V.max()), 0) if len(V) else 0.0, "t_peak_min": float(t[ip]) if len(t) else 0.0,
            "t_empty_min": t_empty, "flood_h": round(float(wet.sum() * dt_h), 2), "vol_hours_m3h": round(float((V * dt_h).sum()), 0)}


def replay(hyd: list[dict], pumps: list[dict], dt: float = DT_MIN, min_vol: float = MIN_VOL_M3, max_depth_m: float | None = None, t_end: float | None = None, series: bool = True) -> dict:
    """series=False 只算 kpi，不把六条序列逐元素 round 成 Python 列表。
    枚举处置包时一个点要算几十遍，只读 kpi——那几条列表是整个求解里最大的一笔无用功（2026-09-24）。"""
    if not hyd:
        return {"error": "无过程线"}
    last = max(float(h["t_min"]) for h in hyd)
    te = float(t_end) if t_end else max(last, max([float(p["arrive_min"]) for p in pumps], default=0.0) + 60.0)
    te = min(max(te, 60.0), T_MAX_MIN)
    t, Vb, e_raw = _grid(hyd, te, dt)
    e, n = decompose(t, Vb)
    Vb2, _ = simulate(t, e, n, [])               # 用同一台模拟器重放基线，保证可比
    Vp, P = simulate(t, e, n, pumps)
    kb, kp = kpi(t, Vb2, min_vol), kpi(t, Vp, min_vol)
    vref = max(float(Vb2.max()), 1e-9)
    depth = (lambda V: [round(float(max_depth_m) * float(np.sqrt(max(v, 0.0) / vref)), 3) for v in V]) if max_depth_m else (lambda V: None)
    # 省下多少分钟。基线到视界都没退干、派泵后退干了，是泵最有价值的那种情况：给「至少省了多少」而不是 None
    # （2026-09-23 修：原来两个分支都返回 None，下游按 0 处理，报告里会输出「那台泵并没有改变退干时刻」，说反了）
    tb, tp = kb["t_empty_min"], kp["t_empty_min"]
    if tb is not None and tp is not None:
        saved_min, saved_lb = tb - tp, False
    elif tb is None and tp is not None:
        saved_min, saved_lb = float(te) - float(tp), True
    else:
        saved_min, saved_lb = None, False
    kpis = {"base": kb, "pump": kp, "saved_min": (round(saved_min, 0) if saved_min is not None else None),
            "saved_is_lower_bound": saved_lb,
            "base_never_empties": kb["t_empty_min"] is None, "pump_never_empties": kp["t_empty_min"] is None,
            "flood_h_saved": round(kb["flood_h"] - kp["flood_h"], 2), "vol_hours_saved": round(kb["vol_hours_m3h"] - kp["vol_hours_m3h"], 0),
            "pumped_total_m3": round(float((P * (dt / 60.0)).sum()), 0)}
    if not series:
        return {"kpi": kpis, "pumps": pumps, "dt_min": dt, "min_vol_m3": min_vol}
    return {"t_min": [float(x) for x in t], "base_m3": [round(float(x), 0) for x in Vb2], "pump_m3": [round(float(x), 0) for x in Vp], "pumped_m3h": [round(float(x), 0) for x in P],
            "base_depth_m": depth(Vb2), "pump_depth_m": depth(Vp), "inflow_m3h": [round(float(x), 0) for x in e], "natural_m3h": [round(float(x), 0) for x in n], "inflow_stated_m3h": [round(float(x), 0) for x in e_raw],
            "kpi": kpis,
            "pumps": pumps, "dt_min": dt, "min_vol_m3": min_vol}


# ---------------------------------------------------------------- 与调度单、手动输入对接
def pumps_from_plan(rows: pd.DataFrame | list[dict]) -> list[dict]:
    """一个积水点的调度单行 → 泵列表。每台从 arrive_min + 架设 开始抽；架设时间 = pumping_from_min − 最晚到达。"""
    df = pd.DataFrame(rows) if not isinstance(rows, pd.DataFrame) else rows
    if not len(df):
        return []
    setup = max(float(df["pumping_from_min"].max()) - float(df["arrive_min"].max()), 0.0)

    def _until(r):
        v = r.get("released_min")
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if np.isfinite(v) else None

    return [{"arrive_min": float(r["arrive_min"]) + setup, "rate_m3h": float(r["rate_m3h"]), "until_min": _until(r),
             "label": f"{r.get('unit_id', '')} {r.get('model', '')}".strip(), "from": r.get("from")} for _, r in df.iterrows()]


def parse_manual(spec: str, models: list[dict]) -> list[dict]:
    """"8in:2@250,6in:1@300" → 泵列表。型号按名字包含匹配（8in / diesel-8in / 8 都行）。"""
    out = []
    for part in [p.strip() for p in str(spec).split(",") if p.strip()]:
        try:
            mc, at = part.split("@"); m, cnt = mc.split(":")
        except ValueError:
            raise ValueError(f"格式应为 型号:台数@到位分钟，收到 {part!r}")
        key = m.strip().lower()
        hit = [x for x in models if key == str(x["model"]).lower() or key in str(x["model"]).lower() or (key.rstrip("in") + "in") in str(x["model"]).lower()]
        if not hit:
            raise ValueError(f"没有型号 {m!r}，可选 {[x['model'] for x in models]}")
        for k in range(int(cnt)):
            out.append({"arrive_min": float(at), "rate_m3h": float(hit[0]["rate_m3h"]), "label": f"{hit[0]['model']} #{k + 1}", "from": "manual"})
    return out


def replay_plan(pts: pd.DataFrame, plan: pd.DataFrame, dt: float = DT_MIN, min_vol: float = MIN_VOL_M3, timeline_dt: float | None = None) -> tuple[list[dict], dict]:
    """按调度单给每个处置点重演；返回 (每点一行, 汇总)。汇总同时给「处置点」与「全部点」两个口径。
    timeline_dt 给了就在汇总里附 timeline：全部积水点在统一时间轴上的无泵 / 有泵水量（页面地图时间轴用）。"""
    rows = []; base_all = pump_all = 0.0; vb_all = vp_all = 0.0
    served = set(plan["fp_id"]) if len(plan) else set()
    t_b, t_p = [], []
    tb_all, tp_all = [], []; never_b = never_p = 0
    tl = {}
    for _, p in pts.iterrows():
        hyd = p.get("hydrograph") or []
        if not isinstance(hyd, list) or not hyd:
            continue
        pumps = pumps_from_plan(plan[plan["fp_id"] == p["fp_id"]]) if p["fp_id"] in served else []
        r = replay(hyd, pumps, dt, min_vol)
        kb, kp = r["kpi"]["base"], r["kpi"]["pump"]
        if timeline_dt and kb["peak_m3"] > min_vol:
            tl[str(p["fp_id"])] = (np.asarray(r["t_min"]), np.asarray(r["base_m3"]), (np.asarray(r["pump_m3"]) if pumps else None))
        base_all += kb["flood_h"]; pump_all += kp["flood_h"]; vb_all += kb["vol_hours_m3h"]; vp_all += kp["vol_hours_m3h"]
        if kb["peak_m3"] > min_vol:
            if kb["t_empty_min"] is None: never_b += 1
            else: tb_all.append(kb["t_empty_min"])
            if kp["t_empty_min"] is None: never_p += 1
            else: tp_all.append(kp["t_empty_min"])
        if pumps:
            rows.append({"fp_id": p["fp_id"], "replay_base_end_min": kb["t_empty_min"], "replay_pump_end_min": kp["t_empty_min"], "replay_saved_min": r["kpi"]["saved_min"],
                         "replay_base_flood_h": kb["flood_h"], "replay_pump_flood_h": kp["flood_h"], "replay_pumped_m3": r["kpi"]["pumped_total_m3"]})
            if kb["t_empty_min"] is not None: t_b.append(kb["t_empty_min"])
            if kp["t_empty_min"] is not None: t_p.append(kp["t_empty_min"])
    sb = sum(x["replay_base_flood_h"] for x in rows); sp = sum(x["replay_pump_flood_h"] for x in rows)
    summary = {"served_points": len(rows), "served_flood_h_base": round(sb, 1), "served_flood_h_pump": round(sp, 1), "served_saved_pct": (round((1 - sp / sb) * 100, 1) if sb > 0 else None),
               "all_points": int(len(pts)), "all_flood_h_base": round(base_all, 1), "all_flood_h_pump": round(pump_all, 1), "all_saved_pct": (round((1 - pump_all / base_all) * 100, 1) if base_all > 0 else None),
               "all_vol_hours_base": round(vb_all, 0), "all_vol_hours_pump": round(vp_all, 0),
               "served_t_empty_median_base_min": (float(np.median(t_b)) if t_b else None), "served_t_empty_median_pump_min": (float(np.median(t_p)) if t_p else None),
               "pumped_total_m3": round(sum(x["replay_pumped_m3"] for x in rows), 0), "dt_min": dt, "min_vol_m3": min_vol,
               # 与 Parsons 同口径：多数点退水时间（中位）与系统排空时间（最后一点退干），全部积水点
               "all_t_empty_median_base_min": (float(np.median(tb_all)) if tb_all else None), "all_t_empty_median_pump_min": (float(np.median(tp_all)) if tp_all else None),
               "system_empty_base_min": (float(np.max(tb_all)) if tb_all else None), "system_empty_pump_min": (float(np.max(tp_all)) if tp_all else None),
               "never_empties_base": int(never_b), "never_empties_pump": int(never_p)}
    if timeline_dt:
        summary["timeline"] = _timeline(tl, float(timeline_dt))
    return rows, summary


def _timeline(tl: dict, dt: float) -> dict:
    """各点自己网格上的曲线 → 统一时间轴（dt 分钟一格，到最长的那条为止），整数 m³；没派泵的点 pump 为 None。"""
    if not tl:
        return {"dt_min": dt, "t_min": [], "points": {}}
    t_end = max(float(v[0][-1]) for v in tl.values())
    t = np.arange(0.0, t_end + dt / 2, dt)
    out = {}
    for k, (tt, vb, vp) in tl.items():
        out[k] = {"base": [int(round(x)) for x in np.interp(t, tt, vb, left=0.0, right=float(vb[-1]))],
                  "pump": ([int(round(x)) for x in np.interp(t, tt, vp, left=0.0, right=float(vp[-1]))] if vp is not None else None)}
    return {"dt_min": dt, "t_min": [float(x) for x in t], "points": out}
