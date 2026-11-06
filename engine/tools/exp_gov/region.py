"""区域数据治理与验证（标准作业程序场景①：阿布扎比内换一个区域；docs/数据治理标准作业程序.md）。

DMT 全市资产库已经治理过（全市治理链 run_chain.sh），换一个区域不用重新治理，只做：
  截取 → 区域检查（高程基准、连通、遗留问题、和顾问模型对账）→ 治理前后一二维验证 → 报告。
一条命令跑完：bash tools/exp_gov/run_region.sh tools/exp_gov/regions/<区域>.json（ArcGIS 的两步在那里调）。

  python tools/exp_gov/region.py domain  <配置>                 计算域 → out/exp_gov/regions/<区域>/domain.geojson
  python tools/exp_gov/region.py check   <配置>                 区域检查 → check.json、<区域>_区域检查.xlsx / .gpkg
  python tools/exp_gov/region.py build   <配置> --storm D61 --model R|G
  python tools/exp_gov/region.py run     <配置> [--par 2 --threads 6]   建模 + 一二维（docker）+ 指标
  python tools/exp_gov/region.py report  <配置>                 对比表 → <区域>_治理前后验证.md / compare.json

配置（regions/<区域>.json）：name、code、work（全市治理工作目录）、domain（from: parsons | geojson）、
consultant（type: parsons | none，reference_run：已有的顾问模型算例）、storms、models。
模型：R = DMT 原样（src_dmt.gdb，不改任何值）；G = 治理后（全市治理导出的 Utility Network）；C = 顾问模型（参照，用已有算例）。
**所有模型用同一套产流**（RD-A 的教训：产流不同会把管网差异掩盖掉，后来专门加算了对照 ⑤ 才分清），入流挂到各模型最近的井。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
from collections import Counter, defaultdict

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(HERE))
import rb01_build as RB  # noqa: E402
import rda_build as RD  # noqa: E402
import v_build as VB  # noqa: E402
import v_metrics as VM  # noqa: E402
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
IMAGE, RES, XCH = "anuga-swmm:1.0", 15, 60
DTM_NEAR = 1.0          # 治理后井盖 − DTM 在 ±1 m 内算「对得上」
DATUM_BAND = (95, 105)  # DMT 原样井盖 − DTM 落在这里 = 「实际 + 100 m」惯例
MATCH_TOL = 2.0         # 顾问模型的管和 DMT 的管两端都在 2 m 内算同一根（RD-A 融合用 0.5 / 2 m）


def cfg_load(p):
    c = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    c["dir"] = ROOT / "out" / "exp_gov" / "regions" / c["name"]
    c["dir"].mkdir(parents=True, exist_ok=True)
    return c


def domain_of(c):
    import geopandas as gpd
    p = c["dir"] / "domain.geojson"
    return gpd.read_file(p).to_crs(32640).geometry.iloc[0]


def consultant(c):
    """顾问模型（Parsons SHP）：管、井、出水口；没有顾问模型时返回 None"""
    k = c.get("consultant") or {}
    if k.get("type") != "parsons":
        return None
    return RB.load(pathlib.Path(k["path"]))


# ------------------------------------------------------------------ 计算域
def cmd_domain(c):
    import geopandas as gpd
    d = c["domain"]
    if d["from"] == "parsons":
        D = RB.domain(RB.load(pathlib.Path(d["path"]))["ca"])
    else:
        D = gpd.read_file(d["path"]).to_crs(32640).union_all()
    gpd.GeoDataFrame({"name": [c["name"]]}, geometry=[D], crs=32640).to_file(c["dir"] / "domain.geojson", driver="GeoJSON")
    print(f"计算域 {c['name']}：{D.area / 1e6:.3f} km² → {c['dir'] / 'domain.geojson'}")


# ------------------------------------------------------------------ 区域检查
def cmd_check(c):
    """SOP 第 2 步的四项检查。每项给数和结论；结论为「停」的，先问用户再往下走。"""
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import LineString, Point
    from shapely.prepared import prep
    D = domain_of(c); pD = prep(D)
    raw = json.loads((c["dir"] / "extract_raw.json").read_text(encoding="utf-8"))
    gov = json.loads((c["dir"] / "extract_gov.json").read_text(encoding="utf-8"))
    dtm = VB.dtm_sampler()
    res, verdict = {"name": c["name"], "domain_km2": round(D.area / 1e6, 3)}, []
    inD = lambda xy: pD.contains(Point(xy))

    # 1) 高程基准：DMT 原样井盖 − DTM 应集中在 +100 m；治理后井盖 − DTM 应集中在 0
    pts = [(r["_xy"], r.get("COVER_LEVE")) for k in ("manholes", "inlets") for r in raw[k] if inD(r["_xy"])]
    pts = [(xy, float(v)) for xy, v in pts if v not in (None, 0) and -50 < float(v) < 500]
    d_raw = np.array([v for (_, v) in pts]) - dtm([xy for xy, _ in pts]) if pts else np.array([])
    d_raw = d_raw[np.isfinite(d_raw)]
    g_rim = {v["assetid"]: v.get("rimelev") for v in gov["vaults"]}
    gp = [(d["_xy"], d.get("coverelev") if d["group"] == "Catch Basin" else g_rim.get(str(d["assetid"])[:-3]))
          for d in gov["devices"] if d["group"] in ("Catch Basin", "Manhole Channel") and inD(d["_xy"])]
    gp = [(xy, float(v)) for xy, v in gp if v is not None]
    d_gov = np.array([v for (_, v) in gp]) - dtm([xy for xy, _ in gp]) if gp else np.array([])
    d_gov = d_gov[np.isfinite(d_gov)]
    band = float(np.mean((d_raw >= DATUM_BAND[0]) & (d_raw <= DATUM_BAND[1]))) if len(d_raw) else None
    near = float(np.mean(np.abs(d_gov) <= DTM_NEAR)) if len(d_gov) else None
    res["datum"] = {"raw_points": len(d_raw), "raw_cover_minus_dtm_median": round(float(np.median(d_raw)), 2) if len(d_raw) else None,
                    "raw_share_95_105": round(band, 3) if band is not None else None,
                    "gov_points": len(d_gov), "gov_cover_minus_dtm_median": round(float(np.median(d_gov)), 2) if len(d_gov) else None,
                    "gov_share_within_1m": round(near, 3) if near is not None else None}
    ok = near is not None and near >= 0.9
    verdict.append(("高程基准", "过" if ok else "停", f"治理后井盖 − DTM 在 ±1 m 内的占 {near:.0%}（原样 +95–105 m 的占 {band:.0%}）"
                    if near is not None and band is not None else "没有可比的点"))

    # 2) 连通：治理后区域内的管，挂上子网（能追踪到出水口 / 终点）的比例
    L = [r for r in gov["lines"] if r.get("assetstate") != 0 and pD.intersects(LineString([r["_xy"][0], *r.get("_vx", []), r["_xy"][1]]))]
    tr = [r for r in L if r.get("smasubnetworkname") not in (None, "", "Unknown")]
    subs = Counter(r["smasubnetworkname"] for r in tr)
    share = len(tr) / max(len(L), 1)
    res["connectivity"] = {"gov_lines": len(L), "traced": len(tr), "traced_share": round(share, 3), "subnetworks": len(subs),
                           "top_subnetworks": subs.most_common(10)}
    verdict.append(("连通", "过" if share >= 0.85 else "看", f"区域内 {len(L):,} 根管，{share:.1%} 能追踪到出水口 / 终点（全市 90.0 %）"))

    # 3) 遗留问题：全市清单里落在区域内的
    lw = ROOT / "out" / "exp_gov" / "ledger"
    lx = sorted(lw.glob("全市遗留问题清单_第*轮.xlsx"), key=lambda p: int(p.stem.rsplit("第", 1)[1][:-1]))[-1]
    sheets = {}
    cache = lx.parent / "_cache"
    cache.mkdir(exist_ok=True)
    for sh, xc, yc in (("待处理（逐条）", "x", "y"), ("网络错误（逐处）", "x", "y"), ("未挂子网的系统", "root_x", "root_y"),
                       ("治理动作记录（逐条）", "x", "y")):
        cp = cache / f"{lx.stem}_{sh}.pkl"                    # 清单 16 万行，多个区域连着查时只读一次 Excel
        if cp.exists() and cp.stat().st_mtime > lx.stat().st_mtime:
            df = pd.read_pickle(cp)
        else:
            df = pd.read_excel(lx, sheet_name=sh)
            df.to_pickle(cp)
        if xc not in df:
            continue
        df = df.dropna(subset=[xc, yc])
        df = df[[pD.contains(Point(x, y)) for x, y in zip(df[xc], df[yc])]]
        sheets[sh] = df
    todo = sheets.get("待处理（逐条）", pd.DataFrame())
    by = todo.groupby(["类型", "需要谁做什么"]).size().rename("条数").reset_index().sort_values("条数", ascending=False) if len(todo) else pd.DataFrame()
    sev = todo["严重程度"].value_counts().to_dict() if len(todo) and "严重程度" in todo else {}
    res["residual"] = {"ledger": lx.name, "todo": len(todo), "todo_affects_model": int(sev.get("影响模型", 0)),
                       "todo_ledger_only": int(sev.get("只影响台账", 0)), "by_severity": sev,
                       "by_type": by.to_dict("records") if len(by) else [],
                       "network_errors": len(sheets.get("网络错误（逐处）", [])), "untraced_systems": len(sheets.get("未挂子网的系统", [])),
                       "actions": len(sheets.get("治理动作记录（逐条）", []))}
    verdict.append(("遗留问题", "过", f"区域内待客户处理 {len(todo):,} 条（其中影响水力模型的 {res['residual']['todo_affects_model']:,} 条）；"
                                    f"网络错误 {res['residual']['network_errors']} 处"))

    # 4) 和顾问模型对账（有顾问模型时）
    K = consultant(c)
    conf = pd.DataFrame()
    if K is not None:
        from scipy.spatial import cKDTree
        G = [r for r in L]
        ge = np.array([[*r["_xy"][0], *r["_xy"][1]] for r in G]) if G else np.zeros((0, 4))
        t0, t1 = cKDTree(ge[:, :2]), cKDTree(ge[:, 2:])
        co = K["co"]; matched, rows = 0, []
        used = set()
        for r in co.itertuples():
            cs = list(r.geometry.coords); a, b = cs[0], cs[-1]
            best = None
            for ta, tb, flip in ((t0, t1, False), (t1, t0, True)):
                da, ia = ta.query(a); db, ib = tb.query(b)
                if ia == ib and max(da, db) <= MATCH_TOL:
                    best = (ia, flip, max(da, db))
            if best is None:
                cand = set(t0.query_ball_point(a, MATCH_TOL)) & set(t1.query_ball_point(b, MATCH_TOL)) | \
                       set(t1.query_ball_point(a, MATCH_TOL)) & set(t0.query_ball_point(b, MATCH_TOL))
                best = (min(cand), None, None) if cand else None
            if best is None:
                rows.append({"类型": "顾问模型有、治理后 DMT 没有的管", "顾问编号": r.LABEL, "x": (a[0] + b[0]) / 2, "y": (a[1] + b[1]) / 2,
                             "顾问管径 mm": r.D, "顾问管底 起 / 止": f"{r.INV_STRT} / {r.INV_STP}"})
                continue
            matched += 1; used.add(best[0])
            g = G[best[0]]
            up, dn = (g.get("downelev"), g.get("upelev")) if best[1] else (g.get("upelev"), g.get("downelev"))
            for nm, kv, gv in (("管底（起点）", r.INV_STRT, up), ("管底（终点）", r.INV_STP, dn), ("管径", r.D, g.get("diameter"))):
                if kv is None or gv is None:
                    continue
                dv = float(gv) - float(kv)
                tol = 0.10 if nm != "管径" else 1.0
                if abs(dv) > tol:
                    rows.append({"类型": f"两边{nm}不一致", "顾问编号": r.LABEL, "DMT 资产编号": g.get("assetid"), "x": (a[0] + b[0]) / 2,
                                 "y": (a[1] + b[1]) / 2, "顾问值": kv, "DMT 值": gv, "差（DMT − 顾问）": round(dv, 3)})
        only_dmt = [G[i] for i in range(len(G)) if i not in used]
        for g in only_dmt:
            rows.append({"类型": "治理后 DMT 有、顾问模型没有的管", "DMT 资产编号": g.get("assetid"),
                         "x": (g["_xy"][0][0] + g["_xy"][1][0]) / 2, "y": (g["_xy"][0][1] + g["_xy"][1][1]) / 2, "DMT 管径 mm": g.get("diameter")})
        conf = pd.DataFrame(rows)
        inv = conf[conf["类型"].str.startswith("两边管底")] if len(conf) else conf
        res["consultant"] = {"label": c["consultant"]["label"], "consultant_pipes": len(co), "matched": matched,
                             "matched_share": round(matched / max(len(co), 1), 3), "dmt_pipes_in_domain": len(G), "only_dmt": len(only_dmt),
                             "by_type": conf.groupby("类型").size().to_dict() if len(conf) else {},
                             "invert_diff_median_m": round(float(inv["差（DMT − 顾问）"].abs().median()), 2) if len(inv) else None}
        verdict.append(("顾问模型对账", "过" if matched / max(len(co), 1) >= 0.6 else "看",
                        f"顾问模型 {len(co):,} 根管里 {matched:,} 根和治理后 DMT 对上（{matched / max(len(co), 1):.0%}）；"
                        f"不一致 {len(conf):,} 条，管底差中位 {res['consultant']['invert_diff_median_m']} m"))
    res["verdict"] = [{"检查": a, "结论": b, "说明": d} for a, b, d in verdict]

    # 写出：json、Excel（每张表带坐标）、gpkg（点图层）
    (c["dir"] / "check.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    xl = c["dir"] / f"{c['name']}_区域检查.xlsx"
    gp_ = c["dir"] / f"{c['name']}_区域检查.gpkg"
    if gp_.exists():
        gp_.unlink()
    with pd.ExcelWriter(xl) as w:
        pd.DataFrame(res["verdict"]).to_excel(w, sheet_name="0 结论", index=False)
        if len(by):
            by.to_excel(w, sheet_name="1 遗留问题汇总", index=False)
        for i, (sh, df) in enumerate(sheets.items(), 2):
            df.to_excel(w, sheet_name=f"{i} {sh}"[:31], index=False)
        if len(conf):
            conf.to_excel(w, sheet_name="9 顾问模型对账", index=False)
    for sh, df, xc, yc in [(sh, df, "root_x" if "root_x" in df else "x", "root_y" if "root_y" in df else "y") for sh, df in sheets.items()] + \
                          ([("顾问模型对账", conf, "x", "y")] if len(conf) else []):
        if len(df):
            g = gpd.GeoDataFrame(df.astype({k: str for k in df.columns if df[k].dtype == object}),
                                 geometry=gpd.points_from_xy(df[xc], df[yc]), crs=32640)
            g.to_file(gp_, layer=sh, driver="GPKG")
    for v in res["verdict"]:
        print(f"  [{v['结论']}] {v['检查']}：{v['说明']}")
    print("写出", xl, gp_)


# ------------------------------------------------------------------ 验证建模
def _end_caps(c, model, D):
    import geopandas as gpd
    bb = tuple(np.add(D.bounds, (-300, -300, 300, 300)))
    W = pathlib.Path(c["work"])
    if model == "R":
        g = gpd.read_file(W / "src_dmt.gdb", layer="SW_CAPPEDEND", bbox=bb)
    else:
        g = gpd.read_file(W / "07_export" / "Stormwater_AD_reporting.gdb", layer="StormwaterJunction", bbox=bb, columns=["d_assettype"])
        g = g[g.d_assettype == "End Cap"]
    return np.c_[g.geometry.x, g.geometry.y]


def _net_r(c, D, log):
    X = json.loads((c["dir"] / "extract_raw.json").read_text(encoding="utf-8"))
    num = VB.num
    P = []
    for r in X["pipes"]:
        d = num(r.get("PIPE_DIAME"))
        P.append({"id": f"R{r['OBJECTID_1']}", "xy": r["_xy"], "vx": r["_vx"], "len": r["_len"], "d_m": d / 1000 if d else None,
                  "up": num(r.get("INVERT_LEV")), "dn": num(r.get("INVERT_L_1")), "n": None})
    Nd = []
    for lay, kind, rim_f in (("manholes", "mh", "COVER_LEVE"), ("inlets", "inlet", "COVER_LEVE"), ("outfalls", "outfall", None), ("pumps", "pump", None)):
        for r in X[lay]:
            Nd.append({"id": f"{kind[:2].upper()}{r['OBJECTID_1']}", "xy": r["_xy"], "kind": kind, "rim": num(r.get(rim_f)) if rim_f else None,
                       "inv": num(r.get("INVERT_LEV"))})
    outs = [n["id"] for n in Nd if n["kind"] == "outfall"]            # 范围（外扩 300 m）内所有出水口
    net, N, info = VB.build_from_features(P, Nd, outs, [], None, D, log, caps=_end_caps(c, "R", D))
    info["_phys"] = {n["id"] for n in Nd if n["kind"] in ("mh", "inlet")}
    info["source"] = "src_dmt.gdb 原样（不排除、不去重、不改哨兵值；只有空值按缺省规则补）"
    return net, N, info


def _net_g(c, D, log):
    import re
    X = json.loads((c["dir"] / "extract_gov.json").read_text(encoding="utf-8"))
    num = VB.num
    P = [{"id": f"G{i + 1}", "xy": r["_xy"], "vx": r["_vx"], "len": r["_len"], "d_m": (r["diameter"] / 1000) if r.get("diameter") else None,
          "up": num(r.get("upelev")), "dn": num(r.get("downelev")), "n": num(r.get("manningn"))}
         for i, r in enumerate(x for x in X["lines"] if x.get("assetstate") != 0)]
    from scipy.spatial import cKDTree
    fg = [d["_xy"] for d in X["devices"] if d["group"] == "Outlet"]
    fgt = cKDTree(np.array(fg)) if fg else None
    Nd = []
    for d in X["devices"]:
        g = d["group"]
        if g in ("Manhole Channel", "Catch Basin"):
            Nd.append({"id": d["assetid"] or f"D{len(Nd)}", "xy": d["_xy"], "kind": "mh" if g == "Manhole Channel" else "inlet",
                       "rim": num(d.get("coverelev")), "inv": num(d.get("elevation"))})
        elif g == "Outfall":
            bt = d.get("boundarytype")
            bc = f"FIXED {d['tailwater']:.3f}" if bt == "User Defined Tailwater" and d.get("tailwater") is not None else "FREE"
            Nd.append({"id": d["assetid"], "xy": d["_xy"], "kind": "outfall", "inv": num(d.get("elevation")), "bc": bc,
                       "flap": bool(fgt is not None and fgt.query(d["_xy"])[0] <= 0.6)})
        elif g in ("Outlet", "Connection", "Pump"):
            Nd.append({"id": d["assetid"] or f"D{len(Nd)}", "xy": d["_xy"], "kind": "junction", "inv": None, "rim": None})
    rim_v = {v["assetid"]: num(v.get("rimelev")) for v in X["vaults"]}
    for n in Nd:
        if n["kind"] == "mh" and str(n["id"]).endswith("-CH"):
            n["rim"] = rim_v.get(n["id"][:-3])
        n["id"] = re.sub(r"\s+", "_", str(n["id"]))
    outs = [n["id"] for n in Nd if n["kind"] == "outfall"]
    net, N, info = VB.build_from_features(P, Nd, outs, [], None, D, log, caps=_end_caps(c, "G", D))
    info["_phys"] = {n["id"] for n in Nd if n["kind"] in ("mh", "inlet")}
    info["source"] = "全市治理导出的 Utility Network（05_un），范围内所有出水口所在的连通块；泵站没有流量和泵曲线，不建泵"
    return net, N, info


def _inflows(c, storm, N, log):
    """同一套产流（顾问模型的子汇水区 + SCS CN），每个子汇水区出口的入流挂到本网最近的井"""
    from scipy.spatial import cKDTree
    K = consultant(c)
    if K is None:
        raise SystemExit("没有顾问模型时的产流（复合 CN 汇水区）尚未接入区域流程")
    t, P, exc, H0, sim_h, subs = RB.hydrographs(K, storm["mm"], storm["dur_h"], log=log, tail=storm.get("tail_h", 4.0))
    co = {k: v[:2] for k, v in RB.nodes_table(K).items()}
    keys = list(N)
    tr = cKDTree(np.array([N[k][:2] for k in keys]))
    H, dist = defaultdict(lambda: np.zeros(len(t))), []
    for n, q in H0.items():
        d, i = tr.query(co[n])
        H[keys[i]] += q
        dist.append(d)
    tot = sum(float(q.sum() * 60) for q in H.values())
    log(f"  入流：{len(H0):,} 个顾问模型节点 → {len(H):,} 口井，合计 {tot:,.0f} m³；改挂距离中位 {np.median(dist):.1f} m、最大 {max(dist):.0f} m")
    return t, P, exc, dict(H), sim_h, {"runoff_m3": round(tot), "runoff": c["runoff"]["note"],
                                       "remap_dist_median_m": round(float(np.median(dist)), 1), "remap_dist_max_m": round(float(max(dist)), 1)}


def fix_outfalls(net):
    """SWMM 规定出水口只能接 1 根进水管、不能有出水管（ERROR 141）。多出水口的区域会碰到（RD-A 只有 1 个出水口，没碰到）。
    做法（RB-01 试跑第二版）：画反了的管（从出水口出发）调转方向；接了多根进水管的，第 2 根起每根配一个同位置、同底高、同边界的出水口。
    第一版把出水口改成封死的连接点、再接 1 m 短管，SWMM 算不稳（RB-01 治理后模型出口流量冲到 1,606 m³/s），弃用。"""
    of = {o[0]: o for o in net["OF"]}
    CO, fixed, rev = [], [], 0
    used = defaultdict(int)
    for co in net["CO"]:
        cid, a, b, ln, n, o1, o2 = co[:7]
        if a in of and b not in of:                       # 出水口在上游端：调转
            a, b, o1, o2 = b, a, o2, o1
            rev += 1
        if b in of:
            used[b] += 1
            if used[b] > 1:                               # 第 2 根起：同位置另配一个出水口
                k2 = f"{b}_{used[b]}"
                o = of[b]
                net["OF"].append((k2, o[1], o[2], o[3]))
                net["coords"][k2] = net["coords"][b]
                fixed.append(k2)
                b = k2
        CO.append((cid, a, b, ln, n, o1, o2, *co[7:]))
    net["CO"] = CO
    return {"added_outfalls": fixed, "reversed_conduits": rev}


def run_name(c, storm_tag):
    return f"RG{c['code']}_{storm_tag}"


def cmd_build(c, storm_tag, model):
    storm = next(s for s in c["storms"] if s["tag"] == storm_tag)
    D = domain_of(c)
    name, log = run_name(c, storm_tag), print
    net, N, info = (_net_r if model == "R" else _net_g)(c, D, log)
    info["outfalls_relinked"] = fix_outfalls(net)
    # 二维计算域外、又不是检查井 / 雨水口的节点（拍门、管件、管子拆分点）：那里没有井口，水冒不出来；
    # 不封的话，溢流在域外丢失、水量对不上（RB-01 试跑：治理后模型拍门节点丢 1.7 万 m³，占溢流 3.9 %）
    phys = info.pop("_phys")
    from shapely.geometry import Point
    from shapely.prepared import prep
    pD = prep(D)
    sealed_out = [k for k, j in net["J"].items() if k not in phys and j[3] < VB.SEAL_SUR and not pD.contains(Point(net["coords"][k]))]
    for k in sealed_out:
        net["J"][k][3] = VB.SEAL_SUR
    info["sealed_outside_nonstructure"] = len(sealed_out)
    log(f"  计算域外的非井节点（拍门、管件、拆分点）封死：{len(sealed_out)} 个")
    log(f"  出水口：调转方向的管 {info['outfalls_relinked']['reversed_conduits']} 根，同位置另配出水口 {len(info['outfalls_relinked']['added_outfalls'])} 个")
    t, P, exc, H, sim_h, ri = _inflows(c, storm, N, log)
    info.update(ri)
    RD.write_inp(WORK / f"{name}_{model}.inp", f"{c['name']} 验证 · 模型 {model} · {storm['label']}", net, H, t, sim_h, log=log)
    # 开跑前先用本机 SWMM 打开、启动一次：模型错误在这里就报，不等一二维容器跑起来才失败（RB-01 试跑的教训）
    from swmm.toolkit import solver
    chk = WORK / "c2d" / f"chk_{name}_{model}"
    try:
        solver.swmm_open(str(WORK / f"{name}_{model}.inp"), f"{chk}.rpt", f"{chk}.out"); solver.swmm_start(0); solver.swmm_end(); solver.swmm_close()
    except Exception as e:
        raise SystemExit(f"SWMM 打不开 {name}_{model}.inp：{e}")
    if not (C2D / f"dem_{name}.tif").exists():
        RB.write_dem(name, D)
    RB.write_meta(name, model, D, N, t, exc, {"note": f"{c['name']} 治理前后验证（tools/exp_gov/region.py）模型 {model}"})
    info.update(name=name, model=model, storm=storm_tag, rain_mm=storm["mm"], sim_h=sim_h, n_nodes_2d=len(N), n_junctions=len(net["J"]),
                n_conduits=len(net["CO"]), n_outfalls=len(net["OF"]), domain_km2=round(D.area / 1e6, 3))
    (c["dir"] / f"inputs_{storm_tag}_{model}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(json.dumps({k: v for k, v in info.items() if not isinstance(v, (list, dict))}, ensure_ascii=False))


# ------------------------------------------------------------------ 一二维 + 指标
def metrics(c, name, model, storm_tag):
    """同 v_metrics（热点命中 / 误报 / 积水面积 / 地面存水），计算域和热点按区域取；写 metrics_<雨>_<模型>.json"""
    import geopandas as gpd
    z = np.load(C2D / f"{name}_{model}_2d.npz")
    xy = z["xy"].astype(float); area = z["area"].astype(float); t = z["t_h"]
    hm = z["hmax"].astype(float); fr = z["frames"].astype(np.float32)
    D = domain_of(c)
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640)
    H = H[H.intersects(D.buffer(VM.RADIUS))].set_index("hotspot_id")
    tw = json.loads((C2D / f"twoway_{name}_{model}.json").read_text(encoding="utf-8"))
    fl = VM.node_flooding(C2D / f"{name}_{model}_2way.rpt")
    in_any = np.zeros(len(xy), bool)
    hs = {}
    for h, r in H.iterrows():
        cc = np.hypot(xy[:, 0] - r.geometry.x, xy[:, 1] - r.geometry.y) <= VM.RADIUS
        in_any |= cc
        mx = float(hm[cc].max()) if cc.any() else 0.0
        hs[h] = {"max_m": round(mx, 2), "hit": bool(mx >= VM.DEPTH)}
    hits = sum(v["hit"] for v in hs.values())
    res = {"storm": storm_tag, "model": model, "run": f"{name}_{model}", "hotspots_n": len(hs), "hits": hits,
           "false_alarm_ha": round(float(area[(~in_any) & (hm >= VM.DEPTH)].sum() / 1e4), 2),
           "domain_ge015_ha": round(float(area[hm >= VM.DEPTH].sum() / 1e4), 2), "domain_ge030_ha": round(float(area[hm >= 0.30].sum() / 1e4), 2),
           "peak_surface_m3": round(float((fr * area).sum(1).max())), "end_surface_m3": round(float((fr[-1] * area).sum())),
           "flood_total_1d_m3": round(sum(fl.values())), "twoway": {k: v for k, v in tw.items() if not isinstance(v, (list, dict))}, "hotspots": hs}
    (c["dir"] / f"metrics_{storm_tag}_{model}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"{name}_{model}：命中 {hits}/{len(hs)}，误报 {res['false_alarm_ha']} ha，≥0.15 m {res['domain_ge015_ha']} ha，≥0.30 m {res['domain_ge030_ha']} ha")


def cmd_run(c, par, threads):
    import os
    from rb01_chain import clean_env, log
    host = os.environ.get("SWBRAIN_SWMM_WORK_HOST") or WORK.as_posix()
    (C2D / "logs").mkdir(parents=True, exist_ok=True)
    jobs = []
    for s in c["storms"]:
        name = run_name(c, s["tag"])
        sim = s["dur_h"] + s.get("tail_h", 4.0)
        for m in c["models"]:
            if not (WORK / f"{name}_{m}.inp").exists():
                r = subprocess.run([sys.executable, __file__, "build", c["_path"], "--storm", s["tag"], "--model", m], cwd=ROOT, env=clean_env())
                if r.returncode:
                    raise SystemExit(f"建模失败 {s['tag']} {m}")
            if not (C2D / f"twoway_{name}_{m}.json").exists():
                jobs.append((name, m, s["tag"], sim))
            elif not (c["dir"] / f"metrics_{s['tag']}_{m}.json").exists():
                metrics(c, name, m, s["tag"])
    live = {}
    log(f"一二维 {len(jobs)} 个：{[(j[0], j[1]) for j in jobs]}")
    t0 = time.time()
    while jobs or live:
        while jobs and len(live) < par:
            name, m, tag, sim = jobs.pop(0)
            cn = f"rg_{name}_{m}".lower()
            subprocess.run(["docker", "rm", "-f", cn], capture_output=True)
            subprocess.run(["docker", "run", "--rm", "-d", "--name", cn, "-e", f"OMP_NUM_THREADS={threads}", "-e", "C2D_DEM_RAW=1", "-e", "C2D_SAVE_UV=1",
                            "-v", f"{host}:/work", IMAGE, "sh", "-c",
                            f"python -u /work/c2d/couple2way_v2.py {name} {m} {RES} {sim:g} {XCH} > /work/c2d/logs/{name}_{m}.log 2>&1"], capture_output=True)
            live[cn] = (name, m, tag)
            log(f"  开始 {name} {m}")
        time.sleep(60)
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True)
        up = r.stdout.split()
        if r.returncode or not up:
            continue
        for cn in [x for x in live if x not in up]:
            name, m, tag = live.pop(cn)
            ok = (C2D / f"twoway_{name}_{m}.json").exists()
            log(f"  {'完成' if ok else '! 失败'} {name} {m}（{(time.time() - t0) / 3600:.1f} h）")
            if ok:
                metrics(c, name, m, tag)
    ref = (c.get("consultant") or {}).get("reference_run")
    if ref and (C2D / f"twoway_{ref['name']}_{ref['model']}.json").exists():
        metrics(c, ref["name"], ref["model"], ref["storm"])         # 顾问模型参照（已有算例，不重算）
        p = c["dir"] / f"metrics_{ref['storm']}_{ref['model']}.json"
        p.rename(c["dir"] / f"metrics_{ref['storm']}_C.json")
    log("全部完成")


# ------------------------------------------------------------------ 报告
def cmd_report(c):
    from v_compare import continuity, exch
    lab = {"C": "顾问模型（参照）", "R": "治理前（DMT 原样）", "G": "治理后"}
    rows = []
    ref = (c.get("consultant") or {}).get("reference_run")
    for s in c["storms"]:
        for m in ["C"] + c["models"]:
            p = c["dir"] / f"metrics_{s['tag']}_{m}.json"
            if not p.exists():
                continue
            mt = json.loads(p.read_text(encoding="utf-8"))
            run = mt["run"]
            tw = json.loads((C2D / f"twoway_{run}.json").read_text(encoding="utf-8"))
            ip = c["dir"] / f"inputs_{s['tag']}_{m}.json"
            inp = json.loads(ip.read_text(encoding="utf-8")) if ip.exists() else {}
            cont = continuity(C2D / f"{run}_2way.rpt")
            d12, d21 = exch(C2D / "logs" / f"{run}.log")
            fl = cont.get("Flooding Loss")
            rows.append({"storm": s["tag"], "storm_label": s["label"], "model": m, "model_label": lab[m], "run": run,
                         "runoff_m3": inp.get("runoff_m3") or (round(cont.get("External Inflow", 0) - (d21 or 0))),
                         "outfall_m3": round(cont.get("External Outflow", 0)),
                         "flood_1d_m3": round(fl) if fl else None, "to_2d_m3": round(d12) if d12 is not None else None,
                         "back_to_1d_m3": round(d21) if d21 is not None else None,
                         "lost_m3": round(fl - d12) if fl and d12 is not None else None,
                         "boundary_out_m3": round(tw.get("boundary_out_m3", 0)),
                         "peak_surface_m3": mt["peak_surface_m3"], "end_surface_m3": mt["end_surface_m3"],
                         "drain_share": round(1 - mt["end_surface_m3"] / max(mt["peak_surface_m3"], 1), 3),
                         "ge015_ha": mt["domain_ge015_ha"], "ge030_ha": mt["domain_ge030_ha"], "false_alarm_ha": mt["false_alarm_ha"],
                         "hits": f"{mt['hits']}/{mt['hotspots_n']}", "n_conduits": inp.get("n_conduits"), "n_outfalls": inp.get("n_outfalls")})
    (c["dir"] / "compare.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    ck = json.loads((c["dir"] / "check.json").read_text(encoding="utf-8")) if (c["dir"] / "check.json").exists() else {}
    f = lambda v: "—" if v is None else (f"{v:,}" if isinstance(v, int) else str(v))
    L = [f"# {c['name']} 数据治理区域检查与治理前后验证（自动生成）", "",
         f"> 生成：tools/exp_gov/region.py report；配置 `{pathlib.Path(c['_path']).name}`。{c.get('note', '')}", "",
         "## 1. 区域检查", "", "| 检查 | 结论 | 说明 |", "|---|---|---|"]
    L += [f"| {v['检查']} | {v['结论']} | {v['说明']} |" for v in ck.get("verdict", [])]
    L += ["", f"逐条清单：`out/exp_gov/regions/{c['name']}/{c['name']}_区域检查.xlsx`（地图图层同名 .gpkg）。", "",
          "## 2. 水量平衡（m³）", "", "| 雨 | 模型 | 进管 | 出口排出 | 管网溢流 | 进二维 | 回到一维 | 差（溢流 − 进二维） |", "|---|---|---|---|---|---|---|---|"]
    L += [f"| {r['storm_label']} | {r['model_label']} | {f(r['runoff_m3'])} | {f(r['outfall_m3'])} | {f(r['flood_1d_m3'])} | {f(r['to_2d_m3'])} | "
          f"{f(r['back_to_1d_m3'])} | {f(r['lost_m3'])} |" for r in rows]
    L += ["", "## 3. 指标", "", "| 雨 | 模型 | 管数 | 出水口 | 地面存水峰值 | 结束时 | 雨后排掉 | ≥0.15 m ha | ≥0.30 m ha | 误报 ha | 热点命中 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    L += [f"| {r['storm_label']} | {r['model_label']} | {f(r['n_conduits'])} | {f(r['n_outfalls'])} | {r['peak_surface_m3']:,} | {r['end_surface_m3']:,} | "
          f"{r['drain_share']:.0%} | {r['ge015_ha']} | {r['ge030_ha']} | {r['false_alarm_ha']} | {r['hits']} |" for r in rows]
    if ref:
        L += ["", f"顾问模型参照用已有算例 `{ref['name']}_{ref['model']}`（同一计算域、网格、雨和产流），没有重算。"]
    p = c["dir"] / f"{c['name']}_治理前后验证.md"
    p.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L)); print("写出", p)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["domain", "check", "build", "run", "report"])
    ap.add_argument("cfg")
    ap.add_argument("--storm"); ap.add_argument("--model", choices=["R", "G"])
    ap.add_argument("--par", type=int, default=2); ap.add_argument("--threads", type=int, default=6)
    a = ap.parse_args(argv)
    c = cfg_load(a.cfg); c["_path"] = str(pathlib.Path(a.cfg).resolve())
    if a.cmd == "domain":
        cmd_domain(c)
    elif a.cmd == "check":
        cmd_check(c)
    elif a.cmd == "build":
        cmd_build(c, a.storm, a.model)
    elif a.cmd == "run":
        cmd_run(c, a.par, a.threads)
    else:
        cmd_report(c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
