"""第 0 步：原始数据原样入库（文件地理数据库，供 UDMS 迁移工具读）+ 逐层逐字段现状探查（治理前基线）。

在 ArcGIS Pro 克隆环境里跑：
  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/s0_stage_profile.py <工作目录>

原样：不改字段、不改值、不改几何；坐标系照源（AECOM shapefile 没有 .prj，按 UTM 40N 声明，记录在探查结果里）。
产物：<工作目录>/src_dmt.gdb、src_aecom_rda.gdb；<工作目录>/00_profile/profile.json
"""
import collections
import json
import os
import sys
import time

import arcpy

arcpy.env.overwriteOutput = True
# 数据源可用环境变量换（标准作业程序场景②，run_full.sh）；EXP_AEC_SRC=none 表示没有顾问模型
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from swbrain import client_data  # noqa: E402

DMT = os.environ.get("EXP_DMT_SRC", str(client_data.STORMWATER))
AEC = os.environ.get("EXP_AEC_SRC", str(client_data.model("RD-A") / "10+20%-1hr"))
SR = arcpy.SpatialReference(32640)
CAT_MAX = 60                                   # 不同值 ≤ 60 个的文本字段，记完整分布


def stage(work):
    out = {}
    for name, src, files in (("src_dmt", DMT, None), ("src_aecom_rda", AEC, None)):
        if src.lower() == "none":
            continue
        gdb = os.path.join(work, name + ".gdb")
        if arcpy.Exists(gdb):
            arcpy.management.Delete(gdb)
        arcpy.management.CreateFileGDB(work, name + ".gdb")
        shps = sorted(f for f in os.listdir(src) if f.lower().endswith(".shp") and not f.startswith("._"))
        for f in shps:
            p = os.path.join(src, f)
            fc = arcpy.ValidateTableName(os.path.splitext(f)[0], gdb)
            sr = arcpy.Describe(p).spatialReference
            declared = None
            if sr.name == "Unknown":
                declared = "无 .prj，按 UTM 40N（EPSG:32640）声明"
            arcpy.conversion.ExportFeatures(p, os.path.join(gdb, fc))
            if declared:
                arcpy.management.DefineProjection(os.path.join(gdb, fc), SR)
            out[f"{name}/{fc}"] = {"source": p, "sr": sr.name if not declared else declared}
            print(f"  {name}/{fc}", arcpy.management.GetCount(os.path.join(gdb, fc))[0], flush=True)
    return out


def profile(fc):
    d = arcpy.Describe(fc)
    flds = [f for f in arcpy.ListFields(fc) if f.type not in ("OID", "Geometry", "GlobalID") and not f.name.lower().startswith("shape")]
    names = [f.name for f in flds]
    n = 0
    null_geom = multipart = 0
    geoms = collections.Counter()
    filled = collections.Counter()
    zero = collections.Counter()
    blank = collections.Counter()
    cats = {f.name: collections.Counter() for f in flds if f.type == "String"}
    rng = {f.name: [None, None] for f in flds if f.type in ("Double", "Single", "Integer", "SmallInteger", "BigInteger")}
    for row in arcpy.da.SearchCursor(fc, ["SHAPE@WKB", "SHAPE@"] + names):
        n += 1
        wkb, g = row[0], row[1]
        if g is None:
            null_geom += 1
        else:
            if g.isMultipart:
                multipart += 1
            geoms[hash(bytes(wkb))] += 1
        for k, v in zip(names, row[2:]):
            if v is None:
                continue
            if isinstance(v, str):
                if v.strip() == "":
                    blank[k] += 1
                    continue
                if len(cats[k]) <= CAT_MAX or v in cats[k]:
                    cats[k][v] += 1
                else:
                    cats[k]["__其他值__"] += 1
            else:
                if v == 0:
                    zero[k] += 1
                if k in rng:
                    lo, hi = rng[k]
                    rng[k] = [v if lo is None else min(lo, v), v if hi is None else max(hi, v)]
            filled[k] += 1
    dup_geom = sum(c - 1 for c in geoms.values() if c > 1)
    res = {"count": n, "shape_type": d.shapeType, "sr": d.spatialReference.name, "null_geometry": null_geom, "multipart": multipart,
           "duplicate_geometry_extra": dup_geom, "fields": {}}
    for f in flds:
        k = f.name
        r = {"type": f.type, "filled": filled[k], "zero": zero[k], "blank": blank[k]}
        if k in cats:
            c = cats[k]
            r["distinct"] = len(c)
            r["values"] = dict(c.most_common(CAT_MAX)) if len(c) <= CAT_MAX + 1 else dict(c.most_common(20))
        if k in rng:
            r["range"] = rng[k]
        res["fields"][k] = r
    uid = [x for x in names if x.upper() in ("UNITID", "LABEL", "ID")]
    if uid:
        c = collections.Counter(r[0] for r in arcpy.da.SearchCursor(fc, [uid[0]]) if r[0] not in (None, "") and str(r[0]).strip())
        res["id_field"] = uid[0]
        res["duplicate_ids"] = {str(k): v for k, v in c.items() if v > 1}
    return res


def main(work):
    t0 = time.time()
    os.makedirs(os.path.join(work, "00_profile"), exist_ok=True)
    print("入库（原样）")
    st = stage(work)
    print("探查")
    P = {"staged": st, "layers": {}}
    for name in ("src_dmt", "src_aecom_rda"):
        if not arcpy.Exists(os.path.join(work, name + ".gdb")):
            continue
        arcpy.env.workspace = os.path.join(work, name + ".gdb")
        for fc in arcpy.ListFeatureClasses():
            P["layers"][f"{name}/{fc}"] = profile(fc)
            r = P["layers"][f"{name}/{fc}"]
            print(f"  {name}/{fc}: {r['count']} 条，空几何 {r['null_geometry']}，多部件 {r['multipart']}，重复几何 {r['duplicate_geometry_extra']}，"
                  f"重复编号 {len(r.get('duplicate_ids', {}))}", flush=True)
    P["seconds"] = round(time.time() - t0)
    json.dump(P, open(os.path.join(work, "00_profile", "profile.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("完成", P["seconds"], "s")


if __name__ == "__main__":
    main(sys.argv[1])
