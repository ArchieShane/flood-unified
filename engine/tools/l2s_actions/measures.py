# -*- coding: utf-8 -*-
"""7 类待补措施的候选生成（设计第 1–7 节）。每个函数返回 (候选列表, 不可建列表)；候选的 id 由 run.py 统一编号。"""
from __future__ import annotations

import math

import numpy as np

from common import HAT, PH, K, DN, item, status_of, near_hs, dn_up, pipe_cost


# ---------------- 1 现状管道扩容 ----------------
def pipe_upsize(M, H, site, min_h=0.17, max_chain_m=300.0):
    """瓶颈管（SWMM「Hours Capacity Limited」≥ 10 min：管自身过流能力不够，不是下游顶托）从热点 / 高后果点 200 m 内的种子顺流串链；
    种子另要求上游节点水头到地面 0.3 m 内；只穿过瓶颈管、不进入已有的链，链长 ≤ 300 m（设计第 1 节，2026-10-30 收紧）"""
    def neck(c):
        s = M.surch.get(c); v = M.cond[c]
        return bool(s) and v["shape"] not in ("FORCE_MAIN", "DUMMY") and v["d"] > 0 and s["cap_limited"] >= min_h

    def seed_ok(c):
        up = M.depth.get(M.cond[c]["n1"]); n1 = M.cond[c]["n1"]
        return neck(c) and up is not None and n1 in M.rim and up["max_hgl"] >= M.rim[n1] - 0.3
    seeds = []
    for c in M.cond:
        if seed_ok(c):
            p = M.xy.get(M.cond[c]["n1"])
            if p:
                d, h = near_hs(p[0], p[1], H)
                if h:
                    seeds.append((c, h, d))
    chains, seen = [], set()
    for c0, h, d in sorted(seeds, key=lambda s: s[2]):
        if c0 in seen:
            continue
        ch, L, c = [], 0.0, c0
        while c and c not in seen and neck(c) and L + M.cond[c]["len"] <= max_chain_m + 1e-6:
            ch.append(c); seen.add(c); L += M.cond[c]["len"]
            nxt = [x for x in M.out_of.get(M.cond[c]["n2"], []) if M.cond[x]["shape"] != "DUMMY"]
            c = max(nxt, key=lambda x: M.cond[x]["d"]) if nxt else None
        if not ch and c0 not in seen:                            # 单根就超 300 m 的长管：自成一链
            ch, L = [c0], M.cond[c0]["len"]; seen.add(c0)
        if ch:
            chains.append((ch, h, d, L))
    cands, rej = [], []
    for ch, h, d, L in chains:
        grades, comp = [], []
        dmax = max(M.cond[c]["d"] for c in ch)
        for k in (1, 2):
            patch, cost, cover_fail, b_short = {}, 0.0, [], []
            for c in ch:
                v = M.cond[c]; nd = dn_up(v["d"], k)
                if nd is None:                                       # > 1.5 m：等面积矩形箱涵，高不超过原管径 + 0.3 m
                    area = math.pi * (DN[-1] if v["d"] <= DN[-1] else v["d"]) ** 2 / 4 * (1.3 ** k)
                    hh = min(v["d"] + 0.3, 2.5); ww = round(area / hh, 2)
                    patch[c] = {"shape": "RECT_CLOSED", "g1": round(hh, 2), "g2": ww, "n": 0.013}; nd_eff = hh
                else:
                    patch[c] = {"shape": "CIRCULAR", "g1": nd, "g2": 0, "n": 0.012}; nd_eff = nd
                for nn, off in ((v["n1"], v["off1"]), (v["n2"], v["off2"])):
                    if nn in M.rim and M.rim[nn] - (M.inv[nn] + off + nd_eff) < K.val("WA726-1-162"):
                        cover_fail.append(nn)
                depth = max(M.rim.get(v["n1"], 0) - M.inv.get(v["n1"], 0), 0)
                p1, p2 = M.cond_line(c)
                if site is not None and p1 and p2:
                    need = K.clear_width_b(depth, nd_eff * 1000)
                    mind = min(float(site.dist_b[site.rc(*p)]) if 0 <= site.rc(*p)[0] < site.H and 0 <= site.rc(*p)[1] < site.W else 99.0
                               for p in (p1, p2, ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)))
                    if need and mind < need[0]:
                        b_short.append(f"{c} {mind:.1f}<{need[0]:.1f} m")
                cost += pipe_cost(nd if nd else DN[-1], v["len"], depth)
            grades.append({"grade": f"+{k} 档", "params": {"pipes": len(ch), "len_m": round(L), "max_d_before": dmax}, "cost_usd": round(cost),
                           "inp_patch": {"xsections": patch}})
            if k == 1:
                comp = [item("WA726-1-162", "A", "review" if cover_fail else "pass", f"覆土 < 1.2 m 的节点 {len(set(cover_fail))} 个（需加保护）" if cover_fail else "覆土满足", "最小覆土 1.2 m"),
                        item("WA726-1-099~118", "A", "review" if b_short else "pass", ("原位换管，净距不足：" + "；".join(b_short[:3])) if b_short else "满足", "与建筑净距 B"),
                        item("WA726-1-119", "A", "unknown", "缺现状公用管线位置（新增资料请求：公用管线走廊图）", "与现状管线净距 ≥ 0.5 m"),
                        item("WA726-1-151/152", "A", "review", "扩容后流速 0.75–2.5 m/s：一维评估后复核", "流速范围"),
                        item("C_traffic", "C", "review", "沿路开挖换管，需交通导改方案", "施工影响")]
        c = {"kind": "pipe_upsize", "family": "输送", "permanence": "永久", "anchor": {"conduits": ch, "from_node": M.cond[ch[0]]["n1"], "to_node": M.cond[ch[-1]]["n2"]},
             "hotspot": h, "hs_dist_m": round(d), "grades": grades, "fidelity": ["F1", "F3"], "compliance": comp,
             "data_flags": ["公用管线走廊图"], "excl": [f"conduit:{x}" for x in ch], "cost_basis": "开挖换管按管径占位单价 + 路面恢复占位；埋深 > 4 m ×1.5（占位）",
             "xy": M.xy.get(M.cond[ch[0]]["n1"])}
        c["status"] = status_of(comp); cands.append(c)
    return cands, rej


