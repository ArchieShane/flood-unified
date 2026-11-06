#!/usr/bin/env bash
# 在服务器上（重新）起后端容器。先 ./docker-build.sh 出镜像，再跑本脚本。
#
#   ./docker-run.sh                 # 用 flood-backend:latest
#   ./docker-run.sh v0.3.0          # 换标签
#
# 镜像里只有代码与依赖，数据全部挂载：
#   ./data/             → /app/data（只读）  台账与批算 CSV、DEM、片区演算、仿真成果（data/temp）。
#                                             换数据只需同步本目录再重跑本脚本，不必重建镜像。
#   flood-sim-ponding   → /app/data/processed/sim_ponding（具名卷，可写）
#                                             溢水点缓存，首次查询现算后落盘，重建容器不丢。
#
# 凭据走 --env-file .env，不进镜像。

set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IMAGE="${IMAGE:-flood-backend}"
TAG="${1:-latest}"
NAME="${NAME:-flood-backend}"
PORT="${PORT:-8101}"

[ -f .env ] || { echo "缺 .env（数据库凭据），照 .env.example 填好再来。" >&2; exit 1; }
[ -f data/processed/hotspots_845.csv ] || { echo "data/ 不完整（缺 processed/hotspots_845.csv）。" >&2; exit 1; }

# 容器以 uid 10001 运行，数据必须全局可读。本机同步过来的有几个目录是 700（DEM_UAE_UTM40 等），
# 读不到 DEM 时 registry.py 会静默全体退回 Zone B —— 每次启动前补一遍读权限（只动目录和不可读的文件）。
find data \( -type d ! -perm -o=rx -o -type f ! -perm -o=r \) -exec chmod a+rX {} +
mkdir -p data/processed/sim_ponding   # 具名卷的挂载点，只读 bind 里必须事先存在

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" \
  --restart unless-stopped \
  -p "${PORT}:8101" \
  --env-file .env \
  -v "$PWD/data:/app/data:ro" \
  -v flood-sim-ponding:/app/data/processed/sim_ponding \
  "${IMAGE}:${TAG}"

echo "已启动 ${NAME}（${IMAGE}:${TAG}），端口 ${PORT}。探活: curl http://127.0.0.1:${PORT}/"
