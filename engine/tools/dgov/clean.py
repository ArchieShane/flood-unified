"""2 清洗层：对一份标准化结果跑质量规则，标出问题，做少量有明确规则的处理。不补缺、不合并（那是融合层、加工层的事）。

  python -m tools.dgov.clean --source dmt_stormwater --extent rda [--dtm <原始 DTM>]

规则：
  - 管网 A–F 组：交给 tools/dataqc（先把标准结构转成 dataqc 认的列名，dataqc 本身不改）；给了 --dtm 时连 E 组一起做；
  - 标准 5.3 节里能在单一来源上判的：Q-CO-20（泵要有曲线或流量）、Q-TA-30（单泵流量范围）、Q-US-32（缺省值占比，本层为 0）；
  - 状态处理：STATUS 是 Abandoned / Expired / Closed / Removed 的、或管道种类是 abandoned 的，标 active = 0（不删，留在资产里可查）。
产物：out/dgov/clean/<source>__<extent>.gpkg（标准结构 + active + issues 列）、.report.md、dataqc 的 result.json 和 report.html。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

from .common import OUT, ROOT, lineage, write_json

INACTIVE = {"abandoned", "expired", "closed", "removed"}
PUMP_MAX_LPS = {"movable": 500.0, "station": 5000.0, "model": 5000.0}     # 标准 Q-TA-30（待客户确认）
NODE_TABLE = {"manhole": "manhole", "junction_chamber": "manhole", "junction": "junction", "interceptor": "interceptor",
              "catchbasin": "catchbasin", "inlet": "inlet", "capped_end": "capped_end", "outfall": "outfall", "wetwell": "tank_wetwell"}


DATUM = 100.0     # 交给 dataqc 时高程加回 100 m：dataqc 按 DMT GDB 的习惯把 0 当缺失，而 NADD 0 m 在沿海是真值；E 组比 DTM 时 dataqc 自己估基准差


def _s(x):
    """文本列：NaN → None（dataqc 要么字符串要么空）"""
    x = x.astype(object)
    return x.where(x.notna(), None)


def to_dataqc(std_gpkg, dst):
    """标准结构 → dataqc 认的 GeoPackage：管段 pipeline / pressure_pipe，节点按种类分表（表名决定 dataqc 认的节点种类）。
    编号用来源里的原始编号（管段两端的 asset_before / asset_after 也是原始编号），这样 dataqc 能查出来源自身的编号问题。"""
    import geopandas as gpd
    import pyogrio
    if dst.exists():
        dst.unlink()
    L = gpd.read_file(std_gpkg, layer="link")
    L = L[L["active"] == 1] if "active" in L.columns else L
    for name, sel in (("pipeline", L.link_kind != "force"), ("pressure_pipe", L.link_kind == "force")):
        g = L[sel]
        if len(g):
            out = gpd.GeoDataFrame({"unitid": g.asset_id, "asset_before": _s(g.from_src), "asset_after": _s(g.to_src), "pipe_diameter": g.diameter_mm,
                                    "invert_level_upstream": g.upelev + DATUM, "invert_level_downstream": g.downelev + DATUM, "pipe_length": g.length_attr,
                                    "pipe_material": _s(g.material) if "material" in g.columns else None, "status": "Active",
                                    "pipe_type": np.where(g.link_kind == "perforated", "Perforated", "Solid")}, geometry=g.geometry, crs=g.crs)
            out.to_file(dst, layer=name, driver="GPKG")
    N = gpd.read_file(std_gpkg, layer="node")
    N = N[N["active"] == 1] if "active" in N.columns else N
    # 节点编号：dataqc 按 asset_before / asset_after 找节点，所以节点表的 unitid 用来源里的原始编号
    for k, g in N.groupby("node_kind"):
        gpd.GeoDataFrame({"unitid": _s(g.src_id), "asset_id": g.asset_id, "invert_level": g.invert_elev + DATUM, "cover_level": g.rim_elev + DATUM},
                         geometry=g.geometry, crs=g.crs).to_file(dst, layer=NODE_TABLE.get(k, k), driver="GPKG")
    try:
        P = gpd.read_file(std_gpkg, layer="pump")
        if len(P):
            gpd.GeoDataFrame({"unitid": P.src_id, "asset_id": P.asset_id}, geometry=P.geometry, crs=P.crs).to_file(dst, layer="pump", driver="GPKG")
    except Exception:
        pass
    return dst


def clean(sid, ext, dtm=None, log=print):
    import geopandas as gpd
    import pyogrio
    src = OUT / "std" / f"{sid}__{ext}.gpkg"
    dst = OUT / "clean" / f"{sid}__{ext}.gpkg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    layers = [x[0] for x in pyogrio.list_layers(src)]
    issues = defaultdict(list)          # asset_id → [规则]
    R = {"source": sid, "extent": ext, "inactive": {}, "rules": {}}
    G = {}
    for lay in layers:
        if lay == "pump_curve":
            continue
        g = gpd.read_file(src, layer=lay)
        st = g["status_src"].astype(str).str.lower() if "status_src" in g.columns else pd.Series([""] * len(g))
        inact = st.isin(INACTIVE)
        if lay == "link":
            inact |= g["link_kind"] == "abandoned"
        g["active"] = (~inact).astype(int)
        R["inactive"][lay] = int(inact.sum())
        G[lay] = g
    # 单一来源规则（标准 5.3）
    if "pump" in G:
        P = G["pump"]
        has_curve = P["curve_id"].notna() if "curve_id" in P.columns else pd.Series(False, index=P.index)
        has_flow = P["flow_lps"].notna() if "flow_lps" in P.columns else pd.Series(False, index=P.index)
        bad20 = P[(P.active == 1) & ~has_curve & ~has_flow]
        R["rules"]["Q-CO-20 泵要有曲线或流量"] = {"count": len(bad20), "ids": bad20.asset_id.tolist(),
                                                "note": "；".join(f"{a}：备注「{c}」" for a, c in zip(bad20.asset_id, bad20.get("comments_src", [""] * len(bad20))))}
        lim = P["pump_kind"].map(PUMP_MAX_LPS).fillna(5000.0)
        bad30 = P[has_flow & (P["flow_lps"] > lim)]
        R["rules"]["Q-TA-30 单泵流量超出泵型范围"] = {"count": len(bad30), "ids": bad30.asset_id.tolist()}
        for a in bad20.asset_id:
            issues[a].append("Q-CO-20")
        for a in bad30.asset_id:
            issues[a].append("Q-TA-30")
    R["rules"]["Q-US-32 缺省值占比"] = {"count": 0, "note": "标准化层不补缺省值；加工层补的值会在这里统计"}
    # 先写一份（带 active），给 dataqc 转接用
    for lay, g in G.items():
        g.to_file(dst, layer=lay, driver="GPKG")
    if "pump_curve" in layers:
        pyogrio.write_dataframe(pyogrio.read_dataframe(src, layer="pump_curve"), dst, layer="pump_curve", driver="GPKG")
    # dataqc
    qdir = OUT / "clean" / f"{sid}__{ext}_dataqc"
    qdir.mkdir(parents=True, exist_ok=True)
    qin = to_dataqc(dst, qdir / "input.gpkg")
    cmd = [sys.executable, "-m", "tools.dataqc", "--pipes", str(qin), "--out", str(qdir), "--gpkg", "--title", f"{sid} · {ext} · 清洗层质检"]
    if dtm:
        cmd += ["--dtm", str(dtm), "--dtm-scope", "network"]
    log("  dataqc：", " ".join(cmd[3:5]))
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        log(r.stdout[-2000:], r.stderr[-2000:])
        raise SystemExit("dataqc 失败")
    res = json.loads((qdir / "result.json").read_text(encoding="utf-8"))
    checks = res["network"]["checks"]
    texts = json.loads((ROOT / "tools" / "dataqc" / "texts_zh.json").read_text(encoding="utf-8"))
    # dataqc 的编号：管段是 unitid（= asset_id），节点是原始编号 → 换回 asset_id
    n2a = {}
    for g in (G.get("node"),):
        if g is not None:
            n2a.update(dict(zip(g.src_id, g.asset_id)))
    for code, c in checks.items():
        ids = c.get("ids") or []
        if c.get("count"):
            name = texts.get(code, {}).get("name") if isinstance(texts.get(code), dict) else texts.get(code)
            R["rules"][f"{code} {name or ''}".strip()] = {"count": c["count"], "total": c.get("total"), "notDone": c.get("notDone")}
            for i in ids:
                issues[n2a.get(i, i)].append(code)
    # issues 列写回
    for lay, g in G.items():
        g["issues"] = [",".join(sorted(set(issues.get(a, [])))) for a in g.asset_id]
        g.to_file(dst, layer=lay, driver="GPKG")
    R["dataqc"] = {"report": str(qdir / "report.html"), "result": str(qdir / "result.json")}
    write_json(dst.with_suffix(".report.json"), R)
    md = [f"# 清洗层质检：{sid} · {ext}", "", f"- 标记为不在用（active = 0）：" + "，".join(f"{k} {v}" for k, v in R["inactive"].items()),
          f"- dataqc 报告：`{R['dataqc']['report']}`", "", "| 规则 | 命中 | 说明 |", "|---|---|---|"]
    for k, v in R["rules"].items():
        md.append(f"| {k} | {v.get('count')}{' / ' + str(v['total']) if v.get('total') else ''} | {(v.get('note') or ('没做：' + v['notDone'] if v.get('notDone') else ''))[:200]} |")
    dst.with_suffix(".report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    lineage("clean", [src], [dst, dst.with_suffix(".report.json"), qdir / "result.json"], {"source": sid, "extent": ext, "dtm": str(dtm) if dtm else None})
    log(f"→ {dst}")
    return dst, R


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.clean")
    ap.add_argument("--source", required=True)
    ap.add_argument("--extent", required=True)
    ap.add_argument("--dtm")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    clean(a.source, a.extent, a.dtm)


if __name__ == "__main__":
    main()
