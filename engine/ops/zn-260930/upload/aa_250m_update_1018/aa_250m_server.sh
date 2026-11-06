#!/usr/bin/env bash
# Al Ain（艾因）二维 250 m 网格：9 场雨 × L1 / L2 = 18 态（2026-10-15；2026-10-17 换新版客户库、14 座塘、算完自动转 shp + dat）
# 口径见 aa_run_250m.py 开头：艾因一维管网（AAM_SW_Processed.gpkg = 2026-10-17 新版客户库 AAM_SW_Data_25092026.gdb 经 aa_preprocess.py 处理，管内底按真实高程）、
# 29 座现状泵站、14 座塘（L2：GDB 1 座 + gpkg 13 座，全部放进）、计算域 = 管网节点外扩 1000 m 连成片取最大一块（以 prep 实算为准）、
# 产流统一 CN 82 / Tc 30 min、地形 = 5 m DTM 按 250 m 取块平均。
# 调度同全市 250 m：tools/c1_batch.py --runner aa_run_250m.py；文件前缀 AA_，计算容器 aapipe_*，和别的互不干扰。
#
# 成果：
#   $NAS_BASE/result/<主机名>/runtime/console_data/flood2d_aa_<情景>g250/   flood-viewer 用（算完自动补热点统计并拷过去；viewer 里区域 Al Ain 艾因 / 250 m 网格）
#   容器里 out/交付/艾因_模型版_<雨量>mm<历时>h_250m/        后处理自动做（一维 / 二维 .sww / 最大水深 tif / 对比 / 统计）；之后 bash sync_to_nas.sh start 同步到 NAS（原流程不变）
#   $NAS_BASE/shp_dat/<主机名>/艾因_模型版_<雨量>mm<历时>h_250m/<L1|L2>/{shp,dat}
#        .sww → shp + RESULTSxxx.dat（hydro2d_convert.py）。**调度器退出（全部算完）后自动转换**，也可手动 bash aa_250m_server.sh convert
#        每个态一个目录，里面有 h2d_index.json 算转完，重复执行会跳过。NAS_BASE 缺省 /media/pop-fs/flood_data
#
# 上传包放在同一个目录：aa_250m_server.sh  aa_run_250m.py  aa_post.py  aa_hotspots.py  c1_batch.py  aa_preprocess.py  aa_cases.py
#                      cases/alain/*.json（含 aa_hotspots.json）  AAM_SW_Processed.gpkg  h2d/{Dockerfile,hydro2d_convert.py,pyshp-*.whl}
# 本脚本先把它们拷进 swbrain 容器（艾因库放 /app/data/，已有则不覆盖）。
#
#   bash aa_250m_server.sh check                      # 只读检查：容器、数据、DTM、镜像、NAS 可写、转换镜像、各步骤产物
#   TAGS=ALAIN_M61H1 bash aa_250m_server.sh start     # 先试跑一场（61 mm / 1 h，L1 + L2）：看 docker stats 的内存、单态耗时；算完自动转 shp + dat
#   bash aa_250m_server.sh start                      # 9 场全排（先 61 mm / 1 h，再其余 1–2 h 雨，最后 24 h 雨）；可重复执行：已完成的跳过，在跑的容器会被认领
#   TAGS=ALAIN_M61H1 bash aa_250m_server.sh rerun-l2   # 重算 L1 + L2（L1 里排进塘的管不再是出水口）；先备份旧结果，算完自动 post、重转 shp+dat、进 viewer、交付拷 NAS、核对（命令名沿用 rerun-l2）
#   bash aa_250m_server.sh status | log | plan | convert | publish   # publish = 补热点统计 + 拷逐帧图目录到 NAS 的 console_data（flood-viewer 用；算完驱动会自动做）
# 可选环境变量：TAGS、SLOTS（缺省 3）、THREADS（缺省 8）、MIN_FREE_GB（缺省 20）、MIN_DISK_GB（缺省 100）、CONV_JOBS（缺省 2）、
#   H2D_ARGS（缺省 --no-csv = 不过滤 1 cm：shp 含全部网格、dat 全域；要过滤就设 H2D_ARGS="--no-csv --wet-only-shp --drop-threshold 0.01"）、VIEWER_URL（缺省 http://localhost:8000）、SYNC_SH（缺省 ../../sync_to_nas.sh）、
#   NAS_BASE、SHP_ROOT、HOST_NAME、C=容器名（缺省 swbrain）
# 第一次 start 会先跑 prep：读艾因库重建一维网、算计算域、把 5 m DTM 取平均成 250 m（约十几分钟，之后缓存 c2d/aa_net_AA250.pkl、dem_AA250.tif）。
# 两个态齐了自动后处理与交付。暂停计算用 docker pause <容器>（保留进度），不要 docker stop。
set -euo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
TAGS=${TAGS:-"ALAIN_M61H1"}      # 2026-10-17 用户只要 61 mm / 1 h；要全部 9 场：TAGS="ALAIN_M61H1 ALAIN_M61H2 ALAIN_M33H1 ALAIN_M33H2 ALAIN_M96H1 ALAIN_M96H2 ALAIN_M33H24 ALAIN_M61H24 ALAIN_M96H24"
RUN="--runner aa_run_250m.py --tags $TAGS"
MIN_DISK_GB=${MIN_DISK_GB:-100}
GDBNAME=AAM_SW_Processed.gpkg
NAS_BASE=${NAS_BASE:-/media/pop-fs/flood_data}
RESULT_ROOT=${RESULT_ROOT:-$NAS_BASE/result}
SHP_ROOT=${SHP_ROOT:-$NAS_BASE/shp_dat}
HOST_NAME=${HOST_NAME:-$(hostname -s)}
H2D_IMAGE=${H2D_IMAGE:-h2d-convert:1.0}
H2D_ARGS=${H2D_ARGS:---no-csv}      # 不过滤 1 cm：shp 含全部三角形、dat 全域全记录（2026-10-18 用户要求）
CONV_JOBS=${CONV_JOBS:-2}
LOG=/work/c2d/logs/c1_batch_aa_run_250m.log
LOCK=/work/c2d/logs/c1_batch_aa_run_250m.lock
DRV_LOG=$HERE/aa_250m_server.driver.log
DRV_PID=$HERE/aa_250m_server.driver.pid

