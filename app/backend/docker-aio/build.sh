#!/usr/bin/env bash
# 构建一体化镜像 flood-aio（前端 + API + 数据库）。拼构建上下文到 docker-aio/.ctx/ 再 docker build。
#
#   ./build.sh [标签]
#   SKIP_WEB=1 ./build.sh                      # 前端已经 npm run build 过（frontend/dist 在）就不再构建
#   PG_BASE=local/postgis:16.11-postgis3.6-pgrouting ./build.sh   # 基础库镜像，默认同 Dockerfile
#
# 镜像里没有数据库内容（只有 PostGIS 软件）：首次启动 ./pgdata 为空时，有 /seed/syq-pipe.dump 就恢复它，
# 没有就建空库（simulation 表结构 + 目录数据）。数据都在 ./pgdata，升级镜像不动它。快照用 dump-db.sh 从某个库导。
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TAG="${1:-latest}"
CTX=".ctx"

WEB="../../frontend"
if [ "${SKIP_WEB:-0}" != "1" ] || [ ! -d "$WEB/dist" ]; then
  (cd "$WEB" && npm ci && npm run build)
fi
[ -f "$WEB/dist/index.html" ] || { echo "前端没有构建产物 $WEB/dist" >&2; exit 1; }

rm -rf "$CTX"; mkdir -p "$CTX"
cp -r ../src ../sql "$CTX/"
cp ../requirements.txt Dockerfile nginx.conf entrypoint.sh "$CTX/"
cp -r "$WEB/dist" "$CTX/web"
cp -r ../../viewer/web "$CTX/viewer-web"        # 推演成果展示页（/viewer/）
find "$CTX" -name __pycache__ -prune -exec rm -rf {} +
# 在 Windows 上签出时脚本可能是 CRLF，进镜像前统一转 LF（CRLF 会让 #!/bin/bash 失效）
sed -i 's/\r$//' "$CTX/entrypoint.sh" "$CTX/nginx.conf" "$CTX/Dockerfile"

docker build ${PG_BASE:+--build-arg PG_BASE="$PG_BASE"} -t "flood-aio:${TAG}" "$CTX"
rm -rf "$CTX"
echo "构建完成：flood-aio:${TAG}"
echo "导出给服务器：docker save flood-aio:${TAG} | gzip > flood-aio_${TAG}.tar.gz"
