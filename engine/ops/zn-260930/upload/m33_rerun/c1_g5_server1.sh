#!/usr/bin/env bash
# 服务器 1（AZPDW006aim01，48 核 / 188 GB，部署包 2026.10.03）：C1 模型版 5 m 网格，多场雨各四态排队跑
#   第一批（原 32 mm 的 M32H1G5 + M32H2G5 因客户雨量改为 33 mm 作废，需按 M33H1G5 + M33H2G5 重跑，计划表 v33）
#   第二批（v30 5m格网 第 9–16、21–36 行，缺省 TAGS，2026-10-10 起）：C1 剩下的六场雨一次排完，按下面的顺序开态
#     M33H24G5 33 mm / 24 h → M61H1G5 61 mm / 1 h → M61H24G5 61 mm / 24 h → M96H1G5 96 mm / 1 h → M96H2G5 96 mm / 2 h → M96H24G5 96 mm / 24 h
#     前两场 6 个态先占满 6 槽；后面的场次等前面的态算完、槽空出来才开，不抢 L2S3 的槽（c1_batch.py 会给 L2S3 留槽）。
#     缺省 TAGS 只排 3 天内能算完的三场 1–2 h 雨（M61H1G5 M96H1G5 M96H2G5，12 个态）；24 h 雨每态约 85–90 h，要跑就显式给 TAGS：
#       TAGS="M61H1G5 M96H1G5 M96H2G5 M33H24G5" bash c1_g5_server1.sh start     # 短雨算完后 M33H24G5 接着占满槽位
#     之后想加场次，用新的 TAGS 再 start 一次即可（已完成的跳过，在跑的容器会被认领）。
# 调度同服务器 2 的 c1_g5_server2.sh（tools/c1_batch.py + c1_run_g5.py）。config.toml 的 dtm_path 已指向 50 cm DTM。
#
# 10.03 镜像里没有 c1_run_g5.py / c1_batch.py，本脚本先把它们（连同新版 export_delivery_c1.py）拷进 swbrain 容器，再在容器里放后台跑。
# 把这几个文件放在服务器上同一个目录里（或保持仓库里的 tools/ 与 tools/chains/ 结构）：
#   c1_g5_server1.sh  c1_run_g5.py  c1_batch.py  export_delivery_c1.py
#
#   bash c1_g5_server1.sh check      # 只读检查：数据路径、DTM、镜像、各步骤产物（开跑前先看这个）
#   bash c1_g5_server1.sh start      # 拷脚本 → 检查替换点 / DTM / 内存 / 磁盘 → 后台开跑（可重复执行：已完成的跳过，在跑的容器会被认领）
#   bash c1_g5_server1.sh status     # 进度表、在跑的容器、可用内存
#   bash c1_g5_server1.sh log        # 跟踪调度日志（Ctrl-C 退出，不影响计算）
#   bash c1_g5_server1.sh plan       # 只列任务
# 看 33 mm 两场的进度：TAGS="M33H1G5 M33H2G5" bash c1_g5_server1.sh status
# 可选环境变量：TAGS（缺省见上）、SLOTS（缺省 6）、THREADS（缺省 7）、MIN_FREE_GB（可用内存，缺省 20）、
#               MIN_DISK_GB（/work 剩余磁盘，缺省 400）、C=容器名（缺省 swbrain）
#   6 槽 × 7 线程 = 42 核，留几核给建模 / 设计 / 后处理；每个 5 m 态约 11–18 GB，6 个同时约 110 GB，188 GB 内存够。
# L2+suggested（L2S3）不用另起：同组 L2+planned 一算完，调度器自动设计箱涵、接着算 L2S3，四态齐了自动后处理与交付。
# 暂停计算用 docker pause <容器>（保留进度），不要 docker stop。
set -euo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
TAGS=${TAGS:-"M61H1G5 M96H1G5 M96H2G5 M33H1G5 M33H2G5"}
MIN_DISK_GB=${MIN_DISK_GB:-400}        # 开跑前 /work 至少要剩这么多（GB）：c1_batch.py 只按内存排队，不看磁盘
find_src(){ for d in "$HERE" "$HERE/.."; do [ -f "$d/$1" ] && { echo "$d/$1"; return; }; done; echo "找不到 $1（放在本脚本同一目录）" >&2; exit 1; }

docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行：先在部署包目录 bash start.sh"; exit 1; }
for f in c1_run_g5.py c1_batch.py export_delivery_c1.py; do docker cp "$(find_src $f)" "$C:/app/tools/$f"; done
LOG=/work/c2d/logs/c1_g5_batch.log     # c1_batch.py 缺省 runner（c1_run_g5.py）的日志名
LOCK=/work/c2d/logs/c1_g5_batch.lock

case "${1:-status}" in
  start)
    docker exec "$C" python tools/c1_run_g5.py check-patches
    # 三场 24 h 雨各模拟 28 h，结果比 1–2 h 雨大得多；有计算容器在跑时 clean_synced.sh 不肯清理，所以开跑前就要留够
    docker exec "$C" df -h /work /app/out
    FREE=$(docker exec "$C" df -BG --output=avail /work | tail -1 | tr -dc 0-9)
    if [ "${FREE:-0}" -lt "$MIN_DISK_GB" ]; then
      echo "✗ /work 只剩 ${FREE} GB（< ${MIN_DISK_GB} GB）：先 sync_to_nas.sh 同步 + verify，再 clean_synced.sh run 腾空间"
      echo "  确定够用就 MIN_DISK_GB=${FREE} bash $0 start"; exit 1
    fi
    docker exec "$C" mkdir -p /work/c2d/logs
    docker exec -d "$C" sh -c "python -u tools/c1_batch.py run --tags $TAGS --slots ${SLOTS:-6} --threads ${THREADS:-7} --min-free-gb ${MIN_FREE_GB:-20} >> $LOG 2>&1"
    sleep 15
    docker exec "$C" tail -n 30 "$LOG"
    echo
    # 预检不过（DTM / 镜像 / 替换点）调度器会在拿锁之前退出：锁文件不在 = 没跑起来
    docker exec "$C" test -f "$LOCK" || { echo "✗ 调度器没跑起来，原因见上面日志"; exit 1; }
    echo "已放后台。看进度：bash $0 status    跟日志：bash $0 log"
    ;;
  check)  for t in $TAGS; do docker exec "$C" python tools/c1_run_g5.py check --tag "$t"; done
          docker exec "$C" df -h /work /app/out ;;
  status) docker exec "$C" python tools/c1_batch.py status --tags $TAGS ;;
  plan)   docker exec "$C" python tools/c1_batch.py plan --tags $TAGS ;;
  log)    docker exec "$C" tail -n 50 -f "$LOG" ;;
  *) echo "用法：bash $0 start|check|status|log|plan"; exit 1 ;;
esac
