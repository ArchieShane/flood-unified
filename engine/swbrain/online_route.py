"""在线卡车路线：ArcGIS Online 路径服务（route-api.arcgis.com，Route_World/solve）给 L3 调度单的每张派单出实际行车路线、逐段导航、到达时间。

2026-10-26 用户：L3 设施落地（车开哪条路）用在线路径分析服务，直接做进系统。
- 密钥：环境变量 ARCGIS_API_KEY；进程启动后才设的，从注册表 HKCU\\Environment 读。不写进任何文件、不打印。
- 出行模式：缺省 Trucking Time（服务端的卡车模式，按卡车限速与卡车限制）。
- 积水障碍：二维积水片做多边形障碍；水深 ≥ impassable_m 禁行（BarrierType 0），其余按 slow_factor 放大卡车通行时间（BarrierType 1）。
  与本地调度同一组阈值（config [dispatch] slow_factor、impassable_depth_m.heavy）。目的地所在那片不设障碍（车停在水边）。
- 结果按「起点、终点、障碍、模式、语言」做键缓存到 out/routes/cache/，同一张派单不重复计费。
- 调用失败（断网、密钥失效、积分不够）时返回 None，由调用方退回本地路网（swbrain/travel.py）。
"""
from __future__ import annotations
import hashlib
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "out" / "routes" / "cache"
URL_SOLVE = "https://route-api.arcgis.com/arcgis/rest/services/World/Route/NAServer/Route_World/solve"
URL_MODES = "https://route-api.arcgis.com/arcgis/rest/services/World/Route/NAServer/Route_World/retrieveTravelModes"
_MODES: dict = {}


def api_key() -> str | None:
    k = os.environ.get("ARCGIS_API_KEY")
    if k:
        return k
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
            return winreg.QueryValueEx(h, "ARCGIS_API_KEY")[0]
    except (ImportError, OSError):
        return None


