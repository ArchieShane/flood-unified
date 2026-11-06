"""全市任务的第二道防线（Line 2 Current = 既有管网 + 已建成调蓄塘）信息。

全市 250 m 二维交付（仿真 #100 / #101 / #102，33 / 61 / 96 mm · 2 h）只有 L1 一态。L2 目前只有
算力侧 2026-09-27 的一份报告「阿布扎比全市 L1 / L2」（DMT 网页，SWMM 5.2.4 一维）：

- 雨：61 mm / 2 h，客户 2 小时设计雨型（与 Mussafah_00 同一场雨）；
- 网：客户 GDB 全量重建，260,183 根管、260,297 个井，8 片 × 2 态；L1 / L2 同一张网，只差塘；
- L2 = 在同一张网上接 22 座已建成的塘（接法是推断的：无出水口管网的最低井 100 m 内有塘就用带拍门短管接入，
  台账没这么写；22 座里 5 座没有任何管道进水）；
- 一维口径「冒出井盖就是损失」（ALLOW_PONDING NO），差值是**下限**：塘只接得到管道送来的水；
- 退水、最大积水面积 / 水深要等全市一二维，降雨服务水平待另跑 —— 这些一律留 None，不拿 L1 二维结果顶替。

另附 Madinat Al Riyad 一二维试跑（按 20 座塘的汇水边界 ∪ 管网服务范围外扩 300 m，385 km²，30 m 网格
1,974,099 个三角单元，模拟 6 h，SWMM ↔ ANUGA 双向耦合）的 L1 / L2 对照，只代表这一片。

33 / 96 mm 没有 L2 结果：`available=False`，页面照实写「这场雨还没跑 L2」，不按 61 mm 比例外推。
已建成塘的位置与台账尺寸取 `storage_asset_dims.csv`（kind=pond，23 座；uid 就是 EPSG:32640 坐标），与雨无关，
三份都挂。报告说 L2 接的是 22 座，但没给清单，哪一座没接对不上，页面按台账 23 座全画并注明。
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[2]
PONDS_CSV = BACKEND_DIR / "data" / "processed" / "storage_asset_dims.csv"

#: 报告出处（页面原样标出）
SOURCE = {
    "solver": "SWMM 5.2.4",
    "dim": "1d",
    "date": "2026-09-27",
    "pipes": 260183,
    "nodes": 260297,
    "pieces": 8,
}

#: 按 (雨深 mm, 历时 h) 挂报告里的数；只有 61 mm / 2 h 一场
CITYWIDE: dict[tuple[float, float], dict[str, Any]] = {
    (61.0, 2.0): {
        # 两态对照：L1 现状 / L2 缓解后。None = 报告写「待全市一二维 / 待另跑」
        "compare": [
            {"key": "flood_volume_m3", "l1": 5485120, "l2": 5343916, "unit": "m³", "better": "lower"},
            {"key": "drain_main_h", "l1": 5.75, "l2": 6.00, "unit": "h", "better": None},
            {"key": "storage_m3", "l1": 2366119, "l2": 4820564, "unit": "m³", "better": "higher"},
            {"key": "temp_pumps", "l1": 0, "l2": 0, "unit": "", "better": None},
        ],
        "pending": ["recession_main", "recession_upstream", "max_area", "max_depth", "service_level"],
        "runoff_share_l1_pct": 61.9,          # L1 剩余占产流
        "ponds_connected": 22,
        "ponds_no_inflow": 5,
        # 接塘的 15 块管网：溢流 102.9 → 88.8 万 m³（−13.7%），其余管网 L2 与 L1 相同
        "connected_networks": {"count": 15, "l1_m3": 1029000, "l2_m3": 888000, "change_pct": -13.7},
        # L1 溢流 54%（297.0 万 m³）落在客户 39 个汇水区之外
        "outside_catchments": {"catchments": 39, "share_pct": 54, "l1_m3": 2970000},
        # 按客户汇水区，L1 剩余从大到小（差值 = L2 − L1，负数是塘减少的溢流）
        "catchments": [
            {"name": "RD RB2", "l1_m3": 707414, "diff_m3": -9},
            {"name": "MU09-MH-1861", "l1_m3": 489025, "diff_m3": 0},
            {"name": "OLD", "l1_m3": 206246, "diff_m3": 0},
            {"name": "ADMLINE", "l1_m3": 181809, "diff_m3": 0},
            {"name": "Mussafah RD09", "l1_m3": 155273, "diff_m3": 0},
            {"name": "29.RB2", "l1_m3": 151379, "diff_m3": -445},
            {"name": "Shaliela", "l1_m3": 123810, "diff_m3": 0},
            {"name": "RB1", "l1_m3": 84607, "diff_m3": 0},
            {"name": "Mussafah_02", "l1_m3": 60162, "diff_m3": 0},
            {"name": "Mussafah_08", "l1_m3": 54526, "diff_m3": 0},
            {"name": "Mussafah_01", "l1_m3": 51067, "diff_m3": 0},
            {"name": "B_C", "l1_m3": 47459, "diff_m3": 0},
        ],
        # Madinat Al Riyad 一二维试跑（只代表这一片）
        "trial": {
            "area_km2": 385,
            "cells": 1974099,
            "grid_m": 30,
            "sim_h": 6,
            "ponds": 20,
            "pond_capacity_m3": 1942555,
            "peak_area_t_h": 3.42,
            "compare": [
                {"key": "overflow_m3", "l1": 3506238, "l2": 2795934, "unit": "m³"},
                {"key": "max_depth_m", "l1": 3.07, "l2": 2.51, "unit": "m"},
                {"key": "area_ge_030_ha", "l1": 879.2, "l2": 832.6, "unit": "ha"},
                {"key": "area_ge_010_ha", "l1": 2972, "l2": 2909, "unit": "ha"},
                {"key": "surface_end_m3", "l1": 8225950, "l2": 7799542, "unit": "m³"},
            ],
            "pond_stored_m3": 541664,
            "pond_overland_m3": 330048,
            "ponds_full": ["RZS_20", "PND_38", "RZS_26", "RZS_27"],
            "ponds_underused": ["PND_39", "PND_37"],
        },
    },
}

NO_L2 = "这个任务没有第二道防线信息"

_ponds_cache: dict[str, Any] = {}


def ponds() -> list[dict[str, Any]]:
    """已建成调蓄塘（台账 23 座），按容量从大到小。坐标由 uid（ST_x_y，EPSG:32640）转 WGS84。"""
    p = PONDS_CSV
    if not p.is_file():
        return []
    mtime = p.stat().st_mtime
    if _ponds_cache.get("mtime") == mtime:
        return _ponds_cache["rows"]
    from rasterio.warp import transform

    with p.open(encoding="utf-8") as fh:
        raw = [r for r in csv.DictReader(fh) if r["kind"] == "pond"]
    xs = [float(r["uid"].split("_")[1]) for r in raw]
    ys = [float(r["uid"].split("_")[2]) for r in raw]
    lons, lats = transform("EPSG:32640", "EPSG:4326", xs, ys) if raw else ([], [])
    num = lambda v: float(v) if v not in ("", None) else None  # noqa: E731
    rows = [{
        "id": int(r["objectid"]),
        "district": r["district"].replace("Madinat AlRiyad", "Madinat Al Riyad"),
        "status": r["status"],
        "lining": None if r["unittype"] in ("", "N/A") else r["unittype"],
        "footprint_m2": num(r["footprint_m2"]),
        "depth_m": num(r["depth_m"]),
        "volume_m3": num(r["total_m3"]),
        "volume_tier": r["tier"],     # measured / derived / assumed，协议值
        "lat": round(lat, 6),
        "lon": round(lon, 6),
    } for r, lon, lat in zip(raw, lons, lats)]
    rows.sort(key=lambda x: -(x["volume_m3"] or 0))
    _ponds_cache.update(mtime=mtime, rows=rows)
    return rows


def applies(run: dict[str, Any], output_dir: str | None) -> bool:
    """全市二维交付的 L1 任务（#100 / #101 / #102）：第二道是本任务的 ?line=l2 视图，任务列表防线列写第二道。"""
    return bool(output_dir and "citywide" in output_dir and run.get("defence_line_key") == "line1")


