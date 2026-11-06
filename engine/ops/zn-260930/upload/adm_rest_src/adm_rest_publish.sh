#!/usr/bin/env bash
# ADM 剩余汇水区 · 发布（在服务器 1 上跑：flood-aio 容器只在这台，登记只能在这里做）
# 把三台服务器算好、已转好 shp+dat 的任务：登记成 flood-aio 仿真任务 → shp+dat 目录改名成 <任务 id> → flood-viewer 重扫 → 出「id ↔ 目录」对照表。
#
# 前提：三台各自的 adm_rest_server.sh 已经跑完（核对通过），成果都在 NAS 上。没算完 / 没转完的任务这里只报告、跳过，下次再跑补上（可重复执行）。
#
#   bash adm_rest_publish.sh            # = check：只读，列出每个任务现在卡在哪一步
#   bash adm_rest_publish.sh go         # 真做：补丁 → 登记 → shp_dat 改名成 id → 重启 flood-aio（有覆盖更新时）→ viewer 重扫 → 对照表
#   bash adm_rest_publish.sh verify     # 只核对已发布的：库里有任务、shp_dat/floodh2d/<id>/{L1,L2} 齐、viewer 目录里有；有缺项返回非 0
#   TAGS="RDH_M61H1 BAG_M61H1" bash adm_rest_publish.sh go     # 只发布指定任务
#
# 做什么、放哪：
#   登记        ./docker-import.sh import <主机>/<区域目录>_61mm1h_<网格>m --by ZN（交付名 = <区域目录>_61mm1h_<网格>m；已登记且 NAS 上没有更新 → 跳过；有更新 → 覆盖原任务，id 不变）
#   shp+dat     $SHP_ROOT/<主机>/<交付名>/ → $SHP_ROOT/floodh2d/<任务 id>/（里面 L1/ L2/，各有 shp/ dat/ h2d_index.json）；已存在同 id 目录先挪到 floodh2d/_bak/
#   对照表      $OUT_CSV（id、范围、雨量、网格、状态、交付名、shp+dat 目录）
# 环境变量：AIO_DIR（flood-aio 部署目录，缺省 /mnt/AI/flood/flood-aio-v4，要有 docker-import.sh）、AIO（容器名 flood-aio）、BY（缺省 ZN）、RESULT、SHP_ROOT、VIEWER_URL（缺省 http://localhost:8000）、NO_RESTART=1
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
AIO_DIR=${AIO_DIR:-/mnt/AI/flood/flood-aio-v4}
AIO=${AIO:-flood-aio}
BY=${BY:-ZN}
NAS_BASE=${NAS_BASE:-/media/pop-fs/flood_data}
RESULT=${RESULT:-$NAS_BASE/result}
SHP_ROOT=${SHP_ROOT:-$NAS_BASE/shp_dat}
ID_ROOT=${ID_ROOT:-$SHP_ROOT/floodh2d}
VIEWER_URL=${VIEWER_URL:-http://localhost:8000}
TSV=$HERE/cases/ad/rest_all.tsv            # 列：标签 代号 区域目录名 网格m 面积km2 积水点数 服务器
OUT_CSV=${OUT_CSV:-$AIO_DIR/adm_rest_id_map.csv}
LOGDIR=$AIO_DIR/adm_rest_logs
CMD=${1:-check}
[ -f "$TSV" ] || { echo "找不到 $TSV（把 cases/ 目录和本脚本放一起）"; exit 1; }
[ -f "$AIO_DIR/docker-import.sh" ] || { echo "找不到 $AIO_DIR/docker-import.sh：用 AIO_DIR=<flood-aio 部署目录> 指定"; exit 1; }
docker ps --format '{{.Names}}' | grep -qx "$AIO" || { echo "容器 $AIO 没在运行"; exit 1; }
cd "$AIO_DIR"; mkdir -p "$LOGDIR"
DATA_DIR=${DATA_DIR:-$AIO_DIR/data}
TAGS=${TAGS:-$(cut -f1 "$TSV" | tr '\n' ' ')}
host_of(){ case "$1" in 1) echo AZPDW006aim01;; 2) echo AZPDW006uca03;; 3) echo AZPDW006uca06;; esac; }
field(){ awk -F'\t' -v t="$1" -v n="$2" '$1==t{print $n; exit}' "$TSV"; }
psql_(){ docker exec "$AIO" psql -U postgres -d syq-pipe -Atc "$1"; }
id_of(){ psql_ "select id from simulation.run where output_dir='processed/sim_deliveries/$1' order by id desc limit 1" 2>/dev/null | head -1; }
newest(){ find "$@" -printf '%T@\n' 2>/dev/null | sort -n | tail -1 | cut -d. -f1; }

