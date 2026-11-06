#!/usr/bin/env bash
# ADM 全市 100 m v2 · 把 9 场成果收集到与 result 平级的英文目录（任一台服务器上跑一次，三台共用 NAS）
#
#   bash adm_100m_v2_collect.sh          # 只列：9 场各在哪、是否齐全、要拷多大（不拷）
#   bash adm_100m_v2_collect.sh copy     # 真拷到 $NAS_BASE/$EXPORT_NAME/，再写 MANIFEST.csv（路径、大小、sha256）并扫描路径无非 ASCII
#
# 来源：$NAS_BASE/result/<主机>/ADM_City_<雨量>mm<历时>h_100m_v2/（只收 run_stats.json 里 missing 为空的场）
# 目标：$NAS_BASE/$EXPORT_NAME/ADM_City_<雨量>mm<历时>h_100m_v2/ ...（不分主机，一场一个目录；路径和文件名都是英文，来源已经是英文，原样拷）
# 环境变量：NAS_BASE（缺省 /media/pop-fs/flood_data）、EXPORT_NAME（缺省 adm_city_100m_v2）、MANIFEST=0 不算 sha256（大文件慢）
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
NAS_BASE=${NAS_BASE:-/media/pop-fs/flood_data}
EXPORT_NAME=${EXPORT_NAME:-adm_city_100m_v2}
DEST=$NAS_BASE/$EXPORT_NAME
TSV=$HERE/cases/ad100v2/adm100v2_all.tsv
GO=0; [ "${1:-}" = copy ] && GO=1
case "$EXPORT_NAME" in */*|"") echo "EXPORT_NAME 只能是一个目录名"; exit 1;; esac
rain_of(){ echo "$1" | sed -E 's/^[A-Z]+_M([0-9]+)H([0-9]+)$/\1/'; }
dur_of(){ echo "$1" | sed -E 's/^[A-Z]+_M([0-9]+)H([0-9]+)$/\2/'; }
echo "== 来源 $NAS_BASE/result/*/   目标 $DEST   [$([ $GO = 1 ] && echo 真拷 || echo 只列)]"
ok=0; bad=0; LIST=()
while IFS=$'\t' read -r tag _ dir grid _ _ _; do
  [ -n "$tag" ] || continue
  dd="${dir}_$(rain_of "$tag")mm$(dur_of "$tag")h_${grid}m_v2"
  src=$(ls -d "$NAS_BASE"/result/*/"$dd" 2>/dev/null | head -1)
  if [ -z "$src" ]; then echo "  ✗ $tag：没找到 $dd"; bad=$((bad+1)); continue; fi
  if ! grep -q '"missing": \[\]' "$src/statistics/run_stats.json" 2>/dev/null; then echo "  ✗ $tag：$src 未完成或有缺项"; bad=$((bad+1)); continue; fi
  n_sww=$(find "$src" -name '*.sww' | wc -l); n_npz=$(find "$src" -name '*_2d.npz' | wc -l); n_tif=$(find "$src" -name '*_max_depth.tif' | wc -l)
  echo "  ✓ $tag  $(du -sh "$src" | cut -f1)  sww $n_sww / npz $n_npz / tif $n_tif   $src"
  [ "$n_sww" -ge 1 ] && [ "$n_npz" -ge 1 ] && [ "$n_tif" -ge 1 ] || { echo "    ✗ 缺 sww / npz / tif"; bad=$((bad+1)); continue; }
  ok=$((ok+1)); LIST+=("$src")
done < "$TSV"
echo "-- 齐全 $ok 场，有问题 $bad 场"
[ "$GO" = 1 ] || { echo "（只是列出；确认无误后：bash $0 copy）"; exit 0; }
[ "$bad" = 0 ] || { echo "有问题的场先处理；不拷"; exit 1; }
mkdir -p "$DEST" || exit 1
for src in "${LIST[@]}"; do
  echo "拷 $(basename "$src") …"; cp -a "$src" "$DEST/" || { echo "  ✗ 拷贝失败"; exit 1; }
done
if [ -n "$(LC_ALL=C find "$DEST" -print | LC_ALL=C grep -P '[^\x00-\x7f]' | head -1)" ]; then echo "✗ 目标里有非 ASCII 路径"; exit 1; fi
if [ "${MANIFEST:-1}" != 0 ]; then
  echo "写 MANIFEST.csv（sha256，大文件要一阵）…"
  ( cd "$DEST" && echo "path,bytes,sha256" > MANIFEST.csv && find . -type f ! -name MANIFEST.csv -print0 | sort -z | while IFS= read -r -d '' f; do echo "${f#./},$(stat -c %s "$f"),$(sha256sum "$f" | cut -d' ' -f1)"; done >> MANIFEST.csv )
fi
echo "完成：$DEST（$(du -sh "$DEST" | cut -f1)，$ok 场，路径无非 ASCII）"
