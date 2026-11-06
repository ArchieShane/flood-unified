# -*- coding: utf-8 -*-
"""L1 / L2 / L2+ 规划结果的本体动作（swbrain/ontology/actions/plans.py）。

守五条：
  1. 各态缺口只和**上一态**比（主线），对照态（L2+B）和它的父态（L2）比——不能把并列方案排成一条链去算相邻差；
  2. 态名宽松识别：「L2+B 放宽」「L2+A+B（放宽）」「l2ab」都能对上；
  3. 「现有方案做完还要补什么」只数本系统补的箱涵，不把 L2+A 的塘算进「补的」；
  4. 控制台深链接带视图 / 态 / 基准，设施编号不进链接（控制台按积水点解析不了）；
  5. 规则规划器把这类问法路由到新动作，不抢积水点的问法。
不读真实数据：ROOT 指到临时目录，目录文件现写。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from swbrain.ontology import agent as A  # noqa: E402
from swbrain.ontology import runtime as R  # noqa: E402

CAT = {"version": 1, "regions": {
    "Mussafah_00": {"label": "Mussafah_00", "aliases": ["mussafah"], "scenario": "61 mm / 2 h", "model": "m",
                    "chain": ["L1", "L2", "L2+A", "L2+A+B"], "refs": {"L2+B": "L2"},
                    "states": {"L1": "既有", "L2": "≡ L1", "L2+A": "Parsons", "L2+A+B": "其上加箱涵", "L2+B": "对照"},
                    "metrics": [{"key": "flood", "zh": "溢出量", "en": "Overflow", "unit": "m³", "better": "low",
                                 "values": {"L1": 100.0, "L2": 100.0, "L2+A": 80.0, "L2+A+B": 40.0, "L2+B": 50.0}}],
                    "facilities": {"L2+A": [{"id": "STO-PO3", "state": "L2+A", "kind": "pond", "kind_zh": "临时塘", "source": "Parsons", "volume_m3": 1200, "lon": 54.5, "lat": 24.3}],
                                   "L2+A+B": [{"id": "STO-PO3", "state": "L2+A", "kind": "pond", "kind_zh": "临时塘", "source": "Parsons", "volume_m3": 1200, "lon": 54.5, "lat": 24.3},
                                              {"id": "NP1", "state": "L2+A+B", "kind": "box", "kind_zh": "地下箱涵", "source": "本系统", "volume_m3": 5000, "land": "open_land", "moved_m": 60, "lon": 54.51, "lat": 24.31}]},
                    "overlap": "重叠", "notes": ["口径"], "console": {"tab": "line2p", "l2p": "wb"}},
    "29.RB2": {"label": "29.RB2", "aliases": ["rb2", "29.rb2"], "scenario": "61 mm / 2 h", "model": "m", "chain": ["L1", "L2", "L2+A", "L2+A+B"],
               "refs": {"L2+B": "L2", "L2+B（放宽）": "L2"},
               "states": {"L1": "a", "L2": "b", "L2+A": "PO 塘方案（来源待确认）", "L2+A+B": "c", "L2+B": "d", "L2+B（放宽）": "e"},
               "metrics": [{"key": "a30", "zh": "≥0.30 m 面积", "en": "Area", "unit": "ha", "better": "low",
                            "values": {"L1": 50.0, "L2": 50.0, "L2+A": 48.0, "L2+A+B": 46.0, "L2+B": 49.0, "L2+B（放宽）": 46.5}}],
               "facilities": {"L2+B（放宽）": [{"id": "NP9", "state": "L2+B（放宽）", "kind": "box", "kind_zh": "地下箱涵", "source": "本系统", "volume_m3": 800, "lon": 54.6, "lat": 24.2}]},
               "overlap": None, "notes": ["PO 塘的设计方未确认"], "console": None}},
    "city": {"basis": "一维", "scenario": "61 mm / 2 h", "L1_flood_m3": 5485120, "L2_flood_m3": 5343916, "diff_m3": 141204, "diff_pct": 2.57,
             "pond_networks": {"n": 15, "diff_pct": 13.7}, "outside_client_catch_pct": 54, "nodes": {"n_nodes": 10, "n_better": 3, "n_worse": 1},
             "coupled_2d": {"status": "running", "res_m": 30, "domain_km2": 1233.6}, "notes": ["差值是下限"], "console": {"tab": "line2p", "l2p": "city", "m": "diff"}}}


class B:
    cfg = {"agent": {"action_log": "out/_test_actions.jsonl"}}
    active_rp = "10"


@pytest.fixture
def rt(tmp_path, monkeypatch):
    d = tmp_path / "console" / "data"; d.mkdir(parents=True)
    (d / "l2plus_catalog.json").write_text(json.dumps(CAT, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(R, "ROOT", tmp_path)
    return R.Runtime(B(), B.cfg)


def test_主线只和上一态比_对照和父态比(rt):
    o = rt.act("state_gap", {"region": "Mussafah"})
    assert o["ok"], o
    st = o["result"]["rows"][0]["step"]
    # 给人看的结果用显示名（L2+A → L2+planned，L2+A+B → L2+suggested，2026-09-28）；深链接里仍是键
    assert st["L2+suggested"]["vs"] == "L2+planned" and round(st["L2+suggested"]["pct"]) == -50       # 80 → 40
    assert st["L2+B"]["vs"] == "L2" and round(st["L2+B"]["pct"]) == -50              # 对照：100 → 50，不和 L2+suggested 比
    assert "L2+suggested" not in [k for k, v in st.items() if v["vs"] == "L2+B"]
    assert o["console_link"] == "/?tab=line2p&v=wb&st=L2%2BA%2BB&base=L2%2BA"
    assert "L2+A" not in o["summary_zh"] and "L2+A" not in o["summary_en"]


def test_新态名也认(rt):
    for s, want in (("L2+planned", "L2+planned"), ("l2+suggested", "L2+suggested"), ("L2+A", "L2+planned")):
        o = rt.act("facility_plan", {"region": "Mussafah", "state": s})
        assert o["ok"] and o["result"]["state"] == want, (s, o.get("error"), o.get("result", {}).get("state"))


def test_不给片区列出有结果的片区(rt):
    o = rt.act("state_gap", {})
    assert o["ok"] and {r["region"] for r in o["result"]["regions"]} == {"Mussafah_00", "29.RB2"}


def test_态名宽松识别(rt):
    for s in ("L2+B 放宽", "l2+b（放宽）", "L2B relaxed"):
        o = rt.act("facility_plan", {"region": "rb2", "state": s})
        assert o["ok"] and o["result"]["state"] == "L2+B（放宽）", (s, o)
    assert not rt.act("facility_plan", {"region": "rb2", "state": "L9"})["ok"]


def test_还要补什么只数本系统补的箱涵(rt):
    o = rt.act("as_built_next", {"region": "Mussafah_00"})
    assert o["ok"], o
    v = o["result"]["variants"][0]
    assert v["n_added"] == 1 and v["added_m3"] == 5000                               # STO-PO3 是 Parsons 的，不算「补的」
    assert "还要补 1 座地下箱涵" in o["summary"]
    assert all(h["kind"] == "fac" for h in o["ui"]["highlight"])
    assert "hl=" not in (o["console_link"] or "")                                     # 设施编号不进链接


def test_全市(rt):
    o = rt.act("city_gap", {})
    assert o["ok"] and "14.1 万 m³" in o["summary"] and "还在算" in o["summary"]
    assert o["console_link"] == "/?tab=line2p&v=city&m=diff"


def test_规则规划器路由(rt):
    pl = A.RulePlanner(rt) if hasattr(A, "RulePlanner") else [c for c in vars(A).values() if isinstance(c, type) and hasattr(c, "plan") and hasattr(c, "compose")][0](rt)
    assert pl.plan("如果 Parsons 方案已经定了，Mussafah 还要补什么？") == [("as_built_next", {"region": "Mussafah_00"})]
    assert pl.plan("全市 L1 和 L2 差多少？") == [("city_gap", {})]
    assert pl.plan("29.RB2 的 L2+B 放宽要建哪些箱涵？") == [("facility_plan", {"region": "29.RB2", "state": "L2+B（放宽）"})]
    assert pl.plan("哪些片区有 L1/L2 对比？") == [("state_gap", {})]
    assert pl.plan("ADM-1 什么时候能退水")[0][0] == "recession_options"             # 积水点的问法不被抢


def test_主答案的联动不被后续查询盖掉():
    """DeepSeek 实测（2026-09-28）：as_built_next 之后又调了 facility_plan L2+A，控制台被切回 L2+A。"""
    from swbrain.ontology.common import merge_ui
    ui = {}
    merge_ui(ui, {"tab": "line2p", "l2p": "wb", "st": "L2+A+B", "base": "L2+A", "highlight": [1, 2], "pin": True})
    merge_ui(ui, {"tab": "line2p", "l2p": "wb", "st": "L2+A", "highlight": [3], "rp": "10"})
    assert ui["st"] == "L2+A+B" and ui["highlight"] == [1, 2] and ui["rp"] == "10"
    ui2 = merge_ui({}, {"st": "L2+A"}); merge_ui(ui2, {"st": "L2+B"})
    assert ui2["st"] == "L2+B"                                   # 没有 pin 时照旧后一步覆盖
