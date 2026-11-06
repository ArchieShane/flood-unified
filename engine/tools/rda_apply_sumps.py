# -*- coding: utf-8 -*-
"""把一个带地面泵站的态（如 OPTCS2，地面泵站在 l2s_sumps_v3 里按 96 mm 两场雨定好）套到其他雨上（2026-10-24，9 场雨复核用）。

做法：<前缀>_<雨>_<新态>.inp = <前缀>_<雨>_<底态>.inp 原样（底态先用 l2opt_variant 按同一组措施编号建好）；
      pilot_<前缀>_<雨>_<新态>.json = 底态的 pilot + 参考雨同名新态 pilot 里的 pumps2d（取水口、流量、关泵水深都不变）
用法：python tools/rda_apply_sumps.py --ref S96D1 --base OPTC --state OPTCS2 --storms S33D1,S33D2,...
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402

C2D = WORK / "c2d"


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="RDA"); ap.add_argument("--ref", default="S96D1")
    ap.add_argument("--base", required=True); ap.add_argument("--state", required=True); ap.add_argument("--storms", required=True)
    a = ap.parse_args(argv)
    p2 = json.loads((C2D / f"pilot_{a.prefix}_{a.ref}_{a.state}.json").read_text(encoding="utf-8"))["pumps2d"]
    for s in a.storms.split(","):
        name = f"{a.prefix}_{s}"
        shutil.copyfile(WORK / f"{name}_{a.base}.inp", WORK / f"{name}_{a.state}.inp")
        meta = json.loads((C2D / f"pilot_{name}_{a.base}.json").read_text(encoding="utf-8"))
        meta["state"] = a.state
        meta["pumps2d"] = list(meta.get("pumps2d") or []) + p2
        (C2D / f"pilot_{name}_{a.state}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        print(f"  {name}_{a.state}：{a.base} + 地面取水口 {len(p2)} 个（{sum(q['q_m3s'] for q in p2):.2f} m³/s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