def state_l2(output_dir: str | None) -> str | None:
    """多态任务里第二道现状那一态的 key（Mussafah #191 的 L2）；没有返回 None。

    用户 2026-09-28 要求这一态也按 L1 × (1 − 2.6%) 和 L1 比（交付的 L2 实算 ≡ L1，页面照实注明）。
    """
    from . import sim_results

    m = sim_results.run_states(output_dir)
    return next((s["key"] for s in (m or {}).get("states", []) if s["defence_line_key"] == "line2_current"), None)


def has_l2(run: dict[str, Any], output_dir: str | None) -> bool:
    return applies(run, output_dir) or state_l2(output_dir) is not None


#: 折算用哪场雨的报告：同雨有报告用同雨，没有就借 61 mm / 2 h（页面标「借用」）
ESTIMATE_FROM = (61.0, 2.0)


def estimate(depth: float | None, dur: float | None) -> dict[str, Any]:
    """L2 页面的三项指标与前 10 按「L2 = L1 × (1 − 减少比例)」折算（用户 2026-09-28 定的口径）。

    减少比例 = 一维报告全市洪水量（溢出井盖）的 (L1 − L2) / L1 = 141,204 / 5,485,120，取到 0.1%，即 2.6%。
    这是一维总量比例套到二维指标上的估算，不是 L2 二维演算；33 / 96 mm 没有自己的报告，借 61 mm 的比例。
    """
    key = (depth, dur) if (depth, dur) in CITYWIDE else ESTIMATE_FROM
    f = next(c for c in CITYWIDE[key]["compare"] if c["key"] == "flood_volume_m3")
    return {
        # 用户 2026-09-28 要求固定取报告写的 2.6%（精确值 2.574% 会让 221.1 折成 215.4，与报告口径对不上）
        "reduction_ratio": round((f["l1"] - f["l2"]) / f["l1"], 3),
        "basis": "flood_volume_m3",
        "from_storm": {"depth_mm": key[0], "duration_h": key[1]},
        "borrowed": key != (depth, dur),
    }


