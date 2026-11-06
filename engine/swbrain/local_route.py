"""本地路径服务：内网 ArcGIS Server 10.8 的 NAServer（ITC_NRN_ROADS_DIAG_20261028/Route），L3 卡车路线的首选来源；ArcGIS Online（swbrain/online_route.py）是备选。

2026-10-28 用户：「先把本地服务接入，然后在线的保持备选」。服务的问题与对比数据见 docs/本地路径服务问题说明_20261028.md。
- 地址、出行模式、证书校验、时间系数在 config.toml [routing]；不要令牌。服务器是自签证书，local_verify_ssl = false 时只对这一个地址跳过校验。
- 出行模式缺省 Heavy Vehicles（重型车限制 + 单行 + 禁转）；它的阻抗 Minutes 是小汽车口径（实测为在线卡车时间的 0.59–0.60 倍，中位），
  返回的分钟数乘 local_time_factor（缺省 1 = 不校正）。
- 积水障碍与在线同口径，只是减速系数挂在 Attr_Minutes 上（online_route.barriers(..., impedance="Minutes")）。
- 中文、英文导航各请求一次（同一组输入，路线相同），英文直接存 steps_en，不靠短语表翻译。
- 结果按请求内容缓存到 out/routes/cache_local/（存的是服务原值，系数在读出后再乘）。
- 不在路上的障碍服务会写一句「has no associated network location」，这是提示不是错；真不可达时错误里有「No route」。
- 只缓存成功结果和「No route」；「Unable to complete operation」重试两次，仍失败不缓存、标 transient。
  2026-10-28 实测这个错误多数是起点落在禁行积水片里（每次复现），routing.solve 会把深水当减速重求。
"""
from __future__ import annotations
import hashlib
import json
import ssl
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "out" / "routes" / "cache_local"
DEFAULTS = {"providers": ["local", "online"], "local_url": "", "local_mode": "Heavy Vehicles", "local_verify_ssl": True, "local_time_factor": 1.0}
_MODES: dict = {}
_CFG: dict = {}


def cfg() -> dict:
    if not _CFG:
        c = {}
        p = ROOT / "config.toml"
        if p.exists():
            c = tomllib.loads(p.read_text(encoding="utf-8")).get("routing") or {}
        _CFG.update({**DEFAULTS, **c})
    return _CFG


def _ctx():
    return None if cfg()["local_verify_ssl"] else ssl._create_unverified_context()   # 自签证书：只对 local_url 这一台


def _post(url: str, params: dict, timeout: float = 120) -> dict:
    data = urllib.parse.urlencode({**params, "f": "json"}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout, context=_ctx()) as r:
        return json.loads(r.read().decode("utf-8"))


def travel_mode(name: str | None = None) -> dict:
    """服务只认完整的出行模式 JSON（传名字会报 Invalid value for parameter travelMode）。"""
    name = name or cfg()["local_mode"]
    if name not in _MODES:
        with urllib.request.urlopen(cfg()["local_url"] + "?f=json", timeout=60, context=_ctx()) as r:
            o = json.loads(r.read().decode("utf-8"))
        if "error" in o:
            raise RuntimeError(f"取出行模式失败：{error_text(o['error'])}")
        _MODES.update({m["name"]: m for m in o.get("supportedTravelModes", [])})
        if name not in _MODES:
            raise RuntimeError(f"服务没有出行模式 {name!r}（有：{', '.join(_MODES) or '无'}）")
    return _MODES[name]


def error_text(err: dict | str) -> str:
    """服务报错 → 一句话；去掉「障碍不在路上」那类提示（它们排在前面，会把真正的「No route」挤出截断长度）。"""
    if not isinstance(err, dict):
        return str(err)[:400]
    det = [d for d in (err.get("details") or []) if "no associated network location" not in d]
    return ("; ".join(det) or err.get("message") or json.dumps(err, ensure_ascii=False))[:400]


def _solve(a, b, bars, names, lang, full=True) -> dict:
    stops = json.dumps({"features": [{"geometry": {"x": a[0], "y": a[1]}, "attributes": {"Name": names[0]}},
                                     {"geometry": {"x": b[0], "y": b[1]}, "attributes": {"Name": names[1]}}], "spatialReference": {"wkid": 4326}})
    p = {"stops": stops, "travelMode": json.dumps(travel_mode()), "returnDirections": "true", "directionsOutputType": "esriDOTFeatureSets",
         "directionsLanguage": lang, "returnRoutes": "true" if full else "false", "outputLines": "esriNAOutputLineTrueShape" if full else "esriNAOutputLineNone",
         "outSR": 4326}
    if bars:
        p["polygonBarriers"] = json.dumps({"features": bars})
    return _post(cfg()["local_url"] + "/solve", p)


