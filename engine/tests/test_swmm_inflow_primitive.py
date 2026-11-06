# -*- coding: utf-8 -*-
"""双向耦合的核心原语：node_set_total_inflow 真的把水送进一维了吗？

2D→1D 那半边全靠这一个接口。如果它只是被忽略、或者**替换**掉了管道来流而不是叠加，
耦合结果会系统性错，而且从大模型的结果上看不出来——所以在一个两节点的最小模型上单独验。

判据（不靠读文档，靠 SWMM 自己的水量平衡表）：
  1. 注了水的那次，External Inflow 必须比不注的那次**多出恰好注入的量**；
  2. 管道来流不能被吃掉——下游出水口的排放量要跟着涨，而不是不变或变少。

这个测试不依赖客户模型，只要装了 swmm-toolkit 就能跑。
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

INP = """[TITLE]
双向耦合原语自检：一根管 + 一个出水口

[OPTIONS]
FLOW_UNITS           LPS
FLOW_ROUTING         DYNWAVE
START_DATE           01/01/2000
START_TIME           00:00:00
END_DATE             01/01/2000
END_TIME             02:00:00
REPORT_STEP          00:05:00
WET_STEP             00:05:00
DRY_STEP             00:05:00
ROUTING_STEP         00:00:05
ALLOW_PONDING        NO
LINK_OFFSETS         DEPTH
MIN_SLOPE            0

[JUNCTIONS]
J1  10.0  5.0  0  0  0

[OUTFALLS]
O1  8.0  FREE  NO

[CONDUITS]
C1  J1  O1  100  0.013  0  0  0  0

[XSECTIONS]
C1  CIRCULAR  1.0  0  0  0

[COORDINATES]
J1  0    0
O1  100  0
"""


def _run(tmp, inject_lps: float, seconds: float):
    """跑一遍；inject_lps > 0 时在 J1 上按 node_set_total_inflow 注水 seconds 秒。"""
    from swmm.toolkit import solver
    from swmm.toolkit.shared_enum import ObjectType

    d = tmp / f"inj{int(inject_lps)}"
    d.mkdir()
    inp = d / "t.inp"
    inp.write_text(INP, encoding="utf-8")
    rpt, out = d / "t.rpt", d / "t.out"
    solver.swmm_open(str(inp), str(rpt), str(out))
    solver.swmm_start(1)
    i = solver.project_get_index(ObjectType.NODE, "J1")
    elapsed = 0.0
    while True:
        if inject_lps > 0:
            solver.node_set_total_inflow(i, inject_lps if elapsed * 86400.0 < seconds else 0.0)
        prev = elapsed
        elapsed = solver.swmm_step()
        if elapsed <= 0 or elapsed <= prev:
            break
    solver.swmm_end()
    solver.swmm_report()
    solver.swmm_close()
    return rpt.read_text(encoding="utf-8", errors="replace")


def _m3(txt: str, key: str):
    """从水量平衡表取 10^6 ltr 那一列，换成 m³。"""
    m = re.search(rf"{re.escape(key)}\s*\.*\s+(-?\d[\d.]*)\s+(-?\d[\d.]*)", txt)
    return float(m.group(2)) * 1000.0 if m else None


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("swmm") is None,
    reason="没装 swmm-toolkit")
def test_set_total_inflow_actually_adds_water(tmp_path):
    """注进去的水必须出现在 External Inflow 里，且量对得上。"""
    Q_LPS, SECS = 50.0, 600.0            # 50 L/s 注 10 分钟 = 30 m³
    a = _run(tmp_path, 0.0, SECS)
    b = _run(tmp_path, Q_LPS, SECS)
    in_a, in_b = _m3(a, "External Inflow"), _m3(b, "External Inflow")
    assert in_a is not None and in_b is not None, "水量平衡表没解析出来"
    want = Q_LPS / 1000.0 * SECS         # 30 m³
    got = in_b - in_a
    assert got == pytest.approx(want, rel=0.05), \
        f"注了 {want:.1f} m³，External Inflow 只多了 {got:.1f} m³ —— 接口没把水送进去"


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("swmm") is None,
    reason="没装 swmm-toolkit")
def test_set_total_inflow_does_not_replace_pipe_flow(tmp_path):
    """注水是**叠加**，不能把管道来流吃掉——出水口排放要跟着涨。"""
    Q_LPS, SECS = 50.0, 600.0
    a = _run(tmp_path, 0.0, SECS)
    b = _run(tmp_path, Q_LPS, SECS)
    out_a, out_b = _m3(a, "External Outflow"), _m3(b, "External Outflow")
    assert out_a is not None and out_b is not None
    assert out_b > out_a, "注了水但出水口排放没涨，说明来流被替换而不是叠加"
    assert (out_b - out_a) == pytest.approx(Q_LPS / 1000.0 * SECS, rel=0.10), \
        f"多排出 {out_b - out_a:.1f} m³，与注入的 {Q_LPS / 1000.0 * SECS:.1f} m³ 对不上"
