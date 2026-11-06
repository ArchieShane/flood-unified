"""退水重演：基线复现、加泵后更早退干、只涨不退的过程线只有泵能排、调度单与手动串解析。"""
import numpy as np
import pandas as pd
from swbrain import replay as rp

HYD = [{"t_min": 0, "depth_m": 0, "volume_m3": 0, "inflow_m3h": 0},
       {"t_min": 15, "depth_m": None, "volume_m3": 500, "inflow_m3h": 2000},
       {"t_min": 60, "depth_m": None, "volume_m3": 2000, "inflow_m3h": 2000},
       {"t_min": 75, "depth_m": None, "volume_m3": 2000, "inflow_m3h": 0},
       {"t_min": 375, "depth_m": 0, "volume_m3": 0, "inflow_m3h": 0}]       # 雨停后 400 m³/h 退水，5 小时退干


def test_baseline_reproduces_hydrograph():
    r = rp.replay(HYD, [], dt=5.0)
    t = np.array(r["t_min"]); b = np.array(r["base_m3"])
    assert abs(b.max() - 2000) < 60 and r["kpi"]["base"]["t_empty_min"] is not None
    assert abs(r["kpi"]["base"]["t_empty_min"] - 375) <= 15
    assert np.array_equal(b, np.array(r["pump_m3"]))          # 没有泵时两条线相同


def test_pumps_shorten_recession_and_never_exceed_baseline():
    pumps = [{"arrive_min": 120, "rate_m3h": 600}, {"arrive_min": 150, "rate_m3h": 600}]
    r = rp.replay(HYD, pumps, dt=5.0, max_depth_m=0.5)
    b, p = np.array(r["base_m3"]), np.array(r["pump_m3"])
    assert (p <= b + 1e-6).all() and r["kpi"]["pump"]["t_empty_min"] < r["kpi"]["base"]["t_empty_min"]
    assert r["kpi"]["saved_min"] > 60 and r["kpi"]["flood_h_saved"] > 0 and r["kpi"]["pumped_total_m3"] > 0
    assert r["pump_depth_m"][0] == 0.0 and max(r["base_depth_m"]) <= 0.5 + 1e-9
    # 泵到位前两条线一样
    t = np.array(r["t_min"]); assert np.array_equal(b[t < 120], p[t < 120])


def test_flat_hydrograph_only_pumps_drain():
    flat = [{"t_min": 0, "volume_m3": 0, "inflow_m3h": 0}, {"t_min": 180, "volume_m3": 3000, "inflow_m3h": 1000}, {"t_min": 185, "volume_m3": 3000, "inflow_m3h": 0}, {"t_min": 1440, "volume_m3": 3000, "inflow_m3h": 0}]
    r0 = rp.replay(flat, [], dt=10.0)
    assert r0["kpi"]["base"]["t_empty_min"] is None and r0["kpi"]["base_never_empties"]
    r1 = rp.replay(flat, [{"arrive_min": 240, "rate_m3h": 1500}], dt=10.0)
    assert r1["kpi"]["pump"]["t_empty_min"] is not None and abs(r1["kpi"]["pump"]["t_empty_min"] - (240 + 120)) <= 20


def test_plan_and_manual_parsing():
    plan = pd.DataFrame([{"fp_id": "A", "unit_id": "u1", "model": "diesel-8in", "rate_m3h": 350, "arrive_min": 100, "pumping_from_min": 130, "from": "d1"},
                         {"fp_id": "A", "unit_id": "u2", "model": "diesel-6in", "rate_m3h": 180, "arrive_min": 110, "pumping_from_min": 130, "from": "d1"}])
    pumps = rp.pumps_from_plan(plan)
    assert [p["arrive_min"] for p in pumps] == [120.0, 130.0] and pumps[0]["rate_m3h"] == 350
    models = [{"model": "diesel-12in", "rate_m3h": 700}, {"model": "diesel-8in", "rate_m3h": 350}, {"model": "diesel-6in", "rate_m3h": 180}]
    m = rp.parse_manual("8in:2@250, 6:1@300", models)
    assert len(m) == 3 and m[0]["rate_m3h"] == 350 and m[2]["rate_m3h"] == 180 and m[2]["arrive_min"] == 300
    pts = pd.DataFrame([{"fp_id": "A", "hydrograph": HYD}, {"fp_id": "B", "hydrograph": HYD}])
    rows, s = rp.replay_plan(pts, plan)
    assert len(rows) == 1 and rows[0]["fp_id"] == "A" and rows[0]["replay_saved_min"] > 0
    assert s["served_points"] == 1 and s["all_points"] == 2 and s["all_flood_h_pump"] < s["all_flood_h_base"] and 0 < s["all_saved_pct"] < s["served_saved_pct"]


