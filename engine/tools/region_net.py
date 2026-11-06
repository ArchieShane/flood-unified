# -*- coding: utf-8 -*-
"""一维管网建模：由管、点要素搭出 SWMM 网络（节点 / 管 / 出水口 / 泵 / 湿井）。

从 tools/exp_gov/v_build.py 的 build_from_features 摘出（2026-10-30），去掉对 Windows 路径、client_data 的依赖：
DTM 采样函数由调用方传入（dtm=…），封堵端坐标由调用方传入（caps=…）。算法不改，逻辑和 F 系列一致：
  管端吸附到节点（0.5 m）→ 只取与出水口 / 湿井 / 泵连通的管 → 缺管底按「同节点其他管 → 沿坡度 → 地面 − 1.5 m」补 →
  节点管底取最小、缺井盖用 DTM → 封堵端封死 → 湿井与泵（吸水管由泵代替）→ 管、断面、顶点。
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

SNAP = 0.5                    # 管端认节点（同 integrate_rda.yaml node_tol_m）
DEF_D, DEF_N, DEF_COVER = 0.300, 0.013, 1.5          # V-4
SEAL_TOL = 0.5          # 管端节点离封堵端 ≤ 0.5 m（同吸附距离）认作封堵端
SEAL_SUR = 100.0        # 封堵端：超压深度给足，不溢流


def build_from_features(P, Nd, keep_outfalls, pumps, wetwell, D, log, caps=None, dtm=None):
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
    # 重复管：起止节点（不分方向）、管径、长度都相同的只留第一根。治理后数据里 GDB 原管（PI_147975 / PI_147974）和 AECOM 补充管
    # （355330_AECOM / 355329_AECOM）并存，不去重会把泵站出水管、湿井进水管的过流能力翻倍，还让出水口多接一根管而被拆井（261031 OFL_193 漏水）
    seen_key, dup_dropped, keep = {}, [], []
    for p in P:
        k = (frozenset((p["a"], p["b"])), round(float(p["len"]), 1), round(float(p["d_m"] or 0), 3))
        if k in seen_key:
            dup_dropped.append({"dropped": p["id"], "kept": seen_key[k]})
            continue
        seen_key[k] = p["id"]
        keep.append(p)
    P = keep
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
    info = {"pipes_in_domain_connected": len(P), "self_loops_dropped": loops, "duplicate_pipes_dropped": dup_dropped, "new_junctions": sum(1 for k in extra.values() if k in used),
            "outfalls_found": [o for o in keep_outfalls if o in used], "pipes_outside_domain_1d_only": n_out}
    if not P:
        raise SystemExit("计算域内没有和 RD-A 出水口连通的管")
    byid = {n["id"]: n for n in Nd}
    xy_of = {n["id"]: tuple(n["xy"]) for n in Nd}
    xy_of.update({v: k for k, v in extra.items()})
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
