"""开源治理引擎 PoC 第 2 步：按 Expanded 规则表建连通边 + 网络拓扑错误检查。

  python tools/oss_gov/o2_checks.py [e1|c2]

输入 out/features.parquet、out/members.parquet、out/assoc.parquet（o1_graph.py 产物）和资产包快照里的 B_Rules / B_EdgeConnectivity。
逐个重合点簇判断（簇 = 容差 0.001 m 内重合的点要素与线折点）：
  点要素 ≥ 2 个         → 25 点位重叠（Stacked points）：每对一条（首个点 ↔ 其余点）
  点 1 个、线若干       → 每条线查节点—线规则（Junction Edge Connectivity，按 资产组 / 资产类型，端子不分）：
                          有规则 = 连通；没有 = 8 缺规则（Missing rule），不连通
  点 ≥ 2 个、线若干     → 一条线对 ≥ 2 个点都有规则 = 9 连通不唯一（Ambiguous connectivity），不连通（候选里恰有一个带端子设备时例外，见代码注释）；
                          恰好 1 个 = 连那个点；0 个 = 8
  没有点、线 ≥ 2 条     → 线—线连通要求簇里至少有一条线是端点（推断：两条线只在中间折点相交不建连通；
                          UN 第八轮对 33 处「中间折点对中间折点」的异类型管一处 10 都没报，印证）。
                          有端点时资产类型不同的线互相 10 缺节点（Missing junction）；同类型的线连通
  线在簇里是中间折点    → 线的边连通策略是 END_VERTEX 时 13（Edge connectivity policy）；本资产包全部 ANY_VERTEX，所以 13 应为 0
几何错误（UN 错误表也有）：19 多部件线、20 自相交线、21 重复折点（同一条线两个折点彼此直接在容差内；闭合线首尾除外）、1 / 5 零长 / 长度在容差内
节点—节点连通关联（System Outflow ↔ 锚点）：查 Junction Junction Connectivity 规则，有 = 连通。
错误码与含义按 Esri 官方「Analyze Network Data」工具说明（见报告引用）；判定细则是本 PoC 的实现，UN 内部算法未公开，带「推断」。
产物：out/errors.parquet、out/edges_rule.parquet（规则连通边）、out/edges_geom.parquet（纯几何连通边）、out/o2_checks.json
"""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyogrio

sys.path.insert(0, str(Path(__file__).parent))
import common as C  # noqa: E402


def load_rules():
    r = pyogrio.read_dataframe(C.AP, layer="B_Rules", read_geometry=False)
    r["rule_id"] = np.arange(1, len(r) + 1)        # 资产包规则表顺序号（UN 内部 RuleID 不同，只作本 PoC 标识）
    je, jj = defaultdict(list), defaultdict(list)
    for x in r.itertuples():
        a = (x.from_feature_class, x.from_asset_group, x.from_asset_type)
        b = (x.to_feature_class, x.to_asset_group, x.to_asset_type)
        if x.rule_type == "Junction Edge Connectivity":
            pt, ln = (a, b) if b[0] == "Line" else (b, a)
            je[(pt, ln[1:])].append(x.rule_id)
        elif x.rule_type == "Junction Junction Connectivity":
            jj[(a, b)].append(x.rule_id)
            jj[(b, a)].append(x.rule_id)
    ec = pyogrio.read_dataframe(C.AP, layer="B_EdgeConnectivity", read_geometry=False)
    policy = {(x.asset_group, x.asset_type): x.edge_connectivity for x in ec.itertuples()}
    ta = pyogrio.read_dataframe(C.AP, layer="B_TerminalConfiguration_Assignment", read_geometry=False)
    terminal = {(x.feature_class, x.asset_group, x.asset_type) for x in ta.itertuples()}
    return r, je, jj, policy, terminal


