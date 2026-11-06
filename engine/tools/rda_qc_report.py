# -*- coding: utf-8 -*-
"""RD-A 一二维模型输入数据质量报告（2026-10-19）：把 dataqc 结果、子汇水区检查、inp↔shp 与最新管网对照合成一页 HTML。

先跑 tools/rda_qc_prep.py 和 python -m tools.dataqc（见 docs/RD-A_数据质检_20261019.md），再跑本脚本：
python tools/rda_qc_report.py --out out/dataqc/RD-A/RD-A_数据质量报告.html
"""
from __future__ import annotations

import argparse
import base64
import collections
import glob
import html
import io
import json
import math
import pathlib
import re
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = ROOT / "20261019/test/RD-A/RD-A"
SCEN = BASE / "2026-1hr"
QC = ROOT / "out/dataqc/RD-A"
INP_DIR = BASE / "Hydraulic Model"
DTM = "D:/阿布扎比/DTM_z40_customer/AUH_DTM_5m_Z40.TIF"
SW = ROOT / "20261019/Stormwater/PIPELINE.shp"
FT = 0.3048

# 每项问题在 RD-A 建模里怎么处理（Claude 自定，记入参数登记表）
HANDLING = {
    "A4": "按相邻管 / 井底插补管底，逐根登记；客户的 inp 里这些管有管底（SewerGEMS 自动插补过），建模以 inp 为准",
    "A7": "模型图层没有材质字段。改从最新全市管网按位置匹配（2,144 / 2,154 根对上），钢筋混凝土 n = 0.015、其余 0.013",
    "A9": "inp 里这些井有井底（SewerGEMS 取接入管最低管底）；建模以 inp 为准，登记",
    "A11": "井 2061 井盖 20.1 m、井底 0.35 m，井深 19.75 m：井盖多半录错（周边地面约 3 m）。建模时井盖改取 DTM，登记",
    "A17": "非标准规格多为英制换算值（如 375、525、675 mm），按原值用",
    "A19": "两根小于 300 mm，现状如此，按原值用",
    "C6": "多为管网起始井，正常；不处理",
    "C7": "平面交叉不相连：立交或漏节点。模型按 inp 的连接关系算，不改；列给客户核对",
    "C8": "压力管接的 J-1 / J-2 / J-4 是压力管节点，shp 没导出、inp 里有；不影响建模",
    "C11": "管端搭在另一根管中段。按 inp 的连接关系算，不改；列给客户核对",
    "C12": "全部由逆坡主干管 4243 一处引起（规则按管底定流向，逆坡把上游判成流不出去）；动力波照常过水，不影响建模",
    "D1": "DN1800 主干管逆坡 0.30 m，在集水井前；动力波可算，按原值用，请客户核对",
    "D2": "零坡管动力波可算，按原值用",
    "D3": "多为 2–5 m 的短管接跌水井，坡度大但不是错；按原值用",
    "D4": "沿流向缩径，形成瓶颈；按原值用（是现状能力的一部分）",
    "D7": "覆土 < 0.3 m，按原值用，登记",
    "D8": "只对照手册的提示（最小覆土 1.2 m），不影响建模",
    "E2": "井盖与 DTM 差 > 1 m。一二维交换用模型井盖还是 DTM 地面要定；RB-01 用的是模型井盖",
    "E3": "井底高于 DTM 地面：井底或该处 DTM 至少一个有误；交换高程取两者较高者，登记",
}
SEV = {"crit": ("影响建模", "crit"), "warn": ("需处理", "warn"), "info": ("提示", "info")}


def b64png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def sec(t, name):
    m = re.search(r"\[" + name + r"\]\n(.*?)(?:\n\[|\Z)", t, re.S)
    return [l.split() for l in m.group(1).splitlines() if l.strip() and not l.startswith(";")] if m else []


