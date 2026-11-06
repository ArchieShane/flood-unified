# -*- coding: utf-8 -*-
"""RD-A 61 mm / 1 h 全套成果：L1 → L2 → L2+planned → L2+suggested（推荐方案 OPTA61PS），2026-10-27 用户：
「输出个针对 rd-a 区域的 61mm1h 的从 L1、L2、L2+planned、L2+suggested 的所有的结果的文件夹，结果里需要有不同场景下的积水点、设施等等的结果」。

两种 61 mm / 1 h 雨型各一套（推荐方案按 AECOM 雨型定，两种都验收过）：
  A61D1  AECOM「10 年 + 20 %」1 h 设计雨原样，60.7 mm，3 分钟一段，峰值 147 mm/h
  S61D1  客户 Mussafah 雨型等比放大，61 mm，5 分钟峰值 352 mm/h（与此前交付的 RDA_61mm1h_全套成果同一场雨）
每个状态：一二维（npz、inp、耦合报告、交换记录、二维设置）、水深栅格（最大 / 逐帧）、积水点（0.15 / 0.30 m 两档，含评估剔除区标记）、设施（逐级累加的 GeoJSON + 清单）；
L2+suggested 另附设施台账、地面取水口、造价汇总、与当前方案（按 96 mm 优化）的差异；有 .sww 的另出飞渡 DTS；有 L3 结果的另附 L3 承接与派单。
复用 tools/export_delivery_rda.py 与 tools/rda_a61_deliver.py 的部件；只读计算结果。
用法：python -X utf8 tools/export_rda_61_full.py [--out out/交付/RDA_61mm1h_全套成果_推荐方案_20261027]
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import export_delivery_rda as X  # noqa: E402
from swbrain import ponding  # noqa: E402
from shapely.geometry import shape  # noqa: E402
from pyproj import Transformer  # noqa: E402

A61, A61P, C96 = ROOT / "out/l2opt/RDA_A61", ROOT / "out/l2opt/RDA_A61P", ROOT / "out/l2opt/RDA_96C"
LEDGER = ROOT / "out/交付/RDA_61mm1h_推荐方案_20261027/RD-A_61mm1h_推荐方案_设施台账.xlsx"
TR = Transformer.from_crs(32640, 4326, always_xy=True).transform
STATES = ["L1", "L2", "L2P", "OPTA61PS"]
DIRN = {"L1": "L1_现状", "L2": "L2_雨前预置泵", "L2P": "L2+planned_客户计划", "OPTA61PS": "L2+suggested_推荐方案"}
ZH = {"L1": "L1 现状", "L2": "L2 雨前预置泵", "L2P": "L2+planned 客户计划", "OPTA61PS": "L2+suggested 推荐方案（61 mm / 1 h）"}
STORMS = {"A61D1": ("AECOM雨型_60.7mm1h", "60.7 mm / 1 h，AECOM「10 年一遇 + 20 % 气候变化」1 小时设计雨原样（RD-A SewerGEMS 模型情景 10yrs.+20%CC-1Hr），3 分钟一段，最大雨强 147 mm/h"),
          "S61D1": ("Mussafah雨型_61mm1h", "61 mm / 1 h，客户 Mussafah 模型「10 年一遇含 CC」1 小时雨型等比放大，5 分钟峰值 352 mm/h（与此前交付的 RDA_61mm1h_全套成果同一场雨）")}
X.LAYER["OPTA61PS"] = "L2+suggested 推荐方案"


def ll(x, y):
    lo, la = TR(x, y); return [round(la, 6), round(lo, 6)]


def suggested_facs(storm):
    """推荐方案新增设施（与系统 rda_console 的 v3_facs 同一套画法，不含本场模拟量）。"""
    V = json.loads((A61P / f"variant_{storm}_OPTA61P.json").read_text(encoding="utf-8"))
    cand = {c["id"]: c for c in json.loads((A61P / "candidates.json").read_text(encoding="utf-8"))["candidates"]}
    G = {f["properties"]["id"]: f["geometry"] for f in json.loads((A61P / "candidates.geojson").read_text(encoding="utf-8"))["features"]}
    import rda_console as RC
    CO = RC.coords(X.WORK / f"RDA_{storm}_OPTA61PS.inp")
    lat = lambda g: [[round(y, 6), round(x, 6)] for x, y in g]   # noqa: E731
    fs = []
    for m in V["measures"]:
        c = cand[m["id"]]; k = c["kind"]; w = CO.get(c.get("well"))
        cost = f"占位造价 {c['cost_usd'] / 1e6:.2f} M USD"
        if k in ("tank_gravity", "tank_pumped"):
            g = shape(G[m["id"]]) if m["id"] in G else None
            if g is not None and g.geom_type == "MultiPolygon":
                g = max(g.geoms, key=lambda q: q.area)
            pc = [round(g.centroid.y, 6), round(g.centroid.x, 6)] if g is not None and g.geom_type == "Polygon" else None
            fs.append({"kind": "tank", "id": m["id"], "vol_m3": c.get("pond_vol_m3"), "ll": pc or ll(c["px"], c["py"]), "poly": lat(g.exterior.coords) if pc else None,
                       "p": {"why": f"{'重力进水地下调蓄池' if k == 'tank_gravity' else '井边泵站 + 地下调蓄池'}（截流井 {c.get('well')}，热点 {c.get('hotspot')}，开挖 {c.get('excavation_m', '—')} m）；{cost}"}})
            if w and pc:
                fs.append({"kind": "gpipe" if k == "tank_gravity" else "conv", "id": m["id"] + ("_g" if k == "tank_gravity" else ""), "ll": ll(*w),
                           **({"line": [ll(*w), pc]} if k == "tank_gravity" else {"to_ll": pc, "q_lps": round(c.get("q_m3s", 0) * 1000)}),
                           "p": {"why": f"重力管 井 {c.get('well')} → 地下池，长 {c.get('pipe_m')} m，管径 {c.get('pipe_d_m')} m" if k == "tank_gravity"
                                 else f"井边泵站 {c.get('n_pumps')} 台 → 压力管 {c.get('pipe_m')} m → 地下池"}})
        elif k == "ps_cluster":
            fs.append({"kind": "conv", "id": m["id"], "q_lps": round(c["q_m3s"] * 1000), "ll": ll(c["ps_x"], c["ps_y"]), "to_ll": ll(*c["of_xy"]),
                       "p": {"why": f"泵站 → 新建排海口：截流井 {', '.join(c.get('wells') or [])}，{c.get('n_pumps')} 台 × {c.get('pump_each_m3s')} m³/s，压力管 {c.get('pipe_m')} m → {c.get('outfall_site')}；{cost}"}})
    sm = json.loads((C96 / "sumps_OPTCS2.json").read_text(encoding="utf-8"))
    for s_ in sm["stations"]:
        for j, it in enumerate(s_["intakes"]):
            fs.append({"kind": "sump", "id": f"{s_['id']}_{j}", "q_lps": round(it["q_m3s"] * 1000), "ll": ll(it["x"], it["y"]), "to_ll": ll(s_["ps_x"], s_["ps_y"]),
                       "poly": [ll(x, y) for x, y in it["poly"]], "p": {"why": f"地面取水口（{it['class']}）→ 地面泵站 {s_['id']}"}})
        fs.append({"kind": "conv", "id": s_["id"], "q_lps": round(s_["q_m3s"] * 1000), "ll": ll(s_["ps_x"], s_["ps_y"]), "to_ll": ll(*s_["of_xy"]),
                   "p": {"why": f"地面泵站：{len(s_['intakes'])} 个取水口，{s_['n_pumps']} 台 × {s_['pump_each_m3s']} m³/s → 新建排海口 {s_['outfall_site']}；占位造价 {s_['cost_usd'] / 1e6:.2f} M USD"}})
    return fs


def ledger_csvs(d):
    import openpyxl
    wb = openpyxl.load_workbook(LEDGER, read_only=True, data_only=True)
    for sh in ("设施台账", "地面取水口", "造价汇总", "与当前方案的差异", "方案对比"):
        R = [list(r) for r in wb[sh].iter_rows(values_only=True)]
        X.wcsv(d / f"L2+suggested_{sh}.csv", R[0], [["" if v is None else v for v in r] for r in R[1:]])


def rain_rows(storm):
    if storm == "S61D1":
        h = json.loads((ROOT / "console/data/flood2d_rda_s61d1/rda_stats.json").read_text(encoding="utf-8"))["hyeto"]
        return ["时刻 h", "雨强 mm/h", "累计雨量 mm", "累计净雨 mm"], [[a, b, c, e] for a, b, c, e in zip(h["t_h"], h["p_mmph"], h["p_cum_mm"], h["exc_cum_mm"])]
    import rda_build as RB
    info = json.loads((ROOT / "out/rda_inputs_A61D1_L1.json").read_text(encoding="utf-8"))
    inp, ts = info["rain_shape"].split(" · ")
    cum = RB.aecom_shape(str(ROOT / "20261019/test/RD-A/RD-A/Hydraulic Model" / inp), ts, 1.0, log=lambda *x: None) * 25.4   # 序列单位是英寸
    rows, prev = [], 0.0
    for i, c in enumerate(cum):
        dt = 1.0 / (len(cum) - 1) if len(cum) > 1 else 1.0
        rows.append([round(i * dt, 3), round((c - prev) / dt, 2) if i else 0.0, round(c, 2)]); prev = c
    return ["时刻 h", "时段平均雨强 mm/h", "累计雨量 mm"], rows


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=str(ROOT / "out/交付/RDA_61mm1h_全套成果_推荐方案_20261027"))
    a = ap.parse_args()
    root = pathlib.Path(a.out); root.mkdir(parents=True, exist_ok=True)
    FAC = json.loads((ROOT / "console/data/flood2d_rda_s61d1/rda_stats.json").read_text(encoding="utf-8"))["facilities"]   # L1 / L2 / L2+planned 设施与雨无关
    summary = []
    for storm, (fold, desc) in STORMS.items():
        run = f"RDA_{storm}"; out = root / fold
        miss = [st for st in STATES if not (X.C2D / "tif" / f"{run}_{st}_depth_max.tif").exists()]
        if miss:
            subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools/npz_to_tif.py"), run, *miss], check=True, stdout=subprocess.DEVNULL)
        dirs = {st: out / DIRN[st] for st in STATES}
        ponding.export_delivery(run, dirs, out, pond_scope="state")
        FAC2 = {**FAC, "OPTA61PS": suggested_facs(storm)}
        X.CHAIN["OPTA61PS"] = ["L1", "L2", "L2P", "OPTA61PS"]
        ex = X.exclude_zones()
        l3f = ROOT / f"console/data/flood2d_rda_{storm.lower()}/l3.json"
        L3 = json.loads(l3f.read_text(encoding="utf-8")) if l3f.exists() else None
        nf = {}
        for st, d in dirs.items():
            X.model_files(run, st, d)
            X.cp(X.C2D / "anuga_out" / f"{run}_{st}.sww", d / "一二维" / f"{run}_{st}_二维水深与流速时序.sww")   # 2026-10-27 补：.sww 也要进交付
            nf[st] = X.facilities(run, st, d, FAC2)
            if L3 and st in L3["states"]:
                X.ponding_extra(st, d, L3, ex)
        ledger_csvs(dirs["OPTA61PS"] / "设施")
        # 公共
        o = out / "公共"; o.mkdir(parents=True, exist_ok=True)
        P = json.loads((X.C2D / f"pilot_{run}_L1.json").read_text(encoding="utf-8")); dom = P.get("domain_polygon")
        if dom:
            from shapely.geometry import Polygon, mapping
            from shapely.ops import transform
            X.wjson(o / "计算域.geojson", X.fc("domain", [{"type": "Feature", "geometry": mapping(transform(TR, Polygon([p[:2] for p in dom]))), "properties": {"name": "RD-A 计算域"}}]))
        X.cp(X.C2D / f"dem_{run}.tif", o / f"{run}_DTM_二维计算用.tif")
        g = json.loads((C96 / "exclude_OPTCS.geojson").read_text(encoding="utf-8"))
        from shapely.geometry import mapping as _m
        from shapely.ops import transform as _t
        X.wjson(o / "评估剔除区.geojson", X.fc("exclude_zones", [{"type": "Feature", "geometry": _m(_t(TR, shape(f["geometry"]))), "properties": f["properties"]} for f in g["features"]]))
        Hs = json.loads((ROOT / "console/data/hotspots.geojson").read_text(encoding="utf-8"))
        ids = {"ADM-75", "ADM-76", "ADM-80", "ADM-161", "ADM-376", "ADM-377", "ADM-378", "ADM-379", "ADM-380", "ADM-382", "ADM-383"}
        X.wjson(o / "客户热点.geojson", X.fc("hotspots_RDA", [f for f in Hs["features"] if f["properties"]["hotspot_id"] in ids]))
        hd, rr = rain_rows(storm); X.wcsv(o / f"降雨过程_{storm}.csv", hd, rr)
        E = json.loads((A61 / f"eval2d_{storm}_ex.json").read_text(encoding="utf-8"))["states"]
        X.wjson(o / f"判据评估_{storm}.json", {"storm": storm, "states": {s: E[s] for s in STATES if s in E}})
        sww = [st for st in STATES if (X.C2D / "anuga_out" / f"{run}_{st}.sww").exists()]
        if sww:
            from swbrain import dts_h2d
            dts_h2d.export_delivery(run, X.C2D / "anuga_out", {s: dirs[s] for s in sww}, out, names={s: ZH[s] for s in sww})
        for st in STATES:
            e = E[st]
            pts = {dep: json.loads((dirs[st] / "积水点" / f"ponding_points_d{dep:.2f}.geojson").read_text(encoding="utf-8"))["features"] for dep in ponding.DELIVERY_DEPTHS}
            summary.append({"降雨": desc.split("，")[0] + ("（AECOM 雨型）" if storm == "A61D1" else "（Mussafah 雨型）"), "状态": ZH[st],
                            "客户热点四项全过（/11）": sum(all(v["pass"].values()) for v in e["hotspots"].values()),
                            "未过的热点": "、".join(h for h, v in e["hotspots"].items() if not all(v["pass"].values())),
                            "全域 ≥0.30 m 面积 ha": e["domain"]["ge030_ha"], "峰值地面积水量 m³": e["domain"].get("peak_vol_m3"),
                            "积水点 ≥0.15 m": len(pts[0.15]), "积水点 ≥0.30 m": len(pts[0.30]),
                            "积水点 ≥0.15 m 峰值积水量合计 m³": round(sum(f["properties"].get("vol_peak_m3") or 0 for f in pts[0.15])),
                            "设施图层要素": nf[st], "L3 承接与派单": "有" if (L3 and st in L3["states"]) else "无（未算）",
                            ".sww / 飞渡DTS": "有" if st in sww else "无", "文件夹": f"{fold}/{DIRN[st]}"})
        print("→", out, flush=True)
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "汇总"
    ws.append(list(summary[0])); [ws.append(list(r.values())) for r in summary]
    for c in ws.columns:
        ws.column_dimensions[c[0].column_letter].width = max(10, min(40, max(len(str(x.value or "")) for x in c) + 2))
    wb.save(root / "RD-A_61mm1h_四个状态_结果汇总.xlsx")
    rows = "\n".join(f"| {r['降雨']} | {r['状态']} | {r['客户热点四项全过（/11）']} | {r['全域 ≥0.30 m 面积 ha']} | {r['积水点 ≥0.15 m']} | {r['积水点 ≥0.30 m']} | {r['L3 承接与派单']} | {r['.sww / 飞渡DTS']} |" for r in summary)
    (root / "README.md").write_text(README.format(gen=time.strftime("%Y-%m-%d %H:%M"), rows=rows), encoding="utf-8")
    print("→", root)


README = """# RD-A 61 mm / 1 h 全套成果：L1 → L2 → L2+planned → L2+suggested（推荐方案）

