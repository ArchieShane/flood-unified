# -*- coding: utf-8 -*-
"""ADM 全市 · 二维 100 m 网格 · 61 mm / 1 h · L1 / L2 · 治理后数据：一维网络全部来自 Z:\flood\全市治理后数据_20261025（2026-10-26）。

和 adm100v2_run.py（10-21 版，合并结果 + 只算一态）的区别（用户 10-26 定）：
  数据     一维管网、节点、出水口、泵、塘、雨水口、渗井全部来自治理后 GDB（开发机上 tools/adm100a_net.py 一次性重建成 cases/ad100a/af_net_CITYAFTER.pkl，服务器不读 GDB）；
           DTM 用服务器上的（二维地形，并在 build 时用服务器 DTM 重取节点地面）；雨型 = 客户 Mussafah 模型的设计雨型；计算域 = 全市 1,233.6 km²（同之前）
  汇水区   数据里没有，产流仍按全市 SCS CN 82 / Tc 30 min 注入节点服务面积，无管网覆盖处净雨直接下在二维
  态       L1 = 既有管网 + 有流量的泵（无塘、无渗井）；L2 = L1 + 全部塘 + 渗井。两态管网 / 泵相同；不看 LEVEL 字段，Proposed 照用（同 10-21 版口径）
  泵       泵机组表 + 泵曲线表（曲线半扬程处流量，按机组加总），无曲线时用台账 FLOW_RATE（≥10 L/s）；其余列入局限清单；可移动泵不参与
  塘 / 渗井 / 雨水口  同 10-21 版
  命名     交付目录和文件名全英文（不含中文），成果目录 ADM_City_61mm1h_100m_after；内部态名 L1 / L2

文件前缀 AF_<情景>（如 AF_CITYAFTER_M61H1_L1.inp），计算容器 afpipe_*，和别的批次互不干扰。情景名 CITYAFTER_M<雨量>H<历时>。

用法（容器里 `python tools/adm100a_run.py ...`）：
  python tools/adm100a_run.py check-patches
  python tools/adm100a_run.py check   --tag CITYAFTER_M61H1
  python tools/adm100a_run.py plan    --tag CITYAFTER_M61H1
  python tools/adm100a_run.py run prep                      --tag CITYAFTER_M61H1
  python tools/adm100a_run.py run build:CITYAFTER_M61H1          --tag CITYAFTER_M61H1
  python tools/adm100a_run.py run compute:CITYAFTER_M61H1:L1     --tag CITYAFTER_M61H1
  python tools/adm100a_run.py run post:CITYAFTER_M61H1 [--force] --tag CITYAFTER_M61H1
多场一起排队用 c1_batch.py --runner adm100a_run.py（服务器上用 adm_100m_after_server.sh）。
"""
from __future__ import annotations

import argparse
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
CASES = ROOT / "cases" / "ad100a"        # 容器里 /app/cases/ad100a（服务器脚本 docker cp 进去）
_HS = CASES / "hotspots_ad.json"
if not _HS.exists():
    raise SystemExit(f"缺 {_HS}：随上传包带上 cases/ad100a/")
REGIONS = json.loads(_HS.read_text(encoding="utf-8"))["regions"]      # {"CITYAFTER": {title, feat, dir, km2, res, rains, ...}}

# ---- c1_batch.py 读这几个名字（缺省是 C1 的）
PREFIX = "AF"                  # 文件名前缀：AF_<情景>_<态>
CONTAINER = "afpipe"           # 计算容器名：afpipe_<情景>_<态>
STEP_LOG = "af"                # 调度器的步骤日志：logs/ah_<步骤>.log
RES_M = 100
GRID = "AF100"                 # 全市共用的 DEM 缓存名
STATES3 = ["L1", "L2"]         # c1_batch：L1 = 既有管网 + 泵（无塘），L2 = L1 + 塘 / 渗井
HAS_DESIGN = False             # c1_batch：没有 design / L2S3 步骤
STATES = STATES3
SNAME = {"L1": "L1", "L2": "L2"}
DIRS = {"L1": "L1_existing_facilities", "L2": "L2_with_ponds"}
RAINS = [(61, 1)]      # 只算 61 mm / 1 h
SCEN = {f"{r}_M{mm}H{h}": (mm, h) for r in REGIONS for mm, h in (REGIONS[r].get("rains") or RAINS)}
CN, TC_MIN = 82.0, 30.0        # 全市值（实际取 B.CN / B.TC_MIN）
# 成果直接写 NAS：$AD_RESULT_ROOT/$AD_HOST/<区域目录>_<雨量>mm<历时>h_100m_after/；NAS 的宿主机路径也挂进 post 的容器（同路径）
RESULT_ROOT = os.environ.get("AD_RESULT_ROOT", "/media/pop-fs/flood_data/result")
AD_HOST = os.environ.get("AD_HOST", "") or "unknown-host"          # 服务器脚本 docker exec -e AD_HOST=$(hostname -s)
NAS_BASE = str(pathlib.PurePosixPath(RESULT_ROOT).parent)
BATCH_PRE = {"desc": "ADM city 100 m (post-improvement data) prep (design storm source, coupling program, post-processing script, case data, network, 100 m DEM)", "steps": ["prep"],
             "outs": [P.WORK / "Mussafah_baseline.inp", P.C2D / "couple2way_v3.py", P.C2D / "npz_to_sww.py", P.C2D / "_chk_inp.py", P.C2D / "adm100a_post.py",
                      P.C2D / "ad100a_cases" / "hotspots_ad.json", P.C2D / "ad100a_cases" / "af_net_CITYAFTER.pkl", P.C2D / f"dem_{GRID}.tif"]}


