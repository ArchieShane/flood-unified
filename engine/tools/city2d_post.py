# -*- coding: utf-8 -*-
"""全市一二维 L1 / L2 后处理：差值图（L2 − L1 全程最大水深）、按客户汇水区与按塘周边的分区统计。

产物：console/data/flood2d_city2d/diff.png、diff.json（范围与色标）；out/city2d_zones_R61D2.json
用法：python tools/city2d_post.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
OUT = ROOT / "console" / "data" / "flood2d_city2d"
TH = (0.10, 0.30, 0.80)
DIFF_MIN = 0.05       # |Δh| 小于 5 cm 不画、不计
DIFF_MAX = 0.50       # 色标上限


def render_diff(xy, dh, cell, path):
    """形心 Δh → 按格取绝对值最大的那个 → 投到 WGS84 → PNG（蓝 = 变浅，红 = 变深）。"""
    from PIL import Image
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    x0, y0 = float(xy[:, 0].min()), float(xy[:, 1].min())
    nx = int(np.ceil((xy[:, 0].max() - x0) / cell)) + 1
    ny = int(np.ceil((xy[:, 1].max() - y0) / cell)) + 1
    idx = np.clip(((xy[:, 1] - y0) / cell).astype(int), 0, ny - 1) * nx + np.clip(((xy[:, 0] - x0) / cell).astype(int), 0, nx - 1)
    v = np.where(np.abs(dh) >= DIFF_MIN, dh, 0.0).astype(np.float32)
    lo = np.zeros(nx * ny, np.float32); hi = np.zeros(nx * ny, np.float32)
    np.minimum.at(lo, idx, v); np.maximum.at(hi, idx, v)
    g = np.where(-lo >= hi, lo, hi).reshape(ny, nx)[::-1]
    tr = from_origin(x0, y0 + ny * cell, cell, cell)
    dtr, dw, dhh = calculate_default_transform("EPSG:32640", "EPSG:4326", nx, ny, left=x0, bottom=y0, right=x0 + nx * cell, top=y0 + ny * cell)
    w = np.zeros((dhh, dw), np.float32)
    reproject(source=g, destination=w, src_transform=tr, src_crs="EPSG:32640", dst_transform=dtr, dst_crs="EPSG:4326",
              resampling=Resampling.nearest, src_nodata=0, dst_nodata=0)
    q = np.clip(np.abs(w) / DIFF_MAX, 0, 1) ** 0.5
    rgba = np.zeros(w.shape + (4,), np.uint8)
    neg, pos = w < 0, w > 0
    rgba[neg] = np.stack([37 * (1 - q[neg]), 99 + 0 * q[neg], 235 - 60 * q[neg], 90 + 165 * q[neg]], -1).astype(np.uint8)
    rgba[pos] = np.stack([220 + 0 * q[pos], 38 + 0 * q[pos], 38 + 0 * q[pos], 90 + 165 * q[pos]], -1).astype(np.uint8)
    # 只为显示：变化的格很稀（全市 182 ha），城市尺度下一个像素都看不见——按 alpha 最大值向外扩 2 像素
    from scipy.ndimage import grey_dilation
    for c in range(4):
        rgba[..., c] = np.where(rgba[..., 3] > 0, rgba[..., c], grey_dilation(np.where(rgba[..., 3] > 0, rgba[..., c], 0), size=5))
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)
    return [[round(dtr.f + dhh * dtr.e, 6), round(dtr.c, 6)], [round(dtr.f, 6), round(dtr.c + dw * dtr.a, 6)]]


def zone_stats(mask, a, h1, h2):
    r = {"area_ha": round(float(a[mask].sum()) / 1e4, 1)}
    for t in TH:
        k = f"ge_{t:.2f}".replace(".", "p")
        r[f"L1_{k}_ha"] = round(float(a[mask & (h1 >= t)].sum()) / 1e4, 1)
        r[f"L2_{k}_ha"] = round(float(a[mask & (h2 >= t)].sum()) / 1e4, 1)
    d = h2 - h1
    r["shallower_ha"] = round(float(a[mask & (d <= -DIFF_MIN)].sum()) / 1e4, 1)
    r["deeper_ha"] = round(float(a[mask & (d >= DIFF_MIN)].sum()) / 1e4, 1)
    r["L1_max_m"] = round(float(h1[mask].max()), 2) if mask.any() else None
    r["L2_max_m"] = round(float(h2[mask].max()), 2) if mask.any() else None
    return r


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    from shapely import contains_xy
    from shapely.validation import make_valid
    z1, z2 = np.load(C2D / "City2D_L1_2d.npz"), np.load(C2D / "City2D_L2_2d.npz")
    xy, a = z1["xy"].astype(float), z1["area"].astype(float)
    h1, h2 = z1["hmax"].astype(float), z2["hmax"].astype(float)
    assert np.allclose(xy, z2["xy"]), "两态网格不同"
    cell = float(json.loads((C2D / "twoway_City2D_L1.json").read_text(encoding="utf-8"))["res_m"])
    b = render_diff(xy, h2 - h1, cell, OUT / "diff.png")
    (OUT / "diff.json").write_text(json.dumps({"bounds": b, "min_m": DIFF_MIN, "vmax_m": DIFF_MAX, "png": "diff.png",
                                               "note": "L2 − L1 全程最大水深；蓝 = L2 更浅，红 = L2 更深；|Δ| < 5 cm 不画"}, ensure_ascii=False), encoding="utf-8")
    print(f"diff.png 范围 {b}")
    out = {"scenario": "61 mm / 2 h", "res_m": cell, "all": zone_stats(np.ones(len(a), bool), a, h1, h2), "catchments": [], "ponds": []}
    C = gpd.read_file(ROOT / "console" / "data" / "catchments_sw.geojson").to_crs(32640)
    for lab, g in C.groupby("label"):
        geom = make_valid(g.geometry.union_all()).buffer(0)
        m = contains_xy(geom, xy[:, 0], xy[:, 1])
        if m.sum() < 10:
            continue
        out["catchments"].append({"label": lab, **zone_stats(m, a, h1, h2)})
    P = gpd.read_file(ROOT / "console" / "data" / "ponds_l2.geojson").to_crs(32640)
    for _, r in P.iterrows():
        m = contains_xy(make_valid(r.geometry).buffer(1000), xy[:, 0], xy[:, 1])   # 塘周边 1 km
        if m.sum() < 10:
            continue
        out["ponds"].append({"unitid": r.unitid, "district": r.get("district"), **zone_stats(m, a, h1, h2)})
    out["catchments"].sort(key=lambda r: -r["shallower_ha"])
    out["ponds"].sort(key=lambda r: -r["shallower_ha"])
    sh = out["all"]["shallower_ha"]
    near = contains_xy(make_valid(P.geometry.buffer(1000).union_all()), xy[:, 0], xy[:, 1])
    out["shallower_within_1km_of_ponds_ha"] = round(float(a[near & (h2 - h1 <= -DIFF_MIN)].sum()) / 1e4, 1)
    (ROOT / "out" / "city2d_zones_R61D2.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    # 控制台：汇水区属性框用（按客户汇水区名）
    (OUT / "zones_catch.json").write_text(json.dumps({r["label"]: {k: r[k] for k in r if k != "label"} for r in out["catchments"]},
                                                     ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"全市：变浅 ≥5 cm {sh} ha（其中塘周边 1 km 内 {out['shallower_within_1km_of_ponds_ha']} ha），变深 {out['all']['deeper_ha']} ha")
    for r in out["catchments"][:8]:
        print(f"  {r['label']:<22} 变浅 {r['shallower_ha']:>6} ha　≥0.30 m {r['L1_ge_0p30_ha']} → {r['L2_ge_0p30_ha']} ha")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
