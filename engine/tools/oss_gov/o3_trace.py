"""开源治理引擎 PoC 第 3 步：子网控制器、子网命名、子网追踪、29 子网名不一致。

  python tools/oss_gov/o3_trace.py [e1|c2]

1 控制器   OUTFALL（Outfall / Non-Permit Outfall）255 个 + SYSTEM_OUTFLOW（System Outflow / Outflow）820 个
2 命名     复现 tools/exp_gov/s5_validate.py f_subnet 的做法（第十四轮版；只读参照，不运行它）：
           管线首末点坐标 round(…, 2) 并查集，中间折点上有别的线端点或有点的也并 = 连通系统；出水口按装载顺序，一个系统一个名 SMA-nnnn（孤立出水口单独一个名）；
           System Outflow 按锚点坐标所在系统另起一套编号 SMI-nnnn（两套编号互不相通，所以同一系统里既有出水口又有 SMI 时名字不同 → UN 报 29）
3 追踪     层级 Stormwater Management Area：MESH、汇型控制器；条件障碍 Asset State = 0 或 1（B_Subnetwork_ConditionBarriers），
           障碍要素本身算进子网（include_barriers = 1）但不从它往外走。端子 / 方向不分（推断：PoC 未建端子模型）。
           按子网名把同名控制器一起作起点做广度优先；走到别的名字的控制器 = 29（两个名字都记）
4 四种口径
   A 纯几何连通：容差内重合就连（不看规则），不阻断
   B 规则连通：只走 o2 判定为合法的连通边，不阻断；21 重复折点的线不可通行（见 no_trav）
   C 类 UN：B + 子网内含任一有错误的要素（8 / 9 / 10 / 13 / 19 / 25；20、21 不挡、但那根线不可通行，见 ERR_BLOCK / NO_TRAV）或有 29 → 整个子网不更新（线的子网名 = Unknown）
   D 类 UN（脏区近似）：同 C，但「有错误」按脏区范围算 = 错误要素外包框（外扩 1 个容差）碰到的所有要素（推断：UN 的脏区是面，挡的是范围）
产物：out/<口径>/subnet.parquet（每个要素在四种口径下的子网名）、out/<口径>/controllers.parquet、out/<口径>/o3_trace.json
"""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyogrio
import shapely
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import breadth_first_order

sys.path.insert(0, str(Path(__file__).parent))
import common as C  # noqa: E402

# 挡子网更新的错误码。21 重复折点不挡（推断，由 UN 第八轮反推：SMA-0052 / 0066 / 0089 / 0099、SMI-0730 里都有 UN 自己报的 21，
# 这 5 个子网 UN 都更新成功了；而含 8 / 9 / 10 / 19 / 25 的子网全部更新失败）
ERR_BLOCK = (8, 9, 10, 13, 19, 25, 1, 5)
# 20 自相交也不挡（第十四轮反推：UN 报了 2 处 20，所在子网 SMA-0099 照常更新，只有那 2 根管是 Unknown）；20、21 的线本身不可通行（见 NO_TRAV）
NO_TRAV = (20, 21)


