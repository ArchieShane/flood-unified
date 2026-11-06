"""治理台账辅助（ArcGIS 环境）：把网络库里带错误的脏区导出成 json（错误码、消息、WKT），供 ledger.py 用。

  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/ledger_dirty.py <工作目录>
输出 <工作目录>/08_area/dirty.json
"""
import json
import os
import sys

import arcpy


def main(work):
    sys.stdout.reconfigure(encoding="utf-8")
    fd = os.path.join(work, "05_un", "Stormwater_AD.geodatabase", "main.UtilityNetwork")
    out = []
    for oid, g, code, msg in arcpy.da.SearchCursor(os.path.join(fd, "main.UN_6_DirtyAreas"), ["OID@", "SHAPE@", "ERRORCODE", "ERRORMESSAGE"]):
        out.append({"oid": oid, "code": code, "msg": msg or "", "wkt": g.WKT})
    os.makedirs(os.path.join(work, "08_area"), exist_ok=True)
    json.dump(out, open(os.path.join(work, "08_area", "dirty.json"), "w", encoding="utf-8"), ensure_ascii=False)
    # 属性规则错误：库里的 GDB_Validation*Errors 表（存的是 4326，取点坐标时投到 32640）
    sr = arcpy.SpatialReference(32640)
    ar = []
    mg = os.path.join(work, "05_un", "Stormwater_AD.geodatabase")
    for t in ("main.GDB_ValidationPointErrors", "main.GDB_ValidationLineErrors", "main.GDB_ValidationPolygonErrors"):
        p = os.path.join(mg, t)
        if not arcpy.Exists(p):
            continue
        flds = [f.name for f in arcpy.ListFields(p)]
        want = [f for f in ("RuleName", "ErrorMessage", "OriginClassName", "OriginGlobalID", "Severity") if f in flds]
        for r in arcpy.da.SearchCursor(p, ["SHAPE@"] + want):
            g = r[0].projectAs(sr) if r[0] is not None else None
            c = g.centroid if g is not None else None
            ar.append(dict(zip(want, r[1:]), x=c.X if c else None, y=c.Y if c else None))
    json.dump(ar, open(os.path.join(work, "08_area", "attr_errors.json"), "w", encoding="utf-8"), ensure_ascii=False, default=str)
    print(len(out), len(ar))


if __name__ == "__main__":
    main(sys.argv[1])
