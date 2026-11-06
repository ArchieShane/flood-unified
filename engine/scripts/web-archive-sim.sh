#!/usr/bin/env bash
# 把页面（flood-new 容器）里指定编号的仿真「删掉」——先备份，删完可以恢复；删后下一个新仿真的编号接着序列走（旧 #1 删掉后新的就是 #2）。
#   scripts/web-archive-sim.sh <app 目录（有 app.env / app.sh 的那个）> <仿真编号[,编号…]>          只打印要做什么（dry-run）
#   scripts/web-archive-sim.sh <app 目录> <仿真编号> --yes                                          真做
#   scripts/web-archive-sim.sh <app 目录> --restore <备份目录>                                       恢复（把备份里的记录和抽取结果放回去）
# 做的事（--yes）：① 备份 simulation schema 的 SQL 转储 + 该仿真的抽取结果目录（sim_deliveries/<键>）到 <app 目录>/backups/sim-<时间>/；
#   ② 用页面自己的接口 DELETE /api/simulations/<id> 删记录（排队 / 在算的先取消）；③ 把抽取结果目录挪进备份（不留在 sim_deliveries 里，免得页面再扫到）。
# 序列不回退：仿真编号来自序列 simulation.run_id_seq，删记录不改它。旧 #1 是库里唯一的一条时，删掉后下一个就是 2。
# 交付原件（NAS 里的 deliveries / result / shp_dat）不碰——那由 archive-stale.sh 挪开。
set -euo pipefail
APP="${1:?用法见脚本开头}"; shift
[ -f "$APP/app.env" ] || { echo "✗ $APP 里没有 app.env"; exit 1; }
. "$APP/app.env"
PSQL=(docker exec "$CONTAINER" psql -U postgres -d syq-pipe -Atc)
running() { [ "$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo none)" = running ]; }
running || { echo "✗ $CONTAINER 没在运行"; exit 1; }
if [ "${1:-}" = "--restore" ]; then
  B="${2:?给备份目录}"; [ -f "$B/simulation.sql" ] || { echo "✗ $B/simulation.sql 不存在"; exit 1; }
  echo "恢复 $B："
  for d in "$B"/sim_deliveries/*/; do [ -d "$d" ] && { k="$(basename "$d")"; [ -e "$APP_DATA_DIR/sim_deliveries/$k" ] && { echo "✗ $APP_DATA_DIR/sim_deliveries/$k 已存在，先处理"; exit 1; }; mv "$d" "$APP_DATA_DIR/sim_deliveries/$k"; echo "  抽取结果 → sim_deliveries/$k"; }; done
  docker exec -i "$CONTAINER" psql -U postgres -d syq-pipe < "$B/simulation.sql" >/dev/null && echo "  数据库记录已恢复（simulation schema 按备份 SQL 重建）"
  chmod -R a+rX "$APP_DATA_DIR/sim_deliveries" 2>/dev/null || true; curl -s "http://127.0.0.1:$APP_PORT/api/viewer/rescan" >/dev/null || true
  exit 0
fi
IDS="${1:?给仿真编号}"; YES=0; [ "${2:-}" = "--yes" ] && YES=1
TS="$(date +%Y%m%d-%H%M%S)"; B="$APP/backups/sim-$TS"
echo "页面 $CONTAINER（端口 $APP_PORT），当前最后编号：$("${PSQL[@]}" "select last_value from simulation.run_id_seq")"
declare -a KEYS=()
for id in ${IDS//,/ }; do
  row="$("${PSQL[@]}" "select output_dir||'|'||status from simulation.run where id=$id")" || true
  [ -n "$row" ] || { echo "  #$id：库里没有，跳过"; continue; }
  key="$(basename "${row%|*}")"; KEYS+=("$key")
  echo "  #$id：抽取结果 sim_deliveries/$key（状态 ${row#*|}）"
done
[ ${#KEYS[@]} -gt 0 ] || { echo "没有可删的"; exit 0; }
if [ $YES = 0 ]; then echo "（dry-run，什么都没做。确认后加 --yes；备份会放在 $B）"; exit 0; fi
mkdir -p "$B/sim_deliveries"
docker exec "$CONTAINER" pg_dump -U postgres -d syq-pipe -n simulation --clean --if-exists > "$B/simulation.sql"
echo "  ✔ 数据库备份 $B/simulation.sql（$(wc -c < "$B/simulation.sql") 字节）"
for k in "${KEYS[@]}"; do [ -d "$APP_DATA_DIR/sim_deliveries/$k" ] && cp -a "$APP_DATA_DIR/sim_deliveries/$k" "$B/sim_deliveries/$k" && echo "  ✔ 备份抽取结果 $k"; done
for id in ${IDS//,/ }; do
  code="$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "http://127.0.0.1:$APP_PORT/api/simulations/$id")"
  echo "  DELETE /api/simulations/$id → HTTP $code"; [ "$code" = 204 ] || { echo "✗ 删除失败，备份在 $B，没有挪抽取结果"; exit 1; }
done
for k in "${KEYS[@]}"; do [ -d "$APP_DATA_DIR/sim_deliveries/$k" ] && rm -rf "$APP_DATA_DIR/sim_deliveries/$k" && echo "  ✔ 抽取结果 $k 已从 sim_deliveries 移走（备份里有）"; done
curl -s "http://127.0.0.1:$APP_PORT/api/viewer/rescan" >/dev/null || true
echo "✔ 完成。下一个新仿真的编号：$(( $("${PSQL[@]}" "select last_value from simulation.run_id_seq") + 1 ))。恢复：$0 $APP --restore $B"
