"""3 融合层：多源合并成一份统一的网络，每个值带来源；被弃用的值留在 alt 里。

  python -m tools.dgov.integrate --config integrate_rda.yaml

规则见配置（RD-A：以 DMT 为底、从 AECOM 取模型参数）。读清洗层的结果（out/dgov/clean/<source>__<extent>.gpkg），
写 out/dgov/int/<extent>.gpkg：node、link、link_optional（雨水口连接管）、pump、pump_curve、catchment，
另有 conflicts（两边都有值但不一致的）、review（要人工核的）两张表；以及 <extent>.report.md。
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from . import STD_VERSION
from .common import CONF, OUT, lineage, load, source, write_json


def _line(g):
    return g.geoms[0] if g.geom_type == "MultiLineString" else g


def _ends(gs):
    a = np.array([(_line(l).coords[0][0], _line(l).coords[0][1], _line(l).coords[-1][0], _line(l).coords[-1][1]) for l in gs])
    return a.reshape(-1, 4)


def match_links(eb, ep, tol):
    """eb、ep：两边管段的端点 (n, 4)。返回 ep 每根对应的 eb 下标（-1 = 没对上）和是否反向。"""
    t0, t1 = cKDTree(eb[:, :2]), cKDTree(eb[:, 2:])
    res, rev = np.full(len(ep), -1), np.zeros(len(ep), bool)
    for i, e in enumerate(ep):
        a = set(t0.query_ball_point(e[:2], tol)) & set(t1.query_ball_point(e[2:], tol))
        b = set(t1.query_ball_point(e[:2], tol)) & set(t0.query_ball_point(e[2:], tol))
        if a:
            res[i] = min(a)
        elif b:
            res[i], rev[i] = min(b), True
    return res, rev


def integrate(cfg_name, log=print):
    import geopandas as gpd
    import pyogrio
    C = load(cfg_name)
    ext = C["extent"]
    B = OUT / "clean" / f"{C['base']}__{ext}.gpkg"
    P = OUT / "clean" / f"{C['params']}__{ext}.gpkg"
    rd = lambda p, lay: gpd.read_file(p, layer=lay)
    BL, PL = rd(B, "link"), rd(P, "link")
    BN, PN = rd(B, "node"), rd(P, "node")
    BL = BL[BL.active == 1].reset_index(drop=True)
    BLg, PLg = BL[BL.link_kind != "force"].reset_index(drop=True), PL[PL.link_kind == "gravity"].reset_index(drop=True)
    cat = gpd.read_file(source(C["params"])["abspath"] / C["catchment_layer"]).set_crs(32640, allow_override=True).geometry.make_valid().union_all()
    eb, ep = _ends(BLg.geometry), _ends(PLg.geometry)
    m = np.full(len(PLg), -1)
    rev = np.zeros(len(PLg), bool)
    for tol in C["match_tol_m"]:
        todo = m < 0
        mm, rr = match_links(eb, ep[todo], tol)
        m[todo], rev[todo] = mm, rr
    log(f"  AECOM 重力管 {len(PLg)} 根，对上 DMT {int((m >= 0).sum())} 根（反向 {int(rev.sum())}）")
    tolv = C["rules"]["value_conflict_tol"]
    links, conflicts, review, optional = [], [], [], []

    def row(base, src_tag, conf, alt=None, flags=""):
        r = {k: base.get(k) for k in ("link_kind", "diameter_mm", "upelev", "downelev", "length_attr", "length_geom", "material", "manning_n",
                                       "un_class", "un_asset_group", "un_asset_type", "status_src", "migration_src", "install_date")}
        r.update(asset_id=base["asset_id"], src=src_tag, conf=json.dumps(conf, ensure_ascii=False), alt=json.dumps(alt or {}, ensure_ascii=False),
                 flags=flags, std_version=STD_VERSION, geometry=base["geometry"])
        return r

    used_b = set()
    for j, i in enumerate(m):
        p = PLg.iloc[j]
        if i < 0:
            links.append(row(p, "AECOM", json.loads(p.conf), flags="DMT无对应"))
            continue
        b = BLg.iloc[i].to_dict()
        used_b.add(i)
        bc, pc = json.loads(b["conf"]), json.loads(p.conf)
        out, conf, alt = dict(b), {}, {}
        for f, tol in (("diameter_mm", tolv["diameter_mm"]), ("upelev", tolv["elev_m"]), ("downelev", tolv["elev_m"]), ("length_attr", 0.5)):
            pv = p[("downelev" if f == "upelev" else "upelev") if (rev[j] and f in ("upelev", "downelev")) else f]
            bv = b.get(f)
            if bv is None or (isinstance(bv, float) and np.isnan(bv)):
                out[f], conf[f] = pv, pc.get(f, "C")
                alt[f] = {"DMT": None}
            else:
                conf[f] = bc.get(f, "B")
                if pv is not None and not (isinstance(pv, float) and np.isnan(pv)) and abs(float(pv) - float(bv)) > tol:
                    alt[f] = {"AECOM": float(pv)}
                    conflicts.append({"asset_id": b["asset_id"], "field": f, "DMT": float(bv), "AECOM": float(pv), "aecom_id": p.asset_id})
        out["manning_n"], conf["manning_n"] = p.manning_n, pc.get("manning_n", "C")
        out["material"] = b.get("material")
        conf["material"] = bc.get("material", "B")
        links.append(row(out, "DMT+AECOM", conf, alt, flags=("AECOM 方向相反" if rev[j] else "")))
    # DMT 没对上的
    Nk = cKDTree(np.c_[BN.geometry.x, BN.geometry.y])
    kinds = BN.node_kind.values
    inc_ends = _ends([l["geometry"] for l in links])
    te = cKDTree(np.r_[inc_ends[:, :2], inc_ends[:, 2:]])
    for i in range(len(BLg)):
        if i in used_b:
            continue
        b = BLg.iloc[i]
        if not b.geometry.intersects(cat):
            continue                                            # 范围外：不收
        e = eb[i]
        k = []
        for xy in (e[:2], e[2:]):
            d, n = Nk.query(xy)
            k.append(kinds[n] if d <= C["node_tol_m"] else "none")
        if {"inlet", "catchbasin"} & set(k):
            r = row(b.to_dict(), "DMT", json.loads(b.conf), flags="雨水口连接管")
            optional.append(r)
            continue
        conn = [te.query(xy)[0] <= C["node_tol_m"] for xy in (e[:2], e[2:])]
        rec = {"asset_id": b.asset_id, "diameter_mm": b.diameter_mm, "length_geom": b.length_geom, "end_kinds": "/".join(k),
               "connected_ends": int(sum(conn)), "geometry": b.geometry}
        if any(conn):
            links.append(row(b.to_dict(), "DMT", json.loads(b.conf), flags="补收主管（待核）"))
            rec["decision"] = "补进（至少一端接上管网）"
        else:
            rec["decision"] = "不补（两端都没接上融合后的管网）"
        review.append(rec)
    # 压力管（AECOM）
    for _, p in PL[PL.link_kind == "force"].iterrows():
        links.append(row(p, "AECOM", json.loads(p.conf), flags="压力管"))
    L = gpd.GeoDataFrame(links, geometry="geometry", crs=32640)
    L["link_id"] = L["asset_id"]
    # 节点：管端吸附到 DMT 节点，没有就 AECOM 节点，再没有就新建
    E = _ends(L.geometry)
    Pk = cKDTree(np.c_[PN.geometry.x, PN.geometry.y])
    nodes, key = {}, {}

    def node_at(xy):
        kk = (round(xy[0], 1), round(xy[1], 1))
        if kk in key:
            return key[kk]
        d, n = Nk.query(xy)
        dp, npi = Pk.query(xy)
        if d <= C["node_tol_m"]:
            b = BN.iloc[n]
            nid = b.asset_id
            if nid not in nodes:
                bc = json.loads(b.conf)
                rec = {"node_id": nid, "node_kind": b.node_kind, "rim_elev": b.rim_elev, "invert_elev": b.invert_elev, "src": "DMT", "conf": bc,
                       "un_class": b.un_class, "un_asset_group": b.un_asset_group, "un_asset_type": b.un_asset_type, "flap": b.get("flap"),
                       "boundary_type": None, "tailwater": None, "geometry": b.geometry}
                if dp <= C["node_tol_m"]:
                    p = PN.iloc[npi]
                    rec["aecom_id"] = p.asset_id
                    for f in ("rim_elev", "invert_elev"):
                        if pd.isna(rec[f]) and not pd.isna(p[f]):
                            rec[f] = p[f]
                            rec["conf"][f] = "C"
                            rec["src"] = "DMT+AECOM"
                    if p.node_kind in ("outfall", "wetwell"):
                        rec.update(node_kind=p.node_kind, boundary_type=p.boundary_type, tailwater=p.tailwater,
                                   flap=p.flap if pd.notna(p.flap) else rec["flap"], un_class=p.un_class, un_asset_group=p.un_asset_group,
                                   un_asset_type=p.un_asset_type, src="DMT+AECOM")
                nodes[nid] = rec
        elif dp <= max(C["match_tol_m"]):                  # DMT 没有节点时，AECOM 节点按管段匹配的最大容差吸附（AECOM 有的节点离管端 0.5–2 m）
            p = PN.iloc[npi]
            nid = p.asset_id
            if nid not in nodes:
                nodes[nid] = {"node_id": nid, "node_kind": p.node_kind, "rim_elev": p.rim_elev, "invert_elev": p.invert_elev, "src": "AECOM",
                              "conf": json.loads(p.conf), "un_class": p.un_class, "un_asset_group": p.un_asset_group, "un_asset_type": p.un_asset_type,
                              "flap": p.flap, "boundary_type": p.boundary_type, "tailwater": p.tailwater, "aecom_id": p.asset_id, "geometry": p.geometry}
        else:
            from shapely.geometry import Point
            nid = f"NEW:{kk[0]:.1f}_{kk[1]:.1f}"
            nodes[nid] = {"node_id": nid, "node_kind": "junction", "rim_elev": None, "invert_elev": None, "src": "新建（管端无节点）",
                          "conf": {"rim_elev": "missing", "invert_elev": "missing"}, "un_class": "StormwaterDevice", "un_asset_group": "Connection",
                          "un_asset_type": "Pipe Connection", "geometry": Point(xy)}
        key[kk] = nid
        return nid

    L["from_node"] = [node_at(e[:2]) for e in E]
    L["to_node"] = [node_at(e[2:]) for e in E]
    N = pd.DataFrame(nodes.values())
    N["conf"] = N["conf"].apply(lambda c: json.dumps(c, ensure_ascii=False))
    N = gpd.GeoDataFrame(N, geometry="geometry", crs=32640)
    # 泵、泵曲线、子汇水区：AECOM
    PP, PC, PCat = rd(P, "pump"), pyogrio.read_dataframe(P, layer="pump_curve"), rd(P, "catchment")
    aec2node = {r.get("aecom_id"): r["node_id"] for r in nodes.values() if r.get("aecom_id")}
    lab2aec = dict(zip(PN.src_id, PN.asset_id))
    PCat["out_node"] = [aec2node.get(lab2aec.get(str(x))) for x in PCat.out_node_src]
    miss = PCat.out_node.isna()
    if miss.any():                                          # 兜底：AECOM 出口节点位置 2 m 内的融合节点
        nk = cKDTree(np.c_[N.geometry.x, N.geometry.y])
        pxy = {r.src_id: (r.geometry.x, r.geometry.y) for r in PN.itertuples()}
        for i in PCat.index[miss]:
            xy = pxy.get(str(PCat.at[i, "out_node_src"]))
            if xy is not None:
                d, j = nk.query(xy)
                if d <= max(C["match_tol_m"]):
                    PCat.at[i, "out_node"] = N.node_id.iloc[j]
    PP["wetwell_node"] = [aec2node.get(lab2aec.get(str(x))) if x else None for x in PP.wetwell_src]
    dmt_ps = rd(B, "pump")
    if len(dmt_ps) and len(PP):
        d = dmt_ps.geometry.distance(PP.geometry.union_all().centroid)
        PP["dmt_station"] = dmt_ps.asset_id[d.idxmin()] if d.min() <= 50 else None
        PP["dmt_station_comments"] = dmt_ps.comments_src[d.idxmin()] if d.min() <= 50 else None
    dst = OUT / "int" / f"{ext}.gpkg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    N.to_file(dst, layer="node", driver="GPKG")
    L.to_file(dst, layer="link", driver="GPKG")
    if optional:
        gpd.GeoDataFrame(optional, geometry="geometry", crs=32640).to_file(dst, layer="link_optional", driver="GPKG")
    PP.to_file(dst, layer="pump", driver="GPKG")
    pyogrio.write_dataframe(PC, dst, layer="pump_curve", driver="GPKG")
    PCat.to_file(dst, layer="catchment", driver="GPKG")
    if conflicts:
        pyogrio.write_dataframe(pd.DataFrame(conflicts), dst, layer="conflicts", driver="GPKG")
    if review:
        gpd.GeoDataFrame(review, geometry="geometry", crs=32640).to_file(dst, layer="review", driver="GPKG")
    S = {"links": len(L), "by_src": L.src.value_counts().to_dict(), "flags": L["flags"].value_counts().to_dict(), "optional_laterals": len(optional),
         "nodes": len(N), "nodes_by_src": N.src.value_counts().to_dict(), "new_nodes": int(N.node_id.str.startswith("NEW:").sum()),
         "conflicts": pd.DataFrame(conflicts).groupby("field").size().to_dict() if conflicts else {},
         "review": pd.DataFrame(review).decision.value_counts().to_dict() if review else {},
         "catchments_without_node": int(PCat.out_node.isna().sum()), "pumps": len(PP)}
    write_json(dst.with_suffix(".log.json"), S)
    lineage("integrate", [B, P, CONF / cfg_name], [dst, dst.with_suffix(".log.json")], {"config": cfg_name})
    log(json.dumps(S, ensure_ascii=False, indent=1))
    log(f"→ {dst}")
    return dst, S


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.integrate")
    ap.add_argument("--config", required=True)
    ap.add_argument("--no-qa", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    dst, _ = integrate(a.config)
    if not a.no_qa:
        qa(load(a.config)["extent"])



def qa(ext, log=print):
    """融合后的管网交给 dataqc 查一遍（编号用融合后的节点号，所以 C8/C9 查的是融合结果自身的一致性）。"""
    import subprocess
    import geopandas as gpd
    from .clean import DATUM, NODE_TABLE, _s
    from .common import ROOT
    src = OUT / "int" / f"{ext}.gpkg"
    qdir = OUT / "int" / f"{ext}_dataqc"
    qdir.mkdir(parents=True, exist_ok=True)
    q = qdir / "input.gpkg"
    if q.exists():
        q.unlink()
    L = gpd.read_file(src, layer="link")
    for name, sel in (("pipeline", L.link_kind != "force"), ("pressure_pipe", L.link_kind == "force")):
        g = L[sel]
        gpd.GeoDataFrame({"unitid": g.asset_id, "asset_before": _s(g.from_node), "asset_after": _s(g.to_node), "pipe_diameter": g.diameter_mm,
                          "invert_level_upstream": g.upelev + DATUM, "invert_level_downstream": g.downelev + DATUM, "pipe_length": g.length_attr,
                          "pipe_material": _s(g.material), "status": "Active"}, geometry=g.geometry, crs=g.crs).to_file(q, layer=name, driver="GPKG")
    N = gpd.read_file(src, layer="node")
    for k, g in N.groupby("node_kind"):
        gpd.GeoDataFrame({"unitid": g.node_id, "invert_level": g.invert_elev + DATUM, "cover_level": g.rim_elev + DATUM},
                         geometry=g.geometry, crs=g.crs).to_file(q, layer=NODE_TABLE.get(k, k), driver="GPKG")
    P = gpd.read_file(src, layer="pump")
    gpd.GeoDataFrame({"unitid": P.asset_id}, geometry=P.geometry, crs=P.crs).to_file(q, layer="pump", driver="GPKG")
    r = subprocess.run([sys.executable, "-m", "tools.dataqc", "--pipes", str(q), "--out", str(qdir), "--gpkg", "--title", f"{ext} 融合后管网质检"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        log(r.stdout[-1500:], r.stderr[-1500:])
        raise SystemExit("dataqc 失败")
    c = json.loads((qdir / "result.json").read_text(encoding="utf-8"))["network"]["checks"]
    hit = {k: (v["count"], v.get("total")) for k, v in c.items() if v.get("count")}
    write_json(qdir / "summary.json", hit)
    log("  融合后 dataqc：" + "，".join(f"{k} {a}/{b}" for k, (a, b) in hit.items()))
    return hit


if __name__ == "__main__":
    main()
