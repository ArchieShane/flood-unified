"""L1 / L2 图层（人工治理成果）质量检查：先查 L1，再查 L1 + L2。

  python tools/l1l2_audit.py <L1L2图层目录> <输出目录>

只读输入，不改原数据。结果写 <输出目录>/audit.json，问题要素写 <输出目录>/flags.gpkg（每类一层），供报告和 GIS 里逐个核对。
检查口径对照《一二维建模数据标准 v0.2》：能不能直接进一二维模型（结构统一、单位和基准、连通、不重复、级别正确）。
"""
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
DMT = ROOT / "20261019" / "Stormwater"
EXIST = Path("D:/阿布扎比/Parsons_Model Data Export/Existing Storage")
V11 = ROOT / "out" / "dgov" / "asset" / "rda" / "L1" / "v1.1-暂定" / "rda_L1_v1.1-暂定.gpkg"

# 关键水力属性在各来源里的字段名（按先后取第一个非空、非 0 的）
KEYS = {
    "Pipeline": {
        "diameter": ["D", "DIA", "DIAMETER", "DIM", "M_D", "PIPE_DIAME", "H/RISE", "HRISE"],
        "up_invert": ["INV_STRT", "SETINVSTRT", "INVSTRT", "ST_IV", "INVERT_LEV"],
        "dn_invert": ["INV_STP", "SETINVTSTP", "INVSTP", "SP_IV", "INVERT_L_1"],
        "roughness": ["MANN_N", "MANNN", "M_N"],
        "up_node": ["START_NODE", "STARTNODE", "START_NID", "START_ID", "STRNDID", "STRT_NDID", "START-NODE", "START_NDLB", "SRTNDID", "START_NOID", "STRNODEID"],
        "dn_node": ["STOP_NODE", "STOPNODE", "STOP_NID", "STOP_ID", "STPNDID", "STP_NDID", "STOP-NODE", "STOP_NDLB", "STP_NID_ID", "STOP_NO-ID", "STPNODEID"],
    },
}
RESULT_COLS = ["CAP_FULL", "V_MAX", "V_(MAX)", "VEL_MAX", "VEL_AVE", "MAX_HGL", "D/D", "D_MAX_R", "DMAX/RISE", "DP_MAX/RS", "QTODESCAP", "EXCAP_FULL", "VELOCITY", "D/R", "DEPTH_MID"]


def log(*a):
    print(*a, flush=True)


def num(s):
    return pd.to_numeric(s, errors="coerce")


def pick(df, cols):
    """逐行取第一个非空、非 0 的候选字段值，返回值和字段名。"""
    val = pd.Series(np.nan, index=df.index)
    src = pd.Series("", index=df.index, dtype=object)
    for c in cols:
        if c not in df:
            continue
        v = num(df[c])
        m = val.isna() & v.notna() & (v != 0)
        val[m] = v[m]
        src[m] = c
    return val, src


def endpoints(g):
    a = shapely.get_point(g.geometry.values, 0)
    b = shapely.get_point(g.geometry.values, -1)
    return np.c_[shapely.get_x(a), shapely.get_y(a)], np.c_[shapely.get_x(b), shapely.get_y(b)]


def load(base):
    L = {}
    for lv in ("L1", "L2"):
        for f in sorted((base / lv).glob("*.shp")):
            L[(lv, f.stem)] = gpd.read_file(f, engine="pyogrio")
    L[("root", "Catchment")] = gpd.read_file(base / "Catchment.shp", engine="pyogrio")
    for g in L.values():
        g["src_folder"] = g["src_folder"].astype(str)
    return L


def doc_consistency(base, L):
    x = pd.read_excel(next(base.glob("*.xlsx")), sheet_name="各层条数")
    out = []
    for r in x.itertuples():
        lv = {"L1": "L1", "L2": "L2", "汇水区": "root"}[r.层级]
        n = len(L[(lv, r.图层)]) if (lv, r.图层) in L else None
        out.append({"level": r.层级, "layer": r.图层, "doc": int(r.合并后条数), "actual": n, "match": n == int(r.合并后条数)})
    crs = {f"{k[0]}/{k[1]}": (g.crs.to_epsg() if g.crs else None) for k, g in L.items()}
    return {"counts": out, "all_match": all(o["match"] for o in out), "crs": crs}


