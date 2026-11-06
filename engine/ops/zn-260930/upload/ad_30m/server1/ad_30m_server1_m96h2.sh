#!/usr/bin/env bash
# ADM 汇水区 30 m 推演 · 96 mm / 2 h（用户 10-17 加；61 mm / 1 h 那批已跑完） · 服务器 1（48 核 / 188 GB，AZPDW006aim01）：MI-8（250.6 km²，约 128 万个三角单元，L1 + L2 两个态同时算，各吃一半核）
# 口径：14 个 ADM 汇水区 × L1 / L2 × 96 mm / 2 h，二维 30 m；每个汇水区的计算域 = 客户汇水区图层（KMZ）里那一个多边形（详见 tools/ad_run_30m.py 开头）。
# 本机分到的情景：MI8_M96H2
#
# 成果直接写 NAS（不用再同步）：
#   $NAS_BASE/result/<主机名>/<区域目录>_96mm2h_30m/    积水点结果表与图层、统计、各态一维 / 二维（含 .sww、最大水深 tif）、计算设置
#   $NAS_BASE/shp_dat/<主机名>/<区域目录>_96mm2h_30m/<L1|L2>/{shp,dat}   .sww → shp + RESULTSxxx.dat（hydro2d_convert.py；推演全部完成后自动转换，在 result 同级目录）
#   NAS_BASE 缺省 /media/pop-fs/flood_data
#
# 目录里要有（整个目录拷到服务器上）：
#   ad_30m_server1_m96h2.sh   ad_run_30m.py  ad_regions.py  ad_post.py  c1_batch.py   cases/ad/*   h2d/{Dockerfile,hydro2d_convert.py,pyshp-*.whl}
#
#   bash ad_30m_server1_m96h2.sh check      # 只读检查：容器 / 镜像 / NAS 可写 / 数据 / 各情景产物（开跑前先看这个）
#   bash ad_30m_server1_m96h2.sh start      # 拷脚本与案例数据进 swbrain 容器 → 检查 → 后台开跑；全部算完自动 sww → shp + dat（可重复执行：已完成的跳过，在跑的容器会被认领）
#   bash ad_30m_server1_m96h2.sh status     # 调度进度表、在跑的容器、可用内存、NAS 上已交付 / 已转换的个数
#   bash ad_30m_server1_m96h2.sh log        # 跟踪调度日志（Ctrl-C 退出，不影响计算）
#   bash ad_30m_server1_m96h2.sh plan       # 只列任务
#   bash ad_30m_server1_m96h2.sh convert    # 手动补转换：把还没转的 .sww 转成 shp + dat（start 的最后一步就是它）
# 可选环境变量：TAGS、SLOTS、THREADS_MAP（如 "MI8=22,RD2=11"，按区域给每个态的线程数）、MIN_FREE_GB、CONV_JOBS、H2D_ARGS（缺省 --no-csv --wet-only-shp --drop-threshold 0.01，即 shp 筛掉水深 < 1 cm 的网格）、NAS_BASE、C=容器名（缺省 swbrain）
# 暂停计算用 docker pause <容器>（保留进度），不要 docker stop。
set -euo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
SERVER=1
NAS_BASE=${NAS_BASE:-/media/pop-fs/flood_data}
RESULT_ROOT=${RESULT_ROOT:-$NAS_BASE/result}
SHP_ROOT=${SHP_ROOT:-$NAS_BASE/shp_dat}
HOST_NAME=${HOST_NAME:-$(hostname -s)}
H2D_IMAGE=${H2D_IMAGE:-h2d-convert:1.0}
# shp 只留全程最大水深 ≥ 1 cm 的网格（--wet-only-shp --drop-threshold 0.01；dat 仍是全域，ID 与 shp 对得上）；30 m 单态几十万～上百万个单元 × 61 帧，逐帧 csv 太慢太大，缺省 --no-csv
H2D_ARGS=${H2D_ARGS:---no-csv --wet-only-shp --drop-threshold 0.01}
CORES=${CORES:-$(nproc)}
scale(){ local r=$(( $1 * CORES / 32 )); [ "$r" -lt 1 ] && r=1; echo "$r"; }     # 线程数按 32 核给的，换核数的机器按比例缩放

