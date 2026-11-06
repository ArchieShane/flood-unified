"""RDA 全套成果（`RDA_61mm1h_全套成果`，一场雨四个状态）的只读读取层。

这份成果和仿真任务那条线（`sim_*`，把交付抽成数据库里的任务）形状不同：
它自己带了「积水点」（从二维结果按阈值现算的 SIM-态-序号）、L3 承接与派单、
设施图层与台账（L2+suggested 带设计参数、造价、NPV、WA-726 合规检查）、四项判据（A–D）。
所以单独走一条只读通道：API 运行期直接读交付目录，不入库、不重算，数字与交付文件逐字段一致。

目录：环境变量 `RDA_DIR`，缺省 `backend/data/1025/RDA_61mm1h_全套成果`。
口径一律以交付自带的 `README.md` / `积水点说明.md` 为准，这里不改任何判据。
"""

from __future__ import annotations

import csv
import json
import math
import os
from functools import lru_cache
from pathlib import Path

import numpy as np

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "1025" / "RDA_61mm1h_全套成果"

#: key → (交付子目录, 文件名里的态记号, 中文名, 防线说明)。顺序即「逐级累加」顺序。
STATES: dict[str, dict] = {
    "L1": {"folder": "L1_现状", "tag": "L1", "label": "L1 现状", "line": "第一道防线 · 现状永久管网"},
    "L2": {"folder": "L2_雨前预置泵", "tag": "L2", "label": "L2 雨前预置泵", "line": "第二道防线 · 雨前预置移动泵"},
    "L2P": {"folder": "L2+planned_客户计划", "tag": "L2P", "label": "L2+planned 客户计划", "line": "第二道防线 · 客户已有计划（视同建成）"},
    # key 是本系统的协议值（接口 / 前端沿用 OPTCS2）；目录与文件名里的记号随交付走：2026-10-27 的交付把 OPTCS2 换成了 OPTA61PS
    "OPTCS2": {"folder": "L2+suggested_推荐方案", "tag": "OPTA61PS", "label": "L2+suggested 推荐方案", "line": "第二道防线 · 推荐方案（当前方案瘦身版）"},
}
DEPTHS = ("0.15", "0.30")

#: 设施图层 kind → 中文名与图例色（前端按 kind 取色，这里只给名字）
FACILITY_KINDS = {
    "ps_l1": "现状泵站",
    "mobile": "雨前预置移动泵",
    "ps_planned": "客户计划泵站",
    "tank": "地下调蓄池",
    "gpipe": "重力管（截流井→调蓄池）",
    "conv": "压力输水（泵→排口）",
    "sump": "地面取水口 / 洼坑",
}

#: 合规检查结果 → 中文
CHECK_RESULT = {"pass": "通过", "review": "需审批 / 复核", "unknown": "缺数据判不了", "fail": "不通过"}


class RdaError(Exception):
    """成果缺失 / 参数不对。API 层转 404 / 422。"""


def root() -> Path:
    p = Path(os.getenv("RDA_DIR") or DEFAULT_DIR)
    if not p.is_dir():
        raise RdaError("没有 RDA 成果目录")
    return p


def available() -> bool:
    try:
        # 交付里一定有 `积水点说明.md`（2026-10-27 的交付没带 README.md）；页面不读它们的内容，只认成果在不在
        return any((root() / n).is_file() for n in ("README.md", "积水点说明.md"))
    except RdaError:
        return False


def _state(key: str) -> dict:
    if key not in STATES:
        raise RdaError("没有这个状态")
    return STATES[key]


def _dir(key: str) -> Path:
    d = root() / _state(key)["folder"]
    if not d.is_dir():
        raise RdaError("这个状态的成果目录不在")
    return d


def _depth(depth: str) -> str:
    d = f"{float(depth):.2f}"
    if d not in DEPTHS:
        raise RdaError("成果只给 0.15 m 与 0.30 m 两档")
    return d


# ---- 读文件 -----------------------------------------------------------------------

def _num(v: str):
    """CSV 单元格 → 数字 / 布尔 / 字符串 / None。「未退」之类文字保留原样。"""
    if v is None:
        return None
    s = v.strip()
    if s == "":
        return None
    if s in ("true", "false"):
        return s == "true"
    try:
        f = float(s)
    except ValueError:
        return s
    if math.isnan(f) or math.isinf(f):
        return None
    return int(f) if f.is_integer() and "." not in s and "e" not in s.lower() else f


def _csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [{k: _num(v) for k, v in r.items()} for r in csv.DictReader(f)]


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _one(folder: Path, pattern: str) -> Path | None:
    hits = sorted(p for p in folder.glob(pattern) if not p.name.startswith("._"))
    return hits[0] if hits else None


# ---- 总览 -------------------------------------------------------------------------

@lru_cache(maxsize=1)
def rain() -> dict:
    rows = _csv(root() / "公共" / "降雨过程_S61D1.csv")
    cols = ["t_h", "intensity_mm_h", "cum_mm", "cum_net_mm"]
    series = [dict(zip(cols, [r[k] for k in r])) for r in rows]
    peak = max(series, key=lambda r: r["intensity_mm_h"])
    return {
        "storm": "S61D1",
        "depth_mm": 61.0,
        "duration_h": 1.0,
        "peak_mm_h": peak["intensity_mm_h"],
        "peak_t_h": peak["t_h"],
        "net_total_mm": series[-1]["cum_net_mm"],
        "series": series,
    }


@lru_cache(maxsize=1)
def criteria() -> dict:
    return _json(root() / "公共" / "判据评估_S61D1.json")


