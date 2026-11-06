"""监端第一步：把 sw_edge 变成有向无环图，沿流向累加汇水面积，给每个节点找到它的终点（出水口/渗井/泵/断头）。"""
from __future__ import annotations
import numpy as np
import pandas as pd
import networkx as nx


TERMINAL_KIND = {
    "outfall": "出水口",
    "soakaway": "渗井",
    "ps_pump": "泵站",
    "sw_cappedend": "封堵端",
}


def orient_edges(edges: pd.DataFrame, vertices: pd.DataFrame, node_outfall: pd.DataFrame) -> pd.DataFrame:
    """给每条边定 u→v。优先资产/标高方向；未知方向的用「到出水口跳数」由高向低；再不行按 source→target 并打标。"""
    e = edges.copy()
    known = e["dir_source"].ne("unknown") & e["from_node"].notna() & e["to_node"].notna()
    e["u"] = np.where(known, e["from_node"], e["source"])
    e["v"] = np.where(known, e["to_node"], e["target"])
    e["orient"] = np.where(known, e["dir_source"], "guess")

    unk = ~known
    if unk.any():
        hops = dict(zip(node_outfall["node"].astype(int), node_outfall["hops"].astype(int)))
        hu = e.loc[unk, "source"].map(hops)
        hv = e.loc[unk, "target"].map(hops)
        # 标高可用时按标高：invert_up 对应 source 端（建图时的约定），高→低
        by_inv = unk & e["invert_valid"].fillna(False).astype(bool) & (e["invert_up"] != e["invert_dn"])
        flip_inv = by_inv & (e["invert_up"] < e["invert_dn"])
        e.loc[flip_inv, ["u", "v"]] = e.loc[flip_inv, ["target", "source"]].values
        e.loc[by_inv, "orient"] = "by_invert"
        # 跳数可用时按跳数
        rest = unk & ~by_inv
        both = rest & hu.reindex(e.index).notna() & hv.reindex(e.index).notna()
        flip_h = both & (hu.reindex(e.index) < hv.reindex(e.index))
        e.loc[flip_h, ["u", "v"]] = e.loc[flip_h, ["target", "source"]].values
        e.loc[both, "orient"] = "by_hops"
    e["u"] = e["u"].astype("int64")
    e["v"] = e["v"].astype("int64")
    return e


def build_dag(e: pd.DataFrame) -> tuple[nx.DiGraph, list[int]]:
    """建有向图并打断环。返回 (DAG, 被移除的边 gid 列表)。"""
    g = nx.DiGraph()
    g.add_nodes_from(pd.unique(np.concatenate([e["u"].values, e["v"].values])).tolist())
    g.add_edges_from(zip(e["u"].values.tolist(), e["v"].values.tolist(), ({"gid": int(x)} for x in e["gid"].values)))
    # 自环
    removed = []
    for u, v in list(nx.selfloop_edges(g)):
        removed.append(g[u][v]["gid"])
        g.remove_edge(u, v)
    # 环：每个强连通分量里反复去掉一条边直到无环。优先去掉方向为 guess 的。
    orient = dict(zip(e["gid"], e["orient"]))
    for comp in [c for c in nx.strongly_connected_components(g) if len(c) > 1]:
        sub = g.subgraph(comp).copy()
        while True:
            try:
                cyc = nx.find_cycle(sub)
            except nx.NetworkXNoCycle:
                break
            cand = sorted(cyc, key=lambda uv: 0 if orient.get(sub[uv[0]][uv[1]]["gid"]) == "guess" else 1)
            u, v = cand[0][0], cand[0][1]
            removed.append(sub[u][v]["gid"])
            sub.remove_edge(u, v)
            g.remove_edge(u, v)
    assert nx.is_directed_acyclic_graph(g)
    return g, removed


def accumulate(g: nx.DiGraph, node_load_m2: dict[int, float], node_len_m: dict[int, float] | None = None):
    """沿拓扑序累加。节点有多条出边时按出边数均分。
    返回 edge_area[gid], edge_inlets[gid], edge_uplen[gid], node_terminal[node]."""
    order = list(nx.topological_sort(g))
    acc_area: dict[int, float] = {}
    acc_cnt: dict[int, float] = {}
    acc_len: dict[int, float] = {}
    edge_area, edge_cnt, edge_len = {}, {}, {}
    for n in order:
        a = acc_area.get(n, 0.0) + node_load_m2.get(n, 0.0)
        c = acc_cnt.get(n, 0.0) + (1.0 if node_load_m2.get(n, 0.0) > 0 else 0.0)
        l = acc_len.get(n, 0.0)
        outs = list(g.successors(n))
        if not outs:
            continue
        k = len(outs)
        for v in outs:
            d = g[n][v]
            gid = d["gid"]
            edge_area[gid] = a / k
            edge_cnt[gid] = c / k
            edge_len[gid] = l / k
            acc_area[v] = acc_area.get(v, 0.0) + a / k
            acc_cnt[v] = acc_cnt.get(v, 0.0) + c / k
            acc_len[v] = acc_len.get(v, 0.0) + l / k + (node_len_m or {}).get(gid, 0.0)
    # 终点归属：逆拓扑序，节点跟着接收份额最大的后继走
    terminal: dict[int, int] = {}
    for n in reversed(order):
        outs = list(g.successors(n))
        if not outs:
            terminal[n] = n
        else:
            terminal[n] = terminal[outs[0]] if len(outs) == 1 else terminal[max(outs, key=lambda v: acc_area.get(v, 0.0))]
    return edge_area, edge_cnt, edge_len, terminal


def classify_terminals(terminal: dict[int, int], vertices: pd.DataFrame) -> pd.DataFrame:
    vt = vertices.set_index("id")
    tids = sorted(set(terminal.values()))
    kinds = []
    for t in tids:
        nt = vt["node_table"].get(t) if t in vt.index else None
        kinds.append(TERMINAL_KIND.get(nt, "断头") if isinstance(nt, str) else "断头")
    return pd.DataFrame({"terminal": tids, "terminal_kind": kinds})
