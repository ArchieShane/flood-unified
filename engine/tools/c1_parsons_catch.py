# -*- coding: utf-8 -*-
"""把 Parsons 模型导出（26092026）里落在 C1 计算域内的子汇水区导成系统图层 console/data/c1_parsons_ca.geojson。

来源：RB3 / B_C / Musaffah_RD09 / ADCG6 / RB_02 / Mussafah-01 各自的子汇水区图层（CA、Catchments、Catchment Table…），
      其余模型在计算域内不到 5 块，不取。几何裁到计算域；没有 .prj 的按 UTM 40N。
属性：model、label、cn（SCS_CN）、tc（min）、area_ha（裁剪后）、out_node、runoff_m3（Parsons 结果字段 RUNOFF_VOL，情景未注明）、q_max
注意：各模型边界有重叠，同一块地可能在两个模型里各有一块；这些是 Parsons 模型的产流划分，不是片区边界。
用法：python tools/c1_parsons_catch.py
"""
from __future__ import annotations

import glob
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); import datapaths  # noqa: E402  客户数据位置：config.toml [data]
BASE = datapaths.parsons("26092026")
MODELS = ["RB3", "B_C", "Musaffah_RD09", "ADCG6", "RB_02", "Mussafah-01"]


def pick(row, *names):
    for n in names:
        if n in row and row[n] is not None and str(row[n]) not in ("nan", ""):
            return row[n]
    return None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    import pandas as pd
    D = gpd.read_file(ROOT / "out" / "c1_domain.geojson").to_crs(32640).geometry.iloc[0]
    parts = []
    for m in MODELS:
        for shp in glob.glob(str(BASE / m / "**" / "*.shp"), recursive=True):
            if not re.match(r"(?i)^(ca|catchments?|catchment table|cat)$", pathlib.Path(shp).stem):
                continue
            g = gpd.read_file(shp)
            g = g.set_crs(32640, allow_override=True) if g.crs is None else g.to_crs(32640)
            g = g[g.intersects(D)].copy()
            g["geometry"] = g.geometry.buffer(0).intersection(D)
            g = g[~g.geometry.is_empty & (g.area > 1)]
            rows = []
            for _, r in g.iterrows():
                d = r.to_dict()
                rows.append({"model": m, "label": pick(d, "LABEL", "LB"), "cn": pick(d, "SCS_CN"), "tc": pick(d, "TC"),
                             "out_node": pick(d, "OUT_NODE"), "runoff_m3": pick(d, "RUNOFF_VOL"), "q_max": pick(d, "Q_(MAX)", "FLOW_MAX"),
                             "area_ha": round(r.geometry.area / 1e4, 4)})
            parts.append(gpd.GeoDataFrame(rows, geometry=list(g.geometry.simplify(0.5)), crs=32640))
            print(f"  {m:<15} {len(g):>6,} 块  {g.area.sum() / 1e4:>7,.0f} ha")
    G = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=32640).to_crs(4326)
    for c in ("cn", "tc", "runoff_m3", "q_max"):
        G[c] = pd.to_numeric(G[c], errors="coerce").round(2)
    gj = json.loads(G.to_json(drop_id=True))
    for f in gj["features"]:                                   # 坐标保留 6 位小数（约 0.1 m），文件小一半
        def rnd(c):
            return [rnd(x) for x in c] if isinstance(c[0], list) else [round(c[0], 6), round(c[1], 6)]
        f["geometry"]["coordinates"] = rnd(f["geometry"]["coordinates"])
    gj.update({"layer": "c1_parsons_ca", "source": "Parsons_Model Data Export/26092026 各模型子汇水区，裁到 C1 计算域",
               "note": "模型之间边界有重叠；RUNOFF_VOL / Q_(MAX) 是 Parsons 导出时的结果字段，对应哪一场雨未注明"})
    out = ROOT / "console" / "data" / "c1_parsons_ca.geojson"
    out.write_text(json.dumps(gj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"→ {out.name}：{len(G):,} 块，{out.stat().st_size / 1048576:.1f} MB；CN {G.cn.min()}–{G.cn.max()}，中位 {G.cn.median()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
