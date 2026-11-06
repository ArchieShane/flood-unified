# -*- coding: utf-8 -*-
"""产流层：把降雨变成逐节点入流过程线。

**为什么需要这一层**：客户交付的三个 .inp 都没有 [SUBCATCHMENTS]，产流是他们在上游
算好、按节点注入的入流序列。所以在那些模型里改雨量计不会改变任何结果——
要换降雨场景，必须自己把产流这一层重建出来。

**用的是客户自己的参数与方法**（Catchment 图层里全有）：
    损失    SCS 径流曲线法（LOSS_ME = "SCS CN"），逐汇水区的 SCS_CN
    汇流    SCS 单位线（UHM = "SCS Unit Hydrograph"），逐汇水区的 TC（分钟）
    面积    A_UNIFIED（m²）　出流节点 OUT_NODE

    S  = 25400/CN − 254                     潜在最大滞留量，mm
    Q  = (P − 0.2S)² / (P + 0.8S)           累计净雨，mm（P > 0.2S 时，否则 0）
    Tl = 0.6 · Tc                            滞后时间
    Tp = Δt/2 + Tl                           峰现时间
    qp = 0.208 · A[km²] · Q[mm] / Tp[h]      单位线峰值，m³/s

**判据**：用他们的雨（61.34 mm / 24 h）跑这一层，必须复现他们交付的入流过程线——
总量、峰值、峰现时刻都要对上。对不上就不要拿它去换雨型。

RB3 的 shapefile 缺全部 .dbf，没有汇水区参数。它按「每个节点的产流面积由它自己交付的
水量反算」重建，CN 与 TC 取另两个区的中位数——总量按构造精确保持，变的只有过程线形状。
这是**退而求其次**，不是等价物，页面与假设总账里都标出来。

用法：
  python tools/runoff.py <汇水区> --P 61.34 --dur 24 --out <后缀>
  python tools/runoff.py <汇水区> --P 61 --dur 2 --out P61D2
"""
from __future__ import annotations

import pathlib
import re
import sys
from collections import defaultdict

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from workdir import WORK

SHP = {"Mussafah": "Musaffah_00", "Shaliela": "Shaliela", "RB3": "RB3"}
P_DELIVERED = 61.34        # 交付模型背后的设计雨深（= 雨量计 51.12 × 1.2 气候变化）
CN_FALLBACK = 82.0         # 另两个区的 SCS_CN 中位数，只给缺 .dbf 的 RB3 用
# TC **不是**取中位数，是拿 RB3 交付的全网过程线反算标定出来的：
# 用另两区的中位数 10 min，重建峰值比交付高 62.9%（RB3 的汇水区大得多、汇流慢）；
# 扫一遍取 30 min，峰值差 +3.3%。这是标定值，不是客户图层里的数，页面与假设总账都标出来。
TC_FALLBACK = 30.0         # 分钟（由交付过程线反算标定）

# SCS 无量纲单位线（t/Tp, q/qp），标准曲线（PRF = 484）
_UH = np.array([
    [0.0, 0.000], [0.1, 0.030], [0.2, 0.100], [0.3, 0.190], [0.4, 0.310],
    [0.5, 0.470], [0.6, 0.660], [0.7, 0.820], [0.8, 0.930], [0.9, 0.990],
    [1.0, 1.000], [1.1, 0.990], [1.2, 0.930], [1.3, 0.860], [1.4, 0.780],
    [1.5, 0.680], [1.6, 0.560], [1.8, 0.390], [2.0, 0.280], [2.2, 0.207],
    [2.4, 0.147], [2.6, 0.107], [2.8, 0.077], [3.0, 0.055], [3.2, 0.040],
    [3.4, 0.029], [3.6, 0.021], [3.8, 0.015], [4.0, 0.011], [4.5, 0.005],
    [5.0, 0.000]])


