#!/bin/bash
# 一体化容器入口：PostgreSQL（后台）→ 等就绪 → uvicorn（API）。任一进程退出则整体退出，由 docker 重启策略拉起。
set -euo pipefail

: "${PIPE_DB_PASSWORD:?镜像未内置数据库口令}"   # 镜像 ENV 已内置；-e 可覆盖

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
mkdir -p "$PGDATA"

# 0) 数据库文件必须在宿主机映射的目录里：跑在容器可写层里，容器一删数据就没了。要无视这条（仅调试）设 ALLOW_EPHEMERAL=1
# 基础镜像声明了 VOLUME，不映射时 docker 会悄悄建一个匿名卷（mountpoint 照样成立，容器删了库就成孤儿），所以还要排除匿名卷
#（匿名卷在宿主机上的路径是 /var/lib/docker/volumes/<64 位十六进制>/_data；具名卷、bind 目录不是这个形状）
if [ "${ALLOW_EPHEMERAL:-0}" != "1" ]; then
  if ! mountpoint -q "$PGDATA" || grep -E " $PGDATA " /proc/self/mountinfo | grep -qE '/volumes/[0-9a-f]{64}/_data'; then
    echo "[entrypoint] $PGDATA 没有映射到宿主机目录或具名卷（-v ./pgdata:$PGDATA），容器重建会丢库，拒绝启动。" >&2; exit 1
  fi
fi

# 1) PGDATA 为空 → 新建一个空集群（库内容在下面第 4 步建）；已有数据就原样使用，绝不覆盖
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  if [ -n "$(ls -A "$PGDATA" 2>/dev/null | grep -v '^lost+found$' || true)" ]; then
    echo "[entrypoint] $PGDATA 非空但不是 PG 数据目录，拒绝覆盖。" >&2; exit 1
  fi
  echo "[entrypoint] 首次启动：initdb ..."
  chown postgres:postgres "$PGDATA"; chmod 700 "$PGDATA"
  gosu postgres initdb -D "$PGDATA" -U postgres --encoding=UTF8 --locale=en_US.utf8     --auth-local=trust --auth-host=scram-sha-256 >/dev/null
fi
chown -R postgres:postgres "$PGDATA"
chmod 700 "$PGDATA"

# 2) 连接策略：本机 trust（仅用于下面设口令），网络一律口令；每次启动重写
cat > "$PGDATA/pg_hba.conf" <<HBA
local   all all                trust
host    all all 127.0.0.1/32   scram-sha-256
host    all all ::1/128        scram-sha-256
host    all all 0.0.0.0/0      scram-sha-256
HBA

# 数据库默认只监听容器内回环（对外只有 nginx 的 8080）；要从外面连库就设 PG_LISTEN='*' 并自己映射 5432
PG_OPTS=(-c listen_addresses="${PG_LISTEN:-127.0.0.1}" -c port=5432
         -c fsync=on -c synchronous_commit=on -c full_page_writes=on
         -c shared_buffers="${PG_SHARED_BUFFERS:-512MB}" -c max_connections="${PG_MAX_CONNECTIONS:-100}")

gosu postgres postgres -D "$PGDATA" "${PG_OPTS[@]}" &
PG_PID=$!

for _ in $(seq 1 120); do
  gosu postgres pg_isready -q -h /var/run/postgresql -p 5432 && break
  kill -0 "$PG_PID" 2>/dev/null || { echo "[entrypoint] postgres 意外退出" >&2; exit 1; }
  sleep 1
done

# 3) 每次启动把口令同步为环境变量（scram）；用 psql 变量避免口令里的引号出事
echo "ALTER ROLE postgres PASSWORD :'pw'" | gosu postgres psql -h /var/run/postgresql -U postgres -d postgres \
  -v ON_ERROR_STOP=1 -v pw="$PIPE_DB_PASSWORD" -f - >/dev/null

