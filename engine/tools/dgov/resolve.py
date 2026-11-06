"""融合层的冲突裁决：两边都有值但不一致的，按证据逐处裁决。每一处都记录：证据、规则、结果、状态（可追踪）。

  python -m tools.dgov.resolve --extent rda

管底冲突（upelev / downelev）——用户 2026-10-21 定：有 DTM 后按物理合理性取，过程要可记录、可追踪：
  对两个候选值各查三条（都用融合后的节点和 DTM）：
    P1 管底不低于所接节点的井底（容差 5 cm）                      ← dataqc D5
    P2 管顶（管底 + 管径）不高于所接节点的井盖（容差 5 cm）        ← dataqc D6
    P3 管顶到 DTM 地面的覆土 ≥ 0.3 m                               ← dataqc D7（改用 DTM 地面）
  R-CF-1 一个候选三条全过、另一个至少一条不过 → 取全过的那个
  R-CF-2 两个都全过 → 取 DMT（标准 2.2 的来源顺序）
  R-CF-3 两个都有不过 → 取 DMT，状态「待核」，进问题登记
  裁决后，管段两端的管底再查一次坡向：逆坡 > 1 cm 的进问题登记
管径冲突 R-CF-4：保留 DMT，进问题登记（DMT 160 mm 小于设计手册推荐的 300 mm，AECOM 可能按最小管径改过）
管长冲突 R-CF-5：保留 DMT 属性管长（模型用几何长度），只记录
产物：out/dgov/int/<extent>.gpkg 里更新 link（值、conf、resolved 列）、新增 decisions 表；out/dgov/int/<extent>_decisions.csv；血缘事件。
"""
from __future__ import annotations

import argparse
import json
import re
import sys

import numpy as np
import pandas as pd

from .common import OUT, lineage, source

TOL, COVER_MIN = 0.05, 0.3


def sector(asset_id):
    m = re.search(r"SW_([A-Z0-9]+)_([A-Z0-9_]+?)_PI_", asset_id or "")
    return f"{m.group(1)}_{m.group(2)}" if m else ""