def load(conn: Any, run_id: int) -> dict[str, Any]:
    """`/api/simulations/{id}/l2`：不是全市 L1 交付的任务 404。"""
    from . import simulation as sim

    run = conn.execute(
        "select id, defence_line_key, depth_mm, duration_h, output_dir from run where id = %s", [run_id]).fetchone()
    if run is None:
        raise sim.SimulationError(404, sim.NOT_FOUND)
    if applies(run, run["output_dir"]):
        return {**for_run(run), "l1_network": network(conn, run_id, None), "storage": storage(conn, run_id)}
    key = state_l2(run["output_dir"])
    if key is None:
        raise sim.SimulationError(404, NO_L2)
    # 片区多态交付：只给折算比例，不挂全市报告数与全市塘位
    m = sim_results_states(run["output_dir"])
    same = next(s.get("same_as") for s in m["states"] if s["key"] == key)
    depth = float(run["depth_mm"]) if run.get("depth_mm") is not None else None
    dur = float(run["duration_h"]) if run.get("duration_h") is not None else None
    # 多态交付各态的储水设施落图走 `/layers/storage?state=`（storage_layer）
    return {"available": False, "storm": {"depth_mm": depth, "duration_h": dur}, "state": key, "same_as": same,
            "estimate": estimate(depth, dur), "ponds": [], "l1_network": network(conn, run_id, same or "L1")}


_state_ponds_cache: dict[str, Any] = {}


