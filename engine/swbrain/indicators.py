"""监端第二步：管段状态 → 汇水区状态 → 片区状态。

核心状态量是每段管的「承载雨强」i_cap（mm/h）：在当前汇水面积假设下，这段管刚好满管时的雨强。
它把管径、坡度、材质、上游规模压成一个能跟设计雨强直接比的数，比能力比更好解释。"""
from __future__ import annotations
import re
import difflib
import numpy as np
import pandas as pd
from . import hydraulics as hy


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower()) if isinstance(s, str) else ""


def edge_state(e: pd.DataFrame, edge_area: dict, edge_cnt: dict, edge_uplen: dict, terminal_of_node: dict,
               terminals: pd.DataFrame, cfg: dict, scenarios: dict[str, float], ground_slope: pd.Series | None = None,
               area_mode: str = "max", edge_c: dict | None = None) -> pd.DataFrame:
    """area_mode="max"：进水口法与道路法取大者（估算）；"dtm"：上游进水口的 DTM 子汇水区之和，上游没有进水口的段退回估算。
    edge_c：每段管上游按面积加权的径流系数（DTM 地表覆盖），没有的用 [model].runoff_c。"""
    m = cfg["model"]
    s = e.copy()
    s["slope"], s["slope_flag"] = hy.edge_slope(s, m["min_slope"], m["default_slope"], ground_slope)
    s["n_manning"] = hy.manning_n_for(s["pipe_material"], m["manning"])
    d_m = (s["pipe_diameter"].fillna(300.0).clip(lower=100.0)) / 1000.0
    s["diameter_flag"] = np.where(s["pipe_diameter"].isna() | (s["pipe_diameter"] < 100), "无管径", "ok")
    s["q_full"] = hy.manning_full_flow(d_m.values, s["slope"].values, s["n_manning"].values)
    a_inlet = s["gid"].map(edge_area).fillna(0.0)
    up_len = s["gid"].map(edge_uplen).fillna(0.0) + s["len_m"].fillna(0.0)
    a_road = up_len * m["road_width_m"]
    if area_mode == "dtm":
        ok = a_inlet.values > 1.0
        s["area_m2"] = np.where(ok, a_inlet.values, np.maximum(a_inlet.values, a_road.values))
        s["area_basis"] = np.where(ok, "dtm", np.where(a_inlet.values >= a_road.values, "inlet", "road"))
    else:
        s["area_m2"] = np.maximum(a_inlet.values, a_road.values)
        s["area_basis"] = np.where(a_inlet.values >= a_road.values, "inlet", "road")
    s["runoff_c"] = (pd.to_numeric(s["gid"].map(edge_c), errors="coerce").fillna(m["runoff_c"]).astype(float).values if edge_c else float(m["runoff_c"]))
    s["n_inlets_up"] = s["gid"].map(edge_cnt).fillna(0.0)
    s["terminal"] = s["v"].map(terminal_of_node)
    s = s.merge(terminals, on="terminal", how="left")
    # 承载雨强：Q_full = C·i·A → i = Q_full/(C·A)，换算成 mm/h
    with np.errstate(divide="ignore", invalid="ignore"):
        s["i_cap_mmh"] = np.where(s["area_m2"] > 1.0, s["q_full"] / (s["runoff_c"] * s["area_m2"]) * 3.6e6, np.nan)
    for name, i in scenarios.items():
        q = hy.rational_q(s["area_m2"].values, i, s["runoff_c"].values)
        s[f"q_{name}"] = q
        s[f"ratio_{name}"] = q / np.where(s["q_full"].values > 1e-9, s["q_full"].values, np.nan)
    s["is_active"] = ~s["pipe_subtype"].isin(["Abandoned"]) & ~s["status"].isin(["Abandoned", "Expired"])
    return s


def _wmedian(values: np.ndarray, weights: np.ndarray) -> float:
    """按长度加权的中位数。"""
    ok = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not ok.any():
        return float("nan")
    v, w = values[ok], weights[ok]
    o = np.argsort(v)
    cw = np.cumsum(w[o])
    return float(v[o][np.searchsorted(cw, cw[-1] / 2.0)])


