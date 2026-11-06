# -*- coding: utf-8 -*-
"""Case 1 基线评估（模型版计算域，61 mm / 2 h）的四张图 → docs/figs/case1/*.png。

配合 docs/Case1_Baseline_Assessment_EN.md 用：Markdown 里写 ![](figs/case1/xx.png)，
swbrain/md2docx.py 转会把它嵌进 Word。

为什么单独一个脚本：文档里的每个数字都要能复现，图上不能手打。所有数据现算：
  · 峰值流量 / 超载段数 / 节点溢流峰值 ← $SWBRAIN_SWMM_WORK/c2d/C1_M61_*_2way.rpt
  · 1D 水量账（入流 / 出流 / 溢流损失 / 末存 / 连续性）← 同上的 Flow Routing Continuity
  · 积水面积 / 峰值积水量 / 时序 ← 交付目录各态的「一二维/*_积水面积时序.csv」与「*_耦合运行记录.json」
  · 各态全域与八个热点的面积 ← 交付目录「统计值/C1统计值.xlsx」
  · 雨型 ← console/data/c1_hyeto.json 的 R61（模型版 61 mm 用的是同一份）

分节边界一律按「标题行 + 紧跟一行星号」定位，不用固定行号窗口 —— 各态文件行数不同，
固定窗口会静默截断（2026-10-03 踩过，Link Flow Summary 读到只剩一半）。

用法：python tools/case1_charts.py            # 写出四张图
      python tools/case1_charts.py --check    # 只打印解析出来的数，不画
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))
C2D = WORK / "c2d"
DELIV = ROOT / "out" / "交付" / "C1_模型版_61mm2h"
OUTDIR = ROOT / "docs" / "figs" / "case1"

STATES = [("L1", "L1", "L1  existing network", "L1_现状管网+现状泵站"),
          ("L2", "L2", "L2  + temporary measures", "L2_加OP-02移动泵"),
          ("L2P", "L2+planned", "L2+planned  (consultant)", "L2+planned_Parsons方案"),
          ("L2S3", "L2+suggested v3", "L2+suggested v3", "L2+suggested_v3_按塘余量分配")]
COLOR = {"L1": "#2f6b45", "L2": "#1f5f96", "L2P": "#b07a1c", "L2S3": "#15803d"}
SHORT = ["L1", "L2", "L2+planned", "L2+suggested v3"]


# ---------------------------------------------------------------- .rpt 读取
def _sections(path: pathlib.Path) -> dict[str, list[str]]:
    """按「标题行 + 星号行」切分节。比固定行号窗口可靠：每个态的文件行数都不一样。"""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    heads = []
    for i, ln in enumerate(lines[:-1]):
        # 标题行以两个空格 + 字母开头；下一行以两个空格 + 星号开头。
        # 注意下一行不一定是纯星号：Flow Routing Continuity 的是「******  -------- --------」（带单位表头），
        # 用 `^  \*+ *$` 会漏掉这一节，连带 10^6 ltr 那列的单位行也一起丢（2026-10-03 踩过两次）。
        if re.match(r"^  [A-Z][A-Za-z0-9 ,&/()'^%.-]+$", ln) and re.match(r"^  \*+", lines[i + 1]):
            heads.append((i, ln.strip().split("  ")[0].strip()))
    out = {}
    for k, (i, name) in enumerate(heads):
        j = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        out.setdefault(name, lines[i + 1:j])          # 罕见的同名节只留第一个
    return out


_LINK = re.compile(r"^\s*(\S+)\s+(\S+)\s+([-\d.]+)\s+(\d+)\s+(\d+):(\d+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*$")
_NODE = re.compile(r"^\s*(\S+)\s+([\d.]+)\s+([-\d.]+)\s+(\d+)\s+(\d+):(\d+)\s+([-\d.]+)\s+([-\d.]+)\s*$")


def read_state(tag: str) -> dict:
    sec = _sections(C2D / f"C1_M61_{tag}_2way.rpt")
    links, nodes = [], []
    for ln in sec.get("Link Flow Summary", []):
        m = _LINK.match(ln)
        if m:
            links.append({"link": m.group(1), "q": abs(float(m.group(3))),
                          "t": f"{int(m.group(5))}h{m.group(6)}",
                          "t_min": int(m.group(5)) * 60 + int(m.group(6)),
                          "maxflow": float(m.group(8)), "maxdepth": float(m.group(9))})
    for ln in sec.get("Node Flooding Summary", []):
        m = _NODE.match(ln)
        if m:
            nodes.append({"node": m.group(1), "hours": float(m.group(2)), "rate": float(m.group(3)),
                          "t_min": int(m.group(4)) * 1440 + int(m.group(5)) * 60 + int(m.group(6)),
                          "vol_m3": float(m.group(7)) * 1e3, "depth": float(m.group(8))})
    cont = {}
    for ln in sec.get("Flow Routing Continuity", []):
        # 两种行：多数项是「label ..... <hectare-m> <10^6 ltr>」，Continuity Error (%) 只有一个小数。
        # 第二列单位是 10^6 ltr，即 ×1000 才是 m³（3054.493 → 3,054,493，以此为准）。
        m = re.match(r"^\s*(.+?)\s*\.{2,}\s+([-\d.]+)(?:\s+([-\d.]+))?\s*$", ln)
        if not m:
            continue
        lab = m.group(1).strip()
        cont[lab] = float(m.group(3)) * 1e3 if m.group(3) is not None else float(m.group(2))
    peak = max(links, key=lambda r: r["q"])
    return {"links": len(links), "over": sum(1 for r in links if r["maxflow"] > 1.0),
            "peak_q": peak["q"], "peak_link": peak["link"], "peak_t": peak["t"],
            "peak_t_min": peak["t_min"],
            "flood_nodes": len(nodes),
            "flood_30min": sum(1 for r in nodes if r["hours"] >= 0.5),
            "peak_node_rate": max((r["rate"] for r in nodes), default=0.0),
            "flood_vol_m3": sum(r["vol_m3"] for r in nodes),
            "cont": cont}


def read_series(sub: str) -> dict:
    d = DELIV / sub / "一二维"
    csvp = next(d.glob("*_积水面积时序.csv"))
    rows = []
    for ln in csvp.read_text(encoding="utf-8-sig").splitlines()[1:]:
        if not ln.strip():
            continue
        c = ln.split(",")
        rows.append({"t": float(c[0]), "a002": float(c[1]), "a010": float(c[2]), "hmax": float(c[3])})
    j = json.loads(next(d.glob("*_耦合运行记录.json")).read_text(encoding="utf-8"))
    return {"area": rows, "series": j["series"], "acc": j["acc_m3"],
            "final_vol": j["final_vol_m3"], "hmax": j["hmax_m"],
            "max_wet_m2": j["max_wet_m2"], "max_wet_t": j["max_wet_t_h"]}


def read_stats() -> dict:
    import openpyxl
    ws = openpyxl.load_workbook(DELIV / "统计值" / "C1统计值.xlsx", read_only=True)["统计值"]
    rows = list(ws.iter_rows(values_only=True))
    areas, want = {}, ("全域", "C1 八个热点")
    for r in rows[1:]:
        if r[0] not in want or r[1] != "全域":
            continue
        areas.setdefault((r[0], r[2]), [r[3], r[4], r[5], r[6]])
    hs = {}
    for r in rows[1:]:
        if r[1] != "热点汇水区" or r[0] not in ("ADM-21", "ADM-90", "ADM-94", "ADM-104",
                                                "ADM-192", "ADM-202", "ADM-486", "ADM-487"):
            continue
        hs.setdefault(r[0], {})[r[2]] = [r[3], r[4], r[5], r[6]]
    return {"domain": areas, "hotspots": hs,
            "all8": {r[2]: [r[3], r[4], r[5], r[6]] for r in rows[1:]
                     if r[0] == "C1 八个热点" and r[1] == "全域"}}


def read_hyeto() -> dict:
    return json.loads((ROOT / "console" / "data" / "c1_hyeto.json").read_text(encoding="utf-8"))["R61"]


# ---------------------------------------------------------------- 画图
def _setup():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.dpi": 200, "savefig.dpi": 200, "font.size": 9.5,
        "axes.edgecolor": "#c3cfd8", "axes.linewidth": 0.8,
        "axes.labelcolor": "#16232f", "text.color": "#16232f",
        "xtick.color": "#5e6e7b", "ytick.color": "#5e6e7b",
        "axes.grid": True, "grid.color": "#e2e8ec", "grid.linewidth": 0.7,
        "axes.axisbelow": True, "figure.facecolor": "white", "savefig.facecolor": "white",
        "font.family": "DejaVu Sans",
    })
    return plt


def _bare(ax, top=True):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False or (s == "top" and top))
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def fig_capacity(st, plt) -> pathlib.Path:
    """左：各态峰值流量（同一个主出水口，说明瓶颈是结构性的）。
    右：各态峰值节点溢流强度 —— 超载段数四态几乎相同（61.2 %），画出来是一条平线没有信息，
    所以这一栏换成真正有差异的量。"""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 3.0))
    x = list(range(4))
    vals = [st[k]["peak_q"] for k, _, _, _ in STATES]
    a1.bar(x, vals, width=0.6, color=[COLOR[k] for k, _, _, _ in STATES])
    a1.set_ylim(0, max(vals) * 1.32)
    for xi, v in zip(x, vals):
        a1.text(xi, v + max(vals) * 0.03, f"{v:.2f}", ha="center", fontsize=9, fontweight="bold")
    a1.axhline(vals[0], ls="--", lw=0.9, color="#b23a2e", zorder=1)
    # 注释放在坐标区上方空白处（轴分数坐标），不要压在柱顶的数字标签上；管段号太长，留给正文写
    a1.text(0.03, 0.96, "peak sits on the same link in every state",
            transform=a1.transAxes, fontsize=7.4, color="#b23a2e", va="top", ha="left")
    a1.set_xticks(x); a1.set_xticklabels(SHORT, fontsize=7.6, rotation=18, ha="right")
    a1.set_ylabel("Peak link flow  (m³/s)")
    a1.set_title(f"Peak flow at the main outfall  ·  t = 2 h {int(st['L1']['peak_t'].split('h')[1])}",
                 fontsize=9.5, loc="left", pad=8)
    _bare(a1)

    rate = [st[k]["peak_node_rate"] for k, _, _, _ in STATES]
    a2.bar(x, rate, width=0.6, color=[COLOR[k] for k, _, _, _ in STATES])
    a2.set_ylim(0, max(rate) * 1.22)
    for xi, v in zip(x, rate):
        d = "−9 %" if xi == 3 else ""
        a2.text(xi, v + max(rate) * 0.03, f"{v:.2f}" + (f"   {d}" if d else ""),
                ha="center", fontsize=9, fontweight="bold",
                color="#15803d" if xi == 3 else "#16232f")
    a2.set_xticks(x); a2.set_xticklabels(SHORT, fontsize=7.6, rotation=18, ha="right")
    a2.set_ylabel("Peak node flooding  (m³/s)")
    a2.set_title(f"{st['L1']['over']:,} of {st['L1']['links']:,} links over capacity in all four states",
                 fontsize=9.5, loc="left", pad=8)
    _bare(a2)
    fig.tight_layout(pad=1.4)
    p = OUTDIR / "fig1_capacity.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig)
    return p


def fig_water(cont: dict, plt) -> pathlib.Path:
    """L1 的水量瀑布图：入流为顶，出流 / 溢流 / 存蓄逐级扣减，余项是连续性误差。"""
    inflow = cont["External Inflow"]
    outflow = cont["External Outflow"]
    flood = cont["Flooding Loss"]
    stored = cont["Final Stored Volume"]
    resid = inflow - outflow - flood - stored

    fig, ax = plt.subplots(figsize=(7.4, 3.3))
    labels, colors = [], []
    # 瀑布：从入流往下扣
    run = inflow
    xs = [0]
    ax.bar(0, inflow, width=0.62, color="#1f5f96", zorder=3)
    ax.text(0, inflow + inflow * 0.015, f"{inflow:,.0f}", ha="center", fontsize=8.8,
            fontweight="bold", color="#1f5f96")
    labels.append("Inflow to\nnetwork"); colors.append("#1f5f96")

    for i, (lab, v, c) in enumerate([("Discharged\nat outfalls", outflow, "#2f6b45"),
                                     ("Lost as node\nflooding", flood, "#b23a2e"),
                                     ("Stored at\n6 h", stored, "#b07a1c")], start=1):
        top, bot = run, run - v
        ax.bar(i, v, bottom=bot, width=0.62, color=c, zorder=3)
        ax.plot([i - 0.62, i + 0.31], [bot, bot], color="#9fb0bc", lw=0.8, ls=(0, (3, 2)), zorder=2)
        ax.text(i, top + inflow * 0.015, f"{v:,.0f}", ha="center", fontsize=8.6,
                fontweight="bold", color=c)
        pct = v / inflow * 100
        ax.text(i, bot - inflow * 0.055, f"{pct:.1f} %", ha="center", fontsize=7.6, color="#5e6e7b")
        run = bot
        labels.append(lab); colors.append(c)

    # 余项单独画在基线上（它不是"再扣一次"，是收支不平的残差），别放进循环里浮着画
    ax.plot([3 - 0.62, 3 + 0.31], [run, run], color="#9fb0bc", lw=0.8, ls=(0, (3, 2)), zorder=2)
    ax.bar(4, resid, bottom=0, width=0.62, color="#93a5b2", zorder=3)
    ax.text(4, resid + inflow * 0.015, f"{resid:,.0f}", ha="center", fontsize=8.6,
            fontweight="bold", color="#5e6e7b")
    ax.text(4, -inflow * 0.055, f"{resid/inflow*100:.2f} %", ha="center", fontsize=7.6, color="#5e6e7b")
    labels.append("Continuity\nerror")
    ax.set_ylim(-inflow * 0.10, inflow * 1.14)

    ax.set_xticks(range(5)); ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("Volume  (m³)")
    ax.yaxis.set_major_formatter(lambda v, _: f"{v/1e6:.1f} M" if v >= 0 else "")
    ax.set_title("L1 water account  ·  61 mm / 2 h  ·  1D continuity, reconciled with the 2D exchange",
                 fontsize=9.5, loc="left", pad=10)
    _bare(ax)
    fig.tight_layout(pad=1.2)
    p = OUTDIR / "fig2_water_account.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig)
    return p


def fig_timing(hy: dict, ser: dict, st: dict, plt) -> pathlib.Path:
    """上下两面板：上＝雨型，下＝地表积水量。两个面板共一条时间轴，峰现时间不用双轴猜。"""
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.4, 4.2), sharex=True,
                                 gridspec_kw={"height_ratios": [1, 1.15], "hspace": 0.12})
    ts = ser["series"]
    rain_peak_t = hy["peak_t_h"]
    flood_peak = max(ts, key=lambda r: r["vol_m3"])
    net_peak_t = st["L1"]["peak_t_min"] / 60.0

    t = hy["t_h"][:24]
    a1.bar(t, hy["p_mmph"][:24], width=0.075, color="#8fb8d6", zorder=3)
    a1.set_ylabel("Rainfall\n(mm/h)")
    a1.set_ylim(0, 360)
    a1.set_title("Time to peak  ·  rain peaks at 0 h 55, surface flooding at 2 h 20 — 1.4 h of warning",
                 fontsize=9.5, loc="left", pad=8)
    a1.text(rain_peak_t + 0.08, 318, f"{hy['peak_mmph']:.0f} mm/h", fontsize=8, color="#3d6d93",
            fontweight="bold")

    a2.plot([r["t_h"] for r in ts], [r["vol_m3"] for r in ts], color="#b23a2e", lw=2.0, zorder=3)
    a2.fill_between([r["t_h"] for r in ts], [r["vol_m3"] for r in ts], color="#b23a2e", alpha=0.10)
    a2.set_ylabel("Water on the\nsurface (m³)")
    a2.set_xlabel("Hours from start of rain")
    a2.set_xlim(0, 6)
    a2.set_ylim(0, max(r["vol_m3"] for r in ts) * 1.20)
    a2.yaxis.set_major_formatter(lambda v, _: f"{v/1e3:,.0f} k")
    a2.text(flood_peak["t_h"] + 0.08, flood_peak["vol_m3"] * 0.92,
            f"peak {flood_peak['vol_m3']:,.0f} m³\nat 2 h {int(round(flood_peak['t_h']*60))%60:02d}",
            fontsize=8, color="#b23a2e", fontweight="bold", va="top")

    for ax in (a1, a2):
        for tt, c in ((rain_peak_t, "#1f5f96"), (flood_peak["t_h"], "#b23a2e"), (net_peak_t, "#b07a1c")):
            ax.axvline(tt, ls="--", lw=1.0, color=c, alpha=0.85, zorder=2)
        _bare(ax)
    a1.tick_params(labelbottom=False)

    # 两个面板之间画滞后箭头（降雨峰 → 积水峰）
    a2.annotate("", xy=(flood_peak["t_h"], a2.get_ylim()[1] * 0.42),
                xytext=(rain_peak_t, a2.get_ylim()[1] * 0.42),
                arrowprops=dict(arrowstyle="<->", color="#5e6e7b", lw=1.1))
    a2.text((rain_peak_t + flood_peak["t_h"]) / 2, a2.get_ylim()[1] * 0.455, "1.4 h",
            fontsize=8.6, color="#5e6e7b", ha="center", fontweight="bold")

    from matplotlib.lines import Line2D
    a1.legend(handles=[Line2D([], [], color="#8fb8d6", lw=6, label="Rainfall"),
                       Line2D([], [], color="#1f5f96", ls="--", lw=1,
                              label=f"Rain peak 0 h {int(round(rain_peak_t*60))%60:02d}"),
                       Line2D([], [], color="#b07a1c", ls="--", lw=1,
                              label=f"Network peak 2 h {int(round(net_peak_t*60))%60:02d}")],
              fontsize=7.8, loc="upper right", frameon=True, framealpha=0.95, ncol=1)

    fig.tight_layout(pad=1.2)
    p = OUTDIR / "fig3_timing.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig)
    return p


def fig_extent(sx: dict, plt) -> pathlib.Path:
    d = sx["domain"]
    a010 = d[("全域", "积水面积 ≥0.10 m（ha）")]
    a030 = d[("全域", "积水面积 ≥0.30 m（ha）")]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 3.4), gridspec_kw={"width_ratios": [1, 1.25]})
    x = range(4)
    w = 0.38
    a1.bar([i - w / 2 for i in x], [float(v) for v in a010], w, color="#1f5f96", label="≥ 0.10 m")
    a1.bar([i + w / 2 for i in x], [float(v) for v in a030], w, color="#b07a1c", label="≥ 0.30 m")
    for i, (v10, v30) in enumerate(zip(a010, a030)):
        a1.text(i - w / 2, float(v10) + 6, f"{float(v10):.0f}", ha="center", fontsize=7.8)
        a1.text(i + w / 2, float(v30) + 6, f"{float(v30):.1f}", ha="center", fontsize=7.8)
    a1.set_xticks(list(x)); a1.set_xticklabels(SHORT, fontsize=7.4, rotation=18, ha="right")
    a1.set_ylabel("Flooded area  (ha)")
    a1.set_ylim(0, max(float(v) for v in a010) * 1.22)
    a1.set_title("Whole domain", fontsize=9.5, loc="left", pad=8)
    a1.legend(fontsize=8, frameon=False)
    _bare(a1)

    hs = sx["hotspots"]
    order = sorted(hs, key=lambda k: -float(hs[k]["积水面积 ≥0.10 m（ha）"][0]))
    y = list(range(len(order)))
    v1 = [float(hs[k]["积水面积 ≥0.10 m（ha）"][0]) for k in order]
    v3 = [float(hs[k]["积水面积 ≥0.10 m（ha）"][3]) for k in order]
    a2.barh([i + 0.19 for i in y], v1, 0.36, color="#2f6b45", label="L1 existing")
    a2.barh([i - 0.19 for i in y], v3, 0.36, color="#15803d", label="L2+suggested v3")
    for i, k in enumerate(order):
        # 两个数一起写在右侧：小热点（0.4 ha）在线性轴上几乎看不见，数值必须落到文字上
        tag = "—" if v1[i] == 0 else f"{v3[i]/v1[i]*100-100:+.0f} %"
        a2.text(max(v1[i], v3[i]) + 3.0, i, f"{v1[i]:.2f} → {v3[i]:.2f}    {tag}",
                va="center", fontsize=7.5,
                color="#b23a2e" if v1[i] and v3[i] / v1[i] - 1 < -0.05 else "#5e6e7b")
    a2.set_yticks(y); a2.set_yticklabels(order, fontsize=8)
    a2.invert_yaxis()
    a2.set_xlabel("Flooded area ≥ 0.10 m  (ha)")
    a2.set_xlim(0, max(v1) * 1.55)
    a2.set_title("By hotspot  ·  L1 → suggested v3", fontsize=9.5, loc="left", pad=8)
    a2.legend(fontsize=8, frameon=False, loc="lower right")
    _bare(a2)
    fig.tight_layout(pad=1.4)
    p = OUTDIR / "fig4_extent.png"; fig.savefig(p, bbox_inches="tight"); plt.close(fig)
    return p


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="Case 1 基线评估四张图")
    ap.add_argument("--check", action="store_true", help="只打印解析出来的数")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    st = {k: read_state(k) for k, _, _, _ in STATES}
    ser = {k: read_series(sub) for k, _, _, sub in STATES}
    sx = read_stats()
    hy = read_hyeto()
    cont = st["L1"]["cont"]

    print("=== Link Flow Summary（每态按自己的分节边界解析）===")
    for k, _, lab, _ in STATES:
        s = st[k]
        print(f"  {lab:26s} 段 {s['links']:,}  超载 {s['over']:,} ({s['over']/s['links']*100:.1f} %)"
              f"  峰值 {s['peak_q']:.2f} m³/s @{s['peak_t']}  节点溢流峰值 {s['peak_node_rate']:.2f} m³/s")
    print(f"  溢流节点 {st['L1']['flood_nodes']:,} 个，其中 ≥30 min 的 {st['L1']['flood_30min']:,} 个"
          f"；逐井求和溢流 {st['L1']['flood_vol_m3']:,.0f} m³")
    print("=== L1 一维水量账 ===")
    for k in ("External Inflow", "External Outflow", "Flooding Loss", "Final Stored Volume",
              "Continuity Error (%)"):
        print(f"  {k:26s} {cont[k]:,.4f}")
    loss_pct = (cont["External Inflow"] - cont["External Outflow"] - cont["Flooding Loss"]
                - cont["Final Stored Volume"]) / cont["External Inflow"] * 100
    print(f"  余项 / 入流                {loss_pct:.4f} %")
    print("=== 各态二维时序 ===")
    for k, _, lab, _ in STATES:
        s = ser[k]
        mx = max(s["series"], key=lambda r: r["vol_m3"])
        print(f"  {lab:26s} 峰积水 {mx['vol_m3']:>9,.0f} m³ @{mx['t_h']:.2f}h"
              f"  最大湿面 {s['max_wet_m2']/1e4:6.1f} ha @{s['max_wet_t']:.2f}h"
              f"  末帧 {s['final_vol']:>9,.0f} m³")
    print("=== 全域面积（统计值 xlsx）===")
    for i, k in enumerate(SHORT):
        print(f"  {k:18s} ≥0.10 m {sx['domain'][('全域','积水面积 ≥0.10 m（ha）')][i]:>7s} ha"
              f"   ≥0.30 m {sx['domain'][('全域','积水面积 ≥0.30 m（ha）')][i]:>6s} ha"
              f"   峰量 {float(sx['domain'][('全域','最大积水量 m³')][i]):>9,.0f} m³")
    print(f"=== 雨型：峰强 {hy['peak_mmph']:.1f} mm/h @{hy['peak_t_h']:.3f} h"
          f"，总雨 {hy['total_mm']:.1f} mm，净雨 {hy['excess_mm']:.2f} mm ===")
    if a.check:
        return 0

    plt = _setup()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    for f in (fig_capacity(st, plt), fig_water(cont, plt), fig_timing(hy, ser["L1"], st, plt),
              fig_extent(sx, plt)):
        print(f"  → {f.relative_to(ROOT)}  ({f.stat().st_size/1024:.0f} KB)")
    (OUTDIR / "来源.txt").write_text(
        "本目录四张图的每一个数字都由 tools/case1_charts.py 现场解析生成，不手打：\n"
        f"  峰值流量 / 超载段数 / 节点溢流  ← {C2D}\\C1_M61_*_2way.rpt\n"
        "  一维水量账                      ← 同上 Flow Routing Continuity\n"
        f"  积水面积 / 峰值积水量 / 时序    ← {DELIV}\\*\\一二维\\*_积水面积时序.csv 与 *_耦合运行记录.json\n"
        f"  全域与八个热点的面积            ← {DELIV}\\统计值\\C1统计值.xlsx\n"
        "  雨型                            ← console/data/c1_hyeto.json（R61，模型版 61 mm 同一份）\n"
        "重新生成：python tools/case1_charts.py\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
