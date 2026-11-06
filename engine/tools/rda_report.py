# -*- coding: utf-8 -*-
"""RD-A 优化成果说明（2026-10-21 初版 v1；2026-10-24 改为 v3：三层约束 + 贪心 + 剪枝 + 地面泵站，v1 作废、收进「历史」）。

当前版本 = OPTCS2（L2+suggested v3 + 7 座地面泵站）。数据全部从计算结果读：
  out/l2opt/RDA_96C/   候选、合规表、贪心路径、剪枝、地面泵站（sumps_OPTCS2）、二维判据（eval2d_<雨>_ex.json，剔除 DTM 填 0 区）
  out/l2opt/RDA_96/    v1（OPT20F）的措施与判据（只作历史）
  c2d/*_2d.npz         二维最大水深
某场雨还没有 OPTCS2 的二维判据时，表里写「待补」。
产物：out/rda/RD-A_优化成果说明.html，同时拷到 console/data/flood2d_rda/report.html（系统 RD-A 视图里的链接）
用法：python tools/rda_report.py
"""
from __future__ import annotations

import base64
import collections
import datetime
import html
import io
import json
import shutil
import sys

import numpy as np

import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
OPT = ROOT / "out" / "l2opt" / "RDA_96C"
OPT1 = ROOT / "out" / "l2opt" / "RDA_96"
STORMS = ["S33D1", "S33D2", "S33D24", "S61D1", "S61D2", "S61D24", "S96D1", "S96D2", "S96D24"]
HS = ["ADM-75", "ADM-76", "ADM-80", "ADM-161", "ADM-376", "ADM-377", "ADM-378", "ADM-379", "ADM-380", "ADM-382", "ADM-383"]
CUR = "OPTCS2"
ST = ["L1", "L2", "L2P", CUR]
LAB = {"L1": "L1 现状", "L2": "L2", "L2P": "L2+planned", "OPTC": "v3（无地面泵）", CUR: "L2+suggested（v3 + 地面泵站）", "OPT20F": "v1（已作废）"}
KIND = {"tank_gravity": "重力进水地下调蓄池", "tank_pumped": "井边泵站 + 地下调蓄池", "ps_cluster": "泵站 → 新建排海口", "surface_ps": "地面泵站 → 新建排海口"}
CRIT = {"A": "热点 200 m 内 ≥0.30 m 积水面积 = 0", "B": "≥0.15 m 面积×时间 ≤ L2+planned 的 10 %", "C": "雨停后 6 h 内退到 0.10 m 以下", "D": "全域比 L2+planned 变深 ≥5 cm 的面积 ≤ 0.05 ha"}
e = html.escape


def png(fig):
    b = io.BytesIO(); fig.savefig(b, format="png", dpi=130, bbox_inches="tight", facecolor="white")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def sname(s):
    mm, h = s[1:].split("D"); return f"{mm} mm / {h} h"


def npass(E, st):
    return sum(all(v["pass"].values()) for v in E[st]["hotspots"].values()) if st in E else None


# ================= 三道防线联动（tools/rda_threelines.py → console/data/flood2d_rda_<雨>/l3.json，2026-10-25）
KZH = {"hospital": "医院", "ambulance": "急救站", "tunnel": "隧道", "metro": "地铁"}
kzh = lambda x: [x := x.replace(k, v) for k, v in KZH.items()][-1] if x else x
RES_COL = {"自然退干": "#16a34a", "L3 按时退干": "#2563eb", "L3 已派、超时": "#f59e0b", "L3 未派": "#dc2626", "只监测": "#94a3b8"}


def l3_docs():
    out = {}
    for s in STORMS:
        f = ROOT / "console" / "data" / f"flood2d_rda_{s.lower()}" / "l3.json"
        if f.exists():
            out[s] = json.loads(f.read_text(encoding="utf-8"))
    return out


def hs_verdict(EVs, D, h):
    """客户历史积水点在当前方案下的结论（与系统 RD-A 页「三道防线联动」② 同一口径，资源 = Island A13）。"""
    S = D["states"].get(CUR); X = (S or {}).get("dispatch", {}).get("island")
    if not S or not X or CUR not in EVs:
        return None, "—"
    if all(EVs[CUR]["hotspots"][h]["pass"].values()):
        return "ok", "已达标"
    ids = S["hs_links"].get(h, [])
    rs = [X["points"][i]["result"] for i in ids if i in X["points"]]
    if not ids:
        return "ok", "只在雨峰短时积水，雨后很快退掉"
    if all(r in ("自然退干", "只监测") for r in rs):
        return "ok", "雨后自然退干，L3 不需出动"
    if all(r in ("自然退干", "只监测", "L3 按时退干") for r in rs):
        return "ok", "L3 按时兜底"
    return "bad", "L3 兜不住 → 回流 L2"


