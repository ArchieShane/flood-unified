"""开源路线：区域截取（同 tools/exp_gov/v_extract.py 的输出格式，供 region.py check / build 用；不用 arcpy）。

  python tools/oss_gov/g_extract.py raw|gov <工作目录> <范围 geojson> <输出目录>

raw：DMT 原样（<工作目录>/src_dmt.gdb）；gov：治理后（<工作目录>/07_export/Stormwater_AD_reporting.gdb，两条路线导出的报表库同结构）。
范围：计算域外扩 300 m 的矩形，与之相交的要素（同 ArcGIS 版的 INTERSECTS）。输出 <输出目录>/extract_<raw|gov>.json。
"""
import json
import os
import sys
import warnings

import numpy as np
import pyogrio
import shapely
from shapely.geometry import box

warnings.filterwarnings("ignore", message="Measured")
BUF = 300.0


def area_box(path):
    import geopandas as gpd
    b = gpd.read_file(path).to_crs(32640).total_bounds
    return box(b[0] - BUF, b[1] - BUF, b[2] + BUF, b[3] + BUF)


def rows(gdb, layer, fields, flt, group_from=None):
    info = pyogrio.read_info(gdb, layer=layer)
    have = {f.lower(): f for f in info["fields"]}
    cols = [have[f.lower()] for f in fields if f.lower() in have]
    df = pyogrio.read_dataframe(gdb, layer=layer, columns=cols + ([group_from] if group_from and group_from in have.values() else []), bbox=flt.bounds)
    df = df[df.geometry.notna() & df.geometry.intersects(flt)]
    out = []
    for r in df.itertuples(index=False):
        g = r.geometry
        d = {}
        for f in fields:
            v = getattr(r, have[f.lower()], None) if f.lower() in have else None
            if isinstance(v, float) and np.isnan(v):
                v = None
            elif hasattr(v, "isoformat"):
                v = None if v != v else v.isoformat()
            elif isinstance(v, np.integer):
                v = int(v)
            d[f] = v
        if group_from:
            d["group"] = getattr(r, group_from, None)
        if g.geom_type in ("LineString", "MultiLineString"):
            c = shapely.get_coordinates(g)
            d["_xy"] = [list(c[0]), list(c[-1])]
            d["_len"] = float(g.length)
            d["_vx"] = [list(p) for p in c[1:-1]]
        else:
            c = shapely.get_coordinates(g)[0]
            d["_xy"] = [float(c[0]), float(c[1])]
        out.append(d)
    return out


def raw(work, flt):
    g = os.path.join(work, "src_dmt.gdb")
    return {
        "pipes": rows(g, "PIPELINE", ["OBJECTID_1", "UNITID", "STATUS", "PIPE_SUBTY", "PIPE_TYPE", "INVERT_LEV", "INVERT_L_1", "PIPE_DIAME", "PIPE_MATER"], flt),
        "manholes": rows(g, "MANHOLECHAMBER", ["OBJECTID_1", "UNITID", "STATUS", "COVER_LEVE", "INVERT_LEV"], flt),
        "outfalls": rows(g, "OUTFALL", ["OBJECTID_1", "UNITID", "STATUS", "INVERT_LEV", "UNITTYPEDE"], flt),
        "pumps": rows(g, "PUMP", ["OBJECTID_1", "UNITID", "STATUS", "FLOW_RATE", "HEAD"], flt),
        "inlets": rows(g, "INLET", ["OBJECTID_1", "UNITID", "STATUS", "COVER_LEVE", "INVERT_LEV"], flt)
                  + rows(g, "CATCHBASIN", ["OBJECTID_1", "UNITID", "STATUS", "COVER_LEVE", "INVERT_LEV"], flt),
        "soakaways": rows(g, "SOAKAWAY", ["OBJECTID_1", "UNITID", "STATUS", "COVER_LEVE", "INVERT_LEV"], flt),
    }


def gov(work, flt):
    rg = os.path.join(work, "07_export", "Stormwater_AD_reporting.gdb")
    L = rows(rg, "StormwaterLine", ["assetid", "ASSETGROUP", "assetstate", "diameter", "upelev", "downelev", "manningn", "smasubnetworkname",
                                    "src_layer", "src_aecom"], flt, group_from="d_assetgroup")
    D = rows(rg, "StormwaterDevice", ["assetid", "ASSETGROUP", "assetstate", "elevation", "coverelev", "boundarytype", "tailwater", "pumpcurveid",
                                      "onelev", "offelev", "rimelev", "initelev", "maxelev", "station_name", "src_layer", "src_aecom",
                                      "smasubnetworkname"], flt, group_from="d_assetgroup")
    V = rows(rg, "StructureJunction", ["assetid", "assetstate", "rimelev", "invertelev", "src_layer", "src_aecom"], flt)
    return {"lines": L, "devices": D, "vaults": V}


def main(mode, work, area, out):
    flt = area_box(area)
    os.makedirs(out, exist_ok=True)
    data = raw(work, flt) if mode == "raw" else gov(work, flt)
    json.dump(data, open(os.path.join(out, f"extract_{mode}.json"), "w", encoding="utf-8"), ensure_ascii=False, default=str)
    print({k: len(v) for k, v in data.items()})


if __name__ == "__main__":
    main(*sys.argv[1:5])
