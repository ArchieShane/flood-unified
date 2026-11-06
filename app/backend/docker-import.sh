#!/usr/bin/env bash
# 在服务器上用一次性容器：自检 NAS 成果 / 抽取并登记一份交付。镜像先按 docker-build.sh 构建或 docker load 好。
#
#   ./docker-import.sh probe  <相对 result 的路径> [--depth N]      只读自检，打印纯文本摘要（可抄出来核对格式）
#   ./docker-import.sh import <相对 result 的路径> --by <操作人缩写> [--c2d <work/c2d>] 抽取 + 登记为仿真任务（可重复执行，按输出目录去重）
#                                                             按交付形状自动选抽取口径（旧 .sww / 全市 / 一二维 npz），见 sim_import.py
#
# 例（NAS 成果都在 /media/pop-fs/flood_data/result/<主机名>/ 下）：
#   ./docker-import.sh probe  .                                         # 整个 result，先看有哪些主机
#   ./docker-import.sh probe  srv1 --depth 6
#   ./docker-import.sh import srv1/runtime/out/交付/C1_模型版_61mm2h_5m --by ZN
#
# 环境变量：RESULT（默认 /media/pop-fs/flood_data/result）、IMAGE / TAG（默认 flood-backend:latest）。
#
# 若一体化容器 flood-aio 在运行，直接在它里面做（库就在容器内）；否则起一次性容器，外部库走 .env。
# exec 默认以 root 进容器：NAS 的目录权限常是 root:<组> 770（如 azurefiles 组），容器里的普通用户 app(uid 10001) 读不了，
# 会被误报成「路径不存在或读不到」。要用非 root：IMPORT_USER=10001:<NAS 目录所属组的 gid> ./docker-import.sh …
# 挂载（一次性容器）：result 整个只读 → /nas；./data 只读 → /app/data，其中 processed/sim_deliveries 另挂可写（抽取结果写这里，
# API 容器通过 ./data 的只读挂载看到同一目录，登记完不用重启）。凭据走 --env-file .env。
# 注意 import 的目录名必须是「片区_雨深mm历时h[_网格m]」的形状（如 C1_模型版_61mm2h_5m），目录名会被解析成雨量与网格。

set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CMD="${1:-}"
REL="${2:-}"
[ -n "$CMD" ] && [ -n "$REL" ] || { sed -n '2,13p' "$0" >&2; exit 2; }
shift 2

RESULT="${RESULT:-/media/pop-fs/flood_data/result}"
IMAGE="${IMAGE:-flood-backend}"
TAG="${TAG:-latest}"
TARGET="/nas/${REL#/}"
TARGET="${TARGET%/.}"

[ -d "$RESULT" ] || { echo "找不到 $RESULT（用 RESULT=... 指到 NAS 的 result 目录）。" >&2; exit 1; }

run=(docker run --rm -e PYTHONPATH=/app/src -e PYTHONUTF8=1 -v "$RESULT:/nas:ro")

# 一体化容器（docker-aio/run.sh 起的 flood-aio，库在容器内、/nas 与可写的 sim_deliveries 已挂好）在跑，就直接 exec 进去做，
# 不另起容器；否则按「后端镜像 + 外部库」起一次性容器（下面 case 里）。
AIO="${AIO_NAME:-flood-aio}"
if docker ps --format '{{.Names}}' | grep -qx "$AIO"; then
  docker exec "$AIO" test -d /nas || { echo "容器 $AIO 里没有 /nas（run.sh 启动时 \$RESULT 目录要存在）。" >&2; exit 1; }
  case "$CMD" in
    probe)  docker exec -u "${IMPORT_USER:-root}" -e PYTHONPATH=/app/src -e PYTHONUTF8=1 "$AIO" python -m stormwater.sim_probe "$TARGET" "$@" ;;
    import) docker exec -u "${IMPORT_USER:-root}" -e PYTHONPATH=/app/src -e PYTHONUTF8=1 "$AIO" python -m stormwater.sim_import "$TARGET" "$@" ;;
    *) echo "未知子命令 $CMD（probe | import）" >&2; exit 2 ;;
  esac
  exit 0
fi

case "$CMD" in
  probe)
    "${run[@]}" "${IMAGE}:${TAG}" python -m stormwater.sim_probe "$TARGET" "$@"
    ;;
  import)
    [ -f .env ] || { echo "缺 .env（数据库凭据）。" >&2; exit 1; }
    [ -f data/processed/hotspots_845.csv ] || { echo "data/ 不完整（缺 processed/hotspots_845.csv，积水点坐标要用它）。" >&2; exit 1; }
    mkdir -p data/processed/sim_deliveries
    chmod -R a+rwX data/processed/sim_deliveries      # 容器内是 uid 10001，要能写
    "${run[@]}" --env-file .env \
      -v "$PWD/data:/app/data:ro" \
      -v "$PWD/data/processed/sim_deliveries:/app/data/processed/sim_deliveries" \
      "${IMAGE}:${TAG}" python -m stormwater.sim_import "$TARGET" "$@"
    ;;
  *)
    echo "未知子命令 $CMD（probe | import）" >&2
    exit 2
    ;;
esac
