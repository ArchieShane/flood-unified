"""从耦合演算成果重算易积水片区（sim_ponding.py）。

总闸是**复现文档**：把 Al Bateen RP100 的原始成果喂进去，107 个片区的成因、代表格、
各项指标、积水指数与名次必须与 `docs/analysis_ponding/ponding_hotspots.csv` 逐个对上，
且不出任何偏离 key —— 输入齐全时本模块就是文档那套算法，一处都没改。

另外几条盯的是降级路径的诚实：缺输入可以算，但必须把替代写进 `deviations`；
小网格上的规则树每条分支各造一个最小算例。原始成果不在本机时相应用例 skip。
"""

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from stormwater import sim_ponding as sp

BACKEND = Path(__file__).resolve().parents[1]
AL_BATEEN = BACKEND / "data" / "al-bateen-library-rp100-20m-20260920T122942Z-96h-priority"
DOC_CSV = BACKEND.parent / "docs" / "analysis_ponding" / "ponding_hotspots.csv"

needs_al_bateen = pytest.mark.skipif(
    not (AL_BATEEN / "terrain_grid.npz").is_file() or not DOC_CSV.is_file(),
    reason="Al Bateen 原始成果或文档 CSV 不在本机",
)


@pytest.fixture(scope="module")
def al_bateen():
    return sp.compute(AL_BATEEN)


@pytest.fixture(scope="module")
def doc_rows():
    return list(csv.DictReader(DOC_CSV.open(encoding="utf-8-sig")))


@needs_al_bateen
class TestReproducesDocument:
    def test_counts(self, al_bateen):
        s, i = al_bateen["summary"], al_bateen["inputs"]
        assert s["selected_cells"] == 1378
        assert s["cluster_count"] == 107
        assert s["by_mechanism"] == {"deep_sink": 4, "surface_depression": 90,
                                     "network_overflow": 10, "coastal_lowland": 3}
        assert i["valid_cells"] == 25034
        assert i["depressions"] == 991

    def test_no_deviation_when_inputs_complete(self, al_bateen):
        assert al_bateen["deviations"] == []

    def test_every_cluster_matches(self, al_bateen, doc_rows):
        mine = {c["cell_id"]: c for c in al_bateen["clusters"]}
        exact = [("rank", "rank"), ("cell_count", "cells"), ("mechanism", "mechanism"), ("score", "ponding_score")]
        close = [("max_depth_m", "max_depth_m"), ("mean_max_depth_m", "mean_max_depth_m"),
                 ("area_ge_0_30m_m2", "area_ge_0_30m_m2"), ("dur_ge_0_15m_h", "dur_ge_0_15m_h"),
                 ("dur_ge_0_30m_h", "dur_ge_0_30m_h"), ("depth_12h_m", "depth_12h_m"),
                 ("drain_time_h", "drain_time_h"), ("mean_speed_ms", "mean_speed_ms"),
                 ("sink_depth_m", "sink_depth_m"), ("basin_capacity_m3", "basin_capacity_m3"),
                 ("swmm_inflow_m3", "swmm_inflow_m3"), ("rain_on_cluster_m3", "rain_on_cluster_m3"),
                 ("lat", "lat"), ("lon", "lon")]
        for row in doc_rows:
            c = mine[int(row["cell_id"])]
            for a, b in exact:
                assert str(c[a]) == row[b] or float(c[a]) == float(row[b]), (row["rank"], a)
            for a, b in close:
                assert c[a] == pytest.approx(float(row[b]), abs=0.051, rel=1e-3), (row["rank"], a)


# ---------------------------------------------------------------------------
# 最小算例：5 × 6 的 20 m 网格，手搓一份交付目录
# ---------------------------------------------------------------------------


