"""优化侧（what-if）：把建议变成干预集合，评估、组合、排期。

评估器两种：
  SurrogateEvaluator  本系统自己的粗算（曼宁 + 推理公式），秒级，用来筛候选、给排序兜底
  ExternalEvaluator   把干预集合写成 whatif_request 交给预测侧（水动力/世界模型），读回 whatif_result 里的 KPI
两种评估器给出同名 KPI；有外部结果时以外部为准，没有时标「待外部模型评估」。
"""
from __future__ import annotations
import json
import math
from dataclasses import asdict
from pathlib import Path
import numpy as np
import pandas as pd
import networkx as nx

from . import hydraulics as hy
from .contracts import Intervention, InterventionSet, Scenario, WhatIfRequest, load_whatif_result, KPI_KEYS

WORK_KINDS = {"扩管改造", "新增调蓄塘/池", "新增渗井群", "出水口增泵/改闸", "泵站增容校核", "连通至有出路管网", "就地消纳（渗井/塘）"}
VERIFY_KINDS_WITH_TRIAL = {"核实拓扑（疑似流向/连接错误）", "核实拓扑（疑似缺失连接）"}


# ---------------------------------------------------------------- 建议 → 干预
def proposal_to_interventions(p: dict, cs: pd.DataFrame, cfg: dict, main: str) -> list[Intervention]:
    a = p.get("action") or {}
    if isinstance(a, str):
        a = json.loads(a)
    kind = p["kind"]
    term = p.get("terminal")
    trow = cs[cs["terminal"] == term].iloc[0] if term is not None and (cs["terminal"] == term).any() else None
    pid = int(p["pid"])
    if kind == "扩管改造" and p.get("gid") is not None:
        if a.get("建议管径mm"):
            return [Intervention("pipe_upsize", {"gid": int(p["gid"])}, {"diameter_mm": float(a["建议管径mm"])}, pid)]
        return [Intervention("pipe_upsize", {"gid": int(p["gid"])}, {"diameter_mm": 3000.0}, pid, note="所需管径超过 DN3000：按 DN3000 试算，实际要靠蓄渗或行泄通道分流，需预测侧评估")]
    if kind in ("新增调蓄塘/池", "新增渗井群"):
        return [Intervention("add_storage", {"vertex_id": int(term), "lon": p.get("lon"), "lat": p.get("lat")},
                             {"volume_m3": float(a.get("需补容量m3", 0)), "outlet_type": "infiltration" if kind == "新增渗井群" else "orifice"}, pid)]
    if kind in ("出水口增泵/改闸", "泵站增容校核"):
        q = float(a.get(f"设计流量_{main}_m3s", 0) or 0)
        tgt = {"outfall_unitid": trow["terminal_unitid"] if trow is not None else None, "vertex_id": int(term)}
        out = [Intervention("add_pump", tgt, {"capacity_m3s": round(q, 3), "on_level_m": cfg["model"]["tide_high_m"] - 0.5, "off_level_m": cfg["model"]["tide_high_m"] - 1.5}, pid)]
        if kind == "出水口增泵/改闸":
            out.append(Intervention("tide_gate", {"outfall_unitid": tgt["outfall_unitid"]}, {"mode": "controlled"}, pid))
        return out
    if kind == "连通至有出路管网":
        return [Intervention("connect", {"from_vertex_id": int(term), "to_vertex_id": None}, {"diameter_mm": 600.0, "length_m": float(a.get("距有出路节点m") or 0)}, pid,
                             note="to_vertex_id 由预测侧按最近有出路节点确定，或人工指定")]
    if kind == "就地消纳（渗井/塘）":
        return [Intervention("add_storage", {"vertex_id": int(term), "lon": p.get("lon"), "lat": p.get("lat")},
                             {"volume_m3": float(a.get("需消纳径流m3", 0)), "outlet_type": "infiltration"}, pid)]
    if kind == "事件前临时泵位":
        return [Intervention("temp_pump", {"vertex_id": int(term)} if term is not None else {"vertex_id": None}, {"capacity_m3s": 0.15, "hours_before_event": 24}, pid)]
    if kind == "核实拓扑（疑似流向/连接错误）" and p.get("gid") is not None:
        return [Intervention("fix_topology", {"gid": int(p["gid"])}, {"action": "reverse"}, pid, note="先核实再改；此处按最可能的『流向反了』给预测侧一个试算版本")]
    if kind == "核实拓扑（疑似缺失连接）" and term is not None:
        return [Intervention("connect", {"from_vertex_id": int(term), "to_vertex_id": None}, {"diameter_mm": 300.0, "length_m": float(a.get("距有出路节点m") or 5)}, pid,
                             note="试算版本：把缺失的连接补上，看这一支的汇水归属变化")]
    return []


