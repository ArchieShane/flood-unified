"""L2S 深度强化学习试点 · 第 1 步：造训练数据（随机措施组合 → SWMM 一维 → 各热点溢流）。

2026-10-26 用户：L2S 用深度强化学习试试，先用本机 RTX 5090（训练在 ArcGIS Pro 的 Python 环境里，那里有 PyTorch 2.9 + CUDA）。
这一步只用 CPU 跑 SWMM（复用 tools/l2opt_screen.py 的 apply / run_jobs / Zones），不动任何正在跑的优化链的文件。
- 组合怎么抽：每个样本抽 k 项（1…kmax）；井按一维筛选得分加权抽（stage1_ranked.json），井内选项随机；
  同一口井（含合并泵站截的各井）不重复截流，地下池 / 泵站占地按圆近似、互不重叠。surface_ps（地面泵）不改一维，不抽。
- 另把逐步挑选每一轮的组合（greedy_path.json，指标已有）原样收进来。
- 输出 out/l2s_rl/<run>/：base.json（基线指标、候选表）、samples.jsonl（每行一个组合 × 一场雨），可接着追加。
- 洼坑项（l2s_pits）这一版不算，奖励只用热点 500 m 内溢流、塘溢流、别处变多。
用法：python -X utf8 tools/l2s_rl_data.py --run RDA_A61 --n 40 --procs 2 [--seed 1] [--kmax 30]（需设 SWBRAIN_SWMM_WORK）
"""
from __future__ import annotations
import argparse
import json
import math
import pathlib
import random
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import geopandas as gpd  # noqa: E402
import l2opt_screen as SC  # noqa: E402
from workdir import WORK  # noqa: E402


def footprint(c):
    """(x, y, 半径 m)：地下池按池面积的圆 + 5 m；合并泵站按泵站场地 15 m。None = 不占地。"""
    if c["kind"] in ("tank_gravity", "tank_pumped") and c.get("px") is not None:
        return c["px"], c["py"], math.sqrt(max(c.get("pond_area_m2") or 0, 1) / math.pi) + 5.0
    if c["kind"] == "ps_cluster" and c.get("ps_x") is not None:
        return c["ps_x"], c["ps_y"], 15.0
    return None


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="RDA_A61"); ap.add_argument("--prefix", default="RDA"); ap.add_argument("--base-state", default="L2P")
    ap.add_argument("--storms", default=""); ap.add_argument("--n", type=int, default=40); ap.add_argument("--kmax", type=int, default=30)
    ap.add_argument("--procs", type=int, default=2); ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    o = ROOT / "out" / "l2opt" / a.run; out = ROOT / "out" / "l2s_rl" / a.run; out.mkdir(parents=True, exist_ok=True)
    D = json.loads((o / "candidates.json").read_text(encoding="utf-8"))
    GP = json.loads((o / "greedy_path.json").read_text(encoding="utf-8"))
    storms = [s for s in a.storms.split(",") if s] or list(GP["storms"])
    C = {c["id"]: c for c in D["candidates"] if c["kind"] != "surface_ps"}
    rank = {r["well"]: r["score"] for r in json.loads((o / "stage1_ranked.json").read_text(encoding="utf-8"))}
    by_well = {}
    for c in C.values():
        by_well.setdefault(c["well"], []).append(c["id"])
    wells = [w for w in by_well if w in rank]; wts = [max(rank[w], 0.0) + 0.05 for w in wells]

    base_paths = {s: str(WORK / f"{a.prefix}_{s}_{a.base_state}.inp") for s in storms}
    btxt = pathlib.Path(base_paths[storms[0]]).read_text(encoding="utf-8")
    hs = [h for g in GP["groups"] for h in g]
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640); H = H[H.hotspot_id.isin(hs)]
    Z = SC.Zones(btxt, {r.hotspot_id: (r.geometry.x, r.geometry.y) for r in H.itertuples()})
    sdepth = {}
    for ln in re.search(r"\[STORAGE\]\n(.*?)\n\n", btxt, re.S).group(1).splitlines():
        q = ln.split()
        if len(q) > 3 and not ln.startswith(";"):
            sdepth[q[0]] = float(q[2])

    bf = out / "base.json"
    if bf.exists():
        B = json.loads(bf.read_text(encoding="utf-8")); basenf = {s: {k: tuple(v) for k, v in B["base_nf"][s].items()} for s in storms}
    else:
        res = SC.run_jobs([("BASE", s, [], sdepth) for s in storms], base_paths, a.procs, log=lambda *x: None)
        basenf = {r[1]: r[2] for r in res}
        B = {"run": a.run, "storms": storms, "hotspots": hs, "groups": GP["groups"], "base_state": a.base_state,
             "base": {s: Z.metrics(basenf[s]) for s in storms}, "base_nf": {s: {k: list(v) for k, v in basenf[s].items()} for s in storms},
             "candidates": [{k: c.get(k) for k in ("id", "kind", "well", "wells", "hotspot", "cost_usd", "overflow_m3", "pond_vol_m3", "q_m3s", "px", "py", "ps_x", "ps_y")} for c in C.values()]}
        bf.write_text(json.dumps(B, ensure_ascii=False), encoding="utf-8")
        print(f"基线：{storms}，候选 {len(C)} 项（{len(wells)} 口有筛选得分的井）")

    sf = out / "samples.jsonl"
    if not sf.exists():                                              # 逐步挑选每一轮的组合：指标现成
        with open(sf, "w", encoding="utf-8") as fh:
            for p in GP["path"][1:]:
                for s in storms:
                    if s in p.get("zones", {}):
                        fh.write(json.dumps({"ids": p["chosen"], "storm": s, "zones": p["zones"][s], "total": p["total"][s],
                                             "pond_spill": (p.get("pond_spill") or {}).get(s), "worse_m3": (p.get("worse") or {}).get(s), "src": "greedy"}, ensure_ascii=False) + "\n")

    rng = random.Random(a.seed)

    def sample():
        k = rng.randint(1, a.kmax); chosen, used, fps = [], set(), []
        for _ in range(k * 6):
            if len(chosen) >= k:
                break
            w = rng.choices(wells, wts)[0]; cid = rng.choice(by_well[w]); c = C[cid]
            ws = set(c.get("wells") or [c["well"]])
            if ws & used:
                continue
            f = footprint(c)
            if f and any(math.hypot(f[0] - g[0], f[1] - g[1]) < f[2] + g[2] for g in fps):
                continue
            chosen.append(cid); used |= ws
            if f:
                fps.append(f)
        return chosen

    combos = [sample() for _ in range(a.n)]
    jobs = [(f"S{i}", s, [SC.prep(dict(C[c]), Z, D) for c in ids], sdepth) for i, ids in enumerate(combos) for s in storms]
    t0 = time.time()
    res = SC.run_jobs(jobs, base_paths, a.procs)
    n_ok = 0
    with open(sf, "a", encoding="utf-8") as fh:
        for key, s, nf, tot, ce, sec in res:
            if nf is None:
                continue
            m = Z.metrics(nf, basenf[s]); n_ok += 1
            fh.write(json.dumps({"ids": combos[int(key[1:])], "storm": s, **m, "ce": ce, "sec": sec, "src": f"rand{a.seed}"}, ensure_ascii=False) + "\n")
    print(f"→ {sf}：新增 {n_ok}/{len(jobs)} 条，{(time.time() - t0) / 60:.1f} min（{a.procs} 路），平均每次 {sum(r[5] for r in res) / max(len(res), 1):.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
