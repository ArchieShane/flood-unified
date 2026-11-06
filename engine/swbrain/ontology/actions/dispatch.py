"""应急调度、资源、重规划、压力测试、预置、路况——本体动作与中英小结。由 Runtime 多继承，方法体与拆分前逐字相同（ROOT 改成 self.root）。"""
from __future__ import annotations
from ..common import ActionError, OBJ_EN, OBJ_ZH, _f, _fmt, _traffic_brief, _v2_brief, clean, norm_fp_id


class DispatchActions:
    def _act_dispatch_plan(self, P):
        key = P["scenario"]
        dp = self._dispatch_payload(key, P)
        k = ((dp.get("kpis") or {}).get("solver") or {}); rows = [clean(r) for r in (dp.get("plan") or [])]
        if P.get("fp_id"):
            rows = [r for r in rows if str(r.get("fp_id")) == P["fp_id"]]
            st = self.load_states(key).get(P["fp_id"]) if P.get("decision_time_min") is None else None
        else:
            st = None
        keep = ("wave", "unit_id", "model", "fp_id", "from", "arrive_min", "pumping_from_min", "done_min", "rate_m3h", "relay_stages", "within_target", "target_drain_min")
        v2b = _v2_brief(dp.get("v2"))
        return {"scenario": self.scenario_object(key), "decision_time_min": dp.get("decision_time_min"), "kpis": k, "levels": (dp.get("levels") or {}).get("points"), "triage": dp.get("triage"), "cost": ((v2b or {}).get("cost") or dp.get("cost")), "tankers": {kk: (dp.get("tankers") or {}).get(kk) for kk in ("used", "pool")},
                "replay": {kk: (dp.get("replay_summary") or {}).get(kk) for kk in ("served_saved_pct", "served_flood_h_base", "served_flood_h_pump", "all_saved_pct")},
                "assignments": [{kk: r.get(kk) for kk in keep} for r in rows[:30]], "n_assignments": len(rows), "state": st, "v2": v2b, "traffic": _traffic_brief(dp.get("traffic")), "robust": bool(dp.get("robust")),
                "_ui": {"rp": key, "tab": "line3", "fp": P.get("fp_id"), "robust": bool(dp.get("robust"))}}

    def _act_replan(self, P):
        key = P["scenario"]
        if not hasattr(self.b, "replan"):
            raise ActionError("后端不支持重规划")
        dry = [x.strip() for x in str(P.get("dry_points") or "").split(",") if x.strip()]
        failed = [x.strip() for x in str(P.get("failed_units") or "").split(",") if x.strip()]
        inc = []
        for tok in [x.strip() for x in str(P.get("incidents") or "").split(";") if x.strip()]:
            ver = tok.endswith("!"); tok = tok.rstrip("!").strip()
            if "," in tok:
                try:
                    lon, lat = [float(v) for v in tok.split(",")[:2]]; inc.append({"lon": lon, "lat": lat, "verified": ver, "source": "agent"})
                except ValueError:
                    pass
            else:
                inc.append({"fp_id": norm_fp_id(tok) if tok else tok, "verified": ver, "source": "agent"})
        r = self.b.replan(key, float(P["t_now_min"]), dry, failed, inc, P.get("objective"))
        if r.get("error"):
            raise ActionError(r["error"])
        rp = r.get("replan") or {}; k = r.get("kpis") or {}; b = r.get("base_kpis") or {}
        rows = r.get("plan") or []
        new_rows = [x for x in rows if float(x.get("depart_min") or 0) >= float(P["t_now_min"])]
        return {"scenario": self.scenario_object(key), "t_now_min": float(P["t_now_min"]), "objective": r.get("objective"), "replan": {kk: rp.get(kk) for kk in ("locked_units", "released_units", "n_dropped", "dry_points", "failed_units", "incidents_matched", "incidents_unmatched", "points_new", "points_no_longer_served")},
                "kpis": k, "base_kpis": b, "cost": r.get("cost"), "n_new_assignments": len(new_rows), "new_assignments": [{kk: x.get(kk) for kk in ("unit_id", "model", "fp_id", "from", "depart_min", "arrive_min", "done_min", "package")} for x in new_rows[:30]],
                "unserved_reasons": r.get("unserved_reasons"), "_ui": {"rp": key, "tab": "line3", "highlight": self._hl([self.get("FloodPoint", f) for f in (rp.get("points_new") or [])[:20]], "points"), "fit": "highlight"}}

    def _sum_replan(self, r, P, lang):
        s = r["scenario"]; lab = s.get("label") if lang == "zh" else (s.get("label_en") or s.get("label")); rp = r["replan"]; k = r.get("kpis") or {}; b = r.get("base_kpis") or {}
        if lang == "zh":
            return (f"{lab}，第 {_fmt(r['t_now_min'])} min 重规划：锁定已出发 {rp.get('locked_units')} 台，报退干 {len(rp.get('dry_points') or [])} 点（释放 {len(rp.get('released_units') or [])} 台），故障 {len(rp.get('failed_units') or [])} 台，"
                    f"993 报案匹配到 {len(rp.get('incidents_matched') or {})} 个点（未匹配 {rp.get('incidents_unmatched')}）；作废未出发派单 {rp.get('n_dropped')} 条，新派 {r['n_new_assignments']} 条，新增处置点 {len(rp.get('points_new') or [])} 个，不再处置 {len(rp.get('points_no_longer_served') or [])} 个。"
                    f"重排后压住 {k.get('points_served')} 点（原 {b.get('points_served')}），III 级达标 {k.get('critical_within_target')}（原 {b.get('critical_within_target')}），费用 {_fmt((r.get('cost') or {}).get('total_aed'))} AED。")
        return (f"{lab}, replanned at minute {_fmt(r['t_now_min'])}: {rp.get('locked_units')} dispatched units locked, {len(rp.get('dry_points') or [])} points reported dry ({len(rp.get('released_units') or [])} units released), {len(rp.get('failed_units') or [])} failed units, "
                f"{len(rp.get('incidents_matched') or {})} points with 993 incidents ({rp.get('incidents_unmatched')} unmatched); {rp.get('n_dropped')} undispatched assignments dropped, {r['n_new_assignments']} new, {len(rp.get('points_new') or [])} newly treated points, {len(rp.get('points_no_longer_served') or [])} no longer treated. "
                f"Now {k.get('points_served')} points served (was {b.get('points_served')}), level III on target {k.get('critical_within_target')} (was {b.get('critical_within_target')}), cost {_fmt((r.get('cost') or {}).get('total_aed'))} AED.")

    def _act_stress_test(self, P):
        key = P["scenario"]
        if not hasattr(self.b, "stress_test"):
            raise ActionError("后端不支持压力测试")
        worse = P.get("worse_scenario")
        worse = (self.scenario_key(worse) if worse and str(worse).strip() else None)
        if worse == key:
            worse = None                                               # 同一情景没意义：按缺省取下一档更重的
        r = self.b.stress_test(key, worse, None, P.get("objective"))
        if r.get("error"):
            raise ActionError(r["error"])
        r["scenario"] = self.scenario_object(key)
        r["_ui"] = {"rp": key, "tab": "line3", "highlight": self._hl([self.get("FloodPoint", w["fp_id"]) for w in (r.get("worst") or [])[:10]], "points"), "fit": "highlight"}
        return r

    def _sum_stress_test(self, r, P, lang):
        s = r["scenario"]; lab = s.get("label") if lang == "zh" else (s.get("label_en") or s.get("label")); wl = r.get("worse_label") if lang == "zh" else (r.get("worse_label_en") or r.get("worse_label"))
        worst = "、".join(f"{w['fp_id']}（缺 {_fmt(w['rate_gap_m3h'])} m³/h ≈ {w['extra_12in_equiv']} 台 12 寸）" for w in (r.get("worst") or [])[:5] if w.get("rate_gap_m3h"))
        if lang == "zh":
            return (f"把 {lab} 的调度单放到 {wl}（24 h {_fmt(r.get('worse_p24_mm'), 1)} mm）上：{r['points_in_plan']} 个处置点里目标时限内达标从 {r.get('within_target_now')} 降到 {r['within_target_worse']}，48 h 内退不干 {r['never_empties_worse']} 个，"
                    f"{r['points_with_gap']} 个点排量不够，合计还差约 {r['extra_12in_equiv_total']} 台 12 寸泵当量。最缺的：{worst or '—'}。")
        worst_en = ", ".join(f"{w['fp_id']} (short {_fmt(w['rate_gap_m3h'])} m³/h ≈ {w['extra_12in_equiv']} × 12in)" for w in (r.get("worst") or [])[:5] if w.get("rate_gap_m3h"))
        return (f"Replaying the {lab} plan on {wl} ({_fmt(r.get('worse_p24_mm'), 1)} mm/24 h): of {r['points_in_plan']} treated points, on-target drops from {r.get('within_target_now')} to {r['within_target_worse']}, {r['never_empties_worse']} never drain within 48 h, "
                f"{r['points_with_gap']} lack pumping capacity, about {r['extra_12in_equiv_total']} extra 12-inch-equivalent pumps in total. Worst: {worst_en or '—'}.")

    def _act_staging_status(self, P):
        key = P["scenario"]
        if not hasattr(self.b, "staging_status"):
            raise ActionError("后端不支持预置到位盘点")
        r = self.b.staging_status(key, P.get("alert_level") or "橙")
        if r.get("error"):
            raise ActionError(r["error"])
        r["scenario"] = self.scenario_object(key); r["_ui"] = {"rp": key, "tab": "line2", "alert": P.get("alert_level") or "橙"}
        return r

    def _sum_staging_status(self, r, P, lang):
        s = r["scenario"]; lab = s.get("label") if lang == "zh" else (s.get("label_en") or s.get("label")); t = r["totals"]
        short = [x for x in r["rows"] if x["gap_vs_plan"] < 0]
        if lang == "zh":
            base = f"{lab} · {r['alert']}级：本系统建议预置 {t['need_units']} 台，客户预案 A 计划 {t['erp_plan_units']} 台（11/1 到位，plan only），"
            base += (f"现场 check-in {t['checked_in_units']} 台，预置到位率 {_fmt(t['readiness_pct'])}%。" if not r.get("plan_only") else "尚无承包商 check-in 数据（exchange/checkins.json 缺），到位率算不出——现在是 plan only。")
            if short:
                base += f" 计划台数低于需要的驻地 {len(short)} 个：" + "、".join(f"{x['name'].split('·')[0].strip()[:18]} 需要 {x['need_units']} / 计划 {x['erp_plan_units']}" for x in short[:5]) + "。"
            return base
        base = f"{lab} · {r['alert']}: this system suggests staging {t['need_units']} units, client Plan A plans {t['erp_plan_units']} (in place by 1 Nov, plan only), "
        base += (f"{t['checked_in_units']} checked in, readiness {_fmt(t['readiness_pct'])}%." if not r.get("plan_only") else "no contractor check-ins yet (exchange/checkins.json missing), so readiness cannot be computed: plan only.")
        if short:
            base += f" {len(short)} depots plan fewer than needed: " + ", ".join(f"{str(x['name']).split('·')[0].strip()[:18]} need {x['need_units']} / plan {x['erp_plan_units']}" for x in short[:5]) + "."
        return base

    # ---------------------------------------------------------------- 生命周期动作

    def _act_resource_check(self, P):
        key = P["scenario"]; dp = self._dispatch_payload(key, P); v2 = dp.get("v2") or {}
        if not v2:
            raise ActionError("该情景没有 v2 调度结果（[dispatch].solver 不是 v2，或调度上下文未装载）")
        res = v2.get("resources") or {}; util = v2.get("utilisation") or {}; sch = v2.get("schedule") or {}
        tot = res.get("totals") or {}
        used = {"units": sum(u.get("units_used", 0) for u in util.values()), "tankers": sum(u.get("tankers_used", 0) for u in util.values()), "crews": sum(u.get("crews_used", 0) for u in util.values()),
                "hose_m": sum(u.get("hose_used_m", 0) for u in util.values()), "fuel_l": sum(u.get("fuel_used_l", 0) for u in util.values())}
        gaps = {"persons_shortage": sch.get("persons_shortage", 0), "relief_persons_at_shift_change": sch.get("relief_crews_needed", 0),
                "depots_over_trip_capacity": [d for d, x in (sch.get("depots") or {}).items() if x.get("trips", 0) > sum((x.get("trip_capacity") or {}).values())]}
        return {"scenario": self.scenario_object(key), "objective": v2.get("objective"), "simulated": res.get("simulated"), "basis": res.get("basis"), "basis_en": res.get("basis_en"),
                "totals": tot, "used": used, "by_depot": [{"depot_id": d, **{k: v for k, v in u.items() if k != "vehicles"}, "vehicles": u.get("vehicles")} for d, u in util.items()],
                "gaps": gaps, "unserved_reasons": v2.get("unserved_reasons"), "cost": v2.get("cost"), "_ui": {"rp": key, "tab": "line3"}}

    def _sum_resource_check(self, r, P, lang):
        s = r["scenario"]; lab = s.get("label") if lang == "zh" else (s.get("label_en") or s.get("label")); t = r["totals"]; u = r["used"]; g = r["gaps"]
        sim = "（车辆 / 班组 / 物资为模拟值）" if r.get("simulated") else ""
        if lang == "zh":
            return (f"{lab} · {OBJ_ZH.get(r.get('objective'), r.get('objective'))}方案的资源占用{sim}：泵 {u['units']}/{t.get('units')} 台，罐车 {u['tankers']}/{t.get('tankers')} 辆，人员 {u['crews']}/{t.get('persons')} 人（当班 {t.get('on_duty')}），"
                    f"软管 {_fmt(u['hose_m'])}/{_fmt(t.get('hose_m'))} m，燃油 {_fmt(u['fuel_l'])}/{_fmt(t.get('fuel_l'))} L；运输车 重 {t.get('vehicles_heavy')} / 轻 {t.get('vehicles_light')} 辆。"
                    f"缺口：人手不足 {g['persons_shortage']} 人，12 h 换班需换 {g['relief_persons_at_shift_change']} 人" + (f"，车次超出能力的驻地 {len(g['depots_over_trip_capacity'])} 个" if g['depots_over_trip_capacity'] else "") + "。"
                    f"费用合计 {_fmt((r.get('cost') or {}).get('total_aed'))} AED。")
        return (f"{lab} · {OBJ_EN.get(r.get('objective'), r.get('objective'))} plan resource use{' (vehicles / crews / materials simulated)' if r.get('simulated') else ''}: pumps {u['units']}/{t.get('units')}, tankers {u['tankers']}/{t.get('tankers')}, staff {u['crews']}/{t.get('persons')} (on duty {t.get('on_duty')}), "
                f"hose {_fmt(u['hose_m'])}/{_fmt(t.get('hose_m'))} m, fuel {_fmt(u['fuel_l'])}/{_fmt(t.get('fuel_l'))} L; vehicles heavy {t.get('vehicles_heavy')} / light {t.get('vehicles_light')}. "
                f"Gaps: {g['persons_shortage']} staff short, {g['relief_persons_at_shift_change']} relief staff at the 12-h shift change" + (f", {len(g['depots_over_trip_capacity'])} depots over trip capacity" if g['depots_over_trip_capacity'] else "") + f". Total cost {_fmt((r.get('cost') or {}).get('total_aed'))} AED.")

    def _sum_dispatch_plan(self, r, P, lang):
        base = self._sum_dispatch_plan_base(r, P, lang); v2 = r.get("v2") or {}
        if not v2 or P.get("fp_id"):
            return base
        pa = v2.get("pareto") or []; c = v2.get("cost") or {}
        if lang == "zh":
            tail = f" 目标「{OBJ_ZH.get(v2.get('objective'), v2.get('objective'))}」，滚动 {v2.get('n_windows')} 个窗口；费用拆解：台时 {_fmt(c.get('duty_aed'))} + 人工 {_fmt(c.get('crew_aed'))} + 燃油 {_fmt(c.get('fuel_aed'))} + 车辆 {_fmt(c.get('vehicle_aed'))} + 罐车 {_fmt(c.get('tanker_aed'))} + 动员 {_fmt(c.get('mobilisation_aed'))}。"
            u = v2.get("used") or {}; tt = v2.get("totals") or {}
            if tt:
                tail += f" 资源占用：泵 {u.get('units')}/{tt.get('units')}、人员 {u.get('crews')}/{tt.get('persons')}、罐车 {u.get('tankers')}/{tt.get('tankers')}" + ("（人员是硬约束，已用满）" if tt.get('persons') and u.get('crews', 0) >= tt.get('persons', 0) else "") + ("；车辆 / 班组 / 物资为模拟值" if v2.get("resources_simulated") else "") + "。"
            if pa:
                tail += " 三方案：" + "；".join(f"{x['zh']} {_fmt(x['cost_aed'])} AED / 压住 {x['points_served']} 点 / 加权积水 {_fmt(x['weighted_flood_hours'])}" for x in pa) + "。"
            if v2.get("persons_shortage"):
                tail += f" 人手缺 {v2['persons_shortage']} 人。"
            sp = v2.get("silo_vs_pooled") or {}
            if sp and not sp.get("error"):
                tail += (f" 承包商分区：集中调度（可跨区开工单）比各管各的多压住 {sp.get('delta_points')} 个点，跨区调 {sp.get('cross_zone_units')} 台，费用差 {_fmt(sp.get('delta_cost_aed'))} AED"
                         f"（各管各的 {_fmt((sp.get('silo') or {}).get('points_served'))} 点 / {_fmt((sp.get('silo') or {}).get('cost_aed'))} AED）。")
            tail += self._robust_sentence(v2.get("robust"), "zh") + self._traffic_sentence(r.get("traffic"), "zh")
            return base + tail
        tail = f" Objective '{OBJ_EN.get(v2.get('objective'), v2.get('objective'))}', {v2.get('n_windows')} rolling windows; cost split: duty {_fmt(c.get('duty_aed'))} + crew {_fmt(c.get('crew_aed'))} + fuel {_fmt(c.get('fuel_aed'))} + vehicles {_fmt(c.get('vehicle_aed'))} + tankers {_fmt(c.get('tanker_aed'))} + mobilisation {_fmt(c.get('mobilisation_aed'))}."
        u = v2.get("used") or {}; tt = v2.get("totals") or {}
        if tt:
            tail += f" Resources: pumps {u.get('units')}/{tt.get('units')}, staff {u.get('crews')}/{tt.get('persons')}, tankers {u.get('tankers')}/{tt.get('tankers')}" + (" (staff is the binding constraint)" if tt.get('persons') and u.get('crews', 0) >= tt.get('persons', 0) else "") + ("; vehicles / crews / materials are simulated" if v2.get("resources_simulated") else "") + "."
        if pa:
            tail += " Three options: " + "; ".join(f"{x['en']} {_fmt(x['cost_aed'])} AED / {x['points_served']} points / weighted flood-hours {_fmt(x['weighted_flood_hours'])}" for x in pa) + "."
        sp = v2.get("silo_vs_pooled") or {}
        if sp and not sp.get("error"):
            tail += f" Contractor zones: pooled dispatch (cross-zone work orders) treats {sp.get('delta_points')} more points than contractors working in silos, moving {sp.get('cross_zone_units')} units across zones at a cost difference of {_fmt(sp.get('delta_cost_aed'))} AED (silo: {_fmt((sp.get('silo') or {}).get('points_served'))} points / {_fmt((sp.get('silo') or {}).get('cost_aed'))} AED)."
        tail += self._robust_sentence(v2.get("robust"), "en") + self._traffic_sentence(r.get("traffic"), "en")
        return base + tail

    def _act_traffic_status(self, P):
        if not hasattr(self.b, "traffic_status"):
            raise ActionError("后端不支持路况")
        r = self.b.traffic_status(refresh=bool(P.get("refresh")), depart=(str(P.get("depart_at")).strip() if P.get("depart_at") else None), key=P.get("scenario"))
        if (r.get("meta") or {}).get("error"):
            raise ActionError(r["meta"]["error"])
        r["scenario"] = self.scenario_object(P["scenario"]); r["_ui"] = {"rp": P["scenario"], "tab": "line3"}
        return r

    def _sum_traffic_status(self, r, P, lang):
        m = r.get("meta") or {}; st = r.get("status") or {}; prov = m.get("provider") or st.get("effective")
        top = [x for x in (r.get("by_depot") or []) if x.get("n")][:3]; rf = r.get("refine") or {}
        if lang == "zh":
            src = {"google": f"Google Routes（{m.get('routing_preference')}，本次请求 {m.get('elements')} 个起终点对）", "simulated": "模拟时段曲线（没有 Google 密钥或请求失败；页面标模拟）", "none": "关闭（系数 1）"}.get(prov, str(prov))
            s = f"路况来源：{src}；出发时刻 {m.get('departure_local')}；驻地拥堵系数 {_fmt(r.get('factor_min'), 2)}–{_fmt(r.get('factor_max'), 2)}（带路况行程 ÷ 自由流，驻地→点行程矩阵按行放大，点→点按均值）。"
            if top:
                s += " 最堵的驻地：" + "、".join(f"{str(x.get('name') or x['depot_id']).split('·')[0].strip()[:16]} ×{_fmt(x.get('factor'), 2)}" for x in top) + "。"
            ser = r.get("series") or {}
            if ser.get("mean_by_t"):
                s += f" 后续滚动窗口按出发时刻各取一次系数（每 {_fmt(ser.get('window_min'))} min 一档，全网均值 {_fmt(ser.get('min'), 2)}–{_fmt(ser.get('max'), 2)}），早晚高峰进了时间轴。"
            if rf.get("refined"):
                worst = [w for w in rf.get("worst") or [] if (w.get("delta_min") or 0) > 0][:3]
                s += f" 对当前调度单 {rf['n_pairs']} 条驻地→点行程逐对精算：平均偏差 {_fmt(rf.get('delta_mean_min'), 1)} min，{rf.get('n_slower_10min')} 条实际比模型慢 10 min 以上" + ("，最大：" + "、".join(f"{w['from']}→{w['fp_id']} +{_fmt(w['delta_min'], 0)} min" for w in worst) if worst else "") + "。"
            if m.get("note") and prov != "google":
                s += f" {m['note']}。"
            if not st.get("has_key"):
                s += f" 要用 Google 实时 / 预测路况：把密钥放进环境变量 {st.get('key_env')} 后重启服务（配置里 provider 已是 google；按起终点对计费，每次刷新约 驻地数 × {st.get('sample_per_depot')} 对）。"
            return s
        src = {"google": f"Google Routes ({m.get('routing_preference')}, {m.get('elements')} origin-destination elements this call)", "simulated": "simulated time-of-day curve (no Google key or the request failed; marked simulated)", "none": "off (factor 1)"}.get(prov, str(prov))
        s = f"Traffic source: {src}; departure {m.get('departure_local')}; depot congestion factors {_fmt(r.get('factor_min'), 2)}–{_fmt(r.get('factor_max'), 2)} (traffic ÷ free-flow; depot→point rows scaled, point→point by the mean)."
        if top:
            s += " Most congested depots: " + ", ".join(f"{str(x.get('name') or x['depot_id']).split('·')[0].strip()[:16]} ×{_fmt(x.get('factor'), 2)}" for x in top) + "."
        ser = r.get("series") or {}
        if ser.get("mean_by_t"):
            s += f" Later rolling windows each take their own factor by departure time (every {_fmt(ser.get('window_min'))} min, network mean {_fmt(ser.get('min'), 2)}–{_fmt(ser.get('max'), 2)}), so rush hours enter the timeline."
        if rf.get("refined"):
            worst = [w for w in rf.get("worst") or [] if (w.get("delta_min") or 0) > 0][:3]
            s += f" Pair-by-pair check of {rf['n_pairs']} depot→point trips in the current plan: mean deviation {_fmt(rf.get('delta_mean_min'), 1)} min, {rf.get('n_slower_10min')} trips more than 10 min slower than modelled" + (", worst: " + ", ".join(f"{w['from']}→{w['fp_id']} +{_fmt(w['delta_min'], 0)} min" for w in worst) if worst else "") + "."
        if m.get("note") and prov != "google":
            s += f" {m['note']}."
        if not st.get("has_key"):
            s += f" For Google live / predictive traffic, put the key in the environment variable {st.get('key_env')} and restart the service (provider is already google; billed per element, about depots × {st.get('sample_per_depot')} per refresh)."
        return s

    def _sum_dispatch_plan_base(self, r, P, lang):
        s = r["scenario"]; k = r["kpis"]; lab = s.get("label") if lang == "zh" else (s.get("label_en") or s.get("label"))
        if P.get("fp_id"):
            rows = r["assignments"]
            if not rows:
                st = r.get("state") or {}
                if not st:
                    return (f"{lab}：{P['fp_id']} 不在该情景的积水点清单里（没算出积水过程）。" if lang == "zh" else f"{lab}: {P['fp_id']} is not in this scenario's flood point list (no flood state).")
                tri = str(st.get("triage") or "")
                if tri.startswith("可派泵") and lang == "zh":
                    return (f"{lab}，决策时刻 {_fmt(r['decision_time_min'])} min：{P['fp_id']} 可派但本轮没排上——{tri}，需排量 {_fmt(st.get('required_rate_m3h'))} m³/h（决策时刻还有 {_fmt(st.get('volume_now_m3'))} m³），优先级分 {_fmt(st.get('priority'), 1)}；"
                            f"本轮 {k.get('units_used')} 台泵分给了 {k.get('points_served')} 个点（求解器按全网加权积水时长最优，不是按单点排队，需排量大、接力多的点容易轮空）。要保它，走预置或加泵。")
                if tri.startswith("可派泵"):
                    return (f"{lab}, decision at {_fmt(r['decision_time_min'])} min: {P['fp_id']} is dispatchable but did not make this round — {tri}, needs {_fmt(st.get('required_rate_m3h'))} m³/h ({_fmt(st.get('volume_now_m3'))} m³ left at decision time), priority {_fmt(st.get('priority'), 1)}; "
                            f"the {k.get('units_used')} pumps went to {k.get('points_served')} points (the solver minimises network-wide weighted flood-hours rather than queueing by point, so points needing many relayed pumps often miss out). Pre-position or add units to cover it.")
                return (f"{lab}，决策时刻 {_fmt(r['decision_time_min'])} min：{P['fp_id']} 没有派单——{tri}。" if lang == "zh" else f"{lab}, decision at {_fmt(r['decision_time_min'])} min: no assignment for {P['fp_id']} — {tri}.")
            u = "、".join(f"{x['unit_id']}（{str(x['model']).replace('diesel-', '')}，{x['from']}，{_fmt(x['arrive_min'])} min 到）" for x in rows) if lang == "zh" else ", ".join(f"{x['unit_id']} ({str(x['model']).replace('diesel-', '')}, from {x['from']}, arrives {_fmt(x['arrive_min'])} min)" for x in rows)
            done = max(_f(x.get("done_min"), 0) for x in rows); ok = all(x.get("within_target") for x in rows); tgt = rows[0].get("target_drain_min"); relay = max(int(_f(x.get("relay_stages"), 0) or 0) for x in rows)
            if lang == "zh":
                return f"{lab}，决策时刻 {_fmt(r['decision_time_min'])} min：{P['fp_id']} 派 {len(rows)} 台：{u}；开泵后合计 {_fmt(sum(_f(x.get('rate_m3h'), 0) for x in rows))} m³/h{f'（{relay} 级接力折减后）' if relay else ''}，{_fmt(done)} min 排干，目标 {_fmt(tgt)} min，{'达标' if ok else '未达标'}。"
            return f"{lab}, decision at {_fmt(r['decision_time_min'])} min: {P['fp_id']} gets {len(rows)} unit(s): {u}; combined {_fmt(sum(_f(x.get('rate_m3h'), 0) for x in rows))} m³/h{f' (after {relay}-stage relay derating)' if relay else ''}, drained at {_fmt(done)} min vs target {_fmt(tgt)} min, {'within target' if ok else 'late'}."
        by = r.get("levels") or {}; rp = r.get("replay") or {}; tk = r.get("tankers") or {}; cost = r.get("cost") or {}
        if lang == "zh":
            return (f"{lab}，决策时刻 {_fmt(r['decision_time_min'])} min：{k.get('points_total')} 个积水点（III {by.get('III', 0)} / II {by.get('II', 0)} / I {by.get('I', 0)}），派泵 {k.get('units_used')} 台覆盖 {k.get('points_served')} 点，"
                    f"III 级达标 {k.get('critical_within_target')}/{k.get('critical_total')}，首台到位中位 {_fmt(k.get('first_arrival_median_min'))} min；罐车 {tk.get('used')}/{tk.get('pool')}；被派点积水时长少 {rp.get('served_saved_pct')}%（{_fmt(rp.get('served_flood_h_base'))}→{_fmt(rp.get('served_flood_h_pump'))} h），"
                    f"费用约 {_fmt(cost.get('total_aed'))} AED。分诊：{'；'.join(f'{a} {b}' for a, b in (r.get('triage') or {}).items())}。")
        return (f"{lab}, decision at {_fmt(r['decision_time_min'])} min: {k.get('points_total')} flood points (III {by.get('III', 0)} / II {by.get('II', 0)} / I {by.get('I', 0)}), {k.get('units_used')} pumps serve {k.get('points_served')} points, "
                f"level III within target {k.get('critical_within_target')}/{k.get('critical_total')}, median first arrival {_fmt(k.get('first_arrival_median_min'))} min; tankers {tk.get('used')}/{tk.get('pool')}; served points flood {rp.get('served_saved_pct')}% fewer hours ({_fmt(rp.get('served_flood_h_base'))}→{_fmt(rp.get('served_flood_h_pump'))} h), "
                f"cost about {_fmt(cost.get('total_aed'))} AED. Triage: {'; '.join(f'{a} {b}' for a, b in (r.get('triage') or {}).items())}.")

    def _act_preposition_plan(self, P):
        key = P["scenario"]; lvl = P.get("alert_level") or "红"
        dp = (self.bundle(key) or {}).get("dispatch") or {}
        pre = (dp.get("preposition") or {}).get(lvl)
        if not pre:
            raise ActionError(f"该情景没有 {lvl} 级预置结果")
        sites = [{"site_id": s.get("site_id"), "name": s.get("name"), "units_suggested": s.get("units_suggested"), "n_covers": len(s.get("covers") or []), "covers": list(s.get("covers") or [])[:12]} for s in pre.get("sites") or []]
        v = pre.get("value") or {}
        return {"scenario": self.scenario_object(key), "alert_level": lvl, "manual": pre.get("manual"), "cover_min": pre.get("cover_min"), "points": pre.get("points"), "covered": pre.get("covered"), "sites": sites,
                "value": {kk: v.get(kk) for kk in ("units_placed", "arrive_median_min", "saved_vs_after_pct", "within_target", "level_iii_served", "cost", "served_flood_h")}, "_ui": {"rp": key, "tab": "line2", "alert": lvl}}

    def _sum_preposition_plan(self, r, P, lang):
        s = r["scenario"]; lab = s.get("label") if lang == "zh" else (s.get("label_en") or s.get("label")); v = r["value"] or {}; wt = v.get("within_target") or {}; cost = (v.get("cost") or {}).get("total_aed")
        names = "、".join(f"{x['name']}（{x['units_suggested']} 台，盖 {x['n_covers']} 点）" for x in r["sites"][:6]) if lang == "zh" else ", ".join(f"{x['name']} ({x['units_suggested']} units, {x['n_covers']} pts)" for x in r["sites"][:6])
        if lang == "zh":
            return f"{lab}，{r['alert_level']}级预警：预置 {len(r['sites'])} 个驻地，{r['cover_min']} min 内覆盖 {r['covered']}/{r['points']} 个点：{names}{'…' if len(r['sites']) > 6 else ''}。预置 {v.get('units_placed')} 台，到位中位 {_fmt(v.get('arrive_median_min'))} min，比事后再派少积水 {v.get('saved_vs_after_pct')}%，达标 {wt.get('pre')}/{wt.get('pre_n')}，费用约 {_fmt(cost)} AED。"
        return f"{lab}, {r['alert_level']} alert: {len(r['sites'])} staging sites cover {r['covered']}/{r['points']} points within {r['cover_min']} min: {names}{'…' if len(r['sites']) > 6 else ''}. {v.get('units_placed')} units pre-positioned, median arrival {_fmt(v.get('arrive_median_min'))} min, {v.get('saved_vs_after_pct')}% fewer flood-hours than dispatching after the event, within target {wt.get('pre')}/{wt.get('pre_n')}, cost about {_fmt(cost)} AED."