def scs_q(P, CN):
    S = 25.4 * (1000.0 / CN - 10.0)
    Ia = 0.2 * S
    return np.where(P > Ia, (P - Ia) ** 2 / (P - Ia + S), 0.0)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(QC / "RD-A_数据质量报告.html"))
    a = ap.parse_args(argv)
    import geopandas as gpd
    import rasterio
    from rasterio.mask import mask
    from scipy import ndimage
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    sys.path.insert(0, str(ROOT))
    from tools.dataqc import thresholds as T

    texts = json.loads((ROOT / "tools/dataqc/texts_zh.json").read_text(encoding="utf-8"))
    R = json.loads((QC / "client_model/result.json").read_text(encoding="utf-8"))
    RS = json.loads((QC / "stormwater_2026/result.json").read_text(encoding="utf-8"))
    gp = QC / "input/rda_network.gpkg"
    cond = gpd.read_file(gp, layer="Conduit")
    mh = gpd.read_file(gp, layer="Manhole")
    pp = gpd.read_file(gp, layer="Pressure_Pipe")
    nodes_other = {l: gpd.read_file(gp, layer=l) for l in ("Outfall", "Pump", "Wetwell_tank")}
    dom = gpd.read_file(QC / "input/rda_domain.geojson")
    cat = gpd.read_file(SCEN / "Catchment.shp").set_crs(32640, allow_override=True)
    lab_of = {**{str(i): str(l) for i, l in zip(cond.pipe_id, cond.label)}, **{str(i): str(l) for i, l in zip(mh.node_id, mh.label)},
              **{str(i): str(l) for i, l in zip(pp.pipe_id, pp.label)}}

    # ── 子汇水区 ──
    cat["wkb"] = cat.geometry.normalize().to_wkb()
    vc = cat.wkb.value_counts()
    cat["pair"] = cat.wkb.map(vc) == 2
    cat["imp"] = cat.LABEL.str.startswith("12SUI")
    union_ha = cat.geometry.union_all().area / 1e4
    sum_ha = cat.A.sum()
    # 客户结果的水量平衡：2024-1hr，用 CN95 径流深反推雨量
    c24 = gpd.read_file(BASE / "2024-1hr/Catchment.shp")
    c24["wkb"] = c24.geometry.normalize().to_wkb()
    d95 = (c24[c24.SCS_CN == 95].RUNOFF_VOL / (c24[c24.SCS_CN == 95].A * 1e4) * 1000).median()
    Ps = np.linspace(1, 400, 4000)
    P24 = float(Ps[np.argmin(abs(scs_q(Ps, 95) - d95))])
    over = 0
    pairs = 0
    for _, g in c24.groupby("wkb"):
        if len(g) == 2:
            pairs += 1
            if g.RUNOFF_VOL.sum() > P24 / 1000 * g.A.iloc[0] * 1e4 * 1.001:
                over += 1
    scen_p = {}
    for d in sorted(BASE.iterdir()):
        if d.is_dir() and (d / "Catchment.shp").exists():
            c = gpd.read_file(d / "Catchment.shp")
            c95 = c[c.SCS_CN == 95]
            dep = (c95.RUNOFF_VOL / (c95.A * 1e4) * 1000).median()
            scen_p[d.name] = (round(float(Ps[np.argmin(abs(scs_q(Ps, 95) - dep))])), round(float(c.RUNOFF_VOL.sum())))
    cn_counts = cat.SCS_CN.value_counts()
    tc5 = int((cat.TC == 5).sum())
    m_labels = set(mh.label.astype(str)) | {"RD-A", "RD-A1", "RD-A2"}
    out_missing = int((~cat.OUT_NODE.astype(str).isin(m_labels)).sum())
    pair_ex = cat[cat.pair].sort_values(["wkb", "LABEL"]).head(6)[["LABEL", "OUT_NODE", "A", "SCS_CN", "TC"]]

    # ── inp ↔ shp ──
    cs = cond.set_index(cond.pipe_id.astype(str))
    ms = mh.set_index(mh.node_id.astype(str))
    inp_rows = []
    for f in sorted(INP_DIR.glob("*.inp")):
        t = f.read_text(encoding="utf-8", errors="ignore")
        J, C = sec(t, "JUNCTIONS"), sec(t, "CONDUITS")
        nd = sum(1 for r in C if r[0] in cs.index and abs(float(r[4]) - cs.loc[r[0], "mann_n"]) > 1e-4)
        jd = sum(1 for r in J if r[0] in ms.index and abs(float(r[1]) * FT - ms.loc[r[0], "invert"]) > 0.02)
        gd = sum(1 for r in J if r[0] in ms.index and abs((float(r[1]) + float(r[2])) * FT - ms.loc[r[0], "cover"]) > 0.02)
        inp_rows.append((f.name.replace("RD - A.stsw_", ""), len(J), len(C), nd, jd, gd))
    t0 = (INP_DIR / "RD - A.stsw_1_51079.inp").read_text(encoding="utf-8", errors="ignore")
    opts = {r[0]: " ".join(r[1:]) for r in sec(t0, "OPTIONS")}
    n_inflow = len(sec(t0, "INFLOWS"))
    n_rough = collections.Counter(float(r[4]) for r in sec(t0, "CONDUITS"))

    # ── 与最新管网对照 ──
    pm = json.loads((ROOT / "out/pipe_material_RDA.json").read_text(encoding="utf-8"))["pipes"]
    mat = collections.Counter(v["material"] or "未匹配" for v in pm.values())
    cond["sd"] = cond.pipe_id.astype(str).map(lambda k: pm.get(k, {}).get("src_diameter"))
    mm = cond[cond.sd.notna()]
    dia_bad = mm[(mm.diameter - mm.sd).abs() >= 1]
    years = collections.Counter((v["installed"] or "")[:4] for v in pm.values() if v.get("installed"))
    sw_extra = gpd.read_file(QC / "stormwater_not_in_model.gpkg")
    # 管底对照（最新管网 = 模型 + 100 m）
    from shapely.geometry import Point
    sw = gpd.read_file(SW, bbox=tuple(cond.total_bounds + np.array([-50, -50, 50, 50])))
    sw = sw[sw.geometry.notna()].explode(index_parts=False).reset_index(drop=True)
    idx = sw.sindex
    dinv = []
    for g, u, dn in zip(cond.geometry, cond.us_invert, cond.ds_invert):
        mid = g.interpolate(0.5, normalized=True)
        j = [k for k in idx.query(mid.buffer(3)) if sw.geometry.iloc[k].distance(mid) <= 3]
        if not j:
            continue
        k = min(j, key=lambda k: sw.geometry.iloc[k].distance(mid))
        r = sw.iloc[k]
        p0 = Point(g.coords[0][:2])
        same = Point(r.geometry.coords[0][:2]).distance(p0) < Point(r.geometry.coords[-1][:2]).distance(p0)
        su, sd_ = (r.INVERT_LEV, r.INVERT_L_1) if same else (r.INVERT_L_1, r.INVERT_LEV)
        dinv.append(max(abs(u - (su - 100)), abs(dn - (sd_ - 100))))
    dinv = np.array([x for x in dinv if np.isfinite(x)])

    # ── DTM ──
    with rasterio.open(DTM) as ds:
        arr, tr = mask(ds, dom.geometry, crop=True, nodata=-9999)
    arr = arr[0].astype(float)
    valid = arr != -9999
    zero = (arr == 0) & valid
    lab, nz = ndimage.label(zero)
    sz = np.bincount(lab.ravel())[1:] if nz else np.array([])
    big = int(np.argmax(sz)) + 1 if nz else 0
    rr, cc = np.nonzero(lab == big)
    zx, zy = tr * (cc.mean(), rr.mean()) if len(rr) else (0, 0)
    neg = (arr < 0) & valid

    # ── 地图 ──
    ext = (tr.c, tr.c + tr.a * arr.shape[1], tr.f + tr.e * arr.shape[0], tr.f)
    show = np.where(valid, arr, np.nan)
    ls = LightSource(azdeg=315, altdeg=45)
    filled = np.where(valid, arr, np.nanmedian(show))
    hs = ls.hillshade(filled, vert_exag=3, dx=5, dy=5)
    ck = R["network"]["checks"]

    def ids(code):
        return set(map(str, ck[code]["ids"]))

    fig, ax = plt.subplots(figsize=(11, 5.6))
    ax.imshow(hs, extent=ext, cmap="gray", alpha=0.55)
    im = ax.imshow(show, extent=ext, cmap="terrain", vmin=-1, vmax=10, alpha=0.55)
    dom.boundary.plot(ax=ax, color="#333", lw=0.8)
    lw = np.clip(cond.diameter / 600.0, 0.4, 3.0)
    cond.plot(ax=ax, color="#1f5fa8", linewidth=lw)
    pp.plot(ax=ax, color="#7a3fb0", linewidth=2)
    sw_extra.plot(ax=ax, color="#e08a00", linewidth=1.2, label="最新管网有、模型没有（187 根）")
    cond[cond.pipe_id.astype(str).isin(ids("D1"))].plot(ax=ax, color="#d7191c", linewidth=5, label="逆坡主干管 4243")
    cond[cond.pipe_id.astype(str).isin(set(map(str, dia_bad.pipe_id)))].plot(ax=ax, color="#ff3fa4", linewidth=3, label="管径与最新数据不同（7 根）")
    e2 = mh[mh.node_id.astype(str).isin(ids("E2"))]
    e3 = mh[mh.node_id.astype(str).isin(ids("E3"))]
    a9 = mh[mh.node_id.astype(str).isin(ids("A9"))]
    e2.plot(ax=ax, color="#f4a300", markersize=10, marker="o", label=f"井盖与 DTM 差 > 1 m（{len(e2)}）")
    e3.plot(ax=ax, color="#d7191c", markersize=14, marker="^", label=f"井底高于地面（{len(e3)}）")
    a9.plot(ax=ax, color="black", markersize=14, marker="x", label=f"缺井底标高（{len(a9)}）")
    if len(rr):
        xs = tr.c + tr.a * (np.arange(arr.shape[1]) + 0.5)
        ys = tr.f + tr.e * (np.arange(arr.shape[0]) + 0.5)
        ax.contour(xs, ys, np.where(lab == big, 1, 0), levels=[0.5], colors="#d7191c", linewidths=1.5)
        ax.annotate("DTM = 0 片区\n2.9 ha", (zx, zy), xytext=(zx + 250, zy - 350), fontsize=9, color="#d7191c",
                    arrowprops=dict(arrowstyle="->", color="#d7191c"))
    for l, g in nodes_other.items():
        g.plot(ax=ax, color="#7a3fb0", markersize=40, marker="s")
    ax.annotate("集水井 / 泵站 / 出水口", (nodes_other["Wetwell_tank"].geometry.x.iloc[0], nodes_other["Wetwell_tank"].geometry.y.iloc[0]),
                xytext=(-170, 25), textcoords="offset points", fontsize=9, color="#7a3fb0", arrowprops=dict(arrowstyle="->", color="#7a3fb0"))
    ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])
    ax.set_xticks([]); ax.set_yticks([]); ax.set_xlabel(""); ax.set_ylabel("")
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01); cb.set_label("DTM 高程 (m)")
    ax.legend(loc="lower left", fontsize=8, framealpha=0.9)
    map1 = b64png(fig); plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 5.0))
    single = cat[~cat.pair]
    pv = cat[cat.pair & ~cat.imp]
    pv.plot(ax=ax, column="SCS_CN", cmap="YlGnBu", vmin=60, vmax=95, edgecolor="#555", linewidth=0.2, legend=True,
            legend_kwds={"label": "成对地块的透水部分 CN", "fraction": 0.025, "pad": 0.01})
    single.plot(ax=ax, facecolor="#f4a300", edgecolor="#333", linewidth=0.4)
    dom.boundary.plot(ax=ax, color="#333", lw=0.8)
    ax.set_xticks([]); ax.set_yticks([]); ax.set_xlabel(""); ax.set_ylabel("")
    ax.set_title(f"1,914 个地块各有一块透水 + 一块不透水（CN 95）子汇水区，几何相同、面积都按整块计；橙色 = 不成对的 {len(single)} 块", fontsize=10)
    map2 = b64png(fig); plt.close(fig)

    # ── HTML ──
    e = html.escape

    def idlist(code, net=R):
        c = net["network"]["checks"][code]
        items = [f"{i}（{lab_of[str(i)]}）" if str(i) in lab_of and lab_of[str(i)] != str(i) else str(i) for i in c["ids"]]
        if not items:
            return ""
        return f'<details><summary>{len(items)} 个编号</summary><p class="ids">{e("、".join(items))}</p></details>'

    rows = []
    order = [k for k in T.ALL_CODES if k in ck]
    for k in order:
        c = ck[k]
        if not c["count"] or k in ("C2",):
            continue
        s, cls = SEV[T.severity(k)]
        rows.append(f'<tr><td class="code">{k}</td><td><b>{e(texts[k]["name"])}</b><div class="sub">{e(texts[k]["impact"])}</div></td>'
                    f'<td class="num">{c["count"]:,}</td><td class="num">{c["total"]:,}</td><td><span class="pill {cls}">{s}</span></td>'
                    f'<td>{e(HANDLING.get(k, "按原值用"))}{idlist(k)}</td></tr>')
    passed = [k for k in order if not ck[k]["count"] and k not in ("C2",)]
    passed_txt = "、".join(f"{k} {texts[k]['name']}" for k in passed if k in texts)
    cks = RS["network"]["checks"]
    cmp_rows = "".join(f"<tr><td class='code'>{k}</td><td>{e(texts[k]['name'])}</td><td class='num'>{ck.get(k, {}).get('count', 0):,}</td>"
                       f"<td class='num'>{cks.get(k, {}).get('count', 0):,}</td></tr>"
                       for k in T.ALL_CODES if (ck.get(k, {}).get("count") or cks.get(k, {}).get("count")) and k in texts)
    dt = R["dtm"]
    dtm_rows = "".join(f"<tr><td class='code'>{c['code']}</td><td>{e(texts.get(c['code'], {}).get('name', ''))}</td><td>{e(c['value'])}</td>"
                       f"<td><span class='pill {'ok' if c['sev'] == 'ok' else 'info' if c['sev'] == 'info' else 'warn'}'>"
                       f"{'通过' if c['sev'] == 'ok' else '提示' if c['sev'] == 'info' else '需处理'}</span></td><td>{e(c['note'])}</td></tr>" for c in dt["checks"])
    inp_tbl = "".join(f"<tr><td>{a_}</td><td class='num'>{b}</td><td class='num'>{c}</td><td class='num'>{d}</td><td class='num'>{f}</td><td class='num'>{g}</td></tr>"
                      for a_, b, c, d, f, g in inp_rows)
    scen_tbl = "".join(f"<tr><td>{k}</td><td class='num'>≈ {v[0]} mm</td><td class='num'>{v[1]:,}</td></tr>" for k, v in scen_p.items())
    pair_tbl = "".join(f"<tr><td>{r.LABEL}</td><td>{r.OUT_NODE}</td><td class='num'>{r.A:.4f}</td><td class='num'>{r.SCS_CN:.0f}</td><td class='num'>{r.TC:g}</td></tr>"
                       for r in pair_ex.itertuples())
    dia_tbl = "".join(f"<tr><td>{r.pipe_id}（{r.label}）</td><td class='num'>{r.diameter:.0f}</td><td class='num'>{r.sd:.0f}</td></tr>" for r in dia_bad.itertuples())
    mat_txt = "、".join(f"{k} {v:,}" for k, v in mat.most_common())
    yr_txt = "、".join(f"{k} 年 {v}" for k, v in sorted(years.items(), key=lambda x: -x[1])[:8])
    rough_txt = "、".join(f"{k:g} × {v:,}" for k, v in sorted(n_rough.items()))
    s = R["network"]["summary"]
    neg_ha = neg.sum() * 25 / 1e4

    page = TEMPLATE.format(
        map1=map1, map2=map2, rows="".join(rows), passed=e(passed_txt), cmp_rows=cmp_rows, dtm_rows=dtm_rows, inp_tbl=inp_tbl,
        scen_tbl=scen_tbl, pair_tbl=pair_tbl, dia_tbl=dia_tbl, mat_txt=e(mat_txt), yr_txt=e(yr_txt), rough_txt=rough_txt,
        links=s["links"], gravity=s["gravity"], pressure=s["pressure"], km=s["lengthKm"], nodes=s["nodes"],
        n_cat=len(cat), n_pairs=int(cat.pair.sum() // 2), n_single=int((~cat.pair).sum()), sum_ha=sum_ha, union_ha=union_ha,
        over=over, pairs=pairs, P24=P24, tc5=tc5, out_missing=out_missing,
        cn95=int(cn_counts.get(95, 0)), cn86=int(cn_counts.get(86, 0)), cn61=int(cn_counts.get(61, 0)),
        n_inflow=n_inflow, flow_units=opts.get("FLOW_UNITS", ""), route_step=opts.get("ROUTING_STEP", ""), ponding=opts.get("ALLOW_PONDING", ""),
        matched=sum(1 for v in pm.values() if v["material"] is not None), n_pm=len(pm), dia_same=len(mm) - len(dia_bad), n_mm=len(mm),
        inv_ok=int((dinv <= 0.05).sum()), inv_n=len(dinv), inv_pct=100 * (dinv <= 0.05).mean(), inv_mid=int(((dinv > 0.05) & (dinv <= 0.3)).sum()),
        inv_big=int((dinv > 0.3).sum()), sw_n=len(sw_extra), sw_km=sw_extra.length.sum() / 1000,
        sw_links=RS["network"]["summary"]["links"], sw_km_all=RS["network"]["summary"]["lengthKm"],
        cells=dt["cells"], zero_n=int(zero.sum()), zero_big=int(sz.max()) if nz else 0, zero_ha=(sz.max() if nz else 0) * 25 / 1e4, zx=zx, zy=zy,
        neg_n=int(neg.sum()), neg_ha=neg_ha, dmin=dt["min"], dmax=dt["max"], p50=dt["quantiles"]["p50"], flat=dt["flat"],
        datum=s["datumBasis"],
    )
    pathlib.Path(a.out).write_text(page, encoding="utf-8")
    print("→", a.out, f"{len(page) / 1e6:.1f} MB")
    return 0


TEMPLATE = r"""<title>RD-A 数据质量报告</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&family=Noto+Serif+SC:wght@600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* 版式：一栏报告，先结论后明细；表格各自横向滚动 */
:root{{--bg:#f6f8f9;--paper:#ffffff;--fg:#17242c;--muted:#5b6b74;--line:#d8e0e4;--accent:#0f5f80;
--crit:#b3261e;--crit-bg:#fbe9e7;--warn:#9a6200;--warn-bg:#fdf1dc;--info:#3d5a6c;--info-bg:#e6eef3;--ok:#1e6b3a;--ok-bg:#e3f2e8;
--display:"Noto Serif SC","Songti SC",serif;--body:"Noto Sans SC","Microsoft YaHei",sans-serif;--mono:"IBM Plex Mono",Consolas,monospace}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#0f171c;--paper:#16222a;--fg:#e3eaee;--muted:#9aabb5;--line:#2b3b45;--accent:#6cc3e6;
--crit:#ff8a80;--crit-bg:#3a1c1a;--warn:#f2c065;--warn-bg:#352a14;--info:#a9c4d4;--info-bg:#1d2e39;--ok:#7fd49b;--ok-bg:#16301f;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#0f171c;--paper:#16222a;--fg:#e3eaee;--muted:#9aabb5;--line:#2b3b45;--accent:#6cc3e6;
--crit:#ff8a80;--crit-bg:#3a1c1a;--warn:#f2c065;--warn-bg:#352a14;--info:#a9c4d4;--info-bg:#1d2e39;--ok:#7fd49b;--ok-bg:#16301f;color-scheme:dark}}
body{{background:var(--bg);color:var(--fg);font-family:var(--body);font-size:15px;line-height:1.7}}
.wrap{{max-width:1080px;margin:0 auto;padding-block:32px 64px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:grid;gap:28px}}
h1,h2{{font-family:var(--display);text-wrap:balance;margin:0}}
h1{{font-size:30px;line-height:1.3}} h2{{font-size:21px;padding-top:6px;border-top:2px solid var(--fg)}}
h3{{font-size:16px;margin:0}}
p{{margin:0;max-width:72ch}}
.eyebrow{{font-family:var(--mono);font-size:12px;letter-spacing:.08em;color:var(--muted);text-transform:uppercase}}
.meta{{color:var(--muted);font-size:13px}}
section{{display:grid;gap:14px;min-width:0}}
.verdicts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}}
.v{{background:var(--paper);border:1px solid var(--line);border-left:5px solid var(--c);padding:12px 14px;display:grid;gap:4px;min-width:0}}
.v.crit{{--c:var(--crit)}} .v.warn{{--c:var(--warn)}} .v.ok{{--c:var(--ok)}}
.v .t{{font-weight:700}} .v .s{{font-size:13px;color:var(--muted)}}
.tbl{{overflow-x:auto;background:var(--paper);border:1px solid var(--line)}}
table{{border-collapse:collapse;width:100%;font-size:13.5px}}
th,td{{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{font-weight:500;color:var(--muted);font-size:12.5px;white-space:nowrap;background:var(--bg)}}
.num{{text-align:right;font-variant-numeric:tabular-nums;font-family:var(--mono);white-space:nowrap}}
.code{{font-family:var(--mono);white-space:nowrap}}
.sub{{font-size:12.5px;color:var(--muted)}}
.pill{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;white-space:nowrap}}
.pill.crit{{background:var(--crit-bg);color:var(--crit)}} .pill.warn{{background:var(--warn-bg);color:var(--warn)}}
.pill.info{{background:var(--info-bg);color:var(--info)}} .pill.ok{{background:var(--ok-bg);color:var(--ok)}}
details summary{{cursor:pointer;color:var(--accent);font-size:12.5px}}
.ids{{font-family:var(--mono);font-size:12px;color:var(--muted);word-break:break-all;max-width:none}}
figure{{margin:0;background:var(--paper);border:1px solid var(--line);padding:10px;display:grid;gap:6px}}
figure img{{width:100%;height:auto;display:block;border-radius:2px}}
figcaption{{font-size:12.5px;color:var(--muted)}}
.callout{{background:var(--crit-bg);border:1px solid var(--crit);padding:14px 16px;display:grid;gap:8px}}
.callout b{{color:var(--crit)}}
ul{{margin:0;padding-left:20px}} li{{margin:2px 0}}
.kv{{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;font-size:14px}}
.kv dt{{color:var(--muted)}} .kv dd{{margin:0;min-width:0}}
@media (max-width:560px){{h1{{font-size:24px}} .kv{{grid-template-columns:1fr}}}}
</style>
<div class="wrap">
<header style="display:grid;gap:8px">
  <div class="eyebrow">RD-A 汇水区 · 一二维水动力模型输入 · 2026-10-19</div>
  <h1>RD-A 数据质量报告</h1>
  <p class="meta">质检工具 tools/dataqc（平台 m4-data 判定规则），另加子汇水区、inp 与 shp、与最新全市管网三项对照。所有数字由 tools/rda_qc_report.py 从数据直接算出。</p>
</header>

<section>
  <h2>结论</h2>
  <div class="verdicts">
    <div class="v crit"><span class="t">子汇水区：面积重复，不能直接用</span><span class="s">{n_pairs:,} 个地块各算了两遍，面积合计 {sum_ha:.0f} ha，实际只有 {union_ha:.0f} ha</span></div>
    <div class="v ok"><span class="t">一维管网：可以用</span><span class="s">连成一张网、全部流向出口；inp 与 shp 一致；与最新全市管网 {matched:,} / {n_pm:,} 根对上</span></div>
    <div class="v warn"><span class="t">管网局部：需核对</span><span class="s">1 根逆坡主干管、24 个缺井底、119 个井盖与地面不符、7 根管径不同</span></div>
    <div class="v warn"><span class="t">DTM：可以用，一处待确认</span><span class="s">计算域全覆盖、无尖峰深坑；一片 {zero_ha:.1f} ha 高程正好为 0</span></div>
  </div>
  <div class="callout">
    <p><b>开跑前要定的一件事：子汇水区怎么算。</b>客户 SewerGEMS 模型把每个地块拆成透水（12SU####，CN 61–86）和不透水（12SUI####，CN 95）两块，但两块都用整块地的面积。客户自己的结果里，{pairs:,} 对中有 {over:,} 对的径流量比降雨量（2024-1hr，约 {P24:.0f} mm）还大。客户 inp 不算产流、直接给入流过程线，所以这些入流也是按双倍面积来的。</p>
    <p>建议：每个地块只算一次面积，按不透水比例 f 合成 CN（f·95 + (1−f)·CN透水），f 从 Makani 土地利用 / 建筑占地取。另一个办法是沿用客户的双倍面积，结果能和客户对比，但水量偏大。</p>
  </div>
</section>

<section>
  <h2>用到的数据</h2>
  <div class="tbl"><table>
    <tr><th>数据</th><th>路径</th><th>用途</th><th>说明</th></tr>
    <tr><td>客户水力模型</td><td class="code">20261019/test/RD-A/RD-A/Hydraulic Model/*.inp（18 个）</td><td>一维管网、泵、出水口</td><td>Bentley SewerGEMS 导出，英制（{flow_units}、ft）；演算步长 {route_step}；ALLOW_PONDING {ponding}；入流过程线 {n_inflow:,} 个节点</td></tr>
    <tr><td>客户模型图层</td><td class="code">…/RD-A/&lt;情景&gt;/*.shp（9 个情景）</td><td>几何、子汇水区</td><td>公制（m、mm）；9 个情景的管网与子汇水区完全相同，只差结果列；没有 .prj，坐标与 DTM 同为 UTM 40N</td></tr>
    <tr><td>最新雨水管网</td><td class="code">20261019/Stormwater/*.shp（全市）</td><td>对照、取管材</td><td>只取 RD-A 范围：{sw_links:,} 根、{sw_km_all:.1f} km；高程基准比模型高 100 m</td></tr>
    <tr><td>DTM</td><td class="code">DTM_z40_customer/AUH_DTM_5m_Z40.TIF</td><td>二维地形</td><td>5 m 格网，UTM 40N；计算域内 {cells:,} 格</td></tr>
    <tr><td>计算域</td><td class="code">out/dataqc/RD-A/input/rda_domain.geojson</td><td>二维范围</td><td>{n_cat:,} 块子汇水区合并，{union_ha:.1f} ha</td></tr>
  </table></div>
</section>

<section>
  <h2>问题在哪里</h2>
  <figure><img src="{map1}" alt="RD-A 管网、DTM 与问题位置图"><figcaption>底图为 DTM 晕渲；蓝线为模型重力管（线宽按管径），紫色为压力管与集水井、泵站、出水口。</figcaption></figure>
</section>

<section>
  <h2>一、子汇水区（产流）</h2>
  <dl class="kv">
    <dt>块数</dt><dd>{n_cat:,} 块：成对 {n_pairs:,} 对，单独 {n_single} 块</dd>
    <dt>面积</dt><dd>属性面积合计 {sum_ha:.1f} ha；几何合并后 {union_ha:.1f} ha（属性面积与几何面积逐块一致）</dd>
    <dt>产流方法</dt><dd>全部 SCS CN + SCS 单位线，符合 WA-726 表 3-1</dd>
    <dt>CN</dt><dd>95 × {cn95:,}、86 × {cn86:,}、61 × {cn61:,}，其余 62–85</dd>
    <dt>汇流时间</dt><dd>{tc5:,} 块为 5 min，其余按公式算</dd>
    <dt>出口节点</dt><dd>全部能在检查井图层找到（找不到的 {out_missing} 块）</dd>
  </dl>
  <figure><img src="{map2}" alt="子汇水区成对情况"></figure>
  <h3>成对的例子</h3>
  <div class="tbl"><table><tr><th>子汇水区</th><th>出口节点</th><th>面积 (ha)</th><th>CN</th><th>汇流时间 (min)</th></tr>{pair_tbl}</table></div>
  <h3>各情景的雨量（用客户结果里 CN 95 子汇水区的径流深反推）</h3>
  <div class="tbl"><table><tr><th>情景</th><th>反推雨量</th><th>客户径流总量 (m³)</th></tr>{scen_tbl}</table></div>
  <p class="meta">同一年份的 1 h、2 h、24 h 总雨量几乎相同。我们的设计雨仍按约定的 33 / 61 / 96 mm × 1 / 2 / 24 h。</p>
</section>

<section>
  <h2>二、一维管网</h2>
  <dl class="kv">
    <dt>规模</dt><dd>{links:,} 根管段（重力 {gravity:,}、压力 {pressure}），{km:.1f} km；{nodes:,} 个节点</dd>
    <dt>连通</dt><dd>1 个连通分量。重力管汇入集水井 50936，3 台泵（轴流 1、混流 2）送到出水口 51083、51084；集水井另有溢流管 3774 通往出水口 31（尾水 1.3 m，有拍门）</dd>
    <dt>高程基准</dt><dd>{datum}</dd>
    <dt>客户糙率</dt><dd>{rough_txt}（= WA-726 新管值）</dd>
  </dl>
  <h3>发现的问题（客户模型）</h3>
  <div class="tbl"><table>
    <tr><th>编号</th><th>检查项</th><th>条数</th><th>参与判定</th><th>程度</th><th>RD-A 建模怎么处理</th></tr>
    {rows}
  </table></div>
  <details><summary>通过的检查项</summary><p class="sub">{passed}</p></details>
</section>

<section>
  <h2>三、与最新全市管网对照</h2>
  <dl class="kv">
    <dt>位置匹配</dt><dd>模型 {n_pm:,} 根管中 {matched:,} 根在最新管网里找到（中点距离 ≤ 3 m、方向差 ≤ 20°）</dd>
    <dt>管径</dt><dd>{n_mm:,} 根里 {dia_same:,} 根相同</dd>
    <dt>管底高程</dt><dd>{inv_n:,} 根里 {inv_ok:,} 根相差 ≤ 5 cm（{inv_pct:.0f} %），5–30 cm {inv_mid}，&gt; 30 cm {inv_big}（最新数据减 100 m 后比）</dd>
    <dt>管材</dt><dd>{mat_txt}</dd>
    <dt>敷设年份</dt><dd>{yr_txt}（1600 是占位值）</dd>
    <dt>模型里没有的</dt><dd>{sw_n} 根、{sw_km:.1f} km，多为 DN300 及以下的雨水口连接管（图中橙色）</dd>
  </dl>
  <h3>管径不同的 7 根</h3>
  <div class="tbl"><table><tr><th>管段</th><th>模型 (mm)</th><th>最新数据 (mm)</th></tr>{dia_tbl}</table></div>
  <h3>两份管网的质检对比（条数）</h3>
  <div class="tbl"><table><tr><th>编号</th><th>检查项</th><th>客户模型</th><th>最新管网</th></tr>{cmp_rows}</table></div>
  <p class="meta">最新管网在 RD-A 范围内没有泵站和出水口要素，所以整张网都判为「无出口」（C4）。一维建模用客户模型，最新管网只取管材。</p>
</section>

<section>
  <h2>四、一维模型文件（inp 与 shp 是否一致）</h2>
  <div class="tbl"><table><tr><th>inp</th><th>检查井</th><th>管段</th><th>糙率不同</th><th>井底不同</th><th>地面不同</th></tr>{inp_tbl}</table></div>
  <p class="meta">高程按 ft × 0.3048 换算后比，差 &gt; 2 cm 算不同。建模以 inp 为准。</p>
</section>

<section>
  <h2>五、DTM（二维地形）</h2>
  <dl class="kv">
    <dt>覆盖</dt><dd>计算域内 {cells:,} 格全部有值</dd>
    <dt>高程</dt><dd>{dmin:.2f} ～ {dmax:.2f} m，中位 {p50:.2f} m；平地 {flat:,} 格</dd>
    <dt>高程为 0</dt><dd>{zero_n:,} 格，其中一片 {zero_big:,} 格（{zero_ha:.1f} ha），中心约 ({zx:.0f}, {zy:.0f})，靠近集水井和出水口。可能是填成 0 的水面或施工区，要看影像确认</dd>
    <dt>低于 0</dt><dd>{neg_n} 格（{neg_ha:.2f} ha），分 3 小片，可能是下穿道</dd>
  </dl>
  <div class="tbl"><table><tr><th>编号</th><th>检查项</th><th>结果</th><th>判定</th><th>说明</th></tr>{dtm_rows}</table></div>
</section>

<section>
  <h2>六、要定或要核对的事</h2>
  <div class="tbl"><table>
    <tr><th>事项</th><th>谁定</th><th>不定的话我会怎么做</th></tr>
    <tr><td><b>子汇水区面积重复</b></td><td>你</td><td>等你定，不开跑</td></tr>
    <tr><td>DTM 高程为 0 的 {zero_ha:.1f} ha</td><td>看影像</td><td>当作地面按原值用，登记</td></tr>
    <tr><td>逆坡主干管 4243、管径不同的 7 根、C7 / C11 连接</td><td>客户</td><td>按客户 inp 原值用，登记</td></tr>
    <tr><td>井盖与 DTM 不符（119 个）</td><td>你</td><td>一二维交换沿用 RB-01 做法，用模型井盖</td></tr>
    <tr><td>井 2061 井盖 20.1 m</td><td>—</td><td>改取 DTM 地面，登记</td></tr>
  </table></div>
</section>

<footer class="meta">结果文件：out/dataqc/RD-A/client_model/report.html（客户模型详细质检）、stormwater_2026/report.html（最新管网）、issues.gpkg（问题图层，可在 ArcGIS / QGIS 打开）、out/pipe_material_RDA.json（管材匹配）。</footer>
</div>
"""

if __name__ == "__main__":
    raise SystemExit(main())
