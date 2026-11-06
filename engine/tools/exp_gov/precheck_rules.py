"""装入前预检：数据里实际出现的「点 × 管」相接组合，逐对查 Expanded 规则表（作业手册第九节开工前第 4 项，2026-10-25 补）。

  python tools/exp_gov/precheck_rules.py <工作目录> [输出 csv]
输入 <工作目录>/02_prep/prep.gdb（预处理结果，装入前）、<工作目录>/Expanded_local.gdb（本地化后的资产包，B_Rules）。
类和资产类型的对应直接读 s3_mapping.py 里的 MAP，不另抄一份。
输出：
  1) 点 × 管：点（检查井流道、雨水口、渗井、泵、出水口……）5 mm 内有管的端点或中间折点 → (点的资产组 / 类型, 管的资产组 / 类型)；
     规则表里没有对应的 Junction Edge 规则 → 装进 UN 会报错误 8（没有规则），列出组合、处数、样例坐标。
  2) 管 × 管：不同类型的管在 5 mm 内相接（端点对端点，或端点对中间折点）、旁边没有点 → 装进去会报错误 10，
     预处理会自动加连接点；这里只列出数量，便于核对。
教训：压力管—渗井、泵—穿孔管、压力管—雨水口、检查井—穿孔管这几类缺规则，前十轮都是装进去报错才知道。
"""
import ast
import collections
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyogrio
import shapely
from scipy.spatial import cKDTree

TOL = 0.005


def load_map():
    src = (Path(__file__).parent / "s3_mapping.py").read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "MAP":
            return ast.literal_eval(node.value)
    raise RuntimeError("s3_mapping.py 里没找到 MAP")


def main(work, out=None, rules_gdb=None):
    sys.stdout.reconfigure(encoding="utf-8")
    MAP = load_map()
    prep = os.path.join(work, "02_prep", "prep.gdb")
    have = {n for n, _ in pyogrio.list_layers(prep)}
    R = pyogrio.read_dataframe(rules_gdb or os.path.join(work, "Expanded_local.gdb"), layer="B_Rules", read_geometry=False)   # 第三个参数可换规则表（自检用）
    je = R[R.rule_type == "Junction Edge Connectivity"]
    rules = {(a, b, c, d) for a, b, c, d in zip(je.from_asset_group, je.from_asset_type, je.to_asset_group, je.to_asset_type)}
    rules |= {(c, d, a, b) for a, b, c, d in rules}

    # 管：端点和中间折点
    P = pyogrio.read_dataframe(prep, layer="PIPE", columns=["assetid", "tgt_ag"])
    vx, vt, vend, vid = [], [], [], []
    for aid, ag, g in zip(P.assetid, P.tgt_ag, P.geometry.values):
        tgt = MAP.get(("PIPE", ag))
        if g is None or tgt is None:
            continue
        lt = (tgt[1], tgt[2])
        for part in getattr(g, "geoms", [g]):
            c = shapely.get_coordinates(part)
            for k, xy in enumerate(c):
                vx.append(xy)
                vt.append(lt)
                vend.append(k == 0 or k == len(c) - 1)
                vid.append(aid)
    vx = np.array(vx)
    vtree = cKDTree(vx)

    # 点
    pts, ptype, pid = [], [], []
    for (cls, typ), tgt in MAP.items():
        if cls in ("PIPE", "POND", "MH_VAULT") or cls not in have:
            continue
        cols = ["assetid", "tgt_at"] if typ else ["assetid"]
        try:
            df = pyogrio.read_dataframe(prep, layer=cls, columns=cols)
        except Exception:
            df = pyogrio.read_dataframe(prep, layer=cls, columns=["assetid"])
        if typ:
            df = df[df.get("tgt_at") == typ] if "tgt_at" in df else df.iloc[0:0]
        for aid, g in zip(df.assetid, df.geometry.values):
            if g is not None:
                pts.append(shapely.get_coordinates(g)[0])
                ptype.append((tgt[1], tgt[2]))
                pid.append(aid)
    pts = np.array(pts)
    ptree = cKDTree(pts)

    # 1) 点 × 管
    miss = collections.defaultdict(list)
    seen = collections.Counter()
    for i, xy in enumerate(pts):
        for j in vtree.query_ball_point(xy, TOL):
            combo = (ptype[i], vt[j])
            seen[combo] += 1
            if (ptype[i][0], ptype[i][1], vt[j][0], vt[j][1]) not in rules:
                miss[combo].append((pid[i], vid[j], round(float(xy[0]), 3), round(float(xy[1]), 3), "端点" if vend[j] else "中间折点"))
    rows = []
    for (pt, lt), lst in sorted(miss.items(), key=lambda kv: -len(kv[1])):
        rows.append({"点（资产组 / 类型）": " / ".join(pt), "管（资产组 / 类型）": " / ".join(lt), "处数": len(lst),
                     "样例（点编号、管编号、x、y、接在管的哪里）": "；".join(f"{a}、{b}、{x}、{y}、{w}" for a, b, x, y, w in lst[:3])})
    M = pd.DataFrame(rows)
    print(f"点 × 管：实际出现 {len(seen)} 种组合；规则表里没有的 {len(M)} 种（装入后会报错误 8）")
    if len(M):
        print(M.to_string(index=False))

    # 2) 管 × 管（不同类型、旁边没有点）
    ll = collections.Counter()
    ends = np.flatnonzero(vend)
    for i in ends:
        for j in vtree.query_ball_point(vx[i], TOL):
            if vid[j] != vid[i] and vt[j] != vt[i] and ptree.query(vx[i])[0] > TOL:
                ll[(vt[i][0], vt[j][0], "端点" if vend[j] else "中间折点")] += 1
    print("\n管 × 管（不同类型直接相接、旁边没有点；预处理会加连接点）：")
    for (a, b, w), n in ll.most_common():
        print(f"  {a} 端点 接 {b} {w}：{n}")
    if out and len(M):
        M.to_csv(out, index=False, encoding="utf-8-sig")
        print("写出", out)
    return M


if __name__ == "__main__":
    main(*sys.argv[1:4])
