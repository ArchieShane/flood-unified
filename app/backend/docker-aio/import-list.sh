#!/usr/bin/env bash
# 按清单批量登记（清单由 ./scan-nas.sh --write 生成，每行「主机 交付目录名」）。
# 逐个登记；每个登记完修一次权限；某个失败记下来继续下一个；最后汇总。同一时刻只允许一个实例（flock）。
# 覆盖更新了「已登记」的交付时，结束后自动重启容器一次（清掉服务进程里的内存缓存）；NO_RESTART=1 可关闭。
#
#   ./import-list.sh /tmp/todo.txt [--by AR]
#   长时间任务放后台：nohup ./import-list.sh /tmp/todo.txt > /tmp/import_list.log 2>&1 &   然后 tail -f /tmp/import_list.log
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIST="${1:?用法：./import-list.sh <清单文件> [--by 缩写]}"; shift || true
BY="AR"; [ "${1:-}" = "--by" ] && BY="${2:?--by 需要缩写}"
NAME="${NAME:-flood-aio}"; DATA_DIR="${DATA_DIR:-$PWD/data}"
# 同一时刻只许一个实例：优先 flock；没有 flock 的环境退回 mkdir 锁
if command -v flock >/dev/null 2>&1; then
  exec 9>/tmp/import_list.lock
  flock -n 9 || { echo "已有一个登记在运行（/tmp/import_list.lock），退出。" >&2; exit 1; }
else
  mkdir /tmp/import_list.lockdir 2>/dev/null || { echo "已有一个登记在运行（/tmp/import_list.lockdir），退出。若确认没有，删掉该目录再试。" >&2; exit 1; }
  trap 'rmdir /tmp/import_list.lockdir 2>/dev/null' EXIT
fi
[ -s "$LIST" ] || { echo "清单为空：$LIST"; exit 0; }

REG=$(docker exec "$NAME" psql -U postgres -d syq-pipe -Atc \
  "select regexp_replace(output_dir, '^processed/sim_deliveries/', '') from simulation.run where output_dir like 'processed/sim_deliveries/%'")
OK=0; FAIL=(); UPDATED=0; TOTAL=$(grep -c . "$LIST")
while read -r host name; do
  [ -n "${name:-}" ] || continue
  echo "=== [$((OK + ${#FAIL[@]} + 1))/$TOTAL] $name ($host)  $(date)"
  grep -qxF "$name" <<<"$REG" && UPDATED=1
  if ./docker-import.sh import "$host/runtime/out/交付/$name" --by "$BY"; then
    chmod -R a+rX "$DATA_DIR/processed/sim_deliveries"; OK=$((OK + 1)); echo "--- done $name  $(date)"
  else
    FAIL+=("$name"); echo "--- FAILED $name  $(date)"
  fi
done < "$LIST"

echo "ALLDONE $(date)  成功 $OK 个；失败 ${#FAIL[@]} 个：${FAIL[*]:-无}"
if [ "$UPDATED" = 1 ] && [ "${NO_RESTART:-0}" != 1 ]; then
  echo "覆盖更新了已登记的交付，重启容器清缓存 ..."; docker restart "$NAME" >/dev/null && echo "已重启。"
fi
[ ${#FAIL[@]} -eq 0 ]
