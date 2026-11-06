# -*- coding: utf-8 -*-
"""地形缺陷备案图：一个点位 + 影像底图 + DTM，供登记表引用（用户 10-28 要求给深坑 230387, 2707779 出图备案）。

左：Esri World Imagery 影像（瓦片，只发了这块范围的瓦片坐标），叠 坑轮廓 / Makani 建筑 / Makani 与 Google、ADSIC 的墓地 POI
右：同范围 DTM 5 m 晕渲 + 等高线，坑轮廓
用法：python tools/pit_record_map.py --x 230387 --y 2707779 --poly out/bldg/pit_230387.geojson --id PIT-RDA-01 [--half 250]
产物：out/bldg/<id>_record.png
"""
from __future__ import annotations

import argparse
import io
import json
import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import rb01_build as RB  # noqa: E402

OUT = ROOT / "out" / "bldg"
TILE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
ATTR = "影像：Esri World Imagery（Esri, Maxar, Earthstar Geographics），影像日期未知"
R = 6378137.0


def merc(lon, lat):
    return R * np.radians(lon), R * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def imagery(lo0, la0, lo1, la1, z=18):
    import requests
    from PIL import Image
    n = 2 ** z
    tx = lambda lo: int((lo + 180) / 360 * n)
    ty = lambda la: int((1 - math.asinh(math.tan(math.radians(la))) / math.pi) / 2 * n)
    x0, x1, y0, y1 = tx(lo0), tx(lo1), ty(la1), ty(la0)
    img = Image.new("RGB", ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256))
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            r = requests.get(TILE.format(z=z, x=x, y=y), timeout=20, headers={"User-Agent": "swbrain-record/1.0"})
            r.raise_for_status()
            img.paste(Image.open(io.BytesIO(r.content)).convert("RGB"), ((x - x0) * 256, (y - y0) * 256))
    ext = 2 * math.pi * R / n
    left, top = -math.pi * R + x0 * ext, math.pi * R - y0 * ext
    return np.asarray(img), (left, left + img.width / n * 0 + (x1 - x0 + 1) * ext, top - (y1 - y0 + 1) * ext, top)


