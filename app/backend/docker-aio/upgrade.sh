#!/usr/bin/env bash
# 一键部署 / 升级：校验镜像包 → （已有容器则）先备份数据库 → docker load → 补挂载点 → ./run.sh <标签> → 等健康。
#
#   ./upgrade.sh v3         需要同目录有 flood-aio_v3.tar.gz 和 flood-aio_v3.tar.gz.sha256
#
# pgdata / data / cache / backups / .env 都沿用，库里已登记的任务不丢。回滚：./run.sh <旧标签>（旧镜像还在本机，没跑过 clean-images.sh 的话）。
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAG="${1:?用法：./upgrade.sh <标签>，例如 ./upgrade.sh v3}"
NAME="${NAME:-flood-aio}"
TAR="flood-aio_${TAG}.tar.gz"

[ -f "$TAR" ] && [ -f "$TAR.sha256" ] || { echo "缺 $TAR 或 $TAR.sha256" >&2; exit 1; }
echo "[1/5] 校验镜像包 ..."; sha256sum -c "$TAR.sha256"

OLD=""
if docker inspect --type container "$NAME" >/dev/null 2>&1; then
  OLD=$(docker inspect --type container -f '{{.Config.Image}}' "$NAME")
  if [ "$(docker inspect --type container -f '{{.State.Running}}' "$NAME")" = "true" ]; then
    echo "[2/5] 升级前备份数据库 ..."; ./backup-db.sh
  else
    echo "[2/5] 容器 $NAME 没在运行，跳过在线备份（库文件在 pgdata/，未动）"
  fi
else
  echo "[2/5] 没有现存容器，跳过备份（首次部署）"
fi

echo "[3/5] 加载镜像 ..."; gunzip -c "$TAR" | docker load
echo "[4/5] 检查挂载点 ..."
[ -f data/processed/hotspots_845.csv ] || { echo "data/ 不完整（缺 data/processed/hotspots_845.csv）" >&2; exit 1; }
mkdir -p data/processed/sim_ponding data/processed/sim_deliveries cache/sim_ponding pgdata
echo "[5/5] 启动新容器 ..."; ./run.sh "$TAG"

echo -n "等待健康检查 "
for _ in $(seq 1 60); do
  s=$(docker inspect --type container -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null || echo starting)
  if [ "$s" = healthy ]; then
    echo; echo "✓ $NAME 已就绪（flood-aio:$TAG）。"
    [ -n "$OLD" ] && echo "  升级前的镜像：$OLD；回滚：./run.sh ${OLD##*:}"
    docker logs --tail 3 "$NAME" 2>&1 | grep entrypoint || true
    exit 0
  fi
  echo -n "."; sleep 3
done
echo; echo "⚠ 180 秒内没变 healthy：docker logs --tail 50 $NAME" >&2; exit 1
