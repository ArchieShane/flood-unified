"""RD-A 61 mm / 1 h 推荐方案（OPTA61PS，53 项）的「L2+suggested 优化过程」图 + 一份 .pptx（2026-10-28 用户要求，仿 rda_ppt_simple.py 的 96 mm 版）。

过程如实画：
  ① 754 个可建候选（RDA_96C 候选，手册 + 空间约束筛过）
  ② 一轮一轮挑（贪心 20 轮，控制雨 96 mm）→ 剪枝 53 项（OPTC，1.054 亿美元）
  ③ 按 AECOM 61 mm / 1 h 雨型再瘦身（RDA_A61P prune：删 7、换 6 → 46 项，9,034 万美元）
  ④ 加 7 座地面泵站（同 OPTCS2，2,837 万美元）→ 53 项，1.1871 亿美元
  ⑤ 一二维验收：A61D1 下客户计划 vs 推荐方案最大水深，11 个热点达标数
用法：python tools/rda_a61_process_figs.py [--out <pptx>]
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import rda_ppt_simple as R  # noqa: E402

A61, A61P = ROOT / "out/l2opt/RDA_A61", ROOT / "out/l2opt/RDA_A61P"
STORM = "A61D1"
R.STORM = STORM                      # depth() 读 A61D1 的二维结果
NAVY, TEAL, ORANGE, GREY = R.NAVY, R.TEAL, R.ORANGE, R.GREY
KCOL, KLAB = R.KCOL, R.KLAB
OUT = ROOT / "out/交付/RDA_61mm1h_推荐方案_20261027/客户汇报_61mm1h优化过程.pptx"


def load():
    D = R.load()
    PA = json.loads((A61P / "prune.json").read_text(encoding="utf-8"))
    ev = json.loads((A61 / f"eval2d_{STORM}_ex.json").read_text(encoding="utf-8"))["states"]
    return D, PA, ev


def npass(ev, st):
    return sum(all(v["pass"].values()) for v in ev[st]["hotspots"].values())


def legend_kinds(ax, counts, y=-0.02):
    from matplotlib.lines import Line2D
    h = [Line2D([], [], marker="s" if k == "surface_ps" else "o", ls="", ms=11, color=c, label=f"{KLAB[k]}  {counts.get(k, 0)} 处")
         for k, c in KCOL.items() if counts.get(k)]
    ax.legend(handles=h, loc="upper center", bbox_to_anchor=(0.5, y), ncol=2, fontsize=13, frameon=False)


def draw_final(ax, cand, PA, SM, s=110):
    fin = [cand[i] for i in PA["final_ids"]]
    counts = {}
    for k in ("tank_gravity", "tank_pumped", "ps_cluster"):
        z = np.array([R.xy_of(c) for c in fin if c["kind"] == k])
        counts[k] = len(z)
        if len(z):
            ax.scatter(z[:, 0], z[:, 1], s=s, c=KCOL[k], edgecolors="white", linewidths=.8, zorder=6)
    z = np.array([[x["ps_x"], x["ps_y"]] for x in SM]); counts["surface_ps"] = len(z)
    ax.scatter(z[:, 0], z[:, 1], s=s * 1.2, c=KCOL["surface_ps"], marker="s", edgecolors="white", linewidths=.8, zorder=6)
    return counts


def fig1(D, PA, ev, path):
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    cand, P, PR, SM, rej, first, dom, roads, sea, H, ex = D
    fig, axs = plt.subplots(2, 2, figsize=(20, 11.6), facecolor="white"); axs = axs.ravel()
    T = ["① 所有能建的位置", "② 一轮一轮挑：每轮只加最划算的", "③ 按 61 mm / 1 h 再瘦身 + 地面泵站", "④ 61 mm / 1 h 暴雨下积水范围明显缩小"]
    for ax, t in zip(axs, T):
        R.base(ax, dom, roads, sea, H); ax.set_title(t, fontsize=22, fontweight="bold", color=NAVY, pad=14)
    pts = np.array([R.xy_of(c) for c in cand.values()])
    axs[0].scatter(pts[:, 0], pts[:, 1], s=9, c="#16A34A", alpha=.55, lw=0, zorder=5)
    axs[0].text(0.5, -0.04, f"绿点：{len(cand)} 个能建的候选\n（另有 {rej} 个不符合手册 / 现场条件，已排除）", transform=axs[0].transAxes, fontsize=16, color=NAVY,
                va="top", ha="center")
    ids = sorted(first, key=first.get); q = np.array([R.xy_of(cand[i]) for i in ids]); r = np.array([first[i] for i in ids])
    sc = axs[1].scatter(q[:, 0], q[:, 1], s=90, c=r, cmap="viridis", vmin=1, vmax=20, edgecolors="white", linewidths=.6, zorder=6)
    cax = axs[1].inset_axes([0.2, -0.09, 0.6, 0.045]); cb = fig.colorbar(sc, cax=cax, orientation="horizontal", ticks=[1, 5, 10, 15, 20])
    cb.set_label("第几轮选中（颜色越深越早）", fontsize=14); cb.ax.tick_params(labelsize=12)
    counts = draw_final(axs[2], cand, PA, SM)
    rm = np.array([R.xy_of(cand[x["id"]]) for x in PA["removed"]] + [R.xy_of(cand[x["from"]]) for x in PA["swapped"]])
    axs[2].scatter(rm[:, 0], rm[:, 1], s=110, marker="x", c=GREY, linewidths=2.2, zorder=7)
    legend_kinds(axs[2], counts)
    axs[2].text(0.98, 0.98, f"灰 × = 瘦身时删掉 / 换掉的 {len(rm)} 项", transform=axs[2].transAxes, ha="right", va="top", fontsize=14, color="#4B5563",
                bbox=dict(fc="white", ec="none", alpha=.85))
    xb, hb = R.depth("L2P", ex); xa, ha = R.depth("OPTA61PS", ex)
    mb, ma = hb >= 0.15, ha >= 0.15
    axs[3].scatter(xb[mb, 0], xb[mb, 1], s=7, c="#F59E0B", lw=0, alpha=.9, zorder=5, marker="s")
    axs[3].scatter(xa[ma, 0], xa[ma, 1], s=7, c="#1D4ED8", lw=0, alpha=.95, zorder=6, marker="s")
    axs[3].legend(handles=[Line2D([], [], marker="s", ls="", ms=11, color="#F59E0B", label="客户计划下积水 ≥15 cm"),
                           Line2D([], [], marker="s", ls="", ms=11, color="#1D4ED8", label="推荐方案后仍 ≥15 cm")], loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=1,
                  fontsize=13, frameon=False)
    axs[3].text(0.98, 0.98, f"AECOM 10 年一遇 + 20 %\n61 mm / 1 h\n热点达标 {npass(ev, 'L2P')} → {npass(ev, 'OPTA61PS')} / 11", transform=axs[3].transAxes,
                ha="right", va="top", fontsize=14, color=NAVY, bbox=dict(fc="white", ec="none", alpha=.85))
    fig.tight_layout(w_pad=2.0, h_pad=6.5); fig.savefig(path, dpi=150, facecolor="white", bbox_inches="tight"); plt.close(fig)


def fig_slim(D, PA, path):
    """瘦身明细：地图（删 / 换）+ 造价瀑布。"""
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    cand, P, PR, SM, rej, first, dom, roads, sea, H, ex = D
    fig = plt.figure(figsize=(22, 9.6), facecolor="white")
    gs = fig.add_gridspec(1, 2, width_ratios=[1.15, 1], wspace=.08)
    ax = fig.add_subplot(gs[0, 0]); R.base(ax, dom, roads, sea, H)
    keep = [cand[i] for i in PR["final_ids"] if i in set(PA["final_ids"])]
    z = np.array([R.xy_of(c) for c in keep]); ax.scatter(z[:, 0], z[:, 1], s=70, c=TEAL, alpha=.55, edgecolors="white", linewidths=.5, zorder=5)
    z = np.array([R.xy_of(cand[x["id"]]) for x in PA["removed"]]); ax.scatter(z[:, 0], z[:, 1], s=150, marker="x", c="#DC2626", linewidths=2.6, zorder=7)
    for x in PA["swapped"]:
        (x0, y0), (x1, y1) = R.xy_of(cand[x["from"]]), R.xy_of(cand[x["to"]])
        ax.scatter([x0], [y0], s=120, marker="o", facecolors="none", edgecolors=GREY, linewidths=2, zorder=7)
        ax.scatter([x1], [y1], s=130, c=ORANGE, edgecolors="white", linewidths=.8, zorder=8)
        if abs(x1 - x0) + abs(y1 - y0) > 5:
            ax.annotate("", (x1, y1), (x0, y0), arrowprops=dict(arrowstyle="->", color=ORANGE, lw=1.8), zorder=8)
    ax.set_title("按 61 mm / 1 h 雨型回头检查：删掉 7 项、换便宜的 6 项", fontsize=21, fontweight="bold", color=NAVY, pad=12)
    ax.legend(handles=[Line2D([], [], marker="o", ls="", ms=10, color=TEAL, alpha=.6, label=f"保留（{len(keep)} 项）"),
                       Line2D([], [], marker="x", ls="", ms=12, mew=2.6, color="#DC2626", label=f"删掉：对这场雨几乎不起作用（{len(PA['removed'])} 项）"),
                       Line2D([], [], marker="o", ls="", ms=10, color=ORANGE, label=f"换成更便宜的同等做法（{len(PA['swapped'])} 项，灰圈 → 橙点）")],
              loc="upper center", bbox_to_anchor=(0.5, -0.01), ncol=1, fontsize=14, frameon=False)
    ax = fig.add_subplot(gs[0, 1])
    c96, rm = PR["cost_final"] / 1e6, sum(x["cost_usd"] for x in PA["removed"]) / 1e6
    sw = sum(x["from_cost"] - x["to_cost"] for x in PA["swapped"]) / 1e6
    smp = sum(x["cost_usd"] for x in SM) / 1e6; fin = PA["cost_final"] / 1e6 + smp
    steps = [("96 mm 方案\n管网部分 53 项", 0, c96, TEAL), ("删掉 7 项", c96 - rm, rm, "#DC2626"), ("换便宜 6 项", c96 - rm - sw, sw, ORANGE),
             ("瘦身后\n46 项", 0, PA["cost_final"] / 1e6, TEAL), ("+ 7 座\n地面泵站", PA["cost_final"] / 1e6, smp, KCOL["surface_ps"]), ("推荐方案\n53 项", 0, fin, NAVY)]
    for k, (lab, b, h, c) in enumerate(steps):
        ax.bar(k, h, bottom=b, color=c, width=.62, edgecolor="white")
        sign = {1: "−", 2: "−", 4: "+"}.get(k, "")
        ax.text(k, b + h + 1.5, f"{sign}{h:.1f}", ha="center", fontsize=15, fontweight="bold", color=c)
    ax.set_xticks(range(len(steps))); ax.set_xticklabels([s[0] for s in steps], fontsize=13)
    ax.set_ylabel("造价（百万美元，占位单价）", fontsize=15); ax.tick_params(axis="y", labelsize=12); ax.set_ylim(0, fin * 1.15); ax.grid(axis="y", alpha=.3)
    for s_ in ("top", "right"):
        ax.spines[s_].set_visible(False)
    ax.set_title("造价怎么变的", fontsize=21, fontweight="bold", color=NAVY, pad=12)
    fig.savefig(path, dpi=150, facecolor="white", bbox_inches="tight"); plt.close(fig)


def fig_final(D, PA, path):
    """最终 53 项分布（单图，大）。"""
    import matplotlib.pyplot as plt
    cand, P, PR, SM, rej, first, dom, roads, sea, H, ex = D
    fig, ax = plt.subplots(figsize=(14, 11), facecolor="white"); R.base(ax, dom, roads, sea, H)
    counts = draw_final(ax, cand, PA, SM, s=170)
    legend_kinds(ax, counts)
    cost = PA["cost_final"] + sum(x["cost_usd"] for x in SM)
    ax.set_title(f"61 mm / 1 h 推荐方案：{sum(counts.values())} 项措施，约 {cost / 1e6:.1f} 百万美元（占位单价）", fontsize=20, fontweight="bold", color=NAVY, pad=12)
    ax.text(0.02, 0.02, "红星 = 客户历史积水点", transform=ax.transAxes, fontsize=13, color="#DC2626", bbox=dict(fc="white", ec="none", alpha=.85))
    fig.savefig(path, dpi=150, facecolor="white", bbox_inches="tight"); plt.close(fig)


def fig_result(D, ev, path):
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    cand, P, PR, SM, rej, first, dom, roads, sea, H, ex = D
    cm = LinearSegmentedColormap.from_list("w", ["#7DD3FC", "#2582EB", "#1D4ED8", "#4C1D95"])
    fig, axs = plt.subplots(1, 2, figsize=(22, 9), facecolor="white")
    for ax, st, t in zip(axs, ("L2P", "OPTA61PS"), ("客户已有计划", "加上推荐方案")):
        R.base(ax, dom, roads, sea, H)
        xy, h = R.depth(st, ex); m = h >= 0.10
        sc = ax.scatter(xy[m, 0], xy[m, 1], s=9, c=np.clip(h[m], 0, 1.0), cmap=cm, vmin=0.1, vmax=1.0, lw=0, marker="s", zorder=5)
        ax.set_title(f"{t}：11 个热点中 {npass(ev, st)} 个达标", fontsize=24, fontweight="bold", color=NAVY if st != "L2P" else ORANGE, pad=12)
    cb = fig.colorbar(sc, ax=axs, orientation="horizontal", fraction=.04, pad=.04, shrink=.5); cb.set_label("最大水深（m）", fontsize=15); cb.ax.tick_params(labelsize=13)
    fig.savefig(path, dpi=150, facecolor="white", bbox_inches="tight"); plt.close(fig)


def build_pptx(out, figs, ev, K):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.enum.shapes import MSO_SHAPE
    prs = Presentation(); prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)
    rgb = lambda h: RGBColor.from_string(h.lstrip("#"))   # noqa: E731

    def text(sl, x, y, w, h, s, size, color=NAVY, bold=False):
        tf = sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame; tf.word_wrap = True
        for k, line in enumerate(s.split("\n")):
            p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
            r = p.add_run(); r.text = line; r.font.size = Pt(size); r.font.bold = bold; r.font.color.rgb = rgb(color); r.font.name = "Microsoft YaHei"

    def page(title, sub, img, notes, foot, key=None):
        sl = prs.slides.add_slide(prs.slide_layouts[6])
        text(sl, 0.5, 0.25, 12.3, 0.7, title, 28, NAVY, True); text(sl, 0.5, 0.95, 12.3, 0.5, sub, 15, "#4A5568")
        band = 5.25 - (1.0 if key else 0.0)
        pic = sl.shapes.add_picture(str(img), Inches(0.5), Inches(1.55), width=Inches(12.33))
        if pic.height > Inches(band):
            r = Inches(band) / pic.height; pic.height = Inches(band); pic.width = int(pic.width * r)
        pic.left = int((prs.slide_width - pic.width) / 2); pic.top = int(Inches(1.55) + (Inches(band) - pic.height) / 2)
        if key:
            bx = sl.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.5), Inches(1.55 + band + 0.1), Inches(12.33), Inches(0.85))
            bx.fill.solid(); bx.fill.fore_color.rgb = rgb(TEAL); bx.line.fill.background()
            p0 = bx.text_frame.paragraphs[0]; bx.text_frame.word_wrap = True; p0.alignment = PP_ALIGN.CENTER
            r0 = p0.add_run(); r0.text = key; r0.font.size = Pt(19); r0.font.bold = True; r0.font.color.rgb = rgb("#FFFFFF"); r0.font.name = "Microsoft YaHei"
        text(sl, 0.5, 6.9, 12.3, 0.4, foot, 11, "#6B7280")
        sl.notes_slide.notes_text_frame.text = notes

    n0, n1 = npass(ev, "L2P"), npass(ev, "OPTA61PS")
    FOOT = "红星 = 客户 11 个历史积水点；RD-A 汇水区；造价为占位单价，只用于比较；积水图为一二维水动力模型（15 m 网格）结果"
    page("一张图看懂：61 mm / 1 h 推荐方案是怎么挑出来的",
         "先排除不能建的 → 每轮只挑最划算的一项 → 按 61 mm / 1 h 雨型回头瘦身 → 补地面泵站 → 一二维水动力模型验收",
         figs[0],
         "① 绿点是所有符合 DMT 设计手册（WA-726）和现场空间条件的可建位置。② 一轮一轮挑：每轮把剩下的位置逐个用模型试算，只加每花一美元减少积水最多的一项。"
         "③ 再按 AECOM 的 61 mm / 1 h 雨型回头检查，删掉对这场雨几乎不起作用的、换成更便宜的同等做法，并补上洼地的地面泵站。④ 橙色是客户计划下仍积水 ≥15 cm 的地方，蓝色是推荐方案后剩下的。",
         FOOT, key=f"{K['n_cand']} 个可建位置 → 20 轮挑选 → 瘦身 {K['n_net']} 项 + {K['n_sump']} 座地面泵站 = {K['n_final']} 项（约 {K['cost'] / 1e6:.1f} 百万美元）→ {n1} / 11 热点达标")
    page("为什么要一轮一轮地挑？",
         "地图：橙色 = 这一阶段新加的设施，绿色 = 之前已选的。曲线：越往后效果越难提升，它是按预算分档的依据",
         figs[1],
         "这一步用的是 96 mm 控制雨，先找出在更强暴雨下也有效的设施组合；下一页再按 61 mm / 1 h 雨型删减。曲线横轴是累计造价，纵轴是热点附近溢流减少比例；前几轮花得少、减得多，后面越来越贵。",
         "曲线为一维模型快速评估值（控制雨 96 mm），最终效果以一二维模型验收为准")
    page("按 61 mm / 1 h 雨型回头瘦身",
         f"删掉 {len(K['PA']['removed'])} 项、换便宜 {len(K['PA']['swapped'])} 项：管网部分从 {K['c96'] / 1e6:.1f} 降到 {K['PA']['cost_final'] / 1e6:.1f} 百万美元，效果基本不变；再加 {K['n_sump']} 座地面泵站",
         figs[2],
         "回头检查时逐项试删：拿掉某项后热点附近溢流几乎不变，就删掉；能用更便宜的同类做法（例如用重力调蓄池代替泵站）达到同样效果，就替换。地面泵站解决的是管网接不进去的洼地积水，管网措施替代不了。",
         "瘦身判据：A61D1 下热点附近溢流增加不超过 1 %；造价为占位单价")
    page(f"推荐方案：{K['n_final']} 项措施分布", "按类型着色：地下调蓄池、井边泵站 + 地下池、泵站 → 排海口、地面泵站（洼地）",
         figs[3], "每一项的位置、规模、造价和对应热点见同目录的设施清单 Excel。", FOOT)
    page(f"结果：61 mm / 1 h 暴雨下，达标热点从 {n0} 个增加到 {n1} 个",
         "AECOM 口径：10 年一遇 + 20 % 气候变化，1 h 60.7 mm（AECOM 雨型）；剩余积水由 L3 应急调度承接",
         figs[4],
         f"左边是客户已有计划，右边是加上推荐方案，同一场雨、同一个模型。客户计划下 11 个热点只有 {n0} 个达标，加上推荐方案后 {n1} 个达标。",
         "达标 = 热点 200 m 内无 30 cm 以上积水、积水面积×时长 ≤ 客户计划的 10 %、雨停 6 小时内退到 10 cm 以下、别处不变深（判据 A–D，待客户确认）")
    prs.save(out)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=str(OUT)); a = ap.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]; plt.rcParams["axes.unicode_minus"] = False
    out = pathlib.Path(a.out); fd = out.parent / "图" / "优化过程"; fd.mkdir(parents=True, exist_ok=True)
    D, PA, ev = load()
    cand, PR, SM = D[0], D[2], D[3]
    K = dict(PA=PA, n_cand=len(cand), n_net=len(PA["final_ids"]), n_sump=len(SM), n_final=len(PA["final_ids"]) + len(SM),
             cost=PA["cost_final"] + sum(x["cost_usd"] for x in SM), c96=PR["cost_final"])
    figs = [fd / "1_一图看懂_61mm1h.png", fd / "2_一轮一轮挑.png", fd / "3_按61mm1h瘦身.png", fd / "4_推荐方案53项分布.png", fd / "5_61mm1h结果对比.png"]
    fig1(D, PA, ev, figs[0]); R.fig2(D, figs[1]); fig_slim(D, PA, figs[2]); fig_final(D, PA, figs[3]); fig_result(D, ev, figs[4])
    build_pptx(out, figs, ev, K)
    print("→", out); [print("→", f) for f in figs]
    print(K["n_final"], round(K["cost"] / 1e6, 2), npass(ev, "L2P"), npass(ev, "OPTA61PS"))


if __name__ == "__main__":
    main()
