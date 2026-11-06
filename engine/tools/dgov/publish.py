"""5 资产层：发布前过关口（标准 5.3 的阻断级规则），过了才发布版本化的模型就绪数据资产，附元数据、质量报告、清单哈希。

  python -m tools.dgov.publish --extent rda --state L1 --version v1 [--provisional]

关口（阻断级，全部为 0 才发布；没过就只出「候选」并列出原因）：
  G-01 每根重力管有管径、上下游管底、糙率             （Q-CO 必填）
  G-02 每个节点有井底；出水口有边界                    （Q-CO-24）
  G-03 每台泵有曲线或流量，且吸水端、出水端都在网里    （Q-CO-20、Q-LC-22）
  G-04 单泵流量在泵型范围内                            （Q-TA-30）
  G-05 子汇水区面积不重叠（重叠 < 1%）、都有出口节点   （Q-LC-30）
  G-06 管段两端的节点都在节点表里                      （Q-US-34 的前提：资产自身一致）
  G-09 资产编号唯一（管段、节点各自不重复；标准 Q-LC-41）
  G-08 Esri 雨水 Utility Network 校验：官方连通规则 0 违规、属性规则 0 错误；UN 追不到出水口的管 = 按编号连通分析无出口的管（两套一致）
       （tools/dgov/un.py 先跑；标准 v0.2 第 11 章、Q-LC-43）
--provisional：问题登记簿里还有「待人工核 / 待客户确认」时用，版本号加「-暂定」，元数据里列出未结问题数。
产物：out/dgov/asset/<extent>/<state>/<version>/：<extent>_<state>_<version>.gpkg、metadata.json、quality.md、MANIFEST.sha256；
      out/dgov/asset/catalog.json（资产目录，追加）。
"""
from __future__ import annotations

import argparse
from pathlib import Path
import datetime as dt
import json
import sys
from collections import Counter

import numpy as np
import pandas as pd

from . import STD_VERSION
from .common import OUT, lineage, load, raw_manifest, sha256, write_json


def gate(ext, N, L, P, C, un_report=None):
    G = {}
    grav = L[L.link_kind != "force"]
    G["G-01 重力管必填"] = grav[grav[["diameter_mm", "upelev", "downelev", "manning_n"]].isna().any(axis=1)].asset_id.tolist()
    G["G-02 节点井底 / 出水口边界"] = N[N.invert_elev.isna()].node_id.tolist() + N[(N.node_kind == "outfall") & N.boundary_type.isna()].node_id.tolist()
    ids = set(N.node_id)
    G["G-03 泵曲线与两端"] = P[(P.curve_id.isna() & P.flow_lps.isna()) | ~P.inlet_node.isin(ids) | ~P.outlet_node.isin(ids)].asset_id.tolist()
    G["G-04 单泵流量范围"] = P[P.flow_lps > 5000].asset_id.tolist()
    ov = 0.0
    if len(C):
        u = C.geometry.union_all().area
        ov = (C.geometry.area.sum() - u) / u
    G["G-05 子汇水区重叠 / 出口"] = (["重叠 %.2f%%" % (100 * ov)] if ov > 0.01 else []) + C[C.out_node_int.isna()].asset_id.tolist()
    G["G-06 管段两端节点存在"] = L[~L.from_node.isin(ids) | ~L.to_node.isin(ids)].asset_id.tolist()
    G["G-08 Utility Network 校验"] = un_gate(ext, N, L, un_report)
    G["G-09 资产编号唯一"] = L[L.asset_id.duplicated()].asset_id.tolist() + N[N.node_id.duplicated()].node_id.tolist()
    return G, ov


