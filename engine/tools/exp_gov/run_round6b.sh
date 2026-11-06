#!/usr/bin/env bash
# 第六轮（统一高程基准）整套：治理链 → 剩余问题 → 导出 → RD-A 治理后重新导出 → 验证 ②③ 重算
W=D:/swbrain_work/exp_gov
PY="/c/Users/zn198/AppData/Local/ESRI/conda/envs/arcgispro-py3-clone1/python.exe"
cd "/d/阿布扎比/排水智能体"
bash tools/exp_gov/run_chain_from_s3.sh || exit 1
echo "== s7 $(date +%T)"; "$PY" -I tools/exp_gov/s7_remaining.py $W > $W/07_remaining.log 2>&1 || echo "== FAILED s7"
echo "== s6 $(date +%T)"; "$PY" -I tools/exp_gov/s6_export.py $W > $W/07_export.log 2>&1 || echo "== FAILED s6"
echo "== extract $(date +%T)"; "$PY" -I tools/exp_gov/v_extract.py gov $W out/dataqc/RD-A/input/rda_domain.geojson > $W/07_extract.log 2>&1 || { echo "== FAILED extract"; exit 1; }
rm -f D:/swbrain_work/swmm/VG_*_G.inp
export SWBRAIN_SWMM_WORK='D:\swbrain_work\swmm'
echo "== verify $(date +%T)"; python tools/exp_gov/v_chain.py --runs D61:G,D61:R,E26:G,E26:R,E24:G,E24:R --par 3 --threads 6 > $W/v_chain_v3.log 2>&1 || echo "== FAILED verify"
echo "== done $(date +%T)"
