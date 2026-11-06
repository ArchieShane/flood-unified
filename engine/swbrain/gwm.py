"""世界模型适配器：Abu Dhabi Urban Flood GWM API（本机 Docker 容器 abu-dhabi-gwm）→ flood_points 契约。

世界模型给的是 250 m 格网、每 5 分钟一帧的地表水深（EPSG:32640，147×188 格，16,761 个陆地格）。
它是"研究级仿真器"（逐格岭回归，模型卡自述不能替代物理求解器），两条已知性质要写进结论：
  · 水深只涨不退：没有下渗/排水项，32 小时末仍是峰值；过程线只有起涨段，退水段为平台；
  · 100 mm/24 h 时约 68% 的降雨在末时刻仍留在地表。
本模块不修正这些，只如实聚合；调度侧看到的体积就是世界模型给的体积。

聚合方法（把格网变成"积水点"）：
  1. 取每格全时段最大水深 mx；mx ≥ depth_min_m 的陆地格算"积水格"；
  2. 积水格中 3×3 邻域的局部极大值为"核"候选，按深度从大到小做非极大值抑制（核间距 ≥ separation_m）；
  3. 每个积水格归到 assign_radius_m 内最近的核；归不到的算弥散积水，不出点；
  4. 每个核出一个 flood_point：几何 = 归属格的并集，lon/lat = 核格中心，体积 = Σ 水深×格面积（取时段最大），
     过程线按 hydrograph_step_min 重采样（深度取归属格最大，入流 = 体积增量/时长，非负）。
  5. 按 深度×log(1+体积) 排序，最多 max_points 个。

用法：
  python -m swbrain.gwm --check                       健康与模型元数据
  python -m swbrain.gwm --run-id trained-gwm-...      解析已有结果
  python -m swbrain.gwm --mm 100 --hours 24           新推演（幂等键 = swbrain-<mm>mm-<h>h）
  python -m swbrain.gwm --return-period 50            按占位 IDF 表取 24 h 雨量做推演
输出到 exchange/predictions/flood_points.gwm-<run_id>.json；run.py 与 serve.py 会自动取最新的 *flood_points*.json。
"""
from __future__ import annotations
import argparse
import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from pyproj import Transformer
from scipy import ndimage
from shapely.geometry import box, mapping
from shapely.ops import unary_union

from . import i18n
from .contracts import SCHEMA_FLOOD_POINTS

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- 接口客户端
class GwmClient:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/"); self.token = token

    def _call(self, path: str, method: str = "GET", body: dict | None = None, headers: dict | None = None, raw: bool = False, auth: bool = True):
        h = {"Authorization": f"Bearer {self.token}"} if auth else {}
        if headers:
            h.update(headers)
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"GWM {method} {path} → HTTP {e.code}: {detail[:300]}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"GWM 不可达 {self.base}：{e.reason}。容器是否在跑？docker ps --filter name=abu-dhabi-gwm") from None
        return payload if raw else json.loads(payload or b"null")

    def live(self) -> dict: return self._call("/health/live", auth=False)
    def ready(self) -> dict: return self._call("/health/ready", auth=False)
    def model(self) -> dict: return self._call("/v1/model")
    def run(self, run_id: str) -> dict: return self._call(f"/v1/runs/{run_id}")
    def artifact(self, run_id: str, name: str) -> bytes: return self._call(f"/v1/runs/{run_id}/artifacts/{name}", raw=True)

    def rollout(self, total_mm: float, hours: int, idempotency_key: str | None = None) -> dict:
        h = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._call("/v1/rollouts", "POST", {"totalRainfallMm": float(total_mm), "durationHours": int(hours)}, headers=h)


def client_from_cfg(cfg: dict) -> GwmClient:
    G = cfg["gwm"]
    token = Path(G["token_file"]).read_text(encoding="utf-8").strip()
    return GwmClient(G["base_url"], token)


