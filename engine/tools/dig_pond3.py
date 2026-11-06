# -*- coding: utf-8 -*-
"""防线 2+（第三版）：进水口装闸，塘满就关。

第二版的毛病：进水管是敞口的，塘满之后管网还在往里灌，水从塘节点冒出来——
实测 6 片方案塘溢流 3,551 m³，几乎和原始积水量一样大。那不是「治理」，是把水从
街道节点搬到了塘节点。

真实工程里塘的进水口是有闸门（penstock）的，塘满就关闸，水留在管网里——管网本来就是
它该待的地方。这一版把进水管从 CONDUIT 换成 ORIFICE + 控制规则：
    IF NODE <塘> DEPTH >= 0.95 × 塘深  THEN ORIFICE <进水口> SETTING = 0

另外两处沿用第二版：一个塘接一片（多根进水管）、容积按这一片实测溢流量定、
进水能力按「× 雨峰窗口 ≈ 库容」配。
"""
import math, pathlib, sys
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))               # 兄弟脚本
sys.path.insert(0, str(HERE.parent))        # swbrain 包
from workdir import WORK
from dig_pond import read_sections, parse_nodes, parse_coords, flooding_nodes, full_flow

DN_TIERS = (300, 400, 500, 600, 800, 900, 1200, 1500)


def cluster_sites(inp: pathlib.Path, rpt: pathlib.Path, n_ponds: int, radius_m: float = 250.0,
                  # mult：库容 = 服务范围内溢流量 × mult。**1.5 是拍的，手册里没有这一条**
                  # —— 它想覆盖的是「峰值同时性」和「进水口来不及吞」两件事，但没有标定过。
                  # 客户一定会问依据，所以这里如实标成假设参数（见 swbrain/assumptions.py）。
                  # 另外两个因子是有依据的：§4.2 的 5% 沉沙，以及 0.30 m 超高（超高不算可用库容）。
                  mult: float = 1.5, depth_m: float = 2.0, max_inlets: int = 4,
                  match_window_h: float | None = 2.0, log=print):
    """贪心聚类。距离矩阵一次算完——Shaliela 有 3,242 个积水节点，逐点循环太慢。"""
    lines, idx = read_sections(inp)
    coords = parse_coords(lines, idx)
    inv, kind = parse_nodes(lines, idx)
    fl = {k: v for k, v in flooding_nodes(rpt).items()
          if k in coords and k in inv and v > 0 and kind.get(k) == "JUNCTIONS"}
    if not fl:
        return []
    names = list(fl)
    X = np.array([coords[k][0] for k in names]); Y = np.array([coords[k][1] for k in names])
    V = np.array([fl[k] for k in names])
    log(f"  积水节点 {len(names)} 个，合计 {V.sum():,.0f} m³")
    near = (X[:, None] - X[None, :]) ** 2 + (Y[:, None] - Y[None, :]) ** 2 <= radius_m ** 2
    alive = np.ones(len(names), bool)
    out = []
    for _ in range(n_ponds):
        if not alive.any():
            break
        sums = (near & alive[None, :]) @ V                     # 每个点：半径内还活着的溢流量之和
        sums[~alive] = -1
        bi = int(np.argmax(sums))
        if sums[bi] <= 0:
            break
        ix = np.nonzero(near[bi] & alive)[0]
        w = V[ix]
        cx = float((X[ix] * w).sum() / w.sum()); cy = float((Y[ix] * w).sum() / w.sum())
        top = ix[np.argsort(-w)][:max_inlets]
        vol = max(float(w.sum()) * mult, 300.0)
        dn = 900
        if match_window_h:
            need = vol / match_window_h
            dn = next((d for d in DN_TIERS if full_flow(d / 1000.0) * len(top) >= need), 1500)
        out.append({"x": cx, "y": cy, "connect": [names[j] for j in top],
                    "n_served": int(len(ix)), "served_m3": float(w.sum()), "vol_m3": vol,
                    "area_m2": vol / depth_m, "depth_m": depth_m, "dn_mm": dn,
                    "q_in_m3h": full_flow(dn / 1000.0) * len(top)})
        alive &= ~near[bi]
    return out


