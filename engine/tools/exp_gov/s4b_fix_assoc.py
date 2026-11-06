"""第 5 步补：UDMS 3.7.2 按映射生成的包含关系（C_Associations 前 33,414 行）只写了两端 GlobalID，
关联类型、域网络、类、资产组、资产类型都是空的，untools 部署前检查判为错误（每行 7 个字段）。
另补服务范围面的空 globalid。按 untools 自己写的关联行的格式，用 GlobalID 到已装要素里反查补齐；关系本身（谁装在谁里）不改。

  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/s4b_fix_assoc.py <工作目录>
"""
import json
import os
import sys

import arcpy

# 类 → (域网络号, C_Associations 里的类名)，照 untools 写的行：结构网络 0 用全名，雨水网络 1 用 Junction / Device / Line
CLS = {"StructureJunction": (0, "StructureJunction"), "StructureBoundary": (0, "StructureBoundary"), "StructureLine": (0, "StructureLine"),
       "StormwaterJunction": (1, "Junction"), "StormwaterDevice": (1, "Device"), "StormwaterLine": (1, "Line")}


def main(work):
    ap = os.path.join(work, "03_mapping", "migration", "Expanded_local.gdb")
    look = {}
    for c, (dn, name) in CLS.items():
        fc = os.path.join(ap, "UtilityNetwork", c)
        st = arcpy.da.ListSubtypes(fc)
        ag = {k: v["Name"] for k, v in st.items()}
        at = {}
        for k, v in st.items():
            dom = v["FieldValues"].get("ASSETTYPE", (None, None))[1]
            at[k] = dom.codedValues if dom is not None else {}
        for gid, g, t in arcpy.da.SearchCursor(fc, ["GLOBALID", "ASSETGROUP", "ASSETTYPE"]):
            look[gid.upper()] = (dn, name, ag.get(g), at.get(g, {}).get(t))
    t = os.path.join(ap, "C_Associations")
    f = ["association_type", "from_domain_network", "from_feature_class", "from_asset_group", "from_asset_type", "from_global_id",
         "to_domain_network", "to_feature_class", "to_asset_group", "to_asset_type", "to_global_id"]
    n = miss = 0
    with arcpy.da.UpdateCursor(t, f) as cur:
        for r in cur:
            if (r[0] or "").strip():
                continue
            a, b = look.get((r[5] or "").upper()), look.get((r[10] or "").upper())
            if a is None or b is None:
                miss += 1
                continue
            r[0] = "Containment"
            r[1:5] = list(a)
            r[6:10] = list(b)
            cur.updateRow(r)
            n += 1
    # 同一检查还报：UDMS 装进资产包的服务范围面 globalid 为空（资产包里是普通 Guid 字段，不会自动生成）→ 补一个
    import uuid
    terr = 0
    with arcpy.da.UpdateCursor(os.path.join(ap, "UtilityNetwork", "ServiceTerritory"), ["globalid"]) as cur:
        for (g,) in cur:
            if not g or g.startswith("{00000000"):
                cur.updateRow(["{" + str(uuid.uuid4()).upper() + "}"])
                terr += 1
    # 第五轮：System Outflow ↔ 渗井 / BMP 点 的节点—节点连通关联（Expanded 规则：Catch Basin / Infiltration、BMP Point → System Outflow 的 Inlet Port）
    dev = os.path.join(ap, "UtilityNetwork", "StormwaterDevice")
    st = arcpy.da.ListSubtypes(dev)
    codes = {v["Name"]: k for k, v in st.items()}
    at_name = {k: (v["FieldValues"]["ASSETTYPE"][1].codedValues if v["FieldValues"]["ASSETTYPE"][1] is not None else {}) for k, v in st.items()}
    anchors = {}
    for gid, g, typ, xy in arcpy.da.SearchCursor(dev, ["GLOBALID", "ASSETGROUP", "ASSETTYPE", "SHAPE@XY"],
                                                  f"ASSETGROUP IN ({codes['Catch Basin']}, {codes['BMP Point']})"):
        anchors[(round(xy[0], 3), round(xy[1], 3))] = (gid, st[g]["Name"], at_name[g].get(typ))
    jj = 0
    nf = 0
    rows = []
    for gid, ax, ay in arcpy.da.SearchCursor(dev, ["GLOBALID", "anchor_x", "anchor_y"], f"ASSETGROUP = {codes['System Outflow']}"):
        a_ = anchors.get((round(ax, 3), round(ay, 3))) if ax is not None else None
        if a_ is None:
            nf += 1
            continue
        rows.append(("Junction Junction Connectivity", 1, "Device", a_[1], a_[2], a_[0], "Single Terminal",
                     1, "Device", "System Outflow", "Outflow", gid, "Inlet Port"))
    fl = ["association_type", "from_domain_network", "from_feature_class", "from_asset_group", "from_asset_type", "from_global_id", "from_terminal",
          "to_domain_network", "to_feature_class", "to_asset_group", "to_asset_type", "to_global_id", "to_terminal"]
    with arcpy.da.InsertCursor(os.path.join(ap, "C_Associations"), fl) as cur:
        for r in rows:
            cur.insertRow(r)
            jj += 1
    res = {"filled": n, "globalid_not_found": miss, "territory_guid_filled": terr, "system_outflow_jj": jj, "system_outflow_anchor_not_found": nf}
    json.dump(res, open(os.path.join(work, "04_load", "fix_assoc.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(res)


if __name__ == "__main__":
    main(sys.argv[1])
