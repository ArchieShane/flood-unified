"""DMT 1D 水力模型导出（2026-09-20 客户发的「Hydraulic Model Data Export」：Bentley 导出的 shapefile + SWMM inp）→ 与本系统对表。

三个模型：Musaffah_00、RB3（shapefile 缺 .dbf，只有几何；属性在 SWMM_Export/WITH CD catchments.inp 里）、Shaliela。
坐标是 UTM 40N（EPSG:32640，与 DTM 同），shapefile 没带 .prj。
shapefile 里带的是一次运行的结果字段：人孔 EVER_OF（是否溢流过）/ FLOW_MAX / HGL，管道 CAP_FULL / V_(MAX)，汇水区 Q_(MAX) / RUNOFF_VOL——哪一场雨的结果导出文件没说，要问 DMT。
SWMM inp 没有 [SUBCATCHMENTS]，径流是按节点预先算好的 48 h 入流序列（[INFLOWS] + *_Inflow_Ts）；[RAINGAGES] 列出了全部设计雨与实测雨（累计序列；雨量计标 IN 但数值就是 mm：10 年 24 h 序列终值 51.12）。

只读客户目录 [dmt_model].dir；输出 out/dmt_model/*（客户数据派生，不入库）与 console/data/dmt_model.geojson。
"""
from __future__ import annotations
import argparse
import collections
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "dmt_model"

LAYER_FILES = {"catchments": ("Catchment",), "conduits": ("Conduit", "conduit"), "manholes": ("Manhole", "Manholes"), "inlets": ("Inlets",),
               "outfalls": ("Outfall", "Outfalls"), "ponds": ("POnds", "ponds", "Ponds"), "pumps": ("Pumps",), "wetwells": ("Wetwell",)}
DEFAULT_MODELS = {"Musaffah_00": {"area": "Mussafah", "inp": "SWMM_Export/Mussafah_00.inp", "parsons": "Mussafah_00"},
                  "RB3": {"area": "Zayed", "inp": "SWMM_Export/WITH CD catchments.inp", "parsons": "RB3"},
                  "Shaliela": {"area": "Al Shahama", "inp": "SWMM_Export/Shaliela.inp", "parsons": "Shaliela"}}
MM_PER_IN = 25.4
SHORT = re.compile(r"_1_hours?|_2_hours?|\(1 ?Hr\)|\(2 ?Hr\)|\(1 ?hr\)|\(2 ?hr\)|1_hour|2_hours", re.I)


def params(cfg: dict) -> dict:
    D = cfg.get("dmt_model") or {}
    return {"dir": D.get("dir"), "crs": D.get("crs", "EPSG:32640"), "models": D.get("models") or DEFAULT_MODELS, "overflow_radius_m": float(D.get("overflow_radius_m", 150))}


def available(cfg: dict) -> bool:
    d = params(cfg)["dir"]
    return bool(d) and Path(d).exists()


# ---------------------------------------------------------------- shapefile
def _find_shp(folder: Path, names) -> Path | None:
    for n in names:
        for p in folder.glob("*.shp"):
            if p.stem.lower() == n.lower():
                return p
    return None


def load_model(cfg: dict, name: str) -> dict:
    """一个模型文件夹 → {layer: GeoDataFrame(EPSG:32640)}；缺 .dbf 的层只有几何。"""
    import geopandas as gpd
    P = params(cfg); folder = Path(P["dir"]) / name
    out = {}
    for layer, names in LAYER_FILES.items():
        shp = _find_shp(folder, names)
        if shp is None:
            continue
        try:
            g = gpd.read_file(shp, engine="pyogrio")
        except Exception:
            g = gpd.read_file(shp, engine="pyogrio", columns=[])
        g = g.set_crs(P["crs"], allow_override=True)
        g.attrs["has_dbf"] = (shp.with_suffix(".dbf")).exists()
        out[layer] = g
    return out


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def boundary(catch):
    """汇水区多边形并成模型边界（EPSG:32640），顺带面积 ha。"""
    u = catch.geometry.buffer(0).union_all() if hasattr(catch.geometry, "union_all") else catch.geometry.buffer(0).unary_union
    return u, float(u.area) / 1e4


def _area_unit_guess(catch, boundary_ha: float) -> tuple[str, float]:
    """汇水区属性 A 的单位：和几何面积比。"""
    if "A" not in catch.columns:
        return "n/a", float("nan")
    s = float(_num(catch["A"]).sum())
    for unit, k in (("m2", 1e4), ("ha", 1.0), ("km2", 1e-2), ("acre", 2.471)):
        if boundary_ha * k * 0.5 <= s <= boundary_ha * k * 1.5:
            return unit, s
    return "?", s


