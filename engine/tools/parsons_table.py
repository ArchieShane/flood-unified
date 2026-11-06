# -*- coding: utf-8 -*-
"""按顾问报告那张表的字段出「现状 / 缓解后 / 改善」对照。

报告表头是这 7 项：
    洪水量 Flood Volume          退水(主网) Recession (majority)
    退水(残余上游) Recession (residual)   系统排空(主网) System Emptying (majority)
    蓄存能力 Storage Capacity     降雨服务水平 Precipitation Serviceability
    临时泵 Temporary Pumps

------------------------------------------------------------------ 每项怎么算，口径写死在这里
报告没有给这些量的定义，所以下面每一项都写明本系统用的判据。
换判据数就会变，**看数之前先看判据**。

`洪水量`   SWMM 连续性表的 Flooding Loss（ALLOW_PONDING NO 时是真实损失）；
           客户那三个模型是 ALLOW_PONDING YES，只能用 Node Flooding Summary 的
           Total Flood Volume，它是「溢出井盖的累计水量」，会跨节点重复计——
           两种口径在输出里分开标。

`退水(主网)`   全系统滞水量（Σ 各节点 PONDED_VOLUME）从**峰值**落到峰值的 10% 所用的时间。
`退水(残余上游)` 从峰值落到几乎为零（< 峰值的 1% 且 < 50 m³）所用的时间。
           这两项对应报告里 2.21 h 与 17 h 那种量级差 —— 主体退得快、上游零星积水拖很久。
`系统排空(主网)` 管内蓄水量 VOLUME_STORED 从峰值落到「死水位 + 10%×(峰值−死水位)」的时间。
           死水位取演算末端值 —— 低于出水口的管段永远存着水（Mussafah 实测 2,745 m³，
           占峰值 12.6%），不扣掉它这一项永远「算不出」。
`蓄存能力`  管内容积 Σ πD²/4·L ＋ 蓄水节点容积，**按几何算**，不是模拟量。
`降雨服务水平` 二分搜索：让洪水量 ≤ 阈值（默认 1% 的产流量）的最大雨深。
           每问一次要跑一次 SWMM，所以默认不做，加 --serviceability 才算。
`临时泵`    缓解态比现状态多出来的泵，逐个从 .inp 比对，不是照抄报告。

用法：
    python tools/parsons_table.py Mussafah --existing _baseline_R61D2 --mitigated _L2box_R61D2
    python tools/parsons_table.py Mussafah --existing _baseline_R61D2 --mitigated _R61D2
"""
from __future__ import annotations

import argparse
import math
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from workdir import WORK  # noqa: E402

MAJORITY_FRAC = 0.10     # 「主网退水」判据：落到峰值的 10%
RESID_FRAC = 0.01        # 「残余上游」判据：落到峰值的 1%
RESID_ABS = 50.0         # 且绝对量小于这个（m³），免得峰值很小时判据失灵


def storage_node_names(inp: pathlib.Path) -> list[str]:
    """从 .inp 的 [STORAGE] 段取蓄水节点名。退水判据要把它们摘出去。"""
    if not inp.exists():
        return []
    out, cur = [], None
    for ln in inp.open(encoding="utf-8", errors="replace"):
        t = ln.strip()
        if t.startswith("["):
            cur = t.strip("[]").upper(); continue
        if cur == "STORAGE" and t and not t.startswith(";"):
            out.append(t.split()[0])
    return out