def un_gate(ext, N, L, un_report=None):
    """UN 报告：规则违规必须为 0；UN 追不到出水口的管必须和「按编号连通、所在连通块里没有出水口」的管完全一致。"""
    rp = Path(un_report) if un_report else OUT / "un" / f"{ext}_un_report.json"
    if not rp.exists():
        return ["没有 UN 校验报告（先跑 python -m tools.dgov.un）"]
    rep = json.loads(rp.read_text(encoding="utf-8"))
    bad = [f"规则违规 {rep['rule_errors']} 处"] if rep["rule_errors"] else []
    bad += [f"属性规则 {k} {v} 处" for k, v in rep.get("attribute_rule_errors", {}).items()]
    par = {}
    def root(x):
        while par.setdefault(x, x) != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for a, b in zip(L.from_node, L.to_node):
        par[root(a)] = root(b)
    has_out = {root(x) for x in N[N.node_kind == "outfall"].node_id}
    no_out = set(L[[root(a) not in has_out for a in L.from_node]].asset_id)
    un_no = set(rep["unreached_lines"])
    bad += [f"UN 追不到、编号分析能到出口：{x}" for x in sorted(un_no - no_out)]
    bad += [f"编号分析无出口、UN 能追到：{x}" for x in sorted(no_out - un_no)]
    return bad


