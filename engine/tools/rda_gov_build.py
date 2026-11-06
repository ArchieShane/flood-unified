# -*- coding: utf-8 -*-
"""RD-A 一二维重算 · 治理数据版（2026-10-28）。设计与参数登记：docs/RD-A_一二维重算_设计与参数登记_20261028.md（用户 10-28 已定待定 1–4）。

管网   第十四轮 Utility Network 导出（out/rda_gov/extract_gov.json），建法同 tools/exp_gov/v_build.model_g（V-3 / V-4），本脚本另改：
       既有管糙率一律按治理后管材 d_material（钢筋混凝土 0.015、其余 0.013，用户规则）；Force Pipe 按压力管 H-W C 130；
       排海出水口尾水 HAT 1.3 m + 拍门（WA-726 4.8）；计算域外的非结构节点封死（区域流程的教训）；井底高出 DTM 1 m 以上的节点不交换
产流   复合 CN 子汇水区（用户 10-20），径流挂到 AECOM 出口最近的结构节点（雨水井 / 检查井）
雨     A61D1：AECOM「10yrs.+20%CC-1Hr」原雨型 60.7 mm / 1 h，雨后 10 h
交换   雨水井 = WA-726 附录 A3 洼地格栅式（635×635、净开孔 50 %、防堵 0.5）；检查井 / 连接点只溢流；地面取单元地面（ground_basis = cell）
二维   Makani 下垫面分糙率：路面 / 铺装 / 其他硬地 0.016、建筑 0.5、绿地 / 未建地块 0.035、水面 0.020
状态   L1；L2 / L2P / OPTA61PH2 = 旧模型对应 inp 相对 L1 新增的元素，按坐标改挂到治理后管网（偏移、启停水深按绝对高程换算）
敏感性 L1B = 交换按一维井盖（旧做法）；L1I = 绿地 / 未建地块二维下渗 10 mm/h
用法：python tools/rda_gov_build.py --state L1|L2|L2P|OPTA61PH2|L1B|L1I [--tag A61D1] [--tail 10] [--as NAME]
产物：$WORK/RDAG_<tag>_<态>.inp、c2d/pilot_…json、c2d/dem_RDAG_<tag>.tif、c2d/surf_RDAG.tif、out/rda_gov/inputs_<tag>_<态>.json（逐项输入登记）
"""
from __future__ import annotations

import argparse
import json
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
import runoff as RO  # noqa: E402
import v_build as VB  # noqa: E402
from workdir import WORK  # noqa: E402

OUT = ROOT / "out" / "rda_gov"
GDB = VB.EXPW / "07_export" / "Stormwater_AD_reporting.gdb"
HM = VB.HM
STORMS = {"A61D1": (60.7, 1.0, HM / "RD - A.stsw_1_51263.inp", "Various_durations-51273-10yrs.+20%CC-1Hr-10yr_TS")}
N_RCP, N_OTHER, HW_C, HAT = 0.015, 0.013, 130, 1.3
INLET = {"P": round(3 * 0.635, 3), "A": round(0.635 * 0.635 * 0.5, 4), "clog": 0.5}
SURF = {0: ("其他硬地", 0.016), 1: ("路面 / 铺装 / 停车", 0.016), 2: ("建筑", 0.5), 3: ("绿地 / 未建地块", 0.035), 4: ("水面", 0.020)}
INFIL_MMH = {3: 10.0}
ROUTING = {"ROUTING_STEP": "0.5", "VARIABLE_STEP": "0.5", "MINIMUM_STEP": "0.1"}
# 1-14 管网初始水量（客户经验值，10-28：客户暂时提供不了地下水位、泵站遥测、CCTV，给定「管道里有 25 % 的水」）。
# 「25 %」按容积理解（每根管断面 25 % 有水，圆管水深约 0.30 D）【自定，按用户原话「有 25% 的水」】；按水深 25 % 理解时少约 1,100 m³。
INIT_FILL = 0.25
# 2-8a 地形深坑填平（用户 10-28「填平吧」）：坑内单元抬到周边地面 ring_median_m（couple2way_v2 dem_edits crest_m）；备案图 out/bldg/<id>_record.png
DEM_FILLS = [
    ("PIT-RDA-01", ROOT / "out" / "bldg" / "pit_230387.geojson"),
    ("PIT-RDA-02", ROOT / "out" / "bldg" / "pit_228924_2707573.geojson"),  # PIT-32, 深 8.56 m, 用户 10-28 按建议填
]
# 2-8 建筑挖洞（用户 10-28「要的」）：Makani 建筑轮廓栅格化 2.5 m → 327 洞、65.1 ha（tools/bldg_holes_prep.py）
BUILDING_HOLES = ROOT / "out" / "bldg" / "holes_RDA.json"


