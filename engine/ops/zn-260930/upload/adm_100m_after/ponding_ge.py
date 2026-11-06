# -*- coding: utf-8 -*-
"""各态「水深 ≥ 阈值」的积水量 → 交付根目录的 ponding_ge.json（management 页面「需要排 → 可以排 → 排完剩余」模块读它）。

口径（和 app/backend/src/stormwater/sim_ponding_ge.py 一致）：对每一帧算 Σ max(水深 − 阈值, 0) × 单元面积，取各帧最大值，单位 m³。
需要排的总积水量 = 基线态（第一态，通常 L1），排完剩余 = 本态，可以排 = 基线 − 本态，页面自己算。

输出 {"threshold_m": 0.15, "volume_m3": {"L1": …, "L2": …}}。键必须和交付里各态的 key 一致（页面按 run.states[].key 取）。

用法：
  python tools/ponding_ge.py <交付目录> L1=<L1 的 *_2d.npz> [L2=<…>] [L2+planned=<…>] [--th 0.15]
  python tools/ponding_ge.py <交付目录> --auto            # 在交付里找 *_<态>_2d.npz / *_二维水深帧与最大水深.npz
别的脚本里：from ponding_ge import write_ponding_ge；write_ponding_ge(dir, {"L1": npz, "L2": npz})，失败只打印不抛（不挡交付）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

THRESHOLD_M = 0.15
FILE = "ponding_ge.json"
# 文件名里的态记号 → 态 key（旧口径的 L2P / L2S3 沿用旧 key，新口径 L2planned 直接用）
STATE_KEYS = {"L1": "L1", "L2": "L2", "L2P": "L2+planned", "L2PLANNED": "L2planned", "L2S3": "L2+suggested"}


def peak_volume_ge(npz_path, threshold_m: float = THRESHOLD_M) -> float:
    z = np.load(npz_path)
    area = z["area"].astype("f8")
    frames = z["frames"]
    return max(float((np.clip(frames[i].astype("f8") - threshold_m, 0, None) * area).sum()) for i in range(frames.shape[0]))


def write_ponding_ge(out_dir, npz_by_state: dict, threshold_m: float = THRESHOLD_M) -> Path | None:
    """算各态并写 <out_dir>/ponding_ge.json；没有 npz 的态跳过，一个都没有或出错返回 None（只打印）。"""
    try:
        vol = {}
        for st, p in npz_by_state.items():
            if p and Path(p).is_file():
                vol[st] = round(peak_volume_ge(p, threshold_m))
        if not vol:
            print("ponding_ge：没有可用的 npz，跳过", file=sys.stderr)
            return None
        out = Path(out_dir) / FILE
        out.write_text(json.dumps({"threshold_m": threshold_m, "volume_m3": vol,
                                   "basis": "max over frames of sum((depth - threshold) x cell area), cells with depth >= threshold"},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"ponding_ge：{vol} → {out}")
        return out
    except Exception as e:      # 不挡交付
        print(f"ponding_ge 失败：{type(e).__name__}: {e}", file=sys.stderr)
        return None


def discover(root) -> dict:
    """在交付里按文件名找各态 npz：…_<L1|L2|L2P|L2S3>_2d.npz，或 <态目录>/一二维/*_二维水深帧与最大水深.npz（态由上级目录名前缀判断）。"""
    root, found = Path(root), {}
    for p in sorted(root.rglob("*.npz")):
        if p.name.startswith("._"):
            continue
        m = re.search(r"_(L2S3|L2PLANNED|L2P|L2|L1)_2d\.npz$", p.name, re.I)
        if m:
            found.setdefault(STATE_KEYS[m.group(1).upper()], p)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("delivery")
    ap.add_argument("pairs", nargs="*", help="态=npz 路径，如 L1=/x/AF_L1_2d.npz")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--th", type=float, default=THRESHOLD_M)
    a = ap.parse_args()
    pairs = dict(x.split("=", 1) for x in a.pairs if "=" in x)
    if a.auto or not pairs:
        pairs = {**discover(a.delivery), **pairs}
    return 0 if write_ponding_ge(a.delivery, pairs, a.th) else 1


if __name__ == "__main__":
    raise SystemExit(main())
