# -*- coding: utf-8 -*-
"""L2+suggested 完整动作清单（强化学习第 1 步）：已有 3 类（l2s_candidates_v3 在治理后模型上的结果）+ 7 类待补措施，统一格式输出。

用法（RD-A，F 系列）：
  SWBRAIN_HOTSPOTS_EXTRA=out/l2opt/targets/HC_A61.geojson python tools/l2s_actions/run.py --prefix RDAG --storm A61D1 --base L2PF --v3 out/l2opt/RDAG_A61 --region RD-A
产物：out/l2s_rl/<region>/actions/{candidates,compliance,rejected,summary}.json、candidates.geojson、check_old_scheme.json、inp_check.json
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import ROOT, Model, hotspots, write_out, status_of  # noqa: E402
import measures as MS  # noqa: E402
from apply import apply  # noqa: E402

PREFIX = {"tank_gravity": "TG", "tank_pumped": "TP", "ps_cluster": "PC", "pipe_upsize": "PU", "ps_upgrade": "PS", "geocell": "GC",
          "inlet_add": "IN", "soakaway": "SK", "flap_gate": "FG", "pond_drawdown": "PD"}
FAMILY = {"tank_gravity": "调蓄", "tank_pumped": "调蓄", "ps_cluster": "抽排"}


def v3_unified(d):
    """已有 3 类：每个 v3 候选 = 一个（井, 容积档）组合；按井 + 类型归并成一个候选、容积档作 grades"""
    if not d:
        return []
    J = json.loads((pathlib.Path(d) / "candidates.json").read_text(encoding="utf-8"))
    comp = json.loads((pathlib.Path(d) / "compliance.json").read_text(encoding="utf-8"))
    groups = {}
    for c in J["candidates"]:
        k = (c["kind"], tuple(c.get("wells") or [c.get("well")]))
        groups.setdefault(k, []).append(c)
    out = []
    for (kind, wells), cs in groups.items():
        cs.sort(key=lambda c: c["cost_usd"])
        items = [{"rule": (i.get("refs") or [{}])[0].get("id", i.get("check", "")), "layer": "A" if str((i.get("refs") or [{}])[0].get("id", "")).startswith("WA726") else "B/C",
                  "result": i["result"], "note": i.get("detail", ""), "zh": i.get("zh", "")} for i in comp.get(cs[0]["id"], [])]
        x = cs[0].get("px") or cs[0].get("ps_x"); y = cs[0].get("py") or cs[0].get("ps_y")
        out.append({"kind": kind, "family": FAMILY[kind], "permanence": "永久", "anchor": {"wells": list(wells)}, "hotspot": cs[0].get("hotspot"), "hs_dist_m": cs[0].get("hs_dist_m"),
                    "grades": [{"grade": f"v3 {c['id']}（{c.get('tier', '')}×）", "params": {"v3_id": c["id"], "vol_m3": c.get("pond_vol_m3"), "q_m3s": c.get("q_m3s")},
                                "cost_usd": c["cost_usd"], "inp_patch": {"v3": c["id"]}} for c in cs],
                    "fidelity": ["F1", "F3"], "compliance": items, "data_flags": [], "excl": [f"well:{w}" for w in wells], "xy": (x, y),
                    "cost_basis": "同 l2s_candidates_v3（压力管、排海口按 WA-726 第三卷单价，其余占位）", "status": status_of(items)})
    return out


def old_scheme_check(cands, prefix_old="out/l2opt/RDA_A61P"):
    """一致性检查：旧最终方案（AECOM 管网上优化）每一项，在新候选集里 ≤ 10 m 内有没有同类候选"""
    p = ROOT / prefix_old
    PR = json.loads((p / "prune.json").read_text(encoding="utf-8"))
    old = {c["id"]: c for c in json.loads((p / "candidates.json").read_text(encoding="utf-8"))["candidates"]}
    rows = []
    for i in PR["final_ids"]:
        c = old[i]; x = c.get("px") or c.get("ps_x"); y = c.get("py") or c.get("ps_y")
        same = [n for n in cands if n["kind"] == c["kind"] and n.get("xy") and n["xy"][0] is not None]
        dmin = min((math.hypot(n["xy"][0] - x, n["xy"][1] - y) for n in same), default=None)
        rows.append({"old": i, "kind": c["kind"], "nearest_new_m": round(dmin, 1) if dmin is not None else None, "covered": dmin is not None and dmin <= 10.0})
    return {"n": len(rows), "covered": sum(r["covered"] for r in rows), "within_50m": sum(1 for r in rows if r["nearest_new_m"] is not None and r["nearest_new_m"] <= 50), "rows": rows}


def inp_check(M, cands, out):
    """每类取第一个候选的第一档写进 inp 副本，用 SWMM 打开（不计算），确认格式能读、元素数对得上"""
    from swmm.toolkit import solver
    res = {}
    for kind in PREFIX:
        c = next((x for x in cands if x["kind"] == kind and "v3" not in x["grades"][0]["inp_patch"]), None)
        if c is None:
            res[kind] = "无候选或沿用 v3 写法（l2opt_variant）"; continue
        with tempfile.TemporaryDirectory() as td:
            dst = pathlib.Path(td) / "t.inp"
            try:
                pilot = apply(M.inp, [(c, 0)], dst)
                solver.swmm_open(str(dst), str(pathlib.Path(td) / "t.rpt"), str(pathlib.Path(td) / "t.out")); solver.swmm_close()
                res[kind] = {"ok": True, "cand": c["id"], "pilot_patch": {k: len(v) for k, v in pilot.items()}}
            except Exception as e:  # noqa: BLE001
                res[kind] = {"ok": False, "cand": c["id"], "error": str(e)[:300]}
    (out / "inp_check.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="RDAG"); ap.add_argument("--storm", default="A61D1"); ap.add_argument("--base", default="L2PF")
    ap.add_argument("--v3", default=None); ap.add_argument("--region", default="RD-A")
    ap.add_argument("--hs", default="ADM-75,ADM-76,ADM-80,ADM-161,ADM-376,ADM-377,ADM-378,ADM-379,ADM-380,ADM-382,ADM-383",
                    help="本区客户热点；高后果点（HC-*，来自 SWBRAIN_HOTSPOTS_EXTRA）自动加入")
    ap.add_argument("--sitedata", default=str(ROOT / "out/rda/sitedata.gpkg")); ap.add_argument("--domain", default=str(ROOT / "out/dataqc/RD-A/input/rda_domain.geojson"))
    a = ap.parse_args(argv)
    out = ROOT / "out/l2s_rl" / a.region / "actions"; out.mkdir(parents=True, exist_ok=True)
    M = Model(a.prefix, a.storm, a.base)
    print(f"模型 {M.inp.name}：管 {len(M.cond)}、泵 {len(M.pumps)}、出水口 {len(M.outfalls)}、塘 / 湿井 {len(M.storage)}；溢流节点 {len(M.flood)}、超载管 {len(M.surch)}")
    keep = set(a.hs.split(","))
    H = [h for h in hotspots() if h[0] in keep or h[0].startswith("HC-")]
    print(f"本区热点 + 高后果点 {len(H)} 个：{', '.join(h[0] for h in H)}")
    import geopandas as gpd
    from l2s_candidates_v3 import Site
    site = Site(a.sitedata, gpd.read_file(a.domain).to_crs(32640), a.prefix, a.storm, a.base)
    from swbrain import ponding
    from pyproj import Transformer
    T = Transformer.from_crs(4326, 32640, always_xy=True)
    pr = ponding.compute(M.run, a.base, geometry=False, pond_scope="state")
    patches = [{"id": f["id"], "hmax_m": f["hmax_m"], "area_m2": f["area_m2"], "vol_peak_m3": f["vol_peak_m3"], **dict(zip(("x", "y"), T.transform(f["lon"], f["lat"])))} for f in pr["feats"]]
    print(f"基线二维积水片 {len(patches)} 个")
    cands, rej = v3_unified(a.v3), []
    for fn, args in ((MS.pipe_upsize, ()), (MS.ps_upgrade, ()), (MS.geocell, ()), (MS.inlet_add, (patches,)), (MS.soakaway, (patches,)), (MS.flap_gate, ()), (MS.pond_drawdown, ())):
        c, r = fn(M, H, site, *args); cands += c; rej += r
        print(f"  {fn.__name__}: 候选 {len(c)}，不可建 {len(r)}")
    cnt = {}
    for c in cands:
        cnt[c["kind"]] = cnt.get(c["kind"], 0) + 1; c["id"] = f"{PREFIX[c['kind']]}{cnt[c['kind']]:04d}"
        c["xy"] = [round(v, 1) if v is not None else None for v in (c.get("xy") or (None, None))]
    meta = {"region": a.region, "model": M.inp.name, "base_state": a.base, "storm": a.storm, "design": "docs/L2S动作清单补全_7类措施候选规则_设计_20261030.md"}
    summ = write_out(out, cands, rej, meta)
    from pyproj import Transformer as TT
    t2 = TT.from_crs(32640, 4326, always_xy=True)
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": list(t2.transform(*c["xy"]))}, "properties": {"id": c["id"], "kind": c["kind"], "status": c["status"],
              "hotspot": c.get("hotspot"), "n_grades": len(c["grades"]), "min_cost_usd": min(g["cost_usd"] for g in c["grades"])}} for c in cands if c["xy"][0] is not None]
    (out / "candidates.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False), encoding="utf-8")
    chk = old_scheme_check(cands); (out / "check_old_scheme.json").write_text(json.dumps(chk, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"旧最终方案 {chk['n']} 项：新候选 ≤ 10 m 覆盖 {chk['covered']}、≤ 50 m {chk['within_50m']}")
    ic = inp_check(M, cands, out)
    print("inp 写入检查：", {k: (v["ok"] if isinstance(v, dict) else v) for k, v in ic.items()})
    print("各类：", dict(summ["by_kind"])); print("→", out)


if __name__ == "__main__":
    main()
