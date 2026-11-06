"""从一份耦合演算成果里**重算**易积水片区——`docs/analysis_ponding/算法与成果说明.docx` 的七步。

`model_ponding.py` 只读 Al Bateen 那次离线分析吐出的 GeoJSON、一个数都不重算；本模块把同一套
算法做成可以对任意一份交付目录跑的代码，挂到 Run history 的任务上（`/layers/ponding`）。

## 七步（与文档一一对应）

① 时序重建：正则扫 `temporal_snapshots/*.geojson` 的 (cell_id, depth_m)，快照只记 ≥ 0.01 m 的格子；
② 逐格指标：最大水深、峰现时刻、0.10 / 0.15 / 0.30 m 分级历时、6 / 12 / 24 h 与末帧残水、最后有水时刻；
③ Priority-Flood 填洼（出口 = 网格四周 + 非陆地格），洼深 sink = filled − dem；
④ 洼地库容：sink > 0.01 m 八连通分区，库容 = Σ sink × 格面积；
⑤ 管网归因：逐格 SWMM 注入体积 V_swmm 对比本地降雨 V_rain = 格面积 × 总雨量；
⑥ 聚类：最大水深 ≥ 0.30 m **或** ≥ 0.15 m 历时 ≥ 3 h 的有效格八连通成片区，代表点取最深格；
⑦ 成因规则树（深坑 → 管网溢流主导 → 演算末未退干 → 地表汇水洼地）+ 五因子积水指数。

阈值、权重、上限全部照抄文档。有两条判据文档写的是「格数」与「分钟」，这里按**物理量**改写，
使 20 m / 99 h 的原场景结果不变、换网格换时长时仍有意义：
「≤ 40 格」→ 片区面积 ≤ 40 × 400 m²；「退水时刻 ≥ 5,900 min」→ ≥ 演算末 − 40 min。

## 输入缺了怎么办——降级，但每一处都写进 `deviations`

文档的输入是 `terrain_grid.npz`（高程 + 陆域 / 永久水体掩膜）、`swmm_exchange.npz`（逐格注入速率）、
最大水深图层的 `maximum_speed_m_s`。三样都在时结果与文档逐项一致（`test_sim_ponding.py` 用
Al Bateen 原始成果对 `docs/analysis_ponding/ponding_hotspots.csv` 锁死）。缺了的按下表替代，
**替代了就在结果里记一个偏离 key**，页面原样列出，不许把降级结果当成文档口径：

| 缺什么 | 替代 | 偏离 key |
|---|---|---|
| terrain_grid 的高程 | `.sww` 的 `elevation_c`（每格 4 个三角形取平均；`sim_sww` 预先写成带 `source="sww"` 的 npz 也算） | `terrain_from_sww` |
| terrain_grid 的掩膜 | 高程恰为 0 的格子当水体（算力侧对海域的填值），其余当陆地；v2 交付的网格建前已套陆域掩膜，无三角形的格即水体，不记偏离 | `land_mask_inferred` |
| swmm_exchange | SWMM 报告 Node Flooding Summary 的全程溢流量 × 耦合回执的节点→格绑定 | `swmm_from_rpt` |
| 流速 | 积水指数去掉滞流度一项，其余四项权重按比例放大 | `no_speed` |

另有两条不是缺文件、而是**演算本身让判据失效**，同样记下：`no_surface_rain`（地表不直接降雨，
全部来水都是管网溢流，「管网溢流主导」的对照失去意义）、`coarse_grid`（网格 > 20 m，一格就超过
深坑判据的面积上限，深坑规则不可能触发）、`no_recession`（末帧地表水量仍 ≥ 峰值的 90%，
退水类指标与「未退干」判据失去区分度）、`partial_run`（交付缺末尾窗口，演算没跑到目标终点，
「演算末」取的是已完成的最后一帧）。

每个片区另给 `onset_min`（成立时刻）：片区内最早一格**累计**满足⑥判据的时刻，供结果地图播放时
逐帧点亮溢水点。判据只增不减，末帧点亮的集合就是全程结果；片区、成因、名次不随帧重算。

结果按「成果目录 + 摘要 mtime + 算法版本」缓存成 JSON，放 `data/processed/sim_ponding/`
（成果目录只读不写）。首次请求现算，全市 250 m 那份约十几秒。

用法（预热缓存 / 看结果）：

    PYTHONPATH=src .venv/bin/python -m stormwater.sim_ponding 40
    PYTHONPATH=src .venv/bin/python -m stormwater.sim_ponding data/al-bateen-library-rp100-20m-20260920T122942Z-96h-priority
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from .model_ponding import MECHANISM_KEYS
from .sim_delivery import is_partial, load_summary

BACKEND_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = BACKEND_DIR / "data" / "processed" / "sim_ponding"

#: 算法有改动就加一，旧缓存自动作废
ALGO_VERSION = 3

# ---- 文档参数（§3.2 / §3.4 / §3.6 / §3.7 / §3.8），改之前先改文档 ----
WET_M = 0.01
DUR_THRESHOLDS = (0.10, 0.15, 0.30)
SELECT_MAX_DEPTH_M = 0.30
SELECT_DUR_DEPTH_M = 0.15
SELECT_DUR_H = 3.0
SINK_MIN_M = 0.01
DEEP_SINK_M = 1.5
DEEP_SINK_MAX_AREA_M2 = 40 * 400.0         # 文档：≤ 40 个 20 m 格
UNDRAINED_MARGIN_MIN = 40.0                # 文档：5,900 / 5,940 min
REF_CELL_M = 20.0
#: 五因子：(权重, 上限)。stg = 3 / (1 + 3 × 平均流速)，上限 3
SCORE_WEIGHTS = {"dep": 0.30, "dur": 0.30, "ext": 0.15, "snk": 0.15, "stg": 0.10}
NO_RECESSION_RATIO = 0.9

MECH = {  # key → 文档里的中文串（与 model_ponding / i18n.EN 同一套）
    v: k for k, v in MECHANISM_KEYS.items()
}

_NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_RX_ID = re.compile(rb'"cell_id":\s*(\d+)')
_RX_D = re.compile(rb'"depth_m":\s*([-\d.eE+]+)')


class PondingError(Exception):
    pass


# ---------------------------------------------------------------------------
# 输入
# ---------------------------------------------------------------------------


def _summary(d: Path) -> dict[str, Any]:
    return load_summary(d)   # v1 / v2 摘要归一（v2 的 .sww 路径是算力机上的绝对路径）


def _output(d: Path, s: dict, key: str) -> Path | None:
    rel = (s.get("outputs") or {}).get(key)
    p = (d / rel).resolve() if rel else None
    return p if p is not None and p.is_file() else None


def _grid(d: Path, s: dict) -> dict[str, Any]:
    """网格几何：列数、行数、格边长、左上角 (x0, y_top)。格号 = 行 × 列数 + 列，行号自北向南。"""
    tg = d / "terrain_grid.npz"
    if tg.is_file():
        t = np.load(tg, allow_pickle=True)
        nr, nc = t["values"].shape
        tr = t["transform"]
        return {"nr": nr, "nc": nc, "cs": float(tr[0]), "x0": float(tr[2]), "ytop": float(tr[5])}
    dom = s.get("domain") or {}
    x0, y0, x1, y1 = dom["bounds_epsg32640"]
    cs = float(dom["cell_size_m"])
    return {"nr": round((y1 - y0) / cs), "nc": round((x1 - x0) / cs), "cs": cs, "x0": x0, "ytop": y1}


def _depth_matrix(d: Path, s: dict, n: int) -> tuple[np.ndarray, np.ndarray]:
    man_p = _output(d, s, "timeline_manifest")
    if man_p is None:
        raise PondingError("成果里没有时序快照")
    snaps = json.loads(man_p.read_text(encoding="utf-8"))["snapshots"]
    times = np.array([f["time_minutes"] for f in snaps], float)
    D = np.zeros((len(snaps), n), np.float32)
    for i, f in enumerate(snaps):
        b = (man_p.parent.parent / f["path"]).read_bytes()
        ids = np.array(_RX_ID.findall(b), np.int64)
        ds = np.array(_RX_D.findall(b), np.float32)
        if len(ids) != len(ds) or ("feature_count" in f and len(ids) != f["feature_count"]):
            raise PondingError(f"快照 {f['path']} 解析条数对不上")
        D[i, ids] = ds
    return D, times


def _terrain(d: Path, s: dict, g: dict, dev: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回 (dem, 有效格, 出口格)。有效格 = 参与聚类的格；出口格 = 填洼时的出流种子（非陆地）。"""
    tg = d / "terrain_grid.npz"
    if tg.is_file():
        t = np.load(tg, allow_pickle=True)
        dem = t["values"].astype(float)
        if "source" in t.files and str(t["source"]) == "sww":
            dev.append("terrain_from_sww")      # sim_sww 从 .sww 逐格平均写的，不是算力侧的 terrain_grid
        water = t["permanent_water_mask"]
        land = t["land_mask"] & ~water
        valid = (t["district_mask"] & ~water) if "district_mask" in t.files else land
        return dem, valid, ~land
    sww = _output(d, s, "native_sww")
    if sww is None:
        raise PondingError("成果里既没有 terrain_grid.npz 也没有 .sww，拿不到地形")
    from scipy.io import netcdf_file   # 只有降级路径要它

    f = netcdf_file(sww, "r", mmap=False)
    try:
        x, y = f.variables["x"][:].copy(), f.variables["y"][:].copy()
        vol = f.variables["volumes"][:].copy()
        ec = f.variables["elevation_c"][:].astype(float)
    finally:
        f.close()
    nr, nc, cs = g["nr"], g["nc"], g["cs"]
    col = np.floor((x[vol].mean(1) - g["x0"]) / cs).astype(int)
    row = np.floor((g["ytop"] - y[vol].mean(1)) / cs).astype(int)
    ok = (row >= 0) & (row < nr) & (col >= 0) & (col < nc)
    idx = row[ok] * nc + col[ok]
    n = nr * nc
    cnt = np.bincount(idx, minlength=n)
    dem = (np.bincount(idx, ec[ok], n) / np.maximum(cnt, 1)).reshape(nr, nc)
    dev.append("terrain_from_sww")
    if (s.get("land_water_treatment") or {}).get("applied"):
        # v2：陆域掩膜在建 ANUGA 网格之前就套上了，网格只铺陆地——没有三角形的格就是水体，不用猜
        water = (cnt == 0).reshape(nr, nc)
    else:
        zero = np.bincount(idx, (ec[ok] == 0.0).astype(float), n)
        water = ((cnt == 0) | (zero == cnt)).reshape(nr, nc)
        dev.append("land_mask_inferred")
    return dem, ~water, water


