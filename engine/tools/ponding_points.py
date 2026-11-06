# -*- coding: utf-8 -*-
"""一二维结果 → 二维模拟积水点清单（SIM-态-序号）。逻辑在 swbrain/ponding.py，这里只是导出 CSV / GeoJSON 的入口。

用法：python tools/ponding_points.py --name RB01_H60 [--states L1,L2] [--depth 0.15] [--min-area 100] [--min-dur 0.5]
产物：out/ponding/{name}_d{depth}.csv、.geojson（面）、_pts.geojson（点）、_params.json
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from swbrain import ponding  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(ponding.main([*sys.argv[1:], "--export"]))
