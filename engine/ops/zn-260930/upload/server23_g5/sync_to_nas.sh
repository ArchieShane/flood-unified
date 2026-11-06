#!/usr/bin/env bash
# 把本机的一二维成果数据同步到 NAS（每台计算服务器各跑一次，可重复执行，只传新增/变化的文件）。
# 成果分三处（见 docker/deploy/docker-compose.yml 的挂载），路径从 swbrain 容器的挂载读，不用手填：
#   /work/c2d            → <DEST>/work/c2d/            原始结果：*_2d.npz、.sww、twoway_*.json、*_2way.rpt、tif/、logs/
#   /app/out             → <DEST>/runtime/out/         c1_stats_<tag>.json、各态输入、交付/
#   /app/console/data    → <DEST>/runtime/console_data/ 网页用：flood2d_c1_<tag>/、c1_hs_series.json、c1_hyeto.json 等
# DEST 缺省 = /media/pop-fs/flood_data/result/<主机名>：每台机器一个子目录，互不覆盖。
# 不合并成一个目录的原因：console_data 里的 c1_hs_series.json、c1_hyeto.json 是"本机所有情景"一个文件，合并会互相覆盖；
# 汇总展示时在展示机上用这三份重新生成（要读 npz，所以 npz 必须同步）。
#
#   bash sync_to_nas.sh check     # 只读：源路径、各目录体量、NAS 剩余空间、试运行要传多少
#   bash sync_to_nas.sh start     # 后台开始同步（断了重跑即续传）
#   bash sync_to_nas.sh status    # 是否在跑、日志末尾、NAS 上已有多少
#   bash sync_to_nas.sh verify    # 逐文件比内容（校验和，慢），列出不一致的文件
# 可选环境变量：NAS（缺省 /media/pop-fs/flood_data/result）、NAME（子目录名，缺省主机名）、C（容器名，缺省 swbrain）、
#               BAK=1（连 _bak_* 备份目录一起传；缺省不传）
# 只复制，不删除本机任何文件；本机清理要等 verify 通过后人工做。
set -euo pipefail
C=${C:-swbrain}
NAS=${NAS:-/media/pop-fs/flood_data/result}
NAME=${NAME:-$(hostname -s)}
DEST="$NAS/$NAME"
LOG="$DEST/_sync.log"
PID="$DEST/_sync.pid"
PROG="$DEST/_sync.progress"

command -v rsync >/dev/null || { echo "本机没有 rsync：先装（apt install rsync / yum install rsync）"; exit 1; }
docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行（要从它读挂载路径）"; exit 1; }
mount_src(){ docker inspect "$C" --format '{{range .Mounts}}{{.Destination}}={{.Source}}{{"\n"}}{{end}}' | sed -n "s#^$1=##p"; }
WORK=$(mount_src /work); OUT=$(mount_src /app/out); CD=$(mount_src /app/console/data)
[ -n "$WORK" ] && [ -n "$OUT" ] && [ -n "$CD" ] || { echo "读不到 $C 的挂载（/work、/app/out、/app/console/data）"; exit 1; }
# 源 → 目标（源末尾的 / 表示同步目录里的内容）
PAIRS=("$WORK/c2d/|$DEST/work/c2d/" "$OUT/|$DEST/runtime/out/" "$CD/|$DEST/runtime/console_data/")
# out/cache 是可重算的缓存，out/state 是数据库快照（不是推演成果）
EXCL=(--exclude '/cache/' --exclude '/state/')
[ "${BAK:-0}" = 1 ] || EXCL+=(--exclude '_bak_*')
# NAS 常是 CIFS/NFS：不保留属主/权限，时间戳允许 2 s 误差，传一半的文件留着下次续传
RS=(rsync -rt --no-perms --no-owner --no-group --modify-window=2 --partial "${EXCL[@]}")

