# -*- coding: utf-8 -*-
"""ADM 全市 250 m v2 · 从「合并结果_20261020」重建一维网络（开发机上跑一次，产物随上传包带到服务器；2026-10-20）。

数据只用 data/合并结果_20261020 的 15 个 shp（另需 DTM / 雨型 / 计算域这三个「非矢量输入」，用户已确认）。
读入 → 统一字段 → 端点坐标吸附建拓扑 → 截取触及计算域的连通块 → 泵 / 塘 / 渗井 / 雨水口 → 出一个 pickle（N，格式同 adm250_run.network()）
和一套「用了什么、没用什么、为什么」的核对表。所有写出的文本都是英文（交付要求无中文）。

用法（PowerShell，用 .venv 的 python）：
  .venv\\Scripts\\python.exe tools\\adm250v2_net.py --out ..\\upload\\adm_250m_v2\\cases\\ad250v2
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import pathlib
import pickle
import re
import sys
import time

import numpy as np
import pandas as pd
import pyogrio
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from shapely.geometry import Point, mapping, shape

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import build_city1d as B  # noqa: E402
import datapaths  # noqa: E402

MERGED = ROOT / "data" / "合并结果_20261020"
OLD_CASES = ROOT.parent / "upload" / "adm_250m" / "cases" / "ad250"

SNAP_TOL = 0.1          # m，同一来源内端点重合
JOIN_TOL = 2.0          # m，模型管端点吸附到 GDB 管端点
OUTFALL_TOL = 2.0       # m，出水口点吸附到井
NODE_TOL = 1.0          # m，井 / 集水井 / 封端等点「就是管网节点」的判据
INLET_DIST = 8.0        # m，雨水口吸附到最近井的上限（用户 10-20 定）
A_INLET = 0.10          # m²，每个雨水口的有效进水孔口面积（假设值，写进核对表）
DISCHARGE_TOL = 20.0    # m，压力管末端离下游井的上限
WET_NEAR = 30.0         # m，没有压力管的泵找吸水井的上限
SOAK_MIN_VOL = 1.0      # m³
WIRE_DIST_M = 8.0       # 塘占地外 8 m 内的井才接（同 adm250_run.auto_wire）
CJK = re.compile("[一-鿿]")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def ascii_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(s)).strip("_") or "X"


def rd(name: str, cols=None, geom=True):
    return pyogrio.read_dataframe(MERGED / f"{name}.shp", columns=cols, read_geometry=geom, use_arrow=False)


def co(df: pd.DataFrame, cols) -> pd.Series:
    """同一个含义在不同子模型里列名不同：取第一个非空。"""
    have = [c for c in cols if c in df.columns]
    return df[have].bfill(axis=1).iloc[:, 0] if have else pd.Series(np.nan, index=df.index)


def line_ends(geoms) -> tuple[np.ndarray, np.ndarray]:
    a = np.empty((len(geoms), 2)); b = np.empty((len(geoms), 2))
    for i, g in enumerate(geoms):
        if g.geom_type == "LineString":
            c0, c1 = g.coords[0], g.coords[-1]
        else:
            m = shapely.line_merge(g)
            if m.geom_type == "LineString":
                c0, c1 = m.coords[0], m.coords[-1]
            else:
                c0, c1 = m.geoms[0].coords[0], m.geoms[-1].coords[-1]
        a[i] = c0[:2]; b[i] = c1[:2]
    return a, b


def union_labels(n: int, pairs: np.ndarray) -> tuple[int, np.ndarray]:
    if len(pairs) == 0:
        return n, np.arange(n)
    g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    return connected_components(g, directed=False)


class Audit:
    def __init__(self):
        self.rows = []

    def add(self, layer, total, in_domain, used, how, unused_reason=""):
        self.rows.append([layer, int(total), int(in_domain), int(used), how, unused_reason])


# ---------------------------------------------------------------------------------------------- 管
def build_pipes(dom, au: Audit):
    cols = ["src_layer", "company", "catchment", "UNITID", "STATUS", "INVERT_LEV", "INVERT_L_1", "PIPE_DIAME",
            "D", "DIAMETER", "DIA", "DIM", "INV_STRT", "INVSTRT", "ST_IV", "SETINVSTRT", "INV_STP", "INVSTP", "SP_IV", "SETINVTSTP"]
    P = rd("Pipeline", cols)
    n_all = len(P)
    isG = (P.src_layer == "Stormwater").values
    sub = np.where(isG, "GDB", P.company.fillna("") + "/" + P.catchment.fillna(""))
    stat = P.STATUS.astype("string").fillna("")
    dead = isG & stat.isin(B.DEAD_STATUS).values
    log(f"管 {n_all:,}（GDB 部分 {isG.sum():,}、模型部分 {(~isG).sum():,}）；GDB 部分废弃 / 过期 / 拆除 {dead.sum():,} 根剔除")
    a, b = line_ends(P.geometry.values)
    length = shapely.length(P.geometry.values)
    # 管径（mm → m）。模型部分列名不一；某子模型中位数 < 20 的当成已经是 m
    dia = np.where(isG, P.PIPE_DIAME.astype(float), co(P, ["D", "DIAMETER", "DIA", "DIM"]).astype(float))
    dia = pd.Series(dia)
    for s in set(sub[~isG]):
        m = sub == s
        if np.nanmedian(dia[m]) < 20:
            dia[m] = dia[m] * 1000.0
    dia = dia.values / 1000.0
    d_bad = ~np.isfinite(dia) | (dia <= 0.05)
    dia = np.where(d_bad, 0.3, dia)
    # 管内底：GDB 部分是 DATUM 基准（+100），模型部分已是 DTM 基准
    iu = P.INVERT_LEV.astype(float).values; idn = P.INVERT_L_1.astype(float).values
    vu = np.where(isG, np.where((iu > 50) & (iu < 500), iu - B.DATUM, np.nan), co(P, ["INV_STRT", "INVSTRT", "ST_IV", "SETINVSTRT"]).astype(float).values)
    vd = np.where(isG, np.where((idn > 50) & (idn < 500), idn - B.DATUM, np.nan), co(P, ["INV_STP", "INVSTP", "SP_IV", "SETINVTSTP"]).astype(float).values)
    vu = np.where(np.abs(vu) > 500, np.nan, vu); vd = np.where(np.abs(vd) > 500, np.nan, vd)
    keep = ~dead
    n_in_dom = int(shapely.intersects(dom, P.geometry.values).sum())
    return dict(n_in_dom=n_in_dom, P=P[["src_layer", "company", "catchment", "UNITID"]].reset_index(drop=True), isG=isG, sub=sub, a=a, b=b, length=length, dia=dia, vu=vu, vd=vd,
                keep=keep, n_all=n_all, n_dead=int(dead.sum()), n_dia_fix=int(d_bad[keep].sum()))


def cluster_nodes(pts: np.ndarray, from_g: np.ndarray):
    """两段吸附：同来源 0.1 m 内合并；仅模型端点的簇再吸附到 2 m 内最近的含 GDB 端点的簇。"""
    n = len(pts)
    t = cKDTree(pts)
    nc, lab = union_labels(n, t.query_pairs(SNAP_TOL, output_type="ndarray"))
    cnt = np.bincount(lab, minlength=nc).astype(float)
    cx = np.bincount(lab, weights=pts[:, 0], minlength=nc) / cnt
    cy = np.bincount(lab, weights=pts[:, 1], minlength=nc) / cnt
    C = np.c_[cx, cy]
    has_g = np.zeros(nc, bool); has_g[lab[from_g]] = True
    mod = np.flatnonzero(~has_g); gd = np.flatnonzero(has_g)
    to = np.arange(nc)
    d, j = cKDTree(C[gd]).query(C[mod])
    sel = d <= JOIN_TOL
    to[mod[sel]] = gd[j[sel]]
    log(f"端点 {n:,} → {SNAP_TOL} m 内合并成 {nc:,} 个点；其中仅模型端点的 {len(mod):,} 个里 {int(sel.sum()):,} 个吸附到 {JOIN_TOL} m 内的 GDB 节点")
    return lab, to, C


def build_network(dom, au: Audit):
    T = build_pipes(dom, au)
    isG, keep = T["isG"], T["keep"]
    idx = np.flatnonzero(keep)
    a, b = T["a"][idx], T["b"][idx]
    pts = np.vstack([a, b]); n = len(idx)
    from_g = np.r_[isG[idx], isG[idx]]
    lab, to, C = cluster_nodes(pts, from_g)
    node = to[lab]                                  # 每个端点的最终簇号
    n1, n2 = node[:n], node[n:]
    # 连通块：触及计算域的整块取
    uniq, inv_ = np.unique(np.r_[n1, n2], return_inverse=True)
    k = len(uniq)
    nc, cl = union_labels(k, np.c_[inv_[:n], inv_[n:]])
    nodes_xy = C[uniq]
    inside = shapely.contains_xy(dom, nodes_xy[:, 0], nodes_xy[:, 1])
    size = np.bincount(cl, minlength=nc)
    touch = np.zeros(nc, bool); touch[cl[inside]] = True
    keep_c = touch & (size >= 2)
    keep_node = keep_c[cl]
    log(f"连通块 {nc:,} 个，触及计算域且 ≥2 个节点的 {int(keep_c.sum()):,} 个；节点 {k:,} → 保留 {int(keep_node.sum()):,}")
    cid = {int(u): i for i, u in enumerate(uniq)}
    name_of = {int(u): f"N{int(u)}" for u in uniq[keep_node]}
    # DTM 地面
    ids = uniq[keep_node]
    xy_arr = C[ids]
    import rasterio
    with rasterio.open(datapaths.get("dtm")) as ds:
        z = np.array([v[0] for v in ds.sample(map(tuple, xy_arr))], float)
        z[(z < -100) | ~np.isfinite(z)] = np.nan
    log(f"DTM 取地面：{int(np.isfinite(z).sum()):,} / {len(z):,}")
    ground0 = dict(zip(ids.tolist(), z.tolist()))
    # 管内底补全
    sel = np.array([(int(x) in name_of) and (int(y) in name_of) for x, y in zip(n1, n2)])
    gu = np.array([ground0.get(int(x), np.nan) for x in n1]); gd_ = np.array([ground0.get(int(x), np.nan) for x in n2])
    vu = T["vu"][idx]; vd = T["vd"][idx]
    def fix(g, v, model):
        cov = g - v
        bad = ~np.isfinite(v) | (cov > B.COVER_MAX) | (model & (cov < -2.0))
        return np.where(bad, g - B.COVER_MED, v), bad
    mod = ~isG[idx]
    vu, bu = fix(gu, vu, mod); vd, bd = fix(gd_, vd, mod)
    nfix = int(((bu | bd) & sel).sum())
    log(f"管内底按中位埋深 {B.COVER_MED} m 补了 {int((bu & sel).sum() + (bd & sel).sum()):,} 个端点（{nfix:,} 根管至少一端）")
    # 节点：井底 = 相邻管内底最小值
    inv: dict[int, float] = {}
    for nn, v in ((n1, vu), (n2, vd)):
        for u, vv in zip(nn[sel], v[sel]):
            p = inv.get(int(u))
            if p is None or vv < p:
                inv[int(u)] = float(vv)
    xy, ground, invn = {}, {}, {}
    for u in ids:
        u = int(u)
        if u not in inv:
            continue
        g = ground0.get(u)
        if g is None or not np.isfinite(g):
            g = inv[u] + B.COVER_MED
        if g - inv[u] < B.COVER_MIN:
            g = inv[u] + B.COVER_MED
        nm = name_of[u]
        xy[nm] = (float(C[u, 0]), float(C[u, 1])); ground[nm] = float(g); invn[nm] = float(inv[u])
    # 管
    dia = T["dia"][idx]; ln = T["length"][idx]
    pipes, dup, loop, seen = [], 0, 0, set()
    for i in np.flatnonzero(sel):
        u, v = int(n1[i]), int(n2[i])
        if u == v:
            loop += 1; continue
        a_, b_ = name_of[u], name_of[v]
        if a_ not in xy or b_ not in xy:
            continue
        z1, z2 = float(vu[i]), float(vd[i])
        if z2 > z1:
            a_, b_, z1, z2 = b_, a_, z2, z1
        z1 = max(z1, invn[a_]); z2 = max(z2, invn[b_])
        key = (min(a_, b_), max(a_, b_), round(float(dia[i]), 3), round(z1, 2), round(z2, 2))
        if key in seen:
            dup += 1; continue
        seen.add(key)
        pipes.append({"id": f"c_{int(idx[i])}", "n1": a_, "n2": b_, "len": float(ln[i]) if ln[i] > 0 else B.MIN_LEN, "z1": z1, "z2": z2, "d": float(dia[i]), "uid": ""})
    log(f"管 {len(pipes):,} 根；两端吸附到同一节点（自环）{loop:,} 根、完全重复 {dup:,} 根已去掉；管径缺失 / 不合理按 0.3 m 补 {T['n_dia_fix']:,} 根")
    n_g = sum(1 for c in pipes if isG[int(c["id"][2:])])
    au.add("Pipeline", T["n_all"], T["n_in_dom"], len(pipes),
           f"gravity conduits (circular, Manning 0.013, node ground from DTM, invert from the data): GDB part {n_g:,}, model part {len(pipes) - n_g:,}",
           f"dead status (GDB part) {T['n_dead']:,}; self-loops after snapping {loop:,}; exact duplicates {dup:,}; "
           f"in blocks not touching the domain or isolated {int(keep.sum()) - len(pipes) - loop - dup:,}")
    return T, dict(xy=xy, ground=ground, inv=invn, pipes=pipes, name_of=name_of, n_components=int(keep_c.sum()), nfix=nfix)


# ---------------------------------------------------------------------------------------------- 主流程
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--bbox", nargs=4, type=float, default=None, help="试跑：只取这个范围（x0 y0 x1 y1，UTM 40N）")
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    import geopandas as gpd
    dom_full = gpd.read_file(OLD_CASES / "CITY_domain.geojson").to_crs(32640).geometry.union_all()
    dom = dom_full
    if a.bbox:
        dom = dom_full.intersection(shapely.box(*a.bbox))
        log(f"试跑范围 {a.bbox}：{dom.area / 1e6:.2f} km²")
    au = Audit()
    t0 = time.time()
    T, G = build_network(dom, au)
    xy, ground, inv, pipes = G["xy"], G["ground"], G["inv"], G["pipes"]
    names = list(xy); P_xy = np.array([xy[k] for k in names]); tree = cKDTree(P_xy)
    in_dom = shapely.contains_xy(dom, P_xy[:, 0], P_xy[:, 1])
    log(f"节点 {len(xy):,}（域内 {int(in_dom.sum()):,}）；管 {len(pipes):,}")

    def near_node(pts: np.ndarray, tol: float):
        d, j = tree.query(pts)
        return d, j

    # ---- 出水口
    O = rd("Outfall", ["src_layer"])
    op = np.c_[O.geometry.x, O.geometry.y]
    o_in = shapely.contains_xy(dom, op[:, 0], op[:, 1])
    d, j = near_node(op, OUTFALL_TOL)
    outs = {names[int(jj)] for dd, jj in zip(d, j) if dd <= OUTFALL_TOL}
    au.add("Outfall", len(O), int(o_in.sum()), len(outs), f"outfall nodes (free outfall via a short pipe): points snapped to a network node within {OUTFALL_TOL:g} m",
           f"{int((o_in & (d > OUTFALL_TOL)).sum())} in-domain points have no network node within {OUTFALL_TOL:g} m (not used); several points may share one node")
    of_dia = {}
    for c in pipes:
        for nd in (c["n1"], c["n2"]):
            if nd in outs and c["d"] > of_dia.get(nd, 0.0):
                of_dia[nd] = c["d"]
    log(f"出水口点 {len(O)}（域内 {int(o_in.sum())}）→ 管网上的出水口节点 {len(outs)}")

    # ---- 节点类图层：是否就是管网节点（核对用，水力上就是节点本身）
    def node_layer(name, how, cols=None):
        L = rd(name, cols or ["src_layer"])
        p = np.c_[L.geometry.x, L.geometry.y]
        ind = shapely.contains_xy(dom, p[:, 0], p[:, 1])
        d, _ = tree.query(p)
        used = int((ind & (d <= NODE_TOL)).sum())
        au.add(name, len(L), int(ind.sum()), used, how, f"{int((ind & (d > NODE_TOL)).sum())} in-domain points are not at a pipe end (isolated, no connection in the data)")
        return L, p, ind, d

    node_layer("Manhole", "network node (manhole): same point as a pipe end")
    node_layer("SW_CAPPEDEND", "network node (capped end of a dead-end pipe): same point as a pipe end; no extra hydraulics")
    node_layer("PETROLINTERCEPTOR", "network node (in-line chamber): same point as a pipe end; no extra hydraulics")
    node_layer("PressureJunction", "node of the pressure (force-main) network used to route pumps")

    # ---- 雨水口 / 集水井：作为二维→一维的额外交换面积
    inlet_area = collections.defaultdict(float)
    n_att = 0
    for name in ("INLET", "CatchBasin"):
        L = rd(name, ["src_layer"])
        p = np.c_[L.geometry.x, L.geometry.y]
        ind = shapely.contains_xy(dom, p[:, 0], p[:, 1])
        d, j = tree.query(p)
        as_node = ind & (d <= NODE_TOL) & (name == "CatchBasin")          # 集水井本身就是管端的，已是节点
        att = ind & ~as_node & (d <= INLET_DIST)
        for jj in j[att]:
            inlet_area[names[int(jj)]] += A_INLET
        n_att += int(att.sum())
        au.add(name, len(L), int(ind.sum()), int(att.sum() + as_node.sum()),
               (f"extra 2D->1D exchange area {A_INLET:g} m2 per inlet added to the nearest network node within {INLET_DIST:g} m" +
                ("; catch basins that are a pipe end are the network node itself" if name == "CatchBasin" else "")),
               f"{int((ind & ~as_node & (d > INLET_DIST)).sum())} in-domain points have no network node within {INLET_DIST:g} m (not used)")
    log(f"雨水口 / 集水井加到节点的交换面积：{n_att:,} 个点 → {len(inlet_area):,} 个节点")

    # ---- 泵
    pumps, junc_fix, new_out, limits_rows = [], {}, [], []
    PU = rd("Pump", ["src_layer", "company", "catchment", "UNITID", "LABEL", "PUMP_FLOW", "ONELEV", "OFFELEV", "STATUS"])
    PP = rd("PressurePipe", ["company", "catchment"])
    WW = rd("WetWell", ["company", "catchment", "LABEL", "ELEV_GND", "INIT_ELEV", "BASE_ELEV", "MAX_ELEV", "NODE_H"])
    pe_a, pe_b = line_ends(PP.geometry.values)
    pe = np.vstack([pe_a, pe_b]); m = len(PP)
    if len(pe):
        nc_p, lp = union_labels(len(pe), np.vstack([cKDTree(pe).query_pairs(1.0, output_type="ndarray"), np.c_[np.arange(m), np.arange(m) + m]]))
    else:
        nc_p, lp = 0, np.array([], int)
    t_pe = cKDTree(pe) if len(pe) else None
    wp = np.c_[WW.geometry.x, WW.geometry.y]
    w_in = shapely.contains_xy(dom, wp[:, 0], wp[:, 1])
    dw, jw = tree.query(wp)
    wet_node = {}
    for i in np.flatnonzero(dw <= OUTFALL_TOL):
        wet_node.setdefault(names[int(jw[i])], i)
    au.add("WetWell", len(WW), int(w_in.sum()), len(wet_node), "pump suction wells (node deepened to the well bottom when a pump is attached)",
           f"{int((w_in & (dw > OUTFALL_TOL)).sum())} in-domain wells are not at a pipe end; wells without a pump with flow are ordinary nodes")
    pp_ok = []
    ppt = np.c_[PU.geometry.x, PU.geometry.y]
    flow = PU.PUMP_FLOW.astype(float).fillna(0.0).values
    isGp = (PU.src_layer == "Stormwater").values
    p_in = shapely.contains_xy(dom, ppt[:, 0], ppt[:, 1])
    comp_of = np.full(len(PU), -1)
    if t_pe is not None:
        dd, jj = t_pe.query(ppt)
        comp_of = np.where(dd <= 1.0, lp[jj], -1)
    groups = collections.defaultdict(list)                       # 分组键 → 泵行号
    why_not = {}
    for i in np.flatnonzero(p_in):
        lab = str(PU.LABEL.iloc[i]) if PU.LABEL.iloc[i] is not None else ""
        if isGp[i]:
            why_not[i] = "GDB pump station record has no flow rate (FLOW_RATE is 0 / empty in the merged data)"; continue
        if not flow[i] > 0:
            why_not[i] = "no pump flow in the merged data (PUMP_FLOW empty or 0; the pump curve is not in the shapefile)"; continue
        if comp_of[i] >= 0:
            groups[("c", int(comp_of[i]))].append(i)
        else:
            groups[("p", i)].append(i)
    n_built = 0
    for key, rows in groups.items():
        Q = float(sum(flow[r] for r in rows)) / 1000.0          # L/s → m³/s（Parsons 客户模型 FLOW_UNITS = LPS）
        touched = set(); far = []
        if key[0] == "c":
            ends_i = np.flatnonzero(lp == key[1])
            d, j = tree.query(pe[ends_i])
            touched = {names[int(jj)] for dd, jj in zip(d, j) if dd <= OUTFALL_TOL}
            far = [(names[int(jj)], pe[e]) for e, dd, jj in zip(ends_i, d, j) if dd <= DISCHARGE_TOL]
        suction = None
        cand = [t for t in touched if t in wet_node]
        if cand:
            suction = min(cand, key=lambda t: inv[t])
        else:
            q0 = ppt[rows[0]]
            d, j = tree.query(q0, k=8)
            for dd, jj in zip(np.atleast_1d(d), np.atleast_1d(j)):
                if dd <= WET_NEAR and names[int(jj)] in wet_node:
                    suction = names[int(jj)]; break
            if suction is None and touched:
                suction = min(touched, key=lambda t: inv[t])
        if suction is None:
            for r in rows:
                why_not[r] = "no suction well found (no wet well / network node within reach of the pump and its pressure line)"
            continue
        sx, sy = xy[suction]
        others = [(t, q) for t, q in far if t != suction]
        if others:
            to = max(others, key=lambda tq: (tq[1][0] - sx) ** 2 + (tq[1][1] - sy) ** 2)[0]
            how = f"discharge to the far end of the pressure line (nearest network node within {DISCHARGE_TOL:g} m of the line end)"
        else:
            to = None; how = "discharge point not found in the data: water leaves the domain through a new free outfall"
        wrow = wet_node.get(suction)
        zb = np.nan
        if wrow is not None:
            for c in ("BASE_ELEV", "INIT_ELEV"):
                v = WW[c].iloc[wrow]
                if v is not None and np.isfinite(v):
                    zb = float(v); break
        if not np.isfinite(zb) or zb > inv[suction] or ground[suction] - zb > 25 or ground[suction] - zb < 0.5:
            zb = inv[suction]
        depth = max(ground[suction] - zb, 1.0)
        on_a = [PU.ONELEV.iloc[r] for r in rows if PU.ONELEV.iloc[r] is not None and np.isfinite(PU.ONELEV.iloc[r])]
        off_a = [PU.OFFELEV.iloc[r] for r in rows if PU.OFFELEV.iloc[r] is not None and np.isfinite(PU.OFFELEV.iloc[r])]
        on, off = 0.5 * depth, 0.1 * depth
        if on_a and off_a:
            o1, o2 = max(float(np.max(on_a)) - zb, 0.0), max(float(np.min(off_a)) - zb, 0.0)
            if o1 > o2 and o1 < depth:
                on, off = o1, o2
        pid = f"PS_{len(pumps) + 1}"
        if to is None:
            to = f"X_{pid}"; new_out.append((to, xy[suction][0] + 5.0, xy[suction][1] + 5.0, zb - 1.0))
        if zb < inv[suction]:
            junc_fix[suction] = zb
        labels = "+".join(ascii_id(PU.LABEL.iloc[r]) for r in rows)[:60]
        pumps.append({"id": pid, "from": suction, "to": to, "q_m3s": Q, "on": on, "off": off, "z_bot": min(zb, inv[suction]), "inv_orig": inv[suction], "ground": ground[suction],
                      "how": how, "ps": pid, "unitid": labels, "n": len(rows), "q_each": Q * 1000.0 / len(rows), "q_src": "merged Pump.PUMP_FLOW (L/s)"})
        n_built += len(rows)
    nb = collections.Counter(why_not.values())
    for r, w in why_not.items():
        limits_rows.append([PU.src_layer.iloc[r], PU.company.iloc[r] if not isGp[r] else "", PU.catchment.iloc[r] if not isGp[r] else "",
                            PU.UNITID.iloc[r] if isGp[r] else "", PU.LABEL.iloc[r], float(flow[r]), w])
    limits = [f"{c} pump record(s) not built: {w}" for w, c in nb.items()]
    au.add("Pump", len(PU), int(p_in.sum()), n_built,
           f"pump stations: {len(pumps)} fixed-flow SWMM pumps (sum of PUMP_FLOW of the pumps sharing one suction well; on/off = pump on/off levels when given, else 50% / 10% of well depth)",
           "; ".join(f"{c} not built: {w}" for w, c in nb.items()))
    au.add("PressurePipe", len(PP), int(shapely.intersects(dom, PP.geometry.values).sum()), len(PP),
           "used only to find each pump's suction well and discharge node (pressure line geometry); not a hydraulic link", "")
    # 可移动泵：不参与（用户 10-20 定）
    MP = rd("MOVABLE_PUMP", ["src_layer"])
    mp = np.c_[MP.geometry.x, MP.geometry.y]
    au.add("MOVABLE_PUMP", len(MP), int(shapely.contains_xy(dom, mp[:, 0], mp[:, 1]).sum()), 0, "not used", "emergency (temporary) pumps: excluded by decision, no fixed position or outlet")
    log(f"泵：建 {len(pumps)} 台组（{n_built} 条泵记录），没建 {len(limits_rows)} 条：{dict(nb)}")

    # ---- 塘
    PD = rd("Pond", ["src_layer", "company", "catchment", "UNITID", "STATUS", "LABEL", "POND_NAME", "DEPTH", "VOLUME", "DEPTH_MAX", "D-MAX", "MAX_DPTH"])
    isGd = (PD.src_layer == "Stormwater").values
    deadp = isGd & PD.STATUS.astype("string").fillna("").isin(B.DEAD_STATUS).values
    cen = shapely.centroid(PD.geometry.values)
    pin = np.array([dom.contains(c) for c in cen]) & ~deadp
    g_depth = PD.DEPTH.astype(float)[isGd & (PD.DEPTH.astype(float) > 0).values]
    med_depth = float(g_depth.median()) if len(g_depth) else 2.0
    log(f"塘 {len(PD)}（GDB {int(isGd.sum())}），域内且未废弃 {int(pin.sum())}；GDB 塘有深度的 {len(g_depth)} 个，中位深度 {med_depth:.2f} m")
    geoms = shapely.force_2d(PD.geometry.values)
    # 去重：模型塘与 GDB 塘重叠 > 50% 的丢掉模型塘（GDB 带容积）
    drop_dup = np.zeros(len(PD), bool)
    gi = np.flatnonzero(isGd & pin)
    gtree = shapely.STRtree(geoms[gi]) if len(gi) else None
    for i in np.flatnonzero(~isGd & pin):
        if gtree is None:
            break
        for k in gtree.query(geoms[i]):
            inter = geoms[i].intersection(geoms[gi[k]]).area
            if inter > 0.5 * min(geoms[i].area, geoms[gi[k]].area):
                drop_dup[i] = True; break
    ponds, links, pond_rows, used_ids = [], [], [], set()
    keys = np.array(names); P_arr = P_xy
    for i in np.flatnonzero(pin & ~drop_dup):
        g = geoms[i]
        area = float(g.area)
        if area < 50:
            continue
        if isGd[i]:
            uid = ascii_id(PD.UNITID.iloc[i])
            dep = float(PD.DEPTH.iloc[i]) if PD.DEPTH.iloc[i] and PD.DEPTH.iloc[i] > 0 else 0.0
            vol_g = float(PD.VOLUME.iloc[i]) if PD.VOLUME.iloc[i] and PD.VOLUME.iloc[i] > 0 else 0.0
            est = dep <= 0
            dep = dep if dep > 0 else med_depth
            if vol_g > 0:
                a_flat = vol_g / dep; vol = vol_g; basis = "GDB VOLUME" + (" with estimated depth (median of GDB ponds)" if est else " and DEPTH")
            else:
                a_flat = area; vol = area * dep; basis = "polygon area x " + ("estimated depth (median of GDB ponds)" if est else "DEPTH"); est = True
            src = "GDB"
        else:
            lab = PD.LABEL.iloc[i] or PD.POND_NAME.iloc[i] or f"{i}"
            uid = ascii_id(f"{PD.company.iloc[i]}_{PD.catchment.iloc[i]}_{lab}")
            dm = [PD[c].iloc[i] for c in ("DEPTH_MAX", "D-MAX", "MAX_DPTH")]
            dm = [float(v) for v in dm if v is not None and np.isfinite(v) and v > 0]
            est = not dm
            dep = dm[0] if dm else med_depth
            a_flat = area; vol = area * dep
            basis = "polygon area x " + ("DEPTH_MAX" if dm else "estimated depth (median of GDB ponds)")
            src = "model (" + str(PD.company.iloc[i]) + ")"
        u0, k = uid, 2
        while uid in used_ids:
            uid = f"{u0}_{k}"; k += 1
        used_ids.add(uid)
        # 接法：占地外 8 m 内的井（不含出水口）最多 4 个，同 adm250_run.auto_wire
        x0, y0, x1, y1 = g.bounds
        m_ = np.flatnonzero((P_arr[:, 0] >= x0 - WIRE_DIST_M) & (P_arr[:, 0] <= x1 + WIRE_DIST_M) & (P_arr[:, 1] >= y0 - WIRE_DIST_M) & (P_arr[:, 1] <= y1 + WIRE_DIST_M))
        near = sorted(((g.distance(Point(P_arr[q])), names[int(q)]) for q in m_ if names[int(q)] not in outs), key=lambda t: t[0])
        near = [kk for dd, kk in near if dd <= WIRE_DIST_M][:4]
        c0 = g.representative_point()
        if near:
            z_bot = float(min(inv[kk] for kk in near)); how_w = f"wired to {len(near)} manhole(s)"
        else:
            dd2 = (P_arr[:, 0] - c0.x) ** 2 + (P_arr[:, 1] - c0.y) ** 2
            z_bot = float(inv[names[int(dd2.argmin())]]); how_w = f"isolated storage (no manhole within {WIRE_DIST_M:g} m of the pond footprint)"
        node = "PND_" + uid
        for kk in near:
            links.append({"id": f"PI_{uid}_{kk}", "n1": kk, "n2": node, "len": 10.0, "z1": inv[kk], "z2": max(z_bot, min(inv[kk], z_bot + dep)), "d": 0.6, "uid": ""})
        ponds.append({"node": node, "unitid": uid, "z_floor": z_bot, "depth": float(dep), "curve": [(0.0, float(a_flat)), (float(dep), float(a_flat))], "vol": float(vol),
                      "xy": (float(c0.x), float(c0.y)), "footprint": mapping(g), "inflows": near, "kind": "pond"})
        pond_rows.append({"unitid": uid, "node": node, "source": src, "volume_m3": round(vol), "depth_m": round(dep, 2), "area_m2": round(a_flat), "estimated": bool(est),
                          "basis": basis, "wiring": how_w, "x": round(c0.x, 1), "y": round(c0.y, 1)})
    n_est = sum(1 for r in pond_rows if r["estimated"])
    au.add("Pond", len(PD), int(pin.sum()), len(ponds), "storage nodes with surface capture on the pond footprint (flat curve, check-valve pipes to up to 4 manholes within 8 m)",
           f"outside domain or dead status {int((~pin).sum())}; overlapping a GDB pond (duplicate) {int(drop_dup.sum())}; footprint < 50 m2 {int((pin & ~drop_dup).sum()) - len(ponds)}; "
           f"{n_est} of the used ponds have estimated volume/depth (see ponds.json)")
    log(f"塘：用 {len(ponds)}（其中 volume/depth 为估算的 {n_est}），接塘管 {len(links)}")

    # ---- 渗井：孤立蓄水节点（只收所在单元的地表水，无下渗）
    SK = rd("SOAKAWAY", ["src_layer", "UNITID", "STATUS", "SOAKAWAY_L", "SOAKAWAY_W", "SOAKAWAY_D"])
    sp = np.c_[SK.geometry.x, SK.geometry.y]
    s_in = shapely.contains_xy(dom, sp[:, 0], sp[:, 1])
    sdead = SK.STATUS.astype("string").fillna("").isin(B.DEAD_STATUS).values
    import rasterio
    with rasterio.open(datapaths.get("dtm")) as ds:
        sz = np.array([v[0] for v in ds.sample(map(tuple, sp))], float)
    n_soak, n_bad = 0, 0
    for i in np.flatnonzero(s_in & ~sdead):
        L_, W_, D_ = (float(SK[c].iloc[i] or 0.0) for c in ("SOAKAWAY_L", "SOAKAWAY_W", "SOAKAWAY_D"))
        if L_ > 100 or W_ > 100:
            L_, W_ = L_ / 1000.0, W_ / 1000.0
        if D_ > 40:
            D_ = D_ / 1000.0
        if not (0.3 <= L_ <= 60 and 0.3 <= W_ <= 60 and 0.5 <= D_ <= 40) or L_ * W_ * D_ < SOAK_MIN_VOL or not np.isfinite(sz[i]):
            n_bad += 1; continue
        uid = "SOAK_" + ascii_id(SK.UNITID.iloc[i] or i)
        if uid in used_ids:
            uid += f"_{i}"
        used_ids.add(uid)
        half = math.sqrt(L_ * W_) / 2.0
        fp = shapely.geometry.box(sp[i, 0] - half, sp[i, 1] - half, sp[i, 0] + half, sp[i, 1] + half)
        ponds.append({"node": "PND_" + uid, "unitid": uid, "z_floor": float(sz[i] - D_), "depth": float(D_), "curve": [(0.0, float(L_ * W_)), (float(D_), float(L_ * W_))], "vol": float(L_ * W_ * D_),
                      "xy": (float(sp[i, 0]), float(sp[i, 1])), "footprint": mapping(fp), "inflows": [], "kind": "soakaway"})
        pond_rows.append({"unitid": uid, "node": "PND_" + uid, "source": "SOAKAWAY", "volume_m3": round(L_ * W_ * D_, 1), "depth_m": round(D_, 2), "area_m2": round(L_ * W_, 1), "estimated": False,
                          "basis": "SOAKAWAY_L x W x D", "wiring": "isolated storage; takes surface water of its cell until full, no infiltration", "x": round(sp[i, 0], 1), "y": round(sp[i, 1], 1)})
        n_soak += 1
    n_dead_in = int((s_in & sdead).sum())
    au.add("SOAKAWAY", len(SK), int(s_in.sum()), n_soak, "isolated storage node L x W x D, takes surface water of its 2D cell until full (no infiltration, decision 10-20)",
           f"{n_bad} in-domain records have no usable L/W/D (0 or implausible); dead status {n_dead_in}")

    # ---- 汇水区：按用户决定不参与
    CT = rd("Catchment", ["src_layer"], geom=False)
    au.add("Catchment", len(CT), 0, 0, "not used", "domain is the whole city (1,233.6 km2); runoff uses the whole-city SCS CN 82 / Tc 30 min (decision 10-20): the catchments cover only 36% of the domain, overlap 1.6x and carry CN = 0 / Tc up to 600")

    # ---- 服务面积
    log("服务面积（最近节点法）…")
    area = B.served_areas(xy, log=lambda *x: None)
    A = sum(area.values())
    R_served = B.K_RADIUS * math.sqrt(A / max(len(xy), 1))
    n_in = int(in_dom.sum())
    log(f"服务面积 {A / 1e6:,.1f} km²，服务半径 {R_served:.1f} m")
    dummy_out = None
    if not outs:
        low = min(xy, key=lambda k: inv[k])
        dummy_out = ("X_NOOUTLET", xy[low][0] + 5.0, xy[low][1] + 5.0, inv[low] - 1.0)
    N = {"dummy_out": dummy_out, "xy": xy, "ground": ground, "inv": inv, "pipes": pipes, "outs": outs, "area": area, "of_dia": of_dia, "n_components": G["n_components"], "n_in": n_in,
         "dropped": [], "A": A, "R_served": R_served, "pumps": pumps, "junc_fix": junc_fix, "new_out": new_out, "limits": limits, "links": links, "ponds": ponds, "inlet_area": dict(inlet_area)}
    # 断言：写出的文本里没有中文
    for s in limits + [p["how"] for p in pumps] + [p["unitid"] for p in pumps] + [p["unitid"] for p in ponds]:
        assert not CJK.search(str(s)), s
    tag = "_trial" if a.bbox else ""
    (out / f"av_net_CITY{tag}.pkl").write_bytes(pickle.dumps(N))
    (out / f"CITY_ponds{tag}.json").write_text(json.dumps({"region": "CITY", "note": "Storage objects used by the model: ponds (merged Pond layer: GDB part with VOLUME / DEPTH, model part with polygon area x DEPTH_MAX) and soakaways. 'estimated' = volume or depth is not given by the data (decision 10-20: polygon area x median depth of GDB ponds).", "ponds": pond_rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out / f"pumps_not_built{tag}.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); w.writerow(["source", "company", "catchment", "unitid", "label", "pump_flow_Lps", "reason"]); w.writerows(limits_rows)
    (out / f"CITY_pumps{tag}.json").write_text(json.dumps({"region": "CITY", "pumps_built": [{k: v for k, v in p.items()} for p in pumps], "not_built_summary": limits}, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out / f"data_usage_audit{tag}.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); w.writerow(["layer", "features", "in_domain", "used_in_calculation", "how_used", "not_used_or_notes"]); w.writerows(au.rows)
    log(f"完成：{out}（{time.time() - t0:.0f} s）：节点 {len(xy):,} 管 {len(pipes):,} 出水口 {len(outs)} 泵 {len(pumps)} 塘 / 渗井 {len(ponds)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