def state_ponds(d: Path) -> list[dict[str, Any]]:
    """实算的 L2 态（全市 #561）模型里真接上的塘：交付 `pond_storage.csv`（塘蓄水表）+ 耦合 .inp 的 [STORAGE] 坐标。

    塘位坐标取 .inp [COORDINATES]（EPSG:32640），按取整坐标对上台账 uid（ST_x_y）挂区属 / 状态 / 尺寸；
    台账 23 座里没进模型的那座（#20 Al Wathbah）不画。库容取交付表的「台账库容」（.inp 曲线口径），
    6 h 末蓄水、满度、地面漫流进塘、溢出照交付原样给。没有塘蓄水表的态（Mussafah 等）返回 []。
    """
    csv_p, inp = d / "pond_storage.csv", d / "coupled_scenario.inp"
    if not (csv_p.is_file() and inp.is_file()):
        return []
    key = f"{d}:{csv_p.stat().st_mtime}:{inp.stat().st_mtime}"
    if _state_ponds_cache.get("key") == key:
        return _state_ponds_cache["rows"]
    from rasterio.warp import transform

    from .sim_sww import _node_coords

    with csv_p.open(encoding="utf-8-sig") as fh:
        table = list(csv.DictReader(fh))
    coords = _node_coords(inp)
    ledger = {(int(float(p_["uid"].split("_")[1])), int(float(p_["uid"].split("_")[2]))): p_
              for p_ in _ledger_rows()}
    num = lambda v: float(v) if v not in ("", None) else None  # noqa: E731
    picked = []
    for r in table:
        name = r["塘"]
        xy = coords.get(f"PND_{name}")
        if xy is None:
            continue
        picked.append((r, name, xy, ledger.get((int(xy[0]), int(xy[1])))))
    lons, lats = transform("EPSG:32640", "EPSG:4326", [x[2][0] for x in picked], [x[2][1] for x in picked]) \
        if picked else ([], [])
    rows = []
    for (r, name, _xy, lg), lon, lat in zip(picked, lons, lats):
        rows.append({
            "id": lg["id"] if lg else name,
            "name": name,
            "district": lg["district"] if lg else None,
            "status": lg["status"] if lg else None,
            "lining": lg["lining"] if lg else None,
            "footprint_m2": lg["footprint_m2"] if lg else None,
            "depth_m": lg["depth_m"] if lg else None,
            "volume_m3": lg["volume_m3"] if lg else None,
            "volume_tier": lg["volume_tier"] if lg else None,
            "model_volume_m3": num(r["台账库容_m3"]),
            "stored_end_m3": num(r["6h末蓄水_m3"]),
            "fill_pct": num(r["满度_%"]),
            "surface_inflow_m3": num(r["其中地面漫流进塘_m3"]),
            "spill_m3": num(r["溢出_m3"]),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
        })
    rows.sort(key=lambda x: -(x["model_volume_m3"] or 0))
    _state_ponds_cache.update(key=key, rows=rows)
    return rows


#: 储水设施周边画多大范围的管网作背景（m）
PIPE_CONTEXT_M = 300.0
_LINK_SECS = {"[CONDUITS]": "conduit", "[PUMPS]": "pump", "[ORIFICES]": "orifice", "[WEIRS]": "weir", "[OUTLETS]": "outlet"}
_net_cache: dict[str, Any] = {}


