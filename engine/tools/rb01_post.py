# -*- coding: utf-8 -*-
"""RB-01 四态一二维结果统计（口径同 C1：c1_post / c1_l2s4_compare）。

  · 全域：全程最大水深 ≥0.10 / ≥0.30 m 面积（塘的占地不算）、最大积水量与时刻、末帧域内留存
  · 每个热点 200 m 环：最大水深、≥0.10 / ≥0.30 m 面积、≥0.15 m 面积×时间（ha·h）、≥0.10 m 持续时间（h）
  · 相对 L2+planned 变深 ≥5 cm 的面积（副作用，C1 v4 / v5 判据）
  · 水量对账（twoway_*.json）、一维（rpt）：出水口出流、节点溢流、塘满度、泵运行
用法：python tools/rb01_post.py [--tag H61]
产物：out/rb01_stats_{tag}.json、.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from rb01_build import HS  # noqa: E402
from rb01_design_l2s import pond_cells, pump_util  # noqa: E402
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
STATES = ["L1", "L2", "L2P", "L2S4", "L2S5"]
NAMES = {"L1": "L1 现状", "L2": "L2 移动泵", "L2P": "L2+planned（PO 塘方案，来源待确认）", "L2S4": "L2+suggested v4（中间步）", "L2S5": "L2+suggested v5"}
R_HS = 200.0


def rpt_info(rpt):
    t = pathlib.Path(rpt).read_text(errors="ignore")
    g = lambda k: float(re.search(k + r"\s*\.+\s+(-?[\d.]+)\s+(-?[\d.]+)", t).group(2)) * 1000.0   # noqa: E731
    out = {"inflow_m3": g("External Inflow"), "outflow_m3": g("External Outflow"), "flooding_m3": g("Flooding Loss"),
           "final_stored_m3": g("Final Stored Volume")}
    m = re.search(r"Storage Volume Summary.*?-{10,}\s*\n.*?-{10,}\s*\n(.*?)\n\s*\n", t, re.S)
    out["ponds"] = {q.split()[0]: {"max_1000m3": float(q.split()[5]), "max_pct": float(q.split()[6])} for q in (m.group(1).splitlines() if m else []) if q.strip()}
    return out


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", default="H61"); a = ap.parse_args(argv)
    import geopandas as gpd
    name = f"RB01_{a.tag}"
    H = gpd.read_file(ROOT / "console" / "data" / "hotspots.geojson").to_crs(32640).set_index("hotspot_id")
    have = [s for s in STATES if (C2D / f"{name}_{s}_2d.npz").exists() and (C2D / f"twoway_{name}_{s}.json").exists()]
    Z = {s: np.load(C2D / f"{name}_{s}_2d.npz") for s in have}
    xy = Z[have[0]]["xy"].astype(float); area = Z[have[0]]["area"].astype(float); t_h = Z[have[0]]["t_h"]
    dt = float(np.diff(t_h).mean())
    # 所有态共用一个「塘」掩膜：L2S5 的新塘也算塘（不计入积水）
    pm = np.zeros(len(xy), bool)
    for s in have:
        pm |= pond_cells(json.loads((C2D / f"pilot_{name}_{s}.json").read_text(encoding="utf-8")), xy)
    ring = {h: np.hypot(xy[:, 0] - H.loc[h].geometry.x, xy[:, 1] - H.loc[h].geometry.y) <= R_HS for h in HS}
    R = {"tag": a.tag, "states": {}, "note": "积水面积不含塘（规划塘与 v5 新塘）占地；热点环 = 热点 200 m 内"}
    for s in have:
        z = Z[s]; hm = z["hmax"].astype(float); fr = z["frames"].astype(np.float32)
        tw = json.loads((C2D / f"twoway_{name}_{s}.json").read_text(encoding="utf-8"))
        ok = ~pm
        V = (fr[:, ok] * area[ok]).sum(1)
        st = {"name": NAMES[s], "area_ge010_ha": round(float(area[ok & (hm >= 0.10)].sum()) / 1e4, 2),
              "area_ge030_ha": round(float(area[ok & (hm >= 0.30)].sum()) / 1e4, 2),
              "max_vol_m3": round(float(V.max())), "max_vol_t_h": round(float(t_h[int(V.argmax())]), 2),
              "end_vol_m3": round(float(V[-1])), "hmax_m": round(float(hm[ok].max()), 2),
              "balance": {k: round(v) for k, v in tw["acc_m3"].items()} | {"boundary_out": round(tw["boundary_out_m3"]), "final_2d": round(tw["final_vol_m3"])},
              "wall_s": round(tw["wall_s"]), "hs": {}}
        st["rpt"] = rpt_info(C2D / f"{name}_{s}_2way.rpt")
        for h in HS:
            c = ring[h] & ok
            hc = fr[:, c]
            st["hs"][h] = {"hmax_m": round(float(hm[c].max()), 2) if c.any() else None,
                           "ge010_ha": round(float(area[c & (hm >= 0.10)].sum()) / 1e4, 3),
                           "ge030_ha": round(float(area[c & (hm >= 0.30)].sum()) / 1e4, 3),
                           "ge015_ha_h": round(float(((hc >= 0.15) * area[c]).sum() * dt / 1e4), 3),
                           "ge010_dur_h": round(float((hc >= 0.10).any(1).sum() * dt), 2)}
        if s != "L2P" and "L2P" in Z:
            d = hm - Z["L2P"]["hmax"].astype(float)
            st["vs_L2P"] = {"deeper_ge005_ha": round(float(area[ok & (d >= 0.05)].sum()) / 1e4, 2),
                            "shallower_ge005_ha": round(float(area[ok & (d <= -0.05)].sum()) / 1e4, 2), "max_deeper_m": round(float(d[ok].max()), 2)}
        if s.startswith("L2S"):
            st["pumps"] = {k: {"pct": u, "m3": round(v)} for k, (u, v) in pump_util(C2D / f"{name}_{s}_2way.rpt").items()}
            st["pumps2d"] = tw["pumps2d"]; st["pond_rows"] = tw["ponds"]
        R["states"][s] = st
    (ROOT / "out" / f"rb01_stats_{a.tag}.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(ROOT / "out" / f"rb01_stats_{a.tag}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["态", "范围", "最大水深 m", "≥0.10 m 面积 ha", "≥0.30 m 面积 ha", "≥0.15 m 面积×时间 ha·h", "≥0.10 m 持续 h",
                    "最大积水量 m³", "末帧积水量 m³", "比 L2+planned 变深 ≥5 cm ha"])
        for s, st in R["states"].items():
            w.writerow([st["name"], "全域（塘外）", st["hmax_m"], st["area_ge010_ha"], st["area_ge030_ha"], "", "", st["max_vol_m3"], st["end_vol_m3"],
                        st.get("vs_L2P", {}).get("deeper_ge005_ha", "")])
            for h, v in st["hs"].items():
                w.writerow([st["name"], f"{h} 200 m 内", v["hmax_m"], v["ge010_ha"], v["ge030_ha"], v["ge015_ha_h"], v["ge010_dur_h"], "", "", ""])
    for s, st in R["states"].items():
        print(f"{s:<5} ≥0.30 {st['area_ge030_ha']:>6} ha  ≥0.10 {st['area_ge010_ha']:>6} ha  最大积水 {st['max_vol_m3']:>8,} m³ @ {st['max_vol_t_h']} h  "
              f"末 {st['end_vol_m3']:>8,}  溢流 {st['rpt']['flooding_m3']:>8,.0f}  出流 {st['rpt']['outflow_m3']:>8,.0f}" +
              (f"  比 L2P 变深 {st['vs_L2P']['deeper_ge005_ha']} ha" if "vs_L2P" in st else ""))
        print("      " + "  ".join(f"{h[4:]}:{v['hmax_m']}/{v['ge030_ha']}/{v['ge015_ha_h']}/{v['ge010_dur_h']}" for h, v in st["hs"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
