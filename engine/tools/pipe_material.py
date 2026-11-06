# -*- coding: utf-8 -*-
"""客户模型管段 → 客户 GDB 管线的管材，按管材定既有管糙率（2026-10-19，参数登记表第 7 项）。

规则（用户 2026-10-19 给定）：既有管道平均曼宁 n = 0.013，混凝土管 0.015（「视管材使用年限而定」——年限没有给出调整规则，
本版只按管材，年限不调，Claude 自定并记录）。GDB 管材取 PIPE_MATERIAL：Reinforced Concrete → 0.015，其余（GRP / uPVC / PVC / Steel /
Other / Undefined）→ 0.013；匹配不上的 → 0.013。
匹配：模型管段中点到 GDB 管线的距离 ≤ 3 m，且两者方向夹角 ≤ 20°，取最近的一条（坐标都是 UTM 40N）。
用法：python tools/pipe_material.py --conduits "<客户模型 Conduits.shp>" --out out/pipe_material_RB01.json
      --src 换管线来源（缺省 DMT GDB 的 PIPELINE）：如 20261019/Stormwater/PIPELINE.shp（2026-10 全市最新，shp 列名截成 10 字符，自动认）
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
GDB = "D:/阿布扎比/DMT_StormWater_AbuDhabi_Processed.gdb/DMT_StormWater_AbuDhabi_Processed.gdb"
N_EXIST = {"Reinforced Concrete": 0.015}
N_DEFAULT = 0.013
TOL_M, TOL_DEG = 3.0, 20.0


def bearing(g):
    (x0, y0), (x1, y1) = g.coords[0][:2], g.coords[-1][:2]          # GDB 的线带 Z
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--conduits", required=True); ap.add_argument("--label", default="LABEL"); ap.add_argument("--out", required=True)
    ap.add_argument("--src", default=GDB, help="管线来源：GDB 目录（读 PIPELINE 层）或 shp")
    a = ap.parse_args(argv)
    import geopandas as gpd
    from shapely.geometry import LineString
    c = gpd.read_file(a.conduits)
    if c.crs is None:
        c = c.set_crs(32640)
    c = c.to_crs(32640)
    bb = tuple(c.total_bounds + np.array([-50, -50, 50, 50]))
    if a.src.lower().endswith(".shp"):
        g = gpd.read_file(a.src, bbox=bb).rename(columns={"PIPE_MATER": "PIPE_MATERIAL", "ASSET_INST": "ASSET_INSTALLATION_DATE", "PIPE_DIAME": "PIPE_DIAMETER"})
    else:
        g = gpd.read_file(a.src, layer="PIPELINE", bbox=bb, columns=["PIPE_MATERIAL", "ASSET_INSTALLATION_DATE", "PIPE_DIAMETER"])
    g = g[g.geometry.notna()].explode(index_parts=False).reset_index(drop=True)
    g["brg"] = [bearing(x) for x in g.geometry]
    sidx = g.sindex
    out, stat = {}, {}
    for r in c.itertuples():
        geom = r.geometry
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type == "MultiLineString":
            geom = max(geom.geoms, key=lambda q: q.length)
        mid = geom.interpolate(0.5, normalized=True); b0 = bearing(geom)
        best = None
        for j in sidx.query(mid.buffer(TOL_M)):
            d = g.geometry.iloc[j].distance(mid)
            da = abs(b0 - g.brg.iloc[j]); da = min(da, 180 - da)
            if d <= TOL_M and da <= TOL_DEG and (best is None or d < best[0]):
                best = (d, j)
        lab = str(getattr(r, a.label))
        if best is None:
            mat, src, dia, inst = None, "未匹配", None, None
        else:
            row = g.iloc[best[1]]
            mat = row.PIPE_MATERIAL; src = f"GDB {round(best[0], 1)} m"; dia = row.PIPE_DIAMETER; inst = str(row.ASSET_INSTALLATION_DATE)[:10]
        n = N_EXIST.get(mat, N_DEFAULT)
        out[lab] = {"material": mat, "n": n, "match": src, "src_diameter": None if dia is None or dia != dia else float(dia), "installed": inst}
        k = mat if mat else "未匹配"
        stat[k] = stat.get(k, 0) + 1
    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(a.out).write_text(json.dumps({"rule": "混凝土 0.015，其余与未匹配 0.013（用户 2026-10-19）", "src": a.src, "tol_m": TOL_M, "tol_deg": TOL_DEG,
                                               "stat": stat, "pipes": out}, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(out)} 根：{stat}")
    print(f"n = 0.015：{sum(1 for v in out.values() if v['n'] == 0.015)} 根；→ {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
