#!/usr/bin/env bash
# 停止一体化容器 flood-aio：先停 nginx / API，数据库做完检查点再退出（最多等 120 秒），不丢已提交的数据。
#
#   ./stop.sh          只停止，容器保留。再起：./start.sh（或 docker start flood-aio）
#   ./stop.sh --rm     停止并删除容器。pgdata / data / cache 都不动；再起：./run.sh <标签>
#
# 手动停止后，docker 重启或服务器重启时不会自动把它拉起来（restart 策略 unless-stopped 只对「没被手动停过」的容器生效）；
# 要恢复服务请 ./start.sh 或 ./run.sh <标签>。
set -euo pipefail
NAME="${NAME:-flood-aio}"
RM=0; [ "${1:-}" = "--rm" ] && RM=1

docker inspect --type container "$NAME" >/dev/null 2>&1 || { echo "容器 $NAME 不存在，无需停止。"; exit 0; }

if [ "$(docker inspect --type container -f '{{.State.Running}}' "$NAME")" = "true" ]; then
  echo "正在停止 $NAME（数据库做检查点，最多 120 秒）..."
  s=$(date +%s)
  docker stop -t 120 "$NAME" >/dev/null
  echo "已停止，用时 $(( $(date +%s) - s )) 秒，退出码 $(docker inspect --type container -f '{{.State.ExitCode}}' "$NAME")。"
  tail_log=$(docker logs --tail 50 "$NAME" 2>&1 || true)      # 先取出来再 grep：grep -q 提前退出会让管道上游吃 SIGPIPE
  if grep -q "database system is shut down" <<<"$tail_log"; then
    echo "数据库已干净关闭。"
  else
    echo "⚠ 日志里没看到「database system is shut down」，下次启动会做崩溃恢复（已提交的数据仍安全）。" >&2
  fi
else
  echo "$NAME 已经是停止状态。"
fi

if [ "$RM" = "1" ]; then
  docker rm "$NAME" >/dev/null && echo "已删除容器 $NAME（pgdata / data / cache 保留）。"
fi
