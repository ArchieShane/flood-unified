# -*- coding: utf-8 -*-
"""艾因 250 m 结果补「热点」统计，让 flood-viewer 的指标卡 / 热点表和别的区域一致（在 swbrain 容器里跑；2026-10-17）。

输入：c2d/AA_<tag>_{L1,L2}_2d.npz、cases/alain/aa_hotspots.json（客户 AAM 热点 184 个，EPSG:32640）、flood2d_aa_<tag> 目录里的 aa_stats.json
口径（同 ad_frames.py）：每个热点取离它 200 m 内的二维单元；250 m 网格单元稀，200 m 内没有单元的取最近的一个（距离 ≤ 250 m 才算在计算域内），再远的热点不在域内、不统计。
同时补交付 公共/（计算域.geojson、现状泵站.json、已建成塘.json、积水点.json），导出积水点结果表（同 ADM 30 m：积水点/积水点结果表.csv / .xlsx / 积水点图层.geojson，在交付目录 out/交付/艾因_模型版_<雨量>mm<历时>h_250m/ 下；184 个热点全部列出，域外的标「域外，未评估」）。
写回 aa_stats.json 的 zones：「<热点>|200m」、「<N> 个热点|200m」（并集）、「全域|all」（原有）。只补统计，不改任何计算结果。
用法：python tools/aa_hotspots.py --tag ALAIN_M61H1 [--dir /app/console/data/flood2d_aa_alain_m61h1g250]
"""
import argparse
import csv
import json
import pathlib
import re
import sys

import numpy as np
from scipy.spatial import cKDTree

HS_R, NEAR_MAX, TH = 200.0, 250.0, (0.10, 0.30)
ROOT = pathlib.Path("/app")
C2D = pathlib.Path("/work/c2d")