def post_out(t: str) -> pathlib.Path:
    return P.C2D / f"af_stats_{t}.json"          # 后处理在 anuga-swmm 容器里跑，只看得到 /work，标记文件放这里


def res_of(reg: str) -> int:
    return int(REGIONS[reg].get("res", RES_M))


def name_of(t: str) -> str:
    return f"{PREFIX}_{t}"


def split_tag(t: str) -> tuple[str, str]:
    reg, scen = t.split("_", 1)
    return reg, scen


def end_h(t: str) -> float:
    return SCEN[t][1] + 4.0


def domain_utm(reg: str):
    import geopandas as gpd
    return gpd.read_file(CASES / f"{reg}_domain.geojson").to_crs(32640).geometry.union_all()


# ------------------------------------------------------------------ 管网：开发机上从治理后 GDB 重建好的缓存（tools/adm100a_net.py）
def network(reg: str, log=print) -> dict:
    f = CASES / f"af_net_{reg}.pkl"
    if not f.exists():
        raise SystemExit(f"缺 {f}：在开发机上跑 .venv/Scripts/python.exe tools/adm100a_net.py --out upload/adm_100m_after/cases/ad100a，随上传包带上")
    return pickle.loads(f.read_bytes())


def resample_ground(N: dict) -> int:
    """用服务器上的 DTM 重取节点地面（pkl 里的地面是开发机 DTM 取的）；取不到 / 离管内底太近的保留原值。"""
    import numpy as np
    import rasterio
    import datapaths
    names = list(N["xy"])
    pts = [tuple(N["xy"][k]) for k in names]
    with rasterio.open(datapaths.get("dtm")) as ds:
        z = np.array([v[0] for v in ds.sample(pts)], float)
    n = 0
    for k, g in zip(names, z):
        if np.isfinite(g) and g > -100 and g - N["inv"][k] >= 0.30 and abs(g - N["ground"][k]) > 1e-6:
            N["ground"][k] = float(g); n += 1
    return n


def apply_pumps(txt: str, N: dict) -> str:
    pumps, junc_fix, new_out = N["pumps"], N["junc_fix"], N["new_out"]
    if N.get("dummy_out"):
        k, x, y, z = N["dummy_out"]
        txt = txt.replace("[OUTFALLS]\n", f"[OUTFALLS]\n{k}  {z:.3f}  FREE  NO\n", 1).replace("[COORDINATES]\n", f"[COORDINATES]\n{k}  {x:.2f}  {y:.2f}\n", 1)
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
    for s, d in ((HERE / "couple2way_v3.py", C / "couple2way_v3.py"), (HERE / "npz_to_sww.py", C / "npz_to_sww.py"), (HERE / "chk_inp.py", C / "_chk_inp.py"),
                 (HERE / "adm100a_post.py", C / "adm100a_post.py")):
        if not d.exists() or d.name in ("adm100a_post.py", "couple2way_v3.py"):        # 这两个每次覆盖，改了脚本不用手工删
            print(f"  cp {s} → {d}")
            if not dry:
                d.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(s, d)
    for reg in REGIONS:
        for f in (f"{reg}_domain.geojson", f"{reg}_ponds.json", f"{reg}_pumps.json", f"af_net_{reg}.pkl", "hotspots_ad.json"):
            if not (CASES / f).exists():
                raise SystemExit(f"缺 {CASES / f}：随上传包带上 cases/ad100a/")
    if not dry:                                                 # 后处理要的案例数据（计算域 / 泵 / 塘 / 积水点 / 核对表）放进 /work/c2d/ad100a_cases/
        (C / "ad100a_cases").mkdir(parents=True, exist_ok=True)
        for f in CASES.glob("*"):
            shutil.copy2(f, C / "ad100a_cases" / f.name)
    if not dry and not (C / f"dem_{GRID}.tif").exists():
        print("  100 m DEM（读 DTM，取块平均；之后缓存 dem_AF100.tif）", flush=True)
        C.mkdir(parents=True, exist_ok=True)
        write_dem(GRID, domain_utm("CITYAFTER"), RES_M)


