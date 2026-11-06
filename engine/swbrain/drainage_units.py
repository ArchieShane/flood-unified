"""汇水区单元与分析范围（本体 Catchment / AnalysisDomain 的数据层，2026-09-29）。

为什么要有：客户 flood 库的 catchment.catchment（39 个面）漏了 ADM-94 / ADM-192 / ADM-487，是用户看图发现的；
本体里只有积水点、没有汇水区，这种漏登没有规则能抓。这里把散在各处的几套汇水区收成一个对象集，每个带来源与可信度：

  client_layer  客户 QGIS 里的 Catchment 图层（OBJECTID / Old_Number / New_Number / HYDROID），我们没有原始数据，
                按截图描绘（tools/photo_catch_trace.py、c1_units_export.py）：RB-3、MBZ、ADGC-10，边界误差几十米
  kmz           客户邮件附件 Hot Spots 506 的 4 个汇水区 KMZ（169 个面，面之间有重叠）
  db39          客户 flood 库 catchment.catchment：39 个建模研究范围（不是全市排水分区），已知漏 3 个重点热点

分析范围（AnalysisDomain）：一次一二维计算用的范围，带「应当包含的热点」与对应的模型情景名，能核对漏了谁。
几何一律 UTM 40N（EPSG:32640）；缺文件的来源跳过（不报错），来源清单见 SOURCES。
"""
from __future__ import annotations

import json
from pathlib import Path

SOURCES = {
    "model": {"zh": "客户水力模型子汇水区（22 个模型）", "en": "client hydraulic-model sub-catchments (22 models)", "rank": 0,
              "confidence": "客户自己建模用的子汇水区拼成，最可信（用户 2026-09-30 定为准）；只覆盖 22 个模型的范围", "file": "out/units_from_models/model_units.geojson"},
    "client_layer": {"zh": "客户 Catchment 图层（按截图描绘）", "en": "client Catchment layer (traced from screenshots)", "rank": 1,
                     "confidence": "手描（按截图描绘，边界误差几十米到上百米）；用户 2026-09-30 定以模型为准，这套降为参考", "file": "out/交付/C1汇水区_客户单元/C1_客户单元_3个面.geojson"},
    "kmz": {"zh": "客户邮件 KMZ（Hot Spots 506）", "en": "client e-mail KMZ (Hot Spots 506)", "rank": 2,
            "confidence": "客户原始；面之间有重叠", "file": "console/data/catchments_kmz.geojson"},
    "db39": {"zh": "客户 flood 库 catchment.catchment（39 个建模研究范围）", "en": "client flood DB catchment.catchment (39 study areas)", "rank": 3,
             "confidence": "手描（用户 2026-09-30）；是建模研究范围不是排水分区，已知漏 ADM-94 / 192 / 487", "file": "console/data/catchments_sw.geojson"},
}
DOMAINS = {
    "C1": {"zh": "C1 计算域（8 个热点汇水区外扩 300 m）", "file": "out/c1_domain.geojson", "model_tags": ["C1_R61", "C1_R96", "C1_D24"],
           "expected": ["ADM-21", "ADM-90", "ADM-94", "ADM-104", "ADM-192", "ADM-202", "ADM-486", "ADM-487"], "units": []},
    "C1M": {"zh": "C1 模型版（RB3 + Musaffah_RD09 + B_C 三个模型的子汇水区）", "file": "out/units_from_models/c1_model_domain.geojson", "model_tags": ["C1_M61"],
            "expected": ["ADM-21", "ADM-90", "ADM-94", "ADM-104", "ADM-192", "ADM-202", "ADM-486", "ADM-487"], "units": ["MODEL:RB3", "MODEL:Musaffah_RD09", "MODEL:B_C"]},
    "C1U": {"zh": "C1 客户单元版（RB-3 + MBZ + ADGC-10 融合）", "file": "out/交付/C1汇水区_客户单元/C1_汇水区_合并.geojson", "model_tags": ["C1_U61"],
            "expected": ["ADM-21", "ADM-90", "ADM-94", "ADM-104", "ADM-192", "ADM-202", "ADM-486", "ADM-487"], "units": ["CL:147", "CL:31", "CL:86"]},
}


def _read(root: Path, rel: str):
    import geopandas as gpd
    p = root / rel
    if not p.exists():
        return None
    g = gpd.read_file(p)
    g = g.set_crs(4326, allow_override=True) if g.crs is None else g
    return g.to_crs(32640)


def _centroid_ll(geom):
    from pyproj import Transformer
    c = geom.representative_point()
    lon, lat = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True).transform(c.x, c.y)
    return round(lon, 6), round(lat, 6)


