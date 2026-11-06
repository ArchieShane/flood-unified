#!/usr/bin/env bash
# 区域推演服务脚本：一个脚本管整个推演（检查 / 启动 / 停止 / 进度 / 日志 / 成果目录）。
# 一般不直接用它，用每个区域自己的 <区域>-server.sh（new_region.sh 自动生成，在 data/regions/<区域>/ 里）：
#
#   RDA-server.sh check  [--scenarios S33D1,S61D1@L2] [--state L1] [--allow-unmodeled]   启动前检查环境（场景写法 S<雨量>D<历时>[@态]，不带 @ = --state，缺省 L1）
#   RDA-server.sh start  [--scenarios …] [--state L1] [--allow-unmodeled]   先 check，不通过不启动；通过后在后台启动（容器 flood-job-<区域>）
#   RDA-server.sh status                                            容器状态 + CPU / 内存 + 各场景进度（阶段、百分比、模拟时间、用时）
#   RDA-server.sh log    [-f] [-n 100] [--scenario S33D1]           推演日志（-f 持续跟随）
#   RDA-server.sh stop   [--pause]                                  停止（可续跑：已有成果的步骤会跳过）；--pause 只暂停（docker pause）
#   RDA-server.sh resume                                            恢复暂停的推演
#   RDA-server.sh report [--scenarios …]                            各场景成果数据的输出目录
#
# 不带 --scenarios 时用 data/regions/<区域>/region.toml 的 [run] default_scenarios。
. "$(dirname "$0")/_lib.sh"
REGION="${1:-}"; CMD="${2:-}"
[ -n "$REGION" ] && [ -n "$CMD" ] || { sed -n '2,16p' "$0"; exit 1; }
shift 2
CONTAINER="flood-job-$REGION"
RUNDIR="$WORK_DIR/run/$REGION"          # 宿主机路径；容器内是 /work/run/<区域>
RTOML="$ENGINE_DIR/data/regions/$REGION/region.toml"
[ -f "$RTOML" ] || die "没有区域 $REGION（$RTOML 不存在，先 scripts/new_region.sh）"

rt_get() { # 读 region.toml 的键；宿主机 python3 不够新（<3.11）就用镜像里的
  if python3 -c 'import tomllib' 2>/dev/null; then python3 "$ENGINE_DIR/tools/region_toml.py" get "$RTOML" "$1"
  else docker run --rm -v "$ENGINE_DIR:/app:ro" "$IMAGE" python3 /app/tools/region_toml.py get "/app/data/regions/$REGION/region.toml" "$1"; fi
}
parse_opts() { SCN=""; STATE=""; PAUSE=0; FOLLOW=0; LINES=50; ONE=""; ALLOW=""
  while [ $# -gt 0 ]; do case "$1" in
    --scenarios) SCN="$2"; shift 2;; --state) STATE="$2"; shift 2;; --pause) PAUSE=1; shift;; --allow-unmodeled) ALLOW="--allow-unmodeled"; shift;;
    -f|--follow) FOLLOW=1; shift;; -n) LINES="$2"; shift 2;; --scenario) ONE="$2"; shift 2;; *) die "不认识的参数：$1";; esac; done
  [ -n "$SCN" ] || SCN="$(rt_get run.default_scenarios)"
  [ -n "$STATE" ] || STATE="$(rt_get run.default_state)"; }
cstate() { local o; if o=$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null) && [ -n "$o" ]; then echo "$o"; else echo none; fi; }

