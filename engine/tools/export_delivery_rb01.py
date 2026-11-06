# -*- coding: utf-8 -*-
"""RB-01 一二维四态交付目录（结构同 C1：export_delivery_c1.py）。

out/交付/RB01_60mm1h/
  readme.md  统计值/（rb01_stats_*.csv / .json）
  公共/  计算域.geojson  二维地形_5mDTM原值.tif  6个热点.geojson
  <态>/  一二维/（.sww、.npz、耦合运行记录.json、积水面积时序.csv、耦合的一维 .inp / .rpt、GIS 最大水深 + 逐帧 GeoTIFF）
         设施GIS/（泵、塘、重力管、地面取水片：GeoJSON WGS84）
         积水点/（二维模拟积水点 SIM-态-序号：CSV + GeoJSON，两档水深阈值；说明见顶层 积水点说明.md）
         飞渡DTS/（DTS Cloud h2d：shp/ 三角网 + dat/ 逐时刻水深流速；说明见顶层 飞渡DTS说明.md）
用法：python tools/export_delivery_rb01.py [--tag H61] [--no-dts]
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from rb01_build import HS, domain, load  # noqa: E402
from rb01_post import NAMES, STATES  # noqa: E402
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
DIRN = {"L1": "L1_现状", "L2": "L2_移动泵", "L2P": "L2+planned_PO塘方案(来源待确认)", "L2S4": "L2+suggested_v4中间步", "L2S5": "L2+suggested_v5"}


def gj(feats, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False), encoding="utf-8")


def readme(tag: str, name: str) -> str:
    """交付 readme：设置 + 结果（统计值）+ 报告与系统。数字都从这次运行的输入清单与统计值取。"""
    from rb01_build import delivery_dir, run_info
    O = ROOT / "out"
    I1 = run_info(tag)
    I2 = json.loads((O / f"rb01_inputs_{tag}_L2.json").read_text(encoding="utf-8"))
    IP = json.loads((O / f"rb01_inputs_{tag}_L2P.json").read_text(encoding="utf-8"))
    S = json.loads((O / f"rb01_stats_{tag}.json").read_text(encoding="utf-8"))["states"]
    n = lambda v, d=0: "—" if v is None else f"{v:,.{d}f}"   # noqa: E731
    sk = "；".join(f"{x['hotspot']} {x['why']}" for x in I2.get("skipped") or [])
    L = [f"# RB-01 汇水区 · {I1['rain_mm']:g} mm / {I1['dur_h']:g} h · 一二维耦合四态", "",
         "数据只用 Parsons 导出的 RB-01 模型（Parsons_Model Data Export/26092026/RB-01，改造后模型）+ 客户 5 m DTM。",
         f"计算：SWMM 5.2（一维）+ ANUGA（二维，15 m 非结构网，单元 ≤ 75 m²，DTM 原值），一维 30 s、交换 60 s，模拟 {I1['sim_h']:g} h，每 5 分钟一帧。",
         f"运行名 {name}；建模：tools/rb01_build.py、rb01_design_l2s.py；统计：tools/rb01_post.py；交付：tools/export_delivery_rb01.py。", ""]
    if tag == "H61":
        L += ["> 2026-10-19 更正：RB-01 应按 61 mm / 1 h 计算，之前的 `RB01_60mm1h`（60 mm）是笔误，以本目录为准。", ""]
    L += ["## 设置",
          f"- 计算域：RB-01 子汇水区并集 20 m 闭运算、填洞 → {I1['domain_km2']:.2f} km²；封闭汇水区，边界自由出流。",
          f"- 产流：{I1['catch_n']:,} 个子汇水区逐个 SCS CN + SCS 单位线，面积加权 CN {I1['cn_aw']}，净雨 {I1['excess_mm_aw']} mm，入流 {I1['runoff_m3']:,} m³。",
          f"- 雨：客户 Mussafah 模型「10 年一遇含 CC 1 h」雨型（21.2 mm）等比放大到 {I1['rain_mm']:g} mm。",
          "- 出水口：RBW3 两口，固定尾水 1.3 m、拍门（模型原设置）。",
          "- L1 = 改造后模型去掉塘 / 泵 / 压力管 / 湿井 / O- 出水口；与客户 GDB 对不上的约 19 km 重力管保留在 L1 里，是否 Parsons 新加无法区分。",
          f"- L2 = L1 + 热点表 planA 移动泵（{len(I2.get('pumps') or [])} 台" + (f"；未布：{sk}" if sk else "") + "）。",
          f"- L2+planned = PO 塘方案（来源待确认）原样：{len(IP.get('ponds') or [])} 个塘（塘底 = 轮廓内 DTM 最低点、1.0 m 深）、{len(IP.get('pumps') or [])} 台泵。",
          "- L2+suggested v5 = C1 v5 规则：先按 L2+planned 残余积水做 v4（地面取水泵 + 截流泵进塘），再把能重力自流的截流井换成「重力管 + 低处新塘」。", "",
          "## 结果（积水面积不含塘占地；热点环 = 热点 200 m 内）", "见 统计值/统计值.csv、结果图/。", "",
          f"| 态 | ≥0.30 m ha | ≥0.10 m ha | 最大积水 m³ | {I1['sim_h']:g} h 末积水 m³ | 一维溢流 m³ | 比 L2+planned 变深 ≥5 cm ha |", "|---|---|---|---|---|---|---|"]
    for s, v in S.items():
        L.append(f"| {v['name']} | {v['area_ge030_ha']} | {v['area_ge010_ha']} | {n(v['max_vol_m3'])} | {n(v['end_vol_m3'])} | {n(v['rpt']['flooding_m3'])} | "
                 + (n(v['vs_L2P']['deeper_ge005_ha'], 2) if "vs_L2P" in v else "—") + " |")
    hs = list(next(iter(S.values()))["hs"])
    L += ["", "热点 200 m 环 ≥0.15 m 面积×时间（ha·h）：", "", "| 态 | " + " | ".join(hs) + " |", "|---|" + "---|" * len(hs)]
    L += [f"| {v['name']} | " + " | ".join(str(v["hs"][h]["ge015_ha_h"]) for h in hs) + " |" for v in S.values()]
    L += ["", "## 报告与系统", "- 离线报告：RB01_报告.html（单文件，图片内嵌，不联网可开）。",
          f"- 系统：防线2+ 多态对比 → RB-01（数据在 console/data/flood2d_rb01_{tag.lower()}/）。"]
    return "\n".join(L) + "\n"


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", default="H61")
    ap.add_argument("--no-dts", action="store_true", help="不出飞渡DTS（每个态约 410 MB、20 s）"); a = ap.parse_args(argv)
    import geopandas as gpd
    from pyproj import Transformer
    from shapely.geometry import LineString, Point, mapping, shape
    from shapely.ops import transform
    tr = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    W = lambda g: mapping(transform(lambda x, y, z=None: tr.transform(x, y), g))   # noqa: E731
    name = f"RB01_{a.tag}"
    from rb01_build import delivery_dir
    out = delivery_dir(a.tag)                       # RB01_{雨量}mm{历时}h，随运行走（2026-10-19：60 mm 是笔误，改 61 mm 重算 → RB01_61mm1h）
    out.mkdir(parents=True, exist_ok=True)
    L = load(); D = domain(L["ca"], log=lambda *x: None)
    gj([{"type": "Feature", "properties": {"name": "RB-01 计算域", "km2": round(D.area / 1e6, 2)}, "geometry": W(D)}], out / "公共" / "计算域.geojson")
    shutil.copy2(C2D / f"dem_{name}.tif", out / "公共" / "二维地形_5mDTM原值.tif")
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson"); H = H[H.hotspot_id.isin(HS)]
    H[["hotspot_id", "criticality", "planA_pumps", "geometry"]].to_file(out / "公共" / "6个热点.geojson", driver="GeoJSON")
    st_dir = out / "统计值"; st_dir.mkdir(exist_ok=True)
    for ext in ("json", "csv"):
        shutil.copy2(ROOT / "out" / f"rb01_stats_{a.tag}.{ext}", st_dir / f"统计值.{ext}")
    nxy = {}
    for s in STATES:
        if not (C2D / f"twoway_{name}_{s}.json").exists():
            continue
        d = out / DIRN[s]; e = d / "一二维"; (e / "GIS").mkdir(parents=True, exist_ok=True); (e / "耦合的一维").mkdir(exist_ok=True)
        for src, dst in ((WORK / f"{name}_{s}.inp", e / "耦合的一维" / f"{name}_{s}.inp"), (C2D / f"{name}_{s}_2way.rpt", e / "耦合的一维" / f"{name}_{s}.rpt"),
                         (C2D / "anuga_out" / f"{name}_{s}.sww", e / f"{name}_{s}_二维水深与流速时序.sww"),
                         (C2D / f"{name}_{s}_2d.npz", e / f"{name}_{s}_二维水深帧与最大水深.npz"),
                         (C2D / f"twoway_{name}_{s}.json", e / f"{name}_{s}_耦合运行记录.json"),
                         (C2D / "tif" / f"{name}_{s}_depth_max.tif", e / "GIS" / f"{name}_{s}_全程最大水深.tif"),
                         (C2D / "tif" / f"{name}_{s}_depth_frames.tif", e / "GIS" / f"{name}_{s}_逐帧水深_5min.tif"),
                         (C2D / "tif" / f"{name}_{s}_depth_frames.json", e / "GIS" / f"{name}_{s}_逐帧水深_5min_波段时刻.json")):
            if src.exists():
                shutil.copy2(src, dst)
            else:
                print(f"  ! 缺 {src.name}")
        tw = json.loads((C2D / f"twoway_{name}_{s}.json").read_text(encoding="utf-8"))
        with open(e / f"{name}_{s}_积水面积时序.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(["时刻 h", "二维域内水量 m³", "淹没面积（>2 cm）ha", "最大水深 m"])
            for r in tw["series"]:
                w.writerow([round(r["t_h"], 3), round(r["vol_m3"]), round(r["wet_m2"] / 1e4, 2), round(r["hmax_m"], 3)])
        # 设施 GIS
        if not nxy:
            for ln_ in open(WORK / f"{name}_L2P.inp", encoding="utf-8"):
                pass
        S, cur = {}, None
        for ln_ in open(WORK / f"{name}_{s}.inp", encoding="utf-8"):
            t = ln_.strip()
            if t.startswith("["):
                cur = t; continue
            if cur == "[COORDINATES]" and t:
                p = t.split(); S[p[0]] = (float(p[1]), float(p[2]))
        meta = json.loads((C2D / f"pilot_{name}_{s}.json").read_text(encoding="utf-8"))
        feats = []
        for p in meta.get("pumps_1d", []) + meta.get("pumps_l2", []):
            a_, b_ = S.get(p["from"]), S.get(p["to"])
            g = LineString([a_, b_]) if a_ and b_ and a_ != b_ else Point(a_)
            feats.append({"type": "Feature", "properties": {"id": p["id"], "q_lps": round(p["q_m3s"] * 1000, 1), "from": p["from"], "to": p["to"],
                                                            "on_m": p.get("on"), "off_m": p.get("off"), "how": p.get("how")}, "geometry": W(g)})
        if feats:
            gj(feats, d / "设施GIS" / "泵（吸水井→排放点）.geojson")
        if meta.get("ponds"):
            gj([{"type": "Feature", "properties": {"node": p["node"], "vol_m3": round(p["vol"]), "new_v5": bool(p.get("new_v5"))}, "geometry": W(shape(p["footprint"]))}
                for p in meta["ponds"]], d / "设施GIS" / "塘.geojson")
        if meta.get("pumps2d"):
            from shapely.geometry import Polygon
            gj([{"type": "Feature", "properties": {"id": q["id"], "q_m3s": q["q_m3s"], "to_pond": q.get("to_pond")}, "geometry": W(Polygon(q["sump_polygon"]))}
                for q in meta["pumps2d"]], d / "设施GIS" / "地面取水片.geojson")
        f5 = ROOT / "out" / f"rb01_{s}_inputs_{a.tag}.json"
        if s == "L2S5" and f5.exists():
            info = json.loads(f5.read_text(encoding="utf-8"))
            gj([{"type": "Feature", "properties": {k: q[k] for k in ("id", "from", "to", "L_m", "d_m", "z_in")},
                 "geometry": W(LineString([S[q["from"]], S[q["to"]]]))} for q in info["pipes"]], d / "设施GIS" / "重力管（井→新塘）.geojson")
            info2 = {k: v for k, v in info.items()}
            for p in info2["ponds"]:
                p.pop("footprint", None)
            (d / "措施清单.json").write_text(json.dumps(info2, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
        elif f5.exists() or (ROOT / "out" / f"rb01_inputs_{a.tag}_{s}.json").exists():
            src = f5 if f5.exists() else ROOT / "out" / f"rb01_inputs_{a.tag}_{s}.json"
            shutil.copy2(src, d / "建模输入清单.json")
        print(f"  {s} → {d.name}")
    # 结果图（rb01_figs.py 的产物；H60 那次是手拷的）
    figs = ROOT / "out" / f"rb01_figs_{a.tag.lower()}"
    if figs.exists():
        (out / "结果图").mkdir(exist_ok=True)
        for f in figs.glob("*.png"):
            shutil.copy2(f, out / "结果图" / f.name)
    # readme（H60 那次手写；这里按统计值生成，积水点 / 飞渡DTS 两节随后整节写入）
    (out / "readme.md").write_text(readme(a.tag, name), encoding="utf-8")
    # 二维模拟积水点（2026-10-18 用户：交付目录要带上、以后导出自动带）：<态>/积水点/ + 积水点说明.md + readme 一节
    sys.path.insert(0, str(ROOT))
    from swbrain import ponding
    have = {s: out / DIRN[s] for s in STATES if (C2D / f"{name}_{s}_2d.npz").exists() and (out / DIRN[s]).exists()}
    files = ponding.export_delivery(name, have, out)
    ponding.upsert_readme(out / "readme.md", ponding.readme_section({NAMES[s]: DIRN[s] for s in have}))
    print(f"  积水点：{len(have)} 个态、{len(files)} 个文件")
    # 飞渡 DTS（2026-10-18 用户：每次结果都要有给飞渡 DTS 的文件夹）：<态>/飞渡DTS/（shp + dat，不出 CSV）+ 飞渡DTS说明.md + readme 一节
    if not a.no_dts:
        from swbrain import dts_h2d
        rows = dts_h2d.export_delivery(name, C2D / "anuga_out", {s: out / DIRN[s] for s in have}, out, names=NAMES)
        done_st = [s for s in have if (out / DIRN[s] / dts_h2d.DTS_DIR).exists()]
        ponding.upsert_readme(out / "readme.md", dts_h2d.readme_section({NAMES[s]: DIRN[s] for s in done_st}), mark=dts_h2d.DTS_MARK)
        print(f"  飞渡DTS：{len(rows)} 个态")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
