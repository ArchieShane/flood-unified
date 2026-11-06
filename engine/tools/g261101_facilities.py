# -*- coding: utf-8 -*-
"""261101 这批新区域的「设施」要素（页面设备清单 / 设施图层），替换 region_deliver.facility_features（RD-A 那版只认 RD-A 的图层和字段，不改它）。

每个要素带 `防线层级`（页面按它拆「所属态」）和 `建模状态`（缺参数 / 引擎不建模的明说，不让页面当成已算进去）：
  出水口   本态用到的全部（L1 = Existing，含界外 100 m 内引入的，标明离计算域多远；L2planned 再加 Planned）：已建模，固定 1.3 m + 拍门（数据标 POND / FREE 的备注在说明里）
           界外 100 m 内但接不上本区主管网、没引入的 Existing 出水口：也列出来，标「未引入」
  泵       Existing（L1）/ Planned（L2planned）：有曲线和启停水位的已建模，其余未建模（缺什么见数据 MISSING）
  塘 / 渗井 / 移动泵 / 临时泵 / Geocell   一律未建模：塘缺容积曲线 / 池底高程 / 进出水连接，渗井和移动泵引擎暂不建模，临时泵只有位置，Geocell 缺进出口和流量
井 / 进水口 / 雨水口 / 封堵端：三态共用（一律 L1），不列进设施清单（有几千个，页面另有图层）。
用 install() 装进 region_deliver；run_region_g.py 启动时调用。
"""
from __future__ import annotations

import json

L1, L2, L2P = "L1 现状", "L2 雨前预置", "L2+planned 客户计划"


def _val(r, k):
    v = getattr(r, k, None)
    if v is None:
        return None
    try:
        if v != v:
            return None
    except Exception:  # noqa: BLE001
        pass
    return v


