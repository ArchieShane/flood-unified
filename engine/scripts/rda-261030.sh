#!/usr/bin/env bash
# RD-A 2026-10-30 新数据 9 场景推演：三台服务器的分工 + 一条命令。
#   scripts/rda-261030.sh plan                           打印分工
#   scripts/rda-261030.sh <1|2|3> <命令> [选项]          在服务器 1 / 2 / 3 上执行
# 命令：
#   go                     一条命令：挪开旧测试结果（不删，放 _old_test_<时间>/）→ 启动（start 会先 check，不过不启动）。每台服务器只需要敲这一条
#   archive                只做「挪开旧测试结果」这一步
#   clean   [--yes]        删旧测试数据（缺省只列出）；旧测试还在跑会拒绝
#   check                  启动前检查（环境、数据包、雨型、网格精度、各态）
#   start                  先 check，通过后在后台启动本服务器分到的场景
#   status | log [-f] | stop [--pause] | resume | report       同 RDA-server.sh
# 说明：
#   @L2 / @L2planned 里临时泵、Geocell、Planned 泵缺设备参数，这些设施不放进模型（不生效），两态照常跑；页面设施图层标「未建模」，点 L2 / L2planned 能看到新增设备。
# 9 场景 = {33,61,96} mm × {1,2,24} h；2026-10-31 起 9 个场景都跑 L1 / L2 / L2planned，共 27 个算例（第一轮 11 个已完成，第二轮补 16 个）。二维网格 15 m（region.toml [grid]）。
# 服务器 1：48 核（并行 8）；服务器 2、3：各 32 核（并行 5）。三台共用 NAS，成果写同一个 NAS 目录；job_id = RDA_<雨量>mm<历时>h[_态]。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ENGINE="$(cd "$HERE/.." && pwd)"
RDA="$ENGINE/data/regions/RDA/RDA-server.sh"
# 第一轮（已完成）：9 场景 L1 + S61D1 的 L2 / L2planned，共 11 个算例，已入库页面 #2–#10。
# 第二轮（补跑）：其余 8 个场景各补 L2 和 L2planned，共 16 个算例。每个场景的 L2 / L2planned 跟它的 L1 在同一台服务器上（共用地形 / 下垫面中间文件）。
# `go` / `start` / `check` 默认只跑第二轮的 16 个（L1 已算完，不重复）；加 --all 才连第一轮的 11 个一起（已算完的会按指纹跳过计算，但会重做后处理和交付）。
R2_1="S33D24@L2,S33D24@L2planned,S61D24@L2,S61D24@L2planned,S96D24@L2,S96D24@L2planned"   # 服务器 1：三个 24 h（最久）
R2_2="S33D1@L2,S33D1@L2planned,S33D2@L2,S33D2@L2planned,S61D2@L2,S61D2@L2planned"           # 服务器 2
R2_3="S96D1@L2,S96D1@L2planned,S96D2@L2,S96D2@L2planned"                                    # 服务器 3
R1_1="S33D24,S61D24,S96D24,S61D1,S61D1@L2,S61D1@L2planned"
R1_2="S33D1,S33D2,S61D2"
R1_3="S96D1,S96D2"
plan() {
  echo "第二轮补跑（缺设备参数的设施不生效，页面标未建模；L2 = L1，L2planned 因 Planned 出水口等生效而不同）："
  echo "服务器 1（48 核，并行 8）：$R2_1"
  echo "服务器 2（32 核，并行 5）：$R2_2"
  echo "服务器 3（32 核，并行 5）：$R2_3"
}
[ $# -ge 1 ] || { sed -n '2,19p' "$0"; exit 1; }
[ "$1" = plan ] && { plan; exit 0; }
SRV="$1"; CMD="${2:-}"; shift 2 || true
ALLOW=""; ALL=0; REST=()
for a in "$@"; do case "$a" in --allow-unmodeled) ALLOW="--allow-unmodeled";; --all) ALL=1;; *) REST+=("$a");; esac; done
case "$SRV" in
  1) LIST="$R2_1"; [ $ALL = 1 ] && LIST="$R1_1,$R2_1";;
  2) LIST="$R2_2"; [ $ALL = 1 ] && LIST="$R1_2,$R2_2";;
  3) LIST="$R2_3"; [ $ALL = 1 ] && LIST="$R1_3,$R2_3";;
  *) echo "服务器号只能是 1 / 2 / 3"; exit 1;;
esac
case "$CMD" in
  go)     echo "服务器 $SRV 分到：$LIST"; "$HERE/archive-stale.sh" RDA "$LIST"; exec "$RDA" start --scenarios "$LIST" $ALLOW;;
  archive) exec "$HERE/archive-stale.sh" RDA "$LIST";;
  clean)  exec "$HERE/cleanup-test.sh" RDA ${REST[@]+"${REST[@]}"};;
  check)  exec "$RDA" check --scenarios "$LIST" $ALLOW;;
  start)  echo "服务器 $SRV 分到：$LIST"; exec "$RDA" start --scenarios "$LIST" $ALLOW;;
  report) exec "$RDA" report --scenarios "$LIST";;
  status|log|stop|resume) exec "$RDA" "$CMD" ${REST[@]+"${REST[@]}"};;
  *) sed -n '2,19p' "$0"; echo "不认识的命令：$CMD"; exit 1;;
esac
