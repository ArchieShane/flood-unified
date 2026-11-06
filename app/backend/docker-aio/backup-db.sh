#!/usr/bin/env bash
# 给运行中的一体化容器做一份在线备份（pg_dump -Fc，不停服务），保留最近 KEEP 份。建议 cron 每天跑：
#   0 3 * * *  /path/to/docker-aio/backup-db.sh
# 恢复：docker exec -i flood-aio pg_restore -U postgres -d syq-pipe --clean --if-exists < backups/xxx.dump
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="${NAME:-flood-aio}"; KEEP="${KEEP:-7}"
mkdir -p backups
F="backups/syq-pipe_$(date +%Y%m%d_%H%M%S).dump"
docker exec "$NAME" pg_dump -U postgres -Fc syq-pipe > "$F.tmp"
mv "$F.tmp" "$F"
ls -1t backups/syq-pipe_*.dump | tail -n +$((KEEP + 1)) | xargs -r rm -f
echo "已备份 $F（保留最近 $KEEP 份）"
