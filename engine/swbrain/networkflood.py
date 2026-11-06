"""全网溢流路由：把「管段满管能力之外的水」从客户热点扩到每一段管，按 DAG 从上游往下游算峰值流量，管段过不去的部分就地溢出、下游只收能过去的。

为什么要这一步：本系统此前只在客户热点处算缺口体积，DMT 的 1D 模型是每个人孔都算溢流，两边差 1–2 个量级不是精度而是口径。
这里给出与 Parsons / DMT 同口径的「全网溢流量」（m³）：
  1. 节点局部径流 q_loc = K·C·i(d)·A_local（推理公式，K = 2.77e-3，A 用 ha）；A_local 由管段的累计上游面积反推（accumulate 的均分规则）。
  2. 拓扑分层（最长路径深度），逐层向量化：节点流量 = 上游各管「能过去的」之和 + 局部径流，按出边数均分；每段 spill = max(Q − Q_full, 0)，pass = Q − spill。
  3. 每段对每个 IDF 历时 d ≥ tc 算手册 3.6 梯形体积 V = spill·((d − tc) + tc·spill/Q)·60，取最大。
不含真正的水面回推和塘、泵、闸的调蓄——那是 1D 模型的事；这里是规划级筛查的自然延伸。
  4. 下游顶托代理 backwater()：自己不溢、但沿流向 ≤ backwater_m 内有溢流段、且峰值利用率 ≥ backwater_ratio 的管段标「顶托风险」——下游一满，它的水面就抬到地面。

prepare() 只依赖 es 的 gid/u/v/orient/area_m2/runoff_c/q_full/tc_min/len_m/is_active/zone_name，结果缓存到 out/state/netflood_prep.npz（gid 集合变了才重算）。
"""
from __future__ import annotations
import hashlib
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import graph, scenarios, standards

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "out" / "state"
K = 2.77e-3                       # WA-726-1 3.3.2：Q(m³/s) = K·C·i(mm/h)·A(ha)
MIN_VOL_M3 = 50.0                 # 与积水点、重演一致：低于这个量不算溢流


def _key(gids: np.ndarray, qfull: np.ndarray, area: np.ndarray) -> str:
    h = hashlib.sha1(); h.update(np.ascontiguousarray(np.sort(gids)).tobytes()); h.update(np.round(qfull, 6).tobytes()); h.update(np.round(area, 1).tobytes())
    return h.hexdigest()


def prepare(es: pd.DataFrame, cache: bool = True, log=None) -> dict:
    """DAG → 拓扑层、节点局部面积、每段的上下游节点索引。返回 numpy 字典（可 np.savez）。"""
    a = es[es["is_active"].astype(bool) & es["u"].notna() & es["v"].notna()].copy()
    a = a[a["q_full"].notna() & (a["q_full"] > 0)]
    gids = a["gid"].astype(int).values
    key = _key(gids, a["q_full"].values.astype(float), a["area_m2"].fillna(0).values.astype(float))
    cache_f = STATE / "netflood_prep.npz"
    if cache and cache_f.exists():
        z = np.load(cache_f, allow_pickle=False)
        if str(z["key"]) == key:
            return {k: z[k] for k in z.files}
    t0 = time.time()
    g, removed = graph.build_dag(a)
    if removed:
        a = a[~a["gid"].isin(removed)]
    import networkx as nx
    order = list(nx.topological_sort(g))
    level = {}
    for n in order:
        level[n] = max([level[p] + 1 for p in g.predecessors(n)], default=0)
    nodes = np.array(order, dtype=np.int64); nidx = {int(n): i for i, n in enumerate(nodes)}
    u = a["u"].astype(int).map(nidx).values; v = a["v"].astype(int).map(nidx).values
    ok = ~(pd.isna(u) | pd.isna(v)); a = a[ok]; u = u[ok].astype(np.int64); v = v[ok].astype(np.int64)
    N = len(nodes); E = len(a)
    outdeg = np.bincount(u, minlength=N).astype(float)
    area = a["area_m2"].fillna(0.0).values.astype(float)
    # 局部面积：load(n) = outdeg(n)·A(任一出边) − Σ A(入边)（accumulate 按出边数均分的逆运算）
    a_in = np.bincount(v, weights=area, minlength=N)
    a_out_any = np.zeros(N); np.maximum.at(a_out_any, u, area)
    load = np.clip(a_out_any * outdeg - a_in, 0.0, None)
    c_edge = a["runoff_c"].fillna(0.5).values.astype(float)
    c_node = np.full(N, np.nan); c_node[u] = c_edge                         # 任一出边的 C（上游面积加权，够用）
    c_node = np.where(np.isfinite(c_node), c_node, 0.5)
    prep = {"key": np.array(key), "gid": a["gid"].astype(int).values, "u": u, "v": v, "level": np.array([level[int(n)] for n in nodes], dtype=np.int64),
            "outdeg": outdeg, "load_m2": load, "c_node": c_node, "q_full": a["q_full"].values.astype(float), "tc_min": a["tc_min"].fillna(10.0).values.astype(float),
            "len_m": a["len_m"].fillna(0.0).values.astype(float), "zone": np.asarray(a["zone_name"].fillna("").astype(str).values, dtype="U"), "node_id": nodes,
            "removed": np.array(removed, dtype=np.int64), "prep_s": np.array(round(time.time() - t0, 1))}
    if cache:
        STATE.mkdir(parents=True, exist_ok=True); np.savez(cache_f, **prep)
    if log:
        log(f"  溢流路由准备：{E:,} 段 / {N:,} 节点 / {int(prep['level'].max()) + 1} 层，去环 {len(removed)} 段，{prep['prep_s']} s")
    return prep


