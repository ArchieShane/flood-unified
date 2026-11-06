# -*- coding: utf-8 -*-
"""计算申请参数（2026-10-06）：计算域（swbrain/domains.py）与执行计划（computejobs.plan / submit / pipe_args）。
不起计算、不连 makani 库；画的面用阿布扎比本岛附近一个约 2.5 km × 2.5 km 的方框。"""
import pytest

from swbrain import domains as DM
from swbrain.agents import computejobs as CJ

BOX = {"type": "Polygon", "coordinates": [[[54.50, 24.40], [54.525, 24.40], [54.525, 24.4225], [54.50, 24.4225], [54.50, 24.40]]]}


@pytest.fixture
def tmp_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(CJ, "DIR", tmp_path / "jobs")
    monkeypatch.setattr(CJ, "external_runs", lambda: [])
    monkeypatch.setattr(DM, "DIR", tmp_path / "domains")
    return tmp_path


# ---------------------------------------------------------------- 计算域
def test_draw_domain_resolves_in_utm():
    D, what, notes = DM.resolve({"source": "draw", "geojson": BOX})
    assert 5.0 < D.area / 1e6 < 7.5 and "自己画" in what and notes == []
    assert 230000 < D.centroid.x < 260000                                   # EPSG:32640


def test_finish_buffer_fragments_holes_and_multipart():
    from shapely.geometry import Polygon, box
    big = Polygon(box(0, 0, 3000, 3000).exterior, [box(1000, 1000, 1200, 1200).exterior.coords])
    tiny = box(5000, 5000, 5050, 5050)                                       # 0.0025 km² < 1%
    D, what, notes = DM.finish(big.union(tiny), 0, "error", "x")
    assert D.interiors == [] or len(D.interiors) == 0
    assert any("碎片" in n for n in notes) and any("洞" in n for n in notes)
    two = box(0, 0, 1000, 1000).union(box(5000, 0, 6000, 1000))
    with pytest.raises(DM.DomainError, match="不相连"):
        DM.finish(two, 0, "error", "x")
    D2, _, n2 = DM.finish(two, 0, "largest", "x")
    assert abs(D2.area - 1e6) < 1 and any("最大块" in n for n in n2)
    D3, w3, _ = DM.finish(two, 2100, "error", "x")                          # 外扩连上
    assert D3.geom_type == "Polygon" and "外扩 2100 m" in w3
    with pytest.raises(DM.DomainError, match="太小"):
        DM.finish(box(0, 0, 100, 100), 0, "error", "x")


def test_resolve_rejects_bad_specs():
    with pytest.raises(DM.DomainError):
        DM.resolve({"source": "nope"})
    with pytest.raises(DM.DomainError, match="至少选一个"):
        DM.resolve({"source": "catch", "ids": []})
    with pytest.raises(DM.DomainError, match="没有收到"):
        DM.resolve({"source": "draw"})


# ---------------------------------------------------------------- 执行计划
def test_unchanged_preset_keeps_its_name(tmp_jobs):
    p = CJ.plan({"kind": "compute", "tag": "M61"})
    assert p["run"] == "M61" and not p["custom"] and p["post"] == "full"
    assert p["steps"] == ["build:M61", "compute:M61:L1,L2,L2P"] and p["km2"] == 38.1


def test_changed_grid_gets_new_name_and_scaled_estimate(tmp_jobs):
    a = CJ.plan({"kind": "compute", "tag": "M61"})
    b = CJ.plan({"kind": "compute", "tag": "M61", "res": 10})
    assert b["run"] != "M61" and b["run"].startswith("M61_61mm2h_r10_") and b["base"] == "M61"
    assert b["est_h"] > 3 * a["est_h"]                                      # ∝ (15/10)³ ≈ 3.4
    assert b["n_tri"] > 2 * a["n_tri"]
    with pytest.raises(ValueError, match="预置情景名"):
        CJ.plan({"kind": "compute", "tag": "M61", "res": 10, "run": "M61"})


def test_custom_domain_is_l1_only_and_named(tmp_jobs):
    p = CJ.plan({"kind": "compute", "domain": {"source": "draw", "geojson": BOX}, "run": "MyBox"})
    assert p["run"] == "MyBox" and p["states"] == ["L1"] and p["post"] == "generic"
    assert any("现状泵站" in n for n in p["notes"])
    with pytest.raises(ValueError, match="只能算 L1"):
        CJ.plan({"kind": "compute", "domain": {"source": "draw", "geojson": BOX}, "states": ["L1", "L2"]})
    auto = CJ.plan({"kind": "compute", "domain": {"source": "draw", "geojson": BOX}})
    assert auto["run"].startswith("DRAW_61mm2h_r15_")


def test_finer_output_grids_are_noted(tmp_jobs):
    p = CJ.plan({"kind": "post", "tag": "M61", "tif_res": 5, "sww_res": 10})
    assert any("GeoTIFF 5 m" in n for n in p["notes"]) and any(".sww 10 m" in n for n in p["notes"])


def test_submit_dedups_and_pipe_args(tmp_jobs):
    j = CJ.submit("compute", "", None, params={"domain": {"source": "draw", "geojson": BOX}, "res": 20, "tif_res": 10})
    assert j["status"] == "pending" and (tmp_jobs / "domains" / f"{j['run']}.geojson").exists()
    a = CJ.pipe_args(j)
    assert a[:2] == ["--tag", j["run"]] and "--domain" in a and a[a.index("--res") + 1] == "20" and a[a.index("--tif-res") + 1] == "10"
    again = CJ.submit("compute", "", None, params={"domain": {"source": "draw", "geojson": BOX}, "res": 20, "tif_res": 10})
    assert again["duplicate"] and again["id"] == j["id"]
    old = {"tag": "M61", "steps": ["post:M61"]}                             # 没有 params 的老任务
    assert CJ.pipe_args(old) == []