def series(out_path: pathlib.Path):
    """逐时刻的系统滞水量、管内蓄水量、溢流率、出流率。"""
    from swmm.toolkit import output, shared_enum as se

    h = output.init()
    output.open(h, str(out_path))
    try:
        n_node = output.get_proj_size(h)[1]
        nper = output.get_times(h, se.Time.NUM_PERIODS)
        step = output.get_times(h, se.Time.REPORT_STEP)
        t = np.arange(nper) * step / 3600.0

        def sysser(attr):
            v = []
            for i in range(nper):
                v.append(output.get_system_attribute(h, i, attr)[0])
            return np.array(v)

        stored = sysser(se.SystemAttribute.VOLUME_STORED)
        flood = sysser(se.SystemAttribute.FLOOD_LOSSES)
        outf = sysser(se.SystemAttribute.OUTFALL_FLOWS)
        # 滞水量要逐节点求和：系统级没有这一项。
        # **蓄水节点要单独摘出来**：调蓄塘/箱涵按设计就是要存住水（排空 24 h），
        # 把它算进「退水」，一座正常工作的塘会让退水时间变成「永远退不完」——
        # 实测 L2+B 的 1,609 m³ 末端滞水里有 1,577 m³ 就在 NP1–NP6 六座箱涵内，
        # 街面只剩 33 m³，和 L1 一模一样。报告里的 Recession 问的是**街面积水**多久消，
        # 所以默认口径排除蓄水节点，全量口径另存一份备查。
        is_sto = np.zeros(n_node, dtype=bool)
        try:
            sto_names = set(storage_node_names(out_path.with_suffix(".inp")))
            for i in range(n_node):
                if output.get_elem_name(h, se.ElementType.NODE, i) in sto_names:
                    is_sto[i] = True
        except Exception:
            pass
        pond = np.zeros(nper)
        pond_st = np.zeros(nper)
        for i in range(nper):
            a = np.asarray(output.get_node_attribute(h, i, se.NodeAttribute.PONDED_VOLUME))
            pond[i] = float(a.sum())
            pond_st[i] = float(a[is_sto].sum()) if is_sto.any() else 0.0
        return dict(t=t, step=step, n_node=n_node, stored=stored,
                    flood=flood, outfall=outf,
                    ponded=pond - pond_st,      # 街面积水（默认口径）
                    ponded_all=pond,            # 含蓄水设施（备查）
                    ponded_sto=pond_st,         # 蓄水设施内
                    n_storage=int(is_sto.sum()))
    finally:
        output.close(h)


def _fall_time(t, v, frac, absmin=None, dead=0.0):
    """从峰值落到「死水位 + frac ×（峰值 − 死水位）」所用的小时数。

    `dead` 是永远排不掉的那部分。管网里低于出水口的管段会一直存着水 ——
    Mussafah 实测末端管内还有 2,745 m³，占峰值 21,793 的 12.6%，
    所以「落到峰值的 10%」这个判据**永远满足不了**，会一直返回「算不出」。
    改成相对**可排出的那部分**算，才有物理含义。
    """
    if len(v) == 0 or v.max() <= 0:
        return 0.0
    ip = int(np.argmax(v))
    span = max(v.max() - dead, 1e-9)
    thr = dead + span * frac
    if absmin is not None and absmin < thr:
        thr = max(absmin, dead)
    for k in range(ip, len(v)):
        if v[k] <= thr:
            return float(t[k] - t[ip])
    return None                      # 演算结束都没退到，如实返回 None


