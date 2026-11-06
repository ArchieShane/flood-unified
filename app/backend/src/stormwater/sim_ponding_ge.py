"""全市一二维任务各态「水深 ≥ 阈值」的积水量（离线算一次，写进成果目录 ponding_ge.json）。

口径与应对设备配置文件（261029）一致：对 60 帧逐帧算 Σ max(水深 − 阈值, 0) × 网格面积，取最大值。
L2 算出来是 5,952,424 m³，正好等于设备配置文件里的「L2 后剩余积水」，用它当口径对账。

用法：PYTHONPATH=src python -m stormwater.sim_ponding_ge <输出文件> L1=<L1 的 *_2d.npz> L2=<L2 的 *_2d.npz> [阈值米]
"""
from __future__ import annotations

import json
import sys

import numpy as np

THRESHOLD_M = 0.15


def peak_volume_ge(npz_path: str, threshold_m: float = THRESHOLD_M) -> float:
    z = np.load(npz_path)
    area = z["area"].astype("f8")
    frames = z["frames"]
    return max(float((np.clip(frames[i].astype("f8") - threshold_m, 0, None) * area).sum()) for i in range(frames.shape[0]))


def main(argv: list[str]) -> None:
    out, *rest = argv
    th = THRESHOLD_M
    states = {}
    for a in rest:
        if "=" in a:
            k, p = a.split("=", 1)
            states[k] = p
        else:
            th = float(a)
    vol = {k: round(peak_volume_ge(p, th)) for k, p in states.items()}
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"threshold_m": th, "volume_m3": vol,
                   "basis": "max over frames of sum((depth - threshold) x cell area), cells with depth >= threshold"},
                  f, ensure_ascii=False, indent=1)
    print(vol)


if __name__ == "__main__":
    main(sys.argv[1:])
