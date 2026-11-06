# -*- coding: utf-8 -*-
"""全市一二维耦合的试跑：Madinat Al Riyad 一片的 L1 / L2。

为什么先试跑这一片（2026-09-27）：全市一维 16 片并发跑了 5.5 h 只完成 2 片，
而一二维耦合不能按管网连通块切片（地表把各片连成一块），只能整城一个进程——
所以先拿一片量真实速度。选 Madinat Al Riyad：全市 23 座已建成塘里 15 座在这
（174 万 m³，占 90%），是 L2 真正 ≠ L1 的地方。

------------------------------------------------------------------ 取哪些管
**整块取连通块，不按框截**：框边截断的管会在断口冒水。凡是 ≥ 50% 节点落在框里的
连通块整块取进来；两块从西边伸进来的大网（2.9 万 / 2.5 万节点，只有 160 / 984 个
节点在框里）不取，它们在框里那一小截的地面按「无管网」处理（二维直接下雨）。

------------------------------------------------------------------ 塘怎么接（L2）
查实（2026-09-27）：这一片 29 块 ≥ 50 节点的管网**没有登记出水口**，而最大的几块
的最低井离某座塘只有 12–28 m（6,619 节点 → PND_39 24 m；5,690 → RZS_18 14 m；
3,875 → RZS_20 20 m；2,604 → RZS_19 12 m）。塘就是这些道路雨水网的出口，只是台账
没把塘登记成出口。全市 L2 的写法（进水口挂在「出水管」上游端）因此是接反了。
所以这里：**每块没有出口的管网，最低井 100 m 内有塘就用一根短管接进最近的塘**；
pond_link 认出的进水管也接上。塘是末端，不设出水口和排空泵——2 小时的雨、6 小时的
窗口里本来就不排空；塘满了水就在网里顶托，从井盖冒到二维。
**这条接法是按位置与标高推断的，不是台账写的**，结果里要标明。

------------------------------------------------------------------ 雨
与 Mussafah_00 完全一致：61 mm / 2 h，客户模型自带的 2 小时设计雨型，SCS CN 82。
（全市一维那一轮 unit_runoff 没给雨型，用的是**均匀雨**——那一轮与 Mussafah 不可比。）
  · 有管网覆盖的地面：净雨经 SCS 单位线汇流后按节点注入一维（同全市一维）；
  · 没有管网覆盖的地面：净雨直接下在二维上（rain-on-grid）——靠地表汇流进塘的
    那部分水，只有这样才算得进去。「覆盖」按最近节点法的收敛半径判。

------------------------------------------------------------------ 全市（--domain city）
同一套写法铺到全市：所有管网连通块、23 座塘全接，计算域 = 管网服务范围 ∪ 23 座塘的地表汇水区，
外扩 300 m。产流改用客户参数（--runoff v2，out/citywide/node_runoff.json）：客户模型覆盖到的节点
用客户的面积 / CN / Tc，按 (CN, Tc) 分组各给一条过程线；落在客户汇水区里却没挂上客户参数的节点
不再按最近节点法另算面积（否则重复计），客户汇水区以外的才退回 v1（CN 82、Tc 30 min）。

用法：python tools/build_pilot.py L1|L2 [--sim 6] [--domain box|hydro|city] [--runoff v1|v2]
产物：$SWBRAIN_SWMM_WORK/Pilot_MAR_{L1,L2}.inp、c2d/dem_Pilot_MAR.tif、c2d/pilot_Pilot_MAR_{L1,L2}.json
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import build_city1d as B  # noqa: E402
from workdir import WORK  # noqa: E402

NAME = "Pilot_MAR"
BOX = (261602.0, 2685414.0, 275983.0, 2699538.0)   # 15 座塘的包络四周各放 2 km（UTM 40N）
MIN_IN_BOX = 0.5


def components(sc, ec):
    par = {}

    def f(x):
        par.setdefault(x, x)
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x
    for a, b in zip(sc, ec):
        if a and b and a != b:
            par[f(a)] = f(b)
    return f


def rain_series(dt_min=1.0):
    """(时刻 h, 累计净雨 mm, 单位面积汇流过程线 m³/s/ha)。雨型取客户 2 h 设计雨型。"""
    import runoff as R
    dt_h = dt_min / 60.0
    shape = R.read_shape(WORK / "Mussafah_baseline.inp", B.DUR_H, log=lambda *a: None)
    t, P_cum = R.hyetograph(B.P_MM, B.DUR_H, dt_h, shape, 0.0, total_h=12.0)
    exc = R.scs_cum_excess(P_cum, B.CN)
    inc = np.diff(exc, append=exc[-1])
    u = R.unit_hydrograph(B.TC_MIN, dt_h, 10_000.0)
    q = np.convolve(inc, u, mode="full")[:len(t)]
    return t, exc, q


def _swap_section(txt, sec, rows):
    """整段换掉 [sec] 的内容（到下一个空行为止）。"""
    head = f"[{sec}]\n"
    i = txt.index(head) + len(head)
    j = txt.index("\n\n", i - 1)
    return txt[:i] + "\n".join(rows) + txt[j:]


def runoff_v2_sections(txt, xy, area, t, dt_min=1.0):
    """把 write_inp 写的单条 TS_RUNOFF 换成按 (CN, Tc) 分组的过程线（客户参数）。"""
    import geopandas as gpd
    import runoff as R
    from shapely.geometry import Point
    from shapely.ops import unary_union
    from shapely.prepared import prep
    from shapely.validation import make_valid
    nr = json.loads((ROOT / "out" / "citywide" / "node_runoff.json").read_text(encoding="utf-8"))
    C0 = gpd.read_file(ROOT / "console" / "data" / "catchments_sw.geojson").to_crs(32640)
    cov = prep(unary_union([make_valid(g).buffer(0) for g in C0.geometry]))
    dt_h = dt_min / 60.0
    shape = R.read_shape(WORK / "Mussafah_baseline.inp", B.DUR_H, log=lambda *a: None)
    _, P_cum = R.hyetograph(B.P_MM, B.DUR_H, dt_h, shape, 0.0, total_h=12.0)
    grp, rows, lost = {}, [], 0.0
    n_cli = n_v1 = n_zero = 0
    a_cli = a_v1 = 0.0
    for k in xy:
        if k in nr:
            cn = float(nr[k]["cn"]); tc = float(nr[k]["tc"]); ar = float(nr[k]["area_m2"])
            n_cli += 1; a_cli += ar
        elif cov.contains(Point(xy[k])):
            n_zero += 1; continue                   # 客户汇水区里、没挂上客户参数：面积已算在客户那边
        else:
            cn, tc, ar = B.CN, B.TC_MIN, float(area.get(k, 0.0))
            n_v1 += 1; a_v1 += ar
        if ar <= 0:
            continue
        g = (int(round(cn)), min((5, 10, 15, 20, 30, 45, 60, 90), key=lambda x: abs(x - tc)))
        grp.setdefault(g, None); rows.append((k, g, ar))
    lost = sum(v["area_m2"] for kk, v in nr.items() if kk not in xy)
    ts = []
    for (cn, tc) in grp:
        exc = R.scs_cum_excess(P_cum, cn)
        q = np.convolve(np.diff(exc, append=exc[-1]), R.unit_hydrograph(tc, dt_h, 10_000.0), mode="full")[:len(t)]
        nz = np.flatnonzero(q > 1e-9)
        for i in range(0, min((nz[-1] + 2) if len(nz) else 1, len(t)), 2):
            hh, mm = divmod(int(round(t[i] * 60)), 60)
            ts.append(f"TS_{cn}_{tc}  04/16/2024  {hh:02d}:{mm:02d}  {q[i]:.6f}")
    inf = [f"{k}  FLOW  TS_{g[0]}_{g[1]}  FLOW  1.0  {ar / 1e4:.5f}" for k, g, ar in rows]
    txt = _swap_section(_swap_section(txt, "INFLOWS", inf), "TIMESERIES", ts)
    print(f"  产流 v2：客户参数节点 {n_cli:,}（{a_cli / 1e6:,.1f} km²）、客户区外 v1 节点 {n_v1:,}（{a_v1 / 1e6:,.1f} km²）、"
          f"客户区内未挂参数不另算 {n_zero:,}；(CN,Tc) 分组 {len(grp)}；客户参数没落到本网的面积 {lost / 1e6:,.2f} km²")
    return txt


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Madinat Al Riyad 一二维耦合试跑的一维模型")
    ap.add_argument("state", choices=["L1", "L2"])
    ap.add_argument("--sim", type=float, default=6.0)
    ap.add_argument("--runoff", choices=["v1", "v2"], default="v1",
                    help="v2：客户各子汇水区自己的面积 / CN / Tc（全市一二维用这个）")
    ap.add_argument("--domain", choices=["box", "hydro", "city"], default="hydro",
                    help="box：矩形框（第一版）；hydro：按塘的地表汇水区 ∪ 管网服务范围划的多边形")
    a = ap.parse_args(argv)
    B.SIM_H = a.sim
    X0, Y0, X1, Y1 = BOX
    name = {"box": NAME, "hydro": NAME + "2", "city": "City2D"}[a.domain]

    print(f"{name} · {a.state} · 计算域 {a.domain}")
    df = B.read_pipes(None, log=lambda *x: None)
    xy_all, ground, inv, (vu, vd) = B.build_nodes(df, log=lambda *x: None)
    sc = df.StartCode.astype("string").fillna("").values
    ec = df.EndCode.astype("string").fillna("").values
    f = components(sc, ec)
    comp = collections.defaultdict(list)
    for n in xy_all:
        comp[f(n)].append(n)
    inb = lambda n: X0 <= xy_all[n][0] <= X1 and Y0 <= xy_all[n][1] <= Y1
    region = None
    if a.domain == "city":
        import geopandas as gpd
        from shapely.ops import unary_union
        P0 = gpd.read_file(ROOT / "console" / "data" / "ponds_l2.geojson").to_crs(32640)
        ids = set(P0.unitid)
        C = gpd.read_file(ROOT / "console" / "data" / "l2_pond_catch.geojson").to_crs(32640)
        region = unary_union(list(C.geometry.buffer(0)) + list(P0.geometry.buffer(0)))
        inb = lambda n: True
        keep_roots = {r for r, ns in comp.items() if len(ns) >= 2}
    elif a.domain == "hydro":
        # 按汇水边界：这 20 座塘的地表汇水区（tools/pond_surface_catchments.py，25 km 窗口；
        # PND_38 / RZS_18 到 25 km 仍未收敛，是下限）。管网取「≥ 50% 节点落在汇水区里」的块，
        # 外加最低井 100 m 内有塘的块（排进塘的网）
        import geopandas as gpd
        from shapely.ops import unary_union
        from shapely.prepared import prep
        P0 = gpd.read_file(ROOT / "console" / "data" / "ponds_l2.geojson").to_crs(32640)
        ids = set(P0.unitid[[X0 <= c.x <= X1 and Y0 <= c.y <= Y1 for c in P0.geometry.centroid]])
        C = gpd.read_file(ROOT / "console" / "data" / "l2_pond_catch.geojson").to_crs(32640)
        region = unary_union(list(C[C.unitid.isin(ids)].geometry.buffer(0)) + list(P0[P0.unitid.isin(ids)].geometry.buffer(0)))
        pr = prep(region.buffer(200.0))
        from shapely.geometry import Point
        inb = lambda n: pr.contains(Point(xy_all[n]))
        near = prep(unary_union(list(P0[P0.unitid.isin(ids)].geometry.buffer(B.LINK_M))))
        keep_roots = {r for r, ns in comp.items()
                      if sum(map(inb, ns)) >= MIN_IN_BOX * len(ns) or near.contains(Point(xy_all[min(ns, key=lambda n: inv[n])]))}
    else:
        keep_roots = {r for r, ns in comp.items() if sum(map(inb, ns)) >= MIN_IN_BOX * len(ns)}
    keep = {n for r in keep_roots for n in comp[r]}
    xy = {n: xy_all[n] for n in xy_all if n in keep}
    outs = B.read_outfalls(set(xy), log=lambda *x: None)

    ln = df.LineLength.astype("float64").values
    dia = df.PIPE_DIAMETER.astype("float64").values / 1000.0
    pipes = []
    for i in range(len(df)):
        n1, n2 = sc[i], ec[i]
        if n1 == n2 or n1 not in keep or n2 not in keep:
            continue
        z1, z2 = float(vu[i]), float(vd[i])
        if z2 > z1:
            n1, n2, z1, z2 = n2, n1, z2, z1
        z1 = max(z1, inv[n1]); z2 = max(z2, inv[n2])
        pipes.append({"id": f"c_{i}", "n1": n1, "n2": n2, "len": float(ln[i]) if ln[i] > 0 else B.MIN_LEN,
                      "z1": z1, "z2": z2, "d": float(dia[i] if dia[i] > 0 else 0.3), "uid": ""})
    n_in = sum(map(inb, xy))
    print(f"  连通块 {len(keep_roots)} 块、节点 {len(xy):,}（框内 {n_in:,}）、管 {len(pipes):,}、出水口 {len(outs)}")

    # 服务面积与收敛半径（二维「有无管网覆盖」按它判）
    area = B.served_areas(xy, log=lambda *x: None)
    A = sum(area.values())
    R_served = B.K_RADIUS * math.sqrt(A / max(len(xy), 1))
    print(f"  管网服务面积 {A / 1e6:,.1f} km²，服务半径 {R_served:.1f} m")

    # 塘：与全市 L2 同一个实现（build_city1d.terminal_ponds），只取框里的
    ponds, links = ([], [])
    if a.state == "L2":
        ponds, links = B.terminal_ponds(xy, inv, pipes, outs,
                                        keep_pond=(lambda u, c: True) if a.domain == "city" else
                                        (lambda u, c: X0 <= c.x <= X1 and Y0 <= c.y <= Y1))   # 两种计算域都用这 20 座，便于对照
        for pd_ in ponds:
            print(f"    {pd_['unitid']:<24}{pd_['vol']:>10,.0f} m³  进水 {len(pd_['inflows'])} 处")

    # 写 .inp：复用 write_inp 写管网，塘另外追加
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / f"{name}_{a.state}.inp"
    t, exc, q = rain_series()
    B.unit_runoff = lambda: [(f"{int(round(h * 60)) // 60:02d}:{int(round(h * 60)) % 60:02d}", float(v))
                             for h, v in zip(t, q)]
    of_dia = {}
    for c in pipes:
        for nd in (c["n1"], c["n2"]):
            if nd in outs and c["d"] > of_dia.get(nd, 0.0):
                of_dia[nd] = c["d"]
    B.write_inp(out, xy, ground, inv, pipes + links, outs, area, None, of_dia, log=lambda *x: None)
    # 提速：44,229 根管里 1,754 根不到 5 m、5,805 根不到 10 m，动力波的可变步长被最短的管卡死。
    # LENGTHENING_STEP 是 SWMM 自带的标准做法（把过短的管在计算上等效加长到满足这个 Courant 步长）；
    # THREADS 让动力波多线程。两项都会让结果略有变化，L1 / L2 同样设置，不影响两者之差的可比性。
    txt = out.read_text(encoding="utf-8")
    # write_inp 已自带 LENGTHENING_STEP 与 THREADS（默认 4 线程，给并行分片用）；试跑一次只跑一个，线程开满
    txt = txt.replace(f"THREADS  {B.SWMM_THREADS}", "THREADS  8", 1)
    if a.runoff == "v2":
        txt = runoff_v2_sections(txt, xy, area, t)
    out.write_text(txt, encoding="utf-8")
    B.append_ponds(out, ponds, links)
    print(f"  → {out.name}（{out.stat().st_size / 1048576:.1f} MB）")

    # 二维要的：DEM、节点、塘轮廓、无管网覆盖处的净雨
    import rasterio
    from rasterio.windows import from_bounds
    from swbrain import db
    c2d = WORK / "c2d"; c2d.mkdir(exist_ok=True)
    # 计算域多边形（hydro）：塘的汇水区 ∪ 管网服务范围（每个井外扩服务半径），再外扩 300 m，取最大的那块
    poly = None
    if a.domain in ("hydro", "city"):
        from shapely.geometry import MultiPoint, Point, Polygon
        from shapely.ops import unary_union
        served = unary_union([Point(v).buffer(R_served, 8) for v in xy.values()]) if len(xy) < 80000 else             MultiPoint(list(xy.values())).buffer(R_served, 4)
        dom = unary_union([region, served]).buffer(300.0).buffer(-100.0).simplify(30.0)
        if dom.geom_type == "MultiPolygon":
            parts = sorted(dom.geoms, key=lambda g: -g.area)
            print(f"  计算域有 {len(parts)} 块，取最大的一块（{parts[0].area / 1e6:,.1f} km²，其余合计 {sum(g.area for g in parts[1:]) / 1e6:,.1f} km²）")
            if a.domain == "city":                       # 全市会碎成好几块：逐块报节点数，丢掉的要看得见
                from shapely.prepared import prep as _prep
                for i, g in enumerate(parts[:12]):
                    pg = _prep(g)
                    print(f"    块 {i + 1}: {g.area / 1e6:>8,.1f} km²  节点 {sum(pg.contains(Point(v)) for v in xy.values()):>8,}"
                          f"  中心 ({g.centroid.x:,.0f}, {g.centroid.y:,.0f})")
            dom = parts[0]
        dom = Polygon(dom.exterior)                      # 不要洞：洞的边也会变成出流边界
        ring = list(dom.exterior.coords)[:-1]
        if dom.exterior.is_ccw is False:
            ring = ring[::-1]
        poly = [[round(x, 1), round(y, 1)] for x, y in ring]
        X0, Y0, X1, Y1 = dom.bounds
        inb = (lambda pg: (lambda n: pg.contains(Point(xy[n]))))(__import__("shapely.prepared", fromlist=["prep"]).prep(dom))
        print(f"  计算域 {dom.area / 1e6:,.1f} km²（{len(poly)} 个顶点），包络 {(X1 - X0) / 1000:.1f} × {(Y1 - Y0) / 1000:.1f} km")
    dem_f = c2d / f"dem_{name}.tif"
    if not dem_f.exists():
        with rasterio.open((db.load_config(None).get("terrain") or {})["dtm_path"]) as ds:
            win = from_bounds(X0, Y0, X1, Y1, ds.transform).round_offsets().round_lengths()
            z = ds.read(1, window=win)
            prof = ds.profile.copy(); prof.update(width=z.shape[1], height=z.shape[0],
                                                  transform=ds.window_transform(win), compress="deflate")
            with rasterio.open(dem_f, "w", **prof) as w:
                w.write(z, 1)
    meta = {"name": name, "state": a.state, "box": (X0, Y0, X1, Y1), "domain_polygon": poly, "R_served_m": R_served,
            "nodes": {k: [round(v[0], 2), round(v[1], 2)] for k, v in xy.items() if inb(k)},
            "ponds": [{"node": p["node"], "unitid": p["unitid"], "vol": p["vol"], "xy": p["xy"],
                       "footprint": p["footprint"]} for p in ponds],
            "rain_t_h": [round(float(x), 4) for x in t], "rain_excess_mm": [round(float(x), 4) for x in exc],
            "links_to_ponds": [{k: v for k, v in l.items() if k in ("id", "n1", "n2", "len", "z1", "z2", "d", "why")} for l in links],
            "note": "塘作为无出口管网的末端，是按位置与标高推断的（不是台账写的）"}
    (c2d / f"pilot_{name}_{a.state}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(f"  二维准备：DEM {dem_f.name}、节点 {len(meta['nodes']):,}、塘 {len(ponds)} → pilot_{name}_{a.state}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
