# -*- coding: utf-8 -*-
"""RB-01 一二维四态的**离线**报告（单个 HTML，图片内嵌 base64、不联网、不取外部字体）。
样式沿用 tools/report_style.html（去掉 Google Fonts，用系统字体）。

用法：python tools/report_rb01.py [--tag H61]
产物：out/rb01_report.html；同时拷到 console/data/flood2d_rb01_h60/report.html（系统里 RB-01 视图有链接）与交付目录
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import pathlib
import re
import shutil
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from rb01_build import HS  # noqa: E402
from workdir import WORK  # noqa: E402

E = html.escape
LAB = {"L1": "L1 现状", "L2": "L2 移动泵", "L2P": "L2+planned（PO 塘方案，来源待确认）", "L2S4": "L2+suggested v4（中间步）", "L2S5": "L2+suggested v5（建议方案）"}


def img(path, alt, w="100%"):
    b = base64.b64encode(pathlib.Path(path).read_bytes()).decode()
    ext = "jpeg" if str(path).lower().endswith(".jpg") else "png"
    return f'<img src="data:image/{ext};base64,{b}" alt="{E(alt)}" style="width:{w};height:auto;border:1px solid var(--line);border-radius:3px;background:#fff">'


def n(v, d=0):
    return "—" if v is None else f"{v:,.{d}f}"


def table(head, rows, hl=None):
    h = "<div class=scroll><table><tr>" + "".join(f"<th>{c}</th>" for c in head) + "</tr>"
    for i, r in enumerate(rows):
        h += f"<tr{' class=hl' if hl is not None and i == hl else ''}>" + "".join(
            f"<td{' class=n' if j else ''}>{c}</td>" for j, c in enumerate(r)) + "</tr>"
    return h + "</table></div>"
def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", default="H61"); a = ap.parse_args(argv)
    O = ROOT / "out"
    sys.path.insert(0, str(HERE))
    from rb01_build import delivery_dir, run_info
    RI = run_info(a.tag); SCEN = f"{RI['rain_mm']:g} mm / {RI['dur_h']:g} h"; SIM = f"{RI['sim_h']:g} h"; DD = delivery_dir(a.tag)
    TW1 = WORK / "c2d" / f"twoway_RB01_{a.tag}_L1.json"
    DATE = time.strftime("%Y-%m-%d", time.localtime(TW1.stat().st_mtime))           # 报告日期 = 计算完成日
    S = json.loads((O / f"rb01_stats_{a.tag}.json").read_text(encoding="utf-8"))["states"]
    I1 = json.loads((O / f"rb01_inputs_{a.tag}_L1.json").read_text(encoding="utf-8"))
    I2 = json.loads((O / f"rb01_inputs_{a.tag}_L2.json").read_text(encoding="utf-8"))
    IP = json.loads((O / f"rb01_inputs_{a.tag}_L2P.json").read_text(encoding="utf-8"))
    V4 = json.loads((O / f"rb01_L2S4_inputs_{a.tag}.json").read_text(encoding="utf-8"))
    V5 = json.loads((O / f"rb01_L2S5_inputs_{a.tag}.json").read_text(encoding="utf-8"))
    TRI = json.loads((pathlib.Path(__import__('workdir').WORK) / 'c2d' / f'twoway_RB01_{a.tag}_L1.json').read_text(encoding='utf-8'))['n_tri']
    sts = [s for s in ("L1", "L2", "L2P", "L2S4", "L2S5") if s in S]
    p, v5 = S["L2P"], S["L2S5"]
    pct = lambda x, y: f"{(y - x) / x * 100:+.0f} %" if x else "—"   # noqa: E731
    style = (HERE / "report_style.html").read_text(encoding="utf-8")
    style = re.sub(r'<link rel="stylesheet" href="https://fonts\.googleapis\.com[^>]*>\s*', "", style)   # 离线：不取外部字体
    shots = O / f"rb01_shots_{a.tag.lower()}"; figs = O / f"rb01_figs_{a.tag.lower()}"
    H = [f"<!doctype html><html lang=zh-CN><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>RB-01 一二维四态</title>{style}</head><body><div class=wrap>"]
    H.append(f"""<p class=eyebrow>RB-01 汇水区 · {SCEN} · 一二维耦合 · 离线报告</p>