# ---- 本机的并行配置（尽量吃满 CPU：同时在算的态 × 每态线程数 ≈ 核数；内存见各区域估算）
TAGS=${TAGS:-"MI8_M96H2"}
SLOTS=${SLOTS:-2}                                    # L1 / L2 同时算
THREADS_MAP=${THREADS_MAP:-"MI8=$(( (CORES - 4) / 2 ))"}   # 留 4 核给调度 / 建模 / 后处理；48 核 → 每态 22 线程
MIN_FREE_GB=${MIN_FREE_GB:-20}
CONV_JOBS=${CONV_JOBS:-2}                            # 算完后 2 个态同时转 shp + dat
SWMM_THREADS=${SWMM_THREADS:-8}         # 一维 SWMM 线程数上限（实际取该区域每态线程数与它的较小值）

LOG=/work/c2d/logs/c1_batch_ad_run_30m.log
LOCK=/work/c2d/logs/c1_batch_ad_run_30m.lock
RUN="--runner ad_run_30m.py --tags $TAGS"
DRV_LOG=$HERE/ad_30m_server${SERVER}_m96h2.driver.log
DRV_PID=$HERE/ad_30m_server${SERVER}_m96h2.driver.pid
EXEC_ENV=(-e "AD_HOST=$HOST_NAME" -e "AD_RESULT_ROOT=$RESULT_ROOT" -e "AD_THREADS_MAP=$THREADS_MAP" -e "AD_SWMM_THREADS=$SWMM_THREADS")
CEXEC=(docker exec "${EXEC_ENV[@]}" "$C")

find_src(){ for d in "$HERE" "$HERE/.." "$HERE/tools"; do [ -f "$d/$1" ] && { echo "$d/$1"; return; }; done; echo "找不到 $1（放在本脚本同一目录下）" >&2; exit 1; }
find_cases(){ for d in "$HERE/cases/ad" "$HERE/../cases/ad"; do [ -d "$d" ] && { echo "$d"; return; }; done; echo "找不到 cases/ad/（放在本脚本同一目录下）" >&2; exit 1; }
driver_alive(){ [ -f "$DRV_PID" ] && kill -0 "$(cat "$DRV_PID")" 2>/dev/null; }
sched_alive(){ docker exec "$C" sh -c "test -f $LOCK && kill -0 \$(cut -d' ' -f1 $LOCK) 2>/dev/null" 2>/dev/null; }