# ---------------- 2 现状泵站扩容 ----------------
def ps_upgrade(M, H, site):
    """以湿井（STORAGE）为进水端的现状泵：加 1 台同型 / 曲线流量 ×1.25 / ×1.5（设计第 2 节）"""
    st = {}
    for p in M.pumps:
        if p["n1"] in M.storage:
            st.setdefault(p["n1"], []).append(p)
    cands = []
    for ww, ps in st.items():
        x, y = M.xy.get(ww, (None, None))
        d, h = near_hs(x, y, H, r=1e9) if x is not None else (None, None)
        q = [max(M.curves.get(p["curve"], [0, 0])[1::2] or [0]) for p in ps]          # 曲线流量（PUMP3：扬程-流量对，取最大流量）
        qtot = sum(q)
        area = (M.curves.get(f"{ww}_Curve") or [0, 0])[1] if f"{ww}_Curve" in M.curves else None
        rng = max(p["on"] - p["off"] for p in ps) if ps else 0
        dep = M.depth.get(ww, {}); full = dep.get("max_d", 0) >= M.maxd.get(ww, 99) - 0.1
        grades = []
        for tag, f, add in (("加 1 台同型泵", 1.0, True), ("全部换大：流量 ×1.25", 1.25, False), ("全部换大：流量 ×1.5", 1.5, False)):
            qn = qtot + max(q) if add else qtot * f
            each = max(q) if add else max(q) * f
            vneed = 0.25 * each * 600                                   # V = 0.25·Q·T，T = 10 min（每小时 6 次启动，WA726-1-295/296）
            vhave = (area or 0) * rng
            ww_add = max(0.0, vneed - vhave)
            n_new = 1 if add else len(ps)                                    # 加 1 台：只买 1 台；换大：全部换
            cost = n_new * (PH["pump_per_036"] * each / 0.36 + PH["elec_per_pump"])
            cost += ww_add * PH["wetwell_m3"]
            grades.append({"grade": tag, "params": {"q_total_m3s": round(qn, 3), "q_each_m3s": round(each, 3), "wetwell_add_m3": round(ww_add, 1)},
                           "cost_usd": round(cost), "inp_patch": {"pumps": {"station": ww, "add_copy_of": ps[int(np.argmax(q))]["id"] if add else None, "scale_flow": None if add else f}}})
        comp = [item("WA726-1-309", "A", "pass" if len(ps) >= 2 else "fail", f"{len(ps)} 台", "每座泵站至少 2 台"),
                item("WA726-1-296", "A", "review" if any(g["params"]["wetwell_add_m3"] > 0 for g in grades) else "pass",
                     f"湿井面积 {area} m²、启停水位差 {rng:.2f} m；不够的档已计入湿井扩建", "湿井有效容积 V = 0.25QT"),
                item("WA726-1-380", "A", "review", "出水管流速 1.5–2.5 m/s：一维评估后复核，超过则加并联压力管", "出水管流速"),
                item("C_power", "C", "unknown", "泵站供电容量无资料（新增资料请求）", "供电容量")]
        c = {"kind": "ps_upgrade", "family": "抽排", "permanence": "永久", "anchor": {"station": ww, "pumps": [p["id"] for p in ps]}, "hotspot": h, "hs_dist_m": round(d) if d else None,
             "grades": grades, "fidelity": ["F1", "F3"], "compliance": comp, "data_flags": ["泵站供电容量"], "excl": [f"station:{ww}"],
             "cost_basis": "每台泵按 0.36 m³/s 15 万 USD 折算 + 电气 8 万（占位）；湿井扩建 1,500 USD/m³（占位）", "xy": (x, y),
             "baseline": {"wetwell_full": bool(full), "q_total_m3s": round(qtot, 3)}}
        c["status"] = status_of(comp); cands.append(c)
    return cands, []


