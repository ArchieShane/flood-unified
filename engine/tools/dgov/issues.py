"""问题登记簿：把要人工核、要问客户、按规则已处理的问题逐条记清楚；人工裁决留痕（只追加，不改历史）。

  python -m tools.dgov.issues build --extent rda                        汇总生成登记簿
  python -m tools.dgov.issues decide --extent rda --issue RDA-I-0012 --decision "…" --by 用户 [--status 已关闭]
  python -m tools.dgov.issues show --extent rda [--status 待人工核]

每条问题：编号、类别、标题、涉及对象、证据（自动算的数）、初判、建议做法、状态、依据（规则或裁决编号）、位置。
状态：已按规则处理 / 待人工核 / 待客户确认 / 已关闭。裁决写进 decisions.jsonl（谁、何时、对哪条、决定什么），
build 重新生成时会把已有裁决合并回去，所以裁决不会丢。
产物：out/dgov/issues/<extent>_issues.gpkg（带位置，可在 ArcGIS / QGIS 里看）、_issues.csv、_issues.md、decisions.jsonl。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial import cKDTree

from .common import OUT, lineage, source

DIR = OUT / "issues"


def _mid(g):
    return g.interpolate(0.5, normalized=True)


def build(ext, log=print):
    import geopandas as gpd
    import pyogrio
    import rasterio
    I = []

    def add(cat, title, objs, evidence, assess, action, status, rule="", geom=None):
        I.append({"category": cat, "title": title, "objects": ";".join(objs) if isinstance(objs, (list, tuple)) else str(objs),
                  "evidence": evidence if isinstance(evidence, str) else json.dumps(evidence, ensure_ascii=False, default=str),
                  "assessment": assess, "action": action, "status": status, "basis": rule, "geometry": geom})
    ig = OUT / "int" / f"{ext}.gpkg"
    L = gpd.read_file(ig, layer="link")
    N = gpd.read_file(ig, layer="node")
    BL = gpd.read_file(OUT / "clean" / f"dmt_stormwater__{ext}.gpkg", layer="link")
    BN = gpd.read_file(OUT / "clean" / f"dmt_stormwater__{ext}.gpkg", layer="node")
    act, aban = BL[BL.active == 1], BL[BL.active == 0]
    # A 补收的主管 / 不补的（review 表）
    R = gpd.read_file(ig, layer="review")
    for r in R.itertuples():
        both = r.connected_ends == 2
        add("A 融合：DMT 有、AECOM 没收的管", f"{r.asset_id}（{r.decision}）", [r.asset_id],
            {"管径mm": r.diameter_mm, "长度m": round(r.length_geom, 1), "两端节点类": r.end_kinds, "接上管网的端数": r.connected_ends},
            "两端都接在管网上，像 AECOM 漏收的主管" if both else ("只有一端接上，可能是末端支管或 AECOM 有意不收" if r.connected_ends == 1 else "两端都没接上，像孤立管"),
            "看图确认：是主管就保留（标 B），不是就移到可选层", "已按规则处理" if both else "待人工核", "integrate_rda.yaml: trunk_candidate", r.geometry)
    # B 只有 AECOM 有的管
    ta = cKDTree(np.c_[_mid(act.geometry).x, _mid(act.geometry).y]) if len(act) else None
    tb = cKDTree(np.c_[_mid(aban.geometry).x, _mid(aban.geometry).y]) if len(aban) else None
    for r in L[L["flags"] == "DMT无对应"].itertuples():
        m = _mid(gpd.GeoSeries([r.geometry])).iloc[0]
        da, ja = ta.query([m.x, m.y])
        db = tb.query([m.x, m.y])[0] if tb is not None else np.inf
        near = act.iloc[ja]
        ev = {"管径mm": r.diameter_mm, "长度m": round(r.geometry.length, 1), "最近DMT在用管m": round(da, 1), "其管径": near.diameter_mm,
              "最近DMT废弃管m": round(db, 1) if np.isfinite(db) else None}
        if db < 3:
            a, act_, st = "DMT 标为废弃，AECOM 仍在用", "问客户：这根管还在不在用", "待客户确认"
        elif da < 3 and near.diameter_mm == r.diameter_mm:
            a, act_, st = "很可能和旁边的 DMT 管是同一根，端点位置差几米（两边节点不同）", "看图确认：是同一根就换成 DMT 的（标 B）", "待人工核"
        elif da < 10:
            a, act_, st = "附近有 DMT 管但管径或走向不同，可能是 AECOM 改过线", "看图确认", "待人工核"
        else:
            a, act_, st = "DMT 资产库里没有：AECOM 新加的（可能是新建工程或建模用的连接段）", "问客户：资产库是否漏登", "待客户确认"
        add("B 融合：AECOM 有、DMT 没有的管", r.asset_id, [r.asset_id], ev, a, act_, st, "integrate_rda.yaml: aecom_only", r.geometry)
    # C 管底冲突待核（按管段归并）
    D = pyogrio.read_dataframe(ig, layer="decisions")
    Li = L.set_index("asset_id")
    for aid, g in D[D.status == "待核"].groupby("asset_id"):
        geo = Li.geometry.get(aid)
        add("C 融合：两边值不一致、按证据也定不了", aid, [aid] + g.decision_id.tolist(),
            g[["decision_id", "field", "DMT", "AECOM", "node_invert", "node_rim", "dtm_ground", "evidence"]].to_dict("records"),
            "两个候选值都过不了物理检查（管底低于井底、管顶高于井盖或覆土不足）：问题多半在节点高程本身", "先核这一处的井盖、井底（见 D 类），再定管底",
            "待人工核", "resolve.py: R-CF-3 / R-CF-4", geo)
    # D 节点高程与 DTM：系统偏差 + 局部异常片
    with rasterio.open(next(p for p in source("dtm_auh")["abspath"].glob("*.TIF") if not p.name.startswith("._"))) as ds:
        BN = BN[BN.rim_elev.notna()].copy()
        g = np.array([v[0] for v in ds.sample(list(zip(BN.geometry.x, BN.geometry.y)))], float)
    g[np.abs(g) > 1e30] = np.nan
    BN["d"] = BN.rim_elev - g
    med = float(np.nanmedian(BN.d))
    add("D 节点高程与 DTM", "DMT 井盖整体比 DTM 地面低", ["全部节点"], {"节点数": int(BN.d.notna().sum()), "井盖−DTM 中位m": round(med, 2),
        "四分位m": [round(float(np.nanpercentile(BN.d, 25)), 2), round(float(np.nanpercentile(BN.d, 75)), 2)]},
        "整体偏差约 0.4 m：可能是两套数据高程基准不同，也可能是 DTM 测的是路面、井盖低于路面", "问客户：资产库高程与 DTM 的基准关系；确认前不改值，比对时扣掉这个偏差",
        "待客户确认", "dataqc E1", BN.geometry.union_all().centroid)
    H = BN[(BN.d - med).abs() > 1.5].copy()
    if len(H) > 1:
        H["cl"] = fcluster(linkage(np.c_[H.geometry.x, H.geometry.y], "single"), 150, "distance")
        for c, q in H.groupby("cl"):
            add("D 节点高程与 DTM", f"局部 {len(q)} 个节点井盖与地面差 {q.d.median() - med:+.1f} m（已扣整体偏差）", q.asset_id.tolist(),
                {"节点数": len(q), "类": q.node_kind.value_counts().to_dict(), "井盖−DTM 中位m": round(float(q.d.median()), 2), "范围": [round(float(q.d.min()), 2), round(float(q.d.max()), 2)]},
                "成片偏差、几乎都是雨水口：像这一批的高程录错（或基准不同）" if len(q) >= 3 else "个别点偏差：像单点录错",
                "看图核；确认是录错后用 DTM 地面推回井盖（标 D），井底按原深度平移", "待人工核", "dataqc E2", q.geometry.union_all().centroid)
    # E 融合后管网还剩的问题（dataqc）
    res = json.loads((OUT / "int" / f"{ext}_dataqc" / "result.json").read_text(encoding="utf-8"))["network"]["checks"]
    names = {"C7": "相交而未连接", "C8": "管段两端的节点编号找不到", "C12": "沿流向到不了出口", "D1": "逆坡", "D5": "管底低于井底", "D6": "管顶高于井盖", "B8": "重复节点", "A13": "管段编号重复"}
    for code, nm in names.items():
        c = res.get(code, {})
        if c.get("count"):
            ids = c.get("ids") or []
            geo = L[L.asset_id.isin([str(i).split("→")[0] for i in ids])].geometry
            add("E 融合后管网质检", f"{code} {nm}：{c['count']} 处", ids[:200], {"命中": c["count"], "总数": c.get("total")},
                "融合后仍在；部分是来源数据本身的问题（见 A–D 类）", "逐处看；能按规则修的进加工层，修不了的保留在这里", "待人工核", f"dataqc {code}",
                geo.union_all().centroid if len(geo) else None)
    new = N[N.node_id.str.startswith("NEW:")]
    if len(new):
        add("E 融合后管网质检", f"管端没有节点、新建了 {len(new)} 个节点（没有高程）", new.node_id.tolist(), {"个数": len(new)},
            "两边都没有这个点的节点", "加工层用相连管段的管底推井底、用 DTM 推井盖（标 D）", "已按规则处理", "integrate.py: 新建节点", new.geometry.union_all().centroid)
    # F 全局待客户确认（标准 13 章）
    for t, a in (("MOVABLE_PUMP 的 FLOW_RATE 单位", "推断 m³/h、置信度 D；用户也不确定"),
                 ("L1 / L2 / L3 定义（泵归哪级、L2 范围、提议的塘算不算）", "见 docs/客户确认_L1L2L3定义_20261021.md"),
                 ("出水口边界：是否按 WA-726-1 §4.8（+1.3 m NADD，带拍门）",
                  "AECOM 模型只在重力溢流出水口用固定 1.3 m + 潮闸，泵站压力管出口是自由出流；加工规则 P-05 已按此分类型设置（2026-10-21），仍请客户确认口径"),
                 ("RD-A 泵站流量：GDB 记录为 0，备注「350&80x2」", "模型用 AECOM 的泵曲线（零扬程 0.47 / 0.13 / 0.13 m³/s）")):
        add("F 待客户确认（全局）", t, ["—"], "", a, "随客户确认邮件发出", "待客户确认", "数据标准 v0.1 第 13 章", None)
    # G 二维地形（DTM 检查的深洼地：施工基坑、下穿通道等，在二维模型里会变成「吸水的坑」）。追加在最后，已有编号不变
    dq = OUT / "int" / f"{ext}_dtm_qc" / "result.json"
    if dq.exists():
        from shapely.geometry import Point
        for lo in json.loads(dq.read_text(encoding="utf-8"))["dtm"].get("lows", []):
            add("G 二维地形", f"DTM 深洼地 {lo['areaHa']} ha，最低 {lo['min']} m", [f"DTM@{lo['x']},{lo['y']}"],
                {"面积ha": lo["areaHa"], "最低m": lo["min"], "格数": lo["cells"], "范围": lo["bbox"]},
                "四周陡坎的深坑：常见是施工基坑、地下室开挖或下穿通道。是临时基坑的，二维模型里会当成吸水的坑",
                "对照建筑和道路底图定性；临时基坑按周边地面填平（模型地形改动，记来源和理由），下穿通道保留并列为重点积水点",
                "待人工核", "dataqc R（深洼地）", Point(lo["x"], lo["y"]))
    G = gpd.GeoDataFrame(I, geometry="geometry", crs=32640)
    G.insert(0, "issue_id", [f"{ext.upper()}-I-{k:04d}" for k in range(1, len(G) + 1)])
    # 合并已有裁决
    dec = DIR / "decisions.jsonl"
    if dec.exists():
        for ln in dec.read_text(encoding="utf-8").splitlines():
            d = json.loads(ln)
            if d.get("extent") != ext:
                continue
            m = G.issue_id == d["issue"]
            G.loc[m, "status"] = d.get("status", "已关闭")
            G.loc[m, "basis"] = G.loc[m, "basis"] + f" | 裁决 {d['time']} {d['by']}：{d['decision']}"
    DIR.mkdir(parents=True, exist_ok=True)
    gp = DIR / f"{ext}_issues.gpkg"
    if gp.exists():
        gp.unlink()
    G[G.geometry.notna()].to_file(gp, layer="issues", driver="GPKG")
    G.drop(columns="geometry").to_csv(DIR / f"{ext}_issues.csv", index=False, encoding="utf-8-sig")
    md = [f"# {ext.upper()} 问题登记簿", "", f"生成：{dt.datetime.now().isoformat(timespec='seconds')}。共 {len(G)} 条。", "",
          "| 类别 | 已按规则处理 | 待人工核 | 待客户确认 | 已关闭 |", "|---|---|---|---|---|"]
    ct = pd.crosstab(G.category, G.status)
    for c, r in ct.iterrows():
        md.append(f"| {c} | {r.get('已按规则处理', 0)} | {r.get('待人工核', 0)} | {r.get('待客户确认', 0)} | {r.get('已关闭', 0)} |")
    for c, g in G.groupby("category"):
        md += ["", f"## {c}", "", "| 编号 | 标题 | 初判 | 建议 | 状态 |", "|---|---|---|---|---|"]
        md += [f"| {r.issue_id} | {r.title} | {r.assessment} | {r.action} | {r.status} |" for r in g.itertuples()]
    (DIR / f"{ext}_issues.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    lineage("issues.build", [ig, OUT / "clean" / f"dmt_stormwater__{ext}.gpkg", OUT / "int" / f"{ext}_dataqc" / "result.json"],
            [gp, DIR / f"{ext}_issues.csv", DIR / f"{ext}_issues.md"], {"extent": ext}, notes=ct.to_json(force_ascii=False))
    log(ct.to_string())
    log(f"→ {DIR / (ext + '_issues.md')}")
    return G


def decide(ext, issue, decision, by, status="已关闭"):
    DIR.mkdir(parents=True, exist_ok=True)
    rec = {"time": dt.datetime.now().isoformat(timespec="seconds"), "extent": ext, "issue": issue, "decision": decision, "by": by, "status": status}
    with open(DIR / "decisions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    lineage("issues.decide", [], [DIR / "decisions.jsonl"], rec)
    print("已记录：", rec)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.issues")
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("--extent", required=True)
    d = sp.add_parser("decide")
    for k in ("--extent", "--issue", "--decision", "--by"):
        d.add_argument(k, required=True)
    d.add_argument("--status", default="已关闭")
    s = sp.add_parser("show")
    s.add_argument("--extent", required=True)
    s.add_argument("--status")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if a.cmd == "build":
        build(a.extent)
    elif a.cmd == "decide":
        decide(a.extent, a.issue, a.decision, a.by, a.status)
    else:
        G = pd.read_csv(DIR / f"{a.extent}_issues.csv", encoding="utf-8-sig")
        if a.status:
            G = G[G.status == a.status]
        print(G[["issue_id", "category", "title", "status"]].to_string(index=False))


if __name__ == "__main__":
    main()
