#!/usr/bin/env bash
# ADM 剩余汇水区推演（客户 flood 库图层 57 条里未推演的 44 条；OLD 是 6 块互不相连的多边形，拆成 6 个任务，共 49 个任务）· 61 mm / 1 h · L1 + L2
# 二维格网：面积 < 1 km² 的 5 m，其余 30 m。三台服务器共用这一份包，按主机名取自己的任务（cases/ad/rest_all.tsv；也可 SERVER=1|2|3 指定）。
# 口径与上一批 14 个 ADM 汇水区相同（CN 82 / Tc 30 min / 曼宁 0.03 / 雨型取 Mussafah 1 h 设计雨型放大到 61 mm / 全市管网截取 / 现状泵站 + 域内已建成塘），详见 tools/ad_run_30m.py 开头。
#
# 一条龙（每个任务算完就接着做，不用等整批结束；全部 start 之后不用再人工操作）：
#   算 L1 / L2（二维 .npz）→ 后处理 → 成果写 NAS（含 .sww 与 .npz；flood-aio 登记要读 .npz）→ 逐帧图进 flood-viewer
#   → .sww 转 shp + dat（不过滤 1 cm，每个态 shp 含全部网格、dat 全域）→ 逐项核对（缺一项都不算完成）
#   最后在服务器 1 的 flood-aio 目录跑 adm_rest_publish.sh：登记成 flood-aio 仿真任务 → shp+dat 目录改名成 <任务 id> → viewer 重扫（见该脚本开头）
#
# 输出位置（脚本结束时也会打印）：
#   $NAS_BASE/result/<主机>/<区域目录>_61mm1h_<网格>m/         交付目录：积水点结果表与图层、统计、各态 一维 / 二维（.sww、.npz、最大水深 tif、耦合运行记录）、公共、计算设置.md
#   $NAS_BASE/result/<主机>/runtime/console_data/flood2d_ad_<代号小写>_m61h1g<网格>/    flood-viewer 用（逐帧 PNG、manifest / stats / ad_stats.json）
#   $NAS_BASE/shp_dat/<主机>/<区域目录>_61mm1h_<网格>m/<L1|L2>/{shp,dat,h2d_index.json}   shp + dat（转换时的名字；publish 之后会改成 shp_dat/floodh2d/<任务 id>/<L1|L2>/）
#   NAS_BASE 缺省 /media/pop-fs/flood_data
#
# 本目录里要有：adm_rest_server.sh  ad_run_30m.py  ad_regions.py  ad_post.py  ad_frames.py  c1_batch.py  cases/ad/*  h2d/{Dockerfile,hydro2d_convert.py,pyshp-*.whl}
#
#   bash adm_rest_server.sh check       # 只读检查：容器 / 镜像 / NAS 可写 / 数据 / 本机任务清单（开跑前先看这个）
#   bash adm_rest_server.sh start       # 后台开跑（可重复执行：已完成的跳过，在跑的容器会被认领）
#   bash adm_rest_server.sh status      # 调度进度、在跑的容器、可用内存、已收尾 / 已核对通过的任务
#   bash adm_rest_server.sh log         # 跟踪调度日志（Ctrl-C 退出，不影响计算）
#   bash adm_rest_server.sh plan        # 只列任务
#   bash adm_rest_server.sh finalize    # 手动补收尾（逐帧图 → 转 shp+dat → 核对）；TAGS="BAGx_M61H1" 指定任务
#   bash adm_rest_server.sh verify      # 只核对，不动文件；有缺项返回非 0
# 可选环境变量：TAGS、SERVER、SLOTS（同时在算的态，缺省 核数/8）、MIN_FREE_GB（缺省 20）、CONV_JOBS（缺省 2）、
#   H2D_ARGS（缺省 --no-csv = 不过滤 1 cm；要过滤设 H2D_ARGS="--no-csv --wet-only-shp --drop-threshold 0.01"）、FORCE=1（逐帧图 / 转换强制重做）、NAS_BASE、HOST_NAME、C=容器名（缺省 swbrain）
# 暂停计算用 docker pause <容器>（保留进度），不要 docker stop。
set -euo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
NAS_BASE=${NAS_BASE:-/media/pop-fs/flood_data}
RESULT_ROOT=${RESULT_ROOT:-$NAS_BASE/result}
SHP_ROOT=${SHP_ROOT:-$NAS_BASE/shp_dat}
HOST_NAME=${HOST_NAME:-$(hostname -s)}
H2D_IMAGE=${H2D_IMAGE:-h2d-convert:1.0}
H2D_ARGS=${H2D_ARGS:---no-csv}         # 不过滤 1 cm（用户 10-18 / 10-19 要求）
CORES=${CORES:-$(nproc)}
case "$HOST_NAME" in AZPDW006aim01) AUTO=1;; AZPDW006uca03) AUTO=2;; AZPDW006uca06) AUTO=3;; *) AUTO="";; esac
SERVER=${SERVER:-$AUTO}
[ -n "$SERVER" ] || { echo "主机名 $HOST_NAME 不认识：用 SERVER=1|2|3 指定本机分到哪份任务"; exit 1; }
ALL_TSV=$HERE/cases/ad/rest_all.tsv          # 列：标签 代号 区域目录名 网格m 面积km2 积水点数 服务器
[ -f "$ALL_TSV" ] || { echo "找不到 $ALL_TSV（把 cases/ 目录一起传上来）"; exit 1; }
TAGS=${TAGS:-$(awk -F'\t' -v s="$SERVER" '$7==s{print $1}' "$ALL_TSV" | tr '\n' ' ')}
TAGS=${TAGS% }
SLOTS=${SLOTS:-$(( CORES / 8 < 2 ? 2 : CORES / 8 ))}
MIN_FREE_GB=${MIN_FREE_GB:-20}
CONV_JOBS=${CONV_JOBS:-2}
SWMM_THREADS=${SWMM_THREADS:-8}
FIN=$HERE/.fin_server$SERVER                 # 每个任务一个标记文件：.ok = 核对通过，.tried = 本次驱动已试过
mkdir -p "$FIN"
LOG=/work/c2d/logs/c1_batch_ad_run_30m.log
LOCK=/work/c2d/logs/c1_batch_ad_run_30m.lock
RUN="--runner ad_run_30m.py --tags $TAGS"
DRV_LOG=$HERE/adm_rest_server$SERVER.driver.log
DRV_PID=$HERE/adm_rest_server$SERVER.driver.pid
EXEC_ENV=(-e "AD_HOST=$HOST_NAME" -e "AD_RESULT_ROOT=$RESULT_ROOT" -e "AD_SWMM_THREADS=$SWMM_THREADS")
CEXEC=(docker exec "${EXEC_ENV[@]}" "$C")
declare -A SD=([L1]="L1_既有管网" [L2]="L2_已建成调蓄")

