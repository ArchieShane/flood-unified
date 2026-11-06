# -*- coding: utf-8 -*-
"""29.RB2 四态对比的一维模型：客户 RB_02 模型的 shapefile 导出 → SWMM .inp（L1 / L2 / L2+A），
L2+B 由 tools/build_l2p.py 的箱涵流程在 L2 上生成。

------------------------------------------------------------------ 数据
客户 RB_02 模型（OneDrive_1_9-26-2026.zip）只有 shapefile：管 33,067、人孔 14,286、雨水口 18,663、出水口 50、
泵 68（点）、湿井 20、塘 42、压力管 144、压力节点 8、子汇水区 66,579（A_UNIFIED / SCS_CN / TC / OUT_NODE）。
两处要当心（2026-09-27 查实）：
  · **压力管的表头错位两格**（dbf 里两列空字段把表头挤歪了）：标 START_NODE 的那列其实是终点，
    起点在 L_SCLD 列，编号在 INV_STRT 列……下面 _pressure_pipes() 按实际含义重排；
  · **泵是点，不是连接**：湿井 →（压力管）→ 泵点 →（压力管）→ 压力节点。这里把「泵点 + 前后两段压力管」
    合成一个 SWMM 泵，从吸水端接到出水端，额定流量 / 扬程取泵点的 PUMP_FLOW / PUMP_HEAD。

------------------------------------------------------------------ 导出缺了什么、怎么补（推断，逐条标出来）
导出里**没有堰、孔口、出流口**这几类连接，而塘正是靠它们接进管网的：
  · BNS_N 的 4 座塘：出口构筑物 BNS_N_POS-1…5 还在（5 根管的起点，管径 400–600 mm），**进口一个都没有**；
  · PO 塘（归属未确认，见下）：旁边 4 m 就是泵（PO-4 ← PMP-31 361 L/s、PO-18 ← PMP-56 102 L/s），泵到塘的连接不在导出里。
补法：
  · 塘的进口 —— 管网在塘边 100 m 内的**末端井**（只进不出、不是出水口）用一根短管接进塘，带单向拍门；
  · 塘的出口 —— 出口构筑物 POS 按最近的塘配对，塘 → POS 用一个底孔（面积 = POS 下游管的断面）；
  · PO 塘的配套泵 —— 泵点 20 m 内有 PO 塘、又没有出水压力管的，泵直接排进那座塘。
**这些连接是推断的，不是客户模型写的**；拿到客户完整模型后替换。管网本身完整（66,134 个管端点只缺上面 5 个 POS）。

------------------------------------------------------------------ 四态（用户 2026-09-27 定）
  L1    客户 RB_02 管网，去掉 PO 塘及其配套泵，去掉 BNS_N 塘
  L2    L1 + BNS_N 塘 1–4（**按推断当作已建成**：同组的 BNS_N_Pond 5 与台账已建成塘 SHM17_PND_40 占地 100% 重合）
  L2+A  L2 + PO 塘与配套泵（= 客户交付的原样）。**PO 塘是谁设计的没有文件写明**（2026-09-28 查实）：
        Parsons 的 4 份报告里没有 RB2；编号 PO-x、361.1 L/s 的 12 寸泵、「泵打到塘边出水口」的接法都和
        Parsons 的惯例一样，但只是像。24 座里只有 PO-4、PO-18 在 29.RB2 内。对外一律叫「PO 塘方案（来源待确认）」
  L2+B  L2 + 本系统的地下箱涵（build_l2p.py，避开路面、建筑、地块与红线）

雨：61 mm / 2 h，客户 2 h 设计雨型（与 Mussafah_00 相同）。产流用 RB_02 自己的子汇水区参数：
逐子汇水区 SCS 净雨 + SCS 单位线（按 CN、Tc 分组），汇到各自的 OUT_NODE。

用法：python tools/rb02_model.py            # 出 L1 / L2 / L2A 三个 .inp 与二维准备文件
产物：$WORK/RB2x_{L1,L2,L2A}.inp、$WORK/RB2x_baseline_R61D2.inp（= L2，供 build_l2p 选址）、c2d/pilot_RB2x_*.json、c2d/dem_RB2x.tif
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import pathlib
import re
import sys
import tempfile
import zipfile

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import build_city1d as B  # noqa: E402
from workdir import WORK  # noqa: E402

ZIP = r"D:/世界模型/OneDrive_1_9-26-2026.zip"
NAME = "RB2x"
REGION = "29.RB2"
SIM_H = 6.0
LINK_M = 100.0
WETWELL_A = 50.0          # 湿井断面面积：导出里只写了「Constant Area」没给数，取 50 m²（推断）
BUF_M = 500.0             # 二维计算域：29.RB2 外扩


def sid(x) -> str:
    """SWMM 的 ID 不能有空格和括号。"""
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", str(x).strip())


def load():
    import geopandas as gpd
    z = zipfile.ZipFile(ZIP); d = tempfile.mkdtemp()
    for n in z.namelist():
        if n.startswith("RB_02/"):
            z.extract(n, d)
    R = lambda L: gpd.read_file(os.path.join(d, "RB_02", L + ".shp")).set_crs(32640, allow_override=True)
    return {L: R(L) for L in ("Conduits", "Manholes", "Catchbasins", "Outfall", "Pumps", "Wetwell", "Ponds",
                              "Pressure_Pipes", "Pressure_Junctions", "Catchments")}


def _pressure_pipes(pp):
    """表头错位两格，按实际含义重排。"""
    return pp.rename(columns={"CHCKVALVE?": "ID_", "USERLGTH?": "INV_STRT_", "ID": "INV_STP_", "INV_STRT": "LABEL_",
                              "INV_STP": "L_", "LABEL": "START_ID_", "L_SCLD": "START_NODE_", "START_ID": "STOP_ID_",
                              "START_NODE": "STOP_NODE_"})


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.parse_args(argv)
    import geopandas as gpd
    from shapely.geometry import Point, mapping
    from shapely.ops import unary_union
    from shapely.validation import make_valid
    import runoff as R

    G = load()
    C = G["Conduits"]; pp = _pressure_pipes(G["Pressure_Pipes"]); PU = G["Pumps"]; PO = G["Ponds"]
    # ---------------- 节点
    node = {}          # id → dict(kind, x, y, inv, gnd)
    for L in ("Manholes", "Catchbasins"):
        for _, r in G[L].iterrows():
            node[sid(r.LABEL)] = {"kind": "J", "x": r.geometry.x, "y": r.geometry.y,
                                  "inv": float(r.ELEV_INV), "gnd": float(r.ELEV_GND)}
    for _, r in G["Pressure_Junctions"].iterrows():      # 这张表也错位：标签在 INV_STRT 列
        node[sid(r.INV_STRT)] = {"kind": "J", "x": r.geometry.x, "y": r.geometry.y, "inv": float(r.ELEV), "gnd": float(r.ELEV_GND)}
    for _, r in G["Wetwell"].iterrows():
        node[sid(r.LABEL)] = {"kind": "WW", "x": r.geometry.x, "y": r.geometry.y, "inv": float(r.INIT_ELEV), "gnd": float(r.ELEV_GND)}
    for _, r in G["Outfall"].iterrows():
        node[sid(r.LABEL)] = {"kind": "O", "x": r.geometry.x, "y": r.geometry.y, "inv": float(r.ELEV_INV),
                              "gnd": float(r.ELEV_GND), "stage": float(r.USER_TLWTR) if r.USER_TLWTR == r.USER_TLWTR else None,
                              "gate": bool(r.TIDAL_GATE)}
    # 缺的 5 个出口构筑物 POS：坐标、管底取它那根管的起点
    for _, r in C.iterrows():
        for end, inv_, pt in (("START_NODE", r.INV_STRT, r.geometry.coords[0]), ("STOP_NODE", r.INV_STP, r.geometry.coords[-1])):
            k = sid(r[end])
            if k not in node:
                node[k] = {"kind": "J", "x": pt[0], "y": pt[1], "inv": float(inv_), "gnd": None, "inferred": "导出里缺的节点（出口构筑物）"}
    gnd = B.sample_dtm([v["x"] for v in node.values()], [v["y"] for v in node.values()])
    for (k, v), g in zip(node.items(), gnd):
        if v["gnd"] is None or not np.isfinite(v["gnd"]):
            v["gnd"] = float(g) if np.isfinite(g) else v["inv"] + B.COVER_MED
    # ---------------- 连接
    links = []
    for _, r in C.iterrows():
        links.append({"id": sid(r.LABEL), "n1": sid(r.START_NODE), "n2": sid(r.STOP_NODE), "len": max(float(r.L), 1.0),
                      "z1": float(r.INV_STRT), "z2": float(r.INV_STP), "d": float(r.D) / 1000.0, "n": float(r.MANN_N or 0.013)})
    pump_pt = {sid(r.LABEL): r for _, r in PU.iterrows()}
    suction, discharge, used_pp = {}, {}, set()
    for _, r in pp.iterrows():
        a, b = sid(r.START_NODE_), sid(r.STOP_NODE_)
        if b in pump_pt: suction[b] = a; used_pp.add(r.LABEL_)
        if a in pump_pt: discharge[a] = b; used_pp.add(r.LABEL_)
    for _, r in pp.iterrows():
        if r.LABEL_ in used_pp:
            continue
        links.append({"id": sid(r.LABEL_), "n1": sid(r.START_NODE_), "n2": sid(r.STOP_NODE_), "len": max(float(r.L_), 1.0),
                      "z1": float(r.INV_STRT_), "z2": float(r.INV_STP_), "d": float(r.D) / 1000.0, "n": 0.011})
    # ---------------- 塘
    PO = PO.copy(); PO["geometry"] = PO.geometry.apply(make_valid)
    gnd_p = B.sample_dtm([g.centroid.x for g in PO.geometry], [g.centroid.y for g in PO.geometry])
    ponds = {}
    for (_, r), g in zip(PO.iterrows(), gnd_p):
        dep = float(r.DEPTH_MAX or 0.0)
        floor = float(r.MAX_HGL) - dep if dep > 0 else (float(g) - 2.0 if np.isfinite(g) else float(r.MAX_HGL) - 2.0)
        grp = "BNS" if str(r.LABEL).startswith("BNS_N") else "PO" if str(r.LABEL).startswith("PO-") else "RM2"
        ponds[sid(r.LABEL)] = {"id": sid(r.LABEL), "label": r.LABEL, "grp": grp, "floor": floor, "depth": max(dep + 0.5, 2.0),
                               "area": max(float(r.SCALE_AREA), 50.0), "ground": float(g) if np.isfinite(g) else None,
                               "geom": r.geometry, "xy": (r.geometry.centroid.x, r.geometry.centroid.y)}
    # 泵：吸水端 → 出水端；没有出水压力管、20 m 内有 PO 塘的，排进塘（推断）
    pumps, inferred = [], []
    for k, r in pump_pt.items():
        s, e = suction.get(k), discharge.get(k)
        if e is None:
            dd = [(p["geom"].distance(r.geometry), pid) for pid, p in ponds.items() if p["grp"] == "PO"]
            dd.sort()
            if dd and dd[0][0] <= 20.0:
                e = dd[0][1]; inferred.append(f"泵 {k} 排进 {e}（{dd[0][0]:.0f} m）")
        if s is None:                                   # 没有吸水压力管：取最近的湿井 / 节点
            best = min(((math.hypot(v["x"] - r.geometry.x, v["y"] - r.geometry.y), n) for n, v in node.items()
                        if v["kind"] in ("WW", "J")), default=(None, None))
            s = best[1]; inferred.append(f"泵 {k} 的吸水端取最近节点 {s}（{best[0]:.0f} m）")
        # PO 塘的泵在导出里是「从管网抽到塘边一个出水口」（PMP-31：MH3962 → O-9，O-9 离 PO-4 只有 4 m）——
        # 等于抽出去就没了，塘永远灌不满，会高估这套方案。改成排进那座塘，塘满了由库容卡住（推断）
        if e in node and node[e]["kind"] == "O":
            dd = sorted((p["geom"].distance(r.geometry), pid) for pid, p in ponds.items() if p["grp"] == "PO")
            if dd and dd[0][0] <= 30.0:
                inferred.append(f"泵 {k}：导出里排到出水口 {e}，改排进旁边的 {dd[0][1]}（{dd[0][0]:.0f} m）")
                e = dd[0][1]
        if s and e:
            pumps.append({"id": k, "n1": s, "n2": e, "q": float(r.PUMP_FLOW) / 1000.0, "h": max(float(r.PUMP_HEAD), 1.0),
                          "po": e in ponds and ponds[e]["grp"] == "PO"})
    # PO 塘的 38 台泵在导出里直接从普通井抽水（没有湿井），动力波在这种没有调蓄容积的井上会失稳：
    # 首轮 L2A 里 MH-RB2-1943 节点连续性 −200%，三台泵「抽」出 30 万 m³、把 PO-8 灌爆——是数值假象。
    # 真实泵站一定有湿井：吸水井改成蓄水节点，面积按 30 s × 总额定流量 / 1 m 取、不小于 WETWELL_A（推断）
    po_ww = collections.defaultdict(float)
    for p in pumps:
        if p["po"] and node.get(p["n1"], {}).get("kind") == "J":
            po_ww[p["n1"]] += p["q"]
    if po_ww:
        inferred.append(f"PO 塘配套泵的吸水井 {len(po_ww)} 处导出里是普通井，补成湿井（面积 max({WETWELL_A:g} m², 30 s × 额定流量)）；"
                        "塘水深到 95% 停泵、回落到 85% 再开（高水位停泵）")
    # 塘的进口：塘边 100 m 内的末端井（只进不出、不是出水口）→ 短管 + 拍门
    outdeg, indeg = collections.Counter(), collections.Counter()
    for l in links:
        outdeg[l["n1"]] += 1; indeg[l["n2"]] += 1
    for p in pumps:
        outdeg[p["n1"]] += 1
    dead = [n for n in node if indeg[n] > 0 and outdeg[n] == 0 and node[n]["kind"] != "O"]
    for pid, p in ponds.items():
        if p["grp"] == "RM2":
            continue
        for n in dead:
            dd = p["geom"].distance(Point(node[n]["x"], node[n]["y"]))
            if dd <= LINK_M:
                p.setdefault("inlets", []).append((n, float(dd)))
    # 塘的出口：POS 按最近的塘配对
    for n, v in node.items():
        if n.startswith("BNS_N_POS"):
            dd = sorted((p["geom"].distance(Point(v["x"], v["y"])), pid) for pid, p in ponds.items() if p["grp"] == "BNS")
            if dd and dd[0][0] <= LINK_M:
                dn = [l for l in links if l["n1"] == n]
                ponds[dd[0][1]].setdefault("outlets", []).append((n, dn[0]["d"] if dn else 0.6, float(dd[0][0])))
    for pid, p in ponds.items():
        if p["grp"] != "RM2":
            inferred.append(f"{p['label']}：进口 {len(p.get('inlets', []))} 处（末端井 → 塘）、出口 {len(p.get('outlets', []))} 处（塘 → POS）")

    # ---------------- 产流：RB_02 自己的子汇水区参数
    K = G["Catchments"]
    dt_h = 1.0 / 60.0
    shape = R.read_shape(WORK / "Mussafah_baseline.inp", B.DUR_H, log=lambda *a: None)
    t, P_cum = R.hyetograph(B.P_MM, B.DUR_H, dt_h, shape, 0.0, total_h=SIM_H + 1)
    area_node = collections.defaultdict(lambda: collections.defaultdict(float))
    coords = np.array([[v["x"], v["y"]] for v in node.values()]); keys = list(node)
    from scipy.spatial import cKDTree
    tree = cKDTree(coords)
    n_direct = 0
    for _, r in K.iterrows():
        a = float(r.A_UNIFIED or 0.0)
        if a <= 0:
            a = float(r.geometry.area)
        k = sid(r.OUT_NODE)
        if k not in node or node[k]["kind"] == "O":
            c0 = r.geometry.centroid; k = keys[tree.query((c0.x, c0.y))[1]]
        else:
            n_direct += 1
        grp = (round(float(r.SCS_CN or 85.0)), max(5.0, round(float(r.TC or 10.0) / 5.0) * 5.0))
        area_node[k][grp] += a
    groups = sorted({g for v in area_node.values() for g in v})
    series = {}
    for cn, tc in groups:
        exc = R.scs_cum_excess(P_cum, cn); inc = np.diff(exc, append=exc[-1])
        u = R.unit_hydrograph(tc, dt_h, 10_000.0)
        series[(cn, tc)] = np.convolve(inc, u, mode="full")[:len(t)]
    # 每个节点一条入流：按它收到的各组面积加权合成（SWMM 一个节点只能挂一条 FLOW 入流）
    node_q = {}
    for k, gm in area_node.items():
        q = sum(series[g] * (a / 1e4) for g, a in gm.items())
        node_q[k] = q
    vol = sum(float(q.sum()) * dt_h * 3600 for q in node_q.values())
    print(f"  产流：子汇水区 {len(K):,} 个（OUT_NODE 直接对上 {n_direct / len(K) * 100:.0f}%），CN×Tc 分组 {len(groups)}，"
          f"入流节点 {len(node_q):,}，总量 {vol:,.0f} m³")

    # ---------------- 写 .inp
    def write(state):
        use_p = {pid: p for pid, p in ponds.items()
                 if (p["grp"] == "RM2") or (p["grp"] == "BNS" and state in ("L2", "L2A")) or (p["grp"] == "PO" and state == "L2A")}
        use_pumps = [p for p in pumps if (not p["po"]) or state == "L2A"]
        Lx = []; ap = Lx.append
        ap("[TITLE]"); ap(f";; RB_02 → {state}（{REGION} 四态对比，塘的进出口为推断）"); ap("")
        ap("[OPTIONS]")
        for kk, vv in (("FLOW_UNITS", "CMS"), ("INFILTRATION", "HORTON"), ("FLOW_ROUTING", "DYNWAVE"), ("LINK_OFFSETS", "ELEVATION"),
                       ("MIN_SLOPE", "0"), ("ALLOW_PONDING", "NO"), ("START_DATE", "01/01/2024"), ("START_TIME", "00:00:00"),
                       ("REPORT_START_DATE", "01/01/2024"), ("REPORT_START_TIME", "00:00:00"), ("END_DATE", "01/01/2024"),
                       ("END_TIME", f"{int(SIM_H):02d}:00:00"), ("REPORT_STEP", "00:05:00"), ("WET_STEP", "00:05:00"),
                       ("DRY_STEP", "01:00:00"), ("ROUTING_STEP", "00:00:10"), ("LENGTHENING_STEP", "10"), ("THREADS", "8")):
            ap(f"{kk}  {vv}")
        ap(""); ap("[REPORT]"); ap("INPUT NO"); ap("CONTROLS NO"); ap("NODES NONE"); ap("LINKS NONE"); ap("")
        ap("[JUNCTIONS]")
        for k, v in node.items():
            if v["kind"] == "J" and k not in use_p and not (state == "L2A" and k in po_ww):
                # 管网直接连着的塘（RM2）在用时写成蓄水节点，不再写成井；L2A 的 PO 泵吸水井写成湿井
                ap(f"{k}  {v['inv']:.3f}  {max(v['gnd'] - v['inv'], 0.5):.3f}  0  0  0")
        ap(""); ap("[OUTFALLS]")
        for k, v in node.items():
            if v["kind"] == "O":
                ap(f"{k}  {v['inv']:.3f}  " + (f"FIXED  {v['stage']:.2f}" if v.get("stage") is not None else "FREE")
                   + f"  {'YES' if v.get('gate') else 'NO'}")
        ap(""); ap("[STORAGE]")
        for k, v in node.items():
            if v["kind"] == "WW":
                ap(f"{k}  {v['inv']:.3f}  {max(v['gnd'] - v['inv'], 1.0):.3f}  0  FUNCTIONAL  0  0  {WETWELL_A}  0  0")
        if state == "L2A":
            for k, q in po_ww.items():
                v = node[k]
                ap(f"{k}  {v['inv']:.3f}  {max(v['gnd'] - v['inv'], 1.0):.3f}  0  FUNCTIONAL  0  0  {max(WETWELL_A, 30.0 * q):.1f}  0  0")
        for pid, p in use_p.items():
            ap(f"{pid}  {p['floor']:.3f}  {p['depth']:.3f}  0  FUNCTIONAL  0  0  {p['area']:.1f}  0  0")
        ap(""); ap("[CONDUITS]")
        cond = list(links)
        for pid, p in use_p.items():
            for n, dd in p.get("inlets", []):
                cond.append({"id": f"PI_{pid}_{n}"[:60], "n1": n, "n2": pid, "len": max(dd, 10.0), "z1": node[n]["inv"],
                             "z2": max(p["floor"], min(node[n]["inv"], p["floor"] + p["depth"])), "d": 0.6, "n": 0.013, "flap": True})
        for l in cond:
            ap(f"{l['id']}  {l['n1']}  {l['n2']}  {l['len']:.2f}  {l['n']:.4f}  {l['z1']:.3f}  {l['z2']:.3f}  0  0")
        ap(""); ap("[ORIFICES]")
        for pid, p in use_p.items():
            for n, d, _ in p.get("outlets", []):
                ap(f"PO_{pid}_{n}"[:60] + f"  {pid}  {n}  BOTTOM  {p['floor']:.3f}  0.65  NO  0")
        ap(""); ap("[PUMPS]")
        for p in use_pumps:
            ap(f"{p['id']}  {p['n1']}  {p['n2']}  PC_{p['id']}  ON  0  0")
        po_rules = [p for p in use_pumps if p["po"] and p["n2"] in use_p]
        if po_rules:
            ap(""); ap("[CONTROLS]")
            for p in po_rules:
                d = use_p[p["n2"]]["depth"]
                ap(f"RULE {p['id']}_FULL"); ap(f"IF NODE {p['n2']} DEPTH >= {0.95 * d:.3f}"); ap(f"THEN PUMP {p['id']} STATUS = OFF"); ap("")
                ap(f"RULE {p['id']}_RESUME"); ap(f"IF NODE {p['n2']} DEPTH < {0.85 * d:.3f}"); ap(f"THEN PUMP {p['id']} STATUS = ON"); ap("")
        ap(""); ap("[XSECTIONS]")
        for l in cond:
            ap(f"{l['id']}  CIRCULAR  {max(l['d'], 0.1):.3f}  0  0  0  1")
        for pid, p in use_p.items():
            for n, d, _ in p.get("outlets", []):
                ap(f"PO_{pid}_{n}"[:60] + f"  CIRCULAR  {max(d, 0.2):.3f}  0  0  0")
        ap(""); ap("[LOSSES]")
        for l in cond:
            if l.get("flap"):
                ap(f"{l['id']}  0  0  0  YES  0")
        ap(""); ap("[CURVES]")
        for p in use_pumps:                              # 3 点扬程—流量曲线：额定点 ± 30%
            ap(f"PC_{p['id']}  Pump3  0  {p['q'] * 1.2:.4f}"); ap(f"PC_{p['id']}  {p['h']:.2f}  {p['q']:.4f}"); ap(f"PC_{p['id']}  {p['h'] * 1.4:.2f}  0")
        ap(""); ap("[INFLOWS]")
        for k, q in node_q.items():
            if (q > 1e-7).any():                         # 低 CN 的子汇水区在 61 mm 下不产流，没有过程线就不挂入流
                ap(f"{k}  FLOW  TS_{k}  FLOW  1.0  1.0")
        ap(""); ap("[TIMESERIES]")
        for k, q in node_q.items():
            nz = np.flatnonzero(q > 1e-7)
            if not len(nz):
                continue
            for i in range(0, min(nz[-1] + 2, len(t)), 2):     # 2 分钟一个点，够解单位线
                hh, mm = divmod(int(round(t[i] * 60)), 60)
                ap(f"TS_{k}  {hh:02d}:{mm:02d}  {q[i]:.6f}")
        ap(""); ap("[COORDINATES]")
        for k, v in node.items():
            if k not in use_p:
                ap(f"{k}  {v['x']:.2f}  {v['y']:.2f}")
        for pid, p in use_p.items():
            ap(f"{pid}  {p['xy'][0]:.2f}  {p['xy'][1]:.2f}")
        ap("")
        f = WORK / f"{NAME}_{state}.inp"; f.write_text("\n".join(Lx), encoding="utf-8")
        return f, use_p

    C0 = gpd.read_file(ROOT / "console" / "data" / "catchments_sw.geojson").to_crs(32640)
    C0["geometry"] = C0.geometry.apply(make_valid).buffer(0)
    reg = unary_union(list(C0[C0.label == REGION].geometry))
    dom = reg.buffer(BUF_M).simplify(20.0)
    from shapely.geometry import Polygon
    dom = Polygon(max(dom.geoms, key=lambda g: g.area).exterior) if dom.geom_type == "MultiPolygon" else Polygon(dom.exterior)
    ring = list(dom.exterior.coords)[:-1]
    if not dom.exterior.is_ccw:
        ring = ring[::-1]
    from shapely.prepared import prep
    pr = prep(dom)
    t_r = np.arange(0.0, SIM_H + 1e-9, dt_h)
    import rasterio
    from rasterio.windows import from_bounds
    from swbrain import db
    c2d = WORK / "c2d"; c2d.mkdir(exist_ok=True)
    X0, Y0, X1, Y1 = dom.bounds
    dem_f = c2d / f"dem_{NAME}.tif"
    with rasterio.open((db.load_config(None).get("terrain") or {})["dtm_path"]) as ds:
        win = from_bounds(X0, Y0, X1, Y1, ds.transform).round_offsets().round_lengths()
        z = ds.read(1, window=win); prof = ds.profile.copy()
        prof.update(width=z.shape[1], height=z.shape[0], transform=ds.window_transform(win), compress="deflate")
        with rasterio.open(dem_f, "w", **prof) as w:
            w.write(z, 1)
    # 无管网覆盖处直接下雨：RB_02 的子汇水区几乎盖满，服务半径取 50 m
    exc_all = R.scs_cum_excess(R.hyetograph(B.P_MM, B.DUR_H, dt_h, shape, 0.0, total_h=SIM_H + 1)[1], 85.0)
    out = {}
    for st in ("L1", "L2", "L2A"):
        f, use_p = write(st)
        if st == "L2":
            (WORK / f"{NAME}_baseline_R61D2.inp").write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        # 地表进塘：只对**地面以下**的开敞塘（塘底低于地面）；地上的「水箱」式塘地表水进不去
        surf = [p for p in use_p.values() if p["grp"] in ("BNS", "PO") and p["ground"] is not None and p["floor"] < p["ground"] - 0.3]
        meta = {"name": NAME, "state": st, "box": dom.bounds, "domain_polygon": [[round(x, 1), round(y, 1)] for x, y in ring],
                "R_served_m": 50.0,
                "nodes": {k: [round(v["x"], 2), round(v["y"], 2)] for k, v in node.items() if v["kind"] != "O" and pr.contains(Point(v["x"], v["y"]))},
                "ponds": [{"node": p["id"], "unitid": p["label"], "vol": p["area"] * p["depth"], "xy": p["xy"],
                           "footprint": mapping(p["geom"])} for p in surf],
                "rain_t_h": [round(float(x), 4) for x in t_r], "rain_excess_mm": [round(float(np.interp(x, t, exc_all)), 4) for x in t_r],
                "inferred": inferred, "note": "塘的进出口、PO 泵到塘的连接是推断的；PO 塘的设计方未确认（客户导出里没有堰 / 孔口 / 出流口）"}
        (c2d / f"pilot_{NAME}_{st}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        out[st] = {"inp": f.name, "ponds": sorted(use_p), "surface_ponds": [p["label"] for p in surf], "nodes_2d": len(meta["nodes"])}
        print(f"  {st}: → {f.name}　塘 {len([p for p in use_p.values() if p['grp'] != 'RM2'])}（地表进塘 {len(surf)}）　"
              f"泵 {len([p for p in pumps if (not p['po']) or st == 'L2A'])}　二维交换节点 {len(meta['nodes']):,}")
    print(f"  二维计算域 {dom.area / 1e6:,.1f} km²（{REGION} {reg.area / 1e4:,.0f} ha 外扩 {BUF_M:.0f} m）")
    print("  推断的连接："); [print("    " + x) for x in inferred]
    (ROOT / "out" / f"{NAME}_build.json").write_text(json.dumps({"states": out, "inferred": inferred}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