# ---------------------------------------------------------------- 成本
def cost_of(iv: Intervention, cfg: dict, es: pd.DataFrame | None = None) -> float:
    c = cfg["cost"]
    if iv.type == "pipe_upsize":
        d = iv.params["diameter_mm"]
        length = 200.0
        if es is not None and "gid" in iv.target:
            row = es[es["gid"] == iv.target["gid"]]
            if len(row):
                length = float(row["len_m"].iat[0])
        rate = c["pipe_aed_per_m_by_dn"]
        key = min((k for k in rate if float(k) >= d), key=float, default=max(rate, key=float))
        return length * float(rate[key])
    if iv.type == "add_storage":
        return float(iv.params.get("volume_m3", 0)) * float(c["storage_aed_per_m3"]) + float(c["storage_fixed_aed"])
    if iv.type == "add_pump":
        return float(iv.params.get("capacity_m3s", 0)) * float(c["pump_aed_per_m3s"]) + float(c["pump_fixed_aed"])
    if iv.type == "tide_gate":
        return float(c["tide_gate_aed"])
    if iv.type == "connect":
        return max(float(iv.params.get("length_m", 0)), 20.0) * float(c["pipe_aed_per_m_by_dn"]["600"]) + float(c["connect_fixed_aed"])
    if iv.type == "fix_topology":
        return float(c["survey_aed_per_item"])
    if iv.type in ("temp_pump", "pre_drain"):
        return float(c["temp_pump_aed_per_event"])
    return 0.0


