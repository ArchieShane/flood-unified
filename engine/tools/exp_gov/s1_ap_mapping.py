"""第 1、2 步的准备：Expanded 资产包换到 UTM 40N（untools）；用 UDMS 生成「源数据 → Expanded」的初始字段映射表（待用户确认，不装数据）。

  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/s1_ap_mapping.py <工作目录>

产物：<工作目录>/Expanded_32640.gdb；<工作目录>/01_mapping/mapping_dmt.xlsx、mapping_aecom_rda.xlsx（UDMS 生成的初稿）
"""
import os
import sys
import time

import arcpy

arcpy.env.overwriteOutput = True
AP = r"D:/swbrain_work/esri_un/exp/Data Models/StormwaterExpanded_AssetPackage.gdb"   # 原件：D:/阿布扎比/客户数据/arcgis/Stormwater_Utility_Network_Expanded.zip
UDMS = r"D:/阿布扎比/客户数据/arcgis/UtilityDataManagementSupport.atbx"


def main(work):
    t0 = time.time()
    from untools.gptools.tools import ChangeGDBSpatialReference
    import untools._version as uv
    ap2 = os.path.join(work, "Expanded_32640.gdb")
    if not arcpy.Exists(ap2):
        print("资产包换坐标系（untools", uv.__version__, "ChangeGDBSpatialReference）", flush=True)
        ChangeGDBSpatialReference().run(gdb=AP, spatial_reference=arcpy.SpatialReference(32640), output_folder=work, output_name="Expanded_32640")
    print("  ", ap2, round(time.time() - t0), "s", flush=True)
    arcpy.ImportToolbox(UDMS)
    out = os.path.join(work, "01_mapping")
    os.makedirs(out, exist_ok=True)
    for src, name in (("src_dmt.gdb", "mapping_dmt"), ("src_aecom_rda.gdb", "mapping_aecom_rda")):
        t1 = time.time()
        print(f"UDMS CreateSimpleDataMapping：{src}", flush=True)
        r = arcpy.udms.CreateSimpleDataMapping(os.path.join(work, src), ap2, out, name, "COUNT_FEATURES", None, None, "ALL")
        print("  ", r.getOutput(0), round(time.time() - t1), "s", flush=True)
        print(r.getMessages()[-1500:], flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
