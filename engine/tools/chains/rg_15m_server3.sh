#!/usr/bin/env bash
# 服务器 3（AZPDW006uca06，32 核 / 64 GB，部署包 2026.10.06）：三个区域 15 m 网格，11 场雨 × L1 / L2 = 22 个态
# 区域：YAS = Yas bridges interchange（汇水区 RBE1-OF1）、BAT = Al Bateen（RD-A）、BNY = Bani Yas（RD 14）；情景名 <区域>_M<雨量>H<历时>。
# 推演计划表 v34「15m格网」第 73–126 行里分给服务器 3 的场次（另一半在服务器 2：rg_15m_server2.sh）。
# 口径（用户 10-11）：计算域 = 客户汇水区图层里那一个多边形；一维 = 全市管网按计算域截取（连通块整块取）；L1 既有管网 + 现状泵站；
# L2 = L1 + 已建成塘（YAS、BAT 区内没有，L2 同 L1，照算；BNY 有 Baniyas Lake）；只做 L1 / L2；产流 CN 82、Tc 30 min（全市值）。
# 详见 tools/rg_run_15m.py 开头。调度同 C1 15 m（tools/c1_batch.py --runner rg_run_15m.py，8 槽 × 4 线程）；文件前缀 RG_，计算容器 rgpipe_*，和 C1 / Mussafah 的互不干扰。
#
# 部署包镜像里没有 rg_run_15m.py / rg_regions.py / rg_post.py、c1_batch.py 是旧版（缺「runner 只做 L1/L2」的两行），本脚本先把它们拷进 swbrain 容器，
# 再把 cases/regions/（三个区域的计算域、现状泵站、已建成塘）拷进 /app/cases/regions/。雨型来源 /work/Mussafah_baseline.inp 在跑过 C1 / Mussafah 的机器上已有。
# 把上传包整个目录放到服务器上：
#   rg_15m_server3.sh  rg_run_15m.py  rg_regions.py  rg_post.py  c1_batch.py  cases/regions/*.{geojson,json}
#
#   bash rg_15m_server3.sh check      # 只读检查：数据、DTM、镜像、文件、各步骤产物（开跑前先看这个）
#   bash rg_15m_server3.sh start      # 拷脚本与数据 → 检查文件 / DTM / 磁盘 → 后台开跑（可重复执行：已完成的跳过，在跑的容器会被认领）
#   bash rg_15m_server3.sh status     # 进度表、在跑的容器、可用内存
#   bash rg_15m_server3.sh log        # 跟踪调度日志（Ctrl-C 退出，不影响计算）
#   bash rg_15m_server3.sh plan       # 只列任务
# 可选环境变量：TAGS（缺省见下）、SLOTS（缺省 8）、THREADS（缺省 4）、MIN_FREE_GB（缺省 6）、C=容器名（缺省 swbrain）
# 两个态齐了自动后处理与交付（交付在 /app/out/交付/<区域目录>_模型版_<雨量>mm<历时>h/）。全部算完后按惯例 bash sync_to_nas.sh start 同步到 NAS。
# 暂停计算用 docker pause <容器>（保留进度），不要 docker stop。
set -euo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
TAGS=${TAGS:-"YAS_M61H24 YAS_M61H1 YAS_M96H1 YAS_M96H2 BAT_M33H1 BAT_M33H2 BAT_M61H2 BNY_M33H24 BNY_M61H24 BNY_M33H2 BNY_M61H1"}
RUN="--runner rg_run_15m.py --tags $TAGS"
find_src(){ for d in "$HERE" "$HERE/.." "$HERE/data"; do [ -f "$d/$1" ] && { echo "$d/$1"; return; }; done; echo "找不到 $1（放在本脚本同一目录或 data/ 下）" >&2; exit 1; }
find_cases(){ for d in "$HERE/cases/regions" "$HERE/../cases/regions" "$HERE/../../cases/regions"; do [ -d "$d" ] && { echo "$d"; return; }; done; echo "找不到 cases/regions/（放在本脚本同一目录下）" >&2; exit 1; }

docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行：先在部署包目录 bash start.sh"; exit 1; }
for f in rg_run_15m.py rg_regions.py rg_post.py c1_batch.py; do docker cp "$(find_src $f)" "$C:/app/tools/$f"; done
docker exec "$C" mkdir -p /app/cases/regions /work/c2d/logs
docker cp "$(find_cases)/." "$C:/app/cases/regions/"
docker exec "$C" test -f /work/Mussafah_baseline.inp || { echo "✗ /work/Mussafah_baseline.inp 不在（雨型来源）：先跑过 C1 / Mussafah 的 prep，或把它拷进 /work"; exit 1; }
LOG=/work/c2d/logs/c1_batch_rg_run_15m.log
LOCK=/work/c2d/logs/c1_batch_rg_run_15m.lock

case "${1:-status}" in
  start)
    docker exec "$C" python tools/rg_run_15m.py check-patches
    docker exec "$C" df -h /work
    docker exec -d "$C" sh -c "python -u tools/c1_batch.py run $RUN --slots ${SLOTS:-8} --threads ${THREADS:-4} --min-free-gb ${MIN_FREE_GB:-6} >> $LOG 2>&1"
    sleep 15
    docker exec "$C" tail -n 30 "$LOG"
    echo
    # 预检不过（DTM / 镜像 / 文件）调度器会在拿锁之前退出：锁文件不在 = 没跑起来
    docker exec "$C" test -f "$LOCK" || { echo "✗ 调度器没跑起来，原因见上面日志"; exit 1; }
    echo "已放后台。看进度：bash $0 status    跟日志：bash $0 log"
    ;;
  check)
    docker exec "$C" python tools/rg_run_15m.py check-patches
    for t in $TAGS; do docker exec "$C" python tools/rg_run_15m.py check --tag "$t"; done
    docker exec "$C" df -h /work ;;
  status) docker exec "$C" python tools/c1_batch.py status $RUN ;;
  plan)   docker exec "$C" python tools/c1_batch.py plan $RUN ;;
  log)    docker exec "$C" tail -n 50 -f "$LOG" ;;
  *) echo "用法：bash $0 start|check|status|log|plan"; exit 1 ;;
esac
