#!/usr/bin/env bash
# 261101 推演的入口：不用写服务器号，按本机主机名自动判断（aim01=服务器 1，uca03=服务器 2，uca06=服务器 3）。
#   scripts/g261101-here.sh <命令> [选项]            命令同 g261101-run.sh：check | start | status | log | stop | resume | report | dtm
#   scripts/g261101-here.sh monitor <命令> [选项]    命令同 g261101-monitor.sh：start | status | once | stop | serve
#   scripts/g261101-here.sh who                      只显示这台机器被判断成第几台
# 例：scripts/g261101-here.sh status      scripts/g261101-here.sh monitor start
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/g261101-host.sh"
[ $# -ge 1 ] || { sed -n '2,7p' "$0"; exit 1; }
case "$1" in
  who) N="$(g261101_resolve "")" || exit 1; echo "本机 $(hostname -s) = 服务器 $N";;
  plan) exec "$HERE/g261101-run.sh" plan;;
  monitor) shift; N="$(g261101_resolve "")" || exit 1; exec "$HERE/g261101-monitor.sh" "$N" "$@";;
  *) N="$(g261101_resolve "")" || exit 1; exec "$HERE/g261101-run.sh" "$N" "$@";;
esac
