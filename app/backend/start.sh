#!/usr/bin/env bash
# 启动后端 API 服务（FastAPI / uvicorn）。前后端已分离：本脚本只起后端，
# 页面在同级的 ../frontend/ 那个 Vue 3 + Vite 工程里，另起 `npm run dev`（见 README §启动）。
# 启动前先检查端口是否已被占用：已在运行则先停止旧进程，再启动新的。
#
#   ./start.sh                # 后台启动，默认端口 8101
#   ./start.sh 8200           # 换端口
#   ./start.sh -f             # 前台启动（Ctrl-C 退出，带 --reload）
#   ./start.sh stop           # 只停止，不启动
#   ./start.sh status         # 看当前状态

set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BACKEND_DIR"

PORT="${PORT:-8101}"
HOST="${HOST:-127.0.0.1}"
APP="stormwater.api.main:app"
APP_DIR="src"
PYTHON="$BACKEND_DIR/.venv/bin/python"
LOG_DIR="$BACKEND_DIR/logs"
LOG_FILE="$LOG_DIR/app.log"
PID_FILE="$LOG_DIR/app.pid"

FOREGROUND=0
ACTION="start"

for arg in "$@"; do
  case "$arg" in
    -f|--foreground) FOREGROUND=1 ;;
    stop)            ACTION="stop" ;;
    status)          ACTION="status" ;;
    restart|start)   ACTION="start" ;;
    -h|--help)       sed -n '2,10p' "$0"; exit 0 ;;
    ''|*[!0-9]*)     echo "未知参数：$arg" >&2; exit 2 ;;
    *)               PORT="$arg" ;;
  esac
done

# 返回占用 $PORT 的 LISTEN 进程 PID（可能多个）
port_pids() {
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true
}

stop_service() {
  local pids
  pids="$(port_pids)"
  if [[ -z "$pids" ]]; then
    echo "端口 $PORT 未被占用，无需停止。"
    rm -f "$PID_FILE"
    return 0
  fi

  echo "端口 $PORT 已被占用，先停止旧服务：$(echo "$pids" | tr '\n' ' ')"
  # shellcheck disable=SC2086
  ps -o pid=,command= -p $pids 2>/dev/null | sed 's/^/    /'

  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  for _ in $(seq 1 20); do           # 最多等 10s 让它优雅退出
    [[ -z "$(port_pids)" ]] && break
    sleep 0.5
  done

  pids="$(port_pids)"
  if [[ -n "$pids" ]]; then
    echo "仍未退出，强制杀掉：$(echo "$pids" | tr '\n' ' ')"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 1
  fi

  if [[ -n "$(port_pids)" ]]; then
    echo "停止失败：端口 $PORT 仍被占用。" >&2
    exit 1
  fi
  echo "旧服务已停止。"
  rm -f "$PID_FILE"
}

show_status() {
  local pids
  pids="$(port_pids)"
  if [[ -z "$pids" ]]; then
    echo "未运行（端口 $PORT 空闲）。"
  else
    echo "运行中，端口 ${PORT}："
    # shellcheck disable=SC2086
    ps -o pid=,etime=,command= -p $pids 2>/dev/null | sed 's/^/    /'
  fi
}

case "$ACTION" in
  status) show_status; exit 0 ;;
  stop)   stop_service; exit 0 ;;
esac

[[ -x "$PYTHON" ]] || { echo "找不到虚拟环境解释器：$PYTHON" >&2; exit 1; }

stop_service
mkdir -p "$LOG_DIR"

if [[ "$FOREGROUND" == "1" ]]; then
  echo "前台启动：http://$HOST:$PORT/   （Ctrl-C 退出）"
  exec "$PYTHON" -m uvicorn "$APP" --app-dir "$APP_DIR" --host "$HOST" --port "$PORT" --reload
fi

echo "后台启动：http://$HOST:$PORT/"
nohup "$PYTHON" -m uvicorn "$APP" --app-dir "$APP_DIR" --host "$HOST" --port "$PORT" \
  >>"$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

# 等端口真正起来，起不来就把日志尾巴打出来
for _ in $(seq 1 30); do
  if [[ -n "$(port_pids)" ]]; then
    echo "启动成功 (pid $(cat "$PID_FILE"))"
    echo "  API 根   : http://$HOST:$PORT/"
    echo "  API 文档 : http://$HOST:$PORT/docs"
    echo "  日志     : $LOG_FILE"
    echo "  页面     : cd ../frontend && npm run dev   （默认 http://localhost:5173/）"
    exit 0
  fi
  kill -0 "$(cat "$PID_FILE")" 2>/dev/null || break
  sleep 0.5
done

echo "启动失败，日志末尾：" >&2
tail -n 30 "$LOG_FILE" >&2
rm -f "$PID_FILE"
exit 1
