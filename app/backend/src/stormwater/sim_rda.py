"""RD-A 全套成果（`RDA_61mm1h_全套成果`，2026-10-25）登记成一个四态仿真任务。

和 `sim_c1`（C1 / 区域交付）是同一类交付：每态一份 15 m 网格的 ANUGA + SWMM 双向耦合结果，npz 里是同一组键
（xy / area / elev / hmax / frames / t_h / uh / vh / rain_mask）。所以二维结果的抽取直接用 `sim_c1.extract_state`
（`state_files` 已认这种文件名：`一二维/<名>_<态>_二维结果.npz`、`_耦合交换记录.json`、同目录的 `.inp / .rpt`）。
这里只补它不认的三样：

- **设施 GIS**：成果的 `设施/<名>_<态>_设施.geojson` 是「逐级累加」的一张图（每个要素带 `防线层级`），
  按 `sim_l2.facilities_layer` 的约定拆成塘 / 泵 / 取水片 / 排水去向 / 重力管，`所属态` 写成各态的 key；
  L2+suggested 的措施（推荐方案 53 项）再并上设施台账里的造价与可建性。
- **热点（积水点）**：成果没有 `统计值` 表，只有 11 个客户热点（`公共/客户热点.geojson`）。每态每个热点 200 m 内、
  并集、全域的最大水深 / 积水面积 / 最大积水量，都从该态 npz 现算（与 `rda.py` 的 `判据评估` 用同一批单元）。
- **态的父子关系**：成果逐级累加（L2 ⊃ L1，L2+planned ⊃ L2，L2+suggested ⊃ L2+planned），
  `states.json` 的 `parent` 照这个写，效果分析按父态比「新增了什么」。

任务只记「积水点 / 判据 / L3 / 措施台账」之外的二维通用部分；RD-A 特有的这些（SIM 编号积水点、L3 派单、WA-726 合规、NPV）
仍在 `/rda` 页（`rda.py`），详情页右上角有入口。

用法：

    PYTHONPATH=src .venv/bin/python -m stormwater.sim_rda extract data/1025/RDA_61mm1h_全套成果   # 只抽不登记
    PYTHONPATH=src .venv/bin/python -m stormwater.sim_rda import  data/1025/RDA_61mm1h_全套成果 --by ZN
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import numpy as np

from . import rda, sim_c1
from .sim_c1 import COLUMNS, STATES, SCHEMA, _label, delivery_res, extent_of, present_states, state_files
from .sim_sww import OUT_ROOT, SwwError, _dump, output_key

#: 成果里设施要素的「防线层级」→ 态 key（逐级累加的图里，每个要素属于它被加进来的那一态）
LEVEL_STATE = {"L1 现状": "L1", "L2 雨前预置": "L2", "L2+planned 客户计划": "L2+planned", "L2+suggested 新增": "L2+suggested"}
PARENT = {"L1": None, "L2": "L1", "L2+planned": "L2", "L2+suggested": "L2+planned"}

#: 设施属性里不直接给读者看的英文缩写 → 中文名（页面弹窗按属性名逐行列出）
_PROP_NAMES = {"p_hotspot": "服务热点", "p_n": "泵台数", "p_path": "管长 m", "p_size": "泵规格", "p_on": "开泵水位 m",
               "p_off": "停泵水位 m", "p_vol": "设计池容 m³", "p_depth": "有效水深 m", "p_floor": "池底高程 m", "p_well": "湿井水位 m"}
_DROP = {"kind", "防线层级", "排到 经度", "排到 纬度", "经度", "纬度"}
NEAR_M = 200.0


def engine_title(root: Path) -> str | None:
    """engine 推演的多态交付（`outputs.json` 的 layout = rda_multi_state）返回它的区域标题（如 RD-A），否则 None。
    这种交付不是 AECOM 的 RDA_61mm1h_全套成果：任务的范围名用区域标题（不是 `RDA`），详情页就不会去读固定的 AECOM 成果（`rda.py` 的 RDA_DIR），
    积水点和热点覆盖从各态自己的二维结果现算（`city_ponding.prepare_rda_layout`）。"""
    p = root / "outputs.json"
    if not p.is_file():
        return None
    try:
        o = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return o.get("title") if o.get("layout") == "rda_multi_state" else None


def is_rda(root: Path) -> bool:
    """交付形状：有 `公共/客户热点.geojson` 与 `公共/判据评估_*.json`，态目录里是 `一二维/*_二维结果.npz`。"""
    return (root / "公共" / "客户热点.geojson").is_file() and any((root / "公共").glob("判据评估_*.json")) \
        and any(root.glob("L1_*/一二维/*_二维结果.npz"))


# ---- 设施 GIS ---------------------------------------------------------------------

def _facility_props(pr: dict[str, Any], state: str, ledger: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out = {"编号": pr.get("编号"), "类型": pr.get("类型"), "所属态": LEVEL_STATE.get(pr.get("防线层级"), state)}
    for k, v in pr.items():
        if k in _DROP or k in out or v is None:
            continue
        out[_PROP_NAMES.get(k, k)] = v
    led = ledger.get(re.sub(r"^(SP\d+)_\d+$", r"", re.sub(r"_g$", "", str(pr.get("编号")))))
    if led:
        out["占位造价 M USD"] = round((led.get("占位造价 USD") or 0) / 1e6, 2)
        out["可建性"] = led.get("可建性")
        if led.get("需审批 / 许可"):
            out["需审批 / 许可"] = led["需审批 / 许可"]
    return out


def extract_facilities(root: Path, base: Path, states: list[str]) -> None:
    """各态一份 facilities.json（`/layers/facilities`）：本态设施图层里的全部要素，`所属态` 标明哪一态加的；
    接口只回「所属态 = 本态」的，逐级累加里继承的不重复画。"""
    ledger = _ledger(root)
    for st in states:
        d = sim_c1._state_dir(root, st) / "设施"
        gj = next((p for p in d.glob("*_设施.geojson") if not p.name.startswith("._")), None)
        if gj is None:
            continue
        feats = json.loads(gj.read_text(encoding="utf-8"))["features"]
        layers: dict[str, list] = {k: [] for k in ("ponds", "pumps", "intake", "gravity", "outlets")}
        for f in feats:
            kind = f["properties"].get("kind")
            layer = {"tank": "ponds", "sump": "intake", "gpipe": "gravity"}.get(kind, "pumps" if f["geometry"]["type"] == "Point" else None)
            if layer:
                layers[layer].append({"type": "Feature", "geometry": f["geometry"],
                                      "properties": _facility_props(f["properties"], st, ledger)})
        link = next((p for p in d.glob("*_排放连线.geojson") if not p.name.startswith("._")), None)
        if link is not None:
            for f in json.loads(link.read_text(encoding="utf-8"))["features"]:
                layers["outlets"].append({"type": "Feature", "geometry": f["geometry"],
                                          "properties": _facility_props(f["properties"], st, ledger)})
        out = base / st.replace("+", "_") / "facilities.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        _dump(out, {"state": st, "layers": {k: {"type": "FeatureCollection", "features": v} for k, v in layers.items()}})
        mine = {k: sum(1 for x in v if x["properties"]["所属态"] == st) for k, v in layers.items()}
        print(f"  设施 GIS {st}: " + " / ".join(f"{k} {n}" for k, n in mine.items()))


def _ledger(root: Path) -> dict[str, dict[str, Any]]:
    """L2+suggested 的措施台账（设计参数 + 占位造价 + 可建性），按编号。没有这一态就是空表。"""
    sd = sim_c1._state_dir(root, "L2+suggested")
    p = next(iter(sorted((sd / "设施").glob("L2+suggested_设施台账*.csv"))), None) if sd else None
    return {r["编号"]: r for r in rda._csv(p)} if p else {}


# ---- 热点（积水点）统计 -----------------------------------------------------------

def _zone_stats(area: np.ndarray, hmax: np.ndarray, frames: np.ndarray, t_h: np.ndarray, m: np.ndarray) -> dict[str, float]:
    """一个范围（单元掩膜）的最大水深 / ≥0.10、≥0.30 m 的面积（ha）/ 逐帧 Σ水深×面积 的最大值与时刻（h）。"""
    a = area[m]
    h = hmax[m]
    vol = (frames[:, m].astype(np.float32) * a).sum(axis=1)
    i = int(vol.argmax())
    return {"max_depth_m": round(float(h.max()), 3) if len(h) else 0.0,
            "area_ge_0_10_ha": round(float(a[h >= 0.10].sum() / 1e4), 3),
            "area_ge_0_30_ha": round(float(a[h >= 0.30].sum() / 1e4), 3),
            "max_volume_m3": round(float(vol[i]), 1), "max_volume_h": round(float(t_h[i]), 3)}


def extract_hotspots(root: Path, base: Path, states: list[str]) -> Path:
    """`hotspots.json`：11 个客户热点逐态的 200 m 口径、热点并集、全域。坐标取交付自带的 `公共/客户热点.geojson`。"""
    feats = json.loads((root / "公共" / "客户热点.geojson").read_text(encoding="utf-8"))["features"]
    lon = np.array([f["geometry"]["coordinates"][0] for f in feats])
    lat = np.array([f["geometry"]["coordinates"][1] for f in feats])
    hx, hy = rda._lonlat_to_utm40(lon, lat)
    ids = [f["properties"]["hotspot_id"] for f in feats]
    stats: dict[str, dict] = {h: {} for h in ids}
    total: dict[str, dict] = {}
    zones: dict[str, dict] = {}
    for st in states:
        z = np.load(state_files(root, st).npz)
        xy, area, hmax, frames, t_h = z["xy"].astype(np.float64), z["area"].astype(np.float64), z["hmax"], z["frames"], z["t_h"]
        # 区域没有客户热点（如 Mussafah 02 没有 HOTSPOT 图层）时 np.stack 空列表会报错：用 0 行掩膜，near.any 全 False，热点统计为空（不编热点）
        near = np.stack([np.hypot(xy[:, 0] - hx[i], xy[:, 1] - hy[i]) <= NEAR_M for i in range(len(ids))]) if ids else np.zeros((0, len(xy)), dtype=bool)
        for i, h in enumerate(ids):
            stats[h][st] = {"near_200m": _zone_stats(area, hmax, frames, t_h, near[i])}
        total[st] = {"near_200m": _zone_stats(area, hmax, frames, t_h, near.any(axis=0))}
        zones[st] = {"domain": _zone_stats(area, hmax, frames, t_h, np.ones(len(area), bool))}
    points = [{"id": f["properties"]["hotspot_id"], "district": f["properties"].get("area"),
               "location": f["properties"].get("location"),
               "lat": round(f["geometry"]["coordinates"][1], 6), "lon": round(f["geometry"]["coordinates"][0], 6),
               "catchment_ha": {}, "stats": stats[f["properties"]["hotspot_id"]]} for f in feats]
    points.sort(key=lambda p: int(p["id"].split("-")[1]))
    out = base / "hotspots.json"
    _dump(out, {"schema": SCHEMA, "note": f"交付的 {len(points)} 个客户热点：逐态数由该态二维结果（npz）按 200 m 范围现算",
                "region_catchment": sim_c1.region_catchment(root), "states": list(states), "points": points,
                "total": total, "zones": zones, "catchments": {"type": "FeatureCollection", "features": []}})
    print(f"  热点 {len(points)} 个 → {out.name}")
    return out


# ---- 抽取与登记 -------------------------------------------------------------------

def extract(root: Path, only: list[str] | None = None) -> Path:
    root = root.resolve()
    if not is_rda(root):
        raise SwwError(f"{root.name} 不是 RD-A 全套成果的形状")
    base = OUT_ROOT / output_key(root, delivery_res(root))
    states = [s for s in present_states(root) if not only or s in only]
    for st in states:
        sim_c1.extract_state(root, st, base / st.replace("+", "_"))
    present = [st for st in STATES if (base / st.replace("+", "_") / "delivery_summary.json").is_file()]
    extract_facilities(root, base, present)
    extract_hotspots(root, base, present)
    _dump(base / "states.json", {
        "schema": "flood_management.sww_states.v1",
        "delivery": root.name,
        "default": "L1" if "L1" in present else present[0],
        "states": [{"key": st, "label": _label(root, st), "defence_line_key": STATES[st][1],
                    "column": COLUMNS[st], "dir": st.replace("+", "_"),
                    "parent": PARENT[st] if PARENT[st] in present else None, "same_as": None} for st in present],
    })
    from . import sim_scope

    hp = base / "hotspots.json"
    hot = json.loads(hp.read_text(encoding="utf-8"))
    hot["scopes"] = sim_scope.compute(root, base, hot)
    _dump(hp, hot)
    print(f"  口径 {len(hot['scopes'])} 个 → hotspots.json")
    if engine_title(root):
        from . import city_ponding
        try:
            city_ponding.prepare_rda_layout(root, base)
        except SwwError as e:
            print(f"  模拟积水点未生成（{e}）")
    return base


def import_all(root: Path, by: str | None = None, only: list[str] | None = None) -> int:
    from . import sim_results
    from . import simulation as sim

    base = extract(root, only)
    keys = [s["key"] for s in json.loads((base / "states.json").read_text(encoding="utf-8"))["states"]]
    top = max((STATES[k][1] for k in keys), key=sim.LINE_ORDER.index)
    with sim.connect() as conn:
        return sim_results.import_delivery(conn, base, by, line_key=top, extent=engine_title(root) or extent_of(root),
                                           variant_label=f"{len(keys)} states: {' / '.join(keys)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, hlp in (("extract", "只从 npz 抽出交付形状"), ("import", "抽取并登记为仿真任务")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("path", type=Path)
        p.add_argument("--state", action="append", choices=list(STATES), help="只处理这几态（可重复）")
        if name == "import":
            p.add_argument("--by", help="操作人缩写，如 ZN")
    a = ap.parse_args()
    if a.cmd == "extract":
        extract(a.path, a.state)
        return
    print(f"已登记为仿真 #{import_all(a.path, a.by, a.state)}")


if __name__ == "__main__":
    main()
