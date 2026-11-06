#!/usr/bin/env bash
# 扫描 NAS 上所有主机的「runtime/out/交付/」，和库里已登记的对比，找出：
#   · 新交付      —— 目录名是交付形状、各态都有运行记录（算完了），但库里还没有
#   · 有更新的    —— 已登记，但 NAS 上的运行记录比上次抽取的结果新（重跑覆盖过），需要重新登记
#   · 不完整的    —— 有态目录没有运行记录（还在算 / 没拷完），先不登记
#   · 同名多主机  —— 同名交付在几台主机上都有：自动取运行记录最新的那一份
# 只读（只查库、只列 NAS 目录），不抽取、不写库。
#
#   ./scan-nas.sh                          只打印报告
#   ./scan-nas.sh --write /tmp/todo.txt    同时把「新交付 + 有更新的」写成清单（每行：主机 交付目录名；5 m 的排最后），
#                                          再用 ./import-list.sh /tmp/todo.txt 登记
#
# 忽略清单 ./nas-ignore.txt（可用 IGNORE_FILE 改）：每行一个交付目录名，`#` 开头是注释；写在里面的永远不报「新交付」
#（比如雨量写错的旧结果、不想登记的旧版本）。
#
# 环境变量：RESULT（默认 /media/pop-fs/flood_data/result）、DATA_DIR（默认 ./data）、NAME（容器名，默认 flood-aio）、IGNORE_FILE
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULT="${RESULT:-/media/pop-fs/flood_data/result}"; DATA_DIR="${DATA_DIR:-$PWD/data}"; NAME="${NAME:-flood-aio}"
IGNORE_FILE="${IGNORE_FILE:-$PWD/nas-ignore.txt}"
OUT=""; [ "${1:-}" = "--write" ] && OUT="${2:?--write 需要一个文件名}"
RX='^.+_[0-9]+(\.[0-9]+)?mm[0-9]+(\.[0-9]+)?h(_[0-9]+(\.[0-9]+)?m|_G[0-9]+(\.[0-9]+)?)?$'

[ -d "$RESULT" ] || { echo "找不到 $RESULT（用 RESULT=... 指到 NAS 的 result 目录）" >&2; exit 1; }
# 已登记的抽取目录名（= 交付目录名）
REG=$(docker exec "$NAME" psql -U postgres -d syq-pipe -Atc \
  "select regexp_replace(output_dir, '^processed/sim_deliveries/', '') from simulation.run where output_dir like 'processed/sim_deliveries/%'")

IGN=""; [ -f "$IGNORE_FILE" ] && IGN=$(sed 's/#.*//; s/^[[:space:]]*//; s/[[:space:]]*$//' "$IGNORE_FILE" | grep . || true)

newest_record() { find "$1" -maxdepth 4 -name '*耦合运行记录.json' -printf '%T@\n' 2>/dev/null | sort -n | tail -1 | cut -d. -f1; }
complete() {   # 每个态目录要有运行记录；只放说明、没有子目录的态（如旧 .sww 交付里 ≡ L1 的 L2）不算缺
  local s
  for s in "$1"/L[0-9]*/; do
    [ -d "$s" ] || continue
    if find "$s" -maxdepth 3 -name '*耦合运行记录.json' 2>/dev/null | grep -q .; then continue; fi
    [ -z "$(find "$s" -mindepth 1 -type d 2>/dev/null | head -1)" ] || return 1
  done
  return 0
}
fmt() { date -d "@$1" '+%Y-%m-%d %H:%M' 2>/dev/null || echo "$1"; }

