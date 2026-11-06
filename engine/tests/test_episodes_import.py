"""客户文件 → 积水经历契约：事件报告表（猜列、坐标 / 地名匹配、未匹配行）、呼叫中心（按事件窗口归属、关键词过滤）、卫星洪涝面（面内的热点积水）。合成文件，不上网。"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd

from swbrain import episodes_import as EI
from tests.test_ontology import Fake


def _hs():
    return Fake.hotspots.copy()


def _xy(fp="ADM-1"):
    h = _hs(); r = h[h["hotspot_id"] == fp].iloc[0]
    return float(r["lon"]), float(r["lat"])


def test_incident_report_matches_by_coords_and_text(tmp_path):
    df = pd.DataFrame([
        {"Date & Time": "16/04/2024 09:40", "Location": "Al Shahama Tunnel entrance", "Latitude": _xy()[1] + 0.0002, "Longitude": _xy()[0] + 0.0001, "Description": "Road flooded, 2 pumps deployed", "Pumps": 2, "Status": "Closed"},
        {"Date & Time": "16/04/2024 10:15", "Location": "Al Shahama Tunnel", "Latitude": None, "Longitude": None, "Description": "Water level 40 cm", "Pumps": None, "Status": "Open"},
        {"Date & Time": "16/04/2024 11:00", "Location": "Somewhere far", "Latitude": 25.9, "Longitude": 55.9, "Description": "flooding", "Pumps": 1, "Status": "Open"},
    ])
    p = tmp_path / "Incidents Report April 2024.xlsx"; df.to_excel(p, index=False)
    res = EI.from_incident_report(p, "2024-04", _hs())
    roles = res["columns"]
    assert roles["time"] == "Date & Time" and roles["lat"] == "Latitude" and roles["lon"] == "Longitude" and roles["location"] == "Location" and roles["units"] == "Pumps"
    st = res["stats"]; assert st["rows"] == 3 and st["matched"] == 2 and st["by_coords"] == 1 and st["by_text"] == 1 and st["unmatched"] == 1
    r0 = res["rows"][0]; assert r0["fp_id"] == "ADM-1" and r0["flooded"] is True and r0["units"] == 2 and r0["stage"] == "reported" and r0["at"].startswith("2024-04-16") and r0["match"]["match_by"] == "coords"
    r1 = res["rows"][1]; assert r1["fp_id"] == "ADM-1" and r1["match"]["match_by"] == "text" and "units" not in r1
    assert res["unmatched"][0]["text"].startswith("Somewhere far")
    # UTM 40N 坐标也认
    from pyproj import Transformer
    ex, ny = Transformer.from_crs(4326, 32640, always_xy=True).transform(_xy()[0] + 0.0001, _xy()[1] + 0.0002)
    df2 = pd.DataFrame([{"Date": "2024-04-16", "Easting": round(ex, 1), "Northing": round(ny, 1), "Details": "flood", "Water Level": 25}])
    p2 = tmp_path / "utm.xlsx"; df2.to_excel(p2, index=False)
    res2 = EI.from_incident_report(p2, "2024-04", _hs(), radius_m=2000)
    assert res2["columns"]["x"] == "Easting" and res2["columns"]["y"] == "Northing" and res2["rows"] and res2["rows"][0]["match"]["coords"] == "utm40n" and res2["rows"][0]["depth_m"] == 0.25
    out = EI.write_or_push(res, tmp_path / "ep.json", False, None)
    j = json.loads((tmp_path / "ep.json").read_text(encoding="utf-8"))
    assert j["schema"] == "swbrain.episodes/1" and len(j["rows"]) == 2 and j["stats"]["matched"] == 2 and out["written"].endswith("ep.json")


def test_call_center_assigns_events_and_filters(tmp_path):
    events = {"2024-04": {"start": "2024-04-15", "end": "2024-04-18"}, "2026-03": {"start": "2026-03-22", "end": "2026-03-28"}}
    df = pd.DataFrame([
        {"Ticket": "T1", "Created": "2024-04-16 08:00", "Address": "Al Shahama Tunnel", "Complaint": "Rain water accumulation on road", "Lat": _xy()[1], "Lon": _xy()[0]},
        {"Ticket": "T2", "Created": "2024-06-01 08:00", "Address": "Al Shahama Tunnel", "Complaint": "flooding", "Lat": _xy()[1], "Lon": _xy()[0]},        # 窗口外
        {"Ticket": "T3", "Created": "2026-03-23 12:00", "Address": "Al Shahama Tunnel", "Complaint": "Street light broken", "Lat": _xy()[1], "Lon": _xy()[0]},  # 与积水无关
        {"Ticket": "T4", "Created": "2026-03-23 12:00", "Address": "Unknown place", "Complaint": "flood water", "Lat": None, "Lon": None},
    ])
    p = tmp_path / "800555 Call Center.xlsx"; df.to_excel(p, index=False)
    res = EI.from_call_center(p, events, _hs())
    assert res["stats"]["matched"] == 1 and res["stats"]["unmatched"] == 1 and res["stats"]["outside_event_windows"] == 1
    r = res["rows"][0]; assert r["fp_id"] == "ADM-1" and r["event_id"] == "2024-04" and r["stage"] == "reported" and r["source"] == "call_center" and r["id"].startswith("CC-2024-04-T1")


def test_flood_polygons_mark_hotspots_inside(tmp_path):
    import geopandas as gpd
    from shapely.geometry import Polygon
    x, y = _xy(); poly = Polygon([(x - 0.001, y - 0.001), (x + 0.001, y - 0.001), (x + 0.001, y + 0.001), (x - 0.001, y + 0.001)])          # 包住 ADM-1，不包 ADM-2
    g = gpd.GeoDataFrame({"name": ["flood"]}, geometry=[poly], crs=4326)
    p = tmp_path / "flood.geojson"; g.to_file(p, driver="GeoJSON")
    res = EI.from_flood_polygons(p, "2024-04", _hs(), buffer_m=30, date="2024-04-19")
    assert res["stats"]["flooded"] == 1 and res["rows"][0]["fp_id"] == "ADM-1" and res["rows"][0]["source"] == "satellite" and res["rows"][0]["at"] == "2024-04-19" and res["rows"][0]["flooded"] is True
    assert not any(r["fp_id"] == "ADM-2" for r in res["rows"])
