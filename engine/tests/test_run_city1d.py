# -*- coding: utf-8 -*-
"""分片合计前的准入判据。

为什么值得单独一个测试文件：今天这一类「静默失败被当成 0 加进合计」踩了三次，
而且每次的假象都不一样 ——
  · 报错的 rpt 里**仍然带着「Analysis ended」**，按完成标记数出「16 片完成 5 片」，
    那 5 片全是 ERROR 211 退出的废数据；
  · 加了「无 ERROR」还不够：重跑后读到 p6/p8 报错，其实是**上一轮的残留 rpt**，
    比 inp 还旧，新一轮根本还没轮到它们；
  · 根因在 `facts()` —— 它读报错的 rpt 时每一项都走 `or 0`，
    于是失败的分片变成「这片没水」，全市合计看上去完全正常。

所以判据要三条一起：rpt 在 / 比 inp 新 / 无 ERROR 且跑完。
下面每条都用一个**已知会出问题的样本**去验，确保判据不是恒真。
"""
from __future__ import annotations

import os
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import run_city1d as RC  # noqa: E402

GOOD = """
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)

  Flow Routing Continuity        Volume        Volume
                                hectare-m      10^6 ltr
  External Inflow .........        1.000         10.000
  External Outflow ........        0.800          8.000
  Flooding Loss ...........        0.200          2.000
  Final Stored Volume .....        0.000          0.000
  Continuity Error (%) ....       -0.010

  Analysis begun on:  Sat Sep 26 15:19:00 2026
  Analysis ended on:  Sat Sep 26 16:59:00 2026
  Total elapsed time: 01:40:00
"""

# 真实样本：解析阶段就退出，但**末尾仍有「Analysis ended」**
ERR_BUT_ENDED = """
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)

  ERROR 211: invalid number Storage at line 127383 of [CURVE] section:
  CRV_PND_SW_MAR_RD55_RZS_34  Storage  0.280  43632.1

  Analysis begun on:  Sat Sep 26 15:19:00 2026
  Analysis ended on:  Sat Sep 26 15:19:01 2026
"""

RUNNING = """
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)

  Analysis begun on:  Sat Sep 26 15:19:00 2026
"""


def _pair(tmp_path, rpt_text, rpt_older=False):
    inp = tmp_path / "City_L2_p1.inp"
    rpt = tmp_path / "City_L2_p1.rpt"
    inp.write_text("[TITLE]\n", encoding="utf-8")
    rpt.write_text(rpt_text, encoding="utf-8")
    if rpt_older:
        old = time.time() - 3600
        os.utime(rpt, (old, old))
    else:
        new = time.time() + 5
        os.utime(rpt, (new, new))
    return rpt, inp


def test_正常的分片可以计入(tmp_path):
    ok, why = RC.part_ok(*_pair(tmp_path, GOOD))
    assert ok, why


def test_报错但带完成标记的分片不计入(tmp_path):
    """最阴的一种：rpt 里有 ERROR，末尾却仍写着 Analysis ended。"""
    ok, why = RC.part_ok(*_pair(tmp_path, ERR_BUT_ENDED))
    assert not ok
    assert "211" in why, why


def test_上一轮的残留rpt不计入(tmp_path):
    """内容完好，但比 inp 旧 —— 是上一轮的产物，新一轮还没轮到它。"""
    ok, why = RC.part_ok(*_pair(tmp_path, GOOD, rpt_older=True))
    assert not ok
    assert "旧" in why or "残留" in why, why


def test_还在跑的分片不计入(tmp_path):
    ok, why = RC.part_ok(*_pair(tmp_path, RUNNING))
    assert not ok
    assert "跑完" in why, why


def test_没有rpt不计入(tmp_path):
    ok, why = RC.part_ok(tmp_path / "缺席.rpt", tmp_path / "缺席.inp")
    assert not ok
    assert "rpt" in why


def test_报错的分片facts会全返回零():
    """这一条是上面判据存在的**理由**，必须一起锁住。

    如果哪天 facts() 改成对报错的 rpt 抛异常，这个测试会红 ——
    那时可以放松判据；只要它还静默返回 0，判据就一条都不能少。
    """
    import tempfile
    d = pathlib.Path(tempfile.mkdtemp())
    f = d / "e.rpt"
    f.write_text(ERR_BUT_ENDED, encoding="utf-8")
    fx = RC.facts(f)
    assert fx["inflow_m3"] == 0 and fx["flood_m3"] == 0, fx
    assert fx["err_route"] is None, fx


