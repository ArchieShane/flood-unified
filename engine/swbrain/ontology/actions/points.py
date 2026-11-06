"""积水点、片区、管段、退水与观测——本体动作与中英小结。由 Runtime 多继承，方法体与拆分前逐字相同（ROOT 改成 self.root）。"""
from __future__ import annotations
import json
import time
import numpy as np
from .. import schema as S
from ...gwmcompare import _xy
from ..common import ActionError, _f, _fmt


class PointsActions:
    def _act_count_points(self, P):
        key = P["scenario"]; fps = list(self._fp_index().values()); states = self.load_states(key)
        by_region = {}
        for r in fps:
            by_region[r.get("region")] = by_region.get(r.get("region"), 0) + 1
        by_level, by_region_state = {}, {}
        for st in states.values():
            by_level[st.get("level")] = by_level.get(st.get("level"), 0) + 1
            reg = str(st["fp_id"]).split("-")[0]; by_region_state[reg] = by_region_state.get(reg, 0) + 1
        served = sum(1 for st in states.values() if st.get("served")); disp = sum(1 for st in states.values() if st.get("dispatchable"))
        wm = sum(1 for st in states.values() if st.get("world_model_confirmed")); tank = sum(1 for st in states.values() if st.get("tanker_served"))
        gw = None
        try:
            g = self.b.gwm_points(key)
            if g and not g.get("error"):
                st_ = g.get("stats") or {}; gw = {"run_id": g.get("run_id"), "matched_by": g.get("matched_by"), "cores": (st_.get("gwm") or {}).get("n"), "agreement": (st_.get("gwm") or {}).get("agreement"), "hotspots_confirmed": (st_.get("hotspots") or {}).get("covered"), "hotspots_in_grid": (st_.get("hotspots") or {}).get("in_grid"), "hotspots_outside_grid": (st_.get("hotspots") or {}).get("outside_grid")}
        except Exception:
            gw = None
        no_state = [r for r in fps if r["fp_id"] not in states]
        return {"scenario": self.scenario_object(key), "client_total": len(fps), "client_by_region": by_region, "with_state": len(states), "with_state_by_region": by_region_state, "by_level": by_level, "served": served, "dispatchable": disp, "tanker_served": tank,
                "world_model_confirmed": wm, "no_state": len(no_state), "no_state_reasons": {"no_network_no_reach": sum(1 for r in no_state if not r.get("network_available") and r.get("edge_gid") is None), "other": sum(1 for r in no_state if r.get("network_available") or r.get("edge_gid") is not None)},
                "world_model": gw, "_ui": {"rp": key, "tab": "line3", "layers": ["flood", "hs"], "fit": "all"}}

    def _sum_count_points(self, r, P, lang):
        s = r["scenario"]; lab = s.get("label") if lang == "zh" else (s.get("label_en") or s.get("label")); bl = r["by_level"]; gw = r.get("world_model") or {}
        reg = "、".join(f"{k} {v}" for k, v in sorted(r["client_by_region"].items(), key=lambda kv: -kv[1])) if lang == "zh" else ", ".join(f"{k} {v}" for k, v in sorted(r["client_by_region"].items(), key=lambda kv: -kv[1]))
        if lang == "zh":
            t = (f"客户清单共 {r['client_total']} 个积水点（{reg}）。{lab}下本系统算出积水过程的 {r['with_state']} 个（III 级 {bl.get('III', 0)} / II 级 {bl.get('II', 0)} / I 级 {bl.get('I', 0)}），其中可派泵 {r['dispatchable']}、已派 {r['served']}、罐车覆盖 {r['tanker_served']}、世界模型印证 {r['world_model_confirmed']}；"
                 f"另 {r['no_state']} 个没算出积水过程（{r['no_state_reasons']['no_network_no_reach']} 个无管网也没匹配到管段，其余体积低于 100 m³ 下限）。")
            if gw.get("cores") is not None:
                ag = gw.get("agreement") or {}
                t += f" 世界模型该情景推演出 {gw['cores']} 个积水核，其中 {ag.get('model_only', 0)} 个是客户清单和本系统都没有的「新增」，{ag.get('both', 0) + ag.get('hotspot', 0)} 个与客户热点对上。地图已点亮积水区与客户热点。"
            return t
        t = (f"The client list has {r['client_total']} flood points ({reg}). At {lab} this system computes a flood state for {r['with_state']} of them (level III {bl.get('III', 0)} / II {bl.get('II', 0)} / I {bl.get('I', 0)}); {r['dispatchable']} dispatchable, {r['served']} served, {r['tanker_served']} by tankers, {r['world_model_confirmed']} confirmed by the world model; "
             f"{r['no_state']} have no state ({r['no_state_reasons']['no_network_no_reach']} without network or matched reach, the rest under the 100 m³ floor).")
        if gw.get("cores") is not None:
            ag = gw.get("agreement") or {}
            t += f" The world model produces {gw['cores']} ponding cores for this scenario, {ag.get('model_only', 0)} of them new (in neither the client list nor this system), {ag.get('both', 0) + ag.get('hotspot', 0)} matching client hotspots. Flood and hotspot layers are lit on the map."
        return t

    def _act_world_model_new_points(self, P):
        key = P["scenario"]; g = self.b.gwm_points(key)
        if not g or g.get("error"):
            raise ActionError((g or {}).get("error") or "世界模型对照不可用")
        feats = [dict(f.get("properties") or {}) for f in (g.get("geojson") or {}).get("features") or []]
        new = [f for f in feats if f.get("agreement") == "model_only"]
        ob = P.get("order_by") or "volume_m3"
        new.sort(key=lambda f: -(_f(f.get(ob)) or 0))
        # 最近的客户热点 / 本系统积水点（给「验证」和「怎么处置」用）
        fps = [r for r in self._fp_index().values() if r.get("lon") is not None]
        states = self.load_states(key)
        xy_all = _xy([r["lon"] for r in fps], [r["lat"] for r in fps]) if fps else None
        rows = []
        for f in new[: int(P.get("limit") or 10)]:
            row = {k: f.get(k) for k in ("fp_id", "lon", "lat", "max_depth_m", "volume_m3", "area_m2", "first_wet_min", "peak_min", "note", "note_en")}
            if xy_all is not None and f.get("lon") is not None:
                d = np.hypot(*(xy_all - _xy([f["lon"]], [f["lat"]])).T)
                order = np.argsort(d)
                j = int(order[0]); row["nearest_hotspot"] = {"fp_id": fps[j]["fp_id"], "dist_m": round(float(d[j])), "location": fps[j].get("location"), "criticality": fps[j].get("criticality")}
                js = next((int(k) for k in order if fps[int(k)]["fp_id"] in states), None)
                row["nearest_system_point"] = {"fp_id": fps[js]["fp_id"], "dist_m": round(float(d[js])), "level": states[fps[js]["fp_id"]].get("level")} if js is not None else None
            rows.append(row)
        vol = float(sum(_f(f.get("volume_m3"), 0) or 0 for f in new))
        st = g.get("stats") or {}
        return {"scenario": self.scenario_object(key), "run_id": g.get("run_id"), "matched_by": g.get("matched_by"), "gwm_scenario": g.get("scenario"), "cores_total": len(feats), "agreement": (st.get("gwm") or {}).get("agreement"), "new_total": len(new), "new_volume_m3": round(vol), "rows": rows,
                "_ui": {"rp": key, "layers": ["gwm", "hs"], "highlight": self._hl(rows, "gwm"), "fit": "highlight"}}

    def _sum_world_model_new_points(self, r, P, lang):
        s = r["scenario"]; lab = s.get("label") if lang == "zh" else (s.get("label_en") or s.get("label")); gs = r.get("gwm_scenario") or {}
        rows = r["rows"]
        def line(i, x):
            nh = x.get("nearest_hotspot") or {}; ns = x.get("nearest_system_point") or {}
            if lang == "zh":
                return f"{i}. {x['fp_id']}：水深 {x.get('max_depth_m')} m，{_fmt(x.get('volume_m3'))} m³，{_fmt((x.get('area_m2') or 0) / 1e4)} ha；最近客户热点 {nh.get('fp_id')}（{_fmt(nh.get('dist_m'))} m，{nh.get('location') or ''}）" + (f"，最近本系统积水点 {ns.get('fp_id')}（{_fmt(ns.get('dist_m'))} m，{ns.get('level')} 级）" if ns else "")
            return f"{i}. {x['fp_id']}: depth {x.get('max_depth_m')} m, {_fmt(x.get('volume_m3'))} m³, {_fmt((x.get('area_m2') or 0) / 1e4)} ha; nearest hotspot {nh.get('fp_id')} ({_fmt(nh.get('dist_m'))} m, {nh.get('location') or ''})" + (f", nearest system point {ns.get('fp_id')} ({_fmt(ns.get('dist_m'))} m, level {ns.get('level')})" if ns else "")
        body = "\n".join(line(i + 1, x) for i, x in enumerate(rows))
        if lang == "zh":
            head = (f"{lab}对应的世界模型推演（{gs.get('name') or r.get('run_id')}{'，同重现期' if r.get('matched_by') == 'return_period' else '，雨量最接近的推演'}）共 {r['cores_total']} 个积水核，其中 {r['new_total']} 个是客户清单和本系统都没有的新增点，合计体积 {_fmt(r['new_volume_m3'] / 1e4, 0)} 万 m³。按{'体积' if (P.get('order_by') or 'volume_m3') == 'volume_m3' else '水深'}最严重的前 {len(rows)} 个（已在地图上高亮，紫色）：")
            tail = "\n注意：世界模型 250 m 格网、水深只涨不退、体积偏大；新增点需现场核实，处置可参照最近的本系统积水点。"
        else:
            head = (f"The world-model run for {lab} ({gs.get('name_en') or gs.get('name') or r.get('run_id')}, {'same return period' if r.get('matched_by') == 'return_period' else 'closest rainfall'}) has {r['cores_total']} ponding cores, {r['new_total']} of them new (in neither the client list nor this system), total volume {_fmt(r['new_volume_m3'] / 1e6, 2)} M m³. Top {len(rows)} by {'volume' if (P.get('order_by') or 'volume_m3') == 'volume_m3' else 'depth'} (highlighted purple on the map):")
            tail = "\nCaveat: 250 m grid, depth never recedes, volume over-stated; new points need field verification and can be handled like their nearest system point."
        return head + "\n" + body + tail

    def _act_recession_batch(self, P):
        key, ids = self._batch_targets(P)
        if not ids:
            raise ActionError("没有可算的积水点（给编号，或换筛选条件）")
        items = []; hl_rows = []
        for fp in ids:
            r = self.b.recession(fp, key, None, bool(P.get("with_upsize", True)))
            if r.get("error"):
                items.append({"fp_id": fp, "error": r["error"]}); continue
            opts = {o["key"]: o for o in r.get("options") or []}; fast = opts.get(r.get("fastest")) if r.get("fastest") else None
            glob, single, prepo = opts.get("global"), opts.get("single"), opts.get("prepo")
            ups = [o for k, o in opts.items() if k.startswith("upsize")]
            best_up = min(ups, key=lambda o: (float(o.get("shortfall_m3") or 0), float(o.get("t_empty_min") or 1e9))) if ups else None
            svg = r.get("single_vs_global") or {}; need = r.get("need_units_for_target") or {}
            pt = r.get("point") or {}
            items.append({"fp_id": fp, "location": pt.get("location"), "level": pt.get("level"), "criticality": pt.get("criticality"), "lon": r.get("lon"), "lat": r.get("lat"), "target_drain_min": r.get("target_drain_min"), "triage": pt.get("triage"),
                          "fastest": (fast["key"] if fast else None), "fastest_label_zh": (fast["label_zh"] if fast else None), "fastest_label_en": (fast["label_en"] if fast else None), "fastest_t_empty_min": (fast.get("t_empty_min") if fast else None), "fastest_within_target": bool(fast and fast.get("within_target")),
                          "fastest_units": (fast.get("n_units") if fast else 0), "fastest_cost_aed": (fast.get("cost_aed") if fast else None), "fastest_site": ((fast.get("site") or {}).get("name") if fast else None),
                          "global_t_empty_min": (glob or {}).get("t_empty_min"), "global_units": (glob or {}).get("n_units"), "single_t_empty_min": (single or {}).get("t_empty_min"), "single_cost_aed": (single or {}).get("cost_aed"), "prepo_t_empty_min": (prepo or {}).get("t_empty_min"), "prepo_cost_aed": (prepo or {}).get("cost_aed"),
                          "units_taken_from_global": svg.get("units_taken_from_global") or [], "displaced_points": [d.get("fp_id") for d in (svg.get("displaced_points") or [])], "displaced_saved_min_total": svg.get("displaced_saved_min_total"),
                          "need_units": need.get("n"), "need_reason_zh": need.get("reason_zh"), "need_reason_en": need.get("reason_en"), "wait_until_min": r.get("wait_until_min"),
                          "upsize": ({"dn_mm": best_up["key"].split("_")[-1], "t_empty_min": best_up.get("t_empty_min"), "shortfall_m3": best_up.get("shortfall_m3"), "cost_aed": best_up.get("cost_aed")} if best_up else None)})
            if r.get("lon") is not None:
                hl_rows.append({"fp_id": fp, "lon": r["lon"], "lat": r["lat"], "location": pt.get("location")})
        ok = [i for i in items if not i.get("error")]
        agg = {"n": len(items), "n_ok": len(ok), "meet_target": sum(1 for i in ok if i["fastest_within_target"]), "never_recede_without_action": sum(1 for i in ok if i.get("global_t_empty_min") is None and not (i.get("global_units") or 0)),
               "fastest_kinds": {}, "units_total": int(sum(i.get("fastest_units") or 0 for i in ok)), "ops_cost_total_aed": round(float(sum(i.get("fastest_cost_aed") or 0 for i in ok))), "prepo_cost_total_aed": round(float(sum(i.get("prepo_cost_aed") or 0 for i in ok))),
               "single_cost_total_aed": round(float(sum(i.get("single_cost_aed") or 0 for i in ok))), "upsize_cost_total_aed": round(float(sum(((i.get("upsize") or {}).get("cost_aed") or 0) for i in ok))),
               "units_taken_from_global": sorted({u for i in ok for u in i.get("units_taken_from_global") or []}), "displaced_points": sorted({d for i in ok for d in i.get("displaced_points") or []}), "displaced_saved_min_total": round(float(sum(i.get("displaced_saved_min_total") or 0 for i in ok))),
               "staging_sites": sorted({i["fastest_site"] for i in ok if i.get("fastest_site")})}
        for i in ok:
            agg["fastest_kinds"][i.get("fastest") or "none"] = agg["fastest_kinds"].get(i.get("fastest") or "none", 0) + 1
        return {"scenario": self.scenario_object(key), "ids": ids, "items": items, "aggregate": agg, "_ui": {"rp": key, "tab": "line3", "highlight": self._hl(hl_rows, "points", label_key="fp_id"), "fit": "highlight", "layers": ["flood", "hs"]}}

    def _sum_recession_batch(self, r, P, lang):
        s = r["scenario"]; lab = s.get("label") if lang == "zh" else (s.get("label_en") or s.get("label")); a = r["aggregate"]
        def h(m):
            return ("48 h 内不退" if lang == "zh" else "no recession in 48 h") if m is None else f"{float(m) / 60:.1f} h"
        lines = []
        for i in r["items"]:
            if i.get("error"):
                lines.append(f"- {i['fp_id']}：{i['error']}"); continue
            if lang == "zh":
                lines.append(f"- {i['fp_id']}（{i.get('location') or ''}，{i.get('level')} 级）：最快 {i.get('fastest_label_zh') or '—'} → {h(i.get('fastest_t_empty_min'))}{'（达标）' if i['fastest_within_target'] else '（仍超目标 ' + _fmt(i.get('target_drain_min')) + ' min）'}，{i.get('fastest_units') or 0} 台，约 {_fmt(i.get('fastest_cost_aed'))} AED"
                             + (f"；全局调度给它 {i.get('global_units')} 台 → {h(i.get('global_t_empty_min'))}" if i.get("global_units") else "；全局调度本轮没给它泵")
                             + (f"；抢泵 {', '.join(i['units_taken_from_global'])} 会让 {', '.join(i['displaced_points'])} 推迟 {h(i.get('displaced_saved_min_total'))}" if i.get("units_taken_from_global") else "")
                             + (f"；达标需 {i['need_units']} 台" if i.get("need_units") else (f"；达标做不到（{i.get('need_reason_zh')}）" if i.get("need_reason_zh") else ""))
                             + (f"；长期换 DN{i['upsize']['dn_mm']} 约 {_fmt(i['upsize'].get('cost_aed'))} AED" if i.get("upsize") and i["upsize"].get("cost_aed") else ""))
            else:
                lines.append(f"- {i['fp_id']} ({i.get('location') or ''}, level {i.get('level')}): fastest {i.get('fastest_label_en') or '—'} → {h(i.get('fastest_t_empty_min'))}{' (within target)' if i['fastest_within_target'] else ' (past target ' + _fmt(i.get('target_drain_min')) + ' min)'}, {i.get('fastest_units') or 0} units, about {_fmt(i.get('fastest_cost_aed'))} AED"
                             + (f"; the global plan gives it {i.get('global_units')} → {h(i.get('global_t_empty_min'))}" if i.get("global_units") else "; nothing from the global plan this round")
                             + (f"; taking {', '.join(i['units_taken_from_global'])} delays {', '.join(i['displaced_points'])} by {h(i.get('displaced_saved_min_total'))}" if i.get("units_taken_from_global") else "")
                             + (f"; {i['need_units']} units to meet target" if i.get("need_units") else (f"; target out of reach ({i.get('need_reason_en')})" if i.get("need_reason_en") else ""))
                             + (f"; long term DN{i['upsize']['dn_mm']} about {_fmt(i['upsize'].get('cost_aed'))} AED" if i.get("upsize") and i["upsize"].get("cost_aed") else ""))
        kinds = "、".join(f"{k} {v}" for k, v in a["fastest_kinds"].items()) if lang == "zh" else ", ".join(f"{k} {v}" for k, v in a["fastest_kinds"].items())
        if lang == "zh":
            head = f"{lab}，{a['n_ok']} 个积水点（已在地图上高亮）：最快手段分布 {kinds}；能在目标时限内退干 {a['meet_target']} 个；合计需泵 {a['units_total']} 台，最快手段运维费用约 {_fmt(a['ops_cost_total_aed'])} AED（全部走事中单点最优约 {_fmt(a['single_cost_total_aed'])} AED，全部预置约 {_fmt(a['prepo_cost_total_aed'])} AED）"
            head += (f"；长期换管造价合计约 {_fmt(a['upsize_cost_total_aed'])} AED（占位单价）" if a["upsize_cost_total_aed"] else "")
            head += (f"。代价：单点最优会从全局调度抢走 {len(a['units_taken_from_global'])} 台泵，让 {len(a['displaced_points'])} 个点推迟合计 {h(a['displaced_saved_min_total'])}" if a["units_taken_from_global"] else "。这些方案用的泵在全局调度里是空着的，不抢别人")
            head += (f"；涉及预置驻地 {len(a['staging_sites'])} 处。" if a["staging_sites"] else "。")
        else:
            head = f"{lab}, {a['n_ok']} points (highlighted on the map): fastest measures {kinds}; {a['meet_target']} can meet their target; {a['units_total']} pumps in total, operating cost of the fastest measures about {_fmt(a['ops_cost_total_aed'])} AED (all single-point dispatch about {_fmt(a['single_cost_total_aed'])} AED, all pre-positioned about {_fmt(a['prepo_cost_total_aed'])} AED)"
            head += (f"; long-term upsizing about {_fmt(a['upsize_cost_total_aed'])} AED (placeholder rates)" if a["upsize_cost_total_aed"] else "")
            head += (f". Trade-off: single-point optima take {len(a['units_taken_from_global'])} pumps from the global plan and delay {len(a['displaced_points'])} points by {h(a['displaced_saved_min_total'])} in total" if a["units_taken_from_global"] else ". The pumps used are idle in the global plan; nobody loses")
            head += (f"; {len(a['staging_sites'])} staging sites involved." if a["staging_sites"] else ".")
        return head + "\n" + "\n".join(lines)

    def _act_cost_summary(self, P):
        key = P["scenario"]
        if P.get("fp_ids"):
            r = self._act_recession_batch({**P, "level": None, "criticality": None, "zone": None, "with_upsize": True})
            r["mode"] = "points"; return r
        dp = (self.bundle(key) or {}).get("dispatch") or {}
        pre = {lvl: {"sites": len((v or {}).get("sites") or []), "units": ((v or {}).get("value") or {}).get("units_placed"), "cost_aed": (((v or {}).get("value") or {}).get("cost") or {}).get("total_aed"), "saved_vs_after_pct": ((v or {}).get("value") or {}).get("saved_vs_after_pct")} for lvl, v in (dp.get("preposition") or {}).items()}
        plans = {k: {"label": v.get("label"), "label_en": v.get("label_en"), "pumps_total": v.get("pumps_total"), "cost_aed": ((v.get("value") or {}).get("cost") or {}).get("total_aed"), "saved_vs_after_pct": (v.get("value") or {}).get("saved_vs_after_pct")} for k, v in (dp.get("plans") or {}).items() if isinstance(v, dict)}
        return {"mode": "scenario", "scenario": self.scenario_object(key), "dispatch_cost": dp.get("cost"), "kpis": (dp.get("kpis") or {}).get("solver"), "replay": dp.get("replay_summary"), "tankers": {k: (dp.get("tankers") or {}).get(k) for k in ("used", "pool", "duty_aed_h")}, "preposition": pre, "plans": plans,
                "_ui": {"rp": key, "tab": "line3"}}

    def _sum_cost_summary(self, r, P, lang):
        if r.get("mode") == "points":
            return self._sum_recession_batch(r, P, lang)
        s = r["scenario"]; lab = s.get("label") if lang == "zh" else (s.get("label_en") or s.get("label")); c = r.get("dispatch_cost") or {}; k = r.get("kpis") or {}; rp = r.get("replay") or {}
        pre = "；".join(f"{lvl}级预置 {v['sites']} 处 {v.get('units') or 0} 台约 {_fmt(v.get('cost_aed'))} AED（比事后少积水 {v.get('saved_vs_after_pct')}%）" for lvl, v in r["preposition"].items() if v.get("cost_aed") is not None) if lang == "zh" else "; ".join(f"{lvl} staging {v['sites']} sites / {v.get('units') or 0} units about {_fmt(v.get('cost_aed'))} AED ({v.get('saved_vs_after_pct')}% fewer flood-hours than after-the-fact)" for lvl, v in r["preposition"].items() if v.get("cost_aed") is not None)
        pl = "；".join(f"{v.get('label')} {v.get('pumps_total')} 台约 {_fmt(v.get('cost_aed'))} AED（少积水 {v.get('saved_vs_after_pct')}%）" for v in r["plans"].values() if v.get("cost_aed") is not None) if lang == "zh" else "; ".join(f"{v.get('label_en') or v.get('label')} {v.get('pumps_total')} units about {_fmt(v.get('cost_aed'))} AED ({v.get('saved_vs_after_pct')}% fewer flood-hours)" for v in r["plans"].values() if v.get("cost_aed") is not None)
        if lang == "zh":
            return (f"{lab}全局账：第三道事中调度 {k.get('units_used')} 台泵、{_fmt(c.get('pump_hours'))} 台时，约 {_fmt(c.get('total_aed'))} AED（台时 × 预案 A 单价 + 动员费），覆盖 {k.get('points_served')}/{k.get('points_total')} 点，被派点积水时长少 {rp.get('served_saved_pct')}%。"
                    + (f" 预置：{pre}。" if pre else "") + (f" 客户预案对比：{pl}。" if pl else "") + " 代价看两处：预置花钱在雨前，但退干最快；单点最优会抢全局的泵——具体到点用 recession_batch。单价为客户预案 A 表与占位假设。")
        return (f"{lab} scenario bill: line-3 dispatch uses {k.get('units_used')} pumps, {_fmt(c.get('pump_hours'))} pump-hours, about {_fmt(c.get('total_aed'))} AED (pump-hours × Plan A rates + mobilisation), serving {k.get('points_served')}/{k.get('points_total')} points with {rp.get('served_saved_pct')}% fewer flood-hours at served points."
                + (f" Staging: {pre}." if pre else "") + (f" Client plans: {pl}." if pl else "") + " Trade-offs: pre-positioning spends before the rain but drains fastest; single-point optima take pumps from the global plan — use recession_batch for specific points. Rates are client Plan A and placeholders.")

    def _act_inspect_point(self, P):
        key = P["scenario"]; fp = P["fp_id"]
        o = self.get("FloodPoint", fp); st = self.load_states(key).get(fp)
        res = {"scenario": self.scenario_object(key), "point": o, "state": st,
               "reach": (self.links("FloodPoint", fp, "drains_via") or [None])[0], "zone": (self.links("FloodPoint", fp, "zone") or [None])[0],
               "assignments": self.load_assignments(key).get(fp, []), "gwm_core": (self.links("FloodState", f"{fp}@{key}", "gwm_core", key) or [None])[0] if st else None,
               "proposals": self.links("FloodPoint", fp, "proposals")[:5], "observations": self.links("FloodPoint", fp, "observations")[-3:],
               "rules_point": self.rules("FloodPoint", o, key), "rules_state": self.rules("FloodState", st, key) if st else [], "_ui": {"hs": fp, "rp": key}}
        try:
            res["history"] = self.point_lifecycle(fp)
        except Exception as ex:
            res["history"] = {"error": str(ex)[:120]}
        return res

    def _sum_inspect_point(self, r, P, lang):
        o, st, sc = r["point"], r.get("state"), r["scenario"]
        L = [self._fp_line(o, lang)]
        lab = sc.get("label") if lang == "zh" else (sc.get("label_en") or sc.get("label"))
        if st is None:
            L.append(f"{lab}：本系统没有算出它的积水过程（{'客户标无管网、也没匹配到管段' if o.get('edge_gid') is None else '匹配了管段但体积低于 100 m³ 下限'}）。" if lang == "zh" else f"{lab}: no system flood state ({'no network and no matched reach' if o.get('edge_gid') is None else 'reach matched but volume under the 100 m³ floor'}).")
        else:
            srv = (f"已派 {st['units_assigned']} 台，首台 {_fmt(st['first_arrive_min'])} min 到位，{_fmt(st['done_min'])} min 排干，{'达标' if st.get('within_target') else '未达标'}" if st.get("served") else f"未派（{st.get('triage') or ''}）") if lang == "zh" else \
                  (f"served by {st['units_assigned']} unit(s), first arrival {_fmt(st['first_arrive_min'])} min, drained {_fmt(st['done_min'])} min, {'within target' if st.get('within_target') else 'late'}" if st.get("served") else f"not served ({st.get('triage') or ''})")
            gw = (f"世界模型{'印证' if st.get('world_model_confirmed') else '未印证'}" + (f"（核 {st['gwm_core']}，{_fmt(st['gwm_dist_m'])} m，水深 {st['gwm_depth_m']} m）" if st.get("gwm_core") else "")) if lang == "zh" else \
                 (f"world model {'confirms' if st.get('world_model_confirmed') else 'does not confirm'}" + (f" (core {st['gwm_core']}, {_fmt(st['gwm_dist_m'])} m, depth {st['gwm_depth_m']} m)" if st.get("gwm_core") else ""))
            if lang == "zh":
                L.append(f"{lab}：{st.get('level')} 级（{st.get('level_priority')}），峰值 {_fmt(st.get('volume_m3'))} m³ / {st.get('max_depth_m')} m，无泵积水 {_fmt(st.get('duration_min'))} min，目标 {_fmt(st.get('target_drain_min'))} min，排放去处 {st.get('discharge_to') or '无'}（{_fmt(st.get('discharge_dist_m'))} m{'，需接力' if st.get('relay_needed') else ''}）；{srv}；{gw}。")
            else:
                L.append(f"{lab}: level {st.get('level')} ({st.get('level_priority')}), peak {_fmt(st.get('volume_m3'))} m³ / {st.get('max_depth_m')} m, {_fmt(st.get('duration_min'))} min flooded without pumps, target {_fmt(st.get('target_drain_min'))} min, discharge {st.get('discharge_to') or 'none'} ({_fmt(st.get('discharge_dist_m'))} m{', relay' if st.get('relay_needed') else ''}); {srv}; {gw}.")
        if r.get("reach"):
            e = r["reach"]
            L.append((f"匹配管段 gid {e['gid']}（DN{_fmt(e.get('pipe_diameter'))}，{_fmt(e.get('len_m'))} m，片区 {e.get('zone_name')}，法定 {e.get('design_rp_yr')} 年{'达标' if e.get('compliant') else '不达标'}，10 年路由溢流 {_fmt(e.get('nf_spill_m3_10'))} m³{'，下游顶托' if e.get('nf_backwater_10') else ''}）。") if lang == "zh" else
                     (f"Matched reach gid {e['gid']} (DN{_fmt(e.get('pipe_diameter'))}, {_fmt(e.get('len_m'))} m, zone {e.get('zone_name')}, statutory {e.get('design_rp_yr')}-yr {'compliant' if e.get('compliant') else 'non-compliant'}, 10-yr routed spill {_fmt(e.get('nf_spill_m3_10'))} m³{', backwater' if e.get('nf_backwater_10') else ''})."))
        fired = [x for x in r.get("rules_point", []) + r.get("rules_state", []) if x.get("fired")]
        if fired:
            def why(x):
                v = x.get("value")
                if not isinstance(v, dict):
                    return ""
                w = v.get("why" if lang == "zh" else "why_en") or v.get("why")
                return f"（{w}）" if w else "（" + ", ".join(f"{k} {vv}" for k, vv in v.items() if k not in ("suggest",)) + "）"
            L.append(("触发规则：" if lang == "zh" else "Rules fired: ") + "；".join(f"{x['zh' if lang == 'zh' else 'en']}{why(x)}" for x in fired))
        if r.get("proposals"):
            L.append((f"相关工程建议 {len(r['proposals'])} 条，首条：{r['proposals'][0].get('title')}" if lang == "zh" else f"{len(r['proposals'])} related proposal(s), first: {r['proposals'][0].get('title_en') or r['proposals'][0].get('title')}"))
        h = r.get("history") or {}
        if h.get("n_events"):
            if lang == "zh":
                L.append(f"历史：{h['n_events']} 场有记录的降雨事件里" + (f"有观测记录 {h['n_observed']} 场、积水 {h['n_flooded']} 次（复发率 {h['recurrence']}）" if h.get("n_observed") else f"没有现场记录，模型推演积水 {h['n_model_flooded']} 次") + (f"，最近一次有记录积水 {h['last_flooded']}" if h.get("last_flooded") else "") + f"；当前状态 {h.get('lifecycle_status_zh')}。")
            else:
                L.append(f"History: over {h['n_events']} recorded rain events, " + (f"{h['n_observed']} with records, flooded {h['n_flooded']} times (recurrence {h['recurrence']})" if h.get("n_observed") else f"no field records; the model floods it in {h['n_model_flooded']}") + (f", last recorded flooding {h['last_flooded']}" if h.get("last_flooded") else "") + f"; current status {h.get('lifecycle_status_en')}.")
        if r.get("observations"):
            ob = r["observations"][-1]
            L.append((f"最近现场观测 {ob.get('observed_at')}：{ob.get('status')} {ob.get('depth_m')} m {ob.get('note') or ''}" if lang == "zh" else f"Latest field observation {ob.get('observed_at')}: {ob.get('status')} {ob.get('depth_m')} m {ob.get('note') or ''}"))
        return " ".join(L)

    def _act_list_points(self, P):
        key = P["scenario"]
        filt = {k: P[k] for k in ("level", "criticality", "region") if P.get(k)}
        if P.get("dispatchable") is not None:
            filt["dispatchable"] = bool(P["dispatchable"])
        if P.get("served") is not None:
            filt["served"] = bool(P["served"])
        if P.get("zone"):
            filt["zone_name"] = P["zone"]
        rows = self.search("FloodState", filt, order_by=P.get("order_by") or "priority", desc=(P.get("order_by") != "done_min"), limit=P.get("limit") or 10, scenario=key)
        total = len(self.search("FloodState", filt, limit=100000, scenario=key))
        keep = ("fp_id", "location", "region", "criticality", "level", "priority", "volume_m3", "max_depth_m", "dispatchable", "triage", "served", "units_assigned", "done_min", "within_target", "world_model_confirmed", "zone_name", "lon", "lat")
        return {"scenario": self.scenario_object(key), "filters": filt, "total": total, "rows": [{k: r.get(k) for k in keep} for r in rows], "_ui": {"rp": key, "tab": "line3", "fp": rows[0]["fp_id"] if rows else None, "highlight": self._hl(rows, "points", label_key="fp_id"), "fit": "highlight", "layers": ["flood"]}}

    def _sum_list_points(self, r, P, lang):
        sc = r["scenario"]; lab = sc.get("label") if lang == "zh" else (sc.get("label_en") or sc.get("label"))
        f = "、".join(f"{k}={v}" for k, v in r["filters"].items()) or ("不筛" if lang == "zh" else "no filter")
        head = f"{lab}，条件 {f}：共 {r['total']} 个，按 {P.get('order_by') or 'priority'} 取前 {len(r['rows'])}：" if lang == "zh" else f"{lab}, filter {f}: {r['total']} points, top {len(r['rows'])} by {P.get('order_by') or 'priority'}:"
        lines = []
        for i, x in enumerate(r["rows"], 1):
            srv = (f"已派 {x['units_assigned']} 台 {'✓' if x.get('within_target') else '✗'}" if x.get("served") else f"未派") if lang == "zh" else (f"served ×{x['units_assigned']} {'✓' if x.get('within_target') else '✗'}" if x.get("served") else "unserved")
            lines.append(f"{i}. {x['fp_id']} {x.get('location') or ''} · {x.get('level')} · {x.get('criticality')} · {_fmt(x.get('volume_m3'))} m³ · {srv}")
        return head + "\n" + "\n".join(lines)

    def _act_upsize_whatif(self, P):
        det = self.b.hotspot_detail(P["fp_id"], int(P.get("dn_steps") or 2), key=(self.scenario_key(P.get("scenario")) if P.get("scenario") else None))      # 按问的情景算，不是激活情景
        if det.get("error"):
            raise ActionError(det["error"])
        cur = det.get("curves") or {}
        if cur.get("error"):
            raise ActionError(cur["error"])
        m = det.get("matched")
        if (m is not None and m.get("edge_gid") is None) or not cur.get("base_kpi"):
            raise ActionError(f"{P['fp_id']} 没有匹配到管段（按 DTM 洼地估算的点），换管 what-if 不适用；可以看它的重演或预置" if (m is not None and m.get("edge_gid") is None) else f"{P['fp_id']} 没有退水曲线")
        return {"fp_id": P["fp_id"], "scenario_key": det.get("scenario_key"), "scenario_label": det.get("scenario_label"), "scenario_label_en": det.get("scenario_label_en"), "reach": cur.get("reach"), "no_gap": cur.get("no_gap"),
                "base_kpi": cur.get("base_kpi"), "pump_kpi": cur.get("pump_kpi"), "pumps": cur.get("pumps"), "upsized": [{kk: o.get(kk) for kk in ("dn_mm", "q_full_m3s", "shortfall_m3", "kpi")} for o in cur.get("upsized") or []], "_ui": {"hs": P["fp_id"]}}

    def _sum_upsize_whatif(self, r, P, lang):
        lab = r.get("scenario_label") if lang == "zh" else (r.get("scenario_label_en") or r.get("scenario_label")); b = r.get("base_kpi") or {}; pk = r.get("pump_kpi"); dn0 = (r.get("reach") or {}).get("dn_mm")
        def te(k):
            return ("不退干" if lang == "zh" else "never empties") if (k or {}).get("t_empty_min") is None else f"{_fmt(k['t_empty_min'])} min"
        if r.get("no_gap"):
            return (f"{r['fp_id']}（{lab}）：匹配管段 DN{_fmt(dn0)} 在该情景下没有缺口，不积水，换管无意义。" if lang == "zh" else f"{r['fp_id']} ({lab}): matched reach DN{_fmt(dn0)} has no shortfall in this scenario; nothing to upsize.")
        ups = "；".join((f"换 DN{_fmt(o['dn_mm'])}：缺口 {_fmt(o.get('shortfall_m3'))} m³，峰值 {_fmt((o.get('kpi') or {}).get('peak_m3'))} m³，{te(o.get('kpi'))}" if lang == "zh" else f"DN{_fmt(o['dn_mm'])}: shortfall {_fmt(o.get('shortfall_m3'))} m³, peak {_fmt((o.get('kpi') or {}).get('peak_m3'))} m³, {te(o.get('kpi'))}") for o in r.get("upsized") or [])
        pump = (f"派泵 {len(r.get('pumps') or [])} 台：{te(pk)}" if pk else "无派单") if lang == "zh" else (f"with {len(r.get('pumps') or [])} pump(s): {te(pk)}" if pk else "no dispatch")
        if lang == "zh":
            return f"{r['fp_id']}（{lab}）现状 DN{_fmt(dn0)}：无泵峰值 {_fmt(b.get('peak_m3'))} m³、{te(b)}、积水 {b.get('flood_h')} h；{pump}；{ups}。"
        return f"{r['fp_id']} ({lab}) current DN{_fmt(dn0)}: no pumps peak {_fmt(b.get('peak_m3'))} m³, {te(b)}, {b.get('flood_h')} h flooded; {pump}; {ups}."

    def _act_compare_world_model(self, P):
        key = P["scenario"]; r = self.b.gwm_points(key)
        if not r or r.get("error"):
            raise ActionError((r or {}).get("error") or "世界模型对照不可用")
        return {"scenario": self.scenario_object(key), "run_id": r.get("run_id"), "matched_by": r.get("matched_by"), "gwm_scenario": r.get("scenario"), "stats": r.get("stats"), "model": {k: (r.get("model") or {}).get(k) for k in ("name", "known_behaviour", "known_behaviour_en")}, "_ui": {"rp": key, "tab": "trust", "gwm": 1}}

    def _sum_compare_world_model(self, r, P, lang):
        st = r["stats"] or {}; g = st.get("gwm") or {}; h = st.get("hotspots") or {}; sy = st.get("system") or {}; ag = g.get("agreement") or {}; gs = r.get("gwm_scenario") or {}
        gl = gs.get("name") if lang == "zh" else (gs.get("name_en") or gs.get("name"))
        if lang == "zh":
            return (f"{gl}（{'同重现期' if r.get('matched_by') == 'return_period' else '雨量最接近的推演'}）：{g.get('n')} 个推演核里 {g.get('with_hotspot')} 个附近有客户热点（两边都有 {ag.get('both', 0)}、只客户热点 {ag.get('hotspot', 0)}、模型独有 {ag.get('model_only', 0)}，独有体积 {_fmt((g.get('model_only_volume_m3') or 0) / 1e4, 0)} 万 m³）；"
                    f"客户热点格网内 {h.get('in_grid') or h.get('n')} 个被印证 {h.get('covered')}（{_fmt((h.get('covered_share') or 0) * 100)}%，格网外 {h.get('outside_grid')} 个不计）；本系统积水点 {sy.get('n')} 个被印证 {sy.get('covered')}。模型水深只涨不退、体积偏大，只作空间对照。")
        return (f"{gl} ({'same return period' if r.get('matched_by') == 'return_period' else 'closest rainfall run'}): {g.get('with_hotspot')} of {g.get('n')} cores have a client hotspot nearby (both {ag.get('both', 0)}, hotspot only {ag.get('hotspot', 0)}, model only {ag.get('model_only', 0)}, model-only volume {_fmt((g.get('model_only_volume_m3') or 0) / 1e6, 1)} M m³); "
                f"{h.get('covered')} of {h.get('in_grid') or h.get('n')} in-grid client hotspots confirmed ({_fmt((h.get('covered_share') or 0) * 100)}%, {h.get('outside_grid')} outside the grid excluded); {sy.get('covered')} of {sy.get('n')} system points confirmed. The model never recedes and over-states volume; spatial cross-check only.")

    def _act_record_observation(self, P):
        fp = P["fp_id"]; now = time.strftime("%Y-%m-%d %H:%M:%S")
        obs = {"obs_id": f"OBS-{time.strftime('%Y%m%dT%H%M%S')}-{fp}", "fp_id": fp, "observed_at": now, "depth_m": float(P.get("depth_m") or 0), "status": P["status"], "note": P.get("note"), "reporter": P.get("reporter") or "agent"}
        self.obs_path.parent.mkdir(parents=True, exist_ok=True)
        with self.obs_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obs, ensure_ascii=False) + "\n")
        self.invalidate("obs"); self._invalidate_lifecycle()
        key = self.scenario_key(None)
        o = self.get("FloodPoint", fp)
        conflict = next((x for x in self.rules("FloodPoint", o, key) if x["rule"] == "observation_conflict"), None)
        rel = self.obs_path.relative_to(self.root).as_posix() if str(self.obs_path).startswith(str(self.root)) else self.obs_path.as_posix()
        return {"observation": obs, "scenario_key": key, "state": self.load_states(key).get(fp) if self._has_backend_states() else None, "conflict": (conflict or {}).get("value"), "file": rel, "_ui": {"hs": fp}}

    def _sum_record_observation(self, r, P, lang):
        o = r["observation"]; c = r.get("conflict")
        if lang == "zh":
            s = f"已登记 {o['fp_id']} 现场观测（{o['observed_at']}，{o['status']}，水深 {o['depth_m']} m{'，' + o['note'] if o.get('note') else ''}）→ {r['file']}。"
            return s + (f" 与推演冲突：{c.get('why')}，已标待核。" if c else " 与当前情景推演不冲突。")
        s = f"Recorded field observation for {o['fp_id']} ({o['observed_at']}, {o['status']}, depth {o['depth_m']} m{', ' + o['note'] if o.get('note') else ''}) → {r['file']}."
        return s + (f" Conflicts with the model: {c.get('why_en') or c.get('why')}; flagged for review." if c else " No conflict with the current scenario.")

    def _act_explain_rule(self, P):
        r = S.RL[P["rule"]]
        props = [f"{o.api}.{p.name}" for o in S.OBJECT_TYPES for p in o.properties if p.derived_by == r.api]
        return {"rule": {k: v for k, v in r.__dict__.items() if k != "fn"}, "derives": props}

    def _sum_explain_rule(self, r, P, lang):
        R = r["rule"]
        return (f"规则「{R['zh']}」（{R['kind']}，作用于 {R['applies_to']}）：{R['logic_zh']} 依据：{R['source']}。" + (f" 派生属性：{', '.join(r['derives'])}。" if r["derives"] else "")) if lang == "zh" else \
               (f"Rule “{R['en']}” ({R['kind']}, on {R['applies_to']}): {R['logic_en']} Source: {R['source']}." + (f" Derives: {', '.join(r['derives'])}." if r["derives"] else ""))

    def _act_zone_overview(self, P):
        z = self.get("Zone", P["zone"])
        if z is None:
            raise ActionError(f"没有片区 {P['zone']}")
        key = P["scenario"]
        pts = [r for r in self.search("FloodState", {"zone_name": z["zone_name"]}, order_by="priority", limit=10000, scenario=key)]
        by = {}
        for r in pts:
            by[r.get("level")] = by.get(r.get("level"), 0) + 1
        props = self.links("Zone", z["zone_name"], "zone_proposals")[:5] if z.get("kind") != "area" else []
        hl_rows = pts[:10] if pts else [fp for fp in self._fp_index().values() if fp.get("zone_name") == z["zone_name"]][:10]       # 该情景没状态的片区就高亮它的积水点本身
        ui = {"tab": "overview", "zone": z["zone_name"], "rp": key} if z.get("kind") != "area" else {"rp": key, "tab": "line3", "layers": ["flood", "hs"], "highlight": self._hl(hl_rows, "points"), "fit": "highlight"}     # 区域片区控制台没有片区图层：高亮它的点
        return {"zone": z, "scenario": self.scenario_object(key), "n_states": len(pts), "by_level": by, "n_served": sum(1 for r in pts if r.get("served")), "top_points": [{k: r.get(k) for k in ("fp_id", "location", "level", "volume_m3", "served")} for r in pts[:5]],
                "proposals": [{k: p.get(k) for k in ("pid", "kind", "title", "title_en", "priority", "when_class")} for p in props], "_ui": ui}

    def _sum_zone_overview(self, r, P, lang):
        z = r["zone"]; s = r["scenario"]; lab = s.get("label") if lang == "zh" else (s.get("label_en") or s.get("label"))
        if z.get("kind") == "area":
            lv = "/".join(f"{k} {v}" for k, v in r["by_level"].items())
            top = "、".join(f"{p['fp_id']}（{p.get('level')}，{_fmt(p.get('volume_m3'))} m³）" for p in r["top_points"][:3]) if lang == "zh" else ", ".join(f"{p['fp_id']} ({p.get('level')}, {_fmt(p.get('volume_m3'))} m³)" for p in r["top_points"][:3])
            if lang == "zh":
                return (f"片区 {z['zone_name']}（{z.get('district_name')}；按客户清单区域归并，没有管网片区指标）：积水点 {z.get('n_flood_points')} 个（{z.get('n_with_reach')} 个匹配到管段），"
                        f"{lab} 下有状态 {r['n_states']} 个（{lv}），已派 {r['n_served']}。最严重：{top}。地图已高亮该片区的点。")
            return (f"Zone {z['zone_name']} ({z.get('district_name')}; grouped from the client list's area field, no network-zone metrics): {z.get('n_flood_points')} flood points ({z.get('n_with_reach')} matched to a reach), "
                    f"{r['n_states']} with a state at {lab} ({lv}), {r['n_served']} served. Worst: {top}. Its points are highlighted on the map.")
        pr = "；".join(f"{p['pid']} {p.get('title') if lang == 'zh' else (p.get('title_en') or p.get('title'))}" for p in r["proposals"][:3])
        if lang == "zh":
            return (f"片区 {z['zone_name']}（{z.get('district_name') or ''}）：管长 {_fmt(z.get('len_km'))} km，风险分 {_fmt(z.get('risk_score'), 1)}，常规雨超载 {_fmt((z.get('over_share_常规') or 0) * 100)}%，极端雨 {_fmt((z.get('over_share_极端') or 0) * 100)}%，蓄渗缺口 {_fmt(z.get('storage_deficit_m3'))} m³，高潮暴露出水口 {z.get('n_tide_outfalls')}，疑似漏登终点 {z.get('n_suspect_outlets')}。"
                    f"积水点 {z.get('n_flood_points')} 个，{lab} 下有状态 {r['n_states']} 个（{'/'.join(f'{k} {v}' for k, v in r['by_level'].items())}），已派 {r['n_served']}。建议 {len(r['proposals'])} 条，如 {pr}。")
        return (f"Zone {z['zone_name']} ({z.get('district_name') or ''}): {_fmt(z.get('len_km'))} km of pipe, risk score {_fmt(z.get('risk_score'), 1)}, overloaded {_fmt((z.get('over_share_常规') or 0) * 100)}% in routine rain and {_fmt((z.get('over_share_极端') or 0) * 100)}% in extreme rain, storage deficit {_fmt(z.get('storage_deficit_m3'))} m³, {z.get('n_tide_outfalls')} tide-exposed outfalls, {z.get('n_suspect_outlets')} suspect terminals. "
                f"{z.get('n_flood_points')} flood points, {r['n_states']} with a state at {lab} ({'/'.join(f'{k} {v}' for k, v in r['by_level'].items())}), {r['n_served']} served. {len(r['proposals'])} proposals, e.g. {pr}.")

    def _act_recession_options(self, P):
        key = P["scenario"]
        r = self.b.recession(P["fp_id"], key, P.get("decision_time_min"))
        if r.get("error"):
            raise ActionError(r["error"])
        if P.get("strict") and hasattr(self.b, "focus_compare"):
            try:
                r["single_vs_global_strict"] = self.b.focus_compare(key, P["fp_id"], P.get("decision_time_min"))
            except Exception as ex:
                r["single_vs_global_strict"] = {"error": str(ex)[:160]}
        keep = ("key", "label_zh", "label_en", "n_units", "t_empty_min", "flood_h", "peak_m3", "never_empties", "within_target", "note_zh", "note_en", "site", "shortfall_m3")
        r["options"] = [{k: o.get(k) for k in keep} | {"units": [u.get("unit_id") for u in (o.get("units") or [])][:6]} for o in r.get("options") or []]
        if not (r.get("point") or {}).get("location"):                      # 调度点位帧没带位置描述：从积水点对象补
            r.setdefault("point", {})["location"] = (self.get("FloodPoint", P["fp_id"]) or {}).get("location")
        r["_ui"] = {"hs": P["fp_id"], "rp": key}
        return r

    def _sum_recession_options(self, r, P, lang):
        lab = r.get("scenario_label") if lang == "zh" else (r.get("scenario_label_en") or r.get("scenario_label"))
        pt = r.get("point") or {}
        loc = pt.get("location"); loc_zh = f"{loc}，" if loc else ""; loc_en = f"{loc}, " if loc else ""
        head = (f"{r['fp_id']}（{loc_zh}{lab}，决策时刻 {_fmt(r.get('decision_time_min'))} min，目标 {_fmt(r.get('target_drain_min'))} min）：" if lang == "zh" else f"{r['fp_id']} ({loc_en}{lab}, decision at {_fmt(r.get('decision_time_min'))} min, target {_fmt(r.get('target_drain_min'))} min): ")
        rec = (r.get("recommendation") or {}).get(lang) or (r.get("recommendation") or {}).get("zh") or ""
        def h(m):
            return "—" if m is None else f"{float(m) / 60:.1f} h"
        rows = [f"{o['label_zh' if lang == 'zh' else 'label_en']}：{h(o.get('t_empty_min')) if o.get('t_empty_min') is not None else ('48 h 内不退' if lang == 'zh' else 'no recession in 48 h')}{(' ✓' if o.get('within_target') else ' ✗')}" for o in r.get("options") or []]
        return head + rec + chr(10) + chr(10).join(rows)

    def _act_replay_point(self, P):
        r = self.b.replay_point(P["fp_id"], P.get("pumps") or None)
        if r.get("error"):
            raise ActionError(r["error"])
        k = r.get("kpi") or {}
        return {"fp_id": P["fp_id"], "pumps_source": r.get("pumps_source"), "pumps": [{kk: p.get(kk) for kk in ("label", "model", "arrive_min", "rate_m3h")} for p in r.get("pumps") or []], "decision_time_min": r.get("decision_time_min"), "point": r.get("point"),
                "kpi": {"base": k.get("base"), "pump": k.get("pump"), "saved_min": k.get("saved_min"), "flood_h_saved": k.get("flood_h_saved"), "pumped_total_m3": k.get("pumped_total_m3"), "base_never_empties": k.get("base_never_empties"), "pump_never_empties": k.get("pump_never_empties")}, "_ui": {"hs": P["fp_id"], "tab": "line3", "fp": P["fp_id"]}}

    def _sum_replay_point(self, r, P, lang):
        k = r["kpi"]; b = k.get("base") or {}; p = k.get("pump") or {}
        def te(x, never):
            return ("不退干" if lang == "zh" else "never empties") if never or (x or {}).get("t_empty_min") is None else f"{_fmt(x['t_empty_min'])} min"
        n = len(r["pumps"]); src = ("调度单" if r.get("pumps_source") == "plan" else "手工指定") if lang == "zh" else ("dispatch plan" if r.get("pumps_source") == "plan" else "manual")
        if lang == "zh":
            return f"{r['fp_id']} 重演：无泵峰值 {_fmt(b.get('peak_m3'))} m³，{te(b, k.get('base_never_empties'))}，积水 {b.get('flood_h')} h；{src} {n} 台泵：{te(p, k.get('pump_never_empties'))}，积水 {p.get('flood_h')} h，少 {k.get('flood_h_saved')} h，抽走 {_fmt(k.get('pumped_total_m3'))} m³。"
        return f"{r['fp_id']} replay: no pumps peak {_fmt(b.get('peak_m3'))} m³, {te(b, k.get('base_never_empties'))}, {b.get('flood_h')} h flooded; {src} with {n} pump(s): {te(p, k.get('pump_never_empties'))}, {p.get('flood_h')} h flooded, {k.get('flood_h_saved')} h saved, {_fmt(k.get('pumped_total_m3'))} m³ pumped."

    def _act_trace_downstream(self, P):
        o = self.get("FloodPoint", P["fp_id"])
        if o.get("edge_gid") is None:
            raise ActionError(f"{P['fp_id']} 没有匹配管段，追不了下游")
        r = self.b.trace(int(o["edge_gid"]))
        if r.get("error"):
            raise ActionError(r["error"])
        return {"fp_id": P["fp_id"], "gid": int(o["edge_gid"]), "downstream_n": len(r.get("downstream_gids") or []), "downstream_km": r.get("downstream_km"), "upstream_n": r.get("upstream_gids_n"), "upstream_km": r.get("upstream_km"), "terminal_kind": r.get("terminal_kind"), "downstream_gids": (r.get("downstream_gids") or [])[:20]}

    def _sum_trace_downstream(self, r, P, lang):
        if lang == "zh":
            return f"{r['fp_id']} 匹配管段 gid {r['gid']}：往下游 {r['downstream_n']} 段、{_fmt(r.get('downstream_km'), 2)} km 到终点（{r.get('terminal_kind') or '未知'}）；上游 {r.get('upstream_n')} 段、{_fmt(r.get('upstream_km'), 2)} km 汇入。"
        return f"{r['fp_id']} matched reach gid {r['gid']}: {r['downstream_n']} reaches / {_fmt(r.get('downstream_km'), 2)} km downstream to the terminal ({r.get('terminal_kind') or 'unknown'}); {r.get('upstream_n')} reaches / {_fmt(r.get('upstream_km'), 2)} km upstream."