def _swmm_volume(d: Path, s: dict, g: dict, dev: list[str]) -> np.ndarray:
    """逐格 SWMM 注入体积（m³），摊平成长度 nr × nc 的数组。"""
    n = g["nr"] * g["nc"]
    ex = d / "swmm_exchange.npz"
    if ex.is_file():
        e = np.load(ex, allow_pickle=True)
        rate = e["swmm_to_anuga_cell_rates_mps"].astype(float)
        step = float(e["report_step_seconds"])
        return rate.sum(0)[:n] * step * g["cs"] ** 2
    rpt = _output(d, s, "swmm_report")
    rec = _output(d, s, "coupling_receipt")
    dev.append("swmm_from_rpt")
    v = np.zeros(n)
    if rpt is None or rec is None:
        return v
    # 回执一百多 MB，只要 (节点, 格) 两个字段，字节流正则扫，比 json.load 快一个量级
    node2cell = {
        m.group(1).decode(): int(m.group(2))
        for m in re.finditer(rb'"swmm_node_id":\s*"([^"]+)",\s*"swmm_node_index":\s*\d+,\s*"anuga_cell_index":\s*(\d+)',
                             rec.read_bytes())
    }
    in_flood, dashes = False, 0
    with rpt.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "Node Flooding Summary" in line:
                in_flood, dashes = True, 0
                continue
            if not in_flood:
                continue
            t = line.split()
            if line.strip().startswith("---"):
                dashes += 1
                continue
            if dashes < 2:
                continue
            if not t:
                break
            cell = node2cell.get(t[0])
            if cell is not None and cell < n:
                v[cell] += float(t[-2]) * 1000.0   # 10^6 ltr → m³
    return v


