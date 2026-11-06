#!/usr/bin/env bash
# RD-A 第二版（区域 RDA2）推演：服务器 2、3 各跑一半，共 28 个算例（9 场景 × L1/L2/L2planned + AECOM 82.2 mm/24 h 只 L1）。
# 第二版和第一版（RDA_*）并存：区域 ID、job_id（RDA2_*）、交付 / shp_dat / flood-viewer 目录都不同，不会覆盖第一版。
# 服务器号按本机主机名自动判断（AZPDW006uca03 = 2，AZPDW006uca06 = 3），不用也不能手写；认不出的机器直接拒绝。
#
#   rda2-run.sh canary     先跑金丝雀（服务器 2：S96D1 三态；服务器 3：S96D24 三态，真跑 34 模拟小时），跑到后处理和交付结束
#   rda2-run.sh verify     金丝雀结束后验收：地形 0.5 m、格内平均、就绪检查、出水口流量、连续性误差、有无失败步骤
#   rda2-run.sh batch      金丝雀验收通过后放出本机负责的全部场景（已算完且指纹一致的会跳过），同时启动监控
#   rda2-run.sh status     进度（容器 + 各场景）
#   rda2-run.sh monitor    （batch 已自动启动）每 5 分钟看一次：容器退出但没做完 → 自动续跑，最多 3 次；做完 / 有失败写进监控日志
#
# 本机负责的场景（按模拟小时配平，两台各约 270）：
#   服务器 2：S96D1、S33D24、S61D24、S82.2D24（只 L1）
#   服务器 3：S96D24、S33D2、S61D2、S96D2、S33D1、S61D1
# 前提：docker/.env 里 DTM_PATH 指向 0.5 m DTM（脚本启动后会在日志里核对「0.5 m」，不是就停掉）。
. "$(dirname "$0")/_lib.sh"
REGION="RDA2"
RS="$ENGINE_DIR/data/regions/$REGION/$REGION-server.sh"
[ -x "$RS" ] || die "没有 $RS（更新包没装好？）"
CMD="${1:-}"; [ -n "$CMD" ] || { sed -n '2,17p' "$0"; exit 1; }

# ---- 服务器号：只认主机名（防止手敲错，见 g261101-host.sh）
if [ -n "${RDA2_TEST_N:-}" ]; then N="$RDA2_TEST_N"      # 仅 109 上验证脚本用（同时可设 RDA2_ALLOW_5M=1 跳过 0.5 m 检查）；服务器上不要设
else
case "$(hostname -s 2>/dev/null | tr 'A-Z' 'a-z')" in
  *uca03) N=2;; *uca06) N=3;;
  *aim01) die "这是服务器 1，第二版 RD-A 只在服务器 2、3 上跑。";;
  *) die "认不出这台机器（主机名 $(hostname -s)）：只能在服务器 2（uca03）/ 3（uca06）上跑。";;
esac
fi
tri() { echo "$1,$1@L2,$1@L2planned"; }
if [ "$N" = 2 ]; then
  CANARY="$(tri S96D1)"
  BATCH="$(tri S33D24),$(tri S61D24),S82.2D24,$(tri S96D1)"
else
  CANARY="$(tri S96D24)"
  BATCH="$(tri S96D24),$(tri S33D2),$(tri S61D2),$(tri S96D2),$(tri S33D1),$(tri S61D1)"
fi
RUNDIR="$WORK_DIR/run/$REGION"
CONTAINER="flood-job-$REGION"
cstate() { docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo none; }

need_dtm() {
  [ "${RDA2_ALLOW_5M:-}" = 1 ] && return 0
  [ -n "${DTM_PATH:-}" ] || die "docker/.env 没设 DTM_PATH（0.5 m DTM）。第二版必须用 0.5 m，先设好再跑（见 rda 检查清单第 3 条）。"
}
# 启动后核对日志里真的用了 0.5 m，没有就停（容器环境变量没传进去的老坑）
confirm_dtm() {
  [ "${RDA2_ALLOW_5M:-}" = 1 ] && return 0
  local i line
  for i in $(seq 1 36); do
    sleep 5
    line="$(grep -m1 "DTM：" "$RUNDIR/run.log" 2>/dev/null || true)"
    [ -n "$line" ] && break
  done
  echo "  日志：${line:-（3 分钟内没看到 DTM 行）}"
  case "$line" in *"0.5 m"*) echo "  ✔ DTM 是 0.5 m";; *) "$RS" stop || true; die "日志里没有「DTM：…0.5 m」，已停掉。检查 docker/.env 的 DTM_PATH 和 start 是否传进容器。";; esac
}

