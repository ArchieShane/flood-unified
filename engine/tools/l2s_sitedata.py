# -*- coding: utf-8 -*-
"""L2+suggested 可建性约束的空间数据（2026-10-23，docs/RD-A_L2S可建性约束设计_20261023.md 第三节）：从 Makani 只读，裁到计算域外扩范围，存一个 GeoPackage。

图层（UTM 40N）：
  buildings       建筑（udm_building）
  road_surface    路面 / 车行道（udm_roadsurface + 干道车行道）
  road_centre     道路中心线（udm_roadcentreline）—— 压力管沿路走线用
  road_islands    道路中央岛、路肩（udm_roadisland / udm_roadshoulder）—— 属道路红线，不建池和泵站
  corridor        管线走廊（udm_utilitycorridor + servicecorridor），带 drainage_alloc（有没有 D 排水走廊）与宽度字段
  utility_lines   现状管线（给水 / 电力 / 灌溉 TSE / 污水 / 燃气），带 kind
  substations     ADDC 变电站（点）
  utility_plots   现状 / 规划公共设施用地（utility_plot + ud_utilityplots），带 subtype（泵站等）
  parks           公园（udm_park + parks_details）
  plots           地块（udm_plot），带用途 / 建设状态
  coast_excl      不能设排海口的海岸：海滩、海岸设施（防波堤 / 滑道）、滨水保护线
  shoreline       海岸线（udm_shoreline）
  sea             海面（udm_seasurface）
  no_dig          隧道、桥（开挖排除）
  trees           树（点）
用法：python tools/l2s_sitedata.py --domain out/dataqc/RD-A/input/rda_domain.geojson --pad 1500 --out out/rda/sitedata.gpkg
"""
from __future__ import annotations

import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

# (图层, Makani 表, 附加列, where)
LAYERS = [
    ("buildings", "udm_building", (), ""),
    ("road_surface", "udm_roadsurface", (), ""),
    ("road_surface", "main_roads_carriageway_ifp2018_1", (), ""),
    ("road_centre", "udm_roadcentreline", (), ""),
    ("road_islands", "udm_roadisland", (), ""), ("road_islands", "udm_roadshoulder", (), ""),
    ("corridor", "udm_utilitycorridor", ("reservationtype", "reservationwidth") + tuple(f"corridor{i:02d}type" for i in range(1, 10)) + tuple(f"corridor{i:02d}width" for i in range(1, 10)), ""),
    ("corridor", "servicecorridor", ("reservationtype", "reservationwidth") + tuple(f"corridor{i:02d}type" for i in range(1, 10)) + tuple(f"corridor{i:02d}width" for i in range(1, 10)), ""),
    ("utility_lines", "water_pipeline", (), ""), ("utility_lines", "power_cable", (), ""), ("utility_lines", "irrigation_tse_pipeline", (), ""),
    ("utility_lines", "sewerage_pipeline", (), ""), ("utility_lines", "gas_pipeline", (), ""),
    ("substations", "adwea_e_substation", ("type", "statusindicator"), ""),
    ("utility_plots", "utility_plot", ("structure_type", "utility_subtype", "plot_status"), ""),
    ("utility_plots", "ud_utilityplots", ("utility_sub_type_code", "existing_status_code"), ""),
    ("parks", "udm_park", ("nameenglish",), ""), ("parks", "parks_details", (), ""),
    ("plots", "udm_plot", ("primaryuseeng", "construction_status", "communityeng"), ""),
    ("coast_excl", "udm_beach", (), ""), ("coast_excl", "udm_coastalfacilities", ("coastalfcltystrctrtype",), ""),
    ("coast_excl", "waterfront_protection_overlay_line", (), ""),
    ("shoreline", "udm_shoreline", (), ""), ("shoreline", "udm_seashore", (), ""),
    ("sea", "udm_seasurface", (), ""),
    ("no_dig", "udm_roadtunnel", (), ""), ("no_dig", "udm_bridge", (), ""),
    ("trees", "udm_tree", (), ""),
]


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True); ap.add_argument("--pad", type=float, default=1500.0); ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    import geopandas as gpd
    import pandas as pd
    from pyproj import Transformer
    from shapely import wkb
    from swbrain import db
    dom = gpd.read_file(a.domain).to_crs(32640)
    x0, y0, x1, y1 = dom.total_bounds + [-a.pad, -a.pad, a.pad, a.pad]
    tw = Transformer.from_crs(32640, 4326, always_xy=True)
    lo0, la0 = tw.transform(x0, y0); lo1, la1 = tw.transform(x1, y1)
    conn = db.connect_source(db.load_config(None), "makani", timeout_ms=900_000)
    out = pathlib.Path(a.out); out.parent.mkdir(parents=True, exist_ok=True); out.unlink(missing_ok=True)
    acc: dict[str, list] = {}
    with conn, conn.cursor() as cur:
        for lay, tab, cols, where in LAYERS:
            cur.execute(f"select find_srid('public', %s, 'shape')", (tab,)); srid = cur.fetchone()[0]
            env = f"st_makeenvelope({lo0},{la0},{lo1},{la1},4326)"
            if srid != 4326:
                env = f"st_transform({env},{srid})"
            cur.execute("select column_name from information_schema.columns where table_schema='public' and table_name=%s", (tab,))
            have = {r[0] for r in cur.fetchall()}
            cs = [c for c in cols if c in have]
            sel = "".join(f", {c}::text" for c in cs)
            cur.execute(f"select st_asbinary(st_transform(st_makevalid(shape),32640)){sel} from public.{tab} where shape && {env} and not st_isempty(shape) {where}")
            rows = [r for r in cur.fetchall() if r[0]]
            df = pd.DataFrame([dict(zip(cs, r[1:])) for r in rows]); df["src"] = tab
            g = gpd.GeoDataFrame(df, geometry=[wkb.loads(bytes(r[0])) for r in rows], crs=32640)
            acc.setdefault(lay, []).append(g)
            print(f"  {lay:<14} ← {tab:<36} {len(g):>6}")
    for lay, gs in acc.items():
        g = gpd.GeoDataFrame(pd.concat(gs, ignore_index=True), crs=32640)
        g = g[~g.geometry.is_empty & g.geometry.notna()]
        if lay == "corridor":
            tcols = [c for c in g.columns if c.startswith("corridor") and c.endswith("type")]
            g["drainage_alloc"] = g[tcols].apply(lambda r: any(str(v).strip().upper() == "D" for v in r), axis=1) if tcols else False
        if lay == "utility_lines":
            g["kind"] = g["src"].map({"water_pipeline": "water", "power_cable": "power", "irrigation_tse_pipeline": "tse", "sewerage_pipeline": "sewer", "gas_pipeline": "gas"})
        for c in g.columns:
            if c != "geometry" and g[c].dtype == object:
                g[c] = g[c].astype(str)
        g.to_file(out, layer=lay)
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
