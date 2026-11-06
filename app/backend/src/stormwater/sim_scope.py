"""C1 的「统计口径」：同一批指标按范围拆开，页面右侧总览用标签切换。

交付的统计值表把水深 / 面积 / 积水量拆成五个口径（管网服务区 / 直接降雨区 2026-09-29 按用户要求不在页面上出，见下）（`统计口径` 页）：

| key | 口径 | 溢水点 / 溢水量怎么归 |
|---|---|---|
| domain | 全域（计算域 42.8 km²） | 域内全部溢水点、域内井溢流 |
| catchment | 八个热点汇水区并集（地表 + 管网） | 点 / 井落在汇水区面内 |
| near_200m | 离热点坐标 200 m 内 | 点 / 井离任一热点 ≤ 200 m |

每个口径、每一态给：溢水点个数、溢水量、积水面积 ≥ 0.10 / ≥ 0.30 m、最大水深、最大积水量。
- 面积 / 水深 / 积水量：原样取 `C1统计值.xlsx`（`sim_c1.extract_hotspots` 已解析到 `hotspots.json`）。
- 溢水点：`sim_ponding` 的片区按代表点（x, y）归属，全域个数与地图上的「溢水点（N）」是同一个数。
- 溢水量：耦合 SWMM 报告 Node Flooding Summary 的逐节点总冒水量（10⁶ L → m³），只算落在计算域内的节点
  （域外的管网只算一维、冒水记为损失），全域合计与交付的「域内井溢流」逐位对得上（`test_sim_scope.py`）。
- 管网服务区 / 直接降雨区两个口径用户要求不出：交付表里有，`hotspots.json` 的 `zones` 仍带着，只是这里不拆、页面不放标签。
- 系统储水能力**不分口径**（管网 + 蓄水节点的几何容积，是整个系统的量），页面各口径下都取效果分析的全域值并注明。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .sim_sww import _node_coords, is_projected, norm_geom

SCOPES = ("domain", "catchment", "near_200m")
NEAR_M = 200.0
CRS_LL, CRS_UTM = "EPSG:4326", "EPSG:32640"


def _flood_by_node(rpt: Path) -> dict[str, float]:
    """报告 Node Flooding Summary：节点 → 全程总冒水量（m³）。表头列：节点 / 冒水小时 / 最大流量 / 日 / 时刻 / 总量 10⁶ L / 最大积水深。"""
    out: dict[str, float] = {}
    on = False
    with rpt.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "Node Flooding Summary" in line:
                on = True
                continue
            if on and "Summary" in line:       # 下一节（有塘时是 Storage Volume Summary，没有塘时是 Outfall Loading Summary）
                break
            t = line.split()
            if on and len(t) == 7 and ":" in t[4]:
                out[t[0]] = float(t[5]) * 1000.0
    return out


def _to_utm(geom: dict[str, Any]) -> dict[str, Any]:
    from rasterio.warp import transform_geom

    geom = norm_geom(geom)
    return geom if is_projected(geom) else transform_geom(CRS_LL, CRS_UTM, geom)


def compute(root: Path, base: Path, hot: dict[str, Any]) -> dict[str, Any]:
    """`root` 是原交付目录（要计算域面），`base` 是抽好的交付（各态目录 + states.json），`hot` 是 hotspots.json 的内容。"""
    from rasterio import features
    from rasterio.transform import Affine
    from rasterio.warp import transform as warp_transform

    from . import sim_ponding

    states = json.loads((base / "states.json").read_text(encoding="utf-8"))["states"]
    tg = np.load(base / states[0]["dir"] / "terrain_grid.npz")
    tr = Affine(*[float(v) for v in tg["transform"]])
    shape = tg["values"].shape

    def burn(geoms: list[dict[str, Any]]) -> np.ndarray:
        return features.rasterize([(g, 1) for g in geoms], out_shape=shape, transform=tr, fill=0, dtype="uint8").astype(bool)

    dom = json.loads((root / "公共" / "计算域.geojson").read_text(encoding="utf-8"))
    domain = burn([_to_utm(f["geometry"]) for f in dom["features"]])
    # 汇水区面只有 C1 交付带；没有就不出「热点汇水区」口径（页面的口径标签按 scopes 的键出，自动少一个）
    cfeats = hot["catchments"]["features"]
    scopes = [s for s in SCOPES if s != "catchment" or cfeats]
    catch = burn([_to_utm(f["geometry"]) for f in cfeats]) & domain if cfeats else np.zeros_like(domain)
    if hot["points"]:
        px, py = warp_transform(CRS_LL, CRS_UTM, [p["lon"] for p in hot["points"]], [p["lat"] for p in hot["points"]])
    else:
        px, py = [], []
    hxy = np.column_stack([px, py]) if len(px) else np.zeros((0, 2))

    def cell(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        col = np.floor((x - tr.c) / tr.a).astype(int)
        row = np.floor((y - tr.f) / tr.e).astype(int)
        ok = (row >= 0) & (row < shape[0]) & (col >= 0) & (col < shape[1])
        return np.where(ok, row, 0), np.where(ok, col, 0), ok

    def near_hot(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return (np.hypot(x[:, None] - hxy[:, 0], y[:, None] - hxy[:, 1]) <= NEAR_M).any(axis=1)

    def member(x: np.ndarray, y: np.ndarray) -> dict[str, np.ndarray]:
        r, c, ok = cell(x, y)
        in_dom = ok & domain[r, c]
        return {"domain": in_dom, "catchment": ok & catch[r, c], "near_200m": in_dom & near_hot(x, y)}

    out: dict[str, dict[str, dict[str, Any]]] = {k: {} for k in scopes}
    for st in states:
        d = base / st["dir"]
        key = st["key"]
        coords = _node_coords(d / "coupled_scenario.inp")
        flood = _flood_by_node(d / "swmm_coupled.rpt")
        names = [n for n in flood if n in coords]
        nx = np.array([coords[n][0] for n in names])
        ny = np.array([coords[n][1] for n in names])
        r, c, ok = cell(nx, ny)
        inside = ok & domain[r, c]
        vol = np.array([flood[n] for n in names])
        nm = member(nx[inside], ny[inside])
        vin = vol[inside]
        cl = sim_ponding.load(d)["clusters"]
        cm = member(np.array([x["x"] for x in cl], float), np.array([x["y"] for x in cl], float)) if cl else None
        for s in scopes:
            o = out[s].setdefault(key, {})
            o["ponding"] = int(cm[s].sum()) if cm else 0
            o["overflow_m3"] = round(float(vin[nm[s]].sum()), 1)
            src = hot["zones"] if s == "domain" else hot["total"]
            st_ = (src.get(key) or {}).get(s) or {}
            o.update({"area_ge_0_10_ha": st_.get("area_ge_0_10_ha"), "area_ge_0_30_ha": st_.get("area_ge_0_30_ha"),
                      "max_depth_m": st_.get("max_depth_m"), "max_volume_m3": st_.get("max_volume_m3")})
    return out
