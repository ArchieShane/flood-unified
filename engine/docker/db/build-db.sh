#!/bin/bash
# 构建时执行：初始化集群 → 建三个库 → 恢复转储 → 分析 → 关闭。本地 socket 信任、网络连接要口令（口令运行时再设）。
set -euo pipefail
mkdir -p "$PGDATA" && chown postgres:postgres "$PGDATA" && chmod 700 "$PGDATA"
run() { gosu postgres "$@"; }
run initdb -D "$PGDATA" -E UTF8 -A trust >/dev/null
cat >> "$PGDATA/pg_hba.conf" <<'HBA'
host all all 0.0.0.0/0 scram-sha-256
HBA
cat >> "$PGDATA/postgresql.conf" <<'CONF'
listen_addresses = '*'
shared_buffers = 1GB
effective_cache_size = 3GB
work_mem = 64MB
max_parallel_workers_per_gather = 2
fsync = off                      # 数据是只读快照、丢了重建，不需要为崩溃恢复付代价
CONF
run pg_ctl -D "$PGDATA" -o "-c listen_addresses=''" -w start >/dev/null
# 转储文件名 → 库名（与 config.toml 的 dbname 一致）
for pair in pipe:pipe makani:makani_sync_full liveability:liveability_data_20260730; do
  dump=${pair%%:*}; db=${pair##*:}
  echo "== $db"
  run createdb "$db"
  run psql -v ON_ERROR_STOP=1 -q -d "$db" -c "create extension if not exists postgis"
  if [ "$db" = pipe ]; then
    run psql -v ON_ERROR_STOP=1 -q -d pipe -c "create schema dmt_irrigationstormwater_abudhabi" -c "create schema dmt_irrigationstormwater_abudhabi_network"
  fi
  run pg_restore --no-owner --no-privileges -j 4 -d "$db" "/dumps/$dump.dump"
  run psql -q -d "$db" -c "analyze"
done
run pg_ctl -D "$PGDATA" -m fast -w stop
du -sh "$PGDATA"
