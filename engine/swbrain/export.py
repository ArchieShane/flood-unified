"""输出：本地 PostGIS（sw_brain.swbrain.*）、GeoJSON、控制台 data.js。"""
from __future__ import annotations
import json
import math
from pathlib import Path
import numpy as np
from . import i18n
import pandas as pd
from shapely.geometry import MultiPoint, mapping

DDL = """
create schema if not exists {s};
create table if not exists {s}.run (
  run_id serial primary key, started_at timestamptz default now(), finished_at timestamptz,
  scenarios jsonb, params jsonb, n_edges int, n_catchments int, n_zones int, n_proposals int, note text);
create table if not exists {s}.edge_state (
  run_id int references {s}.run(run_id) on delete cascade, gid bigint, unitid text, zone_name text, terminal bigint, terminal_kind text,
  pipe_diameter float8, pipe_material text, slope float8, slope_flag text, orient text, n_manning float8, q_full float8,
  area_m2 float8, area_basis text, n_inlets_up float8, ratios jsonb, is_active bool, primary key (run_id, gid));
create table if not exists {s}.catchment_state (
  run_id int references {s}.run(run_id) on delete cascade, terminal bigint, terminal_unitid text, terminal_kind text, zone_name text,
  n_edges int, len_km float8, area_m2 float8, n_inlets float8, storage_m3 float8, runoff_m3 float8, storage_deficit_m3 float8,
  n_pumps int, invert_rel float8, tide_risk_m float8, outfall_type text, cover_p10_rel float8, tide_clearance_m float8, i_cap_p50 float8,
  suspect_missing_outlet bool, needs_storage bool, metrics jsonb, geom geometry(Point, {srid}), primary key (run_id, terminal));
create table if not exists {s}.zone_state (
  run_id int references {s}.run(run_id) on delete cascade, zone_name text, n_edges int, len_km float8, no_outlet_share float8,
  data_issue_share float8, storage_deficit_m3 float8, n_tide_outfalls int, blockage_per_100km float8, risk_score float8,
  i_cap_p50 float8, rankable bool, n_suspect_outlets int, metrics jsonb, geom geometry(MultiPolygon, 4326));
create table if not exists {s}.proposal (
  run_id int references {s}.run(run_id) on delete cascade, pid int, rank int, kind text, title text, zone_name text, gid bigint, terminal bigint,
  when_class text, "when" text, when_reason text, action jsonb, before jsonb, after jsonb, benefit text, priority float8, confidence float8,
  evidence jsonb, caveats text, status text default '建议', decided_by text, decided_at timestamptz, decision_note text,
  geom geometry(Point, 4326), primary key (run_id, pid));
create table if not exists {s}.plan (
  run_id int references {s}.run(run_id) on delete cascade, seq int, set_id text, pid int, kind text, when_class text, "when" text, zone_name text, title text,
  cost_aed float8, benefit float8, benefit_per_maed float8, eval_source text, depends_on text, interventions jsonb, primary key (run_id, seq));
"""


def _j(o):
    def conv(x):
        if isinstance(x, (np.integer,)): return int(x)
        if isinstance(x, (np.floating,)): return None if math.isnan(float(x)) else float(x)
        if isinstance(x, float) and math.isnan(x): return None
        if isinstance(x, (pd.Timestamp,)): return x.isoformat()
        if isinstance(x, dict): return {k: conv(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)): return [conv(v) for v in x]
        return x
    return json.dumps(conv(o), ensure_ascii=False)


def _f(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) or pd.isna(x) else float(x)


def _i(x):
    v = _f(x)
    return None if v is None else int(v)


