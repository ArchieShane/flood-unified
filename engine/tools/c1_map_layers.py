# -*- coding: utf-8 -*-
"""把 C1 建模用到的空间数据导成系统地图图层（console/data/c1_*.geojson）。

  c1_domain.geojson   二维计算域（A3：8 个热点的地表 + 管网汇水区外扩 300 m）
  c1_catch.geojson    8 个热点各自的地表汇水区（D8）与管网汇水区（hotspot_catch_C1），带面积与注记点
  c1_ps_l1.geojson    L1 的现状泵站（点）与排放连线（线）；流量、湿井、启停、排放点来历都在属性里
用法：python tools/c1_map_layers.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "console" / "data"
WORK = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    from shapely.geometry import LineString, Point
    D = gpd.read_file(ROOT / "out" / "c1_domain.geojson")
    D["note"] = "8 个热点（C1 的 7 个 + ADM-487）的地表 + 管网汇水区外扩 300 m；ADM-90 地表汇水区未收敛"
    D.to_file(DATA / "c1_domain.geojson", driver="GeoJSON")
    C = gpd.read_file(DATA / "hotspot_catch_C1.geojson").to_crs(32640)
    C = C[C.kind != "合计"].copy()
    # 管网汇水区 = 各上游井服务范围（泰森 ∩ 46 m）的并集，井之间留下很多缝和小洞，直接画是「蜘蛛网」。
    # 地图上显示用：外扩 20 m 再收回 20 m 把缝连起来，去掉 < 1 ha 的洞；面积仍用原值（area_ha 不变），建模不受影响
    from shapely.geometry import MultiPolygon, Polygon
    def tidy(g):
        g = g.buffer(20.0).buffer(-20.0)
        parts = g.geoms if g.geom_type == "MultiPolygon" else [g]
        out = [Polygon(q.exterior, [r for r in q.interiors if Polygon(r).area >= 1e4]) for q in parts if q.area > 0]
        return MultiPolygon(out) if len(out) > 1 else out[0]
    C["geometry"] = [tidy(g) if k == "管网" else g for g, k in zip(C.geometry, C.kind)]
    C["label_ll"] = None
    rp = C.geometry.representative_point().to_crs(4326)
    C["lab_lat"] = [round(p.y, 6) for p in rp]; C["lab_lon"] = [round(p.x, 6) for p in rp]
    C["unconverged"] = (C.hotspot_id == "ADM-90") & (C.kind == "地表")
    C.drop(columns=["label_ll"]).to_crs(4326).to_file(DATA / "c1_catch.geojson", driver="GeoJSON")
    # 现状泵站与雨无关；流水线只建 R96 的 L1，没有 R61 时用 R96
    rt = "R61" if (WORK / "c2d" / "pilot_C1_R61_L1.json").exists() else "R96"
    meta = json.loads((WORK / "c2d" / f"pilot_C1_{rt}_L1.json").read_text(encoding="utf-8"))
    ex = {s["ps_id"]: s for s in json.loads((ROOT / "out" / "c1_existing_pumps.json").read_text(encoding="utf-8"))["stations"]}
    feats = []
    xy = lambda k: tuple(map(float, k.split("_")[:2])) if k and k[0].isdigit() else None
    outs = {}
    txt = (WORK / f"C1_{rt}_L1.inp").read_text(encoding="utf-8", errors="ignore")
    sec = txt.split("[COORDINATES]")[1]
    for ln in sec.splitlines()[1:]:
        q = ln.split()
        if not q or q[0].startswith("["):
            break
        if q[0].startswith("X_"):
            outs[q[0]] = (float(q[1]), float(q[2]))
    built = set()
    for p in meta["pumps_1d"]:
        a = xy(p["from"]); b = xy(p["to"]) or outs.get(p["to"])
        built.add(p["ps"])
        pr = {"ps": p["ps"], "unitid": p["unitid"], "q_lps": round(p["q_m3s"] * 1000, 1), "n": p["n"], "q_each": p["q_each"], "q_src": p["q_src"],
              "wet": p["from"], "z_bot": round(p["z_bot"], 2), "inv_orig": round(p["inv_orig"], 2), "on": round(p["on"], 2), "off": round(p["off"], 2),
              "to": p["to"], "how": p["how"], "built": True}
        feats.append((Point(a), {**pr, "kind": "ps"}))
        if b and (abs(a[0] - b[0]) + abs(a[1] - b[1])) > 1:
            feats.append((LineString([a, b]), {**pr, "kind": "discharge"}))
    for k, s in ex.items():
        if k in built:
            continue
        feats.append((Point(s["x"], s["y"]), {"ps": k, "unitid": s["unitid"], "q_lps": 0, "q_src": s["q_src"], "how": "未建泵", "built": False, "kind": "ps"}))
    g = gpd.GeoDataFrame([p for _, p in feats], geometry=[f for f, _ in feats], crs=32640).to_crs(4326)
    g.to_file(DATA / "c1_ps_l1.geojson", driver="GeoJSON")
    print(f"c1_domain：{D.area_km2.iloc[0]} km²；c1_catch：{len(C)} 块；c1_ps_l1：泵站 {sum(1 for _, p in feats if p['kind'] == 'ps')}、排放线 {sum(1 for _, p in feats if p['kind'] == 'discharge')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
