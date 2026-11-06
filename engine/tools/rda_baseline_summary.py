# -*- coding: utf-8 -*-
"""RD-A 一二维现状结果摘要（2026-10-20）：全域与 11 个热点（客户热点表里落在 RD-A 汇水区 200 m 内的）的积水指标。

每个热点（200 m 圈）：最大水深、≥0.15 m / ≥0.30 m 积水面积、≥0.15 m 面积×时间、雨停后退到 < 0.10 m 的时间（同优化试点判据 A–C 的量）。
用法：python tools/rda_baseline_summary.py --tag S61D1 [--state L1]
产物：out/rda/baseline_<tag>_<态>.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True); ap.add_argument("--state", default="L1")
    a = ap.parse_args(argv)
    import geopandas as gpd
    name = f"RDA_{a.tag}"
    info = json.loads((ROOT / "out" / f"rda_inputs_{a.tag}_{a.state}.json").read_text(encoding="utf-8"))
    rain_end = float(info["dur_h"])
    z = np.load(C2D / f"{name}_{a.state}_2d.npz")
    xy = z["xy"].astype(float); area = z["area"].astype(float); t = z["t_h"]; dt = float(np.diff(t).mean())
    hm = z["hmax"].astype(float); fr = z["frames"].astype(np.float32)
    dom = gpd.read_file(ROOT / "out/dataqc/RD-A/input/rda_domain.geojson").to_crs(32640)
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640)
    H = H[H.intersects(dom.geometry.iloc[0].buffer(200))].set_index("hotspot_id")
    tw = json.loads((C2D / f"twoway_{name}_{a.state}.json").read_text(encoding="utf-8"))
    rpt = C2D / f"{name}_{a.state}_1d.rpt"
    out = {"name": name, "state": a.state, "rain_mm": info["rain_mm"], "dur_h": info["dur_h"], "sim_h": info["sim_h"],
           "runoff_m3": info["runoff_m3"], "twoway": {k: v for k, v in tw.items() if not isinstance(v, (list, dict))}, "hotspots": {}}
    out["domain"] = {"ge015_ha": round(float(area[hm >= 0.15].sum() / 1e4), 2), "ge030_ha": round(float(area[hm >= 0.30].sum() / 1e4), 2),
                     "ge050_ha": round(float(area[hm >= 0.50].sum() / 1e4), 2), "peak_vol_m3": round(float((fr * area).sum(1).max())),
                     "end_vol_m3": round(float((fr[-1] * area).sum())), "max_m": round(float(hm.max()), 2),
                     "max_xy": [round(float(v)) for v in xy[int(hm.argmax())]]}
    for h, r in H.iterrows():
        c = np.hypot(xy[:, 0] - r.geometry.x, xy[:, 1] - r.geometry.y) <= 200
        hc = fr[:, c]
        dmax_t = hc.max(1) if c.any() else np.zeros(len(t))
        wet_after = np.flatnonzero((t >= rain_end) & (dmax_t >= 0.10))
        rec = 0.0 if not len(wet_after) else (None if wet_after[-1] == len(t) - 1 else float(t[wet_after[-1] + 1] - rain_end))
        out["hotspots"][h] = {"criticality": r.get("criticality"), "location": r.get("location"),
                              "max_m": round(float(hm[c].max()), 2) if c.any() else 0.0,
                              "ge015_ha": round(float(area[c & (hm >= 0.15)].sum() / 1e4), 3), "ge030_ha": round(float(area[c & (hm >= 0.30)].sum() / 1e4), 3),
                              "ge015_ha_h": round(float(((hc >= 0.15) * area[c]).sum() * dt / 1e4), 2), "recede_h_after_rain": rec}
    of = ROOT / "out" / "rda" / f"baseline_{a.tag}_{a.state}.json"
    of.parent.mkdir(parents=True, exist_ok=True)
    of.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    d = out["domain"]
    print(f"{name} {a.state}：全域 ≥0.15 m {d['ge015_ha']} ha、≥0.30 m {d['ge030_ha']} ha、≥0.50 m {d['ge050_ha']} ha，峰值积水 {d['peak_vol_m3']:,} m³，"
          f"结束时 {d['end_vol_m3']:,} m³，最深 {d['max_m']} m @ {d['max_xy']}")
    for h, v in out["hotspots"].items():
        print(f"  {h:<8} 最深 {v['max_m']:>5} m  ≥0.30 {v['ge030_ha']:>6} ha  ≥0.15 面积×时 {v['ge015_ha_h']:>6}  退水 {v['recede_h_after_rain']}")
    print(f"→ {of}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
