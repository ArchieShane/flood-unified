#!/usr/bin/env bash
# 服务器 3（32 核 / 64 GB）：C1 模型版 15 m 网格 33 mm / 24 h（M33H24）+ 61 mm / 1 h（M61H1）各四态
# 推演计划表 v24「15m格网」第 9–16 行。同时最多 8 个态、每态 4 线程（机器配置表：15 m 8 个态 × 4 线程）；调度见 tools/c1_batch.py --runner c1_run_15m.py。
#
# 部署包镜像里没有 c1_run_15m.py / c1_batch.py，本脚本先把它们拷进 swbrain 容器，再在容器里放后台跑。
# 把这三个文件放在服务器上同一个目录里（或保持仓库里的 tools/ 与 tools/chains/ 结构）：
#   c1_15m_server3.sh  c1_run_15m.py  c1_batch.py
#
#   bash c1_15m_server3.sh start      # 拷脚本 → 检查替换点 / DTM / 内存 → 后台开跑（可重复执行：已完成的跳过，在跑的容器会被认领）
#   bash c1_15m_server3.sh status     # 进度表、在跑的容器、可用内存
#   bash c1_15m_server3.sh log        # 跟踪调度日志（Ctrl-C 退出，不影响计算）
#   bash c1_15m_server3.sh plan       # 只列任务
#   bash c1_15m_server3.sh check      # 只读检查：数据路径、DTM、镜像、各步骤产物（开跑前先看这个）
# 可选环境变量：TAGS（缺省 "M33H24 M61H1"；后续排期可加 M61H24 M96H24）、SLOTS（缺省 8）、THREADS（缺省 4）、MIN_FREE_GB（缺省 6）、C=容器名（缺省 swbrain）
# L2+suggested（L2S3）不用另起：同组 L2+planned 一算完，调度器自动设计箱涵、接着算 L2S3，四态齐了自动后处理与交付。
# 暂停计算用 docker pause <容器>（保留进度），不要 docker stop。
set -euo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
TAGS=${TAGS:-"M33H24 M61H1"}
RUN="--runner c1_run_15m.py --tags $TAGS"
find_src(){ for d in "$HERE" "$HERE/.."; do [ -f "$d/$1" ] && { echo "$d/$1"; return; }; done; echo "找不到 $1（放在本脚本同一目录）" >&2; exit 1; }

docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行：先在部署包目录 bash start.sh"; exit 1; }
for f in c1_run_15m.py c1_batch.py; do docker cp "$(find_src $f)" "$C:/app/tools/$f"; done
LOG=/work/c2d/logs/c1_batch_c1_run_15m.log

case "${1:-status}" in
  start)
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
  *) echo "用法：bash $0 start|check|status|log|plan"; exit 1 ;;
esac
