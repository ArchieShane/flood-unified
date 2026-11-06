# -*- coding: utf-8 -*-
"""RD-A 61 mm / 1 h 推荐方案（OPTA61PS = 当前方案按 AECOM 雨型剪枝 + 同一套 7 座地面泵站）交付（2026-10-27）。

方案来源：tools/l2opt_prune.py 以 AECOM「10 年 + 20 %」1 h 设计雨（A61D1）为控制雨，从当前方案的管网侧 53 项（OPTC）剪到 46 项（删 7、换 6），
再加当前方案的 7 座地面泵站（OPTCS2 的 17 个取水口）→ OPTA61PS；二维验收 A61D1、S61D1、S61D24（scratchpad/a61_round2.sh）。
产物 out/交付/RDA_61mm1h_推荐方案_20261027/：
  RD-A_61mm1h_推荐方案_设施台账.xlsx（方案对比、设施台账、地面取水口、造价汇总、与当前方案的差异）、推荐方案_设施.geojson
  <雨>/<状态>/：一二维（npz、inp、耦合报告、设置）、水深栅格、积水点（0.15 / 0.30 m 两档）；雨 = A61D1（AECOM 雨型）、S61D1（Mussafah 雨型）、S61D24
只读计算结果。用法：python -X utf8 tools/rda_a61_deliver.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import export_delivery_rda as X  # noqa: E402
from rda_package import KIND, BASIS  # noqa: E402
from swbrain import ponding  # noqa: E402
from pyproj import Transformer  # noqa: E402

OUT = ROOT / "out/交付/RDA_61mm1h_推荐方案_20261027"
A61, A61P, C96 = ROOT / "out/l2opt/RDA_A61", ROOT / "out/l2opt/RDA_A61P", ROOT / "out/l2opt/RDA_96C"
T = Transformer.from_crs(32640, 4326, always_xy=True).transform
STORMS = {"A61D1": "60.7 mm / 1 h（AECOM 雨型）", "S61D1": "61 mm / 1 h（Mussafah 雨型）", "S61D24": "61 mm / 24 h"}
STATES = {"L1": "L1_现状", "L2": "L2_雨前预置泵", "L2P": "L2+planned_客户计划", "OPTA61PS": "L2+suggested_61mm1h推荐方案"}


def ev(storm):
    E = json.loads((A61 / f"eval2d_{storm}_ex.json").read_text(encoding="utf-8"))["states"]
    f = C96 / f"eval2d_{storm}_ex.json"
    if f.exists():
        E.setdefault("OPTCS2", json.loads(f.read_text(encoding="utf-8"))["states"].get("OPTCS2"))
    return E


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    CAND = {c["id"]: c for c in json.loads((A61P / "candidates.json").read_text(encoding="utf-8"))["candidates"]}
    COMP = json.loads((C96 / "compliance.json").read_text(encoding="utf-8"))
    V = json.loads((A61P / "variant_A61D1_OPTA61P.json").read_text(encoding="utf-8"))
    SU = json.loads((C96 / "sumps_OPTCS2.json").read_text(encoding="utf-8"))
    PR = json.loads((A61P / "prune.json").read_text(encoding="utf-8"))
    # 设施台账（同 rda_package 表 4 的列）
    led, feats = [], []
    for m in sorted(V["measures"], key=lambda m: (m["hotspot"], m["kind"], m["id"])):
        c = CAND[m["id"]]; k = m["kind"]; items = COMP.get(m["id"], [])
        lo, la = T(c.get("px", c.get("ps_x")), c.get("py", c.get("ps_y")))
        olo, ola = T(*c["of_xy"]) if c.get("of_xy") else (None, None)
        led.append({"编号": m["id"], "类型": KIND[k], "服务热点": c["hotspot"], "经度": round(lo, 6), "纬度": round(la, 6),
                    "截流井": "、".join(c.get("wells") or [c.get("well")]), "截流井溢流量 m³": c.get("overflow_m3"),
                    "池容 m³": c.get("pond_vol_m3"), "有效水深 m": c.get("pond_depth_m"), "池底高程 m": c.get("z_floor"), "开挖深度 m": c.get("excavation_m"),
                    "泵台数": c.get("n_pumps"), "设计流量 m³/s": c.get("q_m3s"), "单泵流量 m³/s": c.get("pump_each_m3s"), "泵站场地 m²": c.get("ps_site_m2"),
                    "管长 m": c.get("pipe_m"), "管径 m": c.get("main_d_m") or c.get("pipe_d_m"), "并联根数": c.get("n_mains"), "峰值流速 m/s": c.get("v_ms"),
                    "排口岸点": c.get("outfall_site"), "排口经度": round(olo, 6) if olo else None, "排口纬度": round(ola, 6) if ola else None,
                    "用地（Makani 占比）": "、".join(f"{kk} {vv:.0%}" for kk, vv in (c.get("land") or {}).items() if vv) or None,
                    "占位造价 USD": round(m["cost_usd"]), "造价依据": BASIS[k], "可建性": c.get("status"),
                    "需审批 / 许可": "；".join(i["zh"] for i in items if i["result"] == "review"),
                    "缺数据判不了": "；".join(i["zh"] for i in items if i["result"] == "unknown"), "合规检查项数": len(items),
                    "与当前方案相比": "换入" if any(s["to"] == m["id"] for s in PR["swapped"]) else "保留"})
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lo, 6), round(la, 6)]}, "properties": {"编号": m["id"], "类型": KIND[k], "服务热点": c["hotspot"], "造价 USD": round(m["cost_usd"])}})
    for x in SU["stations"]:
        lo, la = T(x["ps_x"], x["ps_y"]); olo, ola = T(*x["of_xy"]); items = SU["compliance"].get(x["id"], [])
        led.append({"编号": x["id"], "类型": KIND["surface_ps"], "服务热点": x["hotspot"], "经度": round(lo, 6), "纬度": round(la, 6),
                    "截流井": f"地面取水口 {len(x['intakes'])} 个", "泵台数": x["n_pumps"], "设计流量 m³/s": x["q_m3s"], "单泵流量 m³/s": x["pump_each_m3s"],
                    "泵站场地 m²": x["ps_site_m2"], "管长 m": x["pipe_m"], "管径 m": x["main_d_m"], "并联根数": x["n_mains"], "峰值流速 m/s": x["v_ms"],
                    "排口岸点": x["outfall_site"], "排口经度": round(olo, 6), "排口纬度": round(ola, 6), "占位造价 USD": x["cost_usd"], "造价依据": BASIS["surface_ps"],
                    "可建性": x["status"], "需审批 / 许可": "；".join(i["zh"] for i in items if i["result"] == "review"),
                    "缺数据判不了": "；".join(i["zh"] for i in items if i["result"] == "unknown"), "合规检查项数": len(items), "与当前方案相比": "保留"})
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lo, 6), round(la, 6)]}, "properties": {"编号": x["id"], "类型": KIND["surface_ps"], "服务热点": x["hotspot"], "造价 USD": x["cost_usd"]}})
    intake = [{"取水口": f"{x['id']}_{j}", "所属地面泵站": x["id"], "服务热点": x["hotspot"], "类别": it["class"], "经度": round(T(it["x"], it["y"])[0], 6), "纬度": round(T(it["x"], it["y"])[1], 6),
               "坑底高程 m": it["z"], "取水流量 m³/s": it["q_m3s"]} for x in SU["stations"] for j, it in enumerate(x["intakes"])]
    by = {}
    for r in led:
        b = by.setdefault(r["类型"], [0, 0.0]); b[0] += 1; b[1] += r["占位造价 USD"] or 0
    tot = sum(v[1] for v in by.values())
    cost = [{"设施类型": k, "数量": v[0], "占位造价 百万美元": round(v[1] / 1e6, 2), "占比 %": round(100 * v[1] / tot, 1)} for k, v in by.items()]
    cost.append({"设施类型": "合计", "数量": len(led), "占位造价 百万美元": round(tot / 1e6, 2), "占比 %": 100})
    diff = [{"变化": "删去", "编号": r["id"], "类型": KIND[r["kind"]], "服务热点": r["hotspot"], "造价 USD": r["cost_usd"]} for r in PR["removed"]]
    diff += [{"变化": "换成更省的", "编号": f"{s['from']} → {s['to']}", "类型": f"{KIND[s['from_kind']]} → {KIND[s['to_kind']]}", "服务热点": s["hotspot"],
              "造价 USD": f"{s['from_cost']:,} → {s['to_cost']:,}"} for s in PR["swapped"]]
    comp = []
    for storm, lab in STORMS.items():
        E = ev(storm)
        for st, zh in (("L2P", "客户计划"), ("OPTA61S2", "61 mm 专门方案（调泵后，约 4,980 万美元）"), ("OPTA61PS", "推荐方案：当前方案瘦身版"), ("OPTCS2", "当前方案（按 96 mm 优化）")):
            e = E.get(st)
            if e:
                comp.append({"降雨": lab, "方案": zh, "客户热点四项全过（/11）": sum(all(v["pass"].values()) for v in e["hotspots"].values()),
                             "全域 ≥0.30 m 积水面积 ha": e["domain"]["ge030_ha"], "未过的热点": "、".join(h for h, v in e["hotspots"].items() if not all(v["pass"].values()))})
    import openpyxl
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    for name, rows in (("方案对比", comp), ("设施台账", led), ("地面取水口", intake), ("造价汇总", cost), ("与当前方案的差异", diff)):
        ws = wb.create_sheet(name); ws.append(list(rows[0])); [ws.append([r.get(k) for k in rows[0]]) for r in rows]
        for c in ws.columns:
            ws.column_dimensions[c[0].column_letter].width = max(10, min(40, max(len(str(x.value or "")) for x in c) + 2))
    wb.save(OUT / "RD-A_61mm1h_推荐方案_设施台账.xlsx")
    (OUT / "推荐方案_设施.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False), encoding="utf-8")
    print(f"台账 {len(led)} 项，合计 {tot / 1e6:.2f} M USD", cost)
    # 各雨各状态的一二维结果 + 积水点
    for storm in STORMS:
        run = f"RDA_{storm}"; d0 = OUT / storm
        miss = [st for st in STATES if not (X.C2D / "tif" / f"{run}_{st}_depth_max.tif").exists()]
        if miss:
            subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools/npz_to_tif.py"), run, *miss], check=True, stdout=subprocess.DEVNULL)
        dirs = {st: d0 / n for st, n in STATES.items()}
        ponding.export_delivery(run, dirs, d0, pond_scope="state")
        for st, d in dirs.items():
            X.model_files(run, st, d)
        print("→", d0)


if __name__ == "__main__":
    main()