def schema(L):
    out = {}
    for (lv, name), g in L.items():
        cols = [c for c in g.columns if c not in ("geometry", "src_comp", "src_folder", "src_file")]
        filled = {c: float((g[c].notna() & (g[c].astype(str).str.strip() != "")).mean()) for c in cols}
        res = [c for c in cols if c in RESULT_COLS]
        out[f"{lv}/{name}"] = {"columns": len(cols), "columns_under_5pct_filled": sum(v < 0.05 for v in filled.values()), "result_columns": res}
    return out


def pipe_attrs(P, label):
    """每个来源文件夹：关键属性填了多少、数值像什么单位 / 基准。"""
    rows = []
    vals = {}
    for k, cols in KEYS["Pipeline"].items():
        vals[k], vals[k + "_field"] = pick(P, cols)
    D = pd.DataFrame(vals)
    D["src_comp"], D["src_folder"] = P.src_comp, P.src_folder
    for (comp, fol), g in D.groupby(["src_comp", "src_folder"]):
        r = {"src_comp": comp, "src_folder": fol, "n": len(g)}
        for k in KEYS["Pipeline"]:
            r[f"{k}_filled"] = round(float(g[k].notna().mean()), 3)
            fields = g[k + "_field"][g[k + "_field"] != ""].value_counts()
            r[f"{k}_fields"] = ",".join(fields.index[:3])
        r["diameter_median"] = float(g.diameter.median()) if g.diameter.notna().any() else None
        inv = pd.concat([g.up_invert, g.dn_invert]).dropna()
        r["invert_median"] = float(inv.median()) if len(inv) else None
        r["invert_p05"] = float(inv.quantile(0.05)) if len(inv) else None
        r["invert_p95"] = float(inv.quantile(0.95)) if len(inv) else None
        rows.append(r)
    T = pd.DataFrame(rows)

    def dia_unit(m):
        if m is None or np.isnan(m):
            return "无"
        return "米" if m < 4 else ("英寸" if m < 120 else "毫米")

    def inv_datum(m):
        if m is None or np.isnan(m):
            return "无"
        return "NADD+100（资产库口径）" if m > 80 else ("可能是英尺" if m > 25 else "米（NADD）")
    T["diameter_unit_guess"] = T.diameter_median.map(dia_unit)
    T["invert_datum_guess"] = T.invert_median.map(inv_datum)
    return T, D


def near_dup_lines(P, tol=1.0):
    """不同来源文件夹之间：两端点都在 tol 内（不分方向）的管 = 同一根管重复录入。"""
    a, b = endpoints(P)
    key = np.r_[a, b]
    t = cKDTree(key)
    n = len(P)
    pairs = t.query_pairs(tol, output_type="ndarray")
    # 端点对 → 管对
    i, j = pairs[:, 0] % n, pairs[:, 1] % n
    m = i != j
    i, j = i[m], j[m]
    cand = set(zip(np.minimum(i, j), np.maximum(i, j)))
    fol = P.src_folder.values
    dup = []
    for x, y in cand:
        if fol[x] == fol[y]:
            continue
        ax, bx, ay, by = a[x], b[x], a[y], b[y]
        same = (np.hypot(*(ax - ay)) <= tol and np.hypot(*(bx - by)) <= tol) or (np.hypot(*(ax - by)) <= tol and np.hypot(*(bx - ay)) <= tol)
        if same:
            dup.append((x, y))
    return dup


def near_dup_points(G, tol):
    xy = np.c_[G.geometry.x, G.geometry.y]
    pairs = cKDTree(xy).query_pairs(tol, output_type="ndarray")
    fol = G.src_folder.values
    return [(x, y) for x, y in pairs if fol[x] != fol[y]]


def footprints(P, buf=15.0, min_share=0.3):
    """每个来源文件夹的管网覆盖面；两个来源覆盖面大面积重合 = 同一片区两套管网叠在一起。"""
    fp = {}
    for fol, g in P.groupby("src_folder"):
        fp[fol] = shapely.union_all(shapely.buffer(g.geometry.values, buf))
    names = list(fp)
    tree = shapely.STRtree([fp[n] for n in names])
    out = []
    for i, n in enumerate(names):
        for j in tree.query(fp[n]):
            if j <= i:
                continue
            inter = fp[n].intersection(fp[names[j]]).area
            if inter <= 0:
                continue
            s = inter / min(fp[n].area, fp[names[j]].area)
            if s >= min_share:
                out.append({"a": n, "b": names[j], "overlap_km2": round(inter / 1e6, 3), "share_of_smaller": round(s, 3)})
    return sorted(out, key=lambda r: -r["overlap_km2"])


