"""第 2 步（映射）+ 第 3 步（建迁移工作区）：UDMS 标准迁移流程。

  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/s3_mapping.py <工作目录>

1. UDMS CreateSimpleDataMapping：预处理库 → Expanded_local 的映射表初稿（按 tgt_ag / tgt_at 两个字段分类型）
2. 按确认表（docs/Expanded_本地化与字段映射_待确认_20261023.md 第二节 T-1…T-11、第五节）填目标类 / 资产组 / 资产类型、关联键、关联表、不复制字段
3. UDMS CopyFieldsAndDomainsFromMapping：把预处理库里 Expanded 没有的字段（溯源、曼宁系数、出水口边界、湿井参数等，L-6 / L-7）加到资产包
4. UDMS CreateMigrationWorkspace：扩展资产包 + 数据装载工作区（Pro Data Loading Tools）
产物在 <工作目录>/03_mapping/。服务范围面 = 预处理库全部要素范围外扩 1 km 的矩形（Utility Network 必需）。
"""
import json
import os
import sys
import time

import arcpy
import openpyxl

arcpy.env.overwriteOutput = True
UDMS = r"D:/阿布扎比/客户数据/arcgis/UtilityDataManagementSupport.atbx"
SR = arcpy.SpatialReference(32640)
# (预处理类, 类型值) → (目标类, 资产组, 资产类型, 关联键)
MAP = {
    ("MH_VAULT", None): ("StructureJunction", "Sewer Storm Vault", "Manhole", None),
    ("MH_CHANNEL", None): ("StormwaterDevice", "Manhole Channel", "Manhole Channel", "MHC"),
    ("PIPE", "Gravity Pipe"): ("StormwaterLine", "Gravity Pipe", "Stormwater Gravity Pipe", None),
    ("PIPE", "Perforated Pipe"): ("StormwaterLine", "Perforated Pipe", "Stormwater Perforated Pipe", None),
    ("PIPE", "Force Pipe"): ("StormwaterLine", "Force Pipe", "Stormwater Force Pipe", None),
    ("CATCH_BASIN", "Drop Inlet"): ("StormwaterDevice", "Catch Basin", "Drop Inlet", None),
    ("CATCH_BASIN", "Infiltration"): ("StormwaterDevice", "Catch Basin", "Infiltration", None),
    ("CATCH_BASIN", "Sediment Trap"): ("StormwaterDevice", "Catch Basin", "Sediment Trap", None),
    ("END_CAP", None): ("StormwaterJunction", "Fitting", "End Cap", None),
    ("OUTFALL", None): ("StormwaterDevice", "Outfall", "Non-Permit Outfall", None),
    ("FLAP_GATE", None): ("StormwaterDevice", "Outlet", "Flap Gate", None),
    ("PUMP", None): ("StormwaterDevice", "Pump", "Pump", None),
    ("WETWELL", None): ("StormwaterDevice", "Connection", "Pipe Connection", None),
    ("GEN_CONNECTION", None): ("StormwaterJunction", "Fitting", "Transition", None),            # 不同资产组管首尾相接处加的接点。第七轮改为管件 Transition（P1：Pipe Connection 官方只用于检查井里的管端）
    ("SYSTEM_OUTFLOW", None): ("StormwaterDevice", "System Outflow", "Outflow", None),          # 第五轮：内陆系统终点的系统出流点
    ("BMP_POINT", None): ("StormwaterDevice", "BMP Point", "BMP Point", "POND"),
    ("POND", "Retention Basin"): ("StructureBoundary", "BMP Boundary", "Retention Basin", None),
    ("POND", "Detention Basin"): ("StructureBoundary", "BMP Boundary", "Detention Basin", None),
    ("POND", "Sediment Trap"): ("StructureBoundary", "BMP Boundary", "Sediment Trap", None),
}
ASSOC = [  # 关联键, 方向（被映射的是内容物，关联对象是容器 → To From）, 容器类, 表达式, 搜索距离 m
    ("MHC", "To From", "MH_VAULT", None, 0.01),        # T-1：井内流道在检查井结构里
    ("POND", "To From", "POND", None, 0.01),           # T-10：塘内 BMP 点在塘边界里
]
NO_COPY = [("*", "tgt_ag"), ("*", "tgt_at")]


def territory(prep, out):
    xs, ys = [], []
    arcpy.env.workspace = prep
    for fc in arcpy.ListFeatureClasses():
        e = arcpy.Describe(fc).extent
        if e.XMin and e.XMin == e.XMin:
            xs += [e.XMin, e.XMax]
            ys += [e.YMin, e.YMax]
    gdb = os.path.join(out, "territory.gdb")
    if not arcpy.Exists(gdb):
        arcpy.management.CreateFileGDB(out, "territory.gdb")
    fc = os.path.join(gdb, "ServiceTerritory")
    arcpy.management.CreateFeatureclass(gdb, "ServiceTerritory", "POLYGON", spatial_reference=SR, has_z="ENABLED")
    b = 1000
    ring = arcpy.Array([arcpy.Point(x, y, 0) for x, y in ((min(xs) - b, min(ys) - b), (min(xs) - b, max(ys) + b), (max(xs) + b, max(ys) + b),
                                                          (max(xs) + b, min(ys) - b), (min(xs) - b, min(ys) - b))])
    with arcpy.da.InsertCursor(fc, ["SHAPE@"]) as c:
        c.insertRow([arcpy.Polygon(ring, SR, True)])
    return fc


