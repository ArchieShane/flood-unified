"""第二道防线（滞蓄塘）：选址、定容、进水能力与选方案。

这个模块的中心论点是**进水能力才是约束，不是库容**：

    有效库容 = min( 库容 , 进水能力 × 雨峰窗口时长 )

所以下面第一组判据就是「同样的库容，进水能力小的塘救不了多少」。剩下几组守的是开发过程中
真踩到过的三个坑：

1. 容积档按固定挖深列（0/1/2/3 m）——天然洼地动辄两万平米，最浅的 1 m 档就是两万多方库容，
   而需求才两千方，挖十倍的坑存一成的水。改成按需求定档。
2. 天然洼地的库容里，已经被它自己那个积水点占掉的部分不能再算成新增库容，否则同一份水
   算两遍。
3. 有过一个零造价、不限流量的「塘边点自己淌进来」档，模型一律选「就地挖坑」——可 374 个
   积水点的 land_use 全是 road/tunnel，车行道里不能挖塘。这一档已经拿掉。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import pytest

from swbrain import ponds


def _cfg(**over):
    return {"ponds": dict(over)}


def _hyd(peak_m3h=3000.0, rise_min=60.0, recede=True, horizon=1440.0):
    """一条过程线：rise_min 内涨到峰值，然后（可选）线性退水。"""
    v = peak_m3h * rise_min / 60.0
    out = [{"t_min": 0.0, "volume_m3": 0.0, "inflow_m3h": 0.0},
           {"t_min": rise_min, "volume_m3": v, "inflow_m3h": peak_m3h}]
    if recede:
        out.append({"t_min": rise_min + 240.0, "volume_m3": 0.0, "inflow_m3h": 0.0})
    out.append({"t_min": horizon, "volume_m3": 0.0 if recede else v, "inflow_m3h": 0.0})
    return out


def _pts(rows):
    out = []
    for i, r in enumerate(rows):
        out.append({"fp_id": r.get("fp_id", f"P{i}"), "lon": r.get("lon", 54.5), "lat": r.get("lat", 24.4),
                    "dtm_z": r.get("dtm_z", 10.0), "priority": r.get("priority", 1.0),
                    "hydrograph": r.get("hydrograph", _hyd())})
    return pd.DataFrame(out)


def _prep(pts, dists=None, cfg=None):
    served = [(j, (dists[j] if dists else 100.0)) for j in range(len(pts))]
    return ponds.prepare(pts, served, cfg or _cfg())


# ------------------------------------------------------------ 中心论点：进水能力
def test_intake_capacity_is_the_binding_constraint():
    """同样 100,000 m³ 的库容，进水能力从 500 提到 20,000 m³/h，拦下的水必须显著变多。

    这正是「挖了塘就等于防住了」这个直觉错在哪：塘再大，水进不去也没用。
    """
    prep = _prep(_pts([{"hydrograph": _hyd(peak_m3h=20000.0, rise_min=60.0)}]))
    got = {q: ponds.route(prep, 100000.0, 5000.0, q, _cfg())["captured_in_peak_m3"]
           for q in (500.0, 2000.0, 20000.0)}
    assert got[500.0] < got[2000.0] < got[20000.0], f"进水能力没起作用：{got}"
    assert got[20000.0] > 8 * got[500.0], f"进水能力差 40 倍，拦截量只差 {got[20000.0] / got[500.0]:.1f} 倍"


def test_capture_never_exceeds_intake_capacity():
    """拦截量不能超过「进水能力 × 时长」——这条破了说明演算里漏了进水约束。"""
    prep = _prep(_pts([{"hydrograph": _hyd(peak_m3h=50000.0, rise_min=30.0)}]))
    q = 1000.0
    r = ponds.route(prep, 1e9, 5000.0, q, _cfg(peak_window_min=120.0))
    assert r["captured_in_peak_m3"] <= q * 2.0 + 1e-6, \
        f"120 min 最多拦 {q * 2:,.0f} m³，却拦了 {r['captured_in_peak_m3']:,.0f}"
    assert r["peak_intake_m3h"] <= q + 1e-6


def test_capture_never_exceeds_storage():
    """进水能力无限时，拦截总量受库容与下渗约束，不能凭空多出来。"""
    vol = 3000.0
    prep = _prep(_pts([{"hydrograph": _hyd(peak_m3h=50000.0, rise_min=30.0)}]))
    r = ponds.route(prep, vol, 0.0, 1e9, _cfg(infiltration_mm_h=0.0, release_m3h=0.0))
    assert r["captured_total_m3"] <= vol + 1e-6, f"库容 {vol}，却收了 {r['captured_total_m3']}"
    assert r["peak_storage_m3"] <= vol + 1e-6


def test_infiltration_frees_storage_for_more_capture():
    """能渗的塘腾出库容，总拦截量比不渗的大。"""
    pts = _pts([{"hydrograph": _hyd(peak_m3h=20000.0, rise_min=60.0)}])
    dry = ponds.route(_prep(pts), 5000.0, 20000.0, 1e9, _cfg(infiltration_mm_h=0.0))
    wet = ponds.route(_prep(pts), 5000.0, 20000.0, 1e9, _cfg(infiltration_mm_h=50.0))
    assert wet["captured_total_m3"] > dry["captured_total_m3"]


# ------------------------------------------------------------ 容积档按需求定
def test_volume_tiers_track_demand_not_a_fixed_dig_depth():
    """需求 2,000 m³ 的点，不该给出两万方的方案——这是按固定挖深列档时的真实 bug。

    当时 NAT-AAM-176 挖了 22,036 m³ 存 2,031 m³，利用率 15%，造价 991,611 AED；
    改成按需求定档之后同样的效果只要 3,324 m³ / 149,580 AED。
    """
    site = pd.Series({"area_m2": 22000.0, "natural_m3": 0.0})
    opts = ponds.volume_options(site, 0.0, 2000.0, _cfg())
    assert opts, "一个档都没给出来"
    assert min(o["volume_m3"] for o in opts) <= 0.6 * 2000.0, \
        f"最小档 {min(o['volume_m3'] for o in opts):,.0f} m³，需求才 2,000"
    assert max(o["volume_m3"] for o in opts) <= 2.0 * 2000.0, \
        f"最大档 {max(o['volume_m3'] for o in opts):,.0f} m³，远超需求"


def test_volume_tiers_scale_with_demand():
    site = pd.Series({"area_m2": 500000.0, "natural_m3": 0.0})
    small = max(o["volume_m3"] for o in ponds.volume_options(site, 0.0, 1000.0, _cfg()))
    big = max(o["volume_m3"] for o in ponds.volume_options(site, 0.0, 100000.0, _cfg()))
    assert big > 50 * small, f"需求差 100 倍，档位只差 {big / small:.1f} 倍"


def test_volume_is_capped_by_available_land():
    """洼地范围是可用地的代理：地放不下就封顶，并且标出来。"""
    site = pd.Series({"area_m2": 400.0, "natural_m3": 0.0})       # 400 m² × 2.5 m = 1,000 m³ 顶天
    opts = ponds.volume_options(site, 0.0, 100000.0, _cfg())
    assert opts
    assert max(o["volume_m3"] for o in opts) <= 1000.0 + 1e-6
    assert any(o["capped"] for o in opts), "受占地上限却没标 capped"


def test_free_natural_storage_is_not_charged_for():
    """已有的天然库容是白给的，不该收开挖费。"""
    site = pd.Series({"area_m2": 10000.0, "natural_m3": 5000.0})
    opts = ponds.volume_options(site, 5000.0, 5000.0, _cfg())
    zero = [o for o in opts if o["excavate_m3"] == 0]
    assert zero and zero[0]["cost_aed"] == 0.0
    assert zero[0]["volume_m3"] == pytest.approx(5000.0)


# ------------------------------------------------------------ 不能把同一份水算两遍
def test_local_ponding_is_deducted_from_natural_storage():
    """塘就坐在洼地上时，洼地库容里已经被它自己的积水占掉的部分要扣掉。

    不扣的话，「这里天然能存 5,000 方」和「这里正淹着 3,000 方」会被当成两笔独立的库容。
    """
    pts = _pts([{"hydrograph": _hyd(peak_m3h=3000.0, rise_min=60.0, recede=False)}])
    near = ponds.prepare(pts, [(0, 10.0)], _cfg())          # 就在塘位上
    far = ponds.prepare(pts, [(0, 900.0)], _cfg())          # 一公里外，与本地库容无关
    assert near["peak_local_m3"] > 0
    assert far["peak_local_m3"] == 0.0


# ------------------------------------------------------------ 归属：水往低处流
def test_point_below_the_pond_bottom_is_not_assigned():
    """点比塘底还低，水流不进去，不能归给它。"""
    sites = pd.DataFrame([{"site_id": "S", "lon": 54.5, "lat": 24.4, "low_z": 12.0,
                           "natural_m3": 1000.0, "area_m2": 1000.0, "origin": "terrain"}])
    high = _pts([{"lon": 54.502, "lat": 24.4, "dtm_z": 15.0}])       # 高于塘底 3 m
    low = _pts([{"lon": 54.502, "lat": 24.4, "dtm_z": 11.0}])        # 低于塘底 1 m
    assert ponds.assign_points(sites, high, _cfg()), "高处的点该能自流进塘"
    assert not ponds.assign_points(sites, low, _cfg()), "低处的点不该被归进来"


def test_lowest_beats_nearest_for_consolidation():
    """按最低点归属能把一个汇水区的几个点并到同一个塘；按最近归属则各归各的小洼地。

    这是「汇水区尺度的第二道防线」与「逐点挖坑」的分水岭。实测全网：按最近平均一个塘只
    服务 1.27 个点。
    """
    sites = pd.DataFrame([
        {"site_id": "SINK", "lon": 54.500, "lat": 24.400, "low_z": 2.0, "natural_m3": 50000.0,
         "area_m2": 20000.0, "origin": "terrain"},
        {"site_id": "TINY-A", "lon": 54.503, "lat": 24.400, "low_z": 9.0, "natural_m3": 200.0,
         "area_m2": 400.0, "origin": "terrain"},
        {"site_id": "TINY-B", "lon": 54.506, "lat": 24.400, "low_z": 9.0, "natural_m3": 200.0,
         "area_m2": 400.0, "origin": "terrain"},
    ])
    pts = _pts([{"lon": 54.5031, "lat": 24.400, "dtm_z": 10.0},
                {"lon": 54.5061, "lat": 24.400, "dtm_z": 10.0}])
    low = ponds.assign_points(sites, pts, _cfg(assign_by="lowest"))
    near = ponds.assign_points(sites, pts, _cfg(assign_by="nearest"))
    assert len(low) == 1 and len(next(iter(low.values()))) == 2, f"最低点归属没并起来：{low}"
    assert len(near) == 2, f"最近归属该各归各的：{near}"


def test_client_ponds_win_over_terrain_sinks():
    """客户 1D 模型里已经规划的塘是真设施，优先用它，别另起炉灶。"""
    sites = pd.DataFrame([
        {"site_id": "NAT", "lon": 54.500, "lat": 24.400, "low_z": 1.0, "natural_m3": 9e5,
         "area_m2": 20000.0, "origin": "terrain"},
        {"site_id": "POND", "lon": 54.502, "lat": 24.400, "low_z": np.nan, "natural_m3": 5000.0,
         "area_m2": np.nan, "origin": "client_model"},
    ])
    pts = _pts([{"lon": 54.5021, "lat": 24.400, "dtm_z": 10.0}])
    a = ponds.assign_points(sites, pts, _cfg(assign_by="lowest"))
    assert list(a.keys()) == [1], f"没优先用客户的塘：{a}"


# ------------------------------------------------------------ 进水方式与造价
def test_conveyance_capacity_matches_manning():
    from swbrain.hydraulics import manning_full_flow
    opts = ponds.conveyance_options(np.array([100.0]), _cfg())
    dn1500 = [o for o in opts if o.get("dn_mm") == 1500][0]
    want = manning_full_flow(1.5, 0.002, 0.013) * 3600.0
    assert dn1500["capacity_m3h"] == pytest.approx(want, rel=1e-3)
    assert dn1500["capacity_m3h"] > 10000, "DN1500 满管流应该上万 m³/h，一台 12 寸泵才 700"


def test_bigger_pipe_carries_more_than_pumps():
    """量级判据：一根 DN1500 重力管抵十几台 12 寸泵。这是「进水必须靠重力」的依据。"""
    opts = ponds.conveyance_options(np.array([100.0]), _cfg())
    pipe = max(o["capacity_m3h"] for o in opts if o["kind"] == "pipe")
    pump1 = ponds.params(_cfg())["pump_rate_m3h"]
    assert pipe / pump1 > 10, f"DN1500 只抵 {pipe / pump1:.1f} 台泵"


def test_conveyance_cost_scales_with_distance():
    """点离塘越远管越长、越贵。早先版本写死 300 m，远近一个价。"""
    near = ponds.conveyance_options(np.array([100.0]), _cfg())
    far = ponds.conveyance_options(np.array([2000.0]), _cfg())
    pn = [o for o in near if o.get("dn_mm") == 900][0]
    pf = [o for o in far if o.get("dn_mm") == 900][0]
    assert pf["cost_aed"] == pytest.approx(20 * pn["cost_aed"], rel=1e-6), \
        f"距离差 20 倍，造价 {pn['cost_aed']:,.0f} → {pf['cost_aed']:,.0f}"


def test_short_distance_has_a_floor_length():
    """再近的点也得有一段收水管，不能算成零造价。"""
    opts = ponds.conveyance_options(np.array([1.0]), _cfg())
    assert all(o["cost_aed"] > 0 for o in opts)
    assert all(o["len_m"] >= 50.0 for o in opts)


def test_no_free_surface_inflow_option():
    """不存在「零造价、不限流量」的进水档——374 个点全是 road/tunnel，车行道里不能挖塘。"""
    opts = ponds.conveyance_options(np.array([100.0]), _cfg())
    assert opts, "一个进水方案都没有"
    assert not [o for o in opts if o["capacity_m3h"] > 0 and o["cost_aed"] <= 0], \
        "又出现了不要钱的进水能力"


# ------------------------------------------------------------ 选方案
def _pkg(gid, w, cost):
    return {"gid": gid, "w_flood_h_saved": w, "cost_aed": cost, "flood_h_saved": w / 170.0,
            "conveyance_kind": "pipe", "volume_m3": 1.0, "excavate_m3": 1.0,
            "captured_in_peak_m3": 1.0, "n_points": 1}


def _fake_pkgs():
    """量级照着真实运行来：V=100，加权收益上万，造价上百万。

    NAT-AAM-140 实测加权收益 40,419、造价/V 10,588——服务点的 priority 是 1,684，
    所以 24 个积水小时能换回四万多加权小时。夹具按 priority≈170 折算 flood_h_saved。
    """
    sites = pd.DataFrame([{"site_id": f"S{i}", "lon": 54.5, "lat": 24.4, "origin": "terrain"}
                          for i in range(3)])
    pkgs = {
        0: [_pkg(0, 15_000.0, 1_000_000.0),      # 划算
            _pkg(1, 18_000.0, 9_000_000.0),      # 多省两成效果，造价翻九倍——不划算
            _pkg(2, 13_000.0, 900_000.0)],       # 也划算，但不如 gid 0；放这个才验得出「每个塘位只能选一个」
        1: [_pkg(0, 14_000.0, 1_000_000.0)],     # 划算
        2: [_pkg(0, 1.0, 8_000_000.0)],          # 八百万只省 1 个加权小时
    }
    return sites, pkgs


def test_at_most_one_package_per_site():
    """S0 底下挂了两个各自都划算的包（gid 0 和 gid 2），约束一松就会两个都选。"""
    sites, pkgs = _fake_pkgs()
    assert sum(1 for g in pkgs[0] if -g["w_flood_h_saved"] + g["cost_aed"] / 100.0 < 0) >= 2, \
        "夹具里 S0 只有一个划算的包，这条判据探测不到约束在不在"
    r = ponds.solve(sites, pkgs, _cfg())
    got = [z["site"]["site_id"] for z in r["chosen"]]
    assert len(got) == len(set(got)), f"同一个塘位选了多个方案：{got}"


def test_budget_is_respected_and_binds():
    sites, pkgs = _fake_pkgs()
    free = ponds.solve(sites, pkgs, _cfg())
    tight = ponds.solve(sites, pkgs, _cfg(), budget_aed=1_500_000.0)
    cost = sum(z["cost_aed"] for z in tight["chosen"])
    assert cost <= 1_500_000.0 + 1e-6, f"超预算：{cost:,.0f}"
    assert len(tight["chosen"]) < len(free["chosen"]), "预算卡下去结果没变，约束没生效"


def test_bad_value_for_money_package_is_rejected():
    """8,000,000 AED 只省 1 个加权积水小时，V=100 时不该选。"""
    sites, pkgs = _fake_pkgs()
    r = ponds.solve(sites, pkgs, _cfg())
    got = [z["site"]["site_id"] for z in r["chosen"]]
    assert got, "一个都没选，夹具的量级不对，这条判据就成了恒真"
    assert "S2" not in got


def test_expensive_variant_of_the_same_site_is_rejected():
    """同一个塘位，多省两成效果但造价翻九倍的那个包不该被选。"""
    sites, pkgs = _fake_pkgs()
    r = ponds.solve(sites, pkgs, _cfg())
    s0 = [z for z in r["chosen"] if z["site"]["site_id"] == "S0"]
    assert s0 and s0[0]["gid"] == 0, f"选了贵的那个包：{[z['gid'] for z in s0]}"


def test_value_parameter_changes_the_tradeoff():
    """V 是唯一区分「不惜代价」和「省钱」的旋钮，和第三道防线同一把尺子。"""
    sites, pkgs = _fake_pkgs()
    cheap = ponds.solve(sites, pkgs, _cfg(value_aed_per_priority_hour=10.0))
    rich = ponds.solve(sites, pkgs, _cfg(value_aed_per_priority_hour=100000.0))
    assert sum(z["cost_aed"] for z in cheap["chosen"]) < sum(z["cost_aed"] for z in rich["chosen"])


# ------------------------------------------------------------ 端到端
def test_end_to_end_plan_on_a_tiny_fixture():
    sites = pd.DataFrame([{"site_id": "SINK", "name": "洼地", "lon": 54.500, "lat": 24.400,
                           "low_z": 2.0, "spill_z": 6.0, "natural_m3": 0.0, "area_m2": 30000.0,
                           "origin": "terrain", "area": "Test", "hotspot_id": "T-1"}])
    pts = _pts([{"fp_id": "A", "lon": 54.5015, "lat": 24.400, "dtm_z": 10.0, "priority": 50.0,
                 "hydrograph": _hyd(peak_m3h=6000.0, rise_min=60.0, recede=False)},
                {"fp_id": "B", "lon": 54.5025, "lat": 24.400, "dtm_z": 10.0, "priority": 30.0,
                 "hydrograph": _hyd(peak_m3h=4000.0, rise_min=60.0, recede=False)}])
    # V 调高：这条验的是「候选→归属→方案包→选方案」这条流水线通不通，不是经济性。
    # 两个点 priority 50/30，省满 24 h 也才 1,920 加权小时，V=100 时换不回几十万的塘。
    cfg = _cfg(value_aed_per_priority_hour=5000.0)
    assign = ponds.assign_points(sites, pts, cfg)
    assert len(assign) == 1 and len(assign[0]) == 2
    pk = ponds.packages(sites, pts, assign, cfg)
    assert pk and pk[0], "一个可行方案包都没有"
    res = ponds.solve(sites, pk, cfg)
    assert len(res["chosen"]) == 1
    z = res["chosen"][0]
    assert z["flood_h_saved"] > 0 and z["cost_aed"] > 0
    assert z["volume_m3"] > 0 and z["capacity_m3h"] > 0
    assert z["captured_in_peak_m3"] <= z["capacity_m3h"] * 2.0 + 1e-6


def test_never_drains_points_are_flagged():
    """世界模型的过程线只涨不退，基线整个视界都淹，这类点要标出来。"""
    drains = _pts([{"hydrograph": _hyd(recede=True)}])
    never = _pts([{"hydrograph": _hyd(recede=False)}])
    assert ponds.prepare(drains, [(0, 100.0)], _cfg())["n_never_drains"] == 0
    assert ponds.prepare(never, [(0, 100.0)], _cfg())["n_never_drains"] == 1

# ------------------------------------------------------------ 别让文档和代码各说各的
def _example_ponds() -> dict:
    import tomllib
    f = Path(__file__).resolve().parents[1] / "config.example.toml"
    if not f.exists():
        pytest.skip("没有 config.example.toml")
    return (tomllib.loads(f.read_text(encoding="utf-8")) or {}).get("ponds") or {}


def test_example_config_keys_all_exist_in_defaults():
    """config.example.toml 里 [ponds] 的每个键都得是 DEFAULTS 认识的。

    写了个 DEFAULTS 里没有的键，params() 会把它原样塞进去、再也没人读——配置看着生效，
    其实没有。这类漂移只有静态比一遍才抓得住。
    """
    ex = _example_ponds()
    assert ex, "config.example.toml 里没有 [ponds]"
    unknown = sorted(set(ex) - set(ponds.DEFAULTS))
    assert not unknown, f"示例配置里有 DEFAULTS 不认识的键：{unknown}"
    cost_unknown = sorted(set(ex.get("cost") or {}) - set(ponds.DEFAULTS["cost"]))
    assert not cost_unknown, f"[ponds.cost] 里有 DEFAULTS 不认识的键：{cost_unknown}"


def test_example_config_values_match_defaults():
    """示例配置的取值要和 DEFAULTS 一致，否则「缺省是多少」有两个互相矛盾的答案。"""
    ex = _example_ponds()
    diff = {k: (v, ponds.DEFAULTS[k]) for k, v in ex.items()
            if k != "cost" and k in ponds.DEFAULTS and v != ponds.DEFAULTS[k]}
    assert not diff, f"示例配置与 DEFAULTS 对不上：{diff}"


def test_ponds_config_does_not_invalidate_the_context_cache():
    """调塘的参数不能把上下文缓存打掉——那东西要 75 秒才能重建。

    [ponds] 只影响第二道防线的规划，上下文（积水点、行程矩阵、管网匹配）跟它无关。
    """
    from swbrain import artifactcache as ac
    assert "ponds" in ac.CONFIG_SKIP
    base = {"dispatch": {"x": 1}}
    a = ac.config_fingerprint(base)
    b = ac.config_fingerprint({**base, "ponds": {"serve_radius_m": 99999.0}})
    assert a == b, "改 [ponds] 换了缓存目录"
    c = ac.config_fingerprint({**base, "dispatch": {"x": 2}})
    assert a != c, "改 [dispatch] 反倒没换缓存目录，说明这条判据本身失灵了"


def test_pond_assumptions_are_registered():
    """造价是占位、没有用地数据、库容来自 DTM 填洼——这三条必须挂在假设总账上。"""
    from swbrain import assumptions as A
    ids = {it["id"]: it for it in A.register({"ponds": ponds.DEFAULTS})}
    for k, lvl in (("pond_unit_cost", "placeholder"), ("pond_land", "missing"), ("pond_storage", "assumed")):
        assert k in ids, f"假设总账里没有 {k}"
        assert ids[k]["level"] == lvl, f"{k} 的等级是 {ids[k]['level']}，应为 {lvl}"
        assert ids[k]["fix"], f"{k} 没写怎么补"