def geometry_storage(inp: pathlib.Path) -> dict:
    """管内容积 + 蓄水节点容积，按几何算。"""
    sec, cur = {}, None
    for ln in inp.open(encoding="utf-8", errors="replace"):
        s = ln.strip()
        if s.startswith("["):
            cur = s.strip("[]").upper(); sec[cur] = []; continue
        if cur and s and not s.startswith(";"):
            sec[cur].append(s.split())
    xs = {r[0]: r for r in sec.get("XSECTIONS", [])}
    vpipe = 0.0
    for r in sec.get("CONDUITS", []):
        if len(r) < 4:
            continue
        x = xs.get(r[0])
        if not x or len(x) < 3:
            continue
        try:
            d, L = float(x[2]), float(r[3])
        except ValueError:
            continue
        if d <= 0 or L <= 0:
            continue
        shape = x[1].upper()
        a = math.pi * d * d / 4 if shape.startswith("CIRC") else d * (
            float(x[3]) if len(x) > 3 and x[3] not in ("0", "") else d)
        vpipe += a * L
    # 曲线可以**一行写多对**（如 `NP1_Curve Storage 0 2899.6 2.30 2899.6`），
    # 也可以续行只写数对。只取第一对会把库容算没 —— 踩过。
    crv, last = {}, None
    for r in sec.get("CURVES", []):
        if not r:
            continue
        name = r[0]
        rest = r[1:]
        if rest and not re.match(r"^-?[\d.]+$", rest[0]):
            rest = rest[1:]                      # 跳过类型词（Storage / Pump4 …）
        vals = []
        for v in rest:
            try:
                vals.append(float(v))
            except ValueError:
                break
        for i in range(0, len(vals) - 1, 2):
            crv.setdefault(name, []).append((vals[i], vals[i + 1]))
        last = name
    vstore = 0.0
    for r in sec.get("STORAGE", []):
        if len(r) < 6:
            continue
        try:
            ymax = float(r[2])
        except ValueError:
            continue
        pts = sorted(crv.get(r[5], []))
        if len(pts) >= 2:
            v = sum(0.5 * (a0 + a1) * (d1 - d0)
                    for (d0, a0), (d1, a1) in zip(pts, pts[1:]) if d1 <= ymax + 1e-9)
        else:
            v = 0.0
        vstore += v
    return {"pipe_m3": vpipe, "storage_m3": vstore, "total_m3": vpipe + vstore}


def pumps(inp: pathlib.Path) -> set[str]:
    out, cur = set(), None
    for ln in inp.open(encoding="utf-8", errors="replace"):
        s = ln.strip()
        if s.startswith("["):
            cur = s.strip("[]").upper(); continue
        if cur == "PUMPS" and s and not s.startswith(";"):
            out.add(s.split()[0])
    return out


def flood_volume(rpt: pathlib.Path) -> dict:
    t = rpt.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"Flow Routing Continuity(.*?)Continuity Error", t, re.S)
    loss = None
    if m:
        k = re.search(r"Flooding Loss \.+\s*(-?[\d.]+)", m.group(1))
        loss = float(k.group(1)) * 10_000 if k else None
    b = re.search(r"Node Flooding Summary(.*?)\n\s*\n\s*\n", t, re.S)
    tot = 0.0
    if b:
        for ln in b.group(1).splitlines():
            c = ln.split()
            if len(c) >= 7 and re.match(r"^[\w\-.]+$", c[0]) and re.match(r"^[\d.]+$", c[1]):
                try:
                    tot += float(c[-2]) * 1000
                except ValueError:
                    pass
    pond = "ALLOW_PONDING  YES" in t or "ALLOW_PONDING YES" in t
    return {"loss_m3": loss, "node_total_m3": tot}