# ---------------------------------------------------------------- 代理评估器
class SurrogateEvaluator:
    """在本系统的管段状态上直接改、直接重算。够做排序，不出淹没深度。"""

    def __init__(self, es: pd.DataFrame, cs: pd.DataFrame, cfg: dict, scenarios: dict[str, float]):
        self.cfg, self.scen = cfg, scenarios
        self.es = es[es["is_active"]].copy().set_index("gid", drop=False)
        self.cs = cs.set_index("terminal", drop=False)
        self.g = nx.DiGraph()
        self.g.add_edges_from(zip(self.es["u"].astype(int), self.es["v"].astype(int), ({"gid": int(x)} for x in self.es["gid"])))
        self.base = self.kpis(self.es, self.cs, None)

    def kpis(self, es: pd.DataFrame, cs: pd.DataFrame, scenario: str | None) -> dict:
        names = [scenario] if scenario else list(self.scen)
        out = {}
        for n in names:
            over = es[es[f"ratio_{n}"] > self.cfg["decision"]["ratio_threshold"]]
            out[f"surcharge_len_km@{n}"] = round(float(over["len_m"].sum()) / 1000, 2)
            out[f"affected_inlets@{n}"] = int(over.groupby("terminal")["n_inlets_up"].max().sum())
        thr = self.cfg["decision"]["tide_clearance_min_m"]
        tide = cs[(cs["terminal_kind"] == "出水口") & (cs["tide_clearance_m"] < thr) & (cs["n_pumps"] <= 0)]
        out["tide_exposed_inlets"] = int(tide["n_inlets"].sum())
        out["storage_deficit_m3"] = round(float(cs["storage_deficit_m3"].sum()), 0)
        out["no_outlet_len_km"] = round(float(es[~es["terminal_kind"].isin(["出水口", "泵站", "渗井"])]["len_m"].sum()) / 1000, 1)
        return out

    def _downstream_gids(self, node: int) -> list[int]:
        gids, cur, seen = [], node, set()
        while cur not in seen:
            seen.add(cur)
            outs = list(self.g.successors(cur))
            if not outs:
                break
            nxt = max(outs, key=lambda v: self.es.loc[self.g[cur][v]["gid"], "area_m2"])
            gids.append(self.g[cur][nxt]["gid"])
            cur = nxt
        return gids

    def apply(self, ivs: list[Intervention]) -> tuple[pd.DataFrame, pd.DataFrame]:
        es, cs = self.es.copy(), self.cs.copy()
        m = self.cfg["model"]
        dur_s = m["duration_min"] * 60.0
        for iv in ivs:
            if iv.type == "pipe_upsize" and iv.target.get("gid") in es.index:
                gid = iv.target["gid"]
                r = es.loc[gid]
                es.loc[gid, "pipe_diameter"] = iv.params["diameter_mm"]
                es.loc[gid, "q_full"] = float(hy.manning_full_flow(iv.params["diameter_mm"] / 1000.0, r["slope"], r["n_manning"]))
            elif iv.type == "add_storage" and iv.target.get("vertex_id") is not None:
                dq = float(iv.params.get("volume_m3", 0)) / dur_s
                for gid in self._downstream_gids(int(iv.target["vertex_id"])):
                    for n in self.scen:
                        es.loc[gid, f"q_{n}"] = max(0.0, es.loc[gid, f"q_{n}"] - dq)
                t = int(iv.target["vertex_id"])
                if t in cs.index:
                    cs.loc[t, "storage_m3"] = cs.loc[t, "storage_m3"] + float(iv.params.get("volume_m3", 0))
                    cs.loc[t, "storage_deficit_m3"] = max(0.0, cs.loc[t, "runoff_m3"] - cs.loc[t, "storage_m3"]) if cs.loc[t, "needs_storage"] else 0.0
            elif iv.type in ("add_pump", "tide_gate") and iv.target.get("vertex_id") in cs.index:
                cs.loc[iv.target["vertex_id"], "n_pumps"] = max(1, int(cs.loc[iv.target["vertex_id"], "n_pumps"]))
            elif iv.type == "connect" and iv.target.get("from_vertex_id") in cs.index:
                t = iv.target["from_vertex_id"]
                cs.loc[t, "terminal_kind"] = "出水口"
                cs.loc[t, "storage_deficit_m3"] = 0.0
                es.loc[es["terminal"] == t, "terminal_kind"] = "出水口"
        for n in self.scen:
            es[f"ratio_{n}"] = es[f"q_{n}"] / es["q_full"].where(es["q_full"] > 1e-9)
        return es, cs

    def evaluate(self, s: InterventionSet) -> dict:
        es, cs = self.apply(s.interventions)
        k = self.kpis(es, cs, None)
        return {"kpis": k, "delta": {key: round(k[key] - self.base[key], 3) for key in k}, "source": "surrogate"}


# ---------------------------------------------------------------- 外部评估器
class ExternalEvaluator:
    """文件交换：写 exchange/requests/<id>.json，读 exchange/results/<id>.json。有 HTTP 地址时也 POST 一份。"""

    def __init__(self, cfg: dict, root: Path):
        self.cfg, self.root = cfg, root
        self.req_dir = root / cfg["exchange"]["dir"] / "requests"
        self.res_dir = root / cfg["exchange"]["dir"] / "results"
        self.req_dir.mkdir(parents=True, exist_ok=True); self.res_dir.mkdir(parents=True, exist_ok=True)

    def submit(self, sets: list[InterventionSet], scenarios: list[Scenario], network_version: str, baseline: str | None) -> WhatIfRequest:
        req = WhatIfRequest.new(network_version, scenarios, sets, baseline, reply_to=str(self.res_dir))
        req.dump(self.req_dir / f"{req.request_id}.json")
        url = self.cfg["exchange"].get("http_url")
        if url:
            try:
                import urllib.request
                data = json.dumps(asdict(req), ensure_ascii=False).encode("utf-8")
                urllib.request.urlopen(urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}), timeout=10)
            except Exception as e:  # 预测侧不在线不算错，文件已经落盘
                print("  外部模型 HTTP 提交失败（文件已写）：", str(e)[:120])
        return req

    def collect(self) -> dict[tuple[str, str], dict]:
        """读所有结果：{(set_id, scenario): {kpis, delta, run}}。"""
        out = {}
        for f in sorted(self.res_dir.glob("*.json")):
            try:
                d = load_whatif_result(f)
            except Exception as e:
                print("  跳过无效结果文件", f.name, str(e)[:100]); continue
            for r in d["results"]:
                out[(r["set_id"], r["scenario"])] = {"kpis": r["kpis"], "delta": r.get("kpi_delta_vs_baseline", {}), "run": r.get("prediction_run"), "file": f.name, "source": "external"}
        return out


