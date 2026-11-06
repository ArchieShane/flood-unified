#!/usr/bin/env bash
# C1 设计暴雨（61.34 mm / 24 h）的 L2：等 61 / 96 mm 的 L2+suggested v2 都算完（用户 2026-09-29：等 v2 算完再补跑）→ 建模 → 读入检查 → 开跑
set -u
export MSYS_NO_PATHCONV=1
export SWBRAIN_SWMM_WORK='D:\swbrain_work\swmm'
W=/d/swbrain_work/swmm; L=$W/c2d/logs; R=/d/阿布扎比/排水智能体
log(){ echo "[$(date +%H:%M:%S)] $*"; }
log "等 twoway_C1_R61_L2S.json、twoway_C1_R96_L2S.json"
until [ -f $W/c2d/twoway_C1_R61_L2S.json ] && [ -f $W/c2d/twoway_C1_R96_L2S.json ]; do
  if ! docker ps --format '{{.Names}}' | grep -qE '^c1_R(61|96)_L2S$'; then
    [ -f $W/c2d/twoway_C1_R61_L2S.json ] && [ -f $W/c2d/twoway_C1_R96_L2S.json ] || { log "v2 容器都退出了但结果不全，停"; exit 2; }
  fi
  sleep 60
done
log "v2 完成，建 D24 L2"
cd "$R"
python tools/build_c1_l2.py --rain 61 --tag D24 > $L/build_C1_D24_L2.log 2>&1 || { log "建模失败"; tail -20 $L/build_C1_D24_L2.log; exit 3; }
python - <<'PY' || exit 4
import sys
from swmm.toolkit import solver
from swmm.toolkit.shared_enum import ObjectType
inp = r"D:\swbrain_work\swmm\C1_D24_L2.inp"
try:
    solver.swmm_open(inp, inp.replace(".inp", "_parse.rpt"), inp.replace(".inp", "_parse.out"))
    print("读入成功：节点", solver.project_get_count(ObjectType.NODE), "连接", solver.project_get_count(ObjectType.LINK)); solver.swmm_close()
except Exception as e:
    print("读入失败", e); print(open(inp.replace(".inp", "_parse.rpt"), encoding="utf-8", errors="ignore").read()[-2500:]); sys.exit(1)
PY
rm -f $W/C1_D24_L2_parse.*
log "开跑 C1_D24 L2（28 h）"
docker run --rm --name c1_D24_L2 -e OMP_NUM_THREADS=8 -e C2D_DEM_RAW=1 -e C2D_SAVE_UV=1 -v D:/swbrain_work/swmm:/work anuga-swmm:1.0 \
  python -u /work/c2d/couple2way_v2.py C1_D24 L2 15 28 60 > $L/C1_D24_L2.log 2>&1
[ -f $W/c2d/twoway_C1_D24_L2.json ] && log "C1_D24_L2 完成" || { log "C1_D24_L2 没有产出结果"; tail -5 $L/C1_D24_L2.log; exit 5; }