def export_table(tag, HS, masks, kept, Z, states, xy, area, tr):
    """积水点结果表 + 图层 → 交付目录/积水点/。口径同 ad_post：200 m 内最大水深、最近单元水深、面积、最大积水量、出现时刻、起始与持续。"""
    m_ = re.match(r"ALAIN_M(\d+)H(\d+)", tag); rain, dur = int(m_[1]), int(m_[2])
    out = ROOT / "out" / "交付" / f"艾因_模型版_{rain}mm{dur}h_250m" / "积水点"; out.mkdir(parents=True, exist_ok=True)
    flag = lambda d: "积水（≥0.30 m）" if d >= TH[1] else ("浅积水（0.10–0.30 m）" if d >= TH[0] else "否")
    keep = {h["hotspot_id"] for h in kept}
    head = ["积水点", "片区", "位置", "重要性", "x (EPSG:32640)", "y (EPSG:32640)", "是否在二维计算域内"]
    for s in states:
        head += [f"{s} 200 m 内最大水深 m", f"{s} 最近单元水深 m", f"{s} 积水面积 ≥0.10 m (ha)", f"{s} 积水面积 ≥0.30 m (ha)", f"{s} 最大积水量 m³",
                 f"{s} 最大水深时刻 h", f"{s} 水深≥0.10 m 起始 h", f"{s} 水深≥0.10 m 持续 min", f"{s} 是否积水"]
    if len(states) == 2:
        head.append("L2−L1 最大水深变化 m")
    try:
        from pyproj import Transformer
        tf = Transformer.from_crs(32640, 4326, always_xy=True).transform
    except Exception:
        tf = None
    rows, feats = [head], []
    for h in sorted(HS, key=lambda q: (q["criticality"] != "Very High", q["criticality"] != "High", q["hotspot_id"])):
        hid = h["hotspot_id"]; r = [hid, h["area"], h["location"], h["criticality"], h["x"], h["y"], "是" if hid in keep else "否（域外，未评估）"]
        props = {"hotspot_id": hid, "area": h["area"], "location": h["location"], "criticality": h["criticality"], "in_domain": hid in keep}
        dmax = {}
        for s in states:
            if hid not in keep:
                r += [None] * 9; continue
            z = Z[s]; hm = z["hmax"].astype(float); fr = z["frames"]; th = np.asarray(z["t_h"], float)
            idx = np.flatnonzero(masks[f"{hid}|200m"]); d0, i0 = tr.query((h["x"], h["y"]))
            dt_min = float(np.median(np.diff(th)) * 60.0) if len(th) > 1 else 5.0
            ts = np.asarray(fr[:, idx], dtype=np.float32); top = ts.max(axis=1); vol = (ts.astype(np.float64) * area[idx]).sum(axis=1)
            wet = np.flatnonzero(top >= TH[0]); md = round(float(hm[idx].max()), 3); dmax[s] = md
            v = [md, round(float(hm[i0]), 3), round(float(area[idx][hm[idx] >= TH[0]].sum()) / 1e4, 3), round(float(area[idx][hm[idx] >= TH[1]].sum()) / 1e4, 3), round(float(vol.max())),
                 round(float(th[int(top.argmax())]), 2), round(float(th[wet[0]]), 2) if len(wet) else None, round(len(wet) * dt_min) if len(wet) else 0, flag(md)]
            r += v
            props.update({f"{s}_max_depth_m": v[0], f"{s}_nearest_depth_m": v[1], f"{s}_area_ge_0p10_ha": v[2], f"{s}_area_ge_0p30_ha": v[3], f"{s}_peak_vol_m3": v[4],
                          f"{s}_t_peak_h": v[5], f"{s}_t_first_ge_0p10_h": v[6], f"{s}_dur_ge_0p10_min": v[7], f"{s}_flooded": v[8]})
        if len(states) == 2:
            dd = round(dmax["L2"] - dmax["L1"], 3) if hid in keep else None; r.append(dd); props["L2_minus_L1_m"] = dd
        rows.append(r)
        lon, lat = tf(h["x"], h["y"]) if tf else (h["x"], h["y"])
        feats.append({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)] if tf else [h["x"], h["y"]]}})
    with open(out / "积水点结果表.csv", "w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerows(rows)
    try:
        import openpyxl
        wb = openpyxl.Workbook(); ws = wb.active
        for r in rows:
            ws.append(r)
        wb.save(out / "积水点结果表.xlsx")
    except ImportError:
        print("  （容器里没有 openpyxl：只出 csv）")
    crs = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84" if tf else "urn:ogc:def:crs:EPSG::32640"}}
    (out / "积水点图层.geojson").write_text(json.dumps({"type": "FeatureCollection", "name": "艾因_积水点", "crs": crs, "features": feats}, ensure_ascii=False), encoding="utf-8")
    print(f"积水点结果表 → {out}（{len(rows) - 1} 个，域内 {len(keep)}）")


def write_stats_csv(tag, zones, balance, states):
    """统计值.csv：表头「区域,口径,指标,<各态>」，和 c1 / ms / rg 同一格式；全域 + 并集 + 逐热点（200 m 内）+ 水量对账。
    交付目录 对比_缺口分析/统计值.csv 和 out/aa_stats_<tag>.csv 两处都重写。"""
    m_ = re.match(r"ALAIN_M(\d+)H(\d+)", tag); rain, dur = int(m_[1]), int(m_[2])
    rows = [["区域", "口径", "指标"] + states]
    order = [k for k in zones if k.endswith("|200m") and "个热点" not in k] + [k for k in zones if "个热点" in k] + [k for k in zones if k.startswith("全域")]
    for key in order:
        zn, kind = key.split("|")
        for met, lab in (("max_depth_m", "最大水深 m"), ("area_ge_0p10_ha", "积水面积 ≥0.10 m（ha）"), ("area_ge_0p30_ha", "积水面积 ≥0.30 m（ha）"),
                         ("peak_vol_m3", "最大积水量 m³"), ("peak_vol_t_h", "最大积水量出现时刻 h")):
            rows.append([zn, {"200m": "热点 200 m", "all": "全域"}[kind], lab] + [zones[key][s][met] for s in states])
    for k2 in (balance or {}).get(states[0], {}):
        rows.append(["全域", "水量对账 m³", k2] + [balance[s][k2] for s in states])
    for f in (ROOT / "out" / "交付" / f"艾因_模型版_{rain}mm{dur}h_250m" / "对比_缺口分析" / "统计值.csv", ROOT / "out" / f"aa_stats_{tag}.csv"):
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "w", newline="", encoding="utf-8-sig") as fh:
            csv.writer(fh).writerows(rows)
    print(f"统计值.csv → 区域,口径,指标 表头，{len(rows) - 1} 行（含 {len(order) - 2} 个热点区域）")


