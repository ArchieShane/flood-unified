"""演算末路面残留积水：按洼地分块，逐块说清「为什么退不掉」（`/layers/residual`）。

起因（2026-09-27，Mussafah #191 L2+B）：箱涵加上后峰值面积减半，演算末仍有 5,470 m³ 停在路面，
而且 4 h 以后二维 → 一维回灌几乎为 0、路面水量是平的——不是「还在退」，是「退不掉」。
二维计算域没有边界出流，地表水唯一的出路是从耦合井回灌管网；本模块逐块查这条路为什么断了。

## 算法

① 末帧逐格水深取最大水深图层的 `final_depth_m`（.sww 最后一个时刻、15 m 格平均），
   水深 ≥ 0.01 m 的格八连通成块，块水量 = Σ 水深 × 格面积；
② 井口标高 = 耦合 SWMM `[JUNCTIONS]` / `[STORAGE]` 的内底 + 最大深度，井坐标取 `[COORDINATES]` 落格；
③ 逐井回灌量 = 报告 Node Inflow Summary 的侧向入流量 − `[INFLOWS]` 给定入流的积分
   （二维回灌在 SWMM 里记作侧向入流；Mussafah L2+B 反算合计 3,749 m³，耦合记录 3,798 m³）；
④ 每块归一类（类别 key 是协议值，页面翻）：
   - `no_inlet`   洼地里没有井——块内没有任何一格落着井；
   - `below_rim`  块内有井，但块内最高水面仍低于这些井的最低井口（差 ≤ `RIM_MARGIN_M` 也算低于）；
   - `not_taking` 块内有井、水面高过井口 `RIM_MARGIN_M` 以上，井却不收——耦合规则的问题，
                  算力侧耦合代码不在本仓库，只能列出来问交付方。
⑤ 附带：最近的井、最近「回灌过」的井、最近调蓄设施（`[STORAGE]`）及距离；最后 1 h 块水量变化；
   各调蓄设施的最大充满度（报告 Storage Volume Summary），用来排除「容量不够」。

只认 `sim_sww` 抽出来的四态交付（要有 swmm_input / swmm_report / terrain_grid.npz）；别的交付 422。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from .sim_delivery import load_summary
from .sim_ponding import _cell_lonlat, _grid, _label8, _output
from .sim_sww import SCHEMA as SWW_SCHEMA, _FLOW_M3S, _node_coords, _swmm_hours

WET_M = 0.01
RIM_MARGIN_M = 0.02          # 水面比井口高不过 2 cm 仍算「够不着」：格平均水面本身就有这个量级的误差
RETURNED_MIN_M3 = 0.5        # 回灌量反算的舍入噪声（报告体积只有 3 位小数 × 10^6 L）
TREND_MIN = 60.0             # 「最后 1 h 变化」
CLASSES = ("no_inlet", "below_rim", "not_taking")


class ResidualError(Exception):
    pass


# ---------------------------------------------------------------------------
# SWMM 输入 / 报告
# ---------------------------------------------------------------------------


def _inp_tables(inp: Path) -> dict[str, Any]:
    """井口标高、调蓄设施名单、各节点给定入流的体积（m³）。"""
    sec = None
    units = "CMS"
    rim: dict[str, float] = {}
    storage: list[str] = []
    inflows: list[tuple[str, str, float, float]] = []
    ts: dict[str, list[tuple[float, float]]] = {}
    with inp.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if s.startswith("["):
                sec = s.upper()
                continue
            if not s or s.startswith(";"):
                continue
            t = s.split()
            if sec == "[OPTIONS]" and t[0].upper() == "FLOW_UNITS":
                units = t[1].upper()
            elif sec in ("[JUNCTIONS]", "[STORAGE]") and len(t) >= 3:
                rim[t[0]] = float(t[1]) + float(t[2])
                if sec == "[STORAGE]":
                    storage.append(t[0])
            elif sec == "[INFLOWS]" and len(t) >= 3 and t[1].upper() == "FLOW":
                inflows.append((t[0], t[2], float(t[5]) if len(t) > 5 else 1.0, float(t[6]) if len(t) > 6 else 0.0))
            elif sec == "[TIMESERIES]" and len(t) == 3:
                ts.setdefault(t[0], []).append((_swmm_hours(t[1]), float(t[2])))
    k = _FLOW_M3S.get(units)
    if k is None:
        raise ResidualError(f"{inp.name}：不认识的流量单位 {units}")
    given: dict[str, float] = {}
    for node, name, sfac, _base in inflows:
        pts = ts.get(name)
        if not pts:
            continue
        a = np.array(pts)
        # 与 SWMM 一致按线性插值；给定基线在四态交付里都是 0，不计
        given[node] = given.get(node, 0.0) + float(np.trapezoid(a[:, 1], a[:, 0] * 3600)) * sfac * k
    return {"rim": rim, "storage": storage, "given": given}


_ROW = re.compile(r"^\s+(\S+)\s+")


def _rpt_section(text: str, title: str) -> list[list[str]]:
    i = text.find(title)
    if i < 0:
        return []
    body = text[i:].split("\n", 3)[3]
    rows = []
    for line in body.splitlines():
        if line.strip().startswith("*****"):
            break
        t = line.split()
        if len(t) >= 6 and not line.lstrip().startswith("-") and _ROW.match(line):
            rows.append(t)
    return rows


def _rpt_tables(rpt: Path) -> dict[str, Any]:
    text = rpt.read_text(encoding="utf-8", errors="replace")
    lateral: dict[str, float] = {}
    for t in _rpt_section(text, "Node Inflow Summary"):
        # Node Type MaxLat MaxTot days hh:mm LatVol TotVol Err
        if len(t) >= 9 and re.fullmatch(r"\d\d:\d\d", t[5]):
            lateral[t[0]] = float(t[6]) * 1000
    flooded: dict[str, float] = {}
    for t in _rpt_section(text, "Node Flooding Summary"):
        if len(t) >= 7 and re.fullmatch(r"\d\d:\d\d", t[4]):
            flooded[t[0]] = float(t[5]) * 1000
    fill: dict[str, float] = {}
    for t in _rpt_section(text, "Storage Volume Summary"):
        # Name AvgVol AvgPct Evap Exfil MaxVol MaxPct days hh:mm MaxOut
        if len(t) >= 10 and re.fullmatch(r"\d\d:\d\d", t[8]):
            fill[t[0]] = float(t[6])
    return {"lateral": lateral, "flooded": flooded, "fill": fill}


# ---------------------------------------------------------------------------
# 主算
# ---------------------------------------------------------------------------


def _final_depth(d: Path, s: dict, n: int) -> np.ndarray:
    p = _output(d, s, "maximum_depth")
    if p is None:
        raise ResidualError("成果里没有最大水深图层")
    out = np.zeros(n)
    for f in json.loads(p.read_text(encoding="utf-8"))["features"]:
        pr = f["properties"]
        if pr.get("final_depth_m") is None:
            raise ResidualError("最大水深图层没有末帧水深")
        out[pr["cell_id"]] = pr["final_depth_m"]
    return out


def _frame_depth(d: Path, s: dict, n: int, minutes: float) -> tuple[np.ndarray, float] | None:
    """离 `minutes` 最近的那一帧逐格水深。"""
    man = _output(d, s, "timeline_manifest")
    if man is None:
        return None
    snaps = json.loads(man.read_text(encoding="utf-8"))["snapshots"]
    if not snaps:
        return None
    f = min(snaps, key=lambda x: abs(x["time_minutes"] - minutes))
    out = np.zeros(n)
    for ft in json.loads((man.parent.parent / f["path"]).read_text(encoding="utf-8"))["features"]:
        out[ft["properties"]["cell_id"]] = ft["properties"]["depth_m"]
    return out, float(f["time_minutes"])


def compute(run_dir: Path) -> dict[str, Any]:
    d = run_dir.resolve()
    s = load_summary(d)
    # 全市一二维（sim_city）不适用：地表直接受雨、边界自由出流，「只能靠回灌井退水」的前提不成立；
    # 且 26 万个井落到 250 m 格上逐块比，内存峰值 34 GB 会把 API 进程打死（2026-09-28 #561）
    if s.get("schema") != SWW_SCHEMA:
        raise ResidualError("只有片区四态交付拆得出残留积水的成因")
    inp, rpt = _output(d, s, "swmm_input"), _output(d, s, "swmm_report")
    if inp is None or rpt is None or not (d / "terrain_grid.npz").is_file():
        raise ResidualError("这份成果没有耦合一维模型或地形，拆不出残留积水的成因")
    g = _grid(d, s)
    nr, nc, cs = g["nr"], g["nc"], g["cs"]
    n = nr * nc
    cell_a = cs * cs
    t = np.load(d / "terrain_grid.npz", allow_pickle=True)
    dem = np.where(t["land_mask"], t["values"], np.nan).ravel()
    final = _final_depth(d, s, n)
    end_min = float((s.get("domain") or {}).get("simulation_duration_minutes") or 0)
    earlier = _frame_depth(d, s, n, end_min - TREND_MIN)

    tb = _inp_tables(inp)
    rp = _rpt_tables(rpt)
    coords = _node_coords(inp)
    ids = [k for k in coords if k in tb["rim"]]
    xy = np.array([coords[k] for k in ids]) if ids else np.zeros((0, 2))
    col = np.floor((xy[:, 0] - g["x0"]) / cs).astype(int)
    row = np.floor((g["ytop"] - xy[:, 1]) / cs).astype(int)
    inside = (row >= 0) & (row < nr) & (col >= 0) & (col < nc)
    ncell = np.where(inside, row * nc + col, -1)
    rim = np.array([tb["rim"][k] for k in ids])
    returned = np.array([rp["lateral"].get(k, 0.0) - tb["given"].get(k, 0.0) for k in ids])
    took = returned > RETURNED_MIN_M3
    flooded = np.array([rp["flooded"].get(k, 0.0) > RETURNED_MIN_M3 for k in ids])
    sto = [(k, coords[k]) for k in tb["storage"] if k in coords]

    lab, k = _label8((final >= WET_M).reshape(nr, nc))
    lab = lab.ravel()
    lonlat = _cell_lonlat(d, s)
    wl = dem + final
    cell_of_node: dict[int, list[int]] = {}
    for j, c in enumerate(ncell):
        if c >= 0:
            cell_of_node.setdefault(int(c), []).append(j)

    ponds = []
    order = np.argsort(lab)
    bounds = np.searchsorted(lab[order], np.arange(1, k + 2))
    for c in range(1, k + 1):
        cells = order[bounds[c - 1]:bounds[c]]
        vol = float(final[cells].sum() * cell_a)
        if vol < 1.0:
            continue
        rr, cc = cells // nc, cells % nc
        cx = g["x0"] + (cc + 0.5) * cs
        cy = g["ytop"] - (rr + 0.5) * cs
        deep = int(cells[np.argmax(final[cells])])
        surf = float(np.nanmax(wl[cells]))
        in_nodes = [j for cell in cells.tolist() for j in cell_of_node.get(cell, [])]
        if not in_nodes:
            cls = "no_inlet"
        elif float(rim[in_nodes].min()) < surf - RIM_MARGIN_M:
            cls = "not_taking"
        else:
            cls = "below_rim"
        dist = np.hypot(xy[:, 0][:, None] - cx[None], xy[:, 1][:, None] - cy[None]).min(1) if len(ids) else np.array([])
        near = int(np.argmin(dist)) if len(dist) else None
        near_took = int(np.where(took)[0][np.argmin(dist[took])]) if took.any() else None
        sd = {name: float(np.hypot(cx - x, cy - y).min()) for name, (x, y) in sto}
        sn = min(sd, key=sd.get) if sd else None
        lon, lat = lonlat.get(deep, (None, None))
        ponds.append({
            "class": cls,
            "volume_m3": round(vol, 1),
            "volume_before_m3": round(float(earlier[0][cells].sum() * cell_a), 1) if earlier else None,
            "area_m2": float(len(cells) * cell_a),
            "max_depth_m": round(float(final[deep]), 3),
            "floor_m": round(float(np.nanmin(dem[cells])), 2),
            "surface_m": round(surf, 2),
            "inlets": [{"id": ids[j], "rim_m": round(float(rim[j]), 2), "returned_m3": round(float(max(returned[j], 0.0)), 1),
                        "overflowed": bool(flooded[j])} for j in in_nodes],
            "nearest_inlet": {"id": ids[near], "distance_m": round(float(dist[near])), "rim_m": round(float(rim[near]), 2)}
            if near is not None else None,
            "nearest_returning_inlet": {"id": ids[near_took], "distance_m": round(float(dist[near_took]))}
            if near_took is not None else None,
            "nearest_storage": {"id": sn, "distance_m": round(sd[sn]), "max_fill_pct": rp["fill"].get(sn)} if sn else None,
            "cell": deep,
            "x": float(g["x0"] + (deep % nc + 0.5) * cs),
            "y": float(g["ytop"] - (deep // nc + 0.5) * cs),
            "lon": round(lon, 6) if lon is not None else None,
            "lat": round(lat, 6) if lat is not None else None,
            "cells": cells.tolist(),
        })
    ponds.sort(key=lambda p: -p["volume_m3"])
    for i, p in enumerate(ponds, 1):
        p["rank"] = i

    res = s.get("results") or {}
    cp = s.get("coupling") or {}
    total = float(res.get("final_surface_volume_m3") or final.sum() * cell_a)
    by = {c: {"count": 0, "volume_m3": 0.0} for c in CLASSES}
    for p in ponds:
        by[p["class"]]["count"] += 1
        by[p["class"]]["volume_m3"] += p["volume_m3"]
    for v in by.values():
        v["volume_m3"] = round(v["volume_m3"], 1)
    return {
        "summary": {
            "end_minutes": end_min,
            "trend_from_minutes": earlier[1] if earlier else None,
            "surface_volume_m3": round(total, 1),
            "pond_count": len(ponds),
            "pond_volume_m3": round(sum(p["volume_m3"] for p in ponds), 1),
            "by_class": by,
            "overflow_m3": cp.get("total_swmm_to_anuga_m3"),
            "return_m3": cp.get("total_anuga_to_swmm_m3"),
            "boundary_out_m3": cp.get("boundary_outflow_m3"),
            "node_count": len(ids),
            "returning_node_count": int(took.sum()),
            "returned_back_calc_m3": round(float(returned[took].sum()), 1),
            "storage_fill_pct": {name: rp["fill"].get(name) for name, _ in sto},
        },
        "params": {"wet_m": WET_M, "rim_margin_m": RIM_MARGIN_M, "trend_min": TREND_MIN},
        "ponds": ponds,
    }


@lru_cache(maxsize=16)
def _cached(run_dir: str, mtime: float) -> dict[str, Any]:
    return compute(Path(run_dir))


def load(run_dir: Path) -> dict[str, Any]:
    """按「目录 + 摘要 mtime」缓存在进程里（算一次不到 1 s，不落盘）；出门前按最近 POI 挂名。"""
    from . import poi

    d = run_dir.resolve()
    out = json.loads(json.dumps(_cached(str(d), (d / "delivery_summary.json").stat().st_mtime)))
    for p in out["ponds"]:
        hit = poi.nearest(p["x"], p["y"])
        p["poi"] = {"name": hit["name"], "distance_m": hit["distance_m"]} if hit else None
    return out