def load_run(run_id: str, cfg: dict, client: GwmClient | None = None) -> tuple[dict, dict]:
    """返回 (run.json 记录, npz 内容字典)。本机有结果目录就直读，否则走接口下载。"""
    G = cfg["gwm"]
    local = Path(G.get("runs_dir") or "") / run_id if G.get("runs_dir") else None
    if local and (local / "run.json").is_file() and (local / "surface_depth_labels_250m.npz").is_file():
        record = json.loads((local / "run.json").read_text(encoding="utf-8"))
        with np.load(local / "surface_depth_labels_250m.npz", allow_pickle=False) as z:
            arrays = {k: z[k] for k in z.files}
        return record, arrays
    client = client or client_from_cfg(cfg)
    record = json.loads(client.artifact(run_id, "run.json"))
    with np.load(io.BytesIO(client.artifact(run_id, "surface_depth_labels_250m.npz")), allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    return record, arrays


# ---------------------------------------------------------------- 聚合
def aggregate(depth: np.ndarray, times_s: np.ndarray, x: np.ndarray, y: np.ndarray, land: np.ndarray, crs: str, cfg: dict) -> tuple[list[dict], dict]:
    """格网水深时间序列 → 积水点列表与统计。depth[t, cell]，x/y 为格边（x 升序、y 降序），land[R, C]。"""
    G = cfg["gwm"]
    dmin, sep, rad, step = float(G["depth_min_m"]), float(G["separation_m"]), float(G["assign_radius_m"]), int(G["hydrograph_step_min"])
    R, C = land.shape
    cell = float(abs(x[1] - x[0])); cell_area = cell * cell
    mx = depth.max(axis=0).reshape(R, C)
    wet = (mx >= dmin) & land
    stats = {"land_cells": int(land.sum()), "wet_cells": int(wet.sum()), "depth_min_m": dmin, "cell_size_m": cell,
             "surface_storage_end_m3": float(depth[-1, land.reshape(-1)].sum() * cell_area),
             "surface_storage_peak_m3": float((depth[:, land.reshape(-1)].sum(axis=1) * cell_area).max())}
    if not wet.any():
        return [], {**stats, "cores": 0, "points": 0, "diffuse_cells": 0}
    # 1) 核候选：3×3 局部极大值
    is_max = (ndimage.maximum_filter(mx, size=3, mode="nearest") == mx) & wet
    cand = np.argwhere(is_max)
    cx = (x[:-1] + x[1:]) / 2.0; cy = (y[:-1] + y[1:]) / 2.0
    cand_xy = np.c_[cx[cand[:, 1]], cy[cand[:, 0]]]
    order = np.argsort(-mx[cand[:, 0], cand[:, 1]], kind="stable")
    kept: list[int] = []
    for i in order:
        if not kept or np.min(np.hypot(*(cand_xy[kept] - cand_xy[i]).T)) >= sep:
            kept.append(int(i))
    cores = cand[kept]; core_xy = cand_xy[kept]
    # 2) 积水格归核
    wet_rc = np.argwhere(wet)
    wet_xy = np.c_[cx[wet_rc[:, 1]], cy[wet_rc[:, 0]]]
    from scipy.spatial import cKDTree
    dist, near = cKDTree(core_xy).query(wet_xy)
    assigned = dist <= rad
    stats["cores"] = int(len(cores)); stats["diffuse_cells"] = int((~assigned).sum())
    # 3) 每核出点
    tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    t_min = times_s / 60.0
    sample_idx = np.unique(np.r_[np.searchsorted(t_min, np.arange(0, t_min[-1] + 1e-9, step)), len(t_min) - 1])
    pts = []
    for k, (r0, c0) in enumerate(cores):
        members = wet_rc[assigned & (near == k)]
        if not len(members):
            continue
        flat = members[:, 0] * C + members[:, 1]
        d_series = depth[:, flat]                                  # [t, n]
        vol_t = d_series.sum(axis=1, dtype=np.float64) * cell_area
        dep_t = d_series.max(axis=1)
        core_series = depth[:, r0 * C + c0]
        first_wet = int(t_min[int(np.argmax(core_series >= dmin))]) if (core_series >= dmin).any() else None
        hyd = []
        for j, ti in enumerate(sample_idx):
            if j == 0:
                inflow = 0.0
            else:
                dt_h = (t_min[ti] - t_min[sample_idx[j - 1]]) / 60.0
                inflow = max(0.0, (vol_t[ti] - vol_t[sample_idx[j - 1]]) / dt_h) if dt_h > 0 else 0.0
            hyd.append({"t_min": int(round(t_min[ti])), "depth_m": round(float(dep_t[ti]), 3), "volume_m3": round(float(vol_t[ti])), "inflow_m3h": round(float(inflow))})
        geom = unary_union([box(x[c], y[r + 1], x[c + 1], y[r]) for r, c in members])
        geom_ll = _to_lonlat(geom, tr)
        lon, lat = tr.transform(cx[c0], cy[r0])
        n = int(len(members)); area = n * cell_area; dmax = float(mx[r0, c0]); vmax = float(vol_t.max()); ipk = int(vol_t.argmax())
        note = f"GWM 250 m 格网积水核：最大水深 {dmax:.2f} m，{n} 格 {area / 1e4:.0f} ha，{first_wet if first_wet is not None else '—'} 分钟起积水，体积峰值在 {int(t_min[ipk])} 分钟"
        note_en = f"GWM 250 m grid ponding core: max depth {dmax:.2f} m, {n} cells {area / 1e4:.0f} ha, wet from {first_wet if first_wet is not None else '—'} min, volume peak at {int(t_min[ipk])} min"
        pts.append({"fp_id": f"FP-G{r0:03d}-{c0:03d}", "geometry": mapping(geom_ll), "lon": round(float(lon), 6), "lat": round(float(lat), 6),
                    "area_m2": round(area), "max_depth_m": round(dmax, 3), "volume_m3": round(vmax), "duration_min": int(round(t_min[-1] - (first_wet or 0))),
                    "land_use": "grid_cell", "hydrograph": hyd, "note": note, "note_en": note_en,
                    "gwm": {"core_cell": [int(r0), int(c0)], "cells": n, "first_wet_min": first_wet, "peak_min": int(t_min[ipk]), "depth_end_m": round(float(core_series[-1]), 3)}})
    pts.sort(key=lambda p: -(p["max_depth_m"] * np.log1p(p["volume_m3"])))
    stats["points_before_cap"] = len(pts)
    pts = pts[: int(G["max_points"])]
    stats["points"] = len(pts)
    # 建议决策时刻：积水格数达到末时刻一半的时间
    wet_count = (depth[:, land.reshape(-1)] >= dmin).sum(axis=1)
    half = int(np.argmax(wet_count >= 0.5 * wet_count[-1])) if wet_count[-1] > 0 else 0
    stats["suggested_decision_time_min"] = int(round(t_min[half] / 30.0) * 30)
    return pts, stats


def _to_lonlat(geom, tr: Transformer):
    from shapely.ops import transform as sh_transform
    return sh_transform(lambda xx, yy, zz=None: tr.transform(xx, yy), geom)


def to_flood_points(record: dict, pts: list[dict], stats: dict, cfg: dict, name: str | None = None, name_en: str | None = None, return_period_yr: int | None = None) -> dict:
    md = record["metadata"]; sc = md["scenario"]
    mm = float(sc["scenario_total_precipitation_mm"]); hours = int(sc["rainfall_duration_hours"]); sim_h = int(sc["simulation_duration_hours"])
    if not name:
        name = (f"{return_period_yr}年一遇" if return_period_yr else "") + f"（GWM {mm:.0f} mm/{hours} h）"
        name_en = (f"{return_period_yr}-year " if return_period_yr else "") + f"(GWM {mm:.0f} mm/{hours} h)"
    return {
        "schema": SCHEMA_FLOOD_POINTS, "run_id": record["run_id"], "simulated": False,
        "model": {"name": "Abu Dhabi Urban Flood GWM API", "type": "world", "version": f"1.0.0 / {md['model']['release_id']}",
                  "solver": md["solver"], "grid": md["grid"], "claim_boundary": md.get("claim_boundary"),
                  "known_behaviour": "水深只涨不退（无退水项）；过程线的退水段为平台", "known_behaviour_en": "Depth never recedes (no drainage term); hydrograph recession is a plateau",
                  "created_at": record.get("created_at"), "event": md.get("event")},
        "network_version": cfg["exchange"]["network_version"],
        "scenario": {"name": name, "name_en": name_en, "return_period_yr": return_period_yr, "rain_depth_mm": round(mm, 1), "duration_min": sim_h * 60,
                     "rain_duration_min": hours * 60, "tide_m": cfg["model"]["tide_high_m"], "start": None,
                     "suggested_decision_time_min": stats.get("suggested_decision_time_min"),
                     "return_period_source": ((cfg.get("idf") or {}).get("source") or "占位 IDF 表 [idf.return_periods]") if return_period_yr else None},
        "aggregation": {k: stats[k] for k in stats}, "aggregation_params": {k: cfg["gwm"][k] for k in ("depth_min_m", "separation_m", "assign_radius_m", "max_points", "hydrograph_step_min")},
        "gwm_metrics": record.get("metrics"), "points": pts, "road_impacts": [],
    }


def idf_for(cfg: dict, key) -> dict:
    """情景（重现期 / 重现期×气候变化 / 实测事件）的 i15_mmh 与 p24_mm，见 scenarios.get。"""
    from . import scenarios
    return scenarios.get(cfg, key)


def idempotency_key(return_period_yr: int | None, mm: float, hours: int) -> str:
    return f"swbrain-rp{return_period_yr}-{mm:g}mm-{hours}h" if return_period_yr else f"swbrain-{mm:g}mm-{hours}h"


def rollout_for_return_period(cfg: dict, return_period_yr: int, client: GwmClient | None = None) -> tuple[dict, dict]:
    """按 IDF 表取该重现期的 24 h 雨量做推演（幂等，重复调用读回同一结果）并聚合；返回 (flood_points, 推演回执)。"""
    cl = client or client_from_cfg(cfg)
    idf = idf_for(cfg, return_period_yr)
    hours = int((cfg.get("idf") or {}).get("duration_hours", 24)); mm = float(idf["p24_mm"])
    r = cl.rollout(mm, hours, idempotency_key=idempotency_key(return_period_yr, mm, hours))
    fp = adapt(r["runId"], cfg, cl, return_period_yr=return_period_yr)
    fp["scenario"]["i15_mmh"] = idf.get("i15_mmh")
    return fp, r


def adapt(run_id: str, cfg: dict, client: GwmClient | None = None, return_period_yr: int | None = None, name: str | None = None) -> dict:
    record, z = load_run(run_id, cfg, client)
    pts, stats = aggregate(z["depth_m"], z["time_seconds"], z["x"], z["y"], z["land_mask"], record["metadata"]["grid"]["crs"], cfg)
    return to_flood_points(record, pts, stats, cfg, name=name, return_period_yr=return_period_yr)


def write_flood_points(fp: dict, cfg: dict, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or (ROOT / cfg["exchange"]["dir"] / "predictions"); out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"flood_points.gwm-{fp['run_id']}.json"
    p.write_text(json.dumps(fp, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------- 命令行
def main(argv=None):
    import tomllib
    ap = argparse.ArgumentParser(description="世界模型 → flood_points 适配器")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--check", action="store_true", help="只做健康检查与模型元数据")
    ap.add_argument("--run-id", help="解析已有的 GWM 结果")
    ap.add_argument("--mm", type=float, help="总雨量 mm（新推演）")
    ap.add_argument("--hours", type=int, default=24, help="降雨历时小时，默认 24")
    ap.add_argument("--return-period", type=int, help="重现期（年），按 [gwm.return_period_mm_24h] 取雨量")
    ap.add_argument("--name", help="情景名，缺省自动生成")
    ap.add_argument("--out", help="输出目录，缺省 exchange/predictions")
    a = ap.parse_args(argv)
    cfg = tomllib.loads(Path(a.config).read_text(encoding="utf-8"))
    cl = client_from_cfg(cfg)
    t0 = time.perf_counter()
    if a.check or not (a.run_id or a.mm or a.return_period):
        print("live  ", cl.live()); print("ready ", cl.ready()); m = cl.model()
        print("model ", json.dumps({k: m[k] for k in ("model", "grid", "inputs", "timeStepMinutes")}, ensure_ascii=False))
        return 0
    rp = a.return_period
    run_id = a.run_id
    if not run_id:
        mm = a.mm if a.mm is not None else float(idf_for(cfg, rp)["p24_mm"])
        r = cl.rollout(mm, a.hours, idempotency_key=idempotency_key(rp, mm, a.hours))
        run_id = r["runId"]; print(f"推演 {mm:g} mm / {a.hours} h → {run_id}  {time.perf_counter() - t0:.1f}s  metrics={json.dumps(r['metrics'])}")
    fp = adapt(run_id, cfg, cl, return_period_yr=rp, name=a.name)
    p = write_flood_points(fp, cfg, Path(a.out) if a.out else None)
    s = fp["aggregation"]
    print(f"积水格 {s['wet_cells']}/{s['land_cells']}（≥{s['depth_min_m']} m），核 {s['cores']}，出点 {s['points']}（截前 {s['points_before_cap']}），弥散格 {s['diffuse_cells']}，"
          f"建议决策时刻 {s['suggested_decision_time_min']} 分钟，末时刻地表蓄水 {s['surface_storage_end_m3']:.3g} m³")
    top = fp["points"][:5]
    for q in top:
        print(f"  {q['fp_id']} 深 {q['max_depth_m']} m 体积 {q['volume_m3']:,} m³ {q['gwm']['cells']} 格 @ {q['lon']},{q['lat']}")
    print(f"→ {p}  {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