# ---------------------------------------------------------------- 达标成本（回答「要多少钱才能全网不满管」）
def cost_to_standard(es: pd.DataFrame, cfg: dict, scenarios: dict[str, float]) -> dict:
    """按每段管各自扩到目标能力比所需的最小标准管径，累加占位单价，得到每个雨强场景的『全网达标』扩管成本上限。
    静态估算：不考虑下游连锁、不考虑用调蓄替代扩管，所以是上限而不是方案；也不含 61 个疑似漏登终点核实后的变化。"""
    D, c = cfg["decision"], cfg["cost"]
    act = es[es["is_active"] & (es["diameter_flag"] == "ok")]
    rate = c["pipe_aed_per_m_by_dn"]
    keys = sorted(rate, key=float)
    def unit(d):
        for k in keys:
            if float(k) >= d:
                return float(rate[k])
        return float(rate[keys[-1]])
    out = {}
    for name, i in scenarios.items():
        over = act[act[f"ratio_{name}"] > D["ratio_threshold"]]
        total, km, n_unfix = 0.0, 0.0, 0
        for r in over.itertuples(index=False):
            d = hy.required_diameter_mm(getattr(r, f"q_{name}"), r.slope, r.n_manning, D["target_ratio"], D["standard_diameters_mm"])
            if d is None:
                n_unfix += 1; d = 3000.0
            total += float(r.len_m) * unit(d); km += float(r.len_m) / 1000.0
        out[name] = {"雨强mm/h": i, "需扩管km": round(km, 1), "扩管成本上限_亿AED": round(total / 1e8, 1), "DN3000仍不够的段": int(n_unfix)}
    return out


