# -*- coding: utf-8 -*-
"""按客户 QGIS 截图（手机拍屏幕）描汇水区面（用户 2026-09-29：客户 Catchment 图层导不出来，按截图参照库里的图层描）。
通用版，由 tools/rb3_photo_georef.py / rb3_photo_icp.py / rb3_trace_export.py 合并而来：
  1. 红色区域（选中要素的描边 + 内部红点）→ 闭运算填满，取最大块
  2. 照片黑色边界线（去掉填充小点）↔ flood 库 catchment.catchment 39 个面边界：倒角匹配粗搜（比例尺按红色面积与 Shape_Area 估，
     旋转 ±6°，平移 ±2.5 km）→ ICP 求单应（纠透视）
  3. 红色区域向内收缩，收缩量按截图属性 Shape_Area 定；轮廓 → UTM 40N，化简 2 m
用法：python tools/photo_catch_trace.py <照片> <OBJECTID> <Shape_Area m²> <猜的中心 x> <猜的中心 y> [--ymax 像素]
产物：out/catchment_trace/<OBJECTID>/{trace.geojson, H.npy, overlay.jpg, result.json}
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATCH39 = ROOT / "out" / "catchment_rb3" / "work" / "catch39.geojson"


def red_mask(im):
    from scipy import ndimage as ndi
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    red = (r > 150) & (r - g > 70) & (r - b > 60)
    m = ndi.binary_fill_holes(ndi.binary_closing(red, structure=np.ones((25, 25))))
    lab, n = ndi.label(m); sz = ndi.sum(m, lab, range(1, n + 1))
    return lab == (int(np.argmax(sz)) + 1), red


def edge_map(im, red, box):
    from scipy import ndimage as ndi
    gray = im.mean(2)
    dark = (gray < 95) & ~ndi.binary_dilation(red, iterations=6)
    mm = np.zeros_like(dark); y0, y1, x0, x1 = box; mm[y0:y1, x0:x1] = True; dark &= mm
    lab, n = ndi.label(dark, structure=np.ones((3, 3))); sz = ndi.sum(dark, lab, range(1, n + 1))
    keep = np.zeros(n + 1, bool); keep[1:] = sz > 400
    return keep[lab]


def proj(Hm, XY):
    q = np.c_[XY, np.ones(len(XY))] @ Hm.T
    return q[:, :2] / q[:, 2:3]


def fit_h(src, dst):
    def norm(a):
        m = a.mean(0); s = np.sqrt(2) / np.mean(np.linalg.norm(a - m, axis=1))
        T = np.array([[s, 0, -s * m[0]], [0, s, -s * m[1]], [0, 0, 1]]); return T, (np.c_[a, np.ones(len(a))] @ T.T)[:, :2]
    Ts, s2 = norm(src); Td, d2 = norm(dst)
    A = []
    for (x, y), (u, v) in zip(s2, d2):
        A.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u]); A.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _, _, Vt = np.linalg.svd(np.asarray(A)); H = np.linalg.inv(Td) @ Vt[-1].reshape(3, 3) @ Ts
    return H / H[2, 2]


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("photo"); ap.add_argument("oid"); ap.add_argument("area", type=float); ap.add_argument("cx", type=float); ap.add_argument("cy", type=float)
    ap.add_argument("--ymax", type=int, default=None); ap.add_argument("--search", type=float, default=2500.0)
    a = ap.parse_args(argv)
    import geopandas as gpd
    import rasterio.features
    from affine import Affine
    from PIL import Image
    from scipy import ndimage as ndi
    from shapely.geometry import shape
    from shapely.ops import transform as stransform
    out = ROOT / "out" / "catchment_trace" / a.oid; out.mkdir(parents=True, exist_ok=True)
    im = np.asarray(Image.open(a.photo).convert("RGB")).astype(int); Hh, Ww = im.shape[:2]
    M, red = red_mask(im)
    box = (40, a.ymax or Hh - 40, 40, Ww - 40)
    E = edge_map(im, red, box)
    DT, IDX = ndi.distance_transform_edt(~E, return_indices=True)
    G = gpd.read_file(CATCH39)
    pts = []
    for geom in G.geometry:
        for poly in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom]):
            for ring in [poly.exterior, *poly.interiors]:
                k = max(int(ring.length / 10), 4); pts += [ring.interpolate(i / k, normalized=True).coords[0] for i in range(k)]
    P = np.array(pts)
    P = P[(np.abs(P[:, 0] - a.cx) < 12000) & (np.abs(P[:, 1] - a.cy) < 12000)]
    inview = lambda uv: (uv[:, 0] > box[2]) & (uv[:, 0] < box[3]) & (uv[:, 1] > box[0]) & (uv[:, 1] < box[1])

    def cost(Hm, XY, tr):
        uv = proj(Hm, XY); ok = inview(uv)
        if ok.sum() < 200:
            return 1e6
        return float(np.minimum(DT[uv[ok, 1].astype(int), uv[ok, 0].astype(int)], tr).mean())

    m0 = np.sqrt(a.area / M.sum())                       # 未扣描边的比例尺，真实值约大 10–20 %
    cy_px, cx_px = np.array(np.nonzero(M)).mean(1)
    best = (1e9, None)
    Ps = P[::8]
    for sf in np.arange(1.0, 1.36, 0.035):
        s = 1.0 / (m0 * sf)
        for thd in np.arange(-6, 6.1, 1.5):
            th = np.deg2rad(thd); c, sn = np.cos(th), np.sin(th)
            for dx in np.arange(-a.search, a.search + 1, 125):
                for dy in np.arange(-a.search, a.search + 1, 125):
                    X, Y = a.cx + dx, a.cy + dy
                    Hm = np.array([[s * c, -s * sn, cx_px - s * (c * X - sn * Y)], [-s * sn, -s * c, cy_px + s * (sn * X + c * Y)], [0, 0, 1.0]])
                    cst = cost(Hm, Ps, 25.0)
                    if cst < best[0]:
                        best = (cst, Hm, sf, thd, dx, dy)
    print(f"[{a.oid}] 粗搜：cost {best[0]:.2f}，比例 ×{best[2]:.3f}，旋转 {best[3]}°，平移 ({best[4]:.0f}, {best[5]:.0f})")
    Hm = best[1]
    for thr in (40, 30, 20, 14, 10, 8, 8, 8):
        uv = proj(Hm, P); ok = inview(uv)
        ui, vi = uv[ok, 0].astype(int), uv[ok, 1].astype(int); d = DT[vi, ui]; inl = d < thr
        if inl.sum() < 50:
            break
        Hm = fit_h(P[ok][inl], np.c_[IDX[1][vi[inl], ui[inl]], IDX[0][vi[inl], ui[inl]]].astype(float))
    uv = proj(Hm, P); ok = inview(uv); d = DT[uv[ok, 1].astype(int), uv[ok, 0].astype(int)]
    q = {"n_db_pts_in_view": int(ok.sum()), "share_le5px": round(float((d <= 5).mean()), 3), "share_le10px": round(float((d <= 10).mean()), 3),
         "share_le20px": round(float((d <= 20).mean()), 3)}
    Hi = np.linalg.inv(Hm)
    a0 = proj(Hi, np.array([[cx_px, cy_px], [cx_px + 100, cy_px]])); mpp = float(np.linalg.norm(a0[1] - a0[0]) / 100)
    print(f"[{a.oid}] ICP：{q}，局部比例 {mpp:.3f} m/px")
    np.save(out / "H.npy", Hm)
    DTin = ndi.distance_transform_edt(M)

    def px2utm(x, y, z=None):
        qq = np.c_[np.asarray(x, float), np.asarray(y, float), np.ones(np.size(x))] @ Hi.T
        return qq[:, 0] / qq[:, 2], qq[:, 1] / qq[:, 2]

    def poly_at(t):
        m = (DTin > t).astype(np.uint8)
        gs = [shape(g) for g, v in rasterio.features.shapes(m, mask=m.astype(bool), transform=Affine.identity()) if v == 1]
        g = max(gs, key=lambda x: x.area)
        return stransform(lambda x, y, z=None: px2utm(x, y), g).buffer(0).simplify(2.0, preserve_topology=True)

    lo, hi = 0.0, 60.0
    for _ in range(30):
        t = (lo + hi) / 2
        lo, hi = (t, hi) if poly_at(t).area > a.area else (lo, t)
    t = (lo + hi) / 2; Pg = poly_at(t)
    print(f"[{a.oid}] 内缩 {t:.1f} px（{t * mpp:.0f} m）：面积 {Pg.area:,.0f} m²（目标 {a.area:,.0f}），周长 {Pg.length:,.0f} m")
    gpd.GeoDataFrame([{"OBJECTID": a.oid, "trace_area": round(Pg.area), "trace_leng": round(Pg.length), "erode_px": round(t, 2), "m_per_px": round(mpp, 3), **q}],
                     geometry=[Pg], crs=32640).to_file(out / "trace.geojson", driver="GeoJSON")
    json.dump({"coarse": [best[0], best[2], best[3], best[4], best[5]], "icp": q, "m_per_px": mpp, "erode_px": t, "area": Pg.area, "perim": Pg.length},
              open(out / "result.json", "w"), indent=1)
    ov = im.copy().astype(np.uint8)
    for u, v in uv[ok].astype(int):
        ov[max(v - 3, 0):v + 4, max(u - 3, 0):u + 4] = (0, 160, 255)
    xy = np.array(Pg.exterior.coords)[:, :2]; pp = proj(Hm, xy).astype(int)
    from PIL import ImageDraw
    img = Image.fromarray(ov); dr = ImageDraw.Draw(img); dr.line([tuple(p) for p in pp], fill=(255, 220, 0), width=6)
    img.resize((768, 1024)).save(out / "overlay.jpg")


if __name__ == "__main__":
    main()