生成：{gen}

## 内容

两种 61 mm / 1 h 雨型各一套，每套四个状态，每个状态都有二维结果、水深栅格、**二维模拟积水点**、**设施**：

| 文件夹 | 降雨 |
|---|---|
| `AECOM雨型_60.7mm1h/` | AECOM「10 年一遇 + 20 % 气候变化」1 小时设计雨原样：60.7 mm，3 分钟一段，最大雨强 147 mm/h。**推荐方案就是按这场雨定的** |
| `Mussafah雨型_61mm1h/` | 客户 Mussafah 雨型等比放大：61 mm，5 分钟峰值 352 mm/h。峰值更尖，作为更严苛的复核 |

四个状态：`L1_现状/`、`L2_雨前预置泵/`、`L2+planned_客户计划/`、`L2+suggested_推荐方案/`。

**L2+suggested 推荐方案**：53 项设施，占位造价约 1.19 亿美元。来源是当前方案（按 96 mm 优化，60 项，1.34 亿美元）以 AECOM 雨型为控制雨剪枝：删 7 项、换 6 项，7 座地面泵站全部保留。在 61 mm 各场雨下，效果与当前方案相同。逐项台账见 `L2+suggested_推荐方案/设施/L2+suggested_设施台账.csv`，以及 `RDA_61mm1h_推荐方案_20261027/` 里的 Excel。

