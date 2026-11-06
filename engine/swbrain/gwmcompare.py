"""世界模型推演积水 × 客户热点 × 本系统积水点：三方对照（`/api/gwm_points`）。

世界模型（GWM）按 250 m 格网推演，聚成 flood_points 落在 exchange/predictions/flood_points.gwm-*.json；
这里按情景挑出对应重现期（或雨量最接近）的一次推演，把每个推演积水核与客户热点、本系统积水点做空间对照，
给地图一层能与客户热点并排看的多边形，以及"谁印证了谁"的统计。只读文件、不重算模型。
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import scenarios

ROOT = Path(__file__).resolve().parents[1]
_TR = None


def _tr():
    global _TR
    if _TR is None:
        from pyproj import Transformer
        _TR = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    return _TR


def _xy(lon, lat) -> np.ndarray:
    x, y = _tr().transform(np.asarray(lon, float), np.asarray(lat, float))
    return np.c_[x, y]


# ---------------------------------------------------------------- 文件挑选
def gwm_files(cfg: dict, pred_dir: Path | None = None) -> list[dict]:
    """预测目录里所有世界模型（model.type == world）的积水点文件，按修改时间排序。"""
    d = pred_dir or (ROOT / cfg["exchange"]["dir"] / "predictions")
    out = []
    for p in sorted(d.glob("*flood_points*.json"), key=lambda q: q.stat().st_mtime) if d.exists() else []:
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (j.get("model") or {}).get("type") != "world":
            continue
        sc = j.get("scenario") or {}
        rp = sc.get("return_period_yr")
        out.append({"path": str(p), "run_id": j.get("run_id"), "rp": (int(rp) if rp is not None else None), "rain_mm": sc.get("rain_depth_mm"),
                    "n": len(j.get("points") or []), "mtime": p.stat().st_mtime, "name": sc.get("name"), "name_en": sc.get("name_en")})
    return out


def pick_file(cfg: dict, key, files: list[dict] | None = None) -> tuple[dict | None, str]:
    """按情景挑推演：同重现期的取雨量最接近、再取最新；没有同重现期的取 24 h 雨量最接近的一次（标 nearest_rain）。"""
    files = gwm_files(cfg) if files is None else files
    if not files:
        return None, "none"
    scen = scenarios.get(cfg, key)
    p24 = float(scen.get("p24_mm") or 0.0)
    rp = scen.get("return_period_yr")
    if rp is None and str(key).isdigit():
        rp = int(key)
    exact = [f for f in files if rp is not None and f["rp"] == rp]
    if exact:
        best = sorted(exact, key=lambda f: (abs(float(f["rain_mm"] or 0) - p24), -f["mtime"]))[0]
        return best, "return_period"
    best = sorted(files, key=lambda f: (abs(float(f["rain_mm"] or 0) - p24), -f["mtime"]))[0]
    return best, "nearest_rain"


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 格网覆盖
def grid_inside(lon, lat, runs_dir: str | Path, run_id: str) -> np.ndarray | None:
    """各点是否落在该次推演的陆地格内（None = 本机没有结果目录，判不了）。"""
    p = Path(str(runs_dir)) / str(run_id) / "surface_depth_labels_250m.npz"
    if not runs_dir or not p.exists():
        return None
    z = np.load(p)
    x, y, land = z["x"], z["y"], z["land_mask"]
    R, C = land.shape
    xy = _xy(lon, lat)
    ci = np.searchsorted(x, xy[:, 0]) - 1
    ri = np.searchsorted(-y, -xy[:, 1]) - 1
    ok = (ci >= 0) & (ci < C) & (ri >= 0) & (ri < R)
    ins = np.zeros(len(xy), bool)
    ins[ok] = land[ri[ok], ci[ok]].astype(bool)
    return ins


# ---------------------------------------------------------------- 对照
def _polys_m(pts: list[dict]):
    from shapely.geometry import shape, Point
    from shapely.ops import transform
    tr = _tr().transform
    out = []
    for p in pts:
        g = p.get("geometry")
        if g:
            out.append(transform(tr, shape(g)))
        else:                                                   # 只有中心点 + 面积：按等面积圆
            r = float(np.sqrt(max(float(p.get("area_m2") or 1.0), 1.0) / np.pi))
            out.append(Point(*_xy([p["lon"]], [p["lat"]])[0]).buffer(r))
    return out


def _nearest(tree, pts_xy: np.ndarray, radius_m: float) -> tuple[np.ndarray, np.ndarray]:
    """每个点最近的推演多边形（≤ radius_m），返回 (多边形序号或 -1, 距离 m；在多边形内为 0)。"""
    from shapely.geometry import Point
    idx = np.full(len(pts_xy), -1); dist = np.full(len(pts_xy), np.inf)
    if not len(pts_xy) or tree is None:
        return idx, dist
    geoms = [Point(x, y) for x, y in pts_xy]
    pairs, d = tree.query_nearest(geoms, max_distance=radius_m, return_distance=True, all_matches=False)
    idx[pairs[0]] = pairs[1]; dist[pairs[0]] = d
    return idx, dist


def compare(gwm_fp: dict, hs: pd.DataFrame | None, sys_fp: dict | None, radius_m: float = 300.0, inside_grid: np.ndarray | None = None) -> tuple[list[dict], dict, list[dict]]:
    """返回 (每个推演点的标签, 统计, 每个客户热点的命中)。

    标签 agreement：both = 有客户热点也有本系统积水点在半径内；hotspot = 只有客户热点；system = 只有本系统点；model_only = 两边都没有。
    """
    from shapely.strtree import STRtree
    pts = gwm_fp.get("points") or []
    polys = _polys_m(pts)
    tree = STRtree(polys) if polys else None
    tags = [{"fp_id": p.get("fp_id"), "hs_ids": [], "hs_crit": [], "hs_inside_n": 0, "sys_ids": [], "sys_inside_n": 0} for p in pts]
    hs_hits: list[dict] = []
    stats: dict = {"radius_m": float(radius_m), "gwm": {"n": len(pts), "volume_m3": float(sum(float(p.get("volume_m3") or 0) for p in pts))}}

    if hs is not None and len(hs):
        h = hs.dropna(subset=["lon", "lat"]).reset_index(drop=True)
        idx, dist = _nearest(tree, _xy(h["lon"].values, h["lat"].values), radius_m)
        ins = inside_grid if inside_grid is not None and len(inside_grid) == len(h) else None
        by_c: dict[str, dict] = {}
        for i, row in h.iterrows():
            c = str(row.get("criticality") or "")
            bc = by_c.setdefault(c, {"n": 0, "covered": 0, "inside": 0, "outside_grid": 0})
            bc["n"] += 1
            hit = idx[i] >= 0
            out_g = (ins is not None and not bool(ins[i]))
            if out_g:
                bc["outside_grid"] += 1
            if hit:
                bc["covered"] += 1
                if dist[i] <= 0.5:
                    bc["inside"] += 1
                tg = tags[int(idx[i])]
                tg["hs_ids"].append(str(row.get("hotspot_id"))); tg["hs_crit"].append(c)
                if dist[i] <= 0.5:
                    tg["hs_inside_n"] += 1
                hs_hits.append({"hotspot_id": str(row.get("hotspot_id")), "fp_id": pts[int(idx[i])].get("fp_id"), "dist_m": round(float(dist[i]), 0),
                                "depth_m": pts[int(idx[i])].get("max_depth_m"), "criticality": c})
        n = int(len(h)); cov = int((idx >= 0).sum()); insd = int(((idx >= 0) & (dist <= 0.5)).sum())
        og = int((~ins).sum()) if ins is not None else None
        eff = n - (og or 0)
        stats["hotspots"] = {"n": n, "covered": cov, "inside": insd, "outside_grid": og, "in_grid": (eff if ins is not None else None),
                             "covered_share": (round(cov / eff, 3) if eff else 0.0), "by_criticality": by_c}
    if sys_fp is not None and (sys_fp.get("points") or []):
        sp = sys_fp["points"]
        idx, dist = _nearest(tree, _xy([p["lon"] for p in sp], [p["lat"] for p in sp]), radius_m)
        for i, p in enumerate(sp):
            if idx[i] >= 0:
                tg = tags[int(idx[i])]; tg["sys_ids"].append(str(p.get("fp_id")))
                if dist[i] <= 0.5:
                    tg["sys_inside_n"] += 1
        stats["system"] = {"n": len(sp), "covered": int((idx >= 0).sum()), "covered_share": round(float((idx >= 0).mean()), 3) if len(sp) else 0.0,
                           "scenario": (sys_fp.get("scenario") or {}).get("name"), "run_id": sys_fp.get("run_id")}
    # 每个推演点的归类
    cnt = {"both": 0, "hotspot": 0, "system": 0, "model_only": 0}; v_only = 0.0
    for tg, p in zip(tags, pts):
        a = "both" if (tg["hs_ids"] and tg["sys_ids"]) else ("hotspot" if tg["hs_ids"] else ("system" if tg["sys_ids"] else "model_only"))
        tg["agreement"] = a; tg["hs_n"] = len(tg["hs_ids"]); tg["sys_n"] = len(tg["sys_ids"])
        cnt[a] += 1
        if a == "model_only":
            v_only += float(p.get("volume_m3") or 0)
    stats["gwm"].update({"agreement": cnt, "with_hotspot": cnt["both"] + cnt["hotspot"], "with_system": cnt["both"] + cnt["system"], "model_only_volume_m3": round(v_only, 0)})
    return tags, stats, hs_hits


def to_geojson(gwm_fp: dict, tags: list[dict]) -> dict:
    """推演积水核多边形 + 对照标签（给控制台画图层）。"""
    feats = []
    for p, tg in zip(gwm_fp.get("points") or [], tags):
        g = p.get("geometry")
        if not g:
            g = {"type": "Point", "coordinates": [p["lon"], p["lat"]]}
        gw = p.get("gwm") or {}
        props = {"fp_id": p.get("fp_id"), "lon": p.get("lon"), "lat": p.get("lat"), "max_depth_m": p.get("max_depth_m"), "volume_m3": p.get("volume_m3"), "area_m2": p.get("area_m2"),
                 "duration_min": p.get("duration_min"), "first_wet_min": gw.get("first_wet_min"), "peak_min": gw.get("peak_min"), "cells": gw.get("cells"),
                 "note": p.get("note"), "note_en": p.get("note_en"),
                 "agreement": tg["agreement"], "hs_n": tg["hs_n"], "hs_inside_n": tg["hs_inside_n"], "hs_ids": tg["hs_ids"][:8], "hs_crit": tg["hs_crit"][:8],
                 "sys_n": tg["sys_n"], "sys_ids": tg["sys_ids"][:6]}
        feats.append({"type": "Feature", "geometry": g, "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def public(cfg: dict, key, hs: pd.DataFrame | None, sys_fp: dict | None, files: list[dict] | None = None, radius_m: float | None = None) -> dict:
    """某情景的三方对照结果（接口返回体）。没有任何世界模型文件时给 error。"""
    files = gwm_files(cfg) if files is None else files
    f, how = pick_file(cfg, key, files)
    avail = sorted({x["rp"] for x in files if x["rp"] is not None})
    if f is None:
        return {"error": "没有世界模型推演结果文件（exchange/predictions/flood_points.gwm-*.json）", "available_rps": avail}
    fp = load(f["path"])
    r = float(radius_m if radius_m is not None else (cfg.get("hotspots") or {}).get("match_radius_m", 300))
    ins = None
    if hs is not None and len(hs):
        h = hs.dropna(subset=["lon", "lat"])
        ins = grid_inside(h["lon"].values, h["lat"].values, (cfg.get("gwm") or {}).get("runs_dir", ""), fp.get("run_id", ""))
    tags, stats, hits = compare(fp, hs, sys_fp, r, ins)
    scen = scenarios.get(cfg, key)
    md = fp.get("model") or {}
    return {"key": str(key), "run_id": fp.get("run_id"), "matched_by": how, "available_rps": avail,
            "scenario": {"name": (fp.get("scenario") or {}).get("name"), "name_en": (fp.get("scenario") or {}).get("name_en"), "rain_mm": (fp.get("scenario") or {}).get("rain_depth_mm"),
                         "return_period_yr": f["rp"], "ours_p24_mm": scen.get("p24_mm"), "ours_label": scen.get("label"), "ours_label_en": scen.get("label_en")},
            "model": {"name": md.get("name"), "version": md.get("version"), "grid": md.get("grid"), "known_behaviour": md.get("known_behaviour"), "known_behaviour_en": md.get("known_behaviour_en"),
                      "created_at": md.get("created_at")},
            "aggregation": fp.get("aggregation"), "stats": stats, "hotspot_hits": hits, "geojson": to_geojson(fp, tags)}
