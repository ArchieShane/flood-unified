"""按汇水区统计治理前后情况（汇水区报告用）：原始数据量、原始问题、治理处理、治理后网络与剩余错误。
用法（ArcGIS Pro 环境）：python tools/exp_gov/area_stats.py --name RD-A --domain out/dataqc/RD-A/input/rda_domain.geojson
输出：<work>/08_area/<name>_stats.json
"""
import argparse
import collections
import csv
import json
import os
import sys

import arcpy

W = os.environ.get("EXP_GOV_WORK", r"D:/swbrain_work/exp_gov")
DMT_LAYERS = ["PIPELINE", "MANHOLECHAMBER", "INLET", "CATCHBASIN", "SOAKAWAY", "PETROLINTERCEPTOR", "SW_CAPPEDEND", "OUTFALL", "PUMP",
              "MOVABLEPUMP", "POND"]
SENT = {-999.0, -99.0, -111.11, 999.0, 2760.0, 2576.0, 10308.0}
ELEV = {"MANHOLECHAMBER": ["COVER_LEVE", "INVERT_LEV"], "INLET": ["COVER_LEVE", "INVERT_LEV"], "CATCHBASIN": ["COVER_LEVE", "INVERT_LEV"],
        "SW_CAPPEDEND": ["COVER_LEVE", "INVERT_LEV"], "OUTFALL": ["INVERT_LEV"], "PIPELINE": ["INVERT_LEV", "INVERT_L_1"]}
UN = os.path.join(W, "07_export", "Stormwater_AD_reporting.gdb")


def domain(path, buf=0):
    g = json.load(open(path, encoding="utf-8"))["features"][0]["geometry"]
    rings = [g["coordinates"][0]] if g["type"] == "Polygon" else [p[0] for p in g["coordinates"]]
    arr = arcpy.Array([arcpy.Array([arcpy.Point(*p[:2]) for p in r]) for r in rings])
    poly = arcpy.Polygon(arr, arcpy.SpatialReference(32640))
    return poly.buffer(buf) if buf else poly


def field_names(fc):
    return {f.name.upper(): f.name for f in arcpy.ListFields(fc)}


def raw_stats(poly):
    src = os.path.join(W, "src_dmt.gdb")
    out, oids = {}, {}
    for lay in DMT_LAYERS:
        fc = os.path.join(src, lay)
        if not arcpy.Exists(fc):
            continue
        fn = field_names(fc)
        st_f = fn.get("STATUS")
        mat_f = fn.get("PIPE_MATER") if lay == "PIPELINE" else None
        inst_f = fn.get("ASSET_INST")
        cols = ["OID@", "SHAPE@WKT", "SHAPE@Z" if lay != "PIPELINE" else "SHAPE@"] + [x for x in (st_f, mat_f, inst_f) if x]
        el = [fn[e] for e in ELEV.get(lay, []) if e in fn]
        cols += el
        n, status, mats, geoms, znz, old = 0, collections.Counter(), collections.Counter(), collections.Counter(), 0, 0
        elev = {e: collections.Counter() for e in el}
        ids = set()
        for r in arcpy.da.SearchCursor(fc, cols, spatial_filter=poly):
            n += 1
            ids.add(r[0])
            geoms[r[1]] += 1
            g = r[2]
            if lay == "PIPELINE":
                if g and any(abs(p.Z or 0) > 1e-9 for part in g for p in part if p):
                    znz += 1
            elif g not in (None, 0) and abs(g) > 1e-9:
                znz += 1
            i = 3
            if st_f:
                status[str(r[i])] += 1
                i += 1
            if mat_f:
                mats[str(r[i])] += 1
                i += 1
            if inst_f:
                d = r[i]
                if d is not None and d.year <= 1900:
                    old += 1
                i += 1
            for e, v in zip(el, r[i:]):
                k = "空" if v is None else "0" if v == 0 else "哨兵" if round(v, 2) in SENT else "有值"
                elev[e][k] += 1
        if not n:
            continue
        oids[lay] = ids
        out[lay] = {"n": n, "status": dict(status), "dup_geom_extra": sum(c - 1 for c in geoms.values() if c > 1), "z_nonzero": znz,
                    "installdate_le_1900": old, "elev": {e: dict(c) for e, c in elev.items()}}
        if mat_f:
            out[lay]["material"] = dict(mats)
    return out, oids


