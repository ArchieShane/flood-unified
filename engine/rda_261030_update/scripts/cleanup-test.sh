#!/usr/bin/env bash
# 删除这台服务器上旧的 <区域> 测试推演数据：旧数据跑出来的二维结果 / 一维 inp / 日志 / 进度，以及 NAS 上该区域的交付、逐帧图、shp+dat。
# 必须在新数据推演前删：run_region 见到同名的二维结果（<区域>_S<雨量>D<历时>_L1_2d.npz）会当成「续跑」直接跳过计算，不删就会拿旧结果冒充新数据。
#
#   scripts/cleanup-test.sh <区域ID>          只列出会删什么和大小（dry-run，什么都不删）
#   scripts/cleanup-test.sh <区域ID> --yes    真删
#
# 只匹配这个区域的命名：工作目录 c2d/<区域>_S*、<区域>_S*.inp、run/<区域>；NAS 的 deliveries/<区域>_<数字>mm*h*、shp_dat/<区域>_<数字>mm*h*、
# result/*/runtime/console_data/flood2d_ad_<区域小写>_m*h*g*。别的区域和 F 系列交付（<区域>_A61D1…）不会碰。
# 旧测试还在跑（容器 flood-job-<区域> 在 running / paused）时拒绝删——等它跑完。
# 工作目录是每台服务器各一份，要在三台各跑一次；NAS 是共用的，第一台删完后另两台再跑只会报「没有」。
. "$(dirname "$0")/_lib.sh"
[ $# -ge 1 ] || { sed -n '2,14p' "$0"; exit 1; }
REGION="$1"; shift
YES=0; [ "${1:-}" = "--yes" ] && YES=1
LOW="$(echo "$REGION" | tr '[:upper:]' '[:lower:]')"
[ -n "$WORK_DIR" ] && [ -n "$NAS_DIR" ] && [ "$WORK_DIR" != "/" ] && [ "$NAS_DIR" != "/" ] || die "WORK_DIR / NAS_DIR 不对"
if command -v docker >/dev/null 2>&1; then
  S="$(docker inspect -f '{{.State.Status}}' "flood-job-$REGION" 2>/dev/null || echo none)"
  case "$S" in running|paused|restarting) die "旧测试还在跑（容器 flood-job-$REGION 是 $S）。等它跑完再清理；要中止它：${REGION}-server.sh stop";; esac
fi
shopt -s nullglob
T=()
for p in "$WORK_DIR"/c2d/*"${REGION}"_S[0-9]* "$WORK_DIR"/c2d/anuga_out/"${REGION}"_S[0-9]* "$WORK_DIR/${REGION}"_S[0-9]*.inp "$WORK_DIR/run/$REGION"; do T+=("$p"); done
for p in "$NAS_DIR"/deliveries/"${REGION}"_[0-9]*mm[0-9]*h* "$NAS_DIR"/shp_dat/"${REGION}"_[0-9]*mm[0-9]*h* "$NAS_DIR"/result/*/runtime/console_data/flood2d_ad_"${LOW}"_m*h*g*; do T+=("$p"); done
[ ${#T[@]} -gt 0 ] || { echo "没有 $REGION 的旧测试数据（工作目录 $WORK_DIR、NAS $NAS_DIR）。"; exit 0; }
echo "==== $REGION 的旧测试数据（$([ $YES = 1 ] && echo 删除 || echo '只列出，加 --yes 才删')）===="
for p in "${T[@]}"; do
  case "$p" in "$WORK_DIR"/*|"$NAS_DIR"/*) ;; *) die "路径不在工作目录 / NAS 下，拒绝：$p";; esac
  printf '%10s  %s\n' "$(du -sh -- "$p" 2>/dev/null | cut -f1)" "$p"
done
if [ $YES = 1 ]; then
  for p in "${T[@]}"; do rm -rf -- "$p"; done
  echo "✔ 已删 ${#T[@]} 项。management 里如果已经入库过这些旧测试（job_id 形如 ${REGION}_61mm1h），页面上的记录要另外在 management 里删。"
else
  echo; echo "（dry-run，没有删任何东西。确认无误后：$0 $REGION --yes）"
fi