# ---------------- 3 土工格室临时蓄水 ----------------
def geocell(M, H, site, min_vol=100.0, radius=200.0, h_mod=1.0):
    """溢流 ≥ 100 m³、热点 / 高后果点 200 m 内的节点，200 m 内找公共开敞用地放浅埋格室（设计第 3 节）"""
    cands, rej = [], []
    if site is None:
        return cands, rej
    for n, f in sorted(M.flood.items(), key=lambda kv: -kv[1]["vol_m3"]):
        if f["vol_m3"] < min_vol or n not in M.xy or n not in M.rim:
            continue
        x, y = M.xy[n]; d, h = near_hs(x, y, H)
        if not h:
            continue
        need_a = min(f["vol_m3"] * 1.05 / (h_mod * 0.95), 2000 * 1.05 / 0.95)
        found = site.find_site(x, y, radius, max(300.0, need_a * 0.25), 1.0, max_sites=1, min_width=5.0)
        if not found:
            rej.append({"kind": "geocell", "node": n, "why": "200 m 内没有 ≥ 300 m² 的公共开敞用地"}); continue
        dist, cells, cx, cy = found[0]
        A = len(cells) * 4.0
        zg = site.ground(cx, cy) or M.rim[n]
        crest = M.rim[n] - 0.3
        top = min(zg - 0.5, crest - 0.2); bottom = top - h_mod
        L = site.road_dist((x, y), (cx, cy)) or dist * 1.3
        if crest - top < 0.1 + 0.002 * L:
            rej.append({"kind": "geocell", "node": n, "why": f"重力进不去：堰顶 {crest:.2f}，格室顶 {top:.2f}，连接 {L:.0f} m"}); continue
        grades = []
        for V in (200, 500, 1000, 2000):
            Vg = V * 1.05
            if Vg > A * h_mod * 0.95 or V > f["vol_m3"] * 1.5:
                continue
            area = Vg / (h_mod * 0.95)
            grades.append({"grade": f"{V} m³", "params": {"vol_m3": V, "area_m2": round(area), "height_m": h_mod, "top_m": round(top, 2), "conn_m": round(L)},
                           "cost_usd": round(Vg * PH["geocell_m3_event"] + L * PH["pipe_m"][0.4]),
                           "cost_usd_permanent": round(Vg * PH["geocell_m3_perm"] + L * PH["pipe_m"][0.4]),
                           "inp_patch": {"storage": {"id": None, "node": n, "invert": round(bottom, 2), "depth": h_mod, "area": round(area * 0.95, 1), "x": round(cx, 1), "y": round(cy, 1),
                                                     "weir_crest_rel": round(crest - M.inv[n], 2), "orifice_d": 0.15}}})
        if not grades:
            rej.append({"kind": "geocell", "node": n, "why": f"可用地 {A:.0f} m² 放不下 200 m³"}); continue
        lm = site.land_mix(cells)
        comp = [item("WA726-1-122", "A", "pass", "B.2 土工格室离线调蓄，允许形式", "调蓄形式"),
                item("WA726-1-129", "A", "pass", "容积已加 5 % 积沙余量", "积沙余量"),
                item("WA726-2-130", "A", "unknown", "地下水位无资料（M-12）", "地下水低于模块底 ≥ 0.5 m"),
                item("WA726-2-027", "A", "unknown", "无地勘（CBR）", "基层 CBR ≥ 3 %"),
                item("WA726-2-026", "A", "review" if lm.get("无地块记录", 0) > 0.5 else "pass", f"用地构成 {lm}；停车场 / 道路旁需防重型机械", "上方禁止施工机械"),
                item("WA726-1-127", "A", "review", "用地需主管部门或业主许可", "用地许可")]
        c = {"kind": "geocell", "family": "调蓄", "permanence": "临时", "anchor": {"node": n, "site_xy": [round(cx, 1), round(cy, 1)]}, "hotspot": h, "hs_dist_m": round(d),
             "overflow_m3": round(f["vol_m3"]), "grades": grades, "fidelity": ["F1", "F3"], "compliance": comp, "data_flags": ["M-12 地下水位", "地勘 CBR"],
             "excl": [f"node:{n}:storage", f"site:{round(cx, -1)},{round(cy, -1)}"], "xy": (x, y),
             "cost_basis": "按临时布设：每次事件布设 + 撤除 60 USD/m³（占位）；永久建设 300 USD/m³ 另列 cost_usd_permanent（占位）；连接管按 DN400 占位单价"}
        c["status"] = status_of(comp); cands.append(c)
    return cands, rej


