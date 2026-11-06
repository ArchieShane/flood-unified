"""第 1 步：Expanded 本地化（按 docs/Expanded_本地化与字段映射_待确认_20261023.md，用户 2026-10-23 全部同意）。

  "C:/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe" -I tools/exp_gov/s1b_localize.py <工作目录>

输入 <工作目录>/Expanded_32640.gdb（untools 换坐标系后的资产包，不改它）；
输出 <工作目录>/Expanded_local.gdb + <工作目录>/01_localize/changes.json（每项改动一条，写明依据）。
  L-1 管径：英寸编码值域 → 毫米区间值域 Stormwater_Diameter_mm（0–5000）
  L-2 管材：各管材值域加 100 = Glass Reinforced Plastic - GRP
  第三节 1(a)：补连通规则 Manhole Channel ↔ Perforated Pipe
  第五节（补问）：Catch Basin 材质值域加 101 = Concrete（拍门节点—节点规则已按第七节撤掉）
其余字段（溯源、曼宁系数等）由 UDMS「按映射复制字段和值域」加，不在这里加。
"""
import json
import os
import sys

import arcpy

arcpy.env.overwriteOutput = True
INCH = ("Stormwater_Pipe_Diameter", "Stormwater_Outfall_Diameter", "Stormwater_Outlet_Diameter", "Stormwater_Cleanout_Diameter")
MM = "Stormwater_Diameter_mm"
GRP = (100, "Glass Reinforced Plastic - GRP")
CONCRETE = ("Structure_Sewer_Storm_Materials", 101, "Concrete")       # 第五节：INLET MATERIAL = CONCRETE 4,779 个
MATERIAL = ("Stormwater_Pipe_Material", "Stormwater_Outfall_Material", "Stormwater_Outlet_Material", "Stormwater_Cleanout_Material")
CLASSES = ("StructureJunction", "StructureBoundary", "StormwaterLine", "StormwaterJunction", "StormwaterDevice")
NEW_RULES = [
    # 用户确认第三节 1(a)：DMT 有 3,523 个穿孔管端点落在检查井上，Expanded 原规则不允许
    dict(rule_type="Junction Edge Connectivity", from_domain_network=1, from_feature_class="Device", from_asset_group="Manhole Channel",
         from_asset_type="Manhole Channel", to_domain_network=1, to_feature_class="Line", to_asset_group="Perforated Pipe",
         to_asset_type="Stormwater Perforated Pipe"),
    # 第十一轮（用户 2026-10-25 定）：泵后的压力管把水打进渗井（YIS_YS7，挡住 2,668 根管）；泵站直接接穿孔管（ALS，挡住 246 根管）
    dict(rule_type="Junction Edge Connectivity", from_domain_network=1, from_feature_class="Device", from_asset_group="Catch Basin",
         from_asset_type="Infiltration", to_domain_network=1, to_feature_class="Line", to_asset_group="Force Pipe",
         to_asset_type="Stormwater Force Pipe", _id="十一-压力管接渗井"),
    # 泵有端子：照「泵—重力管」只接进水端（Low Pressure In）的写法；穿孔管和重力管一样是流向泵站的收集管（第十一轮首跑报「端子名必填」后补）
    dict(rule_type="Junction Edge Connectivity", from_domain_network=1, from_feature_class="Device", from_asset_group="Pump",
         from_asset_type="Pump", from_terminal="Low Pressure In", to_domain_network=1, to_feature_class="Line", to_asset_group="Perforated Pipe",
         to_asset_type="Stormwater Perforated Pipe", _id="十一-泵接穿孔管"),
    # 第五节曾补「Outlet / Flap Gate ↔ Outfall」节点—节点规则（拍门与出水口同点）；首轮校验证明同点仍报点位重叠（错误 25），
    # 用户 2026-10-23 改为拍门放到接入管上 0.5 m（第七节），这条规则不再需要，撤掉
]