def dem_fills(reg):
    eds = []
    for pid, f in DEM_FILLS:
        ft = json.loads(f.read_text(encoding="utf-8"))["features"][0]
        eds.append({"id": pid, "polygon": ft["geometry"]["coordinates"][0], "crest_m": float(ft["properties"]["ring_median_m"])})
    reg["dem_fills"] = [{"id": e["id"], "crest_m": e["crest_m"], "src": str(f.relative_to(ROOT))} for e, (_, f) in zip(eds, DEM_FILLS)]
    return eds


def building_holes(reg):
    """加载建筑挖洞轮廓（tools/bldg_holes_prep.py 生成的）"""
    if not BUILDING_HOLES.exists():
        return None
    d = json.loads(BUILDING_HOLES.read_text(encoding="utf-8"))
    # holes_RDA.json 里只有元信息，实际轮廓在 pilot JSON 里要从 c2d/pilot_RDAG_A61D1_L1H.json 读
    # 但这里是批次 2 建模，L1H 试算的轮廓就是最终的，直接用
    try:
        h_pilot = json.loads((WORK / "c2d" / "pilot_RDAG_A61D1_L1H.json").read_text(encoding="utf-8"))
        holes = h_pilot["building_holes"]["holes"]
        reg["building_holes"] = {"n_holes": len(holes), "src": "c2d/pilot_RDAG_A61D1_L1H.json (from bldg_holes_prep.py)"}
        return {"holes": holes, **d}
    except Exception as e:
        reg["building_holes"] = {"error": str(e), "fallback": "none"}
        return None


def init_fill(txt, frac=INIT_FILL):
    """给 JUNCTIONS 写初始水深：节点水位 = 相连管端中最低的那个管底 + 该管「断面 frac 有水」对应的水深。
    出水口（定潮位）、泵站湿井不动（湿井按泵的启停水位自己运行）。返回 (新文本, 统计)。"""
    import numpy as np
    from scipy.optimize import brentq
    yd = brentq(lambda y: (lambda th: (th - np.sin(th)) / (2 * np.pi))(2 * np.arccos(1 - 2 * y)) - frac, 1e-6, 1 - 1e-6)
    S, cur = {}, None
    for l in txt.splitlines():
        s = l.split(";")[0].strip()
        if not s:
            continue
        if s.startswith("["):
            cur = s.strip("[]"); S[cur] = []; continue
        S[cur].append(s.split())
    inv = {r[0]: float(r[1]) for r in S.get("JUNCTIONS", [])}
    ymax = {r[0]: float(r[2]) for r in S.get("JUNCTIONS", [])}
    for sec in ("OUTFALLS", "STORAGE"):
        for r in S.get(sec, []):
            inv[r[0]] = float(r[1])
    dia = {r[0]: float(r[2]) for r in S.get("XSECTIONS", []) if r[1] in ("CIRCULAR", "FORCE_MAIN")}
    best = {}
    for r in S.get("CONDUITS", []):
        if r[0] not in dia:
            continue
        for n, off in ((r[1], float(r[5])), (r[2], float(r[6]))):
            if n in ymax:
                z = inv[n] + off
                if n not in best or z < best[n][0]:
                    best[n] = (z, dia[r[0]])
    y0 = {n: min(max(z + yd * d - inv[n], 0.0), ymax[n]) for n, (z, d) in best.items()}
    out, cur = [], None
    for l in txt.splitlines():
        s = l.strip()
        if s.startswith("["):
            cur = s.strip("[]")
        elif cur == "JUNCTIONS" and s and not s.startswith(";"):
            p = l.split()
            if p[0] in y0:
                p[3] = f"{y0[p[0]]:.3f}"
                l = "  ".join(p)
        out.append(l)
    # 统计：按 SWMM 初始化规则（管内水深 = 两端节点水深减偏移的平均，截到 0–D）估管内存水
    vol = full = 0.0
    seg = lambda y, d: (d * d / 8) * ((th := 2 * np.arccos(np.clip(1 - 2 * np.clip(y, 0, d) / d, -1, 1))) - np.sin(th))
    for r in S.get("CONDUITS", []):
        if r[0] not in dia:
            continue
        d, L = dia[r[0]], float(r[3])
        y1 = y0.get(r[1], 0.0) - float(r[5]); y2 = y0.get(r[2], 0.0) - float(r[6])
        vol += seg((y1 + y2) / 2, d) * L; full += np.pi * d * d / 4 * L
    st = {"frac_area": frac, "y_over_D": round(yd, 4), "nodes_set": len(y0),
          "pipe_vol_init_m3": round(vol), "pipe_vol_full_m3": round(full), "share": round(vol / full, 4)}
    return "\n".join(out) + "\n", st