def scs_cum_excess(P_cum_mm: np.ndarray, cn: float) -> np.ndarray:
    """累计雨深 → 累计净雨（mm）。初损按 Ia = 0.2S，是累计口径，不是逐步扣。"""
    S = 25400.0 / cn - 254.0
    x = np.maximum(P_cum_mm - 0.2 * S, 0.0)
    return x * x / np.maximum(P_cum_mm + 0.8 * S, 1e-9)


def unit_hydrograph(tc_min: float, dt_h: float, area_m2: float) -> np.ndarray:
    """单位净雨（1 mm）在这块汇水区上产生的过程线，m³/s，步长 dt_h。"""
    tl = 0.6 * (tc_min / 60.0)
    tp = dt_h / 2.0 + tl
    qp = 0.208 * (area_m2 / 1e6) * 1.0 / max(tp, 1e-9)          # A km²、Q=1 mm
    n = max(int(np.ceil(5.0 * tp / dt_h)) + 1, 2)
    t = np.arange(n) * dt_h
    u = np.interp(t / tp, _UH[:, 0], _UH[:, 1], left=0.0, right=0.0) * qp
    # 体积归一：离散化会丢量，按解析体积（1 mm × 面积）缩回去
    vol = float(u.sum() * dt_h * 3600.0)
    want = area_m2 * 0.001
    return u * (want / vol) if vol > 0 else u


def hyetograph(P_mm: float, dur_h: float, dt_h: float, shape: np.ndarray | None = None,
               start_h: float = 0.0, total_h: float = 48.0) -> tuple[np.ndarray, np.ndarray]:
    """设计雨型 → (时间 h, 累计雨深 mm)。shape 是 0–1 的无量纲累计曲线（按历时归一）。"""
    t = np.arange(0.0, total_h + 1e-9, dt_h)
    if shape is None:                       # 没给形状就用均匀雨（只在自检里用）
        frac = np.clip((t - start_h) / dur_h, 0.0, 1.0)
    else:
        s = np.asarray(shape, dtype=float)
        s = s / max(s[-1], 1e-9)
        tau = np.clip((t - start_h) / dur_h, 0.0, 1.0)
        frac = np.interp(tau, np.linspace(0.0, 1.0, len(s)), s)
    return t, frac * P_mm


def rain_series(inp: pathlib.Path) -> dict:
    """.inp 里所有**非入流**的时间序列 → {名字: (时间 h, 累计值)}。那些就是雨量序列。"""
    sec, inflow_ts, rows = None, set(), defaultdict(list)
    for ln in inp.open(encoding="utf-8", errors="replace"):
        s = ln.strip()
        m = re.match(r"^\[(\w+)\]", s)
        if m:
            sec = m.group(1).upper(); continue
        if not s or s.startswith(";"):
            continue
        p = s.split()
        if sec == "INFLOWS" and len(p) >= 3:
            inflow_ts.add(p[2])
        elif sec == "TIMESERIES" and p:
            rows[p[0]].append(p[1:])
    out = {}
    for k, rr in rows.items():
        if k in inflow_ts:
            continue
        tt, vv = [], []
        for c in rr:
            try:
                if ":" in str(c[0]):
                    h = [float(x) for x in str(c[0]).split(":")] + [0, 0]
                    tt.append(h[0] + h[1] / 60 + h[2] / 3600); vv.append(float(c[1]))
                else:
                    tt.append(float(c[-2])); vv.append(float(c[-1]))
            except (ValueError, IndexError):
                pass
        if len(tt) > 2:
            out[k] = (np.array(tt), np.array(vv))
    return out


