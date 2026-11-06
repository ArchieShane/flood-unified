# -*- coding: utf-8 -*-
"""把「我到底算的是哪一块」导成一个独立图层，杜绝把 Mussafah_00 看成整个穆萨法。

为什么要单独一层：
  穆萨法片区有 8 个编号分区，合计约 5,260 ha。
  我做的一二维耦合只覆盖其中的 **Mussafah_00，148.8 ha**，占 2.8%。
  而控制台原有的两层都不解决这个歧义 ——
    · catchments_sw 有 Mussafah_00，但它只是 39 个同样样式的多边形之一；
    · model_areas 有 Mussafah-01/02/03/08/10/RD09，**偏偏没有 Mussafah_00**。
  两层叠出来，看的人只会得到「穆萨法都算过了」的错觉。

这一层把三件事分开画，属性里写死角色：
  role=modelled   本次真正跑了一二维的汇水区（Mussafah_00）
  role=sibling    同属穆萨法、**本次没跑**的兄弟分区（画成灰/虚线做对照）
  role=domain2d   二维计算域的外框 —— 它比汇水区大得多，
                  「最大淹没面积」的分母是它，不是汇水区，这点必须在图上看得见。

用法：python tools/export_scope.py
产物：console/data/scope.geojson
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "console" / "data"
SRC = DATA / "catchments_sw.geojson"

# 本次一二维耦合真正覆盖的汇水区
MODELLED = "Mussafah_00"
# 同片区、本次没跑的兄弟分区（label 按 catchments_sw 里的原文）
SIBLINGS = ["Mussafah_01", "Mussafah_02", "Mussafah_03", "Mussafah_06",
            "Mussafah_08", "Mussafah_10", "Mussafah RD09"]

# 二维域：从耦合用的 DEM 读外框（EPSG:32640）
DEM = ("C:/Users/zn198/AppData/Local/Temp/claude/D----/"
       "f58f1df3-4927-423f-9aad-51c424e9800d/scratchpad/swmm/c2d/"
       "dem_Mussafah_L1_R61D2.tif")
NET_BUF_M = 100.0     # 节点缓冲半径，用来勾管网实际铺到哪
INP = pathlib.Path(
    "C:/Users/zn198/AppData/Local/Temp/claude/D----/"
    "f58f1df3-4927-423f-9aad-51c424e9800d/scratchpad/swmm/"
    "Mussafah_baseline_R61D2.inp")
RES_M = 15.0          # ANUGA 网格边长，不是 DEM 的 5 m
N_TRI = 258_332       # 本次 61 mm/2 h 的三角单元数


def poly_ha(geom) -> float:
    """按 UTM 40N 算多边形面积（ha）。客户台账的 tbl_ha 不可全信，见下。"""
    from shapely.geometry import shape
    from shapely.ops import transform as shp_tr
    import pyproj
    tr = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True).transform
    return shp_tr(tr, shape(geom)).area / 1e4


def box_4326(bounds, log=print):
    import pyproj
    x0, y0, x1, y1 = bounds
    tr = pyproj.Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return [[[round(v, 5) for v in tr.transform(x, y)] for x, y in ring]]


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    src = json.loads(SRC.read_text(encoding="utf-8"))
    by = {}
    for f in src["features"]:
        by.setdefault(f["properties"].get("label"), f)

    feats = []
    miss = []
    for label, role in [(MODELLED, "modelled")] + [(s, "sibling") for s in SIBLINGS]:
        f = by.get(label)
        if f is None:
            miss.append(label)
            continue
        p = f["properties"]
        # 面积以**几何量出来的**为准，台账 tbl_ha 并列。
        # Mussafah_00 这条两者差 1.81 倍（台账 148.76 / 几何 81.98），
        # 其余 7 个分区比值都在 0.86–1.02 —— 是这一条台账的问题，不是算法的。
        geo_ha = poly_ha(f["geometry"])
        tbl_ha = float(p.get("area_ha") or 0)
        r = (geo_ha / tbl_ha) if tbl_ha else None
        feats.append({
            "type": "Feature", "geometry": f["geometry"],
            "properties": {
                "name": label, "role": role,
                "area_ha": round(geo_ha, 1),          # 几何为准
                "tbl_ha": round(tbl_ha, 1),           # 客户台账
                "tbl_ratio": round(r, 2) if r else None,
                "area_note": (f"客户台账记 {tbl_ha:,.1f} ha，几何只有 {geo_ha:,.1f} ha"
                              f"（{r:.2f}×）——**以几何为准**"
                              if r and (r < 0.85 or r > 1.15) else None),
                "note": ("本次一二维耦合覆盖的就是这一块" if role == "modelled"
                         else "同属穆萨法，**本次没有建模**"),
            }})

    # 一维管网的实际铺设范围 —— 这是第三个「Mussafah_00 面积」，和前两个都不一样：
    #   汇水区多边形  82.0 ha   （客户台账写 148.8，几何只有 82.0）
    #   一维管网范围 235.0 ha   （833 个节点，只有 372 个落在汇水区内）
    #   二维计算域  1,453.1 ha  （淹没面积的分母是它）
    # 三个数差一个数量级，混着报会直接把结论读歪。
    try:
        from shapely.geometry import MultiPoint, Point, mapping
        from shapely.ops import transform as shp_tr
        import pyproj
        cur, xy = None, []
        for ln in INP.open(encoding="utf-8", errors="replace"):
            tt = ln.strip()
            if tt.startswith("["):
                cur = tt.strip("[]").upper(); continue
            if cur == "COORDINATES" and tt and not tt.startswith(";"):
                c = tt.split()
                if len(c) >= 3:
                    try: xy.append((float(c[1]), float(c[2])))
                    except ValueError: pass
        if xy:
            buf = MultiPoint([Point(*v) for v in xy]).buffer(NET_BUF_M)
            back = pyproj.Transformer.from_crs("EPSG:32640", "EPSG:4326",
                                               always_xy=True).transform
            feats.append({
                "type": "Feature",
                "geometry": mapping(shp_tr(back, buf.simplify(5.0))),
                "properties": {
                    "name": f"{MODELLED} 一维管网范围", "role": "net1d",
                    "area_ha": round(buf.area / 1e4, 1), "n_nodes": len(xy),
                    "note": (f"客户一维模型的 {len(xy):,} 个节点各取 {NET_BUF_M:g} m 缓冲的并集。"
                             f"管网铺得比汇水区多边形大 —— 新增设施是按**积水节点**布的，"
                             f"所以有几座落在汇水区多边形之外。"),
                }})
    except Exception as e:
        miss.append(f"一维管网范围（{e}）")

    # 二维域外框
    try:
        import rasterio
        d = rasterio.open(DEM)
        b = (d.bounds.left, d.bounds.bottom, d.bounds.right, d.bounds.top)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": box_4326(b)},
            "properties": {
                "name": f"{MODELLED} 二维计算域", "role": "domain2d",
                "area_ha": round((b[2] - b[0]) * (b[3] - b[1]) / 1e4, 1),
                "res_m": RES_M, "n_tri": N_TRI,
                "note": ("ANUGA 网格 {:g} m（源 DTM 5 m），{:,} 个三角单元。"
                         "「最大淹没面积」的分母是这个域，不是汇水区。"
                         .format(RES_M, N_TRI)),
            }})
    except Exception as e:
        miss.append(f"二维域（{e}）")

    mod = [x for x in feats if x["properties"]["role"] == "modelled"]
    sib = [x for x in feats if x["properties"]["role"] == "sibling"]
    a_mod = sum(x["properties"]["area_ha"] for x in mod)
    a_sib = sum(x["properties"]["area_ha"] for x in sib)
    fc = {"type": "FeatureCollection", "layer": "scope",
          "source": "catchments_sw（客户汇水区）+ 耦合 DEM 外框",
          "summary": {"modelled_ha": a_mod, "siblings_ha": a_sib,
                      "share_pct": round(a_mod / (a_mod + a_sib) * 100, 1)
                      if (a_mod + a_sib) else None},
          "features": feats}
    DATA.mkdir(parents=True, exist_ok=True)
    out = DATA / "scope.geojson"
    out.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")

    print(f"{'分区':<22}{'角色':<10}{'面积 ha':>10}")
    for x in feats:
        p = x["properties"]
        print(f"{p['name']:<22}{p['role']:<10}{p['area_ha']:>10,.1f}")
    print(f"\n本次建模 {a_mod:,.1f} ha，穆萨法其余 {a_sib:,.1f} ha，"
          f"占比 {fc['summary']['share_pct']}%")
    if miss:
        print(f"**没导出来**：{miss}")
    print(f"\n{len(feats)} 个要素 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
