# -*- coding: utf-8 -*-
"""从客户源库导出「离线数据库快照」，供 docker/db 构建带数据的 PostGIS 镜像（2026-10-03）。

服务器纯离线、没有客户源库；换 DTM 后要重跑 `python -m swbrain.run`，它读 pipe / makani / liveability 三个库。
这里只导出这三个库里代码实际用到的表（见 TABLES），不是整库（整库 pipe 7 GB / makani 32 GB / liveability 84 GB）。
flood 库只用于生成控制台图层（结果已在 console/data），不导。

    python tools/make_db_snapshot.py                 # 导出到 docker/db/dumps/<库>.dump（pg_dump -Fc，只读源库）
    python tools/make_db_snapshot.py --only pipe

pg_dump 用本机已有的 pgrouting/pgrouting:17-3.5-4.0 镜像里的（与源库同为 PG 17.5）。口令只从 config.toml 读，经环境变量传给容器，不打印。
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import time
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docker" / "db" / "dumps"
IMAGE = "pgrouting/pgrouting:17-3.5-4.0"
A = "dmt_irrigationstormwater_abudhabi"
N = "dmt_irrigationstormwater_abudhabi_network"

TABLES = {
    "pipe": [f"{A}.{t}" for t in ("inlet", "catchbasin", "outfall", "pond", "sw_reservoirstructure", "soakaway", "ps_pump", "well", "spillway", "movable_pump")]
            + [f"{N}.{t}" for t in ("sw_edge", "sw_edge_vertices_pgr", "sw_node_outfall", "sw_edge_dangling", "sw_asset_node")],
    "makani": [f"public.{t}" for t in (
        # 地表覆盖 / 可用地块（catchments.py、landuse.py）
        "udm_plot", "udm_roadsurface", "udm_building", "udm_parking", "ud_greenarea", "udm_park", "ud_plot", "masterplan_ud_plot", "upc_plot",
        "udm_roadintersection", "udm_roadshoulder", "udm_roadisland", "main_roads_carriageway_ifp2018_1", "main_roads_shoulders_ifp2018_1",
        "main_roads_median_ifp2018_1", "main_roads_utility_corridor_ifp2018_reservation", "main_roads_row_ifp2018_guidance_only", "upc_row",
        "utility_plot", "utility_plots_1", "ud_utilityplots", "upc_utilityplots", "etihadrailrow", "bas_building", "udm_building_3d_supplement",   # bas_building 是视图，依赖后者
        # 设计标准分类（standards.py）与重点单位（refdata.py）
        "bdms_bldg_landuse", "udm_roadcentreline", "udm_roadtunnel", "udm_airportarea",
        "adwea_e_substation", "poi_doh_facilities", "poi_adek_schools_locations", "poi_civildefense_centers", "poi_technicalrescue_stations",
        "poi_ambulance_stations", "metro_facilities",
        # extract.py：在批/拟建雨水结构、片区
        "storm_water_structure", "udm_district")],
    "liveability": ["public.nrn_road_edges", "public.fact_oi_indicators"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=list(TABLES))
    a = ap.parse_args()
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    for name, tabs in TABLES.items():
        if a.only and a.only != name:
            continue
        s = cfg["source"][name]
        dump = f"{name}.dump"
        cmd = ["docker", "run", "--rm", "-e", "PGPASSWORD", "-v", f"{OUT}:/out", "--entrypoint", "pg_dump", IMAGE,
               "-h", s["host"], "-p", str(s["port"]), "-U", s["user"], "-d", s["dbname"],
               "-Fc", "-Z", "6", "--no-owner", "--no-privileges", "-f", f"/out/{dump}"]
        for t in tabs:
            cmd += ["-t", t]
        print(f"[{name}] {len(tabs)} 张表 → {OUT / dump}", flush=True)
        t0 = time.time()
        env = {**__import__("os").environ, "PGPASSWORD": s["password"]}
        r = subprocess.run(cmd, env=env)
        if r.returncode:
            sys.exit(f"[{name}] pg_dump 失败（退出码 {r.returncode}）")
        print(f"[{name}] 完成 {(OUT / dump).stat().st_size / 1048576:.0f} MB，{time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