def name_controllers(feat):
    """复现 s5_validate.f_subnet 的命名（Python 内置 round，与 arcpy 脚本同一套舍入）。"""
    par = {}

    def root(x):
        while par.setdefault(x, x) != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    lines = pyogrio.read_dataframe(C.PREP, layer="PIPE", columns=[], use_arrow=True).geometry.values
    xy, idx = shapely.get_coordinates(lines, return_index=True)
    first = np.r_[0, np.flatnonzero(idx[1:] != idx[:-1]) + 1]
    last = np.r_[first[1:] - 1, len(idx) - 1]
    key = lambda x, y: (round(float(x), 2), round(float(y), 2))
    # 第十四轮（同 s5_validate.f_subnet）：线的中间折点只有在那里有别的线的端点、或有点（设备 / 节点）时才相连
    hubs = {key(*xy[a]) for a in first} | {key(*xy[b]) for b in last}
    for lay in ("MH_CHANNEL", "CATCH_BASIN", "OUTFALL", "FLAP_GATE", "PUMP", "WETWELL", "BMP_POINT", "SYSTEM_OUTFLOW", "END_CAP", "GEN_CONNECTION"):
        try:
            g = pyogrio.read_dataframe(C.PREP, layer=lay, columns=[], use_arrow=True).geometry.values
        except Exception:
            continue
        hubs |= {key(x, y) for x, y in shapely.get_coordinates(g)}
    for a, b in zip(first, last):
        ra = root(key(*xy[a]))
        par[ra] = root(key(*xy[b]))
        for v in range(a + 1, b):
            k = key(*xy[v])
            if k in hubs:
                par[root(k)] = root(ra)
    rows = []
    of = feat[feat.layer == "OUTFALL"].sort_values("fid")
    ofg = pyogrio.read_dataframe(C.PREP, layer="OUTFALL", columns=[], use_arrow=True).geometry.values
    names = {}
    for g, fid in zip(of.gid, of.fid):
        p = shapely.get_coordinates(ofg[fid])[0]
        k = (round(float(p[0]), 2), round(float(p[1]), 2))
        s = names.setdefault(root(k) if k in par else ("isolated", k), f"SMA-{len(names) + 1:04d}")
        rows.append((g, s))
    so = feat[feat.layer == "SYSTEM_OUTFLOW"].sort_values("fid")
    names_i = {}
    for g, ax, ay, aid in zip(so.gid, so.anchor_x, so.anchor_y, so.assetid):
        k = (round(float(ax), 2), round(float(ay), 2)) if ax == ax else None
        r = root(k) if k in par else ("isolated", aid)
        s = names.get(r) or names_i.setdefault(r, f"SMI-{len(names_i) + 1:04d}")     # 第十三轮起：和出水口同一系统的，用出水口的子网名
        rows.append((g, s))
    iso = sum(1 for k in names if isinstance(k, tuple) and k[0] == "isolated")
    return pd.DataFrame(rows, columns=["gid", "name"]), iso


def trace(N, edges, ctrl, barrier, blocked_feats=None):
    """按子网名多源广度优先；障碍点只进不出。返回 {名: 访问到的要素 gid 数组}、29 冲突对。"""
    u, v = edges.u.values, edges.v.values
    src = np.r_[u, v]
    dst = np.r_[v, u]
    keep = ~barrier[src]                                   # 障碍要素不往外走
    A = csr_matrix((np.ones(keep.sum(), np.int8), (src[keep], dst[keep])), shape=(N, N))
    by_name = ctrl.groupby("name").gid.apply(list).to_dict()
    ctrl_name = dict(zip(ctrl.gid, ctrl.name))
    visited, conflicts = {}, set()
    for nm, gs in by_name.items():
        seen = []
        for g in gs:
            if barrier[g]:
                seen.append(np.array([g]))
                continue
            seen.append(breadth_first_order(A, g, directed=True, return_predecessors=False))
        vis = np.unique(np.concatenate(seen))
        visited[nm] = vis
        for g in vis:
            o = ctrl_name.get(g)
            if o is not None and o != nm:
                conflicts.add(tuple(sorted((nm, o))))
    return visited, conflicts


def assign(N, visited, ok_names):
    name = np.full(N, "Unknown", dtype=object)
    cnt = np.zeros(N, np.int32)
    for nm, vis in visited.items():
        if nm in ok_names:
            cnt[vis] += 1
            name[vis] = np.where(cnt[vis] > 1, "Multiple", nm)
    return name


