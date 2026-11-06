"""客户热点清单：清洗规则、匹配加权、预案 A 泵队生成（不依赖客户文件）。"""
import numpy as np
import pandas as pd
from swbrain import hotspots as hs


def test_cleaners():
    assert hs._latlon("24.516553,55.498937", None) == (24.516553, 55.498937)
    assert hs._latlon(" 24.260988°", "55.7°") == (24.260988, 55.7)
    assert hs._latlon("abc", "1") == (np.nan, np.nan) or all(np.isnan(v) for v in hs._latlon("abc", "1"))
    assert hs._crit("very high") == "Very High" and hs._crit("Medium   ") == "Medium" and hs._crit("x") is None
    assert hs._yes("Yes") is True and hs._yes("NO") is False and hs._yes("N/A") is False and hs._yes(None) is None
    assert hs._root_cause("Absense of Drainage Network") == "no_network"
    assert hs._root_cause("Inadequate design capacity") == "capacity" and hs._root_cause("The network needs an upgrade") == "capacity"
    assert hs._root_cause("Topography & Road Engineering") == "topography"


def _hs():
    df = pd.DataFrame([
        {"region": "ADM", "seq": 1, "hotspot_id": "ADM-1", "area": "Zayed", "location": "u1", "lat": 24.40, "lon": 54.50, "criticality": "Very High", "network_available": True, "root_cause": "capacity",
         "planA_pump12": 2, "planA_pump8": 1, "planA_pump6": 7, "planA_tankers": 1, "planA_manpower": 3, "planA_budget_aed": 1e5, "idd_planned_flag": "yes", "idd_stage": "Construction Phase", "idd_owner": "X"},
        {"region": "ADM", "seq": 2, "hotspot_id": "ADM-2", "area": "Zayed", "location": "u2", "lat": 24.41, "lon": 54.51, "criticality": "Medium", "network_available": False, "root_cause": "no_network",
         "planA_pump12": 0, "planA_pump8": 0, "planA_pump6": 2, "planA_tankers": 2, "planA_manpower": 1, "planA_budget_aed": 2e4, "idd_planned_flag": "no", "idd_stage": None, "idd_owner": None},
        {"region": "AAM", "seq": 1, "hotspot_id": "AAM-1", "area": "Hili", "location": "a1", "lat": 24.30, "lon": 55.80, "criticality": "High", "network_available": True, "root_cause": "capacity",
         "planA_pump12": 1, "planA_pump8": 0, "planA_pump6": 0, "planA_tankers": 3, "planA_manpower": 0, "planA_budget_aed": 5e4, "idd_planned_flag": "no", "idd_stage": None, "idd_owner": None},
    ])
    df["planA_pumps"] = df["planA_pump12"] + df["planA_pump8"] + df["planA_pump6"]
    return df


CFG = {"hotspots": {"match_radius_m": 300, "criticality_weight": {"Very High": 6, "High": 4, "Medium": 2, "Low": 1}, "six_inch_bank": 3, "tanker_capacity_m3": 17.0,
                    "pump_models": {"12in": {"model": "diesel-12in", "rate_m3h": 700, "head_m": 15, "hose_m": 100, "setup_min": 25, "crew": 3, "weight_t": 8.0, "vehicle_class": "heavy", "duty_aed_h": 90},
                                    "8in": {"model": "diesel-8in", "rate_m3h": 350, "head_m": 15, "hose_m": 100, "setup_min": 20, "crew": 2, "weight_t": 4.0, "vehicle_class": "light", "duty_aed_h": 60},
                                    "6in": {"model": "diesel-6in", "rate_m3h": 180, "head_m": 12, "hose_m": 60, "setup_min": 15, "crew": 2, "weight_t": 2.0, "vehicle_class": "light", "duty_aed_h": 36}}}}


def test_priority_weights_and_match():
    df = _hs()
    w, ids, crit = hs.priority_weights([54.5002, 54.60], [24.4001, 24.40], df, CFG)    # 第一个点在 ADM-1 30 m 内，第二个点 10 km 外
    assert w.tolist() == [6.0, 0.0] and ids == ["ADM-1", None] and crit == ["Very High", None]


def test_annotate_proposals():
    df = _hs()
    props = pd.DataFrame({"pid": [1, 2], "lon": [54.5001, 54.9], "lat": [24.4, 24.4], "kind": ["扩管改造", "扩管改造"]})
    p = hs.annotate_proposals(props, df, CFG)
    assert p.loc[0, "client_hotspot_id"] == "ADM-1" and "IDD 已计划" in p.loc[0, "client_hotspot"] and "Construction Phase" in p.loc[0, "client_hotspot_en"]
    assert pd.isna(p.loc[1, "client_hotspot_id"]) and pd.isna(p.loc[1, "client_hotspot"])