def read_shape(inp: pathlib.Path, dur_h: float, log=print) -> np.ndarray | None:
    """按**历时**挑客户自己的设计雨型，取它的无量纲累计形状。

    不能按名字挑：24 小时那条和 2 小时那条名字里都含「with_CC」，
    子串匹配会把两条的点混在一起，得到一条毫无意义的曲线。
    """
    cand = [(k, t, v) for k, (t, v) in rain_series(inp).items()
            if abs(t.max() - dur_h) < 0.05 * max(dur_h, 1.0) and v.max() > 0]
    if not cand:
        log(f"    .inp 里没有 {dur_h:g} h 的雨量序列")
        return None
    # 同历时有多条时：先看这些候选的**无量纲形状**差多少。
    # 差得可忽略就随便挑一条（结果一样）；差得大，挑哪条就是个实质选择，必须报出来。
    norm = [(k, np.interp(np.linspace(0, 1, 101), t / t.max(), v / v.max()))
            for k, t, v in cand]
    spread = float(np.max([np.abs(a[1] - b[1]).max() for a in norm for b in norm])) if len(norm) > 1 else 0.0
    # 优先设计雨（名字里带 CC / yr），其次分辨率最细的那条
    def rank(z):
        k = z[0].lower()
        return (0 if ("cc" in k) else 1, -len(z[1]))
    k, t, v = min(cand, key=lambda z: rank((z[0], z[1])))
    log(f"    雨型取自客户自己的「{k[-46:]}」：{len(v)} 点、{t.max():.2f} h、累计 {v.max():.2f} mm")
    log(f"    （同历时共 {len(cand)} 条候选，无量纲形状最大相差 {spread * 100:.1f}%"
        + ("，可忽略" if spread < 0.02 else "，**选哪条会影响结果**") + "）")
    return v


def subcatchments(cfg, model: str, log=print):
    """汇水区参数表。RB3 缺 .dbf，按交付水量反算面积（如实标 derived=True）。"""
    import pandas as pd
    from swbrain import dmtmodel as M
    c = M.load_model(cfg, SHP[model]).get("catchments")
    if c is not None and len(c.columns) > 1 and "SCS_CN" in c.columns:
        d = pd.DataFrame({
            "area_m2": pd.to_numeric(c["A_UNIFIED"], errors="coerce"),
            "cn": pd.to_numeric(c["SCS_CN"], errors="coerce"),
            "tc_min": pd.to_numeric(c["TC"], errors="coerce"),
            "node": c["OUT_NODE"].astype(str)})
        d = d[(d["area_m2"] > 0) & d["cn"].notna() & d["node"].ne("")].copy()
        d["tc_min"] = d["tc_min"].fillna(TC_FALLBACK).clip(lower=1.0)
        log(f"  {model}: 汇水区 {len(d):,} 个（客户图层）"
            f"，面积 {d['area_m2'].sum()/1e4:,.1f} ha，CN 中位 {d['cn'].median():.0f}")
        return d, False
    # 缺 .dbf：面积由交付水量反算
    from delivered import read_inflow_ts
    H = read_inflow_ts(WORK / f"{model}_baseline.inp")
    q_mm = float(scs_cum_excess(np.array([P_DELIVERED]), CN_FALLBACK)[0])
    rows = [{"area_m2": float(np.trapezoid(v, t) * 3600 / 1000.0) / (q_mm / 1000.0),
             "cn": CN_FALLBACK, "tc_min": TC_FALLBACK, "node": n}
            for n, (t, v) in H.items() if np.trapezoid(v, t) > 0]
    d = pd.DataFrame(rows)
    log(f"  {model}: **缺 .dbf**，按交付水量反算 {len(d):,} 个节点的产流面积"
        f"（等效 {d['area_m2'].sum()/1e4:,.1f} ha，CN={CN_FALLBACK:.0f} 取另两区中位，"
        f"TC={TC_FALLBACK:.0f} min 由交付过程线**反算标定**）")
    return d, True