def catchment_state(es: pd.DataFrame, vertices: pd.DataFrame, storage: pd.DataFrame, outfalls: pd.DataFrame,
                    inlet_levels: pd.DataFrame, node_terminal: dict, cfg: dict, scenarios: dict[str, float]) -> pd.DataFrame:
    """按终点聚合。蓄渗容量按 vertex_id 归到终点所在汇水区；塘/调蓄池按最近终点归。"""
    m, D = cfg["model"], cfg["decision"]
    act = es[es["is_active"]]
    g = act.groupby("terminal")
    c = pd.DataFrame({
        "n_edges": g.size(),
        "len_km": g["len_m"].sum() / 1000.0,
        "zone_name": g["zone_name"].agg(lambda x: x.mode().iat[0] if x.notna().any() else None),
        "terminal_kind": g["terminal_kind"].first(),
        "i_cap_p50": g.apply(lambda d: _wmedian(d["i_cap_mmh"].values.astype(float), d["len_m"].values.astype(float))),
    })
    into = act[act["v"] == act["terminal"]].groupby("terminal")
    c["area_m2"] = into["area_m2"].sum()
    c["n_inlets"] = into["n_inlets_up"].sum()
    for name in scenarios:
        c[f"q_peak_{name}"] = into[f"q_{name}"].sum()
        c[f"max_ratio_{name}"] = g[f"ratio_{name}"].max()
        over = act[act[f"ratio_{name}"] > D["ratio_threshold"]].groupby("terminal")["len_m"].sum()
        c[f"over_km_{name}"] = (over / 1000.0).reindex(c.index).fillna(0.0)
        c[f"over_share_{name}"] = c[f"over_km_{name}"] / c["len_km"].where(c["len_km"] > 0)
    c = c.reset_index()
    vt = vertices.set_index("id")
    for col in ("x", "y", "lon", "lat", "unitid"):
        c["terminal_unitid" if col == "unitid" else col] = c["terminal"].map(vt[col])

    # 进水口井盖标高（相对 MSL）：每个汇水区的 p10 / p50，用于顶托净空与低点
    il = inlet_levels.dropna(subset=["vertex_id", "cover_level"]).copy()
    il = il[il["cover_level"] > 50]
    il["cover_rel"] = il["cover_level"] - m["datum_offset_m"]
    il["terminal"] = il["vertex_id"].astype(int).map(node_terminal)
    cov = il.dropna(subset=["terminal"]).groupby("terminal")["cover_rel"].agg(cover_p10_rel=lambda x: x.quantile(0.1), cover_p50_rel="median", n_cover="size")
    c = c.merge(cov, left_on="terminal", right_index=True, how="left")

    # 蓄渗与泵
    from scipy.spatial import cKDTree
    st = storage.copy()
    st["terminal"] = st["vertex_id"].map(lambda v: node_terminal.get(int(v)) if pd.notna(v) else None)
    need = st["terminal"].isna() & st["x"].notna()
    if need.any() and len(c):
        tree = cKDTree(c[["x", "y"]].fillna(0).values)
        _, idx = tree.query(st.loc[need, ["x", "y"]].values, distance_upper_bound=1500.0)
        ok = idx < len(c)
        st.loc[need[need].index[ok], "terminal"] = c["terminal"].values[idx[ok]]
    vol = st[st["kind"].isin(["pond", "reservoir", "soakaway"])].groupby("terminal")["volume_m3"].sum()
    pumps = st[st["kind"] == "pump"].groupby("terminal").agg(n_pumps=("unitid", "size"), pump_kw=("power_kw", "sum"))
    c = c.merge(vol.rename("storage_m3"), left_on="terminal", right_index=True, how="left")
    c = c.merge(pumps, left_on="terminal", right_index=True, how="left")
    c["storage_m3"] = c["storage_m3"].fillna(0.0); c["n_pumps"] = c["n_pumps"].fillna(0).astype(int)
    # 汇水区径流系数：入终点各段按面积加权（DTM 地表覆盖）；没有的用 [model].runoff_c
    if "runoff_c" in act.columns:
        wc = act[act["v"] == act["terminal"]].assign(ac=lambda d: d["area_m2"] * d["runoff_c"]).groupby("terminal").agg(ac=("ac", "sum"), aa=("area_m2", "sum"))
        c = c.merge((wc["ac"] / wc["aa"].where(wc["aa"] > 0)).rename("runoff_c"), left_on="terminal", right_index=True, how="left")
        c["runoff_c"] = c["runoff_c"].fillna(m["runoff_c"])
    else:
        c["runoff_c"] = m["runoff_c"]
    c["runoff_m3"] = hy.runoff_volume(c["area_m2"].values, m["design_depth_mm"], c["runoff_c"].values)

    # 终点性质：渗井/封堵端是渗蓄制排水的设计使然；大断头多半是漏登的出水口或泵站
    area_ha = c["area_m2"] / 1e4
    c["suspect_missing_outlet"] = (c["terminal_kind"] == "断头") & (area_ha >= D["dead_end_suspect_ha"])
    c["needs_storage"] = c["terminal_kind"].isin(["渗井", "封堵端"]) | ((c["terminal_kind"] == "断头") & ~c["suspect_missing_outlet"])
    c["storage_deficit_m3"] = np.where(c["needs_storage"], np.maximum(c["runoff_m3"] - c["storage_m3"], 0.0), 0.0)

    # 出水口顶托：管底相对 MSL、以及汇水区最低一成进水口相对高潮位的净空
    of = outfalls.dropna(subset=["vertex_id"]).copy()
    of["invert_rel"] = np.where(of["invert_level"] > 50, of["invert_level"] - m["datum_offset_m"], np.nan)
    of["tide_risk_m"] = m["tide_high_m"] - of["invert_rel"]
    c = c.merge(of[["vertex_id", "invert_rel", "tide_risk_m", "unittypedesc"]].rename(columns={"vertex_id": "terminal", "unittypedesc": "outfall_type"}),
                on="terminal", how="left")
    c["tide_clearance_m"] = np.where(c["terminal_kind"] == "出水口", c["cover_p10_rel"] - m["tide_high_m"], np.nan)
    return c


