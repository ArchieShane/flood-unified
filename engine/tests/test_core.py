"""核心公式与累加逻辑的单元测试。运行：python -m pytest tests -q  或  python tests/test_core.py"""
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
import networkx as nx
from swbrain import hydraulics as hy, graph


def test_manning_known_value():
    # DN300, S=0.5%, n=0.011 → 约 0.081 m³/s（手算：A=0.0707, R=0.075）
    q = float(hy.manning_full_flow(0.3, 0.005, 0.011))
    assert abs(q - 0.0808) < 0.003, q


def test_rational_and_volume():
    q = float(hy.rational_q(10_000, 40, 0.85))          # 1 ha, 40 mm/h
    assert abs(q - 0.0944) < 0.001, q
    v = float(hy.runoff_volume(10_000, 25, 0.85))        # 1 ha, 25 mm
    assert abs(v - 212.5) < 0.01, v


def test_required_diameter():
    d = hy.required_diameter_mm(0.5, 0.003, 0.011, 0.85, [300, 400, 500, 600, 800, 1000])
    assert d in (600.0, 800.0), d
    assert hy.manning_full_flow(d / 1000, 0.003, 0.011) * 0.85 >= 0.5


def test_edge_slope_flags():
    e = pd.DataFrame({"invert_up": [10.0, 10.0, 10.0, None], "invert_dn": [9.5, 10.2, 9.99, None],
                      "len_m": [100.0, 100.0, 100.0, 100.0], "invert_valid": [True, True, True, False]})
    s, f = hy.edge_slope(e, 0.0005, 0.002)
    assert list(f) == ["ok", "逆坡", "过平", "无标高"]
    assert abs(s[0] - 0.005) < 1e-9 and s[1] == 0.0005 and s[2] == 0.0005 and s[3] == 0.002


def test_accumulate_tree_and_terminal():
    # 1→3, 2→3, 3→4(出水口)；1、2 各挂 2000 m²
    g = nx.DiGraph()
    g.add_edge(1, 3, gid=11); g.add_edge(2, 3, gid=12); g.add_edge(3, 4, gid=13)
    area, cnt, uplen, term = graph.accumulate(g, {1: 2000.0, 2: 2000.0}, {11: 50.0, 12: 50.0, 13: 80.0})
    assert area[11] == 2000 and area[12] == 2000 and area[13] == 4000
    assert cnt[13] == 2 and uplen[13] == 100.0
    assert term[1] == 4 and term[2] == 4 and term[4] == 4


def test_accumulate_split():
    # 1→2, 2→3, 2→4：2 的出边均分
    g = nx.DiGraph()
    g.add_edge(1, 2, gid=1); g.add_edge(2, 3, gid=2); g.add_edge(2, 4, gid=3)
    area, cnt, uplen, term = graph.accumulate(g, {1: 4000.0}, {})
    assert area[2] == 2000 and area[3] == 2000


def test_build_dag_breaks_cycle():
    e = pd.DataFrame({"gid": [1, 2, 3, 4], "u": [1, 2, 3, 3], "v": [2, 3, 1, 4], "orient": ["asset", "asset", "guess", "asset"]})
    g, removed = graph.build_dag(e)
    assert nx.is_directed_acyclic_graph(g) and removed == [3]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok", name)
