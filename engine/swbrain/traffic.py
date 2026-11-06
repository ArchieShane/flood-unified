"""路况：把 Google Routes API 的带路况行程时间套到本地路网行程矩阵上；没有密钥时用明确标注的模拟时段曲线。

数据源（[traffic].provider）
  google     Routes API computeRouteMatrix（routingPreference=TRAFFIC_AWARE，departureTime 可以是未来时刻 → 预测路况）。
             密钥只从环境变量读（api_key_env，缺省 GOOGLE_MAPS_API_KEY），永不写进仓库。按元素（起点×终点）计费，所以：
               · 系数层：每个驻地抽样 sample_per_depot 个目的地（按本地行程分层：近 / 中 / 远），系数 = Σ带路况时长 / Σ自由流时长；
               · 精算层：调度单里实际出现的 驻地→点 对（≤ refine_pairs_max）逐对取 Google 分钟数，报出与模型的偏差。
  simulated  阿布扎比工作日 / 周末时段曲线（当地时间 UTC+4）× 雨天加成，全驻地同一系数；结果标 simulated。
  none       系数 = 1。

套用方式：驻地→点 的重 / 轻型矩阵按驻地系数逐行放大；点→点 矩阵按全网均值放大（泵在点之间转场）。
Google 没有货车路径，重型车的限速与限重桥仍由本地模型负责（系数只表达拥堵）。
"""
from __future__ import annotations
import datetime as _dt
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
FIELD_MASK = "originIndex,destinationIndex,duration,staticDuration,distanceMeters,condition,status"
DEFAULTS = {"provider": "simulated", "api_key_env": "GOOGLE_MAPS_API_KEY", "sample_per_depot": 12, "refine_pairs_max": 300, "cache_ttl_min": 15, "cache_file": "out/traffic_cache.json",
            "routing_preference": "TRAFFIC_AWARE", "traffic_model": "BEST_GUESS", "region_code": "AE", "timeout_s": 25, "factor_min": 0.8, "factor_max": 3.0, "departure": "now",
            "auto_refresh": True, "apply_to_point_to_point": True, "tz_offset_h": 4,
            "per_window": True, "windows_ahead": 8, "window_min": 60, "per_window_sample_per_depot": 6}
# 阿布扎比时段系数（相对自由流；模拟值，来源：常见城市拥堵曲线 + 阿布扎比早晚高峰 7–9 / 17–19；周五周六为周末）
SIM_WEEKDAY = {0: 0.85, 1: 0.82, 2: 0.80, 3: 0.80, 4: 0.82, 5: 0.90, 6: 1.10, 7: 1.40, 8: 1.45, 9: 1.25, 10: 1.10, 11: 1.08, 12: 1.12, 13: 1.15, 14: 1.12, 15: 1.15, 16: 1.30, 17: 1.45, 18: 1.45, 19: 1.30, 20: 1.15, 21: 1.05, 22: 0.95, 23: 0.90}
SIM_WEEKEND = {h: (0.82 if h < 9 else 1.05 if h < 12 else 1.20 if h < 21 else 0.95) for h in range(24)}
SIM_DEFAULTS = {"rain_uplift": 1.15, "weekend_days": [4, 5]}   # Python weekday(): 4 = 周五、5 = 周六


def params(cfg: dict) -> dict:
    P = dict(DEFAULTS); user = dict((cfg or {}).get("traffic") or {})
    sim = {**SIM_DEFAULTS, **(user.pop("simulated", None) or {})}
    P.update(user); P["simulated"] = sim
    return P


def api_key(P: dict) -> str | None:
    from . import envkeys
    return envkeys.get(str(P.get("api_key_env") or "GOOGLE_MAPS_API_KEY"))            # 进程环境 → 用户级 / 机器级注册表（setx 之后没重开的终端）


def provider(cfg_or_P: dict) -> str:
    P = cfg_or_P if "api_key_env" in cfg_or_P else params(cfg_or_P)
    prov = str(P.get("provider") or "simulated").lower()
    if prov == "google" and not api_key(P):
        return "simulated"                                              # 配了 google 但没密钥：退回模拟并在 meta 里说明
    return prov


