"""开源治理引擎 PoC 第 1 步：读预处理库快照 → 要素表 + 重合点簇（连通图的「点」）。

  python tools/oss_gov/o1_graph.py [e1|c2]      重合口径见 common.MODES，默认 c2

1 读要素   pyogrio（arrow）读 prep.gdb 里进 UN 的 16 个类，按 s3_mapping.MAP 标 Expanded 目标类 / 资产组 / 资产类型
2 取坐标   点要素 1 个坐标；线要素每个部分的每个折点，标出要素端点（首部件首点 / 末部件末点）和中间折点
3 重合点簇 所有坐标进 KD 树，query_pairs 找容差内的点对（e1：欧氏 ≤ 0.001 m；c2：各轴 ≤ 0.002 m），并查集（稀疏图连通分量）合成簇。
           不用四舍五入取网格：网格边界两侧相距 < 0.001 m 的点会被拆开，同格相距 > 0.001 m 的点会被并上；这里两种情况都统计出来对照。
           链式传递（A–B、B–C 在容差内而 A–C 不在）照样并成一簇（推断：UN 的 cluster 容差处理同样会传递），簇直径 > 0.001 m 的单独统计。
4 中跨     点落在线的中间折点上 = 中跨连接（Expanded 的线全部是 ANY_VERTEX 策略，见 B_EdgeConnectivity）；
           点落在线段中间但没有折点 = UN 不建连通（只统计，STRtree dwithin 0.001 m）
5 关联     System Outflow ↔ 锚点（渗井 / BMP 点）的节点—节点连通关联，按 s4b_fix_assoc 的做法：锚点坐标与 Catch Basin / BMP Point 重合
结构网络（StructureJunction 检查井 / StructureBoundary 塘）不进重合点簇：结构网络这里没有线，井和井内流道是包含关系不是连通（推断，与 UN 错误表无「井—流道点位重叠」一致）。
产物：out/features.parquet、out/members.parquet（簇成员）、out/assoc.parquet、out/o1_graph.json
"""
import sys

import numpy as np
import pandas as pd
import pyogrio
import shapely
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
import common as C  # noqa: E402

LOAD = ["PIPE", "MH_VAULT", "MH_CHANNEL", "CATCH_BASIN", "END_CAP", "OUTFALL", "FLAP_GATE", "PUMP", "WETWELL", "BMP_POINT", "POND",
        "GEN_CONNECTION", "SYSTEM_OUTFLOW"]