def _inp_net(inp: Path) -> dict[str, Any]:
    """耦合 .inp 的蓄水单元 / 各类连接 / 管径 / 坐标 / 流量单位，按 mtime 缓存（全市 60 MB，约 1 s）。"""
    key = f"{inp}:{inp.stat().st_mtime}"
    if key in _net_cache:
        return _net_cache[key]
    coords: dict[str, tuple[float, float]] = {}
    links: list[tuple[str, str, str, str, float | None]] = []
    diam: dict[str, float] = {}
    storage: dict[str, float] = {}      # 名 → 最大深度
    units = "CMS"
    sec = None
    with inp.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s_ = line.strip()
            if not s_ or s_.startswith(";"):
                continue
            if s_.startswith("["):
                sec = s_.upper()
                continue
            t = s_.split()
            if sec == "[OPTIONS]" and t[0].upper() == "FLOW_UNITS" and len(t) > 1:
                units = t[1].upper()
            elif sec == "[STORAGE]" and len(t) >= 3:
                storage[t[0]] = float(t[2])
            elif sec in _LINK_SECS and len(t) >= 3:
                length = float(t[3]) if sec == "[CONDUITS]" and len(t) >= 4 else None
                links.append((t[0], _LINK_SECS[sec], t[1], t[2], length))
            elif sec == "[XSECTIONS]" and len(t) >= 3:
                try:
                    diam[t[0]] = float(t[2])
                except ValueError:
                    pass
            elif sec == "[COORDINATES]" and len(t) >= 3:
                coords[t[0]] = (float(t[1]), float(t[2]))
    net = {"coords": coords, "links": links, "diam": diam, "storage": storage, "units": units}
    _net_cache.clear()
    _net_cache[key] = net
    return net


def _to_wgs84(xys: list[tuple[float, float]]) -> list[list[float]]:
    from rasterio.warp import transform

    if not xys:
        return []
    lons, lats = transform("EPSG:32640", "EPSG:4326", [x for x, _ in xys], [y for _, y in xys])
    return [[round(la, 6), round(lo, 6)] for lo, la in zip(lons, lats)]


def storage_layer(conn: Any, run_id: int, state: str | None) -> dict[str, Any]:
    """`/api/simulations/{id}/layers/storage?state=`：这一态模型里的储水设施与接它们的管 / 泵，落图用。

    - 只给实算的态（same_as 为空）；≡ 别的态、或没有新增蓄水单元的，404。
    - 规划态只给相对父态新增的蓄水单元与连接（原有泵站、周边既有管网不给）。
    - 全市 #561 L2 用交付塘蓄水表 + 台账（`state_ponds`）；别的态（Mussafah L2+planned / suggested）按 .inp
      [STORAGE] + 报告 Storage Volume Summary（`state_storage`）。「新增」= 父态（states.json 的 parent）模型里没有。
    """
    from . import sim_results
    from . import simulation as sim

    row = conn.execute("select output_dir from run where id = %s", [run_id]).fetchone()
    if row is None:
        raise sim.SimulationError(404, sim.NOT_FOUND)
    m = sim_results.run_states(row["output_dir"])
    if not m:
        raise sim.SimulationError(404, NO_STORAGE)
    key = state or m["default"]
    st = next((x for x in m["states"] if x["key"] == key), None)
    if st is None or st.get("same_as"):
        raise sim.SimulationError(404, NO_STORAGE)
    d = sim_results._resolve(row["output_dir"], key)
    parent = sim_results._resolve(row["output_dir"], st["parent"]) if st.get("parent") else None
    ponds_ = state_ponds(d)
    if ponds_:
        return {"state": key, "basis": "ledger_ponds", "ponds": ponds_, "pipes": state_pond_pipes(d, parent)}
    # 规划态只给这一态的方案内容（用户 2026-09-28：原有设施不要）：相对父态新增的蓄水单元与连接，不画周边既有管网
    ponds_ = [p_ for p_ in state_storage(d, parent) if p_["is_new"]]
    if not ponds_:
        raise sim.SimulationError(404, NO_STORAGE)
    pipes = state_pond_pipes(d, parent) or {"inlets": [], "context": []}
    pipes = {**pipes, "inlets": [x for x in pipes["inlets"] if x["is_new"]], "context": []}
    return {"state": key, "basis": "inp_storage", "ponds": ponds_, "pipes": pipes}


NO_STORAGE = "这一态模型里没有储水设施"


