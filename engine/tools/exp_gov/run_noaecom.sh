#!/usr/bin/env bash
# 对照（用户 2026-10-24 批准）：同一套治理逻辑只治理 DMT、不融合 AECOM，单独工作目录，不碰主线 D:/swbrain_work/exp_gov。
# 工作目录预先放好：Expanded_32640.gdb、src_dmt.gdb、src_aecom_rda.gdb（只用于原样另存汇水区）、02_prep/dtm_at_dmt.csv
set -e
fail() { echo "== FAILED $1 $(date +%T)"; exit 1; }
PY="/c/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe"
W=D:/swbrain_work/exp_gov_noaecom
export EXP_NO_AECOM=1
cd "/d/阿布扎比/排水智能体"
echo "== s1b $(date +%T)"; "$PY" -I tools/exp_gov/s1b_localize.py $W > $W/01_localize.log 2>&1 || fail s1b
echo "== s2 $(date +%T)"; "$PY" -I tools/exp_gov/s2_prep.py $W > $W/02_prep.log 2>&1 || fail s2
echo "== s3 $(date +%T)"; rm -rf $W/03_mapping; "$PY" -I tools/exp_gov/s3_mapping.py $W > $W/03_mapping.log 2>&1 || fail s3
echo "== s4 $(date +%T)"; "$PY" -I tools/exp_gov/s4_load_deploy.py $W > $W/04_load.log 2>&1 || fail s4
echo "== s5 $(date +%T)"; "$PY" -I tools/exp_gov/s5_validate.py $W > $W/06_validate.log 2>&1 || fail s5
echo "== s7 $(date +%T)"; "$PY" -I tools/exp_gov/s7_remaining.py $W > $W/07_remaining.log 2>&1 || echo "== FAILED s7"
echo "== s6 $(date +%T)"; "$PY" -I tools/exp_gov/s6_export.py $W > $W/07_export.log 2>&1 || echo "== FAILED s6"
echo "== extract $(date +%T)"; "$PY" -I tools/exp_gov/v_extract.py gov $W out/dataqc/RD-A/input/rda_domain.geojson > $W/07_extract.log 2>&1 || fail extract
echo "== noaecom end $(date +%T)"
