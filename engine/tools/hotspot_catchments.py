# -*- coding: utf-8 -*-
"""给客户积水热点划汇水区：**地表**（5 m DTM，D8）+ **管网**（冒水井的上游管网所服务的地面），两者合起来才是这个热点的来水范围。

为什么（2026-09-28）：用户问 ADM-94 / 487 / 192 落在哪个汇水区——都不在客户那 39 个汇水区里（那是建模研究范围，不是全市排水分区），
所以直接给热点自己划。城区很平，积水主要是管网冒出来的，只划地表会漏掉大头。

方法与口径（都写进产物）：
  地表  pyflwdir：填洼 + D8；热点周边 OUT_R m 的格当出口，basins() 把每格归到顺流向最先遇到的那个热点（热点之间不重叠）
  管网  全市一维（City_L1_v2_p*，重建管网）：热点周边 WELL_R m 内 6 h 溢流 ≥ 1 m³ 的井为起点，沿管道逆流向找全部上游井；
        每口井的服务范围 = 离它最近、且在 SERVE_R m 内的地面（与全市一维分配产流面积的做法一致），上游井的服务范围合起来
  来水  61 mm / 2 h，SCS CN 82 → 净雨 23.53 mm（全市一维口径）

用法：python tools/hotspot_catchments.py ADM-94 ADM-487 ADM-192 [--tag _C1]
产物：console/data/hotspot_catch.geojson（地图图层）、out/hotspot_catchments.json（逐点统计）、out/hotspot_catchments.png（示意图）
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import pathlib
import re
import sys
from collections import defaultdict, deque

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

DATA = ROOT / "console" / "data"
OUT_R = 50.0
WELL_R = 200.0
SERVE_R = 46.0
Q_MM = 23.53
BUF = 4000.0


def read_net(paths):
    """COORDINATES + CONDUITS/PUMPS/ORIFICES/WEIRS 的上下游 → 节点坐标、有向边。"""
    xy, edges = {}, []
    for p in paths:
        cur = None
        for ln in open(p, encoding="utf-8", errors="replace"):
            s = ln.strip()
            if s.startswith("["):
                cur = s.strip("[]").upper(); continue
            if not s or s.startswith(";") or cur is None:
                continue
            q = s.split()
            if cur == "COORDINATES" and len(q) >= 3:
                xy[q[0]] = (float(q[1]), float(q[2]))
            elif cur in ("CONDUITS", "PUMPS", "ORIFICES", "WEIRS") and len(q) >= 3:
                edges.append((q[1], q[2]))
    return xy, edges


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("ids", nargs="+")
    ap.add_argument("--tag", default="", help="产物文件名后缀（如 _C1），不覆盖默认的 3 点版本")
    a = ap.parse_args(argv); sfx = a.tag
    pd_ = os.path.join(os.path.dirname(importlib.util.find_spec("rasterio").origin), "proj_data")
    os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = pd_
    import geopandas as gpd
    import pyflwdir
    import rasterio
    from rasterio.features import rasterize, shapes
    from rasterio.windows import from_bounds
    from scipy.spatial import cKDTree
    from shapely import voronoi_polygons
    from shapely.geometry import MultiPoint, Point, box, shape
    from shapely.ops import unary_union
    from shapely.validation import make_valid
    from swbrain import db

    H = gpd.read_file(DATA / "hotspots.geojson").to_crs(32640)
    H = H[H.hotspot_id.isin(a.ids)].set_index("hotspot_id").loc[a.ids]
    P = {k: (r.geometry.x, r.geometry.y) for k, r in H.iterrows()}
    X = np.array(list(P.values()))
    print("热点：", {k: tuple(round(v) for v in p) for k, p in P.items()})

    # ---------------- 管网：起点井 → 上游
    xy, edges = read_net(sorted(WORK.glob("City_L1_v2_p*.inp")))
    up = defaultdict(list)
    for f, t in edges:
        up[t].append(f)
    over = {}
    ovf_csv = ROOT / "out/交付/全市_61mm2h/L1_既有管网/一维/溢水点/溢水点汇总.csv"
    if ovf_csv.exists():
        with open(ovf_csv, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                over[r["井号"]] = r
    else:
        # 新机器上没有全市交付目录（city_node_series.py 要另一套逐时结果）：从全市一维 .rpt 的 Node Flooding Summary 取
        # 「6 h 溢流 ≥ 1 m³ 的井」。2026-09-30 复现核验：干净环境里这样取的 69,157 口井与溢水点汇总.csv 逐口相同、累计溢流合计相同
        for rp in sorted(WORK.glob("City_L1_v2_p*.rpt")):
            txt = rp.read_text(encoding="utf-8", errors="replace"); i = txt.find("Node Flooding Summary")
            for ln in (txt[i:].splitlines()[9:] if i >= 0 else []):
                m = re.match(r"\s+(\S+)\s+[\d.]+\s+[\d.]+\s+\d+\s+[\d:]+\s+([\d.]+)", ln)
                if not m:
                    if not ln.strip() or ln.strip().startswith("*"):
                        break
                    continue
                v = float(m.group(2)) * 1000.0                     # 10^6 ltr → m³
                if v >= 1.0:
                    over[m.group(1)] = {"井号": m.group(1), "累计溢流_m3": v}
        print(f"  溢水井取自全市一维 .rpt：{len(over):,} 口（没有 {ovf_csv.name}）")
    names = [k for k in xy]; NX = np.array([xy[k] for k in names]); tr_all = cKDTree(NX)
    net = {}
    for k, p in P.items():
        start = [names[i] for i in tr_all.query_ball_point(p, WELL_R) if names[i] in over]
        seen, dq = set(start), deque(start)
        while dq:
            n = dq.popleft()
            for m in up.get(n, ()):
                if m not in seen:
                    seen.add(m); dq.append(m)
        net[k] = {"start": start, "up": seen}
        print(f"  {k}：起点溢水井 {len(start)} 口 → 上游井 {len(seen):,} 口")
    allnodes = set().union(*[v["up"] for v in net.values()])
    # 服务范围：离最近井 ≤ SERVE_R 的地面，按最近井分给它（Voronoi ∩ 半径）
    sel = np.array([xy[n] for n in allnodes])
    x0, y0 = np.minimum(sel.min(0), X.min(0)) - BUF; x1, y1 = np.maximum(sel.max(0), X.max(0)) + BUF
    near_idx = tr_all.query_ball_point([(x0 + x1) / 2, (y0 + y1) / 2], max(x1 - x0, y1 - y0))
    NN = [names[i] for i in near_idx if x0 <= NX[i, 0] <= x1 and y0 <= NX[i, 1] <= y1]
    vor = voronoi_polygons(MultiPoint([xy[n] for n in NN]), extend_to=box(x0, y0, x1, y1))
    tr_nn = cKDTree(np.array([xy[n] for n in NN]))
    cell = {}
    for g in vor.geoms:
        c = g.representative_point(); j = tr_nn.query((c.x, c.y))[1]
        cell[NN[j]] = g.intersection(Point(xy[NN[j]]).buffer(SERVE_R))
    for k in P:
        net[k]["poly"] = make_valid(unary_union([cell[n] for n in net[k]["up"] if n in cell])).buffer(0)

    # ---------------- 地表：D8
    cfg = db.load_config(None)
    with rasterio.open((cfg.get("terrain") or {})["dtm_path"]) as ds:
        bx0, by0 = X.min(0) - BUF; bx1, by1 = X.max(0) + BUF
        win = from_bounds(bx0, by0, bx1, by1, ds.transform).round_offsets().round_lengths()
        z = ds.read(1, window=win).astype("float32"); tf = ds.window_transform(win); nod = ds.nodata; res = ds.res[0]
    bad = ~np.isfinite(z) | (z < -1000) | ((nod is not None) & (z == np.float32(nod))); z[bad] = -9999.0
    flw = pyflwdir.from_dem(z, nodata=-9999.0, transform=tf, latlon=False)
    ids = np.arange(1, len(P) + 1, dtype=np.int32)
    lab = rasterize(zip([Point(p).buffer(OUT_R) for p in P.values()], ids), out_shape=z.shape, transform=tf, fill=0, all_touched=True, dtype="int32")
    ix = np.flatnonzero(lab)
    bas = flw.basins(idxs=ix, ids=lab.ravel()[ix]).astype("int32")
    edge = np.zeros_like(bas, bool); edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    surf = {}
    for i, k in zip(ids, P):
        m = bas == i
        poly = unary_union([shape(g) for g, v in shapes(m.astype("uint8"), mask=m, transform=tf) if v])
        surf[k] = {"poly": make_valid(poly.simplify(res, preserve_topology=True)).buffer(0), "touches_edge": bool((m & edge).any())}

    # ---------------- 汇总
    # 客户 flood 库的 39 个汇水区面（swbrain.floodgis 导出，要连 flood 库）只用来注记「与哪些客户汇水区重叠」，不影响汇水区本身；
    # 新机器上连不了库就不注记（2026-09-30 复现：用户说那 39 面是手描的，只作对照）
    fcs = DATA / "catchments_sw.geojson"
    C = gpd.read_file(fcs).to_crs(32640) if fcs.exists() else gpd.GeoDataFrame({"label": []}, geometry=[], crs=32640)
    C["geometry"] = [make_valid(g).buffer(0) for g in C.geometry]
    if not fcs.exists():
        print(f"  没有 {fcs.name}（flood 库导出）：不注记与客户汇水区的重叠")
    zz = {}
    for st in ("L1", "L2"):
        zf = WORK / "c2d" / f"City2D_{st}_2d.npz"
        if zf.exists():
            d = np.load(zf); zz[st] = (cKDTree(d["xy"].astype(float)), d["hmax"].astype(float), d["area"].astype(float))
    feats, rows = [], []
    for k in P:
        s, n = surf[k]["poly"], net[k]["poly"]; u = make_valid(s.union(n)).buffer(0)
        ov = sorted(({"label": c.label, "pct": round(u.intersection(c.geometry).area / u.area * 100)} for _, c in C.iterrows()
                     if u.intersects(c.geometry) and u.intersection(c.geometry).area / u.area >= 0.01), key=lambda o: -o["pct"])
        ov = list({o["label"]: o for o in ov}.values())
        ovf = sum(float(over[w]["累计溢流_m3"]) for w in net[k]["start"])
        r = {"hotspot_id": k, "area": H.loc[k, "area"], "location": H.loc[k, "location"], "criticality": H.loc[k, "criticality"],
             "surface_ha": round(s.area / 1e4, 1), "network_ha": round(n.area / 1e4, 1), "overlap_ha": round(s.intersection(n).area / 1e4, 1),
             "total_ha": round(u.area / 1e4, 1), "surface_touches_edge": surf[k]["touches_edge"],
             "start_wells": len(net[k]["start"]), "upstream_wells": len(net[k]["up"]),
             "runoff_surface_m3": round(s.area * Q_MM / 1000), "runoff_network_m3": round(n.area * Q_MM / 1000), "runoff_total_m3": round(u.area * Q_MM / 1000),
             "overflow_start_wells_L1_m3": round(ovf), "client_catch": ov}
        rows.append(r)
        for kind, g in (("地表", s), ("管网", n), ("合计", u)):
            feats.append({"geometry": g, "hotspot_id": k, "kind": kind, "area_ha": round(g.area / 1e4, 1)})
        print(f"  {k}：地表 {r['surface_ha']} ha、管网 {r['network_ha']} ha（重叠 {r['overlap_ha']}）→ 合计 {r['total_ha']} ha；"
              f"净雨来水 {r['runoff_total_m3']:,} m³；起点井 L1 溢流 {r['overflow_start_wells_L1_m3']:,} m³；客户汇水区 {ov or '无'}"
              + ("；⚠ 地表汇水区碰到窗口边" if r["surface_touches_edge"] else ""))
    # 三点之间管网上游的重合
    ks = list(P); shared = {}
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            shared[f"{ks[i]} ∩ {ks[j]}"] = len(net[ks[i]]["up"] & net[ks[j]]["up"])
    g = gpd.GeoDataFrame([{k: v for k, v in f.items() if k != "geometry"} for f in feats], geometry=[f["geometry"] for f in feats], crs=32640).to_crs(4326)
    gj = json.loads(g.to_json(drop_id=True))
    gj.update({"layer": "hotspot_catch", "note": f"地表：5 m DTM 填洼 + D8，出口为热点周边 {OUT_R:g} m；管网：热点周边 {WELL_R:g} m 内溢水井的全部上游井、各自 {SERVE_R:g} m 内最近的地面",
               "rain": f"61 mm / 2 h，SCS CN 82 → 净雨 {Q_MM} mm"})
    (DATA / f"hotspot_catch{sfx}.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    (ROOT / "out" / f"hotspot_catchments{sfx}.json").write_text(json.dumps({"method": gj["note"], "rain": gj["rain"], "hotspots": rows, "shared_upstream_wells": shared},
                                                                      ensure_ascii=False, indent=1), encoding="utf-8")
    print("  管网上游重合的井：", shared)
    # 示意图
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    fig, ax = plt.subplots(figsize=(9, 9))
    cols = ["#1d4ed8", "#b45309", "#7c3aed"]
    C[C.intersects(box(*unary_union([f["geometry"] for f in feats]).bounds))].boundary.plot(ax=ax, color="#94a3b8", lw=0.8, ls="--")
    for c, k in zip(cols, P):
        gpd.GeoSeries([net[k]["poly"]]).plot(ax=ax, color=c, alpha=.18)
        gpd.GeoSeries([surf[k]["poly"]]).boundary.plot(ax=ax, color=c, lw=1.6)
        ax.plot(*P[k], "o", color=c, ms=9, mec="white"); ax.annotate(k, P[k], xytext=(6, 6), textcoords="offset points", color=c, weight="bold")
    ax.set_title("热点汇水区：实线 = 地表（D8），色块 = 管网（上游井服务范围）；灰虚线 = 客户汇水区"); ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    fig.savefig(ROOT / "out" / f"hotspot_catchments{sfx}.png", dpi=110, bbox_inches="tight")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