# ---------------------------------------------------------------- 按标准达标：项目化、分期、X 年
def standard_plan(es: pd.DataFrame, cs: pd.DataFrame, cfg: dict, scenarios: dict[str, float], target: str | None = None) -> dict:
    """目标情景下，把全网达标所需的工程按汇水区打包成项目，按 收益/成本 排序，按年度预算分期。
    静态估算（不经预测模型），给的是『X 年、多少钱、每年干什么』的量级；方案本身要经预测侧评估。"""
    S, D, c = cfg["standard"], cfg["decision"], cfg["cost"]
    T = target or S["target_scenario"]
    if T == "法定标准":
        # 每段管按自己的设计重现期与历时（DMT 表 3-2 / 3-3）：列在 standards.design_intensity 里
        if "ratio_design" not in es.columns:
            return {"error": "缺 ratio_design 列，先跑 standards.design_intensity"}
        qcol, rcol, peak, rain = "q_design", "ratio_design", f"q_peak_{list(scenarios)[0]}", None
    else:
        if T not in scenarios:
            return {"error": f"目标情景 {T} 不在 scenarios 里"}
        qcol, rcol, peak, rain = f"q_{T}", f"ratio_{T}", f"q_peak_{T}", scenarios[T]
    act = es[es["is_active"] & (es["diameter_flag"] == "ok")].copy()
    rate = c["pipe_aed_per_m_by_dn"]; keys = sorted(rate, key=float)
    def unit(d):
        for k in keys:
            if float(k) >= d:
                return float(rate[k])
        return float(rate[keys[-1]])
    over = act[act[rcol] > D["ratio_threshold"]].copy()
    need_d = [hy.required_diameter_mm(q, s, n, D["target_ratio"], D["standard_diameters_mm"]) for q, s, n in zip(over[qcol], over["slope"], over["n_manning"])]
    over["need_d"] = [d if d else 3000.0 for d in need_d]
    over["unfixable"] = [d is None for d in need_d]
    over["cost"] = over["len_m"] * over["need_d"].map(unit)
    proj = over.groupby("terminal").agg(zone_name=("zone_name", lambda x: x.mode().iat[0] if x.notna().any() else None), n_edges=("gid", "size"),
                                        km=("len_m", lambda x: x.sum() / 1000), cost_pipe=("cost", "sum"), n_unfixable=("unfixable", "sum"),
                                        inlets=("n_inlets_up", "max")).reset_index()
    # 同一汇水区的蓄渗缺口与泵
    csi = cs.set_index("terminal")
    proj["cost_storage"] = proj["terminal"].map(csi["storage_deficit_m3"]).fillna(0.0) * float(c["storage_aed_per_m3"])
    tide = (csi["tide_clearance_m"] < D["tide_clearance_min_m"]) & (csi["n_pumps"] <= 0)
    proj["cost_pump"] = proj["terminal"].map(tide).fillna(False).astype(bool) * (float(c["pump_fixed_aed"]) + proj["terminal"].map(csi[peak]).fillna(0.0) * float(c["pump_aed_per_m3s"]))
    proj["cost"] = proj["cost_pipe"] + proj["cost_storage"] + proj["cost_pump"]
    proj["terminal_kind"] = proj["terminal"].map(csi["terminal_kind"])
    proj["suspect"] = proj["terminal"].map(csi["suspect_missing_outlet"]).fillna(False).astype(bool)
    proj["benefit_km"] = proj["km"]
    # 排序按「保护的进水口数 / 百万 AED」：目标是保护人和路，不是修最多公里
    proj["benefit_per_maed"] = (proj["inlets"].fillna(0.0) + 1.0) / (proj["cost"] / 1e6).clip(lower=1e-3)
    proj = proj.sort_values("benefit_per_maed", ascending=False).reset_index(drop=True)
    # 分期：疑似漏登的汇水区先核实，不计入工程年度；其余按年度预算装箱
    budget = float(S["annual_budget_aed"])
    year, spent, years = 1, 0.0, []
    for _, r in proj.iterrows():
        if r["suspect"]:
            years.append(0); continue
        if spent + r["cost"] > budget and spent > 0:
            year += 1; spent = 0.0
        spent += r["cost"]; years.append(year)
    proj["year"] = years
    total = float(proj.loc[proj["year"] > 0, "cost"].sum())
    X = int(proj["year"].max()) if (proj["year"] > 0).any() else None   # 按实际装箱的年数，与分期曲线一致
    by_year = proj[proj["year"] > 0].groupby("year").agg(cost_aed=("cost", "sum"), km=("km", "sum"), n_projects=("terminal", "size")).reset_index()
    total_km = float(act["len_m"].sum()) / 1000
    remaining = total_km * 0 + float(over["len_m"].sum()) / 1000
    curve = []
    for _, y in by_year.iterrows():
        remaining -= float(y["km"])
        curve.append({"year": int(y["year"]), "cost_aed": round(float(y["cost_aed"]), 0), "km_fixed": round(float(y["km"]), 1), "n_projects": int(y["n_projects"]),
                      "remaining_surcharge_km": round(max(remaining, 0.0), 1), "remaining_share": round(max(remaining, 0.0) / total_km, 3)})
    return {"target": T, "rain_mmh": rain, "years": X, "total_cost_aed": round(total, 0), "annual_budget_aed": budget,
            "n_projects": int((proj["year"] > 0).sum()), "n_suspect_first": int(proj["suspect"].sum()), "suspect_km": round(float(proj.loc[proj["suspect"], "km"].sum()), 1),
            "unfixable_edges": int(over["unfixable"].sum()), "curve": curve, "projects": proj}


def residual_state(sur: "SurrogateEvaluator", sets: list[InterventionSet], selected_ids: list[str], cfg: dict, target: str) -> dict:
    """把入选方案全部套上去，看目标情景下还剩什么：仍超载的管段、仍高潮暴露的出水口汇水区、仍疑似漏登的终点。"""
    by_id = {s.set_id: s for s in sets}
    ivs = [iv for sid in selected_ids if sid in by_id for iv in by_id[sid].interventions]
    es, cs = sur.apply(ivs)
    D = cfg["decision"]
    over = es[es[f"ratio_{target}"] > D["ratio_threshold"]]
    tide = cs[(cs["terminal_kind"] == "出水口") & (cs["tide_clearance_m"] < D["tide_clearance_min_m"]) & (cs["n_pumps"] <= 0)]
    sus = cs[cs["suspect_missing_outlet"]]
    kpis = sur.kpis(es, cs, None)
    over, tide, sus = over.reset_index(drop=True), tide.reset_index(drop=True), sus.reset_index(drop=True)
    return {"kpis_after": kpis, "kpis_before": sur.base, "over_edges": over, "tide_catchments": tide, "suspect_catchments": sus,
            "summary": {"目标情景": target, "实施前超载km": round(float(sur.es[sur.es[f"ratio_{target}"] > D["ratio_threshold"]]["len_m"].sum()) / 1000, 1),
                        "实施后超载km": round(float(over["len_m"].sum()) / 1000, 1), "仍高潮暴露出水口": int(len(tide)), "仍疑似漏登终点": int(len(sus))}}