def facilities_layer(conn: Any, run_id: int, state: str | None) -> dict[str, Any]:
    """`/api/simulations/{id}/layers/facilities?state=`：交付的设施 GIS（塘 / 泵点 / 排水去向 / 地面取水片），原样 GeoJSON。

    只给本态自己新增的设施（属性「所属态」等于本态）；L1 现状与上一态继承的都不给。
    """
    import json

    from . import sim_results
    from . import simulation as sim

    row = conn.execute("select output_dir from run where id = %s", [run_id]).fetchone()
    if row is None:
        raise sim.SimulationError(404, sim.NOT_FOUND)
    d = sim_results._resolve(row["output_dir"], state)
    f = d / "facilities.json"
    if not f.is_file():
        raise sim.SimulationError(404, NO_FACILITIES)
    data = json.loads(f.read_text(encoding="utf-8"))
    layers: dict[str, Any] = {}
    for k, fc in data["layers"].items():
        feats = []
        for ft in fc["features"]:
            pr = ft["properties"]
            if pr.get("所属态") != data["state"]:   # 只给本态自己新增的（用户 2026-09-29）：L1 与上一态继承的都不要
                continue
            feats.append({**ft, "properties": {**pr, "is_new": True}})
        layers[k] = {"type": "FeatureCollection", "features": feats}
    return {"state": data["state"], "layers": layers}


NO_FACILITIES = "这一态没有交付设施 GIS"


def state_storage(d: Path, parent: Path | None = None) -> list[dict[str, Any]]:
    """.inp [STORAGE] 各单元：容积按面积曲线积到最大深度（与效果分析同口径），峰值蓄水 / 满度 / 时刻取报告。"""
    from . import sim_perf

    inp, rpt = d / "coupled_scenario.inp", d / "swmm_coupled.rpt"
    if not inp.is_file():
        return []
    net = _inp_net(inp)
    names = [n for n in net["storage"] if n in net["coords"]]
    if not names:
        return []
    old = set(_inp_net(parent / "coupled_scenario.inp")["storage"]) \
        if parent and (parent / "coupled_scenario.inp").is_file() else set()
    cap = sim_perf._storage_capacity(inp)
    peak = _storage_peaks(rpt) if rpt.is_file() else {}
    ll = _to_wgs84([net["coords"][n] for n in names])
    rows = [{"id": n, "name": n, "is_new": bool(parent) and n not in old, "depth_m": net["storage"][n],
             "model_volume_m3": round(cap[n], 1) if n in cap else None, **peak.get(n, {}),
             "lat": xy[0], "lon": xy[1]} for n, xy in zip(names, ll)]
    rows.sort(key=lambda r: (not r["is_new"], -(r["model_volume_m3"] or 0)))
    return rows


def _storage_peaks(rpt: Path) -> dict[str, dict[str, Any]]:
    """报告 Storage Volume Summary：最大蓄水（1000 m³ → m³）、最高满度 %、出现时刻（min）。"""
    out: dict[str, dict[str, Any]] = {}
    on = False
    with rpt.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "Storage Volume Summary" in line:
                on = True
                continue
            if on and "Summary" in line:
                break
            t = line.split()
            if on and len(t) >= 9 and ":" in t[8]:
                try:
                    hh, mm = t[8].split(":")
                    out[t[0]] = {"max_stored_m3": round(float(t[5]) * 1000, 1), "max_fill_pct": float(t[6]),
                                 "max_stored_min": int(t[7]) * 1440 + int(hh) * 60 + int(mm)}
                except ValueError:
                    continue
    return out


_pipes_cache: dict[str, Any] = {}


