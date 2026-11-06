"""一次完整运行：测（取数）→ 监（建图、状态）→ 控（建议）→ 输出。
用法：python -m swbrain.run [--config config.toml] [--no-db] [--top-edges 1500]
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path
import numpy as np
import pandas as pd

from . import networkflood, db, extract, graph, indicators, decisions, export, ingest, whatif, contracts, verification, i18n, refdata, simulate, dispatch, travel, standards, terrain, catchments, floodpoints, hotspots as hsm


def _wq(values, weights, q):
    ok = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    v, w = values[ok], weights[ok]
    o = np.argsort(v); cw = np.cumsum(w[o])
    return v[o][min(np.searchsorted(cw, cw[-1] * q), len(v) - 1)]


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-db", action="store_true", help="不写本地 PostGIS，只出文件")
    ap.add_argument("--top-edges", type=int, default=1500, help="导出几何的瓶颈管段数")
    ap.add_argument("--note", default="")
    a = ap.parse_args(argv)
    cfg = db.load_config(a.config)
    root = Path(__file__).resolve().parents[1]
    scenarios = {k: float(v) for k, v in cfg["model"]["scenarios"].items()}

    # ---- 测：取数（只读） ----
    log("连接 pipe 库")
    with db.connect_source(cfg, "pipe") as pc:
        edges = extract.load_edges(pc, cfg); log(f"  边 {len(edges):,}")
        vertices = extract.load_vertices(pc, cfg); log(f"  顶点 {len(vertices):,}")
        node_outfall = extract.load_node_outfall(pc, cfg)
        inlet_levels = extract.load_inlet_levels(pc, cfg); log(f"  进水口/雨水井标高 {len(inlet_levels):,}")
        outfalls = extract.load_outfalls(pc, cfg); log(f"  出水口 {len(outfalls)}")
        storage = extract.load_storage(pc, cfg); log(f"  蓄渗/泵 {storage.groupby('kind').size().to_dict()}")
    log("连接 makani 库")
    with db.connect_source(cfg, "makani") as mc:
        planned = extract.load_planned(mc); log(f"  在批/拟建雨水结构 {len(planned)}")
        districts = extract.load_districts(mc)
    log("连接宜居库")
    with db.connect_source(cfg, "liveability") as lc:
        blockage = extract.load_blockage(lc); log(f"  分区堵塞统计 {len(blockage)}")

    # ---- 监：建图与状态 ----
    log("定向、建 DAG")
    e = graph.orient_edges(edges, vertices, node_outfall)
    g, removed = graph.build_dag(e)
    log(f"  DAG 节点 {g.number_of_nodes():,} 边 {g.number_of_edges():,}，打断环移除 {len(removed)} 条")
    e = e[~e["gid"].isin(removed)].copy()
    a_inlet = cfg["model"]["inlet_area_m2"]
    load = {int(v): a_inlet for v in inlet_levels["vertex_id"].dropna().astype(int).unique()}
    # 没挂到资产的 inlet 顶点也算进水口（node_table 标为 inlet/catchbasin 的顶点）
    for v, nt in zip(vertices["id"], vertices["node_table"]):
        if nt in ("inlet", "catchbasin") and int(v) not in load:
            load[int(v)] = a_inlet
    # ---- DTM 子汇水区（可选）：进水口面积与径流系数换成地形推算，替掉 600 m²/进水口 与 20 m 路宽 ----
    edge_c, area_mode, csum = None, "max", None
    if catchments.enabled(cfg):
        log("DTM 子汇水区：进水口汇水面积与径流系数（首次约 10 分钟，之后用缓存）")
        cat = catchments.build(cfg, vertices, log=log)
        P = catchments.params(cfg); csum = catchments.summary(cat, P)
        cat_ok = cat[cat["area_m2"] > 0]
        cat_c = dict(zip(cat_ok["vertex_id"].astype(int), cat_ok["c"].astype(float)))
        # 有地形汇水区的按地形（含全在地块内、公共面积为 0 的：按下限计，不再用 600 m² 估）
        for vid, a_ in zip(cat["vertex_id"].astype(int), cat["area_m2"].astype(float).fillna(0.0)):
            load[int(vid)] = max(a_, float(P["min_area_m2"]))
        c0 = float(cfg["model"]["runoff_c"])
        loadC = {v_: a_ * (cat_c[v_] if v_ in cat_c and np.isfinite(cat_c[v_]) else c0) for v_, a_ in load.items()}
        area_mode = "dtm"
        log(f"  {csum['n_with_area']:,}/{csum['n_inlets']:,} 个进水口有地形汇水区：面积中位 {csum['area_median_m2']:.0f} m²（原假设 {a_inlet:.0f}），P90 {csum['area_p90_m2']:.0f}，"
            f"合计 {csum['area_total_km2']} km²，C 中位 {csum['c_median']}，同格均分 {csum['n_shared_cell']:,}，截到 {P['max_area_m2']/1e4:.0f} ha 上限 {csum['n_capped']}")
    else:
        log("未启用 DTM 子汇水区，汇水面积按进水口 × 600 m² 与管长 × 20 m 取大者估")
    edge_len = dict(zip(e["gid"].astype(int), e["len_m"].fillna(0.0)))
    log("沿流向累加汇水面积")
    edge_area, edge_cnt, edge_uplen, terminal = graph.accumulate(g, load, edge_len)
    if area_mode == "dtm":
        edge_ac, _, _, _ = graph.accumulate(g, loadC, None)
        edge_c = {gid: (ac / edge_area[gid]) if edge_area.get(gid, 0.0) > 0 else float("nan") for gid, ac in edge_ac.items()}
    terminals = graph.classify_terminals(terminal, vertices)
    log(f"  终点 {len(terminals):,}：{terminals['terminal_kind'].value_counts().to_dict()}")
    # ---- 地形（客户 5 m DTM，可选）：补井盖标高、无管底标高管段的地面坡度 ----
    gs = None
    if terrain.available(cfg):
        log("DTM 高程：顶点采样（首次慢，之后用缓存）")
        vz = terrain.vertex_elevation(vertices, cfg)
        inlet_levels, tst = terrain.fill_cover_levels(inlet_levels, vertices, vz, cfg)
        log(f"  井盖标高：台账 {tst['台账']:,}，DTM 补缺 {tst['DTM补缺']:,}，DTM 补无资产顶点 {tst['DTM补顶点']:,}，"
            f"与 DTM 差 >{terrain.params(cfg)['cover_tol_m']} m 的台账值 {tst['可疑']:,}（保留台账），仍缺 {tst['仍缺']:,}")
        gs = terrain.ground_slope(e, vz)
        noinv = ~e["invert_valid"].fillna(False).astype(bool)
        log(f"  无管底标高管段 {int(noinv.sum()):,} 条，地面坡度 ≥ 默认坡度可改用 DTM 的 {int((noinv & (gs >= cfg['model']['default_slope'])).sum()):,} 条")
    else:
        log("未配置 DTM（[terrain].dtm_path）或文件不存在，跳过高程补缺")
    log("管段状态")
    es = indicators.edge_state(e, edge_area, edge_cnt, edge_uplen, terminal, terminals, cfg, scenarios, ground_slope=gs, area_mode=area_mode, edge_c=edge_c)
    log(f"  面积依据 {es.loc[es['is_active'], 'area_basis'].value_counts().to_dict()}，径流系数中位（按长度） {float(es.loc[es['is_active'], 'runoff_c'].median()):.2f}")
    # ---- 法定设计标准：DMT WA-726-1 表 3-2（分设施重现期）/ 3-3（历时倍数）/ 3-6（B 区 IDF）落到每段管 ----
    log("法定设计标准（DMT WA-726-1 表 3-2 / 3-3 / 3-6）")
    st0 = root / "out" / "state"
    if not (st0 / "standards_buildings.parquet").exists():
        log("  抓取分类参考数据（首次）：主干路中心线、隧道、机场、公园、建筑用途"); log("  " + json.dumps(standards.build_ref(cfg), ensure_ascii=False))
    if not (st0 / "facilities.parquet").exists():
        log("  抓取路网与重点单位参考数据（首次）"); refdata.build_cache(cfg)
    cls = standards.classify_edges(es, vertices, standards.load_ref(), refdata.load_cache()["facilities"], cfg)
    dsg = standards.design_intensity(es, cls, standards.time_of_concentration(es, g, cfg), cfg)
    es = es.merge(dsg, on="gid", how="left")
    code = standards.compliance_summary(es, cfg)
    log(f"  分类 {es.loc[es['is_active'], 'design_class'].value_counts().to_dict()}")
    log(f"  达标管长份额 {code['达标管长份额']:.0%}，不达标 {code['不达标km']} km，校核不达标 {code['校核不达标km']} km，推理公式适用范围外 {code['推理公式适用范围外管长km']} km")
    log("全网溢流路由（10 年一遇）：过不去的就地溢出 + 下游顶托代理")
    summary_nf = None
    try:
        nf_prep = networkflood.prepare(es, log=log)
        nf10 = networkflood.run(cfg, nf_prep, "10")
        es["nf_spill_m3_10"] = es["gid"].map(nf10["edge_V"]).fillna(0.0)
        es["nf_backwater_10"] = es["gid"].map(nf10["edge_backwater"]).fillna(False).astype(bool)
        summary_nf = networkflood.public(nf10)
        log(f"  全网溢流 {nf10['total_m3']:,.0f} m³，溢流管段 {nf10['edges_spilling']:,}（占管长 {nf10['spilling_len_share']:.1%}），顶托风险管段 {nf10['backwater']['edges']:,}（{nf10['backwater']['len_km']} km）")
    except Exception as ex:
        log(f"  溢流路由跳过：{str(ex)[:160]}")
    log("汇水区状态")
    cs = indicators.catchment_state(es, vertices, storage, outfalls, inlet_levels, terminal, cfg, scenarios)
    log("片区状态")
    zs = indicators.zone_state(es, cs, blockage, cfg, scenarios)

    # ---- 控：建议 ----
    log("生成建议")
    props = decisions.generate(es, cs, zs, vertices, inlet_levels, storage, planned, removed, terminal, cfg, scenarios)
    props = standards.annotate_proposals(props, cfg)
    hs_df = hsm.load(root / cfg["hotspots"]["file"]) if "hotspots" in cfg else None
    tsum = None
    if hs_df is not None and terrain.available(cfg):
        log("热点填洼分析（5 m DTM，灌到溢出）")
        ht = terrain.hotspot_terrain(hs_df, cfg, log=log)
        hs_df = hs_df.merge(ht.drop(columns=["window_m", "risk_low"]), on="hotspot_id", how="left")
        tsum = terrain.summary(ht)
        log(f"  {tsum['n']} 个热点：洼深 ≥0.3 m {tsum['n_depth_030']}，≥0.5 m {tsum['n_depth_050']}，填洼体积合计 {tsum['pond_volume_total_m3']:,.0f} m³，"
            f"洼地出窗（下限）{tsum['n_truncated']}，近海 {tsum['n_coastal']}，无明显洼地 {tsum['n_flat']}")
    if hs_df is not None:
        props = hsm.annotate_proposals(props, hs_df, cfg)
        log(f"  客户热点 {len(hs_df)} 个（{hs_df['region'].value_counts().to_dict()}），300 m 内有客户热点的建议 {int(props['client_hotspot_id'].notna().sum())} 条")
    if len(props):
        props = props.sort_values("priority", ascending=False).reset_index(drop=True); props["rank"] = list(range(1, len(props) + 1))
    log(f"  建议 {len(props)} 条：{props['kind'].value_counts().to_dict() if len(props) else {}}")

    # ---- 预测侧基线（有就并进来，改用模型量重排严重度） ----
    preds = ingest.load_all(root, cfg)
    base = ingest.baseline_states(preds)
    pred_summary = {}
    if base:
        es, cs, pred_summary = ingest.merge_into_states(es, cs, base, terminal)
        props = ingest.reprioritize(props, cs, list(scenarios)[0])
        log(f"  并入预测基线 {list(base)}，建议已按模型冒溢量重排")
    else:
        log("  exchange/predictions 里没有预测基线，严重度用本系统承载雨强")

    # ---- what-if：干预集合 → 评估（代理 / 外部）→ 预算内组合 → 排期 → 发请求 ----
    log("what-if 评估与组合")
    baseline_run = next(iter(base.values()))["run_id"] if base else None
    wi = whatif.run_whatif(es, cs, props, cfg, scenarios, root, cfg["exchange"]["network_version"], baseline_run)
    plan, sched = wi["plan"], wi["schedule"]
    standard = whatif.cost_to_standard(es, cfg, scenarios)
    log("  全网达标扩管成本上限：" + json.dumps(standard, ensure_ascii=False))
    splan = whatif.standard_plan(es, cs, cfg, scenarios)
    splan_code = whatif.standard_plan(es, cs, cfg, scenarios, target="法定标准")
    (root / "out").mkdir(parents=True, exist_ok=True)
    if "projects" in splan_code:
        splan_code["projects"].to_csv(root / "out" / "standard_plan_code_projects.csv", index=False, encoding="utf-8-sig")
    log(f"  按「法定标准（表 3-2 分设施）」达标：{splan_code.get('n_projects')} 个工程项目、总成本 {splan_code.get('total_cost_aed', 0)/1e8:.0f} 亿 AED → {splan_code.get('years')} 年")
    log(f"  按「{splan.get('target')}」达标：{splan.get('n_projects')} 个工程项目、总成本 {splan.get('total_cost_aed', 0)/1e8:.0f} 亿 AED、年度预算 {splan.get('annual_budget_aed', 0)/1e8:.0f} 亿 → {splan.get('years')} 年；先核实 {splan.get('n_suspect_first')} 个疑似漏登汇水区（{splan.get('suspect_km')} km）")
    sur = whatif.SurrogateEvaluator(es, cs, cfg, scenarios)
    selected_ids = plan[plan["selected"]]["set_id"].tolist() if len(plan) else []
    residual = whatif.residual_state(sur, wi["sets"], selected_ids, cfg, cfg["standard"]["target_scenario"])
    log("  预算内方案实施后的剩余：" + json.dumps(residual["summary"], ensure_ascii=False))
    observations, comparisons = verification.run_verification(root, cfg)
    ready = verification.readiness(cfg, root, es, cs, preds, observations)
    promise = verification.commitment(cfg, splan, ready)
    (root / "out" / "验证协议.md").write_text(verification.protocol_markdown(cfg), encoding="utf-8")
    (root / "out" / "Verification_Protocol_EN.md").write_text(i18n.protocol_markdown_en(cfg), encoding="utf-8")
    (root / cfg["exchange"]["dir"] / "examples" / "event_observation.example.json").write_text(json.dumps(contracts.example_event_observation(), ensure_ascii=False, indent=2), encoding="utf-8")
    log("  承诺：" + promise["承诺"] + f"（{promise['就绪条件']}）")

    # ---- 第二、三道防线：预置与应急调度 ----
    log("预置与应急调度")
    ex = root / cfg["exchange"]["dir"] / "examples"; ex.mkdir(parents=True, exist_ok=True)
    st_dir = root / "out" / "state"
    if not (st_dir / "road_edges.parquet").exists():
        log("  抓取路网与重点单位参考数据（首次）"); refdata.build_cache(cfg)
    ref = refdata.load_cache()
    fp_files = sorted(p for p in (root / cfg["exchange"]["dir"] / "predictions").glob("*flood_points*.json") if "system-" not in p.name)
    fp_files.sort(key=lambda p: p.stat().st_mtime)
    src = str(cfg["dispatch"].get("flood_source", "auto"))
    if src in ("system", "auto") and hs_df is not None and floodpoints.available():
        rp_sys = str(cfg["dispatch"].get("system_points_rp", 10))
        matched = floodpoints.match_hotspots(cfg, es=es, cs=cs)
        if "edge_gid" in matched.columns and "nf_spill_m3_10" in es.columns:        # 匹配管段的 10 年溢流量与顶托风险挂到热点
            ei = es.set_index("gid")
            matched["nf_spill_m3_10"] = matched["edge_gid"].map(ei["nf_spill_m3_10"]); matched["nf_backwater_10"] = matched["edge_gid"].map(ei["nf_backwater_10"]).fillna(False).astype(bool)
            hs_df = hs_df.merge(matched[["hotspot_id", "edge_gid", "nf_spill_m3_10", "nf_backwater_10"]], on="hotspot_id", how="left")
            hs_df.to_parquet(root / "out" / "state" / "hotspots_enriched.parquet", index=False)
            nb = hs_df["nf_backwater_10"].fillna(False).astype(bool); ns = hs_df["nf_spill_m3_10"].fillna(0) > 0
            log(f"  热点匹配到管段 {int(hs_df['edge_gid'].notna().sum())} 个：匹配管段 10 年溢流 {int(ns.sum())} 个，顶托风险 {int(nb.sum())} 个（其中不溢流的 {int((nb & ~ns).sum())} 个）")
        fp = floodpoints.build(cfg, rp_sys, matched=matched); fpath = floodpoints.write(fp, cfg); fs = fp["stats"]
        log(f"  积水点来自本系统（客户热点 × 管网缺口 × DTM 洼地）：{fp['scenario']['name']}，{fs['n_points']} 个（管网缺口 {fs['n_network_gap']}，DTM 代理 {fs['n_dtm_proxy']}），"
            f"合计 {fs['volume_total_m3']:,.0f} m³，漫出 {fs['spill_total_m3']:,.0f} m³ → {fpath.name}；建议决策时刻 {fp['scenario']['suggested_decision_time_min']} 分钟")
    elif src in ("prediction", "auto") and fp_files:
        fp = contracts.load_flood_points(fp_files[-1])
        log(f"  积水区来自预测侧：{fp_files[-1].name}，{len(fp['points'])} 个，模型 {(fp.get('model') or {}).get('name')}，建议决策时刻 {(fp.get('scenario') or {}).get('suggested_decision_time_min')} 分钟")
    else:
        fp = simulate.simulate_flood_points(cs, ref["facilities"], cfg, cfg["standard"]["target_scenario"])
        (ex / "flood_points.simulated.json").write_text(json.dumps(fp, ensure_ascii=False), encoding="utf-8")
        log(f"  没有世界模型输出，用模拟积水区 {len(fp['points'])} 个（隧道 {sum(1 for p in fp['points'] if p['land_use']=='tunnel')}），标记 simulated")
    fleet_file = root / cfg["exchange"]["dir"] / "fleet.json"
    if fleet_file.exists():
        fleet = contracts.load_fleet(fleet_file); log(f"  泵队台账：{len(fleet['units'])} 台")
    elif hs_df is not None and (hs_df["region"] == "ADM").any():
        fleet = hsm.fleet_from_plan_a(hs_df, ref["facilities"], cfg)
        (root / cfg["exchange"]["dir"] / "fleet.plan_a.json").write_text(json.dumps(fleet, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  没有泵队台账，用客户预案 A 的分片区泵数生成：{len(fleet['units'])} 个单元（12寸 {sum(1 for u in fleet['units'] if '12in' in u['model'])}，8寸 {sum(1 for u in fleet['units'] if '8in' in u['model'])}，6寸组 {sum(1 for u in fleet['units'] if '6in' in u['model'])}）、{len(fleet['depots'])} 个片区驻地")
    else:
        fleet = simulate.simulate_fleet(ref["facilities"], cfg)
        (ex / "fleet.simulated.json").write_text(json.dumps(fleet, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  没有泵队台账，用模拟 {len(fleet['units'])} 台、{len(fleet['depots'])} 个驻地（民防站真实位置）")
    road = travel.RoadNetwork(ref["edges"], ref["vertices"])
    dres = dispatch.run_dispatch(fp, fleet, ref["facilities"], cs, road, cfg, hotspots=hs_df)
    if hs_df is not None:
        (root / "console" / "data").mkdir(parents=True, exist_ok=True)
        (root / "console" / "data" / "hotspots.geojson").write_text(json.dumps(hsm.to_geojson(hs_df), ensure_ascii=False), encoding="utf-8")
        hstat = dres.get("hotspot_stats") or {}
        g = hstat.get("gwm_vs_hotspots") or {}
        rep = hsm.discrepancy_report(hs_df, cs)
        if len(rep):
            rep.to_csv(root / "out" / "客户热点核对_无管网但有终点.csv", index=False, encoding="utf-8-sig"); log(f"  客户标无管网但 500 m 内有终点的热点 {len(rep)} 个 → out/客户热点核对_无管网但有终点.csv")
        log(f"  客户热点对照：ADM 500 m 内有终点 {(hstat.get('adm_vs_network') or {}).get('within_500m_of_terminal')}；" + (f"世界模型命中 ≥0.15 m {g.get('hit_015')}/{g.get('inside_grid')}（POD {g.get('pod_015')}），≥0.3 m {g.get('hit_030')}" if g else "积水点来自本系统，不算世界模型命中率"))
    dres["fleet"] = fleet
    dpay = export.dispatch_payload(dres, fp)
    export.write_dispatch_files(root / "out", root / "console", dpay)
    log(f"  分诊 {dres['triage']}；求解器 {dres['kpis']['solver']}；贪心 {dres['kpis']['greedy']}")
    (ex / "flood_points.example.json").write_text(json.dumps(contracts.example_flood_points(), ensure_ascii=False, indent=2), encoding="utf-8")
    (ex / "fleet.example.json").write_text(json.dumps(contracts.example_fleet(), ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  干预集合 {len(wi['sets'])}，外部评估覆盖 {wi['n_external']}，入选 {int(plan['selected'].sum()) if len(plan) else 0} 条，"
        f"预算内成本 {float(plan[plan['selected']]['cost_aed'].sum()) if len(plan) else 0:,.0f} AED，请求 {wi['request'].request_id if wi['request'] else '无'}")
    ex = root / cfg["exchange"]["dir"] / "examples"; ex.mkdir(parents=True, exist_ok=True)
    (ex / "prediction_state.example.json").write_text(json.dumps(contracts.example_prediction_state(), ensure_ascii=False, indent=2), encoding="utf-8")
    if wi["request"]:
        (ex / "whatif_result.example.json").write_text(json.dumps(contracts.example_whatif_result(wi["request"]), ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 汇总 ----
    main = list(scenarios)[0]
    act = es[es["is_active"]]
    summary = {
        "生成时间": time.strftime("%Y-%m-%d %H:%M"),
        "管段数": int(len(es)), "在用管长km": round(float(act["len_m"].sum()) / 1000, 1),
        "终点数": int(len(cs)), "终点类型": cs["terminal_kind"].value_counts().to_dict(),
        "能到出水口或泵站的管长份额": round(float(act[act["terminal_kind"].isin(["出水口", "泵站"])]["len_m"].sum() / act["len_m"].sum()), 3),
        "超能力管长km": {k: round(float(act[act[f"ratio_{k}"] > 1]["len_m"].sum()) / 1000, 1) for k in scenarios},
        "超能力管长份额": {k: round(float(act[act[f"ratio_{k}"] > 1]["len_m"].sum() / act["len_m"].sum()), 3) for k in scenarios},
        "无出路汇水区蓄渗缺口m3": round(float(cs["storage_deficit_m3"].sum()), 0),
        "顶托风险出水口数": int((cs["tide_risk_m"] > 0).sum()), "出水口总数(图上)": int(cs["terminal_kind"].eq("出水口").sum()),
        "坡度标记": es["slope_flag"].value_counts().to_dict(), "方向来源": es["orient"].value_counts().to_dict(), "面积依据": es["area_basis"].value_counts().to_dict(),
        "打断环移除边数": len(removed), "建议数": int(len(props)),
        "建议按类型": props["kind"].value_counts().to_dict() if len(props) else {},
        "建议按时间窗": props["when_class"].value_counts().to_dict() if len(props) else {},
        "预测基线": pred_summary or "无（exchange/predictions 为空）",
        "全网达标扩管成本上限(静态,占位单价)": standard,
        "按标准达标的分期": {k: v for k, v in splan.items() if k != "projects"},
        "法定标准": code,
        "客户热点": (dres.get("hotspot_stats") or None),
        "法定标准达标的分期": {k: v for k, v in splan_code.items() if k != "projects"},
        "scenario_rp": cfg["model"].get("scenario_return_period", {}),
        "预算内方案实施后剩余": residual["summary"],
        "就绪度": ready, "承诺": promise,
        "全网溢流_10年": summary_nf, "三道防线": {"第一道 基础设施": {"建议": int(len(props)), "达标年限": splan.get("years")},
                     "第二道 预报后预置": {a: {"站点": len(v.get("sites", [])), "覆盖优先级份额": v.get("covered_priority_share")} for a, v in dres["preposition"].items()},
                     "第三道 应急调度": {"情景": dres["scenario"]["name"], "模拟": dres["simulated"], "积水点": int(len(dres["points"])), "可派": int(dres["points"]["dispatchable"].sum()),
                                      "求解器": dres["kpis"]["solver"], "贪心": dres["kpis"]["greedy"], "求解毫秒": [w.get("ms") for w in dres["waves"]]}}, "已对账事件": [{"event_id": c["event_id"], "pod": c["pod"], "far": c["far"], "rmse": c["depth_rmse_m"], "passed": c["passed"]} for c in comparisons],
        "what-if": {"干预集合": len(wi["sets"]), "外部评估覆盖": wi["n_external"], "入选": int(plan["selected"].sum()) if len(plan) else 0,
                    "入选成本AED": round(float(plan[plan["selected"]]["cost_aed"].sum()), 0) if len(plan) else 0, "预算AED": cfg["whatif"]["budget_aed"],
                    "请求文件": (wi["request"].request_id + ".json") if wi["request"] else None, "代理评估基线KPI": wi["surrogate_baseline"]},
        "承载雨强mm/h分位(按长度加权)": {str(q): round(float(_wq(act["i_cap_mmh"].values.astype(float), act["len_m"].values.astype(float), q)), 1) for q in (0.1, 0.25, 0.5, 0.75, 0.9)},
        "疑似漏登出水口/泵站的断头数": int(cs["suspect_missing_outlet"].sum()),
        "疑似漏登断头承接的管长km": round(float(cs[cs["suspect_missing_outlet"]]["len_km"].sum()), 1),
        "高潮易涝出水口数(净空<阈值)": int((cs["tide_clearance_m"] < cfg["decision"]["tide_clearance_min_m"]).sum()),
        "scenario_en": {k: i18n.scenario_en(k) for k in scenarios},
        "假设": {"设计雨强mm/h": scenarios,
                 "径流系数": ({"按地表覆盖加权": csum["runoff_c_table"], "中位": csum["c_median"], "其余进水口": cfg["model"]["runoff_c"]} if csum else cfg["model"]["runoff_c"]),
                 "汇水面积来源": ("5 m DTM 子汇水区（10 m 网格、D8 流向、按进水口出水点划分，地块内自排不计入；公共面积为 0 的进水口按 100 m² 下限计）" if area_mode == "dtm" else "进水口 × 600 m² 与管长 × 20 m 取大者（估算）"),
                 "子汇水区": csum, "每进水口汇水m2": cfg["model"]["inlet_area_m2"],
                 "道路汇水宽m": cfg["model"]["road_width_m"], "标高基准": f"MSL={cfg['model']['datum_offset_m']}" + ("（已用 5 m DTM 实测校核）" if terrain.available(cfg) else ""), "地形": tsum, "高潮位m": cfg["model"]["tide_high_m"],
                 "设计雨深mm": cfg["model"]["design_depth_mm"], "IDF来源": cfg.get("idf", {}).get("source")},
    }
    log("汇总：" + json.dumps({k: summary[k] for k in ("在用管长km", "能到出水口或泵站的管长份额", "超能力管长份额", "承载雨强mm/h分位(按长度加权)", "高潮易涝出水口数(净空<阈值)", "疑似漏登出水口/泵站的断头数", "建议数")}, ensure_ascii=False))

    # ---- 输出 ----
    log("片区几何")
    zone_geoms = export.zone_hulls(es, vertices, districts)
    top_gids = act.sort_values(f"ratio_{main}", ascending=False).head(a.top_edges)["gid"].tolist()
    res_gids = residual["over_edges"].sort_values(f"ratio_{cfg['standard']['target_scenario']}", ascending=False).head(a.top_edges)["gid"].tolist()
    with db.connect_source(cfg, "pipe") as pc:
        edge_geoms = extract.load_edge_geoms(pc, cfg, sorted(set(top_gids) | set(res_gids)))
    export.write_files(root / "out", root / "console", scenarios, es, cs, zs, props, zone_geoms, edge_geoms, summary, plan, sched,
                       residual=residual, res_gids=res_gids, splan=splan)
    log(f"文件已写到 {root / 'out'} 与 console/data.js")
    try:                                                                # 状态清单：服务装载时据它说明「这批数据从哪来、多少行」
        from . import artifactcache as _ac
        mp = _ac.write_state_manifest(cfg, {"edges": int(len(es)), "active_edges": int(len(act)), "catchments": int(len(cs)), "zones": int(len(zs)), "proposals": len(props)}, root, note=a.note)
        log(f"状态清单 {mp.relative_to(root).as_posix()}")
    except Exception as ex:
        log(f"状态清单未写：{str(ex)[:120]}")
    if not a.no_db:
        with db.connect_output(cfg) as oc:
            run_id = export.write_postgis(oc, cfg, scenarios, es, cs, zs, props, zone_geoms, note=a.note, plan=plan, sched=sched)
        log(f"PostGIS 写入完成 run_id={run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
