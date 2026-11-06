"""C1 热点群交付里的 8 个积水点（`sim_c1.extract_hotspots`）：坐标对上台账、逐态数与交付统计值一致。"""

import json
from pathlib import Path

import pytest

from stormwater import sim_c1

ROOT = Path(__file__).resolve().parents[1] / "data" / "temp260929" / "C1_61mm2h"
STATES = list(sim_c1.STATES)

pytestmark = pytest.mark.skipif(not (ROOT / "统计值" / "C1统计值.xlsx").is_file(), reason="没有 C1 交付")


@pytest.fixture(scope="module")
def hot(tmp_path_factory):
    out = sim_c1.extract_hotspots(ROOT, tmp_path_factory.mktemp("c1"), STATES)
    assert out is not None
    return json.loads(out.read_text(encoding="utf-8"))


def test_eight_points_matched_to_ledger(hot):
    ids = [p["id"] for p in hot["points"]]
    assert ids == ["ADM-21", "ADM-90", "ADM-94", "ADM-104", "ADM-192", "ADM-202", "ADM-486", "ADM-487"]
    assert all(24.3 < p["lat"] < 24.5 and 54.4 < p["lon"] < 54.7 for p in hot["points"])
    assert len(hot["catchments"]["features"]) == 16


def test_stats_match_delivery_summary(hot):
    # 摘要.md：L1 八个热点并集 ≥0.30 m 25.5 ha、200 m 内最深 0.65 m、最大积水量 650,980 m³；L2+suggested 0.40 m
    t = hot["total"]
    assert t["L1"]["catchment"]["area_ge_0_30_ha"] == 25.5
    assert t["L1"]["catchment"]["max_volume_m3"] == 650980
    assert t["L1"]["near_200m"]["max_depth_m"] == 0.65
    assert t["L2+suggested"]["near_200m"]["max_depth_m"] == 0.402
    assert set(hot["points"][0]["stats"]) == set(STATES)


def test_adm90_surface_catchment_flagged_unconverged(hot):
    assert next(p for p in hot["points"] if p["id"] == "ADM-90")["catchment_ha"]["unconverged"] is True


def test_zone_rows_present(hot):
    # 报告表最后一行：全域管网服务区 ≥0.30 m 面积 L1 7.39 → L2+suggested 6.06 ha
    assert hot["zones"]["L1"]["network_zone"]["area_ge_0_30_ha"] == 7.39
    assert hot["zones"]["L2+suggested"]["network_zone"]["area_ge_0_30_ha"] == 6.06
