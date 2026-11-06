# -*- coding: utf-8 -*-
"""ADM 汇水区 · 30 m 网格 · 14 个区域的案例数据（2026-10-15）。

客户 10-15 的 ADM 汇水区计划表，只做 L1 / L2、1 h 61 mm、二维 30 m。计算域 = 客户汇水区图层（KMZ）里那一个多边形（不外扩）；
面积与客户地图一致（客户计划表里 MI-8 / RB-3 / MBZ / YAS OUTFALL 四个面积写错，以地图和 KMZ 为准）。

本脚本在开发机上跑一次，产物放进 cases/ad/（小文件、随上传包走），服务器上不再需要 KMZ / flood 库 / Parsons 导出：
  cases/ad/<区>_domain.geojson        计算域（EPSG:4326）
  cases/ad/<区>_existing_pumps.json   计算域内现状泵站（规则同 c1_existing_pumps.py，复用 rg_regions.write_pumps）
  cases/ad/<区>_ponds.json            计算域内已建成塘（接法取 pondlink/ponds_linked.json 里全市 L2 的 23 座塘；空 = L2 同 L1）
  cases/ad/hotspots_ad.json           14 个区内的积水点（ADM 热点）：编号、位置、重要性、UTM 40N 坐标

用法：python tools/ad_regions.py [区域代号 ...]      （缺省全部）
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))

CASES = ROOT / "cases" / "ad"
SHZ, ISL, WTH = "Shahamah_Zayed catchments.kmz", "island catchment.kmz", "Al Wathba catchments.kmz"
# 区域代号 → 名称、KMZ、KMZ 里的名称、目录名、客户计划表 ADM 序号、客户面积（地图）
REGIONS = {
    "MI8":  dict(title="MI-8 (RD-10) – Mussafah", kmz=SHZ, feat="MI-8", dir="MI-8", adm="B7", km2=250.62),
    "RD2":  dict(title="RD-2 (PS-4 / RD6, 7 & 8)", kmz=SHZ, feat="RD-2", dir="RD-2", adm="B14", km2=126.99),
    "YASO": dict(title="YAS OUTFALL – AUH Airport", kmz=SHZ, feat="YAS OUTFALL", dir="YAS_OUTFALL", adm="B11", km2=25.86),
    "RB1":  dict(title="RB-1", kmz=SHZ, feat="RB-1", dir="RB-1", adm="B10", km2=18.42),
    "RBE1": dict(title="RBE1-OF1 (Yas2 – Al Falah)", kmz=SHZ, feat="RBE1-OF1", dir="RBE1-OF1", adm="B12", km2=22.06),
    "MBZ":  dict(title="MBZ (RD-9) – Mussafah / MBZ", kmz=SHZ, feat="MBZ", dir="MBZ", adm="B9", km2=18.07),
    "RD14": dict(title="RD-14", kmz=WTH, feat="RD 14", dir="RD-14", adm="B15", km2=15.44),
    "RB3":  dict(title="RB-3", kmz=SHZ, feat="RB-3", dir="RB-3", adm="B8", km2=7.99),
    "RDA":  dict(title="RD-A", kmz=ISL, feat="RD-A", dir="RD-A", adm="A1", km2=3.21),
    "RDF":  dict(title="RD-F", kmz=ISL, feat="RD-F", dir="RD-F", adm="A2", km2=5.44),
    "RDC":  dict(title="RD-C", kmz=ISL, feat="RD-C", dir="RD-C", adm="A3", km2=1.94),
    "QAB1": dict(title="Qasr Al Bahr-1", kmz=ISL, feat="Qasr Al Bahr-1", dir="Qasr_Al_Bahr-1", adm="A4", km2=1.52),
    "RDI":  dict(title="RD-I", kmz=ISL, feat="RD-I", dir="RD-I", adm="A5", km2=5.79),
    "RDG":  dict(title="RD-G", kmz=ISL, feat="RD-G", dir="RD-G", adm="A6", km2=4.28),
    # 10-17 加：客户表 B16（边界「是」、1h 61mm L2、30 m）。KMZ 里没有叫 Al Riyadh 的多边形，是 Al Wathba 里 9 块 Pond 汇水区（合计 76.19 km²，客户 76.15）；
    # 它们之间有少量重叠，取并集（75.4 km²）。feats 给多个名字 = 并集；Pond 14 / Pond 10 / Pond 11 / Pond Al WATHBH North 不在里面
    "ALR":  dict(title="Al Riyadh City – all ponds", kmz=WTH, feat="Pond 1/2A/2B/3/4/A/B/c/D", dir="Al_Riyadh_ponds", adm="B16", km2=76.15,
                 feats=["Pond 1", "Pond 2A", "Pond 2B", "Pond 3", "Pond 4", "Pond A", "Pond B", "Pond c", "Pond D"]),
}


def domain_utm(reg: str):
    """KMZ 里该名称的全部多边形取并集（应当是单个多边形，不是就报错）。"""
    import geopandas as gpd
    from shapely.ops import unary_union
    from build_catchment_map import kmz_polygons
    r = REGIONS[reg]
    names = r.get("feats") or [r["feat"]]
    ps = [t[1].buffer(0) for t in kmz_polygons(r["kmz"]) if t[0] in names and t[1].geom_type == "Polygon"]
    if r.get("feats") and len(ps) != len(names):
        raise SystemExit(f"{reg}：{r['kmz']} 里只找到 {len(ps)} / {len(names)} 个多边形")
    if not ps:
        raise SystemExit(f"{reg}：{r['kmz']} 里没有 {r['feat']}")
    g = gpd.GeoSeries([unary_union(ps)], crs=4326).to_crs(32640).iloc[0]
    if g.geom_type != "Polygon":
        raise SystemExit(f"{reg}：计算域不是单个多边形（{g.geom_type}，{len(ps)} 块）")
    return g


def write_domain(reg: str):
    import geopandas as gpd
    D = domain_utm(reg)
    CASES.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame([{"id": reg, "name": REGIONS[reg]["title"], "area_km2": round(D.area / 1e6, 3), "source": f"{REGIONS[reg]['kmz']} / {REGIONS[reg]['feat']}"}],
                     geometry=[D], crs=32640).to_crs(4326).to_file(CASES / f"{reg}_domain.geojson", driver="GeoJSON")
    print(f"  {reg} 计算域 {D.area / 1e6:.2f} km²（客户地图 {REGIONS[reg]['km2']}），{len(D.exterior.coords)} 个顶点 → {reg}_domain.geojson")
    return D


# 全市 L2（data/全市_61mm2h/L2_加22座已建成塘/一二维/耦合的一维/L2_耦合一维模型.inp）里这两座塘的接法：深度、占地、进水管端点码、管长、管底
POND_WIRING = {
    "SW_BYA_EB2_06_PND_41": {    # Baniyas Lake（RD-14）：同 rg_regions.POND_WIRING
        "xy": [259425.78, 2690182.64], "z_bot": 13.010, "depth_m": 0.30, "area_m2": 1872300.0,
        "pipes": [{"from": "259146_2690277", "len": 10.000, "z_from": 9.882},
                  {"from": "259322_2689968", "len": 76.618, "z_from": 10.074},
                  {"from": "259526_2689930", "len": 65.624, "z_from": 11.859}],
        "dia_m": 0.6},
    "SW_SCI_MSH44_PND_42": {     # Shakhbout City 小塘（RD-2）：库容曲线 2500 m² 左右、深 1.02 m，按平底 2500 m² 取（模型库容 2,550 m³，台账 2,497 m³）
        "xy": [256438.13, 2694379.57], "z_bot": 2.880, "depth_m": 1.02, "area_m2": 2500.0,
        "pipes": [{"from": "256408_2694342", "len": 26.900, "z_from": 4.375}],
        "dia_m": 0.6},
    "SW_MAR_RD110_PND_37": {     # Madinat Al Riyad 塘（ALR / B16）：全市 L2 的 pond_link 设计库容 336,975 m³、塘底 25.91、塘顶 30.32（深 4.41 m，台账库容比现状地形能兜的 19,481 m³ 大，全市 L2 按挖到台账库容建）；
        # 按平底 76,413 m²（= 库容 / 深度；占地多边形 76,280 m²）取，库容曲线用等效平底；进水管取 pond_link 认出的 PI_251942（DN160，管底 26.8，距塘 37 m）
        "xy": [265133.68, 2689631.14], "z_bot": 25.910, "depth_m": 4.41, "area_m2": 76413.0,
        "pipes": [{"from": "265328_2689558", "len": 37.100, "z_from": 26.800}],
        "dia_m": 0.6},
}
# 在计算域里，但台账没有库容、没有进水管，全市 L2 也没建的塘（L2 同 L1）
NOT_IN_CITY_L2 = {"SW_AWA_AWS_RZS_32": "台账库容 0、周边 6 根管管内底全部无效，全市 L2 的 22 座里没有它"}


DEFAULT_POND_DEPTH_M = 1.0       # 来源既没给库容也没给合理深度的塘：按平底 1.0 m 深（给了库容的塘取库容 / 占地的中位数就是 1.02 m）
POND_DEPTH_RANGE = (0.1, 5.0)    # 来源给的深度 / 由库容折出的深度落在这个范围里才采用（来源里有 101 m 这种明显是高程的值）
# ADM 14 区内全部塘的清单（_build/ponds/ 里的脚本从 Parsons / 客户 / 现状塘 / 全市 L2 等 6 类来源按空间重叠去重得到 221 个；
# 没有已建成 / 规划字段 → 用户 10-16 定：分不清 planned 的一律按已建成算，只到 L2）
PONDS_ALL = CASES / "ponds_all.geojson"


def pond_depth_vol(area: float, vol, depth):
    """库容 / 深度的取法 → (深度 m, 模型库容 m³, 说明)。"""
    import math
    ok = lambda d: d is not None and not (isinstance(d, float) and math.isnan(d)) and POND_DEPTH_RANGE[0] <= d <= POND_DEPTH_RANGE[1]
    v = None if vol is None or (isinstance(vol, float) and math.isnan(vol)) or vol <= 0 else float(vol)
    if v is not None and ok(v / area):
        return v / area, v, "来源库容 / 占地"
    if ok(depth):
        return float(depth), area * float(depth), "来源深度 × 占地"
    return DEFAULT_POND_DEPTH_M, area * DEFAULT_POND_DEPTH_M, f"缺省深度 {DEFAULT_POND_DEPTH_M:g} m × 占地"


def linked_wiring(uid: str, g) -> dict:
    """全市 L2 的塘（out/pondlink/ponds_linked.json）→ 和 POND_WIRING 同结构的接法。
    库容 = pond_link 的设计库容（全市 L2 按它建），深度 = 库容 / 占地（落在 POND_DEPTH_RANGE 才用，否则取 pond_link 的深度）、占地取塘多边形面积（等效平底）；
    pond_link 认出进水管的塘接那根管（起点码取 start_code，库里没给管底 → 后面 network() 取管网里该井的管内底）；认不出的 auto=True，建模时找占地外 8 m 内的井。"""
    import geopandas as gpd
    from shapely.geometry import shape
    L = {d["unitid"]: d for d in json.loads((ROOT / "out" / "pondlink" / "ponds_linked.json").read_text(encoding="utf-8"))}
    if uid not in L:
        raise SystemExit(f"塘 {uid} 在计算域里，但 POND_WIRING / NOT_IN_CITY_L2 / ponds_linked.json 里都没有它，先补")
    d = L[uid]; des = d["design"]
    area = float(g.area)
    dep, vol, how = pond_depth_vol(area, des["volume_design_m3"], max(float(des["z_top_m"]) - float(des["z_floor_m"]), 0.3))
    c = g.representative_point()
    w = {"xy": [round(c.x, 2), round(c.y, 2)], "z_bot": float(des["z_floor_m"]), "depth_m": round(dep, 3), "area_m2": round(area, 1), "dia_m": 0.6,
         "pipes": [], "how": f"全市 L2 pond_link：设计库容 {des['volume_design_m3']:,.0f} m³（{how}）"}
    fin = (d.get("link") or {}).get("inflow")
    if fin:
        w["pipes"] = [{"from": fin["start_code"], "len": max(float(fin.get("dist_m") or 20.0), 10.0), "z_from": float(fin["invert_m"])}]
    else:
        w["auto"] = True
    return w


def write_ponds(reg: str, D) -> list:
    """计算域内的塘（ponds_all.geojson 去重后清单，代表点落进域内）：全部按已建成塘建进 L2。
    · 全市 L2 的那 23 座：用 POND_WIRING 里手工整理的接法（进水管 / 库容曲线）；全市 L2 本来就没建的不建
    · 其余：wiring.auto = True，建模时（ad_run_30m.network）在管网里找贴着塘的井接进水拍门管；找不到的就是只在二维里收水的独立蓄水体"""
    import geopandas as gpd
    from shapely.geometry import mapping
    ps = gpd.read_file(PONDS_ALL).to_crs(32640)
    ps = ps[ps.geometry.representative_point().within(D)]
    out, skipped = [], []
    for _, r in ps.iterrows():
        g = r.geometry
        if r["cat"].startswith("已建成塘"):
            uid = r["pid"]
            if uid in NOT_IN_CITY_L2:
                skipped.append({"unitid": uid, "why": NOT_IN_CITY_L2[uid]}); continue
            w = POND_WIRING.get(uid)
            if w is None:
                w = linked_wiring(uid, g)          # 10-17：ALR 域里有 13 座全市 L2 的塘，不再逐座手写，按 pond_link 的设计库容 / 进水管推
            out.append({"unitid": uid, "node": f"PND_{uid}", "cat": r["cat"], "vol_ledger_m3": float(r["vol_m3"]) if r["vol_m3"] == r["vol_m3"] else None,
                        "vol_model_m3": round(w["area_m2"] * w["depth_m"]), "xy": w["xy"], "wiring": w,
                        "footprint": mapping(g.simplify(1.0)), "footprint_area_ha": round(g.area / 1e4, 1)})
            continue
        uid = f"PD{int(r['seq']):03d}"
        if str(r["attr_src"]).startswith("ponds.zip") and (r["vol_m3"] or 0) <= 0 and (r["depth_m"] or 0) <= 0:
            skipped.append({"unitid": uid, "why": f"Parsons 模型里库容 0、深度 0（{r['attr_src']}），没有蓄水能力，不建"}); continue
        area = float(g.area)
        dep, vol, how = pond_depth_vol(area, r["vol_m3"], r["depth_m"])
        c = g.representative_point()
        out.append({"unitid": uid, "node": f"PND_{uid}", "name": r["pid"], "cat": r["cat"], "sources": r["sources"], "pipes_5m": int(r["pipes_5m"]),
                    "vol_ledger_m3": float(r["vol_m3"]) if r["vol_m3"] == r["vol_m3"] else None, "vol_model_m3": round(vol), "depth_how": how,
                    "xy": [round(c.x, 2), round(c.y, 2)],
                    "wiring": {"auto": True, "xy": [round(c.x, 2), round(c.y, 2)], "z_bot": None, "depth_m": round(dep, 3), "area_m2": round(area, 1), "pipes": [], "dia_m": 0.6},
                    "footprint": mapping(g.simplify(1.0)), "footprint_area_ha": round(g.area / 1e4, 1)})
    CASES.mkdir(parents=True, exist_ok=True)
    (CASES / f"{reg}_ponds.json").write_text(json.dumps({"region": reg, "ponds": out, "not_modelled": skipped,
                                                         "note": "已建成塘（含分不清规划的 Parsons 塘）；L2 = L1 + 这些塘。空 = 计算域内没有塘，L2 同 L1"}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {reg} 塘 {len(out)} 座（全市 L2 的 {sum(1 for p in out if not p['wiring'].get('auto'))}，其余 {sum(1 for p in out if p['wiring'].get('auto'))}）" + (f"；不建：{[s['unitid'] for s in skipped]}" if skipped else ""))
    return out


def write_hotspots(regs) -> None:
    import geopandas as gpd
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640)
    rows = []
    for reg in regs:
        D = domain_utm(reg)
        m = H[H.within(D)]
        for hid, r in m.set_index("hotspot_id").iterrows():
            rows.append({"hotspot_id": hid, "region": reg, "adm": REGIONS[reg]["adm"], "catchment": REGIONS[reg]["feat"], "area": r["area"], "location": r["location"],
                         "criticality": r["criticality"], "x": round(r.geometry.x, 2), "y": round(r.geometry.y, 2)})
    CASES.mkdir(parents=True, exist_ok=True)
    (CASES / "hotspots_ad.json").write_text(json.dumps({"crs": "EPSG:32640", "regions": REGIONS, "hotspots": rows}, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"  积水点 {len(rows)} 个 → hotspots_ad.json")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    regs = sys.argv[1:] or list(REGIONS)
    import rg_regions as RG
    RG.REGIONS.update({k: v for k, v in REGIONS.items()})
    RG.CASES = CASES
    for reg in regs:
        print(f"== {reg} {REGIONS[reg]['title']}")
        D = write_domain(reg)
        write_ponds(reg, D)
        RG.write_pumps(reg, D)
    if not sys.argv[1:]:
        write_hotspots(list(REGIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
