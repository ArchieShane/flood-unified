"""验证建模第 1 步（ArcGIS 环境）：把 RD-A 范围内的管网要素导成 JSON，供 v_build.py（系统 Python）建 SWMM / ANUGA 输入。

  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/v_extract.py raw|gov <工作目录> <范围 geojson> [输出目录]

raw：未治理的 DMT 原样入库（src_dmt.gdb），字段原值，不做任何清洗（V-2）
gov：治理后的 Utility Network（05_un/Stormwater_AD.geodatabase），带子网名（V-3）
范围：计算域外扩 300 m 的矩形内的要素（v_build 再按计算域和连通性截取）。
输出 <工作目录>/07_verify/extract_<raw|gov>.json
"""
import json
import os
import sys

import arcpy

BUF = 300.0


def box(path):
    g = json.load(open(path, encoding="utf-8"))
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for x in c:
                walk(x)
    for f in g["features"]:
        walk(f["geometry"]["coordinates"])
    e = arcpy.Extent(min(xs) - BUF, min(ys) - BUF, max(xs) + BUF, max(ys) + BUF)
    return arcpy.Polygon(arcpy.Array([arcpy.Point(e.XMin, e.YMin), arcpy.Point(e.XMin, e.YMax), arcpy.Point(e.XMax, e.YMax),
                                      arcpy.Point(e.XMax, e.YMin)]), arcpy.SpatialReference(32640))


def rows(fc, fields, flt, where=None):
    out = []
    have = {f.name.lower() for f in arcpy.ListFields(fc)}
    missing = [f for f in fields if f.lower() not in have]          # 对照 N 没有湿井，UDMS 不加湿井字段 → 记空值
    fields = [f for f in fields if f.lower() in have]
    for r in arcpy.da.SearchCursor(fc, ["SHAPE@"] + fields, where, spatial_filter=flt, spatial_relationship="INTERSECTS"):
        g = r[0]
        if g is None:
            continue
        d = dict(zip(fields, r[1:]), **{f: None for f in missing})
        for k, v in list(d.items()):
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
        if g.type == "polyline":
            d["_xy"] = [[g.firstPoint.X, g.firstPoint.Y], [g.lastPoint.X, g.lastPoint.Y]]
            d["_len"] = g.length
            d["_vx"] = [[p.X, p.Y] for part in g for p in part if p is not None][1:-1]
        else:
            d["_xy"] = [g.firstPoint.X, g.firstPoint.Y]
        out.append(d)
    return out


def raw(work, flt):
    g = os.path.join(work, "src_dmt.gdb")
    return {
        "pipes": rows(os.path.join(g, "PIPELINE"), ["OBJECTID_1", "UNITID", "STATUS", "PIPE_SUBTY", "PIPE_TYPE", "INVERT_LEV", "INVERT_L_1", "PIPE_DIAME", "PIPE_MATER"], flt),
        "manholes": rows(os.path.join(g, "MANHOLECHAMBER"), ["OBJECTID_1", "UNITID", "STATUS", "COVER_LEVE", "INVERT_LEV"], flt),
        "outfalls": rows(os.path.join(g, "OUTFALL"), ["OBJECTID_1", "UNITID", "STATUS", "INVERT_LEV", "UNITTYPEDE"], flt),
        "pumps": rows(os.path.join(g, "PUMP"), ["OBJECTID_1", "UNITID", "STATUS", "FLOW_RATE", "HEAD"], flt),
        "inlets": rows(os.path.join(g, "INLET"), ["OBJECTID_1", "UNITID", "STATUS", "COVER_LEVE", "INVERT_LEV"], flt)
                  + rows(os.path.join(g, "CATCHBASIN"), ["OBJECTID_1", "UNITID", "STATUS", "COVER_LEVE", "INVERT_LEV"], flt),
        "soakaways": rows(os.path.join(g, "SOAKAWAY"), ["OBJECTID_1", "UNITID", "STATUS", "COVER_LEVE", "INVERT_LEV"], flt),
    }


def gov(work, flt):
    mg = os.path.join(work, "05_un", "Stormwater_AD.geodatabase")
    fd = next(p for p in (os.path.join(mg, "main.UtilityNetwork"), os.path.join(mg, "UtilityNetwork")) if arcpy.Exists(p))

    def p(n):
        for x in (os.path.join(fd, n), os.path.join(fd, "main." + n)):
            if arcpy.Exists(x):
                return x
    line, dev, sj = p("StormwaterLine"), p("StormwaterDevice"), p("StructureJunction")
    ag = {fc: {k: v["Name"] for k, v in arcpy.da.ListSubtypes(fc).items()} for fc in (line, dev)}
    L = rows(line, ["assetid", "ASSETGROUP", "assetstate", "diameter", "upelev", "downelev", "manningn", "smasubnetworkname", "src_layer", "src_aecom"], flt)
    for r in L:
        r["group"] = ag[line].get(r["ASSETGROUP"])
    D = rows(dev, ["assetid", "ASSETGROUP", "assetstate", "elevation", "coverelev", "boundarytype", "tailwater", "pumpcurveid", "onelev", "offelev",
                   "rimelev", "initelev", "maxelev", "station_name", "src_layer", "src_aecom", "smasubnetworkname"], flt)
    for r in D:
        r["group"] = ag[dev].get(r["ASSETGROUP"])
    V = rows(sj, ["assetid", "assetstate", "rimelev", "invertelev", "src_layer", "src_aecom"], flt)
    return {"lines": L, "devices": D, "vaults": V}


def main(mode, work, area, out=None):
    flt = box(area)
    out = out or os.path.join(work, "07_verify")        # 第 4 个参数：输出目录（区域流程 region.py 用，各区域分开放）
    os.makedirs(out, exist_ok=True)
    data = raw(work, flt) if mode == "raw" else gov(work, flt)
    json.dump(data, open(os.path.join(out, f"extract_{mode}.json"), "w", encoding="utf-8"), ensure_ascii=False, default=str)
    print({k: len(v) for k, v in data.items()})


if __name__ == "__main__":
    main(*sys.argv[1:5])