field(){ awk -F'\t' -v t="$1" -v n="$2" '$1==t{print $n; exit}' "$ALL_TSV"; }
dd_of(){ echo "$(field "$1" 3)_61mm1h_$(field "$1" 4)m"; }                      # 交付目录名 = flood-aio 里的交付名
vdir_of(){ echo "$RESULT_ROOT/$HOST_NAME/runtime/console_data/flood2d_ad_$(field "$1" 2 | tr 'A-Z' 'a-z')_m61h1g$(field "$1" 4)"; }
find_src(){ for d in "$HERE" "$HERE/.." "$HERE/tools"; do [ -f "$d/$1" ] && { echo "$d/$1"; return; }; done; echo "找不到 $1（放在本脚本同一目录下）" >&2; exit 1; }
driver_alive(){ [ -f "$DRV_PID" ] && kill -0 "$(cat "$DRV_PID")" 2>/dev/null; }
sched_alive(){ docker exec "$C" sh -c "test -f $LOCK && kill -0 \$(cut -d' ' -f1 $LOCK) 2>/dev/null" 2>/dev/null; }
need_container(){ [ -n "${NO_CONTAINER:-}" ] && return 0; docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行：先在部署包目录 bash start.sh"; exit 1; }; }
work_host(){ if [ -n "${WORK_HOST:-}" ]; then echo "$WORK_HOST"; else docker exec "$C" printenv SWBRAIN_SWMM_WORK_HOST 2>/dev/null || true; fi; }       # WORK_HOST=宿主机工作目录（缺省取容器里的 SWBRAIN_SWMM_WORK_HOST）

copy_files(){
  local f
  for f in ad_run_30m.py ad_regions.py ad_post.py c1_batch.py; do docker cp "$(find_src $f)" "$C:/app/tools/$f"; done
  docker cp "$(find_src ad_frames.py)" "$C:/work/c2d/ad_frames.py"
  docker exec "$C" mkdir -p /app/cases/ad /work/c2d/logs
  docker cp "$HERE/cases/ad/." "$C:/app/cases/ad/"
}
check_nas(){
  [ -d "$NAS_BASE" ] || { echo "✗ $NAS_BASE 不存在：NAS 没挂上？"; return 1; }
  mkdir -p "$RESULT_ROOT/$HOST_NAME" "$SHP_ROOT/$HOST_NAME" && touch "$RESULT_ROOT/$HOST_NAME/.w" "$SHP_ROOT/$HOST_NAME/.w" && rm -f "$RESULT_ROOT/$HOST_NAME/.w" "$SHP_ROOT/$HOST_NAME/.w" || { echo "✗ $RESULT_ROOT/$HOST_NAME 或 $SHP_ROOT/$HOST_NAME 写不进去（权限？）"; return 1; }
  echo "✓ NAS 可写：$RESULT_ROOT/$HOST_NAME 、$SHP_ROOT/$HOST_NAME；剩余 $(df -h "$NAS_BASE" | awk 'NR==2{print $4}')"
}
ensure_h2d(){
  docker image inspect "$H2D_IMAGE" >/dev/null 2>&1 && { echo "✓ 转换镜像 $H2D_IMAGE 已有"; return 0; }
  docker image inspect anuga-swmm:1.0 >/dev/null 2>&1 || { echo "✗ 没有 anuga-swmm:1.0，造不了转换镜像"; return 1; }
  [ -f "$HERE/h2d/Dockerfile" ] || { echo "✗ 找不到 $HERE/h2d/Dockerfile（把 h2d/ 目录一起传上来）"; return 1; }
  echo "转换镜像 $H2D_IMAGE 不在，用 h2d/ 里的文件离线构建（anuga-swmm:1.0 + pyshp + hydro2d_convert.py）"
  docker build -t "$H2D_IMAGE" -f "$HERE/h2d/Dockerfile" "$HERE/h2d"
}

# ---- 1. 逐帧图（flood-viewer）：已有且比 ad_stats 新的跳过；FORCE=1 重做
frames_tag(){
  local t=$1 wh out; wh=$(work_host); out=$(vdir_of "$t")
  [ -n "$wh" ] || { echo "  ✗ 取不到宿主机工作目录（容器里的 SWBRAIN_SWMM_WORK_HOST），逐帧图做不了"; return 1; }
  if [ -z "${FORCE:-}" ] && [ -f "$out/stats.json" ] && [ ! "$wh/c2d/ad_stats_$t.json" -nt "$out/stats.json" ]; then echo "  ✓ 逐帧图已是最新 $t"; return 0; fi
  echo "  逐帧图 $t（$(field "$t" 4) m）"
  timeout "${FRAMES_TIMEOUT:-7200}" docker run --rm -e AD_RESULT_ROOT="$RESULT_ROOT" -e AD_HOST="$HOST_NAME" -v "$wh:/work" -v "$NAS_BASE:$NAS_BASE" anuga-swmm:1.0 python -u /work/c2d/ad_frames.py --tag "$t" --res "$(field "$t" 4)" || { echo "  ✗ $t 逐帧图失败"; return 1; }
}

# ---- 2. .sww → shp + dat（只转本任务的两个态；输入输出路径都由任务清单推出，不扫目录，不会碰别的交付）
conv_one(){
  local sww=$1 out=$2 log=$3
  mkdir -p "$(dirname "$log")"; rm -rf "$out.partial"
  if timeout "${CONV_TIMEOUT:-10800}" docker run --rm -e OMP_NUM_THREADS=1 -v "$NAS_BASE:$NAS_BASE" "$H2D_IMAGE" "$sww" -o "$out.partial" --name boundary --src-crs EPSG:32640 $H2D_ARGS > "$log" 2>&1; then
    rm -rf "$out"; mv "$out.partial" "$out"; echo "  ✓ 转换完成 $out"
  else
    echo "  ✗ 转换失败 $sww（日志 $log）"; return 1
  fi
}
convert_tag(){
  local t=$1 dd st sww out pids=() rc=0 p
  dd=$(dd_of "$t")
  for st in L1 L2; do
    sww="$RESULT_ROOT/$HOST_NAME/$dd/${SD[$st]}/二维/AD_${t}_${st}.sww"; out="$SHP_ROOT/$HOST_NAME/$dd/$st"
    [ -f "$sww" ] || { echo "  ✗ 找不到 $sww（后处理没把 .sww 搬到交付目录？）"; rc=1; continue; }
    if [ -z "${FORCE:-}" ] && [ -f "$out/h2d_index.json" ] && [ "$out/h2d_index.json" -nt "$sww" ]; then echo "  ✓ shp+dat 已是最新 $dd/$st"; continue; fi
    while [ "${#pids[@]}" -ge "$CONV_JOBS" ]; do wait "${pids[0]}" || rc=1; pids=("${pids[@]:1}"); done
    conv_one "$sww" "$out" "$SHP_ROOT/$HOST_NAME/_logs/${dd}_${st}.log" & pids+=($!)
  done
  for p in "${pids[@]}"; do wait "$p" || rc=1; done
  return $rc
}

# ---- 3. 逐项核对（缺任何一项返回 1）
verify_tag(){
  local t=$1 dd D S V miss=0 f st nm="AD_$1"
  dd=$(dd_of "$t"); D="$RESULT_ROOT/$HOST_NAME/$dd"; S="$SHP_ROOT/$HOST_NAME/$dd"; V=$(vdir_of "$t")
  echo "---- 核对 $t（$dd）----"
  for f in 00_说明.md 计算设置.md 公共/计算域.geojson 公共/现状泵站.json 公共/已建成塘.json 公共/积水点.json 积水点/积水点结果表.csv 积水点/积水点图层.geojson 统计/ad_stats.json 统计/汇水区汇总.csv; do
    [ -s "$D/$f" ] || { echo "  ✗ 缺 交付/$f"; miss=$((miss+1)); }
  done
  for st in L1 L2; do
    for f in "一维/${nm}_${st}.inp" "一维/${nm}_${st}_一维报告.rpt" "二维/${nm}_${st}.sww" "二维/${nm}_${st}_最大水深.tif" "二维/${st}_耦合运行记录.json" "二维/${nm}_${st}_2d.npz"; do
      [ -s "$D/${SD[$st]}/$f" ] || { echo "  ✗ 缺 交付/${SD[$st]}/$f"; miss=$((miss+1)); }
    done
    [ -f "$S/$st/h2d_index.json" ] || { echo "  ✗ 缺 shp_dat/$HOST_NAME/$dd/$st/h2d_index.json（没转完）"; miss=$((miss+1)); continue; }
    ls "$S/$st/shp/"*.shp >/dev/null 2>&1 || { echo "  ✗ $S/$st/shp 里没有 .shp"; miss=$((miss+1)); }
    ls "$S/$st/dat/"RESULTS*.dat >/dev/null 2>&1 || { echo "  ✗ $S/$st/dat 里没有 RESULTS*.dat"; miss=$((miss+1)); }
  done
  for f in manifest.json ad_stats.json stats.json L1/f000.png L2/f000.png L1/max.png L2/max.png; do
    [ -s "$V/$f" ] || { echo "  ✗ 缺 viewer 目录/$f（$V）"; miss=$((miss+1)); }
  done
  grep -q '全域|all' "$V/ad_stats.json" 2>/dev/null || { echo "  ✗ viewer 统计里没有「全域|all」口径"; miss=$((miss+1)); }
  if [ "$miss" -eq 0 ]; then echo "  ✓ $t：交付文件、npz、shp+dat（L1/L2）、viewer 目录 全部齐全"; return 0; fi
  echo "  ✗ $t 共缺 $miss 项"; return 1
}

finalize_tag(){
  local t=$1
  echo "== $(date '+%F %T') 收尾 $t"
  frames_tag "$t" || true
  convert_tag "$t" || true
  if verify_tag "$t"; then touch "$FIN/$t.ok"; else rm -f "$FIN/$t.ok"; return 1; fi
}
# 每轮：有 ad_stats（后处理做完）且还没核对通过、本次驱动还没试过的任务，各收尾一次；final=1 时（调度器退出后）没通过的再试一次
finalize_ready(){
  local final=${1:-0} t
  for t in $TAGS; do
    [ -f "$FIN/$t.ok" ] && continue
    docker exec "$C" test -f "/work/c2d/ad_stats_$t.json" || continue
    if [ -f "$FIN/$t.tried" ] && [ "$final" != 1 ]; then continue; fi
    [ "$final" = 1 ] && [ -f "$FIN/$t.tried2" ] && continue
    if [ -f "$FIN/$t.tried" ]; then touch "$FIN/$t.tried2"; else touch "$FIN/$t.tried"; fi
    finalize_tag "$t" || echo "✗ $t 收尾有缺项（见上；补完可 bash $0 finalize）"
  done
  return 0
}

print_outputs(){
  local t n_ok=0 n_all=0
  for t in $TAGS; do n_all=$((n_all+1)); [ -f "$FIN/$t.ok" ] && n_ok=$((n_ok+1)); done
  echo "---- 输出位置 ----"
  echo "  交付目录     $RESULT_ROOT/$HOST_NAME/<区域目录>_61mm1h_<网格>m/"
  echo "  shp + dat    $SHP_ROOT/$HOST_NAME/<区域目录>_61mm1h_<网格>m/<L1|L2>/{shp,dat}（不过滤 1 cm：H2D_ARGS=$H2D_ARGS）"
  echo "  flood-viewer $RESULT_ROOT/$HOST_NAME/runtime/console_data/flood2d_ad_<代号小写>_m61h1g<网格>/（viewer 最迟 2 分钟后自动出现）"
  echo "  核对通过 $n_ok / $n_all 个任务；下一步：三台都算完后，在服务器 1 的 flood-aio 目录 bash adm_rest_publish.sh go"
}

case "${1:-status}" in
  _driver)
    echo "==== $(date '+%F %T') 服务器 $SERVER 驱动开始：${TAGS}"
    need_container; copy_files; ensure_h2d
    docker exec "${EXEC_ENV[@]}" -d "$C" sh -c "python -u tools/c1_batch.py run $RUN --slots $SLOTS --threads 4 --min-free-gb ${MIN_FREE_GB} --stagger 20 >> $LOG 2>&1"
    sleep 30
    sched_alive || { echo "✗ 调度器没跑起来："; docker exec "$C" tail -n 30 "$LOG"; exit 1; }
    while sched_alive; do finalize_ready 0; sleep 120; done
    echo "==== $(date '+%F %T') 调度器已退出，进度："
    docker exec "$C" python tools/c1_batch.py status $RUN || true
    finalize_ready 1
    print_outputs
    echo "==== $(date '+%F %T') 全部结束（算完 + 逐帧图 + 转 shp+dat + 核对）；没通过核对的任务见上面的 ✗，修好后 bash $0 finalize"
    rm -f "$DRV_PID"
    ;;
  start)
    driver_alive && { echo "驱动已经在跑（pid $(cat "$DRV_PID")）：bash $0 status"; exit 0; }
    need_container; copy_files
    echo "== 检查"; check_nas; ensure_h2d
    docker image inspect anuga-swmm:1.0 >/dev/null 2>&1 || { echo "✗ 没有 anuga-swmm:1.0"; exit 1; }
    docker exec "$C" test -f /work/Mussafah_baseline.inp || echo "! /work/Mussafah_baseline.inp 不在（雨型来源）：prep 步骤会用客户 Mussafah_00.inp 重做；做不出来会报错"
    docker exec "$C" python tools/ad_run_30m.py check-patches
    docker exec "$C" df -h /work
    echo "服务器 $SERVER（$HOST_NAME）：$(echo "$TAGS" | wc -w) 个任务；本机 $CORES 核；同时最多 $SLOTS 个态；新开一个态前要求可用内存 ≥ ${MIN_FREE_GB} GB"
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
    echo "本机任务（服务器 $SERVER）：标签 / 代号 / 区域目录 / 网格 / 面积 / 积水点数"
    awk -F'\t' -v s="$SERVER" '$7==s{printf "  %-22s %-16s %-26s %3s m  %7s km²  %3s 个\n",$1,$2,$3,$4,$5,$6}' "$ALL_TSV"
    t=${TAGS%% *}; "${CEXEC[@]}" python tools/ad_run_30m.py check --tag "$t"
    docker exec "$C" df -h /work
    echo "本机 $CORES 核，$(awk '/MemTotal/{printf "%.0f", $2/1048576}' /proc/meminfo) GB 内存；同时 $SLOTS 个态" ;;
  status)
    driver_alive && echo "● 驱动在跑（pid $(cat "$DRV_PID")）" || echo "○ 驱动没在跑"
    docker exec "$C" python tools/c1_batch.py status $RUN || true
    echo "-- 在跑的计算容器"; docker ps --format '{{.Names}}\t{{.Status}}' | grep '^adpipe_' || echo "   （没有）"
    echo "-- 可用内存 $(awk '/MemAvailable/{printf "%.0f GB", $2/1048576}' /proc/meminfo)；负载 $(cut -d' ' -f1-3 /proc/loadavg)"
    n_ok=0; n_all=0; for t in $TAGS; do n_all=$((n_all+1)); [ -f "$FIN/$t.ok" ] && n_ok=$((n_ok+1)); done
    echo "-- 收尾核对通过 $n_ok / $n_all 个任务；shp_dat 里已转换 $(find "$SHP_ROOT/$HOST_NAME" -name h2d_index.json 2>/dev/null | wc -l) 个态（含以前批次的）"
    [ -f "$DRV_LOG" ] && { echo "-- 驱动日志末尾"; tail -n 8 "$DRV_LOG"; } ;;
  plan)    "${CEXEC[@]}" python tools/c1_batch.py plan $RUN ;;
  log)     docker exec "$C" tail -n 50 -f "$LOG" ;;
  finalize) need_container; ensure_h2d; check_nas; for t in $TAGS; do finalize_tag "$t" || true; done; print_outputs ;;
  verify)  rc=0; for t in $TAGS; do verify_tag "$t" || rc=1; done; exit $rc ;;
  convert) ensure_h2d; for t in $TAGS; do convert_tag "$t" || true; done ;;
  frames)  need_container; for t in $TAGS; do frames_tag "$t" || true; done ;;
  *) echo "用法：bash $0 start|check|status|log|plan|finalize|verify|convert|frames"; exit 1 ;;
esac