<h1>RB-01：建议方案把 ≥0.30 m 积水从 {n(p['area_ge030_ha'], 1)} ha 降到 {n(v5['area_ge030_ha'], 1)} ha，别处没有变深</h1>
<p class=lede>同一个计算域、同一场雨，只差设施，算 L1 现状、L2 移动泵、L2+planned（PO 塘方案）、L2+suggested 四个态。
数据只用 Parsons 导出的 RB-01 模型和客户 5 m DTM。</p>
<div class=meta><span>计算域 {I1['domain_km2']:.2f} km²</span><span>二维 15 m 网格 · {n(TRI)} 个三角单元</span>
<span>一维 {n(I1['n_nodes'])} 口井 · {n(I1['n_conduits'])} 根管</span><span>模拟 {SIM}</span><span>{DATE}</span></div>""")
    # ---- 结论
    hs = v5["hs"]; hp = p["hs"]
    H.append(f"""<section><div class=sechead><h2>先说结论</h2></div>
<div class=heroGrid>
<div class="hcell d1"><span class=lab>全域 ≥0.30 m 积水</span><span class=big>{n(p['area_ge030_ha'], 1)} → {n(v5['area_ge030_ha'], 1)} ha</span><span class=sub>L2+planned → v5，{pct(p['area_ge030_ha'], v5['area_ge030_ha'])}</span></div>
<div class="hcell d2"><span class=lab>{SIM} 末域内积水</span><span class=big>{n(p['end_vol_m3'])} → {n(v5['end_vol_m3'])} m³</span><span class=sub>{pct(p['end_vol_m3'], v5['end_vol_m3'])}：退得更快</span></div>
<div class="hcell d24"><span class=lab>比 L2+planned 变深 ≥5 cm</span><span class=big>{n(v5['vs_L2P']['deeper_ge005_ha'], 2)} ha</span><span class=sub>变浅 {n(v5['vs_L2P']['shallower_ge005_ha'], 1)} ha</span></div></div>
<div class=card style="margin-top:14px">
<p><b>L2 几乎没有作用</b>：热点表 planA 的 9 台移动泵只是在同一张满了的管网里挪水，≥0.30 m 面积 {n(S['L1']['area_ge030_ha'], 1)} → {n(S['L2']['area_ge030_ha'], 1)} ha。</p>
<p><b>L2+planned 作用有限</b>：8 个塘里 5 个小塘在开始下雨后 0.8–3.6 h 内陆续满了；两个大塘 PO-2、PO-9 的塘面在计算域外，只在一维里收水。≥0.30 m 面积 {n(S['L1']['area_ge030_ha'], 1)} → {n(p['area_ge030_ha'], 1)} ha。</p>
<p><b>v5 的效果集中在热点</b>：200 m 内 ≥0.15 m 面积 × 时间，ADM-291 {hp['ADM-291']['ge015_ha_h']:.2f} → {hs['ADM-291']['ge015_ha_h']:.2f}、ADM-293 {hp['ADM-293']['ge015_ha_h']:.2f} → {hs['ADM-293']['ge015_ha_h']:.2f}、
ADM-101 {hp['ADM-101']['ge015_ha_h']:.2f} → {hs['ADM-101']['ge015_ha_h']:.2f}、ADM-206 {hp['ADM-206']['ge015_ha_h']:.2f} → {hs['ADM-206']['ge015_ha_h']:.2f} ha·h；ADM-140 只降了约三分之一（{hp['ADM-140']['ge015_ha_h']:.2f} → {hs['ADM-140']['ge015_ha_h']:.2f}）；ADM-205 在四个态里都不积水。</p>
<p><b>v5 的代价</b>：{V5['n_pumps']} 台 12 英寸泵（{V5['q_total_m3s']} m³/s）、{len(V5['ponds'])} 座新塘（合计 {n(sum(q['vol'] for q in V5['ponds']))} m³、挖方 {n(sum(q['cut_m3'] for q in V5['ponds']))} m³）、{len(V5['pipes'])} 根重力管；新塘多数在 OSM 未标注的地块，要现场 / 土地库确认。</p></div></section>""")
    # ---- 结果表
    rows = [[E(LAB[s]), n(S[s]['area_ge030_ha'], 2), n(S[s]['area_ge010_ha'], 1), n(S[s]['max_vol_m3']), n(S[s]['end_vol_m3']), n(S[s]['rpt']['flooding_m3']),
             n(S[s]['rpt']['outflow_m3']), n(S[s].get('vs_L2P', {}).get('deeper_ge005_ha'), 2)] for s in sts]
    H.append("<section><div class=sechead><h2>各态结果</h2></div>" + table(
        ["态", "≥0.30 m<span class=u>ha</span>", "≥0.10 m<span class=u>ha</span>", "最大积水量<span class=u>m³</span>", f"{SIM} 末积水<span class=u>m³</span>",
         "一维溢流<span class=u>m³</span>", "出水口出流<span class=u>m³</span>", "比 L2+planned 变深 ≥5 cm<span class=u>ha</span>"], rows, hl=sts.index("L2S5"))
        + "<p class=u style='margin-top:8px;color:var(--muted)'>积水面积按全程最大水深，不含塘（规划塘与 v5 新塘）占地。v4 是 v5 的中间步：v5 要按 v4 的运行结果决定哪些泵换成重力管。</p>")
    H.append("<div style='display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px'>"
             + "".join(f"<figure style='margin:0'>{img(figs / f'{s}_max.png', LAB[s])}<figcaption class=u style='color:var(--muted)'>{E(LAB[s])} · 全程最大水深</figcaption></figure>" for s in ("L1", "L2P", "L2S5"))
             + f"<figure style='margin:0'>{img(figs / 'diff_L2S5_L2P.png', 'v5 − L2+planned')}<figcaption class=u style='color:var(--muted)'>v5 − L2+planned（蓝 = 变浅；绿框 = 新塘）</figcaption></figure></div>")
    H.append(f"<figure style='margin:14px 0 0'>{img(shots / 'L2S5_diff.jpg', '系统截图')}<figcaption class=u style='color:var(--muted)'>系统里的 RB-01 视图（防线2+ 多态对比 → RB-01），v5 对 L2+planned 的差值，带设施符号</figcaption></figure></section>")
    # ---- 热点
    hrows = []
    for h in HS:
        hrows.append([E(h)] + [f"{S[s]['hs'][h]['ge015_ha_h']:.2f}" for s in sts] + [f"{S[s]['hs'][h]['hmax_m']:.2f}" for s in ("L2P", "L2S5")])
    H.append("<section><div class=sechead><h2>各热点（200 m 内）</h2></div>" + table(
        ["热点"] + [f"{E(s)}<span class=u>≥0.15 m ha·h</span>" for s in sts] + ["L2P 最深<span class=u>m</span>", "v5 最深<span class=u>m</span>"], hrows)
        + "<p class=u style='margin-top:8px;color:var(--muted)'>≥0.15 m 面积 × 时间 = 每 5 分钟一帧里水深 ≥0.15 m 的面积乘以时长再相加，同时反映积水范围和退水快慢。</p></section>")
    # ---- 四个态
    pp = IP["ponds"]
    H.append(f"""<section><div class=sechead><h2>四个态分别是什么</h2></div>
