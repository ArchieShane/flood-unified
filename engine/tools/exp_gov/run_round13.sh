#!/usr/bin/env bash
# 第十三轮：修我的实现错误——子网命名、内陆系统出流点、剩余问题分析都按「所有折点连通」（ANY_VERTEX），第十二轮支管接干管中段后两个系统被起两个名字，UN 报错误 29、808 根管掉出子网。规则不变。
W=D:/swbrain_work/exp_gov
PY="/c/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe"
cd "/d/阿布扎比/排水智能体"
bash tools/exp_gov/run_chain.sh || exit 1
echo "== s7 $(date +%T)"; "$PY" -I tools/exp_gov/s7_remaining.py $W > $W/07_remaining.log 2>&1 || echo "== FAILED s7"
echo "== s6 $(date +%T)"; "$PY" -I tools/exp_gov/s6_export.py $W > $W/07_export.log 2>&1 || echo "== FAILED s6"
echo "== round13 end $(date +%T)"
