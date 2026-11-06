#!/bin/bash
# 运行时：用 POSTGRES_PASSWORD 设 postgres 口令（没设就拒绝启动），再前台起服务。
set -euo pipefail
rm -f "$PGDATA/postmaster.pid"                      # 容器被强制停掉后重启时的残留
: "${POSTGRES_PASSWORD:?必须设置 POSTGRES_PASSWORD（部署包 .env）}"
gosu postgres pg_ctl -D "$PGDATA" -o "-c listen_addresses=''" -w start >/dev/null
echo "alter role postgres password :'pw'" | gosu postgres psql -q -d postgres -v pw="$POSTGRES_PASSWORD"      # -c 不做变量替换，走标准输入
gosu postgres pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null
exec gosu postgres postgres -D "$PGDATA"
