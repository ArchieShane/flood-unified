# -*- coding: utf-8 -*-
"""L2+ 设施导出（tools/export_facilities.py）的两个坑：

  1. 泵曲线里的流量要按模型自己的 FLOW_UNITS 换算——Mussafah 客户模型是 LPS，
     不换就把 0.36 m³/s 的移动泵报成 361 m³/s；
  2. 控制规则按**内容**比对：Mussafah 的 L1 是从 Parsons 原件剥出来的，规则名还在、内容被清空，
     只按名字比会漏掉 Parsons 的塘满停泵规则。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import export_facilities as E  # noqa: E402

BASE = """[OPTIONS]
FLOW_UNITS LPS
[JUNCTIONS]
J1 0 3 0 0 0
J2 0 3 0 0 0
[CONTROLS]
RULE R1

[COORDINATES]
J1 250000 2700000
J2 250100 2700000
"""
ST = """[OPTIONS]
FLOW_UNITS LPS
[JUNCTIONS]
J1 0 3 0 0 0
J2 0 3 0 0 0
[STORAGE]
S1 -1 2 0 FUNCTIONAL 0 0 100 0 0
[PUMPS]
P1 S1 J2 PC1 ON 0 0
[CURVES]
PC1 Pump3 10 360 20 180
PC1 30 0
[CONTROLS]
RULE R1
IF NODE S1 DEPTH >= 1.9
THEN PUMP P1 STATUS = OFF
PRIORITY 2
[COORDINATES]
J1 250000 2700000
J2 250100 2700000
S1 250050 2700050
"""


def test_单位换算与规则按内容比对(tmp_path):
    b = tmp_path / "b.inp"; s = tmp_path / "s.inp"
    b.write_text(BASE, encoding="utf-8"); s.write_text(ST, encoding="utf-8")
    r = E.export_state(b, s, tmp_path / "out", "T", log=lambda *a: None)
    assert r["summary"]["n_storage"] == 1 and abs(r["storage"][0]["volume_m3"] - 200) < 1   # 100 m² × 2 m
    assert r["pumps"][0]["q_max_m3s"] == 0.36                                                # 360 L/s
    assert [x["rule"] for x in r["rules"]] == ["R1"]                                         # 同名、L1 里是空的
    assert r["storage"][0]["outline"].startswith("示意轮廓")
    for f in ("蓄水设施.geojson", "管线闸堰.geojson", "泵.csv", "控制规则.csv", "控制规则.txt"):
        assert (tmp_path / "out" / f).exists()


def test_箱涵泵曲线按模型流量单位写(tmp_path):
    """RB_02 重建模型是 CMS：原先写死 L/s，排空泵放大 1000 倍（2026-09-28）。"""
    import dig_pond5 as D5
    import re
    base = (Path(__file__).resolve().parents[1] / "tests").as_posix()
    for units, want in (("LPS", 100.0), ("CMS", 0.1)):
        src = tmp_path / f"s_{units}.inp"
        src.write_text(f"[OPTIONS]\nFLOW_UNITS {units}\n[JUNCTIONS]\nJ1 0 3 0 0 0\nJ2 -1 3 0 0 0\n[CONDUITS]\nC1 J1 J2 50 0.013 0 0 0 0\n"
                       "[XSECTIONS]\nC1 CIRCULAR 0.6 0 0 0 1\n[COORDINATES]\nJ1 250000 2700000\nJ2 250050 2700000\n", encoding="utf-8")
        site = {"id": "NP1", "connect": ["J1"], "x": 250020, "y": 2700020, "vol_m3": 1000.0, "area_m2": 500.0, "bottom_m": -3.0, "top_m": -1.0,
                "inflow": "gravity", "orifice_mm": 300, "dn_mm": 300, "outlet_node": "J2", "empty_q_m3h": 360.0, "q_in_m3h": 1000.0,
                "conveyance_m": 20.0, "lift_m": 0.0, "n_pumps_in": 0, "n_pumps_out": 1, "fill_min": 10,
                "slope": 0.005, "ground_m": 3.0, "depth_m": 2.0, "q_gate_m3h": 1000.0}
        dst = tmp_path / f"d_{units}.inp"
        try:
            D5.dig5(src, dst, [site], "box", empty_start_h=3, log=lambda *a: None)
        except Exception as ex:          # 只关心泵曲线的单位；构造的最小网缺字段时直接看报错
            raise AssertionError(f"dig5 在最小模型上失败：{ex}")
        m = re.search(r"^NP1_Pump Pump4 0 (\S+)", dst.read_text(encoding="utf-8"), re.M)
        assert m and abs(float(m.group(1)) - want) < 1e-6 * max(1, want), (units, m and m.group(1))
