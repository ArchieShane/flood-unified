#!/usr/bin/env bash
# C1 L2+suggested v3（用户 2026-09-29「v3也可以做一下」）后处理：每场雨的 C1_R{r}_L2S3 算完 → 后处理（统计值、差值、逐帧图）→ 副作用 → GeoTIFF → .sww（带流速）
# 交付目录不在这里动：v3 与 v2 谁做正式 L2+suggested 等用户定
set -u
export MSYS_NO_PATHCONV=1
export SWBRAIN_SWMM_WORK='D:\swbrain_work\swmm'
W=/d/swbrain_work/swmm; L=$W/c2d/logs; R=/d/阿布扎比/排水智能体
log(){ echo "[$(date +%H:%M:%S)] $*"; }
cd "$R"
left="61 96"
while [ -n "$left" ]; do
  nl=""
  for r in $left; do
    if [ -f $W/c2d/twoway_C1_R${r}_L2S3.json ] && ! docker ps --format '{{.Names}}' | grep -q "^c1_R${r}_L2S3$"; then
      log "C1_R${r}_L2S3 算完：$(grep -E '水量对账|泵打进塘' $L/C1_R${r}_L2S3.log | tail -1)"
      python tools/c1_post.py --rain $r > $L/post_C1_R${r}_L2S3.log 2>&1 || { log "R$r 后处理失败"; tail -15 $L/post_C1_R${r}_L2S3.log; }
      python tools/c1_l2s_side_effects.py --tag R$r --state L2S3 > $L/side_C1_R${r}_L2S3.log 2>&1 || log "R$r 副作用分析失败"
      head -c 400 $L/side_C1_R${r}_L2S3.log; echo
      python tools/npz_to_tif.py C1_R$r L2S3 > $L/tif_C1_R${r}_L2S3.log 2>&1 || log "R$r GeoTIFF 失败"
      docker run --rm --name c1_sww_R${r}_L2S3 -v D:/swbrain_work/swmm:/work anuga-swmm:1.0 python -u /work/c2d/npz_to_sww.py C1_R$r L2S3 15 > $L/sww_C1_R${r}_L2S3.log 2>&1 || log "R$r .sww 失败"
      log "R$r v3 后处理完成：$(tail -1 $L/sww_C1_R${r}_L2S3.log)"
    elif ! docker ps --format '{{.Names}}' | grep -q "^c1_R${r}_L2S3$" && [ ! -f $W/c2d/twoway_C1_R${r}_L2S3.json ]; then
      log "C1_R${r}_L2S3 容器退出但没有结果"; tail -5 $L/C1_R${r}_L2S3.log
    else
      nl="$nl $r"
    fi
  done
  left=$(echo $nl)
  [ -n "$left" ] && sleep 120
done
log "v3 两场雨后处理全部完成"