def node_hydrographs(subs, t: np.ndarray, P_cum: np.ndarray, dt_h: float, log=print):
    """逐汇水区算净雨、卷积单位线，再按出流节点汇总。返回 {节点: 流量数组 m³/s}。"""
    out = defaultdict(lambda: np.zeros(len(t)))
    # 按 (CN, TC) 分组：几千个汇水区但只有几百种组合，单位线可以复用
    key = list(zip(subs["cn"].round(1), subs["tc_min"].round(2)))
    subs = subs.assign(_k=key)
    n_grp = 0
    for (cn, tc), g in subs.groupby("_k", sort=False):
        exc = scs_cum_excess(P_cum, float(cn))
        # append 而不是 prepend：inc[i] 要是「**从** t[i] 开始那一格的雨」。
        # 用 prepend 会让响应从该格的末端起算，整条过程线晚一个时间步
        # （实测峰现时刻正好差一格；改成 append 后对交付结果的峰现差归零）。
        inc = np.diff(exc, append=exc[-1])              # 逐步净雨增量 mm
        if inc.max() <= 0:
            continue
        n_grp += 1
        # 单位线只和 (tc, 面积) 有关，同组内面积不同 → 按面积线性缩放一条基准单位线
        u1 = unit_hydrograph(float(tc), dt_h, 1.0)      # 1 m² 上 1 mm 净雨的过程线
        base = np.convolve(inc, u1, mode="full")[:len(t)]   # m³/s per m²
        for a, node in zip(g["area_m2"].values, g["node"].values):
            out[node] += base * float(a)
    log(f"  单位线分组 {n_grp} 组（CN×TC 的不同组合），汇总到 {len(out):,} 个节点")
    return dict(out)


def model_nodes(inp: pathlib.Path) -> set:
    """.inp 里真实存在的节点名。"""
    sec, out = None, set()
    for ln in inp.open(encoding="utf-8", errors="replace"):
        s = ln.strip()
        m = re.match(r"^\[(\w+)\]", s)
        if m:
            sec = m.group(1).upper(); continue
        if sec in ("JUNCTIONS", "OUTFALLS", "STORAGE", "DIVIDERS") and s and not s.startswith(";"):
            p = s.split()
            if p:
                out.add(p[0])
    return out


def keep_known_nodes(hydro: dict, inp: pathlib.Path, dt_h: float, log=print) -> dict:
    """汇水区图层的 OUT_NODE 有模型里不存在的（如 RD*），必须剔掉——
    但**不能静默丢**：丢掉多少水要报出来，否则水量平衡就悄悄少了一块。"""
    known = model_nodes(inp)
    # 先试着把名字对上：汇水区图层写「RD 34」（空格），模型里是「RD_34」（下划线），
    # 直接按字符串比会把这片汇水的水整份丢掉。归一化后能对上就对上。
    norm = {k.replace(" ", "_").replace("-", "_").upper(): k for k in known}
    fixed, n_fix = {}, 0
    for k, v in hydro.items():
        if k in known:
            fixed[k] = fixed.get(k, 0) + v
            continue
        alt = norm.get(k.replace(" ", "_").replace("-", "_").upper())
        if alt:
            fixed[alt] = fixed.get(alt, 0) + v
            n_fix += 1
        else:
            fixed[k] = fixed.get(k, 0) + v
    if n_fix:
        log(f"  节点名归一化对上 {n_fix} 个（如「RD 34」→「RD_34」）")
    hydro = fixed
    keep = {k: v for k, v in hydro.items() if k in known}
    drop = {k: v for k, v in hydro.items() if k not in known}
    if drop:
        vd = sum(float(v.sum() * dt_h * 3600) for v in drop.values())
        vk = sum(float(v.sum() * dt_h * 3600) for v in keep.values())
        log(f"  ! 有 {len(drop)} 个出流节点不在模型里（{', '.join(sorted(drop)[:5])}"
            f"{' …' if len(drop) > 5 else ''}），"
            f"丢掉 {vd:,.0f} m³，占 {vd / max(vd + vk, 1e-9) * 100:.2f}%")
    return keep


