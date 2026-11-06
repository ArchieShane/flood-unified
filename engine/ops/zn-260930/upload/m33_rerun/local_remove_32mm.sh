#!/usr/bin/env bash
# 客户把 32 mm 更正为 33 mm：清掉本机上 15 m 网格的 32 mm 旧结果（5 m 的 …G5、33 mm、_bak_* 备份都不动）。
#   bash local_remove_32mm.sh list              # 只列出会删什么（缺省）
#   CONFIRM=yes bash local_remove_32mm.sh run   # 真删（不可恢复；NAS 上的 32 mm 已经删了，没有备份）
# 范围：swbrain 容器挂载的 /work（顶层 + c2d 下三层）、/app/out（含 交付/）、/app/console/data（网页用的 flood2d_*）。
# 匹配规则同 nas_remove_32mm.sh：名字里有 M32H<数字> / m32h<数字>，后面是结尾或 _ . 等，不是 G；交付目录 …模型版_32mm<数字>h（结尾，没有 _5m）。
# 有计算容器在跑就不删（FORCE=1 跳过这个检查）。可选环境变量：C（容器名，缺省 swbrain）；WORK / OUT / CD 可直接指定路径（测试用）
set -euo pipefail
C=${C:-swbrain}
if [ -z "${WORK:-}" ] || [ -z "${OUT:-}" ] || [ -z "${CD:-}" ]; then
  docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "容器 $C 没在运行（要从它读挂载路径）"; exit 1; }
  mount_src(){ docker inspect "$C" --format '{{range .Mounts}}{{.Destination}}={{.Source}}{{"\n"}}{{end}}' | sed -n "s#^$1=##p"; }
  WORK=$(mount_src /work); OUT=$(mount_src /app/out); CD=$(mount_src /app/console/data)
  [ -n "$WORK" ] && [ -n "$OUT" ] && [ -n "$CD" ] || { echo "读不到 $C 的挂载（/work、/app/out、/app/console/data）"; exit 1; }
  if [ "${FORCE:-0}" != 1 ] && docker ps --format '{{.Names}}' | grep -qE '^(c1pipe|mspipe)_'; then echo "✗ 有计算容器在跑，等推演结束再清理（FORCE=1 跳过）"; exit 1; fi
fi
A='.*/[^/]*[Mm]32[Hh][0-9]+([^0-9GgHh][^/]*)?'
B='.*/[^/]*模型版_32mm[0-9]+h'
hits(){
  find "$WORK" -mindepth 1 -maxdepth 4 -name '_bak_*' -prune -o \( -regextype posix-extended \( -regex "$A" -o -regex "$B" \) \) -prune -print0
  find "$OUT"  -mindepth 1 -maxdepth 4 -name '_bak_*' -prune -o \( -regextype posix-extended \( -regex "$A" -o -regex "$B" \) \) -prune -print0
  find "$CD"   -mindepth 1 -maxdepth 2 \( -regextype posix-extended \( -regex "$A" -o -regex "$B" \) \) -prune -print0
}
case "${1:-list}" in
  list)
    echo "== 本机会删的（15 m 的 32 mm；5 m 的 G5、33 mm、_bak_* 不在其中）"
    hits | xargs -0 -r du -sh | sort -k2
    echo "== 合计"; hits | xargs -0 -r du -sc | tail -1
    ;;
  run)
    [ "${CONFIRM:-}" = yes ] || { echo "先 list 看清单；确认后：CONFIRM=yes bash $0 run"; exit 1; }
    hits | xargs -0 -r rm -rf -v | tail -n 5
    echo "完成。再 list 一次应为空。"
    ;;
  *) echo "用法：bash $0 list|run"; exit 1 ;;
esac
