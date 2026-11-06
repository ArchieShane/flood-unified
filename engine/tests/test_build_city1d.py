# -*- coding: utf-8 -*-
"""tools/build_city1d.py 的判据测试。不读 GDB、不跑 SWMM。

钉四件事，每一件都对应一个已经踩过或差点踩的坑：
  1. 井底取相邻管管内底的**最小值** —— 取平均会让某些管口低于井底，
     SWMM 会自己抬高，跑出来的就不是我们给的那张网；
  2. 埋深离谱的端点要按中位埋深补，不能照抄（实测 14.4% 的端点离谱）；
  3. 出水口要**另起节点 + 一根短管**——SWMM 要求出水口只能有一根进水管
     （直接把多管节点标成出水口会报 ERROR 141，已经撞过一次）；
  4. 塘的进水口和出水口**不能接在同一个井上** —— 那是短路，水进来原路流回，
     塘等于不存在（这是我先前写错、后来修掉的）。
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

pytest.importorskip("pandas")
import pandas as pd  # noqa: E402
import build_city1d as BC  # noqa: E402


def _df(rows):
    """rows: (sc, ec, sx, sy, ex, ey, gs, ge, iu, idn, dia, ln, status)"""
    cols = ["StartCode", "EndCode", "StartX", "StartY", "EndX", "EndY",
            "StartElev", "EndElev", "INVERT_LEVEL_UPSTREAM", "INVERT_LEVEL_DOWNSTREAM",
            "PIPE_DIAMETER", "LineLength", "STATUS"]
    return pd.DataFrame(rows, columns=cols).assign(UNITID="u")


# ---------------------------------------------------------------- 井底
def test_井底取相邻管管内底的最小值():
    # 同一个井 A 上挂两根管：一根管内底 108.0（→8.0），一根 107.0（→7.0）
    df = _df([
        ("A", "B", 0, 0, 10, 0, 10.0, 10.0, 108.0, 108.0, 300, 10, "Active"),
        ("A", "C", 0, 0, 0, 10, 10.0, 10.0, 107.0, 107.0, 300, 10, "Active"),
    ])
    xy, ground, inv, _ = BC.build_nodes(df, log=lambda *a: None)
    assert inv["A"] == pytest.approx(7.0)          # 取小的那个，不是平均 7.5


def test_井盖取相邻端点地面的中位数():
    df = _df([
        ("A", "B", 0, 0, 10, 0, 10.0, 12.0, 108.5, 108.5, 300, 10, "Active"),
        ("A", "C", 0, 0, 0, 10, 11.0, 13.0, 108.5, 108.5, 300, 10, "Active"),
    ])
    _, ground, _, _ = BC.build_nodes(df, log=lambda *a: None)
    assert ground["A"] == pytest.approx(10.5)      # median(10, 11)


# ---------------------------------------------------------------- 埋深补值
def test_管内底缺失按中位埋深补():
    df = _df([("A", "B", 0, 0, 10, 0, 10.0, 10.0, 0.0, 0.0, 300, 10, "Active")])
    _, _, inv, _ = BC.build_nodes(df, log=lambda *a: None)
    assert inv["A"] == pytest.approx(10.0 - BC.COVER_MED)


def test_埋深太浅时信管底_抬井盖():
    # 管内底高过地面：多半是地面错了（GDB 地面与 DTM 40% 差 >1 m），管底在 Mussafah 与客户模型 99% 一致。
    # 原先的规则在这里扔掉管底，造出比真实低 3 m 多的「假坑」（2026-09-27，validate_city_net.py）
    df = _df([("A", "B", 0, 0, 10, 0, 10.0, 10.0, 112.0, 112.0, 300, 10, "Active")])
    _, ground, inv, _ = BC.build_nodes(df, log=lambda *a: None)
    assert inv["A"] == pytest.approx(12.0)
    assert ground["A"] - inv["A"] >= BC.COVER_MIN


def test_埋得离谱地深的还是要补():
    df = _df([("A", "B", 0, 0, 10, 0, 130.0, 130.0, 105.0, 105.0, 300, 10, "Active")])   # 埋深 125 m
    _, _, inv, _ = BC.build_nodes(df, log=lambda *a: None)
    assert inv["A"] == pytest.approx(130.0 - BC.COVER_MED)


def test_地面取DTM不取GDB(monkeypatch):
    monkeypatch.setattr(BC, "sample_dtm", lambda x, y: [3.7] * len(x))
    df = _df([("A", "B", 0, 0, 10, 0, 0.18, 0.18, 100.55, 100.55, 300, 10, "Active")])   # GDB 地面 0.18，真实 3.7
    _, ground, inv, _ = BC.build_nodes(df, log=lambda *a: None)
    assert inv["A"] == pytest.approx(0.55) and ground["A"] == pytest.approx(3.7)


def test_埋深正常的原样保留():
    df = _df([("A", "B", 0, 0, 10, 0, 10.0, 10.0, 108.0, 108.0, 300, 10, "Active")])
    _, _, inv, _ = BC.build_nodes(df, log=lambda *a: None)
    assert inv["A"] == pytest.approx(8.0)          # 108 − 100，埋深 2 m，合理


def test_井盖必须高过井底():
    df = _df([("A", "B", 0, 0, 10, 0, 0.0, 0.0, 100.0, 100.0, 300, 10, "Active")])
    _, ground, inv, _ = BC.build_nodes(df, log=lambda *a: None)
    assert ground["A"] - inv["A"] >= BC.COVER_MIN


def test_废弃管要剔掉():
    df = _df([
        ("A", "B", 0, 0, 10, 0, 10.0, 10.0, 108.0, 108.0, 300, 10, "Active"),
        ("C", "D", 0, 0, 10, 0, 10.0, 10.0, 108.0, 108.0, 300, 10, "Abandoned"),
    ])
    st = df.STATUS.astype("string")
    kept = df[~st.isin(BC.DEAD_STATUS)]
    assert len(kept) == 1 and kept.iloc[0].StartCode == "A"


# ---------------------------------------------------------------- 出水口
def test_出水口另起节点_原节点留作井(tmp_path):
    """SWMM 要求出水口只能有一根进水管。直接把多管节点标成出水口会 ERROR 141。"""
    xy = {"A": (0.0, 0.0), "B": (10.0, 0.0), "C": (0.0, 10.0)}
    ground = {k: 10.0 for k in xy}
    inv = {k: 8.0 for k in xy}
    pipes = [{"id": "c1", "n1": "B", "n2": "A", "len": 10, "z1": 8.2, "z2": 8.0, "d": 0.3},
             {"id": "c2", "n1": "C", "n2": "A", "len": 10, "z1": 8.3, "z2": 8.0, "d": 0.3}]
    f = tmp_path / "t.inp"
    BC.write_inp(f, xy, ground, inv, pipes, {"A"}, {k: 0.0 for k in xy},
                 of_dia={"A": 0.6}, log=lambda *a: None)
    t = f.read_text(encoding="utf-8")
    assert "\nOUT_A  " in t                        # 另起的出水口节点
    assert "\nof_A  A  OUT_A  " in t               # 一根短管接过去
    # 原节点 A 仍是井，两根管照旧接在它上面
    j = t[t.index("[JUNCTIONS]"):t.index("[OUTFALLS]")]
    assert "\nA  " in j


def test_出水口只有一根进水管且没有出水管(tmp_path):
    xy = {"A": (0.0, 0.0), "B": (10.0, 0.0)}
    ground = {k: 10.0 for k in xy}; inv = {k: 8.0 for k in xy}
    pipes = [{"id": "c1", "n1": "B", "n2": "A", "len": 10, "z1": 8.2, "z2": 8.0, "d": 0.3}]
    f = tmp_path / "t.inp"
    BC.write_inp(f, xy, ground, inv, pipes, {"A"}, {k: 0.0 for k in xy},
                 of_dia={"A": 0.6}, log=lambda *a: None)
    t = f.read_text(encoding="utf-8")
    sec = t[t.index("[CONDUITS]"):t.index("[XSECTIONS]")]
    ins = [ln for ln in sec.splitlines() if ln.split()[2:3] == ["OUT_A"]]
    outs = [ln for ln in sec.splitlines() if ln.split()[1:2] == ["OUT_A"]]
    assert len(ins) == 1 and len(outs) == 0


# ---------------------------------------------------------------- 塘
def test_塘的进出水口不能是同一个井(tmp_path):
    """短路：水从同一个井进来又流回去，塘等于不存在。先前写错过。"""
    p = {"node": "PND_X", "z_floor": 5.0, "depth": 2.0, "curve": [(0.0, 100.0), (2.0, 100.0)],
         "in_node": "A", "in_z": 5.3, "in_area": 0.5, "weir_z": 7.0,
         "out_node": "B", "out_z": 5.0, "out_area": 0.05}
    assert p["in_node"] != p["out_node"]
    xy = {"A": (0.0, 0.0), "B": (10.0, 0.0), "PND_X": (5.0, 5.0)}
    ground = {"A": 8.0, "B": 8.0, "PND_X": 7.0}
    inv = {"A": 6.0, "B": 4.0, "PND_X": 5.0}
    f = tmp_path / "t.inp"
    BC.write_inp(f, xy, ground, inv, [], set(), {k: 0.0 for k in xy}, ponds=[p],
                 log=lambda *a: None)
    t = f.read_text(encoding="utf-8")
    assert "OI_PND_X  A  PND_X" in t               # 进水从 A
    assert "OO_PND_X  PND_X  B" in t               # 出水去 B
    assert "OW_PND_X  PND_X  A" in t               # 应急堰


def test_塘只能出现在STORAGE里_不能再写进JUNCTIONS(tmp_path):
    """同名会报 ERROR 207，五个分片因此秒退过。"""
    p = {"node": "PND_X", "z_floor": 5.0, "depth": 2.0, "curve": [(0.0, 100.0), (2.0, 100.0)],
         "in_node": "A", "in_z": 5.3, "in_area": 0.5, "weir_z": 7.0}
    xy = {"A": (0.0, 0.0), "PND_X": (5.0, 5.0)}
    f = tmp_path / "t.inp"
    BC.write_inp(f, xy, {"A": 8.0, "PND_X": 7.0}, {"A": 6.0, "PND_X": 5.0},
                 [], set(), {k: 0.0 for k in xy}, ponds=[p], log=lambda *a: None)
    t = f.read_text(encoding="utf-8")
    j = t[t.index("[JUNCTIONS]"):t.index("[OUTFALLS]")]
    s = t[t.index("[STORAGE]"):t.index("[CONDUITS]")]
    assert "PND_X" not in j, "塘不该出现在 [JUNCTIONS]"
    assert "PND_X" in s
    assert "\nA  " in j                              # 普通井还在


def test_应急堰必须带闸(tmp_path):
    """不带闸会从堰倒灌回管网，三个汇水区那轮漏过 672 m³。"""
    p = {"node": "PND_X", "z_floor": 5.0, "depth": 2.0, "curve": [(0.0, 100.0), (2.0, 100.0)],
         "in_node": "A", "in_z": 5.3, "in_area": 0.5, "weir_z": 7.0}
    xy = {"A": (0.0, 0.0), "PND_X": (5.0, 5.0)}
    f = tmp_path / "t.inp"
    BC.write_inp(f, xy, {"A": 8.0, "PND_X": 7.0}, {"A": 6.0, "PND_X": 5.0},
                 [], set(), {k: 0.0 for k in xy}, ponds=[p], log=lambda *a: None)
    t = f.read_text(encoding="utf-8")
    # 只在 [WEIRS] 段里找 —— [XSECTIONS] 里也有 OW_ 开头的行，而且排在前面
    sec = t[t.index("[WEIRS]"):t.index("[PUMPS]")]
    w = [ln for ln in sec.splitlines() if ln.startswith("OW_PND_X")][0]
    assert w.split()[-2] == "YES", w


# ---------------------------------------------------------------- 孔口定径
def test_孔口面积按出流公式反算():
    q, h = 1.0, 1.0
    a = BC._orifice_area(q, h)
    assert BC.CD * a * math.sqrt(2 * 9.81 * h) == pytest.approx(q, rel=1e-9)


def test_要排的水越多孔口越大():
    v = [BC._orifice_area(q, 1.0) for q in (0.1, 0.5, 2.0)]
    assert v == sorted(v)


def test_出水口按二十四小时排空_比进水口小得多():
    """不限流的重力出水会让塘变成旁通管，暴雨期间存不住水。"""
    V, depth = 100_000.0, 2.0
    a_in = BC._orifice_area(V / (BC.FILL_H * 3600.0), depth / 2)
    a_out = BC._orifice_area(V / (BC.EMPTY_H * 3600.0), depth / 2)
    assert a_out < a_in / 10
    assert BC.EMPTY_H == 24.0 and BC.FILL_H == 1.0


# ---------------------------------------------------------------- 产流口径
def test_产流按节点注入_不用子汇水区(tmp_path):
    """SWMM 的 CURVE_NUMBER 下渗结果依赖子汇水区划分：
    同样 CN=82，1,726 m² 的小区跑出 26.9 mm、10 ha 的大区跑出 17.9 mm，
    而纯 SCS 是 23.53 mm。所以改成按公式算好再注入，与划分无关。"""
    xy = {"A": (0.0, 0.0)}
    f = tmp_path / "t.inp"
    BC.write_inp(f, xy, {"A": 10.0}, {"A": 8.0}, [], set(), {"A": 10_000.0},
                 log=lambda *a: None)
    t = f.read_text(encoding="utf-8")
    assert "[SUBCATCHMENTS]" not in t and "[INFILTRATION]" not in t
    line = [ln for ln in t.splitlines() if ln.startswith("A  FLOW  TS_RUNOFF")][0]
    c = line.split()
    assert c[3] == "FLOW" and float(c[5]) == pytest.approx(1.0)   # Sfactor = 1 ha


def test_面积缩放因子就是公顷数(tmp_path):
    xy = {"A": (0.0, 0.0), "B": (1.0, 0.0)}
    f = tmp_path / "t.inp"
    BC.write_inp(f, xy, {k: 10.0 for k in xy}, {k: 8.0 for k in xy}, [], set(),
                 {"A": 25_000.0, "B": 0.0}, log=lambda *a: None)
    t = f.read_text(encoding="utf-8")
    a = [ln for ln in t.splitlines() if ln.startswith("A  FLOW")][0].split()
    assert float(a[5]) == pytest.approx(2.5)                       # 25,000 m² = 2.5 ha
    assert not [ln for ln in t.splitlines() if ln.startswith("B  FLOW")]   # 0 面积不写


def test_单位过程线的体积等于解析值():
    """SCS 产流深 × 1 ha，误差必须在 2% 内 —— 这是缺口分析的分母。"""
    import runoff as R
    import numpy as np
    pts = BC.unit_runoff()
    dt_h = BC.RAIN_STEP_MIN / 60.0
    vol = sum(v for _, v in pts) * dt_h * 3600.0
    want = 10_000.0 * float(R.scs_cum_excess(np.array([BC.P_MM]), BC.CN)[0]) / 1000.0
    assert abs(vol - want) / want < 0.02, (vol, want)


def test_产流深就是二十三点五三毫米():
    import runoff as R
    import numpy as np
    q = float(R.scs_cum_excess(np.array([BC.P_MM]), BC.CN)[0])
    assert q == pytest.approx(23.53, abs=0.01)


def test_不允许积水回落():
    """ALLOW_PONDING NO：溢出即损失，Gap 才有干净的物理含义。"""
    import inspect
    src = inspect.getsource(BC.write_inp)
    assert '("ALLOW_PONDING", "NO")' in src


def test_库容曲线只在第一行写类型():
    """SWMM 只认第一行的 Storage / Pump4 关键字。

    每行都写会逐行报 ERROR 211: invalid number Storage，而且是解析阶段就退出 ——
    全市 8 个 L2 分片里含塘的 5 个就这么挂过，rpt 只剩 2 KB，
    却仍然带着「Analysis ended」，从完成标记上完全看不出来。
    """
    import inspect
    import re
    src = inspect.getsource(BC.write_inp)
    m = re.search(r"\[CURVES\]\"\)(.*?)ap\(\"\"\)", src, re.S)
    assert m, "找不到 [CURVES] 段"
    # 注释里也会出现 Storage / Pump4，只数代码行
    body = chr(10).join(ln for ln in m.group(1).splitlines()
                     if not ln.strip().startswith("#"))
    # 曲线点那一行不许无条件带 Storage
    assert 'Storage  {d:' not in body, "库容曲线每行都写了 Storage"
    assert 'i == 0' in body, "没有按「只有第一行带类型」写"
    # 泵曲线同理
    assert body.count("Pump4") == 1, "泵曲线也不能每行都写 Pump4"


def test_库容曲线要的是水面面积不是平均面积():
    """SWMM 按梯形积分还原体积，所以面积必须是 dV/dh。

    原来写成 V/h（到该深度为止的平均面积）。对等截面的塘两者数值接近，
    所以这个错一直没被发现 —— 判据要拿一个**非等截面**的例子来判。
    """
    # V = h²：水面面积应当 ∝ 2h，平均面积只有 h，两者差一倍
    ss = [(1.0, 1.0), (2.0, 4.0), (3.0, 9.0)]
    curve, how = BC.storage_curve(ss, depth=3.0, V=9.0)
    assert how == "台账逐级库容的增量面积"
    a = dict(curve)
    assert a[2.0] == pytest.approx(3.0, rel=1e-6), a   # (4-1)/(2-1)
    assert a[3.0] == pytest.approx(5.0, rel=1e-6), a   # (9-4)/(3-2)
    # 梯形积分要还原出总体积
    pts = sorted(curve)
    vol = sum(0.5 * (a0 + a1) * (h1 - h0)
              for (h0, a0), (h1, a1) in zip(pts, pts[1:]))
    assert vol == pytest.approx(9.0, rel=0.35), vol


def test_零深度的塘不写出无穷大的水面():
    """台账里有三座塘 z_floor == z_top，stage_storage 全挤在 [0, 0.02]。

    旧代码 V/max(h,1e-6) 会写出 5.6e11 m² 的水面（比整个酋长国还大），
    SWMM 报 ERROR 171 整个分片退出 —— 而且 rpt 里仍带「Analysis ended」，
    从完成标记上看不出来。
    """
    ss = [(0.0, 562031.0), (0.0, 562372.0), (0.01, 563394.0), (0.01, 563735.0)]
    curve, how = BC.storage_curve(ss, depth=0.0, V=561690.0)
    assert "等截面" in how, how
    areas = [a for _, a in curve]
    # 设计深度被兜到 0.3 m，水面面积 = V/0.3 ≈ 1.87e6，不该出现 1e9 量级
    assert max(areas) < 1e7, max(areas)
    assert max(areas) == pytest.approx(561690.0 / 0.3, rel=1e-6)


def test_曲线深度严格递增():
    """写成 {d:.3f} 后塌成同一个 X 同样是 ERROR 171。"""
    ss = [(0.001, 10.0), (0.002, 20.0), (0.4, 100.0), (0.4004, 101.0), (0.9, 200.0)]
    curve, _ = BC.storage_curve(ss, depth=1.0, V=200.0)
    hs = [round(h, 3) for h, _ in curve]
    assert hs == sorted(hs), hs
    assert len(hs) == len(set(hs)), hs
