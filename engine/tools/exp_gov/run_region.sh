#!/usr/bin/env bash
# 区域数据治理与验证，一条命令（标准作业程序场景①，docs/数据治理标准作业程序.md 第三章）：
#   bash tools/exp_gov/run_region.sh tools/exp_gov/regions/<区域>.json [--no-run] [--oss]
# --oss：开源路线（截取用 tools/oss_gov/g_extract.py；不做 ArcGIS 的区域统计 area_stats，区域检查照做）
# 计算域 → 截取（原样 + 治理后）→ 区域统计 → 区域检查（检查结论有「停」就停下）→ 建模 + 一二维 + 指标 → 报告
# --no-run：只做到区域检查（不占机器跑一二维）。每步日志在 out/exp_gov/regions/<区域>/logs/。
set -e
CFG=$(realpath "$1"); shift || true
NORUN=0; ENGINE=arcgis
for x in "$@"; do case $x in --no-run) NORUN=1;; --engine=oss|--oss) ENGINE=oss;; esac; done   # --oss：开源路线（不需要 ArcGIS）
PY_ARC="/c/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe"
cd "/d/阿布扎比/排水智能体"
export SWBRAIN_SWMM_WORK='D:\swbrain_work\swmm'
NAME=$(python -c "import json,sys;print(json.load(open(sys.argv[1],encoding='utf-8'))['name'])" "$CFG")
W=$(python -c "import json,sys;print(json.load(open(sys.argv[1],encoding='utf-8'))['work'])" "$CFG")
R="out/exp_gov/regions/$NAME"; mkdir -p "$R/logs"
fail() { echo "== FAILED $1 $(date +%T)（日志 $R/logs/$1.log）"; exit 1; }
step() { echo "== $1 $(date +%T)"; }
T0=$(date +%s)

step domain;  python tools/exp_gov/region.py domain "$CFG" > "$R/logs/domain.log" 2>&1 || fail domain
if [ $ENGINE = oss ]; then
step extract; python -W ignore tools/oss_gov/g_extract.py raw "$W" "$R/domain.geojson" "$R" > "$R/logs/extract_raw.log" 2>&1 || fail extract_raw
              python -W ignore tools/oss_gov/g_extract.py gov "$W" "$R/domain.geojson" "$R" > "$R/logs/extract_gov.log" 2>&1 || fail extract_gov
else
step extract; "$PY_ARC" -I tools/exp_gov/v_extract.py raw "$W" "$R/domain.geojson" "$R" > "$R/logs/extract_raw.log" 2>&1 || fail extract_raw
              "$PY_ARC" -I tools/exp_gov/v_extract.py gov "$W" "$R/domain.geojson" "$R" > "$R/logs/extract_gov.log" 2>&1 || fail extract_gov
step stats;   EXP_GOV_WORK="$W" "$PY_ARC" -I tools/exp_gov/area_stats.py --name "$NAME" --domain "$R/domain.geojson" > "$R/logs/stats.log" 2>&1 || fail stats
fi
step check;   python tools/exp_gov/region.py check "$CFG" > "$R/logs/check.log" 2>&1 || fail check
cat "$R/logs/check.log" | grep "^\s*\["
if grep -q "\[停\]" "$R/logs/check.log"; then echo "== STOP 区域检查有「停」，先问用户（见 $R/logs/check.log）"; exit 2; fi
echo "== 区域检查用时 $(( ($(date +%s) - T0) / 60 )) 分钟"
[ $NORUN = 1 ] && { echo "== --no-run：到此为止"; exit 0; }
step run;     python tools/exp_gov/region.py run "$CFG" > "$R/logs/run.log" 2>&1 || fail run
step report;  python tools/exp_gov/region.py report "$CFG" > "$R/logs/report.log" 2>&1 || fail report
echo "== done $(date +%T)，共 $(( ($(date +%s) - T0) / 60 )) 分钟；报告 $R/${NAME}_治理前后验证.md"
