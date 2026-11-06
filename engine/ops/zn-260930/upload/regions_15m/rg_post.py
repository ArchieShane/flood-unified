# -*- coding: utf-8 -*-
"""三个区域（YAS / BAT / BNY）15 m 一场雨 L1 / L2 的后处理与交付目录（tools/rg_run_15m.py 的 post:<情景> 最后一步，2026-10-11）。

前面几步已做好：逐帧图 / manifest（pilot_frames.py → console/data/flood2d_rg_<情景>/）、GeoTIFF（npz_to_tif.py → $WORK/c2d/tif/）、
.sww（容器里 npz_to_sww.py → $WORK/c2d/anuga_out/）。这里做（同 ms_post.py，只剩两个态）：
  1. 统计：区域内各热点 200 m 内、热点 200 m 并集、全域，各态的最大水深、积水面积（≥ 0.10 / ≥ 0.30 m）、最大积水量与出现时刻；最深点核查
  2. 水量对账：二维（1D→2D、2D→1D、边界出流、域内留存）与一维（SWMM 连续性：外部入流、出水口出流、溢流、连续性误差）
  3. 差值图：L2 − L1
  4. 交付目录 out/交付/<区域目录>_模型版_<雨量>mm<历时>h/：公共（计算域、热点、现状泵站、塘）、各态一维 / 二维文件、对比统计、计算设置.md、00_说明.md（只复制，不改原始结果）
口径同 C1：形心水深，< 0.02 m 算干，面积按三角单元真实面积加权，最大积水量 = 逐帧 Σ 水深 × 面积 的最大值。
用法：python tools/rg_post.py --tag YAS_M33H1
产物：out/rg_stats_<情景>.json / .csv、console/data/flood2d_rg_<情景>/rg_stats.json、diff_*.png、交付目录
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
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import rg_run_15m as RG  # noqa: E402

C2D = RG.P.C2D
HS_R = 200.0
TH = (0.10, 0.30)
PAIRS = [("L1", "L2")]


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
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", required=True, choices=list(RG.SCEN))
    a = ap.parse_args(argv)
    import geopandas as gpd
    from scipy.spatial import cKDTree
    from city2d_post import render_diff
    t = a.tag; reg, _ = RG.split_tag(t); R = RG.REGIONS[reg]; name = RG.name_of(t); rain, dur = RG.SCEN[t]
    HS = RG.hotspots(reg)
    have = [s for s in RG.STATES if (C2D / f"twoway_{name}_{s}.json").exists() and (C2D / f"{name}_{s}_2d.npz").exists()]
    miss_st = [s for s in RG.STATES if s not in have]
    if miss_st:
        raise SystemExit(f"{name}：{miss_st} 还没算完，两个态齐了再做后处理")
    D = ROOT / "console" / "data" / f"flood2d_rg_{t.lower()}"; D.mkdir(parents=True, exist_ok=True)
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    HS = [h for h in HS if h in H.index]
    Z = {s: np.load(C2D / f"{name}_{s}_2d.npz") for s in have}
    TW = {s: json.loads((C2D / f"twoway_{name}_{s}.json").read_text(encoding="utf-8")) for s in have}
    META = {s: json.loads((C2D / f"pilot_{name}_{s}.json").read_text(encoding="utf-8")) for s in have}
    z0 = Z[have[0]]; xy = z0["xy"].astype(float); area = z0["area"].astype(float); elev = z0["elev"].astype(float)
    tr = cKDTree(xy)
    masks = {}
    for h in HS:
        p = H.loc[h].geometry
        m = np.zeros(len(xy), bool); m[tr.query_ball_point((p.x, p.y), HS_R)] = True
        masks[f"{h}|200m"] = m
    union_key = f"{len(HS)} 个热点|200m"
    masks[union_key] = np.any([masks[f"{h}|200m"] for h in HS], axis=0)
    masks["全域|all"] = np.ones(len(xy), bool)
    res = {"name": name, "tag": t, "region": reg, "title": R["title"], "rain_mm": rain, "dur_h": dur, "sim_h": RG.end_h(t), "res_m": RG.RES_M, "states": have,
           "state_names": {s: RG.SNAME[s] for s in have}, "zones": {}, "deepest": {}, "balance": {}, "diffs": [],
           "koujing": {"水深": "三角单元形心水深（ANUGA stage − elevation）", "干湿": "< 0.02 m 算干",
                       "积水面积": "全程最大水深 ≥ 0.10 / ≥ 0.30 m 的单元面积之和（按三角单元真实面积）",
                       "最大积水量": "逐帧（每 5 分钟）Σ 水深 × 单元面积 的最大值，出现时刻为该帧时刻",
                       "热点 200 m": "离热点坐标 200 m 内的单元", "全域": f"二维计算域（客户汇水区图层 {R['feat']}）",
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
        dh = {h: float(np.hypot(px - H.loc[h].geometry.x, py - H.loc[h].geometry.y)) for h in HS}; nh = min(dh, key=dh.get)
        res["deepest"][s] = {"depth_m": round(float(hm[k]), 2), "x": round(float(px)), "y": round(float(py)), "ground_m": round(float(elev[k]), 2),
                             "min_ground_50m": round(float(elev[tr.query_ball_point((px, py), 50.0)].min()), 2),
                             "median_ground_200m": round(float(np.median(elev[tr.query_ball_point((px, py), 200.0)])), 2),
                             "near_hotspot": nh, "near_dist_m": round(dh[nh])}
        rpt = C2D / f"{name}_{s}_2way.rpt"
        nf = node_flooding(rpt); inside = META[s]["nodes"]
        fin = sum(v for n, v in nf.items() if n in inside); fout = sum(v for n, v in nf.items() if n not in inside)
        rc = routing_continuity(rpt); acc = TW[s]["acc_m3"]
        res["balance"][s] = {"1D→2D": round(acc["1to2"]), "2D→1D": round(acc["2to1"]), "直接落在二维的雨": round(acc.get("rain", 0)), "进塘（二维取水）": round(acc.get("pond", 0)),
                             f"域内留存（{RG.end_h(t):g} h 末）": round(TW[s]["final_vol_m3"]), "边界出流（余项）": round(TW[s]["boundary_out_m3"]),
                             "一维：域内井溢流（=交给二维）": round(fin), "一维：域外节点溢流（只算一维，记为损失）": round(fout),
                             "一维：外部入流（净雨）": round(rc.get("External Inflow", 0)), "一维：出水口出流": round(rc.get("External Outflow", 0)),
                             "一维：连续性误差 %": rc.get("Continuity Error (%)"), "wall_h": round(TW[s]["wall_s"] / 3600, 2)}
    for s1, s2 in PAIRS:
        dh = Z[s2]["hmax"].astype(float) - Z[s1]["hmax"].astype(float)
        fn = f"diff_{s2}_minus_{s1}.png"
        try:
            b = render_diff(xy, dh, float(RG.RES_M), D / fn)
        except Exception as e:                      # 两态完全相同（YAS / BAT 区内没有塘）时差值全 0，画不出图不算失败
            print(f"  差值图 {fn} 没画出来：{e}"); b = None; fn = None
        res["diffs"].append({"a": s1, "b": s2, "png": fn, "bounds": b, "shallower_ha": round(float(area[dh <= -0.05].sum()) / 1e4, 2),
                             "deeper_ha": round(float(area[dh >= 0.05].sum()) / 1e4, 2), "max_abs_diff_m": round(float(np.abs(dh).max()), 3)})
    # ---- 交付目录
    DD = ROOT / "out" / "交付" / f"{R['dir']}_模型版_{rain}mm{dur}h"
    done, miss = [], []
    cp(RG.CASES / f"{reg}_domain.geojson", DD / "公共" / "计算域.geojson", done, miss)
    cp(RG.CASES / f"{reg}_existing_pumps.json", DD / "公共" / "现状泵站.json", done, miss)
    cp(RG.CASES / f"{reg}_ponds.json", DD / "公共" / "已建成塘.json", done, miss)
    (DD / "公共").mkdir(parents=True, exist_ok=True)
    H.loc[HS].reset_index()[["hotspot_id", "area", "criticality", "location", "geometry"]].to_crs(4326).to_file(DD / "公共" / "热点.geojson", driver="GeoJSON")
    W = RG.P.WORK
    for s in have:
        d = DD / RG.DIRS[s]
        cp(W / f"{name}_{s}.inp", d / "一维" / f"{name}_{s}.inp", done, miss)
        cp(C2D / f"{name}_{s}_2way.rpt", d / "一维" / f"{name}_{s}_一维报告.rpt", done, miss)
        cp(C2D / f"twoway_{name}_{s}.json", d / "二维" / f"{s}_耦合运行记录.json", done, miss)
        for f in sorted((C2D / "tif").glob(f"{name}_{s}_depth_*")):
            cp(f, d / "二维" / f.name.replace("_depth_max", "_最大水深").replace("_depth_frames", "_逐帧水深"), done, miss)
        cp(C2D / "anuga_out" / f"{name}_{s}.sww", d / "二维" / f"{name}_{s}.sww", done, miss)
    for dd in res["diffs"]:
        if dd["png"]:
            cp(D / dd["png"], DD / "对比_缺口分析" / dd["png"], done, miss)
    (DD / "对比_缺口分析").mkdir(parents=True, exist_ok=True)
    rows = [["区域", "口径", "指标"] + [RG.SNAME[s] for s in have]]
    for key, v in res["zones"].items():
        zn, kind = key.split("|")
        for met, lab in (("max_depth_m", "最大水深 m"), ("area_ge_0p10_ha", "积水面积 ≥0.10 m（ha）"), ("area_ge_0p30_ha", "积水面积 ≥0.30 m（ha）"),
                         ("peak_vol_m3", "最大积水量 m³"), ("peak_vol_t_h", "最大积水量出现时刻 h")):
            rows.append([zn, {"200m": "热点 200 m", "all": "全域"}[kind], lab] + [v[s][met] for s in have])
    for k2 in res["balance"][have[0]]:
        rows.append(["全域", "水量对账 m³", k2] + [res["balance"][s][k2] for s in have])
    for f in (ROOT / "out" / f"rg_stats_{t}.csv", DD / "对比_缺口分析" / "统计值.csv"):
        with open(f, "w", newline="", encoding="utf-8-sig") as fh:
            csv.writer(fh).writerows(rows)
    inp = json.loads((ROOT / "out" / f"rg_inputs_{t}.json").read_text(encoding="utf-8"))
    ponds = inp.get("ponds") or []
    G = res["zones"][union_key]; A = res["zones"]["全域|all"]
    (DD / "计算设置.md").write_text("\n".join([
        f"# {R['title']} · {rain} mm / {dur} h · 计算设置", "",
        "| 项 | 设置 |", "|---|---|",
        f"| 情景 | {rain} mm / {dur} h，雨型取客户 Mussafah 模型里同历时的设计雨型、等比放大；模拟 {RG.end_h(t):g} h（历时 + 4 h） |",
        f"| 计算域 | 客户汇水区图层 {R['feat']}（{R['kmz']}），{inp['domain_km2']} km²，不外扩；四周自由出流，域外地表来水不算 |",
        f"| 热点 | {'、'.join(HS)} |",
        f"| 一维 | EPA SWMM 5.2（anuga-swmm 镜像），全市一维（客户 GDB 重建）按计算域截取：触及计算域的连通块整块取（{inp['n_components']} 块、{inp['n_nodes']:,} 个节点，"
        f"其中域内 {inp['n_nodes_in_domain']:,}），{inp['n_pipes']:,} 根管、{inp['n_outfalls']} 个出水口；动力波，地面积水关（冒出的水交给二维） |",
        f"| 产流 | SCS CN {inp['cn']:g}、Tc {inp['tc_min']:g} min（全市值；客户没有给这个区域的参数），净雨 {inp['rain_excess_mm']} mm，按管网节点服务面积（服务半径 {inp['R_served_m']} m）注入一维；"
        "离最近井超过服务半径的地面，净雨直接下在二维上 |",
        f"| 二维 | ANUGA，非结构三角网，单元面积上限 {RG.RES_M}²/3 m²（约 {inp['n_tri_est']:,} 个），地形 = DTM 原值（{inp['dtm_res_m']:g} m），曼宁 n = 0.030 |",
        "| 耦合 | couple2way_v2，同进程逐步双向，交换步 60 s；计算域内的井参与交换（井盖孔口 Cd 0.6、DN600），冒出的水分摊到井周 4 个单元 |",
        f"| L1 | 既有管网 + 计算域内的现状泵站 {len(inp['pumps'])} 座" + (f"；没建的泵：{'；'.join(inp['pump_limits'])}" if inp["pump_limits"] else "") + " |",
        "| L2 | L1 + 已建成塘（全市 L2 的接法：深 0.30 m 的平底蓄水节点，进水管带拍门，塘占地内的地面水直接进塘，塘满为止）"
        + ("；" + "、".join(f"{p['unitid']}（模型库容 {p['vol_model_m3']:,.0f} m³）" for p in ponds) if ponds else "；**计算域内没有已建成塘，L2 的模型与 L1 相同**") + " |",
        "| 输出 | 二维每 5 分钟一帧（形心水深、x / y 单宽流量）；.sww 为 UTM 40N（EPSG:32640） |",
        "", "差值只归因于塘：两态同一张管网、同一场雨、同一计算域。", ""]), encoding="utf-8")
    lines = [f"# {R['title']} · {rain} mm / {dur} h · L1 / L2 结果（15 m 网格）", "",
             f"| 态 | 目录 | {len(HS)} 个热点 200 m 内最大水深 | ≥0.10 m 面积（热点 200 m） | ≥0.30 m 面积（全域） | 最大积水量（全域） | 一维溢流（域内） |",
             "|---|---|---|---|---|---|---|"]
    for s in have:
        b = res["balance"][s]
        lines.append(f"| {RG.SNAME[s]} | `{RG.DIRS[s]}/` | {G[s]['max_depth_m']} m | {G[s]['area_ge_0p10_ha']} ha | {A[s]['area_ge_0p30_ha']} ha | "
                     f"{A[s]['peak_vol_m3']:,} m³ | {b['一维：域内井溢流（=交给二维）']:,} m³ |")
    lines += ["", ("L2 比 L1 多 " + "、".join(p["unitid"] for p in ponds) + "。") if ponds else "计算域内没有已建成塘，L2 的模型与 L1 相同，两态结果一致。",
              "", "完整指标与水量对账见 `对比_缺口分析/统计值.csv`；输入参数见 `计算设置.md`。"]
    if miss:
        lines += ["", "**缺的文件**（后处理时不在）：", *[f"- `{m}`" for m in miss]]
    (DD / "00_说明.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    res["delivery"] = {"dir": str(DD), "n_files": len(done), "missing": miss}
    (D / "rg_stats.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    RG.post_out(t).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  → {RG.post_out(t).name}、{D.name}/rg_stats.json；交付 {DD}（{len(done)} 个文件" + (f"，缺 {len(miss)} 个" if miss else "") + "）")
    for s in have:
        dp = res["deepest"][s]
        print(f"    {RG.SNAME[s]:<4} 热点 200 m 内最深 {G[s]['max_depth_m']} m，≥0.10 m {G[s]['area_ge_0p10_ha']} ha；全域最深 {dp['depth_m']} m @ ({dp['x']},{dp['y']}) "
              f"离 {dp['near_hotspot']} {dp['near_dist_m']} m；一维连续性误差 {res['balance'][s]['一维：连续性误差 %']} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
