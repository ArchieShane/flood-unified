#!/usr/bin/env bash
# 开源路线：整套数据治理，一条命令，不需要 ArcGIS（标准作业程序第十章）。和 ArcGIS 路线（tools/exp_gov/run_full.sh）并行、结果逐条可比。
#   EXP_DMT_SRC=<DMT shapefile 目录> EXP_AEC_SRC=<顾问模型 shapefile 目录 | none> bash tools/oss_gov/g_run.sh <工作目录> <轮次号> [<上一轮导出目录>]
# 前提：<工作目录>/Expanded_local.gdb（本地化后的资产包，见 SOP 10.2；从已有工作目录复制即可，开源路线只读它）。
# 规则代码和 ArcGIS 路线是同一份（tools/exp_gov/s0、s2a、s2 通过开源替身 tools/oss_gov/shim/arcpy 运行）；
# 装载、查错、子网、导出由 tools/oss_gov 的 o1–o3、g_export、g_remaining 完成。
set -e
W=${1:?工作目录}; N=${2:?轮次号}; PREV=$3
cd "/d/阿布扎比/排水智能体"
fail() { echo "== FAILED $1 $(date +%T)（日志 $W/$1.log）"; exit 1; }
[ -d "$W/Expanded_local.gdb" ] || fail "缺资产包：$W/Expanded_local.gdb"
[ "${EXP_AEC_SRC:-}" = "none" ] && export EXP_NO_AECOM=1
export EXP_ENGINE=oss OSS_GOV_WORK="$W" OSS_GOV_PREP="$W/02_prep/prep.gdb" OSS_GOV_AP="$W/Expanded_local.gdb"
SHIM="python -W ignore tools/oss_gov/run_shim.py"
T0=$(date +%s)
mkdir -p "$W/02_prep"
if [ ! -d "$W/src_dmt.gdb" ]; then
  echo "== s0 入库探查 $(date +%T)"; $SHIM tools/exp_gov/s0_stage_profile.py "$W" > "$W/00_stage.log" 2>&1 || fail 00_stage
fi
echo "== s2a DTM 采样 $(date +%T)"; [ -f "$W/02_prep/dtm_at_dmt.csv" ] || python tools/exp_gov/s2a_dtm_sample.py "$W" > "$W/02a_dtm.log" 2>&1 || fail 02a_dtm
echo "== s2 预处理（全部修正规则）$(date +%T)"; $SHIM tools/exp_gov/s2_prep.py "$W" > "$W/02_prep.log" 2>&1 || fail 02_prep
echo "== 规则预检 $(date +%T)"; python tools/exp_gov/precheck_rules.py "$W" "$W/02_prep/precheck_rules.csv" "$W/Expanded_local.gdb" > "$W/02_precheck.log" 2>&1 || fail 02_precheck
grep -q "规则表里没有的 0 种" "$W/02_precheck.log" || echo "== 注意：有缺连通规则的组合，见 $W/02_precheck.log"
for s in o1_graph o2_checks o3_trace; do
  echo "== $s $(date +%T)"; python -W ignore tools/oss_gov/$s.py c2 > "$W/$s.log" 2>&1 || fail $s
done
echo "== 导出 $(date +%T)"; python -W ignore tools/oss_gov/g_export.py "$W" > "$W/07_export.log" 2>&1 || fail 07_export
echo "== 剩余分析 $(date +%T)"; python -W ignore tools/oss_gov/g_remaining.py "$W" > "$W/07_remaining.log" 2>&1 || fail 07_remaining
echo "== 遗留问题台账 $(date +%T)"; python tools/exp_gov/ledger.py --work "$W" --round "$N" --out "$W/ledger" > "$W/08_ledger.log" 2>&1 || fail 08_ledger
if [ -n "$PREV" ]; then
  echo "== 逐管对比 $(date +%T)"; python tools/exp_gov/round_diff.py "$PREV" "$W/07_export" "$W/round_diff_r$N.csv" > "$W/08_round_diff.log" 2>&1 || fail 08_round_diff
  tail -20 "$W/08_round_diff.log"
fi
cp "$W/02_prep/prep_log.json" "$W/02_prep/prep_log_round$N.json"
rm -rf "$W/06_validate_round$N" "$W/07_export_round$N"; cp -r "$W/06_validate" "$W/06_validate_round$N"; cp -r "$W/07_export" "$W/07_export_round$N"
python - "$W" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1] + "/06_validate/remaining.json", encoding="utf-8"))
print("== 本轮结果（汇报必报：还追踪不到的比例、变差的地方）：", json.dumps(r["by_class"], ensure_ascii=False))
EOF
echo "== done（开源路线）第 $N 轮，共 $(( ($(date +%s) - T0) / 60 )) 分钟"
