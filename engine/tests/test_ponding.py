# -*- coding: utf-8 -*-
"""二维模拟积水点（swbrain/ponding.py）：合成三角网上的连片、阈值、编号、退水，以及 /api/ponding 与 hydro_ponding 动作。"""
import json

import numpy as np
import pytest

from swbrain import ponding

X0, Y0 = 600000.0, 2600000.0          # 远离真实热点，热点匹配为空


def grid_mesh(n=20, d=10.0):
    """n×n 个 d m 方格，每格切两个三角形；返回 (顶点 x, y, 三角形, 形心, 面积)"""
    xs, ys = np.meshgrid(np.arange(n + 1) * d + X0, np.arange(n + 1) * d + Y0)
    vx, vy = xs.ravel(), ys.ravel()
    k = lambda i, j: j * (n + 1) + i   # noqa: E731
    tris = []
    for j in range(n):
        for i in range(n):
            tris += [[k(i, j), k(i + 1, j), k(i + 1, j + 1)], [k(i, j), k(i + 1, j + 1), k(i, j + 1)]]
    tris = np.array(tris)
    xy = np.c_[vx[tris].mean(1), vy[tris].mean(1)]
    area = np.full(len(tris), d * d / 2)
    return vx, vy, tris, xy, area


def scene(n_frames=13):
    """两片洼地：A（大、深、雨后一直不退）、B（小、浅、2 帧后退）；一个只有 1 格的小水坑（面积不够）"""
    vx, vy, tris, xy, area = grid_mesh()
    i, j = ((xy[:, 0] - X0) // 10).astype(int), ((xy[:, 1] - Y0) // 10).astype(int)
    A = (i >= 2) & (i <= 6) & (j >= 2) & (j <= 6)          # 25 格 = 2 500 m²
    B = (i >= 12) & (i <= 14) & (j >= 12) & (j <= 14)      # 9 格 = 900 m²
    tiny = (i == 18) & (j == 2)                            # 1 格 = 100 m²，两个三角各 50 m²
    t_h = np.arange(1, n_frames + 1) / 12.0
    fr = np.zeros((n_frames, len(xy)), np.float32)
    fr[:, A] = 0.5
    fr[2:8, B] = 0.2                                       # 0.17–0.67 h 有水，之后退
    fr[:, tiny] = 0.4
    hmax = fr.max(0)
    return dict(vx=vx, vy=vy, tris=tris, xy=xy, area=area, elev=np.zeros(len(xy)), hmax=hmax, frames=fr, t_h=t_h, A=A, B=B)


def run_extract(S, **kw):
    return ponding.extract(S["xy"], S["area"], S["elev"], S["hmax"], S["frames"], S["t_h"], state="L1", tris=kw.pop("tris", S["tris"]), **kw)


def test_two_patches_ids_and_volumes():
    S = scene()
    f, n_patch, how = run_extract(S, depth=0.15, min_area=200, min_dur=0.25)
    assert how == "mesh" and n_patch == 3                  # A、B、小水坑
    assert [x["id"] for x in f] == ["SIM-L1-001", "SIM-L1-002"]
    a, b = f
    assert a["area_m2"] == 2500 and a["vol_peak_m3"] == 1250 and a["hmax_m"] == 0.5
    assert a["recede_h"] is None and a["vol_end_m3"] == 1250  # 一直不退
    assert b["area_m2"] == 900 and b["vol_peak_m3"] == 180
    assert b["recede_h"] == pytest.approx(0.5, abs=1e-6)    # 峰值在第 3 帧（0.25 h），第 9 帧（0.75 h）退
    assert b["dur_h"] == pytest.approx(0.5, abs=1e-6)


def test_thresholds_filter():
    S = scene()
    assert len(run_extract(S, depth=0.3, min_area=200, min_dur=0.25)[0]) == 1      # B 只有 0.2 m
    assert len(run_extract(S, depth=0.15, min_area=1000, min_dur=0.25)[0]) == 1    # B 900 m²
    assert len(run_extract(S, depth=0.15, min_area=200, min_dur=0.75)[0]) == 1     # B 只持续 0.5 h
    assert len(run_extract(S, depth=0.15, min_area=50, min_dur=0.25)[0]) == 3      # 小水坑也算


def test_pond_cells_excluded():
    S = scene()
    f, _, _ = run_extract(S, depth=0.15, min_area=200, min_dur=0.25, pond=S["A"])
    assert [x["area_m2"] for x in f] == [900]


def test_approx_adjacency_matches_mesh_on_regular_grid():
    S = scene()
    m = run_extract(S, depth=0.15, min_area=50, min_dur=0.25)[0]
    a, _, how = run_extract(S, depth=0.15, min_area=50, min_dur=0.25, tris=None)
    assert how == "approx"
    assert [(x["area_m2"], x["vol_peak_m3"]) for x in a] == [(x["area_m2"], x["vol_peak_m3"]) for x in m]


@pytest.fixture
def work(tmp_path, monkeypatch):
    S = scene()
    c2d = tmp_path / "c2d"; c2d.mkdir()
    np.savez(c2d / "TST_RUN_L1_2d.npz", xy=S["xy"].astype(np.float32), area=S["area"].astype(np.float32), elev=S["elev"].astype(np.float32),
             hmax=S["hmax"].astype(np.float32), frames=S["frames"].astype(np.float16), t_h=S["t_h"])
    poly = [[X0, Y0], [X0 + 200, Y0], [X0 + 200, Y0 + 200], [X0, Y0 + 200]]
    (c2d / "pilot_TST_RUN_L1.json").write_text(json.dumps({"domain_polygon": poly, "ponds": []}), encoding="utf-8")
    monkeypatch.setenv("SWBRAIN_SWMM_WORK", str(tmp_path))
    monkeypatch.setattr(ponding, "CACHE", tmp_path / "cache")
    return tmp_path


def test_compute_and_cache(work):
    r = ponding.compute("TST_RUN", "L1", 0.15, 200, 0.25)
    assert r["adjacency"] == "approx" and r["n"] == 2 and r["vol_total_m3"] == 1430
    assert r["feats"][0]["id"] == "SIM-L1-001" and r["feats"][0]["geom"]["type"] in ("Polygon", "MultiPolygon")
    assert 54 < r["feats"][0]["lon"] < 60 and r["domain"] and r["n_not_receded"] == 1
    assert list((work / "cache").glob("TST_RUN_L1_*.json"))
    assert ponding.compute("TST_RUN", "L1", 0.15, 200, 0.25, geometry=False)["feats"][0].get("geom") is None


def test_bad_args(work):
    with pytest.raises(ValueError):
        ponding.compute("../etc", "L1")
    with pytest.raises(FileNotFoundError):
        ponding.compute("TST_RUN", "L9")
    with pytest.raises(ValueError):
        ponding.compute("TST_RUN", "L1", depth=0)


def test_api_route(work):
    from swbrain import routes
    h = routes.resolve("GET", "/api/ponding").fn
    assert h(None, {}, None, None)["runs"] == {"TST_RUN": ["L1"]}
    r = h(None, {"run": ["TST_RUN"], "state": ["L1"], "min_area": ["200"], "min_dur": ["0.25"], "geom": ["0"]}, None, None)
    assert r["n"] == 2 and "geom" not in r["feats"][0]
    err, code = h(None, {"run": ["TST_RUN"], "state": ["L7"]}, None, None)
    assert code == 400 and "L1" in err["error"]


def test_action_summary(work):
    from swbrain.ontology.actions.hydro import HydroActions
    H = HydroActions()
    r = H._act_hydro_ponding({"run": "TST_RUN", "state": "L1", "min_area": 200.0, "min_dur": 0.25, "top": 2})
    s = H._sum_hydro_ponding(r, {}, "zh")
    assert "SIM-L1-001" in s and "不是客户热点编号" in s and "2 个" in s
    assert "TST_RUN" in H._sum_hydro_ponding(H._act_hydro_ponding({}), {}, "zh")


def test_void_run_refused(work):
    from swbrain.ontology.actions.hydro import HydroActions
    from swbrain.ontology.common import ActionError
    with pytest.raises(ActionError):
        HydroActions()._act_hydro_ponding({"run": "C1_D24"})


def test_export_delivery_files(work):
    import csv as _csv
    out = work / "交付"; sd = out / "L1_现状"; sd.mkdir(parents=True)
    files = ponding.export_delivery("TST_RUN", {"L1": sd}, out, depths=(0.15,))
    names = sorted(p.name for p in files)
    assert "积水点说明.md" in names and "ponding_d0.15.csv" in names and "ponding_areas_d0.15.geojson" in names
    rows = list(_csv.DictReader(open(sd / "积水点" / "ponding_d0.15.csv", encoding="utf-8")))
    assert rows[0]["id"].startswith("SIM-L1-") and rows[0]["receded"] in ("true", "false")
    assert all(r["recede_h"] == "" for r in rows if r["receded"] == "false")          # 未退留空，不写文字
    gj = json.loads((sd / "积水点" / "ponding_points_d0.15.geojson").read_text(encoding="utf-8"))
    assert gj["features"][0]["properties"]["id"] == rows[0]["id"]
    assert "不是客户热点编号" in (out / "积水点说明.md").read_text(encoding="utf-8")


def test_upsert_readme_replaces_section_and_keeps_newlines(tmp_path):
    p = tmp_path / "readme.md"
    p.write_bytes("# 标题\r\n\r\n正文\r\n".encode("utf-8"))
    ponding.upsert_readme(p, ponding.readme_section({"L1 现状": "L1_现状"}))
    ponding.upsert_readme(p, ponding.readme_section({"L1 现状": "L1_现状", "L2": "L2_移动泵"}))
    t = p.read_bytes().decode("utf-8")
    assert t.count(ponding.README_MARK[0]) == 1 and "L2_移动泵" in t and t.startswith("# 标题\r\n\r\n正文")
    assert "\n" not in t.replace("\r\n", "")


def test_cache_without_geometry_then_with_geometry(work):
    """先不带几何算（缓存里没有 geom），再要几何时必须重算，不能返回没有几何的缓存（2026-10-19 修）"""
    r0 = ponding.compute("TST_RUN", "L1", 0.15, 200, 0.25, geometry=False)
    assert "geom" not in r0["feats"][0]
    r1 = ponding.compute("TST_RUN", "L1", 0.15, 200, 0.25, geometry=True)
    assert r1["feats"][0]["geom"]["type"] in ("Polygon", "MultiPolygon")
