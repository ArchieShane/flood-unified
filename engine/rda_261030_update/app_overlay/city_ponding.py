"""全市一二维交付（`sim_city`，如 #1068 ADM 100 m）的「模拟积水点」：口径与 RD-A 的 `rda.ponding_custom` 一致，
让详情页能像 RD-A 那样给出积水点清单、最大水深 / 面积 / 积水量及其时刻、客户热点覆盖率。

和 RD-A 的差别（页面上都要写明，不能当成同一把尺子）：

- 网格是 100 m 的非结构三角网（56.9 万单元，平均约 2,170 m²），地形被平均到 100 m，小洼地被抹平；
  所以点数和位置与 RD-A（15 m）不可比。
- 连片用三角网共边，要读 `.sww` 里的 `volumes`；`prepare` 时抽成 `points/mesh_edges.npy`（两态同一张网），
  顶点坐标抽成 `points/mesh_tri.npy`（量到边界的距离用）。
- 没有评估剔除区、L3、设施；热点是交付的 470 个（台账 ADM 506 个里落在二维计算域内的），另 36 个在域外，只列不算。
- 距离同客户文档：热点到最近积水片**边界**的平面距离（热点落在片里为 0），按 `.sww` 的三角形顶点量；
  默认阈值也同文档（0.15 / 0.30 m、100 m²、0.5 h）。100 m 网格下一个单元约 2,000 m²，所以 100 m² 实际等于至少 1 个单元。

布局（`sim_deliveries/<键>/points/`）：`ponding_source.json`（态 → npz、热点、域外热点）、`mesh_edges.npy`、
`<态>_2d.npz`（交付 npz 的链接 / 拷贝，页面只读这里，原件不动）。

用法：

    PYTHONPATH=src python -m stormwater.city_ponding prepare <交付目录>      # 交付目录要带 .sww（只读它的网格）
    PYTHONPATH=src python -m stormwater.city_ponding probe <sim_deliveries/键> [深度]   # 算一遍打印概况
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from . import hotspot_dist, rda
from .sim_city import STATES, _link, _state_dir
from .sim_sww import DATA_ROOT, OUT_ROOT, SwwError, _one

POINTS_DIR = "points"
SOURCE_FILE = "ponding_source.json"
NEAR_M = 200.0
#: 重算参数的合法范围（面积 0 = 至少 1 个单元；时长上限取演算时长以内）
LIMITS = {"depth": (0.05, 3.0), "min_area": (0.0, 200000.0), "min_dur": (0.0, 5.0)}
DEFAULT = {"depth": 0.15, "min_area": 100.0, "min_dur": 0.5}      # 与 RD-A / 客户文档一致（100 m²、0.5 h）


class PointsError(Exception):
    """没有数据 / 参数不对；API 层转 404 / 422。"""


# ---------------------------------------------------------------------------
# 准备（离线一次）：链接 npz、抽网格邻接、整理热点
# ---------------------------------------------------------------------------


def _mesh_from_sww(sww: Path) -> tuple[np.ndarray, np.ndarray]:
    """（共边的三角形号对 N×2，三角形顶点的绝对坐标 N×3×2，EPSG:32640）。.sww 的 x / y 是局部坐标，绝对位置 = + xllcorner / yllcorner。"""
    from scipy.io import netcdf_file

    with netcdf_file(str(sww), "r", mmap=False) as f:
        v = f.variables["volumes"][:].astype(np.int64).copy()
        xll, yll = float(np.asarray(f.xllcorner)), float(np.asarray(f.yllcorner))
        x = f.variables["x"][:].astype(np.float64) + xll
        y = f.variables["y"][:].astype(np.float64) + yll
    tri_xy = np.stack([x[v], y[v]], -1)
    e = np.concatenate([v[:, [0, 1]], v[:, [1, 2]], v[:, [2, 0]]])
    tri = np.concatenate([np.arange(len(v))] * 3)
    e.sort(axis=1)
    key = e[:, 0] * (int(e.max()) + 1) + e[:, 1]
    o = np.argsort(key, kind="stable")
    ks, ts = key[o], tri[o]
    same = ks[1:] == ks[:-1]
    return np.stack([ts[:-1][same], ts[1:][same]], 1).astype(np.int32), tri_xy


_pt_tri_dist = hotspot_dist.pt_tri_dist       # 旧名保留给测试


def _ledger_adm_ids() -> list[str]:
    p = DATA_ROOT / "processed" / "hotspots_845.csv"
    if not p.is_file():
        return []
    with p.open(encoding="utf-8-sig", newline="") as f:
        return [r["uid"] for r in csv.DictReader(f) if r["source"] == "ADM"]


def _delivery_adm_ids(root: Path) -> list[str]:
    """台账 csv 不在时（engine 推演的区域交付）用交付自带的全部客户热点 `common/hotspots_all.json`（506 个，域内 / 域外都有）。"""
    p = root / "common" / "hotspots_all.json"
    if not p.is_file():
        return []
    return [h["hotspot_id"] for h in json.loads(p.read_text(encoding="utf-8"))]


def _norm_adm(x: str) -> str:
    """交付写 `ADM-1`，台账写 `ADM-001`：统一补零再比。"""
    m = re.fullmatch(r"ADM-0*(\d+)", x.strip())
    return f"ADM-{int(m.group(1)):03d}" if m else x


def _f(x: str | None) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except ValueError:
        return None


def _hotspot_stats(root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """交付自带的逐热点统计（`hotspots/hotspot_results.csv`，离热点 200 m 内的单元）：热点 → 态 → 指标。
    `class`：flooded = ≥0.30 m、shallow = 0.10–0.30 m、no = 没有（交付的 `flooded?` 列）。"""
    p = root / "hotspots" / "hotspot_results.csv"
    if not p.is_file():
        return {}
    out: dict[str, dict[str, dict[str, Any]]] = {}
    with p.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            per: dict[str, dict[str, Any]] = {}
            for st in STATES:
                if f"{st} max depth within 200 m (m)" not in r:
                    continue
                cls = (r.get(f"{st} flooded?") or "").split(" ")[0]
                per[st] = {
                    "max_depth_m": _f(r[f"{st} max depth within 200 m (m)"]), "nearest_cell_depth_m": _f(r.get(f"{st} nearest cell depth (m)")),
                    "area_ge_0_10_ha": _f(r.get(f"{st} flooded area >=0.10 m (ha)")), "area_ge_0_30_ha": _f(r.get(f"{st} flooded area >=0.30 m (ha)")),
                    "max_volume_m3": _f(r.get(f"{st} peak volume (m3)")), "t_max_depth_h": _f(r.get(f"{st} time of max depth (h)")),
                    "first_ge_0_10_h": _f(r.get(f"{st} first time depth >=0.10 m (h)")),
                    "duration_ge_0_10_min": _f(r.get(f"{st} duration depth >=0.10 m (min)")),
                    "class": cls if cls in ("flooded", "shallow") else "no",
                }
            out[r["hotspot"]] = per
    return out


def _domain_polygon(root: Path) -> dict[str, Any]:
    """计算域面（`common/domain.geojson`，经纬度）→ 页面的「汇水区」。"""
    from .sim_c1 import _round_coords
    from .sim_sww import norm_geom

    p = root / "common" / "domain.geojson"
    feats = []
    if p.is_file():
        for ft in json.loads(p.read_text(encoding="utf-8")).get("features", []):
            g = norm_geom(ft.get("geometry") or {})
            if g.get("type") in ("Polygon", "MultiPolygon"):
                pr = ft.get("properties") or {}
                feats.append({"type": "Feature", "properties": {"name": pr.get("name") or root.name, "area_km2": pr.get("area_km2"), "source": p.name},
                              "geometry": {"type": g["type"], "coordinates": _round_coords(g["coordinates"])}})
    return {"type": "FeatureCollection", "features": feats}


def _scopes(base: Path, states: list[str], hxy_utm: np.ndarray) -> tuple[dict, dict, dict]:
    """逐态的「全域」「热点 200 m 内」两个口径（最大水深 / 积水面积 ≥0.10、≥0.30 m / 最大积水量），同 `sim_rda.extract_hotspots`：
    热点 200 m 内 = 离任一热点形心距离 ≤ 200 m 的单元并集。全市不出溢水点，`ponding` / `overflow_m3` 给 None。"""
    from scipy.spatial import cKDTree

    from .sim_rda import NEAR_M, _zone_stats

    zones: dict[str, dict] = {}
    total: dict[str, dict] = {}
    scopes: dict[str, dict] = {"domain": {}, "near_200m": {}}
    for st in states:
        with np.load(base / f"{st}_2d.npz") as z:
            xy, area, hmax, frames, t_h = z["xy"].astype(np.float64), z["area"].astype(np.float64), z["hmax"], z["frames"], z["t_h"]
        near = np.zeros(len(area), bool)
        for idx in cKDTree(xy).query_ball_point(hxy_utm, NEAR_M):
            near[idx] = True
        zones[st] = {"domain": _zone_stats(area, hmax, frames, t_h, np.ones(len(area), bool))}
        total[st] = {"near_200m": _zone_stats(area, hmax, frames, t_h, near)}
        for k, s in (("domain", zones[st]["domain"]), ("near_200m", total[st]["near_200m"])):
            scopes[k][st] = {"ponding": None, "overflow_m3": None, **{f: s[f] for f in ("area_ge_0_10_ha", "area_ge_0_30_ha", "max_depth_m", "max_volume_m3")}}
    return zones, total, scopes


def prepare(root: Path) -> Path:
    """从交付目录整理出 `points/`。`root` = 原始交付目录（态目录下有 `2D/*_2d.npz`，其一带 `.sww`）。"""
    root = root.resolve()
    base = OUT_ROOT / root.name / POINTS_DIR
    base.mkdir(parents=True, exist_ok=True)
    states: dict[str, str] = {}
    n_tri = None
    sww = None
    for st in STATES:
        sd = _state_dir(root, st)
        if sd is None:
            continue
        npz = _one(sd / "2D", "*_2d.npz")
        with np.load(npz) as z:
            n = int(z["area"].shape[0])
        if n_tri not in (None, n):
            raise SwwError(f"{st} 的单元数 {n} 与前一态 {n_tri} 不同，两态不是同一张网")
        n_tri = n
        _link(npz, base / f"{st}_2d.npz")
        states[st] = f"{st}_2d.npz"
        hits = [p for p in (sd / "2D").glob("*.sww") if not p.name.startswith("._")]
        sww = sww or (hits[0] if hits else None)
    if not states:
        raise SwwError(f"{root} 下没有可用的态目录")
    edges_p = base / "mesh_edges.npy"
    tri_p = base / "mesh_tri.npy"
    if sww is not None:
        edges, tri_xy = _mesh_from_sww(sww)
        if int(edges.max()) >= n_tri or len(tri_xy) != n_tri:
            raise SwwError(".sww 的网格与 npz 的单元数对不上")
        with np.load(base / f"{next(iter(states))}_2d.npz") as z:
            off = np.hypot(*(tri_xy.mean(1) - z["xy"].astype(np.float64)).T)
        if float(np.median(off)) > 2.0:
            raise SwwError(f".sww 三角形形心与 npz 单元坐标对不上（中位偏差 {np.median(off):.1f} m）")
        np.save(edges_p, edges)
        np.save(tri_p, tri_xy)
    elif not (edges_p.is_file() and tri_p.is_file()):
        raise SwwError("交付里没有 .sww，也没有已抽好的 mesh_edges.npy / mesh_tri.npy，连不了片也量不了边界距离")

    hs = json.loads(_one(root / "common", "hotspots.json").read_text(encoding="utf-8"))
    x = np.array([h["x"] for h in hs], float)
    y = np.array([h["y"] for h in hs], float)
    lon, lat = rda._utm40_to_lonlat(x, y)
    stats = _hotspot_stats(root)
    pts = [{"id": h["hotspot_id"], "area": h.get("area"), "location": h.get("location"),
            "criticality": h.get("criticality"), "x": float(x[i]), "y": float(y[i]),
            "lon": round(float(lon[i]), 6), "lat": round(float(lat[i]), 6),
            "stats": stats.get(h["hotspot_id"], {})} for i, h in enumerate(hs)]
    # 地图上「关注积水点」图层读任务根目录的 hotspots.json（与 C1 / RD-A 同一格式，`/api/simulations/{id}/hotspots`）。
    # 全市交付没有汇水区图层，同 RD-A 一样把**计算域**当作汇水区（`region_catchment`，地图上「汇水区」开关画的就是它）；
    # 总览右上的口径标签：全域 = 整个计算域，热点 200 m 内 = 离任一热点 ≤200 m 的单元并集（`scopes`，按各态二维结果现算）。
    empty = {"type": "FeatureCollection", "features": []}
    zones, total, scopes = _scopes(base, list(states), hxy_utm=np.stack([x, y], 1))
    (base.parent / "hotspots.json").write_text(json.dumps({
        "schema": "flood_management.city_hotspots.v1", "states": list(states),
        "note": "交付的 470 个热点（台账 ADM 506 个里落在二维计算域内的）；逐态统计取交付 hotspots/hotspot_results.csv（离热点 200 m 内的单元）；"
                "汇水区 = 计算域（交付没有汇水区图层）",
        "points": [{"id": p["id"], "district": p["area"], "location": p["location"], "criticality": p["criticality"],
                    "lat": p["lat"], "lon": p["lon"], "stats": {st: {"near_200m": v} for st, v in p["stats"].items()}} for p in pts],
        "region_catchment": _domain_polygon(root), "catchments": empty,
        "zones": zones, "total": total, "scopes": scopes,
    }, ensure_ascii=False), encoding="utf-8")
    have = {_norm_adm(p["id"]) for p in pts}
    outside = [a for a in (_ledger_adm_ids() or _delivery_adm_ids(root)) if _norm_adm(a) not in have]
    (base / SOURCE_FILE).write_text(json.dumps({
        "schema": "flood_management.city_ponding_source.v1",
        "delivery": root.name, "n_tri": n_tri, "states": states, "mesh_edges": "mesh_edges.npy",
        "hotspots": pts, "outside_domain": outside,
        "note": "热点取交付 common/hotspots.json（台账 ADM 在二维计算域内的）；outside_domain 是台账里不在域内的，只列不算",
    }, ensure_ascii=False), encoding="utf-8")
    print(f"  points：{len(states)} 态｜{n_tri:,} 单元｜网格邻接 {np.load(edges_p).shape[0]:,} 条｜热点 {len(pts)}｜域外 {len(outside)}")
    return base


def prepare_rda_layout(root: Path, out: Path) -> Path:
    """engine 推演的多态交付（RD-A 全套成果布局：`公共/` + `<态>/一二维/`，`outputs.json` 的 layout = rda_multi_state）的模拟积水点。
    和 `prepare` 同一套口径，只是态目录和文件名不同：npz = `<态>/一二维/*_二维结果.npz`，.sww = 同目录 `*_二维水深与流速时序.sww`，
    热点取 `公共/客户热点.geojson`，逐态热点统计取 `sim_rda.extract` 已经写好的 `<out>/hotspots.json`（不覆盖它）。
    态 key 用 sim_c1 的（L1 / L2 / L2+planned），和页面、`states.json` 一致。`out` = `sim_deliveries/<键>`。"""
    from . import sim_c1

    root = root.resolve()
    base = out / POINTS_DIR
    base.mkdir(parents=True, exist_ok=True)
    states: dict[str, str] = {}
    n_tri = None
    sww = None
    for st in sim_c1.present_states(root):
        sd = sim_c1._state_dir(root, st)
        npz = sim_c1.state_files(root, st).npz
        with np.load(npz) as z:
            n = int(z["area"].shape[0])
        if n_tri not in (None, n):
            raise SwwError(f"{st} 的单元数 {n} 与前一态 {n_tri} 不同，各态不是同一张网")
        n_tri = n
        fn = f"{st.replace('+', '_')}_2d.npz"
        _link(npz, base / fn)
        states[st] = fn
        hits = [p for p in (sd / "一二维").glob("*_二维水深与流速时序.sww") if not p.name.startswith("._")]
        sww = sww or (hits[0] if hits else None)
    if not states:
        raise SwwError(f"{root} 下没有可用的态目录")
    if sww is None:
        raise SwwError("交付里没有 .sww，连不了片也量不了边界距离")
    edges, tri_xy = _mesh_from_sww(sww)
    if int(edges.max()) >= n_tri or len(tri_xy) != n_tri:
        raise SwwError(".sww 的网格与 npz 的单元数对不上")
    with np.load(base / next(iter(states.values()))) as z:
        off = np.hypot(*(tri_xy.mean(1) - z["xy"].astype(np.float64)).T)
    if float(np.median(off)) > 2.0:
        raise SwwError(f".sww 三角形形心与 npz 单元坐标对不上（中位偏差 {np.median(off):.1f} m）")
    np.save(base / "mesh_edges.npy", edges)
    np.save(base / "mesh_tri.npy", tri_xy)
    feats = json.loads((root / "公共" / "客户热点.geojson").read_text(encoding="utf-8"))["features"]
    lon = np.array([f["geometry"]["coordinates"][0] for f in feats], float)
    lat = np.array([f["geometry"]["coordinates"][1] for f in feats], float)
    hx, hy = rda._lonlat_to_utm40(lon, lat)
    old = {}
    hp = out / "hotspots.json"
    if hp.is_file():
        old = {p["id"]: p.get("stats", {}) for p in json.loads(hp.read_text(encoding="utf-8")).get("points", [])}
    pts = []
    for i, f in enumerate(feats):
        pr = f["properties"]
        hid = pr["hotspot_id"]
        pts.append({"id": hid, "area": pr.get("area"), "location": pr.get("location"), "criticality": pr.get("criticality"),
                    "x": float(hx[i]), "y": float(hy[i]), "lon": round(float(lon[i]), 6), "lat": round(float(lat[i]), 6),
                    "stats": {st: v.get("near_200m", v) for st, v in old.get(hid, {}).items()}})
    have = {_norm_adm(p["id"]) for p in pts}
    outside = [a for a in (_ledger_adm_ids() or _delivery_adm_ids(root)) if _norm_adm(a) not in have]
    (base / SOURCE_FILE).write_text(json.dumps({
        "schema": "flood_management.city_ponding_source.v1", "delivery": root.name, "n_tri": n_tri, "states": states,
        "mesh_edges": "mesh_edges.npy", "hotspots": pts, "outside_domain": outside,
        "note": "engine 推演的多态交付：热点取 公共/客户热点.geojson（台账 ADM 在二维计算域内的）；积水点从各态自己的二维结果现算（不读固定的 AECOM RD-A 成果）",
    }, ensure_ascii=False), encoding="utf-8")
    print(f"  points：{len(states)} 态｜{n_tri:,} 单元｜网格邻接 {edges.shape[0]:,} 条｜热点 {len(pts)}｜域外 {len(outside)}")
    return base


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def points_dir(output_dir: str | None) -> Path | None:
    """任务的 `points/` 目录；没有（或不在 DATA_ROOT 下）给 None。"""
    if not output_dir:
        return None
    p = (DATA_ROOT / output_dir.replace("\\", "/") / POINTS_DIR).resolve()
    if DATA_ROOT.resolve() not in p.parents:
        return None
    return p if (p / SOURCE_FILE).is_file() else None


def has_points(output_dir: str | None) -> bool:
    return points_dir(output_dir) is not None


@lru_cache(maxsize=4)
def _source(pdir: str) -> dict[str, Any]:
    return json.loads((Path(pdir) / SOURCE_FILE).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _edges(pdir: str) -> np.ndarray:
    return np.load(Path(pdir) / _source(pdir)["mesh_edges"])


@lru_cache(maxsize=1)
def _tri(pdir: str) -> np.ndarray:
    """三角形顶点的绝对坐标（N×3×2），量热点到积水片边界的距离用。"""
    return np.load(Path(pdir) / "mesh_tri.npy", mmap_mode="r")


@lru_cache(maxsize=2)
def _cells(pdir: str, state: str) -> dict[str, np.ndarray]:
    src = _source(pdir)
    if state not in src["states"]:
        raise PointsError("这份成果没有这个方案态")
    with np.load(Path(pdir) / src["states"][state]) as z:
        return {k: z[k] for k in ("xy", "area", "elev", "hmax", "frames", "t_h")}


def check_params(depth: float, min_area: float, min_dur: float) -> None:
    for name, v in (("depth", depth), ("min_area", min_area), ("min_dur", min_dur)):
        lo, hi = LIMITS[name]
        if not lo <= v <= hi:
            raise ValueError(f"{name} 要在 {lo}–{hi} 之间")


def meta(pdir: Path) -> dict[str, Any]:
    s = _source(str(pdir))
    from . import sim_c1

    out_ids = {_norm_adm(a) for a in s["outside_domain"]}
    # 域外热点也给出位置（没有仿真结果，页面按「未覆盖」计入热点总数，台账 ADM 506 个）
    outside = [{"id": p["id"], "area": p["district"], "location": p["location"], "criticality": p["criticality"],
                "lat": p["lat"], "lon": p["lon"], "stats": {}, "outside": True}
               for p in sim_c1.ledger_adm_points() if _norm_adm(p["id"]) in out_ids]
    return {"states": list(s["states"]), "n_tri": s["n_tri"], "default": DEFAULT,
            "hotspots": [{k: h.get(k) for k in ("id", "area", "location", "criticality", "lat", "lon", "stats")} for h in s["hotspots"]],
            "outside_domain": s["outside_domain"], "outside_hotspots": outside}


@lru_cache(maxsize=32)
def ponding_points(pdir: str, state: str, depth: float = 0.15, min_area: float = 100.0, min_dur: float = 0.5) -> dict[str, Any]:
    """按阈值从二维结果现算积水点（逻辑同 `rda.ponding_custom`，向量化到整张网）：

    1. 全程最大水深 ≥ depth 的单元，按三角网共边连成片；
    2. 连片面积 ≥ min_area（0 = 至少 1 个单元）；
    3. 持续 = 片内「逐帧 ≥depth 的面积 ≥ min_area（0 时取 > 0）」的帧数 × 帧间隔，≥ min_dur；
    4. 峰值积水量 = 逐帧 Σ(水深×单元面积) 的最大值；最大面积 = 逐帧 ≥depth 面积的最大值；
    5. 退水 = 峰值后片内 ≥depth 面积跌破下限的时长，到模拟结束还没退的 receded=False。
    编号 SIM-<态>-<序号>，按峰值积水量降序。
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    check_params(depth, min_area, min_dur)
    c = _cells(pdir, state)
    src = _source(pdir)
    xy, area, F, hmax, t, elev = c["xy"].astype(float), c["area"].astype(float), c["frames"], c["hmax"], c["t_h"], c["elev"]
    n, T = len(xy), len(t)
    dt = float(np.median(np.diff(t))) if T > 1 else 0.0
    thr_a = min_area if min_area > 0 else 1e-6
    hs = src["hotspots"]
    out: dict[str, Any] = {
        "state": state, "depth_m": depth, "min_area_m2": min_area, "min_duration_h": min_dur, "custom": True,
        "count": 0, "peak_vol_sum_m3": 0, "not_receded": 0, "excluded": 0, "mostly_excluded": 0, "points": [],
        "hotspot_hits": [{"id": h["id"], "dist_m": None} for h in hs],
        "hotspot_bins": {"total": len(hs), "0": 0, "50": 0, "100": 0, "200": 0},
        "note": "按所给阈值从二维结果现算（三角网共边连片）；热点距离按到积水片边界量；没有评估剔除区，没有 L3 承接。",
    }
    wet = np.nonzero(hmax >= depth)[0]
    if not len(wet):
        return out
    inw = np.full(n, -1, np.int64)
    inw[wet] = np.arange(len(wet))
    e = _edges(pdir)
    a, b = inw[e[:, 0]], inw[e[:, 1]]
    m = (a >= 0) & (b >= 0)
    g = coo_matrix((np.ones(int(m.sum())), (a[m], b[m])), shape=(len(wet),) * 2)
    nc, lab = connected_components(g, directed=False)
    aw = area[wet]
    hw = hmax[wet].astype(float)
    ar = np.bincount(lab, aw, nc)

    Fw = F[:, wet].astype(np.float32)                                   # (T, W)
    on = Fw >= depth
    a_t = np.stack([np.bincount(lab, aw * on[k], nc) for k in range(T)])    # (T, nc) 逐帧 ≥depth 的面积
    v_t = np.stack([np.bincount(lab, aw * Fw[k], nc) for k in range(T)])    # 逐帧 Σ水深×面积
    order = np.argsort(lab, kind="stable")
    starts = np.r_[0, np.flatnonzero(np.diff(lab[order])) + 1]
    h_t = np.maximum.reduceat(Fw[:, order], starts, axis=1)               # 逐帧片内最大水深

    dur = (a_t >= thr_a).sum(0) * dt
    ok = (ar >= min_area) & (dur >= min_dur - 1e-9)
    ids = np.nonzero(ok)[0]
    if not len(ids):
        return out
    ip, ia, ih = v_t.argmax(0), a_t.argmax(0), h_t.argmax(0)
    col = np.arange(nc)
    vol_peak = v_t[ip, col]
    below = a_t < thr_a
    after = (np.arange(T)[:, None] >= ip[None, :]) & below
    receded = after.any(0)
    recede_h = (after.argmax(0) - ip) * dt

    # 最深单元（给定位）与面积加权形心
    o2 = np.lexsort((-hw, lab))
    first = np.r_[0, np.flatnonzero(np.diff(lab[o2])) + 1]
    deep = wet[o2[first]]                                               # 每片最深单元的全局下标
    cx = np.bincount(lab, xy[wet, 0] * aw, nc) / ar
    cy = np.bincount(lab, xy[wet, 1] * aw, nc) / ar
    a30 = np.bincount(lab, aw * (hw >= 0.30), nc)

    # 热点：片内最近形心的热点，以及 200 m 内的热点
    hxy = np.array([[h["x"], h["y"]] for h in hs], float)
    d_near, i_near = cKDTree(hxy).query(xy[wet])
    o3 = np.lexsort((d_near, lab))
    f3 = np.r_[0, np.flatnonzero(np.diff(lab[o3])) + 1]
    near_d = d_near[o3[f3]]
    near_i = i_near[o3[f3]]
    # 距离按客户文档的口径：热点到积水片**边界**的平面距离（热点落在片里为 0），不是到单元形心。
    # 只在留下的片里找；候选单元按形心 NEAR_M + 150 m 预筛（三角形外接圆半径远小于 150 m），再量到三角形的精确距离。
    keepw = np.nonzero(ok[lab])[0]
    bn = hotspot_dist.boundary_near(_tri(pdir), xy, hxy, [h["id"] for h in hs], wet[keepw], lab[keepw], NEAR_M)
    near, pair_min = bn["near"], bn["pair_min"]
    out["hotspot_hits"] = [{"id": h["id"], "dist_m": None if d_ is None else int(round(d_))} for h, d_ in zip(hs, bn["raw"])]
    out["hotspot_bins"] = bn["bins"]    # 客户文档第二节的分档：落在积水范围内（0 m）/ 50 / 100 / 200 m 内有积水的热点个数

    lo, la = rda._utm40_to_lonlat(xy[deep, 0], xy[deep, 1])
    clo, cla = rda._utm40_to_lonlat(cx, cy)
    tag = state
    ncell = np.bincount(lab, minlength=nc)
    pts = []
    for k in ids.tolist():
        nl = sorted(near.get(k, []))
        pm = pair_min.get(k)
        nid, ndist = (hs[pm[1]]["id"], pm[0]) if pm else (hs[int(near_i[k])]["id"], float(near_d[k]))   # 量不到边界的退回形心距离
        pts.append({
            "lon": round(float(lo[k]), 6), "lat": round(float(la[k]), 6),
            "centroid_lon": round(float(clo[k]), 6), "centroid_lat": round(float(cla[k]), 6),
            "hmax_m": round(float(hw[o2[first[k]]]), 2), "ground_z_m": round(float(elev[deep[k]]), 2),
            "area_m2": int(round(float(ar[k]))), "area_ge030_m2": int(round(float(a30[k]))),
            "area_peak_m2": int(round(float(a_t[ia[k], k]))), "t_area_peak_h": round(float(t[ia[k]]), 2),
            "t_hmax_h": round(float(t[ih[k]]), 2),
            "duration_h": round(float(dur[k]), 2), "vol_peak_m3": int(round(float(vol_peak[k]))),
            "t_peak_h": round(float(t[ip[k]]), 2),
            "receded": bool(receded[k]), "recede_h": round(float(recede_h[k]), 2) if receded[k] else None,
            "vol_end_m3": int(round(float(v_t[-1, k]))), "t_end_h": round(float(t[-1]), 1),
            "nearest_client_hotspot": nid, "nearest_client_hotspot_dist_m": int(round(ndist)),
            "n_client_hotspots_200m": len(nl),
            "near_hotspots": [{"hotspot": h_, "dist_m": int(round(d_))} for d_, h_ in nl],
            "cells": int(ncell[k]), "_k": k,
        })
    pts.sort(key=lambda p: -p["vol_peak_m3"])
    for i, p in enumerate(pts, 1):
        p.update({"id": f"SIM-{tag}-{i:03d}", "state": tag, "depth_threshold_m": depth, "min_area_m2": min_area,
                  "min_duration_h": min_dur, "excluded": False, "mostly_excluded": False, "exclusion_note": None,
                  "area_after_exclusion_m2": None, "l3": None, "status": "已退" if p["receded"] else "未退",
                  "adjacency": "mesh"})
    # 编号 → 连片号，单点过程（point_series）要用；连片号不进返回体
    _LABELS[(pdir, state, depth, min_area, min_dur)] = (wet, lab, {p["id"]: p.pop("_k") for p in pts})
    while len(_LABELS) > 8:
        _LABELS.pop(next(iter(_LABELS)))
    out.update(count=len(pts), peak_vol_sum_m3=sum(p["vol_peak_m3"] for p in pts),
               not_receded=sum(not p["receded"] for p in pts), points=pts)
    return out


