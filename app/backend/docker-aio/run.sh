#!/usr/bin/env bash
# 在目标服务器上起一体化容器（前端 + API + 数据库，只对外 APP_PORT（默认 8002）一个端口）。目录映射：
#   ./data                         → /app/data                        只读  数据目录
#   ./data/processed/sim_deliveries → 同路径                           可写  抽取出的仿真成果（docker-import.sh 写，页面读）
#   ./cache/sim_ponding            → /app/data/processed/sim_ponding  可写  溢水点缓存
#   ./pgdata                       → /var/lib/postgresql/data         可写  数据库文件（首次自动从镜像种子填充，之后所有写入都在这里）
#   ./db/syq-pipe.dump             → /seed/syq-pipe.dump              只读  可选。库快照；仅在 ./pgdata 为空（首次启动）时用来恢复，没有就建空库
#   $RESULT（默认 NAS 的 result）   → /nas                             只读  服务器成果，供 docker-import.sh 登记（目录不存在就不挂）
# 用法：  ./run.sh [标签]      需要同目录 .env（PIPE_DB_PASSWORD=...，可不要，镜像里有默认口令）
# 重启不丢库：库文件在 ./pgdata（宿主机目录），--restart unless-stopped 随 docker 自启，--stop-timeout 120 给数据库做完检查点。
# 容器重建 / 升级镜像都不动 ./pgdata；千万别删它。备份见 backup-db.sh。
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TAG="${1:-latest}"; NAME="${NAME:-flood-aio}"
APP_PORT="${APP_PORT:-8002}"
DATA_DIR="${DATA_DIR:-$PWD/data}"; CACHE_DIR="${CACHE_DIR:-$PWD/cache/sim_ponding}"; PGDATA_DIR="${PGDATA_DIR:-$PWD/pgdata}"
RESULT="${RESULT:-/media/pop-fs/flood_data/result}"

ENVARG=(); [ -f .env ] && ENVARG=(--env-file .env)
[ -f "$DATA_DIR/processed/hotspots_845.csv" ] || { echo "数据目录不完整：$DATA_DIR/processed/hotspots_845.csv 不存在" >&2; exit 1; }
# 挂载点必须事先在宿主机上存在：data 是只读挂载，docker 没法在里面替我们建 sim_ponding / sim_deliveries 的挂载点（会报 read-only file system）
mkdir -p "$CACHE_DIR" "$PGDATA_DIR" "$DATA_DIR/processed/sim_deliveries" "$DATA_DIR/processed/sim_ponding"
# 容器里 API 以 uid 10001 运行：数据全局可读，缓存与成果目录归它
find "$DATA_DIR" \( -type d ! -perm -o=rx -o -type f ! -perm -o=r \) -exec chmod a+rX {} + 2>/dev/null || true
chown 10001 "$CACHE_DIR" "$DATA_DIR/processed/sim_deliveries"

NASARG=(); [ -d "$RESULT" ] && NASARG=(-v "$RESULT:/nas:ro")
SEEDARG=(); [ -f db/syq-pipe.dump ] && { chmod a+r db/syq-pipe.dump; SEEDARG=(-v "$PWD/db/syq-pipe.dump:/seed/syq-pipe.dump:ro"); }
if [ ! -s "$PGDATA_DIR/PG_VERSION" ]; then
  if [ ${#SEEDARG[@]} -gt 0 ]; then echo "首次启动（$PGDATA_DIR 为空）：从 db/syq-pipe.dump 恢复库";
  else echo "首次启动（$PGDATA_DIR 为空）：建空库（simulation 表结构 + 目录数据）"; fi
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped --stop-timeout 120 \
  -p "${APP_PORT}:8080" \
  "${ENVARG[@]}" \
  -v "$DATA_DIR:/app/data:ro" \
  -v "$DATA_DIR/processed/sim_deliveries:/app/data/processed/sim_deliveries" \
  -v "$CACHE_DIR:/app/data/processed/sim_ponding" \
  -v "$PGDATA_DIR:/var/lib/postgresql/data" \
  "${NASARG[@]}" "${SEEDARG[@]}" \
  "flood-aio:${TAG}"
echo "已启动 $NAME，页面 http://<服务器>:${APP_PORT}/ ，接口文档 /docs ；首次启动要初始化数据库（空库几十秒，恢复快照按库大小几分钟），看 docker logs -f $NAME。"
