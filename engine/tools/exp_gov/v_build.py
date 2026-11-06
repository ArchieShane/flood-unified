"""RD-A 治理前后验证：建 9 个一二维算例的输入（参数见 docs/RD-A_治理前后验证_参数确认_20261023.md，用户 2026-10-23 全部确认）。

  python tools/exp_gov/v_build.py --model A|R|G --storm E24|E26|D61

模型
  A  治理前 ①：AECOM 交付的 SewerGEMS inp 照原样（英制换公制），自带入流过程线（V-1）
  R  治理前 ②：未治理的 DMT 资产库原样（07_verify/extract_raw.json），字段原值、不排除、不去重、不改哨兵值（V-2）
  G  治理后：Expanded Utility Network 导出（07_verify/extract_gov.json），RD-A 出水口所在子网（V-3）
  R、G 共用：缺省规则 V-4；复合 CN 子汇水区入流挂到最近的井（V-5）；管网 = 计算域内、与出水口连通的部分
雨（V-7、V-8）：AECOM SewerGEMS 库里这三场 1 h 雨的逐 3 分钟雨量表（StormEventGroup 51273）
  E24 = 2024 年雨 82.2 mm（AECOM inp 51269），E26 = 2026 年雨 72 mm（51266），D61 = 10 年 + 20 % 61.34 mm（51258）；雨后 4 h（V-9）
二维（V-10…V-12）：计算域 = AECOM 子汇水区并集闭运算、填洞；DTM 5 m；15 m；交换 60 s（由 couple2way_v2 读）
产物：$WORK/VG_<雨>_<模型>.inp、$WORK/c2d/pilot_VG_<雨>_<模型>.json、dem_VG_<雨>.tif、out/exp_gov/verify/inputs_<雨>_<模型>.json
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sqlite3
import sys
from collections import defaultdict

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT))
import runoff as RO  # noqa: E402
import rb01_build as RB  # noqa: E402
import rda_build as RD  # noqa: E402
from workdir import WORK  # noqa: E402

from swbrain import client_data  # noqa: E402
HM = client_data.model("RD-A") / "Hydraulic Model"
SHP = client_data.model("RD-A") / "2026-1hr"
EXP = pathlib.Path("D:/swbrain_work/exp_gov/07_verify")
EXPW = EXP.parent
EXP_N = pathlib.Path("D:/swbrain_work/exp_gov_noaecom/07_verify")   # 对照 N：只治理 DMT、不融合 AECOM（run_noaecom.sh）
OUT = ROOT / "out" / "exp_gov" / "verify"
STORMS = {"E24": (82.2, "51269", 256), "E26": (72.0, "51266", 258), "D61": (61.34, "51258", 252)}
FT, CFS, FT2 = RD.FT, RD.CFS, RD.FT2
DUR, TAIL, DT_H = 1.0, 4.0, 1.0 / 60.0
SNAP = 0.5                    # 管端认节点（同 integrate_rda.yaml node_tol_m）
DEF_D, DEF_N, DEF_COVER = 0.300, 0.013, 1.5          # V-4
RDA_OUTFALL = "SW_ABT_W33_OFL_193"


def storm_shape(item):
    con = sqlite3.connect(f"file:{HM / 'RD - A.stsw.sqlite'}?mode=ro", uri=True)
    inc = [r[0] for r in con.execute("select StormEventDepths_StormEventDepth from StormEventGroup_StormEventDepths where SupportElementID=? order by ItemID", (item,))]
    con.close()
    return np.cumsum(inc)                     # 逐时段增量 → 累计（首项 0）


def rain(storm):
    mm, _, item = STORMS[storm]
    shape = storm_shape(item)
    sim_h = DUR + TAIL
    t, P = RO.hyetograph(mm, DUR, DT_H, shape, 0.0, total_h=sim_h)
    return t, P, sim_h, float(shape[-1] * 25.4)


def dtm_sampler():
    import rasterio
    ds = rasterio.open(RB.DTM)

    def f(xy):
        v = np.array([x[0] for x in ds.sample([tuple(p) for p in xy])], float)
        v[(v < -100) | ~np.isfinite(v)] = np.nan
        return v
    return f


# ------------------------------------------------------------------ A：AECOM 原样
def model_a(storm, t, log):
    src = HM / f"RD - A.stsw_1_{STORMS[storm][1]}.inp"
    S = RD.sections(src.read_text(encoding="utf-8", errors="ignore"))
    coords = {r[0]: (float(r[1]) * FT, float(r[2]) * FT) for r in S["COORDINATES"]}
    J = {r[0]: [float(r[1]) * FT, float(r[2]) * FT, float(r[3]) * FT, float(r[4]) * FT, float(r[5]) * FT2] for r in S["JUNCTIONS"]}
    OF = []
    for r in S["OUTFALLS"]:
        inv = float(r[1]) * FT
        OF.append((r[0], inv, f"FIXED {float(r[3]) * FT:.3f}", r[4]) if r[2] == "FIXED" else (r[0], inv, r[2], r[3]))
    ST = [(r[0], float(r[1]) * FT, float(r[2]) * FT, float(r[3]) * FT, r[4], r[5]) for r in S["STORAGE"]]
    XS = {r[0]: r[1:] for r in S["XSECTIONS"]}
    CO = [(r[0], r[1], r[2], float(r[3]) * FT, float(r[4]), float(r[5]) * FT, float(r[6]) * FT, 0, 0) for r in S["CONDUITS"]]   # 糙率照原样
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
        CU.append((r[0], ctype[r[0]], x * FT, y * FT2) if ctype[r[0]].upper() == "STORAGE" else (r[0], ctype[r[0]], x * FT, y * CFS))
    LO = [r for r in S["LOSSES"] if any(float(v) != 0 for v in r[1:4]) or r[4] == "YES"]
    VX = [(r[0], float(r[1]) * FT, float(r[2]) * FT) for r in S.get("VERTICES", [])]
    net = {"J": J, "OF": OF, "ST": ST, "CO": CO, "XS": XO, "PU": PU, "CU": CU, "LO": LO, "VX": VX, "coords": coords}
    # 入流：AECOM 自带过程线（时间 h，流量 cfs）→ 本模型时间轴（V-1、V-5）
    ts_of = {r[0]: r[2] for r in S["INFLOWS"] if len(r) >= 3 and r[1].upper() == "FLOW"}
    raw = defaultdict(list)
    want = set(ts_of.values())
    for r in S["TIMESERIES"]:
        if r[0] in want and len(r) >= 3:
            raw[r[0]].append((float(r[1]), float(r[2])))          # 入流序列：时间（h）、流量（cfs）
    H = {}
    for node, ts in ts_of.items():
        a = np.array(sorted(raw[ts]))
        H[node] = np.interp(t, a[:, 0], a[:, 1], right=0.0) * CFS
    import geopandas as gpd
    mh = gpd.read_file(SHP / "Manhole.shp")
    ids = set(mh.ID.astype(str))
    N = {k: (coords[k][0], coords[k][1], v[0] + v[1], v[0]) for k, v in J.items() if k in ids and k in coords}
    log(f"  A：{src.name}，井 {len(J):,}、管 {len(CO):,}、泵 {len(PU)}、出水口 {len(OF)}；AECOM 入流 {len(H):,} 个节点，"
        f"合计 {sum(float(q.sum()) * DT_H * 3600 for q in H.values()):,.0f} m³（模拟时段内）")
    return net, N, H, {"src_inp": str(src)}


# ------------------------------------------------------------------ R / G：从要素建
SEAL_TOL = 0.5          # 管端节点离封堵端 ≤ 0.5 m（同吸附距离）认作封堵端
SEAL_SUR = 100.0        # 封堵端：超压深度给足，不溢流


def end_caps(model, expw=None):
    """封堵端位置（2026-10-24 用户定：封堵端是封死的管端，不和二维交换、不溢流）。② 取 DMT 原样 SW_CAPPEDEND；③ 取治理后的 Fitting / End Cap"""
    import geopandas as gpd
    bb = (226000, 2703500, 237000, 2711500)
    if model == "R":
        g = gpd.read_file(EXPW / "src_dmt.gdb", layer="SW_CAPPEDEND", bbox=bb)
    else:
        g = gpd.read_file((expw or EXPW) / "07_export" / "Stormwater_AD_reporting.gdb", layer="StormwaterJunction", bbox=bb, columns=["d_assettype"])
        g = g[g.d_assettype == "End Cap"]
    return np.c_[g.geometry.x, g.geometry.y]


def build_from_features(P, Nd, keep_outfalls, pumps, wetwell, D, log, caps=None):
    """P：管 [{id, xy:[[x,y],[x,y]], vx, len, d_m, up, dn, n, force}]；Nd：点 [{id, xy, rim, inv, kind}]（kind: mh / inlet / outfall / pump / other）
    keep_outfalls：认作 RD-A 出口的出水口 id；pumps：[{id, xy, on_elev, off_elev, curve:[(head m, q m3/s)]}]；wetwell：{id, xy, inv, maxd, area} 或 None"""
    from scipy.spatial import cKDTree
    from shapely.geometry import LineString
    from shapely.prepared import prep
    pD = prep(D)
    # 不先按计算域裁：出水口和出水管常在计算域边界外。取与 RD-A 出水口连通的整块（同 C1 A2「整块取」），域外部分只算一维
    pts = np.array([n["xy"] for n in Nd]) if Nd else np.zeros((0, 2))
    tr = cKDTree(pts) if len(pts) else None
    node_of, extra = {}, {}

    def snap(xy):
        if tr is not None:
            d, i = tr.query(xy)
            if d <= SNAP:
                return Nd[i]["id"]
        k = (round(xy[0], 2), round(xy[1], 2))
        if k not in extra:
            extra[k] = f"JN{len(extra) + 1}"
        return extra[k]
    for p in P:
        p["a"], p["b"] = snap(p["xy"][0]), snap(p["xy"][1])
    loops = [p["id"] for p in P if p["a"] == p["b"]]
    P = [p for p in P if p["a"] != p["b"]]
    # 连通：与 RD-A 出水口在同一连通块的管（无向）
    adj = defaultdict(set)
    for i, p in enumerate(P):
        adj[p["a"]].add(i); adj[p["b"]].add(i)
    seen_n, seen_p, stack = set(), set(), [o for o in keep_outfalls if o in adj]
    if wetwell and wetwell["id"] in adj:
        stack.append(wetwell["id"])
    for pu in pumps:
        if pu["id"] in adj:
            stack.append(pu["id"])
    while stack:
        n = stack.pop()
        if n in seen_n:
            continue
        seen_n.add(n)
        for i in adj[n]:
            if i not in seen_p:
                seen_p.add(i); stack += [P[i]["a"], P[i]["b"]]
    P = [P[i] for i in sorted(seen_p)]
    n_out = sum(1 for p in P if not pD.intersects(LineString([p["xy"][0], *p.get("vx", []), p["xy"][1]])))
    used = {p["a"] for p in P} | {p["b"] for p in P}
    info = {"pipes_in_domain_connected": len(P), "self_loops_dropped": loops, "new_junctions": sum(1 for k in extra.values() if k in used),
            "outfalls_found": [o for o in keep_outfalls if o in used], "pipes_outside_domain_1d_only": n_out}
    if not P:
        raise SystemExit("计算域内没有和 RD-A 出水口连通的管")
    byid = {n["id"]: n for n in Nd}
    xy_of = {n["id"]: tuple(n["xy"]) for n in Nd}
    xy_of.update({v: k for k, v in extra.items()})
    dtm = dtm_sampler()
    # 管底缺（V-4）：先用同一节点上其他管的已知管底，再沿相邻管按坡度推，最后地面 − 1.5 m
    fill = {"from_node": 0, "by_slope": 0, "dtm_minus_cover": 0}
    known = defaultdict(list)
    for p in P:
        for end, nd in (("up", "a"), ("dn", "b")):
            if p[end] is not None:
                known[p[nd]].append(p[end])
    for p in P:
        for end, nd in (("up", "a"), ("dn", "b")):
            if p[end] is None and known.get(p[nd]):
                p[end] = min(known[p[nd]]); fill["from_node"] += 1
    slopes = [(p["up"] - p["dn"]) / max(p["len"], 1.0) for p in P if p["up"] is not None and p["dn"] is not None]
    s_med = float(np.median(slopes)) if slopes else 0.003
    for _ in range(50):
        ch = 0
        for p in P:
            if p["up"] is None and p["dn"] is not None:
                p["up"] = p["dn"] + s_med * p["len"]; fill["by_slope"] += 1; ch += 1
            elif p["dn"] is None and p["up"] is not None:
                p["dn"] = p["up"] - s_med * p["len"]; fill["by_slope"] += 1; ch += 1
        if not ch:
            break
    rest = [p for p in P if p["up"] is None or p["dn"] is None]
    if rest:
        z = dtm([xy_of[p["a"]] for p in rest] + [xy_of[p["b"]] for p in rest])
        for i, p in enumerate(rest):
            if p["up"] is None:
                p["up"] = float(z[i]) - DEF_COVER; fill["dtm_minus_cover"] += 1
            if p["dn"] is None:
                p["dn"] = float(z[len(rest) + i]) - DEF_COVER; fill["dtm_minus_cover"] += 1
    info["invert_filled"] = fill
    info["s_median"] = s_med
    # 节点
    ends = defaultdict(list)
    for p in P:
        ends[p["a"]].append(p["up"]); ends[p["b"]].append(p["dn"])
    names = sorted(used)
    zz = dtm([xy_of[k] for k in names])
    J, OF, coords, N, inv_of = {}, [], {}, {}, {}
    rim_dtm = depth0 = 0
    sealed = set()
    if caps is not None and len(caps):
        ctr = cKDTree(caps)
        for k in names:
            if byid.get(k, {"kind": "junction"})["kind"] == "junction" and ctr.query(xy_of[k])[0] <= SEAL_TOL:
                sealed.add(k)
    for k, zg in zip(names, zz):
        nd = byid.get(k, {"kind": "junction", "rim": None, "inv": None})
        own = nd.get("inv") if not (wetwell and k == wetwell["id"]) else wetwell["inv"]
        inv = min(ends[k] + ([own] if own is not None else []))
        inv_of[k] = inv
        coords[k] = xy_of[k]
        if nd["kind"] == "outfall":
            OF.append((k, inv, nd.get("bc") or "FREE", "YES" if nd.get("flap") else "NO"))
            continue
        rim = nd.get("rim")
        if rim is None:
            rim = float(zg) if np.isfinite(zg) else inv + 2.0; rim_dtm += 1
        md = rim - inv
        if md <= 0:
            md = 0.0; depth0 += 1                  # SWMM：最大深度 0 = 取最高接入管顶
        if wetwell and k == wetwell["id"]:
            continue
        if k in sealed:
            J[k] = [inv, md, 0.0, SEAL_SUR, 0.0]          # 封堵端：封死，不进二维交换
            continue
        J[k] = [inv, md, 0.0, 0.0, 0.0]
        # 计算域内所有一维节点都和二维交换（同模型 A：AECOM 2,157 个节点里 2,151 个交换）。
        # 2026-10-23 改：第一版只让检查井、雨水口交换，管端自动建的连接节点（JN）溢出的水在模型里消失（③ 48 %、② 64 %），用户同意改正重算
        if np.isfinite(zg):
            N[k] = (coords[k][0], coords[k][1], inv + md if md > 0 else rim, inv)
    info.update(rim_from_dtm=rim_dtm, depth_zero_swmm_default=depth0, sealed_end_caps=len(sealed))
    ST, PU, CU = [], [], []
    if wetwell and wetwell["id"] in used:
        k = wetwell["id"]
        inv = min(ends[k] + [wetwell["inv"]])
        ST.append((k, inv, wetwell["maxd"] + (wetwell["inv"] - inv), 0.0, "TABULAR", f"{k}_Curve"))
        CU += [(f"{k}_Curve", "STORAGE", 0.0, wetwell["area"]), (f"{k}_Curve", "STORAGE", wetwell["maxd"] + 1.0, wetwell["area"])]
        for pu in pumps:
            outn = pu["id"]
            if outn not in used:
                continue
            # 吸水管（湿井 → 泵点）由泵代替，否则水能绕过泵
            P = [p for p in P if not ({p["a"], p["b"]} == {k, outn})]
            PU.append((f"PU_{pu['id']}", k, outn, f"PC_{pu['id']}", "ON", pu["on_elev"] - inv, pu["off_elev"] - inv))
            for h, q in pu["curve"]:
                CU.append((f"PC_{pu['id']}", "PUMP3", h, q))
    CO, XS, VX = [], [], []
    for p in P:
        CO.append((p["id"], p["a"], p["b"], max(p["len"], 0.3), p["n"] if p["n"] else DEF_N, p["up"] - inv_of[p["a"]], p["dn"] - inv_of[p["b"]], 0, 0))
        XS.append((p["id"], "CIRCULAR", p["d_m"] if p["d_m"] else DEF_D, 0, 0, 0, 1))
        VX += [(p["id"], x, y) for x, y in p.get("vx", [])]
    # 偏移不能为负（节点管底取的是最小值，理论上不会）；出水口节点同样
    CO = [(c[0], c[1], c[2], c[3], c[4], max(c[5], 0.0), max(c[6], 0.0), 0, 0) for c in CO]
    info["pipes_default_d"] = sum(1 for p in P if not p["d_m"])
    info["pipes_default_n"] = sum(1 for p in P if not p["n"])
    net = {"J": J, "OF": OF, "ST": ST, "CO": CO, "XS": XS, "PU": PU, "CU": CU, "LO": [], "VX": VX, "coords": coords}
    return net, N, info


def num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def model_r(D, log):
    X = json.load(open(EXP / "extract_raw.json", encoding="utf-8"))
    P = []
    for r in X["pipes"]:
        d = num(r.get("PIPE_DIAME"))
        P.append({"id": f"R{r['OBJECTID_1']}", "xy": r["_xy"], "vx": r["_vx"], "len": r["_len"], "d_m": d / 1000 if d else None,
                  "up": num(r.get("INVERT_LEV")), "dn": num(r.get("INVERT_L_1")), "n": None, "src": r.get("UNITID")})
    Nd = []
    for lay, kind, rim_f in (("manholes", "mh", "COVER_LEVE"), ("inlets", "inlet", "COVER_LEVE"), ("outfalls", "outfall", None), ("pumps", "pump", None)):
        for r in X[lay]:
            Nd.append({"id": f"{kind[:2].upper()}{r['OBJECTID_1']}", "xy": r["_xy"], "kind": kind, "rim": num(r.get(rim_f)) if rim_f else None,
                       "inv": num(r.get("INVERT_LEV")), "unitid": r.get("UNITID")})
    outs = [n["id"] for n in Nd if n["kind"] == "outfall" and n.get("unitid") == RDA_OUTFALL]
    net, N, info = build_from_features(P, Nd, outs, [], None, D, log, caps=end_caps("R"))
    info["source"] = "src_dmt.gdb 原样（不排除、不去重、不改哨兵值；只有空值按 V-4 补）"
    return net, N, info


def model_g(D, log, exp=EXP):
    """③ 治理后；exp=EXP_N 时为对照 N（同一治理逻辑、不融合 AECOM：没有 AECOM 的管、井、出水口、泵和湿井）"""
    X = json.load(open(exp / "extract_gov.json", encoding="utf-8"))
    outf = [d for d in X["devices"] if d["group"] == "Outfall"]
    rda = next(d for d in outf if d["assetid"] == RDA_OUTFALL)
    sub = rda.get("smasubnetworkname")
    keep_of = [d["assetid"] for d in outf if d.get("smasubnetworkname") == sub or str(d["assetid"]).startswith("AECOM:")]
    P = []
    for r in X["lines"]:
        if r.get("assetstate") == 0:
            continue                                     # 停用（Closed）不过水
        P.append({"id": f"G{len(P) + 1}", "xy": r["_xy"], "vx": r["_vx"], "len": r["_len"], "d_m": (r["diameter"] / 1000) if r.get("diameter") else None,
                  "up": num(r.get("upelev")), "dn": num(r.get("downelev")), "n": num(r.get("manningn")), "src": r.get("assetid")})
    Nd = []
    from scipy.spatial import cKDTree as _T
    fg = [d["_xy"] for d in X["devices"] if d["group"] == "Outlet"]
    fgt = _T(np.array(fg)) if fg else None                 # 第七节：拍门在接入管上离出水口 0.5 m 处
    for d in X["devices"]:
        g = d["group"]
        if g == "Manhole Channel" or g == "Catch Basin":
            kind = "mh" if g == "Manhole Channel" else "inlet"
            Nd.append({"id": d["assetid"] or f"D{len(Nd)}", "xy": d["_xy"], "kind": kind, "rim": num(d.get("coverelev")), "inv": num(d.get("elevation"))})
        elif g == "Outfall":
            bt = d.get("boundarytype")
            bc = f"FIXED {d['tailwater']:.3f}" if bt == "User Defined Tailwater" and d.get("tailwater") is not None else "FREE"
            Nd.append({"id": d["assetid"], "xy": d["_xy"], "kind": "outfall", "inv": num(d.get("elevation")), "bc": bc,
                       "flap": bool(fgt is not None and fgt.query(d["_xy"])[0] <= 0.6)})
        elif g == "Connection" and d.get("src_layer") == "PUMP":       # 第五节：RD-A 泵站与 AECOM 湿井融合的节点
            Nd.append({"id": d["assetid"], "xy": d["_xy"], "kind": "wetwell", "inv": None, "rim": num(d.get("rimelev"))})
        elif g in ("Outlet", "Connection"):                            # 拍门、生成的连接点：只是管与管之间的节点
            Nd.append({"id": d["assetid"] or f"D{len(Nd)}", "xy": d["_xy"], "kind": "junction", "inv": None, "rim": None})
        elif g == "Pump":
            Nd.append({"id": d["assetid"], "xy": d["_xy"], "kind": "pump", "inv": None, "rim": None})

    # 井盖：检查井的井盖在结构（vault）上
    rim_v = {v["assetid"]: num(v.get("rimelev")) for v in X["vaults"]}
    for n in Nd:
        if n["kind"] == "mh" and n["id"].endswith("-CH"):
            n["rim"] = rim_v.get(n["id"][:-3])
    # 泵、湿井参数：融合规则「泵和泵曲线、湿井取 AECOM 模型」→ 从 AECOM 现状 inp 51079 取曲线和湿井面积 / 底高程
    S = RD.sections((HM / "RD - A.stsw_1_51079.inp").read_text(encoding="utf-8", errors="ignore"))
    import geopandas as gpd
    lab2id = dict(zip(gpd.read_file(SHP / "Pump.shp").LABEL, gpd.read_file(SHP / "Pump.shp").ID.astype(int).astype(str)))
    pcurve = {r[0]: r[3] for r in S["PUMPS"]}
    curves = defaultdict(list)
    cur = None
    for r in S["CURVES"]:
        if len(r) == 4:
            cur = r[0]; curves[cur].append((float(r[2]) * FT, float(r[3]) * CFS))
        else:
            curves[r[0]].append((float(r[1]) * FT, float(r[2]) * CFS))
    pumps = []
    for d in X["devices"]:
        if d["group"] == "Pump" and d.get("src_aecom"):
            pid = lab2id[d["src_aecom"]]
            pumps.append({"id": d["assetid"], "xy": d["_xy"], "on_elev": d["onelev"], "off_elev": d["offelev"], "curve": curves[pcurve[pid]]})
    st = S["STORAGE"][0]
    area = next(float(r[3]) * FT2 for r in S["CURVES"] if r[0] == st[5] and len(r) == 4)
    ww = next((n for n in Nd if n["kind"] == "wetwell"), None)
    wetwell = {"id": ww["id"], "xy": ww["xy"], "inv": float(st[1]) * FT, "maxd": float(st[2]) * FT, "area": area} if ww else None  # N：没有融合的湿井
    # SWMM 的 ID 不能带空格（AECOM 泵名「Mixed Pump_1-RD-A」等）：节点 / 泵 ID 里的空白换成「_」
    import re as _re
    sid = lambda x: _re.sub(r"\s+", "_", str(x))
    for n in Nd:
        n["id"] = sid(n["id"])
    for pu in pumps:
        pu["id"] = sid(pu["id"])
    keep_of = [sid(x) for x in keep_of]
    if wetwell:
        wetwell["id"] = sid(wetwell["id"])
    net, N, info = build_from_features(P, Nd, keep_of, pumps, wetwell, D, log, caps=end_caps("G", exp.parent))
    info.update(source="Expanded Utility Network（05_un），RD-A 出水口子网 " + str(sub), outfalls_kept=keep_of, pumps=[p["id"] for p in pumps],
                wetwell=wetwell)
    return net, N, info


def inflows_composite(t, P, net, N, log):
    """V-5：复合 CN 子汇水区（out/rda/catchments_composite），出口是 AECOM 节点 → 挂到本网最近的井"""
    import pandas as pd
    from scipy.spatial import cKDTree
    subs = pd.read_csv(RD.SUBS, dtype={"node": str})
    subs["tc_min"] = subs.tc_min.clip(lower=1.0)
    subs = subs[subs.area_m2 > 0]
    H0 = RO.node_hydrographs(subs, t, P, DT_H, log=log)
    S = RD.sections((HM / "RD - A.stsw_1_51079.inp").read_text(encoding="utf-8", errors="ignore"))
    co = {r[0]: (float(r[1]) * FT, float(r[2]) * FT) for r in S["COORDINATES"]}
    keys = list(N)
    tr = cKDTree(np.array([N[k][:2] for k in keys]))
    H, dist = defaultdict(lambda: np.zeros(len(t))), []
    for n, q in H0.items():
        d, i = tr.query(co[n])
        H[keys[i]] += q
        dist.append(d)
    exc = np.zeros(len(t))
    for cn, g in subs.groupby("cn"):
        exc += RO.scs_cum_excess(P, float(cn)) * g.area_m2.sum()
    exc /= subs.area_m2.sum()
    tot = sum(float(q.sum() * DT_H * 3600) for q in H.values())
    log(f"  入流：{len(subs):,} 块 {subs.area_m2.sum() / 1e4:.1f} ha → {len(H):,} 口井，合计 {tot:,.0f} m³；改挂距离中位 {np.median(dist):.1f} m、最大 {max(dist):.0f} m")
    return dict(H), exc, {"runoff_m3": round(tot), "remap_dist_median_m": round(float(np.median(dist)), 1), "remap_dist_max_m": round(float(max(dist)), 1)}


def inflows_aecom(storm, t, N, log):
    """⑤ 对照（用户 2026-10-25 批准）：治理后的管网 + AECOM 自带入流。AECOM 每个入流节点的过程线挂到本网最近的井（同 V-5 的改挂做法），
    用来分开「产流不同」和「管网不同」：进管的水和 ① 完全一样。"""
    from scipy.spatial import cKDTree
    _, _, Ha, _ = model_a(storm, t, lambda *a, **k: None)
    S = RD.sections((HM / f"RD - A.stsw_1_{STORMS[storm][1]}.inp").read_text(encoding="utf-8", errors="ignore"))
    co = {r[0]: (float(r[1]) * FT, float(r[2]) * FT) for r in S["COORDINATES"]}
    keys = list(N)
    tr = cKDTree(np.array([N[k][:2] for k in keys]))
    H, dist = defaultdict(lambda: np.zeros(len(t))), []
    for n, q in Ha.items():
        d, i = tr.query(co[n])
        H[keys[i]] += q
        dist.append(d)
    tot = sum(float(q.sum() * DT_H * 3600) for q in H.values())
    log(f"  入流（AECOM 自带）：{len(Ha):,} 个节点 → {len(H):,} 口井，合计 {tot:,.0f} m³；改挂距离中位 {np.median(dist):.1f} m、最大 {max(dist):.0f} m")
    return dict(H), np.zeros(len(t)), {"runoff_m3": round(tot), "runoff": "AECOM inp 自带入流（改挂到本网最近的井）",
                                       "remap_dist_median_m": round(float(np.median(dist)), 1), "remap_dist_max_m": round(float(max(dist)), 1)}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["A", "R", "G", "N", "M"], required=True)
    ap.add_argument("--storm", choices=list(STORMS), required=True)
    a = ap.parse_args(argv)
    import geopandas as gpd
    name, st = f"VG_{a.storm}", a.model
    log = print
    t, P, sim_h, mm_check = rain(a.storm)
    log(f"{name}_{st}：{STORMS[a.storm][0]} mm / 1 h（AECOM 雨量表合计 {mm_check:.2f} mm），模拟 {sim_h:g} h")
    cat = gpd.read_file(SHP / "Catchment.shp").set_crs(32640, allow_override=True)
    D = RB.domain(cat)
    if st == "A":
        net, N, H, info = model_a(a.storm, t, log)
        exc = np.zeros(len(t))                       # A 不用本脚本的产流，净雨只作记录
        info["runoff"] = "AECOM inp 自带入流"
    else:
        net, N, info = model_r(D, log) if st == "R" else model_g(D, log, EXP_N if st == "N" else EXP)
        H, exc, ri = inflows_aecom(a.storm, t, N, log) if st == "M" else inflows_composite(t, P, net, N, log)   # M：治理后管网 + AECOM 入流
        info.update(ri)
    RD.write_inp(WORK / f"{name}_{st}.inp", f"RD-A 验证 · 模型 {st} · {a.storm}", net, H, t, sim_h, log=log)
    if not (WORK / "c2d" / f"dem_{name}.tif").exists():
        RB.write_dem(name, D)
    RB.write_meta(name, st, D, N, t, exc, {"note": f"RD-A 治理前后验证（tools/exp_gov/v_build.py）模型 {st}"})
    info.update(name=name, model=st, storm=a.storm, rain_mm=STORMS[a.storm][0], sim_h=sim_h, n_nodes_2d=len(N), n_junctions=len(net["J"]),
                n_conduits=len(net["CO"]), n_outfalls=len(net["OF"]), n_pumps=len(net["PU"]), domain_km2=round(D.area / 1e6, 3))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"inputs_{a.storm}_{st}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    log(json.dumps({k: v for k, v in info.items() if not isinstance(v, (list, dict))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