def connectivity(lines, tol_join=1.0):
    """按几何端点（1 cm 取整）建图：连通块；「接缝」= 悬空端点 1 m 内有别的来源的端点、却没接上。"""
    a, b = endpoints(lines)
    ka = [tuple(x) for x in np.round(a, 2)]
    kb = [tuple(x) for x in np.round(b, 2)]
    par = {}

    def root(x):
        while par.setdefault(x, x) != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for u, v in zip(ka, kb):
        par[root(u)] = root(v)
    comp = Counter(root(u) for u in ka)
    sizes = sorted(comp.values(), reverse=True)
    deg = Counter(ka + kb)
    fol = list(lines.src_folder.values) * 2
    pts = np.r_[a, b]
    keys = ka + kb
    dangling = np.array([deg[k] == 1 for k in keys])
    t = cKDTree(pts)
    seams = []
    for i in np.where(dangling)[0]:
        for j in t.query_ball_point(pts[i], tol_join):
            if j != i and keys[j] != keys[i] and fol[j] != fol[i]:
                seams.append((i % len(lines), j % len(lines), float(np.hypot(*(pts[i] - pts[j])))))
                break
    return {"edges": len(lines), "components": len(comp), "largest": sizes[:5], "components_le_3_edges": sum(s <= 3 for s in sizes),
            "dangling_ends": int(dangling.sum()), "cross_source_seams_1m": len(seams)}, seams


def overlap_area(C):
    g = shapely.make_valid(C.geometry.values)
    tot = shapely.area(g).sum()
    u = shapely.union_all(g).area
    return tot, u


