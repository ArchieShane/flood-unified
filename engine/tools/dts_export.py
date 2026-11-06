# -*- coding: utf-8 -*-
"""一个运行的各态 → 飞渡 DTS 文件夹（自定义计算域的运行没有专门的交付脚本，后处理用这个）。

用法：python tools/dts_export.py --run C1_<运行名> [--states L1,L2] [--out out/交付/<运行名>]
产物：<out>/<态>/飞渡DTS/（shp/ + dat/ + h2d_index.json）、<out>/飞渡DTS说明.md；逻辑在 swbrain/dts_h2d.py
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from swbrain import dts_h2d  # noqa: E402
from workdir import WORK  # noqa: E402


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--states", default="", help="逗号分隔；缺省 = c2d/anuga_out 下这个运行有 .sww 的全部态")
    ap.add_argument("--out", default=None, help="缺省 out/交付/<运行名>")
    a = ap.parse_args(argv)
    sdir = WORK / "c2d" / "anuga_out"
    states = [s for s in a.states.split(",") if s] or sorted(p.stem[len(a.run) + 1:] for p in sdir.glob(f"{a.run}_*.sww")
                                                             if "_" not in p.stem[len(a.run) + 1:])
    if not states:
        raise SystemExit(f"{sdir} 下没有 {a.run}_*.sww")
    out = pathlib.Path(a.out) if a.out else ROOT / "out" / "交付" / a.run
    rows = dts_h2d.export_delivery(a.run, sdir, {s: out / s for s in states}, out)
    print(f"→ {out}：飞渡DTS {len(rows)} 个态")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
