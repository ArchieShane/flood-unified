# -*- coding: utf-8 -*-
"""RD-A 单场雨的全套交付（2026-10-26 用户：61 mm / 1 h 的一二维结果之外，还要积水点和 L2+suggested 设施信息的全套）。

每个态一个文件夹（L1 现状 / L2 雨前预置泵 / L2+planned 客户计划 / L2+suggested 我们的方案）：
  一二维/   .sww（飞渡 DTS 导出时已生成，这里不重做）、.npz、SWMM .inp / 耦合 .rpt、耦合记录 twoway / pilot json
  水深栅格/ 最大水深与逐帧水深 GeoTIFF（tools/npz_to_tif.py 先跑，读 $WORK/c2d/tif）
  积水点/   swbrain.ponding.export_delivery 两档阈值（0.15 / 0.30 m），另加：评估剔除区标记、L3 承接与派单（console l3.json）
  设施/     本态全部设施（逐级累加：L1 ⊂ L2 ⊂ L2+planned ⊂ L2+suggested）GeoJSON + 清单；L2+suggested 另附台账、取水口、合规检查
  飞渡DTS/  已有（swbrain.dts_h2d.export_delivery），不动
公共/：计算域、客户 11 个热点、DTM、评估剔除区、降雨过程、判据评估结果。根目录 README.md。

只读计算结果，不改任何计算。
用法：python tools/export_delivery_rda.py S61D1 --out "out/交付/RDA_L2S_v3_20261025/RDA_61mm1h_全套成果"
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from workdir import WORK  # noqa: E402
from swbrain import ponding  # noqa: E402

C2D = WORK / "c2d"
OPT = ROOT / "out/l2opt/RDA_96C"
PKG = ROOT / "out/交付/RDA_L2S_v3_20261025"
NM = {"L1": "L1_现状", "L2": "L2_雨前预置泵", "L2P": "L2+planned_客户计划", "OPTCS2": "L2+suggested_我们的方案"}
CHAIN = {"L1": ["L1"], "L2": ["L1", "L2"], "L2P": ["L1", "L2", "L2P"], "OPTCS2": ["L1", "L2", "L2P", "OPTCS2"]}
LAYER = {"L1": "L1 现状", "L2": "L2 雨前预置", "L2P": "L2+planned 客户计划", "OPTCS2": "L2+suggested 新增"}
KIND = {"ps_l1": "现状泵站", "mobile": "雨前预置移动泵", "ps_planned": "客户计划泵站", "tank": "地下调蓄池",
        "gpipe": "重力管（截流井→调蓄池）", "conv": "压力输水（泵→排口）", "sump": "地面取水泵站"}
CRS84 = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}


def fc(name, feats):
    return {"type": "FeatureCollection", "name": name, "crs": CRS84, "features": feats}


def wjson(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def wcsv(p, head, rows):
    with open(p, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); w.writerow(head); w.writerows(rows)


def lonlat(ll):
    return [ll[1], ll[0]]


def cp(src, dst):
    src = pathlib.Path(src)
    if not src.exists():
        print("  缺：", src); return False
    dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst); return True


# ---------- 一二维 + 水深栅格 ----------
def model_files(run, st, d):
    o = d / "一二维"; o.mkdir(parents=True, exist_ok=True)
    tag = f"{run}_{st}"
    cp(C2D / f"{tag}_2d.npz", o / f"{tag}_二维结果.npz")
    cp(WORK / f"{tag}.inp", o / f"{tag}_SWMM一维管网.inp")
    cp(C2D / f"{tag}_2way.rpt", o / f"{tag}_一二维耦合_SWMM报告.rpt")
    cp(C2D / f"twoway_{tag}.json", o / f"{tag}_耦合交换记录.json")
    cp(C2D / f"pilot_{tag}.json", o / f"{tag}_二维设置.json")
    t = d / "水深栅格"; t.mkdir(parents=True, exist_ok=True)
    for sfx, zh in (("depth_max.tif", "最大水深.tif"), ("depth_frames.tif", "逐帧水深.tif"), ("depth_frames.json", "逐帧水深_波段时刻.json")):
        cp(C2D / "tif" / f"{tag}_{sfx}", t / f"{tag}_{zh}")


# ---------- 积水点附加：剔除区标记 + L3 ----------
def exclude_zones():
    """评估剔除区：[(多边形, 类别：原因)]。exclude_OPTCS.geojson 本身就是 UTM 40N 坐标（文件里没写 crs），不转换。"""
    from shapely.geometry import shape
    g = json.loads((OPT / "exclude_OPTCS.geojson").read_text(encoding="utf-8"))
    return [(shape(f["geometry"]), f"{f['properties'].get('class', '')}：{f['properties'].get('why', '')}") for f in g["features"]]


def ponding_extra(st, d, L3, EX):
    """积水点的两个补充：
    ① 评估剔除区标记：积水片和剔除区重叠多少、扣掉后还剩多少（< 100 m² 的片判据评估和 L3 都不算）；
    ② L3 承接与派单：rda_l3 先扣剔除区再判积水片，编号会和本交付错开，这里按位置对回本交付的编号（0.15 m 档），L3 自己的编号另列一栏。"""
    import shapely
    from shapely.geometry import Point, shape
    from pyproj import Transformer
    T = Transformer.from_crs(4326, 32640, always_xy=True).transform
    import shapely.ops
    o = d / "积水点"; ezu = shapely.union_all([z for z, _ in EX]) if EX else None
    patches = {}
    for dep in ponding.DELIVERY_DEPTHS:
        sfx = f"d{dep:.2f}"
        g = json.loads((o / f"ponding_areas_{sfx}.geojson").read_text(encoding="utf-8"))
        rows = []
        for f in g["features"]:
            pg = shapely.ops.transform(T, shape(f["geometry"])); a0 = pg.area
            ov = pg.intersection(ezu).area if ezu is not None else 0.0
            why = "；".join(w for z, w in EX if z.intersects(pg))
            rest = a0 - ov
            flag = "否" if ov < 1 else ("是（整片剔除）" if rest < ponding.DEFAULTS["min_area"] else "部分重叠")
            rows.append([f["properties"]["id"], f["properties"]["lon"], f["properties"]["lat"], round(a0), round(ov), round(rest), flag, why])
            if dep == 0.15:
                patches[f["properties"]["id"]] = (pg, flag, f["properties"]["vol_peak_m3"])
        wcsv(o / f"积水点_评估剔除区标记_{sfx}.csv", ["积水点", "经度", "纬度", "积水片面积 m²", "与剔除区重叠 m²", "扣除后 m²", "落在评估剔除区", "剔除区说明"], rows)
    S = L3["states"].get(st)
    if not S:
        return
    pts = S["points"]; D = {k: S["dispatch"][k]["points"] for k in ("island", "adm")}
    m, used = {}, set()
    for p in pts:                                        # L3 点（扣剔除区后的片的最深点）对回交付积水片：先看哪片真包含它，再看距离，再看积水量接近
        q = Point(T(p["lon"], p["lat"]))
        k = min(patches, key=lambda k: (round(patches[k][0].distance(q) / 5), abs(patches[k][2] - p["vol_peak_m3"]) / max(p["vol_peak_m3"], 1)))
        m[p["id"]] = k; used.add(k)
    from collections import Counter
    dup = {k: [i for i, v in m.items() if v == k] for k, n in Counter(m.values()).items() if n > 1}
    if dup:                                              # L3 用近似邻接、本交付用真实网格邻接，个别小片划分不同
        print(f"  {st}：多个 L3 点落在同一交付积水片", dup)
    head = ["积水点", "L3 内部编号", "经度", "纬度", "后果等级", "定级依据", "最大水深 m", "峰值积水量 m³", "峰值时刻 h", "200 m 内客户热点", "热点重要度",
            "300 m 内重点单位", "退干期限 h", "自然退干 h", "≥0.15 m 路面 m²", "≥0.30 m 路面 m²", "≥0.15 m 碰到建筑", "≥0.30 m 碰到建筑", "变电站",
            "L3 手段", "结论", "Island A13：结果", "Island A13：台/辆", "Island A13：到位 h", "Island A13：完成 h",
            "ADM 全部驻地：结果", "ADM 全部驻地：台/辆", "ADM 全部驻地：到位 h", "ADM 全部驻地：完成 h", "派车终点平移 m", "备注"]
    rows = []
    for p in pts:
        r = [m[p["id"]], p["id"], p["lon"], p["lat"], p.get("level"), p.get("level_why"), p.get("max_depth_m"), p.get("vol_peak_m3"), p.get("t_peak_h"),
             p.get("hotspots_200m"), p.get("criticality"), p.get("top_kind"), p.get("deadline_h"), p.get("t_dry_nat_h"),
             p.get("road15_m2"), p.get("road30_m2"), p.get("bldg15"), p.get("bldg30"), "是" if p.get("subst") else "否",
             p.get("l3_means"), p.get("verdict")]
        for k in ("island", "adm"):
            q = D[k].get(p["id"]) or {}
            r += [q.get("result", "不派（无暴露，只监测）" if p.get("level") == "无暴露" else ""), q.get("units", ""), q.get("arrive_h", ""), q.get("done_h", "")]
        rows.append(r + [p.get("road_snap_m"), "与 " + "、".join(i for i in dup[m[p["id"]]] if i != p["id"]) + " 同在一片（L3 按近似邻接拆成两片）" if m[p["id"]] in dup else ""])
    rows.sort(key=lambda r: (int(r[0].rsplit("-", 1)[1]), int(r[1].rsplit("-", 1)[1])))
    nol3 = [k for k in patches if k not in used]

    def partner(k):
        pg = patches[k][0]
        return min(pts, key=lambda p: pg.distance(Point(T(p["lon"], p["lat"]))))
    for k in nol3:
        if patches[k][1] == "否":
            for r in rows:
                if r[1] == partner(k)["id"]:
                    r[-1] = (r[-1] + "；" if r[-1] else "") + f"L3 把 {k} 也算进这片（近似邻接），积水量含 {k}"
    wcsv(o / "L3承接与派单.csv", head, rows)
    if nol3:
        def why(k):
            if patches[k][1] != "否":
                return "落在评估剔除区（DTM 填 0 / 私人地块坑），扣除后不足 100 m²，判据评估和 L3 都不算"
            near = partner(k)
            return (f"系统 L3 按近似邻接判片，把这片和相邻的 {m[near['id']]} 算作同一片（L3 编号 {near['id']}，积水量已合在一起）；"
                    f"本交付按真实三角网邻接分成两片")
        wcsv(o / "未进入L3清单的积水点.csv", ["积水点", "原因"], [[k, why(k)] for k in nol3])
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
              "properties": {"积水点": m[p["id"]], "l3_id": p["id"], **{k: v for k, v in p.items() if k not in ("lon", "lat", "id")},
                             "island_result": (D["island"].get(p["id"]) or {}).get("result"), "adm_result": (D["adm"].get(p["id"]) or {}).get("result")}} for p in pts]
    wjson(o / "L3承接与派单.geojson", fc(f"RDA_{st}_l3", feats))
    wjson(o / "L3承接_汇总.json", {"summary": S["summary"], "dispatch": {k: {x: v for x, v in S["dispatch"][k].items() if x not in ("points", "lines")} for k in ("island", "adm")},
                                   "hotspot_links": S["hs_links"], "basis": L3["basis"]})


# ---------- 设施 ----------
def fac_feature(x, layer):
    pr = {"编号": x["id"], "类型": KIND.get(x["kind"], x["kind"]), "kind": x["kind"], "防线层级": layer,
          "流量 L/s": x.get("q_lps"), "池容 m³": x.get("vol_m3"), "说明": (x.get("p") or {}).get("why")}
    for k, v in (x.get("p") or {}).items():
        if k != "why" and not isinstance(v, (dict, list)):
            pr[f"p_{k}"] = v
    for s, v in (x.get("sim") or {}).items():
        for k2, v2 in (v or {}).items():
            pr[f"本场模拟_{k2}"] = v2
    if x.get("to_ll"):
        pr["排到 经度"], pr["排到 纬度"] = lonlat(x["to_ll"])
    if x.get("poly"):
        geom = {"type": "Polygon", "coordinates": [[lonlat(q) for q in x["poly"]]]}
    elif x.get("line"):
        geom = {"type": "LineString", "coordinates": [lonlat(q) for q in x["line"]]}
    else:
        geom = {"type": "Point", "coordinates": lonlat(x["ll"])}
    pr["经度"], pr["纬度"] = lonlat(x["ll"])
    return {"type": "Feature", "geometry": geom, "properties": pr}


def facilities(run, st, d, FAC):
    o = d / "设施"; o.mkdir(parents=True, exist_ok=True)
    feats = [fac_feature(x, LAYER[s]) for s in CHAIN[st] for x in FAC.get(s, [])]
    wjson(o / f"{run}_{st}_设施.geojson", fc(f"{run}_{st}_facilities", feats))
    # 排放连线（泵 → 排口）单独一层，便于 GIS 画箭头
    links = [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [lonlat(x["ll"]), lonlat(x["to_ll"])]},
              "properties": {"编号": x["id"], "类型": KIND.get(x["kind"], x["kind"]), "防线层级": LAYER[s]}}
             for s in CHAIN[st] for x in FAC.get(s, []) if x.get("to_ll")]
    wjson(o / f"{run}_{st}_排放连线.geojson", fc(f"{run}_{st}_discharge_links", links))
    head = ["防线层级", "编号", "类型", "经度", "纬度", "流量 L/s", "池容 m³", "排到 经度", "排到 纬度", "本场模拟", "说明"]
    rows = []
    for s in CHAIN[st]:
        for x in FAC.get(s, []):
            to = lonlat(x["to_ll"]) if x.get("to_ll") else ["", ""]
            sim = "; ".join(f"{k2}={v2}" for v in (x.get("sim") or {}).values() for k2, v2 in (v or {}).items())
            rows.append([LAYER[s], x["id"], KIND.get(x["kind"], x["kind"]), *lonlat(x["ll"]), x.get("q_lps", ""), x.get("vol_m3", ""), *to, sim, (x.get("p") or {}).get("why", "")])
    wcsv(o / f"{run}_{st}_设施清单.csv", head, rows)
    if st == "OPTCS2":
        import openpyxl
        wb = openpyxl.load_workbook(PKG / "RD-A_L2S成果_设施台账与效果.xlsx", read_only=True, data_only=True)
        for sh, fn in (("设施台账_L2S", "L2+suggested_设施台账（造价与设计参数）.csv"), ("地面取水口", "L2+suggested_地面取水口.csv"),
                       ("造价汇总", "L2+suggested_造价汇总.csv"), ("全寿命成本_NPV", "L2+suggested_全寿命成本_NPV.csv")):
            R = [list(r) for r in wb[sh].iter_rows(values_only=True)]
            wcsv(o / fn, R[0], [["" if v is None else v for v in r] for r in R[1:]])
        cp(OPT / "compliance.json", o / "L2+suggested_合规检查（WA-726 与空间约束）.json")
        cp(OPT / "sumps_OPTCS2.geojson", o / "L2+suggested_地面泵站与取水坑.geojson")
    return len(feats)


# ---------- 公共 ----------
def common(run, storm, out):
    o = out / "公共"; o.mkdir(parents=True, exist_ok=True)
    P = json.loads((C2D / f"pilot_{run}_L1.json").read_text(encoding="utf-8"))
    dom = P.get("domain_polygon")
    if dom:
        wjson(o / "计算域.geojson", fc("domain", [{"type": "Feature", "geometry": dom if "type" in dom else {"type": "Polygon", "coordinates": dom},
                                                     "properties": {"name": "RD-A 计算域"}}]))
    cp(C2D / f"dem_{run}.tif", o / f"{run}_DTM_二维计算用.tif")
    import shapely.ops
    from shapely.geometry import mapping, shape
    from pyproj import Transformer
    T = Transformer.from_crs(32640, 4326, always_xy=True).transform                  # 源文件是 UTM 40N 且没写 crs，交付统一转 WGS84
    g = json.loads((OPT / "exclude_OPTCS.geojson").read_text(encoding="utf-8"))
    wjson(o / "评估剔除区.geojson", fc("exclude_zones", [{"type": "Feature", "geometry": mapping(shapely.ops.transform(T, shape(f["geometry"]))),
                                                          "properties": f["properties"]} for f in g["features"]]))
    cp(OPT / f"eval2d_{storm}_ex.json", o / f"判据评估_{storm}.json")
    st = json.loads((ROOT / f"console/data/flood2d_rda_{storm.lower()}/rda_stats.json").read_text(encoding="utf-8"))
    h = st["hyeto"]
    wcsv(o / f"降雨过程_{storm}.csv", ["时刻 h", "雨强 mm/h", "累计雨量 mm", "累计净雨 mm"],
         [[a, b, c, e] for a, b, c, e in zip(h["t_h"], h["p_mmph"], h["p_cum_mm"], h["exc_cum_mm"])])
    L3 = json.loads((ROOT / f"console/data/flood2d_rda_{storm.lower()}/l3.json").read_text(encoding="utf-8"))
    G = json.loads((ROOT / "console/data/hotspots.geojson").read_text(encoding="utf-8"))           # 全市客户热点，取 RD-A 的 11 个
    wjson(o / "客户热点.geojson", fc("hotspots_RDA", [f for f in G["features"] if f["properties"]["hotspot_id"] in L3["hotspots"]]))
    return h, st


README = """# RD-A {label} 全套成果（{run}）