def publish(ext, state, version, provisional=False, log=print, src=None, un_report=None, extra_layers=()):
    """src / un_report：方案包用（缺省是加工层产物和 L1 的 UN 报告）；extra_layers：方案包多出的层（蓄水节点、库容曲线、L2 问题）。"""
    import geopandas as gpd
    import pyogrio
    src = Path(src) if src else OUT / "proc" / f"{ext}.gpkg"
    N, L, P, C = (gpd.read_file(src, layer=k) for k in ("node", "link", "pump", "catchment"))
    G, ov = gate(ext, N, L, P, C, un_report)
    failed = {k: v for k, v in G.items() if v}
    I = pd.read_csv(OUT / "issues" / f"{ext}_issues.csv", encoding="utf-8-sig")
    open_iss = I[I.status.isin(["待人工核", "待客户确认"])]
    if len(open_iss) and not provisional:
        raise SystemExit(f"还有 {len(open_iss)} 条未结问题：要么先裁决，要么加 --provisional 发「暂定」版")
    tag = version + ("-暂定" if provisional else "")
    kind = "资产" if not failed else "候选（关口没过，不作为资产）"
    d = OUT / "asset" / ext / state / tag
    d.mkdir(parents=True, exist_ok=True)
    gp = d / f"{ext}_{state}_{tag}.gpkg"
    if gp.exists():
        gp.unlink()
    for k in ("node", "link", "pump", "pump_curve", "catchment", "process_log") + tuple(extra_layers):
        pyogrio.write_dataframe(pyogrio.read_dataframe(src, layer=k), gp, layer=k, driver="GPKG")
    # 置信度分布（标准第 6 章）、缺省 / 推断占比（Q-US-32）
    def conf_dist(df, fields):
        out = {}
        for f in fields:
            c = Counter(json.loads(x).get(f, "—") for x in df.conf)
            out[f] = dict(c)
        return out
    cd = {"link": conf_dist(L, ["diameter_mm", "upelev", "downelev", "manning_n"]), "node": conf_dist(N, ["rim_elev", "invert_elev"])}
    inferred = {f"{lay}.{f}": round(100 * (v.get("D", 0) + v.get("E", 0)) / sum(v.values()), 2) for lay, d_ in cd.items() for f, v in d_.items()}
    srcs = load("sources.yaml")["sources"]
    used = ["dmt_stormwater", "aecom_rda", "dtm_auh"]
    meta = {
        "title": f"{ext.upper()} 一二维模型就绪数据资产 · {state} · {tag}", "kind": kind, "created": dt.datetime.now().isoformat(timespec="seconds"),
        "standard": f"一二维建模数据标准 v{STD_VERSION}（docs/一二维建模数据标准_v0.2.md）",
        "state": {"L1": "现状永久设施：管网 + 现状泵站（客户定义的级别待确认，见 docs/客户确认_L1L2L3定义_20261021.md）"}.get(state, state),
        "extent": load("extents.yaml")["extents"][ext], "crs": "EPSG:32640", "vertical_datum": "NADD（DMT GDB 已减 100 m）",
        "sources": {s: {"title": srcs[s]["title"], "owner": srcs[s]["owner"], "registered": raw_manifest(s)["registered"],
                        "files": len(raw_manifest(s)["files"])} for s in used},
        "extra_inputs": {"makani_via_rda_catchments": "out/rda/catchments_composite.gpkg（不透水比例 f；tools/rda_catchments.py，用户 2026-10-20 同意）"},
        "counts": {"nodes": len(N), "links": len(L), "pumps": len(P), "catchments": len(C), "node_kinds": N.node_kind.value_counts().to_dict(),
                   "link_src": L.src.value_counts().to_dict()},
        "gate": {k: len(v) for k, v in G.items()}, "gate_failed": {k: v[:50] for k, v in failed.items()}, "catchment_overlap_pct": round(100 * ov, 3),
        "confidence": cd, "inferred_or_default_pct": inferred,
        "open_issues": {"total": len(open_iss), "by_status": open_iss.status.value_counts().to_dict(), "by_category": open_iss.category.value_counts().to_dict(),
                        "register": str(OUT / "issues" / f"{ext}_issues.csv")},
        "provisional_rule": "P-08：未结问题涉及的对象保留当前值、不删不改，对象上带问题编号（issues 列）" if provisional else None,
        "lineage": str(OUT / "lineage" / "lineage.jsonl"),
    }
    write_json(d / "metadata.json", meta)
    q = [f"# {meta['title']}", "", f"**{kind}**。标准：{meta['standard']}。", "", "## 关口（阻断级）", "", "| 关口 | 不通过数 |", "|---|---|"]
    q += [f"| {k} | {len(v)} |" for k, v in G.items()]
    q += ["", "## 规模", "", f"节点 {len(N)}、管段 {len(L)}、泵 {len(P)}、子汇水区 {len(C)}（重叠 {100 * ov:.2f}%）。", "",
          "## 推断或缺省值占比（Q-US-32）", "", "| 字段 | D + E 占比 |", "|---|---|"] + [f"| {k} | {v}% |" for k, v in inferred.items()]
    q += ["", "## 未结问题", "", f"共 {len(open_iss)} 条：" + "，".join(f"{k} {v}" for k, v in open_iss.status.value_counts().items()) + "。逐条见问题登记簿。"]
    (d / "quality.md").write_text("\n".join(q) + "\n", encoding="utf-8")
    man = [f"{sha256(p)}  {p.name}" for p in sorted(d.iterdir()) if p.is_file() and p.name != "MANIFEST.sha256"]
    (d / "MANIFEST.sha256").write_text("\n".join(man) + "\n", encoding="utf-8")
    cat = OUT / "asset" / "catalog.json"
    C_ = json.loads(cat.read_text(encoding="utf-8")) if cat.exists() else []
    C_ = [x for x in C_ if x.get("path") != str(d)] + [{"title": meta["title"], "kind": kind, "path": str(d), "created": meta["created"],
                                                      "gpkg_sha256": sha256(gp), "open_issues": len(open_iss), "gate_failed": {k: len(v) for k, v in failed.items()}}]
    write_json(cat, C_)
    lineage("publish", [src, OUT / "issues" / f"{ext}_issues.csv"], [gp, d / "metadata.json", d / "quality.md", d / "MANIFEST.sha256"],
            {"extent": ext, "state": state, "version": tag}, notes=f"{kind}；关口不通过：{ {k: len(v) for k, v in failed.items()} }；未结问题 {len(open_iss)}")
    log(f"  {kind}：关口 " + "，".join(f"{k} {len(v)}" for k, v in G.items()))
    log(f"→ {d}")
    return d, meta


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.publish")
    ap.add_argument("--extent", required=True)
    ap.add_argument("--state", default="L1")
    ap.add_argument("--version", required=True)
    ap.add_argument("--provisional", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    publish(a.extent, a.state, a.version, a.provisional)


if __name__ == "__main__":
    main()
