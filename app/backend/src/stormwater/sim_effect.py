"""多态任务的方案效果：同一时刻，各态比基线少上地表多少水、少了几个溢水点（`/states/effect`）。

起因（2026-09-27，Mussafah #191）：四态的退水直方图全灰——溢水点都是封闭洼地里的死水，
演算末一个都没退干，退水时间说明不了方案。方案起作用的方式是**少往地表溢**，不是让地表水退得快
（L1 峰值 12,615 m³ 到 10 h 仍有 10,660 m³）。所以按时刻切片、逐态对基线做减法。

## 口径

- 基线 = `states.json` 的第一态（Mussafah 是 L1 既有管网）；溢水点清单 = 基线的 `sim_ponding` 片区，
  其它态**不另识别**，逐点用基线片区的格子号去各态同一时刻的水深里取数——点对点、不用匹配。
- 时刻档 `CHECKPOINTS_H`（自降雨开始），默认 2 h = 三道防线共用时限 T。某态没演算到这一时刻就
  `reached = False`、数字全空，不拿别的时刻顶替。各态演算时长可以不同（Mussafah L1 10 h、L2+A / L2+B 5 h），
  这正是不用「演算全程」口径的原因：入选判据里的「≥ 0.15 m 持续 ≥ 3 h」吃演算时长，10 h 的基线对 5 h 的
  方案会平白多出 3 个点。
- 某时刻「仍积水」= 片区内最深格水深 ≥ `ACTIVE_DEPTH_M`（0.15 m，即入选判据的持续水深）。
  「消除」= 基线此刻仍积水、本态此刻不积水；反过来的记「新增」（方案把水挤到别处的情形照实列）。
- 地表积水量 = 该帧水深 ≥ `WET_M` 的格 Σ 水深 × 格面积（与出图阈值一致）；片区积水量不设阈值。
- 基线片区以外、本态自己识别出的新片区另列 `outside_points`（全程口径，与时刻无关）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from . import sim_ponding as sp

CHECKPOINTS_H = (2.0, 4.0, 5.0)
DEFAULT_CHECKPOINT_H = 2.0
ACTIVE_DEPTH_M = sp.SELECT_DUR_DEPTH_M
WET_M = sp.WET_M
STATES_FILE = "states.json"


class EffectError(Exception):
    pass


def _state_dirs(root: Path) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    man = root / STATES_FILE
    if not man.is_file():
        raise EffectError("不是多态交付")
    states = json.loads(man.read_text(encoding="utf-8"))["states"]
    if len(states) < 2:
        raise EffectError("只有一态，没有可比的方案")
    dirs = {s["key"]: (root / s["dir"]).resolve() for s in states}
    for k, d in dirs.items():
        if not (d / "delivery_summary.json").is_file():
            raise EffectError(f"{k} 没有成果")
    return states, dirs


def _frames(d: Path) -> tuple[np.ndarray, np.ndarray, float]:
    s = sp._summary(d)
    g = sp._grid(d, s)
    D, times = sp._depth_matrix(d, s, g["nr"] * g["nc"])
    return D, times, g["cs"] * g["cs"]


def _at(times: np.ndarray, minutes: float) -> int | None:
    """离 `minutes` 最近的一帧；超出演算末（差过半个帧距）就是没演算到。"""
    if len(times) == 0:
        return None
    step = float(np.median(np.diff(times))) if len(times) > 1 else 0.0
    i = int(np.argmin(np.abs(times - minutes)))
    return i if abs(times[i] - minutes) <= step / 2 + 1e-6 else None


def compute(root: Path) -> dict[str, Any]:
    states, dirs = _state_dirs(root)
    base_key = states[0]["key"]
    base = sp.load(dirs[base_key])
    pts = base["clusters"]
    cells = [np.asarray(c["cells"], np.int64) for c in pts]
    base_cells = set(int(x) for c in pts for x in c["cells"])

    frames: dict[Path, tuple] = {}          # L2 ≡ L1 硬链同一份，同目录只读一次
    lanes = []
    for st in states:
        d = dirs[st["key"]]
        D, times, cell_a = frames.setdefault(d, _frames(d))
        cps = []
        for h in CHECKPOINTS_H:
            i = _at(times, h * 60)
            if i is None:
                cps.append({"t_h": h, "reached": False})
                continue
            f = D[i]
            vol = [round(float(f[c].sum() * cell_a), 1) for c in cells]
            dep = [round(float(f[c].max()), 3) for c in cells]
            cps.append({
                "t_h": h, "reached": True, "time_minutes": float(times[i]),
                "surface_volume_m3": round(float(f[f >= WET_M].sum() * cell_a), 1),
                "points_volume_m3": round(sum(vol), 1),
                "active": [x >= ACTIVE_DEPTH_M for x in dep],
                "volume_m3": vol,
                "depth_m": dep,
            })
        own = sp.load(d)["clusters"] if st["key"] != base_key else []
        outside = [{"rank": c["rank"], "name": c.get("name"), "lat": c["lat"], "lon": c["lon"],
                    "max_depth_m": c["max_depth_m"]}
                   for c in own if not base_cells.intersection(c["cells"])]
        lanes.append({"key": st["key"], "label": st["label"], "defence_line_key": st["defence_line_key"],
                      "sim_end_minutes": float(times[-1]), "checkpoints": cps, "outside_points": outside})

    # 逐时刻对基线做减法
    b = lanes[0]["checkpoints"]
    for ln in lanes:
        for k, cp in enumerate(ln["checkpoints"]):
            if not cp["reached"] or not b[k]["reached"]:
                continue
            bv, bact = b[k]["surface_volume_m3"], b[k]["active"]
            cp["volume_less_m3"] = round(bv - cp["surface_volume_m3"], 1)
            cp["volume_less_pct"] = round(100 * (bv - cp["surface_volume_m3"]) / bv, 1) if bv > 0 else None
            cp["base_active_count"] = int(sum(bact))
            cp["active_count"] = int(sum(cp["active"]))
            cp["cleared"] = [pts[j]["rank"] for j in range(len(pts)) if bact[j] and not cp["active"][j]]
            cp["added"] = [pts[j]["rank"] for j in range(len(pts)) if cp["active"][j] and not bact[j]]

    return {
        "base": base_key,
        "checkpoints_h": list(CHECKPOINTS_H),
        "default_h": DEFAULT_CHECKPOINT_H,
        "active_depth_m": ACTIVE_DEPTH_M,
        "wet_m": WET_M,
        "points": [{"rank": c["rank"], "name": c.get("name"), "lat": c["lat"], "lon": c["lon"],
                    "cell_id": c["cell_id"], "max_depth_m": c["max_depth_m"]} for c in pts],
        "states": lanes,
    }


@lru_cache(maxsize=8)
def _cached(root: str, stamp: tuple) -> str:
    return json.dumps(compute(Path(root)), ensure_ascii=False)


def load(root: Path) -> dict[str, Any]:
    """进程内按「目录 + 各态摘要 mtime」缓存（首次要读几态全部快照，几秒）；每次返回独立副本。"""
    root = root.resolve()
    _, dirs = _state_dirs(root)
    stamp = tuple(sorted((k, (d / "delivery_summary.json").stat().st_mtime) for k, d in dirs.items()))
    return json.loads(_cached(str(root), stamp))