def layer_summary(cfg: dict, name: str, layers: dict) -> dict:
    """一个模型的口径：规模、结果字段、塘、泵、出水口。"""
    from pyproj import Transformer
    tr = Transformer.from_crs(params(cfg)["crs"], "EPSG:4326", always_xy=True)
    S = {"model": name, **{k: v for k, v in params(cfg)["models"].get(name, {}).items()}, "layers": {k: int(len(v)) for k, v in layers.items()},
         "has_dbf": {k: bool(v.attrs.get("has_dbf")) for k, v in layers.items()}}
    if "catchments" in layers:
        b, ha = boundary(layers["catchments"]); S["boundary_ha"] = round(ha, 1)
        unit, tot = _area_unit_guess(layers["catchments"], ha); S["catchment_area_attr"] = {"unit_guess": unit, "sum": round(tot, 1)}
        c = layers["catchments"]
        if "RUNOFF_VOL" in c.columns:
            S["catchment_results"] = {"runoff_vol_sum": round(float(_num(c["RUNOFF_VOL"]).sum()), 1), "q_max_sum": round(float(_num(c["Q_(MAX)"]).sum()), 1) if "Q_(MAX)" in c.columns else None,
                                      "scs_cn_median": float(_num(c["SCS_CN"]).median()) if "SCS_CN" in c.columns else None, "tc_min_median": float(_num(c["TC"]).median()) if "TC" in c.columns else None}
        minx, miny, maxx, maxy = b.bounds; lon0, lat0 = tr.transform(minx, miny); lon1, lat1 = tr.transform(maxx, maxy)
        S["bbox_lonlat"] = [round(lon0, 5), round(lat0, 5), round(lon1, 5), round(lat1, 5)]
    if "conduits" in layers:
        c = layers["conduits"]
        S["conduits"] = {"n": int(len(c)), "length_km": round(float(c.geometry.length.sum()) / 1000, 1)}
        if "D" in c.columns:
            d = _num(c["D"]); S["conduits"].update({"dn_median_mm": float(d.median()), "dn_max_mm": float(d.max()),
                                                   "cap_full_lps_median": float(_num(c["CAP_FULL"]).median()) if "CAP_FULL" in c.columns else None,
                                                   "v_max_median": float(_num(c["V_(MAX)"]).median()) if "V_(MAX)" in c.columns else None})
    if "manholes" in layers:
        m = layers["manholes"]
        S["manholes"] = {"n": int(len(m))}
        if "EVER_OF" in m.columns:
            of = m["EVER_OF"].astype(str).str.lower().isin(["true", "1", "yes"])
            surch = (_num(m["HGL_OUT"]) >= _num(m["ELEV_GND"]) - 0.05) if ("HGL_OUT" in m.columns and "ELEV_GND" in m.columns) else None
            S["manholes"].update({"overflowed": int(of.sum()), "overflow_share": round(float(of.mean()), 3),
                                  "surcharged_to_ground": (int(surch.sum()) if surch is not None else None), "surcharged_share": (round(float(surch.mean()), 3) if surch is not None else None),
                                  "flow_max_lps_median": float(_num(m["FLOW_MAX"]).median()) if "FLOW_MAX" in m.columns else None,
                                  "gnd_range_m": [float(_num(m["ELEV_GND"]).min()), float(_num(m["ELEV_GND"]).max())] if "ELEV_GND" in m.columns else None})
    if "inlets" in layers and "EVER_OF" in layers["inlets"].columns:
        i = layers["inlets"]; of = i["EVER_OF"].astype(str).str.lower().isin(["true", "1", "yes"])
        S["inlets"] = {"n": int(len(i)), "overflowed": int(of.sum()), "overflow_share": round(float(of.mean()), 3)}
    if "ponds" in layers:
        p = layers["ponds"]; rows = []
        for k, (_, r) in enumerate(p.iterrows()):
            cx, cy = r.geometry.centroid.x, r.geometry.centroid.y; lon, lat = tr.transform(cx, cy)
            rows.append({"label": (r.get("LABEL") if "LABEL" in p.columns and pd.notna(r.get("LABEL")) else f"P{k + 1}"), "max_stor_m3": (float(r["MAX_STOR"]) if "MAX_STOR" in p.columns and pd.notna(r.get("MAX_STOR")) else None),
                         "area_m2": round(float(r.geometry.area), 0), "lon": round(lon, 6), "lat": round(lat, 6)})
        S["ponds"] = {"n": int(len(p)), "max_stor_sum_m3": round(sum(x["max_stor_m3"] or 0 for x in rows), 0), "area_sum_m2": round(sum(x["area_m2"] for x in rows), 0), "items": rows}
    if "pumps" in layers:
        q = layers["pumps"]
        S["pumps"] = {"n": int(len(q)), "definitions": (q["P_DEF"].astype(str).value_counts().to_dict() if "P_DEF" in q.columns else None),
                      "labels": (q["LABEL"].astype(str).tolist()[:40] if "LABEL" in q.columns else None)}
    if "outfalls" in layers:
        o = layers["outfalls"]
        if "LABEL" in o.columns:
            S["outfalls"] = [{"label": r.get("LABEL"), "cond": r.get("BOUNDCOND"), "tailwater_m": (float(r["TAILWATER"]) if pd.notna(r.get("TAILWATER")) else None),
                              "tidal_gate": str(r.get("TIDALGATE?")).lower() == "true", "to": r.get("BNDYID")} for _, r in o.iterrows()]
        else:
            S["outfalls"] = {"n": int(len(o))}
    return S


