"""控端：在什么时间、什么位置、加或调什么设施。每条建议带前后对比、优先级、置信度、依据与假设。
所有建议状态都是「建议」，由人确认后才进入执行，对应任务书的「智能体建议-人工确认-执行-反馈」。

时间窗分五类（when_class）：
  立即        数据治理，不花工程钱但决定其他建议的可信度
  雨季前      维护与清淤，截止日在 config
  事件前      调度，以 NCM 预报为触发条件
  资本计划    工程，进下一年度 IFP 审批周期
  并入在批    工程，500 m 内已有在批/拟建雨水结构，建议并入该项目
"""
from __future__ import annotations
import datetime as dt
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from . import hydraulics as hy
from . import i18n


def _when_maintenance(cfg) -> tuple[str, str]:
    mmdd = cfg["decision"]["rainy_season_deadline"]
    today = dt.date.today()
    y = today.year if today.strftime("%m-%d") <= mmdd else today.year + 1
    return "雨季前", f"雨季前（{y}-{mmdd} 前完成）"


def _when_event(cfg) -> tuple[str, str]:
    return "事件前", f"事件前 24–72 小时（触发：NCM 预报 24h 雨量 ≥ {cfg['decision']['event_trigger_rain_mm']:.0f} mm）"


def _when_capital(planned_hit: str | None) -> tuple[str, str]:
    if planned_hit:
        return "并入在批", f"并入在批项目「{str(planned_hit).strip()[:60]}」的下一审批节点"
    return "资本计划", "下一年度资本计划（IFP 审批周期）"


def _planned_index(planned: pd.DataFrame):
    p = planned.dropna(subset=["x", "y"])
    p = p[p["review_status"].fillna("").str.lower().str.startswith(("proposed", "approved"))]
    return (cKDTree(p[["x", "y"]].values), p.reset_index(drop=True)) if len(p) else (None, p)


def _nearest_planned(tree, p, x, y, r):
    if tree is None or x is None or pd.isna(x):
        return None
    d, i = tree.query([x, y], distance_upper_bound=r)
    return p.loc[i, "project_name"] if i < len(p) else None


def _zn(x) -> str:
    return x if isinstance(x, str) and x.strip() else "未分区"


def _uid(r) -> str:
    u = r.get("terminal_unitid") if hasattr(r, "get") else None
    return u if isinstance(u, str) and u.strip() else f"节点{int(r['terminal'])}"


def _score(severity, exposure, context, confidence):
    return float(np.clip(100 * (0.4 * severity + 0.35 * exposure + 0.1 * context + 0.15 * confidence), 0, 100))