case "$CMD" in
  canary)
    need_dtm
    echo "▶ 服务器 $N 金丝雀：$CANARY"
    "$RS" start --scenarios "$CANARY" --allow-unmodeled
    confirm_dtm
    echo "金丝雀已在跑。看进度：$0 status；跑完（状态 done）后：$0 verify";;

  verify)
    echo "==== 服务器 $N 金丝雀验收（场景：$CANARY）===="
    LOG="$RUNDIR/run.log"; [ -f "$LOG" ] || die "没有日志 $LOG（金丝雀还没跑？）"
    fail=0; chk() { if [ "$1" = ok ]; then echo "  ✔ $2"; else echo "  ✘ $2"; fail=1; fi; }
    [ "$(cstate)" != running ] && chk ok "容器已结束（$(cstate)）" || chk bad "容器还在跑，等 status 显示 done 再验收"
    grep -q "DTM：.*0\.5 m" "$LOG" && chk ok "地形 0.5 m" || chk bad "日志里没有 DTM 0.5 m"
    grep -q "按网格尺寸取平均" "$RUNDIR"/*.log 2>/dev/null && chk ok "地形按网格尺寸取平均（M6 生效）" || chk bad "没看到「按网格尺寸取平均」（M6 没生效，仍是形心单点）"
    grep -q "就绪检查通过" "$LOG" && chk ok "模型就绪检查通过（井盖不低于潮位、无重复管）" || chk bad "没看到就绪检查通过"
    if grep -q "完成，但有步骤失败" "$LOG"; then chk bad "有步骤失败："; grep "完成，但有步骤失败" "$LOG" | sort -u | head -5; else chk ok "没有失败步骤"; fi
    echo "  -- 各算例的 SWMM 报告"
    for tok in $(echo "$CANARY" | tr ',' ' '); do
      tag="${tok%%@*}"; st="${tok#*@}"; [ "$st" = "$tok" ] && st=L1
      R="$WORK_DIR/c2d/${REGION}_${tag}_${st}_2way.rpt"
      if [ ! -f "$R" ]; then chk bad "$tag $st：没有 $R"; continue; fi
      ce="$(grep "Continuity Error (%)" "$R" | tail -1 | awk '{print $NF}' || true)"
      of="$(awk '/Outfall Loading Summary/{f=1} f && $1=="SW_ABT_W33_OFL_193"{print "最大 " $4 " m3/s，累计 " $5*1000 " m3"; exit}' "$R" || true)"
      nj="$(grep -c "OFL_193_J" "$R" || true)"; nj="${nj:-0}"
      echo "     $tag $st：连续性误差 ${ce}%｜OFL_193 ${of:-（没有）}｜OFL_193_J 出现 ${nj} 次"
      [ "${nj:-0}" = 0 ] && chk ok "$tag $st 没有 OFL_193_J" || chk bad "$tag $st 还有 OFL_193_J"
    done
    echo "  （24 h 的连续性误差是 M5，单独看，不在这里判）"
    [ "$fail" = 0 ] && echo "✔ 金丝雀通过，可以放批：$0 batch" || { echo "✘ 金丝雀有问题，先别放批"; exit 1; };;

  batch)
    need_dtm
    echo "▶ 服务器 $N 放批：$BATCH"
    "$RS" start --scenarios "$BATCH" --allow-unmodeled
    confirm_dtm
    mkdir -p "$RUNDIR"
    nohup "$0" monitor >> "$RUNDIR/monitor.log" 2>&1 &
    echo "已启动，监控在后台（日志 $RUNDIR/monitor.log）。看进度：$0 status";;

  status) "$RS" status;;

  monitor)
    tries=0
    echo "[$(date '+%F %T')] 监控启动（服务器 $N）"
    while true; do
      sleep 300
      s="$(cstate)"
      if [ "$s" = running ] || [ "$s" = paused ]; then continue; fi
      if grep -q "全部完成\|场景全部完成" "$RUNDIR/run.log" 2>/dev/null || [ "$(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER" 2>/dev/null)" = 0 ]; then
        echo "[$(date '+%F %T')] 容器已结束（退出码 0）。$(grep -c '完成，但有步骤失败' "$RUNDIR/run.log" 2>/dev/null || true) 条步骤失败记录，详见 $RUNDIR/run.log；$0 status 看汇总"
        exit 0
      fi
      tries=$((tries + 1))
      if [ "$tries" -gt 3 ]; then echo "[$(date '+%F %T')] 容器退出（$s）且已自动续跑 3 次仍没做完，不再重试，请人工看 $RUNDIR/run.log"; exit 1; fi
      echo "[$(date '+%F %T')] 容器退出（$s）但没做完，自动续跑第 $tries 次（已算完且指纹一致的会跳过）"
      "$RS" start --scenarios "$BATCH" --allow-unmodeled || true
    done;;

  *) sed -n '2,17p' "$0"; exit 1;;
esac