# ---------------------------------------------------------------- SWMM inp：雨型与入流
RAIN_KEYS = [  # (正则, 情景 key, 说明)
    (r"2024.*24|24.*2024|2024_April_critical", "2024-04", "2024 年 4 月实测 24 h"),
    (r"2026.*24|24.*2026|-2026-2026yr", "2026-03", "2026 年 3 月实测 24 h"),
    (r"10_years?_(SCS_)?with_CC-10yr|10_years_with_CC-10yr", "10cc", "10 年一遇 + 气候变化 24 h"),
    (r"10_Year_\(24Hr\)|-10yr-10yr", "10", "10 年一遇 24 h"),
    (r"5_Yr", "5", "5 年一遇 24 h"),
]


def classify_rain_name(name: str, duration_h: float | None = None) -> tuple[str | None, str]:
    """雨量计名 → 本系统情景 key。只有 24 h 序列进 5/10/10cc/2024-04/2026-03；1 h / 2 h 短历时版本 key 为 None（说明里标出）。"""
    short = bool(SHORT.search(name)) or (duration_h is not None and duration_h < 20)
    for pat, key, zh in RAIN_KEYS:
        if re.search(pat, name):
            return (None, zh + "（短历时版）") if short else (key, zh)
    m = re.search(r"(\d+)mm", name)
    if m:
        return f"trial{m.group(1)}", f"{m.group(1)} mm 试算"
    return None, ""


def _hms(t: str) -> float:
    h, m, s = (t.split(":") + ["0", "0"])[:3]
    return int(h) * 60 + int(m) + float(s) / 60.0


def resample_cum(pairs: list[tuple[str, float]], dt_min: float = 5.0) -> dict:
    """累计雨量序列 → 统一 dt 网格的累计 mm（单调不减）。"""
    t = np.array([_hms(a) for a, _ in pairs]); v = np.array([b for _, b in pairs], dtype=float)
    o = np.argsort(t); t, v = t[o], v[o]; v = np.maximum.accumulate(v)
    grid = np.arange(0.0, float(t.max()) + 1e-9, dt_min)
    return {"dt_min": dt_min, "cum_mm": [round(float(x), 3) for x in np.interp(grid, t, v)]}


def rain_series_stats(pairs: list[tuple[str, float]]) -> dict:
    """累计序列（雨量计标 IN，数值就是 mm）→ 总雨深、历时、最大 15 分钟、峰值小时强度。"""
    t = np.array([_hms(a) for a, _ in pairs]); v = np.array([b for _, b in pairs], dtype=float)      # 数值就是 mm
    o = np.argsort(t); t, v = t[o], v[o]
    depth = float(v.max()); dur_h = float(t.max()) / 60.0
    grid = np.arange(0.0, t.max() + 1e-9, 15.0); cum = np.interp(grid, t, v)
    inc15 = float(np.max(np.diff(cum))) if len(grid) > 1 else depth
    g60 = np.arange(0.0, t.max() + 1e-9, 60.0); cum60 = np.interp(g60, t, v); inc60 = float(np.max(np.diff(cum60))) if len(g60) > 1 else depth
    return {"depth_mm": round(depth, 2), "duration_h": round(dur_h, 2), "max15_mm": round(inc15, 2), "i15_mmh": round(inc15 * 4, 1), "max60_mm": round(inc60, 2), "n": int(len(pairs))}