# ---------------------------------------------------------------- 组合与排期
def _benefit_score(delta: dict, weights: dict, scenario_weights: dict | None = None) -> float:
    """KPI 减少量按权重折成一个分（越大越好）。带 @情景 后缀的 KPI 再乘该情景的权重，这就是跨情景的稳健选择。"""
    score = 0.0
    sw = scenario_weights or {}
    for k, w in weights.items():
        for key, v in delta.items():
            if key == k:
                score += -float(v) * float(w)
            elif key.startswith(k + "@"):
                sc = key.split("@", 1)[1]
                score += -float(v) * float(w) * float(sw.get(sc, 1.0 if not sw else 0.0))
    return score


def build_sets(props: pd.DataFrame, cs: pd.DataFrame, cfg: dict, main: str, es: pd.DataFrame) -> list[InterventionSet]:
    """每条工程/调度建议一个单干预集合，另加一个『前 N 条打包』集合方便预测侧一次评估。"""
    sets = []
    for _, p in props.iterrows():
        if p["kind"] not in WORK_KINDS and p["kind"] not in ("事件前临时泵位",) and not str(p["kind"]).startswith("核实拓扑"):
            continue
        ivs = proposal_to_interventions(p.to_dict(), cs, cfg, main)
        if not ivs:
            continue
        cost = sum(cost_of(iv, cfg, es) for iv in ivs)
        sets.append(InterventionSet(set_id=f"p{int(p['pid'])}", interventions=ivs, label=str(p["title"])[:80], cost_aed=round(cost, 0), proposals=[int(p["pid"])]))
    return sets


def optimize(sets: list[InterventionSet], evals: dict[str, dict], budget_aed: float, cfg: dict) -> pd.DataFrame:
    """贪心：按 收益/成本 选到预算用完。返回排好序的方案表。"""
    w = cfg["whatif"]["kpi_weights"]
    sw = cfg.get("standard", {}).get("scenario_weights")
    rows = []
    for s in sets:
        ev = evals.get(s.set_id)
        if ev is None:
            continue
        b = _benefit_score(ev["delta"], w, sw)
        rows.append({"set_id": s.set_id, "label": s.label, "cost_aed": s.cost_aed or 0.0, "benefit": round(b, 3),
                     "benefit_per_maed": round(b / max((s.cost_aed or 1.0) / 1e6, 1e-6), 3), "source": ev.get("source"),
                     "delta": ev["delta"], "kpis": ev["kpis"], "proposals": s.proposals,
                     "interventions": [asdict(i) for i in s.interventions]})
    df = pd.DataFrame(rows)
    if not len(df):
        return df
    df = df[df["benefit"] > 0].sort_values("benefit_per_maed", ascending=False).reset_index(drop=True)
    spent, chosen = 0.0, []
    for i, r in df.iterrows():
        if spent + r["cost_aed"] <= budget_aed:
            chosen.append(True); spent += r["cost_aed"]
        else:
            chosen.append(False)
    df["selected"] = chosen
    df["cum_cost_aed"] = df["cost_aed"].where(df["selected"], 0).cumsum()
    return df