生成：{gen}　计算：SWMM 5.2 一维管网 + ANUGA 二维地表双向耦合（15 m 网格，65,210 个单元），模拟 {sim} h（雨后再算 10 h）。
降雨：{rain} mm / {dur} h，5 分钟峰值 {peak} mm/h（`公共/降雨过程_{storm}.csv`；雨型 = 客户 Mussafah 模型「10 年一遇含 CC」同历时雨型等比放大，不是 AECOM RD-A 模型的雨型）。

> **重要：这里的 L2+suggested 不是针对这场雨优化出来的。** 它是按 96 mm（1 h 与 24 h 两场）优化定下的方案（60 项措施，占位造价约 1.34 亿美元），
> 原样放进这场雨里做的一二维验证。四个状态的计算结果都是真实计算结果；但「针对 61 mm / 1 h 的最优方案」还没有——
> 按 AECOM 口径（AECOM「10 年 + 20%」1 h 设计雨 60.7 mm）的专门优化 2026-10-26 已开始计算，结果另行交付。

## 四个状态

| 文件夹 | 状态 | 设施图层要素（逐级累加，含管线和排放连线） |
|---|---|---|
{state_rows}

## 每个状态文件夹里有什么

| 子文件夹 | 内容 |
|---|---|
| `一二维/` | `.sww`（二维水深与流速时序，NetCDF，ANUGA 格式）、`_二维结果.npz`（单元形心坐标、面积、高程、最大水深 hmax、每 5 分钟水深帧、流速）、`.inp`（SWMM 一维管网）、`_一二维耦合_SWMM报告.rpt`、`_耦合交换记录.json`、`_二维设置.json` |
| `水深栅格/` | `_最大水深.tif`、`_逐帧水深.tif`（多波段，每波段一个时刻，时刻见同名 json）；UTM 40N（EPSG:32640），15 m，0 = 干，-9999 = 计算域外 |
| `积水点/` | 二维模拟积水点（两档阈值）+ 评估剔除区标记 + L3 承接与派单，见下文 |
| `设施/` | 本状态全部设施的 GeoJSON（WGS84）、排放连线、清单 CSV；L2+suggested 另有设施台账（设计参数、造价）、地面取水口、造价汇总、全寿命成本、合规检查 |
| `飞渡DTS/` | 飞渡 DTS（h2d）格式，格式说明见 `飞渡DTS说明.md` |

