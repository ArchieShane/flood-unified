#!/usr/bin/env bash
# 启动推演：先检查环境（不通过就不启动），再执行；结束后把各成果数据的输出目录列出来。
#   scripts/start.sh <区域ID> --scenarios S33D1,S61D24 [--state L1] [--detach]
#   --detach  后台跑（推演要很久），之后用 scripts/report.sh 看成果目录
. "$(dirname "$0")/_lib.sh"
[ $# -ge 1 ] || { sed -n '2,5p' "$0"; exit 1; }
REGION="$1"; shift
SCN=""; STATE="L1"; DETACH=0
while [ $# -gt 0 ]; do case "$1" in --scenarios) SCN="$2"; shift 2;; --state) STATE="$2"; shift 2;; --detach) DETACH=1; shift;; *) die "不认识的参数：$1";; esac; done
[ -n "$SCN" ] || die "需要 --scenarios（如 S33D1,S61D24）"
"$ENGINE_DIR/scripts/check.sh" "$REGION" --scenarios "$SCN" --state "$STATE" || exit 1
RUN="$ENGINE_DIR/tools/run_region.py"
[ -f "$RUN" ] || die "推演脚本 tools/run_region.py 还没写（写之前要先确认 L1 / L2 / L2planned 各含哪些数据）。环境检查已通过。"
NAME="flood-job-$REGION-$(date +%Y%m%d-%H%M%S)"
RUNARGS=(-e SWBRAIN_ENV=container -e SWBRAIN_SWMM_WORK=/work -e OMP_NUM_THREADS="$THREADS" -e MAKANI_PASSWORD="${MAKANI_PASSWORD:-}"
         -v "$ENGINE_DIR:/app:ro" -v "$NAS_DIR:/nas" -v "$WORK_DIR:/work" "${DTM_ARGS[@]}" --name "$NAME")
echo; echo "▶ 启动推演：区域 $REGION，场景 $SCN，态 $STATE，容器 $NAME"
if [ "$DETACH" = 1 ]; then
  docker run -d "${RUNARGS[@]}" "$IMAGE" python3 -u /app/tools/run_region.py --region "$REGION" --scenarios "$SCN" --state "$STATE" --host "$(hostname -s)" >/dev/null
  echo "已在后台运行。看进度：docker logs -f $NAME"
  echo "结束后看成果目录：scripts/report.sh $REGION --scenarios $SCN"
  exit 0
fi
docker run --rm "${RUNARGS[@]}" "$IMAGE" python3 -u /app/tools/run_region.py --region "$REGION" --scenarios "$SCN" --state "$STATE" --host "$(hostname -s)" || { echo "✘ 推演失败（退出码 $?）"; "$ENGINE_DIR/scripts/report.sh" "$REGION" --scenarios "$SCN" --state "$STATE" || true; exit 1; }
"$ENGINE_DIR/scripts/report.sh" "$REGION" --scenarios "$SCN" --state "$STATE"
