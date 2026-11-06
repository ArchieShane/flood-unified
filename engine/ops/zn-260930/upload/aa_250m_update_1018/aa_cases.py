# -*- coding: utf-8 -*-
"""Al Ain（艾因）全域 250 m 推演的案例数据：现状泵站、已建成塘（2026-10-15）。

数据来源（都在 data/ 下，只在开发机上跑一次，产物放进 cases/alain/，服务器上不再需要完整版 GDB）：
  data/DMTStormWater_AlAin_Processed.gdb                         管网（PIPELINE / OUTFALL，已带 StartCode 等字段；建 1D 用）
  data/261015/AA_WR/DMT_IrrigationStormWater_AlAin.gdb           完整版：PS_PUMP（流量 L/s、扬程 m）、PS_WET_WELL_AREA（湿井深）、POND（面）
现状泵站（规则沿用 c1_existing_pumps.py / rg_regions.py，区别见下）：
  · 一个泵站 = 同一 PUMP_STATION_ID 的全部 PS_PUMP 记录；台数 = 记录数，单台流量 = 各台 FLOW_RATE 的平均（总流量 = 各台之和，全部同时运行）
  · 艾因 PS_PUMP 的 FLOW_RATE（L/s）每台都填了，不用客户备注 / Parsons；固定流量泵（SWMM PUMP2）、启停 = 井深 50% / 10%
  · 湿井深取 PS_WET_WELL_AREA.DEPTH（同 PUMP_STATION_ID）；库里写 0 的 4 座（PS25–PS28）按其余泵站湿井深的中位数补（Claude 自定，标 depth_note）
  · 吸水井 = 离泵最近的、连通块 ≥ 2 个节点的管网节点；排放点 = 客户 GDB 压力管（PIPE_SUBTYPE = PressrLine）从泵站起逐段追到头，没有就「抽出计算域」（新建自由出水口）
已建成塘：POND 图层（艾因只有 1 座，Sayh Al Saad，库容 45,000 m³、深 6.8 m、塘底 194.0 m）
  · 塘按真实深度建蓄水节点：塘底 194.0 m、深 6.8 m、水面面积 = 库容 ÷ 深度（等截面，6,618 m²；台账占地 11,700 m² 是带边坡的口面）
  · 进水：塘占地 150 m 内的管网出水口（即把水排进塘的那根出水管）；L2 里这个出水口不再自由出流，改为接进塘（塘是末端，不设出水口和排空泵）
用法：python tools/aa_cases.py
产物：cases/alain/aa_existing_pumps.json、cases/alain/aa_ponds.json
"""
from __future__ import annotations

import collections
import json
import math
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))

