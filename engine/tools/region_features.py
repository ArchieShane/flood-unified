# -*- coding: utf-8 -*-
"""v2 数据包（network_v2.gpkg，见 region_pack_v2.py）→ 一维建模要素：管 P、点 Nd、出水口 keep_of、泵 pumps、湿井 wetwell、封堵端 caps。
返回值形状与 region_model.features()（旧 Utility Network 数据包）完全一致，region_net.build_from_features 不用改。

三态（用户 2026-10-30，按 ASSET_STAGE）：
  L1         Existing（已有）。没有 ASSET_STAGE 标注的不会进来（打包时都有标注）。
  L2         L1 + Temporary（临时：RD_A_TEMP_PUMP_SUCTION / DISCHARGE 的 13 个点）。
  L2planned  L2 + Planned（计划：Planned 的井 / 雨水井 / 出水口 / 泵 / Geocell 地下蓄水单元）。
管线不分状态：三态都用全部管线（含 Planned 的管、STATUS = Abandoned / Expired / Removed 的管；剔除会断连通性），在 facilities 里记个数，进参数报告。

建不了的（数据缺参数，不编）记进 info["unmodeled"]：
  · 临时泵：只有吸水点 / 排水点和 6 英寸 × 台数，没有流量 / 扬程 / 曲线 / 接入节点；
  · Geocell：只有顶 / 底标高和面积，没有进出口连接和流量；
  · Planned 的泵 MIT_HS-…_PMP-21 与 UP__RD-P1-1：MODEL_READY = 否（缺曲线、启停水位、湿井）。
含临时 / 计划态但这些设施建不了时，run_region 默认拒绝跑（该态会和更低一态相同或名不副实），加 --allow-unmodeled 才跑，并在参数报告和 README 里写明。
"""
from __future__ import annotations

import math
import re
from collections import Counter

STATE_STAGES = {"L1": ("Existing",), "L2": ("Existing", "Temporary"), "L2planned": ("Existing", "Temporary", "Planned")}
STATE_DIR = {"L1": "L1_existing_facilities", "L2": "L2_temporary_facilities", "L2planned": "L2planned_planned_facilities"}


def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def parse_curve(txt: str):
    """'50937: PUMP3（扬程 m → 流量 L/s）: 1.5→470; 6.1→350; …' → [(head m, q m3/s), …]"""
    body = str(txt).split("：", 1)[-1] if "）:" not in str(txt) else str(txt).split("）:", 1)[1]
    pts = re.findall(r"(-?[\d.]+)\s*→\s*(-?[\d.]+)", body)
    return [(float(h), float(q) / 1000.0) for h, q in pts]