def _speed(d: Path, s: dict, n: int, dev: list[str]) -> np.ndarray | None:
    p = _output(d, s, "maximum_depth")
    if p is None:
        dev.append("no_speed")
        return None
    sp = np.full(n, np.nan)
    has = False
    for f in json.loads(p.read_text(encoding="utf-8"))["features"]:
        pr = f["properties"]
        if pr.get("maximum_speed_m_s") is not None:
            sp[pr["cell_id"]] = pr["maximum_speed_m_s"]
            has = True
    if not has:
        dev.append("no_speed")
        return None
    return sp


def _rain_mm(s: dict) -> float:
    mc = s.get("model_configuration") or {}
    fo = s.get("forcing") or {}
    for v in (mc.get("rainfall_total_mm"), fo.get("generated_total_depth_mm"), fo.get("published_total_depth_mm")):
        if v is not None:
            return float(v)
    raise PondingError("交付摘要里找不到总雨量")


def _surface_rain(d: Path, s: dict) -> bool:
    """地表是否直接受雨。单向溢流型（摘要写明只交换节点溢流、回执里降雨入流为 0）返回 False。"""
    ex = d / "swmm_exchange.npz"
    if ex.is_file():
        e = np.load(ex, allow_pickle=True)
        if "surface_rainfall_fraction_by_cell" in e.files:
            return bool(np.any(e["surface_rainfall_fraction_by_cell"] > 0))
    mc = s.get("model_configuration") or {}
    if mc.get("surface_rainfall_inflow_m3") is not None:    # v2：以回执逐窗降雨入流为准
        return mc["surface_rainfall_inflow_m3"] > 0
    return "overflow only" not in str(mc.get("exchange_quantity", "")).lower()


