"""情景、预警、报告、外部源、假设总账、工程建议 what-if——本体动作与中英小结。由 Runtime 多继承，方法体与拆分前逐字相同（ROOT 改成 self.root）。"""
from __future__ import annotations
import math
from ... import scenarios
from ..common import ActionError, _f, _fmt


class SystemActions:
    def _act_set_scenario(self, P):
        key = P["scenario"]; self.default_scenario = key
        return {"scenario": self.scenario_object(key), "_ui": {"rp": key}}

    def _sum_set_scenario(self, r, P, lang):
        s = r["scenario"]; lab = s.get("label") if lang == "zh" else (s.get("label_en") or s.get("label"))
        by = s.get("points_by_level") or {}
        if lang == "zh":
            return f"已切到 {lab}（15 min 雨强 {s.get('i15_mmh')} mm/h，24 h {s.get('p24_mm')} mm）：积水点 {s.get('n_points')}（III 级 {by.get('III', 0)}），已派 {s.get('n_served')} 点 / {s.get('units_used')} 台泵，III 级达标 {s.get('critical_within_target')}/{s.get('critical_total')}，全网路由溢流 {_fmt((s.get('spill_total_m3') or 0) / 1e4, 1)} 万 m³，预警等级 {s.get('alert_level')}。"
        return f"Switched to {lab} (15-min {s.get('i15_mmh')} mm/h, 24 h {s.get('p24_mm')} mm): {s.get('n_points')} flood points (level III {by.get('III', 0)}), {s.get('n_served')} served / {s.get('units_used')} pumps, level III within target {s.get('critical_within_target')}/{s.get('critical_total')}, routed spill {_fmt((s.get('spill_total_m3') or 0) / 1e6, 2)} M m³, alert level {s.get('alert_level')}."

    def _act_rain_to_scenario(self, P):
        """预报 24 h 雨量 → 年一遇（表 3-6 对数插值）/ 最贴近与偏安全的设计情景 / 可比实测事件 / 三判据预警等级 / 预置窗口；缺省把默认情景切过去。"""
        from ... import alerts as _alerts
        p = float(P["p24_mm"]); lead = P.get("lead_days")
        rows = ((self.cfg.get("idf") or {}).get("return_periods") or {})
        table = sorted(((int(k), float(v["p24_mm"])) for k, v in rows.items() if isinstance(v, dict) and v.get("p24_mm") is not None), key=lambda x: x[1])
        est = None; bracket = None
        if table:
            if p <= table[0][1]:
                bracket = ["<", table[0][0]]
            elif p >= table[-1][1]:
                bracket = [">", table[-1][0]]
            else:
                for (r0, p0), (r1, p1) in zip(table, table[1:]):
                    if p0 <= p < p1:
                        est = math.exp(math.log(r0) + (p - p0) / (p1 - p0) * (math.log(r1) - math.log(r0))); bracket = [r0, r1]; break
        cands = [{"key": c["key"], "kind": c["kind"], "label": c["label"], "label_en": c.get("label_en"), "p24_mm": float(c["p24_mm"]), "i15_mmh": c.get("i15_mmh"), "delta_mm": round(float(c["p24_mm"]) - p, 1)} for c in scenarios.catalog(self.cfg)]
        design = [c for c in cands if c["kind"] in ("rp", "rp_cc")]; events = [c for c in cands if c["kind"] == "event"]
        nearest = min(design, key=lambda c: (abs(c["delta_mm"]), 0 if c["kind"] == "rp_cc" else 1)) if design else None     # 同样近时优先 DMT 现行口径（10 年 + CC）
        above = [c for c in design if c["delta_mm"] >= 0]
        safe = min(above, key=lambda c: c["delta_mm"]) if above else None
        key = nearest["key"] if nearest else None
        lv_rain, _ = _alerts.level_from_rules(self.cfg, p, None, None)
        so = self.scenario_object(key, compute=bool(P.get("with_rules", True))) if key else {}
        iii = (so.get("points_by_level") or {}).get("III"); spill = so.get("spill_total_m3")
        lv_all, trig = _alerts.level_from_rules(self.cfg, p, iii, spill)
        applied = bool(P.get("apply", True)) and key is not None
        if applied:
            self.default_scenario = key
        window = None if lead is None else ("open" if float(lead) >= 1 else ("tight" if float(lead) * 24 >= 6 else "closed"))
        nxt = [{"api": "preposition_plan", "params": {"scenario": key, "alert_level": lv_all}}, {"api": "count_points", "params": {"scenario": key}}, {"api": "list_points", "params": {"scenario": key, "level": "III", "limit": 10}}] if key else []
        return {"p24_mm": p, "return_period": {"years_est": (round(est, 1) if est is not None else None), "bracket": bracket, "table": [{"rp": r, "p24_mm": v} for r, v in table],
                                                "source": "DMT WA-726-1 (2022) 表 3-6 B 区 24 h 雨量，两点对数插值；正式口径以频率曲线为准", "source_en": "DMT WA-726-1 (2022) Table 3-6 zone B 24-h depths, log interpolation between two rows; the frequency curve is the formal basis"},
                "nearest": nearest, "safe_side": safe, "design_candidates": design, "events": events,
                "alert": {"rain_only": lv_rain, "with_scenario_rules": lv_all, "triggers": trig, "scenario_key": key, "scenario_iii_points": iii, "scenario_spill_m3": spill},
                "lead_days": lead, "preposition_window": window, "applied": applied, "next_actions": nxt, "_ui": ({"rp": key} if applied else {})}

    def _sum_rain_to_scenario(self, r, P, lang):
        rp = r["return_period"]; n = r.get("nearest") or {}; sf = r.get("safe_side"); al = r["alert"]; br = rp.get("bracket") or []
        tab = {row["rp"]: row["p24_mm"] for row in rp.get("table") or []}
        if rp.get("years_est") is not None and len(br) == 2:
            yrs_zh = f"约 {_fmt(rp['years_est'], 0)} 年一遇（表 3-6 的 {br[0]} 年 {tab.get(br[0])} mm 与 {br[1]} 年 {tab.get(br[1])} mm 之间对数插值）"
            yrs_en = f"about a {_fmt(rp['years_est'], 0)}-year event (log-interpolated between the {br[0]}-yr {tab.get(br[0])} mm and {br[1]}-yr {tab.get(br[1])} mm rows of Table 3-6)"
        elif br:
            yrs_zh = f"{'不到' if br[0] == '<' else '超过'} {br[1]} 年一遇（{'低于' if br[0] == '<' else '高于'}表 3-6 的{'最小' if br[0] == '<' else '最大'}档 {tab.get(br[1])} mm）"
            yrs_en = f"{'below' if br[0] == '<' else 'beyond'} the {br[1]}-year row of Table 3-6 ({tab.get(br[1])} mm)"
        else:
            yrs_zh = "年一遇无法估（配置里没有表 3-6）"; yrs_en = "return period not available (no Table 3-6 rows configured)"
        ev_zh = "；".join(f"{e['label']} {_fmt(e['p24_mm'])} mm" for e in r.get("events") or []); ev_en = "; ".join(f"{e.get('label_en') or e['label']} {_fmt(e['p24_mm'])} mm" for e in r.get("events") or [])
        iii = al.get("scenario_iii_points"); sp = al.get("scenario_spill_m3"); lead = r.get("lead_days"); win = r.get("preposition_window")
        hours = float(lead) * 24 if lead is not None else 0.0          # 字典字面量会把所有分支的 f-string 都先求值，lead 为 None 时不能直接 float
        if lang == "zh":
            win_zh = {"open": f"距降雨 {_fmt(lead, 1)} 天，预置窗口足够（驻地落位 + 20 min 内覆盖），", "tight": f"距降雨只有 {_fmt(hours, 0)} 小时，预置窗口很紧，", "closed": "已来不及预置，只能事中调度，"}.get(win, "")
            out = (f"预报 24 h {_fmt(r['p24_mm'])} mm ≈ {yrs_zh}。最贴近的情景是 {n.get('label')}（24 h {_fmt(n.get('p24_mm'))} mm，比预报{'多' if (n.get('delta_mm') or 0) >= 0 else '少'} {_fmt(abs(n.get('delta_mm') or 0), 1)} mm）"
                   + (f"；偏安全一侧可用 {sf['label']}（{_fmt(sf['p24_mm'])} mm）" if sf and sf.get("key") != n.get("key") else "") + (f"；可比实测事件：{ev_zh}" if ev_zh else "") + "。"
                   f"预警：单看雨量判据是{al['rain_only']}级；套上该情景的 III 级 {iii if iii is not None else '—'} 个与全网路由溢流 {_fmt((sp or 0) / 1e4, 1)} 万 m³，三判据取最高为 **{al['with_scenario_rules']}级**。"
                   + win_zh + (f"建议接着调 preposition_plan({n.get('key')}, {al['with_scenario_rules']}) 看预置方案。" if n else "")
                   + (f" 控制台与后续回答已切到 {n.get('key')}。" if r.get("applied") else ""))
            return out
        win_en = {"open": f"With {_fmt(lead, 1)} days to go the pre-positioning window is open (site set-up plus 20-min coverage). ", "tight": f"Only {_fmt(hours, 0)} h to go: the pre-positioning window is tight. ", "closed": "Too late to pre-position; dispatch during the event only. "}.get(win, "")
        return (f"Forecast 24-h {_fmt(r['p24_mm'])} mm ≈ {yrs_en}. Nearest scenario: {n.get('label_en') or n.get('label')} ({_fmt(n.get('p24_mm'))} mm, {'+' if (n.get('delta_mm') or 0) >= 0 else '-'}{_fmt(abs(n.get('delta_mm') or 0), 1)} mm vs forecast)"
                + (f"; safe-side option {sf.get('label_en') or sf['label']} ({_fmt(sf['p24_mm'])} mm)" if sf and sf.get("key") != n.get("key") else "") + (f"; comparable observed events: {ev_en}" if ev_en else "") + ". "
                f"Alert: rain alone gives {al['rain_only']}; with this scenario's {iii if iii is not None else '—'} level-III points and {_fmt((sp or 0) / 1e6, 2)} M m³ routed spill the three rules give **{al['with_scenario_rules']}**. "
                + win_en + (f"Next: preposition_plan({n.get('key')}, {al['with_scenario_rules']}) for the staging plan." if n else "") + (f" Console and following answers switched to {n.get('key')}." if r.get("applied") else ""))

    def _act_assumptions(self, P):
        from ... import assumptions as _asm
        items = _asm.register(self.cfg, self.b, P.get("scenario"))
        return {"scenario": self.scenario_object(P["scenario"], compute=False), "items": items, "summary": _asm.summarize(items), "_ui": {"rp": P["scenario"], "assumptions": True}}

    def _sum_assumptions(self, r, P, lang):
        it = r["items"]; sm = r["summary"]
        lv_zh = {"simulated": "模拟", "assumed": "假设", "placeholder": "占位", "unverified": "未核实", "missing": "缺失"}
        if lang == "zh":
            s = f"当前依赖 {sm['n']} 项非实测输入：" + "、".join(f"{lv_zh.get(k, k)} {v}" for k, v in sm["by_level"].items()) + "。"
            s += " 逐条：" + "；".join(f"[{lv_zh.get(x['level'], x['level'])}] {x['what']}" + (f"（替换：{x['fix']}）" if x.get("fix") else "") for x in it) + "。"
            return s
        s = f"The current view rests on {sm['n']} non-measured inputs: " + ", ".join(f"{k} {v}" for k, v in sm["by_level"].items()) + "."
        s += " Items: " + "; ".join(f"[{x['level']}] {x.get('what_en') or x['what']}" + (f" (replace with: {x['fix']})" if x.get("fix") else "") for x in it) + "."
        return s

    def _act_feeds_status(self, P):
        from ... import feeds as _feeds
        return {"feeds": _feeds.status(self.cfg), "_ui": {}}

    def _sum_feeds_status(self, r, P, lang):
        F = r["feeds"]; names = {"incidents": ("993 报案", "993 incidents"), "checkins": ("承包商 check-in", "contractor check-ins"), "bridges": ("桥梁限重台账", "bridge weight register"), "contractors": ("承包商服务边界", "contractor boundaries")}
        parts = []
        for k, v in F.items():
            nm = names.get(k, (k, k))[0 if lang == "zh" else 1]
            if lang == "zh":
                mode = (f"拉 {v['url']}（token {'已设' if v.get('token_set') else '未设'}，{v.get('poll_s')} s 一次）" if v.get("url") else f"推 POST {v['push_endpoint']} 或放文件 {v['file']}")
                data = (f"{v['rows']} 行，更新于 {v.get('as_of') or v.get('mtime')}" if v.get("file_exists") and v.get("rows") else "还没有数据")
                parts.append(f"{nm}：{mode}；{data}")
            else:
                mode = (f"pull {v['url']} (token {'set' if v.get('token_set') else 'missing'}, every {v.get('poll_s')} s)" if v.get("url") else f"push POST {v['push_endpoint']} or drop the file {v['file']}")
                data = (f"{v['rows']} rows, updated {v.get('as_of') or v.get('mtime')}" if v.get("file_exists") and v.get("rows") else "no data yet")
                parts.append(f"{nm}: {mode}; {data}")
        if lang == "zh":
            return "四个外部源用同一套契约、两条路（我们拉，或 ADM 推），ADM 应急系统开放接口后只需在 [feeds.*].url 填地址：" + "；".join(parts) + "。"
        return "All four feeds share one contract and two routes (we pull, or ADM pushes); once ADM opens its interface only [feeds.*].url needs filling: " + "; ".join(parts) + "."

    def _act_proposal_whatif(self, P):
        r = self.b.whatif_one(int(P["pid"]))
        if r.get("error"):
            raise ActionError(r["error"])
        p = self.load_proposals().get(int(P["pid"])) or {}
        return {"pid": int(P["pid"]), "proposal": p, "kpis_before": r.get("kpis_before"), "kpis_after": r.get("kpis_after"), "delta": r.get("delta"), "cost_aed": r.get("cost_aed"), "note": r.get("note"), "note_en": r.get("note_en"), "n_interventions": len(r.get("interventions") or []), "_ui": {"tab": "line1", "pid": int(P["pid"]), "whatif": 1}}

    def _sum_proposal_whatif(self, r, P, lang):
        p = r["proposal"]; title = p.get("title") if lang == "zh" else (p.get("title_en") or p.get("title"))
        if r.get("delta") is None:
            return f"建议 {r['pid']}「{title}」：{r.get('note')}" if lang == "zh" else f"Proposal {r['pid']} “{title}”: {r.get('note_en') or r.get('note')}"
        d = r["delta"]; top = sorted(d.items(), key=lambda kv: -abs(_f(kv[1], 0) or 0))[:4]
        ds = "，".join(f"{k} {'+' if (_f(v, 0) or 0) > 0 else ''}{_fmt(v, 2)}" for k, v in top)
        return (f"建议 {r['pid']}「{title}」（{r['n_interventions']} 项干预）：全网 KPI 变化 {ds}；造价约 {_fmt(r.get('cost_aed'))} AED。" if lang == "zh" else f"Proposal {r['pid']} “{title}” ({r['n_interventions']} interventions): network KPI deltas {ds}; cost about {_fmt(r.get('cost_aed'))} AED.")

    def _act_issue_alert(self, P):
        r = self.b.alert(p24_mm=float(P["p24_mm"]), lead_h=float(P.get("lead_h") or 24), area=P.get("area"), source="agent")
        if r.get("error"):
            raise ActionError(r["error"])
        self.invalidate("alerts")
        sc = r.get("scenario") or {}
        return {"p24_mm": float(P["p24_mm"]), "lead_h": float(P.get("lead_h") or 24), "scenario": sc, "level": r.get("level"), "level_en": r.get("level_en"), "manual": r.get("manual"), "headline": r.get("headline"), "headline_en": r.get("headline_en"),
                "triggers": r.get("triggers") or r.get("rules"), "actions": [{k: a.get(k) for k in ("role", "role_en", "zh", "en")} for a in (r.get("actions") or [])[:8]], "timeline": (r.get("timeline") or [])[:6], "written": bool(r.get("file") or r.get("path") or True), "_ui": {"tab": "line2", "fc": float(P["p24_mm"])}}

    def _sum_issue_alert(self, r, P, lang):
        sc = r.get("scenario") or {}; lab = (sc.get("label") if lang == "zh" else (sc.get("label_en") or sc.get("label"))) or sc.get("key") or ""
        acts = "；".join(f"{a.get('role')}：{a.get('zh')}" for a in r["actions"][:4]) if lang == "zh" else "; ".join(f"{a.get('role_en') or a.get('role')}: {a.get('en') or a.get('zh')}" for a in r["actions"][:4])
        if lang == "zh":
            return f"预报 {r['p24_mm']:g} mm/24 h（提前 {r['lead_h']:g} h）→ 最接近情景 {lab} → 预警等级 {r.get('level')}（手册 {(r.get('manual') or {}).get('level')} 级）：{r.get('headline')}。提示 {len(r['actions'])} 条，如 {acts}。已写 out/alerts/。"
        return f"Forecast {r['p24_mm']:g} mm/24 h (lead {r['lead_h']:g} h) → nearest scenario {lab} → alert {r.get('level_en') or r.get('level')} (manual level {(r.get('manual') or {}).get('level')}): {r.get('headline_en') or r.get('headline')}. {len(r['actions'])} prompts, e.g. {acts}. Written to out/alerts/."

    def _act_generate_report(self, P):
        key = P["scenario"]; lang = P.get("lang") or "zh"
        url = f"/api/report?rp={key}&lang={lang}"
        return {"scenario": self.scenario_object(key, compute=False), "url": url, "lang": lang, "_ui": {"url": url}}

    def _sum_generate_report(self, r, P, lang):
        s = r["scenario"]; lab = s.get("label") if lang == "zh" else (s.get("label_en") or s.get("label"))
        return f"{lab} 的报告：{r['url']}（自包含 HTML，浏览器里另存或打印为 PDF）。" if lang == "zh" else f"Report for {lab}: {r['url']} (self-contained HTML; save or print to PDF from the browser)."
