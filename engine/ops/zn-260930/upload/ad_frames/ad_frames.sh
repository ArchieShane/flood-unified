#!/usr/bin/env bash
# ADM 30 m 推演 → flood-viewer 逐帧图：对本机已交付的每个情景（c2d/ad_stats_<tag>.json 在的）跑 ad_frames.py，
# 成果写 NAS：$RESULT_ROOT/<主机名>/runtime/console_data/flood2d_ad_<区>_m61h1g30/
#   bash ad_frames.sh            # 全部情景；已有 stats.json 的跳过
#   FORCE=1 bash ad_frames.sh    # 重做
#   TAGS="RD2_M61H1 RB1_M61H1" bash ad_frames.sh
# 前提：本机 swbrain 容器在、c2d 里的 _2d.npz 还在、NAS 已挂（同 ad_30m_server*.sh）。
set -euo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
NAS_BASE=${NAS_BASE:-/media/pop-fs/flood_data}
RESULT_ROOT=${RESULT_ROOT:-$NAS_BASE/result}
HOST_NAME=${HOST_NAME:-$(hostname -s)}
IMAGE=${IMAGE:-anuga-swmm:1.0}
RES=${RES:-30}
WORK_HOST=${WORK_HOST:-$(docker exec "$C" printenv SWBRAIN_SWMM_WORK_HOST)}
[ -n "$WORK_HOST" ] || { echo "取不到宿主机工作目录（容器 $C 的 SWBRAIN_SWMM_WORK_HOST），用 WORK_HOST=... 指定"; exit 1; }
docker cp "$HERE/ad_frames.py" "$C:/work/c2d/ad_frames.py"
TAGS=${TAGS:-$(docker exec "$C" sh -c 'ls /work/c2d/ad_stats_*.json 2>/dev/null' | sed 's#.*/ad_stats_##; s#\.json$##' | tr '\n' ' ')}
[ -n "${TAGS// /}" ] || { echo "这台没有已完成后处理的情景（c2d/ad_stats_*.json）"; exit 1; }
echo "主机 $HOST_NAME，工作目录 $WORK_HOST，情景：$TAGS"
for t in $TAGS; do
  reg=${t%%_*}
  # 目录名里的雨量 / 历时取自 tag：<区>_M<雨量>H<历时>
  mm=$(echo "$t" | sed -E 's/.*_M([0-9]+)H([0-9]+)/\1/'); h=$(echo "$t" | sed -E 's/.*_M([0-9]+)H([0-9]+)/\2/')
  out="$RESULT_ROOT/$HOST_NAME/runtime/console_data/flood2d_ad_$(echo "$reg" | tr 'A-Z' 'a-z')_m${mm}h${h}g${RES}"
  if [ -f "$out/stats.json" ] && [ -z "${FORCE:-}" ]; then echo "✓ $t 已有（FORCE=1 重做）"; continue; fi
  echo "==== $(date '+%F %T') $t"
  docker run --rm -e AD_RESULT_ROOT="$RESULT_ROOT" -e AD_HOST="$HOST_NAME" -v "$WORK_HOST:/work" -v "$NAS_BASE:$NAS_BASE" "$IMAGE" \
    python -u /work/c2d/ad_frames.py --tag "$t" --res "$RES"
done
echo "==== 完成。viewer 最迟 120 s 后刷新目录（或开 /api/rescan）"
