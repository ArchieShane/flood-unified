# -*- coding: utf-8 -*-
"""强化学习训练汇水区按 F 系列标准重建（第 2a 步，2026-10-30 用户审定）。设计：docs/L2S强化学习_第2a步_训练汇水区按F系列标准重建_设计_20261030.md。

管网    治理窗口区域流程截取的治理后管网（out/exp_gov/regions/<区>/extract_gov.json），建法同 region._net_g（G 模型）
F 规则  1-3 管糙率按治理后管材；1-5 Force Pipe 按压力管 H-W C 130；排海口（管底 < HAT + 0.5 m）尾水 HAT 1.3 m + 拍门；
        1-6 泵：资产库的泵按位置（≤ 20 m）配 Parsons 泵的设计工况（流量、扬程），配不上的给缺省并标注（决定 ①）；
        1-12 步长 0.5 s；1-14 管网初始 25 % 有水；3-x 雨水口按 WA-726 A3 公式进水、检查井只溢流
产流    Parsons 子汇水区 + 客户 SCS CN（决定 ③）；出口节点坐标（无出口字段时用形心）挂到最近的治理后结构节点
雨      Mussafah「10 年一遇 + 20 %」雨型：61 mm × 1 / 2 / 24 h、96 mm × 1 h（决定 ④）；雨后 10 h
用法：python tools/rl_region_build.py --region RB-01 --storm M61D1 [--state L1] [--smoke]
产物：$WORK/RLF_<码>_<雨>_<态>.inp、c2d/pilot_…json、out/l2s_rl/<区>/model/inputs_<雨>_<态>.json（逐项输入登记）
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys
from collections import Counter, defaultdict

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE / "exp_gov")); sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import rb01_build as RB  # noqa: E402
import rda_build as RD  # noqa: E402
import rda_gov_build as GB  # noqa: E402
import region as RG  # noqa: E402
import runoff as R  # noqa: E402
import v_build as VB  # noqa: E402
from workdir import WORK  # noqa: E402

CFG_DIR = HERE / "rl_regions"
HAT, SEA_MARGIN = 1.3, 0.5
PUMP_MATCH_M = 20.0
Q_DEF, H_DEF, WW_AREA_DEF = 0.36111, 10.0, 20.0          # 配不上 Parsons 的泵：12 寸泵流量、扬程 10 m、湿井 20 m²【自定】
STORMS = {"M61D1": (61.0, 1.0), "M61D2": (61.0, 2.0), "M61D24": (61.0, 24.0), "M96D1": (96.0, 1.0)}
TAIL_H = 10.0
# Parsons 各模型导出的层名、字段名不统一：按候选名找
LAYERS = {"ca": ("Catchments", "CA"), "mh": ("Manholes", "MH"), "cb": ("Catch basins", "Catch Basins", "Catch_Basins", "CB"),
          "pu": ("Pumps", "Pump", "pump"), "ww": ("Wet Well", "Wet_Wells", "Wetwells", "WW")}
F_LABEL, F_GND, F_INV = ("LABEL", "LB"), ("ELEV_GND", "EL_GR"), ("ELEV_INV", "EL_IV")


def log(*a):
    print(*a, flush=True)


def col(df, names):
    for n in names:
        if n in df.columns:
            return df[n]
    return None


# ---------------- 顾问模型（Parsons，可多个合成一个区） ----------------
def parsons(paths):
    import geopandas as gpd
    out = {"ca": [], "nodes": {}, "pumps": [], "ww": []}
    for p in map(pathlib.Path, paths):
        files = {f.stem: f for f in p.glob("*.shp")}

        def rd(k):
            for n in LAYERS[k]:
                if n in files:
                    return gpd.read_file(files[n]).set_crs(32640, allow_override=True)
            return None
        tag = p.name
        for k in ("mh", "cb"):
            g = rd(k)
            if g is None:
                continue
            lab, gnd, inv = col(g, F_LABEL), col(g, F_GND), col(g, F_INV)
            for i, geom in enumerate(g.geometry):
                if lab is not None and geom is not None:
                    out["nodes"][(tag, RB.sid(lab.iloc[i]))] = (geom.x, geom.y)
        ca = rd("ca")
        if ca is not None:
            ca["geometry"] = ca.geometry.make_valid()
            on = col(ca, ("OUT_NODE",))
            for i, r in enumerate(ca.itertuples()):
                a = float(getattr(r, "A_UNIFIED", 0) or 0)
                if a <= 0:
                    continue
                xy = out["nodes"].get((tag, RB.sid(on.iloc[i]))) if on is not None and on.iloc[i] else None
                if xy is None:
                    c = r.geometry.centroid; xy = (c.x, c.y); src = "形心"
                else:
                    src = "出口节点"
                out["ca"].append({"model": tag, "area_m2": a, "cn": float(r.SCS_CN), "tc_min": max(float(r.TC), 1.0), "x": xy[0], "y": xy[1], "src": src})
        pu = rd("pu")
        if pu is not None:
            lab = col(pu, F_LABEL)
            for i, geom in enumerate(pu.geometry):
                if geom is None:
                    continue
                q = float(pu["PUMP_FLOW"].iloc[i] or 0); h = float(pu["PUMP_HEAD"].iloc[i] or 0)
                out["pumps"].append({"model": tag, "label": str(lab.iloc[i]) if lab is not None else "", "x": geom.x, "y": geom.y, "q": q, "h": h})
    return out


# ---------------- F 系列规则 ----------------
def f_materials(net, X, D, reg):
    """1-3：糙率按治理后管材（与 rda_gov_build.network 同规则；G 编号与 region._net_g 一致：跳过停用管后按顺序）"""
    import pyogrio
    bb = tuple(np.add(D.bounds, (-300, -300, 300, 300)))
    M = pyogrio.read_dataframe(GB.GDB, layer="StormwaterLine", bbox=bb, columns=["assetid", "d_material"], read_geometry=False)
    mat = dict(zip(M.assetid, M.d_material))
    gid = {f"G{i + 1}": r for i, r in enumerate(x for x in X["lines"] if x.get("assetstate") != 0)}
    cnt, CO = Counter(), []
    for co in net["CO"]:
        r = gid.get(co[0])
        if r is None:
            CO.append(co); continue
        m = mat.get(r["assetid"]) or "缺"
        n = GB.N_RCP if str(m).startswith("Reinforced Concrete") else GB.N_OTHER
        cnt[f"{m} → {n}"] += 1
        CO.append((co[0], co[1], co[2], co[3], n, *co[5:]))
    net["CO"] = CO
    fm = {c for c, r in gid.items() if r.get("group") == "Force Pipe"}                    # 1-5
    net["XS"] = [(x[0], "FORCE_MAIN", x[2], GB.HW_C, 0, 0, 1) if x[0] in fm else x for x in net["XS"]]
    reg["pipe_n_by_material"] = dict(cnt); reg["force_mains"] = len(fm & {c[0] for c in net["CO"]})


def f_outfalls(net, reg):
    """排海口：管底 < HAT + 0.5 m → 尾水 HAT 1.3 m + 拍门（RD-A 是按名字定的；这里按高程判，逐个登记）"""
    OF, rows = [], []
    for o in net["OF"]:
        sea = o[1] < HAT + SEA_MARGIN
        OF.append((o[0], o[1], f"FIXED {HAT:.3f}", "YES") if sea else o)
        rows.append({"id": o[0], "invert_m": round(o[1], 3), "sea": sea, "boundary": OF[-1][2], "flap": OF[-1][3]})
    net["OF"] = OF; reg["outfalls"] = rows


def f_pumps(net, X, P, reg):
    """1-6：资产库的泵（_net_g 里建成连接点）→ 湿井（STORAGE）+ 泵；出水的管改从新的出水节点出发。
    流量 / 扬程取 ≤ 20 m 内 Parsons 泵的设计工况；配不上的用缺省并标注（决定 ①）"""
    from scipy.spatial import cKDTree
    pz = P["pumps"]; tr = cKDTree(np.array([[p["x"], p["y"]] for p in pz])) if pz else None
    devs = {GB.sid(d["assetid"]): d for d in X["devices"] if d.get("group") == "Pump" and d.get("assetid")}
    rows, used = [], set()
    for k, d in devs.items():
        if k not in net["J"]:
            rows.append({"pump": k, "built": False, "why": "不在管网连通块里"}); continue
        outs = [i for i, c in enumerate(net["CO"]) if c[1] == k]
        if not outs:
            rows.append({"pump": k, "built": False, "why": "没有出水管"}); continue
        x, y = net["coords"][k]
        m, dist = None, None
        if tr is not None:
            dist, j = tr.query((x, y))
            if dist <= PUMP_MATCH_M and j not in used:
                m = pz[j]; used.add(j)
        q = m["q"] if m and m["q"] > 0 else Q_DEF; h = m["h"] if m and m["h"] > 0 else H_DEF
        inv, md = net["J"][k][0], max(net["J"][k][1], 1.0)
        on = (d["onelev"] - inv) if d.get("onelev") is not None else 0.6 * md
        off = (d["offelev"] - inv) if d.get("offelev") is not None else 0.2 * md
        on, off = min(max(on, 0.2), md), min(max(off, 0.0), md)
        if off >= on:
            off = max(on - 0.3, 0.0)
        del net["J"][k]
        cv = f"{k}_WW"; net["ST"].append((k, inv, md, 0.0, "TABULAR", cv))
        net["CU"] += [(cv, "STORAGE", 0.0, WW_AREA_DEF), (cv, "", md, WW_AREA_DEF)]
        po = f"{k}_PO"; net["J"][po] = [inv + md, 1.0, 0.0, VB.SEAL_SUR, 0.0]; net["coords"][po] = (x + 1.0, y + 1.0)
        for i in outs:
            c = net["CO"][i]; net["CO"][i] = (c[0], po, *c[2:5], 0.0, *c[6:])
        pc = f"{k}_PC"                                    # 三点曲线：设计点（H, Q）两侧（0.5H, 1.1Q）、（1.5H, 0.6Q）【自定】
        net["CU"] += [(pc, "PUMP3", round(0.5 * h, 3), round(1.1 * q, 4)), (pc, "", round(h, 3), round(q, 4)), (pc, "", round(1.5 * h, 3), round(0.6 * q, 4))]
        net["PU"].append((f"PU_{k}", k, po, pc, "ON", on, off))
        rows.append({"pump": k, "built": True, "matched": bool(m), "parsons": (m or {}).get("label"), "match_m": round(float(dist), 1) if m else None,
                     "q_m3s": round(q, 4), "head_m": round(h, 2), "on_m": round(on, 2), "off_m": round(off, 2), "default": not bool(m)})
    reg["pumps"] = rows
    reg["pumps_parsons_only"] = [p["label"] for i, p in enumerate(pz) if i not in used]     # Parsons 有、资产库没有：方案泵，不进现状


def inflows(P, net, struct, storm, D, reg):
    from scipy.spatial import cKDTree
    import pandas as pd
    mm, dur = STORMS[storm]
    from shapely.geometry import Point
    Db = D.buffer(300)
    ca = [c for c in P["ca"] if Db.contains(Point(c["x"], c["y"]))]
    keys = [k for k in net["J"] if k in struct]
    tr = cKDTree(np.array([net["coords"][k] for k in keys]))
    d, i = tr.query(np.array([[c["x"], c["y"]] for c in ca]))
    subs = pd.DataFrame({"area_m2": [c["area_m2"] for c in ca], "cn": [c["cn"] for c in ca], "tc_min": [c["tc_min"] for c in ca], "node": [keys[j] for j in i]})
    dt_h = 1.0 / 60.0
    shape = R.read_shape(WORK / "Mussafah_baseline.inp", dur, log=log)
    sim_h = dur + TAIL_H
    t, Pr = R.hyetograph(mm, dur, dt_h, shape, 0.0, total_h=sim_h)
    H = R.node_hydrographs(subs, t, Pr, dt_h, log=log)
    exc = np.zeros(len(t))
    for cn, g in subs.groupby("cn"):
        exc += R.scs_cum_excess(Pr, float(cn)) * g.area_m2.sum()
    exc /= subs.area_m2.sum()
    tot = sum(float(q.sum() * dt_h * 3600) for q in H.values())
    reg["runoff"] = {"storm": storm, "rain_mm": mm, "dur_h": dur, "shape": "Mussafah 10 年一遇 + 20 %（Mussafah_baseline.inp）", "subs_n": len(subs),
                     "subs_km2": round(float(subs.area_m2.sum() / 1e6), 3), "cn_aw": round(float(np.average(subs.cn, weights=subs.area_m2)), 1),
                     "excess_mm_aw": round(float(exc[-1]), 1), "runoff_m3": round(tot), "outlet_src": dict(Counter(c["src"] for c in ca)),
                     "remap_dist_m": {"median": round(float(np.median(d)), 1), "p95": round(float(np.percentile(d, 95)), 1), "max": round(float(d.max()), 1)}}
    log(f"  产流：{len(subs):,} 个 Parsons 子汇水区、{subs.area_m2.sum() / 1e6:.2f} km²，净雨 {exc[-1]:.1f} mm，入流 {tot:,.0f} m³；挂接距离中位 {np.median(d):.1f} m")
    return t, exc, dict(H), sim_h


def plan_a_l2(cfg, net, struct, D, reg):
    """没有已认定状态的区（ADMLINE）：L2 = L1 + 热点表预案 A 移动泵（同 rb01_build 的规则）：吸水井 = 热点最近的结构井，
    排到下游第一口在 L1 一维里不溢流的井（2 km 管长内）；恒流量泵（PUMP2）"""
    import heapq
    import geopandas as gpd
    from scipy.spatial import cKDTree
    l1 = WORK / "bench" / f"smoke_RLF_{cfg['code']}_M61D1_L1.rpt"
    if not l1.exists():
        raise SystemExit(f"先建 L1 并做一维冒烟（--smoke）：缺 {l1}")
    fl = set(RB.flooded(l1))
    hs = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640)
    hs = hs[hs.within(D)]
    inv = {k: v[0] for k, v in net["J"].items()}; inv.update({x[0]: x[1] for x in net["ST"]}); inv.update({o[0]: o[1] for o in net["OF"]})
    down = defaultdict(list)
    for co in net["CO"]:
        a1, b1, ln, o1, o2 = co[1], co[2], co[3], co[5], co[6]
        if a1 in inv and b1 in inv:
            if inv[a1] + o1 >= inv[b1] + o2:
                down[a1].append((b1, ln))
            else:
                down[b1].append((a1, ln))
    jn = [k for k in net["J"] if k in struct]; tr = cKDTree(np.array([net["coords"][k] for k in jn]))
    rows, skipped = [], []
    for h in hs.itertuples():
        units = []
        for c_, (sz, q) in RB.MOBILE.items():
            n = getattr(h, c_, None)
            units += [(sz, q)] * (int(n) if n == n and n else 0)
        if not units:
            units = [("6in", RB.MOBILE["planA_pump6"][1])] * int(getattr(h, "planA_pumps", 0) or 0)
        if not units:
            continue
        d0, i0 = tr.query((h.geometry.x, h.geometry.y)); suc = jn[i0]
        best, seen, pq = None, {suc: 0.0}, [(0.0, suc)]
        while pq:
            d, k = heapq.heappop(pq)
            if d > RB.MAX_PATH:
                break
            if k != suc and k not in fl and k in net["J"]:
                best = (k, d); break
            for b_, Ln in down.get(k, []):
                if d + Ln < seen.get(b_, 1e18):
                    seen[b_] = d + Ln; heapq.heappush(pq, (d + Ln, b_))
        if best is None:
            skipped.append({"hotspot": h.hotspot_id, "why": "2 km 管长内没有不溢流的下游井"}); continue
        dep = max(net["J"][suc][1], 0.5); on = 1.0 if dep >= 1.2 else round(0.8 * dep, 2); off = 0.3 if on > 0.4 else round(0.3 * on, 2)
        for j, (sz, q) in enumerate(units):
            pid = f"L2_{h.hotspot_id}_{j + 1}"
            net["PU"].append((pid, suc, best[0], f"PC_{pid}", "ON", on, off))
            net["CU"] += [(f"PC_{pid}", "PUMP2", 0.0, round(q, 4)), (f"PC_{pid}", "", 100.0, round(q, 4))]
            rows.append({"id": pid, "hotspot": h.hotspot_id, "size": sz, "from": suc, "suction_dist_m": round(float(d0)), "to": best[0], "path_m": round(best[1]), "q_m3s": round(q, 4)})
    reg["l2_plan_a"] = {"pumps": rows, "skipped": skipped, "receiver_basis": str(l1)}
    log(f"  L2 预案 A 移动泵：{len(rows)} 台（{len({r['hotspot'] for r in rows})} 个热点），跳过 {len(skipped)} 个热点")


def build(cfg, storm, state, smoke):
    import geopandas as gpd
    gov = ROOT / cfg["gov_dir"]
    c = {"dir": gov, "work": cfg.get("work", "D:/swbrain_work/exp_gov")}
    D = gpd.read_file(gov / "domain.geojson").to_crs(32640).geometry.iloc[0]
    name = f"RLF_{cfg['code']}_{storm}"; out = ROOT / "out/l2s_rl" / cfg["key"] / "model"; out.mkdir(parents=True, exist_ok=True)
    reg = {"name": name, "state": state, "region": cfg["key"], "design": "docs/L2S强化学习_第2a步_训练汇水区按F系列标准重建_设计_20261030.md",
           "domain_km2": round(D.area / 1e6, 3)}
    log(f"{name}_{state}（计算域 {D.area / 1e6:.2f} km²）")
    net, N, info = RG._net_g(c, D, log)
    info.pop("_phys", None); reg["network"] = {k: v for k, v in info.items() if not isinstance(v, (list, dict))}
    reg["outfalls_relinked"] = RG.fix_outfalls(net)
    X = json.loads((gov / "extract_gov.json").read_text(encoding="utf-8"))
    K = GB.kinds(X); struct = {k for k, v in K.items() if v[0] in ("Manhole Channel", "Catch Basin")}
    from shapely.geometry import Point
    from shapely.prepared import prep
    pD = prep(D)
    sealed = [k for k, j in net["J"].items() if k not in struct and j[3] < VB.SEAL_SUR and not pD.contains(Point(net["coords"][k]))]
    for k in sealed:
        net["J"][k][3] = VB.SEAL_SUR
    reg["sealed_outside_nonstructure"] = len(sealed)
    f_materials(net, X, D, reg); f_outfalls(net, reg)
    P = parsons(cfg["parsons"])
    f_pumps(net, X, P, reg)
    log(f"  泵：资产库 {len(reg['pumps'])} 台，建成 {sum(r['built'] for r in reg['pumps'])}（配上 Parsons {sum(1 for r in reg['pumps'] if r.get('matched'))}）；"
        f"排海口 {sum(r['sea'] for r in reg['outfalls'])} / {len(reg['outfalls'])}")
    t, exc, H, sim_h = inflows(P, net, struct, storm, D, reg)
    adds, mp, extra = None, {}, {}
    old = cfg.get("old_states") or {}
    if state != "L1":
        if state in (old.get("states") or []):                # 已认定的状态：旧模型相对 L1 新增的元素，按坐标改挂（同 RD-A 登记表 1-5c）
            GB.OLD = old["prefix"]
            adds, mp = GB.transfer(state, net, struct, reg)
        elif state == "L2":
            plan_a_l2(cfg, net, struct, D, reg)
        else:
            raise SystemExit(f"{cfg['key']} 没有已认定的 {state}（设计决定 ②）")
    inp = WORK / f"{name}_{state}.inp"
    RD.write_inp(inp, f"{cfg['key']} 治理数据版（F 系列标准）· {state} · {storm}（tools/rl_region_build.py）", net, H, t, sim_h, log=log)
    if adds:
        GB.inject(inp, adds)
        try:
            extra = GB.pilot_extra(state, mp, reg)
        except FileNotFoundError:
            extra = {}
    txt = inp.read_text(encoding="utf-8")
    for k, v in GB.ROUTING.items():
        txt = re.sub(rf"^{k}\s+\S+", f"{k}  {v}", txt, flags=re.M)
    txt, reg["init_fill"] = GB.init_fill(txt, GB.INIT_FILL)
    inp.write_text(txt, encoding="utf-8"); reg["routing"] = GB.ROUTING
    Nx, xt = GB.exchange_nodes(net, N, K, struct, D, reg)
    RB.write_meta(name, state, D, Nx, t, exc, {"note": f"{cfg['key']} F 系列标准（tools/rl_region_build.py）", "xchg_type": xt, "inlet": GB.INLET,
                                               "ground_basis": "cell", "node_min_depth": GB.node_min_depth(net), **extra})
    reg.update(inp=str(inp), sim_h=sim_h, n_junctions=len(net["J"]), n_conduits=len(net["CO"]), n_outfalls=len(net["OF"]), n_pumps=len(net["PU"]), n_storage=len(net["ST"]))
    if smoke:
        reg["smoke_1d"] = smoke_1d(inp)
        log(f"  一维冒烟：{reg['smoke_1d']}")
    (out / f"inputs_{storm}_{state}.json").write_text(json.dumps(reg, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    return reg


def smoke_1d(inp):
    """一维单跑（不耦合二维）：连续性误差、总溢流、耗时；拷到 $WORK/bench 下跑，原文件不动"""
    import shutil
    import time
    from swmm.toolkit import solver
    bd = WORK / "bench"; bd.mkdir(exist_ok=True)
    dst = bd / f"smoke_{inp.name}"; shutil.copy2(inp, dst)
    t0 = time.time(); solver.swmm_run(str(dst), str(dst.with_suffix(".rpt")), str(dst.with_suffix(".out"))); dt = time.time() - t0
    txt = dst.with_suffix(".rpt").read_text(encoding="utf-8", errors="ignore")
    ce = re.findall(r"Continuity Error \(%\) \.+\s+(-?[\d.]+)", txt)
    fl = re.search(r"Flooding Loss \.+\s+([\d.]+)\s+([\d.]+)", txt)
    errs = re.findall(r"ERROR \d+.*", txt)[:5]
    return {"wall_s": round(dt, 1), "continuity_flow_pct": float(ce[-1]) if ce else None, "flooding_10e3m3": float(fl.group(2)) if fl else None, "errors": errs}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True); ap.add_argument("--storm", default="M61D1", choices=list(STORMS))
    ap.add_argument("--state", default="L1", choices=["L1", "L2", "L2P"]); ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args(argv)
    cfg = json.loads((CFG_DIR / f"{a.region}.json").read_text(encoding="utf-8"))
    build(cfg, a.storm, a.state, a.smoke)


if __name__ == "__main__":
    main()
