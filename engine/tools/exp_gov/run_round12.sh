#!/usr/bin/env bash
# 第十二轮（用户 2026-10-25 定）：管端接到别的管中段（≤5 mm 加折点；悬空 ≤0.5 m 移过去加折点）、管接点 0.5 m 内移到检查井 / 雨水口上。
# 预处理已单独跑过并检查（02_prep_r12.log），这里从第 3 步接着跑；本地化库沿用第十一轮（含补的两条规则）
W=D:/swbrain_work/exp_gov
PY="/c/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe"
cd "/d/阿布扎比/排水智能体"
bash tools/exp_gov/run_chain_from_s3.sh || exit 1
echo "== s7 $(date +%T)"; "$PY" -I tools/exp_gov/s7_remaining.py $W > $W/07_remaining.log 2>&1 || echo "== FAILED s7"
echo "== s6 $(date +%T)"; "$PY" -I tools/exp_gov/s6_export.py $W > $W/07_export.log 2>&1 || echo "== FAILED s6"
echo "== round12 end $(date +%T)"