def context(lo, la, rad_deg):
    """Makani 建筑、墓地 POI（名字里带 cemetery / مقبرة 的）"""
    from shapely import wkb
    from swbrain import db
    conn = db.connect_source(db.load_config(None), "makani", timeout_ms=120000)
    pt = f"st_setsrid(st_point({lo},{la}),4326)"
    B, P = [], []
    with conn, conn.cursor() as cur:
        cur.execute(f"select st_asbinary(st_transform(shape,4326)) from public.bas_building where st_dwithin(st_transform(shape,4326),{pt},{rad_deg})")
        B = [wkb.loads(bytes(r[0])) for r in cur.fetchall() if r[0]]
        for t, nm in (("poi_google", "f_name"), ("poi_l_adsic_pois", "cf_nameeng")):
            cur.execute(f"select st_x(st_centroid(st_transform(shape,4326))), st_y(st_centroid(st_transform(shape,4326))), {nm} from public.{t} "
                        f"where st_dwithin(st_transform(shape,4326),{pt},{rad_deg}) and cast({nm} as text) ilike any(array['%cemet%','%مقبر%'])")
            P += [(x, y, f"{t}: {n}") for x, y, n in cur.fetchall()]
    return B, P


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=float, required=True); ap.add_argument("--y", type=float, required=True)
    ap.add_argument("--poly", required=True); ap.add_argument("--id", required=True)
    ap.add_argument("--half", type=float, default=250.0)
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import rasterio
    from matplotlib.colors import LightSource
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform as stf
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    to_ll = Transformer.from_crs(32640, 4326, always_xy=True)
    pit = shape(json.loads(pathlib.Path(a.poly).read_text(encoding="utf-8"))["features"][0]["geometry"])
    props = json.loads(pathlib.Path(a.poly).read_text(encoding="utf-8"))["features"][0]["properties"]
    x0, y0, x1, y1 = a.x - a.half, a.y - a.half, a.x + a.half, a.y + a.half
    lo0, la0 = to_ll.transform(x0, y0); lo1, la1 = to_ll.transform(x1, y1); lo, la = to_ll.transform(a.x, a.y)
    img, ext = imagery(lo0, la0, lo1, la1)
    B, P = context(lo, la, a.half / 90000.0)
    m = lambda g: stf(lambda X, Y, Z=None: merc(np.asarray(X), np.asarray(Y)), g)
    pit_ll = stf(lambda X, Y, Z=None: to_ll.transform(X, Y), pit)
    fig, ax = plt.subplots(1, 2, figsize=(17, 8.6))
    (mx0, my0), (mx1, my1) = merc(lo0, la0), merc(lo1, la1)
    ax[0].imshow(img, extent=ext, interpolation="bilinear")
    for g in B:
        for p in getattr(m(g), "geoms", [m(g)]):
            ax[0].plot(*p.exterior.xy, color="#f1c40f", lw=0.6)
    ax[0].plot(*m(pit_ll).exterior.xy, color="#e74c3c", lw=2.2, label="DTM 深坑轮廓")
    seen = set()
    for px, py, nm in P:
        X, Y = merc(px, py)
        # 中文字体没有阿拉伯字形，只留拉丁部分
        lab = "".join(ch for ch in nm.split(": ", 1)[1] if ord(ch) < 0x0600 or ord(ch) > 0x06FF).strip(" -") or "Cemetery (Arabic name)"
        key = (lab, round(X, -1), round(Y, -1))
        if key in seen:
            continue
        ax[0].plot(X, Y, "c^", ms=9, mec="k", label="墓地 POI" if not seen else None)
        ax[0].annotate(lab[:40], (X, Y), xytext=(8, 4 + 13 * len(seen)), textcoords="offset points",
                       color="white", fontsize=8, bbox=dict(fc="black", alpha=0.5, lw=0))
        seen.add(key)
    ax[0].plot([], [], color="#f1c40f", lw=1, label="Makani 建筑（bas_building）")
    ax[0].set_xlim(mx0, mx1); ax[0].set_ylim(my0, my1); ax[0].set_xticks([]); ax[0].set_yticks([])
    ax[0].legend(loc="lower left", fontsize=9); ax[0].set_title("影像底图")
    ax[0].text(0.01, 0.99, ATTR, transform=ax[0].transAxes, va="top", fontsize=7.5, color="white", bbox=dict(fc="black", alpha=0.5, lw=0))
    with rasterio.open(RB.DTM) as ds:
        from rasterio.windows import from_bounds
        win = from_bounds(x0, y0, x1, y1, ds.transform).round_offsets().round_lengths()
        z = ds.read(1, window=win).astype(float); b = ds.window_bounds(win)
    hs = LightSource(azdeg=315, altdeg=45).shade(z, cmap=plt.cm.terrain, vert_exag=3, blend_mode="soft", vmin=-5, vmax=6)
    ax[1].imshow(hs, extent=(b[0], b[2], b[1], b[3]))
    cs = ax[1].contour(np.linspace(b[0], b[2], z.shape[1]), np.linspace(b[3], b[1], z.shape[0]), z, levels=np.arange(-5, 6, 1), colors="k", linewidths=0.4)
    ax[1].clabel(cs, fontsize=7, fmt="%d")
    ax[1].plot(*pit.exterior.xy, color="#e74c3c", lw=2.2)
    ax[1].set_xlim(x0, x1); ax[1].set_ylim(y0, y1); ax[1].set_aspect("equal"); ax[1].set_title("客户 DTM 5 m（晕渲 + 1 m 等高线）")
    ax[1].tick_params(labelsize=7)
    fig.suptitle(f"{a.id}　UTM 40N ({a.x:.0f}, {a.y:.0f}) / WGS84 ({la:.5f}, {lo:.5f})　面积 {props.get('area_m2')} m²　"
                 f"周边地面 {props.get('ring_median_m')} m　{a.note}", fontsize=11)
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / f"{a.id}_record.png"
    fig.savefig(f, dpi=130, bbox_inches="tight")
    print(f"→ {f}；墓地 POI {len(P)} 个：{[n for *_, n in P]}")


if __name__ == "__main__":
    main()