# ---- 容器里的补丁：登记 AD 交付要 sim_c1 里的 _stats_rows_ad（读 积水点/、统计/）；没有就把包里的三个文件拷进去（重建容器会丢，镜像 v5 以后已内置）
ensure_patch(){
  if docker exec "$AIO" grep -q "_stats_rows_ad" /app/src/stormwater/sim_c1.py 2>/dev/null; then echo "✓ 容器里 sim_c1.py 已有 AD 交付的读取分支"; return 0; fi
  [ -f "$HERE/aio_patch/sim_c1.py" ] || { echo "✗ 容器里 sim_c1.py 太旧，包里又没有 aio_patch/（登记会缺热点 / 统计）"; return 1; }
  echo "容器里 sim_c1.py 太旧：把 aio_patch/ 的 sim_c1.py sim_probe.py sim_ponding.py 拷进 $AIO"
  local f; for f in sim_c1.py sim_probe.py sim_ponding.py; do docker cp "$HERE/aio_patch/$f" "$AIO:/app/src/stormwater/$f" || return 1; done
  PATCHED=1
}

# ---- 一个任务现在到哪一步：delivery（交付齐）→ shp（shp+dat 转完）→ reg（登记）→ pub（shp_dat 已改名成 id）
state_of(){
  local t=$1 host dd D id
  host=$(host_of "$(field "$t" 7)"); dd="$(field "$t" 3)_61mm1h_$(field "$t" 4)m"; D="$RESULT/$host/$dd"
  ST_HOST=$host; ST_DD=$dd; ST_ID=$(id_of "$dd")
  ST_DELIV=0; ST_SHP=0; ST_PUB=0
  if [ -s "$D/统计/ad_stats.json" ] && [ -s "$D/L1_既有管网/二维/AD_${t}_L1_2d.npz" ] && [ -s "$D/L2_已建成调蓄/二维/AD_${t}_L2_2d.npz" ]; then ST_DELIV=1; fi
  if [ -f "$SHP_ROOT/$host/$dd/L1/h2d_index.json" ] && [ -f "$SHP_ROOT/$host/$dd/L2/h2d_index.json" ]; then ST_SHP=1; fi
  if [ -n "$ST_ID" ] && [ -f "$ID_ROOT/$ST_ID/L1/h2d_index.json" ] && [ -f "$ID_ROOT/$ST_ID/L2/h2d_index.json" ]; then ST_PUB=1; fi
}
needs_import(){      # 没登记，或 NAS 上的运行记录比上次抽取的新（重算过）
  local t=$1 ex nw
  [ -z "$ST_ID" ] && return 0
  nw=$(newest "$RESULT/$ST_HOST/$ST_DD/统计/ad_stats.json"); ex=$(newest "$DATA_DIR/processed/sim_deliveries/$ST_DD" -name delivery_summary.json)
  [ -z "$ex" ] && return 0
  [ "${nw:-0}" -gt $((ex + 60)) ]
}