# ---------------- 4 增设雨水口 ----------------
def inlet_add(M, H, site, patches, max_conn=35.0):
    """基线二维积水片（≥ 0.15 m、≥ 100 m²）内或 35 m 内的雨水口饱和、下游接入管有余量时，加雨水口（设计第 4 节）"""
    xt = (M.pilot or {}).get("xchg_type", {})
    inl = [n for n, t in xt.items() if t == "inlet" and n in M.xy]
    struct = [n for n in M.xy if n in M.inv and n in M.out_of]
    if not inl:
        return [], []
    from scipy.spatial import cKDTree
    ti = cKDTree(np.array([M.xy[n] for n in inl])); ts = cKDTree(np.array([M.xy[n] for n in struct]))
    cands, rej = [], []
    for p in patches:
        if p["hmax_m"] < 0.15 or p["area_m2"] < 100:
            continue
        x, y = p["x"], p["y"]
        d, h = near_hs(x, y, H)
        if not h:
            continue
        ids = [inl[i] for i in ti.query_ball_point((x, y), max(max_conn, math.sqrt(p["area_m2"] / math.pi) + max_conn))]
        sat = [n for n in ids if p["hmax_m"] >= 0.12]                 # 积水深过堰流 / 孔流过渡水深（WA726-1-567）即按公式上限进水
        spare = lambda n: all(M.surch.get(c, {}).get("cap_limited", 0) < 0.17 for c in M.out_of.get(n, []))   # noqa: E731
        sat_spare = [n for n in sat if spare(n)]
        tgt_d, tgt_i = ts.query((x, y), k=8)
        tgt = None
        for dd, ii in zip(np.atleast_1d(tgt_d), np.atleast_1d(tgt_i)):
            n = struct[ii]
            if dd > max_conn:
                break
            od = max((M.cond[c]["d"] for c in M.out_of.get(n, [])), default=9)
            if od <= 0.5 and spare(n) and M.rim.get(n, 0) - M.inv.get(n, 0) <= 2.8:
                tgt = (n, dd); break
        if not sat_spare and tgt is None:
            rej.append({"kind": "inlet_add", "patch": p["id"], "why": "附近雨水口不饱和或下游没有余量，35 m 内也没有可接的 ≤ 500 mm 管"}); continue
        grades = []
        if sat_spare:
            grades.append({"grade": f"饱和雨水口旁各加 1 个（{len(sat_spare)} 个）", "params": {"n": len(sat_spare)}, "cost_usd": round(len(sat_spare) * (PH["inlet_each"] + 5 * PH["inlet_conn_m"])),
                           "inp_patch": {"inlets": [{"near": n, "x": M.xy[n][0] + 1.0, "y": M.xy[n][1] + 1.0, "to": n, "len": 5.0} for n in sat_spare]}})
            grades.append({"grade": f"格栅换大（{len(sat_spare)} 个，进水面积 ×2）", "params": {"n": len(sat_spare)}, "cost_usd": round(len(sat_spare) * PH["grate_upgrade_each"]),
                           "inp_patch": {"grate_scale": {n: 2.0 for n in sat_spare}}})
        if tgt is not None:
            for k in (2, 4):
                grades.append({"grade": f"积水最低处加 {k} 个，接 {tgt[0]}", "params": {"n": k, "conn_m": round(tgt[1])}, "cost_usd": round(k * PH["inlet_each"] + max(tgt[1], 5) * PH["inlet_conn_m"]),
                               "inp_patch": {"inlets": [{"near": None, "x": x + 2.0 * j, "y": y, "to": tgt[0], "len": max(round(tgt[1]), 5)} for j in range(k)]}})
        comp = [item("WA726-1-172", "A", "pass" if tgt is not None or sat_spare else "review", "只接 ≤ 500 mm 的管", "接入管径 ≤ 500 mm"),
                item("WA726-2-112", "A", "pass", "连接管 ≤ 35 m", "连接管长度"),
                item("WA726-1-570", "A", "review", "每井 ≤ 4 个雨水口：施工图核对", "每井雨水口数"),
                item("WA726-1-572", "A", "review", "放在路缘、不在车轮轨迹上：施工图定位", "格栅位置"),
                item("M-5", "A", "unknown", "格栅型号未知，缺省 635 × 635、开孔 50 %", "格栅型号")]
        c = {"kind": "inlet_add", "family": "收水", "permanence": "永久", "anchor": {"patch": p["id"], "inlets_saturated": sat_spare}, "hotspot": h, "hs_dist_m": round(d),
             "patch": {"hmax_m": p["hmax_m"], "area_m2": p["area_m2"], "vol_m3": p["vol_peak_m3"]}, "grades": grades, "fidelity": ["F2+", "F3"],
             "fidelity_note": "一维看不出效果：要用洼坑代理扩展（雨水口公式进管）或二维评估；格栅换大一档还需耦合程序支持逐节点格栅面积",
             "compliance": comp, "data_flags": ["M-5 格栅型号"], "excl": [f"patch:{p['id']}"], "xy": (x, y),
             "cost_basis": "每个雨水口 + 截污井 8,000 USD、连接管 400 USD/m、换格栅 3,000 USD（占位）"}
        c["status"] = status_of(comp); cands.append(c)
    return cands, rej


