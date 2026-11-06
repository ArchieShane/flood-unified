"""sim_native：AAM 原生计算结果（ANUGA .sww + SWMM .rpt）的抽取口径。用一个 2 × 2 个 100 m 方格的假 .sww 小样验算。"""

import numpy as np
import pytest
from scipy.io import netcdf_file

from stormwater import sim_import, sim_native


def _fake_sww(path, xll=258900.0, yll=2503900.0):
    """2 × 2 个 100 m 方格（局部坐标 100..300 / 0..200），每格两个三角形；3 帧，只有第一个方格进水。"""
    xs, ys = np.meshgrid([100.0, 200.0, 300.0], [0.0, 100.0, 200.0])
    x, y = xs.ravel().astype("f4"), ys.ravel().astype("f4")
    vid = lambda r, c: r * 3 + c
    tris = []
    for r in range(2):
        for c in range(2):
            tris += [[vid(r, c), vid(r, c + 1), vid(r + 1, c)], [vid(r, c + 1), vid(r + 1, c + 1), vid(r + 1, c)]]
    nt = len(tris)
    f = netcdf_file(path, "w")
    f.xllcorner, f.yllcorner = xll, yll
    f.createDimension("v", len(x)); f.createDimension("t", nt); f.createDimension("k", 3); f.createDimension("f", 3)
    for name, arr, dims, typ in [("x", x, ("v",), "f"), ("y", y, ("v",), "f"),
                                 ("volumes", np.array(tris, "i4"), ("t", "k"), "i"),
                                 ("elevation_c", np.full(nt, 10.0, "f4"), ("t",), "f"),
                                 ("time", np.array([0.0, 300.0, 600.0]), ("f",), "d")]:
        var = f.createVariable(name, typ, dims)
        var[:] = arr
    stage = np.full((3, nt), 10.0, "f4")
    stage[:, 0:2] += np.array([0.0, 0.5, 0.2])[:, None]        # 第一个方格（两个三角形）：0 → 0.5 → 0.2 m
    mom = np.zeros((3, nt), "f4")
    mom[1, 0:2] = 0.5 * 2.0                                      # 水深 0.5 m × 速度 2 m/s
    for name, arr in [("stage_c", stage), ("xmomentum_c", mom), ("ymomentum_c", np.zeros((3, nt), "f4"))]:
        var = f.createVariable(name, "f", ("f", "t"))
        var[:] = arr
    f.close()


def test_extract_grid_aggregates_triangles_to_cells(tmp_path):
    p = tmp_path / "x.sww"
    _fake_sww(p)
    g = sim_native._extract_grid(p, cs=250.0)
    assert g["n_tri"] == 8
    assert g["domain_m2"] == pytest.approx(8 * 5000)
    assert g["tri_hmax"] == pytest.approx(0.5)
    # 两个三角形（一个方格）水深 0.5 m、面积 1 万 m²；同刻 ≥ 0.02 m 的最大面积就是这一格
    assert g["tri_wet_max_m2"] == pytest.approx(10000)
    assert g["tri_wet_max_min"] == pytest.approx(5.0)
    assert g["series"]["time_minutes"] == [0.0, 5.0, 10.0]
    assert g["series"]["volume_m3"][1] == pytest.approx(0.5 * 10000, rel=1e-4)
    assert g["maxsp"].max() == pytest.approx(2.0, rel=1e-3)           # 动量 / 水深
    assert len(g["frames"]) == 3 and g["frames"][2][0] == 600.0
    # 局部坐标要加 xllcorner / yllcorner 才是 EPSG:32640 绝对坐标，并对齐 250 m 整数倍
    assert g["x0"] % 250 == 0 and g["ytop"] % 250 == 0
    assert g["bounds"][0] <= 258900 + 100 and g["bounds"][2] >= 258900 + 300


def test_overflow_series_is_cumulative_network_to_surface():
    bal = [{"end_seconds": 30 * (i + 1), "network_to_surface_m3": 1.0} for i in range(20)]   # 600 s，每窗 1 m³
    s = sim_native._overflow_series(bal, [0.0, 5.0, 10.0])
    assert s["cum_m3"] == [0.0, 10.0, 20.0]


def test_trim_rpt_keeps_summaries_and_drops_series(tmp_path):
    src = tmp_path / "a.rpt"
    src.write_text(
        "  EPA SWMM\n  WARNING 04: minimum elevation drop used for Conduit C1\n"
        "  ******************\n  Node Flooding Summary\n  ******************\n  N1  1.0\n\n"
        "  ***********************\n  Node Results\n  ***********************\n\n"
        "  <<< Node N1 >>>\n  10/16/2026 03:35:00 -0.000\n\n"
        "  Analysis begun on:  Fri Oct 16 01:34:38 2026\n  Analysis ended on:  Fri Oct 16 04:38:23 2026\n",
        encoding="utf-8")
    dst = tmp_path / "b.rpt"
    sim_native.trim_rpt(src, dst)
    t = dst.read_text(encoding="utf-8")
    assert "Node Flooding Summary" in t and "N1  1.0" in t
    assert "WARNING" not in t and "<<< Node" not in t and "Node Results" not in t
    assert "Analysis begun on:  Fri Oct 16 01:34:38 2026" in t and "Analysis ended on" in t
    # 起止时刻能被登记用的正则抓到
    from stormwater.sim_results import _RPT_TIME
    assert [m.group(1) for m in map(_RPT_TIME.search, t.splitlines()) if m] == ["begun", "ended"]


def test_trim_rpt_rejects_truncated_report(tmp_path):
    src = tmp_path / "a.rpt"
    src.write_text("  Node Flooding Summary\n  <<< Node N1 >>>\n", encoding="utf-8")
    with pytest.raises(sim_native.SwwError, match="Analysis begun"):
        sim_native.trim_rpt(src, tmp_path / "b.rpt")


def test_detect_native_shape(tmp_path):
    root = tmp_path / "原生计算结果_可拷贝"            # 目录名不是「片区_雨深mm历时h」也要认
    (root / "L1").mkdir(parents=True)
    (root / "配套参数与标识").mkdir()
    (root / sim_native.MARK_NOTE).write_text("x", encoding="utf-8")
    assert not sim_native.detect(root)
    (root / "L1" / "AAM_L1_2D.sww").write_bytes(b"CDF\x02")
    assert sim_native.detect(root) and sim_import.detect(root) == "native"


def test_import_any_routes_native(tmp_path, monkeypatch):
    root = tmp_path / "原生计算结果_可拷贝"
    (root / "L1").mkdir(parents=True)
    (root / "配套参数与标识").mkdir()
    (root / sim_native.MARK_NOTE).write_text("x", encoding="utf-8")
    (root / "L1" / "AAM_L1_2D.sww").write_bytes(b"CDF\x02")
    monkeypatch.setattr(sim_native, "import_all", lambda r, by, only: 77)
    assert sim_import.import_any(root, "ZN") == ("native", 77)
