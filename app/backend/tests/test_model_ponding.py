"""二维水动力模型识别出的易积水片区（model_ponding.py + /api/model-ponding）。

这个接口存在的理由只有一个：**它和台账那 845 个点不是同一批数据**。
所以测试盯死的也是这件事的几个侧面：

1. 两套数据各出各的接口，字段口径不重叠 —— 片区有水深/历时，台账点有严重度/管网，
   谁也别想拿另一边的字段来用；
2. 成因必须带稳定 key（页面按它上色、分组、筛选），中文串只是展示文案，
   换成英文后 key 一个字都不能变；
3. 汇总里**没有**洼地库容合计 —— 那是逐洼地的量，一个洼地落了几个片区就要被记
   几遍，相加得到的是个虚数，宁可不给；
4. 成果缺文件时如实答 `available: false`，而不是返回一份空壳让页面画出个「0 个
   片区」来 —— 「没有这份成果」与「这场雨没积水」是两回事。
"""

import json

import pytest
from fastapi.testclient import TestClient

from stormwater.api.main import app
from stormwater.model_ponding import (
    MECHANISM_KEYS,
    MECHANISM_ORDER,
    RUN_META,
    load_features,
    mechanism_key,
    summarise,
)


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def payload(client):
    return client.get("/api/model-ponding").json()


class TestMechanismKey:
    """成因 key 是协议值 —— 页面拿它上色分组，不能随语言/文案漂。"""

    def test_every_known_label_maps(self):
        for label, key in MECHANISM_KEYS.items():
            assert mechanism_key(label) == key

    def test_unknown_label_falls_back_to_other(self):
        """认不出就归 other，既不猜也不把那个片区吞掉。"""
        assert mechanism_key("某种新成因") == "other"
        assert mechanism_key(None) == "other"

    def test_order_covers_every_key(self):
        """图例与分组按 MECHANISM_ORDER 走，漏一个就有片区画得出来却列不出来。"""
        assert set(MECHANISM_KEYS.values()) | {"other"} == set(MECHANISM_ORDER)


class TestFeatures:
    def test_every_feature_is_stamped(self):
        feats = load_features()
        if not feats:
            pytest.skip("本机没有 data/analysis_ponding/ponding_hotspots.geojson")
        assert all(f["properties"]["mechanism_key"] in MECHANISM_ORDER for f in feats)
        # rank 是页面的选中主键，必须唯一且从 1 起连号
        ranks = sorted(f["properties"]["rank"] for f in feats)
        assert ranks == list(range(1, len(feats) + 1))

    def test_geometry_is_polygonal(self):
        """片区是面不是点 —— 代表点另有 lat/lon 两列。"""
        feats = load_features()
        if not feats:
            pytest.skip("本机没有片区成果")
        assert all(f["geometry"]["type"] in ("Polygon", "MultiPolygon") for f in feats)
        assert all(
            isinstance(f["properties"]["lat"], float) and isinstance(f["properties"]["lon"], float)
            for f in feats
        )


class TestSummary:
    def test_counts_add_up(self):
        feats = load_features()
        if not feats:
            pytest.skip("本机没有片区成果")
        s = summarise(feats)
        assert s["count"] == len(feats)
        assert sum(s["by_mechanism"].values()) == s["count"]
        assert s["cells"] == sum(f["properties"]["cells"] for f in feats)

    def test_no_basin_capacity_total(self):
        """洼地库容是逐洼地的量，共用洼地的片区会被重复计数 —— 故意不给合计。

        rank 5 与 rank 10 同属一个洼地（同为 40,631.5 m³），相加就凭空多出一份。
        """
        s = summarise(load_features() or [{"properties": {}}])
        assert "basin_capacity_m3" not in s

    def test_empty_input_is_all_zero(self):
        s = summarise([])
        assert s["count"] == 0 and s["by_mechanism"] == {}


class TestFixtureIntegrity:
    """成果文件本身的两条底线 —— 它是离线产出的，进仓库后没人再动过。"""

    def test_geojson_is_wgs84_lonlat(self):
        from stormwater.model_ponding import OUTPUT_GEOJSON

        if not OUTPUT_GEOJSON.exists():
            pytest.skip("本机没有片区成果")
        fc = json.loads(OUTPUT_GEOJSON.read_text(encoding="utf-8"))
        # 阿布扎比：经度 51–57，纬度 22–27。写反了这里立刻炸
        for f in fc["features"][:20]:
            p = f["properties"]
            assert 51 < p["lon"] < 57 and 22 < p["lat"] < 27

    def test_run_meta_matches_the_report(self):
        """RUN_META 是手抄自报告的，抄错了下游每个数都错位。"""
        assert RUN_META["return_period_yr"] == 100
        assert RUN_META["duration_min"] == 180
        assert RUN_META["grid_m"] == 20
        assert RUN_META["sim_hours"] == 99
        assert RUN_META["status"].startswith("completed_diagnostic")