<div class="lvl a"><div class=tag><b>L1</b><span>现状</span></div><div class=body><h3>改造后模型去掉方案设施</h3>
<p>RB-01 导出是 Parsons 改造后的模型（"SHP files exported after enhancement"），没有改造前版本。去掉 8 个塘、28 台泵、61 段压力管、3 口湿井、28 个 O- 出水口，只留重力管网和 RBW3 出水口。</p>
<div class=inmodel>约 19 km 重力管（532 段，以 300 mm 为主）和客户 GDB 对不上，可能是 Parsons 新加的，也可能只是画法不同；没有改造前模型无法区分，留在 L1。客户 GDB 在 RB-01 内没有登记现状泵站和塘。</div></div></div>
<div class="lvl b"><div class=tag><b>L2</b><span>雨前移动泵</span></div><div class=body><h3>热点表 planA 的移动泵</h3>
<p>{len(I2['pumps'])} 台（6 英寸 50 L/s、12 英寸 194 L/s，取 config 泵型表），吸水井 = 热点最近的井，排到下游第一口在 L1 一维结果里不溢流的井。ADM-206 下游 2 km 管长内找不到这样的井，没布。</p></div></div>
<div class="lvl p1"><div class=tag><b>L2+P</b><span>PO 塘方案</span></div><div class=body><h3>模型自带的塘、泵、压力管（来源待确认）</h3>
<p>Parsons 结果汇总表里有 RB-01 页，但没有能直接对上的方案图，按「PO 塘方案（来源待确认）」称呼。泵流量取模型 PUMP_FLOW（为 0 的按 361.11 L/s），启停 = 吸水井深 50 % / 10 %；塘底 = 轮廓内 DTM 最低点、1.0 m 深。</p>
<div class=inmodel>塘：{'、'.join(f"{q['pond']} {n(q['vol_m3'])} m³" + ('（域外）' if q['no_surface'] else '') for q in pp)}<br>
RBW3 出口井 MH-32 上的 4 台泵：照规则 50 % 开泵水位低于 1.3 m 尾水，24 h 一维核对时反而让溢流比 L1 还多；改成水位过尾水才开（Claude 自定）。</div></div></div>
<div class="lvl p2"><div class=tag><b>L2+S</b><span>v5 建议方案</span></div><div class=body><h3>C1 v5 规则：先 v4，再把能自流的换成重力管 + 新塘</h3>
<p>v4：按 L2+planned 残余积水布 {len(V4['measures'])} 处地面取水泵（1.5 h 抽完，打进有余量的塘），每个热点 500 m 内溢流最多的井（覆盖 80 %、最多 4 口）装截流泵进塘，共 {V4['n_pumps']} 台。<br>
v5：v4 的泵全都出过力，没有去掉的；{len(V5['pipes'])} 口截流井在 500 m 管长内找到了比井口低 0.5 m 的空地，换成重力管 + 1.5 m 深新塘（容积 = 2 × 溢流量）；{sum(1 for c in V5['convs'] if c['v5']['how'] == 'pump')} 口找不到，保留泵。</p>
<div class=inmodel>新塘选址：不在建筑（外扩 3 m）、道路、水体里，至少 20 m 宽；土地用 OSM（2026-10-16 按 RB-01 范围下载）。残余积水范围取整个计算域（C1 是热点汇水区）——Claude 自定。</div></div></div></section>""")
    # ---- 措施清单
    prow = [[E(q['id']), n(q['area_m2']), n(q['vol']), f"{q['z_top']:.2f}", f"{q['z_floor']:.2f}", n(q['cut_m3']), E(q['land']), E(', '.join(q['wells']))] for q in V5["ponds"]]
    H.append("<section><div class=sechead><h2>v5 新塘清单</h2></div>" + table(["塘", "占地<span class=u>m²</span>", "容积<span class=u>m³</span>", "塘顶<span class=u>m</span>",
             "塘底<span class=u>m</span>", "挖方<span class=u>m³</span>", "土地", "接的井"], prow)
             + "<p class=u style='margin-top:8px;color:var(--muted)'>每个塘的满度、地表进塘量、泵的抽水量和运行占比，在系统里点设施符号能看到；GIS 文件在交付目录 L2+suggested_v5/设施GIS/。</p></section>")
    # ---- 水量对账
    brow = [[E(LAB[s])] + [n(S[s]['balance'][k]) for k in ("1to2", "2to1", "pond", "pump_pond", "boundary_out", "final_2d")] for s in sts]
    H.append("<section><div class=sechead><h2>水量对账（m³）</h2></div>" + table(["态", "1D→2D", "2D→1D", "地表进塘", "泵打进塘", "边界出流（余项）", f"{SIM} 末二维留存"], brow)
             + f"<p class=u style='margin-top:8px;color:var(--muted)'>一维侧：SWMM Flooding Loss 与交给二维的水差 −0.1 % 到 −0.8 %。产流 {n(I1['runoff_m3'])} m³ 全部经一维进管网。</p></section>")
    # ---- 设置
    srows = [["计算域", f"RB-01 子汇水区并集（11.13 km²）20 m 闭运算、填洞 → {I1['domain_km2']:.2f} km²；封闭汇水区，边界自由出流，域外地表来水不算"],
             ["一维", "RB-01 模型管网原值（管径、管底、长度、曼宁 n），井 = Manholes + Catch basins；出水口 RBW3 固定尾水 1.3 m、拍门"],
             ["产流", f"{n(I1['catch_n'])} 个子汇水区逐个 SCS CN + SCS 单位线（A_UNIFIED、SCS_CN、TC、OUT_NODE），面积加权 CN {I1['cn_aw']}，净雨 {I1['excess_mm_aw']} mm。重复的面是同一块拆给两口井，面积属性相加正好是整块，不去重"],
             ["雨", f"客户 Mussafah 模型「10 年一遇含 CC 1 h」雨型（21.2 mm）等比放大到 {RI['rain_mm']:g} mm"],
             ["时长", f"{RI['dur_h']:g} h 雨 + {RI['sim_h'] - RI['dur_h']:g} h = {SIM}"],
             ["二维", "ANUGA，15 m 非结构三角网（单元 ≤ 75 m²），地形取 5 m DTM 原值，曼宁 0.030"],
             ["耦合", "一维 30 s、交换 60 s；井盖 DN600、Cd 0.6；每 5 分钟存一帧水深与流速"],
             ["泵启停", "吸水井深 50 % 开、10 % 停（RBW3 出口井按尾水另定）；移动泵井中水深 > 1.0 m 开、< 0.3 m 停"],
             ["塘", "PO 塘：塘底 = 轮廓内 DTM 最低点、1.0 m、直立壁；v5 新塘 1.5 m；塘面在域外的只在一维收水"]]
    H.append("<section><div class=sechead><h2>计算设置</h2></div>" + table(["项", "取值"], srows).replace("<td class=n>", "<td style='text-align:left'>") + "</section>")
    # ---- 保留
    H.append("""<section><div class=sechead><h2>要一起看的保留</h2></div>
