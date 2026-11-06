# -*- coding: utf-8 -*-
"""L2+suggested · 贪心之后的「剪枝 + 替换」（2026-10-21，用户：「按照你的建议来」；方法说明见 docs/L2S优化方法_贪心算法依据与改进_20261021.md 第五节）。

贪心只往前加、不回头：早选的措施后来可能多余，或者有更便宜的同井替代。这里对贪心最后一轮的组合做两遍局部改进（同贪心的一维评估、同一个 J）：
  ① 剪枝：逐项试「去掉」；去掉后 J 涨得最少、省钱最多的先去，每去一项整组复算一次，累计 J 不超过原值 × (1 + --tol)
  ② 替换：逐项试「换成同一口井的其他候选」（换去向：出水口 31 / 新建排海口 / 新塘 / 地下池 / 重力；换泵档；换塘容），
          只看比原项便宜的；按省钱多少排序，逐个接受（整组复算），累计 J 同样不超过原值 × (1 + --tol)
约束同贪心：塘满溢出比原组合多出不超过 --max-spill；新塘占地互不重叠。
用法：python tools/l2opt_prune.py --run RDA_96B --prefix RDA [--step 20] [--tol 0.01] [--procs 16]
--pits（2026-10-24）：J 同贪心加洼坑项；地面泵（surface_ps）同样参与剪枝和换档（同一洼坑换便宜档）。
产物：out/l2opt/<运行>/prune.json（去掉了什么、换了什么、最终措施编号、造价、J、两场雨各热点溢流）
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
from l2opt_greedy import jscore  # noqa: E402
from workdir import WORK  # noqa: E402


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True); ap.add_argument("--prefix", default="RDA"); ap.add_argument("--base-state", default="L2P")
    ap.add_argument("--step", type=int, default=None, help="贪心第几轮的组合（缺省最后一轮）")
    ap.add_argument("--tol", type=float, default=0.01, help="J 允许变差的比例（缺省 1 %%）")
    ap.add_argument("--max-spill", type=float, default=300.0); ap.add_argument("--procs", type=int, default=16)
    ap.add_argument("--pits", action="store_true", help="J 加洼坑项、地面泵参与剪枝（同 l2opt_greedy --pits）")
    a = ap.parse_args(argv)
    import geopandas as gpd
    from shapely.geometry import shape
    o = ROOT / "out" / "l2opt" / a.run
    D = json.loads((o / "candidates.json").read_text(encoding="utf-8"))
    C = {c["id"]: c for c in D["candidates"]}
    G = {f["properties"]["id"]: f["geometry"] for f in json.loads((o / "candidates.geojson").read_text(encoding="utf-8"))["features"]}
    GP = json.loads((o / "greedy_path.json").read_text(encoding="utf-8"))
    P = GP["path"]; last = P[-1] if a.step is None else next(p for p in P if p["step"] == a.step)
    storms = tuple(GP["storms"]); s1 = storms[0]           # 场数不限（2026-10-26 改，原来固定两场）
    base_paths = {s: str(WORK / f"{a.prefix}_{s}_{a.base_state}.inp") for s in storms}
    btxt = pathlib.Path(base_paths[s1]).read_text(encoding="utf-8")
    hs = [h for g in GP["groups"] for h in g]
    H = __import__("hotspot_src").load(32640)
    H = H[H.hotspot_id.isin(hs)]
    Z = SC.Zones(btxt, {r.hotspot_id: (r.geometry.x, r.geometry.y) for r in H.itertuples()})
    sdepth = {}
    for ln in re.search(r"\[STORAGE\]\n(.*?)\n\n", btxt, re.S).group(1).splitlines():
        q = ln.split()
        if len(q) > 3 and not ln.startswith(";"):
            sdepth[q[0]] = float(q[2])
    res = SC.run_jobs([("BASE", s, [], sdepth) for s in storms], base_paths, a.procs, log=lambda *x: None)
    basenf = {r[1]: r[2] for r in res}
    PT = PITS.PitTerm(o / "pits.json", C) if a.pits else None
    base = {s: (PT.add(Z.metrics(basenf[s]), s, basenf[s], []) if PT else Z.metrics(basenf[s])) for s in storms}
    oned = lambda ids: [i for i in ids if C[i]["kind"] != "surface_ps"]   # noqa: E731

    def measures_of(ids):
        return [SC.prep(dict(C[i]), Z, D) for i in oned(ids)]

    def evaluate(sets):
        """{标签: 措施编号列表} → {标签: {雨: metrics}}；一维部分相同的组合只跑一次 SWMM（地面泵不改一维）"""
        key = {lab: tuple(sorted(oned(ids))) for lab, ids in sets.items()}
        uniq = {}
        for lab, k in key.items():
            uniq.setdefault(k, lab)
        jobs = [(uniq[k], s, measures_of(list(k)), sdepth) for k in uniq for s in storms]
        nfs = {}
        for lab, s, nf, *_ in SC.run_jobs(jobs, base_paths, a.procs, log=lambda *x: None):
            if nf is not None:
                nfs[(lab, s)] = nf
        out = {}
        for lab, ids in sets.items():
            for s in storms:
                nf = nfs.get((uniq[key[lab]], s))
                if nf is not None:
                    m = Z.metrics(nf, basenf[s])
                    out.setdefault(lab, {})[s] = PT.add(m, s, nf, ids) if PT else m
        return {k: v for k, v in out.items() if len(v) == len(storms)}

    poly = {}

    def fp(cid):
        if cid not in poly:
            poly[cid] = shape(G[cid]) if C[cid]["kind"] in ("gravity", "pump_newpond", "tank_gravity", "tank_pumped") and cid in G else None   # 10-24 补 v3 地下池
        return poly[cid]

    def overlaps(cid, ids):
        p = fp(cid)
        return p is not None and any(fp(x) is not None and fp(x).intersects(p) for x in ids if x != cid)

    cost = lambda ids: sum(C[i]["cost_usd"] for i in ids)   # noqa: E731
    ids = list(last["chosen"])
    t0 = time.time()
    ref = evaluate({"REF": ids})["REF"]
    J_ref = jscore(ref, base, hs); J_cap = J_ref * (1 + a.tol); spill0 = {s: ref[s]["pond_spill"] for s in storms}
    print(f"{a.run} 第 {last['step']} 轮组合：{len(ids)} 项，{cost(ids) / 1e6:.2f} M USD，J {J_ref:.3f}（上限 {J_cap:.3f}）")

    def ok(m):
        return all(m[s]["pond_spill"] - spill0[s] <= a.max_spill for s in storms)

    log = {"run": a.run, "step": last["step"], "tol": a.tol, "J_ref": round(J_ref, 4), "cost_ref": cost(ids), "removed": [], "swapped": []}
    # ① 剪枝
    singles = evaluate({f"-{i}": [x for x in ids if x != i] for i in ids})
    order = []
    for i in ids:
        m = singles.get(f"-{i}")
        if m and ok(m):
            dj = jscore(m, base, hs) - J_ref
            if J_ref + dj <= J_cap:
                order.append((dj / C[i]["cost_usd"], i, dj))
    order.sort()
    print(f"① 剪枝：单独去掉后 J 仍在上限内的 {len(order)} 项（{(time.time() - t0) / 60:.0f} min）")
    cur_m, J_cur = ref, J_ref
    for _, i, dj in order:
        trial = [x for x in ids if x != i]
        m = evaluate({"T": trial}).get("T")
        if not m or not ok(m):
            continue
        J_t = jscore(m, base, hs)
        if J_t <= J_cap:
            ids, cur_m, J_cur = trial, m, J_t
            log["removed"].append({"id": i, "kind": C[i]["kind"], "hotspot": C[i]["hotspot"], "cost_usd": C[i]["cost_usd"], "J_after": round(J_t, 4)})
            print(f"   去掉 {i}（{C[i]['kind']}，{C[i]['hotspot']}，{C[i]['cost_usd'] / 1e6:.2f} M）→ J {J_t:.3f}")
    # ② 替换
    alts = {}
    for i in ids:
        w = C[i].get("well")
        if not w:
            continue
        for c in D["candidates"]:
            if c.get("well") == w and c["id"] != i and c["cost_usd"] < C[i]["cost_usd"] and not overlaps(c["id"], [x for x in ids if x != i]):
                alts.setdefault(i, []).append(c["id"])
    sets = {f"{i}>{j}": [j if x == i else x for x in ids] for i, js in alts.items() for j in js}
    print(f"② 替换：{len(alts)} 项有更便宜的同井候选，共 {len(sets)} 种（{(time.time() - t0) / 60:.0f} min）")
    sw = evaluate(sets) if sets else {}
    best = {}
    for lab, m in sw.items():
        i, j = lab.split(">")
        if ok(m) and jscore(m, base, hs) <= J_cap:
            save = C[i]["cost_usd"] - C[j]["cost_usd"]
            if i not in best or save > best[i][1]:
                best[i] = (j, save)
    for i, (j, save) in sorted(best.items(), key=lambda kv: -kv[1][1]):
        if i not in ids or overlaps(j, [x for x in ids if x != i]):
            continue
        trial = [j if x == i else x for x in ids]
        m = evaluate({"T": trial}).get("T")
        if not m or not ok(m):
            continue
        J_t = jscore(m, base, hs)
        if J_t <= J_cap:
            ids, cur_m, J_cur = trial, m, J_t
            log["swapped"].append({"from": i, "to": j, "from_kind": C[i]["kind"], "to_kind": C[j]["kind"], "hotspot": C[i]["hotspot"],
                                   "from_cost": C[i]["cost_usd"], "to_cost": C[j]["cost_usd"], "J_after": round(J_t, 4),
                                   "to_storage": C[j].get("storage"), "to_outfall": C[j].get("outfall_site")})
            print(f"   {i}（{C[i]['kind']} {C[i]['cost_usd'] / 1e6:.2f} M）→ {j}（{C[j]['kind']} {C[j]['cost_usd'] / 1e6:.2f} M）→ J {J_t:.3f}")
    log.update(final_ids=ids, cost_final=cost(ids), J_final=round(J_cur, 4), n_ref=len(last["chosen"]), n_final=len(ids),
               zones={s: cur_m[s]["zones"] for s in storms}, pond_spill={s: cur_m[s]["pond_spill"] for s in storms},
               worse={s: cur_m[s].get("worse_m3") for s in storms}, pit={s: cur_m[s].get("pit") for s in storms}, minutes=round((time.time() - t0) / 60))
    (o / "prune.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {o / 'prune.json'}：{log['n_ref']} → {len(ids)} 项，{log['cost_ref'] / 1e6:.2f} → {cost(ids) / 1e6:.2f} M USD，J {J_ref:.3f} → {J_cur:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
