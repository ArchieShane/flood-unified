"""把数据资产装进 Esri 完整的 Stormwater Utility Network Essentials（标准 v0.2 第 11 章、附录 A），用 Esri 的连通规则、属性规则校验。

要在 ArcGIS Pro 自带的 Python 里跑：
  "D:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe" -I tools/dgov/un_build_ess.py \
      <资产 gpkg> <工作目录> <服务范围 shp> <Essentials 资产包 gdb> <UDMS .atbx> [网络名]

部署引擎：首选 Esri 官方 conda 包 untools（Utility Network Package Tools 3.7.x，conda.anaconda.org/esri，装在 Pro 的克隆环境里）；
没有时退而用 UDMS 工具箱（.atbx）里内置的同一套引擎 udms_pt。ArcGIS Online 上那份 .atbx 版资产包工具只有 3.3 版，在 Pro 3.7 上不能用。
步骤：
  1 资产包换到 UTM 40N（缓存，只做一次）→ 部署成移动地理数据库（Essentials 全部字段、值域、158 条连通规则、属性规则、端子配置、层级）
  2 加本标准的扩展字段（来源编号、两端节点、糙率、置信度等）
  3 按资产组逐组写入：管段、节点、泵；资产组和资产类型用 Essentials 的编码；和泵重合的节点去掉（泵本身是节点）
  4 管接到哪个端子：进出水口的管接 Inlet Port；泵的吸水管接 Low Pressure In，出水管接 High Pressure Out
  5 启用拓扑、校验；出水口设为子网控制器（同一连通系统共用子网名），更新子网
  6 跑 Esri 的属性规则（校验类）
  7 报告 un_report.json：连通错误、属性规则错误、子网、追不到出水口的管
工作目录要用纯英文路径。
"""
import collections
import json
import os
import sqlite3
import sys
import time

import arcpy

arcpy.env.overwriteOutput = True
SR = arcpy.SpatialReference(32640)
TIER = "Stormwater Management Area"
# 我们的资产组 / 类型 → Essentials 的资产组 / 类型（名字）
LINE_MAP = {"Gravity Pipe": ("Gravity Pipe", "Stormwater Gravity Pipe"),
            "Force Pipe": ("Force Pipe", "Stormwater Force Pipe"),
            "Perforated Pipe": ("Perforated Pipe", "Stormwater Perforated Pipe")}
NODE_MAP = {"BMP Inlet": ("Inlet", "Inlet"), "Catch Basin": ("Catch Basin", "Catch Basin"), "Connection": ("Connection", "Pipe Connection"),
            "Outlet": ("Outfall", "Non-Permit Outfall"), "Sewer Storm Vault": ("Manhole", "Manhole"),
            "BMP Point": ("BMP Point", "BMP Point")}                     # L2 格室的蓄水节点
# 管材：资产库写法 → Essentials 值域（Stormwater_Pipe_Material）。GRP 在 Esri 值域里没有，按最接近的 FRP（玻璃纤维增强）；原文留在 material_src
MATERIAL = {"GRP": 12, "UPVC": 20, "PVC": 20, "REINFORCED CONCRETE": 22, "RC": 22, "VITRIFIED CLAY": 26, "DUCTILE IRON": 11, "HDPE": 15, "OTHER": 16}
EXT_LINE = [("dgov_id", "TEXT", 160), ("material_src", "TEXT", 50), ("from_node", "TEXT", 160), ("to_node", "TEXT", 160), ("manning_n", "DOUBLE", None),
            ("link_kind", "TEXT", 20), ("src", "TEXT", 40), ("conf", "TEXT", 600), ("issues", "TEXT", 400)]
EXT_DEV = [("dgov_id", "TEXT", 160), ("node_kind", "TEXT", 30), ("rim_elev", "DOUBLE", None), ("outfall_kind", "TEXT", 30),
           ("src", "TEXT", 40), ("conf", "TEXT", 600), ("issues", "TEXT", 400)]


def codes(fc):
    """资产组名 → (组编码, {类型名: 类型编码})"""
    out = {}
    for code, s in arcpy.da.ListSubtypes(fc).items():
        dom = [d for fn, (dv, d) in s["FieldValues"].items() if fn.upper() == "ASSETTYPE"]
        out[s["Name"]] = (code, {v: k for k, v in (dom[0].codedValues.items() if dom and dom[0] else [])})
    return out


