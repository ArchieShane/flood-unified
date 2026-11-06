# -*- coding: utf-8 -*-
"""三个区域 · 汇水区计算域 · 二维 15 m 网格 · 9 场雨 × L1 / L2（2026-10-11）。

区域（推演计划表 v34「15m格网」第 73–126 行）：
  YAS  Yas bridges interchange（汇水区 RBE1-OF1，22.07 km²，热点 ADM-3 / 85 / 490 / 7）
  BAT  Al Bateen（汇水区 RD-A，3.21 km²，热点 ADM-76 / 75 / 377 / 379 / 380）
  BNY  Bani Yas（汇水区 RD 14，15.45 km²，热点 ADM-49）
情景名 <区域>_M<雨量>H<历时>，如 YAS_M33H1、BAT_M61H2、BNY_M96H24；雨量 33 / 61 / 96 mm × 历时 1 / 2 / 24 h，共 27 个。

用户 10-11 定的口径：
  计算域   客户汇水区图层（KMZ）里那一个多边形（cases/regions/<区域>_domain.geojson），不外扩、不闭运算；四周自由出流，域外地表来水不算
  一维管网 全市一维（客户 GDB 重建，build_city1d），触及计算域的连通块整块取（同 C1：域外部分只算一维，冒出的水记为损失）
  L1       既有管网 + 计算域内客户 GDB 的现状泵站（cases/regions/<区域>_existing_pumps.json，规则同 c1_existing_pumps.py；没有流量数据的泵不建、列为局限）
  L2       L1 + 已建成塘（cases/regions/<区域>_ponds.json；全市 L2 的接法：深 0.30 m 的平底蓄水节点，进水管带拍门，塘占地内的地面水直接进塘）
           YAS、BAT 计算域内没有已建成塘，L2 的模型同 L1，照算；BNY 有 1 座（Baniyas Lake）
  只做 L1 / L2，不做 L2+planned / L2+suggested
  产流     SCS CN 82、Tc 30 min（全市值，客户没给这三处的参数）；雨型取客户 Mussafah 模型里同历时的设计雨型、等比放大；模拟终止 = 历时 + 4 h
           净雨按全市管网的节点服务面积注入一维；离最近井超过服务半径的地面，净雨直接下在二维上（同 C1 参数 B8）
  二维     couple2way_v2，15 m（单元面积上限 75 m²），地形取 [terrain].dtm_path 原值，曼宁 0.030，交换步 60 s

文件前缀 RG_<情景>（如 RG_YAS_M33H1_L1.inp），交付到 out/交付/<区域目录>_模型版_<雨量>mm<历时>h/，不碰 C1 与 Mussafah。
不改 tools/ 里任何已有脚本（c1_batch.py 加了「runner 可以只做 L1/L2」两行，对别的 runner 没影响）。

用法（容器里 `python tools/rg_run_15m.py ...`；源码方式用项目 .venv 的 Python）：
  python tools/rg_run_15m.py check-patches
  python tools/rg_run_15m.py check --tag YAS_M33H1
  python tools/rg_run_15m.py plan  --tag YAS_M33H1
  python tools/rg_run_15m.py run   prep                       --tag YAS_M33H1
  python tools/rg_run_15m.py run   build:YAS_M33H1            --tag YAS_M33H1
  python tools/rg_run_15m.py run   compute:YAS_M33H1:L1,L2    --tag YAS_M33H1
  python tools/rg_run_15m.py run   post:YAS_M33H1 [--force]   --tag YAS_M33H1
多场雨一起排队用 c1_batch.py --runner rg_run_15m.py（服务器上用 tools/chains/rg_15m_server{2,3}.sh）。
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
from rg_regions import CASES, REGIONS  # noqa: E402

# ---- c1_batch.py 读这几个名字（缺省是 C1 的）
PREFIX = "RG"                  # 文件名前缀：RG_<情景>_<态>
CONTAINER = "rgpipe"           # 计算容器名：rgpipe_<情景>_<态>
STEP_LOG = "rg"                # 调度器的步骤日志：logs/rg_<步骤>.log
RES_M = 15
STATES3 = ["L1", "L2"]         # c1_batch：本 runner 只有这两个态
HAS_DESIGN = False             # c1_batch：没有 design / L2S3 步骤
STATES = STATES3
SNAME = {"L1": "L1", "L2": "L2"}
DIRS = {"L1": "L1_既有管网", "L2": "L2_已建成调蓄"}
RAINS = [(33, 1), (33, 2), (33, 24), (61, 1), (61, 2), (61, 24), (96, 1), (96, 2), (96, 24)]
SCEN = {f"{r}_M{mm}H{h}": (mm, h) for r in REGIONS for mm, h in RAINS}
CN, TC_MIN = 82.0, 30.0        # 全市值（build_city1d.CN / TC_MIN；这里只在 inputs.json 里写出来，实际取 B.CN / B.TC_MIN）
BATCH_PRE = {"desc": "三区域准备（雨型来源 Mussafah_baseline.inp、耦合程序、读入检查）", "steps": ["prep"],
             "outs": [P.WORK / "Mussafah_baseline.inp", P.C2D / "couple2way_v2.py", P.C2D / "npz_to_sww.py", P.C2D / "_chk_inp.py"]}


def post_out(t: str) -> pathlib.Path:
    return ROOT / "out" / f"rg_stats_{t}.json"


def name_of(t: str) -> str:
    return f"{PREFIX}_{t}"


def split_tag(t: str) -> tuple[str, str]:
    reg, scen = t.split("_", 1)
    return reg, scen


def end_h(t: str) -> float:
    return SCEN[t][1] + 4.0


def hotspots(reg: str) -> list[str]:
    return [f"ADM-{h}" for h in REGIONS[reg]["hs"]]


def domain_utm(reg: str):
    import geopandas as gpd
    return gpd.read_file(CASES / f"{reg}_domain.geojson").to_crs(32640).geometry.union_all()


def ponds_of(reg: str) -> list:
    return json.loads((CASES / f"{reg}_ponds.json").read_text(encoding="utf-8"))["ponds"]


# ------------------------------------------------------------------ 管网：从全市重建网截取（同 build_c1），按区域缓存
def network(reg: str, log=print) -> dict:
    cache = P.C2D / f"rg_net_{reg}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    import build_city1d as B
    import build_pilot as BP
    from shapely.geometry import Point
    from shapely.prepared import prep as sprep
    D = domain_utm(reg)
    PSJ = json.loads((CASES / f"{reg}_existing_pumps.json").read_text(encoding="utf-8"))["stations"]
    drop_uid = {u for s in PSJ if s.get("q_each_lps") and s.get("forcemain_unitids") for u in s["forcemain_unitids"]}
    df = B.read_pipes(None, log=lambda *x: None)
    xy_all, ground, inv, (vu, vd) = B.build_nodes(df, log=lambda *x: None)
    sc = df.StartCode.astype("string").fillna("").values
    ec = df.EndCode.astype("string").fillna("").values
    f = BP.components(sc, ec)
    comp = collections.defaultdict(list)
    for n in xy_all:
        comp[f(n)].append(n)
    pd0 = sprep(D)
    keep_roots = {r for r, ns in comp.items() if len(ns) >= 2 and any(pd0.contains(Point(xy_all[n])) for n in ns)}   # 触及计算域的连通块整块取
    keep = {n for r in keep_roots for n in comp[r]}
    xy = {n: xy_all[n] for n in xy_all if n in keep}
    outs = B.read_outfalls(set(xy), log=lambda *x: None)
    ln = df.LineLength.astype("float64").values
    dia = df.PIPE_DIAMETER.astype("float64").values / 1000.0
    uid = df.UNITID.astype("string").fillna("").values
    pipes, dropped = [], []
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
    n_in = sum(pd0.contains(Point(v)) for v in xy.values())
    log(f"  {reg} 连通块 {len(keep_roots)}、节点 {len(xy):,}（域内 {n_in:,}）、管 {len(pipes):,}、出水口 {len(outs)}；删掉换成泵的压力管 {dropped}")
    area = B.served_areas(xy, log=lambda *x: None)
    A = sum(area.values())
    R_served = B.K_RADIUS * math.sqrt(A / max(len(xy), 1))
    log(f"  管网服务面积 {A / 1e6:,.1f} km²，服务半径 {R_served:.1f} m")
    of_dia = {}
    for c in pipes:
        for nd in (c["n1"], c["n2"]):
            if nd in outs and c["d"] > of_dia.get(nd, 0.0):
                of_dia[nd] = c["d"]
    # 现状泵站（规则同 build_c1：湿井 = 吸水井，井底加深到「地面 − 井深」，固定流量 = 台数 × 单台，启停 = 井深 50% / 10%）
    pumps, junc_fix, new_out, limits = [], {}, [], []
    for s in PSJ:
        n_p, qe = s.get("n_pumps"), s.get("q_each_lps")
        if not n_p or not qe:
            limits.append(f"{s['ps_id']}（{s['unitid']}）：{s['q_src']}"); continue
        wet = s["wetwell_node"]
        if wet not in xy:
            limits.append(f"{s['ps_id']}：湿井 {wet} 不在截取的管网里，没建泵"); continue
        depth = float(s["well_depth_m"] or 0)
        if depth <= 0:
            limits.append(f"{s['ps_id']}：客户库没有井深，没建泵"); continue
        z_bot = min(inv[wet], ground[wet] - depth)
        junc_fix[wet] = z_bot
        dn, src = s.get("discharge_node"), s["discharge_src"]
        if dn and dn in xy and dn != wet and "压力管" in src:
            to, how = dn, src
        else:
            to = f"X_{s['unitid']}"; new_out.append((to, s["x"] + 5.0, s["y"] + 5.0, z_bot - 1.0))
            how = "抽出计算域（新建自由出水口）" + (f"；压力管末端 {dn} 不在截取的管网里" if dn and dn not in xy else "")
        pumps.append({"id": f"PS_{s['ps_id']}".replace(" ", "_"), "from": wet, "to": to, "q_m3s": n_p * qe / 1000.0, "on": 0.5 * depth, "off": 0.1 * depth,
                      "z_bot": z_bot, "inv_orig": inv[wet], "ground": ground[wet], "how": how, "ps": s["ps_id"], "unitid": s["unitid"],
                      "n": n_p, "q_each": qe, "q_src": s["q_src"]})
    # 塘的进水管：全市 L2 的接法（拍门短管，端点码同全市重建网）
    links, ponds = [], []
    for p in ponds_of(reg):
        w = p["wiring"]
        node = p["node"]
        for pi in w["pipes"]:
            k = pi["from"]
            if k not in xy:
                raise SystemExit(f"{reg}：塘 {p['unitid']} 的进水管起点 {k} 不在截取的管网里，先查")
            links.append({"id": f"PI_{p['unitid']}_{k}", "n1": k, "n2": node, "len": max(pi["len"], 10.0), "z1": inv[k],
                          "z2": max(w["z_bot"], min(inv[k], w["z_bot"] + w["depth_m"])), "d": max(w["dia_m"], 0.6), "uid": ""})
        ponds.append({"node": node, "unitid": p["unitid"], "z_floor": w["z_bot"], "depth": w["depth_m"], "curve": [(0.0, w["area_m2"]), (w["depth_m"], w["area_m2"])],
                      "vol": float(p["vol_model_m3"]), "xy": tuple(w["xy"]), "footprint": p["footprint"], "inflows": [pi["from"] for pi in w["pipes"]]})
    N = {"xy": xy, "ground": {k: ground[k] for k in xy}, "inv": {k: inv[k] for k in xy}, "pipes": pipes, "outs": outs, "area": area, "of_dia": of_dia,
         "n_components": len(keep_roots), "n_in": n_in, "dropped": dropped, "A": A, "R_served": R_served,
         "pumps": pumps, "junc_fix": junc_fix, "new_out": new_out, "limits": limits, "links": links, "ponds": ponds}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(N))
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
    for reg in REGIONS:
        for k in ("domain", "existing_pumps", "ponds"):
            f = CASES / f"{reg}_{k}.json" if k != "domain" else CASES / f"{reg}_domain.geojson"
            if not f.exists():
                raise SystemExit(f"缺 {f}：在开发机上跑 python tools/rg_regions.py 生成，随上传包带上")


# ------------------------------------------------------------------ 建模：L1 / L2 的 .inp、DEM、pilot
def write_dem(name: str, D):
    """从 [terrain].dtm_path 裁出计算域（外扩 50 m）→ c2d/dem_<名>.tif；返回 (形状, 分辨率)。"""
    import rasterio
    from rasterio.windows import from_bounds
    import datapaths
    X0, Y0, X1, Y1 = D.bounds
    with rasterio.open(datapaths.get("dtm")) as ds:
        win = from_bounds(X0 - 50, Y0 - 50, X1 + 50, Y1 + 50, ds.transform).round_offsets().round_lengths()
        z = ds.read(1, window=win)
        prof = ds.profile.copy(); prof.update(width=z.shape[1], height=z.shape[0], transform=ds.window_transform(win), compress="deflate")
        with rasterio.open(P.C2D / f"dem_{name}.tif", "w", **prof) as w:
            w.write(z, 1)
        return z.shape, ds.res[0]


def build(t: str, dry: bool) -> None:
    import numpy as np
    from shapely.geometry import Point
    from shapely.prepared import prep as sprep
    import build_city1d as B
    import runoff as R
    W, C = P.WORK, P.C2D
    reg, scen = split_tag(t); rain, dur = SCEN[t]; te = end_h(t); name = name_of(t)
    if dry:
        print(f"  （{name}：L1 / L2 的 .inp、dem_{name}.tif、pilot_{name}_<态>.json）"); return
    C.mkdir(parents=True, exist_ok=True)
    N = network(reg)
    D = domain_utm(reg); pd_ = sprep(D)
    B.P_MM = float(rain); B.DUR_H = float(dur); B.SIM_H = te
    dt_h = 1.0 / 60.0
    shape = R.read_shape(W / "Mussafah_baseline.inp", float(dur), log=lambda *a: None)
    tt, P_cum = R.hyetograph(float(rain), float(dur), dt_h, shape, 0.0, total_h=te + 1.0)
    exc = R.scs_cum_excess(P_cum, B.CN)
    q = np.convolve(np.diff(exc, append=exc[-1]), R.unit_hydrograph(B.TC_MIN, dt_h, 10_000.0), mode="full")[:len(tt)]
    B.unit_runoff = lambda: [(f"{int(round(h * 60)) // 60:02d}:{int(round(h * 60)) % 60:02d}", float(v)) for h, v in zip(tt, q)]
    print(f"{name}：{rain} mm / {dur} h，模拟 {te:g} h；净雨 {exc[-1]:.1f} mm（CN {B.CN:g}、Tc {B.TC_MIN:g} min）；计算域 {D.area / 1e6:.2f} km²")
    X0, Y0, X1, Y1 = D.bounds
    zshape, dtm_res = write_dem(name, D)
    poly = [[round(x, 1), round(y, 1)] for x, y in D.simplify(15.0).exterior.coords[:-1]]
    nodes = {k: [round(v[0], 2), round(v[1], 2)] for k, v in N["xy"].items() if pd_.contains(Point(v))}
    info = {"name": name, "region": reg, "title": REGIONS[reg]["title"], "rain_mm": rain, "dur_h": dur, "sim_h": te, "cn": B.CN, "tc_min": B.TC_MIN,
            "rain_excess_mm": round(float(exc[-1]), 2), "domain_km2": round(D.area / 1e6, 3), "domain_vertices": len(poly),
            "n_components": N["n_components"], "n_nodes": len(N["xy"]), "n_nodes_in_domain": len(nodes), "n_pipes": len(N["pipes"]), "n_outfalls": len(N["outs"]),
            "served_km2": round(N["A"] / 1e6, 2), "R_served_m": round(N["R_served"], 1), "mesh_res_m": RES_M, "max_tri_m2": RES_M * RES_M / 3.0,
            "n_tri_est": int(D.area / (0.65 * RES_M * RES_M / 3.0)), "dtm_res_m": dtm_res, "dem_px": [int(zshape[1]), int(zshape[0])],
            "pumps": N["pumps"], "pump_limits": N["limits"], "dropped_forcemains": N["dropped"],
            "ponds": [{"unitid": p["unitid"], "vol_model_m3": p["vol"], "inflows": p["inflows"]} for p in N["ponds"]],
            "manning_2d": 0.030, "dt_1d_s": 30, "dt_couple_s": 60, "frame_s": 300, "states": {}}
    for s in STATES3:
        out = W / f"{name}_{s}.inp"
        links = N["links"] if s == "L2" else []
        B.write_inp(out, N["xy"], N["ground"], N["inv"], N["pipes"] + links, N["outs"], N["area"], None, N["of_dia"], log=lambda *x: None)
        txt = out.read_text(encoding="utf-8").replace(f"THREADS  {B.SWMM_THREADS}", "THREADS  4", 1)
        txt = txt.replace(";; 全市一维管网（GDB 重建）", f";; {REGIONS[reg]['title']} 一维管网（全市 GDB 重建模型截取）· {SNAME[s]} · {rain} mm / {dur} h（tools/rg_run_15m.py）", 1)
        out.write_text(apply_pumps(txt, N), encoding="utf-8")
        if s == "L2":
            B.append_ponds(out, N["ponds"], links)
        ponds2d = [{"node": p["node"], "unitid": p["unitid"], "vol": p["vol"], "xy": list(p["xy"]), "footprint": p["footprint"]} for p in N["ponds"]] if s == "L2" else []
        meta = {"name": name, "state": s, "box": (X0, Y0, X1, Y1), "domain_polygon": poly, "R_served_m": N["R_served"], "nodes": nodes, "ponds": ponds2d,
                "rain_t_h": [round(float(x), 4) for x in tt], "rain_excess_mm": [round(float(x), 4) for x in exc], "pumps_1d": N["pumps"],
                "note": f"{REGIONS[reg]['title']} {SNAME[s]}：全市管网截取；现状泵站 {len(N['pumps'])} 座；" + (f"已建成塘 {len(ponds2d)} 座" if s == "L2" else "无塘")}
        (C / f"pilot_{name}_{s}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        info["states"][s] = {"inp_mb": round(out.stat().st_size / 1048576, 1), "n_ponds": len(ponds2d), "n_pumps": len(N["pumps"])}
        print(f"  {s:<3} → {out.name}（{out.stat().st_size / 1048576:.1f} MB）：泵 {len(N['pumps'])}、塘 {len(ponds2d)}")
    for lim in N["limits"]:
        print(f"  ! 局限：{lim}")
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / f"rg_inputs_{t}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  二维：节点（域内）{len(nodes):,}，DEM {zshape[1]}×{zshape[0]} @ {dtm_res:g} m，三角单元约 {info['n_tri_est']:,}（估）")


# ------------------------------------------------------------------ 步骤表：(id, 说明, 产物, 命令列表)
def chk(name, states):
    return P.dock("python", "/work/c2d/_chk_inp.py", *[f"{name}_{s}" for s in states], threads=1)


def compute_step(t, states):
    name, W, C = name_of(t), P.WORK, P.C2D
    thr = int(os.environ.get("C2D_G5_THREADS", "4"))

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
    rain, dur = SCEN[t]; reg, _ = split_tag(t)
    return {
        "prep": ("prep", "三区域准备（雨型来源、耦合程序、案例数据齐不齐）", BATCH_PRE["outs"], [prep]),
        "build": (f"build:{t}", f"{t}：建 L1 / L2（{rain} mm / {dur} h）",
                  [W / f"{name}_{s}.inp" for s in STATES3] + [C / f"pilot_{name}_{s}.json" for s in STATES3] + [C / f"dem_{name}.tif"],
                  [lambda dry: build(t, dry), chk(name, STATES3)]),
        "post": (f"post:{t}", f"{t}：后处理、GeoTIFF、.sww、交付目录（{rain} mm / {dur} h）", [post_out(t)],
                 [P.py("pilot_frames.py", "--name", name, "--states", ",".join(STATES), "--out", f"flood2d_rg_{t.lower()}",
                       "--region", f"{REGIONS[reg]['title']}（汇水区计算域）", "--scen", f"{rain}mm/{dur}h", "--stats-tag", t),
                  P.py("npz_to_tif.py", name, *STATES),
                  *[P.dock("python", "-u", "/work/c2d/npz_to_sww.py", name, s, RES_M, threads=4) for s in STATES],
                  P.py("rg_post.py", "--tag", t)]),
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
            raise SystemExit(f"不认识的情景 {k[1]}（共 {len(SCEN)} 个：<区域>_M<雨量>H<历时>）")
        return [steps_for(k[1])[k[0]]]
    if k[0] == "compute" and len(k) == 3:
        return [compute_step(k[1], k[2].split(","))]
    raise SystemExit(f"不认识的步骤：{spec}（见 plan）")


def done(outs):
    return all(pathlib.Path(o).exists() for o in outs)


def check_patches() -> int:
    bad = 0
    for f in ("pilot_frames.py", "npz_to_tif.py", "rg_post.py", "runoff.py", "build_city1d.py", "build_pilot.py", "strip_baseline.py", "couple2way_v2.py", "npz_to_sww.py", "chk_inp.py"):
        ok = (HERE / f).exists(); bad += not ok
        print(("  ✓ " if ok else "  ✗ ") + f"tools/{f}" + ("" if ok else " 不在"))
    for reg in REGIONS:
        for f in (f"{reg}_domain.geojson", f"{reg}_existing_pumps.json", f"{reg}_ponds.json"):
            ok = (CASES / f).exists(); bad += not ok
            print(("  ✓ " if ok else "  ✗ ") + f"cases/regions/{f}" + ("" if ok else " 不在"))
    print("文件都在（本 runner 不替换别的脚本的源码，没有替换点）" if not bad else f"有 {bad} 个文件不在")
    return 1 if bad else 0


def check(t) -> int:
    import datapaths
    ok = True
    print(f"{REGIONS[split_tag(t)[0]]['title']} · {t}（{SCEN[t][0]} mm / {SCEN[t][1]} h）· 工作目录 {P.WORK}")
    for lab, p in (("GDB", datapaths.get("gdb_dir")), ("DTM", datapaths.get("dtm")), ("雨型来源", P.WORK / "Mussafah_baseline.inp"),
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