def main(work):
    src = os.path.join(work, "Expanded_32640.gdb")
    ap = os.path.join(work, "Expanded_local.gdb")
    out = os.path.join(work, "01_localize")
    os.makedirs(out, exist_ok=True)
    if arcpy.Exists(ap):
        arcpy.management.Delete(ap)
    arcpy.management.Copy(src, ap)
    log = []

    arcpy.management.CreateDomain(ap, MM, "Diameter (mm)", "DOUBLE", "RANGE")
    arcpy.management.SetValueForRangeDomain(ap, MM, 0, 5000)
    log.append({"id": "L-1", "action": "CreateDomain", "domain": MM, "range": [0, 5000], "why": "DMT 管径 63–3000 mm，原值域是英寸编码"})
    for c in CLASSES:
        fc = os.path.join(ap, "UtilityNetwork", c)
        for code, s in arcpy.da.ListSubtypes(fc).items():
            for f, (dflt, dom) in s["FieldValues"].items():
                if dom is not None and dom.name in INCH:
                    arcpy.management.AssignDomainToField(fc, f, MM, f"{code}: {s['Name']}")
                    log.append({"id": "L-1", "action": "AssignDomainToField", "class": c, "subtype": s["Name"], "field": f, "old": dom.name, "new": MM})
    for d in MATERIAL:
        arcpy.management.AddCodedValueToDomain(ap, d, GRP[0], GRP[1])
        log.append({"id": "L-2", "action": "AddCodedValueToDomain", "domain": d, "code": GRP[0], "name": GRP[1], "why": "GRP 占 DMT 管道 54%，原值域无 GRP；用户定新增编码"})

    arcpy.management.AddCodedValueToDomain(ap, CONCRETE[0], CONCRETE[1], CONCRETE[2])
    log.append({"id": "五-雨水口材质", "action": "AddCodedValueToDomain", "domain": CONCRETE[0], "code": CONCRETE[1], "name": CONCRETE[2],
                "why": "INLET MATERIAL = CONCRETE 4,779 个，Catch Basin 材质值域无混凝土；用户定新增编码"})

    rules = os.path.join(ap, "B_Rules")
    flds = [f.name for f in arcpy.ListFields(rules) if f.type not in ("OID",)]
    have = {tuple(r) for r in arcpy.da.SearchCursor(rules, flds)}
    with arcpy.da.InsertCursor(rules, flds) as cur:
        for r in NEW_RULES:
            row = tuple(r.get(f) for f in flds)
            if row in have:
                continue
            cur.insertRow(row)
            log.append({"id": r.get("_id") or ("三-1(a)" if r["to_asset_group"] == "Perforated Pipe" else "五-拍门"), "action": "B_Rules insert", "rule": r,
                        "why": "用户确认补连通规则"})
    log.append({"rules_after": int(arcpy.management.GetCount(rules)[0])})
    # 第七轮（用户 2026-10-24 定，对照 Esri 文档 P2）：几何 Z 保持 0、高程在属性里，停用 3 条「从 Z 算高程」的计算规则，
    # 防止以后在网络里编辑时用 Z = 0 改写管底、井盖、井底（官方属性规则说明：规则「需按实施调整或启用」）
    off = ("Device - Set Attributes from Z", "Line - Set Attributes from Z", "StructureJunction - Manhole Elevation Attributes")
    with arcpy.da.UpdateCursor(os.path.join(ap, "B_AttributeRules"), ["in_table", "name", "is_enabled"], "type = 'CALCULATION'") as cur:
        for t_, n_, e_ in cur:
            if n_ in off and e_ != 0:
                cur.updateRow([t_, n_, 0])
                log.append({"id": "七-P2", "action": "B_AttributeRules disable", "table": t_, "rule": n_, "why": "Z = 0，高程在属性；防止计算规则改写高程"})
    json.dump(log, open(os.path.join(out, "changes.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for x in log:
        print(x, flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
