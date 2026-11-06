# -*- coding: utf-8 -*-
"""给已建成的 L2 塘标上「它和汇水区、水力模型区的关系」，写回控制台图层。

用户问（2026-09-26）：「难道已经建的蓄水池都和汇水区无关么？」
物理上当然有关——每座塘都有自己的汇水范围。断的是数据：
  · 客户 catchment.catchment 那 39 个多边形是**建模研究的范围**（名字就是模型名），
    不是全市排水分区：合计 431 km²，Madinat Al Riyad（23 座里的 16 座）覆盖率 0%；
  · 塘挂在道路工程下（DP2289… / RD110、RD113），管线台账里没有一行上下游指向塘；
  · 客户水力模型里也没有：RB_02 的 42 座模型塘只和台账重合 1 座（见 out/rb02_ponds_vs_ledger.json）。
所以图上要把这层关系**直接画出来**，而不是靠转述。

每座塘写入：
  catch_in       落在哪些汇水区里，[{label, pct}]，pct 是塘占地落在区内的比例
  catch_near     离最近汇水区的名字与距离（米，0 表示相交）
  catch_rel      in（占地 ≥ 50% 在区内）/ part（部分在区内）/ edge（界外 ≤ 200 m）/ out
  in_model_area / nearest_model / dist_to_model_m   与 22 个水力模型区的关系（原先由临时脚本写，这里补成可复现）

几何先 make_valid：客户的汇水区与模型区都有自相交，直接算会抛 GEOSException（踩过）。

用法：python tools/pond_catchment.py
产物：console/data/ponds_l2.geojson、ponds_l2_pts.geojson（就地加字段）、out/l2_ponds_by_catchment.json
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "console" / "data"
EDGE_M = 200.0            # 界外这么近算「贴边」：客户汇水区边界本身就有几十米的误差


def _geom(g, to_utm):
    from shapely.geometry import shape
    from shapely.ops import transform
    from shapely.validation import make_valid
    return transform(to_utm, make_valid(shape(g))).buffer(0)


def relate(ponds, catch, models):
    """纯几何：塘 × 汇水区 × 模型区。输入都是 UTM 的 shapely 几何，便于测试。"""
    out = []
    for pid, g in ponds:
        inc = []
        for lab, c in catch:
            if g.intersects(c):
                pct = g.intersection(c).area / g.area * 100 if g.area else 0.0
                if pct >= 0.5:
                    inc.append({"label": lab, "pct": round(pct)})
        inc.sort(key=lambda x: -x["pct"])
        near = min(catch, key=lambda x: x[1].distance(g))
        dn = round(near[1].distance(g))
        best = inc[0]["pct"] if inc else 0
        rel = ("in" if best >= 50 else "part" if best > 0 else "edge" if dn <= EDGE_M else "out")
        inm = [m for m, mg in models if g.intersects(mg)]
        nm = min(models, key=lambda x: x[1].distance(g))
        out.append({"id": pid, "catch_in": inc, "catch_near": {"label": near[0], "dist_m": dn},
                    "catch_rel": rel, "in_model_area": bool(inm),
                    "nearest_model": inm[0] if inm else nm[0],
                    "dist_to_model_m": 0.0 if inm else round(nm[1].distance(g), 1)})
    return out


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    from pyproj import Transformer
    to_utm = Transformer.from_crs(4326, 32640, always_xy=True).transform
    P = json.loads((DATA / "ponds_l2.geojson").read_text(encoding="utf-8"))
    C = json.loads((DATA / "catchments_sw.geojson").read_text(encoding="utf-8"))
    M = json.loads((DATA / "model_areas.geojson").read_text(encoding="utf-8"))
    ponds = [(f["properties"]["unitid"], _geom(f["geometry"], to_utm)) for f in P["features"]]
    catch = [(f["properties"].get("label") or f["properties"].get("id"), _geom(f["geometry"], to_utm))
             for f in C["features"]]
    models = [(f["properties"]["model"], _geom(f["geometry"], to_utm)) for f in M["features"]]
    rel = {r["id"]: r for r in relate(ponds, catch, models)}

    for fn in ("ponds_l2.geojson", "ponds_l2_pts.geojson"):
        gj = json.loads((DATA / fn).read_text(encoding="utf-8"))
        for f in gj["features"]:
            r = rel.get(f["properties"].get("unitid"))
            if r:
                f["properties"].update({k: v for k, v in r.items() if k != "id"})
        gj["catch_note"] = ("客户 catchment.catchment 的 39 个多边形是建模研究范围，不是全市排水分区；"
                            "塘不在其中不等于没有汇水区")
        (DATA / fn).write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")

    vol = {f["properties"]["unitid"]: float(f["properties"].get("volume_m3") or 0) for f in P["features"]}
    rows = sorted(rel.values(), key=lambda r: ("in", "part", "edge", "out").index(r["catch_rel"]))
    (ROOT / "out" / "l2_ponds_by_catchment.json").write_text(
        json.dumps([{**r, "volume_m3": vol[r["id"]]} for r in rows], ensure_ascii=False, indent=1),
        encoding="utf-8")
    name = {"in": "在汇水区内", "part": "部分在区内", "edge": f"界外 ≤{EDGE_M:.0f} m", "out": "不在任何汇水区"}
    for k in ("in", "part", "edge", "out"):
        g = [r for r in rows if r["catch_rel"] == k]
        print(f"{name[k]:<10}{len(g):>3} 座  {sum(vol[r['id']] for r in g):>12,.0f} m³")
        for r in g:
            where = (", ".join(f"{x['label']} {x['pct']}%" for x in r["catch_in"]) or
                     f"最近 {r['catch_near']['label']} {r['catch_near']['dist_m']:,} m")
            print(f"    {r['id']:<24}{vol[r['id']]:>10,.0f} m³  {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