def write_postgis(conn, cfg, scenarios, es, cs, zs, props, zone_geoms: dict, note: str = "", plan=None, sched=None) -> int:
    s, srid = cfg["output"]["schema"], cfg["output"]["srid"]
    with conn.cursor() as cur:
        cur.execute(DDL.format(s=s, srid=srid))
        cur.execute(f"insert into {s}.run (scenarios, params, n_edges, n_catchments, n_zones, n_proposals, note) values (%s,%s,%s,%s,%s,%s,%s) returning run_id",
                    (_j(scenarios), _j(cfg["model"] | {"decision": cfg["decision"]}), len(es), len(cs), len(zs), len(props), note))
        run_id = cur.fetchone()[0]
        ratio_cols = [c for c in es.columns if c.startswith("ratio_")]
        with cur.copy(f"copy {s}.edge_state (run_id,gid,unitid,zone_name,terminal,terminal_kind,pipe_diameter,pipe_material,slope,slope_flag,orient,n_manning,q_full,area_m2,area_basis,n_inlets_up,ratios,is_active) from stdin") as cp:
            for r in es.itertuples(index=False):
                d = r._asdict()
                cp.write_row((run_id, int(d["gid"]), d["unitid"], d["zone_name"], _i(d["terminal"]), d.get("terminal_kind"), _f(d["pipe_diameter"]), d["pipe_material"],
                              _f(d["slope"]), d["slope_flag"], d["orient"], _f(d["n_manning"]), _f(d["q_full"]), _f(d["area_m2"]), d["area_basis"], _f(d["n_inlets_up"]),
                              _j({c: _f(d[c]) for c in ratio_cols} | {"i_cap_mmh": _f(d["i_cap_mmh"]), "design_class": d.get("design_class"), "design_rp_yr": _i(d.get("design_rp_yr")),
                                  "tc_min": _f(d.get("tc_min")), "design_i_mmh": _f(d.get("design_i_mmh")), "compliant": (None if d.get("compliant") is None or pd.isna(d.get("compliant")) else bool(d.get("compliant")))}), bool(d["is_active"])))
        metric_cols = [c for c in cs.columns if c.startswith(("q_peak_", "max_ratio_", "over_km_", "over_share_"))]
        for r in cs.itertuples(index=False):
            d = r._asdict()
            cur.execute(f"""insert into {s}.catchment_state values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, case when %s::float8 is null then null else st_setsrid(st_makepoint(%s::float8,%s::float8),{srid}) end)""",
                        (run_id, int(d["terminal"]), d["terminal_unitid"], d["terminal_kind"], d["zone_name"], int(d["n_edges"]), _f(d["len_km"]), _f(d["area_m2"]), _f(d["n_inlets"]),
                         _f(d["storage_m3"]), _f(d["runoff_m3"]), _f(d["storage_deficit_m3"]), int(d["n_pumps"]), _f(d.get("invert_rel")), _f(d.get("tide_risk_m")), d.get("outfall_type"),
                         _f(d.get("cover_p10_rel")), _f(d.get("tide_clearance_m")), _f(d.get("i_cap_p50")), bool(d.get("suspect_missing_outlet")), bool(d.get("needs_storage")),
                         _j({c: _f(d[c]) for c in metric_cols}), _f(d["x"]), _f(d["x"]), _f(d["y"])))
        zm = [c for c in zs.columns if c.startswith(("over_km_", "over_share_"))]
        for r in zs.itertuples(index=False):
            d = r._asdict()
            gj = zone_geoms.get(d["zone_name"])
            cur.execute(f"""insert into {s}.zone_state values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, case when %s::text is null then null else st_multi(st_setsrid(st_geomfromgeojson(%s::text),4326)) end)""",
                        (run_id, d["zone_name"], int(d["n_edges"]), _f(d["len_km"]), _f(d["no_outlet_share"]), _f(d["data_issue_share"]), _f(d["storage_deficit_m3"]),
                         int(d["n_tide_outfalls"]), _f(d.get("blockage_per_100km")), _f(d["risk_score"]), _f(d.get("i_cap_p50")), bool(d.get("rankable")), int(d.get("n_suspect_outlets") or 0),
                         _j({c: _f(d[c]) for c in zm} | {"n_inlets": _f(d["n_inlets"])}), gj, gj))
        for r in props.itertuples(index=False):
            d = r._asdict()
            cur.execute(f"""insert into {s}.proposal (run_id,pid,rank,kind,title,zone_name,gid,terminal,when_class,"when",when_reason,action,before,after,benefit,priority,confidence,evidence,caveats,status,geom)
                            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, case when %s::float8 is null then null else st_setsrid(st_makepoint(%s::float8,%s::float8),4326) end)""",
                        (run_id, int(d["pid"]), int(d["rank"]), d["kind"], d["title"], d["zone_name"], _i(d["gid"]), _i(d["terminal"]), d["when_class"], d["when"], d["when_reason"],
                         _j(d["action"]), _j(d["before"]), _j(d["after"]), d["benefit"], _f(d["priority"]), _f(d["confidence"]), _j(d["evidence"]), d["caveats"], d["status"],
                         _f(d["lon"]), _f(d["lon"]), _f(d["lat"])))
        if sched is not None and len(sched):
            iv_by_set = {r["set_id"]: r["interventions"] for _, r in plan.iterrows()} if plan is not None and len(plan) else {}
            for r in sched.itertuples(index=False):
                d = r._asdict()
                cur.execute(f"""insert into {s}.plan values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (run_id, int(d["seq"]), d["set_id"], _i(d["pid"]), d["kind"], d["when_class"], d["when"], d["zone_name"], d["title"], _f(d["cost_aed"]),
                             _f(d["benefit"]), _f(d["benefit_per_maed"]), d["eval_source"], d["depends_on"], _j(iv_by_set.get(d["set_id"], []))))
        cur.execute(f"update {s}.run set finished_at = now() where run_id = %s", (run_id,))
    conn.commit()
    return run_id


def zone_hulls(es: pd.DataFrame, vertices: pd.DataFrame, districts: pd.DataFrame | None) -> dict:
    """片区几何：能按名字对上 udm_district 的用区界，否则用节点凸包。返回 {zone: geojson_str}。"""
    import re, difflib
    norm = lambda s: re.sub(r"[^a-z0-9]", "", str(s).lower()) if isinstance(s, str) else ""
    out = {}
    dmap = {}
    if districts is not None and len(districts):
        dmap = {norm(n): g for n, g in zip(districts["nameenglish"], districts["geojson"])}
    vt = vertices.set_index("id")
    for zn, grp in es.groupby("zone_name", dropna=False):
        key = norm(zn)
        hit = difflib.get_close_matches(key, list(dmap), n=1, cutoff=0.85) if key else []
        if hit:
            out[zn] = dmap[hit[0]]
            continue
        pts = vt.loc[vt.index.intersection(pd.unique(np.concatenate([grp["u"].values, grp["v"].values]))), ["lon", "lat"]].dropna()
        if len(pts) >= 3:
            hull = MultiPoint(list(zip(pts["lon"], pts["lat"]))).convex_hull
            if hull.geom_type == "Polygon":
                out[zn] = json.dumps(mapping(hull))
    return out


def write_files(out_dir: Path, console_dir: Path, scenarios, es, cs, zs, props, zone_geoms: dict, edge_geoms: pd.DataFrame, summary: dict, plan=None, sched=None,
                residual=None, res_gids=None, splan=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    main = list(scenarios)[0]

    def fc(features):
        return {"type": "FeatureCollection", "features": features}

    pf = []
    for r in props.itertuples(index=False):
        d = r._asdict()
        geom = None if pd.isna(d["lon"]) else {"type": "Point", "coordinates": [float(d["lon"]), float(d["lat"])]}
        pf.append({"type": "Feature", "geometry": geom, "properties": json.loads(_j({k: v for k, v in d.items() if k not in ("lon", "lat", "x", "y")}))})
    zf = []
    for r in zs.itertuples(index=False):
        d = r._asdict()
        gj = zone_geoms.get(d["zone_name"])
        zf.append({"type": "Feature", "geometry": json.loads(gj) if gj else None,
                   "properties": json.loads(_j({k: v for k, v in d.items() if k != "blockage_key"}))})
    cf = []
    for r in cs.itertuples(index=False):
        d = r._asdict()
        if pd.isna(d["lon"]):
            continue
        keep = ["terminal", "terminal_unitid", "terminal_kind", "zone_name", "n_edges", "len_km", "area_m2", "n_inlets", "storage_m3", "runoff_m3",
                "storage_deficit_m3", "n_pumps", "invert_rel", "tide_risk_m", "outfall_type", "cover_p10_rel", "tide_clearance_m", "i_cap_p50",
                "suspect_missing_outlet", "needs_storage", f"max_ratio_{main}", f"over_share_{main}", f"q_peak_{main}"]
        cf.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(d["lon"]), float(d["lat"])]},
                   "properties": json.loads(_j({k: d.get(k) for k in keep}))})
    ef = []
    if len(edge_geoms):
        eg = es.merge(edge_geoms, on="gid", how="inner")
        for r in eg.itertuples(index=False):
            d = r._asdict()
            ef.append({"type": "Feature", "geometry": json.loads(d["geojson"]),
                       "properties": json.loads(_j({"gid": d["gid"], "unitid": d["unitid"], "zone_name": d["zone_name"], "pipe_diameter": d["pipe_diameter"],
                                                    "slope": d["slope"], "slope_flag": d["slope_flag"], "q_full": d["q_full"], "area_m2": d["area_m2"], "area_basis": d.get("area_basis"), "runoff_c": d.get("runoff_c"), "i_cap_mmh": d["i_cap_mmh"], "n_inlets_up": d["n_inlets_up"],
                                                    **{c: d[c] for c in es.columns if c.startswith("ratio_")}}))})
    sched_rows = json.loads(_j(sched.to_dict(orient="records"))) if sched is not None and len(sched) else []
    plan_rows = json.loads(_j(plan.drop(columns=["interventions"]).to_dict(orient="records"))) if plan is not None and len(plan) else []
    if plan is not None and len(plan):
        plan.to_csv(out_dir / "plan.csv", index=False, encoding="utf-8-sig")
    if sched is not None and len(sched):
        sched.to_csv(out_dir / "schedule.csv", index=False, encoding="utf-8-sig")
    # 剩余风险：预算内方案实施后目标情景下仍超载的管段 + 仍高潮暴露的出水口 + 仍疑似漏登的终点
    rf = []
    if residual is not None:
        T = residual["summary"]["目标情景"]
        if res_gids and len(edge_geoms):
            eg = residual["over_edges"][residual["over_edges"]["gid"].isin(res_gids)].merge(edge_geoms, on="gid", how="inner")
            for r in eg.itertuples(index=False):
                d = r._asdict()
                rf.append({"type": "Feature", "geometry": json.loads(d["geojson"]),
                           "properties": json.loads(_j({"kind": "仍超载管段", "kind_en": "Still overloaded pipe", "gid": d["gid"], "unitid": d["unitid"], "zone_name": d["zone_name"], "pipe_diameter": d["pipe_diameter"],
                                                        "ratio": d[f"ratio_{T}"], "i_cap_mmh": d["i_cap_mmh"]}))})
        for r in residual["tide_catchments"].itertuples(index=False):
            d = r._asdict()
            if pd.notna(d["lon"]):
                rf.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(d["lon"]), float(d["lat"])]},
                           "properties": json.loads(_j({"kind": "仍高潮暴露出水口", "kind_en": "Still tide-exposed outfall", "unitid": d["terminal_unitid"], "zone_name": d["zone_name"], "tide_clearance_m": d["tide_clearance_m"], "n_inlets": d["n_inlets"]}))})
        for r in residual["suspect_catchments"].itertuples(index=False):
            d = r._asdict()
            if pd.notna(d["lon"]):
                rf.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(d["lon"]), float(d["lat"])]},
                           "properties": json.loads(_j({"kind": "仍疑似漏登终点", "kind_en": "Still suspected unregistered terminal", "unitid": d["terminal_unitid"], "zone_name": d["zone_name"], "len_km": d["len_km"], "area_ha": d["area_m2"] / 1e4}))})
        (out_dir / "residual.geojson").write_text(json.dumps(fc(rf), ensure_ascii=False), encoding="utf-8")
    if splan is not None and "projects" in splan:
        splan["projects"].to_csv(out_dir / "standard_plan_projects.csv", index=False, encoding="utf-8-sig")
    data = {"summary": summary, "scenarios": scenarios, "proposals": fc(pf), "zones": fc(zf), "catchments": fc(cf), "bottlenecks": fc(ef),
            "schedule": sched_rows, "plan": plan_rows, "residual": fc(rf),
            "standard_plan": {k: v for k, v in (splan or {}).items() if k != "projects"}}
    for k in ("proposals", "zones", "catchments", "bottlenecks"):
        (out_dir / f"{k}.geojson").write_text(json.dumps(data[k], ensure_ascii=False), encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    props.drop(columns=[c for c in ("x", "y") if c in props.columns]).to_csv(out_dir / "proposals.csv", index=False, encoding="utf-8-sig")
    zs.to_csv(out_dir / "zone_state.csv", index=False, encoding="utf-8-sig")
    # 控制台按页签懒加载：一个文件一个页签，不再塞一个 4.8MB 的 data.js
    dd = console_dir / "data"; dd.mkdir(parents=True, exist_ok=True)
    (dd / "summary.json").write_text(json.dumps({"summary": summary, "scenarios": scenarios, "standard_plan": data["standard_plan"]}, ensure_ascii=False), encoding="utf-8")
    for k in ("proposals", "zones", "catchments", "bottlenecks", "residual"):
        (dd / f"{k}.geojson").write_text(json.dumps(data[k], ensure_ascii=False), encoding="utf-8")
    (dd / "schedule.json").write_text(json.dumps({"schedule": sched_rows, "plan": plan_rows}, ensure_ascii=False), encoding="utf-8")
    old = console_dir / "data.js"
    if old.exists():
        old.unlink()
    # 在线服务用的中间状态（parquet）：拖雨强、点 what-if 都从这里当场算
    st = out_dir / "state"; st.mkdir(parents=True, exist_ok=True)
    keep = ["gid", "unitid", "u", "v", "zone_name", "len_m", "pipe_diameter", "pipe_material", "slope", "slope_flag", "orient", "n_manning", "q_full", "area_m2", "area_basis", "runoff_c",
            "n_inlets_up", "terminal", "terminal_kind", "i_cap_mmh", "is_active", "diameter_flag", "dir_conflict", "pipe_subtype", "status", "invert_valid",
            "design_class", "design_rp_yr", "check_rp_yr", "tc_multiplier", "tc_min", "design_duration_min", "design_i_mmh", "q_design", "ratio_design", "compliant", "check_i_mmh", "ratio_check", "rational_valid",
            "nf_spill_m3_10", "nf_backwater_10"] + [c for c in es.columns if c.startswith(("q_", "ratio_"))]
    keep = list(dict.fromkeys(c for c in keep if c in es.columns))   # 去重、保序
    es_out = es[keep].copy()
    for c in es_out.columns:            # parquet 不接受混合类型的 object 列，统一成字符串或数值
        if es_out[c].dtype == object and c not in ("unitid", "zone_name", "pipe_material", "slope_flag", "orient", "area_basis", "terminal_kind", "diameter_flag", "pipe_subtype", "status"):
            es_out[c] = pd.to_numeric(es_out[c], errors="coerce")
        elif es_out[c].dtype == object:
            es_out[c] = es_out[c].astype("string")
    es_out.to_parquet(st / "es.parquet", index=False)
    def _pq(df):
        d = df.copy()
        for c in d.columns:
            if d[c].dtype == object:
                try:
                    d[c] = pd.to_numeric(d[c])
                except Exception:
                    d[c] = d[c].astype("string")
        return d
    _pq(cs).to_parquet(st / "cs.parquet", index=False)
    _pq(zs.drop(columns=[c for c in ("blockage_key",) if c in zs.columns])).to_parquet(st / "zs.parquet", index=False)
    (st / "props.json").write_text(_j(props.drop(columns=[c for c in ("x", "y") if c in props.columns]).to_dict(orient="records")), encoding="utf-8")


# ---------------------------------------------------------------- 第二、三道防线：预置与应急调度的输出
def dispatch_payload(r: dict, fp: dict) -> dict:
    """把 run_dispatch 的结果整理成页面与接口共用的 JSON：积水区 GeoJSON、调度单、贪心基线、指标、预置、驻地。"""
    pts = r["points"]
    geoms = {p["fp_id"]: p.get("geometry") for p in fp["points"]}
    note_en_by_id = {p["fp_id"]: p.get("note_en") for p in fp["points"]}
    keep = ["fp_id", "lon", "lat", "area_m2", "max_depth_m", "volume_m3", "duration_min", "land_use", "note", "priority", "top_kind", "facilities", "target_drain_min",
            "volume_now_m3", "inflow_now_m3h", "required_rate_m3h", "discharge_dist_m", "discharge_to", "relay_needed", "triage", "dispatchable", "client_hotspot_id", "client_criticality", "level", "level_priority", "location", "region", "source"]
    keep = [c for c in keep if c in pts.columns]
    served = set(r["plan"]["fp_id"]) if len(r["plan"]) else set()
    feats = []
    for d in pts[keep].to_dict(orient="records"):
        d["served"] = d["fp_id"] in served
        d["note_en"] = note_en_by_id.get(d["fp_id"]) or i18n.note_en(d.get("note", ""))
        d["facilities"] = list(d["facilities"]) if isinstance(d["facilities"], (list, tuple, np.ndarray)) else d["facilities"]
        g = geoms.get(d["fp_id"]) or {"type": "Point", "coordinates": [d["lon"], d["lat"]]}
        feats.append({"type": "Feature", "geometry": g, "properties": json.loads(_j(d))})
    plan_rows = json.loads(_j(r["plan"].to_dict(orient="records"))) if len(r["plan"]) else []
    greedy_rows = json.loads(_j(r["plan_greedy"].to_dict(orient="records"))) if len(r["plan_greedy"]) else []
    lines = [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[p["from_lon"], p["from_lat"]], [p["lon"], p["lat"]]]},
              "properties": {k: p[k] for k in ("wave", "unit_id", "model", "fp_id", "travel_min", "arrive_min", "done_min", "within_target", "priority")}} for p in plan_rows]
    depots = json.loads(_j(r["depots"].to_dict(orient="records")))
    units_by_depot = {d: g["model"].value_counts().to_dict() for d, g in r["units"].groupby("from")} if len(r["units"]) else {}
    for d in depots:
        d["units"] = units_by_depot.get(d["depot_id"], {})
    return {"scenario": r["scenario"], "flood_source": r.get("flood_source"), "levels": r.get("levels"), "simulated": r["simulated"], "fleet_simulated": r["fleet_simulated"], "fleet_source": r.get("fleet_source"),
            "hotspot_stats": r.get("hotspot_stats"), "decision_time_min": r["decision_time_min"], "replay_summary": r.get("replay_summary"), "plans": r.get("plans"), "timeline": r.get("timeline"), "cost": r.get("cost"), "tankers": r.get("tankers"),
            "travel_method": r["travel_method"], "travel_method_en": r.get("travel_method_en", r["travel_method"]), "travel_ms": r["travel_ms"],
            "v2": (json.loads(_j(r["v2"])) if r.get("v2") is not None else None),
            "triage": r["triage"], "triage_en": r.get("triage_en", {}), "kpis": json.loads(_j(r["kpis"])), "waves": json.loads(_j(r["waves"])),
            "points": {"type": "FeatureCollection", "features": feats}, "plan": plan_rows, "plan_greedy": greedy_rows,
            "routes": {"type": "FeatureCollection", "features": lines}, "depots": depots, "preposition": json.loads(_j(r["preposition"])),
            "fleet_models": r["fleet"]["models"] if "fleet" in r else None, "n_units": int(len(r["units"]))}


def write_dispatch_files(out_dir: Path, console_dir: Path, payload: dict):
    dd = console_dir / "data"; dd.mkdir(parents=True, exist_ok=True)
    (dd / "dispatch.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    od = out_dir / "dispatch"; od.mkdir(parents=True, exist_ok=True)
    (od / "flood_points.geojson").write_text(json.dumps(payload["points"], ensure_ascii=False), encoding="utf-8")
    (od / "routes.geojson").write_text(json.dumps(payload["routes"], ensure_ascii=False), encoding="utf-8")
    pd.DataFrame(payload["plan"]).to_csv(od / "dispatch_plan.csv", index=False, encoding="utf-8-sig")
    (od / "preposition.json").write_text(json.dumps(payload["preposition"], ensure_ascii=False, indent=2), encoding="utf-8")
    (od / "kpis.json").write_text(json.dumps({"kpis": payload["kpis"], "waves": payload["waves"], "triage": payload["triage"]}, ensure_ascii=False, indent=2), encoding="utf-8")
