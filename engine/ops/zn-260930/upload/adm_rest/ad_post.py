# -*- coding: utf-8 -*-
"""ADM 汇水区 30 m · L1 / L2 后处理与交付（在 anuga-swmm 容器里跑，只用 numpy / scipy / rasterio / pyproj；2026-10-15）。

做什么（一个情景 = 一个区域 × 61 mm / 1 h，两个态）：
  1. 积水点结果表：计算域内每个积水点（ADM 热点，cases/ad/hotspots_ad.json）× 每个态一行——离该点 200 m 内的最大水深、最近单元水深、
     ≥0.10 / ≥0.30 m 积水面积、最大积水量及出现时刻、水深 ≥ 0.10 m 的起始时刻与持续时间、是否积水（最大水深 ≥ 0.30 m）；L1 → L2 的水深变化
  2. 积水点图层：积水点位置 + 上表指标（GeoJSON，WGS84；L1 / L2 的指标分列）
  3. 汇水区汇总：全域最大水深、积水面积、最大积水量、最深点离最近积水点多远；一维溢流与水量对账（SWMM 报告）
  4. 最大水深 GeoTIFF（30 m，UTM 40N）、.sww（从 /work/c2d/anuga_out 直接搬到交付目录，本地不留）、一维 .inp / 报告、耦合运行记录、计算域 / 泵站 / 塘
  交付目录直接写在 NAS：$AD_RESULT_ROOT/$AD_HOST/<区域目录>_61mm1h_30m/（不用再同步）。
  标记文件 /work/c2d/ad_stats_<情景>.json 最后写（调度器据此认为本步完成）。

用法（容器内）：python /work/c2d/ad_post.py --tag RDA_M61H1 --res 30
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import re
import shutil
import sys
import time

import numpy as np

W = pathlib.Path("/work"); C2D = W / "c2d"
TH = (0.10, 0.30)          # 积水阈值 m
HS_R = 200.0               # 积水点影响半径 m
DRY = 0.02
SNAME = {"L1": "L1", "L2": "L2"}
DIRS = {"L1": "L1_既有管网", "L2": "L2_已建成调蓄"}


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


def cp(src: pathlib.Path, dst: pathlib.Path, done: list, miss: list, move: bool = False) -> None:
    if not src.exists():
        miss.append(str(src)); return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(src), str(dst))
    else:
        shutil.copy2(src, dst)
    done.append(dst)


def write_csv(path: pathlib.Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerows(rows)


def write_xlsx(path: pathlib.Path, rows: list) -> bool:
    try:
        import openpyxl
    except ImportError:
        return False
    wb = openpyxl.Workbook(); ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)
    return True


def max_depth_tif(z, res: float, out: pathlib.Path) -> None:
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_origin
    pd = os.path.join(os.path.dirname(__import__("importlib.util").util.find_spec("rasterio").origin), "proj_data")
    os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = pd
    xy = z["xy"].astype(float); hm = z["hmax"].astype(float)
    x0 = np.floor(xy[:, 0].min() / res) * res; y1 = np.ceil(xy[:, 1].max() / res) * res
    nx = int(np.ceil((xy[:, 0].max() - x0) / res)) + 1; ny = int(np.ceil((y1 - xy[:, 1].min()) / res)) + 1
    col = np.clip(((xy[:, 0] - x0) / res).astype(int), 0, nx - 1); row = np.clip(((y1 - xy[:, 1]) / res).astype(int), 0, ny - 1)
    idx = row * nx + col
    inside = np.zeros(nx * ny, bool); inside[idx] = True
    g = np.zeros(nx * ny, np.float32)
    np.maximum.at(g, idx, np.where(hm >= DRY, hm, 0.0).astype(np.float32))
    g[~inside] = -9999.0
    with rasterio.open(out, "w", driver="GTiff", width=nx, height=ny, count=1, dtype="float32", crs=CRS.from_epsg(32640),
                       transform=from_origin(x0, y1, res, res), nodata=-9999.0, compress="deflate") as ds:
        ds.write(g.reshape(ny, nx), 1)
        ds.set_band_description(1, "全程最大水深 m（0 = 干，< 0.02 m 记 0；nodata = 计算域外）")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", required=True); ap.add_argument("--res", type=float, default=30.0)
    a = ap.parse_args()
    from scipy.spatial import cKDTree
    t0 = time.time()
    t = a.tag; reg = t.split("_")[0]; name = f"AD_{t}"
    host = os.environ.get("AD_HOST") or "unknown"
    root = pathlib.Path(os.environ.get("AD_RESULT_ROOT", "/media/pop-fs/flood_data/result"))
    cases = C2D / "ad_cases"
    meta_all = json.loads((cases / "hotspots_ad.json").read_text(encoding="utf-8"))
    RG = meta_all["regions"][reg]
    HS = [h for h in meta_all["hotspots"] if h["region"] == reg]
    inp = json.loads((C2D / f"ad_inputs_{t}.json").read_text(encoding="utf-8"))
    rain, dur = inp["rain_mm"], inp["dur_h"]
    states = [s for s in ("L1", "L2") if (C2D / f"twoway_{name}_{s}.json").exists() and (C2D / f"{name}_{s}_2d.npz").exists()]
    if len(states) < 2:
        raise SystemExit(f"{name}：{[s for s in ('L1', 'L2') if s not in states]} 还没算完")
    DD = root / host / f"{RG['dir']}_{rain:g}mm{dur:g}h_{a.res:g}m"
    DD.mkdir(parents=True, exist_ok=True)
    print(f"{name}：积水点 {len(HS)} 个；交付目录 {DD}", flush=True)
    Z = {s: np.load(C2D / f"{name}_{s}_2d.npz") for s in states}
    TW = {s: json.loads((C2D / f"twoway_{name}_{s}.json").read_text(encoding="utf-8")) for s in states}
    META = {s: json.loads((C2D / f"pilot_{name}_{s}.json").read_text(encoding="utf-8")) for s in states}
    z0 = Z[states[0]]; xy = z0["xy"].astype(float); area = z0["area"].astype(float); elev = z0["elev"].astype(float)
    tr = cKDTree(xy)
    # ---- 积水点：每个点周围 200 m 内的单元
    cells = {}
    for h in HS:
        idx = np.array(tr.query_ball_point((h["x"], h["y"]), HS_R), dtype=np.int64)
        d0, i0 = tr.query((h["x"], h["y"]))
        cells[h["hotspot_id"]] = (idx, int(i0), float(d0))
    res_hs = {s: {} for s in states}; zones = {}; deepest = {}; balance = {}
    for s in states:
        z = Z[s]; hm = z["hmax"].astype(float); fr = z["frames"]; th = np.asarray(z["t_h"], float)
        dt_min = float(np.median(np.diff(th)) * 60.0) if len(th) > 1 else 5.0
        for h in HS:
            idx, i0, d0 = cells[h["hotspot_id"]]
            if len(idx) == 0:
                idx = np.array([i0])
            ts = np.asarray(fr[:, idx], dtype=np.float32)                       # (帧, 单元)
            top = ts.max(axis=1); vol = (ts.astype(np.float64) * area[idx]).sum(axis=1)
            wet = np.flatnonzero(top >= TH[0])
            res_hs[s][h["hotspot_id"]] = {
                "max_depth_200m": round(float(hm[idx].max()), 3), "nearest_cell_depth": round(float(hm[i0]), 3), "nearest_cell_dist_m": round(d0, 1),
                "area_ge_0p10_ha": round(float(area[idx][hm[idx] >= TH[0]].sum()) / 1e4, 3), "area_ge_0p30_ha": round(float(area[idx][hm[idx] >= TH[1]].sum()) / 1e4, 3),
                "peak_vol_m3": round(float(vol.max())), "t_peak_h": round(float(th[int(top.argmax())]), 2),
                "t_first_ge_0p10_h": round(float(th[wet[0]]), 2) if len(wet) else None, "duration_ge_0p10_min": round(len(wet) * dt_min) if len(wet) else 0,
                "n_cells_200m": int(len(idx))}
        allv = np.zeros(len(th))
        for i in range(len(th)):                                               # 逐帧 Σ 水深 × 面积（全域）
            allv[i] = float((np.asarray(fr[i], dtype=np.float64) * area).sum())
        ip = int(allv.argmax())
        zones[s] = {"max_depth_m": round(float(hm.max()), 3), "area_ge_0p10_ha": round(float(area[hm >= TH[0]].sum()) / 1e4, 2),
                    "area_ge_0p30_ha": round(float(area[hm >= TH[1]].sum()) / 1e4, 2), "peak_vol_m3": round(float(allv[ip])), "peak_vol_t_h": round(float(th[ip]), 2),
                    "domain_area_ha": round(float(area.sum()) / 1e4, 1), "n_cells": int(len(xy))}
        k = int(hm.argmax()); px, py = xy[k]
        near = min(HS, key=lambda h: (h["x"] - px) ** 2 + (h["y"] - py) ** 2) if HS else None
        deepest[s] = {"depth_m": round(float(hm[k]), 2), "x": round(float(px)), "y": round(float(py)), "ground_m": round(float(elev[k]), 2),
                      "near_hotspot": near["hotspot_id"] if near else None,
                      "near_dist_m": round(float(np.hypot(px - near["x"], py - near["y"]))) if near else None}
        rpt = C2D / f"{name}_{s}_2way.rpt"
        nf = node_flooding(rpt) if rpt.exists() else {}; inside = META[s]["nodes"]
        rc = routing_continuity(rpt) if rpt.exists() else {}; acc = TW[s]["acc_m3"]
        balance[s] = {"1D→2D": round(acc["1to2"]), "2D→1D": round(acc["2to1"]), "直接落在二维的雨": round(acc.get("rain", 0)), "进塘（二维取水）": round(acc.get("pond", 0)),
                      f"域内留存（{inp['sim_h']:g} h 末）": round(TW[s]["final_vol_m3"]), "边界出流（余项）": round(TW[s]["boundary_out_m3"]),
                      "一维：域内井溢流（=交给二维）": round(sum(v for n, v in nf.items() if n in inside)),
                      "一维：域外节点溢流（只算一维，记为损失）": round(sum(v for n, v in nf.items() if n not in inside)),
                      "一维：外部入流（净雨）": round(rc.get("External Inflow", 0)), "一维：出水口出流": round(rc.get("External Outflow", 0)),
                      "一维：连续性误差 %": rc.get("Continuity Error (%)"), "wall_h": round(TW[s]["wall_s"] / 3600, 2)}
        print(f"  {s}: 全域最深 {zones[s]['max_depth_m']} m，≥0.30 m {zones[s]['area_ge_0p30_ha']} ha；积水点 200 m 内最深 "
              f"{max([v['max_depth_200m'] for v in res_hs[s].values()] or [0])} m（{time.time() - t0:.0f} s）", flush=True)
    # ---- 积水点结果表 + 图层
    def flag(d):
        return "积水（≥0.30 m）" if d >= TH[1] else ("浅积水（0.10–0.30 m）" if d >= TH[0] else "否")
    head = ["积水点", "汇水区", "ADM 序号", "所属片区", "位置", "重要性", "x (EPSG:32640)", "y (EPSG:32640)"]
    for s in states:
        head += [f"{s} 200 m 内最大水深 m", f"{s} 最近单元水深 m", f"{s} 积水面积 ≥0.10 m (ha)", f"{s} 积水面积 ≥0.30 m (ha)", f"{s} 最大积水量 m³",
                 f"{s} 最大水深时刻 h", f"{s} 水深≥0.10 m 起始 h", f"{s} 水深≥0.10 m 持续 min", f"{s} 是否积水"]
    head += ["L2−L1 最大水深变化 m"]
    rows = [head]; feats = []
    for h in sorted(HS, key=lambda q: (q["criticality"] != "Very High", q["criticality"] != "High", int(q["hotspot_id"].split("-")[1]) if q["hotspot_id"].split("-")[1].isdigit() else 0)):
        hid = h["hotspot_id"]; r = [hid, h["catchment"], h["adm"], h["area"], h["location"], h["criticality"], h["x"], h["y"]]; props = dict(hotspot_id=hid, catchment=h["catchment"], area=h["area"], location=h["location"], criticality=h["criticality"])
        for s in states:
            v = res_hs[s][hid]
            r += [v["max_depth_200m"], v["nearest_cell_depth"], v["area_ge_0p10_ha"], v["area_ge_0p30_ha"], v["peak_vol_m3"], v["t_peak_h"], v["t_first_ge_0p10_h"], v["duration_ge_0p10_min"], flag(v["max_depth_200m"])]
            props.update({f"{s}_max_depth_m": v["max_depth_200m"], f"{s}_nearest_depth_m": v["nearest_cell_depth"], f"{s}_area_ge_0p10_ha": v["area_ge_0p10_ha"], f"{s}_area_ge_0p30_ha": v["area_ge_0p30_ha"],
                          f"{s}_peak_vol_m3": v["peak_vol_m3"], f"{s}_t_peak_h": v["t_peak_h"], f"{s}_t_first_ge_0p10_h": v["t_first_ge_0p10_h"], f"{s}_dur_ge_0p10_min": v["duration_ge_0p10_min"], f"{s}_flooded": flag(v["max_depth_200m"])})
        dd = round(res_hs["L2"][hid]["max_depth_200m"] - res_hs["L1"][hid]["max_depth_200m"], 3); r.append(dd); props["L2_minus_L1_m"] = dd
        rows.append(r)
        from pyproj import Transformer
        lon, lat = Transformer.from_crs(32640, 4326, always_xy=True).transform(h["x"], h["y"])
        feats.append({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}})
    HSd = DD / "积水点"; HSd.mkdir(parents=True, exist_ok=True)
    write_csv(HSd / "积水点结果表.csv", rows); x_ok = write_xlsx(HSd / "积水点结果表.xlsx", rows)
    (HSd / "积水点图层.geojson").write_text(json.dumps({"type": "FeatureCollection", "name": f"{RG['feat']}_积水点", "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
                                                        "features": feats}, ensure_ascii=False), encoding="utf-8")
    # ---- 汇水区汇总
    srows = [["区域", "指标"] + [SNAME[s] for s in states]]
    for met, lab in (("max_depth_m", "全域最大水深 m"), ("area_ge_0p10_ha", "积水面积 ≥0.10 m（ha）"), ("area_ge_0p30_ha", "积水面积 ≥0.30 m（ha）"), ("peak_vol_m3", "最大积水量 m³"), ("peak_vol_t_h", "最大积水量出现时刻 h"), ("domain_area_ha", "二维计算域面积 ha")):
        srows.append([RG["feat"], lab] + [zones[s][met] for s in states])
    n_f = {s: sum(1 for v in res_hs[s].values() if v["max_depth_200m"] >= TH[1]) for s in states}
    n_s = {s: sum(1 for v in res_hs[s].values() if TH[0] <= v["max_depth_200m"] < TH[1]) for s in states}
    srows += [[RG["feat"], f"积水点总数"] + [len(HS)] * len(states), [RG["feat"], "积水点：积水（≥0.30 m）个数"] + [n_f[s] for s in states], [RG["feat"], "积水点：浅积水（0.10–0.30 m）个数"] + [n_s[s] for s in states]]
    for s_lab, key in (("最深点水深 m", "depth_m"), ("最深点离最近积水点 m", "near_dist_m")):
        srows.append([RG["feat"], s_lab] + [deepest[s][key] for s in states])
    srows.append([RG["feat"], "最深点最近的积水点"] + [deepest[s]["near_hotspot"] for s in states])
    for k2 in balance[states[0]]:
        srows.append(["全域", f"水量对账 m³：{k2}"] + [balance[s][k2] for s in states])
    write_csv(DD / "统计" / "汇水区汇总.csv", srows)
    # ---- 文件交付
    done, miss = [], []
    cp(cases / f"{reg}_domain.geojson", DD / "公共" / "计算域.geojson", done, miss)
    cp(cases / f"{reg}_existing_pumps.json", DD / "公共" / "现状泵站.json", done, miss)
    cp(cases / f"{reg}_ponds.json", DD / "公共" / "已建成塘.json", done, miss)
    (DD / "公共").mkdir(parents=True, exist_ok=True)
    (DD / "公共" / "积水点.json").write_text(json.dumps(HS, ensure_ascii=False, indent=0), encoding="utf-8")
    for s in states:
        d = DD / DIRS[s]
        cp(W / f"{name}_{s}.inp", d / "一维" / f"{name}_{s}.inp", done, miss)
        cp(C2D / f"{name}_{s}_2way.rpt", d / "一维" / f"{name}_{s}_一维报告.rpt", done, miss)
        cp(C2D / f"twoway_{name}_{s}.json", d / "二维" / f"{s}_耦合运行记录.json", done, miss)
        max_depth_tif(Z[s], a.res, d / "二维" / f"{name}_{s}_最大水深.tif"); done.append(d / "二维" / f"{name}_{s}_最大水深.tif")
        cp(C2D / "anuga_out" / f"{name}_{s}.sww", d / "二维" / f"{name}_{s}.sww", done, miss, move=True)
        cp(C2D / f"{name}_{s}_2d.npz", d / "二维" / f"{name}_{s}_2d.npz", done, miss)      # flood-aio 登记（sim_c1）读它
    ponds = json.loads((cases / f"{reg}_ponds.json").read_text(encoding="utf-8")).get("ponds") or []
    (DD / "计算设置.md").write_text("\n".join([
        f"# {RG['title']} · {rain:g} mm / {dur:g} h · {a.res:g} m · 计算设置", "", "| 项 | 设置 |", "|---|---|",
        f"| 情景 | {rain:g} mm / {dur:g} h（客户 ADM 汇水区计划表），雨型取客户 Mussafah 模型里 {dur:g} h 的设计雨型、等比放大；模拟 {inp['sim_h']:g} h（历时 + 4 h） |",
        f"| 计算域 | 客户汇水区图层 {RG['feat']}（{RG['kmz']}），{inp['domain_km2']} km²（客户地图 {RG['km2']} km²），不外扩；四周自由出流，域外地表来水不算 |",
        f"| 积水点 | ADM 热点中落在计算域内的 {len(HS)} 个（公共/积水点.json） |",
        f"| 一维 | EPA SWMM 5.2，全市一维（客户 GDB 重建）按计算域截取：触及计算域的连通块整块取（{inp['n_components']} 块、{inp['n_nodes']:,} 个节点，其中域内 {inp['n_nodes_in_domain']:,}），{inp['n_pipes']:,} 根管、{inp['n_outfalls']} 个出水口；动力波，地面积水关（冒出的水交给二维） |",
        f"| 产流 | SCS CN {inp['cn']:g}、Tc {inp['tc_min']:g} min（全市值；客户没给这个区域的参数），净雨 {inp['rain_excess_mm']} mm，按管网节点服务面积（服务半径 {inp['R_served_m']} m）注入一维；离最近井超过服务半径的地面，净雨直接下在二维上 |",
        f"| 二维 | ANUGA，非结构三角网，单元面积上限 {a.res:g}²/3 m²（约 {inp['n_tri_est']:,} 个），地形 = DTM（{inp['dtm_res_m']:g} m）先按 {a.res:g} m 取块平均，曼宁 n = 0.030 |",
        "| 耦合 | couple2way_v2，同进程逐步双向，交换步 60 s；计算域内的井参与交换（井盖孔口 Cd 0.6、DN600），冒出的水分摊到井周 4 个单元 |",
        f"| L1 | 既有管网 + 计算域内现状泵站 {len(inp['pumps'])} 座" + (f"；没建的泵：{'；'.join(inp['pump_limits'])}" if inp["pump_limits"] else "") + " |",
        "| L2 | L1 + 已建成塘（全市 L2 的接法：平底蓄水节点，进水管带拍门，塘占地内的地面水直接进塘）"
        + ("；" + "、".join(f"{p['unitid']}（模型库容 {p['vol_model_m3']:,.0f} m³）" for p in ponds) if ponds else "；**计算域内没有可建的已建成塘，L2 的模型与 L1 相同**") + " |",
        "| 积水点指标 | 离积水点坐标 200 m 内的单元：最大水深、积水面积（≥0.10 / ≥0.30 m）、最大积水量；网格单元较粗时（30 m：200 m 内只有几十个单元）同时给出最近单元水深 |",
        "| 输出 | 二维每 5 分钟一帧；.sww 与最大水深 GeoTIFF 为 UTM 40N（EPSG:32640）；shp + dat 在 shp_dat/ 目录（由服务器脚本转换） |", ""]), encoding="utf-8")
    lines = [f"# {RG['title']} · {rain:g} mm / {dur:g} h · L1 / L2 结果（{a.res:g} m 网格）", "",
             f"| 态 | 目录 | 全域最大水深 | ≥0.30 m 面积 | 最大积水量 | 积水点（{len(HS)} 个）中积水 / 浅积水 | 一维溢流（域内） |", "|---|---|---|---|---|---|---|"]
    for s in states:
        lines.append(f"| {SNAME[s]} | `{DIRS[s]}/` | {zones[s]['max_depth_m']} m | {zones[s]['area_ge_0p30_ha']} ha | {zones[s]['peak_vol_m3']:,} m³ | {n_f[s]} / {n_s[s]} | {balance[s]['一维：域内井溢流（=交给二维）']:,} m³ |")
    lines += ["", ("L2 比 L1 多 " + "、".join(p["unitid"] for p in ponds) + "。") if ponds else "计算域内没有可建的已建成塘，L2 的模型与 L1 相同，两态结果一致。",
              "", "目录：`积水点/`（积水点结果表 csv" + ("/xlsx" if x_ok else "") + "、积水点图层 geojson）、`统计/汇水区汇总.csv`、各态 `一维/` `二维/`（含 .sww、最大水深 tif）、`公共/`、`计算设置.md`。"]
    if miss:
        lines += ["", "**缺的文件**（后处理时不在）：", *[f"- `{m}`" for m in miss]]
    (DD / "00_说明.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    res = {"name": name, "tag": t, "region": reg, "title": RG["title"], "rain_mm": rain, "dur_h": dur, "sim_h": inp["sim_h"], "res_m": a.res, "states": states, "zones": zones, "deepest": deepest,
           "balance": balance, "hotspots": {s: res_hs[s] for s in states}, "n_hotspots": len(HS), "delivery": {"dir": str(DD), "n_files": len(done), "missing": miss},
           "sww": {s: str(DD / DIRS[s] / "二维" / f"{name}_{s}.sww") for s in states}, "wall_post_s": round(time.time() - t0)}
    (DD / "统计" / "ad_stats.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (C2D / f"ad_stats_{t}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")      # 标记文件，最后写
    print(f"  → {DD}（{len(done)} 个文件" + (f"，缺 {len(miss)} 个" if miss else "") + f"）；{time.time() - t0:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