# ---------------------------------------------------------------- 出发时刻
def parse_departure(dep, t_min: float = 0.0, P: dict | None = None) -> _dt.datetime:
    """dep: None / "now" → 现在 + t_min 分钟（事件按「现在开始」算）；ISO 字串（带或不带时区，不带按当地 UTC+4）→ 该时刻 + t_min。返回 UTC。"""
    tz = int((P or {}).get("tz_offset_h", 4))
    if dep is None or str(dep).strip().lower() in ("", "now"):
        base = _dt.datetime.now(_dt.timezone.utc)
    else:
        s = str(dep).strip().replace("Z", "+00:00")
        d = _dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone(_dt.timedelta(hours=tz)))
        base = d.astimezone(_dt.timezone.utc)
    out = base + _dt.timedelta(minutes=float(t_min or 0.0))
    now = _dt.datetime.now(_dt.timezone.utc)
    if out < now + _dt.timedelta(seconds=30):                          # Google 不接受过去的出发时刻
        out = now + _dt.timedelta(seconds=60)
    return out


def local_hour(d_utc: _dt.datetime, P: dict) -> tuple[int, int]:
    loc = d_utc.astimezone(_dt.timezone(_dt.timedelta(hours=int(P.get("tz_offset_h", 4)))))
    return loc.hour, loc.weekday()


def simulated_factor(d_utc: _dt.datetime, P: dict, raining: bool = True) -> float:
    h, wd = local_hour(d_utc, P)
    sim = P["simulated"]
    curve = SIM_WEEKEND if wd in set(int(x) for x in sim.get("weekend_days", [4, 5])) else SIM_WEEKDAY
    f = float(curve.get(h, 1.0)) * (float(sim.get("rain_uplift", 1.15)) if raining else 1.0)
    return float(min(max(f, float(P["factor_min"])), float(P["factor_max"])))


# ---------------------------------------------------------------- Google Routes：computeRouteMatrix
def _post(body: dict, key: str, timeout_s: float) -> list:
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        txt = r.read().decode("utf-8")
    data = json.loads(txt) if txt.strip() else []
    return data if isinstance(data, list) else [data]


def _sec(s) -> float | None:
    if s is None:
        return None
    try:
        return float(str(s).rstrip("s"))
    except ValueError:
        return None