## 结果汇总（详见 `RD-A_61mm1h_四个状态_结果汇总.xlsx`）

| 降雨 | 状态 | 客户热点四项全过（/11） | 全域 ≥0.30 m 面积 ha | 积水点 ≥0.15 m | 积水点 ≥0.30 m | L3 承接与派单 | .sww / 飞渡 DTS |
|---|---|---|---|---|---|---|---|
{rows}

## 每个状态文件夹

| 子文件夹 | 内容 |
|---|---|
| `一二维/` | `_二维水深与流速时序.sww`（ANUGA 标准格式，每 5 分钟一帧水深与流速）、`_二维结果.npz`（单元形心、面积、高程、最大水深、每 5 分钟水深帧、流速）、`.inp`（SWMM 管网，含本状态的全部设施）、`_一二维耦合_SWMM报告.rpt`、`_耦合交换记录.json`、`_二维设置.json`（含地面泵的取水口） |
| `水深栅格/` | `_最大水深.tif`、`_逐帧水深.tif`（多波段，时刻见同名 json）；15 m，UTM 40N |
| `积水点/` | 二维模拟积水点，0.15 m 和 0.30 m 两档：中文清单、积水范围面、最深点、与客户热点的对应、评估剔除区标记；有 L3 结果的另附 L3 承接与派单。积水点与积水面的关系见 `docs/二维模拟积水点与积水面的关系说明_20261027` |
| `设施/` | 本状态全部设施，逐级累加（L1 ⊂ L2 ⊂ L2+planned ⊂ L2+suggested）：`_设施.geojson`（WGS84，调蓄池画占地，管线、泵站画点和线）、`_排放连线.geojson`、`_设施清单.csv`；L2+suggested 另有设施台账、地面取水口、造价汇总、与当前方案的差异、方案对比 |
| `飞渡DTS/` | 只有保留了 .sww 的状态才有 |

