# -*- coding: utf-8 -*-
"""C1 热点群的一二维模型（先做 L1）。参数全部来自参数对齐页 https://claude.ai/artifact/CkRHWiRfwvXCBvwBQuxNp4 （2026-09-28 确认）。

  A1 范围      C1 的 7 个热点 + ADM-487
  A2 一维管网  全市一维模型（客户 GDB 重建，build_city1d）截取：触及计算域的连通块整块取（域外部分只算一维）
  A3 计算域    8 个热点的地表 + 管网汇水区外扩 300 m = 42.8 km²（tools/c1_existing_pumps.domain），自由出流
  B4–B7 雨     61 mm / 2 h 与 96 mm / 2 h，客户 2 h 设计雨型（96 mm 按同一雨型等比放大）
  B8           全域均匀；离最近井超过服务半径的地面，净雨直接下在二维上
  C9 L1        现状管网 + 客户 GDB 的现状泵站（tools/c1_existing_pumps.py，排放点与流量规则见那里）
  D13          启停 = 井深 50% / 10%；固定流量；备注写几台就几台同时开
  E16 产流     SCS CN 82、Tc 30 min，SCS 单位线（同全市一维）
  E17 时长     6 h（2 h 雨 + 4 h）
  E18 二维     15 m（单元面积上限 15² / 3 = 75 m²），地形取 5 m DTM 原值（couple2way_v2 C2D_DEM_RAW=1）
  E20 输出     同全市，另存每帧流速（C2D_SAVE_UV=1），.sww 带流速

现状泵站在一维里的写法（本脚本的实现，交用户核对）：
  · 湿井 = 泵站位置的那口井；井底加深到「地面 − 客户库井深」，启停水位相对加深后的井底
  · 泵 = SWMM PUMP2 曲线（与水深无关的固定流量），流量 = 台数 × 单台流量
  · ADM_RD-01：客户压力管（2,132 m）在全市模型里被当成重力管读进来了，这里删掉那根管，换成泵
  · 排进出水口 OFL_137 的：每台泵在 OFL_137 位置另建一个自由出水口（SWMM 的出水口只能挂一根管）
  · 抽出计算域的（PSS_98、PSS_99）：各接一个新的自由出水口节点，水离开模型
用法：python tools/build_c1.py --rain 61|96
产物：$WORK/C1_R61_L1.inp、c2d/dem_C1_R61.tif、c2d/pilot_C1_R61_L1.json、out/c1_L1_inputs_R61.json（输入清单）
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import build_city1d as B  # noqa: E402
import build_pilot as BP  # noqa: E402
from c1_existing_pumps import domain  # noqa: E402
from workdir import WORK  # noqa: E402

import datapaths  # noqa: E402  客户数据位置：config.toml [data]
GDB_DIR = str(datapaths.get("gdb_dir"))


def load_domain(path, where=None, layer=None, buffer=0.0, multi="error", log=print):
    """面文件 → 单个多边形（EPSG:32640）。二维外边界只能是一个环：
    where 按属性筛（SQL WHERE，如 NAME='Mussafah'），buffer 外扩 m，
    多块面：小于总面积 1% 的碎块丢掉并提示；剩下仍不止一块时 multi=error 报错、largest 只留最大块。
    内部的洞会被填上（二维不挖洞）。"""
    import geopandas as gpd
    from shapely.geometry import Polygon
    g = gpd.read_file(path, layer=layer, where=where) if (where or layer) else gpd.read_file(path)
    if g.empty:
        raise SystemExit(f"计算域：{path} 筛选后没有要素（where={where!r}）")
    if g.crs is None:
        raise SystemExit(f"计算域：{path} 没有坐标系，先在 GIS 里定义")
    if g.crs.is_geographic and (abs(g.total_bounds[[0, 2]]).max() > 180 or abs(g.total_bounds[[1, 3]]).max() > 90):
        # GeoJSON 没写坐标系时按 WGS84 读，投影坐标会被当成经纬度
        raise SystemExit(f"计算域：{path} 标的是经纬度但坐标是投影坐标（{g.total_bounds.round(0).tolist()}），坐标系标错了")
    D = g.to_crs(32640).geometry.union_all()
    if buffer:
        D = D.buffer(float(buffer))
    parts = sorted(getattr(D, "geoms", [D]), key=lambda p: -p.area)
    tot = sum(p.area for p in parts)
    tiny = [p for p in parts if p.area < 0.01 * tot]
    parts = [p for p in parts if p.area >= 0.01 * tot]
    if tiny:
        log(f"  计算域：丢掉 {len(tiny)} 块碎片（共 {sum(p.area for p in tiny) / 1e6:.3f} km²，各 < 总面积 1%）")
    if len(parts) > 1:
        desc = "、".join(f"{p.area / 1e6:.2f}" for p in parts)
        if multi != "largest":
            raise SystemExit(f"计算域是 {len(parts)} 块不相连的面（{desc} km²），二维只能算一块。"
                             f"可以加 --buffer 让它们连上，或 --multi largest 只留最大块，或分成几个情景分别算")
        log(f"  计算域：{len(parts)} 块不相连（{desc} km²），只留最大块")
    D = parts[0]
    if D.interiors:
        log(f"  计算域：填上 {len(D.interiors)} 个内部的洞（{(Polygon(D.exterior).area - D.area) / 1e6:.3f} km²）")
        D = Polygon(D.exterior)
    if D.area < 0.1e6:
        raise SystemExit(f"计算域：{path} 只有 {D.area / 1e6:.4f} km²，坐标系可能不对")
    log(f"  计算域：{path}" + (f" [{where}]" if where else "") + (f" 外扩 {buffer:g} m" if buffer else "") + f" → {D.area / 1e6:.2f} km²")
    return D


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--rain", type=float, required=True, help="总雨量 mm（61 / 96 / 61.34 …）")
    ap.add_argument("--dur", type=float, default=2.0, help="降雨历时 h；雨型取客户 Mussafah 模型里同历时的设计雨型（24 h 那条就是 SCS II 型）")
    ap.add_argument("--tag", default=None, help="情景名，缺省 R{雨量取整}（如 R61）；设计暴雨用 D24；客户单元版计算域用 U61")
    ap.add_argument("--domain", default=None, help="计算域面文件（缺省 = 8 个热点汇水区外扩 300 m）；客户单元版：out/交付/C1汇水区_客户单元/C1_汇水区_合并.shp（用户 2026-09-29「都按建议，开始跑」）")
    ap.add_argument("--where", default=None, help="按属性筛选计算域要素（SQL WHERE），如 \"NAME='Mussafah'\"")
    ap.add_argument("--layer", default=None, help="gpkg / gdb 里的图层名")
    ap.add_argument("--buffer", type=float, default=0.0, help="计算域外扩 m（缺省 0；缺省计算域本身已外扩 300 m）")
    ap.add_argument("--multi", choices=["error", "largest"], default="error", help="计算域是几块不相连的面时：报错（缺省）/ 只留最大块")
    ap.add_argument("--res", type=float, default=15.0, help="二维网格 m（计算域边界按它简化；单元数估算用；缺省 15）")
    a = ap.parse_args(argv)
    RES = float(a.res)
    a.tag = a.tag or f"R{int(round(a.rain))}"
    if not a.domain and (a.where or a.layer or a.buffer):
        raise SystemExit("--where / --layer / --buffer 要配 --domain 用")
    import geopandas as gpd
    import numpy as np
    import rasterio
    from rasterio.windows import from_bounds
    from shapely.geometry import Point
    from shapely.prepared import prep
    from swbrain import db

    state = "L1"
    B.P_MM = float(a.rain); B.DUR_H = float(a.dur); B.SIM_H = B.DUR_H + 4.0      # E17：降雨历时 + 4 h
    name = f"C1_{a.tag}"
    if a.domain:
        D = load_domain(a.domain, a.where, a.layer, a.buffer, a.multi)
    else:
        D = domain()
    print(f"{name} · {state} · 计算域 {D.area / 1e6:.1f} km² · {B.P_MM:g} mm / {B.DUR_H:g} h")
    PSJ = json.loads((ROOT / "out" / "c1_existing_pumps.json").read_text(encoding="utf-8"))["stations"]
    drop_uid = {u for s in PSJ if s.get("q_each_lps") and s.get("forcemain_unitids") for u in s["forcemain_unitids"]}

    # ---- 管网：连通块整块取
    df = B.read_pipes(None, log=lambda *x: None)
    xy_all, ground, inv, (vu, vd) = B.build_nodes(df, log=lambda *x: None)
    sc = df.StartCode.astype("string").fillna("").values
    ec = df.EndCode.astype("string").fillna("").values
    f = BP.components(sc, ec)
    comp = collections.defaultdict(list)
    for n in xy_all:
        comp[f(n)].append(n)
    pr = prep(D.buffer(200.0))
    inb = lambda n: pr.contains(Point(xy_all[n]))
    # 触及计算域的连通块**整块**取（2026-09-28 改）：按全市试跑的「≥ 50% 节点在域内」会丢掉给 ADM-94 / 192 排水的大网
    # （域内 9,211 口井只剩 5,485）。域外那部分只算一维，冒出的水不进二维（记为损失）
    pd0 = prep(D)
    keep_roots = {r for r, ns in comp.items() if len(ns) >= 2 and any(pd0.contains(Point(xy_all[n])) for n in ns)}
    keep = {n for r in keep_roots for n in comp[r]}
    xy = {n: xy_all[n] for n in xy_all if n in keep}
    outs = B.read_outfalls(set(xy), log=lambda *x: None)
    ln = df.LineLength.astype("float64").values
    dia = df.PIPE_DIAMETER.astype("float64").values / 1000.0
    uid = df.UNITID.astype("string").fillna("").values
    pipes, dropped = [], []
    for i in range(len(df)):
        n1, n2 = sc[i], ec[i]
        if n1 == n2 or n1 not in keep or n2 not in keep:
            continue
        if uid[i] in drop_uid:
            dropped.append(uid[i]); continue
        z1, z2 = float(vu[i]), float(vd[i])
        if z2 > z1:
            n1, n2, z1, z2 = n2, n1, z2, z1
        z1 = max(z1, inv[n1]); z2 = max(z2, inv[n2])
        pipes.append({"id": f"c_{i}", "n1": n1, "n2": n2, "len": float(ln[i]) if ln[i] > 0 else B.MIN_LEN,
                      "z1": z1, "z2": z2, "d": float(dia[i] if dia[i] > 0 else 0.3), "uid": ""})
    n_in = sum(map(inb, xy))
    print(f"  连通块 {len(keep_roots)}、节点 {len(xy):,}（域内 {n_in:,}）、管 {len(pipes):,}、出水口 {len(outs)}；删掉换成泵的压力管 {dropped}")
    area = B.served_areas(xy, log=lambda *x: None)
    A = sum(area.values())
    R_served = B.K_RADIUS * math.sqrt(A / max(len(xy), 1))
    print(f"  管网服务面积 {A / 1e6:,.1f} km²，服务半径 {R_served:.1f} m")

    # ---- 写 .inp
    out = WORK / f"{name}_{state}.inp"
    if B.DUR_H == 2.0:
        t, exc, q = BP.rain_series()
    else:                                    # 长历时：同一套做法，过程线长度按模拟时长（build_pilot 的写死 12 h）
        import runoff as R
        dt_h = 1.0 / 60.0
        shape = R.read_shape(WORK / "Mussafah_baseline.inp", B.DUR_H, log=print)
        t, P_cum = R.hyetograph(B.P_MM, B.DUR_H, dt_h, shape, 0.0, total_h=B.SIM_H + 1.0)
        exc = R.scs_cum_excess(P_cum, B.CN)
        q = np.convolve(np.diff(exc, append=exc[-1]), R.unit_hydrograph(B.TC_MIN, dt_h, 10_000.0), mode="full")[:len(t)]
    B.unit_runoff = lambda: [(f"{int(round(h * 60)) // 60:02d}:{int(round(h * 60)) % 60:02d}", float(v)) for h, v in zip(t, q)]
    of_dia = {}
    for c in pipes:
        for nd in (c["n1"], c["n2"]):
            if nd in outs and c["d"] > of_dia.get(nd, 0.0):
                of_dia[nd] = c["d"]
    B.write_inp(out, xy, ground, inv, pipes, outs, area, None, of_dia, log=lambda *x: None)
    txt = out.read_text(encoding="utf-8").replace(f"THREADS  {B.SWMM_THREADS}", "THREADS  8", 1)
    txt = txt.replace(";; 全市一维管网（GDB 重建）", f";; C1 热点群一维管网（全市 GDB 重建模型截取）· {state} · {B.P_MM:g} mm / 2 h", 1)

    # ---- 现状泵站
    import pyogrio
    OF = pyogrio.read_dataframe(GDB_DIR, layer="OUTFALL", columns=["UNITID", "PointCode"])
    OF = OF.set_geometry(OF.geometry).to_crs(32640) if OF.crs else OF
    pumps, junc_fix, new_out = [], {}, []
    for s in PSJ:
        n_p, qe = s.get("n_pumps"), s.get("q_each_lps")
        if not n_p or not qe:
            continue
        wet = s["wetwell_node"]
        if wet not in xy:
            print(f"  ! {s['ps_id']} 湿井 {wet} 不在截取的管网里，跳过"); continue
        depth = float(s["well_depth_m"])
        z_bot = min(inv[wet], ground[wet] - depth)
        junc_fix[wet] = z_bot
        src = s["discharge_src"]
        if s.get("discharge_node") and s["discharge_node"] in xy and "压力管" in src:
            to, how = s["discharge_node"], src
        elif s["ps_id"] in ("ADM_RD-09", "ADM_RD-26", "ADM_RD-27"):
            # 进 OFL_137：SWMM 出水口只能挂一根管（原出水口已挂着重力管），每台泵在 OFL_137 位置另建一个自由出水口
            g = OF[OF.UNITID == "SW_MUS_MN1_OFL_137"].geometry.iloc[0]
            to = f"X_OFL137_{s['ps_id']}".replace(" ", "_"); new_out.append((to, g.x + 3.0, g.y + 3.0, z_bot - 1.0)); how = "出水口 OFL_137（推断，RD-09 相距 1 m）"
        else:
            to = f"X_{s['unitid']}"; new_out.append((to, s["x"] + 5.0, s["y"] + 5.0, z_bot - 1.0)); how = "抽出计算域（新建出水口）"
        pumps.append({"id": f"PS_{s['ps_id']}".replace(" ", "_"), "from": wet, "to": to, "q_m3s": n_p * qe / 1000.0,
                      "on": 0.5 * depth, "off": 0.1 * depth, "z_bot": z_bot, "inv_orig": inv[wet], "ground": ground[wet],
                      "how": how, "ps": s["ps_id"], "unitid": s["unitid"], "n": n_p, "q_each": qe, "q_src": s["q_src"]})
    # 井底加深
    lines = txt.split("\n"); i0 = lines.index("[JUNCTIONS]")
    for j in range(i0 + 1, len(lines)):
        if not lines[j].strip():
            break
        k = lines[j].split()[0]
        if k in junc_fix:
            lines[j] = f"{k}  {junc_fix[k]:.3f}  {max(ground[k] - junc_fix[k], 0.5):.3f}  0  0  0"
    txt = "\n".join(lines)
    if new_out:
        txt = txt.replace("[OUTFALLS]\n", "[OUTFALLS]\n" + "".join(f"{k}  {z:.3f}  FREE  NO\n" for k, x, y, z in new_out), 1)
        txt = txt.replace("[COORDINATES]\n", "[COORDINATES]\n" + "".join(f"{k}  {x:.2f}  {y:.2f}\n" for k, x, y, z in new_out), 1)
    sec = "[PUMPS]\n" + "".join(f"{p['id']}  {p['from']}  {p['to']}  PC_{p['id']}  ON  {p['on']:.2f}  {p['off']:.2f}\n" for p in pumps) + "\n"
    crv = "".join(f"PC_{p['id']}  PUMP2  0  {p['q_m3s']:.4f}\nPC_{p['id']}  100  {p['q_m3s']:.4f}\n" for p in pumps)
    txt = txt.replace("[XSECTIONS]", sec + "[XSECTIONS]", 1)
    txt = re.sub(r"\[CURVES\]\n", "[CURVES]\n" + crv, txt, count=1) if "[CURVES]" in txt else txt.replace("[TIMESERIES]", "[CURVES]\n" + crv + "\n[TIMESERIES]", 1)
    out.write_text(txt, encoding="utf-8")
    print(f"  → {out.name}（{out.stat().st_size / 1048576:.1f} MB），泵站 {len(pumps)}")
    for p in pumps:
        print(f"    {p['id']:<16} {p['n']}×{p['q_each']:g} L/s  {p['from']} → {p['to']}（{p['how']}）  井底 {p['inv_orig']:.2f} → {p['z_bot']:.2f} m，开 {p['on']:.2f} / 停 {p['off']:.2f} m")

    # ---- 二维
    c2d = WORK / "c2d"; c2d.mkdir(exist_ok=True)
    ring = list(D.exterior.coords)[:-1]
    if not D.exterior.is_ccw:
        ring = ring[::-1]
    poly = [[round(x, 1), round(y, 1)] for x, y in D.simplify(RES).exterior.coords[:-1]]
    X0, Y0, X1, Y1 = D.bounds
    dem_f = c2d / f"dem_{name}.tif"
    with rasterio.open((db.load_config(None).get("terrain") or {})["dtm_path"]) as ds:
        win = from_bounds(X0 - 50, Y0 - 50, X1 + 50, Y1 + 50, ds.transform).round_offsets().round_lengths()
        z = ds.read(1, window=win)
        prof = ds.profile.copy(); prof.update(width=z.shape[1], height=z.shape[0], transform=ds.window_transform(win), compress="deflate")
        with rasterio.open(dem_f, "w", **prof) as w:
            w.write(z, 1)
        dtm_res = ds.res[0]
    pd_ = prep(D)
    meta = {"name": name, "state": state, "box": (X0, Y0, X1, Y1), "domain_polygon": poly, "R_served_m": R_served,
            "nodes": {k: [round(v[0], 2), round(v[1], 2)] for k, v in xy.items() if pd_.contains(Point(v))},
            "ponds": [], "rain_t_h": [round(float(x), 4) for x in t], "rain_excess_mm": [round(float(x), 4) for x in exc],
            "pumps_1d": pumps, "note": "C1 L1：参数见参数对齐页；现状泵站排放点中 RD-26/27（→ OFL_137）与 PSS_98/99（抽出计算域）是推断"}
    (c2d / f"pilot_{name}_{state}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    n_tri_est = D.area / (0.65 * RES * RES / 3.0)
    inputs = {"name": name, "state": state, "rain_mm": B.P_MM, "dur_h": B.DUR_H, "sim_h": B.SIM_H, "cn": B.CN, "tc_min": B.TC_MIN,
              "rain_excess_mm": round(float(exc[-1]), 2),
              "domain_src": {"file": a.domain, "where": a.where, "layer": a.layer, "buffer_m": a.buffer} if a.domain else "8 个热点汇水区外扩 300 m",
              "domain_km2": round(D.area / 1e6, 2), "domain_vertices": len(poly),
              "n_components": len(keep_roots), "n_nodes": len(xy), "n_nodes_in_domain": len(meta["nodes"]), "n_pipes": len(pipes),
              "n_outfalls": len(outs), "served_km2": round(A / 1e6, 2), "R_served_m": round(R_served, 1),
              "mesh_res_m": RES, "max_tri_m2": RES * RES / 3.0, "n_tri_est": int(n_tri_est), "dtm_res_m": dtm_res,
              "dem_px": [int(z.shape[1]), int(z.shape[0])], "pumps": pumps, "dropped_forcemains": dropped,
              "manning_2d": 0.030, "dt_1d_s": 30, "dt_couple_s": 60, "frame_s": 300}
    (ROOT / "out" / f"c1_L1_inputs_{a.tag}.json").write_text(json.dumps(inputs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  二维：计算域 {len(poly)} 个顶点，节点（域内）{len(meta['nodes']):,}，DEM {z.shape[1]}×{z.shape[0]} @ {dtm_res:g} m，"
          f"三角单元约 {n_tri_est:,.0f}（估）→ pilot_{name}_{state}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