def parse_inp(path: Path) -> dict:
    """扫一遍 inp：雨量计序列统计、节点入流合计、节点/管道/塘/泵/出水口计数。大文件（RB3 142 MB）约 1 分钟，结果缓存在 out/dmt_model。"""
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / f"inp2_{Path(path).stem.replace(' ', '_')}.json"          # v2：带 5 分钟累计序列
    if cache.exists() and cache.stat().st_mtime >= Path(path).stat().st_mtime:
        return json.loads(cache.read_text(encoding="utf-8"))
    sec = None; rain = collections.defaultdict(list); cur = None; acc = [0, 0.0, 0.0]; inflow = {}
    counts = collections.Counter(); options = {}; outfalls = []; storage = []; pumps = []; curves = collections.defaultdict(list)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith(";"):
                continue
            m = re.match(r"^\[(\w+)\]", s)
            if m:
                sec = m.group(1).upper(); continue
            parts = s.split(); counts[sec] += 1
            if sec == "TIMESERIES":
                name = parts[0]
                if "_Inflow" in name:
                    if name != cur:
                        if cur is not None:
                            inflow[cur] = tuple(acc)
                        cur = name; acc = [0, 0.0, 0.0]
                    try:
                        v = float(parts[-1]); acc[0] += 1; acc[1] += v; acc[2] = max(acc[2], v)
                    except ValueError:
                        pass
                else:
                    try:
                        rain[name].append((parts[-2], float(parts[-1])))
                    except (ValueError, IndexError):
                        pass
            elif sec == "OPTIONS" and len(parts) > 1:
                options[parts[0]] = " ".join(parts[1:])
            elif sec == "OUTFALLS":
                outfalls.append(parts)
            elif sec == "STORAGE":
                storage.append(parts[:6])
            elif sec == "PUMPS":
                pumps.append(parts[:5])
            elif sec == "CURVES" and len(parts) >= 3:
                curves[parts[0]].append(parts[1:])
    if cur is not None:
        inflow[cur] = tuple(acc)
    rains = []
    for name, pairs in rain.items():
        st = rain_series_stats(pairs); key, zh = classify_rain_name(name, st["duration_h"])
        rains.append({"name": name, "scenario_key": key, "label_zh": zh, **st, **({"series": resample_cum(pairs)} if key else {})})
    # 塘容：STORAGE TABULAR 的 深度-面积 曲线积分
    ponds = []
    for st_ in storage:
        if len(st_) >= 6 and st_[4].upper() == "TABULAR":
            cv = curves.get(st_[5]) or []
            rows = []
            for a in cv:
                try:
                    rows.append((float(a[-2]), float(a[-1])))
                except (ValueError, IndexError):
                    pass
            if len(rows) > 1:
                d = np.array([r[0] for r in rows]); A = np.array([r[1] for r in rows]); o = np.argsort(d)
                vol = float(np.trapezoid(A[o], d[o])) if hasattr(np, "trapezoid") else float(np.trapz(A[o], d[o]))
                ponds.append({"label": st_[0], "invert_m": float(st_[1]), "depth_m": float(d.max()), "area_m2": round(float(A.max()), 0), "volume_m3": round(vol, 0), "is_pond": bool(re.match(r"^(PO|STO)", st_[0], re.I))})
    tot = 0.0; peak = 0.0
    for n, ssum, vmax in inflow.values():
        dt_s = (48 * 3600) / max(n - 1, 1); tot += ssum * dt_s / 1000.0; peak += vmax / 1000.0
    res = {"file": str(path), "options": {k: options[k] for k in ("FLOW_UNITS", "FLOW_ROUTING", "INFILTRATION", "START_DATE", "END_DATE", "REPORT_STEP", "ALLOW_PONDING") if k in options},
           "counts": {k: int(v) for k, v in counts.items() if k}, "rain": rains,
           "inflow": {"nodes": len(inflow), "total_m3": round(tot, 0), "peak_sum_m3s": round(peak, 2), "steps_per_series": int(next(iter(inflow.values()))[0]) if inflow else None},
           "outfalls": outfalls, "storage": storage, "storage_volumes": ponds, "pumps": pumps, "parsed_at": time.strftime("%Y-%m-%d %H:%M")}
    cache.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def event_hyetographs(cfg: dict) -> dict:
    """{情景 key: {区域: {dt_min, cum_mm, depth_mm}}}——每个模型的 24 h 雨量计序列按 [dmt_model.models.X].area 归到热点区域名。写 out/dmt_model/rain_events.json。"""
    P = params(cfg); out = {}
    for name, M in P["models"].items():
        inp = Path(P["dir"]) / M.get("inp", "")
        if not inp.exists():
            continue
        r = parse_inp(inp)
        for x in r["rain"]:
            if x.get("scenario_key") and x.get("series"):
                out.setdefault(x["scenario_key"], {})[M.get("area", name)] = {**x["series"], "depth_mm": x["depth_mm"], "i15_mmh": x["i15_mmh"], "model": name, "gauge": x["name"]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rain_events.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def rain_table(cfg: dict) -> pd.DataFrame:
    """三个模型的雨型汇总表（按情景 key）。"""
    P = params(cfg); rows = []
    for name, M in P["models"].items():
        inp = Path(P["dir"]) / M.get("inp", "")
        if not inp.exists():
            continue
        r = parse_inp(inp)
        for x in r["rain"]:
            rows.append({"model": name, "area": M.get("area"), **x})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 与本系统对表
def compare(cfg: dict, hs: pd.DataFrame | None, fp: dict | None, summaries: dict | None = None, layers_by_model: dict | None = None, edge_V: dict | None = None) -> dict:
    """每个模型边界内：客户热点数、本系统积水点数与体积、DMT 溢流人孔数；热点到最近溢流人孔的距离（命中率）。"""
    import geopandas as gpd
    from shapely.geometry import Point
    P = params(cfg); res = {}
    for name in P["models"]:
        L = layers_by_model.get(name) if layers_by_model else load_model(cfg, name)
        if not L or "catchments" not in L:
            continue
        b, ha = boundary(L["catchments"])
        bg = gpd.GeoSeries([b], crs=P["crs"]).to_crs("EPSG:4326").iloc[0]
        R = {"model": name, "area": P["models"][name].get("area"), "boundary_ha": round(ha, 1), "parsons_flood_m3": P["models"][name].get("parsons_flood_m3")}
        if "conduits" in L:
            R["dmt_conduit_km"] = round(float(L["conduits"].geometry.length.sum()) / 1000, 1)
        # 客户热点
        if hs is not None and len(hs):
            h = hs.dropna(subset=["lon", "lat"])
            inside = h[[bg.contains(Point(x, y)) for x, y in zip(h["lon"], h["lat"])]]
            R["hotspots_inside"] = {"n": int(len(inside)), "ids": inside["hotspot_id"].astype(str).tolist(), "by_criticality": inside["criticality"].value_counts().to_dict() if "criticality" in inside.columns else None}
            if "manholes" in L and "EVER_OF" in L["manholes"].columns and len(inside):
                m = L["manholes"]; of = m[m["EVER_OF"].astype(str).str.lower().isin(["true", "1", "yes"])]
                if len(of):
                    from scipy.spatial import cKDTree
                    from pyproj import Transformer
                    tr = Transformer.from_crs("EPSG:4326", P["crs"], always_xy=True)
                    X, Y = tr.transform(inside["lon"].values.astype(float), inside["lat"].values.astype(float))
                    d_of, _ = cKDTree(np.c_[of.geometry.x.values, of.geometry.y.values]).query(np.c_[X, Y])
                    d_all, _ = cKDTree(np.c_[m.geometry.x.values, m.geometry.y.values]).query(np.c_[X, Y])
                    rad = P["overflow_radius_m"]
                    ours_sp = (pd.to_numeric(inside["nf_spill_m3_10"], errors="coerce").fillna(0) > 0).values if "nf_spill_m3_10" in inside.columns else np.zeros(len(inside), bool)
                    ours_bw = inside["nf_backwater_10"].fillna(False).astype(bool).values if "nf_backwater_10" in inside.columns else np.zeros(len(inside), bool)
                    ours = ours_sp | ours_bw; dmt = d_of <= rad
                    R["hotspot_vs_overflow"] = {"radius_m": rad, "within": int(dmt.sum()), "n": int(len(inside)), "median_dist_m": round(float(np.median(d_of)), 0),
                                                "nearest_manhole_median_m": round(float(np.median(d_all)), 0),
                                                "concordance": {"ours_flagged": int(ours.sum()), "dmt_near": int(dmt.sum()), "both": int((ours & dmt).sum()), "ours_only": int((ours & ~dmt).sum()), "dmt_only": int((~ours & dmt).sum()), "neither": int((~ours & ~dmt).sum())} if ("nf_spill_m3_10" in inside.columns) else None,
                                                "per_hotspot": [{"hotspot_id": str(i), "criticality": c, "dist_overflow_m": round(float(a), 0), "dist_manhole_m": round(float(z), 0), "our_spill": bool(x), "our_backwater": bool(y)}
                                                                for i, c, a, z, x, y in zip(inside["hotspot_id"], inside.get("criticality", [None] * len(inside)), d_of, d_all, ours_sp, ours_bw)]}
        # 本系统管段（真实台账 + 当场算的能力比）落在边界内的份额
        es_p = ROOT / "out" / "state" / "es.parquet"; vx_p = ROOT / "out" / "state" / "vertices.parquet"
        if es_p.exists() and vx_p.exists():
            es = pd.read_parquet(es_p, columns=[c for c in ["gid", "u", "v", "len_m", "pipe_diameter", "ratio_design", "compliant", "i_cap_mmh", "rational_valid", "is_active", "q_full"] if True])
            vx = pd.read_parquet(vx_p, columns=["id", "x", "y"]).set_index("id")
            ux = vx["x"].reindex(es["u"]).values; uy = vx["y"].reindex(es["u"]).values; wx = vx["x"].reindex(es["v"]).values; wy = vx["y"].reindex(es["v"]).values
            mx = (ux + wx) / 2; my = (uy + wy) / 2
            minx, miny, maxx, maxy = b.bounds
            cand = np.isfinite(mx) & (mx >= minx) & (mx <= maxx) & (my >= miny) & (my <= maxy)
            from shapely import contains_xy
            inside_e = np.zeros(len(es), dtype=bool); inside_e[cand] = contains_xy(b, mx[cand], my[cand])
            e = es[inside_e]
            if len(e):
                act = e[e["is_active"].astype(bool)] if "is_active" in e.columns else e
                rv = act[act["rational_valid"].astype(bool)] if "rational_valid" in act.columns else act
                i10 = float((cfg.get("idf") or {}).get("return_periods", {}).get("10", {}).get("i15_mmh", 57.7))
                R["our_network_inside"] = {"edges": int(len(act)), "length_km": round(float(act["len_m"].sum()) / 1000, 1), "dn_median_mm": float(_num(act["pipe_diameter"]).median()),
                                           "q_full_lps_median": (round(float(_num(act["q_full"]).median()) * 1000, 0) if "q_full" in act.columns else None),
                                           "over_capacity_10yr_share": (round(float((_num(rv["i_cap_mmh"]) < i10).mean()), 3) if len(rv) else None),
                                           "non_compliant_share": (round(float((~rv["compliant"].astype(bool)).mean()), 3) if len(rv) and "compliant" in rv.columns else None),
                                           "rational_valid": int(len(rv))}
                if edge_V:                                        # 本系统全网溢流路由：边界内管段的溢流量合计，按情景
                    R["our_flood_inside_m3"] = {k: round(float(pd.Series(v).reindex(act["gid"].values).fillna(0.0).sum()), 0) for k, v in edge_V.items()}
        # 本系统积水点
        if fp and fp.get("points"):
            pts = [p for p in fp["points"] if p.get("lon") is not None and bg.contains(Point(p["lon"], p["lat"]))]
            R["flood_points_inside"] = {"n": len(pts), "volume_m3": round(sum(float(p.get("volume_m3") or 0) for p in pts), 0),
                                        "by_source": dict(collections.Counter(p.get("source") for p in pts)), "ids": [p["fp_id"] for p in pts][:60],
                                        "scenario": (fp.get("scenario") or {}).get("scenario_key")}
        # DMT 结果
        if "manholes" in L and "EVER_OF" in L["manholes"].columns:
            m = L["manholes"]; of = m["EVER_OF"].astype(str).str.lower().isin(["true", "1", "yes"])
            R["dmt_overflow_manholes"] = int(of.sum()); R["dmt_manholes"] = int(len(m))
        if "catchments" in L and "RUNOFF_VOL" in L["catchments"].columns:
            R["dmt_runoff_vol_sum"] = round(float(_num(L["catchments"]["RUNOFF_VOL"]).sum()), 0)
        if summaries and name in summaries:
            R["dmt_ponds"] = {"n": summaries[name].get("ponds", {}).get("n"), "max_stor_sum_m3": summaries[name].get("ponds", {}).get("max_stor_sum_m3")}
        inp = Path(P["dir"]) / P["models"][name].get("inp", "")
        if inp.exists():
            r = parse_inp(inp); R["swmm_inflow_total_m3"] = r["inflow"]["total_m3"]; R["swmm_inflow_nodes"] = r["inflow"]["nodes"]
        res[name] = R
    return res


# ---------------------------------------------------------------- 输出
def to_geojson(cfg: dict, layers_by_model: dict) -> dict:
    """页面图层：模型边界（多边形）、临时塘（点，带容积）、溢流人孔（点）、泵（点）。EPSG:4326。"""
    import geopandas as gpd
    P = params(cfg); feats = []
    for name, L in layers_by_model.items():
        if "catchments" in L:
            b, ha = boundary(L["catchments"]); g = gpd.GeoSeries([b], crs=P["crs"]).to_crs("EPSG:4326").iloc[0].simplify(0.0002)
            feats.append({"type": "Feature", "geometry": json.loads(gpd.GeoSeries([g]).to_json())["features"][0]["geometry"], "properties": {"kind": "boundary", "model": name, "area_ha": round(ha, 1)}})
        if "ponds" in L:
            p = L["ponds"].to_crs("EPSG:4326")
            for _, r in p.iterrows():
                c = r.geometry.centroid
                feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(c.x, 6), round(c.y, 6)]},
                              "properties": {"kind": "pond", "model": name, "label": r.get("LABEL"), "max_stor_m3": (float(r["MAX_STOR"]) if "MAX_STOR" in p.columns and pd.notna(r.get("MAX_STOR")) else None)}})
        if "manholes" in L and "EVER_OF" in L["manholes"].columns:
            m = L["manholes"]; m = m[m["EVER_OF"].astype(str).str.lower().isin(["true", "1", "yes"])].to_crs("EPSG:4326")
            for _, r in m.iterrows():
                feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(r.geometry.x, 6), round(r.geometry.y, 6)]},
                              "properties": {"kind": "overflow", "model": name, "label": r.get("LABEL"), "flow_max_lps": (float(r["FLOW_MAX"]) if pd.notna(r.get("FLOW_MAX")) else None), "gnd_m": (float(r["ELEV_GND"]) if pd.notna(r.get("ELEV_GND")) else None)}})
        if "pumps" in L:
            q = L["pumps"].to_crs("EPSG:4326")
            for _, r in q.iterrows():
                feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(r.geometry.x, 6), round(r.geometry.y, 6)]},
                              "properties": {"kind": "pump", "model": name, "label": r.get("LABEL") if "LABEL" in q.columns else None, "definition": r.get("P_DEF") if "P_DEF" in q.columns else None}})
    return {"type": "FeatureCollection", "features": feats, "source": "DMT Hydraulic Model Data Export (2026-09-20)", "crs_note": "shapefile 无 .prj，按 EPSG:32640 处理"}


