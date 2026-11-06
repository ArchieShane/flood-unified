# -*- coding: utf-8 -*-
"""L2+suggested 优化试点：把一组候选措施做成可跑一二维的「态」（.inp + pilot），给 l2opt_run2d.py 验证用。

  .inp   = RB01_<雨>_L2P.inp + 这些措施（写法同一维筛选 l2opt_screen.apply：泵 / 新塘 / 重力管 + 拍门 / 现有塘加深）
  pilot  = pilot_RB01_<雨>_L2P.json + 新塘（重力塘、泵送新塘）的占地（UTM），二维里当塘面；加深的塘改容积
用法：python tools/l2opt_variant.py --run RB01_H61 --storm S96D1 --state OPT1 --greedy-step 5
      python tools/l2opt_variant.py --run RB01_H61 --storm S96D1 --state OPT1 --ids GR0681,PP0003
产物：$WORK/RB01_<雨>_<态>.inp、c2d/pilot_RB01_<雨>_<态>.json、out/l2opt/<运行>/variant_<雨>_<态>.json（措施清单）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
import l2opt_screen as SC  # noqa: E402
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"


def build(run, storm, state, ids, log=print, prefix="RB01"):
    import geopandas as gpd
    from pyproj import Transformer
    from shapely.geometry import mapping, shape
    from shapely.ops import transform as stf
    o = ROOT / "out" / "l2opt" / run
    D = json.loads((o / "candidates.json").read_text(encoding="utf-8"))
    G = {f["properties"]["id"]: f["geometry"] for f in json.loads((o / "candidates.geojson").read_text(encoding="utf-8"))["features"]}
    C = {c["id"]: c for c in D["candidates"]}
    name = f"{prefix}_{storm}"
    btxt = (WORK / f"{name}_L2P.inp").read_text(encoding="utf-8")
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640)
    Z = SC.Zones(btxt, {r.hotspot_id: (r.geometry.x, r.geometry.y) for r in H[H.hotspot_id.isin({C[i]["hotspot"] for i in ids})].itertuples()})
    sdepth = {}
    for ln in re.search(r"\[STORAGE\]\n(.*?)\n\n", btxt, re.S).group(1).splitlines():
        q = ln.split()
        if len(q) > 3 and not ln.startswith(";"):
            sdepth[q[0]] = float(q[2])
    sumps = [C[i] for i in ids if C[i]["kind"] == "sump"]                  # 地面取水泵是二维设施：不进一维措施，进 pilot 的 pumps2d
    sps = [C[i] for i in ids if C[i]["kind"] == "surface_ps"]              # 地面泵站（l2s_pit_candidates，v4 起并入候选，2026-10-25）：同样只进 pumps2d，取水口 = 候选的 intakes
    ms = [SC.prep(dict(C[i]), Z, D) for i in ids if C[i]["kind"] not in ("sump", "surface_ps")]
    txt = SC.apply(btxt, ms, sdepth).replace("L2+planned", f"L2+suggested 优化 {state}（L2+planned + {len(ids)} 项）", 1)
    # 地面泵 → 新塘：一维里加这个塘（蓄水节点）；→ 出水口：水离开系统（couple2way 里没有 to_pond / to_xy 的取水点就是外排）
    T0 = Transformer.from_crs(4326, 32640, always_xy=True)
    new_stor = []
    for c in sumps:
        if c.get("dest_kind") == "newpond":
            px, py = T0.transform(c["lon"], c["lat"])
            new_stor.append((f"NP_{c['id']}", c["pond_top_m"] - c["pond_depth_m"], c["pond_depth_m"], c["pond_area_m2"], px, py))
    if new_stor:
        txt = SC.add_section_lines(txt, "[STORAGE]", [f"{k}  {zf:.3f}  {d:.3f}  0  FUNCTIONAL  0  0  {A:.1f}  0  0" for k, zf, d, A, _, _ in new_stor])
        txt = SC.add_section_lines(txt, "[COORDINATES]", [f"{k}  {x:.2f}  {y:.2f}" for k, _, _, _, x, y in new_stor])
    (WORK / f"{name}_{state}.inp").write_text(txt, encoding="utf-8")
    meta = json.loads((C2D / f"pilot_{name}_L2P.json").read_text(encoding="utf-8"))
    meta["state"] = state
    T = Transformer.from_crs(4326, 32640, always_xy=True)
    U = lambda g: mapping(stf(lambda x, y, z=None: T.transform(x, y), shape(g)))   # noqa: E731
    if sumps:
        from swbrain import ponding
        pr = ponding.compute(f"{prefix}_S96D1", "L2P")                         # 取水片 = 96 mm / 1 h 下 L2+planned 的积水片（候选就是从这里出的）
        gp = {f["id"]: f["geom"] for f in pr["feats"]}
        p2 = list(meta.get("pumps2d") or [])
        for c in sumps:
            g = shape(U(gp[c["ponding_point"]]))
            g = max(getattr(g, "geoms", [g]), key=lambda q: q.area)
            e = {"id": c["id"], "q_m3s": c["q_m3s"], "off_m": 0.03, "sump_polygon": [list(p) for p in g.exterior.coords], "xy": [c["sx"], c["sy"]],
                 "to_xy": None, "pond_cap_frac": 0.9}
            if c.get("dest_kind") == "pond":
                e["to_pond"] = e["to_node"] = c["dest"]
            elif c.get("dest_kind") == "newpond":
                e["to_pond"] = e["to_node"] = f"NP_{c['id']}"
            p2.append(e)
        meta["pumps2d"] = p2
    if sps:                                                                # 与 l2s_sumps_v3 写 OPTCS2 的 pumps2d 同一格式（取水片、流量、关泵水深、外排）
        p2 = list(meta.get("pumps2d") or [])
        for c in sps:
            for k, it in enumerate(c.get("intakes") or []):
                p2.append({"id": f"{c['id']}_{k}", "q_m3s": it["q_m3s"], "off_m": 0.03, "sump_polygon": it["poly"], "xy": [it["x"], it["y"]], "to_xy": None, "pond_cap_frac": 0.9})
        meta["pumps2d"] = p2
    for i in ids:
        c = C[i]
        if c["kind"] in ("gravity", "pump_newpond", "tank_gravity", "tank_pumped") or (c["kind"] == "sump" and c.get("dest_kind") == "newpond"):
            fp = U(G[i]); g = shape(fp)
            meta["ponds"].append({"node": f"NP_{i}", "unitid": f"NP_{i}", "vol": c["pond_vol_m3"], "xy": [g.centroid.x, g.centroid.y], "footprint": fp, "opt": True,
                                  "underground": c.get("storage") == "地下调蓄池", "no_surface": c.get("storage") == "地下调蓄池"})
        elif c["kind"] == "deepen":
            for p in meta["ponds"]:
                if p["node"] == c["pond"]:
                    p["vol"] = round(p["vol"] + c["add_vol_m3"])
    (C2D / f"pilot_{name}_{state}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    rec = {"run": run, "storm": storm, "state": state, "ids": ids, "cost_usd": sum(C[i]["cost_usd"] for i in ids),
           "measures": [{k: C[i].get(k) for k in ("id", "kind", "well", "hotspot", "n_pumps", "q_m3s", "dest", "pond_vol_m3", "pond_depth_m", "pipe_m", "pipe_d_m",
                                                  "add_vol_m3", "pond", "land", "parcels", "cost_usd", "lon", "lat")} for i in ids]}
    (o / f"variant_{storm}_{state}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"→ {name}_{state}.inp、pilot_{name}_{state}.json：{len(ids)} 项，新塘 {sum(C[i]['kind'] in ('gravity', 'pump_newpond') for i in ids)} 座，"
        f"占位造价 {rec['cost_usd'] / 1e6:.2f} M USD")
    return rec


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="RB01_H61"); ap.add_argument("--storm", required=True); ap.add_argument("--state", required=True)
    ap.add_argument("--ids", default=""); ap.add_argument("--greedy-step", type=int, default=None)
    ap.add_argument("--prefix", default="RB01", help="模型名前缀：RB01 / RDA（2026-10-20 起 RD-A 共用这套脚本）")
    a = ap.parse_args(argv)
    if a.greedy_step is not None:
        P = json.loads((ROOT / "out" / "l2opt" / a.run / "greedy_path.json").read_text(encoding="utf-8"))["path"]
        ids = next(p for p in P if p["step"] == a.greedy_step)["chosen"]
    else:
        ids = [i for i in a.ids.split(",") if i]
    build(a.run, a.storm, a.state, ids, prefix=a.prefix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