def _steps(o) -> list[dict]:
    pts = {f["attributes"]["ObjectID"]: f["attributes"] for f in (o.get("directionPoints") or {}).get("features", [])}
    out = []
    for f in (o.get("directionLines") or {}).get("features", []):
        x = f["attributes"]; dp = pts.get(x.get("DirectionPointID")) or {}
        out.append({"text": dp.get("DisplayText"), "road": dp.get("AlternateName") or dp.get("Name"), "meters": round(float(x.get("Meters") or 0), 1),
                    "minutes": round(float(x.get("Minutes") or 0), 3),
                    "path": [[round(u, 6), round(v, 6)] for pa in (f.get("geometry") or {}).get("paths", []) for u, v in pa]})
    return out


def _local_bars(bars):
    """在线口径的障碍（Attr_TruckTravelTime）→ 本地口径（Attr_Minutes）；禁行障碍不动。"""
    return [{**x, "attributes": {("Attr_Minutes" if k.startswith("Attr_") else k): v for k, v in x["attributes"].items()}} for x in (bars or [])]


def raw(a, b, bars=None, names=("起点", "终点"), use_cache: bool = True) -> dict:
    """服务原值：{minutes, km, segs[{text, road, meters, minutes, path}], steps, steps_en, messages} 或 {error}。bars 用在线口径传进来即可。"""
    bars = _local_bars(bars)
    key = hashlib.sha1(json.dumps([a, b, bars, cfg()["local_mode"], cfg()["local_url"], "local-v2"], sort_keys=True).encode()).hexdigest()
    cf = CACHE / f"{key}.json"
    if use_cache and cf.exists():
        return json.loads(cf.read_text(encoding="utf-8"))
    for k in range(3):                                                   # 「Unable to complete operation」：重试两次（多数是起点在禁行障碍里，重试不会好，见模块说明）
        try:
            o = _solve(a, b, bars, names, "zh-CN")
        except Exception as ex:  # noqa: BLE001 — 内网不通 / 超时 / 证书 / 服务地址错：不缓存，调用方退回在线
            return {"error": f"local service error: {type(ex).__name__}: {str(ex)[:200]}", "transport": True}
        if not ("error" in o and "No route" not in error_text(o["error"])):
            break
        time.sleep(1.0 + k)
    if "error" in o and "No route" not in error_text(o["error"]):
        return {"error": error_text(o["error"]), "transient": True}       # 不缓存，调用方换备选；下次重跑再试本地
    if "error" in o or not o.get("routes", {}).get("features"):
        r = {"error": error_text(o.get("error") or "no route")}
    else:
        at = o["routes"]["features"][0]["attributes"]; segs = _steps(o)
        km = at.get("Total_Meters"); km = float(km) / 1000 if km is not None else sum(s["meters"] for s in segs) / 1000
        try:
            en = [s["text"] for s in _steps(_solve(a, b, bars, names, "en", full=False))]
        except Exception:  # noqa: BLE001 — 英文导航拿不到不影响路线
            en = None
        r = {"minutes": round(float(at.get("Total_Minutes") or 0), 3), "km": round(km, 3), "segs": segs,
             "steps": [s["text"] for s in segs], "steps_en": en if en and len(en) == len(segs) else None,
             "messages": [m.get("description") for m in o.get("messages", []) if "no associated network location" not in (m.get("description") or "")][:5]}
    CACHE.mkdir(parents=True, exist_ok=True); cf.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    return r


def detail(a, b, bars=None, names=("起点", "终点")) -> dict:
    """同 tools/rda_vehicle_tracks.route_detail 的结构（逐段导航线），分钟乘 local_time_factor；minutes_raw 留服务原值。"""
    r = raw(a, b, bars, names)
    if "error" in r:
        return r
    f = float(cfg()["local_time_factor"])
    return {"minutes": round(r["minutes"] * f, 2), "minutes_raw": round(r["minutes"], 2), "km": r["km"], "time_factor": f,
            "segs": [{**s, "minutes": round(s["minutes"] * f, 3)} for s in r["segs"]], "steps_en": r.get("steps_en"), "source": "local"}


def route(a, b, bars=None, names=("起点", "终点")) -> dict:
    """同 swbrain/online_route.route 的结构：{minutes, km, steps, steps_en, line[[lat, lon]…], messages, mode, source}。"""
    r = detail(a, b, bars, names)
    if "error" in r:
        return r
    line = [[y, x] for s in r["segs"] for x, y in s["path"]]
    return {"minutes": r["minutes"], "minutes_raw": r["minutes_raw"], "km": r["km"], "time_factor": r["time_factor"],
            "steps": [s["text"] for s in r["segs"]], "steps_en": r["steps_en"], "line": line,
            "messages": raw(a, b, bars, names).get("messages", []), "mode": cfg()["local_mode"], "source": "local"}
