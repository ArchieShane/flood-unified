# -*- coding: utf-8 -*-
"""全市 L1 / L2 一维结果 → 两态各自的指标、两态之间的差值，按片区汇总。

术语（2026-09-27 与用户对齐，全系统统一）：
  · 「L1 剩余」「L2 剩余」—— 各态自己管网接不住的水（溢出井盖的水，ALLOW_PONDING NO 下是真实损失）
  · 「L1 → L2 差值」或「改善」—— 两态之间的变化 = L1 剩余 − L2 剩余，即已建成的塘减少了多少
    用户说的 gap 就是这个差值。

输入：$WORK/City_{L1,L2}{tag}_p*.{inp,rpt,out}（tools/build_city1d.py + run_city1d.py）。
只读每片报告里的「Node Flooding Summary」（每个冒水井的累计溢流）与系统级时序，
不需要逐节点时序，所以 REPORT NODES NONE 也能算。

按三种片区汇总：客户 39 个汇水区（按名称合并）、出水口汇水域（tools/citywide.py）、1 km 方格（地图用）。
这里给出的是**一维**口径：洪水量、系统排空、蓄存能力、临时泵。
退水（主网 / 残余上游）要地面积水随时间怎么退，一维里没有（全市模型 ALLOW_PONDING NO），
等全市一二维耦合跑完补；降雨服务水平另跑。

用法：python tools/city_gap.py [--tag _v2]
产物：out/city_gap{tag}.json、console/data/city_gap_grid.geojson、console/data/city_gap_catch.geojson
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import parsons_table as PT  # noqa: E402
import run_city1d as RC  # noqa: E402
from workdir import WORK  # noqa: E402

GRID_M = 1000.0


def node_floods(rpt: pathlib.Path) -> dict[str, float]:
    """Node Flooding Summary：每个冒水井的累计溢流（10^6 ltr → m³）。"""
    t = rpt.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"Node Flooding Summary.*?-{20,}\n.*?-{20,}\n(.*?)\n\s*\n", t, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        p = ln.split()
        if len(p) >= 6:
            try:
                out[p[0]] = float(p[5]) * 1000.0
            except ValueError:
                pass
    return out


def system_series(out: pathlib.Path):
    """系统级时序：管内存水量（m³）、溢流速率。"""
    from swmm.toolkit import output, shared_enum as se
    h = output.init(); output.open(h, str(out))
    try:
        n = output.get_times(h, se.Time.NUM_PERIODS); st = output.get_times(h, se.Time.REPORT_STEP)
        v = np.array([output.get_system_attribute(h, i, se.SystemAttribute.VOLUME_STORED)[0] for i in range(n)])
        return np.arange(1, n + 1) * st / 3600.0, v
    finally:
        output.close(h)


def one_state(state, tag, n):
    fl, stor, tot, pumps, geo, win = {}, None, collections.Counter(), 0, collections.Counter(), set()
    t_ser = None
    for i in range(1, n + 1):
        inp, rpt, out = (WORK / f"City_{state}{tag}_p{i}{e}" for e in (".inp", ".rpt", ".out"))
        ok, why = RC.part_ok(rpt, inp)
        if not ok:
            raise SystemExit(f"{state} p{i} 不能计入：{why}——合计残缺，不出结果")
        fl.update(node_floods(rpt))
        fx = RC.facts(rpt)
        for k in ("inflow_m3", "outflow_m3", "flood_m3", "stored_m3"):
            tot[k] += fx[k] or 0
        t, v = system_series(out)
        t_ser = t if t_ser is None else t_ser
        stor = v if stor is None else stor[:len(v)] + v[:len(stor)]
        g = PT.geometry_storage(inp)
        for k, val in g.items():
            if isinstance(val, (int, float)):
                geo[k] += val
        pumps += len(PT.pumps(inp))
        win.add(RC.part_window_h(inp))
    dead = float(stor[-1])
    empt = PT._fall_time(t_ser, stor, PT.MAJORITY_FRAC, dead=dead)
    return fl, {"inflow_m3": tot["inflow_m3"], "outflow_m3": tot["outflow_m3"], "flood_m3": tot["flood_m3"],
                "stored_end_m3": tot["stored_m3"], "n_flooded_nodes": len(fl),
                "emptying_majority_h": empt, "peak_stored_m3": float(stor.max()),
                "storage_capacity_m3": geo.get("total_m3") or geo.get("pipe_m3", 0) + geo.get("storage_m3", 0),
                "storage_detail": dict(geo), "pumps": pumps, "windows_h": sorted(w for w in win if w)}


def pond_networks(tag, n):
    """L2 里接进塘的管网块（接塘管 PI_… 起点所在的连通块）的节点集合。

    为什么要单列：L1 与 L2 的分片方式不同（接塘管把几块网并成一组），而 SWMM 的可变步长由整份模型定，
    同一张网放进不同分片会算出略有差别的数——这是**数值噪声**。实测（2026-09-27）不接塘的网
    L1 / L2 只差 1,838 m³（0.04%），接塘的 15 块网差 14.1 万 m³（−13.7%）。所以差值只在接塘的网上算，
    别处记 0，噪声量写进口径。"""
    par = {}

    def f(x):
        par.setdefault(x, x)
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x
    seeds = set()
    for i in range(1, n + 1):
        t = (WORK / f"City_L2{tag}_p{i}.inp").read_text(encoding="utf-8", errors="replace")
        sec = t.split("[CONDUITS]")[1].split("\n[")[0]
        for ln in sec.splitlines():
            q = ln.split()
            if len(q) > 3 and not ln.startswith(";"):
                par[f(q[1])] = f(q[2])
                if q[0].startswith("PI_"):
                    seeds.add(q[1])
    roots = {f(x) for x in seeds}
    return {k for k in list(par) if f(k) in roots}, len(roots)


def xy_of(node: str):
    """重建网的节点名就是端点码「x_y」（UTM 40N 整米）。塘节点（PND_…）不在这里。"""
    try:
        x, y = node.split("_")[:2]
        return float(x), float(y)
    except (ValueError, IndexError):
        return None


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="_v2")
    ap.add_argument("--parts", type=int, default=8)
    a = ap.parse_args(argv)
    import geopandas as gpd
    from shapely.geometry import box

    res, fl = {}, {}
    for st in ("L1", "L2"):
        fl[st], res[st] = one_state(st, a.tag, a.parts)
        r = res[st]
        print(f"{st}：入流 {r['inflow_m3']:,.0f}  排走 {r['outflow_m3']:,.0f}  溢流 {r['flood_m3']:,.0f} m³"
              f"（{r['flood_m3'] / r['inflow_m3'] * 100:.1f}%）  冒水井 {r['n_flooded_nodes']:,}  "
              f"排空 {r['emptying_majority_h']} h  蓄存能力 {r['storage_capacity_m3']:,.0f} m³  泵 {r['pumps']}  窗口 {r['windows_h']}")
    aff, n_aff = pond_networks(a.tag, a.parts)
    nodes_all = set(fl["L1"]) | set(fl["L2"])
    s = lambda st, inset: sum(v for k, v in fl[st].items() if (k in aff) == inset)
    a1, a2, o1, o2 = s("L1", True), s("L2", True), s("L1", False), s("L2", False)
    noise = o1 - o2
    d = a1 - a2
    print(f"接塘的管网 {n_aff} 块：L1 {a1:,.0f} → L2 {a2:,.0f}，差值 {d:,.0f} m³（{d / a1 * 100:.1f}%）")
    print(f"不接塘的管网：L1 {o1:,.0f}、L2 {o2:,.0f}，差 {noise:,.0f} m³（{noise / o1 * 100:+.2f}%，分片不同带来的数值噪声，记 0）")
    print(f"全市 L1 → L2 差值（塘减少的溢流）：{d:,.0f} m³（占 L1 剩余 {d / res['L1']['flood_m3'] * 100:.2f}%）")
    # 两态剩余按「接塘的网算真差值、其余 L2 取 L1 的数」统一口径
    fl["L2"] = {k: (fl["L2"].get(k, 0.0) if k in aff else fl["L1"].get(k, 0.0)) for k in nodes_all}
    res["L2"]["flood_m3_consistent"] = res["L1"]["flood_m3"] - d

    # 按节点 → 片区
    nodes = sorted(set(fl["L1"]) | set(fl["L2"]))
    pts = [(n, xy_of(n)) for n in nodes]
    pts = [(n, p) for n, p in pts if p]
    G = gpd.GeoDataFrame({"node": [n for n, _ in pts], "L1": [fl["L1"].get(n, 0.0) for n, _ in pts],
                          "L2": [fl["L2"].get(n, 0.0) for n, _ in pts]},
                         geometry=gpd.points_from_xy([p[0] for _, p in pts], [p[1] for _, p in pts]), crs=32640)
    out = {"tag": a.tag, "terms": {"L1剩余": "L1 管网接不住的水（溢出井盖，m³）", "L2剩余": "同上，L2",
                                    "差值": "L1剩余 − L2剩余，已建成的塘减少了多少（用户说的 gap）"},
           "city": {"L1": res["L1"], "L2": res["L2"], "diff_m3": d,
                    "diff_pct": d / res["L1"]["flood_m3"] * 100 if res["L1"]["flood_m3"] else None,
                    "pond_networks": {"n": n_aff, "L1_m3": a1, "L2_m3": a2, "diff_m3": d, "diff_pct": d / a1 * 100 if a1 else None},
                    "noise_elsewhere_m3": noise, "noise_pct": noise / o1 * 100 if o1 else None},
           "notes": ["一维口径：退水两项要等全市一二维；降雨服务水平另跑",
                     "差值只在接塘的管网上算；其余管网 L1 / L2 只差分片带来的数值噪声（见 noise_pct），记 0",
                     "演算窗口 30 h（build_city1d 的结束时刻 bug，已修；溢出量 5 h 内定型，不影响结论）"]}

    def agg(zones, key, label):
        j = gpd.sjoin(G, zones[[key, "geometry"]], predicate="within", how="inner")
        s = j.groupby(key)[["L1", "L2"]].sum()
        s["diff"] = s["L1"] - s["L2"]
        z = zones.set_index(key).join(s, how="left").fillna({"L1": 0.0, "L2": 0.0, "diff": 0.0})
        print(f"  按{label}：{(s['L1'] > 0).sum()} 个有溢流；差值 ≠ 0 的 {(s['diff'].abs() > 1).sum()} 个")
        return z

    C = gpd.read_file(ROOT / "console" / "data" / "catchments_sw.geojson").to_crs(32640)
    C["geometry"] = C.geometry.buffer(0)
    C = C.dissolve(by="label", as_index=False)[["label", "geometry"]]
    zc = agg(C, "label", "客户汇水区").reset_index()
    x0, y0, x1, y1 = G.total_bounds
    xs = np.arange(np.floor(x0 / GRID_M) * GRID_M, x1 + GRID_M, GRID_M)
    ys = np.arange(np.floor(y0 / GRID_M) * GRID_M, y1 + GRID_M, GRID_M)
    cells = gpd.GeoDataFrame({"cell": [f"{int(x)}_{int(y)}" for x in xs for y in ys]},
                             geometry=[box(x, y, x + GRID_M, y + GRID_M) for x in xs for y in ys], crs=32640)
    zg = agg(cells, "cell", "1 km 方格").reset_index()
    zg = zg[(zg["L1"] > 0) | (zg["L2"] > 0)]
    out["by_catchment"] = [{"label": r.label, "L1_m3": round(r.L1), "L2_m3": round(r.L2), "diff_m3": round(r["diff"])}
                           for _, r in zc.sort_values("L1", ascending=False).iterrows()]
    out["outside_client_catch_L1_m3"] = round(res["L1"]["flood_m3"] - float(zc["L1"].sum()))
    (ROOT / "out" / f"city_gap{a.tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    # 控制台用的一份：去掉大块明细
    lite = {k: v for k, v in out.items() if k != "by_catchment"}
    lite["by_catchment"] = out["by_catchment"][:40]
    lite["validation"] = json.loads((ROOT / "out" / "validate_city_net_Mussafah.json").read_text(encoding="utf-8"))         if (ROOT / "out" / "validate_city_net_Mussafah.json").exists() else None
    (ROOT / "console" / "data" / "city_gap.json").write_text(json.dumps(lite, ensure_ascii=False), encoding="utf-8")
    for z, fn in ((zg, "city_gap_grid.geojson"), (zc, "city_gap_catch.geojson")):
        z = z.to_crs(4326)
        for c in ("L1", "L2", "diff"):
            z[c] = z[c].round(0)
        (ROOT / "console" / "data" / fn).write_text(z.to_json(drop_id=True), encoding="utf-8")
    print(f"  客户汇水区之外的 L1 溢流 {out['outside_client_catch_L1_m3']:,.0f} m³"
          f"（占 {out['outside_client_catch_L1_m3'] / res['L1']['flood_m3'] * 100:.0f}%）")
    print(f"→ out/city_gap{a.tag}.json、console/data/city_gap_grid.geojson、city_gap_catch.geojson")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
