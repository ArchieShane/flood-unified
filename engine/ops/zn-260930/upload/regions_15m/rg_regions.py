# -*- coding: utf-8 -*-
"""三个区域（Yas bridges interchange / Al Bateen / Bani Yas）的案例数据：计算域、现状泵站、已建成塘（2026-10-11）。

推演计划表 v34「15m格网」第 73–126 行。用户 10-11 定的口径：
  计算域   客户汇水区图层（KMZ）里的那一个多边形：RBE1-OF1 / RD-A / RD 14（不外扩、不闭运算）
  一维管网 全市一维（客户 GDB 重建，build_city1d），触及计算域的连通块整块取（同 C1）
  L1       既有管网 + 计算域内客户 GDB 的现状泵站（规则同 c1_existing_pumps.py）
  L2       L1 + 已建成塘（客户 flood 库 stormwater_drainage.pond 里落进计算域的；全市 L2 的接法：深 0.30 m 的平底蓄水节点、面积 = 库容 ÷ 0.3、进水管取自全市 L2）
  只做 L1 / L2，不做 L2+planned / L2+suggested

本脚本在开发机上跑一次，产物放进仓库 cases/regions/（小文件、入库），服务器上不再需要 KMZ、flood 库、Parsons 导出：
  cases/regions/<REG>_domain.geojson           计算域（EPSG:4326）
  cases/regions/<REG>_existing_pumps.json      计算域内现状泵站（同 out/c1_existing_pumps.json 的格式）
  cases/regions/<REG>_ponds.json               计算域内已建成塘（空 = L2 同 L1）

用法：python tools/rg_regions.py [YAS BAT BNY]
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))

CASES = ROOT / "cases" / "regions"
# 区域代号 → 名称、KMZ、KMZ 里的名称、热点（推演计划表第 3 列）
REGIONS = {
    "YAS": dict(title="Yas bridges interchange (RBE1-OF1)", kmz="Shahamah_Zayed catchments.kmz", feat="RBE1-OF1",
                hs=["3", "85", "490", "7"], dir="Yas_bridges_interchange"),
    "BAT": dict(title="Al Bateen (RD-A)", kmz="island catchment.kmz", feat="RD-A",
                hs=["76", "75", "377", "379", "380"], dir="Al_Bateen"),
    "BNY": dict(title="Bani Yas (RD 14)", kmz="Al Wathba catchments.kmz", feat="RD 14",
                hs=["49"], dir="Bani_Yas"),
}

# 全市 L2 里 Baniyas Lake（SW_BYA_EB2_06_PND_41）的接法（data/全市_61mm2h/L2_加22座已建成塘/一二维/耦合的一维/L2_耦合一维模型.inp）：
# 三根进水管，端点码就是 GDB 的 StartCode/EndCode，名字同全市重建的节点；塘在模型里深 0.30 m、水面 1,872,300 m²（= 562,000 / 0.3 的取整）
POND_WIRING = {
    "SW_BYA_EB2_06_PND_41": {
        "xy": [259425.78, 2690182.64], "z_bot": 13.010, "depth_m": 0.30, "area_m2": 1872300.0,
        "pipes": [{"from": "259146_2690277", "len": 10.000, "z_from": 9.882},
                  {"from": "259322_2689968", "len": 76.618, "z_from": 10.074},
                  {"from": "259526_2689930", "len": 65.624, "z_from": 11.859}],
        "dia_m": 0.6,
    }}


def domain_utm(reg: str):
    import geopandas as gpd
    from build_catchment_map import kmz_polygons
    r = REGIONS[reg]
    t = next(t for t in kmz_polygons(r["kmz"]) if t[0] == r["feat"] and t[1].geom_type == "Polygon")
    return gpd.GeoSeries([t[1].buffer(0)], crs=4326).to_crs(32640).iloc[0]


def write_domain(reg: str) -> object:
    import geopandas as gpd
    D = domain_utm(reg)
    if D.geom_type != "Polygon":
        raise SystemExit(f"{reg}：计算域不是单个多边形（{D.geom_type}）")
    CASES.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame([{"id": reg, "name": REGIONS[reg]["title"], "area_km2": round(D.area / 1e6, 3), "source": f"{REGIONS[reg]['kmz']} / {REGIONS[reg]['feat']}"}],
                     geometry=[D], crs=32640).to_crs(4326).to_file(CASES / f"{reg}_domain.geojson", driver="GeoJSON")
    print(f"  {reg} 计算域 {D.area / 1e6:.2f} km²，{len(D.exterior.coords)} 个顶点 → {reg}_domain.geojson")
    return D


def write_ponds(reg: str, D) -> list:
    """flood 库里落进计算域的塘（台账库容 0 的按全市 L2 的口径取模型库容）。"""
    import tomllib
    import psycopg
    import geopandas as gpd
    from shapely import wkb as shwkb
    from shapely.geometry import mapping
    c = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))["source"]["flood"]
    with psycopg.connect(host=c["host"], port=c["port"], user=c["user"], password=c["password"], dbname=c["dbname"], connect_timeout=30) as conn, conn.cursor() as cur:
        cur.execute("select unitid, volume, depth, st_asbinary(st_transform(geom, 32640)) from stormwater_drainage.pond where geom is not null")
        rows = cur.fetchall()
    out = []
    for uid, vol, dep, wkbb in rows:
        g = shwkb.loads(bytes(wkbb))
        if not D.intersects(g):
            continue
        w = POND_WIRING.get(uid)
        if w is None:
            raise SystemExit(f"{reg}：塘 {uid} 在计算域里，但 POND_WIRING 里没有它的接法，先补")
        vol_m = round(w["area_m2"] * w["depth_m"])
        out.append({"unitid": uid, "node": f"PND_{uid}", "vol_ledger_m3": vol, "vol_model_m3": vol_m, "xy": w["xy"], "wiring": w,
                    "footprint": mapping(g.simplify(1.0)), "footprint_area_ha": round(g.area / 1e4, 1)})
    CASES.mkdir(parents=True, exist_ok=True)
    (CASES / f"{reg}_ponds.json").write_text(json.dumps({"region": reg, "ponds": out, "note": "已建成塘；L2 = L1 + 这些塘（全市 L2 的接法）。空 = 计算域内没有已建成塘，L2 同 L1"},
                                                         ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {reg} 已建成塘 {len(out)} 座：{[p['unitid'] for p in out]}")
    return out


def write_pumps(reg: str, D) -> list:
    """计算域内的现状泵站（规则同 c1_existing_pumps.py；吸水井 / 排放点取全市重建网里最近的井）。"""
    import glob
    import math
    import re
    import geopandas as gpd
    import numpy as np
    import pandas as pd
    from scipy.spatial import cKDTree
    import build_city1d as B
    import c1_existing_pumps as E
    df = B.read_pipes(None, log=lambda *x: None)
    xy_all, _, _, _ = B.build_nodes(df, log=lambda *x: None)
    bb = tuple(D.bounds)
    PS = gpd.read_file(E.GDB, layer="PS_PUMP", bbox=bb).to_crs(32640)
    PS = PS[PS.within(D)]
    L = gpd.read_file(E.GDB, layer="PIPELINE", bbox=bb).to_crs(32640)
    PL = L[L.PIPE_SUBTYPE.astype(str) == "PressrLine"]
    pp, pr = [], []
    for m in E.PARSONS.iterdir():
        if not m.is_dir():
            continue
        for shp in glob.glob(str(m / "**" / "*.shp"), recursive=True):
            n = pathlib.Path(shp).stem
            if re.match(r"(?i)^(pumps?|pump table)$", n) or re.match(r"(?i)^(pressure.?pipes?|pr pipes|pressure pipe table)$", n):
                x = gpd.read_file(shp, bbox=bb)
                x = x.set_crs(32640, allow_override=True) if x.crs is None else x.to_crs(32640)
                if len(x):
                    x["model"] = m.name
                    (pp if "ump" in n.lower() else pr).append(x)
    PP = gpd.GeoDataFrame(pd.concat(pp), crs=32640) if pp else None
    PR = gpd.GeoDataFrame(pd.concat(pr), crs=32640) if pr else None
    gdb_segs, par_segs = E.seg_list(PL), (E.seg_list(PR, tag=True) if PR is not None else [])
    names = list(xy_all); tr = cKDTree(np.array([xy_all[k] for k in names]))
    near = lambda p: (names[tr.query(p)[1]], float(tr.query(p)[0]))
    rows = []
    for _, r in PS.iterrows():
        c = r.geometry
        cap = E.capacity(r.COMMENTS)
        oos = "out of service" in str(r.COMMENTS).lower()
        parsons = []
        if PP is not None:
            d = PP.distance(c)
            for _, q in PP[d < 80].iterrows():
                f = q.get("PUMP_FLOW")
                parsons.append({"model": q.model, "flow_lps": float(f) if f is not None and not pd.isna(f) else None, "dist_m": round(float(q.geometry.distance(c)))})
        if oos:
            n_p, q_p, src = 0, 0.0, "停用（客户备注 Out of Service）"
        elif cap:
            n_p, q_p, src = cap[0], cap[1], f"客户备注 {cap[0]}×{cap[1]:g}"
        elif any(p["flow_lps"] for p in parsons):
            fl = [p["flow_lps"] for p in parsons if p["flow_lps"]]
            n_p, q_p, src = len(fl), round(sum(fl) / len(fl), 1), "Parsons 同址泵 PUMP_FLOW（" + " + ".join(f"{v:g}" for v in fl) + "）"
        else:
            n_p, q_p, src = None, None, "无数据：不建泵，列为局限"
        wet, dwet = near((c.x, c.y))
        dis, how, pipe_len, fm_ids = None, None, None, []
        for segs, label in ((gdb_segs, "客户 GDB 压力管"), (par_segs, "Parsons 压力管")):
            end, length, fm, used_ids = E.trace(segs, (c.x, c.y), 10.0 if label.startswith("客户") else 30.0)
            if end is not None and math.hypot(end[0] - c.x, end[1] - c.y) > 30:
                dis, how, pipe_len = near(end), label + (f"（{fm}）" if fm else ""), round(length)
                fm_ids = [u for u in used_ids if u] if label.startswith("客户") else []
                break
        depth = float(r.RESERVIOR_DEPTH_METER or 0)
        rows.append({"unitid": r.UNITID, "ps_id": r.PUMP_STATION_ID, "status": r.STATUS, "migration": r.INFORMIGRATION, "comment": r.COMMENTS,
                     "n_pumps": n_p, "q_each_lps": q_p, "q_src": src, "parsons": parsons, "wetwell_node": wet, "wetwell_dist_m": round(dwet),
                     "well_depth_m": depth, "on_m": round(0.5 * depth, 2) if depth else None, "off_m": round(0.1 * depth, 2) if depth else None,
                     "depth_note": "客户库没有井深" if not depth else "", "discharge_node": dis[0] if dis else None,
                     "discharge_dist_m": round(dis[1]) if dis else None, "discharge_src": how or "未知：需要用户定", "forcemain_m": pipe_len,
                     "forcemain_unitids": fm_ids, "x": round(c.x, 1), "y": round(c.y, 1)})
    CASES.mkdir(parents=True, exist_ok=True)
    (CASES / f"{reg}_existing_pumps.json").write_text(json.dumps({"region": reg, "domain_km2": round(D.area / 1e6, 2), "stations": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {reg} 计算域内现状泵站 {len(rows)} 座")
    for r in rows:
        print(f'    {r["ps_id"]!s:<10} {r["unitid"]:<26} {r["n_pumps"]}×{r["q_each_lps"]} L/s [{r["q_src"]}] 井 {r["wetwell_node"]}({r["wetwell_dist_m"]} m) 深 {r["well_depth_m"]} → {r["discharge_node"]}（{r["discharge_src"]}）')
    return rows


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    regs = sys.argv[1:] or list(REGIONS)
    for reg in regs:
        print(f"== {reg} {REGIONS[reg]['title']}")
        D = write_domain(reg)
        write_ponds(reg, D)
        write_pumps(reg, D)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
