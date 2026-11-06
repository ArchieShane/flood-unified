"""一二维计算 / 报告交付 / 数据质检三个专业智能体的本体动作（多智能体，2026-09-30）。

归属见 schema.ActionType.owner：hydro_* → 一二维计算智能体，delivery_status / report_submit → 报告交付，qa_* → 数据质检。
只读动作读计算产物（$SWBRAIN_SWMM_WORK/c2d/、out/c1_stats_<tag>.json、out/交付/）；两个 *_submit 动作只写一条待批准的申请
（out/agents/jobs/），真正起计算要管理端批准（swbrain/agents/computejobs.py）。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import time

from ..common import ActionError

ROOT = pathlib.Path(__file__).resolve().parents[3]
TAGS = ("R61", "R96", "M61", "D24", "U61")
TAG_ZH = {"R61": "原计算域 61 mm / 2 h", "R96": "原计算域 96 mm / 2 h", "M61": "模型版计算域 61 mm / 2 h（以此为准）", "D24": "设计暴雨 61.34 mm / 24 h（原计算域，已作废）", "U61": "客户单元版 61 mm / 2 h（已被模型版取代）"}
# 无有效结果的情景（2026-10-02 用户定）：D24 那次用的是原计算域（8 个热点汇水区外扩 300 m，42.8 km²），不是模型版计算域（38.1 km²），
# 范围不对，结果作废；模型版下没算过设计暴雨。情景保留，但不再报那批数字，问到了就说明原因。
VOID = {"D24": "D24（设计暴雨 61.34 mm / 24 h）那次用的是原计算域（8 个热点汇水区外扩 300 m，42.8 km²），不是以此为准的模型版计算域"
               "（客户 RB3 + Musaffah_RD09 + B_C，38.1 km²）；范围不对，2026-10-02 已作废，结果不再提供。要模型版下的设计暴雨要重新建模并计算。"}
ST_ZH = {"L1": "L1 现状", "L2": "L2 + 移动泵", "L2P": "L2+planned", "L2S": "L2+suggested v2", "L2S3": "L2+suggested v3"}
STATES = ("L1", "L2", "L2P", "L2S", "L2S3")
UNION = "C1 八个热点|catch"
HS3 = ("ADM-94", "ADM-487", "ADM-192")
DELIV = {"R61": "C1_61mm2h", "R96": "C1_96mm2h", "U61": "C1_客户单元_61mm2h", "M61": "C1_模型版_61mm2h", "D24": "C1_设计暴雨_61.34mm24h"}


def _n(v):
    return f"{v:,.0f}" if isinstance(v, (int, float)) else "—"


def _jobs():
    from ...agents import computejobs
    return computejobs


def _requester(agent: str) -> dict:
    """申请记下是谁提的：智能体 + 发起这次对话的人（统一身份模式下是主体号）。留痕用：谁提交、谁批准事后可查。"""
    from ... import identity
    p = identity.current()
    return {"agent": agent, "actor": "agent", **({"principal": p.actor, "name": p.name} if p else {})}


def _pipe():
    spec = importlib.util.spec_from_file_location("c1_pipeline", ROOT / "tools" / "c1_pipeline.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def _stats(tag):
    p = ROOT / "out" / f"c1_stats_{tag}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _twoway(tag, st):
    p = _jobs().work_dir() / "c2d" / f"twoway_C1_{tag}_{st}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _continuity(tag, st):
    """一维 SWMM 报告里的连续性误差（取最后一处 = 流量演算）。"""
    p = _jobs().work_dir() / "c2d" / f"C1_{tag}_{st}_2way.rpt"
    if not p.exists():
        return None
    v = re.findall(r"Continuity Error \(%\) \.+\s+(-?[\d.]+)", p.read_text(encoding="utf-8", errors="replace"))
    return float(v[-1]) if v else None


def _states_of(tag):
    W = _jobs().work_dir()
    out = []
    for st in STATES:
        built = (W / f"C1_{tag}_{st}.inp").exists()
        done = (W / "c2d" / f"twoway_C1_{tag}_{st}.json").exists()
        if built or done:
            out.append({"state": st, "zh": ST_ZH[st], "built": built, "computed": done})
    return out


class HydroActions:
    # ---------------------------------------------------------------- 一二维计算智能体
    def _act_hydro_cases(self, P):
        tags = [P["tag"]] if P.get("tag") else list(TAGS)
        runs = [r for r in _jobs().external_runs() if not r.get("error")]
        rows = []
        for t in tags:
            if t in VOID:                                             # 作废的情景：只回一条说明，不报旧数字
                rows.append({"tag": t, "zh": TAG_ZH[t], "void": VOID[t], "states": [], "post_done": False, "union_by_state": {}, "running": []})
                continue
            S = _stats(t); sts = _states_of(t)
            if not sts and not S:
                continue
            m = {}
            if S:
                z = (S.get("zones") or {}).get(UNION) or {}
                m = {st: {"area_ge_0p30_ha": (z.get(st) or {}).get("area_ge_0p30_ha"), "peak_vol_m3": (z.get(st) or {}).get("peak_vol_m3")} for st in z}
            running = [{"state": r["state"], "pct": r.get("pct"), "paused": r.get("paused"), "eta_h": r.get("eta_h")} for r in runs if r.get("name") == f"C1_{t}"]
            rows.append({"tag": t, "zh": TAG_ZH[t], "states": sts, "post_done": bool(S), "union_by_state": m, "running": running})
        return {"cases": rows, "_ui": {}}

    def _sum_hydro_cases(self, r, P, lang):
        zh = lang == "zh"; parts = []
        for c in r["cases"]:
            if c.get("void"):                                        # 作废的情景：一句说明，不报旧数字
                parts.append((f"{c['tag']}（{c['zh']}）：{c['void']}" if zh else f"{c['tag']}: {c['void']}"))
                continue
            done = [s["state"] for s in c["states"] if s["computed"]]
            run = [f"{x['state']} {x['pct']}%{'（暂停）' if x['paused'] else ''}" for x in c["running"]]
            a = c["union_by_state"]; best = a.get("L2S3") or a.get("L2P") or {}; base = a.get("L1") or {}
            eff = (f"，8 个热点汇水区 ≥0.30 m 积水 L1 {base.get('area_ge_0p30_ha')} ha → {('L2S3' if a.get('L2S3') else 'L2P')} {best.get('area_ge_0p30_ha')} ha" if base and best else "") if zh else ""
            parts.append((f"{c['tag']}（{c['zh']}）：已算 {'/'.join(done) or '无'}" + (f"；在算 {'、'.join(run)}" if run else "") + ("" if c["post_done"] else "；未做后处理") + eff) if zh else
                         f"{c['tag']}: computed {'/'.join(done) or 'none'}" + (f"; running {', '.join(run)}" if run else ""))
        return ("；".join(parts) + "。") if zh else ("; ".join(parts) + ".")

    def _act_hydro_result(self, P):
        t = P["tag"]
        if t in VOID:
            raise ActionError(VOID[t])
        S = _stats(t)
        if not S:
            raise ActionError(f"{t} 还没有后处理结果（out/c1_stats_{t}.json）；先算完并做后处理")
        zones = S.get("zones") or {}
        sts = [P["state"]] if P.get("state") else [s for s in STATES if s in (zones.get(UNION) or {})]
        hs = P.get("hotspot")
        keys = ([f"{hs}|catch", f"{hs}|200m"] if hs else [UNION]) + ([] if hs else [f"{h}|200m" for h in HS3])
        miss = [k for k in keys if k not in zones]
        if hs and miss:
            raise ActionError(f"{t} 的统计里没有 {hs}（有：{sorted({k.split('|')[0] for k in zones})[:12]}）")
        out = {"tag": t, "zh": TAG_ZH[t], "rain_mm": S.get("rain_mm"), "zones": {}}
        for k in keys:
            if k in zones:
                out["zones"][k] = {st: zones[k].get(st) for st in sts if zones[k].get(st)}
        out["deepest"] = {st: (S.get("deepest") or {}).get(st) for st in sts}
        out["_ui"] = {}
        return out

    def _sum_hydro_result(self, r, P, lang):
        zh = lang == "zh"; lines = []
        for k, by in r["zones"].items():
            nm = k.replace("|catch", " 汇水区").replace("|200m", " 200 m 内") if zh else k
            seg = [(f"{ST_ZH.get(st, st)}：≥0.30 m {v.get('area_ge_0p30_ha')} ha、最大积水量 {_n(v.get('peak_vol_m3'))} m³、最深 {v.get('max_depth_m')} m" if zh else
                    f"{st}: ≥0.30 m {v.get('area_ge_0p30_ha')} ha, peak volume {_n(v.get('peak_vol_m3'))} m³, max depth {v.get('max_depth_m')} m") for st, v in by.items() if v]
            if seg:
                lines.append(f"{nm}——" + "；".join(seg) if zh else f"{nm} — " + "; ".join(seg))
        u = r["zones"].get(UNION) or {}
        if "L1" in u and len(u) > 1:                                  # 各态比 L1：8 个热点汇水区 ≥0.30 m 面积与最大积水量的变化
            b = u["L1"]
            def pct(x, y):
                return f"{(x - y) / y * 100:+.1f}%" if isinstance(x, (int, float)) and isinstance(y, (int, float)) and y else "—"
            cmp_ = [(f"{ST_ZH.get(st, st)} {pct(v.get('area_ge_0p30_ha'), b.get('area_ge_0p30_ha'))}（面积）/ {pct(v.get('peak_vol_m3'), b.get('peak_vol_m3'))}（积水量）" if zh else
                     f"{st} {pct(v.get('area_ge_0p30_ha'), b.get('area_ge_0p30_ha'))} area / {pct(v.get('peak_vol_m3'), b.get('peak_vol_m3'))} volume") for st, v in u.items() if st != "L1" and v]
            lines.insert(0, ("与 L1 相比（8 个热点汇水区 ≥0.30 m 积水面积 / 最大积水量）：" if zh else "vs L1 (8-hotspot catchments, ≥0.30 m area / peak volume): ") + ("；".join(cmp_) if zh else "; ".join(cmp_)))
        head = f"{r['tag']}（{r['zh']}）" if zh else r["tag"]
        return head + ("：" if zh else ": ") + ("。".join(lines) if zh else ". ".join(lines)) + ("。" if zh else ".")

    def _act_hydro_ponding(self, P):
        from ... import ponding
        runs = ponding.available()
        run = P.get("run")
        void = {f"C1_{t}": why for t, why in VOID.items()}                  # 作废的情景不报积水点
        if not run:
            return {"runs": {k: v for k, v in runs.items() if k not in void}, "void": sorted(k for k in runs if k in void), "_ui": {}}
        if run in void:
            raise ActionError(void[run])
        if run not in runs:
            raise ActionError(f"没有运行 {run} 的二维结果（有：{', '.join(sorted(runs)) or '无'}）")
        have = runs[run]
        st = P.get("state")
        if st and st not in have:
            alt = [s for s in have if s.startswith(st[:3])] if st.startswith("L2S") else []   # 问 suggested 时取这个运行最新的 L2S 版本
            if not alt:
                raise ActionError(f"{run} 没有 {st}（有：{', '.join(have)}）")
            st = alt[-1]
        kw = {k: P[k] for k in ("depth", "min_area", "min_dur") if P.get(k) is not None}
        out = {"run": run, "states": have, "hotspot": P.get("hotspot"), "by_state": {}, "_ui": {}}
        for s in ([st] if st else have):
            try:
                r = ponding.compute(run, s, geometry=False, pond_scope="state", **kw)
            except (ValueError, FileNotFoundError) as ex:
                raise ActionError(str(ex))
            fs = ponding.near_hotspot(r, P["hotspot"]) if P.get("hotspot") else r["feats"]
            out["params"] = r["params"]; out["adjacency"] = r["adjacency"]; out["t_end_h"] = r["t_end_h"]
            out["by_state"][s] = {"n": len(fs), "vol_total_m3": int(sum(f["vol_peak_m3"] for f in fs)), "n_not_receded": sum(f["recede_h"] is None for f in fs),
                                  "top": [{k: f[k] for k in ("id", "hmax_m", "area_m2", "vol_peak_m3", "t_peak_h", "recede_h", "near", "nearest", "lon", "lat")}
                                          for f in fs[:int(P.get("top") or 8)]] if st else []}
        return out

    def _sum_hydro_ponding(self, r, P, lang):
        zh = lang == "zh"
        if "by_state" not in r:
            lst = "；".join(f"{k}（{'/'.join(v)}）" for k, v in sorted(r["runs"].items()))
            vd = "、".join(r.get("void") or [])
            return (f"有二维结果的运行：{lst}。问的时候带上运行名，比如 RB01_H60。" + (f"{vd} 已作废，不报。" if vd else "") if zh else
                    f"Runs with 2D results: {lst}. Name one, e.g. RB01_H60." + (f" {vd} is void." if vd else ""))
        p = r.get("params") or {}
        head = (f"{r['run']} 二维模拟积水点（阈值 ≥{p.get('depth'):g} m、≥{p.get('min_area'):g} m²、≥{p.get('min_dur'):g} h，试算值未与客户对齐；编号 SIM-… 不是客户热点编号"
                + (f"；只看客户热点 {r['hotspot']} 200 m 内" if r.get("hotspot") else "") + "）：") if zh else \
               (f"{r['run']} simulated ponding points (≥{p.get('depth'):g} m, ≥{p.get('min_area'):g} m², ≥{p.get('min_dur'):g} h; SIM ids are not client hotspot ids"
                + (f"; within 200 m of {r['hotspot']}" if r.get("hotspot") else "") + "): ")
        parts = []
        for s, v in r["by_state"].items():
            seg = (f"{s} {v['n']} 个、峰值积水量合计 {_n(v['vol_total_m3'])} m³、{v['n_not_receded']} 个到 {r.get('t_end_h')} h 未退" if zh else
                   f"{s}: {v['n']} points, total peak volume {_n(v['vol_total_m3'])} m³, {v['n_not_receded']} not receded by {r.get('t_end_h')} h")
            if v["top"]:
                seg += ("；最大的：" if zh else "; largest: ") + ("、" if zh else ", ").join(
                    (f"{f['id']}（{_n(f['vol_peak_m3'])} m³、深 {f['hmax_m']} m、{_n(f['area_m2'])} m²、最近客户热点 {f['nearest'][0]} {f['nearest'][1]} m）" if zh else
                     f"{f['id']} ({_n(f['vol_peak_m3'])} m³, {f['hmax_m']} m deep, {_n(f['area_m2'])} m², nearest client hotspot {f['nearest'][0]} {f['nearest'][1]} m)") if f.get("nearest") else
                    (f"{f['id']}（{_n(f['vol_peak_m3'])} m³）" if zh else f"{f['id']} ({_n(f['vol_peak_m3'])} m³)") for f in v["top"])
            parts.append(seg)
        return head + ("；".join(parts) + "。" if zh else "; ".join(parts) + ".")

    def _act_hydro_progress(self, P):
        J = _jobs()
        runs = J.external_runs()
        jobs = J.list_jobs()
        queue = [{k: j.get(k) for k in ("id", "title", "status", "est_h", "created", "progress", "note")} for j in jobs if j["status"] not in J.FINAL]
        st = sorted((j for j in jobs if j.get("kind") == "selftest" and j["status"] in ("done", "failed")), key=lambda j: j.get("finished") or "")
        last = {k: st[-1].get(k) for k in ("id", "status", "finished", "verify")} if st else None   # 最近一次走队列的快速验证
        return {"runs": runs, "queue": queue, "last_selftest": last, "_ui": {}}

    def _sum_hydro_progress(self, r, P, lang):
        zh = lang == "zh"
        s = r.get("last_selftest"); v = (s or {}).get("verify") or {}
        tail = "" if not s else ((f"最近一次快速验证 {s['id']}（{s['finished']}）：" + (f"{v['passed']} / {v['n']} 项通过" + ("" if v["all_ok"] else "，未过：" + "、".join(v["failed"])) if v else f"{s['status']}，没有判定表")) + "。"
                                 if zh else f" Last selftest {s['id']}: " + (f"{v['passed']}/{v['n']} passed." if v else f"{s['status']}, no verify table."))
        if not r["runs"] and not r["queue"]:
            return ("现在没有在算的一二维计算，队列里也没有待办。" if zh else "No 1D/2D run in progress and nothing queued.") + tail
        rs = [(f"{x['name']} {x['state']}：{x.get('t_h', '?')} / {x.get('end_h', '?')} h（{x.get('pct', '?')}%）" + ("，暂停中" if x.get("paused") else (f"，约 {x['eta_h']} h 后算完（粗估）" if x.get("eta_h") is not None else "")))
              if zh else f"{x['name']} {x['state']}: {x.get('pct', '?')}%" + (" (paused)" if x.get("paused") else "") for x in r["runs"] if not x.get("error")]
        qs = [(f"{j['title']}（{j['status']}，预计 {j['est_h']} h）" if zh else f"{j['title']} ({j['status']})") for j in r["queue"]]
        return ((("在算：" + "；".join(rs) + "。" if rs else "") + ("队列：" + "；".join(qs) + "。" if qs else "")) if zh else (("Running: " + "; ".join(rs) + ". " if rs else "") + ("Queue: " + "; ".join(qs) + "." if qs else ""))) + tail

    def _act_hydro_submit(self, P):
        J = _jobs()
        states = [s.strip().upper() for s in re.split(r"[,，/\s]+", P.get("states") or "") if s.strip()]
        params = {k: P[k] for k in ("res", "tif_res", "sww_res", "rain", "dur", "run") if P.get(k) not in (None, "")}
        if P.get("domain_source"):
            params["domain"] = {"source": P["domain_source"], "ids": [x.strip() for x in re.split(r"[,，]+", P.get("domain_ids") or "") if x.strip()],
                                "buffer_m": P.get("buffer_m") or 0}
        try:
            j = J.submit(P.get("kind") or "compute", P.get("tag") or "", states or None, bool(P.get("then_v3")), P.get("reason") or "", _requester("hydro"), params=params)
        except ValueError as ex:                                     # DomainError 是 ValueError 的子类
            raise ActionError(str(ex))
        return {"job": {k: j.get(k) for k in ("id", "title", "run", "steps", "est_h", "status", "duplicate", "km2", "n_tri", "params", "notes", "warnings")}, "_ui": {}}

    def _sum_hydro_submit(self, r, P, lang):
        j = r["job"]
        if lang == "zh":
            pr = j.get("params") or {}
            extra = (f"运行名 {j.get('run')}，计算域 {j.get('km2')} km²、网格 {pr.get('res', 15):g} m（约 {j.get('n_tri', 0):,} 个三角单元）、雨 {pr.get('rain', 61):g} mm / {pr.get('dur', 2):g} h，"
                     if pr else "")
            tips = "；".join((j.get("warnings") or []) + (j.get("notes") or []))
            return (f"已有同样的申请 {j['id']}（{j['status']}），没有重复提交。" if j.get("duplicate") else
                    f"已提交计算申请 {j['id']}：{j['title']}，{extra}步骤 {' → '.join(j['steps'])}，预计墙钟约 {j['est_h']} h。要在管理端批准后才会开始计算。"
                    + (f"注意：{tips}" if tips else ""))
        return (f"Same request {j['id']} already exists ({j['status']})." if j.get("duplicate") else
                f"Submitted request {j['id']}: {j['title']}, steps {' → '.join(j['steps'])}, ~{j['est_h']} h wall clock. It starts only after approval in the admin console.")

    # ---------------------------------------------------------------- 报告交付智能体
    def _act_delivery_status(self, P):
        base = ROOT / "out" / "交付"
        tags = [P["tag"]] if P.get("tag") else list(TAGS)
        rows = []
        for t in tags:
            d = base / DELIV[t]
            if not d.exists():
                rows.append({"tag": t, "zh": TAG_ZH[t], "folder": str(d), "exists": False}); continue
            files = [p for p in d.rglob("*") if p.is_file()]
            rows.append({"tag": t, "zh": TAG_ZH[t], "folder": str(d), "exists": True, "n_files": len(files), "size_gb": round(sum(p.stat().st_size for p in files) / 1e9, 2),
                         "states": sorted(p.name for p in d.iterdir() if p.is_dir() and p.name not in ("公共", "统计值")),
                         "has": {k: (d / k).exists() for k in ("readme.md", "计算设置.md", "资料清单.md", "统计值/C1统计值.xlsx")}})
        links = {}
        lf = ROOT / "cases" / "C1" / "links.json"
        if lf.exists():
            links = json.loads(lf.read_text(encoding="utf-8"))
        rep = ROOT / "out" / "c1_report.html"
        return {"deliveries": rows, "links": links, "report_local_updated": (time.strftime("%Y-%m-%d %H:%M", time.localtime(rep.stat().st_mtime)) if rep.exists() else None), "_ui": {}}

    def _sum_delivery_status(self, r, P, lang):
        zh = lang == "zh"
        ds = [(f"{d['tag']}：{d['folder']}（{d['n_files']} 个文件，{d['size_gb']} GB，态 {'/'.join(d['states'])}）" if d["exists"] else f"{d['tag']}：还没有交付目录") if zh else
              (f"{d['tag']}: {d['folder']} ({d['n_files']} files, {d['size_gb']} GB)" if d["exists"] else f"{d['tag']}: no delivery folder yet") for d in r["deliveries"]]
        L = r["links"]
        tail = (f"。在线报告 {L['report']['url']}；资料清单 {L['materials']['url']}" if zh else f". Online report {L['report']['url']}; materials list {L['materials']['url']}") if L.get("report") else ""
        return ("；".join(ds) if zh else "; ".join(ds)) + tail + ("。" if zh else ".")

    def _act_report_submit(self, P):
        J = _jobs()
        try:
            j = J.submit("post", P["tag"], None, False, P.get("reason") or "", _requester("report"))
        except ValueError as ex:
            raise ActionError(str(ex))
        return {"job": {k: j.get(k) for k in ("id", "title", "steps", "est_h", "status", "duplicate")}, "_ui": {}}

    def _sum_report_submit(self, r, P, lang):
        return self._sum_hydro_submit(r, P, lang)

    # ---------------------------------------------------------------- 数据质检智能体
    def _act_qa_data(self, P):
        st = _pipe().status(P.get("tag") or "M61")
        vf = ROOT / "out" / "demo" / "verify.json"                     # 快速验证（tools/demo_case.py，合成算例）最近一次的结果
        demo = json.loads(vf.read_text(encoding="utf-8")) if vf.exists() else None
        st["demo"] = {"ok": demo["ok"], "when": demo.get("when"), "n": len(demo["checks"]), "failed": [c["check"] for c in demo["checks"] if not c["ok"]]} if demo else None
        return {**st, "_ui": {}}

    def _sum_qa_data(self, r, P, lang):
        zh = lang == "zh"
        bad = [d["key"] for d in r["data"] if not d["ok"]] + [e["item"] for e in r["env"] if not e["ok"]]
        todo = [s["step"] for s in r["steps"] if not s["done"]]
        if zh:
            return ((f"数据与环境齐了（{len(r['data'])} 项客户数据、docker 镜像、Python 依赖都在）" if not bad else f"缺 {len(bad)} 项：{'、'.join(bad)}（路径写进 config.toml [data]，见 docs/C1_资料清单.md）")
                    + (f"；{r['tag']} 还差这些步骤：{'、'.join(todo)}" if todo else f"；{r['tag']} 的全部步骤都已完成")
                    + ("；快速验证（合成算例）还没跑过：python tools/c1_pipeline.py selftest。" if not r.get("demo") else
                       f"；快速验证（合成算例，{r['demo']['when']}）{'全部 ' + str(r['demo']['n']) + ' 项通过' if r['demo']['ok'] else '未通过：' + '、'.join(r['demo']['failed'])}。"))
        return ("Data and environment complete" if not bad else f"Missing {len(bad)}: {', '.join(bad)}") + (f"; {r['tag']} still needs: {', '.join(todo)}." if todo else f"; all {r['tag']} steps done.")

    def _act_qa_results(self, P):
        t = P["tag"]; S = _stats(t) or {}
        sts = [P["state"]] if P.get("state") else [s["state"] for s in _states_of(t) if s["computed"]]
        if not sts:
            raise ActionError(f"{t} 还没有算完的态")
        rows = []
        for st in sts:
            tw = _twoway(t, st); ce = _continuity(t, st); bal = (S.get("balance") or {}).get(st) or {}
            checks = []
            if tw:
                n = len(tw.get("series") or []); want = int(round(float(tw.get("end_h") or 6) * 12))
                checks.append({"check": "算完（帧数）", "value": f"{n} / {want}", "ok": n >= want, "rule": "每 5 分钟一帧，帧数够"})
            else:
                checks.append({"check": "算完", "value": "没有运行记录", "ok": False, "rule": f"twoway_C1_{t}_{st}.json 存在"})
            if ce is not None:
                checks.append({"check": "一维连续性误差", "value": f"{ce:.2f} %", "ok": abs(ce) < 2.0, "rule": "< 2 %"})
            a, b = bal.get("1D→2D"), bal.get("一维：域内井溢流（=交给二维）")
            if a and b:
                d = abs(a - b) / max(a, 1) * 100
                checks.append({"check": "一二维交换对账", "value": f"差 {d:.2f} %", "ok": d < 0.2, "rule": "域内井溢流 = 交给二维的水，差 < 0.2 %"})
            if st in ("L2S", "L2S3"):
                sp = ROOT / "out" / f"c1_{st}_side_{t}.json"
                if sp.exists():
                    v = json.loads(sp.read_text(encoding="utf-8")).get("deeper_total_ha")
                    checks.append({"check": "副作用（比 L2+planned 变深）", "value": f"{v} ha", "ok": (v or 0) < 0.5, "rule": "v3 应接近 0（< 0.5 ha）"})
            rows.append({"state": st, "zh": ST_ZH.get(st, st), "checks": checks, "ok": all(c["ok"] for c in checks)})
        return {"tag": t, "states": rows, "ok": all(r["ok"] for r in rows), "_ui": {}}

    def _sum_qa_results(self, r, P, lang):
        zh = lang == "zh"
        seg = []
        for s in r["states"]:
            bad = [c for c in s["checks"] if not c["ok"]]
            seg.append((f"{s['zh']} {'全部通过' if not bad else '未通过：' + '、'.join(c['check'] + ' ' + c['value'] + '（标准 ' + c['rule'] + '）' for c in bad)}"
                        + "（" + "，".join(f"{c['check']} {c['value']}" for c in s["checks"] if c["ok"]) + "）") if zh else
                       f"{s['state']} {'pass' if not bad else 'FAIL: ' + ', '.join(c['check'] + ' ' + c['value'] for c in bad)}")
        return (f"{r['tag']} 结果质检：" + "；".join(seg) + "。") if zh else (f"{r['tag']} QA: " + "; ".join(seg) + ".")
