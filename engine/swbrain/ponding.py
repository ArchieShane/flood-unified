# -*- coding: utf-8 -*-
"""二维模拟积水点：从一二维结果（c2d/{run}_{态}_2d.npz）判出一片片积水，每片一行。

和系统里另外两种「点」分开：
  · 客户热点（hotspots.geojson，ADM-xxx）——客户报的、淹过的地方；
  · floodpoints.py 的系统积水点（fp_id 用的就是客户热点编号）——第三道防线按客户热点估的待排体积。
这里的点是模型算出来的，编号 SIM-{态}-{序号}，序号按峰值积水量从大到小，只在同一运行、同一态、同一组阈值内有效。

判定（阈值可调；缺省值是 2026-10-18 RB-01 试算用的，尚未和客户对齐）：
  · 单元「积水」= 全程最大水深 ≥ depth，塘的占地不算（这个运行所有态的塘的并集）
  · 积水单元按三角网共边连成片；拿不到原生 .sww 网格时用单元形心近邻近似（结果标 adjacency = "approx"）
  · 保留 片面积 ≥ min_area 且 持续 ≥ min_dur；持续 = 片内水深 ≥ depth 的面积 ≥ min_area 的帧数 × 帧间隔
  · 峰值积水量 = 逐帧 Σ(片内单元水深 × 单元面积) 的最大值；退水 = 峰值起到 ≥depth 面积跌破 min_area；末帧还没退记 None
  · 片按全程最大水深包络切分：不同时段的两摊水若在包络上相连，会算成一片
接口：/api/ponding；本体动作 hydro_ponding；命令行 python -m swbrain.ponding --name RB01_H60
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE = ROOT / "out" / "ponding" / "cache"
DEFAULTS = {"depth": 0.15, "min_area": 100.0, "min_dur": 0.5}
R_NEAR = 200.0
RUN_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
STATE_RE = re.compile(r"^[A-Za-z0-9]{1,12}$")
VERSION = 1


def work_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("SWBRAIN_SWMM_WORK", r"D:\swbrain_work\swmm"))


def c2d() -> pathlib.Path:
    return work_dir() / "c2d"


def available(run: str | None = None) -> dict[str, list[str]]:
    """{运行名: [有二维结果的态]}"""
    out: dict[str, list[str]] = {}
    for p in sorted(c2d().glob("*_2d.npz")):
        m = re.match(r"^(.+)_([A-Za-z0-9]+)_2d$", p.stem)
        if m and (run is None or m.group(1) == run):
            out.setdefault(m.group(1), []).append(m.group(2))
    return out


def check_args(run: str, state: str) -> pathlib.Path:
    if not RUN_RE.match(run or "") or not STATE_RE.match(state or ""):
        raise ValueError("运行名 / 态只能是字母、数字、下划线")
    p = c2d() / f"{run}_{state}_2d.npz"
    if not p.exists():
        have = available(run).get(run)
        raise FileNotFoundError(f"没有 {run} {state} 的二维结果" + (f"（这个运行有：{', '.join(have)}）" if have else "（没有这个运行）"))
    return p


# ---------------------------------------------------------------- 核心（纯数组，便于测试）
def mesh_adjacency(tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """三角形共边的单元对 (a, b)"""
    n = len(tris)
    e = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    t = np.tile(np.arange(n), 3)
    o = np.lexsort((e[:, 1], e[:, 0])); e, t = e[o], t[o]
    same = (e[1:] == e[:-1]).all(1)
    return t[:-1][same], t[1:][same]


def approx_adjacency(xy: np.ndarray, area: np.ndarray, cand: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """没有网格时：候选单元里形心距离 ≤ 1.2 × 两单元 √面积 的均值 的算相邻。
    1.2 是拿 RB01_H60 L1 的真网格标定的：共边邻居的这个比值 99.9% ≤ 1.23；取 1.2 时 ≥0.15 m 的片数 515 vs 网格 509、其中 ≥100 m² 的 350 vs 350"""
    from scipy.spatial import cKDTree
    idx = np.flatnonzero(cand)
    if len(idx) < 2:
        return np.zeros(0, int), np.zeros(0, int)
    s = np.sqrt(area[idx])
    pairs = cKDTree(xy[idx]).query_pairs(r=1.2 * float(s.max()), output_type="ndarray")
    if not len(pairs):
        return np.zeros(0, int), np.zeros(0, int)
    d = np.hypot(*(xy[idx[pairs[:, 0]]] - xy[idx[pairs[:, 1]]]).T)
    ok = d <= 0.6 * (s[pairs[:, 0]] + s[pairs[:, 1]])
    return idx[pairs[ok, 0]], idx[pairs[ok, 1]]


def label_patches(n: int, a: np.ndarray, b: np.ndarray, keep: np.ndarray) -> np.ndarray:
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    m = keep[a] & keep[b]
    g = coo_matrix((np.ones(int(m.sum())), (a[m], b[m])), shape=(n, n))
    _, lab = connected_components(g, directed=False)
    return np.where(keep, lab, -1)


def extract(xy, area, elev, hmax, frames, t_h, depth=0.15, min_area=100.0, min_dur=0.5, pond=None, tris=None, state="S"):
    """返回 (积水点列表[按峰值积水量降序，带 _cells], 判出的片总数, 邻接方式)"""
    xy = np.asarray(xy, float); area = np.asarray(area, float); hmax = np.asarray(hmax, float)
    fr = np.asarray(frames, np.float32); t_h = np.asarray(t_h, float)
    n = len(xy)
    dt = float(np.diff(t_h).mean()) if len(t_h) > 1 else 0.0
    keep = (hmax >= depth) & (~pond if pond is not None else True)
    if tris is not None:
        a, b = mesh_adjacency(np.asarray(tris)); how = "mesh"
    else:
        a, b = approx_adjacency(xy, area, keep); how = "approx"
    lab = label_patches(n, a, b, keep)
    cells = np.flatnonzero(lab >= 0)
    if not len(cells):
        return [], 0, how
    _, inv = np.unique(lab[cells], return_inverse=True)
    groups = np.split(cells[np.argsort(inv, kind="stable")], np.cumsum(np.bincount(inv))[:-1])
    out = []
    for c in groups:
        A = float(area[c].sum())
        if A < min_area:
            continue
        wet = ((fr[:, c] >= depth) * area[c]).sum(1) >= min_area
        dur = float(wet.sum()) * dt
        if dur < min_dur:
            continue
        V = (fr[:, c] * area[c]).sum(1)
        k = int(V.argmax())
        after = np.flatnonzero(~wet[k:])
        deep = int(c[int(hmax[c].argmax())])
        cen = (xy[c] * area[c, None]).sum(0) / A
        out.append({"state": state, "deep_xy": xy[deep].tolist(), "cen_xy": cen.tolist(),
                    "hmax_m": round(float(hmax[deep]), 2), "z_m": round(float(elev[deep]), 2),
                    "area_m2": round(A), "area30_m2": round(float(area[c][hmax[c] >= 0.30].sum())),
                    "dur_h": round(dur, 2), "vol_peak_m3": round(float(V[k])), "t_peak_h": round(float(t_h[k]), 2),
                    "recede_h": round(float(t_h[k + after[0]] - t_h[k]), 2) if len(after) else None,
                    "vol_end_m3": round(float(V[-1])), "_cells": c})
    out.sort(key=lambda r: -r["vol_peak_m3"])
    for i, r in enumerate(out, 1):
        r["id"] = f"SIM-{state}-{i:03d}"
    return out, len(groups), how


# ---------------------------------------------------------------- 读结果、出带几何的记录
def _read_sww(run, state, n_cells, xy):
    """原生 ANUGA 输出（c2d/anuga_out/{run}_{态}.sww）的三角形；单元数或顺序对不上就不用"""
    p = c2d() / "anuga_out" / f"{run}_{state}.sww"
    if not p.exists():
        return None, None, None
    from scipy.io import netcdf_file
    with netcdf_file(str(p), "r", mmap=False) as D:
        if D.variables["volumes"].shape[0] != n_cells:
            return None, None, None
        x = D.variables["x"].data.astype(float) + float(D.xllcorner)
        y = D.variables["y"].data.astype(float) + float(D.yllcorner)
        tri = D.variables["volumes"].data.astype(np.int64)
    if not np.allclose(np.c_[x[tri].mean(1), y[tri].mean(1)], xy, atol=0.5):
        return None, None, None
    return tri, x, y


def _pond_mask(run, xy, state=None):
    """塘占地（不算积水）。缺省（state=None）：这个运行所有态的塘占地取并集（原做法）；
    给 state：只用这个态自己的地表塘，跳过地下调蓄池——与判据评估（l2opt_eval2d）和 L3 承接（rda_l3）一致。
    2026-10-26 加：RD-A 的并集带进了弃用方案 OPT20F 的 40 个明塘，L1 也被扣掉 1,444 个单元（RB01_H61 L1 漏 569 个积水单元、C1_M61 L1 257 个）。
    2026-10-26 用户同意：对外的入口（export / export_delivery / 命令行 / /api/ponding / 积水点动作 / ponding_map）缺省改为 state；
    compute() 本身缺省仍是 run——优化工具（l2opt_candidates / l2opt_variant / l2s_sumps_v3 / l2opt_add_sumps）靠它，正在跑的优化链保持口径不变。"""
    from matplotlib.path import Path as MPath
    m = np.zeros(len(xy), bool)
    for p in ([c2d() / f"pilot_{run}_{state}.json"] if state else c2d().glob(f"pilot_{run}_*.json")):
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for pd in meta.get("ponds", []) or []:
            if state and pd.get("underground"):
                continue
            fp = pd.get("footprint") or {}
            polys = fp.get("coordinates", []) if fp.get("type") == "MultiPolygon" else [fp.get("coordinates", [])]
            for poly in polys:
                if poly and len(poly[0]) >= 3:
                    m |= MPath(np.array(poly[0])[:, :2]).contains_points(xy)
    return m


def _domain(run):
    for st in ("L1", None):
        p = c2d() / f"pilot_{run}_{st}.json" if st else next(iter(sorted(c2d().glob(f"pilot_{run}_*.json"))), None)
        if p and p.exists():
            d = json.loads(p.read_text(encoding="utf-8")).get("domain_polygon")
            if d:
                return d
    return None


def _hotspots(bounds_utm):
    """计算域外包框外扩 500 m 内的客户热点（UTM 40N 坐标）"""
    p = ROOT / "console" / "data" / "hotspots.geojson"
    if not p.exists():
        return [], np.zeros((0, 2))
    from pyproj import Transformer
    T = Transformer.from_crs(4326, 32640, always_xy=True)
    ids, pts = [], []
    x0, y0, x1, y1 = bounds_utm
    for f in json.loads(p.read_text(encoding="utf-8")).get("features", []):
        lon, lat = f["geometry"]["coordinates"][:2]
        x, y = T.transform(lon, lat)
        if x0 - 500 <= x <= x1 + 500 and y0 - 500 <= y <= y1 + 500:
            ids.append(f["properties"]["hotspot_id"]); pts.append((x, y))
    return ids, np.array(pts).reshape(-1, 2)


def _key(run, state, depth, min_area, min_dur):
    return f"{run}_{state}_d{depth:g}_a{min_area:g}_t{min_dur:g}"


def compute(run: str, state: str, depth: float = DEFAULTS["depth"], min_area: float = DEFAULTS["min_area"],
            min_dur: float = DEFAULTS["min_dur"], geometry: bool = True, use_cache: bool = True, pond_scope: str = "run") -> dict:
    """一个运行、一个态、一组阈值的积水点（WGS84 坐标）。结果按 npz 修改时间缓存到 out/ponding/cache/。"""
    npz = check_args(run, state)
    depth, min_area, min_dur = float(depth), float(min_area), float(min_dur)
    if not (0.01 <= depth <= 5 and 0 <= min_area <= 1e6 and 0 <= min_dur <= 72):
        raise ValueError("阈值超范围：水深 0.01–5 m、面积 0–1e6 m²、持续 0–72 h")
    if pond_scope not in ("run", "state"):
        raise ValueError("pond_scope 只能是 run（所有态塘占地并集）或 state（本态地表塘）")
    key = _key(run, state, depth, min_area, min_dur) + ("_pstate" if pond_scope == "state" else "")
    cf = CACHE / f"{key}.json"
    stamp = f"{VERSION}:{npz.stat().st_mtime_ns}"
    if use_cache and cf.exists():
        try:
            r = json.loads(cf.read_text(encoding="utf-8"))
            # 缓存可能是不带几何算的（geometry=False）：要几何而缓存里没有就重算（2026-10-19 修：之前直接返回，拿不到积水范围）
            if r.get("stamp") == stamp and (not geometry or all("geom" in f for f in r["feats"])):
                return r if geometry else _strip(r)
        except (OSError, ValueError):
            pass
    from pyproj import Transformer
    T = Transformer.from_crs(32640, 4326, always_xy=True)
    z = np.load(npz)
    xy = z["xy"].astype(float); area = z["area"].astype(float)
    tri, vx, vy = _read_sww(run, state, len(xy), xy)
    pond = _pond_mask(run, xy, state if pond_scope == "state" else None)
    feats, n_patch, how = extract(xy, area, z["elev"].astype(float), z["hmax"].astype(float), z["frames"], z["t_h"],
                                  depth, min_area, min_dur, pond=pond, tris=tri, state=state)
    hid, hxy = _hotspots((xy[:, 0].min(), xy[:, 1].min(), xy[:, 0].max(), xy[:, 1].max()))
    from scipy.spatial import cKDTree
    from shapely.geometry import MultiPoint, Polygon, mapping
    from shapely.ops import transform as stransform, unary_union
    _save_cells(key, stamp, [f["id"] for f in feats], [f["_cells"] for f in feats])
    for f in feats:
        c = f.pop("_cells")
        if len(hid):
            d = cKDTree(xy[c]).query(hxy)[0]          # 热点到片内最近单元
            o = np.argsort(d)
            f["near"] = [[hid[i], round(float(d[i]))] for i in o if d[i] <= R_NEAR]
            f["nearest"] = [hid[o[0]], round(float(d[o[0]]))]
        else:
            f["near"], f["nearest"] = [], None
        f["lon"], f["lat"] = [round(v, 6) for v in T.transform(*f.pop("deep_xy"))]
        f["clon"], f["clat"] = [round(v, 6) for v in T.transform(*f.pop("cen_xy"))]
        if geometry:
            if tri is not None:
                g = unary_union([Polygon(np.c_[vx[t], vy[t]]) for t in tri[c]])
            else:
                g = unary_union(MultiPoint(xy[c]).geoms).buffer(float(np.sqrt(2 * area[c].max())) * 0.6)
            g = stransform(lambda x, y: T.transform(x, y), g.buffer(0)).simplify(0.00002)
            f["geom"] = json.loads(json.dumps(mapping(g)), parse_float=lambda s: round(float(s), 6))
    dom = _domain(run)
    r = {"run": run, "state": state, "params": {"depth": depth, "min_area": min_area, "min_dur": min_dur, "pond_scope": pond_scope},
         "adjacency": how, "n_patches": n_patch, "n": len(feats), "vol_total_m3": int(sum(f["vol_peak_m3"] for f in feats)),
         "n_not_receded": sum(f["recede_h"] is None for f in feats), "t_end_h": round(float(z["t_h"][-1]), 2),
         "domain": [[round(a, 6) for a in T.transform(*p[:2])][::-1] for p in dom] if dom else None,
         "hotspots_in_area": hid, "feats": feats, "stamp": stamp,
         "note": "编号 SIM-态-序号（按峰值积水量降序）只在本运行、本态、本组阈值内有效；不是客户热点编号。阈值未与客户对齐。"}
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps(r, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass
    return r if geometry else _strip(r)


def _strip(r):
    return {**r, "feats": [{k: v for k, v in f.items() if k != "geom"} for f in r["feats"]]}


# ---- 每片积水包含哪些网格单元（旁路缓存）：跨态对照、把别处判出的点挂回本模块的编号，都靠它 ----
def _save_cells(key, stamp, ids, cells):
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(CACHE / f"{key}_cells.npz", stamp=np.array(stamp), ids=np.array(ids, dtype=str),
                            off=np.cumsum([0] + [len(c) for c in cells]), cells=np.concatenate(cells) if cells else np.zeros(0, int))
    except OSError:
        pass


def cells(run, state, depth=DEFAULTS["depth"], min_area=DEFAULTS["min_area"], min_dur=DEFAULTS["min_dur"], pond_scope="state"):
    """→ {积水点编号: 单元下标数组}；旁路缓存缺失或过期就重算一次。"""
    key = _key(run, state, float(depth), float(min_area), float(min_dur)) + ("_pstate" if pond_scope == "state" else "")
    stamp = f"{VERSION}:{check_args(run, state).stat().st_mtime_ns}"
    f = CACHE / f"{key}_cells.npz"
    for attempt in (0, 1):
        if f.exists():
            z = np.load(f)
            if str(z["stamp"]) == stamp:
                o = z["off"]
                return {str(i): z["cells"][o[k]:o[k + 1]] for k, i in enumerate(z["ids"])}
        if attempt == 0:
            compute(run, state, depth, min_area, min_dur, geometry=False, use_cache=False, pond_scope=pond_scope)
    raise RuntimeError(f"拿不到 {key} 的单元表")


def compare(run, state, ref, depth=DEFAULTS["depth"], min_area=DEFAULTS["min_area"], min_dur=DEFAULTS["min_dur"], pond_scope="state",
            geometry=True):
    """本态积水点 × 基准态积水点（同一运行、同一网格、同一组阈值），按共有网格单元对应：
    每个本态点 → 对应的基准态点及重叠面积；基准态里本态已没有积水的点 = 已消除；本态里基准态没有的 = 新出现。"""
    a, b = check_args(run, state), check_args(run, ref)
    za, zb = np.load(a), np.load(b)
    if za["xy"].shape != zb["xy"].shape or not np.allclose(za["xy"], zb["xy"]):
        raise ValueError(f"{state} 与 {ref} 的二维网格不同，不能逐单元对照")
    area = za["area"].astype(float)
    ca, cb = cells(run, state, depth, min_area, min_dur, pond_scope), cells(run, ref, depth, min_area, min_dur, pond_scope)
    owner = np.full(len(area), -1); rid = list(cb)
    for k, i in enumerate(rid):
        owner[cb[i]] = k
    rb = compute(run, ref, depth, min_area, min_dur, geometry=geometry, pond_scope=pond_scope)
    fb = {f["id"]: f for f in rb["feats"]}
    match, hit = {}, set()
    for i, c in ca.items():
        o = owner[c]; o = o[o >= 0]
        if len(o):
            ks, inv = np.unique(o, return_inverse=True)
            ov = np.bincount(inv, weights=area[c][owner[c] >= 0])
            match[i] = [[rid[k], round(float(v))] for k, v in sorted(zip(ks, ov), key=lambda z: -z[1])]
            hit.update(rid[k] for k in ks)
        else:
            match[i] = []
    keep = ("id", "lon", "lat", "clon", "clat", "hmax_m", "area_m2", "vol_peak_m3", "recede_h", "near", "nearest") + (("geom",) if geometry else ())
    gone = [{k: fb[i].get(k) for k in keep} for i in rid if i not in hit]
    ref_of = {i: fb.get(m[0][0]) if m else None for i, m in match.items()}
    return {"ref": ref, "match": match,
            "ref_brief": {i: {"hmax_m": f["hmax_m"], "vol_peak_m3": f["vol_peak_m3"], "area_m2": f["area_m2"]} for i, f in fb.items()},
            "gone": gone,
            "summary": {"n_ref": len(rid), "n_cur": len(ca), "n_gone": len(gone), "n_new": sum(1 for m in match.values() if not m),
                        "n_kept_ref": len(hit), "vol_ref_m3": int(sum(f["vol_peak_m3"] for f in fb.values())),
                        "vol_gone_m3": int(sum(f["vol_peak_m3"] for f in gone)),
                        "n_shallower": sum(1 for i, f in ref_of.items() if f is not None and _hm(run, state, i, ca, za) < f["hmax_m"] - 0.005),
                        "n_deeper": sum(1 for i, f in ref_of.items() if f is not None and _hm(run, state, i, ca, za) > f["hmax_m"] + 0.005)}}


def _hm(run, state, i, ca, z):
    return round(float(z["hmax"][ca[i]].max()), 2)


def locate(run, state, lonlat, depth=DEFAULTS["depth"], min_area=DEFAULTS["min_area"], min_dur=DEFAULTS["min_dur"], pond_scope="state", tol_m=30.0):
    """把别处判出的点（经纬度）挂到本模块同一态的积水点编号上：点所在网格单元属于哪片；不在任何片内时取 tol_m 内最近的片，否则 None。"""
    from pyproj import Transformer
    from scipy.spatial import cKDTree
    z = np.load(check_args(run, state)); xy = z["xy"].astype(float)
    C = cells(run, state, depth, min_area, min_dur, pond_scope)
    owner = np.full(len(xy), -1); ids = list(C)
    for k, i in enumerate(ids):
        owner[C[i]] = k
    T = Transformer.from_crs(4326, 32640, always_xy=True)
    p = np.array([T.transform(lo, la) for lo, la in lonlat]).reshape(-1, 2)
    _, j = cKDTree(xy).query(p)
    wet = np.flatnonzero(owner >= 0)
    tw = cKDTree(xy[wet]) if len(wet) else None
    out = []
    for q, jj in zip(p, j):
        if owner[jj] >= 0:
            out.append(ids[owner[jj]])
        elif tw is not None:
            d, k = tw.query(q)
            out.append(ids[owner[wet[k]]] if d <= tol_m else None)
        else:
            out.append(None)
    return out


def near_hotspot(r: dict, hotspot: str) -> list[dict]:
    return [f for f in r["feats"] if any(h == hotspot for h, _ in f.get("near") or [])]


# ---------------------------------------------------------------- 命令行：预算缺省阈值（后处理用）+ 导出 CSV / GeoJSON
ZH_COLS = [("id", "编号"), ("state", "态"), ("lon", "最深点经度"), ("lat", "最深点纬度"), ("clon", "形心经度"), ("clat", "形心纬度"),
           ("hmax_m", "最大水深 m"), ("z_m", "最深点地面高程 m"), ("area_m2", "面积 m²"), ("area30_m2", "其中 ≥0.30 m（m²）"),
           ("dur_h", "持续 h"), ("vol_peak_m3", "峰值积水量 m³"), ("t_peak_h", "峰值时刻 h"), ("recede_h", "退水 h（峰值起）"),
           ("vol_end_m3", "末帧积水量 m³"), ("near", "200 m 内客户热点"), ("nearest", "最近客户热点")]


def _row(f):
    v = {k: f.get(k) for k, _ in ZH_COLS}
    v["recede_h"] = "未退" if f["recede_h"] is None else f["recede_h"]
    v["near"] = ";".join(h for h, _ in f.get("near") or [])
    v["nearest"] = f"{f['nearest'][0]} {f['nearest'][1]} m" if f.get("nearest") else ""
    return v


def export(run, states, depth, min_area, min_dur, out_dir=None, pond_scope="state"):
    import csv
    out_dir = pathlib.Path(out_dir or ROOT / "out" / "ponding"); out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{run}_d{depth:g}" + ("_pstate" if pond_scope == "state" else "")
    rows, gj, pts = [], [], []
    for s in states:
        r = compute(run, s, depth, min_area, min_dur, pond_scope=pond_scope)
        print(f"{s:<5} 积水片 {r['n_patches']:>5} → 积水点 {r['n']:>4}  峰值积水量合计 {r['vol_total_m3']:>9,} m³  未退 {r['n_not_receded']:>4}  邻接 {r['adjacency']}")
        for f in r["feats"]:
            row = _row(f); rows.append(row)
            props = {zh: row[k] for k, zh in ZH_COLS}
            gj.append({"type": "Feature", "geometry": f["geom"], "properties": props})
            pts.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]}, "properties": props})
    with open(out_dir / f"{stem}.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); w.writerow([zh for _, zh in ZH_COLS]); w.writerows([[r[k] for k, _ in ZH_COLS] for r in rows])
    for nm, fs in ((f"{stem}.geojson", gj), (f"{stem}_pts.geojson", pts)):
        (out_dir / nm).write_text(json.dumps({"type": "FeatureCollection", "features": fs}, ensure_ascii=False), encoding="utf-8")
    (out_dir / f"{stem}_params.json").write_text(json.dumps({"run": run, "states": states, "depth_m": depth, "min_area_m2": min_area, "min_dur_h": min_dur,
                                                             "note": "阈值为试算缺省，未与用户 / 客户对齐"}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {out_dir / stem}.csv / .geojson / _pts.geojson")


# ---------------------------------------------------------------- 交付目录：每个态一个 积水点/ 文件夹 + 顶层数据说明（export_delivery_rb01 / c1 调用）
DELIVERY_DEPTHS = (0.15, 0.30)
MACHINE_COLS = [  # (字段, 中文含义, 单位)
    ("id", "积水点编号 SIM-态-序号（按峰值积水量降序；只在同一运行、同一态、同一组阈值内有效；不是客户热点编号）", ""),
    ("run", "运行名", ""), ("state", "态", ""),
    ("depth_threshold_m", "判定水深阈值", "m"), ("min_area_m2", "最小连片面积", "m²"), ("min_duration_h", "最短持续", "h"),
    ("lon", "最深点经度（WGS84）", "°"), ("lat", "最深点纬度（WGS84）", "°"),
    ("centroid_lon", "面积加权形心经度（WGS84）", "°"), ("centroid_lat", "面积加权形心纬度（WGS84）", "°"),
    ("hmax_m", "片内全程最大水深", "m"), ("ground_z_m", "最深点地面高程（DTM，与模型同一高程基准）", "m"),
    ("area_m2", "积水面积（全程最大水深 ≥ 阈值的单元面积之和）", "m²"), ("area_ge030_m2", "其中最大水深 ≥0.30 m 的面积", "m²"),
    ("duration_h", "持续：片内 ≥阈值面积 ≥ 最小面积的累计时长", "h"),
    ("vol_peak_m3", "峰值积水量：逐帧 Σ(水深×单元面积) 的最大值", "m³"), ("t_peak_h", "峰值积水量出现时刻（从降雨开始）", "h"),
    ("receded", "模拟结束前是否退到最小面积以下（true / false）", ""), ("recede_h", "从峰值到退下的时长；未退为空", "h"),
    ("vol_end_m3", "模拟结束时片内积水量", "m³"), ("t_end_h", "模拟结束时刻", "h"),
    ("nearest_client_hotspot", "最近的客户热点编号（客户原编号 ADM-xxx）", ""), ("nearest_client_hotspot_dist_m", "到最近客户热点的距离（热点到片内最近单元形心）", "m"),
    ("n_client_hotspots_200m", "200 m 内客户热点个数（明细见 ponding_near_hotspots_*.csv）", ""),
    ("adjacency", "连片方式：mesh = 三角网共边；approx = 单元近邻近似", ""),
]


def _machine_row(run, r, f):
    p = r["params"]; nh = f.get("nearest") or [None, None]
    return {"id": f["id"], "run": run, "state": r["state"], "depth_threshold_m": p["depth"], "min_area_m2": p["min_area"], "min_duration_h": p["min_dur"],
            "lon": f["lon"], "lat": f["lat"], "centroid_lon": f["clon"], "centroid_lat": f["clat"], "hmax_m": f["hmax_m"], "ground_z_m": f["z_m"],
            "area_m2": f["area_m2"], "area_ge030_m2": f["area30_m2"], "duration_h": f["dur_h"], "vol_peak_m3": f["vol_peak_m3"], "t_peak_h": f["t_peak_h"],
            "receded": f["recede_h"] is not None, "recede_h": f["recede_h"], "vol_end_m3": f["vol_end_m3"], "t_end_h": r["t_end_h"],
            "nearest_client_hotspot": nh[0], "nearest_client_hotspot_dist_m": nh[1], "n_client_hotspots_200m": len(f.get("near") or []),
            "adjacency": r["adjacency"]}


def export_delivery(run: str, state_dirs: dict, root_dir, depths=DELIVERY_DEPTHS, pond_scope: str = "state") -> list[pathlib.Path]:
    """state_dirs：{态: 交付目录里这个态的文件夹}；每个态写 <文件夹>/积水点/，root_dir 写 积水点说明.md。返回写出的文件。"""
    import csv
    root_dir = pathlib.Path(root_dir); done = []
    summary = []
    for st, d in state_dirs.items():
        o = pathlib.Path(d) / "积水点"; o.mkdir(parents=True, exist_ok=True)
        for old in o.glob("*"):                                  # 阈值档变了时不留旧文件
            if old.is_file():
                old.unlink()
        for dep in depths:
            r = compute(run, st, dep, pond_scope=pond_scope)
            sfx = f"d{dep:.2f}"
            rows = [_machine_row(run, r, f) for f in r["feats"]]
            cols = [c for c, _, _ in MACHINE_COLS]
            with open(o / f"ponding_{sfx}.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=cols); w.writeheader()
                w.writerows([{k: ("" if v is None else str(v).lower() if isinstance(v, bool) else v) for k, v in x.items()} for x in rows])
            with open(o / f"ponding_near_hotspots_{sfx}.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh); w.writerow(["id", "client_hotspot", "dist_m"])
                w.writerows([[f["id"], h, dd] for f in r["feats"] for h, dd in f.get("near") or []])
            with open(o / f"积水点清单_{sfx}.csv", "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh); w.writerow([zh for _, zh in ZH_COLS]); w.writerows([[_row(f)[k] for k, _ in ZH_COLS] for f in r["feats"]])
            fc = lambda feats: {"type": "FeatureCollection", "name": f"{run}_{st}_{sfx}",                       # noqa: E731
                                "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}, "features": feats}
            (o / f"ponding_areas_{sfx}.geojson").write_text(json.dumps(fc([{"type": "Feature", "geometry": f["geom"], "properties": x}
                                                                           for f, x in zip(r["feats"], rows)]), ensure_ascii=False), encoding="utf-8")
            (o / f"ponding_points_{sfx}.geojson").write_text(json.dumps(fc([{"type": "Feature", "geometry": {"type": "Point", "coordinates": [x["lon"], x["lat"]]}, "properties": x}
                                                                            for x in rows]), ensure_ascii=False), encoding="utf-8")
            done += [o / f"ponding_{sfx}.csv", o / f"ponding_near_hotspots_{sfx}.csv", o / f"积水点清单_{sfx}.csv", o / f"ponding_areas_{sfx}.geojson", o / f"ponding_points_{sfx}.geojson"]
            summary.append((st, dep, r["n"], r["vol_total_m3"], r["n_not_receded"], r["t_end_h"], r["adjacency"]))
    doc = root_dir / "积水点说明.md"
    doc.write_text(delivery_doc(run, summary), encoding="utf-8"); done.append(doc)
    return done


def delivery_doc(run, summary) -> str:
    L = [f"# 二维模拟积水点 · {run}", "",
         "从一二维结果（各态 `一二维/*_二维水深帧与最大水深.npz`）按阈值判出的一片片积水，每片一行。生成：`swbrain/ponding.py`（系统里同一套：`/api/ponding`、控制台「二维模拟积水点」）。", "",
         "**编号 `SIM-态-序号` 是模型算出来的积水点，不是客户热点编号。** 客户热点用客户原编号（ADM-xxx），只出现在 `nearest_client_hotspot` 和 `ponding_near_hotspots_*.csv` 里。"
         "序号按峰值积水量从大到小排，只在同一运行、同一态、同一组阈值内有效：换了态或阈值，同一个编号不是同一处。", "",
         "**阈值是试算值，尚未与客户对齐**：最小连片 100 m²、最短持续 0.5 h，水深分 ≥0.15 m（`d0.15`）与 ≥0.30 m（`d0.30`）两档。", "",
         "## 判定", "",
         "1. 单元「积水」= 全程最大水深 ≥ 水深阈值；塘的占地不算。",
         "2. 积水单元按三角网共边连成片（拿不到原生网格时用单元近邻近似，`adjacency = approx`）。",
         "3. 保留连片面积 ≥ 100 m² 且持续 ≥ 0.5 h 的片。",
         "4. 峰值积水量 = 每 5 分钟一帧求 Σ(水深 × 单元面积)，取最大；不是用最大水深图乘面积（那样偏大）。",
         "5. 退水 = 从峰值时刻起，到片内 ≥阈值面积跌破 100 m² 的时长；模拟结束还没退的 `receded = false`、`recede_h` 为空。",
         "6. 片按全程最大水深包络切分：不同时段的两摊水若在包络上相连，会算成一片。", "",
         "## 文件（每个态的 `积水点/` 文件夹）", "",
         "| 文件 | 给谁 | 内容 |", "|---|---|---|",
         "| `ponding_d*.csv` | 程序 | 积水点清单，英文字段，UTF-8 无 BOM，逗号分隔，数字列只有数字（空 = 无值） |",
         "| `ponding_points_d*.geojson` | 程序 / GIS | 积水点（最深处），属性同上，WGS84 |",
         "| `ponding_areas_d*.geojson` | 程序 / GIS | 积水范围面（Polygon / MultiPolygon），属性同上，WGS84 |",
         "| `ponding_near_hotspots_d*.csv` | 程序 | 积水点 × 200 m 内客户热点：`id, client_hotspot, dist_m` |",
         "| `积水点清单_d*.csv` | 人 | 中文表头版（UTF-8 带 BOM，Excel 直接打开），内容同上 |", "",
         "## 字段（`ponding_*.csv` 与 GeoJSON 属性）", "", "| 字段 | 含义 | 单位 |", "|---|---|---|"]
    L += [f"| `{c}` | {zh} | {u} |" for c, zh, u in MACHINE_COLS]
    L += ["", "## 本次结果", "", "| 态 | 水深阈值 | 积水点数 | 峰值积水量合计 m³ | 模拟结束仍未退 | 连片方式 |", "|---|---|---|---|---|---|"]
    L += [f"| {st} | ≥{dep:.2f} m | {n} | {v:,} | {nr}（到 {te} h） | {adj} |" for st, dep, n, v, nr, te, adj in summary]
    L += ["", "峰值积水量合计是各片峰值之和（各片峰值时刻不同），只作量级参考。"]
    return "\n".join(L) + "\n"


README_MARK = ("<!-- ponding:start -->", "<!-- ponding:end -->")


def readme_section(state_names: dict) -> str:
    """交付 readme 里的一节（export 脚本用 upsert_readme 写进去，重导时整节替换）"""
    return "\n".join([README_MARK[0], "## 二维模拟积水点", "",
                      "每个态的 `积水点/` 文件夹：从一二维结果判出的积水点清单（CSV）、积水点与积水范围（GeoJSON，WGS84）、积水点 × 客户热点对照；"
                      "字段、判定方法、本次结果见 `积水点说明.md`。",
                      "编号 `SIM-态-序号` 是模型算出的积水点，**不是客户热点编号**；阈值（≥0.15 / ≥0.30 m、≥100 m²、≥0.5 h）为试算值，尚未与客户对齐。", "",
                      *[f"- `{d}/积水点/`：{nm}" for nm, d in state_names.items()], README_MARK[1]])


def upsert_readme(path, section: str, mark=README_MARK):
    """整节（mark 两个标记之间）替换或追加；不动文件其余部分的换行符。飞渡 DTS 一节也用它（dts_h2d.DTS_MARK）"""
    path = pathlib.Path(path)
    t = ""
    if path.exists():
        with open(path, encoding="utf-8", newline="") as fh:     # Path.read_text 的 newline 参数要 Python 3.13，CI 是 3.12
            t = fh.read()
    nl = "\r\n" if "\r\n" in t else "\n"
    section = section.replace("\n", nl)
    a, b = mark
    if a in t and b in t:
        t = t[:t.index(a)] + section + t[t.index(b) + len(b):]
    else:
        t = t.rstrip("\r\n") + nl + nl + section + nl
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(t)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="二维模拟积水点（SIM-态-序号）")
    ap.add_argument("--name", required=True, help="运行名，如 RB01_H60、C1_M61")
    ap.add_argument("--states", default="", help="逗号分隔；缺省 = 这个运行有结果的全部态")
    ap.add_argument("--depth", type=float, default=DEFAULTS["depth"])
    ap.add_argument("--min-area", type=float, default=DEFAULTS["min_area"])
    ap.add_argument("--min-dur", type=float, default=DEFAULTS["min_dur"])
    ap.add_argument("--export", action="store_true", help="另写 out/ponding/{run}_d{depth}.csv / .geojson")
    ap.add_argument("--pond-scope", choices=("run", "state"), default="state", help="塘占地：state = 本态地表塘（缺省，与判据评估 / L3 一致）；run = 本运行所有态并集（旧口径）")
    a = ap.parse_args(argv)
    states = [s for s in a.states.split(",") if s] or available(a.name).get(a.name, [])
    if not states:
        raise SystemExit(f"{c2d()} 下没有 {a.name}_*_2d.npz（工作目录不对？设 SWBRAIN_SWMM_WORK）")
    if a.export:
        export(a.name, states, a.depth, a.min_area, a.min_dur, pond_scope=a.pond_scope)
    else:
        for s in states:
            r = compute(a.name, s, a.depth, a.min_area, a.min_dur, pond_scope=a.pond_scope)
            print(f"{s:<5} 积水点 {r['n']:>4}  峰值积水量合计 {r['vol_total_m3']:>9,} m³  邻接 {r['adjacency']}  → 缓存")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
