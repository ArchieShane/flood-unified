# -*- coding: utf-8 -*-
"""RD-A 治理数据版一二维结果：按同一口径统计积水面积和水量对账，供登记表 / 交付报告引用。

口径
  面积     单元最大水深 hmax ≥ 阈值的单元面积之和（npz 的 area），阈值 0.15 / 0.30 / 0.50 m
  剔除     out/l2opt/RDA_96C/exclude_OPTCS.geojson（ADM-80 岸边 DTM = 0 区，UTM 40N），按单元形心判定；
           另列「剔除 + 已填平深坑之外的地面 < 0 m 单元」不做，深坑另见 out/bldg/
  水量     twoway_<tag>.json 的 acc_m3 / final_vol_m3 / boundary_out_measured_m3；SWMM 连续性误差取 rpt
不改任何结果文件，只读。

用法  python tools/rda_gov_stats.py --c2d D:/swbrain_work/swmm/c2d --out out/rda_gov/stats_A61D1.json
"""
import argparse
import json
import pathlib
import re

import numpy as np
from matplotlib.path import Path as MPath

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXCLUDE = ROOT / "out" / "l2opt" / "RDA_96C" / "exclude_OPTCS.geojson"
STATES = ["L1", "L2", "L2P", "OPTA61PH2", "L1F", "L2F", "L2PF", "OPTA61PH2F"]
THR = (0.15, 0.30, 0.50)


def rings(geom):
    t, c = geom["type"], geom["coordinates"]
    polys = [c] if t == "Polygon" else c if t == "MultiPolygon" else []
    return [p[0] for p in polys]  # 只取外环；剔除区没有洞


def excl_mask(xy):
    m = np.zeros(len(xy), bool)
    for f in json.loads(EXCLUDE.read_text(encoding="utf-8"))["features"]:
        for r in rings(f["geometry"]):
            m |= MPath(np.asarray(r)[:, :2]).contains_points(xy)
    return m


def cont_err(rpt):
    """rpt 里第一个 Continuity Error 是径流（无），第二个是演算；取最后一个"""
    v = re.findall(r"Continuity Error \(%\) \.+\s+(-?[\d.]+)", rpt.read_text(encoding="utf-8", errors="replace"))
    return float(v[-1]) if v else None


def one(c2d, name, st):
    tag = f"{name}_{st}"
    z = np.load(c2d / f"{tag}_2d.npz")
    xy, area, hmax, elev = z["xy"].astype(float), z["area"].astype(float), z["hmax"].astype(float), z["elev"]
    ex = excl_mask(xy)
    tw = json.loads((c2d / f"twoway_{tag}.json").read_text(encoding="utf-8"))
    acc = tw["acc_m3"]
    r = {"n_tri": int(len(area)), "domain_ha": round(area.sum() / 1e4, 2), "excl_ha": round(area[ex].sum() / 1e4, 2),
         "hmax_m": round(float(hmax.max()), 2), "hmax_ex_m": round(float(hmax[~ex].max()), 2),
         "area_ha": {}, "area_ex_ha": {}}
    for t in THR:
        r["area_ha"][f"{t:.2f}"] = round(area[hmax >= t].sum() / 1e4, 2)
        r["area_ex_ha"][f"{t:.2f}"] = round(area[(hmax >= t) & ~ex].sum() / 1e4, 2)
    r["below0_wet015_ex_ha"] = round(area[(hmax >= 0.15) & ~ex & (elev < 0)].sum() / 1e4, 2)
    i = int(np.argmax(np.where(ex, -1, hmax)))
    r["hmax_ex_at"] = [round(float(xy[i, 0]), 1), round(float(xy[i, 1]), 1)]
    r["water_m3"] = {"1to2": round(acc["1to2"]), "2to1": round(acc["2to1"]), "pump_out": round(acc.get("pump_out", 0)),
                     "final_2d": round(tw["final_vol_m3"]), "boundary_measured": round(tw["boundary_out_measured_m3"]),
                     "closure_2d": tw["closure_err_2d"]}
    r["swmm_cont_err_pct"] = cont_err(c2d / f"{tag}_2way.rpt")
    r["n_flip_nodes"] = tw.get("n_flip_nodes")
    r["peak_wet_ha"] = round(tw["max_wet_m2"] / 1e4, 2)
    r["peak_wet_t_h"] = round(tw["max_wet_t_h"], 2)
    return r


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--c2d", required=True)
    ap.add_argument("--name", default="RDAG_A61D1")
    ap.add_argument("--states", nargs="*", default=STATES)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    c2d = pathlib.Path(a.c2d)
    res = {"name": a.name, "exclude": str(EXCLUDE.relative_to(ROOT)), "states": {}}
    for st in a.states:
        res["states"][st] = one(c2d, a.name, st)
        s = res["states"][st]
        print(f"{st:11s} 全域 ≥.15/.30/.50 {s['area_ha']}  剔除后 {s['area_ex_ha']}  最深 {s['hmax_m']} / 剔除后 {s['hmax_ex_m']} @ {s['hmax_ex_at']}  连续性 {s['swmm_cont_err_pct']}%")
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print("→", a.out)


if __name__ == "__main__":
    raise SystemExit(main())
