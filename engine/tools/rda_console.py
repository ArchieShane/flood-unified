# -*- coding: utf-8 -*-
"""RD-A 结果 → 系统「防线2+ 多态对比 → RD-A」视图的数据（2026-10-21；格式同 RB-01 视图的 rb01_stats.json，前端共用一套代码）。

每场雨一个目录 console/data/flood2d_rda_<雨>/：
  manifest.json / stats.json / <态>/f###.png / max.png    pilot_frames.py 出（逐帧水深图）
  rda_stats.json                                         本脚本出：统计区、差值图、各态设施（带设计参数与本场实际表现）、雨型、热点水深过程、水量对账
总目录 console/data/flood2d_rda/index.json：有哪些雨、各态在各场雨下的热点达标数（前端的雨型下拉框）
态：L1 现状 / L2 + 预置移动泵 / L2P = L2+planned（客户 Temporary Intervention 情景里列入计划的 2 台泵）/
    OPT20F = L2+suggested v1（单一出水口 31、未按手册与空间约束，2026-10-23 作废，只作历史）/
    OPTC = L2+suggested v3（三层约束候选 → 贪心 + 剪枝，RDA_96C）/ OPTCS2 = v3 + 地面泵站（l2s_sumps_v3，按峰值加大）
    （2026-10-24 中间版：v3 只有 96 mm 1 h / 24 h 两场；判据按剔除 DTM 填 0 区的 eval2d_<雨>_ex.json）
用法：python tools/rda_console.py [--storms S96D1,...] [--no-frames]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
from rb01_console import storage  # noqa: E402
from rb01_design_l2s import pond_cells, pump_util  # noqa: E402
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
# OPTA61PS = 61 mm / 1 h 推荐方案（2026-10-27）；OPTA61PH2 / OPTCS3X = 最终方案：推荐方案 / 96 mm 方案 + 高后果积水点地面泵站（2026-10-28 接入页面）
STATES = ["L1", "L2", "L2P", "OPT20F", "OPTC", "OPTCS2", "OPTA61PS", "OPTA61PH2", "OPTCS3X"]
PAIRS = [("L1", "L2"), ("L1", "L2P"), ("L2P", "OPT20F"), ("L1", "OPT20F"), ("L2P", "OPTC"), ("L2P", "OPTCS2"), ("OPTC", "OPTCS2"), ("L2P", "OPTA61PS"),
         ("L2P", "OPTA61PH2"), ("OPTA61PS", "OPTA61PH2"), ("L2P", "OPTCS3X"), ("OPTCS2", "OPTCS3X")]
CHAIN = {"L1": ["L1"], "L2": ["L1", "L2"], "L2P": ["L1", "L2P"], "OPT20F": ["L1", "L2P", "OPT20F"], "OPTC": ["L1", "L2P", "OPTC"], "OPTCS2": ["L1", "L2P", "OPTCS2"],
         "OPTA61PS": ["L1", "L2P", "OPTA61PS"], "OPTA61PH2": ["L1", "L2P", "OPTA61PS", "OPTA61PH2"], "OPTCS3X": ["L1", "L2P", "OPTCS2", "OPTCS3X"]}
# 最终方案 = 基准方案 + 高后果积水点地面泵站：(基准态, 高后果泵站文件, 判据目录（客户热点 + HC 一起评）, 目标文件)
FINAL = {"OPTA61PH2": ("OPTA61PS", ROOT / "out/l2opt/RDA_A61P/sumps_OPTA61PH2.json", ROOT / "out/l2opt/RDA_HCA61", "HC_A61"),
         "OPTCS3X": ("OPTCS2", ROOT / "out/l2opt/RDA_96C/sumps_OPTCS3X.json", ROOT / "out/l2opt/RDA_HC96", "HC_96")}
OPT3 = ROOT / "out" / "l2opt" / "RDA_96C"
OPTD10 = ROOT / "out" / "l2opt" / "RDA_D10"
V3_KIND = {"tank_gravity": "重力进水地下调蓄池", "tank_pumped": "井边泵站 + 地下调蓄池", "ps_cluster": "泵站 → 新建排海口"}
HS = ["ADM-75", "ADM-76", "ADM-80", "ADM-161", "ADM-376", "ADM-377", "ADM-378", "ADM-379", "ADM-380", "ADM-382", "ADM-383"]
DESIGN = ["S25D1", "S31D2"]          # 2026-10-25 设计标准雨（10 年一遇 + 20 % CC：1 h 25.4 mm、2 h 30.8 mm；24 h = S61D24），档 1 用，判据在 RDA_D10
ALL = DESIGN + ["S33D1", "S33D2", "S33D24", "A61D1", "S61D1",   # A61D1 = AECOM「10 年 + 20 %」1 h 设计雨原样（2026-10-27）
       "S61D2", "S61D24", "S96D1", "S96D2", "S96D24"]
IS_DESIGN = set(DESIGN) | {"S61D24"}
OPT = ROOT / "out" / "l2opt" / "RDA_96"
UNION = "热点合计|200m"


def coords(inp):
    t = pathlib.Path(inp).read_text(encoding="utf-8", errors="ignore")
    sec = re.search(r"\[COORDINATES\]\n(.*?)(?:\n\[|\Z)", t, re.S).group(1)
    return {q[0]: (float(q[1]), float(q[2])) for q in (ln.split() for ln in sec.splitlines()) if len(q) >= 3 and not q[0].startswith(";")}


def sections(inp):
    out, cur = {}, None
    for ln in pathlib.Path(inp).read_text(encoding="utf-8", errors="ignore").splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]; out[cur] = []
        elif cur and s and not s.startswith(";"):
            out[cur].append(s.split())
    return out


def one(storm, frames=True):
    import geopandas as gpd
    from pyproj import Transformer
    from scipy.spatial import cKDTree
    from city2d_post import render_diff
    from shapely.geometry import shape
    name = f"RDA_{storm}"; dname = f"flood2d_rda_{storm.lower()}"; D = ROOT / "console" / "data" / dname
    info = json.loads((ROOT / "out" / f"rda_inputs_{storm}_L1.json").read_text(encoding="utf-8"))
    RAIN, DUR, SIM = float(info["rain_mm"]), float(info["dur_h"]), float(info["sim_h"])
    have = [s for s in STATES if (C2D / f"{name}_{s}_2d.npz").exists()]
    if frames:
        subprocess.run([sys.executable, str(HERE / "pilot_frames.py"), "--name", name, "--states", ",".join(have), "--out", dname,
                        "--region", "RD-A 汇水区（客户 SewerGEMS 模型子汇水区并集，3.15 km²）", "--scen", f"{RAIN:g}mm/{DUR:g}h", "--stats-tag", storm],
                       cwd=ROOT, check=True)
    tr = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    ll = lambda x, y: [round(v, 6) for v in tr.transform(x, y)[::-1]]   # noqa: E731
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
    masks[UNION] = np.any([masks[f"{h}|200m"] for h in HS], axis=0)
    masks["全域|all"] = ~pm
    res = {"name": name, "storm": storm, "rain_mm": RAIN, "dur_h": DUR, "states": have, "zones": {}, "diffs": [], "balance": {}, "hs": {}, "facilities": {}}
    for s in have:
        hm = Z[s]["hmax"].astype(float); fr = Z[s]["frames"].astype(np.float32)
        for k, m in masks.items():
            vols = (fr[:, m] * area[m]).sum(1); i = int(vols.argmax())
            res["zones"].setdefault(k, {})[s] = {"max_depth_m": round(float(hm[m].max()), 3), "area_ge_0p10_ha": round(float(area[m & (hm >= 0.10)].sum()) / 1e4, 2),
                                                 "area_ge_0p30_ha": round(float(area[m & (hm >= 0.30)].sum()) / 1e4, 2),
                                                 "peak_vol_m3": round(float(vols[i])), "peak_vol_t_h": round(float(th[i]), 2), "zone_area_ha": round(float(area[m].sum()) / 1e4, 1)}
        acc = TW[s]["acc_m3"]
        res["balance"][s] = {"1D→2D": round(acc["1to2"]), "2D→1D": round(acc["2to1"]), "进塘（地表）": round(acc["pond"]), "地面泵抽走": round(acc.get("pump_out", 0)),
                             f"域内留存（{SIM:g} h 末）": round(TW[s]["final_vol_m3"]), "边界出流（余项）": round(TW[s]["boundary_out_m3"])}
        for h in HS:
            idx = T.query_ball_point((H.loc[h].geometry.x, H.loc[h].geometry.y), 50.0)
            r50 = fr[:, idx].max(1)
            dt = float(np.diff(th).mean())
            res["hs"].setdefault(h, {})[s] = {"r50": [round(float(v), 3) for v in r50], "peak_m": round(float(r50.max()), 2),
                                              "h_ge30": round(float((r50 >= 0.30).sum() * dt), 2), "end_m": round(float(r50[-1]), 2)}
    res["hs_t_h"] = [round(float(v), 4) for v in th]
    for s1, s2 in PAIRS:
        if s1 in Z and s2 in Z:
            dh = Z[s2]["hmax"].astype(float) - Z[s1]["hmax"].astype(float); dh[pm] = 0
            fn = f"diff_{s2}_minus_{s1}.png"; b = render_diff(xy, dh, 15.0, D / fn)
            res["diffs"].append({"a": s1, "b": s2, "png": fn, "bounds": b, "shallower_ha": round(float(area[dh <= -0.05].sum()) / 1e4, 2),
                                 "deeper_ha": round(float(area[dh >= 0.05].sum()) / 1e4, 2)})
    # ---- 设施
    PU = {s: pump_util(C2D / f"{name}_{s}_2way.rpt") for s in have}
    ST = {s: storage(C2D / f"{name}_{s}_2way.rpt") for s in have}

    def sim_pump(pid, sts):
        return {s: {"vol_m3": round(PU[s][pid][1]), "util_pct": PU[s][pid][0]} for s in sts if pid in PU.get(s, {})}

    def sim_pond(node, sts):
        return {s: {"max_m3": round(ST[s][node][0]), "max_pct": ST[s][node][1]} for s in sts if node in ST.get(s, {})}

    def outfall_of(S, start):
        """沿压力管 / 管段往下游追到出水口"""
        nxt = {}
        for c in S.get("CONDUITS", []):
            nxt.setdefault(c[1], []).append(c[2])
        of = {o[0] for o in S.get("OUTFALLS", [])}
        seen, st = set(), [start]
        while st:
            n = st.pop()
            if n in of:
                return n
            if n in seen:
                continue
            seen.add(n); st += nxt.get(n, [])
        return None

    F = {}
    S1 = sections(WORK / f"{name}_L1.inp"); C1 = coords(WORK / f"{name}_L1.inp")
    qmax = {}
    for c in S1.get("CURVES", []):
        if len(c) >= 3:
            try:
                qmax[c[0]] = max(qmax.get(c[0], 0.0), float(c[-1]))
            except ValueError:
                pass
    F["L1"] = []
    for p in S1["PUMPS"]:
        of = outfall_of(S1, p[2])
        F["L1"].append({"kind": "ps_l1", "id": p[0], "q_lps": round(qmax.get(p[3], 0) * 1000), "ll": ll(*C1[p[1]]), "to_ll": ll(*C1[of]) if of in C1 else None,
                        "p": {"on": round(float(p[5]), 2), "off": round(float(p[6]), 2),
                              "why": f"现有 RD-A 泵站（全市管网 SW_ABT_W33_PSS_69，注记 350 & 80×2）：湿井 {p[1]} → 出水口 {of}；泵曲线最大流量取客户 Pump3 曲线"},
                        "sim": sim_pump(p[0], [s for s in have])})
    if "L2" in have:
        i2 = json.loads((ROOT / "out" / f"rda_inputs_{storm}_L2.json").read_text(encoding="utf-8"))
        F["L2"] = [{"kind": "mobile", "id": p["id"][3:], "q_lps": round(p["q_m3s"] * 1000), "ll": ll(*C1[p["from"]]), "to_ll": ll(*C1[p["to"]]),
                    "p": {"hotspot": p["hotspot"], "size": p["size"], "well": p["well_depth_m"], "on": p["on"], "off": p["off"], "path": p["path_m"],
                          "why": "热点表 Plan A 预置移动泵：吸水井 = 热点最近的井，排到沿管往下游第一口在 L1 一维结果里不溢流的井"},
                    "sim": sim_pump(p["id"], ["L2"])} for p in i2["pumps_l2"]]
    if "L2P" in have:
        SP = sections(WORK / f"{name}_L2P.inp"); CP = coords(WORK / f"{name}_L2P.inp")
        base = {p[0] for p in S1["PUMPS"]}
        qp = {}
        for c in SP.get("CURVES", []):
            if len(c) >= 3:
                try:
                    qp[c[0]] = max(qp.get(c[0], 0.0), float(c[-1]))
                except ValueError:
                    pass
        F["L2P"] = []
        for p in SP["PUMPS"]:
            if p[0] in base:
                continue
            of = outfall_of(SP, p[2])
            F["L2P"].append({"kind": "ps_planned", "id": p[0], "q_lps": round(qp.get(p[3], 0) * 1000), "ll": ll(*CP[p[1]]), "to_ll": ll(*CP[of]) if of in CP else None,
                             "p": {"why": f"客户 SewerGEMS 情景「Temporary Intervention」（inp 51159）里的泵：吸水井 {p[1]} → 出水口 {of}；"
                                          "设施台账状态「设计中」、挂 ADM-380 等热点（可信度弱）。同情景的泵 51169 判「只是方案」，未纳入"},
                             "sim": sim_pump(p[0], [s for s in have if s in ("L2P", "OPT20F")])})
    if "OPT20F" in have:
        var = json.loads((OPT / f"variant_{storm}_OPT20.json").read_text(encoding="utf-8"))
        cand = {c["id"]: c for c in json.loads((OPT / "candidates.json").read_text(encoding="utf-8"))["candidates"]}
        G = {f["properties"]["id"]: f["geometry"] for f in json.loads((OPT / "candidates.geojson").read_text(encoding="utf-8"))["features"]}
        CO = coords(WORK / f"{name}_OPT20F.inp")
        lat = lambda g: [[round(y, 6), round(x, 6)] for x, y in g]   # noqa: E731  WGS84 lon,lat → lat,lon
        fs = []
        for m in var["measures"]:
            c = cand[m["id"]]; k = c["kind"]; g = shape(G[m["id"]]) if m["id"] in G else None
            w = CO.get(c.get("well"))
            if k in ("pump_newpond", "gravity"):
                poly = g if g is not None and g.geom_type in ("Polygon", "MultiPolygon") else None
                if poly is not None and poly.geom_type == "MultiPolygon":
                    poly = max(poly.geoms, key=lambda q: q.area)
                pc = [round(poly.centroid.y, 6), round(poly.centroid.x, 6)] if poly is not None else None
                fs.append({"kind": "pond", "id": f"NP_{m['id']}", "vol_m3": c.get("pond_vol_m3"), "ll": pc, "poly": lat(poly.exterior.coords) if poly is not None else None,
                           "p": {"vol": c.get("pond_vol_m3"), "depth": c.get("pond_depth_m"), "floor": round(c.get("pond_top_m", 0) - c.get("pond_depth_m", 0), 2),
                                 "why": f"新塘（{'重力接' if k == 'gravity' else '泵送'}，井 {c.get('well')}，热点 {c.get('hotspot')}）；用地 {c.get('land') or '—'}"},
                           "sim": sim_pond(f"NP_{m['id']}", ["OPT20F"])})
                if k == "gravity" and w and pc:
                    fs.append({"kind": "gpipe", "id": m["id"], "ll": ll(*w), "line": [ll(*w), pc],
                               "p": {"path": c.get("pipe_m"), "depth": c.get("pipe_d_m"), "why": f"重力管 井 {c.get('well')} → 新塘（n 0.012、拍门）"}})
                elif w and pc:
                    fs.append({"kind": "conv", "id": m["id"], "q_lps": round(c["q_m3s"] * 1000), "ll": ll(*w), "to_ll": pc,
                               "p": {"n": c.get("n_pumps"), "hotspot": c.get("hotspot"), "path": c.get("pipe_m"),
                                     "why": f"截流泵 井 {c.get('well')} → 新塘；压力管 DN{int((c.get('main_d_m') or 0) * 1000)} × {c.get('n_mains')}，扬程 {c.get('head_m')} m（WA-726 7.3.2）"},
                               "sim": sim_pump(f"P_{m['id']}", ["OPT20F"])})
            elif k == "pump_outfall" and w:
                to = c.get("of_xy")
                fs.append({"kind": "conv", "id": m["id"], "q_lps": round(c["q_m3s"] * 1000), "ll": ll(*w), "to_ll": ll(*to) if to else None,
                           "p": {"n": c.get("n_pumps"), "hotspot": c.get("hotspot"), "path": c.get("pipe_m"),
                                 "why": f"截流泵 井 {c.get('well')} → 海边出水口 31（尾水 1.3 m）；压力管 DN{int((c.get('main_d_m') or 0) * 1000)} × {c.get('n_mains')}，扬程 {c.get('head_m')} m"},
                           "sim": sim_pump(f"P_{m['id']}", ["OPT20F"])})
        sm = json.loads((OPT / "sumps_OPT20F.json").read_text(encoding="utf-8"))
        polys = {q["id"]: q for q in META["OPT20F"].get("pumps2d", [])}
        pumped = {q["id"]: q["pumped_m3"] for q in TW["OPT20F"].get("pumps2d", [])}
        of31 = C1.get("31")
        for s_ in sm["sumps"]:
            q = polys.get(s_["id"], {})
            fs.append({"kind": "sump", "id": s_["id"], "q_lps": round(s_["q_m3s"] * 1000), "ll": ll(s_["sx"], s_["sy"]), "to_ll": ll(*of31) if of31 else None,
                       "poly": [ll(x, y) for x, y in q.get("sump_polygon", [])] or None,
                       "p": {"n": s_["n_pumps"], "hotspot": s_["hotspot"], "peak": s_.get("vol_peak_m3"), "path": s_["pipe_m"],
                             "why": "地面取水泵：二维里热点附近退不掉的低洼积水（井口比坑底高，管网收不进去），抽到海边出水口 31" + ("；手工补点" if s_["ponding_point"] == "手工补点" else "")},
                       "sim": {"OPT20F": {"vol_m3": round(pumped.get(s_["id"], 0))}}})
        F["OPT20F"] = fs

    def v3_facs(st):
        """v3 方案（OPTC / OPTCS2）的设施：地下池画占地，泵站画到排口的连线；OPTCS2 另加地面泵站"""
        var = json.loads((OPT3 / f"variant_{storm}_OPTC.json").read_text(encoding="utf-8"))
        cand = {c["id"]: c for c in json.loads((OPT3 / "candidates.json").read_text(encoding="utf-8"))["candidates"]}
        comp = json.loads((OPT3 / "compliance.json").read_text(encoding="utf-8"))
        G = {f["properties"]["id"]: f["geometry"] for f in json.loads((OPT3 / "candidates.geojson").read_text(encoding="utf-8"))["features"]}
        CO = coords(WORK / f"{name}_{st}.inp")
        lat = lambda g: [[round(y, 6), round(x, 6)] for x, y in g]   # noqa: E731
        todo = lambda cid: "；".join(i["zh"] for i in comp.get(cid, []) if i["result"] in ("review", "unknown"))   # noqa: E731
        fs = []
        for m in var["measures"]:
            c = cand[m["id"]]; k = c["kind"]; w = CO.get(c.get("well"))
            cost = f"占位造价 {c['cost_usd'] / 1e6:.2f} M USD（{c.get('cost_basis', '')}）"
            if k in ("tank_gravity", "tank_pumped"):
                g = shape(G[m["id"]]) if m["id"] in G else None
                if g is not None and g.geom_type == "MultiPolygon":
                    g = max(g.geoms, key=lambda q: q.area)
                pc = [round(g.centroid.y, 6), round(g.centroid.x, 6)] if g is not None and g.geom_type == "Polygon" else None
                fs.append({"kind": "tank", "id": m["id"], "vol_m3": c.get("pond_vol_m3"), "ll": pc, "poly": lat(g.exterior.coords) if pc else None,
                           "p": {"vol": c.get("pond_vol_m3"), "depth": c.get("pond_depth_m"), "floor": c.get("z_floor"),
                                 "why": f"{V3_KIND[k]}（截流井 {c.get('well')}，热点 {c.get('hotspot')}，开挖 {c.get('excavation_m', '—')} m）；用地 {c.get('land') or '—'}；{cost}；"
                                        f"有条件可建，待办：{todo(m['id']) or '—'}"},
                           "sim": sim_pond(f"NP_{m['id']}", [st])})
                if w and pc:
                    fs.append({"kind": "gpipe" if k == "tank_gravity" else "conv", "id": m["id"] + ("_g" if k == "tank_gravity" else ""), "ll": ll(*w),
                               **({"line": [ll(*w), pc]} if k == "tank_gravity" else {"to_ll": pc, "q_lps": round(c.get("q_m3s", 0) * 1000)}),
                               "p": {"path": c.get("pipe_m"), "why": (f"重力管 井 {c.get('well')} → 地下池，长 {c.get('pipe_m')} m，管径 {c.get('pipe_d_m')} m" if k == "tank_gravity"
                                                                     else f"井边泵站 {c.get('n_pumps')} 台 → 压力管 {c.get('pipe_m')} m DN{int((c.get('main_d_m') or 0) * 1000)} → 地下池")},
                               "sim": {} if k == "tank_gravity" else sim_pump(f"P_{m['id']}", [st])})
            elif k == "ps_cluster":
                ps = (c["ps_x"], c["ps_y"])
                fs.append({"kind": "conv", "id": m["id"], "q_lps": round(c["q_m3s"] * 1000), "ll": ll(*ps), "to_ll": ll(*c["of_xy"]),
                           "p": {"n": c.get("n_pumps"), "hotspot": c.get("hotspot"), "path": c.get("pipe_m"),
                                 "why": f"{V3_KIND[k]}：截流井 {', '.join(c.get('wells') or [])} → 湿井 → {c.get('n_pumps')} 台 × {c.get('pump_each_m3s')} m³/s → 压力管 {c.get('pipe_m')} m "
                                        f"DN{int(c['main_d_m'] * 1000)}×{c['n_mains']}（流速 {c.get('v_ms')} m/s）→ 新建排海口 {c.get('outfall_site')}；场地 {c.get('ps_site_m2')} m²；{cost}；"
                                        f"有条件可建，待办：{todo(m['id']) or '—'}"},
                           "sim": sim_pump(f"P_{m['id']}", [st])})
        if st in ("OPTCS2", "OPTCS3X"):
            sm = json.loads((OPT3 / "sumps_OPTCS2.json").read_text(encoding="utf-8"))
            pl = [q["pumped_m3"] for q in TW[st].get("pumps2d", [])]       # 按顺序：最终方案里基准泵站在前、高后果泵站在后，编号会重复
            k_ = 0
            for s_ in sm["stations"]:
                for j, it in enumerate(s_["intakes"]):
                    fs.append({"kind": "sump", "id": f"{s_['id']}_{j}", "q_lps": round(it["q_m3s"] * 1000), "ll": ll(it["x"], it["y"]), "to_ll": ll(s_["ps_x"], s_["ps_y"]),
                               "poly": [ll(x, y) for x, y in it["poly"]],
                               "p": {"hotspot": s_["hotspot"], "why": f"地面取水口（{it['class']}）→ 地面泵站 {s_['id']}{('；' + it['why']) if it['why'] else ''}"},
                               "sim": {st: {"vol_m3": round(pl[k_] if k_ < len(pl) else 0)}}})
                    k_ += 1
                fs.append({"kind": "conv", "id": s_["id"], "q_lps": round(s_["q_m3s"] * 1000), "ll": ll(s_["ps_x"], s_["ps_y"]), "to_ll": ll(*s_["of_xy"]),
                           "p": {"n": s_["n_pumps"], "hotspot": s_["hotspot"], "path": s_["pipe_m"],
                                 "why": f"地面泵站（按小泵站过可建性检查，DOM-B11）：{len(s_['intakes'])} 个取水口，{s_['n_pumps']} 台 × {s_['pump_each_m3s']} m³/s → 压力管 {s_['pipe_m']} m "
                                        f"DN{int(s_['main_d_m'] * 1000)} → 新建排海口 {s_['outfall_site']}；占位造价 {s_['cost_usd'] / 1e6:.2f} M USD；{s_['status']}"}})
        return fs
    for st in ("OPTC", "OPTCS2", "OPTCS3X"):
        if st in have:
            F[st] = v3_facs(st)
    from export_rda_61_full import suggested_facs
    if "OPTA61PS" in have:                                          # 61 mm / 1 h 推荐方案：当前方案按 AECOM 雨型剪枝 + 同一套地面泵站（与 tools/export_rda_61_full.py 同一画法）
        F["OPTA61PS"] = suggested_facs(storm)
    if "OPTA61PH2" in have:
        F["OPTA61PH2"] = suggested_facs(storm)

    def hc_facs(st):
        """高后果积水点地面泵站（最终方案在基准方案上加的部分）；模拟量接在基准泵站之后按顺序取"""
        sm = json.loads(FINAL[st][1].read_text(encoding="utf-8"))
        base_n = sum(len(s_["intakes"]) for s_ in json.loads((OPT3 / "sumps_OPTCS2.json").read_text(encoding="utf-8"))["stations"])
        pl = [q["pumped_m3"] for q in TW[st].get("pumps2d", [])][base_n:]
        fs, k_ = [], 0
        for s_ in sm["stations"]:
            sid = f"HC{s_['id']}"
            for j, it in enumerate(s_["intakes"]):
                fs.append({"kind": "sump", "id": f"{sid}_{j}", "q_lps": round(it["q_m3s"] * 1000), "ll": ll(it["x"], it["y"]), "to_ll": ll(s_["ps_x"], s_["ps_y"]),
                           "poly": [ll(x, y) for x, y in it["poly"]],
                           "p": {"hotspot": s_["hotspot"], "why": f"地面取水口（{it['class']}）→ 高后果积水点 {s_['hotspot']} 的地面泵站 {sid}"},
                           "sim": {st: {"vol_m3": round(pl[k_] if k_ < len(pl) else 0)}}})
                k_ += 1
            fs.append({"kind": "conv", "id": sid, "q_lps": round(s_["q_m3s"] * 1000), "ll": ll(s_["ps_x"], s_["ps_y"]), "to_ll": ll(*s_["of_xy"]),
                       "p": {"n": s_["n_pumps"], "hotspot": s_["hotspot"], "path": s_["pipe_m"],
                             "why": f"高后果积水点 {s_['hotspot']} 的地面泵站：{len(s_['intakes'])} 个取水口，{s_['n_pumps']} 台 × {s_['pump_each_m3s']} m³/s → 压力管 {s_['pipe_m']} m "
                                    f"DN{int(s_['main_d_m'] * 1000)} → 排口 {s_['outfall_site']}；占位造价 {s_['cost_usd'] / 1e6:.2f} M USD；{s_['status']}"}})
        return fs
    for st in FINAL:
        if st in F:
            F[st] = F[st] + hc_facs(st)
    res["facilities"] = F; res["chain"] = CHAIN

    # ---- 造价（2026-10-24 用户：「页面里补充 L2+suggested 对应的成本」）：按设施类型汇总占位造价，注明单价来源
    BASIS = {"tank_gravity": "池体 350 USD/m³、重力管、排空泵：占位单价",
             "tank_pumped": "压力管按 WA-726 第三卷 p349 单价；池体、泵站、湿井：占位",
             "ps_cluster": "压力管、排海口按 WA-726 第三卷 p349 单价；泵站、湿井、重力连接管：占位",
             "surface_ps": "压力管、排海口按 WA-726 第三卷 p349 单价；泵站、湿井、取水井：占位"}
    KLAB = {"tank_gravity": "重力进水地下调蓄池", "tank_pumped": "井边泵站 + 地下调蓄池", "ps_cluster": "泵站 → 新建排海口", "surface_ps": "地面泵站 → 新建排海口"}

    def cost_v3(st):
        var = json.loads((OPT3 / f"variant_{storm}_OPTC.json").read_text(encoding="utf-8"))
        cand = {c["id"]: c for c in json.loads((OPT3 / "candidates.json").read_text(encoding="utf-8"))["candidates"]}
        rows, of = {}, set()
        for m in var["measures"]:
            k = m["kind"]; r = rows.setdefault(k, {"n": 0, "usd": 0.0})
            r["n"] += 1; r["usd"] += m["cost_usd"]
            if cand[m["id"]].get("outfall_site"):
                of.add(cand[m["id"]]["outfall_site"])
        if st == "OPTCS2":
            sm = json.loads((OPT3 / "sumps_OPTCS2.json").read_text(encoding="utf-8"))
            r = rows.setdefault("surface_ps", {"n": 0, "usd": 0.0})
            for x in sm["stations"]:
                r["n"] += 1; r["usd"] += x["cost_usd"]; of.add(x["outfall_site"])
        items = [{"kind": k, "label": KLAB[k], "n": v["n"], "usd": round(v["usd"]), "basis": BASIS[k]} for k, v in rows.items()]
        return {"total_usd": round(sum(i["usd"] for i in items)), "items": items, "n_outfalls_new": len(of),
                "note": "占位造价：只用于方案比较，不是预算。压力管、排海口用 WA-726 第三卷附录 L 单价，其余为占位单价；未含 NPV（运维、更换），NPV 对比表随成果说明给出。"}

    def cost_v1():
        var = json.loads((OPT / f"variant_{storm}_OPT20.json").read_text(encoding="utf-8"))
        sm = json.loads((OPT / "sumps_OPT20F.json").read_text(encoding="utf-8"))
        tot = sum(m["cost_usd"] for m in var["measures"]) + sum(x["cost_usd"] for x in sm["sumps"])
        return {"total_usd": round(tot), "items": [{"kind": "v1", "label": f"{len(var['measures'])} 项一维措施 + {len(sm['sumps'])} 处地面泵", "n": len(var["measures"]) + len(sm["sumps"]),
                                                    "usd": round(tot), "basis": "占位单价"}], "n_outfalls_new": 0,
                "note": "v1 已作废（单一出水口、未按手册和空间约束），造价只作历史对照。"}
    res["cost"] = {}
    for st in ("OPTC", "OPTCS2"):
        if st in have:
            res["cost"][st] = cost_v3(st)
    if "OPTA61PS" in have:
        A61P = ROOT / "out" / "l2opt" / "RDA_A61P"
        var = json.loads((A61P / f"variant_{storm}_OPTA61P.json").read_text(encoding="utf-8"))
        cand = {c["id"]: c for c in json.loads((A61P / "candidates.json").read_text(encoding="utf-8"))["candidates"]}
        rows, of = {}, set()
        for m in var["measures"]:
            r = rows.setdefault(m["kind"], {"n": 0, "usd": 0.0}); r["n"] += 1; r["usd"] += m["cost_usd"]
            if cand[m["id"]].get("outfall_site"):
                of.add(cand[m["id"]]["outfall_site"])
        r = rows.setdefault("surface_ps", {"n": 0, "usd": 0.0})
        for x in json.loads((OPT3 / "sumps_OPTCS2.json").read_text(encoding="utf-8"))["stations"]:
            r["n"] += 1; r["usd"] += x["cost_usd"]; of.add(x["outfall_site"])
        items = [{"kind": k, "label": KLAB[k], "n": v["n"], "usd": round(v["usd"]), "basis": BASIS[k]} for k, v in rows.items()]
        res["cost"]["OPTA61PS"] = {"total_usd": round(sum(i["usd"] for i in items)), "items": items, "n_outfalls_new": len(of),
                                   "note": "61 mm / 1 h 推荐方案：当前方案（按 96 mm 优化）以 AECOM 1 h 设计雨为控制雨剪枝（删 7、换 6），地面泵站全部保留。占位造价，只用于方案比较。"}
    for st, (base, sf, _, _) in FINAL.items():                       # 最终方案 = 基准方案造价 + 高后果积水点地面泵站
        if st in have and base in res["cost"]:
            sm = json.loads(sf.read_text(encoding="utf-8"))
            c = json.loads(json.dumps(res["cost"][base]))
            c["items"].append({"kind": "surface_ps_hc", "label": "高后果积水点地面泵站 → 排口", "n": len(sm["stations"]), "usd": round(sum(x["cost_usd"] for x in sm["stations"])),
                               "basis": BASIS["surface_ps"]})
            c["total_usd"] = round(sum(i["usd"] for i in c["items"]))
            c["n_outfalls_new"] = c["n_outfalls_new"] + len({x["outfall_site"] for x in sm["stations"]} - {None})
            c["note"] = (f"最终方案：在{'61 mm / 1 h 推荐方案' if base == 'OPTA61PS' else '96 mm 方案'}上，给不在客户热点附近、后果高且不能自然退干的模型积水点"
                         f"（{FINAL[st][3].replace('_', '-')} 共 {len(sm['stations'])} 座泵站）补地面泵站。占位造价，只用于方案比较。")
            res["cost"][st] = c
    if "OPT20F" in have:
        try:
            res["cost"]["OPT20F"] = cost_v1()
        except FileNotFoundError:
            pass
    # ---- 雨型
    import runoff as R
    shape_ = R.read_shape(WORK / "Mussafah_baseline.inp", DUR, log=lambda *x: None)
    t5 = np.arange(0, SIM + 1e-9, 1 / 12); _, P = R.hyetograph(RAIN, DUR, 1 / 12, shape_, 0.0, total_h=SIM)
    src = f"客户 Mussafah 模型「10 年一遇含 CC {DUR:g} h」雨型等比放大"
    if storm == "A61D1":                                            # AECOM 雨型原样：3 分钟一段（序列单位英寸）
        import rda_build as RB
        inp, ts = info["rain_shape"].split(" · ")
        cum = RB.aecom_shape(str(ROOT / "20261019/test/RD-A/RD-A/Hydraulic Model" / inp), ts, DUR, log=lambda *x: None) * 25.4
        t5 = np.arange(0, SIM + 1e-9, 1 / 20); P = np.interp(t5, np.linspace(0, DUR, len(cum)), cum, right=cum[-1])
        src = "AECOM RD-A 模型「10yrs.+20%CC 1 h」设计雨原样（3 分钟一段）"
    exc = np.array(META["L1"]["rain_excess_mm"]); te = np.array(META["L1"]["rain_t_h"])
    ex5 = np.interp(t5, te, exc)
    inc = np.diff(P, prepend=0)[1:]
    k_ = 1.0 / float(t5[1] - t5[0])                                 # 每小时几段
    res["hyeto"] = {"t_h": [round(float(v), 4) for v in t5[:-1]], "p_mmph": [round(float(v) * k_, 2) for v in inc], "p_cum_mm": [round(float(v), 2) for v in P[1:]],
                    "exc_cum_mm": [round(float(v), 2) for v in ex5[1:]], "dt_min": round(60 / k_), "sim_h": SIM, "dur_h": DUR, "rain_mm": RAIN,
                    "peak_mmph": round(float(inc.max()) * k_, 1), "peak_t_h": round(float(t5[int(inc.argmax())]), 3), "excess_mm": round(float(exc[-1]), 1),
                    "source": src}
    # ---- 判据 A–D（l2opt_eval2d 的结果）
    # v3 两场雨：用剔除 DTM 填 0 区的口径（RDA_96C/eval2d_<雨>_ex.json），基线各态也按同一口径；其余雨和 v1 沿用 RDA_96 的旧口径
    E = {}
    for ev in (OPT / f"eval2d_{storm}.json", OPTD10 / f"eval2d_{storm}_ex.json", OPT3 / f"eval2d_{storm}_ex.json", ROOT / "out" / "l2opt" / "RDA_A61" / f"eval2d_{storm}_ex.json"):
        if ev.exists():
            E.update(json.loads(ev.read_text(encoding="utf-8"))["states"])
    res["eval_basis"] = "剔除 ADM-80 的 DTM 填 0 区（out/l2opt/RDA_96C/exclude_OPTCS.geojson）" if (OPT3 / f"eval2d_{storm}_ex.json").exists() or (OPTD10 / f"eval2d_{storm}_ex.json").exists() else "全域"
    EH = {}                                                         # 客户热点 + 高后果积水点一起评的结果（RDA_HCA61 / RDA_HC96）
    for st, (base, _, d_, _) in FINAL.items():
        f_ = d_ / f"eval2d_{storm}_ex.json"
        if f_.exists():
            for s, v in json.loads(f_.read_text(encoding="utf-8"))["states"].items():
                EH.setdefault(s, v)
                if s == st:
                    E.setdefault(s, v)
    if E:                                                           # 「热点达标」只数 11 个客户热点；高后果积水点另列 eval_hc
        res["eval"] = {s: {"pass": sum(all(v["pass"].values()) for h, v in E[s]["hotspots"].items() if h in HS), "n": len([h for h in E[s]["hotspots"] if h in HS]),
                           "hs": {h: v["pass"] for h, v in E[s]["hotspots"].items() if h in HS}, "pass_D": E[s]["domain"]["pass_D"]} for s in have if s in E}
    if EH:
        res["eval_hc"] = {s: {h: v["pass"] for h, v in EH[s]["hotspots"].items() if h.startswith("HC-")} for s in have if s in EH}
    from console_en import add_en; add_en(res)   # 英文界面用的 *_en 字段（2026-10-28）
    (D / "rda_stats.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print(f"→ {D / 'rda_stats.json'}：{len(res['diffs'])} 张差值图、设施 " + "、".join(f"{k} {len(v)}" for k, v in F.items()))
    return {"storm": storm, "rain_mm": RAIN, "dur_h": DUR, "dir": dname, "states": have, "design": storm in IS_DESIGN,
            **({"label": "60.7 mm / 1 h（AECOM 雨型）", "label_en": "60.7 mm / 1 h (AECOM pattern)"} if storm == "A61D1" else {}), "eval": {s: v["pass"] for s, v in res.get("eval", {}).items()}}


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--storms", default=",".join(ALL)); ap.add_argument("--no-frames", action="store_true")
    a = ap.parse_args(argv)
    idx_f = ROOT / "console" / "data" / "flood2d_rda" / "index.json"
    idx_f.parent.mkdir(parents=True, exist_ok=True)
    idx = json.loads(idx_f.read_text(encoding="utf-8")) if idx_f.exists() else {"storms": []}
    by = {s["storm"]: s for s in idx["storms"]}
    for s in a.storms.split(","):
        by[s] = one(s, frames=not a.no_frames)
    idx["storms"] = [by[s] for s in ALL if s in by]
    idx["hotspots"] = HS
    idx_f.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {idx_f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