def _hotspot_pass(h: dict) -> bool:
    return all(h["pass"].values())


def criteria_for(key: str) -> dict:
    """某态的四项判据：逐热点 A/B/C，加域级 D。"""
    tag = _state(key)["tag"]
    st = criteria()["states"].get(tag)
    if st is None:
        raise RdaError("成果里没有这个状态的判据评估")
    hs = []
    for hid, v in st["hotspots"].items():
        hs.append({"id": hid, **{k: v[k] for k in ("ge030_ha", "ge015_ha_h", "base_ge015_ha_h", "recede_h_after_rain", "max_m")},
                   "pass": v["pass"], "all_pass": _hotspot_pass(v)})
    return {
        "state": key,
        "hotspots": hs,
        "passed": sum(h["all_pass"] for h in hs),
        "total": len(hs),
        "domain": st["domain"],
        "all_pass": st.get("all_pass"),
        "rules": {
            "A": "热点 200 m 内无 ≥0.30 m 积水",
            "B": "≥0.15 m 面积×时间 ≤ L2+planned 的 10 %",
            "C": "雨停后 6 h 内退到 0.10 m 以下",
            "D": "比 L2+planned 加深的面积 ≤ 0.05 ha（域级）",
        },
    }


def _facility_counts(key: str) -> dict:
    g = facilities_geojson(key)
    out: dict[str, int] = {}
    for f in g["features"]:
        k = f["properties"]["kind"]
        out[k] = out.get(k, 0) + 1
    return out


def overview() -> dict:
    states = []
    for key, s in STATES.items():
        d = root() / s["folder"]
        if not d.is_dir():
            continue
        crit = criteria_for(key)
        n15 = len(_csv(d / "积水点" / "ponding_d0.15.csv"))
        n30 = len(_csv(d / "积水点" / "ponding_d0.30.csv"))
        counts = _facility_counts(key)
        states.append({
            "key": key, "label": s["label"], "line": s["line"],
            "ponding_count": {"0.15": n15, "0.30": n30},
            "facility_features": sum(counts.values()),
            "facility_counts": counts,
            "facility_totals": facility_totals(key),
            "hotspots_passed": crit["passed"], "hotspots_total": crit["total"],
            "ge030_ha": crit["domain"]["ge030_ha"],
            "peak_vol_m3": crit["domain"]["peak_vol_m3"],
            "end_vol_m3": crit["domain"]["end_vol_m3"],
            "pass_d": crit["domain"]["pass_D"],
        })
    sugg_cost = cost_summary()
    return {
        "name": "RDA_S61D1",
        "title": "RD-A 61 mm / 1 h 全套成果",
        "rain": {k: v for k, v in rain().items() if k != "series"},
        "model": "SWMM 5.2 一维管网 + ANUGA 二维地表双向耦合（15 m 网格，65,210 个单元），模拟 11.0 h（雨后再算 10 h）",
        "states": states,
        "facility_kinds": FACILITY_KINDS,
        "suggested_measures": sugg_cost["total_count"] if sugg_cost else 0,
        "new_outfalls": len({r["排口岸点"] for r in ledger() if r.get("排口岸点")}),
        "ponding_rule": {
            "min_area_m2": 100, "min_duration_h": 0.5, "depths_m": [0.15, 0.30],
            "text": "最大水深 ≥ 阈值的单元按三角网共边连成片，去掉本状态地表塘占地，再去掉面积 < 100 m²、持续 < 0.5 h 的片；"
                    "峰值积水量取逐帧 Σ(水深×单元面积) 的最大值。阈值为试算值，尚未与客户对齐。",
        },
        "disclaimer": "造价是占位单价，用于方案比较，不是工程概算；设施位置落地前仍需现场核实和主管部门许可。",
    }


def rain_series() -> list[dict]:
    return rain()["series"]


# ---- 积水点 -----------------------------------------------------------------------

def ponding(key: str, depth: str = "0.15") -> dict:
    """某态某档的积水点清单：机器版字段 + 评估剔除标记 + 200 m 内热点 + L3 承接（仅 0.15 m 档）。"""
    d = _dir(key)
    dep = _depth(depth)
    pdir = d / "积水点"
    rows = _csv(pdir / f"ponding_d{dep}.csv")
    if not rows:
        raise RdaError("这个状态没有积水点清单")
    excl = {r["积水点"]: r for r in _csv(pdir / f"积水点_评估剔除区标记_d{dep}.csv")}
    near: dict[str, list] = {}
    for r in _csv(pdir / f"ponding_near_hotspots_d{dep}.csv"):
        near.setdefault(r["id"], []).append({"hotspot": r["client_hotspot"], "dist_m": r["dist_m"]})
    l3 = {r["积水点"]: r for r in _csv(pdir / "L3承接与派单.csv")} if dep == "0.15" else {}
    for r in rows:
        x = excl.get(r["id"])
        r["excluded"] = bool(x and x["落在评估剔除区"] == "是")
        r["exclusion_note"] = x["剔除区说明"] if x else None
        r["area_after_exclusion_m2"] = x["扣除后 m²"] if x else None
        # 客户文档（RD-A 模拟积水点与客户热点重合分析 2026-10-26）的口径：一半以上面积落在剔除区内的片不计入。
        # 成果自带的「落在评估剔除区」判据更严（L1 一片都没标），所以另给这个标记，不改成果原字段
        r["mostly_excluded"] = bool(x and r["area_m2"] and float(x["扣除后 m²"]) < 0.5 * float(r["area_m2"]))
        r["near_hotspots"] = near.get(r["id"], [])
        r["l3"] = _l3_row(l3[r["id"]]) if r["id"] in l3 else None
        # 未退的点 recede_h 为空，前端要「未退」二字
        r["status"] = "未退" if r["receded"] is False else "已退"
    _add_peak_times(key, dep, rows)
    return {
        "state": key, "depth_m": float(dep), "count": len(rows),
        "peak_vol_sum_m3": sum(r["vol_peak_m3"] or 0 for r in rows),
        "not_receded": sum(r["receded"] is False for r in rows),
        "excluded": sum(r["excluded"] for r in rows),
        "mostly_excluded": sum(r["mostly_excluded"] for r in rows),
        "points": rows,
    }


