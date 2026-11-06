#!/usr/bin/env bash
# 用户 2026-09-29 20:45「暂停中的任务可以在现在的任务完成后恢复运行」：
# 等 C1 客户单元版（C1_U61）四个态都算完（L1 / L2 / L2P / L2S3）→ docker unpause 设计暴雨三个容器（D24 L1 / L2P / L2）。
# D24 L2+suggested 仍由 c1_d24_l2s.sh 在 D24 L2P 算完后自动设计开跑（v2 规则，未改）。
set -u
W=/d/swbrain_work/swmm; L=$W/c2d/logs
log(){ echo "[$(date +%H:%M:%S)] $*"; }
log "等 C1_U61 L1 / L2 / L2P / L2S3 全部算完"
while true; do
  n=0
  for s in L1 L2 L2P L2S3; do [ -f $W/c2d/twoway_C1_U61_$s.json ] && n=$((n+1)); done
  running=$(docker ps --format '{{.Names}}' | grep -c '^c1u_R61_' || true)
  if [ "$n" -ge 4 ] && [ "$running" -eq 0 ]; then break; fi
  # 链条出错（L2S3 没能开跑）也要恢复，不然设计暴雨一直停着
  if [ "$running" -eq 0 ] && [ -f $W/c2d/twoway_C1_U61_L2P.json ] && ! ps -ef | grep -v grep | grep -q c1u_l2s3.sh && [ ! -f $W/c2d/twoway_C1_U61_L2S3.json ]; then
    log "U61 L2S3 链条已结束但没有结果，照样恢复设计暴雨"; break
  fi
  sleep 120
done
log "U61 完成（$n / 4），恢复设计暴雨"
docker unpause c1_D24_L1 c1_D24_L2P c1_D24_L2 && log "已恢复：$(docker ps --format '{{.Names}} {{.Status}}' | grep c1_D24 | tr '\n' ';')"
