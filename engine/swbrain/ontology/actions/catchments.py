"""汇水区 / 分析范围的只读动作（2026-09-29）：catchment_info。"""
from __future__ import annotations

from ..common import ActionError


class CatchmentActions:
    def _act_catchment_info(self, P):
        fp, name, dom = P.get("fp_id"), P.get("catchment"), P.get("domain")
        if not (fp or name or dom):
            raise ActionError("给一个积水点编号、汇水区名称或分析范围（C1 / C1U）")
        out: dict = {"mode": None}
        if fp:
            if self.get("FloodPoint", fp) is None:
                raise ActionError(f"没有积水点 {fp}")
            cs = self.catchments_of(fp)
            flags = [r for r in self.rules("FloodPoint", self.get("FloodPoint", fp)) if r["rule"] in ("catchment_uncovered", "client_db_missing") and r.get("fired")]
            out.update(mode="point", fp_id=fp, catchments=[{k: c.get(k) for k in ("catchment_id", "name", "title", "source", "source_zh", "confidence", "area_ha")} for c in cs],
                       flags=[{"rule": f["rule"], "zh": f["zh"], "why": (f["value"] or {}).get("why"), "why_en": (f["value"] or {}).get("why_en")} for f in flags])
            pt = self._fp_coords(fp)
            out["_ui"] = {"highlight": self._hl([pt], "points") if pt else [], "fit": "highlight", "layers": ["c1u"] if any(c["source"] == "client_layer" for c in cs) else []}
            return out
        if name:
            C = self.load_catchments(); ids = self.catchment_ids(name)
            items = []
            for cid in ids[:5]:
                c = C[cid]; r = {r_["rule"]: r_ for r_ in self.rules("Catchment", c)}
                assets = self.links("Catchment", cid, "catchment_assets")
                items.append({**{k: c.get(k) for k in ("catchment_id", "name", "title", "source", "source_zh", "confidence", "area_ha", "shape_area_m2", "n_flood_points", "fp_ids")},
                              "n_assets": len(assets), "assets": [{k: a.get(k) for k in ("asset_id", "kind_zh", "name")} for a in assets[:10]],
                              "overlap": (r.get("catchment_overlap") or {}).get("value")})
            pts = [self._fp_coords(f) for it in items[:1] for f in it["fp_ids"]]
            out.update(mode="catchment", query=name, items=items,
                       _ui={"highlight": self._hl([p for p in pts if p], "points"), "fit": "highlight", "layers": ["c1u"] if any(i["source"] == "client_layer" for i in items) else ["kcatch"]})
            return out
        d = self.get("AnalysisDomain", dom)
        if d is None:
            raise ActionError(f"没有分析范围 {dom}（有：{list(self.load_domains())}）")
        units = self.links("AnalysisDomain", d["domain_id"], "domain_units")
        miss = [r for r in self.rules("AnalysisDomain", d) if r["rule"] == "domain_missing_expected" and r.get("fired")]
        out.update(mode="domain", domain={k: d.get(k) for k in ("domain_id", "name", "area_km2", "model_tags", "expected_fp_ids")},
                   units=[{k: u.get(k) for k in ("catchment_id", "name", "title", "area_ha", "n_flood_points")} for u in units],
                   n_flood_points=len(d.get("fp_ids") or []), expected_inside=[f for f in d["expected_fp_ids"] if f in (d.get("fp_ids") or [])],
                   missing=(miss[0]["value"] or {}).get("missing") if miss else [],
                   _ui={"highlight": self._hl([self._fp_coords(f) for f in d["expected_fp_ids"] if self._fp_coords(f)], "points"), "fit": "highlight", "layers": ["c1u"] if d["domain_id"] == "C1U" else ["c1"]})
        return out

    def _sum_catchment_info(self, r, P, lang):
        zh = lang == "zh"
        if r["mode"] == "point":
            if not r["catchments"]:
                s = f"{r['fp_id']} 不在任何客户汇水区单元里（客户图层 / KMZ / flood 库都没有）。" if zh else f"{r['fp_id']} lies in no client catchment unit."
            else:
                s = (f"{r['fp_id']} 所在汇水区：" + "；".join(f"{c['name']}（{c['source_zh']}，{c['area_ha']} ha）" for c in r["catchments"]) + "。") if zh else \
                    (f"{r['fp_id']} is in: " + "; ".join(f"{c['name']} ({c['source']}, {c['area_ha']} ha)" for c in r["catchments"]) + ".")
            for f in r["flags"]:
                s += (f" ⚠ {f['zh']}：{f['why']}" if zh else f" ⚠ {f['why_en']}")
            return s
        if r["mode"] == "catchment":
            parts = []
            for it in r["items"]:
                ov = it.get("overlap")
                parts.append((f"{it['name']}（{it['source_zh']}，{it['area_ha']} ha；可信度：{it['confidence']}）：积水点 {it['n_flood_points']} 个"
                              + (f"（{'、'.join(it['fp_ids'][:8])}{' …' if len(it['fp_ids']) > 8 else ''}）" if it["fp_ids"] else "")
                              + f"，登记设施 {it['n_assets']} 件" + (f"；⚠ {ov['why']}" if ov else "")) if zh else
                             (f"{it['name']} ({it['source']}, {it['area_ha']} ha): {it['n_flood_points']} flood points, {it['n_assets']} registered assets" + (f"; ⚠ {ov['why_en']}" if ov else "")))
            return ("；".join(parts) + "。") if zh else ("; ".join(parts) + ".")
        d = r["domain"]
        ms = "、".join(f"{m['fp_id']}（离边界 {m['dist_m']} m）" for m in r["missing"]) if r["missing"] else ""
        if zh:
            return (f"分析范围 {d['name']}：{d['area_km2']} km²，由 {len(r['units'])} 个汇水区单元组成" + (f"（{'、'.join(u['name'] for u in r['units'])}）" if r["units"] else "（不是按客户单元拼的）")
                    + f"；范围内积水点 {r['n_flood_points']} 个，应当包含的 {len(d['expected_fp_ids'])} 个热点里 {len(r['expected_inside'])} 个在内" + (f"，不在的：{ms}" if ms else "")
                    + f"。对应模型情景：{'、'.join(d['model_tags'])}。")
        return (f"Domain {d['domain_id']}: {d['area_km2']} km², {len(r['units'])} catchment units; {r['n_flood_points']} flood points inside; {len(r['expected_inside'])} of {len(d['expected_fp_ids'])} expected hotspots inside"
                + (f", missing: {', '.join(m['fp_id'] for m in r['missing'])}" if r["missing"] else "") + f". Model runs: {', '.join(d['model_tags'])}.")