PEAK_TIME_FIELDS = ("area_peak_m2", "t_area_peak_h", "t_hmax_h")


def _add_peak_times(key: str, dep: str, rows: list[dict]) -> None:
    """成果清单只给了峰值积水量的时刻（`t_peak_h`）；最大面积和最大水深出现在哪个时刻要从逐帧水深现算。

    用成果的默认阈值（100 m²、0.5 h）从二维结果重算同一批片，按「最深单元的经纬度」对上成果清单的行，
    且峰值积水量、峰值时刻也相等才采用（默认阈值下与成果逐点一致，见 `test_rda.py`）；对不上的行填 None，
    **不改清单里任何已有字段**。读不了二维结果（缺文件 / 缺 scipy）时整批填 None。
    """
    try:
        c = {(round(p["lon"], 5), round(p["lat"], 5)): p for p in ponding_custom(key, float(dep), 100.0, 0.5)["points"]}
    except Exception:
        c = {}
    for r in rows:
        q = c.get((round(float(r["lon"]), 5), round(float(r["lat"]), 5)))
        ok = bool(q) and q["vol_peak_m3"] == r["vol_peak_m3"] and abs(q["t_peak_h"] - r["t_peak_h"]) < 0.01
        for f in PEAK_TIME_FIELDS:
            r[f] = q[f] if ok else None


def _l3_row(r: dict) -> dict:
    def pool(prefix: str) -> dict:
        return {"result": r.get(f"{prefix}：结果"), "units": r.get(f"{prefix}：台/辆"),
                "arrive_h": r.get(f"{prefix}：到位 h"), "done_h": r.get(f"{prefix}：完成 h")}
    return {
        "internal_id": r["L3 内部编号"], "level": r["后果等级"], "basis": r["定级依据"],
        "unit_importance": r["热点重要度"], "units_300m": r["300 m 内重点单位"],
        "deadline_h": r["退干期限 h"], "natural_drain_h": r["自然退干 h"],
        "road_ge015_m2": r["≥0.15 m 路面 m²"], "road_ge030_m2": r["≥0.30 m 路面 m²"],
        "bldg_ge015": r["≥0.15 m 碰到建筑"], "bldg_ge030": r["≥0.30 m 碰到建筑"],
        "substation": r["变电站"], "measure": r["L3 手段"], "verdict": r["结论"],
        "island": pool("Island A13"), "adm": pool("ADM 全部驻地"),
        "shift_m": r["派车终点平移 m"], "note": r["备注"],
    }


def ponding_areas(key: str, depth: str = "0.15") -> dict:
    p = _dir(key) / "积水点" / f"ponding_areas_d{_depth(depth)}.geojson"
    if not p.is_file():
        raise RdaError("这个状态没有积水范围图层")
    return _json(p)


def l3(key: str) -> dict:
    p = _dir(key) / "积水点" / "L3承接_汇总.json"
    if not p.is_file():
        raise RdaError("这个状态没有 L3 承接汇总")
    d = _json(p)
    d["not_in_l3"] = _csv(_dir(key) / "积水点" / "未进入L3清单的积水点.csv")
    return d


# ---- 设施 -------------------------------------------------------------------------

def facilities_geojson(key: str) -> dict:
    d = _dir(key) / "设施"
    p = _one(d, "RDA_S61D1_*_设施.geojson")
    if p is None:
        raise RdaError("这个状态没有设施图层")
    return _json(p)


def facilities_list(key: str) -> list[dict]:
    p = _one(_dir(key) / "设施", "RDA_S61D1_*_设施清单.csv")
    return _csv(p) if p else []


def _ledger_files() -> Path:
    return _dir("OPTCS2") / "设施"


@lru_cache(maxsize=1)
def ledger() -> list[dict]:
    """L2+suggested 的措施台账（设计参数 + 造价 + 可建性 + 审批）；项数以交付为准（2026-10-27 的推荐方案是 53 项）。"""
    p = _one(_ledger_files(), "L2+suggested_设施台账*.csv")
    return _csv(p) if p else []


def cost_summary() -> dict | None:
    d = _ledger_files()
    p = _one(d, "L2+suggested_造价汇总.csv")
    if p is None:
        return None
    rows = _csv(p)
    total = next((r for r in rows if r["设施类型"] == "合计"), None)
    return {
        "rows": [r for r in rows if r["设施类型"] != "合计"],
        "total": total,
        "total_count": total["数量"] if total else 0,
    }


def npv() -> dict | None:
    p = _one(_ledger_files(), "L2+suggested_全寿命成本*.csv")
    if p is None:
        return None
    rows = _csv(p)
    schemes = [r for r in rows if r.get("方案")]
    breakdown = [{"item": r["造价分项（当前方案）"], "m_usd": r["M USD"], "pct": r["占比 %"]}
                 for r in rows if r.get("造价分项（当前方案）")]
    for r in schemes:
        for k in ("造价分项（当前方案）", "M USD", "占比 %"):
            r.pop(k, None)
    return {"schemes": schemes, "breakdown": breakdown}