ELEV_TOL = 1.0
OLD = "RDAG_A61D1"           # 旧模型（治理数据版）的同名雨：各状态措施从这里取
SEA_OUTFALLS = {"SW_ABT_W33_OFL_193", "AECOM:RD-A1", "AECOM:RD-A2"}      # RD-A 排海口（重力出口 + 泵站两个压力管出口）
sid = lambda x: re.sub(r"\s+", "_", str(x))


def log(*a):
    print(*a, flush=True)


def kinds(X):
    """治理后设备 → (组, 来源层)；管端自动建的连接点 JN* 不在这里"""
    return {sid(d["assetid"]): (d["group"], d.get("src_layer")) for d in X["devices"] if d.get("assetid")}


def materials():
    import pyogrio
    M = pyogrio.read_dataframe(GDB, layer="StormwaterLine", bbox=(226000, 2703500, 237000, 2711500),
                               columns=["assetid", "d_material"], read_geometry=False)
    return dict(zip(M.assetid, M.d_material))


def network(D, reg):
    """第十四轮导出 → 一维管网（v_build.model_g 的建法），再按登记表 1-3、1-5、1-9…1-11 改"""
    src = OUT / "extract_gov.json"
    X = json.loads(src.read_text(encoding="utf-8"))
    mat = materials()
    # 1-3：糙率一律按治理后管材；manningn 只有和 AECOM 匹配上的管才有，是 AECOM 的新管值，不用（1-4）
    gid, k, nmat = {}, 0, Counter()
    for r in X["lines"]:
        m = mat.get(r["assetid"]) or "缺"
        r["manningn"] = N_RCP if str(m).startswith("Reinforced Concrete") else N_OTHER
        nmat[f"{m} → {r['manningn']}"] += 1
        if r.get("assetstate") == 0:
            continue
        k += 1; gid[f"G{k}"] = r                      # 与 model_g 的编号一致（跳过停用管后按顺序 G1、G2…）
    tmp = OUT / "model"; tmp.mkdir(exist_ok=True)
    (tmp / "extract_gov.json").write_text(json.dumps(X, ensure_ascii=False), encoding="utf-8")
    orig = VB.end_caps
    VB.end_caps = lambda model, expw=None: orig(model, VB.EXPW)      # 封堵端取全市导出（第十四轮 reporting gdb）
    try:
        net, N, info = VB.model_g(D, log, exp=tmp)
    finally:
        VB.end_caps = orig
    reg["source"] = {"extract": str(src), "un": str(VB.EXPW / "05_un" / "Stormwater_AD.geodatabase"), "round": 14,
                     **{kk: v for kk, v in info.items() if kk not in ("wetwell",)}}
    reg["pipe_n_by_material"] = dict(nmat)
    K = kinds(X)
    # 1-5：Force Pipe 按压力管（H-W C 130）
    fm = {c[0] for c in net["CO"] if gid.get(c[0], {}).get("group") == "Force Pipe"}
    net["XS"] = [(x[0], "FORCE_MAIN", x[2], HW_C, 0, 0, 1) if x[0] in fm else x for x in net["XS"]]
    reg["force_mains"] = sorted(gid[c]["assetid"] for c in fm)
    # 1-9 / 1-10：排海口尾水 HAT + 拍门；其余出水口照导出的边界（无类型 = 自由出流），逐个登记
    OF = []
    for o in net["OF"]:
        if o[0] in SEA_OUTFALLS:
            OF.append((o[0], o[1], f"FIXED {HAT:.3f}", "YES"))
        else:
            OF.append(o)
    net["OF"] = OF
    reg["outfalls"] = [{"id": o[0], "invert_m": round(o[1], 3), "boundary": o[2], "flap": o[3]} for o in OF]
    # 计算域外的非结构节点（拍门、泵出口、管件、连接点）封死：那里没有井口，水冒不出来；不封就在域外丢水（区域流程教训）
    from shapely.geometry import Point
    from shapely.prepared import prep
    pD = prep(D)
    struct = {k for k, v in K.items() if v[0] in ("Manhole Channel", "Catch Basin")}
    sealed_out = [k for k, j in net["J"].items() if k not in struct and j[3] < VB.SEAL_SUR and not pD.contains(Point(net["coords"][k]))]
    for k in sealed_out:
        net["J"][k][3] = VB.SEAL_SUR
    # 泵出口节点在压力管上：封死（同 AECOM 30,480 m）
    pump_out = {p[2] for p in net["PU"]}
    for k in pump_out:
        if k in net["J"]:
            net["J"][k][3] = VB.SEAL_SUR
    reg["sealed"] = {"outside_domain_nonstructure": len(sealed_out), "pump_outlets": sorted(pump_out & set(net["J"])),
                     "end_caps": info.get("sealed_end_caps")}
    return net, N, X, K, struct