def route_matrix(origins, destinations, departure_utc: _dt.datetime, P: dict, key: str, post=None) -> list[dict]:
    """origins / destinations: [(lon, lat), …]。返回元素列表 {oi, di, duration_s, static_s, dist_m, condition}。按 Routes API 的元素上限分块（TRAFFIC_AWARE 625、OPTIMAL 100）。"""
    post = post or _post
    pref = str(P.get("routing_preference") or "TRAFFIC_AWARE").upper()
    limit = 100 if pref == "TRAFFIC_AWARE_OPTIMAL" else 625
    out = []
    n_o, n_d = len(origins), len(destinations)
    if not n_o or not n_d:
        return out
    per_chunk_d = max(1, min(n_d, limit // 1))
    o_per = max(1, limit // per_chunk_d)
    # 分块：先切目的地块（≤ limit），再切起点块使 o × d ≤ limit
    d_chunks = [list(range(i, min(n_d, i + per_chunk_d))) for i in range(0, n_d, per_chunk_d)]
    for dch in d_chunks:
        o_per = max(1, limit // len(dch))
        for oi0 in range(0, n_o, o_per):
            och = list(range(oi0, min(n_o, oi0 + o_per)))
            body = {"origins": [{"waypoint": {"location": {"latLng": {"latitude": float(origins[i][1]), "longitude": float(origins[i][0])}}}} for i in och],
                    "destinations": [{"waypoint": {"location": {"latLng": {"latitude": float(destinations[j][1]), "longitude": float(destinations[j][0])}}}} for j in dch],
                    "travelMode": "DRIVE", "routingPreference": pref, "departureTime": departure_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "regionCode": str(P.get("region_code") or "AE"), "units": "METRIC"}
            if pref == "TRAFFIC_AWARE_OPTIMAL":
                body["trafficModel"] = str(P.get("traffic_model") or "BEST_GUESS")
            for el in post(body, key, float(P.get("timeout_s", 25))):
                if not isinstance(el, dict):
                    continue
                cond = el.get("condition") or ("ROUTE_EXISTS" if el.get("duration") else "ROUTE_NOT_FOUND")
                out.append({"oi": och[int(el.get("originIndex", 0))], "di": dch[int(el.get("destinationIndex", 0))], "duration_s": _sec(el.get("duration")), "static_s": _sec(el.get("staticDuration")),
                            "dist_m": (float(el["distanceMeters"]) if el.get("distanceMeters") is not None else None), "condition": cond})
    return out


# ---------------------------------------------------------------- 缓存：同一对、同一出发时段（15 min 桶）在 TTL 内复用
class Cache:
    def __init__(self, path: Path | None, ttl_min: float):
        self.path = path; self.ttl = float(ttl_min) * 60.0; self.d = {}
        if path is not None and path.exists():
            try:
                self.d = json.loads(path.read_text(encoding="utf-8")) or {}
            except Exception:
                self.d = {}

    @staticmethod
    def key(o, d, dep_utc: _dt.datetime) -> str:
        b = int(dep_utc.timestamp() // 900)
        return f"{float(o[0]):.4f},{float(o[1]):.4f}|{float(d[0]):.4f},{float(d[1]):.4f}|{b}"

    def get(self, k: str):
        v = self.d.get(k)
        if v and time.time() - float(v.get("at", 0)) <= self.ttl:
            return v
        return None

    def put(self, k: str, v: dict):
        self.d[k] = {**v, "at": time.time()}

    def save(self):
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            keep = {k: v for k, v in self.d.items() if time.time() - float(v.get("at", 0)) <= max(self.ttl, 3600 * 6)}
            self.path.write_text(json.dumps(keep), encoding="utf-8")
        except Exception:
            pass


def fetch_pairs(pairs: list[tuple[tuple, tuple]], dep_utc: _dt.datetime, P: dict, key: str, cache: Cache | None = None, post=None) -> tuple[dict, int]:
    """pairs: [((olon, olat), (dlon, dlat)), …] → {pair_index: element}；先查缓存，剩下的按起点分组请求。返回 (结果, 实际请求的元素数)。"""
    res = {}; todo = []
    for i, (o, d) in enumerate(pairs):
        hit = cache.get(Cache.key(o, d, dep_utc)) if cache is not None else None
        if hit is not None:
            res[i] = hit
        else:
            todo.append(i)
    n_req = 0
    if todo:
        by_o = {}
        for i in todo:
            by_o.setdefault(tuple(round(float(x), 6) for x in pairs[i][0]), []).append(i)
        for o, idxs in by_o.items():
            dests = [pairs[i][1] for i in idxs]
            els = route_matrix([o], dests, dep_utc, P, key, post=post); n_req += len(dests)
            for el in els:
                i = idxs[el["di"]]
                v = {k: el[k] for k in ("duration_s", "static_s", "dist_m", "condition")}
                res[i] = v
                if cache is not None:
                    cache.put(Cache.key(pairs[i][0], pairs[i][1], dep_utc), v)
        if cache is not None:
            cache.save()
    return res, n_req


# ---------------------------------------------------------------- 系数层：每个驻地抽样目的地 → 驻地系数
def sample_destinations(T_row: np.ndarray, priority: np.ndarray, k: int) -> list[int]:
    """按本地行程分层抽样：可达点按行程分成 k 层，每层取优先级最高的一个（覆盖近 / 中 / 远，偏向要去的点）。"""
    ok = np.where(np.isfinite(T_row) & (T_row > 0))[0]
    if not len(ok):
        return []
    k = int(max(1, min(k, len(ok))))
    order = ok[np.argsort(T_row[ok])]
    bins = np.array_split(order, k)
    out = []
    for b in bins:
        if len(b):
            out.append(int(b[np.argmax(priority[b])]))
    return out


def depot_factors(ctx: dict, P: dict, dep_utc: _dt.datetime, key: str | None, cache: Cache | None = None, post=None, raining: bool = True) -> dict:
    """返回 {"provider", "departure_utc", "departure_local", "factors": {depot_id: {factor, n, p90, mean_speed_kmh}}, "elements", "simulated", "note"}。"""
    depots = ctx["depots"]; T = ctx["T_heavy_d"]; pri = ctx["pts0"]["priority"].to_numpy(dtype=float) if "priority" in ctx["pts0"].columns else np.ones(len(ctx["pts0"]))
    dst = ctx["pts0"][["lon", "lat"]].to_numpy(dtype=float)
    loc = dep_utc.astimezone(_dt.timezone(_dt.timedelta(hours=int(P.get("tz_offset_h", 4)))))
    prov = provider(P)
    meta = {"provider": prov, "departure_utc": dep_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "departure_local": loc.strftime("%Y-%m-%d %H:%M") + f" (UTC+{int(P.get('tz_offset_h', 4))})", "factors": {}, "elements": 0,
            "simulated": prov != "google", "routing_preference": P.get("routing_preference"), "note": None, "sample_per_depot": int(P.get("sample_per_depot", 12))}
    if prov == "none":
        meta["factors"] = {str(d): {"factor": 1.0, "n": 0} for d in depots["depot_id"]}; meta["note"] = "路况关闭：系数 1.0"
        return meta
    if prov != "google":
        f = simulated_factor(dep_utc, P, raining)
        h, wd = local_hour(dep_utc, P)
        meta["factors"] = {str(d): {"factor": round(f, 3), "n": 0, "source": "simulated"} for d in depots["depot_id"]}
        meta["note"] = f"模拟时段曲线（当地 {h:02d} 时、{'周末' if wd in (4, 5) else '工作日'}）× 雨天加成 {P['simulated'].get('rain_uplift')}" + ("；配置 provider=google 但环境变量里没有密钥" if str(P.get("provider")).lower() == "google" else "")
        return meta
    pairs = []; owner = []
    for i, d in depots.reset_index(drop=True).iterrows():
        idx = sample_destinations(np.asarray(T[i], dtype=float), pri, int(P.get("sample_per_depot", 12)))
        for j in idx:
            pairs.append(((float(d["lon"]), float(d["lat"])), (float(dst[j, 0]), float(dst[j, 1])))); owner.append((str(d["depot_id"]), int(j)))
    try:
        res, n_req = fetch_pairs(pairs, dep_utc, P, key, cache, post=post)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as ex:
        f = simulated_factor(dep_utc, P, raining)
        meta.update({"provider": "simulated", "simulated": True, "factors": {str(d): {"factor": f, "n": 0, "source": "simulated"} for d in depots["depot_id"]}, "note": f"Google 请求失败（{str(ex)[:120]}），退回模拟曲线", "error": str(ex)[:200]})
        return meta
    meta["elements"] = n_req
    acc = {}
    for i, (did, j) in enumerate(owner):
        el = res.get(i)
        if not el or el.get("duration_s") is None or not el.get("static_s"):
            continue
        a = acc.setdefault(did, {"dur": 0.0, "sta": 0.0, "ratios": [], "dist": 0.0, "n": 0})
        a["dur"] += float(el["duration_s"]); a["sta"] += float(el["static_s"]); a["ratios"].append(float(el["duration_s"]) / max(float(el["static_s"]), 1.0)); a["dist"] += float(el.get("dist_m") or 0.0); a["n"] += 1
    fmin, fmax = float(P["factor_min"]), float(P["factor_max"])
    for did in depots["depot_id"].astype(str):
        a = acc.get(did)
        if not a or a["n"] == 0:
            meta["factors"][did] = {"factor": 1.0, "n": 0, "source": "no data"}
            continue
        f = min(max(a["dur"] / max(a["sta"], 1.0), fmin), fmax)
        meta["factors"][did] = {"factor": round(f, 3), "n": a["n"], "p90": round(float(np.percentile(a["ratios"], 90)), 3), "mean_speed_kmh": round(a["dist"] / 1000.0 / max(a["dur"] / 3600.0, 1e-6), 1), "source": "google"}
    fs = [v["factor"] for v in meta["factors"].values() if v.get("n")]
    meta["note"] = f"Google Routes {P.get('routing_preference')}，{len(fs)} 个驻地各抽样 ≤{P.get('sample_per_depot')} 个目的地，系数 {min(fs):.2f}–{max(fs):.2f}" if fs else "Google 没返回可用元素"
    return meta


def factor_series(ctx: dict, P: dict, dep_utc0: _dt.datetime, t0_min: float, key: str | None, cache: Cache | None = None, post=None, raining: bool = True, base: dict | None = None) -> dict:
    """按滚动窗口出发时刻各取一次驻地系数：{"t0_min", "series": {"<t_min>": {depot_id: factor}}, "elements"}。第 0 个窗口用 base（已算好的 meta），后面每个窗口 dep0 + k×window_min；
    Google 时后续窗口用更少的抽样（per_window_sample_per_depot）省元素；模拟曲线免费。"""
    W = int(P.get("windows_ahead", 8)); wm = float(P.get("window_min", 60))
    series = {}; elements = 0
    f0 = {d: float(v.get("factor", 1.0)) for d, v in ((base or {}).get("factors") or {}).items()}
    series[str(int(round(t0_min)))] = f0
    Pk = {**P, "sample_per_depot": int(P.get("per_window_sample_per_depot", 6))}
    for k in range(1, W + 1):
        dep_k = dep_utc0 + _dt.timedelta(minutes=k * wm)
        mk = depot_factors(ctx, Pk, dep_k, key, cache, post=post, raining=raining)
        if mk.get("error"):
            break
        elements += int(mk.get("elements") or 0)
        series[str(int(round(t0_min + k * wm)))] = {d: float(v.get("factor", 1.0)) for d, v in (mk.get("factors") or {}).items()}
    vals = [f for s in series.values() for f in s.values()]
    return {"t0_min": float(t0_min), "window_min": wm, "series": series, "series_elements": elements, "series_min": (round(min(vals), 3) if vals else None), "series_max": (round(max(vals), 3) if vals else None)}


def scale_at(ctx: dict, t_now: float):
    """build_classes 用：t_now 时刻相对于基准窗口的缩放——(每个驻地的比值数组, 点→点的均值比值)。没有序列就 (None, 1.0)。"""
    tr = ctx.get("traffic") or {}
    ser = tr.get("series") or {}
    if not ser or len(ser) < 2:
        return None, 1.0
    keys = sorted(float(k) for k in ser)
    k = max([x for x in keys if x <= float(t_now) + 1e-6] or [keys[0]])
    f_t = ser[str(int(round(k)))]; f_0 = ser[str(int(round(keys[0])))]
    depots = ctx["depots"]["depot_id"].astype(str).tolist()
    sc = np.array([float(f_t.get(d, 1.0)) / max(float(f_0.get(d, 1.0)), 1e-6) for d in depots], dtype=float)
    m_t = float(np.mean([f_t.get(d, 1.0) for d in depots])) if depots else 1.0; m_0 = float(np.mean([f_0.get(d, 1.0) for d in depots])) if depots else 1.0
    return sc, (m_t / max(m_0, 1e-6))


def apply(ctx: dict, meta: dict, P: dict | None = None) -> dict:
    """把驻地系数套到行程矩阵上，返回新的上下文（浅拷贝；矩阵是新数组）。ctx["traffic"] 记录来源与系数。"""
    P = P or {}
    F = meta.get("factors") or {}
    depots = ctx["depots"].reset_index(drop=True)
    fac = np.array([float((F.get(str(d)) or {}).get("factor", 1.0)) for d in depots["depot_id"]], dtype=float)
    out = dict(ctx)
    out["T_heavy_d"] = np.asarray(ctx["T_heavy_d"], dtype=float) * fac[:, None]
    out["T_light_d"] = np.asarray(ctx["T_light_d"], dtype=float) * fac[:, None]
    g = float(np.mean(fac)) if len(fac) else 1.0
    if P.get("apply_to_point_to_point", True) and "T_pp" in ctx:
        out["T_pp"] = np.asarray(ctx["T_pp"], dtype=float) * g
    out["traffic"] = {**{k: v for k, v in meta.items() if k != "factors"}, "factor_mean": round(g, 3), "factor_min": (round(float(fac.min()), 3) if len(fac) else 1.0), "factor_max": (round(float(fac.max()), 3) if len(fac) else 1.0),
                      "by_depot": {str(d): F.get(str(d)) for d in depots["depot_id"]}, "applied": True}
    return out                                                          # 后续窗口的时变缩放由 dispatch2.build_classes 按 traffic["series"] 现场乘（见 scale_at）


# ---------------------------------------------------------------- 精算层：调度单里的 驻地→点 对逐对取 Google 分钟数
def refine_plan(plan, ctx: dict, P: dict, dep_utc: _dt.datetime, key: str | None, cache: Cache | None = None, post=None) -> dict:
    """对调度单里从驻地出发的行程（from ∈ depots）逐对查 Google；报出模型分钟数（已含驻地系数）与 Google 分钟数的偏差。不改调度单。"""
    import pandas as pd
    if plan is None or not len(plan) or provider(P) != "google" or not key:
        return {"refined": False, "n_pairs": 0, "note": ("没有调度单" if plan is None or not len(plan) else "只有 provider=google 且有密钥时做逐对精算")}
    depots = ctx["depots"].reset_index(drop=True); dloc = {str(r["depot_id"]): (float(r["lon"]), float(r["lat"])) for _, r in depots.iterrows()}
    df = pd.DataFrame(plan)
    df = df[df["from"].astype(str).isin(dloc.keys())]
    if not len(df):
        return {"refined": False, "n_pairs": 0, "note": "调度单里没有从驻地出发的行程"}
    df = df.assign(_o=df["from"].astype(str), _d=list(zip(df["lon"].astype(float).round(5), df["lat"].astype(float).round(5))))
    grp = df.groupby(["_o", "_d"])
    keys = list(grp.groups.keys())[: int(P.get("refine_pairs_max", 300))]
    pairs = [(dloc[o], (float(d[0]), float(d[1]))) for o, d in keys]
    res, n_req = fetch_pairs(pairs, dep_utc, P, key, cache, post=post)
    rows = []
    for i, (o, d) in enumerate(keys):
        el = res.get(i)
        if not el or el.get("duration_s") is None:
            continue
        g = grp.get_group((o, d)); model_min = float(g["travel_min"].astype(float).mean()); goog = float(el["duration_s"]) / 60.0
        rows.append({"from": o, "fp_id": str(g["fp_id"].iloc[0]), "units": int(g["unit_id"].nunique()), "model_min": round(model_min, 1), "google_min": round(goog, 1), "google_static_min": (round(float(el["static_s"]) / 60.0, 1) if el.get("static_s") else None),
                     "delta_min": round(goog - model_min, 1), "dist_km": (round(float(el["dist_m"]) / 1000.0, 1) if el.get("dist_m") else None), "condition": el.get("condition")})
    if not rows:
        return {"refined": False, "n_pairs": 0, "elements": n_req, "note": "Google 没返回可用元素"}
    d = np.array([r["delta_min"] for r in rows])
    rows.sort(key=lambda r: -abs(r["delta_min"]))
    return {"refined": True, "n_pairs": len(rows), "elements": n_req, "delta_mean_min": round(float(d.mean()), 1), "delta_abs_p90_min": round(float(np.percentile(np.abs(d), 90)), 1), "n_slower_10min": int((d > 10).sum()),
            "worst": rows[:10], "departure_utc": dep_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "note": "Google 分钟数（带路况） − 模型分钟数（本地路网 × 驻地系数）；正值 = 实际比模型慢"}


def status(cfg: dict) -> dict:
    """给控制台 / 智能体：现在配的是什么源、有没有密钥、缓存有多少。"""
    P = params(cfg)
    cf = ROOT / str(P.get("cache_file") or "out/traffic_cache.json")
    n_cache = 0
    if cf.exists():
        try:
            n_cache = len(json.loads(cf.read_text(encoding="utf-8")) or {})
        except Exception:
            n_cache = 0
    return {"configured": str(P.get("provider")), "effective": provider(P), "has_key": bool(api_key(P)), "key_env": P.get("api_key_env"), "sample_per_depot": P.get("sample_per_depot"),
            "refine_pairs_max": P.get("refine_pairs_max"), "cache_ttl_min": P.get("cache_ttl_min"), "cached_pairs": n_cache, "routing_preference": P.get("routing_preference"),
            "simulated": {"rain_uplift": P["simulated"].get("rain_uplift"), "weekday_peak": max(SIM_WEEKDAY.values()), "weekend_peak": max(SIM_WEEKEND.values())}}
