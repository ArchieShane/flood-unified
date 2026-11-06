# -*- coding: utf-8 -*-
"""建筑处理第 2 步（用户 10-28 同意）：在 RDAG L1 上生成两个对比态，同雨 A61D1、同其余参数。

L1N  建筑不处理：建筑占地糙率改成其他硬地 0.016（等于「水可从楼上流过」）
L1H  建筑挖洞：Makani 建筑轮廓 = 二维网格里的洞，洞边反射墙；洞边短边自然把网格加密；建筑类糙率同 L1N（楼已经挖掉，剩下的是楼边地面）
L1（已有）= 高糙率法 n 0.5。三态对比见 tools/bldg_holes_compare.py

轮廓处理（Claude 定，结果里写明）：
  · 三张 Makani 建筑表合并溶解（同 bldg_dtm_check.py）
  · 栅格法（CELL = 2.5 m）：建筑栅格化 → 闭运算（一格宽、约 < 3 m 的缝放不下网格，当作堵死）→ 填天井 → 去对角相接
    → 去掉离计算域边界 < EDGE m 的格 → 描边成阶梯多边形。边长、洞间距都 ≥ CELL，网格不出极小三角形
    （矢量缓冲 + 化简的做法试过两次，洞间距收敛不了，网格最小内切圆 0.02–0.05 m，二维步长拖垮）
  · 面积 < MIN_AREA m² 的不挖
用法：python tools/bldg_holes_prep.py   产物：c2d/pilot_RDAG_A61D1_L1N|L1H.json、$WORK/RDAG_A61D1_L1N|L1H.inp、out/bldg/holes_RDA.json
"""
from __future__ import annotations

import copy
import json
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE / "exp_gov")); sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import bldg_dtm_check as BC  # noqa: E402
from workdir import WORK  # noqa: E402

NAME, BASE = "RDAG_A61D1", "L1"
CELL, EDGE, MIN_AREA = 2.5, 4.0, 25.0
N_BLD_AS = 0.016                     # 挖洞 / 不处理两态里「建筑」类糙率 = 其他硬地


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    from shapely.geometry import Polygon
    c2d = WORK / "c2d"
    m0 = json.loads((c2d / f"pilot_{NAME}_{BASE}.json").read_text(encoding="utf-8"))
    D = Polygon(m0["domain_polygon"])
    parts, U, cnt = BC.buildings(D)
    # 矢量缓冲 + 化简收敛不了（10-28 两次：洞间距 < 3 m 仍 38–71 对，网格最小内切圆 0.02–0.05 m，二维步长拖垮）。
    # 改成栅格法：建筑栅格化到 CELL m → 闭运算并掉一格宽的缝 → 填天井 → 去对角相接 → 去掉离边界近的格 → 描边成阶梯多边形。
    # 阶梯多边形的边长、洞间距都 ≥ CELL，网格不会出极小三角形
    import numpy as np
    from rasterio.features import rasterize, shapes
    from rasterio.transform import from_origin
    from scipy import ndimage as ndi
    from shapely.geometry import shape
    x0, y0, x1, y1 = D.bounds
    Wc, Hc = int(np.ceil((x1 - x0) / CELL)) + 2, int(np.ceil((y1 - y0) / CELL)) + 2
    tf = from_origin(x0 - CELL, y1 + CELL, CELL, CELL)
    B = rasterize(((p, 1) for p in parts), out_shape=(Hc, Wc), transform=tf, fill=0, dtype="uint8").astype(bool)
    B = ndi.binary_closing(B, structure=np.ones((3, 3)))                               # 一格宽（< GAP）的缝并掉
    B = ndi.binary_fill_holes(B)                                                       # 楼内天井不留（不会有水进去）
    for _ in range(20):                                                                # 对角相接（2×2 里只有对角两格）→ 补一格
        a, b, c, d = B[:-1, :-1], B[:-1, 1:], B[1:, :-1], B[1:, 1:]
        pinch = (a & d & ~b & ~c) | (b & c & ~a & ~d)
        if not pinch.any():
            break
        r, k = np.nonzero(pinch); B[r, k + 1] = True
    B &= rasterize([(D.buffer(-EDGE), 1)], out_shape=(Hc, Wc), transform=tf, fill=0, dtype="uint8").astype(bool)
    lab, n = ndi.label(B)
    keep = np.flatnonzero(np.bincount(lab.ravel())[1:] * CELL * CELL >= MIN_AREA) + 1
    B = np.isin(lab, keep)
    P = [Polygon(shape(g).exterior).simplify(0.01) for g, v in shapes(B.astype("uint8"), mask=B, transform=tf, connectivity=4) if v == 1]
    P = [q for q in P if q.is_valid and q.area >= MIN_AREA]
    from shapely.strtree import STRtree
    tr = STRtree(P)
    close = sum(1 for i, q in enumerate(P) for j in tr.query(q.buffer(CELL - 0.01)) if j > i and q.distance(P[j]) < CELL - 0.01)
    edge = sum(1 for q in P if D.exterior.distance(q) < EDGE - CELL * 0.75)
    e_min = min(float(np.hypot(*np.diff(np.array(q.exterior.coords), axis=0).T).min()) for q in P)
    holes = [[[round(x, 2), round(y, 2)] for x, y in list(q.exterior.coords)[:-1]] for q in P]
    area = sum(q.area for q in P)
    print(f"  洞间距 < {CELL:g} m 的对数 {close}、离边界 < {EDGE - CELL * 0.75:.1f} m 的洞 {edge}、最短边 {e_min:.2f} m", flush=True)
    n_v = sum(len(h) for h in holes)
    info = {"makani_counts": cnt, "n_buildings_dissolved": len(parts), "bld_area_ha": round(U.area / 1e4, 2),
            "method": "raster", "cell_m": CELL, "edge_m": EDGE, "min_area_m2": MIN_AREA, "min_edge_m": round(e_min, 2),
            "n_holes": len(holes), "n_pairs_closer_than_cell": close, "n_near_edge": edge, "hole_area_ha": round(area / 1e4, 2),
            "hole_share_of_domain": round(area / D.area, 4), "n_vertices": n_v}
    print(json.dumps(info, ensure_ascii=False, indent=1), flush=True)
    for st, bh in (("L1N", None), ("L1H", holes)):
        m = copy.deepcopy(m0); m["state"] = st
        m["surface_classes"]["n"]["2"] = N_BLD_AS
        m["note"] = (m.get("note") or "") + f"；{st}：建筑类 n {N_BLD_AS}" + ("、建筑挖洞（tools/bldg_holes_prep.py）" if bh else "、建筑不处理")
        if bh:
            m["building_holes"] = {"holes": bh, **info}
        (c2d / f"pilot_{NAME}_{st}.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        shutil.copyfile(WORK / f"{NAME}_{BASE}.inp", WORK / f"{NAME}_{st}.inp")
    (BC.OUT / "holes_RDA.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print("→ pilot / inp：L1N、L1H", flush=True)


if __name__ == "__main__":
    main()