def zone_state(es: pd.DataFrame, cs: pd.DataFrame, blockage: pd.DataFrame, cfg: dict, scenarios: dict[str, float]) -> pd.DataFrame:
    D = cfg["decision"]
    act = es[es["is_active"]]
    g = act.groupby("zone_name", dropna=False)
    z = pd.DataFrame({"n_edges": g.size(), "len_km": g["len_m"].sum() / 1000.0,
                      "i_cap_p50": g.apply(lambda d: _wmedian(d["i_cap_mmh"].values.astype(float), d["len_m"].values.astype(float)))})
    for name in scenarios:
        over = act[act[f"ratio_{name}"] > D["ratio_threshold"]].groupby("zone_name", dropna=False)["len_m"].sum()
        z[f"over_km_{name}"] = (over / 1000.0).reindex(z.index).fillna(0.0)
        z[f"over_share_{name}"] = z[f"over_km_{name}"] / z["len_km"].where(z["len_km"] > 0)
    no_out = act[~act["terminal_kind"].isin(["出水口", "泵站", "渗井"])].groupby("zone_name", dropna=False)["len_m"].sum()
    z["no_outlet_share"] = (no_out / 1000.0).reindex(z.index).fillna(0.0) / z["len_km"].where(z["len_km"] > 0)
    bad = act[(act["slope_flag"] != "ok") | (act["orient"] == "guess") | act["dir_conflict"].fillna(False).astype(bool)]
    z["data_issue_share"] = (bad.groupby("zone_name", dropna=False)["len_m"].sum() / 1000.0).reindex(z.index).fillna(0.0) / z["len_km"].where(z["len_km"] > 0)
    cz = cs.groupby("zone_name", dropna=False)
    z["storage_deficit_m3"] = cz["storage_deficit_m3"].sum().reindex(z.index).fillna(0.0)
    z["n_tide_outfalls"] = cs[cs["tide_clearance_m"] < D["tide_clearance_min_m"]].groupby("zone_name", dropna=False).size().reindex(z.index).fillna(0).astype(int)
    z["n_suspect_outlets"] = cs[cs["suspect_missing_outlet"]].groupby("zone_name", dropna=False).size().reindex(z.index).fillna(0).astype(int)
    z["n_inlets"] = cz["n_inlets"].sum().reindex(z.index).fillna(0.0)
    z = z.reset_index()
    z["rankable"] = z["len_km"] >= D["zone_min_km"]

    bl = blockage.dropna(subset=["district_name"]).copy()
    bl["key"] = bl["district_name"].map(_norm)
    keys = bl["key"].tolist()
    def match(zn):
        k = _norm(zn)
        if not k:
            return None
        hit = difflib.get_close_matches(k, keys, n=1, cutoff=0.8)
        return hit[0] if hit else None
    z["blockage_key"] = z["zone_name"].map(match)
    z = z.merge(bl[["key", "district_name", "blockage_per_100km", "total_blockages"]].drop_duplicates("key"),
                left_on="blockage_key", right_on="key", how="left").drop(columns=["key"])

    main = list(scenarios)[0]
    def nz(col, mask):
        v = z[col].fillna(0.0).astype(float).where(mask, 0.0)
        mx = v.max()
        return v / mx if mx > 0 else v * 0
    r = z["rankable"]
    z["risk_score"] = (100 * (0.5 * nz(f"over_share_{main}", r) + 0.1 * nz("no_outlet_share", r) + 0.2 * nz("n_tide_outfalls", r)
                              + 0.2 * nz("blockage_per_100km", r))).round(1)
    z.loc[~r, "risk_score"] = np.nan
    return z.sort_values(["rankable", "risk_score"], ascending=[False, False])
