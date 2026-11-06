"""开源路线：建 Expanded 模型并导出（对应 ArcGIS 路线的 s3 映射 + s4 装载 / 关联 / 端子 + s5 校验子网 + s6 导出 + ledger_dirty）。

  OSS_GOV_WORK=<工作目录> python tools/oss_gov/g_export.py <工作目录>

输入：<工作目录>/02_prep/prep.gdb（开源跑的预处理）、Expanded_local.gdb（资产包）、out/c2/（o1 建图、o2 查错、o3 子网的结果）
输出（和 ArcGIS 路线同名、同结构，下游 ledger.py / round_diff.py / region.py 两条路线通用）：
  07_export/Stormwater_AD_reporting.gdb   5 个网络类（编码 + d_ 描述列、子网名、isconnected、端子、关联状态、控制器）
                                          + Associations + SubnetworkControllers
  07_export/governance_records.gdb        排除、问题、冲突、移动泵、AECOM 汇水区（预处理库原样复制）
  06_validate/s5_report.json              错误按 UN 错误号计数、子网数、控制器数（口径同 ArcGIS：按要素计）
  08_area/dirty.json                      网络错误（每处一条，带 WKT）
复现的 ArcGIS 做法（第十四轮逐字段核对得出）：
  关联   检查井结构 ⊃ 井内流道（映射的关联键）；塘 ⊃ 和塘（外扩 0.01 m）相交、规则表允许的要素（untools Associate）；
         系统出流点 — 锚点（节点 — 节点）；0.01 m 内重合、规则表允许互接的两个点（节点 — 节点）
  关联状态  容器 1、内容 4、节点 — 节点连通 32，按位相加
  端子   按数字化方向（untools AssignTerminalConnections）：管的终点在设备上 → 设备的上游端子，起点在设备上 → 下游端子；
         只在规则表里有「该端子 — 该类管」的规则时才挂（泵的出水重力管没有端子、压力管挂高压出口）
  控制器  出水口 OUT_<编号>、系统出流点 SO_<编号>（同 s5_validate），端子 Inlet Port，层级 Stormwater Management Area
  结构的子网名  检查井结构 = 井内流道的子网名；塘 = 塘内要素的子网名（只有一个时），否则 Unknown
映射（预处理类 → 目标类 / 资产组 / 资产类型）读 tools/exp_gov/s3_mapping.py 的 MAP，两条路线同一份。
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyogrio
import shapely
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from assetpkg import AssetPackage  # noqa: E402
from fgdb import write as fwrite  # noqa: E402

CLASSES = ("StormwaterLine", "StormwaterDevice", "StormwaterJunction", "StructureJunction", "StructureBoundary")
SHORT = {"StormwaterLine": "Line", "StormwaterDevice": "Device", "StormwaterJunction": "Junction",
         "StructureJunction": "StructureJunction", "StructureBoundary": "StructureBoundary"}
GEOM = {"StormwaterLine": "MultiLineString", "StructureBoundary": "MultiPolygon"}
NO_COPY = {"tgt_ag", "tgt_at", "shape_length", "shape_area"}
ASSOC_TOL = 0.01                     # untools Associate 的距离（用户 2026-10-23 定「只认重合」）
TIER, TIER_CODE = "Stormwater Management Area", 1
ST_CONTAINER, ST_CONTENT, ST_CONNECT = 1, 4, 32


def load_map():
    src = (HERE.parent / "exp_gov" / "s3_mapping.py").read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "MAP":
            return ast.literal_eval(node.value)
    raise RuntimeError("s3_mapping.py 里没有 MAP")


def gidfmt(g):
    return "{" + str(g).upper().strip("{}") + "}"


def main(work):
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.time()
    W = Path(work)
    prep = W / "02_prep" / "prep.gdb"
    apg = W / "Expanded_local.gdb"
    ap = AssetPackage(apg)
    MAP = load_map()
    o = W / "out" / "c2"
    feat = pd.read_parquet(o / "features.parquet")
    sub = pd.read_parquet(o / "subnet.parquet").set_index("gid")
    ctrl = pd.read_parquet(o / "controllers.parquet")
    err = pd.read_parquet(o / "errors.parquet")
    jj_anchor = pd.read_parquet(o / "assoc.parquet")
    rules = pyogrio.read_dataframe(apg, layer="B_Rules", read_geometry=False)
    tca = pyogrio.read_dataframe(apg, layer="B_TerminalConfiguration_Assignment", read_geometry=False)
    tct = pyogrio.read_dataframe(apg, layer="B_TerminalConfiguration_Terminals", read_geometry=False)
    name_of = sub["C"].fillna("Unknown")
    gid_of = {(l, f): g for l, f, g in zip(feat.layer, feat.fid, feat.gid)}
    glob_of = dict(zip(feat.gid, feat.globalid))
    ctrl_name = dict(zip(ctrl.gid, ctrl.name))
    R = {"engine": "oss", "classes": {}}

    # 1 映射：预处理类 → Expanded 类（编码、缺省值、子网名）
    frames = {c: [] for c in CLASSES}
    layers = [n for n, _ in pyogrio.list_layers(prep)]
    for (lay, typ), (cls, ag_name, at_name, _) in MAP.items():
        if lay not in layers or cls not in CLASSES:
            continue
        df = pyogrio.read_dataframe(prep, layer=lay)
        df["fid_"] = np.arange(len(df))
        if typ is not None:
            col = "tgt_ag" if "tgt_ag" in df and (df["tgt_ag"] == typ).any() else "tgt_at"
            df = df[df[col] == typ]
        if not len(df):
            continue
        ag = ap.subtype_code(cls, ag_name)
        df = df.rename(columns={c: c.lower() for c in df.columns if c != "geometry"})
        df = df.drop(columns=[c for c in df.columns if c in NO_COPY])
        for f, v in {**ap.field_defaults(cls), **ap.defaults(cls, ag)}.items():     # 类一级缺省 + 资产组缺省（后者优先）
            if f not in df.columns:
                df[f] = v
            elif f != "assetstate":
                df[f] = df[f].where(df[f].notna(), v)
        for f in ap.not_nullable_numeric(cls):              # 不允许为空、又没有缺省值的数值字段：ArcGIS 填 0
            if f not in df.columns:
                df[f] = 0
            else:
                df[f] = df[f].where(df[f].notna(), 0)
        df["assetgroup"], df["assettype"] = ag, ap.assettype_code(cls, ag_name, at_name)
        df["_gid"] = [gid_of[(lay, int(i))] for i in df["fid_"]]
        df["_ag"], df["_at"] = ag_name, at_name
        df["globalid"] = [gidfmt(glob_of[g]) for g in df["_gid"]]
        if cls.startswith("Stormwater"):
            nm = [name_of.get(g, "Unknown") for g in df["_gid"]]
            df["smasubnetworkname"] = nm
            df["isconnected"] = [0 if n in ("Unknown", "", None) else 1 for n in nm]
        frames[cls].append(df.drop(columns=["fid_"]))
    F = {c: pd.concat(v, ignore_index=True) for c, v in frames.items() if v}
    import geopandas as gpd
    for c in F:
        F[c] = gpd.GeoDataFrame(F[c], geometry="geometry", crs=32640)
    gpos = {c: dict(zip(F[c]["_gid"], range(len(F[c])))) for c in F}
    cls_of = {g: c for c in F for g in F[c]["_gid"]}

    # 2 关联
    assoc = []                                   # (类型, from gid, to gid)
    # 井结构和井内流道由同一条源记录生成（s2_prep.channels）：按源图层 + 源记录号配对（3 口井没有资产编号，不能按编号配）
    sk = lambda d: list(zip(d.src_layer, pd.to_numeric(d.src_oid, errors="coerce")))
    vault = {k: g for k, g in zip(sk(F["StructureJunction"]), F["StructureJunction"]["_gid"]) if k[1] == k[1]}
    dev = F["StormwaterDevice"]
    for k, g, ag_ in zip(sk(dev), dev["_gid"], dev["_ag"]):
        if ag_ == "Manhole Channel" and k in vault:
            assoc.append(("Containment", vault[k], g))
    crules = rules[rules.rule_type == "Containment"]
    allow = defaultdict(set)
    for r in crules.itertuples():
        allow[(r.from_feature_class, r.from_asset_group, r.from_asset_type)].add((r.to_feature_class, r.to_asset_group, r.to_asset_type))
    if "StructureBoundary" in F:
        P = F["StructureBoundary"]
        pond_pairs = set()
        for pg, pgid, pag, pat in zip(P.geometry.values, P["_gid"], P["_ag"], P["_at"]):
            ok = allow.get(("StructureBoundary", pag, pat), set())
            zone = pg.buffer(ASSOC_TOL)
            for c in ("StormwaterDevice", "StormwaterJunction", "StormwaterLine", "StructureBoundary"):
                if c not in F:
                    continue
                X = F[c]
                idx = shapely.STRtree(X.geometry.values).query(zone, predicate="intersects")   # 和塘（外扩 0.01 m）相交就算在塘里（同 untools：44 根相交的管全部被关联）   # 和塘（外扩 0.01 m）相交就算在塘里（同 untools：44 根相交的管全部被关联，哪怕只有一小段进塘）
                for i in idx:
                    g = X["_gid"].iat[i]
                    if g != pgid and (SHORT[c], X["_ag"].iat[i], X["_at"].iat[i]) in ok and frozenset((pgid, g)) not in pond_pairs:
                        assoc.append(("Containment", pgid, g))
                        pond_pairs.add(frozenset((pgid, g)))   # 塘按存储顺序处理；已建过的一对不再建反方向（同 ArcGIS：塘 38 ⊃ 塘 39，没有 39 ⊃ 38）
    for f_, t_ in zip(jj_anchor.from_gid, jj_anchor.to_gid):
        assoc.append(("Junction Junction Connectivity", f_, t_))
    jrules = {(r.from_asset_group, r.from_asset_type, r.to_asset_group, r.to_asset_type)
              for r in rules[rules.rule_type == "Junction Junction Connectivity"].itertuples()}
    pts = pd.concat([F[c][["_gid", "_ag", "_at", "geometry"]] for c in ("StormwaterDevice", "StormwaterJunction") if c in F], ignore_index=True)
    xy = shapely.get_coordinates(pts.geometry.values)
    have_pair = {frozenset((a, b)) for t, a, b in assoc}
    for i, j in sorted(cKDTree(xy).query_pairs(ASSOC_TOL)):
        a, b = pts.iloc[i], pts.iloc[j]
        if ((a._ag, a._at, b._ag, b._at) in jrules or (b._ag, b._at, a._ag, a._at) in jrules) and frozenset((a._gid, b._gid)) not in have_pair:
            assoc.append(("Junction Junction Connectivity", a._gid, b._gid))
    status = defaultdict(int)
    for t, a, b in assoc:
        if t == "Containment":
            status[a] |= ST_CONTAINER
            status[b] |= ST_CONTENT
        else:
            status[a] |= ST_CONNECT
            status[b] |= ST_CONNECT
    for c in F:
        F[c]["associationstatus"] = [status.get(g, 0) for g in F[c]["_gid"]]
    R["associations"] = pd.Series([t for t, *_ in assoc]).value_counts().to_dict()

    # 3 端子（线的两端；按数字化方向）
    cfg_of = {(r.asset_group, r.asset_type): r.terminal_configuration_name for r in tca.itertuples() if r.feature_class == "Device"}
    up_t = defaultdict(list)
    for r in tct.itertuples():
        up_t[(r.terminal_configuration_name, r.upstream == 1)].append(r.name)
    je = {(r.from_asset_group, r.from_asset_type, r.from_terminal, r.to_asset_group, r.to_asset_type)
          for r in rules[rules.rule_type == "Junction Edge Connectivity"].itertuples() if isinstance(r.from_terminal, str)}
    term_code = {v: k for k, v in ap.domain("Network_5_TerminalNames").items()}
    dpos = {}
    for c in ("StormwaterDevice",):
        D = F[c]
        for p, ag_, at_ in zip(shapely.get_coordinates(D.geometry.values), D["_ag"], D["_at"]):
            if (ag_, at_) in cfg_of:
                dpos[(round(p[0], 4), round(p[1], 4))] = (ag_, at_, cfg_of[(ag_, at_)])
    L = F["StormwaterLine"]
    fr, to = [], []
    for g, lag, lat in zip(L.geometry.values, L["_ag"], L["_at"]):
        c = shapely.get_coordinates(g)
        res = []
        for p, upstream in ((c[0], False), (c[-1], True)):
            d = dpos.get((round(p[0], 4), round(p[1], 4)))
            name = None
            if d:
                for tn in up_t.get((d[2], upstream), []):
                    if (d[0], d[1], tn, lag, lat) in je:
                        name = tn
                        break
            res.append(term_code.get(name, 0) if name else 0)
        fr.append(res[0]); to.append(res[1])
    L["fromdeviceterminal"], L["todeviceterminal"] = fr, to

    # 4 子网控制器、结构的子网名
    D = F["StormwaterDevice"]
    nm_ctrl = []
    for g, a, ag_ in zip(D["_gid"], D.assetid, D["_ag"]):
        if g in ctrl_name:
            pre = "OUT_" if ag_ == "Outfall" else "SO_"
            nm_ctrl.append("Inlet Port/" + re.sub(r"[^A-Za-z0-9_]", "_", f"{pre}{a if isinstance(a, str) and a else gidfmt(glob_of[g])}")[:64])
        else:
            nm_ctrl.append("Unknown")
    D["subnetworkcontrollername"] = nm_ctrl
    D["issubnetworkcontroller"] = [1 if g in ctrl_name else 0 for g in D["_gid"]]
    D["tiername"] = [TIER_CODE if g in ctrl_name else 0 for g in D["_gid"]]
    D["tierrank"] = D["tiername"]
    D["terminalconfiguration"] = "Default"
    content = defaultdict(set)
    for t, a, b in assoc:
        if t == "Containment":
            content[a].add(name_of.get(b, "Unknown") if cls_of.get(b, "").startswith("Stormwater") else None)
    for c in ("StructureJunction", "StructureBoundary"):
        if c in F:
            F[c]["subnetworkname"] = ["::".join(sorted(s)) if s else "Unknown"          # 多个子网用「::」连起来（同 ArcGIS）
                                      for s in ({x for x in content.get(g, set()) if x not in (None, "Unknown")} for g in F[c]["_gid"])]

    # 5 写报表库（编码值域字段都加 d_ 描述列，同 UDMS 导出）
    ex = W / "07_export"
    ex.mkdir(parents=True, exist_ok=True)
    rg = ex / "Stormwater_AD_reporting.gdb"
    shutil.rmtree(rg, ignore_errors=True)
    for cls in CLASSES:
        if cls not in F:
            continue
        df = F[cls]
        dom_fields = {f[0].lower() for f in ap.fields(cls) if f[3]} | {"assetgroup", "assettype", "tiername"}
        for f in sorted(dom_fields):
            if f in df.columns:
                if f == "tiername":
                    df["d_tiername"] = [TIER if v == TIER_CODE else "Unknown" for v in df[f]]
                    continue
                df[f"d_{f}"] = [ap.describe(cls, a, f, v) if v == v and v is not None else None for a, v in zip(df["assetgroup"], df[f])]
        df["orig_fid"] = np.arange(1, len(df) + 1)
        df = df.drop(columns=["_gid", "_ag", "_at"])
        for c in ("src_oid", "src_compkey"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int32")
        PD = {"SmallInteger": "Int16", "Integer": "Int32", "Double": "float64", "Single": "float32"}
        for f, ft, _, _ in ap.fields(cls):                  # 按资产包的字段类型写（缺省值填进文本列会变成 '1.0'）
            fl = f.lower()
            if fl in df.columns and ft in PD:
                df[fl] = pd.to_numeric(df[fl], errors="coerce").astype(PD[ft])
        fwrite(df, rg, cls, geometry_type=GEOM.get(cls, "Point"), promote_to_multi=cls in GEOM)
        R["classes"][cls] = len(df)
        print(f"  {cls}: {len(df):,}", flush=True)
    A = pd.DataFrame([(t, gidfmt(glob_of[a]), gidfmt(glob_of[b])) for t, a, b in assoc], columns=["d_associationtype", "fromglobalid", "toglobalid"])
    fwrite(A, rg, "Associations")
    # 子网控制器表（同 UDMS 导出的 SubnetworkControllers：控制器名不带端子前缀）
    dv = pyogrio.read_dataframe(rg, layer="StormwaterDevice", columns=["globalid", "subnetworkcontrollername", "d_assetgroup", "d_assettype"],
                                read_geometry=False)
    dv = dv[dv.subnetworkcontrollername != "Unknown"]
    gname = {gidfmt(glob_of[g]): n for g, n in ctrl_name.items()}
    Cn = pd.DataFrame({"subnetworkcontrollername": dv.subnetworkcontrollername.str.split("/", n=1).str[1].values, "tierrank": TIER_CODE,
                       "tiername": TIER, "d_tiername": TIER, "featureglobalid": dv.globalid.values, "featureassetgroup": dv.d_assetgroup.values,
                       "featureassettype": dv.d_assettype.values, "domainnetworkname": "Stormwater",
                       "subnetworkname": [gname.get(x) for x in dv.globalid], "d_featureterminalid": "Inlet Port"})
    fwrite(Cn, rg, "SubnetworkControllers")
    R["controllers"] = len(Cn)

    # 6 治理记录库
    rec = ex / "governance_records.gdb"
    shutil.rmtree(rec, ignore_errors=True)
    for t in ("EXCLUDED", "ISSUES", "CONFLICTS", "MOVABLE_PUMP", "AECOM_CATCHMENT"):
        if t in layers:
            d = pyogrio.read_dataframe(prep, layer=t)
            if "geometry" in d and d.geometry.isna().all():
                fwrite(pd.DataFrame(d.drop(columns="geometry")), rec, t)
            else:
                fwrite(d, rec, t, promote_to_multi=t == "AECOM_CATCHMENT")

    # 7 校验结果（口径同 ArcGIS s5_report：错误按要素计）+ 08_area/dirty.json
    v = W / "06_validate"
    v.mkdir(parents=True, exist_ok=True)
    by_code = {str(int(c)): int(g.origin_gid.ge(0).sum() + g.dest_gid.ge(0).sum()) for c, g in err.groupby("code")}
    lines_named = name_of[[g for g in sub.index if sub.at[g, "kind"] == "line"]]
    rep = {"engine": "oss", "dirty_areas": int(len(err)), "errors_by_un_code": by_code, "subnetworks": int(ctrl.name.nunique()),
           "controllers": len(ctrl), "lines_by_subnetwork_state": {"有子网": int((~lines_named.isin(["Unknown"])).sum()),
                                                                    "无子网": int(lines_named.isin(["Unknown"]).sum())}}
    json.dump(rep, open(v / "s5_report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    lg = gpd.read_parquet(o / "lines_geom.parquet").set_index("gid").geometry
    fa = feat.set_index("gid")
    dirty = []
    for i, r in err.reset_index(drop=True).iterrows():
        g = lg.get(r.origin_gid)
        if g is None:
            g = shapely.Point(fa.at[r.origin_gid, "minx"], fa.at[r.origin_gid, "miny"])
        dirty.append({"oid": i + 1, "code": int(r.code), "msg": f"{int(r.code)},{r.note or ''}", "wkt": g.envelope.wkt})
    (W / "08_area").mkdir(exist_ok=True)
    json.dump(dirty, open(W / "08_area" / "dirty.json", "w", encoding="utf-8"), ensure_ascii=False)
    R["s5_report"] = rep
    R["seconds"] = round(time.time() - t0)
    json.dump(R, open(ex / "s6_report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps(R, ensure_ascii=False, default=str)[:1500])


if __name__ == "__main__":
    main(sys.argv[1])
