"""RD-A 61 mm / 1 h 的 L3 应急调度交付包（2026-10-28 用户要求：L3 涉及的设备、调度过程、每次出车的行驶路线都要完整输出）。

只读导出，不重算：
  console/data/flood2d_rda_<storm>/l3.json        各态残余积水点、后果分级、两种调度口径的派单（rda_threelines.py）
  console/data/flood2d_rda_<storm>/l3_hca61.json  最终方案 OPTA61PH2（推荐方案 + 3 座高后果点地面泵站）的派单
  console/data/flood2d_rda_<storm>/routes.json    ArcGIS Online 卡车路线（折线 + 逐段导航，rda_routes.py）
  exchange/fleet.plan_a.json                      预案 A 驻地与设备型号
输出：out/交付/RDA_61mm1h_L3应急调度_20261028/
用法：python tools/export_rda_l3.py [--out <dir>]
"""
from __future__ import annotations
import argparse
import csv
import json
import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "out/交付/RDA_61mm1h_L3应急调度_20261028"
STORMS = {"A61D1": "AECOM雨型_60.7mm1h", "S61D1": "Mussafah雨型_61mm1h"}
STATES = {"L1": "L1_现状", "L2": "L2_雨前预置泵", "L2P": "L2+planned_客户计划", "OPTA61PS": "L2+suggested_推荐方案53项",
          "OPTA61PH2": "L2+suggested_最终方案56项（含高后果点泵站）"}
POOL_ZH = {"island": "Island 片区驻地 A13（现状口径）", "adm": "ADM 五个驻地集中调度（上限口径）"}
KIND = {"tanker-17m3": "罐车", "diesel-12in": "移动泵", "diesel-8in": "移动泵", "diesel-6in-x3": "移动泵（3 台一组）"}
MODEL_ZH = {"tanker-17m3": "吸污罐车 17 m³", "diesel-12in": "12 寸柴油泵", "diesel-8in": "8 寸柴油泵", "diesel-6in-x3": "6 寸柴油泵 ×3 台一组"}


def hm(h):
    if h is None:
        return ""
    m = round(float(h) * 60)
    return f"T+{m // 60:02d}:{m % 60:02d}"


def key(a, b):
    return f"{round(a[0], 5)},{round(a[1], 5)}|{round(b[0], 5)},{round(b[1], 5)}"


def wcsv(path, rows, cols=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = cols or (list(rows[0]) if rows else ["（无）"])
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def wgeo(path, feats):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:4326"}}, "features": feats},
                               ensure_ascii=False), encoding="utf-8")


def load_fleet():
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    F = json.loads((ROOT / cfg["exchange"]["dir"] / "fleet.plan_a.json").read_text(encoding="utf-8"))
    tk = cfg["dispatch"].get("tankers", {})
    models = {m["model"]: m for m in F["models"]}
    models["tanker-17m3"] = {"model": "tanker-17m3", "capacity_m3": float(tk.get("capacity_m3", 17)), "duty_aed_h": float(tk.get("duty_aed_h", 250)),
                             "note": "客户预案 A 罐车，只给数量与 250 AED/h；容量按 4,500 gal 罐车估"}
    depots = {d["depot_id"]: d for d in F["depots"]}
    return F, models, depots


