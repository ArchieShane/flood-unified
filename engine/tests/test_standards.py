"""法定设计标准模块：IDF 公式与表格一致、汇流时间取最长路径、分类优先级、规范标注。"""
import numpy as np
import pandas as pd
import networkx as nx
import tomllib
from pathlib import Path
from swbrain import standards as st

CFG = tomllib.loads((Path(__file__).resolve().parents[1] / "config.example.toml").read_text(encoding="utf-8"))


def test_idf_equation_matches_table_zone_b():
    # 表 3-6 与表 3-7 应互相印证：15 分钟与 60 分钟两档，误差 < 3%
    for rp in (5, 10, 25, 50, 100):
        for d in (15, 60, 1440):
            eq, tb = st.idf_intensity(CFG, rp, d), st.idf_table(CFG, rp, d)
            assert abs(eq - tb) / tb < 0.03, (rp, d, eq, tb)
    assert abs(st.idf_intensity(CFG, 100, 15) - 138.54) < 1.5
    assert abs(st.idf_intensity(CFG, 5, 15) - 51.04) < 1.0
    assert st.idf_intensity(CFG, 50, 2) == st.idf_intensity(CFG, 50, 5)      # 历时下限 5 分钟


def test_time_of_concentration_longest_path():
    # 1→3 长管慢流，2→3 短管；3→4 出口。4 的 tc 应沿 1→3→4
    g = nx.DiGraph()
    g.add_edge(1, 3, gid=11); g.add_edge(2, 3, gid=12); g.add_edge(3, 4, gid=13)
    es = pd.DataFrame({"gid": [11, 12, 13], "len_m": [600.0, 60.0, 300.0], "pipe_diameter": [300.0, 300.0, 600.0],
                       "q_full": [0.0707 * 1.0, 0.0707 * 1.0, 0.2827 * 1.0]})   # 满管流速 1 m/s
    tc = st.time_of_concentration(es, g, CFG)
    assert abs(tc.iloc[0] - (10 + 10)) < 0.05          # 600 m / 1 m/s = 10 min + 入流 10
    assert abs(tc.iloc[1] - (10 + 1)) < 0.05
    assert abs(tc.iloc[2] - (10 + 10 + 5)) < 0.05      # 走最长路径 1→3（10）再 300 m（5）


def test_design_intensity_and_compliance():
    es = pd.DataFrame({"gid": [1, 2], "area_m2": [6000.0, 6000.0], "q_full": [0.05, 0.5], "pipe_diameter": [300.0, 900.0], "len_m": [100.0, 100.0], "is_active": [True, True]})
    cls = pd.DataFrame({"gid": [1, 2], "design_class": ["underpass", "low_density"], "design_rp_yr": [50, 5], "check_rp_yr": [100, 0], "tc_multiplier": [1.0, 3.0]})
    tc = pd.Series([10.0, 10.0])
    d = st.design_intensity(es, cls, tc, CFG)
    assert d.loc[0, "design_duration_min"] == 10 and d.loc[1, "design_duration_min"] == 30
    assert d.loc[0, "design_i_mmh"] > d.loc[1, "design_i_mmh"] * 3      # 下穿 50 年 10 分钟 vs 低密度 5 年 30 分钟
    assert np.isfinite(d.loc[0, "ratio_check"]) and np.isnan(d.loc[1, "ratio_check"])
    assert bool(d.loc[1, "compliant"]) and not bool(d.loc[0, "compliant"])
    es2 = es.merge(d, on="gid")
    summ = st.compliance_summary(es2, CFG)
    assert summ["不达标km"] == 0.1 and {b["class"] for b in summ["按类别"]} == {"underpass", "low_density"}


def test_annotate_proposals_soakaway_flag():
    props = pd.DataFrame({"kind": ["新增渗井群", "扩管改造", "核实终点（疑似漏登出水口/泵站）"], "priority": [80.0, 70.0, 90.0]})
    p = st.annotate_proposals(props, CFG)
    assert p.loc[0, "regulatory"].startswith("WA-726-1 §4.12") and p.loc[0, "priority"] == 68.0 and p.loc[0, "regulatory_flag"] == "需个案批准"
    assert "Manning" in p.loc[1, "regulatory_en"] and p.loc[1, "priority"] == 70.0
    assert p.loc[2, "regulatory"] == ""
