#!/usr/bin/env bash
# 服务器 1（AZPDW006aim01，48 核 / 188 GB，部署包 2026.10.03）：C1 模型版 5 m 网格 32 mm / 1 h（M32H1G5）+ 32 mm / 2 h（M32H2G5）各四态
# 推演计划表 v27 5m格网 第 1–8 行。调度同服务器 2 的 c1_g5_server2.sh（tools/c1_batch.py + c1_run_g5.py）。
# 这台机器 10-08 刚跑完 M61G5（旧脚本 c1_run_m61_5m.py 逐步手动跑），config.toml 的 dtm_path 已指向 50 cm DTM。
#
# 10.03 镜像里没有 c1_run_g5.py / c1_batch.py，本脚本先把它们（连同新版 export_delivery_c1.py）拷进 swbrain 容器，再在容器里放后台跑。
# 把这几个文件放在服务器上同一个目录里（或保持仓库里的 tools/ 与 tools/chains/ 结构）：
#   c1_g5_server1.sh  c1_run_g5.py  c1_batch.py  export_delivery_c1.py
#
#   bash c1_g5_server1.sh start      # 拷脚本 → 检查替换点 / DTM / 内存 → 后台开跑（可重复执行：已完成的跳过，在跑的容器会被认领）
#   bash c1_g5_server1.sh status     # 进度表、在跑的容器、可用内存
#   bash c1_g5_server1.sh log        # 跟踪调度日志（Ctrl-C 退出，不影响计算）
#   bash c1_g5_server1.sh plan       # 只列任务
# 可选环境变量：SLOTS（缺省 6）、THREADS（缺省 7）、MIN_FREE_GB（缺省 20）、C=容器名（缺省 swbrain）
#   6 槽 × 7 线程 = 42 核，留几核给建模 / 设计 / 后处理；每个 5 m 态约 11–18 GB，6 个同时约 110 GB，188 GB 内存够。
# 暂停计算用 docker pause <容器>（保留进度），不要 docker stop。
set -euo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
TAGS="M32H1G5 M32H2G5"
find_src(){ for d in "$HERE" "$HERE/.."; do [ -f "$d/$1" ] && { echo "$d/$1"; return; }; done; echo "找不到 $1（放在本脚本同一目录）" >&2; exit 1; }

docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行：先在部署包目录 bash start.sh"; exit 1; }
for f in c1_run_g5.py c1_batch.py export_delivery_c1.py; do docker cp "$(find_src $f)" "$C:/app/tools/$f"; done
LOG=/work/c2d/logs/c1_g5_batch.log     # c1_batch.py 缺省 runner（c1_run_g5.py）的日志名

case "${1:-status}" in
  start)
    docker exec "$C" python tools/c1_run_g5.py check-patches
    docker exec "$C" mkdir -p /work/c2d/logs
    docker exec -d "$C" sh -c "python -u tools/c1_batch.py run --tags $TAGS --slots ${SLOTS:-6} --threads ${THREADS:-7} --min-free-gb ${MIN_FREE_GB:-20} >> $LOG 2>&1"
    sleep 15
    docker exec "$C" tail -n 30 "$LOG"
    echo
    echo "已放后台。看进度：bash $0 status    跟日志：bash $0 log"
    ;;
  status) docker exec "$C" python tools/c1_batch.py status --tags $TAGS ;;
  plan)   docker exec "$C" python tools/c1_batch.py plan --tags $TAGS ;;
  log)    docker exec "$C" tail -n 50 -f "$LOG" ;;
  *) echo "用法：bash $0 start|status|log|plan"; exit 1 ;;
esac