def zm_line(geom):
    arr = arcpy.Array()
    for part in geom:
        for p in part:
            if p:
                arr.add(arcpy.Point(p.X, p.Y, 0, 0))
    return arcpy.Polyline(arr, SR, True, True)


def zm_point(geom):
    p = geom.firstPoint
    return arcpy.PointGeometry(arcpy.Point(p.X, p.Y, 0, 0), SR, True, True)


def deploy(work, terr_shp, asset_package, atbx, name):
    try:                                   # 首选 Esri 官方 conda 包 untools（Utility Network Package Tools 3.7.x，装在 ArcGIS Pro 克隆环境里）
        from untools.gptools.tools import AssetPackageToFileGeodatabase, ChangeGDBSpatialReference
        engine = "untools " + __import__("untools._version", fromlist=["x"]).__version__
    except ImportError:                    # 退而用 UDMS 工具箱里内置的同一套引擎 udms_pt
        sys.path.insert(0, atbx)
        from udms_pt.gptools.tools import AssetPackageToFileGeodatabase, ChangeGDBSpatialReference
        engine = "udms_pt " + __import__("udms_pt._version", fromlist=["x"]).__version__
    print("    部署引擎：", engine)
    root = os.path.dirname(work.rstrip("/\\"))
    ap = os.path.join(root, "Essentials_32640.gdb")
    if not arcpy.Exists(ap):
        ChangeGDBSpatialReference().run(gdb=asset_package, spatial_reference=SR, output_folder=root, output_name="Essentials_32640")
    for ext in (".geodatabase", "0.geodatabase"):                     # 引擎遇到同名文件会自动改名（加 0），先清掉旧的
        old = os.path.join(work, name + ext)
        if os.path.exists(old):
            os.remove(old)
    tg = os.path.join(work, "terr.gdb")
    if arcpy.Exists(tg):
        arcpy.management.Delete(tg)
    arcpy.management.CreateFileGDB(work, "terr.gdb")
    terr = os.path.join(tg, "territory")
    arcpy.management.CreateFeatureclass(tg, "territory", "POLYGON", None, "ENABLED", "ENABLED", SR)
    arcpy.management.Append(terr_shp, terr, "NO_TEST")
    out = AssetPackageToFileGeodatabase().run(asset_package=ap, service_territory=terr, output_folder=work, gdb_name=name,
                                              feature_dataset_name="UtilityNetwork", utility_network_name="Stormwater",
                                              load_data=False, post_process=True, workspace_type="MGDB")
    return out, engine


