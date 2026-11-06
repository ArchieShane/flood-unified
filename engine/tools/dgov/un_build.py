"""把数据资产建成 Esri 雨水 Utility Network（Stormwater Utility Network Essentials 结构；标准 v0.2 第 11 章、附录 A），用 Esri 官方连通规则校验，和 dataqc 对照。

要在 ArcGIS Pro 自带的 Python 里跑（arcpy）：
  "D:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" -I tools/dgov/un_build.py <资产 gpkg> <工作目录> <服务范围 shp> <Esri 资产包 gdb> [网络名]

Esri 资产包（Essentials 的 Data Models/StormwaterEssentials_AssetPackage.gdb）只当「蓝图」读：连通规则取它的 B_Rules 表，
资产组、资产类型、端子配置照它的命名。资产包部署工具（Utility Network Package Tools）只有 Pro 3.3 版、在 3.7 上用不了，所以用 Pro 自带工具建网。

步骤：
  1 资产 gpkg → 暂存文件库，按「一个源类 = 一个资产组」拆分；资产类型用编码值域（迁移工具只认值域）
  2 MigrateToUtilityNetwork：建 UN（UTM 40N，雨水域网络，分区式，汇型控制器）并装数据
  3 端子配置（出水口 Pipe Directional Dual Terminal、泵 Pressure Increasing），导入 Esri 官方规则（只取用得到的资产组和类型）
  4 启用网络拓扑、校验 → 错误表
  5 出水口设为子网控制器，更新子网 → 每根管属于哪个出水口
  6 写报告 un_report.json

工作目录要用纯英文路径（ArcGIS 的部分工具不认中文路径）。
"""
import collections
import csv
import json
import os
import sys
import time

import arcpy

arcpy.env.overwriteOutput = True
SR = arcpy.SpatialReference(32640)

# 资产组 → 源类拆分（迁移工具要求一个源类 = 一个资产组）。资产组、资产类型名必须和 Esri Essentials 一致，规则才对得上
OUTFALL, OUTFALL_TYPE = "Outfall", "Non-Permit Outfall"
PARTS = [
    # 名, 资产层, 筛选, 目标类, 资产组, 是否控制器, 资产类型
    ("gravity_pipe", "link", "un_asset_type = 'Gravity Pipe'", "StormwaterLine", "Gravity Pipe", False, "Stormwater Gravity Pipe"),
    ("force_pipe", "link", "un_asset_type = 'Force Pipe'", "StormwaterLine", "Force Pipe", False, "Stormwater Force Pipe"),
    ("perforated_pipe", "link", "un_asset_type = 'Perforated Pipe'", "StormwaterLine", "Perforated Pipe", False, "Stormwater Perforated Pipe"),
    ("inlet", "node", "un_asset_group = 'BMP Inlet'", "StormwaterDevice", "Inlet", False, "Inlet"),          # DMT INLET（路边雨水口）
    ("catch_basin", "node", "un_asset_group = 'Catch Basin'", "StormwaterDevice", "Catch Basin", False, "Catch Basin"),
    ("connection", "node", "un_asset_group = 'Connection'", "StormwaterDevice", "Connection", False, "Pipe Connection"),
    ("outfall", "node", "un_asset_group = 'Outlet'", "StormwaterDevice", OUTFALL, True, OUTFALL_TYPE),
    ("manhole", "node", "un_asset_group = 'Sewer Storm Vault'", "StormwaterDevice", "Manhole", False, "Manhole"),  # 湿井也放这里（Esri 没有湿井类型）
    ("pump", "pump", None, "StormwaterDevice", "Pump", False, "Pump"),
]
DN = {"0": "Structure", "1": "Stormwater"}