def intakes() -> list[dict]:
    p = _one(_ledger_files(), "L2+suggested_地面取水口.csv")
    return _csv(p) if p else []


def scheme_compare() -> list[dict]:
    """推荐方案与客户计划 / 专门方案 / 当前方案的并排对比（`方案对比.csv`：每场雨、每个方案的客户热点四项全过数与 ≥0.30 m 面积）。"""
    p = _one(_ledger_files(), "L2+suggested_方案对比.csv")
    return _csv(p) if p else []


def scheme_diff() -> list[dict]:
    """推荐方案相对当前方案删去 / 换掉了哪些措施（`与当前方案的差异.csv`）。"""
    p = _one(_ledger_files(), "L2+suggested_与当前方案的差异.csv")
    return _csv(p) if p else []


def _col(r: dict, prefix: str):
    """台账列名按前缀取（交付改过表头：『截流井溢流量 m³（96 mm 两场取大）』→『截流井溢流量 m³』），按最新的来、旧版也认。"""
    return next((v for k, v in r.items() if k.startswith(prefix)), None)


def surface_ps_geojson() -> dict:
    p = _one(_ledger_files(), "L2+suggested_地面泵站与取水坑.geojson")
    return _json(p) if p else {"type": "FeatureCollection", "features": []}


@lru_cache(maxsize=1)
def _compliance_all() -> dict:
    p = _one(_ledger_files(), "L2+suggested_合规检查*.json")
    return _json(p) if p else {}


def compliance(fid: str) -> dict:
    """一项措施的 WA-726 / 空间约束逐条检查。"""
    items = _compliance_all().get(fid)
    if items is None:
        raise RdaError("没有这项设施的合规检查")
    out = []
    for it in items:
        out.append({
            "check": it["check"], "name": it["zh"], "result": it["result"],
            "result_zh": CHECK_RESULT.get(it["result"], it["result"]),
            "detail": it.get("detail") or "",
            "refs": [r.get("id") for r in it.get("refs", []) if r.get("id")],
        })
    tally: dict[str, int] = {}
    for it in out:
        tally[it["result"]] = tally.get(it["result"], 0) + 1
    return {"id": fid, "tally": tally, "checks": out}


def suggested() -> dict:
    """点 sugg 时要看的全部：逐项措施（类型、服务热点、设计参数、造价、审批）+ 汇总 + NPV + 取水口。"""
    led = ledger()
    if not led:
        raise RdaError("没有 L2+suggested 的设施台账")
    items = []
    for r in led:
        tally = compliance(r["编号"])["tally"] if r["编号"] in _compliance_all() else {}
        items.append({
            "id": r["编号"], "type": r["类型"], "hotspot": r["服务热点"],
            "lon": r["经度"], "lat": r["纬度"],
            "intercept_wells": r["截流井"], "overflow_m3": _col(r, "截流井溢流量"),
            "tank_m3": r["池容 m³"], "effective_depth_m": r["有效水深 m"], "invert_m": r["池底高程 m"],
            "excavation_m": r["开挖深度 m"],
            "pumps": r["泵台数"], "design_q_m3s": r["设计流量 m³/s"], "pump_q_m3s": r["单泵流量 m³/s"],
            "station_area_m2": r["泵站场地 m²"],
            "pipe_len_m": r["管长 m"], "pipe_dia_m": r["管径 m"], "pipe_parallel": r["并联根数"], "velocity_ms": r["峰值流速 m/s"],
            "outfall": r["排口岸点"], "outfall_lon": r["排口经度"], "outfall_lat": r["排口纬度"],
            "land_makani": r["用地（Makani 占比）"],
            "cost_usd": r["占位造价 USD"], "cost_basis": r["造价依据"],
            "buildability": r["可建性"], "approvals": r["需审批 / 许可"], "missing_data": r["缺数据判不了"],
            "check_count": r["合规检查项数"], "check_tally": tally,
            "vs_current": r.get("与当前方案相比"),        # 相对当前方案（96 mm 优化版）：保留 / 新增 / 换成更省的…
        })
    return {
        "measures": items,
        "by_type": cost_summary(),
        "npv": npv(),
        "intakes": intakes(),
        "compare": scheme_compare(),
        "diff": scheme_diff(),
        "surface_ps": surface_ps_geojson(),
        "note": "造价是占位单价，用于方案比较，不是工程概算；位置已按 WA-726 规则、用地和地下管线约束筛过，落地前仍需现场核实和主管部门许可。",
    }


# ---- 其它图层 ---------------------------------------------------------------------

def hotspots() -> dict:
    return _json(root() / "公共" / "客户热点.geojson")


def domain() -> dict:
    return _json(root() / "公共" / "计算域.geojson")


def exclusion_zones() -> dict:
    return _json(root() / "公共" / "评估剔除区.geojson")