need_container(){ docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行：先在部署包目录 bash start.sh"; exit 1; }; }
driver_alive(){ [ -f "$DRV_PID" ] && kill -0 "$(cat "$DRV_PID")" 2>/dev/null; }
sched_alive(){ docker exec "$C" sh -c "test -f $LOCK && kill -0 \$(cut -d' ' -f1 $LOCK) 2>/dev/null" 2>/dev/null; }

copy_files(){
  local f
  for f in aa_run_250m.py aa_post.py aa_hotspots.py aa_spill_selftest.py aa_accept.py c1_batch.py; do [ -f "$HERE/$f" ] || { echo "找不到 $HERE/$f"; exit 1; }; docker cp "$HERE/$f" "$C:/app/tools/$f"; done
  docker exec "$C" mkdir -p /app/cases/alain /app/data /work/c2d/logs
  docker cp "$HERE/cases/alain/." "$C:/app/cases/alain/"
  if docker exec "$C" test -f "/app/data/$GDBNAME"; then
    echo "= /app/data/$GDBNAME 已在容器里，不覆盖（换了新库要先 docker exec $C rm /app/data/$GDBNAME 和 /work/c2d/aa_net_AA250.pkl）"
  else
    [ -f "$HERE/$GDBNAME" ] || { echo "找不到 $HERE/$GDBNAME"; exit 1; }
    docker cp "$HERE/$GDBNAME" "$C:/app/data/$GDBNAME"
  fi
}

check_nas(){
  [ -d "$NAS_BASE" ] || { echo "✗ $NAS_BASE 不存在：NAS 没挂上？（shp + dat 要写这里）"; return 1; }
  mkdir -p "$SHP_ROOT/$HOST_NAME" && touch "$SHP_ROOT/$HOST_NAME/.w" && rm -f "$SHP_ROOT/$HOST_NAME/.w" || { echo "✗ $SHP_ROOT/$HOST_NAME 写不进去（权限？）"; return 1; }
  echo "✓ NAS 可写：$SHP_ROOT/$HOST_NAME；剩余 $(df -h "$NAS_BASE" | awk 'NR==2{print $4}')"
}
ensure_h2d(){
  docker image inspect "$H2D_IMAGE" >/dev/null 2>&1 && { echo "✓ 转换镜像 $H2D_IMAGE 已有"; return 0; }
  docker image inspect anuga-swmm:1.0 >/dev/null 2>&1 || { echo "✗ 没有 anuga-swmm:1.0，造不了转换镜像"; return 1; }
  [ -f "$HERE/h2d/Dockerfile" ] || { echo "✗ 找不到 $HERE/h2d/Dockerfile（把 h2d/ 目录一起传上来）"; return 1; }
  echo "转换镜像 $H2D_IMAGE 不在，用 h2d/ 里的文件离线构建（anuga-swmm:1.0 + pyshp + hydro2d_convert.py）"
  docker build -t "$H2D_IMAGE" -f "$HERE/h2d/Dockerfile" "$HERE/h2d"
}

# ---- .sww → shp + dat（每个态一个容器、单线程；CONV_JOBS 个并行）
# 输入：宿主机上的 <工作目录>/c2d/anuga_out/AA_ALAIN_M<雨量>H<历时>_<L1|L2>.sww（不含 _bak_*）
# 输出：$SHP_ROOT/<主机>/艾因_模型版_<雨量>mm<历时>h_250m/<L1|L2>/{shp,dat}，有 h2d_index.json 算转完
conv_one(){
  local sww=$1 out=$2 log=$3
  mkdir -p "$(dirname "$log")"; rm -rf "$out.partial"
  if docker run --rm -e OMP_NUM_THREADS=1 -v "$NAS_BASE:$NAS_BASE" -v "$(dirname "$sww"):$(dirname "$sww"):ro" "$H2D_IMAGE" "$sww" -o "$out.partial" --name boundary --src-crs EPSG:32640 $H2D_ARGS > "$log" 2>&1; then
    rm -rf "$out"; mv "$out.partial" "$out"; echo "  ✓ 转换完成 $out"
  else
    echo "  ✗ 转换失败 $sww（日志 $log）"; return 1
  fi
}
export -f conv_one
export NAS_BASE H2D_IMAGE H2D_ARGS
convert_all(){
  ensure_h2d; check_nas
  local wh; wh=$(docker exec "$C" printenv SWBRAIN_SWMM_WORK_HOST 2>/dev/null || true)
  [ -n "$wh" ] || { echo "✗ 取不到宿主机工作目录（容器里的 SWBRAIN_SWMM_WORK_HOST），没法找 .sww"; return 1; }
  local list; list=$(mktemp)
  find "$wh/c2d/anuga_out" -maxdepth 1 -name 'AA_ALAIN_M*H*_L[12].sww' -not -name '*_bak_*' -type f 2>/dev/null | sort | while IFS= read -r sww; do
    b=$(basename "$sww" .sww); st=${b##*_}; tag=${b#AA_}; tag=${tag%_*}
    mm=$(echo "$tag" | sed -E 's/ALAIN_M([0-9]+)H([0-9]+)/\1/'); h=$(echo "$tag" | sed -E 's/ALAIN_M([0-9]+)H([0-9]+)/\2/')
    out="$SHP_ROOT/$HOST_NAME/艾因_模型版_${mm}mm${h}h_250m/$st"
    [ -f "$out/h2d_index.json" ] && continue
    printf '%s\t%s\t%s\n' "$sww" "$out" "$SHP_ROOT/$HOST_NAME/_logs/AA_${tag}_${st}.log"
  done > "$list"
  local n; n=$(( $(wc -l < "$list") ))
  echo "$(date '+%F %T') 待转换 $n 个 .sww（并行 ${CONV_JOBS}；参数 $H2D_ARGS）"
  if [ "$n" -gt 0 ]; then
    tr '\t' '\n' < "$list" | xargs -d '\n' -n 3 -P "$CONV_JOBS" bash -c 'conv_one "$1" "$2" "$3"' _ || echo "有转换失败的，看 $SHP_ROOT/$HOST_NAME/_logs/"
  fi
  rm -f "$list"
  echo "$(date '+%F %T') 转换结束：艾因 shp_dat 里已有 $(find "$SHP_ROOT/$HOST_NAME" -path '*艾因_模型版_*' -name h2d_index.json 2>/dev/null | wc -l) 个态"
}

# ---- flood-viewer：补热点统计（zones）、把逐帧图目录（flood2d_aa_<情景>g250，名字里的 g250 决定 viewer 归到 250 m 网格）拷到
#      $RESULT_ROOT/<主机>/runtime/console_data/；viewer 最迟 SCAN_TTL（缺省 120 s）后自动出现，也可 curl localhost:<viewer端口>/api/rescan 立即刷新
publish_viewer(){
  local t dn dst="$RESULT_ROOT/$HOST_NAME/runtime/console_data"
  mkdir -p "$dst" || { echo "✗ $dst 建不了（NAS 没挂 / 没权限）"; return 1; }
  for t in $TAGS; do
    dn="flood2d_aa_$(echo "$t" | tr 'A-Z' 'a-z')g250"
    docker exec "$C" test -f "/app/console/data/$dn/aa_stats.json" || { echo "  · $t 还没有后处理结果（/app/console/data/$dn），跳过 viewer"; continue; }
    docker exec "$C" python tools/aa_hotspots.py --tag "$t" --dir "/app/console/data/$dn" || echo "  ✗ $t 热点统计失败（viewer 只会显示全域卡片）"
    rm -rf "$dst/$dn"; docker cp "$C:/app/console/data/$dn" "$dst/$dn" && echo "  ✓ viewer 目录 $dst/$dn"
  done
}
# ---- 把交付目录 / 统计拷到 NAS 的 result/<主机>/runtime/out/（等价于 sync_to_nas.sh 对 out/ 的那一部分，直接做，不等同步），再触发 sync_to_nas.sh 同步 work/c2d 等其余部分
nas_delivery(){
  local t=$1 mm hh dn o="$RESULT_ROOT/$HOST_NAME/runtime/out"
  mm=$(echo "$t" | sed -E 's/ALAIN_M([0-9]+)H([0-9]+)/\1/'); hh=$(echo "$t" | sed -E 's/ALAIN_M([0-9]+)H([0-9]+)/\2/')
  dn="艾因_模型版_${mm}mm${hh}h_250m"
  mkdir -p "$o/交付" || { echo "✗ $o/交付 建不了"; return 1; }
  rm -rf "$o/交付/$dn"; docker cp "$C:/app/out/交付/$dn" "$o/交付/$dn" || { echo "✗ 拷交付目录失败"; return 1; }
  for f in "aa_stats_$t.json" "aa_stats_$t.csv" "aa_inputs_$t.json"; do docker cp "$C:/app/out/$f" "$o/$f" 2>/dev/null || echo "  · /app/out/$f 不在（跳过）"; done
  echo "  ✓ 交付目录 → $o/交付/$dn"
  local sy=${SYNC_SH:-$HERE/../../sync_to_nas.sh}
  if [ -f "$sy" ]; then ( cd "$(dirname "$sy")" && bash "$sy" start ) || echo "  · sync_to_nas.sh start 没成功（可手动运行）"; else echo "  · 没找到 $sy：work/c2d 等请手动 bash sync_to_nas.sh start"; fi
  curl -s -m 15 "${VIEWER_URL:-http://localhost:8000}/api/rescan" >/dev/null 2>&1 && echo "  ✓ flood-viewer 已重新扫描" || echo "  · flood-viewer 重新扫描没成功（最迟 2 分钟后自动出现）"
}
# ---- 逐项核对 NAS 上该有的文件一个不少；缺任何一项返回 1
verify_delivery(){
  local t=$1 mm hh dn nm D S V miss=0 f st
  mm=$(echo "$t" | sed -E 's/ALAIN_M([0-9]+)H([0-9]+)/\1/'); hh=$(echo "$t" | sed -E 's/ALAIN_M([0-9]+)H([0-9]+)/\2/')
  dn="艾因_模型版_${mm}mm${hh}h_250m"; nm="AA_$t"
  D="$RESULT_ROOT/$HOST_NAME/runtime/out/交付/$dn"; S="$SHP_ROOT/$HOST_NAME/$dn"; V="$RESULT_ROOT/$HOST_NAME/runtime/console_data/flood2d_aa_$(echo "$t" | tr 'A-Z' 'a-z')g250"
  echo "---- 核对 NAS 上的成果（$t）----"
  for f in 00_说明.md 计算设置.md 公共/输入参数.json 公共/计算域.geojson 公共/现状泵站.json 公共/已建成塘.json 公共/积水点.json 公共/热点.geojson \
           积水点/积水点结果表.csv 积水点/积水点结果表.xlsx 积水点/积水点图层.geojson 统计/aa_stats.json 对比_缺口分析/统计值.csv \
           "L1_既有管网/一维/${nm}_L1.inp" "L1_既有管网/一维/${nm}_L1_一维报告.rpt" "L1_既有管网/二维/${nm}_L1.sww" "L1_既有管网/二维/${nm}_L1_最大水深.tif" "L1_既有管网/二维/L1_耦合运行记录.json" "L1_既有管网/二维/${nm}_L1_逐帧水深.json" "L1_既有管网/二维/${nm}_L1_逐帧水深.tif" \
           "L2_已建成调蓄/一维/${nm}_L2.inp" "L2_已建成调蓄/一维/${nm}_L2_一维报告.rpt" "L2_已建成调蓄/二维/${nm}_L2.sww" "L2_已建成调蓄/二维/${nm}_L2_最大水深.tif" "L2_已建成调蓄/二维/L2_耦合运行记录.json" "L2_已建成调蓄/二维/${nm}_L2_逐帧水深.json" "L2_已建成调蓄/二维/${nm}_L2_逐帧水深.tif" \
           对比_缺口分析/diff_L2_minus_L1.png; do
    [ -s "$D/$f" ] || { echo "  ✗ 缺 交付/$f"; miss=$((miss+1)); }
  done
  head -n 1 "$D/对比_缺口分析/统计值.csv" 2>/dev/null | grep -q '区域,口径,指标' || { echo "  ✗ 统计值.csv 表头不是「区域,口径,指标」"; miss=$((miss+1)); }
  for st in L1 L2; do
    [ -f "$S/$st/h2d_index.json" ] || { echo "  ✗ 缺 shp_dat/$dn/$st/h2d_index.json（没转完）"; miss=$((miss+1)); continue; }
    ls "$S/$st/shp/"*.shp >/dev/null 2>&1 || { echo "  ✗ $S/$st/shp 里没有 .shp"; miss=$((miss+1)); }
    ls "$S/$st/dat/"RESULTS*.dat >/dev/null 2>&1 || { echo "  ✗ $S/$st/dat 里没有 RESULTS*.dat"; miss=$((miss+1)); }
  done
  for f in manifest.json aa_stats.json stats.json L1/f000.png L2/f000.png L1/max.png L2/max.png diff_L2_minus_L1.png; do
    [ -s "$V/$f" ] || { echo "  ✗ 缺 viewer 目录/$f"; miss=$((miss+1)); }
  done
  if [ "$miss" -eq 0 ]; then echo "  ✓ 交付文件、统计值表头、shp+dat（L1/L2）、viewer 目录 全部齐全"; return 0; fi
  echo "  ✗ 共缺 $miss 项"; return 1
}
# ---- 一个情景的收尾：转 shp+dat（不过滤）→ 进 viewer → 交付拷到 NAS → 核对
finalize(){
  local t=$1
  convert_all || echo "✗ 转换 shp+dat 有失败的（见上）"
  publish_viewer || echo "✗ 进 flood-viewer 有失败的（见上）"
  echo "== 交付目录 → NAS"; nas_delivery "$t" || true
  verify_delivery "$t" || echo "✗✗ 有缺项，见上；补完后可重复运行 bash $0 finalize（TAGS=$t）"
}

print_outputs(){
  echo "---- 输出位置 ----"
  echo "  shp + dat      $SHP_ROOT/$HOST_NAME/艾因_模型版_<雨量>mm<历时>h_250m/<L1|L2>/{shp,dat}"
  echo "  交付目录       容器 $C 内 /app/out/交付/艾因_模型版_<雨量>mm<历时>h_250m/（bash sync_to_nas.sh start 同步到 $RESULT_ROOT/$HOST_NAME/runtime/out/）"
  echo "  积水点结果表   容器 $C 内 /app/out/交付/艾因_模型版_<雨量>mm<历时>h_250m/积水点/{积水点结果表.csv,.xlsx,积水点图层.geojson}（和交付目录一起同步到 NAS）"
  echo "  flood-viewer   $RESULT_ROOT/$HOST_NAME/runtime/console_data/flood2d_aa_<情景>g250/（区域 Al Ain 艾因，格网 250 m；viewer 的 server.py 要是 2026-10-17 之后的版本）"
}

case "${1:-status}" in
  _driver)
    echo "==== $(date '+%F %T') 驱动开始：调度 → 自动转 shp + dat"
    need_container; copy_files
    docker exec -d "$C" sh -c "python -u tools/c1_batch.py run $RUN --slots ${SLOTS:-3} --threads ${THREADS:-8} --min-free-gb ${MIN_FREE_GB:-20} >> $LOG 2>&1"
    sleep 30
    sched_alive || { echo "✗ 调度器没跑起来："; docker exec "$C" tail -n 30 "$LOG"; exit 1; }
    while sched_alive; do sleep 60; done
    echo "==== $(date '+%F %T') 调度器已退出，进度："
    docker exec "$C" python tools/c1_batch.py status $RUN || true
    for t in $TAGS; do finalize "$t"; done
    print_outputs
    echo "==== $(date '+%F %T') 全部结束（算完 + 转 shp+dat + 进 flood-viewer + 交付拷 NAS + 核对）"
    rm -f "$DRV_PID"
    ;;
  start)
    driver_alive && { echo "驱动已经在跑（pid $(cat "$DRV_PID")）：bash $0 status"; exit 0; }
    need_container; copy_files
    echo "== 检查"; check_nas; ensure_h2d
    docker exec "$C" python tools/aa_run_250m.py check-patches
    docker exec "$C" test -f /work/Mussafah_baseline.inp || echo "! /work/Mussafah_baseline.inp 不在：prep 会从客户 Mussafah_00.inp 生成"
    docker exec "$C" df -h /work
    FREE=$(docker exec "$C" df -BG --output=avail /work | tail -1 | tr -dc 0-9)
    if [ "${FREE:-0}" -lt "$MIN_DISK_GB" ]; then
      echo "✗ /work 只剩 ${FREE} GB（< ${MIN_DISK_GB} GB）：先 sync_to_nas.sh 同步 + verify，再 clean_synced.sh run 腾空间"; exit 1
    fi
    nohup bash "$0" _driver >> "$DRV_LOG" 2>&1 &
    echo $! > "$DRV_PID"
    sleep 5
    echo "已放后台（驱动 pid $!，日志 $DRV_LOG）：算完自动转 shp + dat。看进度：bash $0 status    跟调度日志：bash $0 log    内存：docker stats"
    ;;
  check)
    need_container; copy_files
    check_nas || true
    docker image inspect "$H2D_IMAGE" >/dev/null 2>&1 && echo "✓ 转换镜像 $H2D_IMAGE" || echo "· 转换镜像 $H2D_IMAGE 还没有（start 时会用 h2d/ 离线构建）"
    docker exec "$C" python tools/aa_run_250m.py check-patches
    for t in $TAGS; do docker exec "$C" python tools/aa_run_250m.py check --tag "$t"; done
    docker exec "$C" df -h /work ;;
  status)
    driver_alive && echo "● 驱动在跑（pid $(cat "$DRV_PID")）" || echo "○ 驱动没在跑"
    docker exec "$C" python tools/c1_batch.py status $RUN || true
    echo "-- 在跑的计算容器"; docker ps --format '{{.Names}}\t{{.Status}}' | grep '^aapipe_' || echo "   （没有）"
    echo "-- 可用内存 $(awk '/MemAvailable/{printf "%.0f GB", $2/1048576}' /proc/meminfo)；负载 $(cut -d' ' -f1-3 /proc/loadavg)"
    echo "-- 已转 shp + dat 的态：$(find "$SHP_ROOT/$HOST_NAME" -path '*艾因_模型版_*' -name h2d_index.json 2>/dev/null | wc -l)"
    [ -f "$DRV_LOG" ] && { echo "-- 驱动日志末尾"; tail -n 6 "$DRV_LOG"; } ;;
  plan)    docker exec "$C" python tools/c1_batch.py plan $RUN ;;
  log)     docker exec "$C" tail -n 50 -f "$LOG" ;;
  rerun-l2)
    # 重算 L1 + L2（口径：排进塘的那根管在 L1 里不是出水口、水憋在管网里；L2 接进塘）。先备份旧结果，再重建网络缓存 → build（--force）→ L1/L2 一二维 → post → 补热点 / 积水点 → 验收 → 重转 shp+dat → 进 viewer / NAS
    driver_alive && { echo "驱动还在跑（pid $(cat "$DRV_PID")），先等它结束"; exit 1; }
    need_container; copy_files; check_nas; ensure_h2d
    echo "TAGS=$TAGS：重算 L1 + L2（取第一个）；L1 里排进塘的那根管不再是出水口，所以 L1 也要重算"
    echo "== 塘接入口径自检（几秒）"; docker exec "$C" python tools/aa_spill_selftest.py || { echo "✗ 自检未通过，不开跑（把上面的输出发给 Claude）"; exit 1; }
    nohup bash "$0" _rerun_l2 >> "$DRV_LOG" 2>&1 &
    echo $! > "$DRV_PID"; sleep 3
    echo "已放后台（pid $!，日志 $DRV_LOG）；约 2 小时。看进度：bash $0 status / tail -f $DRV_LOG / bash $0 log" ;;
  _rerun_l2)
    t=${TAGS%% *}; name=AA_$t; ts=$(date +%m%d_%H%M); bak=/work/c2d/_bak_nospill_$ts
    echo "==== $(date '+%F %T') 重算 L2：$t（备份 $C:$bak）"
    need_container
    docker exec "$C" sh -c "mkdir -p $bak && cp -a /work/c2d/${name}_L1* /work/c2d/${name}_L2* /work/c2d/twoway_${name}_L1.json /work/c2d/twoway_${name}_L2.json /work/c2d/pilot_${name}_L1.json /work/c2d/pilot_${name}_L2.json /work/${name}_L1.inp /work/${name}_L2.inp $bak/ 2>/dev/null; cp -a /work/c2d/aa_net_AA250.pkl $bak/ 2>/dev/null; cp -a /app/out/交付 $bak/交付 2>/dev/null; cp -a /app/out/aa_stats_$t.* $bak/ 2>/dev/null; ls $bak | head -20"
    docker exec "$C" rm -f /work/c2d/aa_net_AA250.pkl          # 塘的接入规则变了（只有出水口落在塘上才算一维进水），网络缓存必须重建
    docker exec "$C" cp /app/cases/alain/aa_ponds.json /app/cases/alain/aa_ponds.json.in_use 2>/dev/null
    docker exec "$C" python -u tools/aa_run_250m.py run prep --tag "$t" || { echo "✗ prep 失败"; rm -f "$DRV_PID"; exit 1; }
    docker exec "$C" python -u tools/aa_run_250m.py run "build:$t" --tag "$t" --force || { echo "✗ build 失败（看上面的 SWMM 读入检查）"; rm -f "$DRV_PID"; exit 1; }
    docker exec "$C" python -u tools/aa_run_250m.py run "compute:$t:L1,L2" --tag "$t" --force || { echo "✗ L1/L2 计算失败"; rm -f "$DRV_PID"; exit 1; }
    docker exec "$C" python -u tools/aa_run_250m.py run "post:$t" --tag "$t" --force || { echo "✗ post 失败"; rm -f "$DRV_PID"; exit 1; }
    docker exec "$C" python tools/aa_hotspots.py --tag "$t" || { echo "✗ 补热点失败"; rm -f "$DRV_PID"; exit 1; }
    echo "== 验收（L2 不应比 L1 更差；交付文件齐全）"
    if ! docker exec "$C" python tools/aa_accept.py --tag "$t" --files; then
      if [ "${FORCE:-}" = 1 ]; then echo "! 验收未通过，但 FORCE=1，继续"; else
        echo "✗ 验收未通过：没有转 shp+dat、没有进 viewer；旧 L2 备份在 $C:$bak。把上面的输出发给 Claude；确要继续：FORCE=1 再运行 bash $0 convert 和 publish"; rm -f "$DRV_PID"; exit 1; fi
    fi
    mm=$(echo "$t" | sed -E 's/ALAIN_M([0-9]+)H([0-9]+)/\1/'); hh=$(echo "$t" | sed -E 's/ALAIN_M([0-9]+)H([0-9]+)/\2/')
    for st in L1 L2; do   # 旧的（过滤版 / 无堰版）shp+dat 改名备份，两个态都按不过滤重转；nofilter 目录里无堰的旧 L2 也备份，免得和新 L2 混
      for r in "$SHP_ROOT" "$NAS_BASE/shp_dat_nofilter"; do
        old="$r/$HOST_NAME/艾因_模型版_${mm}mm${hh}h_250m/$st"
        [ -d "$old" ] && [ \( "$st" = L2 -o "$r" = "$SHP_ROOT" \) ] && mv "$old" "$old.bak_$ts" && echo "旧 $st shp+dat → $old.bak_$ts"
      done
    done
    finalize "$t"
    print_outputs
    echo "==== $(date '+%F %T') L1+L2 重算结束（交付目录已拷 NAS 并触发 sync_to_nas.sh；旧结果备份在 $C:$bak）"
    rm -f "$DRV_PID" ;;
  selftest) need_container; copy_files; docker exec "$C" python tools/aa_spill_selftest.py ;;
  accept) need_container; docker exec "$C" python tools/aa_accept.py --tag "${TAGS%% *}" --files ;;
  finalize) need_container; for t in $TAGS; do finalize "$t"; done ;;
  verify) for t in $TAGS; do verify_delivery "$t"; done ;;
  convert) need_container; convert_all ;;
  publish) need_container; publish_viewer; print_outputs ;;
  *) echo "用法：bash $0 start|check|status|log|plan|convert|publish|rerun-l2|selftest|accept|finalize|verify"; exit 1 ;;
esac