## 积水点不是固定的：从二维结果按阈值动态算

积水点不是事先标好的点，而是从二维结果里**按阈值现算**出来的：

1. 数据：`一二维/*_二维结果.npz` 里每个网格单元的全程最大水深 `hmax` 和每 5 分钟的水深帧 `frames`（`.sww` 里是同一份数据）。
2. 阈值（三个都可调）：
   - 水深 `depth`：本交付给两档，**0.15 m**（文件名 `d0.15`）和 **0.30 m**（`d0.30`）；
   - 最小面积 `min_area` = **100 m²**：连成片的积水单元面积小于它的不算；
   - 最短持续 `min_dur` = **0.5 h**：水深超过阈值的时间短于它的不算。
3. 做法：最大水深 ≥ 阈值的单元按网格相邻关系连成片 → 去掉本状态地表塘自身的占地（塘里有水是正常蓄水，不算积水；地下调蓄池不占地表，不扣）→ 去掉面积、时长不够的片 → 每片编号 `SIM-<状态>-<序号>`，记最大水深、面积、峰值积水量、峰值时刻、超阈值时长、是否退干、200 m 内的客户热点。
4. 重新计算：
   - 命令行：`python -m swbrain.ponding --name {run} --states OPTCS2 --depth 0.2 --min-area 200 --min-dur 1 --pond-scope state --export`
   - 系统接口：`/api/ponding?run={run}&state=OPTCS2&depth=0.2&min_area=200&min_dur=1&pond_scope=state`
   - 用本交付同样的阈值（0.15 m / 100 m² / 0.5 h，`pond_scope=state`）重算，编号与本交付一致。
   - 阈值不同，点数就不同；编号只在同一组阈值内有效。