def main():
    C.setup(sys.argv)
    T = C.Timer()
    R = {"prep_classes": C.prep_classes()}
    R["not_loaded"] = {k: v for k, v in R["prep_classes"].items() if k not in LOAD}

    # 1 读要素 —— 线在前（gid 0..NL-1），点在后
    dfs = {lay: C.read_prep(lay) for lay in LOAD}
    T.lap("1 读预处理库（pyogrio）")
    lines = dfs.pop("PIPE")
    pts = pd.concat([d for k, d in dfs.items() if k != "POND"], ignore_index=True)
    ponds = dfs["POND"]
    feat = pd.concat([lines.drop(columns="geometry"), pts.drop(columns="geometry"), ponds.drop(columns="geometry")], ignore_index=True)
    feat["gid"] = np.arange(len(feat))
    feat["kind"] = np.where(feat.un_class == "StormwaterLine", "line",
                            np.where(feat.un_class.isin(["StructureJunction", "StructureBoundary"]), "struct", "point"))
    NL = len(lines)
    geoms = np.concatenate([lines.geometry.values, pts.geometry.values, ponds.geometry.values])
    feat["globalid"] = C.norm_gid(feat["GlobalID"])
    R["features"] = feat.groupby(["un_class", "ag", "at"]).size().reset_index().values.tolist()
    R["unmapped"] = int(feat.un_class.isna().sum())

    # 2 坐标
    parts, pidx = shapely.get_parts(lines.geometry.values, return_index=True)
    xy, vidx = shapely.get_coordinates(parts, return_index=True)
    lgid = pidx[vidx]                                     # 每个折点属于哪条线（gid）
    n = len(xy)
    first_of_part = np.r_[True, vidx[1:] != vidx[:-1]]
    last_of_part = np.r_[vidx[1:] != vidx[:-1], True]
    first_of_feat = np.r_[True, lgid[1:] != lgid[:-1]]
    last_of_feat = np.r_[lgid[1:] != lgid[:-1], True]
    is_end = (first_of_feat & first_of_part) | (last_of_feat & last_of_part)
    vtx = np.arange(n) - np.maximum.accumulate(np.where(first_of_feat, np.arange(n), 0))
    is_pt = (feat.kind.values == "point")
    pgid = feat.gid.values[is_pt]
    pxy = shapely.get_coordinates(geoms[pgid])
    allxy = np.vstack([xy, pxy])
    occ = pd.DataFrame({"gid": np.r_[lgid, pgid], "vtx": np.r_[vtx, np.zeros(len(pgid), int)], "is_end": np.r_[is_end, np.ones(len(pgid), bool)],
                        "is_line": np.r_[np.ones(n, bool), np.zeros(len(pgid), bool)]})
    R["coords"] = {"line_vertices": int(n), "line_endpoints": int(is_end.sum()), "points": int(len(pgid)),
                   "multipart_lines": int((shapely.get_num_geometries(lines.geometry.values) > 1).sum())}
    T.lap("2 取坐标")

    # 3 重合点簇
    tree = cKDTree(allxy)
    _, p_norm, r_ = C.MODES[C.MODE]
    pairs = tree.query_pairs(r=r_, p=p_norm, output_type="ndarray")
    T.lap("3a KD 树找容差内点对")
    N = len(allxy)
    g = coo_matrix((np.ones(len(pairs), np.int8), (pairs[:, 0], pairs[:, 1])), shape=(N, N))
    ncl, lab = connected_components(g, directed=False)
    occ["cid"] = lab
    d = np.hypot(*(allxy[pairs[:, 0]] - allxy[pairs[:, 1]]).T)
    # 与「四舍五入到 0.001 m 取格」对照
    key = np.round(allxy, 3)
    kx = pd.Series(list(map(tuple, key)))
    split_by_round = int((kx.values[pairs[:, 0]] != kx.values[pairs[:, 1]]).sum())
    kid = pd.factorize(pd.MultiIndex.from_arrays([key[:, 0], key[:, 1]]))[0]
    tmp = pd.DataFrame({"k": kid, "c": lab})
    merged_by_round = int((tmp.groupby("k").c.nunique() > 1).sum())
    # 簇直径（链式传递）
    big = pd.Series(lab).value_counts()
    big = big[big > 1].index.values
    sel = np.isin(lab, big)
    cx = pd.DataFrame({"c": lab[sel], "x": allxy[sel, 0], "y": allxy[sel, 1]}).groupby("c").agg(x0=("x", "min"), x1=("x", "max"), y0=("y", "min"), y1=("y", "max"))
    diam = np.hypot(cx.x1 - cx.x0, cx.y1 - cx.y0)
    dc = np.abs(allxy[pairs[:, 0]] - allxy[pairs[:, 1]]).max(1)
    R["clusters"] = {"mode": C.MODE, "pairs_within_tol": int(len(pairs)), "pairs_euclid_gt_0.001": int((d > C.TOL).sum()),
                     "pairs_cheb_0.001_to_0.002": int(((dc > C.TOL) & (dc <= 2 * C.TOL + 1e-7)).sum()), "pairs_exactly_equal": int((d == 0).sum()), "pairs_0_to_0.0005": int(((d > 0) & (d <= 0.0005)).sum()),
                     "pairs_0.0005_to_0.001": int(((d > 0.0005) & (d <= C.TOL)).sum()), "clusters_size_gt1": int(len(big)),
                     "clusters_diameter_gt_tol（链式）": int((diam > C.TOL).sum()),
                     "rounding_would_split_pairs": split_by_round, "rounding_cells_spanning_2plus_clusters": merged_by_round}
    T.lap("3b 连通分量 + 容差诊断")

    # 只留下与连通有关的簇：≥2 个不同要素，或同一条线 ≥2 个折点（重复折点检查用）
    grp = occ.groupby("cid").gid
    nfeat = grp.nunique()
    nocc = grp.size()
    keep = nfeat.index[(nfeat >= 2) | (nocc > nfeat)]
    mem = occ[occ.cid.isin(keep)].copy()
    mem["x"] = allxy[mem.index.values, 0]
    mem["y"] = allxy[mem.index.values, 1]
    mem.to_parquet(C.OUT / "members.parquet")
    R["clusters"]["clusters_kept"] = int(len(keep))

    # 4 中跨
    pm = mem[~mem.is_line]
    lm = mem[mem.is_line]
    mid_cids = set(lm.loc[~lm.is_end, "cid"])
    R["midspan"] = {"points_on_interior_vertex": int(pm.cid.isin(mid_cids).sum()),
                    "line_endpoint_on_other_line_interior_vertex": int(lm[lm.is_end].cid.isin(mid_cids).sum())}
    ltree = shapely.STRtree(lines.geometry.values)
    q = ltree.query(geoms[pgid], predicate="dwithin", distance=r_ if p_norm == 2.0 else r_ * 2 ** 0.5)
    on_line = pd.DataFrame({"p": pgid[q[0]], "l": q[1]})
    pc = occ.iloc[n:].set_index("gid").cid                  # 点 → 簇
    lc = occ.iloc[:n].groupby(["gid", "cid"]).size().reset_index()[["gid", "cid"]]
    on_line["pc"] = pc.reindex(on_line.p).values
    hit = on_line.merge(lc, left_on=["l", "pc"], right_on=["gid", "cid"], how="left", indicator=True)
    nov = hit[hit._merge == "left_only"]
    R["midspan"]["points_on_segment_without_vertex（UN 不连）"] = int(len(nov))
    R["midspan"]["examples_on_segment_without_vertex"] = feat.loc[nov.p.values[:10], ["layer", "assetid", "globalid"]].assign(
        line=feat.loc[nov.l.values[:10], "assetid"].values).values.tolist()
    T.lap("4 中跨 / 线段上无折点的点")

    # 5 关联：System Outflow ↔ 锚点
    so = feat[(feat.layer == "SYSTEM_OUTFLOW")]
    anc = feat[(feat.ag.isin(["Catch Basin", "BMP Point"]))]
    axy = shapely.get_coordinates(geoms[anc.gid.values])
    at = cKDTree(axy)
    dd, ii = at.query(np.c_[so.anchor_x.values, so.anchor_y.values], distance_upper_bound=r_, p=p_norm)
    ok = np.isfinite(dd)
    assoc = pd.DataFrame({"from_gid": anc.gid.values[ii[ok]], "to_gid": so.gid.values[ok], "type": "JJ"})
    # 0.01 m 内重合、规则表允许互接的两个点（untools Associate 建「节点 — 节点连通」关联，子网追踪会走它；
    # RD-64 两个相距 9.5 mm 的雨水口靠它连进 SMI-0729，第一版开源没建，9 根管追不到）
    rr = pyogrio.read_dataframe(C.AP, layer="B_Rules", read_geometry=False)
    jr = {(a_, b_, c_, d_) for a_, b_, c_, d_ in rr.loc[rr.rule_type == "Junction Junction Connectivity",
                                                       ["from_asset_group", "from_asset_type", "to_asset_group", "to_asset_type"]].itertuples(index=False)}
    pp = feat[feat.kind == "point"]
    pxy = shapely.get_coordinates(geoms[pp.gid.values])
    have = {frozenset(x) for x in zip(assoc.from_gid, assoc.to_gid)}
    extra = []
    for i, j in sorted(cKDTree(pxy).query_pairs(0.01)):
        a_, b_ = pp.iloc[i], pp.iloc[j]
        if frozenset((a_.gid, b_.gid)) in have:
            continue
        if (a_.ag, a_["at"], b_.ag, b_["at"]) in jr:
            extra.append((a_.gid, b_.gid, "JJ"))
        elif (b_.ag, b_["at"], a_.ag, a_["at"]) in jr:            # 方向按规则表（o2 按 from → to 查规则）
            extra.append((b_.gid, a_.gid, "JJ"))
    if extra:
        assoc = pd.concat([assoc, pd.DataFrame(extra, columns=["from_gid", "to_gid", "type"])], ignore_index=True)
    assoc.to_parquet(C.OUT / "assoc.parquet")
    R["assoc"] = {"system_outflow": int(len(so)), "anchor_found": int(ok.sum()), "anchor_not_found": int((~ok).sum()), "coincident_jj": len(extra)}

    # 要素表：点坐标、线的外包框（脏区近似用）
    b = shapely.bounds(geoms)
    feat[["minx", "miny", "maxx", "maxy"]] = b
    feat["length"] = shapely.length(geoms)
    feat["nparts"] = shapely.get_num_geometries(geoms)
    feat["is_simple"] = shapely.is_simple(geoms)
    feat.drop(columns=["GlobalID"]).to_parquet(C.OUT / "features.parquet")
    import geopandas as gpd
    gpd.GeoDataFrame({"gid": np.arange(NL)}, geometry=lines.geometry.values, crs=32640).to_parquet(C.OUT / "lines_geom.parquet")
    T.lap("5 关联 + 写出")
    R["timing_s"] = T.total()
    C.jdump(R, C.OUT / "o1_graph.json")
    print(R["clusters"], R["midspan"], R["assoc"], flush=True)


if __name__ == "__main__":
    main()
