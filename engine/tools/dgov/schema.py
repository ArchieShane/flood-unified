"""标准数据结构（数据标准 v0.1 第 4 章）。每层：字段 → (类型, 含义)。

公共字段（每层都有）：asset_id（来源前缀:原始编号）、src_id、src_dataset、src_layer、src_sha256、std_version、
conf（JSON：字段 → 置信度 A–E；缺值记 "missing"）、un_class / un_asset_group / un_asset_type（对应 Esri 雨水 Utility Network）。
"""
COMMON = {
    "asset_id": ("str", "资产编号：来源前缀:原始编号"),
    "src_id": ("str", "原始编号"),
    "src_dataset": ("str", "来源数据集编号（sources.yaml）"),
    "src_layer": ("str", "来源图层或文件"),
    "src_sha256": ("str", "来源文件哈希"),
    "std_version": ("str", "加工时遵循的标准版本"),
    "conf": ("str", "JSON：字段 → 置信度 A–E / missing"),
    "un_class": ("str", "Esri 雨水 UN 要素类"),
    "un_asset_group": ("str", "Esri 资产组"),
    "un_asset_type": ("str", "Esri 资产类型"),
    "status_src": ("str", "来源里的状态原文"),
    "migration_src": ("str", "来源里的迁移 / 录入状态原文（GDB 的 INFORMIGRATION）"),
}

LAYERS = {
    "node": {
        "node_kind": ("str", "manhole / junction_chamber / catchbasin / inlet / capped_end / interceptor / outfall / wetwell / junction"),
        "rim_elev": ("float", "井盖或地面高程，m NADD"),
        "invert_elev": ("float", "井底高程，m NADD"),
        "install_date": ("str", "安装日期 ISO"),
        "boundary_type": ("str", "出水口：FREE / FIXED / TIDAL"),
        "tailwater": ("float", "出水口固定尾水位，m NADD"),
        "flap": ("str", "出水口是否带拍门：yes / no / 空"),
        "outfall_id_src": ("str", "来源里的所属出水口编号"),
        "comments_src": ("str", "来源备注原文"),
    },
    "link": {
        "link_kind": ("str", "gravity / force / perforated / abandoned / culvert / channel"),
        "from_src": ("str", "上游节点原始编号（来源字段）"),
        "to_src": ("str", "下游节点原始编号（来源字段）"),
        "diameter_mm": ("float", "管径 mm"),
        "upelev": ("float", "上游管底，m NADD（Esri upelev）"),
        "downelev": ("float", "下游管底，m NADD（Esri downelev）"),
        "length_attr": ("float", "属性管长 m"),
        "length_geom": ("float", "几何长度 m"),
        "material": ("str", "材质"),
        "manning_n": ("float", "曼宁糙率"),
        "install_date": ("str", "安装日期 ISO"),
    },
    "pump": {
        "pump_kind": ("str", "station（固定泵站）/ movable（移动泵）/ model（顾问模型里的泵）"),
        "station_id": ("str", "泵站编号"),
        "flow_lps": ("float", "流量 L/s（额定或模型值，见 conf）"),
        "head_m": ("float", "扬程 m"),
        "on_elev": ("float", "开泵水位 m NADD"),
        "off_elev": ("float", "停泵水位 m NADD"),
        "curve_id": ("str", "泵曲线编号（pump_curve 表）"),
        "n_pumps": ("float", "台数（泵站记录）"),
        "wetwell_src": ("str", "吸水湿井原始编号"),
        "comments_src": ("str", "来源备注原文"),
    },
    "storage": {
        "storage_kind": ("str", "pond / soakaway / wetwell"),
        "volume_m3": ("float", "库容 m³"),
        "depth_m": ("float", "深度 m"),
        "floor_elev": ("float", "底高程 m NADD"),
        "rim_elev": ("float", "顶 / 地面高程 m NADD"),
        "area_m2": ("float", "面积 m²（属性或几何）"),
        "length_m": ("float", "长 m（渗井）"),
        "width_m": ("float", "宽 m（渗井）"),
        "comments_src": ("str", "来源备注原文"),
    },
    "catchment": {
        "area_m2": ("float", "面积 m²（属性）"),
        "area_geom_m2": ("float", "几何面积 m²"),
        "cn": ("float", "SCS CN"),
        "tc_min": ("float", "汇流时间 min"),
        "out_node_src": ("str", "出口节点原始编号"),
    },
}

# 无几何的表
TABLES = {
    "pump_curve": {
        "curve_id": ("str", "曲线编号"), "curve_type": ("str", "SWMM PUMP1–4"), "seq": ("int", "点序"),
        "x": ("float", "PUMP3：扬程 m"), "y_m3s": ("float", "流量 m³/s"),
        "src_dataset": ("str", ""), "src_layer": ("str", ""), "src_sha256": ("str", ""), "std_version": ("str", ""),
    },
}


def fields(layer):
    return {**COMMON, **LAYERS[layer]}
