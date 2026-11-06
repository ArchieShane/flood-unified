"""从 SewerGEMS 模型（.stsw.sqlite）里抽出某个方案备选新加的设施：L2 / 计划措施治理的原始层（标准 v0.2 第 4 章、7.2）。

  python -m tools.dgov.l2_extract --extent rda

做法：比较「方案的拓扑备选」和「Base Active Topology」，只在方案里启用的元素 = 方案新加的设施。
逐个取几何（库里是英尺，换成米）和物理属性（管径、管底、泵的设计流量和扬程、塘的库容），按计算范围裁剪。
来源模型和备选在 config/l2_sources.yaml 里登记。产物：out/dgov/l2/<extent>_l2_raw.gpkg（point / line / polygon 三层）。
置信度一律 C（顾问模型）；阶段、级别不在模型里，取自热点表和客户确认，另行补（见 RD-A 数据现状分析）。
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import shapely

from .common import OUT, ROOT, lineage, load, sha256

FT = 0.3048
CFS_LPS = 28.316846592
ACRE_M2 = 4046.8564224
# 元素类别：按 ElementID 出现在哪张表里判断
CLASSES = ["Conduit", "PressurePipe", "Outfall", "Manhole", "PressureJunction", "WetWell", "StandardPump", "Pond", "Catchment"]


def _q(c, sql, *a):
    return c.execute(sql, a).fetchall()


def extract(db, alts, extent_geom, log=print):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    lab = dict(_q(c, "select ElementID, Label from HMIModelingElement"))
    alt_ids = {a for (a,) in _q(c, "select AlternativeID from HMIAlternative where IsDeleted=0")}
    names = {lab[a].strip(): a for a in alt_ids if lab.get(a)}          # 只在备选里找名字（情景有同名的）
    tables = {r[0] for r in _q(c, "select name from sqlite_master where type='table'")}
    cls = {}
    for t in CLASSES:
        if t in tables:
            for (e,) in _q(c, f'select DomainElementID from "{t}"'):
                cls[e] = t
    base = [a for a, t in _q(c, "select AlternativeID, AlternativeTypeID from HMIAlternative where IsDeleted=0") if t == 3 and lab.get(a) == "Base Active Topology"][0]
    phys = [a for a, t in _q(c, "select AlternativeID, AlternativeTypeID from HMIAlternative where IsDeleted=0") if t == 4 and lab.get(a) == "Base Physical"][0]
    # 几何
    geom, outline = {}, {}            # 塘既是节点（点）又有轮廓（面）：两样都留
    for t in ("BaseNode", "BaseDirectedNode", "BaseLink", "BasePolygon"):
        for e, g in _q(c, f'select DomainElementID, HMIGeometry from "{t}_HmiDataSetGeometry_Data"'):
            if g is not None:
                gg = shapely.transform(shapely.from_wkb(bytes(g)), lambda xy: xy * FT)
                if t == "BasePolygon" and e in geom:
                    outline[e] = gg
                else:
                    geom.setdefault(e, gg) if t == "BasePolygon" else geom.__setitem__(e, gg)
    # 物理属性
    dia = {e: d * FT * 1000 for e, d in _q(c, "select DomainElementID, ConduitDiameter from Conduit_Physical_Data where AlternativeID=?", phys)}
    dia.update({e: d * FT * 1000 for e, d in _q(c, "select DomainElementID, Physical_PipeDiameter from PressurePipe_Physical_Data where AlternativeID=?", phys)})
    mann = dict(_q(c, "select DomainElementID, Physical_ManningsN from GravityLinkBase_Physical_Data where AlternativeID=?", phys))
    inv = {e: (u * FT, d * FT) for e, u, d in _q(c, "select DomainElementID, Physical_UpstreamInvert, Physical_DownstreamInvert from PhysicalLink_Physical_Data where AlternativeID=?", phys)}
    ninv = {e: v * FT for e, v in _q(c, "select DomainElementID, Physical_InvertElevation from GravityNode_Physical_Data where AlternativeID=?", phys)}
    # 压力管汇合点、泵（有向节点）、湿井的高程在各自的表里（2026-10-23 补：之前漏了，发布关口 G-02 拦下）
    ninv.update({e: v * FT for e, v in _q(c, "select DomainElementID, Physical_PressureJunctionElevation from PressureJunction_Physical_Data where AlternativeID=?", phys)})
    ninv.update({e: v * FT for e, v in _q(c, "select DomainElementID, Physical_InvertElevation from BaseDirectedNode_Physical_Data where AlternativeID=?", phys) if v is not None})
    if "WetWell_Physical_Data" in tables:
        ninv.update({e: v * FT for e, v in _q(c, "select DomainElementID, Physical_TankBaseElevation from WetWell_Physical_Data where AlternativeID=?", phys)})
    pdef = dict(_q(c, "select DomainElementID, Physical_PumpDefinitionID from StandardPump_Physical_Data where AlternativeID=?", phys))
    pd_q = {s: (q * CFS_LPS, h * FT) for s, q, h in _q(c, "select SupportElementID, DesignDischarge, DesignHead from IdahoPumpDefinition")}
    pcurve = collections.defaultdict(list)
    for s, q, h in _q(c, "select SupportElementID, PumpCurve_Discharge, PumpCurve_Head from IdahoPumpDefinition_PumpCurve"):
        pcurve[s].append((q * CFS_LPS, h * FT))
    onoff = {e: (a * FT, b * FT) for e, a, b in _q(c, "select DomainElementID, Operational_OnElevation, Operational_OffElevation from StandardPump_Operational_Data")}
    vol, curve_rows = {}, {}
    if "Pond_Physical_ElevationAreaListItem" in tables:
        ea = collections.defaultdict(list)
        for e, z, a, vs in _q(c, "select DomainElementID, ElevationAreaListItem_PondElevation, ElevationAreaListItem_PondArea, ElevationAreaListItem_VoidSpace from Pond_Physical_ElevationAreaListItem where AlternativeID=?", phys):
            ea[e].append((z * FT, a * ACRE_M2, vs / 100.0))
        for e, rows in ea.items():
            rows.sort()
            curve_rows[e] = rows
            # 每段的孔隙率取两端较小的：格室顶上那一段孔隙率为 0（覆土），不算储水
            v = sum((rows[i + 1][0] - rows[i][0]) * (rows[i][1] + rows[i + 1][1]) / 2 * min(rows[i][2], rows[i + 1][2]) for i in range(len(rows) - 1))
            vol[e] = (v, rows[0][0], rows[-1][0], max(r[1] for r in rows), rows[0][2])
    # 拓扑：管的起止节点（库里按元素编号记，不按几何）；出水口的边界元素（格室：出水口 → 塘）
    topo = {e: (a_, b_) for e, _, a_, b_ in _q(c, "select DomainElementID, AlternativeID, HMITopologyStartNodeID, HMITopologyStopNodeID from BaseLink_HMIDataSetTopology_Data")}
    bnd = {}
    if "Outfall_BoundaryCondition_BoundaryConditionType_BoundaryElementType" in tables:
        bnd = {e: t for e, _, t in _q(c, "select * from Outfall_BoundaryCondition_BoundaryConditionType_BoundaryElementType") if t}
    out = []
    for alt_name in alts:
        alt = names[alt_name]
        on = set()
        for t in ("BaseLink", "BaseNode", "BasePolygon", "BaseDirectedNode"):
            by = collections.defaultdict(dict)
            for e, a, v in _q(c, f'select DomainElementID, AlternativeID, HMIActiveTopologyIsActive from "{t}_HMIActiveTopology_Data"'):
                by[e][a] = v
            on |= {e for e, d in by.items() if d.get(alt) == 1 and d.get(base, 1) == 0}
        keep = {e for e in on if geom.get(e) is not None and geom[e].intersects(extent_geom)}
        for e in list(keep):
            if e in topo:
                keep |= {n for n in topo[e] if n in on and geom.get(n) is not None}   # 管引用的方案节点，在范围外也要（如 TIP 的出水口 O-13）
        for e in keep:
            g = geom.get(e)
            k = cls.get(e) or ("PondOutletStructure" if str(lab.get(e, "")).startswith("POS-") else "?")
            r = {"alt": alt_name.strip(), "element_id": e, "label": lab.get(e), "class": k, "geometry": g, "conf": "C", "src_db": str(db)}
            if e in topo:
                r["start_id"], r["stop_id"] = topo[e]
                r["start_label"], r["stop_label"] = lab.get(topo[e][0]), lab.get(topo[e][1])
                r["start_class"], r["stop_class"] = cls.get(topo[e][0]), cls.get(topo[e][1])
                sg, eg = geom.get(topo[e][0]), geom.get(topo[e][1])
                r["start_xy"] = f"{sg.x:.3f},{sg.y:.3f}" if sg is not None and sg.geom_type == "Point" else None
                r["stop_xy"] = f"{eg.x:.3f},{eg.y:.3f}" if eg is not None and eg.geom_type == "Point" else None
            if e in bnd:
                r["boundary_element_id"], r["boundary_element"] = bnd[e], lab.get(bnd[e])
            if e in dia:
                r["diameter_mm"] = round(dia[e], 0)
            if e in mann:
                r["manning_n"] = mann[e]
            if e in curve_rows:
                r["storage_curve"] = json.dumps([[round(z, 3), round(a, 1), v] for z, a, v in curve_rows[e]])
            r["outside_extent"] = not g.intersects(extent_geom)
            if e in inv:
                r["up_invert_m"], r["dn_invert_m"] = (round(x, 3) for x in inv[e])
            if e in ninv:
                r["invert_m"] = round(ninv[e], 3)
            if e in pdef:
                q, h = pd_q.get(pdef[e], (None, None))
                r["design_q_lps"] = round(q, 1) if q else None
                r["design_head_m"] = round(h, 2) if h else None
                cv = pcurve.get(pdef[e], [])
                r["curve_max_q_lps"] = round(max(x[0] for x in cv), 1) if cv else None
                if e in onoff:
                    r["on_elev_m"], r["off_elev_m"] = (round(x, 3) for x in onoff[e])
            if e in vol:
                v, zb, zt, amax, vs = vol[e]
                r.update(volume_m3=round(v, 1), bottom_m=round(zb, 3), top_m=round(zt, 3), max_area_m2=round(amax, 1), void_ratio=vs)
            out.append(r)
            if e in outline:                                    # 塘的轮廓另出一条（面层）
                out.append({**r, "geometry": outline[e], "class": k + "_outline"})
    return out


def main(argv=None):
    import geopandas as gpd
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.l2_extract")
    ap.add_argument("--extent", required=True)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load("l2_sources.yaml")[a.extent]
    terr = gpd.read_file(cfg["extent_shp"]).to_crs(32640).geometry.iloc[0]
    rows = []
    for m in cfg["models"]:
        rr = extract(Path(m["db"]), m["alternatives"], terr)
        for r in rr:
            r["model"] = m["name"]
        rows += rr
        print(f"  {m['name']}: {len(rr)} 个元素", dict(collections.Counter((r['alt'], r['class']) for r in rr)))
    G = gpd.GeoDataFrame(rows, geometry="geometry", crs=32640)
    dst = OUT / "l2" / f"{a.extent}_l2_raw.gpkg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    for gt, name in (("Point", "point"), ("LineString", "line"), ("Polygon", "polygon")):
        sub = G[G.geom_type.str.contains(gt)]
        if len(sub):
            sub.to_file(dst, layer=name, driver="GPKG")
    lineage("l2_extract", [Path(m["db"]) for m in cfg["models"]], [dst], {"extent": a.extent, "models": [{k: m[k] for k in ("name", "alternatives")} for m in cfg["models"]]},
            notes=f"{len(G)} 个元素")
    print(f"→ {dst}")


if __name__ == "__main__":
    main()