# ---------------- 5 渗井 ----------------
def soakaway(M, H, site, patches):
    """WA726-1-229：一般不允许。按积水片生成，但默认禁用（措施类别许可关闭），缺地下水与渗透系数无法定尺寸（设计第 5 节）"""
    cands = []
    for p in patches:
        if p["hmax_m"] < 0.15 or p["area_m2"] < 100:
            continue
        d, h = near_hs(p["x"], p["y"], H)
        if not h:
            continue
        n0 = max(1, min(4, round(p["area_m2"] / 500)))
        grades = [{"grade": f"{k} 个", "params": {"n": k, "storage_depth_m": 2.0, "ksat_mmh": None}, "cost_usd": k * PH["soakaway_each"],
                   "inp_patch": {"soakaway": {"n": k, "x": p["x"], "y": p["y"], "ksat_mmh": None}}} for k in sorted({1, 2, 4, n0})]
        comp = [item("WA726-1-229", "A", "review", "新建系统一般不允许，须研究后逐案考虑并与 ADM 商定", "渗井一般不允许"),
                item("WA726-1-231", "A", "unknown", "井底高出最高地下水位 ≥ 1.0 m：无地下水资料（M-12），RD-A 为填海岛、地下水高", "地下水净距"),
                item("M-13", "A", "unknown", "渗透系数无资料：不给缺省值，无法定尺寸", "渗透系数")]
        c = {"kind": "soakaway", "family": "下渗", "permanence": "永久", "anchor": {"patch": p["id"]}, "hotspot": h, "hs_dist_m": round(d), "grades": grades,
             "fidelity": ["F1（资料到位后）", "F3"], "compliance": comp, "data_flags": ["M-12 地下水位", "M-13 渗透系数"], "excl": [f"patch:{p['id']}:soak"],
             "enabled_default": False, "xy": (p["x"], p["y"]), "cost_basis": "每个渗井 25,000 USD（占位）"}
        c["status"] = status_of(comp); cands.append(c)
    return cands, []