def npv_section():
    """tools/rda_npv.py → out/rda/npv.json：造价分项 + 60 年 NPV（DEC-10，只进报告）"""
    f = ROOT / "out" / "rda" / "npv.json"
    if not f.exists():
        return "<p class='meta'>NPV 对比表待生成：python tools/rda_npv.py</p>"
    d = json.loads(f.read_text(encoding="utf-8")); D = d["basis"]["dec10"]; S = d["schemes"]; P = d["basis"]["parts"]
    r0 = str(D["discount_rate"]); rs = [str(x) for x in D["discount_sensitivity"]]
    pct = lambda x: f"{float(x) * 100:g} %"
    rows = "".join(f"<tr{' class=\"tot\"' if k == CUR else ''}><td>{e(v['label'])}</td><td class='num'>{v['npv'][r0]['capex'] / 1e6:.1f}</td>"
                   f"<td class='num'>{v['npv'][r0]['om_per_year'] / 1e6:.2f}</td><td class='num'>{v['npv'][r0]['energy_per_year'] / 1e3:.0f} k</td>"
                   f"<td class='num'>{v['npv'][r0]['pv_om'] / 1e6:.1f}</td><td class='num'>{v['npv'][r0]['pv_replace'] / 1e6:.1f}</td>"
                   + "".join(f"<td class='num'>{v['npv'][r]['npv'] / 1e6:.1f}</td>" for r in [rs[0], r0, rs[1]]) + "</tr>" for k, v in S.items())
    cur = S.get(CUR) or next(iter(S.values())); tot = sum(cur["parts"].values())
    om = D["annual_om_pct_of_capex"]; rep_y = D["replace_years"]
    OMK = {"tank": f"{pct(om['rc_structure'])} + 清淤 {pct(om['tank_cleaning'])}", "civil": pct(om["rc_structure"]), "mech": pct(om["pump_station_mech_elec"]),
           "fm": pct(om["pressure_pipe"]), "gpipe": pct(om["gravity_pipe"]), "outfall": pct(om["outfall"])}
    RPK = {"tank": rep_y["rc_structure"], "civil": rep_y["rc_structure"], "mech": rep_y["mech_elec"], "fm": rep_y["pipes"], "gpipe": rep_y["pipes"], "outfall": rep_y["rc_structure"]}
    prow = "".join(f"<tr><td>{e(P[k])}</td><td class='num'>{v / 1e6:.1f}</td><td class='num'>{v / tot * 100:.0f}</td><td class='num'>{OMK[k]}</td><td class='num'>{RPK[k]} 年</td></tr>"
                   for k, v in cur["parts"].items())
    return f"""<h3>全寿命成本（NPV，按 DEC-10 占位参数）</h3>
<div class="tbl"><table><tr><th>方案</th><th>建设费 M USD</th><th>每年运维 M USD</th><th>每年电费</th><th>运维现值 M</th><th>更换现值 M</th><th>NPV {pct(rs[0])}</th><th>NPV {pct(r0)}</th><th>NPV {pct(rs[1])}</th></tr>{rows}</table></div>
<div class="tbl"><table><tr><th>当前方案造价分项</th><th>M USD</th><th>%</th><th>年运维（占建设费）</th><th>更换周期</th></tr>{prow}</table></div>
<p class="meta">口径（DEC-10）：评估期 {D['period_years']} 年，折现率 {pct(r0)}（另算 {pct(rs[0])} / {pct(rs[1])}）；机电 {rep_y['mech_elec']} 年一换、土建与排海口 {rep_y['rc_structure']} 年一换、管道 {rep_y['pipes']} 年内不换，不计残值；
泵每年运行 {D['pump_run_hours_per_year']} h、电价 {D['electricity_usd_per_kwh']} USD/kWh、线到水效率 {D['pump_wire_to_water_eff']}，扬程统一按 {d['basis']['head_m']:g} m 估。运维比例、运行小时、电价、效率均为占位值，待客户给正式值；
NPV 只用于方案比较，优化和剪枝仍按建设费（用户 2026-10-23）。造价分项按候选生成时的同一套公式拆出，逐项与原造价核对一致。</p>"""


