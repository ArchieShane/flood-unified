# -*- coding: utf-8 -*-
"""飞渡 DTS 转换（swbrain/dts_h2d.py）：合成 .sww → shp + dat，交付目录接入。"""
import json

import numpy as np
import pytest
from scipy.io import netcdf_file

from swbrain import dts_h2d


def make_sww(path, nt=3):
    """2 个三角形（一个逆时针、一个顺时针），nt 个时刻；第 1 个单元有水、第 2 个干"""
    x = np.array([0.0, 10.0, 10.0, 0.0]); y = np.array([0.0, 0.0, 10.0, 10.0])
    vol = np.array([[0, 1, 2], [0, 3, 2]], dtype=np.int32)           # 第 1 个逆时针，第 2 个顺时针
    elev_c = np.array([1.0, 2.0])
    with netcdf_file(str(path), "w") as nc:
        nc.xllcorner = 600000.0; nc.yllcorner = 2600000.0
        nc.createDimension("np", 4); nc.createDimension("nv", 2); nc.createDimension("three", 3); nc.createDimension("nt", nt)
        for k, a, d in (("x", x, ("np",)), ("y", y, ("np",)), ("elevation_c", elev_c, ("nv",))):
            v = nc.createVariable(k, "f8", d); v[:] = a
        v = nc.createVariable("volumes", "i4", ("nv", "three")); v[:] = vol
        v = nc.createVariable("time", "f8", ("nt",)); v[:] = np.arange(1, nt + 1) * 300.0
        st = np.tile(elev_c, (nt, 1)); st[:, 0] += np.arange(1, nt + 1) * 0.1          # 单元 1 水深 0.1、0.2、0.3
        v = nc.createVariable("stage_c", "f8", ("nt", "nv")); v[:] = st
        xm = np.zeros((nt, 2)); xm[:, 0] = 0.05
        v = nc.createVariable("xmomentum_c", "f8", ("nt", "nv")); v[:] = xm
        v = nc.createVariable("ymomentum_c", "f8", ("nt", "nv")); v[:] = np.zeros((nt, 2))
    return path


def test_convert_shp_and_dat(tmp_path):
    import shapefile
    s = make_sww(tmp_path / "T_L1.sww")
    m = dts_h2d.convert(s, tmp_path / "o", name="T_L1", no_csv=True)
    assert m["triangles_total"] == 2 and len(m["timesteps"]) == 3 and m["crs_shp"] == "EPSG:32640"
    r = shapefile.Reader(str(tmp_path / "o" / "shp" / "T_L1"))
    assert len(r) == 2 and [rec["ID"] for rec in r.records()] == [1, 2]
    for sh in r.shapes():                                               # shapefile 外环顺时针：有向面积 < 0
        p = np.array(sh.points)
        assert 0.5 * np.sum(p[:-1, 0] * p[1:, 1] - p[1:, 0] * p[:-1, 1]) < 0
    rec = dts_h2d.read_dat(tmp_path / "o" / "dat" / "RESULTS003.dat")
    assert (tmp_path / "o" / "dat" / "RESULTS003.dat").stat().st_size == 2 * 28
    assert list(rec["id"]) == [1, 2]
    assert rec["h"][0] == pytest.approx(0.3) and rec["u"][0] == pytest.approx(0.05 / 0.3)
    assert rec["h"][1] == 0 and rec["u"][1] == 0                         # 干单元流速 0
    assert not list((tmp_path / "o" / "dat").glob("*.csv"))             # 系统里不出 CSV


def test_cli_default_still_writes_csv(tmp_path):
    s = make_sww(tmp_path / "T_L1.sww", nt=1)
    assert dts_h2d.main([str(s), "-o", str(tmp_path / "c")]) == 0
    assert (tmp_path / "c" / "dat" / "RESULTS001.csv").exists() and (tmp_path / "c" / "dat" / "RESULTS_index.csv").exists()


def test_unknown_option_rejected(tmp_path):
    with pytest.raises(TypeError):
        dts_h2d.convert(make_sww(tmp_path / "T_L1.sww"), tmp_path / "o", nope=1)


def test_export_delivery_replaces_old_and_writes_doc(tmp_path):
    sdir = tmp_path / "anuga_out"; sdir.mkdir()
    make_sww(sdir / "RUN_L1.sww")
    out = tmp_path / "交付"; st = out / "L1_现状"
    (st / dts_h2d.DTS_DIR / "dat").mkdir(parents=True); (st / dts_h2d.DTS_DIR / "dat" / "RESULTS999.dat").write_bytes(b"old")
    rows = dts_h2d.export_delivery("RUN", sdir, {"L1": st, "L2": out / "L2"}, out, names={"L1": "L1 现状"})
    assert [r[0] for r in rows] == ["L1 现状"]                          # L2 没有 .sww：跳过
    assert not (st / dts_h2d.DTS_DIR / "dat" / "RESULTS999.dat").exists()
    assert json.loads((st / dts_h2d.DTS_DIR / "h2d_index.json").read_text(encoding="utf-8"))["triangles_total"] == 2
    doc = (out / "飞渡DTS说明.md").read_text(encoding="utf-8")
    assert "28 字节" in doc and "L1 现状" in doc
