# -*- coding: utf-8 -*-
"""C1 各态设施（给系统地图符号化 + 参数框，用户 2026-09-29「设施做更友好的符号化表达」）。

每个设施：kind（ps_l1 现状泵站 / mobile OP-02 移动泵 / ps_planned Parsons 泵站 / pond 规划塘 / conv 转输泵 / sump 地面取水泵 / box 箱涵）、
id、ll、to_ll（排到哪）、q_lps、p（设计参数，键名在 console/i18n.js c1v.p_*）、poly（塘的占地轮廓）、
sim = {态: {本场实际表现}}——泵：抽水量 m³、运行时间占比 %、启停次数（SWMM Pumping Summary）；二维取水点：抽水量（twoway pumps2d）；
塘：6 h 末满度、地表进塘、溢出（twoway ponds）；箱涵：最大蓄水量与满度（SWMM Storage Volume Summary）。
L1 设施在 L2 / L2+ 各态里都在，所以 sim 按态分开存，系统按当前态取。
用法：python tools/c1_facilities.py        # 就地更新 console/data/flood2d_c1_r{61,96}/c1_stats.json 的 facilities（不重做后处理）
      c1_post.py 调 build(r, have, meta)
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
STATES = ["L1", "L2", "L2P", "L2S", "L2S3"]
Q12 = 360.0


def _table(rpt, title, ncol):
    t = rpt.read_text(errors="ignore") if rpt.exists() else ""
    m = re.search(title + r".*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        q = ln.split()
        if len(q) >= ncol:
            out[q[0]] = q
    return out


def _sim(name, st):
    rpt = C2D / f"{name}_{st}_2way.rpt"
    pumps = {k: {"util_pct": float(q[1]), "starts": int(q[2]), "vol_m3": round(float(q[6]) * 1000.0)}
             for k, q in _table(rpt, r"Pumping Summary", 7).items()}
    sto = {k: {"max_m3": round(float(q[5]) * 1000.0), "max_pct": float(q[6])} for k, q in _table(rpt, r"Storage Volume Summary", 7).items()}
    tw = C2D / f"twoway_{name}_{st}.json"
    tw = json.loads(tw.read_text(encoding="utf-8")) if tw.exists() else {}
    ponds = {p["unitid"]: {"fill_pct": round(p.get("fill_pct") or 0, 1), "end_m3": round(p.get("vol_end_m3") or 0),
                           "surface_m3": round(p.get("surface_captured_m3") or 0), "pumped_in_m3": round(p.get("pumped_in_m3") or 0),
                           "overflow_m3": round(p.get("overflow_m3") or 0)} for p in tw.get("ponds", [])}
    p2d = {q["id"]: round(q.get("pumped_m3") or 0) for q in tw.get("pumps2d", [])}
    return pumps, sto, ponds, p2d


def build(r, have, meta=None, tag=None):
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    ll = lambda x, y: [round(v, 6) for v in tr.transform(x, y)[::-1]]
    nodexy = lambda k: tuple(map(float, k.split("_")[:2])) if k and k[0].isdigit() else None
    tag = tag or f"R{r}"
    name = f"C1_{tag}"
    meta = meta or json.loads((C2D / f"pilot_{name}_L1.json").read_text(encoding="utf-8"))
    S = {st: _sim(name, st) for st in have}

    def pump_sim(pid):
        return {st: S[st][0][pid] for st in have if pid in S[st][0]}

    F = {"L1": []}
    for p in meta.get("pumps_1d", []):
        a = nodexy(p["from"]); b = nodexy(p["to"])
        F["L1"].append({"kind": "ps_l1", "id": p["ps"], "q_lps": round(p["q_m3s"] * 1000), "ll": ll(*a), "to_ll": ll(*b) if b else None, "how": p["how"],
                        "p": {"n": p.get("n"), "q_each": p.get("q_each"), "src": p.get("q_src"), "on": round(p["on"], 2), "off": round(p["off"], 2),
                              "unitid": p.get("unitid"), "to": p["how"]},
                        "sim": pump_sim(p["id"])})
    f2 = ROOT / "out" / f"c1_L2_inputs_{tag}.json"
    if f2.exists() and "L2" in have:
        F["L2"] = [{"kind": "mobile", "id": p["site"], "q_lps": p["q_lps"], "ll": ll(p["x"], p["y"]),
                    "to_ll": ll(*nodexy(p["receiver"])) if nodexy(p["receiver"]) else None, "how": p["how"],
                    "p": {"n": 1, "q_each": p["q_lps"], "src": p["q_src"], "on": p["on"], "off": p["off"], "well": p["well_depth_m"],
                          "suction": p["suction_dist_m"], "path": p["path_m"], "to": p["how"], "hs": p.get("near_hotspot")},
                    "sim": pump_sim(p["id"])} for p in json.loads(f2.read_text(encoding="utf-8"))["pumps"]]
    f3 = ROOT / "out" / f"c1_L2P_inputs_{tag}.json"
    if f3.exists() and "L2P" in have:
        import geopandas as gpd
        from shapely.geometry import shape
        G = gpd.read_file(ROOT / "console" / "data" / "c1_pumps.geojson").set_index("id")
        d3 = json.loads(f3.read_text(encoding="utf-8"))
        mp = json.loads((C2D / f"pilot_{name}_L2P.json").read_text(encoding="utf-8"))
        foot = {p["node"]: p["footprint"] for p in mp.get("ponds", [])}
        pond_xy = {q["node"]: q["xy"] for q in d3["ponds"]}
        F["L2P"] = []
        for p in d3["pumps"]:
            g = G.loc[p["station"]].geometry
            F["L2P"].append({"kind": "ps_planned", "id": p["station"], "q_lps": p["q_lps"], "ll": [round(g.y, 6), round(g.x, 6)],
                             "to_ll": ll(*pond_xy[p["to"]]) if p["to"] in pond_xy else (ll(*nodexy(p["to"])) if nodexy(p["to"]) else None),
                             "how": p["how_from"] + " → " + p["how_to"],
                             "p": {"q_lps": p["q_lps"], "wetwell": p.get("wetwell"), "on": p["on"], "off": p["off"], "from": p["how_from"], "to": p["how_to"]},
                             "sim": pump_sim(p["id"])})
        for q in d3["ponds"]:
            poly = None
            if q["node"] in foot:
                gg = shape(foot[q["node"]]).simplify(2.0)
                parts = gg.geoms if hasattr(gg, "geoms") else [gg]
                poly = [[ll(x, y) for x, y, *_ in part.exterior.coords] for part in parts]
            F["L2P"].append({"kind": "pond", "id": q["node"], "vol_m3": round(q["vol"]), "area_ha": q["area_ha"], "ll": ll(*q["xy"]), "poly": poly,
                             "p": {"vol": round(q["vol"]), "area": q["area_ha"], "depth": q.get("depth"), "floor": round(q["z_floor"], 2) if q.get("z_floor") is not None else None},
                             "sim": {st: S[st][2][q["node"]] for st in have if q["node"] in S[st][2]}})
    for st in ("L2S", "L2S3"):
        f4 = ROOT / "out" / f"c1_{st}_inputs_{tag}.json"
        if not (f4.exists() and st in have):
            continue
        d4 = json.loads(f4.read_text(encoding="utf-8"))
        M = {m["id"]: m for m in d4["measures"]}
        F[st] = []
        for e in d4.get("map", []):
            m = M.get(e["id"], {})
            f = {**e, "p": {"why": m.get("why"), "patch": m.get("area_ha"), "peak": m.get("peak_vol_m3"), "hs": m.get("near_hotspot"), "hsd": m.get("near_dist_m")}}
            if e["kind"] in ("conv", "sump"):
                pm = re.search(r"规划塘 PND_C1_\d+", m.get("why") or "")
                to = m.get("target") or m.get("to_pond") or (pm.group(0) if (m.get("to_xy") and pm) else "PROPOSED OUTFALL（新建出水口）")
                f["p"].update(n=m.get("n_pumps"), q_each=Q12, to=to)
                f["sim"] = ({s: S[s][0][f"{e['id']}_P"] for s in have if s == st and f"{e['id']}_P" in S[s][0]} if e["kind"] == "conv"
                            else {s: {"vol_m3": S[s][3][e["id"]]} for s in have if s == st and e["id"] in S[s][3]})
            if e["kind"] == "box":
                f["p"].update(vol=m.get("vol_m3"), node=m.get("node"))
                f["sim"] = {s: S[s][1][f"BOX_{e['id']}"] for s in have if s == st and f"BOX_{e['id']}" in S[s][1]}
            F[st].append(f)
    return F


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    for r in (61, 96):
        p = ROOT / "console" / "data" / f"flood2d_c1_r{r}" / "c1_stats.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["facilities"] = build(r, d["states"])
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        print(f"R{r}：" + "；".join(f"{k} {len(v)}" for k, v in d["facilities"].items()),
              "| 有 sim 的：", sum(1 for v in d["facilities"].values() for f in v if f.get("sim")))


if __name__ == "__main__":
    main()
