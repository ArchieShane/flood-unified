# -*- coding: utf-8 -*-
"""L2+suggested 优化试点 · 一二维验证打分：按方案第 3 节 / 12.1 的验收判据，逐热点、逐条给通过 / 不通过。

判据（阈值是 12.1 的执行值）：
  A 热点 200 m 内 ≥0.30 m 积水面积 = 0（容差 0.005 ha）
  B 热点 200 m 内 ≥0.15 m 面积×时间 ≤ L2+planned 同值的 10 %
  C 雨停后 6 h 内热点 200 m 内最大水深退到 < 0.10 m
  D 全域比 L2+planned 变深 ≥5 cm 的面积 ≤ 0.05 ha（塘占地不算）
基线 = 同一场雨的 L2+planned（RB01_<雨>_L2P）；塘掩膜 = 两个态的塘占地并集。
用法：python tools/l2opt_eval2d.py --storm S96D1 --states L2P,V5B,OPT1 [--hs ADM-101,...]
产物：out/l2opt/RB01_H61/eval2d_<雨>.json（追加 / 覆盖同名态）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from rb01_design_l2s import pond_cells  # noqa: E402
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"
HS6 = "ADM-101,ADM-140,ADM-205,ADM-206,ADM-291,ADM-293"
TOL_A, FRAC_B, REC_H, DEEP_M, DEEP_HA = 0.005, 0.10, 6.0, 0.05, 0.05


def load(name, st):
    z = np.load(C2D / f"{name}_{st}_2d.npz")
    meta = json.loads((C2D / f"pilot_{name}_{st}.json").read_text(encoding="utf-8"))
    return z, meta


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--storm", required=True); ap.add_argument("--states", required=True); ap.add_argument("--hs", default=HS6)
    ap.add_argument("--run", default="RB01_H61")
    ap.add_argument("--prefix", default="RB01", help="模型名前缀：RB01 / RDA（2026-10-20 起 RD-A 共用这套脚本）")
    ap.add_argument("--exclude", default="", help="评估剔除区 geojson（UTM 40N 面）：数据问题造成的假积水（如 DTM 填 0），所有态同样剔除；结果另存 eval2d_<雨>_ex.json")
    a = ap.parse_args(argv)
    import geopandas as gpd
    name = f"{a.prefix}_{a.storm}"
    info = json.loads((ROOT / "out" / f"{a.prefix.lower()}_inputs_{a.storm}_L1.json").read_text(encoding="utf-8"))
    rain_end = float(info["dur_h"])
    H = __import__("hotspot_src").load(32640).set_index("hotspot_id")
    hs = a.hs.split(",")
    zb, mb = load(name, "L2P")
    xy = zb["xy"].astype(float); area = zb["area"].astype(float); t = zb["t_h"]; dt = float(np.diff(t).mean())
    ring = {h: np.hypot(xy[:, 0] - H.loc[h].geometry.x, xy[:, 1] - H.loc[h].geometry.y) <= 200 for h in hs}
    hb = zb["hmax"].astype(float); fb = zb["frames"].astype(np.float32)
    pm_b = pond_cells(mb, xy)
    if a.exclude:                                                         # 剔除区当作「塘占地」一样不计（2026-10-24）
        import shapely
        from shapely.geometry import shape
        ex = shapely.union_all([shape(f["geometry"]) for f in json.loads(pathlib.Path(a.exclude).read_text(encoding="utf-8"))["features"]])
        exm = shapely.contains_xy(ex, xy[:, 0], xy[:, 1]) if not ex.is_empty else np.zeros(len(xy), bool)
        print(f"  剔除区 {exm.sum()} 格 {area[exm].sum() / 1e4:.2f} ha")
        pm_b = pm_b | exm
    of = ROOT / "out" / "l2opt" / a.run / (f"eval2d_{a.storm}_ex.json" if a.exclude else f"eval2d_{a.storm}.json")
    R = json.loads(of.read_text(encoding="utf-8")) if of.exists() else {"storm": a.storm, "states": {}}
    base_hs = {}
    for h in hs:
        c = ring[h] & ~pm_b
        base_hs[h] = float(((fb[:, c] >= 0.15) * area[c]).sum() * dt / 1e4)
    for st in a.states.split(","):
        z, m = (zb, mb) if st == "L2P" else load(name, st)
        if len(z["xy"]) != len(xy):
            print(f"  ! {st} 网格和 L2P 不同，跳过"); continue
        hm = z["hmax"].astype(float); fr = z["frames"].astype(np.float32)
        pm = pm_b | pond_cells(m, xy); ok = ~pm
        rec = {"hotspots": {}, "domain": {}}
        allpass = True
        for h in hs:
            c = ring[h] & ok
            hc = fr[:, c]
            a30 = float(area[c & (hm >= 0.30)].sum() / 1e4)
            hah = float(((hc >= 0.15) * area[c]).sum() * dt / 1e4)
            dmax_t = hc.max(1) if c.any() else np.zeros(len(t))
            after = np.flatnonzero((t >= rain_end) & (dmax_t < 0.10))
            wet_after = np.flatnonzero((t >= rain_end) & (dmax_t >= 0.10))
            if not len(wet_after):
                rec_h = 0.0
            elif wet_after[-1] == len(t) - 1:
                rec_h = None                                              # 到模拟结束还没退
            else:
                rec_h = float(t[wet_after[-1] + 1] - rain_end)
            pa = a30 <= TOL_A
            pb = base_hs[h] <= 0.01 or hah <= FRAC_B * base_hs[h]
            pc = rec_h is not None and rec_h <= REC_H
            allpass &= pa and pb and pc
            rec["hotspots"][h] = {"ge030_ha": round(a30, 3), "ge015_ha_h": round(hah, 2), "base_ge015_ha_h": round(base_hs[h], 2),
                                  "recede_h_after_rain": rec_h, "max_m": round(float(hm[c].max()), 2) if c.any() else 0.0,
                                  "pass": {"A": pa, "B": pb, "C": pc}}
        d = hm - hb
        deeper = float(area[ok & (d >= DEEP_M)].sum() / 1e4)
        rec["domain"] = {"ge030_ha": round(float(area[ok & (hm >= 0.30)].sum() / 1e4), 2), "peak_vol_m3": round(float((fr[:, ok] * area[ok]).sum(1).max())),
                         "end_vol_m3": round(float((fr[-1, ok] * area[ok]).sum())), "deeper_ge005_ha": round(deeper, 3),
                         "shallower_ge005_ha": round(float(area[ok & (d <= -DEEP_M)].sum() / 1e4), 2), "pass_D": deeper <= DEEP_HA}
        rec["all_pass"] = bool(allpass and deeper <= DEEP_HA)
        R["states"][st] = rec
        n_ok = sum(all(v["pass"].values()) for v in rec["hotspots"].values())
        print(f"{a.storm} {st:<6} ≥0.30 全域 {rec['domain']['ge030_ha']:>6} ha · 变深 {deeper:.3f} ha · 热点全过 {n_ok}/{len(hs)} · "
              + " ".join(f"{h[4:]}:{v['ge030_ha']:.2f}/{v['ge015_ha_h']:.1f}/{v['recede_h_after_rain'] if v['recede_h_after_rain'] is not None else '未退'}" for h, v in rec["hotspots"].items()))
    of.parent.mkdir(parents=True, exist_ok=True)
    of.write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {of}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
