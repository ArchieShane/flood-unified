#!/usr/bin/env bash
# ADM 30 m（*_61mm1h_30m）重新转 shp + dat，**不过滤**：shp 含全部三角形、dat 全域全记录（不加 --wet-only-shp / --drop-dry）。
# 成果都在 NAS 的 result/ 下（三台服务器的都在），所以**只需在一台机器上跑**：扫 $RESULT_ROOT/*/<区域目录>_61mm1h_30m/<态>/二维/*.sww（所有主机），
# 输出到 $SHP_ROOT/<原主机名>/<区域目录>/<L1|L2>/（缺省 $NAS_BASE/shp_dat_nofilter），不动原来过滤过的 shp_dat/。只转 ADM 这批，不碰更早的情景。
# 需要：docker、h2d-convert:1.0 镜像（没有就用本目录 h2d/ 离线构建，需要本机有 anuga-swmm:1.0）、NAS 挂载。
#   bash ad_convert_full.sh plan    # 只列要转的 .sww 和大小，并显示 NAS 剩余空间
#   bash ad_convert_full.sh         # 真转（已转完的 h2d_index.json 在的跳过；可重复执行）
# 可选：CONV_JOBS（并行转几个态，缺省 4）、H2D_ARGS（缺省 --no-csv）、SHP_ROOT、RESULT_ROOT、NAS_BASE、HOSTS（只转这几个主机，空格分隔，缺省全部）
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
NAS_BASE=${NAS_BASE:-/media/pop-fs/flood_data}
RESULT_ROOT=${RESULT_ROOT:-$NAS_BASE/result}
SHP_ROOT=${SHP_ROOT:-$NAS_BASE/shp_dat_nofilter}
H2D_IMAGE=${H2D_IMAGE:-h2d-convert:1.0}
H2D_ARGS=${H2D_ARGS:---no-csv}
CONV_JOBS=${CONV_JOBS:-4}
PAT='*_61mm1h_30m/*/二维/*.sww'
[ -d "$RESULT_ROOT" ] || { echo "✗ $RESULT_ROOT 不存在：NAS 没挂上？"; exit 1; }

list_sww(){
  local h
  for h in ${HOSTS:-$(ls "$RESULT_ROOT")}; do
    [ -d "$RESULT_ROOT/$h" ] || continue
    find "$RESULT_ROOT/$h" -path "$RESULT_ROOT/$h/$PAT" -type f 2>/dev/null
  done | sort
}
# <RESULT_ROOT>/<主机>/<区域目录>/<L1_既有管网>/二维/<名>.sww  →  <SHP_ROOT>/<主机>/<区域目录>/<L1>/
out_of(){
  local sww=$1 st reg host
  st=$(basename "$(dirname "$(dirname "$sww")")"); reg=$(basename "$(dirname "$(dirname "$(dirname "$sww")")")")
  host=$(basename "$(dirname "$(dirname "$(dirname "$(dirname "$sww")")")")")
  echo "$SHP_ROOT/$host/$reg/${st%%_*}"
}

if [ "${1:-}" = plan ]; then
  echo "输出目录 $SHP_ROOT ；转换参数 $H2D_ARGS（无过滤）；并行 $CONV_JOBS"
  list_sww | while IFS= read -r f; do
    o=$(out_of "$f"); mark="  "; [ -f "$o/h2d_index.json" ] && mark="✓ "
    printf '%s%7.0f MB  %s\n' "$mark" "$(( $(stat -c %s "$f") / 1048576 ))" "$f"
  done
  list_sww | xargs -d '\n' -r stat -c %s | awk '{t+=$1} END{printf "共 %d 个 .sww，%.1f GB（✓ = 已转，会跳过）\n",NR,t/1073741824}'
  echo "NAS 剩余：$(df -h "$NAS_BASE" | awk 'NR==2{print $4}')（不过滤的 shp + dat 通常比 sww 大几倍，留足空间）"
  exit 0
fi

ensure_h2d(){
  docker image inspect "$H2D_IMAGE" >/dev/null 2>&1 && { echo "✓ 转换镜像 $H2D_IMAGE 已有"; return 0; }
  docker image inspect anuga-swmm:1.0 >/dev/null 2>&1 || { echo "✗ 没有 anuga-swmm:1.0，造不了转换镜像"; return 1; }
  echo "转换镜像 $H2D_IMAGE 不在，用 h2d/ 里的文件离线构建"
  docker build -t "$H2D_IMAGE" -f "$HERE/h2d/Dockerfile" "$HERE/h2d"
}
conv_one(){
  local sww=$1 out=$2 log=$3
  mkdir -p "$(dirname "$log")"; rm -rf "$out.partial"
  if docker run --rm -e OMP_NUM_THREADS=1 -v "$NAS_BASE:$NAS_BASE" "$H2D_IMAGE" "$sww" -o "$out.partial" --name boundary --src-crs EPSG:32640 $H2D_ARGS > "$log" 2>&1; then
    rm -rf "$out"; mv "$out.partial" "$out"; echo "  ✓ 转换完成 $out"
  else
    echo "  ✗ 转换失败 $sww（日志 $log）"; return 1
  fi
}
export -f conv_one
export NAS_BASE H2D_IMAGE H2D_ARGS

ensure_h2d
mkdir -p "$SHP_ROOT" && touch "$SHP_ROOT/.w" && rm -f "$SHP_ROOT/.w" || { echo "✗ $SHP_ROOT 写不进去"; exit 1; }
LIST=$(mktemp)
list_sww | while IFS= read -r sww; do
  o=$(out_of "$sww"); [ -f "$o/h2d_index.json" ] && continue
  printf '%s\n%s\n%s\n' "$sww" "$o" "$SHP_ROOT/_logs/$(echo "${o#$SHP_ROOT/}" | tr '/' '_').log"
done > "$LIST"
n=$(( $(wc -l < "$LIST") / 3 ))
echo "$(date '+%F %T') 待转换 $n 个 .sww（并行 $CONV_JOBS）→ $SHP_ROOT"
[ "$n" -gt 0 ] && { xargs -d '\n' -n 3 -P "$CONV_JOBS" bash -c 'conv_one "$1" "$2" "$3"' _ < "$LIST" || echo "有转换失败的，看 $SHP_ROOT/_logs/"; }
rm -f "$LIST"
echo "$(date '+%F %T') 结束：$SHP_ROOT 里已有 $(find "$SHP_ROOT" -name h2d_index.json 2>/dev/null | wc -l) 个态"
