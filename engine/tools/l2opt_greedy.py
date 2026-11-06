# -*- coding: utf-8 -*-
"""L2+suggested 优化试点 · 一维贪心组合（按热点分组、每轮每组加一项、整组实算），得到成本–效果路径。

目标 J（越小越好）= Σ 雨 Σ 热点 （热点 500 m 内井溢流量 / 基线同值），只算基线 > 500 m³ 的热点；
每项措施的「效率」= ΔJ / 占位造价。效果优先：一直加到没有哪组还能降 ≥ min_gain 为止，路径上每一轮就是成本–效果曲线的一个点。
候选池（第一阶段有效果的井，每井最多 3 项）：最大容积的重力塘址、泵 → 新塘（1× 泵档、2× 容积）、泵 → 最近现有塘（1× 泵档）；
已选的井可以「升档」（换成 1.5× 泵档 / 更大的塘）。新塘占地不能和已选的新塘重叠。
每轮：池里每项单独加到当前组合上，先跑 1 h 雨排序，每组前 TOPK 项再跑 24 h 雨，按两场合计的效率每组选一项；
选中的几项一起加进去，整组再实算一次（含相互影响），作为这一轮的结果。
用法：python tools/l2opt_greedy.py --run RB01_H61 [--procs 8] [--max-steps 15]
洼坑项（2026-10-24，--pits）：J 再加 Σ 雨 Σ 热点（洼坑代理的 a30 + a10 / 基线同值），地面泵候选（surface_ps，锚点 "PIT:<洼坑>"）
和一维候选同池竞争；地面泵不改一维模型，不用跑 SWMM，直接用当前组合的一维溢流算洼坑项（tools/l2s_pits.py）。
产物：out/l2opt/<运行>/greedy_path.json（每轮：选了什么、累计造价、两场雨各热点溢流、J）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
import l2opt_screen as SC  # noqa: E402
import l2s_pits as PITS  # noqa: E402
from workdir import WORK  # noqa: E402

GROUPS = [("ADM-205", "ADM-206"), ("ADM-101", "ADM-291"), ("ADM-140",), ("ADM-293",)]
TOPK = 4
PER_KIND = 2         # 每组每类措施进 24 h 评估的上限（重力 / 泵 → 新塘 / 泵 → 出水口 / 泵 → 现有塘）


def stage1_rank(o):
    """第一阶段（理想截流）结果 → 井的得分（两场雨 × 各热点 500 m 内溢流量减少 / 基线，基线 > 500 m³ 的热点）"""
    d = json.loads((o / "screen_stage1.json").read_text(encoding="utf-8")); B = d["base"]
    rows = []
    for w, v in d["items"].items():
        sc = 0.0
        for s in d["storms"]:
            if s not in v:
                continue
            for k, b in B[s]["zones"].items():
                if k.endswith("|500") and b > 500:
                    sc += (b - v[s]["zones"][k]) / b
        rows.append({"well": w, "score": sc})
    rows.sort(key=lambda r: -r["score"])
    (o / "stage1_ranked.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def jscore(metrics, base, hs):
    j = 0.0
    for s, m in metrics.items():
        for h in hs:
            b = base[s]["zones"][f"{h}|500"]
            if b > 500:
                j += m["zones"][f"{h}|500"] / b
        j += PITS.pit_j(m, base[s], hs)
    return j


def gscore(metrics, base, hs_g):
    return jscore(metrics, base, hs_g)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="RB01_H61"); ap.add_argument("--storms", default="S96D1,S96D24")
    ap.add_argument("--procs", type=int, default=8); ap.add_argument("--max-steps", type=int, default=15)
    ap.add_argument("--score-min", type=float, default=0.05, help="第一阶段得分下限（进候选池的井）")
    ap.add_argument("--resume", action="store_true", help="接着 greedy_path.json 的最后一轮继续")
    ap.add_argument("--min-gain", type=float, default=0.005, help="一组一轮至少降这么多 J 才加")
    ap.add_argument("--prefix", default="RB01", help="模型名前缀：RB01 / RDA（2026-10-20 起 RD-A 共用这套脚本）")
    ap.add_argument("--groups", default="", help="热点分组：组内用 + 连、组间用 , 分（缺省 = RB-01 的 4 组）")
    ap.add_argument("--base-state", default="L2P")
    ap.add_argument("--max-spill", type=float, default=300.0, help="塘满溢出（一维 PND_ / NP_ 节点溢流）比当前组合多这么多 m³ 就不选（2026-10-19：避免再把 PO-3 打满溢到地面）")
    ap.add_argument("--pits", action="store_true", help="J 加洼坑项、候选池加地面泵（需要运行目录里的 pits.json 和 surface_ps 候选，2026-10-24）")
    a = ap.parse_args(argv)
    global GROUPS
    if a.groups:
        GROUPS = [tuple(g.split("+")) for g in a.groups.split(",")]
    import geopandas as gpd
    from shapely.geometry import shape
    o = ROOT / "out" / "l2opt" / a.run
    D = json.loads((o / "candidates.json").read_text(encoding="utf-8"))
    G = {f["properties"]["id"]: f["geometry"] for f in json.loads((o / "candidates.geojson").read_text(encoding="utf-8"))["features"]}
    C = {c["id"]: c for c in D["candidates"]}
    rank = stage1_rank(o)
    wells = [r["well"] for r in rank if r["score"] >= a.score_min]
    storms = a.storms.split(",")                 # 第一场排序，其余各场复核（2026-10-26 改：原来固定两场；档 1 三场、AECOM 61/1h 一场）
    s1, rest = storms[0], storms[1:]
    base_paths = {s: str(WORK / f"{a.prefix}_{s}_{a.base_state}.inp") for s in storms}
    btxt = pathlib.Path(base_paths[s1]).read_text(encoding="utf-8")
    H = __import__("hotspot_src").load(32640)
    hs = [h for g in GROUPS for h in g]
    H = H[H.hotspot_id.isin(hs)]
    Z = SC.Zones(btxt, {r.hotspot_id: (r.geometry.x, r.geometry.y) for r in H.itertuples()})
    sdepth = {}
    for ln in re.search(r"\[STORAGE\]\n(.*?)\n\n", btxt, re.S).group(1).splitlines():
        q = ln.split()
        if len(q) > 3 and not ln.startswith(";"):
            sdepth[q[0]] = float(q[2])
    grp_of = {h: gi for gi, g in enumerate(GROUPS) for h in g}

    # ---- 候选池
    def menu(w, upgrade=False):
        cs = [c for c in D["candidates"] if c.get("well") == w]
        if w.startswith("PIT:"):                              # 地面泵：各档都进池（不跑 SWMM，评估便宜）；已选的只给更高档
            cur_q = C[chosen[w]]["q_m3s"] if w in chosen else 0.0
            return [c for c in cs if c["kind"] == "surface_ps" and c["q_m3s"] > cur_q]
        out = []
        gr = [c for c in cs if c["kind"] in ("gravity", "tank_gravity")]
        if gr:
            out.append(max(gr, key=lambda c: (c.get("storage") != "地下调蓄池", c["pond_vol_m3"], -c["pipe_m"])))
        pn = sorted([c for c in cs if c["kind"] in ("pump_newpond", "tank_pumped")], key=lambda c: (c["n_pumps"], c["pond_vol_m3"]))
        pp = sorted([c for c in cs if c["kind"] == "pump_pond"], key=lambda c: (c["pipe_m"], c["n_pumps"]))
        if pn:
            tiers = sorted({c["n_pumps"] for c in pn})
            n = tiers[-1] if upgrade else tiers[len(tiers) // 2]
            # 同档先挑地面塘（便宜），没有再挑地下调蓄池；同类取最大容积（2026-10-21）
            out.append(max([c for c in pn if c["n_pumps"] == n], key=lambda c: (c.get("storage") != "地下调蓄池", c["pond_vol_m3"])))
        px = sorted([c for c in cs if c["kind"] in ("pump_outfall", "ps_cluster")], key=lambda c: c["n_pumps"])
        if px and any(c["kind"] == "ps_cluster" for c in px):
            # v3 合并泵站：给两个选项 —— 只截这一口井的最便宜方案；截多口井、每美元截流量最大的方案（2026-10-23）
            single = [c for c in px if len(c.get("wells") or []) <= 1]
            multi = [c for c in px if len(c.get("wells") or []) > 1]
            if single:
                out.append(min(single, key=lambda c: c["cost_usd"]))
            if multi:
                out.append(max(multi, key=lambda c: c["overflow_m3"] / c["cost_usd"]))
        elif px:
            tiers = sorted({c["n_pumps"] for c in px})
            n = tiers[-1] if upgrade else tiers[len(tiers) // 2]
            # 同档取造价最低的去向（2026-10-21 修：原来取生成顺序第一个 = 出水口 31，新建排海口的选项永远进不了池子）
            out.append(min((c for c in px if c["n_pumps"] == n), key=lambda c: c["cost_usd"]))
        if pp:
            d0 = pp[0]["dest"]; tiers = sorted({c["n_pumps"] for c in pp if c["dest"] == d0})
            n = tiers[-1] if upgrade else tiers[len(tiers) // 2]
            out.append(next(c for c in pp if c["dest"] == d0 and c["n_pumps"] == n))
        return out

    poly = {}

    def footprint(cid):
        if cid not in poly:
            c = C[cid]
            poly[cid] = shape(G[cid]) if c["kind"] in ("gravity", "pump_newpond", "tank_gravity", "tank_pumped") and cid in G else None
        return poly[cid]

    def conflicts(cid, chosen):
        # v3（2026-10-23）：合并泵站一次截好几口井，同一口井不能被两项措施截流
        mine = set(C[cid].get("wells") or [C[cid].get("well")])
        anchor = C[cid].get("well")
        if any(set(C[x].get("wells") or [C[x].get("well")]) & mine for k, x in chosen.items() if k != anchor):
            return True
        p = footprint(cid)
        if p is None:
            return False
        return any(footprint(x) is not None and footprint(x).intersects(p) for x in chosen.values() if x != cid)

    chosen = {}                 # 井 → 候选编号
    path = []
    # 基线
    res = SC.run_jobs([("BASE", s, [], sdepth) for s in storms], base_paths, a.procs, log=lambda *x: None)
    basenf = {r[1]: r[2] for r in res}
    PT = PITS.PitTerm(o / "pits.json", C) if a.pits else None

    def mets(s, nf, ids, basenf_=None):
        m = Z.metrics(nf, basenf_)
        return PT.add(m, s, nf, ids) if PT else m
    base = {s: mets(s, basenf[s], []) for s in storms}
    cur = {s: base[s] for s in storms}
    cur_nf = dict(basenf)
    if PT:
        wells += sorted({c["well"] for c in D["candidates"] if c["kind"] == "surface_ps"})
        print("洼坑项基线（m²）：" + "；".join(f"{s} " + ", ".join(f"{h} {v:.0f}" for h, v in base[s]["pit"].items() if v >= PITS.PIT_MIN_M2) for s in storms))
    J0 = jscore(cur, base, hs)
    path.append({"step": 0, "added": [], "cost_usd": 0, "J": round(J0, 4), "zones": {s: cur[s]["zones"] for s in cur}, "total": {s: cur[s]["total"] for s in cur},
                 "pond_spill": {s: cur[s]["pond_spill"] for s in cur}})
    step0 = 1
    if a.resume and (o / "greedy_path.json").exists():                     # 接着上次的路径走（保留已选的措施与每轮记录）
        old = json.loads((o / "greedy_path.json").read_text(encoding="utf-8"))
        last = old["path"][-1]
        path = old["path"]
        chosen = {C[i]["well"]: i for i in last.get("chosen", [])}
        cur = {s: {"zones": last["zones"][s], "total": last["total"][s], "pond_spill": (last.get("pond_spill") or {}).get(s, base[s]["pond_spill"])} for s in storms}
        step0 = last["step"] + 1
        print(f"接着第 {last['step']} 轮：已选 {len(chosen)} 项，J {last['J']}")
    print(f"基线 J = {J0:.3f}（{sum(1 for s in cur for h in hs if base[s]['zones'][f'{h}|500'] > 500)} 项），候选井 {len(wells)} 口")

    def measures_of(ids):
        return [SC.prep(dict(C[i]), Z, D) for i in ids if C[i]["kind"] != "surface_ps"]

    t0 = time.time()
    for step in range(step0, a.max_steps + 1):
        pool = []
        for w in wells:
            opts = menu(w, upgrade=w in chosen)
            for c in opts:
                if chosen.get(w) == c["id"] or conflicts(c["id"], chosen):
                    continue
                pool.append(c["id"])
        if not pool:
            print("候选池空了"); break
        cur_ids = list(chosen.values())

        def with_(cid):
            w = C[cid]["well"]
            return [i for k, i in chosen.items() if k != w] + [cid]

        # ① 1 h 雨排序（地面泵不改一维，直接用当前组合的溢流）
        sump = {cid for cid in pool if C[cid]["kind"] == "surface_ps"}
        jobs = [(cid, s1, measures_of(with_(cid)), sdepth) for cid in pool if cid not in sump]
        r1 = {r[0]: r for r in SC.run_jobs(jobs, base_paths, a.procs, log=lambda *x: None)} if jobs else {}
        for cid in sump:
            r1[cid] = (cid, s1, cur_nf[s1])
        cand = []
        for cid, r in r1.items():
            if r[2] is None:
                continue
            m = mets(s1, r[2], with_(cid), basenf[s1])
            if m["pond_spill"] - cur[s1]["pond_spill"] > a.max_spill:
                continue
            gi = grp_of[C[cid]["hotspot"]]
            dj = gscore({s1: cur[s1]}, base, GROUPS[gi]) - gscore({s1: m}, base, GROUPS[gi])
            dcost = C[cid]["cost_usd"] - (C[chosen[C[cid]["well"]]]["cost_usd"] if C[cid]["well"] in chosen else 0)
            cand.append((gi, cid, dj, max(dcost, 1.0), m))
        # ② 每组前 TOPK 项跑 24 h
        top = []
        for gi in range(len(GROUPS)):
            # 不要求 1 h 雨下为正：ADM-293 这类主要在 24 h 长历时雨下积水的热点，1 h 下降得少甚至略升（2026-10-19 修）
            # 每类措施最多 PER_KIND 项进 24 h（2026-10-19 修：重力塘便宜，按 1 h 效率总占满名额，但在 24 h 下塘小溢流被拒；
            # 真正管用的泵送方案因为贵排不进前几名，ADM-293 一直选不上）
            g, used = [], {}
            for c_ in sorted([c for c in cand if c[0] == gi], key=lambda c: -c[2] / c[3]):
                k_ = C[c_[1]]["kind"]
                if k_ == "surface_ps" or used.get(k_, 0) < PER_KIND:
                    g.append(c_); used[k_] = used.get(k_, 0) + 1
            top += g
        if not top:
            print(f"第 {step} 轮：1 h 雨下没有能再降的"); break
        if not rest:                                                       # 只有一场控制雨：不复核，按排序雨全部候选挑
            top = cand
        r2 = {}
        for s in rest:
            r2[s] = {r[0]: r for r in SC.run_jobs([(c[1], s, measures_of(with_(c[1])), sdepth) for c in top if c[1] not in sump], base_paths, a.procs, log=lambda *x: None)}
            for c in top:
                if c[1] in sump:
                    r2[s][c[1]] = (c[1], s, cur_nf[s])
        picks = []
        for gi in range(len(GROUPS)):
            best = None
            for g_, cid, dj1, dc, m1 in [c for c in top if c[0] == gi]:
                dj, ok = dj1, True
                for s in rest:
                    r = r2[s].get(cid)
                    if r is None or r[2] is None:
                        ok = False; break
                    m2 = mets(s, r[2], with_(cid), basenf[s])
                    if m2["pond_spill"] - cur[s]["pond_spill"] > a.max_spill:
                        ok = False; break
                    dj += gscore({s: cur[s]}, base, GROUPS[gi]) - gscore({s: m2}, base, GROUPS[gi])
                if ok and dj >= a.min_gain and (best is None or dj / dc > best[2] / best[3]):
                    best = (gi, cid, dj, dc)
            if best:
                picks.append(best)
        if not picks:
            print(f"第 {step} 轮：没有哪组还能降 ≥ {a.min_gain}"); break
        # 加进去（同组只一项；占地互不冲突）
        for gi, cid, dj, dc in picks:
            if not conflicts(cid, chosen):
                chosen[C[cid]["well"]] = cid
        ids = list(chosen.values())
        rr = SC.run_jobs([("CUR", s, measures_of(ids), sdepth) for s in storms], base_paths, a.procs, log=lambda *x: None)
        cur = {r[1]: mets(r[1], r[2], ids, basenf[r[1]]) for r in rr}
        cur_nf = {r[1]: r[2] for r in rr}
        cost = sum(C[i]["cost_usd"] for i in ids)
        J = jscore(cur, base, hs)
        path.append({"step": step, "added": [{"id": cid, "well": C[cid]["well"], "kind": C[cid]["kind"], "hotspot": C[cid]["hotspot"], "dJ_est": round(dj, 4),
                                              "dcost_usd": round(dc)} for gi, cid, dj, dc in picks],
                     "chosen": ids, "cost_usd": round(cost), "J": round(J, 4), "zones": {s: cur[s]["zones"] for s in cur},
                     "total": {s: cur[s]["total"] for s in cur}, "worse": {s: cur[s].get("worse_m3") for s in cur},
                     "pit": {s: cur[s].get("pit") for s in cur},
                     "pond_spill": {s: cur[s]["pond_spill"] for s in cur}})
        print(f"第 {step} 轮（{(time.time() - t0) / 60:.0f} min）：加 {', '.join(p[1] for p in picks)} → {len(ids)} 项，累计 {cost / 1e6:.2f} M USD，J {J:.3f}（基线 {J0:.3f}）")
        (o / "greedy_path.json").write_text(json.dumps({"run": a.run, "storms": storms, "J0": J0, "groups": GROUPS, "base": base, "path": path},
                                                       ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"→ {o / 'greedy_path.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
