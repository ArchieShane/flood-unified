# -*- coding: utf-8 -*-
"""全市 250 m 一场雨 L1 / L2 的后处理与交付目录（tools/city_run_250m.py 的 post:<情景> 最后一步，2026-10-14）。

前面几步已做好：逐帧图 / manifest（pilot_frames.py → console/data/flood2d_ct_<情景>/）、GeoTIFF（npz_to_tif.py → $WORK/c2d/tif/）、
.sww（容器里 npz_to_sww.py → $WORK/c2d/anuga_out/）。这里做（同 rg_post.py，全域一个口径，不分热点）：
  1. 统计：全域各态最大水深、积水面积（≥ 0.10 / ≥ 0.30 m）、最大积水量与出现时刻；最深点
  2. 水量对账：二维（1D→2D、2D→1D、直接下在二维的雨、进塘、边界出流、域内留存）与一维（SWMM 连续性、溢流）
  3. 差值图：L2 − L1
  4. 交付目录 out/交付/全市_模型版_<雨量>mm<历时>h_250m/（只复制，不改原始结果）
口径同 C1：形心水深，< 0.02 m 算干，面积按三角单元真实面积加权，最大积水量 = 逐帧 Σ 水深 × 面积 的最大值。
用法：python tools/ct_post.py --tag CITY_M61H2
产物：out/ct_stats_<情景>.json / .csv、console/data/flood2d_ct_<情景>/diff_*.png、交付目录
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
import city_run_250m as CT  # noqa: E402

C2D = CT.P.C2D
TH = (0.10, 0.30)


def routing_continuity(rpt: pathlib.Path) -> dict:
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
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", required=True, choices=list(CT.SCEN))
    a = ap.parse_args(argv)
    from city2d_post import render_diff
    t = a.tag; name = CT.name_of(t); rain, dur = CT.SCEN[t]
    have = [s for s in CT.STATES if (C2D / f"twoway_{name}_{s}.json").exists() and (C2D / f"{name}_{s}_2d.npz").exists()]
    if len(have) < len(CT.STATES):
        raise SystemExit(f"{name}：{[s for s in CT.STATES if s not in have]} 还没算完，两个态齐了再做后处理")
    D = ROOT / "console" / "data" / f"flood2d_ct_{t.lower()}"; D.mkdir(parents=True, exist_ok=True)
    Z = {s: np.load(C2D / f"{name}_{s}_2d.npz") for s in have}
    TW = {s: json.loads((C2D / f"twoway_{name}_{s}.json").read_text(encoding="utf-8")) for s in have}
    META = {s: json.loads((C2D / f"pilot_{name}_{s}.json").read_text(encoding="utf-8")) for s in have}
    inp = json.loads((ROOT / "out" / f"ct_inputs_{t}.json").read_text(encoding="utf-8"))
    z0 = Z[have[0]]; xy = z0["xy"].astype(float); area = z0["area"].astype(float); elev = z0["elev"].astype(float)
    res = {"name": name, "tag": t, "title": "Abu Dhabi 全市", "rain_mm": rain, "dur_h": dur, "sim_h": CT.end_h(t), "res_m": CT.RES_M, "runoff": inp["runoff"],
           "states": have, "all": {}, "deepest": {}, "balance": {}, "diffs": [],
           "koujing": {"水深": "三角单元形心水深（ANUGA stage − elevation）", "干湿": "< 0.02 m 算干",
                       "积水面积": "全程最大水深 ≥ 0.10 / ≥ 0.30 m 的单元面积之和（按三角单元真实面积）",
                       "最大积水量": "逐帧（每 5 分钟）Σ 水深 × 单元面积 的最大值",
                       "地形": "50 cm DTM 先按 250 m 取块平均再赋给三角单元：有效分辨率 250 m，小尺度起伏（窄沟、路缘石、小凹地）被抹平"}}
    for s in have:
        z = Z[s]; hm = z["hmax"].astype(float); fr = z["frames"]; th = z["t_h"]
        vols = np.array([float((fr[i].astype(float) * area).sum()) for i in range(len(th))])
        i = int(vols.argmax()) if len(vols) else 0
        res["all"][s] = {"max_depth_m": round(float(hm.max()), 3), "area_ge_0p10_ha": round(float(area[hm >= TH[0]].sum()) / 1e4, 2),
                         "area_ge_0p30_ha": round(float(area[hm >= TH[1]].sum()) / 1e4, 2), "peak_vol_m3": round(float(vols[i])) if len(vols) else 0,
                         "peak_vol_t_h": round(float(th[i]), 2) if len(th) else None, "zone_area_ha": round(float(area.sum()) / 1e4, 1)}
        k = int(hm.argmax()); res["deepest"][s] = {"depth_m": round(float(hm[k]), 2), "x": round(float(xy[k][0])), "y": round(float(xy[k][1])), "ground_m": round(float(elev[k]), 2)}
        rpt = C2D / f"{name}_{s}_2way.rpt"
        nf = node_flooding(rpt); inside = META[s]["nodes"]
        fin = sum(v for n, v in nf.items() if n in inside); fout = sum(v for n, v in nf.items() if n not in inside)
        rc = routing_continuity(rpt); acc = TW[s]["acc_m3"]
        res["balance"][s] = {"1D→2D": round(acc["1to2"]), "2D→1D": round(acc["2to1"]), "直接落在二维的雨": round(acc.get("rain", 0)), "进塘（二维取水）": round(acc.get("pond", 0)),
                             f"域内留存（{CT.end_h(t):g} h 末）": round(TW[s]["final_vol_m3"]), "边界出流（余项）": round(TW[s]["boundary_out_m3"]),
                             "一维：域内井溢流（=交给二维）": round(fin), "一维：域外节点溢流（只算一维，记为损失）": round(fout),
                             "一维：外部入流（净雨）": round(rc.get("External Inflow", 0)), "一维：出水口出流": round(rc.get("External Outflow", 0)),
                             "一维：连续性误差 %": rc.get("Continuity Error (%)"), "wall_h": round(TW[s]["wall_s"] / 3600, 2)}
    for s1, s2 in (("L1", "L2"),):
        dh = Z[s2]["hmax"].astype(float) - Z[s1]["hmax"].astype(float)
        fn = f"diff_{s2}_minus_{s1}.png"
        try:
            b = render_diff(xy, dh, float(CT.RES_M), D / fn)
        except Exception as e:
            print(f"  差值图 {fn} 没画出来：{e}"); b = None; fn = None
        res["diffs"].append({"a": s1, "b": s2, "png": fn, "bounds": b, "shallower_ha": round(float(area[dh <= -0.05].sum()) / 1e4, 2),
                             "deeper_ha": round(float(area[dh >= 0.05].sum()) / 1e4, 2), "max_abs_diff_m": round(float(np.abs(dh).max()), 3)})
    DD = CT.deliv_dir(t); W = CT.P.WORK
    done, miss = [], []
    (DD / "公共").mkdir(parents=True, exist_ok=True)
    cp(ROOT / "out" / f"ct_inputs_{t}.json", DD / "公共" / "输入参数.json", done, miss)
    for s in have:
        d = DD / CT.DIRS[s]
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
    rows = [["口径", "指标"] + have]
    for met, lab in (("max_depth_m", "最大水深 m"), ("area_ge_0p10_ha", "积水面积 ≥0.10 m（ha）"), ("area_ge_0p30_ha", "积水面积 ≥0.30 m（ha）"),
                     ("peak_vol_m3", "最大积水量 m³"), ("peak_vol_t_h", "最大积水量出现时刻 h")):
        rows.append(["全域", lab] + [res["all"][s][met] for s in have])
    for k2 in res["balance"][have[0]]:
        rows.append(["水量对账 m³", k2] + [res["balance"][s][k2] for s in have])
    for f in (ROOT / "out" / f"ct_stats_{t}.csv", DD / "对比_缺口分析" / "统计值.csv"):
        with open(f, "w", newline="", encoding="utf-8-sig") as fh:
            csv.writer(fh).writerows(rows)
    ponds = inp.get("ponds") or []
    (DD / "计算设置.md").write_text("\n".join([
        f"# Abu Dhabi 全市 · {rain} mm / {dur} h · 250 m 网格 · 计算设置", "", "| 项 | 设置 |", "|---|---|",
        f"| 情景 | {rain} mm / {dur} h，雨型取客户 Mussafah 模型里同历时的设计雨型、等比放大；模拟 {CT.end_h(t):g} h（历时 + 4 h） |",
        f"| 计算域 | 管网服务范围 ∪ 23 座塘的地表汇水区，外扩 300 m，{inp['domain_km2']} km²；四周自由出流 |",
        f"| 一维 | EPA SWMM 5.2，全市一维（客户 GDB 重建）整网一个模型：{inp['n_nodes']:,} 个节点（域内 {inp['n_nodes_in_domain']:,}）、{inp['n_pipes']:,} 根管、{inp['n_outfalls']} 个出水口；动力波，地面积水关（冒出的水交给二维）；没有泵站 |",
        f"| 产流 | {'客户各子汇水区自己的面积 / CN / Tc 按 (CN, Tc) 分组注入，客户汇水区以外 CN 82、Tc 30 min' if inp['runoff'] == 'v2' else 'SCS CN 82、Tc 30 min（全市统一）'}；"
        f"服务半径 {inp['R_served_m']} m 以外的地面净雨按 CN 82（{inp['rain_excess_mm']} mm）直接下在二维上 |",
        f"| 二维 | ANUGA，非结构三角网，单元面积上限 {CT.RES_M}²/3 m²（约 {inp['n_tri_est']:,} 个），曼宁 n = 0.030 |",
        "| 地形 | 50 cm DTM 先按 250 m 取块平均再赋给三角单元：有效分辨率 250 m，小尺度起伏被抹平，积水深度 / 面积与 30 m、15 m、5 m 版不能直接比 |",
        "| 耦合 | couple2way_v2，同进程逐步双向，交换步 60 s；井盖孔口 Cd 0.6、DN600 |",
        "| L1 | 既有管网，没有塘 |",
        "| L2 | L1 + 已建成塘（深 0.30 m 的平底蓄水节点，进水管带拍门，塘占地内的地面水直接进塘；接法按位置与标高推断）：" + "、".join(f"{p['unitid']}" for p in ponds) + " |",
        "| 输出 | 二维每 5 分钟一帧；.sww 为 UTM 40N（EPSG:32640） |", "", "差值只归因于塘：两态同一张管网、同一场雨、同一计算域。", ""]), encoding="utf-8")
    A = res["all"]
    (DD / "00_说明.md").write_text("\n".join([
        f"# Abu Dhabi 全市 · {rain} mm / {dur} h · L1 / L2 结果（250 m 网格）", "",
        "| 态 | 目录 | 最大水深 | ≥0.10 m 面积 | ≥0.30 m 面积 | 最大积水量 | 一维溢流（域内） |", "|---|---|---|---|---|---|---|",
        *[f"| {s} | `{CT.DIRS[s]}/` | {A[s]['max_depth_m']} m | {A[s]['area_ge_0p10_ha']} ha | {A[s]['area_ge_0p30_ha']} ha | {A[s]['peak_vol_m3']:,} m³ | "
          f"{res['balance'][s]['一维：域内井溢流（=交给二维）']:,} m³ |" for s in have],
        "", "详细口径见 计算设置.md；统计与对账见 对比_缺口分析/统计值.csv。", ""]), encoding="utf-8")
    res["deliver_dir"] = str(DD); res["missing"] = miss
    CT.post_out(t).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (D / "ct_stats.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{name}：交付 {DD}（{len(done)} 个文件" + (f"，缺 {len(miss)} 个：{miss[:3]}" if miss else "") + "）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