def write_public(tag, kept=None):
    """交付目录 公共/ 下补齐（同 ad_post / rg_post）：计算域.geojson（EPSG:4326）、现状泵站.json、已建成塘.json、积水点.json（有 kept 才写）。
    读端（sim_scope 等）假定每个交付的 公共/ 都带 计算域.geojson，缺了会按「没有域」处理。"""
    m_ = re.match(r"ALAIN_M(\d+)H(\d+)", tag); rain, dur = int(m_[1]), int(m_[2])
    pub = ROOT / "out" / "交付" / f"艾因_模型版_{rain}mm{dur}h_250m" / "公共"; pub.mkdir(parents=True, exist_ok=True)
    meta = json.loads((C2D / f"pilot_AA_{tag}_L2.json").read_text(encoding="utf-8"))
    ring = [[float(p_[0]), float(p_[1])] for p_ in meta["domain_polygon"]]
    try:
        from pyproj import Transformer
        tf = Transformer.from_crs(32640, 4326, always_xy=True).transform
        ring_o = [[round(x, 6), round(y, 6)] for x, y in (tf(*c) for c in ring)]; crs = "urn:ogc:def:crs:OGC:1.3:CRS84"
    except Exception:
        ring_o, crs = ring, "urn:ogc:def:crs:EPSG::32640"
    if ring_o[0] != ring_o[-1]:
        ring_o.append(ring_o[0])
    a = 0.5 * abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1])))
    gj = {"type": "FeatureCollection", "name": "艾因_计算域", "crs": {"type": "name", "properties": {"name": crs}},
          "features": [{"type": "Feature", "properties": {"name": "艾因 250 m 计算域", "area_km2": round(a / 1e6, 2), "res_m": 250},
                        "geometry": {"type": "Polygon", "coordinates": [ring_o]}}]}
    (pub / "计算域.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    for src, dst in (("aa_existing_pumps.json", "现状泵站.json"), ("aa_ponds.json", "已建成塘.json")):
        f = ROOT / "cases" / "alain" / src
        if f.exists():
            (pub / dst).write_bytes(f.read_bytes())
    if kept is not None:
        (pub / "积水点.json").write_text(json.dumps(kept, ensure_ascii=False, indent=0), encoding="utf-8")
        feats = []                                           # 公共/热点.geojson：同 rg_post（域内热点，EPSG:4326）
        for h in kept:
            lon, lat = tf(h["x"], h["y"]) if crs.endswith("CRS84") else (h["x"], h["y"])
            feats.append({"type": "Feature", "properties": {k: h[k] for k in ("hotspot_id", "area", "criticality", "location")}, "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}})
        (pub / "热点.geojson").write_text(json.dumps({"type": "FeatureCollection", "name": "艾因_热点", "crs": {"type": "name", "properties": {"name": crs}}, "features": feats}, ensure_ascii=False), encoding="utf-8")
    print(f"公共/ → {pub}（计算域 {a / 1e6:.1f} km²，{crs.split(':')[-1]}）")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", required=True); ap.add_argument("--dir")
    ap.add_argument("--hotspots", default=str(ROOT / "cases" / "alain" / "aa_hotspots.json"))
    a = ap.parse_args()
    D = pathlib.Path(a.dir or ROOT / "console" / "data" / f"flood2d_aa_{a.tag.lower()}g250")
    name = f"AA_{a.tag}"
    cs = json.loads((D / "aa_stats.json").read_text(encoding="utf-8"))
    HS = json.loads(pathlib.Path(a.hotspots).read_text(encoding="utf-8"))["hotspots"]
    states = [s for s in ("L1", "L2") if (C2D / f"{name}_{s}_2d.npz").exists()]
    Z = {s: np.load(C2D / f"{name}_{s}_2d.npz") for s in states}
    z0 = Z[states[0]]; xy = z0["xy"].astype(float); area = z0["area"].astype(float); tr = cKDTree(xy)
    masks, kept, out = {}, [], 0
    for h in HS:
        idx = tr.query_ball_point((h["x"], h["y"]), HS_R)
        if not idx:
            d0, i0 = tr.query((h["x"], h["y"]))
            if d0 > NEAR_MAX:
                out += 1; continue
            idx = [int(i0)]
        m = np.zeros(len(xy), bool); m[idx] = True
        masks[f"{h['hotspot_id']}|200m"] = m; kept.append(h)
    masks[f"{len(kept)} 个热点|200m"] = np.any(list(masks.values()), axis=0)
    print(f"热点 {len(HS)} 个：在计算域内 {len(kept)}，域外 {out}")
    zones = {k: v for k, v in cs.get("zones", {}).items() if k.startswith("全域")}
    if "全域|all" not in zones and cs.get("all"):
        zones["全域|all"] = cs["all"]            # 容器里的 aa_stats.json 没有 zones（旧版 aa_post），用 all 补上
    for s in states:
        z = Z[s]; fr = z["frames"]; th = np.asarray(z["t_h"], float); hm = z["hmax"].astype(float)
        for k, m in masks.items():
            v = np.array([float((fr[i].astype(float)[m] * area[m]).sum()) for i in range(len(th))]); i = int(v.argmax())
            zones.setdefault(k, {})[s] = {"max_depth_m": round(float(hm[m].max()), 3), "area_ge_0p10_ha": round(float(area[m & (hm >= TH[0])].sum()) / 1e4, 2),
                                         "area_ge_0p30_ha": round(float(area[m & (hm >= TH[1])].sum()) / 1e4, 2), "peak_vol_m3": round(float(v[i])),
                                         "peak_vol_t_h": round(float(th[i]), 2), "zone_area_ha": round(float(area[m].sum()) / 1e4, 1)}
    export_table(a.tag, HS, masks, kept, Z, states, xy, area, tr)
    cs["zones"] = zones; cs["n_hotspots"] = len(kept)
    write_stats_csv(a.tag, zones, cs.get("balance"), states)
    write_public(a.tag, kept)
    cs["hotspots_info"] = {h["hotspot_id"]: {k: h[k] for k in ("area", "location", "criticality")} for h in kept}
    (D / "aa_stats.json").write_text(json.dumps(cs, ensure_ascii=False, indent=1), encoding="utf-8")
    m_ = re.match(r"ALAIN_M(\d+)H(\d+)", a.tag)
    st = ROOT / "out" / "交付" / f"艾因_模型版_{int(m_[1])}mm{int(m_[2])}h_250m" / "统计"; st.mkdir(parents=True, exist_ok=True)   # 同 ad_post 的 统计/（读端按 统计/*_stats.json 数已交付的情景）
    (st / "aa_stats.json").write_text(json.dumps(cs, ensure_ascii=False, indent=1), encoding="utf-8")
    u = f"{len(kept)} 个热点|200m"
    for s in states:
        print(f"  {s}: 热点 200 m 内最深 {zones[u][s]['max_depth_m']} m，≥0.30 m {zones[u][s]['area_ge_0p30_ha']} ha，最大积水量 {zones[u][s]['peak_vol_m3']:,} m³")
    print(f"→ {D / 'aa_stats.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
