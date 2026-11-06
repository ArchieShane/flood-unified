# -*- coding: utf-8 -*-
"""用客户水力模型还原汇水区单元（用户 2026-09-30：「开始第 1 步，用模型数据还原三个汇水区」）。

数据：客户 9 月 26 日发来的 22 个水力模型（D:/世界模型/OneDrive_1_9-26-2026.zip，shapefile）。
做法：每个子汇水区（CA / Catchments）经 OUT_NODEID 接到一个节点，沿管网向下游追到出水口：
  边 = 重力管（START_ID → STOP_ID）+ 压力管；泵站：湿井 → 60 m 内最近的泵点 → 从泵点出发的压力管；
  多条去路时取跳数最少的那个出水口（分叉记下来）。
  按出水口把子汇水区分组、求并集，得到「排到这个出水口的汇水区」。
核对：客户 Catchment 图层截图属性——RB-3 = Sub_Catchm 822、Shape_Area 2,800,069 m²、Model_Name RAHA BEACH OUTFALL-3；
      MBZ = 4,455 / 14,631,222 m² / MussafahRD09；ADGC-10 = 594 / 2,207,574 m² / Outfall-B_C。
用法：python tools/units_from_models.py [模型名 …]（缺省 RB3 B_C Musaffah_RD09）
产物：out/units_from_models/<模型>_by_outfall.geojson（每个出水口一个面，带子汇水区数、面积、所含热点）与 summary.json
"""
from __future__ import annotations

import json
import pathlib
import sys
import zipfile
from collections import defaultdict, deque

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); import datapaths  # noqa: E402  客户数据位置：config.toml [data]
ZIP = datapaths.get("client_models_zip").as_posix()
OUT = ROOT / "out" / "units_from_models"
HS = ["ADM-21", "ADM-90", "ADM-94", "ADM-104", "ADM-192", "ADM-202", "ADM-486", "ADM-487"]


def layer(names, model, keys):
    for k in keys:
        for n in names:
            if n.startswith(model + "/") and n.lower().endswith(".shp") and pathlib.PurePosixPath(n).stem.lower() == k.lower():
                return n
    return None


def col(df, *cands):
    for c in cands:
        if c in df.columns:
            return c
    return None


def run(model, names, H):
    import geopandas as gpd
    import numpy as np
    from scipy.spatial import cKDTree
    V = "/vsizip/" + ZIP + "/"
    rd = lambda n: (lambda g: g.set_crs(32640, allow_override=True) if g.crs is None else g.to_crs(32640))(gpd.read_file(V + n))
    ca = rd(layer(names, model, ["CA", "Catchments", "Catchment"]))
    co = rd(layer(names, model, ["CO", "Conduit", "Conduits"]))
    of = rd(layer(names, model, ["OF", "Outfalls", "Outfall"]))
    pp_n = layer(names, model, ["PRESSURE PIPE", "Prpipes", "Pressure pipes"])
    pu_n = layer(names, model, ["Pump", "Pumps"])
    ww_n = layer(names, model, ["WW", "Wetwells", "Wetwell"])
    s_id = col(co, "START_ID", "STRT_NDID"); e_id = col(co, "STOP_ID", "STP_NID_ID")
    down = defaultdict(set)
    for a, b in zip(co[s_id], co[e_id]):
        down[int(a)].add(int(b))
    if pp_n:
        pp = rd(pp_n)
        for a, b in zip(pp[col(pp, "START_ID", "STRT_NDID")], pp[col(pp, "STOP_ID", "STP_NID_ID")]):
            down[int(a)].add(int(b))
    if pu_n and ww_n:
        pu = rd(pu_n); ww = rd(ww_n)
        tr = cKDTree(np.c_[pu.geometry.x, pu.geometry.y])
        for i, g in zip(ww["ID"], ww.geometry):
            d, j = tr.query((g.x, g.y))
            if d <= 60:
                down[int(i)].add(int(pu["ID"].iloc[j]))
    lab = col(of, "LABEL", "LB")
    outs = {int(i): str(l) for i, l in zip(of["ID"], of[lab])}
    memo: dict[int, tuple] = {}

    def outfall_of(n):
        if n in memo:
            return memo[n]
        seen = {n}; q = deque([(n, 0)]); found = []
        while q:
            k, d = q.popleft()
            if k in outs:
                found.append((d, outs[k]))
                continue
            for m in down.get(k, ()):
                if m not in seen:
                    seen.add(m); q.append((m, d + 1))
        found.sort()
        memo[n] = (found[0][1] if found else None, sorted({f[1] for f in found}))
        return memo[n]

    on = col(ca, "OUT_NODEID")
    if on is None:
        ca["_out"] = "(无 OUT_NODE 字段：整个模型算一片)"; ca["_all"] = ""
    else:
        r = [outfall_of(int(x)) if x == x else (None, []) for x in ca[on]]
        ca["_out"] = [a or "(追不到出水口)" for a, _ in r]; ca["_all"] = [";".join(b) for _, b in r]
    groups = []
    for o, g in ca.groupby("_out"):
        u = g.geometry.buffer(0).union_all()
        hs = [h for h in HS if u.contains(H.loc[h].geometry)]
        groups.append({"outfall": o, "n_sub": int(len(g)), "area_m2": round(u.area), "hotspots": hs,
                       "n_split": int(sum(1 for x in g["_all"] if ";" in x)), "geometry": u})
    G = gpd.GeoDataFrame(groups, geometry="geometry", crs=32640).sort_values("area_m2", ascending=False)
    OUT.mkdir(parents=True, exist_ok=True)
    G.to_crs(4326).to_file(OUT / f"{model}_by_outfall.geojson", driver="GeoJSON")
    return G, len(ca)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    names = zipfile.ZipFile(ZIP).namelist()
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    summ = {}
    for m in (sys.argv[1:] or ["RB3", "B_C", "Musaffah_RD09"]):
        G, n = run(m, names, H)
        print(f"\n== {m}：子汇水区 {n}，出水口分组 {len(G)}")
        for r in G.head(12).itertuples():
            print(f"   {str(r.outfall):<28} 子汇水区 {r.n_sub:>5}  面积 {r.area_m2 / 1e6:6.2f} km²  热点 {','.join(r.hotspots) or '—'}" + (f"  （{r.n_split} 个有分叉）" if r.n_split else ""))
        summ[m] = [{k: v for k, v in r._asdict().items() if k not in ("geometry", "Index")} for r in G.itertuples()]
    (OUT / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
