# -*- coding: utf-8 -*-
"""RD-A 状态改名（2026-10-20）：L2+planned 改定义后，旧 L2P（inp 51118 泵站扩容）的产物改名为 L2PA 留作参考。
用法：python tools/rda_rename_state.py --tags S61D24 [--src L2P --dst L2PA]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from workdir import WORK  # noqa: E402


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", required=True); ap.add_argument("--src", default="L2P"); ap.add_argument("--dst", default="L2PA")
    a = ap.parse_args(argv)
    C, R = WORK / "c2d", ROOT / "out"
    n = 0
    for tag in a.tags.split(","):
        nm = f"RDA_{tag}"
        for d, pat in ((WORK, "{nm}_{s}.inp"), (C, "pilot_{nm}_{s}.json"), (C, "twoway_{nm}_{s}.json"), (C, "{nm}_{s}_2d.npz"),
                       (C, "{nm}_{s}_2way.rpt"), (C, "{nm}_{s}_2way.out"), (C / "logs", "{nm}_{s}.log"),
                       (R, f"rda_inputs_{tag}_{{s}}.json"), (R / "rda", f"baseline_{tag}_{{s}}.json")):
            f = d / pat.format(nm=nm, s=a.src)
            if f.exists():
                f.replace(d / pat.format(nm=nm, s=a.dst)); n += 1
    print(f"改名 {n} 个文件：{a.src} → {a.dst}（{a.tags}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