running(){ [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }

case "${1:-status}" in
  check)
    [ -d "$NAS" ] || { echo "✗ $NAS 不存在：NAS 没挂上？"; exit 1; }
    mkdir -p "$DEST" && touch "$DEST/.w" && rm -f "$DEST/.w" || { echo "✗ $DEST 写不进去（权限？试 sudo）"; exit 1; }
    echo "本机 $NAME，镜像 $(docker inspect "$C" --format '{{.Config.Image}}')，目标 $DEST"
    for p in "${PAIRS[@]}"; do echo "  ${p%%|*}  →  ${p##*|}"; done
    echo; echo "== 源目录体量（含不传的 cache/state/_bak_*）"; du -sh "$WORK/c2d" "$OUT" "$CD" 2>/dev/null || true
    ls -d "$WORK"/_bak_* "$OUT"/_bak_* 2>/dev/null | sed 's/^/  备份目录（缺省不传，BAK=1 才传）：/' || true
    echo; echo "== NAS 剩余空间"; df -h "$NAS" | tail -1
    echo; echo "== 试运行：本次要传的量"
    for p in "${PAIRS[@]}"; do
      echo "-- ${p%%|*}"; "${RS[@]}" -n --stats "${p%%|*}" "${p##*|}" 2>&1 | grep -E 'Number of regular files transferred|Total transferred file size' || true
    done
    ;;
  start)
    running && { echo "已经在跑（pid $(cat "$PID")）：bash $0 status"; exit 0; }
    mkdir -p "$DEST/work/c2d" "$DEST/runtime/out" "$DEST/runtime/console_data"
    nohup bash "$0" _run >> "$LOG" 2>&1 &
    echo $! > "$PID"
    echo "已放后台（pid $!），日志 $LOG。看进度：bash $0 status"
    ;;
  _run)
    echo "==== $(date '+%F %T') 开始  $NAME → $DEST"
    i=0
    for p in "${PAIRS[@]}"; do
      i=$((i + 1)); echo "-- $(date '+%T') 第 $i/${#PAIRS[@]} 部分 ${p%%|*}"
      # 实时进度写 _sync.progress（status 读最后一行）；--no-inc-recursive 先扫完整棵树，百分比才准
      "${RS[@]}" --info=progress2 --no-inc-recursive --stats "${p%%|*}" "${p##*|}" > "$PROG"
      grep -E 'Number of regular files transferred|Total transferred file size' "$PROG"
    done
    rm -f "$PROG"
    { echo "主机：$NAME"; echo "镜像：$(docker inspect "$C" --format '{{.Config.Image}}')"; echo "同步完成：$(date '+%F %T')"
      echo "源：WORK=$WORK  OUT=$OUT  CONSOLE_DATA=$CD"
      echo "已有结果的情景/态："; ls "$WORK/c2d" | sed -n 's/^twoway_\(.*\)\.json$/  \1/p'
      echo "已有后处理："; ls "$OUT" | sed -n 's/^c1_stats_\(.*\)\.json$/  \1/p'
      echo "交付目录："; ls "$OUT/交付" 2>/dev/null | sed 's/^/  /'
    } > "$DEST/MANIFEST.txt"
    echo "==== $(date '+%F %T') 完成；清单 $DEST/MANIFEST.txt"
    rm -f "$PID"
    ;;
  status)
    if running; then echo "● 在跑（pid $(cat "$PID")）"; else echo "○ 没在跑"; fi
    [ -f "$LOG" ] && { echo "-- 日志末尾"; tail -n 15 "$LOG"; }
    if running && [ -f "$PROG" ]; then
      # progress2 一行：已传字节  百分比  速度  剩余时间（本部分）；刚开始几分钟在扫文件，还没有百分比
      last=$(tr '\r' '\n' < "$PROG" | grep -E '[0-9]+%' | tail -1)
      echo "-- 当前部分进度（已传 / 百分比 / 速度 / 预计剩余）"; echo "   ${last:-正在扫描文件列表…}"
    fi
    echo "-- NAS 上已有"; du -sh "$DEST/work/c2d" "$DEST/runtime/out" "$DEST/runtime/console_data" 2>/dev/null || true
    ;;
  verify)
    running && { echo "还在同步，等完成再校验"; exit 1; }
    bad=0
    for p in "${PAIRS[@]}"; do
      echo "-- 校验 ${p%%|*}"
      d=$("${RS[@]}" -n -c --itemize-changes "${p%%|*}" "${p##*|}" | grep -E '^[<>c]f' || true)
      if [ -n "$d" ]; then echo "$d" | head -50; bad=$((bad + $(echo "$d" | wc -l))); else echo "   一致"; fi
    done
    [ $bad -eq 0 ] && echo "✓ 全部一致（本机文件可以按需清理）" || { echo "✗ $bad 个文件不一致或没传：再跑 bash $0 start"; exit 1; }
    ;;
  *) echo "用法：bash $0 check|start|status|verify"; exit 1 ;;
esac