def generate(es: pd.DataFrame, cs: pd.DataFrame, zs: pd.DataFrame, vertices: pd.DataFrame, inlet_levels: pd.DataFrame,
             storage: pd.DataFrame, planned: pd.DataFrame, removed_gids: list[int], node_terminal: dict,
             cfg: dict, scenarios: dict[str, float]) -> pd.DataFrame:
    D, M = cfg["decision"], cfg["model"]
    names = list(scenarios)
    main, heavy = names[0], (names[1] if len(names) > 1 else names[0])
    tree, pidx = _planned_index(planned)
    vt = vertices.set_index("id")
    zrisk = zs.set_index("zone_name")["risk_score"].fillna(0.0)
    zmax = float(zrisk.max()) if len(zrisk) and zrisk.max() > 0 else 1.0
    zctx = lambda zn: float(zrisk.get(zn, 0.0)) / zmax
    act = es[es["is_active"]].copy()
    props: list[dict] = []

    def add(when_class, when, **kw):
        kw.update(when_class=when_class, when=when)
        kw.setdefault("status", "建议")
        kw["pid"] = len(props) + 1
        for k in ("lon", "lat", "x", "y", "gid", "terminal", "zone_name"):
            kw.setdefault(k, None)
        props.append(kw)

    # ---------- D1 扩管：常规雨下承载雨强不足的管段，每个终点取最严重一段 ----------
    over = act[(act[f"ratio_{main}"] > D["ratio_threshold"]) & (act["diameter_flag"] == "ok") & act["terminal"].notna()].copy()
    over["excess"] = over[f"ratio_{main}"] - 1.0
    top = (over.sort_values(["terminal", f"ratio_{main}"], ascending=[True, False]).groupby("terminal").head(1)
              .sort_values("n_inlets_up", ascending=False).head(D["max_per_type"]))
    conf_edge = lambda r: max(0.2, 1.0 - 0.4 * (r["slope_flag"] != "ok") - 0.3 * (r["orient"] == "guess") - 0.2 * (r["area_basis"] == "road"))
    sev_max = float(top["excess"].clip(upper=5).max()) if len(top) else 1.0
    exp_max = float(top["n_inlets_up"].max()) if len(top) else 1.0
    for _, r in top.iterrows():
        new_d = hy.required_diameter_mm(r[f"q_{main}"], r["slope"], r["n_manning"], D["target_ratio"], D["standard_diameters_mm"])
        run = over[over["terminal"] == r["terminal"]]
        after = (r[f"q_{main}"] / hy.manning_full_flow(new_d / 1000.0, r["slope"], r["n_manning"])) if new_d else None
        x, y = vt["x"].get(r["v"]), vt["y"].get(r["v"])
        wc, w = _when_capital(_nearest_planned(tree, pidx, x, y, D["merge_planned_within_m"]))
        jump = (new_d or 3000.0) / max(float(r["pipe_diameter"]), 1.0)
        if jump >= 3.0:
            add("立即", "立即（先核实台账）", kind="核实拓扑（疑似流向/连接错误）",
                title=f"{_zn(r['zone_name'])}：DN{int(r['pipe_diameter'])} 管段 {r['unitid'] if isinstance(r['unitid'], str) else r['gid']} 被算出承接 {r['area_m2']/1e4:.0f} ha、{int(r['n_inlets_up'])} 个进水口，需 DN{int(new_d) if new_d else '≥3000'} 才够，八成是流向或连接登错",
                zone_name=r["zone_name"], gid=int(r["gid"]), terminal=int(r["terminal"]), lon=vt["lon"].get(r["v"]), lat=vt["lat"].get(r["v"]), x=x, y=y,
                when_reason="管径跳三倍以上不是设计问题，是台账问题；核实后这一支的所有建议都会变",
                action={"现管径mm": float(r["pipe_diameter"]), "算得所需管径mm": new_d, "上游汇水面积ha": round(float(r["area_m2"]) / 1e4, 1), "方向来源": r["orient"], "坡度标记": r["slope_flag"]},
                before={"承载雨强mm/h": round(float(r["i_cap_mmh"]), 1)}, after={}, benefit="消除一处会把整支管网算错的拓扑错误",
                priority=_score(1.0, min(r["n_inlets_up"], 2000) / 2000.0, zctx(r["zone_name"]), 0.9), confidence=0.9,
                evidence={"来源": "pipe.sw_edge", "gid": int(r["gid"])}, caveats="")
            continue
        add(wc, w, kind="扩管改造",
            title=f"{_zn(r['zone_name'])}：管段 {r['unitid'] if isinstance(r['unitid'], str) else r['gid']} 由 DN{int(r['pipe_diameter'])} 扩至 DN{int(new_d) if new_d else '≥3000'}",
            zone_name=r["zone_name"], gid=int(r["gid"]), terminal=int(r["terminal"]), lon=vt["lon"].get(r["v"]), lat=vt["lat"].get(r["v"]), x=x, y=y,
            when_reason=f"工程类：需立项与审批；同一终点下共 {len(run)} 段承载雨强不足，宜整段一起改",
            action={"现管径mm": float(r["pipe_diameter"]), "建议管径mm": new_d, "坡度": round(float(r["slope"]), 5), "曼宁n": float(r["n_manning"]),
                    "同终点不足段数": int(len(run)), "同终点不足长度km": round(float(run["len_m"].sum()) / 1000, 2)},
            before={"承载雨强mm/h": round(float(r["i_cap_mmh"]), 1), f"能力比_{main}": round(float(r[f"ratio_{main}"]), 2), f"能力比_{heavy}": round(float(r[f"ratio_{heavy}"]), 2)},
            after={f"能力比_{main}": round(float(after), 2) if after else None, "承载雨强mm/h": round(scenarios[main] / D["target_ratio"], 1) if after else None},
            benefit=(f"上游 {int(r['n_inlets_up'])} 个进水口的承载雨强从 {r['i_cap_mmh']:.0f} 提到 ≥{scenarios[main]/D['target_ratio']:.0f} mm/h" if after else "需水力模型复核"),
            priority=_score(min(r["excess"], 5) / sev_max, r["n_inlets_up"] / exp_max, zctx(r["zone_name"]), conf_edge(r)),
            confidence=round(conf_edge(r), 2),
            evidence={"来源": "pipe.sw_edge", "gid": int(r["gid"]), "坡度标记": r["slope_flag"], "方向来源": r["orient"], "汇水面积法": r["area_basis"],
                      "汇水面积ha": round(float(r["area_m2"]) / 1e4, 2)},
            caveats="推理公式+曼宁满管流的规划级筛查；雨强取 DMT WA-726-1 (2022) B 区 IDF，单口汇水面积为假设值；扩管前先看下游段能力")

    # ---------- D2 新增调蓄/渗井：渗蓄制汇水区的容量缺口 ----------
    need = cs[cs["needs_storage"] & (cs["storage_deficit_m3"] > 0) & (cs["area_m2"] / 1e4 >= D["no_outlet_min_ha"])]
    need = need.sort_values("storage_deficit_m3", ascending=False).head(D["max_per_type"])
    dmax = float(need["storage_deficit_m3"].max()) if len(need) else 1.0
    for _, r in need.iterrows():
        wc, w = _when_capital(_nearest_planned(tree, pidx, r["x"], r["y"], D["merge_planned_within_m"]))
        kind = "新增渗井群" if r["storage_deficit_m3"] < 500 else "新增调蓄塘/池"
        add(wc, w, kind=kind,
            title=f"{_zn(r['zone_name'])}：{r['terminal_kind']}汇水区 {_uid(r)} 补 {r['storage_deficit_m3']:,.0f} m³ 蓄渗容量",
            zone_name=r["zone_name"], terminal=int(r["terminal"]), lon=r["lon"], lat=r["lat"], x=r["x"], y=r["y"],
            when_reason="工程类；渗蓄制汇水区靠渗蓄消纳，缺口越大越应排在前",
            action={"汇水面积ha": round(float(r["area_m2"]) / 1e4, 2), "设计雨深mm": M["design_depth_mm"], "现有蓄渗容量m3": round(float(r["storage_m3"]), 0),
                    "需补容量m3": round(float(r["storage_deficit_m3"]), 0), "选址": "汇水区终点附近，优先市政/未定用途地块"},
            before={"径流量m3": round(float(r["runoff_m3"]), 0), "蓄渗容量m3": round(float(r["storage_m3"]), 0)},
            after={"蓄渗容量m3": round(float(r["runoff_m3"]), 0)},
            benefit=f"{int(r['n_inlets'])} 个进水口所在汇水区在 {M['design_depth_mm']:.0f} mm 单场雨下不再外溢",
            priority=_score(r["storage_deficit_m3"] / dmax, min(r["n_inlets"], 300) / 300.0, zctx(r["zone_name"]), 0.6), confidence=0.6,
            evidence={"来源": "sw_edge 汇水累加 + pond/soakaway/sw_reservoirstructure", "终点类型": r["terminal_kind"], "汇入边数": int(r["n_edges"])},
            caveats="渗井容积按尺寸估算、缺尺寸取默认值；土壤渗透率未计；终点若实为未登记的连接，缺口会被高估")

    # ---------- D3 出水口高潮易涝：汇水区最低一成进水口相对高潮位净空不足 ----------
    tide = cs[(cs["terminal_kind"] == "出水口") & (cs["tide_clearance_m"] < D["tide_clearance_min_m"]) & (cs["area_m2"] / 1e4 >= D["no_outlet_min_ha"])].copy()
    tide["sev"] = (D["tide_clearance_min_m"] - tide["tide_clearance_m"]).clip(upper=3.0)
    tide = tide.sort_values(["sev", "area_m2"], ascending=False).head(D["max_per_type"])
    for _, r in tide.iterrows():
        wc, w = _when_capital(_nearest_planned(tree, pidx, r["x"], r["y"], D["merge_planned_within_m"]))
        has_pump = r["n_pumps"] > 0
        add(wc, w, kind="泵站增容校核" if has_pump else "出水口增泵/改闸",
            title=(f"{_zn(r['zone_name'])}：出水口 {r['terminal_unitid']} 汇水区最低一成进水口相对高潮位净空 {r['tide_clearance_m']:.2f} m，"
                   + ("已有泵站需校核" if has_pump else "建议加泵或改可控闸")),
            zone_name=r["zone_name"], terminal=int(r["terminal"]), lon=r["lon"], lat=r["lat"], x=r["x"], y=r["y"],
            when_reason="工程类；高潮遇暴雨时拍门不开、上游满管，是沿海排水最典型的失效",
            action={"出水口类型": r["outfall_type"], "管底相对MSL_m": None if pd.isna(r["invert_rel"]) else round(float(r["invert_rel"]), 2),
                    "高潮位假设_m": M["tide_high_m"], "最低一成进水口相对MSL_m": round(float(r["cover_p10_rel"]), 2),
                    f"设计流量_{main}_m3s": round(float(r[f"q_peak_{main}"]), 3), "上游汇水面积ha": round(float(r["area_m2"]) / 1e4, 1), "现有泵数": int(r["n_pumps"])},
            before={"高潮净空m": round(float(r["tide_clearance_m"]), 2), f"汇水区不足管长km_{main}": round(float(r[f"over_km_{main}"]), 2)},
            after={"高潮时可排流量m3s": round(float(r[f"q_peak_{main}"]), 3)},
            benefit=f"高潮期保住上游 {int(r['n_inlets'])} 个进水口、{r['len_km']:.1f} km 管网的出路",
            priority=_score(r["sev"] / 3.0, min(r["area_m2"] / 1e4, 300) / 300.0, zctx(r["zone_name"]), 0.7), confidence=0.7,
            evidence={"来源": "pipe.inlet.cover_level（MSL=100 基准）+ outfall.invert_level", "汇水区进水口数": int(r["n_cover"]) if pd.notna(r["n_cover"]) else 0},
            caveats="潮位取 DMT 手册设计高潮 +1.3 m NADD（表 4-5）；井盖标高为台账值")

    # ---------- D4a 疑似漏登的出水口/泵站：大断头 ----------
    ok_nodes = es[es["terminal_kind"].isin(["出水口", "泵站"])]["v"].drop_duplicates()
    okxy = vt.loc[vt.index.intersection(ok_nodes), ["x", "y"]].dropna()
    oktree = cKDTree(okxy.values) if len(okxy) else None
    sus = cs[cs["suspect_missing_outlet"]].sort_values("area_m2", ascending=False).head(D["max_per_type"] // 2)
    amax = float(sus["area_m2"].max()) if len(sus) else 1.0
    for _, r in sus.iterrows():
        dist = float(oktree.query([r["x"], r["y"]])[0]) if (oktree is not None and pd.notna(r["x"])) else None
        add("立即", "立即（先核实台账）", kind="核实终点（疑似漏登出水口/泵站）",
            title=f"{_zn(r['zone_name'])}：断头 {_uid(r)} 上游 {r['area_m2']/1e4:.0f} ha、{r['len_km']:.1f} km 管网，" + (f"距最近有出路节点 {dist:.0f} m" if dist is not None else "无有出路节点参照"),
            zone_name=r["zone_name"], terminal=int(r["terminal"]), lon=r["lon"], lat=r["lat"], x=r["x"], y=r["y"],
            when_reason="这么大的汇水区不可能真的没有出路；八成是台账里少了一段管或一个出水口/泵站。核实后上游所有建议的可信度都会变",
            action={"上游汇水面积ha": round(float(r["area_m2"]) / 1e4, 1), "上游管长km": round(float(r["len_km"]), 2), "上游进水口数": int(r["n_inlets"]),
                    "距最近有出路节点m": None if dist is None else round(dist, 0), "建议": "对照 GIS 与现场，补登缺失管段或终点资产"},
            before={"出路": "台账上无"}, after={"出路": "核实后补登"},
            benefit=f"{int(r['n_inlets'])} 个进水口的汇水归属得到确认",
            priority=_score(r["area_m2"] / amax, min(r["n_inlets"], 2000) / 2000.0, zctx(r["zone_name"]), 0.9), confidence=0.9,
            evidence={"来源": "sw_edge 拓扑终点分类", "终点表": r["terminal_kind"]}, caveats="")

    # ---------- D4b 小断头/封堵端且无蓄渗：连通或就地消纳 ----------
    used_terms = {p["terminal"] for p in props if p.get("terminal") is not None}
    dead = cs[cs["terminal_kind"].isin(["断头", "封堵端"]) & ~cs["suspect_missing_outlet"] & (cs["area_m2"] / 1e4 >= D["no_outlet_min_ha"] * 2)
              & (cs["storage_m3"] <= 0) & ~cs["terminal"].isin(used_terms)]
    dead = dead.sort_values("area_m2", ascending=False).head(D["max_per_type"] // 2)
    amax = float(dead["area_m2"].max()) if len(dead) else 1.0
    for _, r in dead.iterrows():
        dist = float(oktree.query([r["x"], r["y"]])[0]) if (oktree is not None and pd.notna(r["x"])) else None
        if dist is not None and dist < 10:
            add("立即", "立即（先核实台账）", kind="核实拓扑（疑似缺失连接）",
                title=f"{_zn(r['zone_name'])}：{r['terminal_kind']} {_uid(r)} 上游 {r['area_m2']/1e4:.1f} ha 无出路，但 {dist:.0f} m 内就有有出路的节点，八成是台账缺了一段连接",
                zone_name=r["zone_name"], terminal=int(r["terminal"]), lon=r["lon"], lat=r["lat"], x=r["x"], y=r["y"],
                when_reason="几米的缺口不是工程，是台账；补登后这一支的汇水归属就通了",
                action={"上游汇水面积ha": round(float(r["area_m2"]) / 1e4, 1), "上游管长km": round(float(r["len_km"]), 2), "距有出路节点m": round(dist, 1), "建议": "对照 GIS 与现场补登连接管段"},
                before={"出路": "台账上无"}, after={"出路": "补登后有"}, benefit=f"{int(r['n_inlets'])} 个进水口的汇水归属得到确认",
                priority=_score(0.6, min(r["n_inlets"], 300) / 300.0, zctx(r["zone_name"]), 0.9), confidence=0.9,
                evidence={"来源": "sw_edge 拓扑终点分类 + 最近有出路节点距离"}, caveats="")
            continue
        wc, w = _when_capital(_nearest_planned(tree, pidx, r["x"], r["y"], D["merge_planned_within_m"]))
        connect = dist is not None and dist < 600
        add(wc, w, kind="连通至有出路管网" if connect else "就地消纳（渗井/塘）",
            title=f"{_zn(r['zone_name'])}：{r['terminal_kind']} {_uid(r)} 上游 {r['area_m2']/1e4:.1f} ha 无出路" + (f"，距最近有出路节点 {dist:.0f} m" if dist is not None else ""),
            zone_name=r["zone_name"], terminal=int(r["terminal"]), lon=r["lon"], lat=r["lat"], x=r["x"], y=r["y"],
            when_reason="工程类；先核实是台账漏登还是真断头，再决定连通或消纳",
            action={"上游汇水面积ha": round(float(r["area_m2"]) / 1e4, 1), "上游管长km": round(float(r["len_km"]), 2), "距有出路节点m": None if dist is None else round(dist, 0),
                    "需消纳径流m3": round(float(r["runoff_m3"]), 0)},
            before={"出路": "无"}, after={"出路": "连通" if connect else "就地消纳"},
            benefit=f"{int(r['n_inlets'])} 个进水口获得出路",
            priority=_score(r["area_m2"] / amax, min(r["n_inlets"], 300) / 300.0, zctx(r["zone_name"]), 0.5), confidence=0.5,
            evidence={"来源": "sw_edge 拓扑终点分类", "终点表": r["terminal_kind"]},
            caveats="断头可能是数据漏登（源库有 1.3 万条拓扑问题记录），列为先核实项")

    # ---------- D5 数据治理：影响决策可信度的片区 + 打断环 ----------
    dz = zs[zs["rankable"] & (zs["data_issue_share"] > 0.25)].sort_values("data_issue_share", ascending=False).head(15)
    for _, r in dz.iterrows():
        add("立即", "立即", kind="数据治理/补测",
            title=f"{_zn(r['zone_name'])}：{r['data_issue_share']*100:.0f}% 管长缺标高、方向不明或冲突，先补测再定工程",
            zone_name=r["zone_name"], when_reason="不花工程钱，但决定上面所有工程建议的可信度",
            action={"问题管长km": round(float(r["data_issue_share"] * r["len_km"]), 1), "建议": "补测管底标高与流向，优先承载雨强不足段与出水口上游主干"},
            before={"数据问题份额": round(float(r["data_issue_share"]), 2)}, after={"数据问题份额": 0.05},
            benefit="该片区扩管/调蓄建议的置信度从 0.5 提到 0.9 以上",
            priority=_score(r["data_issue_share"], min(r["len_km"], 200) / 200.0, zctx(r["zone_name"]), 0.9), confidence=0.9,
            evidence={"来源": "sw_edge.slope_flag / orient / dir_conflict"}, caveats="")
    if removed_gids:
        add("立即", "立即", kind="数据治理/补测", title=f"打断有向环时移除了 {len(removed_gids)} 条边的方向，需人工核实流向",
            when_reason="环路使汇水累加失真", action={"gid样例": [int(g) for g in removed_gids[:20]], "总数": len(removed_gids)},
            before={}, after={}, benefit="消除累加失真", priority=60.0, confidence=0.9, evidence={"来源": "graph.build_dag"}, caveats="")

    # ---------- D6 调度：雨季前维护排序 ----------
    wc_m, w_m = _when_maintenance(cfg)
    for _, r in zs[zs["rankable"]].head(12).iterrows():
        if not r["risk_score"] > 0:
            continue
        add(wc_m, w_m, kind="雨季前维护排序", title=f"{_zn(r['zone_name'])}：风险分 {r['risk_score']:.0f}，优先清淤、检修拍门与进水口",
            zone_name=r["zone_name"], when_reason="维护窗口在雨季前；排序依据是承载雨强不足份额、无出路份额、高潮易涝出水口数与去年堵塞率",
            action={"承载雨强中位mm/h": None if pd.isna(r["i_cap_p50"]) else round(float(r["i_cap_p50"]), 1),
                    f"不足份额_{main}": round(float(r[f"over_share_{main}"] or 0), 3), "无出路份额": round(float(r["no_outlet_share"] or 0), 3),
                    "高潮易涝出水口数": int(r["n_tide_outfalls"]), "疑似漏登终点数": int(r["n_suspect_outlets"]),
                    "每百公里堵塞数": None if pd.isna(r["blockage_per_100km"]) else round(float(r["blockage_per_100km"]), 1),
                    "蓄渗缺口m3": round(float(r["storage_deficit_m3"]), 0)},
            before={"风险分": float(r["risk_score"])}, after={}, benefit="把有限的维护队伍排到最可能出事的片区",
            priority=float(min(r["risk_score"], 100)), confidence=0.7,
            evidence={"来源": "zone_state + liveability.fact_oi_indicators(sewage_blocakge_sws, 2024-01~04)"}, caveats="堵塞统计只覆盖 2024 年 1–4 月")

    # ---------- D6 调度：事件前临时泵位（汇水区内相对低点） ----------
    wc_e, w_e = _when_event(cfg)
    il = inlet_levels.dropna(subset=["vertex_id", "cover_level"]).copy()
    il = il[il["cover_level"] > 50]
    il["vertex_id"] = il["vertex_id"].astype(int)
    il["terminal"] = il["vertex_id"].map(node_terminal)
    il = il.dropna(subset=["terminal"])
    risky = set(cs[(cs[f"over_share_{heavy}"] > 0.3) & (cs["n_edges"] >= 20)]["terminal"])
    il["x"] = il["vertex_id"].map(vt["x"]); il["y"] = il["vertex_id"].map(vt["y"])
    il = il.dropna(subset=["x", "y"])
    cand = il[il["terminal"].isin(risky)]
    if len(cand) and len(il) > 10:
        alltree = cKDTree(il[["x", "y"]].values)
        cov = il["cover_level"].values
        nb = alltree.query_ball_point(cand[["x", "y"]].values, r=250.0)
        med = np.array([np.median(cov[ix]) if len(ix) >= 6 else np.nan for ix in nb])
        cand = cand.assign(below=med - cand["cover_level"].values)
    else:
        cand = cand.assign(below=np.nan)
    low = cand[(cand["below"] >= D["low_point_below_median_m"]) & (cand["below"] <= D["low_point_max_m"])]
    low = low.sort_values("below", ascending=False).groupby("terminal").head(1).head(20)
    for _, r in low.iterrows():
        zn = es.loc[es["v"] == r["vertex_id"], "zone_name"].dropna()
        zn = zn.iat[0] if len(zn) else None
        add(wc_e, w_e, kind="事件前临时泵位",
            title=f"{_zn(zn)}：进水口 {r['unitid']} 比 250 m 邻域中位低 {r['below']:.1f} m 且所在汇水区强降雨下承载不足，预置移动泵",
            zone_name=zn, terminal=int(r["terminal"]), lon=vt["lon"].get(r["vertex_id"]), lat=vt["lat"].get(r["vertex_id"]), x=vt["x"].get(r["vertex_id"]), y=vt["y"].get(r["vertex_id"]),
            when_reason="调度类：不改设施，按预报把移动泵与人员预置到低点",
            action={"井盖标高相对MSL_m": round(float(r["cover_level"] - M["datum_offset_m"]), 2), "低于250m邻域中位m": round(float(r["below"]), 2)},
            before={}, after={}, benefit="缩短低点积水时间", priority=float(30 + 10 * min(r["below"], 3.0)), confidence=0.6,
            evidence={"来源": "pipe.inlet.cover_level + catchment_state"}, caveats="井盖标高是台账值，需现场核")

    p = pd.DataFrame(props)
    if len(p):
        p["priority"] = p["priority"].round(1)
        p = p.sort_values("priority", ascending=False).reset_index(drop=True)
        p["rank"] = np.arange(1, len(p) + 1)
        p = i18n.add_english(p)
    return p