def _delivery(tmp: Path, depth: np.ndarray, dem: np.ndarray, *, times=None, swmm=None,
              speed=0.1, rain_mm=60.0) -> Path:
    """depth: (帧, 行, 列)。写 terrain_grid / swmm_exchange / 快照 / 最大水深图层 / 摘要。"""
    nf, nr, nc = depth.shape
    times = times if times is not None else [15.0 * i for i in range(nf)]
    land = np.ones((nr, nc), bool)
    np.savez(tmp / "terrain_grid.npz", values=dem.astype(np.float32), land_mask=land,
             permanent_water_mask=~land, district_mask=land,
             transform=np.array([20.0, 0, 0, 0, -20.0, 100.0, 0, 0, 1]))
    rate = np.zeros((1, nr * nc), np.float32)
    if swmm is not None:
        rate[0] = swmm.ravel() / 1800.0 / 400.0
    np.savez(tmp / "swmm_exchange.npz", swmm_to_anuga_cell_rates_mps=rate,
             surface_rainfall_fraction_by_cell=np.ones(nr * nc, np.float32),
             report_step_seconds=np.float64(1800.0))
    snap = tmp / "temporal_snapshots"
    snap.mkdir()
    man = []
    for i in range(nf):
        feats = [{"type": "Feature", "geometry": None,
                  "properties": {"cell_id": int(k), "depth_m": float(v)}}
                 for k, v in enumerate(depth[i].ravel()) if v >= 0.01]
        (snap / f"t{i:03d}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
        man.append({"index": i, "time_minutes": times[i], "path": f"temporal_snapshots/t{i:03d}.geojson",
                    "feature_count": len(feats)})
    (snap / "manifest.json").write_text(json.dumps({"snapshots": man}))
    mx = depth.max(0).ravel()
    feats = []
    for k, v in enumerate(mx):
        if v <= 0:
            continue
        r, c = divmod(k, nc)
        ring = [[c, -r], [c + 1, -r], [c + 1, -r - 1], [c, -r - 1], [c, -r]]
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                      "properties": {"cell_id": k, "maximum_depth_m": float(v), "maximum_speed_m_s": speed}})
    (tmp / "maximum_depth_wgs84.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    (tmp / "delivery_summary.json").write_text(json.dumps({
        "run_id": "toy", "forcing": {"generated_total_depth_mm": rain_mm},
        "outputs": {"maximum_depth": "maximum_depth_wgs84.geojson",
                    "timeline_manifest": "temporal_snapshots/manifest.json"}}))
    return tmp


def _flat(nr=5, nc=6, z=3.0):
    return np.full((nr, nc), z)


class TestRules:
    def test_selection_either_criterion(self, tmp_path):
        d = np.zeros((20, 5, 6))
        d[:, 1, 1] = 0.35                      # 只过深度判据（历时也长，但单独成片）
        d[:13, 3, 4] = 0.2                     # 只过历时：13 帧 × 15 min = 3.25 h
        d[:11, 1, 4] = 0.2                     # 都不过：2.75 h
        r = sp.compute(_delivery(tmp_path, d, _flat()))
        assert r["summary"]["cluster_count"] == 2
        assert {c["cell_id"] for c in r["clusters"]} == {1 * 6 + 1, 3 * 6 + 4}

    def test_onset_is_cumulative(self, tmp_path):
        d = np.zeros((20, 5, 6))
        d[5:, 1, 1] = 0.35                     # 第 5 帧起过深度判据 → 75 min
        d[2:15, 3, 4] = 0.2                    # 历时判据：第 2–13 帧累计 12 帧 × 15 min 满 3 h → 195 min
        r = sp.compute(_delivery(tmp_path, d, _flat()))
        by = {c["cell_id"]: c["onset_min"] for c in r["clusters"]}
        assert by == {1 * 6 + 1: 75.0, 3 * 6 + 4: 195.0}

    def test_eight_connected(self, tmp_path):
        d = np.zeros((2, 5, 6))
        d[:, 1, 1] = d[:, 2, 2] = 0.4          # 对角相邻 → 一个片区
        r = sp.compute(_delivery(tmp_path, d, _flat()))
        assert r["summary"]["cluster_count"] == 1
        assert r["clusters"][0]["cell_count"] == 2

    def test_deep_sink(self, tmp_path):
        dem = _flat()
        dem[2, 2] = 1.0                        # 2 m 深的孤立坑
        d = np.zeros((4, 5, 6))
        d[:, 2, 2] = 1.5
        r = sp.compute(_delivery(tmp_path, d, dem))
        assert r["clusters"][0]["mechanism_key"] == "deep_sink"
        assert r["clusters"][0]["sink_depth_m"] == 2.0

    def test_network_overflow(self, tmp_path):
        d = np.zeros((4, 5, 6))
        d[:, 2, 2] = 0.4
        sw = np.zeros((5, 6))
        sw[2, 2] = 400 * 0.06 + 1              # 注入量刚好超过本地降雨
        r = sp.compute(_delivery(tmp_path, d, _flat(), swmm=sw))
        assert r["clusters"][0]["mechanism_key"] == "network_overflow"

    def test_undrained_until_end(self, tmp_path):
        d = np.zeros((10, 5, 6))
        d[:, 2, 2] = 0.4                       # 最后一帧仍有水
        r = sp.compute(_delivery(tmp_path, d, _flat()))
        assert r["clusters"][0]["mechanism_key"] == "coastal_lowland"

    def test_surface_depression(self, tmp_path):
        d = np.zeros((10, 5, 6))
        d[:4, 2, 2] = 0.4                      # 早早退干
        r = sp.compute(_delivery(tmp_path, d, _flat()))
        assert r["clusters"][0]["mechanism_key"] == "surface_depression"
        assert r["deviations"] == []


