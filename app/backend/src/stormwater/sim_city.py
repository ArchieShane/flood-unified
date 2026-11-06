"""把「全市一二维 L1 / L2」两态交付抽成仿真系统认的交付形状，再登记成一个多态仿真任务。

交付是 `data/temp260928/全市_61mm2h/`（2026-09-28，算力侧「防线 2+ → 全市一二维」，在线报告
https://claude.ai/artifact/NjMBR3ueF7RBUGYQJGm9bY ）：

    全市_61mm2h/
        readme.md  计算设置.md                    两态口径（一维 8 片 / 一二维整网耦合）
        L1_既有管网/一二维/L1_二维水深帧与最大水深.npz   6,322,454 个三角单元：xy / area / elev / hmax / frames(72) / t_h / rain_mask
        L1_既有管网/一二维/L1_耦合运行记录.json         逐 5 min 水量、1D↔2D 交换累计、直接落地的雨、塘蓄水
        L1_既有管网/一二维/耦合的一维/*.rpt|inp          与二维同进程耦合的整网 SWMM（ALLOW_PONDING NO）
        L2_加22座已建成塘/…                             同 L1 的结构，另有 塘蓄水.csv
        对比_缺口分析/全市L1L2统计值.csv                43 项统计值及口径（本模块的数按它对账）

和 Mussafah（`sim_sww.py`）不同的地方，也是不能直接复用它的原因：

- 网格是**非结构**三角网（平均 195 m²，地形有效分辨率 30 m），不是 15 m 规则网格切的，`sim_sww._extract_grid`
  的「每格恰好 4 个三角形」判据过不了；.sww（8 GB）是事后由 npz 重建的、没有流速，本模块直接读 npz。
- 全市 1,234 km²，逐三角单元出 GeoJSON 页面画不动。落图格网取 **250 m**，原点对齐 250 m 整数倍，
  与全市 #100 / #101 / #102 的格子逐格重合；格内水深 = 格内三角单元水深按面积平均（与 `sim_sww` 同口径）。
  格平均会把最深处抹平（L1 250 m 格最大 0.94 m，三角单元 3.82 m），所以页面卡片的数不取格网：
- 最大水深 = npz `hmax`（全部三角单元全程最大）的最大值；最大积水面积 = **同一时刻**三角单元水深 ≥ 0.02 m
  （交付的干湿阈值）面积最大的那一帧；逐帧三条时序（面积 / 最大水深 / 地表水量）写 `surface_series.json`，
  按 72 帧 npz 逐帧算（帧是 float16，与耦合记录的 wet_m2 对账差 < 0.1%）。
  注意全市地表**直接受雨**（离最近井 > 46 m 的 872 km² 上 CN 82 净雨 23.5 mm），≥ 0.02 m 的面积里有雨水膜；
  交付自己报的「同一时刻最大面积」用 ≥ 0.10 m（L1 8,438.95 ha），也一并写进 `results` 备查。
- 时间：二维从 0 h（降雨开始）起算，没有 Mussafah 那样的 start_h 偏移；帧 t = 5 … 360 min。
- 流速：交付没有（.sww 的动量全为 0），格网不写 `maximum_speed_m_s`，`sim_ponding` 照记 `no_speed`。
- 地形：`terrain_grid.npz` 由三角单元 `elev` 按面积平均到 250 m 格，带 `source = "sww"`（取自模型网格，
  不是算力侧的 terrain_grid），`sim_ponding` 照记 `terrain_from_sww`。计算域外的格子没有三角单元，当出流边界。
- 管网归因：节点坐标取耦合 SWMM 的 [COORDINATES] 落到 250 m 格，`sim_ponding` 走 `swmm_from_rpt` 降级。
- 耦合 SWMM 的 .rpt / .inp 各一百多 / 六十 MB，硬链到输出目录（同卷零拷贝），不另拷。

## 两态、一个任务

L1（既有管网，第一道）→ L2（同一张网加 22 座已建成塘，第二道现状），L2 的 `parent` 是 L1。
和 Mussafah 的 L2 不同，这里的 L2 是**真算过**的（不是 ≡ L1 的硬链），`same_as` 为空。
任务记最高一道（第二道现状），操作人按用户要求记 AR（`simulation.PROTECTED_OPERATORS`，不能删）。

用法：

    PYTHONPATH=src .venv/bin/python -m stormwater.sim_city import data/temp260928/全市_61mm2h --by AR
    PYTHONPATH=src .venv/bin/python -m stormwater.sim_city extract data/temp260928/全市_61mm2h   # 只抽不登记
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from .sim_sww import (DATA_ROOT, DELIVERY_WET_M, OUT_ROOT, WET_M, SwwError, _cell_polys, _dump, _node_coords,
                      _one, _outfalls, _parse_name)

SCHEMA = "flood_management.city_state_delivery.v1"
CELL_M = 250.0            # 落图格网，与全市 #100–102 对齐
MODEL_RES_M = 30.0        # 地形有效分辨率（计算设置.md：5 m DTM 先平均到 30 m 再赋给三角单元）

#: 态 → (子目录前缀, 防线, 中文名)；中文名改了要同步 i18n.EN
STATES = {
    "L1": ("L1_", "line1", "Existing network"),
    "L2": ("L2_", "line2_current", "Built ponds (22)"),
}
COLUMNS = {"L1": "line1", "L2": "line2_current"}
PARENT = {"L1": None, "L2": "L1"}

#: 交付布局。zh = 2026-09-28 全市交付（中文目录、250 m 落图格、6.3M 单元）；
#: adm = 2026-10-26 阿布扎比全市「改造后数据」（英文目录 `1D/` + `2D/`、100 m 模型 56.9 万单元、L2 = L1 + 170 塘 / 渗井）。
#: 两者的 npz 同形（xy / area / elev / hmax / frames / t_h / rain_mask / uh / vh）。
LAYOUTS: dict[str, dict[str, Any]] = {
    "zh": {
        "cell_m": CELL_M, "model_res_m": MODEL_RES_M,
        "labels": {"L1": "Existing network", "L2": "Built ponds (22)"},
        "terrain": "customer 5 m DTM averaged to 30 m, unstructured mesh (mean 195 m²)",
        "report_url": "https://claude.ai/artifact/NjMBR3ueF7RBUGYQJGm9bY",
    },
    "adm": {
        "cell_m": 100.0, "model_res_m": 100.0,
        "labels": {"L1": "Existing facilities", "L2": "Ponds + soakaways (170)"},
        "terrain": "DTM averaged to 100 m blocks, unstructured mesh (about 569k cells)",
        "report_url": None,
    },
}


def engine_meta(root: Path) -> dict | None:
    """engine（tools/run_region.py）写在交付根上的 outputs.json：有它就按它显示区域 / 网格 / 雨型 / 地形等标签，不用 ADM 全市交付写死的那套。"""
    p = root / "outputs.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (OSError, ValueError):
        return None


def layout_of(root: Path) -> str:
    """`adm`：态目录下有 `1D/` 与 `2D/*_2d.npz`；否则按中文布局（`一二维/`）。"""
    for sd in sorted(p for p in root.iterdir() if p.is_dir() and re.match(r"L\d", p.name)):
        if (sd / "1D").is_dir() and any(not p.name.startswith("._") for p in (sd / "2D").glob("*_2d.npz")):
            return "adm"
    return "zh"


def state_label(state: str, layout: str = "zh") -> str:
    return f"{state} {LAYOUTS[layout]['labels'][state]}"


def _state_dir(root: Path, state: str) -> Path | None:
    hits = [p for p in root.iterdir() if p.is_dir() and p.name.startswith(STATES[state][0])]
    return hits[0] if len(hits) == 1 else None


def _link(src: Path, dst: Path) -> None:
    """把大文件（.rpt / .inp）放进抽取结果：同卷且源文件对所有人可读才硬链（零拷贝）；否则拷贝，且**不继承源文件的权限位**。
    NAS 上的文件常是 root:<组> 660，`copy2` 会把 660 带过来，API 进程（容器里的普通用户）就读不了，页面的储水设施 / 效果分析报 500。"""
    if dst.exists():
        dst.unlink()
    try:
        if src.stat().st_mode & 0o004:
            os.link(src, dst)
            return
    except OSError:
        pass
    shutil.copyfile(src, dst)       # 不带权限位：按进程 umask 落成 644


def _stats(root: Path) -> dict[str, dict[str, str]]:
    """全市L1L2统计值.csv（adm 布局为 statistics/summary.csv，列 metric）：指标 → 行（L1 / L2 两列为交付原值字符串）。"""
    p = root / "对比_缺口分析" / "全市L1L2统计值.csv"
    key = "指标"
    if not p.is_file():
        p, key = root / "statistics" / "summary.csv", "metric"
    if not p.is_file():
        return {}
    with p.open(encoding="utf-8-sig", newline="") as f:
        return {r[key]: r for r in csv.DictReader(f)}


# ---------------------------------------------------------------------------
# 从 npz 聚到 250 m 格
# ---------------------------------------------------------------------------


def _row_blocks(npz: Path, key: str, n_rows: int, budget_bytes: int = 192 * 2**20):
    """逐块读 npz 里一个 (行, 列) 数组：产出 (起始行, 块)。块大小按字节预算折成行数，至少 1 行。
    np.load 取 npz 的键会把整个数组解到内存；这里直接开 zip 成员、读 .npy 头后按行流式读（压缩与否都行）。
    读不了（Fortran 序 / 非二维 / 头版本不认）就退回整块读再切，行为相同、只是吃内存。"""
    import zipfile

    with zipfile.ZipFile(npz) as zf, zf.open(f"{key}.npy") as fh:
        ver = np.lib.format.read_magic(fh)
        hdr = {(1, 0): np.lib.format.read_array_header_1_0, (2, 0): np.lib.format.read_array_header_2_0}.get(ver)
        shape, fortran, dtype = hdr(fh) if hdr else ((), True, None)
        if hdr and not fortran and len(shape) == 2 and shape[0] == n_rows and not dtype.hasobject:
            row_bytes = int(shape[1]) * dtype.itemsize
            step = max(1, budget_bytes // max(row_bytes, 1))
            for i in range(0, n_rows, step):
                k = min(step, n_rows - i)
                buf = fh.read(k * row_bytes)
                if len(buf) != k * row_bytes:
                    raise ValueError(f"{npz.name}:{key} 提前结束（要 {k * row_bytes} 字节，读到 {len(buf)}）")
                yield i, np.frombuffer(buf, dtype).reshape(k, int(shape[1]))
            return
    arr = np.load(npz)[key]
    step = max(1, budget_bytes // max(arr[0].nbytes, 1))
    for i in range(0, n_rows, step):
        yield i, arr[i:i + step]


def _extract_grid(npz: Path, cs: float = CELL_M) -> dict[str, Any]:
    """三角单元聚到 cs 米落图格（原点对齐 cs 的整数倍）。"""
    z = np.load(npz)
    xy = z["xy"].astype(float)
    area = z["area"].astype(float)
    elev = z["elev"].astype(float)
    hmax_tri = z["hmax"].astype(float)
    t_min = np.asarray(z["t_h"], float) * 60
    rain_frac = float(z["rain_mask"].mean()) if "rain_mask" in z.files else None
    x0 = np.floor(xy[:, 0].min() / cs) * cs
    ytop = np.ceil(xy[:, 1].max() / cs) * cs
    x1 = np.ceil(xy[:, 0].max() / cs) * cs
    y0 = np.floor(xy[:, 1].min() / cs) * cs
    nc, nr = int(round((x1 - x0) / cs)), int(round((ytop - y0) / cs))
    col = np.floor((xy[:, 0] - x0) / cs).astype(np.int64)
    row = np.floor((ytop - xy[:, 1]) / cs).astype(np.int64)
    cell = row * nc + col
    n = nr * nc
    cell_area = np.bincount(cell, area, n)
    has = cell_area > 0
    dem = np.full(n, np.nan)
    dem[has] = np.bincount(cell, elev * area, n)[has] / cell_area[has]

    maxd = np.zeros(n)
    peak_min = np.full(n, np.nan)
    frames: list[tuple[float, np.ndarray]] = []
    wet, hmx, vol = [], [], []
    last = None
    # 有单宽流量（C1 的 npz 带 uh / vh）就顺带出格内最大流速，口径同 sim_sww：Σ(动量×面积) / Σ(水深×面积)
    has_speed = "uh" in z.files and "vh" in z.files
    maxsp = np.zeros(n) if has_speed else None
    # frames / uh / vh 是 (帧数, 三角单元数) 的 float16：5 m 的 24 h 雨约 336 × 800 万 × 2 B ≈ 5 GB 一个，
    # 绝不整块读，逐块（每块几帧）从 zip 里流式读，内存只随块大小走
    n_fr = len(t_min)
    blocks = _row_blocks(npz, "frames", n_fr)
    if has_speed:
        blocks = zip(blocks, _row_blocks(npz, "uh", n_fr), _row_blocks(npz, "vh", n_fr))
    for blk in blocks:
        F, UH, VH = (blk[0][1], blk[1][1], blk[2][1]) if has_speed else (blk[1], None, None)
        i0 = blk[0][0] if has_speed else blk[0]
        for k in range(F.shape[0]):
            i = i0 + k
            h = F[k].astype(np.float32)
            wet.append(float(area[h >= DELIVERY_WET_M].sum()))
            hmx.append(float(h.max()))
            vol.append(float((h * area).sum()))
            dc = np.bincount(cell, h * area, n)
            d = np.zeros(n)
            d[has] = dc[has] / cell_area[has]
            up = d > maxd
            maxd[up] = d[up]
            peak_min[up] = t_min[i]
            if maxsp is not None:
                mx = np.bincount(cell, UH[k].astype(np.float32) * area, n)
                my = np.bincount(cell, VH[k].astype(np.float32) * area, n)
                w = d >= WET_M
                sp = np.zeros(n)
                sp[w] = np.hypot(mx[w], my[w]) / dc[w]
                np.maximum(maxsp, sp, out=maxsp)
            frames.append((float(t_min[i]) * 60, d.astype(np.float32)))
            last = d
    wet_a = np.asarray(wet)
    j = int(np.argmax(wet_a))
    return {
        "nr": nr, "nc": nc, "cs": cs, "x0": float(x0), "ytop": float(ytop),
        "bounds": [float(x0), float(y0), float(x1), float(ytop)],
        "n_tri": int(len(area)), "domain_m2": float(area.sum()), "has": has, "dem": dem,
        "maxd": maxd, "peak_min": peak_min, "maxsp": maxsp, "final": last, "frames": frames, "end_min": float(t_min[-1]),
        "tri_hmax": float(hmax_tri.max()),
        "tri_hmax_xy": [float(v) for v in xy[int(np.argmax(hmax_tri))]],
        "tri_wet_max_m2": float(wet_a[j]), "tri_wet_max_min": float(t_min[j]),
        # 交付「全程最大」三档：各单元全程最大水深过阈值的面积之和（不同单元可在不同时刻）
        "tri_ever": {thr: float(area[hmax_tri >= thr].sum()) for thr in (0.02, 0.10, 0.30, 0.80)},
        "tri_vol_max_m3": max(vol), "tri_vol_end_m3": vol[-1],
        "rain_mask_fraction": rain_frac,
        "series": {"time_minutes": [round(float(t), 3) for t in t_min],
                   "wet_area_m2": [round(a, 1) for a in wet],
                   "max_depth_m": [round(x, 4) for x in hmx],
                   "volume_m3": [round(x, 1) for x in vol]},
    }


def _overflow_series(rec: dict[str, Any]) -> dict[str, Any]:
    ser = rec.get("series") or []
    return {"note": "耦合运行记录逐 5 min 的 SWMM → 地表累计溢出量；time_minutes 从降雨起算",
            "time_minutes": [round(float(r["t_h"]) * 60, 3) for r in ser],
            "cum_m3": [round(float(r["acc_1to2"]), 1) for r in ser]}


# ---------------------------------------------------------------------------
# 写 v1 形状的交付
# ---------------------------------------------------------------------------


def extract_state(root: Path, state: str, out: Path) -> dict[str, Any]:
    region, depth_mm, dur_h = _parse_name(root)
    sd = _state_dir(root, state)
    if sd is None:
        raise SwwError(f"{root} 下没有 {state} 的目录")
    lay = layout_of(root)
    L = LAYOUTS[lay]
    E = engine_meta(root)
    if E:       # engine 推演：网格、地形、状态名按这次的实际值
        L = {**L, "cell_m": float(E["res_m"]), "model_res_m": float(E["res_m"]), "terrain": E["terrain"],
             "labels": {**L["labels"], "L1": "Existing facilities (permanent)"}}
    if lay == "adm":
        d2, d1 = sd / "2D", sd / "1D"
        npz = _one(d2, "*_2d.npz")
        rec = json.loads(_one(d2, "*_coupled_run_record.json").read_text(encoding="utf-8"))
    else:
        d2 = sd / "一二维"
        d1 = d2 / "耦合的一维"
        npz = _one(d2, "*_二维水深帧与最大水深.npz")
        rec = json.loads(_one(d2, "*_耦合运行记录.json").read_text(encoding="utf-8"))
    rpt = _one(d1, "*.rpt")
    inp = _one(d1, "*.inp")

    g = _extract_grid(npz, L["cell_m"])
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
    _dump(out / "temporal_snapshots" / "manifest.json", {"schema": SCHEMA, "snapshots": snaps})
    _dump(out / "surface_series.json", {
        "schema": SCHEMA, "wet_threshold_m": DELIVERY_WET_M,
        "note": "逐 5 min 帧、按三角单元（npz）：wet_area_m2 = 水深 ≥ 阈值的单元面积之和，max_depth_m = 该帧单元最大水深，"
                "volume_m3 = Σ 水深 × 单元面积；time_minutes 从降雨起算。全市地表直接受雨，面积含雨水膜",        **g["series"],
        "overflow": _overflow_series(rec),
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
        '{"schema": "' + SCHEMA + f'", "note": "节点坐标取耦合 SWMM 的 [COORDINATES]，落到 {cs:g} m 格", "bindings": [\n'
        + ",\n".join(binds) + "\n]}\n", encoding="utf-8")
    _link(rpt, out / "swmm_coupled.rpt")
    _link(inp, out / "coupled_scenario.inp")
    ponds = d2 / "塘蓄水.csv"
    if ponds.is_file():
        shutil.copyfile(ponds, out / "pond_storage.csv")

    outf = _outfalls(inp)
    _dump(out / "scenario.json", {
        "scope": "citywide",
        # 有管网覆盖的地面按客户汇水区 CN / Tc 产流注入一维；离井 > 46 m 的地面净雨直接下在二维上
        "spatial_pattern": (E["spatial_pattern"] if E else "Service-area injection at nodes + direct rain on 2D" if lay == "adm"
                            else "Client catchments + direct rain on 2D"),
        "rainfall_profile": {"profile_id": E["tag"] if E else f"R{depth_mm:g}D{dur_h:g}",
                             "provenance": {"temporal_assumption": E["rain_shape_label"] if E else "client Mussafah 1 h design storm shape" if lay == "adm"
                                            else "client 2 h design storm"}},
        "outfall_mode": "fixed" if E else "free",
        "outfall_level_m": E["outfall_level_m"] if E else None,
        "outfalls": [{"name": o[0], "type": o[1], "stage_m": o[2]} for o in outf],
        "start_time": None,
    })

    acc = rec.get("acc_m3") or {}
    st = _stats(root)

    def stat(name: str) -> float | None:
        try:
            return float(st[name][state])
        except (KeyError, TypeError, ValueError):
            return None

    summary = {
        "schema": SCHEMA,
        "run_id": f"{root.name}/{state}",
        "scenario_label": f"{region} · {depth_mm:g} mm / {dur_h:g} h · {state_label(state, lay)}",
        "state": state,
        "status": "completed",
        "solver": "ANUGA 2D + SWMM 5.2.4",
        "claim_boundary": (
            (
                f"{state_label(state, lay)}：{E['title']} 区域一二维耦合：SWMM 5.2 + ANUGA 双向耦合（60 s 交换），非结构三角网 {g['n_tri']:,} 个单元（名义 {E['res_m']:g} m），"
                f"{E['terrain']}；管网初始水量 25 %、出水口固定水位 {E['outfall_level_m']:g} m 并带拍门、地面糙率按下垫面分区；产流为子汇水区 SCS CN + 单位线，不在二维上直接下雨；"
                "未标定、未经工程认可。L1 = 永久设施（数据里没有明确临时 / Planned 标注的一律 L1）。"
            ) if E else (
                f"{state_label(state, lay)}：阿布扎比全市整网 SWMM（改造后管网数据）与 ANUGA 双向耦合（60 s 交换），"
                f"非结构三角网 {g['n_tri']:,} 个单元（约 100 m）、地形为 DTM 平均到 100 m，小尺度起伏（窄沟、路缘、小洼地）被抹平，"
                f"演算 0 → {g['end_min'] / 60:g} h；产流为全市统一 SCS CN 82、Tc 30 min（数据集无汇水区图层），"
                "离井 > 44.7 m 的地面直接受雨。糙率全域 0.030 未分下垫面；交付方未标定、未经工程认可。"
                f"页面格网是三角单元按面积平均到 {cs:g} m；最大水深 / 积水面积按三角单元统计。"
                + ("塘 / 渗井共 170 个（148 塘 + 22 渗井，其中 145 个库容或深度为估算），渗井无下渗；"
                   "一维管网与泵站同 L1。" if state == "L2" else "已建泵站 38 组（有流量数据者），无塘、无渗井。")
            ) if lay == "adm" else (
                f"{state_label(state)}：全市整网 SWMM 与 ANUGA 双向耦合（60 s 交换），非结构三角网 "
                f"{g['n_tri']:,} 个单元、地形有效分辨率 30 m，演算 0 → {g['end_min'] / 60:g} h；"
                "离井 > 46 m 的地面直接受雨（CN 82）。糙率全域 0.030 未分下垫面；交付方未标定、未经工程认可。"
                "页面格网是三角单元按面积平均到 250 m，格内最深处被抹平；最大水深 / 积水面积按三角单元统计。"
                + ("塘按深 0.30 m 的平底蓄水节点建模（库容对、塘内水位与顶托不真实），接塘管按位置与标高推断；"
                   "一维只有 17 座塘有进水管，一二维 22 座都在。" if state == "L2" else "")
            )
        ),
        "source": {"delivery": str(root.relative_to(DATA_ROOT)) if DATA_ROOT in root.parents else str(root),
                   "state_dir": sd.name, "native_npz": npz.name, "coupling_record": rec.get("tag"),
                   "report_url": L["report_url"]},
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
            "output_step_minutes": 5.0,
        },
        "results": {
            "maximum_depth_m": round(g["tri_hmax"], 4),
            "maximum_depth_xy_epsg32640": g["tri_hmax_xy"],
            "peak_inundated_area_m2": round(g["tri_wet_max_m2"], 1),
            "peak_inundated_area_minutes": g["tri_wet_max_min"],
            "peak_inundated_threshold_m": DELIVERY_WET_M,
            "maximum_depth_cell_m": round(float(g["maxd"].max()), 4),
            "maximum_depth_triangle_m": round(g["tri_hmax"], 4),
            "inundated_cells_ge_0_01m": int(len(ids)),
            "inundated_area_ge_0_01m2": float(len(ids) * cell_a),
            "inundated_area_ge_0_05m2": float((g["maxd"] >= 0.05).sum() * cell_a),
            "final_inundated_cell_count_ge_0_01m": int((g["final"] >= WET_M).sum()),
            "final_inundated_area_ge_0_01m2": float((g["final"] >= WET_M).sum() * cell_a),
            # 交付口径（三角单元），只为与「全市L1L2统计值」对账，页面卡片不用
            "delivery_ever_wet_area_ge_0_02m2": round(g["tri_ever"][0.02], 1),
            "delivery_max_area_ge_0_10m2": round(g["tri_ever"][0.10], 1),
            "delivery_max_area_ge_0_30m2": round(g["tri_ever"][0.30], 1),
            "delivery_max_area_ge_0_80m2": round(g["tri_ever"][0.80], 1),
            "delivery_same_time_area_ge_0_10m2": (stat("flooded area >=0.10 m (ha)" if lay == "adm"
                                                       else "积水 ≥0.10 m 同一时刻最大面积") or 0) * 1e4 or None,
            "delivery_wet_record_max_m2": rec.get("max_wet_m2"),
            "peak_surface_volume_m3": g["tri_vol_max_m3"],
            "final_surface_volume_m3": g["tri_vol_end_m3"],
            "minimum_published_depth_m": WET_M,
            "pond_inflow_m3": acc.get("pond"),
            "pond_stored_end_m3": (round(sum(p.get("vol_end_m3") or 0 for p in rec.get("ponds") or []), 1) or None
                                   if lay == "adm" else stat("塘蓄住的水（6 h 末）")),
        },
        "coupling": {
            "coupling_mode": "bidirectional",
            "coupled_node_count": rec.get("n_nodes"),
            "exchange_step_seconds": rec.get("dt_c"),
            "total_swmm_to_anuga_m3": acc.get("1to2"),
            "total_anuga_to_swmm_m3": acc.get("2to1"),
            "boundary_outflow_m3": rec.get("boundary_out_m3"),
        },
        "model_configuration": {
            "coupling_mode": "bidirectional",
            "return_period_years": None,
            "rainfall_total_mm": depth_mm,
            "rainfall_duration_minutes": dur_h * 60,
            "exchange_quantity": "node overflow to surface and surface return to node (bidirectional)",
            "dynamic_head_feedback": True,
            "simulation_duration_minutes": g["end_min"],
            "output_interval_minutes": 5.0,
            "model_cell_size_m": L["model_res_m"],
            "terrain_product": L["terrain"],
            # 直接落在二维地面的净雨（耦合记录 acc_m3.rain）：> 0，sim_ponding 不亮 no_surface_rain
            "surface_rainfall_inflow_m3": acc.get("rain"),
            "surface_rain_cell_fraction": g["rain_mask_fraction"],
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
    print(f"  {state}：{len(ids):,} 格 ≥ {WET_M} m｜最大水深 {g['tri_hmax']:.2f} m｜"
          f"同刻 ≥ 0.02 m 最大 {g['tri_wet_max_m2'] / 1e4:,.0f} ha @ {g['tri_wet_max_min']:g} min"
          f"（记录 {(rec.get('max_wet_m2') or 0) / 1e4:,.0f} ha）｜≥0.30 m 全程 {g['tri_ever'][0.30] / 1e4:,.2f} ha")
    return summary


def extract(root: Path, only: list[str] | None = None) -> Path:
    root = root.resolve()
    base = OUT_ROOT / root.name
    for state in STATES:
        if only and state not in only:
            continue
        if not only and _state_dir(root, state) is None:
            continue            # 只有部分态的交付（如 engine 的 L1 单态推演）：没有的态不抽取，不报错
        extract_state(root, state, base / state)
    src = root / "对比_缺口分析" / "全市L1L2统计值.csv"
    if src.is_file():
        (base / "analysis").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, base / "analysis" / "state_stats.csv")
    lay = layout_of(root)
    present = [st for st in STATES if (base / st / "delivery_summary.json").is_file()]
    _dump(base / "states.json", {
        "schema": "flood_management.sww_states.v1",
        "delivery": root.name,
        "default": "L1" if "L1" in present else present[0],
        "states": [{"key": st, "label": state_label(st, lay), "defence_line_key": STATES[st][1],
                    "column": COLUMNS[st], "dir": st,
                    "parent": PARENT[st] if PARENT[st] in present else None,
                    "same_as": None} for st in present],
    })
    return base


def import_all(root: Path, by: str | None = None, only: list[str] | None = None) -> int:
    from . import sim_results
    from . import simulation as sim

    base = extract(root, only)
    if layout_of(root.resolve()) == "adm":
        # 详情页的「模拟积水点」（口径同 RD-A）要用二维结果 + 网格邻接；交付里没带 .sww 就只是不出这一块，不挡登记
        try:
            from . import city_ponding
            city_ponding.prepare(root)
        except Exception as e:      # noqa: BLE001
            print(f"  模拟积水点未生成（{e}）；补上 .sww 后跑 python -m stormwater.city_ponding prepare <交付目录>")
    keys = [s["key"] for s in json.loads((base / "states.json").read_text(encoding="utf-8"))["states"]]
    top = max((STATES[k][1] for k in keys), key=sim.LINE_ORDER.index)
    with sim.connect() as conn:
        E = engine_meta(root.resolve())
        return sim_results.import_delivery(conn, base, by, line_key=top, extent=E["title"] if E else "City-wide",
                                           variant_label=("L1 (permanent facilities)" if E and keys == ["L1"] else f"{len(keys)} states: {' / '.join(keys)}"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, hlp in (("extract", "只从 npz 抽出交付形状"), ("import", "抽取并登记为仿真任务")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("path", type=Path)
        p.add_argument("--state", action="append", choices=list(STATES), help="只处理这几态（可重复）")
        if name == "import":
            p.add_argument("--by", help="操作人缩写，如 AR")
    args = ap.parse_args()
    if args.cmd == "extract":
        extract(args.path, args.state)
        return
    print(f"已登记为仿真 #{import_all(args.path, args.by, args.state)}")


if __name__ == "__main__":
    main()