def load_units(root) -> dict[str, dict]:
    """{catchment_id: 属性 + _geom（UTM shapely）}"""
    root = Path(root); out: dict[str, dict] = {}
    g = _read(root, SOURCES["model"]["file"])
    if g is not None:
        for r in g.itertuples():
            out[r.unit_id] = {"catchment_id": r.unit_id, "name": r.name, "title": f"{r.model}（{'模型整片' if r.level == 'model' else '按出水口分区'}）", "source": "model",
                              "model": r.model, "level": r.level, "n_sub": int(r.n_sub), "_geom": r.geometry}
    g = _read(root, SOURCES["client_layer"]["file"])
    if g is not None:
        for r in g.itertuples():
            cid = f"CL:{int(r.OBJECTID)}"
            out[cid] = {"catchment_id": cid, "name": r.New_Number, "title": getattr(r, "Title", None), "source": "client_layer",
                        "objectid": int(r.OBJECTID), "new_number": r.New_Number, "old_number": getattr(r, "Old_Number", None), "hydroid": getattr(r, "HYDROID", None),
                        "shape_area_m2": getattr(r, "Shape_Area", None), "_geom": r.geometry}
    g = _read(root, SOURCES["kmz"]["file"])
    if g is not None:
        for i, r in enumerate(g.itertuples()):
            nm = (getattr(r, "name", None) or getattr(r, "label", None) or f"#{i}")
            cid = f"KMZ:{i}"
            out[cid] = {"catchment_id": cid, "name": str(nm), "title": str(nm), "source": "kmz", "group": getattr(r, "group", None), "_geom": r.geometry}
    g = _read(root, SOURCES["db39"]["file"])
    if g is not None:
        for r in g.itertuples():
            cid = f"DB:{r.id}"
            out[cid] = {"catchment_id": cid, "name": str(r.label), "title": str(r.label), "source": "db39", "_geom": r.geometry}
    for cid, u in out.items():
        geom = u["_geom"].buffer(0)
        u["_geom"] = geom
        S = SOURCES[u["source"]]
        u.update({"source_zh": S["zh"], "confidence": S["confidence"], "area_ha": round(geom.area / 1e4, 1)})
        u["lon"], u["lat"] = _centroid_ll(geom)
    return out


def load_domains(root) -> dict[str, dict]:
    root = Path(root); out = {}
    for did, d in DOMAINS.items():
        g = _read(root, d["file"])
        if g is None:
            continue
        geom = g.geometry.union_all().buffer(0) if hasattr(g.geometry, "union_all") else g.unary_union.buffer(0)
        lon, lat = _centroid_ll(geom)
        out[did] = {"domain_id": did, "name": d["zh"], "area_km2": round(geom.area / 1e6, 2), "model_tags": d["model_tags"], "expected_fp_ids": d["expected"],
                    "unit_ids": d["units"], "source_file": d["file"], "lon": lon, "lat": lat, "_geom": geom}
    return out


class PointIndex:
    """点 → 包含它的单元（STRtree）。"""

    def __init__(self, units: dict[str, dict]):
        import shapely
        self.ids = list(units)
        self.geoms = [units[k]["_geom"] for k in self.ids]
        self.tree = shapely.STRtree(self.geoms) if self.geoms else None

    def containing(self, lon: float, lat: float) -> list[str]:
        if self.tree is None or lon is None or lat is None:
            return []
        from pyproj import Transformer
        from shapely.geometry import Point
        x, y = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True).transform(float(lon), float(lat))
        p = Point(x, y)
        return [self.ids[i] for i in self.tree.query(p, predicate="intersects")]


def public(u: dict) -> dict:
    """去掉几何，给 API / 智能体用。"""
    return {k: v for k, v in u.items() if not k.startswith("_")}


def overlaps_same_source(units: dict[str, dict], cid: str, min_frac: float = 0.01) -> list[dict]:
    """同一来源里和它重叠超过自身面积 min_frac 的单元。"""
    u = units[cid]; g = u["_geom"]; out = []
    for k, v in units.items():
        if k == cid or v["source"] != u["source"] or not g.intersects(v["_geom"]):
            continue
        a = g.intersection(v["_geom"]).area
        if a > min_frac * g.area:
            out.append({"catchment_id": k, "name": v["name"], "overlap_ha": round(a / 1e4, 1)})
    return out


def dump_summary(root) -> str:
    u = load_units(root); d = load_domains(root)
    by = {}
    for v in u.values():
        by[v["source"]] = by.get(v["source"], 0) + 1
    return json.dumps({"units": by, "domains": {k: v["area_km2"] for k, v in d.items()}}, ensure_ascii=False)