def node_min_depth(net):
    """每个节点接入管的最高管顶（相对节点底）：ground_basis = cell 时节点深度不能比它浅"""
    inv = {k: v[0] for k, v in net["J"].items()}
    dia = {x[0]: float(x[2]) for x in net["XS"]}
    md = defaultdict(float)
    for c in net["CO"]:
        md[c[1]] = max(md[c[1]], float(c[5]) + dia[c[0]]); md[c[2]] = max(md[c[2]], float(c[6]) + dia[c[0]])
    return {k: round(md[k], 3) for k in inv if k in md}


def exchange_nodes(net, N, K, struct, D, reg):
    """交换节点 = 计算域内、没封死的节点；雨水井 → inlet，其余（检查井、连接点、高于地面的节点）→ 只溢流"""
    from shapely.geometry import Point
    from shapely.prepared import prep
    pD = prep(D)
    dtm = VB.dtm_sampler()
    keys = [k for k in N if k in net["J"] and net["J"][k][3] < VB.SEAL_SUR and pD.contains(Point(N[k][0], N[k][1]))]
    z = dtm([N[k][:2] for k in keys])
    xt, elevated = {}, []
    for k, zg in zip(keys, z):
        if np.isfinite(zg) and net["J"][k][0] > zg + ELEV_TOL:
            elevated.append(k)
        xt[k] = "inlet" if K.get(k, ("", ""))[0] == "Catch Basin" and k not in elevated else "overflow"
    reg["exchange"] = {"nodes": len(keys), "by_type": dict(Counter(xt.values())),
                       "by_asset": dict(Counter(K.get(k, ("连接点 JN",))[0] for k in keys)),
                       "elevated_overflow_only": elevated}
    return {k: N[k] for k in keys}, xt