def _post(url: str, params: dict, timeout: float = 90) -> dict:
    k = api_key()
    if not k:
        raise RuntimeError("没有 ArcGIS 密钥（环境变量 ARCGIS_API_KEY）")
    data = urllib.parse.urlencode({**params, "f": "json", "token": k}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def travel_mode(name: str = "Trucking Time") -> dict:
    if name not in _MODES:
        r = _post(URL_MODES, {})
        if "error" in r:
            raise RuntimeError(f"取出行模式失败：{r['error']}")
        _MODES.update({m["name"]: m for m in r.get("supportedTravelModes", [])})
    return _MODES[name]


def _esri_polygon(g) -> dict:
    rings = []
    for p in (list(g.geoms) if g.geom_type == "MultiPolygon" else [g]):
        rings.append([[round(x, 6), round(y, 6)] for x, y in p.exterior.coords])
        rings += [[[round(x, 6), round(y, 6)] for x, y in i.coords] for i in p.interiors]
    return {"rings": rings, "spatialReference": {"wkid": 4326}}


def barriers(polys, depths, impassable_m: float, slow_factor: float, impedance: str = "TruckTravelTime", simplify_deg: float = 0.00005) -> list[dict]:
    """积水片（WGS84 shapely）→ 多边形障碍要素。"""
    out = []
    for g, h in zip(polys, depths):
        a = {"BarrierType": 0} if h >= impassable_m else {"BarrierType": 1, f"Attr_{impedance}": float(slow_factor)}
        out.append({"geometry": _esri_polygon(g.simplify(simplify_deg)), "attributes": a})
    return out


def route(a, b, bars: list[dict] | None = None, mode: str = "Trucking Time", lang: str = "zh-CN", names=("起点", "终点"), use_cache: bool = True) -> dict | None:
    """a、b：[lon, lat]。返回 {minutes, km, steps[], line[[lat, lon]…], messages[]}；失败返回 {error}。"""
    key = hashlib.sha1(json.dumps([a, b, bars or [], mode, lang], sort_keys=True).encode()).hexdigest()
    cf = CACHE / f"{key}.json"
    if use_cache and cf.exists():
        return json.loads(cf.read_text(encoding="utf-8"))
    tm = travel_mode(mode)
    p = {"stops": json.dumps({"features": [{"geometry": {"x": a[0], "y": a[1]}, "attributes": {"Name": names[0]}},
                                           {"geometry": {"x": b[0], "y": b[1]}, "attributes": {"Name": names[1]}}],
                              "spatialReference": {"wkid": 4326}}),
         "travelMode": json.dumps(tm), "returnDirections": "true", "directionsLanguage": lang, "returnRoutes": "true",
         "outputLines": "esriNAOutputLineTrueShape", "outSR": 4326}
    if bars:
        p["polygonBarriers"] = json.dumps({"features": bars})
    try:
        o = _post(URL_SOLVE, p)
    except Exception as ex:  # noqa: BLE001 — 断网 / 超时 / 密钥问题都退回本地
        return {"error": str(ex)}
    if "error" in o or not o.get("routes", {}).get("features"):
        det = [x for x in " ".join((o.get("error") or {}).get("details") or []).split("  ") if x and "no associated network location" not in x]   # 不在路上的积水片提示不算错
        return {"error": ("; ".join(det) or json.dumps(o.get("error") or o.get("messages") or "no route", ensure_ascii=False))[:400]}
    ft = o["routes"]["features"][0]; at = ft["attributes"]
    imp = tm.get("impedanceAttributeName", "TravelTime")
    line = [[round(y, 6), round(x, 6)] for path in ft["geometry"].get("paths", []) for x, y in path]
    r = {"minutes": round(float(at.get(f"Total_{imp}") or at.get("Total_TravelTime") or 0), 2), "km": round(float(at.get("Total_Kilometers") or 0), 3),
         "steps": [x["attributes"]["text"] for x in (o.get("directions") or [{}])[0].get("features", [])],
         "line": line, "messages": [m.get("description") for m in o.get("messages", [])][:5], "mode": mode}
    CACHE.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    return r


DETAIL_CACHE = ROOT / "out" / "routes" / "cache_detail"


def detail(a, b, bars: list[dict] | None = None, names=("起点", "终点"), mode: str = "Trucking Time") -> dict:
    """在线卡车路线 + 逐段导航线（车辆回放用）：{minutes, km, segs[{text, road, meters, minutes, path[[lon, lat]…]}]} 或 {error}。
    原在 tools/rda_vehicle_tracks.route_detail（2026-10-28 挪到这里，缓存键不变，已算过的不重复计费）。"""
    key = hashlib.sha1(json.dumps([a, b, bars or [], "detail-v1"], sort_keys=True).encode()).hexdigest()
    cf = DETAIL_CACHE / f"{key}.json"
    if cf.exists():
        return json.loads(cf.read_text(encoding="utf-8"))
    p = {"stops": json.dumps({"features": [{"geometry": {"x": a[0], "y": a[1]}, "attributes": {"Name": names[0]}},
                                           {"geometry": {"x": b[0], "y": b[1]}, "attributes": {"Name": names[1]}}], "spatialReference": {"wkid": 4326}}),
         "travelMode": json.dumps(travel_mode(mode)), "returnDirections": "true", "directionsOutputType": "esriDOTFeatureSets",
         "directionsLanguage": "zh-CN", "returnRoutes": "true", "outputLines": "esriNAOutputLineTrueShape", "outSR": 4326}
    if bars:
        p["polygonBarriers"] = json.dumps({"features": bars})
    try:
        o = _post(URL_SOLVE, p)
    except Exception as ex:  # noqa: BLE001
        return {"error": str(ex)[:300]}
    if "error" in o or not o.get("routes", {}).get("features"):
        return {"error": json.dumps(o.get("error") or "no route", ensure_ascii=False)[:400]}
    at = o["routes"]["features"][0]["attributes"]
    pts = {f["attributes"]["ObjectID"]: f["attributes"] for f in (o.get("directionPoints") or {}).get("features", [])}
    segs = []
    for f in (o.get("directionLines") or {}).get("features", []):
        x = f["attributes"]; dp = pts.get(x.get("DirectionPointID")) or {}
        path = [[round(u, 6), round(v, 6)] for pa in f["geometry"].get("paths", []) for u, v in pa]
        segs.append({"text": dp.get("DisplayText"), "road": dp.get("AlternateName") or dp.get("Name"), "meters": round(float(x.get("Meters") or 0), 1),
                     "minutes": round(float(x.get("Minutes") or 0), 3), "path": path})
    r = {"minutes": round(float(at.get("Total_TruckTravelTime") or 0), 2), "km": round(float(at.get("Total_Kilometers") or 0), 3), "segs": segs}
    DETAIL_CACHE.mkdir(parents=True, exist_ok=True); cf.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    return r
