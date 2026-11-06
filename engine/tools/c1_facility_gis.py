# -*- coding: utf-8 -*-
"""C1 交付：各态的设施空间图层（用户 2026-09-29：结果文件里没有蓄水池的空间图层——原来只导了 设施.csv）。
每个态的目录下建 设施GIS/，放这个态里在用的全部设施（含从上一态继承的：L2+planned 含 L1 现状泵站，L2+suggested 含 L1 + L2+planned）：
  蓄水池_规划塘       面   规划塘占地轮廓；库容、占地、塘深（1 m 围堤，参数 D14）、塘底高程；本态 6 h 末满度 / 蓄水 / 地表进塘 / 泵打入 / 塘满溢出
  蓄水池_箱涵         点   L2+suggested 的箱涵（一维蓄水节点，没有设计占地，点在接入的井上）；容积、最大蓄水、最大满度
  泵站与泵点          点   现状泵站 / OP-02 移动泵 / Parsons 泵站 / 转输泵 / 地面取水泵；台数、流量、开停泵水深、来源；本态抽水量、运行时间占比、启停次数
  排水去向            线   泵点 → 排到的位置（塘 / 接收井 / 出水口）
  地面取水片          面   地面取水泵的取水范围（L2+suggested）
格式：GeoJSON（WGS84）+ Shapefile（UTM 40N，属性 UTF-8）。数据：console/data/flood2d_c1_r{r}/c1_stats.json 的 facilities（tools/c1_facilities.py）
用法：python tools/c1_facility_gis.py
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FOLDER = {"L1": "L1_现状管网+现状泵站", "L2": "L2_加OP-02移动泵", "L2P": "L2+planned_Parsons方案", "L2S": "L2+suggested_转输与地面取水泵",
          "L2S3": "L2+suggested_v3_按塘余量分配"}
CHAIN = {"L1": ["L1"], "L2": ["L1", "L2"], "L2P": ["L1", "L2P"], "L2S": ["L1", "L2P", "L2S"], "L2S3": ["L1", "L2P", "L2S3"]}
KIND = {"ps_l1": "现状泵站", "mobile": "OP-02 移动泵", "ps_planned": "Parsons 泵站", "conv": "转输泵", "sump": "地面取水泵", "box": "箱涵", "pond": "规划塘"}
LAB = {"L1": "L1", "L2": "L2", "L2P": "L2+planned", "L2S": "L2+suggested", "L2S3": "L2+suggested v3"}


# Shapefile 字段名最长 10 字节（中文会被截成乱码）：Shapefile 用英文短名，GeoJSON 保留中文；对照表写进 说明.md
SHP = {"编号": "id", "类型": "kind", "所属态": "src_state", "本态": "state", "库容_m3": "cap_m3", "占地_ha": "area_ha", "塘深_m": "depth_m",
       "塘底高程_m": "floor_m", "末满度_pct": "fill_pct", "末蓄水_m3": "end_m3", "地表进塘_m3": "surf_m3", "泵打入_m3": "pumpin_m3",
       "溢出_m3": "ovflow_m3", "说明": "note", "容积_m3": "vol_m3", "接入井": "node", "最大蓄水_m3": "max_m3", "最大满度_pct": "maxfil_pct",
       "流量_Ls": "q_lps", "台数": "n_pumps", "单泵_Ls": "q_each", "开泵_m": "on_m", "停泵_m": "off_m", "流量来源": "q_src", "排到": "to",
       "本态抽水_m3": "pumped_m3", "运行时间_pct": "util_pct", "启停次数": "starts", "积水片_ha": "patch_ha", "最大积水_m3": "peak_m3"}


def write(gdf, d, name):
    import warnings
    if gdf is None or not len(gdf):
        return 0
    d.mkdir(parents=True, exist_ok=True)
    gdf.to_file(d / f"{name}.geojson", driver="GeoJSON")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gdf.rename(columns=SHP).to_crs(32640).to_file(d / f"{name}.shp", driver="ESRI Shapefile", encoding="utf-8")
    (d / f"{name}.cpg").write_text("UTF-8", encoding="ascii")
    return len(gdf)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    from shapely.geometry import LineString, Point, Polygon, MultiPolygon
    for tag in ("R61", "R96", "U61", "D24", "M61"):           # U61 = 客户单元版；D24 = 设计暴雨（2026-09-29）
        r = 61 if tag.startswith("D") else int(tag[1:])
        f0 = ROOT / "console" / "data" / f"flood2d_c1_{tag.lower()}" / "c1_stats.json"
        if not f0.exists():
            continue
        cs = json.loads(f0.read_text(encoding="utf-8"))
        F = cs.get("facilities", {}); have = cs["states"]
        sumps = {}
        for st in ("L2S", "L2S3"):
            f = ROOT / "out" / f"c1_{st}_inputs_{tag}.json"
            if f.exists():
                sumps[st] = {m["id"]: m for m in json.loads(f.read_text(encoding="utf-8"))["measures"]}
        base = ROOT / "out" / "交付" / (f"C1_客户单元_{r}mm2h" if tag.startswith("U") else f"C1_模型版_{r}mm2h" if tag.startswith("M") else "C1_设计暴雨_61.34mm24h" if tag.startswith("D") else f"C1_{r}mm2h")
        for st in have:
            if st not in FOLDER or not (base / FOLDER[st]).exists():
                continue
            d = base / FOLDER[st] / "设施GIS"
            pond_rows, pond_g, box_rows, box_g, pt_rows, pt_g, ln_rows, ln_g, sp_rows, sp_g = [], [], [], [], [], [], [], [], [], []
            for src in CHAIN[st]:
                for f in F.get(src, []):
                    k = f["kind"]; S = (f.get("sim") or {}).get(st) or {}; P = f.get("p") or {}
                    common = {"编号": f["id"], "类型": KIND.get(k, k), "所属态": LAB[src], "本态": LAB[st]}
                    if k == "pond":
                        polys = [Polygon([(lo, la) for la, lo in ring]) for ring in (f.get("poly") or [])]
                        geom = polys[0] if len(polys) == 1 else MultiPolygon(polys) if polys else Point(f["ll"][1], f["ll"][0])
                        pond_rows.append({**common, "库容_m3": P.get("vol"), "占地_ha": P.get("area"), "塘深_m": P.get("depth"), "塘底高程_m": P.get("floor"),
                                          "末满度_pct": S.get("fill_pct"), "末蓄水_m3": S.get("end_m3"), "地表进塘_m3": S.get("surface_m3"),
                                          "泵打入_m3": S.get("pumped_in_m3"), "溢出_m3": S.get("overflow_m3"), "说明": "Parsons 规划塘；1 m 围堤、塘底平于地面最低点（参数 D14）"})
                        pond_g.append(geom); continue
                    if k == "box":
                        box_rows.append({**common, "容积_m3": f.get("vol_m3"), "接入井": P.get("node"), "最大蓄水_m3": S.get("max_m3"), "最大满度_pct": S.get("max_pct"),
                                         "说明": "一维蓄水节点，没有设计占地；点在接入的井上。" + str(P.get("why") or "")[:180]})
                        box_g.append(Point(f["ll"][1], f["ll"][0])); continue
                    n = P.get("n"); qe = P.get("q_each")
                    pt_rows.append({**common, "流量_Ls": f.get("q_lps"), "台数": n, "单泵_Ls": qe, "开泵_m": P.get("on"), "停泵_m": P.get("off"),
                                    "流量来源": P.get("src"), "排到": P.get("to") or f.get("how"), "本态抽水_m3": S.get("vol_m3"), "运行时间_pct": S.get("util_pct"),
                                    "启停次数": S.get("starts"), "说明": str(P.get("why") or f.get("how") or "")[:200]})
                    pt_g.append(Point(f["ll"][1], f["ll"][0]))
                    if f.get("to_ll"):
                        ln_rows.append({**common, "流量_Ls": f.get("q_lps"), "排到": P.get("to") or f.get("how")})
                        ln_g.append(LineString([(f["ll"][1], f["ll"][0]), (f["to_ll"][1], f["to_ll"][0])]))
                    if k == "sump" and src in sumps and f["id"] in sumps[src] and sumps[src][f["id"]].get("sump_polygon"):
                        m = sumps[src][f["id"]]
                        sp_rows.append({**common, "流量_Ls": f.get("q_lps"), "积水片_ha": m.get("area_ha"), "最大积水_m3": m.get("peak_vol_m3"), "排到": P.get("to")})
                        sp_g.append(gpd.GeoSeries([Polygon(m["sump_polygon"])], crs=32640).to_crs(4326).iloc[0])
            mk = lambda rows, g: gpd.GeoDataFrame(rows, geometry=g, crs=4326) if rows else None
            n = [write(mk(pond_rows, pond_g), d, "蓄水池_规划塘"), write(mk(box_rows, box_g), d, "蓄水池_箱涵"), write(mk(pt_rows, pt_g), d, "泵站与泵点"),
                 write(mk(ln_rows, ln_g), d, "排水去向"), write(mk(sp_rows, sp_g), d, "地面取水片")]
            if any(n):
                (d / "说明.md").write_text(
                    f"# {LAB[st]} · {r} mm / 2 h · 设施空间图层\n\n这个态里在用的全部设施，含从上一态继承的（{' + '.join(LAB[x] for x in CHAIN[st])}）。"
                    "GeoJSON 是 WGS84，Shapefile 是 UTM 40N（EPSG:32640，属性 UTF-8）。「本态」开头的字段是这一场雨、这个态的计算结果（6 h）。\n\n"
                    f"| 图层 | 几何 | 个数 |\n|---|---|---|\n| 蓄水池_规划塘 | 面（规划塘占地） | {n[0]} |\n| 蓄水池_箱涵 | 点（接入井；一维节点，无设计占地） | {n[1]} |\n"
                    f"| 泵站与泵点 | 点 | {n[2]} |\n| 排水去向 | 线（泵点 → 排到的位置） | {n[3]} |\n| 地面取水片 | 面 | {n[4]} |\n\n"
                    "C1 计算域内没有已建成的塘 / 蓄水构筑物，所以 L1、L2 没有蓄水池图层。\n\n"
                    "Shapefile 字段名最长 10 个字符，用英文短名；GeoJSON 用中文字段名。对照：\n\n| GeoJSON | Shapefile |\n|---|---|\n"
                    + "".join(f"| {k} | {v} |\n" for k, v in SHP.items()), encoding="utf-8")
            print(f"{tag} {LAB[st]:<16} 塘 {n[0]:>2} · 箱涵 {n[1]} · 泵点 {n[2]:>2} · 去向线 {n[3]:>2} · 取水片 {n[4]:>2} → {d.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
