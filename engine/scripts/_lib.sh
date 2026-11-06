#!/usr/bin/env bash
# 公共函数：engine 目录、镜像、路径默认值。被 new_region.sh / check.sh / start.sh / report.sh 引用。
set -euo pipefail
ENGINE_DIR="${ENGINE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
[ -f "$ENGINE_DIR/docker/.env" ] && set -a && . "$ENGINE_DIR/docker/.env" && set +a
TAG="${TAG:-v3}"
IMAGE="${IMAGE:-flood-worker:$TAG}"
NAS_DIR="${NAS_DIR:-/media/pop-fs/flood_data}"
WORK_DIR="${WORK_DIR:-/data/swbrain_work}"
THREADS="${THREADS:-5}"

# 服务器上的高精度 DTM（可选）：.env 里设 DTM_PATH=<单个 tif，或放分块 tif 的目录>，就只读挂进容器，推演改用它（不设则用包里 5 m 的 dtm_5m.tif）
DTM_ARGS=()
if [ -n "${DTM_PATH:-}" ]; then
  if [ -d "$DTM_PATH" ]; then DTM_ARGS=(-v "$DTM_PATH:/dtm:ro" -e FLOOD_DTM=/dtm)
  else DTM_ARGS=(-v "$(dirname "$DTM_PATH"):/dtm:ro" -e FLOOD_DTM="/dtm/$(basename "$DTM_PATH")"); fi
fi

die() { echo "✗ $*" >&2; exit 1; }
need_docker() { command -v docker >/dev/null 2>&1 || die "没有 docker"; }

# 在容器里跑 engine 里的脚本（镜像只是运行环境，脚本从宿主机 engine/ 映射进去）：dock <脚本路径相对 /app> [参数…]
dock() {
  need_docker
  docker run --rm -e SWBRAIN_ENV=container -e SWBRAIN_SWMM_WORK=/work -e MAKANI_PASSWORD="${MAKANI_PASSWORD:-}" \
    -v "$ENGINE_DIR:/app:ro" -v "$NAS_DIR:/nas" -v "$WORK_DIR:/work" "${DTM_ARGS[@]}" "$IMAGE" python3 -u "/app/$1" "${@:2}"
}

# 改 region.toml：先用宿主机 python3（>=3.11），没有就用镜像里的（engine 以可写方式映射）
region_toml() {
  if python3 -c 'import tomllib' 2>/dev/null; then python3 "$ENGINE_DIR/tools/region_toml.py" "$@"
  else need_docker; docker run --rm -v "$ENGINE_DIR:/app" "$IMAGE" python3 /app/tools/region_toml.py "${@/#$ENGINE_DIR/\/app}"; fi
}
