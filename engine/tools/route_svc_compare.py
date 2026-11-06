# -*- coding: utf-8 -*-
"""本地 NAServer（ITC_NRN_ROADS_DIAG_20261028）vs ArcGIS Online 卡车路线：同一批 RD-A L3 派单段、同一组积水障碍，逐段比时间、距离、线形。

只读、只新增：派单与障碍同 tools/rda_vehicle_tracks.py（直接复用它的函数）；在线结果走它的缓存（out/routes/cache_detail，已算过的不再计费），
本地结果缓存到 out/routes/cache_local/。输出 out/routes/svc_compare/compare_<雨>.csv + summary.json。
用法：python -X utf8 tools/route_svc_compare.py [--storms S61D1,A61D1] [--threads 4]
"""
from __future__ import annotations
import argparse
import hashlib
import json
import pathlib
import ssl
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import rda_vehicle_tracks as VT   # noqa: E402
from shapely.geometry import LineString, Point   # noqa: E402
from swbrain import travel   # noqa: E402

SVC = "https://10.255.254.103:6443/arcgis/rest/services/ITC_NRN_ROAD/ITC_NRN_ROADS_DIAG_20261028/NAServer/Route"
CTX = ssl._create_unverified_context()   # 该服务器自签证书，只对这一台
CACHE = ROOT / "out" / "routes" / "cache_local"
OUT = ROOT / "out" / "routes" / "svc_compare"
_TM: dict = {}