def schedule(plan: pd.DataFrame, props: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """排期：立即（核实）→ 雨季前 → 事件前 → 资本计划按收益/成本；同一支上的工程排在其核实项之后。"""
    order = {"立即": 0, "雨季前": 1, "事件前": 2, "并入在批": 3, "资本计划": 4}
    pm = props.set_index("pid")
    rows = []
    for _, r in plan[plan["selected"]].iterrows():
        pid = r["proposals"][0]
        p = pm.loc[pid]
        rows.append({"seq": None, "set_id": r["set_id"], "pid": pid, "kind": p["kind"], "kind_en": p.get("kind_en"), "when_class": p["when_class"], "when_class_en": p.get("when_class_en"),
                     "when": p["when"], "when_en": p.get("when_en"), "zone_name": p["zone_name"], "title": p["title"], "title_en": p.get("title_en"),
                     "cost_aed": r["cost_aed"], "benefit": r["benefit"], "benefit_per_maed": r["benefit_per_maed"], "eval_source": r["source"],
                     "depends_on": "先做同片区『核实终点/核实拓扑/数据治理』" if p["when_class"] in ("资本计划", "并入在批") else "",
                     "depends_on_en": "After the zone's verify/data-fix items" if p["when_class"] in ("资本计划", "并入在批") else ""})
    # 核实类不进 sets（无成本收益），直接从建议表拿来排在最前
    for pid, p in pm[pm["when_class"] == "立即"].iterrows():
        rows.append({"seq": None, "set_id": None, "pid": int(pid), "kind": p["kind"], "kind_en": p.get("kind_en"), "when_class": "立即", "when_class_en": "Immediately",
                     "when": p["when"], "when_en": p.get("when_en"), "zone_name": p["zone_name"], "title": p["title"], "title_en": p.get("title_en"),
                     "cost_aed": cfg["cost"]["survey_aed_per_item"], "benefit": None, "benefit_per_maed": None, "eval_source": "rule", "depends_on": "", "depends_on_en": ""})
    df = pd.DataFrame(rows)
    if not len(df):
        return df
    df["_o"] = df["when_class"].map(order).fillna(9)
    df = df.sort_values(["_o", "benefit_per_maed"], ascending=[True, False]).drop(columns="_o").reset_index(drop=True)
    df["seq"] = np.arange(1, len(df) + 1)
    return df


def run_whatif(es: pd.DataFrame, cs: pd.DataFrame, props: pd.DataFrame, cfg: dict, scenarios: dict[str, float], root: Path,
               network_version: str, baseline_prediction_run: str | None = None) -> dict:
    main = list(scenarios)[0]
    sets = build_sets(props, cs, cfg, main, es)
    sur = SurrogateEvaluator(es, cs, cfg, scenarios)
    evals = {s.set_id: sur.evaluate(s) for s in sets}
    ext = ExternalEvaluator(cfg, root)
    ext_results = ext.collect()
    n_ext = 0
    for s in sets:
        hits = {sc: v for (sid, sc), v in ext_results.items() if sid == s.set_id}
        if hits:
            # 有外部结果：以主情景（或任一）为准覆盖代理评估
            v = hits.get(main) or next(iter(hits.values()))
            evals[s.set_id] = {"kpis": v["kpis"], "delta": v["delta"], "source": "external", "run": v.get("run")}
            n_ext += 1
    plan = optimize(sets, evals, float(cfg["whatif"]["budget_aed"]), cfg)
    sched = schedule(plan, props, cfg)
    # 把前 N 个入选方案打包成一个请求交给预测侧（单个 + 打包）
    top = plan[plan["selected"]].head(int(cfg["whatif"]["request_top_n"])) if len(plan) else plan
    req = None
    if len(top):
        by_id = {s.set_id: s for s in sets}
        singles = [by_id[s] for s in top["set_id"]]
        bundle = InterventionSet(set_id="bundle-top%d" % len(singles), interventions=[iv for s in singles for iv in s.interventions],
                                 label=f"入选前 {len(singles)} 条打包", cost_aed=float(sum(s.cost_aed or 0 for s in singles)), proposals=[p for s in singles for p in s.proposals])
        scen_objs = [Scenario(name=k, rain_mmh=v, duration_min=cfg["model"]["duration_min"], tide_m=cfg["model"]["tide_high_m"]) for k, v in scenarios.items()]
        req = ext.submit(singles + [bundle], scen_objs, network_version, baseline_prediction_run)
    return {"sets": sets, "evals": evals, "plan": plan, "schedule": sched, "request": req, "n_external": n_ext, "surrogate_baseline": sur.base}
