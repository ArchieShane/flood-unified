#!/bin/bash
# 在服务器上用 docker 跑成果拷贝 / 改英文名（宿主机不需要 python，也不需要联网）。
# 先载入镜像（一次）：  docker load -i export-en_1.0.tar.gz
# NAS 以只读（:ro）挂进容器——即使脚本有 bug 也写不了 NAS；目标目录单独读写挂载。
#
#   bash export_results_en.sh list
#   bash export_results_en.sh copy   /你的目标目录 [--dry-run] [--include 正则] [--no-sww] [--no-shpdat]
#   bash export_results_en.sh rename /你的目标目录 [--dry-run]
# 环境变量：NAS_BASE（缺省 /media/pop-fs/flood_data）、IMAGE（缺省 export-en:1.0）
set -euo pipefail
NAS_BASE=${NAS_BASE:-/media/pop-fs/flood_data}
IMAGE=${IMAGE:-export-en:1.0}
mode=${1:-}; [ -n "$mode" ] || { sed -n 2,10p "$0"; exit 1; }
shift
[ -d "$NAS_BASE" ] || { echo "✗ $NAS_BASE 不存在"; exit 1; }
docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "✗ 没有镜像 $IMAGE：先 docker load -i export-en_1.0.tar.gz"; exit 1; }
args=(--rm -u "$(id -u):$(id -g)")
case "$mode" in
  list)
    docker run "${args[@]}" -v "$NAS_BASE:$NAS_BASE:ro" "$IMAGE" list --nas-base "$NAS_BASE" "$@" ;;
  copy)
    dest=${1:?缺目标目录}; shift
    mkdir -p "$dest"; dest=$(cd "$dest" && pwd -P)
    nb=$(readlink -f "$NAS_BASE")
    case "$dest/" in "$nb/result/"*|"$nb/shp_dat/"*) echo "✗ 目标不能在 result / shp_dat 里面"; exit 1;; esac
    case "$nb/result/" in "$dest/"*) echo "✗ 目标不能包含 result"; exit 1;; esac
    docker run "${args[@]}" -v "$NAS_BASE:$NAS_BASE:ro" -v "$dest:$dest" "$IMAGE" copy "$dest" --nas-base "$NAS_BASE" "$@" ;;
  rename)
    dest=${1:?缺目标目录}; shift
    dest=$(cd "$dest" && pwd -P)
    # rename 不挂 NAS：容器里根本看不到原始数据
    docker run "${args[@]}" -v "$dest:$dest" "$IMAGE" rename "$dest" --nas-base "$NAS_BASE" "$@" ;;
  *) echo "mode 只能是 list | copy | rename"; exit 1;;
esac
