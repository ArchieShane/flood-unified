#!/usr/bin/env bash
# ADM 全市 100 m · 61 mm / 1 h · L1 + L2 · 治理后数据（Z:\flood\全市治理后数据_20261025）· 只在服务器 1 跑（2026-10-26）
# 一维网络是开发机从治理后 GDB 重建好的 cases/ad100a/af_net_CITYAFTER.pkl（服务器不读 GDB）；DTM 用服务器上的；口径见 adm100a_run.py 开头和 README.txt。
#   L1 = 既有管网 + 有流量的泵（无塘）；L2 = L1 + 塘 / 渗井
#
# 一条龙（start 之后不用再人工操作）：
#   建 L1/L2 → 两态一二维耦合计算（同时算）→ 后处理（积水点表、统计、.sww、.npz、最大水深 tif、核对表）直接写 NAS
#   → 逐项核对 → .sww 转 shp + dat（写到 shp_dat）→ 逐帧图写进 flood-viewer 的数据目录（viewer 最迟 2 分钟后自动出现）→ 总核对
#
# 输出位置（脚本结束时也会打印）：
#   交付     $NAS_BASE/result/<主机>/ADM_City_61mm1h_100m_after/{L1_existing_facilities,L2_with_ponds}/{1D,2D}、hotspots/、statistics/、common/、00_README.md、calculation_settings.md（全英文）
#   shp+dat  $NAS_BASE/shp_dat/<主机>/ADM_City_61mm1h_100m_after/{L1,L2}/{shp,dat}（和 result 同级；hydro2d_convert，H2D_ARGS 缺省 --no-csv，不过滤 1 cm）
#   viewer   $NAS_BASE/result/<主机>/runtime/console_data/flood2d_ad_cityafter_m61h1g100/（逐帧图；flood-viewer 里：区域 “ADM · Abu Dhabi 全市（治理后）” → 100 m 网格 → 61 mm / 1 h）
#   NAS_BASE 缺省 /media/pop-fs/flood_data
#
# 本目录里要有：adm_100m_after_server.sh  adm100a_run.py  adm100a_post.py  adm100a_frames.py  couple2way_v3.py  c1_batch.py  cases/ad100a/*  h2d/{Dockerfile,hydro2d_convert.py,pyshp-*.whl}
#
#   bash adm_100m_after_server.sh check       # 只读检查：容器 / 镜像 / NAS 可写 / 数据 / 任务清单（开跑前先看这个）
#   bash adm_100m_after_server.sh start       # 后台开跑（可重复执行：已完成的跳过）
#   bash adm_100m_after_server.sh status      # 调度进度、在跑的容器、可用内存、各项交付是否齐
#   bash adm_100m_after_server.sh log         # 跟踪调度日志（Ctrl-C 退出，不影响计算）
#   bash adm_100m_after_server.sh plan        # 只列任务
#   bash adm_100m_after_server.sh finalize    # 手动补：核对 → 转换 shp+dat → 逐帧图 → 总核对
#   bash adm_100m_after_server.sh convert|frames|verify   # 单做其中一步（verify 只读，有缺项返回非 0）
# 可选环境变量：TAGS、THREADS（每个态的线程，缺省 (核数-4)/2）、SWMM_THREADS（一维 SWMM 线程上限，缺省 8）、MIN_FREE_GB（缺省 40）、MIN_DISK_GB（缺省 100）、
#   H2D_ARGS、NAS_BASE、HOST_NAME、C=容器名（缺省 swbrain）、FORCE_HOST=1（不是服务器 1 也允许）
# 内存 / 耗时没有实测：100 m 全市约 57 万三角单元 + 28 万节点整网 SWMM，两态同时算；开跑后头一两小时看 docker stats 与 status，吃紧就先 docker pause 一个态。
# 暂停计算用 docker pause <容器>（保留进度），不要 docker stop；容器里没有 kill / pgrep。
set -euo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
NAS_BASE=${NAS_BASE:-/media/pop-fs/flood_data}
RESULT_ROOT=${RESULT_ROOT:-$NAS_BASE/result}
SHP_ROOT=${SHP_ROOT:-$NAS_BASE/shp_dat}
HOST_NAME=${HOST_NAME:-$(hostname -s)}
H2D_IMAGE=${H2D_IMAGE:-h2d-convert:1.0}
H2D_ARGS=${H2D_ARGS:---no-csv}
CORES=${CORES:-$(nproc)}
MEM_GB=${MEM_GB:-$(awk '/MemTotal/{printf "%d", $2/1048576}' /proc/meminfo)}
if [ "$HOST_NAME" != "AZPDW006aim01" ] && [ -z "${FORCE_HOST:-}" ]; then echo "本批只在服务器 1（AZPDW006aim01）跑，本机是 $HOST_NAME；确实要在这台跑请加 FORCE_HOST=1"; exit 1; fi
TAGS=${TAGS:-CITYAFTER_M61H1}
THREADS=${THREADS:-$(( (CORES - 4) / 2 < 1 ? 1 : (CORES - 4) / 2 ))}
SWMM_THREADS=${SWMM_THREADS:-8}
MIN_FREE_GB=${MIN_FREE_GB:-40}
MIN_DISK_GB=${MIN_DISK_GB:-100}
CONV_JOBS=${CONV_JOBS:-2}
FIN=$HERE/.fin_after
mkdir -p "$FIN"
LOG=/work/c2d/logs/c1_batch_adm100a_run.log
LOCK=/work/c2d/logs/c1_batch_adm100a_run.lock
RUN="--runner adm100a_run.py --tags $TAGS"
DRV_LOG=$HERE/adm_100m_after_server.driver.log
DRV_PID=$HERE/adm_100m_after_server.driver.pid
EXEC_ENV=(-e "AD_HOST=$HOST_NAME" -e "AD_RESULT_ROOT=$RESULT_ROOT" -e "AD_SWMM_THREADS=$SWMM_THREADS" -e "C2D_G5_THREADS=$THREADS")
CEXEC=(docker exec "${EXEC_ENV[@]}" "$C")
SD1="L1_existing_facilities"; SD2="L2_with_ponds"

