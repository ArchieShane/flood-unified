#!/usr/bin/env bash
# 261101 六区推演的监控 + 自动善后。每台服务器各启动一个（后台常驻，不用一直盯着）：
#   （服务器号 <1|2|3> 可以不写，脚本按主机名自动判断；写错会拦住。下面的 <1|2|3> 都可省略）
#   scripts/g1104-monitor.sh <1|2|3> start      后台启动（每 5 分钟一圈）
#   scripts/g1104-monitor.sh <1|2|3> status     看汇总（三台合在一起，内容来自 NAS 上的 monitor/summary.txt）
#   scripts/g1104-monitor.sh <1|2|3> once       现在立刻做一圈并打印
#   scripts/g1104-monitor.sh <1|2|3> stop       停监控（不影响推演）
#   scripts/g1104-monitor.sh <1|2|3> serve      在这台起一个网页（端口 8090，用环境变量 MONITOR_PORT 改；浏览器打开 http://<服务器IP>:8090/ 看三台进度，每 60 秒自动刷新）
#   scripts/g1104-monitor.sh <1|2|3> start --no-heal   只监控报警，不自动续跑
#   MONITOR_REGIONS=RDF,RB3 scripts/g1104-monitor.sh 3 start   这台额外跑了别的区域时，指定要盯哪些区域（缺省 = 这台分到的）
# 做什么、怎么善后见 tools/g261102_monitor.py 开头。监控结果写在 <NAS>/monitor/：summary.txt、index.html、alerts.log、server<N>.json（三台共用 NAS，所以三台能互相看见）。
. "$(dirname "$0")/_lib.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/g261101-host.sh"
# 服务器号可以不写（按主机名自动判断）；写了就必须和主机名一致
if [[ "${1:-}" =~ ^[123]$ ]]; then SRV="$(g261101_resolve "$1")" || exit 1; shift; else SRV="$(g261101_resolve "")" || { sed -n '2,12p' "$0"; exit 1; }; fi
CMD="${1:-}"; shift || true
LIST="$(grep -E "^SRV${SRV}=" "$HERE/g1104-run.sh" | head -1 | sed -E 's/^SRV[0-9]="([^"]*)".*/\1/' | tr ' ' ',')"
[ -n "$LIST" ] || die "没从 g1104-run.sh 里读到服务器 $SRV 的区域"
[ -z "${MONITOR_REGIONS:-}" ] || LIST="${MONITOR_REGIONS// /,}"      # 临时多盯别的区域（如这台额外跑了 RB3）：MONITOR_REGIONS=RDF,RB3 $0 3 start
SLOTS=5; [ "$SRV" = 1 ] && SLOTS=8
PIDF="$WORK_DIR/run/g261101-monitor.pid"; LOGF="$WORK_DIR/run/g261101-monitor.log"; MDIR="$NAS_DIR/monitor"; SPID="$WORK_DIR/run/g261101-monitor-serve.pid"
mkdir -p "$WORK_DIR/run" "$MDIR"
PY=(python3 "$ENGINE_DIR/tools/g261102_monitor.py" --server "$SRV" --regions "$LIST" --engine "$ENGINE_DIR" --nas "$NAS_DIR" --work "$WORK_DIR" --slots "$SLOTS")
alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
case "$CMD" in
  start) alive "$PIDF" && die "监控已经在运行（pid $(cat "$PIDF")）"
         nohup "${PY[@]}" "$@" >> "$LOGF" 2>&1 & echo $! > "$PIDF"; sleep 2
         alive "$PIDF" && echo "✔ 监控已在后台运行（pid $(cat "$PIDF")），服务器 $SRV 的区域：$LIST。看汇总：$0 $SRV status；网页：$0 $SRV serve" || { echo "✗ 监控没起来，看 $LOGF"; tail -5 "$LOGF"; exit 1; };;
  stop) alive "$PIDF" && kill "$(cat "$PIDF")" && rm -f "$PIDF" && echo "已停监控" || echo "监控没有在运行";;
  once) exec "${PY[@]}" --once "$@";;
  status) alive "$PIDF" && echo "监控：运行中（pid $(cat "$PIDF")）" || echo "监控：没有在运行（$0 $SRV start）"
          [ -f "$MDIR/summary.txt" ] && cat "$MDIR/summary.txt" || echo "（还没有汇总，监控启动后 1 分钟内会生成）";;
  serve) alive "$SPID" && die "网页已经在运行（pid $(cat "$SPID")）"
         PORT="${MONITOR_PORT:-8090}"
         nohup python3 -m http.server "$PORT" --directory "$MDIR" >> "$WORK_DIR/run/g261101-monitor-serve.log" 2>&1 & echo $! > "$SPID"; sleep 1
         alive "$SPID" && echo "✔ 网页：http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT/ （三台共用 NAS，任意一台的网页都能看到三台）" || { rm -f "$SPID"; echo "✗ 没起来（端口 $PORT 被占用？换一个：MONITOR_PORT=18090 $0 $SRV serve）"; };;
  *) sed -n "2,12p" "$0"; exit 1;;
esac