def state_pond_pipes(d: Path, parent: Path | None = None) -> dict[str, Any] | None:
    """储水设施的接入连接与周边管网（耦合 .inp + .rpt）。

    - `inlets`：一端是蓄水单元的连接（管 / 泵 / 孔口 / 堰），外加父态模型里没有的新增连接（方案新建的管、泵）；
      带类型、管径、长度与报告 Link Flow Summary 的峰值流量（换成 m³/s）/ 时刻 / 峰值满流比。
      全市 #561 的塘接法是算力侧推断的（无出水口管网的最低井 100 m 内有塘就用短管接入），台账没这么写。
    - `context`：两端都在某个蓄水单元 `PIPE_CONTEXT_M` 以内的其余连接，只画细线作背景。
    坐标 EPSG:32640 → WGS84，线段只画两端点（交付没有 [VERTICES]）。
    """
    import numpy as np

    from .sim_sww import _FLOW_M3S

    inp, rpt = d / "coupled_scenario.inp", d / "swmm_coupled.rpt"
    if not inp.is_file():
        return None
    pinp = parent / "coupled_scenario.inp" if parent else None
    key = f"{inp}:{inp.stat().st_mtime}:{pinp if pinp and pinp.is_file() else ''}"
    if _pipes_cache.get("key") == key:
        return _pipes_cache["data"]
    net = _inp_net(inp)
    coords, storage = net["coords"], net["storage"]
    old = {x[0] for x in _inp_net(pinp)["links"]} if pinp and pinp.is_file() else None
    net = _inp_net(inp)     # 父态解析会顶掉缓存，取回本态
    pond_xy = np.array([coords[n] for n in storage if n in coords]) if storage else np.zeros((0, 2))

    def near(n: str) -> bool:
        xy = coords.get(n)
        return xy is not None and bool(len(pond_xy)) and \
            float(np.min(np.hypot(pond_xy[:, 0] - xy[0], pond_xy[:, 1] - xy[1]))) <= PIPE_CONTEXT_M

    inlets, context = [], []
    for ln in net["links"]:
        cid, _k, a, b, _len = ln
        if a not in coords or b not in coords:
            continue
        is_new = old is not None and cid not in old
        if a in storage or b in storage or is_new:
            inlets.append((ln, is_new))
        elif near(a) and near(b):
            context.append(ln)
    flows = _link_flows(rpt, {x[0][0] for x in inlets}, _FLOW_M3S.get(net["units"], 1.0)) if rpt.is_file() else {}

    ends = [coords[n] for ln in [x[0] for x in inlets] + context for n in (ln[2], ln[3])]
    ll = _to_wgs84(ends)
    out_in, out_ctx = [], []
    for k, ln in enumerate([x[0] for x in inlets] + context):
        cid, kind, a, b, length = ln
        seg = [ll[2 * k], ll[2 * k + 1]]
        if k < len(inlets):
            sto = b if b in storage else a if a in storage else None
            pond = sto.removeprefix("PND_") if sto and sto.startswith("PND_") else sto
            out_in.append({"id": cid, "kind": kind, "pond": pond, "is_new": inlets[k][1], "from": a, "to": b,
                           "length_m": length, "diameter_m": net["diam"].get(cid) if kind == "conduit" else None,
                           "latlngs": seg, **flows.get(cid, {})})
        else:
            out_ctx.append({"diameter_m": net["diam"].get(cid), "latlngs": seg})
    data = {"inlets": out_in, "context": out_ctx, "context_radius_m": PIPE_CONTEXT_M}
    _pipes_cache.update(key=key, data=data)
    return data


def _link_flows(rpt: Path, ids: set[str], to_m3s: float = 1.0) -> dict[str, dict[str, Any]]:
    """报告 Link Flow Summary 里指定连接的峰值流量（按 FLOW_UNITS 换成 m³/s）、出现时刻（min）、峰值满流比（仅管道）。"""
    out: dict[str, dict[str, Any]] = {}
    on = False
    with rpt.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "Link Flow Summary" in line:
                on = True
                continue
            if on and ("Flow Classification Summary" in line or "Conduit Surcharge Summary" in line):
                break
            t = line.split()
            if on and len(t) >= 5 and t[0] in ids and ":" in t[4]:
                hh, mm = t[4].split(":")
                r = {"max_flow_m3s": round(float(t[2]) * to_m3s, 4),
                     "max_flow_min": int(t[3]) * 1440 + int(hh) * 60 + int(mm)}
                if t[1] == "CONDUIT" and len(t) >= 8:
                    r["max_full_flow"] = float(t[6])
                out[t[0]] = r
    return out