def stage(gpkg, work, territory_shp):
    stg = os.path.join(work, "stage.gdb")
    if arcpy.Exists(stg):
        arcpy.management.Delete(stg)
    arcpy.management.CreateFileGDB(work, "stage.gdb")
    arcpy.env.outputZFlag = "Enabled"
    arcpy.env.outputMFlag = "Enabled"
    arcpy.env.outputCoordinateSystem = SR
    # Esri 里泵本身就是节点（带进、出两个端子）：和泵重合的节点去掉，管直接接泵（F-36）
    pumps = list(arcpy.da.SearchCursor(os.path.join(gpkg, "main.pump"), ["asset_id", "SHAPE@"]))
    pump_node = {}
    for nid, g in arcpy.da.SearchCursor(os.path.join(gpkg, "main.node"), ["node_id", "SHAPE@"]):
        for aid, pg in pumps:
            if g.distanceTo(pg) < 0.05:
                pump_node[nid] = aid
    q = lambda ids: ", ".join("'" + i.replace("'", "''") + "'" for i in ids) or "''"
    mapping, types = [], {}
    for name, lyr, where, tcls, ag, ctrl, at_name in PARTS:
        out = os.path.join(stg, name)
        if lyr == "node":
            where = f"({where}) AND node_id NOT IN ({q(list(pump_node))})"
        arcpy.conversion.ExportFeatures(os.path.join(gpkg, "main." + lyr), out, where)
        if int(arcpy.management.GetCount(out)[0]) == 0:
            print(f"  {name:15} 0（跳过）")
            continue
        ren = lambda v, a=at_name: a
        codes = {at_name: 1}
        dom = f"AT_{name}"
        arcpy.management.CreateDomain(stg, dom, f"{ag} asset types", "SHORT", "CODED")
        for v, c in codes.items():
            arcpy.management.AddCodedValueToDomain(stg, dom, c, v)
        arcpy.management.AddField(out, "ATYPE", "SHORT")
        arcpy.management.AssignDomainToField(out, "ATYPE", dom)
        with arcpy.da.UpdateCursor(out, ["un_asset_type", "ATYPE"]) as cur:
            for at, _ in cur:
                cur.updateRow([at, codes[ren(at or "Unknown")]])
        types[(tcls, ag)] = sorted(codes)
        mapping.append([out, tcls, ag, "ATYPE", ctrl])
        print(f"  {name:15} {arcpy.management.GetCount(out)[0]:>6}  {tcls} / {ag} / {at_name}")
    terr = os.path.join(stg, "territory")
    arcpy.management.CreateFeatureclass(stg, "territory", "POLYGON", None, "ENABLED", "ENABLED", SR)
    arcpy.management.Append(territory_shp, terr, "NO_TEST")
    return mapping, types, terr, pump_node


def find_un(work, name):
    mg = os.path.join(work, name, f"{name}.geodatabase")
    return mg, os.path.join(mg, "main.UtilityNetwork", "main.Stormwater_UN")


def esri_rules(asset_package):
    """资产包 B_Rules 表 → Import Rules 的 CSV 格式（行字典）。"""
    H = ["RULETYPE", "FROMFEATURECLASS", "FROMASSETGROUP", "FROMASSETTYPE", "FROMTERMINAL", "TOFEATURECLASS", "TOASSETGROUP", "TOASSETTYPE", "TOTERMINAL",
         "VIAFEATURECLASS", "VIAASSETGROUP", "VIAASSETTYPE", "VIATERMINAL"]
    rows = []
    for r in arcpy.da.SearchCursor(os.path.join(asset_package, "B_Rules"), "*"):
        r = ["" if v in (None, "None") else str(v) for v in r[1:]]
        fc = lambda d, c: (DN.get(d, "") + c) if c else ""
        rows.append(dict(zip(H, [r[0], fc(r[1], r[2]), r[3], r[4], r[5], fc(r[6], r[7]), r[8], r[9], r[10], fc(r[11], r[12]), r[13], r[14], r[15]])))
    return rows


def filtered_rules(asset_package, types, out_csv):
    used = {(c, g, t) for (c, g), ts in types.items() for t in ts}
    rows = esri_rules(asset_package)
    keep = []
    for r in rows:
        ends = [(r[p + "FEATURECLASS"], r[p + "ASSETGROUP"], r[p + "ASSETTYPE"]) for p in ("FROM", "TO", "VIA") if r[p + "FEATURECLASS"]]
        if r["RULETYPE"] == "Containment":          # 结构网络这一轮没建
            continue
        if all(e in used for e in ends):
            keep.append(r)
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(keep)
    return len(rows), len(keep)