def main(gpkg, work, terr_shp, asset_package, atbx, name="rda_ess"):
    t0 = time.time()
    R = {"gpkg": gpkg, "esri_model": "Stormwater Utility Network Essentials（完整部署，UDMS udms_pt）"}
    os.makedirs(work, exist_ok=True)
    print("1 部署 Essentials")
    un, R["engine"] = deploy(work, terr_shp, asset_package, atbx, name)
    mg = un.split(".geodatabase")[0] + ".geodatabase"
    fd = os.path.dirname(un)
    line, dev = os.path.join(fd, "main.StormwaterLine"), os.path.join(fd, "main.StormwaterDevice")
    if not arcpy.Exists(line):
        line, dev = os.path.join(fd, "StormwaterLine"), os.path.join(fd, "StormwaterDevice")
    print("   ", un, round(time.time() - t0), "s")
    try:
        arcpy.un.DisableNetworkTopology(un)
    except Exception:
        pass
    print("2 本地化：管径值域改为毫米")
    # Essentials 的管径值域是美制英寸公称尺寸（12"、18"…），阿联酋的数据是毫米（300、315、450…）。改成毫米的范围值域，不强行套英寸（标准 v0.2 附录 A）
    arcpy.management.CreateDomain(mg, "Stormwater_Pipe_Diameter_mm", "管径（毫米）", "DOUBLE", "RANGE")
    arcpy.management.SetValueForRangeDomain(mg, "Stormwater_Pipe_Diameter_mm", 0, 5000)          # 0 = 未知（字段缺省值是 0）
    sub = [str(c) for c in arcpy.da.ListSubtypes(line)]
    arcpy.management.AssignDomainToField(line, "diameter", "Stormwater_Pipe_Diameter_mm", sub)
    R["localized"] = {"StormwaterLine.diameter": "Stormwater_Pipe_Diameter（英寸） → Stormwater_Pipe_Diameter_mm（0–5000 mm，0 = 未知）"}
    print("2b 扩展字段")
    for fc, ext in ((line, EXT_LINE), (dev, EXT_DEV)):
        have = {f.name.lower() for f in arcpy.ListFields(fc)}
        for n, t, ln in ext:
            if n not in have:
                arcpy.management.AddField(fc, n, t, field_length=ln)
    print("3 写入")
    lc, dc = codes(line), codes(dev)
    pumps = list(arcpy.da.SearchCursor(os.path.join(gpkg, "main.pump"), ["asset_id", "SHAPE@"]))
    pump_node, outfalls = {}, set()
    nodes = list(arcpy.da.SearchCursor(os.path.join(gpkg, "main.node"),
                                       ["node_id", "SHAPE@", "un_asset_group", "node_kind", "invert_elev", "rim_elev", "outfall_kind", "src", "conf", "issues"]))
    for nid, g, *_ in nodes:
        for aid, pg in pumps:
            if g.distanceTo(pg) < 0.05:
                pump_node[nid] = aid
    n_in = collections.Counter()
    with arcpy.da.Editor(mg):
        with arcpy.da.InsertCursor(dev, ["SHAPE@", "ASSETGROUP", "ASSETTYPE", "assetid", "elevation", "depth", "dgov_id", "node_kind", "rim_elev",
                                         "outfall_kind", "src", "conf", "issues"]) as cur:
            for nid, g, ag, kind, inv, rim, ok, src, conf, iss in nodes:
                if nid in pump_node:
                    continue
                eg, et = NODE_MAP[ag]
                if eg == "Outfall":
                    outfalls.add(nid)
                depth = (rim - inv) if rim is not None and inv is not None else None
                cur.insertRow([zm_point(g), dc[eg][0], dc[eg][1][et], nid[:60], inv, depth, nid, kind, rim, ok, src, (conf or "")[:600], (iss or "")[:400]])
                n_in[eg] += 1
            for aid, g in pumps:
                cur.insertRow([zm_point(g), dc["Pump"][0], dc["Pump"][1]["Pump"], aid[:60], None, None, aid, "pump", None, None, "AECOM", "", ""])
                n_in["Pump"] += 1
        with arcpy.da.InsertCursor(line, ["SHAPE@", "ASSETGROUP", "ASSETTYPE", "assetid", "diameter", "upelev", "downelev", "material",
                                          "material_src", "dgov_id", "from_node", "to_node", "manning_n", "link_kind", "src", "conf", "issues"]) as cur:
            for row in arcpy.da.SearchCursor(os.path.join(gpkg, "main.link"), ["SHAPE@", "un_asset_type", "asset_id", "diameter_mm", "upelev", "downelev",
                                                                               "material", "from_node", "to_node", "manning_n", "link_kind", "src", "conf", "issues"]):
                g, at, aid, d, ue, de, mat, fn, tn, n, lk, src, conf, iss = row
                eg, et = LINE_MAP[at]
                cur.insertRow([zm_line(g), lc[eg][0], lc[eg][1][et], aid[:60], d, ue, de, MATERIAL.get(str(mat or "").strip().upper(), 0), (mat or "")[:50], aid, fn, tn, n, lk, src, (conf or "")[:600], (iss or "")[:400]])
                n_in[eg] += 1
    R["loaded"] = dict(n_in)
    R["pump_nodes_replaced"] = pump_node
    print("   ", dict(n_in))
    print("4 端子")
    term = {t.terminalName: t.terminalId for tc in arcpy.Describe(un).terminalConfigurations for t in tc.terminals}
    nset = collections.Counter()
    with arcpy.da.Editor(mg):
        with arcpy.da.UpdateCursor(line, ["from_node", "to_node", "FROMDEVICETERMINAL", "TODEVICETERMINAL"]) as cur:
            for fn, tn, ft, tt in cur:
                if tn in outfalls:
                    tt = term["Inlet Port"]; nset["to_outfall_inlet_port"] += 1
                if tn in pump_node:
                    tt = term["Low Pressure In"]; nset["to_pump_low_in"] += 1
                if fn in pump_node:
                    ft = term["High Pressure Out"]; nset["from_pump_high_out"] += 1
                cur.updateRow([fn, tn, ft, tt])
    R["terminals_set"] = dict(nset)
    print("5 拓扑、子网")
    arcpy.un.EnableNetworkTopology(un, 1000000)
    arcpy.un.ValidateNetworkTopology(un)
    par = {}

    def root(x):
        while par.setdefault(x, x) != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for fn, tn in arcpy.da.SearchCursor(line, ["from_node", "to_node"]):
        par[root(fn)] = root(tn)
    sysname = {}
    ctrl = os.path.join(work, "controllers_in.csv")
    with open(ctrl, "w", encoding="utf-8", newline="") as f:
        import csv
        w = csv.writer(f)
        w.writerow(["SUBNETWORKCONTROLLERNAME", "FEATUREGLOBALID", "FEATURECLASSNAME", "FEATUREASSETGROUP", "FEATUREASSETTYPE",
                    "FEATURETERMINAL", "TIERNAME", "SUBNETWORKNAME", "DESCRIPTION", "NOTES"])
        for gid, nid in arcpy.da.SearchCursor(dev, ["GLOBALID", "dgov_id"], f"ASSETGROUP = {dc['Outfall'][0]}"):
            s = sysname.setdefault(root(nid), f"{name.split('_')[0].upper()}-{len(sysname) + 1}")
            w.writerow([f"OUT_{nid}"[:64], gid, "StormwaterDevice", "Outfall", "Non-Permit Outfall", "Inlet Port", TIER, s, "", ""])
    arcpy.un.ImportSubnetworkControllers(un, ctrl)
    arcpy.un.ValidateNetworkTopology(un)
    arcpy.un.UpdateSubnetwork(un, "Stormwater", TIER, all_subnetworks_in_tier="ALL_SUBNETWORKS_IN_TIER", continue_on_failure="CONTINUE_ON_FAILURE")
    print("6 属性规则")
    try:
        arcpy.management.EvaluateRules(mg, ["VALIDATION_RULES"])
        R["attribute_rules_evaluated"] = True
    except Exception as e:
        R["attribute_rules_evaluated"] = f"失败：{str(e)[-300:]}"
    print("7 报告")
    con = sqlite3.connect(mg)
    tabs = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    dt = [t for t in tabs if t.startswith("UN_") and t.endswith("_DirtyAreas")][0]
    errs = con.execute(f"select ERRORCODE, ERRORMESSAGE from {dt} where ERRORCODE <> 0").fetchall()
    R["rule_errors"] = len(errs)
    R["rule_error_samples"] = [m for _, m in errs[:30]]
    R["dirty_areas"] = con.execute(f"select count(*) from {dt}").fetchone()[0]
    lt = "StormwaterLine" if "StormwaterLine" in tabs else [t for t in tabs if t.endswith("StormwaterLine")][0]
    R["unreached_lines"] = [r[0] for r in con.execute(f"select dgov_id from {lt} where ISCONNECTED <> 1")]
    R["subnetworks"] = dict(collections.Counter(r[0] for r in con.execute(f"select smasubnetworkname from {lt}")))   # Essentials 这一层级的子网名字段
    ar = collections.Counter()
    for t in tabs:
        if t.startswith("GDB_Validation") and t.endswith("Errors"):
            cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
            for rn, msg in con.execute(f'select RuleName, ErrorMessage from "{t}"'):
                ar[f"{rn}：{msg}"] += 1
    R["attribute_rule_errors"] = dict(ar)
    con.close()
    R["un"] = un
    R["seconds"] = round(time.time() - t0)
    json.dump(R, open(os.path.join(work, "un_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: R[k] for k in ("loaded", "terminals_set", "rule_errors", "attribute_rules_evaluated", "attribute_rule_errors", "subnetworks", "seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    main(*sys.argv[1:7])