def records(oids):
    g = os.path.join(W, "07_export", "governance_records.gdb")
    exc, iss = collections.Counter(), collections.Counter()
    for r in arcpy.da.SearchCursor(os.path.join(g, "EXCLUDED"), ["layer", "src_oid", "reason"]):
        if r[1] in oids.get(r[0], ()):
            exc[f"{r[0]}｜{r[2]}"] += 1
    for r in arcpy.da.SearchCursor(os.path.join(g, "ISSUES"), ["layer", "src_oid", "code"]):
        if r[1] in oids.get(r[0], ()):
            iss[f"{r[2]}｜{r[0]}"] += 1
    con = collections.Counter()
    for r in arcpy.da.SearchCursor(os.path.join(g, "CONFLICTS"), ["cls", "field"]):
        con[f"{r[0]}.{r[1]}"] += 1
    return {"excluded": dict(exc), "issues": dict(iss), "conflicts_aecom": dict(con)}


def governed(poly, poly_buf):
    out = {}
    for cls in ("StormwaterLine", "StormwaterDevice", "StormwaterJunction", "StructureJunction", "StructureBoundary"):
        fc = os.path.join(UN, cls)
        fn = field_names(fc)
        cols = ["d_assetgroup", "d_assettype", "smasubnetworkname", "src_key", "src_aecom", "src_layer", "isconnected"]
        cols = [c for c in cols if c.upper() in fn]
        grp, sub, orig, n = collections.Counter(), collections.Counter(), collections.Counter(), 0
        for r in arcpy.da.SearchCursor(fc, cols, spatial_filter=poly):
            d = dict(zip(cols, r))
            n += 1
            grp[f"{d.get('d_assetgroup')} / {d.get('d_assettype')}"] += 1
            s = d.get("smasubnetworkname")
            sub["无子网" if s in (None, "", "Unknown") else s] += 1
            if d.get("src_key"):
                orig["DMT 主记录"] += 1
            elif d.get("src_aecom"):
                orig["AECOM 补入"] += 1
            else:
                orig[f"派生 / 生成（{d.get('src_layer') or '—'}）"] += 1
        out[cls] = {"n": n, "groups": dict(grp), "subnetworks": dict(sub.most_common()), "origin": dict(orig)}
    # 网络错误（UDMS Summarize UN Errors 的结果），按 300 m 外扩统计，避免边界上的漏掉
    e = collections.Counter()
    for r in arcpy.da.SearchCursor(os.path.join(W, "06_validate/un_errors/Summary.geodatabase/main.Errors"),
                                   ["ErrorCode", "OriginAssetGroup", "DestinationAssetGroup"], spatial_filter=poly_buf):
        e[f"{r[0]}｜{r[1]} → {r[2]}"] += 1
    out["un_errors_300m"] = dict(e.most_common())
    out["attr_rule_errors_300m"] = attr_errors(poly_buf)
    iso = collections.Counter()
    with open(os.path.join(W, "06_validate/remaining_isolated.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if poly_buf.contains(arcpy.PointGeometry(arcpy.Point(float(r["root_x"]), float(r["root_y"])), poly.spatialReference)):
                iso[r["class"]] += int(r["n_pipes"])
    out["isolated_systems_pipes_300m"] = dict(iso)
    return out


def attr_errors(geom):
    """属性规则错误：直接读网络库的 GDB_Validation*Errors（UDMS Summarize Attribute Rules 在本库跑不成），按规则名计数并列出要素"""
    g = os.path.join(W, "05_un", "Stormwater_AD.geodatabase")
    cnt, items = collections.Counter(), []
    for t in ("main.GDB_ValidationPointErrors", "main.GDB_ValidationLineErrors", "main.GDB_ValidationPolygonErrors"):
        fn = field_names(os.path.join(g, t))
        cols = ["RuleName", "ErrorMessage"] + [fn[k] for k in ("ORIGINOBJECTCLASSNAME", "ORIGINGLOBALID", "ORIGINOBJECTID") if k in fn] + ["SHAPE@XY"]
        for r in arcpy.da.SearchCursor(os.path.join(g, t), cols, spatial_filter=geom):
            cnt[f"{r[0]}｜{(r[1] or '').strip()}"] += 1
            items.append([str(x) for x in r[:-1]] + [round(r[-1][0], 6), round(r[-1][1], 6)])
    return {"by_rule": dict(cnt.most_common()), "items": items}


def subnet_stats(name, poly):
    """一个子网（如 RD-A 出水口的子网）的要素构成、来源、管长、控制器，以及子网范围内的错误"""
    out, ext = {}, None
    for cls in ("StormwaterLine", "StormwaterDevice", "StormwaterJunction"):
        fc = os.path.join(UN, cls)
        fn = field_names(fc)
        cols = [c for c in ("d_assetgroup", "d_assettype", "src_key", "src_aecom", "src_layer", "assetid", "issubnetworkcontroller")
                if c.upper() in fn] + ["SHAPE@"]
        grp, orig, length, ctl, inside = collections.Counter(), collections.Counter(), 0.0, [], 0
        for r in arcpy.da.SearchCursor(fc, cols, where_clause=f"smasubnetworkname = '{name}'"):
            d = dict(zip(cols, r))
            g = d["SHAPE@"]
            grp[f"{d['d_assetgroup']} / {d['d_assettype']}"] += 1
            orig["DMT 主记录" if d.get("src_key") else "AECOM 补入" if d.get("src_aecom") else f"派生 / 生成（{d.get('src_layer') or '—'}）"] += 1
            if cls == "StormwaterLine":
                length += g.length
            if d.get("issubnetworkcontroller") == 1:
                ctl.append(d.get("assetid"))
            if not poly.disjoint(g):
                inside += 1
            e = g.extent
            ext = e if ext is None else arcpy.Extent(min(ext.XMin, e.XMin), min(ext.YMin, e.YMin), max(ext.XMax, e.XMax), max(ext.YMax, e.YMax))
        out[cls] = {"n": sum(grp.values()), "in_catchment": inside, "groups": dict(grp), "origin": dict(orig)}
        if length:
            out[cls]["length_km"] = round(length / 1000, 2)
        if ctl:
            out[cls]["controllers"] = ctl
    box = arcpy.Polygon(ext.polygon.getPart(), poly.spatialReference) if ext else None   # 要带坐标系，错误表是 4326
    out["extent"] = [ext.XMin, ext.YMin, ext.XMax, ext.YMax] if ext else None
    if box:
        e = collections.Counter()
        for r in arcpy.da.SearchCursor(os.path.join(W, "06_validate/un_errors/Summary.geodatabase/main.Errors"), ["ErrorCode"], spatial_filter=box):
            e[str(r[0])] += 1
        out["un_errors_in_extent"] = dict(e)
        out["attr_rule_errors_in_extent"] = attr_errors(box)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--subnet", nargs="*", default=[], help="另外统计的子网名（如 RD-A 出水口所在子网）")
    a = ap.parse_args()
    poly, poly_buf = domain(a.domain), domain(a.domain, 300)
    raw, oids = raw_stats(poly)
    res = {"name": a.name, "domain_km2": round(poly.area / 1e6, 3), "raw_dmt": raw, "records": records(oids), "governed": governed(poly, poly_buf),
           "subnets": {n: subnet_stats(n, poly) for n in a.subnet}}
    od = os.path.join(W, "08_area")
    os.makedirs(od, exist_ok=True)
    p = os.path.join(od, f"{a.name}_stats.json")
    json.dump(res, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(p)


if __name__ == "__main__":
    main()
