#!/usr/bin/env bash
# 第十轮：全市收尾（用户 2026-10-25 定）：毫米级按簇吸附、悬空吸附先于毫米级、压力管接雨水口按重力管、塘点让位、渗井与雨水口同点留渗井、去重复折点。重跑治理链、剩余问题、导出
W=D:/swbrain_work/exp_gov
PY="/c/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe"
cd "/d/阿布扎比/排水智能体"
bash tools/exp_gov/run_chain.sh || exit 1
echo "== s7 $(date +%T)"; "$PY" -I tools/exp_gov/s7_remaining.py $W > $W/07_remaining.log 2>&1 || echo "== FAILED s7"
echo "== s6 $(date +%T)"; "$PY" -I tools/exp_gov/s6_export.py $W > $W/07_export.log 2>&1 || echo "== FAILED s6"
echo "== round10 end $(date +%T)"