def _ledger_rows() -> list[dict[str, Any]]:
    """台账塘（`ponds()` 的行）带回 uid，给 `state_ponds` 按坐标对。"""
    with PONDS_CSV.open(encoding="utf-8") as fh:
        uid = {int(r["objectid"]): r["uid"] for r in csv.DictReader(fh) if r["kind"] == "pond"}
    return [{**p_, "uid": uid[p_["id"]]} for p_ in ponds() if p_["id"] in uid]


def network(conn: Any, run_id: int, state: str | None) -> dict[str, Any] | None:
    """第一道的三项管网指标（第二道页的 Outflow Volume / Flooded Nodes / Flood Duration），与效果分析同一口径：
    溢流量 = 耦合的管网溢出地表总量；溢流节点 = Node Flooding Summary 里溢流量过阈值的节点；溢流时长 = 它们 Hours Flooded 的平均。"""
    from . import sim_perf
    from . import sim_ponding as sp
    from . import sim_results

    d = sim_results._run_dir(conn, run_id, state)
    s = sp._summary(d)
    rpt = sp._output(d, s, "swmm_report")
    if rpt is None:
        return None
    r = sim_perf._rpt(rpt)
    return {"overflow_to_surface_m3": (s.get("coupling") or {}).get("total_swmm_to_anuga_m3"),
            "flooded_node_count": r["flooded_node_count"], "mean_flood_hours": r["mean_flood_hours"]}


#: 一维报告里 L2 比 L1 多出来的储水能力（m³）= 接入的 22 座塘；与雨无关
POND_STORAGE_M3 = 4820564 - 2366119


@lru_cache(maxsize=8)
def _inp_storage(path: str, mtime: float) -> float:
    from . import sim_perf

    inp = Path(path)
    return sim_perf._pipe_volume(inp) + sum(sim_perf._storage_capacity(inp).values())


def storage(conn: Any, run_id: int) -> dict[str, Any] | None:
    """全市单态交付的系统储水能力（总览第四项），与雨无关、与效果分析 `system_storage_m3` 同口径（按 .inp 自算）。

    L1 = 本交付 .inp 的满管管容 + 调蓄单元容积（#100–102 是同一张 93,669 根管的网，1,058,142 m³）。
    不取一维报告的 2,366,119：那是 9/27 按客户 GDB 重建的 260,183 根管的另一张网。
    L2 没有同网实算，= 本交付 L1 + 一维报告里 22 座塘带来的增量（`POND_STORAGE_M3`，借用，页面注明）。
    """
    from . import sim_ponding as sp
    from . import sim_results

    d = sim_results._run_dir(conn, run_id, None)
    inp = sp._output(d, sp._summary(d), "swmm_input")
    if inp is None:
        return None
    l1 = _inp_storage(str(inp), inp.stat().st_mtime)
    return {"l1": round(l1), "l2": round(l1 + POND_STORAGE_M3), "basis": "inp", "l2_basis": "l1_plus_report_ponds",
            "pond_m3": POND_STORAGE_M3}


def sim_results_states(output_dir: str | None) -> dict[str, Any]:
    from . import sim_results

    return sim_results.run_states(output_dir) or {"states": []}


def for_run(run: dict[str, Any]) -> dict[str, Any]:
    depth = float(run["depth_mm"]) if run.get("depth_mm") is not None else None
    dur = float(run["duration_h"]) if run.get("duration_h") is not None else None
    data = CITYWIDE.get((depth, dur)) if depth is not None and dur is not None else None
    pl = ponds()
    return {
        "estimate": estimate(depth, dur),
        "available": data is not None,
        "storm": {"depth_mm": depth, "duration_h": dur},
        "available_storms": [{"depth_mm": d, "duration_h": h} for d, h in CITYWIDE],
        "source": SOURCE,
        "ponds": pl,
        "ponds_total_m3": round(sum(p["volume_m3"] or 0 for p in pl)),
        **(data or {}),
    }