def inflows(net, struct, storm, tail, reg):
    """复合 CN 产流（rda_build.hydrographs，AECOM 原雨型）→ 挂到 AECOM 出口最近的结构节点（雨水井 / 检查井）"""
    from scipy.spatial import cKDTree
    mm, dur, inp, ts = STORMS[storm]
    RD.SHAPE_SRC = (str(inp), ts)
    t, P, exc, H0, sim_h, subs, tot = RD.hydrographs(mm, dur, "f", tail, log=log)
    S = RD.sections(RD.SRC_INP.read_text(encoding="utf-8", errors="ignore"))
    co = {r[0]: (float(r[1]) * VB.FT, float(r[2]) * VB.FT) for r in S["COORDINATES"]}
    keys = [k for k in net["J"] if k in struct]
    tr = cKDTree(np.array([net["coords"][k] for k in keys]))
    H, dist = defaultdict(lambda: np.zeros(len(t))), []
    for n, q in H0.items():
        d, i = tr.query(co[n]); H[keys[i]] += q; dist.append(d)
    dist = np.array(dist)
    reg["runoff"] = {"storm": storm, "rain_mm": mm, "dur_h": dur, "shape": f"{inp.name} · {ts}", "sim_h": sim_h,
                     "subs_n": len(subs), "subs_ha": round(float(subs.area_m2.sum() / 1e4), 2), "excess_mm_aw": round(float(exc[-1]), 2),
                     "runoff_m3": round(tot), "outlets": len(H0), "to_nodes": len(H),
                     "remap_dist_m": {"median": round(float(np.median(dist)), 2), "p95": round(float(np.percentile(dist, 95)), 1),
                                      "max": round(float(dist.max()), 1), "n_gt_5m": int((dist > 5).sum())}}
    log(f"  入流：{len(H0):,} 个 AECOM 出口 → {len(H):,} 个结构节点，改挂距离中位 {np.median(dist):.2f} m、最大 {dist.max():.1f} m")
    return t, exc, dict(H), sim_h


def surface_raster(D, reg, res=2.0):
    """Makani 下垫面 → 分类栅格（2 m）：建筑 2 > 路面 / 铺装 / 停车 1 > 水面 4 > 绿地 / 未建地块 3 > 其他硬地 0。同 rda_catchments.py 的表"""
    import rasterio
    from pyproj import Transformer
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from shapely import wkb
    from swbrain import db
    from swbrain.landuse import PAVE_TABLES
    import rda_catchments as RC
    f = WORK / "c2d" / "surf_RDAG.tif"
    x0, y0, x1, y1 = np.array(D.bounds) + np.array([-30, -30, 30, 30])
    W, H = int(np.ceil((x1 - x0) / res)), int(np.ceil((y1 - y0) / res))
    tf = from_origin(x0, y1, res, res)
    tw = Transformer.from_crs(32640, 4326, always_xy=True)
    lo0, la0 = tw.transform(x0, y0); lo1, la1 = tw.transform(x1, y1)
    env = lambda s: f"st_makeenvelope({lo0},{la0},{lo1},{la1},4326)" if s == 4326 else f"st_makeenvelope({x0},{y0},{x1},{y1},32640)"
    conn = db.connect_source(db.load_config(None), "makani", timeout_ms=600_000)
    cnt = {}

    def lay(cur, tables, where=""):
        g = []
        for t, s in tables:
            try:
                cur.execute(f"select st_asbinary(st_transform(st_makevalid(shape),32640)) from public.{t} where shape && {env(s)} and not st_isempty(shape){where}")
                gg = [wkb.loads(bytes(r[0])) for r in cur.fetchall() if r[0]]
            except Exception as e:
                conn.rollback(); cnt[t] = f"读不到：{str(e).splitlines()[0][:60]}"; continue
            gg = [x for x in gg if x.geom_type in ("Polygon", "MultiPolygon", "GeometryCollection") and not x.is_empty]
            cnt[t] = len(gg); g += gg
        return g

    def ras(g):
        return rasterize(((x, 1) for x in g), out_shape=(H, W), transform=tf, fill=0, dtype="uint8").astype(bool) if g else np.zeros((H, W), bool)
    with conn, conn.cursor() as cur:
        bld = ras(lay(cur, RC.BLD))
        pave = ras(lay(cur, PAVE_TABLES)) | ras(lay(cur, RC.PARK))
        water = ras(lay(cur, RC.WATER))
        green = ras(lay(cur, RC.GREEN)) | ras(lay(cur, (("udm_plot", 4326),), " and construction_status ilike 'not constructed%'"))
    c = np.zeros((H, W), np.uint8)
    c[green] = 3; c[water] = 4; c[pave] = 1; c[bld] = 2
    with rasterio.open(RB.DTM) as ds:
        crs = ds.crs                      # 坐标系取 DTM 文件自带的（本机 PROJ_LIB 指向 PostgreSQL 的旧 proj.db，按 EPSG 号查会失败）
    prof = {"driver": "GTiff", "width": W, "height": H, "count": 1, "dtype": "uint8", "crs": crs, "transform": tf, "compress": "deflate"}
    with rasterio.open(f, "w", **prof) as w:
        w.write(c, 1)
    from rasterio.features import geometry_mask
    inside = ~geometry_mask([D], out_shape=(H, W), transform=tf)
    share = {SURF[k][0]: round(float((c[inside] == k).mean()), 4) for k in SURF}
    reg["surface"] = {"raster": str(f), "res_m": res, "makani_counts": cnt, "share_in_domain": share,
                      "n": {SURF[k][0]: SURF[k][1] for k in SURF}}
    log(f"  下垫面（计算域内占比）：{share}")
    return f


