#!/usr/bin/env bash
# 服务器 2（32 核 / 64 GB，部署包 2026.10.06）：C1 模型版 15 m 网格五场雨各四态（L1 / L2 / L2+planned / L2+suggested）
# 推演计划表 v25「15m格网」第 1–8、17–20、25–32 行：
#   M33H1 33 mm / 1 h（1–4）  M33H2 33 mm / 2 h（5–8）  M61 61 mm / 2 h（17–20）  M96H1 96 mm / 1 h（25–28）  M96H2 96 mm / 2 h（29–32）
# 同时最多 8 个态、每态 4 线程（机器配置表：15 m 8 个态 × 4 线程）；调度见 tools/c1_batch.py --runner c1_run_15m.py，与服务器 3 的 c1_15m_server3.sh 同一套。
#
# M61、M96H1 部署包里带了旧结果，用户 10-08 要求重算：先执行一次 reset，把旧结果挪到备份目录（不删），调度器才会重算。
# 部署包镜像里没有 c1_run_15m.py / c1_batch.py，本脚本先把它们拷进 swbrain 容器，再在容器里放后台跑。
# 把这三个文件放在服务器上同一个目录里（或保持仓库里的 tools/ 与 tools/chains/ 结构）：
#   c1_15m_server2.sh  c1_run_15m.py  c1_batch.py
#
#   bash c1_15m_server2.sh reset      # 只做一次：M61 / M96H1 旧结果挪到 /work/_bak_15m_reset 与 /app/out/_bak_15m_reset（做过就不再做）
#   bash c1_15m_server2.sh start      # 拷脚本 → 检查替换点 / DTM / 内存 → 后台开跑（可重复执行：已完成的跳过，在跑的容器会被认领）
#   bash c1_15m_server2.sh status     # 进度表、在跑的容器、可用内存
#   bash c1_15m_server2.sh log        # 跟踪调度日志（Ctrl-C 退出，不影响计算）
#   bash c1_15m_server2.sh plan       # 只列任务
#   bash c1_15m_server2.sh check      # 只读检查：数据路径、DTM、镜像、各步骤产物
# 可选环境变量：TAGS（缺省 "M33H1 M33H2 M61 M96H1 M96H2"）、SLOTS（缺省 8）、THREADS（缺省 4）、MIN_FREE_GB（缺省 6）、C=容器名（缺省 swbrain）
# L2+suggested（L2S3）不用另起：同组 L2+planned 一算完，调度器自动设计箱涵、接着算 L2S3，四态齐了自动后处理与交付。
# 暂停计算用 docker pause <容器>（保留进度），不要 docker stop。
set -euo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
TAGS=${TAGS:-"M33H1 M33H2 M61 M96H1 M96H2"}
REDO="M61 M96H1"                      # 部署包里有旧结果、要重算的情景
RUN="--runner c1_run_15m.py --tags $TAGS"
find_src(){ for d in "$HERE" "$HERE/.."; do [ -f "$d/$1" ] && { echo "$d/$1"; return; }; done; echo "找不到 $1（放在本脚本同一目录）" >&2; exit 1; }

docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行：先在部署包目录 bash start.sh"; exit 1; }
for f in c1_run_15m.py c1_batch.py; do docker cp "$(find_src $f)" "$C:/app/tools/$f"; done
LOG=/work/c2d/logs/c1_batch_c1_run_15m.log
MARK=/work/c2d/logs/c1_15m_server2.reset_done

case "${1:-status}" in
  reset)
    if docker exec "$C" test -f "$MARK"; then echo "已经 reset 过（$MARK），不再挪：再挪会把新算的结果也挪走"; exit 0; fi
    if docker exec "$C" test -f /work/c2d/logs/c1_batch_c1_run_15m.lock; then echo "✗ 调度器锁文件在，先确认调度器没在跑再 reset"; exit 1; fi
    docker exec "$C" sh -c "docker ps --format '{{.Names}}' 2>/dev/null | grep -E 'c1pipe_($(echo $REDO | tr ' ' '|'))_' && { echo '✗ 还有这些情景的计算容器在跑'; exit 1; } || true"
    docker exec "$C" sh -c '
      W=/work/_bak_15m_reset; O=/app/out/_bak_15m_reset
      mkdir -p $W/c2d/anuga_out $W/c2d/logs $O/交付 $O/console_data /work/c2d/logs
      for t in '"$REDO"'; do
        lt=$(echo $t | tr A-Z a-z)
        for f in /work/C1_${t}_* ;                         do [ -e "$f" ] && mv -v "$f" $W/; done
        for f in /work/c2d/C1_${t}_* /work/c2d/twoway_C1_${t}_* /work/c2d/pilot_C1_${t}_* ; do [ -e "$f" ] && mv -v "$f" $W/c2d/; done
        for f in /work/c2d/anuga_out/C1_${t}_* ;           do [ -e "$f" ] && mv -v "$f" $W/c2d/anuga_out/; done
        for f in /work/c2d/logs/C1_${t}_* /work/c2d/logs/g5_*_${t}.log /work/c2d/logs/g5_*_${t}_* ; do [ -e "$f" ] && mv -v "$f" $W/c2d/logs/; done
        for f in /app/out/c1_stats_${t}.* ;                do [ -e "$f" ] && mv -v "$f" $O/; done
        [ -e /app/console/data/flood2d_c1_${lt} ] && mv -v /app/console/data/flood2d_c1_${lt} $O/console_data/
      done
      for d in C1_模型版_61mm2h C1_模型版_96mm1h; do [ -e "/app/out/交付/$d" ] && mv -v "/app/out/交付/$d" $O/交付/; done
      date "+%F %T" > '"$MARK"'
      echo "旧结果已挪到 $W 与 $O（没删，确认新结果没问题后再自行删除）"
    '
    ;;
  start)
    docker exec "$C" test -f "$MARK" || { echo "✗ 还没 reset：M61 / M96H1 的旧结果在，调度器会当成已完成跳过。先 bash $0 reset"; exit 1; }
    docker exec "$C" python tools/c1_run_15m.py check-patches
    docker exec "$C" mkdir -p /work/c2d/logs
    docker exec -d "$C" sh -c "python -u tools/c1_batch.py run $RUN --slots ${SLOTS:-8} --threads ${THREADS:-4} --min-free-gb ${MIN_FREE_GB:-6} >> $LOG 2>&1"
    sleep 15
    docker exec "$C" tail -n 30 "$LOG"
    echo
    # 预检不过（DTM / 镜像 / 替换点）调度器会在拿锁之前退出：锁文件不在 = 没跑起来
    docker exec "$C" test -f /work/c2d/logs/c1_batch_c1_run_15m.lock || { echo "✗ 调度器没跑起来，原因见上面日志"; exit 1; }
    echo "已放后台。看进度：bash $0 status    跟日志：bash $0 log"
    ;;
  check)  for t in $TAGS; do docker exec "$C" python tools/c1_run_15m.py check --tag "$t"; done ;;
  status) docker exec "$C" python tools/c1_batch.py status $RUN ;;
  plan)   docker exec "$C" python tools/c1_batch.py plan $RUN ;;
  log)    docker exec "$C" tail -n 50 -f "$LOG" ;;
  *) echo "用法：bash $0 reset|start|check|status|log|plan"; exit 1 ;;
esac
