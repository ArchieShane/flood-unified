"""仿真任务的结果图层：把算力侧交付的耦合演算成果目录挂到 Run history 的一条任务上，供详情页落图。

交付目录（v1 `interactive_coupled_2d_delivery.v1` / v2 `public_citywide_2d_delivery.v2`，
两版摘要的差异由 `sim_delivery.load_summary` 归一，本模块只看归一后的形状）长这样：

    <run_id>/
        input-routing30s-72h/scenario.json          雨情与边界参数（v2 为 input-routing30s-74h-total/）
        runs/<variant>/delivery_summary.json         交付摘要（网格、结果指标、各输出文件的相对路径）
        runs/<variant>/maximum_depth_wgs84.geojson   逐格最大水深（250 m 方格，WGS84）
        runs/<variant>/temporal_snapshots/           每 5 min 一帧的地表水深 + manifest.json
        runs/<variant>/swmm_dynamic.rpt              SWMM 报告（取起止时刻、溢流节点数）

口径：
- 任务表 `run.output_dir` 记**成果目录相对 `backend/data/` 的路径**，只读不写；目录不在
  `backend/data/` 之下的一律拒绝（防 `..` 越界）。
- 时序快照里出现的格子全都包含在最大水深图层里（已核对：1299 ⊇ 各帧），故前端几何只取一次
  （最大水深图层），每帧只传「格子号 → 水深」的紧凑数组（~8 KB），不重复传 300 KB 的多边形。
- 结果指标只回写成果里直接有的：管井溢出点数 = SWMM「Node Flooding Summary」的行数。
  仍积水点、排水量、积水时长这份成果没有对应口径，留空，不拿格子数顶替。
- 本次演算是单向耦合（SWMM → ANUGA）的诊断性运行，未标定、未经工程认可（摘要里的
  `claim_boundary`），页面上要原样标出。

用法（登记一份交付目录，可重复执行，按 output_dir 去重）：

    PYTHONPATH=src .venv/bin/python -m stormwater.sim_results import \\
        data/temp/abu-dhabi-citywide-coupled-33mm-2h-250m-one-way-land-masked-74h
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg

from . import sim_delivery
from . import simulation as sim

BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_ROOT = BACKEND_DIR / "data"

NO_LAYERS = "这个任务没有结果图层"
NO_FRAME = "没有这一帧"
NO_PONDING = "这份成果算不出溢水点"
NO_RESIDUAL = "这份成果拆不出残留积水的成因"
NO_STATE = "这份成果没有这个方案态"
NO_EFFECT = "这个任务不是多态成果，没有方案效果可比"
NO_PROPOSED_PONDS = "没有拟建调蓄池数据"
NO_HOTSPOTS = "这个任务没有积水点"
NO_EQUIPMENT = "这个任务没有应对设备配置数据"
NO_PONDING_GE = "这个任务没有各态积水量（ponding_ge.json）"
NO_PIPES = "这个任务没有管线图层（pipes.geojson）"
NO_PARAMS = "这个任务没有推演参数（params.json）"
NO_OUTPUTS = "这个任务没有成果位置记录（outputs.json）"

#: 拟建调蓄池（交付方给的选址方案，EPSG:32640 多边形），与具体任务无关，详情页按需叠加
PROPOSED_PONDS_FILE = DATA_ROOT / "temp" / "proposed_ponds.geojson"
MESSAGES = (NO_LAYERS, NO_FRAME, NO_PONDING, NO_RESIDUAL, NO_STATE, NO_EFFECT, NO_HOTSPOTS, NO_EQUIPMENT, NO_PONDING_GE, NO_PIPES, NO_PARAMS, NO_OUTPUTS)

#: 全市应对设备配置（2026-10-28 交付）：放在成果目录根上的两个文件，第一档固定设施（L2 sugg，总水量 45%）、第二档移动泵与罐车（L3，20%）
EQUIPMENT_FILES = {"fixed": "equipment_fixed.json", "mobile": "equipment_mobile.json"}
#: 原文件里只留数字和协议值；英文标题 / 范围 / 备注由页面按中英文自己写，不透传
PONDING_GE_FILE = "ponding_ge.json"
_EQUIPMENT_KEEP = ("generated", "inputs", "assumptions", "scenarios")

#: 多态交付（一个任务涵盖几种工况，如 Mussafah 四态）的清单文件，放在 `output_dir` 根上，见 `sim_sww.py`：
#: {"default": "L1", "states": [{"key", "label", "defence_line_key", "dir", "same_as"}]}
STATES_FILE = "states.json"

#: 快照水深的输出精度（毫米取整，最小出图水深 10 mm，足够分档着色）
DEPTH_DECIMALS = 3


# ---------------------------------------------------------------------------
# 读取成果
# ---------------------------------------------------------------------------


def _states(root: Path) -> dict[str, Any] | None:
    man = root / STATES_FILE
    return _load(man) if man.is_file() else None


def _posix(output_dir: str) -> str:
    """库里的 output_dir 若是在 Windows 上登记的（反斜杠分隔），换成正斜杠再拼路径。"""
    return output_dir.replace("\\", "/")


def run_states(output_dir: str | None) -> dict[str, Any] | None:
    """多态任务的态清单（给 get_run / list_runs）；单一交付返回 None。"""
    if not output_dir:
        return None
    root = (DATA_ROOT / _posix(output_dir)).resolve()
    if DATA_ROOT.resolve() not in root.parents:
        return None
    return _states(root)


def _resolve(output_dir: str | None, state: str | None = None) -> Path:
    """任务的成果目录。多态任务按 `state` 取那一态的子目录（不给取清单里的 default）；单一交付给了 state 就 404。"""
    if not output_dir:
        raise sim.SimulationError(404, NO_LAYERS)
    root = DATA_ROOT.resolve()
    p = (DATA_ROOT / _posix(output_dir)).resolve()
    if root not in p.parents:
        raise sim.SimulationError(404, NO_LAYERS)
    m = _states(p)
    if m is not None:
        key = state or m["default"]
        hit = next((s for s in m["states"] if s["key"] == key), None)
        if hit is None:
            raise sim.SimulationError(404, NO_STATE)
        sub = (p / hit["dir"]).resolve()
        if p not in sub.parents:
            raise sim.SimulationError(404, NO_LAYERS)
        p = sub
    elif state:
        raise sim.SimulationError(404, NO_STATE)
    if not (p / "delivery_summary.json").is_file():
        raise sim.SimulationError(404, NO_LAYERS)
    return p


def _run_dir(conn: psycopg.Connection, run_id: int, state: str | None = None) -> Path:
    row = conn.execute("select output_dir from run where id = %s", [run_id]).fetchone()
    if row is None:
        raise sim.SimulationError(404, sim.NOT_FOUND)
    return _resolve(row["output_dir"], state)


def _load(p: Path) -> Any:
    return sim_delivery._json(p)


def _summary(d: Path) -> dict[str, Any]:
    """交付摘要，v1 / v2 两版 schema 归一后的形状（见 `sim_delivery`）。"""
    return sim_delivery.load_summary(d)


def _output(d: Path, key: str) -> Path:
    rel = _summary(d)["outputs"].get(key)
    p = (d / rel).resolve() if rel else None
    if p is None or d not in p.parents or not p.is_file():
        raise sim.SimulationError(404, NO_LAYERS)
    return p


def _manifest(d: Path) -> list[dict[str, Any]]:
    return _load(_output(d, "timeline_manifest"))["snapshots"]


def layers_meta(conn: psycopg.Connection, run_id: int, state: str | None = None) -> dict[str, Any]:
    """详情页地图的元数据：网格、结果摘要、时间轴（帧号 + 分钟），不带几何。"""
    d = _run_dir(conn, run_id, state)
    s = _summary(d)
    geo = _load(_output(d, "maximum_depth"))
    xs, ys = [], []
    for f in geo["features"]:
        for x, y in f["geometry"]["coordinates"][0]:
            xs.append(x)
            ys.append(y)
    return {
        "run_ref": s.get("run_id"),
        "solver": s.get("solver"),
        "status": s.get("status"),
        "claim_boundary": s.get("claim_boundary"),
        "domain": s.get("domain"),
        "results": s.get("results"),
        "coupling": s.get("coupling"),
        "model_configuration": s.get("model_configuration"),
        # v2 才有：缺末尾窗口时 partial = True，页面要标「退水未验证」
        "delivery": s.get("delivery"),
        "admission": s.get("admission"),
        "partial": sim_delivery.is_partial(s),
        # 模型的真实输入是逐井入流过程线（sim_sww 抽的）时为 True，详情页画入流过程线而不是派生雨型
        "has_inflow": bool(s["outputs"].get("inflow_hydrograph")),
        # [[南, 西], [北, 东]]，Leaflet fitBounds 直接用
        "bounds": [[min(ys), min(xs)], [max(ys), max(xs)]] if xs else None,
        "cell_count": len(geo["features"]),
        "frames": [{"index": f["index"], "time_minutes": f["time_minutes"]} for f in _manifest(d)],
    }


def timeline(conn: psycopg.Connection, run_id: int, state: str | None = None) -> dict[str, Any]:
    """播放用的逐帧「当前值」，与 `frames` 一一对应；某项这份交付拿不出就给 null，前端不拿别的口径顶替。

    - 积水面积 / 最深水深：有 `surface_series`（sww 交付）时取同一时刻的三角形口径（水深 ≥ 0.02 m，与卡片的
      「最大积水面积」同口径，峰值那一帧就等于它）；没有就给 null，前端按本帧 ≥ 1 cm 的格子自己算（`area_basis = cell`）。
    - 地表积水总量 `volume_m3`：有 `surface_series` 时取同一帧的 Σ(水深 × 单元面积)，没有就给 null。
    - 累计溢水量：截至本帧的 SWMM → 地表累计量。sww 交付取耦合记录（逐 10 min，帧落在两点之间线性插值）；
      全市交付取回执逐窗累加（5 min 一窗，与帧对齐）。末帧等于卡片上的全程量。超出记录范围给 null。
    - 已出现的溢水点：片区成立时刻 `onset_min` ≤ 本帧的个数，与地图逐帧点亮同一判据；片区算不出给 null。
    """
    import numpy as np

    from . import sim_ponding

    d = _run_dir(conn, run_id, state)
    s = _summary(d)
    t = np.asarray([f["time_minutes"] for f in _manifest(d)], float)
    out: dict[str, Any] = {"time_minutes": t.tolist(), "area_basis": "cell", "wet_threshold_m": None,
                           "wet_area_m2": None, "max_depth_m": None, "volume_m3": None,
                           "overflow_basis": None, "overflow_cum_m3": None, "ponding_lit": None, "ponding_total": None}
    rel = s["outputs"].get("surface_series")
    ser = _load(d / rel) if rel and (d / rel).is_file() else None
    if ser is not None:
        st = np.asarray(ser["time_minutes"], float)
        j = np.clip(np.searchsorted(st, t), 0, len(st) - 1)
        j = np.where((j > 0) & (np.abs(st[np.maximum(j - 1, 0)] - t) < np.abs(st[j] - t)), j - 1, j)
        hit = np.abs(st[j] - t) <= 0.5
        pick = lambda k: [float(ser[k][i]) if h else None for i, h in zip(j.tolist(), hit.tolist())]
        out.update(area_basis="triangle", wet_threshold_m=ser.get("wet_threshold_m"),
                   wet_area_m2=pick("wet_area_m2"), max_depth_m=pick("max_depth_m"),
                   volume_m3=pick("volume_m3") if "volume_m3" in ser else None)   # 地表积水总量（Σ水深×单元面积）
        ov = ser.get("overflow")
        if ov and ov.get("time_minutes"):
            ot, oc = np.asarray(ov["time_minutes"], float), np.asarray(ov["cum_m3"], float)
            v = np.interp(t, ot, oc, left=0.0)
            out.update(overflow_basis="record", overflow_cum_m3=[
                None if x > ot[-1] + 1e-6 else round(float(y), 1) for x, y in zip(t, v)])
    else:
        rc = sim_delivery.receipt_overflow_series(d, s["outputs"])
        if rc is not None:
            ends, cum = np.asarray(rc[0], float) / 60, np.asarray(rc[1], float)
            k = np.searchsorted(ends, t + 1e-6, side="right") - 1
            out.update(overflow_basis="receipt", overflow_cum_m3=[
                None if x > ends[-1] + 1e-6 else (0.0 if i < 0 else round(float(cum[i]), 1)) for x, i in zip(t, k)])
    try:
        cl = sim_ponding.load(d)["clusters"]
    except sim_ponding.PondingError:
        cl = None
    if cl is not None and all(c.get("onset_min") is not None for c in cl):
        on = np.asarray([c["onset_min"] for c in cl], float)
        out.update(ponding_total=len(cl), ponding_lit=[int((on <= x + 1e-6).sum()) for x in t])
    return out


def max_depth_geojson(conn: psycopg.Connection, run_id: int, state: str | None = None) -> Path:
    """逐格最大水深（原文件直出，~500 KB）。其 cell_id 即各帧紧凑数组里的格子号。"""
    return _output(_run_dir(conn, run_id, state), "maximum_depth")


_ponds_cache: dict[str, Any] = {}


def proposed_ponds() -> dict[str, Any]:
    """拟建调蓄池 FeatureCollection，几何由 EPSG:32640 转成 WGS84 供 Leaflet 落图；属性原样透传。

    文件按修改时间缓存，换文件不用重启。投影转换借 rasterio（已是依赖，不引 pyproj）。
    """
    from rasterio.warp import transform

    p = PROPOSED_PONDS_FILE
    if not p.is_file():
        raise sim.SimulationError(404, NO_PROPOSED_PONDS)
    mtime = p.stat().st_mtime
    if _ponds_cache.get("mtime") == mtime:
        return _ponds_cache["body"]
    src = _load(p)

    def ring(r: list[list[float]]) -> list[list[float]]:
        lon, lat = transform("EPSG:32640", "EPSG:4326", [q[0] for q in r], [q[1] for q in r])
        return [[round(x, 7), round(y, 7)] for x, y in zip(lon, lat)]

    feats = []
    for f in src["features"]:
        g = f["geometry"]
        if g["type"] == "Polygon":
            coords = [ring(r) for r in g["coordinates"]]
        elif g["type"] == "MultiPolygon":
            coords = [[ring(r) for r in poly] for poly in g["coordinates"]]
        else:
            continue
        feats.append({"type": "Feature", "geometry": {"type": g["type"], "coordinates": coords},
                      "properties": f.get("properties") or {}})
    body = {"type": "FeatureCollection", "crs_note": "EPSG:4326（由 32640 转出）", "features": feats}
    _ponds_cache.update(mtime=mtime, body=body)
    return body


def inflow(conn: psycopg.Connection, run_id: int, state: str | None = None) -> dict[str, Any]:
    """模型实际用的总入流过程线（`sim_sww` 从耦合 SWMM 的 [INFLOWS] 累加）；没有这份的交付 404。"""
    return _load(_output(_run_dir(conn, run_id, state), "inflow_hydrograph"))


def frame(conn: psycopg.Connection, run_id: int, index: int, state: str | None = None) -> dict[str, Any]:
    """第 index 帧：`cells` 与 `depth_m` 两列等长，只列水深 ≥ 出图阈值的格子。"""
    d = _run_dir(conn, run_id, state)
    snaps = {f["index"]: f for f in _manifest(d)}
    snap = snaps.get(index)
    if snap is None:
        raise sim.SimulationError(404, NO_FRAME)
    p = (d / snap["path"]).resolve()
    if d not in p.parents or not p.is_file():
        raise sim.SimulationError(404, NO_FRAME)
    feats = _load(p)["features"]
    return {
        "index": index,
        "time_minutes": snap["time_minutes"],
        "cells": [f["properties"]["cell_id"] for f in feats],
        "depth_m": [round(f["properties"]["depth_m"], DEPTH_DECIMALS) for f in feats],
    }


def ponding(conn: psycopg.Connection, run_id: int, state: str | None = None) -> dict[str, Any]:
    """按 docs/analysis_ponding 的算法从成果重算的易积水片区（`sim_ponding.py`，首次现算后缓存）。"""
    from . import sim_ponding

    d = _run_dir(conn, run_id, state)
    try:
        return sim_ponding.load(d)
    except sim_ponding.PondingError as exc:
        raise sim.SimulationError(422, NO_PONDING) from exc


def equipment(conn: psycopg.Connection, run_id: int) -> dict[str, Any]:
    """全市应对设备配置：{fixed, mobile} 各是 generated / inputs / assumptions / scenarios。

    只有总量和数量，没有位置、没有逐台设备；管长、扬程、效率是占位值（页面照实注明）。两个文件缺一个就整体 404。
    """
    row = conn.execute("select output_dir from run where id = %s", [run_id]).fetchone()
    if row is None:
        raise sim.SimulationError(404, sim.NOT_FOUND)
    root = (DATA_ROOT / _posix(row["output_dir"] or "")).resolve()
    paths = {k: root / f for k, f in EQUIPMENT_FILES.items()}
    if DATA_ROOT.resolve() not in root.parents or not all(p.is_file() for p in paths.values()):
        raise sim.SimulationError(404, NO_EQUIPMENT)
    out = {k: {f: _load(p)[f] for f in _EQUIPMENT_KEEP} for k, p in paths.items()}
    # 各态「水深 ≥ 阈值」的积水量（sim_ponding_ge.py 离线算的），有就随设备配置一起给，页面的 L1 / L2 总览用
    ge = root / PONDING_GE_FILE
    if ge.is_file():
        j = _load(ge)
        out["ponding_ge"] = {"threshold_m": j["threshold_m"], "volume_m3": j["volume_m3"]}
    return out


def ponding_ge(conn: psycopg.Connection, run_id: int) -> dict[str, Any]:
    """各态「水深 ≥ 阈值」的积水量（成果目录根上的 `ponding_ge.json`，`sim_ponding_ge.py` 离线算的）：
    页面的「需要排 → 可以排 → 排完剩余」模块用。没有这个文件的任务 404。"""
    row = conn.execute("select output_dir from run where id = %s", [run_id]).fetchone()
    if row is None:
        raise sim.SimulationError(404, sim.NOT_FOUND)
    root = (DATA_ROOT / _posix(row["output_dir"] or "")).resolve()
    p = root / PONDING_GE_FILE
    if DATA_ROOT.resolve() not in root.parents or not p.is_file():
        raise sim.SimulationError(404, NO_PONDING_GE)
    j = _load(p)
    return {"threshold_m": j["threshold_m"], "volume_m3": j["volume_m3"]}


def run_pipes(conn: psycopg.Connection, run_id: int) -> dict[str, Any]:
    """这次推演进模型的管线（成果目录根上的 `pipes.geojson`，engine 写的）：地图「雨水管网」开关画它。没有的任务 404。"""
    row = conn.execute("select output_dir from run where id = %s", [run_id]).fetchone()
    if row is None:
        raise sim.SimulationError(404, sim.NOT_FOUND)
    root = (DATA_ROOT / _posix(row["output_dir"] or "")).resolve()
    p = root / "pipes.geojson"
    if DATA_ROOT.resolve() not in root.parents or not p.is_file():
        raise sim.SimulationError(404, NO_PIPES)
    return _load(p)


def run_outputs(conn: psycopg.Connection, run_id: int) -> dict[str, Any]:
    """这次推演的成果数据都在哪（engine 写在交付根上的 `outputs.json`：相对 NAS 根目录的路径、用的规则版本、怎么接着做 L2 sugg / L3），
    加上同一区域（region）之前所有推演——以后要在某个汇水区上加 L2 sugg / L3，直接从这里找到它做过的 L1 及其成果。没有 outputs.json 的任务 404。"""
    row = conn.execute("select output_dir from run where id = %s", [run_id]).fetchone()
    if row is None:
        raise sim.SimulationError(404, sim.NOT_FOUND)
    root = (DATA_ROOT / _posix(row["output_dir"] or "")).resolve()
    p = root / "outputs.json"
    if DATA_ROOT.resolve() not in root.parents or not p.is_file():
        raise sim.SimulationError(404, NO_OUTPUTS)
    out = dict(_load(p))
    same = []
    for d in sorted(root.parent.iterdir()):
        q = d / "outputs.json"
        if d == root or not q.is_file():
            continue
        try:
            o = _load(q)
        except (OSError, ValueError):
            continue
        if o.get("region") != out.get("region"):
            continue
        rel = d.resolve().relative_to(DATA_ROOT.resolve()).as_posix()
        r = conn.execute("select id from run where output_dir = %s", [rel]).fetchone()
        same.append({"run_id": r["id"] if r else None, "job_id": o.get("job_id"), "tag": o.get("tag"), "rain_mm": o.get("rain_mm"), "dur_h": o.get("dur_h"),
                     "states": o.get("states"), "generated_at": o.get("generated_at"), "paths": o.get("paths")})
    out["same_region_runs"] = sorted(same, key=lambda x: (x["rain_mm"] or 0, x["dur_h"] or 0))
    return out


def run_params(conn: psycopg.Connection, run_id: int) -> dict[str, Any]:
    """本次推演的全部参数（成果目录根上的 `params.json`，engine 的 `tools/run_params.py` 写的）：
    公共规则（带来源 / 出处）+ 本次运行的输入登记 + 规则与实际取值的一致性核对。页面「推演参数」模块用。没有就 404。"""
    row = conn.execute("select output_dir from run where id = %s", [run_id]).fetchone()
    if row is None:
        raise sim.SimulationError(404, sim.NOT_FOUND)
    root = (DATA_ROOT / _posix(row["output_dir"] or "")).resolve()
    p = root / "params.json"
    if DATA_ROOT.resolve() not in root.parents or not p.is_file():
        raise sim.SimulationError(404, NO_PARAMS)
    return _load(p)


def hotspots(conn: psycopg.Connection, run_id: int) -> dict[str, Any]:
    """交付自带的积水点（热点）：坐标、汇水区面、逐态统计（`sim_c1.extract_hotspots` 写在任务根目录）。没有的 404。"""
    row = conn.execute("select output_dir from run where id = %s", [run_id]).fetchone()
    if row is None:
        raise sim.SimulationError(404, sim.NOT_FOUND)
    root = (DATA_ROOT / _posix(row["output_dir"] or "")).resolve()
    p = root / "hotspots.json"
    if DATA_ROOT.resolve() not in root.parents or not p.is_file():
        raise sim.SimulationError(404, NO_HOTSPOTS)
    hot = dict(_load(p))
    from . import sim_c1

    hot["ledger_points"] = sim_c1.region_ledger_points(hot.get("region_catchment") or {})
    if hot.get("schema") == "flood_management.city_hotspots.v1":
        # 全市：台账 ADM 506 个里落在二维计算域外的也列出来（没有仿真结果，outside=true），关注积水点总数对上台账
        have = {sim_c1._adm(p["id"]) for p in hot["points"]}
        hot["points"] = list(hot["points"]) + [{**p, "stats": {}, "outside": True} for p in sim_c1.ledger_adm_points()
                                               if sim_c1._adm(p["id"]) not in have]
    return hot


def residual(conn: psycopg.Connection, run_id: int, state: str | None = None) -> dict[str, Any]:
    """演算末路面残留积水按洼地分块 + 逐块退不掉的原因（`sim_residual.py`）。只有四态交付拆得出。"""
    from . import sim_residual

    d = _run_dir(conn, run_id, state)
    try:
        return sim_residual.load(d)
    except sim_residual.ResidualError as exc:
        raise sim.SimulationError(422, NO_RESIDUAL) from exc


# ---------------------------------------------------------------------------
# 登记交付目录 → run / run_result
# ---------------------------------------------------------------------------

_RPT_TIME = re.compile(r"Analysis (begun|ended) on:\s+(.+?)\s*$")


def _rpt_facts(rpt: Path) -> dict[str, Any]:
    """从 SWMM 报告取起止时刻与溢流节点数。报告一百多 MB，逐行扫，不整读。

    起止时刻是算力机的本地钟，与 input_receipt 的 `created_at`（UTC）对得上，按 UTC 理解。
    """
    out: dict[str, Any] = {"started_at": None, "ended_at": None, "overflow_node_count": None}
    in_flood = False
    dashes = 0
    count = 0
    with rpt.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "Node Flooding Summary" in line:
                in_flood, dashes, count = True, 0, 0
                continue
            if in_flood:
                s = line.strip()
                if s.startswith("---"):
                    dashes += 1
                    continue
                if dashes >= 2:          # 表头两条横线之后是数据行，空行即表尾
                    if not s:
                        out["overflow_node_count"] = count
                        in_flood = False
                    else:
                        count += 1
                continue
            m = _RPT_TIME.search(line)
            if m:
                t = datetime.strptime(m.group(2), "%a %b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)
                out["started_at" if m.group(1) == "begun" else "ended_at"] = t
    if in_flood:
        out["overflow_node_count"] = count
    return out


def _find_delivery(root: Path) -> Path:
    """给的是整份交付目录就往下找 runs/*/delivery_summary.json（只许有一个）。"""
    if (root / "delivery_summary.json").is_file():
        return root
    hits = sorted(root.glob("runs/*/delivery_summary.json"))
    if len(hits) != 1:
        raise SystemExit(f"{root} 下找到 {len(hits)} 份 delivery_summary.json，需恰好一份")
    return hits[0].parent


