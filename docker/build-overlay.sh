#!/usr/bin/env bash
# 构建 flood-new 镜像（叠加在已有 flood-aio 镜像上）：./docker/build-overlay.sh [标签] [基础镜像]
#   SKIP_WEB=1 ：frontend/dist 已构建则跳过 npm
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${1:-v2}"; BASE="${2:-flood-aio:v8-sim}"
WEB=app/frontend
if [ "${SKIP_WEB:-0}" != "1" ] || [ ! -d "$WEB/dist" ]; then (cd "$WEB" && npm ci && npm run build); fi
CTX=docker/.ctx; rm -rf "$CTX"; mkdir -p "$CTX"
cp -r app/backend/src app/backend/sql "$CTX/"
cp -r "$WEB/dist" "$CTX/web"; cp -r app/viewer/web "$CTX/viewer-web"
cp app/backend/docker-aio/nginx.conf docker/Dockerfile.overlay "$CTX/"
find "$CTX" -name __pycache__ -prune -exec rm -rf {} + ; find "$CTX" -name '._*' -delete
docker build --build-arg BASE_IMAGE="$BASE" -f "$CTX/Dockerfile.overlay" -t "flood-new:${TAG}" "$CTX"
echo "构建完成：flood-new:${TAG}"
