# -*- coding: utf-8 -*-
"""把客户 22 个水力模型的覆盖范围导成控制台图层，好和已建成的 L2 塘叠着看。

**为什么需要这一层**：系统里原来只有客户 `catchment.catchment` 那 39 个汇水区边界，
没有「哪片地有水力模型」这一层。而 L2 的关键问题恰恰是
——194 万 m³ 已建成的塘，一座都不在这 22 个模型区内，最近的只差 13 m。
不把两层叠出来，这句话只能靠我转述。

范围怎么求：各模型自己的 Catchment 图层求并集。
**8 个大模型（RB3 / Shaliela / RB_02 / ADM_Line / Musaffah 09 / RB-01 /
Airport / SD Phase 1）的多边形有自相交**，直接 union 会抛 GEOSException ——
踩过一次，还因此得出过「0/23」的假结论。所以先 make_valid，再不行退到凸包，
**并把用了哪种方法写进属性**，图上点开就能看到，不留暗账。

用法：python tools/export_model_areas.py
产物：console/data/model_areas.geojson
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import sys
import zipfile

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

ZIP = r"D:/世界模型/OneDrive_1_9-26-2026.zip"
VSI = "/vsizip/" + ZIP
OUT = ROOT / "console" / "data"
CA_PATS = [r"ca", r"cat", r"catchment", r"catchments", r"catchment table"]
SIMPLIFY_M = 10.0


def footprint(geoms, log=print):
    """并集。无效几何先修，修不好退凸包，并返回用的是哪种方法。"""
    from shapely.ops import unary_union
    from shapely import make_valid

    try:
        return unary_union(geoms), "并集"
    except Exception:
        pass
    try:
        fixed = []
        for g in geoms:
            if g is None or g.is_empty:
                continue
            fixed.append(g if g.is_valid else make_valid(g))
        return unary_union(fixed), "修复后并集"
    except Exception:
        pass
    try:
        return unary_union([g.buffer(0) for g in geoms if g is not None]), "buffer(0) 并集"
    except Exception:
        from shapely.geometry import MultiPolygon
        pts = [p for g in geoms if g is not None for p in g.exterior.coords] \
            if all(hasattr(g, "exterior") for g in geoms if g is not None) else []
        from shapely.geometry import MultiPoint
        return MultiPoint(pts).convex_hull, "**凸包（并集修不好）**"


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    import pyogrio
    import pyproj
    from shapely.geometry import mapping
    from shapely.ops import transform as shp_transform

    zf = zipfile.ZipFile(ZIP)
    by = collections.defaultdict(list)
    for n in zf.namelist():
        if n.lower().endswith(".shp") and not n.startswith("__MACOSX"):
            by[n.split("/")[0]].append(n)

    to4326 = pyproj.Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True).transform
    feats = []
    print(f"{'模型区':<24}{'子汇水区':>9}{'面积 ha':>11}{'求范围的方法':<16}")
    for k in sorted(by):
        ca = None
        for p in CA_PATS:
            for n in by[k]:
                if re.fullmatch(p, n.split("/")[-1].rsplit(".", 1)[0].lower()):
                    ca = n; break
            if ca:
                break
        if not ca:
            continue
        d = pyogrio.read_dataframe(f"{VSI}/{ca}")
        g = [x for x in d.geometry if x is not None and not x.is_empty]
        if not g:
            continue
        poly, how = footprint(g)
        if poly is None or poly.is_empty:
            print(f"{k:<24}范围求不出，跳过"); continue
        area = poly.area / 1e4
        simp = poly.simplify(SIMPLIFY_M, preserve_topology=True)
        gj = mapping(shp_transform(to4326, simp))

        def rnd(c):
            if isinstance(c[0], (int, float)):
                return (round(c[0], 5), round(c[1], 5))
            return [rnd(x) for x in c]

        def round_geom(g):
            # 并集可能出 GeometryCollection —— 它没有 coordinates，只有 geometries
            if "coordinates" in g:
                g["coordinates"] = rnd(g["coordinates"])
            elif "geometries" in g:
                for sub in g["geometries"]:
                    round_geom(sub)
            return g

        gj = round_geom(gj)
        feats.append({"type": "Feature", "geometry": gj,
                      "properties": {"model": k, "n_sub": len(d),
                                     "area_ha": round(area, 1), "method": how}})
        print(f"{k:<24}{len(d):>9,}{area:>11,.0f}　{how:<16}")

    fc = {"type": "FeatureCollection", "layer": "model_areas",
          "source": "客户 22 个水力模型的 Catchment 图层并集（OneDrive_1_9-26-2026）",
          "features": feats}
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / "model_areas.geojson"
    txt = json.dumps(fc, ensure_ascii=False)
    f.write_text(txt, encoding="utf-8")
    print(f"\n{len(feats)} 个模型区　{len(txt) / 1048576:.1f} MB → {f}")
    print(f"合计 {sum(x['properties']['area_ha'] for x in feats):,.0f} ha")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
