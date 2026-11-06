# -*- coding: utf-8 -*-
"""Mussafah_00 模型版 15 m 一场雨四态的后处理与交付目录（tools/ms_run_15m.py 的 post:<情景> 最后一步，2026-10-10）。

前面几步已做好：逐帧图 / manifest（pilot_frames.py → console/data/flood2d_ms_<情景>/）、GeoTIFF（npz_to_tif.py → $WORK/c2d/tif/）、
.sww（容器里 npz_to_sww.py → $WORK/c2d/anuga_out/）。这里做：
  1. 统计：4 个热点各自 200 m 内、4 个热点 200 m 并集、全域，各态的最大水深、积水面积（≥ 0.10 / ≥ 0.30 m）、最大积水量与出现时刻；最深点核查
  2. 水量对账：二维（1D→2D、2D→1D、边界出流、域内留存）与一维（SWMM 连续性：外部入流、出水口出流、溢流、连续性误差）
  3. 相邻态差值图：L2+planned − L1、L2+suggested − L2+planned、L2+suggested − L1
  4. 交付目录 out/交付/Mussafah_00_模型版_<雨量>mm<历时>h/：公共（计算域、热点、汇水区参数）、各态一维 / 二维文件、
     L2+suggested 措施清单、对比统计、计算设置.md、00_说明.md（只复制，不改原始结果）
口径同 C1：形心水深，< 0.02 m 算干，面积按三角单元真实面积加权，最大积水量 = 逐帧 Σ 水深 × 面积 的最大值。
用法：python tools/ms_post.py --tag M33H1
产物：out/ms_stats_<情景>.json / .csv、console/data/flood2d_ms_<情景>/ms_stats.json、diff_*.png、交付目录
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys

import numpy as np

os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = os.path.join(os.path.dirname(importlib.util.find_spec("rasterio").origin), "proj_data")

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import ms_run_15m as MS  # noqa: E402

C2D = MS.P.C2D
HS_R = 200.0
TH = (0.10, 0.30)
DIRS = {"L1": "L1_既有管网", "L2": "L2_已建成调蓄", "L2P": "L2+planned", "L2S3": "L2+suggested"}
PAIRS = [("L1", "L2P"), ("L2P", "L2S3"), ("L1", "L2S3")]


def routing_continuity(rpt: pathlib.Path) -> dict:
    """SWMM 报告的 Flow Routing Continuity（CMS 模型：第二列是 10^6 ltr = 1000 m³）。"""
    t = rpt.read_text(errors="ignore")
    m = re.search(r"Flow Routing Continuity(.*?)\n\s*\n\s*\n", t, re.S) or re.search(r"Flow Routing Continuity(.*?)\*{5,}", t, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        k = re.match(r"\s*([A-Za-z][A-Za-z /()%]+?)\s*\.{2,}\s*(-?[\d.]+)(?:\s+(-?[\d.]+))?", ln)
        if k:
            out[k.group(1).strip()] = float(k.group(3)) * 1000.0 if k.group(3) else float(k.group(2))
    return out


def node_flooding(rpt: pathlib.Path) -> dict:
    t = rpt.read_text(errors="ignore")
    m = re.search(r"Node Flooding Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        q = ln.split()
        if len(q) >= 6:
            out[q[0]] = float(q[5]) * 1000.0
    return out


def cp(src: pathlib.Path, dst: pathlib.Path, done: list, miss: list) -> None:
    if not src.exists():
        miss.append(str(src)); return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst); done.append(dst)


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", required=True, choices=list(MS.SCEN))
    a = ap.parse_args(argv)
    import geopandas as gpd
    from scipy.spatial import cKDTree
    from city2d_post import render_diff
    t = a.tag; name = MS.name_of(t); rain, dur = MS.SCEN[t]
    have = [s for s in MS.STATES if (C2D / f"twoway_{name}_{s}.json").exists() and (C2D / f"{name}_{s}_2d.npz").exists()]
    miss_st = [s for s in MS.STATES if s not in have]
    if miss_st:
        raise SystemExit(f"{name}：{miss_st} 还没算完，四态齐了再做后处理")
    D = ROOT / "console" / "data" / f"flood2d_ms_{t.lower()}"; D.mkdir(parents=True, exist_ok=True)
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    Z = {s: np.load(C2D / f"{name}_{s}_2d.npz") for s in have}
    TW = {s: json.loads((C2D / f"twoway_{name}_{s}.json").read_text(encoding="utf-8")) for s in have}
    META = {s: json.loads((C2D / f"pilot_{name}_{s}.json").read_text(encoding="utf-8")) for s in have}
    z0 = Z[have[0]]; xy = z0["xy"].astype(float); area = z0["area"].astype(float); elev = z0["elev"].astype(float)
    tr = cKDTree(xy)
    masks = {}
    for h in MS.HS:
        p = H.loc[h].geometry
        m = np.zeros(len(xy), bool); m[tr.query_ball_point((p.x, p.y), HS_R)] = True
        masks[f"{h}|200m"] = m
    masks["4 个热点|200m"] = np.any([masks[f"{h}|200m"] for h in MS.HS], axis=0)
    masks["全域|all"] = np.ones(len(xy), bool)
    res = {"name": name, "tag": t, "rain_mm": rain, "dur_h": dur, "sim_h": MS.end_h(t), "res_m": MS.RES_M, "states": have,
           "state_names": {s: MS.SNAME[s] for s in have}, "zones": {}, "deepest": {}, "balance": {}, "diffs": [],
           "koujing": {"水深": "三角单元形心水深（ANUGA stage − elevation）", "干湿": "< 0.02 m 算干",
                       "积水面积": "全程最大水深 ≥ 0.10 / ≥ 0.30 m 的单元面积之和（按三角单元真实面积）",
                       "最大积水量": "逐帧（每 5 分钟）Σ 水深 × 单元面积 的最大值，出现时刻为该帧时刻",
                       "热点 200 m": "离热点坐标 200 m 内的单元", "全域": "二维计算域（客户 Musaffah_00 模型汇水区并集）",
                       "一维溢流": "SWMM Node Flooding Summary 累加；计算域内的井溢流交给二维，域外的井只算一维、记为损失"}}
    for s in have:
        z = Z[s]; hm = z["hmax"].astype(float); fr = z["frames"]; th = z["t_h"]
        for key, m in masks.items():
            vols = np.array([float((fr[i].astype(float)[m] * area[m]).sum()) for i in range(len(th))])
            i = int(vols.argmax()) if len(vols) else 0
            res["zones"].setdefault(key, {})[s] = {
                "max_depth_m": round(float(hm[m].max()) if m.any() else 0.0, 3),
                "area_ge_0p10_ha": round(float(area[m & (hm >= TH[0])].sum()) / 1e4, 2),
                "area_ge_0p30_ha": round(float(area[m & (hm >= TH[1])].sum()) / 1e4, 2),
                "peak_vol_m3": round(float(vols[i])) if len(vols) else 0, "peak_vol_t_h": round(float(th[i]), 2) if len(th) else None,
                "zone_area_ha": round(float(area[m].sum()) / 1e4, 1)}
        k = int(hm.argmax()); px, py = xy[k]
        dh = {h: float(np.hypot(px - H.loc[h].geometry.x, py - H.loc[h].geometry.y)) for h in MS.HS}; nh = min(dh, key=dh.get)
        res["deepest"][s] = {"depth_m": round(float(hm[k]), 2), "x": round(float(px)), "y": round(float(py)), "ground_m": round(float(elev[k]), 2),
                             "min_ground_50m": round(float(elev[tr.query_ball_point((px, py), 50.0)].min()), 2),
                             "median_ground_200m": round(float(np.median(elev[tr.query_ball_point((px, py), 200.0)])), 2),
                             "near_hotspot": nh, "near_dist_m": round(dh[nh])}
        rpt = C2D / f"{name}_{s}_2way.rpt"
        nf = node_flooding(rpt); inside = META[s]["nodes"]
        fin = sum(v for n, v in nf.items() if n in inside); fout = sum(v for n, v in nf.items() if n not in inside)
        rc = routing_continuity(rpt); acc = TW[s]["acc_m3"]
        res["balance"][s] = {"1D→2D": round(acc["1to2"]), "2D→1D": round(acc["2to1"]), "泵排出系统（二维取水）": round(acc.get("pump_out", 0)),
                             f"域内留存（{MS.end_h(t):g} h 末）": round(TW[s]["final_vol_m3"]), "边界出流（余项）": round(TW[s]["boundary_out_m3"]),
                             "一维：域内井溢流（=交给二维）": round(fin), "一维：不交换节点溢流（域外或埋地节点，只算一维，记为损失）": round(fout),
                             "一维：外部入流（净雨）": round(rc.get("External Inflow", 0)), "一维：出水口出流": round(rc.get("External Outflow", 0)),
                             "一维：连续性误差 %": rc.get("Continuity Error (%)"), "wall_h": round(TW[s]["wall_s"] / 3600, 2)}
    for s1, s2 in PAIRS:
        dh = Z[s2]["hmax"].astype(float) - Z[s1]["hmax"].astype(float)
        fn = f"diff_{s2}_minus_{s1}.png"
        b = render_diff(xy, dh, float(MS.RES_M), D / fn)
        res["diffs"].append({"a": s1, "b": s2, "png": fn, "bounds": b, "shallower_ha": round(float(area[dh <= -0.05].sum()) / 1e4, 2),
                             "deeper_ha": round(float(area[dh >= 0.05].sum()) / 1e4, 2)})
    f5 = ROOT / "out" / f"ms_L2S3_inputs_{t}.json"
    res["l2s3"] = json.loads(f5.read_text(encoding="utf-8")) if f5.exists() else None

    # ---- 交付目录
    DD = ROOT / "out" / "交付" / f"Mussafah_00_模型版_{rain}mm{dur}h"
    done, miss = [], []
    cp(MS.DOMAIN, DD / "公共" / "计算域.geojson", done, miss)
    cp(MS.SUBS, DD / "公共" / "汇水区参数表.csv", done, miss)
    (DD / "公共").mkdir(parents=True, exist_ok=True)
    H.loc[MS.HS].reset_index()[["hotspot_id", "area", "criticality", "location", "geometry"]].to_crs(4326).to_file(DD / "公共" / "热点.geojson", driver="GeoJSON")
    W = MS.P.WORK
    for s in have:
        d = DD / DIRS[s]
        cp(W / f"{name}_{s}.inp", d / "一维" / f"{name}_{s}.inp", done, miss)
        cp(C2D / f"{name}_{s}_2way.rpt", d / "一维" / f"{name}_{s}_一维报告.rpt", done, miss)
        cp(C2D / f"twoway_{name}_{s}.json", d / "二维" / f"{s}_耦合运行记录.json", done, miss)
        for f in sorted((C2D / "tif").glob(f"{name}_{s}_depth_*")):
            cp(f, d / "二维" / f.name.replace("_depth_max", "_最大水深").replace("_depth_frames", "_逐帧水深"), done, miss)
        cp(C2D / "anuga_out" / f"{name}_{s}.sww", d / "二维" / f"{name}_{s}.sww", done, miss)
    if res["l2s3"]:
        (DD / DIRS["L2S3"] / "设施").mkdir(parents=True, exist_ok=True)
        (DD / DIRS["L2S3"] / "设施" / "措施清单.json").write_text(json.dumps(res["l2s3"], ensure_ascii=False, indent=1), encoding="utf-8")
    for dd in res["diffs"]:
        cp(D / dd["png"], DD / "对比_缺口分析" / dd["png"], done, miss)
    (DD / "对比_缺口分析").mkdir(parents=True, exist_ok=True)
    rows = [["区域", "口径", "指标"] + [MS.SNAME[s] for s in have]]
    for key, v in res["zones"].items():
        zn, kind = key.split("|")
        for met, lab in (("max_depth_m", "最大水深 m"), ("area_ge_0p10_ha", "积水面积 ≥0.10 m（ha）"), ("area_ge_0p30_ha", "积水面积 ≥0.30 m（ha）"),
                         ("peak_vol_m3", "最大积水量 m³"), ("peak_vol_t_h", "最大积水量出现时刻 h")):
            rows.append([zn, {"200m": "热点 200 m", "all": "全域"}[kind], lab] + [v[s][met] for s in have])
    for k2 in res["balance"][have[0]]:
        rows.append(["全域", "水量对账 m³", k2] + [res["balance"][s][k2] for s in have])
    for f in (ROOT / "out" / f"ms_stats_{t}.csv", DD / "对比_缺口分析" / "统计值.csv"):
        with open(f, "w", newline="", encoding="utf-8-sig") as fh:
            csv.writer(fh).writerows(rows)
    inp = json.loads((ROOT / "out" / f"ms_inputs_{t}.json").read_text(encoding="utf-8"))
    G = res["zones"]["4 个热点|200m"]; A = res["zones"]["全域|all"]
    l2 = res["l2s3"] or {}
    (DD / "计算设置.md").write_text("\n".join([
        f"# Mussafah_00 模型版 · {rain} mm / {dur} h · 计算设置", "",
        "| 项 | 设置 |", "|---|---|",
        f"| 情景 | {rain} mm / {dur} h，雨型取客户 Mussafah 模型里同历时的设计雨型、等比放大；模拟 {MS.end_h(t):g} h（历时 + 4 h） |",
        f"| 计算域 | 客户 Musaffah_00 模型汇水区并集，{MS.CLOSE_M:g} m 闭运算连成一块、补 < {MS.HOLE_HA:g} ha 空洞，{inp['domain_km2']} km²；四周自由出流，域外地表来水不算 |",
        "| 热点 | ADM-86、ADM-87（工业区地道，Very High），同位置 ADM-14、ADM-189 |",
        "| 一维 | EPA SWMM 5.2（anuga-swmm 镜像），客户交付的 Mussafah_00.inp；L1 = 剥掉 Parsons 塘 / 集水井 / 配套泵（既有泵站 RD-27 / RD-34 保留）；流量单位换成 CMS，动力波 1 s 步长，地面积水关（冒出的水交给二维） |",
        f"| 产流 | 客户汇水区图层逐汇水区 SCS CN + SCS 单位线（Tc），{inp['catch_ha']} ha，净雨 {inp['excess_mm']} mm，产流 {inp['runoff_m3']:,} m³，按出流节点注入一维；二维上不另下雨 |",
        "| 四态 | L1 既有管网；L2 已建成调蓄（区内 0 座，模型同 L1，照算）；L2+planned = 客户原件（Parsons STO-PO3 塘 + W-4 / W-6 集水井 + 2 台泵）；"
        "L2+suggested = 在 L2+planned 上按这场雨的残余积水用 v3 规则加措施（转输泵进有余量的塘或排出水口 / 地面取水泵排出水口 / 箱涵） |",
        f"| 二维 | ANUGA，非结构三角网，单元面积上限 {MS.RES_M}²/3 m²（约 {inp['n_tri_est']:,} 个），地形 = DTM 原值（{inp['dtm']['res_m']:g} m），曼宁 n = 0.030 |",
        "| 耦合 | couple2way_v2，同进程逐步双向，交换步 60 s；计算域内的井参与交换（井盖孔口 Cd 0.6、DN600），冒出的水分摊到井周 4 个单元 |",
        "| 设施 | 塘、集水井、箱涵、转输泵都在一维里；地面取水泵在二维上按片取水；DTM 不挖 |",
        "| 输出 | 二维每 5 分钟一帧（形心水深、x / y 单宽流量）；.sww 为 UTM 40N（EPSG:32640） |",
        "", "差值只归因于设施：四态同一张管网、同一场雨、同一计算域。", ""]), encoding="utf-8")
    lines = [f"# Mussafah_00 模型版 · {rain} mm / {dur} h · 四态结果（15 m 网格）", "",
             "| 态 | 目录 | 4 个热点 200 m 内最大水深 | ≥0.10 m 面积（热点 200 m） | ≥0.30 m 面积（全域） | 最大积水量（全域） | 一维溢流（域内） |",
             "|---|---|---|---|---|---|---|"]
    for s in have:
        b = res["balance"][s]
        lines.append(f"| {MS.SNAME[s]} | `{DIRS[s]}/` | {G[s]['max_depth_m']} m | {G[s]['area_ge_0p10_ha']} ha | {A[s]['area_ge_0p30_ha']} ha | "
                     f"{A[s]['peak_vol_m3']:,} m³ | {b['一维：域内井溢流（=交给二维）']:,} m³ |")
    lines += ["", f"L2+suggested 措施：{l2.get('summary', {})}，泵合计 {l2.get('q_total_m3s', 0)} m³/s，箱涵 {l2.get('box_total_m3', 0):,} m³（详见 `L2+suggested/设施/措施清单.json`）。",
              "", "完整指标与水量对账见 `对比_缺口分析/统计值.csv`；输入参数见 `计算设置.md`。",
              "9 月 28 日那版 `Mussafah_00_61mm2h`（旧计算域 1.97 × 7.39 km 矩形、5 m DTM、旧耦合程序）口径不同，不要和本目录直接对比。"]
    if miss:
        lines += ["", "**缺的文件**（后处理时不在）：", *[f"- `{m}`" for m in miss]]
    (DD / "00_说明.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    res["delivery"] = {"dir": str(DD), "n_files": len(done), "missing": miss}
    (D / "ms_stats.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    MS.post_out(t).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  → {MS.post_out(t).name}、{D.name}/ms_stats.json；交付 {DD}（{len(done)} 个文件" + (f"，缺 {len(miss)} 个" if miss else "") + "）")
    for s in have:
        dp = res["deepest"][s]
        print(f"    {MS.SNAME[s]:<13} 热点 200 m 内最深 {G[s]['max_depth_m']} m，≥0.10 m {G[s]['area_ge_0p10_ha']} ha；全域最深 {dp['depth_m']} m @ ({dp['x']},{dp['y']}) "
              f"离 {dp['near_hotspot']} {dp['near_dist_m']} m；一维连续性误差 {res['balance'][s]['一维：连续性误差 %']} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
