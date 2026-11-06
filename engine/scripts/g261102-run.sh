#!/usr/bin/env bash
# 261102 新区域（A-A1-A2 / Mussafah 01 / 02 / 03 / 08 / 10 / 06 / RD-G；RB2 这次不推演）9 场景推演：服务器 2、3 的分工 + 一条命令。由 g261102-run.sh 复制改出（不改旧文件）。
#   scripts/g261102-run.sh plan                          打印分工
#   scripts/g261102-run.sh <2|3> <命令> [选项]           在服务器 2 / 3 上执行（平时用 g261102-here.sh，不用写号）
# 命令：check | start | status | log [-f] | stop [--pause] | resume | report | dtm（含义同 g261102-run.sh）
# 说明：
#   · 9 场景 = {33,61,96} mm × {1,2,24} h；服务器 2 / 3 先只跑 1 h / 2 h；24 h 场景写死在服务器 1 的分工里（SRV1，等 ADM LINE / RB3 队列结束后 start，自动只跑 24 h）；服务器 2 / 3 以后也可 start --24h 补自己的区；态按数据真实情况（见各区 region.toml [run] default_scenarios）：出水口 / 井 / 进水口 / 雨水口 / 封堵端 / 管线三态共用（都算 L1），只有泵 / 塘 / 临时设施 / Geocell 分态。二维网格 15 m。
#   · 缺参数的设施（多数泵、塘、移动泵、临时泵、Geocell）不放进模型，页面设备清单标「未建模」；L2 / L2planned 里新增的设施都没建模时，结果和 L1 相同，照常计算、照常出成果。
#   · 服务器 1 这次不参与（ADM LINE / RB3 还在跑）。队列的 pid / 日志沿用 g261101-queue.*（监控靠它判断队列还在不在），所以同一台机器上 g261101 的队列和本队列不能同时开。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ENGINE="$(cd "$HERE/.." && pwd)"
. "$HERE/_lib.sh"
# ---- 分工：工作量大致按 计算域面积 × 算例数（km²·例）：MUS08 203、MUS01 128、MUS02 127 | MUS06 145、RDG 112、AA1A2 78、MUS10 77、MUS03 70。服务器 1 空着不派活。
# 服务器 1：等 ADM LINE / RB3 的队列结束后，只跑这 8 个区的 24 h 场景（共 60 个，写死在这里；start 时自动只跑 24 h，不用加参数、不用设环境变量）。大区在前。
SRV1="MUS08 MUS06 MUS01 MUS02 RDG AA1A2 MUS10 MUS03"
SRV2="MUS08 MUS01 MUS02"
SRV3="MUS06 RDG AA1A2 MUS10 MUS03"
QLOG="$WORK_DIR/run/g261101-queue.log"
QPID="$WORK_DIR/run/g261101-queue.pid"
plan() { echo "服务器 1（48 核，并行 8）：只跑 24 h 场景：$SRV1"; echo "服务器 2（32 核，并行 5）：$SRV2"; echo "服务器 3（32 核，并行 5）：$SRV3"; }
[ $# -ge 1 ] || { sed -n '2,18p' "$0"; exit 1; }
[ "$1" = plan ] && { plan; exit 0; }
SRV="$1"; CMD="${2:-}"; shift 2 || true
case "$SRV" in 1) LIST="$SRV1"; ONLY24=1;; 2) LIST="$SRV2"; ONLY24=0;; 3) LIST="$SRV3"; ONLY24=0;; --queue) LIST="$CMD"; CMD=__queue; ONLY24="${G261102_ONLY24:-0}";; *) die "服务器号只能是 1 / 2 / 3";; esac
# 24 h 场景：默认 start 不含 24 h（region.toml [run] default_scenarios），加 --24h 才只跑 scenarios_24h（服务器 1 空出来后补）。
scn24() { grep -E '^scenarios_24h' "$ENGINE/data/regions/$1/region.toml" | sed -E 's/^[^=]*=[[:space:]]*//' | tr -d '[]" '; }
rs() { echo "$ENGINE/data/regions/$1/$1-server.sh"; }
mkdir -p "$WORK_DIR/run"
case "$CMD" in
  check)  SC24=$ONLY24; [ "${1:-}" = --24h ] && SC24=1
          for r in $LIST; do echo; echo "################ $r ################"; if [ "$SC24" = 1 ]; then "$(rs "$r")" check --scenarios "$(scn24 "$r")" --allow-unmodeled || true; else "$(rs "$r")" check --allow-unmodeled || true; fi; done;;
  start)
    if [ -f "$QPID" ] && kill -0 "$(cat "$QPID")" 2>/dev/null; then die "队列已经在运行（pid $(cat "$QPID")）。看进度：$0 $SRV status"; fi
    if [ "$ONLY24" = 1 ] || [ "${1:-}" = --24h ]; then export G261102_ONLY24=1; echo "只跑 24 h 场景（scenarios_24h）"; else unset G261102_ONLY24; echo "不含 24 h 场景（服务器 2 / 3 先只跑 1 h / 2 h；以后补 24 h：加 --24h）"; fi
    echo "服务器 $SRV 分到：$LIST"
    nohup "$0" --queue "$LIST" >> "$QLOG" 2>&1 &
    echo $! > "$QPID"
    echo "已在后台排队推演（pid $(cat "$QPID")）。看进度：$0 $SRV status    队列日志：$0 $SRV log -f";;
  __queue)
    echo "[$(date '+%F %T')] 队列开始：$LIST"
    for r in $LIST; do
      echo "[$(date '+%F %T')] ▶ $r"
      SC=(); [ "$ONLY24" != 1 ] || SC=(--scenarios "$(scn24 "$r")")
      if "$(rs "$r")" start ${SC[@]+"${SC[@]}"} --allow-unmodeled; then
        sleep 5; docker wait "flood-job-$r" >/dev/null 2>&1 || true
        echo "[$(date '+%F %T')] ■ $r 结束（容器退出码 $(docker inspect -f '{{.State.ExitCode}}' "flood-job-$r" 2>/dev/null || echo ?)）；成果目录：$(rs "$r") report"
      else
        echo "[$(date '+%F %T')] ✗ $r 没启动（check 没通过，原因见上面的输出）→ 跳过"
      fi
    done
    echo "[$(date '+%F %T')] 队列结束";;
  status)
    if [ -f "$QPID" ] && kill -0 "$(cat "$QPID")" 2>/dev/null; then echo "队列：运行中（pid $(cat "$QPID")）"; else echo "队列：没有在运行"; fi
    [ ! -f "$QLOG" ] || { echo "---- 队列日志最近 8 行 ----"; tail -n 8 "$QLOG"; }
    for r in $LIST; do echo; echo "################ $r ################"; "$(rs "$r")" status || true; done;;
  log)    F="$QLOG"; [ -f "$F" ] || die "还没有队列日志"; if [ "${1:-}" = "-f" ]; then exec tail -n 50 -f "$F"; else exec tail -n 50 "$F"; fi;;
  stop)
    [ -f "$QPID" ] && kill "$(cat "$QPID")" 2>/dev/null && rm -f "$QPID" && echo "已停队列（不再启动后面的区域）" || echo "队列没有在运行"
    for r in $LIST; do "$(rs "$r")" stop "$@" || true; done;;
  resume) for r in $LIST; do "$(rs "$r")" resume || true; done;;
  dtm)
    echo "engine/docker/.env 的 DTM_PATH：${DTM_PATH:-（没设，会用包里的 5 m）}"
    for r in $LIST; do echo "== $r"; grep -h "DTM：" "$WORK_DIR/run/$r/run.log" 2>/dev/null | sort -u | head -3 | cut -c1-200 || true; done;;
  report) for r in $LIST; do echo; echo "################ $r ################"; "$(rs "$r")" report || true; done;;
  *) sed -n '2,18p' "$0"; die "不认识的命令：$CMD";;
esac
