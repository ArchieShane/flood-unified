"""降雨事件与积水经历：历史、复盘、漏斗、登记、关闭、工程建议状态——本体动作与中英小结。由 Runtime 多继承，方法体与拆分前逐字相同（ROOT 改成 self.root）。"""
from __future__ import annotations
from .. import schema as S
from ... import scenarios
from ..common import ActionError, _fmt


class LifecycleActions:
    def _act_point_history(self, P):
        fp = P["fp_id"]; o = self.get("FloodPoint", fp)
        if o is None:
            raise ActionError(f"没有积水点 {fp}")
        h = self.point_lifecycle(fp)
        props = self._proposals_of(fp, o)
        rules = [x for x in self.rules("FloodPoint", o, None) if x["rule"] in ("recurrence", "history_vs_model", "repeat_offender", "treatment_effective", "treatment_status", "lifecycle_status")]
        return {"point": {k: o.get(k) for k in ("fp_id", "location", "region", "area", "criticality", "idd_planned_flag", "idd_stage", "lon", "lat", "lifecycle_status", "treatment_status", "recurrence", "recurrence_model", "last_flooded")},
                "history": h, "proposals": [{k: p.get(k) for k in ("pid", "title", "title_en", "kind", "status", "status_at", "status_note")} for p in props[:6]], "rules": rules,
                "current_event": h.get("current"), "_ui": {"hs": fp, "highlight": self._hl([o], "points"), "fit": "highlight"}}

    def _sum_point_history(self, r, P, lang):
        from ... import lifecycle as LC
        o = r["point"]; h = r["history"]; rows = h.get("history") or []
        fired = [x for x in r.get("rules") or [] if x.get("fired")]
        if lang == "zh":
            if not rows:
                return f"{o['fp_id']}（{o.get('location') or ''}）：目录里还没有任何历史降雨事件。"
            parts = []
            for x in rows:
                rec = ("有记录积水" + (f"，最大水深 {x['obs_depth_max_m']} m" if x.get("obs_depth_max_m") is not None else "") + (f"，{_fmt(x['obs_duration_min'])} min" if x.get("obs_duration_min") is not None else "") + (f"，实际派 {x['units_sent']} 台" if x.get("units_sent") is not None else "")) if x.get("observed_flooded") else ("记录无积水" if x.get("observed_flooded") is False else ("没有逐点记录，但所在片区（" + "、".join(x.get("area_report_names") or []) + "）有公开报道积水" if x.get("area_reported") else "没有现场记录"))
                mod = ("模型推演积水" + (f" {x['model_depth_m']} m / {_fmt(x['model_duration_min'])} min" if x.get("model_depth_m") is not None else "")) if x.get("model_flooded") else ("模型不积水" if x.get("model_flooded") is False else "模型没算这场")
                parts.append(f"{x.get('label') or x['event_id']}（{x.get('date')}，24 h {_fmt(x.get('p24_mm'), 0)} mm ≈ {_fmt(x.get('rp_equiv_yr'), 0)} 年一遇）：{rec}；{mod}；{LC.AGREEMENT_ZH.get(x.get('agreement'), '')}；阶段 {x.get('status_zh')}")
            s = f"{o['fp_id']}（{o.get('location') or ''}，{o.get('criticality') or ''}）的历史：" + "。".join(parts) + "。"
            s += f" 复发率 {h.get('recurrence') or '—（没有现场记录）'}" + (f"，模型口径 {h.get('recurrence_model')}" if h.get("recurrence_model") else "") + f"；当前 {h.get('lifecycle_status_zh')}；工程治理 {LC.TREATMENT.get(o.get('treatment_status'), o.get('treatment_status'))}" + (f"（{len(r.get('proposals') or [])} 条建议，首条「{(r['proposals'][0].get('title') or '')[:30]}」{r['proposals'][0].get('status') or ''}）" if r.get("proposals") else "") + "。"
            if fired:
                s += " 触发：" + "；".join(f"{x['zh']}（{(x.get('value') or {}).get('why', '')}）" for x in fired) + "。"
            if not h.get("n_observed"):
                s += " 注意：历史两场的逐点积水记录客户还没给（契约 exchange/episodes.json），现在「积水 / 不积水」是模型对当时雨量的推演，标为推演估计。"
            return s
        if not rows:
            return f"{o['fp_id']} ({o.get('location') or ''}): no historical rain events in the catalogue yet."
        parts = []
        for x in rows:
            rec = ("recorded flooded" + (f", max depth {x['obs_depth_max_m']} m" if x.get("obs_depth_max_m") is not None else "") + (f", {_fmt(x['obs_duration_min'])} min" if x.get("obs_duration_min") is not None else "") + (f", {x['units_sent']} pumps sent" if x.get("units_sent") is not None else "")) if x.get("observed_flooded") else ("recorded dry" if x.get("observed_flooded") is False else ("no per-point record, but its area (" + ", ".join(x.get("area_report_names") or []) + ") was named in public flood reports" if x.get("area_reported") else "no field record"))
            mod = ("model floods it" + (f" ({x['model_depth_m']} m / {_fmt(x['model_duration_min'])} min)" if x.get("model_depth_m") is not None else "")) if x.get("model_flooded") else ("model dry" if x.get("model_flooded") is False else "model not run")
            parts.append(f"{x.get('label_en') or x.get('label') or x['event_id']} ({x.get('date')}, {_fmt(x.get('p24_mm'), 0)} mm/24 h ≈ {_fmt(x.get('rp_equiv_yr'), 0)}-yr): {rec}; {mod}; {LC.AGREEMENT_EN.get(x.get('agreement'), '')}; stage {x.get('status_en')}")
        s = f"History of {o['fp_id']} ({o.get('location') or ''}, {o.get('criticality') or ''}): " + ". ".join(parts) + "."
        s += f" Recurrence {h.get('recurrence') or 'n/a (no field records)'}" + (f", model-based {h.get('recurrence_model')}" if h.get("recurrence_model") else "") + f"; current status {h.get('lifecycle_status_en')}; treatment {LC.TREATMENT_EN.get(o.get('treatment_status'), o.get('treatment_status'))}" + (f" ({len(r.get('proposals') or [])} proposal(s), first '{(r['proposals'][0].get('title_en') or r['proposals'][0].get('title') or '')[:30]}' {r['proposals'][0].get('status') or ''})" if r.get("proposals") else "") + "."
        if fired:
            s += " Fired: " + "; ".join(f"{x['en']} ({(x.get('value') or {}).get('why_en') or (x.get('value') or {}).get('why', '')})" for x in fired) + "."
        if not h.get("n_observed"):
            s += " Note: per-point flooding records for the two historical events have not been supplied yet (contract exchange/episodes.json); flooded / dry here is the model's estimate for that rain, marked as estimate."
        return s

    def _act_event_review(self, P):
        from ... import lifecycle as LC
        eid = self.event_key(P.get("event_id"), prefer_open=False) if P.get("event_id") else self.event_key(None, prefer_open=True)
        ev = self.event_object(eid); eps = self.episodes_in(eid)
        by_status = {}; by_region = {}
        ix = self._fp_index()
        for e in eps:
            by_status[e["status"]] = by_status.get(e["status"], 0) + 1
            reg = str((ix.get(e["fp_id"]) or {}).get("region") or "?")
            by_region.setdefault(reg, {"episodes": 0, "observed_flooded": 0, "model_flooded": 0})
            by_region[reg]["episodes"] += 1; by_region[reg]["observed_flooded"] += 1 if e.get("observed_flooded") else 0; by_region[reg]["model_flooded"] += 1 if e.get("model_flooded") else 0
        v = ev.get("_verification") or LC.verification(eps, self.cfg)
        misses = [e for e in eps if e["agreement"] == "miss"]; fas = [e for e in eps if e["agreement"] == "false_alarm"]
        hist = sorted([x for x in self.load_events().values() if x.get("kind", "historical") == "historical"], key=LC.sort_key)
        fails = 0
        for x in reversed(hist):
            vv = LC.verification(self.episodes_in(x["event_id"]), self.cfg)
            if vv["verdict"] == "fail":
                fails += 1
            else:
                break
        hl = [{**(ix.get(e["fp_id"]) or {}), "fp_id": e["fp_id"]} for e in (misses + fas)[:20]]
        return {"event": {k: ev.get(k) for k in ("event_id", "kind", "label", "label_en", "date", "start", "end", "p24_mm", "rp_equiv_yr", "scenario_key", "alert_level", "source", "origin", "n_episodes", "n_observed_flooded", "n_model_flooded")},
                "verification": v, "by_status": by_status, "by_status_zh": {LC.STAGE_ZH.get(k, k): n for k, n in by_status.items()}, "by_region": by_region,
                "misses": [{"fp_id": e["fp_id"], "obs_depth_max_m": e.get("obs_depth_max_m"), "sources": e.get("sources")} for e in misses[:12]], "false_alarms": [{"fp_id": e["fp_id"], "model_depth_m": e.get("model_depth_m")} for e in fas[:12]],
                "consecutive_failures": fails, "recalibrate": fails >= int(v.get("recalibrate_after_failures", 2)),
                "_ui": {"rp": (ev.get("scenario_key") if str(ev.get("scenario_key")) in self.scenario_keys() else None), "highlight": self._hl(hl, "points"), "fit": ("highlight" if hl else None)}}

    def _sum_event_review(self, r, P, lang):
        from ... import lifecycle as LC
        e = r["event"]; v = r["verification"]; bs = r["by_status"]
        lab = e.get("label") if lang == "zh" else (e.get("label_en") or e.get("label"))
        if lang == "zh":
            s = f"{lab}（{e.get('date') or e.get('start')}，缺省片区 24 h {_fmt(e.get('p24_mm'), 0)} mm ≈ {_fmt(e.get('rp_equiv_yr'), 0)} 年一遇，对应情景 {e.get('scenario_key')}）：{e.get('n_episodes')} 个积水点有经历，模型推演积水 {e.get('n_model_flooded')} 个，有记录积水 {e.get('n_observed_flooded')} 个。"
            s += " 阶段分布：" + "、".join(f"{LC.STAGE_ZH.get(k, k)} {n}" for k, n in sorted(bs.items(), key=lambda kv: -kv[1])) + "。"
            if v["n_observed"]:
                s += f" 对账（{v['n_observed']} 个有记录）：命中 {v['hits']}、漏报 {v['misses']}、误报 {v['false_alarms']}，POD {_fmt(v['pod'], 2)}、FAR {_fmt(v['far'], 2)}" + (f"、水深 RMSE {v['depth_rmse_m']} m" if v.get("depth_rmse_m") is not None else "") + f"，结论 {v['verdict']}（阈值 POD ≥ {v['thresholds']['pod_min']}、FAR ≤ {v['thresholds']['far_max']}、至少 {v['thresholds']['min_observed']} 个记录）。"
                if r["misses"]:
                    s += " 漏报：" + "、".join(x["fp_id"] for x in r["misses"][:8]) + "。"
                if r["false_alarms"]:
                    s += " 误报：" + "、".join(x["fp_id"] for x in r["false_alarms"][:8]) + "。"
                if r.get("recalibrate"):
                    s += f" 已连续 {r['consecutive_failures']} 场不合格，按 [verification] 该触发模型重校准。"
            else:
                s += " 这场雨还没有任何逐点的现场记录，对账做不了（insufficient）；客户的投诉 / 巡查 / 993 台账落成 exchange/episodes.json 就能算命中率与漏报。"
            return s
        s = f"{lab} ({e.get('date') or e.get('start')}, default {_fmt(e.get('p24_mm'), 0)} mm/24 h ≈ {_fmt(e.get('rp_equiv_yr'), 0)}-yr, scenario {e.get('scenario_key')}): {e.get('n_episodes')} points have an episode, model floods {e.get('n_model_flooded')}, records show {e.get('n_observed_flooded')} flooded."
        s += " Stages: " + ", ".join(f"{LC.STAGE_EN.get(k, k)} {n}" for k, n in sorted(bs.items(), key=lambda kv: -kv[1])) + "."
        if v["n_observed"]:
            s += f" Verification ({v['n_observed']} recorded): hits {v['hits']}, misses {v['misses']}, false alarms {v['false_alarms']}, POD {_fmt(v['pod'], 2)}, FAR {_fmt(v['far'], 2)}" + (f", depth RMSE {v['depth_rmse_m']} m" if v.get("depth_rmse_m") is not None else "") + f", verdict {v['verdict']} (POD ≥ {v['thresholds']['pod_min']}, FAR ≤ {v['thresholds']['far_max']}, ≥ {v['thresholds']['min_observed']} records)."
            if r["misses"]:
                s += " Misses: " + ", ".join(x["fp_id"] for x in r["misses"][:8]) + "."
            if r["false_alarms"]:
                s += " False alarms: " + ", ".join(x["fp_id"] for x in r["false_alarms"][:8]) + "."
            if r.get("recalibrate"):
                s += f" {r['consecutive_failures']} consecutive failures: recalibration is due per [verification]."
        else:
            s += " No per-point field records for this rain yet, so verification is insufficient; once the client's complaint / inspection / 993 logs land in exchange/episodes.json, POD and misses can be computed."
        return s

    def _act_lifecycle_overview(self, P):
        from ... import lifecycle as LC
        eid = self.event_key(P.get("event_id"), prefer_open=True)
        ev = self.event_object(eid); eps = self.episodes_in(eid); ix = self._fp_index()
        order = ["none", "estimated"] + list(LC.STAGES)
        by_status = {k: 0 for k in order}; by_region = {}
        for e in eps:
            by_status[e["status"]] = by_status.get(e["status"], 0) + 1
            reg = str((ix.get(e["fp_id"]) or {}).get("region") or "?")
            by_region.setdefault(reg, {k: 0 for k in order}); by_region[reg][e["status"]] = by_region[reg].get(e["status"], 0) + 1
        funnel = [{"status": k, "zh": LC.STAGE_ZH.get(k, k), "en": LC.STAGE_EN.get(k, k), "n": by_status.get(k, 0)} for k in order if by_status.get(k, 0)]
        active = [e for e in eps if e["status"] in ("reported", "verified", "dispatched", "arrived", "pumping")]
        return {"event": {k: ev.get(k) for k in ("event_id", "kind", "label", "label_en", "date", "p24_mm", "scenario_key")}, "funnel": funnel, "by_region": by_region, "n_episodes": len(eps),
                "n_active": len(active), "n_closed": by_status.get("closed", 0) + by_status.get("reviewed", 0), "active": [{"fp_id": e["fp_id"], "status": e["status"], "status_zh": e["status_zh"]} for e in active[:20]],
                "_ui": {"rp": (ev.get("scenario_key") if str(ev.get("scenario_key")) in self.scenario_keys() else None), "tab": "line3", "highlight": self._hl([{**(ix.get(e["fp_id"]) or {}), "fp_id": e["fp_id"]} for e in active[:30]], "points")}}

    def _sum_lifecycle_overview(self, r, P, lang):
        e = r["event"]; lab = e.get("label") if lang == "zh" else (e.get("label_en") or e.get("label"))
        f = r["funnel"]
        if lang == "zh":
            s = f"{lab}（{e.get('kind')}）：{r['n_episodes']} 个积水点有经历——" + "、".join(f"{x['zh']} {x['n']}" for x in f) + f"。处置中 {r['n_active']} 个，已关闭 / 复盘 {r['n_closed']} 个。"
            if e.get("kind") == "historical":
                s += " 历史事件的阶段只到记录能证明的那一步（多数是「推演估计」或「已核实」）；进行中的事件用 update_episode 逐步登记。"
            return s
        s = f"{lab} ({e.get('kind')}): {r['n_episodes']} points with an episode: " + ", ".join(f"{x['en']} {x['n']}" for x in f) + f". {r['n_active']} active, {r['n_closed']} closed / reviewed."
        if e.get("kind") == "historical":
            s += " For historical events stages only go as far as records prove (mostly 'model estimate' or 'verified'); live events are advanced with update_episode."
        return s

    def _act_update_episode(self, P):
        from ... import lifecycle as LC
        fp = P["fp_id"]
        if self.get("FloodPoint", fp) is None:
            raise ActionError(f"没有积水点 {fp}")
        eid = self.event_key(P.get("event_id"), prefer_open=True)
        ev = self.load_events().get(eid) or {}
        if ev.get("kind", "historical") == "historical" and not P.get("event_id"):
            raise ActionError("现在没有开着的预报 / 进行中事件；要给历史事件补记录请明确 event_id（如 2024-04），批量补录用 exchange/episodes.json")
        at = str(P.get("at") or LC.now_iso())
        rec = {"fp_id": fp, "event_id": eid, "stage": P["stage"], "at": at, "flooded": (True if P["stage"] in ("reported", "verified", "dispatched", "arrived", "pumping", "dry") else None),
               "depth_m": P.get("depth_m"), "units": P.get("units"), "note": P.get("note"), "source": "log", "by": "agent", "logged_at": LC.now_iso()}
        LC.append_jsonl(LC._path(self.cfg, "episodes_file"), {k: v for k, v in rec.items() if v is not None}); self._invalidate_lifecycle()
        ep = self.load_episodes().get(f"{fp}@{eid}")
        fired = [x for x in self.rules("FloodEpisode", ep, None) if x.get("fired")] if ep else []
        return {"record": rec, "episode": ep, "event": {k: ev.get(k) for k in ("event_id", "kind", "label", "label_en")}, "rules_fired": fired, "file": LC._path(self.cfg, "episodes_file").relative_to(self.root).as_posix() if str(LC._path(self.cfg, "episodes_file")).startswith(str(self.root)) else str(LC._path(self.cfg, "episodes_file")), "_ui": {"hs": fp}}

    def _sum_update_episode(self, r, P, lang):
        from ... import lifecycle as LC
        rec = r["record"]; ep = r.get("episode") or {}; ev = r["event"]
        tail = "；".join(f"{x['zh' if lang == 'zh' else 'en']}（{(x.get('value') or {}).get('why' if lang == 'zh' else 'why_en', '')}）" for x in r.get("rules_fired") or [])
        if lang == "zh":
            return f"已登记 {rec['fp_id']} 在 {ev.get('label') or ev['event_id']} 的阶段「{LC.STAGE_ZH.get(rec['stage'])}」（{rec['at']}" + (f"，水深 {rec['depth_m']} m" if rec.get("depth_m") is not None else "") + (f"，派 {rec['units']} 台" if rec.get("units") is not None else "") + f"）→ {r['file']}；该经历现在处于「{ep.get('status_zh')}」" + (f"，触发：{tail}" if tail else "") + "。"
        return f"Logged stage '{LC.STAGE_EN.get(rec['stage'])}' for {rec['fp_id']} in {ev.get('label_en') or ev.get('label') or ev['event_id']} ({rec['at']}" + (f", depth {rec['depth_m']} m" if rec.get("depth_m") is not None else "") + (f", {rec['units']} units" if rec.get("units") is not None else "") + f") → {r['file']}; the episode is now '{ep.get('status_en')}'" + (f"; fired: {tail}" if tail else "") + "."

    def _act_close_point(self, P):
        from ... import lifecycle as LC
        fp = P["fp_id"]; eid = self.event_key(P.get("event_id"), prefer_open=True)
        ep = self.load_episodes().get(f"{fp}@{eid}")
        if ep is None:
            raise ActionError(f"{fp} 在事件 {eid} 里没有经历，无从关闭")
        if ep["status"] in ("closed", "reviewed"):
            raise ActionError(f"{fp} 在 {eid} 已是「{ep['status_zh']}」")
        ready = next((x for x in self.rules("FloodEpisode", ep, None) if x["rule"] == "ready_to_close" and x.get("fired")), None)
        if not ready:
            raise ActionError(f"{fp} 在 {eid} 还没有退干记录（阶段 {ep['status_zh']}）：先登记 dry 阶段或现场观测 dry，再关闭")
        at = LC.now_iso()
        LC.append_jsonl(LC._path(self.cfg, "episodes_file"), {"fp_id": fp, "event_id": eid, "stage": "closed", "at": at, "note": P.get("note"), "source": "log", "by": "agent", "logged_at": at}); self._invalidate_lifecycle()
        ep2 = self.load_episodes().get(f"{fp}@{eid}")
        return {"fp_id": fp, "event_id": eid, "closed_at": at, "episode": ep2, "was": ep["status"], "_ui": {"hs": fp}}

    def _sum_close_point(self, r, P, lang):
        from ... import lifecycle as LC
        if lang == "zh":
            return f"已关闭 {r['fp_id']} 在 {r['event_id']} 的这次经历（{r['closed_at']}，之前是「{LC.STAGE_ZH.get(r['was'], r['was'])}」）。点本身与历史保留；下一场雨再有记录会另起一次经历。"
        return f"Closed the episode of {r['fp_id']} in {r['event_id']} at {r['closed_at']} (was '{r['was']}'). The point and its history remain; the next rain starts a new episode."

    def _act_set_proposal_status(self, P):
        from ... import lifecycle as LC
        pid = int(P["pid"]); p = self.get("Proposal", pid)
        if p is None:
            raise ActionError(f"没有建议 {pid}")
        at = LC.now_iso()
        LC.append_jsonl(LC._path(self.cfg, "proposal_status_file"), {"pid": pid, "status": P["status"], "at": at, "note": P.get("note"), "by": "agent"}); self._invalidate_lifecycle()
        p2 = self.get("Proposal", pid)
        fps = [f for f, r in self._fp_index().items() if p2.get("client_hotspot_id") == f or (p2.get("gid") is not None and r.get("edge_gid") == p2.get("gid"))]
        pts = [{"fp_id": f, "treatment_status": LC.treatment_status(self._proposals_of(f, self._fp_index().get(f)))} for f in fps[:12]]
        return {"proposal": {k: p2.get(k) for k in ("pid", "title", "title_en", "kind", "status", "status_at", "status_note", "client_hotspot_id", "gid")}, "was": p.get("status"), "points": pts, "_ui": {"tab": "line1", "pid": pid}}

    def _sum_set_proposal_status(self, r, P, lang):
        from ... import lifecycle as LC
        p = r["proposal"]; pts = r.get("points") or []
        if lang == "zh":
            return f"建议 {p['pid']}「{(p.get('title') or '')[:40]}」状态 {r.get('was') or '建议'} → {p['status']}（{p.get('status_at')}）。" + (f" 相关积水点 {len(pts)} 个的工程治理状态变为 {LC.TREATMENT.get(pts[0]['treatment_status'])}：" + "、".join(x['fp_id'] for x in pts[:8]) + "。" if pts else "") + (" 建成后要等下一场雨的记录验证「治理是否见效」。" if p["status"] == "已建成" else "")
        return f"Proposal {p['pid']} '{(p.get('title_en') or p.get('title') or '')[:40]}' status {r.get('was') or 'proposed'} → {p['status']} ({p.get('status_at')})." + (f" Treatment status of {len(pts)} related point(s) is now {LC.TREATMENT_EN.get(pts[0]['treatment_status'])}: " + ", ".join(x['fp_id'] for x in pts[:8]) + "." if pts else "") + (" Effectiveness will be verified by the next rain's records." if p["status"] == "已建成" else "")

    def _act_open_event(self, P):
        from ... import lifecycle as LC
        date = str(P["date"]).strip(); p24 = float(P["p24_mm"])
        eid = str(P.get("event_id") or f"EV-{date.replace('-', '')}").strip()
        if eid in self.load_events():
            raise ActionError(f"事件 {eid} 已存在")
        if P.get("scenario"):
            sk = P["scenario"]
        else:
            cat = [c for c in scenarios.catalog(self.cfg) if c.get("kind") in ("rp", "rp_cc") and c.get("p24_mm")]
            sk = min(cat, key=lambda c: abs(float(c["p24_mm"]) - p24))["key"] if cat else self.scenario_key(None)
        lvl = S.r_alert_level({"p24_mm": p24}, {"cfg": self.cfg})
        rec = {"event_id": eid, "kind": P.get("kind") or "forecast", "label": P["label"], "date": date, "p24_mm": p24, "scenario_key": str(sk), "alert_level": lvl, "opened_at": LC.now_iso(), "opened_by": "agent", "source": "open_event"}
        LC.append_jsonl(LC._path(self.cfg, "events_file"), rec); self._invalidate_lifecycle()
        ev = self.event_object(eid); eps = self.episodes_in(eid)
        return {"event": ev, "n_alerted": sum(1 for e in eps if e["status"] == "alerted"), "n_episodes": len(eps), "_ui": {"rp": str(sk)}}

    def _sum_open_event(self, r, P, lang):
        e = r["event"]
        if lang == "zh":
            return f"已开启事件 {e['event_id']}「{e.get('label')}」（{e.get('kind')}，{e.get('date')}，24 h {_fmt(e.get('p24_mm'), 0)} mm ≈ {_fmt(e.get('rp_equiv_yr'), 0)} 年一遇，对应情景 {e.get('scenario_key')}，雨量判据预警 {e.get('alert_level')}）：该情景下推演积水的 {r['n_alerted']} 个点进入「预警待命」。之后用 update_episode 登记上报 / 核实 / 到位 / 退干，close_point 关闭，event_review 复盘。"
        return f"Opened event {e['event_id']} '{e.get('label')}' ({e.get('kind')}, {e.get('date')}, {_fmt(e.get('p24_mm'), 0)} mm/24 h ≈ {_fmt(e.get('rp_equiv_yr'), 0)}-yr, scenario {e.get('scenario_key')}, rain-rule alert {e.get('alert_level')}): {r['n_alerted']} points modelled flooded are now 'on alert'. Use update_episode to log stages, close_point to close, event_review to review."