def secs(p):
    """inp → {节 → [原样行]}（去注释、去空行，保序）"""
    S, cur = {}, None
    for ln in pathlib.Path(p).open(encoding="utf-8", errors="replace"):
        s = ln.strip()
        if s.startswith("["):
            cur = s[1:-1].upper(); S.setdefault(cur, []); continue
        if cur and s and not s.startswith(";"):
            S[cur].append(s)
    return S


SKIP = {"TITLE", "OPTIONS", "REPORT", "EVAPORATION", "INFLOWS", "TIMESERIES"}


def transfer(st, net, struct, reg):
    """旧模型（AECOM 管网版）里这个态相对 L1 新增的元素 → 治理后管网（登记表 1-5c）。
    引用的旧节点按坐标改挂到同类节点：井 → 雨水井 / 检查井，出水口 → 出水口，蓄水 → 湿井；
    管偏移、泵启停水深、控制规则里的水深都按绝对高程换算（新值 = 旧值 + 旧井底 − 新井底，不小于 0）"""
    from scipy.spatial import cKDTree
    b, s = secs(WORK / f"{OLD}_L1.inp"), secs(WORK / f"{OLD}_{st}.inp")
    inv_old, typ_old = {}, {}
    for sec, ty in (("JUNCTIONS", "J"), ("OUTFALLS", "OF"), ("STORAGE", "ST")):
        for r in b.get(sec, []):
            f = r.split(); inv_old[f[0]] = float(f[1]); typ_old[f[0]] = ty
    co_old = {r.split()[0]: (float(r.split()[1]), float(r.split()[2])) for r in b["COORDINATES"]}
    inv_new = {k: v[0] for k, v in net["J"].items()}
    inv_new.update({o[0]: o[1] for o in net["OF"]}); inv_new.update({x[0]: x[1] for x in net["ST"]})
    pools = {"J": [k for k in net["J"] if k in struct], "OF": [o[0] for o in net["OF"]], "ST": [x[0] for x in net["ST"]]}
    trees = {ty: cKDTree(np.array([net["coords"][k] for k in ks])) for ty, ks in pools.items() if ks}
    mp, rows = {}, []

    def m(old):
        if old not in inv_old:
            return old                                   # 这个态自己新建的元素：原名
        if old not in mp:
            ty = typ_old[old]
            d, i = trees[ty].query(co_old[old]); new = pools[ty][i]
            mp[old] = new
            rows.append({"old": old, "new": new, "type": ty, "dist_m": round(float(d), 2),
                         "inv_old": round(inv_old[old], 3), "inv_new": round(inv_new[new], 3)})
        return mp[old]

    def shift(old, v):
        return v + inv_old[old] - inv_new[m(old)] if old in inv_old else v
    adds, clipped, rem = defaultdict(list), [], {}
    for sec, lines in s.items():
        if sec in SKIP:
            continue
        have = set(b.get(sec, []))
        rem[sec] = [ln for ln in b.get(sec, []) if ln not in set(lines)]
        for ln in (ln for ln in lines if ln not in have):
            f = ln.split()
            if sec == "CONDUITS":
                o1, o2 = shift(f[1], float(f[5])), shift(f[2], float(f[6]))
                if o1 < 0 or o2 < 0:
                    clipped.append({"link": f[0], "off_up": round(o1, 3), "off_dn": round(o2, 3)})
                f[1], f[2], f[5], f[6] = m(f[1]), m(f[2]), f"{max(o1, 0):.3f}", f"{max(o2, 0):.3f}"
            elif sec == "PUMPS":
                on, off = float(f[5]), float(f[6])
                if on or off:
                    on, off = max(shift(f[1], on), 0.0), max(shift(f[1], off), 0.0)
                f[1], f[2], f[5], f[6] = m(f[1]), m(f[2]), f"{on:.3f}", f"{off:.3f}"
            elif sec == "CONTROLS":
                mm_ = re.match(r"(IF|AND|OR)\s+NODE\s+(\S+)\s+DEPTH\s*([<>=]+)\s*([\d.]+)", ln)
                if mm_ and mm_.group(2) in inv_old:
                    o = mm_.group(2)
                    ln = f"{mm_.group(1)} NODE {m(o)} DEPTH {mm_.group(3)} {max(shift(o, float(mm_.group(4))), 0):.3f}"
                adds[sec].append(ln); continue
            elif sec == "COORDINATES" and f[0] in inv_old:
                continue
            adds[sec].append("  ".join(f))
    # 名字不能和治理后管网重名
    taken = set(net["J"]) | set(inv_new) | {c[0] for c in net["CO"]} | {p[0] for p in net["PU"]}
    for sec in ("JUNCTIONS", "STORAGE", "OUTFALLS", "CONDUITS", "PUMPS"):
        dup = [ln.split()[0] for ln in adds.get(sec, []) if ln.split()[0] in taken]
        if dup:
            raise SystemExit(f"{st} 新增元素和治理后管网重名：{dup[:5]}")
    far = [r for r in rows if r["dist_m"] > 10]
    reg["transfer"] = {"from": f"{OLD}_{st}.inp − {OLD}_L1.inp", "added": {k: len(v) for k, v in adds.items()},
                       "not_transferred_removed": {k: v for k, v in rem.items() if v},
                       "node_map": rows, "node_map_gt_10m": far, "offset_clipped": clipped}
    log(f"  {st} 措施：新增 {dict((k, len(v)) for k, v in adds.items())}；引用旧节点 {len(rows)} 个（> 10 m 的 {len(far)} 个），"
        f"偏移被截到 0 的管 {len(clipped)} 根；旧模型删掉、这里没删的：{ {k: v for k, v in rem.items() if v} }")
    return dict(adds), mp


