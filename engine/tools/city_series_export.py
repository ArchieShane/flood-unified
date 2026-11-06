# -*- coding: utf-8 -*-
"""全市一维 L1 / L2 的逐时过程 → 控制台播放用的精简数据（2026-09-28）。

来源：tools/run_city1d.py L1 L2 --tag _v2ts（8 片 × 2 态，6 h 窗口，每 5 分钟存一帧逐井结果）。
  · 500 m 方格：每 5 分钟、每格从井盖冒出的水量（m³ / 5 min）。7 万口冒水井逐井逐帧装不进浏览器，按格汇总
  · 全市溢流速率曲线（m³/s），L1 / L2
  · L2 的塘：每 5 分钟的水深 → 按 TABULAR 曲线积出蓄水量，和设计库容比
口径与 city_gap.py 一致：**L2 只在接塘的管网上取 L2 自己的数，其余取 L1 的**（分片不同带来的数值噪声记 0）。
这一轮 6 h 窗口的连续性误差最大 5.5%（30 h 窗口那轮是 4.5%）：数用来看过程，全市总量以 city_gap_v2 为准。

产物：console/data/city_series.json
用法：python tools/city_series_export.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from city_gap import pond_networks, xy_of  # noqa: E402
from workdir import WORK  # noqa: E402

TAG, PARTS, CELL = "_v2ts", 8, 500.0


def _curve_vol(inp: pathlib.Path) -> dict:
    """塘节点 → (底标高, 最大水深, 水深→蓄量的函数)。"""
    t = inp.read_text(encoding="utf-8", errors="replace")
    sec = lambda n: re.search(r"\[" + n + r"\]\n(.*?)(?:\n\[|\Z)", t, re.S)
    st = sec("STORAGE"); cv = sec("CURVES")
    curves = {}
    for ln in (cv.group(1).splitlines() if cv else []):
        p = ln.split()
        if len(p) < 3 or ln.startswith(";"):
            continue
        vals = p[2:] if not re.match(r"^-?[\d.]+$", p[1]) else p[1:]
        c = curves.setdefault(p[0], [])
        for i in range(0, len(vals) - 1, 2):
            c.append((float(vals[i]), float(vals[i + 1])))
    out = {}
    for ln in (st.group(1).splitlines() if st else []):
        p = ln.split()
        if len(p) > 5 and p[0].startswith("PND_") and p[4].upper() == "TABULAR":
            pts = sorted(curves.get(p[5], [(0, 0)]))
            d = np.array([x for x, _ in pts]); a = np.array([y for _, y in pts])
            dd = np.linspace(0, float(p[2]), 200); aa = np.interp(dd, d, a)
            vol = np.concatenate([[0], np.cumsum((aa[1:] + aa[:-1]) / 2 * np.diff(dd))])
            out[p[0]] = (float(p[2]), dd, vol)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    from pyproj import Transformer
    from swmm.toolkit import output, shared_enum as se
    tr = Transformer.from_crs("EPSG:32640", "EPSG:4326", always_xy=True)
    aff, n_aff = pond_networks(TAG, PARTS)
    cells: dict[str, int] = {}
    series = {"L1_all": {}, "L1_aff": {}, "L2_aff": {}}
    sysr = {"L1": None, "L1_aff": None, "L2_aff": None}
    ponds = []
    t_h = None
    for st in ("L1", "L2"):
        for i in range(1, PARTS + 1):
            f = WORK / f"City_{st}{TAG}_p{i}.out"
            h = output.init(); output.open(h, str(f))
            try:
                n = output.get_times(h, se.Time.NUM_PERIODS); step = output.get_times(h, se.Time.REPORT_STEP)
                t_h = np.arange(1, n + 1) * step / 3600.0
                nn = output.get_proj_size(h)[1]
                names = [output.get_elem_name(h, se.ElementType.NODE, k) for k in range(nn)]
                cix = np.full(nn, -1); inaff = np.zeros(nn, bool)
                for k, nm in enumerate(names):
                    p = xy_of(nm)
                    if p:
                        key = f"{int(p[0] // CELL)}_{int(p[1] // CELL)}"
                        cix[k] = cells.setdefault(key, len(cells))
                    inaff[k] = nm in aff
                F = np.array([output.get_node_attribute(h, j, se.NodeAttribute.FLOODING_LOSSES) for j in range(n)]) * step   # m³ / 5 min
                ok = cix >= 0
                for tag, mask in ((("L1_all", ok),) if st == "L1" else ()) + ((f"{st}_aff", ok & inaff),):
                    acc = series[tag]
                    for k in np.flatnonzero(mask & (F.max(axis=0) > 0)):
                        c = int(cix[k]); acc[c] = acc.get(c, 0) + F[:, k]
                    s = F[:, mask].sum(axis=1)
                    key = "L1" if tag == "L1_all" else tag
                    sysr[key] = s if sysr[key] is None else sysr[key] + s
                if st == "L2":
                    cv = _curve_vol(WORK / f"City_L2{TAG}_p{i}.inp")
                    for k, nm in enumerate(names):
                        if nm in cv:
                            dmax, dd, vol = cv[nm]
                            dep = np.array([output.get_node_attribute(h, j, se.NodeAttribute.INVERT_DEPTH)[k] for j in range(n)])
                            v = np.interp(np.clip(dep, 0, dmax), dd, vol)
                            ponds.append({"id": nm[4:] if nm.startswith("PND_") else nm, "cap_m3": round(float(vol[-1])), "vol_m3": [round(float(x)) for x in v],
                                          "max_fill_pct": round(float(v.max() / vol[-1] * 100), 1) if vol[-1] else None})
            finally:
                output.close(h)
            print(f"  {st} p{i}：节点 {nn:,}")
    # L2 = L1 全部 − L1 接塘网 + L2 接塘网（与 city_gap 同一口径）
    idx = sorted(set(series["L1_all"]) | set(series["L2_aff"]))
    inv = {v: k for k, v in cells.items()}
    z = np.zeros(len(t_h))
    rows = []
    for c in idx:
        a = series["L1_all"].get(c, z); b = a - series["L1_aff"].get(c, z) + series["L2_aff"].get(c, z)
        if max(a.max(), b.max()) < 1:
            continue
        gx, gy = (int(v) for v in inv[c].split("_"))
        lo0, la0 = tr.transform(gx * CELL, gy * CELL); lo1, la1 = tr.transform((gx + 1) * CELL, (gy + 1) * CELL)
        rows.append({"b": [round(la0, 5), round(lo0, 5), round(la1, 5), round(lo1, 5)],
                     "L1": [int(round(x)) for x in a], "L2": [int(round(max(x, 0))) for x in b]})
    s1 = sysr["L1"]; s2 = s1 - sysr["L1_aff"] + sysr["L2_aff"]
    out = {"tag": TAG, "cell_m": CELL, "step_min": 5, "t_h": [round(float(x), 3) for x in t_h], "n_pond_networks": n_aff,
           "unit": "m³ / 5 min", "note": "L2 只在接塘的管网上取自己的数；6 h 窗口连续性误差最大 5.5%，看过程用，全市总量以 city_gap_v2 为准",
           "sys": {"L1": [round(float(x) / 300, 3) for x in s1], "L2": [round(float(x) / 300, 3) for x in s2], "unit": "m³/s"},
           "cells": rows, "ponds": sorted(ponds, key=lambda p: -p["cap_m3"])}
    f = ROOT / "console" / "data" / "city_series.json"
    f.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    pk = int(np.argmax(s1))
    print(f"方格 {len(rows):,}、塘 {len(ponds)}；全市溢流速率峰值 L1 {s1[pk] / 300:.1f} m³/s @ {t_h[pk]:.2f} h；"
          f"累计 L1 {s1.sum():,.0f} / L2 {s2.sum():,.0f} m³ → {f}（{f.stat().st_size / 1048576:.1f} MB）")
    for p in out["ponds"][:8]:
        print(f"    {p['id']:<26} 库容 {p['cap_m3']:>9,} m³  最高 {p['max_fill_pct']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
