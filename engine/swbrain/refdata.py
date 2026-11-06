"""应急调度要用的真实参考数据：路网图、重点单位、隧道、民防站（候选驻地）。只读源库，缓存到 out/state/。

路网：宜居库 nrn_road_edges（41 万段，cost_driving 单位为秒，SRID 4326），源库没装 pgRouting，
所以行程时间在本地用 scipy 稀疏图的 Dijkstra 算；边取中点坐标，用来判断是否落在积水区内。
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .db import df

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "out" / "state"


def load_road_graph(conn) -> tuple[pd.DataFrame, pd.DataFrame]:
    """边（id, source, target, cost_s, rcost_s, len_m, mid_lon, mid_lat）与顶点（vid, lon, lat）。"""
    e = df(conn, """
      select id, source, target, cost_driving cost_s, reverse_cost_driving rcost_s, length_m len_m,
             st_x(st_lineinterpolatepoint(st_linemerge(geom), 0.5)) mid_lon, st_y(st_lineinterpolatepoint(st_linemerge(geom), 0.5)) mid_lat,
             st_x(st_startpoint(st_linemerge(geom))) s_lon, st_y(st_startpoint(st_linemerge(geom))) s_lat,
             st_x(st_endpoint(st_linemerge(geom))) t_lon, st_y(st_endpoint(st_linemerge(geom))) t_lat
      from public.nrn_road_edges where geometrytype(st_linemerge(geom)) = 'LINESTRING'""")
    v1 = e[["source", "s_lon", "s_lat"]].rename(columns={"source": "vid", "s_lon": "lon", "s_lat": "lat"})
    v2 = e[["target", "t_lon", "t_lat"]].rename(columns={"target": "vid", "t_lon": "lon", "t_lat": "lat"})
    v = pd.concat([v1, v2]).drop_duplicates("vid").reset_index(drop=True)
    e = e.drop(columns=["s_lon", "s_lat", "t_lon", "t_lat"])
    # 不可通行的占位代价（1e12）当成断开
    for c in ("cost_s", "rcost_s"):
        e[c] = pd.to_numeric(e[c], errors="coerce")
        e.loc[e[c] > 1e9, c] = np.nan
    return e, v


def load_facilities(mconn, lconn) -> pd.DataFrame:
    """重点单位：医院（床位）、学校、隧道、地铁站、民防/救援/急救站、变电站。统一 kind/name/lon/lat/size。"""
    parts = []
    parts.append(df(mconn, """select 'hospital' kind, facility_name_in_english name, coalesce(nullif(regexp_replace(bed_capacity::text, '[^0-9.]', '', 'g'), ''), '0')::float size, facility_type sub,
                                 st_x(shape) lon, st_y(shape) lat from public.poi_doh_facilities
                              where facility_category ilike '%hospital%' or facility_type ilike '%hospital%'"""))
    parts.append(df(mconn, """select 'clinic' kind, facility_name_in_english name, 0::float size, facility_type sub, st_x(shape) lon, st_y(shape) lat
                              from public.poi_doh_facilities where not (facility_category ilike '%hospital%' or facility_type ilike '%hospital%')"""))
    parts.append(df(mconn, """select 'school' kind, coalesce(udm_elms_plotallocation_name_e, adek_sch_name_ar) name, 0::float size, adek_sch_education_type_en sub,
                                 st_x(shape) lon, st_y(shape) lat from public.poi_adek_schools_locations"""))
    parts.append(df(mconn, """select 'tunnel' kind, coalesce(roadtunneltype,'road tunnel') name, st_area(shape::geography)::float size, physicalstatus sub,
                                 st_x(st_centroid(shape)) lon, st_y(st_centroid(shape)) lat from public.udm_roadtunnel"""))
    parts.append(df(mconn, """select 'metro' kind, facility_name name, 0::float size, facility_type sub,
                                 st_x(st_transform(shape,4326)) lon, st_y(st_transform(shape,4326)) lat from public.metro_facilities"""))
    parts.append(df(mconn, """select 'civil_defense' kind, coalesce(nullif(civil_defense_center_eng_name,''), civil_defense_center_ar_name) || coalesce(' [' || nullif(civil_defense_center_category,'-') || ']', '') name,
                                 0::float size, service_type sub, st_x(shape) lon, st_y(shape) lat from public.poi_civildefense_centers"""))
    parts.append(df(mconn, """select 'rescue' kind, technical_rescue_stn_eng_name name, 0::float size, null sub, st_x(shape) lon, st_y(shape) lat from public.poi_technicalrescue_stations"""))
    parts.append(df(mconn, """select 'ambulance' kind, ambulance_station_english_name name, 0::float size, null sub, st_x(shape) lon, st_y(shape) lat from public.poi_ambulance_stations"""))
    parts.append(df(mconn, """select 'substation' kind, name, 0::float size, type sub, st_x(st_transform(st_centroid(shape),4326)) lon, st_y(st_transform(st_centroid(shape),4326)) lat
                              from public.adwea_e_substation where type ilike any(array['%primary%','%grid%','%132%','%400%','%220%']) limit 400"""))
    f = pd.concat(parts, ignore_index=True)
    f = f.dropna(subset=["lon", "lat"]).reset_index(drop=True)
    f["fid"] = np.arange(1, len(f) + 1)
    return f


def build_cache(cfg) -> dict:
    from .db import connect_source
    STATE.mkdir(parents=True, exist_ok=True)
    with connect_source(cfg, "liveability") as lc:
        e, v = load_road_graph(lc)
    with connect_source(cfg, "makani") as mc, connect_source(cfg, "liveability") as lc:
        f = load_facilities(mc, lc)
    e.to_parquet(STATE / "road_edges.parquet", index=False)
    v.to_parquet(STATE / "road_vertices.parquet", index=False)
    f.to_parquet(STATE / "facilities.parquet", index=False)
    return {"edges": len(e), "vertices": len(v), "facilities": f.groupby("kind").size().to_dict()}


def load_cache() -> dict:
    return {"edges": pd.read_parquet(STATE / "road_edges.parquet"), "vertices": pd.read_parquet(STATE / "road_vertices.parquet"),
            "facilities": pd.read_parquet(STATE / "facilities.parquet")}


if __name__ == "__main__":
    from .db import load_config
    print(json.dumps(build_cache(load_config()), ensure_ascii=False))
