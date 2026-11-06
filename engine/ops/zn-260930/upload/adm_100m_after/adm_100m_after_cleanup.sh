#!/usr/bin/env bash
# ADM 全市 100 m（治理后数据，L1 / L2）批次 · 彻底停止并删除本批成果（服务器 1 上跑）
#
#   bash adm_100m_after_cleanup.sh        # 只列：会停掉什么、会删什么（目录 + 大小），不动任何东西
#   bash adm_100m_after_cleanup.sh go     # 真停、真删（不可恢复）
#
# 只动本批（标签 TAGS，缺省 CITYAFTER_M61H1），不碰以前批次的任何成果；100 m DEM 缓存（dem_AF100.tif）缺省保留，要清掉加 DROP_CACHE=1。
# 步骤（go）：1 停驱动  2 停调度器（容器里没有 pkill，用 python 扫 /proc）并删锁  3 删计算容器 afpipe_<标签>_L1/L2  4 删 /work 里本批文件
#   5 删 NAS：result/<主机>/ADM_City_61mm1h_100m_after、shp_dat/<主机>/ADM_City_61mm1h_100m_after、viewer 逐帧图目录 flood2d_ad_cityafter_m61h1g100  6 删核对标记与驱动日志
set -uo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
NAS_BASE=${NAS_BASE:-/media/pop-fs/flood_data}
RESULT_ROOT=${RESULT_ROOT:-$NAS_BASE/result}
SHP_ROOT=${SHP_ROOT:-$NAS_BASE/shp_dat}
HOST_NAME=${HOST_NAME:-$(hostname -s)}
TAGS=${TAGS:-CITYAFTER_M61H1}
GO=0; [ "${1:-}" = go ] && GO=1
LOCK=/work/c2d/logs/c1_batch_adm100a_run.lock
DRV_PID=$HERE/adm_100m_after_server.driver.pid
run(){ if [ "$GO" = 1 ]; then "$@"; fi; }
sz(){ du -sh "$1" 2>/dev/null | cut -f1; }
docker ps >/dev/null 2>&1 || { echo "docker 不可用"; exit 1; }
echo "== $HOST_NAME：本批任务 $TAGS  [$([ $GO = 1 ] && echo 真删 || echo 只列)]"

echo; echo "-- 1. 驱动"
if [ -f "$DRV_PID" ] && kill -0 "$(cat "$DRV_PID")" 2>/dev/null; then echo "  驱动 pid $(cat "$DRV_PID") 在跑 → 停"; run kill "$(cat "$DRV_PID")"; else echo "  驱动没在跑"; fi
run rm -f "$DRV_PID"

echo; echo "-- 2. 调度器 / 本批的步骤进程（容器 $C 里）"
docker exec -i "$C" python - "$GO" <<'PY'
import os, signal, sys
go = sys.argv[1] == "1"
me = os.getpid()
hit = []
for p in os.listdir("/proc"):
    if not p.isdigit() or int(p) == me:
        continue
    try:
        cmd = open(f"/proc/{p}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        continue
    if "adm100a_run.py" in cmd and "python" in cmd:
        hit.append((int(p), cmd))
for pid, cmd in hit:
    print(f"  {'kill' if go else '会停'} pid {pid}: {cmd[:110]}")
    if go:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError as e:
            print("   ", e)
if not hit:
    print("  没有调度器 / 步骤进程在跑")
PY
run docker exec "$C" rm -f "$LOCK"

echo; echo "-- 3. 计算 / 后处理容器（本批标签）"
N=0
for t in $TAGS; do
  for s in L1 L2; do
    n=afpipe_${t}_${s}
    if docker ps -a --format '{{.Names}}' | grep -qx "$n"; then echo "  容器 $n → 删"; run docker rm -f "$n" >/dev/null; N=$((N+1)); fi
  done
done
PAT=$(echo "$TAGS" | tr ' ' '|')
docker ps --no-trunc --format '{{.ID}}\t{{.Image}}\t{{.Command}}' | grep -E "(AF_|--tag )($PAT)([ _.\"]|$)" | while IFS=$'\t' read -r id img cmd; do
  echo "  容器 ${id:0:12}（$img）${cmd:0:90} → 删"; run docker rm -f "$id" >/dev/null
done
[ "$N" = 0 ] && echo "  没有命名的计算容器"

echo; echo "-- 4. 工作目录（容器 $C 里 /work）里本批的文件"
for t in $TAGS; do
  L="/work/AF_${t}_* /work/c2d/AF_${t}_* /work/c2d/twoway_AF_${t}_* /work/c2d/pilot_AF_${t}_* /work/c2d/dem_AF_${t}.tif /work/c2d/af_stats_${t}.json /work/c2d/af_inputs_${t}.json /work/c2d/anuga_out/AF_${t}_* /work/c2d/logs/AF_${t}_*.log /work/c2d/logs/af_*_${t}.log"
  n=$(docker exec "$C" sh -c "ls -d $L 2>/dev/null | wc -l")
  [ "${n:-0}" -gt 0 ] && echo "  $t：$n 项"
  run docker exec "$C" sh -c "rm -rf $L"
done
if [ -n "${DROP_CACHE:-}" ]; then echo "  DROP_CACHE：100 m DEM 缓存 → 删"; run docker exec "$C" sh -c "rm -f /work/c2d/dem_AF100.tif"; fi

echo; echo "-- 5. NAS 上本批的目录"
TOT=0
for t in $TAGS; do
  mm=$(echo "$t" | sed -E 's/.*_M([0-9]+)H([0-9]+)/\1/'); h=$(echo "$t" | sed -E 's/.*_M([0-9]+)H([0-9]+)/\2/'); reg=$(echo "${t%%_*}" | tr 'A-Z' 'a-z')
  dd="ADM_City_${mm}mm${h}h_100m_after"
  for d in "$RESULT_ROOT/$HOST_NAME/$dd" "$SHP_ROOT/$HOST_NAME/$dd" "$RESULT_ROOT/$HOST_NAME/runtime/console_data/flood2d_ad_${reg}_m${mm}h${h}g100"; do
    case "$d" in */ADM_City_*mm*h_100m_after|*/flood2d_ad_cityafter_m*h*g100) ;; *) echo "  ✗ 目录名异常，跳过：$d"; continue;; esac
    if [ -e "$d" ]; then echo "  $(sz "$d")  $d"; TOT=$((TOT+1)); run rm -rf "$d"; fi
  done
done
[ "$TOT" = 0 ] && echo "  NAS 上没有本批的目录"

echo; echo "-- 6. 核对标记 / 驱动日志"
run rm -rf "$HERE/.fin_after" "$HERE/adm_100m_after_server.driver.log"
echo "  $HERE/.fin_after  $HERE/adm_100m_after_server.driver.log"
echo
if [ "$GO" = 1 ]; then echo "完成：本批已停并删除。要重新开始：bash adm_100m_after_server.sh start（会从头建模计算）。"; else echo "（只是列出；确认无误后加 go 真执行：bash $0 go）"; fi
