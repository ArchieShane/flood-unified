# -*- coding: utf-8 -*-
"""本地 NAServer 能力探测：同一段 A13 → SIM-L1-005，试 FeatureSet 导航、中文导航、多边形障碍（禁行 / 按系数减速）。只读请求。"""
import json
import ssl
import sys
import urllib.parse
import urllib.request

U = "https://10.255.254.103:6443/arcgis/rest/services/ITC_NRN_ROAD/ITC_NRN_ROADS_DIAG_20261028/NAServer/Route/solve"
CTX = ssl._create_unverified_context()   # 该服务器自签证书，只对这一台
A, B = [54.38653, 24.45691], None


def solve(p):
    data = urllib.parse.urlencode({**p, "f": "json"}).encode()
    with urllib.request.urlopen(urllib.request.Request(U, data=data), timeout=90, context=CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    d = json.load(open("console/data/flood2d_rda_s61d1/tracks.json", encoding="utf-8"))
    leg = d["states"]["L1"]["island"]["units"]["PA-162"]["legs"][0]
    b = leg["path"][-1][1:]
    stops = json.dumps({"features": [{"geometry": {"x": A[0], "y": A[1]}, "attributes": {"Name": "A13"}},
                                     {"geometry": {"x": b[0], "y": b[1]}, "attributes": {"Name": leg["fp"]}}], "spatialReference": {"wkid": 4326}})
    with urllib.request.urlopen(U.rsplit("/", 1)[0] + "?f=json", timeout=60, context=CTX) as r:   # 出行模式要传整个对象，不认名字
        tm = next(m for m in json.loads(r.read().decode("utf-8"))["supportedTravelModes"] if m["name"] == "Heavy Vehicles")
    base = {"stops": stops, "travelMode": json.dumps(tm), "returnDirections": "true", "directionsOutputType": "esriDOTFeatureSets",
            "directionsLanguage": "zh-CN", "returnRoutes": "true", "outputLines": "esriNAOutputLineTrueShape", "outSR": 4326}
    print("online ref:", leg["drive"], "min", leg["km"], "km")
    for name, extra in [("plain", {}),
                        ("mode-name-ok?", {}),
                        ("slow x3 box", {"polygonBarriers": json.dumps({"features": [{"geometry": {"rings": [[[54.375, 24.448], [54.385, 24.448], [54.385, 24.456], [54.375, 24.456], [54.375, 24.448]]], "spatialReference": {"wkid": 4326}},
                                                                                       "attributes": {"BarrierType": 1, "Attr_Minutes": 3.0}}]})}),
                        ("block box", {"polygonBarriers": json.dumps({"features": [{"geometry": {"rings": [[[54.375, 24.448], [54.385, 24.448], [54.385, 24.456], [54.375, 24.456], [54.375, 24.448]]], "spatialReference": {"wkid": 4326}},
                                                                                     "attributes": {"BarrierType": 0}}]})})]:
        try:
            o = solve({**base, **extra})
        except Exception as ex:  # noqa: BLE001
            print(name, "EXC", ex); continue
        if "error" in o:
            print(name, "ERROR", json.dumps(o["error"], ensure_ascii=False)[:400]); continue
        f = (o.get("routes") or {}).get("features") or []
        at = f[0]["attributes"] if f else {}
        dl = (o.get("directionLines") or {}).get("features", []); dp = (o.get("directionPoints") or {}).get("features", [])
        print(name, {k: v for k, v in at.items() if k.startswith("Total_")}, "dirLines", len(dl), "dirPts", len(dp),
              "keys", [k for k in o if k not in ("routes",)])
        if dp[:3]:
            print("   sample", [x["attributes"].get("DisplayText") for x in dp[:4]], "attr keys", list(dl[0]["attributes"]) if dl else None)


if __name__ == "__main__":
    main()