rain_of(){ echo "$1" | sed -E 's/^[A-Z]+_M([0-9]+)H([0-9]+)$/\1/'; }
dur_of(){ echo "$1" | sed -E 's/^[A-Z]+_M([0-9]+)H([0-9]+)$/\2/'; }
reg_of(){ echo "${1%%_*}"; }
dd_of(){ echo "ADM_City_$(rain_of "$1")mm$(dur_of "$1")h_100m_after"; }                    # 交付目录名
vd_of(){ echo "flood2d_ad_$(reg_of "$1" | tr 'A-Z' 'a-z')_m$(rain_of "$1")h$(dur_of "$1")g100"; }   # flood-viewer 目录名
find_src(){ for d in "$HERE" "$HERE/.." "$HERE/tools"; do [ -f "$d/$1" ] && { echo "$d/$1"; return; }; done; echo "找不到 $1（放在本脚本同一目录下）" >&2; exit 1; }
driver_alive(){ [ -f "$DRV_PID" ] && kill -0 "$(cat "$DRV_PID")" 2>/dev/null; }
sched_alive(){ docker exec "$C" sh -c "test -f $LOCK && kill -0 \$(cut -d' ' -f1 $LOCK) 2>/dev/null" 2>/dev/null; }
need_container(){ docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行：先在部署包目录 bash start.sh"; exit 1; }; }

copy_files(){
  local f
  for f in adm100a_run.py adm100a_post.py couple2way_v3.py c1_batch.py; do docker cp "$(find_src $f)" "$C:/app/tools/$f"; done
  docker cp "$(find_src adm100a_frames.py)" "$C:/work/c2d/adm100a_frames.py" 2>/dev/null || { docker exec "$C" mkdir -p /work/c2d; docker cp "$(find_src adm100a_frames.py)" "$C:/work/c2d/adm100a_frames.py"; }
  docker exec "$C" mkdir -p /app/cases/ad100a /work/c2d/logs
  docker cp "$HERE/cases/ad100a/." "$C:/app/cases/ad100a/"
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

# ---- .sww → shp + dat（每个态一个容器、单线程；CONV_JOBS 个并行；有 h2d_index.json 的算转完了）
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
  local list t dd s sww out; list=$(mktemp)
  for t in $TAGS; do
    dd=$(dd_of "$t")
    for s in L1:$SD1 L2:$SD2; do
      sww="$RESULT_ROOT/$HOST_NAME/$dd/${s#*:}/2D/AF_${t}_${s%%:*}.sww"; out="$SHP_ROOT/$HOST_NAME/$dd/${s%%:*}"
      [ -s "$sww" ] || { echo "  · 还没有 $sww（该态没算完 / 后处理没做完），跳过"; continue; }
      [ -f "$out/h2d_index.json" ] && continue
      printf '%s\t%s\t%s\n' "$sww" "$out" "$SHP_ROOT/$HOST_NAME/_logs/${dd}_${s%%:*}.log" >> "$list"
    done
  done
  local n; n=$(wc -l < "$list")
  echo "$(date '+%F %T') 待转换 $n 个 .sww（并行 ${CONV_JOBS}）"
  if [ "$n" -gt 0 ]; then
    tr '\t' '\n' < "$list" | xargs -d '\n' -n 3 -P "$CONV_JOBS" bash -c 'conv_one "$1" "$2" "$3"' _ || echo "有转换失败的，看 $SHP_ROOT/$HOST_NAME/_logs/"
  fi
  rm -f "$list"
  echo "$(date '+%F %T') 转换结束：本批 shp_dat 里已有 $(find "$SHP_ROOT/$HOST_NAME/$(dd_of "${TAGS%% *}")" -name h2d_index.json 2>/dev/null | wc -l) 个态"
}

# ---- 逐帧图 → flood-viewer 数据目录（已有且比 af_stats 新的跳过；FORCE=1 全重做）
frames_all(){
  local wh; wh=$(docker exec "$C" printenv SWBRAIN_SWMM_WORK_HOST 2>/dev/null || true)
  [ -n "$wh" ] || { echo "取不到宿主机工作目录（容器里的 SWBRAIN_SWMM_WORK_HOST），逐帧图跳过"; return 1; }
  local t out rc=0
  for t in $TAGS; do
    docker exec "$C" test -f "/work/c2d/af_stats_$t.json" || { echo "  · $t 后处理还没做完，逐帧图跳过"; continue; }
    out="$RESULT_ROOT/$HOST_NAME/runtime/console_data/$(vd_of "$t")"
    if [ -z "${FORCE:-}" ] && [ -f "$out/stats.json" ] && [ ! "$wh/c2d/af_stats_$t.json" -nt "$out/stats.json" ]; then echo "  ✓ 逐帧图已是最新 $t"; continue; fi
    echo "  逐帧图 $t → $out"
    docker run --rm -e AD_RESULT_ROOT="$RESULT_ROOT" -e AD_HOST="$HOST_NAME" -v "$wh:/work" -v "$NAS_BASE:$NAS_BASE" anuga-swmm:1.0 python -u /work/c2d/adm100a_frames.py --tag "$t" --res 100 || { echo "  ✗ $t 逐帧图失败"; rc=1; }
  done
  return $rc
}

# ---- 逐项核对（缺任何一项返回 1）：交付 + shp_dat + viewer
verify_tag(){
  local t=$1 dd D miss=0 f nm="AF_$1" s SD V S
  dd=$(dd_of "$t"); D="$RESULT_ROOT/$HOST_NAME/$dd"
  echo "---- 核对 $t（$dd）----"
  for f in 00_README.md calculation_settings.md common/domain.geojson common/storage_objects.json common/pumps_built.json common/pumps_not_built.csv common/data_usage_audit.csv common/hotspots.json hotspots/hotspot_results.csv hotspots/hotspot_layer.geojson statistics/run_stats.json statistics/summary.csv; do
    [ -s "$D/$f" ] || { echo "  ✗ 缺 交付/$f"; miss=$((miss+1)); }
  done
  for s in L1:$SD1 L2:$SD2; do
    SD=${s#*:}; S=${s%%:*}
    for f in "1D/${nm}_${S}.inp" "1D/${nm}_${S}_1D_report.rpt" "2D/${nm}_${S}.sww" "2D/${nm}_${S}_max_depth.tif" "2D/${S}_coupled_run_record.json" "2D/${nm}_${S}_2d.npz"; do
      [ -s "$D/$SD/$f" ] || { echo "  ✗ 缺 交付/$SD/$f"; miss=$((miss+1)); }
    done
    [ -f "$SHP_ROOT/$HOST_NAME/$dd/$S/h2d_index.json" ] || { echo "  ✗ shp+dat 没转完：$SHP_ROOT/$HOST_NAME/$dd/$S"; miss=$((miss+1)); }
  done
  grep -q '"missing": \[\]' "$D/statistics/run_stats.json" 2>/dev/null || { echo "  ✗ run_stats.json 里 missing 非空（后处理时有文件不在）"; miss=$((miss+1)); }
  if [ -e "$D" ] && [ -n "$(LC_ALL=C find "$D" -print 2>/dev/null | LC_ALL=C grep -P '[^\x00-\x7f]' | head -1)" ]; then echo "  ✗ 交付里有非 ASCII 的路径名（要求无中文）"; miss=$((miss+1)); fi
  V="$RESULT_ROOT/$HOST_NAME/runtime/console_data/$(vd_of "$t")"
  for f in manifest.json stats.json ad_stats.json L1/f000.png L1/max.png L2/f000.png L2/max.png; do
    [ -s "$V/$f" ] || { echo "  ✗ 缺 viewer/$(vd_of "$t")/$f"; miss=$((miss+1)); }
  done
  if [ -s "$V/ad_stats.json" ]; then
    for k in '"全域|all"' '个热点|200m"' '"title"'; do grep -q "$k" "$V/ad_stats.json" || { echo "  ✗ viewer 统计文件里没有 $k"; miss=$((miss+1)); }; done
  fi
  if [ "$miss" -eq 0 ]; then echo "  ✓ $t：交付文件、.sww、.npz、最大水深 tif、积水点表、核对表、shp+dat（L1/L2）、flood-viewer 逐帧图 全部齐全，交付路径无中文"; return 0; fi
  echo "  ✗ $t 共缺 $miss 项"; return 1
}

finalize_all(){
  echo "== $(date '+%F %T') 核对交付 → 转 shp+dat → 逐帧图 → 总核对"
  convert_all || true
  frames_all || true
  local t rc=0
  for t in $TAGS; do if verify_tag "$t"; then touch "$FIN/$t.ok"; else rm -f "$FIN/$t.ok"; rc=1; fi; done
  print_outputs
  return $rc
}

print_outputs(){
  local t dd
  echo "---- 输出位置 ----"
  for t in $TAGS; do
    dd=$(dd_of "$t")
    echo "  交付     $RESULT_ROOT/$HOST_NAME/$dd/（$SD1、$SD2 各含 1D / 2D；hotspots/、statistics/、common/、00_README.md、calculation_settings.md）"
    echo "  shp+dat  $SHP_ROOT/$HOST_NAME/$dd/{L1,L2}/{shp,dat}"
    echo "  viewer   $RESULT_ROOT/$HOST_NAME/runtime/console_data/$(vd_of "$t")/（flood-viewer：ADM · Abu Dhabi 全市（治理后）→ 100 m → 61 mm / 1 h；最迟 2 分钟后自动出现，要马上看请 GET /api/rescan）"
  done
}

case "${1:-status}" in
  _driver)
    echo "==== $(date '+%F %T') 驱动开始：$TAGS（两态同时算、每态 $THREADS 线程）"
    need_container; copy_files
    docker exec "${EXEC_ENV[@]}" -d "$C" sh -c "python -u tools/c1_batch.py run $RUN --slots 1 --threads $THREADS --min-free-gb ${MIN_FREE_GB} --stagger 20 >> $LOG 2>&1"
    sleep 30
    sched_alive || { echo "✗ 调度器没跑起来："; docker exec "$C" tail -n 30 "$LOG"; exit 1; }
    while sched_alive; do sleep 120; done
    echo "==== $(date '+%F %T') 调度器已退出，进度："
    docker exec "$C" python tools/c1_batch.py status $RUN || true
    finalize_all || echo "✗ 有缺项（见上；补完可 bash $0 finalize）"
    echo "==== $(date '+%F %T') 全部结束"
    rm -f "$DRV_PID"
    ;;
  start)
    driver_alive && { echo "驱动已经在跑（pid $(cat "$DRV_PID")）：bash $0 status"; exit 0; }
    need_container; copy_files
    echo "== 检查"; check_nas; ensure_h2d
    docker image inspect anuga-swmm:1.0 >/dev/null 2>&1 || { echo "✗ 没有 anuga-swmm:1.0"; exit 1; }
    docker exec "$C" test -f /work/Mussafah_baseline.inp || echo "! /work/Mussafah_baseline.inp 不在（雨型来源）：prep 步骤会用客户 Mussafah_00.inp 重做；做不出来会报错"
    docker exec "$C" python tools/adm100a_run.py check-patches
    docker exec "$C" df -h /work
    FREE=$(docker exec "$C" df -BG --output=avail /work | tail -1 | tr -dc 0-9)
    if [ "${FREE:-0}" -lt "$MIN_DISK_GB" ]; then echo "✗ /work 只剩 ${FREE} GB（< ${MIN_DISK_GB} GB）：先清理 / 同步 NAS 腾空间"; exit 1; fi
    echo "服务器 1（$HOST_NAME）：任务 $TAGS；$CORES 核 / ${MEM_GB} GB；L1 / L2 同时算，每态 $THREADS 线程（一维 SWMM 最多 $SWMM_THREADS）；新开一个态前要求可用内存 ≥ ${MIN_FREE_GB} GB"
    echo "第一次会先做 prep：拷脚本与案例数据、服务器 DTM 取平均成 100 m DEM（几分钟，之后有缓存）"
    nohup bash "$0" _driver >> "$DRV_LOG" 2>&1 &
    echo $! > "$DRV_PID"
    sleep 5
    echo "已放后台（驱动 pid $!，日志 $DRV_LOG）。看进度：bash $0 status    跟调度日志：bash $0 log"
    print_outputs
    ;;
  check)
    need_container; copy_files
    check_nas || true
    docker image inspect anuga-swmm:1.0 >/dev/null 2>&1 && echo "✓ 镜像 anuga-swmm:1.0" || echo "✗ 没有 anuga-swmm:1.0"
    docker image inspect "$H2D_IMAGE" >/dev/null 2>&1 && echo "✓ 转换镜像 $H2D_IMAGE" || echo "· 转换镜像 $H2D_IMAGE 还没有（start 时会用 h2d/ 离线构建）"
    docker exec "$C" python tools/adm100a_run.py check-patches
    t=${TAGS%% *}; "${CEXEC[@]}" python tools/adm100a_run.py check --tag "$t"
    docker exec "$C" df -h /work
    echo "本机 $CORES 核，${MEM_GB} GB 内存；L1 / L2 同时算，每态 $THREADS 线程"
    print_outputs ;;
  status)
    driver_alive && echo "● 驱动在跑（pid $(cat "$DRV_PID")）" || echo "○ 驱动没在跑"
    docker exec "$C" python tools/c1_batch.py status $RUN || true
    echo "-- 在跑的计算容器"; docker ps --format '{{.Names}}\t{{.Status}}' | grep '^afpipe_' || echo "   （没有）"
    echo "-- 可用内存 $(awk '/MemAvailable/{printf "%.0f GB", $2/1048576}' /proc/meminfo)；负载 $(cut -d' ' -f1-3 /proc/loadavg)"
    for t in $TAGS; do [ -f "$FIN/$t.ok" ] && echo "-- $t：全部核对通过" || echo "-- $t：还没核对通过（bash $0 verify 看缺什么）"; done
    [ -f "$DRV_LOG" ] && { echo "-- 驱动日志末尾"; tail -n 8 "$DRV_LOG"; } ;;
  plan)    "${CEXEC[@]}" python tools/c1_batch.py plan $RUN ;;
  log)     docker exec "$C" tail -n 50 -f "$LOG" ;;
  convert) need_container; check_nas; convert_all ;;
  frames)  need_container; frames_all ;;
  finalize) need_container; check_nas; finalize_all ;;
  verify)  rc=0; for t in $TAGS; do verify_tag "$t" || rc=1; done; exit $rc ;;
  *) echo "用法：bash $0 start|check|status|log|plan|finalize|convert|frames|verify"; exit 1 ;;
esac