declare -A BEST_T BEST_H HOSTS NHOST
SKIPPED=0; IGNORED=0
shopt -s nullglob
for p in "$RESULT"/*/runtime/out/交付/*/; do
  p=${p%/}; name=${p##*/}; host=${p#"$RESULT"/}; host=${host%%/*}
  [[ "$name" =~ $RX ]] || { SKIPPED=$((SKIPPED + 1)); continue; }
  if [ -n "$IGN" ] && grep -qxF "$name" <<<"$IGN"; then IGNORED=$((IGNORED + 1)); continue; fi
  t=$(newest_record "$p"); t=${t:-0}
  HOSTS[$name]+="$host($(fmt "$t"))  "; NHOST[$name]=$(( ${NHOST[$name]:-0} + 1 ))
  if ! complete "$p" || [ "$t" = 0 ]; then continue; fi
  if [ "$t" -gt "${BEST_T[$name]:-0}" ]; then BEST_T[$name]=$t; BEST_H[$name]=$host; fi
done

NEW=(); UPD=(); SAME=0; BAD=(); MULTI=()
for name in "${!HOSTS[@]}"; do
  [ "${NHOST[$name]}" -gt 1 ] && MULTI+=("$name")
  if [ -z "${BEST_H[$name]:-}" ]; then BAD+=("$name"); continue; fi
  if grep -qxF "$name" <<<"$REG"; then
    ex=$(find "$DATA_DIR/processed/sim_deliveries/$name" -maxdepth 2 -name delivery_summary.json -printf '%T@\n' 2>/dev/null | sort -n | tail -1 | cut -d. -f1)
    if [ -n "$ex" ] && [ "${BEST_T[$name]}" -gt $((ex + 60)) ]; then UPD+=("$name"); else SAME=$((SAME + 1)); fi
  else
    NEW+=("$name")
  fi
done

show() { local n; for n in "$@"; do echo "  $n   ← ${BEST_H[$n]:-?}  运行记录 $(fmt "${BEST_T[$n]:-0}")"; done; }
echo "=== NAS 扫描 $(date '+%F %T') | 主机目录 $(ls -d "$RESULT"/*/ 2>/dev/null | wc -l) 个 | 交付 ${#HOSTS[@]} 个（跳过非交付目录 $SKIPPED 个，如 .bak 备份、汇水区；按 nas-ignore.txt 忽略 $IGNORED 个目录）"
echo "已登记且无变化：$SAME 个"
echo; echo "▶ 新交付（${#NEW[@]}）：库里还没有，各态都算完了"; ((${#NEW[@]})) && show $(printf '%s\n' "${NEW[@]}" | sort)
echo; echo "▶ 有更新的（${#UPD[@]}）：已登记，但 NAS 上的运行记录比上次抽取的新，需要重新登记"; ((${#UPD[@]})) && show $(printf '%s\n' "${UPD[@]}" | sort)
echo; echo "▶ 不完整的（${#BAD[@]}）：有态目录没有运行记录（还在算或没拷完），先不登记"
for n in $(printf '%s\n' "${BAD[@]:-}" | sort); do [ -n "$n" ] && echo "  $n   ← ${HOSTS[$n]}"; done
echo; echo "▶ 同名多主机（${#MULTI[@]}）：自动取运行记录最新的那份"
for n in $(printf '%s\n' "${MULTI[@]:-}" | sort); do [ -n "$n" ] && echo "  $n   ${HOSTS[$n]}  → 用 ${BEST_H[$n]:-无}"; done

if [ -n "$OUT" ]; then
  { for n in $(printf '%s\n' "${NEW[@]:-}" "${UPD[@]:-}" | sort | grep -v '_5m$\|_G[0-9.]*$' || true); do [ -n "$n" ] && echo "${BEST_H[$n]} $n"; done
    for n in $(printf '%s\n' "${NEW[@]:-}" "${UPD[@]:-}" | sort | grep '_5m$\|_G[0-9.]*$' || true); do [ -n "$n" ] && echo "${BEST_H[$n]} $n"; done; } > "$OUT"
  echo; echo "已写清单 $OUT（$(wc -l < "$OUT") 项，15 m 在前、5 m 在后）。登记：./import-list.sh $OUT"
fi