def test_fleet_from_plan_a():
    df = _hs()
    fac = pd.DataFrame({"kind": ["civil_defense", "hospital"], "name": ["CD Zayed", "H"], "lon": [54.505, 54.6], "lat": [24.405, 24.5], "size": [0, 0], "sub": [None, None]})
    fleet = hs.fleet_from_plan_a(df, fac, CFG)
    assert fleet["schema"] == "swbrain.fleet/1" and fleet["simulated"] is False and len(fleet["depots"]) == 1     # 只取 ADM，一个片区
    d = fleet["depots"][0]; assert d["plan_a"] == {"pump12": 2, "pump8": 1, "pump6": 9, "tankers": 3, "manpower": 4, "hotspots": 2} and d["name"].startswith("CD Zayed")
    models = {u["model"] for u in fleet["units"]}
    assert len(fleet["units"]) == 2 + 1 + 3 and models == {"diesel-12in", "diesel-8in", "diesel-6in-x3"}       # 9 台 6 寸 → 3 组
    m6 = next(m for m in fleet["models"] if m["model"] == "diesel-6in-x3"); assert m6["rate_m3h"] == 540


def test_contract_roundtrip(tmp_path):
    df = _hs(); p = hs.write_contract(df, tmp_path / "h.json", "test")
    back = hs.load(p)
    assert len(back) == 3 and set(back["hotspot_id"]) == {"ADM-1", "ADM-2", "AAM-1"} and back.attrs["source"] == "test"
    s = hs.stats(back)
    assert s["n"] == 3 and s["by_criticality"]["Very High"] == 1 and s["planA"]["pumps"] == 13 and s["idd_planned"] == 1
    gj = hs.to_geojson(back); assert len(gj["features"]) == 3 and gj["features"][0]["properties"]["criticality_zh"] == "极高"


def test_apply_kmz_coords_fixes_row_shift(tmp_path):
    """v4.4 表第 n 行填的是第 n+1 个点的坐标；KMZ 按序号覆盖后应对齐，并报出错行数。"""
    import zipfile
    pts = {1: (54.691, 24.546), 2: (54.649, 24.493), 3: (54.645, 24.486)}
    pm = "".join(f'<Placemark><name>{k}</name><ExtendedData><SchemaData><SimpleData name="م">{k}</SimpleData><SimpleData name="الوصف">d{k}</SimpleData>'
                 f'<SimpleData name="المركز">c</SimpleData><SimpleData name="الاولوية">Very Important</SimpleData></SchemaData></ExtendedData>'
                 f'<Point><coordinates>{v[0]},{v[1]},0</coordinates></Point></Placemark>' for k, v in pts.items())
    kml = f'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder>{pm}<Placemark><name>poly</name><Polygon><outerBoundaryIs><LinearRing><coordinates>54,24,0 54.1,24,0 54.1,24.1,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Folder></Document></kml>'
    kmz = tmp_path / "x.kmz"
    with zipfile.ZipFile(kmz, "w") as z:
        z.writestr("doc.kml", kml)
    df = pd.DataFrame([{"region": "ADM", "seq": 1, "hotspot_id": "ADM-1", "lon": 54.649, "lat": 24.493},      # 错一行：拿的是 2 号的坐标
                       {"region": "ADM", "seq": 2, "hotspot_id": "ADM-2", "lon": 54.645, "lat": 24.486},
                       {"region": "ADM", "seq": 9, "hotspot_id": "ADM-9", "lon": 54.0, "lat": 24.0},           # KMZ 里没有
                       {"region": "AAM", "seq": 1, "hotspot_id": "AAM-1", "lon": 55.7, "lat": 24.2}])         # 其他区不动
    out, st = hs.apply_kmz_coords(df, kmz, "ADM")
    assert st["kmz_points"] == 3 and st["matched"] == 2 and st["unmatched_in_xlsx"] == 1 and st["xlsx_row_shift_plus1"] == 2
    a = out.set_index("hotspot_id")
    assert (a.at["ADM-1", "lon"], a.at["ADM-1", "lat"]) == pts[1] and a.at["ADM-1", "coord_source"] == "kmz" and a.at["ADM-1", "lon_xlsx"] == 54.649
    assert a.at["ADM-9", "coord_source"] == "xlsx" and a.at["AAM-1", "lon"] == 55.7 and a.at["ADM-2", "desc_ar"] == "d2"
    assert st["moved_median_m"] > 200
