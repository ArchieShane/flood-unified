#!/usr/bin/env bash
# 261101 新区域（RD-C / RD-F / RB3 / Musaffah RD09 / ADM LINE；RD-I 这次不做）9 场景推演：三台服务器的分工 + 一条命令。
#   scripts/g261101-run.sh plan                          打印分工
#   scripts/g261101-run.sh <1|2|3> <命令> [选项]         在服务器 1 / 2 / 3 上执行
# 命令：
#   check    启动前检查本服务器分到的每个区域（环境、数据包、雨型、网格精度、各态、Tc/CN 是否齐全）
#   start    先 check，通过的区域排队依次推演（一个区域跑完再跑下一个，区域内按服务器并行槽位同时跑多个算例）；没通过的区域跳过并写进日志
#   status   队列 + 各区域进度
#   log [-f] 队列日志（<工作目录>/run/g261101-queue.log）；看某个区域的日志：data/regions/<区域>/<区域>-server.sh log -f
#   stop [--pause] / resume     停止（可续跑）/ 暂停恢复
#   report   各区域成果输出目录
#   dtm      确认推演用的地形：engine/docker/.env 的 DTM_PATH，以及各区域日志里的「DTM：…（N 个 tif，0.5 m）」那一行
# 说明：
#   · 9 场景 = {33,61,96} mm × {1,2,24} h；态按数据真实情况：ADM LINE 只跑 L1，其余区域 L1 / L2 / L2planned（见各区 region.toml [run]）。二维网格 15 m。
#   · 缺参数的设施（多数泵、塘、渗井、移动泵、临时泵、Geocell）不放进模型，页面设备清单标「未建模」；L2 里新增的设施都没建模时，L2 和 L1 的结果相同，照常出成果。
#   · 服务器 1：48 核（并行 8）；服务器 2、3：各 32 核（并行 5）。三台共用 NAS，成果写同一个 NAS 目录；job_id = <区域>_<雨量>mm<历时>h（多态在同一个交付根里）。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ENGINE="$(cd "$HERE/.." && pwd)"
. "$HERE/_lib.sh"
# ---- 分工（可以改：每台一行，区域按顺序排队）。工作量大致按 计算域面积 × 算例数：ADM LINE 41 km²×9、RB3 19 km²×27、RD09 15 km²×27、RD-F 5 km²×27、RD-C 2 km²×27
SRV1="ADMLINE RB3"        # 内存最大的 188 GB 给最大的两个区
SRV2="RD09 RDC"
SRV3="RDF"
QLOG="$WORK_DIR/run/g261101-queue.log"
QPID="$WORK_DIR/run/g261101-queue.pid"
plan() { echo "服务器 1（48 核，并行 8）：$SRV1"; echo "服务器 2（32 核，并行 5）：$SRV2"; echo "服务器 3（32 核，并行 5）：$SRV3"; }
[ $# -ge 1 ] || { sed -n '2,18p' "$0"; exit 1; }
[ "$1" = plan ] && { plan; exit 0; }
SRV="$1"; CMD="${2:-}"; shift 2 || true
case "$SRV" in 1) LIST="$SRV1";; 2) LIST="$SRV2";; 3) LIST="$SRV3";; --queue) LIST="$CMD"; CMD=__queue;; *) die "服务器号只能是 1 / 2 / 3";; esac
rs() { echo "$ENGINE/data/regions/$1/$1-server.sh"; }
mkdir -p "$WORK_DIR/run"
case "$CMD" in
  check)  for r in $LIST; do echo; echo "################ $r ################"; "$(rs "$r")" check --allow-unmodeled || true; done;;
  start)
    if [ -f "$QPID" ] && kill -0 "$(cat "$QPID")" 2>/dev/null; then die "队列已经在运行（pid $(cat "$QPID")）。看进度：$0 $SRV status"; fi
    echo "服务器 $SRV 分到：$LIST"
    nohup "$0" --queue "$LIST" >> "$QLOG" 2>&1 &
    echo $! > "$QPID"
    echo "已在后台排队推演（pid $(cat "$QPID")）。看进度：$0 $SRV status    队列日志：$0 $SRV log -f";;
  __queue)
    echo "[$(date '+%F %T')] 队列开始：$LIST"
    for r in $LIST; do
      echo "[$(date '+%F %T')] ▶ $r"
      if "$(rs "$r")" start --allow-unmodeled; then
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