def features_v2(R, state: str, log=print):
    import geopandas as gpd
    import numpy as np
    import region_model as RM
    import model_rules as MR
    if state not in STATE_STAGES:
        raise SystemExit(f"不认识的态 {state}（L1 / L2 / L2planned）")
    stages = set(STATE_STAGES[state])
    gp = R.p("network_v2.gpkg")
    if not gp.is_file():
        raise SystemExit(f"没有 {gp}（先跑 tools/region_pack_v2.py --region {R.id}）")
    rules = R.rules
    sea = float(MR.get("outfalls.sea_level_m", rules))
    sid = RM.sid
    info: dict = {"state": state, "stages": sorted(stages), "unmodeled": []}
    # ---------------- 管线：不分状态全用
    ln = gpd.read_file(gp, layer="pipes")
    P, nmat = [], Counter()
    for r in ln.itertuples():
        g = r.geometry
        parts = list(g.geoms) if g.geom_type == "MultiLineString" else [g]
        co = [c for part in parts for c in part.coords]
        mat = RM.MATERIAL_NAME.get(str(r.PIPE_MATER or "").strip(), "Unknown")
        n = MR.manning_for(mat, rules)
        nmat[f"{mat} → {n}"] += 1
        d = num(r.PIPE_DIAME)
        P.append({"id": f"G{len(P) + 1}", "xy": [co[0][:2], co[-1][:2]], "vx": [c[:2] for c in co[1:-1]], "len": num(r.PIPE_LENGT) or g.length,
                  "d_m": d / 1000 if d else None, "up": num(r.INVERT_LEV), "dn": num(r.INVERT_L_1), "n": n, "src": r.UNITID,
                  "force": str(r.PIPE_SUBTY or "") == "PressrLine"})
    info["_pipe_by_id"] = {pp["id"]: (str(r_status).strip() or "（空）", pp["src"]) for pp, r_status in zip(P, ln["STATUS"])}
    st_cnt = Counter(str(s).strip() or "（空）" for s in ln["STATUS"])
    info["pipes"] = {"n": len(ln), "by_asset_stage": dict(Counter(ln["ASSET_STAGE"].fillna("（空）"))), "by_STATUS": dict(st_cnt),
                     "n_abandoned_expired_removed": int(sum(st_cnt.get(k, 0) for k in ("Abandoned", "Expired", "Removed"))),
                     "rule": "管线不分状态全用（含 Planned 的管、STATUS = Abandoned / Expired / Removed 的管）：剔除这些会让管网出现连通性问题"}

    def layer(name):
        d = gpd.read_file(gp, layer=name)
        stage = d["ASSET_STAGE"].fillna("")
        return d[stage.isin(stages)], d[~stage.isin(stages)]
    Nd, seen = [], set()

    def add(n):
        base, k = n["id"], 1
        while n["id"] in seen:
            k += 1
            n["id"] = f"{base}_{k}"
        seen.add(n["id"])
        Nd.append(n)
    fac = {}
    # ---------------- 点设施
    mh, mh_off = layer("manholes")
    for r in mh.itertuples():
        add({"id": sid(r.UNITID), "xy": (r.geometry.x, r.geometry.y), "kind": "mh", "rim": num(r.COVER_LEVE), "inv": num(r.INVERT_LEV)})
    inl, inl_off = layer("inlets")
    cb, cb_off = layer("catchbasins")
    for d in (inl, cb):
        for r in d.itertuples():
            add({"id": sid(r.UNITID), "xy": (r.geometry.x, r.geometry.y), "kind": "inlet", "rim": None, "inv": num(r.INVERT_LEV)})
    of, of_off = layer("outfalls")
    keep_of = []
    for r in of.itertuples():
        keep_of.append(sid(r.UNITID))
        add({"id": sid(r.UNITID), "xy": (r.geometry.x, r.geometry.y), "kind": "outfall", "inv": num(r.INVERT_LEV), "bc": f"FIXED {sea:.3f}", "flap": True})
    caps_df, _ = layer("cappedends")
    caps = np.c_[caps_df.geometry.x, caps_df.geometry.y] if len(caps_df) else np.zeros((0, 2))
    fac.update({"manholes": len(mh), "inlets": len(inl), "catchbasins": len(cb), "cappedends": len(caps_df), "outfalls": len(of)})
    info["outfalls"] = [{"id": r.UNITID, "stage": r.ASSET_STAGE, "src": r.src, "data_BND_TYPE": r.BND_TYPE, "data_BND_STAGE": num(r.BND_STAGE)} for r in of.itertuples()]
    info["outfall_rule"] = f"进入本态的出水口一律固定水位 {sea:g} m + 拍门（公共规则 outfalls.sea_level_m，同 F 系列）；数据里的 BND_TYPE 仅记录（AE_RDA_RD-A1 / A2 数据标 FREE）"
    # ---------------- 泵与湿井：数据里的 CAP / ON / OFF / 曲线 / WW_*
    pm, pm_off = layer("pumps")
    pumps, wetwell = [], None
    st_row = None
    for r in pm.itertuples():
        curve = parse_curve(r.PUMP_CURVE) if r.PUMP_CURVE else []
        if curve and num(r.ON_EL_M) is not None and num(r.OFF_EL_M) is not None:
            pumps.append({"id": sid(r.UNITID), "xy": (r.geometry.x, r.geometry.y), "on_elev": num(r.ON_EL_M), "off_elev": num(r.OFF_EL_M), "curve": curve, "ref": r.UNITID})
        elif num(r.WW_INV) is not None and not curve:
            st_row = r                       # 泵站本体（湿井参数）
        else:
            info["unmodeled"].append({"item": r.UNITID, "layer": "pumps", "stage": r.ASSET_STAGE, "reason": f"MODEL_READY = {r.MODEL_READY}；{r.MISSING or '缺额定流量 / 泵曲线、启停水位、湿井'}"})
    for r in pm_off.itertuples():
        info.setdefault("excluded_by_stage", []).append({"item": r.UNITID, "layer": "pumps", "stage": r.ASSET_STAGE})
    if pumps and st_row is None:
        raise SystemExit("泵有曲线但找不到泵站本体（WW_INV）")
    if st_row is not None and pumps:
        wetwell = {"id": sid(st_row.UNITID), "xy": (st_row.geometry.x, st_row.geometry.y), "inv": num(st_row.WW_INV), "maxd": num(st_row.WW_DEPTH), "area": num(st_row.WW_AREA)}
        add({"id": wetwell["id"], "xy": wetwell["xy"], "kind": "wetwell", "inv": None, "rim": None})
        for p in pumps:
            add({"id": p["id"], "xy": p["xy"], "kind": "pump", "inv": None, "rim": None})
    fac["pumps"] = len(pumps)
    # ---------------- 临时 / 计划设施（数据只有位置，参数不全）
    if "Temporary" in stages:
        ts = gpd.read_file(gp, layer="temp_pump_suction"); td = gpd.read_file(gp, layer="temp_pump_discharge")
        info["unmodeled"].append({"item": f"临时泵 吸水点 {len(ts)} + 排水点 {len(td)}", "layer": "temp_pump_*", "stage": "Temporary",
                                  "reason": "只有位置和『6 英寸 × 台数』，没有流量 / 扬程 / 曲线 / 接入节点；规则文件没有临时泵取值"})
        fac["temp_pump_points"] = len(ts) + len(td)
    if "Planned" in stages:
        gc = gpd.read_file(gp, layer="geocell")
        info["unmodeled"].append({"item": f"Geocell 地下蓄水单元 {len(gc)} 个", "layer": "geocell", "stage": "Planned",
                                  "reason": "只有顶 / 底标高和面积（STORAGEMAX 仅 5 个有值，VOL 为 0），没有进出口连接和流量；规则文件没有 Geocell 取值"})
        fac["geocell"] = len(gc)
    excl = {"manholes": mh_off, "inlets": inl_off, "catchbasins": cb_off, "outfalls": of_off}
    info["excluded_by_stage"] = info.get("excluded_by_stage", []) + [{"layer": k, "n": len(v), "by_stage": dict(Counter(v["ASSET_STAGE"].fillna("（空）")))} for k, v in excl.items() if len(v)]
    info["facilities"] = fac
    log(f"  设施（{state} = {sorted(stages)}）：{fac}；管 {len(ln)}（不分状态）；出水口 {keep_of}；未建模 {len(info['unmodeled'])} 项")
    return P, Nd, keep_of, pumps, wetwell, caps, dict(nmat), info
