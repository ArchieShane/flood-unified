# -*- coding: utf-8 -*-
"""三道防线的「三个现状」对比：客户领导要看的那张表。

客户的逻辑是三步一比：

    现状①  防线1   只有既有管网                    → 内涝到什么程度
    现状②  防线2   加上已规划的塘（Parsons 方案）    → 缓解了多少
    现状③  防线2+  加上我们在电脑里新挖的塘          → 还能再缓解多少

三者一比，回答「在哪挖、挖多大，既能最大程度防内涝、成本又最低」。

每个现状都是**真跑一遍水动力模型**得到的，不是估算：
  out/three_lines/<汇水区>.<态>.rpt      SWMM 5.2.4 的运行报告
  out/three_lines/<汇水区>.nodes.parquet 节点坐标（.inp 上百 MB，坐标抽出来单放）

本模块只读这些产物、拼成页面要的形状；跑模型那一步在 tools/ 下的脚本里（一次几十分钟）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 态名统一口径（2026-09-26）。**key 不动** —— key 是落盘文件名的一部分
# （<区>.L2P.ponds.geojson、<区>.L2B.rpt），改 key 会让已跑好的结果全部失联；
# 改的是给人看的名字。对齐关系：
#   L1  = 既有灰色管网                     （key L1）
#   L2  = **已建成的 23 座调蓄塘** —— 本系统三个汇水区内**一座都没有**，
#         最近一座离 Mussafah_00 有 7.3 km，所以这一态在这里 ≡ L1，不单列。
#   L2+A= Parsons 顾问设计的新增调蓄         （key L2，历史命名，别照 key 念）
#   L2+B= 本系统设计的新增调蓄               （key L2B 地下箱涵 / key L2L 地面开敞塘变体）
STATES = (
    ("L1", "L1 既有管网", "L1 — existing network",
     "只有既有雨水管网，没有任何塘。这是「什么都不做」的底。"
     "注：**L2（已建成的 23 座调蓄塘）在这三个汇水区内为空** —— "
     "一座都不落在区内，最近一座离 Mussafah_00 汇水区 7.3 km，所以 L2 ≡ L1"),
    ("L2", "L2+A Parsons 设计的新增调蓄", "L2+A — consultant's proposed storage",
     "加上 Parsons 2027 过渡方案的塘与泵（**尚未建成**：报告写明既有系统里一个塘都没有，"
     "且「proposed within available open spaces, subject to site suitability」——地块还没定）"),
    ("L2B", "L2+B 本系统设计·地下调蓄箱涵", "L2+B — ours, buried off-line box",
     "就地建地下离线箱涵（手册 §4.2 B）：埋在管底以下，重力进水 + 带闸孔口控流"
     "（塘满关闸，水留在管网里）+ 单向应急溢流堰 + 排空泵。建成区内合规，库容能做满，"
     "代价是深开挖与钢筋混凝土结构。地面没有变化，二维地形不动。"),
    ("L2L", "L2+B 变体·地面开敞塘（手册否决）", "L2+B variant — open lagoon (ruled out)",
     "地面开敞塘，塘顶齐地面往下挖 2 m（手册 §4.14.1(b) 推荐进出水高差 1.8–2.4 m）。"
     "手册 §4.2 禁止在建成区内或紧邻建成区设开敞塘，所以只落在台账 Public Open Space / "
     "Recreational / Agricultural 的地块上——实测这类地块离积水点中位 2.4–2.9 km，"
     "所以它自带一条输水干管，多数塘还要泵提升。二维地形要按塘的真实轮廓挖。"),
)
STATE_ZH = {k: zh for k, zh, _, _ in STATES}


def params(cfg: dict) -> dict:
    d = {"dir": "out/three_lines", "scenario": "10cc", "top_points": 400}
    d.update((cfg.get("three_lines") or {}))
    return d


def _dir(cfg: dict) -> Path:
    p = Path(params(cfg)["dir"])
    return p if p.is_absolute() else ROOT / p


def catchments(cfg: dict) -> list[dict]:
    """有三态数据的汇水区。缺哪一态也照列，页面好标「还没跑」。"""
    d = _dir(cfg)
    if not d.exists():
        return []
    names = sorted({f.name.split(".")[0] for f in d.glob("*.rpt")})
    out = []
    for n in names:
        have = [k for k, *_ in STATES if (d / f"{n}.{k}.rpt").exists()]
        out.append({"catchment": n, "states": have,
                    "complete": len(have) == len(STATES),
                    "has_nodes": (d / f"{n}.nodes.parquet").exists()})
    return out


# ---------------------------------------------------------------- 解析 .rpt
def _num(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def parse_rpt(f: Path) -> dict:
    """SWMM 运行报告 → 水量平衡、逐点溢流、蓄水节点使用情况。"""
    t = f.read_text(encoding="utf-8", errors="replace")
    o: dict = {"balance": {}, "nodes": [], "storage": []}
    for k, zh in (("External Inflow", "inflow_m3"), ("External Outflow", "outflow_m3"),
                  ("Flooding Loss", "flood_loss_m3"), ("Final Stored Volume", "stored_m3")):
        m = re.search(rf"{re.escape(k)}\s*\.*\s+(-?\d[\d.]*)\s+(-?\d[\d.]*)", t)
        if m:
            o["balance"][zh] = round(float(m.group(2)) * 1000, 1)
    m = re.search(r"Continuity Error \(%\)\s*\.*\s+(-?\d[\d.]*)", t)
    o["balance"]["continuity_pct"] = _num(m.group(1)) if m else None

    b = re.search(r"Node Flooding Summary(.*?)\n\s*\n\s*\n", t, re.S)
    if b:
        for ln in b.group(1).splitlines():
            c = ln.split()
            # 节点 | 积水小时 | 最大速率 LPS | 天 | 时:分 | 总溢流量 10^6L | 最大积水深 m
            if len(c) >= 7 and re.match(r"^[\w\-.]+$", c[0]) and re.match(r"^[\d.]+$", c[1]):
                v = _num(c[-2])
                if v is None:
                    continue
                o["nodes"].append({"node": c[0], "hours": _num(c[1], 0.0),
                                   "volume_m3": round(v * 1000, 1),
                                   "ponded_depth_m": _num(c[-1], 0.0)})
    s = re.search(r"Storage Volume Summary(.*?)\n\s*\n\s*\n", t, re.S)
    if s:
        for ln in s.group(1).splitlines():
            c = ln.split()
            if len(c) >= 8 and re.match(r"^[A-Za-z]", c[0]) and _num(c[1]) is not None:
                o["storage"].append({"node": c[0], "avg_m3": round(_num(c[1], 0) * 1000, 1),
                                     "max_m3": round(_num(c[5], 0) * 1000, 1),
                                     "max_pct_full": _num(c[6], 0.0)})
    return o


def _nodes_xy(cfg: dict, catchment: str, key: str | None = None) -> dict:
    """每个现状各自的节点坐标。

    不能只用基准那一份：防线2 / 2+ 的模型多了集水井 W-*、泵出口 PMP-*_Junction、塘 PO-* / NP-*，
    查不到坐标的节点会被静默丢掉——实测 Shaliela 防线2 因此漏掉 35,692 m³（把 −31% 说成了 −50%）。
    """
    d = _dir(cfg)
    f = d / f"{catchment}.{key}.nodes.parquet" if key else d / f"{catchment}.nodes.parquet"
    if not f.exists():
        f = d / f"{catchment}.nodes.parquet"
    if not f.exists():
        return {}
    try:
        import pandas as pd
        d = pd.read_parquet(f)
    except Exception:
        return {}
    return {str(r.node): (float(r.lon), float(r.lat)) for r in d.itertuples(index=False)}


def _hotspots_near(cfg: dict, pts: list[dict], radius_m: float = 150.0) -> dict:
    """把溢流节点对到客户热点上——领导问的是「历史积水点消没消失」，不是「哪个井冒水」。"""
    f = ROOT / "out" / "state" / "hotspots_enriched.parquet"
    if not f.exists() or not pts:
        return {}
    try:
        import numpy as np
        import pandas as pd
        from scipy.spatial import cKDTree
        hs = pd.read_parquet(f, columns=["hotspot_id", "lon", "lat"]).dropna()
    except Exception:
        return {}
    if not len(hs):
        return {}
    kx = 111000.0 * np.cos(np.radians(24.4)); ky = 111000.0
    tree = cKDTree(np.c_[hs["lon"].values * kx, hs["lat"].values * ky])
    px = np.array([p["lon"] for p in pts]) * kx
    py = np.array([p["lat"] for p in pts]) * ky
    d, i = tree.query(np.c_[px, py], distance_upper_bound=radius_m)
    out = {}
    for k, (dd, ii) in enumerate(zip(d, i)):
        if np.isfinite(dd) and ii < len(hs):
            pts[k]["hotspot_id"] = str(hs["hotspot_id"].iloc[ii])
            out.setdefault(str(hs["hotspot_id"].iloc[ii]), []).append(pts[k]["volume_m3"])
    return out


def state(cfg: dict, catchment: str, key: str) -> dict:
    """一个现状：基础设施 + 推演结果 + 指标。"""
    f = _dir(cfg) / f"{catchment}.{key}.rpt"
    if not f.exists():
        return {"state": key, "available": False,
                "note": f"{STATE_ZH.get(key, key)} 还没跑（缺 {f.name}）"}
    r = parse_rpt(f)
    xy = _nodes_xy(cfg, catchment, key)
    P = params(cfg)

    # 塘（蓄水节点）：Parsons 的 PO-*，我们的 NP-*
    ponds = [s for s in r["storage"] if re.match(r"^(NP|PO|STO)", s["node"], re.I)]
    fp = _pond_footprints(cfg, catchment, key, [p["node"] for p in ponds], xy)
    for p in ponds:
        p["lon"], p["lat"] = xy.get(p["node"], (None, None))
        p["origin"] = "system_plan" if p["node"].upper().startswith("NP") else "client_model"
        # 塘是个面，不是一个点：选址时落的真实轮廓带上，地图放大能看到边界
        ft = fp.get(p["node"])
        if ft:
            p["footprint"] = ft["geometry"]
            for k2 in ("land", "land_use", "plot_id", "moved_m", "q_in_m3h", "vol_m3",
                       "bottom_m", "top_m", "ground_m", "inflow", "lift_m",
                       "n_pumps_in", "n_pumps_out", "conveyance_m", "dn_mm", "form"):
                if ft["properties"].get(k2) is not None:
                    p[k2] = ft["properties"][k2]

    # 只有**塘**的溢流不算街道积水：塘是露天大水面，水漫在塘周围。
    # 集水井 W-* 是路边一个小竖井，它冒水就是路上淹了，必须算进街道积水——
    # 一度把它也排除，Shaliela 防线2 的成绩从 −31.0% 虚增到 −49.6%，凭空给方案加了 19 个百分点。
    # 泵出口 *_Junction 是模型里的虚节点，单列不计。
    pond_ids = {p["node"] for p in ponds}
    POND = re.compile(r"^(NP|PO|STO)[-_]?\d", re.I)
    DUMMY = re.compile(r"_Junction$", re.I)
    pts, spill, dummy, no_xy = [], 0.0, 0.0, 0.0
    for n in r["nodes"]:
        if n["node"] in pond_ids or POND.match(n["node"]):
            spill += n["volume_m3"]; continue
        if DUMMY.search(n["node"]):
            dummy += n["volume_m3"]; continue
        lon, lat = xy.get(n["node"], (None, None))
        if lon is None:
            no_xy += n["volume_m3"]
            pts.append({**n, "lon": None, "lat": None})
            continue
        pts.append({**n, "lon": round(lon, 6), "lat": round(lat, 6)})
    pts.sort(key=lambda z: -z["volume_m3"])
    hs = _hotspots_near(cfg, pts)

    street = round(sum(p["volume_m3"] for p in pts), 1)
    hrs = sorted(((p["hours"], p["volume_m3"]) for p in pts), key=lambda z: z[0])
    med = None
    if hrs:
        tot = sum(v for _, v in hrs); acc = 0.0
        for h, v in hrs:
            acc += v
            if acc >= tot / 2:
                med = h; break
    return {
        "state": key, "available": True, "label": STATE_ZH.get(key, key),
        "balance": r["balance"],
        "kpi": {
            "street_flood_m3": street,
            "n_flooded_nodes": len(pts),
            "n_hotspots_flooded": len(hs),
            "median_flood_h": med,
            "max_ponded_depth_m": round(max((p["ponded_depth_m"] for p in pts), default=0.0), 2),
            "pond_capacity_m3": round(sum(p["max_m3"] for p in ponds) if ponds else 0.0, 0),
            "pond_used_m3": round(sum(p["max_m3"] for p in ponds), 0),
            "pond_spill_m3": round(spill, 1),
            "dummy_node_m3": round(dummy, 1),
            "no_coord_m3": round(no_xy, 1),
            "n_ponds": len(ponds),
            "continuity_pct": r["balance"].get("continuity_pct"),
        },
        "ponds": ponds,
        "points": pts[:int(P["top_points"])],
        "n_points_total": len(pts),
        "hotspots_flooded": sorted(hs),
    }


# ------------------------------------------------ 基础设施底图（管、井、泵、出水口、塘）
# 三个现状共用同一套既有管网，只有塘不一样。底图单独一个接口，页面加载一次、三态复用。
NET_LAYERS = (
    ("conduit", "管线", "Conduit"),
    ("manhole", "检查井", "Manhole"),
    ("inlet", "雨水口", "Inlet"),
    ("outfall", "出水口", "Outfall"),
    ("pump", "泵", "Pump"),
    ("wetwell", "湿井", "Wet well"),
    ("pond", "防线2 的塘（Parsons 方案，尚未建成）", "Line-2 pond (proposed)"),
)
_NET_SRC = {"conduits": "conduit", "manholes": "manhole", "inlets": "inlet",
            "outfalls": "outfall", "pumps": "pump", "wetwells": "wetwell", "ponds": "pond"}


def _dmt_folder(cfg: dict, catchment: str) -> str | None:
    """三态里的汇水区名 → 客户模型导出里的文件夹名（Mussafah ↔ Musaffah_00）。"""
    from . import dmtmodel
    for folder, meta in (dmtmodel.params(cfg)["models"] or {}).items():
        p = str((meta or {}).get("parsons") or folder)
        if catchment in (folder, p) or p.startswith(catchment) or folder.startswith(catchment):
            return folder
    return None


def _cell(row, col, cast=float):
    if col not in getattr(row, "index", ()):
        return None
    v = row.get(col)
    try:
        if v is None or (isinstance(v, float) and v != v):
            return None
        return cast(v)
    except (TypeError, ValueError):
        return None


def network(cfg: dict, catchment: str, rebuild: bool = False) -> dict:
    """既有基础设施底图：管线、检查井、雨水口、出水口、泵、湿井、防线2 的塘（面）。

    来源是客户 2026-09-20 交付的 Bentley shapefile（EPSG:32640，无 .prj），
    不是台账库——台账在模型边界内的管长只有模型的 39%~115%，两者对不上，底图用模型自己的。
    **RB3 那份缺全部 .dbf**，只有几何没有属性，点开一根管什么也看不到，页面要如实标。

    结果缓存到 out/three_lines/<汇水区>.net.geojson：客户目录不一定挂得上，
    缓存在就不必每次读 shapefile（也让演示机不依赖那 276 MB 的原件）。
    """
    cache = _dir(cfg) / f"{catchment}.net.geojson"
    if cache.exists() and not rebuild:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    from . import dmtmodel
    folder = _dmt_folder(cfg, catchment)
    if not folder or not dmtmodel.available(cfg):
        return {"type": "FeatureCollection", "features": [], "catchment": catchment,
                "note": "客户模型导出目录没挂上（config [dmt_model].dir），底图不可用"}
    L = dmtmodel.load_model(cfg, folder)
    feats, counts, attrs = [], {}, {}
    for src, layer in _NET_SRC.items():
        g = L.get(src)
        if g is None or not len(g):
            continue
        has = bool(g.attrs.get("has_dbf")) and len(g.columns) > 1
        attrs[layer] = has
        w = g.to_crs("EPSG:4326")
        for _, r in w.iterrows():
            geom = r.geometry
            if geom is None or geom.is_empty:
                continue
            if layer == "conduit":
                gj = {"type": "LineString",
                      "coordinates": [[round(x, 6), round(y, 6)] for x, y in geom.coords]}
                p = {"label": r.get("LABEL") if has else None,
                     "dia_mm": (lambda d: round(d * 1000) if d and d < 10 else d)(_cell(r, "D")),
                     "length_m": _cell(r, "LENGTH"), "cap_full": _cell(r, "CAP_FULL")}
            elif layer in ("pond",):
                gg = geom if geom.geom_type == "Polygon" else geom.convex_hull
                gj = {"type": "Polygon",
                      "coordinates": [[[round(x, 6), round(y, 6)] for x, y in gg.exterior.coords]]}
                p = {"label": r.get("LABEL") if has else None,
                     "max_stor_m3": _cell(r, "MAX_STOR"), "origin": "client_model",
                     "status": "planned"}
            else:
                c = geom if geom.geom_type == "Point" else geom.centroid
                gj = {"type": "Point", "coordinates": [round(c.x, 6), round(c.y, 6)]}
                p = {"label": (r.get("LABEL") if "LABEL" in r.index else r.get("ID")) if has else None,
                     "gnd_m": _cell(r, "ELEV_GND"), "inv_m": _cell(r, "ELEV_INV")}
                if layer == "manhole":
                    p["ever_overflow"] = (str(r.get("EVER_OF")).lower() in ("true", "1", "yes")
                                          if has and "EVER_OF" in r.index else None)
                    p["flow_max_lps"] = _cell(r, "FLOW_MAX")
                elif layer == "pump":
                    p["pump_flow"] = _cell(r, "PUMP_FLOW"); p["pump_head"] = _cell(r, "PUMP_HEAD")
                elif layer == "inlet":
                    p["inlet_type"] = r.get("INLET_TYPE") if has and "INLET_TYPE" in r.index else None
            feats.append({"type": "Feature", "geometry": gj,
                          "properties": {"layer": layer, **{k: v for k, v in p.items() if v is not None}}})
            counts[layer] = counts.get(layer, 0) + 1
    out = {"type": "FeatureCollection", "features": feats, "catchment": catchment,
           "model_folder": folder, "counts": counts, "has_attributes": attrs,
           "layers": [{"key": k, "zh": zh, "en": en, "n": counts.get(k, 0),
                       "attrs": attrs.get(k, False)} for k, zh, en in NET_LAYERS],
           "source": "DMT Hydraulic Model Data Export（2026-09-20 客户交付）",
           "crs_note": "shapefile 无 .prj，按 EPSG:32640 转 WGS84",
           "caveat": ("RB3 这份 shapefile 缺全部 .dbf，只有几何没有属性"
                      if not any(attrs.values()) else "")}
    try:
        cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return out


def _centroid(geom: dict) -> tuple[float, float] | None:
    try:
        ring = geom["coordinates"][0]
        return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
    except Exception:
        return None


def _pond_footprints(cfg: dict, catchment: str, key: str, nodes=(), xy: dict | None = None) -> dict:
    """塘的真实轮廓（面）：客户领导放大之后要看得见塘的边界，不能只给一个圆点。

    防线2+  选址脚本落的 <区>.L2P.ponds.geojson，properties.id 就是 SWMM 节点名。
    防线2   客户 POnds.shp（底图里的 pond 图层）。编号对不上：图层写 P1 / STO-PO3，
            SWMM 写 PO-1，先按归一化编号配；**RB3 那份缺 .dbf 连标签都没有**，
            退一步按塘心距最近配（300 m 内），配不上就不给面、仍画圆点。
    """
    if key in ("L2P", "L2B", "L2L"):
        f = _dir(cfg) / f"{catchment}.{key}.ponds.geojson"
        if not f.exists():
            return {}
        try:
            gj = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {str(ft["properties"].get("id")): ft for ft in gj.get("features", [])
                if ft.get("properties")}
    if key != "L2" or not xy or not nodes:
        return {}
    from .dmtmodel import _pond_key
    net = network(cfg, catchment)
    pool = [ft for ft in net.get("features", [])
            if (ft.get("properties") or {}).get("layer") == "pond"]
    if not pool:
        return {}
    out, left = {}, []
    by_key = {}
    for ft in pool:
        lab = ft["properties"].get("label")
        if lab:
            by_key.setdefault(_pond_key(lab), ft)
        else:
            left.append(ft)
    used = set()
    todo = []
    for node in nodes:
        ft = by_key.get(_pond_key(node))
        if ft is not None and id(ft) not in used:
            used.add(id(ft)); out[node] = ft
        else:
            todo.append(node)
    # 剩下的按塘心距最近配（RB3 无标签时全走这条）
    spare = [ft for ft in pool if id(ft) not in used]
    for node in todo:
        if node not in xy or xy[node][0] is None:
            continue
        lon, lat = xy[node]
        best, bd = None, 300.0 / 111000.0
        for ft in spare:
            c = _centroid(ft["geometry"])
            if not c:
                continue
            d = ((c[0] - lon) * 0.91) ** 2 + (c[1] - lat) ** 2
            if d ** 0.5 < bd:
                best, bd = ft, d ** 0.5
        if best is not None:
            spare.remove(best); out[node] = best
    return out


def compare(cfg: dict, catchment: str) -> dict:
    """三态一起给，附两两差值。"""
    st = {k: state(cfg, catchment, k) for k, *_ in STATES}
    base = st["L1"].get("kpi") or {}
    for k, s in st.items():
        if not s.get("available") or k == "L1" or not base.get("street_flood_m3"):
            continue
        kp = s["kpi"]
        s["delta_vs_L1"] = {
            "street_flood_m3": round(kp["street_flood_m3"] - base["street_flood_m3"], 1),
            "street_flood_pct": round(kp["street_flood_m3"] / base["street_flood_m3"] * 100 - 100, 1),
            "n_flooded_nodes": kp["n_flooded_nodes"] - base["n_flooded_nodes"],
            "n_hotspots_flooded": kp["n_hotspots_flooded"] - base["n_hotspots_flooded"],
        }
    # 哪些历史热点在②/③里消失了
    h1 = set(st["L1"].get("hotspots_flooded") or [])
    for k in (x[0] for x in STATES if x[0] != "L1"):
        if st[k].get("available"):
            hk = set(st[k].get("hotspots_flooded") or [])
            st[k]["hotspots_cleared"] = sorted(h1 - hk)
            st[k]["hotspots_new"] = sorted(hk - h1)
    return {"catchment": catchment,
            "scenario": params(cfg)["scenario"],
            "states": [{"key": k, "zh": zh, "en": en, "desc": d, **st[k]}
                       for k, zh, en, d in STATES],
            "caveat": "现状①（防线1）是从交付的「治理后」模型剥离缓解措施重建的，"
                      "不是客户的既有管网模型；要拿客户自己的 existing .inp 才算定论。"}


def compare_all(cfg: dict) -> dict:
    """三个汇水区一起看：每区一行三态，外加全网合计。

    领导问的是「整体投下去有多大效果」，单看一个汇水区答不了。
    合计只把三态都跑齐的区算进去，缺态的区单列——免得拿不完整的数凑总量。
    """
    cs = catchments(cfg)
    rows, partial = [], []
    w = {"catchments": 0, **{x[0]: 0.0 for x in STATES},
         "nodes_L1": 0, "nodes_L2P": 0, "hot_L1": 0, "hot_L2P": 0,
         "pond_L2": 0.0, "pond_L2P": 0.0, "cleared": []}
    for c in cs:
        r = compare(cfg, c["catchment"])
        rows.append(r)
        st = {x["key"]: x for x in r["states"]}
        if not all(st[k].get("available") for k in (x[0] for x in STATES)):
            partial.append(c["catchment"])
            continue
        w["catchments"] += 1
        for k in (x[0] for x in STATES):
            w[k] += st[k]["kpi"]["street_flood_m3"]
        w["nodes_L1"] += st["L1"]["kpi"]["n_flooded_nodes"]
        w["nodes_L2P"] += st["L2B"]["kpi"]["n_flooded_nodes"]
        w["hot_L1"] += st["L1"]["kpi"]["n_hotspots_flooded"]
        w["hot_L2P"] += st["L2B"]["kpi"]["n_hotspots_flooded"]
        w["pond_L2"] += st["L2"]["kpi"]["pond_used_m3"]
        w["pond_L2P"] += st["L2B"]["kpi"]["pond_used_m3"]
        w["cleared"] += (st["L2B"].get("hotspots_cleared") or [])
    if w["L1"]:
        for k in (x[0] for x in STATES if x[0] != "L1"):
            w[f"{k}_pct"] = round(w[k] / w["L1"] * 100 - 100, 1)
    w["partial"] = partial
    return {"scenario": params(cfg)["scenario"], "catchments": rows, "total": w,
            "caveat": rows[0]["caveat"] if rows else ""}


def main(argv=None):
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    from . import db
    ap = argparse.ArgumentParser(description="L1 / L2+A / L2+B 各态对比")
    ap.add_argument("catchment", nargs="?", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    cfg = db.load_config(a.config)
    cs = catchments(cfg)
    if not a.catchment:
        A = compare_all(cfg)
        ks = [x[0] for x in STATES]
        num = {"L1": "①", "L2": "②", "L2B": "③", "L2L": "④", "L2P": "·"}
        print(f"各态一起看（情景 {A['scenario']}）　单位 m³：街道积水量")
        print("  ① L1 既有管网　② L2+A Parsons 设计　③ L2+B 本系统设计（箱涵）"
              "　④ L2+B 变体（开敞塘，手册否决）")
        print("  **L2（已建成的 23 座塘）在这三个汇水区内为空，≡ L1**")
        print()
        hdr = f"{'汇水区':<11}"
        for k in ks:
            hdr += f"{num.get(k, '') + ' ' + STATE_ZH[k]:>16}" + ("" if k == "L1" else f"{'':>8}")
        print(hdr + f"{'积水节点 ①→③':>17}")
        print('-' * 118)
        for r in A['catchments']:
            st = {x['key']: x for x in r['states']}
            miss = [STATE_ZH[k] for k in ks if not st[k].get('available')]
            if miss:
                print(f"{r['catchment']:<11}还缺：{'、'.join(miss)}")
                continue
            row = f"{r['catchment']:<11}"
            for k in ks:
                row += f"{st[k]['kpi']['street_flood_m3']:>16,.0f}"
                if k != "L1":
                    row += f"{st[k]['delta_vs_L1']['street_flood_pct']:>7.1f}%"
            n13 = (f"{st['L1']['kpi']['n_flooded_nodes']:,} → "
                   f"{st['L2B']['kpi']['n_flooded_nodes']:,}")
            print(row + f"{n13:>17}")
            for k in ks[1:]:
                cl = st[k].get('hotspots_cleared') or []
                if cl:
                    print(f"{'':<11}  {STATE_ZH[k]} 让 {len(cl)} 个历史热点不再积水："
                          f"{'、'.join(cl[:10])}")
        w = A['total']
        if w.get('L1'):
            print('-' * 118)
            row = f"{'合计':<11}"
            for k in ks:
                row += f"{w[k]:>16,.0f}"
                if k != "L1":
                    row += f"{w[f'{k}_pct']:>7.1f}%"
            n13 = f"{w['nodes_L1']:,} → {w['nodes_L2P']:,}"
            print(row + f"{n13:>17}")
            tail = ('；' + '、'.join(w['partial']) + ' 还缺态，未计入）') if w['partial'] else '）'
            print()
            print(f"（合计只算四态都跑齐的 {w['catchments']} 个区{tail}")
            print(f"塘实际用掉：防线2 {w['pond_L2']:,.0f} m³，防线2+ 箱涵 {w['pond_L2P']:,.0f} m³")
        print()
        for k, zh, en, d in STATES:
            print(f"{num.get(k, '')} {zh}：{d}")
        print()
        print(A['caveat'])
        return 0
    r = compare(cfg, a.catchment)
    print(f"{r['catchment']}（情景 {r['scenario']}）")
    print(f"{'现状':<20}{'街道积水 m³':>13}{'较防线1':>9}{'积水节点':>9}{'仍淹热点':>9}"
          f"{'中位时长 h':>11}{'塘数':>5}{'塘用量 m³':>11}{'误差%':>8}")
    print("-" * 100)
    for s in r["states"]:
        if not s.get("available"):
            print(f"{s['zh']:<20}{s.get('note', '未跑')}"); continue
        k = s["kpi"]; d = s.get("delta_vs_L1") or {}
        pct = f"{d['street_flood_pct']:+.1f}%" if d else "—"
        med = k["median_flood_h"] if k["median_flood_h"] is not None else 0.0
        err = k["continuity_pct"] if k["continuity_pct"] is not None else float("nan")
        print(f"{s['zh']:<20}{k['street_flood_m3']:>13,.0f}{pct:>9}"
              f"{k['n_flooded_nodes']:>9,}{k['n_hotspots_flooded']:>9}{med:>11.2f}"
              f"{k['n_ponds']:>5}{k['pond_used_m3']:>11,.0f}{err:>8.2f}")
        if s.get("hotspots_cleared"):
            print(f"{'':<20}→ 消失的历史热点 {len(s['hotspots_cleared'])} 个："
                  f"{'、'.join(s['hotspots_cleared'][:8])}"
                  f"{' …' if len(s['hotspots_cleared']) > 8 else ''}")
    print(f"\n{r['caveat']}")
    if a.out:
        p = Path(a.out); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"写到 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