CASES = ROOT / "cases" / "alain"
GDB_PROC = ROOT / "data" / "2026-10-17" / "AAM_SW_Processed.gpkg"   # tools/aa_preprocess.py 从新版客户库生成
GDB_FULL = GDB_PROC


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    import numpy as np
    import pyogrio
    import geopandas as gpd
    from scipy.spatial import cKDTree
    from shapely.geometry import Point, mapping
    import build_city1d as B
    import build_pilot as BP
    import c1_existing_pumps as E
    B.GDB = str(GDB_PROC); B.DATUM = 0.0
    df = B.read_pipes(None, log=lambda *x: None)
    xy, ground, inv, _ = B.build_nodes(df, log=lambda *x: None)
    sc = df.StartCode.astype("string").fillna("").values
    ec = df.EndCode.astype("string").fillna("").values
    f = BP.components(sc, ec)
    comp = collections.defaultdict(list)
    for n in xy:
        comp[f(n)].append(n)
    names = [n for r, ns in comp.items() if len(ns) >= 2 for n in ns]
    tr = cKDTree(np.array([xy[k] for k in names]))
    near = lambda p: (names[int(tr.query(p)[1])], float(tr.query(p)[0]))
    outs = B.read_outfalls(set(xy), log=lambda *x: None)
    print(f"管网节点 {len(xy):,}（连通块 ≥ 2 的 {len(names):,}），出水口 {len(outs)}")

    # ---------------- 泵站
    PS = gpd.read_file(GDB_FULL, layer="PS_PUMP").to_crs(32640)
    WW = pyogrio.read_dataframe(GDB_FULL, layer="PS_WET_WELL_AREA", read_geometry=False, columns=["PUMP_STATION_ID", "DEPTH", "BASE_LEVEL", "GROUND_LEVEL", "UNITID"])
    # 2026-10-17 新库有两座泵站的 PUMP_STATION_ID 填的是 "NA" / "Others"（Al Maqam TWA、Zakhir RAZ，各 4×240 L/s、扬程 16 m、湿井深 16.05 m）：按 UNITID 前缀改成 <前缀>_PSS_NEW
    _fix = lambda u: re.match(r"(SW_[A-Z0-9]+_[A-Z0-9]+)_", str(u)).group(1) + "_PSS_NEW"
    for D in (PS, WW):
        bad = D.PUMP_STATION_ID.astype(str).isin(["NA", "Others"])
        D.loc[bad, "PUMP_STATION_ID"] = D.loc[bad, "UNITID"].map(_fix)
    wdepth = WW.groupby("PUMP_STATION_ID").DEPTH.max()
    known = sorted(float(v) for v in wdepth if v and v > 0.5)
    dflt = round(float(np.median(known)), 2)
    print(f"湿井深：{len(known)} 座有值，中位数 {dflt} m（缺值的用它）")
    L = gpd.read_file(GDB_PROC, layer="PIPELINE", columns=["PIPE_SUBTYPE", "UNITID"]).to_crs(32640)
    PL = L[L.PIPE_SUBTYPE.astype(str) == "PressrLine"]
    gdb_segs = E.seg_list(PL)
    rows = []
    for sid, g in PS.groupby("PUMP_STATION_ID"):
        qs = [float(v) for v in g.FLOW_RATE if v and v > 0]
        c = Point(float(g.geometry.x.mean()), float(g.geometry.y.mean()))
        if not qs:
            n_p, q_p, src = None, None, "无流量数据：不建泵，列为局限"
        else:
            n_p, q_p, src = len(qs), round(sum(qs) / len(qs), 2), f"台账 PS_PUMP.FLOW_RATE：{' + '.join(f'{v:g}' for v in qs)} L/s"
        wet, dwet = near((c.x, c.y))
        end, length, _, used = E.trace(gdb_segs, (c.x, c.y), 10.0)
        dis, how, fm_ids = None, None, []
        if end is not None and math.hypot(end[0] - c.x, end[1] - c.y) > 30:
            dis, how = near(end), "客户 GDB 压力管"
            fm_ids = [u for u in used if u]
        depth = float(wdepth.get(sid, 0) or 0)
        note = ""
        if depth <= 0.5:
            depth, note = dflt, f"台账湿井深为 0，按其余泵站中位数 {dflt} m 补（Claude 自定）"
        rows.append({"unitid": str(g.UNITID.iloc[0]), "ps_id": sid, "name": str(g.UNITDESC.iloc[0]), "status": "", "comment": "", "n_pumps": n_p, "q_each_lps": q_p, "q_src": src,
                     "head_m": float(g.HEAD.mean()), "wetwell_node": wet, "wetwell_dist_m": round(dwet), "well_depth_m": depth, "on_m": round(0.5 * depth, 2), "off_m": round(0.1 * depth, 2),
                     "depth_note": note, "discharge_node": dis[0] if dis else None, "discharge_dist_m": round(dis[1]) if dis else None,
                     "discharge_src": how or "未知：抽出计算域（新建自由出水口）", "forcemain_m": round(length) if dis else None, "forcemain_unitids": fm_ids,
                     "x": round(c.x, 1), "y": round(c.y, 1)})
    CASES.mkdir(parents=True, exist_ok=True)
    (CASES / "aa_existing_pumps.json").write_text(json.dumps({"region": "ALAIN", "stations": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"现状泵站 {len(rows)} 座 → aa_existing_pumps.json")
    for r in rows:
        print(f'  {r["ps_id"]:<20} {r["n_pumps"]}×{r["q_each_lps"]} L/s  井 {r["wetwell_node"]}({r["wetwell_dist_m"]} m) 深 {r["well_depth_m"]} → {r["discharge_node"]}（{r["discharge_src"]}）{r["depth_note"]}')

    # ---------------- 塘
    PD = gpd.read_file(GDB_FULL, layer="POND").to_crs(32640)
    ponds = []
    for _, p in PD.iterrows():
        g = p.geometry
        unit = str(p.get("UID") or p.get("UNITID") or "") if str(p.get("UID") or p.get("UNITID") or "") not in ("", "None") else f"ST_{int(p.XCOORD)}_{int(p.YCOORD)}"
        vol, depth, zb = float(p.VOLUME), float(p.DEPTH), float(p.BOTTOM_FLOOR_LEVEL)
        # 2026-10-18 用户定：只有「客户出水口落在塘上（≤ 10 m）」才算这座塘的一维进水（Sayh Al Saad：OFL_400 正好在塘边）；
        # 不再按 150 m / 500 m 放宽去猜——那是我的假设，出水口 35–233 m 外多半是排去别处
        cand = [(g.distance(Point(xy[n])), n) for n in outs if g.distance(Point(xy[n])) <= 10.0]
        cand.sort()
        if not cand:
            raise SystemExit(f"塘 {unit}：10 m 内没有管网出水口，先查")
        ins = []
        for d, n in cand:
            ins.append({"from": n, "len": max(round(d, 1), 10.0), "z_from": round(inv[n], 3), "is_outfall": True})
        print(f"塘 {unit}：库容 {vol:,.0f} m³，深 {depth} m，塘底 {zb} m，进水出水口 {[(i['from'], i['len']) for i in ins]}")
        c0 = g.centroid
        ponds.append({"unitid": unit, "node": f"PND_{unit}", "vol_model_m3": round(vol), "vol_ledger_m3": vol, "xy": [round(c0.x, 2), round(c0.y, 2)],
                      "wiring": {"xy": [round(c0.x, 2), round(c0.y, 2)], "z_bot": zb, "depth_m": depth, "area_m2": round(vol / depth, 1), "pipes": ins, "dia_m": 1.0},
                      "footprint": mapping(g.simplify(1.0)), "footprint_area_ha": round(g.area / 1e4, 2),
                      "ledger": {"cover_level": float(p.COVER_LEVEL), "floor_material": str(p.FLOOR_MATERIAL), "type": str(p.UNITTYPE), "status": str(p.STATUS)}})
    # ---- 其余 13 个塘（AAM_stormwater_vector.gpkg / aam_pond_existing，用户 10-17 要求全部放进 L2）
    # 只有点位 + 面积 + 最大深度 + 库容，没有塘底高程、没有轮廓：
    #   轮廓 = 以点位为圆心、面积 = area_m2 的圆；等截面面积 = 库容 ÷ 最大深度（和台账口径一致，库容以台账为准）
    #   塘底：圆内 DTM 起伏（P90 − P10）≥ 一半最大深度 → 塘已在 DTM 里挖出（天然湖 / 已建塘），塘底 = P10；否则（在建、DTM 还是原地面）塘底 = DTM 中位数 − 最大深度
    #   进水：圆周 150 m 内的管网出水口；没有就放宽到 500 m 内最近的 3 个出水口；再没有就列为局限（塘进 L2 但孤立）
    from shapely.geometry import shape
    GP = ROOT / "data" / "2026-10-17" / "AAM_stormwater_vector.gpkg"
    ex = gpd.read_file(GP, layer="aam_pond_existing").to_crs(32640)
    ids = set(p["unitid"] for p in ponds)
    for _, r in ex.iterrows():
        c0 = r.geometry
        area_g, depth, vol = float(r.area_m2), float(r.max_depth_m), float(r.volume_m3)
        rad = math.sqrt(area_g / math.pi)
        g = c0.buffer(rad, 32)
        unit = f"AAMP_{int(c0.x)}_{int(c0.y)}"
        assert unit not in ids; ids.add(unit)
        gx, gy = np.meshgrid(np.linspace(c0.x - rad, c0.x + rad, 41), np.linspace(c0.y - rad, c0.y + rad, 41))
        gx, gy = gx.ravel(), gy.ravel(); m = (gx - c0.x) ** 2 + (gy - c0.y) ** 2 <= rad * rad
        z = B.sample_dtm(gx[m], gy[m]); z = z[np.isfinite(z)]
        p10, p50, p90 = (float(np.percentile(z, q)) for q in (10, 50, 90))
        dug = (p90 - p10) >= 0.5 * depth
        zb = p10 if dug else p50 - depth
        zrule = "DTM 圆内 P10（塘已在 DTM 里）" if dug else "DTM 中位数 − 最大深度（DTM 还是原地面）"
        # 点塘（gpkg）没有轮廓、也没有客户的进水管记录：不接一维（出水口离塘 35–233 m，去向不明），只靠二维占地里的地表水进塘
        near, how = [], "无一维进水（点塘，无进水管记录）"
        ins = []
        print(f"塘 {r['name']}（{r.status}）：库容 {vol:,.0f} m³、深 {depth} m、面积 {area_g:,.0f} m²；DTM P10/P50/P90 = {p10:.1f}/{p50:.1f}/{p90:.1f} → 塘底 {zb:.2f} m（{zrule}）；进水 {len(ins)} 个（{how}）" + ("" if ins else "  ← 没有出水口，孤立"))
        ponds.append({"unitid": unit, "node": f"PND_{unit}", "vol_model_m3": round(vol), "vol_ledger_m3": vol, "xy": [round(c0.x, 2), round(c0.y, 2)],
                      "wiring": {"xy": [round(c0.x, 2), round(c0.y, 2)], "z_bot": round(zb, 3), "depth_m": depth, "area_m2": round(vol / depth, 1), "pipes": ins, "dia_m": 1.0},
                      "footprint": mapping(g), "footprint_area_ha": round(g.area / 1e4, 2),
                      "ledger": {"name": str(r["name"]), "status": str(r.status), "area_m2": area_g, "info_source": str(r.info_source), "z_bot_rule": zrule, "dtm_p10_p50_p90": [round(p10, 2), round(p50, 2), round(p90, 2)],
                                 "inflow_rule": how, "src": "AAM_stormwater_vector.gpkg / aam_pond_existing"}})
    (CASES / "aa_ponds.json").write_text(json.dumps({"region": "ALAIN", "ponds": ponds, "note": "L2 = L1 + 全部塘（GDB POND 1 座 + gpkg aam_pond_existing 13 个）；塘按最大深度、等截面；进水出水口 L2 里改接进塘"}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"塘 {len(ponds)} 座 → aa_ponds.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
