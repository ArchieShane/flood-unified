"""L1 / L2 / L2+ 规划结果：各态缺口、全市 L1/L2、各态设施、「现有方案当既成事实还要补什么」。

数据只读 console/data/l2plus_catalog.json（tools/l2plus_catalog.py 从 state_table / pilot_frames / city_gap / 设施文件汇总），
与控制台「防线 2+」各视图、在线报告同源；这里不算任何新东西。2026-09-28 加：此前 35 个动作都是积水点 / 调度，
客户在对话里问「Parsons 做完还要补什么」智能体答不了。
"""
from __future__ import annotations
import json
from ..common import ActionError, _fmt


class PlanActions:
    # ---- 目录与名称 ----
    def _l2p_catalog(self) -> dict:
        p = self.root / "console" / "data" / "l2plus_catalog.json"
        if not p.exists():
            raise ActionError("还没有 L2+ 结果目录（先跑 tools/l2plus_catalog.py）")
        return self._memo(("l2p_catalog", p.stat().st_mtime), lambda: json.loads(p.read_text(encoding="utf-8")))

    def _l2p_region(self, name, required=True):
        cat = self._l2p_catalog(); R = cat.get("regions") or {}
        if not name:
            if required:
                raise ActionError("要指明片区：" + " / ".join(R))
            return None, None
        low = str(name).strip().lower().replace(" ", "")
        for k, v in R.items():
            if low == k.lower().replace(" ", "") or any(low == a.replace(" ", "") or a.replace(" ", "") in low for a in v.get("aliases", [])):
                return k, v
        raise ActionError(f"没有片区 {name!r}；有：{' / '.join(R)}")

    @staticmethod
    def _l2p_state(reg, st):
        if st is None:
            return None
        names = list(reg["states"])
        relaxed = any(w in str(st).lower() for w in ("放宽", "relax", "vacant"))
        s = str(st).upper().replace(" ", "").replace("＋", "+")
        for w in ("（放宽）", "(放宽)", "放宽", "RELAXED", "RELAX", "VACANT"):
            s = s.replace(w, "")
        s = s.replace("L2+SUGGESTED", "L2+A+B").replace("L2+PLANNED", "L2+A")       # 显示名（2026-09-28 用户定）→ 目录里的键
        s = {"L2A": "L2+A", "L2B": "L2+B", "L2AB": "L2+A+B", "L2A+B": "L2+A+B", "L2+AB": "L2+A+B",
             "PLANNED": "L2+A", "SUGGESTED": "L2+A+B"}.get(s, s)
        for n in names:
            base = n.split("（")[0].upper()
            if base == s and (("放宽" in n) == relaxed):
                return n
        for n in names:
            if n.split("（")[0].upper() == s:
                return n
        raise ActionError(f"{reg['label']} 没有态 {st!r}；有：{' / '.join(names)}")

    @staticmethod
    def _l2p_rows(reg):
        """各指标 × 各态；主线相邻差 + 对照态相对其父态的差。"""
        chain, refs = reg.get("chain") or [], reg.get("refs") or {}
        parent = {s: chain[i - 1] for i, s in enumerate(chain) if i} | refs
        rows = []
        for m in reg.get("metrics") or []:
            v = m["values"]; step = {}
            for s, p0 in parent.items():
                a, b = v.get(p0), v.get(s)
                if a is not None and b is not None:
                    step[s] = {"vs": p0, "abs": b - a, "pct": (b - a) / a * 100 if a else None}
            rows.append({"key": m["key"], "zh": m["zh"], "en": m["en"], "unit": m["unit"], "better": m["better"], "values": v, "step": step})
        return rows

    def _l2p_ui(self, reg, st=None, base=None, fac=None):
        ui = dict(reg.get("console") or {})
        if ui.get("l2p") in ("wb", "rb2") and st:
            ui["st"] = st
            if base:
                ui["base"] = base
        if fac:
            ui["highlight"] = self._hl([f for f in fac if f.get("lon") is not None], "fac", "id")[:40]
            ui["fit"] = "highlight"
        return ui

    # ---- 1) 各态缺口 ----
    def _act_state_gap(self, P):
        cat = self._l2p_catalog()
        if not P.get("region"):
            regs = []
            for k, v in (cat.get("regions") or {}).items():
                rows = self._l2p_rows(v); end = v["chain"][-1] if v.get("chain") else None
                head = next((r for r in rows if r["unit"] in ("m³",)), None)
                regs.append({"region": k, "states": list(v["states"]), "chain": v.get("chain"), "refs": v.get("refs"), "scenario": v.get("scenario"),
                             "headline": {"metric": head["zh"], "unit": head["unit"], "L1": head["values"].get("L1"), "end": end, "end_value": head["values"].get(end)} if head and end else None})
            return {"regions": regs, "city": bool(cat.get("city")), "_ui": {"tab": "line2p", "l2p": "wb"}}
        k, reg = self._l2p_region(P["region"])
        rows = self._l2p_rows(reg)
        end = reg["chain"][-1] if reg.get("chain") else None
        return {"region": k, "scenario": reg.get("scenario"), "model": reg.get("model"), "states": reg["states"], "chain": reg.get("chain"), "refs": reg.get("refs"),
                "rows": rows, "overlap": reg.get("overlap"), "notes": reg.get("notes"), "report": reg.get("report"),
                "_ui": self._l2p_ui(reg, st=end, base=(reg["chain"][-2] if len(reg.get("chain") or []) > 1 else None))}

    def _sum_state_gap(self, r, P, lang):
        if "regions" in r:
            if lang == "zh":
                return "有 L1 / L2 / L2+ 多态结果的片区：" + "；".join(
                    f"{x['region']}（{' → '.join(x['chain'] or [])}" + (f"，对照 {' / '.join(x['refs'])}" if x.get("refs") else "") + "）"
                    + (f"：{x['headline']['metric']} L1 {_fmt(x['headline']['L1'])} → {x['headline']['end']} {_fmt(x['headline']['end_value'])} {x['headline']['unit']}" if x.get("headline") else "")
                    for x in r["regions"]) + ("。另有全市 L1 / L2（一维）。" if r.get("city") else "。")
            return "Regions with L1 / L2 / L2+ results: " + "; ".join(f"{x['region']} ({' → '.join(x['chain'] or [])})" for x in r["regions"]) + ("; plus citywide L1 / L2 (1D)." if r.get("city") else ".")
        rows = r["rows"]; chain = r["chain"] or []

        def fmtv(v, u):
            return "—" if v is None else (_fmt(v, 2) if u in ("m", "h") else _fmt(v, 1) if u == "ha" else _fmt(v))
        parts = []
        for x in rows[:4]:
            seq = " → ".join(f"{s} {fmtv(x['values'].get(s), x['unit'])}" for s in chain if s in x["values"])
            last = x["step"].get(chain[-1]) if chain else None
            tail = f"（最后一步 {last['pct']:+.1f}%）" if last and last.get("pct") is not None else ""
            parts.append(f"{x['zh'] if lang == 'zh' else x['en']}：{seq} {x['unit']}{tail}")
        refs = r.get("refs") or {}
        ref_txt = ""
        if refs and rows:
            x = rows[0]
            ref_txt = ("；对照 " if lang == "zh" else "; reference ") + "、".join(f"{s} {fmtv(x['values'].get(s), x['unit'])} {x['unit']}" for s in refs if s in x["values"])
        if lang == "zh":
            return (f"{r['region']} · {r['scenario']}（主线 {' → '.join(chain)}，每一步只和上一态比）：" + "；".join(parts) + ref_txt + "。"
                    + (f"{r['overlap']}" if r.get("overlap") else "") + (f" 口径：{r['notes'][0]}。" if r.get("notes") else ""))
        return f"{r['region']} · {r['scenario']} (main chain {' → '.join(chain)}): " + "; ".join(parts) + ref_txt + "."

    # ---- 2) 全市 L1 / L2 ----
    def _act_city_gap(self, P):
        c = self._l2p_catalog().get("city")
        if not c:
            raise ActionError("还没有全市 L1 / L2 结果")
        return {**c, "_ui": dict(c.get("console") or {})}

    def _sum_city_gap(self, r, P, lang):
        pn = r.get("pond_networks") or {}; nd = r.get("nodes") or {}; tw = r.get("coupled_2d") or {}
        if lang == "zh":
            return (f"全市 L1 / L2（{r['basis']}，{r['scenario']}）：L1 管网接不住 {_fmt(r['L1_flood_m3'] / 1e4, 1)} 万 m³，L2（加 22 座已建成塘）{_fmt(r['L2_flood_m3'] / 1e4, 1)} 万 m³，"
                    f"差值 {_fmt(r['diff_m3'] / 1e4, 1)} 万 m³（{r['diff_pct']:.1f}%），全部出在接塘的 {pn.get('n')} 块管网上（那里 −{_fmt(pn.get('diff_pct'), 1)}%）。"
                    f"冒水井 {_fmt(nd.get('n_nodes'))} 口，L2 溢流减少 {_fmt(nd.get('n_better'))} 口、增加 {_fmt(nd.get('n_worse'))} 口（塘满顶托）。"
                    f"L1 溢流有 {r['outside_client_catch_pct']}% 落在客户汇水区之外。一二维（{tw.get('res_m')} m、{_fmt(tw.get('domain_km2'))} km²）"
                    + (self._city2d_zh(tw) if tw.get("status") == "done" else "还在算，推演播放、最大积水面积 / 水深要等它。") + " 口径：" + "；".join(r["notes"][:2]) + "。")
        return (f"Citywide L1 / L2 ({r['scenario']}, 1D): L1 overflow {_fmt(r['L1_flood_m3'] / 1e6, 2)} M m³, L2 (22 built ponds) {_fmt(r['L2_flood_m3'] / 1e6, 2)} M m³, "
                f"difference {_fmt(r['diff_m3'])} m³ ({r['diff_pct']:.1f}%), all in the {pn.get('n')} pond-connected networks. Coupled 1D/2D is {'done' if tw.get('status') == 'done' else 'still running'}.")

    @staticmethod
    def _city2d_zh(tw):
        a, b = tw.get("L1") or {}, tw.get("L2") or {}
        if not a or not b:
            return "已完成。"
        pc = lambda k: (b[k] - a[k]) / a[k] * 100 if a.get(k) else 0
        near = "、".join(f"{x['unitid'].replace('SW_', '')} {x['L1_ge_0p30_ha']:.0f}→{x['L2_ge_0p30_ha']:.0f} ha" for x in (tw.get("near_ponds") or [])[:3])
        return (f"已完成：积水 ≥0.30 m 最大面积 {_fmt(a['max_area_ge_0p30_ha'])} → {_fmt(b['max_area_ge_0p30_ha'])} ha（{pc('max_area_ge_0p30_ha'):+.1f}%），"
                f"≥0.8 m 深水 {_fmt(a['area_ge_0p80_ha'])} → {_fmt(b['area_ge_0p80_ha'])} ha，最大水深都是 {a['max_depth_m']:.2f} m；"
                f"管网溢流进地面 {pc('flood_1d_to_2d_m3'):+.1f}%。最大水深降 5 cm 以上的只有 {_fmt(tw['shallower_ha'])} ha，其中 {_fmt(tw['shallower_near_ponds_ha'])} ha 在塘周边 1 km 内"
                f"（如 {near}）。22 座塘蓄了 {_fmt(tw['ponds_stored_m3'] / 1e4, 1)} 万 m³，是库容的 {tw['ponds_stored_m3'] / tw['ponds_capacity_m3'] * 100:.0f}%。")

    # ---- 3) 各态设施 ----
    def _act_facility_plan(self, P):
        k, reg = self._l2p_region(P["region"])
        st = self._l2p_state(reg, P.get("state")) if P.get("state") else (reg["chain"][-1] if reg.get("chain") else None)
        fac = (reg.get("facilities") or {}).get(st) or []
        by = {}
        for f in fac:
            b = by.setdefault(f["kind_zh"], {"n": 0, "volume_m3": 0.0, "source": f.get("source")})
            b["n"] += 1; b["volume_m3"] += f.get("volume_m3") or 0
        return {"region": k, "state": st, "state_desc": reg["states"].get(st), "facilities": fac[:60], "n": len(fac), "by_kind": by,
                "all_states": {s: len(v) for s, v in (reg.get("facilities") or {}).items()}, "notes": reg.get("notes"),
                "_ui": self._l2p_ui(reg, st=st, fac=fac)}

    def _sum_facility_plan(self, r, P, lang):
        if not r["n"]:
            return (f"{r['region']} 的 {r['state']}（{r['state_desc']}）没有新增设施。" if lang == "zh"
                    else f"{r['region']} {r['state']} has no added facilities.")
        kinds = "；".join(f"{k} {v['n']} 处、{_fmt(v['volume_m3'])} m³（{v['source']}）" for k, v in r["by_kind"].items())
        big = sorted([f for f in r["facilities"] if f.get("volume_m3")], key=lambda f: -(f["volume_m3"] or 0))[:4]
        land = {"open_land": "空地", "road_reserve": "道路红线", "vacant_plot": "未盖楼地块"}
        top = "、".join(f"{f['id']} {_fmt(f['volume_m3'])} m³" + (f"（{land.get(f.get('land'), f.get('land'))}，挪 {_fmt(f.get('moved_m'))} m）" if f.get("land") else "") for f in big)
        if lang == "zh":
            return f"{r['region']} · {r['state']}（{r['state_desc']}）：{kinds}。最大的：{top}。地图已标出这些设施。"
        return f"{r['region']} · {r['state']}: " + "; ".join(f"{v['n']} × {k}, {_fmt(v['volume_m3'])} m³" for k, v in r["by_kind"].items()) + ". Highlighted on the map."

    # ---- 4) 现有方案当既成事实，还要补什么 ----
    def _act_as_built_next(self, P):
        k, reg = self._l2p_region(P["region"])
        chain = reg.get("chain") or []
        if "L2+A+B" not in chain or "L2+A" not in chain:
            raise ActionError(f"{k} 没有「在现有方案之上再加」的推演（只有 {' → '.join(chain)}）")
        rows = self._l2p_rows(reg)
        pick = lambda st: {x["key"]: {"zh": x["zh"], "en": x["en"], "unit": x["unit"], "before": x["values"].get("L2+A"), "after": x["values"].get(st),
                                      "pct": ((x["values"].get(st) - x["values"].get("L2+A")) / x["values"].get("L2+A") * 100) if x["values"].get("L2+A") and x["values"].get(st) is not None else None}
                           for x in rows}
        variants = [s for s in reg["states"] if s.startswith("L2+A+B") and any(s in x["values"] for x in rows)]
        added = {s: [f for f in (reg.get("facilities") or {}).get(s, []) if f.get("kind") == "box"] for s in variants}
        out = {"region": k, "scenario": reg.get("scenario"), "base": "L2+A", "base_desc": reg["states"].get("L2+A"),
               "variants": [{"state": s, "desc": reg["states"].get(s), "change": pick(s), "added": added[s][:30], "n_added": len(added[s]),
                             "added_m3": sum(f.get("volume_m3") or 0 for f in added[s])} for s in variants],
               "residual_L1_to_A": {x["key"]: x["step"].get("L2+A") for x in rows}, "overlap": reg.get("overlap"), "notes": reg.get("notes"),
               "_ui": {**self._l2p_ui(reg, st="L2+A+B", base="L2+A", fac=(reg.get("facilities") or {}).get("L2+A+B")), "pin": True}}
        return out

    def _sum_as_built_next(self, r, P, lang):
        v0 = r["variants"][0] if r["variants"] else None
        if not v0:
            return f"{r['region']}：还没有 L2+A+B 的结果。"
        ch = list(v0["change"].values())

        def one(x):
            d = 2 if x["unit"] in ("m", "h") else 1 if x["unit"] == "ha" else 0
            return f"{x['zh']} {_fmt(x['before'], d)} → {_fmt(x['after'], d)} {x['unit']}" + (f"（{x['pct']:+.0f}%）" if x.get("pct") is not None else "")
        more = ""
        if len(r["variants"]) > 1:
            v1 = r["variants"][1]; x = list(v1["change"].values())[0]
            more = f"；{v1['state']}：补 {v1['n_added']} 座、{_fmt(v1['added_m3'])} m³，{one(x)}"
        if lang == "zh":
            return (f"{r['region']}（{r['scenario']}）把 L2+A「{r['base_desc']}」当既成事实：还要补 {v0['n_added']} 座地下箱涵、可用库容 {_fmt(v0['added_m3'])} m³；"
                    f"补完 " + "，".join(one(x) for x in ch[:3]) + more + "。" + (r.get("overlap") or "")
                    + (f" 注意：{r['notes'][0]}。" if r.get("notes") else ""))
        return (f"{r['region']}: taking L2+A as built, add {v0['n_added']} underground boxes ({_fmt(v0['added_m3'])} m³ usable); "
                + ", ".join(f"{x['en']} {_fmt(x['before'])} → {_fmt(x['after'])} {x['unit']}" for x in ch[:3]) + ".")

    # ---- 5) 资产种类是什么（多模态：定义 + 剖面示意 / 实景图）----
    def _act_asset_kind(self, P):
        from ... import asset_kb as K
        q = str(P["kind"]).strip().lower()
        hit = next((k for k, e in K.KB.items() if q in (k, e["zh"].lower(), e["en"].lower()) or e["zh"] in P["kind"]
                    or (len(q) >= 3 and (q in e["en"].lower() or q in k))), None)
        if hit is None:
            alias = {"箱涵": "box_culvert", "调蓄池": "box_culvert", "塘": "pond", "临时塘": "pond", "井": "wetwell", "集水井": "wetwell",
                     "泵": "stationary_pump", "移动泵": "mobile_pump", "护栏": "jersey_barrier", "挡水": "small_barrier", "地面管": "surface_pipeline"}
            hit = next((v for k2, v in alias.items() if k2 in P["kind"]), None)
        if hit is None:
            raise ActionError(f"没有这种资产：{P['kind']}；有：" + "、".join(e["zh"] for e in K.KB.values()))
        row = next(r for r in K.catalog() if r["kind"] == hit)
        seen = set()                     # 同一座设施会出现在好几态里（L2+A 的塘也在 L2+A+B 里）：按 片区 + 态的方案 + 编号去重
        try:
            for rn, reg in (self._l2p_catalog().get("regions") or {}).items():
                for st, fs in (reg.get("facilities") or {}).items():
                    for f in fs:
                        if (f.get("kind") == "box" and hit == "box_culvert") or f.get("kind") == hit:
                            seen.add((rn, f.get("source") or st, f["id"]))
        except ActionError:
            pass
        return {**row, "n_instances": len(seen), "_ui": {"tab": "ontology", "kind": hit}}

    def _sum_asset_kind(self, r, P, lang):
        lv = r.get("level_user") or r.get("level_a") or r.get("level_b") or "—"
        fn = "、".join(f["zh"] for f in r["functions"]) if lang == "zh" else ", ".join(f["en"] for f in r["functions"])
        if lang == "zh":
            return (f"{r['zh']}（{r['en']}）：{r['def_zh']} 功能：{fn}；客户分级：{lv}。模型里：{r['model_zh']} 关键参数：{'、'.join(r['params'])}。"
                    + (f"剖面示意见卡片。" if r.get("schematic") else "") + (f"本系统的片区结果里有 {r['n_instances']} 座（同一座在几个态里只算一次；不同方案里的各算各的）。" if r.get("n_instances") else ""))
        return f"{r['en']}: {r['def_en']} Functions: {fn}; client level: {lv}."

