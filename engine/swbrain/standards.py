"""法定设计标准：把 DMT《雨水与地下排水设计手册》WA-726-1 第三版（2022-04）落到每一段管上。

手册怎么规定（页码为 PDF 页）：
  · 表 3-2（p35–36）设计重现期分设施：一般管网 1/5 年；主干路、宫殿、关键区 1/10；次要道路涵洞、无出口塘、机场海港 1/25（校核 1/50）；
    下穿通道与地下车库坡道、主干路涵洞 1/50（校核 1/100）。
  · 表 3-3（p36）设计历时 = 汇流时间 tc × 倍数 M：主干路/下穿/机场 1×；高密度区 2×；低密度区 3×；工业区 5×；公园空地 12×。
  · 3.2.3（p37–39）tc = 入流时间（不透水面最少 10 分钟）+ 管内行程时间 Σ L/V_full。
  · 表 3-6/3-7（p44）B 区 IDF：i = α·t^(−β)，t 小时。
  · 3.3（p46）推理公式 Q = C·i·A 只适用 A < 80 ha 或 tc < 30 min。
本模块给每段管定「类别 → 设计重现期、校核重现期、历时倍数 → tc → 设计雨强 → 设计流量/满管能力」，并汇总达标份额。
分类依据 makani 库：道路中心线 roadtype、道路隧道面、机场面、公园面、建筑用途面（用途与层数）；重点单位来自 refdata 缓存。
分类是启发式（比如 secondaryRoad 也当主干路、≥4 层当高密度），都在 config [standards] 里，能改。
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from . import db, hydraulics as hy

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "out" / "state"

CLASS_ORDER = ["underpass", "airport", "major_road", "critical_facility", "industrial", "open_space", "high_density", "low_density", "general"]
CLASS_ZH = {"underpass": "下穿/地下车库", "airport": "机场/海港", "major_road": "主干路", "critical_facility": "重点单位周边", "industrial": "工业区",
            "open_space": "公园/空地", "high_density": "高密度区", "low_density": "低密度区", "general": "一般区"}
CLASS_EN = {"underpass": "Underpass / basement ramp", "airport": "Airport / seaport", "major_road": "Major road", "critical_facility": "Critical facility surroundings",
            "industrial": "Industrial", "open_space": "Park / open space", "high_density": "High-density sector", "low_density": "Low-density sector", "general": "General"}


# ---------------------------------------------------------------- IDF（表 3-6 / 3-7）
def idf_intensity(cfg: dict, return_period_yr: int, duration_min: float) -> float:
    """按表 3-7 公式 i = α·t^(−β)（t 小时）。历时下限 5 分钟（表格最短档）。"""
    eq = cfg["idf"]["equation"][str(int(return_period_yr))]
    t_h = max(float(duration_min), 5.0) / 60.0
    return float(eq["alpha"]) * t_h ** (-float(eq["beta"]))


def idf_table(cfg: dict, return_period_yr: int, duration_min: float) -> float:
    """按表 3-6 查表（线性内插，对数历时）。用于核对公式。"""
    d = np.asarray(cfg["idf"]["durations_min"], float); i = np.asarray(cfg["idf"]["intensity_mmh"][str(int(return_period_yr))], float)
    x = np.clip(float(duration_min), d[0], d[-1])
    return float(np.exp(np.interp(np.log(x), np.log(d), np.log(i))))


# ---------------------------------------------------------------- 参考数据（makani）
def build_ref(cfg: dict) -> dict:
    """从 makani 抓分类要素，缓存到 out/state/standards_*.parquet。只读源库。"""
    S = cfg["standards"]
    STATE.mkdir(parents=True, exist_ok=True)
    mconn = db.connect_source(cfg, "makani")
    out = {}
    with mconn:
        major = list(S["roadtype_major"]["values"])
        roads = db.df(mconn, """
            select roadtype, st_x((dp).geom) lon, st_y((dp).geom) lat
            from (select roadtype, st_dumppoints(st_segmentize(shape, 0.0004)) dp
                  from public.udm_roadcentreline
                  where roadtype = any(%s) and coalesce(physicalstatus,'') not in ('removed','Planned')) q""", (major,))
        out["major_roads"] = roads
        out["tunnels"] = db.df(mconn, "select roadtunnelid id, st_astext(st_centroid(shape)) c, st_astext(shape) wkt from public.udm_roadtunnel where coalesce(physicalstatus,'')<>'removed'")
        out["airports"] = db.df(mconn, "select airportareaid id, airportareatype sub, st_astext(shape) wkt from public.udm_airportarea where coalesce(physicalstatus,'')<>'removed'")
        out["parks"] = db.df(mconn, "select parkid id, nameenglish name, st_astext(shape) wkt from public.udm_park")
        out["buildings"] = db.df(mconn, """
            select bldg_usagecategorytype usage, coalesce(nullif(regexp_replace(bldg_buildingnumoffloors::text, '[^0-9.]', '', 'g'), ''), '0')::float floors, plots_primaryuseeng plot_use,
                   st_x(st_centroid(shape)) lon, st_y(st_centroid(shape)) lat
            from public.bdms_bldg_landuse where bldg_buildingstatus = 'As Built'""")
    for k, v in out.items():
        v.to_parquet(STATE / f"standards_{k}.parquet", index=False)
    return {k: len(v) for k, v in out.items()}


def load_ref() -> dict:
    need = ["major_roads", "tunnels", "airports", "parks", "buildings"]
    if not all((STATE / f"standards_{k}.parquet").exists() for k in need):
        raise FileNotFoundError("缺 out/state/standards_*.parquet，先跑 python -m swbrain.standards --build-ref")
    return {k: pd.read_parquet(STATE / f"standards_{k}.parquet") for k in need}


def _xy(lon, lat):
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    x, y = tr.transform(np.asarray(lon, float), np.asarray(lat, float))
    return np.c_[x, y]


def _polys_xy(wkts):
    from shapely import wkt as swkt
    from shapely.ops import transform as sh_transform
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    geoms = []
    for w in wkts:
        try:
            g = swkt.loads(w)
            geoms.append(sh_transform(lambda xx, yy, zz=None: tr.transform(xx, yy), g))
        except Exception:
            continue
    return geoms


# ---------------------------------------------------------------- 分类（表 3-2 / 3-3）
def classify_edges(es: pd.DataFrame, vertices: pd.DataFrame, ref: dict, facilities: pd.DataFrame | None, cfg: dict) -> pd.DataFrame:
    """给每段管一个类别及其设计重现期、校核重现期、历时倍数。优先级：下穿 > 机场 > 主干路 > 重点单位 > 工业 > 公园 > 高/低密度 > 一般。"""
    from shapely import STRtree
    from shapely.geometry import Point
    S = cfg["standards"]; C = S["classes"]
    vt = vertices.set_index("id")
    mx = (es["u"].map(vt["x"]).values + es["v"].map(vt["x"]).values) / 2.0
    my = (es["u"].map(vt["y"]).values + es["v"].map(vt["y"]).values) / 2.0
    ok = np.isfinite(mx) & np.isfinite(my)
    pts_xy = np.c_[np.where(ok, mx, 0.0), np.where(ok, my, 0.0)]
    n = len(es)
    cls = np.full(n, "general", dtype=object)
    # 建筑用途（先算，作为底色）
    b = ref["buildings"].dropna(subset=["lon", "lat"])
    if len(b):
        bxy = _xy(b["lon"].values, b["lat"].values)
        d, j = cKDTree(bxy).query(pts_xy, distance_upper_bound=float(S["building_buffer_m"]))
        hit = np.isfinite(d)
        usage = b["usage"].fillna("").values; floors = b["floors"].fillna(0).values
        jj = np.where(hit, j, 0)
        u = np.where(hit, usage[jj], ""); f = np.where(hit, floors[jj], 0.0)
        is_ind = hit & np.isin(u, ["Industrial"])
        is_high = hit & (np.isin(u, ["Commercial", "CommercialResidential", "Governmental", "Educational", "Religious", "Utility"]) | (f >= float(S["high_density_min_floors"])))
        is_low = hit & ~is_ind & ~is_high
        cls[is_low] = "low_density"; cls[is_high] = "high_density"; cls[is_ind] = "industrial"
    # 公园面（包含）
    parks = _polys_xy(ref["parks"]["wkt"].dropna().values)
    if parks:
        tree = STRtree(parks); q = [Point(x, y) for x, y in pts_xy]
        inside = tree.query(q, predicate="within")
        cls[np.unique(inside[0])] = "open_space"
    # 重点单位周边（医院/学校/地铁站 100 m 内）
    if facilities is not None and len(facilities):
        fac = facilities[facilities["kind"].isin(["hospital", "school", "metro", "civil_defense"])].dropna(subset=["lon", "lat"])
        if len(fac):
            d, _ = cKDTree(_xy(fac["lon"].values, fac["lat"].values)).query(pts_xy, distance_upper_bound=float(S["facility_buffer_m"]))
            cls[np.isfinite(d) & ~np.isin(cls, ["underpass", "airport", "major_road"])] = "critical_facility"
    # 主干路（中心线采样点距离）
    r = ref["major_roads"].dropna(subset=["lon", "lat"])
    if len(r):
        d, _ = cKDTree(_xy(r["lon"].values, r["lat"].values)).query(pts_xy, distance_upper_bound=float(S["road_buffer_m"]))
        cls[np.isfinite(d)] = "major_road"
    # 机场面（缓冲）
    ap = _polys_xy(ref["airports"]["wkt"].dropna().values)
    if ap:
        tree = STRtree(ap); q = [Point(x, y) for x, y in pts_xy]
        near = tree.query(q, predicate="dwithin", distance=float(S["airport_buffer_m"]))
        cls[np.unique(near[0])] = "airport"
    # 下穿（隧道面缓冲）
    tn = _polys_xy(ref["tunnels"]["wkt"].dropna().values)
    if tn:
        tree = STRtree(tn); q = [Point(x, y) for x, y in pts_xy]
        near = tree.query(q, predicate="dwithin", distance=float(S["tunnel_buffer_m"]))
        cls[np.unique(near[0])] = "underpass"
    cls[~ok] = "general"
    out = pd.DataFrame({"gid": es["gid"].values, "design_class": cls})
    out["design_rp_yr"] = [int(C[c]["rp"]) for c in cls]
    out["check_rp_yr"] = [int(C[c]["check"]) for c in cls]
    out["tc_multiplier"] = [float(C[c]["m"]) for c in cls]
    return out


# ---------------------------------------------------------------- 汇流时间（3.2.3）
def time_of_concentration(es: pd.DataFrame, g, cfg: dict) -> pd.Series:
    """tc（分钟）= 入流时间 + 最长上游路径的管内行程时间。行程时间按满管流速 V = Q_full / A，夹持在 [0.3, 3.0] m/s。"""
    import networkx as nx
    S = cfg["standards"]
    vlo, vhi = float(S["v_full_clip_ms"][0]), float(S["v_full_clip_ms"][1])
    d_m = (es["pipe_diameter"].fillna(300.0).clip(lower=100.0) / 1000.0).values
    area = np.pi * d_m ** 2 / 4.0
    v = np.clip(es["q_full"].values / np.where(area > 1e-6, area, np.nan), vlo, vhi)
    v = np.where(np.isfinite(v), v, vlo)
    t_edge = dict(zip(es["gid"].astype(int).values, es["len_m"].fillna(0.0).values / v))          # 秒
    node_t: dict[int, float] = {}
    edge_tc: dict[int, float] = {}
    for u in nx.topological_sort(g):
        tu = node_t.get(u, 0.0)
        for vv in g.successors(u):
            gid = int(g[u][vv]["gid"])
            te = tu + t_edge.get(gid, 0.0)
            edge_tc[gid] = te
            if te > node_t.get(vv, 0.0):
                node_t[vv] = te
    entry = float(S["entry_time_min"])
    return pd.Series(es["gid"].astype(int).map(edge_tc).fillna(0.0).values / 60.0 + entry, index=es.index, name="tc_min")


# ---------------------------------------------------------------- 设计雨强与达标
def design_intensity(es: pd.DataFrame, cls: pd.DataFrame, tc_min: pd.Series, cfg: dict) -> pd.DataFrame:
    """设计历时 = M × tc；设计雨强按 B 区公式；设计流量 Q = C·i·A；能力比 = Q / Q_full。校核重现期同法。"""
    m, D = cfg["model"], cfg["decision"]
    s = es.merge(cls, on="gid", how="left")
    s["tc_min"] = tc_min.values
    s["design_duration_min"] = np.maximum(s["tc_multiplier"].fillna(2.0) * s["tc_min"], 5.0)
    s["design_i_mmh"] = [idf_intensity(cfg, rp, d) for rp, d in zip(s["design_rp_yr"].fillna(5).astype(int), s["design_duration_min"])]
    c_ = s["runoff_c"].values if "runoff_c" in s.columns else m["runoff_c"]     # DTM 地表覆盖加权的 C（有则用）
    s["q_design"] = hy.rational_q(s["area_m2"].values, s["design_i_mmh"].values, c_)
    qf = np.where(s["q_full"].values > 1e-9, s["q_full"].values, np.nan)
    s["ratio_design"] = s["q_design"].values / qf
    s["compliant"] = s["ratio_design"] <= float(D["ratio_threshold"])
    chk = s["check_rp_yr"].fillna(0).astype(int).values
    ci = np.array([idf_intensity(cfg, rp, d) if rp > 0 else np.nan for rp, d in zip(chk, s["design_duration_min"])])
    s["check_i_mmh"] = ci
    s["ratio_check"] = hy.rational_q(s["area_m2"].values, np.nan_to_num(ci), c_) / qf
    s.loc[chk <= 0, "ratio_check"] = np.nan
    s["rational_valid"] = (s["area_m2"] < 80e4) | (s["tc_min"] < 30)      # 3.3：A<80 ha 或 tc<30 min
    keep = ["gid", "design_class", "design_rp_yr", "check_rp_yr", "tc_multiplier", "tc_min", "design_duration_min", "design_i_mmh", "q_design", "ratio_design",
            "compliant", "check_i_mmh", "ratio_check", "rational_valid"]
    return s[keep]


def compliance_summary(es: pd.DataFrame, cfg: dict) -> dict:
    """按类别的达标份额；校核重现期不达标的公里数；推理公式适用范围外的管长。"""
    D = cfg["decision"]
    a = es[es["is_active"]].copy()
    tot = float(a["len_m"].sum())
    by = []
    for c in CLASS_ORDER:
        sub = a[a["design_class"] == c]
        if not len(sub):
            continue
        km = float(sub["len_m"].sum()) / 1000
        over = float(sub.loc[~sub["compliant"].fillna(False).astype(bool), "len_m"].sum()) / 1000
        chk = sub["ratio_check"].notna()
        over_chk = float(sub.loc[chk & (sub["ratio_check"] > D["ratio_threshold"]), "len_m"].sum()) / 1000 if chk.any() else None
        C = cfg["standards"]["classes"][c]
        by.append({"class": c, "class_zh": CLASS_ZH[c], "class_en": CLASS_EN[c], "rp": int(C["rp"]), "check_rp": int(C["check"]), "m": float(C["m"]),
                   "km": round(km, 1), "share_of_network": round(km / tot * 1000, 3) if tot else None, "over_km": round(over, 1),
                   "compliant_share": round(1 - over / km, 3) if km else None, "check_over_km": (round(over_chk, 1) if over_chk is not None else None),
                   "tc_p50_min": round(float(sub["tc_min"].median()), 1), "design_i_p50_mmh": round(float(sub["design_i_mmh"].median()), 1)})
    over_all = float(a.loc[~a["compliant"].fillna(False).astype(bool), "len_m"].sum()) / 1000
    return {"依据": "DMT WA-726-1 (2022) 表 3-2 / 3-3 / 3-6", "达标管长份额": round(1 - over_all * 1000 / tot, 3) if tot else None,
            "不达标km": round(over_all, 1), "在用管长km": round(tot / 1000, 1),
            "校核不达标km": round(sum(b["check_over_km"] or 0 for b in by), 1),
            "推理公式适用范围外管长km": round(float(a.loc[~a["rational_valid"].fillna(True).astype(bool), "len_m"].sum()) / 1000, 1),
            "按类别": by}


# ---------------------------------------------------------------- 建议的规范标注
REGULATORY = {
    "新增渗井群": ("WA-726-1 §4.12：新建系统原则上不允许渗井/渗沟；仅在地下水位深、渗透性高、可维护时经研究个案批准。本建议须附渗透试验与地下水位论证，否则改为调蓄或连通。",
                   "WA-726-1 §4.12: soakaways are generally not permitted in new systems; allowed case-by-case only with deep groundwater, high permeability and practical maintenance. Needs percolation tests and groundwater evidence, else switch to storage or connection."),
    "就地消纳（渗井/塘）": ("WA-726-1 §4.12 渗井需个案批准；§4.2 塘/离线池容积加 5% 沉沙，开放塘不得在建成区内；§4.1.2 无出口塘按 1/25 年蓄满。",
                          "WA-726-1 §4.12 soakaways need case-by-case approval; §4.2 ponds/off-line tanks +5% for sediment, no open lagoons in built-up areas; §4.1.2 lagoons without outlet sized for the 1-in-25-year storm."),
    "新增调蓄塘/池": ("WA-726-1 §4.2：优先在线调蓄；塘/离线池容积加 5% 沉沙；开放塘不得在建成区内或附近；须 NPV 论证；§4.1.2 无出口塘按 1/25 年蓄满，有出口塘按上一档重现期。",
                     "WA-726-1 §4.2: on-line storage preferred; ponds/off-line tanks +5% for sediment; no open lagoons in or near built-up areas; NPV required; §4.1.2 lagoons without outlet sized for 1-in-25-year, with outlet for the next higher standard."),
    "出水口增泵/改闸": ("WA-726-1 §6.3.5：出水口泵站按 1/10 年设计，至少 2 台泵（2 台各 66–100%，3 台各 50%），湿井有效容积 V = 0.25·Q·T；§4.8：设计高潮 +1.3 m NADD，出水口前设拍门/鸭嘴阀与 100 mm 栅，管顶低于大潮低潮位。",
                       "WA-726-1 §6.3.5: outfall pumping stations designed for 1-in-10-year, minimum two pumps (2 × 66–100% or 3 × 50%), wet-well active volume V = 0.25·Q·T; §4.8: design high tide +1.3 m NADD, flap/duckbill valve and 100 mm screen at the outfall, soffit below MLWS."),
    "泵站增容校核": ("WA-726-1 §6.3.4–6.3.5：峰值由 2–3 台工作泵承担，最少 2 台；设计流量另加 10% 余量；出水口泵站按 1/10 年。",
                     "WA-726-1 §6.3.4–6.3.5: peak covered by 2–3 duty pumps, minimum two; +10% allowance on design flow; outfall stations at 1-in-10-year."),
    "扩管改造": ("WA-726-1 §4.3：Manning n 0.011–0.013，流速 0.75–2.5 m/s，允许承压但 1/5 年时井盖下留 0.3 m；§3.3 汇水 >80 ha 或 tc >30 min 须用 SCS/水动力模型复核，推理公式只作筛查。",
                 "WA-726-1 §4.3: Manning n 0.011–0.013, velocity 0.75–2.5 m/s, surcharge allowed with 0.3 m clearance below covers at 1-in-5-year; §3.3 catchments >80 ha or tc >30 min need SCS/hydrodynamic verification, rational method is screening only."),
    "连通至有出路管网": ("WA-726-1 §4.1：新系统须接入既有网、蒸发塘或直排海；接入点、既有系统最高水位与最大允许排放量由部门指定。",
                         "WA-726-1 §4.1: new systems must discharge to the existing network, evaporation lagoons or the sea; connection point, maximum water level and permitted discharge are set by the Department."),
}


def annotate_proposals(props: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """给建议加规范条文与英文；渗井类建议需个案批准，优先级打折（config [standards].soakaway_priority_factor）。"""
    if not len(props):
        return props
    p = props.copy()
    p["regulatory"] = p["kind"].map(lambda k: REGULATORY.get(k, ("", ""))[0])
    p["regulatory_en"] = p["kind"].map(lambda k: REGULATORY.get(k, ("", ""))[1])
    f = float(cfg["standards"].get("soakaway_priority_factor", 0.85))
    soak = p["kind"].isin(["新增渗井群", "就地消纳（渗井/塘）"])
    p.loc[soak, "priority"] = (p.loc[soak, "priority"] * f).round(2)
    p.loc[soak, "regulatory_flag"] = "需个案批准"
    p["regulatory_flag"] = p.get("regulatory_flag", pd.Series([None] * len(p), index=p.index))
    return p


def main(argv=None):
    import tomllib
    ap = argparse.ArgumentParser(description="法定设计标准：参考数据抓取与 IDF 核对")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--build-ref", action="store_true", help="从 makani 抓分类要素并缓存")
    ap.add_argument("--check-idf", action="store_true", help="公式 vs 表格核对")
    a = ap.parse_args(argv)
    cfg = tomllib.loads(Path(a.config).read_text(encoding="utf-8"))
    if a.build_ref:
        print(json.dumps(build_ref(cfg), ensure_ascii=False))
    if a.check_idf or not a.build_ref:
        for rp in (5, 10, 25, 50, 100):
            row = [f"{d:>5}:{idf_intensity(cfg, rp, d):6.1f}/{idf_table(cfg, rp, d):6.1f}" for d in (5, 15, 30, 60, 180, 1440)]
            print(f"{rp:>3} yr  " + "  ".join(row) + "   (公式/表格 mm/h)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
