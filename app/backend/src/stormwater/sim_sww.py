"""把「四态」一二维双向耦合成果（ANUGA .sww + SWMM）抽成仿真系统认的交付形状，再登记成仿真任务。

第一份是 `data/temp/Mussafah_00_61mm2h/`（2026-09-26，四态）；2026-09-28 换成 `data/temp260928/Mussafah_00_61mm2h/`
（五态：L2+A 改名 L2+planned、新增 L2+suggested，L1 / L2+planned / L2+B 的 .sww 与上一版逐字节同大小同时刻）：

    Mussafah_00_61mm2h/
        00_说明.md                          四态口径与主要结果
        L1_既有管网/二维/L1_二维水深时序.sww   ANUGA 全时刻水深（15 m 规则三角网，每格 4 个三角形，~2.6 GB）
        L1_既有管网/二维/L1_耦合运行记录.json  逐时刻水量、1D↔2D 交换累计、start_h / end_h
        L1_既有管网/二维/耦合的一维/*.rpt|inp  与二维同进程耦合的 SWMM（ALLOW_PONDING NO）
        L2_已建成调蓄/说明.md                 区内 0 座已建成塘，本态 ≡ L1，不放文件
        L2+planned/…  L2+suggested/…  L2+B_本系统箱涵/…  同 L1 的结构
        对比_缺口分析/五态统计值.csv          35 项统计值及口径（本模块的「最大积水面积 / 最大水深」按它对账）

它没有 `delivery_summary.json`、没有逐格最大水深图层、没有 5 min 快照——`sim_results` / `sim_ponding`
一样都读不了。本模块从 .sww 抽出这三样，写成 v1 形状放到 `data/processed/sim_deliveries/<交付>/<态>/`
（原交付目录只读不写），再走 `sim_results.import_delivery` 登记。

## 抽取口径

- 格网：.sww 就是 15 m 规则网格切成的三角网（258,332 个三角形 = 131 × 493 格 × 4），逐格水深 = 格内
  三角形水深（stage_c − elevation_c）按面积平均。每格恰好 4 个三角形，不是 4 个就报错不硬抽。
- 时间：.sww 的 `time` 从**二维启动**起算，二维在模型时刻 `start_h`（0.5 h）才启动（此前没有溢流，
  地表无源）。系统里的 `time_minutes` 一律按**降雨开始**起算 = .sww 时刻 + start_h。交付自带的
  「最大积水面积 t = 1.67 h」等是 .sww 时刻，换到系统里要加 0.5 h。
- 帧：.sww 每 1 min 一帧，按系统口径每 5 min 抽一帧（模型时刻整 5 分钟）。最大水深、峰现时刻、
  最大流速是**全部**时刻扫出来的，不受抽帧影响。二维启动前没有帧，不补空帧。
- 地表不直接受雨：来水全部是 SWMM 井口溢流（耦合记录 末态水量 = 1→2 累计 − 2→1 累计，闭合差 0）。
  `surface_rainfall_inflow_m3` 取这条闭合的残差，`sim_ponding` 据此亮 `no_surface_rain`。
- 管网归因：没有逐格注入速率，写一份「节点 → 格」绑定（节点坐标取耦合 SWMM 的 [COORDINATES]），
  `sim_ponding` 走 `swmm_from_rpt` 降级（节点全程溢流量 × 绑定），偏离照记。
- 入流：这份 SWMM 没有汇水子区（雨量计也没被引用），驱动模型的是 647 个检查井的 `[INFLOWS]` 入流过程线
  （客户模型预先算好的径流）。`inflow_hydrograph.json` 把它们逐分钟线性插值后累加，按 5 min 出平均流量与累计
  入流量——详情页据此画「入流过程线」，**不画**系统按 Zone B 派生的雨型（那不是这份模型的输入）。
  核对：累计 34,803 m³ + 二维回灌检查井 12,610 m³ = 耦合 SWMM 报告的 External Inflow 47,414 m³。
- 最大积水面积 / 最大水深（2026-09-28 按交付的「五态统计值」改口径，页面原先的数是错的）：
  - 最大积水面积 = **同一时刻**三角形水深 ≥ 0.02 m 的面积（按三角形真实面积），取最大的那一帧（L1 16.03 ha @ 2.17 h）。
    原先页面取的是「任一帧 ≥ 0.01 m 的 15 m 格并集」（L1 33.2 ha），把不同时刻的水叠在一起，不是任何时刻的积水面积。
  - 最大水深 = 全部三角形全程最大水深的最大值（L1 1.352 m）。原先取 15 m 格内 4 个三角形的面积平均（0.99 m），把最深处抹平了。
  - 逐分钟的这两条时序（及同刻地表水量）写进 `surface_series.json`，效果分析的共同演算窗按它截取；格并集、格平均水深
    照旧留在 `results` 里（`inundated_area_ge_0_01m2` / `maximum_depth_cell_m`），出图与溢水点识别仍用 15 m 格。
  - 同一文件的 `overflow` 是耦合记录逐 10 min 的 SWMM → 地表累计溢出量，详情页播放时逐帧的「累计溢水量」按它取
    （`sim_results.timeline`）；旧抽取结果用 `overflow` 子命令补，不必重抽 .sww。
- 地形：`terrain_grid.npz` 由 .sww 的 `elevation_c` 逐格平均而来，带 `source = "sww"`，
  `sim_ponding` 照记 `terrain_from_sww`。计算域是闭合矩形（边界出流 ≈ 0），域内全部按陆地。

## 一个任务涵盖多态

整份交付登记成**一个**仿真任务：`run.output_dir` 指向 `sim_deliveries/<交付>/`，根上的 `states.json`
列出各态（key / 名称 / 对应防线 / 子目录 / same_as），图层接口带 `?state=` 取某一态，不给取 L1。
详情页右上角在这类任务上换成态切换（L1 / L2 / L2+planned / L2+suggested），「对比」各态并排。
交付里还有 L2+B（不做 Parsons、单做箱涵的对照），用户 2026-09-28 要求从系统删掉：`STATES` 不列它，抽取与登记都跳过。
对应防线：L1 → 第一道；L2（≡ L1，硬链 L1 的抽取结果，摘要单独写明）→ 第二道（现状）；
L2+planned、L2+suggested → 第二道（规划）。任务本身记最高一道（第二道规划），资源勾到这一道。
主线 L1 → L2 → L2+planned → L2+suggested 逐步叠加：`states.json` 的 `parent` 记每态叠在哪一态上，
效果分析「新增调蓄」按 parent 算（L2+suggested 只列 NP1–NP6，不把 Parsons 的塘井算成它的）。

用法：

    PYTHONPATH=src .venv/bin/python -m stormwater.sim_sww import data/temp260928/Mussafah_00_61mm2h --by AR
    PYTHONPATH=src .venv/bin/python -m stormwater.sim_sww extract data/temp260928/Mussafah_00_61mm2h   # 只抽不登记
    PYTHONPATH=src .venv/bin/python -m stormwater.sim_sww overflow data/temp260928/Mussafah_00_61mm2h  # 只补累计溢出量时序
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_ROOT = BACKEND_DIR / "data"
OUT_ROOT = DATA_ROOT / "processed" / "sim_deliveries"

SCHEMA = "flood_management.sww_state_delivery.v1"
WET_M = 0.01             # 出图 / 快照阈值，与 v1 / v2 交付一致
DELIVERY_WET_M = 0.02    # 交付「最大积水面积」的干湿阈值（三角形水深），页面的最大积水面积也用它
FRAME_STEP_S = 300       # 系统口径：每 5 min 一帧
CHUNK = 30               # 逐块读 .sww 的时间步数（一块 ~90 MB）

#: 态 → (子目录前缀, 防线, 中文名)。L2 没有自己的文件，抽取时硬链 L1
#: 两个 L2+ 同属第二道规划；效果分析页按 `COLUMNS` 分列：Parsons 是 Planned，在其上加箱涵是 Suggested。
#: 交付的 L2+B（单做箱涵的对照）按用户 2026-09-28 要求不接
STATES = {
    "L1": ("L1_", "line1", "Existing network"),
    "L2": ("L2_", "line2_current", "Built storage (≡ L1)"),
    "L2+planned": ("L2+planned", "line2_planned", "Planned scheme"),
    "L2+suggested": ("L2+suggested", "line2_planned", "Planned scheme + our box culverts"),
}
COLUMNS = {"L1": "line1", "L2": "line2_current", "L2+planned": "line2_planned",
           "L2+suggested": "line2_suggested"}
#: 每态叠在哪一态上（交付 00_说明.md 的「五态是什么」）；效果分析的「新增调蓄」按它算
PARENT = {"L1": None, "L2": "L1", "L2+planned": "L2", "L2+suggested": "L2+planned"}
#: 交付目录名：`<片区…>_<雨深>mm<历时>h[_<网格>m | _G<网格>]`，如 `Mussafah_00_61mm2h`、`C1_模型版_61mm2h_5m`、`YAS_61mm2h_G5`
_REGION_RX = re.compile(r"^(?P<region>.+?)_(?P<depth>\d+(?:\.\d+)?)mm(?P<dur>\d+(?:\.\d+)?)h"
                        r"(?:_G(?P<g>\d+(?:\.\d+)?)|_(?P<m>\d+(?:\.\d+)?)m)?(?:_全套成果)?(?:_after)?$")
#: 历史交付（无网格后缀）的标称网格；输出键只在网格不等于它时才带后缀，已抽好的目录名因此不变
LEGACY_RES_M = 15.0


def state_label(state: str) -> str:
    """态的展示名（`states.json` 的 label，中文原串；i18n.EN 有对应英文，改这里要同步改键）。"""
    return f"{state} {STATES[state][2]}"


class SwwError(Exception):
    pass


# ---------------------------------------------------------------------------
# 读交付
# ---------------------------------------------------------------------------


def _state_dir(root: Path, state: str) -> Path | None:
    hits = [p for p in root.iterdir() if p.is_dir() and p.name.startswith(STATES[state][0])]
    return hits[0] if len(hits) == 1 else None


def _one(d: Path, pattern: str) -> Path:
    hits = sorted(p for p in d.glob(pattern) if not p.name.startswith("._"))   # 忽略 macOS 打包带的 ._ 伴生文件
    if len(hits) != 1:
        raise SwwError(f"{d} 下 {pattern} 找到 {len(hits)} 个，需恰好一个")
    return hits[0]


def parse_delivery_name(name: str) -> tuple[str, float, float, float | None]:
    """目录名 → (片区, 雨深 mm, 历时 h, 目录名里标的网格 m；没标为 None)。"""
    m = _REGION_RX.match(name)
    if not m:
        raise SwwError(f"交付目录名 {name} 不是「片区_雨深mm历时h[_网格m]」的形状")
    res = m.group("g") or m.group("m")
    return m.group("region"), float(m.group("depth")), float(m.group("dur")), float(res) if res else None


def norm_geom(g: dict) -> dict:
    """GeoJSON 几何兜底：RD-A 成果的 `计算域.geojson` 把 Polygon 写成了「一个环」（坐标少一层嵌套），补回去。"""
    c = g.get("coordinates")
    if g.get("type") == "Polygon" and c and isinstance(c[0], list) and c[0] and isinstance(c[0][0], (int, float)):
        return {**g, "coordinates": [c]}
    return g


def _first_xy(c):
    while isinstance(c, list) and c and isinstance(c[0], list):
        c = c[0]
    return c


def is_projected(g: dict) -> bool:
    """坐标已经是米（UTM）而不是经纬度：RD-A 成果的 `计算域.geojson` 标了 CRS84，坐标却是 EPSG:32640。"""
    xy = _first_xy(g.get("coordinates") or [])
    return bool(xy) and abs(xy[0]) > 360


def _parse_name(root: Path) -> tuple[str, float, float]:
    return parse_delivery_name(root.name)[:3]


def output_key(root: Path, res_m: float | None = None) -> str:
    """抽取结果目录名（`sim_deliveries/<键>/`）。网格取运行记录的 `res_m`（缺则取目录名里标的），
    不是历史的 15 m 就加 `_<n>m` 后缀，让同雨量的 15 m 与 5 m 并存、互不覆盖。"""
    region, depth, dur, named = parse_delivery_name(root.name)
    res = res_m if res_m else named
    key = f"{region}_{depth:g}mm{dur:g}h"
    return key if res is None or abs(res - LEGACY_RES_M) < 1e-6 else f"{key}_{res:g}m"


def _node_coords(inp: Path) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    sec = None
    with inp.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if s.startswith("["):
                sec = s.upper()
                continue
            if sec != "[COORDINATES]" or not s or s.startswith(";"):
                continue
            t = s.split()
            if len(t) >= 3:
                out[t[0]] = (float(t[1]), float(t[2]))
    return out


#: SWMM FLOW_UNITS → m³/s
_FLOW_M3S = {"CMS": 1.0, "LPS": 1e-3, "MLD": 1 / 86.4, "CFS": 0.0283168466, "GPM": 6.30901964e-5, "MGD": 0.0438126364}
INFLOW_STEP_MIN = 5


def _swmm_hours(s: str) -> float:
    """TIMESERIES 的时刻：十进制小时或 H:MM[:SS]。"""
    if ":" in s:
        parts = [float(x) for x in s.split(":")]
        return parts[0] + parts[1] / 60 + (parts[2] / 3600 if len(parts) > 2 else 0)
    return float(s)


def _inflow_hydrograph(inp: Path, rain_mm: float, rain_min: float, area_ha: float | None) -> dict[str, Any] | None:
    """`[INFLOWS]` 各节点外部入流过程线逐分钟累加 → 5 min 平均流量 + 累计入流量。没有外部入流返回 None。

    SWMM 的外部入流 = 时间序列 × 比例系数 + 基线；时间序列按线性插值、首尾之外取 0（与 SWMM 一致）。"""
    sec = None
    units = "CMS"
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
            elif sec == "[INFLOWS]" and len(t) >= 3 and t[1].upper() == "FLOW":
                sfac = float(t[5]) if len(t) > 5 else 1.0
                base = float(t[6]) if len(t) > 6 else 0.0
                inflows.append((t[0], t[2], sfac, base))
            elif sec == "[TIMESERIES]" and len(t) == 3:
                ts.setdefault(t[0], []).append((_swmm_hours(t[1]), float(t[2])))
    if not inflows:
        return None
    if units not in _FLOW_M3S:
        raise SwwError(f"{inp.name}：不认识的流量单位 {units}")
    ends = [max(h for h, _ in ts[name]) for _, name, _, _ in inflows if name in ts]
    grid = np.arange(0, int(np.ceil(max(ends) * 60)) + 1, dtype=float)       # 分钟
    q = np.zeros_like(grid)
    for _, name, sfac, base in inflows:
        pts = ts.get(name)
        if not pts:
            continue
        a = np.array(pts)
        q += np.interp(grid, a[:, 0] * 60, a[:, 1], left=0.0, right=0.0) * sfac + base
    q *= _FLOW_M3S[units]
    nz = np.nonzero(q > 1e-9)[0]
    end = int(np.ceil((nz[-1] + 1) / INFLOW_STEP_MIN) * INFLOW_STEP_MIN) if len(nz) else INFLOW_STEP_MIN
    end = max(end, int(np.ceil(rain_min / INFLOW_STEP_MIN) * INFLOW_STEP_MIN))
    grid, q = grid[: end + 1], np.pad(q, (0, max(0, end + 1 - len(q))))[: end + 1]
    # 每步体积 = 分钟点梯形积分；5 min 平均流量 = 步体积 / 300 s
    seg = (q[:-1] + q[1:]) / 2 * 60
    vol_step = seg.reshape(-1, INFLOW_STEP_MIN).sum(1)
    total = float(seg.sum())
    ip = int(np.argmax(q))
    return {
        "schema": SCHEMA,
        "source": f"{inp.name} [INFLOWS]",
        "node_count": len(inflows),
        "flow_units": units,
        "step_min": INFLOW_STEP_MIN,
        "t_min": [float(x) for x in np.arange(0, end, INFLOW_STEP_MIN)],
        "q_mean_m3s": [round(float(v) / (INFLOW_STEP_MIN * 60), 4) for v in vol_step],
        "cum_volume_m3": [round(float(v), 1) for v in np.cumsum(vol_step)],
        "total_volume_m3": round(total, 1),
        "peak_m3s": round(float(q[ip]), 3),
        "peak_time_min": float(grid[ip]),
        "first_inflow_min": float(grid[nz[0]]) if len(nz) else None,
        "last_inflow_min": float(grid[nz[-1]]) if len(nz) else None,
        "rainfall_total_mm": rain_mm,
        "rainfall_duration_minutes": rain_min,
        "catchment_area_ha": area_ha,
        # 总入流量摊到汇水区上的等效径流深，与雨量相比即综合径流系数（汇水区面积取交付的 region_ha）
        "equivalent_depth_mm": round(total / (area_ha * 1e4) * 1000, 1) if area_ha else None,
    }


def _region_ha(root: Path) -> float | None:
    for name in ("五态缺口表.json", "四态缺口表.json"):     # 2026-09-28 起五态；旧版四态
        try:
            v = json.loads((root / "对比_缺口分析" / name).read_text(encoding="utf-8")).get("region_ha")
        except (OSError, ValueError):
            continue
        return float(v) if v else None
    return None


def _outfalls(inp: Path) -> list[tuple[str, str, str | None]]:
    """[(名, 类型, 固定水位)]"""
    out = []
    sec = None
    with inp.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if s.startswith("["):
                sec = s.upper()
                continue
            if sec == "[OUTFALLS]" and s and not s.startswith(";"):
                t = s.split()
                out.append((t[0], t[2].upper(), t[3] if t[2].upper() == "FIXED" and len(t) > 3 else None))
    return out


# ---------------------------------------------------------------------------
# 从 .sww 抽格网
# ---------------------------------------------------------------------------


def _extract_grid(sww: Path, start_s: float, cs: float = 15.0) -> dict[str, Any]:
    """逐格：高程、最大水深 / 峰现 / 最大流速（全时刻）、末帧水深、每 5 min 一帧的水深。"""
    from scipy.io import netcdf_file

    f = netcdf_file(sww, "r", mmap=True)
    try:
        v = f.variables
        x, y = np.array(v["x"][:], float), np.array(v["y"][:], float)
        vol = np.array(v["volumes"][:], np.int64)
        ec = np.array(v["elevation_c"][:], float)
        times = np.array(v["time"][:], float)
        x0, x1, y0, y1 = x.min(), x.max(), y.min(), y.max()
        nc, nr = round((x1 - x0) / cs), round((y1 - y0) / cs)
        cx, cy = x[vol].mean(1), y[vol].mean(1)
        col = np.floor((cx - x0) / cs).astype(np.int64)
        row = np.floor((y1 - cy) / cs).astype(np.int64)
        if col.min() < 0 or col.max() >= nc or row.min() < 0 or row.max() >= nr:
            raise SwwError(f"{sww.name}：三角形格心越出 {nr} × {nc} 格网")
        cell = row * nc + col
        n = nr * nc
        cnt = np.bincount(cell, minlength=n)
        if not np.all((cnt == 4) | (cnt == 0)):
            raise SwwError(f"{sww.name} 不是 15 m 规则网格切的三角网（有格子不是 4 个三角形）")
        p = np.stack([x[vol], y[vol]], -1)
        area = 0.5 * np.abs((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
                            - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1]))
        cell_area = np.bincount(cell, area, n)
        has = cnt > 0
        dem = np.full(n, np.nan)
        dem[has] = np.bincount(cell, ec * area, n)[has] / cell_area[has]

        model_s = times + start_s
        keep = np.where(np.abs(np.mod(model_s + 0.5, FRAME_STEP_S) - 0.5) < 1e-3)[0]   # 模型时刻整 5 分钟
        maxd = np.zeros(n)
        peak_s = np.full(n, np.nan)
        maxsp = np.zeros(n)
        frames: list[tuple[float, np.ndarray]] = []
        tri_hmax = 0.0
        # 逐时刻三角形水深 ≥ 0.02 m 的面积：交付「最大积水面积」的口径（L1 16.03 ha 在 .sww 1.667 h 逐位对上）
        wet_tri_area = []
        hmax_t = []
        wet_tri_t = []
        vol_t = []
        last = None
        keep_set = set(keep.tolist())
        for a in range(0, len(times), CHUNK):
            b = min(a + CHUNK, len(times))
            st = np.array(v["stage_c"][a:b], float)
            xm = np.array(v["xmomentum_c"][a:b], float)
            ym = np.array(v["ymomentum_c"][a:b], float)
            for i in range(b - a):
                h = np.maximum(st[i] - ec, 0.0)
                hmax_t.append(float(h.max()))
                tri_hmax = max(tri_hmax, hmax_t[-1])
                wet_tri_area.append(float(area[h >= DELIVERY_WET_M].sum()))
                wet_tri_t.append(float(times[a + i]))
                vol_t.append(float((h * area).sum()))
                dc = np.bincount(cell, h * area, n)
                mx = np.bincount(cell, xm[i] * area, n)
                my = np.bincount(cell, ym[i] * area, n)
                d = np.zeros(n)
                d[has] = dc[has] / cell_area[has]
                sp = np.zeros(n)
                w = d >= WET_M
                sp[w] = np.hypot(mx[w], my[w]) / dc[w]
                up = d > maxd
                maxd[up] = d[up]
                peak_s[up] = model_s[a + i]
                np.maximum(maxsp, sp, out=maxsp)
                if a + i in keep_set:
                    frames.append((float(model_s[a + i]), d.astype(np.float32)))
                last = d
    finally:
        v = None      # 先放掉变量引用，mmap 才关得干净
        f.close()
    return {
        "nr": nr, "nc": nc, "cs": cs, "x0": float(x0), "ytop": float(y1),
        "bounds": [float(x0), float(y0), float(x1), float(y1)],
        "n_tri": int(len(vol)), "has": has, "dem": dem,
        "maxd": maxd, "peak_s": peak_s, "maxsp": maxsp, "final": last,
        "frames": frames, "end_s": float(model_s[-1]), "start_s": start_s,
        "tri_hmax": tri_hmax, "tri_wet_max_m2": max(wet_tri_area),
        "tri_wet_max_sww_h": wet_tri_t[int(np.argmax(wet_tri_area))] / 3600, "tri_vol_max_m3": max(vol_t),
        "tri_vol_end_m3": vol_t[-1],
        "series": {"time_minutes": [round((t + start_s) / 60, 3) for t in wet_tri_t],
                   "wet_area_m2": [round(a, 1) for a in wet_tri_area],
                   "max_depth_m": [round(x, 4) for x in hmax_t],
                   "volume_m3": [round(x, 1) for x in vol_t]},
    }


def _cell_polys(g: dict[str, Any], ids: np.ndarray) -> list[list[list[float]]]:
    """格号 → WGS84 方格（逆时针、首尾闭合）。投影转换借 rasterio（已是依赖，不引 pyproj）。"""
    from rasterio.warp import transform

    nc, cs = g["nc"], g["cs"]
    r, c = np.divmod(ids, nc)
    xl, xr = g["x0"] + c * cs, g["x0"] + (c + 1) * cs
    yt, yb = g["ytop"] - r * cs, g["ytop"] - (r + 1) * cs
    xs = np.concatenate([xl, xr, xr, xl])
    ys = np.concatenate([yt, yt, yb, yb])
    lon, lat = transform("EPSG:32640", "EPSG:4326", xs.tolist(), ys.tolist())
    k = len(ids)
    lon, lat = np.array(lon).reshape(4, k), np.array(lat).reshape(4, k)
    out = []
    for j in range(k):
        ring = [[round(float(lon[q, j]), 7), round(float(lat[q, j]), 7)] for q in (0, 3, 2, 1, 0)]
        out.append(ring)
    return out


# ---------------------------------------------------------------------------
# 写 v1 形状的交付
# ---------------------------------------------------------------------------


def _dump(p: Path, obj: Any) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)


def _overflow_series(rec: dict[str, Any]) -> dict[str, Any]:
    """耦合记录逐 10 min 的「SWMM → 地表」累计溢出量（acc_1to2）。记录的 `t_h` 已是降雨起算的模型时刻
    （其 wet_m2 峰现 2.17 h = surface_series 的 130 min），不再加 start_h。播放时逐帧的累计溢水量按它取。"""
    ser = rec.get("series") or []
    return {"note": "耦合运行记录逐 10 min 的 SWMM → 地表累计溢出量；time_minutes 从降雨起算",
            "time_minutes": [round(float(r["t_h"]) * 60, 3) for r in ser],
            "cum_m3": [round(float(r["acc_1to2"]), 1) for r in ser]}


def patch_overflow(root: Path) -> list[Path]:
    """只把耦合记录的累计溢出量补进已抽好的 `surface_series.json`，不重抽 .sww（L2 ≡ L1，取 L1 的记录）。"""
    root = root.resolve()
    base = OUT_ROOT / root.name
    done = []
    for st in json.loads((base / "states.json").read_text(encoding="utf-8"))["states"]:
        src = _state_dir(root, "L1" if st.get("same_as") == "L1" else st["key"])
        p = base / st["dir"] / "surface_series.json"
        if src is None or not p.is_file():
            continue
        rec = json.loads(_one(src / "二维", "*_耦合运行记录.json").read_text(encoding="utf-8"))
        ser = json.loads(p.read_text(encoding="utf-8"))
        ser["overflow"] = _overflow_series(rec)
        _dump(p, ser)   # 先写临时文件再换名，L2 与 L1 的硬链就此断开，各存一份
        done.append(p)
    return done


def extract_state(root: Path, state: str, out: Path) -> dict[str, Any]:
    region, depth_mm, dur_h = _parse_name(root)
    sd = _state_dir(root, state)
    if sd is None:
        raise SwwError(f"{root} 下没有 {state} 的目录")
    d2 = sd / "二维"
    sww = _one(d2, "*_二维水深时序.sww")
    rec = json.loads(_one(d2, "*_耦合运行记录.json").read_text(encoding="utf-8"))
    rpt = _one(d2 / "耦合的一维", "*.rpt")
    inp = _one(d2 / "耦合的一维", "*.inp")
    start_s = float(rec["start_h"]) * 3600

    g = _extract_grid(sww, start_s, float(rec.get("res_m", 15.0)))
    out.mkdir(parents=True, exist_ok=True)
    (out / "temporal_snapshots").mkdir(exist_ok=True)
    nc, cs = g["nc"], g["cs"]
    cell_a = cs * cs

    # 逐格最大水深（只列曾 ≥ 0.01 m 的格；各帧的格子都在其中）
    ids = np.where(g["maxd"] >= WET_M)[0]
    polys = _cell_polys(g, ids)
    feats = []
    for cid, ring in zip(ids.tolist(), polys):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "cell_id": cid,
                "depth_m": round(float(g["maxd"][cid]), 4),
                "maximum_depth_m": round(float(g["maxd"][cid]), 4),
                "maximum_depth_time_minutes": round(float(g["peak_s"][cid]) / 60, 2),
                "final_depth_m": round(float(g["final"][cid]), 4),
                "maximum_speed_m_s": round(float(g["maxsp"][cid]), 3),
                "model_cell_size_m": cs,
            },
        })
    _dump(out / "maximum_depth_wgs84.geojson",
          {"type": "FeatureCollection", "name": f"{region}_{state}_maximum_depth", "features": feats})

    snaps = []
    for i, (ts, d) in enumerate(g["frames"]):
        w = np.where(d >= WET_M)[0]
        name = f"temporal_snapshots/surface_depth_t{i:03d}.geojson"
        _dump(out / name, {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": None,
                          "properties": {"cell_id": int(c), "depth_m": round(float(d[c]), 4)}} for c in w],
            "metadata": {"time_index": i, "time_seconds": ts, "time_minutes": ts / 60},
        })
        snaps.append({"index": i, "time_seconds": ts, "time_minutes": ts / 60, "path": name,
                      "feature_count": int(len(w))})
    for old in (out / "temporal_snapshots").glob("surface_depth_t*.geojson"):
        if int(old.stem[-3:]) >= len(snaps):
            old.unlink()
    _dump(out / "temporal_snapshots" / "manifest.json", {"schema": SCHEMA, "snapshots": snaps})
    # 逐分钟（.sww 每帧）的三角形口径：同刻积水面积（≥ 0.02 m）、该帧最大水深、地表水量；时刻按降雨起算
    _dump(out / "surface_series.json", {
        "schema": SCHEMA, "wet_threshold_m": DELIVERY_WET_M,
        "note": "逐 .sww 帧、按三角单元：wet_area_m2 = 水深 ≥ 阈值的三角形面积之和，max_depth_m = 该帧三角形最大水深，"
                "volume_m3 = Σ 水深 × 三角形面积；time_minutes 从降雨起算（.sww 时刻 + 二维启动）",
        **g["series"],
        "overflow": _overflow_series(rec),
    })

    # 地形（供 sim_ponding 填洼；来源写进 npz，sim_ponding 照记 terrain_from_sww）
    has = g["has"].reshape(g["nr"], nc)
    np.savez_compressed(
        out / "terrain_grid.npz",
        values=np.nan_to_num(g["dem"], nan=0.0).reshape(g["nr"], nc),
        transform=np.array([cs, 0.0, g["x0"], 0.0, -cs, g["ytop"]]),
        land_mask=has, permanent_water_mask=np.zeros_like(has), source=np.array("sww"),
    )

    # 节点 → 格绑定（sim_ponding 的 swmm_from_rpt 降级按这个把节点溢流量落到格上）
    coords = _node_coords(inp)
    binds = []
    for k, (nid, (xx, yy)) in enumerate(coords.items()):
        c = int(np.floor((xx - g["x0"]) / cs))
        r = int(np.floor((g["ytop"] - yy) / cs))
        if 0 <= r < g["nr"] and 0 <= c < nc:
            binds.append(f'{{"swmm_node_id": {json.dumps(nid)}, "swmm_node_index": {k}, "anuga_cell_index": {r * nc + c}}}')
    (out / "node_cell_binding.json").write_text(
        '{"schema": "' + SCHEMA + '", "note": "节点坐标取耦合 SWMM 的 [COORDINATES]，落到 15 m 格", "bindings": [\n'
        + ",\n".join(binds) + "\n]}\n", encoding="utf-8")
    shutil.copyfile(rpt, out / "swmm_coupled.rpt")          # 不用 copy2：NAS 文件常是 660，权限位带过来 API 进程读不了
    shutil.copyfile(inp, out / "coupled_scenario.inp")
    hyd = _inflow_hydrograph(inp, depth_mm, dur_h * 60, _region_ha(root))
    if hyd is not None:
        _dump(out / "inflow_hydrograph.json", hyd)

    outf = _outfalls(inp)
    fixed = [o for o in outf if o[1] == "FIXED" and o[2] is not None]
    _dump(out / "scenario.json", {
        "scope": region,
        # 雨不是直接落在二维上，是客户模型逐井给的入流过程线；不写 uniform，免得被登记成「Uniform citywide」
        "spatial_pattern": "Per-node inflows (client model)",
        "rainfall_profile": {"profile_id": f"R{depth_mm:g}D{dur_h:g}",
                             "provenance": {"temporal_assumption": "node inflow hydrographs"}},
        "outfall_mode": "fixed" if fixed else "free",
        "outfall_level_m": float(fixed[0][2]) if fixed else None,
        "outfalls": [{"name": o[0], "type": o[1], "stage_m": o[2]} for o in outf],
        "start_time": None,        # 模型钟是 SWMM 的 2000-01-01 虚拟时刻，不是真实日期
    })

    acc12, acc21 = float(rec["acc_1to2_m3"]), float(rec["acc_2to1_m3"])
    rain_resid = float(rec["final_vol_m3"]) - (acc12 - acc21) + float(rec.get("boundary_out_m3") or 0)
    n_all = int(g["has"].sum())
    summary = {
        "schema": SCHEMA,
        "run_id": f"{root.name}/{state}",
        "scenario_label": f"{region} · {depth_mm:g} mm / {dur_h:g} h · {state} {STATES[state][2]}",
        "state": state,
        "status": "completed",
        "solver": "ANUGA 2D + SWMM 5.2.4",
        "claim_boundary": (
            f"{state}（{STATES[state][2]}）：SWMM 与 ANUGA 同进程逐步双向耦合，15 m 网格，二维从降雨后 "
            f"{rec['start_h']:g} h 起算到 {rec['end_h']:g} h；地表不直接受雨，来水全是井口溢流。"
            "交付方未标定、未经工程认可。"
            + ("箱涵的 1.5 倍聚类系数是假设，空地权属与地下管线未核。" if state == "L2+suggested" else "")
            + ("2027 过渡方案，规划设计，未建。" if state in ("L2+planned", "L2+suggested") else "")
        ),
        "source": {"delivery": str(root.relative_to(DATA_ROOT)) if DATA_ROOT in root.parents else str(root),
                   "state_dir": sd.name, "native_sww": sww.name, "coupling_record": rec.get("tag")},
        "domain": {
            "bounds_epsg32640": g["bounds"],
            "cell_size_m": cs,
            "rectangular_cells": g["nr"] * nc,
            "active_land_cells": n_all,
            "triangle_count": g["n_tri"],
            "coupling_start_minutes": start_s / 60,
            "simulation_duration_minutes": g["end_s"] / 60,
            "simulation_duration_hours": g["end_s"] / 3600,
            "output_step_minutes": FRAME_STEP_S / 60,
        },
        "results": {
            # 页面「最大积水面积 / 最大水深」读这三项：交付口径（三角形，面积取同一时刻 ≥ 0.02 m 的最大那一帧）
            "maximum_depth_m": round(g["tri_hmax"], 4),
            "peak_inundated_area_m2": round(g["tri_wet_max_m2"], 1),
            "peak_inundated_area_minutes": round(g["tri_wet_max_sww_h"] * 60 + start_s / 60, 2),
            "peak_inundated_threshold_m": DELIVERY_WET_M,
            # 15 m 格口径（出图、溢水点识别用），不再当「最大」对外报
            "maximum_depth_cell_m": round(float(g["maxd"].max()), 4),
            "maximum_depth_triangle_m": round(g["tri_hmax"], 4),
            "inundated_cells_ge_0_01m": int(len(ids)),
            "inundated_area_ge_0_01m2": float(len(ids) * cell_a),
            "inundated_area_ge_0_05m2": float((g["maxd"] >= 0.05).sum() * cell_a),
            "final_inundated_cell_count_ge_0_01m": int((g["final"] >= WET_M).sum()),
            "final_inundated_area_ge_0_01m2": float((g["final"] >= WET_M).sum() * cell_a),
            # 交付口径（三角形水深 ≥ 0.02 m、时刻按 .sww 即二维启动起算），只为与交付的摘要对账，页面不用
            "delivery_peak_wet_area_ge_0_02m2": round(g["tri_wet_max_m2"], 1),
            "delivery_peak_wet_time_sww_h": round(g["tri_wet_max_sww_h"], 3),
            "peak_surface_volume_m3": g["tri_vol_max_m3"],
            "final_surface_volume_m3": g["tri_vol_end_m3"],
            "minimum_published_depth_m": WET_M,
        },
        "coupling": {
            "coupling_mode": "bidirectional",
            "coupled_node_count": rec.get("n_coupled"),
            "exchange_step_seconds": rec.get("dt_c"),
            "total_swmm_to_anuga_m3": acc12,
            "total_anuga_to_swmm_m3": acc21,
            "boundary_outflow_m3": rec.get("boundary_out_m3"),
        },
        "model_configuration": {
            "coupling_mode": "bidirectional",
            "return_period_years": None,
            "rainfall_total_mm": depth_mm,
            "rainfall_duration_minutes": dur_h * 60,
            "exchange_quantity": "node overflow to surface and surface return to node (bidirectional)",
            "dynamic_head_feedback": True,
            "simulation_duration_minutes": g["end_s"] / 60,
            "output_interval_minutes": FRAME_STEP_S / 60,
            "model_cell_size_m": cs,
            "terrain_product": "customer 5 m DTM, 15 m ANUGA grid",
            # 地表是否直接受雨的实证：耦合记录的水量闭合残差（末态 − 1→2 + 2→1），≈ 0 即全靠溢流
            "surface_rainfall_inflow_m3": round(max(rain_resid, 0.0), 3),
        },
        "outputs": {
            "maximum_depth": "maximum_depth_wgs84.geojson",
            "timeline_manifest": "temporal_snapshots/manifest.json",
            "swmm_report": "swmm_coupled.rpt",
            "swmm_input": "coupled_scenario.inp",
            "coupling_receipt": "node_cell_binding.json",
            "surface_series": "surface_series.json",
            **({"inflow_hydrograph": "inflow_hydrograph.json"} if hyd is not None else {}),
        },
    }
    _dump(out / "delivery_summary.json", summary)
    return summary


def _link_l2(l1: Path, l2: Path, root: Path) -> dict[str, Any]:
    """L2 ≡ L1：硬链 L1 的抽取结果（同卷零拷贝，`resolve()` 不跟硬链，路径校验照常过），摘要单独写。"""
    if l2.exists():
        shutil.rmtree(l2)
    for p in l1.rglob("*"):
        q = l2 / p.relative_to(l1)
        if p.is_dir():
            q.mkdir(parents=True, exist_ok=True)
        elif p.name != "delivery_summary.json":
            q.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(p, q)
            except OSError:
                shutil.copy2(p, q)
    s = json.loads((l1 / "delivery_summary.json").read_text(encoding="utf-8"))
    note = (root / "L2_已建成调蓄" / "说明.md")
    s["run_id"] = s["run_id"].rsplit("/", 1)[0] + "/L2"
    s["state"] = "L2"
    s["scenario_label"] = s["scenario_label"].replace("L1 既有管网", "L2 已建成调蓄（≡ L1）")
    s["claim_boundary"] = ("L2（已建成调蓄）：区内 0 座已建成塘（最近一座 7.3 km 外），交付方判定本态 ≡ L1，"
                           "所有结果即 L1 的结果。" + s["claim_boundary"].split("：", 1)[1])
    s["source"]["state_dir"] = note.parent.name if note.is_file() else "L2（交付未单列）"
    s["source"]["same_as"] = "L1"
    _dump(l2 / "delivery_summary.json", s)
    return s


def extract(root: Path, only: list[str] | None = None) -> dict[str, Path]:
    root = root.resolve()
    base = OUT_ROOT / root.name
    done: dict[str, Path] = {}
    for state in STATES:
        if only and state not in only:
            continue
        out = base / state.replace("+", "_")
        if state == "L2":
            l1 = base / "L1"
            if not (l1 / "delivery_summary.json").is_file():
                extract_state(root, "L1", l1)
            _link_l2(l1, out, root)
        elif _state_dir(root, state) is None:
            continue
        else:
            extract_state(root, state, out)
        done[state] = out
        print(f"  {state} → {out.relative_to(DATA_ROOT)}")
    # 交付的逐态统计值原样带过来（效果分析的「系统储水能力」按它取，见 sim_perf），缺了不报错
    for name in ("五态统计值.csv", "四态统计值.csv"):
        src = root / "对比_缺口分析" / name
        if src.is_file():
            (base / "analysis").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, base / "analysis" / "state_stats.csv")
            break
    # 清单只列目录真在的态（按 STATES 的先后），不带上次抽过、这次没选的
    present = [st for st in STATES if (base / st.replace("+", "_") / "delivery_summary.json").is_file()]
    _dump(base / "states.json", {
        "schema": "flood_management.sww_states.v1",
        "delivery": root.name,
        "default": "L1" if "L1" in present else present[0],
        "states": [{"key": st, "label": state_label(st), "defence_line_key": STATES[st][1],
                    "column": COLUMNS[st], "dir": st.replace("+", "_"),
                    "parent": PARENT[st] if PARENT[st] in present else None,
                    "same_as": "L1" if st == "L2" else None} for st in present],
    })
    return done


def import_all(root: Path, by: str | None = None, only: list[str] | None = None) -> int:
    """抽取后把整份交付登记成一个任务（按 output_dir 去重，重复执行只更新）。"""
    from . import sim_results
    from . import simulation as sim

    root = root.resolve()
    region, _, _ = _parse_name(root)
    extract(root, only)
    base = OUT_ROOT / root.name
    keys = [s["key"] for s in json.loads((base / "states.json").read_text(encoding="utf-8"))["states"]]
    top = max((STATES[k][1] for k in keys), key=sim.LINE_ORDER.index)
    with sim.connect() as conn:
        return sim_results.import_delivery(conn, base, by, line_key=top, extent=region,
                                           variant_label=f"{len(keys)} states: {' / '.join(keys)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("overflow", help="只把耦合记录的累计溢出量补进已抽好的 surface_series.json")
    p.add_argument("path", type=Path)
    for name, hlp in (("extract", "只从 .sww 抽出交付形状"), ("import", "抽取并登记为仿真任务")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("path", type=Path)
        p.add_argument("--state", action="append", choices=list(STATES), help="只处理这几态（可重复）")
        if name == "import":
            p.add_argument("--by", help="操作人缩写，如 ZN")
    args = ap.parse_args()
    if args.cmd == "overflow":
        for q in patch_overflow(args.path):
            print(f"  {q.relative_to(DATA_ROOT)}")
        return
    if args.cmd == "extract":
        extract(args.path, args.state)
        return
    print(f"已登记为仿真 #{import_all(args.path, args.by, args.state)}")


if __name__ == "__main__":
    main()
