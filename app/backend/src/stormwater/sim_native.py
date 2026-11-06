"""把「原生计算结果」（AAM 全行政区一二维耦合，SWMM .out/.rpt + ANUGA .sww）抽成仿真系统认的交付形状，再登记成仿真任务。

交付是 `data/261016/原生计算结果_可拷贝/`（2026-10-16，阿尔艾因全行政区，100 m 计算网格，60 mm/h 降 1 h + 退水 4 h，共 5 h）：

    原生计算结果_可拷贝/
        请先阅读_原生结果说明.txt  原生文件完整性与质量.json
        L1/   AAM_L1_2D.sww（ANUGA，61 帧 × 2,688,898 三角形，3 GB）  AAM_L1_1D.rpt（1.6 GB，其中 98% 是逐节点逐管段的时间序列）
              AAM_L1_1D.out（SWMM 二进制）  AAM_L1_1D.inp  run_receipt.json  water_balance_windows.json（30 s 一窗的水量账）
        L2/   同 L1 的结构；比 L1 多一座初始为空的已确认雨水塘（45,000 m³，只靠管道进水）
        配套参数与标识/  configuration.json（计算口径）  node_mapping.csv  link_mapping_and_adaptations.csv  grid.npz …

和已有的几种交付（`sim_sww` / `sim_city` / `sim_c1`）不同，也是不能直接复用的原因：

- 地表**直接受雨**（均匀 60 mm/h，只下在 ANUGA 地表；没有汇水子区、没有外部入流）。雨量 806,669,400 m³，
  管网只靠井口 / 排口与地表双向交换水量（地表 → 管网 18.3 Mm³，管网 → 地表 17.9 Mm³）。
- 网格是 100 m 规则方格各切两个三角形（每格 5000 m² × 2），共 1,344,449 个有效方格、13,444 km²。逐三角形出图页面画不动，
  落图格网取 **250 m**（绝对坐标对齐 250 m 整数倍，与全市交付同口径），格内水深 = 三角形水深按面积平均；
  格平均会把最深处抹平，所以页面卡片的最大水深 / 积水面积按**三角形**统计（同 `sim_city`）。
- .sww 的 x / y 是局部坐标，绝对位置 = + `xllcorner` / `yllcorner`（EPSG:32640）。水深 = `stage_c − elevation_c`，
  速度 = 动量 / 水深（干单元零水深处理）。时间从降雨开始起算（starttime = 0），每 300 s 一帧，共 61 帧（含 0 时刻）。
- 耦合 SWMM 的 .rpt 把每个节点 / 管段的 5 min 时间序列都写了进去（2,580 万行），而页面只读它前面的汇总表
  （溢流节点、管段流量、泵、储水）。抽取时只保留汇总表、去掉 `WARNING` 逐行提示与逐节点时间序列（1.6 GB → 约 130 MB），
  末尾补回「Analysis begun / ended」。.out 二进制页面不读，不进抽取结果（原件仍在交付目录）。
- 累计溢水量取 `water_balance_windows.json`（30 s 一窗）里「管网 → 地表」的逐窗累加，在每个 5 min 帧上取值。
- **质量**：交付自己标了 `both_quality_passed = false`（SWMM 连续性误差 1.27 %、0.87 % 的步未收敛）。原生结果照留，
  页面的 `claim_boundary` 把这点写明，不得当作「物理精度已验证」的成果引用。
- L2 的塘全程没有进水（`pond_final_volume_m3 = 0`，管道进水口高于地面积水），两态结果近乎一致；如实呈现，不夸大。

## 两态、一个任务

L1（既有管网，第一道）→ L2（同一基线 + 已确认雨水塘，第二道现状），L2 的 `parent` 是 L1。整份交付登记成**一个**多态任务。

## 用法

    # 本机：抽取 + 登记（交付目录 9.7 GB，只在算力 / 开发机上需要）
    PYTHONPATH=src python -m stormwater.sim_native import <原生计算结果_可拷贝> --by ZN
    PYTHONPATH=src python -m stormwater.sim_native extract <原生计算结果_可拷贝>      # 只抽不登记
    # 服务器：只带抽取结果（约 1 GB，在 data/processed/sim_deliveries/AAM_60mm1h_100m/），不需要原件
    PYTHONPATH=src python -m stormwater.sim_native register AAM_60mm1h_100m --by ZN   # 只登记已抽好的结果
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from .sim_sww import (DATA_ROOT, DELIVERY_WET_M, OUT_ROOT, WET_M, FRAME_STEP_S, SwwError, _cell_polys, _dump,
                      _node_coords, _outfalls)

SCHEMA = "flood_management.native_state_delivery.v1"
CELL_M = 250.0            # 落图格网，与全市交付对齐
MODEL_RES_M = 100.0       # 计算网格
OUT_KEY = "AAM_60mm1h_100m"
REGION = "AAM"
#: 交付里的标志文件：`detect` 靠它们认形状
MARK_NOTE = "请先阅读_原生结果说明.txt"
MARK_PARAMS = "配套参数与标识"

#: 态 → (防线, 中文名)；中文名改了要同步 i18n.EN
STATES = {
    "L1": ("line1", "Existing network"),
    "L2": ("line2_current", "Confirmed rain pond (1)"),
}
COLUMNS = {"L1": "line1", "L2": "line2_current"}
PARENT = {"L1": None, "L2": "L1"}

# 与 sim_perf / sim_residual 的 `_rpt_section` 对得上的汇总表标题（保留这些之前的全部内容）
_SERIES_MARK = "<<< Node"


def state_label(state: str) -> str:
    return f"{state} {STATES[state][1]}"


def detect(root: Path) -> bool:
    """是不是这种「原生计算结果」交付：有说明文件、配套参数目录，且 L1/ 下有 *_2D.sww。"""
    return ((root / MARK_NOTE).is_file() and (root / MARK_PARAMS).is_dir()
            and any((root / "L1").glob("*_2D.sww")))


def _one(d: Path, pattern: str) -> Path:
    hits = sorted(p for p in d.glob(pattern) if not p.name.startswith("._"))
    if len(hits) != 1:
        raise SwwError(f"{d} 下 {pattern} 找到 {len(hits)} 个，需恰好一个")
    return hits[0]


# ---------------------------------------------------------------------------
# .rpt 瘦身
# ---------------------------------------------------------------------------


def trim_rpt(src: Path, dst: Path) -> None:
    """只留汇总表：丢掉 `WARNING` 行，遇到逐节点时间序列（`<<< Node …`）就停，末尾补回起止时刻两行。

    起止时刻在原文件的最后（`_RPT_TIME` 按 "Analysis begun/ended on" 抓），所以流式读完整个文件取这两行，
    不能只读前半。标题行前面那一对星号横线属于时间序列章节，一并丢掉。"""
    begun = ended = None
    keep: list[str] = []
    in_series = False
    with src.open(encoding="utf-8", errors="replace") as fh, dst.open("w", encoding="utf-8", newline="\n") as out:
        for line in fh:
            if in_series:
                if "Analysis begun on" in line:
                    begun = line
                elif "Analysis ended on" in line:
                    ended = line
                continue
            if line.startswith("  WARNING"):
                continue
            if _SERIES_MARK in line:
                in_series = True
                # 把已缓冲、属于时间序列章节标题的两三行（星号 + 章节名）丢掉
                while keep and (keep[-1].strip() == "" or set(keep[-1].strip()) <= {"*"}
                                or keep[-1].strip().endswith(("Results", "Result"))):
                    keep.pop()
                out.writelines(keep)
                keep = []
                continue
            keep.append(line)
            if len(keep) > 8:
                out.write(keep.pop(0))
            if "Analysis begun on" in line:
                begun = line
            elif "Analysis ended on" in line:
                ended = line
        if not in_series:
            out.writelines(keep)
        out.write("\n")
        for ln in (begun, ended):
            if ln:
                out.write(ln if ln.endswith("\n") else ln + "\n")
    if not begun or not ended:
        raise SwwError(f"{src.name} 没找到 Analysis begun / ended 行，.rpt 不完整")


# ---------------------------------------------------------------------------
# 从 .sww 聚到 250 m 格
# ---------------------------------------------------------------------------


def _attr(f, name: str) -> float:
    return float(np.asarray(getattr(f, name)))


def _extract_grid(sww: Path, cs: float = CELL_M) -> dict[str, Any]:
    from scipy.io import netcdf_file

    f = netcdf_file(sww, "r", mmap=True)
    try:
        v = f.variables
        xll, yll = _attr(f, "xllcorner"), _attr(f, "yllcorner")
        x = np.array(v["x"][:], np.float64) + xll
        y = np.array(v["y"][:], np.float64) + yll
        vol = np.array(v["volumes"][:], np.int64)
        ec = np.array(v["elevation_c"][:], np.float64)
        times = np.array(v["time"][:], np.float64)
        p = np.stack([x[vol], y[vol]], -1)
        area = 0.5 * np.abs((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
                            - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1]))
        cx, cy = p[:, :, 0].mean(1), p[:, :, 1].mean(1)
        del p
        x0 = np.floor(cx.min() / cs) * cs
        x1 = np.ceil(cx.max() / cs) * cs
        y0 = np.floor(cy.min() / cs) * cs
        ytop = np.ceil(cy.max() / cs) * cs
        nc, nr = int(round((x1 - x0) / cs)), int(round((ytop - y0) / cs))
        col = np.floor((cx - x0) / cs).astype(np.int64)
        row = np.floor((ytop - cy) / cs).astype(np.int64)
        cell = row * nc + col
        n = nr * nc
        cell_area = np.bincount(cell, area, n)
        has = cell_area > 0
        dem = np.full(n, np.nan)
        dem[has] = np.bincount(cell, ec * area, n)[has] / cell_area[has]

        t_min = times / 60
        maxd = np.zeros(n)
        peak_min = np.full(n, np.nan)
        maxsp = np.zeros(n)
        tri_hmax = np.zeros(len(area), np.float32)
        frames: list[tuple[float, np.ndarray]] = []
        wet, hmx, vol_t = [], [], []
        last = None
        for i in range(len(times)):
            st = np.array(v["stage_c"][i], np.float64)
            h = np.maximum(st - ec, 0.0)
            np.maximum(tri_hmax, h.astype(np.float32), out=tri_hmax)
            wet.append(float(area[h >= DELIVERY_WET_M].sum()))
            hmx.append(float(h.max()))
            vol_t.append(float((h * area).sum()))
            dc = np.bincount(cell, h * area, n)
            d = np.zeros(n)
            d[has] = dc[has] / cell_area[has]
            up = d > maxd
            maxd[up] = d[up]
            peak_min[up] = t_min[i]
            mx = np.bincount(cell, np.array(v["xmomentum_c"][i], np.float64) * area, n)
            my = np.bincount(cell, np.array(v["ymomentum_c"][i], np.float64) * area, n)
            w = d >= WET_M
            sp = np.zeros(n)
            sp[w] = np.hypot(mx[w], my[w]) / dc[w]
            np.maximum(maxsp, sp, out=maxsp)
            frames.append((float(times[i]), d.astype(np.float32)))
            last = d
    finally:
        v = None
        try:
            f.close()
        except Exception:      # mmap 引用还在时 scipy 会警告，不影响结果
            pass
    wet_a = np.asarray(wet)
    j = int(np.argmax(wet_a))
    imax = int(np.argmax(tri_hmax))
    return {
        "nr": nr, "nc": nc, "cs": cs, "x0": float(x0), "ytop": float(ytop),
        "bounds": [float(x0), float(y0), float(x1), float(ytop)],
        "n_tri": int(len(area)), "domain_m2": float(area.sum()), "has": has, "dem": dem,
        "maxd": maxd, "peak_min": peak_min, "maxsp": maxsp, "final": last, "frames": frames,
        "end_min": float(t_min[-1]),
        "tri_hmax": float(tri_hmax.max()), "tri_hmax_xy": [float(cx[imax]), float(cy[imax])],
        "tri_wet_max_m2": float(wet_a[j]), "tri_wet_max_min": float(t_min[j]),
        # 各三角形全程最大水深过阈值的面积之和（不同单元可在不同时刻）；交付的「≥0.15 m 全程包络」用得上
        "tri_ever": {thr: float(area[tri_hmax >= thr].sum()) for thr in (0.02, 0.10, 0.15, 0.30, 0.80)},
        "tri_vol_max_m3": max(vol_t), "tri_vol_end_m3": vol_t[-1],
        "series": {"time_minutes": [round(float(t), 3) for t in t_min],
                   "wet_area_m2": [round(a, 1) for a in wet],
                   "max_depth_m": [round(x, 4) for x in hmx],
                   "volume_m3": [round(x, 1) for x in vol_t]},
    }


def _overflow_series(balance: list[dict[str, Any]], t_min: list[float]) -> dict[str, Any]:
    """`water_balance_windows.json`（30 s 一窗）「管网 → 地表」逐窗累加，取各帧时刻的累计值；0 时刻为 0。"""
    ends = np.array([w["end_seconds"] for w in balance], float) / 60
    cum = np.cumsum([w["network_to_surface_m3"] for w in balance])
    pick = [0.0 if t <= 0 else float(cum[min(int(np.searchsorted(ends, t - 1e-6)), len(cum) - 1)]) for t in t_min]
    return {"note": "water_balance_windows.json 逐 30 s 窗的 管网 → 地表 累计量，按帧时刻取值；time_minutes 从降雨起算",
            "time_minutes": [round(t, 3) for t in t_min],
            "cum_m3": [round(c, 1) for c in pick]}


# ---------------------------------------------------------------------------
# 写 v1 形状的交付
# ---------------------------------------------------------------------------


def extract_state(root: Path, state: str, out: Path) -> dict[str, Any]:
    sd = root / state
    sww = _one(sd, "*_2D.sww")
    rpt = _one(sd, "*_1D.rpt")
    inp = _one(sd, "*_1D.inp")
    rec = json.loads((sd / "run_receipt.json").read_text(encoding="utf-8"))
    balance = json.loads((sd / "water_balance_windows.json").read_text(encoding="utf-8"))
    cfg = json.loads((root / MARK_PARAMS / "configuration.json").read_text(encoding="utf-8"))
    rain = cfg["rainfall"]
    depth_mm = float(rain["total_depth_mm"])
    dur_h = float(rain["rain_duration_seconds"]) / 3600

    g = _extract_grid(sww)
    out.mkdir(parents=True, exist_ok=True)
    (out / "temporal_snapshots").mkdir(exist_ok=True)
    nc, cs = g["nc"], g["cs"]
    cell_a = cs * cs

    ids = np.where(g["maxd"] >= WET_M)[0]
    feats = []
    for cid, ring in zip(ids.tolist(), _cell_polys(g, ids)):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "cell_id": cid,
                "depth_m": round(float(g["maxd"][cid]), 4),
                "maximum_depth_m": round(float(g["maxd"][cid]), 4),
                "maximum_depth_time_minutes": round(float(g["peak_min"][cid]), 2),
                "final_depth_m": round(float(g["final"][cid]), 4),
                "maximum_speed_m_s": round(float(g["maxsp"][cid]), 3),
                "model_cell_size_m": cs,
            },
        })
    _dump(out / "maximum_depth_wgs84.geojson",
          {"type": "FeatureCollection", "name": f"{REGION}_{state}_maximum_depth", "features": feats})

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
    _dump(out / "temporal_snapshots" / "manifest.json", {"schema": SCHEMA, "snapshots": snaps})
    _dump(out / "surface_series.json", {
        "schema": SCHEMA, "wet_threshold_m": DELIVERY_WET_M,
        "note": "逐 5 min 帧、按三角单元（.sww）：wet_area_m2 = 水深 ≥ 阈值的三角形面积之和，max_depth_m = 该帧三角形最大水深，"
                "volume_m3 = Σ 水深 × 三角形面积；time_minutes 从降雨起算。地表直接受雨，面积含雨水膜",
        **g["series"],
        "overflow": _overflow_series(balance, g["series"]["time_minutes"]),
    })

    has = g["has"].reshape(g["nr"], nc)
    np.savez_compressed(
        out / "terrain_grid.npz",
        values=np.nan_to_num(g["dem"], nan=0.0).reshape(g["nr"], nc),
        transform=np.array([cs, 0.0, g["x0"], 0.0, -cs, g["ytop"]]),
        land_mask=has, permanent_water_mask=np.zeros_like(has), source=np.array("sww"),
    )

    coords = _node_coords(inp)
    binds = []
    for k, (nid, (xx, yy)) in enumerate(coords.items()):
        c = int(np.floor((xx - g["x0"]) / cs))
        r = int(np.floor((g["ytop"] - yy) / cs))
        if 0 <= r < g["nr"] and 0 <= c < nc:
            binds.append(f'{{"swmm_node_id": {json.dumps(nid)}, "swmm_node_index": {k}, "anuga_cell_index": {r * nc + c}}}')
    (out / "node_cell_binding.json").write_text(
        '{"schema": "' + SCHEMA + '", "note": "节点坐标取耦合 SWMM 的 [COORDINATES]，落到 250 m 格", "bindings": [\n'
        + ",\n".join(binds) + "\n]}\n", encoding="utf-8")
    trim_rpt(rpt, out / "swmm_coupled.rpt")
    shutil.copyfile(inp, out / "coupled_scenario.inp")            # 不用 copy2：不带源文件的权限位
    shutil.copyfile(sd / "run_receipt.json", out / "run_receipt.json")
    shutil.copyfile(sd / "water_balance_windows.json", out / "water_balance_windows.json")

    outf = _outfalls(inp)
    _dump(out / "scenario.json", {
        "scope": REGION,
        "spatial_pattern": "uniform",
        "rainfall_profile": {"profile_id": f"R{depth_mm:g}D{dur_h:g}",
                             "provenance": {"temporal_assumption": f"uniform {rain['rate_mm_per_hour']:g} mm/h for {dur_h:g} h"}},
        "outfall_mode": "free",
        "outfall_level_m": None,
        "outfalls": [{"name": o[0], "type": o[1], "stage_m": o[2]} for o in outf],
        "start_time": None,
    })

    pond = cfg.get("pond") or {}
    quality = bool(rec.get("quality_passed"))
    summary = {
        "schema": SCHEMA,
        "run_id": f"{OUT_KEY}/{state}",
        "scenario_label": f"{REGION} · {depth_mm:g} mm / {dur_h:g} h · {state_label(state)}",
        "state": state,
        "status": "completed",
        "solver": "ANUGA 2D + SWMM 5.2.4",
        "claim_boundary": (
            f"{state_label(state)}：阿尔艾因全行政区（69 个区的并集）{g['n_tri']:,} 个三角单元（100 m 方格各切两个），"
            f"SWMM 与 ANUGA 每 {rec['coupling_seconds']} s 双向耦合，演算 0 → {g['end_min'] / 60:g} h；"
            f"{rain['rate_mm_per_hour']:g} mm/h 均匀降雨只下在二维地表，无入渗、无蒸发、无外部入流、无泵站。"
            + ("交付自评质量未通过（" if not quality else "交付自评质量通过（")
            + f"SWMM 连续性误差 {rec['swmm_routing_error_percent']:.2f} %、{rec['swmm_nonconverging_steps_percent']:.2f} % 的步未收敛），"
              "不能当作物理精度已验证；未标定、未经工程认可。"
            + "页面格网是三角单元按面积平均到 250 m，格内最深处被抹平；最大水深 / 积水面积按三角单元统计，面积含雨水膜。"
            + (f"塘（{pond.get('capacity_m3', 0):,.0f} m³，初始为空）只靠管道进水，本次全程未蓄到水，二维结果（.sww）与 L1 逐字节相同。" if state == "L2" else "")
        ),
        "source": {"delivery": str(root.relative_to(DATA_ROOT)) if DATA_ROOT in root.parents else str(root),
                   "state_dir": sd.name, "native_sww": sww.name, "native_out": rec.get("native_1d_binary"),
                   "native_inp_sha256": rec.get("native_inp_sha256"), "grid_sha256": rec.get("grid_sha256"),
                   "quality_passed": quality},
        "domain": {
            "bounds_epsg32640": g["bounds"],
            "area_m2": round(g["domain_m2"], 1),
            "cell_size_m": cs,
            "rectangular_cells": g["nr"] * nc,
            "active_land_cells": int(g["has"].sum()),
            "triangle_count": g["n_tri"],
            "coupling_start_minutes": 0.0,
            "simulation_duration_minutes": g["end_min"],
            "simulation_duration_hours": g["end_min"] / 60,
            "output_step_minutes": FRAME_STEP_S / 60,
        },
        "results": {
            "maximum_depth_m": round(g["tri_hmax"], 4),
            "maximum_depth_xy_epsg32640": g["tri_hmax_xy"],
            "peak_inundated_area_m2": round(g["tri_wet_max_m2"], 1),
            "peak_inundated_area_minutes": g["tri_wet_max_min"],
            "peak_inundated_threshold_m": DELIVERY_WET_M,
            "maximum_depth_cell_m": round(float(g["maxd"].max()), 4),
            "maximum_depth_triangle_m": round(g["tri_hmax"], 4),
            "maximum_speed_cell_m_s": round(float(g["maxsp"].max()), 3),
            "inundated_cells_ge_0_01m": int(len(ids)),
            "inundated_area_ge_0_01m2": float(len(ids) * cell_a),
            "inundated_area_ge_0_05m2": float((g["maxd"] >= 0.05).sum() * cell_a),
            "final_inundated_cell_count_ge_0_01m": int((g["final"] >= WET_M).sum()),
            "final_inundated_area_ge_0_01m2": float((g["final"] >= WET_M).sum() * cell_a),
            # 交付口径，只为与 run_receipt 对账（其「≥0.15 m 全程包络 2052.77 km²」）
            "delivery_ever_wet_area_ge_0_15m2": round(g["tri_ever"][0.15], 1),
            "delivery_receipt_envelope_ge_0_15m_km2": rec.get("maximum_depth_envelope_area_ge_0_15m_km2"),
            "delivery_receipt_maximum_depth_m": rec.get("maximum_depth_m"),
            "delivery_receipt_maximum_velocity_mps": rec.get("maximum_velocity_mps"),
            "peak_surface_volume_m3": g["tri_vol_max_m3"],
            "final_surface_volume_m3": g["tri_vol_end_m3"],
            "minimum_published_depth_m": WET_M,
            "pond_final_volume_m3": rec.get("pond_final_volume_m3"),
        },
        "coupling": {
            "coupling_mode": "bidirectional",
            "coupled_node_count": rec.get("native_node_count"),
            "exchange_step_seconds": rec.get("coupling_seconds"),
            "total_swmm_to_anuga_m3": rec.get("network_to_surface_volume_m3"),
            "total_anuga_to_swmm_m3": rec.get("surface_to_network_volume_m3"),
            "boundary_outflow_m3": -float(rec.get("boundary_net_inflow_m3") or 0.0),
        },
        "quality": {
            "quality_passed": quality,
            "swmm_routing_error_percent": rec.get("swmm_routing_error_percent"),
            "swmm_nonconverging_steps_percent": rec.get("swmm_nonconverging_steps_percent"),
            "whole_system_residual_percent_of_rain": rec.get("whole_system_residual_percent_of_rain"),
            "surface_failed_windows": rec.get("surface_failed_windows"),
        },
        "model_configuration": {
            "coupling_mode": "bidirectional",
            "return_period_years": None,
            "rainfall_total_mm": depth_mm,
            "rainfall_duration_minutes": dur_h * 60,
            "exchange_quantity": "node overflow to surface and surface capture into inlets (bidirectional)",
            "dynamic_head_feedback": True,
            "simulation_duration_minutes": g["end_min"],
            "output_interval_minutes": FRAME_STEP_S / 60,
            "model_cell_size_m": MODEL_RES_M,
            "terrain_product": "customer 5 m DTM area-mean to 100 m mesh",
            "surface_rainfall_inflow_m3": rec.get("rainfall_volume_m3"),
        },
        "outputs": {
            "maximum_depth": "maximum_depth_wgs84.geojson",
            "timeline_manifest": "temporal_snapshots/manifest.json",
            "swmm_report": "swmm_coupled.rpt",
            "swmm_input": "coupled_scenario.inp",
            "coupling_receipt": "node_cell_binding.json",
            "surface_series": "surface_series.json",
        },
    }
    _dump(out / "delivery_summary.json", summary)
    print(f"  {state}：{len(ids):,} 格 ≥ {WET_M} m｜最大水深 {g['tri_hmax']:.2f} m（交付 {rec.get('maximum_depth_m'):.2f}）｜"
          f"同刻 ≥ 0.02 m 最大 {g['tri_wet_max_m2'] / 1e6:,.0f} km² @ {g['tri_wet_max_min']:g} min｜"
          f"≥0.15 m 全程 {g['tri_ever'][0.15] / 1e6:,.1f} km²（交付 {rec.get('maximum_depth_envelope_area_ge_0_15m_km2')}）")
    return summary


def extract(root: Path, only: list[str] | None = None) -> Path:
    root = root.resolve()
    if not detect(root):
        raise SwwError(f"{root} 不是「原生计算结果」交付（要有 {MARK_NOTE}、{MARK_PARAMS}/、L1/*_2D.sww）")
    base = OUT_ROOT / OUT_KEY
    for state in STATES:
        if only and state not in only:
            continue
        extract_state(root, state, base / state)
    for name in ("原生文件完整性与质量.json", MARK_NOTE):
        if (root / name).is_file():
            shutil.copyfile(root / name, base / name)
    present = [st for st in STATES if (base / st / "delivery_summary.json").is_file()]
    _dump(base / "states.json", {
        "schema": "flood_management.sww_states.v1",
        "delivery": root.name,
        "default": "L1" if "L1" in present else present[0],
        "states": [{"key": st, "label": state_label(st), "defence_line_key": STATES[st][0],
                    "column": COLUMNS[st], "dir": st,
                    "parent": PARENT[st] if PARENT[st] in present else None,
                    "same_as": None} for st in present],
    })
    return base


def register(base: Path, by: str | None = None) -> int:
    """把已抽好的 `sim_deliveries/<键>/` 登记成一个多态任务（按 output_dir 去重，重复执行只更新）。不需要交付原件。"""
    from . import sim_results
    from . import simulation as sim

    base = base.resolve()
    states = json.loads((base / "states.json").read_text(encoding="utf-8"))["states"]
    keys = [s["key"] for s in states]
    top = max((STATES[k][0] for k in keys), key=sim.LINE_ORDER.index)
    with sim.connect() as conn:
        return sim_results.import_delivery(conn, base, by, line_key=top, extent=REGION,
                                           variant_label=f"{len(keys)} states: {' / '.join(keys)}")


def import_all(root: Path, by: str | None = None, only: list[str] | None = None) -> int:
    return register(extract(root, only), by)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, hlp in (("extract", "只从 .sww / .rpt 抽出交付形状"), ("import", "抽取并登记为仿真任务")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("path", type=Path)
        p.add_argument("--state", action="append", choices=list(STATES), help="只处理这几态（可重复）")
        if name == "import":
            p.add_argument("--by", help="操作人缩写，如 ZN")
    p = sub.add_parser("register", help="只登记已抽好的结果（服务器上用，不需要交付原件）")
    p.add_argument("path", type=Path, help="sim_deliveries 下的目录，或其目录名（如 AAM_60mm1h_100m）")
    p.add_argument("--by", help="操作人缩写，如 ZN")
    a = ap.parse_args()
    if a.cmd == "extract":
        extract(a.path, a.state)
        return
    if a.cmd == "register":
        base = a.path if a.path.is_dir() else OUT_ROOT / a.path
        print(f"已登记为仿真 #{register(base, a.by)}")
        return
    try:
        print(f"已登记为仿真 #{import_all(a.path, a.by, a.state)}")
    except SwwError as e:
        raise SystemExit(f"✗ {e}") from e


if __name__ == "__main__":
    main()
