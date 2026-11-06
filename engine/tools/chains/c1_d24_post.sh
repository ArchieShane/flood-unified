#!/usr/bin/env bash
# C1 设计暴雨（61.34 mm / 24 h，情景 C1_D24）后处理：等 C1_D24_L2S 算完 → 统计值 / 差值图 / 逐帧图 → 副作用 → GeoTIFF → .sww（带流速）→ 热点过程线 → 交付目录
# 已授权的自动后处理（用户 2026-09-28「9-13项都自动做，先不提交」）；报告与系统截图由 Claude 接着做
set -u
export MSYS_NO_PATHCONV=1
export SWBRAIN_SWMM_WORK='D:\swbrain_work\swmm'
W=/d/swbrain_work/swmm; L=$W/c2d/logs; R=/d/阿布扎比/排水智能体
log(){ echo "[$(date +%H:%M:%S)] $*"; }
log "等 C1_D24_L2S"
until [ -f $W/c2d/twoway_C1_D24_L2S.json ] && ! docker ps --format '{{.Names}}' | grep -q '^c1_D24_L2S$'; do
  if ! docker ps --format '{{.Names}}' | grep -q '^c1_D24_L2S$' && [ ! -f $W/c2d/twoway_C1_D24_L2S.json ]; then
    log "c1_D24_L2S 容器退出但没有结果"; tail -5 $L/C1_D24_L2S.log; break
  fi
  sleep 120
done
cd "$R"
export PROJ_DATA=$(python -c "import rasterio,os;print(os.path.join(os.path.dirname(rasterio.__file__),'proj_data'))")
log "后处理 D24"
python tools/c1_post.py --tag D24 > $L/post_C1_D24.log 2>&1 || { log "c1_post 失败"; tail -15 $L/post_C1_D24.log; }
[ -f $W/c2d/twoway_C1_D24_L2S.json ] && { python tools/c1_l2s_side_effects.py --tag D24 --state L2S > $L/side_C1_D24.log 2>&1 || log "副作用分析失败"; }
python tools/npz_to_tif.py C1_D24 L1 L2 L2P L2S > $L/tif_C1_D24.log 2>&1 || log "GeoTIFF 失败"
for s in L1 L2 L2P L2S; do
  [ -f $W/c2d/C1_D24_${s}_2d.npz ] || continue
  docker run --rm --name c1_sww_D24_$s -v D:/swbrain_work/swmm:/work anuga-swmm:1.0 python -u /work/c2d/npz_to_sww.py C1_D24 $s 15 > $L/sww_C1_D24_$s.log 2>&1 || log "D24 $s .sww 失败"
  log "D24 $s .sww：$(tail -1 $L/sww_C1_D24_$s.log)"
done
python tools/c1_hs_series.py > $L/hs_series_D24.log 2>&1 || log "热点过程线失败"
python tools/export_delivery_c1.py --tag D24 > $L/export_C1_D24.log 2>&1 || { log "交付导出失败"; tail -10 $L/export_C1_D24.log; }
log "D24 后处理完成：$(grep -E '个文件' $L/export_C1_D24.log | tail -1)"
