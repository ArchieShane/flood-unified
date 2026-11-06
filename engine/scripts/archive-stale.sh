#!/usr/bin/env bash
# 把「旧测试 / 旧数据」留下的结果挪开（不删，改名放到 _old_test_<时间>/ 里，相对路径不变，要找回就挪回去），给新数据推演腾位置。
#   scripts/archive-stale.sh <区域ID> <场景记号，逗号分隔>        如  RDA S33D24,S61D1
# 只处理传进来的这几个场景（+ 态），且只处理「有二维结果、但没有输入指纹（<态>.run.fp，或旧版的 <态>.inp.sha）」的——也就是不是用现在这套输入算的。
# 用现在这套输入算出来的新结果（有指纹）不会碰；别的场景、别的区域、别的服务器分到的场景都不会碰。
# 挪的东西：工作目录里该场景的二维结果 / inp / sww / 地形 / 日志；NAS 上该 job_id 的交付、shp+dat、逐帧图。
# 推演容器 flood-job-<区域> 在跑时拒绝（旧测试没跑完不能挪）。
. "$(dirname "$0")/_lib.sh"
[ $# -ge 2 ] || { sed -n '2,9p' "$0"; exit 1; }
REGION="$1"; LIST="$2"
LOW="$(echo "$REGION" | tr '[:upper:]' '[:lower:]')"; TS="$(date +%Y%m%d-%H%M%S)"
if command -v docker >/dev/null 2>&1; then
  S="$(docker inspect -f '{{.State.Status}}' "flood-job-$REGION" 2>/dev/null || echo none)"
  case "$S" in running|paused|restarting) die "容器 flood-job-$REGION 还在跑（$S）：旧测试没跑完不能挪。等它跑完，或先 ${REGION}-server.sh stop";; esac
fi
shopt -s nullglob
MOVED=0
mv_to_archive() {  # $1 = 绝对路径（在 WORK_DIR 或 NAS_DIR 下）
  local p="$1" root rel dest
  case "$p" in "$WORK_DIR"/*) root="$WORK_DIR";; "$NAS_DIR"/*) root="$NAS_DIR";; *) die "路径不在工作目录 / NAS 下，拒绝：$p";; esac
  rel="${p#"$root"/}"; dest="$root/_old_test_$TS/$rel"
  mkdir -p "$(dirname "$dest")" && mv -- "$p" "$dest" && MOVED=$((MOVED + 1)) && echo "  → $rel"
}
for tok in ${LIST//,/ }; do
  tag="${tok%@*}"; st="${tok#*@}"; [ "$st" = "$tok" ] && st=L1
  [[ "$tag" =~ ^S([0-9]+)D([0-9]+)$ ]] || continue
  mm="${BASH_REMATCH[1]}"; h="${BASH_REMATCH[2]}"
  name="${REGION}_${tag}"; tg="${name}_${st}"
  sfx=""                                   # 多态交付（region.toml [delivery] layout = "rda"）：各态共用同一个交付根 / 逐帧图目录，job_id 不带态后缀
  jid="${REGION}_${mm}mm${h}h"
  c2d="$WORK_DIR/c2d"
  legacy_ok=0; [ -f "$c2d/${tg}.inp.sha" ] && [ -z "${DTM_PATH:-}" ] && legacy_ok=1   # 旧版指纹只记了一维 inp（不含地形）：配了高精度 DTM（DTM_PATH）时它证明不了地形，当旧结果挪开
  if [ -f "$c2d/${tg}_2d.npz" ] && [ ! -f "$c2d/${tg}.run.fp" ] && [ "$legacy_ok" = 0 ]; then
    echo "[$tok] 有二维结果但没有输入指纹 = 旧测试 / 旧数据算的，挪开（job_id $jid）："
    for p in "$c2d/${tg}"* "$c2d"/*_"${tg}"* "$c2d/anuga_out/${tg}"* "$WORK_DIR/${tg}.inp" "$WORK_DIR/run/$REGION/inputs_${tag}_${st}.json" "$WORK_DIR/run/$REGION/${tok//@/_}.log"; do [ -e "$p" ] && mv_to_archive "$p"; done
    if [ "$st" = L1 ]; then   # 地形 / 下垫面是这个场景各态共用的：只在挪 L1 时一起挪，新一轮会重新生成
      for p in "$c2d/dem_${name}.tif" "$c2d/surf_${name}.tif"; do [ -e "$p" ] && mv_to_archive "$p"; done
    fi
    if [ "$st" = L1 ]; then     # 交付根 / shp+dat / 逐帧图是各态共用的：只在挪 L1 的旧结果时一起挪（旧测试只有 L1），L2 / L2planned 新跑会写自己的态子目录
      for p in "$NAS_DIR/deliveries/$jid" "$NAS_DIR/shp_dat/$jid"; do [ -e "$p" ] && mv_to_archive "$p"; done
      for p in "$NAS_DIR"/result/*/runtime/console_data/flood2d_ad_"${LOW}"_m"${mm}"h"${h}"g[0-9]*; do [ -e "$p" ] && mv_to_archive "$p"; done
    fi
  fi
done
if [ $MOVED -gt 0 ]; then echo "✔ 挪开 $MOVED 项，放在 $WORK_DIR/_old_test_$TS/ 和 $NAS_DIR/_old_test_$TS/（没删，要找回就挪回原位）"
else echo "没有要挪的旧结果（这几个场景在本机没有旧测试留下的结果）"; fi