def main():
    C.setup(sys.argv)
    T = C.Timer()
    feat = pd.read_parquet(C.OUT / "features.parquet")
    mem = pd.read_parquet(C.OUT / "members.parquet")
    assoc = pd.read_parquet(C.OUT / "assoc.parquet")
    rules, je, jj, policy, terminal = load_rules()
    R = {"rules": rules.rule_type.value_counts().to_dict(), "je_keys": len(je), "edge_policy": pd.Series(policy).value_counts().to_dict()}
    mhc_pp = [k for k in je if k[0][1] == "Manhole Channel" and k[1][0] == "Perforated Pipe"]
    R["localized_rule_MHC_perforated_present"] = bool(mhc_pp)
    T.lap("读规则 / 簇")

    fc = feat.un_class.map(C.RULE_FC).values
    ag, at, kind = feat["ag"].values, feat["at"].values, feat["kind"].values
    errs, e_rule, e_geom = [], [], []
    stat = defaultdict(int)

    def err(code, o, d=-1, cid=-1, note=""):
        errs.append((code, o, d, cid, note))

    mem = mem.sort_values(["cid", "is_line", "gid"])
    cids = mem.cid.values
    starts = np.r_[0, np.flatnonzero(cids[1:] != cids[:-1]) + 1, len(cids)]
    G, L, E, V = mem.gid.values, mem.is_line.values, mem.is_end.values, mem.vtx.values
    X, Y = mem.x.values, mem.y.values
    _, p_norm, r_ = C.MODES[C.MODE]

    def near(i, j):
        d = (abs(X[i] - X[j]), abs(Y[i] - Y[j]))
        return (max(d) if p_norm == float("inf") else float(np.hypot(*d))) <= r_

    for s, e in zip(starts[:-1], starts[1:]):
        cid = cids[s]
        g, isl, ise = G[s:e], L[s:e], E[s:e]
        P = list(dict.fromkeys(g[~isl]))
        lin = defaultdict(list)                       # 线 gid → 在簇里的折点（是否端点, 折点号, 行号）
        for k, (gg, ee, vv) in enumerate(zip(g[isl], ise[isl], V[s:e][isl])):
            lin[gg].append((ee, vv, s + np.flatnonzero(isl)[k]))
        # 21 重复折点：同一条线有两个折点彼此直接在容差内（不经别的要素链式传递；闭合线首尾两个端点除外）
        for gg, vs in lin.items():
            if len(vs) < 2:
                continue
            dup = [(a[1], b[1]) for i_, a in enumerate(vs) for b in vs[i_ + 1:] if near(a[2], b[2]) and not (a[0] and b[0])]
            if dup:
                err(21, gg, -1, cid, f"折点 {dup} 重合")
            elif all(x[0] for x in vs):
                stat["闭合线首尾重合"] += 1
        Ls = list(lin)
        allf = P + Ls
        if len(allf) >= 2:
            for x in allf[1:]:
                e_geom.append((allf[0], x))
        # 13 边连通策略
        for gg in Ls:
            if not any(x[0] for x in lin[gg]) and len(allf) >= 2 and policy.get((ag[gg], at[gg]), "ANY_VERTEX") != "ANY_VERTEX":
                err(13, gg, -1, cid, "中间折点连接违反 END_VERTEX 策略")
        if len(P) >= 2:
            stat["点位重叠簇"] += 1
            for p in P[1:]:
                err(25, P[0], p, cid)
        if P:
            for gg in Ls:
                ok = [(p, je.get(((fc[p], ag[p], at[p]), (ag[gg], at[gg])), [])) for p in P]
                ok = [(p, rid) for p, rid in ok if rid]
                if len(ok) == 1:
                    e_rule.append((ok[0][0], gg))
                    if not any(x[0] for x in lin[gg]):
                        stat["中跨节点—线连通"] += 1
                elif len(ok) >= 2:
                    # 候选里有带端子的设备（出水口等）时不报 9、线接到该设备（推断：UN 第八轮 3 处「出水口 + BMP 点」重叠只报 25 不报 9；
                    # 而「BMP 点 + 检查井流道 / 管堵」等无端子组合都报了 9）
                    tm = [p for p, _ in ok if (fc[p], ag[p], at[p]) in terminal]
                    if len(tm) == 1:
                        e_rule.append((tm[0], gg))
                        stat["重叠点里有端子设备、线接端子设备"] += 1
                    else:
                        for p, rid in ok:          # 照 UN 错误表：每个有规则的点 ↔ 线一行
                            err(9, p, gg, cid, "规则 " + ",".join(str(r) for r in rid))
                else:
                    for p in P:
                        err(8, p, gg, cid)
        elif len(Ls) >= 2 and any(x[0] for gg in Ls for x in lin[gg]):
            by_t = defaultdict(list)
            for gg in Ls:
                by_t[at[gg]].append(gg)
            for t, gs in by_t.items():
                for x in gs[1:]:
                    e_rule.append((gs[0], x))
            if len(by_t) >= 2:
                ts = list(by_t)
                for i in range(len(ts)):
                    for j in range(i + 1, len(ts)):
                        for a_ in by_t[ts[i]]:
                            for b_ in by_t[ts[j]]:
                                if any(x[0] for x in lin[a_] + lin[b_]):      # 两条都只是中间折点 = 交叉不连，不报
                                    err(10, a_, b_, cid)
    T.lap("逐簇判断连通与错误")

    # 节点—节点关联
    n_ok = 0
    for f_, t_ in zip(assoc.from_gid.values, assoc.to_gid.values):
        e_geom.append((f_, t_))
        if jj.get(((fc[f_], ag[f_], at[f_]), (fc[t_], ag[t_], at[t_]))):
            e_rule.append((f_, t_))
            n_ok += 1
        else:
            err(8, f_, t_, -1, "节点—节点关联无规则")
    R["assoc_with_jj_rule"] = n_ok

    # 几何错误
    ln = feat[feat.kind == "line"]
    for g_ in ln.gid[ln.nparts > 1]:
        err(19, g_)
    for g_ in ln.gid[~ln.is_simple & (ln.nparts == 1)]:
        err(20, g_)
    for g_ in ln.gid[ln.length <= C.TOL]:
        err(1 if feat.at[g_, "length"] == 0 else 5, g_)
    T.lap("关联 + 几何错误")

    E_ = pd.DataFrame(errs, columns=["code", "origin_gid", "dest_gid", "cid", "note"])
    E_.to_parquet(C.OUT / "errors.parquet")
    pd.DataFrame(np.array(e_rule, dtype=np.int64).reshape(-1, 2), columns=["u", "v"]).to_parquet(C.OUT / "edges_rule.parquet")
    pd.DataFrame(np.array(e_geom, dtype=np.int64).reshape(-1, 2), columns=["u", "v"]).to_parquet(C.OUT / "edges_geom.parquet")
    R["errors_by_code"] = E_.code.value_counts().sort_index().to_dict()
    R["edges"] = {"rule": len(e_rule), "geom": len(e_geom)}
    R["stats"] = dict(stat)
    R["timing_s"] = T.total()
    C.jdump(R, C.OUT / "o2_checks.json")
    print(R, flush=True)


if __name__ == "__main__":
    main()