def tl_section(EV, plt):
    L3D = l3_docs()
    sts = [s for s in STORMS if s in L3D and CUR in L3D[s]["states"]]
    if not sts:
        return "<p class='meta'>还没有三道防线联动结果：运行 python tools/rda_threelines.py。</p>", ""
    HI = next(iter(L3D.values())).get("hotspots") or {}
    # ① 历史积水点 × 雨
    head = "".join(f"<th>{sname(s).replace(' mm / ', '/')}</th>" for s in sts)
    hrows = ""
    for h in HS:
        hi = HI.get(h, {}); pa = hi.get("planA") or {}
        tds = ""
        for s in sts:
            k, v = hs_verdict(EV[s], L3D[s], h)
            tds += f"<td class='c {k or ''}' style='font-family:var(--body);font-size:12.5px'>{e(v)}</td>"
        hrows += (f"<tr><td class='code'>{h}</td><td>{e(hi.get('criticality') or '')}</td>{tds}"
                  f"<td class='meta'>泵 {pa.get('pumps') if pa.get('pumps') is not None else '—'} · 罐车 {pa.get('tankers') or 0}</td></tr>")
    # ② 链条：每一级防线之后的残余积水与 L3 派单（Island / ADM）
    crow = ""
    for s in sts:
        D = L3D[s]
        for st in [x for x in ST if x in D["states"]]:
            S = D["states"][st]; sm = S["summary"]; lv = sm.get("levels") or {}
            def cnt(p):
                c = (S["dispatch"].get(p) or {}).get("counts") or {}
                return f"{c.get('L3 按时退干', 0)} / {c.get('L3 已派、超时', 0)} / {c.get('L3 未派', 0)}"
            I = S["dispatch"].get("island") or {}; A = S["dispatch"].get("adm") or {}
            ci = (I.get("counts") or {})
            crow += (f"<tr{' class=\"tot\"' if st == CUR else ''}><td>{sname(s)}</td><td>{e(LAB[st])}</td><td class='num'>{sm.get('n', 0)}</td>"
                     f"<td class='num'>{lv.get('关键', 0)} / {lv.get('高', 0)} / {lv.get('中', 0)} / {sm.get('n_noexpo', 0)}</td><td class='num'>{ci.get('自然退干', 0)}</td>"
                     f"<td class='num'>{cnt('island')}</td><td class='num'>{I.get('pumps', 0)} / {I.get('tankers', 0)}</td>"
                     f"<td class='num'>{cnt('adm')}</td><td class='num'>{A.get('pumps', 0)} / {A.get('tankers', 0)}</td>"
                     f"<td class='num' style='color:{'var(--bad)' if sm.get('n_beyond') else 'inherit'}'>{sm.get('n_beyond', 0)}</td></tr>")
    # ③ 回流 L2（当前方案）
    back = ""
    for s in sts:
        S = L3D[s]["states"][CUR]; X = S["dispatch"].get("island") or {}
        for p in S["points"]:
            r = (X.get("points") or {}).get(p["id"], {})
            if p.get("level") != "无暴露" and (str(p["verdict"]).startswith("超出") or (p.get("level") == "关键" and r.get("result") not in ("自然退干", "L3 按时退干"))):
                back += (f"<tr><td>{sname(s)}</td><td class='code'>{e(p['id'])}</td><td>{e(p.get('level', ''))}：{e(kzh(p.get('level_why', '')))}</td>"
                         f"<td class='num'>{p['vol_peak_m3']:,}</td><td class='num'>{p['max_depth_m']:.2f}</td>"
                         f"<td>{'超出 L3 单点能力' if str(p['verdict']).startswith('超出') else '关键点 L3 超时'}；L3：{e(r.get('result', '—'))}</td></tr>")
    # ④ 图：96 mm / 1 h（没有就用第一场）的 L2+planned 与当前方案
    s0 = "S96D1" if "S96D1" in sts else sts[0]
    fig_html = ""
    try:
        import geopandas as gpd
        dom = gpd.read_file(ROOT / "out/dataqc/RD-A/input/rda_domain.geojson").to_crs(4326)
        H = gpd.read_file(ROOT / "console/data/hotspots.geojson").to_crs(4326); H = H[H.hotspot_id.isin(HS)]
        fig, axs = plt.subplots(1, 2, figsize=(15, 5.2))
        for ax, st in zip(axs, ["L2P", CUR]):
            S = L3D[s0]["states"].get(st)
            dom.boundary.plot(ax=ax, color="#333", lw=1, ls="--")
            if S:
                X = S["dispatch"].get("island") or {}
                seen = set()
                for l in X.get("lines", []):
                    k = (l["from"], l["fp"])
                    if k in seen:
                        continue
                    seen.add(k); ax.plot([l["a"][0], l["b"][0]], [l["a"][1], l["b"][1]], color="#64748b", lw=.6, ls="--", alpha=.6, zorder=2)
                vmax = max([p["vol_peak_m3"] for p in S["points"]] + [1])
                for res_, col in RES_COL.items():
                    q = [p for p in S["points"] if (X.get("points") or {}).get(p["id"], {}).get("result") == res_]
                    if q:
                        ax.scatter([p["lon"] for p in q], [p["lat"] for p in q], s=[12 + 300 * (p["vol_peak_m3"] / vmax) ** .5 for p in q], c=col,
                                   edgecolors=["#7f1d1d" if str(p["verdict"]).startswith("超出") else "k" for p in q],
                                   linewidths=[2.2 if str(p["verdict"]).startswith("超出") else .4 for p in q], alpha=.85, zorder=4, label=f"{res_}（{len(q)}）")
            H.plot(ax=ax, color="red", marker="*", markersize=60, zorder=5)
            for r in H.itertuples():
                ax.annotate(r.hotspot_id, (r.geometry.x, r.geometry.y), xytext=(3, 3), textcoords="offset points", fontsize=6.5, color="#b91c1c")
            b = dom.total_bounds; ax.set_xlim(b[0] - .002, b[2] + .002); ax.set_ylim(b[1] - .002, b[3] + .002)
            ax.set_aspect(1 / np.cos(np.radians(24.46))); ax.set_xticks([]); ax.set_yticks([]); ax.set_xlabel(""); ax.set_ylabel(""); ax.set_title(f"{sname(s0)} · {LAB[st]}", fontsize=11)
            from matplotlib.lines import Line2D
            X = (S or {}).get("dispatch", {}).get("island") or {}
            cn = collections.Counter((X.get("points") or {}).get(p["id"], {}).get("result") for p in (S or {}).get("points", []))
            hd = [Line2D([], [], marker="o", ls="", color=c, markeredgecolor="k", markeredgewidth=.4, ms=8, label=f"{k}（{cn[k]}）") for k, c in RES_COL.items() if cn.get(k)]
            hd.append(Line2D([], [], marker="o", ls="", color="white", markeredgecolor="#7f1d1d", markeredgewidth=2.2, ms=8, label=f"粗红边 = 超出 L3（{sum(str(p['verdict']).startswith('超出') for p in (S or {}).get('points', []))}）"))
            ax.legend(handles=hd, fontsize=7.5, loc="upper center", bbox_to_anchor=(0.5, -0.01), ncol=3, frameon=False)
        fig_html = (f"<figure><img src='{png(fig)}' alt='L3 派单结果'><figcaption>残余积水点按 L3 处置结果着色（资源 = Island 驻地 A13）；圆大小 = 峰值积水量；粗红边 = 超出 L3 单点能力；"
                    f"虚线 = 驻地派车；红星 = 客户历史积水点。驻地 A13 在图框外东侧。</figcaption></figure>")
        plt.close(fig)
    except Exception as ex:                                   # 出图失败不影响报告
        fig_html = f"<p class='meta'>（派单图未生成：{e(str(ex)[:120])}）</p>"
    html_ = f"""
<p><b>口径：</b>L3 的输入就是这一级防线之后二维模型里仍然积水的地方（≥0.15 m、≥100 m²、≥0.5 h），不是另一套点。谁先处置看后果、不看出身：碰到医院 / 急救 / 隧道出入、输配电变电站、道路、建筑的，按「关键 / 高 / 中」排；只在空地、未建地块、公园里的只监测、不派 L3。客户历史积水点单列、保留客户编号，客户定的重要度只会抬高等级。派单用系统第三道防线的调度求解器（滚动 MILP、路网行程、退水重演）和客户预案 A 的车队与人员；资源分「仅 Island 驻地 A13（现状）」和「ADM 全部驻地集中调度（上限）」。按时 = 雨停后在处置时限内退干。</p>
<h3>客户历史积水点：当前方案下的结论（资源 = Island A13）</h3>
<div class="tbl"><table><tr><th>热点</th><th>客户重要度</th>{head}<th>预案 A 预置</th></tr>{hrows}</table></div>
<h3>每一级防线之后的残余积水与 L3 派单</h3>
<div class="tbl"><table><tr><th>暴雨</th><th>防线</th><th>残余点</th><th>关键 / 高 / 中 / 无暴露</th><th>自然退干</th><th>Island：按时 / 超时 / 未派</th><th>Island：泵 / 罐车</th><th>ADM 全部：按时 / 超时 / 未派</th><th>ADM 全部：泵 / 罐车</th><th>超出 L3</th></tr>{crow}</table></div>
<h3>回流给 L2 的点（当前方案）：L2 下一轮的优化对象</h3>
<div class="tbl"><table><tr><th>暴雨</th><th>积水点</th><th>后果</th><th>m³</th><th>m</th><th>原因</th></tr>{back or "<tr><td colspan='6'>无</td></tr>"}</table></div>
{fig_html}
<p class="meta">后果等级（我们的建议，待客户确认）：关键 = 医院 / 急救 / 隧道 / 地铁 300 m 内且路面或建筑处 ≥0.30 m，或淹到输电变电站；高 = ≥0.30 m 路面 ≥200 m²、≥0.30 m 碰到建筑或配电变电站；中 = ≥0.15 m 路面 ≥100 m² 或碰到建筑。车辆、软管、燃油、单价为模拟值；调度求解器不释放已派出的班组（原有简化），人员瓶颈可能偏高；ADM 全部驻地没扣其他片区同时要用的资源，是上限。系统里：防线2+ 多态对比 → RD-A →「三道防线联动」。</p>"""
    nb = sum(L3D[s]["states"][CUR]["summary"].get("n_beyond", 0) for s in sts)
    nbad = sum(1 for s in sts for h in HS if hs_verdict(EV[s], L3D[s], h)[0] == "bad")
    note = (f"<p><b>三道防线联动：</b>已算 {len(sts)} 场雨。当前方案下，客户 11 个历史积水点里，需要回流 L2 的 {'有 ' + str(nbad) + ' 处次' if nbad else '没有'}"
            f"（其余已达标、短时积水很快退掉、自然退干，或由 L3 按时兜底）；超出 L3 单点能力、要 L2 继续解决的模型积水点共 {nb} 处次，都在客户热点之外。详见第三节。</p>")
    return html_, note


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from pyproj import Transformer
    from shapely.geometry import shape
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]; plt.rcParams["axes.unicode_minus"] = False
    T = Transformer.from_crs(32640, 4326, always_xy=True)
    D = json.loads((OPT / "candidates.json").read_text(encoding="utf-8"))
    CAND = {c["id"]: c for c in D["candidates"]}
    COMP = json.loads((OPT / "compliance.json").read_text(encoding="utf-8"))
    GEO = {f["properties"]["id"]: f["geometry"] for f in json.loads((OPT / "candidates.geojson").read_text(encoding="utf-8"))["features"]}
    SUM = json.loads((OPT / "summary.json").read_text(encoding="utf-8"))
    PR = json.loads((OPT / "prune.json").read_text(encoding="utf-8"))
    G = json.loads((OPT / "greedy_path.json").read_text(encoding="utf-8"))
    V = json.loads((OPT / "variant_S96D1_OPTC.json").read_text(encoding="utf-8"))
    SU = json.loads((OPT / "sumps_OPTCS2.json").read_text(encoding="utf-8"))
    EV = {}
    for s in STORMS:
        f = OPT / f"eval2d_{s}_ex.json"
        EV[s] = json.loads(f.read_text(encoding="utf-8"))["states"] if f.exists() else {}
    done = [s for s in STORMS if CUR in EV[s]]

    # ---- 造价
    by = collections.defaultdict(lambda: {"n": 0, "pumps": 0, "q": 0.0, "vol": 0, "cost": 0.0})
    for m in V["measures"]:
        c = CAND[m["id"]]; b = by[m["kind"]]; b["n"] += 1; b["pumps"] += c.get("n_pumps") or 0; b["q"] += c.get("q_m3s") or 0
        b["vol"] += c.get("pond_vol_m3") or 0; b["cost"] += m["cost_usd"]
    for x in SU["stations"]:
        b = by["surface_ps"]; b["n"] += 1; b["pumps"] += x["n_pumps"]; b["q"] += x["q_m3s"]; b["cost"] += x["cost_usd"]
    tot = sum(b["cost"] for b in by.values())
    outfalls = {CAND[m["id"]].get("outfall_site") for m in V["measures"] if CAND[m["id"]].get("outfall_site")} | {x["outfall_site"] for x in SU["stations"]}
    order = ["tank_gravity", "tank_pumped", "ps_cluster", "surface_ps"]
    sumtab = "".join(f"<tr><td>{KIND[k]}</td><td class='num'>{by[k]['n']}</td><td class='num'>{by[k]['pumps'] or '—'}</td><td class='num'>{by[k]['q']:.1f}</td>"
                     f"<td class='num'>{by[k]['vol']:,.0f}</td><td class='num'>{by[k]['cost'] / 1e6:.1f}</td><td class='num'>{by[k]['cost'] / tot * 100:.0f}</td></tr>" for k in order if k in by)
    sumtab += (f"<tr class='tot'><td>合计</td><td class='num'>{sum(b['n'] for b in by.values())}</td><td class='num'>{sum(b['pumps'] for b in by.values())}</td>"
               f"<td class='num'>{sum(b['q'] for b in by.values()):.1f}</td><td class='num'>{sum(b['vol'] for b in by.values()):,.0f}</td><td class='num'>{tot / 1e6:.1f}</td><td class='num'>100</td></tr>")

    # ---- 待办（合规表里的 review / unknown）汇总
    todo = collections.Counter()
    for m in V["measures"]:
        for i in COMP.get(m["id"], []):
            if i["result"] in ("review", "unknown"):
                todo[(i["zh"], i["result"])] += 1
    for x in SU["stations"]:
        for i in SU["compliance"].get(x["id"], []):
            if i["result"] in ("review", "unknown"):
                todo[(i["zh"], i["result"])] += 1
    todotab = "".join(f"<tr><td>{e(k[0])}</td><td>{'需审批 / 许可' if k[1] == 'review' else '缺数据，判不了'}</td><td class='num'>{n}</td></tr>"
                      for k, n in sorted(todo.items(), key=lambda kv: -kv[1]))

    # ---- 逐项清单
    lst = ""
    for m in sorted(V["measures"], key=lambda m: (m["hotspot"], m["kind"])):
        c = CAND[m["id"]]
        if m["kind"] == "ps_cluster":
            size = f"{c['n_pumps']} 台 · {c['q_m3s']:.2f} m³/s；截流井 {len(c.get('wells') or [])} 口"
            dest = f"{c.get('outfall_site')}（压力管 {c.get('pipe_m')} m DN{int(c['main_d_m'] * 1000)}×{c['n_mains']}）"
        elif m["kind"] == "tank_pumped":
            size = f"{c['n_pumps']} 台 · {c['q_m3s']:.2f} m³/s；池 {c['pond_vol_m3']:,} m³"
            dest = f"地下池（压力管 {c.get('pipe_m')} m）"
        else:
            size = f"池 {c['pond_vol_m3']:,} m³，深 {c.get('pond_depth_m')} m，开挖 {c.get('excavation_m')} m"
            dest = f"重力管 {c.get('pipe_m')} m"
        land = "、".join(f"{k} {v:.0%}" for k, v in (c.get("land") or {}).items() if v) or "—"
        lst += (f"<tr><td class='code'>{m['id']}</td><td>{e(m['hotspot'])}</td><td>{KIND[m['kind']]}</td><td>{e(size)}</td><td>{e(dest)}</td>"
                f"<td>{e(land)}</td><td class='num'>{m['cost_usd'] / 1e6:.2f}</td></tr>")
    for x in SU["stations"]:
        lst += (f"<tr><td class='code'>{x['id']}</td><td>{e(x['hotspot'])}</td><td>{KIND['surface_ps']}</td>"
                f"<td>{x['n_pumps']} 台 · {x['q_m3s']:.2f} m³/s；取水口 {len(x['intakes'])} 个（{e('、'.join(sorted(x['classes'])))}）</td>"
                f"<td>{e(x['outfall_site'])}（压力管 {x['pipe_m']} m DN{int(x['main_d_m'] * 1000)}）</td><td>—</td><td class='num'>{x['cost_usd'] / 1e6:.2f}</td></tr>")

    # ---- 措施地图
    dom = gpd.read_file(ROOT / "out/dataqc/RD-A/input/rda_domain.geojson").to_crs(4326)
    H = gpd.read_file(ROOT / "console/data/hotspots.geojson").to_crs(4326); H = H[H.hotspot_id.isin(HS)]
    fig, ax = plt.subplots(figsize=(11, 6.6))
    dom.boundary.plot(ax=ax, color="#333", lw=1, ls="--")
    for m in V["measures"]:
        c = CAND[m["id"]]; g = shape(GEO[m["id"]]) if m["id"] in GEO else None
        if m["kind"] in ("tank_gravity", "tank_pumped") and g is not None:
            gpd.GeoSeries([g], crs=4326).plot(ax=ax, color="#0f766e", alpha=.55, edgecolor="#0f766e", lw=.6)
        if m["kind"] in ("ps_cluster", "tank_pumped"):
            lo, la = T.transform(c["ps_x"], c["ps_y"]); ax.plot(lo, la, marker="D", color="#7c3aed", ms=5.5, mec="white")
            if c.get("of_xy"):
                ox, oy = T.transform(*c["of_xy"]); ax.plot([lo, ox], [la, oy], color="#7c3aed", lw=.7, alpha=.6); ax.plot(ox, oy, marker="s", color="#111", ms=5)
    for x in SU["stations"]:
        lo, la = T.transform(x["ps_x"], x["ps_y"]); ax.plot(lo, la, marker="o", color="#db2777", ms=7, mec="white")
        ox, oy = T.transform(*x["of_xy"]); ax.plot([lo, ox], [la, oy], color="#db2777", lw=.7, alpha=.6, ls="--"); ax.plot(ox, oy, marker="s", color="#111", ms=5)
    H.plot(ax=ax, color="red", markersize=40, marker="*", zorder=5)
    for r in H.itertuples():
        ax.annotate(r.hotspot_id, (r.geometry.x, r.geometry.y), xytext=(4, 3), textcoords="offset points", fontsize=7.5, color="#b91c1c")
    ax.plot([], [], "s", color="#0f766e", alpha=.6, label=f"地下调蓄池（{by['tank_gravity']['n'] + by['tank_pumped']['n']} 座）")
    ax.plot([], [], "D", color="#7c3aed", label=f"泵站（{by['ps_cluster']['n'] + by['tank_pumped']['n']} 座）")
    ax.plot([], [], "o", color="#db2777", label=f"地面泵站（{by['surface_ps']['n']} 座）")
    ax.plot([], [], "s", color="#111", label=f"新建排海口（{len(outfalls)} 处岸点）")
    ax.legend(loc="lower left", fontsize=8); ax.set_xticks([]); ax.set_yticks([]); ax.set_aspect(1 / np.cos(np.radians(24.47)))
    mmap = png(fig); plt.close(fig)

    # ---- 水深对比图
    def dmap(storm):
        fig, axs = plt.subplots(1, 2, figsize=(11, 3.6))
        Hu = H.to_crs(32640)
        for ax, st in zip(axs, ("L2P", CUR)):
            z = np.load(C2D / f"RDA_{storm}_{st}_2d.npz"); xy = z["xy"]; hm = z["hmax"]; m = hm >= 0.10
            sc = ax.scatter(xy[m, 0], xy[m, 1], c=np.clip(hm[m], 0, 1), s=1.6, cmap="Blues", vmin=0, vmax=1, marker="s")
            ax.set_title(f"{LAB[st]} · ≥0.30 m {EV[storm][st]['domain']['ge030_ha']} ha", fontsize=10)
            ax.set_xticks([]); ax.set_yticks([]); ax.set_aspect("equal")
            Hu.plot(ax=ax, color="red", markersize=18, marker="*")
        fig.colorbar(sc, ax=axs, fraction=.02, label="全程最大水深 m（≥0.10 m 才画，≥1 m 同色）")
        fig.suptitle(sname(storm), fontsize=11)
        return png(fig)
    figs = "".join(f"<figure><img src='{dmap(s)}' alt='{sname(s)} 最大水深对比'><figcaption>{sname(s)}：L2+planned 与当前方案的全程最大水深。</figcaption></figure>"
                   for s in ("S96D1", "S96D24") if s in done)
    plt.close("all")

    # ---- 造价—效果曲线（一维贪心路径 + 已算的二维点）
    P = G["path"]
    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    xs = [p.get("cost_usd", 0) / 1e6 for p in P]; ys = [p["J"] for p in P]
    ax.plot(xs, ys, "-o", color="#0f5f80", ms=3.5, label="一维 J（左轴）")
    ax.plot([PR["cost_final"] / 1e6], [PR["J_final"]], "*", color="#0f5f80", ms=12, label="剪枝后（v3）")
    ax.set_xlabel("累计占位造价（百万美元）"); ax.set_ylabel("J（L2+planned = 22）"); ax.grid(alpha=.3); ax.set_ylim(0, 23)
    pts = []
    for p in P:
        st = f"G{p['step']}"
        a = [npass(EV[s], st) for s in ("S96D1", "S96D24") if st in EV[s]]
        if len(a) == 2:
            pts.append((p["cost_usd"] / 1e6, a[0], a[1]))
    if pts:
        ax2 = ax.twinx(); ax2.plot([q[0] for q in pts], [q[2] for q in pts], "-s", color="#d97706", ms=4, label="二维达标热点：96 mm / 24 h（右轴）")
        ax2.plot([q[0] for q in pts], [q[1] for q in pts], "-^", color="#b45309", ms=4, label="二维达标热点：96 mm / 1 h（右轴）"); ax2.set_ylim(0, 11.5); ax2.set_ylabel("达标热点（/11）")
        h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels(); ax.legend(h1 + h2, l1 + l2, fontsize=7.5, loc="upper right")
    else:
        ax.legend(fontsize=8)
    curve = png(fig); plt.close(fig)
    curve_note = "二维点（各轮方案在 96 mm 两场雨下的达标热点数）已画上。" if pts else "各轮方案的二维验证还在计算，完成后补上二维点。"

    # ---- 9 场雨表
    rows9 = ""
    for s in STORMS:
        E = EV[s]
        if CUR not in E:
            rows9 += f"<tr><td>{sname(s)}</td><td colspan='9' class='meta'>待补：这场雨的当前方案还在计算</td></tr>"
            continue
        cells = "".join(f"<td class='num'>{E[st]['domain']['ge030_ha']:.2f}</td>" for st in ST)
        pas = "".join(f"<td class='num {'ok' if npass(E, st) == 11 else ''}'>{npass(E, st)}/11</td>" for st in ST)
        rows9 += f"<tr><td>{sname(s)}</td>{cells}{pas}<td class='num'>{E[CUR]['domain']['deeper_ge005_ha']:.2f}</td></tr>"
    hsrow = "".join(f"<th>{sname(s).replace(' mm / ', '/')}</th>" for s in done)
    mat = ""
    for h in HS:
        tds = ""
        for s in done:
            p = EV[s][CUR]["hotspots"][h]["pass"]; bad = "".join(k for k, v in p.items() if not v)
            tds += f"<td class='c {'ok' if not bad else 'bad'}'>{'✓' if not bad else e(bad)}</td>"
        mat += f"<tr><td>{h}</td>{tds}</tr>"
    p1 = {s: npass(EV[s], CUR) for s in done}
    head_cards = "".join(f"<div class='card'><span>{sname(s)}</span><b>{p1[s]} / 11</b><span>L2+planned {npass(EV[s], 'L2P')} / 11</span></div>" for s in done)

    # ---- 路径表、剪枝
    path_rows = "".join(f"<tr><td class='num'>{p['step']}</td><td class='num'>{len(p.get('chosen', []))}</td><td class='num'>{p.get('cost_usd', 0) / 1e6:.1f}</td><td class='num'>{p['J']:.2f}</td></tr>"
                        for p in P if p["step"] in (0, 2, 4, 6, 8, 11, 14, 20))
    # ---- v1 历史
    v1rows = ""
    for s in STORMS:
        f = OPT1 / f"eval2d_{s}.json"
        if f.exists():
            E1 = json.loads(f.read_text(encoding="utf-8"))["states"]
            if "OPT20F" in E1:
                v1rows += f"<tr><td>{sname(s)}</td><td class='num'>{npass(E1, 'OPT20F')}/11</td><td class='num'>{E1['OPT20F']['domain']['ge030_ha']:.2f}</td></tr>"
    rej = SUM.get("rejected", {})
    tlsec, tlnote = tl_section(EV, plt)
    page = TEMPLATE.format(tlsec=tlsec, tlnote=tlnote, npvsec=npv_section(),
        date=datetime.date.today().isoformat(), tot=tot / 1e6, nmeas=sum(b["n"] for b in by.values()), nof=len(outfalls), head_cards=head_cards,
        ndone=len(done), rows9=rows9, mat=mat, hsrow=hsrow, figs=figs, mmap=mmap, sumtab=sumtab, todotab=todotab, lst=lst, curve=curve, curve_note=curve_note,
        path_rows=path_rows, crit="".join(f"<li><b>{k}</b>：{v}</li>" for k, v in CRIT.items()),
        ncand=f"{len(D['candidates']):,}", nrej=f"{sum(rej.values()):,}", nwells=SUM.get("wells", "—"),
        pr_from=PR["n_ref"], pr_to=PR["n_final"], pr_c0=PR["cost_ref"] / 1e6, pr_c1=PR["cost_final"] / 1e6, pr_j0=PR["J_ref"], pr_j1=PR["J_final"],
        v1rows=v1rows or "<tr><td colspan='3'>—</td></tr>",
        ex_note="（判据统计剔除了 ADM-80 岸边约 9.7 ha 的 DTM 填 0 区和私人地块深坑，各态同一口径）")
    out = ROOT / "out" / "rda" / "RD-A_优化成果说明.html"
    out.write_text(page, encoding="utf-8")
    dst = ROOT / "console" / "data" / "flood2d_rda" / "report.html"
    shutil.copyfile(out, dst)
    print(f"→ {out}（{out.stat().st_size / 1e6:.1f} MB），并拷到 {dst}；已有当前方案结果的雨：{', '.join(done)}")
    return 0


