# -*- coding: utf-8 -*-
"""L2+suggested 地面泵候选（2026-10-24，用户：「先做造价–效果曲线和地面泵并入候选」）：给 l2s_pits 的每个洼坑配几档地面泵，
和一维候选（地下池 / 泵站）放进同一个候选池，由贪心 + 剪枝一起挑。

每个洼坑一个锚点（well = "PIT:<洼坑>"），同一锚点只能选一档（贪心里可以升档，剪枝里可以换便宜档）。
档位   q = {0.1, 0.25, 0.5, 1.0} × 洼坑基线（L2P 吸水二维）进水峰值，下限 0.05 m³/s，去重（Claude 自定）
泵站   l2s_sumps_v3.design_station：场地、供电、临路、不积水、湿井深、配泵（DEC-04）、压力管流速、新建排海口，同 ps_cluster 的检查（DOM-B11）
       一个洼坑一座泵站；选完后相邻的再合建，是成果整理时的事（不在优化里）
产物：out/l2opt/<新运行>/ = 原运行的 candidates / geojson / compliance / screen_stage1 / pits 拷贝 + 地面泵候选（kind = surface_ps）
用法：python tools/l2s_pit_candidates.py --run RDA_96C --out RDA_96D
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
import l2s_candidates_v3 as V3  # noqa: E402
import l2s_sumps_v3 as SU  # noqa: E402

FRACS = (0.1, 0.25, 0.5, 1.0)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True); ap.add_argument("--out", required=True); ap.add_argument("--prefix", default="RDA")
    ap.add_argument("--sitedata", default=str(ROOT / "out/rda/sitedata.gpkg")); ap.add_argument("--domain", default=str(ROOT / "out/dataqc/RD-A/input/rda_domain.geojson"))
    a = ap.parse_args(argv)
    import geopandas as gpd
    from pyproj import Transformer
    from shapely.geometry import LineString, Point, Polygon, mapping
    from shapely.ops import transform as stf
    TI = Transformer.from_crs(32640, 4326, always_xy=True)
    W = lambda g: mapping(stf(lambda x, y, z=None: TI.transform(x, y), g))   # noqa: E731
    src = ROOT / "out" / "l2opt" / a.run; o = ROOT / "out" / "l2opt" / a.out; o.mkdir(parents=True, exist_ok=True)
    for f in ("candidates.geojson", "compliance.json", "screen_stage1.json", "pits.json", "summary.json", "rejected.json"):
        if (src / f).exists():
            shutil.copyfile(src / f, o / f)
    D = json.loads((src / "candidates.json").read_text(encoding="utf-8"))
    PJ = json.loads((src / "pits.json").read_text(encoding="utf-8"))
    dom = gpd.read_file(a.domain).to_crs(32640)
    site = V3.Site(a.sitedata, dom, a.prefix, PJ["storms"][0], PJ["base"])
    ctx = SU.Ctx(site)
    tun = site.L["no_dig"]; tun = tun[tun.src == "udm_roadtunnel"]
    comp = json.loads((o / "compliance.json").read_text(encoding="utf-8"))
    G = json.loads((o / "candidates.geojson").read_text(encoding="utf-8"))
    new, bad = [], []
    for p in PJ["pits"]:
        if not p["storms"]:
            bad.append((p["id"], "没有进水过程线（先跑 l2s_pits inflow）")); continue
        ipk = max(max(S["inflow_m3s"]) for S in p["storms"].values())
        pt = Point(p["x"], p["y"]); dt = float(tun.distance(pt).min()) if len(tun) else 1e9
        cls = "隧道引道" if dt <= 50 else "地面洼坑"
        why = "用户 2026-10-24 选 (b)：作为新建泵站计入方案，待客户确认是否有现状泵站" if cls == "隧道引道" else ""
        for q in sorted({round(max(0.05, f * ipk), 2) for f in FRACS}):
            intake = {"ids": [p["id"]], "class": cls, "why": why, "x": p["x"], "y": p["y"], "z": p["bottom_m"], "q": q, "hotspot": p["hotspot"],
                      "geom": Polygon(p["poly"])}
            ok, res, items = SU.design_station(ctx, [intake])
            if not ok:
                bad.append((f"{p['id']} q={q}", res)); continue
            cid = f"SS{len([c for c in new]) + 1:04d}"
            c = {"id": cid, "kind": "surface_ps", "well": f"PIT:{p['id']}", "wells": [], "pits": [p["id"]], "pit_q": {p["id"]: q},
                 "hotspot": p["hotspot"], "hs_dist_m": p["hs_dist_m"], "overflow_m3": 0, "overflow_rate_m3s": 0.0, "inflow_peak_base_m3s": round(ipk, 3),
                 "lon": round(TI.transform(res["ps_x"], res["ps_y"])[0], 6), "lat": round(TI.transform(res["ps_x"], res["ps_y"])[1], 6), **res}
            new.append(c); comp[cid] = items
            G["features"].append({"type": "Feature", "properties": {"id": cid, "kind": "surface_ps", "status": res["status"]}, "geometry": W(Point(res["ps_x"], res["ps_y"]))})
            G["features"].append({"type": "Feature", "properties": {"id": cid + "_link", "kind": "link"},
                                  "geometry": W(LineString([(res["ps_x"], res["ps_y"]), tuple(res["of_xy"])]))})
            print(f"  {cid} {p['id']} {p['hotspot']} {cls} q {q} m³/s（基线进水峰值 {ipk:.2f}）：{res['n_pumps']} 台，压力管 {res['pipe_m']} m → {res['outfall_site']}，"
                  f"{res['cost_usd'] / 1e6:.2f} M USD，{res['status']}")
    for k, why in bad:
        print(f"  ! {k}：{why}")
    D["candidates"] += new
    D.setdefault("meta", {})["surface_ps"] = {"n": len(new), "rule": __doc__.split("产物")[0].strip(), "failed": bad}
    (o / "candidates.json").write_text(json.dumps(D, ensure_ascii=False, default=float), encoding="utf-8")
    (o / "candidates.geojson").write_text(json.dumps(G, ensure_ascii=False), encoding="utf-8")
    (o / "compliance.json").write_text(json.dumps(comp, ensure_ascii=False, default=float), encoding="utf-8")
    print(f"→ {o}：原候选 {len(D['candidates']) - len(new)} + 地面泵 {len(new)}（{len({c['well'] for c in new})} 个洼坑），不可建 {len(bad)} 档")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