def _utm40_to_lonlat(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """EPSG:32640 → WGS84（Krüger 级数反算，精度远小于一格；环境里没有 pyproj）。"""
    a, f = 6378137.0, 1 / 298.257223563
    k0, lon0 = 0.9996, math.radians(57.0)
    n = f / (2 - f)
    A = a / (1 + n) * (1 + n**2 / 4 + n**4 / 64)
    b = [n / 2 - 2 * n**2 / 3 + 37 * n**3 / 96, n**2 / 48 + n**3 / 15, 17 * n**3 / 480]
    d = [2 * n - 2 * n**2 / 3 - 2 * n**3, 7 * n**2 / 3 - 8 * n**3 / 5, 56 * n**3 / 15]
    xi = y / (k0 * A)
    eta = (x - 500000.0) / (k0 * A)
    xi_p, eta_p = xi.copy(), eta.copy()
    for j in (1, 2, 3):
        xi_p -= b[j - 1] * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
        eta_p -= b[j - 1] * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
    chi = np.arcsin(np.sin(xi_p) / np.cosh(eta_p))
    lat = chi.copy()
    for j in (1, 2, 3):
        lat += d[j - 1] * np.sin(2 * j * chi)
    lon = lon0 + np.arctan2(np.sinh(eta_p), np.cos(xi_p))
    return np.degrees(lon), np.degrees(lat)


@lru_cache(maxsize=8)
def max_depth(key: str, min_depth: float = 0.05) -> dict:
    """逐单元全程最大水深（来自 `_二维结果.npz` 的 hmax），只列 ≥ min_depth 的单元，紧凑数组。"""
    s = _state(key)
    p = _one(_dir(key) / "一二维", f"RDA_S61D1_{s['tag']}_二维结果.npz")
    if p is None:
        raise RdaError("这个状态没有二维结果")
    z = np.load(p)
    h = z["hmax"].astype(np.float64)
    m = h >= min_depth
    lon, lat = _utm40_to_lonlat(z["xy"][m, 0].astype(np.float64), z["xy"][m, 1].astype(np.float64))
    area = z["area"][m].astype(np.float64)
    return {
        "state": key, "min_depth_m": min_depth, "count": int(m.sum()),
        "cell_edge_m": round(float(np.sqrt(np.median(z["area"]))), 1),
        "lon": [round(float(v), 6) for v in lon], "lat": [round(float(v), 6) for v in lat],
        "depth_m": [round(float(v), 2) for v in h[m]], "area_m2": [round(float(v), 1) for v in area],
        "max_m": round(float(h.max()), 2),
    }


# ---- 逐帧、热点 50 m 水深过程（成果里没有现成文件，从 `_二维结果.npz` 现取）-------------

def _lonlat_to_utm40(lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """WGS84 → EPSG:32640（Krüger 级数正算，与 `_utm40_to_lonlat` 互逆）。"""
    a, f = 6378137.0, 1 / 298.257223563
    k0, lon0 = 0.9996, math.radians(57.0)
    n = f / (2 - f)
    A = a / (1 + n) * (1 + n**2 / 4 + n**4 / 64)
    al = [n / 2 - 2 * n**2 / 3 + 5 * n**3 / 16, 13 * n**2 / 48 - 3 * n**3 / 5, 61 * n**3 / 240]
    phi, dl = np.radians(lat), np.radians(lon) - lon0
    e = 2 * math.sqrt(n) / (1 + n)
    t = np.sinh(np.arctanh(np.sin(phi)) - e * np.arctanh(e * np.sin(phi)))
    xi = np.arctan2(t, np.cos(dl))
    eta = np.arctanh(np.sin(dl) / np.sqrt(1 + t**2))
    x = eta.copy()
    y = xi.copy()
    for j in (1, 2, 3):
        x += al[j - 1] * np.cos(2 * j * xi) * np.sinh(2 * j * eta)
        y += al[j - 1] * np.sin(2 * j * xi) * np.cosh(2 * j * eta)
    return 500000.0 + k0 * A * x, k0 * A * y


def _npz_path(key: str) -> Path:
    p = _one(_dir(key) / "一二维", f"RDA_S61D1_{_state(key)['tag']}_二维结果.npz")
    if p is None:
        raise RdaError("这个状态没有二维结果")
    return p


@lru_cache(maxsize=5)
def _cells(key: str) -> dict:
    z = np.load(_npz_path(key))
    return {"xy": z["xy"].astype(np.float64), "area": z["area"].astype(np.float64),
            "frames": z["frames"], "hmax": z["hmax"].astype(np.float32), "t_h": z["t_h"].astype(np.float64),
            "elev": z["elev"].astype(np.float64)}


@lru_cache(maxsize=1)
def grid() -> dict:
    """全部单元形心（四态同一张网），逐帧接口只回单元号，前端拿这张表落点。"""
    c = _cells("L1")
    lon, lat = _utm40_to_lonlat(c["xy"][:, 0], c["xy"][:, 1])
    return {"count": len(lon), "cell_edge_m": round(float(np.sqrt(np.median(c["area"]))), 1),
            "lon": [round(float(v), 6) for v in lon], "lat": [round(float(v), 6) for v in lat]}


@lru_cache(maxsize=5)
def frame_index(key: str) -> dict:
    """逐帧的全域指标：积水 ≥0.10 m 面积（ha）、最深水深，给时间轴曲线用。"""
    c = _cells(key)
    F, area = c["frames"], c["area"]
    wet10 = [(area * (F[i] >= 0.10)).sum() / 1e4 for i in range(len(F))]
    return {"state": key, "t_h": [round(float(v), 4) for v in c["t_h"]],
            "wet10_ha": [round(float(v), 2) for v in wet10],
            "dmax_m": [round(float(F[i].max()), 3) for i in range(len(F))]}


def frame(key: str, index: int, mode: str = "depth", base: str | None = None, min_depth: float = 0.10) -> dict:
    """第 index 帧。mode=depth：水深 ≥ min_depth 的单元；mode=diff：本态减对比基准的水深差（|差| ≥ 0.05 m）。"""
    c = _cells(key)
    if not 0 <= index < len(c["frames"]):
        raise RdaError("没有这一帧")
    d = c["frames"][index].astype(np.float32)
    if mode == "diff":
        if base is None:
            raise ValueError("差值模式要给对比基准")
        d = d - _cells(base)["frames"][index].astype(np.float32)
        m = np.abs(d) >= 0.05
    else:
        m = d >= min_depth
    ids = np.nonzero(m)[0]
    return {"state": key, "index": index, "t_h": round(float(c["t_h"][index]), 4), "mode": mode, "base": base,
            "cells": ids.tolist(), "v": [round(float(x), 2) for x in d[ids]]}


def max_depth_diff(key: str, base: str, min_diff: float = 0.05) -> dict:
    """全程最大水深的差（本态 − 基准），负数 = 变浅。"""
    d = _cells(key)["hmax"] - _cells(base)["hmax"]
    ids = np.nonzero(np.abs(d) >= min_diff)[0]
    return {"state": key, "base": base, "cells": ids.tolist(), "v": [round(float(x), 2) for x in d[ids]]}


@lru_cache(maxsize=1)
def hotspot_series() -> dict:
    """11 个客户热点 50 m 内的最深水深逐帧（四态）+ 50 m 内 ≥0.30 m 的时长（h）。"""
    feats = hotspots()["features"]
    lon = np.array([f["geometry"]["coordinates"][0] for f in feats])
    lat = np.array([f["geometry"]["coordinates"][1] for f in feats])
    hx, hy = _lonlat_to_utm40(lon, lat)
    ids = [f["properties"]["hotspot_id"] for f in feats]
    out: dict = {"hotspots": ids, "states": {}}
    for key in STATES:
        c = _cells(key)
        F = c["frames"]
        dt = float(np.median(np.diff(c["t_h"])))
        per = {}
        for i, hid in enumerate(ids):
            near = np.nonzero(np.hypot(c["xy"][:, 0] - hx[i], c["xy"][:, 1] - hy[i]) <= 50.0)[0]
            if len(near) == 0:
                per[hid] = {"depth_m": None, "ge030_h": None, "max_m": None, "end_m": None}
                continue
            curve = F[:, near].astype(np.float32).max(axis=1)
            per[hid] = {"depth_m": [round(float(v), 3) for v in curve],
                        "ge030_h": round(float((curve >= 0.30).sum() * dt), 2),
                        "max_m": round(float(curve.max()), 2), "end_m": round(float(curve[-1]), 2)}
        out["states"][key] = per
    out["t_h"] = [round(float(v), 4) for v in _cells("L1")["t_h"]]
    return out


def facility_totals(key: str) -> list[dict]:
    """「这个状态包含的设施」：按防线层级 × 类型的数量与规模（池容 / 泵流量）。"""
    groups: dict[tuple, dict] = {}
    for f in facilities_geojson(key)["features"]:
        p = f["properties"]
        g = groups.setdefault((p["防线层级"], p["kind"]), {"level": p["防线层级"], "kind": p["kind"], "count": 0, "vol_m3": 0.0, "q_ls": 0.0})
        g["count"] += 1
        g["vol_m3"] += p.get("池容 m³") or 0
        g["q_ls"] += p.get("流量 L/s") or 0
    return [{**g, "name": FACILITY_KINDS.get(g["kind"], g["kind"])} for g in groups.values()]


# ---- 积水点按自定义阈值重算 ---------------------------------------------------------

#: 重算参数的合法范围（成果给的是 0.15 / 0.30 m、100 m²、0.5 h 三个试算值）
PONDING_LIMITS = {"depth": (0.05, 3.0), "min_area": (0.0, 20000.0), "min_dur": (0.0, 11.0)}


@lru_cache(maxsize=1)
def _mesh_edges() -> np.ndarray:
    """二维网格的三角形共边关系（N×2 的三角形号对）。

    网格取自 L1 的 `.sww`（`volumes`，四个状态同一张网、同一单元顺序）：两个三角形共一条边才算相邻，
    与成果「积水点说明」里的 `adjacency = mesh` 同口径，不用形心距离近似。
    """
    from scipy.io import netcdf_file

    p = _one(_dir("L1") / "一二维", "*.sww")
    if p is None:
        raise RdaError("没有二维网格文件（.sww），算不了连片")
    with netcdf_file(str(p), "r", mmap=False) as f:
        v = f.variables["volumes"][:].astype(np.int64).copy()
    e = np.concatenate([v[:, [0, 1]], v[:, [1, 2]], v[:, [2, 0]]])
    tri = np.concatenate([np.arange(len(v))] * 3)
    e.sort(axis=1)
    key = e[:, 0] * (int(e.max()) + 1) + e[:, 1]
    o = np.argsort(key, kind="stable")
    ks, ts = key[o], tri[o]
    same = ks[1:] == ks[:-1]
    return np.stack([ts[:-1][same], ts[1:][same]], 1)


def check_ponding_params(depth: float, min_area: float, min_dur: float) -> None:
    for name, v in (("depth", depth), ("min_area", min_area), ("min_dur", min_dur)):
        lo, hi = PONDING_LIMITS[name]
        if not lo <= v <= hi:
            raise ValueError(f"{name} 要在 {lo}–{hi} 之间")


@lru_cache(maxsize=32)
def ponding_custom(key: str, depth: float = 0.15, min_area: float = 100.0, min_dur: float = 0.5) -> dict:
    """从二维结果按阈值现算积水点，口径与成果的 `swbrain.ponding` 一致（见 `积水点说明.md`）：

    1. 全程最大水深 ≥ depth 的单元，按三角网共边连成片；
    2. 连片面积 ≥ min_area；
    3. 持续 = 片内「逐帧 ≥depth 的面积 ≥ min_area」的帧数 × 帧间隔，≥ min_dur；
    4. 峰值积水量 = 逐帧 Σ(水深×单元面积)（整片所有单元）的最大值；
    5. 退水 = 峰值后，片内 ≥depth 面积跌破 min_area 的时长，到模拟结束还没退的 receded=False。
    编号 SIM-<态>-<序号>，按峰值积水量降序。

    和成果自带清单的差别：这里**不扣地表塘占地**（成果的塘占地范围没随成果给出）。
    用成果的默认阈值重算，点数与成果逐态一致（`test_rda.py` 锁死），逐点数字也一致。
    返回体与 `ponding()` 同形，另带 `cells`（片内单元号，给前端画范围）；没有 L3 承接，故 `l3` 为 None。
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    check_ponding_params(depth, min_area, min_dur)
    c = _cells(key)
    xy, area, F, hmax, t = c["xy"], c["area"], c["frames"], c["hmax"], c["t_h"]
    elev = c["elev"]
    dt = float(np.median(np.diff(t)))
    tag = _state(key)["tag"]
    wet = np.nonzero(hmax >= depth)[0]
    points: list[dict] = []
    if len(wet):
        e = _mesh_edges()
        inw = np.full(len(xy), -1)
        inw[wet] = np.arange(len(wet))
        a, b = inw[e[:, 0]], inw[e[:, 1]]
        m = (a >= 0) & (b >= 0)
        g = coo_matrix((np.ones(int(m.sum())), (a[m], b[m])), shape=(len(wet),) * 2)
        n, lab = connected_components(g, directed=False)
        ar = np.bincount(lab, weights=area[wet], minlength=n)
        hs = hotspots()["features"]
        hid = [f["properties"]["hotspot_id"] for f in hs]
        hx, hy = _lonlat_to_utm40(np.array([f["geometry"]["coordinates"][0] for f in hs]),
                                  np.array([f["geometry"]["coordinates"][1] for f in hs]))
        for k in np.nonzero(ar >= min_area)[0]:
            cells = wet[lab == k]
            ca = area[cells]
            Fc = F[:, cells].astype(np.float32)
            a_t = ((Fc >= depth) * ca).sum(axis=1)
            dur = float((a_t >= min_area).sum() * dt)
            if dur < min_dur:
                continue
            vol = (Fc * ca).sum(axis=1)
            ip = int(vol.argmax())
            ia = int(a_t.argmax())                       # 同一时刻片内 ≥depth 的面积最大的那一帧
            ih = int(Fc.max(axis=1).argmax())            # 片内水深最大的那一帧
            after = np.nonzero(a_t[ip:] < min_area)[0]
            receded = bool(len(after))
            deep = int(cells[hmax[cells].argmax()])
            cxy = (xy[cells] * ca[:, None]).sum(axis=0) / ca.sum()
            lo, la = _utm40_to_lonlat(xy[[deep], 0], xy[[deep], 1])
            clo, cla = _utm40_to_lonlat(cxy[[0]], cxy[[1]])
            dist = np.array([np.hypot(xy[cells, 0] - hx[i], xy[cells, 1] - hy[i]).min() for i in range(len(hid))])
            order = np.argsort(dist)
            near = [{"hotspot": hid[i], "dist_m": int(round(dist[i]))} for i in order if dist[i] <= 200.0]
            points.append({
                "lon": round(float(lo[0]), 6), "lat": round(float(la[0]), 6),
                "centroid_lon": round(float(clo[0]), 6), "centroid_lat": round(float(cla[0]), 6),
                "hmax_m": round(float(hmax[deep]), 2), "ground_z_m": round(float(elev[deep]), 2),
                "area_m2": int(round(ca.sum())), "area_ge030_m2": int(round(ca[hmax[cells] >= 0.30].sum())),
                "duration_h": round(dur, 2), "vol_peak_m3": int(round(float(vol[ip]))), "t_peak_h": round(float(t[ip]), 2),
                # 三个「最大值在哪个时刻」：积水量见 t_peak_h；最大面积 = 同一时刻 ≥depth 的面积之和最大（不是各单元最大水深的并集 area_m2）
                "area_peak_m2": int(round(float(a_t[ia]))), "t_area_peak_h": round(float(t[ia]), 2),
                "t_hmax_h": round(float(t[ih]), 2),
                "receded": receded, "recede_h": round(float(after[0] * dt), 2) if receded else None,
                "vol_end_m3": int(round(float(vol[-1]))), "t_end_h": round(float(t[-1]), 1),
                "nearest_client_hotspot": hid[order[0]], "nearest_client_hotspot_dist_m": int(round(dist[order[0]])),
                "n_client_hotspots_200m": len(near), "near_hotspots": near, "adjacency": "mesh",
                "cells": cells.tolist(),
            })
    points.sort(key=lambda p: -p["vol_peak_m3"])
    for i, p in enumerate(points, 1):
        p.update({"id": f"SIM-{tag}-{i:03d}", "state": tag, "depth_threshold_m": depth, "min_area_m2": min_area,
                  "min_duration_h": min_dur, "excluded": False, "mostly_excluded": False, "exclusion_note": None,
                  "area_after_exclusion_m2": None,
                  "l3": None, "status": "已退" if p["receded"] else "未退"})
    return {
        "state": key, "depth_m": depth, "min_area_m2": min_area, "min_duration_h": min_dur, "custom": True,
        "count": len(points), "peak_vol_sum_m3": sum(p["vol_peak_m3"] for p in points),
        "not_receded": sum(not p["receded"] for p in points), "excluded": 0, "mostly_excluded": 0, "points": points,
        "note": "按所给阈值从二维结果现算（三角网共边连片）；不扣地表塘占地，没有 L3 承接与评估剔除标记。",
    }


def cell_series(F: np.ndarray, area: np.ndarray, cells: np.ndarray, depth: float, t_h: np.ndarray) -> dict:
    """一片积水（一组单元）逐帧的变化过程：积水量 Σ(水深×面积)、≥depth 的面积、片内最大水深。
    与 `ponding_custom` 的峰值积水量 / 最大面积同一算法，所以曲线峰值 = 清单里的 vol_peak_m3 / area_peak_m2。"""
    Fc = F[:, cells].astype(np.float32)
    ca = area[cells].astype(np.float64)
    return {
        "t_h": [round(float(v), 4) for v in t_h],
        "vol_m3": [round(float(v), 1) for v in (Fc * ca).sum(axis=1)],
        "area_m2": [round(float(v), 1) for v in ((Fc >= depth) * ca).sum(axis=1)],
        "depth_m": [round(float(v), 3) for v in Fc.max(axis=1)],
        "depth_threshold_m": depth, "n_cells": int(len(cells)),
    }


def point_series(key: str, lon: float, lat: float, depth: float = 0.15, min_area: float = 100.0, min_dur: float = 0.5) -> dict:
    """某个积水点随时间的积水量 / 面积 / 最大水深。积水点按经纬度对上（成果自带清单与重算清单都带 lon / lat，
    默认阈值下两者逐点一致，匹配方式同 `_add_peak_times`）；阈值要与清单当时用的一致，否则找不到。"""
    pts = ponding_custom(key, depth, min_area, min_dur)["points"]
    p = next((q for q in pts if round(q["lon"], 5) == round(lon, 5) and round(q["lat"], 5) == round(lat, 5)), None)
    if p is None:
        raise RdaError("找不到这个积水点（态或阈值变了？）")
    c = _cells(key)
    return {"id": p["id"], "state": key, "vol_peak_m3": p["vol_peak_m3"], "t_peak_h": p["t_peak_h"],
            **cell_series(c["frames"], c["area"], np.asarray(p["cells"], dtype=np.int64), depth, c["t_h"])}


@lru_cache(maxsize=1)
def _exclusion_rings() -> list[np.ndarray]:
    """评估剔除区的多边形环（UTM 40N 坐标）。"""
    rings = []
    for f in exclusion_zones()["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            ll = np.array(poly[0], dtype=np.float64)
            x, y = _lonlat_to_utm40(ll[:, 0], ll[:, 1])
            rings.append(np.stack([x, y], 1))
    return rings


@lru_cache(maxsize=4)
def _exclusion_mask(key: str) -> np.ndarray:
    """各单元是否落在评估剔除区内（整张网一次，热点圆内积水量与单热点过程共用）。"""
    from . import hotspot_buffer as hb

    c = _cells(key)
    return hb.points_in_polygons(c["xy"][:, 0], c["xy"][:, 1], _exclusion_rings())


def hotspot_circle_series(key: str, hotspot_id: str, depth: float = 0.15) -> dict:
    """某个客户热点 200 m 圆内逐帧的积水量 / 面积 / 最大水深（扣评估剔除区；口径同 `hotspot_buffer`，曲线峰值 = 清单里的圆内峰值积水量）。"""
    from . import hotspot_buffer as hb

    hb.check_depth(depth)
    f = next((x for x in hotspots()["features"] if x["properties"]["hotspot_id"] == hotspot_id), None)
    if f is None:
        raise RdaError("找不到这个热点")
    hx, hy = _lonlat_to_utm40(np.array([f["geometry"]["coordinates"][0]]), np.array([f["geometry"]["coordinates"][1]]))
    c = _cells(key)
    return {"id": hotspot_id, "state": key,
            **hb.circle_series(c["xy"], c["area"], c["frames"], c["t_h"], (float(hx[0]), float(hy[0])), depth, skip=_exclusion_mask(key))}


@lru_cache(maxsize=32)
def hotspot_buffer(key: str, depth: float = 0.15) -> dict:
    """客户热点 200 m 圆内的地表峰值积水量（水深 ≥ depth 的单元），及各热点圆的重叠；口径见 `hotspot_buffer.py`。
    落在评估剔除区内的单元不算（与积水点覆盖「剔除多半在剔除区的片」同口径）。"""
    from . import hotspot_buffer as hb

    hb.check_depth(depth)
    c = _cells(key)
    hs = hotspots()["features"]
    hx, hy = _lonlat_to_utm40(np.array([f["geometry"]["coordinates"][0] for f in hs]),
                              np.array([f["geometry"]["coordinates"][1] for f in hs]))
    skip = _exclusion_mask(key)
    out = hb.buffer_stats(c["xy"], c["area"], c["frames"], c["t_h"], np.stack([hx, hy], 1),
                          [f["properties"]["hotspot_id"] for f in hs], depth, skip=skip)
    out["state"] = key
    out["n_excluded_cells"] = int(skip.sum())
    return out