# ---------------- 6 防潮拍门 ----------------
def flap_gate(M, H, site):
    """没有拍门、且管底低于 HAT + 0.5 m 的出水口（设计第 6 节）。RD-A 的 F 系列已给排海口加了拍门，预计没有候选"""
    cands = []
    for o, r in M.outfalls.items():
        gated = len(r) > 4 and r[-1].upper() == "YES" if r[2] in ("FIXED", "TIDAL", "TIMESERIES") else (len(r) > 3 and r[3].upper() == "YES")
        if gated or float(r[1]) > HAT + 0.5:
            continue
        x, y = M.xy.get(o, (None, None))
        d, h = near_hs(x, y, H, r=1e9) if x is not None else (None, None)
        comp = [item("WA726-1-215", "A", "review", "止回阀井室盖板须高于 HAT 1.3 m", "井室高程"), item("WA726-1-431", "A", "pass", "拍门 / 鸭嘴阀 / 锥形阀", "型式")]
        c = {"kind": "flap_gate", "family": "出水口", "permanence": "永久", "anchor": {"outfall": o}, "hotspot": h, "hs_dist_m": round(d) if d else None,
             "grades": [{"grade": "加拍门", "params": {}, "cost_usd": PH["flap_per_m_dn"], "inp_patch": {"outfall_gate": o}}],
             "fidelity": ["F1", "F3"], "compliance": comp, "data_flags": [], "excl": [f"outfall:{o}"], "xy": (x, y), "cost_basis": "按管径 4 万 USD（占位）"}
        c["status"] = status_of(comp); cands.append(c)
    return cands, []


# ---------------- 7 雨前塘预降水位 ----------------
def pond_drawdown(M, H, site, lead_h=24.0):
    """有出流（泵或出口）的现状塘：预降 0.25 / 0.5 / 1.0 m（设计第 7 节）。湿井（接泵、面积 < 500 m²）不算塘；RD-A 没有塘"""
    cands = []
    for s, r in M.storage.items():
        curve = M.curves.get(r[5] if len(r) > 5 else "", [])
        area = max(curve[1::2]) if curve else (float(r[5]) if len(r) > 5 and r[4] == "FUNCTIONAL" else 0.0)
        if area < 500:                                                     # 湿井（几十 m²）不算塘
            continue
        x, y = M.xy.get(s, (None, None)); d, h = near_hs(x, y, H, r=1e9) if x is not None else (None, None)
        init = float(r[3]) if len(r) > 3 else 0.0
        grades = [{"grade": f"预降 {dd} m", "params": {"drop_m": dd}, "cost_usd": round(dd * area * PH["drawdown_event_per_m3"]), "inp_patch": {"storage_init": {s: max(0.0, init - dd)}}}
                  for dd in (0.25, 0.5, 1.0) if init - dd >= 0]
        if not grades:
            continue
        comp = [item("WA726-1-238", "A", "pass", "7 天内回到景观水位", "回升时间"), item("WA726-1-256", "A", "review", "湿塘溶解氧 ≥ 5 ppm：预降过深影响景观和生态", "水质"),
                item("C_operation", "C", "review", f"需雨情预报（提前 {lead_h:g} h）和值守人员，运行职责待客户确认", "运行条件")]
        c = {"kind": "pond_drawdown", "family": "运行", "permanence": "临时", "anchor": {"pond": s}, "hotspot": h, "hs_dist_m": round(d) if d else None, "grades": grades,
             "fidelity": ["F1", "F3"], "compliance": comp, "data_flags": [], "excl": [f"pond:{s}:op"], "xy": (x, y), "cost_basis": "每次事件按排水量 0.4 USD/m³（泵电费 + 人工，占位）"}
        c["status"] = status_of(comp); cands.append(c)
    return cands, []
