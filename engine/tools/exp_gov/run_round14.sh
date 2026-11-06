#!/usr/bin/env bash
# 第十四轮：修我的实现错误——「中间折点连通」只在有别的线端点或有点时成立（UN 规则）；第十三轮把所有折点都当连通，误判内陆系统、误合并系统。规则不变。
W=D:/swbrain_work/exp_gov
PY="/c/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe"
cd "/d/阿布扎比/排水智能体"
bash tools/exp_gov/run_chain.sh || exit 1
echo "== s7 $(date +%T)"; "$PY" -I tools/exp_gov/s7_remaining.py $W > $W/07_remaining.log 2>&1 || echo "== FAILED s7"
echo "== s6 $(date +%T)"; "$PY" -I tools/exp_gov/s6_export.py $W > $W/07_export.log 2>&1 || echo "== FAILED s6"
echo "== round14 end $(date +%T)"