def fill(xlsx, log):
    wb = openpyxl.load_workbook(xlsx)
    ws = wb["Data Mappings"]
    hdr = [c.value for c in ws[1]]
    col = {h: i for i, h in enumerate(hdr)}
    unmapped = []
    for row in ws.iter_rows(min_row=2):
        cls = row[col["Source Class"]].value
        if not cls:
            continue
        typ = row[col["Source Type Name"]].value
        typ = None if typ in (None, "", "NULL") else str(typ)
        key = (cls, typ) if (cls, typ) in MAP else (cls, None)
        m = MAP.get(key)
        if m is None:
            unmapped.append((cls, typ, row[col["Source Row Count"]].value))
            continue
        row[col["Target Class"]].value, row[col["Target Subtype (Asset Group)"]].value, row[col["Target Asset Type"]].value = m[:3]
        if m[3]:
            row[col["Association Key"]].value = m[3]
        log.append({"source": cls, "type": typ, "count": row[col["Source Row Count"]].value, "where": row[col["Source Definition Query"]].value, "target": m[:3],
                    "assoc": m[3]})
    a = wb["Associations"]
    for r in list(a.iter_rows(min_row=2)):
        for c in r:
            c.value = None
    for i, r in enumerate(ASSOC, start=2):
        for j, v in enumerate(r, start=1):
            a.cell(i, j).value = v
    e = wb["Exclude Copy Fields"]
    for i, r in enumerate(NO_COPY, start=2):
        for j, v in enumerate(r, start=1):
            e.cell(i, j).value = v
    wb.save(xlsx)
    return unmapped


def main(work):
    t0 = time.time()
    prep = os.path.join(work, "02_prep", "prep.gdb")
    ap = os.path.join(work, "Expanded_local.gdb")
    out = os.path.join(work, "03_mapping")
    os.makedirs(out, exist_ok=True)
    arcpy.ImportToolbox(UDMS)
    rep = {"steps": []}

    st = arcpy.management.CreateTable("memory", "source_types")[0]
    arcpy.management.AddFields(st, [["SourceType", "TEXT"], ["MatchType", "TEXT"], ["SourceClass", "TEXT"]])
    with arcpy.da.InsertCursor(st, ["SourceType", "MatchType", "SourceClass"]) as c:
        c.insertRow(["tgt_ag", "include", "*"])
        c.insertRow(["tgt_at", "include", "*"])
    print("1 UDMS CreateSimpleDataMapping", flush=True)
    r = arcpy.udms.CreateSimpleDataMapping(prep, ap, out, "mapping_prep", "COUNT_FEATURES", st, None, "ONLY_UN")
    xlsx = r.getOutput(0)
    rep["steps"].append({"tool": "udms.CreateSimpleDataMapping", "out": xlsx, "messages": r.getMessages()[-2000:]})
    print("  ", xlsx, flush=True)

    print("2 按确认表填映射", flush=True)
    log = []
    unmapped = fill(xlsx, log)
    rep["mapping"], rep["unmapped"] = log, unmapped
    for x in log:
        print("  ", x, flush=True)
    if unmapped:
        print("  没有映射的源类型：", unmapped, flush=True)

    print("3 UDMS CopyFieldsAndDomainsFromMapping", flush=True)
    # 这个工具在映射表带关联时要写 associations.csv 却没有输出目录（UDMS 3.7.2 自身限制，报 ERROR 000110: None does not exist）。
    # 复制字段与关联无关，所以给它一份去掉关联键和关联表的副本，字段内容完全相同。
    xlsx_cf = os.path.join(out, "mapping_copyfields.xlsx")
    wb = openpyxl.load_workbook(xlsx)
    ws = wb["Data Mappings"]
    k = [c.value for c in ws[1]].index("Association Key")
    for row in ws.iter_rows(min_row=2):
        row[k].value = None
    for row in wb["Associations"].iter_rows(min_row=2):
        for c in row:
            c.value = None
    wb.save(xlsx_cf)
    r = arcpy.udms.CopyFieldsAndDomainsFromMapping(prep, ap, xlsx_cf, "LOWER")
    rep["steps"].append({"tool": "udms.CopyFieldsAndDomainsFromMapping", "messages": r.getMessages()[-3000:]})
    print(r.getMessages()[-1500:], flush=True)

    print("4 UDMS CreateMigrationWorkspace", flush=True)
    terr = territory(prep, out)
    r = arcpy.udms.CreateMigrationWorkspace(prep, ap, xlsx, terr, out, "migration", None, "DO_NOT_COPY_FIELDS", None, "NO_STATS", "DONT_MATCH")
    rep["steps"].append({"tool": "udms.CreateMigrationWorkspace", "extended_package": r.getOutput(0), "data_loading_workspace": r.getOutput(1),
                         "messages": r.getMessages()[-4000:]})
    print(r.getMessages()[-2500:], flush=True)
    print("  扩展资产包", r.getOutput(0), "\n  装载工作区", r.getOutput(1), flush=True)
    rep["seconds"] = round(time.time() - t0)
    json.dump(rep, open(os.path.join(out, "s3_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main(sys.argv[1])
