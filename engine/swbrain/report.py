"""一键报告（PoC 议程第 6 项）：把某个情景下五页控制台的结论渲染成一份自包含的 HTML（无外链、可直接存 PDF）。

输入全是已有对象：summary.json、情景筛查（含全网溢流）、调度结果（含重演、预置、预案并排、成本）、预警提示单、DMT 对表（有则带）。
不重新计算，只排版；所有数字都带来源模块名，便于对账。
"""
from __future__ import annotations
import html
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CSS = """
body{font-family:system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:#1b2430;margin:0;background:#fff}
.page{max-width:960px;margin:0 auto;padding:28px 32px}
h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:26px 0 8px;padding-bottom:4px;border-bottom:2px solid #0b5fd7} h3{font-size:13.5px;margin:14px 0 6px;color:#3c4e5b}
.m{color:#66717f;font-size:12px} .kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:8px 0}
.kpi{background:#f4f6f8;border-radius:10px;padding:10px 12px} .kpi b{display:block;font-size:20px;line-height:1.2} .kpi span{color:#66717f;font-size:12px;display:block} .kpi small{display:block;color:#66717f;font-size:11px;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin:6px 0} td,th{padding:5px 6px;border-bottom:1px solid #e3e7ec;text-align:left;vertical-align:top} th{color:#66717f;font-weight:500;font-size:12px}
.tag{display:inline-block;border-radius:5px;padding:0 7px;font-size:11px;color:#fff;line-height:18px} .box{background:#e6effc;border-left:4px solid #0b5fd7;padding:10px 12px;border-radius:8px;margin:10px 0}
.foot{margin-top:28px;padding-top:10px;border-top:1px solid #e3e7ec;color:#66717f;font-size:11px}
@media print{.page{padding:0} h2{page-break-after:avoid} table{page-break-inside:avoid}}
"""
LV_COLOR = {"蓝": "#2563eb", "黄": "#ca8a04", "橙": "#ea580c", "红": "#b91c1c"}


LV_EN = {"蓝": "Blue", "黄": "Yellow", "橙": "Orange", "红": "Red"}
ASSUMPTION_EN = {                                                   # 批处理汇总里的中文键 → 英文（报告的「主要假设」表）
    "设计雨强mm/h": "Design rainfall intensity (mm/h)", "径流系数": "Runoff coefficient", "汇水面积来源": "Catchment area source",
    "子汇水区": "Sub-catchments", "每进水口汇水m2": "Catchment per inlet (m²)", "道路汇水宽m": "Road catchment width (m)",
    "标高基准": "Level datum", "地形": "Terrain", "高潮位m": "Design high tide (m)", "设计雨深mm": "Design rainfall depth (mm)", "IDF来源": "IDF source",
}


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _n(x, d=0) -> str:
    try:
        if x is None:
            return "—"
        return f"{float(x):,.{d}f}"
    except (TypeError, ValueError):
        return _e(x)


def _pct(x, d=0) -> str:
    return "—" if x is None else f"{float(x) * 100:.{d}f}%"


def _vol(v, en: bool) -> str:
    if v is None:
        return "—"
    v = float(v)
    return (f"{v/1e6:.2f} M m³" if v >= 1e6 else f"{v/1e3:,.0f} k m³") if en else (f"{v/1e8:.2f} 亿 m³" if v >= 1e8 else f"{v/1e4:.1f} 万 m³")