def one(model: str, tag: str, log=print) -> dict:
    inp = WORK / f"{model}{tag}.inp"
    rpt, out = inp.with_suffix(".rpt"), inp.with_suffix(".out")
    if not (inp.exists() and rpt.exists() and out.exists()):
        log(f"  缺 {inp.stem} 的 inp/rpt/out"); return {}
    s = series(out)
    fv = flood_volume(rpt)
    g = geometry_storage(inp)
    return {
        "flood_loss_m3": fv["loss_m3"],
        "flood_nodesum_m3": fv["node_total_m3"],
        "recession_majority_h": _fall_time(s["t"], s["ponded"], MAJORITY_FRAC),
        "recession_residual_h": _fall_time(s["t"], s["ponded"], RESID_FRAC, RESID_ABS),
        # 死水位取演算末端值：低于出水口的管段永远存着水，不扣掉就永远「算不出」
        "emptying_majority_h": _fall_time(s["t"], s["stored"], MAJORITY_FRAC,
                                          dead=float(s["stored"][-1])),
        "dead_storage_m3": float(s["stored"][-1]),
        "storage_pipe_m3": g["pipe_m3"],
        "storage_extra_m3": g["storage_m3"],
        "storage_total_m3": g["total_m3"],
        "n_pumps": len(pumps(inp)),
        "peak_ponded_m3": float(s["ponded"].max()),
        "peak_ponded_all_m3": float(s.get("ponded_all", s["ponded"]).max()),
        "end_ponded_m3": float(s["ponded"][-1]),
        "end_ponded_sto_m3": float(s.get("ponded_sto", np.zeros(1))[-1]),
        "n_storage": int(s.get("n_storage", 0)),
        "peak_stored_m3": float(s["stored"].max()),
        "sim_h": float(s["t"][-1]),
    }


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="按顾问报告的字段出「现状/缓解后/改善」表")
    ap.add_argument("model")
    ap.add_argument("--existing", required=True, help="现状态的文件后缀，如 _baseline_R61D2")
    ap.add_argument("--mitigated", required=True, help="缓解态的文件后缀，如 _L2box_R61D2")
    a = ap.parse_args(argv)

    print(f"{a.model}：现状 {a.existing}　缓解后 {a.mitigated}")
    E = one(a.model, a.existing)
    M = one(a.model, a.mitigated)
    if not E or not M:
        return 1

    def line(zh, en, key, unit, fmt="{:,.0f}", better="low"):
        e, m = E.get(key), M.get(key)
        if e is None or m is None:
            imp = "算不出"
        elif e == 0:
            imp = "—"
        else:
            d = (m - e) / e * 100
            good = (d < 0) if better == "low" else (d > 0)
            imp = f"{abs(d):.2f}% {'减少' if d < 0 else '增加'}" + ("" if good else " ⚠")
        fe = fmt.format(e) if e is not None else "—"
        fm = fmt.format(m) if m is not None else "—"
        print(f"  {zh:<14}{en:<28}{fe + ' ' + unit:>16}{fm + ' ' + unit:>16}{imp:>18}")

    print(f"\n  {'参数':<14}{'Parameter':<28}{'现状':>16}{'缓解后':>16}{'改善':>18}")
    line("洪水量", "Flood Volume", "flood_loss_m3", "m³")
    line("洪水量(节点累计)", "Flood Vol (node sum)", "flood_nodesum_m3", "m³")
    line("退水(主网)", "Recession (majority)", "recession_majority_h", "hrs", "{:,.2f}")
    line("退水(残余上游)", "Recession (residual)", "recession_residual_h", "hrs", "{:,.2f}")
    line("系统排空(主网)", "System Emptying", "emptying_majority_h", "hrs", "{:,.2f}")
    line("蓄存能力", "Storage Capacity", "storage_total_m3", "m³", "{:,.0f}", "high")
    line("临时泵", "Temporary Pumps", "n_pumps", "台", "{:,.0f}", "high")
    print(f"\n  判据：退水按**全系统滞水量**从峰值落到 {MAJORITY_FRAC:.0%}（主网）/ "
          f"{RESID_FRAC:.0%} 且 <{RESID_ABS:g} m³（残余）；")
    print(f"        排空按**管内蓄水量**落到 {MAJORITY_FRAC:.0%}；蓄存能力按几何算。")
    print(f"        演算时长 {E['sim_h']:.1f} h —— 返回「算不出」表示演算结束仍未退到。")
    print(f"  峰值滞水量 现状 {E['peak_ponded_m3']:,.0f} → 缓解后 {M['peak_ponded_m3']:,.0f} m³")
    print(f"  管内死水位 现状 {E.get('dead_storage_m3', 0):,.0f} → "
          f"缓解后 {M.get('dead_storage_m3', 0):,.0f} m³（永远排不掉的那部分）")
    print(f"  「降雨服务水平」要二分搜索多次重跑，这里不含，见模块说明。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