def _pond_key(label) -> str:
    """塘编号归一：图层写 P1 / STO-PO3，SWMM 写 PO-1，指的是同一个塘。"""
    t = re.sub(r"[^A-Z0-9]", "", str(label or "").upper()).replace("STO", "")
    m = re.match(r"^P(?:O)?(\d+)$", t)
    return f"PO{m.group(1)}" if m else t


def ponds_as_sites(summaries: dict) -> list[dict]:
    """临时塘 → 预置候选驻地（Parsons 2027 方案的泵放在塘边），并给出库容。

    库容优先用 SWMM 蓄水曲线，不用图层属性。图层的 max_stor_m3 只有一部分对：Shaliela
    10 个塘里 PO-2 / PO-3 与 Parsons 报告表 11 分毫不差，其余 8 个偏小 3–20 倍（PO-4 标
    3,489.5，而同一条记录的面积 17,715 m² × 设计水深 2 m = 35,431，报告也写 35,430.8）；
    RB3 七个塘该属性全是 null。SWMM 曲线体积则与报告表逐个吻合（Shaliela 10/10、RB3 7/7），
    所以拿它当准。2026-09-25 查 Parsons 四份汇水区评估报告时发现。
    """
    rows = []
    for name, S in summaries.items():
        po = S.get("ponds") or {}
        swmm = {_pond_key(x.get("label")): x for x in ((po.get("swmm_ponds") or {}).get("items") or [])}
        for p in po.get("items", []):
            if p.get("lon") is None:
                continue
            k = _pond_key(p.get("label"))
            w = swmm.get(k)
            vol, src = (w.get("volume_m3"), "swmm_curve") if w and w.get("volume_m3") else (p.get("max_stor_m3"), "layer_attr")
            rows.append({"site_id": f"POND-{name}-{p.get('label')}", "name": f"{p.get('label')} ({name})",
                         "lon": p["lon"], "lat": p["lat"], "max_stor_m3": vol, "stor_source": src,
                         "swmm_label": (w or {}).get("label"), "layer_max_stor_m3": p.get("max_stor_m3"),
                         "area_m2": p.get("area_m2"), "model": name, "area": S.get("area")})
    return rows


