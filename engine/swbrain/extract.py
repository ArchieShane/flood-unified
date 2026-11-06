"""测端：从三个源库只读取数。每个函数返回一张 DataFrame，字段名统一成小写英文。"""
from __future__ import annotations
import pandas as pd
from .db import df

# ---------- pipe 库：管网图 ----------

def load_edges(conn, cfg) -> pd.DataFrame:
    n = cfg["source"]["pipe"]["schema_network"]
    sql = f"""
      select gid, source, target, from_node, to_node, dir_source, dir_conflict, invert_valid,
             invert_up, invert_dn, len_m, pipe_diameter, pipe_material, pipe_type, pipe_subtype,
             status, zone_name, outfallid, sourcetable, sourceid, unitid
      from {n}.sw_edge
    """
    e = df(conn, sql)
    e["pipe_diameter"] = pd.to_numeric(e["pipe_diameter"], errors="coerce")
    e["len_m"] = pd.to_numeric(e["len_m"], errors="coerce")
    return e


def load_vertices(conn, cfg) -> pd.DataFrame:
    n = cfg["source"]["pipe"]["schema_network"]
    sql = f"""
      select id, node_table, unitid, cnt, ein, eout,
             st_x(the_geom) x, st_y(the_geom) y,
             st_x(st_transform(the_geom, 4326)) lon, st_y(st_transform(the_geom, 4326)) lat
      from {n}.sw_edge_vertices_pgr
    """
    return df(conn, sql)


def load_node_outfall(conn, cfg) -> pd.DataFrame:
    n = cfg["source"]["pipe"]["schema_network"]
    return df(conn, f"select node, outfall, hops from {n}.sw_node_outfall")


def load_dangling(conn, cfg) -> pd.DataFrame:
    n = cfg["source"]["pipe"]["schema_network"]
    return df(conn, f"select gid, sourcetable, sourceid, issue, detail, len_m from {n}.sw_edge_dangling")


# ---------- pipe 库：资产属性（挂到图顶点上） ----------

def load_inlet_levels(conn, cfg) -> pd.DataFrame:
    a, n = cfg["source"]["pipe"]["schema_assets"], cfg["source"]["pipe"]["schema_network"]
    sql = f"""
      select an.vertex_id, an.unitid, 'inlet' node_table, i.cover_level, i.invert_level, i.unittype
      from {n}.sw_asset_node an join {a}.inlet i on i.unitid = an.unitid
      where an.node_table = 'inlet' and an.vertex_id is not null
      union all
      select an.vertex_id, an.unitid, 'catchbasin', c.cover_level, c.invert_level, c.unittype
      from {n}.sw_asset_node an join {a}.catchbasin c on c.unitid = an.unitid
      where an.node_table = 'catchbasin' and an.vertex_id is not null
    """
    return df(conn, sql)


def load_outfalls(conn, cfg) -> pd.DataFrame:
    a, n = cfg["source"]["pipe"]["schema_assets"], cfg["source"]["pipe"]["schema_network"]
    sql = f"""
      select o.unitid, o.invert_level, o.base_level, o.unittypedesc, o.numberofpipes, o.pipe_diameter,
             an.vertex_id, st_x(st_centroid(o.geom)) x, st_y(st_centroid(o.geom)) y,
             st_x(st_transform(st_centroid(o.geom),4326)) lon, st_y(st_transform(st_centroid(o.geom),4326)) lat
      from {a}.outfall o left join {n}.sw_asset_node an on an.unitid = o.unitid and an.node_table='outfall'
    """
    return df(conn, sql)