`积水点/` 里的文件：

| 文件 | 内容 |
|---|---|
| `积水点清单_d0.15.csv` / `_d0.30.csv` | 中文表头清单（Excel 直接打开） |
| `ponding_d*.csv` | 机器读的英文字段版，含阈值本身 |
| `ponding_areas_d*.geojson` / `ponding_points_d*.geojson` | 积水片的面和代表点（WGS84） |
| `ponding_near_hotspots_d*.csv` | 积水点与 200 m 内客户热点的对应 |
| `积水点_评估剔除区标记_d*.csv` | 是否落在评估剔除区：DTM 高程填 0 的空洞、私人地块内的坑、塘占地等，不是真实道路积水，判据评估时剔除（`公共/评估剔除区.geojson`） |
| `L3承接与派单.csv` / `.geojson` / `L3承接_汇总.json` | 交给 L3 的残余积水点（「积水点」栏是本交付 0.15 m 档的编号；「L3 内部编号」是系统 L3 页上的编号，因 L3 先扣剔除区再编号，两者不同）：后果等级（关键 / 高 / 中 / 无暴露）、退干期限、L3 手段，以及两种调度范围（仅 Island A13 驻地；ADM 全部驻地）的派单结果 |

L3 用的积水点是 0.15 m 档，并扣除了评估剔除区；没进 L3 清单的积水点列在 `未进入L3清单的积水点.csv`。
系统 L3 页判片时用的是近似邻接（按单元间距），本交付用二维计算网格的真实三角形邻接：绝大多数积水片完全一致，个别斜角相接的片一边算一片、一边算两片，积水总量不变（逐条见 `未进入L3清单的积水点.csv` 和 `L3承接与派单.csv` 的「备注」）。

