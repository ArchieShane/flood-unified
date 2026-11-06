#!/usr/bin/env bash
# 把本机 c2d 里的二维结果 .npz（AD_<区>_M61H1_<L1|L2>_2d.npz：每 5 分钟一帧的形心水深 + 全程最大水深 + 单元坐标/面积/高程）拷到 NAS 交付目录：
#   $RESULT_ROOT/<主机名>/<区域目录>_61mm1h_30m/<L1_既有管网|L2_已建成调蓄>/二维/AD_<区>_M61H1_<态>_2d.npz
#   bash ad_copy_npz.sh            # 先只列出要拷的文件和大小
#   bash ad_copy_npz.sh go         # 真拷（先写 .partial 再改名；目标已有且大小相同的跳过）
#   TAGS="RD2_M61H1 RB1_M61H1" bash ad_copy_npz.sh go
set -euo pipefail
C=${C:-swbrain}
GO=${1:-}
WORK_HOST=${WORK_HOST:-$(docker exec "$C" printenv SWBRAIN_SWMM_WORK_HOST)}
[ -n "$WORK_HOST" ] || { echo "取不到宿主机工作目录，用 WORK_HOST=... 指定"; exit 1; }
TAGS=${TAGS:-$(docker exec "$C" sh -c 'ls /work/c2d/ad_stats_*.json 2>/dev/null' | sed 's#.*/ad_stats_##; s#\.json$##' | tr '\n' ' ')}
declare -A SD=([L1]="L1_既有管网" [L2]="L2_已建成调蓄")
tot=0; n=0
for t in $TAGS; do
  DD=$(docker exec "$C" python -c "import json;print(json.load(open('/work/c2d/ad_stats_$t.json',encoding='utf-8'))['delivery']['dir'])")
  for s in L1 L2; do
    src="$WORK_HOST/c2d/AD_${t}_${s}_2d.npz"; dst="$DD/${SD[$s]}/二维/AD_${t}_${s}_2d.npz"
    [ -f "$src" ] || { echo "  （没有 $src）"; continue; }
    sz=$(stat -c %s "$src"); tot=$((tot + sz)); n=$((n + 1))
    if [ -f "$dst" ] && [ "$(stat -c %s "$dst")" = "$sz" ]; then echo "  ✓ 已有 $dst"; continue; fi
    printf '  %s  %s MB  →  %s\n' "$t/$s" "$((sz / 1048576))" "$dst"
    if [ "$GO" = go ]; then mkdir -p "$(dirname "$dst")"; cp "$src" "$dst.partial" && mv "$dst.partial" "$dst"; fi
  done
done
echo "共 $n 个文件，$((tot / 1073741824)) GB（$((tot / 1048576)) MB）"
[ "$GO" = go ] || echo "（只是列出；确认后加 go 真拷）"
