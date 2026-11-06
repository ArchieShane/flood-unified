"""RD-A 高后果积水点的选择过程 → 系统页面数据（2026-10-28 用户：客户会问为什么是这些点、其余点为什么优先级低，要能在页面上表达）。

规则（与 2026-10-27 选出 HC_A61 / HC_96 时相同，本脚本复算并逐个核对现有目标文件）：
  ① 候选 = 该档基准方案（AECOM 档 OPTA61PS、96 mm 档 OPTCS2）在本档每场设计雨下的二维残余积水点
       （≥0.15 m、≥100 m²、≥0.5 h，扣本态塘与评估剔除区；同 L3 承接口径 tools/rda_l3.assess）
  ② 后果分级：关键 / 高 / 中 / 无暴露（碰到医院急救隧道地铁、变电站、路面、建筑；rda_l3.consequence）
  ③ 入选 = 后果等级为「关键」或「高」，且在处置时限内不能自然退干（要靠 L3，或 L3 也兜不住）
  ④ 各场雨入选点按 100 m 合并成一个目标（同一处洼地在不同雨型下编号不同），编号 HC-<档>-序号，重要度：关键 → Very High、高 → High
落选的点逐个给出原因：中 / 无暴露（后果低）、能在时限内自然退干。
产物：console/data/rda_hc.json（页面「高后果积水点」节读取）；打印与现有目标文件的核对结果。只读二维结果，不重算。
用法：python -X utf8 tools/rda_hc_select.py（需 unset PROJ_LIB、设 SWBRAIN_SWMM_WORK）
"""
from __future__ import annotations
import json
import math
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import rda_l3 as L3  # noqa: E402
from console_en import zh2en  # noqa: E402

TIERS = [  # 档键, 名称, 基准方案态, 基准方案名, 本档雨, 现有目标文件, 加了高后果点泵站后的方案态
    ("A61", "AECOM 口径 61 mm / 1 h", "OPTA61PS", "推荐方案 53 项", ["A61D1", "S61D1", "S61D24"], "HC_A61", "OPTA61PH2"),
    ("96", "96 mm 极端雨", "OPTCS2", "96 mm 方案", ["S96D1", "S96D2", "S96D24"], "HC_96", "OPTCS3X"),
]
LAB = {"A61D1": "60.7 mm / 1 h（AECOM）", "S61D1": "61 mm / 1 h（Mussafah）", "S61D24": "61 mm / 24 h", "S96D1": "96 mm / 1 h", "S96D2": "96 mm / 2 h", "S96D24": "96 mm / 24 h"}
LAB_EN = {"A61D1": "60.7 mm / 1 h (AECOM)", "S61D1": "61 mm / 1 h (Mussafah)", "S61D24": "61 mm / 24 h", "S96D1": "96 mm / 1 h", "S96D2": "96 mm / 2 h", "S96D24": "96 mm / 24 h"}
CRIT = {"关键": "Very High", "高": "High"}
R_MERGE = 100.0
K = math.cos(math.radians(24.46)) * 111000


def dist(a, b):
    return math.hypot((a[0] - b[0]) * K, (a[1] - b[1]) * 111000)


