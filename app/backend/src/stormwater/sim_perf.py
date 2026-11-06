"""多态任务的效果分析页（Figma「Performance Analysis」）：一对「既有 vs 方案」的全部数字（`/states/perf`）。

页面从「{防线}仿真」总览底部按钮进来，左边地图按基线溢水点逐点分类着色，右边是摘要、建议与两张
「既有 / 方案 / 变化」对照表。本模块只出数，摘要与建议的文案在前端按数字套模板（不是大模型生成）。

## 口径（沿用 `sim_effect` 的「同一时刻」，理由见其模块头）

- 既有 = 第一态（Mussafah 是 L1），方案 = `vs`（不给取最后一态，Mussafah 是 L2+B）。
- 对照表按态分列（`columns`，列名取 states.json 的 `column`：Line 1 / Line 2 Current / Line 2 Planned /
  Line 2 Suggested，缺省用防线），变化一律相对第一态。
- 共同演算窗 = [0, min(**全部态**演算末)]（Mussafah L1 / L2 10 h、其余三态 5 h → 5 h），各列同一个窗。峰值水深、积水面积、峰值地表
  积水量都只在窗内取——窗外的帧另一态没有，不能拿来比。
  有 `surface_series.json`（sww 交付）时按交付口径取：最大积水面积 = 窗内**同一时刻**三角形水深 ≥ 0.02 m 面积最大的那一帧，
  最大水深 = 三角形最大水深，峰值地表积水量 = 逐帧 Σ 水深 × 三角形面积 的最大值——与交付「五态统计值」（截到 5 h）逐位对上
  （2026-09-28 改；原先取 5 min 格快照、面积取「任一帧 ≥ 0.01 m 的格并集」，比任何时刻的积水面积都大一倍）。
  没有这份时序的交付退回格快照，面积仍取同一时刻的最大帧，不取并集。
- 时刻切片（`h`，默认 2 h）的地表积水量、溢水点消除 / 新增直接取 `sim_effect`。
- 溢流井数 / 溢到地表水量 / 调蓄峰值蓄量是 SWMM 报告的全程量。只有各态**最晚一次溢流**都落在共同窗内
  才可比（`comparable`）；Mussafah 三态最晚溢流都在 4.5 h 以前，可比。
- 调蓄容量 = `[STORAGE]` 各单元 TABULAR 面积曲线积到最大深度（FUNCTIONAL 形状按 A = a·d^b + c 积分）。
- 系统储水能力（`system_storage_m3`，2026-09-28 加）= 管网 + 蓄水节点的几何容积。交付带了逐态统计值
  （`analysis/state_stats.csv`，由 `sim_sww` 从「对比_缺口分析/五态统计值.csv」原样拷来）就取其
  「蓄存能力（管网 + 蓄水节点）」一行（含检查井、箱涵 0.30 m 超高，L1 11,126 m³），`system_storage_basis = "delivery"`；
  没有这份就按 .inp 自算：满管管容（CIRCULAR / FORCE_MAIN / RECT）+ 调蓄容量，不含检查井，`basis = "inp"`，偏小。
- 每列附 `new_storage`：比它的 `parent` 态（states.json；不给取第一态）多出来的调蓄单元（Mussafah L2+suggested 比
  L2+planned、L2+B 比 L2，都是 NP1–NP6），逐座给平均面积（满容量 ÷ 最大深度）、容量与演算峰值蓄量；`parent` 写回列上——
  前端「勾选蓄水池」按面积占比在 parent 列与该列之间分摊（估算，见 PerfPanel）。
- 每座新增调蓄单元再附 `devices`（2026-09-28 加）：从该单元出发，沿 parent 态没有的管段走到 parent 态已有的节点为止，
  沿途的进水管 / 闸孔 / 溢流堰 / 泵都算它的配套（Mussafah 每座箱涵 = 4 根进水管 + 4 个闸孔 + 1 道溢流堰 + 1 台排空泵）。
  每件给 .inp 里的能力（断面、长度、堰顶、泵曲线最大流量）与耦合报告里的演算表现（峰值流量、泵运行占比 / 抽走水量），
  `rules` 是作用到这些设备上的 [CONTROLS] 原文（SWMM 语法，协议文本，不翻译）。流量一律折成 L/s。
  设备 `kind`（conduit / orifice / weir / pump）是协议值。
- 每列还附 `new_links`（.inp 里比 parent 态多的管段 / 泵）与 `new_pumps`（交付设施 GIS 里本态新增的泵点，含二维取水、不进 SWMM 的
  地面取水泵；没有设施 GIS 为 `None`）。建议 / 摘要写「本态新增了什么」优先取 `new_pumps`。
- 服务水平（接得住多大的雨）要多场雨才定得出，这里只有一场，照设计稿写「待定」（`None`）。
- 逐点分类（时刻 `h`，按基线溢水点、同一批格子，「仍积水」= 最深格 ≥ 0.15 m）：
  `both` 两态都积水且少不到 `REDUCED_PCT`；`reduced` 两态都积水、方案少 ≥ `REDUCED_PCT`；
  `cleared` 方案消除；`added` 方案新增；`dry` 此刻两态都不积水。类别 key 是协议值。
- 逐点原因：方案态演算末的残留积水块（`sim_residual`）与该点格子相交者里水量最大那块的类别
  （no_inlet / below_rim / not_taking）；不相交记 `None`（演算末此处路面已无残留）。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from . import sim_effect, sim_ponding as sp, sim_residual

REDUCED_PCT = 10.0
CATEGORIES = ("both", "reduced", "cleared", "added", "dry")
_FLOOD_MIN_M3 = sim_residual.RETURNED_MIN_M3


class PerfError(Exception):
    pass


# ---------------------------------------------------------------------------
# SWMM：调蓄容量、溢流井、调蓄峰值
# ---------------------------------------------------------------------------


def _sections(inp: Path, names: set[str]) -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = {n: [] for n in names}
    sec = None
    for line in inp.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("["):
            sec = s.upper()
            continue
        if sec in out and s and not s.startswith(";"):
            out[sec].append(s.split())
    return out


def _curves(rows: list[list[str]]) -> dict[str, tuple[str | None, list[tuple[float, float]]]]:
    """[CURVES] → {名: (类型, 点列)}；类型只写在每条曲线的首行（Storage / Pump4 …）。"""
    curves: dict[str, tuple[str | None, list[tuple[float, float]]]] = {}
    for row in rows:
        name, rest = row[0], row[1:]
        kind = None
        if rest and not re.fullmatch(r"[-+.\deE]+", rest[0]):
            kind, rest = rest[0], rest[1:]
        nums = [float(x) for x in rest]
        k0, pts = curves.get(name, (None, []))
        pts.extend(zip(nums[::2], nums[1::2]))
        curves[name] = (k0 or kind, pts)
    return curves


def _storage_capacity(inp: Path, depth: dict[str, float] | None = None) -> dict[str, float]:
    """各调蓄单元满容量（m³）：面积曲线从 0 积到最大深度。给了 `depth` 就顺手记下各单元最大深度。"""
    t = _sections(inp, {"[STORAGE]", "[CURVES]"})
    curves = {k: v[1] for k, v in _curves(t["[CURVES]"]).items()}
    cap: dict[str, float] = {}
    for row in t["[STORAGE]"]:
        name, dmax, shape = row[0], float(row[2]), row[4].upper()
        if depth is not None:
            depth[name] = dmax
        if shape == "TABULAR" and row[5] in curves:
            pts = sorted(curves[row[5]])
            d = np.array([p[0] for p in pts])
            a = np.array([p[1] for p in pts])
            grid = np.linspace(0.0, dmax, 200)
            cap[name] = float(np.trapezoid(np.interp(grid, d, a), grid))
        elif shape == "FUNCTIONAL":
            ca, cb, cc = (float(x) for x in row[5:8])
            cap[name] = ca * dmax ** (cb + 1) / (cb + 1) + cc * dmax
    return cap


# 流量单位 → L/s（.inp 的 FLOW_UNITS；报告里的流量同单位）
_TO_LPS = {"LPS": 1.0, "CMS": 1000.0, "MLD": 1000 / 86.4, "CFS": 28.3168, "GPM": 0.0630902, "MGD": 43.8126}
_LINK_SECS = {"[CONDUITS]": "conduit", "[ORIFICES]": "orifice", "[WEIRS]": "weir", "[PUMPS]": "pump"}
_KIND_ORDER = {"conduit": 0, "orifice": 1, "weir": 2, "pump": 3}
_NODE_SECS = ("[JUNCTIONS]", "[OUTFALLS]", "[STORAGE]", "[DIVIDERS]")


def _rules(inp: Path) -> list[dict[str, Any]]:
    """[CONTROLS] 逐条拆成 {name, if, then, priority, targets}；条件 / 动作保留 SWMM 原文。"""
    sec, out, cur, part = None, [], None, "if"
    for line in inp.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s.startswith("["):
            sec = s.upper()
            continue
        if sec != "[CONTROLS]" or not s or s.startswith(";"):
            continue
        w = s.split()
        head = w[0].upper()
        if head == "RULE":
            cur, part = {"name": w[1] if len(w) > 1 else "", "if": [], "then": [], "priority": None, "targets": []}, "if"
            out.append(cur)
            continue
        if cur is None:
            continue
        if head == "PRIORITY":
            cur["priority"] = float(w[1]) if len(w) > 1 else None
            continue
        if head in ("THEN", "ELSE"):
            part = "then"
        cur[part].append(" ".join(w))
        if part == "then" and len(w) > 2:
            cur["targets"].append(w[2])       # THEN / AND  PUMP PU_NP1 STATUS = ON
    return out


def _network(inp: Path) -> dict[str, Any]:
    """.inp 的节点、管段（含能力参数）与邻接表；流量折成 L/s。"""
    names = set(_LINK_SECS) | set(_NODE_SECS) | {"[XSECTIONS]", "[CURVES]", "[OPTIONS]"}
    t = _sections(inp, names)
    opts = {r[0].upper(): r[1] for r in t["[OPTIONS]"] if len(r) > 1}
    q = _TO_LPS.get(opts.get("FLOW_UNITS", "LPS").upper(), 1.0)
    xs = {r[0]: r for r in t["[XSECTIONS]"]}
    curves = _curves(t["[CURVES]"])
    nodes = {r[0] for sec in _NODE_SECS for r in t[sec]}
    links: dict[str, dict[str, Any]] = {}
    for sec, kind in _LINK_SECS.items():
        for r in t[sec]:
            if len(r) < 3:
                continue
            L: dict[str, Any] = {"id": r[0], "kind": kind, "from": r[1], "to": r[2]}
            x = xs.get(r[0])
            if x is not None and len(x) > 2:
                L["shape"] = x[1].upper()
                L["size_m"] = float(x[2])
                if len(x) > 3 and float(x[3]) > 0:
                    L["size2_m"] = float(x[3])
            if kind == "conduit" and len(r) > 4:
                L.update(length_m=float(r[3]), roughness=float(r[4]))
            elif kind == "orifice" and len(r) > 5:
                L.update(orifice_type=r[3].upper(), offset_m=float(r[4]), cd=float(r[5]))
            elif kind == "weir" and len(r) > 5:
                L.update(weir_type=r[3].upper(), crest_m=float(r[4]), cd=float(r[5]))
            elif kind == "pump" and len(r) > 3:
                ctype, pts = curves.get(r[3], (None, []))
                L.update(curve=r[3], curve_type=ctype,
                         init_status=r[4].upper() if len(r) > 4 else None,
                         q_max_lps=round(max(p[1] for p in pts) * q, 2) if pts else None)
            links[r[0]] = L
    adj: dict[str, list[str]] = {}
    for L in links.values():
        adj.setdefault(L["from"], []).append(L["id"])
        adj.setdefault(L["to"], []).append(L["id"])
    return {"q": q, "nodes": nodes, "links": links, "adj": adj}


def _hhmm(days: str, hm: str) -> float:
    h, m = hm.split(":")
    return int(days) * 1440 + int(h) * 60 + int(m)


def _rpt_devices(text: str, q: float) -> dict[str, dict[str, Any]]:
    """耦合报告里逐管段 / 逐泵 / 逐调蓄单元的演算表现（流量 L/s、时刻分钟、水量 m³）。"""
    out: dict[str, dict[str, Any]] = {}
    for t in sim_residual._rpt_section(text, "Link Flow Summary"):
        if len(t) >= 5 and re.fullmatch(r"\d\d:\d\d", t[4]):
            out.setdefault(t[0], {}).update(max_flow_lps=round(float(t[2]) * q, 2), max_flow_minutes=_hhmm(t[3], t[4]))
    for t in sim_residual._rpt_section(text, "Pumping Summary"):
        if len(t) >= 10 and re.fullmatch(r"[\d.]+", t[1]):
            out.setdefault(t[0], {}).update(
                utilized_pct=float(t[1]), startups=int(t[2]), avg_flow_lps=round(float(t[4]) * q, 2),
                volume_m3=round(float(t[6]) * 1000, 1),          # 10^6 ltr = 1000 m³
                off_curve_low_pct=float(t[8]), off_curve_high_pct=float(t[9]))
    for t in sim_residual._rpt_section(text, "Node Depth Summary"):
        if len(t) >= 7 and t[1].upper() == "STORAGE":
            out.setdefault(t[0], {}).update(max_depth_m=float(t[3]))
    for t in sim_residual._rpt_section(text, "Node Inflow Summary"):
        if len(t) >= 6 and t[1].upper() == "STORAGE" and re.fullmatch(r"\d\d:\d\d", t[5]):
            out.setdefault(t[0], {}).update(max_inflow_lps=round(float(t[3]) * q, 2), max_inflow_minutes=_hhmm(t[4], t[5]))
    return out


def _unit_devices(net: dict[str, Any], unit: str, old_links: set[str], old_nodes: set[str],
                  storages: set[str]) -> list[str]:
    """从调蓄单元出发，沿 parent 态没有的管段走，走到 parent 已有的节点或别的调蓄单元就停：沿途管段即其配套。"""
    taken: list[str] = []
    seen, stack = {unit}, [unit]
    while stack:
        n = stack.pop()
        for lid in net["adj"].get(n, []):
            if lid in old_links or lid in taken:
                continue
            taken.append(lid)
            L = net["links"][lid]
            m = L["to"] if L["from"] == n else L["from"]
            if m in old_nodes or m in storages or m in seen:
                continue
            seen.add(m)
            stack.append(m)
    return sorted(taken, key=lambda k: (_KIND_ORDER[net["links"][k]["kind"]], k))


def _pipe_volume(inp: Path) -> float:
    """满管管容（m³）：[CONDUITS] 长度 × [XSECTIONS] 断面；认不得的断面形状不计。"""
    t = _sections(inp, {"[CONDUITS]", "[XSECTIONS]"})
    length = {r[0]: float(r[3]) for r in t["[CONDUITS]"] if len(r) > 3}
    vol = 0.0
    for r in t["[XSECTIONS]"]:
        if r[0] not in length or len(r) < 3:
            continue
        shape, g1 = r[1].upper(), float(r[2])
        g2 = float(r[3]) if len(r) > 3 else 0.0
        if shape in ("CIRCULAR", "FORCE_MAIN"):
            a = np.pi * g1 * g1 / 4
        elif shape in ("RECT_CLOSED", "RECT_OPEN"):
            a = g1 * g2
        else:
            continue
        vol += a * length[r[0]]
    return vol


def _delivery_storage(root: Path) -> dict[str, float]:
    """交付统计值里各态的「蓄存能力（管网 + 蓄水节点）」（m³）；没有这份返回空。"""
    import csv

    p = root / "analysis" / "state_stats.csv"
    if not p.is_file():
        return {}
    with p.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("指标") or "").startswith("蓄存能力"):
                out = {}
                for k, v in row.items():
                    try:
                        out[k] = float(v)
                    except (TypeError, ValueError):
                        pass
                return out
    return {}


def _rpt(rpt: Path) -> dict[str, Any]:
    text = rpt.read_text(encoding="utf-8", errors="replace")
    flooded = []
    for t in sim_residual._rpt_section(text, "Node Flooding Summary"):
        if len(t) >= 7 and re.fullmatch(r"\d\d:\d\d", t[4]):
            h, m = t[4].split(":")
            flooded.append((float(t[5]) * 1000, int(t[3]) * 24 + int(h) + int(m) / 60, float(t[1])))
    stored: dict[str, float] = {}
    fill: dict[str, float] = {}
    for t in sim_residual._rpt_section(text, "Storage Volume Summary"):
        if len(t) >= 10 and re.fullmatch(r"\d\d:\d\d", t[8]):
            stored[t[0]] = float(t[5]) * 1000
            fill[t[0]] = float(t[6])
    hit = [f for f in flooded if f[0] > _FLOOD_MIN_M3]
    return {
        "flooded_node_count": len(hit),
        "latest_flood_h": round(max((f[1] for f in hit), default=0.0), 2),
        # 溢流时长：溢流节点 Hours Flooded 的平均（第二道页的 Flood Duration）
        "mean_flood_hours": round(sum(f[2] for f in hit) / len(hit), 3) if hit else None,
        "storage_peak_m3": stored,
        "storage_fill_pct": fill,
    }


# ---------------------------------------------------------------------------
# 共同窗内的峰值
# ---------------------------------------------------------------------------


def _series(d: Path) -> dict[str, Any] | None:
    s = sp._summary(d)
    p = sp._output(d, s, "surface_series")
    return json.loads(p.read_text(encoding="utf-8")) if p is not None else None


def _window(d: Path, end_min: float) -> dict[str, Any]:
    ser = _series(d)
    if ser is not None:
        t = np.asarray(ser["time_minutes"], float)
        k = t <= end_min + 1e-6
        vol = np.asarray(ser["volume_m3"], float)[k]
        area = np.asarray(ser["wet_area_m2"], float)[k]
        i, j = int(np.argmax(vol)), int(np.argmax(area))
        return {
            "peak_surface_volume_m3": round(float(vol[i]), 1),
            "peak_surface_volume_minutes": float(t[k][i]),
            "max_depth_m": round(float(np.asarray(ser["max_depth_m"], float)[k].max()), 3),
            "inundated_area_m2": round(float(area[j]), 1),
            "inundated_area_minutes": float(t[k][j]),
            "basis": "triangle",
        }
    D, times, cell_a = sim_effect._frames(d)
    k = times <= end_min + 1e-6
    W = D[k]
    vol = (np.where(W >= sp.WET_M, W, 0).sum(1) * cell_a)
    wet = (W >= sp.WET_M).sum(1) * cell_a
    i, j = int(np.argmax(vol)), int(np.argmax(wet))
    return {
        "peak_surface_volume_m3": round(float(vol[i]), 1),
        "peak_surface_volume_minutes": float(times[k][i]),
        "max_depth_m": round(float(W.max()), 3),
        "inundated_area_m2": float(wet[j]),
        "inundated_area_minutes": float(times[k][j]),
        "basis": "cell",
    }


# ---------------------------------------------------------------------------
# 主算
# ---------------------------------------------------------------------------


def _state_facts(d: Path) -> dict[str, Any]:
    s = sp._summary(d)
    inp, rpt = sp._output(d, s, "swmm_input"), sp._output(d, s, "swmm_report")
    if inp is None or rpt is None:
        raise PerfError("没有耦合一维模型")
    dmax: dict[str, float] = {}
    cap = _storage_capacity(inp, dmax)
    r = _rpt(rpt)
    net = _network(inp)
    return {
        "_net": net,
        "_rules": _rules(inp),
        "_dev_sim": _rpt_devices(rpt.read_text(encoding="utf-8", errors="replace"), net["q"]),
        "sim_end_minutes": float((s.get("domain") or {}).get("simulation_duration_minutes") or 0),
        "overflow_to_surface_m3": (s.get("coupling") or {}).get("total_swmm_to_anuga_m3"),
        "flooded_node_count": r["flooded_node_count"],
        "latest_flood_h": r["latest_flood_h"],
        "mean_flood_hours": r["mean_flood_hours"],
        "storage_units": sorted(cap),
        "storage_capacity_m3": round(sum(cap.values()), 1),
        "system_storage_m3": round(_pipe_volume(inp) + sum(cap.values()), 1),
        "system_storage_basis": "inp",
        "storage_peak_m3": round(sum(r["storage_peak_m3"].get(k, 0.0) for k in cap), 1),
        "storage_fill_pct": {k: r["storage_fill_pct"].get(k) for k in cap},
        "_cap": cap,
        "_units": {k: {"id": k, "depth_m": dmax[k], "area_m2": round(cap[k] / dmax[k], 1) if dmax[k] > 0 else None,
                       "capacity_m3": round(cap[k], 1), "peak_m3": round(r["storage_peak_m3"].get(k, 0.0), 1),
                       "fill_pct": r["storage_fill_pct"].get(k)} for k in cap},
    }


def _with_devices(f: dict[str, Any], fp: dict[str, Any], u: dict[str, Any]) -> dict[str, Any]:
    """新增调蓄单元 + 其配套设备（相对 parent 态 `fp` 新增的管段）+ 作用到它们的控制规则。"""
    net, sim = f["_net"], f["_dev_sim"]
    ids = _unit_devices(net, u["id"], set(fp["_net"]["links"]), fp["_net"]["nodes"], set(f["_cap"]))
    devices = [{**net["links"][k], "sim": sim.get(k) or None} for k in ids]
    mine = set(ids) | {u["id"]}
    rules = [{k: v for k, v in r.items() if k != "targets"} for r in f["_rules"]
             if mine.intersection(r["targets"])]
    s = sim.get(u["id"]) or {}
    return {**u, "sim_max_depth_m": s.get("max_depth_m"), "sim_max_inflow_lps": s.get("max_inflow_lps"),
            "sim_max_inflow_minutes": s.get("max_inflow_minutes"), "devices": devices, "rules": rules}


def _new_links(f: dict[str, Any], fp: dict[str, Any]) -> list[dict[str, Any]]:
    """该态相对 parent 态多出来的管段（含泵）：调蓄池配套之外的新增设备（如 Suggested 加的转输 / 地面取水泵）也在这里。"""
    net, sim = f["_net"], f["_dev_sim"]
    old = set(fp["_net"]["links"])
    ids = sorted((k for k in net["links"] if k not in old), key=lambda k: (_KIND_ORDER[net["links"][k]["kind"]], k))
    return [{**net["links"][k], "sim": sim.get(k) or None} for k in ids]


def _new_pumps(d: Path) -> list[dict[str, Any]] | None:
    """交付设施 GIS（facilities.json）里本态自己新增的泵点：类型（转输泵 / 地面取水泵 / …）、台数、流量。

    比 .inp 多一类：地面取水泵在二维里取水，不进 SWMM，`new_links` 里看不见。没有这份交付返回 None。
    """
    f = d / "facilities.json"
    if not f.is_file():
        return None
    data = json.loads(f.read_text(encoding="utf-8"))
    out = []
    for ft in data.get("layers", {}).get("pumps", {}).get("features", []):
        pr = ft["properties"]
        if pr.get("所属态") == data.get("state"):
            out.append({"id": pr.get("编号"), "type": pr.get("类型"), "units": int(pr.get("台数") or 1),
                        "q_lps": pr.get("流量_Ls"), "to": pr.get("排到"), "volume_m3": pr.get("本态抽水_m3")})
    return out


def compute(root: Path, vs: str | None, h: float | None) -> dict[str, Any]:
    states, dirs = sim_effect._state_dirs(root)
    base = states[0]
    others = [s for s in states[1:]]
    tgt = next((s for s in others if s["key"] == vs), None) if vs else others[-1]
    if tgt is None:
        raise PerfError(f"没有这一态：{vs}")
    eff = sim_effect.load(root)
    hs = eff["checkpoints_h"]
    h = eff["default_h"] if h is None else float(h)
    if h not in hs:
        raise PerfError(f"时刻档只有 {hs}")
    ci = hs.index(h)
    lane_b = eff["states"][0]
    lane_v = next(x for x in eff["states"] if x["key"] == tgt["key"])
    cb, cv = lane_b["checkpoints"][ci], lane_v["checkpoints"][ci]

    facts = {st["key"]: _state_facts(dirs[st["key"]]) for st in states}
    dstore = _delivery_storage(root)
    if all(st["key"] in dstore for st in states):
        for st in states:
            facts[st["key"]].update(system_storage_m3=round(dstore[st["key"]], 1), system_storage_basis="delivery")
    fb, fv = facts[base["key"]], facts[tgt["key"]]
    common = min(f["sim_end_minutes"] for f in facts.values())
    win = {st["key"]: _window(dirs[st["key"]], common) for st in states}
    comparable = max(f["latest_flood_h"] for f in facts.values()) * 60 <= common
    recs = {st["key"]: sp.load(dirs[st["key"]])["recession"] for st in states}
    rb, rv = recs[base["key"]], recs[tgt["key"]]

    keys = [st["key"] for st in states]
    parent = {st["key"]: st.get("parent") if st.get("parent") in keys else base["key"] for st in states}
    parent[base["key"]] = None
    columns = []
    for st, lane in zip(states, eff["states"]):
        cp = lane["checkpoints"][ci]
        f = facts[st["key"]]
        fp = facts[parent[st["key"]]] if parent[st["key"]] else fb
        columns.append({
            "key": st["key"], "label": st["label"], "column": st.get("column") or st["defence_line_key"],
            "same_as": st.get("same_as"), "parent": parent[st["key"]],
            "at_h": {"reached": bool(cp.get("reached") and cb.get("reached")),
                     "surface_volume_m3": cp.get("surface_volume_m3"),
                     "active": int(sum(cp["active"])) if cp.get("reached") else None},
            "window": win[st["key"]],
            "recession": recs[st["key"]],
            "network": {k: v for k, v in f.items() if not k.startswith("_")},
            "new_storage": [_with_devices(f, fp, u) for k, u in f["_units"].items() if k not in fp["_cap"]],
            "new_links": _new_links(f, fp) if parent[st["key"]] else [],
            "new_pumps": _new_pumps(dirs[st["key"]]) if parent[st["key"]] else None,
        })
    try:
        res = sim_residual.load(dirs[tgt["key"]])
    except sim_residual.ResidualError:
        res = None

    # 逐点分类 + 原因
    base_pts = sp.load(dirs[base["key"]])["clusters"]
    points = []
    reached = cb.get("reached") and cv.get("reached")
    for j, (p, c) in enumerate(zip(eff["points"], base_pts)):
        row = {**p, "area_m2": c["area_m2"], "mechanism_key": c.get("mechanism_key")}
        if reached:
            a0, a1 = cb["active"][j], cv["active"][j]
            v0, v1 = cb["volume_m3"][j], cv["volume_m3"][j]
            less = round(100 * (v0 - v1) / v0, 1) if v0 > 0 else None
            if a0 and not a1:
                cat = "cleared"
            elif a1 and not a0:
                cat = "added"
            elif not a0:
                cat = "dry"
            else:
                cat = "reduced" if less is not None and less >= REDUCED_PCT else "both"
            row.update(category=cat, volume_base_m3=v0, volume_vs_m3=v1, volume_less_pct=less,
                       depth_base_m=cb["depth_m"][j], depth_vs_m=cv["depth_m"][j])
        else:
            row.update(category=None)
        reason = None
        if res is not None:
            cells = set(c["cells"])
            hits = [q for q in res["ponds"] if cells.intersection(q["cells"])]
            if hits:
                q = max(hits, key=lambda x: x["volume_m3"])
                reason = {"class": q["class"], "volume_m3": q["volume_m3"], "rank": q["rank"],
                          "nearest_inlet": q.get("nearest_inlet"), "nearest_storage": q.get("nearest_storage")}
        row["residual"] = reason
        points.append(row)

    new_units = [k for k in fv["storage_units"] if k not in fb["_cap"]]
    for f in facts.values():
        for k in [k for k in f if k.startswith("_")]:
            f.pop(k)
    return {
        "base": {"key": base["key"], "label": base["label"], "defence_line_key": base["defence_line_key"]},
        "vs": {"key": tgt["key"], "label": tgt["label"], "defence_line_key": tgt["defence_line_key"],
               "same_as": tgt.get("same_as")},
        "options": [{"key": s["key"], "label": s["label"], "same_as": s.get("same_as")} for s in others],
        "checkpoints_h": hs,
        "h": h,
        "active_depth_m": eff["active_depth_m"],
        "reduced_pct": REDUCED_PCT,
        "common_end_minutes": common,
        "at_h": {
            "reached": bool(reached),
            "surface_volume_base_m3": cb.get("surface_volume_m3"),
            "surface_volume_vs_m3": cv.get("surface_volume_m3"),
            "volume_less_m3": cv.get("volume_less_m3"),
            "volume_less_pct": cv.get("volume_less_pct"),
            "active_base": cv.get("base_active_count"),
            "active_vs": cv.get("active_count"),
            "cleared": len(cv.get("cleared") or []),
            "added": len(cv.get("added") or []),
        },
        "window": {"base": win[base["key"]], "vs": win[tgt["key"]]},
        "recession": {"base": rb, "vs": rv},
        "network": {"base": fb, "vs": fv, "comparable": comparable,
                    "new_storage_units": new_units,
                    "new_storage_max_fill_pct": max((fv["storage_fill_pct"].get(k) or 0 for k in new_units), default=None)},
        "residual": None if res is None else {
            "end_minutes": res["summary"]["end_minutes"],
            "surface_volume_m3": res["summary"]["surface_volume_m3"],
            "pond_count": res["summary"]["pond_count"],
            "by_class": res["summary"]["by_class"],
        },
        "level_of_service": None,
        "columns": columns,
        "category_counts": {k: sum(1 for p in points if p["category"] == k) for k in CATEGORIES},
        "points": points,
    }


def single(root: Path) -> dict[str, Any]:
    """单态任务（engine 的 L1 单态推演）：没有可比的方案，只给总览要的本态数（页面 `LevelPanel` 读 columns / common_end_minutes / network.comparable）。"""
    man = json.loads((root / "states.json").read_text(encoding="utf-8"))
    st = man["states"][0]
    d = (root / st["dir"]).resolve()
    f = _state_facts(d)
    dstore = _delivery_storage(root)
    if st["key"] in dstore:
        f.update(system_storage_m3=round(dstore[st["key"]], 1), system_storage_basis="delivery")
    end = f["sim_end_minutes"]
    for k in [k for k in f if k.startswith("_")]:
        f.pop(k)
    return {
        "single": True,
        "base": {"key": st["key"], "label": st["label"], "defence_line_key": st["defence_line_key"]},
        "options": [], "common_end_minutes": end,
        "window": {"base": _window(d, end)},
        "network": {"base": f, "comparable": True},
        "columns": [{"key": st["key"], "label": st["label"], "column": st.get("column") or st["defence_line_key"], "same_as": st.get("same_as"),
                     "parent": None, "window": _window(d, end), "network": f}],
        "points": [], "category_counts": {},
    }


@lru_cache(maxsize=16)
def _cached(root: str, vs: str | None, h: float | None, stamp: tuple) -> str:
    return json.dumps(compute(Path(root), vs, h), ensure_ascii=False)


def load(root: Path, vs: str | None = None, h: float | None = None) -> dict[str, Any]:
    """进程内按「目录 + 各态摘要 mtime + vs + h」缓存；每次返回独立副本。"""
    root = root.resolve()
    man = root / "states.json"
    if man.is_file() and len(json.loads(man.read_text(encoding="utf-8"))["states"]) == 1:
        return single(root)
    _, dirs = sim_effect._state_dirs(root)
    stamp = tuple(sorted((k, (d / "delivery_summary.json").stat().st_mtime) for k, d in dirs.items()))
    return json.loads(_cached(str(root), vs, h, stamp))
