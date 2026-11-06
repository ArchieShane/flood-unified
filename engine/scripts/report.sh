#!/usr/bin/env bash
# 列出各场景成果数据的输出目录（deliveries / flood2d / shp_dat）和关键文件是否齐全。
#   scripts/report.sh <区域ID> --scenarios S33D1,S61D24 [--state L1] [--json out.json]
. "$(dirname "$0")/_lib.sh"
[ $# -ge 1 ] || { sed -n '2,3p' "$0"; exit 1; }
REGION="$1"; shift
SCN=""; STATE="L1"; EXTRA=()
while [ $# -gt 0 ]; do case "$1" in --scenarios) SCN="$2"; shift 2;; --state) STATE="$2"; shift 2;; --json) EXTRA+=(--json "/nas/$2"); shift 2;; *) die "不认识的参数：$1";; esac; done
[ -n "$SCN" ] || die "需要 --scenarios"
dock tools/report_outputs.py --region "$REGION" --scenarios "$SCN" --state "$STATE" --nas /nas --host "$(hostname -s)" "${EXTRA[@]}"
echo "（以上是容器内路径 /nas/…；宿主机上对应 $NAS_DIR/…）"
