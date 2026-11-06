#!/usr/bin/env bash
# C1 模型版计算域（RB3 + Musaffah_RD09 + B_C 三个客户模型，38.1 km²）61 mm / 2 h 的 L2+suggested v3（用户 2026-09-30「按模型版重算61mm2小时的l1、l2、l2+」）：
# 等 C1_M61_L2P 算完 → 按这次的残余积水设计 v3（tools/c1_design_l2s3.py）→ 读入检查 → 开跑（6 h）
set -u
export MSYS_NO_PATHCONV=1
export SWBRAIN_SWMM_WORK='D:\swbrain_work\swmm'
W=/d/swbrain_work/swmm; L=$W/c2d/logs; R=/d/阿布扎比/排水智能体
log(){ echo "[$(date +%H:%M:%S)] $*"; }
log "等 twoway_C1_M61_L2P.json"
until [ -f $W/c2d/twoway_C1_M61_L2P.json ] && ! docker ps --format '{{.Names}}' | grep -q '^c1m_R61_L2P$'; do
  if ! docker ps --format '{{.Names}}' | grep -q '^c1m_R61_L2P$' && [ ! -f $W/c2d/twoway_C1_M61_L2P.json ]; then
    log "c1m_R61_L2P 容器退出但没有结果，停"; tail -5 $L/C1_M61_L2P.log; exit 2
  fi
  sleep 60
done
log "M61 L2+planned 完成，设计 v3"
cd "$R"
export PROJ_DATA=$(python -c "import rasterio,os;print(os.path.join(os.path.dirname(rasterio.__file__),'proj_data'))")
python tools/c1_design_l2s3.py --rain 61 --tag M61 --base L2P > $L/design_C1_M61_L2S3.log 2>&1 || { log "设计失败"; tail -20 $L/design_C1_M61_L2S3.log; exit 3; }
grep -E "残余|措施|塘分配" $L/design_C1_M61_L2S3.log
docker run --rm -v D:/swbrain_work/swmm:/work anuga-swmm:1.0 python /work/c2d/_chk_inp.py C1_M61_L2S3 || { log "读入失败"; exit 4; }
log "开跑 C1_M61 L2S3（6 h）"
docker run --rm --name c1m_R61_L2S3 -e OMP_NUM_THREADS=8 -e C2D_DEM_RAW=1 -e C2D_SAVE_UV=1 -v D:/swbrain_work/swmm:/work anuga-swmm:1.0 \
  python -u /work/c2d/couple2way_v2.py C1_M61 L2S3 15 6 60 > $L/C1_M61_L2S3.log 2>&1
log "C1_M61 L2S3 结束：$(grep -E '水量对账' $L/C1_M61_L2S3.log | tail -1)"
