#!/usr/bin/env bash
# 一批区域只做区域检查（不跑一二维）：bash tools/exp_gov/run_regions_check.sh <配置目录> [--oss]
# 每个区域调 run_region.sh --no-run；一个区域失败不影响后面的；最后汇总成一张表（region_summary.py）。
D=${1:?配置目录}; shift   # 缺省走 ArcGIS 路线（正式结果）；加 --oss 只作交叉核对
cd "/d/阿布扎比/排水智能体"
for c in "$D"/*.json; do
  echo "#### $(basename "$c" .json) $(date +%T)"
  bash tools/exp_gov/run_region.sh "$c" --no-run "$@" 2>&1 | grep -E "^\s*\[|FAILED|STOP|用时" || echo "  （失败，见该区域 logs/）"
done
python tools/exp_gov/region_summary.py "$D"