if [ "$CMD" = check ]; then
  n=0; declare -A C=()
  printf '%-22s %-12s %-6s %s\n' 标签 主机 id 状态
  for t in $TAGS; do
    state_of "$t"
    if [ "$ST_PUB" = 1 ] && ! needs_import "$t" && [ "$ST_SHP" = 0 ]; then s="已发布（id $ST_ID）"
    elif [ "$ST_DELIV" = 0 ]; then s="未算完（NAS 上交付不全：缺 ad_stats.json / 两个态的 npz）"
    elif [ "$ST_SHP" = 0 ] && [ "$ST_PUB" = 0 ]; then s="shp+dat 没转完（服务器 $(field "$t" 7) 上 bash adm_rest_server.sh finalize）"
    elif needs_import "$t"; then s="待登记" ; [ -n "$ST_ID" ] && s="待覆盖更新（NAS 比库新）"
    elif [ "$ST_PUB" = 0 ]; then s="已登记 #$ST_ID，待改名 shp_dat"
    else s="已发布（id $ST_ID；shp_dat 里还有新转的一份，go 会替换）"; fi
    C[$s]=$(( ${C[$s]:-0} + 1 ))
    printf '%-22s %-12s %-6s %s\n' "$t" "${ST_HOST#AZPDW006}" "${ST_ID:--}" "$s"
  done
  echo; echo "汇总："; for k in "${!C[@]}"; do echo "  ${C[$k]} 个  $k"; done
  echo; echo "真做用：bash $0 go"
  exit 0
fi

if [ "$CMD" = verify ]; then
  bad=0; cat=$(curl -s -m 20 "$VIEWER_URL/api/catalog" 2>/dev/null || true)
  for t in $TAGS; do
    state_of "$t"
    [ -n "$ST_ID" ] || { echo "✗ $t：库里没有任务（$ST_DD）"; bad=$((bad+1)); continue; }
    for st in L1 L2; do
      [ -f "$ID_ROOT/$ST_ID/$st/h2d_index.json" ] && ls "$ID_ROOT/$ST_ID/$st/shp/"*.shp >/dev/null 2>&1 && ls "$ID_ROOT/$ST_ID/$st/dat/"RESULTS*.dat >/dev/null 2>&1 || { echo "✗ $t（id $ST_ID）：$ID_ROOT/$ST_ID/$st 的 shp / dat 不全"; bad=$((bad+1)); }
    done
    code=ad_$(field "$t" 2 | tr 'A-Z' 'a-z')
    [ -z "$cat" ] || grep -q "\"$code\"" <<<"$cat" || { echo "✗ $t：viewer 目录里没有区域 $code（还没刷新？viewer 的 server.py 是否更新过）"; bad=$((bad+1)); }
  done
  [ "$bad" = 0 ] && echo "✓ 核对通过：库、shp+dat（floodh2d/<id>/L1|L2）、viewer 都齐" || echo "✗ 共 $bad 项缺"
  exit $((bad > 0))
fi

