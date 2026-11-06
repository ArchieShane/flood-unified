#!/usr/bin/env bash
# ADM 全市 100 m v2 · 33 / 61 / 96 mm × 1 / 2 / 24 h（9 场）· 只推演 L2（交付里统一叫 L1）· 三台服务器共用本包（2026-10-21）
# 一维网络全部来自「合并结果_20261020」（开发机已重建成 cases/ad100v2/ah_net_CITY.pkl，服务器不再读客户 GDB）；汇水区不参与；泵 / 塘 / 渗井 / 雨水口的口径见 tools/adm100v2_run.py 开头和 README.txt。
# 交付成果（路径和内容）全英文、无中文，状态叫 L1；后处理结尾会扫一遍，有中文就不写完成标记（该任务不算算完）。
#
# 固定分工（cases/ad100v2/adm100v2_all.tsv 最后一列；按主机名认服务器，认不出用 SERVER=1|2|3）：
#   服务器 1（aim01，48 核 / 188 GB）：CITY_M33H24 CITY_M61H24 CITY_M96H24（三场 24 h 雨）
#   服务器 2（uca03，32 核 / 64 GB） ：CITY_M33H1  CITY_M61H1  CITY_M96H1（三场 1 h 雨）
#   服务器 3（uca06，32 核 / 64 GB） ：CITY_M33H2  CITY_M61H2  CITY_M96H2（三场 2 h 雨）
#
# 一条龙（每场算完就接着做，start 之后不用再人工操作）：
#   建 L1 → L1 一二维耦合计算 → 后处理（积水点结果表与图层、统计、.sww、.npz、最大水深 tif、核对表）直接写 NAS → 逐项核对（缺一项都不算完成）
#
# 输出位置（脚本结束时也会打印）：
#   $NAS_BASE/result/<主机>/ADM_City_<雨量>mm<历时>h_100m_v2/L1_existing_facilities/{1D,2D}/   .inp .rpt .sww .npz 最大水深 tif 耦合运行记录
#   $NAS_BASE/result/<主机>/ADM_City_<雨量>mm<历时>h_100m_v2/{hotspots,statistics,common}/、00_README.md、calculation_settings.md
#   NAS_BASE 缺省 /media/pop-fs/flood_data
#
# 本目录里要有：adm_100m_v2_server.sh  adm100v2_run.py  adm100v2_post.py  couple2way_v3.py  c1_batch.py  cases/ad100v2/*
#
#   bash adm_100m_v2_server.sh check       # 只读检查：容器 / 镜像 / NAS 可写 / 数据 / 本机任务清单（开跑前先看这个）
#   bash adm_100m_v2_server.sh start       # 后台开跑（可重复执行：已完成的跳过，在跑的容器会被认领）
#   bash adm_100m_v2_server.sh status      # 调度进度、在跑的容器、可用内存、已核对通过的任务
#   bash adm_100m_v2_server.sh log         # 跟踪调度日志（Ctrl-C 退出，不影响计算）
#   bash adm_100m_v2_server.sh plan        # 只列任务
#   bash adm_100m_v2_server.sh finalize    # 手动补核对；TAGS="CITY_M61H2" 指定任务
#   bash adm_100m_v2_server.sh verify      # 只核对，不动文件；有缺项返回非 0
# 可选环境变量：TAGS、SERVER、SLOTS（同时在算的态，缺省 内存 GB / 90，至少 1：188 GB → 2，64 GB → 1）、THREADS（每个态的线程，缺省 核数 / SLOTS，最多 16；一维 SWMM 与耦合共用）、
#   MIN_FREE_GB（新开一个态前要求的可用内存，缺省 40）、MIN_DISK_GB（/work 剩余，缺省 100）、NAS_BASE、HOST_NAME、C=容器名（缺省 swbrain）
# 内存 / 单态耗时没有实测过（27.6 万节点整网 SWMM + 约 57 万个二维三角单元，是 250 m 的 6 倍多；250 m v2 实测 1–2 h 雨 2.6–3.8 h、24 h 雨 3 态并行约 10 h，100 m 预计 5–10 倍）：开跑后先看 docker stats 与 status，必要时用 SLOTS / THREADS 调。
# 暂停计算用 docker pause <容器>（保留进度），不要 docker stop。
set -euo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
NAS_BASE=${NAS_BASE:-/media/pop-fs/flood_data}
RESULT_ROOT=${RESULT_ROOT:-$NAS_BASE/result}
HOST_NAME=${HOST_NAME:-$(hostname -s)}
CORES=${CORES:-$(nproc)}
MEM_GB=${MEM_GB:-$(awk '/MemTotal/{printf "%d", $2/1048576}' /proc/meminfo)}
case "$HOST_NAME" in AZPDW006aim01) AUTO=1;; AZPDW006uca03) AUTO=2;; AZPDW006uca06) AUTO=3;; *) AUTO="";; esac
SERVER=${SERVER:-$AUTO}
[ -n "$SERVER" ] || { echo "主机名 $HOST_NAME 不认识：用 SERVER=1|2|3 指定本机分到哪份任务"; exit 1; }
ALL_TSV=$HERE/cases/ad100v2/adm100v2_all.tsv          # 列：标签 代号 区域目录名 网格m 面积km2 积水点数 服务器
[ -f "$ALL_TSV" ] || { echo "找不到 $ALL_TSV（把 cases/ 目录一起传上来）"; exit 1; }
TAGS=${TAGS:-$(awk -F'\t' -v s="$SERVER" '$7==s{print $1}' "$ALL_TSV" | tr '\n' ' ')}
TAGS=${TAGS% }
[ -n "$TAGS" ] || { echo "服务器 $SERVER 在任务清单里没有任务"; exit 1; }
SLOTS=${SLOTS:-$(( MEM_GB / 90 < 1 ? 1 : MEM_GB / 90 ))}
THREADS=${THREADS:-$(( CORES / SLOTS > 16 ? 16 : CORES / SLOTS ))}
MIN_FREE_GB=${MIN_FREE_GB:-40}
MIN_DISK_GB=${MIN_DISK_GB:-100}
FIN=$HERE/.fin_server$SERVER                 # 每个任务一个标记文件：.ok = 核对通过，.tried = 本次驱动已试过
mkdir -p "$FIN"
LOG=/work/c2d/logs/c1_batch_adm100v2_run.log
LOCK=/work/c2d/logs/c1_batch_adm100v2_run.lock
RUN="--runner adm100v2_run.py --tags $TAGS"
DRV_LOG=$HERE/adm_100m_v2_server$SERVER.driver.log
DRV_PID=$HERE/adm_100m_v2_server$SERVER.driver.pid
EXEC_ENV=(-e "AD_HOST=$HOST_NAME" -e "AD_RESULT_ROOT=$RESULT_ROOT" -e "AD_SWMM_THREADS=$THREADS" -e "C2D_G5_THREADS=$THREADS")
CEXEC=(docker exec "${EXEC_ENV[@]}" "$C")
SD="L1_existing_facilities"

