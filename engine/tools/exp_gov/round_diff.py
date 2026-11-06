"""轮次对比（每轮收尾必做，作业手册「每轮收尾检查单」第 2 项）：逐根管比两轮的状态，找出变差的管和原因所在的系统。

  python tools/exp_gov/round_diff.py <旧轮导出目录> <新轮导出目录> [输出 csv]
  例：python tools/exp_gov/round_diff.py D:/swbrain_work/exp_gov/07_export_round10 D:/swbrain_work/exp_gov/07_export

状态：A 挂上子网；B 系统有出水口、子网没更新；C 内陆系统（终点渗井 / 塘）、子网没更新；D 孤立或断开。
按 assetid 对齐（空编号、重复编号的管不参与）。输出状态转移表；凡是从 A 变成别的、或从 B / C 变成 D 的，按新轮所在系统汇总，列出样例编号，写 csv。
第九轮「挂上子网的管反而少了 3,152 根」就是靠这个对比发现的（2026-10-25）。
"""
import sys

import numpy as np
import pandas as pd

from ledger import systems


def classify(export_dir):
    U, of, sink = systems(f"{export_dir}/Stormwater_AD_reporting.gdb")
    U["state"] = np.where(U.traced, "A", np.where(U.sys.isin(of), "B", np.where(U.sys.isin(sink), "C", "D")))
    U = U[U.assetid.notna() & ~U.assetid.duplicated(keep=False)]
    return U[["assetid", "state", "sys"]]


def main(old, new, out=None):
    sys.stdout.reconfigure(encoding="utf-8")
    a, b = classify(old), classify(new)
    m = a.merge(b, on="assetid", suffixes=("_old", "_new"))
    print("状态转移（行 = 旧轮，列 = 新轮；按编号对齐的管）")
    print(pd.crosstab(m.state_old, m.state_new, margins=True))
    worse = m[((m.state_old == "A") & (m.state_new != "A")) | (m.state_old.isin(["B", "C"]) & (m.state_new == "D"))]
    rows = []
    for s, g in worse.groupby("sys_new"):
        rows.append({"新轮系统代表点 x": s[0], "新轮系统代表点 y": s[1], "变差的管数": len(g),
                     "旧轮状态": "、".join(f"{k} {v}" for k, v in g.state_old.value_counts().items()),
                     "新轮状态": "、".join(f"{k} {v}" for k, v in g.state_new.value_counts().items()),
                     "样例编号": "、".join(g.assetid.head(5))})
    W = pd.DataFrame(rows).sort_values("变差的管数", ascending=False) if rows else pd.DataFrame()
    print(f"\n变差的管：{len(worse)} 根，分布在 {len(W)} 个系统")
    if len(W):
        print(W.head(15).to_string(index=False))
    if out and len(W):
        W.to_csv(out, index=False, encoding="utf-8-sig")
        print("写出", out)
    return W


if __name__ == "__main__":
    main(*sys.argv[1:4])
