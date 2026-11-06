#!/usr/bin/env bash
# 1104 推演的入口：不用写服务器号，按本机主机名自动判断（aim01=服务器 1，uca03=服务器 2，uca06=服务器 3）。
#   scripts/g1104-here.sh <命令> [选项]            命令同 g1104-run.sh：check | start | status | log | stop | resume | report | dtm
#   scripts/g1104-here.sh monitor <命令> [选项]    命令同 g261101-monitor.sh：start | status | once | stop | serve（自动只盯本机分到的 1104 区域）
#   scripts/g1104-here.sh who | plan
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/g261101-host.sh"
[ $# -ge 1 ] || { sed -n '2,6p' "$0"; exit 1; }
case "$1" in
  who) N="$(g261101_resolve "")" || exit 1; echo "本机 $(hostname -s) = 服务器 $N";;
  plan) exec "$HERE/g1104-run.sh" plan;;
  monitor) shift; N="$(g261101_resolve "")" || exit 1
           L="$(grep -E "^SRV${N}=" "$HERE/g1104-run.sh" | head -1 | sed -E 's/^SRV[0-9]="([^"]*)".*/\1/' | tr ' ' ',')"
           [ -n "$L" ] || { echo "✗ 服务器 $N 这次没分到 1104 区域"; exit 1; }
                      MONITOR_REGIONS="$L" exec "$HERE/g1104-monitor.sh" "$N" "$@";;
  *) N="$(g261101_resolve "")" || exit 1; exec "$HERE/g1104-run.sh" "$N" "$@";;
esac
