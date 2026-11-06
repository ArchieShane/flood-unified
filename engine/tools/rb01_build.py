# -*- coding: utf-8 -*-
"""RB-01 汇水区一二维四态建模（2026-10-16，用户「都按建议来」）。数据**只用** Parsons 导出的 RB-01 模型 SHP
（Parsons_Model Data Export/26092026/RB-01，"SHP files exported after enhancement" = 改造后模型）。

  计算域    RB-01 子汇水区并集，20 m 闭运算连成一块、填洞（Claude 自定 20 m）；封闭汇水区，域外来水不算
  一维      模型自带管网：Manholes + Catch basins = 井，Conduits = 圆管（D、INV_STRT/STP、L、MANN_N 原值）
  出水口    RBW3 两个口：固定尾水位 1.3 m、拍门（模型原设置）
  产流      逐子汇水区 SCS CN（SCS_CN）+ SCS 单位线（TC），面积 = A_UNIFIED（同一个面拆给几口井时各记一份，
            合计 11.08 km² ≈ 并集 11.13 km²，不去重），汇到 OUT_NODE；全域都有汇水区，二维上不再直接下雨
  雨        客户 Mussafah 模型「10 年一遇含 CC 1 h」雨型等比放大到 --rain（缺省 61 mm；2026-10-19 前误用 60 mm，见 H60）；模拟 历时 + 4 h
  L1        去掉塘、泵、压力管、湿井 W-4/6/8 及接湿井的 3 根管、28 个 O- 出水口
  L2P       PO 塘方案（来源待确认）原样：8 个塘（一维蓄水节点，塘底 = 轮廓内 DTM 最低点、1.0 m 深、直立壁，同 C1 D14）、
            28 台泵（固定流量 = PUMP_FLOW，为 0 时取 361.11 L/s；启停 = 吸水井深 50 % / 10 %，同 C1 D13），
            泵沿压力管追到 O- 出水口：口在塘里 → 直接打进塘的一维节点；O-26/27/30/31 → 自由出流（离开系统）
  L2        L1 + 热点表 planA 移动泵（6 英寸 180 m³/h、12 英寸 700 m³/h，config 泵型表），吸水井 = 热点最近的井，
            接收井 = 沿管往下游第一口在 L1 结果里不溢流的井（同 C1 L2 规则）

用法：python tools/rb01_build.py l1|l2p|l2 [--tag H61] [--rain 61] [--dur 1]
产物：$WORK/RB01_{tag}_{态}.inp、c2d/pilot_RB01_{tag}_{态}.json、c2d/dem_RB01_{tag}.tif、out/rb01_inputs_{tag}_{态}.json
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import runoff as R  # noqa: E402
from workdir import WORK  # noqa: E402

SRC = pathlib.Path("D:/阿布扎比/Parsons_Model Data Export/26092026/RB-01")
DTM = "D:/阿布扎比/DTM_z40_customer/AUH_DTM_5m_Z40.TIF"
CLOSE_M = 20.0           # 计算域闭运算（把相隔十几米的碎块连上）
POND_D = 1.0             # 塘深（同 C1 D14）
Q_DEFAULT = 0.36111      # PUMP_FLOW 为 0 的泵按 12 英寸 361.11 L/s（吸水管 Q_(MAX) 也是这个数）
TW_RBW3 = 1.3
OUTFALL_CHAMBER = {"MH-32"}   # RBW3 出口前的井（PMP-24/25/28/29 从这里抽）
HS = ["ADM-101", "ADM-140", "ADM-205", "ADM-206", "ADM-291", "ADM-293"]
MOBILE = {"planA_pump12": ("12in", 700 / 3600), "planA_pump8": ("8in", 350 / 3600), "planA_pump6": ("6in", 180 / 3600)}
MAX_PATH = 2000.0


def sid(s):
    """SWMM 名字里不放 / 和空格"""
    return re.sub(r"[^\w\-.]", "_", str(s))


def load(src=SRC):
    import geopandas as gpd
    rd = lambda n: gpd.read_file(src / f"{n}.shp").set_crs(32640, allow_override=True)
    L = {k: rd(n) for k, n in (("ca", "Catchments"), ("co", "Conduits"), ("mh", "Manholes"), ("cb", "Catch basins"),
                               ("of", "Outfalls"), ("po", "Ponds"), ("pp", "Pressure pipes"), ("pu", "Pumps"), ("ww", "Wet Well"))}
    L["ca"]["geometry"] = L["ca"].geometry.make_valid()
    return L


def domain(ca, log=print):
    from shapely.geometry import Polygon
    U = ca.union_all()
    V = U.buffer(CLOSE_M).buffer(-CLOSE_M)
    parts = sorted(getattr(V, "geoms", [V]), key=lambda p: -p.area)
    if len(parts) > 1 and parts[1].area > 0.01 * parts[0].area:
        raise SystemExit(f"计算域闭运算 {CLOSE_M} m 后仍有 {len(parts)} 块：{[round(p.area / 1e6, 3) for p in parts]} km²")
    D = Polygon(parts[0].exterior)
    log(f"  计算域：子汇水区并集 {U.area / 1e6:.2f} km² → 闭运算 {CLOSE_M:g} m、填洞 → {D.area / 1e6:.2f} km²")
    return D
def nodes_table(L):
    """井 = Manholes + Catch basins：名字 → (x, y, 地面, 井底)"""
    N = {}
    for k in ("mh", "cb"):
        for r in L[k].itertuples():
            N[sid(r.LABEL)] = (r.geometry.x, r.geometry.y, float(r.ELEV_GND), float(r.ELEV_INV))
    return N


def hydrographs(L, rain, dur, log=print, tail=4.0):
    """逐子汇水区 SCS CN + 单位线 → 逐节点入流（runoff.py 同一套算法，客户自己的方法）"""
    import pandas as pd
    ca = L["ca"]
    subs = pd.DataFrame({"area_m2": ca.A_UNIFIED.astype(float), "cn": ca.SCS_CN.astype(float),
                         "tc_min": ca.TC.astype(float).clip(lower=1.0), "node": ca.OUT_NODE.map(sid)})
    subs = subs[subs.area_m2 > 0]
    dt_h = 1.0 / 60.0
    shape = R.read_shape(WORK / "Mussafah_baseline.inp", dur, log=log)
    if shape is None:
        raise SystemExit(f"客户 Mussafah 模型里没有 {dur:g} h 的雨型")
    sim_h = dur + tail                 # 雨后再模拟 tail 小时（缺省 4；优化试点用 10，2026-10-19）
    t, P = R.hyetograph(rain, dur, dt_h, shape, 0.0, total_h=sim_h)
    H = R.node_hydrographs(subs, t, P, dt_h, log=log)
    tot = sum(float(q.sum() * dt_h * 3600) for q in H.values())
    # 面积加权的平均净雨（只作记录）
    exc = np.zeros(len(t))
    for (cn), g in subs.groupby("cn"):
        exc += R.scs_cum_excess(P, float(cn)) * g.area_m2.sum()
    exc /= subs.area_m2.sum()
    log(f"  产流：{len(subs):,} 个子汇水区、{subs.area_m2.sum() / 1e6:.2f} km²，面积加权 CN {np.average(subs.cn, weights=subs.area_m2):.1f}，"
        f"净雨 {exc[-1]:.1f} mm，入流合计 {tot:,.0f} m³ → {len(H):,} 个节点")
    return t, P, exc, H, sim_h, subs


def flooded(rpt):
    t = pathlib.Path(rpt).read_text(errors="ignore")
    m = re.search(r"Node Flooding Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    if not m:
        if "No nodes were flooded" in t:
            return set()
        raise SystemExit(f"{rpt} 里没有 Node Flooding Summary")
    return {ln.split()[0] for ln in m.group(1).splitlines() if ln.strip()}


def pond_floor(geom):
    import rasterio
    from rasterio.mask import mask
    with rasterio.open(DTM) as r:
        arr, _ = mask(r, [geom], crop=True, nodata=-9999)
    v = arr[0][arr[0] > -9000]
    return float(v.min())
def pump_routes(L, N):
    """压力管图：每台泵 → (吸水节点, 出口 O-x)。吸水端 = 与泵相连的井 / 湿井，出口沿 J- 节点追到 O-。"""
    adj = {}
    for r in L["pp"].itertuples():
        a, b = sid(r.START_NODE), sid(r.STOP_NODE)
        adj.setdefault(a, set()).add(b); adj.setdefault(b, set()).add(a)
    wws = {sid(x) for x in L["ww"].LABEL}
    out = {}
    for r in L["pu"].itertuples():
        p = sid(r.LABEL); nb = adj.get(p, set())
        suc = [n for n in nb if n in N or n in wws]
        if len(suc) != 1:
            raise SystemExit(f"泵 {p} 的吸水节点不是一个：{nb}")
        dst, seen, stack = None, {p, suc[0]}, [n for n in nb if n != suc[0]]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            if n.startswith("O-"):
                dst = n; break
            if n.startswith("J-"):
                stack += list(adj.get(n, ()))
        if dst is None:
            raise SystemExit(f"泵 {p} 沿压力管追不到 O- 出水口：{nb}")
        q = float(r.PUMP_FLOW) / 1000.0
        out[p] = {"suction": suc[0], "dst": dst, "q_m3s": q if q > 0 else Q_DEFAULT, "q_src": "PUMP_FLOW" if q > 0 else "模型 PUMP_FLOW = 0，按 12 英寸 361.11 L/s（Claude 自定）",
                  "x": r.geometry.x, "y": r.geometry.y}
    return out


def write_inp(path, title, N, conduits, outfalls, storage, pumps, H, t, sim_h, log=print):
    """outfalls: [(名, 井底, 'FIXED 1.3' | 'FREE', 拍门 YES/NO, x, y)]；storage: [(名, 底, 深, 面积 m², x, y)]；
    pumps: [{id, from, to, q_m3s, on, off}]"""
    st_names = {s[0] for s in storage}; of_names = {o[0] for o in outfalls}
    eh, em = int(sim_h), int(round((sim_h % 1) * 60))
    Ls = [f"[TITLE]\n;; {title}\n", "[OPTIONS]"]
    for k, v in (("FLOW_UNITS", "CMS"), ("INFILTRATION", "HORTON"), ("FLOW_ROUTING", "DYNWAVE"), ("LINK_OFFSETS", "ELEVATION"),
                 ("MIN_SLOPE", "0"), ("ALLOW_PONDING", "NO"), ("SKIP_STEADY_STATE", "NO"),
                 ("START_DATE", "04/16/2024"), ("START_TIME", "00:00:00"), ("REPORT_START_DATE", "04/16/2024"),
                 ("REPORT_START_TIME", "00:00:00"), ("END_DATE", "04/16/2024"), ("END_TIME", f"{eh:02d}:{em:02d}:00"),
                 ("SWEEP_START", "01/01"), ("SWEEP_END", "12/31"), ("DRY_DAYS", "0"), ("REPORT_STEP", "00:05:00"),
                 ("WET_STEP", "00:05:00"), ("DRY_STEP", "01:00:00"), ("ROUTING_STEP", "00:00:30"),
                 ("LENGTHENING_STEP", "10"), ("THREADS", "6")):
        Ls.append(f"{k}  {v}")
    Ls += ["", "[REPORT]", "INPUT  NO", "CONTROLS  NO", "SUBCATCHMENTS  NONE", "NODES  NONE", "LINKS  NONE", "",
           "[EVAPORATION]", "CONSTANT  0.0", "", "[JUNCTIONS]"]
    for k, (x, y, zg, zi) in N.items():
        if k in st_names or k in of_names:
            continue
        Ls.append(f"{k}  {zi:.3f}  {max(zg - zi, 0.5):.3f}  0  0  0")
    Ls += ["", "[OUTFALLS]"]
    for k, zi, typ, gate, *_ in outfalls:
        Ls.append(f"{k}  {zi:.3f}  {typ}  {gate}")
    if storage:
        Ls += ["", "[STORAGE]"]
        for k, zb, d, A, *_ in storage:
            Ls.append(f"{k}  {zb:.3f}  {d:.3f}  0  FUNCTIONAL  0  0  {A:.1f}  0  0")
    Ls += ["", "[CONDUITS]"]
    for c in conduits:
        Ls.append(f"{c['id']}  {c['n1']}  {c['n2']}  {max(c['len'], 1.0):.3f}  {c['n']:.4f}  {c['z1']:.3f}  {c['z2']:.3f}  0  0")
    if pumps:
        Ls += ["", "[PUMPS]"]
        for p in pumps:
            Ls.append(f"{p['id']}  {p['from']}  {p['to']}  PC_{p['id']}  ON  {p['on']:.2f}  {p['off']:.2f}")
    Ls += ["", "[XSECTIONS]"]
    for c in conduits:
        Ls.append(f"{c['id']}  CIRCULAR  {c['d']:.3f}  0  0  0  1")
    if pumps:
        Ls += ["", "[CURVES]"]
        for p in pumps:
            Ls += [f"PC_{p['id']}  PUMP2  0  {p['q_m3s']:.4f}", f"PC_{p['id']}  100  {p['q_m3s']:.4f}"]
    Ls += ["", "[INFLOWS]"]
    keep = {n: q for n, q in H.items() if q.max() > 1e-7}
    for n in sorted(keep):
        Ls.append(f"{n}  FLOW  TS_{n}  FLOW  1.0  1.0  0")
    Ls += ["", "[TIMESERIES]"]
    for n in sorted(keep):
        q = keep[n]; nz = np.nonzero(q > 1e-7)[0]
        lo, hi = max(int(nz[0]) - 1, 0), min(int(nz[-1]) + 2, len(t))
        for i in range(lo, hi):
            Ls.append(f"TS_{n}  {t[i]:.5f}  {q[i]:.6g}")
        if hi < len(t):
            Ls.append(f"TS_{n}  {t[-1]:.5f}  0")
    Ls += ["", "[COORDINATES]"]
    for k, (x, y, *_r) in N.items():
        Ls.append(f"{k}  {x:.2f}  {y:.2f}")
    for k, zi, typ, gate, x, y in outfalls:
        if k not in N:
            Ls.append(f"{k}  {x:.2f}  {y:.2f}")
    for k, zb, d, A, x, y in storage:
        if k not in N:
            Ls.append(f"{k}  {x:.2f}  {y:.2f}")
    path.write_text("\n".join(Ls) + "\n", encoding="utf-8")
    log(f"  → {path.name}（{path.stat().st_size / 1048576:.1f} MB）：井 {len(N) - len(of_names & set(N)):,}、管 {len(conduits):,}、"
        f"出水口 {len(outfalls)}、蓄水 {len(storage)}、泵 {len(pumps)}、入流节点 {len(keep):,}")
# 既有管糙率（参数登记表第 7 项，2026-10-19）："material" = 按客户 GDB 管材，混凝土 0.015、其余与匹配不上的 0.013（用户给的规则，
# tools/pipe_material.py → out/pipe_material_RB01.json）；"parsons" = 客户 Parsons 模型原值（0.011–0.013，= WA-726 表 4-1 新管值；H60 / H61 用的是这个）
PIPE_N_MODE = "material"
_PIPE_MAT = None


def pipe_n(r):
    global _PIPE_MAT
    if PIPE_N_MODE == "parsons":
        return float(r.MANN_N or 0.013)
    if _PIPE_MAT is None:
        _PIPE_MAT = json.loads((ROOT / "out" / "pipe_material_RB01.json").read_text(encoding="utf-8"))["pipes"]
    return float(_PIPE_MAT.get(str(r.LABEL), {}).get("n", 0.013))


def base_network(L, N, with_facilities):
    """重力管 + RBW3 出水口；with_facilities 时带湿井和接湿井的管"""
    wws = {sid(r.LABEL): r for r in L["ww"].itertuples()}
    conduits, ww_inv = [], {}
    for r in L["co"].itertuples():
        a, b = sid(r.START_NODE), sid(r.STOP_NODE)
        if (a in wws or b in wws) and not with_facilities:
            continue
        if b in wws:
            ww_inv[b] = min(ww_inv.get(b, 1e9), float(r.INV_STP))
        conduits.append({"id": sid(r.LABEL), "n1": a, "n2": b, "len": float(r.L), "n": pipe_n(r),
                         "z1": float(r.INV_STRT), "z2": float(r.INV_STP), "d": float(r.D) / 1000.0})
    N = dict(N)
    if with_facilities:
        for k, r in wws.items():
            N[k] = (r.geometry.x, r.geometry.y, float(r.ELEV_GND), ww_inv.get(k, float(r.INIT_ELEV)) - 0.0)
    outfalls = []
    for r in L["of"].itertuples():
        k = sid(r.LABEL)
        if k.startswith("SW-ML-OUTFALL"):
            outfalls.append((k, float(r.ELEV_INV), f"FIXED  {TW_RBW3}", "YES", r.geometry.x, r.geometry.y))
    return N, conduits, outfalls


def write_meta(name, state, D, N, t, exc, extra, res=15.0):
    from shapely.geometry import Point
    from shapely.prepared import prep
    c2d = WORK / "c2d"; c2d.mkdir(exist_ok=True)
    pd_ = prep(D)
    poly = [[round(x, 1), round(y, 1)] for x, y in D.simplify(res).exterior.coords[:-1]]
    meta = {"name": name, "state": state, "box": D.bounds, "domain_polygon": poly,
            # 全域都有子汇水区（产流全部经单位线进一维），二维上不直接下雨：服务半径取极大
            "R_served_m": 1e9,
            "nodes": {k: [round(v[0], 2), round(v[1], 2)] for k, v in N.items() if pd_.contains(Point(v[0], v[1]))},
            "ponds": [], "rain_t_h": [round(float(x), 4) for x in t], "rain_excess_mm": [round(float(x), 4) for x in exc]}
    meta.update(extra)
    (c2d / f"pilot_{name}_{state}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def write_dem(name, D):
    import rasterio
    from rasterio.windows import from_bounds
    X0, Y0, X1, Y1 = D.bounds
    f = WORK / "c2d" / f"dem_{name}.tif"
    with rasterio.open(DTM) as ds:
        win = from_bounds(X0 - 50, Y0 - 50, X1 + 50, Y1 + 50, ds.transform).round_offsets().round_lengths()
        z = ds.read(1, window=win)
        prof = ds.profile.copy(); prof.update(width=z.shape[1], height=z.shape[0], transform=ds.window_transform(win), compress="deflate")
        with rasterio.open(f, "w", **prof) as w:
            w.write(z, 1)
    return f, z.shape
def run_info(tag: str) -> dict:
    """这次运行的雨量 / 历时 / 模拟时长（rb01_build l1 写的 out/rb01_inputs_{tag}_L1.json）。后处理、报告、交付都从这里取，不写死。"""
    return json.loads((ROOT / "out" / f"rb01_inputs_{tag}_L1.json").read_text(encoding="utf-8"))


def scen_label(tag: str) -> str:
    i = run_info(tag)
    return f"{i['rain_mm']:g} mm / {i['dur_h']:g} h"


def delivery_dir(tag: str) -> pathlib.Path:
    """交付目录 out/交付/RB01_{雨量}mm{历时}h（H60 → RB01_60mm1h，H61 → RB01_61mm1h）"""
    i = run_info(tag)
    return ROOT / "out" / "交付" / f"RB01_{i['rain_mm']:g}mm{i['dur_h']:g}h"


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("state", choices=["l1", "l2p", "l2"])
    ap.add_argument("--tag", default="H61"); ap.add_argument("--rain", type=float, default=61.0); ap.add_argument("--dur", type=float, default=1.0)
    ap.add_argument("--tail", type=float, default=4.0, help="雨停后再模拟几小时（缺省 4）")
    ap.add_argument("--pipe-n", choices=["material", "parsons"], default="material", help="既有管糙率：按管材（缺省，2026-10-19 起）/ Parsons 原值")
    ap.add_argument("--ref-rpt", default=None, help="L2 选接收井用的 L1 报告（缺省 = 一维单算的 L1：c2d/RB01_{tag}_L1_1d.rpt）")
    a = ap.parse_args(argv)
    global PIPE_N_MODE
    PIPE_N_MODE = a.pipe_n
    import geopandas as gpd
    from shapely.geometry import Point
    name = f"RB01_{a.tag}"
    L = load(); N0 = nodes_table(L)
    print(f"{name} · {a.state.upper()} · {a.rain:g} mm / {a.dur:g} h · 数据 {SRC}")
    D = domain(L["ca"])
    t, P, exc, H, sim_h, subs = hydrographs(L, a.rain, a.dur, tail=a.tail)
    miss = [n for n in H if n not in N0]
    if miss:
        raise SystemExit(f"OUT_NODE 不在井表里：{miss[:10]}")
    info = {"name": name, "rain_mm": a.rain, "dur_h": a.dur, "sim_h": sim_h, "pipe_n": a.pipe_n, "domain_km2": round(D.area / 1e6, 3),
            "catch_n": len(subs), "catch_km2": round(subs.area_m2.sum() / 1e6, 3), "cn_aw": round(float(np.average(subs.cn, weights=subs.area_m2)), 2),
            "excess_mm_aw": round(float(exc[-1]), 2), "runoff_m3": round(sum(float(q.sum() * 60) for q in H.values())), "mesh_res_m": 15.0}

    if a.state == "l1":
        N, conduits, outfalls = base_network(L, N0, with_facilities=False)
        write_inp(WORK / f"{name}_L1.inp", f"RB-01 · L1（现状：改造后模型去掉塘 / 泵 / 压力管 / 湿井 / O- 出水口）· {a.rain:g} mm / {a.dur:g} h",
                  N, conduits, outfalls, [], [], H, t, sim_h)
        f, shp = write_dem(name, D)
        write_meta(name, "L1", D, N, t, exc, {"note": "RB-01 L1（tools/rb01_build.py）"})
        info.update(state="L1", n_nodes=len(N), n_conduits=len(conduits), dem_px=list(shp))
    elif a.state == "l2p":
        N, conduits, outfalls = base_network(L, N0, with_facilities=True)
        routes = pump_routes(L, N)
        of = {sid(r.LABEL): r for r in L["of"].itertuples()}
        storage, ponds_meta, pinfo = [], [], []
        for r in L["po"].itertuples():
            k = f"PND_{sid(r.LABEL)}"; zb = pond_floor(r.geometry); A = float(r.geometry.area)
            g_in = r.geometry.intersection(D); frac = g_in.area / A
            p = r.geometry.representative_point()
            storage.append((k, zb, POND_D, A, p.x, p.y))
            from shapely.geometry import mapping
            ponds_meta.append({"node": k, "unitid": k, "vol": A * POND_D, "xy": [p.x, p.y],
                               "footprint": mapping(g_in if frac > 0.01 else r.geometry), "no_surface": frac <= 0.01})
            pinfo.append({"pond": sid(r.LABEL), "node": k, "area_ha": round(A / 1e4, 2), "floor_dtm_min": round(zb, 2), "depth_m": POND_D,
                          "vol_m3": round(A * POND_D), "model_floor": round(float(r.MAX_HGL - r.D_MAX), 2), "model_max_storage_m3": round(float(r.MAX_ST)),
                          "in_domain_frac": round(frac, 2), "no_surface": frac <= 0.01})
        pond_of = {k: f"PND_{sid(r.BNDYID)}" for k, r in of.items() if k.startswith("O-") and isinstance(r.BNDYID, str) and r.BNDYID}
        pumps = []
        for pid, rt in routes.items():
            suc = rt["suction"]; zg, zi = N[suc][2], N[suc][3]; dep = max(zg - zi, 0.5)
            on, off = 0.5 * dep, 0.1 * dep
            if suc in OUTFALL_CHAMBER:
                # 出口井（RBW3 前）的泵：井深 50 % 开 = 标高 0.57 m，低于尾水 1.3 m，会把井里水位压低、重力出流停掉
                # （24 h 一维核对：L2+planned 溢流 247k 反比 L1 226k 多）。改成水位过尾水才开、回到尾水下 0.3 m 停（Claude 自定，2026-10-16）
                on, off = TW_RBW3 + 0.2 - zi, TW_RBW3 - 0.3 - zi
            if rt["dst"] in pond_of:
                to, how = pond_of[rt["dst"]], f"{rt['dst']} 在塘 {pond_of[rt['dst']][4:]} 里 → 打进塘"
            else:
                o = of[rt["dst"]]; to = sid(rt["dst"]); how = f"{rt['dst']} 自由出流（离开系统）"
                outfalls.append((to, float(o.ELEV_INV), "FREE", "NO", o.geometry.x, o.geometry.y))
            pumps.append({"id": f"P_{pid}", "pump": pid, "from": suc, "to": to, "q_m3s": rt["q_m3s"], "q_src": rt["q_src"],
                          "on": on, "off": off, "well_depth_m": round(dep, 2), "how": how, "x": rt["x"], "y": rt["y"]})
        write_inp(WORK / f"{name}_L2P.inp", f"RB-01 · L2+planned（PO 塘方案，来源待确认：改造后模型原样）· {a.rain:g} mm / {a.dur:g} h",
                  N, conduits, outfalls, storage, pumps, H, t, sim_h)
        write_meta(name, "L2P", D, N, t, exc, {"ponds": ponds_meta, "pumps_1d": pumps, "note": "RB-01 L2+planned = PO 塘方案（来源待确认）"})
        info.update(state="L2P", ponds=pinfo, pumps=pumps, n_nodes=len(N), n_conduits=len(conduits))
        for p in pinfo:
            print(f"    塘 {p['pond']:<6} {p['area_ha']:>6} ha  底 {p['floor_dtm_min']:.2f}（模型 {p['model_floor']:.2f}）  库容 {p['vol_m3']:>7,}（模型 {p['model_max_storage_m3']:,}）  域内 {p['in_domain_frac']:.0%}")
        for p in pumps:
            print(f"    {p['id']:<9} {p['q_m3s'] * 1000:>6.1f} L/s  {p['from']}（深 {p['well_depth_m']}）开 {p['on']:.2f} 停 {p['off']:.2f} → {p['how']}")
    else:
        N, conduits, outfalls = base_network(L, N0, with_facilities=False)
        rpt = pathlib.Path(a.ref_rpt) if a.ref_rpt else WORK / "c2d" / f"{name}_L1_1d.rpt"
        fl = flooded(rpt)
        hs = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640)
        hs = hs[hs.hotspot_id.isin(HS)]
        down = {}
        for c in conduits:
            a1, b1 = (c["n1"], c["n2"]) if c["z1"] >= c["z2"] else (c["n2"], c["n1"])
            down.setdefault(a1, []).append((b1, c["len"]))
        from scipy.spatial import cKDTree
        jn = list(N); tr = cKDTree(np.array([N[k][:2] for k in jn]))
        pumps, skipped = [], []
        for h in hs.itertuples():
            units = []
            for col, (sz, q) in MOBILE.items():
                n = getattr(h, col)
                units += [(sz, q)] * (int(n) if n == n and n else 0)
            if not units:                      # 只有总数没有分型号：按 6 英寸（Claude 自定）
                units = [("6in", MOBILE["planA_pump6"][1])] * int(h.planA_pumps or 0)
            d0, i0 = tr.query((h.geometry.x, h.geometry.y)); suc = jn[i0]
            best, seen, pq = None, {suc: 0.0}, [(0.0, suc)]
            while pq:
                d, k = heapq.heappop(pq)
                if d > MAX_PATH:
                    break
                if k != suc and k not in fl:
                    best = (k, d, "下游第一口不溢流的井"); break
                for b, Ln in down.get(k, []):
                    if d + Ln < seen.get(b, 1e18):
                        seen[b] = d + Ln; heapq.heappush(pq, (d + Ln, b))
            if best is None:
                skipped.append({"hotspot": h.hotspot_id, "why": "2 km 管长内没有不溢流的下游井"}); continue
            dep = max(N[suc][2] - N[suc][3], 0.5)
            on = 1.0 if dep >= 1.2 else round(0.8 * dep, 2)
            for j, (sz, q) in enumerate(units):
                pumps.append({"id": f"L2_{h.hotspot_id}_{j + 1}", "hotspot": h.hotspot_id, "size": sz, "from": suc, "suction_dist_m": round(float(d0)),
                              "to": best[0], "path_m": round(best[1]), "how": best[2], "q_m3s": q, "on": on,
                              "off": 0.3 if on > 0.4 else round(0.3 * on, 2), "well_depth_m": round(dep, 2)})
        write_inp(WORK / f"{name}_L2.inp", f"RB-01 · L2（L1 + 热点表 planA 移动泵）· {a.rain:g} mm / {a.dur:g} h",
                  N, conduits, outfalls, [], pumps, H, t, sim_h)
        write_meta(name, "L2", D, N, t, exc, {"pumps_l2": pumps, "note": "RB-01 L2 = L1 + planA 移动泵"})
        info.update(state="L2", pumps=pumps, skipped=skipped, receiver_basis=str(rpt))
        for p in pumps:
            print(f"    {p['id']:<14} {p['size']} {p['q_m3s'] * 1000:.0f} L/s  {p['from']}（{p['suction_dist_m']} m，深 {p['well_depth_m']}）→ {p['to']} {p['path_m']} m  开 {p['on']} 停 {p['off']}")
        print(f"  未建：{skipped}")
    (ROOT / "out" / f"rb01_inputs_{a.tag}_{info['state']}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