## 判据（热点 200 m 内，四项全过才算达标）

A 无 ≥0.30 m 积水；B ≥0.15 m 面积×时间 ≤ L2+planned 的 10 %；C 雨停后 6 h 内退到 0.10 m 以下；D 比 L2+planned 加深的面积 ≤0.05 ha。
结果在 `公共/判据评估_{storm}.json`。

## 本场结果摘要

| 状态 | 热点达标（/11） | 全域 ≥0.30 m 面积 ha | 积水点 ≥0.15 m | 积水点 ≥0.30 m | 设施图层要素 |
|---|---|---|---|---|---|
{sum_rows}

L2+suggested 新增措施 {n_meas} 项（`设施/L2+suggested_设施台账（造价与设计参数）.csv`，一项措施在图层里可能是池 + 重力管 + 输水管几个要素）。

## 公共/

计算域、客户热点、二维计算用 DTM（UTM 40N）、评估剔除区、降雨过程、判据评估结果。

## 说明

- 造价是占位单价，用于方案比较，不是工程概算。
- L2+suggested 的设施位置已按 WA-726 规则、用地和地下管线约束筛过（`设施/L2+suggested_合规检查（WA-726 与空间约束）.json`），落地前仍需现场核实和主管部门许可。
"""


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("storm"); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    storm, out = a.storm, pathlib.Path(a.out); run = f"RDA_{storm}"
    out.mkdir(parents=True, exist_ok=True)
    stats = json.loads((ROOT / f"console/data/flood2d_rda_{storm.lower()}/rda_stats.json").read_text(encoding="utf-8"))
    FAC = stats["facilities"]
    L3 = json.loads((ROOT / f"console/data/flood2d_rda_{storm.lower()}/l3.json").read_text(encoding="utf-8"))
    dirs = {s: out / n for s, n in NM.items()}
    print("积水点 …")
    ponding.export_delivery(run, dirs, out, pond_scope="state")       # 本态地表塘：与判据评估、L3 承接一致（并集会带进弃用方案的塘）
    ex = exclude_zones()
    nf, npts = {}, {}
    for st, d in dirs.items():
        print(st, "…")
        model_files(run, st, d)
        ponding_extra(st, d, L3, ex)
        nf[st] = facilities(run, st, d, FAC)
        npts[st] = {dep: len(json.loads((d / "积水点" / f"ponding_points_d{dep:.2f}.geojson").read_text(encoding="utf-8"))["features"]) for dep in ponding.DELIVERY_DEPTHS}
    h, _ = common(run, storm, out)
    E = json.loads((OPT / f"eval2d_{storm}_ex.json").read_text(encoding="utf-8"))["states"]
    zh = {"L1": "L1 现状", "L2": "L2 雨前预置泵", "L2P": "L2+planned 客户计划", "OPTCS2": "L2+suggested 我们的方案（按 96 mm 优化）"}
    srows = "\n".join(f"| `{NM[s]}/` | {zh[s]} | {nf[s]} |" for s in NM)
    sums = "\n".join(f"| {zh[s]} | {sum(all(v['pass'].values()) for v in E[s]['hotspots'].values())} | {E[s]['domain']['ge030_ha']} | {npts[s][0.15]} | {npts[s][0.30]} | {nf[s]} |" for s in NM)
    mm, du = storm[1:].split("D")
    import time
    txt = README.format(label=f"{h['rain_mm']:.0f} mm / {du} h", run=run, gen=time.strftime("%Y-%m-%d %H:%M"), sim=h["sim_h"], rain=h["rain_mm"], dur=du,
                        peak=h["peak_mmph"], storm=storm, state_rows=srows, sum_rows=sums,
                        n_meas=sum(1 for _ in open(dirs["OPTCS2"] / "设施" / "L2+suggested_设施台账（造价与设计参数）.csv", encoding="utf-8-sig")) - 1)
    rd = out / "README.md"
    old = rd.read_text(encoding="utf-8") if rd.exists() else ""
    from swbrain import dts_h2d
    keep = ""
    if dts_h2d.DTS_MARK[0] in old:                       # 飞渡 DTS 一节原样保留
        i, j = old.index(dts_h2d.DTS_MARK[0]), old.index(dts_h2d.DTS_MARK[1]) + len(dts_h2d.DTS_MARK[1])
        keep = "\n" + old[i:j] + "\n"
    rd.write_text(txt + keep, encoding="utf-8")
    print("→", out, nf, npts)


if __name__ == "__main__":
    main()
