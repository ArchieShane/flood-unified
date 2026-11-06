#!/usr/bin/env bash
# 服务器 1（AZPDW006aim01，48 核 / 188 GB，部署包 2026.10.03）：Abu Dhabi 全市 250 m 网格，9 场雨 × L1 / L2 = 18 态
# 推演计划表「250m格网」页。口径见 tools/city_run_250m.py 开头：全市 1,234 km² 计算域、一二维耦合、地形 = 50 cm DTM 按 250 m 取块平均、只做 L1 / L2。
# 调度同 C1 / 区域 15 m：tools/c1_batch.py --runner city_run_250m.py；文件前缀 CT_，计算容器 ctpipe_*，和 5 m / 15 m 的互不干扰。
#
# 部署包镜像里没有 city_run_250m.py / ct_post.py、c1_batch.py 是旧版，本脚本先把它们拷进 swbrain 容器。
# 上传包放在同一个目录：city_250m_server1.sh  city_run_250m.py  ct_post.py  c1_batch.py
#
#   bash city_250m_server1.sh check      # 只读检查：数据、DTM、镜像、文件、各步骤产物（开跑前先看这个）
#   TAGS=CITY_M61H2 bash city_250m_server1.sh start    # 先试跑一场（61 mm / 2 h，L1 + L2 两个态）：看 docker stats 的内存、单态耗时
#   bash city_250m_server1.sh start      # 18 态全排（先 1–2 h 雨，再 24 h 雨）；可重复执行：已完成的跳过，在跑的容器会被认领
#   bash city_250m_server1.sh status | log | plan
# 可选环境变量：TAGS、SLOTS（缺省 5）、THREADS（缺省 8；一维 SWMM 与耦合共用）、MIN_FREE_GB（可用内存，缺省 20）、MIN_DISK_GB（缺省 200）、
#               CITY_RUNOFF=v1（全市统一 CN 82、Tc 30 min；缺省 v2 = 客户子汇水区参数，同 30 m 版）、C=容器名（缺省 swbrain）
# 第一次 start 会先跑 prep：读 GDB 重建全市一维网、算计算域与塘、把 DTM 取平均成 250 m（约十几分钟，之后缓存 c2d/ct_net_CT250.pkl、dem_CT250.tif）。
# 两个态齐了自动后处理与交付（/app/out/交付/全市_模型版_<雨量>mm<历时>h_250m/）。全部算完后 bash sync_to_nas.sh start。
# 暂停计算用 docker pause <容器>（保留进度），不要 docker stop。
set -euo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
TAGS=${TAGS:-"CITY_M61H2 CITY_M33H1 CITY_M33H2 CITY_M61H1 CITY_M96H1 CITY_M96H2 CITY_M33H24 CITY_M61H24 CITY_M96H24"}
RUN="--runner city_run_250m.py --tags $TAGS"
MIN_DISK_GB=${MIN_DISK_GB:-200}
find_src(){ for d in "$HERE" "$HERE/.." "$HERE/data"; do [ -f "$d/$1" ] && { echo "$d/$1"; return; }; done; echo "找不到 $1（放在本脚本同一目录下）" >&2; exit 1; }

docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行：先在部署包目录 bash start.sh"; exit 1; }
for f in city_run_250m.py ct_post.py c1_batch.py; do docker cp "$(find_src $f)" "$C:/app/tools/$f"; done
docker exec "$C" mkdir -p /work/c2d/logs
# 全市底图数据（客户塘轮廓、塘汇水区、客户汇水区、子汇水区产流参数）：缺的才拷，不覆盖容器里已有的
docker exec "$C" mkdir -p /app/console/data /app/out/citywide
for f in ponds_l2.geojson l2_pond_catch.geojson catchments_sw.geojson; do
  docker exec "$C" test -f "/app/console/data/$f" || docker cp "$HERE/data/$f" "$C:/app/console/data/$f"
done
docker exec "$C" mkdir -p /app/out/pondlink
docker exec "$C" test -f /app/out/pondlink/ponds_linked.json || docker cp "$HERE/pondlink/ponds_linked.json" "$C:/app/out/pondlink/ponds_linked.json"
docker exec "$C" test -f /app/out/citywide/node_runoff.json || docker cp "$HERE/citywide/node_runoff.json" "$C:/app/out/citywide/node_runoff.json"
LOG=/work/c2d/logs/c1_batch_city_run_250m.log
LOCK=/work/c2d/logs/c1_batch_city_run_250m.lock

case "${1:-status}" in
  start)
    docker exec "$C" python tools/city_run_250m.py check-patches
    docker exec "$C" test -f /work/Mussafah_baseline.inp || echo "! /work/Mussafah_baseline.inp 不在：prep 会从客户 Mussafah_00.inp 生成"
    docker exec "$C" df -h /work
    FREE=$(docker exec "$C" df -BG --output=avail /work | tail -1 | tr -dc 0-9)
    if [ "${FREE:-0}" -lt "$MIN_DISK_GB" ]; then
      echo "✗ /work 只剩 ${FREE} GB（< ${MIN_DISK_GB} GB）：先 sync_to_nas.sh 同步 + verify，再 clean_synced.sh run 腾空间"; exit 1
    fi
    docker exec -d "$C" sh -c "python -u tools/c1_batch.py run $RUN --slots ${SLOTS:-5} --threads ${THREADS:-8} --min-free-gb ${MIN_FREE_GB:-20} >> $LOG 2>&1"
    sleep 15
    docker exec "$C" tail -n 30 "$LOG"
    echo
    docker exec "$C" test -f "$LOCK" || { echo "✗ 调度器没跑起来，原因见上面日志"; exit 1; }
    echo "已放后台。看进度：bash $0 status    跟日志：bash $0 log    内存：docker stats"
    ;;
  check)
    docker exec "$C" python tools/city_run_250m.py check-patches
    for t in $TAGS; do docker exec "$C" python tools/city_run_250m.py check --tag "$t"; done
    docker exec "$C" df -h /work ;;
  status) docker exec "$C" python tools/c1_batch.py status $RUN ;;
  plan)   docker exec "$C" python tools/c1_batch.py plan $RUN ;;
  log)    docker exec "$C" tail -n 50 -f "$LOG" ;;
  *) echo "用法：bash $0 start|check|status|log|plan"; exit 1 ;;
esac
