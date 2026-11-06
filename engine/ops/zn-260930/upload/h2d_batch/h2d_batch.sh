#!/usr/bin/env bash
# 批量把 NAS 上的 .sww 转成 DTS Cloud h2d（shp + RESULTSxxx.dat）。可重复执行：已有 h2d_index.json 的跳过。
#   bash h2d_batch.sh list                 # 只列出会处理哪些 .sww
#   bash h2d_batch.sh run [N并行，缺省2]    # 开跑（后台用 nohup bash h2d_batch.sh run 2 &）
# 环境变量：SRC（缺省 /media/pop-fs/flood_data/result）、OUT（缺省 /media/pop-fs/flood_data/h2d）、
#           PY（缺省 python3）、CONV（缺省与本脚本同目录的 hydro2d_convert.py）、EXTRA（附加给转换器的参数，如 "--wet-only-shp --drop-dry --drop-threshold 0 --skip-initial"）
set -euo pipefail
SRC=${SRC:-/media/pop-fs/flood_data/result}
OUT=${OUT:-/media/pop-fs/flood_data/h2d}
PY=${PY:-python3}
HERE=$(cd "$(dirname "$0")" && pwd)
CONV=${CONV:-$HERE/hydro2d_convert.py}
EXTRA=${EXTRA:-}

# 各主机 work/c2d/anuga_out/<情景>_<态>.sww（不含 _bak_* 备份）
list(){ find "$SRC" -path '*/work/c2d/anuga_out/*.sww' -not -path '*_bak_*' | sort; }

one(){
  f=$1
  host=$(echo "$f" | sed -n "s#^$SRC/\([^/]*\)/.*#\1#p")
  name=$(basename "$f" .sww)
  dst="$OUT/$host/$name"
  [ -f "$dst/h2d_index.json" ] && { echo "跳过 $host/$name"; return 0; }
  mkdir -p "$dst"
  echo "== $(date +%T) $host/$name"
  $PY -I "$CONV" "$f" -o "$dst" --name "$name" $EXTRA > "$dst/_convert.log" 2>&1 \
    || { echo "!! 失败 $host/$name，见 $dst/_convert.log"; rm -f "$dst/h2d_index.json"; return 1; }
}
export -f one; export SRC OUT PY CONV EXTRA

case "${1:-list}" in
  list) list; echo "共 $(list | wc -l) 个" ;;
  run)  list | xargs -P "${2:-2}" -I{} bash -c 'one "$1"' _ {} ;;
  *) echo "用法：bash $0 list|run [并行数]"; exit 1 ;;
esac