case "$CMD" in
  check)
    parse_opts "$@"; exec "$ENGINE_DIR/scripts/check.sh" "$REGION" --scenarios "$SCN" --state "$STATE" $ALLOW;;

  start)
    parse_opts "$@"; need_docker
    "$ENGINE_DIR/scripts/check.sh" "$REGION" --scenarios "$SCN" --state "$STATE" $ALLOW || exit 1
    GRID="$(rt_get grid.res_m 2>/dev/null || true)"; GRID="${GRID:-15}"        # 二维网格精度（region.toml [grid] res_m，本次 15 m），推演和成果都按它标注
    case "$(cstate)" in running|paused|restarting) die "$CONTAINER 已在运行（$(cstate)）。看进度：$REGION-server.sh status；要重跑先 stop";; esac
    [ -f "$ENGINE_DIR/tools/run_region.py" ] || die "推演主脚本 tools/run_region.py 还没写（写之前要先确认 L1 / L2 / L2planned 各含哪些数据）。环境检查已通过。"
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    mkdir -p "$RUNDIR"
    dock tools/run_status.py init "/work/run/$REGION/status.json" "$REGION" "$SCN" "$(hostname -s)"
    echo "▶ 启动推演：区域 $REGION，场景 $SCN，缺省态 $STATE，二维网格 ${GRID} m，容器 $CONTAINER（后台）"
    docker run -d --name "$CONTAINER" -e SWBRAIN_ENV=container -e SWBRAIN_SWMM_WORK=/work -e OMP_NUM_THREADS="$THREADS" -e MAKANI_PASSWORD="${MAKANI_PASSWORD:-}" \
      -v "$ENGINE_DIR:/app:ro" -v "$NAS_DIR:/nas" -v "$WORK_DIR:/work" ${DTM_ARGS[@]+"${DTM_ARGS[@]}"} "$IMAGE" \
      bash -c "set -o pipefail; python3 -u /app/tools/run_region.py --region '$REGION' --scenarios '$SCN' --state '$STATE' --grid '$GRID' $ALLOW --host '$(hostname -s)' --status /work/run/$REGION/status.json 2>&1 | tee -a /work/run/$REGION/run.log" >/dev/null
    echo "已启动。看进度：$REGION-server.sh status    看日志：$REGION-server.sh log -f    停止：$REGION-server.sh stop"
    echo "结束后各成果输出目录：$REGION-server.sh report";;

  status)
    echo "==== 容器 $CONTAINER ===="
    S="$(cstate)"
    if [ "$S" = none ]; then echo "没有（没启动过，或已被清理）"; else
      docker inspect -f '状态 {{.State.Status}}  启动于 {{.State.StartedAt}}  退出码 {{.State.ExitCode}}' "$CONTAINER"
      [ "$S" = running ] && docker stats --no-stream --format 'CPU {{.CPUPerc}}  内存 {{.MemUsage}}（{{.MemPerc}}）' "$CONTAINER" || true; fi
    echo; echo "==== 推演进度 ===="
    dock tools/run_status.py show "/work/run/$REGION/status.json" || true
    if [ -f "$RUNDIR/run.log" ]; then echo; echo "==== 最近日志（完整日志：$REGION-server.sh log -f）===="; tail -n 5 "$RUNDIR/run.log"; fi;;

  log)
    parse_opts "$@"
    F="$RUNDIR/run.log"; [ -z "$ONE" ] || F="$RUNDIR/$ONE.log"
    if [ -f "$F" ]; then [ "$FOLLOW" = 1 ] && exec tail -n "$LINES" -f "$F" || exec tail -n "$LINES" "$F"
    elif [ "$(cstate)" != none ]; then [ "$FOLLOW" = 1 ] && exec docker logs -f --tail "$LINES" "$CONTAINER" || exec docker logs --tail "$LINES" "$CONTAINER"
    else die "还没有日志（$F 不存在，也没有容器）"; fi;;

  stop)
    parse_opts "$@"; need_docker
    case "$(cstate)" in
      none) echo "没有在运行的 $CONTAINER";;
      running|restarting)
        if [ "$PAUSE" = 1 ]; then docker pause "$CONTAINER" >/dev/null && dock tools/run_status.py state "/work/run/$REGION/status.json" stopped "已暂停（docker pause），resume 恢复" && echo "⏸ 已暂停 $CONTAINER（保留进度；$REGION-server.sh resume 恢复）"
        else echo "⏹ 停止 $CONTAINER（最多等 120 s 让当前步骤收尾）…"; docker stop -t 120 "$CONTAINER" >/dev/null; dock tools/run_status.py state "/work/run/$REGION/status.json" stopped "已停止，start 后会跳过已完成的步骤"; echo "已停止。再 start 会接着跑（已有成果的步骤跳过）"; fi;;
      paused) docker unpause "$CONTAINER" >/dev/null; docker stop -t 120 "$CONTAINER" >/dev/null; dock tools/run_status.py state "/work/run/$REGION/status.json" stopped "已停止"; echo "已停止";;
      *) echo "$CONTAINER 已经是 $(cstate)";; esac;;

  resume)
    [ "$(cstate)" = paused ] && docker unpause "$CONTAINER" >/dev/null && dock tools/run_status.py state "/work/run/$REGION/status.json" running "已恢复" && echo "▶ 已恢复 $CONTAINER" || die "没有暂停的 $CONTAINER";;

  report)
    parse_opts "$@"; exec "$ENGINE_DIR/scripts/report.sh" "$REGION" --scenarios "$SCN" --state "$STATE";;

  *) sed -n '2,16p' "$0"; die "不认识的命令：$CMD";;
esac