def _post(url, p, timeout=120):
    data = urllib.parse.urlencode({**p, "f": "json"}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout, context=CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def mode(name="Heavy Vehicles"):
    if not _TM:
        with urllib.request.urlopen(SVC + "?f=json", timeout=60, context=CTX) as r:
            _TM.update({m["name"]: m for m in json.loads(r.read().decode("utf-8"))["supportedTravelModes"]})
    return _TM[name]


def local_detail(a, b, bars, names):
    """同 VT.route_detail 的返回结构，打本地服务；障碍的减速系数挂在 Attr_Minutes 上。"""
    bars = [{**x, "attributes": {("Attr_Minutes" if k.startswith("Attr_") else k): v for k, v in x["attributes"].items()}} for x in (bars or [])]
    key = hashlib.sha1(json.dumps([a, b, bars, "local-v1"], sort_keys=True).encode()).hexdigest()
    cf = CACHE / f"{key}.json"
    if cf.exists():
        return json.loads(cf.read_text(encoding="utf-8"))
    p = {"stops": json.dumps({"features": [{"geometry": {"x": a[0], "y": a[1]}, "attributes": {"Name": names[0]}},
                                           {"geometry": {"x": b[0], "y": b[1]}, "attributes": {"Name": names[1]}}], "spatialReference": {"wkid": 4326}}),
         "travelMode": json.dumps(mode()), "returnDirections": "true", "directionsOutputType": "esriDOTFeatureSets",
         "directionsLanguage": "zh-CN", "returnRoutes": "true", "outputLines": "esriNAOutputLineTrueShape", "outSR": 4326}
    if bars:
        p["polygonBarriers"] = json.dumps({"features": bars})
    try:
        o = _post(SVC + "/solve", p)
    except Exception as ex:  # noqa: BLE001
        return {"error": str(ex)[:300]}
    if "error" in o or not o.get("routes", {}).get("features"):
        return {"error": json.dumps(o.get("error") or "no route", ensure_ascii=False)[:400]}
    at = o["routes"]["features"][0]["attributes"]
    pts = {f["attributes"]["ObjectID"]: f["attributes"] for f in (o.get("directionPoints") or {}).get("features", [])}
    segs = []
    for f in (o.get("directionLines") or {}).get("features", []):
        x = f["attributes"]; dp = pts.get(x.get("DirectionPointID")) or {}
        segs.append({"text": dp.get("DisplayText"), "road": dp.get("AlternateName") or dp.get("Name"), "meters": round(float(x.get("Meters") or 0), 1),
                     "minutes": round(float(x.get("Minutes") or 0), 3),
                     "path": [[round(u, 6), round(v, 6)] for pa in f["geometry"].get("paths", []) for u, v in pa]})
    km = at.get("Total_Meters"); km = float(km) / 1000 if km is not None else sum(s["meters"] for s in segs) / 1000
    r = {"minutes": round(float(at.get("Total_Minutes") or 0), 2), "km": round(km, 3), "segs": segs}
    CACHE.mkdir(parents=True, exist_ok=True); cf.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    return r


def local_for(pairs, polys, hard, soft, threads):
    """同 VT.routes_for：目的地 / 出发那片不设障碍；深水截断时改成只减速并标 blocked。"""
    def one(item):
        k, (a, b, dep, fp) = item
        skip = {i for i, g in enumerate(polys) for q in (a, b) if g.contains(Point(*q)) or g.distance(Point(*q)) < 0.0003}
        r = local_detail(a, b, [x for i, x in enumerate(hard) if i not in skip], (dep, fp))
        if "error" in r and "No route" in r["error"]:
            r = {**local_detail(a, b, [x for i, x in enumerate(soft) if i not in skip], (dep, fp)), "blocked": True}
        return k, r
    with ThreadPoolExecutor(threads) as ex:
        return dict(ex.map(one, pairs.items()))


def line_of(r):
    pts = [tuple(c) for s in (r.get("segs") or []) for c in s["path"]]
    return LineString(pts) if len(pts) >= 2 else None


def overlap(l1, l2, tol_m=30.0):
    """l1 落在 l2 两侧 tol_m 内的长度份额（经纬度近似换米）。"""
    if l1 is None or l2 is None or l1.length == 0:
        return None
    return round(l1.intersection(l2.buffer(tol_m / 111000.0)).length / l1.length, 3)


def online_cached(a, b, bars):
    """只读在线缓存（同 VT.route_detail 的键）；没缓存的不去请求，免得计费。"""
    key = hashlib.sha1(json.dumps([a, b, bars or [], "detail-v1"], sort_keys=True).encode()).hexdigest()
    cf = VT.CACHE / f"{key}.json"
    return json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else None


def online_for(pairs, polys, hard, soft):
    out = {}
    for k, (a, b, dep, fp) in pairs.items():
        skip = {i for i, g in enumerate(polys) for q in (a, b) if g.contains(Point(*q)) or g.distance(Point(*q)) < 0.0003}
        r = online_cached(a, b, [x for i, x in enumerate(hard) if i not in skip])
        if r is not None and "error" in r and "No route" in r["error"]:
            r2 = online_cached(a, b, [x for i, x in enumerate(soft) if i not in skip])
            r = {**r2, "blocked": True} if r2 is not None else None
        out[k] = r
    return out


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--storms", default=",".join(VT.STORMS)); ap.add_argument("--states", default=",".join(VT.STATES))
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args(argv)
    C = VT.L3.context(); cfg = C["cfg"]; tf = float(cfg["dispatch"].get("truck_time_factor", 1.0))
    road = travel.RoadNetwork(C["ref"]["edges"], C["ref"]["vertices"])
    if tf != 1.0:
        _tm = road.travel_minutes
        road.travel_minutes = lambda *x, **k: _tm(*x, **k) * tf
    VT.TL.A13_LL = next([d["lon"], d["lat"]] for d in C["fleet"]["depots"] if d["depot_id"] == "A13")
    fleets = {p: VT.TL.sub_fleet(C["fleet"], VT.TL.POOLS[p]) for p in ("island", "adm")}
    OUT.mkdir(parents=True, exist_ok=True); summ = {}
    for storm in a.storms.split(","):
        _, runs = VT.run_dispatch(storm, a.states.split(","), C, road, fleets)
        rows = []
        for st in dict.fromkeys(s for s, _ in runs):
            pairs, sched = {}, {}
            for (s_, p), R in runs.items():
                if s_ != st or not len(R["plan"]):
                    continue
                for _, x in R["plan"].iterrows():
                    aa = [round(float(x["from_lon"]), 5), round(float(x["from_lat"]), 5)]; bb = [round(float(x["lon"]), 5), round(float(x["lat"]), 5)]
                    k = f"{aa[0]},{aa[1]}|{bb[0]},{bb[1]}"
                    pairs.setdefault(k, (aa, bb, str(x["from"]), str(x["fp_id"])))
                    if "travel_min" in x:
                        sched.setdefault(k, float(x["travel_min"]))
            if not pairs:
                continue
            polys, hard, soft = VT.state_barriers(storm, st, cfg)
            on = online_for(pairs, polys, hard, soft)
            lo = local_for(pairs, polys, hard, soft, a.threads)
            for k, (aa, bb, dep, fp) in pairs.items():
                o, l = on.get(k), lo.get(k)
                ok_o = bool(o) and "error" not in o; ok_l = bool(l) and "error" not in l
                lo_o, lo_l = (line_of(o) if ok_o else None), (line_of(l) if ok_l else None)
                rows.append({"storm": storm, "state": st, "from": dep, "fp": fp, "a": aa, "b": bb,
                             "sched_min": sched.get(k),
                             "online_min": o.get("minutes") if ok_o else None, "online_km": o.get("km") if ok_o else None,
                             "online_blocked": bool(o.get("blocked")) if ok_o else None, "online_err": (o or {}).get("error") if o else "no-cache",
                             "local_min": l.get("minutes") if ok_l else None, "local_km": l.get("km") if ok_l else None,
                             "local_blocked": bool(l.get("blocked")) if ok_l else None, "local_err": (l or {}).get("error"),
                             "local_steps": len(l.get("segs") or []) if ok_l else None,
                             "shape_local_on_online": overlap(lo_l, lo_o), "shape_online_on_local": overlap(lo_o, lo_l)})
            print(f"  {storm} {st:9s} 段 {len(pairs)}：本地出错 {sum(1 for r in rows if r['state'] == st and r['storm'] == storm and r['local_err'])}", flush=True)
        df = pd.DataFrame(rows); df.to_csv(OUT / f"compare_{storm}.csv", index=False, encoding="utf-8-sig")
        both = df.dropna(subset=["online_min", "local_min"])
        rt = both["local_min"] / both["online_min"]; rk = both["local_km"] / both["online_km"]
        summ[storm] = {"legs": len(df), "online_ok": int(df["online_min"].notna().sum()), "local_ok": int(df["local_min"].notna().sum()),
                       "local_errors": df["local_err"].dropna().astype(str).str[:160].value_counts().to_dict(),
                       "online_missing_cache": int((df["online_err"] == "no-cache").sum()),
                       "time_ratio_local_over_online": {q: round(float(rt.quantile(v)), 3) for q, v in (("p10", .1), ("p50", .5), ("p90", .9))} if len(both) else None,
                       "km_ratio_local_over_online": {q: round(float(rk.quantile(v)), 3) for q, v in (("p10", .1), ("p50", .5), ("p90", .9))} if len(both) else None,
                       "km_within_5pct": int((abs(rk - 1) <= 0.05).sum()), "shape_overlap_p50": round(float(both["shape_local_on_online"].median()), 3) if len(both) else None,
                       "shape_overlap_ge_0.9": int((both["shape_local_on_online"] >= 0.9).sum()),
                       "blocked_online": int(df["online_blocked"].fillna(False).sum()), "blocked_local": int(df["local_blocked"].fillna(False).sum())}
        print(json.dumps({storm: summ[storm]}, ensure_ascii=False, indent=1), flush=True)
    (OUT / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    print("→", OUT)


if __name__ == "__main__":
    raise SystemExit(main())
