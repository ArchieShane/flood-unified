"""全网溢流路由：链式管网上游过不去的水不再进下游；分叉均分；梯形体积只对 d ≥ tc；无上游面积 → 无溢流。"""
import numpy as np
import pandas as pd
from swbrain import networkflood as nf

CFG = {"idf": {"durations_min": [15, 60, 1440], "return_periods": {"10": {"i15_mmh": 57.7, "p24_mm": 51.1}},
               "intensity_mmh": {"10": [57.7, 21.18, 2.13]}, "equation": {"10": {"alpha": 31.0, "beta": 0.62}}, "zone": "B"},
       "scenarios": {}}


def _es(rows):
    df = pd.DataFrame(rows)
    df["orient"] = "asset"; df["is_active"] = True; df["len_m"] = 100.0; df["zone_name"] = "Z"
    return df


def test_chain_caps_flow_and_spills_locally(tmp_path, monkeypatch):
    monkeypatch.setattr(nf, "STATE", tmp_path)
    # 1→2→3：上游段 A 累计面积 1 ha，下游段 B 累计 2 ha（B 自己再收 1 ha）。C=1，i=57.7 → A 段流量 K·57.7·1 = 0.16 m³/s
    es = _es([{"gid": 1, "u": 1, "v": 2, "area_m2": 10000.0, "runoff_c": 1.0, "q_full": 0.05, "tc_min": 10.0},
              {"gid": 2, "u": 2, "v": 3, "area_m2": 20000.0, "runoff_c": 1.0, "q_full": 10.0, "tc_min": 10.0}])
    prep = nf.prepare(es, cache=True)
    assert list(prep["gid"]) == [1, 2] and prep["load_m2"][prep["u"][0]] == 10000.0 and prep["load_m2"][prep["u"][1]] == 10000.0
    I = np.array([[57.7, 21.18, 2.13]] * 2); durs = np.array([15.0, 60.0, 1440.0])
    r = nf.route(prep, I, durs)
    qA = nf.K * 1.0 * 57.7 * 1.0
    assert abs(r["q_peak_m3s"][0] - qA) < 1e-9 and abs(r["spill_peak_m3s"][0] - (qA - 0.05)) < 1e-9
    # 下游 B 只收 A 能过去的 0.05 + 自己的 1 ha 径流，远小于 10 m³/s → 不溢
    assert abs(r["q_peak_m3s"][1] - (0.05 + qA)) < 1e-9 and r["spill_peak_m3s"][1] == 0.0 and not r["spilling"][1]
    # A 段梯形体积：d=15 ≥ tc=10 → e·((15−10)+10·e/q)·60
    e = qA - 0.05; v15 = e * ((15 - 10) + 10 * e / qA) * 60
    assert r["spilling"][0] and r["V_m3"][0] >= v15 - 1e-6
    # 第二次 prepare 走缓存
    prep2 = nf.prepare(es, cache=True); assert str(prep2["key"]) == str(prep["key"])


def test_split_and_zero_area(tmp_path, monkeypatch):
    monkeypatch.setattr(nf, "STATE", tmp_path)
    # 节点 1 有两条出边，各拿一半；一条无上游面积的支管不溢
    es = _es([{"gid": 1, "u": 1, "v": 2, "area_m2": 5000.0, "runoff_c": 0.8, "q_full": 0.001, "tc_min": 10.0},
              {"gid": 2, "u": 1, "v": 3, "area_m2": 5000.0, "runoff_c": 0.8, "q_full": 0.001, "tc_min": 10.0},
              {"gid": 3, "u": 4, "v": 2, "area_m2": 0.0, "runoff_c": 0.8, "q_full": 0.001, "tc_min": 10.0}])
    prep = nf.prepare(es, cache=False)
    assert prep["outdeg"][prep["u"][0]] == 2 and abs(prep["load_m2"][prep["u"][0]] - 10000.0) < 1e-6
    r = nf.route(prep, np.array([[57.7, 21.18, 2.13]] * 3), np.array([15.0, 60.0, 1440.0]))
    assert abs(r["q_peak_m3s"][0] - r["q_peak_m3s"][1]) < 1e-12 and r["spilling"][0] and r["spilling"][1]
    assert r["q_peak_m3s"][2] == 0.0 and not r["spilling"][2]


def test_run_summary_and_scenario_factor(tmp_path, monkeypatch):
    monkeypatch.setattr(nf, "STATE", tmp_path)
    es = _es([{"gid": 1, "u": 1, "v": 2, "area_m2": 10000.0, "runoff_c": 1.0, "q_full": 0.05, "tc_min": 10.0}])
    prep = nf.prepare(es, cache=False)
    r10 = nf.run(CFG, prep, "10")
    assert r10["scenario_key"] == "10" and r10["edges_spilling"] == 1 and r10["total_m3"] > 0 and r10["by_zone"][0]["zone_name"] == "Z"
    cfg_cc = {**CFG, "scenarios": {"cc": {"10cc": {"base_rp": 10, "factor": 1.2, "label": "cc", "label_en": "cc"}}}}
    rcc = nf.run(cfg_cc, prep, "10cc")
    assert rcc["total_m3"] > r10["total_m3"] and "edge_V" not in nf.public(rcc)


def test_backwater_flags_near_full_reaches_upstream_of_spill(tmp_path, monkeypatch):
    monkeypatch.setattr(nf, "STATE", tmp_path)
    # Z(9→1) → A(1→2, 350 m) → B(2→3) 在人孔 2 溢流。A 的下游端就是溢流人孔（距离 0）且利用率 0.9 → 顶托；Z 的下游端离溢流人孔 350 m → 不标；B 自己溢流不标
    qA = nf.K * 1.0 * 57.7 * 1.0
    es = _es([{"gid": 9, "u": 9, "v": 1, "area_m2": 5000.0, "runoff_c": 1.0, "q_full": nf.K * 57.7 * 0.5 / 0.9, "tc_min": 10.0},
              {"gid": 1, "u": 1, "v": 2, "area_m2": 10000.0, "runoff_c": 1.0, "q_full": qA / 0.9, "tc_min": 10.0},
              {"gid": 2, "u": 2, "v": 3, "area_m2": 20000.0, "runoff_c": 1.0, "q_full": 0.01, "tc_min": 10.0}])
    es.loc[es["gid"] == 1, "len_m"] = 350.0
    prep = nf.prepare(es, cache=False)
    r = nf.route(prep, np.array([[57.7, 21.18, 2.13]] * 3), np.array([15.0, 60.0, 1440.0]))
    bw = nf.backwater(prep, r, max_m=300.0, ratio_thr=0.8)
    g = list(prep["gid"])
    assert r["spilling"][g.index(2)] and not r["spilling"][g.index(1)] and not r["spilling"][g.index(9)]
    assert bw["flag"][g.index(1)] and not bw["flag"][g.index(2)] and not bw["flag"][g.index(9)]
    assert bw["dist_m"][g.index(1)] == 0.0 and bw["dist_m"][g.index(9)] == 350.0 and bw["edges"] == 1
    res = nf.run(CFG, prep, "10")
    assert res["backwater"]["edges"] == 1 and "edge_backwater" in res and "edge_backwater" not in nf.public(res)
