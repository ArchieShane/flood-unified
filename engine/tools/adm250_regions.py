# -*- coding: utf-8 -*-
"""ADM 全市 · 二维 250 m · 9 场雨 · 只算 L2 · 案例数据生成（2026-10-19）。

用户 10-19 定：Abu Dhabi 全市（计算域取现有全市 1,234 km²，同 city_run_250m.py）、33 / 61 / 96 mm × 1 / 2 / 24 h、二维 250 m、
只算 L2（现状管网 + 现状泵站 + 已建成塘）、口径取 ADM 标准（同 adm_rest：CN 82 / Tc 30 min / 曼宁 0.03 / 全市管网截取 / 现状泵站 + 域内已建成塘 / 积水点结果表），
不进 flood-viewer / flood-aio，不转 shp + dat。

在开发机上跑一次（用 .venv 的 python），产物进 cases/ad250/（随上传包走）：
  cases/ad250/CITY_domain.geojson        计算域（EPSG:4326）
  cases/ad250/CITY_existing_pumps.json   计算域内现状泵站（ad_regions / rg_regions 同一套规则）
  cases/ad250/CITY_ponds.json            计算域内已建成塘（ponds_all.geojson 去重清单 + 全市 L2 的 23 座接法）
  cases/ad250/hotspots_ad.json           计算域内的 ADM 积水点
  cases/ad250/adm250_all.tsv             任务清单：标签 代号 区域目录名 网格m 面积km2 积水点数 服务器（服务器脚本按主机名取自己的）

用法：.venv/Scripts/python.exe tools/adm250_regions.py [--data <含 ponds_l2.geojson 等的目录>]
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))

CASES = ROOT / "cases" / "ad250"
UPLOAD_DATA = ROOT.parent / "upload" / "city_250m" / "data"
RES_M = 250
TITLE = "ADM City (Abu Dhabi, whole city)"
DIR = "ADM_City"
# 固定分工（用户 10-19 确认）：服务器 1（48 核 / 188 GB）4 场，服务器 2 2 场，服务器 3 3 场
PLAN = {
    "CITY_M33H24": 1, "CITY_M61H24": 1, "CITY_M96H24": 1, "CITY_M33H1": 1,
    "CITY_M61H2": 2, "CITY_M96H2": 2,
    "CITY_M33H2": 3, "CITY_M61H1": 3, "CITY_M96H1": 3,
}
RAINS = [(33, 1), (33, 2), (33, 24), (61, 1), (61, 2), (61, 24), (96, 1), (96, 2), (96, 24)]


def city_domain(data: pathlib.Path):
    """同 city_run_250m.network()：塘 + 塘汇水区 ∪ 管网服务范围，外扩 300 m 再内缩 100 m，取最大一块、去洞。"""
    import geopandas as gpd
    from shapely.geometry import MultiPoint, Polygon
    from shapely.ops import unary_union
    import build_city1d as B
    import build_pilot as BP
    df = B.read_pipes(None, log=lambda *x: None)
    xy_all, ground, inv, _ = B.build_nodes(df, log=lambda *x: None)
    sc = df.StartCode.astype("string").fillna("").values
    ec = df.EndCode.astype("string").fillna("").values
    f = BP.components(sc, ec)
    comp = collections.defaultdict(list)
    for n in xy_all:
        comp[f(n)].append(n)
    keep = {n for r, ns in comp.items() if len(ns) >= 2 for n in ns}
    xy = {n: xy_all[n] for n in xy_all if n in keep}
    area = B.served_areas(xy, log=lambda *x: None)
    R_served = B.K_RADIUS * math.sqrt(sum(area.values()) / max(len(xy), 1))
    P0 = gpd.read_file(data / "ponds_l2.geojson").to_crs(32640)
    C0 = gpd.read_file(data / "l2_pond_catch.geojson").to_crs(32640)
    region = unary_union(list(C0.geometry.buffer(0)) + list(P0.geometry.buffer(0)))
    served = MultiPoint(list(xy.values())).buffer(R_served, 4)
    dom = unary_union([region, served]).buffer(300.0).buffer(-100.0).simplify(30.0)
    if dom.geom_type == "MultiPolygon":
        parts = sorted(dom.geoms, key=lambda g: -g.area)
        print(f"  计算域有 {len(parts)} 块，取最大一块 {parts[0].area / 1e6:,.1f} km²")
        dom = parts[0]
    return Polygon(dom.exterior), len(xy), R_served


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default=str(UPLOAD_DATA))
    a = ap.parse_args()
    data = pathlib.Path(a.data)
    import geopandas as gpd
    import ad_regions as AR
    import rg_regions as RG
    import build_city1d as B
    CASES.mkdir(parents=True, exist_ok=True)
    D, n_nodes, R = city_domain(data)
    km2 = D.area / 1e6
    print(f"== CITY 计算域 {km2:,.1f} km²（{len(D.exterior.coords)} 个顶点；全市一维 {n_nodes:,} 个节点，服务半径 {R:.1f} m）")
    gpd.GeoDataFrame([{"id": "CITY", "name": TITLE, "area_km2": round(km2, 3), "source": "city_run_250m 同一计算域"}], geometry=[D], crs=32640).to_crs(4326).to_file(CASES / "CITY_domain.geojson", driver="GeoJSON")
    reg = dict(title=TITLE, kmz="city_run_250m domain", feat="ADM City", dir=DIR, adm="CITY", km2=round(km2, 2), res=RES_M, rains=[list(r) for r in RAINS])
    # ad_regions.write_ponds / rg_regions.write_pumps 按 CASES 写文件、按 REGIONS 取名称
    AR.CASES = CASES; RG.CASES = CASES; RG.REGIONS.update({"CITY": reg})
    _rp, _bn = B.read_pipes, B.build_nodes
    _c = {}
    B.read_pipes = lambda *x, **k: _c.setdefault("df", _rp(*x, **k))
    B.build_nodes = lambda df, *x, **k: _c.setdefault("nodes", _bn(df, *x, **k))
    ponds = AR.write_ponds("CITY", D)
    pumps = RG.write_pumps("CITY", D)
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640)
    sel = H[H.within(D)]
    rows = [{"hotspot_id": hid, "region": "CITY", "adm": "CITY", "catchment": "ADM City", "area": r["area"], "location": r["location"], "criticality": r["criticality"],
             "x": round(r.geometry.x, 2), "y": round(r.geometry.y, 2)} for hid, r in sel.set_index("hotspot_id").iterrows()]
    (CASES / "hotspots_ad.json").write_text(json.dumps({"crs": "EPSG:32640", "regions": {"CITY": reg}, "hotspots": rows}, ensure_ascii=False, indent=0), encoding="utf-8")
    tab = chr(9)
    lines = [tab.join([t, "CITY", DIR, str(RES_M), f"{km2:.2f}", str(len(rows)), str(s)]) for t, s in PLAN.items()]
    (CASES / "adm250_all.tsv").write_text(chr(10).join(lines) + chr(10), encoding="utf-8", newline=chr(10))
    n_pumps = len(pumps) if hasattr(pumps, "__len__") else "?"
    print(f"积水点 {len(rows)} 个；塘 {len(ponds)} 座；泵站 {n_pumps}；任务 {len(PLAN)} 个 → {CASES / 'adm250_all.tsv'}")
    for s in (1, 2, 3):
        print(f"服务器{s}：{' '.join(t for t, v in PLAN.items() if v == s)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
