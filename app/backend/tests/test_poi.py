"""仿真溢水点按最近 POI 起名（poi.py + sim_ponding.name_clusters）。"""

import csv

import pytest

from stormwater import poi, sim_ponding
from stormwater.i18n import tr


@pytest.fixture
def poi_csv(tmp_path, monkeypatch):
    p = tmp_path / "poi_google.csv"
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=poi.FIELDS)
        w.writeheader()
        w.writerow({"objectid": 1, "name": "Al Bateen Mall", "type": "shopping_mall", "region": "Abu Dhabi",
                    "x": 1000.0, "y": 2000.0})
        w.writerow({"objectid": 2, "name": "مقبرة", "type": "cemetery", "region": "Abu Dhabi",
                    "x": 5000.0, "y": 2000.0})
    monkeypatch.setattr(poi, "POI_CSV", p)
    poi._index.cache_clear()
    yield p
    poi._index.cache_clear()


def test_nearest_picks_closest_and_rounds(poi_csv):
    hit = poi.nearest(1030.0, 2040.4)
    assert hit["objectid"] == 1
    assert hit["distance_m"] == 50
    assert hit["label"] == "Overflow point 50 m from Al Bateen Mall"


def test_name_clusters(poi_csv):
    out = {"clusters": [{"x": 4900.0, "y": 2000.0}, {"x": None, "y": None}]}
    sim_ponding.name_clusters(out)
    a, b = out["clusters"]
    assert a["name"] == "Overflow point 100 m from ⁨مقبرة⁩"   # 阿拉伯文名套方向隔离符
    assert a["poi"] == {"objectid": 2, "name": "مقبرة", "type": "cemetery", "distance_m": 100}
    assert b["name"] is None and b["poi"] is None


def test_no_csv_means_no_name(tmp_path, monkeypatch):
    monkeypatch.setattr(poi, "POI_CSV", tmp_path / "missing.csv")
    poi._index.cache_clear()
    try:
        assert poi.nearest(0.0, 0.0) is None
    finally:
        poi._index.cache_clear()


def test_label_translates_at_api_boundary():
    zh = poi.label("Premier Motors", 15.2)
    assert tr(zh, "zh") == "Overflow point 15 m from Premier Motors"
    assert tr(zh, "en") == "Overflow point 15 m from Premier Motors"
