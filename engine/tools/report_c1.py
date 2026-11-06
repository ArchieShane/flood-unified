# -*- coding: utf-8 -*-
"""C1 热点群一二维的在线报告（L1 / L2 / L2+planned / L2+suggested × 61 / 96 mm）→ out/c1_report.html，发布到
https://claude.ai/artifact/NZKLeppNDkvRx593PSjrYJ 。样式 tools/report_style.html（与 Mussafah / 全市 / 29.RB2 报告同一套）。

为什么放在仓库里（2026-09-29）：原来的生成脚本在会话的临时目录，08:36 被整个清空；这一份按已发布的页面与会话记录重建。
数据：out/c1_stats_R{61,96}.json（c1_post.py）、out/c1_L*_inputs_R*.json（各态的建模输入 / 措施）、out/c1_params_answers.json（参数对齐页快照）、
      out/c1_L2S1_R61_summary.json（弃用的 v1 土堤版）、$WORK/c2d/*_2way.rpt 与 twoway_*.json（泵抽水量、塘满度）；
      截图 out/c1_shots/{TG(r)}_{态}.jpg（有就放）。只写已算完的态，没算完的显示「计算中」。
用法：python tools/report_c1.py
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
C2D = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm")) / "c2d"
ST = ["L1", "L2", "L2P", "L2S", "L2S3"]
LAB = {"L1": "L1", "L2": "L2", "L2P": "L2+planned", "L2S": "L2+suggested", "L2S3": "L2+suggested v3"}
COL = {"L1": "var(--l1)", "L2": "var(--l2)", "L2P": "var(--pa)", "L2S": "var(--pab)", "L2S3": "var(--pc)"}
PARENT = {"L2": "L1", "L2P": "L2", "L2S": "L2P", "L2S3": "L2P"}      # v3 和 v2 都是 L2+planned 上加措施，都跟 L2+planned 比


def n(v, d=0):
    return "—" if v is None else f"{v:,.{d}f}"


def TG(r):
    """报告里的 r：61 / 96（原计算域）或 "M61"（模型版）→ 情景名。"""
    return r if isinstance(r, str) else f"R{r}"


def LBL(r):
    return {"M61": "模型版 61 mm / 2 h", "M96H1": "模型版 96 mm / 1 h"}.get(r, f"{r} mm")


def jload(p):
    p = pathlib.Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


R = {r: jload(ROOT / "out" / f"c1_stats_{TG(r)}.json") for r in (61, 96)}
R = {k: v for k, v in R.items() if v}
P = jload(ROOT / "out" / "c1_params_answers.json") or {}
L1i = {r: jload(ROOT / "out" / f"c1_L1_inputs_{TG(r)}.json") for r in (61, 96)}
L2i = {r: jload(ROOT / "out" / f"c1_L2_inputs_{TG(r)}.json") for r in (61, 96)}
L2Pi = {r: jload(ROOT / "out" / f"c1_L2P_inputs_{TG(r)}.json") for r in (61, 96)}
L2Si = {r: jload(ROOT / "out" / f"c1_L2S_inputs_{TG(r)}.json") for r in (61, 96)}
L2S3i = {r: jload(ROOT / "out" / f"c1_L2S3_inputs_{TG(r)}.json") for r in (61, 96)}
SI = {"L2S": L2Si, "L2S3": L2S3i}
for _d, _k in ((L1i, "L1"), (L2i, "L2"), (L2Pi, "L2P"), (L2Si, "L2S"), (L2S3i, "L2S3")):      # 模型版的建模输入（设施清单、容量核算用）
    _d["M61"] = jload(ROOT / "out" / f"c1_{_k}_inputs_M61.json")
IMG = {}
for r in (61, 96):
    for st in ST:
        f = ROOT / "out" / "c1_shots" / f"{TG(r)}_{st}.jpg"
        if f.exists():
            IMG[f"{r}_{st}"] = "data:image/jpeg;base64," + base64.b64encode(f.read_bytes()).decode()


def z(r, zone, st, k):
    try:
        return R[r]["zones"][zone][st][k]
    except KeyError:
        return None


def pct(a, b):
    return None if a in (None, 0) or b is None else (b - a) / a * 100


def cell(r, zone, st, k, d):
    x = z(r, zone, st, k)
    if x is None:
        return "<td class='n' style='color:var(--muted)'>计算中</td>"
    sub = ""
    if st in PARENT:
        p = pct(z(r, zone, PARENT[st], k), x)
        if p is not None:
            sub = (f'<span class="u" style="color:{"var(--good)" if p < 0 else "var(--warn)"}">{p:+.0f}%</span>' if abs(p) >= 0.5 else '<span class="u">=</span>')
    return f"<td class='n'>{n(x, d)}{sub}</td>"


ROWS = [("八个热点汇水区 · 积水 ≥0.30 m 面积", "C1 八个热点|catch", "area_ge_0p30_ha", "ha", 1),
        ("八个热点汇水区 · 积水 ≥0.10 m 面积", "C1 八个热点|catch", "area_ge_0p10_ha", "ha", 1),
        ("八个热点汇水区 · 最大积水量", "C1 八个热点|catch", "peak_vol_m3", "m³", 0),
        ("热点 200 m 内 · 最大水深", "C1 八个热点|200m", "max_depth_m", "m", 2),
        ("热点 200 m 内 · 积水 ≥0.10 m 面积", "C1 八个热点|200m", "area_ge_0p10_ha", "ha", 2),
        ("全域管网服务区 · 积水 ≥0.30 m 面积", "全域|served", "area_ge_0p30_ha", "ha", 1)]


def main_table(r):
    head = "".join(f"<th style='color:{COL[s]}'>{LAB[s]}</th>" for s in ST)
    rows = "".join(f"<tr><td>{zh}<span class='u' style='color:var(--muted)'>{u}</span></td>{''.join(cell(r, zn, s, k, d) for s in ST)}</tr>"
                   for zh, zn, k, u, d in ROWS)
    return f"<div class='scroll'><table><tr><th>指标（{r} mm / 2 h）</th>{head}</tr>{rows}</table></div>"


def hs_table(r):
    if r not in R:
        return ""
    ids = [k.split("|")[0] for k in R[r]["zones"] if k.startswith("ADM-") and k.endswith("|catch")]
    have = [s for s in ST if s in R[r]["states"]]
    th = "".join(f"<th style='color:{COL[s]}'>{LAB[s]}</th>" for s in have)
    rows = []
    for h in ids:
        tds = "".join(f"<td class='n'>{n(z(r, h + '|catch', s, 'area_ge_0p30_ha'), 1)} <span class='u'>ha</span> · {n(z(r, h + '|200m', s, 'max_depth_m'), 2)} <span class='u'>m</span></td>" for s in have)
        rows.append(f"<tr><td><b>{h}</b></td>{tds}</tr>")
    return f"<div class='scroll'><table><tr><th>热点（{r} mm）· 汇水区 ≥0.30 m 面积 · 200 m 内最深</th>{th}</tr>{''.join(rows)}</table></div>"


def pumped(r, st):
    """SWMM Pumping Summary：按泵组合计 6 h 抽水量（m³）与平均利用率。"""
    f = C2D / f"C1_{TG(r)}_{st}_2way.rpt"
    if not f.exists():
        return None
    t = f.read_text(errors="ignore")
    m = re.search(r"Pumping Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    g = {}
    for ln in (m.group(1).splitlines() if m else []):
        q = ln.split()
        if len(q) < 7:
            continue
        k = ("现状泵站" if q[0].startswith("PS_") else "L2 移动泵" if q[0].startswith("L2_") else "Parsons 泵站" if q[0].startswith("L2P_")
             else "L2+suggested 转输泵" if q[0].startswith("S") else "其他")
        e = g.setdefault(k, {"n": 0, "m3": 0.0, "util": 0.0}); e["n"] += 1; e["m3"] += float(q[6]) * 1000; e["util"] += float(q[1])
    for e in g.values():
        e["util"] /= max(e["n"], 1)
    return g


def sump_out(r, st):
    tw = jload(C2D / f"twoway_C1_{TG(r)}_{st}.json")
    return None if not tw else sum(p.get("pumped_m3") or 0 for p in tw.get("pumps2d", []))


def findings():
    li = []
    for r in (61, 96):
        if r not in R:
            continue
        have = R[r]["states"]
        zc = lambda st: z(r, "C1 八个热点|catch", st, "area_ge_0p30_ha")
        zd = lambda st: z(r, "C1 八个热点|200m", st, "max_depth_m")
        ov = R[r]["balance"]["L1"]["1D→2D"]
        parts = [f"<b>{r} mm</b>：L1 下计算域内管网冒到地面 {n(ov)} m³，八个热点汇水区 ≥0.30 m 积水 {n(zc('L1'), 1)} ha、热点 200 m 内最深 {n(zd('L1'), 2)} m"]
        if "L2" in have:
            mv = (pumped(r, "L2") or {}).get("L2 移动泵", {}).get("m3")
            parts.append(f"L2 的 23 台移动泵 6 h 只抽了 {n(mv)} m³，而且是在同一张已经满了的管网里挪水，面积 {pct(zc('L1'), zc('L2')):+.0f}%")
        if "L2P" in have:
            pv = (pumped(r, "L2P") or {}).get("Parsons 泵站", {}).get("m3")
            P2 = (jload(C2D / f"twoway_C1_{TG(r)}_L2P.json") or {}).get("ponds", [])
            fill = sum(p.get("vol_end_m3") or 0 for p in P2) / max(sum(p.get("vol_design_m3") or 0 for p in P2), 1) * 100
            parts.append(f"L2+planned 的 27 座泵站抽了 {n(pv)} m³（1,535 L/s 满负荷 6 h 也只有约 3.3 万 m³，比冒出来的水少一个数量级），12 座塘 6 h 末只满 {fill:.0f}%，"
                         f"面积比 L2 {pct(zc('L2') if 'L2' in have else zc('L1'), zc('L2P')):+.0f}%")
        if "L2S" in have:
            d = L2Si.get(r) or {}; g = pumped(r, "L2S") or {}
            cv = g.get("L2+suggested 转输泵", {}).get("m3", 0) + (sump_out(r, "L2S") or 0)
            parts.append(f"L2+suggested（{len(d.get('measures', []))} 处转输 / 地面取水泵 / 箱涵，泵合计 {d.get('q_total_m3s', '—')} m³/s，6 h 抽走 {n(cv)} m³）"
                         f"面积比 L2+planned {pct(zc('L2P'), zc('L2S')):+.0f}%、比 L1 {pct(zc('L1'), zc('L2S')):+.0f}%，热点 200 m 内最深 {n(zd('L2S'), 2)} m")
        if "L2S3" in have:
            s3 = jload(ROOT / "out" / f"c1_L2S3_side_{TG(r)}.json") or {}; s2 = jload(ROOT / "out" / f"c1_L2S_side_{TG(r)}.json") or {}
            parts.append(f"<b>L2+suggested v3</b>（同样的措施处数，按塘的剩余库容重新分配去向、塘满停泵）面积比 L2+planned {pct(zc('L2P'), zc('L2S3')):+.0f}%、比 L1 {pct(zc('L1'), zc('L2S3')):+.0f}%，"
                         f"热点 200 m 内最深 {n(zd('L2S3'), 2)} m；<b>v2 的副作用（比 L2+planned 变深 {s2.get('deeper_total_ha', '—')} ha）降到 {s3.get('deeper_total_ha', '—')} ha</b>")
        li.append("<li>" + "；".join(parts) + "。</li>")
    li.append("<li><b>瓶颈是「能从管网里拿走多少水」，不是库容</b>：规划塘库容 44 万 m³ 但几乎空着；要压住热点，泵排 / 转输的能力得和冒水量同一量级。"
              "挡水土堤（v1）只会把水挡在堤外、就地变深，已弃用。</li>")
    return "<ul>" + "".join(li) + "</ul>"


def side_note():
    li = []
    for r in (61, 96):
        s = jload(ROOT / "out" / f"c1_L2S_side_{TG(r)}.json")
        if not s or not s["by_nearest"]:
            continue
        def one(x):
            cap = f"（库容 {n(x['pond_vol_m3'])} m³，6 h 末满 {x['pond_fill_pct']}%）" if x.get("pond_vol_m3") else ""
            return f"{x['near']}{cap} 周边 {x['deeper_ha']} ha、最多 +{x['max_dh_m']} m"
        rows = "；".join(one(x) for x in s["by_nearest"][:5])
        li.append(f"<li>{r} mm：比 L2+planned 变深 {s['deeper_total_ha']} ha，都在收水的小塘周边——{rows}</li>")
    if not li:
        return ""
    return ("<div class='card flag' style='margin-top:12px'><h3>v2 的一处不足：小塘周边溢出</h3><ul class='m'>" + "".join(li) + "</ul>"
            "<p>两个原因：① 设计时只看塘在 L2+planned 末的满度（&lt; 80% 就收），没核算加进来的水装不装得下，2,000–6,000 m³ 的小塘会被灌满；"
            "② 地面取水泵的水放在塘中心附近 20 个地面单元上，每 60 s 交换步里有一部分在被塘接住之前漫出了小塘的边。"
            "改进（v3，已算）：① 按塘的剩余库容分配每处措施的去向（可用 = 0.9 × 库容 ×（1 − L2+planned 末满度）），装不下的送到更大的塘；② 进塘的转输泵加控制规则，塘水深到 0.90 m（满 1.0 m）停泵；"
            "③ 地面取水泵直接打进塘的一维节点，塘到 90 % 即停。" + v3_side() + "</p></div>")


def v3_side():
    out = []
    for r in (61, 96):
        s3 = jload(ROOT / "out" / f"c1_L2S3_side_{TG(r)}.json"); s2 = jload(ROOT / "out" / f"c1_L2S_side_{TG(r)}.json")
        tw = jload(C2D / f"twoway_C1_{TG(r)}_L2S3.json")
        if not s3 or not tw:
            continue
        ov = [p for p in tw.get("ponds", []) if (p.get("overflow_m3") or 0) > 10]
        out.append(f"{r} mm：变深面积 {s2['deeper_total_ha'] if s2 else '—'} → <b>{s3['deeper_total_ha']} ha</b>，塘满溢出 "
                   + ("、".join(f"{p['unitid']} {n(p['overflow_m3'])} m³" for p in ov) if ov else "0") + f"，泵打进塘 {n(tw.get('acc_m3', {}).get('pump_pond'))} m³")
    return ("<br><b>v3 结果</b>：" + "；".join(out) + "。") if out else ""


def facility_tables(rs=(61, 96)):
    out = []
    for r in rs:
        if r not in RX:
            continue
        have = [s for s in ST if s in RX[r]["states"]]
        rows = []
        for st in have:
            g = pumped(r, st) or {}
            so = sump_out(r, st)
            if so:
                g = dict(g); e = g.setdefault("L2+suggested 转输泵", {"n": 0, "m3": 0.0, "util": 0.0}); e["m3"] = e["m3"] + so
            def pc(k):
                if k not in g:
                    return "<td class='n'>—</td>"
                e = g[k]
                return f"<td class='n'>{n(e['m3'])}<span class='u'> · {e['n']} 台（一维）· 利用率 {e['util']:.0f}%</span></td>"
            rows.append(f"<tr><td style='color:{COL[st]}'><b>{LAB[st]}</b></td>" + "".join(pc(k) for k in ("现状泵站", "L2 移动泵", "Parsons 泵站", "L2+suggested 转输泵")) + "</tr>")
        out.append(f"<h3>{LBL(r)} · 6 h 内各组泵抽走的水（m³）</h3><div class='scroll'><table><tr><th>态</th><th>现状泵站</th><th>L2 移动泵</th><th>Parsons 泵站</th><th>L2+suggested 转输 + 地面取水</th></tr>{''.join(rows)}</table></div>")
        tw = jload(C2D / f"twoway_C1_{TG(r)}_L2P.json")
        if tw:
            P2 = tw.get("ponds", [])
            pr = "".join(f"<tr><td>{p['unitid']}</td><td class='n'>{n(p.get('vol_design_m3'))}</td><td class='n'>{n(p.get('vol_end_m3'))}</td><td class='n'>{n(p.get('fill_pct'), 0)}%</td><td class='n'>{n(p.get('surface_captured_m3'))}</td></tr>"
                         for p in sorted(P2, key=lambda x: -(x.get("vol_design_m3") or 0)))
            tot = sum(p.get("vol_end_m3") or 0 for p in P2); cap = sum(p.get("vol_design_m3") or 0 for p in P2)
            out.append(f"<h3>{LBL(r)} · L2+planned 的 12 座塘</h3><p class='m'>6 h 末合计蓄水 {n(tot)} / {n(cap)} m³（{tot / cap * 100 if cap else 0:.0f}%）。</p>"
                       f"<div class='scroll'><table><tr><th>塘</th><th>库容 m³</th><th>6 h 末蓄水 m³</th><th>满度</th><th>其中地面直接进塘 m³</th></tr>{pr}</table></div>")
    return "".join(out)


def balance(r):
    if r not in R:
        return ""
    have = [s for s in ST if s in R[r]["states"]]
    B = R[r]["balance"]
    keys = [k for k in B[have[0]] if k != "wall_h" and (k != "泵排出系统（二维取水）" or any(B[s].get(k) for s in have))]
    rows = "".join("<tr><td>" + k + "</td>" + "".join(f"<td class='n'>{n(B[s].get(k))}</td>" for s in have) + "</tr>" for k in keys)
    rows += "<tr><td>计算墙钟时间（h）</td>" + "".join(f"<td class='n'>{n(B[s].get('wall_h'), 2)}</td>" for s in have) + "</tr>"
    return f"<div class='scroll'><table><tr><th>水量对账（{r} mm，m³）</th>{''.join(f'<th>{LAB[s]}</th>' for s in have)}</tr>{rows}</table></div>"


def l2s_list(r):
    d = L2Si.get(r)
    if not d:
        return "<p style='color:var(--muted)'>L2+planned 算完后按这场雨的残余积水设计（计算中）。</p>"
    kz = {"conv": "转输", "bund": "挡水土堤", "sump": "地面取水泵", "box": "箱涵"}
    li = "".join(f"<li><b>{m['id']} · {kz[m['kind']]}</b>（{m['near_hotspot']} 旁 {m['near_dist_m']} m，残余积水 {m['area_ha']} ha / {n(m['peak_vol_m3'])} m³）：{m['why']}</li>" for m in d["measures"])
    s = d["summary"]
    return (f"<p style='margin-top:0'>{r} mm 下 L2+planned 还剩 {d['n_residual_spots']} 处 ≥0.30 m 的积水点，处理 {len(d['measures'])} 处："
            f"转输 {s['conv']}、地面取水泵 {s['sump']}、箱涵 {s['box']}（泵合计 {d['q_total_m3s']} m³/s，箱涵 {n(d['box_total_m3'])} m³）；"
            f"其余 {len(d['skipped'])} 处离热点 500 m 以外或太小，不处理。</p><ul class='m'>{li}</ul>")


def v1_note():
    s = jload(ROOT / "out" / "c1_L2S1_R61_summary.json")
    if not s:
        return ""
    rows = "".join(f"<li>{k}：堤外变深 {v[0]} ha，最多加深 {v[1]} m</li>" for k, v in s["deeper_by_bund"].items())
    return (f"<div class='card flag' style='margin-top:12px'><h3>试过又弃用的：v1 土堤版（61 mm）</h3>"
            f"<p style='margin-top:0'>第一版 L2+suggested 用 4 道土堤把地表水挡在地道 / 洼地外（{s['measures']}）。算完发现：热点汇水区 ≥0.30 m 面积从 {s['catch_ge_0p30_ha']['L2P']} 降到 {s['catch_ge_0p30_ha']['L2S1']} ha，"
            f"但<b>热点 200 m 内最大水深从 {s['hs200_max_depth_m']['L2P']} 升到 {s['hs200_max_depth_m']['L2S1']} m</b>，堤外 {s['deeper_ha_vs_L2P']} ha 变深——{s['adm90']}：</p>"
            f"<ul class='m'>{rows}</ul><p>{s['why']}。96 mm 的 v1 用了同样的土堤，算到约 2.5 h 时停掉，直接换 v2。v1 的结果文件保留：{s['files']}。</p></div>")


def state_desc():
    q2 = L2i.get(61) or L2i.get(96); q3 = L2Pi.get(61) or L2Pi.get(96); l1 = L1i.get(61) or {}; ps = l1.get("pumps", [])
    return f"""<ul>