def dig3(src: pathlib.Path, dst: pathlib.Path, ponds: list[dict], empty_h: float = 12.0,
         empty_start_h: int = 14, close_at: float = 0.95, drain_depth: float = 0.30,
         log=print):
    """挖塘：进水口用带闸的孔口（塘满关闸）+ 排空泵。"""
    lines, idx = read_sections(src)
    inv, kind = parse_nodes(lines, idx)
    coords = parse_coords(lines, idx)
    add = {"STORAGE": [], "CURVES": [], "ORIFICES": [], "XSECTIONS": [],
           "COORDINATES": [], "PUMPS": [], "CONTROLS": []}
    for p in ponds:
        pid = p["id"]
        cns = [c for c in p["connect"] if c in inv]
        if not cns:
            log(f"  ✗ {pid}: 接入点都不在模型里"); continue
        dn = float(p["dn_mm"]) / 1000.0
        z = min(inv[c] for c in cns) - 0.30 - float(p["depth_m"])
        vol = p["area_m2"] * p["depth_m"]
        add["STORAGE"].append(f"{pid} {z:.3f} {p['depth_m']:.2f} 0 TABULAR {pid}_Curve 0 0 0 0 0")
        add["CURVES"].append(f"{pid}_Curve Storage 0 {p['area_m2']:.1f} "
                             f"{p['depth_m']:.2f} {p['area_m2']:.1f}")
        add["COORDINATES"].append(f"{pid} {p['x']:.3f} {p['y']:.3f}")
        ors = []
        for j, cn in enumerate(cns, 1):
            oid = f"IN_{pid}_{j}"
            # SIDE 型孔口，底标高 = 接入点管底，放流系数 0.65
            add["ORIFICES"].append(f"{oid} {cn} {pid} SIDE 0 0.65 NO 0")
            add["XSECTIONS"].append(f"{oid} CIRCULAR {dn:.3f} 0 0 0")
            ors.append(oid)
        # 塘满关闸：水留在管网里，而不是穿过塘再从塘里冒出来
        add["CONTROLS"].append(f"RULE {pid}_GATE_SHUT")
        add["CONTROLS"].append(f"IF NODE {pid} DEPTH >= {close_at * p['depth_m']:.2f}")
        for k, oid in enumerate(ors):
            add["CONTROLS"].append(f"{'THEN' if k == 0 else 'AND'} ORIFICE {oid} SETTING = 0")
        add["CONTROLS"] += ["PRIORITY 3", ""]
        add["CONTROLS"].append(f"RULE {pid}_GATE_OPEN")
        add["CONTROLS"].append(f"IF NODE {pid} DEPTH < {close_at * p['depth_m'] * 0.9:.2f}")
        for k, oid in enumerate(ors):
            add["CONTROLS"].append(f"{'THEN' if k == 0 else 'AND'} ORIFICE {oid} SETTING = 1")
        add["CONTROLS"] += ["PRIORITY 2", ""]
        # 排空泵：雨峰过后抽回管网，塘才能循环使用
        if empty_h:
            q_lps = vol / (empty_h * 3600.0) * 1000.0
            tgt = cns[0]                                    # 抽回它的主进水节点
            add["PUMPS"].append(f"PU_{pid} {pid} {tgt} {pid}_Pump OFF 0 0")
            add["CURVES"].append(f"{pid}_Pump Pump4 0 {q_lps:.1f} {p['depth_m']:.2f} {q_lps:.1f}")
            # 排空条件里**必须**带上「接收节点已经退水」。
            # 只按时钟（SIMULATION TIME > 14:00）开泵会把塘里的水抽回还在超载的管网，
            # 那个节点再冒出来、又顺重力流回塘——管网→塘→泵→管网的死循环。
            # 实测：三个区的排空泵都开了 70.8% 的时长，Shaliela 回灌 477,985 m³
            # （占总入流 127%）、RB3 882,535 m³（111%），把结果污染得没法看。
            add["CONTROLS"] += [
                f"RULE {pid}_EMPTY_ON", f"IF NODE {pid} DEPTH > 0.05",
                f"AND NODE {tgt} DEPTH < {drain_depth:.2f}",
                f"AND SIMULATION TIME > {empty_start_h}:00:00",
                f"THEN PUMP PU_{pid} STATUS = ON", "PRIORITY 1", "",
                f"RULE {pid}_EMPTY_OFF", f"IF NODE {pid} DEPTH <= 0.05",
                f"THEN PUMP PU_{pid} STATUS = OFF", "PRIORITY 4", "",
                f"RULE {pid}_EMPTY_HOLD", f"IF NODE {tgt} DEPTH >= {drain_depth:.2f}",
                f"THEN PUMP PU_{pid} STATUS = OFF", "PRIORITY 5", ""]
            p["empty_q_lps"] = q_lps
        log(f"  ✓ {pid}: {vol:>9,.0f} m³ ｜服务 {p['n_served']:>3} 点 ({p['served_m3']:>8,.0f} m³)"
            f" ｜{len(ors)} 个 DN{int(dn * 1000)} 带闸进水口 = {p['q_in_m3h']:>8,.0f} m³/h"
            f"（灌满 {vol / p['q_in_m3h'] * 60:>4.0f} min）")
    # 原文件没有的小节要插在 [CONTROLS] **之前**：SWMM 解析控制规则时按出现顺序查对象，
    # 把 [ORIFICES] 追加到文件末尾会得到「ERROR 209: undefined object」。
    new_secs = [sec for sec, rows in add.items() if sec not in idx and rows]
    anchor = idx.get("CONTROLS", [len(lines), len(lines)])[0]
    out = []
    for i, ln in enumerate(lines):
        if i == anchor:
            for sec in new_secs:
                out.append(f"[{sec}]"); out.extend(add[sec]); out.append("")
        out.append(ln)
        for sec, rows in add.items():
            if sec in idx and i == idx[sec][0] and rows:
                out.extend(rows)
    if anchor >= len(lines):
        for sec in new_secs:
            out.append(f"[{sec}]"); out.extend(add[sec]); out.append("")
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    return dst


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "Mussafah"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    ss = cluster_sites(WORK / f"{model}_baseline.inp", WORK / f"{model}_baseline.rpt", n)
    for i, s in enumerate(ss, 1):
        s["id"] = f"NP{i}"
    dig3(WORK / f"{model}_baseline.inp", WORK / f"{model}_L2t_test.inp", ss)