def reason(x):
    """落选 / 入选原因（中文、英文）。"""
    hi = x["level"] in ("关键", "高")
    nat = str(x["verdict"]).startswith("自然退干")
    if x["level"] == "无暴露":
        return "out_noexpo", "无暴露：只在空地 / 未建地块 / 公园，只监测", "No exposure: only open land / vacant plots / parks; monitor only"
    if not hi:
        return "out_mid", "后果为「中」：浅水碰到路面或建筑，交 L3 或自然退干即可", "Medium consequence: shallow water on roads or buildings; L3 or natural drainage is enough"
    if nat:
        return "out_nat", "后果高，但能在处置时限内自然退干", "High consequence, but drains naturally within the deadline"
    if str(x["verdict"]).startswith("超出"):
        return "in_beyond", "入选：后果高，且超出 L3 单点处置能力", "Selected: high consequence and beyond L3 single-point capacity"
    return "in_l3", "入选：后果高，且不能自然退干，要靠 L3 抽排", "Selected: high consequence and does not drain naturally; needs L3 pumping"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    C = L3.context()
    out = {"generated": time.strftime("%Y-%m-%d %H:%M"), "merge_m": R_MERGE, "tiers": []}
    for key, name, st, stn, storms, tf, final in TIERS:
        pts, funnel = [], []
        for s in storms:
            r = L3.assess(s, st, C)
            if r is None:
                print("缺二维结果", s, st); continue
            _, rows, _ = r
            f = {"storm": s, "label": LAB[s], "label_en": LAB_EN[s], "n": len(rows)}
            for x in rows:
                code, zh, en = reason(x)
                f[code] = f.get(code, 0) + 1
                pts.append({"storm": s, "id": x["id"], "lon": x["lon"], "lat": x["lat"], "level": x["level"], "level_why": x["level_why"],
                            "level_why_en": x.get("level_why_en") or (zh2en(x["level_why"]) or ""), "max_depth_m": x["max_depth_m"], "vol_peak_m3": x["vol_peak_m3"],
                            "area30_m2": x.get("area30_m2"), "hotspots_200m": x.get("hotspots_200m") or "", "verdict": x["verdict"],
                            "t_dry_nat_h": x.get("t_dry_nat_h"), "deadline_h": x.get("deadline_h"), "code": code, "why": zh, "why_en": en})
            funnel.append(f)
        sel = [p for p in pts if p["code"].startswith("in_")]
        sel.sort(key=lambda p: (-{"关键": 2, "高": 1}[p["level"]], -p["vol_peak_m3"]))
        groups = []
        for p in sel:                                              # 100 m 内合并（同一处洼地在不同雨型下）
            g = next((g for g in groups if any(dist((p["lon"], p["lat"]), (q["lon"], q["lat"])) <= R_MERGE for q in g)), None)
            (g.append(p) if g is not None else groups.append([p]))
        # 与现有目标文件逐个核对（按位置）
        T = json.loads((ROOT / f"out/l2opt/targets/{tf}.geojson").read_text(encoding="utf-8"))["features"]
        targets = []
        for t in T:
            xy = t["geometry"]["coordinates"][:2]
            g = min(groups, key=lambda g: min(dist(xy, (q["lon"], q["lat"])) for q in g)) if groups else None
            d = min(dist(xy, (q["lon"], q["lat"])) for q in g) if g else None
            ok = d is not None and d <= R_MERGE
            top = max(g, key=lambda q: q["vol_peak_m3"]) if ok else {}
            targets.append({"hc_id": t["properties"]["hotspot_id"], "lon": xy[0], "lat": xy[1], "criticality": t["properties"]["criticality"],
                            "why": top.get("level_why") or t["properties"]["location"], "why_en": top.get("level_why_en", ""), "level": top.get("level"),
                            "max_depth_m": max((q["max_depth_m"] for q in g), default=None) if ok else None,
                            "vol_peak_m3": max((q["vol_peak_m3"] for q in g), default=None) if ok else None, "members": [{"storm": q["storm"], "id": q["id"], "verdict_code": q["code"]} for q in (g or [])] if ok else [],
                            "match_m": round(d) if d is not None else None})
            for q in (g or []):
                if d is not None and d <= R_MERGE:
                    q["hc_id"] = t["properties"]["hotspot_id"]
        # 每个目标补了什么泵站 / 为什么没补（l2s_sumps_v3 的 stations / unresolved / excluded）
        SF = {"OPTA61PH2": ROOT / "out/l2opt/RDA_A61P/sumps_OPTA61PH2.json", "OPTCS3X": ROOT / "out/l2opt/RDA_96C/sumps_OPTCS3X.json"}[final]
        SM = json.loads(SF.read_text(encoding="utf-8"))
        from pyproj import Transformer
        TT = Transformer.from_crs(32640, 4326, always_xy=True)
        for t_ in targets:
            st_ = [s_ for s_ in SM["stations"] if s_["hotspot"] == t_["hc_id"]]
            t_["pumps"] = [{"id": f"HC{s_['id']}", "n_intakes": len(s_["intakes"]), "q_lps": round(s_["q_m3s"] * 1000), "cost_usd": round(s_["cost_usd"])} for s_ in st_]
            if not st_:
                ex = [e for e in SM.get("excluded") or [] if dist(TT.transform(e["x"], e["y"]), (t_["lon"], t_["lat"])) <= 50]
                un = [u for u in SM.get("unresolved") or [] if u["hotspot"] == t_["hc_id"]]
                if ex:
                    t_["no_pump"] = "private_pit"; t_["no_pump_why"] = f"{ex[0]['class']}：{ex[0]['why']}，不在公共用地，不配公共泵站"
                    t_["no_pump_why_en"] = "Deep pit inside a private built plot (basement ramp / foundation pit); not public land, so no public pump station"
                elif un:
                    t_["no_pump"] = "no_site"; t_["no_pump_why"] = un[0]["why"]
                    t_["no_pump_why_en"] = "No suitable pump-station site within 300 m of the intake (excavatable, set back from buildings, next to a road, power within 1 km, not flooded)"
                else:
                    t_["no_pump"] = "other"; t_["no_pump_why"] = "未配泵站（原因待查）"; t_["no_pump_why_en"] = "No pump station (reason to be checked)"
        matched = {id(q) for t in targets for q in sel if q.get("hc_id") == t["hc_id"]}
        extra = [[q["storm"], q["id"]] for q in sel if id(q) not in matched]
        print(f"{name}: 候选 {len(pts)} 点次，入选 {len(sel)} 点次 → {len(groups)} 处；现有目标 {len(T)} 处，"
              f"全部对上 {all(t['members'] for t in targets)}，未归入现有目标的入选点 {extra}")
        out["tiers"].append({"key": key, "name": name, "name_en": name.replace("口径", "standard").replace("极端雨", "extreme storm"), "base_state": st, "base_name": stn,
                             "final_state": final, "storms": storms, "funnel": funnel, "points": pts, "targets": targets, "n_groups": len(groups), "unmatched": extra})
    p = ROOT / "console/data/rda_hc.json"
    p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("→", p)


if __name__ == "__main__":
    main()