# ---------------------------------------------------------------------------
# 算法
# ---------------------------------------------------------------------------


def _fill(dem: np.ndarray, outlet: np.ndarray) -> np.ndarray:
    """Priority-Flood（Wang & Liu 2006）：种子 = 出口格 ∪ 网格四周，按高程升序外扩。"""
    nr, nc = dem.shape
    filled = dem.copy()
    closed = np.zeros_like(outlet)
    heap: list[tuple[float, int, int]] = []
    border = np.zeros_like(outlet)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    for r, c in zip(*np.where(outlet | border)):
        heapq.heappush(heap, (float(dem[r, c]), int(r), int(c)))
        closed[r, c] = True
    while heap:
        e, r, c = heapq.heappop(heap)
        for dr, dc in _NB:
            p, q = r + dr, c + dc
            if 0 <= p < nr and 0 <= q < nc and not closed[p, q]:
                closed[p, q] = True
                filled[p, q] = max(float(dem[p, q]), e)
                heapq.heappush(heap, (float(filled[p, q]), p, q))
    return filled


def _label8(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """八连通标号，0 = 背景，片区号从 1 起（与 scipy.ndimage.label 一致，按行优先扫描顺序编号）。"""
    nr, nc = mask.shape
    lab = np.zeros(mask.shape, np.int32)
    k = 0
    for r, c in zip(*np.where(mask)):
        if lab[r, c]:
            continue
        k += 1
        lab[r, c] = k
        stack = [(int(r), int(c))]
        while stack:
            a, b = stack.pop()
            for dr, dc in _NB:
                p, q = a + dr, b + dc
                if 0 <= p < nr and 0 <= q < nc and mask[p, q] and not lab[p, q]:
                    lab[p, q] = k
                    stack.append((p, q))
    return lab, k


def _cell_lonlat(d: Path, s: dict) -> dict[int, tuple[float, float]]:
    """格号 → 格心经纬度，取最大水深图层方格四角的平均（免得引 pyproj）。"""
    p = _output(d, s, "maximum_depth")
    out: dict[int, tuple[float, float]] = {}
    if p is None:
        return out
    for f in json.loads(p.read_text(encoding="utf-8"))["features"]:
        ring = f["geometry"]["coordinates"][0][:4]
        out[f["properties"]["cell_id"]] = (sum(v[0] for v in ring) / 4, sum(v[1] for v in ring) / 4)
    return out


def compute(run_dir: Path) -> dict[str, Any]:
    d = run_dir.resolve()
    s = _summary(d)
    dev: list[str] = []
    g = _grid(d, s)
    nr, nc, cs = g["nr"], g["nc"], g["cs"]
    n, cell_a = nr * nc, cs * cs

    # ① ②
    D, times = _depth_matrix(d, s, n)
    step_h = float(np.median(np.diff(times))) / 60 if len(times) > 1 else 0.0
    maxd = D.max(0)
    peak_t = times[D.argmax(0)]
    dur = {th: (D >= th).sum(0) * step_h for th in DUR_THRESHOLDS}
    wet = D >= WET_M
    ever = wet.any(0)
    last_wet = np.where(ever, times[len(times) - 1 - np.argmax(wet[::-1], 0)], np.nan)

    def at(tmin: float) -> np.ndarray | None:
        return D[int(np.argmin(np.abs(times - tmin)))] if times[-1] >= tmin else None

    residual = {h: at(h * 60) for h in (6, 12, 24)}
    end_min = float(times[-1])

    # ③ ④
    dem, valid, outlet = _terrain(d, s, g, dev)
    filled = _fill(dem, outlet)
    sink = np.where(outlet, 0.0, np.maximum(filled - dem, 0.0))
    blab, nb = _label8(sink > SINK_MIN_M)
    b_flat = blab.ravel()
    cap = np.bincount(b_flat, (sink * cell_a).ravel(), nb + 1)
    b_area = np.bincount(b_flat, minlength=nb + 1) * cell_a

    # ⑤
    v_swmm = _swmm_volume(d, s, g, dev)
    rain_m = _rain_mm(s) / 1000
    speed = _speed(d, s, n, dev)
    if not _surface_rain(d, s):
        dev.append("no_surface_rain")
    if cs > REF_CELL_M:
        dev.append("coarse_grid")
    if is_partial(s):
        dev.append("partial_run")
    vol_t = (D.astype(float) * cell_a).sum(1)
    if vol_t.max() > 0 and vol_t[-1] >= NO_RECESSION_RATIO * vol_t.max():
        dev.append("no_recession")

    # ⑥
    v_flat = valid.ravel()
    sel = v_flat & ((maxd >= SELECT_MAX_DEPTH_M) | (dur[SELECT_DUR_DEPTH_M] >= SELECT_DUR_H))
    lab, k = _label8(sel.reshape(nr, nc))
    l_flat = lab.ravel()
    # 逐格成立时刻：从哪一帧起**累计**满足判据（截至该帧最大水深 ≥ 0.30 m，或 ≥ 0.15 m 已累计 ≥ 3 h）。
    # 两条都只增不减，末帧必然与上面全程判据一致；播放时片区按它点亮，片区本身不随帧重算。
    s_idx = np.flatnonzero(sel)
    Ds = D[:, s_idx]
    hit = np.maximum.accumulate(Ds >= SELECT_MAX_DEPTH_M, 0) | \
        (np.cumsum(Ds >= SELECT_DUR_DEPTH_M, 0) * step_h >= SELECT_DUR_H)
    onset = np.full(n, np.nan)
    onset[s_idx] = times[hit.argmax(0)]

    # ⑦
    lonlat = _cell_lonlat(d, s)
    sink_f, dem_f = sink.ravel(), dem.ravel()
    weights = dict(SCORE_WEIGHTS)
    if speed is None:
        weights.pop("stg")
    wsum = sum(weights.values())
    order = np.argsort(l_flat, kind="stable")
    bounds = np.searchsorted(l_flat[order], np.arange(1, k + 2))
    clusters = []
    for c in range(1, k + 1):
        cells = order[bounds[c - 1]:bounds[c]]
        rep = int(cells[np.argmax(maxd[cells])])
        a = len(cells) * cell_a
        sk_cell = int(cells[np.argmax(sink_f[cells])])
        sk = float(sink_f[sk_cell])
        basin = int(b_flat[sk_cell])
        vs = float(v_swmm[cells].sum())
        vr = a * rain_m
        drain = float(np.nanmax(last_wet[cells]))
        if sk >= DEEP_SINK_M and a <= DEEP_SINK_MAX_AREA_M2:
            key = "deep_sink"
        elif vs > vr:
            key = "network_overflow"
        elif drain >= end_min - UNDRAINED_MARGIN_MIN:
            key = "coastal_lowland"
        else:
            key = "surface_depression"
        a30 = float((maxd[cells] >= SELECT_MAX_DEPTH_M).sum() * cell_a)
        mean_sp = round(float(np.nanmean(speed[cells])), 2) if speed is not None else None
        # 文档的评分吃的是**出表精度**的值（水深 3 位、洼深 / 流速 2 位）——按原值算会差 0.1 分、
        # 相邻名次对调。照它来，Al Bateen 107 个片区的分数与名次才逐个对得上。
        f = {
            "dep": min(round(float(maxd[rep]), 3) / 1.0, 3),
            "dur": min(float(dur[0.15][cells].max()) / 6.0, 3),
            "ext": min(a30 / 4000.0, 3),
            "snk": min(round(sk, 2) / 1.0, 2),
        }
        if mean_sp is not None:
            f["stg"] = 3 / (1 + 3 * mean_sp)
        score = 100 * sum(weights[i] * f[i] for i in weights) / wsum / 3
        ll = lonlat.get(rep)
        r_row, r_col = divmod(rep, nc)
        clusters.append({
            "score": round(score, 1),
            "mechanism_key": key,
            "mechanism": MECH[key],
            "lat": round(ll[1], 6) if ll else None,
            "lon": round(ll[0], 6) if ll else None,
            "x": round(g["x0"] + (r_col + 0.5) * cs, 1),      # 代表格格心，EPSG:32640
            "y": round(g["ytop"] - (r_row + 0.5) * cs, 1),
            "cell_id": rep,
            "cell_count": len(cells),
            "area_m2": a,
            "max_depth_m": round(float(maxd[rep]), 3),
            "mean_max_depth_m": round(float(maxd[cells].mean()), 3),
            "area_ge_0_30m_m2": a30,
            "peak_time_min": float(peak_t[rep]),
            "onset_min": float(np.nanmin(onset[cells])),
            "dur_ge_0_10m_h": round(float(dur[0.10][cells].max()), 2),
            "dur_ge_0_15m_h": round(float(dur[0.15][cells].max()), 2),
            "dur_ge_0_30m_h": round(float(dur[0.30][cells].max()), 2),
            **{f"depth_{h}h_m": (round(float(r[rep]), 3) if r is not None else None) for h, r in residual.items()},
            "depth_end_m": round(float(D[-1, rep]), 3),
            "drain_time_h": round(drain / 60, 2),
            "mean_speed_ms": mean_sp,
            "elev_min_m": round(float(dem_f[cells].min()), 2),
            "sink_depth_m": round(sk, 2),
            "basin_capacity_m3": round(float(cap[basin]), 1) if basin else 0.0,
            "basin_area_m2": float(b_area[basin]) if basin else 0.0,
            "swmm_inflow_m3": round(vs, 1),
            "rain_on_cluster_m3": round(vr, 1),
            "cells": [int(x) for x in cells],
        })
    clusters.sort(key=lambda r: -r["score"])
    for i, r in enumerate(clusters, 1):
        r["rank"] = i

    return {
        "algo_version": ALGO_VERSION,
        "run_ref": s.get("run_id"),
        "claim_boundary": s.get("claim_boundary"),
        "inputs": {
            "cell_size_m": cs,
            "grid": [nr, nc],
            "frames": len(times),
            "frame_step_min": step_h * 60,
            "sim_end_min": end_min,
            "rainfall_mm": rain_m * 1000,
            "valid_cells": int(v_flat.sum()),
            "depressions": nb,
        },
        "deviations": dev,
        "criteria": {
            "select_max_depth_m": SELECT_MAX_DEPTH_M,
            "select_dur_depth_m": SELECT_DUR_DEPTH_M,
            "select_dur_h": SELECT_DUR_H,
        },
        "summary": {
            "selected_cells": int(sel.sum()),
            "cluster_count": k,
            "area_m2": float(sel.sum() * cell_a),
            "by_mechanism": dict(Counter(r["mechanism_key"] for r in clusters)),
        },
        "clusters": clusters,
    }


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------


def _cache_path(run_dir: Path) -> Path:
    d = run_dir.resolve()
    key = f"{d}|{(d / 'delivery_summary.json').stat().st_mtime}|{ALGO_VERSION}"
    return CACHE_DIR / f"{d.name}-{hashlib.sha1(key.encode()).hexdigest()[:12]}.json"


def load(run_dir: Path) -> dict[str, Any]:
    """读缓存，没有就现算并落盘；出门前给每个片区按最近 POI 起名（不进缓存，换 POI 表不用重算）。"""
    p = _cache_path(run_dir)
    if p.is_file():
        out = json.loads(p.read_text(encoding="utf-8"))
    else:
        out = compute(run_dir)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # 临时文件名带唯一后缀：详情页同时请求 ponding 图层和 states/perf，两边都发现没有缓存、都现算，
        # 共用一个 .tmp 会让后写完的那个 replace 时找不到文件（500）
        tmp = p.with_name(f"{p.stem}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    return recession(name_clusters(out))


#: 退水历时分档上界（h），详情页「退水时间」条形图按这几档计数；末一档是 ≥ 最后一个边界
RECESSION_EDGES_H = (2.0, 4.0, 8.0)


def recession(out: dict[str, Any]) -> dict[str, Any]:
    """逐片区退水历时 = 片区最后一格退干（`drain_time_h`）− 代表格峰现（`peak_time_min`）。

    与成因规则树「演算末未退干」同一判据：最后有水时刻 ≥ 演算末 − 40 min 的片区算**没退干**，
    `recession_h = None`、单列 `undrained`，不拿演算时长顶替、不外推——一份一路涨到末帧的成果
    （`no_recession`）就会几乎全落在这一列，页面照实显示。中位数只在已退干的片区里取。
    不进缓存（纯派生），改分档不用重算。
    """
    end = out["inputs"]["sim_end_min"]
    rec: list[float] = []
    for c in out["clusters"]:
        drained = c["drain_time_h"] * 60 < end - UNDRAINED_MARGIN_MIN
        c["drained"] = drained
        c["recession_h"] = round(max(0.0, c["drain_time_h"] - c["peak_time_min"] / 60), 2) if drained else None
        if drained:
            rec.append(c["recession_h"])
    counts = np.bincount(np.searchsorted(RECESSION_EDGES_H, rec, side="right"),
                         minlength=len(RECESSION_EDGES_H) + 1) if rec else [0] * (len(RECESSION_EDGES_H) + 1)
    out["recession"] = {
        "edges_h": list(RECESSION_EDGES_H),
        "counts": [int(n) for n in counts],
        "undrained": len(out["clusters"]) - len(rec),
        "median_h": round(float(np.median(rec)), 2) if rec else None,
    }
    return out


def name_clusters(out: dict[str, Any]) -> dict[str, Any]:
    """「{最近 POI}附近{距离}米的溢水点」，距离从代表格（最深格）格心量起。见 `poi.py`。"""
    from . import poi

    for c in out["clusters"]:
        hit = poi.nearest(c["x"], c["y"]) if c.get("x") is not None else None
        c["name"] = hit["label"] if hit else None
        c["poi"] = {k: hit[k] for k in ("objectid", "name", "type", "distance_m")} if hit else None
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("target", help="仿真任务号，或成果目录（含 delivery_summary.json）")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()
    if args.target.isdigit():
        from . import sim_results
        from . import simulation as sim

        with sim.connect() as conn:
            run_dir = sim_results._run_dir(conn, int(args.target))
    else:
        run_dir = Path(args.target)
    r = load(run_dir)
    sm = r["summary"]
    print(f"{r['run_ref']}：入选 {sm['selected_cells']} 格 → {sm['cluster_count']} 个片区 {sm['by_mechanism']}")
    print(f"偏离文档口径：{r['deviations'] or '无'}")
    for c in r["clusters"][: args.top]:
        where = c["name"] or f"{c['lat']}, {c['lon']}"
        print(f"  #{c['rank']:<3} {c['score']:5.1f} {c['mechanism']:<12} 最大 {c['max_depth_m']:.3f} m  "
              f"≥0.15m {c['dur_ge_0_15m_h']:.2f} h  {where}")


if __name__ == "__main__":
    main()
