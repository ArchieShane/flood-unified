#!/usr/bin/env bash
# 从「现有数据库」导出快照 db/syq-pipe.dump（pg_dump -Fc），供 build.sh 做镜像里的数据库种子。
#
#   PGHOST=10.255.254.109 PGPORT=35433 PGPASSWORD=*** ./dump-db.sh     # 内网原库
#   PGHOST=127.0.0.1 PGPORT=35433 PGPASSWORD=*** ./dump-db.sh          # 旧一体化容器（映射了 5432→35433）
#
# 本机没有 pg_dump 时用基础库镜像里的（--network host，要在 Linux 服务器上跑）。
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${PGHOST:?设 PGHOST}"; : "${PGPASSWORD:?设 PGPASSWORD}"
PGPORT="${PGPORT:-5432}"; PGUSER="${PGUSER:-postgres}"; PGDATABASE="${PGDATABASE:-syq-pipe}"
PG_BASE="${PG_BASE:-local/postgis:16.11-postgis3.6-pgrouting}"
mkdir -p db
OUT="db/syq-pipe.dump"; TMP="$OUT.tmp"

if command -v pg_dump >/dev/null; then
  pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -Fc -f "$TMP" "$PGDATABASE"
else
  docker run --rm --network host -e PGPASSWORD -v "$PWD/db:/out" "$PG_BASE" \
    pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -Fc -f "/out/$(basename "$TMP")" "$PGDATABASE"
fi
mv "$TMP" "$OUT"
ls -lh "$OUT"
