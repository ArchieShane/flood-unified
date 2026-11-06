"""第 9 步：导出。

  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/s6_export.py <工作目录>

1 UDMS ExportReportingData：治理后 Utility Network 的 5 个网络类（带几何、值域编码转成描述文字）+ 关联 + 子网控制器
  → 07_export/Stormwater_AD_reporting.gdb（全市模型就绪 / 交付用的扁平数据，不依赖 Utility Network 也能读）
2 untools UtilityNetworkToAssetPackage（含数据）：治理后网络导出成资产包留档 → 07_export/Stormwater_AD_archive_AP.gdb
3 排除清单、问题登记、冲突表、移动泵、AECOM 汇水区：从预处理库原样复制到 07_export/governance_records.gdb
结果写 07_export/s6_report.json。
"""
import json
import os
import sys
import time

import arcpy

arcpy.env.overwriteOutput = True
UDMS = r"D:/阿布扎比/客户数据/arcgis/UtilityDataManagementSupport.atbx"
CLASSES = ("StormwaterLine", "StormwaterDevice", "StormwaterJunction", "StructureJunction", "StructureBoundary")


def main(work):
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.time()
    mg = os.path.join(work, "05_un", "Stormwater_AD.geodatabase")
    fd = os.path.join(mg, "main.UtilityNetwork")
    un = os.path.join(fd, "main.Stormwater")
    out = os.path.join(work, "07_export")
    os.makedirs(out, exist_ok=True)
    R = {}

    print("1 UDMS ExportReportingData", flush=True)
    arcpy.ImportToolbox(UDMS)
    rg = os.path.join(out, "Stormwater_AD_reporting.gdb")
    if arcpy.Exists(rg):
        arcpy.management.Delete(rg)
    arcpy.management.CreateFileGDB(out, "Stormwater_AD_reporting.gdb")
    vt = [[os.path.join(fd, "main." + c), c, True] for c in CLASSES]
    try:
        r = arcpy.udms.ExportReportingData(rg, vt, ["LOAD_RECORDS", "UN_ASSOCIATIONS", "UN_CONTROLLERS"], un)
        arcpy.env.workspace = rg
        R["reporting"] = {"gdb": rg, "tables": {t: int(arcpy.management.GetCount(t)[0]) for t in (arcpy.ListFeatureClasses() or []) + (arcpy.ListTables() or [])},
                          "messages": r.getMessages()[-1500:]}
    except Exception as e:
        R["reporting"] = {"error": str(e)[-2000:]}
    print(json.dumps(R["reporting"], ensure_ascii=False)[:1500], flush=True)

    print("2 untools UtilityNetworkToAssetPackage", flush=True)
    import pandas as pd
    pd.set_option("future.infer_string", False)
    from untools.gptools.tools import UtilityNetworkToAssetPackage
    old = os.path.join(out, "Stormwater_AD_archive_AP.gdb")
    if arcpy.Exists(old):                                   # untools 遇到同名会自动改名加 0，先删旧的
        arcpy.management.Delete(old)
    try:
        ap = UtilityNetworkToAssetPackage().run(utility_network=un, folder=out, name="Stormwater_AD_archive_AP", include_data=True, export_mode="WORKSPACE")
        R["archive_ap"] = {"gdb": str(ap)}
    except BaseException as e:
        R["archive_ap"] = {"error": repr(e)[-2000:]}
    print(R["archive_ap"], flush=True)

    print("3 治理记录", flush=True)
    rec = os.path.join(out, "governance_records.gdb")
    if arcpy.Exists(rec):
        arcpy.management.Delete(rec)
    arcpy.management.CreateFileGDB(out, "governance_records.gdb")
    prep = os.path.join(work, "02_prep", "prep.gdb")
    n = {}
    for t in ("EXCLUDED", "ISSUES", "CONFLICTS"):
        arcpy.management.Copy(os.path.join(prep, t), os.path.join(rec, t))
        n[t] = int(arcpy.management.GetCount(os.path.join(rec, t))[0])
    for f in ("MOVABLE_PUMP", "AECOM_CATCHMENT"):
        arcpy.management.Copy(os.path.join(prep, f), os.path.join(rec, f))
        n[f] = int(arcpy.management.GetCount(os.path.join(rec, f))[0])
    R["records"] = {"gdb": rec, "counts": n}
    print(R["records"], flush=True)
    R["seconds"] = round(time.time() - t0)
    json.dump(R, open(os.path.join(out, "s6_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main(sys.argv[1])