def load_storage(conn, cfg) -> pd.DataFrame:
    """塘、调蓄池、渗井、泵，统一成 kind/unitid/volume_m3/x/y/vertex_id。"""
    a, n = cfg["source"]["pipe"]["schema_assets"], cfg["source"]["pipe"]["schema_network"]
    st = cfg["model"]["storage"]
    sql = f"""
      select 'pond' kind, p.unitid, nullif(p.volume,0) volume_m3, nullif(p.asset_area,0) area_m2,
             null::float8 power_kw, null::text station, null::bigint vertex_id,
             st_x(st_centroid(p.geom)) x, st_y(st_centroid(p.geom)) y
      from {a}.pond p
      union all
      select 'reservoir', r.unitid,
             case when r.max_water_level>0 and r.bed_level>0 and r.reservoir_area>0
                  then (r.max_water_level-r.bed_level)*r.reservoir_area else null end,
             nullif(r.reservoir_area,0), null, null, null,
             st_x(st_centroid(r.geom)), st_y(st_centroid(r.geom))
      from {a}.sw_reservoirstructure r
      union all
      select 'soakaway', s.unitid,
             case when s.soakaway_length>0 and s.soakaway_width>0 and s.cover_level>s.bottom_level and s.bottom_level>0
                  then s.soakaway_length*s.soakaway_width*(s.cover_level-s.bottom_level)*{st['soakaway_porosity']}
                  when s.soakaway_diameter>0 and s.cover_level>s.bottom_level and s.bottom_level>0
                  then 3.1416*power(s.soakaway_diameter/2000.0,2)*(s.cover_level-s.bottom_level)*{st['soakaway_porosity']}
                  else null end,
             null, null, null, an.vertex_id,
             st_x(st_centroid(s.geom)), st_y(st_centroid(s.geom))
      from {a}.soakaway s left join {n}.sw_asset_node an on an.unitid=s.unitid and an.node_table='soakaway'
      union all
      select 'pump', pp.unitid, null, null, nullif(pp.power_kw,0), pp.pump_station_id, an.vertex_id,
             st_x(st_centroid(pp.geom)), st_y(st_centroid(pp.geom))
      from {a}.ps_pump pp left join {n}.sw_asset_node an on an.unitid=pp.unitid and an.node_table='ps_pump'
    """
    s = df(conn, sql)
    s.loc[(s.kind == "soakaway") & s.volume_m3.isna(), "volume_m3"] = st["soakaway_default_m3"]
    s.loc[(s.kind == "reservoir") & s.volume_m3.isna(), "volume_m3"] = st["reservoir_default_m3"]
    s["volume_estimated"] = s.volume_m3.isna() | (s.kind == "soakaway") | (s.kind == "reservoir")
    return s


def load_edge_geoms(conn, cfg, gids: list[int]) -> pd.DataFrame:
    """只为要输出的管段取几何（GeoJSON，WGS84）。"""
    if not gids:
        return pd.DataFrame(columns=["gid", "geojson"])
    n = cfg["source"]["pipe"]["schema_network"]
    return df(conn, f"select gid, st_asgeojson(st_transform(st_force2d(geom),4326), 6) geojson from {n}.sw_edge where gid = any(%s)", (list(map(int, gids)),))


# ---------- makani 库：规划审批中的雨水结构、区界 ----------

def load_planned(conn) -> pd.DataFrame:
    sql = """
      select objectid, project_name, review_status, data_source, review_date::date review_date,
             pumpstation, chamber, storagebasin, overflow, outfall, weir,
             st_x(st_transform(st_centroid(shape),32640)) x, st_y(st_transform(st_centroid(shape),32640)) y,
             st_x(st_centroid(shape)) lon, st_y(st_centroid(shape)) lat
      from public.storm_water_structure
      where shape is not null
    """
    return df(conn, sql)


def load_districts(conn) -> pd.DataFrame:
    sql = """
      select districtid, nameenglish, municipalityname, population,
             st_asgeojson(st_simplifypreservetopology(shape, 0.0005), 5) geojson
      from public.udm_district
    """
    return df(conn, sql)


# ---------- 宜居库：分区雨水堵塞统计 ----------

def load_blockage(conn) -> pd.DataFrame:
    sql = """
      select district_id, data->>'District_Name_Eng' district_name,
             (data->>'Total_Blockages')::float total_blockages,
             (data->>'Network_Length_KM')::float network_km,
             (data->>'SW_Blockage_per_100KM')::float blockage_per_100km,
             data->>'Notes' notes
      from public.fact_oi_indicators where indicator_type = 'sewage_blocakge_sws'
    """
    return df(conn, sql)
