"""RD-A 治理前后验证：9 个算例汇总对比（水量平衡 + 指标 + AECOM 参照），写 out/exp_gov/verify/compare.md / compare.json。

  python tools/exp_gov/v_compare.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
OUT = ROOT / "out" / "exp_gov" / "verify"
STORMS = [("D61", "61.34 mm 设计暴雨"), ("E26", "72 mm（2026 年雨）"), ("E24", "82.2 mm（2024 年雨）")]
MODELS = [("A", "① AECOM 原样"), ("R", "② DMT 原样"), ("G", "③ 治理后"), ("N", "④ 只治理 DMT（对照）"), ("M", "⑤ 治理后管网 + AECOM 产流（对照）")]   # N 有结果才列


def continuity(rpt):
    t = open(rpt, encoding="utf-8", errors="ignore").read()
    m = re.search(r"Flow Routing Continuity(.*?)Continuity Error", t, re.S)
    out = {}
    if m:
        for ln in m.group(1).splitlines():
            p = re.match(r"\s*([A-Za-z ]+?)\s*\.{2,}\s*([-\d.]+)\s+([-\d.]+)", ln)
            if p:
                out[p.group(1).strip()] = float(p.group(3)) * 1000.0      # 10^6 ltr → m³
    return out


def exch(log):
    last = [ln for ln in open(log, encoding="utf-8", errors="ignore") if "↓1D→2D" in ln]
    if not last:
        return None, None
    a = re.search(r"↓1D→2D\s*([\d,]+)", last[-1]); b = re.search(r"↑2D→1D\s*([\d,]+)", last[-1])
    return float(a.group(1).replace(",", "")), float(b.group(1).replace(",", ""))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rows = []
    for s, sl in STORMS:
        for m, ml in MODELS:
            if not (OUT / f"metrics_{s}_{m}.json").exists():
                continue
            mt = json.loads((OUT / f"metrics_{s}_{m}.json").read_text(encoding="utf-8"))
            inp = json.loads((OUT / f"inputs_{s}_{m}.json").read_text(encoding="utf-8"))
            tw = json.loads((C2D / f"twoway_VG_{s}_{m}.json").read_text(encoding="utf-8"))
            c = continuity(C2D / f"VG_{s}_{m}_2way.rpt")
            d12, d21 = exch(C2D / "logs" / f"VG_{s}_{m}.log")
            runoff = inp.get("runoff_m3") or (c.get("External Inflow", 0) - (d21 or 0))
            rows.append({"storm": s, "storm_label": sl, "model": m, "model_label": ml,
                         "runoff_m3": round(runoff), "outfall_m3": round(c.get("External Outflow", 0)), "flood_1d_m3": round(c.get("Flooding Loss", 0)),
                         "to_2d_m3": round(d12 or 0), "back_to_1d_m3": round(d21 or 0), "stored_end_m3": round(c.get("Final Stored Volume", 0)),
                         "surface_end_m3": round(tw["final_vol_m3"]), "boundary_out_m3": round(tw["boundary_out_m3"]),
                         "lost_m3": round(c.get("Flooding Loss", 0) - (d12 or 0)),
                         "hits": mt["hits"], "n_hot": mt["hotspots_n"], "false_alarm_ha": mt["false_alarm_ha"], "ge015_ha": mt["domain_ge015_ha"],
                         "ge030_ha": mt["domain_ge030_ha"], "hmax": round(tw["hmax_m"], 2), "hotspots": mt["hotspots"], "aecom_ref": mt["aecom_reference"]})
    L = ["# RD-A 治理前后验证：算例对比", "", "## 1. 水量平衡（m³）", "",
         "| 雨 | 模型 | 产流入流 | 出水口排出 | 一维溢流 | 进二维 | 回到一维 | 丢失（溢流 − 进二维） | 结束时管网存水 | 结束时地面存水 | 流出计算域边界 |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['storm_label']} | {r['model_label']} | {r['runoff_m3']:,} | {r['outfall_m3']:,} | {r['flood_1d_m3']:,} | {r['to_2d_m3']:,} | {r['back_to_1d_m3']:,} | "
                 f"{r['lost_m3']:,} | {r['stored_end_m3']:,} | {r['surface_end_m3']:,} | {r['boundary_out_m3']:,} |")
    L += ["", "## 2. 指标", "", "| 雨 | 模型 | 热点命中 | 误报面积 ha | 全域 ≥0.15 m ha | 全域 ≥0.30 m ha | 最大水深 m |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['storm_label']} | {r['model_label']} | {r['hits']}/{r['n_hot']} | {r['false_alarm_ha']} | {r['ge015_ha']} | {r['ge030_ha']} | {r['hmax']} |")
    L += ["", "## 3. 各热点最大水深（m）", ""]
    hs = sorted(rows[0]["hotspots"])
    L.append("| 雨 | 模型 | " + " | ".join(hs) + " |")
    L.append("|---|---|" + "---|" * len(hs))
    for r in rows:
        L.append(f"| {r['storm_label']} | {r['model_label']} | " + " | ".join(str(r["hotspots"][h]["max_m"]) for h in hs) + " |")
    L += ["", "## 4. 与 AECOM 摘要对照（只当参照）", "", "| 雨 | AECOM 热点 | AECOM 溢流 m³ | AECOM 退水 h | 各模型井溢流 m³ / 退水 h（按 " + " / ".join(ml for m, ml in MODELS) + "） |", "|---|---|---|---|---|"]
    for s, sl in STORMS:
        rr = {r["model"]: r for r in rows if r["storm"] == s}
        for i, ref in enumerate(rr["A"]["aecom_ref"]):
            cells = []
            for m in [m for m, _ in MODELS if m in rr]:
                x = rr[m]["aecom_ref"][i]
                cells.append(f"{m}: {x['model_node_flood_m3']:,} / {x['model_recede_h']}")
            L.append(f"| {sl} | {ref['aecom_hotspot']} | {ref['aecom_flood_m3']:,} | {ref['aecom_recession_h']} | " + "；".join(cells) + " |")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "compare.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (OUT / "compare.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
