# -*- coding: utf-8 -*-
"""C1 一二维结果后处理（用户 2026-09-28 授权自动做）。每场雨一份，已算完的态都处理，没算完的跳过。

  1. 逐帧图、最大水深图、各态统计：tools/pilot_frames.py → console/data/flood2d_c1_r{61,96}/
  2. 热点统计：每个热点在「自己的地表 + 管网汇水区」内、以及「热点 200 m 内」的最大水深、积水面积（≥0.10 / ≥0.30 m）、
     最大积水量（逐帧 Σ 水深 × 面积 的最大值）与出现时刻
  3. 全域分两块：管网服务区内（离井 ≤ 服务半径）与直接降雨区（离井 > 服务半径，多为内陆空地）
  4. 最深点核查：位置、地面高程、周边 50 m 内最低地面、离最近热点多远
  5. 水量对账：1D→2D、2D→1D、二维直接降雨、进塘、边界出流、域内留存（twoway_*.json）
  6. 相邻两态的差值图（全程最大水深之差，|Δh| < 5 cm 不画）：L2−L1、L2P−L2、L2S−L2P、L2P−L1、L2S−L1
  7. 统计值表（每项带统计口径）：out/交付/C1/…/统计值.csv / .xlsx（交付目录由 export_delivery_c1.py 统一整理）
用法：python tools/c1_post.py [--rain 61|96]（默认两场都做）
产物：console/data/flood2d_c1_r{r}/{manifest,stats,c1_stats}.json、diff_*.png；out/c1_stats_R{r}.json、out/c1_stats_R{r}.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import subprocess
import sys

import importlib.util
import os

import numpy as np

# rasterio 要用自己带的 proj.db：机器上 PostgreSQL / PostGIS 的旧 proj.db 会先被找到（差值图投影时报 CRSError）
os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = os.path.join(os.path.dirname(importlib.util.find_spec("rasterio").origin), "proj_data")

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
STATES = ["L1", "L2", "L2P", "L2S", "L2S3"]
NAMES = {"L1": "L1", "L2": "L2", "L2P": "L2+planned", "L2S": "L2+suggested", "L2S3": "L2+suggested v3"}
PAIRS = [("L1", "L2"), ("L2", "L2P"), ("L2P", "L2S"), ("L1", "L2P"), ("L1", "L2S"), ("L2P", "L2S3"), ("L2S", "L2S3"), ("L1", "L2S3")]
HS_R = 200.0
TH = (0.10, 0.30)


def facilities(r, have, meta):
    """每个态在地图上要画的设施（经纬度）：L1 现状泵站、L2 移动泵、L2+planned 泵站与塘、L2+suggested 措施。"""
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    ll = lambda x, y: [round(v, 6) for v in tr.transform(x, y)[::-1]]
    nodexy = lambda k: tuple(map(float, k.split("_")[:2])) if k and k[0].isdigit() else None
    F = {"L1": []}
    for p in meta.get("pumps_1d", []):
        a = nodexy(p["from"]); b = nodexy(p["to"])
        F["L1"].append({"kind": "ps_l1", "id": p["ps"], "q_lps": round(p["q_m3s"] * 1000), "ll": ll(*a), "to_ll": ll(*b) if b else None, "how": p["how"]})
    f2 = ROOT / "out" / f"c1_L2_inputs_R{r}.json"
    if f2.exists():
        F["L2"] = [{"kind": "mobile", "id": p["site"], "q_lps": p["q_lps"], "ll": ll(p["x"], p["y"]),
                    "to_ll": ll(*nodexy(p["receiver"])) if nodexy(p["receiver"]) else None, "how": p["how"]} for p in json.loads(f2.read_text(encoding="utf-8"))["pumps"]]
    f3 = ROOT / "out" / f"c1_L2P_inputs_R{r}.json"
    if f3.exists():
        import geopandas as gpd
        S = gpd.read_file(ROOT / "console" / "data" / "c1_pumps.geojson").set_index("id")
        d3 = json.loads(f3.read_text(encoding="utf-8"))
        F["L2P"] = [{"kind": "ps_planned", "id": p["station"], "q_lps": p["q_lps"], "ll": [round(S.loc[p["station"]].geometry.y, 6), round(S.loc[p["station"]].geometry.x, 6)],
                     "how": p["how_from"] + " → " + p["how_to"]} for p in d3["pumps"]]
        F["L2P"] += [{"kind": "pond", "id": q["node"], "vol_m3": round(q["vol"]), "area_ha": q["area_ha"], "ll": ll(*q["xy"])} for q in d3["ponds"]]
    f4 = ROOT / "out" / f"c1_L2S_inputs_R{r}.json"
    if f4.exists():
        F["L2S"] = json.loads(f4.read_text(encoding="utf-8")).get("map", [])
    f5 = ROOT / "out" / f"c1_L2S3_inputs_R{r}.json"          # v3（2026-09-29）：按塘剩余库容分配
    if f5.exists():
        F["L2S3"] = json.loads(f5.read_text(encoding="utf-8")).get("map", [])
    return F


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--rain", type=int, choices=[61, 96])
    ap.add_argument("--tag", default=None, help="情景名：R61 / R96（原计算域）或 U61（客户单元版计算域 RB-3 + MBZ + ADGC-10，2026-09-29）")
    a = ap.parse_args(argv)
    import geopandas as gpd
    from matplotlib.path import Path as MPath
    from scipy.spatial import cKDTree
    from shapely.ops import unary_union
    from city2d_post import render_diff
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    CA = gpd.read_file(ROOT / "console" / "data" / "hotspot_catch_C1.geojson").to_crs(32640)
    CA = CA[CA.kind != "合计"]
    hs_ids = list(dict.fromkeys(CA.hotspot_id))
    tags = [a.tag] if a.tag else [f"R{a.rain}"] if a.rain else ["R61", "R96"]
    for tag in tags:
        r = 61 if tag.startswith("D") else int(tag[1:]); name = f"C1_{tag}"            # D24 = 设计暴雨 61.34 mm / 24 h
        U = tag.startswith("U"); MV = tag.startswith("M")                 # U = 客户单元版；M = 模型版（2026-09-30）
        have = [s for s in STATES if (C2D / f"twoway_{name}_{s}.json").exists() and (C2D / f"{name}_{s}_2d.npz").exists()]
        if not have:
            print(f"{name}：还没有算完的态"); continue
        out_dir = f"flood2d_c1_{tag.lower()}"
        print(f"== {name}：{have}")
        subprocess.run([sys.executable, str(HERE / "pilot_frames.py"), "--name", name, "--states", ",".join(have), "--out", out_dir,
                        "--region", "C1 客户单元（RB-3 + MBZ + ADGC-10，20.3 km²）" if U else "C1 模型版（RB3 + Musaffah_RD09 + B_C 三个客户模型，38.1 km²）" if MV else "C1 热点群（8 个热点的汇水区外扩 300 m，42.8 km²）",
                        "--scen", "61.34mm/24h" if tag.startswith("D") else f"{r}mm/2h", "--stats-tag", f"{tag}D2"], check=True)
        D = ROOT / "console" / "data" / out_dir
        meta = json.loads((C2D / f"pilot_{name}_L1.json").read_text(encoding="utf-8"))
        Z = {s: np.load(C2D / f"{name}_{s}_2d.npz") for s in have}
        TW = {s: json.loads((C2D / f"twoway_{name}_{s}.json").read_text(encoding="utf-8")) for s in have}
        z0 = Z[have[0]]; xy = z0["xy"].astype(float); area = z0["area"].astype(float); elev = z0["elev"].astype(float)
        rain_mask = z0["rain_mask"].astype(bool)
        tr = cKDTree(xy)
        # 区域掩膜
        masks = {}
        for h in hs_ids:
            g = unary_union(list(CA[CA.hotspot_id == h].geometry.buffer(0)))
            sel = np.zeros(len(xy), bool)
            for part in (g.geoms if hasattr(g, "geoms") else [g]):
                sel |= MPath(np.array(part.exterior.coords)[:, :2]).contains_points(xy)
            masks[f"{h}|catch"] = sel
            p = H.loc[h].geometry
            m2 = np.zeros(len(xy), bool); m2[tr.query_ball_point((p.x, p.y), HS_R)] = True
            masks[f"{h}|200m"] = m2
        masks["C1 八个热点|catch"] = np.any([masks[f"{h}|catch"] for h in hs_ids], axis=0)      # 并集：各热点汇水区有重叠，不能相加
        masks["C1 八个热点|200m"] = np.any([masks[f"{h}|200m"] for h in hs_ids], axis=0)
        if U:      # 客户单元版：另按客户的 3 个汇水区单元及其并集统计
            UG = gpd.read_file(ROOT / "out" / "交付" / "C1汇水区_客户单元" / "C1_客户单元_3个面.shp").to_crs(32640)
            for nm, g in zip(UG.New_Number, UG.geometry):
                sel = np.zeros(len(xy), bool)
                for part in (g.geoms if hasattr(g, "geoms") else [g]):
                    sel |= MPath(np.array(part.exterior.coords)[:, :2]).contains_points(xy)
                masks[f"{nm}|unit"] = sel
            masks["C1 客户单元|unit"] = np.any([masks[f"{nm}|unit"] for nm in UG.New_Number], axis=0)
        if MV:     # 模型版：按三个客户模型汇水区（及 B_C 的 O-2 分区）与并集统计
            MG = gpd.read_file(ROOT / "out" / "units_from_models" / "model_units.geojson").to_crs(32640)
            MG = MG[MG.model.isin(["RB3", "Musaffah_RD09", "B_C"])]
            for nm, g in zip(MG.name, MG.geometry):
                sel = np.zeros(len(xy), bool)
                for part in (g.geoms if hasattr(g, "geoms") else [g]):
                    sel |= MPath(np.array(part.exterior.coords)[:, :2]).contains_points(xy)
                masks[f"{nm}|unit"] = sel
            masks["C1 模型单元|unit"] = np.any([masks[f"{nm}|unit"] for nm in MG[MG.level == "model"].name], axis=0)
        masks["全域|served"] = ~rain_mask
        masks["全域|rain_on_grid"] = rain_mask
        masks["全域|all"] = np.ones(len(xy), bool)
        res = {"name": name, "rain_mm": 61.34 if tag.startswith("D") else r, "dur_h": 24 if tag.startswith("D") else 2, "states": have, "state_names": {s: NAMES[s] for s in have}, "zones": {}, "deepest": {},
               "balance": {}, "diffs": [],
               "koujing": {"水深": "三角单元形心水深（ANUGA stage − elevation）", "干湿": "< 0.02 m 算干",
                           "积水面积": "全程最大水深 ≥ 0.10 / ≥ 0.30 m 的单元面积之和（按三角单元真实面积）",
                           "最大积水量": "逐帧（每 5 分钟）Σ 水深 × 单元面积 的最大值，出现时刻为该帧时刻",
                           "热点汇水区": "该热点的地表（5 m DTM 填洼 D8）+ 管网（上游井服务范围）汇水区并集",
                           "热点 200 m": "离热点坐标 200 m 内的单元",
                           "管网服务区 / 直接降雨区": f"离最近井 ≤ / > {meta['R_served_m']:.1f} m；直接降雨区的净雨直接下在二维上"}}
        for s in have:
            z = Z[s]; hm = z["hmax"].astype(float); fr = z["frames"]; th = z["t_h"]
            for key, m in masks.items():
                vols = np.array([float((fr[i].astype(float)[m] * area[m]).sum()) for i in range(len(th))])
                i = int(vols.argmax())
                res["zones"].setdefault(key, {})[s] = {
                    "max_depth_m": round(float(hm[m].max()) if m.any() else 0.0, 3),
                    "area_ge_0p10_ha": round(float(area[m & (hm >= TH[0])].sum()) / 1e4, 2),
                    "area_ge_0p30_ha": round(float(area[m & (hm >= TH[1])].sum()) / 1e4, 2),
                    "peak_vol_m3": round(float(vols[i])), "peak_vol_t_h": round(float(th[i]), 2), "zone_area_ha": round(float(area[m].sum()) / 1e4, 1)}
            k = int(hm.argmax()); px, py = xy[k]
            near = tr.query_ball_point((px, py), 50.0)
            dh = {h: float(np.hypot(px - H.loc[h].geometry.x, py - H.loc[h].geometry.y)) for h in hs_ids}
            nh = min(dh, key=dh.get)
            res["deepest"][s] = {"depth_m": round(float(hm[k]), 2), "x": round(float(px)), "y": round(float(py)), "ground_m": round(float(elev[k]), 2),
                                 "min_ground_50m": round(float(elev[near].min()), 2), "median_ground_200m": round(float(np.median(elev[tr.query_ball_point((px, py), 200.0)])), 2),
                                 "rain_on_grid_cell": bool(rain_mask[k]), "near_hotspot": nh, "near_dist_m": round(dh[nh])}
            t = TW[s]; acc = t["acc_m3"]
            # 一维侧：计算域内的井溢流 = 交给二维的水；域外的井（整块取进来的管网伸出域外的部分）只算一维，溢流记为损失
            import re as _re
            rt = (C2D / f"{name}_{s}_2way.rpt").read_text(errors="ignore")
            mb = _re.search(r"Node Flooding Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", rt, _re.S)
            fin = fout = 0.0
            if mb:
                for ln in mb.group(1).splitlines():
                    q = ln.split()
                    if len(q) >= 6:
                        v = float(q[5]) * 1000.0
                        if q[0] in meta["nodes"]:
                            fin += v
                        else:
                            fout += v
            res["balance"][s] = {"1D→2D": round(acc["1to2"]), "2D→1D": round(acc["2to1"]), "二维直接降雨": round(acc["rain"]), "进塘（地表）": round(acc["pond"]),
                                 "泵排出系统（二维取水）": round(acc.get("pump_out", 0)), "域内留存（6 h 末）": round(t["final_vol_m3"]),
                                 "边界出流（余项）": round(t["boundary_out_m3"]),
                                 "一维：域内井溢流（=交给二维）": round(fin), "一维：域外井溢流（只算一维，记为损失）": round(fout), "wall_h": round(t["wall_s"] / 3600, 2)}
        for s1, s2 in PAIRS:
            if s1 in Z and s2 in Z:
                dh = Z[s2]["hmax"].astype(float) - Z[s1]["hmax"].astype(float)
                fn = f"diff_{s2}_minus_{s1}.png"
                b = render_diff(xy, dh, 30.0, D / fn)
                res["diffs"].append({"a": s1, "b": s2, "png": fn, "bounds": b, "shallower_ha": round(float(area[dh <= -0.05].sum()) / 1e4, 2),
                                     "deeper_ha": round(float(area[dh >= 0.05].sum()) / 1e4, 2)})
        from c1_facilities import build as fac_build          # 2026-09-29：带设计参数与各态实际表现（系统符号化 + 参数框）
        res["facilities"] = fac_build(r, have, meta, tag=tag)
        (D / "c1_stats.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        (ROOT / "out" / f"c1_stats_{tag}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        with open(ROOT / "out" / f"c1_stats_{tag}.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(["区域", "口径", "指标"] + [NAMES[s] for s in have])
            for key, v in res["zones"].items():
                zn, kind = key.split("|")
                kj = {"catch": "热点汇水区（并集）" if zn.startswith("C1") else "热点汇水区", "200m": "热点 200 m", "served": "管网服务区", "rain_on_grid": "直接降雨区", "all": "全域", "unit": "客户汇水区单元"}[kind]
                for met, lab in (("max_depth_m", "最大水深 m"), ("area_ge_0p10_ha", "积水面积 ≥0.10 m（ha）"),
                                 ("area_ge_0p30_ha", "积水面积 ≥0.30 m（ha）"), ("peak_vol_m3", "最大积水量 m³"), ("peak_vol_t_h", "最大积水量出现时刻 h")):
                    w.writerow([zn, kj, lab] + [v[s][met] for s in have])
            for s in have:
                for k2, v2 in res["balance"][s].items():
                    w.writerow(["全域", "水量对账", f"{NAMES[s]} · {k2}"] + [""] * 0 + [v2])
        print(f"  → {D.name}/c1_stats.json、out/c1_stats_{tag}.json / .csv；差值图 {len(res['diffs'])} 张")
        for s in have:
            dp = res["deepest"][s]
            print(f"    {NAMES[s]} 最深 {dp['depth_m']} m @ ({dp['x']},{dp['y']}) 地面 {dp['ground_m']}（50 m 内最低 {dp['min_ground_50m']}，200 m 中位 {dp['median_ground_200m']}）"
                  f" 离 {dp['near_hotspot']} {dp['near_dist_m']} m {'直接降雨区' if dp['rain_on_grid_cell'] else '管网服务区'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
