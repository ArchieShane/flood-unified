# -*- coding: utf-8 -*-
"""区域数据打包 v2（2026-10-30 全市治理后数据 + 按汇水区附属设施）：新数据是扁平图层（PIPELINE / MANHOLECHAMBER / INLET / …），
状态由 ASSET_STAGE（Existing / Temporary / Planned）标明，不再是 Utility Network 的 StormwaterLine / Device / Junction。

取数规则（用户 2026-10-30 确认）：
  管线      全市 PIPELINE，不分状态全取（含 STATUS = Abandoned / Expired / Removed，剔除会断连通性）。
            范围 = 与计算域内任一出水口连通的整块 ∪ 计算域外扩 margin 内的管。
  点设施    井 / 进水口 / 雨水井 / 封堵端 / 出水口 / 泵：RD-A 汇水区 gdb（RD_A_*）为主，再用全市同名图层补上「落在 RD-A 计算域内、汇水区图层没有」的
            （汇水区边界比计算域小几米，出水口 3 个 Existing、井 10 个、泵站 AE_RDA 三台泵都在这几米里）。按 UNITID 去重，来源记在 src 列。
  临时设施  RD_A_TEMP_PUMP_SUCTION / DISCHARGE（汇水区 gdb）。
  计划设施  RD_A_GEOCELL_AREA（汇水区 gdb）；Planned 的管 / 井 / 出水口 / 泵跟在上面各图层里，由 ASSET_STAGE 区分。
  出水口不外扩（计算域内已有 3 个 Existing）。

产出：data/regions/<区域>/network_v2.gpkg（图层：pipes、manholes、inlets、catchbasins、cappedends、outfalls、pumps、temp_pump_suction、
temp_pump_discharge、geocell）和 manifest_v2.json（各图层各状态的个数、来源、与汇水区图层的差异）。

用法：python tools/region_pack_v2.py --region RDA
数据路径来自 region.toml 的 [inputs_v2]（city_gdb / facility_gdb）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tomllib
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

PIPE_COLS = ["UNITID", "STATUS", "PIPE_MATER", "PIPE_SUBTY", "PIPE_DIAME", "INVERT_LEV", "INVERT_L_1", "PIPE_LENGT", "ASSET_STAGE", "STAGE_SRC", "START_ASSE", "END_ASSET_"]
PT_COLS = {
    "manholes": ("MANHOLECHAMBER", ["UNITID", "STATUS", "INVERT_LEV", "COVER_LEVE", "ASSET_STAGE", "STAGE_SRC"]),
    "inlets": ("INLET", ["UNITID", "STATUS", "INVERT_LEV", "COVER_LEVE", "ASSET_STAGE", "STAGE_SRC"]),
    "catchbasins": ("CATCHBASIN", ["UNITID", "STATUS", "INVERT_LEV", "COVER_LEVE", "ASSET_STAGE", "STAGE_SRC"]),
    "cappedends": ("SW_CAPPEDEND", ["UNITID", "STATUS", "INVERT_LEV", "COVER_LEVE", "ASSET_STAGE", "STAGE_SRC"]),
    "outfalls": ("OUTFALL", ["UNITID", "STATUS", "INVERT_LEV", "BND_TYPE", "BND_STAGE", "ASSET_STAGE", "STAGE_SRC"]),
    "pumps": ("PUMP", ["UNITID", "STATUS", "CAP_Q_LS", "CAP_H_M", "ON_EL_M", "OFF_EL_M", "N_PUMPS", "MODEL_READY", "MISSING", "WW_INV", "WW_DEPTH", "WW_AREA",
                       "PUMP_CURVE", "ASSET_STAGE", "STAGE_SRC"]),
}


def _force2d(gdf):
    import shapely
    gdf = gdf.copy()
    gdf["geometry"] = shapely.force_2d(gdf.geometry.values)
    return gdf


def _read(gdb: str, layer: str, cols: list[str], **kw):
    import geopandas as gpd
    import pyogrio
    have = {c for c in pyogrio.read_info(gdb, layer=layer)["fields"]}
    use = [c for c in cols if c in have]
    d = gpd.read_file(gdb, layer=layer, columns=use, engine="pyogrio", **kw)
    for c in cols:
        if c not in d.columns:
            d[c] = None
    if d.crs is not None and d.crs.to_epsg() != 32640:
        d = d.to_crs(32640)
    return _force2d(d)


def trace_connected(lines, seeds_xy, tol: float = 2.0):
    """端点吸附（0.01 m）后无向遍历，返回与任一种子点连通的管下标集合。"""
    from scipy.spatial import cKDTree

    def ends(g):
        parts = list(g.geoms) if g.geom_type == "MultiLineString" else [g]
        a, b = parts[0].coords[0], parts[-1].coords[-1]
        return (round(a[0], 2), round(a[1], 2)), (round(b[0], 2), round(b[1], 2))
    E = [ends(g) for g in lines.geometry]
    node: dict = {}
    for i, (a, b) in enumerate(E):
        node.setdefault(a, []).append(i)
        node.setdefault(b, []).append(i)
    keys = list(node)
    tr = cKDTree(np.array(keys))
    seeds = set()
    for x, y in seeds_xy:
        d, i = tr.query((x, y))
        if d < tol:
            seeds.add(keys[i])
    seen, stack, conn = set(), list(seeds), set()
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        for i in node[n]:
            if i not in conn:
                conn.add(i)
                a, b = E[i]
                stack.append(b if a == n else a)
    return conn, len(seeds)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--margin", type=float, default=None, help="管外扩距离 m（缺省取 region.toml [network] margin_m）")
    a = ap.parse_args()
    import geopandas as gpd
    out = ROOT / "data" / "regions" / a.region
    cfg = tomllib.loads((out / "region.toml").read_text(encoding="utf-8"))
    src = cfg["inputs_v2"]
    city, fac = src["city_gdb"], src["facility_gdb"]
    marg = a.margin if a.margin is not None else float(cfg["network"].get("margin_m", 60))
    dom = gpd.read_file(out / cfg["region"]["domain"]).to_crs(32640).geometry.union_all()
    gp = out / "network_v2.gpkg"
    if gp.exists():
        gp.unlink()
    M: dict = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "sources": {"city_gdb": city, "facility_gdb": fac},
               "rule": __doc__.split("取数规则")[1].split("产出")[0].strip(), "layers": {}}

    def stage_count(d):
        return dict(Counter(d["ASSET_STAGE"].fillna("（空）")))

    # ---------------- 点设施：汇水区图层为主 + 全市计算域内补充
    pts = {}
    for name, (lay, cols) in PT_COLS.items():
        c = _read(city, lay, cols, mask=dom.buffer(0.01))
        c = c[c.geometry.intersects(dom)]
        try:
            f = _read(fac, "RD_A_" + lay, cols)
        except Exception:  # noqa: BLE001
            f = c.iloc[0:0].copy()
        f["src"] = "RD_A汇水区"
        extra = c[~c.UNITID.isin(f.UNITID)].copy()
        extra["src"] = "全市图层·计算域内补充"
        d = gpd.GeoDataFrame(__import__("pandas").concat([f, extra], ignore_index=True), crs=32640)
        d.to_file(gp, layer=name, driver="GPKG")
        pts[name] = d
        M["layers"][name] = {"n": len(d), "by_stage": stage_count(d), "n_from_RD_A": len(f), "n_city_extra": len(extra), "city_extra_by_stage": stage_count(extra),
                             "city_extra_ids": extra.UNITID.tolist() if len(extra) <= 30 else f"{len(extra)} 个"}
        print(f"  ✓ {name}: {len(d)}（汇水区 {len(f)} + 全市补 {len(extra)}）{stage_count(d)}")
    # ---------------- 管线：全市，连通追踪
    print("读全市 PIPELINE（27 万条）…")
    lines = _read(city, "PIPELINE", PIPE_COLS)
    seeds = [(g.x, g.y) for g in pts["outfalls"].geometry]
    conn, n_seed = trace_connected(lines, seeds)
    lines["connected"] = [i in conn for i in range(len(lines))]
    sub = lines[lines.geometry.intersects(dom.buffer(marg)) | lines["connected"]].copy()
    sub.to_file(gp, layer="pipes", driver="GPKG")
    M["layers"]["pipes"] = {"n": len(sub), "n_connected_to_outfalls": int(sub.connected.sum()), "outfall_seeds_snapped": n_seed, "by_stage": stage_count(sub),
                            "by_STATUS": dict(Counter(sub["STATUS"].fillna("（空）").astype(str).str.strip().replace("", "（空）"))), "margin_m": marg,
                            "note": "不分状态全取；STATUS = Abandoned / Expired / Removed 的管保留（剔除会断连通性），推演参数报告里写明"}
    print(f"  ✓ pipes: {len(sub)}（连通 {int(sub.connected.sum())}）{stage_count(sub)}")
    # ---------------- 临时 / 计划设施（汇水区 gdb）
    for name, lay in (("temp_pump_suction", "RD_A_TEMP_PUMP_SUCTION"), ("temp_pump_discharge", "RD_A_TEMP_PUMP_DISCHARGE"), ("geocell", "RD_A_GEOCELL_AREA")):
        d = gpd.read_file(fac, layer=lay, engine="pyogrio")
        d = d.to_crs(32640) if d.crs is not None and d.crs.to_epsg() != 32640 else d
        d = _force2d(d)
        keep = [c for c in d.columns if len(c) <= 40 or c in ("geometry",)]
        d[keep].to_file(gp, layer=name, driver="GPKG")
        M["layers"][name] = {"n": len(d), "by_stage": stage_count(d)}
        print(f"  ✓ {name}: {len(d)} {stage_count(d)}")
    (out / "manifest_v2.json").write_text(json.dumps(M, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("manifest_v2.json 已写")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
