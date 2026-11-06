"""docs/优化模型说明.md 附录 A 的形式化表述，必须和送进 HiGHS 的东西逐元素一致。

写文档最怕的是它和代码各走各的。这里拦下 `scipy.optimize.milp` 真正收到的
(c, A, lo, hi, bounds, integrality)，再按附录 A.4 的公式从参数独立重建一遍，逐元素比。
公式改了、代码改了、只改一边，这条就红。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pytest

from swbrain import dispatch2 as D2, dispatch as D1, resources as RS
from tests.test_dispatch2 import _cfg, _ctx

EPS = 1e-3          # 附录 A.4 里的平局打破项系数
BUDGET = 50000.0


@pytest.fixture
def case(monkeypatch):
    """跑一次 solve_window，同时拦下送进求解器的矩阵。"""
    captured = {}
    real = D2.milp

    def spy(c, constraints=None, integrality=None, bounds=None, options=None):
        if "c" not in captured:                      # 只留第一次（窗口 1）
            captured["c"] = np.asarray(c, dtype=float).copy()
            captured["A"] = constraints.A.toarray() if hasattr(constraints.A, "toarray") else np.asarray(constraints.A, dtype=float)
            captured["lo"] = np.asarray(constraints.lb, dtype=float).copy()
            captured["hi"] = np.asarray(constraints.ub, dtype=float).copy()
            captured["ub"] = np.asarray(bounds.ub, dtype=float).copy()
            captured["integrality"] = np.asarray(integrality).copy()
        return real(c, constraints=constraints, integrality=integrality, bounds=bounds, options=options)

    monkeypatch.setattr(D2, "milp", spy)

    cfg = _cfg()
    ctx = _ctx(cfg, n_units12=3, n_units8=3, tankers=4, crews=40)
    ctx = {**ctx, "cfg": cfg}
    P = D2.params(cfg)
    res = RS.build(ctx["fleet"], None, cfg)
    V = float(P["value_aed_per_priority_hour"]["balanced"])
    t = 60.0
    pts = D1.feasibility(ctx["pts0"], ctx["cs"], ctx["fleet"], cfg, t)
    pts["level"], pts["level_priority"] = D1.emergency_level(pts)
    cand = pts[(pts["volume_now_m3"] >= 50) & (pts["dispatchable"] | ((~pts["discharge_ok"].astype(bool)) & pts["pump_worthy"].astype(bool)))]
    classes = D2.build_classes(ctx["units"].assign(at_pt=-1, busy_until=np.nan), ctx["depots"], res, ctx, t)
    pkgs = {int(p): D2.point_packages(int(p), cand.loc[p], ctx["fleet"]["models"], classes, res, V, cfg, P, t,
                                      not bool(cand.loc[p, "discharge_ok"]), alt=None) for p in cand.index}
    pkgs = {p: g for p, g in pkgs.items() if g}
    depot_state = {did: {"crews_avail": ds["crews"]["on_duty"] + ds["crews"]["callable"], "vehicles": dict(ds["vehicles"]),
                         "hose_m": float(ds["materials"]["hose_m"]), "fuel_l": float(ds["materials"]["fuel_l"])}
                   for did, ds in res["depots"].items()}
    D2.solve_window(cand, classes, pkgs, res, V, float(P["delay_weight"]), P, depot_state, BUDGET, None,
                    zones={}, pooled=True, work_order_aed=0.0)
    assert captured, "没拦到送进求解器的矩阵，夹具八成没解出东西"
    return {"cfg": cfg, "ctx": ctx, "P": P, "res": res, "V": V, "t": t,
            "cand": cand, "classes": classes, "pkgs": pkgs, "depot_state": depot_state, "got": captured}


def _rebuild(case):
    """按附录 A.4 从参数独立重建 (c, A, lo, hi, ub)。"""
    P, res, V = case["P"], case["res"], case["V"]
    cand, classes, pkgs, depot_state = case["cand"], case["classes"], case["pkgs"], case["depot_state"]
    alpha = float(P["delay_weight"]); beta = float(P.get("arrival_hours_weight", 1.0))
    rates, cons = res["rates"], res["consumption"]
    mtl = P["max_travel_min_by_level"]

    plist = [int(p) for p in cand.index if pkgs.get(int(p))]
    yvars = [(p, g["gid"]) for p in plist for g in pkgs[p]]
    nvars = []
    for c in classes:
        for p in plist:
            tv = c["travel"][p]
            lim = float(mtl.get(str(cand.loc[p, "level"]), mtl.get("I", 90)))
            if np.isfinite(tv) and tv <= lim and any(c["model"] in g["need"] for g in pkgs[p]):
                nvars.append((c["cid"], p))
    ny, nn = len(yvars), len(nvars)
    yix = {k: i for i, k in enumerate(yvars)}
    nix = {k: ny + i for i, k in enumerate(nvars)}
    cls = {c["cid"]: c for c in classes}
    w = cand["priority"].astype(float).to_dict()

    tau_star = {}                                             # τ*[p,m]
    for (cid, p) in nvars:
        c0 = cls[cid]; k = (p, c0["model"]); v0 = float(c0["travel"][p]) + float(c0["avail_min"])
        tau_star[k] = min(tau_star.get(k, np.inf), v0)

    c_vec = np.zeros(ny + nn); money = np.zeros(ny + nn)
    for (p, gid), i in yix.items():                           # 目标：y 部分
        g = pkgs[p][gid]; wp = float(w[p])
        c_vec[i] = wp * g["dH"] + alpha * wp * g["delay_delta_min"] / 60.0 + g["cost_aed"] / V
        money[i] = float(g["cost_aed"])
    for (cid, p), i in nix.items():                           # 目标：n 部分
        c = cls[cid]; tv = float(c["travel"][p]); km = tv / 60.0 * rates["vehicle_speed_kmh"]
        tcost = 2 * km * float(rates["vehicle_aed_km"].get(c["vehicle_class"], 3.0)) + tv / 60.0 * max(c["crew"], 1) * rates["crew_aed_h"]
        nbar = max(1, sum(g["n_units"] for g in pkgs[p]) / max(1, len(pkgs[p])))
        e = max(0.0, (tv + float(c["avail_min"])) - tau_star.get((p, c["model"]), tv + float(c["avail_min"])))
        c_vec[i] = tcost / V + EPS * tv + beta * float(w[p]) * e / 60.0 / nbar
        money[i] = tcost

    rows = []

    def row(pairs, lo_, hi_):
        v = np.zeros(ny + nn)
        for j, coef in pairs:
            v[j] += coef
        rows.append((v, lo_, hi_))

    counts = {}
    for p in plist:                                                                   # (1)
        row([(yix[(p, g["gid"])], 1.0) for g in pkgs[p]], -np.inf, 1.0)
    counts["1 每点最多一个包"] = len(rows)
    for p in plist:                                                                   # (2)
        for m in sorted({m for g in pkgs[p] for m in g["need"]}):
            pairs = [(nix[(c["cid"], p)], 1.0) for c in classes if c["model"] == m and (c["cid"], p) in nix]
            pairs += [(yix[(p, g["gid"])], -float(g["need"].get(m, 0))) for g in pkgs[p] if g["need"].get(m)]
            row(pairs, 0.0, 0.0)
    counts["2 型号平衡"] = len(rows) - sum(counts.values())
    for c in classes:                                                                 # (3)
        pairs = [(nix[(c["cid"], p)], 1.0) for p in plist if (c["cid"], p) in nix]
        if pairs:
            row(pairs, -np.inf, float(c["count"]))
    counts["3 类容量"] = len(rows) - sum(counts.values())
    for did, ds in depot_state.items():                                               # (4)(5)(6)
        pump_cls = [c for c in classes if c["kind"] == "pump" and c["depot_id"] == did]
        if not pump_cls:
            continue
        pairs = [(nix[(c["cid"], p)], float(c["crew"])) for c in pump_cls for p in plist if (c["cid"], p) in nix]
        if pairs:
            row(pairs, -np.inf, float(ds["crews_avail"]))
        for vc in ("heavy", "light"):
            pairs = [(nix[(c["cid"], p)], 1.0 / float(cons["carry"].get(c["model"], 1))) for c in pump_cls if c["vehicle_class"] == vc for p in plist if (c["cid"], p) in nix]
            if pairs:
                row(pairs, -np.inf, float(ds["vehicles"].get(vc, 0)) * float(cons["trips_per_window"]))
        pairs = [(nix[(c["cid"], p)], float(cons["hose_base_m"]) + float(cand.loc[p, "relay_stages"] or 0) * float(cons["hose_per_stage_m"])) for c in pump_cls for p in plist if (c["cid"], p) in nix]
        if pairs:
            row(pairs, -np.inf, float(ds["hose_m"]))
    counts["456 驻地班组/车次/软管"] = len(rows) - sum(counts.values())
    pairs = [(yix[(p, g["gid"])], float(g["fuel_l"])) for p in plist for g in pkgs[p] if g["fuel_l"]]   # (7)
    if pairs:
        row(pairs, -np.inf, sum(ds["fuel_l"] for ds in depot_state.values()))
    counts["7 全网燃油"] = len(rows) - sum(counts.values())
    pairs = [(i, float(money[i])) for i in range(ny + nn) if money[i]]                                   # (8)
    row(pairs, -np.inf, BUDGET)
    counts["8 预算"] = len(rows) - sum(counts.values())

    ub = np.ones(ny + nn)
    for (cid, p), i in nix.items():
        ub[i] = float(cls[cid]["count"])
    return {"c": c_vec, "A": np.array([r[0] for r in rows]), "lo": np.array([r[1] for r in rows]),
            "hi": np.array([r[2] for r in rows]), "ub": ub, "ny": ny, "nn": nn, "counts": counts}


def test_formulation_is_not_trivial(case):
    """判据别恒真：这个算例得真有变量和约束。"""
    got = case["got"]
    assert len(got["c"]) >= 15 and got["A"].shape[0] >= 10


def test_objective_vector_matches(case):
    mine = _rebuild(case); got = case["got"]
    assert len(mine["c"]) == len(got["c"]), f"变量数：附录 {len(mine['c'])}，代码 {len(got['c'])}"
    assert np.allclose(mine["c"], got["c"], atol=1e-9), f"目标系数最大差 {np.abs(mine['c'] - got['c']).max():.3g}"


def test_constraint_matrix_matches(case):
    mine = _rebuild(case); got = case["got"]
    assert mine["A"].shape == got["A"].shape, f"约束矩阵形状：附录 {mine['A'].shape}，代码 {got['A'].shape}"
    assert np.allclose(mine["A"], got["A"], atol=1e-9)


def test_bounds_match(case):
    mine = _rebuild(case); got = case["got"]
    big = 1e30
    assert np.allclose(np.nan_to_num(mine["lo"], neginf=-big), np.nan_to_num(got["lo"], neginf=-big), atol=1e-9)
    assert np.allclose(np.nan_to_num(mine["hi"], posinf=big), np.nan_to_num(got["hi"], posinf=big), atol=1e-9)
    assert np.allclose(mine["ub"], got["ub"], atol=1e-9)


def test_all_variables_are_integer(case):
    assert np.all(case["got"]["integrality"] == 1), "附录 A.3 说全部变量整数"


def test_constraint_row_counts_match_the_appendix(case):
    """八类约束的行数加起来就是总行数——附录 A.4 没有漏掉哪一类。"""
    mine = _rebuild(case)
    assert sum(mine["counts"].values()) == mine["A"].shape[0]
    assert mine["counts"]["1 每点最多一个包"] == len([p for p in case["pkgs"]])
    assert mine["counts"]["7 全网燃油"] == 1 and mine["counts"]["8 预算"] == 1


def test_package_coefficients_have_the_declared_sign(case):
    """附录 A.5：两个系数都是增量，都 ≤ 0。"""
    for p, gs in case["pkgs"].items():
        for g in gs:
            assert g["dH"] <= 1e-9, (p, g["label_zh"], g["dH"])
            assert g["delay_delta_min"] <= 1e-6, (p, g["label_zh"], g["delay_delta_min"])
