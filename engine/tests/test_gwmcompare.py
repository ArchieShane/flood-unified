"""三方对照：合成推演多边形 × 客户热点 × 本系统点的归类与统计；文件挑选按重现期、再按最近雨量。"""
import json

import pandas as pd

from swbrain import gwmcompare as gc


def _sq(lon, lat, half=0.001):
    return {"type": "Polygon", "coordinates": [[[lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half], [lon - half, lat + half], [lon - half, lat - half]]]}


def test_compare_tags_and_stats():
    fp = {"run_id": "r1", "points": [
        {"fp_id": "G1", "geometry": _sq(54.50, 24.40), "lon": 54.50, "lat": 24.40, "max_depth_m": 0.8, "volume_m3": 1000, "area_m2": 40000, "gwm": {"first_wet_min": 600, "peak_min": 900}},
        {"fp_id": "G2", "geometry": _sq(54.60, 24.40), "lon": 54.60, "lat": 24.40, "max_depth_m": 0.5, "volume_m3": 500, "area_m2": 40000},
        {"fp_id": "G3", "geometry": _sq(54.70, 24.40), "lon": 54.70, "lat": 24.40, "max_depth_m": 0.4, "volume_m3": 300, "area_m2": 40000},
        {"fp_id": "G4", "lon": 54.80, "lat": 24.40, "max_depth_m": 0.4, "volume_m3": 700, "area_m2": 40000}]}          # G4 没几何：等面积圆
    hs = pd.DataFrame([{"hotspot_id": "ADM-1", "lon": 54.5002, "lat": 24.4001, "criticality": "High"},     # G1 内
                       {"hotspot_id": "ADM-2", "lon": 54.6025, "lat": 24.40, "criticality": "Low"},        # G2 边外约 150 m
                       {"hotspot_id": "ADM-3", "lon": 54.9, "lat": 24.6, "criticality": "Medium"},         # 远
                       {"hotspot_id": "ADM-4", "lon": None, "lat": None, "criticality": "Low"}])           # 无坐标
    sysfp = {"run_id": "s", "scenario": {"name": "10"}, "points": [{"fp_id": "S1", "lon": 54.50, "lat": 24.40}, {"fp_id": "S2", "lon": 54.70, "lat": 24.40}, {"fp_id": "S3", "lon": 55.0, "lat": 24.0}]}
    tags, st, hits = gc.compare(fp, hs, sysfp, 300.0)
    assert {t["fp_id"]: t["agreement"] for t in tags} == {"G1": "both", "G2": "hotspot", "G3": "system", "G4": "model_only"}
    assert tags[0]["hs_inside_n"] == 1 and tags[1]["hs_inside_n"] == 0 and tags[0]["sys_inside_n"] == 1
    H = st["hotspots"]
    assert H["n"] == 3 and H["covered"] == 2 and H["inside"] == 1 and H["outside_grid"] is None and H["in_grid"] is None
    assert H["by_criticality"]["High"]["covered"] == 1 and H["by_criticality"]["Medium"]["covered"] == 0 and H["by_criticality"]["Low"]["n"] == 1
    assert st["system"]["covered"] == 2 and st["gwm"]["agreement"] == {"both": 1, "hotspot": 1, "system": 1, "model_only": 1} and st["gwm"]["model_only_volume_m3"] == 700
    assert st["gwm"]["with_hotspot"] == 2 and st["gwm"]["with_system"] == 2
    assert {h["hotspot_id"] for h in hits} == {"ADM-1", "ADM-2"} and [h for h in hits if h["hotspot_id"] == "ADM-1"][0]["dist_m"] == 0
    gj = gc.to_geojson(fp, tags)
    f0, f3 = gj["features"][0]["properties"], gj["features"][3]
    assert len(gj["features"]) == 4 and f0["hs_ids"] == ["ADM-1"] and f0["first_wet_min"] == 600 and f3["properties"]["agreement"] == "model_only" and f3["geometry"]["type"] == "Point"
    # 只有推演、没有热点也没有本系统点
    tags2, st2, hits2 = gc.compare(fp, None, None, 300.0)
    assert all(t["agreement"] == "model_only" for t in tags2) and "hotspots" not in st2 and hits2 == []


def test_gwm_files_and_pick(tmp_path, monkeypatch):
    def w(name, rp, mm):
        (tmp_path / name).write_text(json.dumps({"schema": "x", "run_id": name, "model": {"type": "world"}, "scenario": {"return_period_yr": rp, "rain_depth_mm": mm}, "points": []}), encoding="utf-8")
    w("flood_points.gwm-a.json", 50, 90.0); w("flood_points.gwm-b.json", 50, 81.8); w("flood_points.gwm-c.json", 10, 55.0)
    (tmp_path / "flood_points.system-10.json").write_text(json.dumps({"model": {"type": "system"}, "scenario": {}, "points": []}), encoding="utf-8")
    (tmp_path / "flood_points.broken.json").write_text("{not json", encoding="utf-8")
    files = gc.gwm_files({"exchange": {"dir": "x"}}, tmp_path)
    assert len(files) == 3 and sorted(f["rp"] for f in files) == [10, 50, 50]
    tbl = {"5": {"p24_mm": 44.9, "return_period_yr": 5}, "50": {"p24_mm": 81.8, "return_period_yr": 50}, "10cc": {"p24_mm": 61.3}}
    monkeypatch.setattr(gc.scenarios, "get", lambda cfg, key: tbl[str(key)])
    f, how = gc.pick_file({}, "50", files); assert f["run_id"] == "flood_points.gwm-b.json" and how == "return_period"      # 同重现期取雨量最接近
    f, how = gc.pick_file({}, "10cc", files); assert f["run_id"] == "flood_points.gwm-c.json" and how == "nearest_rain"    # 无重现期取雨量最接近
    f, how = gc.pick_file({}, "5", files); assert f["run_id"] == "flood_points.gwm-c.json" and how == "nearest_rain"       # 没有 5 年的推演
    assert gc.pick_file({}, "50", []) == (None, "none")
