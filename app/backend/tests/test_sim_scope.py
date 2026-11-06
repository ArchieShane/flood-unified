"""C1 口径拆分（`sim_scope`）：全域与交付 / 地图对得上，口径之间守恒。需要已抽好的 `processed/sim_deliveries/C1_61mm2h`。"""

import json
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
HOT = BACKEND / "data" / "processed" / "sim_deliveries" / "C1_61mm2h" / "hotspots.json"

pytestmark = pytest.mark.skipif(not HOT.is_file(), reason="C1 交付还没抽")


@pytest.fixture(scope="module")
def scopes():
    d = json.loads(HOT.read_text(encoding="utf-8"))
    if "scopes" not in d:
        pytest.skip("hotspots.json 里还没有 scopes（重跑 sim_c1 extract）")
    return d["scopes"]


def test_domain_matches_delivery(scopes):
    # 交付「一维：域内井溢流」465,814（L1）；节点归属按栅格，差在边缘几口井，容差 0.1%
    assert scopes["domain"]["L1"]["overflow_m3"] == pytest.approx(465814, rel=1e-3)
    # 地图「溢水点（N）」：L1 1056、L2+suggested 1021
    assert scopes["domain"]["L1"]["ponding"] == 1056
    assert scopes["domain"]["L2+suggested"]["ponding"] == 1021
    assert scopes["domain"]["L1"]["max_volume_m3"] == 815972


def test_only_three_scopes(scopes):
    assert list(scopes) == ["domain", "catchment", "near_200m"]


def test_subscopes_inside_domain(scopes):
    for st, d in scopes["domain"].items():
        for s in ("catchment", "near_200m"):
            assert scopes[s][st]["ponding"] <= d["ponding"]
            assert scopes[s][st]["overflow_m3"] <= d["overflow_m3"]