def test_有一片不合格就把合计标成残缺(tmp_path, monkeypatch):
    """合计不能在缺片的情况下装作正常。"""
    monkeypatch.setattr(RC, "WORK", tmp_path)
    for i in (1, 2):
        (tmp_path / f"City_L1_p{i}.inp").write_text("[TITLE]\n", encoding="utf-8")
        r = tmp_path / f"City_L1_p{i}.rpt"
        r.write_text(GOOD if i == 1 else ERR_BUT_ENDED, encoding="utf-8")
        n = time.time() + 5
        os.utime(r, (n, n))
    out = RC.report_parts(["L1"], "", 2, log=lambda *a: None)
    agg = out["L1"]
    assert agg["complete"] is False
    assert agg["n_parts_ok"] == 1 and agg["n_parts"] == 2
    assert agg["bad_parts"] and agg["bad_parts"][0][0] == 2
    # 只计入了好的那一片：0.2 hectare-m = 2,000 m³
    assert agg["flood_m3"] == pytest.approx(2000.0, rel=1e-6), agg["flood_m3"]


def _inp(tmp_path, name, end="12:00:00", start="00:00:00"):
    p = tmp_path / name
    p.write_text(f"[OPTIONS]\nSTART_TIME  {start}\nEND_TIME  {end}\n", encoding="utf-8")
    return p


def test_逐片读出演算窗口(tmp_path):
    assert RC.part_window_h(_inp(tmp_path, "a.inp", "12:00:00")) == pytest.approx(12.0)
    assert RC.part_window_h(_inp(tmp_path, "b.inp", "06:00:00")) == pytest.approx(6.0)
    # 跨天：END 小于 START 要补 24 h，否则会算出负数窗口
    assert RC.part_window_h(_inp(tmp_path, "c.inp", "02:00:00", "23:00:00")) == pytest.approx(3.0)
    assert RC.part_window_h(tmp_path / "没有这个.inp") is None


def test_同一态里混用窗口要被报出来(tmp_path, monkeypatch):
    """L1 八片都是 12 h，而 L2 有三片 12 h、五片 6 h。

    窗口不同 = 溢出量在不同积分区间上比。这场雨 4 h 就定型所以实际等价，
    但**等价要证，不能假设** —— 不报出来，合计看上去完全正常，没人会去问。
    """
    monkeypatch.setattr(RC, "WORK", tmp_path)
    for i, end in enumerate(("12:00:00", "06:00:00"), 1):
        _inp(tmp_path, f"City_L2_p{i}.inp", end)
        r = tmp_path / f"City_L2_p{i}.rpt"
        r.write_text(GOOD, encoding="utf-8")
        n = time.time() + 5
        os.utime(r, (n, n))
    out = RC.report_parts(["L2"], "", 2, log=lambda *a: None)
    assert out["L2"]["windows_h"] == [6.0, 12.0], out["L2"]["windows_h"]


def test_窗口不一致时汇总要出警告(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(RC, "WORK", tmp_path)
    rows = {
        "L1": {"inflow_m3": 1e5, "outflow_m3": 8e4, "flood_m3": 2e4, "stored_m3": 0,
               "err_route": 0.1, "elapsed": "8/8 片", "n_flooded_nodes": 10,
               "complete": True, "windows_h": [12.0]},
        "L2": {"inflow_m3": 1e5, "outflow_m3": 8.5e4, "flood_m3": 1.5e4, "stored_m3": 0,
               "err_route": 0.1, "elapsed": "8/8 片", "n_flooded_nodes": 8,
               "complete": True, "windows_h": [6.0, 12.0]},
    }
    RC._print(rows)
    txt = capsys.readouterr().out
    assert "混用" in txt, "同一态里混用窗口没被点出来"
    assert "不一致" in txt, "两态窗口不同没被点出来"
    assert "参考" in txt, "没说清这种情况下 Gap 只能当参考"