def main(base, out):
    base, out = Path(base), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    R = {"input": str(base), "checked": time.strftime("%Y-%m-%d %H:%M")}
    flags = {}
    log("读入")
    L = load(base)
    R["counts"] = {f"{k[0]}/{k[1]}": len(v) for k, v in L.items()}

    log("0 文档一致性、结构")
    R["doc"] = doc_consistency(base, L)
    R["schema"] = schema(L)

    # ---------------- L1 ----------------
    log("1 L1：关键属性、单位、基准")
    P = L[("L1", "Pipeline")]
    R["L1_pipe_multipart_or_empty"] = {"multipart": int((P.geom_type == "MultiLineString").sum()), "empty_or_null": int((P.geometry.isna() | P.geometry.is_empty).sum())}
    P = P[P.geometry.notna() & ~P.geometry.is_empty].explode(index_parts=False).reset_index(drop=True)
    T, D = pipe_attrs(P, "L1")
    T.to_csv(out / "L1_pipeline_attrs_by_source.csv", index=False, encoding="utf-8-sig")
    R["L1_pipe_attr_summary"] = {
        "pipes": len(P),
        "no_diameter": int(D.diameter.isna().sum()), "no_up_invert": int(D.up_invert.isna().sum()), "no_dn_invert": int(D.dn_invert.isna().sum()),
        "no_roughness": int(D.roughness.isna().sum()),
        "diameter_unit_guess": T.groupby("diameter_unit_guess").n.sum().to_dict(),
        "invert_datum_guess": T.groupby("invert_datum_guess").n.sum().to_dict(),
    }
    st = P[P.src_comp.isin(["Stormwater", "RD-A修复"])]
    R["L1_status"] = st.groupby("src_comp").STATUS.value_counts(dropna=False).rename("n").reset_index().astype(str).to_dict("records")
    bad = st[st.STATUS.astype(str).str.lower().isin(["abandoned", "expired", "removed", "inactive", "closed"])]
    flags["L1_管_资产库状态非在用"] = bad[["src_comp", "src_folder", "UNITID", "STATUS", "geometry"]]
    R["L1_status_not_in_use"] = len(bad)

    log("2 L1：重复（不同来源之间，1 mm 以外的）")
    dl = near_dup_lines(P, 1.0)
    R["L1_pipe_near_dup"] = {"pairs": len(dl), "by_source_pair": Counter(tuple(sorted((P.src_comp[x], P.src_comp[y]))) for x, y in dl).most_common()}
    R["L1_pipe_near_dup"]["by_source_pair"] = [[" / ".join(k), v] for k, v in R["L1_pipe_near_dup"]["by_source_pair"]]
    R["L1_pipe_near_dup"]["by_folder_pair_top"] = [[" / ".join(k), v] for k, v in Counter(tuple(sorted((P.src_folder[x], P.src_folder[y]))) for x, y in dl).most_common(15)]
    if dl:
        idx = sorted({y for _, y in dl})
        flags["L1_管_近似重复"] = P.loc[idx, ["src_comp", "src_folder", "src_file", "geometry"]]
    for name, tol in (("Manhole", 0.5), ("Pump", 30.0), ("Outfall", 10.0), ("WetWell", 30.0)):
        G = L[("L1", name)].reset_index(drop=True)
        d = near_dup_points(G, tol)
        R[f"L1_{name}_near_dup"] = {"tol_m": tol, "pairs": len(d), "by_source_pair": [[" / ".join(sorted((G.src_comp[x], G.src_comp[y]))), 1] for x, y in d][:0]}
        R[f"L1_{name}_near_dup"]["by_source_pair"] = [[" / ".join(k), v] for k, v in Counter(tuple(sorted((G.src_comp[x], G.src_comp[y]))) for x, y in d).most_common()]
        if d:
            idx = sorted({i for p in d for i in p})
            flags[f"L1_{name}_近似重复"] = G.loc[idx, ["src_comp", "src_folder", "src_file", "geometry"]]
    log("3 L1：来源覆盖面重叠（两套管网叠在同一片区）")
    R["L1_footprint_overlaps"] = footprints(P)

    log("4 L1：几何连通")
    lines = pd.concat([P[["src_comp", "src_folder", "geometry"]], L[("L1", "PressurePipe")][["src_comp", "src_folder", "geometry"]]], ignore_index=True)
    lines = gpd.GeoDataFrame(lines, geometry="geometry", crs=P.crs).explode(index_parts=False).reset_index(drop=True)
    R["L1_connectivity"], seams = connectivity(lines)
    if seams:
        a, _ = endpoints(lines)
        flags["L1_接缝_不同来源端点1m内未接上"] = gpd.GeoDataFrame(
            {"src_folder": [lines.src_folder[i] for i, _, _ in seams], "other": [lines.src_folder[j] for _, j, _ in seams], "gap_m": [g for _, _, g in seams]},
            geometry=gpd.points_from_xy([a[i][0] for i, _, _ in seams], [a[i][1] for i, _, _ in seams]), crs=P.crs)

    log("5 L1：级别是否站得住（L1 = 已建永久设施）")
    pumps = L[("L1", "Pump")].reset_index(drop=True)
    dmt_ps = gpd.read_file(DMT / "PUMP.shp", engine="pyogrio").to_crs(32640)
    t = cKDTree(np.c_[dmt_ps.geometry.x, dmt_ps.geometry.y])
    dd, _ = t.query(np.c_[pumps.geometry.x, pumps.geometry.y])
    pumps["dist_dmt_ps_m"] = dd
    far = pumps[(pumps.src_comp != "Stormwater") & (dd > 200)]
    R["L1_pump_not_near_dmt_station"] = {"model_pumps": int((pumps.src_comp != "Stormwater").sum()), "over_200m_from_any_DMT_station": len(far),
                                         "by_folder": far.src_folder.value_counts().to_dict()}
    flags["L1_泵_200m内无资产库泵站"] = far[["src_comp", "src_folder", "src_file", "dist_dmt_ps_m", "geometry"]]
    ponds = L[("L1", "Pond")].reset_index(drop=True)
    ex = pd.concat([gpd.read_file(f, engine="pyogrio").set_crs(32640, allow_override=True)[["geometry"]] for f in EXIST.rglob("*.shp")]
                   + [gpd.read_file(DMT / "POND.shp", engine="pyogrio").to_crs(32640)[["geometry"]]], ignore_index=True)
    exu = shapely.union_all(shapely.make_valid(ex.geometry.values))
    share = np.array([shapely.make_valid(g).intersection(exu).area / max(g.area, 1e-9) for g in ponds.geometry])
    ponds["share_in_existing"] = share
    unv = ponds[(ponds.src_comp != "Stormwater") & (share < 0.5)]
    R["L1_pond_existence"] = {"model_ponds": int((ponds.src_comp != "Stormwater").sum()), "not_in_existing_storage_or_dmt_pond": len(unv),
                              "by_folder": unv.src_folder.value_counts().to_dict()}
    flags["L1_塘_不在已建蓄水图层和资产库塘里"] = unv[["src_comp", "src_folder", "src_file", "share_in_existing", "geometry"]]
    R["L1_movable_pump"] = len(L[("L1", "MOVABLE_PUMP")])

    log("6 汇水区")
    C = L[("root", "Catchment")]
    inv = ~C.geometry.is_valid
    R["catchment_invalid_geometry"] = {"n": int(inv.sum()), "by_folder": C[inv].src_folder.value_counts().head(20).to_dict()}
    flags["汇水区_几何无效"] = C[inv][["src_comp", "src_folder", "src_file", "geometry"]]
    tot, u = overlap_area(C)
    R["catchment"] = {"n": len(C), "sum_area_km2": round(tot / 1e6, 2), "union_area_km2": round(u / 1e6, 2), "double_counted_km2": round((tot - u) / 1e6, 2)}
    per = []
    for fol, g in C.groupby("src_folder"):
        s, uu = overlap_area(g)
        per.append({"src_folder": fol, "src_comp": g.src_comp.iloc[0], "n": len(g), "sum_km2": round(s / 1e6, 3), "union_km2": round(uu / 1e6, 3),
                    "self_overlap_ratio": round(s / uu, 2) if uu else None})
    R["catchment_by_source"] = sorted(per, key=lambda r: -(r["self_overlap_ratio"] or 0))
    fp = {r["src_folder"]: shapely.union_all(shapely.make_valid(C[C.src_folder == r["src_folder"]].geometry.values)) for r in per}
    cross = []
    names = list(fp)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a_, b_ = fp[names[i]], fp[names[j]]
            if a_.intersects(b_):
                x = a_.intersection(b_).area
                if x > 1e4:
                    cross.append({"a": names[i], "b": names[j], "overlap_km2": round(x / 1e6, 3)})
    R["catchment_cross_source_overlap"] = sorted(cross, key=lambda r: -r["overlap_km2"])

    # ---------------- L1 + L2 ----------------
    log("7 L2：属性、级别、与 L1 的关系")
    L2p = L[("L2", "Pump")].reset_index(drop=True)
    dd, _ = t.query(np.c_[L2p.geometry.x, L2p.geometry.y])
    L2p["dist_dmt_ps_m"] = dd
    R["L2_pump_near_dmt_station"] = {"n": len(L2p), "within_100m_of_DMT_station": int((dd <= 100).sum()),
                                     "by_folder": L2p[dd <= 100].src_folder.value_counts().to_dict()}
    flags["L2_泵_100m内有资产库已建泵站"] = L2p[dd <= 100][["src_comp", "src_folder", "src_file", "dist_dmt_ps_m", "geometry"]]
    L2o = L[("L2", "Pond")].reset_index(drop=True)
    sh2 = np.array([shapely.make_valid(g).intersection(exu).area / max(g.area, 1e-9) for g in L2o.geometry])
    L2o["share_in_existing"] = sh2
    R["L2_pond_in_existing"] = {"n": len(L2o), "over_50pct_inside_existing_storage": int((sh2 >= 0.5).sum()),
                                "by_folder": L2o[sh2 >= 0.5].src_folder.value_counts().to_dict()}
    flags["L2_塘_与已建蓄水重合"] = L2o[sh2 >= 0.5][["src_comp", "src_folder", "src_file", "share_in_existing", "geometry"]]
    # L2 与 L1 近似重复（同一设施两层都有 → L1+L2 时算两遍）
    t1 = cKDTree(np.c_[pumps.geometry.x, pumps.geometry.y])
    d12, j12 = t1.query(np.c_[L2p.geometry.x, L2p.geometry.y])
    R["L2_pump_dup_in_L1"] = {"within_5m": int((d12 <= 5).sum()), "within_30m": int((d12 <= 30).sum())}
    pu = shapely.union_all(shapely.make_valid(ponds.geometry.values))
    s12 = np.array([shapely.make_valid(g).intersection(pu).area / max(g.area, 1e-9) for g in L2o.geometry])
    R["L2_pond_dup_in_L1"] = {"over_50pct": int((s12 >= 0.5).sum()), "by_folder": L2o[s12 >= 0.5].src_folder.value_counts().to_dict()}
    flags["L2_塘_与L1塘重合"] = L2o[s12 >= 0.5][["src_comp", "src_folder", "src_file", "geometry"]].assign(share=s12[s12 >= 0.5])
    # L2 的管能不能接上 L1
    a1, b1 = endpoints(lines)
    l1pts = np.r_[a1, b1]
    tl = cKDTree(l1pts)
    l2lines = pd.concat([L[("L2", "Pipeline")], L[("L2", "PressurePipe")]], ignore_index=True)
    l2lines = gpd.GeoDataFrame(l2lines, geometry="geometry", crs=P.crs)
    a2, b2 = endpoints(l2lines)
    l2nodes = pd.concat([L[("L2", k)] for k in ("Pump", "WetWell", "Outfall", "PressureJunction")], ignore_index=True)
    l2pts = np.c_[l2nodes.geometry.x, l2nodes.geometry.y]
    tn = cKDTree(np.r_[l2pts, a2, b2])
    da, _ = tl.query(a2)
    db, _ = tl.query(b2)
    # 端点接到 L1 管端 1 m 内、或接到 L2 自己的节点 / 管端 1 m 内 都算接上
    def own(p):
        return np.array([len(tn.query_ball_point(x, 1.0)) > 1 for x in p])
    oa, ob = own(a2), own(b2)
    loose = ~((da <= 1) | oa) | ~((db <= 1) | ob)
    R["L2_lines_connect"] = {"n": len(l2lines), "both_ends_attached": int((~loose).sum()), "some_end_loose": int(loose.sum()),
                             "touch_L1_any_end": int(((da <= 1) | (db <= 1)).sum())}
    flags["L2_管_端点悬空"] = l2lines[loose][["src_comp", "src_folder", "geometry"]]
    # L2 属性
    def filled(g, cols):
        return {c: round(float(num(g[c]).fillna(0).ne(0).mean()), 3) for c in cols if c in g}
    R["L2_attrs"] = {
        "Pump_AECOM": filled(L2p[L2p.src_comp == "AECOM"], ["DES_Q_LS", "DES_H_M"]),
        "Pond_AECOM": filled(L2o[L2o.src_comp == "AECOM"], ["VOL_M3", "AREA_M2", "BOT_EL_M", "TOP_EL_M"]),
        "Pump_Parsons_columns": [c for c in L2p.columns if L2p[L2p.src_comp == "Parsons"][c].notna().any()][:40],
        "Pond_Parsons_columns": [c for c in L2o.columns if L2o[L2o.src_comp == "Parsons"][c].notna().any()][:40],
        "L2_stage": L[("L2", "Pump")].get("L2_STAGE", pd.Series(dtype=str)).value_counts().to_dict(),
    }
    # 合起来
    both = pd.concat([lines, gpd.GeoDataFrame(l2lines[["src_comp", "src_folder", "geometry"]], crs=P.crs)], ignore_index=True)
    R["L1L2_connectivity"], _ = connectivity(gpd.GeoDataFrame(both, geometry="geometry", crs=P.crs).explode(index_parts=False).reset_index(drop=True))

    # ---------------- RD-A ----------------
    log("8 RD-A：修复成果对照治理资产 v1.1")
    ra = P[P.src_comp == "RD-A修复"].reset_index(drop=True)
    rn = L[("L1", "Manhole")]
    rn = rn[rn.src_comp == "RD-A修复"]
    a_, b_ = endpoints(ra)
    tn = cKDTree(np.c_[rn.geometry.x, rn.geometry.y])
    dA, _ = tn.query(a_)
    dB, _ = tn.query(b_)
    v = gpd.read_file(V11, layer="link")
    R["RDA"] = {"pipes_repair": len(ra), "pipes_v11": len(v), "repair_nodes": len(rn),
                "pipe_ends_not_on_node_1mm": int(((dA > 0.001) | (dB > 0.001)).sum()),
                "pipe_ends_over_0.5m_from_node": int(((dA > 0.5) | (dB > 0.5)).sum())}
    R["RDA_connectivity"], _ = connectivity(gpd.GeoDataFrame(ra[["src_comp", "src_folder", "geometry"]], crs=P.crs).explode(index_parts=False).reset_index(drop=True))

    R["seconds"] = round(time.time() - t0)
    (out / "audit.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    fg = out / "flags.gpkg"
    if fg.exists():
        fg.unlink()
    for k, g in flags.items():
        if len(g):
            gpd.GeoDataFrame(g, geometry="geometry", crs=32640).to_file(fg, layer=k, driver="GPKG", engine="pyogrio")
    R["flags"] = {k: len(g) for k, g in flags.items()}
    (out / "audit.json").write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log(json.dumps({k: R[k] for k in ("flags", "seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1], sys.argv[2])
