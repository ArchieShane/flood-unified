#!/usr/bin/env bash
# 列出 NAS 上可转换的 .sww（去重、带大小），按序号选择后调用 h2d-convert 镜像转成 shp + dat。
#   bash h2d_pick.sh [list] [关键字]   # 关键字可省（运行后会问），如 M61H1、Mussafah、RG_BAT；list = 只列表不转换
# 去重：按"大小 + 头 1 MB + 尾 4 MB 的 md5"判同一份数据（交付目录里的副本、多台机器上的同名结果只留一份；
#       优先留 work/c2d/anuga_out 里的原始文件）。指纹缓存在 $OUT/.fp_cache，第二次运行很快。ALL=1 则不去重。
# 排除名字里带 .bak / _bak_ 的目录。
# 环境变量：SRC（缺省 /media/pop-fs/flood_data/result）、OUT（缺省 /media/pop-fs/flood_data/h2d）、
#           IMG（缺省 h2d-convert:1.0）、EXTRA（缺省 "--src-crs EPSG:32640 --wet-only-shp --drop-dry --drop-threshold 0.01 --no-csv"，即 shp、dat 都去掉水深 <= 1 cm 的部分）
set -euo pipefail
SRC=${SRC:-/media/pop-fs/flood_data/result}
OUT=${OUT:-/media/pop-fs/flood_data/h2d}
IMG=${IMG:-h2d-convert:1.0}
EXTRA=${EXTRA:---src-crs EPSG:32640 --wet-only-shp --drop-dry --drop-threshold 0.01 --no-csv}
ROOT=$(dirname "$SRC")            # 挂到容器 /data，SRC、OUT 都得在它下面
[ "${SRC#"$ROOT"/}" != "$SRC" ] && [ "${OUT#"$ROOT"/}" != "$OUT" ] || { echo "SRC、OUT 须在同一父目录下"; exit 1; }

LISTONLY=0; [ "${1:-}" = list ] && { LISTONLY=1; shift; }
KW=${1:-}

# 全部 .sww（不含备份），anuga_out 里的排前面，其余按路径排序
mapfile -t ALLF < <(find "$SRC" -type f -name '*.sww' -not -path '*.bak*' -not -path '*_bak_*' -printf '%p\n' \
  | awk '{ k = ($0 ~ /\/work\/c2d\/anuga_out\//) ? 0 : 1; print k "\t" $0 }' | sort -t$'\t' -k1,1n -k2,2 | cut -f2-)
