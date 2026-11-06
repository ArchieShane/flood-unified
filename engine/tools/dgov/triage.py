"""问题按「对一二维计算结果的影响」分级（标准 v0.2 第 0.2 节：每个问题挂到它影响的物理过程上）。

  python -m tools.dgov.triage --extent rda --asset <资产 gpkg>

每条问题：影响哪个过程、在不在某个热点的**汇水路径**上、影响量（管径、两个候选值差多少、是不是全局性的），按下表分「高 / 中 / 低」。
汇水路径 = 沿管网流向（管段 from → to）追踪：热点 100 m 内全部节点（没有则 200 m 内最近 3 个）的全部上游，加上通往出水口的全部下游。
  高：全局性的（级别定义、出水口边界、泵站能力、井盖与地面的整体偏差、二维地形的深坑）；
      或在汇水路径上，且（管径 ≥ 400 mm、管底差 ≥ 0.2 m、属于连通质检问题、或是离热点 ≤ 150 m 的井盖偏差）
  中：在汇水路径上的其余问题；或不在路径上但管径 ≥ 600 mm；或成片的井盖偏差
  低：其余（不在任何热点汇水路径上的小管、零散问题）
2026-10-21 第一版用「离热点多远」判断，RD-A 小、热点密，500 m 几乎覆盖全区，「中」偏多，改为路径判断
阈值是经验值，写在本文件里，可调。产物：out/dgov/issues/<extent>_triage.csv、_triage.md
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from .common import OUT, ROOT, lineage

NEAR, MID_DIST, BIG_D, MID_D, BIG_INV, MID_INV = 150, 500, 600, 400, 0.5, 0.2


def process_of(cat, title, ev):
    c = cat[0]
    if c == "A":
        return "连通 / 过流能力（资产库有、模型没收的管要不要补）"
    if c == "B":
        return "过流能力 / 连通（模型独有的管要不要留）"
    if c == "C":
        f = {e.get("field") for e in ev} if isinstance(ev, list) else set()
        return "过流能力（管径）" if f == {"diameter_mm"} else "坡度与过流（管底高程）"
    if c == "D":
        return "一二维交换（井盖高程，全局）" if title.startswith("DMT 井盖整体") else "一二维交换（井盖高程，局部）"
    if c == "E":
        return "连通（融合后管网质检）"
    if c == "G":
        return "二维地形"
    if "级别" in title or "L1 / L2" in title:
        return "分态（L1 / L2 / L2+planned）"
    if "出水口" in title:
        return "出水口边界"
    if "泵站流量" in title:
        return "泵"
    if "MOVABLE" in title:
        return "无（RD-A 没有移动泵）"
    return "其他"


def main(argv=None):
    import geopandas as gpd
    from shapely.geometry import Point
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.triage")
    ap.add_argument("--extent", required=True)
    ap.add_argument("--asset", required=True)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    # 清单以 CSV 为准（GeoPackage 会漏掉没有位置的全局问题），位置从 GeoPackage 按编号补
    I = pd.read_csv(OUT / "issues" / f"{a.extent}_issues.csv", encoding="utf-8-sig")
    gk = gpd.read_file(OUT / "issues" / f"{a.extent}_issues.gpkg")[["issue_id", "geometry"]]
    I = gpd.GeoDataFrame(I.merge(gk, on="issue_id", how="left"), geometry="geometry", crs=32640)
    L = gpd.read_file(a.asset, layer="link").set_index("asset_id")
    N = gpd.read_file(a.asset, layer="node").set_index("node_id")
    terr = gpd.read_file(f"D:/swbrain_work/esri_un/runs/{a.extent}/territory.shp").to_crs(4326).geometry.iloc[0]
    hs = pd.DataFrame(json.loads((ROOT / "exchange" / "hotspots" / "hotspots.json").read_text(encoding="utf-8"))["hotspots"])
    hs = hs[[isinstance(x, (int, float)) and terr.contains(Point(x, y)) for x, y in zip(hs.lon, hs.lat)]]
    H = gpd.GeoDataFrame(hs[["hotspot_id"]], geometry=gpd.points_from_xy(hs.lon, hs.lat), crs=4326).to_crs(32640)
    # 热点汇水路径：上游全部 + 下游到出水口
    import collections
    from scipy.spatial import cKDTree
    up, down = collections.defaultdict(set), collections.defaultdict(set)
    for lid, fn, tn in zip(L.index, L.from_node, L.to_node):
        down[fn].add((tn, lid))
        up[tn].add((fn, lid))
    nt = cKDTree(np.c_[N.geometry.x, N.geometry.y])
    path_of = collections.defaultdict(set)          # 对象编号 → 在哪些热点的汇水路径上
    for hid, hg in zip(H.hotspot_id, H.geometry):
        seeds = [N.index[i] for i in nt.query_ball_point([hg.x, hg.y], 100)]       # 100 m 内全部节点（含干管上的井）
        if not seeds:                                                                # 100 m 内没有：取 200 m 内最近 3 个
            dd, ii = nt.query([hg.x, hg.y], k=3)
            seeds = [N.index[i] for d, i in zip(dd, ii) if d <= 200]
        for direction in (up, down):
            seen, stack = set(seeds), list(seeds)
            while stack:
                x = stack.pop()
                path_of[x].add(hid)
                for y, lid in direction.get(x, ()):
                    path_of[lid].add(hid)
                    if y not in seen:
                        seen.add(y)
                        stack.append(y)
    rows = []
    for r in I.itertuples():
        try:
            ev = json.loads(r.evidence) if isinstance(r.evidence, str) and r.evidence.strip()[:1] in "[{" else None
        except Exception:
            ev = None
        objs = [o.split("→")[0] for x in str(r.objects).split(";") for o in x.split("×") if o and o != "—"]
        geoms = [L.geometry[o] for o in objs if o in L.index] + [N.geometry[o] for o in objs if o in N.index]
        if not geoms and r.geometry is not None:
            geoms = [r.geometry]
        dia = [L.diameter_mm[o] for o in objs if o in L.index and pd.notna(L.diameter_mm[o])]
        if isinstance(ev, dict) and ev.get("管径mm"):
            dia.append(ev["管径mm"])
        dmax = max(dia) if dia else None
        dist, near_hs = None, None
        if geoms and len(H):
            ds = [(g.distance(h), hid) for g in geoms for h, hid in zip(H.geometry, H.hotspot_id)]
            dist, near_hs = min(ds)
        dinv = None
        if isinstance(ev, list):
            dd = [abs(e["DMT"] - e["AECOM"]) for e in ev if e.get("field") in ("upelev", "downelev") and e.get("DMT") is not None and e.get("AECOM") is not None]
            dinv = max(dd) if dd else None
        proc = process_of(r.category, r.title, ev)
        glob = r.category[0] in "FG" or r.title.startswith("DMT 井盖整体")
        on = sorted(set().union(*[path_of.get(o, set()) for o in objs])) if objs else []
        local_d = r.category[0] == "D" and not glob
        if proc.startswith("无"):
            g = "低"
        elif glob or (on and ((dmax or 0) >= MID_D or (dinv or 0) >= MID_INV or r.category[0] in "CE" or (local_d and (dist or 1e9) <= NEAR))):
            g = "高"
        elif on or (dmax or 0) >= BIG_D or local_d:
            g = "中"
        else:
            g = "低"
        rows.append({"issue_id": r.issue_id, "category": r.category, "title": r.title, "status": r.status, "process": proc, "impact": g,
                     "diameter_mm_max": dmax, "nearest_hotspot": near_hs, "dist_to_hotspot_m": round(dist, 0) if dist is not None else None,
                     "invert_diff_m": round(dinv, 2) if dinv is not None else None, "objects": len(objs), "on_hotspot_path": ",".join(on)})
    T = pd.DataFrame(rows)
    order = {"高": 0, "中": 1, "低": 2}
    T = T.sort_values(["impact", "dist_to_hotspot_m"], key=lambda s: s.map(order) if s.name == "impact" else s)
    T.to_csv(OUT / "issues" / f"{a.extent}_triage.csv", index=False, encoding="utf-8-sig")
    op = T[T.status.isin(["待人工核", "待客户确认"])]
    def mdt(df):
        df = df.reset_index() if df.index.name else df
        return ["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)] +                ["| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    pv = op.pivot_table(index="process", columns="impact", values="issue_id", aggfunc="count", fill_value=0)
    md = [f"# {a.extent.upper()} 问题按对计算结果的影响分级", "", f"未结 {len(op)} 条。分级规则见 `tools/dgov/triage.py` 文件头。", "",
          "## 按影响的过程 × 级别", ""] + mdt(pv) + ["", "## 高影响的问题", ""] +          mdt(op[op.impact == "高"][["issue_id", "category", "title", "process", "diameter_mm_max", "on_hotspot_path", "dist_to_hotspot_m", "invert_diff_m", "status"]])
    (OUT / "issues" / f"{a.extent}_triage.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    lineage("triage", [OUT / "issues" / f"{a.extent}_issues.gpkg", a.asset], [OUT / "issues" / f"{a.extent}_triage.csv"], {"extent": a.extent},
            notes=json.dumps(op.impact.value_counts().to_dict(), ensure_ascii=False))
    print(op.impact.value_counts().to_dict())
    print(op.pivot_table(index="process", columns="impact", values="issue_id", aggfunc="count", fill_value=0).to_string())


if __name__ == "__main__":
    main()