class TestRecession:
    def test_drained_and_undrained_split(self, tmp_path):
        d = np.zeros((10, 5, 6))
        d[:4, 1, 1] = 0.4                      # 早早退干
        d[:, 3, 4] = 0.4                       # 演算末仍有水
        r = sp.recession(sp.compute(_delivery(tmp_path, d, _flat())))
        by = {c["drained"]: c for c in r["clusters"]}
        assert by[False]["recession_h"] is None
        assert by[True]["recession_h"] == pytest.approx(by[True]["drain_time_h"] - by[True]["peak_time_min"] / 60)
        rc = r["recession"]
        assert rc["undrained"] == 1 and sum(rc["counts"]) == 1
        assert rc["median_h"] == by[True]["recession_h"]

    def test_all_undrained_has_no_median(self, tmp_path):
        d = np.zeros((10, 5, 6))
        d[:, 2, 2] = 0.4
        rc = sp.recession(sp.compute(_delivery(tmp_path, d, _flat())))["recession"]
        assert rc == {"edges_h": [2.0, 4.0, 8.0], "counts": [0, 0, 0, 0], "undrained": 1, "median_h": None}


class TestDegradation:
    def test_missing_speed_drops_stagnation_term(self, tmp_path):
        d = np.zeros((10, 5, 6))
        d[:4, 2, 2] = 0.4
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        full = sp.compute(_delivery(tmp_path / "a", d, _flat()))
        base = _delivery(tmp_path / "b", d, _flat())
        g = json.loads((base / "maximum_depth_wgs84.geojson").read_text())
        for f in g["features"]:
            del f["properties"]["maximum_speed_m_s"]
        (base / "maximum_depth_wgs84.geojson").write_text(json.dumps(g))
        r = sp.compute(base)
        assert "no_speed" in r["deviations"]
        assert r["clusters"][0]["mean_speed_ms"] is None
        assert r["clusters"][0]["score"] != full["clusters"][0]["score"]

    def test_no_recession_flagged(self, tmp_path):
        d = np.zeros((10, 5, 6))
        d[:, 2, 2] = 0.4
        assert "no_recession" in sp.compute(_delivery(tmp_path, d, _flat()))["deviations"]

    def test_no_surface_rain_flagged(self, tmp_path):
        d = np.zeros((4, 5, 6))
        d[:2, 2, 2] = 0.4
        base = _delivery(tmp_path, d, _flat())
        e = dict(np.load(base / "swmm_exchange.npz"))
        e["surface_rainfall_fraction_by_cell"] = np.zeros_like(e["surface_rainfall_fraction_by_cell"])
        np.savez(base / "swmm_exchange.npz", **e)
        assert "no_surface_rain" in sp.compute(base)["deviations"]
