#!/usr/bin/env bash
# 第十一轮（用户 2026-10-25 定）：断成几截的管连成一截、补压力管—渗井和泵—穿孔管两条连通规则、同点阈值 5 mm、悬空吸附先认毫米级接点、中间折点加连接点。重跑治理链、剩余问题、导出
W=D:/swbrain_work/exp_gov
PY="/c/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe"
cd "/d/阿布扎比/排水智能体"
bash tools/exp_gov/run_chain.sh || exit 1
echo "== s7 $(date +%T)"; "$PY" -I tools/exp_gov/s7_remaining.py $W > $W/07_remaining.log 2>&1 || echo "== FAILED s7"
echo "== s6 $(date +%T)"; "$PY" -I tools/exp_gov/s6_export.py $W > $W/07_export.log 2>&1 || echo "== FAILED s6"
echo "== round11 end $(date +%T)"
