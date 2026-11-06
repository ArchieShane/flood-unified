"""预警触发与自动提示（PoC 议程第 5 项）：一条预报（24 h 雨量，可分区）→ 对应情景 → 预警等级 → 给谁什么提示。

这是规则层，不接真实预报源，也不推送：把已经算好的情景结果（积水点分级、全网溢流量、预置方案、下穿隧道）按 [alerts] 里的阈值和模板
变成可读的提示单，写到 out/alerts/ 留痕。真接了 NCM 预报接口或短信/邮件网关，只是把 forecast 换成实时的、把 messages 发出去。
等级用的是 [dispatch.manual_levels] 的蓝/黄/橙/红（WA-726-3 表 11-1 的 例行/I/II/III）。
"""
from __future__ import annotations
import json
import time
from pathlib import Path

from . import scenarios

ROOT = Path(__file__).resolve().parents[1]
LEVELS = ["蓝", "黄", "橙", "红"]
LEVEL_EN = {"蓝": "Blue", "黄": "Yellow", "橙": "Orange", "红": "Red"}
DEFAULTS = {
    "rain_mm": {"黄": 45.0, "橙": 70.0, "红": 90.0},               # 24 h 雨量：约 5 年 / 25 年 / 100 年一遇
    "iii_points": {"黄": 75, "橙": 100, "红": 150},                 # 情景下 III 级积水点数（III 级主要由重要度定，随雨量变化不大，阈值放高，主要看下面两条）
    "spill_m3": {"黄": 1.5e6, "橙": 3.0e6, "红": 5.0e6},            # 全网溢流量：10 年约 225 万、10 年+CC 305 万、2024-04 645 万、100 年 733 万
    "roles": [
        {"role": "控制室值班", "role_en": "Control room duty officer", "contact": "（待填：值班电话 / 群）", "contact_en": "(to be filled: duty phone / group)", "contact_en": "(to be filled: duty phone / group)"},
        {"role": "预置与泵队调度", "role_en": "Staging & pump fleet dispatch", "contact": "（待填：泵队承包商 / 民防联络人）", "contact_en": "(to be filled: pump contractor / civil defence liaison)", "contact_en": "(to be filled: pump contractor / civil defence liaison)"},
        {"role": "交通与警方", "role_en": "Traffic & police", "contact": "（待填：ITC / 警方联络人）", "contact_en": "(to be filled: ITC / police liaison)", "contact_en": "(to be filled: ITC / police liaison)"},
        {"role": "运维巡查", "role_en": "O&M patrol", "contact": "（待填：各区运维承包商）", "contact_en": "(to be filled: O&M contractor per area)", "contact_en": "(to be filled: O&M contractor per area)"},
    ],
}


def params(cfg: dict) -> dict:
    A = dict(DEFAULTS); A.update({k: v for k, v in (cfg.get("alerts") or {}).items()})
    for k in ("rain_mm", "iii_points", "spill_m3"):
        A[k] = {**DEFAULTS[k], **{kk: float(vv) for kk, vv in ((cfg.get("alerts") or {}).get(k) or {}).items()}}
    return A


def pick_scenario(cfg: dict, p24_mm: float) -> dict:
    """预报 24 h 雨量 → 目录里 24 h 雨量最接近的情景（同样接近时优先重现期，再气候变化，再实测事件）。"""
    pref = {"rp": 0, "rp_cc": 1, "event": 2}
    cands = sorted(scenarios.catalog(cfg), key=lambda s: (abs(float(s["p24_mm"]) - float(p24_mm)), pref.get(s["kind"], 9)))
    s = cands[0]
    return {"key": s["key"], "kind": s["kind"], "p24_mm": float(s["p24_mm"]), "label": s["label"], "label_en": s["label_en"], "delta_mm": round(float(s["p24_mm"]) - float(p24_mm), 1)}


def level_from_rules(cfg: dict, p24_mm: float, iii_points: int | None, spill_m3: float | None) -> tuple[str, list[dict]]:
    """三条规则各给一个等级，取最高；返回 (等级, 触发明细)。"""
    A = params(cfg); trig = []
    def hit(rule, value, table):
        lv = "蓝"
        for L in ("黄", "橙", "红"):
            if value is not None and float(value) >= float(table[L]):
                lv = L
        trig.append({"rule": rule, "value": (round(float(value), 1) if value is not None else None), "thresholds": {k: table[k] for k in ("黄", "橙", "红")}, "level": lv})
        return lv
    lv = [hit("rain_24h_mm", p24_mm, A["rain_mm"])]
    if iii_points is not None:
        lv.append(hit("level_iii_points", iii_points, A["iii_points"]))
    if spill_m3 is not None:
        lv.append(hit("network_spill_m3", spill_m3, A["spill_m3"]))
    level = max(lv, key=LEVELS.index)
    return level, trig