#: 最近几次 `ponding_points` 的连片结果：(目录, 态, 阈值…) → (湿格全局号, 湿格所属连片号, 编号→连片号)
_LABELS: dict[tuple, tuple[np.ndarray, np.ndarray, dict[str, int]]] = {}


def point_series(pdir: str, state: str, point_id: str, depth: float = 0.15, min_area: float = 100.0, min_dur: float = 0.5) -> dict[str, Any]:
    """某个积水点逐帧的积水量 / ≥depth 面积 / 最大水深（算法见 `rda.cell_series`）。阈值要与清单一致，编号才对得上。"""
    key = (pdir, state, depth, min_area, min_dur)
    r = ponding_points(*key)
    if key not in _LABELS:                  # 清单走了 lru 缓存、连片结果被挤出去了：重算一次补回来
        ponding_points.__wrapped__(*key)
    p = next((x for x in r["points"] if x["id"] == point_id), None)
    if p is None or key not in _LABELS:
        raise PointsError("找不到这个积水点（态或阈值变了？）")
    wet, lab, idmap = _LABELS[key]
    c = _cells(pdir, state)
    cells = wet[lab == idmap[point_id]]
    return {"id": point_id, "state": state, "vol_peak_m3": p["vol_peak_m3"], "t_peak_h": p["t_peak_h"],
            **rda.cell_series(c["frames"], c["area"].astype(float), cells, depth, c["t_h"])}


