# -*- coding: utf-8 -*-
"""RB-01 结果 → 控制台「RB-01」视图的数据（格式同 C1 视图：manifest / stats 由 pilot_frames.py 出，这里出 rb01_stats.json）。

  zones      全域（塘外）、6 个热点 200 m 环、6 个热点合计（并集）：最大水深、≥0.10 / ≥0.30 m 面积、最大积水量与时刻
  diffs      相邻态 / 对 L2+planned 的全程最大水深差值图（|Δh| ≥ 5 cm）
  facilities 各态设施（泵 / 塘 / 重力管 / 地面取水片），带设计参数与本场实际表现（SWMM 泵运行、塘满度、二维取水量）
  hyeto      雨型（雨量 / 历时取这次运行的 out/rb01_inputs_{tag}_L1.json，客户 Mussafah 10 年一遇含 CC 1 h 雨型放大）与面积加权净雨
  hs         6 个热点 50 m 内最深水深过程线（每 5 分钟）
  balance    水量对账
用法：python tools/rb01_console.py [--tag H61]     （先跑 pilot_frames.py --name RB01_H61 … --out flood2d_rb01_h61）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from rb01_build import HS, MOBILE  # noqa: E402
from rb01_design_l2s import pond_cells, pump_util  # noqa: E402
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
STATES = ["L1", "L2", "L2P", "L2S4", "L2S5"]
PAIRS = [("L1", "L2"), ("L2", "L2P"), ("L1", "L2P"), ("L2P", "L2S4"), ("L2P", "L2S5"), ("L2S4", "L2S5"), ("L1", "L2S5")]
CHAIN = {"L1": ["L1"], "L2": ["L1", "L2"], "L2P": ["L1", "L2P"], "L2S4": ["L1", "L2P", "L2S4"], "L2S5": ["L1", "L2P", "L2S5"]}


def storage(rpt):
    t = pathlib.Path(rpt).read_text(errors="ignore")
    m = re.search(r"Storage Volume Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    return {q.split()[0]: (float(q.split()[5]) * 1000, float(q.split()[6])) for q in (m.group(1).splitlines() if m else []) if q.strip()}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", default="H61"); a = ap.parse_args(argv)
    import geopandas as gpd
    from pyproj import Transformer
    from scipy.spatial import cKDTree
    from city2d_post import render_diff
    name = f"RB01_{a.tag}"; D = ROOT / "console" / "data" / f"flood2d_rb01_{a.tag.lower()}"
    tr = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    ll = lambda x, y: [round(v, 6) for v in tr.transform(x, y)[::-1]]   # noqa: E731
    have = [s for s in STATES if (C2D / f"{name}_{s}_2d.npz").exists()]
    Z = {s: np.load(C2D / f"{name}_{s}_2d.npz") for s in have}
    TW = {s: json.loads((C2D / f"twoway_{name}_{s}.json").read_text(encoding="utf-8")) for s in have}
    META = {s: json.loads((C2D / f"pilot_{name}_{s}.json").read_text(encoding="utf-8")) for s in have}
    xy = Z["L1"]["xy"].astype(float); area = Z["L1"]["area"].astype(float); th = Z["L1"]["t_h"]
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    pm = np.zeros(len(xy), bool)
    for s in have:
        pm |= pond_cells(META[s], xy)
    T = cKDTree(xy)
    masks = {}
    for h in HS:
        m = np.zeros(len(xy), bool); m[T.query_ball_point((H.loc[h].geometry.x, H.loc[h].geometry.y), 200.0)] = True
        masks[f"{h}|200m"] = m & ~pm
    masks["六个热点|200m"] = np.any([masks[f"{h}|200m"] for h in HS], axis=0)
    masks["全域|all"] = ~pm
    from rb01_build import run_info
    RI = run_info(a.tag); RAIN, DUR, SIM = float(RI["rain_mm"]), float(RI["dur_h"]), float(RI["sim_h"])
    res = {"name": name, "rain_mm": RAIN, "dur_h": DUR, "states": have, "zones": {}, "diffs": [], "balance": {}, "hs": {}, "facilities": {}}
    for s in have:
        hm = Z[s]["hmax"].astype(float); fr = Z[s]["frames"].astype(np.float32)
        for k, m in masks.items():
            vols = (fr[:, m] * area[m]).sum(1); i = int(vols.argmax())
            res["zones"].setdefault(k, {})[s] = {"max_depth_m": round(float(hm[m].max()), 3), "area_ge_0p10_ha": round(float(area[m & (hm >= 0.10)].sum()) / 1e4, 2),
                                                 "area_ge_0p30_ha": round(float(area[m & (hm >= 0.30)].sum()) / 1e4, 2),
                                                 "peak_vol_m3": round(float(vols[i])), "peak_vol_t_h": round(float(th[i]), 2), "zone_area_ha": round(float(area[m].sum()) / 1e4, 1)}
        acc = TW[s]["acc_m3"]
        res["balance"][s] = {"1D→2D": round(acc["1to2"]), "2D→1D": round(acc["2to1"]), "进塘（地表）": round(acc["pond"]), "泵打进塘（二维取水）": round(acc.get("pump_pond", 0)),
                             "域内留存（5 h 末）": round(TW[s]["final_vol_m3"]), "边界出流（余项）": round(TW[s]["boundary_out_m3"])}
        for h in HS:
            idx = T.query_ball_point((H.loc[h].geometry.x, H.loc[h].geometry.y), 50.0)
            r50 = fr[:, idx].max(1)
            res["hs"].setdefault(h, {})[s] = {"r50": [round(float(v), 3) for v in r50], "peak_m": round(float(r50.max()), 2),
                                              "h_ge30": round(float((r50 >= 0.30).sum() * 5 / 60), 2), "end_m": round(float(r50[-1]), 2)}
    res["hs_t_h"] = [round(float(v), 4) for v in th]
    for s1, s2 in PAIRS:
        if s1 in Z and s2 in Z:
            dh = Z[s2]["hmax"].astype(float) - Z[s1]["hmax"].astype(float); dh[pm] = 0
            fn = f"diff_{s2}_minus_{s1}.png"; b = render_diff(xy, dh, 15.0, D / fn)
            res["diffs"].append({"a": s1, "b": s2, "png": fn, "bounds": b, "shallower_ha": round(float(area[dh <= -0.05].sum()) / 1e4, 2),
                                 "deeper_ha": round(float(area[dh >= 0.05].sum()) / 1e4, 2)})
    # ---- 设施
    S = {}
    for ln in open(WORK / f"{name}_L2S5.inp", encoding="utf-8"):
        p = ln.split()
        if len(p) == 3:
            try:
                S[p[0]] = (float(p[1]), float(p[2]))
            except ValueError:
                pass
    PU = {s: pump_util(C2D / f"{name}_{s}_2way.rpt") for s in have}
    ST = {s: storage(C2D / f"{name}_{s}_2way.rpt") for s in have}

    def sim_pump(pid, sts):
        return {s: {"vol_m3": round(PU[s][pid][1]), "util_pct": PU[s][pid][0]} for s in sts if pid in PU.get(s, {})}

    def sim_pond(node, sts):
        return {s: {"max_m3": round(ST[s][node][0]), "max_pct": ST[s][node][1],
                    **next(({"surface_m3": round(r["surface_captured_m3"]), "pumped_in_m3": round(r["pumped_in_m3"]), "overflow_m3": round(r["overflow_m3"])}
                            for r in TW[s]["ponds"] if r["unitid"] == node), {})} for s in sts if node in ST.get(s, {})}
    F = {"L1": []}
    l2 = META["L2"]["pumps_l2"]
    F["L2"] = [{"kind": "mobile", "id": p["id"][3:], "q_lps": round(p["q_m3s"] * 1000), "ll": ll(*S[p["from"]]), "to_ll": ll(*S[p["to"]]),
                "p": {"hotspot": p["hotspot"], "size": p["size"], "well": p["well_depth_m"], "on": p["on"], "off": p["off"], "path": p["path_m"], "why": p["how"]},
                "sim": sim_pump(p["id"], ["L2"])} for p in l2]
    sts_p = [s for s in ("L2P", "L2S4", "L2S5") if s in have]
    F["L2P"] = [{"kind": "ps_planned", "id": p["pump"], "q_lps": round(p["q_m3s"] * 1000, 1), "ll": ll(p["x"], p["y"]), "to_ll": ll(*S[p["to"]]) if p["to"] in S else None,
                 "p": {"well": p["well_depth_m"], "on": round(p["on"], 2), "off": round(p["off"], 2), "why": p["how"] + "；" + p["q_src"]}, "sim": sim_pump(p["id"], sts_p)}
                for p in META["L2P"]["pumps_1d"]]
    from shapely.geometry import shape
    for p in META["L2P"]["ponds"]:
        g = shape(p["footprint"]); g = max(g.geoms, key=lambda q: q.area) if hasattr(g, "geoms") else g
        F["L2P"].append({"kind": "pond", "id": p["node"][4:], "vol_m3": round(p["vol"]), "ll": ll(*p["xy"]), "poly": [ll(x, y) for x, y in g.exterior.coords],
                         "p": {"vol": round(p["vol"]), "why": "PO 塘方案（来源待确认）：塘底 = 轮廓内 DTM 最低点、1.0 m 深" + ("；塘面在计算域外，只在一维收水" if p.get("no_surface") else "")},
                         "sim": sim_pond(p["node"], sts_p)})
    for s in ("L2S4", "L2S5"):
        if s not in have:
            continue
        info = json.loads((ROOT / "out" / f"rb01_{s}_inputs_{a.tag}.json").read_text(encoding="utf-8"))
        v4 = json.loads((ROOT / "out" / f"rb01_L2S4_inputs_{a.tag}.json").read_text(encoding="utf-8"))
        fs = []
        convs = info["convs"] if s == "L2S5" else v4["convs"]
        for c in convs:
            if s == "L2S5" and c["v5"]["how"] == "gravity":
                continue
            fs.append({"kind": "conv", "id": c["id"], "q_lps": round(c["q_m3s"] * 1000), "ll": ll(*S[c["node"]]), "to_ll": ll(*S[c["to_pond"]]) if c["to_pond"] in S else None,
                       "p": {"n": c["n_pumps"], "hotspot": c["near_hotspot"], "why": c["why"]}, "sim": sim_pump(f"{c['id']}_P", [s])})
        sump_ids = set(info["sumps"]) if s == "L2S5" else {m["id"] for m in v4["measures"]}
        pumped = {q["id"]: q["pumped_m3"] for q in TW[s]["pumps2d"]}
        for m in v4["measures"]:
            if m["id"] in sump_ids:
                fs.append({"kind": "sump", "id": m["id"], "q_lps": round(m["q_m3s"] * 1000), "ll": m["ll"], "to_ll": ll(*S[m["to_pond"]]) if m["to_pond"] in S else None,
                           "poly": [ll(x, y) for x, y in m["sump_polygon"]], "p": {"n": m["n_pumps"], "patch": m["area_ha"], "peak": m["peak_vol_m3"], "why": m["why"]},
                           "sim": {s: {"vol_m3": round(pumped.get(m["id"], 0))}}})
        if s == "L2S5":
            for p in info["ponds"]:
                g = shape(p["footprint"]); g = max(g.geoms, key=lambda q: q.area) if hasattr(g, "geoms") else g
                fs.append({"kind": "pond", "id": p["id"], "vol_m3": p["vol"], "ll": ll(*p["xy"]), "poly": [ll(x, y) for x, y in g.exterior.coords],
                           "p": {"vol": p["vol"], "depth": 1.5, "floor": p["z_floor"], "why": f"v5 新塘（{p['land']}），重力接 {', '.join(p['wells'])}；挖方 {p['cut_m3']:,} m³"},
                           "sim": sim_pond(p["node"], ["L2S5"])})
            for q in info["pipes"]:
                fs.append({"kind": "gpipe", "id": q["id"], "ll": ll(*S[q["from"]]), "line": [ll(*S[q["from"]]), ll(*S[q["to"]])],
                           "p": {"path": q["L_m"], "depth": q["d_m"], "why": f"重力管 {q['from']} → {q['pond']}（Ø{q['d_m']:.2f} m，进口 = 井口 − 0.5 m，拍门）"}})
        F[s] = fs
    res["facilities"] = F; res["chain"] = CHAIN
    # ---- 雨型
    import runoff as R
    shape_ = R.read_shape(WORK / "Mussafah_baseline.inp", DUR, log=lambda *x: None)
    t5 = np.arange(0, SIM + 1e-9, 1 / 12); _, P = R.hyetograph(RAIN, DUR, 1 / 12, shape_, 0.0, total_h=SIM)
    exc = np.array(META["L1"]["rain_excess_mm"]); te = np.array(META["L1"]["rain_t_h"])
    ex5 = np.interp(t5, te, exc)
    inc = np.diff(P, prepend=0)[1:]
    res["hyeto"] = {"t_h": [round(float(v), 4) for v in t5[:-1]], "p_mmph": [round(float(v) * 12, 2) for v in inc], "p_cum_mm": [round(float(v), 2) for v in P[1:]],
                    "exc_cum_mm": [round(float(v), 2) for v in ex5[1:]], "dt_min": 5, "sim_h": SIM, "dur_h": DUR, "rain_mm": RAIN,
                    "peak_mmph": round(float(inc.max()) * 12, 1), "peak_t_h": round(float(t5[int(inc.argmax())]), 3), "excess_mm": round(float(exc[-1]), 1),
                    "source": "客户 Mussafah 模型「10 年一遇含 CC 1 h」雨型等比放大"}
    from console_en import add_en; add_en(res)   # 英文界面用的 *_en 字段（2026-10-28）
    (D / "rb01_stats.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print(f"→ {D / 'rb01_stats.json'}：{len(res['zones'])} 个统计区、{len(res['diffs'])} 张差值图、设施 " + "、".join(f"{k} {len(v)}" for k, v in F.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
