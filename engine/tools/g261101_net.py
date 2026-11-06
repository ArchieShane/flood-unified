# -*- coding: utf-8 -*-
"""261101 新区域的一维管网建模 + 泵站要素（替换 region_net.build_from_features 和 region_features.features_v2，不改它们）。

和 RD-A 版的差别（只有这三处；其余逻辑与 region_net.build_from_features 逐行相同）：
  1. 本态没有出水口（也没有泵站）时不报错，按数据原样保留全部管线——用户 2026-11-01：RD-F、Musaffah RD09 的 L1 数据里就是没有出水口，按数据实际来。
  2. 泵站：RD-A 的做法是「全区一个湿井、所有泵挂在它上面」。这批区域有好几个泵站，每台有参数的泵自己带湿井参数（WW_INV / WW_DEPTH / WW_AREA）：
     泵点所在节点当湿井，以它为起点的压力管改接到新节点 <泵>__out，泵（湿井 → <泵>__out）接上；找不到出水压力管的泵不建模。
  3. features_g 在 features_v2 的基础上，把打包时单独放在 pumps_modeled 图层里的「有曲线 + 启停水位 + 湿井参数」的泵加进来（features_v2 只认一个泵站本体，多泵站会错）。
用 install() 装进 region_features / region_net；run_region_g.py 启动时调用。
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

SNAP = 0.5
DEF_D, DEF_N, DEF_COVER = 0.300, 0.013, 1.5
SEAL_TOL = 0.5
SEAL_SUR = 100.0


def build_from_features_g(P, Nd, keep_outfalls, pumps, wetwell, D, log, caps=None, dtm=None):
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
    for pu in pumps:
        if pu.get("node", pu["id"]) in adj:
            stack.append(pu.get("node", pu["id"]))
    if not stack:       # 本态没有出水口 / 泵站节点：数据里就是这样，不治理——管网没有排出口，所有管都留下（RD-A 的做法会因为「没有出水口」直接报错）
        log("  ⚠ 本态没有出水口 / 泵站节点：管网没有排出口，按数据原样保留全部管线")
        stack = list(adj)
    while stack:
        n = stack.pop()
        if n in seen_n:
            continue
        seen_n.add(n)
        for i in adj[n]:
            if i not in seen_p:
                seen_p.add(i); stack += [P[i]["a"], P[i]["b"]]
    P = [P[i] for i in sorted(seen_p)]
    # 泵站：每台泵自己的湿井（数据里泵行带 WW_INV / WW_DEPTH / WW_AREA）。泵点所在节点 n 当湿井（蓄水）；以 n 为起点的压力管改接到新节点 n__out，
    # 泵（n → n__out）代替原来的接头；找不到出水压力管的泵不建模（记进 info["pumps_unmodeled"]）。
    stations, pump_ok, pump_skip = {}, [], []
    node_p = defaultdict(list)
    for i, p in enumerate(P):
        node_p[p["a"]].append(i); node_p[p["b"]].append(i)
    for pu in pumps:
        n = pu.get("node", pu["id"])
        outs = [i for i in node_p.get(n, []) if P[i]["a"] == n and P[i].get("force")]
        if not outs:
            pump_skip.append({"item": pu.get("ref", n), "reason": "泵点所在节点上没有以它为起点的压力管，找不到出水管，泵没有放进模型"})
            continue
        if n not in stations:
            stations[n] = {"out": f"{n}__out", "ww": pu["ww"]}
            for i in outs:
                P[i]["a"] = f"{n}__out"
        pump_ok.append(pu)
    pumps = pump_ok
    n_out = sum(1 for p in P if not pD.intersects(LineString([p["xy"][0], *p.get("vx", []), p["xy"][1]])))
    used = {p["a"] for p in P} | {p["b"] for p in P} | set(stations)
    info = {"pipes_in_domain_connected": len(P), "self_loops_dropped": loops, "new_junctions": sum(1 for k in extra.values() if k in used),
            "outfalls_found": [o for o in keep_outfalls if o in used], "pipes_outside_domain_1d_only": n_out,
            "pump_stations": sorted(stations), "pumps_in_model": len(pumps), "pumps_unmodeled": pump_skip}
    if not P:
        raise SystemExit("计算域内没有和 RD-A 出水口连通的管")
    byid = {n["id"]: n for n in Nd}
    xy_of = {n["id"]: tuple(n["xy"]) for n in Nd}
    xy_of.update({v: k for k, v in extra.items()})
    for n, st in stations.items():
        xy_of[st["out"]] = xy_of[n]
    assert dtm is not None, "需要 dtm（xy 列表 → 地面高程）采样函数"
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
        own = stations[k]["ww"]["inv"] if k in stations else nd.get("inv")
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
        if k in stations:
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
    for k, st in stations.items():
        ww = st["ww"]
        inv = min(ends[k] + [ww["inv"]])
        ST.append((k, inv, ww["maxd"] + (ww["inv"] - inv), 0.0, "TABULAR", f"{k}_Curve"))
        CU += [(f"{k}_Curve", "STORAGE", 0.0, ww["area"]), (f"{k}_Curve", "STORAGE", ww["maxd"] + 1.0, ww["area"])]
    for pu in pumps:
        k = pu.get("node", pu["id"])
        inv = min(ends[k] + [stations[k]["ww"]["inv"]])
        PU.append((f"PU_{pu['id']}", k, stations[k]["out"], f"PC_{pu['id']}", "ON", pu["on_elev"] - inv, pu["off_elev"] - inv))
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
    if not OF:
        # SWMM 动力波要求至少一个出水口节点（ERROR 145: no acceptable outlet nodes）。本态数据里没有出水口（用户：按数据实际来，不治理），
        # 放一个不接任何管的占位出水口，只为让求解器能启动——管网照样没有排出口，水只能留在管里或溢出到二维。
        k0 = next(iter(J))
        OF.append(("NO_OUTFALL_PLACEHOLDER", min(j[0] for j in J.values()), "FREE", "NO"))
        coords["NO_OUTFALL_PLACEHOLDER"] = (coords[k0][0] + 1.0, coords[k0][1] + 1.0)
        info["no_outfall_placeholder"] = "本态没有出水口：加了一个不接任何管的占位出水口，仅为让 SWMM 启动，管网实际没有排出口"
        log("  ⚠ " + info["no_outfall_placeholder"])
    net = {"J": J, "OF": OF, "ST": ST, "CO": CO, "XS": XS, "PU": PU, "CU": CU, "LO": [], "VX": VX, "coords": coords}
    return net, N, info


_ORIG = {}


def features_g(R, state, log=print):
    import geopandas as gpd
    import pyogrio
    import region_features as RF
    import region_model as RM
    P, Nd, keep_of, pumps, wetwell, caps, nmat, info = _ORIG["features_v2"](R, state, log)
    gp = R.p("network_v2.gpkg")
    if "pumps_modeled" in {l[0] for l in pyogrio.list_layers(gp)}:
        pm = gpd.read_file(gp, layer="pumps_modeled")
        pm = pm[pm["ASSET_STAGE"].isin(RF.STATE_STAGES[state])]
        for r in pm.itertuples():
            nid = RM.sid(r.UNITID)
            Nd.append({"id": nid, "xy": (r.geometry.x, r.geometry.y), "kind": "wetwell", "inv": None, "rim": None})
            segs = [RF.parse_curve(t) for t in str(r.PUMP_CURVE).split(" | ") if t.strip()]    # 站里每台泵一条曲线（用 " | " 隔开），N 台并联各接一个泵
            for i, curve in enumerate(segs):
                pumps.append({"id": nid if len(segs) == 1 else f"{nid}_{i + 1}", "node": nid, "xy": (r.geometry.x, r.geometry.y), "on_elev": float(r.ON_EL_M), "off_elev": float(r.OFF_EL_M),
                              "curve": curve, "ref": r.UNITID, "ww": {"inv": float(r.WW_INV), "maxd": float(r.WW_DEPTH), "area": float(r.WW_AREA)}})
        info["facilities"]["pumps"] = len(pumps)
    return P, Nd, keep_of, pumps, None, caps, nmat, info


def install():
    import region_features
    import region_net
    _ORIG["features_v2"] = region_features.features_v2
    region_features.features_v2 = features_g
    region_net.build_from_features = build_from_features_g
