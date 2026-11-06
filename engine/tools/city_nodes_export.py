# -*- coding: utf-8 -*-
"""全市一维 L1 / L2 的逐井溢流、逐管满管度 → 控制台用的精简数据。

1 km 方格只能看出「哪一片」，看不出是哪口井不再冒水、哪条干管松了下来。
这里把 8 片 × 2 态的 .rpt 拆到井、管一级：
  · 井：Node Flooding Summary 的累计溢流（m³）
  · 管：Link Flow Summary 的最大水深 / 满管深（1.00 = 满管或压力流）
口径与 city_gap.py 一致：L2 的数只在接塘的管网上取 L2 自己的，其余取 L1 的（分片不同带来的数值噪声记 0）。

产物：console/data/city_nodes.json、console/data/city_pipes.json
用法：python tools/city_nodes_export.py [--tag _v2] [--parts 8]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from city_gap import node_floods, pond_networks, xy_of  # noqa: E402
from workdir import WORK  # noqa: E402

PIPE_KEEP = 0.8        # 管只导出接塘的管网里的（别处 L1 = L2）：61 mm / 2 h 下全市 26 万根管有 23 万根满管度 ≥ 0.8，
                       # 全铺开既看不出区别、也有 10 MB。接塘的网里再只留任一态 ≥ 0.8 或两态差 ≥ 0.05 的
PIPE_DIFF = 0.05


def link_depth_ratio(rpt: pathlib.Path) -> dict[str, float]:
    """Link Flow Summary 的最后一列：Max/Full Depth。"""
    t = rpt.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"Link Flow Summary.*?-{20,}\n.*?-{20,}\n(.*?)\n\s*\n", t, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        p = ln.split()
        if len(p) >= 8 and p[1] == "CONDUIT":
            try:
                out[p[0]] = float(p[-1])
            except ValueError:
                pass
    return out


def conduit_ends(inp: pathlib.Path) -> dict[str, tuple[str, str]]:
    t = inp.read_text(encoding="utf-8", errors="replace")
    sec = t.split("[CONDUITS]")[1].split("\n[")[0]
    out = {}
    for ln in sec.splitlines():
        q = ln.split()
        if len(q) > 3 and not ln.startswith(";"):
            out[q[0]] = (q[1], q[2])
    return out


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="_v2")
    ap.add_argument("--parts", type=int, default=8)
    a = ap.parse_args(argv)
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)

    fl = {"L1": {}, "L2": {}}; dr = {"L1": {}, "L2": {}}; ends = {}
    for st in ("L1", "L2"):
        for i in range(1, a.parts + 1):
            b = WORK / f"City_{st}{a.tag}_p{i}"
            fl[st].update(node_floods(b.with_suffix(".rpt")))
            dr[st].update(link_depth_ratio(b.with_suffix(".rpt")))
            ends.update(conduit_ends(b.with_suffix(".inp")))
    aff, n_aff = pond_networks(a.tag, a.parts)
    pick = lambda st, k, d, dflt: d[st].get(k, dflt) if (st == "L1" or k in aff) else d["L1"].get(k, dflt)

    nodes = []
    for k in sorted(set(fl["L1"]) | set(fl["L2"])):
        p = xy_of(k)
        if not p:
            continue
        v1, v2 = fl["L1"].get(k, 0.0), pick("L2", k, fl, 0.0)
        if v1 < 1 and v2 < 1:
            continue
        lo, la = tr.transform(*p)
        nodes.append([round(la, 5), round(lo, 5), round(v1), round(v2)])
    pipes = []
    for k, (n1, n2) in ends.items():
        if k not in dr["L1"] and k not in dr["L2"]:
            continue
        if n1 not in aff and n2 not in aff:
            continue
        d1 = dr["L1"].get(k, 0.0)
        d2 = dr["L2"].get(k, d1)
        if max(d1, d2) < PIPE_KEEP and abs(d1 - d2) < PIPE_DIFF:
            continue
        p1, p2 = xy_of(n1), xy_of(n2)
        if not p1 or not p2:
            continue
        (lo1, lo2), (la1, la2) = tr.transform([p1[0], p2[0]], [p1[1], p2[1]])
        pipes.append([round(la1, 5), round(lo1, 5), round(la2, 5), round(lo2, 5), round(d1, 2), round(d2, 2)])

    better = sum(1 for n in nodes if n[2] - n[3] >= 1); worse = sum(1 for n in nodes if n[3] - n[2] >= 1)
    relieved = sum(1 for p in pipes if p[4] >= 0.99 and p[5] < 0.99)
    meta = {"tag": a.tag, "n_pond_networks": n_aff,
            "note": "L2 只在接塘的管网上取自己的数，其余取 L1（分片数值噪声记 0）；满管度 = 最大水深 / 管径，1.00 = 满管或压力流",
            "cols_nodes": ["lat", "lng", "L1_m3", "L2_m3"], "cols_pipes": ["lat1", "lng1", "lat2", "lng2", "L1_ratio", "L2_ratio"],
            "n_nodes": len(nodes), "n_better": better, "n_worse": worse,
            "n_pipes": len(pipes), "n_relieved": relieved, "pipe_keep": PIPE_KEEP}
    D = ROOT / "console" / "data"
    (D / "city_nodes.json").write_text(json.dumps({**meta, "nodes": nodes}, separators=(",", ":")), encoding="utf-8")
    (D / "city_pipes.json").write_text(json.dumps({**meta, "pipes": pipes}, separators=(",", ":")), encoding="utf-8")
    print(f"冒水井 {len(nodes):,}（L2 溢流减少 {better:,}、增加 {worse:,}）；导出管段 {len(pipes):,}（L1 满管 → L2 不满 {relieved:,}）")
    for f in ("city_nodes.json", "city_pipes.json"):
        print(f"  {f}: {(D / f).stat().st_size / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