<div class="card flag"><h3>和 Parsons 汇总表对不上</h3><p>他们表里 24 h、61.34 mm 下溢流 247,483 → 207,497 m³（−16 %）。同一份模型我们只算一维：L1 225,486 m³，L2+planned 井溢流 195,156 + 塘满溢出 76,732 m³。建模方式不同（塘、出流边界、产流），这组数字不能直接比。</p></div>
<div class="card flag"><h3>L1 里有 19 km 来源不明的管</h3><p>没有改造前的模型，L1 是拆出来的；如果这些管是 Parsons 新加的，真实现状比 L1 更差。</p></div>
<div class="card flag"><h3>新塘的地要确认</h3><p>""" + f"{len(V5['ponds'])} 座里只有 {sum(1 for q in V5['ponds'] if q['land'] == '明确空地')} 座落在 OSM 明确的空地上，" + """其余在 OSM 未标注的地块（多半是空地，也可能是 OSM 漏画），要现场或内网土地库确认；地下水位也没核。</p></div>
<div class=card><h3>PO 塘方案的出处</h3><p>RB-01 模型里的 PO 塘和泵看起来像 Parsons 的临时方案，但没有方案图能直接对上，报告里一律写「来源待确认」。</p></div></section>""")
    H.append(f"<div class=foot>工具：tools/rb01_build.py、rb01_design_l2s.py、rb01_post.py、rb01_console.py、report_rb01.py · 交付目录 out/交付/{DD.name}/ · 系统：防线2+ 多态对比 → RB-01</div></div></body></html>")
    out = O / f"rb01_report_{a.tag.lower()}.html"; out.write_text("\n".join(H), encoding="utf-8")
    for dst in (ROOT / "console" / "data" / f"flood2d_rb01_{a.tag.lower()}" / "report.html", DD / "RB01_报告.html"):
        dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(out, dst)
    print(f"→ {out}（{out.stat().st_size / 1048576:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