def main(gpkg, work, territory_shp, asset_package, name="rda_un"):
    t0 = time.time()
    R = {"gpkg": gpkg, "esri_model": asset_package}
    print("1 暂存")
    mapping, types, terr, pump_node = stage(gpkg, work, territory_shp)
    R["pump_nodes_replaced"] = pump_node
    R["types"] = {f"{c}/{g}": t for (c, g), t in types.items()}
    print("2 迁移建网")
    arcpy.un.MigrateToUtilityNetwork(work, terr, "Stormwater_UN", "UtilityNetwork", name, mapping,
                                     [["Stormwater", "SINK", "PARTITIONED"]], None,
                                     "LOAD", "MERGE", "DO_NOT_INCLUDE", "DO_NOT_INCLUDE", "Current")
    mg, un = find_un(work, name)
    fd = os.path.join(mg, "main.UtilityNetwork")
    counts = {}
    for fc in ("StormwaterLine", "StormwaterDevice"):
        c = collections.Counter(arcpy.da.SearchCursor(os.path.join(fd, "main." + fc), ["ASSETGROUP", "ASSETTYPE"]))
        counts[fc] = sum(c.values())
    R["loaded"] = counts
    print("  ", counts)
    # 迁移工具会按现有数据自动生成「全部允许」的连通规则，那样任何违规都查不出来：全部删掉，只留 Esri 官方规则
    auto = os.path.join(work, "rules_auto_from_migrate.csv")
    arcpy.un.ExportRules(un, "All", auto)
    R["auto_rules_deleted"] = sum(1 for _ in open(auto, encoding="utf-8-sig")) - 1
    arcpy.un.DeleteRule(un, "All")
    print("3 端子配置（照 Essentials：出水口 Pipe Directional Dual Terminal、控制器在 Inlet Port；泵 Pressure Increasing），导入 Esri 官方规则")
    arcpy.un.AddTerminalConfiguration(un, "Pipe Directional Dual Terminal", "DIRECTIONAL",
                                      [["Inlet Port", True], ["Outlet Port", False]], None, None, "All")
    arcpy.un.SetTerminalConfiguration(un, "Stormwater", "StormwaterDevice", OUTFALL, OUTFALL_TYPE, "Pipe Directional Dual Terminal")
    arcpy.un.AddTerminalConfiguration(un, "Pressure Increasing", "DIRECTIONAL",
                                      [["Low Pressure In", True], ["High Pressure Out", False]], None, None, "All")
    arcpy.un.SetTerminalConfiguration(un, "Stormwater", "StormwaterDevice", "Pump", "Pump", "Pressure Increasing")
    term = {t.terminalName: t.terminalId for tc in arcpy.Describe(un).terminalConfigurations for t in tc.terminals}
    # 管接到哪个端子：进出水口的管接 Inlet Port；进泵的管接 Low Pressure In，出泵的压力管接 High Pressure Out
    outlet_ids = {r[0] for r in arcpy.da.SearchCursor(os.path.join(work, "stage.gdb", "outfall"), ["node_id"])}
    nset = collections.Counter()
    line = os.path.join(fd, "main.StormwaterLine")
    with arcpy.da.Editor(mg):
        with arcpy.da.UpdateCursor(line, ["from_node", "to_node", "FROMDEVICETERMINAL", "TODEVICETERMINAL"]) as cur:
            for fn, tn, ft, tt in cur:
                if tn in outlet_ids:
                    tt = term["Inlet Port"]; nset["to_outlet_inlet_port"] += 1
                if tn in pump_node:
                    tt = term["Low Pressure In"]; nset["to_pump_low_in"] += 1
                if fn in pump_node:
                    ft = term["High Pressure Out"]; nset["from_pump_high_out"] += 1
                cur.updateRow([fn, tn, ft, tt])
    R["terminals_set"] = dict(nset)
    rc = os.path.join(work, "rules_used.csv")
    n_all, n_keep = filtered_rules(asset_package, types, rc)
    arcpy.un.ImportRules(un, "All", rc)
    R["rules"] = {"esri_total": n_all, "imported": n_keep}
    print(f"   Esri 规则 {n_all} 条，用得上的 {n_keep} 条")
    print("4 启用拓扑、校验")
    arcpy.un.EnableNetworkTopology(un, 1000000)
    arcpy.un.ValidateNetworkTopology(un)
    d = arcpy.Describe(un)
    print("5 出水口设为子网控制器、更新子网")
    dn = [x for x in d.domainNetworks if x.domainNetworkName == "Stormwater"][0]
    tier = dn.tiers[0].name                          # 迁移工具建的层级叫 Stormwater System
    dev = os.path.join(fd, "main.StormwaterDevice")
    ag_code = {v["Name"]: k for k, v in arcpy.da.ListSubtypes(dev).items()}           # 资产组编码按名字查，不写死
    outlets = [r for r in arcpy.da.SearchCursor(dev, ["GLOBALID", "ASSETGROUP", "ASSETTYPE", "node_id"]) if r[1] == ag_code[OUTFALL]]
    # 子网名按「连通的管网系统」取：同一系统有多个出水口时共用一个子网名（Esri 示例同此；分别命名会报错 29）
    par = {}
    def root(x):
        while par.setdefault(x, x) != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for fn, tn in arcpy.da.SearchCursor(line, ["from_node", "to_node"]):
        par[root(fn)] = root(tn)
    sysname = {}
    for _, _, _, nid in outlets:
        sysname.setdefault(root(nid), f"{name.split('_')[0].upper()}-{len(sysname) + 1}")
    ctrl_csv = os.path.join(work, "controllers_in.csv")
    ag_name = {ag_code[OUTFALL]: OUTFALL}
    at_dom = {1: OUTFALL_TYPE}
    with open(ctrl_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SUBNETWORKCONTROLLERNAME", "FEATUREGLOBALID", "FEATURECLASSNAME", "FEATUREASSETGROUP", "FEATUREASSETTYPE",
                    "FEATURETERMINAL", "TIERNAME", "SUBNETWORKNAME", "DESCRIPTION", "NOTES"])
        for gid, ag, at, nid in outlets:
            w.writerow([f"OUT_{nid}", gid, "StormwaterDevice", ag_name[ag], at_dom[at], "Inlet Port", tier, sysname[root(nid)], "", ""])
    arcpy.un.ImportSubnetworkControllers(un, ctrl_csv)
    arcpy.un.ValidateNetworkTopology(un)               # 设控制器会产生脏区，不先校验子网更新不了
    arcpy.un.UpdateSubnetwork(un, "Stormwater", tier, all_subnetworks_in_tier="ALL_SUBNETWORKS_IN_TIER",
                              continue_on_failure="CONTINUE_ON_FAILURE")
    print("6 报告")
    sub = collections.Counter(r[0] for r in arcpy.da.SearchCursor(line, ["SUBNETWORKNAME"]))
    isc = collections.Counter(r[0] for r in arcpy.da.SearchCursor(line, ["ISCONNECTED"]))
    R["subnetworks"] = dict(sub)
    R["lines_isconnected"] = {str(k): v for k, v in isc.items()}
    R["outlets"] = {o[3]: sysname[root(o[3])] for o in outlets}
    # 网络错误（连通规则不允许、端子不对等）记在 UN 的脏区系统表里（ERRORCODE ≠ 0）；移动地理数据库就是 SQLite，直接读
    import sqlite3
    con = sqlite3.connect(mg)
    t = [r[0] for r in con.execute("select name from sqlite_master where type = 'table' and name like 'UN_%_DirtyAreas'")][0]
    errs = con.execute(f"select ERRORCODE, ERRORMESSAGE from {t} where ERRORCODE <> 0").fetchall()
    R["rule_errors"] = len(errs)
    R["rule_error_samples"] = [m for _, m in errs[:30]]
    R["dirty_areas"] = con.execute(f"select count(*) from {t}").fetchone()[0]
    R["unreached_lines"] = [r[0] for r in con.execute("select asset_id from StormwaterLine where ISCONNECTED <> 1")]
    con.close()
    R["seconds"] = round(time.time() - t0)
    json.dump(R, open(os.path.join(work, "un_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(R, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main(*sys.argv[1:6])
