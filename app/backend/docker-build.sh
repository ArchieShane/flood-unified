#!/usr/bin/env bash
# 构建后端 API 镜像（linux/amd64）。
#
#   ./docker-build.sh                  # 构建 flood-backend:latest
#   ./docker-build.sh v0.2.0           # 换标签
#   IMAGE=reg.example.com/flood-backend ./docker-build.sh v0.2.0
#
# 本机是 Apple 芯片时这是一次交叉构建：pip 装轮子那一步跑在 QEMU 里，
# 头一回会慢（十几分钟量级），之后有层缓存就快了。

set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IMAGE="${IMAGE:-flood-backend}"
TAG="${1:-latest}"
PLATFORM="${PLATFORM:-linux/amd64}"

command -v docker >/dev/null || { echo "找不到 docker —— 本机未装容器运行时。" >&2; exit 1; }

echo "构建 ${IMAGE}:${TAG}  (${PLATFORM})"
docker buildx build \
  --platform "$PLATFORM" \
  --tag "${IMAGE}:${TAG}" \
  --load \
  .

echo
echo "构建完成：${IMAGE}:${TAG}"
docker image inspect "${IMAGE}:${TAG}" --format '  体积: {{.Size}} 字节 ({{div .Size 1048576}} MB)' 2>/dev/null || true
cat <<'TIP'

运行（数据整个挂载、凭据用 --env-file 注入，都不在镜像里）：

  ./docker-run.sh

  探活: curl http://127.0.0.1:8101/
  文档: http://127.0.0.1:8101/docs

注意后端要连的是内网库 10.255.254.109:35433 —— 容器得能路由到公司网络，
宿主机走 VPN/隧道时，别忘了 .env 里的 PIPE_DB_HOST 改成宿主机可达的地址
（容器内的 127.0.0.1 不是宿主机，SSH 隧道场景要写 host.docker.internal）。
TIP
