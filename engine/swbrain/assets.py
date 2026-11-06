# -*- coding: utf-8 -*-
"""地表水管理资产台账（客户图上的 SW Assets）：客户 L1/L2/L3 分级里的实体资产。

客户（DMT）把地表水管理资源分三级：

    L1  既有永久设施      管线、泵站、出水口、雨水口、坝、干河谷、明渠、生态格室、塘
    L2  雨前临时/过渡措施   按交付方分三条线：
          CAPEX  塘、地面管、溢流堰、泵站、生态格室
          IDD    泵、土堤、明渠
          市政    塘、地面管、Jersey 护栏、沙袋、小型挡水、土堤、固定泵
    L3  雨中应急资源      罐车、移动泵、吊车

本体里原先只有三样对得上：管段（Reach）= L1 管线，泵/罐车（PumpUnit）= L3 移动泵与罐车。
其余二十多项一个都没有，L2 整层是空的——而 L2 正是第二道防线的主体。

这个模块把它们收成一个 Asset 类型，不给每种资产建一个类：种类差异放 `kind`，
分级放 `level`，谁出钱谁建放 `delivery`。这样一个类型覆盖图上全部资产，
而且客户以后加新种类（比如渗井、调蓄池）不用改本体结构。

**现在有数据的只有塘。** 其余种类的台账客户还没给（见假设总账 asset_register）。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 分级：与客户 2026-09-25 那张 Surface Water Management Levels 图一致
LEVELS = {
    "L1": ("既有永久设施", "Existing / permanent"),
    "L2": ("雨前临时 / 过渡措施", "Intermediate / temporary"),
    "L3": ("雨中应急资源", "ERP / during event"),
}

# 交付方：客户把 L2 按这三条线分开列，因为出钱、施工、运维的主体不同
DELIVERY = {
    "capex": ("资本项目", "CAPEX"),
    "idd": ("IDD", "IDD"),
    "municipality": ("市政", "Municipality"),
    "erp": ("应急响应", "ERP"),
    "existing": ("既有", "Existing"),
}

# 种类 → (中文, 英文, 缺省层级)。覆盖客户图上全部 20 种资产。
KINDS = {
    "pipeline":        ("管线", "Pipeline", "L1"),
    "pump_station":    ("泵站", "Pump station", "L1"),
    "outfall":         ("出水口", "Outfall", "L1"),
    "inlet":           ("雨水口", "Inlet", "L1"),
    "dam":             ("坝", "Dam", "L1"),
    "wadi":            ("干河谷", "Wadi", "L1"),
    "ditch":           ("明渠", "Ditch", "L1"),
    "geocell":         ("生态格室", "Geocell", "L1"),
    "soakaway":        ("渗井", "Soakaway", "L1"),
    "pond":            ("蓄水塘", "Pond", "L2"),
    "surface_pipeline": ("地面管线", "Surface pipeline", "L2"),
    "spillway":        ("溢流堰", "Spillway", "L2"),
    "bund":            ("土堤", "Bund", "L2"),
    "jersey_barrier":  ("Jersey 护栏", "Jersey barrier", "L2"),
    "sandbag":         ("沙袋", "Sand bag", "L2"),
    "small_barrier":   ("小型挡水", "Small barrier", "L2"),
    "stationary_pump": ("固定泵", "Stationary pump", "L2"),
    "tanker":          ("罐车", "Tanker", "L3"),
    "mobile_pump":     ("移动泵", "Mobile pump", "L3"),
    "crane":           ("吊车", "Crane", "L3"),
}

# 管网排放终点的类型（cs.parquet.terminal_kind）→ 资产种类。
# 断头 / 封堵端不是资产，是管网数据的断裂，不收进来。
TERMINAL_KIND = {"出水口": "outfall", "泵站": "pump_station", "渗井": "soakaway"}
NOT_ASSETS = {"断头", "封堵端"}

# 这三种已经有专门的本体对象类型，不在 Asset 里重复建模——只在这里标明归属，
# 免得「缺数清单」把它们也算进去。
COVERED_BY = {"pipeline": "Reach", "mobile_pump": "PumpUnit", "tanker": "PumpUnit"}

# 状态机沿用 Proposal 那套，便于「建议 → 设施」接上
STATUSES = ("planned", "approved", "under_construction", "built", "verified", "rejected")
STATUS_ZH = {"planned": "规划中", "approved": "已批准", "under_construction": "在建",
             "built": "已建成", "verified": "已验证有效", "rejected": "已否决"}

# 雨水口只收热点附近的：全量 194,510 个不适合装进本体。
# 半径与上限可在 config.toml 的 [assets] 里调。
DEFAULTS = {"inlet_radius_m": 200.0, "inlet_max": 40000}

# 来源：这一行数据是谁给的，决定可信度
SOURCES = {
    "client_register": "客户资产台账",
    "client_model": "客户 1D 水力模型",
    "client_report": "顾问评估报告",
    "system_plan": "本系统规划（swbrain.ponds）",
}


def _row(fid, kind, *, level=None, delivery="existing", name=None, name_en=None,
         lon=None, lat=None, source="client_register", **kw) -> dict:
    zh, en, lv = KINDS.get(kind, (kind, kind, "L1"))
    d = {"asset_id": str(fid), "kind": kind, "kind_zh": zh, "kind_en": en,
         "level": level or lv, "delivery": delivery,
         "name": name or str(fid), "name_en": name_en or name or str(fid),
         "lon": None if lon is None else float(lon), "lat": None if lat is None else float(lat),
         "status": "planned", "source": source, "source_zh": SOURCES.get(source, source),
         "capacity_m3": None, "intake_m3h": None, "footprint_m2": None, "cost_aed": None,
         "zone_name": None, "area": None, "model_ref": None, "serves_fp_ids": [], "note": None,
         "catchment_m2": None, "n_inlets": None, "load_ratio": {}, "tide_clearance_m": None,
         "outfall_type": None, "subkind": None, "runoff_c": None}
    d.update({k: v for k, v in kw.items() if k in d or k in
              ("capacity_m3", "intake_m3h", "footprint_m2", "cost_aed", "zone_name",
               "area", "model_ref", "serves_fp_ids", "note", "status",
               "catchment_m2", "n_inlets", "load_ratio", "tide_clearance_m", "outfall_type",
               "subkind", "runoff_c")})
    return d


def _network_terminals(root: Path) -> list[dict]:
    """管网排放终点 = L1 既有资产（出水口 / 泵站 / 渗井）。

    负荷比 load_ratio 用的是 cs.parquet 的 max_ratio_*（该终点汇水范围内最吃紧管段的
    流量/能力）。**不要把它当成终点本身的通过能力**——2026-09-24 查过，源库里没有终点
    吞吐量，拿管径反推出来的值和 max_ratio 差两个量级（见假设总账 terminal_capacity）。
    它能回答「这一片管网吃不吃得消」，回答不了「这个出水口还能再接多少」。
    """
    f = root / "out" / "state" / "cs.parquet"
    if not f.exists():
        return []
    try:
        import pandas as pd
        d = pd.read_parquet(f)
    except Exception:
        return []
    out = []
    for r in d.itertuples(index=False):
        kind = TERMINAL_KIND.get(str(getattr(r, "terminal_kind", "")))
        if not kind or not _fin(getattr(r, "lon", None)):
            continue
        uid = getattr(r, "terminal_unitid", None) or f"T{getattr(r, 'terminal', '')}"
        ratios = {k: round(float(getattr(r, f"max_ratio_{k}")), 3)
                  for k in ("常规", "强降雨", "极端", "百年一遇")
                  if _fin(getattr(r, f"max_ratio_{k}", None))}
        out.append(_row(uid, kind, level="L1", delivery="existing",
                        name=str(uid), lon=r.lon, lat=r.lat, source="client_register",
                        zone_name=getattr(r, "zone_name", None),
                        status="built",
                        model_ref=str(getattr(r, "terminal", "")),
                        catchment_m2=_num(getattr(r, "area_m2", None)),
                        n_inlets=_num(getattr(r, "n_inlets", None)),
                        load_ratio=ratios,
                        tide_clearance_m=_num(getattr(r, "tide_clearance_m", None)),
                        outfall_type=(getattr(r, "outfall_type", None) or None),
                        note="来自管网台账汇总 cs.parquet"))
    return out


def _fin(x) -> bool:
    try:
        return x is not None and float(x) == float(x)
    except (TypeError, ValueError):
        return False


def _num(x):
    return round(float(x), 2) if _fin(x) else None


def _inlets_near_points(root: Path, cfg: dict | None = None) -> list[dict]:
    """热点附近的雨水口 = L1 既有资产。

    全量 194,510 个（vertices.node_table 为 inlet / catchbasin），不适合都装进本体，
    只收热点半径内的。缺省 200 m——实测每个热点中位 28 个、合计 13,794 个（占全量 7%），
    量级可控又够描述局部进水能力。每个带 DTM 汇水面积与径流系数
    （out/state/inlet_catchments.parquet），所以「这个点周围能收多少水」是算得出来的。
    """
    P = {**DEFAULTS, **((cfg or {}).get("assets") or {})}
    st = root / "out" / "state"
    fv, fc, fh = st / "vertices.parquet", st / "inlet_catchments.parquet", st / "hotspots_enriched.parquet"
    if not (fv.exists() and fc.exists() and fh.exists()):
        return []
    try:
        import numpy as np
        import pandas as pd
        from scipy.spatial import cKDTree
        from pyproj import Transformer
        v = pd.read_parquet(fv, columns=["id", "x", "y", "node_table"])
        ic = pd.read_parquet(fc, columns=["vertex_id", "area_m2", "c"])
        hs = pd.read_parquet(fh, columns=["hotspot_id", "lon", "lat"]).dropna(subset=["lon", "lat"])
    except Exception:
        return []
    if not len(hs):
        return []
    inl = v[v["id"].isin(ic["vertex_id"])].merge(ic, left_on="id", right_on="vertex_id", how="inner")
    inl = inl.dropna(subset=["x", "y"])
    if not len(inl):
        return []
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    hx, hy = tr.transform(hs["lon"].values.astype(float), hs["lat"].values.astype(float))
    tree = cKDTree(np.c_[inl["x"].values, inl["y"].values])
    near = tree.query_ball_point(np.c_[hx, hy], float(P["inlet_radius_m"]))
    serves: dict[int, list[str]] = {}
    for hid, idxs in zip(hs["hotspot_id"].astype(str), near):
        for i in idxs:
            serves.setdefault(int(i), []).append(hid)
    if len(serves) > int(P["inlet_max"]):                      # 上限兜底，先收服务热点多的
        keep = sorted(serves, key=lambda i: -len(serves[i]))[:int(P["inlet_max"])]
        serves = {i: serves[i] for i in keep}
    inv = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    out = []
    for i, fps in serves.items():
        r = inl.iloc[i]
        lon, lat = inv.transform(float(r["x"]), float(r["y"]))
        out.append(_row(f"INL-{int(r['id'])}", "inlet", level="L1", delivery="existing",
                        name=f"雨水口 {int(r['id'])}", lon=lon, lat=lat,
                        source="client_register", status="built",
                        subkind=str(r["node_table"]),
                        catchment_m2=_num(r["area_m2"]), runoff_c=_num(r["c"]),
                        serves_fp_ids=sorted(set(fps)),
                        model_ref=str(int(r["id"])),
                        note=f"热点 {float(P['inlet_radius_m']):.0f} m 内；汇水面积与径流系数来自 DTM 填洼"))
    return out


def _client_register(root: Path) -> list[dict]:
    """客户自己的资产台账（exchange/assets.json）。

    这是长期该走的路——客户给一份全量台账，L1/L2/L3 二十多种资产一次到位。
    现在这个文件还不存在，留好接口。
    """
    f = root / "exchange" / "assets.json"
    if not f.exists():
        return []
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = raw.get("facilities") if isinstance(raw, dict) else raw
    out = []
    for r in (items or []):
        k = str(r.get("kind") or "").strip().lower()
        if k not in KINDS:
            continue
        out.append(_row(r.get("asset_id") or r.get("id"), k,
                        level=r.get("level"), delivery=r.get("delivery") or "existing",
                        name=r.get("name"), name_en=r.get("name_en"),
                        lon=r.get("lon"), lat=r.get("lat"), source="client_register",
                        capacity_m3=r.get("capacity_m3"), intake_m3h=r.get("intake_m3h"),
                        footprint_m2=r.get("footprint_m2"), cost_aed=r.get("cost_aed"),
                        zone_name=r.get("zone_name"), area=r.get("area"),
                        status=r.get("status") if r.get("status") in STATUSES else "built"))
    return out


def _model_ponds(root: Path) -> list[dict]:
    """客户 1D 水力模型里的临时塘（Parsons 2027 过渡方案）= L2 / CAPEX。"""
    f = root / "out" / "dmt_model" / "ponds_sites.json"
    if not f.exists():
        return []
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for r in raw:
        if r.get("lon") is None:
            continue
        cap = r.get("max_stor_m3")
        out.append(_row(r["site_id"], "pond", level="L2", delivery="capex",
                        name=r.get("name"), lon=r["lon"], lat=r["lat"],
                        source="client_model", capacity_m3=cap,
                        footprint_m2=r.get("area_m2"), area=r.get("area"),
                        model_ref=r.get("swmm_label") or r.get("model"),
                        # planned 而不是 approved：Parsons 2026-09-17 的报告是**给 DMT 的建议书**，
                        # 不是竣工资料。三份报告都写明既有系统里一个塘都没有
                        # （"no NMDC pumps or ponds have been found for the system recently"），
                        # 且塘「proposed within available open spaces, subject to site suitability」——
                        # 地块还没定、泵还没采购（"Procure and position 33 number of 12-inch mobile pumps"）。
                        # DMT 批没批我们不知道，所以只能记 planned。
                        status="planned",
                        note=f"Parsons 2027 过渡方案拟建；库容来源 {r.get('stor_source') or '未标'}"))
    return out


def _system_ponds(root: Path) -> list[dict]:
    """本系统规划出来的塘（swbrain.ponds 的产物）= L2+ 建议，状态 planned。"""
    out = []
    for f in sorted((root / "out").glob("ponds_*.json")):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        rp = f.stem.replace("ponds_", "")
        for z in (raw.get("chosen") or []):
            s = z.get("site") or {}
            if s.get("lon") is None:
                continue
            out.append(_row(f"PLAN-{rp}-{s.get('site_id')}", "pond", level="L2",
                            delivery="capex", name=s.get("name") or s.get("site_id"),
                            lon=s["lon"], lat=s["lat"], source="system_plan",
                            capacity_m3=z.get("volume_m3"), intake_m3h=z.get("capacity_m3h"),
                            footprint_m2=z.get("footprint_m2"), cost_aed=z.get("cost_aed"),
                            area=s.get("area"), serves_fp_ids=list(z.get("points") or []),
                            status="planned", note=z.get("label")))
    return out


def load(root: Path | None = None, cfg: dict | None = None) -> list[dict]:
    """全部资产，按 asset_id 去重。后面的来源覆盖前面的：

        管网终点 → 雨水口 → 本系统规划的塘 → 客户 1D 模型的塘 → 客户台账

    客户自己的台账优先级最高，我们从管网 / 模型 / 规划里推出来的垫底。
    """
    root = root or ROOT
    rows: dict[str, dict] = {}
    src = (_network_terminals, lambda p: _inlets_near_points(p, cfg),
           _system_ponds, _model_ponds, _client_register)
    for fn in src:
        for r in fn(root):
            rows[r["asset_id"]] = r
    return sorted(rows.values(), key=lambda r: (r["level"], r["kind"], r["asset_id"]))


def summary(rows: list[dict] | None = None, root: Path | None = None,
            cfg: dict | None = None) -> dict:
    rows = load(root, cfg) if rows is None else rows
    by_level, by_kind, by_source = {}, {}, {}
    for r in rows:
        by_level[r["level"]] = by_level.get(r["level"], 0) + 1
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
    missing = [k for k in KINDS if k not in by_kind and k not in COVERED_BY]
    return {"n": len(rows), "by_level": by_level, "by_kind": by_kind, "by_source": by_source,
            "capacity_m3": round(sum(float(r["capacity_m3"] or 0) for r in rows), 0),
            "kinds_declared": len(KINDS), "kinds_with_data": len(by_kind),
            "kinds_elsewhere": dict(COVERED_BY), "kinds_missing": missing}


def main(argv=None):
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    rows = load()
    s = summary(rows)
    print(f"排水资产 {s['n']} 件；声明了 {s['kinds_declared']} 种资产，有数据的 {s['kinds_with_data']} 种")
    print(f"  按层级 {s['by_level']}")
    print(f"  按种类 {s['by_kind']}")
    print(f"  按来源 {s['by_source']}")
    print(f"  库容合计 {s['capacity_m3']:,.0f} m³")
    print(f"\n已由别的对象类型承载的 {len(COVERED_BY)} 种："
          + "、".join(f"{KINDS[k][0]}→{v}" for k, v in COVERED_BY.items()))
    print(f"没有任何数据的 {len(s['kinds_missing'])} 种："
          + "、".join(f"{KINDS[k][0]}" for k in s["kinds_missing"]))
    print("  → 客户给 exchange/assets.json 全量台账即可一次补齐（见假设总账 asset_register）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
