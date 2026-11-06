# -*- coding: utf-8 -*-
"""从 Parsons 的 C1 OP-02 图纸提取 12 英寸移动泵的位置（C1 的 L2 用，参数对齐页 C10，用户 2026-09-28 选定）。

图例：绿色圈 + 三角 = PROPOSED MOBILE PUMP ON DUTY 12"；品红色圈 + 三角 = ON STANDBY 12"。
落点：图上每个符号的中心，用 extract_c1_design.georef（塘轮廓配准）换算到 UTM 40N。
每处主用、备用成对，按距离配成一个泵点（主用泵的位置为准）。
图上没有流量——12 英寸泵的流量待用户确定，这里不填。
用法：python tools/extract_c1_op02.py
产物：out/c1_op02_pumps.json、out/c1_op02_check.png（核对图）、console/data/c1_op02_pumps.geojson
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from extract_c1_design import MAP_X1, ROOT, georef  # noqa: E402

import datapaths  # noqa: E402  客户数据位置：config.toml [data]
PDF = datapaths.parsons("Critical Hotspots", "PDF", "CRITICAL HOTSPOT-C1_OP-02.pdf")
DUTY, STBY = (0.0, 1.0, 0.0), (1.0, 0.0, 1.0)
same = lambda c, ref: bool(c) and all(abs(a - b) < 0.1 for a, b in zip(c, ref))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import geopandas as gpd
    import numpy as np
    import pymupdf as fitz
    page = fitz.open(PDF)[0]
    sp = [(s["text"].strip(), fitz.Rect(s["bbox"])) for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]]
    dr = page.get_drawings()
    M, n_ponds, rmax, hs_off, H, ids = georef(dr, sp)
    sym = {"duty": [], "standby": []}
    for x in dr:
        r = x["rect"]
        if x.get("fill") is not None and x["type"] == "fs" and len(x["items"]) <= 6 and 3 < r.width < 20 and r.x0 < MAP_X1 and r.y1 < 1180:
            for k, ref in (("duty", DUTY), ("standby", STBY)):
                if same(x["fill"], ref):
                    sym[k].append(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
    # 同一个符号可能由几段拼成：3 pt 内合并
    for k in sym:
        out = []
        for p in sym[k]:
            if not any(math.hypot(p[0] - q[0], p[1] - q[1]) < 3 for q in out):
                out.append(p)
        sym[k] = out
    xy = lambda p: tuple(float(v) for v in np.array([*p, 1]) @ M)
    duty = [xy(p) for p in sym["duty"]]; stby = [xy(p) for p in sym["standby"]]
    sites = []
    left = list(range(len(stby)))
    for i, d in enumerate(duty):
        j = min(left, key=lambda j: math.hypot(d[0] - stby[j][0], d[1] - stby[j][1])) if left else None
        dj = math.hypot(d[0] - stby[j][0], d[1] - stby[j][1]) if j is not None else None
        if j is not None and dj < 60:
            left.remove(j)
        else:
            j, dj = None, None
        dh = {h: math.hypot(d[0] - H.loc[h].geometry.x, d[1] - H.loc[h].geometry.y) for h in ids}
        nh = min(dh, key=dh.get)
        sites.append({"x_utm": round(d[0], 1), "y_utm": round(d[1], 1), "standby_gap_m": round(dj, 1) if dj is not None else None,
                      "near_hotspot": nh, "near_dist_m": round(dh[nh]), "size": '12"', "q_lps": None, "_pdf": sym["duty"][i]})
    sites.sort(key=lambda s: (-s["y_utm"], s["x_utm"]))
    for n, s in enumerate(sites, 1):
        s["id"] = f"MP-{n:02d}"
    gdf = gpd.GeoDataFrame([{k: v for k, v in s.items() if k != "_pdf"} for s in sites],
                           geometry=gpd.points_from_xy([s["x_utm"] for s in sites], [s["y_utm"] for s in sites]), crs=32640).to_crs(4326)
    gdf.to_file(ROOT / "console" / "data" / "c1_op02_pumps.geojson", driver="GeoJSON")
    out = {"source": PDF.name, "georef": {"n_ponds": n_ponds, "resid_m_max": round(rmax, 2)},
           "n_duty_symbols": len(duty), "n_standby_symbols": len(stby), "unpaired_standby": len(left),
           "sites": [{k: v for k, v in s.items() if k != "_pdf"} for s in sites],
           "note": "编号 MP-xx 是本脚本按北→南排的，图上没有编号；12 英寸泵流量待定；落点待用户核对"}
    (ROOT / "out" / "c1_op02_pumps.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for s in sites:
        c = fitz.Point(*s["_pdf"])
        page.draw_circle(c, 11, color=(0.1, 0.2, 0.9), width=1.6)
        page.insert_text(fitz.Point(c.x + 12, c.y + 4), s["id"], fontsize=12, color=(0.1, 0.2, 0.9))
    page.get_pixmap(dpi=110, clip=fitz.Rect(20, 180, 1880, 1200)).save(ROOT / "out" / "c1_op02_check.png")
    print(f"配准：{n_ponds} 座塘，残差最大 {rmax:.2f} m；主用符号 {len(duty)}，备用符号 {len(stby)}，未配对备用 {len(left)}")
    for s in sites:
        print(f'{s["id"]}  UTM {s["x_utm"]:.0f},{s["y_utm"]:.0f}  备用相距 {s["standby_gap_m"]} m  近 {s["near_hotspot"]} {s["near_dist_m"]} m')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