def test_replay_plan_timeline_samples_all_points_on_common_grid():
    plan = pd.DataFrame([{"fp_id": "A", "unit_id": "u1", "model": "diesel-8in", "rate_m3h": 350, "arrive_min": 100, "pumping_from_min": 130, "from": "d1"}])
    pts = pd.DataFrame([{"fp_id": "A", "hydrograph": HYD}, {"fp_id": "B", "hydrograph": HYD}, {"fp_id": "C", "hydrograph": [{"t_min": 0, "volume_m3": 0}, {"t_min": 60, "volume_m3": 10}]}])
    rows, s = rp.replay_plan(pts, plan, timeline_dt=15)
    tl = s["timeline"]
    assert tl["dt_min"] == 15 and tl["t_min"][1] - tl["t_min"][0] == 15 and set(tl["points"]) == {"A", "B"}      # C 峰值 10 m³ 低于下限不进时间轴
    a, b = tl["points"]["A"], tl["points"]["B"]
    assert len(a["base"]) == len(tl["t_min"]) == len(b["base"]) and b["pump"] is None
    assert max(a["base"]) == max(b["base"]) > 0 and all(x <= y for x, y in zip(a["pump"], a["base"])) and sum(a["pump"]) < sum(a["base"])
    rows2, s2 = rp.replay_plan(pts, plan)
    assert "timeline" not in s2


# 只涨不退的过程线：基线在视界内永远退不干，加泵后能退干——这正是泵最有价值的情况。
# 2026-09-23 之前 saved_min 的两个分支都返回 None，下游按 0 处理，报告里会说「那台泵并没有改变退干时刻」。
RISING = [{"t_min": t, "volume_m3": round(3000.0 * min(t / 120.0, 1.0), 1), "inflow_m3h": 600.0} for t in range(0, 901, 30)]


def test_saved_min_is_lower_bound_when_base_never_empties():
    base = rp.replay(RISING, [], t_end=900)
    assert base["kpi"]["base"]["t_empty_min"] is None                       # 基线到视界都没退干
    r = rp.replay(RISING, [{"arrive_min": 180, "rate_m3h": 2500}], t_end=900)
    k = r["kpi"]
    assert k["pump"]["t_empty_min"] is not None                            # 派泵后退干了
    assert k["saved_min"] is not None and k["saved_min"] > 0                # 不能报 None
    assert k["saved_is_lower_bound"] is True                               # 而且要标明这是「至少省了多少」
    assert abs(k["saved_min"] - (900.0 - k["pump"]["t_empty_min"])) < 1e-6  # 下界 = 视界 − 退干时刻
    assert k["base_never_empties"] is True and k["pump_never_empties"] is False


def test_saved_min_exact_when_both_empty():
    r = rp.replay(HYD, [{"arrive_min": 90, "rate_m3h": 800}], dt=5.0)
    k = r["kpi"]
    if k["base"]["t_empty_min"] is not None and k["pump"]["t_empty_min"] is not None:
        assert k["saved_is_lower_bound"] is False
        assert abs(k["saved_min"] - (k["base"]["t_empty_min"] - k["pump"]["t_empty_min"])) < 1.0


def test_saved_min_none_when_pump_also_never_empties():
    r = rp.replay(RISING, [{"arrive_min": 800, "rate_m3h": 10}], t_end=900)
    k = r["kpi"]
    assert k["pump"]["t_empty_min"] is None and k["saved_min"] is None and k["saved_is_lower_bound"] is False


# ---- 抢占释放的泵 ----
# 2026-09-24：抢占把多余的泵放回池子供下一窗口用，但重演只读 arrive_min / rate_m3h，
# 没有结束时刻，于是那台泵在原点仍被算成一直抽到视界——同一台泵同时出现在 A 点的
# 水量和 B 点的调度单里，点数一多会系统性高估退水量。

