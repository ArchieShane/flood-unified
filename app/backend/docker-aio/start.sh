#!/usr/bin/env bash
# 启动已存在但处于停止状态的 flood-aio 容器（./stop.sh 停掉的），并等到健康。
# 容器不存在（比如 ./stop.sh --rm 之后）请用 ./run.sh <标签> 重建。
set -euo pipefail
NAME="${NAME:-flood-aio}"
docker inspect --type container "$NAME" >/dev/null 2>&1 || { echo "容器 $NAME 不存在，请用 ./run.sh <标签> 创建。" >&2; exit 1; }
docker start "$NAME" >/dev/null
echo -n "等待健康检查 "
for _ in $(seq 1 60); do
  s=$(docker inspect --type container -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null || echo starting)
  [ "$s" = healthy ] && { echo; echo "$NAME 已就绪。"; exit 0; }
  echo -n "."; sleep 3
done
echo; echo "180 秒内没变 healthy，看日志：docker logs --tail 50 $NAME" >&2; exit 1