def main():
    C.setup(sys.argv)
    T = C.Timer()
    feat = pd.read_parquet(C.OUT / "features.parquet")
    err = pd.read_parquet(C.OUT / "errors.parquet")
    N = len(feat)
    ctrl, iso = name_controllers(feat)
    ctrl.to_parquet(C.OUT / "controllers.parquet")
    T.lap("1–2 控制器与命名")
    R = {"controllers": len(ctrl), "names": int(ctrl.name.nunique()), "names_SMA": int(ctrl.name.str.startswith("SMA").sum()),
         "isolated_outfalls": iso}
    barrier = feat.assetstate.isin([0, 1]).values & (feat.kind != "struct").values
    R["barriers"] = feat[barrier].groupby("un_class").size().to_dict()

    bad = np.zeros(N, bool)
    e = err[err.code.isin(ERR_BLOCK)]
    bad[e.origin_gid.values] = True
    bad[e.dest_gid.values[e.dest_gid.values >= 0]] = True
    # 脏区近似：错误要素外包框外扩 1 个容差，碰到的要素
    bb = feat.loc[bad, ["minx", "miny", "maxx", "maxy"]].values
    boxes = shapely.box(bb[:, 0] - C.TOL, bb[:, 1] - C.TOL, bb[:, 2] + C.TOL, bb[:, 3] + C.TOL)
    allb = shapely.box(*feat[["minx", "miny", "maxx", "maxy"]].values.T)
    hit = shapely.STRtree(allb).query(boxes, predicate="intersects")[1]
    bad_area = np.zeros(N, bool)
    bad_area[hit] = True
    bad_area &= (feat.kind != "struct").values
    R["error_features"] = int(bad.sum())
    R["dirty_area_features"] = int(bad_area.sum())

    # 21 重复折点的线：不挡子网更新，但追踪不经过它（推断，由 UN 第八轮反推：SMA-0089 / 0052 / 0099 在 21 线以外的 144 条线 UN 都是 Unknown，
    # 去掉 21 线后这些线正好追不到）。只用于 B / C / D；A 是纯几何，不看错误
    no_trav = set(err.loc[err.code.isin(NO_TRAV), "origin_gid"])
    R["not_traversable_21"] = len(no_trav)
    out = pd.DataFrame({"gid": feat.gid, "kind": feat.kind})
    for var, ed in (("A", "edges_geom"), ("B", "edges_rule")):
        edges = pd.read_parquet(C.OUT / f"{ed}.parquet")
        if var == "B":
            edges = edges[~edges.u.isin(no_trav) & ~edges.v.isin(no_trav)]
        vis, conf = trace(N, edges, ctrl, barrier)
        T.lap(f"追踪 {var}")
        names = set(vis)
        out[var] = assign(N, vis, names)
        R[var] = {"conflicts_29": sorted(conf), "n_conflict_pairs": len(conf)}
        if var == "B":
            in29 = {x for p in conf for x in p}
            failC = {nm for nm, v in vis.items() if nm in in29 or bad[v].any()}
            failD = {nm for nm, v in vis.items() if nm in in29 or bad_area[v].any()}
            out["C"] = assign(N, vis, names - failC)
            out["D"] = assign(N, vis, names - failD)
            R["C"] = {"failed": len(failC), "failed_by_29": len(in29), "updated": len(names - failC), "failed_names": sorted(failC)}
            R["D"] = {"failed": len(failD), "updated": len(names - failD), "failed_names": sorted(failD)}
            # 29 当错误记一条（每个冲突对一条；UN 是在每个冲突控制器处建脏区）
            e29 = pd.DataFrame([(29, -1, -1, -1, f"{a}|{b}") for a, b in sorted(conf)], columns=err.columns)
            e29.to_parquet(C.OUT / "errors_29.parquet")
    lines = out.kind == "line"
    for var in "ABCD":
        s = out.loc[lines, var]
        R[var]["lines_in_subnet"] = int((~s.isin(["Unknown", "Multiple"])).sum())
        R[var]["lines_multiple"] = int((s == "Multiple").sum())
        R[var]["share"] = round(R[var]["lines_in_subnet"] / int(lines.sum()), 4)
    out.to_parquet(C.OUT / "subnet.parquet")
    R["timing_s"] = T.total()
    C.jdump(R, C.OUT / "o3_trace.json")
    print({k: (v if not isinstance(v, dict) else {a: b for a, b in v.items() if a not in ("failed_names", "conflicts_29")}) for k, v in R.items()}, flush=True)


if __name__ == "__main__":
    main()
