# -*- coding: utf-8 -*-
"""L1 / L2 / L2+ 各片区结果目录：智能体本体动作（state_gap / city_gap / facility_plan / as_built_next）的**唯一数据源**。

为什么要单独一份：三个片区的指标口径不一样（Mussafah 用 state_table 的全表；29.RB2 与 Madinat Al Riyad 用
pilot_frames 的 ≥0.10 / ≥0.30 m 面积），设施散在十几个文件里（why_*.json、*.ponds.geojson、c2d/pilot_*.json）。
这里统一成「片区 → 态 → 指标 / 设施」，数字与控制台、在线报告同源，本脚本不算任何新东西。

态的命名（对外）：
  Mussafah_00   L1 → L2 → L2+A（Parsons，有报告）→ L2+A+B；L2+B 对照
  29.RB2        L1 → L2（BNS 塘）→ L2+A（PO 塘方案，**来源待确认**）→ L2+A+B（严格 / 放宽）；L2+B（严格 / 放宽）对照
  Madinat Al Riyad  L1 → L2（20 座已建成塘）

用法：python tools/l2plus_catalog.py      产物：console/data/l2plus_catalog.json
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
OUT = ROOT / "out"
DATA = ROOT / "console" / "data"
WORK = pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))


def _j(p):
    p = pathlib.Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _tr():
    from pyproj import Transformer
    return Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)


def _ll(tr, x, y):
    lo, la = tr.transform(x, y)
    return round(lo, 6), round(la, 6)


def _centroid(geom):
    ring = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
    pts = ring[:-1] if ring[0] == ring[-1] else ring
    return round(sum(p[0] for p in pts) / len(pts), 6), round(sum(p[1] for p in pts) / len(pts), 6)


def _boxes(gj_path, state, source):
    g = _j(gj_path)
    out = []
    for f in (g or {}).get("features", []):
        p = f["properties"]; lon, lat = _centroid(f["geometry"])
        out.append({"id": p["id"], "state": state, "kind": "box", "kind_zh": "地下箱涵", "source": source,
                    "volume_m3": p.get("vol_m3"), "bottom_m": p.get("bottom_m"), "top_m": p.get("top_m"),
                    "land": p.get("land"), "moved_m": p.get("moved_m"), "inflow": p.get("inflow"),
                    "dn_mm": p.get("dn_mm"), "conveyance_m": p.get("conveyance_m"), "lon": lon, "lat": lat})
    return out


def mussafah(tr):
    st = _j(OUT / "state_table_Mussafah_00_R61D2.json")
    why = _j(OUT / "l2plus" / "why_Mussafah_R61D2.json") or {}
    if not st:
        return None
    T = st["table"]
    keys = [("溢出量", "洪水量(节点累计)", "Overflow (node sum)", "m³"), ("最大积水面积", "二维·峰值淹没面积", "Max flooded area", "ha"),
            ("最大积水深度", "二维·峰值水深", "Max depth", "m"), ("街面峰值滞水", "峰值滞水", "Peak street ponding", "m³"),
            ("街面退水（主网）", "退水(主网)", "Recession (majority)", "h"), ("街面退水（残余上游）", "退水(残余上游)", "Recession (residual)", "h"),
            ("蓄存能力", "蓄存能力", "Storage capacity", "m³")]
    metrics = [{"key": k2, "zh": zh, "en": en, "unit": u, "better": T[k2]["better"], "values": T[k2]["values"]} for zh, k2, en, u in keys if k2 in T]
    fac = {"L2+A": [], "L2+B": [], "L2+A+B": []}
    for p in why.get("parsons", []):
        lon, lat = _ll(tr, p["x"], p["y"])
        fac["L2+A"].append({"id": p["id"], "state": "L2+A", "kind": "pond" if p["id"].startswith("STO") else "well",
                            "kind_zh": "临时塘" if p["id"].startswith("STO") else "集水井", "source": "Parsons（交付模型原件）",
                            "volume_m3": p.get("volume_m3"), "bottom_m": p.get("bottom_m"), "top_m": p.get("top_m"), "lon": lon, "lat": lat})
    for o in why.get("ours", []):
        lon, lat = _ll(tr, o["x"], o["y"])
        fac["L2+B"].append({"id": o["id"], "state": "L2+B", "kind": "box", "kind_zh": "地下箱涵", "source": "本系统（从 L1 出发选址）",
                            "volume_m3": o.get("vol_usable_m3"), "land": o.get("land"), "moved_m": o.get("moved_m"),
                            "served_m3": o.get("served_m3"), "share_of_gap_pct": o.get("share_of_gap"), "lon": lon, "lat": lat})
    fac["L2+A+B"] = fac["L2+A"] + _boxes(DATA / "l2ab_fp.geojson", "L2+A+B", "本系统（在 Parsons 之上选址）")
    return {"label": "Mussafah_00", "aliases": ["mussafah", "musaffah", "mussafah_00", "穆萨法"], "area_ha": st.get("region_ha"),
            "scenario": "61 mm / 2 h（B 区 2 h 百年一遇的 1.11 倍）", "model": "SWMM 5.2 + ANUGA 15 m 双向耦合",
            "chain": ["L1", "L2", "L2+A", "L2+A+B"], "refs": {"L2+B": "L2"},
            "states": {"L1": "既有管网", "L2": "已建成调蓄（区内 0 座，≡ L1）", "L2+A": "Parsons 2027 过渡方案（1 塘 + 2 井 + 2 泵）",
                       "L2+A+B": "Parsons 视为既成事实，其上再加本系统 6 座地下箱涵", "L2+B": "对照：不做 Parsons、单做本系统 6 座箱涵"},
            "metrics": metrics, "facilities": fac,
            "overlap": "叠加后 Parsons 的塘最高只装到 55%（单做 L2+A 时 93%）：上游的水先被箱涵接走了；如果箱涵要建，Parsons 那座塘可以考虑缩小或挪位。",
            "notes": ["Parsons 设施按交付模型原件：塘 STO-PO3 1,200 m³（报告表 11 写 900 m³，待客户确认）",
                      "箱涵定容的 1.5 倍聚类系数是假设、未标定；可用水深 2.0 m + 0.3 m 超高",
                      "L2+A 不是复现 Parsons 报告：报告用 61.34 mm / 24 h，这里是 61 mm / 2 h"],
            "console": {"tab": "line2p", "l2p": "wb"},
            "report": "https://claude.ai/artifact/8w5RRx7f1bx1HJgESs9cYD"}


PF_KEYS = [("≥0.30 m 最大积水面积", "max_area_ge_0p30_ha", "Max area ≥0.30 m", "ha", "low"),
           ("≥0.10 m 最大积水面积", "max_area_ge_0p10_ha", "Max area ≥0.10 m", "ha", "low"),
           ("最大积水深度", "max_depth_m", "Max depth", "m", "low"),
           ("管网溢流进地面", "flood_1d_to_2d_m3", "1D → 2D overflow", "m³", "low"),
           ("6 h 末地面上的水", "final_vol_m3", "Water on the ground at 6 h", "m³", "low"),
           ("地表进塘", "into_ponds_surface_m3", "Surface water into ponds", "m³", "high")]


def _pf_metrics(stats, name_of):
    out = []
    for zh, k, en, u, b in PF_KEYS:
        vals = {name_of[s]: stats[s][k] for s in name_of if s in stats and k in stats[s]}
        if vals:
            out.append({"key": k, "zh": zh, "en": en, "unit": u, "better": b, "values": vals})
    return out


def rb2(tr):
    stats = _j(OUT / "pilot_RB2x_R61D2.json")
    if not stats:
        return None
    name_of = {"L1": "L1", "L2": "L2", "L2A": "L2+A", "L2AB": "L2+A+B", "L2AB2": "L2+A+B（放宽）", "L2B": "L2+B", "L2B2": "L2+B（放宽）", "L2AC": "L2+A+C"}
    fac = {}
    for st, disp, kind, kz, src, pref in (("L2", "L2", "pond", "已建成塘（BNS）", "RB_02 模型（按已建成处理，用户 2026-09-27 同意）", "BNS"),
                                         ("L2A", "L2+A", "pond", "PO 塘", "RB_02 模型的 PO 塘（设计方未确认）", "PO")):
        m = _j(WORK / "c2d" / f"pilot_RB2x_{st}.json") or {}
        fac[disp] = []
        for p in m.get("ponds", []):
            if not str(p.get("unitid", "")).startswith(pref):
                continue
            lon, lat = _ll(tr, *p["xy"])
            fac[disp].append({"id": p["unitid"], "state": disp, "kind": kind, "kind_zh": kz, "source": src,
                              "volume_m3": round(p.get("vol") or 0), "lon": lon, "lat": lat})
    fac["L2+A+B"] = fac.get("L2+A", []) + _boxes(OUT / "three_lines" / "RB2x.L2AB.ponds.geojson", "L2+A+B", "本系统（在 PO 塘方案之上、严格用地）")
    fac["L2+A+B（放宽）"] = fac.get("L2+A", []) + _boxes(OUT / "three_lines" / "RB2x.L2AB2.ponds.geojson", "L2+A+B（放宽）", "本系统（在 PO 塘方案之上、可借用未盖楼地块）")
    fac["L2+B"] = _boxes(OUT / "three_lines" / "RB2x.L2B.R61D2.ponds.geojson", "L2+B", "本系统（从 L2 出发、严格用地）")
    fac["L2+B（放宽）"] = _boxes(OUT / "three_lines" / "RB2x.L2B.R61D2v.ponds.geojson", "L2+B（放宽）", "本系统（从 L2 出发、可借用未盖楼地块）")
    des = (_j(OUT / "RB2x_L2AC_design.json") or {}) if "L2AC" in stats else {}      # 统计里有这一态才列设施
    fac["L2+A+C"] = [] if not des else fac.get("L2+A", []) + [
        {"id": p["id"], "state": "L2+A+C", "kind": "stationary_pump", "kind_zh": "临时固定泵（地面管）", "source": "本系统（雨前放置的 12 寸固定泵 + 地面管）",
         "n_pumps": p["n_pumps"], "q_m3s": p["q_m3s"], "pipe_m": p["pipe_m"], "to": p.get("to_node") or "空地",
         "lon": p["lonlat"][0], "lat": p["lonlat"][1], "to_lon": (p.get("to_lonlat") or [None, None])[0], "to_lat": (p.get("to_lonlat") or [None, None])[1]}
        for p in des.get("pumps", [])]
    if not des:
        fac.pop("L2+A+C", None)
    present = [name_of[s] for s in name_of if s in stats]
    return {"label": "29.RB2", "aliases": ["29.rb2", "rb2", "rb_02", "rb02"], "area_km2_domain": 39.0,
            "scenario": "61 mm / 2 h", "model": "SWMM 5.2 + ANUGA 30 m 双向耦合，计算域 29.RB2 外扩 500 m（39 km²）",
            "chain": [s for s in ["L1", "L2", "L2+A", "L2+A+B"] if s in present],
            "refs": {k: v for k, v in {"L2+A+B（放宽）": "L2+A", "L2+B": "L2", "L2+B（放宽）": "L2", "L2+A+C": "L2+A"}.items() if k in present},
            "states": {"L1": "既有管网（RB_02 模型）", "L2": "加 5 座 BNS 塘（按已建成处理）",
                       "L2+A": "PO 塘方案（来源待确认）：RB_02 模型里的 PO 塘与配套泵，24 座里只有 PO-4、PO-18 在 29.RB2 内",
                       "L2+A+B": "PO 塘方案视为既成事实，其上再加本系统箱涵（严格用地）", "L2+A+B（放宽）": "同上，可借用未盖楼的已分配地块",
                       "L2+B": "对照：不做 PO 塘、单做本系统箱涵（严格用地）", "L2+B（放宽）": "对照：单做箱涵，可借用未盖楼地块",
                       **({"L2+A+C": "PO 塘方案之上，不做箱涵，改在深积水低点雨前放 12 寸固定泵、用地面管排到出水口（L2 临时转输）"} if "L2AC" in stats else {})},
            "metrics": _pf_metrics(stats, name_of), "facilities": fac,
            "overlap": "叠加后 PO 塘的地表进水不变（2.6 万 m³），这里没有 Mussafah 那种被上游箱涵抢水的情况。",
            "notes": ["PO 塘的设计方没有文件写明：Parsons 的 4 份报告（ICAD / Mussafah / RB3 / Shaliela）里没有 RB2，编号与泵型只是像 Parsons",
                      "这一片的溢流分散在 8,000 多口井上、一半出在雨水口：集中调蓄（塘 / 箱涵）能压住深积水（≥0.8 m 面积最多少 68%），压不住大面积浅积水（≥0.10 m 只少 14%）；浅积水要靠 L2 的临时转输 / 挡导和长期的雨水口与支管扩容",
                      "严格用地下 29.RB2 的空地只放得下理想库容的 24%（8 座）；放宽后 50%（16 座）",
                      "一维包含整个 RB_02 管网，二维只覆盖 29.RB2 外扩 500 m：域外井的溢流不进二维",
                      "最大水深五态都是 1.08 m，是一处与方案无关的固定低洼"],
            # 英文（系统英文界面读，2026-10-28），与上面逐条对应
            "states_en": {"L1": "Existing network (RB_02 model)", "L2": "+ 5 BNS ponds (treated as built)",
                          "L2+A": "PO pond scheme (source unconfirmed): the PO ponds and pumps in the RB_02 model; of the 24, only PO-4 and PO-18 are inside 29.RB2",
                          "L2+A+B": "PO pond scheme taken as given, plus this system's box culverts on top (strict land use)",
                          "L2+A+B（放宽）": "As above, may borrow allocated plots not yet built on",
                          "L2+B": "Comparison: no PO ponds, this system's box culverts only (strict land use)",
                          "L2+B（放宽）": "Comparison: box culverts only, may borrow plots not yet built on",
                          **({"L2+A+C": "On top of the PO pond scheme, no box culverts: 12-inch fixed pumps placed before the storm at deep low points, discharging through surface pipes to outfalls (L2 temporary transfer)"} if "L2AC" in stats else {})},
            "notes_en": ["Who designed the PO ponds is not documented: none of Parsons' 4 reports (ICAD / Mussafah / RB3 / Shaliela) covers RB2; the numbering and pump types only resemble Parsons'",
                         "Overflow here is spread over more than 8,000 manholes, half of it at gullies: central storage (ponds / box culverts) can hold down deep water (area ≥0.8 m reduced by up to 68%) but not widespread shallow ponding (≥0.10 m only 14% less); shallow ponding needs L2 temporary transfer / diversion and, long term, more gully and branch-pipe capacity",
                         "With strict land use, open land in 29.RB2 fits only 24% of the ideal storage (8 sites); relaxed, 50% (16 sites)",
                         "1D covers the whole RB_02 network, 2D only 29.RB2 plus 500 m: overflow from manholes outside the 2D domain does not enter 2D",
                         "Maximum depth is 1.08 m in all five states: a fixed low spot unrelated to the schemes"],
            "console": {"tab": "line2p", "l2p": "rb2"}, "report": "https://claude.ai/artifact/T9wuV4541wbdepwmhFdTiX"}


def madinat(tr):
    stats = _j(OUT / "pilot_Pilot_MAR2_R61D2.json")
    if not stats:
        return None
    name_of = {"L1": "L1", "L2": "L2"}
    ponds = []
    for p in (stats.get("L2") or {}).get("ponds", []):
        ponds.append({"id": p["unitid"], "state": "L2", "kind": "pond", "kind_zh": "已建成塘", "source": "客户台账 stormwater_drainage.pond",
                      "volume_m3": p.get("vol_design_m3"), "stored_m3": p.get("vol_end_m3"), "fill_pct": p.get("fill_pct")})
    pts = _j(DATA / "ponds_l2_pts.geojson") or {}
    ll = {f["properties"].get("unitid"): f["geometry"]["coordinates"] for f in pts.get("features", [])}
    for p in ponds:
        if p["id"] in ll:
            p["lon"], p["lat"] = round(ll[p["id"]][0], 6), round(ll[p["id"]][1], 6)
    return {"label": "Madinat Al Riyad", "aliases": ["madinat al riyad", "madinat", "riyad", "mar", "利雅得"],
            "scenario": "61 mm / 2 h", "model": "SWMM 5.2 + ANUGA 30 m 双向耦合，按水文边界的计算域 385 km²",
            "chain": ["L1", "L2"], "refs": {}, "states": {"L1": "既有管网（从 GDB 重建）", "L2": "加 20 座已建成塘（194 万 m³）"},
            "metrics": _pf_metrics(stats, name_of), "facilities": {"L2": ponds},
            "overlap": None,
            "notes": ["塘接为无出口管网的末端是按位置与标高推断的，台账没有这么写",
                      "塘的水六成来自地表漫流；PND_39、PND_37 等大塘远没用满，是水到不了，不是塘不够大"],
            "console": {"tab": "line2p", "l2p": "pilot"}, "report": "https://claude.ai/artifact/NjMBR3ueF7RBUGYQJGm9bY"}


def _city2d(done):
    base = {"status": "done" if done else "running", "domain_km2": 1233.6, "res_m": 30}
    st, z = _j(DATA / "flood2d_city2d" / "stats.json"), _j(OUT / "city2d_zones_R61D2.json")
    if not (done and st and z):
        return {**base, "note": "全市一二维（30 m，632 万三角单元）完成后才有推演播放、最大积水面积 / 水深"}
    k = ("max_area_ge_0p10_ha", "max_area_ge_0p30_ha", "max_depth_m", "flood_1d_to_2d_m3", "final_vol_m3", "into_ponds_surface_m3")
    ponds = st["L2"].get("ponds", [])
    return {**base, "L1": {x: st["L1"].get(x) for x in k} | {"area_ge_0p80_ha": z["all"]["L1_ge_0p80_ha"]},
            "L2": {x: st["L2"].get(x) for x in k} | {"area_ge_0p80_ha": z["all"]["L2_ge_0p80_ha"]},
            "shallower_ha": z["all"]["shallower_ha"], "shallower_near_ponds_ha": z["shallower_within_1km_of_ponds_ha"], "deeper_ha": z["all"]["deeper_ha"],
            "ponds_stored_m3": round(sum(p.get("vol_end_m3", 0) for p in ponds)), "ponds_capacity_m3": round(sum(p.get("vol_design_m3", 0) for p in ponds)),
            "near_ponds": [{x: r[x] for x in ("unitid", "L1_ge_0p30_ha", "L2_ge_0p30_ha", "L1_max_m", "L2_max_m", "shallower_ha")} for r in z["ponds"][:8]],
            "console": {"tab": "line2p", "l2p": "city2d"},
            "note": "全市一二维 30 m、6 h：从全市看塘的作用很小（≥0.30 m 面积 −2.8%），在塘周边 1 km 内 ≥0.30 m 面积普遍少四到七成；"
                    "6 h 末积水还在峰值的 94%，退水要把二维算长才有数"}


def city():
    g = _j(OUT / "city_gap_v2.json")
    if not g:
        return None
    c = g["city"]; nodes = _j(DATA / "city_nodes.json") or {}
    tw = {s: _j(WORK / "c2d" / f"twoway_City2D_{s}.json") for s in ("L1", "L2")}
    done = all(tw.values()) and all((x or {}).get("end_h", 0) >= 6 for x in tw.values())
    return {"basis": "一维（SWMM，全市 8 片 × 2 态）", "scenario": "61 mm / 2 h",
            "L1_flood_m3": round(c["L1"]["flood_m3"]), "L2_flood_m3": round(c["L1"]["flood_m3"] - c["diff_m3"]),
            "diff_m3": round(c["diff_m3"]), "diff_pct": round(c["diff_pct"], 2),
            "pond_networks": {k: (round(v, 1) if isinstance(v, float) else v) for k, v in c["pond_networks"].items()},
            "inflow_m3": round(c["L1"]["inflow_m3"]), "storage_L1_m3": round(c["L1"]["storage_capacity_m3"]), "storage_L2_m3": round(c["L2"]["storage_capacity_m3"]),
            "emptying_h": [c["L1"]["emptying_majority_h"], c["L2"]["emptying_majority_h"]],
            "outside_client_catch_pct": round(g.get("outside_client_catch_L1_m3", 0) / c["L1"]["flood_m3"] * 100),
            "top_catchments": [{k: r[k] for k in ("label", "L1_m3", "diff_m3")} for r in g.get("by_catchment", []) if r.get("L1_m3", 0) > 0][:8],
            "nodes": {k: nodes.get(k) for k in ("n_nodes", "n_better", "n_worse", "n_pipes", "n_relieved")},
            "coupled_2d": _city2d(done),
            "notes": ["一维口径：塘只接得到管道送来的水，差值是下限",
                      "全市差值只有 2.6% 的原因是 22 座塘只接在 15 块管网上（主要在 Madinat Al Riyad），别处 L2 = L1；"
                      "「54% 的 L1 溢流在客户汇水区之外」说的是客户汇水区覆盖不全，和差值大小无关，不要把两者连起来解释",
                      "差值只在接塘的 15 块管网上算；其余管网的 L1 / L2 差是分片数值噪声（0.04%），记 0",
                      "塘接为无出口管网的末端是推断的"],
            "console": {"tab": "line2p", "l2p": "city", "m": "diff"}, "report": "https://claude.ai/artifact/NjMBR3ueF7RBUGYQJGm9bY"}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    tr = _tr()
    regions = {k: v for k, v in (("Mussafah_00", mussafah(tr)), ("29.RB2", rb2(tr)), ("Madinat Al Riyad", madinat(tr))) if v}
    cat = {"version": 1, "built": __import__("time").strftime("%Y-%m-%d %H:%M"), "regions": regions, "city": city()}
    f = DATA / "l2plus_catalog.json"
    f.write_text(json.dumps(cat, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for k, v in regions.items():
        print(f"  {k}: 态 {len(v['states'])}、指标 {len(v['metrics'])} 项、设施 " + "、".join(f"{s} {len(x)}" for s, x in v["facilities"].items()))
    print(f"  全市：{'有' if cat['city'] else '无'}　→ {f}（{f.stat().st_size / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
