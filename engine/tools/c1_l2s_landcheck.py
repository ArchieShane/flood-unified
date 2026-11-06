# -*- coding: utf-8 -*-
"""C1 L2+suggested 的措施落在什么地方？（用户 2026-09-29：「是不是没有考虑周边的土地利用情况？箱涵出现在了建筑物的地下？」）

对每处措施查三个位置：
  符号点 = 残余积水片的形心（系统里画的位置）；
  接入点 = 模型里真正接的位置（箱涵 / 转输泵 = 片内的井；地面取水泵 = 积水片本身）；
  积水片 = 这一处 ≥0.30 m 的二维单元（凸包外扩 8 m，与设计时一致）。
量：点是否落在建筑里、离最近建筑多远、积水片压到多少建筑面积、落在哪类地块（makani ud_plot）。
建筑 = makani public.bas_building；地块 = public.ud_plot。
产物：out/c1_{态}_land_{tag}.json + 屏幕表
用法：python tools/c1_l2s_landcheck.py [--state L2S] [--tag R96]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--state", default="L2S"); ap.add_argument("--tag", default=None)
    a = ap.parse_args()
    import numpy as np
    import psycopg
    import shapely
    import shapely.ops
    from pyproj import Transformer
    from shapely import wkb
    from shapely.geometry import MultiPoint, Point, shape
    from shapely.strtree import STRtree
    from swbrain import db
    from workdir import WORK
    cfg = db.load_config(None)["source"]["makani"]
    conn = psycopg.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"], password=cfg["password"], dbname=cfg["dbname"], connect_timeout=20)
    dom = shape(json.loads((ROOT / "out" / "c1_domain.geojson").read_text(encoding="utf-8"))["features"][0]["geometry"])
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32640", always_xy=True)
    to_ll = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    if dom.bounds[0] > 1000:                                  # 已是 UTM
        dom_utm = dom
    else:
        dom_utm = shapely.ops.transform(to_utm.transform, dom)
    x0, y0, x1, y1 = dom_utm.buffer(200).bounds
    lo0, la0 = to_ll.transform(x0, y0); lo1, la1 = to_ll.transform(x1, y1)
    with conn.cursor() as cur:
        cur.execute("select st_asbinary(st_transform(shape, 32640)) from public.bas_building where shape && st_makeenvelope(%s,%s,%s,%s,4326)", (lo0, la0, lo1, la1))
        B = [wkb.loads(bytes(r[0])) for r in cur.fetchall() if r[0]]
        cur.execute("select st_asbinary(shape), level_1_allocation_type_eng from public.ud_plot where shape && st_transform(st_makeenvelope(%s,%s,%s,%s,4326), 32640)", (lo0, la0, lo1, la1))
        P = [(wkb.loads(bytes(r[0])), r[1]) for r in cur.fetchall() if r[0]]
    conn.close()
    B = [g.buffer(0) for g in B if g is not None and not g.is_empty]
    TB = STRtree(B); TP = STRtree([g for g, _ in P])
    print(f"计算域内建筑 {len(B):,} 栋、地块 {len(P):,} 块")

    def bld(pt):
        idx = TB.query(pt.buffer(300))
        if len(idx) == 0:
            return False, None
        d = min(B[i].distance(pt) for i in idx)
        return d == 0.0, round(d, 1)

    def use(pt):
        for i in TP.query(pt):
            if P[i][0].contains(pt):
                return P[i][1] or "（地块无用地类型）"
        return "台账无覆盖"

    rows_all = {}
    for tag in ([a.tag] if a.tag else ["R61", "R96"]):
        f = ROOT / "out" / f"c1_{a.state}_inputs_{tag}.json"
        if not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        z = np.load(WORK / "c2d" / f"C1_{tag}_{d.get('base', 'L2P')}_2d.npz"); xy = z["xy"].astype(float); hm = z["hmax"]
        meta = json.loads((WORK / "c2d" / f"pilot_C1_{tag}_L1.json").read_text(encoding="utf-8"))
        nxy = {k: tuple(v) for k, v in meta["nodes"].items()}
        rows = []
        for m in d["measures"]:
            c = Point(m["cx"], m["cy"]); inb, db_ = bld(c)
            node = m.get("node") or m.get("from")
            npt = Point(*nxy[node]) if node in nxy else None
            ninb, nd = bld(npt) if npt else (None, None)
            patch = None
            if m.get("sump_polygon"):
                patch = shapely.geometry.Polygon(m["sump_polygon"])
            else:
                r = max(40.0, (m["area_ha"] * 1e4 / np.pi) ** 0.5 + 25.0)
                sel = (np.hypot(xy[:, 0] - m["cx"], xy[:, 1] - m["cy"]) < r) & (hm >= 0.30)
                if sel.sum() >= 3:
                    patch = MultiPoint([tuple(p) for p in xy[sel]]).convex_hull.buffer(8.0)
            ov = sum(B[i].intersection(patch).area for i in TB.query(patch)) if patch is not None else 0.0
            rows.append({"id": m["id"], "kind": m["kind"], "near_hotspot": m["near_hotspot"], "area_ha": m["area_ha"],
                         "centroid_in_building": inb, "centroid_to_building_m": db_, "centroid_use": use(c),
                         "node": node, "node_in_building": ninb, "node_to_building_m": nd, "node_use": use(npt) if npt else None,
                         "patch_building_m2": round(ov), "patch_building_pct": round(ov / patch.area * 100, 1) if patch is not None and patch.area else None})
        rows_all[tag] = rows
        print(f"\n== {tag} {a.state}：{len(rows)} 处")
        print(f"{'措施':<5}{'类型':<6}{'热点':<9}{'形心在楼里':>8}{'形心离楼':>8}{'接入井在楼里':>10}{'积水片压楼':>10}  形心地块")
        for r in rows:
            print(f"{r['id']:<5}{r['kind']:<6}{r['near_hotspot']:<9}{'是' if r['centroid_in_building'] else '':>8}{(str(r['centroid_to_building_m']) + ' m') if r['centroid_to_building_m'] is not None else '>300 m':>8}"
                  f"{'是' if r['node_in_building'] else '':>10}{(str(r['patch_building_pct']) + '%') if r['patch_building_pct'] is not None else '':>10}  {r['centroid_use']}")
        n_c = sum(1 for r in rows if r["centroid_in_building"]); n_n = sum(1 for r in rows if r["node_in_building"])
        print(f"  形心落在楼里 {n_c} 处；接入井落在楼里 {n_n} 处；积水片压楼 >20% 的 {sum(1 for r in rows if (r['patch_building_pct'] or 0) > 20)} 处")
        (ROOT / "out" / f"c1_{a.state}_land_{tag}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
