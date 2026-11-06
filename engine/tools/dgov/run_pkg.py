"""方案包：逐个打包（L1 资产 + 一个 L2 方案）→ Esri Essentials 校验（UN）→ 发布关口 → 发布为「L1+方案」的暂定资产。

  python -m tools.dgov.run_pkg --extent rda --l1 <L1 资产 gpkg> --version v1.3 [--scheme TIP PS ...]
"""
from __future__ import annotations

import argparse
import sys

import pyogrio

from . import l2_package, publish, un
from .common import OUT


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tools.dgov.run_pkg")
    ap.add_argument("--extent", required=True)
    ap.add_argument("--l1", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--scheme", nargs="+", default=list(l2_package.SCHEMES))
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    for s in a.scheme:
        print(f"\n=== L1+{s}", flush=True)
        pkg, _ = l2_package.build(a.extent, a.l1, s)
        tag = f"L1+{s}"
        un.build(a.extent, src=pkg, tag=tag)
        extra = [k for k in ("storage", "storage_curve", "l2_issues") if k in [x[0] for x in pyogrio.list_layers(pkg)]]
        publish.publish(a.extent, tag, a.version, provisional=True, src=pkg,
                        un_report=OUT / "un" / f"{a.extent}_{tag}_un_report.json", extra_layers=extra)


if __name__ == "__main__":
    main()
