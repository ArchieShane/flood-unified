"""给不懂技术的客户：3 页「一图看懂」L2+suggested 优化过程（离线 .pptx，2026-10-25 用户要求）。

图全部用 RD-A 真实数据画：
  图 1 一图看懂：① 所有可建位置（候选 754 个）→ ② 一轮一轮挑（贪心 20 轮，按轮次着色）→ ③ 最终方案（瘦身后 53 项 + 7 座地面泵站）→ ④ 积水变化（设计标准雨下，方案前 / 后深积水范围叠图）
  图 2 一步步挑：第 1 / 3 / 10 / 20 轮的地图 + 「花的钱 vs 溢流减少」曲线
  图 3 结果：设计标准雨（61 mm / 24 h）下，客户计划 vs 我们的方案的最大水深
数据：out/l2opt/RDA_96C（candidates、greedy_path、prune、eval2d）、sumps_OPTCS2、c2d 二维结果；剔除区同验收口径。
用法：python tools/rda_ppt_simple.py [--out <pptx>]
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from workdir import WORK  # noqa: E402

OPT = ROOT / "out/l2opt/RDA_96C"
C2D = WORK / "c2d"
HS = ["ADM-75", "ADM-76", "ADM-80", "ADM-161", "ADM-376", "ADM-377", "ADM-378", "ADM-379", "ADM-380", "ADM-382", "ADM-383"]
NAVY, TEAL, ORANGE, SAND, GREY = "#14213D", "#0F766E", "#C2410C", "#F6F4EF", "#9CA3AF"
KCOL = {"tank_gravity": "#0F766E", "tank_pumped": "#7C3AED", "ps_cluster": "#1D4ED8", "surface_ps": "#C2410C"}
KLAB = {"tank_gravity": "地下调蓄池（重力进水）", "tank_pumped": "井边泵站 + 地下池", "ps_cluster": "泵站 → 排海口", "surface_ps": "地面泵站（洼地）"}
STORM = "S61D24"            # 设计标准雨：10 年一遇 + 20 % 气候变化，24 h 61 mm


def xy_of(c):
    if c.get("px") is not None:
        return float(c["px"]), float(c["py"])
    return float(c["ps_x"]), float(c["ps_y"])


def load():
    import geopandas as gpd
    cand = {c["id"]: c for c in json.loads((OPT / "candidates.json").read_text(encoding="utf-8"))["candidates"]}
    P = json.loads((OPT / "greedy_path.json").read_text(encoding="utf-8")); P = P if isinstance(P, list) else P["path"]
    PR = json.loads((OPT / "prune.json").read_text(encoding="utf-8"))
    SM = json.loads((OPT / "sumps_OPTCS2.json").read_text(encoding="utf-8"))["stations"]
    rej = sum(json.loads((OPT / "summary.json").read_text(encoding="utf-8"))["rejected"].values())   # 不可建的候选数（rejected.json 是原因清单，条数不等于个数）
    first = {}
    for p in P:
        for i in p.get("chosen", []):
            first.setdefault(i, p["step"])
    dom = gpd.read_file(ROOT / "out/dataqc/RD-A/input/rda_domain.geojson").to_crs(32640)
    sd = ROOT / "out/rda/sitedata.gpkg"
    roads = gpd.read_file(sd, layer="road_centre").to_crs(32640)
    sea = gpd.read_file(sd, layer="sea").to_crs(32640)
    H = gpd.read_file(ROOT / "console/data/hotspots.geojson").to_crs(32640); H = H[H.hotspot_id.isin(HS)]
    import shapely
    from shapely.geometry import shape
    ex = shapely.union_all([shape(f["geometry"]) for f in json.loads((OPT / "exclude_OPTCS.geojson").read_text(encoding="utf-8"))["features"]])
    return cand, P, PR, SM, rej, first, dom, roads, sea, H, ex


def depth(state, ex):
    import shapely
    z = np.load(C2D / f"RDA_{STORM}_{state}_2d.npz")
    xy = z["xy"].astype(float); h = z["hmax"].astype(float)
    ok = ~shapely.contains_xy(ex, xy[:, 0], xy[:, 1])
    meta = json.loads((C2D / f"pilot_RDA_{STORM}_{state}.json").read_text(encoding="utf-8"))
    from matplotlib.path import Path as MPath
    for pd in meta.get("ponds") or []:                                   # 池 / 塘占地不算积水（同验收口径）
        fp = pd.get("footprint") or {}
        polys = fp.get("coordinates", []) if fp.get("type") == "MultiPolygon" else [fp.get("coordinates", [])]
        for poly in polys:
            if poly and len(poly[0]) >= 3:
                ok &= ~MPath(np.array(poly[0])[:, :2]).contains_points(xy)
    return xy[ok], h[ok]


def base(ax, dom, roads, sea, H, label=True):
    b = dom.total_bounds; pad = 120
    sea.plot(ax=ax, color="#DCEBF2", lw=0)
    roads.plot(ax=ax, color="#CBD2D9", lw=0.5)
    dom.boundary.plot(ax=ax, color="#475569", lw=1.2, ls="--")
    ax.scatter(H.geometry.x, H.geometry.y, marker="*", s=170, c="#DC2626", edgecolors="white", linewidths=0.8, zorder=8)
    ax.set_xlim(b[0] - pad, b[2] + pad); ax.set_ylim(b[1] - pad, b[3] + pad)
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.set_xlabel(""); ax.set_ylabel("")
    for s in ax.spines.values():
        s.set_visible(False)


def fig1(D, path):
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    cand, P, PR, SM, rej, first, dom, roads, sea, H, ex = D
    fig, axs = plt.subplots(2, 2, figsize=(20, 11.6), facecolor="white"); axs = axs.ravel()
    T = ["① 所有能建的位置", "② 一轮一轮挑：每轮只加最划算的", "③ 回头瘦身后的最终方案", "④ 积水范围明显缩小"]
    for ax, t in zip(axs, T):
        base(ax, dom, roads, sea, H); ax.set_title(t, fontsize=22, fontweight="bold", color=NAVY, pad=14)
    pts = np.array([xy_of(c) for c in cand.values()])
    axs[0].scatter(pts[:, 0], pts[:, 1], s=9, c="#16A34A", alpha=.55, lw=0, zorder=5)
    axs[0].text(0.5, -0.04, f"绿点：{len(cand)} 个能建的候选\n（另有 {rej} 个不符合手册 / 现场条件，已排除）", transform=axs[0].transAxes, fontsize=16, color=NAVY,
                va="top", ha="center")
    ids = sorted(first, key=first.get); q = np.array([xy_of(cand[i]) for i in ids]); r = np.array([first[i] for i in ids])
    sc = axs[1].scatter(q[:, 0], q[:, 1], s=90, c=r, cmap="viridis", vmin=1, vmax=20, edgecolors="white", linewidths=.6, zorder=6)
    cax = axs[1].inset_axes([0.2, -0.09, 0.6, 0.045]); cb = fig.colorbar(sc, cax=cax, orientation="horizontal", ticks=[1, 5, 10, 15, 20])
    cb.set_label("第几轮选中（颜色越深越早）", fontsize=14); cb.ax.tick_params(labelsize=12)
    fin = [cand[i] for i in PR["final_ids"]]
    for k in ("tank_gravity", "tank_pumped", "ps_cluster"):
        z = np.array([xy_of(c) for c in fin if c["kind"] == k])
        if len(z):
            axs[2].scatter(z[:, 0], z[:, 1], s=110, c=KCOL[k], edgecolors="white", linewidths=.8, zorder=6)
    z = np.array([[s["ps_x"], s["ps_y"]] for s in SM]); axs[2].scatter(z[:, 0], z[:, 1], s=130, c=KCOL["surface_ps"], marker="s", edgecolors="white", linewidths=.8, zorder=6)
    axs[2].legend(handles=[Line2D([], [], marker="s" if k == "surface_ps" else "o", ls="", ms=11, color=c, label=KLAB[k]) for k, c in KCOL.items()],
                  loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=2, fontsize=13, frameon=False)
    xb, hb = depth("L2P", ex); xa, ha = depth("OPTCS2", ex)
    mb, ma = hb >= 0.15, ha >= 0.15
    axs[3].scatter(xb[mb, 0], xb[mb, 1], s=7, c="#F59E0B", lw=0, alpha=.9, zorder=5, marker="s")
    axs[3].scatter(xa[ma, 0], xa[ma, 1], s=7, c="#1D4ED8", lw=0, alpha=.95, zorder=6, marker="s")
    axs[3].legend(handles=[Line2D([], [], marker="s", ls="", ms=11, color="#F59E0B", label="客户计划下积水 ≥15 cm"),
                           Line2D([], [], marker="s", ls="", ms=11, color="#1D4ED8", label="我们的方案后仍 ≥15 cm")], loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=1, fontsize=13, frameon=False)
    axs[3].text(0.98, 0.98, "设计标准雨\n61 mm / 24 h", transform=axs[3].transAxes, ha="right", va="top", fontsize=14, color=NAVY)
    fig.tight_layout(w_pad=2.0, h_pad=6.5); fig.savefig(path, dpi=150, facecolor="white", bbox_inches="tight"); plt.close(fig)


def fig2(D, path):
    import matplotlib.pyplot as plt
    cand, P, PR, SM, rej, first, dom, roads, sea, H, ex = D
    steps = [1, 3, 10, 20]
    byS = {p["step"]: p for p in P}
    fig = plt.figure(figsize=(24, 9.2), facecolor="white")
    gs = fig.add_gridspec(2, 4, height_ratios=[1, 1.15], hspace=.12, wspace=.06)
    for j, s in enumerate(steps):
        ax = fig.add_subplot(gs[0, j]); base(ax, dom, roads, sea, H)
        ch = byS[s]["chosen"]; new = set(byS[s]["chosen"]) - set(byS[steps[j - 1]]["chosen"] if j else [])
        for i in ch:
            x, y = xy_of(cand[i])
            ax.scatter(x, y, s=120 if i in new else 70, c=ORANGE if i in new else TEAL, edgecolors="white", linewidths=.7, zorder=7)
        ax.set_title(f"第 {s} 轮：已选 {len(ch)} 项", fontsize=22, fontweight="bold", color=NAVY)
        ax.text(0.03, 0.03, f"累计约 {byS[s]['cost_usd'] / 1e6:.0f} 百万美元", transform=ax.transAxes, fontsize=16, color=NAVY, bbox=dict(fc="white", ec="none", alpha=.85))
    ax = fig.add_subplot(gs[1, :])
    cost = [p["cost_usd"] / 1e6 for p in P]; pct = [100 * (1 - p["J"] / P[0]["J"]) for p in P]
    ax.plot(cost, pct, "-", color=TEAL, lw=4)
    ax.scatter(cost, pct, s=40, color=TEAL, zorder=4)
    for s in steps:
        c, v = byS[s]["cost_usd"] / 1e6, 100 * (1 - byS[s]["J"] / P[0]["J"])
        ax.scatter([c], [v], s=260, color=ORANGE, zorder=5, edgecolors="white", linewidths=1.5)
        ax.annotate(f"第 {s} 轮", (c, v), xytext=(0, -38), textcoords="offset points", ha="center", fontsize=16, color=ORANGE, fontweight="bold")
    pc, pv = PR["cost_final"] / 1e6, 100 * (1 - PR["J_final"] / P[0]["J"])
    ax.scatter([pc], [pv], s=320, marker="*", color=NAVY, zorder=6)
    ax.annotate(f"回头瘦身：同样效果只要 {pc:.0f} 百万美元", (pc, pv), xytext=(-20, 28), textcoords="offset points", ha="right", fontsize=16, color=NAVY, fontweight="bold")
    ax.set_xlabel("累计花费（百万美元，占位单价）", fontsize=16); ax.set_ylabel("热点附近溢流减少", fontsize=16)
    ax.set_ylim(0, 75); ax.set_xlim(-3, max(cost) * 1.04); ax.grid(alpha=.3); ax.tick_params(labelsize=13)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    for s_ in ("top", "right"):
        ax.spines[s_].set_visible(False)
    ax.text(0.99, 0.08, "前几轮花得少、减得多；越往后，每一份效果越贵", transform=ax.transAxes, ha="right", fontsize=18, color=TEAL, fontweight="bold")
    fig.savefig(path, dpi=150, facecolor="white", bbox_inches="tight"); plt.close(fig)


def fig3(D, path, ev):
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    cand, P, PR, SM, rej, first, dom, roads, sea, H, ex = D
    cm = LinearSegmentedColormap.from_list("w", ["#7DD3FC", "#2582EB", "#1D4ED8", "#4C1D95"])
    fig, axs = plt.subplots(1, 2, figsize=(22, 9), facecolor="white")
    for ax, st, t in zip(axs, ("L2P", "OPTCS2"), ("客户已有计划", "加上我们的方案")):
        base(ax, dom, roads, sea, H)
        xy, h = depth(st, ex); m = h >= 0.10
        sc = ax.scatter(xy[m, 0], xy[m, 1], s=9, c=np.clip(h[m], 0, 1.0), cmap=cm, vmin=0.1, vmax=1.0, lw=0, marker="s", zorder=5)
        n = sum(all(v["pass"].values()) for v in ev[st]["hotspots"].values())
        ax.set_title(f"{t}：11 个热点中 {n} 个达标", fontsize=24, fontweight="bold", color=NAVY if st == "OPTCS2" else ORANGE, pad=12)
    cb = fig.colorbar(sc, ax=axs, orientation="horizontal", fraction=.04, pad=.04, shrink=.5); cb.set_label("最大水深（m）", fontsize=15); cb.ax.tick_params(labelsize=13)
    fig.savefig(path, dpi=150, facecolor="white", bbox_inches="tight"); plt.close(fig)


KEY = {}


def build_pptx(out, figs, ev):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    prs = Presentation(); prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    rgb = lambda h: RGBColor.from_string(h.lstrip("#"))   # noqa: E731

    def text(sl, x, y, w, h, s, size, color=NAVY, bold=False, align=PP_ALIGN.LEFT):
        tb = sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)); tf = tb.text_frame; tf.word_wrap = True
        for k, line in enumerate(s.split("\n")):
            p = tf.paragraphs[0] if k == 0 else tf.add_paragraph(); p.alignment = align
            r = p.add_run(); r.text = line; r.font.size = Pt(size); r.font.bold = bold; r.font.color.rgb = rgb(color); r.font.name = "Microsoft YaHei"
        return tb

    def page(title, sub, img, notes, foot, key=None):
        sl = prs.slides.add_slide(blank)
        bg = sl.background.fill; bg.solid(); bg.fore_color.rgb = rgb("#FFFFFF")
        text(sl, 0.5, 0.25, 12.3, 0.7, title, 30, NAVY, True)
        text(sl, 0.5, 0.95, 12.3, 0.5, sub, 16, "#4A5568")
        band = 5.25 - (1.0 if key else 0.0)                                  # 图的可用高度（下方留给关键结论）
        pic = sl.shapes.add_picture(str(img), Inches(0.5), Inches(1.55), width=Inches(12.33))
        if pic.height > Inches(band):
            r = Inches(band) / pic.height; pic.height = Inches(band); pic.width = int(pic.width * r); pic.left = int((prs.slide_width - pic.width) / 2)
        pic.top = int(Inches(1.55) + (Inches(band) - pic.height) / 2)
        if key:
            from pptx.enum.shapes import MSO_SHAPE
            bx = sl.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.5), Inches(1.55 + band + 0.1), Inches(12.33), Inches(0.85))
            bx.fill.solid(); bx.fill.fore_color.rgb = rgb("#0F766E"); bx.line.fill.background()
            tf = bx.text_frame; tf.word_wrap = True; p0 = tf.paragraphs[0]; p0.alignment = PP_ALIGN.CENTER
            r0 = p0.add_run(); r0.text = key; r0.font.size = Pt(20); r0.font.bold = True; r0.font.color.rgb = rgb("#FFFFFF"); r0.font.name = "Microsoft YaHei"
        text(sl, 0.5, 6.9, 12.3, 0.4, foot, 11, "#6B7280")
        sl.notes_slide.notes_text_frame.text = notes
        return sl

    n2 = sum(all(v["pass"].values()) for v in ev["OPTCS2"]["hotspots"].values()); n0 = sum(all(v["pass"].values()) for v in ev["L2P"]["hotspots"].values())
    nc, nf, cf = KEY["n_cand"], KEY["n_final"], KEY["cost_final"] / 1e8
    page("一张图看懂：我们怎样找出“能建、管用、又划算”的方案",
         "先排除不能建的 → 每轮只挑最划算的一项 → 回头删掉多余的 → 用一二维水动力模型验收",
         figs[0],
         "从左往右讲：① 绿点是所有符合 DMT 设计手册、行业经验和现场空间条件的可建位置；不满足的已经排除。② 我们不是一次定方案，而是一轮一轮挑：每轮把剩下的位置逐个用模型试算，只加“每花一美元减少积水最多”的那一项，颜色越深越早被选中。③ 最后回头检查，删掉多余的、换成更便宜的同类做法，得到最终方案。④ 橙色是客户已有计划下仍会积水的地方，蓝色是加上我们方案后剩下的：在设计标准雨下大部分消失了。红星是客户的 11 个历史积水点。",
         "红星 = 客户历史积水点；数据：RD-A 汇水区，WA-726 手册 821 条 + 行业经验 + Makani 空间数据；积水图为一二维水动力模型结果",
         key=f"{nc} 个可建位置  →  20 轮挑选  →  {nf} 项措施（约 {cf:.2f} 亿美元，占位单价）  →  设计标准暴雨下 {n2} / 11 个热点达标")
    page("为什么要一轮一轮地挑？",
         "地图：橙色 = 这一阶段新加的设施，绿色 = 之前已选的。曲线：越往后效果越难提升，它就是按预算分档的依据",
         figs[1],
         "上面四张图：第 1、3、10、20 轮时已选中的设施，橙色是这一阶段新加的。下面的曲线：横轴是累计花的钱，纵轴是热点附近的溢流减少了多少。前几轮花得少、减得多；第 10 轮以后曲线走平，再花很多钱只多减一点。星号是回头瘦身后的方案：效果几乎一样，钱少了三分之一以上。所以方案不是越大越好，客户可以按预算选在曲线的哪个位置。",
         "曲线为快速一维模型评估值（控制雨 96 mm），最终效果以一二维模型验收为准；造价为占位单价，只用于比较")
    page(f"结果：设计标准暴雨下，达标热点从 {n0} 个增加到 {n2} 个",
         "设计标准 = 10 年一遇 + 20% 气候变化（24 h 61 mm）；更极端的暴雨由 L3 应急调度兜底",
         figs[2],
         f"左边是客户已有计划，右边是加上我们的方案，同一场设计标准暴雨、同一个模型。颜色越深积水越深。客户计划下 11 个热点只有 {n0} 个达标，加上我们的方案后 {n2} 个达标。对于超过设计标准的极端暴雨，永久设施不可能全部扛住，剩余积水交给 L3 应急调度：系统会算出要派多少移动泵、从哪里出发、多久退干。具体投资多少，可以在“设计标准最小方案”和“关键点加强”之间按预算选择。",
         "达标 = 热点 200 m 内无 30 cm 以上积水、积水时长大幅减少、雨停 6 小时内退干、别处不变深；模型尚未率定，看相对差别")
    prs.save(out)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=str(ROOT / "out/交付/RDA_L2S_v3_20261025/客户汇报_优化方法一图看懂.pptx"))
    a = ap.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]; plt.rcParams["axes.unicode_minus"] = False
    out = pathlib.Path(a.out); fd = out.parent / "图" / "一图看懂"; fd.mkdir(parents=True, exist_ok=True)
    D = load()
    SM = D[3]; PRf = D[2]; cand = D[0]
    KEY.update(n_cand=len(cand), n_final=len(PRf["final_ids"]) + len(SM),
               cost_final=sum(cand[i]["cost_usd"] for i in PRf["final_ids"]) + sum(x["cost_usd"] for x in SM))
    ev = json.loads((OPT / f"eval2d_{STORM}_ex.json").read_text(encoding="utf-8"))["states"]
    figs = [fd / "1_一图看懂.png", fd / "2_一轮一轮挑.png", fd / "3_设计雨结果.png"]
    fig1(D, figs[0]); fig2(D, figs[1]); fig3(D, figs[2], ev)
    build_pptx(out, figs, ev)
    print("→", out); [print("→", f) for f in figs]


if __name__ == "__main__":
    main()