[ ${#ALLF[@]} -gt 0 ] || { echo "$SRC 下没有 .sww"; exit 1; }

# ---- 去重 ----
mkdir -p "$OUT"; CACHE="$OUT/.fp_cache"; touch "$CACHE"
declare -A FPC; while IFS=$'\t' read -r key fp; do FPC["$key"]=$fp; done < "$CACHE"
fingerprint(){ # 路径 → 指纹（带缓存，key = 路径 + 大小 + 修改时间）
  local f=$1 key; key="$f|$(stat -c '%s|%Y' "$f")"
  if [ -z "${FPC[$key]:-}" ]; then
    FPC["$key"]=$({ stat -c '%s' "$f"; head -c 1048576 "$f"; tail -c 4194304 "$f"; } | md5sum | cut -d' ' -f1)
    printf '%s\t%s\n' "$key" "${FPC[$key]}" >> "$CACHE"
  fi
  echo "${FPC[$key]}"
}
FILES=(); declare -A SEEN
if [ "${ALL:-0}" = 1 ]; then FILES=("${ALLF[@]}")
else
  echo "（去重中：第一次要读每个文件的头尾，稍等；之后有缓存）" >&2
  for f in "${ALLF[@]}"; do
    fp=$(fingerprint "$f")
    if [ -z "${SEEN[$fp]:-}" ]; then SEEN[$fp]=1; FILES+=("$f"); fi
  done
fi

# ---- 名字：anuga_out 里的用文件名；交付目录里的用 "<方案目录>__<态目录>" ----
name_of(){
  local f=$1
  if [[ $f == */work/c2d/anuga_out/* ]]; then basename "$f" .sww
  else local d1; d1=$(dirname "$f"); local st; st=$(basename "$(dirname "$d1")"); local sc; sc=$(basename "$(dirname "$(dirname "$d1")")")
       echo "${sc}__${st}"; fi
}
dest_of(){ local rel=${1#"$SRC"/}; echo "$OUT/${rel%%/*}/$(name_of "$1")"; }


# ---- 说明：区域 | 降雨量 历时 网格 | 态（从名字/目录名解析；认不出的显示 ?）----
desc_of(){
  local f=$1 n area mm h g st
  n=$(basename "$f" .sww)
  if [[ $f == */work/c2d/anuga_out/* ]]; then
    if [[ $n =~ ^(C1|MS|RG_BAT|RG_BNY|RG_YAS)_M([0-9]+)(H([0-9]+))?(G5)?_(L[0-9A-Z]+)$ ]]; then
      case ${BASH_REMATCH[1]} in C1) area=C1;; MS) area=Mussafah;; RG_BAT) area="Al Bateen";; RG_BNY) area="Bani Yas";; RG_YAS) area=Yas;; esac
      mm=${BASH_REMATCH[2]}; h=${BASH_REMATCH[4]:-2}; g=15; [ -n "${BASH_REMATCH[5]}" ] && g=5
      case ${BASH_REMATCH[6]} in L1) st=L1;; L2) st=L2;; L2P) st=L2+planned;; L2S3) st=L2+suggested;; *) st=${BASH_REMATCH[6]};; esac
      echo "$area | ${mm}mm ${h}h ${g}m | $st"; return
    fi
  else
    local d1 stdir scdir; d1=$(dirname "$f"); stdir=$(basename "$(dirname "$d1")"); scdir=$(basename "$(dirname "$(dirname "$d1")")")
    if [[ $scdir =~ ([0-9]+)mm([0-9]+)h(_5m)? ]]; then
      mm=${BASH_REMATCH[1]}; h=${BASH_REMATCH[2]}; g=15; [ -n "${BASH_REMATCH[3]}" ] && g=5
      case $scdir in 全市*) area=全市; g=250;; C1_*) area=C1;; Mussafah*) area=Mussafah; [[ $scdir == *模型版* ]] || g="?";;
        Al_Bateen*) area="Al Bateen";; Bani_Yas*) area="Bani Yas";; Yas_*) area=Yas;; *) area="?";; esac
      st=${stdir%%_*}
      echo "$area | ${mm}mm ${h}h ${g}m | $st"; return
    fi
  fi
  echo "?"
}

# ---- 关键字筛选 ----
if [ -z "$KW" ] && [ $LISTONLY = 0 ]; then
  read -r -p "关键字筛选（如 61mm、5m、Mussafah、L2+planned；回车=显示全部）: " KW
fi
SHOW=()
for f in "${FILES[@]}"; do
  [ -z "$KW" ] || [[ "$(desc_of "$f")|$(name_of "$f")|${f#"$SRC"/}" == *"$KW"* ]] && SHOW+=("$f")
done
[ ${#SHOW[@]} -gt 0 ] || { echo "没有匹配「$KW」的文件"; exit 1; }

# ---- 转换记录：每转完一个追加一行；删除输出目录后仍能看出哪些转过 ----
REC="$OUT/converted.tsv"
declare -A DONE
if [ -s "$REC" ]; then while IFS=$'\t' read -r t _ _ _ src _; do DONE["$src"]=$t; done < <(tail -n +2 "$REC"); fi

printf '\n%4s  %7s  %-36s %-24s %s\n' 序号 大小 说明 名字 来源
i=0
for f in "${SHOW[@]}"; do
  i=$((i + 1)); d=$(dest_of "$f"); mark=""
  if [ -n "${DONE[${f#"$SRC"/}]:-}" ]; then
    mark="  [已转换 ${DONE[${f#"$SRC"/}]}]"; [ -f "$d/h2d_index.json" ] || mark="$mark（输出目录已不在）"
  elif [ -f "$d/h2d_index.json" ]; then mark="  [已转换]"; fi
  printf '%4d  %7s  %-36s %-24s %s%s\n' "$i" "$(du -h "$f" | cut -f1)" "$(desc_of "$f")" "$(name_of "$f")" "${f#"$SRC"/}" "$mark"
done
echo "共 ${#SHOW[@]} 个（去重前 ${#ALLF[@]} 个）"
[ $LISTONLY = 1 ] && exit 0

echo; read -r -p "输入要转换的序号（空格分隔，可用 5-7，a=全部，q=退出）: " ans
[ "$ans" = q ] || [ -z "$ans" ] && exit 0
SEL=()
if [ "$ans" = a ]; then for ((k = 1; k <= ${#SHOW[@]}; k++)); do SEL+=("$k"); done
else
  for t in $ans; do
    if [[ $t =~ ^([0-9]+)-([0-9]+)$ ]]; then for ((k = BASH_REMATCH[1]; k <= BASH_REMATCH[2]; k++)); do SEL+=("$k"); done
    elif [[ $t =~ ^[0-9]+$ ]]; then SEL+=("$t")
    else echo "无法识别：$t"; exit 1; fi
  done
fi

for n in "${SEL[@]}"; do
  [ "$n" -ge 1 ] && [ "$n" -le ${#SHOW[@]} ] || { echo "序号 $n 超出范围，跳过"; continue; }
  f=${SHOW[$((n - 1))]}; d=$(dest_of "$f")
  if [ -f "$d/h2d_index.json" ] || [ -n "${DONE[${f#"$SRC"/}]:-}" ]; then
    read -r -p "[$n] 已转换过（$d），重做吗？(y/N) " yn; [ "$yn" = y ] || continue
  fi
  mkdir -p "$d"
  echo "== $(date +%T) [$n] ${f#"$SRC"/} → $d"
  t0=$(date +%s)
  docker run --rm -v "$ROOT":/data "$IMG" "/data/${f#"$ROOT"/}" -o "/data/${d#"$ROOT"/}" --name boundary $EXTRA     2>&1 | tee "$d/_convert.log" | tail -n 5
  if [ -f "$d/h2d_index.json" ]; then
    sz=$(du -sh "$d" | cut -f1); nd=$(ls "$d/dat" 2>/dev/null | grep -c '\.dat$' || true)
    [ -s "$REC" ] || printf '完成时间	主机	名字	说明	源文件	输出目录	输出大小	帧数	耗时秒
' > "$REC"
    printf '%s	%s	%s	%s	%s	%s	%s	%s	%s
' "$(date '+%F %T')" "$(rel=${f#"$SRC"/}; echo "${rel%%/*}")" "$(name_of "$f")" "$(desc_of "$f")"       "${f#"$SRC"/}" "$d" "$sz" "$nd" "$(( $(date +%s) - t0 ))" >> "$REC"
    echo "完成：$sz  $nd 帧  $d    （已记入 $REC）"
  else echo "!! 失败，见 $d/_convert.log"; fi
done