def inject(path, adds):
    """把各节的新增行接到已写好的 inp 里（没有这一节就在文末新建）"""
    txt = path.read_text(encoding="utf-8").split("\n")
    out, cur, done = [], None, set()

    def flush():
        if cur in adds and cur not in done:
            while out and out[-1] == "":
                out.pop()
            out.extend(adds[cur]); out.append(""); done.add(cur)
    for ln in txt:
        if ln.startswith("["):
            flush(); cur = ln.strip()[1:-1].upper()
        out.append(ln)
    flush()
    for sec, lines in adds.items():
        if sec not in done:
            out += ["", f"[{sec}]", *lines]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def pilot_extra(st, mp, reg):
    """旧 pilot 里这个态的塘、二维取水点 → 本模型（塘 / 取水点本身是新建元素，原名；引用旧节点的按 mp 改名）"""
    p = json.loads((WORK / "c2d" / f"pilot_{OLD}_{st}.json").read_text(encoding="utf-8"))
    ponds = p.get("ponds") or []
    for x in ponds:
        x["node"] = mp.get(x["node"], x["node"])
    pumps2d = p.get("pumps2d") or []
    for q in pumps2d:
        if q.get("to_pond"):
            q["to_pond"] = mp.get(q["to_pond"], q["to_pond"])
    reg["pilot_transfer"] = {"ponds": len(ponds), "pumps2d": len(pumps2d)}
    return {"ponds": ponds, "pumps2d": pumps2d}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True, choices=["L1", "L2", "L2P", "OPTA61PH2", "L1B", "L1I"])
    ap.add_argument("--tag", default="A61D1", choices=list(STORMS))
    ap.add_argument("--tail", type=float, default=10.0)
    ap.add_argument("--init-fill", type=float, default=INIT_FILL, help="管道初始有水的断面比例；0 = 全空")
    ap.add_argument("--suffix", default="", help="态名后缀，敏感性用（如 E = 全空）")
    a = ap.parse_args(argv)
    import geopandas as gpd
    OUT.mkdir(parents=True, exist_ok=True)
    name, st = f"RDAG_{a.tag}", a.state
    stn = st + a.suffix                       # 文件用的态名（敏感性加后缀）；建模逻辑仍按 st
    reg = {"name": name, "state": stn, "register": "docs/RD-A_一二维重算_设计与参数登记_20261028.md"}
    log(f"{name}_{stn}")
    cat = gpd.read_file(VB.SHP / "Catchment.shp").set_crs(32640, allow_override=True)
    D = RB.domain(cat)
    net, N, X, K, struct = network(D, reg)
    Nx, xt = exchange_nodes(net, N, K, struct, D, reg)
    t, exc, H, sim_h = inflows(net, struct, a.tag, a.tail, reg)
    mp, extra = {}, {}
    base = st if st in ("L2", "L2P", "OPTA61PH2") else None
    if base:
        adds, mp = transfer(base, net, struct, reg)
    inp = WORK / f"{name}_{stn}.inp"
    RD.write_inp(inp, f"RD-A 治理数据版 · {stn} · {a.tag}（tools/rda_gov_build.py）", net, H, t, sim_h, log=log)
    if base:
        inject(inp, adds)
        extra = pilot_extra(base, mp, reg)
    # 1-12 一维步长（用户 10-28 定，冒烟 DXG）：客户值 1 s / 0.75 / 0.5 s 下回流使连续性误差 4.1 %，缩到 0.5 s / 0.5 / 0.1 s 降到 1.47 %，积水结果不变
    txt = inp.read_text(encoding="utf-8")
    for k, v in ROUTING.items():
        txt = re.sub(rf"^{k}\s+\S+", f"{k}  {v}", txt, flags=re.M)
    if a.init_fill > 0:
        txt, reg["init_fill"] = init_fill(txt, a.init_fill)
        log(f"  初始水量：{reg['init_fill']}")
    else:
        reg["init_fill"] = {"frac_area": 0.0, "note": "全空（敏感性）"}
    inp.write_text(txt, encoding="utf-8")
    reg["routing"] = ROUTING
    if not (WORK / "c2d" / f"dem_{name}.tif").exists():
        RB.write_dem(name, D)
    sf = WORK / "c2d" / "surf_RDAG.tif"
    if not sf.exists():
        surface_raster(D, reg)
    sc = {"raster": "$C2D/surf_RDAG.tif", "n": {str(k): v[1] for k, v in SURF.items()}, "default_n": 0.016,
          "labels": {str(k): v[0] for k, v in SURF.items()}}
    if st == "L1I":
        sc["infil_mmh"] = {str(k): v for k, v in INFIL_MMH.items()}
    meta = {"note": f"RD-A 治理数据版 {stn}（tools/rda_gov_build.py）", "xchg_type": xt, "inlet": INLET,
            "surface_classes": sc, **extra}
    meta["dem_edits"] = dem_fills(reg) + list(meta.get("dem_edits") or [])
    bh = building_holes(reg)
    if bh:
        meta["building_holes"] = bh
    if st != "L1B":
        meta.update(ground_basis="cell", node_min_depth=node_min_depth(net))
    RB.write_meta(name, stn, D, Nx, t, exc, meta)
    reg.update(inp=str(inp), sim_h=sim_h, domain_km2=round(D.area / 1e6, 3), n_junctions=len(net["J"]), n_conduits=len(net["CO"]),
               n_outfalls=len(net["OF"]), n_pumps=len(net["PU"]), n_storage=len(net["ST"]), inlet=INLET,
               ground_basis=meta.get("ground_basis", "一维井盖（敏感性 L1B）"), infil_mmh=sc.get("infil_mmh"))
    (OUT / f"inputs_{a.tag}_{stn}.json").write_text(json.dumps(reg, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    log(json.dumps({k: v for k, v in reg.items() if not isinstance(v, (list, dict))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