def import_delivery(conn: psycopg.Connection, path: Path, created_by: str | None = None, *,
                    line_key: str = "line1", batch_id: Any = None, extent: str | None = None,
                    variant_label: str | None = None) -> int:
    """登记一份交付为已完成任务。默认第一道防线；`sim_sww` 登记四态时按态传防线、同批号、片区名与方案名。"""
    if line_key not in sim.LINE_ORDER:
        raise SystemExit(f"未知的防线 {line_key}")
    root = path.resolve()
    m = _states(root)
    if m is not None:
        # 多态：任务挂整份交付（output_dir = 根），参数与报告取 default 态，起止时刻取各态的最早 / 最晚
        run_dir = (root / next(s for s in m["states"] if s["key"] == m["default"])["dir"]).resolve()
        state_dirs = [(root / s["dir"]).resolve() for s in m["states"]]
        out_root = root
    else:
        run_dir = _find_delivery(root)
        state_dirs = [run_dir]
        out_root = run_dir
    if DATA_ROOT.resolve() not in out_root.parents:
        raise SystemExit(f"成果目录须在 {DATA_ROOT} 之下：{out_root}")
    output_dir = out_root.relative_to(DATA_ROOT.resolve()).as_posix()   # Windows 上登记也写正斜杠，Linux 容器才读得到
    s = _summary(run_dir)
    scenario_path = (run_dir / s["outputs"].get("swmm_input", "missing")).parent / "scenario.json"
    sc = json.loads(scenario_path.read_text(encoding="utf-8")) if scenario_path.is_file() else {}
    prof = sc.get("rainfall_profile") or {}
    mc = s["model_configuration"]
    facts = _rpt_facts(_output(run_dir, "swmm_report"))
    for sd in state_dirs:
        if sd == run_dir:
            continue
        f2 = _rpt_facts(_output(sd, "swmm_report"))
        if f2["started_at"] and (facts["started_at"] is None or f2["started_at"] < facts["started_at"]):
            facts["started_at"] = f2["started_at"]
        if f2["ended_at"] and (facts["ended_at"] is None or f2["ended_at"] > facts["ended_at"]):
            facts["ended_at"] = f2["ended_at"]

    depth = float(mc["rainfall_total_mm"])
    dur_h = float(mc["rainfall_duration_minutes"]) / 60
    key = f"{depth:g}mm_{dur_h:g}h"
    preset = conn.execute("select key from storm_preset where key = %s", [key]).fetchone()
    sim_h = float(mc["simulation_duration_minutes"]) / 60
    start = sc.get("start_time")
    f = {
        "rain_source": "design_storm",
        "storm_preset_key": preset["key"] if preset else None,
        "depth_mm": depth,
        "duration_h": dur_h,
        # 这场是客户指定的均匀雨（5 min 等分），不是 Zone B 交替块雨型
        "temporal_pattern": f"{prof.get('provenance', {}).get('temporal_assumption', 'uniform')} "
                            f"({prof.get('profile_id', '—')})",
        "peak_position_pct": None,
        "spatial_distribution": "Uniform citywide" if sc.get("spatial_pattern") == "uniform" else sc.get("spatial_pattern"),
        "extent": extent or ("City-wide" if sc.get("scope") == "citywide" else (sc.get("scope") or "City-wide")),
        "grid_m": float(mc["model_cell_size_m"]),
        "terrain": mc.get("terrain_product"),
        "tide_level_m": sc.get("outfall_level_m"),
        "tide_datum": None,
        "tide_label": (f"Outfall {sc.get('outfall_mode', '—')} {sc['outfall_level_m']:g} m"
                       if sc.get("outfall_level_m") is not None else f"Outfall {sc['outfall_mode']}"
                       if sc.get("outfall_mode") else None),
        "gate_close_above_m": None,
        "model_start_utc": datetime.fromisoformat(start).replace(tzinfo=timezone.utc) if start else None,
        "after_rain_mode": "fixed",
        "after_rain_max_h": round(sim_h - dur_h, 1),
        "baseflow_pct": None, "baseflow_min_dia_mm": None, "baseflow_note": None,
        "defence_line_key": line_key,      # 默认 line1：现状管网 + 泵站，未加任何临时措施
        "queue_all_lines": False,
        "compute_target": s.get("solver"),
    }
    f["rain_label"] = sim._rain_label(f)
    cols = {
        **f,
        "status": "done",
        "progress_pct": 100,
        "started_at": facts["started_at"],
        "ended_at": facts["ended_at"],
        "ended_is_estimate": False,
        "est_run_count": len(state_dirs),
        "est_runtime_h": round((facts["ended_at"] - facts["started_at"]) / timedelta(hours=1), 1)
                         if facts["started_at"] and facts["ended_at"] else None,
        "created_by": sim._user_id(conn, created_by) if created_by else None,
        "output_dir": output_dir,
        "batch_id": batch_id,
        "variant_label": variant_label,
    }
    hit = conn.execute("select id from run where output_dir = %s", [output_dir]).fetchone()
    if hit:
        run_id = hit["id"]
        conn.execute(f"update run set {', '.join(f'{k} = %s' for k in cols)}, updated_at = now() "
                     "where id = %s", [*cols.values(), run_id])
    else:
        run_id = conn.execute(f"insert into run ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
                              "returning id", list(cols.values())).fetchone()["id"]
    # 本道及之前各道的资源全勾（与向导提交同一口径：第 k 道包含前 k 道）
    res = [r["id"] for r in conn.execute(
        "select id from defence_resource where defence_line_key = any(%s)", [list(sim._upto(line_key))]).fetchall()]
    sim._set_resources(conn, run_id, res)
    sim.put_result(conn, run_id, {"overflow_node_count": facts["overflow_node_count"]})
    sim.write_defence_basis(conn, run_id)
    return run_id


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="登记一份交付目录为已完成的仿真任务")
    imp.add_argument("path", type=Path)
    imp.add_argument("--by", help="操作人缩写，如 ZN")
    args = ap.parse_args()
    with sim.connect() as conn:
        run_id = import_delivery(conn, args.path, args.by)
        row = sim.get_run(conn, run_id, "zh")
    print(f"已登记为仿真 #{run_id}：{row['rain_label']}｜{row['extent']} {row['grid_m']:g} m｜"
          f"{row['started_at']} → {row['ended_at']}｜溢出节点 {row['result']['overflow_node_count']}")


