"""汇水区单元 / 分析范围（本体 Catchment、AnalysisDomain，2026-09-29）。

起因：客户 flood 库 catchment.catchment 漏了 ADM-94 / 192 / 487，是用户看图发现的——本体里没有汇水区，没有规则能抓。
这里锁住：三套来源都装得上；三个点落在客户图层的 RB-3 里、且被「客户库漏登」规则抓到；C1 客户单元版漏 ADM-21；规划器把汇水区问法路由到 catchment_info。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pytest

from swbrain import drainage_units as DU
from swbrain.ontology import schema as S
from swbrain.ontology.common import ActionError

pytestmark = pytest.mark.skipif(not (ROOT / DU.SOURCES["client_layer"]["file"]).exists(), reason="客户单元描绘文件不在")


def _hs(fp):
    gj = json.loads((ROOT / "console" / "data" / "hotspots.geojson").read_text(encoding="utf-8"))
    f = next(f for f in gj["features"] if f["properties"]["hotspot_id"] == fp)
    return f["geometry"]["coordinates"]


@pytest.fixture(scope="module")
def units():
    return DU.load_units(ROOT)


def test_sources_load(units):
    by = {}
    for u in units.values():
        by[u["source"]] = by.get(u["source"], 0) + 1
    assert by.get("client_layer") == 3 and by.get("db39") == 39 and by.get("kmz", 0) >= 150
    assert by.get("model", 0) >= 22                                            # 22 个模型整片 + B_C 出水口分区
    rb3 = units["CL:147"]
    assert rb3["name"] == "RB-3" and rb3["title"] == "RAHA BEACH OUTFALL-3" and 250 < rb3["area_ha"] < 450


def test_three_priority_points_in_rb3_not_in_db(units):
    idx = DU.PointIndex(units)
    for fp in ("ADM-94", "ADM-192", "ADM-487"):
        ids = idx.containing(*_hs(fp))
        assert "CL:147" in ids, (fp, ids)
        assert not any(i.startswith("DB:") for i in ids), (fp, ids)       # 客户 flood 库确实漏了
        cs = [DU.public(units[i]) for i in ids]
        v = S.r_client_db_missing({"_catchments": cs}, {})
        assert v and ("RB3" in v["why"] or "RB-3" in v["why"])                  # 有模型时报模型名（RB3），否则报客户图层名（RB-3）


def test_client_db_missing_quiet_when_db_has_it():
    assert S.r_client_db_missing({"_catchments": [{"source": "client_layer", "name": "MBZ"}, {"source": "db39", "name": "Mussafah RD09"}]}, {}) is None
    assert S.r_client_db_missing({"_catchments": [{"source": "kmz", "name": "MBZ"}]}, {}) is None     # 没有客户图层就不判漏登


def test_uncovered_only_for_adm():
    assert S.r_catchment_uncovered({"lon": 54.5, "lat": 24.4, "region": "ADM", "_catchments": []}, {})
    assert S.r_catchment_uncovered({"lon": 55.7, "lat": 24.2, "region": "AAM", "_catchments": []}, {}) is None
    assert S.r_catchment_uncovered({"lon": 54.5, "lat": 24.4, "region": "ADM", "_catchments": [{"source": "kmz"}]}, {}) is None


def test_model_units_cover_priority_points(units):
    """2026-09-30 用户定以客户水力模型为准：三个重点热点在 RB3 模型里，ADM-21 在 B_C 的 O-2 分区里，都不在 flood 库 39 面里。"""
    idx = DU.PointIndex(units)
    for fp, want in (("ADM-94", "MODEL:RB3"), ("ADM-192", "MODEL:RB3"), ("ADM-487", "MODEL:RB3"), ("ADM-21", "MODEL:B_C:O-2")):
        ids = idx.containing(*_hs(fp))
        assert want in ids, (fp, ids)
    cs = [DU.public(units[i]) for i in idx.containing(*_hs("ADM-94"))]
    v = S.r_client_db_missing({"_catchments": cs}, {})
    assert v and "水力模型" in v["why"]


def test_c1m_domain_has_all_eight():
    from pyproj import Transformer
    from shapely.geometry import Point
    d = DU.load_domains(ROOT)["C1M"]
    T = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    assert all(d["_geom"].contains(Point(*T.transform(*_hs(fp)))) for fp in d["expected_fp_ids"])
    assert 36 < d["area_km2"] < 40


def test_c1u_domain_misses_adm21():
    from pyproj import Transformer
    from shapely.geometry import Point
    d = DU.load_domains(ROOT)["C1U"]
    T = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    out = [fp for fp in d["expected_fp_ids"] if not d["_geom"].contains(Point(*T.transform(*_hs(fp))))]
    assert out == ["ADM-21"], out
    assert 19 < d["area_km2"] < 22


def test_overlap_rule(units):
    ov = DU.overlaps_same_source(units, "CL:31")                                 # 融合后的客户单元之间不应重叠
    assert ov == []
    assert S.r_catchment_overlap({"_overlaps": [{"name": "X", "overlap_ha": 3.0}]}, {})["overlaps"]


def test_schema_declares_catchment_types_and_action():
    assert {"Catchment", "AnalysisDomain"} <= set(S.OT)
    assert {"catchments", "catchment_points", "catchment_assets", "domain_units", "domain_points"} <= set(S.LT)
    assert {"catchment_uncovered", "client_db_missing", "catchment_overlap", "domain_missing_expected"} <= set(S.RL)
    assert "catchment_info" in S.AT and S.AT["catchment_info"].side_effect == "none"
    assert S.check() == [] and S.check_runtime() == []


class _StubRt:
    def __init__(self, units):
        self._u = units

    def load_catchments(self):
        return {k: DU.public(v) for k, v in self._u.items()}

    def scenario_key(self, s):
        raise ActionError("no scenarios in stub")

    def zone_names(self):
        return []


@pytest.mark.parametrize("q,want", [
    ("ADM-94 在哪个汇水区？", ("catchment_info", {"fp_id": "ADM-94"})),
    ("RB-3 里有哪些积水点？", ("catchment_info", {"catchment": "RB-3"})),
    ("C1 客户单元版漏了哪些热点？", ("catchment_info", {"domain": "C1U"})),
    ("C1 模型版汇水区包含哪些热点？", ("catchment_info", {"domain": "C1M"})),
    ("Which catchment is ADM-192 in?", ("catchment_info", {"fp_id": "ADM-192"})),
])
def test_planner_routes_catchment_questions(units, q, want):
    from swbrain.ontology.agent import RulePlanner
    plan = RulePlanner(_StubRt(units)).plan(q)
    assert plan and plan[0][0] == want[0] and all(plan[0][1].get(k) == v for k, v in want[1].items()), plan
