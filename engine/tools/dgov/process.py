"""4 加工层：在融合（含冲突裁决）结果上补缺、换算、定边界，产出完整的模型输入。补的每个值都标置信度 D / E，并写明方法。

  python -m tools.dgov.process --extent rda

RD-A 的加工规则（P-xx 编号，逐条记进 process_log）：
  P-01 管段缺糙率：取对上的 AECOM 管里同材质的糙率中位数；材质对不上取全部中位数。置信度 D
  P-02 管段缺管底：取所接节点的井底。置信度 D
  P-03 节点缺井底（含管端新建的节点）：取所接管段该端管底的最小值。置信度 D
  P-04 节点缺井盖：取 DTM 地面（不扣整体偏差：问题 D 类整体偏差待客户确认）。置信度 D
  P-05 出水口边界，按出水口类型分别定（标准 v0.2 4.3 `outfall_kind`，2026-10-21 修订，依据 AECOM RD-A 模型的设置）：
        重力出水口（接重力管）→ WA-726-1 §4.8 固定尾水 +1.3 m NADD、带拍门，置信度 B；
        泵站压力管出口（只接压力管）→ 自由出流、不带拍门，置信度 C（AECOM 模型同此设置）。原来一律按 +1.3 m（F-21、F-38）
  P-06 子汇水区：AECOM 的成对地块（12SU 透水 + 12SUI 不透水）每个地块只算一次面积，用 out/rda/catchments_composite.gpkg
        （tools/rda_catchments.py，用户 2026-10-20 同意；不透水比例 f 取 Makani，Makani 是这一步额外用到的来源，在血缘里登记）。
        径流按 f·A 用 CN 95、(1 − f)·A 用 CN透水 分开算；合成 CN 只作参考。出口节点换成融合后的节点。
  P-07 泵：AECOM 的 3 台泵和泵曲线原样（置信度 C）；出水端 = 泵位置的节点；吸水端 = 接进泵位置那根吸水管的上游节点（湿井）。
        2026-10-21 修正：原来取融合表的 wetwell_node，而 AECOM 的湿井和出水口同名（「RD-A」，F-05），被认成了出水口 OFL_193（F-37）
  P-09 压力管网里的节点（泵位、压力管汇合点）缺井底：从 AECOM Base 情景的 SWMM 导出按坐标取（2 m 内）。置信度 C
        （资产层关口 G-02 拦下后加的规则，2026-10-21）
  P-08 暂定处理（等用户 / 客户裁决，见问题登记簿）：A 类补收管、B 类 AECOM 独有管（含 DMT 标废弃的 52 根）、
        C 类待核管底、D 类节点高程异常——都**保留当前值、不删不改**，只在资产里带上问题编号。裁决后出 v1.1。
  P-11 完全重复的记录（编号、两端节点、几何都相同）只留一条：资产库里同一根管登记了两次，模型里会把过流能力算成两倍（标准 Q-LC-41）
  P-10 管端几何吸附到所接节点：融合时按编号挂接（资产库 0.5 m、AECOM 2 m 容差），节点位置变了、管的几何没动。
        Utility Network 按几何判连通，差 1 mm 就是断开（F-35，规则 Q-LC-43）。把管的首末点移到节点上，逐根记移动距离
产物：out/dgov/proc/<extent>.gpkg（node、link、pump、pump_curve、catchment、process_log）。
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from .common import OUT, ROOT, lineage, source

WA726_TAILWATER = 1.3
COMPOSITE = ROOT / "out" / "rda" / "catchments_composite.gpkg"


def process(ext, log=print):
    import geopandas as gpd
    import pyogrio
    import rasterio
    src = OUT / "int" / f"{ext}.gpkg"
    L, N = gpd.read_file(src, layer="link"), gpd.read_file(src, layer="node")
    PLOG = []

    def plog(rule, obj, field, value, conf, method):
        PLOG.append({"rule": rule, "object": obj, "field": field, "value": None if value is None or (isinstance(value, float) and np.isnan(value)) else float(value),
                     "conf": conf, "method": method})

    def setc(df, i, f, v, c):
        cf = json.loads(df.at[i, "conf"]) if isinstance(df.at[i, "conf"], str) else {}
        cf[f] = c
        df.at[i, "conf"] = json.dumps(cf, ensure_ascii=False)
        df.at[i, f] = v
    # 问题编号挂回对象（P-08）
    I = pd.read_csv(OUT / "issues" / f"{ext}_issues.csv", encoding="utf-8-sig")
    ob2iss = {}
    for r in I.itertuples():
        for o in str(r.objects).split(";"):
            ob2iss.setdefault(o.split("→")[0], []).append(r.issue_id)
    # P-01 糙率
    m = L[(L.src == "DMT+AECOM") & L.manning_n.notna()]
    by_mat = m.groupby("material").manning_n.median().to_dict()
    allmed = float(m.manning_n.median())
    for i in L.index[L.manning_n.isna() & (L.link_kind != "force")]:
        v = by_mat.get(L.at[i, "material"], allmed)
        setc(L, i, "manning_n", v, "D")
        plog("P-01", L.at[i, "asset_id"], "manning_n", v, "D", f"对上的 AECOM 管同材质（{L.at[i, 'material']}）糙率中位数" if L.at[i, "material"] in by_mat else "对上的 AECOM 管糙率中位数")
    # 节点表索引
    Nx = N.set_index("node_id")
    # P-02 管底
    for f, nf in (("upelev", "from_node"), ("downelev", "to_node")):
        for i in L.index[L[f].isna() & (L.link_kind != "force")]:
            v = Nx.at[L.at[i, nf], "invert_elev"]
            if pd.notna(v):
                setc(L, i, f, float(v), "D")
                plog("P-02", L.at[i, "asset_id"], f, v, "D", f"取所接节点 {L.at[i, nf]} 的井底")
    # P-03 井底
    ends = pd.concat([L[["from_node", "upelev"]].rename(columns={"from_node": "n", "upelev": "z"}),
                      L[["to_node", "downelev"]].rename(columns={"to_node": "n", "downelev": "z"})]).dropna()
    zmin = ends.groupby("n").z.min()
    for i in N.index[N.invert_elev.isna()]:
        v = zmin.get(N.at[i, "node_id"])
        if v is not None:
            setc(N, i, "invert_elev", float(v), "D")
            plog("P-03", N.at[i, "node_id"], "invert_elev", v, "D", "所接管段该端管底的最小值")
    # P-09 压力管网里的节点（泵位、压力管汇合点）：AECOM 的 shapefile 没导出这些点，从 Base 情景的 SWMM 导出（英尺）按坐标 2 m 内取井底。置信度 C
    #       （2026-10-21 资产层关口 G-02 拦下 6 个无井底节点后加的规则）
    nn = N.index[N.invert_elev.isna()]
    if len(nn):
        sys.path.insert(0, str(ROOT / "tools"))
        from r017_l2_audit import inp_section
        from scipy.spatial import cKDTree as _T
        s = source("aecom_rda")
        inp = next(s["abspath"].rglob(f"*_{s['base_run']}.inp"))
        ft = 0.3048
        xy = {r[0]: (float(r[1]) * ft, float(r[2]) * ft) for r in inp_section(inp, "COORDINATES")}
        J = {r[0]: float(r[1]) * ft for r in inp_section(inp, "JUNCTIONS")}
        keys = [k for k in xy if k in J]
        tj = _T([xy[k] for k in keys])
        for i in nn:
            d, j = tj.query([N.geometry.x[i], N.geometry.y[i]])
            if d <= 2.0:
                setc(N, i, "invert_elev", J[keys[j]], "C")
                plog("P-09", N.at[i, "node_id"], "invert_elev", J[keys[j]], "C", f"AECOM SWMM 导出（运算 {s['base_run']}）节点 {keys[j]}，距离 {d:.2f} m")
    # P-04 井盖
    miss = N.index[N.rim_elev.isna()]
    dtm = next(p for p in source("dtm_auh")["abspath"].glob("*.TIF") if not p.name.startswith("._"))
    if len(miss):
        with rasterio.open(dtm) as ds:
            g = [v[0] for v in ds.sample(list(zip(N.geometry.x[miss], N.geometry.y[miss])))]
        for i, v in zip(miss, g):
            if abs(v) < 1e30:
                setc(N, i, "rim_elev", float(v), "D")
                plog("P-04", N.at[i, "node_id"], "rim_elev", v, "D", "DTM 地面（未扣整体偏差，见问题 D 类）")
    # P-05 出水口边界：按接进来的管是重力管还是压力管分类型
    N["outfall_kind"] = None
    for i in N.index[N.node_kind == "outfall"]:
        nid = N.at[i, "node_id"]
        kinds = set(L[(L.to_node == nid) | (L.from_node == nid)].link_kind)
        before = (N.at[i, "boundary_type"], N.at[i, "tailwater"], N.at[i, "flap"])
        if kinds and kinds <= {"force"}:
            N.at[i, "outfall_kind"], N.at[i, "boundary_type"], N.at[i, "flap"] = "pump_discharge", "FREE", "no"
            setc(N, i, "tailwater", None, "C")
            plog("P-05", nid, "boundary_type", None, "C", f"泵站压力管出口：自由出流、不带拍门（AECOM RD-A 模型同此设置）；原值 {before}")
        else:
            N.at[i, "outfall_kind"], N.at[i, "boundary_type"], N.at[i, "flap"] = "gravity_sea", "FIXED", "yes"
            setc(N, i, "tailwater", WA726_TAILWATER, "B")
            plog("P-05", nid, "tailwater", WA726_TAILWATER, "B", f"重力出水口：WA-726-1 §4.8 固定尾水 +1.3 m NADD、带拍门；原值 {before}")
    N["issues"] = [",".join(ob2iss.get(x, [])) for x in N.node_id]
    L["issues"] = [",".join(ob2iss.get(x, [])) for x in L.asset_id]
    # P-07 泵
    P = gpd.read_file(src, layer="pump")
    PC = pyogrio.read_dataframe(src, layer="pump_curve")
    from scipy.spatial import cKDTree
    t = cKDTree(np.c_[N.geometry.x, N.geometry.y])
    P["outlet_node"] = [N.node_id.iloc[t.query([g.x, g.y])[1]] for g in P.geometry]
    suction = {}                                   # 泵位置节点 ← 吸水管的上游节点
    for r in L[L.link_kind == "force"].itertuples():
        suction.setdefault(r.to_node, r.from_node)
    P["inlet_node"] = [suction.get(o, w) for o, w in zip(P.outlet_node, P.wetwell_node)]
    for r in P.itertuples():
        plog("P-07", r.asset_id, "curve_id", None, "C", f"AECOM 泵曲线 {r.curve_id}；吸水 {r.inlet_node}（吸水管上游节点；融合表记的是 {r.wetwell_node}）→ 出水 {r.outlet_node}")
    # P-06 子汇水区
    Ci = gpd.read_file(src, layer="catchment")
    lab2node = dict(zip(Ci.out_node_src.astype(str), Ci.out_node))
    C = gpd.read_file(COMPOSITE).to_crs(32640)
    C["out_node_int"] = C.out_node.astype(str).map(lab2node)
    C["asset_id"] = "CAT:" + C.label.astype(str)
    C["conf"] = json.dumps({"area_m2": "C", "f": "D", "cn_perv": "C", "tc_min": "C"}, ensure_ascii=False)
    C["src"] = "AECOM 子汇水区（成对合成）+ Makani 不透水比例"
    plog("P-06", f"{len(C)} 块", "area_m2/f/cn_perv", float(C.area_m2.sum()), "C/D",
         f"AECOM 成对地块合成，{len(Ci)} 块 → {len(C)} 块；面积 {Ci.area_m2.sum() / 1e4:.1f} → {C.area_m2.sum() / 1e4:.1f} ha；f 面积加权 {np.average(C.f, weights=C.area_m2):.3f}")
    # P-11 完全重复的管只留一条
    key = L.asset_id + "|" + L.from_node + "|" + L.to_node + "|" + L.geometry.normalize().to_wkt()
    dup = L[key.duplicated()]
    for r in dup.itertuples():
        plog("P-11", r.asset_id, "record", None, "—", "完全重复的记录（编号、两端节点、几何相同），去掉重复的一条")
    L = L[~key.duplicated()].reset_index(drop=True)
    # P-10 管端几何吸附到节点（放在最后：前面的规则不动几何）
    from shapely.geometry import LineString, Point
    xy = dict(zip(N.node_id, zip(N.geometry.x, N.geometry.y)))
    geoms = []
    for i, r in L.iterrows():
        c = list(r.geometry.coords)
        for end, nid in ((0, r.from_node), (-1, r.to_node)):
            d = Point(c[end]).distance(Point(xy[nid]))
            if d >= 0.001:
                plog("P-10", r.asset_id, "geometry", d, "—", f"{'起点' if end == 0 else '终点'}移到节点 {nid}，移动 {d:.3f} m")
                c[end] = xy[nid]
        geoms.append(LineString(c))
    L = L.set_geometry(gpd.GeoSeries(geoms, index=L.index, crs=L.crs))
    dst = OUT / "proc" / f"{ext}.gpkg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    N.to_file(dst, layer="node", driver="GPKG")
    L.to_file(dst, layer="link", driver="GPKG")
    P.to_file(dst, layer="pump", driver="GPKG")
    pyogrio.write_dataframe(PC, dst, layer="pump_curve", driver="GPKG")
    C.to_file(dst, layer="catchment", driver="GPKG")
    PL = pd.DataFrame(PLOG)
    pyogrio.write_dataframe(PL, dst, layer="process_log", driver="GPKG")
    S = PL.groupby("rule").size().to_dict()
    S["catchments_without_node"] = int(C.out_node_int.isna().sum())
    lineage("process", [src, OUT / "issues" / f"{ext}_issues.csv", dtm, COMPOSITE], [dst],
            {"extent": ext, "rules": ["P-01", "P-02", "P-03", "P-04", "P-05", "P-06", "P-07", "P-08", "P-09", "P-10", "P-11"]},
            notes=json.dumps(S, ensure_ascii=False) + "；P-06 用到 Makani（经 tools/rda_catchments.py，2026-10-20 产物）")
    log("  加工：" + json.dumps(S, ensure_ascii=False))
    log(f"→ {dst}")
    return dst, S


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.process")
    ap.add_argument("--extent", required=True)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    process(a.extent)


if __name__ == "__main__":
    main()
