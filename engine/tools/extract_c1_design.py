# -*- coding: utf-8 -*-
"""从 Parsons 的 C1 图纸（PDF 版，用户 2026-09-28 选定）提取 27 座泵站：参数 + 真实坐标。

为什么：C1 的 L2+planned 按 PDF 版建（参数对齐页 C11）；PDF 没有坐标，SHP 里也没有这 27 座泵站。
用户要求「手工落点，我核对」——本脚本给出落点，输出供核对的叠加图，不直接进模型。

做法（每步都可复查）：
  1. 参数：每座泵站旁的标注框（矢量文字 P1 / WET WELL SIZE / … / 1 DUTY 1 ASSIST），已与图右上角的表逐行核对
  2. 泵符号：图例「SUBMERSIBLE PUMP (DUTY / ASSIST) IN WET WELL」= 橙色方块里的绿色三角；
     绿色填充里成对相接的是阀门井（VALVE CHAMBER，蝶形两半），落单的是泵 → 正好 27 个
  3. 编号 ↔ 符号：每个标注框边上起一根引线，终点离哪个泵符号最近就是哪座（27 个一一对应，无重复）
  4. 坐标：图纸坐标 → UTM 40N 用仿射；控制点 = PDF 上的塘轮廓形心 ↔ SHP（AUH_HotSpot_Locations_Ponds）同一座塘的形心，
     先用 7 个 HS 标注粗配、再按最近塘迭代；残差 < 1 m（PDF 是 CAD 按比例出图）
用法：python tools/extract_c1_design.py
产物：out/c1_design_params.json、out/c1_pumps_check.png（核对图）、console/data/c1_pumps.geojson
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); import datapaths  # noqa: E402  客户数据位置：config.toml [data]
PDF = datapaths.parsons("Critical Hotspots", "PDF", "CRITICAL HOTSPOT-C1_WITH MECHANICAL DATA.pdf")
# 表里的 Type（表是栅格图，按表抄）与标了 (pond) 的泵站
TYPE = {**{k: 1 for k in ("P7-A", "P7-B", "P9-A", "P10-A", "P16-A")},
        **{k: 2 for k in ("P10", "P13", "P9", "P22", "P14", "P20", "P21", "P6", "P7", "P15", "P16")},
        "P17": 3, "P18": 3, "P1": 4, "P3": 4, "P4": 4, **{k: 5 for k in ("P19", "P12", "P5", "P8", "P2", "P11")}}
POND = {"P19", "P12", "P5", "P8"}
green = lambda c: bool(c) and c[1] > 0.8 and c[0] < 0.3 and c[2] < 0.3
red = lambda c: bool(c) and c[0] > 0.8 and c[1] < 0.2 and c[2] < 0.2
MAP_X1 = 2080          # 图框右侧是表格、图例、说明


def georef(dr, sp):
    """PDF 图纸坐标 → UTM 40N 仿射。先用 HS 标注粗配，再用 PDF 塘轮廓形心 ↔ SHP 同一座塘形心迭代。
    返回 (M, 塘数, 残差最大 m, HS 标注偏移 m, 热点表, 热点 id)。C1 各张图纸（PDF 版、OP-01、OP-02）共用。"""
    import geopandas as gpd
    import numpy as np
    from shapely.geometry import Polygon
    txt = [t for t, _ in sp]
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    hs = {}
    for i, (t, r) in enumerate(sp):
        m = re.fullmatch(r"HS\s*-\s*(\d+)?", t)
        if m:
            hs["ADM-" + (m[1] or txt[i + 1]).strip()] = ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
    ids = [k for k in hs if k in H.index]
    M = np.linalg.lstsq(np.array([[*hs[k], 1] for k in ids]), np.array([[H.loc[k].geometry.x, H.loc[k].geometry.y] for k in ids]), rcond=None)[0]
    polys = []
    for x in dr:
        r = x["rect"]
        if red(x.get("color")) and r.width > 15 and r.height > 15 and r.x0 < MAP_X1:
            pts = [(q.x, q.y) for it in x["items"] if it[0] == "l" for q in (it[1], it[2])]
            if len(pts) >= 4:
                pg = Polygon(pts); pg = pg if pg.is_valid else pg.convex_hull
                polys.append((pg.centroid.x, pg.centroid.y, pg.area))
    gj = json.loads((ROOT / "console" / "data" / "parsons_crit.geojson").read_text(encoding="utf-8"))
    P = gpd.GeoDataFrame.from_features([f for f in gj["features"] if f["properties"]["code"] == "C1" and f["properties"]["kind"] == "pond"], crs=4326).to_crs(32640)
    W = np.array([[c.x, c.y] for c in P.geometry.centroid]); AW = P.area.values
    for _ in range(4):
        sc = abs(np.linalg.det(M[:2, :2]))
        X = np.array([[cx, cy, 1] for cx, cy, _ in polys]) @ M
        pairs = []
        for i, w in enumerate(W):
            d = np.hypot(*(X - w).T); j = int(d.argmin())
            if d[j] < 150 and 0.5 < polys[j][2] * sc / AW[i] < 2:
                pairs.append((i, j))
        A = np.array([[polys[j][0], polys[j][1], 1] for _, j in pairs]); B = W[[i for i, _ in pairs]]
        M = np.linalg.lstsq(A, B, rcond=None)[0]
    resid = np.hypot(*(A @ M - B).T)
    hs_off = {k: round(float(np.hypot(*(np.array([*hs[k], 1]) @ M - [H.loc[k].geometry.x, H.loc[k].geometry.y])))) for k in ids}
    return M, len(pairs), float(resid.max()), hs_off, H, ids

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import pymupdf as fitz
    import geopandas as gpd
    import numpy as np
    from shapely.geometry import Polygon
    page = fitz.open(PDF)[0]
    sp = [(s["text"].strip(), fitz.Rect(s["bbox"])) for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]]
    txt = [t for t, _ in sp]
    dr = page.get_drawings()

    # 1 参数
    st, lab = {}, {}
    for i, (t, r) in enumerate(sp):
        if re.fullmatch(r"P\d+(-[AB])?", t) and i + 5 < len(sp) and txt[i + 1].startswith("WET WELL"):
            ww = re.match(r"(\d+) M DIA & (\d+) M DEEP", txt[i + 2]); pc = re.match(r"(\d+) L/S @ (\d+) M HEAD", txt[i + 4])
            st[t] = {"id": t, "q_each_lps": int(pc[1]), "head_m": int(pc[2]), "n_pumps": 2, "q_total_lps": 2 * int(pc[1]),
                     "arrangement": txt[i + 5].title(), "wetwell_dia_m": int(ww[1]), "wetwell_depth_m": int(ww[2]),
                     "type": TYPE.get(t), "pond_tag": t in POND}
            lab[t] = r
    # 2 泵符号
    g = [x["rect"] for x in dr if green(x.get("fill")) and x["rect"].width < 20 and x["rect"].x0 < MAP_X1]
    used, pumps = set(), []
    for i, a in enumerate(g):
        if i in used:
            continue
        mate = [j for j, b in enumerate(g) if j != i and j not in used and max(a.x0, b.x0) - min(a.x1, b.x1) < 0.6 and max(a.y0, b.y0) - min(a.y1, b.y1) < 0.6]
        used.add(i)
        if mate:
            used.add(mate[0])
        else:
            pumps.append(((a.x0 + a.x1) / 2, (a.y0 + a.y1) / 2))
    assert len(pumps) == len(st) == 27, (len(pumps), len(st))
    # 3 引线
    boxes = {}
    for x in dr:
        f = x.get("fill")
        if f and min(f) > 0.97 and x["rect"].width > 60 and x["rect"].height > 40:
            for t, r in lab.items():
                if x["rect"].contains(r) and (t not in boxes or x["rect"].get_area() < boxes[t].get_area()):
                    boxes[t] = x["rect"]
    def on_edge(pt, r, tol=2.0):
        return r.x0 - tol <= pt.x <= r.x1 + tol and r.y0 - tol <= pt.y <= r.y1 + tol and not (r.x0 + tol < pt.x < r.x1 - tol and r.y0 + tol < pt.y < r.y1 - tol)
    match = {}
    for t, r in boxes.items():
        best = None
        for x in dr:
            if x.get("fill") is not None:
                continue
            pts = [q for it in x["items"] if it[0] == "l" for q in (it[1], it[2])]
            if len(pts) < 2:
                continue
            for s, e in ((pts[0], pts[-1]), (pts[-1], pts[0])):
                if on_edge(s, r) and not r.contains(e) and math.hypot(s.x - e.x, s.y - e.y) > 8:
                    j = min(range(27), key=lambda j: math.hypot(e.x - pumps[j][0], e.y - pumps[j][1]))
                    d = math.hypot(e.x - pumps[j][0], e.y - pumps[j][1])
                    if d < 40 and (best is None or d < best[0]):
                        best = (d, j)
        match[t] = best
    js = [m[1] for m in match.values() if m]
    assert len(js) == 27 and len(set(js)) == 27, "引线配对不是一一对应，需要人工看"
    # 4 仿射（georef）
    M, n_ponds, rmax, hs_off, H, ids = georef(dr, sp)
    # 落点
    for t, (d, j) in match.items():
        x, y = np.array([*pumps[j], 1]) @ M
        dh = {h: float(math.hypot(x - H.loc[h].geometry.x, y - H.loc[h].geometry.y)) for h in ids}
        nh = min(dh, key=dh.get)
        st[t].update(x_utm=round(float(x), 1), y_utm=round(float(y), 1), near_hotspot=nh, near_dist_m=round(dh[nh]),
                     _pdf=[round(pumps[j][0], 1), round(pumps[j][1], 1)], leader_gap_pt=round(d, 1))
    order = sorted(st.values(), key=lambda s: (s["type"], s["id"]))
    pts = gpd.GeoDataFrame(order, geometry=gpd.points_from_xy([s["x_utm"] for s in order], [s["y_utm"] for s in order]), crs=32640).to_crs(4326)
    for s, q in zip(order, pts.geometry):
        s.update(lon=round(q.x, 7), lat=round(q.y, 7))
    tot = sum(s["q_total_lps"] for s in order)
    out = {"source": str(PDF.name) + "（2026-09-10 打印）；参数对齐页 C11 选定 PDF 版",
           "georef": {"method": "仿射：PDF 塘轮廓形心 ↔ SHP 同一座塘形心", "n_ponds": n_ponds,
                      "resid_m_max": round(rmax, 2), "m_per_pt": round(float(math.hypot(*M[0])), 4),
                      "hs_label_offset_m": hs_off, "note": "HS 文字标注离热点圆本身有偏移，不是配准误差"},
           "status": "落点待用户核对（out/c1_pumps_check.png）；核对前不进模型",
           "stations": order, "n_stations": len(order), "q_total_lps": tot, "q_duty_only_lps": tot // 2}
    (ROOT / "out" / "c1_design_params.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    pts.drop(columns=["_pdf"]).to_file(ROOT / "console" / "data" / "c1_pumps.geojson", driver="GeoJSON")
    # 核对图：在 PDF 上标出每个符号配到的编号
    for s in order:
        c = fitz.Point(*s["_pdf"])
        page.draw_circle(c, 10, color=(0.85, 0, 0.85), width=1.6)
        page.insert_text(fitz.Point(c.x + 11, c.y + 4), s["id"], fontsize=12, color=(0.85, 0, 0.85))
    page.get_pixmap(dpi=110, clip=fitz.Rect(20, 180, 1880, 1200)).save(ROOT / "out" / "c1_pumps_check.png")
    print(f"配准：{n_ponds} 座塘，残差最大 {rmax:.2f} m，比例 {math.hypot(*M[0]):.3f} m/pt")
    for s in order:
        print(f'{s["id"]:<6} 型{s["type"]} {s["q_each_lps"]:>4}×2 L/s @{s["head_m"]:>3} m  湿井 {s["wetwell_dia_m"]}×{s["wetwell_depth_m"]} m  '
              f'UTM {s["x_utm"]:.0f},{s["y_utm"]:.0f}  近 {s["near_hotspot"]} {s["near_dist_m"]} m  引线差 {s["leader_gap_pt"]} pt')
    print("HS 标注偏移（m）", hs_off)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
