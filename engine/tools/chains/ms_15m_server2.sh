#!/usr/bin/env bash
# 服务器 2（AZPDW006uca03，32 核 / 64 GB，部署包 2026.10.06）：Mussafah_00 模型版 15 m 网格，5 场雨 × 四态 = 20 个态
# 推演计划表 v29「15m格网」第 41–52、65–72 行：
#   M96H24 96 mm / 24 h · M33H24 33 mm / 24 h · M96H2 96 mm / 2 h · M61H1 61 mm / 1 h · M33H2 33 mm / 2 h
#   24 h 的两场排在前面（各模拟 28 h），短雨补空槽。另一半（第 37–40、53–64 行）在服务器 3：ms_15m_server3.sh。
# 口径（用户 10-10「按建议」）：计算域 = 客户 Musaffah_00 模型汇水区并集（1.55 km²）；一维 = 客户 Mussafah_00.inp，L1 剥掉 Parsons 措施；
# L2 区内 0 座已建成调蓄、照算；L2+planned = 客户原件；L2+suggested 用 C1 的 v3 规则。详见 tools/ms_run_15m.py 开头。
# 调度同 C1 15 m（tools/c1_batch.py --runner ms_run_15m.py，8 槽 × 4 线程）；文件前缀 MS_，计算容器 mspipe_*，和 C1 的互不干扰。
#
# 部署包镜像里没有 ms_run_15m.py / ms_post.py、c1_batch.py 是旧版，本脚本先把它们拷进 swbrain 容器，
# 再把计算域、汇水区参数表拷进 /app/out/units_from_models/，客户模型 Mussafah.inp / Mussafah_baseline.inp 不在 /work 才拷，然后在容器里放后台跑。
# 把上传包整个目录放到服务器上（或保持仓库里的 tools/ 与 tools/chains/ 结构）：
#   ms_15m_server2.sh  ms_run_15m.py  ms_post.py  c1_batch.py  data/{ms_model_domain.geojson, ms_subcatchments.csv, Mussafah.inp, Mussafah_baseline.inp}
#
#   bash ms_15m_server2.sh check      # 只读检查：数据、DTM、镜像、替换点、各步骤产物（开跑前先看这个）
#   bash ms_15m_server2.sh start      # 拷脚本与数据 → 检查替换点 / DTM / 磁盘 → 后台开跑（可重复执行：已完成的跳过，在跑的容器会被认领）
#   bash ms_15m_server2.sh status     # 进度表、在跑的容器、可用内存
#   bash ms_15m_server2.sh log        # 跟踪调度日志（Ctrl-C 退出，不影响计算）
#   bash ms_15m_server2.sh plan       # 只列任务
# 可选环境变量：TAGS（缺省见上）、SLOTS（缺省 8）、THREADS（缺省 4）、MIN_FREE_GB（缺省 6）、C=容器名（缺省 swbrain）
# L2+suggested（L2S3）不用另起：同组 L2+planned 一算完，调度器自动设计、接着算 L2S3，四态齐了自动后处理与交付
#   （交付在 /app/out/交付/Mussafah_00_模型版_<雨量>mm<历时>h/）。全部算完后按惯例 bash sync_to_nas.sh start 同步到 NAS。
# 暂停计算用 docker pause <容器>（保留进度），不要 docker stop。
set -euo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
TAGS=${TAGS:-"M96H24 M33H24 M96H2 M61H1 M33H2"}
RUN="--runner ms_run_15m.py --tags $TAGS"
find_src(){ for d in "$HERE" "$HERE/.." "$HERE/data" "$HERE/../../out/units_from_models"; do [ -f "$d/$1" ] && { echo "$d/$1"; return; }; done; echo "找不到 $1（放在本脚本同一目录或 data/ 下）" >&2; exit 1; }

docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行：先在部署包目录 bash start.sh"; exit 1; }
for f in ms_run_15m.py ms_post.py c1_batch.py; do docker cp "$(find_src $f)" "$C:/app/tools/$f"; done
docker exec "$C" mkdir -p /app/out/units_from_models /work/c2d/logs
for f in ms_model_domain.geojson ms_subcatchments.csv; do docker cp "$(find_src $f)" "$C:/app/out/units_from_models/$f"; done
for f in Mussafah.inp Mussafah_baseline.inp; do       # 部署包的工作目录里一般已有（C1 的雨型取自它）：有就不动
  docker exec "$C" test -f "/work/$f" || { echo "  /work/$f 不在，从上传包拷"; docker cp "$(find_src $f)" "$C:/work/$f"; }
done
LOG=/work/c2d/logs/c1_batch_ms_run_15m.log
LOCK=/work/c2d/logs/c1_batch_ms_run_15m.lock

case "${1:-status}" in
  start)
    docker exec "$C" python tools/ms_run_15m.py check-patches
    docker exec "$C" df -h /work
    docker exec -d "$C" sh -c "python -u tools/c1_batch.py run $RUN --slots ${SLOTS:-8} --threads ${THREADS:-4} --min-free-gb ${MIN_FREE_GB:-6} >> $LOG 2>&1"
    sleep 15
    docker exec "$C" tail -n 30 "$LOG"
    echo
    # 预检不过（DTM / 镜像 / 替换点）调度器会在拿锁之前退出：锁文件不在 = 没跑起来
    docker exec "$C" test -f "$LOCK" || { echo "✗ 调度器没跑起来，原因见上面日志"; exit 1; }
    echo "已放后台。看进度：bash $0 status    跟日志：bash $0 log"
    ;;
  check)
    docker exec "$C" python tools/ms_run_15m.py check-patches
    for t in $TAGS; do docker exec "$C" python tools/ms_run_15m.py check --tag "$t"; done
    docker exec "$C" df -h /work ;;
  status) docker exec "$C" python tools/c1_batch.py status $RUN ;;
  plan)   docker exec "$C" python tools/c1_batch.py plan $RUN ;;
  log)    docker exec "$C" tail -n 50 -f "$LOG" ;;
  *) echo "用法：bash $0 start|check|status|log|plan"; exit 1 ;;
esac