def facility_features(R, state: str, stages: list[str]) -> list[dict]:
    import geopandas as gpd
    import pyogrio
    gp = R.p("network_v2.gpkg")
    have = {l[0] for l in pyogrio.list_layers(gp)}
    feats = []

    def rd(name):
        return gpd.read_file(gp, layer=name) if name in have else None

    def add(gdf, level, kind, ftype, modeled, note=None, idcol="UNITID", extra=None, label=None):
        if gdf is None or not len(gdf):
            return
        g = gdf.to_crs(4326)
        for i, r in enumerate(g.itertuples()):
            nid = _val(r, idcol) or _val(r, "LABEL") or f"{kind}-{i + 1}"
            pr = {"编号": str(nid), "类型": ftype, "kind": kind, "防线层级": level, "建模状态": "已建模" if modeled else "未建模（数据缺参数）" if label is None else label}
            if note:
                pr["说明"] = note
            for k, fn in (extra or {}).items():
                try:
                    v = fn(r)
                except Exception:  # noqa: BLE001
                    v = None
                if v is not None:
                    pr[k] = v
            feats.append({"type": "Feature", "geometry": json.loads(gpd.GeoSeries([r.geometry]).to_json())["features"][0]["geometry"], "properties": pr})

    f1 = lambda x: None if x is None or x != x else round(float(x), 3)  # noqa: E731
    # ---- 出水口
    of = rd("outfalls")
    if of is not None and len(of):
        of_in = of[of["ASSET_STAGE"].isin(stages)]
        for _, level, stg in ((0, L1, "Existing"), (1, L2P, "Planned")):
            sub = of_in[of_in["ASSET_STAGE"] == stg]
            if not len(sub):
                continue
            ins = sub[sub["SCOPE"] == "计算域内"]
            out = sub[sub["SCOPE"] != "计算域内"]
            ext = {"边界类型（数据）": lambda r: _val(r, "BND_TYPE"), "出水口来源": lambda r: _val(r, "SCOPE")}
            add(ins, level, "outfall", "出水口" if stg == "Existing" else "出水口（计划）", True, "进模型：固定水位 1.3 m + 拍门", extra=ext)
            add(out, level, "outfall", "出水口（界外 100 m 内引入）", True, "不在计算域内，距离见「出水口来源」；接在本区主管网上，所以引入；进模型：固定水位 1.3 m + 拍门",
                extra={**ext, "离计算域 m": lambda r: f1(r.DIST_M)})
        rej = rd("outfalls_rejected")
        add(rej, L1, "outfall", "出水口（界外 100 m 内，未引入）", False, "接不上本区主管网，没有引入模型", label="未引入（接不上本区主管网）",
            extra={"离计算域 m": lambda r: f1(r.DIST_M), "边界类型（数据）": lambda r: _val(r, "BND_TYPE")})
    # ---- 泵
    pm = rd("pumps")
    pmod = rd("pumps_modeled")
    if pm is not None and pmod is not None and len(pmod):
        import pandas as pd
        pm = gpd.GeoDataFrame(pd.concat([pm, pmod], ignore_index=True), crs=pm.crs or pmod.crs)
    elif pm is None or not len(pm):
        pm = pmod
    if pm is not None and len(pm):
        pm = pm[pm["ASSET_STAGE"].isin(stages)]
        for stage, level in (("Existing", L1), ("Planned", L2P)):
            sub = pm[pm["ASSET_STAGE"] == stage]
            ready = sub[sub["PUMP_CURVE"].notna() & sub["ON_EL_M"].notna()]
            other = sub[~sub.index.isin(ready.index)]
            q = {"额定流量 L/s": lambda r: f1(r.CAP_Q_LS)}
            add(ready, level, "pump", "泵", True, extra={**q, "开泵水位 m": lambda r: f1(r.ON_EL_M), "停泵水位 m": lambda r: f1(r.OFF_EL_M)})
            add(other, level, "pump", "泵", False, "未建模：缺泵曲线 / 启停水位 / 湿井等参数（见 MISSING）", extra={**q, "缺参数": lambda r: _val(r, "MISSING")})
    # ---- 塘、渗井、移动泵（Existing → L1；Planned → L2planned；Temporary → L2）
    lev = {"Existing": L1, "Temporary": L2, "Planned": L2P}
    for lay, ftype, kind, why, extra in (
        ("ponds", "塘", "tank", "未建模：缺容积曲线（水位-面积）/ 池底高程 / 进出水连接（接哪个检查井 / 排口、重力或泵），数据 MODEL_READY = 否（见缺参数）",
         {"库容 m3": lambda r: f1(r.CAP_VOL_M3), "水深 m": lambda r: f1(r.CAP_DEPTH_M), "缺参数": lambda r: _val(r, "MISSING"), "名称": lambda r: _val(r, "POND_NAME")}),
        ("soakaways", "渗井", "sump", "未建模：引擎暂不建模渗井（数据只有位置和标高）", None),
        ("movable_pumps", "移动泵", "pump", "未建模：只有额定流量 / 扬程，没有泵曲线、启停水位和接入节点", {"流量": lambda r: f1(r.FLOW_RATE), "扬程": lambda r: f1(r.HEAD)}),
    ):
        d = rd(lay)
        if d is None or not len(d):
            continue
        d = d[d["ASSET_STAGE"].isin(stages)]
        for stg in ("Existing", "Temporary", "Planned"):
            add(d[d["ASSET_STAGE"] == stg], lev[stg], kind, ftype, False, why, extra=extra)
    # ---- 临时泵（只有位置）
    if "Temporary" in stages:
        for lay, ftype, kind in (("temp_pump_suction", "临时泵吸水点", "sump"), ("temp_pump_discharge", "临时泵排水点", "pump"), ("temp_pump_site", "临时泵站点", "pump")):
            d = rd(lay)
            add(d, L2, kind, ftype, False, "未建模：只有位置和泵规格，没有流量 / 扬程 / 曲线 / 接入节点", idcol="HOTSPOT_ID",
                extra={"泵规格": lambda r: None if _val(r, "PUMP_DETAIL") is None else str(r.PUMP_DETAIL), "服务热点": lambda r: None if _val(r, "HOTSPOT_ID") is None else str(r.HOTSPOT_ID)})
    # ---- Geocell（计划）
    if "Planned" in stages:
        for lay, ftype in (("geocell", "Geocell 地下蓄水单元（面）"), ("geocell_points", "Geocell 地下蓄水单元（点）")):
            add(rd(lay), L2P, "tank", ftype, False, "未建模：没有进出口连接和流量（只有顶 / 底标高 / 面积 / 库容）", idcol="LABEL",
                extra={"顶标高 m": lambda r: f1(r.TOP_LVL), "底标高 m": lambda r: f1(r.BOT_LVL)})
    return feats


def install():
    import region_deliver
    region_deliver.facility_features = facility_features
