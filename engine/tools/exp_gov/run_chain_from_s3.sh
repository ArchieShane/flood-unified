#!/usr/bin/env bash
# Expanded 治理整条链（第 1–7 步）：本地化 → 预处理（含第 8 步修正规则）→ 映射 / 迁移工作区 → 装数据、关联、端子、补字段、部署 → 校验、子网
set -e
fail() { echo "== FAILED $1 $(date +%T)"; exit 1; }
PY="/c/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe"
W=D:/swbrain_work/exp_gov
cd "/d/阿布扎比/排水智能体"
echo "== s3 $(date +%T)"; rm -rf $W/03_mapping; "$PY" -I tools/exp_gov/s3_mapping.py $W > $W/03_mapping.log 2>&1 || fail s3
echo "== s4 $(date +%T)"; "$PY" -I tools/exp_gov/s4_load_deploy.py $W > $W/04_load.log 2>&1 || fail s4
echo "== s5 $(date +%T)"; "$PY" -I tools/exp_gov/s5_validate.py $W > $W/06_validate.log 2>&1 || fail s5
echo "== done $(date +%T)"
