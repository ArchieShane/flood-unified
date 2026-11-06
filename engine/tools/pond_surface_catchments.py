# -*- coding: utf-8 -*-
"""从客户 5 m DTM 划 23 座已建成 L2 塘的**地表**汇水区。

为什么要自己划：客户 catchment.catchment 那 39 个多边形是建模研究范围，23 座塘里 15 座
（145 万 m³，占 75%）落在所有多边形之外（见 tools/pond_catchment.py）。不划出每座塘自己的
汇水区，就回答不了「这座塘在一场雨里能接到多少水」，L2 到底贡献多少也就无从谈起。

方法（pyflwdir，Deltares 的开源水文库）：
  1. 裁 DTM：23 座塘的包络四周各放 BUF_M；
  2. from_dem：优先洪泛填洼 + D8 流向，出口为窗口边缘与无数据格（海）；
  3. 每座塘的占地格都当作它的出口，basins() 把每一格归到**顺流向遇到的第一座塘**——
     上游那座塘接走的水不再算给下游那座；
  4. 栅格转面、按塘合并。

口径与保留（看结果前必须知道）：
  · 只是**地表**汇水：管网把水从一片转输到另一片，这里看不见。6 座有管道进水的塘，
    真实汇水范围会比这里大；靠地表汇流的塘（大多数）以这里为准；
  · 城区很平，填洼后的平地上 D8 走向对 DTM 噪声敏感，边界是**指示性**的；
  · DTM 的时相可能早于部分塘的建成（2021–2023），塘在 DTM 上不一定是坑，所以出口
    用的是台账轮廓本身，不依赖 DTM 里有没有坑；
  · 汇水区碰到裁剪窗口边缘的会标出来（touches_edge）：那座塘的汇水区可能被截断了。

产流：客户自己的 SCS 曲线法，CN = 82（Mussafah 全区 82、Shaliela 面积加权 82.5），
61 mm 下净雨 23.53 mm，与历时无关（见 tools/citywide.py）。

用法：python tools/pond_surface_catchments.py [--buf 5000] [--res 5]
产物：out/l2_pond_catchments.json（逐座统计）、console/data/l2_pond_catch.geojson（地图图层）
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "console" / "data"
OUT = ROOT / "out"

CN = 82.0
P_MM = 61.0


def scs_runoff_mm(p_mm: float, cn: float) -> float:
    """SCS 曲线法净雨深。S = 25400/CN − 254，Ia = 0.2 S。"""
    s = 25400.0 / cn - 254.0
    ia = 0.2 * s
    return 0.0 if p_mm <= ia else (p_mm - ia) ** 2 / (p_mm - ia + s)


def pond_stats(area_m2: float, vol_m3: float, q_mm: float) -> dict:
    """一座塘在这场雨里接到多少水、装得下多少。vol 为 0 / 缺时只给来水量。"""
    inflow = area_m2 * q_mm / 1000.0
    o = {"catch_ha": round(area_m2 / 1e4, 1), "runoff_m3": round(inflow)}
    if vol_m3 and vol_m3 > 0:
        o["fill_pct"] = round(min(inflow / vol_m3, 9.99) * 100)       # 来水 / 库容；>100% 表示装不下
        o["spill_m3"] = round(max(inflow - vol_m3, 0))
        o["idle_m3"] = round(max(vol_m3 - inflow, 0))                  # 这场雨里用不上的库容
    return o


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="划 L2 塘的地表汇水区")
    ap.add_argument("--buf", type=float, default=5000.0, help="窗口四周放宽 m")
    ap.add_argument("--res", type=float, default=5.0, help="分辨率 m（5 = DTM 原生）")
    a = ap.parse_args(argv)

    import geopandas as gpd
    import pyflwdir
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.features import rasterize, shapes
    from rasterio.windows import from_bounds
    from shapely.geometry import shape
    from shapely.ops import unary_union
    from shapely.validation import make_valid
    from swbrain import db

    cfg = db.load_config(None)
    dtm = (cfg.get("terrain") or {}).get("dtm_path")
    ponds = gpd.read_file(DATA / "ponds_l2.geojson").to_crs(32640)
    ponds["geometry"] = ponds.geometry.apply(make_valid)
    x0, y0, x1, y1 = ponds.total_bounds
    x0, y0, x1, y1 = x0 - a.buf, y0 - a.buf, x1 + a.buf, y1 + a.buf

    with rasterio.open(dtm) as ds:
        win = from_bounds(x0, y0, x1, y1, ds.transform).round_offsets().round_lengths()
        f = ds.res[0] / a.res
        H, W = int(win.height * f), int(win.width * f)
        z = ds.read(1, window=win, out_shape=(H, W), resampling=Resampling.average).astype("float32")
        tf = ds.window_transform(win) * ds.window_transform(win).scale(1 / f, 1 / f) \
            if f != 1 else ds.window_transform(win)
        nod = ds.nodata
    bad = ~np.isfinite(z) | (z < -1000) | ((nod is not None) & (z == np.float32(nod)))
    z[bad] = -9999.0
    print(f"DTM 窗口 {W}×{H} @ {a.res:g} m（{W * a.res / 1000:.1f} × {H * a.res / 1000:.1f} km），"
          f"无数据 {bad.mean() * 100:.1f}%")

    flw = pyflwdir.from_dem(z, nodata=-9999.0, transform=tf, latlon=False)

    # 每座塘的占地格 = 它的出口。all_touched：小塘（几千 m²）在 5 m 格上也至少占几格
    ids = np.arange(1, len(ponds) + 1, dtype=np.int32)
    lab = rasterize(zip(ponds.geometry, ids), out_shape=(H, W), transform=tf, fill=0,
                    all_touched=True, dtype="int32")
    idx = np.flatnonzero(lab)
    basins = flw.basins(idxs=idx, ids=lab.ravel()[idx]).astype("int32")

    q_mm = scs_runoff_mm(P_MM, CN)
    catch = gpd.read_file(DATA / "catchments_sw.geojson").to_crs(32640)
    catch["geometry"] = catch.geometry.apply(make_valid).buffer(0)
    edge = np.zeros_like(basins, bool); edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True

    feats, rows = [], []
    for i, (_, p) in zip(ids, ponds.iterrows()):
        m = basins == i
        n = int(m.sum())
        if not n:
            continue
        poly = unary_union([shape(g) for g, v in shapes(m.astype("uint8"), mask=m, transform=tf) if v])
        poly = make_valid(poly.simplify(a.res, preserve_topology=True))
        area = n * a.res * a.res
        vol = float(p.get("volume_m3") or 0)
        ov = []
        for _, c in catch.iterrows():
            if poly.intersects(c.geometry):
                pct = poly.intersection(c.geometry).area / poly.area * 100
                if pct >= 1:
                    ov.append({"label": c.get("label") or c.get("id"), "pct": round(pct)})
        ov = sorted({o["label"]: o for o in ov}.values(), key=lambda o: -o["pct"])
        r = {"unitid": p["unitid"], "district": p.get("district"), "volume_m3": vol,
             "pond_ha": round(p.geometry.area / 1e4, 2), **pond_stats(area, vol, q_mm),
             "touches_edge": bool((m & edge).any()), "in_client_catch": ov}
        rows.append(r)
        feats.append({"type": "Feature", "geometry": poly, "properties": r})

    g = gpd.GeoDataFrame([f["properties"] for f in feats], geometry=[f["geometry"] for f in feats],
                         crs=32640).to_crs(4326)
    gj = json.loads(g.to_json(drop_id=True))
    gj.update({"layer": "l2_pond_catch", "rain": f"{P_MM:g} mm · SCS CN {CN:g} → 净雨 {q_mm:.2f} mm",
               "note": "从客户 5 m DTM 按 D8 划的地表汇水区；不含管网转输，平地上边界是指示性的"})
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "l2_pond_catch.geojson").write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    rows.sort(key=lambda r: -r["runoff_m3"])
    (OUT / "l2_pond_catchments.json").write_text(json.dumps(
        {"rain": gj["rain"], "res_m": a.res, "buf_m": a.buf, "ponds": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    print(f"净雨 {q_mm:.2f} mm（{P_MM:g} mm，CN {CN:g}）\n")
    print(f"{'塘':<24}{'库容 m³':>11}{'汇水 ha':>9}{'来水 m³':>10}{'来水/库容':>9}  客户汇水区")
    for r in rows:
        fp = f"{r['fill_pct']}%" if "fill_pct" in r else "—"
        cc = "、".join(f"{o['label']} {o['pct']}%" for o in r["in_client_catch"]) or "无"
        print(f"{r['unitid']:<24}{r['volume_m3']:>11,.0f}{r['catch_ha']:>9,.1f}{r['runoff_m3']:>10,.0f}"
              f"{fp:>9}  {cc}{'  ⚠ 碰到窗口边' if r['touches_edge'] else ''}")
    tv = sum(r["volume_m3"] for r in rows); tr = sum(r["runoff_m3"] for r in rows)
    used = sum(min(r["runoff_m3"], r["volume_m3"]) for r in rows if r["volume_m3"] > 0)
    print(f"\n合计：库容 {tv:,.0f} m³，来水 {tr:,.0f} m³，这场雨里能用上的库容 {used:,.0f} m³"
          f"（{used / tv * 100:.0f}%）" if tv else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