def _fmt_m3(v):
    return f"{v/1e4:.1f} 万 m³" if v is not None else "—"


def advisory(cfg: dict, bundle: dict, forecast: dict, write: bool = True) -> dict:
    """bundle = {key, label, label_en, p24_mm, scenario(含 network_flood), dispatch(payload)}；forecast = {p24_mm, lead_h, issued_at, source, area}。"""
    A = params(cfg); D = cfg.get("dispatch") or {}; ML = D.get("manual_levels") or {}
    sc = bundle.get("scenario") or {}; dp = bundle.get("dispatch") or {}
    nf = sc.get("network_flood") or {}
    lv_pts = ((dp.get("levels") or {}).get("points") or {})
    iii = lv_pts.get("III"); spill = nf.get("total_m3")
    level, trig = level_from_rules(cfg, float(forecast["p24_mm"]), iii, spill)
    manual = ML.get(level) or {}
    feats = (dp.get("points") or {}).get("features") or []
    P = [f["properties"] for f in feats]
    tunnels = [p for p in P if p.get("land_use") == "tunnel"]
    crit = sorted([p for p in P if p.get("level") == "III"], key=lambda p: -(p.get("priority") or 0))[:8]
    pre = (dp.get("preposition") or {}).get(level) or {}
    sites = sorted(pre.get("sites") or [], key=lambda s: -len(s.get("covers") or []))
    val = pre.get("value") if isinstance(pre.get("value"), dict) and not pre.get("value", {}).get("error") else None
    zones = (nf.get("by_zone") or [])[:5]
    mob = float(ML.get("mobilization_min", 30)); trt = float(ML.get("trt_min", 120))
    t_dec = (dp.get("decision_time_min") or D.get("decision_time_min"))
    lead_h = float(forecast.get("lead_h", 24))
    # ---- 给谁什么 ----
    actions = []
    actions.append({"role": A["roles"][0]["role"], "role_en": A["roles"][0]["role_en"], "contact": A["roles"][0].get("contact"),
                    "zh": f"按 WA-726-3 表 11-1 宣布{level}级（{manual.get('level', '-')} 级应急）；预报 24 h {forecast['p24_mm']:.0f} mm，按情景「{bundle.get('label')}」推演：III 级积水点 {iii if iii is not None else '—'} 个，全网溢流 {_fmt_m3(spill)}。事件开始 {t_dec} 分钟为缺省决策时刻，届时用第三道页面重解调度。",
                    "en": f"Declare {LEVEL_EN[level]} (Level {manual.get('level', '-')}) per WA-726-3 Table 11-1; forecast {forecast['p24_mm']:.0f} mm / 24 h, scenario \"{bundle.get('label_en')}\": {iii if iii is not None else '—'} Level-III flood points, network spill {(spill or 0)/1e3:,.0f} k m³. Default decision time {t_dec} min after onset; re-solve the dispatch then."})
    if sites:
        top = "、".join(f"{s.get('name')}（{s.get('units_suggested')} 台）" for s in sites[:5])
        top_en = ", ".join(f"{s.get('name')} ({s.get('units_suggested')} pumps)" for s in sites[:5])
        saved = f"，比事后调度少 {val['saved_vs_after_pct']}% 积水时长" if val and val.get("saved_vs_after_pct") is not None else ""
        actions.append({"role": A["roles"][1]["role"], "role_en": A["roles"][1]["role_en"], "contact": A["roles"][1].get("contact"),
                        "zh": f"{level}级预置：{len(sites)} 处驻地共 {sum(int(s.get('units_suggested') or 0) for s in sites)} 台泵，事件开始后 {mob:.0f} 分钟内出发；前五处 {top}{saved}。",
                        "en": f"{LEVEL_EN[level]} staging: {sum(int(s.get('units_suggested') or 0) for s in sites)} pumps at {len(sites)} sites, depart within {mob:.0f} min of onset; top five {top_en}" + (f", {val['saved_vs_after_pct']}% less flooded time than post-event dispatch" if val and val.get('saved_vs_after_pct') is not None else "") + "."})
    if tunnels:
        tl = "、".join(f"{p.get('fp_id')}" + (f"（{str(p.get('location'))[:18]}）" if p.get("location") else "") for p in tunnels[:6])
        actions.append({"role": A["roles"][2]["role"], "role_en": A["roles"][2]["role_en"], "contact": A["roles"][2].get("contact"),
                        "zh": f"{len(tunnels)} 处下穿 / 隧道预计积水，准备封闭与绕行预案：{tl}{'…' if len(tunnels) > 6 else ''}。到场目标 {trt:.0f} 分钟。",
                        "en": f"{len(tunnels)} underpasses / tunnels expected to flood; prepare closure and detour plans: {', '.join(p.get('fp_id') for p in tunnels[:6])}{'…' if len(tunnels) > 6 else ''}. Target response time {trt:.0f} min."})
    if zones or crit:
        zl = "、".join(f"{z['zone_name']}（{_fmt_m3(z['V_m3'])}）" for z in zones)
        cl = "、".join(f"{p.get('fp_id')}" for p in crit[:6])
        actions.append({"role": A["roles"][3]["role"], "role_en": A["roles"][3]["role_en"], "contact": A["roles"][3].get("contact"),
                        "zh": f"巡查重点片区（按预测溢流量）：{zl or '—'}；重点盯防 III 级积水点：{cl or '—'}。雨前清理进水口与格栅。",
                        "en": f"Patrol priority zones (by predicted spill): {', '.join(z['zone_name'] for z in zones) or '—'}; watch Level-III points: {cl or '—'}. Clear inlets and grates before the rain."})
    timeline = [{"t": f"T−{lead_h:g}h", "zh": "发布预警，通知各角色", "en": "Issue the alert, notify all roles"},
                {"t": f"T−{max(lead_h - 6, 1):g}h", "zh": "预置泵队到驻地待命", "en": "Pre-position pumps at staging sites"},
                {"t": f"T+{mob:.0f}min", "zh": "预置泵出发", "en": "Pre-positioned pumps depart"},
                {"t": f"T+{trt:.0f}min", "zh": "到场目标（手册 TRT）", "en": "Target response time (manual TRT)"},
                {"t": f"T+{t_dec}min", "zh": "决策时刻：重解调度、复核封路", "en": "Decision time: re-solve dispatch, review closures"}]
    out = {"schema": "swbrain.alert_advisory/1", "issued_at": forecast.get("issued_at") or time.strftime("%Y-%m-%d %H:%M"), "forecast": forecast,
           "scenario": {"key": bundle.get("key"), "label": bundle.get("label"), "label_en": bundle.get("label_en"), "p24_mm": bundle.get("p24_mm"), "delta_mm": bundle.get("delta_mm")},
           "level": level, "level_en": LEVEL_EN[level], "manual": {"level": manual.get("level"), "priority": manual.get("priority"), "zh": manual.get("zh"), "en": manual.get("en")},
           "triggers": trig,
           "impact": {"points": (dp.get("kpis") or {}).get("solver", {}).get("points_total") or len(P), "level_iii_points": iii, "level_ii_points": lv_pts.get("II"), "tunnels": len(tunnels),
                      "network_spill_m3": spill, "spilling_len_share": nf.get("spilling_len_share"), "top_zones": zones,
                      "preposition": {"sites": len(sites), "pumps": sum(int(s.get("units_suggested") or 0) for s in sites), "saved_vs_after_pct": (val or {}).get("saved_vs_after_pct")}},
           "actions": actions, "timeline": timeline,
           "headline": f"{level}级预警 · 预报 24 h {forecast['p24_mm']:.0f} mm · 预计 III 级积水点 {iii if iii is not None else '—'} 个 · 全网溢流 {_fmt_m3(spill)}",
           "headline_en": f"{LEVEL_EN[level]} alert · forecast {forecast['p24_mm']:.0f} mm/24 h · {iii if iii is not None else '—'} Level-III flood points · network spill {(spill or 0)/1e3:,.0f} k m³",
           "note": "规则层演示：预报为手动输入或情景代入，未接 NCM 预报源与推送渠道；阈值与角色在 config [alerts]",
           "note_en": "Rule-layer demo: forecast entered manually or taken from a scenario; no NCM feed or messaging gateway connected; thresholds and roles in config [alerts]"}
    if write:
        d = ROOT / "out" / "alerts"; d.mkdir(parents=True, exist_ok=True)
        (d / f"advisory_{time.strftime('%Y%m%dT%H%M%S')}_{level}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
