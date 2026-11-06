#!/usr/bin/env bash
# C1 设计暴雨（61.34 mm / 24 h）的 L2+suggested（用户 2026-09-29「要」）：等 C1_D24_L2P 算完 → 按这场雨的残余积水设计（c1_design_l2s.py，v2 规则：转输 + 地面取水泵 + 必要处箱涵，不用土堤）→ 读入检查 → 开跑（28 h）
set -u
export MSYS_NO_PATHCONV=1
export SWBRAIN_SWMM_WORK='D:\swbrain_work\swmm'
W=/d/swbrain_work/swmm; L=$W/c2d/logs; R=/d/阿布扎比/排水智能体
log(){ echo "[$(date +%H:%M:%S)] $*"; }
log "等 twoway_C1_D24_L2P.json"
until [ -f $W/c2d/twoway_C1_D24_L2P.json ]; do
  if ! docker ps --format '{{.Names}}' | grep -q '^c1_D24_L2P$'; then
    [ -f $W/c2d/twoway_C1_D24_L2P.json ] || { log "C1_D24_L2P 容器退出但没有结果，停"; exit 2; }
  fi
  sleep 120
done
log "D24 L2+planned 完成，设计 L2+suggested"
cd "$R"
python tools/c1_design_l2s.py --rain 61 --tag D24 --base L2P > $L/design_C1_D24_L2S.log 2>&1 || { log "设计失败"; tail -20 $L/design_C1_D24_L2S.log; exit 3; }
grep -E "残余|措施" $L/design_C1_D24_L2S.log
python - <<'PY' || exit 4
import sys
from swmm.toolkit import solver
from swmm.toolkit.shared_enum import ObjectType
inp = r"D:\swbrain_work\swmm\C1_D24_L2S.inp"
try:
    solver.swmm_open(inp, inp.replace(".inp", "_parse.rpt"), inp.replace(".inp", "_parse.out"))
    print("读入成功：节点", solver.project_get_count(ObjectType.NODE), "连接", solver.project_get_count(ObjectType.LINK)); solver.swmm_close()
except Exception as e:
    print("读入失败", e); print(open(inp.replace(".inp", "_parse.rpt"), encoding="utf-8", errors="ignore").read()[-2500:]); sys.exit(1)
PY
rm -f $W/C1_D24_L2S_parse.*
log "开跑 C1_D24 L2S（28 h）"
docker run --rm --name c1_D24_L2S -e OMP_NUM_THREADS=8 -e C2D_DEM_RAW=1 -e C2D_SAVE_UV=1 -v D:/swbrain_work/swmm:/work anuga-swmm:1.0 \
  python -u /work/c2d/couple2way_v2.py C1_D24 L2S 15 28 60 > $L/C1_D24_L2S.log 2>&1
[ -f $W/c2d/twoway_C1_D24_L2S.json ] && log "C1_D24_L2S 完成" || { log "C1_D24_L2S 没有产出结果"; tail -5 $L/C1_D24_L2S.log; exit 5; }