def export_state(sd, storm, st, S, R, models, depots, summary):
    pts = {p["id"]: p for p in S["points"]}
    rows_pt, feats_pt = [], []
    for p in S["points"]:
        r = {"积水点编号": p["id"], "经度": p["lon"], "纬度": p["lat"], "后果等级": p.get("level", ""), "定级依据": p.get("level_why", ""),
             "200 m 内客户热点": p.get("hotspots_200m", ""), "最大水深 m": p.get("max_depth_m"), "峰值积水量 m³": p.get("vol_peak_m3"),
             "峰值时刻": hm(p.get("t_peak_h")), "处置时限": hm(p.get("deadline_h")), "自然退干时刻": hm(p.get("t_dry_nat_h")),
             "L3 处置方式": p.get("l3_means", ""), "预估设备数": p.get("l3_units"), "承接结论": p.get("verdict", ""), "关键点": "是" if p.get("key_point") else "",
             "派车终点平移 m": p.get("road_snap_m")}
        for pool, D in S["dispatch"].items():
            x = D["points"].get(p["id"], {})
            r[f"调度结果（{pool}）"] = x.get("result", "")
            r[f"出动设备（{pool}）"] = "；".join(f"{MODEL_ZH.get(m, m)} ×{int(n)}" for m, n in (x.get("models") or {}).items())
            r[f"到场（{pool}）"] = hm(x.get("arrive_h")); r[f"抽排完成（{pool}）"] = hm(x.get("done_h"))
        rows_pt.append(r)
        feats_pt.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]}, "properties": r})
    wcsv(sd / "L3_积水点处置清单.csv", rows_pt); wgeo(sd / "L3_积水点处置清单.geojson", feats_pt)

    rows_u, rows_ev, feats_rt, rows_step, rows_util = [], [], [], [], []
    miss = 0
    for pool, D in S["dispatch"].items():
        home = {}                                                           # 设备所属驻地：该设备第一次出动的出发驻地
        for ln in sorted(D["lines"], key=lambda x: x.get("arrive_h") or 0):
            if not ln["from"].startswith("SIM"):
                home.setdefault(ln["unit"], ln["from"])
        for ln in D["lines"]:
            rt = (R or {}).get(key(ln["a"], ln["b"]))
            m = models.get(ln["model"], {})
            mins = rt.get("minutes") if rt else None
            dep = (ln["arrive_h"] - mins / 60) if (rt and ln.get("arrive_h") is not None) else None
            src = ln["from"]; tgt = pts.get(ln["fp"], {}); hop = src.startswith("SIM")   # 转场：上一个积水点抽完后直接去下一个点
            dep_id = home.get(ln["unit"], "") if hop else src
            u = {"调度口径": POOL_ZH[pool], "设备编号": ln["unit"], "设备类别": KIND.get(ln["model"], ln["model"]), "型号": MODEL_ZH.get(ln["model"], ln["model"]),
                 "排量 m³/h": m.get("rate_m3h", ""), "罐容 m³": m.get("capacity_m3", ""), "班组人数": m.get("crew", ""), "台时单价 AED/h": m.get("duty_aed_h", ""),
                 "出发方式": "转场（上一个积水点抽完后直接前往）" if hop else "从驻地出发", "出发地": src,
                 "所属驻地": dep_id, "驻地名称": (depots.get(dep_id) or {}).get("name", ""), "目标积水点": ln["fp"], "积水点后果等级": tgt.get("level", ""),
                 "出发时刻": hm(dep), "到场时刻": hm(ln.get("arrive_h")), "抽排完成时刻": hm(ln.get("done_h")),
                 "到场 h": ln.get("arrive_h"), "完成 h": ln.get("done_h"), "在场时长 h": round(ln["done_h"] - ln["arrive_h"], 2) if ln.get("done_h") is not None and ln.get("arrive_h") is not None else "",
                 "路线行程 min": mins if mins is not None else "", "路线里程 km": rt.get("km", "") if rt else "", "路线来源": "ArcGIS Online 卡车路线" if rt else "未取到在线路线（直线示意）",
                 "跨片区": "是" if ln.get("cross") else "", "处置结果": (D["points"].get(ln["fp"]) or {}).get("result", "")}
            rows_u.append(u)
            for t, ev in ((dep, "转场出发" if hop else "从驻地出发"), (ln.get("arrive_h"), "到场、开始架设抽排"), (ln.get("done_h"), "抽排完成、撤离")):
                if t is not None:
                    rows_ev.append({"调度口径": POOL_ZH[pool], "时刻": hm(t), "h": round(t, 3), "设备编号": ln["unit"], "型号": u["型号"], "事件": ev, "出发地": src, "所属驻地": dep_id, "积水点": ln["fp"]})
            if rt and rt.get("line"):
                coords = [[c[1], c[0]] for c in rt["line"]]          # routes.json 存 [lat, lon]
            else:
                coords = [ln["a"], ln["b"]]; miss += 1
            feats_rt.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                             "properties": {k: u[k] for k in ("调度口径", "设备编号", "型号", "出发方式", "出发地", "所属驻地", "目标积水点", "出发时刻", "到场时刻", "路线行程 min", "路线里程 km", "路线来源")}})
            if rt:
                en = rt.get("steps_en") or []
                for i, s in enumerate(rt.get("steps") or [], 1):
                    rows_step.append({"调度口径": POOL_ZH[pool], "设备编号": ln["unit"], "出发地": src, "目标积水点": ln["fp"], "序号": i, "导航": s,
                                      "Directions": en[i - 1] if i - 1 < len(en) else ""})
        for dep_id, x in (D.get("util") or {}).items():
            if x.get("units_used") or x.get("tankers_used") or x.get("crews_used"):
                rows_util.append({"调度口径": POOL_ZH[pool], "驻地": dep_id, "驻地名称": (depots.get(dep_id) or {}).get("name", ""),
                                  "已用泵组": x.get("units_used"), "泵组总数": x.get("units_total"), "已用罐车": x.get("tankers_used"), "罐车总数": x.get("tankers_total"),
                                  "已用人员": x.get("crews_used"), "人员总数": x.get("crews_total")})
        summary.append({"降雨": STORMS[storm], "状态": STATES[st], "调度口径": POOL_ZH[pool], "残余积水点": D["n_points"], "可派点": D["n_dispatchable"],
                        **{k: D["counts"].get(k, 0) for k in ("自然退干", "只监测", "L3 按时退干", "L3 已派、超时", "L3 未派")},
                        "出动移动泵（组）": D["pumps"], "出动罐车": D["tankers"], "跨片区设备": D["cross_zone_units"], "指示性费用 AED": D["cost_aed"],
                        "首次派单": hm(D["t_decide_h"]), "未派原因": "；".join(f"{k} {v}" for k, v in (D.get("unserved_reasons") or {}).items())})
    rows_u.sort(key=lambda r: (r["调度口径"], r["到场 h"] if r["到场 h"] is not None else 99, r["设备编号"]))
    rows_ev.sort(key=lambda r: (r["调度口径"], r["h"], r["设备编号"]))
    wcsv(sd / "L3_出动设备明细.csv", rows_u); wcsv(sd / "L3_调度时间线.csv", rows_ev); wcsv(sd / "L3_导航步骤.csv", rows_step)
    wcsv(sd / "L3_驻地资源占用.csv", rows_util); wgeo(sd / "L3_行车路线.geojson", feats_rt)
    (sd / "L3_汇总.json").write_text(json.dumps({"storm": storm, "state": st, "summary": S.get("summary"),
                                                 "dispatch": {p: {k: v for k, v in D.items() if k not in ("points", "lines")} for p, D in S["dispatch"].items()}},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    return len(rows_u), miss


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=str(OUT)); a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    F, models, depots = load_fleet()
    wcsv(out / "公共/预案A_驻地.csv", [{"驻地": d["depot_id"], "名称": d["name"], "片区": d.get("area", ""), "经度": d["lon"], "纬度": d["lat"],
                                       "12 寸泵": d["plan_a"]["pump12"], "8 寸泵": d["plan_a"]["pump8"], "6 寸泵": d["plan_a"]["pump6"], "罐车": d["plan_a"]["tankers"],
                                       "人员": d["plan_a"]["manpower"], "负责热点数": d["plan_a"]["hotspots"]}
                                      for k, d in depots.items() if k in ("A11", "A12", "A13", "A14", "A15")])
    wcsv(out / "公共/设备型号参数.csv", [{"型号代码": k, "名称": MODEL_ZH.get(k, k), "类别": KIND.get(k, ""), "排量 m³/h": m.get("rate_m3h", ""), "罐容 m³": m.get("capacity_m3", ""),
                                         "扬程 m": m.get("head_m", ""), "软管 m": m.get("hose_m", ""), "架设 min": m.get("setup_min", ""), "班组人数": m.get("crew", ""),
                                         "重量 t": m.get("weight_t", ""), "运输车型": m.get("vehicle_class", ""), "台时单价 AED/h": m.get("duty_aed_h", ""), "说明": m.get("note", "")}
                                        for k, m in models.items()])
    summary, log = [], []
    for storm, sname in STORMS.items():
        d = ROOT / f"console/data/flood2d_rda_{storm.lower()}"
        L = json.loads((d / "l3.json").read_text(encoding="utf-8"))
        HC = json.loads((d / "l3_hca61.json").read_text(encoding="utf-8")) if (d / "l3_hca61.json").exists() else {"states": {}}
        RT = json.loads((d / "routes.json").read_text(encoding="utf-8"))["states"] if (d / "routes.json").exists() else {}
        allR = {}
        for v in RT.values():
            allR.update(v.get("routes") or {})
        if storm == "A61D1":
            (out / "公共/调度依据.json").write_text(json.dumps(L.get("basis"), ensure_ascii=False, indent=1), encoding="utf-8")
        for st in STATES:
            S = (L["states"].get(st) if st != "OPTA61PH2" else None) or HC["states"].get(st)
            if not S:
                log.append(f"{storm} {st}: 无 L3 结果"); continue
            n, miss = export_state(out / sname / STATES[st], storm, st, S, (RT.get(st) or {}).get("routes") or allR, models, depots, summary)
            log.append(f"{storm} {st}: 出动 {n} 台次，无在线路线 {miss}")
    wcsv(out / "L3_各状态调度汇总.csv", summary)
    try:
        import pandas as pd
        pd.DataFrame(summary).to_excel(out / "L3_各状态调度汇总.xlsx", index=False)
    except Exception as e:  # noqa: BLE001
        log.append(f"xlsx 未写：{e}")
    print("\n".join(log)); print("→", out)


if __name__ == "__main__":
    main()