if __name__ == "__main__":
    main()


def effect(conn: psycopg.Connection, run_id: int) -> dict[str, Any]:
    """多态任务逐时刻的方案效果：各态比基线少上地表多少水、少了几个溢水点（`sim_effect.py`）。"""
    from . import sim_effect
    from .sim_ponding import PondingError

    row = conn.execute("select output_dir from run where id = %s", [run_id]).fetchone()
    if row is None:
        raise sim.SimulationError(404, sim.NOT_FOUND)
    if not row["output_dir"]:
        raise sim.SimulationError(404, NO_LAYERS)
    p = (DATA_ROOT / _posix(row["output_dir"])).resolve()
    if DATA_ROOT.resolve() not in p.parents or _states(p) is None:
        raise sim.SimulationError(422, NO_EFFECT)
    try:
        return sim_effect.load(p)
    except (sim_effect.EffectError, PondingError) as exc:
        raise sim.SimulationError(422, NO_EFFECT) from exc


def perf(conn: psycopg.Connection, run_id: int, vs: str | None, h: float | None) -> dict[str, Any]:
    """多态任务的效果分析页：一对「既有 vs 方案」的对照数字（`sim_perf.py`）。"""
    from . import sim_effect, sim_perf
    from .sim_ponding import PondingError

    row = conn.execute("select output_dir from run where id = %s", [run_id]).fetchone()
    if row is None:
        raise sim.SimulationError(404, sim.NOT_FOUND)
    if not row["output_dir"]:
        raise sim.SimulationError(404, NO_LAYERS)
    p = (DATA_ROOT / _posix(row["output_dir"])).resolve()
    m = _states(p) if DATA_ROOT.resolve() in p.parents else None
    if m is None:
        raise sim.SimulationError(422, NO_EFFECT)
    if vs is not None and vs not in [s["key"] for s in m["states"][1:]]:
        raise sim.SimulationError(404, NO_STATE)
    try:
        return sim_perf.load(p, vs, h)
    except (sim_perf.PerfError, sim_effect.EffectError, PondingError) as exc:
        raise sim.SimulationError(422, NO_EFFECT) from exc
