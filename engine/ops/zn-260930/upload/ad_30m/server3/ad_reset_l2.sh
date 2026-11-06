#!/usr/bin/env bash
# ADM 30 m 推演 · 10-16 塘清单更新后「只重做 L2」：把这台上有塘的区域的旧 L2 结果挪到备份目录、清掉 L2 的中间产物。
# 之后正常 `bash ad_30m_server<N>.sh start`：build 重建 L1/L2 的 .inp，L1 已有结果跳过，只算 L2，再后处理、转 shp+dat。
# 再跑 `FORCE=1 bash ad_frames.sh` 重做 viewer 的逐帧图。
#
#   bash ad_reset_l2.sh            # 本机有的、受影响的区域（MI8 RD2 YASO RB1 RBE1 MBZ RB3 RDG），先只列出要做什么
#   bash ad_reset_l2.sh go         # 真做：移动 NAS 上旧 L2（result / shp_dat / viewer 逐帧图）→ $NAS_BASE/old_L2_1016/，删 L2 的计算中间文件
#   TAGS="RD2_M61H1" bash ad_reset_l2.sh go
# 旧结果只是挪走（同一个 NAS 卷内 mv，不复制），不删。L1、没有塘的区域（RD-A / RD-C / RD-F / RD-I / Qasr Al Bahr-1）、RD-14（塘没变）不动。
set -euo pipefail
C=${C:-swbrain}
NAS_BASE=${NAS_BASE:-/media/pop-fs/flood_data}
RESULT_ROOT=${RESULT_ROOT:-$NAS_BASE/result}
SHP_ROOT=${SHP_ROOT:-$NAS_BASE/shp_dat}
HOST_NAME=${HOST_NAME:-$(hostname -s)}
BK=${BK:-$NAS_BASE/old_L2_1016}
AFFECTED="MI8 RD2 YASO RB1 RBE1 MBZ RB3 RDG"
GO=${1:-}
if [ -z "${TAGS:-}" ]; then
  TAGS=""
  for t in $(docker exec "$C" sh -c 'ls /work/c2d/ad_stats_*.json 2>/dev/null' | sed 's#.*/ad_stats_##; s#\.json$##'); do
    for r in $AFFECTED; do [ "${t%%_*}" = "$r" ] && TAGS="$TAGS $t"; done
  done
fi
[ -n "${TAGS// /}" ] || { echo "这台没有要重做的区域"; exit 0; }
echo "主机 $HOST_NAME，要重做 L2 的情景：$TAGS"
for t in $TAGS; do
  reg=${t%%_*}; rl=$(echo "$reg" | tr 'A-Z' 'a-z')
  DD=$(docker exec "$C" python -c "import json;print(json.load(open('/work/c2d/ad_stats_$t.json',encoding='utf-8'))['delivery']['dir'])")
  rd=$(basename "$DD")
  mm=$(echo "$t" | sed -E 's/.*_M([0-9]+)H([0-9]+)/\1/'); h=$(echo "$t" | sed -E 's/.*_M([0-9]+)H([0-9]+)/\2/')
  moves=("$DD/L2_已建成调蓄|$BK/result/$HOST_NAME/$rd/L2_已建成调蓄"
         "$SHP_ROOT/$HOST_NAME/$rd/L2|$BK/shp_dat/$HOST_NAME/$rd/L2"
         "$RESULT_ROOT/$HOST_NAME/runtime/console_data/flood2d_ad_${rl}_m${mm}h${h}g30|$BK/console_data/$HOST_NAME/flood2d_ad_${rl}_m${mm}h${h}g30")
  echo "== $t"
  for m in "${moves[@]}"; do
    src=${m%%|*}; dst=${m##*|}
    if [ -e "$src" ]; then
      echo "  mv $src → $dst"
      [ "$GO" = go ] && { mkdir -p "$(dirname "$dst")"; rm -rf "$dst"; mv "$src" "$dst"; }
    else echo "  （没有 $src）"; fi
  done
  echo "  删 c2d / work 里 L2 的中间文件（AD_${t}_L2.inp、pilot_、twoway_、_2d.npz、_2way.rpt、ad_stats_${t}.json）"
  [ "$GO" = go ] && docker exec "$C" sh -c "rm -f /work/AD_${t}_L2.inp /work/c2d/pilot_AD_${t}_L2.json /work/c2d/twoway_AD_${t}_L2.json /work/c2d/AD_${t}_L2_2d.npz /work/c2d/AD_${t}_L2_2way.rpt /work/c2d/ad_stats_${t}.json"
done
[ "$GO" = go ] && echo "完成。下一步：bash ad_30m_server*.sh start" || echo "（只是列出；确认无误后加 go 真做）"
