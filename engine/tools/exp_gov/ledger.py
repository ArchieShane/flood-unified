"""全市治理台账 + 遗留问题包（用户 2026-10-24 定为标准动作；2026-10-25 要求：治理前问题、怎么治理、解决了什么、解决不了什么都要记清楚）。

  先在 ArcGIS 环境跑 ledger_dirty.py <工作目录>（导出脏区和属性规则错误），再：
  python tools/exp_gov/ledger.py --work D:/swbrain_work/exp_gov --round 10 --out out/exp_gov/ledger

输入（都是治理链的产物，只读）：
  00_profile/profile.json            第 0 步探查（治理前）
  02_prep/prep_log[_roundN].json     各轮预处理统计
  06_validate[_roundN]/s5_report.json、remaining.json、remaining_isolated.csv   各轮网络校验和剩余分类
  07_export/governance_records.gdb   排除、问题、冲突三张表（逐条）
  07_export/Stormwater_AD_reporting.gdb   治理后的交付库（带 dmt_ 原始字段）
  08_area/dirty.json、attr_errors.json    ledger_dirty.py 的产物
  src_dmt.gdb                        原始数据（给逐条记录取坐标）
输出 <out>/：
  rounds.json                各轮指标
  ledger.json                台账用的全部数字
  全市遗留问题清单_第N轮.xlsx  汇总、待处理（逐条）、治理动作记录（逐条）、网络错误、未挂子网的系统
  全市遗留问题_第N轮.gpkg      同上的点图层
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyogrio
import shapely

# 问题码 → 问题（中文）、我们的处理、严重程度、需要谁做什么、状态
CODES = {
    "DATUM-ODD": ("高程统一基准后仍和地形对不上", "保留减 100 m 后的值，登记", "影响模型", "客户核对这条高程", "待客户确认"),
    "E-SENT": ("高程是哨兵值（−999、−99 等）", "置空，建模时按缺省规则补", "影响模型", "客户补正确高程", "待客户补数据"),
    "E-RANGE": ("高程超出合理范围", "原样保留，登记", "影响模型", "客户核对这条高程", "待客户确认"),
    "REV-SLOPE": ("倒坡：起点管底低于终点", "原样保留，登记", "影响模型", "客户核对测量数据", "待客户确认"),
    "DUP-GEOM": ("几何相同、属性不同的重复记录", "都装入，登记", "只影响台账", "客户确认哪条有效", "待客户确认"),
    "DUP-ID": ("编号重复", "照装，登记", "只影响台账", "客户给唯一编号", "待客户确认"),
    "ID-EMPTY": ("编号为空", "资产编号置空，登记", "只影响台账", "客户补编号", "待客户补数据"),
    "S-NULL": ("状态为空", "按在用装入（用户定）", "只影响台账", "客户确认状态", "待客户确认"),
    "S-ODD": ("状态值不规范（Undefined 等）", "按在用装入（S 表）", "只影响台账", "客户确认状态", "待客户确认"),
    "T-ODD": ("类型值不规范", "按缺省类型装入", "只影响台账", "客户确认类型", "待客户确认"),
    "M-ODD": ("管材写法无法识别", "管材置未知，原文保留", "影响模型", "客户确认管材", "待客户确认"),
    "D-TEXT": ("管径是文字", "管径置空，原文保留", "影响模型", "客户补管径", "待客户补数据"),
    "MULTIPART": ("一条管由多段组成（多部件）", "不拆，登记（用户定）", "只影响台账", "客户确认是否应拆成多条", "待客户确认"),
    "TYPE-CONFLICT": ("DMT 与 AECOM 对同一段管类型不同", "按 AECOM 作重力管（用户定）", "影响模型", "客户确认", "已处理，待客户确认"),
    "POND-NOPIPE": ("塘内及边界 5 m 内没有管端", "塘点放在塘的质心", "影响模型", "客户确认塘的进水管", "待客户确认"),
    "POND-YIELD": ("塘的接入管末端已有出水口 / 检查井", "不放塘点（用户定，第十轮）", "影响模型", "末端是检查井的，客户确认塘的进水管", "已处理，待客户确认"),
    "POND-ENDCAP": ("塘的接入管末端记成了封堵端", "封堵端换成塘点（用户定，第十轮）", "影响模型", "—", "已处理"),
    "FG-NOPIPE": ("拍门找不到接入管", "拍门不装，登记", "影响模型", "客户确认拍门位置", "待客户确认"),
    "FORCE-INLET": ("压力管的端点直接接雨水口", "这段改按重力管（用户定，第十轮）", "影响模型", "客户确认管的类型", "已处理，待客户确认"),
    "STACK": ("同一位置有多个点（检查井、雨水井、雨水口、封堵端）", "按 检查井 > 雨水井 > 雨水口 > 封堵端 留一个，其余排除（用户定）", "影响模型", "—", "已处理"),
    "STACK-SOAK": ("渗井和雨水口在同一位置", "留渗井、去掉雨水口（用户定，第十轮）", "影响模型", "—", "已处理"),
    "SNAP": ("管端悬空（0.5 m 内有可接的点或管端）", "吸附过去（用户定）", "影响模型", "—", "已处理"),
    "SNAP-MM": ("管端之间有几毫米的缝", "接上（用户定）", "影响模型", "—", "已处理"),
    "MULTIPART-JOIN": ("一根管断成几截（截间距 ≤ 0.5 m）", "首尾相连合成一截（用户定，第十一轮）", "影响模型", "—", "已处理"),
    "SNAP-SEG": ("管端落在别的管中段（支管接干管没打折点）", "接上：≤5 mm 在干管上加折点；悬空且 ≤0.5 m 的移过去并加折点（用户定，第十二轮）", "影响模型", "—", "已处理"),
    "SNAP-JCT": ("几根管的接点离检查井 / 雨水口不到 0.5 m，没接到井上", "整个接点移到井上（用户定，第十二轮）", "影响模型", "—", "已处理"),
    "SNAP-SKIP": ("管两端都悬空，吸附后会变成零长度", "不吸附，登记", "影响模型", "客户核对这根管", "待客户确认"),
}
LINE_LAYERS = {"PIPELINE"}


def load_json(p):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def rounds(work, upto, last="06_validate"):
    """各轮指标。第一至三轮的网络库已被覆盖，只有当轮日志里的数字（报告第 2.2 节），照抄并注明。"""
    early = {1: (1453, 3.8), 2: (621, 27.5), 3: (615, 30.1)}
    out = []
    for n in range(1, upto + 1):
        d = os.path.join(work, f"06_validate_round{n}") if n < upto else os.path.join(work, last)
        s, r = load_json(os.path.join(d, "s5_report.json")), load_json(os.path.join(d, "remaining.json"))
        rep, sub = s.get("report", {}), s.get("subnet", {})
        bc = r.get("by_class") or {}
        tot = sum(bc.values())
        row = {"round": n, "dirty_areas": rep.get("dirty_areas"), "errors": rep.get("errors_by_un_code"),
               "subnetworks": sub.get("subnetworks"), "controllers": sub.get("controllers"), "by_class": bc,
               "traced_pct": round(100 * bc.get("A 已挂子网", 0) / tot, 1) if tot else None, "source": "网络库"}
        if n in early:
            row.update(dirty_areas=early[n][0], traced_pct=early[n][1], source="当轮日志（网络库已覆盖）")
        out.append(row)
    return out


def rep_points(work):
    """原始数据每条记录的代表点：(图层, OID) → (x, y)。线取中点。"""
    pts = {}
    src = os.path.join(work, "src_dmt.gdb")
    for lay, _ in pyogrio.list_layers(src):
        df = pyogrio.read_dataframe(src, layer=lay, columns=[], fid_as_index=True)
        g = df.geometry.values
        if lay in LINE_LAYERS:
            p = shapely.line_interpolate_point(shapely.line_merge(g), 0.5, normalized=True)
        else:
            p = shapely.point_on_surface(g)
        xy = shapely.get_coordinates(p, include_z=False) if len(p) else np.zeros((0, 2))
        ok = ~shapely.is_empty(p)
        for oid, (x, y) in zip(df.index[ok], xy):
            pts[(lay, int(oid))] = (round(float(x), 3), round(float(y), 3))
    return pts


def classify_dirty(D, info):
    """脏区按涉及的要素归类（与 2026-10-25 的分析同口径）。错误码在消息里：每段「码,源,{GlobalID}」。"""
    rows = []
    for d in D:
        items = []
        for part in d["msg"].split(";"):
            p = part.split(",")
            if len(p) >= 2 and p[0].strip().isdigit():
                g = re.search(r"\{[0-9A-Fa-f-]+\}", part)
                items.append((int(p[0]), info.get(g.group(0).upper()) if g else None))
        codes = sorted({c for c, _ in items}) or [int(np.log2(d["code"])) if d["code"] and (d["code"] & (d["code"] - 1)) == 0 else d["code"]]
        kinds = sorted({f"{x[1]}/{x[2]}" for _, x in items if x})
        ids = sorted({str(x[3]) for _, x in items if x and isinstance(x[3], str) and x[3]})
        k = " | ".join(kinds)
        cs = set(codes)
        if cs == {10}:
            t = "重力管和穿孔管直接相接、中间没有节点"
        elif 8 in cs and "Force" in k:
            t = "压力管接在雨水口等点上（没有对应的连通规则）"
        elif 8 in cs:
            t = "其他没有连通规则的连接（渗井、泵、穿孔管等）"
        elif 9 in cs and "BMP" in k:
            t = "塘点和井 / 封堵端 / 管同点，管不知道接哪个"
        elif 9 in cs and "Infiltration" in k:
            t = "渗井和雨水口同点，管不知道接哪个"
        elif 9 in cs:
            t = "雨水口 / 检查井 / 封堵端同点，管不知道接哪个"
        elif 25 in cs and ("Outfall" in k or "BMP" in k):
            t = "出水口和塘点同点"
        elif 25 in cs:
            t = "两个点完全重合"
        elif cs & {19, 20, 21}:
            t = "管的几何有问题（多部件、自相交、重复折点）"
        elif 29 in cs:
            t = "子网命名冲突"
        else:
            t = "其他 " + "+".join(map(str, codes))
        rows.append({"oid": d["oid"], "codes": "+".join(map(str, codes)), "type": t, "assets": "、".join(ids)[:300], "kinds": k[:200], "wkt": d["wkt"]})
    return pd.DataFrame(rows)


def systems(gdb):
    """治理后交付库里按管端坐标（0.001 m）连成系统；返回每根管所在系统和系统汇总。"""
    U = pyogrio.read_dataframe(gdb, layer="StormwaterLine", columns=["assetid", "smasubnetworkname"])
    par = {}

    def root(x):
        while par.setdefault(x, x) != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    A = []
    C = [shapely.get_coordinates(geom) for geom in U.geometry.values]
    # 中间折点上有别的线的端点或有点才算连通（UN 规则，2026-10-25 第十四轮）
    hubs = {tuple(np.round(c[0], 3)) for c in C} | {tuple(np.round(c[-1], 3)) for c in C}
    for lay in ("StormwaterDevice", "StormwaterJunction"):
        pts = pyogrio.read_dataframe(gdb, layer=lay, columns=[])
        hubs |= {tuple(v) for v in np.round(shapely.get_coordinates(pts.geometry.values), 3)}
    for c in C:
        a, b = tuple(np.round(c[0], 3)), tuple(np.round(c[-1], 3))
        par[root(a)] = root(b)
        for v in np.round(c[1:-1], 3):
            if tuple(v) in hubs:
                par[root(tuple(v))] = root(a)
        A.append(a)
    U["sys"] = [root(a) for a in A]
    U["traced"] = U.smasubnetworkname.notna() & (U.smasubnetworkname != "Unknown")
    dev = pyogrio.read_dataframe(gdb, layer="StormwaterDevice", columns=["assetid", "d_assetgroup", "d_assettype"])
    dev["p"] = [tuple(np.round(shapely.get_coordinates(g)[0], 3)) for g in dev.geometry.values]
    dev["sys"] = [root(p) if p in par else None for p in dev.p]
    of = set(dev[dev.d_assetgroup == "Outfall"].sys.dropna())
    sink = set(dev[((dev.d_assetgroup == "Catch Basin") & (dev.d_assettype == "Infiltration")) | (dev.d_assetgroup == "BMP Point")].sys.dropna())
    return U, of, sink


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--archive", action="store_true", help="读存档的 06_validate_roundN / 07_export_roundN / 08_area/dirty_rN.json（复核旧轮用）")
    a = ap.parse_args(argv)
    W, N, out = a.work, a.round, Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ex = f"07_export_round{N}" if a.archive else "07_export"
    va = f"06_validate_round{N}" if a.archive else "06_validate"
    rep_gdb = os.path.join(W, ex, "Stormwater_AD_reporting.gdb")
    rec_gdb = os.path.join(W, ex, "governance_records.gdb")

    R = rounds(W, N, va)
    json.dump(R, open(out / "rounds.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("各轮：", [(r["round"], r["dirty_areas"], r["traced_pct"]) for r in R])

    pts = rep_points(W)
    print("原始记录代表点", len(pts))

    # 1) 逐条：问题登记、排除
    I = pyogrio.read_dataframe(rec_gdb, layer="ISSUES", read_geometry=False)
    X = pyogrio.read_dataframe(rec_gdb, layer="EXCLUDED", read_geometry=False)
    C = pyogrio.read_dataframe(rec_gdb, layer="CONFLICTS", read_geometry=False)

    def xy(lay, oid):
        try:
            return pts.get((str(lay), int(oid)), (None, None))
        except (TypeError, ValueError):
            return (None, None)
    todo, done = [], []
    joined = set(I[I.code == "MULTIPART-JOIN"].assetid)
    for r in I.itertuples():
        if r.code == "MULTIPART" and r.assetid in joined:
            continue                                        # 已在 MULTIPART-JOIN 里记为已处理
        q, how, sev, who, st = CODES.get(r.code, (r.code, "", "", "", "待定"))
        x, y = xy(r.layer, r.src_oid)
        row = {"类型": q, "问题码": r.code, "图层": r.layer, "原始 OID": r.src_oid, "资产编号": r.assetid, "说明": r.desc_,
               "我们的处理": how, "严重程度": sev, "需要谁做什么": who, "状态": st, "x": x, "y": y}
        (done if st == "已处理" else todo).append(row)
    for r in X.itertuples():
        x, y = xy(r.layer, r.src_oid)
        done.append({"类型": "排除：" + re.sub(r"与 \S+ \S+ 同点", "与别的点同点", str(r.reason).split("，")[0]), "问题码": "EXCLUDED", "图层": r.layer, "原始 OID": r.src_oid, "资产编号": r.assetid,
                     "说明": r.reason, "我们的处理": "不装入，进排除清单", "严重程度": "影响模型", "需要谁做什么": "客户确认这些记录确实不在用", "状态": "已处理，待客户确认", "x": x, "y": y})

    # 2) 治理后交付库：要素坐标和派生问题（占位安装日期、泵站无流量）
    info, axy = {}, {}
    for lay in ("StormwaterLine", "StormwaterDevice", "StormwaterJunction", "StructureJunction"):
        flds = [f for f in ("globalid", "assetid", "d_assetgroup", "d_assettype", "installdate") if f in pyogrio.read_info(rep_gdb, layer=lay)["fields"]]
        df = pyogrio.read_dataframe(rep_gdb, layer=lay, columns=flds)
        p = shapely.line_interpolate_point(shapely.line_merge(df.geometry.values), 0.5, normalized=True) if lay == "StormwaterLine" else shapely.point_on_surface(df.geometry.values)
        c = shapely.get_coordinates(p)
        for g, aid, ag, at, (x, y) in zip(df.globalid, df.assetid, df.d_assetgroup, df.d_assettype, c):
            info[str(g).upper()] = (lay, ag, at, aid)
            if aid:
                axy[aid] = (round(float(x), 3), round(float(y), 3))
        if "installdate" in df:                             # 管、雨水口 / 雨水井、封堵端、检查井都查（2026-10-25 补：原来只查了管）
            yr = pd.to_datetime(df.installdate, errors="coerce", utc=True).dt.year
            ph = df[yr <= 1900]
            for aid, ag_, (x, y) in zip(ph.assetid, ph.d_assetgroup, c[(yr <= 1900).values]):
                todo.append({"类型": "安装日期是占位值（1900 年以前）", "问题码": "DATE-PH", "图层": f"{lay} / {ag_}", "原始 OID": None, "资产编号": aid,
                             "说明": "installdate ≤ 1900（DMT 多为 1600 年）", "我们的处理": "照原样装入", "严重程度": "只影响台账",
                             "需要谁做什么": "客户补安装日期", "状态": "待客户补数据", "x": round(float(x), 3), "y": round(float(y), 3)})
    pu = pyogrio.read_dataframe(os.path.join(W, "src_dmt.gdb"), layer="PUMP", columns=["UNITID", "FLOW_RATE"], read_geometry=False, fid_as_index=True)
    for oid, r in pu.iterrows():
        if not r.FLOW_RATE:
            x, y = xy("PUMP", oid)
            todo.append({"类型": "泵站没有流量和泵曲线", "问题码": "PUMP-NOFLOW", "图层": "PUMP", "原始 OID": oid, "资产编号": r.UNITID,
                         "说明": f"FLOW_RATE = {r.FLOW_RATE}", "我们的处理": "照原样装入（RD-A 已从 AECOM 补齐）", "严重程度": "影响模型",
                         "需要谁做什么": "客户提供泵站参数", "状态": "待客户补数据", "x": x, "y": y})
    for r in C.itertuples():
        x, y = axy.get(r.assetid, (None, None))
        todo.append({"类型": "DMT 与 AECOM 数值不一致（RD-A）", "问题码": "CONFLICT", "图层": r.cls, "原始 OID": None, "资产编号": r.assetid,
                     "说明": f"{r.field}：DMT {r.DMT} / AECOM {r.AECOM}（{r.aecom_id}）", "我们的处理": "以 DMT 为准，差异登记", "严重程度": "影响模型",
                     "需要谁做什么": "客户逐类确认以哪份为准", "状态": "待客户确认", "x": x, "y": y})

    # 3) 网络错误（脏区）和它们挡住的管
    D = load_json(os.path.join(W, "08_area", f"dirty_r{N}.json" if a.archive else "dirty.json"))
    dg = classify_dirty(D, info) if D else pd.DataFrame(columns=["oid", "codes", "type", "assets", "kinds", "wkt"])
    U, of, sink = systems(rep_gdb)
    import geopandas as gpd
    if len(dg):
        dgg = gpd.GeoDataFrame(dg.drop(columns="wkt"), geometry=shapely.from_wkt(dg.wkt.tolist()), crs=32640)
        j = gpd.sjoin(dgg[["geometry"]], U[["sys", "geometry"]], predicate="intersects")
        sys_of = j.groupby(level=0).sys.apply(set)
    else:
        dgg = gpd.GeoDataFrame(dg, geometry=[], crs=32640)
        sys_of = pd.Series(dtype=object)
    untr = U[~U.traced].groupby("sys").size()
    dgg["挡住的未挂子网管数"] = [int(sum(untr.get(s, 0) for s in sys_of.get(i, set()))) for i in dgg.index]
    c = dgg.geometry.centroid
    dgg["x"], dgg["y"] = c.x.round(3), c.y.round(3)
    by_sys_err = collections.defaultdict(set)
    for i, ss in sys_of.items():
        for s in ss:
            by_sys_err[s].add(dgg.loc[i, "type"])

    # 4) 未挂子网的系统（逐个系统）
    srows = []
    for s, g in U[~U.traced].groupby("sys"):
        if s in of:
            cls = "B 有出水口，子网没更新"
        elif s in sink:
            cls = "C 内陆系统（终点是渗井 / 塘），子网没更新"
        else:
            cls = "D 孤立或断开（没有出水口、渗井、塘）"
        srows.append({"类别": cls, "管数": len(g), "代表点 x": s[0], "代表点 y": s[1], "挡住它的错误类型": "；".join(sorted(by_sys_err.get(s, ()))) or ("—" if not cls.startswith("D") else "无（缺管或缺终点）")})
    S = pd.DataFrame(srows).sort_values("管数", ascending=False)
    iso = pd.read_csv(os.path.join(W, va, "remaining_isolated.csv"), encoding="utf-8-sig") if os.path.exists(os.path.join(W, va, "remaining_isolated.csv")) else pd.DataFrame()

    # 5) 属性规则错误
    AR = pd.DataFrame(load_json(os.path.join(W, "08_area", f"attr_errors_r{N}.json" if a.archive else "attr_errors.json")))

    # 6) 汇总
    T = pd.DataFrame(todo)
    Dn = pd.DataFrame(done)
    summ = (T.groupby(["状态", "需要谁做什么", "类型"]).size().rename("条数").reset_index().sort_values(["状态", "条数"], ascending=[True, False]))
    summ_done = Dn.groupby(["类型", "状态"]).size().rename("条数").reset_index().sort_values("条数", ascending=False)
    er = []
    for t_, g_ in dgg.groupby("type"):
        ss = set().union(*[sys_of.get(i, set()) for i in g_.index])
        alone = {s_ for s_ in ss if by_sys_err.get(s_) == {t_}}
        er.append({"错误类型": t_, "处数": len(g_), "所在系统里未挂子网的管": int(sum(untr.get(s_, 0) for s_ in ss)),
                   "其中只被这一类挡住的": int(sum(untr.get(s_, 0) for s_ in alone))})
    err_sum = pd.DataFrame(er).sort_values("所在系统里未挂子网的管", ascending=False) if er else pd.DataFrame()
    sys_sum = S.groupby("类别").agg(系统数=("管数", "size"), 管数=("管数", "sum")).reset_index()
    total_lines = len(U)
    led = {"round": N, "lines_total": total_lines, "lines_traced": int(U.traced.sum()), "traced_pct": round(100 * U.traced.mean(), 1),
           "rounds": R, "todo_by_type": T.groupby("类型").size().to_dict(), "done_by_type": Dn.groupby("类型").size().to_dict(),
           "errors": err_sum.to_dict("records"), "systems": sys_sum.to_dict("records"),
           "attr_errors": AR.groupby("RuleName").size().to_dict() if len(AR) and "RuleName" in AR else {}}
    json.dump(led, open(out / "ledger.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)

    xl = out / f"全市遗留问题清单_第{N}轮.xlsx"
    with pd.ExcelWriter(xl, engine="openpyxl") as w:
        pd.DataFrame([{"项目": "管道总数", "数值": total_lines}, {"项目": "能追踪到出水口 / 终点（挂上子网）", "数值": int(U.traced.sum())},
                      {"项目": "占比 %", "数值": led["traced_pct"]}]).to_excel(w, sheet_name="总览", index=False)
        summ.to_excel(w, sheet_name="待处理汇总", index=False)
        sys_sum.to_excel(w, sheet_name="未挂子网汇总", index=False)
        err_sum.to_excel(w, sheet_name="网络错误汇总", index=False)
        T.to_excel(w, sheet_name="待处理（逐条）", index=False)
        dgg.drop(columns="geometry").to_excel(w, sheet_name="网络错误（逐处）", index=False)
        S.to_excel(w, sheet_name="未挂子网的系统", index=False)
        if len(iso):
            iso.to_excel(w, sheet_name="孤立系统（离别的系统多远）", index=False)
        if len(AR):
            AR.to_excel(w, sheet_name="属性规则错误", index=False)
        summ_done.to_excel(w, sheet_name="治理动作汇总", index=False)
        Dn.to_excel(w, sheet_name="治理动作记录（逐条）", index=False)
    gp = out / f"全市遗留问题_第{N}轮.gpkg"
    if gp.exists():
        gp.unlink()
    for name, df in (("待处理", T), ("治理动作", Dn)):
        d = df.dropna(subset=["x", "y"])
        gpd.GeoDataFrame(d, geometry=gpd.points_from_xy(d.x, d.y), crs=32640).to_file(gp, layer=name, driver="GPKG")
    gpd.GeoDataFrame(dgg.drop(columns="geometry"), geometry=gpd.points_from_xy(dgg.x, dgg.y), crs=32640).to_file(gp, layer="网络错误", driver="GPKG")
    gpd.GeoDataFrame(S, geometry=gpd.points_from_xy(S["代表点 x"], S["代表点 y"]), crs=32640).to_file(gp, layer="未挂子网的系统", driver="GPKG")
    print(json.dumps({k: v for k, v in led.items() if k not in ("rounds",)}, ensure_ascii=False, indent=1, default=str)[:4000])
    print("输出：", xl, gp)


if __name__ == "__main__":
    main()