field(){ awk -F'\t' -v t="$1" -v n="$2" '$1==t{print $n; exit}' "$ALL_TSV"; }
rain_of(){ echo "$1" | sed -E 's/^[A-Z]+_M([0-9]+)H([0-9]+)$/\1/'; }
dur_of(){ echo "$1" | sed -E 's/^[A-Z]+_M([0-9]+)H([0-9]+)$/\2/'; }
dd_of(){ echo "$(field "$1" 3)_$(rain_of "$1")mm$(dur_of "$1")h_$(field "$1" 4)m_v2"; }       # 交付目录名
find_src(){ for d in "$HERE" "$HERE/.." "$HERE/tools"; do [ -f "$d/$1" ] && { echo "$d/$1"; return; }; done; echo "找不到 $1（放在本脚本同一目录下）" >&2; exit 1; }
driver_alive(){ [ -f "$DRV_PID" ] && kill -0 "$(cat "$DRV_PID")" 2>/dev/null; }
sched_alive(){ docker exec "$C" sh -c "test -f $LOCK && kill -0 \$(cut -d' ' -f1 $LOCK) 2>/dev/null" 2>/dev/null; }
need_container(){ [ -n "${NO_CONTAINER:-}" ] && return 0; docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行：先在部署包目录 bash start.sh"; exit 1; }; }

copy_files(){
  local f
  for f in adm100v2_run.py adm100v2_post.py couple2way_v3.py c1_batch.py; do docker cp "$(find_src $f)" "$C:/app/tools/$f"; done
  docker exec "$C" mkdir -p /app/cases/ad100v2 /work/c2d/logs
  docker cp "$HERE/cases/ad100v2/." "$C:/app/cases/ad100v2/"
}
check_nas(){
  [ -d "$NAS_BASE" ] || { echo "✗ $NAS_BASE 不存在：NAS 没挂上？"; return 1; }
  mkdir -p "$RESULT_ROOT/$HOST_NAME" && touch "$RESULT_ROOT/$HOST_NAME/.w" && rm -f "$RESULT_ROOT/$HOST_NAME/.w" || { echo "✗ $RESULT_ROOT/$HOST_NAME 写不进去（权限？）"; return 1; }
  echo "✓ NAS 可写：$RESULT_ROOT/$HOST_NAME；剩余 $(df -h "$NAS_BASE" | awk 'NR==2{print $4}')"
}

# ---- 逐项核对（缺任何一项返回 1）
verify_tag(){
  local t=$1 dd D miss=0 f nm="AH_$1" L
  dd=$(dd_of "$t"); D="$RESULT_ROOT/$HOST_NAME/$dd"; L="$D/$SD"
  echo "---- 核对 $t（$dd）----"
  for f in 00_README.md calculation_settings.md common/domain.geojson common/storage_objects.json common/pumps_built.json common/pumps_not_built.csv common/data_usage_audit.csv common/hotspots.json hotspots/hotspot_results.csv hotspots/hotspot_layer.geojson statistics/run_stats.json statistics/summary.csv; do
    [ -s "$D/$f" ] || { echo "  ✗ 缺 交付/$f"; miss=$((miss+1)); }
  done
  for f in "1D/${nm}_L1.inp" "1D/${nm}_L1_1D_report.rpt" "2D/${nm}_L1.sww" "2D/${nm}_L1_max_depth.tif" "2D/L1_coupled_run_record.json" "2D/${nm}_L1_2d.npz"; do
    [ -s "$L/$f" ] || { echo "  ✗ 缺 交付/$SD/$f"; miss=$((miss+1)); }
  done
  grep -q '"missing": \[\]' "$D/statistics/run_stats.json" 2>/dev/null || { echo "  ✗ run_stats.json 里 missing 非空（后处理时有文件不在）"; miss=$((miss+1)); }
  if [ -e "$D" ] && [ -n "$(LC_ALL=C find "$D" -print 2>/dev/null | LC_ALL=C grep -P '[^\x00-\x7f]' | head -1)" ]; then echo "  ✗ 交付里有非 ASCII 的路径名（要求无中文）"; miss=$((miss+1)); fi
  if [ "$miss" -eq 0 ]; then echo "  ✓ $t：交付文件、.sww、.npz、最大水深 tif、积水点表、核对表 全部齐全，路径无中文"; return 0; fi
  echo "  ✗ $t 共缺 $miss 项"; return 1
}

finalize_tag(){
  local t=$1
  echo "== $(date '+%F %T') 核对 $t"
  if verify_tag "$t"; then touch "$FIN/$t.ok"; else rm -f "$FIN/$t.ok"; return 1; fi
}
# 每轮：有 ah_stats（后处理做完）且还没核对通过、本次驱动还没试过的任务，各核对一次；final=1 时（调度器退出后）没通过的再试一次
finalize_ready(){
  local final=${1:-0} t
  for t in $TAGS; do
    [ -f "$FIN/$t.ok" ] && continue
    docker exec "$C" test -f "/work/c2d/ah_stats_$t.json" || continue
    if [ -f "$FIN/$t.tried" ] && [ "$final" != 1 ]; then continue; fi
    [ "$final" = 1 ] && [ -f "$FIN/$t.tried2" ] && continue
    if [ -f "$FIN/$t.tried" ]; then touch "$FIN/$t.tried2"; else touch "$FIN/$t.tried"; fi
    finalize_tag "$t" || echo "✗ $t 核对有缺项（见上；补完可 bash $0 finalize）"
  done
  return 0
}

print_outputs(){
  local t n_ok=0 n_all=0
  for t in $TAGS; do n_all=$((n_all+1)); [ -f "$FIN/$t.ok" ] && n_ok=$((n_ok+1)); done
  echo "---- 输出位置 ----"
  echo "  交付目录  $RESULT_ROOT/$HOST_NAME/ADM_City_<雨量>mm<历时>h_100m_v2/（L1_existing_facilities/{1D,2D}、hotspots/、statistics/、common/、00_README.md、calculation_settings.md）"
  echo "  本批不转 shp+dat、不进 flood-viewer / flood-aio；需要时从交付目录里的 .sww 补做"
  echo "  核对通过 $n_ok / $n_all 个任务"
}

case "${1:-status}" in
  _driver)
    echo "==== $(date '+%F %T') 服务器 $SERVER 驱动开始：${TAGS}（同时 $SLOTS 个态、每态 $THREADS 线程）"
    need_container; copy_files
    docker exec "${EXEC_ENV[@]}" -d "$C" sh -c "python -u tools/c1_batch.py run $RUN --slots $SLOTS --threads $THREADS --min-free-gb ${MIN_FREE_GB} --stagger 20 >> $LOG 2>&1"
    sleep 30
    sched_alive || { echo "✗ 调度器没跑起来："; docker exec "$C" tail -n 30 "$LOG"; exit 1; }
    while sched_alive; do finalize_ready 0; sleep 120; done
    echo "==== $(date '+%F %T') 调度器已退出，进度："
    docker exec "$C" python tools/c1_batch.py status $RUN || true
    finalize_ready 1
    print_outputs
    echo "==== $(date '+%F %T') 全部结束（算完 + 后处理写 NAS + 核对）；没通过核对的任务见上面的 ✗，修好后 bash $0 finalize"
    rm -f "$DRV_PID"
    ;;
  start)
    driver_alive && { echo "驱动已经在跑（pid $(cat "$DRV_PID")）：bash $0 status"; exit 0; }
    need_container; copy_files
    echo "== 检查"; check_nas
    docker image inspect anuga-swmm:1.0 >/dev/null 2>&1 || { echo "✗ 没有 anuga-swmm:1.0"; exit 1; }
    docker exec "$C" test -f /work/Mussafah_baseline.inp || echo "! /work/Mussafah_baseline.inp 不在（雨型来源）：prep 步骤会用客户 Mussafah_00.inp 重做；做不出来会报错"
    docker exec "$C" python tools/adm100v2_run.py check-patches
    docker exec "$C" df -h /work
    FREE=$(docker exec "$C" df -BG --output=avail /work | tail -1 | tr -dc 0-9)
    if [ "${FREE:-0}" -lt "$MIN_DISK_GB" ]; then echo "✗ /work 只剩 ${FREE} GB（< ${MIN_DISK_GB} GB）：先清理 / 同步 NAS 腾空间"; exit 1; fi
    echo "服务器 $SERVER（$HOST_NAME）：$(echo "$TAGS" | wc -w) 个任务（$TAGS）；本机 $CORES 核 / ${MEM_GB} GB；同时最多 $SLOTS 个态、每态 $THREADS 线程；新开一个态前要求可用内存 ≥ ${MIN_FREE_GB} GB"
    echo "第一次会先做 prep：拷脚本与案例数据、DTM 取平均成 100 m DEM（几分钟，之后有缓存）；一维网络是开发机上从合并结果重建好的 ah_net_CITY.pkl"
    nohup bash "$0" _driver >> "$DRV_LOG" 2>&1 &
    echo $! > "$DRV_PID"
    sleep 5
    echo "已放后台（驱动 pid $!，日志 $DRV_LOG）。看进度：bash $0 status    跟调度日志：bash $0 log"
    ;;
  check)
    need_container; copy_files
    check_nas || true
    docker image inspect anuga-swmm:1.0 >/dev/null 2>&1 && echo "✓ 镜像 anuga-swmm:1.0" || echo "✗ 没有 anuga-swmm:1.0"
    docker exec "$C" python tools/adm100v2_run.py check-patches
    echo "本机任务（服务器 $SERVER）：标签 / 区域目录 / 网格 / 面积 / 积水点数"
    awk -F'\t' -v s="$SERVER" '$7==s{printf "  %-16s %-10s %3s m  %8s km²  %4s 个\n",$1,$3,$4,$5,$6}' "$ALL_TSV"
    t=${TAGS%% *}; "${CEXEC[@]}" python tools/adm100v2_run.py check --tag "$t"
    docker exec "$C" df -h /work
    echo "本机 $CORES 核，${MEM_GB} GB 内存；同时 $SLOTS 个态、每态 $THREADS 线程" ;;
  status)
    driver_alive && echo "● 驱动在跑（pid $(cat "$DRV_PID")）" || echo "○ 驱动没在跑"
    docker exec "$C" python tools/c1_batch.py status $RUN || true
    echo "-- 在跑的计算容器"; docker ps --format '{{.Names}}\t{{.Status}}' | grep '^ahpipe_' || echo "   （没有）"
    echo "-- 可用内存 $(awk '/MemAvailable/{printf "%.0f GB", $2/1048576}' /proc/meminfo)；负载 $(cut -d' ' -f1-3 /proc/loadavg)"
    n_ok=0; n_all=0; for t in $TAGS; do n_all=$((n_all+1)); [ -f "$FIN/$t.ok" ] && n_ok=$((n_ok+1)); done
    echo "-- 核对通过 $n_ok / $n_all 个任务"
    [ -f "$DRV_LOG" ] && { echo "-- 驱动日志末尾"; tail -n 8 "$DRV_LOG"; } ;;
  plan)    "${CEXEC[@]}" python tools/c1_batch.py plan $RUN ;;
  log)     docker exec "$C" tail -n 50 -f "$LOG" ;;
  finalize) need_container; check_nas; for t in $TAGS; do finalize_tag "$t" || true; done; print_outputs ;;
  verify)  rc=0; for t in $TAGS; do verify_tag "$t" || rc=1; done; exit $rc ;;
  *) echo "用法：bash $0 start|check|status|log|plan|finalize|verify"; exit 1 ;;
esac