def run_all(cfg: dict, hs=None, fp=None, log=print) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    P = params(cfg); layers_by_model = {}; summaries = {}
    for name in P["models"]:
        if not (Path(P["dir"]) / name).exists():
            log(f"  {name}: 目录不存在，跳过"); continue
        L = load_model(cfg, name); layers_by_model[name] = L
        summaries[name] = layer_summary(cfg, name, L)
        inp = Path(P["dir"]) / P["models"][name].get("inp", "")
        if inp.exists():
            r = parse_inp(inp); summaries[name]["swmm"] = {"options": r["options"], "counts": r["counts"], "inflow": r["inflow"], "rain": r["rain"], "storage_volumes": r.get("storage_volumes", [])}
            sp = [x for x in r.get("storage_volumes", []) if x["is_pond"]]
            po = summaries[name].setdefault("ponds", {"n": 0, "max_stor_sum_m3": 0, "items": []})
            po["swmm_ponds"] = {"n": len(sp), "volume_sum_m3": round(sum(x["volume_m3"] for x in sp), 0), "items": sp}
            if not po.get("max_stor_sum_m3"):
                po["max_stor_sum_m3"] = po["swmm_ponds"]["volume_sum_m3"]; po["max_stor_source"] = "swmm_curves"
        log(f"  {name}: {summaries[name]['layers']} 边界 {summaries[name].get('boundary_ha')} ha")
    (OUT / "summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    try:
        hy = event_hyetographs(cfg); log(f"  实测雨型 → out/dmt_model/rain_events.json：{ {k: sorted(v) for k, v in hy.items()} }")
    except Exception as ex:
        log(f"  实测雨型导出跳过：{str(ex)[:120]}")
    gj = to_geojson(cfg, layers_by_model)
    (ROOT / "console" / "data").mkdir(parents=True, exist_ok=True)
    (ROOT / "console" / "data" / "dmt_model.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    (OUT / "ponds_sites.json").write_text(json.dumps(ponds_as_sites(summaries), ensure_ascii=False, indent=1), encoding="utf-8")
    edge_V = None
    es_p = ROOT / "out" / "state" / "es.parquet"
    if es_p.exists():
        try:
            from . import networkflood
            es = pd.read_parquet(es_p); prep = networkflood.prepare(es, log=log)
            keys = [k for k in ("10", "10cc", "2024-04", "2026-03") if k in __import__("swbrain.scenarios", fromlist=["keys"]).keys(cfg)]
            edge_V = {k: networkflood.run(cfg, prep, k)["edge_V"] for k in keys}
            log(f"  全网溢流量：{ {k: round(float(v.sum())) for k, v in edge_V.items()} }")
        except Exception as ex:
            log(f"  全网溢流路由跳过：{str(ex)[:120]}")
    cmp_ = compare(cfg, hs, fp, summaries, layers_by_model, edge_V) if (hs is not None or fp is not None) else {}
    if cmp_:
        (OUT / "compare.json").write_text(json.dumps(cmp_, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        (ROOT / "console" / "data" / "dmt_compare.json").write_text(json.dumps(cmp_, ensure_ascii=False, default=str), encoding="utf-8")
    return {"summaries": summaries, "compare": cmp_, "geojson_features": len(gj["features"])}


def main(argv=None):
    from . import db, hotspots as hsm
    ap = argparse.ArgumentParser(description="DMT 1D 模型导出 → 口径与对表")
    ap.add_argument("--config", default=None)
    ap.add_argument("--rp", default="10", help="对表用哪一档本系统积水点（exchange/predictions/flood_points.system-<key>.json）")
    a = ap.parse_args(argv)
    cfg = db.load_config(a.config)
    if not available(cfg):
        print("config [dmt_model].dir 未配置或不存在"); return 1
    hs = hsm.load(ROOT / cfg["hotspots"]["file"]) if "hotspots" in cfg else None
    enr = ROOT / "out" / "state" / "hotspots_enriched.parquet"        # run.py 挂了匹配管段的溢流/顶托标记的版本
    if enr.exists():
        hs = pd.read_parquet(enr)
    fpp = ROOT / "exchange" / "predictions" / f"flood_points.system-{a.rp}.json"
    fp = json.loads(fpp.read_text(encoding="utf-8")) if fpp.exists() else None
    r = run_all(cfg, hs, fp)
    for name, S in r["summaries"].items():
        mh = S.get("manholes", {}); po = S.get("ponds", {})
        print(f"{name}: 边界 {S.get('boundary_ha')} ha · 管道 {S.get('conduits', {}).get('length_km')} km · 人孔 {mh.get('n')}（溢流 {mh.get('overflowed')}）· 塘 {po.get('n')} 合计 {po.get('max_stor_sum_m3')} m³ · 泵 {S.get('pumps', {}).get('n')}")
        for x in S.get("swmm", {}).get("rain", []):
            if x.get("scenario_key"):
                print(f"    雨 {x['scenario_key']:8s} {x['depth_mm']:7.2f} mm / {x['duration_h']:5.1f} h  最大15min {x['max15_mm']:5.2f} mm（{x['i15_mmh']:5.1f} mm/h）  ← {x['name'][:60]}")
    for name, C in r["compare"].items():
        hv = C.get("hotspot_vs_overflow") or {}
        print(f"{name} 对表：热点 {C.get('hotspots_inside', {}).get('n')} 个，{hv.get('within')}/{hv.get('n')} 在溢流人孔 {hv.get('radius_m')} m 内（中位 {hv.get('median_dist_m')} m）；本系统积水点 {C.get('flood_points_inside', {}).get('n')} 个 {C.get('flood_points_inside', {}).get('volume_m3')} m³；DMT 溢流人孔 {C.get('dmt_overflow_manholes')}/{C.get('dmt_manholes')}，SWMM 入流合计 {C.get('swmm_inflow_total_m3')} m³")
        if C.get("our_flood_inside_m3"):
            print(f"    全网溢流路由（边界内）: {C['our_flood_inside_m3']}  vs Parsons: {C.get('parsons_flood_m3')}")
        cc = (C.get("hotspot_vs_overflow") or {}).get("concordance")
        if cc:
            print(f"    热点一致性（本系统溢流/顶托 vs DMT 溢流人孔 {hv.get('radius_m')} m 内）: 都标 {cc['both']}，只本系统 {cc['ours_only']}，只 DMT {cc['dmt_only']}，都没 {cc['neither']}")
    print(f"→ {OUT} · console/data/dmt_model.geojson（{r['geojson_features']} 要素）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