[ "$CMD" = go ] || { echo "用法：bash $0 check|go|verify"; exit 1; }
PATCHED=0; UPDATED=0; OK=0; FAIL=(); SKIP=()
ensure_patch || exit 1
mkdir -p "$ID_ROOT" || { echo "✗ $ID_ROOT 建不了"; exit 1; }
for t in $TAGS; do
  state_of "$t"
  echo "=== $t（$ST_HOST / $ST_DD）$(date '+%F %T')"
  if [ "$ST_DELIV" = 0 ]; then echo "  · 交付不全（还没算完？），跳过"; SKIP+=("$t"); continue; fi
  if [ "$ST_SHP" = 0 ] && [ "$ST_PUB" = 0 ]; then echo "  · shp+dat 没转完，跳过（服务器上 bash adm_rest_server.sh finalize）"; SKIP+=("$t"); continue; fi
  if needs_import "$t"; then
    [ -n "$ST_ID" ] && UPDATED=1
    if ./docker-import.sh import "$ST_HOST/$ST_DD" --by "$BY" > "$LOGDIR/import_$t.log" 2>&1; then
      chmod -R a+rX "$DATA_DIR/processed/sim_deliveries" 2>/dev/null || true
      echo "  ✓ 登记完成：$(grep -E '已登记为仿真' "$LOGDIR/import_$t.log" | tail -1)"
    else
      echo "  ✗ 登记失败（日志 $LOGDIR/import_$t.log）：$(tail -n 3 "$LOGDIR/import_$t.log" | tr '\n' ' ')"; FAIL+=("$t"); continue
    fi
    ST_ID=$(id_of "$ST_DD")
  else
    echo "  · 已登记 #$ST_ID，NAS 上没有更新，不重登"
  fi
  [ -n "$ST_ID" ] || { echo "  ✗ 登记后库里查不到 $ST_DD 的任务 id"; FAIL+=("$t"); continue; }
  src="$SHP_ROOT/$ST_HOST/$ST_DD"; dst="$ID_ROOT/$ST_ID"
  if [ "$ST_SHP" = 1 ]; then
    if [ -e "$dst" ]; then mkdir -p "$ID_ROOT/_bak"; mv "$dst" "$ID_ROOT/_bak/${ST_ID}_$(date +%m%d_%H%M%S)" && echo "  · 旧的 floodh2d/$ST_ID 挪到 floodh2d/_bak/"; fi
    if mv "$src" "$dst"; then
      printf '{"run_id": %s, "delivery": "%s", "host": "%s", "tag": "%s"}\n' "$ST_ID" "$ST_DD" "$ST_HOST" "$t" > "$dst/_run.json"
      echo "  ✓ shp+dat：$src → $dst"
    else
      echo "  ✗ shp_dat 改名失败：$src → $dst"; FAIL+=("$t"); continue
    fi
  else
    echo "  · shp+dat 已是 floodh2d/$ST_ID（没有新转的）"
  fi
  OK=$((OK+1))
done

if [ "$UPDATED" = 1 ] || [ "$PATCHED" = 1 ]; then
  if [ "${NO_RESTART:-0}" = 1 ]; then echo "! 有覆盖更新 / 打了补丁，NO_RESTART=1 没重启：页面可能还显示旧缓存"
  else echo "有覆盖更新 / 打了补丁，重启 $AIO 清缓存 ..."; docker restart "$AIO" >/dev/null && echo "已重启。"; fi
fi
echo "viewer 重扫："; curl -s -m 20 "$VIEWER_URL/api/rescan" >/dev/null 2>&1 && echo "  ✓ $VIEWER_URL/api/rescan" || echo "  · 没连上 $VIEWER_URL（最迟 2 分钟后自动出现；端口不对用 VIEWER_URL=...）"

# ---- 对照表：本批任务的 id ↔ shp+dat 目录
{ echo "run_id,extent,depth_mm,duration_h,grid_m,status,delivery,shp_dat_dir"
  for t in $TAGS; do
    state_of "$t"; [ -n "$ST_ID" ] || continue
    row=$(psql_ "select extent||','||depth_mm||','||duration_h||','||grid_m||','||status from simulation.run where id=$ST_ID")
    echo "$ST_ID,$row,$ST_DD,$ID_ROOT/$ST_ID"
  done; } > "$OUT_CSV"
echo; echo "对照表：$OUT_CSV（$(( $(wc -l < "$OUT_CSV") - 1 )) 行）"
echo "本次：发布 $OK 个；跳过 ${#SKIP[@]} 个（没算完 / 没转完）；失败 ${#FAIL[@]} 个${FAIL[*]:+：${FAIL[*]}}"
[ "${#SKIP[@]}" = 0 ] || echo "跳过的：${SKIP[*]}"
echo "下一步：bash $0 verify"
[ "${#FAIL[@]}" = 0 ]
