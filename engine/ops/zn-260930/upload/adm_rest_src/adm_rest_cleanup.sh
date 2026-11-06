#!/usr/bin/env bash
# ADM 剩余汇水区批次 · 彻底停止并删除本批成果（每台服务器各跑一次；2026-10-19 用户要求）
#
#   bash adm_rest_cleanup.sh        # 只列：会停掉什么、会删什么（目录 + 大小），不动任何东西
#   bash adm_rest_cleanup.sh go     # 真停、真删（不可恢复）
#
# 只动「本机分到的本批任务」（cases/ad/rest_all.tsv 里本服务器的那些标签），不碰：
#   · 以前批次（ADM 14 区 / ALR / C1 / 艾因 …）的任何计算成果和 NAS 目录
#   · 公用的准备产物（雨型来源、耦合程序、/work/c2d/ad_cases 等）——重新 start 时用得上
# 步骤（go）：
#   1. 停驱动（adm_rest_server.sh 的后台收尾进程）
#   2. 停调度器：容器 swbrain 里 c1_batch.py / ad_run_30m.py 的进程（容器里没有 pkill，用 python 扫 /proc），再删调度器锁
#   3. 删本批的计算容器 adpipe_<标签>_<态>，以及正在跑的 后处理 / 逐帧图 / 转换 容器（按命令行里的标签认）
#   4. 删本机工作目录里本批的文件：/work/AD_<标签>_*.inp、/work/c2d/ 下 AD_<标签>_* twoway_ pilot_ dem_ ad_stats_ ad_inputs_ anuga_out/ logs/ 管网缓存 ad_net2_<代号>.pkl
#   5. 删 NAS 上本批的目录：result/<主机>/<区域目录>_61mm1h_<网格>m/、shp_dat/<主机>/<区域目录>_61mm1h_<网格>m/（含 _logs 里本批的日志）、
#      result/<主机>/runtime/console_data/flood2d_ad_<代号>_m61h1g<网格>/（flood-viewer 用）
#   6. 删本脚本目录里的收尾标记 .fin_server<N>/ 与驱动日志
# 还没做 adm_rest_publish.sh go 的话，flood-aio 里没有本批任务，不用处理；做过的话 flood-aio 里的任务和 shp_dat/floodh2d/<id>/ 不在本脚本范围（要删另说）。
# 可选环境变量：SERVER、C（容器名，缺省 swbrain）、NAS_BASE、HOST_NAME
set -uo pipefail
C=${C:-swbrain}
HERE=$(cd "$(dirname "$0")" && pwd)
NAS_BASE=${NAS_BASE:-/media/pop-fs/flood_data}
RESULT_ROOT=${RESULT_ROOT:-$NAS_BASE/result}
SHP_ROOT=${SHP_ROOT:-$NAS_BASE/shp_dat}
HOST_NAME=${HOST_NAME:-$(hostname -s)}
case "$HOST_NAME" in AZPDW006aim01) AUTO=1;; AZPDW006uca03) AUTO=2;; AZPDW006uca06) AUTO=3;; *) AUTO="";; esac
SERVER=${SERVER:-$AUTO}
[ -n "$SERVER" ] || { echo "主机名 $HOST_NAME 不认识：用 SERVER=1|2|3 指定"; exit 1; }
ALL_TSV=$HERE/cases/ad/rest_all.tsv
[ -f "$ALL_TSV" ] || { echo "找不到 $ALL_TSV"; exit 1; }
GO=0; [ "${1:-}" = go ] && GO=1
LOCK=/work/c2d/logs/c1_batch_ad_run_30m.lock
DRV_PID=$HERE/adm_rest_server$SERVER.driver.pid
TAGS=$(awk -F'\t' -v s="$SERVER" '$7==s{print $1}' "$ALL_TSV" | tr '\n' ' '); TAGS=${TAGS% }
PAT=$(echo "$TAGS" | tr ' ' '|')
field(){ awk -F'\t' -v t="$1" -v n="$2" '$1==t{print $n; exit}' "$ALL_TSV"; }
run(){ if [ "$GO" = 1 ]; then "$@"; fi; }
sz(){ du -sh "$1" 2>/dev/null | cut -f1; }
docker ps >/dev/null 2>&1 || { echo "docker 不可用"; exit 1; }
WH=$(docker exec "$C" printenv SWBRAIN_SWMM_WORK_HOST 2>/dev/null || true)