def route(prep: dict, i_edge_dur: np.ndarray, durations_min: np.ndarray) -> dict:
    """i_edge_dur: (E, D) 每段管、每个历时的雨强 mm/h（已含片区因子）。返回每段 spill 峰值、梯形体积最大值、临界历时。"""
    u, v, outdeg, load, c_node, qfull, tc = prep["u"], prep["v"], prep["outdeg"], prep["load_m2"], prep["c_node"], prep["q_full"], prep["tc_min"]
    N = len(outdeg); E, D = i_edge_dur.shape
    # 节点雨强取任一出边的
    i_node = np.zeros((N, D)); i_node[u] = i_edge_dur
    q_loc = K * c_node[:, None] * i_node * (load / 1e4)[:, None]              # m³/s
    q_in = np.zeros((N, D)); q_edge = np.zeros((E, D)); spill = np.zeros((E, D))
    lev_node = prep["level"]; lev_edge = lev_node[u]
    order_e = np.argsort(lev_edge, kind="stable"); bounds = np.searchsorted(lev_edge[order_e], np.arange(lev_edge.max() + 2))
    for L in range(int(lev_edge.max()) + 1):
        idx = order_e[bounds[L]:bounds[L + 1]]
        if not len(idx):
            continue
        un = u[idx]
        Q = (q_in[un] + q_loc[un]) / outdeg[un][:, None]
        sp = np.maximum(Q - qfull[idx][:, None], 0.0)
        q_edge[idx] = Q; spill[idx] = sp
        np.add.at(q_in, v[idx], Q - sp)
    # 手册 3.6 梯形体积：对 d ≥ tc 的历时
    d = durations_min[None, :].astype(float); tcm = tc[:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        V = spill * ((d - tcm) + tcm * np.where(q_edge > 0, spill / q_edge, 0.0)) * 60.0
    V = np.where(d >= tcm - 1e-9, V, 0.0)
    V = np.where(V >= MIN_VOL_M3, V, 0.0)
    k = np.argmax(V, axis=1)
    Vmax = V[np.arange(E), k]
    return {"V_m3": Vmax, "d_star_min": durations_min[k].astype(float), "spill_peak_m3s": spill.max(axis=1), "q_peak_m3s": q_edge.max(axis=1), "spilling": Vmax > 0}


def backwater(prep: dict, r: dict, max_m: float = 300.0, ratio_thr: float = 0.8) -> dict:
    """下游顶托代理：每段「下游端人孔」到下游最近溢流人孔的沿管距离（溢流发生在溢流段的上游人孔；下游端人孔自己溢 = 0），逆拓扑层向量化；
    标记 = 自己不溢 ∧ 距离 ≤ max_m ∧ 峰值 Q / Q_full ≥ ratio_thr。"""
    u, v, lev, ln, qfull = prep["u"], prep["v"], prep["level"], prep["len_m"], prep["q_full"]
    E = len(u); N = len(lev)
    spilling = r["spilling"]
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(qfull > 0, r["q_peak_m3s"] / qfull, 0.0)
    node_dist = np.full(N, np.inf); dist = np.full(E, np.inf)
    lev_e = lev[u]; order = np.argsort(-lev_e, kind="stable")
    neg = -lev_e[order]; bounds = np.searchsorted(neg, np.arange(neg.min(), neg.max() + 2))
    for k in range(len(bounds) - 1):
        idx = order[bounds[k]:bounds[k + 1]]
        if not len(idx):
            continue
        d_e = node_dist[v[idx]]                                   # 该段下游端人孔到最近溢流人孔的距离
        dist[idx] = d_e
        np.minimum.at(node_dist, u[idx], np.where(spilling[idx], 0.0, d_e + ln[idx]))   # 上游端人孔：本段溢流则它就是溢流人孔
    flag = (~spilling) & np.isfinite(dist) & (dist <= max_m) & (ratio >= ratio_thr)
    return {"flag": flag, "dist_m": dist, "ratio": ratio, "edges": int(flag.sum()), "len_km": round(float(ln[flag].sum()) / 1000, 1),
            "len_share": round(float(ln[flag].sum() / max(ln.sum(), 1e-9)), 4), "max_m": float(max_m), "ratio_thr": float(ratio_thr)}


def intensities_for(cfg: dict, prep: dict, scen) -> tuple[np.ndarray, np.ndarray]:
    """(E, D) 雨强矩阵：基准重现期的 IDF 曲线 × 每段管所在片区的情景因子。"""
    s = scen if isinstance(scen, dict) else scenarios.get(cfg, scen)
    durs = np.array([float(x) for x in cfg["idf"]["durations_min"]], dtype=float)
    base = np.array([standards.idf_intensity(cfg, s["base_rp"], float(d)) for d in durs], dtype=float)
    f = scenarios.zone_factors(cfg, s, prep["zone"])
    return base[None, :] * np.asarray(f, dtype=float)[:, None], durs


def run(cfg: dict, prep: dict, scen, top_zones: int = 12) -> dict:
    """一个情景的全网溢流量与分片区汇总。"""
    t0 = time.perf_counter()
    I, durs = intensities_for(cfg, prep, scen)
    r = route(prep, I, durs)
    V = r["V_m3"]; sp = r["spilling"]
    NF = cfg.get("networkflood") or {}
    bw = backwater(prep, r, float(NF.get("backwater_m", 300)), float(NF.get("backwater_ratio", 0.8)))
    zone = pd.Series(prep["zone"]); ln = prep["len_m"]
    byz = pd.DataFrame({"zone": zone, "V": V, "len": ln, "sp": sp, "bw": bw["flag"]}).groupby("zone").agg(V_m3=("V", "sum"), len_km=("len", lambda x: x.sum() / 1000), n_spill=("sp", "sum"), n_bw=("bw", "sum"))
    byz = byz[byz.index != ""].sort_values("V_m3", ascending=False)
    s = scen if isinstance(scen, dict) else scenarios.get(cfg, scen)
    return {"scenario_key": s["key"], "total_m3": round(float(V.sum()), 0), "edges": int(len(V)), "edges_spilling": int(sp.sum()),
            "spilling_len_km": round(float(ln[sp].sum()) / 1000, 1), "spilling_len_share": round(float(ln[sp].sum() / max(ln.sum(), 1e-9)), 4),
            "peak_spill_total_m3s": round(float(r["spill_peak_m3s"].sum()), 2), "d_star_median_min": (float(np.median(r["d_star_min"][sp])) if sp.any() else None),
            "top_edges": [{"gid": int(prep["gid"][i]), "V_m3": round(float(V[i]), 0), "zone": str(prep["zone"][i]), "spill_m3s": round(float(r["spill_peak_m3s"][i]), 3)} for i in np.argsort(-V)[:15]],
            "by_zone": [{"zone_name": z, "V_m3": round(float(w["V_m3"]), 0), "len_km": round(float(w["len_km"]), 1), "n_spill": int(w["n_spill"]), "n_backwater": int(w["n_bw"])} for z, w in byz.head(top_zones).iterrows()],
            "backwater": {k: bw[k] for k in ("edges", "len_km", "len_share", "max_m", "ratio_thr")},
            "edge_V": pd.Series(V, index=prep["gid"]), "edge_backwater": pd.Series(bw["flag"], index=prep["gid"]), "edge_spilling": pd.Series(sp, index=prep["gid"]),
            "computed_ms": round((time.perf_counter() - t0) * 1000, 1),
            "method": "推理公式峰值流量沿管网 DAG 逐段路由，过不去的就地溢出，按手册 3.6 梯形取各历时最大体积；顶托风险 = 不溢但下游 %d m 内有溢流段且利用率 ≥ %.0f%%；不含真正的水面回推与调蓄" % (bw["max_m"], bw["ratio_thr"] * 100),
            "method_en": "Rational-method peak flows routed reach by reach down the network DAG, capped at full-pipe capacity with the excess spilled locally; Manual 3.6 trapezoid volume, max over IDF durations; backwater risk = not spilling but a spilling reach within %d m downstream and utilisation ≥ %.0f%%; no true surface-profile computation or storage" % (bw["max_m"], bw["ratio_thr"] * 100)}


def public(res: dict) -> dict:
    """去掉大数组，给接口用。"""
    return {k: v for k, v in res.items() if not k.startswith("edge_")}
