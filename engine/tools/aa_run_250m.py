# -*- coding: utf-8 -*-
"""Al Ain（艾因）· 二维 250 m 计算网格 · 9 场雨 × L1 / L2（2026-10-15）。

18 态：雨量 33 / 61 / 96 mm × 历时 1 / 2 / 24 h × L1 / L2，情景名 ALAIN_M<雨量>H<历时>（用户 10-15 定：雨量沿用全市 9 场，计算域外扩 1000 m 连成大片）。
管网数据：data/AAM_SW_Processed.gpkg（艾因新版库 2026-10-17 经 tools/aa_preprocess.py 处理，EPSG:32640）；泵站 / 塘：cases/alain/（tools/aa_cases.py 从完整版 GDB 抽出，服务器上不需要完整版）。
口径（沿用全市 250 m 版 tools/city_run_250m.py，艾因的数据差异见下）：
  计算域   管网节点按服务半径缓冲 → 外扩 1000 m 再内缩 800 m（把相邻管网连成片）→ 取最大一块、去洞；四周自由出流；其余小块的管网照样进一维，只是不进二维
  一维管网 艾因所有连通块（≥ 2 个节点）整网一个模型；**管内底基准 = 真实高程**（艾因台账管内底 − 0，不是全市那份的 DEM + 100；实测 管内底 − DTM 中位 −1.2 m）；
           只有 2.3% 的管有管内底，其余按埋深 1.61 m 从 5 m DTM 地面往下推（全市的补法）
  L1       既有管网 + 27 座现状泵站（PS_PUMP 台账流量，固定流量泵，启停 = 湿井深 50% / 10%；多数泵站台账里没有压力管，抽出的水按「抽出计算域」处理）
  L2       L1 + 已建成塘 1 座（POND：Sayh Al Saad，45,000 m³、深 6.8 m、塘底 194.0 m；把水排进塘的出水口在 L2 里改接进塘，塘是末端）
  产流     艾因没有客户子汇水区参数：全市统一 SCS CN 82、Tc 30 min（= 全市 v1 / 三区域口径）；雨型取客户 Mussafah 模型里同历时的设计雨型、等比放大
           离最近井超过服务半径的地面，净雨按 CN 82 直接下在二维上；模拟终止 = 历时 + 4 h
  二维     couple2way_v2，250 m（单元面积上限 250²/3 m²），交换步 60 s，曼宁 0.030
  地形     [terrain].dtm_path（5 m DTM）先按 250 m 取块平均，写成 c2d/dem_AA250.tif（共用）；dock 开了 C2D_DEM_RAW=1，所以 DEM 必须预先平均到 250 m
  未建模   692 座渗井（SOAKAWAY，443 座在管网上）、没有出水口的管网块（约 4 成节点）：水留在一维、满了冒出交给二维，没有渗透 / 外排
文件前缀 AA_<情景>（如 AA_ALAIN_M61H2_L1.inp）；计算容器 aapipe_*；交付 out/交付/艾因_模型版_<雨量>mm<历时>h_250m/。

用法（容器里 `python tools/aa_run_250m.py ...`）：
  python tools/aa_run_250m.py check-patches
  python tools/aa_run_250m.py check --tag ALAIN_M61H2
  python tools/aa_run_250m.py plan  --tag ALAIN_M61H2
  python tools/aa_run_250m.py run prep                          --tag ALAIN_M61H2
  python tools/aa_run_250m.py run build:ALAIN_M61H2             --tag ALAIN_M61H2
  python tools/aa_run_250m.py run compute:ALAIN_M61H2:L1,L2     --tag ALAIN_M61H2
  python tools/aa_run_250m.py run post:ALAIN_M61H2 [--force]    --tag ALAIN_M61H2
多场雨排队用 c1_batch.py --runner aa_run_250m.py（tools/chains/aa_250m_server.sh）。
艾因 GDB 路径：环境变量 AA_GDB（缺省 <仓库>/data/AAM_SW_Processed.gpkg）。
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import pathlib
import pickle
import re
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import c1_pipeline as P  # noqa: E402

# ---- c1_batch.py 读这几个名字
PREFIX = "AA"
CONTAINER = "aapipe"
STEP_LOG = "aa"
RES_M = 250
STATES3 = ["L1", "L2"]
HAS_DESIGN = False
STATES = STATES3
SNAME = {"L1": "L1", "L2": "L2"}
DIRS = {"L1": "L1_既有管网", "L2": "L2_已建成调蓄"}
RAINS = [(33, 1), (33, 2), (33, 24), (61, 1), (61, 2), (61, 24), (96, 1), (96, 2), (96, 24)]
SCEN = {f"ALAIN_M{mm}H{h}": (mm, h) for mm, h in RAINS}
GRID = "AA250"                 # 共用的 DEM / 网络缓存名
SOLVER_THREADS = 8
CASES = ROOT / "cases" / "alain"
GDB = pathlib.Path(os.environ.get("AA_GDB", ROOT / "data" / "AAM_SW_Processed.gpkg"))
DATUM_AA = 0.0                 # 艾因管内底 = 真实高程（全市是 DEM + 100）
DOMAIN_BUFFER_M, DOMAIN_CLOSE_M = 1000.0, 800.0
NETPKL = P.C2D / f"aa_net_{GRID}.pkl"
DEMTIF = P.C2D / f"dem_{GRID}.tif"
BATCH_PRE = {"desc": "艾因 250 m 准备（雨型来源、耦合程序、艾因一维网与计算域缓存、250 m DEM）", "steps": ["prep"],
             "outs": [P.WORK / "Mussafah_baseline.inp", P.C2D / "couple2way_v2.py", P.C2D / "npz_to_sww.py", P.C2D / "_chk_inp.py", NETPKL, DEMTIF]}


def post_out(t: str) -> pathlib.Path:
    return ROOT / "out" / f"aa_stats_{t}.json"


def name_of(t: str) -> str:
    return f"{PREFIX}_{t}"


def end_h(t: str) -> float:
    return SCEN[t][1] + 4.0


def deliv_dir(t: str) -> pathlib.Path:
    mm, h = SCEN[t]
    return ROOT / "out" / "交付" / f"艾因_模型版_{mm}mm{h}h_250m"


def _bind(B) -> None:
    B.GDB = str(GDB)
    B.DATUM = DATUM_AA


# ------------------------------------------------------------------ 艾因一维网 + 计算域 + 泵 + 塘（一次，缓存）
def network(log=print) -> dict:
    if NETPKL.exists():
        return pickle.loads(NETPKL.read_bytes())
    import numpy as np
    import build_city1d as B
    import build_pilot as BP
    from shapely.geometry import MultiPoint, Point, Polygon
    from shapely.prepared import prep as sprep
    _bind(B)
    PSJ = json.loads((CASES / "aa_existing_pumps.json").read_text(encoding="utf-8"))["stations"]
    PDS = json.loads((CASES / "aa_ponds.json").read_text(encoding="utf-8"))["ponds"]
    drop_uid = {u for s in PSJ if s.get("q_each_lps") and s.get("forcemain_unitids") for u in s["forcemain_unitids"]}
    df = B.read_pipes(None, log=lambda *x: None)
    xy_all, ground, inv, (vu, vd) = B.build_nodes(df, log=lambda *x: None)
    sc = df.StartCode.astype("string").fillna("").values
    ec = df.EndCode.astype("string").fillna("").values
    f = BP.components(sc, ec)
    comp = collections.defaultdict(list)
    for n in xy_all:
        comp[f(n)].append(n)
    keep_roots = {r for r, ns in comp.items() if len(ns) >= 2}
    keep = {n for r in keep_roots for n in comp[r]}
    xy = {n: xy_all[n] for n in xy_all if n in keep}
    outs = B.read_outfalls(set(xy), log=lambda *x: None)
    ln = df.LineLength.astype("float64").values
    dia = df.PIPE_DIAMETER.astype("float64").values / 1000.0
    pipes, dropped = [], []
    uid = df.UNITID.astype("string").fillna("").values
    for i in range(len(df)):
        n1, n2 = sc[i], ec[i]
        if n1 == n2 or n1 not in keep or n2 not in keep:
            continue
        if uid[i] in drop_uid:
            dropped.append(uid[i]); continue
        z1, z2 = float(vu[i]), float(vd[i])
        if z2 > z1:
            n1, n2, z1, z2 = n2, n1, z2, z1
        z1 = max(z1, inv[n1]); z2 = max(z2, inv[n2])
        pipes.append({"id": f"c_{i}", "n1": n1, "n2": n2, "len": float(ln[i]) if ln[i] > 0 else B.MIN_LEN,
                      "z1": z1, "z2": z2, "d": float(dia[i] if dia[i] > 0 else 0.3), "uid": ""})
    log(f"  艾因一维：连通块 {len(keep_roots)}、节点 {len(xy):,}、管 {len(pipes):,}、出水口 {len(outs)}；删掉换成泵的压力管 {len(dropped)} 根")
    area = B.served_areas(xy, log=lambda *x: None)
    A = sum(area.values())
    R_served = B.K_RADIUS * math.sqrt(A / max(len(xy), 1))
    log(f"  管网服务面积 {A / 1e6:,.1f} km²，服务半径 {R_served:.1f} m")
    of_dia = {}
    for c in pipes:
        for nd in (c["n1"], c["n2"]):
            if nd in outs and c["d"] > of_dia.get(nd, 0.0):
                of_dia[nd] = c["d"]
    # ---- 计算域：节点（25 m 栅格去重）缓冲服务半径 → 外扩 1000 m、内缩 800 m → 最大一块、去洞
    pts = np.unique(np.round(np.array(list(xy.values())) / 25.0).astype(np.int64), axis=0) * 25.0
    served = MultiPoint([tuple(p) for p in pts]).buffer(max(R_served, 30.0), 4)
    dom = served.buffer(DOMAIN_BUFFER_M).buffer(-DOMAIN_CLOSE_M).simplify(30.0)
    if dom.geom_type == "MultiPolygon":
        parts = sorted(dom.geoms, key=lambda g: -g.area)
        log(f"  计算域有 {len(parts)} 块，取最大一块 {parts[0].area / 1e6:,.1f} km²（其余合计 {sum(g.area for g in parts[1:]) / 1e6:,.1f} km²）")
        dom = parts[0]
    dom = Polygon(dom.exterior)
    ring = list(dom.exterior.coords)[:-1]
    if dom.exterior.is_ccw is False:
        ring = ring[::-1]
    poly = [[round(x, 1), round(y, 1)] for x, y in ring]
    pd_ = sprep(dom)
    nodes = {k: [round(v[0], 2), round(v[1], 2)] for k, v in xy.items() if pd_.contains(Point(v))}
    log(f"  计算域 {dom.area / 1e6:,.1f} km²（{len(poly)} 个顶点），域内节点 {len(nodes):,} / {len(xy):,}")
    # ---- 现状泵站（规则同 build_c1 / rg_run_15m：湿井 = 吸水井，井底加深到「地面 − 井深」，固定流量 = 台数 × 单台）
    pumps, junc_fix, new_out, limits = [], {}, [], []
    for s in PSJ:
        n_p, qe = s.get("n_pumps"), s.get("q_each_lps")
        if not n_p or not qe:
            limits.append(f"{s['ps_id']}：{s['q_src']}"); continue
        wet = s["wetwell_node"]
        if wet not in xy:
            limits.append(f"{s['ps_id']}：湿井 {wet} 不在管网里，没建泵"); continue
        depth = float(s["well_depth_m"])
        z_bot = min(inv[wet], ground[wet] - depth)
        junc_fix[wet] = z_bot
        dn, src = s.get("discharge_node"), s["discharge_src"]
        if dn and dn in xy and dn != wet and "压力管" in src:
            to, how = dn, src
        else:
            to = f"X_{s['ps_id']}"; new_out.append((to, s["x"] + 5.0, s["y"] + 5.0, z_bot - 1.0))
            how = "抽出计算域（台账没有压力管；新建自由出水口）"
        pumps.append({"id": f"PS_{s['ps_id']}", "from": wet, "to": to, "q_m3s": n_p * qe / 1000.0, "on": 0.5 * depth, "off": 0.1 * depth,
                      "z_bot": z_bot, "inv_orig": inv[wet], "ground": ground[wet], "how": how, "ps": s["ps_id"], "unitid": s["unitid"],
                      "n": n_p, "q_each": qe, "q_src": s["q_src"], "depth_note": s.get("depth_note", "")})
    # ---- 塘：进水 = 排进塘的出水口，L2 里该出水口不再自由出流
    links, ponds, pond_outs = [], [], set()
    for p in PDS:
        w = p["wiring"]; node = p["node"]
        for pi in w["pipes"]:
            k = pi["from"]
            if k not in xy:
                raise SystemExit(f"塘 {p['unitid']} 的进水节点 {k} 不在管网里，先查")
            if pi.get("is_outfall"):
                pond_outs.add(k)
            links.append({"id": f"PI_{p['unitid']}_{k}", "n1": k, "n2": node, "len": max(pi["len"], 10.0), "z1": inv[k],
                          "z2": max(w["z_bot"], min(inv[k], w["z_bot"] + w["depth_m"])), "d": max(w["dia_m"], of_dia.get(k, 0.6)), "uid": ""})
        ponds.append({"node": node, "unitid": p["unitid"], "z_floor": w["z_bot"], "depth": w["depth_m"], "curve": [(0.0, w["area_m2"]), (w["depth_m"], w["area_m2"])],
                      "vol": float(p["vol_model_m3"]), "xy": tuple(w["xy"]), "footprint": p["footprint"], "inflows": [pi["from"] for pi in w["pipes"]]})
    N = {"xy": xy, "ground": {k: ground[k] for k in xy}, "inv": {k: inv[k] for k in xy}, "pipes": pipes, "outs": outs, "area": area, "of_dia": of_dia,
         "n_components": len(keep_roots), "A": A, "R_served": R_served, "poly": poly, "bounds": dom.bounds, "domain_km2": dom.area / 1e6, "nodes": nodes,
         "pumps": pumps, "junc_fix": junc_fix, "new_out": new_out, "limits": limits, "dropped": dropped, "links": links, "ponds": ponds, "pond_outs": pond_outs}
    NETPKL.parent.mkdir(parents=True, exist_ok=True)
    NETPKL.write_bytes(pickle.dumps(N))
    return N


def apply_pumps(txt: str, N: dict) -> str:
    pumps, junc_fix, new_out = N["pumps"], N["junc_fix"], N["new_out"]
    lines = txt.split("\n"); i0 = lines.index("[JUNCTIONS]")
    for j in range(i0 + 1, len(lines)):
        if not lines[j].strip():
            break
        k = lines[j].split()[0]
        if k in junc_fix:
            lines[j] = f"{k}  {junc_fix[k]:.3f}  {max(N['ground'][k] - junc_fix[k], 0.5):.3f}  0  0  0"
    txt = "\n".join(lines)
    if not pumps:
        return txt
    if new_out:
        txt = txt.replace("[OUTFALLS]\n", "[OUTFALLS]\n" + "".join(f"{k}  {z:.3f}  FREE  NO\n" for k, x, y, z in new_out), 1)
        txt = txt.replace("[COORDINATES]\n", "[COORDINATES]\n" + "".join(f"{k}  {x:.2f}  {y:.2f}\n" for k, x, y, z in new_out), 1)
    sec = "[PUMPS]\n" + "".join(f"{p['id']}  {p['from']}  {p['to']}  PC_{p['id']}  ON  {p['on']:.2f}  {p['off']:.2f}\n" for p in pumps) + "\n"
    crv = "".join(f"PC_{p['id']}  PUMP2  0  {p['q_m3s']:.4f}\nPC_{p['id']}  100  {p['q_m3s']:.4f}\n" for p in pumps)
    txt = txt.replace("[XSECTIONS]", sec + "[XSECTIONS]", 1)
    return re.sub(r"\[CURVES\]\n", "[CURVES]\n" + crv, txt, count=1) if "[CURVES]" in txt else txt.replace("[TIMESERIES]", "[CURVES]\n" + crv + "\n[TIMESERIES]", 1)


def write_dem(N: dict) -> None:
    """5 m DTM → 计算域包络内按 250 m 取块平均 → c2d/dem_AA250.tif。"""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds
    from affine import Affine
    import datapaths
    X0, Y0, X1, Y1 = N["bounds"]
    with rasterio.open(datapaths.get("dtm")) as ds:
        win = from_bounds(X0 - 50, Y0 - 50, X1 + 50, Y1 + 50, ds.transform).round_offsets().round_lengths()
        f = RES_M / ds.res[0]
        Hh, Ww = max(int(win.height / f), 1), max(int(win.width / f), 1)
        print(f"  DTM {ds.res[0]:g} m：窗口 {win.width}×{win.height} 像素 → 取平均成 {Ww}×{Hh} 格 @ {RES_M} m", flush=True)
        z = ds.read(1, window=win, out_shape=(Hh, Ww), resampling=Resampling.average).astype("float32")
        tr = ds.window_transform(win) * Affine.scale(win.width / Ww, win.height / Hh)
        prof = ds.profile.copy(); prof.update(width=Ww, height=Hh, transform=tr, compress="deflate", dtype="float32", tiled=False)
        prof.pop("blockxsize", None); prof.pop("blockysize", None)
        with rasterio.open(DEMTIF, "w", **prof) as w:
            w.write(z, 1)


# ------------------------------------------------------------------ 准备
def prep(dry: bool) -> None:
    W, C = P.WORK, P.C2D
    if not (W / "Mussafah_baseline.inp").exists():
        import datapaths
        if not (W / "Mussafah.inp").exists():
            s = datapaths.get("client_swmm_export") / "Mussafah_00.inp"
            print(f"  cp {s} → {W / 'Mussafah.inp'}")
            if not dry:
                shutil.copy2(s, W / "Mussafah.inp")
        print("  strip_baseline.py：Mussafah.inp → Mussafah_baseline.inp（雨型来源）")
        if not dry:
            subprocess.run(P.py("strip_baseline.py"), check=True, cwd=ROOT, env=P.env())
    for s, d in ((HERE / "couple2way_v2.py", C / "couple2way_v2.py"), (HERE / "npz_to_sww.py", C / "npz_to_sww.py"), (HERE / "chk_inp.py", C / "_chk_inp.py")):
        if not d.exists():
            print(f"  cp {s} → {d}")
            if not dry:
                d.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(s, d)
    for k in ("aa_existing_pumps.json", "aa_ponds.json"):
        if not (CASES / k).exists():
            raise SystemExit(f"缺 {CASES / k}：在开发机上跑 python tools/aa_cases.py 生成，随上传包带上")
    if not NETPKL.exists() or not DEMTIF.exists():
        print("  艾因一维网 + 计算域 + 泵 + 塘（读 GDB）、250 m DEM")
        if not dry:
            C.mkdir(parents=True, exist_ok=True)
            N = network()
            if not DEMTIF.exists():
                write_dem(N)


def build(t: str, dry: bool) -> None:
    import numpy as np
    import build_city1d as B
    import runoff as R
    W, C = P.WORK, P.C2D
    rain, dur = SCEN[t]; te = end_h(t); name = name_of(t)
    if dry:
        print(f"  （{name}：L1 / L2 的 .inp、pilot_{name}_<态>.json；DEM 与网络用 prep 的缓存）"); return
    _bind(B)
    N = network()
    B.P_MM = float(rain); B.DUR_H = float(dur); B.SIM_H = te
    dt_h = 1.0 / 60.0
    shape = R.read_shape(W / "Mussafah_baseline.inp", float(dur), log=lambda *a: None)
    tt, P_cum = R.hyetograph(float(rain), float(dur), dt_h, shape, 0.0, total_h=te + 1.0)
    exc = R.scs_cum_excess(P_cum, B.CN)
    q = np.convolve(np.diff(exc, append=exc[-1]), R.unit_hydrograph(B.TC_MIN, dt_h, 10_000.0), mode="full")[:len(tt)]
    B.unit_runoff = lambda: [(f"{int(round(h * 60)) // 60:02d}:{int(round(h * 60)) % 60:02d}", float(v)) for h, v in zip(tt, q)]
    print(f"{name}：{rain} mm / {dur} h，模拟 {te:g} h；净雨 {exc[-1]:.1f} mm（CN {B.CN:g}、Tc {B.TC_MIN:g} min）；计算域 {N['domain_km2']:,.1f} km²")
    X0, Y0, X1, Y1 = N["bounds"]
    info = {"name": name, "rain_mm": rain, "dur_h": dur, "sim_h": te, "runoff": "v1", "cn": B.CN, "tc_min": B.TC_MIN, "rain_excess_mm": round(float(exc[-1]), 2),
            "domain_km2": round(N["domain_km2"], 2), "n_components": N["n_components"], "n_nodes": len(N["xy"]), "n_nodes_in_domain": len(N["nodes"]),
            "n_pipes": len(N["pipes"]), "n_outfalls": len(N["outs"]), "served_km2": round(N["A"] / 1e6, 2), "R_served_m": round(N["R_served"], 1),
            "mesh_res_m": RES_M, "max_tri_m2": RES_M * RES_M / 3.0, "n_tri_est": int(N["domain_km2"] * 1e6 / (0.65 * RES_M * RES_M / 3.0)),
            "pumps": N["pumps"], "pump_limits": N["limits"], "dropped_forcemains": N["dropped"],
            "ponds": [{"unitid": p["unitid"], "vol_model_m3": p["vol"], "inflows": p["inflows"]} for p in N["ponds"]],
            "manning_2d": 0.030, "dt_1d_s": 30, "dt_couple_s": 60, "frame_s": 300, "states": {}}
    for s in STATES3:
        out = W / f"{name}_{s}.inp"
        links = N["links"] if s == "L2" else []
        # 2026-10-18 用户定：排进塘的那根管（客户出水口 OFL_400，落在 Sayh Al Saad 塘上）在 L1 里**不是出水口**——塘是已建成的，水现实中进塘、不会凭空消失，
        # L1 没有塘，所以这段管网末端封闭、水憋在管网里（从井冒出）；L2 加上塘，水进塘，冒水自然比 L1 少。L1 / L2 都去掉这些自由出水口
        outs = N["outs"] - N["pond_outs"]
        B.write_inp(out, N["xy"], N["ground"], N["inv"], N["pipes"] + links, outs, N["area"], None, N["of_dia"], log=lambda *x: None)
        txt = out.read_text(encoding="utf-8").replace(f"THREADS  {B.SWMM_THREADS}", f"THREADS  {SOLVER_THREADS}", 1)
        txt = txt.replace(";; 全市一维管网（GDB 重建）", f";; 艾因一维管网（GDB 重建）· {SNAME[s]} · {rain} mm / {dur} h（tools/aa_run_250m.py）", 1)
        out.write_text(apply_pumps(txt, N), encoding="utf-8")
        if s == "L2":
            B.append_ponds(out, N["ponds"], links)
        ponds2d = [{"node": p["node"], "unitid": p["unitid"], "vol": p["vol"], "xy": list(p["xy"]), "footprint": p["footprint"]} for p in N["ponds"]] if s == "L2" else []
        meta = {"name": name, "state": s, "box": (X0 - 50, Y0 - 50, X1 + 50, Y1 + 50), "domain_polygon": N["poly"], "R_served_m": N["R_served"], "nodes": N["nodes"], "ponds": ponds2d,
                "rain_t_h": [round(float(x), 4) for x in tt], "rain_excess_mm": [round(float(x), 4) for x in exc], "pumps_1d": N["pumps"],
                "note": f"艾因 250 m {SNAME[s]}：艾因一维管网（GDB 重建）；现状泵站 {len(N['pumps'])} 座" + (f"；已建成塘 {len(ponds2d)} 座" if s == "L2" else "；无塘")}
        (C / f"pilot_{name}_{s}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        info["states"][s] = {"inp_mb": round(out.stat().st_size / 1048576, 1), "n_ponds": len(ponds2d), "n_pumps": len(N["pumps"])}
        print(f"  {s:<3} → {out.name}（{out.stat().st_size / 1048576:.1f} MB）：泵 {len(N['pumps'])}、塘 {len(ponds2d)}")
    for lim in N["limits"]:
        print(f"  ! 局限：{lim}")
    d = C / f"dem_{name}.tif"
    if not d.exists():
        try:
            os.link(DEMTIF, d)
        except OSError:
            shutil.copy2(DEMTIF, d)
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / f"aa_inputs_{t}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ 步骤表
def chk(name, states):
    return P.dock("python", "/work/c2d/_chk_inp.py", *[f"{name}_{s}" for s in states], threads=1)


def compute_step(t, states):
    name, C = name_of(t), P.C2D
    thr = int(os.environ.get("C2D_G5_THREADS", str(SOLVER_THREADS)))

    def f(dry):
        (C / "logs").mkdir(parents=True, exist_ok=True)
        names = []
        for s in states:
            n = f"{CONTAINER}_{t}_{s}"
            cmd = P.dock("sh", "-c", f"python -u /work/c2d/couple2way_v2.py {name} {s} {RES_M} {end_h(t):g} 60 > /work/c2d/logs/{name}_{s}.log 2>&1",
                         name=n, threads=thr, detach=True)
            print("  " + " ".join(cmd))
            if not dry:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL); names.append(n)
        while names and not dry:
            time.sleep(60)
            live = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
            for s in states:
                lg = C / "logs" / f"{name}_{s}.log"
                tail = lg.read_text(encoding="utf-8", errors="replace").replace("\r", "\n").strip().splitlines()[-1:] if lg.exists() else ["(还没有日志)"]
                print(f"  [{time.strftime('%H:%M')}] {s}: {tail[0][:110] if tail else ''}")
            names = [n for n in names if n in live]
        bad = [s for s in states if not (C / f"twoway_{name}_{s}.json").exists()]
        if bad and not dry:
            raise SystemExit(f"  ! {name} {bad} 容器已退出但没有结果，看 {C / 'logs'}")
    return (f"compute:{t}:{','.join(states)}", f"{t}：一二维耦合计算 {' / '.join(states)}（{RES_M} m 网格，{thr} 线程，阻塞到算完）",
            [C / f"twoway_{name}_{s}.json" for s in states] + [C / f"{name}_{s}_2d.npz" for s in states], [f])


def steps_for(t):
    name, W, C = name_of(t), P.WORK, P.C2D
    rain, dur = SCEN[t]
    return {
        "prep": ("prep", "艾因 250 m 准备（雨型来源、耦合程序、艾因网缓存、250 m DEM）", BATCH_PRE["outs"], [prep]),
        "build": (f"build:{t}", f"{t}：建 L1 / L2（{rain} mm / {dur} h）",
                  [W / f"{name}_{s}.inp" for s in STATES3] + [C / f"pilot_{name}_{s}.json" for s in STATES3] + [C / f"dem_{name}.tif"],
                  [lambda dry: build(t, dry), chk(name, STATES3)]),
        "post": (f"post:{t}", f"{t}：后处理、GeoTIFF、.sww、交付目录（{rain} mm / {dur} h）", [post_out(t)],
                 [P.py("pilot_frames.py", "--name", name, "--states", ",".join(STATES), "--out", f"flood2d_aa_{t.lower()}g{RES_M}",
                       "--region", "Al Ain 艾因（250 m 网格）", "--scen", f"{rain}mm/{dur}h", "--stats-tag", t),
                  P.py("npz_to_tif.py", name, *STATES),
                  *[P.dock("python", "-u", "/work/c2d/npz_to_sww.py", name, s, RES_M, threads=4) for s in STATES],
                  P.py("aa_post.py", "--tag", t)]),
    }


def all_steps(t):
    s = steps_for(t)
    return [s["prep"], s["build"], compute_step(t, STATES3), s["post"]]


def parse_spec(spec, t):
    k = spec.split(":")
    if k[0] == "prep":
        return [steps_for(t)["prep"]]
    if k[0] in ("build", "post") and len(k) == 2:
        if k[1] not in SCEN:
            raise SystemExit(f"不认识的情景 {k[1]}（共 {len(SCEN)} 个：ALAIN_M<雨量>H<历时>）")
        return [steps_for(k[1])[k[0]]]
    if k[0] == "compute" and len(k) == 3:
        return [compute_step(k[1], k[2].split(","))]
    raise SystemExit(f"不认识的步骤：{spec}（见 plan）")


def done(outs):
    return all(pathlib.Path(o).exists() for o in outs)


def check_patches() -> int:
    bad = 0
    for f in ("pilot_frames.py", "npz_to_tif.py", "aa_post.py", "city2d_post.py", "runoff.py", "build_city1d.py", "build_pilot.py", "strip_baseline.py", "couple2way_v2.py", "npz_to_sww.py", "chk_inp.py"):
        ok = (HERE / f).exists(); bad += not ok
        print(("  ✓ " if ok else "  ✗ ") + f"tools/{f}" + ("" if ok else " 不在"))
    for f in (CASES / "aa_existing_pumps.json", CASES / "aa_ponds.json", GDB):
        ok = f.exists(); bad += not ok
        print(("  ✓ " if ok else "  ✗ ") + str(f) + ("" if ok else " 不在"))
    print("文件都在" if not bad else f"有 {bad} 个文件不在")
    return 1 if bad else 0


def check(t) -> int:
    import datapaths
    ok = True
    print(f"艾因 250 m · {t}（{SCEN[t][0]} mm / {SCEN[t][1]} h）· 产流 v1（CN 82 / Tc 30 min）· 工作目录 {P.WORK}")
    for lab, p in (("艾因GDB", GDB), ("DTM", datapaths.get("dtm")), ("雨型来源", P.WORK / "Mussafah_baseline.inp"),
                   ("耦合程序", P.C2D / "couple2way_v2.py"), ("读入检查", P.C2D / "_chk_inp.py"), (".sww 转换", P.C2D / "npz_to_sww.py")):
        e = bool(p) and pathlib.Path(p).exists(); ok &= e
        print(f"  {'✓' if e else '✗'} {lab:<8} {p}")
    try:
        img = subprocess.run(["docker", "image", "inspect", P.IMAGE], capture_output=True, timeout=30).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        img = False
    ok &= img
    print(f"  {'✓' if img else '✗'} 镜像     {P.IMAGE}")
    print("步骤（✓ = 产物已在）")
    for sid, desc, outs, _ in all_steps(t):
        miss = [str(o) for o in outs if not pathlib.Path(o).exists()]
        print(f"  {'·' if miss else '✓'} {sid:<30} {desc}" + (f"\n{'':36}缺 {len(miss)} 个，如 {miss[0]}" if miss else ""))
    print("\n数据与环境齐了。" if ok else "\n有 ✗ 项：先补齐。")
    return 0 if ok else 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:2] == ["check-patches"]:
        return check_patches()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["check", "plan", "run"])
    ap.add_argument("steps", nargs="*")
    ap.add_argument("--tag", required=True, choices=list(SCEN))
    ap.add_argument("--force", action="store_true", help="产物已在也重做")
    ap.add_argument("--dry", action="store_true", help="只打印命令")
    a = ap.parse_args()
    if a.cmd == "check":
        return check(a.tag)
    if a.cmd == "run" and not a.dry:
        for d in (ROOT / "out", ROOT / "console" / "data", P.C2D / "logs"):
            d.mkdir(parents=True, exist_ok=True)
    steps = all_steps(a.tag) if a.cmd == "plan" or a.steps == ["all"] else [s for sp in a.steps for s in parse_spec(sp, a.tag)]
    for sid, desc, outs, cmds in steps:
        if a.cmd == "plan":
            print(f"\n[{'✓' if done(outs) else ' '}] {sid} — {desc}")
            for c in cmds:
                print("      " + ("（内置步骤）" if callable(c) else " ".join(map(str, c))))
            continue
        if done(outs) and not a.force:
            print(f"= {sid}：产物已在，跳过（--force 重做）"); continue
        print(f"\n▶ {sid} — {desc}", flush=True)
        for c in cmds:
            if callable(c):
                c(a.dry); continue
            print("  $ " + " ".join(map(str, c)), flush=True)
            if not a.dry:
                r = subprocess.run(c, cwd=ROOT, env=P.env())
                if r.returncode:
                    raise SystemExit(f"  ! 失败（退出码 {r.returncode}）：{sid}")
        if not a.dry and not done(outs):
            raise SystemExit(f"  ! {sid} 跑完了但产物不全：{[str(o) for o in outs if not pathlib.Path(o).exists()]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
