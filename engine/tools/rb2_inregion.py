# -*- coding: utf-8 -*-
"""29.RB2 各态「只在 29.RB2 范围内」的积水面积与深水面积（不含 500 m 缓冲带）。

为什么单列：计算域是 29.RB2 外扩 500 m，全域最大水深 1.085 m 出在西缘离边界 88 m 的缓冲带洼地里；
29.RB2 内也有一处约 1 m 深的封闭洼地（24.3137°N 54.6535°E）七态都灌满到 1.08 m——
所以面积只算区内，深水看 ≥0.8 m 面积，不看最大水深。

产物：out/pilot_RB2x_R61D2_inregion.json、console/data/flood2d_rb2/inregion.json
用法：python tools/rb2_inregion.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

STATES = ["L1", "L2", "L2A", "L2AB", "L2AB2", "L2B", "L2B2", "L2AC"]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    from shapely import contains_xy
    from shapely.ops import unary_union
    from shapely.validation import make_valid
    C0 = gpd.read_file(ROOT / "console" / "data" / "catchments_sw.geojson").to_crs(32640)
    reg = unary_union([make_valid(g).buffer(0) for g in C0[C0.label == "29.RB2"].geometry])
    out = {}
    for st in STATES:
        z = np.load(WORK / "c2d" / f"RB2x_{st}_2d.npz")
        h, xy, a = z["hmax"].astype(float), z["xy"].astype(float), z["area"].astype(float)
        inr = contains_xy(reg, xy[:, 0], xy[:, 1])
        out[st] = {"max_depth_in_region_m": round(float(h[inr].max()), 3),
                   "area_ge_0p30_in_region_ha": round(float(a[inr & (h >= 0.30)].sum()) / 1e4, 2),
                   "area_ge_0p10_in_region_ha": round(float(a[inr & (h >= 0.10)].sum()) / 1e4, 2),
                   "area_ge_0p80_in_region_ha": round(float(a[inr & (h >= 0.80)].sum()) / 1e4, 2)}
        print(st, out[st])
    doc = {"region": "29.RB2（不含 500 m 缓冲带）",
           "note": "最大水深只在 29.RB2 范围内取；区内一处约 1 m 深的封闭洼地七态都灌满，与方案无关，深水看 ≥0.8 m 面积",
           "states": out}
    f = ROOT / "out" / "pilot_RB2x_R61D2_inregion.json"
    f.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copy(f, ROOT / "console" / "data" / "flood2d_rb2" / "inregion.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