`公共/`：计算域、客户热点、二维计算用 DTM、评估剔除区、降雨过程、判据评估结果。

## 模型与口径

- 一维 SWMM 5.2（AECOM RD-A 管网，换算公制）+ 二维 ANUGA。网格参数 15 m：三角形面积 ≤75 m²，65,210 个单元，高程取 5 m DTM；一二维每 60 s 交换一次。雨停后再算 10 h。
- 判据（热点 200 m 内）：
  - A：无 ≥0.30 m 积水；
  - B：≥0.15 m 面积 × 时间 ≤ 客户计划的 10 %；
  - C：雨停后 6 h 内退到 0.10 m 以下；
  - D：全域比客户计划加深 ≥5 cm 的面积 ≤0.05 ha。
  - 阈值是建议值，待客户确认。
- 造价是占位单价，用于方案比较，不是工程概算。
- 管网来自 AECOM 顾问模型。治理前后对比已证明它与治理后的 DMT 管网一致，但不是用治理后数据重建的。
- L3 承接与派单：行程时间按 ArcGIS Online 卡车路线实测校正（本地路网 × 1.70）；2026-10-27 修正了调度求解器的一处问题（第一轮无可派点时提前结束），各状态均为修正后的结果。
"""


if __name__ == "__main__":
    main()