def render_html(cfg: dict, S: dict, bundle: dict, advisory: dict | None = None, dmt_compare: dict | None = None, lang: str = "zh", access_note: str | None = None) -> str:
    en = lang == "en"; T = (lambda zh, e_: e_ if en else zh)
    sc = bundle.get("scenario") or {}; dp = bundle.get("dispatch") or {}; nf = sc.get("network_flood") or {}
    CC = S.get("法定标准") or {}; L3 = S.get("三道防线") or {}; l3 = L3.get("第三道 应急调度") or {}
    SPC = S.get("法定标准达标的分期") or {}; SP = S.get("按标准达标的分期") or {}
    ks = (dp.get("kpis") or {}).get("solver") or {}; rs = dp.get("replay_summary") or {}; lv = (dp.get("levels") or {}).get("points") or {}
    pre = dp.get("preposition") or {}; plans = dp.get("plans") or {}; cost = dp.get("cost") or {}
    label = bundle.get("label_en" if en else "label") or bundle.get("key")
    now = time.strftime("%Y-%m-%d %H:%M")
    P = []
    P.append(f"<h1>{T('会思考的排水系统 · 阿布扎比', 'The Thinking Drainage System · Abu Dhabi')} <span class='m'>{T('情景报告', 'Scenario report')}</span></h1>")
    P.append(f"<div class='m'>{T('情景', 'Scenario')}：<b>{_e(label)}</b> · {T('管网 15 分钟雨强', 'network 15-min intensity')} {_e(sc.get('i_mmh'))} mm/h"
             f"{(' (' + _n(sc['i_range_mmh'][0], 1) + '–' + _n(sc['i_range_mmh'][1], 1) + ')') if sc.get('i_range_mmh') else ''} · {T('生成', 'generated')} {now} · run {_e(S.get('生成时间'))} · {_n(S.get('管段数'))} {T('段', 'reaches')}</div>")
    P.append(f"<div class='box'><b>{_e(T((S.get('承诺') or {}).get('承诺'), (S.get('承诺') or {}).get('commitment_en')))}</b><div class='m'>{_e(T((S.get('承诺') or {}).get('就绪条件'), (S.get('承诺') or {}).get('readiness_en')))} · {_e(T((S.get('承诺') or {}).get('说明'), (S.get('承诺') or {}).get('note_en')))}</div></div>")
    # ---- 1 现状 ----
    P.append(f"<h2>1. {T('现状', 'Current state')} <span class='m'>{T('真实台账当场算', 'computed live from the real register')}</span></h2><div class='kpis'>")
    P.append(f"<div class='kpi'><b>{_n(S.get('在用管长km'))} km</b><span>{T('在用管长', 'Active pipe length')}</span><small>{_pct(S.get('能到出水口或泵站的管长份额'))} {T('能追溯到出水口/泵站', 'traceable to an outfall or pump')}</small></div>")
    P.append(f"<div class='kpi'><b>{_pct(CC.get('达标管长份额'))}</b><span>{T('法定标准达标管长', 'Code-compliant length')}</span><small>{T('不达标', 'non-compliant')} {_n(CC.get('不达标km'))} km · {_e(CC.get('依据'))}</small></div>")
    P.append(f"<div class='kpi'><b>{_pct(sc.get('surcharge_share'))}</b><span>{T('本情景承载不足管长', 'Over-capacity length, this scenario')}</span><small>{_n(sc.get('surcharge_km'))} km · {_n(sc.get('n_edges_over'))} {T('段', 'reaches')}</small></div>")
    P.append(f"<div class='kpi'><b>{_vol(nf.get('total_m3'), en)}</b><span>{T('全网溢流量（本情景）', 'Network-wide spill, this scenario')}</span><small>{_n(nf.get('edges_spilling'))} {T('段溢流 · 占管长', 'reaches spilling · ')} {_pct(nf.get('spilling_len_share'), 1)}{(' · ' + T('顶托风险', 'backwater risk') + ' ' + _n((nf.get('backwater') or {}).get('edges')) + ' ' + T('段', 'reaches')) if nf.get('backwater') else ''}</small></div>")
    P.append(f"<div class='kpi'><b>{_n(S.get('高潮易涝出水口数(净空<阈值)'))} / {_n(S.get('出水口总数(图上)'))}</b><span>{T('高潮易涝出水口', 'Tide-exposed outfalls')}</span></div>")
    P.append(f"<div class='kpi'><b>{_n(S.get('疑似漏登出水口/泵站的断头数'))}</b><span>{T('疑似漏登终点', 'Suspected unregistered terminals')}</span><small>{T('承接', 'carry')} {_n(S.get('疑似漏登断头承接的管长km'))} km</small></div></div>")
    if nf.get("by_zone"):
        P.append(f"<h3>{T('溢流量最大的片区', 'Zones with the largest spill')}</h3><table><tr><th>{T('片区', 'Zone')}</th><th>{T('溢流量', 'Spill')}</th><th>{T('溢流管段', 'Spilling reaches')}</th><th>{T('顶托风险', 'Backwater risk')}</th><th>km</th></tr>")
        for z in nf["by_zone"][:8]:
            P.append(f"<tr><td>{_e(z.get('zone_name'))}</td><td>{_vol(z.get('V_m3'), en)}</td><td>{_n(z.get('n_spill'))}</td><td>{_n(z.get('n_backwater'))}</td><td>{_n(z.get('len_km'), 1)}</td></tr>")
        P.append("</table>")
    # ---- 2 第一道 ----
    P.append(f"<h2>2. {T('第一道防线：基础设施', 'Line 1: infrastructure')}</h2><div class='kpis'>")
    P.append(f"<div class='kpi'><b>{_n(S.get('建议数'))}</b><span>{T('建议', 'Proposals')}</span><small>{_e(json.dumps(S.get('建议按类型') or {}, ensure_ascii=False))[:120]}</small></div>")
    P.append(f"<div class='kpi'><b>{_n(SPC.get('n_projects'))}</b><span>{T('法定标准方案项数', 'Code-standard programme works')}</span><small>{_n((SPC.get('total_cost_aed') or 0)/1e8, 0)} {T('亿 AED', 'x10⁸ AED')} · {_n(SPC.get('years'))} {T('年', 'years')}</small></div>")
    P.append(f"<div class='kpi'><b>{_n(SP.get('n_projects'))}</b><span>{T('百年一遇方案项数', '100-yr programme works')}</span><small>{_n((SP.get('total_cost_aed') or 0)/1e8, 0)} {T('亿 AED', 'x10⁸ AED')} · {_n(SP.get('years'))} {T('年', 'years')}</small></div></div>")
    if CC.get("按类别"):
        P.append(f"<h3>{T('分设施达标（WA-726-1 表 3-2）', 'Compliance by facility class (WA-726-1 Table 3-2)')}</h3><table><tr><th>{T('类别', 'Class')}</th><th>{T('重现期', 'RP')}</th><th>km</th><th>{T('达标份额', 'Compliant share')}</th><th>{T('不达标 km', 'Non-compliant km')}</th></tr>")
        for c in CC["按类别"]:
            P.append(f"<tr><td>{_e(c.get('class_en' if en else 'class_zh'))}</td><td>{_e(c.get('rp'))}</td><td>{_n(c.get('km'))}</td><td>{_pct(c.get('compliant_share'))}</td><td>{_n(c.get('over_km'))}</td></tr>")
        P.append("</table>")
    # ---- 3 第二道 ----
    P.append(f"<h2>3. {T('第二道防线：预报后预置', 'Line 2: pre-positioning on forecast')}</h2>")
    if pre:
        P.append(f"<table><tr><th>{T('预警', 'Alert')}</th><th>{T('驻地', 'Sites')}</th><th>{T('泵', 'Pumps')}</th><th>{T('到位中位 min', 'Median arrival min')}</th><th>{T('全网积水时长', 'Flooded time')}</th><th>{T('比事后', 'vs post-event')}</th><th>{T('按目标退干', 'On target')}</th><th>{T('III 级处置', 'Level III served')}</th><th>{T('指示成本', 'Indicative cost')}</th></tr>")
        for a, p in pre.items():
            v = p.get("value") if isinstance(p.get("value"), dict) and not p.get("value", {}).get("error") else None
            P.append(f"<tr><td><span class='tag' style='background:{LV_COLOR.get(a, '#333')}'>{_e(LV_EN.get(a, a) if en else a)}</span></td><td>{_n(len(p.get('sites') or []))}</td><td>{_n(v['units_placed']) if v else '—'}</td><td>{_n(v['arrive_median_min']) if v else '—'}</td>"
                     f"<td>{(_n(v['flood_h']['pre']) + ' h') if v else '—'}</td><td>{('−' + _n(v['saved_vs_after_pct'], 1) + '%') if v and v.get('saved_vs_after_pct') is not None else '—'}</td><td>{(str(v['within_target']['pre']) + '/' + str(v['within_target']['pre_n'])) if v else '—'}</td><td>{_n(v['level_iii_served']['pre']) if v else '—'}</td><td>{(_n((v.get('cost') or {}).get('total_aed')) + ' AED') if v and v.get('cost') else '—'}</td></tr>")
        P.append("</table>")
    if plans:
        red = (pre.get("红") or {}).get("value") or {}
        P.append(f"<h3>{T('预案并排（同一场雨、同一重演引擎）', 'Plans side by side (same storm, same replay engine)')}</h3><table><tr><th>{T('预案', 'Plan')}</th><th>{T('泵', 'Pumps')}</th><th>{T('到位 min', 'Arrival min')}</th><th>{T('全网比事后', 'Network vs post-event')}</th><th>{T('覆盖点内比事后', 'Within covered points')}</th><th>{T('按目标退干', 'On target')}</th><th>{T('指示成本 AED', 'Indicative cost AED')}</th></tr>")
        def row(lab, w, tot=None):
            if not w or w.get("error"):
                return f"<tr><td>{_e(lab)}</td><td colspan='6' class='m'>{_e((w or {}).get('error') or '—')}</td></tr>"
            sf = w.get("served_flood_h") or {}
            return (f"<tr><td>{_e(lab)}</td><td>{_n(w.get('units_placed'))}{(' / ' + _n(tot)) if tot and tot != w.get('units_placed') else ''}</td><td>{_n(w.get('arrive_median_min'))}</td><td>{('−' + _n(w.get('saved_vs_after_pct'), 1) + '%') if w.get('saved_vs_after_pct') is not None else '—'}</td>"
                    f"<td>{('−' + _n(sf.get('saved_pct'), 1) + '% (' + _n(sf.get('n')) + ')') if sf.get('saved_pct') is not None else '—'}</td><td>{_e((w.get('within_target') or {}).get('pre'))}/{_e((w.get('within_target') or {}).get('pre_n'))}</td><td>{_n((w.get('cost') or {}).get('total_aed'))}</td></tr>")
        P.append(row(T("本系统预置（红级）", "This system, Red level"), red if red and not red.get("error") else None))
        for k, p in plans.items():
            P.append(row(p.get("label_en" if en else "label") or k, p.get("value"), p.get("pumps_total")))
        P.append("</table>")
        if cost:
            P.append(f"<div class='m'>{T('事后调度指示成本', 'Post-event dispatch indicative cost')} {_n(cost.get('total_aed'))} AED（{_n(cost.get('pump_hours'))} {T('台时', 'pump-hours')}）· {T('单价按客户预案 A 表小时价，动员费与最短计费为假设', 'hourly rates from the client Plan A table; mobilisation and minimum charge are assumptions')}</div>")
    # ---- 4 第三道 ----
    P.append(f"<h2>4. {T('第三道防线：应急调度', 'Line 3: emergency response')} <span class='m'>{T('决策时刻', 'decision time')} {_e(dp.get('decision_time_min'))} min</span></h2><div class='kpis'>")
    P.append(f"<div class='kpi'><b>{_n(ks.get('points_served'))} / {_n(ks.get('points_total'))}</b><span>{T('处置点数', 'Points served')}</span><small>{T('用泵', 'pumps used')} {_n(ks.get('units_used'))} · {T('首车到达中位', 'median first arrival')} {_n(ks.get('first_arrival_median_min'))} min</small></div>")
    P.append(f"<div class='kpi'><b>{_n(ks.get('critical_within_target'))} / {_n(ks.get('critical_total'))}</b><span>{T('关键点按时', 'Critical points on time')}</span></div>")
    P.append(f"<div class='kpi'><b>−{_n(rs.get('served_saved_pct'), 1)}%</b><span>{T('处置点积水时长', 'Flooded time at served points')}</span><small>{_n(rs.get('served_flood_h_base'))} → {_n(rs.get('served_flood_h_pump'))} h</small></div></div>")
    P.append(f"<div class='m'>{T('全部', 'All')} {_n(rs.get('all_points'))} {T('个积水点合计', 'flood points')} {_n(rs.get('all_flood_h_base'))} → {_n(rs.get('all_flood_h_pump'))} h；{T('系统排空', 'system emptying')} {_n((rs.get('system_empty_base_min') or 0)/60, 1)} → {_n((rs.get('system_empty_pump_min') or 0)/60, 1)} h；{T('48 h 未退干', 'not drained within 48 h')} {_n(rs.get('never_empties_base'))} → {_n(rs.get('never_empties_pump'))}；"
             f"{T('事件分级', 'Event levels')} III {_n(lv.get('III'))} · II {_n(lv.get('II'))} · I {_n(lv.get('I'))}</div>")
    tri = dp.get("triage") or {}; tri_en = dp.get("triage_en") or {}
    if tri:
        P.append(f"<h3>{T('分诊', 'Triage')}</h3><table>" + "".join(f"<tr><td>{_e(tri_en.get(k, k) if en else k)}</td><td>{_n(v)}</td></tr>" for k, v in sorted(tri.items(), key=lambda kv: -kv[1])) + "</table>")
    plan = dp.get("plan") or []
    if plan:
        P.append(f"<h3>{T('调度单（前 15 条）', 'Dispatch order (first 15)')}</h3><table><tr><th>{T('波', 'Wave')}</th><th>{T('泵', 'Unit')}</th><th>{T('去', 'To')}</th><th>{T('到达 min', 'Arrive min')}</th><th>{T('排干 min', 'Drained min')}</th><th>{T('目标内', 'On target')}</th></tr>")
        for r in sorted(plan, key=lambda x: (x.get("wave", 1), -(x.get("priority") or 0)))[:15]:
            P.append(f"<tr><td>{_e(r.get('wave'))}</td><td>{_e(r.get('unit_id'))} <span class='m'>{_e(str(r.get('model', '')).replace('diesel-', ''))}</span></td><td>{_e(r.get('fp_id'))} <span class='m'>{_e(r.get('top_kind'))}</span></td><td>{_e(r.get('arrive_min'))}</td><td>{_e(r.get('done_min') or '—')}</td><td>{'✓' if r.get('within_target') else '✗'}</td></tr>")
        P.append("</table>")
    # ---- 5 预警 ----
    if advisory and not advisory.get("error"):
        a = advisory
        P.append(f"<h2>5. {T('预警触发与提示（规则层演示）', 'Early-warning trigger and prompts (rule-layer demo)')}</h2>")
        P.append(f"<div><span class='tag' style='background:{LV_COLOR.get(a['level'], '#333')};font-size:13px;line-height:24px;padding:0 10px'>{_e(a['level_en'] if en else a['level'])} · {_e(('Level ' + str(a['manual'].get('level'))) if en else (str(a['manual'].get('level')) + ' 级'))}</span> <b>{_e(a['headline_en'] if en else a['headline'])}</b></div>")
        P.append(f"<table><tr><th>{T('角色', 'Role')}</th><th>{T('提示', 'Prompt')}</th></tr>" + "".join(f"<tr><td><b>{_e(x['role_en'] if en else x['role'])}</b><div class='m'>{_e(x.get('contact_en') if en else x.get('contact'))}</div></td><td>{_e(x['en'] if en else x['zh'])}</td></tr>" for x in a.get("actions", [])) + "</table>")
        P.append(f"<div class='m'>{' → '.join(_e(x['t']) + ' ' + _e(x['en'] if en else x['zh']) for x in a.get('timeline', []))}</div><div class='m'>{_e(a.get('note_en' if en else 'note'))}</div>")
    # ---- 6 可信度 ----
    P.append(f"<h2>6. {T('数据与可信度', 'Data and credibility')}</h2>")
    P.append(f"<div class='m'>{T('可信：排序与位置来自真实几何与标高。量级：公里数、成本、年限算法可信、参数为占位。不能给：淹没深度、历时、受灾建筑数，要预测侧的水动力模型。', 'Trust: ranking and location come from real geometry and levels. Order of magnitude: kilometres, costs and years have sound algorithms but placeholder parameters. Not provided: inundation depth, duration and affected buildings, which need the hydrodynamic model.')}</div>")
    A = S.get("假设") or {}
    if A:
        P.append(f"<h3>{T('主要假设', 'Key assumptions')}</h3><table>" + "".join(f"<tr><td style='width:30%'>{_e(ASSUMPTION_EN.get(k, k) if en else k)}</td><td class='m'>{_e(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)[:260]}</td></tr>" for k, v in A.items()) + "</table>")
    if dmt_compare:
        P.append(f"<h3>{T('DMT 1D 模型对表（三个模型导出）', 'DMT 1D model check (three model exports)')}</h3><table><tr><th>{T('模型', 'Model')}</th><th>{T('边界内热点', 'Hotspots inside')}</th><th>{T('本系统全网溢流 10 年+CC / 2024-04', 'Our network spill 10-yr+CC / Apr 2024')}</th><th>{T('Parsons 溢流量', 'Parsons flood volume')}</th><th>{T('台账 / 模型管长 km', 'Register / model km')}</th></tr>")
        for name, C in dmt_compare.items():
            of = C.get("our_flood_inside_m3") or {}; pf = C.get("parsons_flood_m3") or {}; on = C.get("our_network_inside") or {}
            P.append(f"<tr><td><b>{_e(name)}</b></td><td>{_n((C.get('hotspots_inside') or {}).get('n'))}</td><td>{_vol(of.get('10cc'), en)} / {_vol(of.get('2024-04'), en)}</td><td>{_vol(pf.get('10cc'), en)} / {_vol(pf.get('2024-04'), en)}</td><td>{_n(on.get('length_km'))} / {_n(C.get('dmt_conduit_km'))}</td></tr>")
        P.append("</table>")
    P.append(f"<div class='foot'>{T('方法', 'Methods')}：{_e(sc.get('method_en' if en else 'method'))}；{_e(nf.get('method_en' if en else 'method'))}。{T('本报告由系统按当前状态排版生成，不重新计算；每个数字都可在控制台对应页面复核。', 'Rendered from the current system state without recomputation; every figure can be checked on the corresponding console page.')} {_e(access_note or '')}</div>")
    title = f"{T('情景报告', 'Scenario report')} · {label} · {now}"
    return f"<!doctype html><html lang='{'en' if en else 'zh-CN'}'><head><meta charset='utf-8'><title>{_e(title)}</title><style>{CSS}</style></head><body><div class='page'>{''.join(P)}</div></body></html>"