<li><b style="color:var(--l1)">L1</b>：现状管网（客户 GDB 重建；截取触及计算域的 {n(l1.get('n_components'))} 个连通块、{n(l1.get('n_nodes'))} 口井）+ 客户库登记的 {len(ps)} 座现状泵站（合计 {n(sum(p['q_m3s'] for p in ps) * 1000)} L/s）。C1 内没有已建塘。</li>
<li><b style="color:var(--l2)">L2</b>：L1 + Parsons OP-02 的 {q2['n_pumps'] if q2 else '—'} 台 12 英寸移动泵（雨前布设在固定点位，合计 {n(q2['q_total_lps'] if q2 else None)} L/s），各从最近的井抽、排到下游在 L1 96 mm 里不溢流的井。</li>
<li><b style="color:var(--pa)">L2+planned</b>：L1 + Parsons PDF 版方案：{q3['n_pumps'] if q3 else '—'} 座固定泵站（只开主用，合计 {n(q3['q_total_lps'] if q3 else None)} L/s）+ 12 座地上衬砌塘（1 m 围堤，{n(sum(p['vol'] for p in q3['ponds']) if q3 else None)} m³）+ 压力管排放；保留 L2 的 MP-16。两场雨设施相同。</li>
<li><b style="color:var(--pab)">L2+suggested</b>（v2）：L2+planned + 按<b>这场雨</b>的残余积水设计：管网接不住的点用固定泵转输到有余量的规划塘 / 出水口，地表来水的洼地用地面取水泵抽走，没有出路的才用箱涵；两场雨各一套。v1 的挡水土堤已弃用（见下）。</li>
<li><b style="color:var(--pc)">L2+suggested v3</b>：和 v2 同样的措施处数与位置，只改去向与控制：按规划塘的剩余库容分配、塘满停泵、地面取水泵直接打进塘；为了消掉 v2 在小塘周边的副作用（用户 2026-09-29 同意做 v3）。<b>2026-10-02 用户定：原计算域交付以 v3 为准，v2 留作历史</b>——交付目录两版都在，`计算设置.md` 的 C12 补充写明以 v3 为准；比 L2+planned 变深的面积 61 mm 由 v2 的 0.5 ha 降到 0.0 ha，96 mm 由 0.7 ha 降到 0.04 ha。</li></ul>"""


def params_list():
    order = ["A1", "A2", "A3", "B4", "B5", "B6", "B7", "B8", "C9", "C10", "C11", "C12", "D13", "D14", "D15", "E16", "E17", "E18", "E19", "E20"]
    rows = []
    for k in order:
        v = P.get(k) or {}
        rows.append(f"<tr><td><b>{k}</b></td><td>{v.get('choice_label', '')}</td><td style='font-size:13px'>{v.get('value') or ''}"
                    + (f"<br><span style='color:var(--muted)'>{v['note']}</span>" if v.get("note") and "Claude 自定" in v.get("note", "") else "") + "</td></tr>")
    return "<div class='scroll'><table><tr><th>项</th><th>确认的选项</th><th>取值 / 说明（含 Claude 自定）</th></tr>" + "".join(rows) + "</table></div>"


def shots(r):
    ks = [s for s in ST if f"{r}_{s}" in IMG]
    if not ks:
        return ""
    btn = "".join(f'<button type="button" data-s="{r}_{s}" aria-selected="{str(s == ks[-1]).lower()}">{LAB[s]}</button>' for s in ks)
    return (f'<div class="card player" style="margin-top:12px"><div class="pl-head"><div class="seg" role="tablist" aria-label="{r} mm 截图">{btn}</div></div>'
            f'<figure class="shot"><img id="img{r}" src="{IMG[f"{r}_{ks[-1]}"]}" alt="系统 C1 视图 {r} mm" width="1500" height="1000"></figure>'
            f'<p class="m">系统「防线 2+ 多态对比 → C1」：{r} mm 下各态全程最大水深（截图 2026-09-29）；设施按类型分符号——深灰方块 现状泵站、青三角 OP-02 移动泵、橙方块 Parsons 泵站、蓝面 规划塘、紫菱形 转输泵、玫红圆 地面取水泵、灰条 箱涵，符号大小随流量，虚线是排水去向。系统里可逐帧播放、看与上一态的差值、点设施看参数与实际表现。</p></div>')


def dp_note():
    out = []
    for r in R:
        d = R[r]["deepest"].get("L1")
        if d:
            out.append(f"{r} mm 全域最深 {d['depth_m']} m 在 UTM ({d['x']}, {d['y']})：地面 {d['ground_m']} m，200 m 内地面中位 {d['median_ground_200m']} m，离 {d['near_hotspot']} {d['near_dist_m']} m——是封闭洼地 / 坑，与热点无关")
    return "；".join(out)


def kcard(r):
    have = [s for s in ST if r in R and s in R[r]["states"]]
    if not have:
        return f"<div class='kc'><span class='lab'>{r} mm / 2 h</span><span class='big'>计算中</span></div>"
    a = [z(r, "C1 八个热点|catch", s, "area_ge_0p30_ha") for s in have]
    chain = "<i>→</i>".join(f"<span style='color:{COL[s]}'>{n(v, 1)}</span>" for s, v in zip(have, a))
    p = pct(a[0], a[-1])
    return (f"<div class='kc'><span class='lab'>{r} mm / 2 h · 八个热点汇水区 ≥0.30 m 积水面积 · ha</span><span class='big'>{chain}</span>"
            f"<span class='sub'>{' → '.join(LAB[s] for s in have)}{f' · {LAB[have[-1]]} 比 L1 {p:+.0f}%' if p is not None and len(have) > 1 else ''}</span></div>")


CSS = """<style>
  :root{--pab:#7c3aed} @media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--pab:#a78bfa}} :root[data-theme="dark"]{--pab:#a78bfa}
  .kgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
  .kc{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:13px 15px}
  .kc .lab{font-family:var(--cond);font-size:13px;font-weight:600;color:var(--muted);display:block}
  .kc .big{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:clamp(17px,2.4vw,22px);font-weight:600;display:flex;gap:6px;flex-wrap:wrap;align-items:baseline;margin-top:4px}
  .kc .big i{font-style:normal;color:var(--muted);font-size:.7em} .kc .sub{font-family:var(--mono);font-size:12px;color:var(--muted);display:block;margin-top:3px}
  .pl-head .seg{display:flex;flex-wrap:wrap;gap:4px;background:var(--paper);border-radius:4px;padding:3px}
  .pl-head .seg button{flex:1 1 120px;border:0;background:none;padding:7px 10px;border-radius:3px;font:600 13px var(--cond);color:var(--muted);cursor:pointer}
  .pl-head .seg button[aria-selected="true"]{background:var(--card);color:var(--ink);box-shadow:0 0 0 1px var(--line)}
  button:focus-visible{outline:2px solid var(--l2);outline-offset:2px}
  .shot{margin:12px 0 0} .shot img{width:100%;height:auto;display:block;border:1px solid var(--line);border-radius:3px}
  ul.m li{margin:4px 0;font-size:13.5px} .m{font-size:13.5px;color:var(--muted)}
  @media (max-width:720px){.kgrid{grid-template-columns:1fr}}
  .part{margin:40px 0 14px;padding:16px 18px;border-radius:6px;border:1px solid var(--line);background:var(--card)}
  .part h2{margin:4px 0 6px} .part p{margin:0;font-size:14px;color:var(--muted)}
  .part .tag{display:inline-block;font:600 12px var(--cond);letter-spacing:.06em;padding:2px 8px;border-radius:3px;color:#fff}
  .part-a{border-left:6px solid var(--pc)} .part-a .tag{background:var(--pc)}
  .part-b{border-left:6px solid var(--l1)} .part-b .tag{background:var(--l1)}
  .part-c{border-left:6px solid var(--muted);background:var(--paper)} .part-c .tag{background:var(--muted)}
  details.histbox{margin:10px 0;border:1px dashed var(--line);border-radius:6px;padding:10px 14px;background:var(--paper)}
  details.histbox>summary{cursor:pointer;font:600 14px var(--cond);color:var(--muted)}
  section.hist .sechead h2{color:var(--muted)} section.hist .sechead h2::before{content:"历史 · ";font-size:.8em}
  section.hist .card{opacity:.92}
</style>"""


# ---------------- 设施容量核算（用户 2026-09-29「加上设施容量核算表」）----------------
# 每个设施：额定能力 → 6 h 实际用了多少 → 判定；再看排放点接不接得住。泵的「能力用了」= 实际抽水量 ÷（额定流量 × 6 h）。
# 判定口径（Claude 自定）：≥ 70% 满负荷（能力是瓶颈）、20–70% 部分、< 20% 基本闲着（多半是吸水处水不够或没到开泵水位）。
SIM_S = 6 * 3600.0
_RPT = {}


def _rpt_tables(r, st):
    k = (r, st)
    if k not in _RPT:
        f = C2D / f"C1_{TG(r)}_{st}_2way.rpt"
        t = f.read_text(errors="ignore") if f.exists() else ""
        def tab(title, ncol):
            m = re.search(title + r".*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
            return {q[0]: q for q in (l.split() for l in (m.group(1).splitlines() if m else [])) if len(q) >= ncol}
        pump = {a: {"util": float(q[1]), "m3": float(q[6]) * 1000} for a, q in tab("Pumping Summary", 7).items()}
        flood = {a: float(q[5]) * 1000 for a, q in tab("Node Flooding Summary", 6).items()}
        sto = {a: {"max_m3": float(q[5]) * 1000, "max_pct": float(q[6])} for a, q in tab("Storage Volume Summary", 7).items()}
        tw = jload(C2D / f"twoway_C1_{TG(r)}_{st}.json") or {}
        ponds = {p["unitid"]: p for p in tw.get("ponds", [])}
        p2d = {q["id"]: q.get("pumped_m3") or 0 for q in tw.get("pumps2d", [])}
        _RPT[k] = {"pump": pump, "flood": flood, "sto": sto, "ponds": ponds, "p2d": p2d}
    return _RPT[k]


def _verdict(u):
    if u is None:
        return "<span style='color:var(--muted)'>无记录</span>"
    if u >= 70:
        return "<b style='color:var(--warn)'>满负荷</b>"
    if u >= 20:
        return "部分"
    return "<span style='color:var(--muted)'>基本闲着</span>"


def _recv(r, st, to):
    """排放点接不接得住：塘看溢出，井看这一态的溢流（及比上一态多出的部分），出水口自由出流。"""
    T = _rpt_tables(r, st)
    if not to:
        return "—"
    if to.startswith("PND_"):
        p = T["ponds"].get(to)
        if not p:
            return esc(to)
        ov = p.get("overflow_m3") or 0
        return f"{esc(to)}：" + (f"<b style='color:var(--warn)'>塘满溢出 {n(ov)} m³</b>" if ov > 10 else f"接得住（末满 {n(p.get('fill_pct'), 0)}%）")
    if to.startswith("X_") or "OUTFALL" in to or to.startswith("OF"):
        return "出水口（自由出流）"
    f = T["flood"].get(to, 0.0)
    par = PARENT.get(st)
    f0 = _rpt_tables(r, par)["flood"].get(to, 0.0) if par else 0.0
    if f <= 10:
        return "接收井接得住" + (f"（溢流 {n(f)} m³，可忽略）" if f > 0 else "")
    return f"<b style='color:var(--warn)'>接收井溢流 {n(f)} m³</b>" + (f"（比 {LAB[par]} 多 {n(f - f0)}）" if par else "")


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pump_rows(r):
    """[(态, 类型, 编号, 额定 L/s, 实际 m³, 能力用了 %, 运行时间 %, 排放点)]"""
    rows = []
    have = RX[r]["states"]
    meta = jload(C2D / f"pilot_C1_{TG(r)}_L1.json") or {}
    if "L1" in have:
        T = _rpt_tables(r, "L1")
        for p in meta.get("pumps_1d", []):
            e = T["pump"].get(p["id"]); q = p["q_m3s"] * 1000
            rows.append(("L1", "现状泵站", ("PSS_" + p["ps"]) if str(p["ps"]).isdigit() else p["ps"], q, e and e["m3"], e and e["m3"] / (q / 1000 * SIM_S) * 100, e and e["util"], _recv(r, "L1", p["to"]), p.get("q_src")))
    if "L2" in have and L2i.get(r):
        T = _rpt_tables(r, "L2")
        for p in L2i[r]["pumps"]:
            e = T["pump"].get(p["id"]); q = p["q_lps"]
            rows.append(("L2", "OP-02 移动泵", p["site"], q, e and e["m3"], e and e["m3"] / (q / 1000 * SIM_S) * 100, e and e["util"], _recv(r, "L2", p["to"]),
                         f"吸水井深 {p['well_depth_m']} m，开 {p['on']} / 停 {p['off']} m"))
    if "L2P" in have and L2Pi.get(r):
        T = _rpt_tables(r, "L2P")
        for p in L2Pi[r]["pumps"]:
            e = T["pump"].get(p["id"]); q = p["q_lps"]
            note = p["how_from"] + ("；从塘里抽，塘里水深没到开泵水位就不开" if p["from"].startswith("PND_") else "")
            rows.append(("L2P", "Parsons 泵站", p["station"], q, e and e["m3"], e and e["m3"] / (q / 1000 * SIM_S) * 100, e and e["util"], _recv(r, "L2P", p["to"]), note))
    for sst in ("L2S", "L2S3"):
      if sst not in have or not SI[sst].get(r):
        continue
      T = _rpt_tables(r, sst)
      for m in SI[sst][r]["measures"]:
            if m["kind"] not in ("conv", "sump"):
                continue
            q = m["q_m3s"] * 1000
            if m["kind"] == "conv":
                e = T["pump"].get(f"{m['id']}_P"); v = e and e["m3"]; u = e and e["util"]; to = m["to"]
            else:
                v = T["p2d"].get(m["id"]); u = None
                pm = re.search(r"规划塘 (PND_C1_\d+)", m.get("why") or "")
                to = m.get("to_pond") or (pm.group(1) if (m.get("to_xy") and pm) else "PROPOSED OUTFALL")
            rows.append((sst, "转输泵" if m["kind"] == "conv" else "地面取水泵", m["id"], q, v, v / (q / 1000 * SIM_S) * 100 if v is not None else None, u,
                         _recv(r, sst, to), f"{m.get('n_pumps')} 台 12 英寸 · {m['near_hotspot']}"))
    return rows


def _store_rows(r):
    rows = []
    have = RX[r]["states"]
    for st in ("L2P", "L2S", "L2S3"):
        if st not in have:
            continue
        T = _rpt_tables(r, st)
        for k, p in sorted(T["ponds"].items(), key=lambda x: -(x[1].get("vol_design_m3") or 0)):
            rows.append((st, "规划塘", k, p.get("vol_design_m3"), p.get("vol_end_m3"), p.get("fill_pct"), p.get("surface_captured_m3"), p.get("pumped_in_m3"), p.get("overflow_m3")))
        if st in SI and SI[st].get(r):
            for m in SI[st][r]["measures"]:
                if m["kind"] == "box":
                    s = T["sto"].get(f"BOX_{m['id']}") or {}
                    rows.append((st, "箱涵", m["id"], m["vol_m3"], s.get("max_m3"), s.get("max_pct"), None, None, None))
    return rows


def capacity_section(rs=(61, 96), title="设施容量核算", hist=False):
    out = []
    for r in rs:
        if r not in RX:
            continue
        pr = _pump_rows(r)
        # 汇总：每态每类 额定合计、6 h 满负荷可抽、实际、判定分布
        agg = {}
        for st, kind, _id, q, v, u6, ut, recv, _note in pr:
            a = agg.setdefault((st, kind), {"n": 0, "q": 0.0, "v": 0.0, "full": 0, "part": 0, "idle": 0, "bad_recv": 0})
            a["n"] += 1; a["q"] += q; a["v"] += v or 0
            a["full" if (u6 or 0) >= 70 else "part" if (u6 or 0) >= 20 else "idle"] += 1
            a["bad_recv"] += 1 if "warn" in recv else 0
        srow = "".join(
            f"<tr><td style='color:{COL[st]}'><b>{LAB[st]}</b></td><td>{kind}</td><td class='n'>{a['n']}</td><td class='n'>{n(a['q'])}</td><td class='n'>{n(a['q'] / 1000 * SIM_S)}</td>"
            f"<td class='n'>{n(a['v'])}</td><td class='n'>{a['v'] / (a['q'] / 1000 * SIM_S) * 100 if a['q'] else 0:.0f}%</td>"
            f"<td class='n'>{a['full']} / {a['part']} / {a['idle']}</td><td class='n'>{a['bad_recv'] or '—'}</td></tr>"
            for (st, kind), a in agg.items())
        out.append(f"<h3>{LBL(r)} · 泵：额定能力与 6 h 实际</h3><div class='scroll'><table><tr><th>态</th><th>类型</th><th>个数</th><th>额定合计 L/s</th><th>6 h 满负荷可抽 m³</th>"
                   f"<th>实际抽水 m³</th><th>能力用了</th><th>满负荷 / 部分 / 闲着</th><th>排放点接不住</th></tr>{srow}</table></div>")
        det = "".join(
            f"<tr><td style='color:{COL[st]}'>{LAB[st]}</td><td>{kind}</td><td class='id'>{esc(i)}</td><td class='n'>{n(q)}</td><td class='n'>{n(v)}</td>"
            f"<td class='n'>{n(u6, 0)}{'%' if u6 is not None else ''}</td><td class='n'>{n(ut, 0)}{'%' if ut is not None else ''}</td><td>{_verdict(u6)}</td><td>{recv}</td>"
            f"<td class='m'>{esc(note or '')}</td></tr>" for st, kind, i, q, v, u6, ut, recv, note in pr)
        out.append(f"<details><summary class='m'>逐台明细（{len(pr)} 个泵点）</summary><div class='scroll'><table><tr><th>态</th><th>类型</th><th>编号</th><th>额定 L/s</th><th>实际抽水 m³</th>"
                   f"<th>能力用了</th><th>运行时间</th><th>判定</th><th>排放点</th><th>备注</th></tr>{det}</table></div></details>")
        sr = _store_rows(r)
        if sr:
            srw = "".join(
                f"<tr><td style='color:{COL[st]}'>{LAB[st]}</td><td>{kind}</td><td class='id'>{esc(i)}</td><td class='n'>{n(cap)}</td><td class='n'>{n(v)}</td>"
                f"<td class='n'>{n(fp, 0)}{'%' if fp is not None else ''}</td><td class='n'>{n(sf)}</td><td class='n'>{n(pi)}</td>"
                f"<td class='n'>{('<b style=\"color:var(--warn)\">' + n(ov) + '</b>') if (ov or 0) > 1 else n(ov)}</td></tr>" for st, kind, i, cap, v, fp, sf, pi, ov in sr)
            tot = {}
            for st, kind, i, cap, v, fp, sf, pi, ov in sr:
                t_ = tot.setdefault((st, kind), [0, 0.0, 0.0, 0]); t_[0] += 1; t_[1] += cap or 0; t_[2] += v or 0; t_[3] += 1 if (ov or 0) > 1 else 0
            summ = "；".join(f"{LAB[st]} {kind} {t_[0]} 个：库容 {n(t_[1])} m³，用到 {n(t_[2])} m³（{t_[2] / t_[1] * 100 if t_[1] else 0:.0f}%）" + (f"，<b style='color:var(--warn)'>{t_[3]} 个溢出</b>" if t_[3] else "")
                            for (st, kind), t_ in tot.items())
            out.append(f"<h3>{LBL(r)} · 塘与箱涵：库容与用到多少</h3><p class='m'>{summ}。塘是 6 h 末蓄水，箱涵是全程最大蓄水。</p>"
                       f"<details><summary class='m'>逐个明细（{len(sr)} 个）</summary><div class='scroll'><table><tr><th>态</th><th>类型</th><th>编号</th><th>库容 m³</th><th>蓄水 m³</th><th>满度</th>"
                       f"<th>地表进塘 m³</th><th>泵打入 m³</th><th>溢出 m³</th></tr>{srw}</table></div></details>")
    if not out:
        return ""
    return ("<style>.cap td,.cap th{text-align:left}.cap td.n,.cap th.n{text-align:right}.cap td.id{white-space:nowrap;font-family:var(--mono)}</style>"
            f"<section{' class=hist' if hist else ''}><div class='sechead'><h2>{title}</h2></div><div class='card cap'>"
            "<p class='m' style='margin-top:0'>每个设施：额定能力、6 h 里实际用了多少、排出去的水接不接得住。<b>能力用了</b> = 实际抽水量 ÷（额定流量 × 6 h）；"
            "判定口径：≥ 70% 满负荷（能力是瓶颈，泵再大就能多抽），20–70% 部分，&lt; 20% 基本闲着（多半是吸水处水不够、没到开泵水位）。"
            "<b>运行时间</b> 是 SWMM 的 Percent Utilized（6 h 里泵开着的时间占比；地面取水泵在二维里抽，没有这一项）。"
            "排放点：塘看有没有塘满溢出，接收井看这一态有没有溢流、比上一态多了多少（≤ 10 m³ 算可忽略），出水口按自由出流。以上判定口径为 Claude 自定。</p>"
            + "".join(out) + "</div></section>")


# ---------------- 客户最关注的 3 个热点：水深过程（用户 2026-09-29）----------------
HSS = jload(ROOT / "console" / "data" / "c1_hs_series.json") or {}
HS3 = ["ADM-94", "ADM-487", "ADM-192"]
KZ = {"conv": "转输泵", "sump": "地面取水泵", "bund": "挡水土堤", "box": "箱涵"}      # 措施类型代号 → 中文


def hs_svg(r, h, y_max):
    D = HSS.get(TG(r), {}); S = D.get(h) or {}
    t = D.get("t_h") or []
    if not S or not t:
        return ""
    W, Hh, L, Rm, T, B = 420, 190, 40, 10, 12, 30
    pw, ph = W - L - Rm, Hh - T - B
    TM = max(6.0 if not str(r).endswith('H1') else 5.0, 1.0)
    X = lambda v: L + v / TM * pw
    Y = lambda v: T + ph - min(v, y_max) / y_max * ph
    s = [f"<svg viewBox='0 0 {W} {Hh}' role='img' aria-label='{h} {r} mm 水深过程' style='width:100%;height:auto;display:block'>"]
    step = 0.2 if y_max > 0.6 else 0.1
    v = 0.0
    while v <= y_max + 1e-9:
        s.append(f"<line x1='{L}' x2='{W - Rm}' y1='{Y(v):.1f}' y2='{Y(v):.1f}' stroke='var(--line)'/><text x='{L - 5}' y='{Y(v) + 4:.1f}' text-anchor='end' font-size='10' fill='var(--muted)'>{v:.1f}</text>")
        v += step
    s.append(f"<line x1='{L}' x2='{W - Rm}' y1='{Y(0.30):.1f}' y2='{Y(0.30):.1f}' stroke='var(--warn)' stroke-dasharray='4 3'/>"
             f"<text x='{W - Rm - 2}' y='{Y(0.30) - 3:.1f}' text-anchor='end' font-size='10' fill='var(--warn)'>0.30 m</text>")
    for k in range(int(TM) + 1):
        s.append(f"<text x='{X(k):.1f}' y='{T + ph + 14}' text-anchor='middle' font-size='10' fill='var(--muted)'>{k}</text>")
    s.append(f"<text x='{W - Rm}' y='{Hh - 3}' text-anchor='end' font-size='10' fill='var(--muted)'>降雨开始后 h</text><text x='{L}' y='{T - 2}' font-size='10' fill='var(--muted)'>水深 m（50 m 内最深）</text>")
    for st in [x for x in ST if x in S]:
        pts = " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in zip(t, S[st]["r50"]))
        s.append(f"<polyline points='{pts}' fill='none' stroke='{COL[st]}' stroke-width='{2.4 if st in ('L2S', 'L2S3') else 1.6}' stroke-linejoin='round'/>")
    s.append("</svg>")
    return "".join(s)


def hs3_section(rs=(61, 96), title="客户最关注的三个热点：水深怎么变", hist=False):
    if not HSS:
        return ""
    info = HSS.get("hotspots", {})
    leg = "".join(f"<span style='display:inline-flex;align-items:center;gap:5px;margin-right:14px'><i style='width:16px;height:3px;background:{COL[s]};display:inline-block'></i>{LAB[s]}</span>" for s in ST if any(s in (HSS.get(TG(r), {}).get(h) or {}) for r in rs for h in HS3))
    cards, rows = [], []
    for r in rs:
        D = HSS.get(TG(r), {})
        ym = max(0.6, max((max(D[h][s]["r50"]) for h in HS3 if h in D for s in D[h]), default=0.6) * 1.1)
        ym = round(ym + 0.049, 1)
        for h in HS3:
            if h not in D:
                continue
            cards.append(f"<div class='card' style='margin:0'><h3 style='margin-top:0'>{h} · {LBL(r)}</h3>{hs_svg(r, h, ym)}</div>")
            for st in [x for x in ST if x in D[h]]:
                e = D[h][st]; base = D[h].get("L1", {})
                dd = e["peak_m"] - base.get("peak_m", e["peak_m"])
                rows.append(f"<tr><td>{h}</td><td>{LBL(r)}</td><td style='color:{COL[st]}'><b>{LAB[st]}</b></td><td class='n'>{e['peak_m']:.2f}"
                            + (f" <span style='color:{'var(--good)' if dd < 0 else 'var(--warn)'}'>{dd:+.2f}</span>" if st != "L1" and abs(dd) >= 0.005 else "")
                            + f"</td><td class='n'>{e['peak_t_h']:.2f}</td><td class='n'>{e['h_ge30']:.1f}</td><td class='n'>{e['end_m']:.2f}</td><td class='n'>{e['peak_pt_m']:.2f}</td><td class='n'>{e['a200_max_ha']:.1f}</td></tr>")
    desc = "；".join(f"<b>{h}</b>（{info.get(h, {}).get('criticality', '')}，{esc(info.get(h, {}).get('root_cause_zh', ''))}）" for h in HS3)
    return (f"<section{' class=hist' if hist else ''}><div class='sechead'><h2>{title}</h2></div>"
            f"<p class='m'>{desc}。都在计算域里，客户台账的重要度都是 Very High。曲线是热点 50 m 内最深的水深（热点坐标是一个点，积水坑常偏开几十米），每 5 分钟一个值；红虚线是 0.30 m。</p>"
            f"<p class='m'>{leg}</p><div class='kgrid' style='grid-template-columns:repeat(auto-fit,minmax(300px,1fr));align-items:start'>{''.join(cards)}</div>"
            f"<div class='card cap' style='margin-top:12px'><div class='scroll'><table><tr><th>热点</th><th>雨</th><th>态</th><th class='n'>最深 m（比 L1）</th><th class='n'>何时最深 h</th>"
            f"<th class='n'>≥0.30 m 持续 h</th><th class='n'>模拟末 m</th><th class='n'>热点坐标处最深 m</th><th class='n'>200 m 内 ≥0.10 m 最大面积 ha</th></tr>{''.join(rows)}</table></div></div></section>")


# ---------------- C1 模型版（计算域 = 客户 3 个水力模型 RB3 + Musaffah_RD09 + B_C 的子汇水区并集，38.1 km²，情景 C1_M61，2026-09-30，以此为准）----------------
RM = jload(ROOT / "out" / "c1_stats_M61.json")
RX = {**R, **({"M61": RM} if RM else {})}          # 设施表、容量核算按情景取（原计算域 61 / 96 + 模型版）
for _s in ("L1", "L2", "L2P", "L2S3"):
    _f = ROOT / "out" / "c1_shots" / f"M61_{_s}.jpg"
    if _f.exists():
        IMG[f"M61_{_s}"] = "data:image/jpeg;base64," + base64.b64encode(_f.read_bytes()).decode()
# 与原计算域的差别从哪来（2026-09-30 核算：原计算域各态二维全程最大水深 ≥0.30 m 的单元，落在 8 个热点汇水区并集里的，按是否在模型版计算域内拆开）
M61_ATTR = {"L1": (25.5, 17.3, 8.2, 16.0), "L2P": (24.7, 16.5, 8.2, 15.3), "L2S3": (22.7, 15.4, 7.3, 14.7)}   # 原域合计、其中在模型版域内、域外、模型版


def m61_lead():
    """「先说结论」的第一条：模型版（以此为准）。"""
    if not RM:
        return ""
    zc = lambda st: ((RM["zones"].get("C1 八个热点|catch") or {}).get(st) or {}).get("area_ge_0p30_ha")
    hs = HSS.get("M61", {})
    pk = lambda h, st: ((hs.get(h) or {}).get(st) or {}).get("peak_m")
    return (f"<p style='margin-top:0'><b>以模型版为准</b>（计算域 = 客户 3 个水力模型的子汇水区，38.1 km²，61 mm / 2 h，<a href='#m61'>详见「C1 模型版」</a>）：八个热点汇水区 ≥0.30 m 积水 "
            f"L1 {n(zc('L1'), 1)} ha → L2 {n(zc('L2'), 1)} → L2+planned {n(zc('L2P'), 1)} → L2+suggested v3 {n(zc('L2S3'), 1)} ha（比 L1 {pct(zc('L1'), zc('L2S3')):+.0f}%）；"
            f"三个重点热点 200 m 内最深 v3 下 ADM-94 {pk('ADM-94', 'L2S3')} m、ADM-487 {pk('ADM-487', 'L2S3')} m、ADM-192 {pk('ADM-192', 'L2S3')} m。"
            f"比原计算域少的约 8 ha 在模型版域外，不是方案差异。原计算域 61 / 96 mm 当时的结论在第三部分（历史版本）。</p>")


def _sumd(v):
    return sum(v.values()) if isinstance(v, dict) else v


def m61_facilities():
    """模型版各态的设施：每态加了什么（清单）→ 各组泵抽了多少、塘满几成（实际发挥）→ 逐台容量核算。"""
    if not RM:
        return ""
    a1, a2, a3, a4 = L1i.get("M61") or {}, L2i.get("M61") or {}, L2Pi.get("M61") or {}, L2S3i.get("M61") or {}
    q1 = sum(p.get("q_m3s", 0) for p in a1.get("pumps", [])) * 1000
    pv = sum(p.get("vol") or 0 for p in a3.get("ponds", []))
    kinds = "、".join(f"{KZ.get(k, k)} {v} 处" for k, v in (a4.get("summary") or {}).items() if v)
    inv = [
        ("L1", "现状管网 + 现状泵站", f"{len(a1.get('pumps', []))} 座现状泵站（客户 GDB PS_PUMP），合计 {n(q1)} L/s；湿井在泵站井，启停 = 井深 50% / 10%"),
        ("L2", "L1 + OP-02 移动泵", f"+ {a2.get('n_pumps', '—')} 台 12 英寸移动泵（雨前布设），合计 {n(a2.get('q_total_lps'))} L/s，排到下游不溢流的井"),
        ("L2P", "L1 + Parsons 方案", f"+ {a3.get('n_pumps', '—')} 座固定泵站（只开主用），合计 {n(a3.get('q_total_lps'))} L/s；+ {len(a3.get('ponds', []))} 座地上衬砌塘，库容 {n(pv)} m³"
                                    + (f"；保留 L2 的 {len(a3.get('kept_mobile') or [])} 台移动泵" if a3.get("kept_mobile") else "")),
        ("L2S3", "L2+planned + 补充措施（v3）", f"+ {len(a4.get('measures', []))} 处：{kinds or '—'}，泵合计 {a4.get('q_total_m3s', '—')} m³/s；去向按塘剩余库容分配（12 座塘可用 {n(_sumd(a4.get('pond_budget_m3')))} m³，分完还剩 {n(_sumd(a4.get('pond_left_m3')))} m³），塘水深 ≥ 0.9 m 停泵"),
    ]
    rows = "".join(f"<tr><td style='color:{COL[st]}'><b>{LAB[st]}</b></td><td>{d}</td><td>{t}</td></tr>" for st, d, t in inv)
    return (f"<section id='m61fac'><div class='sechead'><h2>模型版 · 各态的设施</h2></div>"
            f"<div class='card'><h3 style='margin-top:0'>每个态有什么</h3><div class='scroll'><table><tr><th>态</th><th>组成</th><th>设施</th></tr>{rows}</table></div>"
            f"<p class='m'>设施的落点、参数与原计算域相同（同一套 Parsons 图纸与客户 GDB）；模型版计算域外的设施也保留在一维里。L2+suggested v3 按模型版这次的残余积水重新设计。</p></div>"
            f"<div class='card' style='margin-top:12px'><h3 style='margin-top:0'>实际发挥了多少</h3>{facility_tables(('M61',))}</div>"
            f"</section>"
            f"{capacity_section(('M61',), title='模型版 · 设施容量核算')}")


def m61_section():
    if not RM:
        return ""
    have = [s for s in ST if s in RM["states"]]
    zz = lambda zone, st, k: ((RM["zones"].get(zone) or {}).get(st) or {}).get(k)
    rows = []
    for zone, lab in (("C1 模型单元|unit", "模型版计算域合计（38.1 km²）"), ("RB3|unit", "RB3（ADM-94 / 487 / 192 所在）"), ("Musaffah_RD09|unit", "Musaffah_RD09（ADM-90）"),
                      ("B_C|unit", "B_C（ADM-21 / 104 / 486）"), ("C1 八个热点|catch", "八个热点汇水区（与原计算域同口径，限域内）"), ("C1 八个热点|200m", "热点 200 m 内")):
        for k, kl, d in (("area_ge_0p30_ha", "≥0.30 m 积水面积 ha", 2), ("max_depth_m", "最大水深 m", 2), ("peak_vol_m3", "最大积水量 m³", 0)):
            vals = [zz(zone, s, k) for s in have]
            if all(v is None for v in vals):
                continue
            base = vals[0]
            tds = "".join(f"<td class='n'>{n(v, d)}" + (f"<span class='u' style='color:{'var(--good)' if v < base else 'var(--warn)'}'>{(v - base) / base * 100:+.0f}% 比 L1</span>" if (i and base and v is not None and abs(v - base) > 1e-9) else "") + "</td>"
                          for i, v in enumerate(vals))
            rows.append(f"<tr><td>{lab}</td><td>{kl}</td>{tds}</tr>")
    th = "".join(f"<th style='color:{COL[s]}'>{LAB[s]}</th>" for s in have)
    d3 = jload(ROOT / "out" / "c1_L2S3_inputs_M61.json") or {}
    s3 = jload(ROOT / "out" / "c1_L2S3_side_M61.json") or {}
    hs = HSS.get("M61", {}); R0 = HSS.get("R61", {})
    hrows = "".join(f"<tr><td>{h}</td>" + "".join(f"<td class='n'>{((R0.get(h) or {}).get(s) or {}).get('peak_m', '—')} / <b>{((hs.get(h) or {}).get(s) or {}).get('peak_m', '—')}</b></td>" for s in ("L1", "L2P", "L2S3")) + "</tr>" for h in HS3)
    arows = "".join(f"<tr><td>{LAB[s]}</td><td class='n'>{a[0]}</td><td class='n'>{a[1]}</td><td class='n'>{a[2]}</td><td class='n'><b>{a[3]}</b></td></tr>" for s, a in M61_ATTR.items())
    ks = [s for s in have if f"M61_{s}" in IMG]
    btn = "".join(f'<button type="button" data-s="M61_{s}" aria-selected="{str(s == ks[-1]).lower()}">{LAB[s]}</button>' for s in ks)
    shots_html = (f'<div class="card player" style="margin-top:12px"><div class="pl-head"><div class="seg" role="tablist">{btn}</div></div>'
                  f'<figure class="shot"><img id="imgM61" src="{IMG[f"M61_{ks[-1]}"]}" alt="模型版" width="1500" height="1000"></figure>'
                  f'<p class="m">系统「防线 2+ 多态对比 → C1 → 61 mm · 模型版」，全程最大水深。</p></div>') if ks else ""
    B = RM["balance"]
    xd = max(abs(B[s]["1D→2D"] - B[s]["一维：域内井溢流（=交给二维）"]) / B[s]["1D→2D"] * 100 for s in have)
    return (f"<section id='m61'><div class='sechead'><h2>C1 模型版（61 mm / 2 h）· 以此为准</h2></div>"
            f"<div class='card'><p style='margin-top:0'>用户 2026-09-30 定：汇水区<b>以客户 22 个水力模型的子汇水区为准</b>（flood 库与截图描绘都是手描、不准）。"
            f"C1 的 8 个热点落在客户 3 个模型里——<b>RB3</b>（ADM-94 / 487 / 192 / 202）、<b>Musaffah_RD09</b>（ADM-90）、<b>B_C</b>（ADM-21 / 104 / 486）——"
            f"计算域取这 3 个模型子汇水区的并集（重叠处理后、补细缝与小空洞），<b>38.1 km²，8 个热点全在域内</b>；按用户「按模型版重算 61mm 2 小时的 l1、l2、l2+」只跑 61 mm / 2 h。"
            f"其余参数同原计算域；L2+suggested 用 v3 规则按这次的残余积水重新设计（{len(d3.get('measures', []))} 处：{'、'.join(f'{KZ.get(k, k)} {v}' for k, v in (d3.get('summary') or {}).items() if v)}，"
            f"泵合计 {d3.get('q_total_m3s', '—')} m³/s，全部打进有余量的规划塘）。情景名 C1_M61；交付目录 <code>C1_模型版_61mm2h</code>。</p>"
            f"<div class='scroll'><table><tr><th>范围</th><th>指标</th>{th}</tr>{''.join(rows)}</table></div>"
            f"<p class='m'>质检：四个态都算完（72 / 72 帧）；一维连续性误差 1.74–1.78 %；一二维交换对账差 ≤ {xd:.2f} %；v3 比 L2+planned 变深 {s3.get('deeper_total_ha', '—')} ha（无副作用）。"
            f"二维直接降雨 {n(B['L1']['二维直接降雨'])} m³（原计算域 66.0 万 m³）。</p></div>"
            f"<div class='card flag' style='margin-top:12px'><h3>和原计算域（42.8 km²）比，积水少三分之一——差别在计算域的边界，不在方案</h3>"
            f"<p style='margin-top:0'>八个热点汇水区 ≥0.30 m 积水面积 L1 下 原计算域 25.5 ha，模型版 16.0 ha。两个计算域只重叠 23.6 km²：原计算域有 19.2 km² 在模型版外，"
            f"模型版 14.5 km² 在原计算域外。<b>核算</b>：把原计算域的积水单元按是否在模型版计算域内拆开——</p>"
            f"<div class='scroll'><table><tr><th>态</th><th>原计算域 ha</th><th>其中在模型版域内</th><th>在模型版域外</th><th>模型版 ha</th></tr>{arows}</table></div>"
            f"<p class='m'>原计算域多出来的约 8 ha 全在模型版域外，也就是不在这 3 个客户模型的子汇水区里；域内两版相差约 1 ha，来自边界上来水的不同。各态之间的变化方向两版一致。</p>"
            f"<h3 style='margin-top:12px'>三个重点热点 200 m 内最深（原计算域 / <b>模型版</b>，m）</h3>"
            f"<div class='scroll'><table><tr><th>热点</th><th>L1</th><th>L2+planned</th><th>L2+suggested v3</th></tr>{hrows}</table></div>"
            f"<p class='m'>ADM-487：v3 下降到 0.20 m（原计算域 0.32 m）；ADM-94：L1 下 0.21 m 比原计算域深，L2+planned 后降到 0.13 m；ADM-192 两版都在 0.2 m 以下。</p></div>"
            f"{shots_html}"
            f"</section>")


# ---------------- C1 模型版 96 mm / 1 h（情景 C1_M96H1，2026-10-03；同模型版计算域，只换雨：1 h 设计雨型放大到 96 mm，模拟 5 h）----------------
RH = jload(ROOT / "out" / "c1_stats_M96H1.json")
for _s in ("L1", "L2", "L2P", "L2S3"):
    _f = ROOT / "out" / "c1_shots" / f"M96H1_{_s}.jpg"
    if _f.exists():
        IMG[f"M96H1_{_s}"] = "data:image/jpeg;base64," + base64.b64encode(_f.read_bytes()).decode()


def m96h1_section():
    if not RH:
        return ""
    have = [s for s in ST if s in RH["states"]]
    zz = lambda zone, st, k: ((RH["zones"].get(zone) or {}).get(st) or {}).get(k)
    rows = []
    for zone, lab in (("C1 模型单元|unit", "模型版计算域合计（38.1 km²）"), ("RB3|unit", "RB3（ADM-94 / 487 / 192 所在）"), ("Musaffah_RD09|unit", "Musaffah_RD09（ADM-90）"),
                      ("B_C|unit", "B_C（ADM-21 / 104 / 486）"), ("C1 八个热点|catch", "八个热点汇水区"), ("C1 八个热点|200m", "热点 200 m 内")):
        for k, kl, d in (("area_ge_0p30_ha", "≥0.30 m 积水面积 ha", 2), ("max_depth_m", "最大水深 m", 2), ("peak_vol_m3", "最大积水量 m³", 0)):
            vals = [zz(zone, s, k) for s in have]
            if all(v is None for v in vals):
                continue
            base = vals[0]
            tds = "".join(f"<td class='n'>{n(v, d)}" + (f"<span class='u' style='color:{'var(--good)' if v < base else 'var(--warn)'}'>{(v - base) / base * 100:+.0f}% 比 L1</span>" if (i and base and v is not None and abs(v - base) > 1e-9) else "") + "</td>"
                          for i, v in enumerate(vals))
            rows.append(f"<tr><td>{lab}</td><td>{kl}</td>{tds}</tr>")
    th = "".join(f"<th style='color:{COL[s]}'>{LAB[s]}</th>" for s in have)
    d3 = jload(ROOT / "out" / "c1_L2S3_inputs_M96H1.json") or {}
    s3 = jload(ROOT / "out" / "c1_L2S3_side_M96H1.json") or {}
    a1 = jload(ROOT / "out" / "c1_L1_inputs_M96H1.json") or {}
    kinds = "、".join(f"{KZ.get(k, k)} {v}" for k, v in (d3.get("summary") or {}).items() if v)
    ks = [s for s in have if f"M96H1_{s}" in IMG]
    btn = "".join(f'<button type="button" data-s="M96H1_{s}" aria-selected="{str(s == ks[-1]).lower()}">{LAB[s]}</button>' for s in ks)
    shots_html = (f'<div class="card player" style="margin-top:12px"><div class="pl-head"><div class="seg" role="tablist">{btn}</div></div>'
                  f'<figure class="shot"><img id="imgM96H1" src="{IMG[f"M96H1_{ks[-1]}"]}" alt="模型版 96 mm / 1 h" width="1500" height="1000"></figure>'
                  f'<p class="m">系统「防线 2+ 多态对比 → C1 → 96 mm / 1 h · 模型版」，全程最大水深。</p></div>') if ks else ""
    # 各组泵抽走的水（5 h）
    prow = []
    for st in have:
        g = pumped("M96H1", st) or {}
        so = sump_out("M96H1", st)
        if so:
            g = dict(g); e = g.setdefault("L2+suggested 转输泵", {"n": 0, "m3": 0.0, "util": 0.0}); e["m3"] += so
        c = lambda k: (f"<td class='n'>{n(g[k]['m3'])}<span class='u'> · {g[k]['n']} 台 · 利用率 {g[k]['util']:.0f}%</span></td>" if k in g else "<td class='n'>—</td>")
        prow.append(f"<tr><td style='color:{COL[st]}'><b>{LAB[st]}</b></td>" + "".join(c(k) for k in ("现状泵站", "L2 移动泵", "Parsons 泵站", "L2+suggested 转输泵")) + "</tr>")
    pump_html = (f"<h3>5 h 内各组泵抽走的水（m³）</h3><div class='scroll'><table><tr><th>态</th><th>现状泵站</th><th>L2 移动泵</th><th>Parsons 泵站</th><th>L2+suggested 转输 + 地面取水</th></tr>{''.join(prow)}</table></div>")
    B = RH["balance"]
    keys = [k for k in B[have[0]] if k != "wall_h" and (k != "泵排出系统（二维取水）" or any(B[s].get(k) for s in have))]
    brow = "".join("<tr><td>" + k.replace("6 h 末", "5 h 末") + "</td>" + "".join(f"<td class='n'>{n(B[s].get(k))}</td>" for s in have) + "</tr>" for k in keys)
    brow += "<tr><td>计算墙钟时间（h）</td>" + "".join(f"<td class='n'>{n(B[s].get('wall_h'), 2)}</td>" for s in have) + "</tr>"
    bal_html = f"<div class='scroll'><table><tr><th>水量对账（96 mm / 1 h，m³）</th>{''.join(f'<th>{LAB[s]}</th>' for s in have)}</tr>{brow}</table></div>"
    B1 = B["L1"]; tot1 = B1["一维：域内井溢流（=交给二维）"] + B1["一维：域外井溢流（只算一维，记为损失）"]
    out_share = B1["一维：域外井溢流（只算一维，记为损失）"] / tot1 * 100
    zc = lambda st: zz("C1 八个热点|catch", st, "area_ge_0p30_ha")
    zv = lambda st: zz("C1 八个热点|catch", st, "peak_vol_m3")
    return (f"<section id='m96h1'><div class='sechead'><h2>C1 模型版（96 mm / 1 h）· 短历时强降雨</h2></div>"
            f"<div class='card'><p style='margin-top:0'>用户 2026-10-03 要求在模型版计算域上再跑一场 <b>1 h 96 mm</b>。计算域、参数、四个态的定义与上面的 61 mm / 2 h 相同；"
            f"雨型取客户 Mussafah 模型 1 h 设计雨型里带 CC 的那条（10 年一遇含气候变化）放大到 96 mm，模拟 5 h（历时 + 4 h）；"
            f"L2+suggested v3 按这场雨 L2+planned 的残余积水重新设计（{len(d3.get('measures', []))} 处：{kinds or '—'}，泵合计 {d3.get('q_total_m3s', '—')} m³/s）。情景名 C1_M96H1；交付目录 <code>C1_模型版_96mm1h</code>。</p>"
            f"<p><b>结果</b>：八个热点汇水区 ≥0.30 m 积水 L1 {n(zc('L1'), 1)} ha → L2 {n(zc('L2'), 1)} → L2+planned {n(zc('L2P'), 1)} → L2+suggested v3 {n(zc('L2S3'), 1)} ha（比 L1 {pct(zc('L1'), zc('L2S3')):+.0f}%）；"
            f"最大积水量 {n(zv('L1'))} → {n(zv('L2S3'))} m³（{pct(zv('L1'), zv('L2S3')):+.0f}%）。四个态之间的差别主要在热点局部（200 m 内 ≥0.30 m 面积 L1 {n(zz('C1 八个热点|200m', 'L1', 'area_ge_0p30_ha'), 2)} → v3 {n(zz('C1 八个热点|200m', 'L2S3', 'area_ge_0p30_ha'), 2)} ha）；计算域合计最大水深各态都是 {n(zz('C1 模型单元|unit', 'L1', 'max_depth_m'), 2)} m。</p>"
            f"<div class='scroll'><table><tr><th>范围</th><th>指标</th>{th}</tr>{''.join(rows)}</table></div>"
            f"<p class='m'>v3 比 L2+planned 变深 {s3.get('deeper_total_ha', '—')} ha。一维侧溢流合计 {n(tot1)} m³（L1），其中 <b>{out_share:.0f}% 发生在计算域外的井</b>，只算一维、记为损失、不进二维——"
            f"所以「SWMM Flooding Loss 比交给二维的水多 65%」不是交换不闭合，而是域外井的溢流：域内井溢流 {n(B1['一维：域内井溢流（=交给二维）'])} m³ 与交给二维的 {n(B1['1D→2D'])} m³ 对上（差 &lt; 0.1%）。"
            f"这部分水落在客户 3 个模型子汇水区以外，本计算域不模拟它在地面的去向。</p></div>"
            f"<div class='card' style='margin-top:12px'>{pump_html}</div>"
            f"<div class='card' style='margin-top:12px'><h3 style='margin-top:0'>水量对账</h3>{bal_html}"
            f"<p class='m'>「域内留存」为模拟末（5 h 末）二维域内的水量。</p></div>"
            f"{shots_html}"
            f"</section>")


# ---------------- C1 客户单元版（计算域 = RB-3 + MBZ + ADGC-10，20.3 km²，情景 C1_U61，2026-09-29）----------------
RU = jload(ROOT / "out" / "c1_stats_U61.json")
for _s in ("L1", "L2", "L2P", "L2S3"):
    _f = ROOT / "out" / "c1_shots" / f"U61_{_s}.jpg"
    if _f.exists():
        IMG[f"U61_{_s}"] = "data:image/jpeg;base64," + base64.b64encode(_f.read_bytes()).decode()


def u61_section():
    if not RU:
        return ""
    have = [s for s in ST if s in RU["states"]]
    zz = lambda zone, st, k: ((RU["zones"].get(zone) or {}).get(st) or {}).get(k)
    rows = []
    for zone, lab in (("C1 客户单元|unit", "三个客户单元合计（20.3 km²）"), ("RB-3|unit", "RB-3（三个重点热点所在）"), ("MBZ|unit", "MBZ"), ("ADGC-10|unit", "ADGC-10"),
                      ("C1 八个热点|catch", "八个热点汇水区（与原计算域同口径，限域内）"), ("C1 八个热点|200m", "热点 200 m 内")):
        for k, kl, d in (("area_ge_0p30_ha", "≥0.30 m 积水面积 ha", 2), ("max_depth_m", "最大水深 m", 2), ("peak_vol_m3", "最大积水量 m³", 0)):
            vals = [zz(zone, s, k) for s in have]
            base = vals[0]
            tds = "".join(f"<td class='n'>{n(v, d)}" + (f"<span class='u' style='color:{'var(--good)' if v < base else 'var(--warn)'}'>{(v - base) / base * 100:+.0f}% 比 L1</span>" if (i and base and v is not None and abs(v - base) > 1e-9) else "") + "</td>"
                          for i, v in enumerate(vals))
            rows.append(f"<tr><td>{lab}</td><td>{kl}</td>{tds}</tr>")
    th = "".join(f"<th style='color:{COL[s]}'>{LAB[s]}</th>" for s in have)
    d3 = jload(ROOT / "out" / "c1_L2S3_inputs_U61.json") or {}
    s3 = jload(ROOT / "out" / "c1_L2S3_side_U61.json") or {}
    hs = HSS.get("U61", {}); R0 = HSS.get("R61", {})
    hrows = "".join(f"<tr><td>{h}</td>" + "".join(f"<td class='n'>{((R0.get(h) or {}).get(s) or {}).get('peak_m', '—')} / <b>{((hs.get(h) or {}).get(s) or {}).get('peak_m', '—')}</b></td>" for s in ("L1", "L2P", "L2S3")) + "</tr>" for h in HS3)
    ks = [s for s in have if f"U61_{s}" in IMG]
    btn = "".join(f'<button type="button" data-s="U61_{s}" aria-selected="{str(s == ks[-1]).lower()}">{LAB[s]}</button>' for s in ks)
    shots_html = (f'<div class="card player" style="margin-top:12px"><div class="pl-head"><div class="seg" role="tablist">{btn}</div></div>'
                  f'<figure class="shot"><img id="imgU61" src="{IMG[f"U61_{ks[-1]}"]}" alt="客户单元版" width="1500" height="1000"></figure>'
                  f'<p class="m">系统「防线 2+ 多态对比 → C1 → 61 mm · 客户单元」，全程最大水深。</p></div>') if ks else ""
    B = RU["balance"]
    return (f"<section class='hist'><div class='sechead'><h2>客户单元版（61 mm / 2 h）· 已被模型版取代</h2></div>"
            f"<div class='card'><p style='margin-top:0'>用户 2026-09-29 定：计算域换成客户 Catchment 图层的三个单元 <b>RB-3 + MBZ + ADGC-10</b> 融合（20.3 km²，按截图描绘），只跑 61 mm / 2 h；"
            f"ADM-21 在域外 65 m 不包进来，域外地表来水不算，域外的 L2+planned 设施与 6 座现状泵站都保留，L2+suggested 用 v3 规则按这次的残余积水重新设计"
            f"（{len(d3.get('measures', []))} 处：{'、'.join(f'{KZ.get(k, k)} {v}' for k, v in (d3.get('summary') or {}).items() if v)}，泵合计 {d3.get('q_total_m3s', '—')} m³/s）。其余参数同原计算域。情景名 C1_U61，原结果保留。</p>"
            f"<div class='scroll'><table><tr><th>范围</th><th>指标</th>{th}</tr>{''.join(rows)}</table></div>"
            f"<p class='m'>v3 副作用（比 L2+planned 变深）：{s3.get('deeper_total_ha', '—')} ha。一维连续性误差 1.75–1.80 %；二维直接降雨 {n(B['L1']['二维直接降雨'])} m³（原计算域 66.0 万 m³）。</p></div>"
            f"<div class='card flag' style='margin-top:12px'><h3>和原计算域（42.8 km²）比，积水小很多——原因在计算域，不在方案</h3>"
            f"<p style='margin-top:0'>八个热点汇水区 ≥0.30 m 积水面积 L1 下 原计算域 {n(z(61, 'C1 八个热点|catch', 'L1', 'area_ge_0p30_ha'), 1)} ha，客户单元版 {n(zz('C1 八个热点|catch', 'L1', 'area_ge_0p30_ha'), 1)} ha。"
            f"核算：原计算域 L1 那 25.5 ha 里 <b>21.3 ha 在三个客户单元以外</b>，其中 13.9 ha 在客户 KMZ 的 RD-2（ADM-90 伸进内陆的地表汇水区，127 km² 里切进来 20 km²）里，"
            f"20.0 ha 落在没有管网覆盖、雨直接下在地面上的单元——内陆空地的洼地积水；落在三个客户单元里的只有 4.2 ha，和本版的 3.9 ha 相当。"
            f"客户单元版按客户的汇水区单元划，这片不在里面。三个重点热点自己的水深两版差不多（见下表：原计算域 / <b>客户单元版</b>，m），说明它们的积水主要来自本单元，不是来自内陆。</p>"
            f"<div class='scroll'><table><tr><th>热点</th><th>L1</th><th>L2+planned</th><th>L2+suggested v3</th></tr>{hrows}</table></div>"
            f"<p class='m'>两版的 L2+suggested 都取 v3。</p></div>"
            f"{shots_html}"
            f"</section>")


HY = jload(ROOT / "console" / "data" / "c1_hyeto.json") or {}


def hyeto_svg(tag, x_max=None, y_max=None, c_max=None):
    """雨型图：柱 = 时段平均雨强（mm/h，左轴），线 = 累计雨量（mm，右轴），虚线 = 累计净雨。数据 console/data/c1_hyeto.json（tools/c1_hyetograph.py）。"""
    h = HY.get(tag)
    if not h:
        return ""
    t, p, pc, ec = h["t_h"], h["p_mmph"], h["p_cum_mm"], h["exc_cum_mm"]
    dt = h["dt_min"] / 60.0
    x_max = x_max or h["dur_h"] + 0.5
    y_max = y_max or max(p) * 1.1
    c_max = c_max or h["rain_mm"] * 1.1
    W, H, L, Rm, T, B = 640, 250, 52, 52, 16, 38
    pw, ph = W - L - Rm, H - T - B
    X = lambda v: L + v / x_max * pw
    Y = lambda v: T + ph - v / y_max * ph
    Yc = lambda v: T + ph - v / c_max * ph
    s = [f"<svg viewBox='0 0 {W} {H}' role='img' aria-label='{h['rain_mm']:g} mm / {h['dur_h']:g} h 雨型' style='width:100%;height:auto;display:block'>"]
    step_y = 100 if y_max > 300 else 50 if y_max > 120 else 10
    for v in range(0, int(y_max) + 1, step_y):
        s.append(f"<line x1='{L}' x2='{W - Rm}' y1='{Y(v):.1f}' y2='{Y(v):.1f}' stroke='var(--line)' stroke-width='1'/>"
                 f"<text x='{L - 6}' y='{Y(v) + 4:.1f}' text-anchor='end' font-size='11' fill='var(--muted)'>{v}</text>")
    step_c = 20 if c_max > 60 else 10
    for v in range(0, int(c_max) + 1, step_c):
        s.append(f"<text x='{W - Rm + 6}' y='{Yc(v) + 4:.1f}' font-size='11' fill='var(--l2)'>{v}</text>")
    step_x = 0.5 if x_max <= 4 else 2 if x_max <= 16 else 4
    v = 0.0
    while v <= x_max + 1e-9:
        s.append(f"<line x1='{X(v):.1f}' x2='{X(v):.1f}' y1='{T + ph}' y2='{T + ph + 4}' stroke='var(--muted)'/>"
                 f"<text x='{X(v):.1f}' y='{T + ph + 17}' text-anchor='middle' font-size='11' fill='var(--muted)'>{v:g}</text>")
        v += step_x
    bw = max(dt / x_max * pw - 1.0, 1.0)
    for a, b in zip(t, p):
        if a >= x_max or b <= 0:
            continue
        s.append(f"<rect x='{X(a) + 0.5:.1f}' y='{Y(b):.1f}' width='{bw:.1f}' height='{T + ph - Y(b):.1f}' fill='var(--pa)'><title>{a:.2f}–{a + dt:.2f} h：{b:.1f} mm/h</title></rect>")
    pts = lambda arr, f: " ".join(f"{X(a + dt):.1f},{f(c):.1f}" for a, c in zip(t, arr) if a + dt <= x_max + 1e-9)
    s.append(f"<polyline points='{X(0):.1f},{Yc(0):.1f} {pts(pc, Yc)}' fill='none' stroke='var(--l2)' stroke-width='2'/>")
    s.append(f"<polyline points='{X(0):.1f},{Yc(0):.1f} {pts(ec, Yc)}' fill='none' stroke='var(--l2)' stroke-width='1.6' stroke-dasharray='5 4'/>")
    s.append(f"<line x1='{L}' x2='{W - Rm}' y1='{T + ph}' y2='{T + ph}' stroke='var(--muted)'/>")
    s.append(f"<text x='{L}' y='{T - 4}' font-size='11' fill='var(--muted)'>雨强 mm/h（每 {h['dt_min']} 分钟）</text>"
             f"<text x='{W - Rm}' y='{T - 4}' text-anchor='end' font-size='11' fill='var(--l2)'>累计 mm</text>"
             f"<text x='{L + pw / 2:.1f}' y='{H - 4}' text-anchor='middle' font-size='11' fill='var(--muted)'>降雨开始后 h</text></svg>")
    cap = (f"<p class='m' style='margin:6px 0 0'>{h['rain_mm']:g} mm / {h['dur_h']:g} h · 最大 {h['dt_min']} 分钟雨强 <b>{h['peak_mmph']:g} mm/h</b>"
           f"（{h['peak_t_h']:.2f}–{h['peak_t_h'] + dt:.2f} h，这 {h['dt_min']} 分钟下 {h['peak_mmph'] * dt:.1f} mm）· 净雨 {h['excess_mm']:g} mm（CN {h['cn']:g}）"
           f"<br>柱 = 雨强，实线 = 累计雨量，虚线 = 累计净雨（有管网覆盖处经单位线注入一维，无管网覆盖处直接下在二维上）</p>")
    return f"<div class='card'><h3>{h['rain_mm']:g} mm / {h['dur_h']:g} h</h3>{''.join(s)}{cap}</div>"


def hyeto_section():
    if not HY:
        return ""
    two = [k for k in ("R61", "R96") if k in HY]
    ym = max(max(HY[k]["p_mmph"]) for k in two) * 1.08 if two else None
    cards = "".join(hyeto_svg(k, x_max=2.5, y_max=ym, c_max=105) for k in two)
    if "M96H1" in HY:      # 1 h 雨：纵轴单独定（雨强比 2 h 那条高）
        cards += hyeto_svg("M96H1", x_max=1.5, y_max=max(HY["M96H1"]["p_mmph"]) * 1.08, c_max=105)
    src = HY[two[0]]["source"] if two else ""
    return (f"<section><div class='sechead'><h2>降雨（雨型）</h2></div><div class='kgrid' style='align-items:start'>{cards}</div>"
            f"<p class='m'>雨型：{src.split('，')[0]}——「10 年一遇 2 h（含气候变化）」设计雨，原总量 51.9 mm，每 30 秒一个点；"
            f"61 mm 与 96 mm 按总量等比放大，形状相同（参数 B4–B7）。这条雨的雨峰很集中：0.96–0.99 h 这 2.5 分钟就下了全场的 38%，"
            f"按 5 分钟算 61 mm 那场峰值 {(HY.get('R61') or {}).get('peak_mmph') or HY['R96']['peak_mmph'] * 61 / 96:.1f} mm/h、96 mm 那场 {HY['R96']['peak_mmph']:g} mm/h——短时雨强非常大。"
            f"2 h 雨的图纵轴相同，便于对比。"
            + (f"另一张是 96 mm / 1 h（模型版 96 mm / 1 h 用）：取 Mussafah 模型的 1 h 设计雨型（带 CC 的那条）放大到 96 mm，5 分钟最大雨强 {HY['M96H1']['peak_mmph']:g} mm/h。" if 'M96H1' in HY else "")
            + "</p></section>")


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    style = (ROOT / "tools" / "report_style.html").read_text(encoding="utf-8")
    done = sum(len(R[r]["states"]) for r in R)
    ntri = next((jload(ROOT / "console" / "data" / f"flood2d_c1_r{r}" / "stats.json") or {}).get("L1", {}).get("n_tri") for r in R) if R else None
    if ntri is None:      # 原计算域结果不在本机时，取模型版（15 m 网格，同一套）
        ntri = (jload(ROOT / "console" / "data" / "flood2d_c1_m96h1" / "stats.json") or {}).get("L1", {}).get("n_tri")
    body = f"""<title>C1 热点群一二维</title>
{style}{CSS}
<div class="wrap">
<p class="eyebrow">DMT · C1 热点群（ADM-21 / 90 / 94 / 104 / 192 / 202 / 486 + ADM-487）· SWMM 5.2 + ANUGA 15 m 双向耦合</p>
<h1>C1 热点群一二维</h1>
<p class="lede">客户要求对 Parsons 关键热点群 C1 做 L1、L2、L2+planned、L2+suggested 四个态的一二维水动力分析，降雨 61 mm / 2 h 与 96 mm / 2 h。
全部输入参数先在参数对齐页逐项确认（20 项）；之后按用户授权自动推进，自动推进中定的项都标「Claude 自定」。报告分三部分：<b>第一部分模型版是正式结果（以此为准）</b>；第二部分是两版通用的说明；第三部分是计算域已被取代的历史版本（原计算域、客户单元版），默认收起，仅供对照。</p>
<div class="meta"><span>计算域：模型版 38.1 km²（以此为准）· 原 42.8 km²</span><span>15 m 网格 · {n(ntri)} 个三角单元</span><span>地形 5 m DTM 原值</span><span>模拟 6 h</span><span>2026-09-30</span></div>

<div class="part part-a"><span class="tag">第一部分</span><h2>模型版 · 正式结果（以此为准）</h2>
<p>计算域 = 客户 3 个水力模型（RB3 + Musaffah_RD09 + B_C）的子汇水区并集，38.1 km²；61 mm / 2 h；L1 / L2 / L2+planned / L2+suggested v3 四个态。本报告的结论只看这一部分。</p></div>

<section><div class="sechead"><h2>先说结论</h2></div><div class="card">{m61_lead()}</div></section>

{m61_section()}

{m61_facilities()}

{hs3_section(("M61",), title="模型版 · 客户最关注的三个热点：水深怎么变")}

{m96h1_section()}

{hs3_section(("M96H1",), title="模型版 96 mm / 1 h · 客户最关注的三个热点：水深怎么变")}

<div class="part part-b"><span class="tag">第二部分</span><h2>通用说明</h2>
<p>两个版本共用的：雨型、四个态的定义、计算设置、要一起看的保留。</p></div>

{hyeto_section()}

<section><div class="sechead"><h2>四个态分别是什么</h2></div><div class="card">{state_desc()}</div></section>

<section><div class="sechead"><h2>计算设置（参数对齐页 20 项）</h2></div><div class="card"><p class="m" style="margin-top:0">下表是 2026-09-28 逐项确认的记录；其中 <b>A3 计算域</b>后来两次调整：2026-09-29 客户单元版（历史）、2026-09-30 <b>模型版</b>（客户 3 个水力模型的子汇水区，38.1 km²，以此为准）。其余参数三版相同。</p>{params_list()}</div></section>

<section><div class="sechead"><h2>要一起看的保留</h2></div>
  <div class="card flag">
  <p style="margin-top:0"><b>（原计算域）计算域外的来水。</b>ADM-90 的地表汇水区伸进内陆、碰到计算窗口边未收敛，窗口外的来水算不进；计算域内有大片内陆空地，统计口径里把「八个热点汇水区」「热点 200 m」「管网服务区」分开给。</p>
  <p><b>产流偏少。</b>统一 CN 82、Tc 30 min（参数 E16）；Parsons 子汇水区 CN 中位 85。</p>
  <p><b>现状泵站的排放点有推断。</b>RD-26 / RD-27 → OFL_137、PSS_98 / PSS_99 抽出计算域是推断；PSS_20 / PSS_21 没有流量数据未建；RD-02 客户库标停用。</p>
  <p><b>Parsons 方案的实现有推断。</b>8 座泵站在 Parsons 压力管网里找不到终点，排到最近的规划塘；标 (pond) 的 4 座从旁边的塘抽；地道线性调蓄没建（图纸只有示意）。</p>
  <p><b>L2 的移动泵。</b>客户定义里「事件中调派的移动泵」属 L3；OP-02 是雨前布设在固定点位，按 L2 处理，建议向客户确认。移动泵从检查井抽水时启停频繁（检查井蓄水小），抽水量本身没问题。</p>
  <p><b>L2+suggested 的规模。</b>泵按片内峰值溢流率定台数、按 3 h 抽完定地面取水泵，96 mm 那套约 99 台 12 英寸泵、规模偏大，是「能压到什么程度」的上限试算，工程上可再优化。</p>
  <p><b>最深点。</b>{dp_note()}。</p>
  <p style="color:var(--muted)">L2+suggested 只处理离 8 个热点 500 m 内、最大积水 ≥ 200 m³ 或 ≥ 0.05 ha 的残余积水（Claude 自定）；12 英寸泵按 0.36 m³/s（沿用 29.RB2 的取值）。</p>
  </div></section>

<div class="part part-c"><span class="tag">第三部分</span><h2>历史版本 · 仅供对照，不作为结论</h2>
<p>计算域已被模型版取代的两个版本：<b>原计算域</b>（8 个热点的地表 + 管网汇水区外扩 300 m，42.8 km²，61 与 96 mm / 2 h，含已弃用的 L2+suggested v1 / v2）和 <b>客户单元版</b>（按截图描绘的 RB-3 + MBZ + ADGC-10，20.3 km²）。
两者与模型版的差别主要在计算域边界（见第一部分的核算）。默认收起，点开看。</p></div>

<details class="histbox"><summary>展开历史版本：原计算域 61 / 96 mm（结论、结果、各热点、措施、设施、容量、水量对账）</summary>
<section class="hist"><div class="sechead"><h2>原计算域 · 当时的结论</h2></div><div class="card">{findings()}</div></section>

<section class="hist"><div class="sechead"><h2>原计算域 · 结果（42.8 km²，8 个热点汇水区外扩 300 m）</h2></div>
  <div class="kgrid">{kcard(61)}{kcard(96)}</div>
  <div class="card" style="margin-top:12px"><h3>61 mm / 2 h</h3>{main_table(61)}{shots(61)}</div>
  <div class="card" style="margin-top:12px"><h3>96 mm / 2 h</h3>{main_table(96)}{shots(96)}</div>
</section>

{hs3_section((61, 96), title="原计算域 · 三个重点热点：水深怎么变", hist=True)}

<section class="hist"><div class="sechead"><h2>原计算域 · 各热点</h2></div><div class="card">{hs_table(61)}{hs_table(96)}</div></section>

<section class="hist"><div class="sechead"><h2>原计算域 · L2+suggested 的措施（按场雨各一套）</h2></div>
  <div class="card"><h3>61 mm</h3>{l2s_list(61)}<h3>96 mm</h3>{l2s_list(96)}</div>{side_note()}{v1_note()}</section>

<section class="hist"><div class="sechead"><h2>原计算域 · 设施实际发挥了多少</h2></div><div class="card">{facility_tables()}</div></section>

{capacity_section((61, 96), title="原计算域 · 设施容量核算", hist=True)}

<section class="hist"><div class="sechead"><h2>原计算域 · 水量对账</h2></div>
  <div class="card">{balance(61)}{balance(96)}
  <p class="m">一维侧：计算域内井的溢流 = 交给二维的水（差 &lt; 0.1%，交换闭合）；计算域外的井（整块取进来的管网伸出域外的部分）只算一维，溢流记为损失，不进二维。</p></div></section>
</details>

<details class="histbox"><summary>展开历史版本：客户单元版（20.3 km²，61 mm / 2 h）</summary>
{u61_section()}
</details>

<p class="foot">一维 SWMM 5.2（客户 GDB 重建的全市模型截取，SCS CN 82 + 单位线按节点注入）；二维 ANUGA，15 m 非结构三角网，与 SWMM 同进程每 60 s 双向交换；
每 5 分钟一帧（带流速）。系统：防线 2+ 多态对比 → C1；参数对齐页：https://claude.ai/artifact/CkRHWiRfwvXCBvwBQuxNp4 。交付目录：out/交付/C1_61mm2h、C1_96mm2h。</p>
</div>
<script>(function(){{const I={json.dumps(IMG)};
  document.querySelectorAll('[data-s]').forEach(b=>b.onclick=()=>{{const r=b.dataset.s.split('_')[0];document.getElementById('img'+r).src=I[b.dataset.s];
    b.parentElement.querySelectorAll('[data-s]').forEach(x=>x.setAttribute('aria-selected',String(x===b)));}});}})();</script>
"""
    out = ROOT / "out" / "c1_report.html"
    out.write_text(body, encoding="utf-8")
    print(f"→ {out}：{len(body) // 1024} KB，已算完 {done} 个情景，截图 {len(IMG)} 张")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
