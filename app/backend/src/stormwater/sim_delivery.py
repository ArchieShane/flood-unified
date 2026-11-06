"""读算力侧交付摘要，把各版 schema 归一成 v1 的形状——`sim_results` 与 `sim_ponding` 只认这一个入口。

两版交付：

- v1 `gwm.abu_dhabi_flood.interactive_coupled_2d_delivery.v1`（recession-validation 那份）：
  `outputs` 全是相对路径，有 `model_configuration` / `coupling_summary`。
- v2 `gwm.abu_dhabi_flood.public_citywide_2d_delivery.v2`（2026-09-26 的 land-masked-74h 三份，
  33 / 61 / 96 mm）：没有 `model_configuration`，雨量在 `forcing`、地形在 `surface`、演算时长在
  `domain.simulation_duration_minutes`；`outputs.native_sww` 写的是**算力机上的绝对路径**，
  SWMM 报告 / 输入 / 耦合回执压根没列（文件其实都在目录里）。

归一只做两件事，**不编数**：
1. 路径：摘要里的路径在本机不存在（或越出成果目录）就退到目录内同名文件；没列的按交付的固定文件名补，
   文件确实在才补。
2. 字段：v1 有、v2 没有的，只从同一份交付里**直接有的量**补：
   - `model_configuration` ← `forcing` + `domain` + `surface` + `coupling`；
   - `coupling.total_swmm_to_anuga_m3` 等 ← 耦合回执逐窗求和（v1 那份逐窗和与其摘要值逐位相等，口径相同）；
   - `results.inundated_area_ge_0_05m2` / `final_inundated_area_ge_0_01m2` ← 最大水深图层逐格的
     `maximum_depth_m` / `final_depth_m` 计格数 × 格面积。v2 的「末帧」是已完成的最后一窗
     （`delivery.last_completed_time_seconds`），不是目标演算终点。

v2 的 `status` / `claim_boundary` / `delivery` / `admission` 原样透传，页面要把「缺末尾窗口、退水未验证」亮出来。

v2 摘要里有两处与原始成果对不上，**不采信**（2026-09-26 用 33 mm 那份的 .sww 核过）：
- `land_water_treatment.rainfall_applied_to = active_land_mesh_only` 读着像地表受雨，但回执 887 窗
  `rainfall_inflow_m3` 全为 0，.sww 地表水量峰值 243 万 m³（33 mm 落满陆域应有 3,460 万），
  来水实为 SWMM 溢流 278 万 − 外流 65 万。地表是否受雨以回执为准（`surface_rainfall_inflow_m3`）。
- `results.final_surface_volume_m3`（529 万）≈ 250 m 格水深 × 整格面积之和，是 .sww 三角网实算
  末态水量（213 万）的 2.5 倍。页面不引用这个数。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

#: 交付目录里的固定文件名（v1 摘要里列过，v2 没列）
_DEFAULT_OUTPUTS = {
    "swmm_report": "swmm_dynamic.rpt",
    "swmm_output": "swmm_dynamic.out",
    "coupling_receipt": "bidirectional_coupling_receipt.json",
    "native_sww": "abu_dhabi_swmm_anuga_bidirectional_pilot.sww",
}

SCHEMA_V2 = "gwm.abu_dhabi_flood.public_citywide_2d_delivery.v2"

_RECEIPT_KEYS = ("rainfall_inflow_m3", "total_swmm_to_anuga_m3", "total_anuga_to_swmm_m3", "external_outflow_m3")


@lru_cache(maxsize=16)
def _read_json(path: str, mtime: float) -> Any:   # mtime 进缓存键：文件被替换就重读
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _json(p: Path) -> Any:
    return _read_json(str(p), p.stat().st_mtime)


def _inside(d: Path, p: Path) -> bool:
    return d in p.parents


def _fix_outputs(d: Path, outputs: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, rel in outputs.items():
        if not isinstance(rel, str):
            continue
        p = (d / rel).resolve()
        if key == "swmm_input" and p.is_file():          # v1 的输入在 ../../input-*/，本就在成果目录外
            out[key] = rel
        elif _inside(d, p) and p.is_file():
            out[key] = str(p.relative_to(d))
        elif (d / Path(rel).name).is_file():             # 算力机绝对路径 → 目录内同名文件
            out[key] = Path(rel).name
    for key, name in _DEFAULT_OUTPUTS.items():
        if key not in out and (d / name).is_file():
            out[key] = name
    if "swmm_input" not in out:
        hits = sorted(d.parent.parent.glob("input-*/coupled_scenario.inp"))
        if len(hits) == 1:
            out["swmm_input"] = str(Path("..") / ".." / hits[0].parent.name / hits[0].name)
    return out


@lru_cache(maxsize=16)
def _receipt_totals_cached(path: str, mtime: float) -> dict[str, float]:
    # 回执一百多 MB，逐窗字段字节流正则扫（逐接口明细已被截断，不会重复计数）
    b = Path(path).read_bytes()
    tot = {}
    for k in _RECEIPT_KEYS:
        vals = re.findall(rb'"' + k.encode() + rb'":\s*(-?[0-9.eE+-]+)', b)
        tot[k] = sum(float(v) for v in vals)
    tot["window_count"] = len(re.findall(rb'"window_index":', b))
    return tot


_WINDOW_END = re.compile(rb'"window_index":\s*\d+,\s*"start_seconds":\s*[-0-9.eE+]+,\s*"end_seconds":\s*([-0-9.eE+]+)')


@lru_cache(maxsize=8)
def _receipt_overflow_cached(path: str, mtime: float) -> tuple[list[float], list[float]] | None:
    b = Path(path).read_bytes()
    ends = [float(v) for v in _WINDOW_END.findall(b)]
    vals = [float(v) for v in re.findall(rb'"total_swmm_to_anuga_m3":\s*(-?[0-9.eE+-]+)', b)]
    if not ends or len(ends) != len(vals):   # 对不齐就不出，不猜哪一窗对哪一个数
        return None
    acc, cum = 0.0, []
    for v in vals:
        acc += v
        cum.append(acc)
    return ends, cum


def receipt_overflow_series(d: Path, outputs: dict[str, str]) -> tuple[list[float], list[float]] | None:
    """回执逐窗的「SWMM → 地表」累计溢出量：(各窗末时刻 s, 截至该窗末的累计 m³)；末项即摘要的全程量。"""
    rel = outputs.get("coupling_receipt")
    p = d / rel if rel else None
    if p is None or not p.is_file() or not p.name.endswith("receipt.json"):
        return None
    return _receipt_overflow_cached(str(p), p.stat().st_mtime)


def receipt_totals(d: Path, outputs: dict[str, str]) -> dict[str, float] | None:
    rel = outputs.get("coupling_receipt")
    if not rel:
        return None
    p = d / rel
    return _receipt_totals_cached(str(p), p.stat().st_mtime)


def _max_depth_counts(p: Path) -> tuple[int, int] | None:
    """(最大水深 ≥ 0.05 m 的格数, 末帧水深 ≥ 0.01 m 的格数)；图层不带这两列就返回 None。"""
    n05 = nfin = 0
    has_final = False
    for f in _json(p)["features"]:
        pr = f["properties"]
        if (pr.get("maximum_depth_m") or pr.get("depth_m") or 0) >= 0.05:
            n05 += 1
        if pr.get("final_depth_m") is not None:
            has_final = True
            if pr["final_depth_m"] >= 0.01:
                nfin += 1
    return (n05, nfin) if has_final else None


def load_summary(d: Path) -> dict[str, Any]:
    """读 `d/delivery_summary.json` 并归一成 v1 形状（返回新 dict，不改缓存里的原件）。"""
    d = d.resolve()
    raw = _json(d / "delivery_summary.json")
    s = json.loads(json.dumps(raw))
    s["outputs"] = _fix_outputs(d, s.get("outputs") or {})
    if s.get("schema") != SCHEMA_V2 or "model_configuration" in s:   # 只补 v2；Al Bateen 等别的交付原样返回
        return s

    fo = s.get("forcing") or {}
    dom = s.setdefault("domain", {})
    cp = s.setdefault("coupling", {})
    res = s.setdefault("results", {})
    cs = float(dom["cell_size_m"])
    sim_min = dom.get("simulation_duration_minutes")
    if sim_min is not None:
        dom.setdefault("simulation_duration_hours", float(sim_min) / 60)
    cp.setdefault("coupling_mode", cp.get("mode"))

    tot = receipt_totals(d, s["outputs"])
    if tot:
        cp.setdefault("total_swmm_to_anuga_m3", tot["total_swmm_to_anuga_m3"])
        cp.setdefault("total_anuga_to_swmm_m3", tot["total_anuga_to_swmm_m3"])
        cp.setdefault("receipt", s["outputs"]["coupling_receipt"])

    md = s["outputs"].get("maximum_depth")
    counts = _max_depth_counts(d / md) if md else None
    if counts:
        res.setdefault("inundated_area_ge_0_05m2", counts[0] * cs * cs)
        res.setdefault("final_inundated_area_ge_0_01m2", counts[1] * cs * cs)
        res.setdefault("final_inundated_cell_count_ge_0_01m", counts[1])

    exq = cp.get("exchange_quantity")
    if isinstance(exq, dict):
        exq = "; ".join(f"{k}: {v}" for k, v in exq.items())
    s["model_configuration"] = {
        "coupling_mode": cp.get("mode"),
        "return_period_years": None,
        "rainfall_total_mm": round(float(fo["total_depth_mm"]), 6) if fo.get("total_depth_mm") is not None else None,
        "rainfall_duration_minutes": fo.get("duration_minutes"),
        "exchange_quantity": exq,
        "dynamic_head_feedback": cp.get("dynamic_head_feedback_in_this_run"),
        "simulation_duration_minutes": sim_min,
        "output_interval_minutes": dom.get("output_step_minutes"),
        "model_cell_size_m": cs,
        "terrain_product": (s.get("surface") or {}).get("product"),
        # 地表是否直接受雨的实证：回执逐窗 rainfall_inflow_m3 之和（摘要里的 rainfall_applied_to 只是网格策略）
        "surface_rainfall_inflow_m3": tot["rainfall_inflow_m3"] if tot else None,
    }
    return s


def is_partial(s: dict[str, Any]) -> bool:
    """交付是否缺窗口（v2 的 `delivery.completed_window_count < expected_window_count`）。"""
    dl = s.get("delivery") or {}
    done, exp = dl.get("completed_window_count"), dl.get("expected_window_count")
    return done is not None and exp is not None and done < exp
