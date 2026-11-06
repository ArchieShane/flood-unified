# -*- coding: utf-8 -*-
"""按范围从 Overpass 下载 OSM 面 / 道路（out geom），格式同 out/osm/*.json（c1_design_l2s5.osm_layers 能直接读）。
只发出一个经纬度范围，不带任何项目数据。

用法：python tools/osm_fetch.py <南> <西> <北> <东> <输出目录>
  例：python tools/osm_fetch.py 24.400 54.555 24.470 54.650 out/osm_rb01
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

URL = "https://overpass-api.de/api/interpreter"
KEYS = ["building", "landuse", "leisure", "natural", "amenity", "aeroway", "highway"]


def fetch(key, bbox):
    s, w, n, e = bbox
    q = f'[out:json][timeout:180];(way["{key}"]({s},{w},{n},{e});relation["{key}"]({s},{w},{n},{e}););out geom;'
    req = urllib.request.Request(URL, data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": "swbrain-osm-fetch/1.0"})
    for k in range(4):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as ex:                       # 429 / 504：等一会儿再试
            print(f"  {key} 第 {k + 1} 次失败：{ex}"); time.sleep(20 * (k + 1))
    raise SystemExit(f"{key} 下载失败")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    s, w, n, e = map(float, sys.argv[1:5]); out = pathlib.Path(sys.argv[5]); out.mkdir(parents=True, exist_ok=True)
    for key in KEYS:
        d = fetch(key, (s, w, n, e))
        (out / f"{key}.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        print(f"  {key}: {len(d.get('elements', [])):,} 个要素")
        time.sleep(5)
    (out / "SOURCE.txt").write_text(f"OpenStreetMap via {URL}，范围 S{s} W{w} N{n} E{e}，下载 {time.strftime('%Y-%m-%d %H:%M')}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