def hotspot_circle_series(pdir: str, state: str, hotspot_id: str, depth: float = 0.15) -> dict[str, Any]:
    """某个交付热点 200 m 圆内逐帧的积水量 / 面积 / 最大水深（口径同 `hotspot_buffer`，曲线峰值 = 清单里的圆内峰值积水量）。"""
    from . import hotspot_buffer as hb

    hb.check_depth(depth)
    h = next((x for x in _source(pdir)["hotspots"] if x["id"] == hotspot_id), None)
    if h is None:
        raise PointsError("找不到这个热点")
    c = _cells(pdir, state)
    return {"id": hotspot_id, "state": state,
            **hb.circle_series(c["xy"].astype(float), c["area"].astype(float), c["frames"], c["t_h"], (float(h["x"]), float(h["y"])), depth)}


@lru_cache(maxsize=16)
def hotspot_buffer(pdir: str, state: str, depth: float = 0.15) -> dict[str, Any]:
    """交付的热点 200 m 圆内的地表峰值积水量（水深 ≥ depth 的单元），及各热点圆的重叠；口径见 `hotspot_buffer.py`。"""
    from . import hotspot_buffer as hb

    hb.check_depth(depth)
    c = _cells(pdir, state)
    hs = _source(pdir)["hotspots"]
    out = hb.buffer_stats(c["xy"].astype(float), c["area"].astype(float), c["frames"], c["t_h"],
                          np.array([[h["x"], h["y"]] for h in hs], float), [h["id"] for h in hs], depth)
    out["state"] = state
    return out


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="从交付目录整理出 points/")
    p.add_argument("path", type=Path)
    q = sub.add_parser("probe", help="算一遍打印概况")
    q.add_argument("path", type=Path, help="sim_deliveries/<键>")
    q.add_argument("depth", type=float, nargs="?", default=0.15)
    a = ap.parse_args()
    if a.cmd == "prepare":
        prepare(a.path)
        return
    pdir = (a.path / POINTS_DIR).resolve()
    for st in _source(str(pdir))["states"]:
        r = ponding_points(str(pdir), st, a.depth, DEFAULT["min_area"], DEFAULT["min_dur"])
        near = {h["hotspot"] for p_ in r["points"] for h in p_["near_hotspots"]}
        assert r["hotspot_bins"]["200"] == len(near), (r["hotspot_bins"], len(near))
        print(f"{st} ≥{a.depth} m：{r['count']} 片｜峰值合计 {r['peak_vol_sum_m3']:,} m³｜未退 {r['not_receded']}｜"
              f"200 m 内覆盖热点 {len(near)}")


if __name__ == "__main__":
    main()
