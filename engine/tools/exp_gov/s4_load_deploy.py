"""第 4、5 步：装数据（Pro Data Loading Tools）→ 建关联、挂端子（untools）→ 部署成 Utility Network（untools）。

  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/s4_load_deploy.py <工作目录> [起始步]

1 load       arcpy.management.LoadDataUsingWorkspace(DataReference.xlsx) → 03_mapping/migration/Expanded_local.gdb（扩展资产包）
2 assoc      untools Associate（= CreateUtilityNetworkAssociations 的引擎），距离 0.01 m（用户 2026-10-23 选「只认重合」）
3 terminals  untools AssignTerminalConnections，honor_direction=True：按数字化方向挂上下游端子
             （第三节 2 已定「不翻转几何、按数字化方向」，挂不上的留空，由校验报出）
3b fixap     s4b_fix_assoc：UDMS 关联行补字段、服务范围面补 globalid（untools 部署前检查要求）
4 tolerance  UDMS CalculateTolerances（记录 UTM 40N 下的 XY / Z 容差）
5 deploy     untools AssetPackageToFileGeodatabase（装数据、后处理），输出移动地理数据库 05_un/Stormwater_AD.geodatabase
每步结果写 04_load/s4_report.json。
"""
import json
import os
import sys
import time

import arcpy

arcpy.env.overwriteOutput = True
UDMS = r"D:/阿布扎比/客户数据/arcgis/UtilityDataManagementSupport.atbx"
SR = arcpy.SpatialReference(32640)
STEPS = ("load", "assoc", "terminals", "fixap", "tolerance", "deploy")


def counts(gdb):
    out = {}
    for c in ("StructureJunction", "StructureBoundary", "StormwaterLine", "StormwaterJunction", "StormwaterDevice"):
        p = os.path.join(gdb, "UtilityNetwork", c)
        out[c] = int(arcpy.management.GetCount(p)[0])
    out["C_Associations"] = int(arcpy.management.GetCount(os.path.join(gdb, "C_Associations"))[0])
    return out


def main(work, start="load"):
    mig = os.path.join(work, "03_mapping", "migration")
    ap = os.path.join(mig, "Expanded_local.gdb")
    out = os.path.join(work, "04_load")
    os.makedirs(out, exist_ok=True)
    rp = os.path.join(out, "s4_report.json")
    R = json.load(open(rp, encoding="utf-8")) if os.path.exists(rp) and start != "load" else {}
    todo = STEPS[STEPS.index(start):]

    def save():
        json.dump(R, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)

    t0 = time.time()
    if "load" in todo:
        print("1 LoadDataUsingWorkspace", flush=True)
        r = arcpy.management.LoadDataUsingWorkspace(os.path.join(mig, "DataLoadingWorkspace", "DataReference.xlsx"))
        R["load"] = {"messages": r.getMessages()[-6000:], "counts": counts(ap), "s": round(time.time() - t0)}
        print(R["load"]["counts"], flush=True)
        save()
    if "assoc" in todo:
        print("2 untools 建关联（0.01 m）", flush=True)
        from untools.data_manipulation.associations import Associate
        from untools.data_manipulation.base import BaseClass
        # untools 3.7.1 的 bug：associations.find_connected_points 里循环变量 lines 覆盖了临时线图层路径，
        # 收尾 _cleanup 去删一个 set 而报错。这里只让清理跳过非路径的参数，算法不动。
        _orig = BaseClass._cleanup

        def _cleanup(*data):
            _orig(*[d for d in data if isinstance(d, str)])
        BaseClass._cleanup = staticmethod(_cleanup)
        R["untools_patch"] = "BaseClass._cleanup 跳过非路径参数（untools 3.7.1 find_connected_points 变量覆盖 bug）"
        # 克隆环境是 pandas 3.0，字符串列缺省是 Arrow 类型，untools 的常量比较（Constant.__eq__）在 Arrow 列上报 NotImplementedError。
        import pandas as pd
        pd.set_option("future.infer_string", False)
        R["pandas_option"] = "future.infer_string = False（pandas 3.0 与 untools 3.7.1 兼容）"
        before = counts(ap)["C_Associations"]
        a = Associate(path=ap, default_tolerance=0.01)
        a.main()
        R["assoc"] = {"tolerance_m": 0.01, "before": before, "after": counts(ap)["C_Associations"], "s": round(time.time() - t0)}
        print(R["assoc"], flush=True)
        save()
    if "terminals" in todo:
        print("3 untools 挂端子", flush=True)
        from untools.gptools.tools import AssignTerminalConnections
        log = os.path.join(out, "terminals.log")
        AssignTerminalConnections().run(path=ap, domain_network="Stormwater", honor_direction=True, keep_existing=False, log_file=log)
        n = {}
        for f in ("FROMDEVICETERMINAL", "TODEVICETERMINAL"):
            n[f] = sum(1 for (v,) in arcpy.da.SearchCursor(os.path.join(ap, "UtilityNetwork", "StormwaterLine"), [f]) if v)
        R["terminals"] = {"honor_direction": True, "lines_with_terminal": n, "log": log, "s": round(time.time() - t0)}
        print(R["terminals"], flush=True)
        save()
    if "fixap" in todo:
        print("3b 补 UDMS 关联行的空字段、服务范围面的空 globalid（s4b_fix_assoc）", flush=True)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import s4b_fix_assoc
        s4b_fix_assoc.main(work)
        R["fixap"] = json.load(open(os.path.join(out, "fix_assoc.json"), encoding="utf-8"))
        save()
    if "tolerance" in todo:
        print("4 UDMS CalculateTolerances", flush=True)
        arcpy.ImportToolbox(UDMS)
        try:
            r = arcpy.udms.CalculateTolerances(SR)
            R["tolerance"] = {"messages": r.getMessages()[-1500:], "outputs": [str(r.getOutput(i)) for i in range(r.outputCount)]}
        except Exception as e:
            R["tolerance"] = {"error": str(e)[-800:]}
        print(R["tolerance"], flush=True)
        save()
    if "deploy" in todo:
        print("5 untools 部署（装数据）", flush=True)
        from untools.gptools.tools import AssetPackageToFileGeodatabase
        un_dir = os.path.join(work, "05_un")
        os.makedirs(un_dir, exist_ok=True)
        for ext in (".geodatabase", "0.geodatabase"):
            p = os.path.join(un_dir, "Stormwater_AD" + ext)
            if os.path.exists(p):
                os.remove(p)
        terr = os.path.join(work, "03_mapping", "territory.gdb", "ServiceTerritory")
        un = AssetPackageToFileGeodatabase().run(asset_package=ap, service_territory=terr, output_folder=un_dir, gdb_name="Stormwater_AD",
                                                  feature_dataset_name="UtilityNetwork", utility_network_name="Stormwater",
                                                  load_data=True, post_process=True, workspace_type="MGDB")
        R["deploy"] = {"un": str(un), "s": round(time.time() - t0)}
        print(R["deploy"], flush=True)
        save()


if __name__ == "__main__":
    main(*sys.argv[1:3])
