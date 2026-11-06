"""热点 200 m 内退水时间（Tatica 问询 Hotspot drainage 页 L 列），读 result-017 的 L1 .sww（每 5 min 一帧）。

  python tools/tatica_recession.py <result-017.tar> <解包目录> <输出 json>

每个热点：形心在热点坐标 200 m 内的三角单元，水深 = stage_c − elevation_c；
最后一帧仍有单元 ≥ 0.10 m → "> 5"；否则给最后一个 ≥ 0.10 m 的帧之后那一帧的时刻（h）。
.sww 逐个解出、算完即删。
"""
import json
import subprocess
import sys
from pathlib import Path

import netCDF4
import numpy as np

THRESH, RADIUS = 0.10, 200.0


def recession(sww, hotspots):
    d = netCDF4.Dataset(sww)
    x = d.variables["x"][:] + d.xllcorner
    y = d.variables["y"][:] + d.yllcorner
    tri = d.variables["volumes"][:]
    cx, cy = x[tri].mean(axis=1), y[tri].mean(axis=1)
    zc = d.variables["elevation_c"][:]
    t = d.variables["time"][:] / 3600.0
    out = []
    for h in hotspots:
        m = np.flatnonzero(np.hypot(cx - h["x"], cy - h["y"]) <= RADIUS)
        if not len(m):
            out.append({"hotspot": h["hotspot_id"], "cells": 0}); continue
        dep = d.variables["stage_c"][:, m] - zc[m]
        wet = (dep >= THRESH).any(axis=1)
        if not wet.any():
            r = 0.0
        elif wet[-1]:
            r = "> 5"
        else:
            last = int(np.flatnonzero(wet)[-1])
            r = float(round(t[last + 1], 3))
        out.append({"hotspot": h["hotspot_id"], "cells": int(len(m)), "first_wet_h": float(t[np.argmax(wet)]) if wet.any() else None,
                    "dry_h": r, "hmax_m": float(dep.max())})
    d.close()
    return out


def main(tar, work, out):
    work = Path(work)
    names = subprocess.run(["tar", "-tf", tar], capture_output=True, text=True, check=True).stdout.split()
    res = {}
    for n in [n for n in names if n.endswith("_L1.sww")]:
        folder = n.split("/")[2]
        subprocess.run(["tar", "-xf", tar, "-C", str(work), n], check=True)
        f = work / n
        hs = json.loads((f.parents[2] / "common" / "hotspots.json").read_text(encoding="utf-8"))
        res[folder] = recession(f, hs)
        f.unlink()
        print(folder, len(hs), "hotspots", flush=True)
    Path(out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1] == "--one":
        print(json.dumps(recession(sys.argv[2], json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))), indent=1))
    else:
        main(*sys.argv[1:4])
