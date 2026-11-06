"""RDA 全套成果读取层：数字与交付文件的 README 摘要逐项对得上。成果目录不在时整体 skip。"""

import pytest
from fastapi.testclient import TestClient

from stormwater import rda
from stormwater.api.main import app

pytestmark = pytest.mark.skipif(not rda.available(), reason="没有 RDA 成果目录")

client = TestClient(app)


def test_overview_matches_readme():
    ov = client.get("/api/rda/overview").json()
    by = {s["key"]: s for s in ov["states"]}
    # README「本场结果摘要」：热点达标 /11、积水点两档、设施图层要素
    assert [by[k]["hotspots_passed"] for k in ("L1", "L2", "L2P", "OPTCS2")] == [1, 1, 1, 6]
    assert [by[k]["ponding_count"]["0.15"] for k in ("L1", "L2", "L2P", "OPTCS2")] == [67, 67, 69, 30]
    assert [by[k]["ponding_count"]["0.30"] for k in ("L1", "L2", "L2P", "OPTCS2")] == [22, 22, 22, 6]
    assert by["OPTCS2"]["hotspots_total"] == 11
    assert ov["suggested_measures"] == 60


def test_ponding_joins_l3_only_for_015():
    p = client.get("/api/rda/states/OPTCS2/ponding?depth=0.15").json()
    assert p["count"] == 30 and p["not_receded"] == 6
    first = p["points"][0]
    assert first["id"] == "SIM-OPTCS2-001" and first["l3"]["level"] == "高"
    assert client.get("/api/rda/states/OPTCS2/ponding?depth=0.30").json()["points"][0]["l3"] is None
    assert client.get("/api/rda/states/OPTCS2/ponding?depth=0.2").status_code == 404


def test_suggested_ledger_and_compliance():
    s = client.get("/api/rda/suggested").json()
    assert len(s["measures"]) == 60
    assert s["by_type"]["total"]["数量"] == 60
    assert round(sum(m["cost_usd"] for m in s["measures"]) / 1e6, 2) == 133.74
    c = client.get(f"/api/rda/suggested/{s['measures'][0]['id']}/compliance").json()
    assert c["checks"] and sum(c["tally"].values()) == len(c["checks"])


def test_max_depth_lonlat_in_abu_dhabi():
    d = client.get("/api/rda/states/OPTCS2/max-depth").json()
    assert d["count"] > 0 and abs(d["max_m"] - 2.11) < 0.01
    assert 54.2 < min(d["lon"]) and max(d["lon"]) < 54.5 and 24.3 < min(d["lat"]) and max(d["lat"]) < 24.6


def test_unknown_state_404():
    assert client.get("/api/rda/states/XX/ponding").status_code == 404


def test_utm_roundtrip():
    import numpy as np
    lon, lat = np.array([54.34, 54.33]), np.array([24.46, 24.455])
    x, y = rda._lonlat_to_utm40(lon, lat)
    lo2, la2 = rda._utm40_to_lonlat(x, y)
    assert np.allclose(lo2, lon, atol=1e-7) and np.allclose(la2, lat, atol=1e-7)


def test_frames_and_diff():
    fi = client.get("/api/rda/states/OPTCS2/frames").json()
    assert len(fi["t_h"]) == 132 and max(fi["dmax_m"]) > 2.0
    g = client.get("/api/rda/grid").json()
    fr = client.get("/api/rda/states/OPTCS2/frames/10").json()
    assert fr["cells"] and max(fr["cells"]) < g["count"]
    d = client.get("/api/rda/states/OPTCS2/frames/20?mode=diff&base=L2P").json()
    assert len(d["cells"]) == len(d["v"])
    assert client.get("/api/rda/states/OPTCS2/frames/20?mode=diff").status_code == 422


def test_hotspot_series_and_totals():
    s = client.get("/api/rda/hotspots/series").json()
    assert len(s["hotspots"]) == 11 and len(s["states"]["OPTCS2"]["ADM-75"]["depth_m"]) == 132
    ov = client.get("/api/rda/overview").json()
    assert ov["new_outfalls"] >= 1 and ov["states"][3]["facility_totals"]


@pytest.mark.parametrize("key", ["L1", "L2", "L2P", "OPTCS2"])
@pytest.mark.parametrize("depth", ["0.15", "0.30"])
def test_custom_ponding_reproduces_delivered_list(key, depth):
    """成果的默认阈值（0.15 / 0.30 m、100 m²、0.5 h）重算，点数、编号顺序与逐点数字都要与交付清单一致。"""
    mine = rda.ponding_custom(key, float(depth), 100.0, 0.5)["points"]
    ref = rda.ponding(key, depth)["points"]
    assert [p["id"] for p in mine] == [p["id"] for p in ref]
    for m, r in zip(mine, ref):
        assert abs(m["vol_peak_m3"] - r["vol_peak_m3"]) <= max(2, 0.003 * r["vol_peak_m3"]), r["id"]
        assert abs(m["area_m2"] - r["area_m2"]) <= max(5, 0.005 * r["area_m2"]), r["id"]
        assert m["receded"] == r["receded"] and abs(m["hmax_m"] - r["hmax_m"]) <= 0.01


def test_custom_ponding_endpoint_and_limits():
    d = client.get("/api/rda/states/OPTCS2/ponding/custom?depth=0.2&min_area=200&min_dur=1").json()
    assert d["custom"] and d["count"] > 0 and d["points"][0]["cells"]
    assert client.get("/api/rda/states/OPTCS2/ponding/custom?depth=9").status_code == 422
    deeper = client.get("/api/rda/states/OPTCS2/ponding/custom?depth=0.5").json()["count"]
    assert deeper <= client.get("/api/rda/states/OPTCS2/ponding/custom?depth=0.1").json()["count"] + 10