def resolve(ext, log=print):
    import geopandas as gpd
    import pyogrio
    import rasterio
    dst = OUT / "int" / f"{ext}.gpkg"
    L = gpd.read_file(dst, layer="link")
    N = gpd.read_file(dst, layer="node").set_index("node_id")
    CF = pyogrio.read_dataframe(dst, layer="conflicts")
    dtm_dir = source("dtm_auh")["abspath"]
    dtm = next(p for p in dtm_dir.glob("*.TIF") if not p.name.startswith("._"))
    with rasterio.open(dtm) as ds:
        nd = ds.nodata
        g = [v[0] for v in ds.sample(list(zip(N.geometry.x, N.geometry.y)))]
    N["dtm_ground"] = [None if (v is None or v == nd or abs(v) > 1e30) else float(v) for v in g]
    Li = L.set_index("asset_id")
    rows = []
    for k, c in enumerate(CF.itertuples(), 1):
        did = f"CF-{ext}-{k:04d}"
        base = {"decision_id": did, "asset_id": c.asset_id, "field": c.field, "DMT": c.DMT, "AECOM": c.AECOM, "sector": sector(c.asset_id)}
        if c.field in ("upelev", "downelev"):
            lk = Li.loc[c.asset_id]
            nid = lk["from_node"] if c.field == "upelev" else lk["to_node"]
            n = N.loc[nid]
            D = float(lk["diameter_mm"]) / 1000 if pd.notna(lk["diameter_mm"]) else 0.0
            ev = {}
            for s in ("DMT", "AECOM"):
                v = getattr(c, s)
                p1 = None if pd.isna(n.invert_elev) else v >= n.invert_elev - TOL
                p2 = None if pd.isna(n.rim_elev) else v + D <= n.rim_elev + TOL
                p3 = None if n.dtm_ground is None or pd.isna(n.dtm_ground) else (n.dtm_ground - (v + D)) >= COVER_MIN
                p1, p2, p3 = (None if x is None else bool(x) for x in (p1, p2, p3))      # numpy 的 bool 转成 Python 的，才能写 JSON
                ev[s] = {"P1": p1, "P2": p2, "P3": p3, "ok": all(x is not False for x in (p1, p2, p3)) and any(x is not None for x in (p1, p2, p3))}
            if ev["DMT"]["ok"] and not ev["AECOM"]["ok"]:
                rule, chosen, status = "R-CF-1", "DMT", "已裁决"
            elif ev["AECOM"]["ok"] and not ev["DMT"]["ok"]:
                rule, chosen, status = "R-CF-1", "AECOM", "已裁决"
            elif ev["DMT"]["ok"] and ev["AECOM"]["ok"]:
                rule, chosen, status = "R-CF-2", "DMT", "已裁决"
            else:
                rule, chosen, status = "R-CF-3", "DMT", "待核"
            base.update(node_id=nid, node_invert=n.invert_elev, node_rim=n.rim_elev, dtm_ground=n.dtm_ground, diameter_m=D,
                        evidence=json.dumps(ev, ensure_ascii=False), rule=rule, chosen=chosen, status=status)
        elif c.field == "diameter_mm":
            base.update(rule="R-CF-4", chosen="DMT", status="待核", evidence="DMT 160 mm 小于设计手册推荐的 300 mm；AECOM 可能按最小管径改过")
        else:
            base.update(rule="R-CF-5", chosen="DMT", status="已裁决", evidence="属性管长不进模型（用几何长度），只记录")
        base["chosen_value"] = base["DMT"] if base["chosen"] == "DMT" else base["AECOM"]
        rows.append(base)
    Dd = pd.DataFrame(rows)
    # 应用到 link：值、置信度、resolved
    L["resolved"] = "{}"
    for i, r in L.iterrows():
        sub = Dd[Dd.asset_id == r.asset_id]
        if not len(sub):
            continue
        conf, res = json.loads(r.conf), {}
        for d in sub.itertuples():
            if d.field in ("upelev", "downelev") and d.chosen == "AECOM":
                L.at[i, d.field] = d.chosen_value
                conf[d.field] = "C"
            res[d.field] = d.decision_id
        L.at[i, "conf"] = json.dumps(conf, ensure_ascii=False)
        L.at[i, "resolved"] = json.dumps(res, ensure_ascii=False)
    # 裁决后坡向
    adverse = L[(L.link_kind != "force") & L.upelev.notna() & L.downelev.notna() & (L.downelev > L.upelev + 0.01)]
    touched = set(Dd.asset_id)
    Dd["adverse_after"] = Dd.asset_id.isin(set(adverse.asset_id))
    L.to_file(dst, layer="link", driver="GPKG")
    pyogrio.write_dataframe(Dd, dst, layer="decisions", driver="GPKG")
    csv = OUT / "int" / f"{ext}_decisions.csv"
    Dd.to_csv(csv, index=False, encoding="utf-8-sig")
    summ = {"decisions": len(Dd), "by_rule": Dd.rule.value_counts().to_dict(), "by_status": Dd.status.value_counts().to_dict(),
            "chosen": Dd.chosen.value_counts().to_dict(), "adverse_after_in_decided": int(Dd.loc[Dd.adverse_after, "asset_id"].nunique()),
            "adverse_total": len(adverse), "adverse_touched": len(set(adverse.asset_id) & touched),
            "by_sector": Dd[Dd.field.isin(["upelev", "downelev"])].groupby(["sector", "chosen"]).size().unstack(fill_value=0).to_dict("index")}
    lineage("resolve", [dst, dtm], [dst, csv], {"extent": ext, "rules": ["R-CF-1", "R-CF-2", "R-CF-3", "R-CF-4", "R-CF-5"], "tol": TOL, "cover_min": COVER_MIN},
            notes=json.dumps({k: v for k, v in summ.items() if k != "by_sector"}, ensure_ascii=False))
    log(json.dumps(summ, ensure_ascii=False, indent=1, default=str))
    return Dd, summ


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.resolve")
    ap.add_argument("--extent", required=True)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    resolve(a.extent)


if __name__ == "__main__":
    main()
