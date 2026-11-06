#!/usr/bin/env bash
# 整套数据治理，一条命令（标准作业程序场景②：新城市 / 新数据源，或客户交来新版 DMT；docs/数据治理标准作业程序.md 第四章）：
#   EXP_DMT_SRC=<DMT shapefile 目录> EXP_AEC_SRC=<顾问模型导出目录 | none> bash tools/exp_gov/run_full.sh <工作目录> <轮次号> [<上一轮导出目录>]
# 前提（第 0 步，见 SOP 4.1）：<工作目录>/Expanded_32640.gdb 已就位（s1_ap_mapping.py 生成，或从已有工作目录复制）；
#                             新数据源的字段结构和 DMT 不同的，先写好 s2_prep / s3_mapping 的适配（SOP 4.2），否则第 2 步会报错。
# 顺序：入库探查 → 本地化 → 预处理（全部修正规则）→ 装入前规则预检（run_chain.sh 里）→ 映射 → 装数据部署 → 校验子网 → 剩余分析 → 导出
#       → 遗留问题台账 → 和上一轮逐管对比 → 本轮存档。任何一步失败即停，并说明是哪一步。
set -e
W=${1:?工作目录}; N=${2:?轮次号}; PREV=$3
PY="/c/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe"
cd "/d/阿布扎比/排水智能体"
fail() { echo "== FAILED $1 $(date +%T)（日志 $W/$1.log）"; exit 1; }
[ -d "$W/Expanded_32640.gdb" ] || fail "缺资产包：$W/Expanded_32640.gdb（SOP 4.1）"
[ "${EXP_AEC_SRC:-}" = "none" ] && export EXP_NO_AECOM=1
T0=$(date +%s)
if [ ! -d "$W/src_dmt.gdb" ]; then
  echo "== s0 $(date +%T)"; "$PY" -I tools/exp_gov/s0_stage_profile.py "$W" > "$W/00_stage.log" 2>&1 || fail 00_stage
fi
EXP_GOV_WORK="$W" bash tools/exp_gov/run_chain.sh || exit 1
echo "== s7 $(date +%T)"; "$PY" -I tools/exp_gov/s7_remaining.py "$W" > "$W/07_remaining.log" 2>&1 || fail 07_remaining
echo "== s6 $(date +%T)"; "$PY" -I tools/exp_gov/s6_export.py "$W" > "$W/07_export.log" 2>&1 || fail 07_export
echo "== ledger $(date +%T)"; "$PY" -I tools/exp_gov/ledger_dirty.py "$W" > "$W/08_ledger_dirty.log" 2>&1 || fail 08_ledger_dirty
python tools/exp_gov/ledger.py --work "$W" --round "$N" --out "$W/ledger" > "$W/08_ledger.log" 2>&1 || fail 08_ledger
if [ -n "$PREV" ]; then
  echo "== round_diff $(date +%T)"; python tools/exp_gov/round_diff.py "$PREV" "$W/07_export" "$W/round_diff_r$N.csv" > "$W/08_round_diff.log" 2>&1 || fail 08_round_diff
  tail -20 "$W/08_round_diff.log"
fi
echo "== 存档 $(date +%T)"
cp "$W/02_prep/prep_log.json" "$W/02_prep/prep_log_round$N.json"
rm -rf "$W/06_validate_round$N" "$W/07_export_round$N"; cp -r "$W/06_validate" "$W/06_validate_round$N"; cp -r "$W/07_export" "$W/07_export_round$N"
python - "$W" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1] + "/06_validate/remaining.json", encoding="utf-8"))
print("== 本轮结果（汇报时必报：还追踪不到的比例、变差的地方）：", json.dumps(r, ensure_ascii=False)[:600])
EOF
echo "== done 第 $N 轮，共 $(( ($(date +%s) - T0) / 60 )) 分钟；遗留问题清单 $W/ledger/"