need_container(){ docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行：先在部署包目录 bash start.sh"; exit 1; }; }
copy_files(){
  for f in ad_run_30m.py ad_regions.py ad_post.py c1_batch.py; do docker cp "$(find_src $f)" "$C:/app/tools/$f"; done
  docker cp "$(find_src ad_frames.py)" "$C:/work/c2d/ad_frames.py"
  docker exec "$C" mkdir -p /app/cases/ad /work/c2d/logs
  docker cp "$(find_cases)/." "$C:/app/cases/ad/"
}
check_nas(){
  [ -d "$NAS_BASE" ] || { echo "✗ $NAS_BASE 不存在：NAS 没挂上？"; return 1; }
  mkdir -p "$RESULT_ROOT/$HOST_NAME" "$SHP_ROOT/$HOST_NAME" && touch "$RESULT_ROOT/$HOST_NAME/.w" && rm -f "$RESULT_ROOT/$HOST_NAME/.w" || { echo "✗ $RESULT_ROOT/$HOST_NAME 写不进去（权限？）"; return 1; }
  echo "✓ NAS 可写：$RESULT_ROOT/$HOST_NAME 、$SHP_ROOT/$HOST_NAME；剩余 $(df -h "$NAS_BASE" | awk 'NR==2{print $4}')"
}
ensure_h2d(){
  docker image inspect "$H2D_IMAGE" >/dev/null 2>&1 && { echo "✓ 转换镜像 $H2D_IMAGE 已有"; return 0; }
  docker image inspect anuga-swmm:1.0 >/dev/null 2>&1 || { echo "✗ 没有 anuga-swmm:1.0，造不了转换镜像"; return 1; }
  echo "转换镜像 $H2D_IMAGE 不在，用 h2d/ 里的文件离线构建（anuga-swmm:1.0 + pyshp + hydro2d_convert.py）"
  docker build -t "$H2D_IMAGE" -f "$HERE/h2d/Dockerfile" "$HERE/h2d"
}

# ---- .sww → shp + dat（每个态一个容器、单线程；CONV_JOBS 个并行）
conv_one(){
  local sww=$1 out=$2 log=$3
  mkdir -p "$(dirname "$log")"
  rm -rf "$out.partial"
  if docker run --rm -e OMP_NUM_THREADS=1 -v "$NAS_BASE:$NAS_BASE" "$H2D_IMAGE" "$sww" -o "$out.partial" --name boundary --src-crs EPSG:32640 $H2D_ARGS > "$log" 2>&1; then
    rm -rf "$out"; mv "$out.partial" "$out"; echo "  ✓ 转换完成 $out"
  else
    echo "  ✗ 转换失败 $sww（日志 $log）"; return 1
  fi
}
export -f conv_one
export NAS_BASE H2D_IMAGE H2D_ARGS
convert_all(){
  ensure_h2d
  local list; list=$(mktemp)
  # <区域目录>/<L1_既有管网>/二维/<名>.sww  →  $SHP_ROOT/<主机>/<区域目录>/<L1>/；有 h2d_index.json 的算转完了
  find "$RESULT_ROOT/$HOST_NAME" -path "${PATHPAT:-*/二维/*.sww}" -type f 2>/dev/null | sort | while IFS= read -r sww; do
    st_dir=$(basename "$(dirname "$(dirname "$sww")")"); reg_dir=$(basename "$(dirname "$(dirname "$(dirname "$sww")")")")
    out="$SHP_ROOT/$HOST_NAME/$reg_dir/${st_dir%%_*}"
    [ -f "$out/h2d_index.json" ] && continue
    printf '%s\t%s\t%s\n' "$sww" "$out" "$SHP_ROOT/$HOST_NAME/_logs/${reg_dir}_${st_dir%%_*}.log"
  done > "$list"
  local n; n=$(wc -l < "$list")
  echo "$(date '+%F %T') 待转换 $n 个 .sww（并行 ${CONV_JOBS}）"
  if [ "$n" -gt 0 ]; then
    tr '\t' '\n' < "$list" | xargs -d '\n' -n 3 -P "$CONV_JOBS" bash -c 'conv_one "$1" "$2" "$3"' _ || echo "有转换失败的，看 $SHP_ROOT/$HOST_NAME/_logs/"
  fi
  rm -f "$list"
  echo "$(date '+%F %T') 转换结束：shp_dat 里已有 $(find "$SHP_ROOT/$HOST_NAME" -name h2d_index.json 2>/dev/null | wc -l) 个态"
}

# ---- viewer 逐帧图：每个情景（c2d/ad_stats_<tag>.json 在的）写到 $RESULT_ROOT/$HOST_NAME/runtime/console_data/flood2d_ad_<区>_m<雨量>h<历时>g30/；
#      已有且比 ad_stats 新的跳过（重算 L2 后 ad_stats 变新，会自动重做）；FORCE=1 全重做。flood-viewer 最迟 2 分钟后自动出现 / 更新
frames_all(){
  local wh; wh=$(docker exec "$C" printenv SWBRAIN_SWMM_WORK_HOST 2>/dev/null || true)
  [ -n "$wh" ] || { echo "取不到宿主机工作目录（容器里的 SWBRAIN_SWMM_WORK_HOST），逐帧图跳过"; return 0; }
  local t reg mm h out
  for t in $TAGS; do
    docker exec "$C" test -f "/work/c2d/ad_stats_$t.json" || continue
    reg=${t%%_*}; mm=$(echo "$t" | sed -E 's/.*_M([0-9]+)H([0-9]+)/\1/'); h=$(echo "$t" | sed -E 's/.*_M([0-9]+)H([0-9]+)/\2/')
    out="$RESULT_ROOT/$HOST_NAME/runtime/console_data/flood2d_ad_$(echo "$reg" | tr 'A-Z' 'a-z')_m${mm}h${h}g30"
    if [ -z "${FORCE:-}" ] && [ -f "$out/stats.json" ] && [ ! "$wh/c2d/ad_stats_$t.json" -nt "$out/stats.json" ]; then echo "  ✓ 逐帧图已是最新 $t"; continue; fi
    echo "  逐帧图 $t"
    docker run --rm -e AD_RESULT_ROOT="$RESULT_ROOT" -e AD_HOST="$HOST_NAME" -v "$wh:/work" -v "$NAS_BASE:$NAS_BASE" anuga-swmm:1.0 python -u /work/c2d/ad_frames.py --tag "$t" --res 30 || echo "  ✗ $t 逐帧图失败"
  done
}

case "${1:-status}" in
  _driver)
    echo "==== $(date '+%F %T') 服务器 $SERVER 驱动开始：调度 → 自动转换"
    need_container; copy_files
    docker exec "${EXEC_ENV[@]}" -d "$C" sh -c "python -u tools/c1_batch.py run $RUN --slots $SLOTS --threads 4 --min-free-gb ${MIN_FREE_GB} --stagger 20 >> $LOG 2>&1"
    sleep 30
    sched_alive || { echo "✗ 调度器没跑起来："; docker exec "$C" tail -n 30 "$LOG"; exit 1; }
    while sched_alive; do sleep 60; done
    echo "==== $(date '+%F %T') 调度器已退出，进度："
    docker exec "$C" python tools/c1_batch.py status $RUN || true
    convert_all
    frames_all
    echo "==== $(date '+%F %T') 全部结束（算完 + 转换 + 逐帧图）"
    rm -f "$DRV_PID"
    ;;
  start)
    driver_alive && { echo "驱动已经在跑（pid $(cat "$DRV_PID")）：bash $0 status"; exit 0; }
    need_container
    copy_files
    echo "== 检查"; check_nas; ensure_h2d
    docker image inspect anuga-swmm:1.0 >/dev/null 2>&1 || { echo "✗ 没有 anuga-swmm:1.0"; exit 1; }
    docker exec "$C" test -f /work/Mussafah_baseline.inp || echo "! /work/Mussafah_baseline.inp 不在（雨型来源）：prep 步骤会用客户 Mussafah_00.inp 重做；做不出来会报错"
    docker exec "$C" python tools/ad_run_30m.py check-patches
    docker exec "$C" df -h /work
    echo "本机 $CORES 核；同时最多 $SLOTS 个态；每态线程：$THREADS_MAP（没列的区域 4）；新开一个态前要求可用内存 ≥ ${MIN_FREE_GB} GB"
    nohup bash "$0" _driver >> "$DRV_LOG" 2>&1 &
    echo $! > "$DRV_PID"
    sleep 5
    echo "已放后台（驱动 pid $!，日志 $DRV_LOG）。看进度：bash $0 status    跟调度日志：bash $0 log"
    ;;
  check)
    need_container; copy_files
    check_nas || true
    docker image inspect anuga-swmm:1.0 >/dev/null 2>&1 && echo "✓ 镜像 anuga-swmm:1.0" || echo "✗ 没有 anuga-swmm:1.0"
    docker image inspect "$H2D_IMAGE" >/dev/null 2>&1 && echo "✓ 转换镜像 $H2D_IMAGE" || echo "· 转换镜像 $H2D_IMAGE 还没有（start 时会用 h2d/ 离线构建）"
    docker exec "$C" python tools/ad_run_30m.py check-patches
    for t in $TAGS; do "${CEXEC[@]}" python tools/ad_run_30m.py check --tag "$t"; done
    docker exec "$C" df -h /work
    echo "本机 $CORES 核，$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo) GB 内存；同时 $SLOTS 个态；每态线程：$THREADS_MAP" ;;
  status)
    driver_alive && echo "● 驱动在跑（pid $(cat "$DRV_PID")）" || echo "○ 驱动没在跑"
    docker exec "$C" python tools/c1_batch.py status $RUN || true
    echo "-- 在跑的计算容器"; docker ps --format '{{.Names}}\t{{.Status}}' | grep '^adpipe_' || echo "   （没有）"
    echo "-- 可用内存 $(awk '/MemAvailable/{printf "%.0f GB", $2/1048576}' /proc/meminfo)；负载 $(cut -d' ' -f1-3 /proc/loadavg)"
    echo "-- NAS：已交付 $(find "$RESULT_ROOT/$HOST_NAME" -path '*/统计/ad_stats.json' 2>/dev/null | wc -l) 个情景（result），已转换 $(find "$SHP_ROOT/$HOST_NAME" -name h2d_index.json 2>/dev/null | wc -l) 个态（shp_dat）"
    [ -f "$DRV_LOG" ] && { echo "-- 驱动日志末尾"; tail -n 6 "$DRV_LOG"; } ;;
  plan)    "${CEXEC[@]}" python tools/c1_batch.py plan $RUN ;;
  log)     docker exec "$C" tail -n 50 -f "$LOG" ;;
  convert) convert_all ;;
  frames)  frames_all ;;
  *) echo "用法：bash $0 start|check|status|log|plan|convert|frames"; exit 1 ;;
esac
