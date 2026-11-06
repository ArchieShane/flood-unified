# -*- coding: utf-8 -*-
"""1105 治理后数据 → 区域数据包（Mussafah_00[MUS00] / ADCG6 / E14-1[E141] / RD-L[RDL] / RD-M[RDM]；MU09-MH-1861 本次不推演，用户 2026-11-06 定，待重新治理）。
本文件由 g1104_pack.py 复制改出（不改旧代码），1105 的改动：数据源换成 1105/（区域图层、全市图层同一个 GDB）；建模范围用 MODEL_BOUNDARY；子汇水区标签用 SUB_UID（SUB_ID 跨模型有重名，说明.txt）；区域图层里随带的界外出水口（CATCHMENT=汇水区外，按管网接到本区的末端设施）只要接在本区主管网上就引入，不再套 100 m 距离；region.toml 加 mem_gb_per_case（内存防护）。
以下为 g1104_pack 的说明：
本文件由 g261102_pack.py 复制改出（不改旧代码），改动：区域表与各区态；数据源拆成三处（5 区 GDB、全市管线 GDB、261101 全市图层只用来补窗口内邻区节点和界外出水口）；出水口候选 = 区域图层 ∪ 全市图层；RD-E 不引入界外出水口（用户 2026-11-05）。
以下为 g261102_pack 的说明：
以下为原说明：
只给这一批新区域用；不改 region_pack.py / region_pack_v2.py（RD-A 的打包），产出的目录布局和 RD-A 的 v2 数据包兼容，推演主脚本（run_region.py）直接读。

数据来源（用户 2026-11-01 确认的口径）：
  数据       按汇水区附属设施_治理后.gdb（顶层图层 = 全市；RD_C_* 等 = 按汇水区切出的）；DTM = 全市 5 m（服务器上换 50 cm 由 DTM_PATH 决定）
  子汇水区   治理后 SUBCATCHMENT 已带 CN / Tc / 不透水比例 / 出口节点 OUTLET：直接当 catchments.gpkg 用（LABEL=SUB_ID，OUT_NODE=OUTLET，SCS_CN=CN）。
  管线       全市 PIPELINE，不分状态、不分 ASSET_STAGE 全取（三态共用）：范围 = 计算域外扩 150 m 窗口内的管（全市管网连成一片，不整块拉进来；
             出水口「接在主管网上」也按窗口内的管网判断）。
  井 / 进水口 / 雨水口 / 封堵端   三态共用，一律算 L1（打包时 ASSET_STAGE 改成 Existing，原值留在 ASSET_STAGE_ORIG）。
  出水口     计算域内：Existing（L1/L2）和 Planned（L2planned），数据里什么样就什么样；
             计算域外 100 m 内的 Existing：只有接在本区主管网上的才引入（页面设备清单标「界外 100 m 内引入」）；接不上的不用。
             进模型时一律固定 1.3 m + 拍门（规则 outfalls.sea_level_m，同 RD-A / F 系列）。
  泵 / 塘 / 渗井 / 移动泵 / 临时泵 / Geocell   按 ASSET_STAGE 分态；缺参数的不建模、照常跑，页面设备清单标「未建模」。
  不做任何数据治理：数据缺什么就是缺什么，不补参数、不改管。

用法（本机，要 geopandas / pyogrio / rasterio / scipy）：
  python tools/g261101_pack.py --src /Volumes/ArchieShane/temp/flood/261101 --dtm /Volumes/.../AUH_DTM_5m_Z40.TIF [--regions RDC,RDF]
产出 data/regions/<ID>/{region.toml, domain.geojson, catchments.gpkg, aecom_manholes.gpkg, catchment_outlets.csv, aecom_exchange_nodes.csv, network_v2.gpkg,
  dtm_5m.tif, dem_edits.json, manifest_v2.json, manifest.json, <ID>-server.sh}；makani.gpkg 要在 109 上用 g261101_makani.py 补（需要库访问）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

# 跑哪几态（用户 2026-11-01）：按数据真实情况；ADM LINE 只跑 L1，其余三态都有数据（Temporary / Planned 设施都有）
# 数据里的临时 / 计划标记（泵 / 塘 / 移动泵 / 临时泵 / Geocell 的 ASSET_STAGE；出水口、井、管线三态共用不算）：
#   Temporary：A-A1-A2（临时泵出水）、Mussafah 01（移动泵 + 临时泵）、02、03、08、06、RD-G（临时泵）；Planned：A-A1-A2、08、10（泵 / 塘）、06（塘）、RD-G（Geocell）
STATES = {"ADCG6": ["L1"], "MUS00": ["L1", "L2"], "E141": ["L1", "L2"]}      # 1105：按图层里的 ASSET_STAGE 标注：ADCG6 没有 Temporary / Planned 设施 → 只 L1；Mussafah_00（TEMP_PUMP_SITE 1 个）、E14-1（临时泵吸水 / 排水点各 1）有 Temporary 无 Planned → L1 L2；RD-L / RD-M 有临时泵 + Planned Geocell → 三态。
_OLD_STATES_1104 = {"RDE": ["L1", "L2"]}      # 其余区域三态都有（RDI2、RB2 有 Planned 的 Geocell，所以也是三态；2026-11-05 发现，用户原话「没有 planned 设备就只跑 L1 L2」的前提不成立）
# 用户 2026-11-05：没有 Planned 设备的区只跑 L1、L2。实际核对：只有 RD-E 没有 Planned 设备；RD-I（Geocell 24 面 / 14 点）、RB2（Geocell 23 点）、RB1（泵 / 塘）、Airport（泵 / 塘）都有 → 三态，
# 其中新增设施全部没建模的态，推演时自动复制 L1 结果（run_region_1104.py：输入指纹和 L1 相同就复制，不重算）
SCENARIOS = [f"S{r}D{d}" for r in (33, 61, 96) for d in (1, 2, 24)]
# 区域 ID（job_id / 目录名）→ (页面标题, 图层前缀)
REGIONS = {
    "MUS00": ("Mussafah_00", "Mussafah_00"),
    "ADCG6": ("ADCG6", "ADCG6"),
    "E141": ("E14-1", "E14_1"),
    "RDL": ("RD-L", "RD_L"),
    "RDM": ("RD-M", "RD_M"),
}
KEEP_LARGEST: set = set()     # 用户 2026-11-05：计算域多块时只算最大块
NO_OUTSIDE: set = set()     # 用户 2026-11-05：RD-E 界外 96 m 的出水口不用（用了也没多大作用）；其余区仍按界外 100 m 规则
FAC_GDB = "1105/按汇水区附属设施_治理后.gdb"
PIPE_GDB = "1105/全市治理后数据.gdb"
CITY_GDB = "1105/全市治理后数据.gdb"      # 1104 没带全市图层：只用来补窗口内邻区节点（子汇水区出口）和界外出水口候选（区域图层里有的 UNITID 以区域图层为准）
OUTSIDE_M = 100.0        # 界外引入出水口的距离（用户 2026-11-01）
MARGIN_M = 150.0         # 管线窗口：计算域外扩这么多（含界外 100 m 内的出水口及其接管）
WINDOW_M = MARGIN_M
SEAL = ("manholes", "inlets", "catchbasins", "cappedends")   # 三态共用的节点

PIPE_COLS = ["UNITID", "STATUS", "PIPE_MATER", "PIPE_SUBTY", "PIPE_DIAME", "INVERT_LEV", "INVERT_L_1", "PIPE_LENGT", "ASSET_STAGE", "STAGE_SRC", "START_ASSE", "END_ASSET_"]
NODE_COLS = ["UNITID", "STATUS", "INVERT_LEV", "COVER_LEVE", "ASSET_STAGE", "STAGE_SRC"]
PT_LAYERS = {
    "manholes": ("MANHOLECHAMBER", NODE_COLS), "inlets": ("INLET", NODE_COLS), "catchbasins": ("CATCHBASIN", NODE_COLS), "cappedends": ("SW_CAPPEDEND", NODE_COLS),
    "outfalls": ("OUTFALL", ["UNITID", "STATUS", "INVERT_LEV", "BND_TYPE", "BND_STAGE", "FLAP_GATE", "ASSET_STAGE", "STAGE_SRC"]),
    "pumps": ("PUMP", ["UNITID", "STATUS", "CAP_Q_LS", "CAP_H_M", "ON_EL_M", "OFF_EL_M", "N_PUMPS", "MODEL_READY", "MISSING", "PARAM_ROUTE", "WW_INV", "WW_DEPTH", "WW_AREA",
                       "PUMP_CURVE", "ASSET_STAGE", "STAGE_SRC"]),
}
# 只用来在页面设备清单里列出（引擎不建模）的设施
EXTRA_LAYERS = {
    "ponds": ("POND", ["UNITID", "POND_NAME", "STATUS", "CAP_VOL_M3", "CAP_DEPTH_M", "CAP_AREA_M2", "BOTTOM_EL_M", "MODEL_READY", "MISSING", "PARAM_ROUTE", "ASSET_STAGE", "STAGE_SRC"]),
    "soakaways": ("SOAKAWAY", ["UNITID", "STATUS", "INVERT_LEV", "COVER_LEVE", "ASSET_STAGE", "STAGE_SRC"]),
    "movable_pumps": ("MOVABLE_PUMP", ["UNITID", "STATUS", "FLOW_RATE", "HEAD", "POWER", "FUELTYPE", "ASSET_STAGE", "STAGE_SRC"]),
    "wetwells": ("WETWELL", ["UNITID", "STATUS", "INVERT_LEV", "INIT_ELEV", "MAX_ELEV", "ASSET_STAGE", "STAGE_SRC"]),
}
TEMP_LAYERS = {"temp_pump_suction": "TEMP_PUMP_SUCTION", "temp_pump_discharge": "TEMP_PUMP_DISCHARGE", "temp_pump_site": "TEMP_PUMP_SITE"}
GEO_LAYERS = {"geocell": "GEOCELL_AREA", "geocell_points": "GEOCELL"}


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def force2d(g):
    import shapely
    g = g.copy()
    g["geometry"] = shapely.force_2d(g.geometry.values)
    return g


def rd(gdb, layer, cols=None, **kw):
    """读图层（缺的列补 None），统一成 UTM 40N 2D；图层不存在返回 None。"""
    import geopandas as gpd
    import pyogrio
    try:
        have = set(pyogrio.read_info(gdb, layer=layer)["fields"])
    except Exception:  # noqa: BLE001
        return None
    use = [c for c in cols if c in have] if cols else None
    d = gpd.read_file(gdb, layer=layer, columns=use, engine="pyogrio", **kw)
    for c in cols or []:
        if c not in d.columns:
            d[c] = None
    if d.crs is not None and d.crs.to_epsg() != 32640:
        d = d.to_crs(32640)
    return force2d(d)


def write_layer(gp: Path, name: str, gdf, cols, geom_type_hint="Point"):
    """写 gpkg 图层；空图层也写（建模代码和设施清单按图层存在来读）。"""
    import geopandas as gpd
    from shapely.geometry import Point, Polygon
    if gdf is None or not len(gdf):
        dummy = gpd.GeoDataFrame({c: [None] for c in cols}, geometry=[Point(0, 0) if geom_type_hint == "Point" else Polygon([(0, 0), (1, 0), (1, 1)])], crs=32640)
        dummy.to_file(gp, layer=name, driver="GPKG")
        # 删掉占位行，图层留空
        import sqlite3
        con = sqlite3.connect(gp)
        con.execute(f'delete from "{name}"')
        con.commit()
        con.close()
        return 0
    gdf.to_file(gp, layer=name, driver="GPKG")
    return len(gdf)


def stage_count(d):
    return dict(Counter(d["ASSET_STAGE"].fillna("（空）"))) if len(d) and "ASSET_STAGE" in d else {}


def components(lines, tol_dec=2):
    """管线端点（四舍五入到 0.01 m）建图，返回 (端点→分量号, 每根管的分量号, 端点坐标数组)。"""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    def ends(g):
        parts = list(g.geoms) if g.geom_type == "MultiLineString" else [g]
        a, b = parts[0].coords[0], parts[-1].coords[-1]
        return (round(a[0], tol_dec), round(a[1], tol_dec)), (round(b[0], tol_dec), round(b[1], tol_dec))
    E = [ends(g) for g in lines.geometry]
    idx: dict = {}
    for a, b in E:
        idx.setdefault(a, len(idx))
        idx.setdefault(b, len(idx))
    ia = np.array([idx[a] for a, _ in E]); ib = np.array([idx[b] for _, b in E])
    n = len(idx)
    _, lab = connected_components(coo_matrix((np.ones(len(E)), (ia, ib)), shape=(n, n)), directed=False)
    pts = np.array(list(idx.keys()))
    return pts, lab, lab[ia]


def pack_region(rid: str, src: Path, dtm_src: Path, pipes_all, comp_cache=None, log=print, flat: Path = None) -> dict:
    import geopandas as gpd
    import rasterio
    from rasterio.windows import from_bounds
    from scipy.spatial import cKDTree
    from shapely.geometry import mapping
    import model_rules as MR

    title, pref = REGIONS[rid]
    gdb = str(src / FAC_GDB)
    cgdb = str(src / CITY_GDB)
    out = ROOT / "data" / "regions" / rid
    out.mkdir(parents=True, exist_ok=True)
    rules = MR.load()
    M: dict = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "region": rid, "title": title, "source_gdb": str(src / FAC_GDB), "pipe_gdb": str(src / PIPE_GDB), "city_gdb_supplement": str(src / CITY_GDB), "layers": {}, "decisions": {}}
    log(f"\n==== {rid}（{title}，图层前缀 {pref}）====")
    # ---------------- 子汇水区 → catchments.gpkg / aecom_manholes.gpkg / catchment_outlets.csv
    sub = rd(gdb, f"{pref}_SUBCATCHMENT")
    bnd = rd(gdb, f"{pref}_MODEL_BOUNDARY")      # 说明.txt：建模范围用 MODEL_BOUNDARY
    sub["SUB_ID"] = sub["SUB_UID"].astype(str)      # 说明.txt：子汇水区标签用 SUB_UID（SUB_ID 跨模型有重名）
    sub["geometry"] = sub.geometry.make_valid()
    bnd["geometry"] = bnd.geometry.make_valid()
    # 重复的 SUB_ID（同 ID 不同出口 / 面积的两块）：第二块起加 _2、_3 后缀，只改名字，不动数据；SWMM 里子汇水区重名会报错或被覆盖
    dup_ids = sub.SUB_ID.astype(str).duplicated(keep=False)
    sub["SUB_ID_ORIG"] = sub.SUB_ID.astype(str)
    sub["SUB_ID"] = sub.SUB_ID.astype(str) + sub.groupby(sub.SUB_ID.astype(str)).cumcount().map(lambda k: "" if k == 0 else f"_{k + 1}")
    M["decisions"]["dup_sub_id_renamed"] = int((sub.SUB_ID != sub.SUB_ID_ORIG).sum())
    # 计算域多块（闭运算后仍不连通）：引擎只支持单块计算域。用户 2026-11-05 定（Airport、RB2）：只算最大块，其余块的子汇水区不进模型（清单写 dropped_subcatchments.csv，manifest 记面积）。
    import pandas as _pd
    from shapely.geometry import Polygon as _Poly
    _close = float(MR.get("twod.domain_closing_m", rules))
    _V = sub.union_all().buffer(_close).buffer(-_close)
    _parts = sorted(getattr(_V, "geoms", [_V]), key=lambda p: -p.area)
    M["decisions"]["domain_blocks_before"] = [round(p.area / 1e6, 3) for p in _parts[:8]]
    if len(_parts) > 1 and _parts[1].area > 0.01 * _parts[0].area:
        if rid not in KEEP_LARGEST:
            raise SystemExit(f"{rid}：计算域闭运算后有 {len(_parts)} 块，且没有「只算最大块」的决定")
        _main = _Poly(_parts[0].exterior)
        _keep = sub.geometry.representative_point().within(_main) | sub.geometry.intersects(_main.buffer(-1)) & (sub.geometry.intersection(_main).area > 0.5 * sub.geometry.area)
        _drop = sub[~_keep]
        _pd.DataFrame({"SUB_ID": _drop.SUB_ID.astype(str), "A_HA": _drop.A_HA, "OUTLET": _drop.OUTLET.astype(str)}).to_csv(out / "dropped_subcatchments.csv", index=False, encoding="utf-8")
        M["decisions"]["dropped_blocks"] = {"rule": "只算最大块（用户 2026-11-05）", "n_sub_dropped": int(len(_drop)), "area_ha_dropped": round(float(_drop.A_HA.sum()), 1), "n_sub_kept": int(_keep.sum()), "area_ha_kept": round(float(sub[_keep].A_HA.sum()), 1)}
        log(f"  ⚠ 计算域多块 {M['decisions']['domain_blocks_before']} km²：只留最大块，丢掉 {len(_drop)} 个子汇水区（{_drop.A_HA.sum():.0f} ha），保留 {int(_keep.sum())} 个（{sub[_keep].A_HA.sum():.0f} ha）")
        sub = sub[_keep].copy()
    nodes_all = {}
    for lay in ("MANHOLECHAMBER", "INLET", "CATCHBASIN", "SW_CAPPEDEND"):
        for G_, nm_ in ((gdb, f"{pref}_{lay}"), (cgdb, lay)):      # 区域图层优先，全市图层（261101）补邻区节点
            d = rd(G_, nm_, ["UNITID"], bbox=tuple(sub.total_bounds + np.array([-500, -500, 500, 500])))
            if d is None:
                continue
            for u, g in zip(d.UNITID, d.geometry):
                nodes_all.setdefault(str(u), (g.x, g.y, lay))
    cat = gpd.GeoDataFrame({
        "LABEL": sub.SUB_ID.astype(str), "OUT_NODEID": np.arange(1, len(sub) + 1), "OUT_NODE": sub.OUTLET.astype(str), "TC": sub.TC.astype(float),
        "SCS_CN": sub.CN.astype(float), "A": sub.AREA_HA.astype(float)}, geometry=sub.geometry.values, crs=32640)
    cat.to_file(out / "catchments.gpkg", layer="catchments", driver="GPKG")
    # 出口节点坐标（LABEL = OUT_NODE，推演把径流挂到离它最近的结构节点）
    rows, outl = [], []
    for r in sub.itertuples():
        u = str(r.OUTLET)
        if u in nodes_all:
            x, y, lay = nodes_all[u]
            rows.append({"LABEL": u, "geometry": __import__("shapely.geometry", fromlist=["Point"]).Point(x, y)})
        else:
            c = r.geometry.representative_point()
            rows.append({"LABEL": u, "geometry": c})
        dist = float(r.OUTLET_D) if r.OUTLET_D == r.OUTLET_D and r.OUTLET_D is not None else 0.0
        outl.append({"label": r.SUB_ID, "out_node": u, "layer": r.OUTLET_LYR, "dist_m": round(dist, 2), "found_in_network": u in nodes_all, "flag": "far" if (dist > 5.0 or u not in nodes_all) else "", "src": r.OUTLET_SRC})
    man = gpd.GeoDataFrame(rows, crs=32640).drop_duplicates("LABEL")
    man.to_file(out / "aecom_manholes.gpkg", layer="manholes", driver="GPKG")
    import pandas as pd
    pd.DataFrame(outl).to_csv(out / "catchment_outlets.csv", index=False, encoding="utf-8")
    n_far = sum(1 for o in outl if o["flag"] == "far")
    M["layers"]["subcatchments"] = {"n": len(cat), "area_ha": round(float(cat.A.sum()), 1), "outlet_not_in_network": sum(1 for o in outl if not o["found_in_network"]), "outlet_far_gt_5m": n_far,
                                    "dup_geometry": int(cat.geometry.to_wkb().duplicated().sum())}
    log(f"  子汇水区 {len(cat)}，出口不在管网里 {M['layers']['subcatchments']['outlet_not_in_network']}，偏远出口 {n_far}，几何重复 {M['layers']['subcatchments']['dup_geometry']}")
    # ---------------- 计算域（同推演代码：子汇水区并集闭运算、填洞）
    from shapely.geometry import Polygon
    close = float(MR.get("twod.domain_closing_m", rules))
    V = cat.union_all().buffer(close).buffer(-close)
    parts = sorted(getattr(V, "geoms", [V]), key=lambda p: -p.area)
    D = Polygon(parts[0].exterior)
    M["domain_km2"] = round(D.area / 1e6, 3)
    M["domain_parts"] = [round(p.area / 1e6, 4) for p in parts[:5]]
    M["boundary_km2"] = round(float(bnd.geometry.area.iloc[0]) / 1e6, 3)
    log(f"  计算域 {M['domain_km2']} km²（汇水区边界 {M['boundary_km2']} km²），闭运算后 {len(parts)} 块")
    dg = gpd.GeoDataFrame({"name": [title]}, geometry=[D], crs=32640).to_crs(4326)
    dg.to_file(out / "domain.geojson", driver="GeoJSON")
    x0, y0, x1, y1 = D.bounds
    # ---------------- 点设施：区域图层 ∪ 计算域内的全市图层（按 UNITID 去重）
    gp = out / "network_v2.gpkg"
    if gp.exists():
        gp.unlink()
    big = D.union(bnd.geometry.iloc[0]).buffer(OUTSIDE_M + 10).bounds
    pts = {}
    for name, (lay, cols) in {**PT_LAYERS, **EXTRA_LAYERS}.items():
        f = rd(gdb, f"{pref}_{lay}", cols)
        c = rd(cgdb, lay, cols, bbox=big)
        if c is None:
            c = f.iloc[0:0] if f is not None else None
        zone = D.union(bnd.geometry.iloc[0])
        if name in SEAL:       # 井 / 进水口 / 雨水口 / 封堵端：取计算域外扩 WINDOW_M 窗口内的全市图层（子汇水区出口可能在邻区图层里，用户 2026-11-02 确认）
            zone = zone.buffer(WINDOW_M)
        in_dom = c[c.geometry.intersects(zone)] if c is not None and len(c) else c
        if f is None:
            f = in_dom.iloc[0:0] if in_dom is not None else None
        if f is not None and len(f):
            f = f.copy(); f["src"] = f"{pref}汇水区图层"
        extra = in_dom[~in_dom.UNITID.isin(f.UNITID)].copy() if in_dom is not None and len(in_dom) and f is not None else (in_dom.copy() if in_dom is not None else None)
        if extra is not None and len(extra):
            extra["src"] = "全市图层·窗口内补充" if name in SEAL else "全市图层·计算域内补充"
        d = pd.concat([x for x in (f, extra) if x is not None and len(x)], ignore_index=True) if any(x is not None and len(x) for x in (f, extra)) else None
        if d is not None:
            d = gpd.GeoDataFrame(d, crs=32640)
            # 面图层（塘）可能在全市图层里才有 Z / 多边形，force2d 已做
        pts[name] = (d, c)
    # ---------------- 管线与出水口
    # 连通性只看「计算域外扩 WINDOW_M」这个窗口里的管线（全市管网是几万根连成一片的，不能整块拉进来；窗口外的管不进模型）
    pl = pipes_all[pipes_all.geometry.intersects(D.union(bnd.geometry.iloc[0]).buffer(WINDOW_M))].copy()
    comp_pts, lab, pipe_lab = components(pl)
    pl = pl.reset_index(drop=True)
    cp = pl[pl.geometry.intersects(D)]
    main_lab = Counter(pipe_lab[cp.index]).most_common(1)[0][0] if len(cp) else None
    tree = cKDTree(comp_pts)
    # 出水口
    of_d, of_city = pts["outfalls"]
    cand = []
    Z = D.union(bnd.geometry.iloc[0])       # 「域内」= 计算域 ∪ 汇水区边界（子汇水区并集可能比边界小，边界内的出水口也算域内）
    # 候选 = 区域图层 + 全市图层里计算域内 + 全市图层里计算域外 100 m 内
    seen = set()
    allo_c = rd(cgdb, "OUTFALL", PT_LAYERS["outfalls"][1], bbox=big)
    allo_r = rd(gdb, f"{pref}_OUTFALL", PT_LAYERS["outfalls"][1])
    allo = pd.concat([x for x in (allo_r, allo_c[~allo_c.UNITID.isin(allo_r.UNITID)] if allo_r is not None else allo_c) if x is not None and len(x)], ignore_index=True)    # 区域图层优先
    allo = gpd.GeoDataFrame(allo, crs=32640)
    for r in allo.itertuples():
        inside = Z.contains(r.geometry)
        dist = 0.0 if inside else float(Z.distance(r.geometry))
        d, i = tree.query((r.geometry.x, r.geometry.y))
        snapped = d < 2.0
        on_main = bool(snapped and lab[i] == main_lab)
        cand.append((r, inside, dist, snapped, on_main, float(d)))
    keep, outside_used, outside_rej = [], [], []
    for r, inside, dist, snapped, on_main, dsn in cand:
        row = {"UNITID": r.UNITID, "STATUS": r.STATUS, "INVERT_LEV": r.INVERT_LEV, "BND_TYPE": r.BND_TYPE, "BND_STAGE": r.BND_STAGE, "FLAP_GATE": r.FLAP_GATE, "ASSET_STAGE": r.ASSET_STAGE,
               "STAGE_SRC": r.STAGE_SRC, "geometry": r.geometry, "src": "区域图层 / 全市图层", "SCOPE": "计算域内" if inside else "", "DIST_M": round(dist, 1), "SNAP_M": round(dsn, 2), "ON_MAIN_NET": on_main}
        if inside:
            keep.append(row)
        elif r.UNITID in set(allo_r.UNITID) if allo_r is not None else False:      # 1105：区域图层随带的界外出水口（说明.txt：按管网接到本区的末端设施），接在主管网上就引入，不套 100 m
            row["SCOPE"] = f"区域图层随带的界外出水口（离计算域 {dist:.0f} m）" if on_main else "区域图层随带但接不上本区主管网，不用"
            (outside_used if on_main else outside_rej).append(row)
            if on_main:
                keep.append(row)
        elif rid not in NO_OUTSIDE and dist <= OUTSIDE_M and r.ASSET_STAGE == "Existing":
            row["SCOPE"] = f"界外 {OUTSIDE_M:g} m 内引入（离计算域 {dist:.0f} m）" if on_main else "界外 100 m 内但接不上本区主管网，不用"
            (outside_used if on_main else outside_rej).append(row)
            if on_main:
                keep.append(row)
    of = gpd.GeoDataFrame(keep, crs=32640) if keep else None
    if of is not None:      # 出水口三态共用，一律算 L1（用户 2026-11-02）；原 ASSET_STAGE 留在 ASSET_STAGE_ORIG
        of["ASSET_STAGE_ORIG"] = of["ASSET_STAGE"]
        of["ASSET_STAGE"] = "Existing"
    M["layers"]["outfalls"] = {"n": len(keep), "by_stage": {"Existing": len(keep)}, "inside_domain": sum(1 for k in keep if k["SCOPE"] == "计算域内"), "by_stage_orig": dict(Counter(k["ASSET_STAGE"] for k in keep)),
                               "outside_introduced": [{"id": k["UNITID"], "dist_m": k["DIST_M"], "bnd": k["BND_TYPE"]} for k in outside_used],
                               "outside_rejected_not_on_main_net": [{"id": k["UNITID"], "dist_m": k["DIST_M"]} for k in outside_rej],
                               "inside_bnd_type": dict(Counter(str(k["BND_TYPE"]) for k in keep if k["SCOPE"] == "计算域内"))}
    log(f"  出水口：域内 {M['layers']['outfalls']['inside_domain']}（{M['layers']['outfalls']['by_stage']}），界外引入 {len(outside_used)}，界外接不上不用 {len(outside_rej)}")
    # 管线：与所选出水口连通的整块 ∪ 域外扩 margin 内
    seed_xy = [(g.x, g.y) for g in of.geometry] if of is not None else []
    seed_lab = set()
    for x, y in seed_xy:
        d, i = tree.query((x, y))
        if d < 2.0:
            seed_lab.add(lab[i])
    conn = np.isin(pipe_lab, list(seed_lab))
    sel = pl.copy()                      # 窗口内的管全取（不分状态、不分 ASSET_STAGE）
    sel["connected"] = conn
    sel = sel[PIPE_COLS + ["connected", "geometry"]]
    sel.to_file(gp, layer="pipes", driver="GPKG")
    cnt = Counter(str(s).strip() or "（空）" for s in sel.STATUS)
    M["layers"]["pipes"] = {"n": len(sel), "n_connected_to_selected_outfalls": int(sel.connected.sum()), "by_stage": stage_count(sel), "by_STATUS": dict(cnt), "window_m": WINDOW_M,
                            "n_in_domain": int(sel.geometry.intersects(D).sum()), "main_component_pipes_in_domain": int((pipe_lab[cp.index] == main_lab).sum()) if len(cp) else 0,
                            "note": "不分状态、不分 ASSET_STAGE 全取；三态共用；只取计算域外扩 150 m 窗口内的管（全市管网连成一片，不整块拉进来）"}
    log(f"  管线 {len(sel)}（域内 {M['layers']['pipes']['n_in_domain']}，连通所选出水口 {int(sel.connected.sum())}）by_stage {stage_count(sel)}")
    # ---------------- 写点图层
    for name in ("manholes", "inlets", "catchbasins", "cappedends", "pumps", *EXTRA_LAYERS):
        d, _ = pts[name]
        cols = (PT_LAYERS.get(name) or EXTRA_LAYERS[name])[1] + ["src"]
        if d is not None and len(d) and name in SEAL:
            d = d.copy(); d["ASSET_STAGE_ORIG"] = d["ASSET_STAGE"]; d["ASSET_STAGE"] = "Existing"
            cols = cols + ["ASSET_STAGE_ORIG"]
        if name == "pumps" and d is not None and len(d):
            # 有曲线 + 启停水位 + 湿井参数的泵单独放 pumps_modeled（g261101_net 按泵站建模）；其余留在 pumps（缺参数，未建模）
            ok = d.PUMP_CURVE.notna() & d.ON_EL_M.notna() & d.OFF_EL_M.notna() & d.WW_INV.notna() & d.WW_DEPTH.notna() & d.WW_AREA.notna()
            write_layer(gp, "pumps_modeled", d[ok][cols + ["geometry"]] if ok.any() else None, cols)
            M["layers"]["pumps_modeled"] = {"n": int(ok.sum()), "by_stage": stage_count(d[ok])}
            d = d[~ok]
            if not len(d):
                d = None
        write_layer(gp, name, d[cols + ["geometry"]] if d is not None and len(d) else None, cols, "Polygon" if name == "ponds" else "Point")
        M["layers"][name] = {"n": 0 if d is None else len(d), "by_stage": stage_count(d) if d is not None else {},
                             "by_stage_orig": dict(Counter(d["ASSET_STAGE_ORIG"].fillna("（空）"))) if d is not None and len(d) and name in SEAL else None}
        log(f"  {name}: {M['layers'][name]['n']} {M['layers'][name]['by_stage']}" + (f"（原 ASSET_STAGE {M['layers'][name]['by_stage_orig']} → 三态共用，一律 Existing）" if M['layers'][name]['by_stage_orig'] else ""))
    ocols = ["UNITID", "STATUS", "INVERT_LEV", "BND_TYPE", "BND_STAGE", "FLAP_GATE", "ASSET_STAGE", "STAGE_SRC", "src", "SCOPE", "DIST_M", "SNAP_M", "ON_MAIN_NET", "ASSET_STAGE_ORIG"]
    write_layer(gp, "outfalls", of[ocols + ["geometry"]] if of is not None else None, ocols)
    rj = gpd.GeoDataFrame(outside_rej, crs=32640) if outside_rej else None        # 界外 100 m 内但接不上主管网：不进模型，只在设备清单里列出
    ocr = [c for c in ocols if c != "ASSET_STAGE_ORIG"]
    write_layer(gp, "outfalls_rejected", rj[ocr + ["geometry"]] if rj is not None else None, ocr)
    # 临时 / 计划设施：整张区域图层（带全部原字段里 ≤ 60 字符的）
    for name, lay in {**TEMP_LAYERS, **GEO_LAYERS}.items():
        d = rd(gdb, f"{pref}_{lay}")
        gt = "Polygon" if name == "geocell" else "Point"
        if d is not None and len(d):
            keep_c = [c for c in d.columns if len(c) <= 60 or c == "geometry"]
            d = d[keep_c]
            for c in ("TOP_LVL", "BOT_LVL", "LABEL", "HOTSPOT_ID", "PUMP_DETAIL", "STORAGEMAX"):
                if c not in d.columns:
                    d[c] = None
            write_layer(gp, name, d, [c for c in d.columns if c != "geometry"], gt)
        else:
            write_layer(gp, name, None, ["TOP_LVL", "BOT_LVL", "LABEL", "HOTSPOT_ID", "PUMP_DETAIL", "STORAGEMAX", "ASSET_STAGE"], gt)
        M["layers"][name] = {"n": 0 if d is None else len(d), "by_stage": stage_count(d) if d is not None else {}}
        log(f"  {name}: {M['layers'][name]['n']} {M['layers'][name]['by_stage']}")
    # ---------------- 雨水口交换节点清单（治理后：INLET / CATCHBASIN 都按雨水口；检查井只溢流）
    ex = []
    for nm, kind in (("inlets", "inlet"), ("catchbasins", "inlet"), ("manholes", "mh")):
        d, _ = pts[nm]
        if d is not None:
            ex += [{"node": str(u), "type": kind, "layer": nm, "in_gdb": True, "makani_table": ""} for u in d.UNITID]
    pd.DataFrame(ex).to_csv(out / "aecom_exchange_nodes.csv", index=False, encoding="utf-8")
    M["decisions"]["exchange_inlet_rule"] = "治理后 INLET 和 CATCHBASIN 当雨水口（inlet），检查井只溢流；没有 AECOM 雨水口清单可对"
    # ---------------- DTM（5 m 裁剪，计算域 + 300 m）、dem_edits（不做治理：空）
    with rasterio.open(dtm_src) as ds:
        win = from_bounds(x0 - 300, y0 - 300, x1 + 300, y1 + 300, ds.transform).round_offsets().round_lengths()
        z = ds.read(1, window=win)
        prof = ds.profile.copy()
        prof.update(driver="GTiff", width=z.shape[1], height=z.shape[0], transform=ds.window_transform(win), compress="deflate", predictor=3, dtype="float32", count=1, tiled=True,
                    blockxsize=256, blockysize=256)
        for k in ("photometric", "interleave"):
            prof.pop(k, None)
        with rasterio.open(out / "dtm_5m.tif", "w", **prof) as w_:
            w_.write(z.astype("float32"), 1)
        nod = ds.nodata
        zz = z[(z != nod) & np.isfinite(z)]
    ins = None
    M["dtm"] = {"cells": [int(z.shape[1]), int(z.shape[0])], "min_m": round(float(zz.min()), 2), "max_m": round(float(zz.max()), 2), "nodata_share": round(float(1 - zz.size / z.size), 4)}
    (out / "dem_edits.json").write_text("[]", encoding="utf-8")
    M["decisions"]["dem_edits"] = "不做深坑填平（不做数据治理）"
    log(f"  DTM 5 m 裁剪 {M['dtm']}")
    # ---------------- region.toml、wrapper、manifest
    (out / "region.toml").write_text(f'''# 区域登记：{title}（1105 治理后数据；tools/g1105_pack.py 生成，勿手改）
[region]
id = "{rid}"
title = "{title}"
domain = "domain.geojson"            # 汇水区（子汇水区并集）外框，WGS84；推演用的计算域由 catchments.gpkg 闭运算得到

[network]
outfalls = []                        # v2 数据包不用（出水口在 network_v2.gpkg，打包时已按口径选好）
margin_m = {MARGIN_M:g}
format = "v2"

[inputs]
catchments = "治理后 {pref}_SUBCATCHMENT（已含 CN / Tc / 出口 OUTLET）"
manholes = "治理后 {pref} 出口节点坐标（aecom_manholes.gpkg，LABEL = OUTLET）"

[inputs_v2]
facility_gdb = "{src / FAC_GDB}"

[delivery]
layout = "rda"                       # 多态交付：同一场景的 L1 / L2 / L2planned 放同一个交付根，页面登记成同一个仿真

[grid]
res_m = 15

[catchments]
far_outlet_m = 5.0

[run]
# 9 场景 = {{33,61,96}} mm × {{1,2,24}} h；态按数据真实情况。顺序：所有 L1 在前，再 L2，再 L2planned（1104 批原顺序 S33D1, S33D1@L2, … 会让等 L1 的 L2 占满并行位，事后才用 g1104-reorder.sh 改）
default_scenarios = {json.dumps([sc + ("" if st == "L1" else "@" + st) for st in STATES.get(rid, ["L1", "L2", "L2planned"]) for sc in SCENARIOS if not sc.endswith("D24")])}
scenarios_24h = {json.dumps([sc + ("" if st == "L1" else "@" + st) for st in STATES.get(rid, ["L1", "L2", "L2planned"]) for sc in SCENARIOS if sc.endswith("D24")])}
default_state = "L1"
mem_gb_per_case = 8                  # 单个算例预留内存（GB）；推演按「可用内存 ÷ 这个数」限制并行，并记录实测峰值（run_region_1105.py），以后以实测为准
''', encoding="utf-8")
    (out / f"{rid}-server.sh").write_text(f'#!/usr/bin/env bash\n# {rid} 推演服务脚本：{rid}-server.sh check | start | stop | status | log | resume | report\nexec "$(cd "$(dirname "$0")/../../.." && pwd)/scripts/region-server-g1105.sh" "{rid}" "$@"\n', encoding="utf-8")
    (out / f"{rid}-server.sh").chmod(0o755)
    files = {}
    for f in ("catchments.gpkg", "aecom_manholes.gpkg", "catchment_outlets.csv", "aecom_exchange_nodes.csv", "network_v2.gpkg", "dtm_5m.tif", "dem_edits.json", "domain.geojson", "makani.gpkg"):
        if not (out / f).exists():
            continue                       # makani.gpkg 在 109 上用 g261101_makani.py 补；已经有的保留并记进 manifest
        files[f] = {"bytes": (out / f).stat().st_size, "sha256": sha(out / f)}
    (out / "manifest.json").write_text(json.dumps({"generated_at": M["generated_at"], "files": files}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "manifest_v2.json").write_text(json.dumps(M, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return M


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True, help="/Volumes/ArchieShane/temp/flood（含 1105/）")
    ap.add_argument("--dtm", required=True, help="全市 5 m DTM（EPSG:32640）")
    ap.add_argument("--regions", default=",".join(REGIONS))
    a = ap.parse_args()
    import geopandas as gpd
    src = Path(a.src)
    print("读全市 PIPELINE（1105/全市治理后数据.gdb）…", flush=True)
    pl = rd(str(src / PIPE_GDB), "PIPELINE", PIPE_COLS).reset_index(drop=True)
    print(f"  {len(pl)} 条管", flush=True)
    summary = {}
    for rid in [r.strip() for r in a.regions.split(",") if r.strip()]:
        summary[rid] = pack_region(rid, src, Path(a.dtm), pl)
    print("\n完成。各区 manifest_v2.json 在 data/regions/<ID>/ 里。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