TEMPLATE = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>RD-A 优化成果说明</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Serif+SC:wght@600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* 一栏报告：先结论，再方法与明细；表格各自横向滚动；历史版本收在最后 */
:root{{--bg:#f5f7f8;--paper:#fff;--fg:#16232b;--muted:#5a6a73;--line:#d7dfe3;--accent:#0f5f80;--ok:#1e6b3a;--okbg:#e3f2e8;--bad:#a52a1e;--badbg:#fbe9e7;
--display:"Noto Serif SC","Songti SC",serif;--body:"Noto Sans SC","Microsoft YaHei",sans-serif;--mono:"IBM Plex Mono",Consolas,monospace}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#0f171c;--paper:#16222a;--fg:#e3eaee;--muted:#9aabb5;--line:#2b3b45;--accent:#6cc3e6;--ok:#7fd49b;--okbg:#16301f;--bad:#ff8a80;--badbg:#3a1c1a;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#0f171c;--paper:#16222a;--fg:#e3eaee;--muted:#9aabb5;--line:#2b3b45;--accent:#6cc3e6;--ok:#7fd49b;--okbg:#16301f;--bad:#ff8a80;--badbg:#3a1c1a;color-scheme:dark}}
body{{margin:0;background:var(--bg);color:var(--fg);font-family:var(--body);font-size:15px;line-height:1.7}}
.wrap{{max-width:1100px;margin:0 auto;padding-block:32px 64px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:grid;gap:26px}}
h1,h2{{font-family:var(--display);margin:0;text-wrap:balance}} h1{{font-size:30px}} h2{{font-size:21px;border-top:2px solid var(--fg);padding-top:6px}} h3{{font-size:16px;margin:0}}
p{{margin:0;max-width:75ch}} ul,ol{{margin:0;padding-left:20px}}
.meta{{color:var(--muted);font-size:13px}} .eyebrow{{font-family:var(--mono);font-size:12px;letter-spacing:.08em;color:var(--muted)}}
section{{display:grid;gap:12px;min-width:0}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}}
.card{{background:var(--paper);border:1px solid var(--line);padding:12px 14px;display:grid;gap:2px}} .card b{{font-size:24px;font-variant-numeric:tabular-nums}} .card span{{color:var(--muted);font-size:13px}}
.tbl{{overflow-x:auto;background:var(--paper);border:1px solid var(--line)}} table{{border-collapse:collapse;width:100%;font-size:13.5px}}
th,td{{padding:6px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{font-weight:500;color:var(--muted);font-size:12.5px;background:var(--bg);white-space:nowrap}}
.num{{text-align:right;font-variant-numeric:tabular-nums;font-family:var(--mono);white-space:nowrap}} .code{{font-family:var(--mono);white-space:nowrap}}
td.ok,.c.ok{{background:var(--okbg);color:var(--ok)}} .c.bad{{background:var(--badbg);color:var(--bad);font-family:var(--mono)}} .c{{text-align:center}}
tr.tot td{{font-weight:700}}
figure{{margin:0;background:var(--paper);border:1px solid var(--line);padding:10px;display:grid;gap:6px}} figure img{{width:100%;height:auto;display:block}} figcaption{{font-size:12.5px;color:var(--muted)}}
.note{{background:var(--paper);border-left:4px solid var(--accent);padding:10px 14px;display:grid;gap:6px}}
details summary{{cursor:pointer;color:var(--accent)}}
</style></head><body><div class="wrap">
<header style="display:grid;gap:6px"><div class="eyebrow">RD-A 汇水区 · L2+suggested · 当前版本 v3 + 地面泵站 · {date}</div><h1>RD-A 优化成果说明</h1>
<p class="meta">一二维耦合（SWMM 5.2 + ANUGA，15 m 网格）。所有候选措施都先过三层约束：WA-726 手册条文、领域知识、Makani 空间数据。造价是占位单价（压力管、排海口用 WA-726 第三卷单价），只用于比较方案，不是预算。模型未率定：方案之间的相对差别可信，积水的绝对深度和范围待率定。所有数字由 tools/rda_report.py 从计算结果直接读出。</p></header>

<section><h2>结论</h2>
<div class="cards">
<div class="card"><span>当前方案（L2+suggested）</span><b>{tot:.1f} M USD</b><span>{nmeas} 项措施，新建排海口 {nof} 处岸点（占位价）</span></div>
{head_cards}
</div>
<div class="note">
<p><b>中间说明：</b>9 场雨中已有 {ndone} 场完成当前方案的二维复核，其余标「待补」。</p>
<p><b>目前没过的：</b>96 mm / 1 h 下，ADM-377～380 共用的洼坑和 ADM-76 隧道引道在雨峰时积水超过 0.30 m（判据 A），1 小时 96 mm 泵抽不过来；ADM-161 雨后有一处退不到 0.10 m，它的判据 B 基线接近 0、本身偏严。</p>
{tlnote}
<p><b>和作废的 v1 比：</b>v1 在 96 mm / 1 h 下有 5 / 11，但它把所有水泵到一个出水口、没按手册和空间约束，不可建。可建性约束在 1 小时极端雨下牺牲了一部分效果，这是实际约束的代价。</p>
</div>
</section>

<section><h2>一、9 场雨的验证</h2>
<p>判据（对照 L2+planned）{ex_note}：</p><ul>{crit}</ul>
<div class="tbl"><table><tr><th>暴雨</th><th>L1</th><th>L2</th><th>L2+planned</th><th>当前方案</th><th>L1</th><th>L2</th><th>L2+planned</th><th>当前方案</th><th>当前方案变深（ha）</th></tr>
<tr><th></th><th colspan="4">全域 ≥0.30 m 积水面积（ha）</th><th colspan="4">热点四项全过</th><th></th></tr>{rows9}</table></div>
<h3>当前方案逐热点结果（✓ = 四项全过；字母 = 没过的判据）</h3>
<div class="tbl"><table><tr><th>热点</th>{hsrow}</tr>{mat}</table></div>
{figs}
</section>

<section><h2>二、方案的措施与造价</h2>
<figure><img src="{mmap}" alt="当前方案措施分布"><figcaption>青 = 地下调蓄池占地；紫菱 = 泵站；粉圆 = 地面泵站；黑方 = 新建排海口岸点；连线为示意直线（压力管长度按道路最短路径计算）；红星 = 客户热点。</figcaption></figure>
<div class="tbl"><table><tr><th>类型</th><th>座</th><th>泵（台）</th><th>泵量（m³/s）</th><th>池容（m³）</th><th>占位造价（M USD）</th><th>%</th></tr>{sumtab}</table></div>
<p class="meta">单价来源：压力管、排海口按 WA-726 第三卷附录 L（p349）；池体 350 USD/m³、泵站、湿井、重力管、取水井为占位单价。同一岸点的排口在造价里分别计算，实际可合建。</p>
{npvsec}
<h3>还要办的事（各措施合规表里「需审批 / 许可」和「缺数据」的项，按出现次数）</h3>
<div class="tbl"><table><tr><th>事项</th><th>类别</th><th class="num">涉及措施</th></tr>{todotab}</table></div>
<details><summary>逐项清单（{nmeas} 项）</summary><div class="tbl"><table><tr><th>编号</th><th>热点</th><th>类型</th><th>规模</th><th>去向</th><th>用地（Makani）</th><th>M USD</th></tr>{lst}</table></div></details>
</section>

<section><h2>三、三道防线联动：剩下的积水交给 L3</h2>
{tlsec}
</section>

<section><h2>四、方法</h2>
<ol>
<li><b>三层约束生成候选：</b>A 层 WA-726 约束登记表 821 条 + 10 条取值决定；B 层 15 组领域规则（重力进水、作业带、道路红线、供电、泵站场地、排口岸段等）；C 层 Makani 15 个空间图层 + DTM + 基线积水。热点 800 m 内 {nwells} 口溢流井，共生成 {ncand} 个可建或有条件可建的候选，另有 {nrej} 个不可建被剔除。</li>
<li><b>一维筛选与贪心：</b>控制雨 96 mm / 1 h、96 mm / 24 h；11 个热点分 6 组，逐轮挑「每美元降 J 最多」的措施，共 20 轮。J = 各热点 500 m 内检查井溢流量与 L2+planned 之比的总和（基线 22）。</li>
<li><b>剪枝和替换：</b>{pr_from} → {pr_to} 项，{pr_c0:.1f} → {pr_c1:.1f} M USD，J {pr_j0:.2f} → {pr_j1:.2f}（允许变差 1 %）。主要是把单井泵站换成就近的重力进水地下池。</li>
<li><b>二维验证与地面泵站：</b>二维里热点附近的封闭洼坑管网收不进去，配 7 座地面泵站（按小泵站过同样的可建性检查），流量按雨峰时的涨水速度定。</li>
<li><b>9 场雨复核：</b>33 / 61 / 96 mm × 1 / 2 / 24 h，对比 L1、L2、L2+planned。</li>
</ol>
<figure><img src="{curve}" alt="造价—效果曲线"><figcaption>贪心路径的造价—效果曲线。{curve_note}</figcaption></figure>
<div class="tbl"><table><tr><th>轮</th><th>措施数</th><th>累计 M USD</th><th>J</th></tr>{path_rows}</table></div>
<p class="meta">贪心加剪枝是在「评估很贵、约束很多、要能解释」下的现实选择，结果是较优而不是严格最优；依据与局限见 docs/L2S优化方案与咨询公司做法对比_20261024.md 第七节。</p>
</section>

<section><h2>五、模型与参数</h2>
<ul>
<li><b>一维：</b>客户 SewerGEMS RD-A 模型（inp 51079），英制换算为公制。2,157 口井、2,160 根管，含现有 RD-A 泵站。</li>
<li><b>糙率：</b>既有管按资产库管材、考虑老化：钢筋混凝土 n = 0.015，其余 0.013（WA-726 表 4-1 的新管值为 0.011–0.013）。新建重力管 0.012（表 4-1 GRP）。新建压力管按 7.3.2 节 Colebrook、峰值流速 ≤ 2.0 m/s。</li>
<li><b>产流：</b>每块地只算一次面积，按 Makani 不透水比例拆成两部分分别算径流再相加（WA-726 3.4.1）。</li>
<li><b>雨：</b>客户 Mussafah 模型同历时「10 年一遇含 CC」雨型，按比例缩放到 33 / 61 / 96 mm；雨停后再算 10 h。</li>
<li><b>边界与二维：</b>出水口固定尾水 1.3 m、带拍门（WA-726 表 4-5，1.8 m 作敏感性）。二维 15 m 网格、5 m DTM，井口交换 Cd = 0.6，二维糙率 0.030（WA-726 3.2.3 未开发区范围内）。</li>
</ul>
<p class="meta">逐项来源见 docs/RD-A_建模说明.md（第九节为输入清单，第十节为缺失输入）；要向客户补的资料见 docs/RD-A_客户补充资料请求_20261024.md。</p>
</section>

<section><h2>六、状态定义与主要不确定事项</h2>
<ul>
<li><b>L1：</b>现状，客户模型原样，含 RD-A 泵站（SW_ABT_W33_PSS_69）。</li>
<li><b>L2：</b>L1 加热点表 Plan A 的 15 台预置移动泵。</li>
<li><b>L2+planned：</b>L1 加客户情景「Temporary Intervention」（inp 51159）里列入计划的 2 台泵（51207、51225）及压力管；51169 只是方案，未计入。</li>
<li><b>L2+suggested：</b>L2+planned 加本次方案的措施。</li>
</ul>
<ul>
<li>模型没有观测数据，未率定。</li>
<li>ADM-76 旁隧道引道的排水泵站：暂作为新建计入方案（用户选择）；客户若确认已有现状泵站，改为 L1 并扣除造价。</li>
<li>ADM-80 岸边 DTM 填 0 区、ADM-161 东侧私人地块深坑：不配措施，判据统计剔除。</li>
<li>所有措施都是「有条件可建」：用地许可、EAD 排口审批、地下水（池底低于 0 m 的）、现状管线埋深（净距判不了）待落实。</li>
</ul>
</section>

<section><h2>七、历史版本</h2>
<details><summary>v1（2026-10-21，已作废）</summary>
<div style="display:grid;gap:10px;margin-top:8px">
<p>v1 用 84 项一维措施（40 座地面新塘、44 处泵直排现有出水口 31）+ 19 处地面取水泵，占位造价约 180 M USD。它把所有泵站的水都排到一个出水口，没有按 WA-726 手册（已开发区不建明塘、泵站场地与供电、排口审批）和空间约束（道路红线、离建筑净距）筛选，2026-10-23 判为不可建、作废。下表是它当时的结果（旧口径，未剔除 DTM 填 0 区），只作对照。</p>
<div class="tbl"><table><tr><th>暴雨</th><th>热点四项全过</th><th>全域 ≥0.30 m（ha）</th></tr>{v1rows}</table></div>
<p class="meta">v2（RDA_96B，允许新建排海口）同样未按约束，未发布即作废。</p>
</div></details>
</section>
<footer class="meta">系统：防线2+ 多态对比 → RD-A（可切换暴雨和状态，看逐帧水深、差值、设施参数、造价与运行情况）。数据：console/data/flood2d_rda_*；计算：tools/rda_*.py、tools/l2opt_*.py、tools/l2s_*.py。</footer>
</div></body></html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