# ------------------------------------------------------------------ 建模：L1 的 .inp、DEM、pilot
def write_dem(name: str, D, RES_M: float):
    """DTM → 计算域外扩 50 m 的包络内按 RES_M 取块平均 → c2d/dem_<名>.tif；返回 (形状, 原 DTM 分辨率)。"""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds
    from affine import Affine
    import datapaths
    X0, Y0, X1, Y1 = D.bounds
    with rasterio.open(datapaths.get("dtm")) as ds:
        win = from_bounds(X0 - 50, Y0 - 50, X1 + 50, Y1 + 50, ds.transform).round_offsets().round_lengths()
        f = RES_M / ds.res[0]
        Hh, Ww = max(int(win.height / f), 1), max(int(win.width / f), 1)
        print(f"  DTM {ds.res[0]:g} m：窗口 {win.width}×{win.height} 像素 → 取平均成 {Ww}×{Hh} 格 @ {RES_M} m", flush=True)
        z = ds.read(1, window=win, out_shape=(Hh, Ww), resampling=Resampling.average).astype("float32")
        tr = ds.window_transform(win) * Affine.scale(win.width / Ww, win.height / Hh)
        prof = ds.profile.copy(); prof.update(width=Ww, height=Hh, transform=tr, compress="deflate", dtype="float32", tiled=False)
        prof.pop("blockxsize", None); prof.pop("blockysize", None)
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
        print(f"  （{name}：L1 的 .inp、dem_{name}.tif、pilot_{name}_<态>.json）"); return
    C.mkdir(parents=True, exist_ok=True)
    N = network(reg)
    print(f"  节点地面：用服务器 DTM 重取，{resample_ground(N):,} 个节点与 pkl 里的值不同（其余保留）")
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
    RES_M = res_of(reg)
    import rasterio
    shared = C / f"dem_{GRID}.tif"
    if not shared.exists():
        write_dem(GRID, D, RES_M)
    with rasterio.open(shared) as ds:
        zshape = (ds.height, ds.width)
    d = C / f"dem_{name}.tif"                       # 耦合程序读 c2d/dem_<名>.tif：全市共用一份 100 m DEM，硬链接 / 复制给每个情景名
    if not d.exists():
        try:
            os.link(shared, d)
        except OSError:
            shutil.copy2(shared, d)
    poly = [[round(x, 1), round(y, 1)] for x, y in D.simplify(15.0).exterior.coords[:-1]]
    nodes = {k: [round(v[0], 2), round(v[1], 2)] for k, v in N["xy"].items() if pd_.contains(Point(v))}
    inlet = {k: round(a, 3) for k, a in N.get("inlet_area", {}).items() if k in nodes}
    info = {"name": name, "region": reg, "title": REGIONS[reg]["title"], "rain_mm": rain, "dur_h": dur, "sim_h": te, "cn": B.CN, "tc_min": B.TC_MIN,
            "rain_excess_mm": round(float(exc[-1]), 2), "domain_km2": round(D.area / 1e6, 3), "domain_vertices": len(poly),
            "n_components": N["n_components"], "n_nodes": len(N["xy"]), "n_nodes_in_domain": len(nodes), "n_pipes": len(N["pipes"]), "n_outfalls": len(N["outs"]),
            "served_km2": round(N["A"] / 1e6, 2), "R_served_m": round(N["R_served"], 1), "mesh_res_m": RES_M, "max_tri_m2": RES_M * RES_M / 3.0,
            "n_tri_est": int(D.area / (0.65 * RES_M * RES_M / 3.0)), "dem_px": [int(zshape[1]), int(zshape[0])],
            "pumps": N["pumps"], "pump_limits": N["limits"], "ponds": [{"unitid": p["unitid"], "vol_model_m3": p["vol"], "inflows": p["inflows"], "kind": p.get("kind", "pond")} for p in N["ponds"]],      # 仅 L2 用
            "n_inlet_nodes": len(inlet), "inlet_area_total_m2": round(sum(inlet.values()), 1),
            "manning_2d": 0.030, "dt_1d_s": 30, "dt_couple_s": 60, "frame_s": 300, "states": {}}
    for s in STATES3:
        out = W / f"{name}_{s}.inp"
        ponds_s = N["ponds"] if s == "L2" else []                  # L1 无塘 / 渗井
        links = N["links"] if s == "L2" else []
        B.write_inp(out, N["xy"], N["ground"], N["inv"], N["pipes"] + links, N["outs"], N["area"], None, N["of_dia"], log=lambda *x: None)
        txt = out.read_text(encoding="utf-8").replace(f"THREADS  {B.SWMM_THREADS}", f"THREADS  {min(threads_of(reg), int(os.environ.get('AD_SWMM_THREADS', '8')))}", 1)
        title = f";; {REGIONS[reg]['title']} 1D network rebuilt from the post-improvement data set, {SNAME[s]}, {rain} mm / {dur} h (tools/adm100a_run.py)"
        txt = re.sub(r"(\[TITLE\]\n);;[^\n]*", lambda m: m.group(1) + title, txt, count=1)
        out.write_text(apply_pumps(txt, N), encoding="utf-8")
        if ponds_s:
            B.append_ponds(out, ponds_s, links)
        ponds2d = [{"node": p["node"], "unitid": p["unitid"], "vol": p["vol"], "xy": list(p["xy"]), "footprint": p["footprint"]} for p in ponds_s]
        meta = {"name": name, "state": s, "box": (X0, Y0, X1, Y1), "domain_polygon": poly, "R_served_m": N["R_served"], "nodes": nodes, "node_inlet_area": inlet, "ponds": ponds2d,
                "rain_t_h": [round(float(x), 4) for x in tt], "rain_excess_mm": [round(float(x), 4) for x in exc], "pumps_1d": N["pumps"],
                "note": f"{REGIONS[reg]['title']} {SNAME[s]}: post-improvement network; {len(N['pumps'])} pump groups; {len(ponds2d)} ponds / soakaways; {len(inlet)} nodes with inlet exchange area"}
        (C / f"pilot_{name}_{s}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        info["states"][s] = {"inp_mb": round(out.stat().st_size / 1048576, 1), "n_ponds": len(ponds2d), "n_pumps": len(N["pumps"])}
        print(f"  {s:<3} → {out.name}（{out.stat().st_size / 1048576:.1f} MB）：泵 {len(N['pumps'])}、塘 / 渗井 {len(ponds2d)}、带雨水口面积的井 {len(inlet):,}")
    for lim in N["limits"]:
        print(f"  ! 局限：{lim}")
    info["dem_res_m"] = RES_M
    (C / f"af_inputs_{t}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")      # 后处理（anuga-swmm 容器）读这份
    print(f"  二维：节点（域内）{len(nodes):,}，DEM {zshape[1]}×{zshape[0]} @ {RES_M} m（DTM 取平均），三角单元约 {info['n_tri_est']:,}（估）")


def dock_nas(*cmd, threads=8):
    """anuga-swmm 容器，多挂 NAS（宿主机路径同名挂进去）并带上主机名；成果直接写 $AD_RESULT_ROOT/$AD_HOST/。"""
    return ["docker", "run", "--rm", "-e", f"OMP_NUM_THREADS={threads}", "-e", f"AD_RESULT_ROOT={RESULT_ROOT}", "-e", f"AD_HOST={AD_HOST}",
            "-v", f"{P.HOST_WORK}:/work", "-v", f"{NAS_BASE}:{NAS_BASE}", P.IMAGE, *map(str, cmd)]


# ------------------------------------------------------------------ 步骤表：(id, 说明, 产物, 命令列表)
def chk(name, states):
    return P.dock("python", "/work/c2d/_chk_inp.py", *[f"{name}_{s}" for s in states], threads=1)


def threads_of(reg: str) -> int:
    """每个态的线程数：全市一个 27 万节点的整网 SWMM，缺省 8（调度器 --threads / C2D_G5_THREADS 可改；AD_THREADS_MAP="CITY=12" 也行）。"""
    for kv in os.environ.get("AD_THREADS_MAP", "").replace(";", ",").split(","):
        if "=" in kv and kv.split("=")[0].strip() == reg:
            return max(1, int(kv.split("=")[1]))
    return int(os.environ.get("C2D_G5_THREADS", "8"))


def compute_step(t, states):
    name, W, C = name_of(t), P.WORK, P.C2D
    RES_M = res_of(split_tag(t)[0])
    thr = threads_of(split_tag(t)[0])

    def f(dry):
        (C / "logs").mkdir(parents=True, exist_ok=True)
        names = []
        todo = [s for s in states if not ((C / f"twoway_{name}_{s}.json").exists() and (C / f"{name}_{s}_2d.npz").exists())]    # 已算完的态不重算
        if len(todo) < len(states):
            print(f"  已有结果的态跳过：{[s for s in states if s not in todo]}")
        for s in todo:
            n = f"{CONTAINER}_{t}_{s}"
            cmd = P.dock("sh", "-c", f"python -u /work/c2d/couple2way_v3.py {name} {s} {RES_M} {end_h(t):g} 60 > /work/c2d/logs/{name}_{s}.log 2>&1",
                         name=n, threads=thr, detach=True)
            print("  " + " ".join(cmd))
            if not dry:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL); names.append(n)
        while names and not dry:
            time.sleep(60)
            live = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
            for s in todo:
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
    RES_M = res_of(reg)
    return {
        "prep": ("prep", BATCH_PRE["desc"], BATCH_PRE["outs"], [prep]),
        "build": (f"build:{t}", f"{t}：建 L1 / L2（{rain} mm / {dur} h）",
                  [W / f"{name}_{s}.inp" for s in STATES3] + [C / f"pilot_{name}_{s}.json" for s in STATES3] + [C / f"dem_{name}.tif"],
                  [lambda dry: build(t, dry), chk(name, STATES3)]),
        "post": (f"post:{t}", f"{t}：.sww、积水点结果表与图层、交付目录（直接写 NAS：{RESULT_ROOT}/<主机>/，{rain} mm / {dur} h）", [post_out(t)],
                 [*[P.dock("python", "-u", "/work/c2d/npz_to_sww.py", name, s, RES_M, threads=8) for s in STATES],
                  dock_nas("python", "-u", "/work/c2d/adm100a_post.py", "--tag", t, "--res", RES_M, threads=8)]),
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
    for f in ("adm100a_post.py", "couple2way_v3.py", "runoff.py", "build_city1d.py", "build_pilot.py", "strip_baseline.py", "npz_to_sww.py", "chk_inp.py"):
        ok = (HERE / f).exists(); bad += not ok
        print(("  ✓ " if ok else "  ✗ ") + f"tools/{f}" + ("" if ok else " 不在"))
    for reg in REGIONS:
        for f in (f"{reg}_domain.geojson", f"{reg}_ponds.json", f"{reg}_pumps.json", f"af_net_{reg}.pkl"):
            ok = (CASES / f).exists(); bad += not ok
            print(("  ✓ " if ok else "  ✗ ") + f"cases/ad100a/{f}" + ("" if ok else " 不在"))
    ok = (CASES / "hotspots_ad.json").exists(); bad += not ok
    print(("  ✓ " if ok else "  ✗ ") + "cases/ad100a/hotspots_ad.json" + ("" if ok else " 不在"))
    print("文件都在（本 runner 不替换别的脚本的源码，没有替换点）" if not bad else f"有 {bad} 个文件不在")
    return 1 if bad else 0


def check(t) -> int:
    import datapaths
    ok = True
    if AD_HOST == "unknown-host":
        print("  ! 环境变量 AD_HOST（宿主机主机名）没设：成果会写到 result/unknown-host/；用 adm_100m_after_server.sh 启动就会带上")
    print(f"{REGIONS[split_tag(t)[0]]['title']} · {t}（{SCEN[t][0]} mm / {SCEN[t][1]} h）· 工作目录 {P.WORK}")
    for lab, p in (("DTM", datapaths.get("dtm")), ("雨型来源", P.WORK / "Mussafah_baseline.inp"), ("管网缓存", CASES / "af_net_CITYAFTER.pkl"),
                   ("耦合程序", HERE / "couple2way_v3.py"), ("读入检查", P.C2D / "_chk_inp.py"), (".sww 转换", P.C2D / "npz_to_sww.py"), ("后处理脚本", HERE / "adm100a_post.py")):   # NAS 在宿主机上由服务器脚本检查（容器里看不到）
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
