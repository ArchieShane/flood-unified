# -*- coding: utf-8 -*-
"""只重出某几态的逐帧 PNG（含 max.png），不动 manifest 里的帧统计。

为什么要有它：pilot_frames.py 会整份重写 manifest / stats.json / out/pilot_*.json，
而有时只是图出错了（例如 2026-10-29 新参数四态被画成了不透明白底大图，盖住整张底图），
统计数是对的、不该重算。这里用和 pilot_frames 同一个 render()（同色标、同出图阈值），
逐帧覆盖 PNG；bounds 若与 manifest 不一致就改成新值并报出来。

用法：python tools/rerender_frames.py --name RDAG_A61D1 --states L1F,L2F --out flood2d_rda_a61d1
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from pilot_frames import render  # noqa: E402
from workdir import WORK  # noqa: E402


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--states", required=True, help="逗号分隔")
    ap.add_argument("--out", required=True, help="console/data 下的目录")
    a = ap.parse_args(argv)
    out = ROOT / "console" / "data" / a.out
    mf = out / "manifest.json"
    man = json.loads(mf.read_text(encoding="utf-8"))
    by = {s["state"]: s for s in man["states"]}
    changed = False
    for st in a.states.split(","):
        z = np.load(WORK / "c2d" / f"{a.name}_{st}_2d.npz")
        tw = json.loads((WORK / "c2d" / f"twoway_{a.name}_{st}.json").read_text(encoding="utf-8"))
        xy, fr, hmax, cell = z["xy"].astype(float), z["frames"], z["hmax"], float(tw["res_m"])
        d = out / st
        d.mkdir(parents=True, exist_ok=True)
        ent = by.get(st)
        if ent is not None and len(ent["frames"]) != len(fr):
            print(f"  {st}: manifest {len(ent['frames'])} 帧 ≠ npz {len(fr)} 帧，跳过"); continue
        bounds = None
        for i in range(len(fr)):
            bounds = render(xy, fr[i].astype(float), cell, d / f"f{i:03d}.png")
        render(xy, hmax.astype(float), cell, d / "max.png")
        if ent is not None and ent["bounds"] != bounds:
            print(f"  {st}: bounds {ent['bounds']} → {bounds}")
            ent["bounds"] = bounds
            changed = True
        print(f"  {st}: 重出 {len(fr)} 帧 + max.png")
    if changed:
        shutil.copy2(mf, mf.with_suffix(".json.pre_rerender"))
        mf.write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
        print("  manifest bounds 已更新（原件 manifest.json.pre_rerender）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