# 4) 建库（只在库不存在时做一次；之后每次启动只幂等地补齐 simulation 表结构，镜像升级带来的新列自动补上）
#    - /seed/syq-pipe.dump 存在（-v 进来的 pg_dump -Fc 快照）→ 恢复它（跳过 vector 扩展：没有任何列用它）
#    - 否则建空库：simulation 表结构 + 目录类种子（操作人 / 雨型预设 / 防线与资源）。INIT_DEMO_RUNS=1 才灌设计稿的演示任务
DB="${PIPE_DB_NAME:-syq-pipe}"
PSQL=(gosu postgres psql -h /var/run/postgresql -U postgres -v ON_ERROR_STOP=1 -q)
if [ "$("${PSQL[@]}" -d postgres -Atc "select count(*) from pg_database where datname = '$DB'")" = "0" ]; then
  echo "[entrypoint] 库 $DB 不存在，创建 ..."
  "${PSQL[@]}" -d postgres -c "create database \"$DB\""
  if [ -f "${SEED_DUMP:-/seed/syq-pipe.dump}" ]; then
    echo "[entrypoint] 从 ${SEED_DUMP:-/seed/syq-pipe.dump} 恢复（大库要几分钟）..."
    LIST=$(mktemp); pg_restore -l "${SEED_DUMP:-/seed/syq-pipe.dump}" | grep -vE ' EXTENSION - vector| COMMENT - EXTENSION vector' > "$LIST"
    chmod a+r "$LIST"
    gosu postgres pg_restore -h /var/run/postgresql -U postgres -d "$DB" --no-owner -j 4 -L "$LIST" "${SEED_DUMP:-/seed/syq-pipe.dump}"       || echo "[entrypoint] pg_restore 有告警（常见于已存在的扩展），继续" >&2
  else
    echo "[entrypoint] 没有快照，建空库（simulation 表结构 + 目录数据）"
    "${PSQL[@]}" -d "$DB" -c 'create extension if not exists postgis' 2>/dev/null || true
  fi
  NEW_DB=1
else
  NEW_DB=0
fi
"${PSQL[@]}" -d "$DB" -f /app/sql/simulation_schema.sql >/dev/null
if [ "$NEW_DB" = "1" ] && [ ! -f "${SEED_DUMP:-/seed/syq-pipe.dump}" ]; then
  if [ "${INIT_DEMO_RUNS:-0}" = "1" ]; then
    "${PSQL[@]}" -d "$DB" -f /app/sql/simulation_seed.sql >/dev/null
  else
    sed '/^-- 任务：/,$d' /app/sql/simulation_seed.sql | "${PSQL[@]}" -d "$DB" -f - >/dev/null
  fi
fi
echo "[entrypoint] 库 $DB 就绪（simulation.run 现有 $("${PSQL[@]}" -d "$DB" -Atc 'select count(*) from simulation.run') 条），启动 API ..."

# 4) API 以非 root 运行；数据目录只读挂载，读不到 DEM 时 registry 会静默退回 Zone B —— 先提示
[ -f /app/data/processed/hotspots_845.csv ] || echo "[entrypoint] 警告：/app/data 里没有 processed/hotspots_845.csv，数据目录是否已映射？" >&2
[ -d /app/data/DEM_UAE_UTM40 ] || echo "[entrypoint] 警告：缺 /app/data/DEM_UAE_UTM40，高程将静默退回 Zone B。" >&2

gosu app uvicorn stormwater.api.main:app --app-dir /app/src --host 127.0.0.1 --port 8101 &
API_PID=$!
nginx -g 'daemon off;' &
WEB_PID=$!

# 先停入口（nginx）和 API，最后 fast shutdown 数据库（做完检查点才退出；docker stop 要给足时间：--stop-timeout 120）
term() { kill -QUIT "$WEB_PID" 2>/dev/null || true; kill -TERM "$API_PID" 2>/dev/null || true
         wait "$API_PID" 2>/dev/null || true; wait "$WEB_PID" 2>/dev/null || true
         kill -INT "$PG_PID" 2>/dev/null || true; wait "$PG_PID" 2>/dev/null || true; exit 0; }
trap term TERM INT

wait -n "$PG_PID" "$API_PID" "$WEB_PID" || true
echo "[entrypoint] 有进程退出，容器结束" >&2
kill -QUIT "$WEB_PID" 2>/dev/null || true
kill -TERM "$API_PID" 2>/dev/null || true
kill -INT "$PG_PID" 2>/dev/null || true
wait || true
exit 1
