"""方案数据包：L1 资产 + 某个 L2 / 计划措施方案（标准 v0.2 第 3 章「模型态」、第 4 章）。

  python -m tools.dgov.l2_package --extent rda --l1 <L1 资产 gpkg> --scheme TIP

客户还没确认各措施的级别和阶段，所以先「一个方案一个包」，客户答复后再拼成 L2 / L2+planned 态（2026-10-21 自定，已告知用户）。
来源：out/dgov/l2/<extent>_l2_raw.gpkg（tools/dgov/l2_extract.py 从 AECOM 原始模型库抽出）。
换算规则（AECOM SewerGEMS → 本标准，记进 process_log，规则号 L2-xx）：
  L2-01 连接关系按模型库的拓扑（管的起止节点编号），不按几何猜
  L2-02 接现有设施的管：现有元素坐标 2 m 内最近的 L1 节点；2–10 m 的也接上但标问题；10 m 外的进问题登记、管端保持原位
  L2-03 格室：每个塘（土工格室）建一个蓄水节点，带高程—面积曲线和孔隙率；「边界元素是塘」的出水口、塘内的出口构筑物都并到这个蓄水节点
  L2-04 泵：和 L1 泵站同一种表达——在泵的位置建一个节点，泵的吸水端 = 吸水管的上游节点，出水端 = 泵位节点；能力取设计流量和扬程（模型里没有完整曲线）
  L2-05 新建节点缺井盖：取 DTM 地面（置信度 D）
  L2-06 出水口边界同 P-05：只接压力管的为泵站出口（自由出流），其余按 WA-726 +1.3 m 带拍门
  L2-07 管端几何移到所接节点上（同 P-10）
  L2-08 方案里接不到本片区 L1 管网的部分（例如格室接在相邻片区的管上，只因范围外扩 200 m 才落进来）不进本片区的包，逐个列出
所有 L2 对象：level = L2，scheme = 方案名，stage = 计划中（待客户确认），conf = C（顾问模型），推断值另标 D。
产物：out/dgov/pkg/<extent>/L1+<方案>.gpkg（node、link、pump、pump_curve、storage、storage_curve、catchment、process_log）
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from .common import OUT, lineage, source

SCHEMES = {"TIP": ["TIP"], "PROP_PUMP_UP": ["PROP_PUMP UP"], "ADD_TANKS": ["ADD TANKS"], "PS": ["PS"]}
STAGE = "计划中（待客户确认）"
WA726 = 1.3


def build(ext, l1, scheme, log=print):
    import geopandas as gpd
    import pyogrio
    import rasterio
    from scipy.spatial import cKDTree
    from shapely.geometry import LineString, Point
    raw = OUT / "l2" / f"{ext}_l2_raw.gpkg"
    alts = SCHEMES[scheme]
    P = gpd.read_file(raw, layer="point")
    Ln = gpd.read_file(raw, layer="line")
    Po = gpd.read_file(raw, layer="polygon") if "polygon" in [x[0] for x in pyogrio.list_layers(raw)] else gpd.GeoDataFrame()
    P, Ln = P[P.alt.isin(alts)], Ln[Ln.alt.isin(alts)]
    Po = Po[Po.alt.isin(alts)] if len(Po) else Po
    N = gpd.read_file(l1, layer="node")
    L = gpd.read_file(l1, layer="link")
    PU = gpd.read_file(l1, layer="pump")
    PC = pyogrio.read_dataframe(l1, layer="pump_curve")
    C = gpd.read_file(l1, layer="catchment")
    PL = pyogrio.read_dataframe(l1, layer="process_log")
    for df in (N, L, PU, C):
        df["level"], df["scheme"], df["stage"] = "L1", "", "现状"
    plog = []

    def lg(rule, obj, field, value, conf, method):
        plog.append({"rule": rule, "object": obj, "field": field, "value": value, "conf": conf, "method": method})
    tag = lambda r: f"L2:{scheme}:{r['label']}#{r['element_id']}"
    nt = cKDTree(np.c_[N.geometry.x, N.geometry.y])
    # L2-03 塘 → 蓄水节点；塘内出口构筑物、边界元素是塘的出水口 → 并到蓄水节点
    st_rows, curve_rows, alias = [], [], {}
    for r in (Po[Po["class"] == "Pond"].to_dict("records") if len(Po) else []):
        nid = tag(r)
        pt = r["geometry"].representative_point()
        cv = json.loads(r["storage_curve"]) if isinstance(r.get("storage_curve"), str) else []
        st_rows.append({"node_id": nid, "geometry": pt, "label": r["label"], "volume_m3": r.get("volume_m3"), "bottom_m": r.get("bottom_m"),
                        "top_m": r.get("top_m"), "max_area_m2": r.get("max_area_m2"), "void_ratio": r.get("void_ratio"), "footprint_wkt": r["geometry"].wkt})
        for i, (z, a, v) in enumerate(cv):
            curve_rows.append({"node_id": nid, "seq": i, "elev_m": z, "area_m2": a, "void_ratio": v})
        alias[r["element_id"]] = nid
    stG = gpd.GeoDataFrame(st_rows, geometry="geometry", crs=32640) if st_rows else None
    for r in P.to_dict("records"):
        if r["class"] == "Outfall" and pd.notna(r.get("boundary_element_id")) and int(r["boundary_element_id"]) in alias:
            alias[r["element_id"]] = alias[int(r["boundary_element_id"])]
            lg("L2-03", tag(r), "merged_into", alias[r["element_id"]], "C", f"出水口的边界元素是塘 {r.get('boundary_element')}：并到蓄水节点")
        elif r["class"] == "PondOutletStructure" and stG is not None:
            inside = [s for s in st_rows if Point(r["geometry"].x, r["geometry"].y).within(__import__("shapely").from_wkt(s["footprint_wkt"]))]
            if inside:
                alias[r["element_id"]] = inside[0]["node_id"]
                lg("L2-03", tag(r), "merged_into", inside[0]["node_id"], "C", "塘内的出口构筑物：并到蓄水节点")
    # 新节点（不是塘、没被合并的点）
    new_nodes, new_pumps = [], []
    kind_of = {"Outfall": ("outfall", "Outlet"), "Manhole": ("manhole", "Sewer Storm Vault"), "PressureJunction": ("junction", "Connection"),
               "WetWell": ("wetwell", "Sewer Storm Vault"), "StandardPump": ("junction", "Connection"), "PondOutletStructure": ("junction", "Connection")}
    for r in P.to_dict("records"):
        if r["element_id"] in alias:
            continue
        nid = tag(r)
        alias[r["element_id"]] = nid
        k, ag = kind_of.get(r["class"], ("junction", "Connection"))
        new_nodes.append({"node_id": nid, "node_kind": k, "invert_elev": r.get("invert_m"), "rim_elev": None, "src": "AECOM", "un_asset_group": ag,
                          "un_asset_type": "", "conf": json.dumps({"invert_elev": "C"}), "aecom_id": r["label"], "geometry": r["geometry"]})
        if r["class"] == "StandardPump":
            new_pumps.append(r)
    for s_ in st_rows:
        new_nodes.append({"node_id": s_["node_id"], "node_kind": "storage", "invert_elev": s_["bottom_m"], "rim_elev": s_["top_m"], "src": "AECOM",
                          "un_asset_group": "BMP Point", "un_asset_type": "BMP Point", "conf": json.dumps({"invert_elev": "C", "rim_elev": "C"}),
                          "aecom_id": s_["label"], "geometry": s_["geometry"]})
    NN = gpd.GeoDataFrame(new_nodes, geometry="geometry", crs=32640) if new_nodes else gpd.GeoDataFrame(columns=["node_id", "geometry"], crs=32640)
    # L2-02 方案外的现有元素 → L1 节点
    def map_existing(eid, label, xy):
        if eid in alias:
            return alias[eid], None
        if not isinstance(xy, str):
            return None, f"现有元素 {label} 没有坐标"
        x, y = map(float, xy.split(","))
        d, i = nt.query([x, y])
        nid = N.node_id.iloc[i]
        if d <= 2:
            lg("L2-02", label, "mapped_to", nid, "C", f"现有元素 {label} → L1 节点 {nid}（{d:.2f} m）")
            return nid, None
        if d <= 10:
            lg("L2-02", label, "mapped_to", nid, "D", f"现有元素 {label} → L1 节点 {nid}（{d:.2f} m，偏远，已挂问题）")
            return nid, f"现有元素 {label} 离最近的 L1 节点 {d:.1f} m（2–10 m）"
        return None, f"现有元素 {label} 10 m 内没有 L1 节点（最近 {d:.0f} m）"
    links, issues = [], []
    pos = {r["node_id"]: r["geometry"] for r in new_nodes}
    pos.update(dict(zip(N.node_id, N.geometry)))
    for r in Ln.to_dict("records"):
        a, ea = map_existing(int(r["start_id"]), r.get("start_label"), r.get("start_xy"))
        b, eb = map_existing(int(r["stop_id"]), r.get("stop_label"), r.get("stop_xy"))
        lid = tag(r)
        for e in (ea, eb):
            if e:
                issues.append({"object": lid, "issue": e})
        c = list(r["geometry"].coords)
        for end, nid in ((0, a), (-1, b)):
            if nid and nid in pos:
                q = pos[nid]
                dd = Point(c[end]).distance(q)
                if dd >= 0.001:
                    lg("L2-07", lid, "geometry", round(dd, 3), "—", f"{'起点' if end == 0 else '终点'}移到节点 {nid}，移动 {dd:.2f} m")
                c[end] = (q.x, q.y)
        force = r["class"] == "PressurePipe"
        links.append({"asset_id": lid, "link_id": lid, "from_node": a, "to_node": b, "link_kind": "force" if force else "gravity",
                      "diameter_mm": r.get("diameter_mm"), "upelev": r.get("up_invert_m"), "downelev": r.get("dn_invert_m"),
                      "manning_n": r.get("manning_n"), "un_class": "StormwaterLine", "un_asset_group": "Pipe",
                      "un_asset_type": "Force Pipe" if force else "Gravity Pipe", "src": "AECOM", "material": None,
                      "conf": json.dumps({k: "C" for k in ("diameter_mm", "upelev", "downelev", "manning_n")}), "geometry": LineString(c)})
    LL = gpd.GeoDataFrame(links, geometry="geometry", crs=32640)
    # L2-08 只留接到 L1 管网的部分：方案内部按连通分块，块里没有任何 L1 节点的整块去掉
    l1ids = set(N.node_id)
    par = {}

    def root(x):
        while par.setdefault(x, x) != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    ok = lambda x: isinstance(x, str) and x != ""                   # 没映射上的端点是 NaN（NaN 不等于自己，会让 root 死循环）
    for a_, b_ in zip(LL.from_node, LL.to_node):
        if ok(a_) and ok(b_):
            par[root(a_)] = root(b_)
    for n in NN.node_id if len(NN) else []:
        root(n)
    good = {root(x) for x in par if x in l1ids}
    drop_nodes = {n for n in (NN.node_id if len(NN) else []) if root(n) not in good}
    drop_links = LL[[not ((ok(a_) and root(a_) in good) or (ok(b_) and root(b_) in good)) for a_, b_ in zip(LL.from_node, LL.to_node)]].asset_id.tolist()
    excluded = {"nodes": sorted(drop_nodes), "links": drop_links,
                "storage_m3": round(float(np.nansum([s_["volume_m3"] for s_ in st_rows if s_["node_id"] in drop_nodes])), 1)}
    for x in sorted(drop_nodes) + drop_links:
        lg("L2-08", x, "excluded", None, "—", "没接到本片区 L1 管网（接在相邻片区的管上），不进本片区的包")
    issues = [it for it in issues if it["object"] not in drop_links]
    LL = LL[~LL.asset_id.isin(drop_links)].reset_index(drop=True)
    NN = NN[~NN.node_id.isin(drop_nodes)].reset_index(drop=True)
    st_rows = [s_ for s_ in st_rows if s_["node_id"] not in drop_nodes]
    curve_rows = [c_ for c_ in curve_rows if c_["node_id"] not in drop_nodes]
    stG = gpd.GeoDataFrame(st_rows, geometry="geometry", crs=32640) if st_rows else None
    new_pumps = [r for r in new_pumps if alias[r["element_id"]] not in drop_nodes]
    # L2-04 泵
    pumps = []
    for r in new_pumps:
        nid = alias[r["element_id"]]
        suction = LL[LL.to_node == nid]
        inlet = suction.from_node.iloc[0] if len(suction) else None
        pumps.append({"asset_id": tag(r), "src_id": r["label"], "src_dataset": r.get("src_db"), "conf": "C", "un_class": "StormwaterDevice",
                      "un_asset_group": "Pump", "un_asset_type": "Pump", "pump_kind": "L2", "on_elev": r.get("on_elev_m"), "off_elev": r.get("off_elev_m"),
                      "curve_id": None, "flow_lps": r.get("design_q_lps"), "design_head_m": r.get("design_head_m"),
                      "inlet_node": inlet, "outlet_node": nid, "geometry": r["geometry"]})
        lg("L2-04", tag(r), "flow_lps", r.get("design_q_lps"), "C", f"设计流量 {r.get('design_q_lps')} L/s、扬程 {r.get('design_head_m')} m（模型里没有完整曲线）")
        if inlet is None:
            issues.append({"object": tag(r), "issue": "泵没有吸水管"})
    PP = gpd.GeoDataFrame(pumps, geometry="geometry", crs=32640) if pumps else None
    # L2-05 新节点缺井盖：DTM
    dtm = next(p for p in source("dtm_auh")["abspath"].glob("*.TIF") if not p.name.startswith("._"))
    miss = NN.index[NN.rim_elev.isna()] if len(NN) else []
    if len(miss):
        with rasterio.open(dtm) as ds:
            for i, v in zip(miss, ds.sample(list(zip(NN.geometry.x[miss], NN.geometry.y[miss])))):
                if abs(v[0]) < 1e30:
                    NN.at[i, "rim_elev"] = float(v[0])
                    lg("L2-05", NN.at[i, "node_id"], "rim_elev", float(v[0]), "D", "DTM 地面")
    # L2-06 出水口边界
    for i in NN.index[NN.node_kind == "outfall"]:
        nid = NN.at[i, "node_id"]
        kinds = set(LL[(LL.to_node == nid) | (LL.from_node == nid)].link_kind)
        if kinds and kinds <= {"force"}:
            NN.loc[i, ["outfall_kind", "boundary_type", "flap"]] = ["pump_discharge", "FREE", "no"]
        else:
            NN.loc[i, ["outfall_kind", "boundary_type", "flap", "tailwater"]] = ["gravity_sea", "FIXED", "yes", WA726]
        lg("L2-06", nid, "boundary_type", None, "C", NN.at[i, "outfall_kind"])
    for df in (NN, LL) + ((PP,) if PP is not None else ()):
        df["level"], df["scheme"], df["stage"] = "L2", scheme, STAGE
    # 合并后数值列可能变成 object（L2 行里有 None）：写出前统一转成数，否则下游当文本读
    NUM = ("rim_elev", "invert_elev", "tailwater", "diameter_mm", "upelev", "downelev", "manning_n", "flow_lps", "on_elev", "off_elev", "design_head_m")
    def num(df):
        for c in NUM:
            if c in df:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df
    NN, LL = num(NN), num(LL)
    PP = num(PP) if PP is not None else None
    out = OUT / "pkg" / ext / f"L1+{scheme}.gpkg"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    num(pd.concat([N, NN], ignore_index=True)).pipe(gpd.GeoDataFrame, crs=32640).to_file(out, layer="node", driver="GPKG")
    num(pd.concat([L, LL], ignore_index=True)).pipe(gpd.GeoDataFrame, crs=32640).to_file(out, layer="link", driver="GPKG")
    num(pd.concat([PU, PP], ignore_index=True) if PP is not None else PU).pipe(gpd.GeoDataFrame, crs=32640).to_file(out, layer="pump", driver="GPKG")
    pyogrio.write_dataframe(PC, out, layer="pump_curve", driver="GPKG")
    C.to_file(out, layer="catchment", driver="GPKG")
    if stG is not None:
        stG.drop(columns="footprint_wkt").to_file(out, layer="storage", driver="GPKG")
        pyogrio.write_dataframe(pd.DataFrame(curve_rows), out, layer="storage_curve", driver="GPKG")
    pyogrio.write_dataframe(pd.concat([PL, pd.DataFrame(plog)], ignore_index=True).astype({"value": str}), out, layer="process_log", driver="GPKG")
    pyogrio.write_dataframe(pd.DataFrame(issues or [{"object": "", "issue": ""}]), out, layer="l2_issues", driver="GPKG")
    S = {"nodes_added": len(NN), "links_added": len(LL), "pumps_added": len(pumps), "storage_nodes": len(st_rows),
         "storage_m3": round(float(np.nansum([s_["volume_m3"] for s_ in st_rows])), 0), "issues": len(issues),
         "excluded_neighbour": {"nodes": len(excluded["nodes"]), "links": len(excluded["links"]), "storage_m3": excluded["storage_m3"]}}
    lineage("l2_package", [l1, raw], [out], {"extent": ext, "scheme": scheme, "alternatives": alts}, notes=json.dumps(S, ensure_ascii=False))
    log(f"  {scheme}: {json.dumps(S, ensure_ascii=False)}")
    for it in issues:
        log(f"    问题：{it['object']} — {it['issue']}")
    return out, S


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.l2_package")
    ap.add_argument("--extent", required=True)
    ap.add_argument("--l1", required=True)
    ap.add_argument("--scheme", nargs="+", default=list(SCHEMES))
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    for s in a.scheme:
        build(a.extent, a.l1, s)


if __name__ == "__main__":
    main()