echo "== 服务器 $SERVER（$HOST_NAME）：本批任务 $(echo "$TAGS" | wc -w) 个  [$([ $GO = 1 ] && echo 真删 || echo 只列)]"

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
    if ("c1_batch.py" in cmd or "ad_run_30m.py" in cmd) and "python" in cmd:
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

echo; echo "-- 3. 计算 / 后处理 / 转换容器（本批标签）"
N=0
for t in $TAGS; do for s in L1 L2; do
  n=adpipe_${t}_$s
  if docker ps -a --format '{{.Names}}' | grep -qx "$n"; then echo "  容器 $n → 删"; run docker rm -f "$n" >/dev/null; N=$((N+1)); fi
done; done
docker ps --no-trunc --format '{{.ID}}\t{{.Image}}\t{{.Command}}' | grep -E "(AD_|--tag )($PAT)([ _.\"]|$)" | while IFS=$'\t' read -r id img cmd; do
  echo "  容器 ${id:0:12}（$img）${cmd:0:90} → 删"; run docker rm -f "$id" >/dev/null
done
[ "$N" = 0 ] && echo "  没有命名的计算容器"

echo; echo "-- 4. 工作目录（容器 $C 里 /work）里本批的文件"
for t in $TAGS; do
  code=$(field "$t" 2)
  n=$(docker exec "$C" sh -c "ls -d /work/AD_${t}_* /work/c2d/AD_${t}_* /work/c2d/twoway_AD_${t}_* /work/c2d/pilot_AD_${t}_* /work/c2d/dem_AD_${t}.tif /work/c2d/ad_stats_${t}.json /work/c2d/ad_inputs_${t}.json /work/c2d/anuga_out/AD_${t}_* /work/c2d/logs/ad_*_${t}.log /work/c2d/logs/ad_*_${t}_L?.log /work/c2d/logs/AD_${t}_L?.log /work/c2d/ad_net2_${code}.pkl 2>/dev/null | wc -l")
  [ "${n:-0}" -gt 0 ] && echo "  $t：$n 项"
  run docker exec "$C" sh -c "rm -rf /work/AD_${t}_* /work/c2d/AD_${t}_* /work/c2d/twoway_AD_${t}_* /work/c2d/pilot_AD_${t}_* /work/c2d/dem_AD_${t}.tif /work/c2d/ad_stats_${t}.json /work/c2d/ad_inputs_${t}.json /work/c2d/anuga_out/AD_${t}_* /work/c2d/logs/ad_*_${t}.log /work/c2d/logs/ad_*_${t}_L?.log /work/c2d/logs/AD_${t}_L?.log /work/c2d/ad_net2_${code}.pkl"
done

echo; echo "-- 5. NAS 上本批的目录"
TOT=0
for t in $TAGS; do
  dd="$(field "$t" 3)_61mm1h_$(field "$t" 4)m"; code=$(field "$t" 2 | tr 'A-Z' 'a-z'); res=$(field "$t" 4)
  for d in "$RESULT_ROOT/$HOST_NAME/$dd" "$SHP_ROOT/$HOST_NAME/$dd" "$RESULT_ROOT/$HOST_NAME/runtime/console_data/flood2d_ad_${code}_m61h1g${res}"; do
    case "$d" in */_61mm1h_*m|"$RESULT_ROOT/$HOST_NAME/"|"$SHP_ROOT/$HOST_NAME/"|"") echo "  ✗ 路径异常，跳过：$d"; continue;; esac
    if [ -e "$d" ]; then echo "  $(sz "$d")  $d"; TOT=$((TOT+1)); run rm -rf "$d"; fi
  done
  for lg in "$SHP_ROOT/$HOST_NAME/_logs/${dd}_L1.log" "$SHP_ROOT/$HOST_NAME/_logs/${dd}_L2.log"; do run rm -f "$lg"; done
done
[ "$TOT" = 0 ] && echo "  NAS 上没有本批的目录"

echo; echo "-- 6. 收尾标记 / 驱动日志"
run rm -rf "$HERE/.fin_server$SERVER" "$HERE/adm_rest_server$SERVER.driver.log"
echo "  $HERE/.fin_server$SERVER  $HERE/adm_rest_server$SERVER.driver.log"

echo
if [ "$GO" = 1 ]; then echo "完成：本机本批已停并删除。要重新开始：bash adm_rest_server.sh start（会从头建模计算）。"; else echo "（只是列出；确认无误后加 go 真执行：bash $0 go）"; fi