def write_inflows(src: pathlib.Path, dst: pathlib.Path, hydro: dict,
                  t: np.ndarray, log=print):
    """把新的入流过程线写进 .inp：换掉 [INFLOWS] 与对应的 [TIMESERIES]。"""
    lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
    sec, keep, old_ts = None, [], set()
    for ln in lines:
        s = ln.strip()
        m = re.match(r"^\[(\w+)\]", s)
        if m:
            sec = m.group(1).upper()
        if sec == "INFLOWS" and not m and s and not s.startswith(";"):
            p = s.split()
            if len(p) >= 3:
                old_ts.add(p[2])
    out, sec = [], None
    for ln in lines:
        s = ln.strip()
        m = re.match(r"^\[(\w+)\]", s)
        if m:
            sec = m.group(1).upper()
            out.append(ln)
            if sec == "INFLOWS":
                for n in sorted(hydro):
                    out.append(f"{n} FLOW {n}_Inflow_Ts FLOW 1.0 1.0 0")
            continue
        if sec == "INFLOWS" and s and not s.startswith(";"):
            continue                                    # 丢掉旧的
        if sec == "TIMESERIES" and s and not s.startswith(";"):
            p = s.split()
            if p and p[0] in old_ts:
                continue                                # 丢掉旧的入流序列
        out.append(ln)
    # 新的入流序列插在 [TIMESERIES] 之后
    idx = next((i for i, l in enumerate(out) if l.strip().upper().startswith("[TIMESERIES")), None)
    block = []
    for n in sorted(hydro):
        q = hydro[n] * 1000.0                           # m³/s → LPS
        nz = np.nonzero(q > 1e-6)[0]
        if not len(nz):
            continue
        lo, hi = max(int(nz[0]) - 1, 0), min(int(nz[-1]) + 2, len(t))
        block.append(f"{n}_Inflow_Ts          0 0")
        for i in range(lo, hi):
            block.append(f"{n}_Inflow_Ts          {t[i]:.10g} {q[i]:.6g}")
        block.append(f"{n}_Inflow_Ts          {t[-1]:.10g} 0")
    if idx is None:
        out += ["[TIMESERIES]"] + block
    else:
        out = out[:idx + 1] + block + out[idx + 1:]
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    log(f"  → {dst.name}（{len(hydro):,} 个入流节点，{len(block):,} 行序列）")
    return dst


if __name__ == "__main__":
    import argparse, json, time
    from swbrain import db
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="重建产流层：降雨 → 逐节点入流过程线")
    ap.add_argument("model", choices=list(SHP))
    ap.add_argument("--P", type=float, default=P_DELIVERED, help="累计雨深 mm")
    ap.add_argument("--dur", type=float, default=24.0, help="历时 h")
    ap.add_argument("--dt", type=float, default=1.0, help="入流序列步长 min（1 min 才解得出 Tc=10 min 的单位线峰值；5 min 会把峰削掉 5%%）")
    ap.add_argument("--start", type=float, default=0.0, help="降雨起始时刻 h")
    ap.add_argument("--shape", default="auto", help="雨型形状：auto=用客户自己的设计雨型，uniform=均匀")
    ap.add_argument("--out", default=None, help="输出 .inp 的后缀，不给就只算不写")
    a = ap.parse_args()
    cfg = db.load_config(None)
    src = WORK / f"{a.model}_baseline.inp"
    dt_h = a.dt / 60.0
    print(f"{a.model}：P = {a.P} mm，历时 {a.dur} h，步长 {a.dt:g} min")
    shape = None
    if a.shape == "auto":
        shape = read_shape(src, a.dur)
        if shape is None:
            print("    退回均匀雨型")
    t, P_cum = hyetograph(a.P, a.dur, dt_h, shape, a.start)
    print(f"  雨型：累计 {P_cum[-1]:.2f} mm，"
          f"50% 雨量在第 {np.interp(0.5 * a.P, P_cum, t):.2f} h")
    subs, derived = subcatchments(cfg, a.model)
    t0 = time.perf_counter()
    H = node_hydrographs(subs, t, P_cum, dt_h)
    tot = sum(float(q.sum() * dt_h * 3600) for q in H.values())
    Qs = np.sum(list(H.values()), axis=0)
    ip = int(np.argmax(Qs))
    print(f"  产流合计 {tot:,.0f} m³　全网峰值 {Qs.max():.2f} m³/s 在第 {t[ip]:.2f} h"
          f"　（{time.perf_counter() - t0:.0f}s）")
    if a.out:
        write_inflows(src, WORK / f"{a.model}_{a.out}.inp", H, t)