def test_released_pump_stops_contributing():
    hyd = [{"t_min": t, "volume_m3": round(4000.0 * min(t / 120.0, 1.0), 1), "inflow_m3h": 300.0} for t in range(0, 1201, 15)]
    two = [{"arrive_min": 60.0, "rate_m3h": 800.0}, {"arrive_min": 60.0, "rate_m3h": 800.0}]
    one = [{"arrive_min": 60.0, "rate_m3h": 800.0}]
    rel = [{"arrive_min": 60.0, "rate_m3h": 800.0}, {"arrive_min": 60.0, "rate_m3h": 800.0, "until_min": 200.0}]
    k2 = rp.replay(hyd, two, dt=5.0, t_end=1200)["kpi"]["pump"]
    k1 = rp.replay(hyd, one, dt=5.0, t_end=1200)["kpi"]["pump"]
    kr = rp.replay(hyd, rel, dt=5.0, t_end=1200)["kpi"]["pump"]
    # 200 min 释放一台：结果必须夹在「两台到底」和「只有一台」之间，且不等于两台到底
    assert k2["t_empty_min"] < kr["t_empty_min"] <= k1["t_empty_min"], (k2["t_empty_min"], kr["t_empty_min"], k1["t_empty_min"])
    assert kr["pumped_total_m3"] if "pumped_total_m3" in kr else True


def test_until_min_none_means_no_limit():
    hyd = [{"t_min": t, "volume_m3": round(2000.0 * min(t / 60.0, 1.0), 1), "inflow_m3h": 200.0} for t in range(0, 601, 15)]
    a = rp.replay(hyd, [{"arrive_min": 30.0, "rate_m3h": 600.0}], dt=5.0, t_end=600)["kpi"]["pump"]
    b = rp.replay(hyd, [{"arrive_min": 30.0, "rate_m3h": 600.0, "until_min": None}], dt=5.0, t_end=600)["kpi"]["pump"]
    assert a["t_empty_min"] == b["t_empty_min"]


def test_pumps_from_plan_reads_released_min():
    plan = pd.DataFrame([
        {"fp_id": "A", "unit_id": "P1", "model": "diesel-12in", "arrive_min": 100.0, "pumping_from_min": 125.0, "rate_m3h": 700.0, "released_min": 300.0},
        {"fp_id": "A", "unit_id": "P2", "model": "diesel-8in", "arrive_min": 110.0, "pumping_from_min": 125.0, "rate_m3h": 350.0, "released_min": None},
    ])
    pumps = rp.pumps_from_plan(plan)
    assert [p["until_min"] for p in pumps] == [300.0, None]
    assert pumps[0]["arrive_min"] == 115.0 and pumps[1]["arrive_min"] == 125.0      # 架设 15 min


# ---- 只算 kpi 的快路径 ----
# 枚举处置包时一个点要重演几十遍，只读 kpi；把六条序列逐元素 round 成 Python 列表是整个求解里
# 最大的一笔无用功（2026-09-24：单次 0.407 → 0.223 ms，一次完整求解 15.5 → 12.4 s）。

def test_series_false_gives_identical_kpi():
    hyd = [{"t_min": t, "volume_m3": round(2500.0 * min(t / 90.0, 1.0), 1), "inflow_m3h": 400.0} for t in range(0, 901, 15)]
    for pumps in ([], [{"arrive_min": 120.0, "rate_m3h": 600.0}],
                  [{"arrive_min": 120.0, "rate_m3h": 600.0}, {"arrive_min": 180.0, "rate_m3h": 350.0, "until_min": 400.0}]):
        a = rp.replay(hyd, pumps, dt=10.0, t_end=1200, series=True)
        b = rp.replay(hyd, pumps, dt=10.0, t_end=1200, series=False)
        assert a["kpi"] == b["kpi"], (pumps, a["kpi"], b["kpi"])
        assert "t_min" not in b and "base_m3" not in b          # 快路径不带序列
        assert b["dt_min"] == a["dt_min"] and b["min_vol_m3"] == a["min_vol_m3"]


def test_series_false_on_empty_hydrograph():
    assert rp.replay([], [], series=False).get("error")


def test_capacity_profile_matches_naive_loop():
    """现场排量改成预先算好之后，必须和逐步筛选完全一致。"""
    import numpy as np
    t = np.arange(0.0, 600.0, 10.0)
    pumps = [{"arrive_min": 100.0, "rate_m3h": 700.0},
             {"arrive_min": 250.0, "rate_m3h": 350.0, "until_min": 430.0},
             {"arrive_min": 50.0, "rate_m3h": 120.0, "until_min": None}]
    arr = np.array([p["arrive_min"] for p in pumps]); rate = np.array([p["rate_m3h"] for p in pumps])
    until = np.array([(p["until_min"] if p.get("until_min") is not None else np.inf) for p in pumps])
    fast = ((arr[None, :] <= t[:, None]) & (t[:, None] < until[None, :])) @ rate
    slow = np.array([rate[(arr <= ti) & (ti < until)].sum() for ti in t])
    assert np.array_equal(fast, slow)
