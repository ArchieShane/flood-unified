# -*- coding: utf-8 -*-
"""ADM 全市 100 m（治理后数据）· 从「全市治理后数据_20261025」重建一维网络（开发机上跑一次，产物随上传包带到服务器；2026-10-26）。

数据只用 Z:\\flood\\全市治理后数据_20261025（GDB：管网 / 井 / 雨水口 / 出水口 / 泵 + 泵机组表 + 泵曲线表 / 塘 / 渗井 / 湿井）。
另需 DTM（二维地形与节点地面，服务器上的）、客户 Mussafah 模型里的设计雨型、全市计算域 1,233.6 km²（用户沿用），都不是矢量管网数据。
口径沿用 adm250v2_net.py（上一版全市 v2）：管网类只剔除 Abandoned / Expired / Removed（不看 LEVEL，Proposed 照用），泵要有流量才建，
塘 = 面积 × 最大深度（缺深度取有深度塘的中位深度并标「估算」），渗井 = 孤立蓄水，INLET / CATCHBASIN 给最近井加 2D→1D 交换面积，汇水区不参与，可移动泵不参与。
和上一版的不同：数据已统一成一套字段、管内底已是 DTM 基准（NADD，原值 = NADD + 100 另存 *_RAW）、泵流量来自泵机组表 + 泵曲线表（不再是 PUMP_FLOW）、
压力管在 PIPELINE 里（PIPE_SUBTY = PressrLine）。L1 / L2 两态的管网、泵相同；L1 不含塘 / 渗井，L2 加塘 / 渗井（态的拆分在 runner 里做，本脚本给每个蓄水对象标 kind）。

用法（PowerShell，.venv 的 python）：
  .venv\\Scripts\\python.exe -X utf8 tools\\adm100a_net.py --out ..\\upload\\adm_100m_after\\cases\\ad100a
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
from shapely.geometry import Point, mapping

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import build_city1d as B  # noqa: E402
import datapaths  # noqa: E402

GDB = pathlib.Path(r"Z:\flood\全市治理后数据_20261025\全市治理后数据.gdb")
OLD_CASES = ROOT.parent / "upload" / "adm_100m_v2" / "cases" / "ad100v2"
REG = "CITYAFTER"

SNAP_TOL = 0.5          # m，管端点合并成同一个井（旧 GDB 部分用 0.5 m）
OUTFALL_TOL = 2.0       # m，出水口点吸附到井
NODE_TOL = 1.0          # m，井 / 封端等点「就是管网节点」的判据
INLET_DIST = 8.0        # m，雨水口吸附到最近井的上限
A_INLET = 0.10          # m²，每个雨水口的有效进水孔口面积（假设值，写进核对表）
DISCHARGE_TOL = 20.0    # m，压力管末端离下游井的上限
WET_NEAR = 30.0         # m，没有压力管的泵找吸水井的上限
FLOW_MIN_LPS = 10.0    # 台账 FLOW_RATE 小于它的当成泵台数
SOAK_MIN_VOL = 1.0      # m³
WIRE_DIST_M = 8.0       # 塘占地外 8 m 内的井才接
DEPTH_OK = 30.0         # m，塘深度超过它的当数据错误（有 101 m 的）
CJK = re.compile("[一-鿿]")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def ascii_id(s) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(s)).strip("_") or "X"


def ascii_txt(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\x20-\x7e]+", " ", str(s or ""))).strip()


def rd(name: str, geom=True):
    return pyogrio.read_dataframe(GDB, layer=name, read_geometry=geom, use_arrow=False)


def line_ends(geoms) -> tuple[np.ndarray, np.ndarray]:
    a = np.empty((len(geoms), 2)); b = np.empty((len(geoms), 2))
    for i, g in enumerate(geoms):
        if g is None or g.is_empty:
            a[i] = b[i] = (np.nan, np.nan); continue
        if g.geom_type == "LineString":
            c0, c1 = g.coords[0], g.coords[-1]
        else:
            m = shapely.line_merge(g)
            if m.is_empty:
                a[i] = b[i] = (np.nan, np.nan); continue
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


def num(s) -> np.ndarray:
    return pd.to_numeric(s, errors="coerce").astype(float).values


# ---------------------------------------------------------------------------------------------- 管
def build_network(dom, au: Audit):
    P = rd("PIPELINE")
    n_all = len(P)
    sub = np.asarray(P.PIPE_SUBTY.astype("string").fillna("").astype(object))
    grav = sub == "GravitLine"
    press = sub == "PressrLine"
    stat = P.STATUS.astype("string").fillna("").str.strip()
    dead = np.asarray(stat.isin(B.DEAD_STATUS).astype(bool))
    log(f"管 {n_all:,}：重力管 {int(grav.sum()):,}、压力管 {int(press.sum()):,}、LocSupLine {int((sub == 'LocSupLine').sum())}；废弃 / 过期 / 拆除 {int(dead.sum()):,}")
    a, b = line_ends(P.geometry.values)
    length = shapely.length(P.geometry.values)
    dia = num(P.PIPE_DIAME)
    d_bad = ~np.isfinite(dia) | (dia <= 50)
    dia = np.where(d_bad, 300.0, dia) / 1000.0
    vu = num(P.INVERT_LEV); vd = num(P.INVERT_L_1)          # NADD = DTM 基准
    vu = np.where((vu > -60) & (vu < 200), vu, np.nan); vd = np.where((vd > -60) & (vd < 200), vd, np.nan)
    keep = grav & ~dead & np.isfinite(a[:, 0]) & np.isfinite(b[:, 0])
    n_in_dom = int(shapely.intersects(dom, P.geometry.values[keep]).sum())
    idx = np.flatnonzero(keep)
    pa, pb = a[idx], b[idx]
    pts = np.vstack([pa, pb]); n = len(idx)
    t = cKDTree(pts)
    nc0, lab = union_labels(len(pts), t.query_pairs(SNAP_TOL, output_type="ndarray"))
    cnt = np.bincount(lab, minlength=nc0).astype(float)
    C = np.c_[np.bincount(lab, weights=pts[:, 0], minlength=nc0) / cnt, np.bincount(lab, weights=pts[:, 1], minlength=nc0) / cnt]
    log(f"端点 {len(pts):,} → {SNAP_TOL} m 内合并成 {nc0:,} 个井")
    n1, n2 = lab[:n], lab[n:]
    nc, cl = union_labels(nc0, np.c_[n1, n2])
    inside = shapely.contains_xy(dom, C[:, 0], C[:, 1])
    size = np.bincount(cl, minlength=nc)
    touch = np.zeros(nc, bool); touch[cl[inside]] = True
    keep_c = touch & (size >= 2)
    keep_node = keep_c[cl]
    log(f"连通块 {nc:,} 个，触及计算域且 ≥2 个节点的 {int(keep_c.sum()):,} 个；节点保留 {int(keep_node.sum()):,}")
    ids = np.flatnonzero(keep_node)
    name_of = {int(u): f"N{int(u)}" for u in ids}
    import rasterio
    with rasterio.open(datapaths.get("dtm")) as ds:
        z = np.array([v[0] for v in ds.sample(map(tuple, C[ids]))], float)
        z[(z < -100) | ~np.isfinite(z)] = np.nan
    log(f"DTM 取地面：{int(np.isfinite(z).sum()):,} / {len(z):,}（建模时服务器上会用服务器的 DTM 重取一遍）")
    ground0 = dict(zip(ids.tolist(), z.tolist()))
    sel = keep_node[n1] & keep_node[n2]
    gu = np.array([ground0.get(int(x), np.nan) for x in n1]); gd_ = np.array([ground0.get(int(x), np.nan) for x in n2])
    vu = vu[idx]; vd = vd[idx]

    def fix(g, v):
        cov = g - v
        bad = ~np.isfinite(v) | (cov > B.COVER_MAX) | (cov < -2.0)
        return np.where(bad, g - B.COVER_MED, v), bad
    vu, bu = fix(gu, vu); vd, bd = fix(gd_, vd)
    nfix = int(((bu | bd) & sel).sum())
    log(f"管内底按中位埋深 {B.COVER_MED} m 补了 {int((bu & sel).sum() + (bd & sel).sum()):,} 个端点（{nfix:,} 根管至少一端）")
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
        if g is None or not np.isfinite(g) or g - inv[u] < B.COVER_MIN:
            g = inv[u] + B.COVER_MED
        nm = name_of[u]
        xy[nm] = (float(C[u, 0]), float(C[u, 1])); ground[nm] = float(g); invn[nm] = float(inv[u])
    dd_ = dia[idx]; ln = length[idx]
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
        key = (min(a_, b_), max(a_, b_), round(float(dd_[i]), 3), round(z1, 2), round(z2, 2))
        if key in seen:
            dup += 1; continue
        seen.add(key)
        pipes.append({"id": f"c_{int(idx[i])}", "n1": a_, "n2": b_, "len": float(ln[i]) if ln[i] > 0 else B.MIN_LEN, "z1": z1, "z2": z2, "d": float(dd_[i]), "uid": ""})
    log(f"重力管 {len(pipes):,} 根；自环 {loop:,}、完全重复 {dup:,} 已去掉；管径缺失 / 不合理按 0.3 m 补 {int(d_bad[idx].sum()):,} 根")
    au.add("PIPELINE (gravity, GravitLine)", int(grav.sum()), n_in_dom, len(pipes),
           "gravity conduits (circular, Manning 0.013, node ground from DTM, invert from the data in the DTM datum)",
           f"dead status {int((grav & dead).sum()):,}; self-loops after snapping {loop:,}; exact duplicates {dup:,}; in blocks not touching the domain or isolated {int(keep.sum()) - len(pipes) - loop - dup:,}")
    au.add("PIPELINE (pressure, PressrLine)", int(press.sum()), int(shapely.intersects(dom, P.geometry.values[press]).sum()), int(press.sum()),
           "used only to find each pump's suction well and discharge node (pressure line geometry); not a hydraulic link", "")
    au.add("PIPELINE (LocSupLine)", int((sub == "LocSupLine").sum()), 0, 0, "not used", "unknown line type (30 records), not treated as a gravity conduit")
    G = dict(xy=xy, ground=ground, inv=invn, pipes=pipes, n_components=int(keep_c.sum()), nfix=nfix)
    press_geo = P.geometry.values[press & ~dead]
    return G, press_geo


# ---------------------------------------------------------------------------------------------- 主流程
def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--bbox", nargs=4, type=float, default=None, help="试跑：只取这个范围（x0 y0 x1 y1，UTM 40N）")
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    import geopandas as gpd
    dom = gpd.read_file(OLD_CASES / "CITY_domain.geojson").to_crs(32640).geometry.union_all()
    if a.bbox:
        dom = dom.intersection(shapely.box(*a.bbox))
        log(f"试跑范围 {a.bbox}：{dom.area / 1e6:.2f} km²")
    au = Audit()
    t0 = time.time()
    G, press_geo = build_network(dom, au)
    xy, ground, inv, pipes = G["xy"], G["ground"], G["inv"], G["pipes"]
    names = list(xy); P_xy = np.array([xy[k] for k in names]); tree = cKDTree(P_xy)
    in_dom = shapely.contains_xy(dom, P_xy[:, 0], P_xy[:, 1])
    log(f"节点 {len(xy):,}（域内 {int(in_dom.sum()):,}）；管 {len(pipes):,}")

    def pts_of(df):
        return np.c_[df.geometry.x, df.geometry.y]

    # ---- 出水口
    O = rd("OUTFALL")
    op = pts_of(O)
    o_in = shapely.contains_xy(dom, op[:, 0], op[:, 1])
    od = O.STATUS.astype("string").fillna("").str.strip().isin(B.DEAD_STATUS).astype(bool).to_numpy()
    d, j = tree.query(op)
    outs = {names[int(jj)] for dd, jj, dead in zip(d, j, od) if dd <= OUTFALL_TOL and not dead}
    bt = O.BND_TYPE.astype("string").fillna("").value_counts().to_dict()
    au.add("OUTFALL", len(O), int(o_in.sum()), len(outs), f"outfall nodes (all treated as free outfalls via a short pipe; BND_TYPE in the data is {bt} but boundary stages are not applied): points snapped to a network node within {OUTFALL_TOL:g} m",
           f"{int((o_in & (d > OUTFALL_TOL)).sum())} in-domain points have no network node within {OUTFALL_TOL:g} m (not used); several points may share one node; dead status {int(od.sum())}")
    of_dia = {}
    for c in pipes:
        for nd in (c["n1"], c["n2"]):
            if nd in outs and c["d"] > of_dia.get(nd, 0.0):
                of_dia[nd] = c["d"]
    log(f"出水口点 {len(O)}（域内 {int(o_in.sum())}）→ 管网上的出水口节点 {len(outs)}")

    def node_layer(name, how):
        L = rd(name)
        p = pts_of(L)
        ind = shapely.contains_xy(dom, p[:, 0], p[:, 1])
        d, _ = tree.query(p)
        used = int((ind & (d <= NODE_TOL)).sum())
        au.add(name, len(L), int(ind.sum()), used, how, f"{int((ind & (d > NODE_TOL)).sum())} in-domain points are not at a pipe end (isolated, no connection in the data)")
        return L, p, ind, d

    node_layer("MANHOLECHAMBER", "network node (manhole): same point as a pipe end")
    node_layer("SW_CAPPEDEND", "network node (capped end of a dead-end pipe): same point as a pipe end; no extra hydraulics")
    node_layer("PETROLINTERCEPTOR", "network node (in-line chamber): same point as a pipe end; no extra hydraulics")

    # ---- 雨水口 / 集水井
    inlet_area = collections.defaultdict(float)
    n_att = 0
    for name in ("INLET", "CATCHBASIN"):
        L = rd(name)
        p = pts_of(L)
        ind = shapely.contains_xy(dom, p[:, 0], p[:, 1])
        d, j = tree.query(p)
        as_node = ind & (d <= NODE_TOL) & (name == "CATCHBASIN")
        att = ind & ~as_node & (d <= INLET_DIST)
        for jj in j[att]:
            inlet_area[names[int(jj)]] += A_INLET
        n_att += int(att.sum())
        au.add(name, len(L), int(ind.sum()), int(att.sum() + as_node.sum()),
               (f"extra 2D->1D exchange area {A_INLET:g} m2 per inlet added to the nearest network node within {INLET_DIST:g} m" +
                ("; catch basins that are a pipe end are the network node itself" if name == "CATCHBASIN" else "")),
               f"{int((ind & ~as_node & (d > INLET_DIST)).sum())} in-domain points have no network node within {INLET_DIST:g} m (not used)")
    log(f"雨水口 / 集水井加到节点的交换面积：{n_att:,} 个点 → {len(inlet_area):,} 个节点")

    # ---- 泵：流量 = 泵机组表各台的泵曲线（取曲线中间扬程点的流量）之和
    pumps, junc_fix, new_out, limits_rows = [], {}, [], []
    PU = rd("PUMP"); UN = rd("PUMP_UNIT", geom=False); CV = rd("PUMP_CURVE", geom=False)
    WW = rd("WETWELL")
    CV = CV.sort_values(["CURVE", "SEQ"])
    curve_q = {}
    for cname, g in CV.groupby("CURVE"):
        h = g.HEAD_M.astype(float).values; q = g.FLOW_LPS.astype(float).values
        if len(h) == 0 or not np.isfinite(q).any():
            continue
        hm = 0.5 * np.nanmax(h)
        curve_q[cname] = float(np.interp(hm, h, q)) if np.nanmax(h) > np.nanmin(h) else float(q[0])
    UN["q_lps"] = UN.CURVE.map(curve_q)
    un_by = {k: g for k, g in UN.groupby("STATION")}
    pe_a, pe_b = line_ends(press_geo)
    pe = np.vstack([pe_a, pe_b]); m = len(press_geo)
    nc_p, lp = union_labels(len(pe), np.vstack([cKDTree(pe).query_pairs(1.0, output_type="ndarray"), np.c_[np.arange(m), np.arange(m) + m]]))
    t_pe = cKDTree(pe)
    wp = pts_of(WW)
    w_in = shapely.contains_xy(dom, wp[:, 0], wp[:, 1])
    dw, jw = tree.query(wp)
    wet_node = {}
    for i in np.flatnonzero(dw <= OUTFALL_TOL):
        wet_node.setdefault(names[int(jw[i])], i)
    au.add("WETWELL", len(WW), int(w_in.sum()), len(wet_node), "pump suction wells (node deepened to the well bottom INVERT_LEV when a pump is attached)",
           f"{int((w_in & (dw > OUTFALL_TOL)).sum())} in-domain wells are not at a pipe end; wells without a pump with flow are ordinary nodes")
    ppt = pts_of(PU)
    p_in = shapely.contains_xy(dom, ppt[:, 0], ppt[:, 1])
    pdead = PU.STATUS.astype("string").fillna("").str.strip().isin(B.DEAD_STATUS).astype(bool).to_numpy()
    dd, jj = t_pe.query(ppt)
    comp_of = np.where(dd <= 1.0, lp[jj], -1)
    flow_st = []; q_from = {}; dup_of = {}; seen_units = {}
    for i in range(len(PU)):
        g = un_by.get(PU.UNITID.iloc[i])
        q = float(np.nansum(g.q_lps.values)) if g is not None else 0.0
        if g is not None and q > 0:                              # 同一批泵挂在同一模型的几个站点记录下（如 RD-DY 的 TP-1 / TP-2 站各列一遍 TP-1..3）：只算第一个
            key = (str(PU.UNITID.iloc[i]).split("_PS_")[0], tuple(sorted(zip(g.PUMP_ID.astype(str), g.CURVE.astype(str)))))
            if key in seen_units:
                dup_of[i] = seen_units[key]; q = 0.0
            else:
                seen_units[key] = str(PU.UNITID.iloc[i])
        q = q if np.isfinite(q) else 0.0
        fr = pd.to_numeric(PU.FLOW_RATE.iloc[i], errors="coerce")
        if not q > 0 and np.isfinite(fr) and fr >= FLOW_MIN_LPS:        # 没有曲线：用台账 FLOW_RATE（L/s，整站）；< 10 的值是泵台数（和 COMMENTS 的 2x58 对不上流量），不用
            q = float(fr); q_from[i] = "FLOW_RATE field (L/s, whole station)"
        flow_st.append(q)
    flow_st = np.array(flow_st)
    groups = collections.defaultdict(list)
    why_not = {}
    for i in np.flatnonzero(p_in):
        if pdead[i]:
            why_not[i] = "expired pump station record"; continue
        if i in dup_of:
            why_not[i] = "duplicate: the same pump units are already counted under station record " + ascii_id(dup_of[i]); continue
        if not flow_st[i] > 0:
            why_not[i] = "no pump flow: no pump curve with flow > 0 (PUMP_UNIT / PUMP_CURVE) and FLOW_RATE is empty or below 10 (those values are pump counts); capacity, if any, is only in the free-text COMMENTS"; continue
        groups[("c", int(comp_of[i]))].append(i) if comp_of[i] >= 0 else groups[("p", i)].append(i)
    n_built = 0
    for key, rows in groups.items():
        Q = float(sum(flow_st[r] for r in rows)) / 1000.0
        touched, far = set(), []
        if key[0] == "c":
            ends_i = np.flatnonzero(lp == key[1])
            d, j = tree.query(pe[ends_i])
            touched = {names[int(jj_)] for dd_, jj_ in zip(d, j) if dd_ <= OUTFALL_TOL}
            far = [(names[int(jj_)], pe[e]) for e, dd_, jj_ in zip(ends_i, d, j) if dd_ <= DISCHARGE_TOL]
        suction = None
        cand = [t for t in touched if t in wet_node]
        if cand:
            suction = min(cand, key=lambda t: inv[t])
        else:
            q0 = ppt[rows[0]]
            d, j = tree.query(q0, k=8)
            for dd_, jj_ in zip(np.atleast_1d(d), np.atleast_1d(j)):
                if dd_ <= WET_NEAR and names[int(jj_)] in wet_node:
                    suction = names[int(jj_)]; break
            if suction is None and touched:
                suction = min(touched, key=lambda t: inv[t])
            if suction is None:
                d1, j1 = tree.query(q0)
                if d1 <= 5.0:
                    suction = names[int(j1)]
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
            v = pd.to_numeric(WW.INVERT_LEV.iloc[wrow], errors="coerce")
            if np.isfinite(v):
                zb = float(v)
        if not np.isfinite(zb) or zb > inv[suction] or ground[suction] - zb > 25 or ground[suction] - zb < 0.5:
            zb = inv[suction]
        depth = max(ground[suction] - zb, 1.0)
        ung = [un_by[PU.UNITID.iloc[r]] for r in rows if PU.UNITID.iloc[r] in un_by]
        on_a = [float(v) for g in ung for v in g.ON_ELEV.astype(float) if np.isfinite(v) and v != 0.0]
        off_a = [float(v) for g in ung for v in g.OFF_ELEV.astype(float) if np.isfinite(v) and v != 0.0]
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
        labels = "+".join(ascii_id(PU.UNITID.iloc[r]) for r in rows)[:60]
        nun = int(sum(len(g) for g in ung)) or len(rows)
        pumps.append({"id": pid, "from": suction, "to": to, "q_m3s": Q, "on": on, "off": off, "z_bot": min(zb, inv[suction]), "inv_orig": inv[suction], "ground": ground[suction],
                      "how": how, "ps": pid, "unitid": labels, "n": nun, "q_each": Q * 1000.0 / max(nun, 1), "q_src": "; ".join(sorted({q_from.get(r, "PUMP_UNIT + PUMP_CURVE: curve flow at half the maximum head, summed over the units (L/s)") for r in rows})),
                      "level": str(PU.LEVEL.iloc[rows[0]])})
        n_built += len(rows)
    nb = collections.Counter(why_not.values())
    for r, w in why_not.items():
        limits_rows.append([PU.UNITID.iloc[r], str(PU.LEVEL.iloc[r]), str(PU.STATUS.iloc[r]), ascii_txt(PU.COMMENTS.iloc[r]), ascii_txt(PU.FLOW_RATE.iloc[r]), w])
    limits = [f"{c} pump station record(s) not built: {w}" for w, c in nb.items()]
    au.add("PUMP", len(PU), int(p_in.sum()), n_built,
           f"pump stations: {len(pumps)} fixed-flow SWMM pumps (flow = sum over the station's units of the pump-curve flow at half the maximum head, or the FLOW_RATE field when there is no curve; on/off levels from PUMP_UNIT when given, else 50% / 10% of well depth)",
           "; ".join(f"{c} not built: {w}" for w, c in nb.items()))
    MP = rd("MOVABLE_PUMP")
    mp = pts_of(MP)
    au.add("MOVABLE_PUMP", len(MP), int(shapely.contains_xy(dom, mp[:, 0], mp[:, 1]).sum()), 0, "not used", "emergency (temporary) pumps: excluded by decision, no fixed position or outlet")
    au.add("PUMP_UNIT / PUMP_CURVE", len(UN), len(UN), int(sum(len(un_by.get(PU.UNITID.iloc[r], [])) for rs in groups.values() for r in rs)),
           "pump units and their head-flow curves: give the flow and on/off levels of the built pumps", f"{int(UN.CURVE.isna().sum())} units have no curve; curves with zero flow give no flow")
    log(f"泵：建 {len(pumps)} 个泵组（{n_built} 个泵站记录），没建 {len(limits_rows)} 个：{dict(nb)}")

    # ---- 塘
    PD = rd("POND")
    deadp = PD.STATUS.astype("string").fillna("").str.strip().isin(B.DEAD_STATUS).astype(bool).to_numpy()
    geoms = shapely.force_2d(shapely.make_valid(PD.geometry.values))
    cen = shapely.centroid(geoms)
    pin = np.array([dom.contains(c) for c in cen]) & ~deadp
    dep_all = num(PD.DEPTH); vol_all = num(PD.VOLUME)
    ok_d = np.isfinite(dep_all) & (dep_all > 0) & (dep_all <= DEPTH_OK)
    med_depth = float(np.median(dep_all[ok_d])) if ok_d.any() else 2.0
    log(f"塘 {len(PD)}，域内且未废弃 {int(pin.sum())}；有可信深度的 {int(ok_d.sum())} 个，中位深度 {med_depth:.2f} m")
    ponds, links, pond_rows, used_ids = [], [], [], set()
    P_arr = P_xy
    for i in np.flatnonzero(pin):
        g = geoms[i]
        area = float(g.area)
        if area < 50:
            continue
        uid = ascii_id(PD.UNITID.iloc[i])
        dep = float(dep_all[i]) if ok_d[i] else 0.0
        vol_g = float(vol_all[i]) if np.isfinite(vol_all[i]) and vol_all[i] > 0 else 0.0
        est = dep <= 0
        dep = dep if dep > 0 else med_depth
        if vol_g > 0:
            a_flat = vol_g / dep; vol = vol_g; basis = "VOLUME" + (" with estimated depth (median of ponds with a plausible DEPTH)" if est else " and DEPTH")
        else:
            a_flat = area; vol = area * dep; basis = "polygon area x " + ("estimated depth (median of ponds with a plausible DEPTH)" if est else "DEPTH"); est = True
        u0, k = uid, 2
        while uid in used_ids:
            uid = f"{u0}_{k}"; k += 1
        used_ids.add(uid)
        x0, y0, x1, y1 = g.bounds
        m_ = np.flatnonzero((P_arr[:, 0] >= x0 - WIRE_DIST_M) & (P_arr[:, 0] <= x1 + WIRE_DIST_M) & (P_arr[:, 1] >= y0 - WIRE_DIST_M) & (P_arr[:, 1] <= y1 + WIRE_DIST_M))
        near = sorted(((g.distance(Point(P_arr[q])), names[int(q)]) for q in m_ if names[int(q)] not in outs), key=lambda t: t[0])
        near = [kk for dd_, kk in near if dd_ <= WIRE_DIST_M][:4]
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
                      "xy": (float(c0.x), float(c0.y)), "footprint": mapping(g), "inflows": near, "kind": "pond", "level": str(PD.LEVEL.iloc[i])})
        pond_rows.append({"unitid": uid, "node": node, "source": "POND", "level_in_data": str(PD.LEVEL.iloc[i]), "volume_m3": round(vol), "depth_m": round(dep, 2), "area_m2": round(a_flat),
                          "estimated": bool(est), "basis": basis, "wiring": how_w, "x": round(c0.x, 1), "y": round(c0.y, 1)})
    n_est = sum(1 for r in pond_rows if r["estimated"])
    au.add("POND", len(PD), int(pin.sum()), len(ponds), "L2 only: storage nodes with surface capture on the pond footprint (flat curve, check-valve pipes to up to 4 manholes within 8 m)",
           f"outside domain or dead status {int((~pin).sum())}; footprint < 50 m2 {int(pin.sum()) - len(ponds)}; {n_est} of the used ponds have estimated volume/depth (see ponds.json); the LEVEL field is not used (decision: L2 = L1 + all ponds)")
    log(f"塘：用 {len(ponds)}（其中 volume/depth 为估算的 {n_est}），接塘管 {len(links)}")

    # ---- 渗井
    SK = rd("SOAKAWAY")
    sp = pts_of(SK)
    s_in = shapely.contains_xy(dom, sp[:, 0], sp[:, 1])
    sdead = SK.STATUS.astype("string").fillna("").str.strip().isin(B.DEAD_STATUS).astype(bool).to_numpy()
    import rasterio
    with rasterio.open(datapaths.get("dtm")) as ds:
        sz = np.array([v[0] for v in ds.sample(map(tuple, sp))], float)
    n_soak, n_bad = 0, 0
    for i in np.flatnonzero(s_in & ~sdead):
        L_, W_, D_ = (float(pd.to_numeric(SK[c].iloc[i], errors="coerce") or 0.0) for c in ("SOAKAWAY_L", "SOAKAWAY_W", "SOAKAWAY_D"))
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
                      "xy": (float(sp[i, 0]), float(sp[i, 1])), "footprint": mapping(fp), "inflows": [], "kind": "soakaway", "level": "L1"})
        pond_rows.append({"unitid": uid, "node": "PND_" + uid, "source": "SOAKAWAY", "level_in_data": "L1", "volume_m3": round(L_ * W_ * D_, 1), "depth_m": round(D_, 2), "area_m2": round(L_ * W_, 1), "estimated": False,
                          "basis": "SOAKAWAY_L x W x D", "wiring": "isolated storage; takes surface water of its cell until full, no infiltration", "x": round(sp[i, 0], 1), "y": round(sp[i, 1], 1)})
        n_soak += 1
    au.add("SOAKAWAY", len(SK), int(s_in.sum()), n_soak, "L2 only (with the ponds): isolated storage node L x W x D, takes surface water of its 2D cell until full (no infiltration)",
           f"{n_bad} in-domain records have no usable L/W/D (0 or implausible); dead status {int((s_in & sdead).sum())}")
    au.add("EXCLUDED", len(rd("EXCLUDED", geom=False)), 0, 0, "not used", "pipes the data set itself marks as excluded from the network")
    au.add("Catchment", 0, 0, 0, "not used", "no catchment layer in this data set; domain is the whole city (1,233.6 km2); runoff uses the whole-city SCS CN 82 / Tc 30 min")

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
    for s in limits + [p["how"] for p in pumps] + [p["unitid"] for p in pumps] + [p["unitid"] for p in ponds]:
        assert not CJK.search(str(s)), s
    tag = "_trial" if a.bbox else ""
    (out / f"af_net_{REG}{tag}.pkl").write_bytes(pickle.dumps(N))
    (out / f"{REG}_ponds{tag}.json").write_text(json.dumps({"region": REG, "note": "Storage objects used in L2 (not in L1): ponds (POND layer; volume / depth from the data, else polygon area x median plausible depth, marked estimated) and soakaways.", "ponds": pond_rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out / f"pumps_not_built{tag}.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); w.writerow(["unitid", "level_in_data", "status", "comments_ascii", "flow_rate_field", "reason"]); w.writerows(limits_rows)
    (out / f"{REG}_pumps{tag}.json").write_text(json.dumps({"region": REG, "pumps_built": pumps, "not_built_summary": limits}, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out / f"data_usage_audit{tag}.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); w.writerow(["layer", "features", "in_domain", "used_in_calculation", "how_used", "not_used_or_notes"]); w.writerows(au.rows)
    log(f"完成：{out}（{time.time() - t0:.0f} s）：节点 {len(xy):,} 管 {len(pipes):,} 出水口 {len(outs)} 泵 {len(pumps)} 塘 / 渗井 {len(ponds)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
