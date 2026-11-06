# -*- coding: utf-8 -*-
"""L2+A+B：把 L2+A 的方案当**既成事实**，在它之上再加本系统的设施。

L2+A 是谁：Mussafah 是 Parsons 的方案（有报告）；RB2x 是 RB_02 模型里的 PO 塘与配套泵，**设计方未确认**
（Parsons 的 4 份报告里没有 RB2，只是编号与泵型像 Parsons），对外叫「PO 塘方案（来源待确认）」。

和 L2+B 的区别：L2+B 是从 L1 出发的**并列方案**（箱涵按 L1 的积水聚类）；L2+A+B 是**叠加**——
基准换成 L2+A（交付模型原件，塘 / 井 / 泵都在），只对 L2+A 做完之后**还在冒水**的井聚类选址。
回答的是「现有计划做完以后，还需要补什么、补在哪」。

在 build_l2p 的严格选址上加两条约束：
  · L2+A 设施占的地（塘、集水井，外扩 BOX_GAP_M）从可用地里扣掉；
  · L2+A 的泵取水的那几口井（新增管的上游端）不做箱涵的进水口——不和它的泵抢同一口井。
L2+A 设施按**交付模型原件**（2026-09-27 与用户确认）：Mussafah 的 STO-PO3 按模型的 1,200 m³，不是报告的 900 m³。

用法：
  python tools/l2ab.py Mussafah                # → $WORK/Mussafah_L2AB_R61D2.inp（及 _72h）
  python tools/l2ab.py RB2x [--vacant]         # → $WORK/RB2x_L2AB.inp / RB2x_L2AB2.inp + c2d/pilot_RB2x_L2AB*.json
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import sys

VAC = "--vacant" in sys.argv
if VAC:
    sys.argv.remove("--vacant"); os.environ["L2P_VACANT"] = "1"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "Mussafah"
os.environ.setdefault("L2P_BASE_TAG", "R61D2")
os.environ.setdefault("L2P_OUT_TAG", "R61D2v" if VAC else "R61D2")
os.environ.setdefault("L2P_EMPTY_H", "3")              # 2 h 雨峰在第 1.1 h，排空起判 = ceil(1.1) + 2
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
sys.stdout.reconfigure(encoding="utf-8")
import dig_pond3  # noqa: E402
import build_l2p as L  # noqa: E402
import export_facilities as EF  # noqa: E402
from swbrain import db, landuse as LU  # noqa: E402
from workdir import OUT, WORK  # noqa: E402

# 模型 → (L2+A 的一维文件, L1 的一维文件, 聚类参数（与该区 L2+B 相同，便于对比）, 只在哪个汇水区里聚类)
CASES = {
    "Mussafah": ("Mussafah_R61D2", "Mussafah_baseline_R61D2", (6, 250.0), None),
    "RB2x": ("RB2x_L2A", "RB2x_L1", (24, 150.0), "29.RB2"),
}


def parsons_objects(base_inp, a_inp):
    """L2+A 相对 L1 新增的蓄水节点（塘 / 井）的轮廓，和 L2+A 的泵取水的井。"""
    B, A = EF.parse_inp(base_inp), EF.parse_inp(a_inp)
    D = EF.diff(B, A)
    xy = {r[0]: (float(r[1]), float(r[2])) for r in A.get("COORDINATES", [])}
    C = EF.curves(A)
    new_nodes = {r[0] for r in D["STORAGE"]} | {r[0] for r in D["JUNCTIONS"]}
    stor = {}
    for r in D["STORAGE"]:
        if r[0] in xy:
            stor[r[0]] = (xy[r[0]], EF.storage_geom(r, C)["area_top_m2"])
    # 取水井：新增管 / 泵里「上游端是既有节点」的那一端（CO-6：CB4483 → W-4）
    feed = {r[1] for sec in ("CONDUITS", "PUMPS", "ORIFICES") for r in D[sec]
            if r[1] not in new_nodes and r[2] in new_nodes}
    # 泵的吸水端本身也算：RB2x 的 PO 泵从既有检查井直接抽，L2+A 里才把这些井补成湿井（rb02_model）
    feed |= {r[1] for r in D["PUMPS"]}
    return stor, feed


def main():
    base_a, base_1, (n, rad), region = CASES[MODEL]
    st = "L2AB2" if VAC else "L2AB"
    alias = MODEL + "A"                                   # build_l2p 读 $WORK/<名>_baseline_R61D2.inp
    for ext in (".inp", ".rpt"):
        shutil.copy(WORK / f"{base_a}{ext}", WORK / f"{alias}_baseline_R61D2{ext}")
    stor, feed = parsons_objects(WORK / f"{base_1}.inp", WORK / f"{base_a}.inp")
    print(f"{MODEL} · {st}：以 L2+A（{base_a}）为既成事实")
    print(f"  L2+A 蓄水设施 {len(stor)} 处（从可用地里扣掉）、取水井 {len(feed)} 口（不做箱涵进水口）：{sorted(feed)[:8]}")

    # 1) 聚类只看 L2+A 之后还在冒水的井；L2+A 的取水井剔掉；RB2x 只看 29.RB2 里的
    orig = dig_pond3.flooding_nodes
    keep = None
    if region:
        import geopandas as gpd
        from shapely.geometry import Point
        from shapely.ops import unary_union
        from shapely.validation import make_valid
        from dig_pond import read_sections, parse_coords
        C0 = gpd.read_file(HERE.parent / "console" / "data" / "catchments_sw.geojson").to_crs(32640)
        reg = unary_union([make_valid(g).buffer(0) for g in C0[C0.label == region].geometry])
        lines, idx = read_sections(WORK / f"{base_a}.inp")
        cxy = parse_coords(lines, idx)
        keep = lambda k: k in cxy and reg.contains(Point(cxy[k]))

    def residual(rpt):
        fl = orig(rpt)
        out = {k: v for k, v in fl.items() if k not in feed and (keep is None or keep(k))}
        print(f"  L2+A 之后仍冒水的井 {len(out):,} 口，{sum(out.values()):,.0f} m³（剔掉 L2+A 的取水井"
              f"{'、只留 ' + region + ' 内' if region else ''}）")
        return out
    dig_pond3.flooding_nodes = residual

    # 2) L2+A 设施占的地从两（三）张可用地图里扣掉
    orig_masks = LU.strict_land_masks

    def masks(cfg, bb, *a, **k):
        from rasterio.features import rasterize
        from rasterio.transform import from_origin
        from shapely.geometry import Point
        r = list(orig_masks(cfg, bb, *a, **k))
        x0, y1, res = r[-2]
        polys = [Point(p).buffer((ar ** 0.5) / 2 + L.BOX_GAP_M, cap_style=3) for p, ar in stor.values()]
        if polys:
            used = rasterize(polys, out_shape=r[0].shape, transform=from_origin(x0, y1, res, res),
                             fill=0, default_value=1, all_touched=True).astype(bool)
            for i in range(len(r) - 2):
                r[i] = r[i] & ~used
            print(f"  扣掉 L2+A 设施占地 {int(used.sum()) * res * res:,.0f} m²")
        return tuple(r)
    LU.strict_land_masks = masks

    L.PLAN[alias] = (n, rad)
    res = L.build(db.load_config(None), alias, "box")
    src = WORK / f"{alias}_L2box_{os.environ['L2P_OUT_TAG']}.inp"
    gj_src = OUT / f"{alias}.L2B.{os.environ['L2P_OUT_TAG']}.ponds.geojson"
    if MODEL == "Mussafah":
        dst = WORK / f"Mussafah_{st}_R61D2.inp"
        shutil.copy(src, dst)
        t = dst.read_text(encoding="utf-8")
        (WORK / f"Mussafah_{st}_R61D2_72h.inp").write_text(
            re.sub(r"^END_DATE\s+\S+", "END_DATE  01/04/2000", t, count=1, flags=re.M), encoding="utf-8")
        shutil.copy(gj_src, OUT / f"Mussafah.{st}.R61D2.ponds.geojson")
    else:
        shutil.copy(src, WORK / f"RB2x_{st}.inp")
        shutil.copy(gj_src, OUT / f"RB2x.{st}.ponds.geojson")
        C2D = WORK / "c2d"
        meta = json.loads((C2D / "pilot_RB2x_L2A.json").read_text(encoding="utf-8"))
        meta["state"] = st
        meta.setdefault("inferred", []).append(
            f"L2+A+B：PO 塘方案（交付模型原件，设计方未确认）视为既成事实，其上再加本系统箱涵 {res['n']} 座"
            f"（{'放宽：可借用未盖楼的已分配地块' if VAC else '严格用地选址'}）；箱涵是地下构筑物，不做地表进水")
        (C2D / f"pilot_RB2x_{st}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    json.dump({**res, "state": st, "base": base_a, "n_boxes": n, "radius_m": rad, "vacant": VAC,
               "l2a_storage": sorted(stor), "l2a_feed_nodes": sorted(feed)},
              open(OUT / f"{MODEL}_{st}_build.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"→ {MODEL}_{st}：箱涵 {res['n']}/{res['n_total']} 座、{res['got']:,.0f} m³（理想 {res['want']:,.0f} m³）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
