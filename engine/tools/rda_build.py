# -*- coding: utf-8 -*-
"""RD-A 汇水区一二维建模（2026-10-20）。一维从客户 SewerGEMS 导出的 inp 换算成公制重建，产流按去重后的子汇水区自己算。

数据
  一维      20261019/test/RD-A/RD-A/Hydraulic Model/RD - A.stsw_1_51079.inp（18 个 inp 管网相同，取这一个）：英制 → 公制
            （ft × 0.3048、cfs × 0.0283168、ft² × 0.0929）。井、管、湿井（TABULAR 面积曲线）、3 台泵（Pump3 曲线）、6 根压力管、3 个出水口原样
  产流      tools/rda_catchments.py 的结果（out/rda/catchments_composite.subs.csv）：每地块只算一次面积，不透水 f·A 用 CN 95、透水 (1−f)·A 用客户
            CN透水，分别算再相加（WA-726 3.4.1）；SCS CN + SCS 单位线（runoff.py，同 RB-01）。客户 inp 里的入流过程线（双倍面积）不用
  雨        客户 Mussafah 模型同历时「10 年一遇含 CC」雨型等比缩放（同 RB-01 与优化试点）
  二维      计算域 = 子汇水区并集闭运算 20 m、填洞；DTM 5 m 原值；15 m 网格（同 RB-01）
参数（参数登记表）
  既有重力管糙率  按最新全市管网管材：钢筋混凝土 0.015，其余与匹配不上的 0.013（out/pipe_material_RDA.json；用户规则，第 7 项）
  既有压力管      Hazen-Williams C = 130、拍门，客户模型原值（WA-726 表 7-2 的 135 / 140 是新管设计值，既有管沿用客户值，Claude 自定）
  泵 / 湿井       客户模型原样：Pump3 曲线、启停水深、湿井面积曲线（客户数据）
  出水口 31       固定尾水 1.3 m、拍门（客户模型原值 = WA-726 表 4-5）
  演算            动力波，ROUTING_STEP 1 s、VARIABLE_STEP 0.75、MINIMUM_STEP 0.5 s、SLOT 超载（客户模型原值：湿井只有 13.6 m²，
                  RB-01 用的 30 s 会让泵频繁启停；Claude 采用客户值）；ALLOW_PONDING NO（溢流交给二维，同 RB-01）
  井盖            模型原值（同 RB-01）；与 DTM 差 > 5 m 且井底在地面以下的改取 DTM（井 2061 井盖 20.1 m，Claude 自定）。
                  井底比 DTM 地面高 1 m 以上的井（1233–1242，井底约 8.8 m、地面 2.4 m：高架 / 平台上的排水）原样保留，
                  但不和二维地面交换（溢流记在一维里），Claude 自定
状态（2026-10-20 与用户确认；客户 18 个 inp 的分组见 docs/RD-A_建模说明.md）
  L1    现状：客户模型原样（含现有泵站 = 全市管网 SW_ABT_W33_PSS_69「Cpcty 350&80x2」；9 个情景文件夹、AECOM「Existing」都是这个）
  L2    L1 + 热点表 planA 预置移动泵（同 RB-01 L2 规则）
  L2P   L2+planned（暂定）：客户 inp 51118 = 泵站扩容（轴流泵换曲线 51122、1 → 4 台，2 根 2.0 m 压力管到 51083 和新出水口 51167，删管 3171 / 3257 / 3694）。
        客户只在旧 24 h 设计雨下算过，来源未确认（疑 AECOM 概念方案）；用户同意暂定，文档要写清
  L2PB  参考：客户 inp 51159 = 现有泵站不动，井 2156 / 1409 / 2015 各加 1 台泵到新出水口 51173 / 51236（同样来源未确认）
  方案 inp 删掉的节点上原有的入流挪到最近的井（Claude 自定）
用法：python tools/rda_build.py l1 --tag S61D1 --rain 61 --dur 1 [--tail 4] [--f-col f|f_min]
产物：$WORK/RDA_{tag}_{态}.inp、c2d/pilot_RDA_{tag}_{态}.json、c2d/dem_RDA_{tag}.tif、out/rda_inputs_{tag}_{态}.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import runoff as R  # noqa: E402
import rb01_build as RB  # noqa: E402
from workdir import WORK  # noqa: E402

SRC_INP = ROOT / "20261019/test/RD-A/RD-A/Hydraulic Model/RD - A.stsw_1_51079.inp"
SHP = ROOT / "20261019/test/RD-A/RD-A/2026-1hr"
SUBS = ROOT / "out/rda/catchments_composite.subs.csv"
PLOTS = ROOT / "out/rda/catchments_composite.gpkg"
PIPE_MAT = ROOT / "out/pipe_material_RDA.json"
FT, CFS, FT2 = 0.3048, 0.028316846592, 0.09290304
RIM_FIX_M = 5.0


def sections(t):
    """[名] → 行列表（去注释、去空行）"""
    out, cur = {}, None
    for ln in t.splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]; out[cur] = []
        elif cur and s and not s.startswith(";"):
            out[cur].append(s.split())
    return out


def dtm_at(xy):
    import rasterio
    with rasterio.open(RB.DTM) as ds:
        return np.array([v[0] for v in ds.sample(xy)], float)


def build_network(src=SRC_INP, log=print):
    """客户 inp → 公制的各节（列表），以及井表 N（名 → x, y, 地面, 井底）"""
    S = sections(src.read_text(encoding="utf-8", errors="ignore"))
    mat = json.loads(PIPE_MAT.read_text(encoding="utf-8"))["pipes"]
    coords = {r[0]: (float(r[1]) * FT, float(r[2]) * FT) for r in S["COORDINATES"]}
    J = {r[0]: [float(r[1]) * FT, float(r[2]) * FT, float(r[3]) * FT, float(r[4]) * FT, float(r[5]) * FT2] for r in S["JUNCTIONS"]}
    # 井盖与 DTM 差 > 5 m 的改取 DTM（井盖录错）
    names = [k for k in J if k in coords]
    z = dtm_at([coords[k] for k in names])
    fixed, elevated = [], []
    for k, zd in zip(names, z):
        rim = J[k][0] + J[k][1]
        if not (J[k][1] > 0 and np.isfinite(zd) and zd > -100 and J[k][3] < 1000):
            continue
        if J[k][0] > zd + 1.0:
            elevated.append(k)
        elif abs(rim - zd) > RIM_FIX_M:
            fixed.append({"node": k, "rim_model": round(rim, 2), "rim_dtm": round(float(zd), 2)}); J[k][1] = max(float(zd) - J[k][0], 0.5)
    log(f"  井盖改取 DTM：{fixed}；高于地面、不和二维交换：{elevated}")
    OF = []
    for r in S["OUTFALLS"]:
        inv = float(r[1]) * FT
        if r[2] == "FIXED":
            OF.append((r[0], inv, f"FIXED {float(r[3]) * FT:.3f}", r[4]))
        else:
            OF.append((r[0], inv, r[2], r[3]))
    ST = [(r[0], float(r[1]) * FT, float(r[2]) * FT, float(r[3]) * FT, r[4], r[5]) for r in S["STORAGE"]]
    XS = {r[0]: r[1:] for r in S["XSECTIONS"]}
    CO, n_src = [], {"0.015 混凝土": 0, "0.013 其他材质": 0, "0.013 未匹配": 0, "压力管 H-W": 0}
    for r in S["CONDUITS"]:
        k = r[0]; fm = XS[k][0] == "FORCE_MAIN"
        if fm:
            n = float(r[4]); n_src["压力管 H-W"] += 1
        else:
            m = mat.get(k)
            n = float(m["n"]) if m else 0.013
            n_src["0.015 混凝土" if n == 0.015 else "0.013 其他材质" if m and m["material"] else "0.013 未匹配"] += 1
        CO.append((k, r[1], r[2], float(r[3]) * FT, n, float(r[5]) * FT, float(r[6]) * FT, 0, 0))
    log(f"  糙率：{n_src}")
    XO = []
    for k, x in XS.items():
        if x[0] == "FORCE_MAIN":
            XO.append((k, "FORCE_MAIN", float(x[1]) * FT, float(x[2]), 0, 0, int(x[5]) if len(x) > 5 else 1))
        else:
            XO.append((k, x[0], float(x[1]) * FT, float(x[2]) * FT, float(x[3]) * FT, float(x[4]) * FT, int(x[5]) if len(x) > 5 else 1))
    PU = [(r[0], r[1], r[2], r[3], r[4], float(r[5]) * FT, float(r[6]) * FT) for r in S["PUMPS"]]
    CU, ctype = [], {}
    for r in S["CURVES"]:
        if len(r) == 4:
            ctype[r[0]] = r[1]; x, y = float(r[2]), float(r[3])
        else:
            x, y = float(r[1]), float(r[2])
        typ = ctype[r[0]]
        if typ.upper() == "STORAGE":
            CU.append((r[0], typ, x * FT, y * FT2))
        else:                                   # Pump3：x = 扬程 ft，y = 流量 cfs
            CU.append((r[0], typ, x * FT, y * CFS))
    LO = [r for r in S["LOSSES"] if any(float(v) != 0 for v in r[1:4]) or r[4] == "YES"]
    VX = [(r[0], float(r[1]) * FT, float(r[2]) * FT) for r in S.get("VERTICES", [])]
    # 井表：给二维交换用，只放检查井（压力管节点、泵出口节点不和地面交换）
    import geopandas as gpd
    mh = gpd.read_file(SHP / "Manhole.shp")
    mh_ids = set(mh.ID.astype(str))
    N = {k: (coords[k][0], coords[k][1], v[0] + v[1], v[0]) for k, v in J.items() if k in mh_ids and k in coords and k not in elevated}
    net = {"J": J, "OF": OF, "ST": ST, "CO": CO, "XS": XO, "PU": PU, "CU": CU, "LO": LO, "VX": VX, "coords": coords,
           "n_src": n_src, "rim_fixed": fixed, "elevated": elevated, "coords_all": coords}
    return net, N


SHAPE_SRC = None      # (inp, 时间序列名)：缺省 None = Mussafah 雨型；--shape-inp 给了就用指定模型里的雨型


def aecom_shape(inp, ts, dur, log=print):
    """客户 SewerGEMS 导出 inp 里的一条雨量序列 → 无量纲累计形状（2026-10-26 加：按 AECOM 口径跑 61 mm / 1 h）。
    AECOM 的设计雨是 VOLUME 格式（每个时间点的值 = 从这一刻起一个间隔内的雨量，第一点为 0）：去掉开头的 0，逐段累加成 0…总量。"""
    rows = []
    sec = None
    for ln in pathlib.Path(inp).open(encoding="utf-8", errors="replace"):
        q = ln.strip()
        if q.startswith("["):
            sec = q.upper(); continue
        if sec == "[TIMESERIES]" and q and not q.startswith(";") and q.split()[0] == ts:
            f = q.split(); h, m = f[-2].split(":")[:2]; rows.append((int(h) * 60 + int(m), float(f[-1])))
    if not rows:
        raise SystemExit(f"{inp} 里没有雨量序列 {ts}")
    rows.sort(); v = [x for _, x in rows]
    while v and v[0] == 0:
        v.pop(0)
    step = rows[1][0] - rows[0][0]
    if abs(len(v) * step / 60 - dur) > 1e-6:
        raise SystemExit(f"{ts}：{len(v)} 段 × {step} min ≠ 历时 {dur:g} h")
    cum = np.concatenate([[0.0], np.cumsum(v)])
    log(f"    雨型取自 AECOM「{ts}」：{len(v)} 段 × {step} min、原总量 {cum[-1] * 25.4:.1f} mm、最大 {step} min 雨强 {max(v) * 25.4 * 60 / step:.0f} mm/h（按原值）")
    return cum


def hydrographs(rain, dur, f_col="f", tail=4.0, log=print):
    import pandas as pd
    subs = pd.read_csv(SUBS, dtype={"node": str})
    if f_col == "f_min":                      # 敏感性：不透水只数 Makani 画出来的建筑 / 道路 / 停车
        import geopandas as gpd
        P = gpd.read_file(PLOTS)
        fm = dict(zip(P.label, P.f_min)); A = dict(zip(P.label, P.area_m2))
        rows = []
        for r in subs.itertuples():
            base = r.label.replace("12SUI", "12SU", 1)
            if r.part == "不透水":
                rows.append((r.label, r.node, A[base] * fm[base], r.cn, r.tc_min, r.part))
            elif r.part == "透水":
                rows.append((r.label, r.node, A[base] * (1 - fm[base]), r.cn, r.tc_min, r.part))
            else:
                rows.append(tuple(r[1:]))
        subs = pd.DataFrame(rows, columns=subs.columns)
    subs = subs.rename(columns={"tc_min": "tc_min"})
    subs["tc_min"] = subs.tc_min.clip(lower=1.0)
    subs = subs[subs.area_m2 > 0]
    dt_h = 1.0 / 60.0
    shape = aecom_shape(*SHAPE_SRC, dur, log=log) if SHAPE_SRC else R.read_shape(WORK / "Mussafah_baseline.inp", dur, log=log)
    if shape is None:
        raise SystemExit(f"客户 Mussafah 模型里没有 {dur:g} h 的雨型")
    sim_h = dur + tail
    t, P = R.hyetograph(rain, dur, dt_h, shape, 0.0, total_h=sim_h)
    H = R.node_hydrographs(subs, t, P, dt_h, log=log)
    exc = np.zeros(len(t))
    for cn, g in subs.groupby("cn"):
        exc += R.scs_cum_excess(P, float(cn)) * g.area_m2.sum()
    exc /= subs.area_m2.sum()
    tot = sum(float(q.sum() * dt_h * 3600) for q in H.values())
    log(f"  产流：{len(subs):,} 块、{subs.area_m2.sum() / 1e4:.1f} ha，净雨 {exc[-1]:.1f} mm，入流合计 {tot:,.0f} m³ → {len(H):,} 个节点")
    return t, P, exc, H, sim_h, subs, tot


def write_inp(path, title, net, H, t, sim_h, extra=None, log=print):
    """extra：优化措施追加的元素（与 net 同结构的列表），本版 L1 不用"""
    eh, em = int(sim_h), int(round((sim_h % 1) * 60))
    L = [f"[TITLE]\n;; {title}\n", "[OPTIONS]"]
    for k, v in (("FLOW_UNITS", "CMS"), ("INFILTRATION", "HORTON"), ("FLOW_ROUTING", "DYNWAVE"), ("LINK_OFFSETS", "DEPTH"),
                 ("FORCE_MAIN_EQUATION", "H-W"), ("MIN_SLOPE", "0"), ("ALLOW_PONDING", "NO"), ("SKIP_STEADY_STATE", "NO"),
                 ("START_DATE", "04/16/2024"), ("START_TIME", "00:00:00"), ("REPORT_START_DATE", "04/16/2024"), ("REPORT_START_TIME", "00:00:00"),
                 ("END_DATE", "04/16/2024"), ("END_TIME", f"{eh:02d}:{em:02d}:00"), ("SWEEP_START", "01/01"), ("SWEEP_END", "12/31"),
                 ("DRY_DAYS", "0"), ("REPORT_STEP", "00:05:00"), ("WET_STEP", "00:05:00"), ("DRY_STEP", "01:00:00"),
                 ("ROUTING_STEP", "00:00:01"), ("VARIABLE_STEP", "0.75"), ("MINIMUM_STEP", "0.5"), ("LENGTHENING_STEP", "0"),
                 ("NORMAL_FLOW_LIMITED", "BOTH"), ("SURCHARGE_METHOD", "SLOT"), ("MAX_TRIALS", "8"), ("HEAD_TOLERANCE", "0.0015"), ("THREADS", "6")):
        L.append(f"{k}  {v}")
    L += ["", "[REPORT]", "INPUT  NO", "CONTROLS  NO", "SUBCATCHMENTS  NONE", "NODES  NONE", "LINKS  NONE", "", "[EVAPORATION]", "CONSTANT  0.0", "",
          "[JUNCTIONS]"]
    for k, (inv, md, ini, sur, pa) in net["J"].items():
        L.append(f"{k}  {inv:.3f}  {md:.3f}  {ini:.3f}  {sur:.3f}  {pa:.2f}")
    L += ["", "[OUTFALLS]"] + [f"{k}  {inv:.3f}  {typ}  {gate}" for k, inv, typ, gate in net["OF"]]
    L += ["", "[STORAGE]"] + [f"{k}  {inv:.3f}  {md:.3f}  {ini:.3f}  {typ}  {cv}  0  0" for k, inv, md, ini, typ, cv in net["ST"]]
    L += ["", "[CONDUITS]"] + [f"{k}  {a}  {b}  {ln:.3f}  {n:.4f}  {o1:.3f}  {o2:.3f}  0  0" for k, a, b, ln, n, o1, o2, *_ in net["CO"]]
    L += ["", "[PUMPS]"] + [f"{k}  {a}  {b}  {c}  {s}  {on:.3f}  {off:.3f}" for k, a, b, c, s, on, off in net["PU"]]
    L += ["", "[XSECTIONS]"] + [f"{k}  {typ}  {g1:.4f}  {g2:g}  {g3:g}  {g4:g}  {nb}" for k, typ, g1, g2, g3, g4, nb in net["XS"]]
    if net["LO"]:
        L += ["", "[LOSSES]"] + ["  ".join(r) for r in net["LO"]]
    L += ["", "[CURVES]"]
    seen = set()
    for k, typ, x, y in net["CU"]:
        L.append(f"{k}  {typ if k not in seen else ''}  {x:.5f}  {y:.6f}"); seen.add(k)
    L += ["", "[INFLOWS]"]
    keep = {n: q for n, q in H.items() if q.max() > 1e-7}
    for n in sorted(keep):
        L.append(f"{n}  FLOW  TS_{n}  FLOW  1.0  1.0  0")
    L += ["", "[TIMESERIES]"]
    for n in sorted(keep):
        q = keep[n]; nz = np.nonzero(q > 1e-7)[0]
        lo, hi = max(int(nz[0]) - 1, 0), min(int(nz[-1]) + 2, len(t))
        for i in range(lo, hi):
            L.append(f"TS_{n}  {t[i]:.5f}  {q[i]:.6g}")
        if hi < len(t):
            L.append(f"TS_{n}  {t[-1]:.5f}  0")
    L += ["", "[COORDINATES]"] + [f"{k}  {x:.2f}  {y:.2f}" for k, (x, y) in net["coords"].items()]
    if net["VX"]:
        L += ["", "[VERTICES]"] + [f"{k}  {x:.2f}  {y:.2f}" for k, x, y in net["VX"]]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    log(f"  → {path.name}（{path.stat().st_size / 1048576:.1f} MB）：井 {len(net['J']):,}、管 {len(net['CO']):,}、出水口 {len(net['OF'])}、"
        f"蓄水 {len(net['ST'])}、泵 {len(net['PU'])}、入流节点 {len(keep):,}")


def run_info(tag: str, state: str = "L1") -> dict:
    return json.loads((ROOT / "out" / f"rda_inputs_{tag}_{state}.json").read_text(encoding="utf-8"))


# 状态 → 客户 inp（同一套管网的不同方案）
# 2026-10-20 晚按 docs/L1_L2_L2planned_识别方法.md（SewerGEMS 情景树 + 热点表）改定，用户：「可以，按这个推进」：
#   L2P  = 情景「Temporary Intervention」（inp 51159）里列入计划的 2 台泵 51207、51225 与其压力管；同情景的泵 51169 离有计划的热点 534 m，
#          判「只是方案」，去掉（连同它的出水管 51172 与新出水口 51173）
#   L2PA = 51118 泵站扩容：情景树里已删，只剩导出，作参考（之前暂定的 L2+planned）
#   L2PB = 51159 原样（含 51169），参考
SRC = {"L1": SRC_INP, "L2": SRC_INP,
       "L2P": SRC_INP.with_name("RD - A.stsw_1_51159.inp"),
       "L2PA": SRC_INP.with_name("RD - A.stsw_1_51118.inp"),
       "L2PB": SRC_INP.with_name("RD - A.stsw_1_51159.inp")}
DROP_PUMPS = {"L2P": {"51169"}}
TITLE = {"L1": "现状：客户模型原样，含现有 RD-A 泵站",
         "L2": "L1 + 热点表 planA 预置移动泵",
         "L2P": "L2+planned：情景 Temporary Intervention（inp 51159）中列入计划的泵 51207、51225 及压力管（去掉只是方案的 51169）",
         "L2PA": "参考：inp 51118 泵站扩容（情景树里已删，算不算数待客户确认）",
         "L2PB": "参考：inp 51159 原样（含只是方案的泵 51169）"}
MOBILE = RB.MOBILE


def drop_pumps(net, ids, log=print):
    """去掉这些泵，以及从它出口一路接下去的压力管、压力管节点、只被它用的出水口"""
    gone_links, gone_nodes = set(), set()
    out_of = {p[0]: p[2] for p in net["PU"]}
    stack = [out_of[i] for i in ids if i in out_of]
    adj = {}
    for c in net["CO"]:
        adj.setdefault(c[1], []).append(c)
    fm = {x[0] for x in net["XS"] if x[1] == "FORCE_MAIN"}
    while stack:
        n = stack.pop()
        if n in gone_nodes:
            continue
        gone_nodes.add(n)
        for c in adj.get(n, []):
            if c[0] in fm:
                gone_links.add(c[0]); stack.append(c[2])
    still = {c[1] for c in net["CO"] if c[0] not in gone_links} | {c[2] for c in net["CO"] if c[0] not in gone_links} | \
            {p[1] for p in net["PU"] if p[0] not in ids} | {p[2] for p in net["PU"] if p[0] not in ids}
    gone_nodes -= still
    net["PU"] = [p for p in net["PU"] if p[0] not in ids]
    net["CO"] = [c for c in net["CO"] if c[0] not in gone_links]
    net["XS"] = [x for x in net["XS"] if x[0] not in gone_links]
    net["LO"] = [r for r in net["LO"] if r[0] not in gone_links]
    net["J"] = {k: v for k, v in net["J"].items() if k not in gone_nodes}
    net["OF"] = [o for o in net["OF"] if o[0] not in gone_nodes]
    used = {p[3] for p in net["PU"]} | {s[5] for s in net["ST"]}
    net["CU"] = [c for c in net["CU"] if c[0] in used or not c[0] or c[1].upper() == "STORAGE"]
    net["coords"] = {k: v for k, v in net["coords"].items() if k not in gone_nodes}
    net["VX"] = [v for v in net["VX"] if v[0] not in gone_links]
    log(f"  去掉泵 {sorted(ids)}，连带压力管 {sorted(gone_links)}、节点 / 出水口 {sorted(gone_nodes)}")


def remap_inflows(H, net, N, log=print):
    """方案 inp 删掉的节点（如 51118 删了井 1062、1504）：原来进这些节点的入流挪到最近的井（Claude 自定）"""
    have = set(net["J"]) | {s[0] for s in net["ST"]}
    miss = [n for n in H if n not in have]
    if not miss:
        return H, []
    from scipy.spatial import cKDTree
    keys = list(N)
    tr = cKDTree(np.array([N[k][:2] for k in keys]))
    co = dict(net["coords_all"])
    base = sections(SRC_INP.read_text(encoding="utf-8", errors="ignore"))["COORDINATES"]       # 删掉的节点坐标从现状 inp 取
    for r in base:
        co.setdefault(r[0], (float(r[1]) * FT, float(r[2]) * FT))
    moved = []
    for n in miss:
        if n not in co:
            raise SystemExit(f"出口节点 {n} 不在 inp 里，也没有坐标")
        d, i = tr.query(co[n]); to = keys[i]
        H[to] = H.get(to, 0) + H.pop(n); moved.append({"from": n, "to": to, "dist_m": round(float(d), 1)})
    log(f"  入流改挂：{moved}")
    return H, moved


def mobile_pumps(net, N, ref_rpt, log=print):
    """L2：热点表 planA 移动泵（同 RB-01 L2 规则：吸水井 = 热点最近的井，接收井 = 沿管往下游第一口在 L1 一维结果里不溢流的井，2 km 内）"""
    import heapq
    import geopandas as gpd
    from scipy.spatial import cKDTree
    fl = RB.flooded(ref_rpt)
    dom = gpd.read_file(ROOT / "out/dataqc/RD-A/input/rda_domain.geojson").to_crs(32640)
    hs = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640)
    hs = hs[hs.intersects(dom.geometry.iloc[0].buffer(200))]
    J = net["J"]
    down = {}
    for k, a_, b, ln, n, o1, o2, *_ in net["CO"]:
        if a_ in J and b in J:
            za, zb = J[a_][0] + o1, J[b][0] + o2
            u, v = (a_, b) if za >= zb else (b, a_)
            down.setdefault(u, []).append((v, ln))
    jn = list(N); tr = cKDTree(np.array([N[k][:2] for k in jn]))
    pumps, skipped = [], []
    for h in hs.itertuples():
        units = []
        for col, (sz, q) in MOBILE.items():
            v = getattr(h, col)
            units += [(sz, q)] * (int(v) if v == v and v else 0)
        if not units:
            units = [("6in", MOBILE["planA_pump6"][1])] * int(h.planA_pumps or 0)
        if not units:
            continue
        d0, i0 = tr.query((h.geometry.x, h.geometry.y)); suc = jn[i0]
        best, seen, pq = None, {suc: 0.0}, [(0.0, suc)]
        while pq:
            d, k = heapq.heappop(pq)
            if d > RB.MAX_PATH:
                break
            if k != suc and k not in fl and k in N:
                best = (k, d); break
            for b, Ln in down.get(k, []):
                if d + Ln < seen.get(b, 1e18):
                    seen[b] = d + Ln; heapq.heappush(pq, (d + Ln, b))
        if best is None:
            skipped.append({"hotspot": h.hotspot_id, "why": "2 km 管长内没有 L1 不溢流的下游井", "units": len(units)}); continue
        dep = max(N[suc][2] - N[suc][3], 0.5)
        on = 1.0 if dep >= 1.2 else round(0.8 * dep, 2)
        off = 0.3 if on > 0.4 else round(0.3 * on, 2)
        for j, (sz, q) in enumerate(units):
            pid = f"L2_{h.hotspot_id}_{j + 1}"
            pumps.append({"id": pid, "hotspot": h.hotspot_id, "size": sz, "from": suc, "suction_dist_m": round(float(d0)), "to": best[0],
                          "path_m": round(best[1]), "q_m3s": q, "on": on, "off": off, "well_depth_m": round(dep, 2)})
            net["PU"].append((pid, suc, best[0], f"PC_{pid}", "ON", on, off))
            net["CU"] += [(f"PC_{pid}", "PUMP2", 0.0, q), (f"PC_{pid}", "PUMP2", 100.0, q)]
    for p_ in pumps:
        log(f"    {p_['id']:<14} {p_['size']} {p_['q_m3s'] * 1000:.0f} L/s  {p_['from']}（{p_['suction_dist_m']} m）→ {p_['to']} {p_['path_m']} m  开 {p_['on']} 停 {p_['off']}")
    log(f"  移动泵 {len(pumps)} 台；没建的：{skipped}")
    return pumps, skipped


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("state", choices=["l1", "l2", "l2p", "l2pa", "l2pb"])
    ap.add_argument("--tag", required=True); ap.add_argument("--rain", type=float, required=True); ap.add_argument("--dur", type=float, default=1.0)
    ap.add_argument("--tail", type=float, default=4.0)
    ap.add_argument("--f-col", choices=["f", "f_min"], default="f", help="不透水比例：f（缺省，Makani 没画的硬地按不透水）/ f_min（只数建筑、道路、停车）")
    ap.add_argument("--no-2d", action="store_true", help="只出 inp（一维敏感性用）")
    ap.add_argument("--ref-rpt", default=None, help="L2 选接收井用的 L1 一维报告（缺省 c2d/RDA_{tag}_L1_1d.rpt）")
    ap.add_argument("--shape-inp", default=None, help="雨型改取这个 inp 里的序列（缺省 Mussafah 同历时雨型），与 --shape-ts 同用")
    ap.add_argument("--shape-ts", default=None, help="--shape-inp 里的雨量序列名（VOLUME 格式）")
    a = ap.parse_args(argv)
    global SHAPE_SRC
    if a.shape_inp:
        SHAPE_SRC = (a.shape_inp, a.shape_ts)
    import geopandas as gpd
    name = f"RDA_{a.tag}"; st = a.state.upper()
    print(f"{name} · {st} · {a.rain:g} mm / {a.dur:g} h · 不透水 {a.f_col} · {SRC[st].name}")
    net, N = build_network(SRC[st])
    if DROP_PUMPS.get(st):
        drop_pumps(net, DROP_PUMPS[st])
    t, P, exc, H, sim_h, subs, tot = hydrographs(a.rain, a.dur, a.f_col, a.tail)
    H, moved = remap_inflows(H, net, N)
    extra = {}
    if st == "L2":
        rpt = pathlib.Path(a.ref_rpt) if a.ref_rpt else WORK / "c2d" / f"{name}_L1_1d.rpt"
        pumps, skipped = mobile_pumps(net, N, rpt)
        extra = {"pumps_l2": pumps, "skipped": skipped, "receiver_basis": str(rpt)}
    write_inp(WORK / f"{name}_{st}.inp", f"RD-A · {st}（{TITLE[st]}）· {a.rain:g} mm / {a.dur:g} h · 不透水 {a.f_col}", net, H, t, sim_h)
    info = {"name": name, "state": st, "title": TITLE[st], "rain_mm": a.rain, "dur_h": a.dur, "sim_h": sim_h, "f_col": a.f_col, "src_inp": str(SRC[st]),
            "subs_n": len(subs), "subs_ha": round(float(subs.area_m2.sum() / 1e4), 2), "excess_mm_aw": round(float(exc[-1]), 2),
            "runoff_m3": round(tot), "pipe_n": net["n_src"], "rim_fixed": net["rim_fixed"], "elevated_no_2d": net["elevated"], "inflow_moved": moved,
            "n_pumps": len(net["PU"]), "n_outfalls": len(net["OF"]), "n_nodes_2d": len(N), "mesh_res_m": 15.0,
            "rain_shape": f"{pathlib.Path(a.shape_inp).name} · {a.shape_ts}" if a.shape_inp else "Mussafah 同历时雨型", **extra}
    if not a.no_2d:
        cat = gpd.read_file(SHP / "Catchment.shp").set_crs(32640, allow_override=True)
        D = RB.domain(cat)
        if not (WORK / "c2d" / f"dem_{name}.tif").exists():
            RB.write_dem(name, D)
        RB.write_meta(name, st, D, N, t, exc, {"note": f"RD-A {st}（tools/rda_build.py）：{TITLE[st]}"})
        info.update(domain_km2=round(D.area / 1e6, 3))
    (ROOT / "out" / f"rda_inputs_{a.tag}_{st}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
